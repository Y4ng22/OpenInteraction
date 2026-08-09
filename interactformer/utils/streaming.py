"""
Streaming utilities for real-time audio/video processing.

Implements the core streaming primitives used throughout InteractFormer:
- AudioChunk: A single 200ms audio fragment
- MicroTurn: The atomic unit of interaction (one 200ms processing cycle)
- StreamingBuffer: Ring-buffer for managing streaming state

These are the building blocks of InteractFormer's Explicit Temporal Grid,
which replaces continuous-stream and turn-based paradigms with explicit
time-aligned micro-turn management.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Generator
from collections import deque
import numpy as np


@dataclass
class AudioChunk:
    """A single chunk of audio corresponding to one micro-turn (200ms).

    At 24kHz sample rate, each chunk contains 4800 samples per channel.
    For dMel encoding (80 mel bands, 25ms window, 10ms hop), this yields
    roughly 20 mel frames per chunk.

    Attributes:
        samples: Raw audio samples (numpy array, shape [num_samples]).
        sample_rate: Sample rate in Hz (default 24000).
        timestamp_ms: Absolute timestamp of this chunk in the stream.
        chunk_id: Sequential chunk identifier.
        is_speech: Whether this chunk contains speech (VAD-free; determined
            by the model's implicit understanding rather than an external VAD).
    """
    samples: np.ndarray
    sample_rate: int = 24000
    timestamp_ms: float = 0.0
    chunk_id: int = 0
    is_speech: Optional[bool] = None

    @property
    def duration_ms(self) -> float:
        """Duration of this chunk in milliseconds."""
        return len(self.samples) / self.sample_rate * 1000

    @property
    def num_samples(self) -> int:
        """Number of audio samples in this chunk."""
        return len(self.samples)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "sample_rate": self.sample_rate,
            "timestamp_ms": self.timestamp_ms,
            "chunk_id": self.chunk_id,
            "num_samples": self.num_samples,
            "duration_ms": self.duration_ms,
        }


@dataclass
class MicroTurn:
    """The atomic unit of interaction in InteractFormer.

    Each micro-turn represents a 200ms time slice (following TML's design).
    During one micro-turn, the Interaction Model:
    1. Receives incoming audio/video chunks
    2. Processes them through the temporal grid
    3. Optionally generates speech output
    4. Checks for pending Background Model results

    The key difference from traditional turn-based systems:
    - No artificial turn boundaries — the model decides when to speak
    - No VAD — speech/silence is part of the model's learned context
    - Overlap is natural — the model can process input while generating output

    Attributes:
        turn_id: Sequential micro-turn identifier.
        timestamp_ms: Absolute timestamp of this turn.
        audio_in: Incoming audio chunk (user speech).
        audio_out: Generated audio chunk (model speech).
        text_out: Generated text tokens.
        background_injections: Pending results from the Background Model.
        is_silence: Whether the user side is silent.
        should_interrupt: Whether the model should interrupt its own speech.
    """
    turn_id: int
    timestamp_ms: float
    audio_in: Optional[AudioChunk] = None
    audio_out: Optional[np.ndarray] = None
    text_out: Optional[list[int]] = None
    background_injections: list[dict] = field(default_factory=list)
    is_silence: bool = True
    should_interrupt: bool = False

    @property
    def has_user_speech(self) -> bool:
        """Whether the user is speaking in this micro-turn."""
        return self.audio_in is not None and self.audio_in.is_speech


class StreamingBuffer:
    """Ring-buffer for managing streaming audio/video state.

    Maintains a sliding window of recent micro-turns, providing context
    for the model's temporal understanding. This is the mechanism through
    which InteractFormer achieves TML's "time-awareness" capability.

    The buffer has two regions:
    - History window: Past micro-turns kept for context (e.g., 5 seconds = 25 turns)
    - Active window: Current micro-turn being processed
    """

    def __init__(
        self,
        max_history_ms: int = 5000,  # 5 seconds of context
        micro_turn_ms: int = 200,
        sample_rate: int = 24000,
    ):
        self.micro_turn_ms = micro_turn_ms
        self.sample_rate = sample_rate
        self.samples_per_turn = int(sample_rate * micro_turn_ms / 1000)

        max_turns = max_history_ms // micro_turn_ms
        self._history: deque[MicroTurn] = deque(maxlen=max_turns)
        self._pending_samples = np.array([], dtype=np.float32)
        self._current_turn_id: int = 0
        self._start_time_ms: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def current_turn_id(self) -> int:
        return self._current_turn_id

    @property
    def history_turns(self) -> list[MicroTurn]:
        """Return recent history as a list (oldest first)."""
        with self._lock:
            return list(self._history)

    def push_audio(self, samples: np.ndarray) -> list[MicroTurn]:
        """Push incoming audio samples and return completed micro-turns.

        Splits raw audio into 200ms chunks at micro-turn boundaries.
        Each complete chunk becomes a MicroTurn.

        Args:
            samples: Raw audio samples at self.sample_rate.

        Returns:
            List of newly completed MicroTurns.
        """
        with self._lock:
            if self._start_time_ms is None:
                self._start_time_ms = time.time() * 1000

            samples = np.asarray(samples, dtype=np.float32)
            if samples.ndim != 1:
                raise ValueError("streaming samples must be a one-dimensional array")
            if self._pending_samples.size:
                samples = np.concatenate([self._pending_samples, samples])

            completed_turns = []
            offset = 0
            while offset + self.samples_per_turn <= len(samples):
                chunk_samples = samples[offset:offset + self.samples_per_turn]
                timestamp = (
                    self._start_time_ms
                    + self._current_turn_id * self.micro_turn_ms
                )

                turn = MicroTurn(
                    turn_id=self._current_turn_id,
                    timestamp_ms=timestamp,
                    audio_in=AudioChunk(
                        samples=chunk_samples,
                        sample_rate=self.sample_rate,
                        timestamp_ms=timestamp,
                        chunk_id=self._current_turn_id,
                    ),
                )
                self._history.append(turn)
                completed_turns.append(turn)
                self._current_turn_id += 1
                offset += self.samples_per_turn

            # Preserve an incomplete tail for the next network/audio callback.
            # Dropping it made chunk boundaries depend on callback packet size
            # and caused gaps in long-running conversations.
            self._pending_samples = samples[offset:].copy()
            return completed_turns

    def push_background_result(
        self, result: dict, target_turn_id: int
    ) -> None:
        """Inject a background model result into the relevant micro-turn.

        Args:
            result: The background model's output (text, tool result, etc.).
            target_turn_id: The micro-turn to associate this result with.
        """
        with self._lock:
            for turn in reversed(self._history):
                if turn.turn_id == target_turn_id:
                    turn.background_injections.append(result)
                    return

    def get_context_window(
        self, num_turns: int = 25
    ) -> list[MicroTurn]:
        """Get the most recent N micro-turns as context."""
        with self._lock:
            turns = list(self._history)
            return turns[-num_turns:] if len(turns) > num_turns else turns

    def clear(self) -> None:
        """Reset the buffer."""
        with self._lock:
            self._history.clear()
            self._current_turn_id = 0
            self._start_time_ms = None
            self._pending_samples = np.array([], dtype=np.float32)


def chunk_audio_stream(
    audio_stream: Generator[np.ndarray, None, None],
    chunk_ms: int = 200,
    sample_rate: int = 24000,
) -> Generator[AudioChunk, None, None]:
    """Split a streaming audio generator into fixed-duration chunks.

    This is the entry point for audio preprocessing in InteractFormer.
    Unlike traditional pipelines that use VAD to find speech boundaries,
    we simply split at fixed 200ms intervals and let the model learn
    to interpret speech/silence patterns.

    Args:
        audio_stream: Generator yielding raw audio samples.
        chunk_ms: Duration of each chunk in milliseconds.
        sample_rate: Audio sample rate in Hz.

    Yields:
        AudioChunk objects at fixed intervals.
    """
    samples_per_chunk = int(sample_rate * chunk_ms / 1000)
    buffer = np.array([], dtype=np.float32)
    chunk_id = 0
    start_time = time.time() * 1000

    for samples in audio_stream:
        buffer = np.concatenate([buffer, samples])

        while len(buffer) >= samples_per_chunk:
            chunk_samples = buffer[:samples_per_chunk]
            buffer = buffer[samples_per_chunk:]

            yield AudioChunk(
                samples=chunk_samples,
                sample_rate=sample_rate,
                timestamp_ms=start_time + chunk_id * chunk_ms,
                chunk_id=chunk_id,
            )
            chunk_id += 1
