"""
Streaming Talker: Real-time speech generation.

The Talker converts hidden states from the Interaction Thinker into
streaming audio waveforms. It's based on Qwen3-Omni's Talker architecture
but modified for:

1. **Temporal grid-aligned generation**: Generates exactly one cell's
   worth of audio per forward pass (200ms at 24kHz = 4800 samples).
2. **Interruption-aware**: Can stop mid-generation when the user interrupts.
3. **Multi-codebook streaming**: Uses MTP (Multi-Token Prediction) for
   efficient residual codebook generation.

Architecture:
    Hidden States → Codec Token Prediction (autoregressive)
                  → Code2Wav Renderer (streaming)
                  → Waveform Output
"""

from typing import Optional, Tuple, Generator
import torch
import torch.nn as nn
import torch.nn.functional as F


class MultiTokenPredictor(nn.Module):
    """Multi-Token Prediction (MTP) for efficient codec generation.

    Instead of autoregressively generating codec tokens one-by-one
    across all codebooks (which would be slow), MTP predicts one
    codebook per step plus residual codebooks via a lightweight
    parallel prediction head.

    This is the same technique used in Qwen3-Omni and is critical
    for achieving sub-300ms latency.
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_codebooks: int = 32,
        codebook_size: int = 4096,
        num_mtp_heads: int = 4,
    ):
        super().__init__()
        self.num_codebooks = num_codebooks
        self.codebook_size = codebook_size

        # Primary codebook predictor (layer 0)
        self.primary_head = nn.Linear(d_model, codebook_size)

        # MTP heads for residual codebooks (layers 1..N-1)
        self.mtp_heads = nn.ModuleList([
            nn.Sequential(
                nn.Linear(d_model + codebook_size, d_model // num_mtp_heads),
                nn.SiLU(),
                nn.Linear(d_model // num_mtp_heads, codebook_size),
            )
            for _ in range(num_codebooks - 1)
        ])

    def forward(
        self,
        hidden_states: torch.Tensor,
        primary_token: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, list[torch.Tensor]]:
        """Predict codec tokens from hidden states.

        Args:
            hidden_states: [B, d_model] or [B, T, d_model].
            primary_token: [B] first codebook token (for MTP conditioning).

        Returns:
            primary_logits: [B, codebook_size]
            residual_logits: list of [B, codebook_size] for each residual book
        """
        # Primary codebook
        primary_logits = self.primary_head(hidden_states)

        # Residual codebooks via MTP
        residual_logits = []
        current_context = hidden_states

        for mtp_head in self.mtp_heads:
            if primary_token is not None:
                # Condition on primary token embedding
                token_onehot = F.one_hot(
                    primary_token, self.codebook_size
                ).float()
                mtp_input = torch.cat([current_context, token_onehot], dim=-1)
            else:
                mtp_input = torch.cat([
                    current_context,
                    torch.zeros(
                        current_context.size(0), self.codebook_size,
                        device=current_context.device,
                    ),
                ], dim=-1)

            logits = mtp_head(mtp_input)
            residual_logits.append(logits)

        return primary_logits, residual_logits


class Code2WavRenderer(nn.Module):
    """Streaming Code2Wav renderer.

    Converts codec tokens to audio waveforms frame-by-frame.
    Uses a convolutional decoder that processes one frame at a time
    for streaming — no need to wait for the full sequence.

    Operates at 12.5Hz (80ms per frame), so each 200ms micro-turn
    generates ~2.5 codec frames.
    """

    def __init__(
        self,
        codebook_size: int = 4096,
        codebook_dim: int = 128,
        num_codebooks: int = 32,
        sample_rate: int = 24000,
        frame_rate: float = 12.5,  # Hz
    ):
        super().__init__()
        self.codebook_size = codebook_size
        self.num_codebooks = num_codebooks
        self.sample_rate = sample_rate
        self.frame_rate = frame_rate
        self.samples_per_frame = int(sample_rate / frame_rate)  # 1920

        # Codebook embeddings
        self.codebook_embedding = nn.Embedding(
            codebook_size * num_codebooks, codebook_dim
        )

        # Streaming decoder (transposed convolutions for upsampling)
        self.decoder = nn.Sequential(
            nn.ConvTranspose1d(
                codebook_dim, codebook_dim * 2,
                kernel_size=8, stride=4, padding=2,
            ),
            nn.SiLU(),
            nn.ConvTranspose1d(
                codebook_dim * 2, codebook_dim,
                kernel_size=8, stride=4, padding=2,
            ),
            nn.SiLU(),
            nn.ConvTranspose1d(
                codebook_dim, 1,
                kernel_size=12, stride=4, padding=4,
            ),
            nn.Tanh(),  # Output in [-1, 1]
        )

        # Overlap-add buffer for smooth frame transitions
        self._overlap_buffer: Optional[torch.Tensor] = None
        self._overlap_len: int = 32  # samples

    def forward(
        self,
        codec_tokens: list[torch.Tensor],
        streaming: bool = True,
    ) -> torch.Tensor:
        """Render codec tokens to waveform.

        Args:
            codec_tokens: List of [B] tensors, one per codebook.
            streaming: If True, use overlap-add for continuity.

        Returns:
            waveform: [B, samples_per_frame] audio samples.
        """
        B = codec_tokens[0].size(0)
        device = codec_tokens[0].device

        # Compute codebook embedding as sum of weighted codebook vectors
        embeddings = []
        for cb_idx, token in enumerate(codec_tokens):
            emb_idx = token + cb_idx * self.codebook_size
            emb = self.codebook_embedding(emb_idx)  # [B, codebook_dim]
            embeddings.append(emb)

        # Sum across codebooks (weighted, learned implicitly)
        x = torch.stack(embeddings, dim=0).sum(dim=0)  # [B, codebook_dim]
        x = x.unsqueeze(-1)  # [B, codebook_dim, 1]

        # Decode to waveform
        waveform = self.decoder(x).squeeze(1)  # [B, samples]

        # Overlap-add for streaming continuity
        if streaming and self._overlap_buffer is not None:
            overlap = min(self._overlap_len, waveform.size(-1))
            waveform[:, :overlap] = (
                0.5 * waveform[:, :overlap] +
                0.5 * self._overlap_buffer[:, -overlap:]
            )

        # Store overlap for next frame
        if streaming:
            self._overlap_buffer = waveform[:, -self._overlap_len:].detach()

        return waveform

    def reset_stream(self) -> None:
        """Reset streaming state for a new session."""
        self._overlap_buffer = None


class StreamingTalker(nn.Module):
    """Streaming Talker: converts Thinker states to speech in real-time.

    Processes hidden states from the Interaction Thinker and generates
    streaming audio output. Unlike batch TTS systems, this generates
    speech frame-by-frame as hidden states arrive from the Thinker.

    Key features:
    - Temporal-grid-aligned: one output frame per micro-turn
    - Interruptible: can stop generation mid-utterance
    - Quality-latency tradeoff: adjustable via num_mtp_heads and frame rate
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_codebooks: int = 32,
        codebook_size: int = 4096,
        codebook_dim: int = 128,
        sample_rate: int = 24000,
        frame_rate: float = 12.5,
        num_mtp_heads: int = 4,
    ):
        super().__init__()
        self.d_model = d_model
        self.num_codebooks = num_codebooks
        self.sample_rate = sample_rate
        self.frame_rate = frame_rate
        self.samples_per_frame = int(sample_rate / frame_rate)

        # Hidden state → codec projection
        self.state_to_codec = nn.Sequential(
            nn.Linear(d_model, d_model),
            nn.SiLU(),
            nn.Linear(d_model, d_model // 2),
        )

        # Multi-token predictor
        self.mtp = MultiTokenPredictor(
            d_model=d_model // 2,
            num_codebooks=num_codebooks,
            codebook_size=codebook_size,
            num_mtp_heads=num_mtp_heads,
        )

        # Code2Wav renderer
        self.renderer = Code2WavRenderer(
            codebook_size=codebook_size,
            codebook_dim=codebook_dim,
            num_codebooks=num_codebooks,
            sample_rate=sample_rate,
            frame_rate=frame_rate,
        )

        # Interruption state
        self._interrupted: bool = False

    def forward(
        self,
        hidden_states: torch.Tensor,
        is_interrupted: bool = False,
    ) -> Tuple[torch.Tensor, list[torch.Tensor]]:
        """Generate speech for one micro-turn.

        Args:
            hidden_states: [B, d_model] or [B, T, d_model] from Thinker.
            is_interrupted: Whether to stop generation early.

        Returns:
            waveform: [B, samples] generated audio.
            codec_tokens: List of predicted codec tokens for each codebook.
        """
        if is_interrupted:
            self._interrupted = True
            return (
                torch.zeros(hidden_states.size(0), self.samples_per_frame,
                           device=hidden_states.device),
                [],
            )

        # Take the last hidden state for codec prediction
        if hidden_states.dim() == 3:
            h = hidden_states[:, -1, :]  # [B, d_model]
        else:
            h = hidden_states  # [B, d_model]

        # Project to codec space
        codec_hidden = self.state_to_codec(h)  # [B, d_model//2]

        # Predict codec tokens
        primary_logits, residual_logits = self.mtp(codec_hidden)

        # Sample / argmax codec tokens
        primary_token = primary_logits.argmax(dim=-1)  # [B]

        residual_tokens = []
        for logits in residual_logits:
            token = logits.argmax(dim=-1)
            residual_tokens.append(token)

        all_tokens = [primary_token] + residual_tokens

        # Render to waveform
        waveform = self.renderer(all_tokens, streaming=not self._interrupted)

        return waveform, all_tokens

    def stream_generate(
        self,
        hidden_stream: Generator[torch.Tensor, None, None],
    ) -> Generator[Tuple[torch.Tensor, list[torch.Tensor]], None, None]:
        """Generator-based streaming interface.

        Yields audio chunks as hidden states arrive from the Thinker.
        This is the main inference loop for the Talker.

        Args:
            hidden_stream: Generator yielding [d_model] hidden states.

        Yields:
            (waveform_chunk, codec_tokens) for each hidden state.
        """
        self.renderer.reset_stream()
        self._interrupted = False

        for hidden_state in hidden_stream:
            if self._interrupted:
                break

            waveform, tokens = self.forward(hidden_state.unsqueeze(0))
            yield waveform.squeeze(0), tokens

    def interrupt(self) -> None:
        """Signal interruption to stop speech generation."""
        self._interrupted = True

    def reset(self) -> None:
        """Reset state for a new utterance."""
        self._interrupted = False
        self.renderer.reset_stream()
