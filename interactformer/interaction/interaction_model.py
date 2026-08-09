"""
Interaction Model (S1): Real-time multimodal streaming interaction.

The Interaction Model is the low-latency frontend of InteractFormer.
It integrates:
- MultimodalEncoder: early-fusion encoding of audio, vision, text
- TemporalGrid: explicit 200ms time-aligned micro-turn management
- InteractionThinker: MoE-based fast reasoning
- StreamingTalker: real-time speech generation

This is the main entry point for the S1 interaction path. It processes
streaming multimodal input at 200ms granularity and produces streaming
speech and text output.

Key design decisions (vs. DuplexOmni S1):
1. Temporal grid replaces continuous stream → explicit time-awareness
2. Bridge-aware attention replaces marker injection → cleaner S1-S2 interface
3. Implicit turn management replaces [CUT] markers → learned, not scripted
4. Encoder-free fusion replaces Qwen encoders → lighter, co-trainable
"""

from typing import Optional, Dict, Generator
import torch
import torch.nn as nn
import torch.nn.functional as F

from interactformer.interaction.encoder import MultimodalEncoder
from interactformer.interaction.temporal_grid import TemporalGrid, GridCell
from interactformer.interaction.thinker import InteractionThinker
from interactformer.interaction.talker import StreamingTalker


class InteractionModel(nn.Module):
    """Interaction Model: the real-time face of InteractFormer.

    This is what the user interacts with directly. It maintains a
    continuous presence through the Explicit Temporal Grid, processing
    streaming audio/video/text in 200ms micro-turns.

    The model can:
    - Listen and see continuously (no turn boundaries)
    - Speak in streaming fashion (frame-by-frame audio output)
    - Delegate complex tasks to the Background Model
    - Handle interruptions gracefully (both giving and taking)
    - Maintain temporal awareness (TimeSpeak, CueSpeak)

    Usage:
        model = InteractionModel(config)
        for micro_turn in stream:
            output = model.process_micro_turn(micro_turn)
            if output.speech is not None:
                play_audio(output.speech)
            if output.should_delegate:
                background_model.submit(output.context)
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_layers: int = 24,
        num_heads: int = 16,
        num_kv_heads: int = 4,
        d_ff: int = 5632,
        num_experts: int = 8,
        num_experts_per_tok: int = 2,
        vocab_size: int = 152064,
        audio_sample_rate: int = 24000,
        micro_turn_ms: int = 200,
        num_codebooks: int = 32,
        **kwargs,
    ):
        super().__init__()
        self.d_model = d_model
        self.micro_turn_ms = micro_turn_ms
        self.audio_sample_rate = audio_sample_rate
        self.samples_per_turn = int(audio_sample_rate * micro_turn_ms / 1000)

        # Sub-modules
        self.encoder = MultimodalEncoder(
            d_model=d_model,
            audio_sample_rate=audio_sample_rate,
            audio_encoder_type="dmel",
        )

        self.temporal_grid = TemporalGrid(
            d_model=d_model,
            cell_duration_ms=micro_turn_ms,
        )

        self.thinker = InteractionThinker(
            d_model=d_model,
            num_layers=num_layers,
            num_heads=num_heads,
            num_kv_heads=num_kv_heads,
            d_ff=d_ff,
            num_experts=num_experts,
            num_experts_per_tok=num_experts_per_tok,
            vocab_size=vocab_size,
        )

        self.talker = StreamingTalker(
            d_model=d_model,
            num_codebooks=num_codebooks,
            sample_rate=audio_sample_rate,
            chunk_duration_ms=micro_turn_ms,
        )

        # Runtime state
        self._streaming = False
        self._current_silence_ms: float = 0.0

    @torch.inference_mode()
    def process_micro_turn(
        self,
        audio_chunk: Optional[torch.Tensor] = None,
        image: Optional[torch.Tensor] = None,
        text_tokens: Optional[torch.Tensor] = None,
        background_context: Optional[torch.Tensor] = None,
        timestamp_ms: Optional[float] = None,
    ) -> "MicroTurnOutput":
        """Process one 200ms micro-turn of multimodal input.

        This is the core interaction loop. Each call processes exactly
        one micro-turn (200ms) of input and optionally produces output.

        Args:
            audio_chunk: [B, samples_per_turn] raw audio for this turn.
            image: [B, C, H, W] image (only on turns where vision matters).
            text_tokens: [B, T_text] text input tokens (for text chat).
            background_context: [B, num_slots, d_model] S2 context from
                the Streaming Context Bridge.
            timestamp_ms: Absolute timestamp of this turn.

        Returns:
            MicroTurnOutput with speech, text, and metadata.
        """
        device = next(self.parameters()).device

        # A heartbeat with no explicit input is still a real micro-turn: it
        # represents silence.  Feeding a zero waveform keeps time advancing
        # and lets the learned turn policy react without a special VAD path.
        if audio_chunk is None and image is None and text_tokens is None:
            audio_chunk = torch.zeros(
                1, self.samples_per_turn, device=device, dtype=torch.float32
            )

        if audio_chunk is not None:
            if not isinstance(audio_chunk, torch.Tensor):
                audio_chunk = torch.as_tensor(audio_chunk)
            if audio_chunk.dim() == 1:
                audio_chunk = audio_chunk.unsqueeze(0)
            if audio_chunk.dim() != 2 or audio_chunk.size(0) != 1:
                raise ValueError("streaming audio must have shape [1, samples]")
            if audio_chunk.size(1) > self.samples_per_turn:
                raise ValueError(
                    f"audio chunk has {audio_chunk.size(1)} samples; "
                    f"expected at most {self.samples_per_turn}"
                )
            if audio_chunk.size(1) < self.samples_per_turn:
                audio_chunk = F.pad(
                    audio_chunk, (0, self.samples_per_turn - audio_chunk.size(1))
                )
            audio_chunk = torch.nan_to_num(audio_chunk.float()).to(device)

        # === Step 1: Encode multimodal input ===
        text_embeddings = None
        if text_tokens is not None:
            if isinstance(text_tokens, str):
                tokens = self._tokenizer(
                    text_tokens, return_tensors="pt",
                    truncation=True, max_length=128,
                )
                token_ids = tokens["input_ids"].to(device)
                text_embeddings = self.thinker.token_embedding(token_ids)
            elif isinstance(text_tokens, torch.Tensor):
                if text_tokens.dtype in (torch.long, torch.int32, torch.int64):
                    text_embeddings = self.thinker.token_embedding(
                        text_tokens.to(device)
                    )
                else:
                    text_embeddings = text_tokens.to(device)

        fused = self.encoder(
            audio=audio_chunk,
            images=image.to(device) if image is not None else None,
            text_embeddings=text_embeddings,
        )  # [B, T_enc, d_model]

        # === Step 2: Create temporal grid cell ===
        if timestamp_ms is None:
            timestamp_ms = (
                self.temporal_grid._current_cell_id * self.micro_turn_ms
            )

        # Estimate speech activity (energy-threshold heuristic during
        # development; target is learned speech-state from TemporalGrid)
        is_speaking = self._estimate_speech_activity(audio_chunk)

        cell = self.temporal_grid.create_cell(
            timestamp_ms=timestamp_ms,
            audio_embedding=fused[:, -1:, :] if audio_chunk is not None else None,
            text_embedding=fused,
        )
        cell.is_user_speaking = is_speaking

        # Update silence tracking
        if not is_speaking:
            self._current_silence_ms += self.micro_turn_ms
        else:
            self._current_silence_ms = 0.0

        # === Step 3: Thinker processing ===
        # Get recent cells for context (excluding the new cell created above
        # since its hidden_state is not yet computed)
        recent_cells = self.temporal_grid.get_recent_cells(24)  # ~5 seconds excl. current
        recent_hidden = [
            c.hidden_state for c in recent_cells
            if c.hidden_state is not None
        ]

        # Stack recent hidden states and prepend current input
        # shape: [B, T_past+1, d_model] — current turn is always the last position
        if recent_hidden:
            past_hidden = torch.stack(recent_hidden, dim=1)  # [B, T_past, d_model]
            context_hidden = torch.cat([past_hidden, fused[:, -1:, :]], dim=1)
        else:
            context_hidden = fused[:, -1:, :]  # [B, 1, d_model]

        # Apply temporal position encoding from TemporalGrid
        num_seq_cells = context_hidden.shape[1]
        cell_ids = torch.arange(
            cell.cell_id - num_seq_cells + 1, cell.cell_id + 1,
            device=device
        ).unsqueeze(0)  # [1, num_seq_cells]
        silence_tensor = torch.full(
            (1, num_seq_cells), self._current_silence_ms,
            device=device, dtype=torch.float32
        )
        context_hidden = self.temporal_grid.forward(
            cell_ids, context_hidden, silence_tensor
        )

        # Build attention mask matching the actual sequence length
        attn_mask = self.temporal_grid.build_attention_mask(
            num_seq_cells, include_future=False
        ).to(device)

        # Ensure mask shape compatibility: [T, T] → broadcastable to [B, H, T, T]
        if attn_mask.dim() == 2:
            attn_mask = attn_mask.unsqueeze(0).unsqueeze(0)  # [1, 1, T, T]

        # Run Thinker
        thinker_output = self.thinker(
            input_embeddings=context_hidden,
            bridge_context=background_context,
            attention_mask=attn_mask,
        )

        # Store hidden state in current cell
        cell.hidden_state = thinker_output["hidden_states"][:, -1, :]

        # === Step 4: Decide whether to speak ===
        current_hidden = cell.hidden_state
        should_speak, speech_confidence = self.temporal_grid.should_model_speak(
            current_hidden,
            recent_hidden[-5:] if len(recent_hidden) >= 5 else recent_hidden,
        )

        # === Step 5: Check for interruption ===
        should_interrupt = False
        if len(recent_hidden) >= 2:
            should_interrupt, _ = self.temporal_grid.detect_interruption(
                current_hidden, recent_hidden[-1]
            )

        # === Step 6: Generate speech if needed ===
        speech_output = None
        codec_tokens = None
        if should_speak and not should_interrupt:
            speech_output, codec_tokens = self.talker(
                thinker_output["hidden_states"],
                is_interrupted=False,
            )
            cell.is_model_speaking = True
            cell.output_speech = speech_output  # Write back for context
        elif should_interrupt:
            self.talker.interrupt()
            cell.is_model_speaking = False

        # === Step 7: Generate text output ===
        text_logits = thinker_output["logits"][:, -1, :]  # Last token
        predicted_tokens = text_logits.argmax(dim=-1).tolist()  # [B]
        # Write decoded text for S2 consumption
        if predicted_tokens:
            cell.output_text_tokens = predicted_tokens
            cell.output_text = self._tokenizer.decode(
                predicted_tokens, skip_special_tokens=True
            )

        # === Step 8: Check if delegation is needed ===
        delegation_score = thinker_output["delegation_score"].item()
        should_delegate = delegation_score > 0.5

        return MicroTurnOutput(
            cell=cell,
            speech=speech_output,
            text_logits=text_logits,
            should_delegate=should_delegate,
            delegation_score=delegation_score,
            should_interrupt=should_interrupt,
            speech_confidence=speech_confidence,
            silence_duration_ms=self._current_silence_ms,
            context_for_delegation=(
                self._build_delegation_context(recent_cells)
                if should_delegate else None
            ),
        )

    def _estimate_speech_activity(
        self, audio_chunk: Optional[torch.Tensor]
    ) -> bool:
        """Estimate whether the audio chunk contains speech.

        Currently uses energy-threshold heuristic. Target architecture:
        replace with learned speech-state estimation from the TemporalGrid
        (see temporal_grid.py's speech_gate and interruption_detector).

        TODO: implement SpeechActivityEstimator abstract class for
        pluggable heuristic/learned backends.
        """
        if audio_chunk is None:
            return False
        energy = (audio_chunk ** 2).mean().item()
        return energy > 1e-4

    def _build_delegation_context(
        self, recent_cells: list[GridCell]
    ) -> Dict:
        """Build a delegation context package for the Background Model.

        This is the S1→S2 communication: a rich context package (per
        TML's design) rather than a standalone query. It includes the
        full recent conversation, temporal state, and the specific
        reason for delegation.
        """
        return {
            "type": "delegation_request",
            "timestamp_ms": recent_cells[-1].timestamp_ms if recent_cells else 0,
            "num_context_cells": len(recent_cells),
            "context_duration_ms": len(recent_cells) * self.micro_turn_ms,
            "user_has_spoken": any(c.is_user_speaking for c in recent_cells),
            "silence_duration_ms": self._current_silence_ms,
            "hidden_states": torch.stack([
                c.hidden_state for c in recent_cells if c.hidden_state is not None
            ]) if recent_cells else None,
        }

    def start_session(self) -> None:
        """Start a new interaction session."""
        self._streaming = True
        self._current_silence_ms = 0.0
        self.talker.reset()

    def end_session(self) -> None:
        """End the current interaction session."""
        self._streaming = False
        self.talker.interrupt()
        self.talker.reset()

    def new_runtime_state(self) -> Dict:
        """Create isolated mutable streaming state for one session."""
        return {
            "cells": {},
            "current_cell_id": 0,
            "current_silence_ms": 0.0,
            "streaming": False,
            "talker_interrupted": False,
            "talker_overlap_buffer": None,
            "talker_pending_waveform": None,
        }

    def load_runtime_state(self, state: Dict) -> None:
        """Activate a session's state before a serialized model step."""
        self.temporal_grid._cells = state["cells"]
        self.temporal_grid._current_cell_id = state["current_cell_id"]
        self._current_silence_ms = state["current_silence_ms"]
        self._streaming = state["streaming"]
        self.talker._interrupted = state["talker_interrupted"]
        self.talker.renderer._overlap_buffer = state["talker_overlap_buffer"]
        self.talker._pending_waveform = state["talker_pending_waveform"]

    def save_runtime_state(self, state: Dict) -> None:
        """Persist mutable streaming state after a model step."""
        state.update({
            "cells": self.temporal_grid._cells,
            "current_cell_id": self.temporal_grid._current_cell_id,
            "current_silence_ms": self._current_silence_ms,
            "streaming": self._streaming,
            "talker_interrupted": self.talker._interrupted,
            "talker_overlap_buffer": self.talker.renderer._overlap_buffer,
            "talker_pending_waveform": self.talker._pending_waveform,
        })

    def forward(
        self,
        audio: Optional[torch.Tensor] = None,
        images: Optional[torch.Tensor] = None,
        text_tokens: Optional[torch.Tensor] = None,
        background_context: Optional[torch.Tensor] = None,
    ) -> Dict:
        """Batch forward pass (non-streaming, for training).

        Args:
            audio: [B, T_audio] raw audio.
            images: [B, C, H, W] images.
            text_tokens: [B, T_text] text tokens.
            background_context: [B, num_slots, d_model] bridge context.

        Returns:
            Dict with 'hidden_states', 'logits', 'delegation_score'.
        """
        fused = self.encoder(
            audio=audio,
            images=images,
            text_tokens=text_tokens,
        )

        thinker_output = self.thinker(
            input_embeddings=fused,
            bridge_context=background_context,
        )

        return {
            "hidden_states": thinker_output["hidden_states"],
            "logits": thinker_output["logits"],
            "delegation_score": thinker_output["delegation_score"],
        }


class MicroTurnOutput:
    """Output of one micro-turn processing step.

    Attributes:
        cell: The temporal grid cell that was processed.
        speech: Generated speech waveform for this turn.
        text_logits: Text output logits (for text-based responses).
        should_delegate: Whether to send to Background Model.
        delegation_score: Confidence in delegation decision.
        should_interrupt: Whether the model should interrupt its speech.
        speech_confidence: Confidence in the speak/don't-speak decision.
        silence_duration_ms: How long the user has been silent.
        context_for_delegation: Context package for S2 if delegating.
    """

    def __init__(
        self,
        cell: GridCell,
        speech: Optional[torch.Tensor] = None,
        text_logits: Optional[torch.Tensor] = None,
        should_delegate: bool = False,
        delegation_score: float = 0.0,
        should_interrupt: bool = False,
        speech_confidence: float = 0.0,
        silence_duration_ms: float = 0.0,
        context_for_delegation: Optional[Dict] = None,
    ):
        self.cell = cell
        self.speech = speech
        self.text_logits = text_logits
        self.should_delegate = should_delegate
        self.delegation_score = delegation_score
        self.should_interrupt = should_interrupt
        self.speech_confidence = speech_confidence
        self.silence_duration_ms = silence_duration_ms
        self.context_for_delegation = context_for_delegation
