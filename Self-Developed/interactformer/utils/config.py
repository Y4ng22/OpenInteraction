"""
Configuration management for InteractFormer.

Provides structured configuration for model architecture, training,
and inference with support for YAML/JSON serialization.
"""

from dataclasses import dataclass, field
from typing import Optional, Literal
import json
import yaml
from pathlib import Path


@dataclass
class InteractionModelConfig:
    """Configuration for the Interaction Model (S1).

    This model handles real-time, low-latency streaming interaction
    across audio, video, and text modalities.

    Attributes:
        hidden_size: Dimension of hidden states.
        num_layers: Number of transformer layers.
        num_attention_heads: Number of attention heads.
        num_kv_heads: Number of key-value heads (for GQA).
        intermediate_size: FFN intermediate dimension.
        micro_turn_ms: Duration of each micro-turn in milliseconds.
            Following TML's design, default is 200ms.
        audio_sample_rate: Input audio sample rate in Hz.
        audio_encoder_type: Type of audio encoder ("dmel" for TML-style
            encoder-free early fusion, or "whisper" for traditional).
        vision_patch_size: Size of image patches for vision encoding.
        num_experts: Number of experts in MoE layers.
        num_experts_per_tok: Number of active experts per token.
    """
    hidden_size: int = 2048
    num_layers: int = 24
    num_attention_heads: int = 16
    num_kv_heads: int = 4
    intermediate_size: int = 5632
    micro_turn_ms: int = 200
    audio_sample_rate: int = 24000
    audio_encoder_type: Literal["dmel", "whisper"] = "dmel"
    vision_patch_size: int = 40
    num_experts: int = 8
    num_experts_per_tok: int = 2


@dataclass
class BackgroundModelConfig:
    """Configuration for the Background Model (S2).

    The background model handles asynchronous deep reasoning, knowledge
    retrieval, and tool use. Unlike DuplexOmni which uses a single
    pluggable S2, InteractFormer supports a Multi-Background Ensemble.

    Attributes:
        model_type: Type of background model ("transformer", "llm").
        model_name_or_path: HuggingFace model identifier or local path.
        max_context_length: Maximum context window size.
        ensemble_mode: How multiple background models collaborate
            ("parallel", "cascade", "voting").
        reasoning_depth: Depth of reasoning chain ("shallow", "deep", "adaptive").
        tool_use_enabled: Whether tool calling is enabled.
        retrieval_enabled: Whether knowledge retrieval is enabled.
        retrieval_top_k: Number of retrieval results.
        stream_chunk_size: Size of streaming chunks for progressive injection.
    """
    model_type: Literal["transformer", "llm"] = "llm"
    model_name_or_path: str = "doubao-seed-evolving"
    max_context_length: int = 32768
    ensemble_mode: Literal["parallel", "cascade", "voting"] = "parallel"
    reasoning_depth: Literal["shallow", "deep", "adaptive"] = "adaptive"
    tool_use_enabled: bool = True
    retrieval_enabled: bool = True
    retrieval_top_k: int = 5
    stream_chunk_size: int = 200  # ms, matches micro-turn


@dataclass
class BridgeConfig:
    """Configuration for the Streaming Context Bridge.

    The bridge is InteractFormer's key innovation: instead of marker-based
    injection (DuplexOmni's 「...」) or batch context packages (TML),
    it uses progressive chunk-level fusion at the granularity of micro-turns.

    Attributes:
        fusion_mode: How S2 results are fused into S1
            ("cross_attention", "gate", "concat").
        injection_strategy: When to inject S2 results
            ("eager" = immediately, "scheduled" = at turn boundaries,
             "adaptive" = based on semantic completeness).
        max_pending_chunks: Maximum queued S2 chunks before forcing injection.
        context_compression_ratio: Compression ratio for long contexts.
        time_alignment: Whether to align injection with temporal grid.
    """
    fusion_mode: Literal["cross_attention", "gate", "concat"] = "cross_attention"
    injection_strategy: Literal["eager", "scheduled", "adaptive"] = "adaptive"
    max_pending_chunks: int = 8
    context_compression_ratio: float = 0.5
    time_alignment: bool = True


@dataclass
class ModelConfig:
    """Top-level model configuration."""
    interaction: InteractionModelConfig = field(default_factory=InteractionModelConfig)
    background: BackgroundModelConfig = field(default_factory=BackgroundModelConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)


@dataclass
class InferenceConfig:
    """Configuration for inference / serving.

    Attributes:
        device: Device to run on ("cuda", "cpu", "mps").
        dtype: Data type for inference.
        streaming_session_enabled: Use persistent GPU memory sessions.
        max_micro_turns: Maximum micro-turns before session reset.
        temperature: Sampling temperature.
        top_p: Nucleus sampling threshold.
        repetition_penalty: Penalty for repeated tokens.
    """
    device: str = "cuda"
    dtype: str = "bfloat16"
    streaming_session_enabled: bool = True
    max_micro_turns: int = 15000  # ~50 minutes at 200ms/turn
    temperature: float = 0.7
    top_p: float = 0.9
    repetition_penalty: float = 1.1


class Config:
    """Main configuration class with serialization support."""

    def __init__(
        self,
        model: Optional[ModelConfig] = None,
        inference: Optional[InferenceConfig] = None,
    ):
        self.model = model or ModelConfig()
        self.inference = inference or InferenceConfig()

    def save(self, path: str | Path) -> None:
        """Save configuration to YAML file."""
        path = Path(path)
        data = {
            "model": {
                "interaction": self._dataclass_to_dict(self.model.interaction),
                "background": self._dataclass_to_dict(self.model.background),
                "bridge": self._dataclass_to_dict(self.model.bridge),
            },
            "inference": self._dataclass_to_dict(self.inference),
        }
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Load configuration from YAML file."""
        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f)

        interaction = InteractionModelConfig(**data["model"]["interaction"])
        background = BackgroundModelConfig(**data["model"]["background"])
        bridge = BridgeConfig(**data["model"]["bridge"])
        inference = InferenceConfig(**data["inference"])

        return cls(
            model=ModelConfig(
                interaction=interaction,
                background=background,
                bridge=bridge,
            ),
            inference=inference,
        )

    @staticmethod
    def _dataclass_to_dict(obj) -> dict:
        """Convert a dataclass instance to a dictionary."""
        result = {}
        for field_name in obj.__dataclass_fields__:
            value = getattr(obj, field_name)
            result[field_name] = value
        return result
