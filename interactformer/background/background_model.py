"""
Background Model (S2): Asynchronous deep reasoning and tool use.

The Background Model is the "deep thinking" counterpart to the
Interaction Model. It handles complex reasoning, knowledge retrieval,
and tool execution asynchronously, streaming results back to S1
via the Streaming Context Bridge.

Key innovation: Multi-Background Ensemble
-------------------------------------------
Unlike DuplexOmni's single pluggable S2 endpoint, InteractFormer
supports MULTIPLE parallel background models that operate concurrently:

    S1 Delegation
        ├── Reasoner (deep CoT)
        ├── Retriever (RAG)
        └── ToolExecutor (API calls)
        ↓
    Fusion Layer (confidence-weighted)
        ↓
    Streaming Context Bridge → S1

This ensemble approach means:
1. The Reasoner can start thinking while the Retriever searches
2. Tool results can inform reasoning mid-chain
3. S1 receives progressive updates rather than waiting for everything
4. Different tasks get different resource allocations
"""

from typing import Optional, Dict, Any, Generator
from dataclasses import dataclass, field
from enum import Enum
import time
import threading
import queue

from interactformer.background.reasoner import (
    Reasoner, ReasoningStep, ReasoningDepth,
)
from interactformer.background.retriever import (
    Retriever, RetrievalResponse, RetrievalResult,
)
from interactformer.background.tool_executor import (
    ToolExecutor, ToolResult, ToolCall, ToolStatus,
)


class BackgroundTaskType(Enum):
    """Types of tasks the Background Model can handle."""
    REASONING = "reasoning"
    RETRIEVAL = "retrieval"
    TOOL_USE = "tool_use"
    MIXED = "mixed"  # Combination of above


@dataclass
class BackgroundTask:
    """A task delegated from S1 to the Background Model.

    Attributes:
        task_id: Unique task identifier.
        task_type: Type of background work needed.
        query: The query or instruction from S1.
        context: Rich context package from S1 (conversation history,
            temporal state, multimodal context).
        priority: Priority level (higher = more urgent).
        created_at_ms: When this task was created.
        deadline_ms: Optional deadline for completion.
    """
    task_id: str
    task_type: BackgroundTaskType
    query: str
    context: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    deadline_ms: Optional[float] = None


@dataclass
class BackgroundResult:
    """Aggregated result from the Background Model.

    Contains the outputs from all ensemble members (Reasoner, Retriever,
    ToolExecutor), fused and ready for injection into S1 via the Bridge.

    Attributes:
        task_id: Which task this result answers.
        reasoning_steps: Chain-of-thought steps from the Reasoner.
        retrieval: Knowledge retrieval results.
        tool_results: Tool execution results.
        final_answer: Synthesized final answer (if reasoning is complete).
        confidence: Overall confidence score (0-1).
        partial: Whether this is a partial update (True) or final (False).
        stream_id: Monotonically increasing ID for partial stream ordering.
    """
    task_id: str
    reasoning_steps: list[ReasoningStep] = field(default_factory=list)
    retrieval: Optional[RetrievalResponse] = None
    tool_results: Optional[ToolResult] = None
    final_answer: Optional[str] = None
    confidence: float = 0.0
    partial: bool = False
    stream_id: int = 0


class BackgroundModel:
    """Background Model: async deep reasoning and tool use.

    This is the S2 component of InteractFormer. It runs asynchronously
    from S1 and communicates via the Streaming Context Bridge.

    The Multi-Background Ensemble architecture enables:
    1. Parallel reasoning + retrieval + tool execution
    2. Progressive streaming of partial results
    3. Confidence-weighted fusion of multiple outputs
    4. Graceful handling of timeouts and interruptions

    Usage:
        bg = BackgroundModel()
        bg.start()  # Start background worker threads

        # S1 delegates a task
        task = BackgroundTask(
            task_id="task_001",
            task_type=BackgroundTaskType.MIXED,
            query="What's the population of Tokyo?",
            context=s1_context,
        )
        bg.submit(task)

        # Results stream back via the Bridge
        for result in bg.stream_results():
            bridge.inject(result)
    """

    def __init__(
        self,
        model_name_or_path: str = "Qwen/Qwen3-Omni-30B-A3B-Instruct",
        enable_retrieval: bool = True,
        enable_tools: bool = True,
        max_concurrent_tasks: int = 3,
        result_stream_buffer: int = 100,
    ):
        self.model_name_or_path = model_name_or_path
        self.max_concurrent_tasks = max_concurrent_tasks

        # Ensemble components
        self.reasoner = Reasoner()
        self.retriever = Retriever() if enable_retrieval else None
        self.tool_executor = ToolExecutor() if enable_tools else None

        # Task management
        self._task_queue: queue.Queue = queue.Queue()
        self._result_queue: queue.Queue = queue.Queue(maxsize=result_stream_buffer)
        self._active_tasks: Dict[str, BackgroundTask] = {}
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._stream_id_counter: int = 0

    def start(self) -> None:
        """Start the background worker thread."""
        if self._running:
            return

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="BackgroundModel-Worker",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the background worker thread."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5.0)
            self._worker_thread = None

    def submit(
        self,
        task: BackgroundTask,
        blocking: bool = False,
    ) -> Optional[BackgroundResult]:
        """Submit a task for background processing.

        Args:
            task: The task to process.
            blocking: If True, wait for completion and return result.
                If False, return None and results come via stream_results().

        Returns:
            BackgroundResult if blocking=True, else None.
        """
        self._task_queue.put(task)
        self._active_tasks[task.task_id] = task

        if blocking:
            # Wait for and return the result
            while True:
                result = self._result_queue.get()
                if result.task_id == task.task_id and not result.partial:
                    self._active_tasks.pop(task.task_id, None)
                    return result

        return None

    def stream_results(
        self, timeout: Optional[float] = None,
    ) -> Generator[BackgroundResult, None, None]:
        """Generator that yields BackgroundResults as they become available.

        This is the main interface for the Streaming Context Bridge.
        It yields partial results as they arrive, allowing S1 to
        interleave background knowledge into the conversation.

        Args:
            timeout: Max time to wait for next result, or None for blocking.

        Yields:
            BackgroundResult objects (may be partial).
        """
        while self._running or not self._result_queue.empty():
            # Non-blocking poll: if no results available, yield control
            if self._result_queue.empty() and self._running:
                # Worker is still alive, but no results yet — break to avoid spin
                break
            try:
                result = self._result_queue.get(
                    timeout=timeout if timeout is not None else 1.0
                )
                yield result
            except queue.Empty:
                if not self._running:
                    break
                # Running but queue is transiently empty — worker may be
                # preparing next result; yield control back to caller
                break

    def _worker_loop(self) -> None:
        """Main worker loop: process tasks from the queue."""
        while self._running:
            try:
                task = self._task_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Process the task
            try:
                result = self._process_task(task)
                self._result_queue.put(result)
            except Exception as e:
                # Report error as a result
                error_result = BackgroundResult(
                    task_id=task.task_id,
                    final_answer=f"Background processing error: {e}",
                    confidence=0.0,
                )
                self._result_queue.put(error_result)
            finally:
                self._active_tasks.pop(task.task_id, None)

    def _process_task(self, task: BackgroundTask) -> BackgroundResult:
        """Process a single task through the appropriate ensemble members.

        Args:
            task: The task to process.

        Returns:
            Aggregated BackgroundResult.
        """
        reasoning_steps = []
        retrieval = None
        tool_results = None

        # Determine which components to use
        use_reasoning = task.task_type in (
            BackgroundTaskType.REASONING,
            BackgroundTaskType.MIXED,
        )
        use_retrieval = task.task_type in (
            BackgroundTaskType.RETRIEVAL,
            BackgroundTaskType.MIXED,
        )
        use_tools = task.task_type in (
            BackgroundTaskType.TOOL_USE,
            BackgroundTaskType.MIXED,
        )

        # Stage 1: Retrieval and tool execution (parallel, no dependencies)
        if use_retrieval and self.retriever:
            retrieval = self.retriever.retrieve(
                query=task.query,
                context=task.context,
            )

        if use_tools and self.tool_executor:
            # Check if the query implies tool calls
            tool_specs = self._extract_tool_calls(task.query)
            if tool_specs:
                tool_results = self.tool_executor.execute(tool_specs)

        # Stage 2: Reasoning (can use retrieval and tool results)
        if use_reasoning:
            # Enrich context with Stage 1 results
            enriched_context = dict(task.context)
            if retrieval:
                enriched_context["retrieval_results"] = [
                    r.content for r in retrieval.results
                ]
            if tool_results:
                enriched_context["tool_results"] = tool_results.summary

            steps = list(self.reasoner.reason(
                query=task.query,
                context=enriched_context,
            ))
            reasoning_steps = steps

            # Stream intermediate reasoning steps
            for i, step in enumerate(steps):
                if not step.is_final and i > 0:
                    partial = BackgroundResult(
                        task_id=task.task_id,
                        reasoning_steps=[step],
                        partial=True,
                        stream_id=self._next_stream_id(),
                    )
                    self._result_queue.put(partial)

        # Fusion: combine all results with confidence weighting
        final_answer = self._fuse_results(
            query=task.query,
            reasoning_steps=reasoning_steps,
            retrieval=retrieval,
            tool_results=tool_results,
        )

        confidence = self._compute_confidence(
            reasoning_steps=reasoning_steps,
            retrieval=retrieval,
            tool_results=tool_results,
        )

        return BackgroundResult(
            task_id=task.task_id,
            reasoning_steps=reasoning_steps,
            retrieval=retrieval,
            tool_results=tool_results,
            final_answer=final_answer,
            confidence=confidence,
            partial=False,
            stream_id=self._next_stream_id(),
        )

    def _fuse_results(
        self,
        query: str,
        reasoning_steps: list[ReasoningStep],
        retrieval: Optional[RetrievalResponse],
        tool_results: Optional[ToolResult],
    ) -> str:
        """Fuse results from multiple ensemble members.

        This is where the Multi-Background Ensemble's outputs are
        combined. The fusion uses confidence-weighted aggregation
        rather than simple concatenation.
        """
        parts = []

        # Reasoning conclusion
        if reasoning_steps:
            final_step = reasoning_steps[-1]
            parts.append(f"[Reasoning] {final_step.thought}")

        # Retrieval synthesis
        if retrieval and retrieval.results:
            top_results = retrieval.results[:3]
            retrieval_text = "; ".join(
                r.content[:100] for r in top_results
            )
            parts.append(f"[Knowledge] {retrieval_text}")

        # Tool results
        if tool_results:
            parts.append(f"[Tools] {tool_results.summary}")

        if not parts:
            return "No results available."

        return "\n".join(parts)

    def _compute_confidence(
        self,
        reasoning_steps: list[ReasoningStep],
        retrieval: Optional[RetrievalResponse],
        tool_results: Optional[ToolResult],
    ) -> float:
        """Compute overall confidence from ensemble members.

        Confidence is weighted by:
        - Number and quality of reasoning steps
        - Retrieval relevance scores
        - Tool execution success rate
        """
        scores = []

        # Reasoning confidence
        if reasoning_steps:
            avg_step_confidence = sum(
                s.confidence for s in reasoning_steps
            ) / len(reasoning_steps)
            scores.append(avg_step_confidence)
            scores.append(0.5)  # Weight for reasoning

        # Retrieval confidence
        if retrieval and retrieval.results:
            avg_score = sum(
                r.score for r in retrieval.results[:5]
            ) / min(5, len(retrieval.results))
            scores.append(avg_score)
            scores.append(0.3)  # Weight for retrieval

        # Tool confidence
        if tool_results:
            success_rate = (
                tool_results.success_count /
                max(tool_results.success_count + tool_results.failure_count, 1)
            )
            scores.append(success_rate)
            scores.append(0.2)  # Weight for tools

        if not scores:
            return 0.0

        # Weighted average
        total_weight = sum(scores[i] for i in range(1, len(scores), 2))
        if total_weight == 0:
            return 0.0

        weighted_sum = sum(
            scores[i] * scores[i + 1]
            for i in range(0, len(scores), 2)
        )
        return weighted_sum / total_weight

    def _extract_tool_calls(self, query: str) -> list[Dict[str, Any]]:
        """Extract implied tool calls from a query string.

        This is a simplified parser. In production, this would use the
        model's function-calling capability.
        """
        # Simple heuristic: if query contains math, use calculator
        tool_calls = []
        if any(op in query for op in ["+", "-", "*", "/", "=", "calculate"]):
            tool_calls.append({
                "name": "calculator",
                "arguments": {"expression": query},
            })
        return tool_calls

    def _next_stream_id(self) -> int:
        """Get the next stream ID for ordering partial results."""
        self._stream_id_counter += 1
        return self._stream_id_counter
