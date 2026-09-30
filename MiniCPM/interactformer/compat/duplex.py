"""DuplexOmni checkpoint compatibility analysis.

The released DuplexOmni checkpoint is a Qwen3-Omni-MoE model.  InteractFormer
uses a smaller custom research architecture, so matching one or two dimensions
is not enough to make transformer or codec weights loadable.  This module turns
that architectural comparison into a deterministic report before a 70GB
checkpoint is downloaded or loaded.
"""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class TargetArchitecture:
    """Shape-defining fields of the current InteractFormer S1 prototype."""

    hidden_size: int = 2048
    vocab_size: int = 152064
    num_hidden_layers: int = 24
    num_attention_heads: int = 16
    num_key_value_heads: int = 4
    num_experts: int = 8
    num_experts_per_tok: int = 2
    vision_patch_size: int = 40
    audio_frontend: str = "dmel"
    num_codebooks: int = 32
    codebook_size: int = 4096


@dataclass(frozen=True)
class CompatibilityItem:
    component: str
    status: str
    source: str
    target: str
    reason: str


@dataclass
class DuplexCompatibilityReport:
    source_model_type: str
    source_architecture: str
    recommended_mode: str
    direct_checkpoint_load: bool
    items: list[CompatibilityItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_model_type": self.source_model_type,
            "source_architecture": self.source_architecture,
            "recommended_mode": self.recommended_mode,
            "direct_checkpoint_load": self.direct_checkpoint_load,
            "items": [asdict(item) for item in self.items],
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        rows = [
            "| Component | Status | DuplexOmni | InteractFormer | Reason |",
            "|---|---|---|---|---|",
        ]
        for item in self.items:
            values = (
                item.component,
                item.status,
                item.source,
                item.target,
                item.reason,
            )
            rows.append("| " + " | ".join(_escape(value) for value in values) + " |")
        rows.extend([
            "",
            f"Recommended integration: `{self.recommended_mode}`",
            f"Direct `load_state_dict`: `{self.direct_checkpoint_load}`",
        ])
        if self.notes:
            rows.append("")
            rows.extend(f"- {note}" for note in self.notes)
        return "\n".join(rows)


def load_json_config(path: str | Path) -> Dict[str, Any]:
    """Load a local Hugging Face ``config.json`` without executing model code."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("model config must contain a JSON object")
    return data


def inspect_duplex_config(
    config: Dict[str, Any],
    target: Optional[TargetArchitecture] = None,
) -> DuplexCompatibilityReport:
    """Compare a DuplexOmni/Qwen3-Omni config with the local S1 prototype."""
    target = target or TargetArchitecture()
    thinker = _mapping(config.get("thinker_config"))
    thinker_text = _mapping(thinker.get("text_config"))
    thinker_audio = _mapping(thinker.get("audio_config"))
    thinker_vision = _mapping(thinker.get("vision_config"))
    talker = _mapping(config.get("talker_config"))
    talker_text = _mapping(talker.get("text_config"))
    code2wav = _mapping(config.get("code2wav_config"))

    source_vocab = _int(thinker_text.get("vocab_size"), config.get("vocab_size"))
    source_hidden = _int(thinker_text.get("hidden_size"), thinker.get("hidden_size"))

    items = [
        _same_shape_item(
            "tokenizer/vocabulary",
            source_vocab,
            target.vocab_size,
            "Tokenizer assets are reusable when special-token IDs are preserved.",
            "Different vocabulary rows make embeddings and LM heads incompatible.",
        ),
        _conditional_embedding_item(source_vocab, source_hidden, target),
        _architecture_item(
            "Thinker transformer",
            thinker_text,
            target,
        ),
        CompatibilityItem(
            component="audio frontend",
            status="incompatible",
            source=(
                f"Qwen audio encoder: {thinker_audio.get('num_hidden_layers', '?')} layers, "
                f"{thinker_audio.get('num_mel_bins', '?')} mel bins"
            ),
            target="encoder-free dMel lightweight embedding",
            reason="TML-style early fusion and DuplexOmni's trained audio encoder use different modules and representations.",
        ),
        CompatibilityItem(
            component="vision frontend",
            status="incompatible",
            source=f"ViT patch={thinker_vision.get('patch_size', '?')}",
            target=f"hMLP patch={target.vision_patch_size}",
            reason="Patch geometry, depth, positional encoding, and projection weights differ.",
        ),
        CompatibilityItem(
            component="Talker",
            status="external-or-distill",
            source=(
                f"hidden={talker_text.get('hidden_size', '?')}, "
                f"layers={talker_text.get('num_hidden_layers', '?')}, "
                f"experts={talker_text.get('num_experts', '?')}, "
                f"code groups={talker.get('num_code_groups', '?')}"
            ),
            target=(
                f"hidden={target.hidden_size}, lightweight MTP, "
                f"codebooks={target.num_codebooks}"
            ),
            reason="The local Talker is not the Qwen3-Omni Talker implementation; use the released Talker intact or distill it.",
        ),
        CompatibilityItem(
            component="codec / code2wav",
            status="incompatible",
            source=(
                f"quantizers={code2wav.get('num_quantizers', '?')}, "
                f"codebook={code2wav.get('codebook_size', '?')}"
            ),
            target=(
                f"codebooks={target.num_codebooks}, "
                f"codebook={target.codebook_size}"
            ),
            reason="Codec token IDs only have meaning with the matching trained decoder and quantizer layout.",
        ),
        CompatibilityItem(
            component="full released checkpoint",
            status="reusable-as-a-whole",
            source="Qwen3OmniMoeForConditionalGeneration",
            target="external S1 backend / teacher",
            reason="The checkpoint can be run through its own model code or modified vLLM stack and wrapped by the 200ms session protocol.",
        ),
    ]

    architecture = config.get("architectures") or []
    architecture_name = architecture[0] if architecture else "unknown"
    return DuplexCompatibilityReport(
        source_model_type=str(config.get("model_type", "unknown")),
        source_architecture=str(architecture_name),
        recommended_mode="external_backbone_then_distill",
        direct_checkpoint_load=False,
        items=items,
        notes=[
            "Do not use ignore_mismatched_sizes or strict=False as a conversion strategy; it leaves most of S1 randomly initialized.",
            "Start with the complete DuplexOmni Thinker+Talker runtime, then train 200ms interaction behavior and distill into the TML-style student.",
            "A tokenizer match is necessary but not sufficient for representation-level weight compatibility.",
        ],
    )


def _architecture_item(
    component: str,
    source: Dict[str, Any],
    target: TargetArchitecture,
) -> CompatibilityItem:
    source_values = {
        "hidden": source.get("hidden_size"),
        "layers": source.get("num_hidden_layers"),
        "heads": source.get("num_attention_heads"),
        "kv": source.get("num_key_value_heads"),
        "experts": source.get("num_experts"),
        "topk": source.get("num_experts_per_tok"),
    }
    target_values = {
        "hidden": target.hidden_size,
        "layers": target.num_hidden_layers,
        "heads": target.num_attention_heads,
        "kv": target.num_key_value_heads,
        "experts": target.num_experts,
        "topk": target.num_experts_per_tok,
    }
    shape_match = all(source_values[key] == value for key, value in target_values.items())
    return CompatibilityItem(
        component=component,
        status="implementation-incompatible" if shape_match else "shape-incompatible",
        source=_compact(source_values.items()),
        target=_compact(target_values.items()),
        reason=(
            "Even matching dimensions would not be sufficient because normalization, QK norm, RoPE, MoE routing, and parameter names differ."
            if shape_match
            else "Layer count, attention layout, and MoE expert topology do not match."
        ),
    )


def _conditional_embedding_item(
    source_vocab: Optional[int],
    source_hidden: Optional[int],
    target: TargetArchitecture,
) -> CompatibilityItem:
    compatible = (
        source_vocab == target.vocab_size and source_hidden == target.hidden_size
    )
    return CompatibilityItem(
        component="token embedding / LM head",
        status="shape-compatible-only" if compatible else "shape-incompatible",
        source=f"[{source_vocab}, {source_hidden}]",
        target=f"[{target.vocab_size}, {target.hidden_size}]",
        reason=(
            "Rows have compatible shapes, but selective copying is experimental because embeddings are co-adapted with the Duplex transformer."
            if compatible
            else "Vocabulary or hidden dimension differs."
        ),
    )


def _same_shape_item(
    component: str,
    source: Optional[int],
    target: int,
    same_reason: str,
    different_reason: str,
) -> CompatibilityItem:
    same = source == target
    return CompatibilityItem(
        component=component,
        status="reusable" if same else "incompatible",
        source=str(source),
        target=str(target),
        reason=same_reason if same else different_reason,
    )


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(*values: Any) -> Optional[int]:
    for value in values:
        if isinstance(value, int):
            return value
    return None


def _compact(items: Iterable[tuple[str, Any]]) -> str:
    return ", ".join(f"{key}={value}" for key, value in items)


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")

