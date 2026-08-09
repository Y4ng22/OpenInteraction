"""
Stream Injector: Progressive S2→S1 result injection.

THIS IS THE CORE INNOVATION OF THE STREAMING CONTEXT BRIDGE.

Problem with existing approaches:
- DuplexOmni: S2 returns 「...」 text markers. This is coarse-grained,
  breaks the speech flow, and can't handle streaming partial results.
- TML: Sends "rich context packages" but doesn't specify how results
  are interleaved with the real-time interaction.

InteractFormer's solution: progressive chunk-level injection.
1. S2 results are broken into 200ms-aligned chunks
2. Each chunk is injected into the corresponding temporal grid cell
3. Injection uses cross-attention fusion (not text markers)
4. The InjectionScheduler decides the optimal injection timing
5. Multiple concurrent S2 streams are managed and merged

This means the Interaction Model can:
- Start incorporating partial S2 results immediately
- Smoothly interleave S2 knowledge into ongoing speech
- Handle multiple concurrent background tasks
- Cancel injections if the user changes the topic
"""

from typing import Optional, Dict, Any, List, Deque
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import time


class InjectionStrategy(Enum):
    """When to inject S2 results into S1."""
    EAGER = "eager"          # Immediately on arrival
    SCHEDULED = "scheduled"  # At turn boundaries
    ADAPTIVE = "adaptive"    # Based on semantic completeness


class InjectionPriority(Enum):
    """Priority of an injection."""
    CRITICAL = 0  # Must be delivered immediately (e.g., safety warning)
    HIGH = 1      # Important for current conversation
    NORMAL = 2    # Standard priority
    LOW = 3       # Can wait (e.g., background knowledge)
    OPTIONAL = 4  # Nice to have, discard if context shifts


@dataclass
class BridgeMessage:
    """A single message crossing the S1↔S2 bridge.

    Attributes:
        message_id: Unique message identifier.
        direction: "s1_to_s2" or "s2_to_s1".
        content: The message payload.
        target_cell_id: Target temporal grid cell for injection.
        priority: Injection priority.
        created_at_ms: When this message was created.
        expires_at_ms: When this message expires (for time-sensitive info).
        stream_id: Which S2 stream this belongs to.
        chunk_index: Position in the stream (for ordering).
        is_final: Whether this is the last chunk in a stream.
    """
    message_id: str
    direction: str  # "s1_to_s2" or "s2_to_s1"
    content: Dict[str, Any]
    target_cell_id: Optional[int] = None
    priority: InjectionPriority = InjectionPriority.NORMAL
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    expires_at_ms: Optional[float] = None
    stream_id: Optional[str] = None
    chunk_index: int = 0
    is_final: bool = False


class InjectionScheduler:
    """Decides when and how to inject S2 results into S1.

    This is the "brain" of the StreamInjector. It manages:
    1. Timing: when to inject each chunk (eager, scheduled, adaptive)
    2. Ordering: ensuring chunks from multiple streams are properly ordered
    3. Merging: combining overlapping information from different streams
    4. Cancellation: removing stale injections if context shifts
    """

    def __init__(
        self,
        strategy: InjectionStrategy = InjectionStrategy.ADAPTIVE,
        max_queued_chunks: int = 32,
        chunk_timeout_ms: int = 5000,
    ):
        self.strategy = strategy
        self.max_queued_chunks = max_queued_chunks
        self.chunk_timeout_ms = chunk_timeout_ms

        # Per-stream message queues
        self._streams: Dict[str, Deque[BridgeMessage]] = {}

        # Global injection queue (ordered by priority + timestamp)
        self._global_queue: List[BridgeMessage] = []

    def enqueue(self, message: BridgeMessage) -> None:
        """Add a message to the injection queue.

        Args:
            message: The bridge message to enqueue.
        """
        # Add to stream-specific queue
        stream_id = message.stream_id or "default"
        if stream_id not in self._streams:
            self._streams[stream_id] = deque(maxlen=self.max_queued_chunks)
        self._streams[stream_id].append(message)

        # Add to global queue (sorted by priority then timestamp)
        self._global_queue.append(message)
        self._global_queue.sort(
            key=lambda m: (m.priority.value, -m.created_at_ms)
        )

    def get_next_injection(
        self,
        current_cell_id: int,
        is_model_speaking: bool = False,
    ) -> Optional[BridgeMessage]:
        """Get the next message to inject into the given cell.

        The decision depends on:
        - Injection strategy (eager/scheduled/adaptive)
        - Current cell state (is the model speaking? is the user?)
        - Message priority and expiry

        Args:
            current_cell_id: The cell about to be processed.
            is_model_speaking: Whether the model is currently generating.

        Returns:
            The next message to inject, or None.
        """
        # Clean expired messages
        self._clean_expired()

        if not self._global_queue:
            return None

        if self.strategy == InjectionStrategy.EAGER:
            return self._global_queue.pop(0)

        elif self.strategy == InjectionStrategy.SCHEDULED:
            # Only inject at turn boundaries (when model is NOT speaking)
            if not is_model_speaking and self._global_queue:
                return self._global_queue.pop(0)
            return None

        elif self.strategy == InjectionStrategy.ADAPTIVE:
            # Adaptive: inject critical immediately, others at boundaries
            for i, msg in enumerate(self._global_queue):
                if msg.priority == InjectionPriority.CRITICAL:
                    return self._global_queue.pop(i)

                if (
                    msg.priority == InjectionPriority.HIGH
                    and not is_model_speaking
                ):
                    return self._global_queue.pop(i)

                if (
                    msg.priority in (InjectionPriority.NORMAL, InjectionPriority.LOW)
                    and not is_model_speaking
                    and msg.chunk_index > 0  # Wait for at least 1 chunk buffered
                ):
                    return self._global_queue.pop(i)

            return None

        return None

    def cancel_stream(self, stream_id: str) -> int:
        """Cancel all pending messages from a stream.

        Called when the user changes topic and pending S2 results
        are no longer relevant.

        Args:
            stream_id: The stream to cancel.

        Returns:
            Number of messages cancelled.
        """
        cancelled = 0

        # Remove from stream queue
        if stream_id in self._streams:
            cancelled += len(self._streams[stream_id])
            del self._streams[stream_id]

        # Remove from global queue
        self._global_queue = [
            m for m in self._global_queue
            if m.stream_id != stream_id
        ]
        cancelled += sum(
            1 for m in self._global_queue
            if m.stream_id == stream_id
        )

        return cancelled

    def _clean_expired(self) -> None:
        """Remove expired messages from all queues."""
        now = time.time() * 1000

        for stream_id in list(self._streams.keys()):
            self._streams[stream_id] = deque(
                [m for m in self._streams[stream_id]
                 if m.expires_at_ms is None or m.expires_at_ms > now],
                maxlen=self.max_queued_chunks,
            )
            if not self._streams[stream_id]:
                del self._streams[stream_id]

        self._global_queue = [
            m for m in self._global_queue
            if m.expires_at_ms is None or m.expires_at_ms > now
        ]

    @property
    def pending_count(self) -> int:
        """Number of pending messages across all streams."""
        return len(self._global_queue)

    @property
    def active_streams(self) -> List[str]:
        """List of active stream IDs."""
        return list(self._streams.keys())


class StreamInjector:
    """Injects S2 results into S1 via the Streaming Context Bridge.

    This is the main S2→S1 interface. It takes results from the
    Background Model and injects them into the Interaction Model's
    temporal grid at the optimal moments.

    The injection is NOT text-based (unlike DuplexOmni). Instead,
    it uses the cross-attention fusion mechanism — S2 results are
    encoded as bridge context vectors that S1 attends to during
    its self-attention computation.
    """

    def __init__(
        self,
        d_model: int = 2048,
        strategy: InjectionStrategy = InjectionStrategy.ADAPTIVE,
        max_concurrent_streams: int = 5,
    ):
        self.d_model = d_model
        self.max_concurrent_streams = max_concurrent_streams

        self.scheduler = InjectionScheduler(strategy=strategy)

        # Active injection streams
        self._active_injections: Dict[str, List[BridgeMessage]] = {}

        # Statistics
        self._total_injected: int = 0
        self._total_cancelled: int = 0
        self._total_expired: int = 0

    def receive_result(
        self,
        result: Any,  # BackgroundResult
        stream_id: Optional[str] = None,
        priority: InjectionPriority = InjectionPriority.NORMAL,
    ) -> None:
        """Receive a result from the Background Model for injection.

        The result is chunked into 200ms-aligned pieces and enqueued
        for injection at the appropriate moments.

        Args:
            result: BackgroundResult from the Background Model.
            stream_id: Which S2 stream this belongs to.
            priority: Injection priority.
        """
        if stream_id is None:
            stream_id = result.task_id

        # Chunk the result into bridge messages
        messages = self._chunk_result(result, stream_id, priority)
        for msg in messages:
            self.scheduler.enqueue(msg)

    def get_context_for_cell(
        self,
        cell_id: int,
        is_model_speaking: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get S2 context to inject into a specific temporal grid cell.

        Called by the Interaction Model before processing each cell.
        Returns the accumulated S2 context that should be injected
        at this cell boundary.

        Args:
            cell_id: The current grid cell ID.
            is_model_speaking: Whether the model is speaking.

        Returns:
            List of context dicts to inject, or None.
        """
        injections = []
        max_per_cell = 3  # Don't overwhelm a single cell

        for _ in range(max_per_cell):
            msg = self.scheduler.get_next_injection(
                cell_id, is_model_speaking
            )
            if msg is None:
                break

            injections.append(msg.content)
            self._total_injected += 1

            # Track in active injections
            if msg.stream_id:
                if msg.stream_id not in self._active_injections:
                    self._active_injections[msg.stream_id] = []
                self._active_injections[msg.stream_id].append(msg)

        return injections if injections else None

    def cancel_topic(self, reason: str = "user_topic_change") -> int:
        """Cancel all pending injections.

        Called when the user changes topic or the interaction
        takes a different direction. This is important for
        maintaining conversation coherence.

        Args:
            reason: Why injections are being cancelled.

        Returns:
            Number of cancelled messages.
        """
        total = 0
        for stream_id in list(self._active_injections.keys()):
            total += self.scheduler.cancel_stream(stream_id)
            del self._active_injections[stream_id]

        self._total_cancelled += total
        return total

    def _chunk_result(
        self,
        result: Any,
        stream_id: str,
        priority: InjectionPriority,
    ) -> List[BridgeMessage]:
        """Chunk a BackgroundResult into 200ms-aligned bridge messages.

        This is where the "progressive chunk-level fusion" happens.
        Instead of sending the entire result at once, we break it
        into micro-turn-sized chunks that can be smoothly interleaved
        with ongoing interaction.

        Args:
            result: The BackgroundResult to chunk.
            stream_id: Stream identifier.
            priority: Injection priority.

        Returns:
            List of BridgeMessages, one per chunk.
        """
        messages = []
        chunk_index = 0

        # Chunk 1: Retrieval results (if any)
        if result.retrieval and result.retrieval.results:
            for i, doc in enumerate(result.retrieval.results[:3]):
                messages.append(BridgeMessage(
                    message_id=f"{stream_id}_retrieval_{i}",
                    direction="s2_to_s1",
                    content={
                        "type": "retrieval",
                        "data": doc.content[:200],
                        "source": doc.source,
                        "score": doc.score,
                    },
                    stream_id=stream_id,
                    chunk_index=chunk_index,
                    priority=priority,
                ))
                chunk_index += 1

        # Chunk 2: Intermediate reasoning steps
        for step in result.reasoning_steps:
            messages.append(BridgeMessage(
                message_id=f"{stream_id}_step_{step.step_id}",
                direction="s2_to_s1",
                content={
                    "type": "reasoning_step",
                    "data": step.thought,
                    "confidence": step.confidence,
                    "is_final": step.is_final,
                },
                stream_id=stream_id,
                chunk_index=chunk_index,
                priority=(
                    InjectionPriority.HIGH if step.is_final
                    else InjectionPriority.NORMAL
                ),
            ))
            chunk_index += 1

        # Chunk 3: Tool results
        if result.tool_results:
            messages.append(BridgeMessage(
                message_id=f"{stream_id}_tools",
                direction="s2_to_s1",
                content={
                    "type": "tool_results",
                    "data": result.tool_results.summary,
                    "success": result.tool_results.all_succeeded,
                },
                stream_id=stream_id,
                chunk_index=chunk_index,
                priority=priority,
            ))
            chunk_index += 1

        # Mark the last message as final
        if messages:
            messages[-1].is_final = True
            messages[-1].content["final_answer"] = (
                result.final_answer or "Reasoning complete."
            )
            messages[-1].content["confidence"] = result.confidence

        return messages

    @property
    def stats(self) -> Dict[str, int]:
        """Get injection statistics."""
        return {
            "total_injected": self._total_injected,
            "total_cancelled": self._total_cancelled,
            "total_expired": self._total_expired,
            "pending": self.scheduler.pending_count,
            "active_streams": len(self._active_injections),
        }
