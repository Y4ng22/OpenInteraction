"""Tests for safe DuplexOmni checkpoint compatibility inspection."""

import json

from interactformer.compat.duplex import inspect_duplex_config, load_json_config


def _released_shape_config():
    return {
        "architectures": ["Qwen3OmniMoeForConditionalGeneration"],
        "model_type": "qwen3_omni_moe",
        "vocab_size": 152064,
        "thinker_config": {
            "text_config": {
                "vocab_size": 152064,
                "hidden_size": 2048,
                "num_hidden_layers": 48,
                "num_attention_heads": 32,
                "num_key_value_heads": 4,
                "num_experts": 128,
                "num_experts_per_tok": 8,
            },
            "audio_config": {"num_hidden_layers": 32, "num_mel_bins": 128},
            "vision_config": {"patch_size": 16},
        },
        "talker_config": {
            "num_code_groups": 16,
            "text_config": {
                "hidden_size": 1024,
                "num_hidden_layers": 20,
                "num_experts": 128,
            },
        },
        "code2wav_config": {"num_quantizers": 16, "codebook_size": 2048},
    }


def test_released_duplex_shapes_require_external_backbone():
    report = inspect_duplex_config(_released_shape_config())
    by_component = {item.component: item for item in report.items}

    assert report.direct_checkpoint_load is False
    assert report.recommended_mode == "external_backbone_then_distill"
    assert by_component["tokenizer/vocabulary"].status == "reusable"
    assert by_component["token embedding / LM head"].status == "shape-compatible-only"
    assert by_component["Thinker transformer"].status == "shape-incompatible"
    assert by_component["full released checkpoint"].status == "reusable-as-a-whole"


def test_local_config_loading_does_not_execute_model_code(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_released_shape_config()), encoding="utf-8")
    report = inspect_duplex_config(load_json_config(path))
    assert "Direct `load_state_dict`: `False`" in report.to_markdown()
