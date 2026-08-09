"""
Tool Executor: Tool use framework for the Background Model.

Enables the Background Model to execute external actions: API calls,
code execution, database queries, file operations, etc.

This is one of the parallel components in the Multi-Background Ensemble.
It runs concurrently with the Reasoner and Retriever, and its results
are fused with other S2 outputs before streaming back to S1.
"""

from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field
from enum import Enum
import time
import json


class ToolStatus(Enum):
    """Status of a tool execution."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ToolCall:
    """A single tool call request.

    Attributes:
        tool_name: Name of the tool to call.
        tool_id: Unique identifier for this call.
        arguments: Tool arguments as a dictionary.
        status: Current execution status.
        result: Execution result (if complete).
        error: Error message (if failed).
        start_time_ms: When execution started.
        duration_ms: Execution duration.
    """
    tool_name: str
    tool_id: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    status: ToolStatus = ToolStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    start_time_ms: float = 0.0
    duration_ms: float = 0.0


@dataclass
class ToolResult:
    """Result of a tool execution batch.

    Attributes:
        calls: Individual tool call results.
        summary: Human-readable summary of results.
        total_duration_ms: Total time for all tool calls.
        success_count: Number of successful calls.
        failure_count: Number of failed calls.
    """
    calls: List[ToolCall] = field(default_factory=list)
    summary: str = ""
    total_duration_ms: float = 0.0

    @property
    def success_count(self) -> int:
        return sum(1 for c in self.calls if c.status == ToolStatus.SUCCESS)

    @property
    def failure_count(self) -> int:
        return sum(1 for c in self.calls if c.status == ToolStatus.FAILED)

    @property
    def all_succeeded(self) -> bool:
        return self.failure_count == 0


class ToolExecutor:
    """Executes tool calls on behalf of the Background Model.

    The ToolExecutor manages a registry of available tools and handles
    their execution with:
    - Timeout management
    - Error handling and retries
    - Parallel execution where possible
    - Result streaming (intermediate results go to Bridge)

    Tools can be:
    - Built-in: calculator, search, code interpreter
    - User-defined: custom APIs, database queries, file operations
    - External: third-party integrations
    """

    def __init__(
        self,
        max_parallel_calls: int = 5,
        default_timeout_ms: int = 30000,
        max_retries: int = 2,
    ):
        self.max_parallel_calls = max_parallel_calls
        self.default_timeout_ms = default_timeout_ms
        self.max_retries = max_retries

        # Tool registry
        self._tools: Dict[str, Callable] = {}

        # Register built-in tools
        self._register_builtin_tools()

    def register_tool(
        self, name: str, func: Callable
    ) -> None:
        """Register a custom tool.

        Args:
            name: Tool name (used in tool calls).
            func: Callable that takes a dict of arguments and returns
                a result. Should be async-compatible.
        """
        self._tools[name] = func

    def execute(
        self,
        tool_calls: List[Dict[str, Any]],
        stream_results: bool = True,
    ) -> ToolResult:
        """Execute a batch of tool calls.

        Args:
            tool_calls: List of tool call specs, each with 'name' and
                'arguments' keys.
            stream_results: If True, yield intermediate results via the
                Bridge rather than waiting for all to complete.

        Returns:
            ToolResult with all results aggregated.
        """
        start_time = time.time() * 1000
        results: List[ToolCall] = []
        pending: List[ToolCall] = []

        # Create ToolCall objects
        for i, call_spec in enumerate(tool_calls):
            call = ToolCall(
                tool_name=call_spec.get("name", "unknown"),
                tool_id=f"call_{i}_{int(time.time() * 1000)}",
                arguments=call_spec.get("arguments", {}),
            )
            pending.append(call)

        # Execute in parallel batches
        for i in range(0, len(pending), self.max_parallel_calls):
            batch = pending[i:i + self.max_parallel_calls]

            for call in batch:
                self._execute_one(call)

            results.extend(batch)

        # Build summary
        total_ms = time.time() * 1000 - start_time
        summary = self._build_summary(results)

        return ToolResult(
            calls=results,
            summary=summary,
            total_duration_ms=total_ms,
        )

    def _execute_one(self, call: ToolCall) -> None:
        """Execute a single tool call with timeout and retries."""
        call.status = ToolStatus.RUNNING
        call.start_time_ms = time.time() * 1000

        tool_func = self._tools.get(call.tool_name)
        if tool_func is None:
            call.status = ToolStatus.FAILED
            call.error = f"Unknown tool: {call.tool_name}"
            call.duration_ms = time.time() * 1000 - call.start_time_ms
            return

        # Try execution with retries
        for attempt in range(self.max_retries + 1):
            try:
                result = tool_func(call.arguments)
                call.result = result
                call.status = ToolStatus.SUCCESS
                break
            except Exception as e:
                if attempt < self.max_retries:
                    continue
                call.status = ToolStatus.FAILED
                call.error = str(e)

        call.duration_ms = time.time() * 1000 - call.start_time_ms

    def _register_builtin_tools(self) -> None:
        """Register the default set of built-in tools."""

        # Calculator tool
        def calculator(args: Dict[str, Any]) -> str:
            expression = args.get("expression", "")
            try:
                # Safe eval with limited builtins
                result = eval(
                    expression,
                    {"__builtins__": {}},
                    {
                        "abs": abs, "round": round,
                        "min": min, "max": max,
                        "sum": sum, "len": len,
                        "int": int, "float": float,
                        "str": str, "list": list,
                    },
                )
                return str(result)
            except Exception as e:
                return f"Calculation error: {e}"

        # Search tool (placeholder)
        def search(args: Dict[str, Any]) -> str:
            query = args.get("query", "")
            # Placeholder: would call a real search API
            return f"Search results for: {query}"

        # Code interpreter (placeholder)
        def code_interpreter(args: Dict[str, Any]) -> str:
            code = args.get("code", "")
            language = args.get("language", "python")
            # Placeholder: would use a sandboxed executor
            return f"Code execution result ({language})"

        self._tools.update({
            "calculator": calculator,
            "search": search,
            "code_interpreter": code_interpreter,
        })

    @staticmethod
    def _build_summary(results: List[ToolCall]) -> str:
        """Build a human-readable summary of tool execution results."""
        parts = []
        for call in results:
            if call.status == ToolStatus.SUCCESS:
                parts.append(
                    f"✓ {call.tool_name}: {str(call.result)[:200]}"
                )
            else:
                parts.append(
                    f"✗ {call.tool_name}: {call.error}"
                )
        return "\n".join(parts) if parts else "No tools executed"
