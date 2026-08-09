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


class Reasoner:
    """Deep reasoning engine for the Background Model.

    Performs chain-of-thought reasoning on delegated queries. Results
    are streamed incrementally so S1 can provide real-time feedback
    (unlike DuplexOmni which returns a single 「...」 block).

    The Reasoner supports multiple reasoning strategies:
    - Chain-of-thought: sequential step-by-step reasoning
    - Tree-of-thought: branching exploration of alternatives
    - Reflexion: iterative refinement with self-critique
    """

    def __init__(
        self,
        model_name_or_path: str = "Qwen/Qwen3-Omni-30B-A3B-Instruct",
        max_steps: int = 10,
        temperature: float = 0.7,
        strategy: str = "chain_of_thought",
    ):
        self.model_name_or_path = model_name_or_path
        self.max_steps = max_steps
        self.temperature = temperature
        self.strategy = strategy

    def reason(
        self,
        query: str,
        context: Dict[str, Any],
        depth: ReasoningDepth = ReasoningDepth.ADAPTIVE,
    ) -> Generator[ReasoningStep, None, None]:
        """Perform reasoning on a delegated query.

        Args:
            query: The question or task to reason about.
            context: Rich context package from S1 including conversation
                history, temporal state, and multimodal context.
            depth: Desired reasoning depth.

        Yields:
            ReasoningStep objects as reasoning progresses.
            S1 can consume these incrementally via the Bridge.
        """
        # Determine effective depth
        if depth == ReasoningDepth.ADAPTIVE:
            effective_max = self._determine_depth(query, context)
        elif depth == ReasoningDepth.DEEP:
            effective_max = self.max_steps
        else:
            effective_max = min(3, self.max_steps)

        # Build reasoning prompt from context
        prompt = self._build_prompt(query, context)

        # Multi-step reasoning loop
        current_thought = query
        references = []

        for step_id in range(effective_max):
            # Generate next reasoning step
            # (In production, this calls the LLM)
            step = self._generate_step(
                prompt, current_thought, step_id, references
            )

            # Check if we've reached a conclusion
            if step.is_final:
                yield step
                break

            yield step
            current_thought = step.thought

    def _determine_depth(
        self, query: str, context: Dict[str, Any]
    ) -> int:
        """Determine appropriate reasoning depth based on query complexity.

        Simple questions ("What's the weather?") → shallow
        Complex questions ("Why did the model fail?") → deep
        """
        # Heuristic: longer queries + tool calls → deeper reasoning
        complexity_score = min(len(query) / 100, 1.0)
        if context.get("requires_tool_use"):
            complexity_score += 0.3
        if context.get("is_multiturn"):
            complexity_score += 0.2

        depth = int(complexity_score * self.max_steps)
        return max(2, min(depth, self.max_steps))

    def _build_prompt(
        self, query: str, context: Dict[str, Any]
    ) -> str:
        """Build the reasoning prompt from the S1 context package.

        This is where the "rich context package" (TML's term) is
        transformed into a reasoning prompt. We include conversation
        history, temporal information, and multimodal context.
        """
        parts = []

        # System instruction
        parts.append(
            "You are the Background Reasoning Model of InteractFormer. "
            "Your role is to perform deep reasoning on queries delegated "
            "by the real-time Interaction Model. Think step by step and "
            "provide your reasoning as a chain of thoughts."
        )

        # Conversation context from S1
        if context.get("conversation_summary"):
            parts.append(f"\n## Conversation Context\n{context['conversation_summary']}")

        # Temporal context
        silence_ms = context.get("silence_duration_ms", 0)
        parts.append(f"\n## Temporal State\nUser has been silent for {silence_ms/1000:.1f}s")

        # The actual query
        parts.append(f"\n## Query\n{query}")

        # Instruction for streaming output
        parts.append(
            "\nProvide your reasoning as numbered steps. Mark your final "
            "conclusion with [FINAL]."
        )

        return "\n".join(parts)

    def _generate_step(
        self,
        prompt: str,
        previous_thought: str,
        step_id: int,
        references: list[str],
    ) -> ReasoningStep:
        """Generate a single reasoning step.

        This is a placeholder that would call the actual LLM in production.
        The real implementation uses the same model architecture as the
        Interaction Thinker but with deeper reasoning prompts.
        """
        # Placeholder: in production, this calls the LLM
        # For now, return a structured placeholder step
        return ReasoningStep(
            step_id=step_id,
            thought=(
                f"[S2 Reasoning Step {step_id}]: Analyzing '{previous_thought[:50]}...' "
                f"in context of the conversation."
            ),
            confidence=0.8 - step_id * 0.1,  # Decreasing confidence per step
            is_final=(step_id >= 3),
            references=references,
        )
