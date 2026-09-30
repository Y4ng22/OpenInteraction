"""
Orchestrator — Coordinates Interaction Model and Background Model.

The Orchestrator is the main control loop of InteractFormer. It manages:
1. Streaming session lifecycle (start, maintain, end)
2. Micro-turn scheduling (200ms rhythm)
3. S1 ↔ S2 communication via the Streaming Context Bridge
4. Implicit turn management (no [CUT] markers)
5. Resource allocation and latency management

This is the top-level component that ties everything together.
"""

from interactformer.orchestrator.session import (
    StreamingSession, SessionState, SessionConfig,
)
from interactformer.orchestrator.scheduler import (
    MicroTurnScheduler, SchedulerConfig,
)
from interactformer.orchestrator.orchestrator import Orchestrator

__all__ = [
    "StreamingSession",
    "SessionState",
    "SessionConfig",
    "MicroTurnScheduler",
    "SchedulerConfig",
    "Orchestrator",
]
