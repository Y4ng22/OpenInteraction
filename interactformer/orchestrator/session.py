"""
Streaming Session: Manages the lifecycle of an interaction session.

A session represents a continuous interaction between a user and
InteractFormer. Unlike traditional chatbot sessions (which are
request-response), an InteractFormer session is a persistent
streaming connection with:

- Continuous audio/video input
- Continuous model output (speech, text, actions)
- No artificial turn boundaries
- Graceful handling of connection drops and reconnects
- Session-level state (context, history, background tasks)

Each session maps to one user and maintains its own Temporal Grid,
Interaction Model state, and Background Model task queue.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import time
import uuid


class SessionState(Enum):
    """States in the session lifecycle."""
    INITIALIZING = "initializing"  # Setting up models and connections
    ACTIVE = "active"              # Live interaction in progress
    IDLE = "idle"                  # User is present but not interacting
    BACKGROUND_PROCESSING = "background_processing"  # S2 working async
    PAUSED = "paused"              # Temporarily paused (e.g., user mute)
    RECONNECTING = "reconnecting"  # Lost connection, trying to resume
    ENDING = "ending"              # Session is shutting down
    ENDED = "ended"                # Session is complete


@dataclass
class SessionConfig:
    """Configuration for a streaming session.

    Attributes:
        max_session_duration_ms: Maximum session length before reset.
        idle_timeout_ms: How long before an idle session ends.
        reconnect_grace_period_ms: How long to preserve state for reconnect.
        max_concurrent_background_tasks: S2 task limit per session.
        enable_session_persistence: Save session state across reconnects.
    """
    max_session_duration_ms: int = 3_600_000  # 1 hour
    idle_timeout_ms: int = 300_000  # 5 minutes
    reconnect_grace_period_ms: int = 60_000  # 1 minute
    max_concurrent_background_tasks: int = 5
    enable_session_persistence: bool = True


@dataclass
class SessionMetrics:
    """Runtime metrics for a session.

    These track the performance and behavior of a session for
    monitoring and optimization purposes.
    """
    # Timing
    started_at_ms: float = 0.0
    last_user_input_ms: float = 0.0
    last_model_output_ms: float = 0.0

    # Counters
    total_micro_turns: int = 0
    total_user_speech_turns: int = 0
    total_model_speech_turns: int = 0
    total_delegations: int = 0
    total_injections: int = 0
    total_interruptions: int = 0

    # Latency (ms)
    avg_thinker_latency_ms: float = 0.0
    avg_talker_latency_ms: float = 0.0
    avg_bridge_latency_ms: float = 0.0

    # Resource usage
    peak_memory_mb: float = 0.0
    avg_gpu_utilization: float = 0.0


class StreamingSession:
    """A single InteractFormer interaction session.

    Manages the full lifecycle of a user interaction, from initial
    connection through active conversation to graceful shutdown.

    Sessions are the unit of:
    - User identity and authentication
    - Conversation history and context
    - Temporal Grid state
    - Background task management
    - Metrics collection
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ):
        # Full 128-bit identifier avoids collisions and makes session IDs
        # unsuitable for casual guessing when exposed by a network service.
        self.session_id = session_id or uuid.uuid4().hex
        self.user_id = user_id or "anonymous"
        self.config = config or SessionConfig()

        # State
        self.state: SessionState = SessionState.INITIALIZING
        self.created_at_ms: float = time.time() * 1000
        self.metrics = SessionMetrics()

        # Context storage
        self.user_context: Dict[str, Any] = {}
        self.session_context: Dict[str, Any] = {}

        # Background task tracking (bounded)
        self._active_background_tasks: Dict[str, Any] = {}
        self._completed_background_tasks: list[str] = []
        self._max_task_history: int = 200  # Prune beyond this

    def start(self) -> None:
        """Start the session."""
        self.state = SessionState.ACTIVE
        self.metrics.started_at_ms = time.time() * 1000

    def pause(self) -> None:
        """Pause the session (user muted, etc.)."""
        if self.state == SessionState.ACTIVE:
            self.state = SessionState.PAUSED

    def resume(self) -> None:
        """Resume a paused session."""
        if self.state == SessionState.PAUSED:
            self.state = SessionState.ACTIVE

    def go_idle(self) -> None:
        """Mark session as idle (user present but not interacting)."""
        if self.state == SessionState.ACTIVE:
            self.state = SessionState.IDLE

    def go_active(self) -> None:
        """Mark session as active (user interacting again)."""
        if self.state in (SessionState.IDLE, SessionState.PAUSED):
            self.state = SessionState.ACTIVE

    def is_expired(self) -> bool:
        """Check if the session has exceeded its maximum duration."""
        elapsed = time.time() * 1000 - self.created_at_ms
        return elapsed > self.config.max_session_duration_ms

    def is_idle_timeout(self) -> bool:
        """Check if the session has been idle too long."""
        if self.metrics.last_user_input_ms == 0:
            return False
        elapsed = time.time() * 1000 - self.metrics.last_user_input_ms
        return elapsed > self.config.idle_timeout_ms

    def end(self) -> None:
        """End the session."""
        self.state = SessionState.ENDED

    def register_user_input(self) -> None:
        """Record a user input event."""
        self.metrics.last_user_input_ms = time.time() * 1000

    def register_model_output(self) -> None:
        """Record a model output event."""
        self.metrics.last_model_output_ms = time.time() * 1000

    def register_delegation(self, task_id: str) -> None:
        """Record a delegation to the Background Model."""
        self.metrics.total_delegations += 1
        self._active_background_tasks[task_id] = {
            "started_at_ms": time.time() * 1000,
            "status": "pending",
        }

    def register_injection(self) -> None:
        """Record a bridge injection event."""
        self.metrics.total_injections += 1

    def register_interruption(self) -> None:
        """Record an interruption event."""
        self.metrics.total_interruptions += 1

    def complete_background_task(self, task_id: str) -> None:
        """Mark a background task as complete."""
        if task_id in self._active_background_tasks:
            self._active_background_tasks[task_id]["status"] = "complete"
            self._active_background_tasks[task_id]["completed_at_ms"] = (
                time.time() * 1000
            )
            self._completed_background_tasks.append(task_id)
        # Prune if exceeding max history
        if len(self._completed_background_tasks) > self._max_task_history:
            self._completed_background_tasks = (
                self._completed_background_tasks[-self._max_task_history:]
            )
        # Prune completed tasks from active dict
        completed = [
            tid for tid, t in self._active_background_tasks.items()
            if t.get("status") == "complete"
        ]
        for tid in completed[-self._max_task_history:]:
            pass  # Keep recent ones
        for tid in completed[:-self._max_task_history]:
            del self._active_background_tasks[tid]

    @property
    def duration_ms(self) -> float:
        """Total session duration so far."""
        return time.time() * 1000 - self.created_at_ms

    @property
    def is_active(self) -> bool:
        """Whether the session is currently active."""
        return self.state in (
            SessionState.ACTIVE,
            SessionState.BACKGROUND_PROCESSING,
        )

    @property
    def summary(self) -> Dict[str, Any]:
        """Get a summary of the session state."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "state": self.state.value,
            "duration_s": self.duration_ms / 1000,
            "total_micro_turns": self.metrics.total_micro_turns,
            "total_delegations": self.metrics.total_delegations,
            "total_injections": self.metrics.total_injections,
            "total_interruptions": self.metrics.total_interruptions,
            "active_background_tasks": len(self._active_background_tasks),
        }
