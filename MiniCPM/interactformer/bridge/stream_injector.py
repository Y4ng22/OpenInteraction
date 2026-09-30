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
import threading


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
        self._lock = threading.RLock()

    def enqueue(self, message: BridgeMessage) -> None:
        """Add a message to the injection queue.

        Args:
            message: The bridge message to enqueue.
        """
        with self._lock:
            stream_id = message.stream_id or "default"
            if message.expires_at_ms is None:
                message.expires_at_ms = message.created_at_ms + self.chunk_timeout_ms
            if stream_id not in self._streams:
                self._streams[stream_id] = deque()

            # Keep both indexes bounded.  The old deque(maxlen=...) silently
            # evicted from the per-stream view while leaving the same message
            # in the global list, so sustained S2 traffic grew memory forever.
            if len(self._streams[stream_id]) >= self.max_queued_chunks:
                evicted = self._streams[stream_id].popleft()
                self._global_queue = [m for m in self._global_queue if m is not evicted]

            self._streams[stream_id].append(message)
            self._global_queue.append(message)
            self._global_queue.sort(
                key=lambda m: (m.priority.value, m.created_at_ms, m.chunk_index)
            )

            while len(self._global_queue) > self.max_queued_chunks:
                evicted = self._global_queue.pop()
                evicted_stream = evicted.stream_id or "default"
                stream_queue = self._streams.get(evicted_stream)
                if stream_queue is not None:
                    self._streams[evicted_stream] = deque(
                        m for m in stream_queue if m is not evicted
                    )
                    if not self._streams[evicted_stream]:
                        del self._streams[evicted_stream]

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
        with self._lock:
            self._clean_expired_locked()
            if not self._global_queue:
                return None

            for i, msg in enumerate(self._global_queue):
                stream_id = msg.stream_id or "default"
                stream_queue = self._streams.get(stream_id)
                # Never deliver a high-priority final chunk ahead of earlier
                # chunks in the same stream.
                if stream_queue and stream_queue[0] is not msg:
                    continue

                eligible = self.strategy == InjectionStrategy.EAGER
                if self.strategy == InjectionStrategy.SCHEDULED:
                    eligible = not is_model_speaking
                elif self.strategy == InjectionStrategy.ADAPTIVE:
                    eligible = (
                        msg.priority == InjectionPriority.CRITICAL
                        or not is_model_speaking
                    )

                if eligible:
                    return self._pop_at_locked(i)
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
        with self._lock:
            cancelled = sum(1 for m in self._global_queue if m.stream_id == stream_id)
            self._streams.pop(stream_id, None)
            self._global_queue = [m for m in self._global_queue if m.stream_id != stream_id]
            return cancelled

    def _clean_expired(self) -> None:
        """Remove expired messages from all queues."""
        with self._lock:
            self._clean_expired_locked()

    def _clean_expired_locked(self) -> None:
        now = time.time() * 1000

        for stream_id in list(self._streams.keys()):
            self._streams[stream_id] = deque(
                [m for m in self._streams[stream_id]
                 if m.expires_at_ms is None or m.expires_at_ms > now],
            )
            if not self._streams[stream_id]:
                del self._streams[stream_id]

        self._global_queue = [
            m for m in self._global_queue
            if m.expires_at_ms is None or m.expires_at_ms > now
        ]

    def _pop_at_locked(self, index: int) -> BridgeMessage:
        message = self._global_queue.pop(index)
        stream_id = message.stream_id or "default"
        stream_queue = self._streams.get(stream_id)
        if stream_queue is not None:
            self._streams[stream_id] = deque(m for m in stream_queue if m is not message)
            if not self._streams[stream_id]:
                del self._streams[stream_id]
        return message

    @property
    def pending_count(self) -> int:
        """Number of pending messages across all streams."""
        with self._lock:
            return len(self._global_queue)

    @property
    def active_streams(self) -> List[str]:
        """List of active stream IDs."""
        with self._lock:
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
        self._seen_message_ids: Dict[str, set[str]] = {}
        self._next_chunk_index: Dict[str, int] = {}
        self._lock = threading.RLock()

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

        with self._lock:
            if (
                stream_id not in self._seen_message_ids
                and len(self._seen_message_ids) >= self.max_concurrent_streams
            ):
                raise RuntimeError("Maximum concurrent bridge streams reached")

            seen = self._seen_message_ids.setdefault(stream_id, set())
            next_index = self._next_chunk_index.get(stream_id, 0)

            # Final results repeat reasoning/retrieval items already sent as
            # partials.  Stable message IDs let us suppress those duplicates.
            for msg in self._chunk_result(result, stream_id, priority):
                if msg.message_id in seen:
                    continue
                seen.add(msg.message_id)
                msg.chunk_index = next_index
                next_index += 1
                self.scheduler.enqueue(msg)
            self._next_chunk_index[stream_id] = next_index

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
                if msg.is_final:
                    self._active_injections.pop(msg.stream_id, None)
                    self._seen_message_ids.pop(msg.stream_id, None)
                    self._next_chunk_index.pop(msg.stream_id, None)

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
        stream_ids = set(self.scheduler.active_streams)
        stream_ids.update(self._active_injections.keys())
        stream_ids.update(self._seen_message_ids.keys())
        for stream_id in stream_ids:
            total += self.scheduler.cancel_stream(stream_id)
            self._active_injections.pop(stream_id, None)
            self._seen_message_ids.pop(stream_id, None)
            self._next_chunk_index.pop(stream_id, None)

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

        # A final answer must be its own message.  Previously, error-only and
        # answer-only BackgroundResults produced zero messages and vanished;
        # attaching the answer to a repeated reasoning chunk also caused it to
        # disappear during partial-result de-duplication.
        if result.final_answer and not result.partial:
            messages.append(BridgeMessage(
                message_id=f"{stream_id}_final",
                direction="s2_to_s1",
                content={
                    "type": "final_answer",
                    "data": result.final_answer,
                    "confidence": result.confidence,
                },
                stream_id=stream_id,
                chunk_index=chunk_index,
                priority=InjectionPriority.HIGH,
            ))
            chunk_index += 1

        # Mark the last message as final
        if messages and not result.partial:
            messages[-1].is_final = True

        return messages

    @property
    def stats(self) -> Dict[str, int]:
        """Get injection statistics."""
        return {
            "total_injected": self._total_injected,
            "total_cancelled": self._total_cancelled,
            "total_expired": self._total_expired,
            "pending": self.scheduler.pending_count,
            "active_streams": len(self.scheduler.active_streams),
        }
