"""
InteractFormer: A Real-Time Multimodal Interaction Framework
=============================================================

InteractFormer implements a dual-model architecture for real-time,
full-duplex human-AI interaction across audio, video, and text modalities.

Architecture Overview
---------------------
The framework consists of two asynchronous models connected by a
Streaming Context Bridge:

1. **Interaction Model** (S1) — Low-latency, real-time streaming model
   that continuously processes multimodal input (audio, video, text) in
   200ms micro-turns and generates streaming speech/text output.

2. **Background Model** (S2) — Asynchronous deep reasoning model that
   handles complex reasoning, knowledge retrieval, and tool use.
   Results stream back to the Interaction Model via the Context Bridge.

3. **Streaming Context Bridge** — A novel progressive chunk-level fusion
   mechanism that replaces traditional marker-based injection (e.g.,
   DuplexOmni's 「...」 markers) with continuous, time-aligned context
   streaming between S1 and S2.

Key Innovation
--------------
Unlike prior work (TML Interaction Models, DuplexOmni, Qwen3-Omni),
InteractFormer introduces:
- **Explicit Temporal Grid**: 200ms time-aligned micro-turn management
- **Streaming Context Bridge**: Progressive S2→S1 fusion without markers
- **Multi-Background Ensemble**: Parallel heterogeneous background models
- **Implicit Turn Management**: Learned interruption without explicit tokens

References
----------
- TML Interaction Models (Thinking Machines Lab, 2026)
- DuplexOmni (Huang et al., 2025)
- Qwen3-Omni (Alibaba, 2025)
- Moshi (Kyutai, 2024)

License
-------
Apache 2.0
"""

__version__ = "0.1.0"
__author__ = "InteractFormer Team"

# Lazy imports: heavy modules (torch-dependent) are imported on demand
# to allow structural inspection without full dependencies.

def __getattr__(name):
    _imports = {
        "InteractionModel": "interactformer.interaction.interaction_model",
        "BackgroundModel": "interactformer.background.background_model",
        "Orchestrator": "interactformer.orchestrator.orchestrator",
        "StreamingContextBridge": "interactformer.bridge.cross_attention",
        "MiniCPMORealtimeClient": "interactformer.backends.minicpmo_realtime",
    }
    if name in _imports:
        import importlib
        module = importlib.import_module(_imports[name])
        return getattr(module, name)
    raise AttributeError(f"module 'interactformer' has no attribute '{name}'")

__all__ = [
    "InteractionModel",
    "BackgroundModel",
    "Orchestrator",
    "StreamingContextBridge",
    "MiniCPMORealtimeClient",
]
