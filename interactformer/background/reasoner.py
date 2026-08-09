"""
Reasoner: Deep chain-of-thought reasoning for the Background Model.

The Reasoner performs multi-step reasoning on complex queries delegated
by the Interaction Model. It runs asynchronously and streams intermediate
results back to S1 via the Streaming Context Bridge.

Unlike a standard LLM call, the Reasoner:
1. Receives a rich context package (not a standalone query)
2. Performs multi-step chain-of-thought
3. Streams intermediate conclusions (not just final answer)
4. Can be interrupted if S1 gets a more urgent user input
"""

from typing import Optional, Generator, Dict, Any
from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod


class ReasoningDepth(Enum):
    """Depth of reasoning chain."""
    SHALLOW = "shallow"    # Quick fact lookup / simple reasoning
    DEEP = "deep"          # Multi-step chain-of-thought
    ADAPTIVE = "adaptive"  # Model decides depth based on complexity


@dataclass
class ReasoningStep:
    """A single step in the reasoning chain.

    Each step is streamed back to S1 as it's generated, allowing the
    Interaction Model to interleave partial results into the conversation
    (e.g., "Let me think about that..." or showing intermediate steps).
    """
    step_id: int
    thought: str
    confidence: float
    is_final: bool = False
    references: list[str] = None

    def __post_init__(self):
        if self.references is None:
            self.references = []


class ReasoningBackend(ABC):
    """Abstract interface for pluggable S2 reasoning backends.

    Implementations can range from lightweight deterministic algorithms
    to full LLM-based chain-of-thought engines.

    All backends must support streaming partial outputs so S1 can
    interleave intermediate results into the interaction.
    """

    @abstractmethod
    def generate_stream(
        self, query: str, context: Dict[str, Any]
    ) -> Generator[ReasoningStep, None, None]:
        """Generate reasoning steps as a stream.

        Args:
            query: The reasoning query.
            context: Rich context from S1 (conversation, temporal state, etc.).

        Yields:
            ReasoningStep objects. The last step should have is_final=True.
        """
        ...


class DeterministicBackend(ReasoningBackend):
    """Lightweight deterministic backend for development and testing.

    Produces structured reasoning steps without requiring an LLM.
    Useful for validating the S1→S2→S1 pipeline during development.
    """

    def generate_stream(
        self, query: str, context: Dict[str, Any]
    ) -> Generator[ReasoningStep, None, None]:
        silence_s = context.get("silence_duration_ms", 0) / 1000
        num_cells = context.get("num_context_cells", 1)

        steps = [
            ReasoningStep(
                step_id=0,
                thought=f"Analyzing query in context of {num_cells} interaction cells...",
                confidence=0.9,
            ),
            ReasoningStep(
                step_id=1,
                thought=f"User has been silent for {silence_s:.1f}s. "
                        f"Considering temporal context for turn-taking implications.",
                confidence=0.8,
            ),
            ReasoningStep(
                step_id=2,
                thought=f"Conclusion: The query appears to be about '{query[:80]}...'. "
                        f"Further analysis would require a trained LLM backend.",
                confidence=0.7,
                is_final=True,
            ),
        ]
        yield from steps


class Reasoner:
    """Deep reasoning engine for the Background Model.

    Performs chain-of-thought reasoning on delegated queries. Results
    are streamed incrementally so S1 can provide real-time feedback.

    Uses a pluggable ReasoningBackend for actual generation.
    Defaults to DeterministicBackend for development.
    """

    def __init__(
        self,
        backend: Optional[ReasoningBackend] = None,
        max_steps: int = 10,
    ):
        self.backend = backend or DeterministicBackend()
        self.max_steps = max_steps

    def reason(
        self,
        query: str,
        context: Dict[str, Any],
        depth: ReasoningDepth = ReasoningDepth.ADAPTIVE,
    ) -> Generator[ReasoningStep, None, None]:
        """Perform reasoning on a delegated query.

        Delegates to the pluggable backend for step generation.

        Yields:
            ReasoningStep objects as reasoning progresses.
        """
        # Normalize context keys: ContextPackager nests values inside
        # 'temporal_state', but Reasoner expects them at top level
        normalized = self._normalize_context(context)

        step_count = 0
        for step in self.backend.generate_stream(query, normalized):
            yield step
            step_count += 1
            if step_count >= self.max_steps:
                break

    def _normalize_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize context key structure between ContextPackager and Reasoner.

        ContextPackager produces: {temporal_state: {silence_duration_ms: ...}, ...}
        Reasoner expects:       {silence_duration_ms: ..., ...}

        Handle both formats.
        """
        result = dict(context)
        # Flatten temporal_state if present
        temporal = context.get("temporal_state", {})
        if isinstance(temporal, dict):
            for k, v in temporal.items():
                if k not in result:
                    result[k] = v
        # Flatten interaction_state if present
        inter = context.get("interaction_state", {})
        if isinstance(inter, dict):
            for k, v in inter.items():
                if k not in result:
                    result[k] = v
        # Build conversation_summary from conversation list if present
        conversation = context.get("conversation", [])
        if conversation and "conversation_summary" not in result:
            turns = []
            for turn in conversation[-5:]:  # Last 5 turns
                speaker = turn.get("speaker", "unknown")
                content = turn.get("content", [])
                if isinstance(content, list):
                    content = " ".join(str(c) for c in content[:50])
                turns.append(f"{speaker}: {content}")
            result["conversation_summary"] = " | ".join(turns)
        return result
