"""
Context Packager: Prepares rich context packages for S2 delegation.

When the Interaction Model decides to delegate a task to the Background
Model, it doesn't just send a text query — it sends a "rich context
package" (TML's term) containing:

1. The full recent conversation (not just the current turn)
2. Temporal state (silence duration, speaking patterns)
3. Multimodal context (what the user is seeing/hearing)
4. The specific delegation query

This is the S1→S2 direction of the Streaming Context Bridge.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import time

from interactformer.interaction.temporal_grid import GridCell


@dataclass
class ContextPackage:
    """A rich context package for Background Model delegation.

    This is what TML calls a "rich context package — not a standalone
    query, but the full conversation." It contains everything S2 needs
    to understand the current interaction state.

    Attributes:
        package_id: Unique package identifier.
        created_at_ms: Timestamp when this package was created.
        query: The specific query or task for S2.
        conversation: Recent conversation in chronological order.
        temporal_state: Current temporal state of the interaction.
        multimodal_snapshot: Key visual/audio information.
        interaction_state: S1's internal state summary.
        priority: Delegation priority.
    """
    package_id: str
    created_at_ms: float
    query: str
    conversation: List[Dict[str, Any]] = field(default_factory=list)
    temporal_state: Dict[str, Any] = field(default_factory=dict)
    multimodal_snapshot: Dict[str, Any] = field(default_factory=dict)
    interaction_state: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0


class ContextPackager:
    """Builds rich context packages for S1→S2 delegation.

    The packager extracts relevant context from the Temporal Grid
    and Interaction Model state, structuring it for efficient
    consumption by the Background Model.

    Design principle: Give S2 everything it needs to reason effectively,
    not just a text query. The interaction context matters.
    """

    def __init__(
        self,
        max_conversation_turns: int = 50,
        max_context_tokens: int = 4096,
        include_audio_features: bool = True,
        include_vision_features: bool = True,
        micro_turn_ms: int = 200,
    ):
        self.max_conversation_turns = max_conversation_turns
        self.max_context_tokens = max_context_tokens
        self.include_audio_features = include_audio_features
        self.include_vision_features = include_vision_features
        self.micro_turn_ms = micro_turn_ms
        self._package_counter: int = 0

    def build_package(
        self,
        query: str,
        recent_cells: List[GridCell],
        silence_duration_ms: float = 0.0,
        priority: int = 0,
    ) -> ContextPackage:
        """Build a context package from the current interaction state.

        Args:
            query: The delegation query.
            recent_cells: Recent temporal grid cells from S1.
            silence_duration_ms: How long the user has been silent.
            priority: Task priority.

        Returns:
            ContextPackage ready for S2.
        """
        package_id = f"ctx_{self._next_id()}_{int(time.time()*1000)}"

        # Extract conversation from grid cells
        conversation = self._extract_conversation(recent_cells)

        # Extract temporal state
        temporal_state = self._extract_temporal_state(
            recent_cells, silence_duration_ms
        )

        # Extract multimodal context
        multimodal_snapshot = self._extract_multimodal_context(recent_cells)

        # Extract interaction state
        interaction_state = self._extract_interaction_state(recent_cells)

        return ContextPackage(
            package_id=package_id,
            created_at_ms=time.time() * 1000,
            query=query,
            conversation=conversation,
            temporal_state=temporal_state,
            multimodal_snapshot=multimodal_snapshot,
            interaction_state=interaction_state,
            priority=priority,
        )

    def _extract_conversation(
        self, cells: List[GridCell]
    ) -> List[Dict[str, Any]]:
        """Extract conversation turns from temporal grid cells.

        Converts the grid-based representation to a more traditional
        turn-based format that's easier for S2 to reason about.
        """
        conversation = []
        current_turn = {"speaker": None, "content": [], "start_ms": 0}

        for cell in cells[-self.max_conversation_turns:]:
            speaker = "user" if cell.is_user_speaking else "model"

            if speaker != current_turn["speaker"]:
                # Start a new turn
                if current_turn["speaker"] is not None:
                    conversation.append(current_turn)
                current_turn = {
                    "speaker": speaker,
                    "content": [],
                    "start_ms": cell.timestamp_ms,
                }

            # Add cell content to current turn
            if cell.output_text:
                current_turn["content"].extend(cell.output_text)

        # Add final turn
        if current_turn["speaker"] is not None:
            conversation.append(current_turn)

        return conversation

    def _extract_temporal_state(
        self,
        cells: List[GridCell],
        silence_duration_ms: float,
    ) -> Dict[str, Any]:
        """Extract temporal state information.

        This is critical for time-aware reasoning. S2 needs to know
        how long the user has been silent, the conversation rhythm, etc.
        """
        if not cells:
            return {"silence_duration_ms": 0.0}

        # Calculate speaking rhythm
        user_speaking_cells = sum(
            1 for c in cells if c.is_user_speaking
        )
        model_speaking_cells = sum(
            1 for c in cells if c.is_model_speaking
        )

        return {
            "silence_duration_ms": silence_duration_ms,
            "silence_duration_s": silence_duration_ms / 1000,
            "num_recent_cells": len(cells),
            "duration_ms": len(cells) * self.micro_turn_ms,
            "user_speaking_ratio": user_speaking_cells / max(len(cells), 1),
            "model_speaking_ratio": model_speaking_cells / max(len(cells), 1),
            "first_cell_timestamp_ms": cells[0].timestamp_ms if cells else 0,
            "last_cell_timestamp_ms": cells[-1].timestamp_ms if cells else 0,
        }

    def _extract_multimodal_context(
        self, cells: List[GridCell]
    ) -> Dict[str, Any]:
        """Extract multimodal context snapshot."""
        context = {
            "has_audio": False,
            "has_vision": False,
            "has_text": False,
        }

        for cell in cells[-10:]:  # Last 2 seconds
            if cell.audio_embedding is not None:
                context["has_audio"] = True
            if cell.vision_embedding is not None:
                context["has_vision"] = True
            if cell.text_embedding is not None:
                context["has_text"] = True

        # Add most recent visual snapshot if available
        for cell in reversed(cells):
            if cell.vision_embedding is not None and self.include_vision_features:
                context["latest_vision_cell_id"] = cell.cell_id
                context["latest_vision_timestamp_ms"] = cell.timestamp_ms
                break

        return context

    def _extract_interaction_state(
        self, cells: List[GridCell]
    ) -> Dict[str, Any]:
        """Extract S1 internal state summary."""
        if not cells:
            return {}

        latest = cells[-1]
        return {
            "current_cell_id": latest.cell_id,
            "current_cell_state": latest.cell_state,
            "is_model_speaking": latest.is_model_speaking,
            "is_user_speaking": latest.is_user_speaking,
            "has_pending_injections": latest.has_background_updates,
            "num_pending_injections": len(latest.background_injections),
        }

    def to_dict(self, package: ContextPackage) -> Dict[str, Any]:
        """Serialize a ContextPackage to a dictionary for transport.

        This is used when S1 and S2 are in different processes or
        machines. The dictionary format is designed to be easily
        serializable (JSON-compatible).
        """
        return {
            "package_id": package.package_id,
            "created_at_ms": package.created_at_ms,
            "query": package.query,
            "conversation": package.conversation,
            "temporal_state": package.temporal_state,
            "multimodal_snapshot": package.multimodal_snapshot,
            "interaction_state": package.interaction_state,
            "priority": package.priority,
        }

    def _next_id(self) -> int:
        """Generate next package ID."""
        self._package_counter += 1
        return self._package_counter
