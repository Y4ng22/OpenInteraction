"""
Explicit Temporal Grid: Time-aligned micro-turn management.

THIS IS INTERACTFORMER'S CORE INNOVATION.

While TML Interaction Models describes micro-turns conceptually, and
DuplexOmni uses continuous streams with marker-based conventions,
InteractFormer introduces an EXPLICIT temporal grid that structures
all interaction through 200ms time-aligned cells.

Each GridCell represents one 200ms time slice and contains:
- Input features (audio, vision, text) for that slice
- Output features (speech, text) generated for that slice
- Temporal metadata (timestamp, position encoding)
- Attention mask (which cells are visible)
- Background injection state (pending/active/complete)

The grid provides:
1. **Time-awareness**: The model explicitly knows "when" it is, enabling
   proactive speech (TimeSpeak) and cue-based responses (CueSpeak),
   matching TML's benchmarks.
2. **Interruption modeling**: Instead of explicit [CUT] tokens
   (DuplexOmni), interruption is learned as a temporal pattern where
   the model generates output while receiving input in the same cell.
3. **Background synchronization**: S2 results are injected at cell
   boundaries, naturally aligned with the interaction rhythm.
"""

from dataclasses import dataclass, field
from typing import Optional, Literal
import torch
import torch.nn as nn
import math


@dataclass
class GridCell:
    """A single cell in the temporal grid = one 200ms micro-turn.

    This is the atomic unit of InteractFormer's interaction paradigm.
    Each cell is processed independently by the Interaction Model, but
    the grid structure provides temporal context across cells.

    Attributes:
        cell_id: Sequential cell index in the session.
        timestamp_ms: Absolute timestamp in milliseconds.
        audio_embedding: Encoded audio features [d_model].
        vision_embedding: Encoded vision features [N_patches, d_model].
        text_embedding: Text token embeddings [T_text, d_model].
        hidden_state: Computed hidden state after thinker processing.
        output_speech: Generated speech waveform for this cell.
        output_text: Generated text tokens for this cell.
        background_injections: Pending S2 results to inject.
        cell_state: Current state of this cell in the pipeline.
        is_user_speaking: Whether user speech is detected.
        is_model_speaking: Whether the model is generating speech.
    """
    cell_id: int
    timestamp_ms: float
    audio_embedding: Optional[torch.Tensor] = None
    vision_embedding: Optional[torch.Tensor] = None
    text_embedding: Optional[torch.Tensor] = None
    hidden_state: Optional[torch.Tensor] = None
    output_speech: Optional[torch.Tensor] = None
    output_text: Optional[list[int]] = None
    background_injections: list = field(default_factory=list)

    # Cell lifecycle state
    cell_state: Literal[
        "pending",      # Waiting for input
        "encoding",     # Encoding input features
        "thinking",     # Thinker processing
        "talking",      # Talker generating speech
        "injecting",    # Injecting background results
        "complete",     # Done
    ] = "pending"

    is_user_speaking: bool = False
    is_model_speaking: bool = False

    @property
    def has_background_updates(self) -> bool:
        """Whether this cell has pending background model injections."""
        return len(self.background_injections) > 0


class TemporalGridPositionEncoding(nn.Module):
    """Position encoding for the temporal grid.

    Unlike standard sinusoidal position encodings that encode token
    positions, this encodes ABSOLUTE TEMPORAL POSITIONS at the
    micro-turn level. The model learns to associate specific time
    patterns with interaction behaviors:

    - Short silence (~200-400ms) → likely turn-taking pause
    - Long silence (>1s) → user may be thinking or done
    - Rapid interleaving → interruption or backchanneling
    - Proactive timing → model should speak at specific intervals

    This is what enables InteractFormer's TimeSpeak and CueSpeak
    capabilities (matching TML's benchmarks).
    """

    def __init__(
        self,
        d_model: int = 2048,
        max_cells: int = 15000,  # ~50 minutes at 200ms
        cell_duration_ms: int = 200,
    ):
        super().__init__()
        self.d_model = d_model
        self.max_cells = max_cells
        self.cell_duration_ms = cell_duration_ms

        # Learnable position encoding per cell
        self.position_embedding = nn.Embedding(max_cells, d_model)

        # Time-of-day embedding (optional, for long-running sessions)
        self.time_embedding = nn.Embedding(1440, d_model // 4)  # minutes in a day

        # Cell duration scale factor (learnable)
        self.duration_scale = nn.Parameter(torch.ones(1))

        # Silence duration encoding: the model learns that longer
        # silences mean different things
        self.silence_projector = nn.Sequential(
            nn.Linear(1, d_model // 4),
            nn.SiLU(),
            nn.Linear(d_model // 4, d_model // 4),
        )

    def forward(
        self,
        cell_ids: torch.Tensor,
        silence_durations: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Compute temporal position encodings.

        Args:
            cell_ids: [B, num_cells] cell indices.
            silence_durations: [B, num_cells] silence duration before
                each cell (in ms). Used for turn-taking learning.

        Returns:
            [B, num_cells, d_model] temporal position encodings.
        """
        B, N = cell_ids.shape
        device = cell_ids.device

        # Base position encoding
        pos_emb = self.position_embedding(cell_ids.clamp(0, self.max_cells - 1))

        # Silence duration features
        if silence_durations is not None:
            silence_norm = silence_durations.float() / 1000.0  # Normalize to seconds
            silence_emb = self.silence_projector(
                silence_norm.unsqueeze(-1)
            )  # [B, N, d_model//4]

            # Concatenate with position encoding
            pos_emb = torch.cat([
                pos_emb[..., :self.d_model * 3 // 4],
                silence_emb,
            ], dim=-1)

        return pos_emb * self.duration_scale


class TemporalGrid(nn.Module):
    """Explicit Temporal Grid — InteractFormer's core innovation.

    Manages the grid of 200ms cells that structure all interaction.
    Provides:
    1. Cell creation and management
    2. Temporal position encoding
    3. Attention masking (which cells are visible to which)
    4. Background injection scheduling
    5. Implicit turn management (no [CUT] markers)

    The grid replaces DuplexOmni's marker-based duplex conventions
    (^, [CUT], [WAIT], [PENDXS]) with learned temporal patterns.
    """

    def __init__(
        self,
        d_model: int = 2048,
        cell_duration_ms: int = 200,
        max_history_cells: int = 125,  # 25 seconds context
        max_lookahead_cells: int = 5,   # 1 second lookahead
    ):
        super().__init__()
        self.d_model = d_model
        self.cell_duration_ms = cell_duration_ms
        self.max_history_cells = max_history_cells
        self.max_lookahead_cells = max_lookahead_cells

        # Position encoding
        self.position_encoding = TemporalGridPositionEncoding(
            d_model=d_model,
            cell_duration_ms=cell_duration_ms,
        )

        # Learnable gate for determining when the model should speak
        # This replaces DuplexOmni's explicit [CUT] markers
        self.speech_gate = nn.Sequential(
            nn.Linear(d_model, d_model // 4),
            nn.SiLU(),
            nn.Linear(d_model // 4, 1),
            nn.Sigmoid(),
        )

        # Interruption detector: learned from temporal patterns
        self.interruption_detector = nn.Sequential(
            nn.Linear(d_model * 2, d_model // 4),  # current + previous
            nn.SiLU(),
            nn.Linear(d_model // 4, 2),  # [continue, interrupt]
        )

        # Cell state buffer (not a parameter, managed at runtime)
        self._cells: dict[int, GridCell] = {}
        self._current_cell_id: int = 0

    def create_cell(
        self,
        timestamp_ms: float,
        audio_embedding: Optional[torch.Tensor] = None,
        vision_embedding: Optional[torch.Tensor] = None,
        text_embedding: Optional[torch.Tensor] = None,
    ) -> GridCell:
        """Create a new grid cell for the current micro-turn.

        Args:
            timestamp_ms: Absolute timestamp.
            audio_embedding: Encoded audio for this cell.
            vision_embedding: Encoded vision for this cell.
            text_embedding: Text embeddings for this cell.

        Returns:
            The newly created GridCell.
        """
        cell = GridCell(
            cell_id=self._current_cell_id,
            timestamp_ms=timestamp_ms,
            audio_embedding=audio_embedding,
            vision_embedding=vision_embedding,
            text_embedding=text_embedding,
            cell_state="encoding",
        )
        self._cells[self._current_cell_id] = cell
        self._current_cell_id += 1

        # Prune old cells
        self._prune_old_cells()

        return cell

    def get_recent_cells(self, n: Optional[int] = None) -> list[GridCell]:
        """Get the most recent N cells (or all if N is None)."""
        if n is None:
            n = self.max_history_cells

        sorted_ids = sorted(self._cells.keys())
        recent_ids = sorted_ids[-n:] if len(sorted_ids) > n else sorted_ids
        return [self._cells[cid] for cid in recent_ids]

    def should_model_speak(
        self,
        current_hidden: torch.Tensor,
        recent_hidden: list[torch.Tensor],
    ) -> tuple[bool, float]:
        """Determine if the model should speak in the current cell.

        This is the IMPLICIT turn management mechanism. Instead of
        relying on explicit markers like DuplexOmni's [CUT] or
        external VAD like traditional systems, the model learns to
        predict speaking opportunities from the temporal context.

        Args:
            current_hidden: Hidden state of the current cell.
            recent_hidden: Hidden states of recent cells.

        Returns:
            (should_speak, confidence) tuple.
        """
        speech_prob = self.speech_gate(current_hidden)
        return speech_prob > 0.5, speech_prob.item()

    def detect_interruption(
        self,
        current_hidden: torch.Tensor,
        previous_hidden: torch.Tensor,
    ) -> tuple[bool, float]:
        """Detect if interruption is needed.

        Learned from temporal patterns: when the user starts speaking
        while the model is speaking, the model should detect this and
        yield gracefully.

        Args:
            current_hidden: Current cell's hidden state.
            previous_hidden: Previous cell's hidden state.

        Returns:
            (should_interrupt, confidence) tuple.
        """
        combined = torch.cat([current_hidden, previous_hidden], dim=-1)
        logits = self.interruption_detector(combined)
        probs = torch.softmax(logits, dim=-1)
        # probs: [B, 2]; index with [0, 1] not [1] for batch safety
        return probs[0, 1] > 0.5, probs[0, 1].item()

    def build_attention_mask(
        self,
        num_cells: int,
        include_future: bool = False,
    ) -> torch.Tensor:
        """Build temporal attention mask for the grid.

        By default, cells can only attend to past cells (causal mask).
        When include_future is True, limited lookahead is allowed for
        planning and anticipation.

        Args:
            num_cells: Number of cells in the sequence.
            include_future: Whether to allow limited future attention.

        Returns:
            [num_cells, num_cells] attention mask (True = allowed).
        """
        mask = torch.ones(num_cells, num_cells, dtype=torch.bool)

        # Causal: cell i can attend to cells j <= i
        for i in range(num_cells):
            for j in range(num_cells):
                if include_future and j - i <= self.max_lookahead_cells:
                    mask[i, j] = True
                else:
                    mask[i, j] = j <= i

        return mask

    def inject_background_result(
        self,
        cell_id: int,
        result: dict,
    ) -> None:
        """Inject a background model result into a specific cell.

        Args:
            cell_id: Target cell for injection.
            result: Background model output dict with keys like
                'text', 'tool_output', 'confidence'.
        """
        if cell_id in self._cells:
            self._cells[cell_id].background_injections.append(result)
            self._cells[cell_id].cell_state = "injecting"

    def _prune_old_cells(self) -> None:
        """Remove cells beyond max_history_cells."""
        max_id = self._current_cell_id - self.max_history_cells
        old_ids = [cid for cid in self._cells if cid < max_id]
        for cid in old_ids:
            del self._cells[cid]

    def forward(
        self,
        cell_ids: torch.Tensor,
        hidden_states: torch.Tensor,
        silence_durations: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Apply temporal position encoding to hidden states.

        Args:
            cell_ids: [B, num_cells]
            hidden_states: [B, num_cells, d_model]
            silence_durations: [B, num_cells]

        Returns:
            [B, num_cells, d_model] temporally encoded hidden states.
        """
        pos_emb = self.position_encoding(cell_ids, silence_durations)
        return hidden_states + pos_emb
