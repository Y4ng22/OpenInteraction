"""
Interaction Model (S1) — Real-time multimodal streaming interaction.

The Interaction Model is the low-latency frontend of InteractFormer.
It continuously processes streaming audio, video, and text input in
200ms micro-turns and generates streaming speech and text output.

Architecture:
    Input Stream → Temporal Grid → Multimodal Encoder → Thinker (MoE)
                                                          ↓
    Output Stream ← Audio Decoder ← Talker ←─────────────┘

Key differences from DuplexOmni S1:
- Explicit Temporal Grid instead of continuous stream
- Encoder-free early fusion (dMel + hMLP) instead of Qwen encoders
- Implicit turn management without [CUT] markers
"""

from interactformer.interaction.encoder import MultimodalEncoder
from interactformer.interaction.temporal_grid import TemporalGrid, GridCell
from interactformer.interaction.thinker import InteractionThinker
from interactformer.interaction.talker import StreamingTalker
from interactformer.interaction.interaction_model import InteractionModel

__all__ = [
    "MultimodalEncoder",
    "TemporalGrid",
    "GridCell",
    "InteractionThinker",
    "StreamingTalker",
    "InteractionModel",
]
