"""
Streaming Context Bridge — Progressive S1 ↔ S2 communication.

THIS IS INTERACTFORMER'S KEY ARCHITECTURAL INNOVATION.

Traditional approaches:
- DuplexOmni: S2 returns 「...」 markers injected into S1's text stream
- TML Interaction Models: Background model sends "rich context packages"
  but the interleaving mechanism is not specified
- Qwen3-Omni: No separate S2; single-model think-speak

InteractFormer's Streaming Context Bridge:
- Progressive chunk-level fusion at 200ms granularity
- Cross-attention based injection (not text markers)
- Bidirectional: S1 → S2 (delegation) and S2 → S1 (results)
- Time-aligned: injections are synchronized with the Temporal Grid
- Multi-stream: can handle multiple concurrent S2 results

Components:
- ContextPackager: packages S1 state for S2 delegation
- StreamInjector: injects S2 results into S1 at the right time
- CrossAttentionFusion: the neural mechanism for S2→S1 information flow
"""

from interactformer.bridge.context_packager import ContextPackager
from interactformer.bridge.stream_injector import (
    StreamInjector, InjectionScheduler, BridgeMessage,
)
from interactformer.bridge.cross_attention import CrossAttentionFusion

__all__ = [
    "ContextPackager",
    "StreamInjector",
    "InjectionScheduler",
    "BridgeMessage",
    "CrossAttentionFusion",
]
