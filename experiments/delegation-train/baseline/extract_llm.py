"""Save MiniCPM-o 4.5's language model as a plain Qwen3 checkpoint, so vLLM can serve its text chat mode.

MiniCPM-o's full-duplex mode has no tool-call path, so the untrained baseline uses the simplex chat mode with text
input. For text-only input that mode runs nothing but the `llm.*` weights (Qwen3-8B shape) with the model's own chat
template, which is exactly what this checkpoint holds. The vision, audio and TTS weights are left out.

    python extract_llm.py --src <MiniCPM-o-4_5 snapshot> --out /local/data/xy2764/models/minicpmo45-llm
"""
import argparse
import json
import shutil
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file

QWEN3_KEYS = ["attention_bias", "attention_dropout", "bos_token_id", "eos_token_id", "head_dim", "hidden_act",
              "hidden_size", "initializer_range", "intermediate_size", "max_position_embeddings", "max_window_layers",
              "num_attention_heads", "num_hidden_layers", "num_key_value_heads", "rms_norm_eps", "rope_scaling",
              "rope_theta", "sliding_window", "tie_word_embeddings", "use_cache", "use_sliding_window", "vocab_size"]
TOKENIZER_FILES = ["tokenizer.json", "vocab.json", "merges.txt", "special_tokens_map.json", "added_tokens.json",
                   "generation_config.json"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cfg = json.loads((args.src / "config.json").read_text())
    qcfg = {k: cfg[k] for k in QWEN3_KEYS}
    qcfg.update(architectures=["Qwen3ForCausalLM"], model_type="qwen3", torch_dtype="bfloat16")
    (args.out / "config.json").write_text(json.dumps(qcfg, indent=2))

    weight_map = json.loads((args.src / "model.safetensors.index.json").read_text())["weight_map"]
    shards = sorted({f for k, f in weight_map.items() if k.startswith("llm.")})
    index = {}
    for i, shard in enumerate(shards):
        tensors = {}
        with safe_open(args.src / shard, framework="pt") as f:
            for k in f.keys():
                if k.startswith("llm."):
                    tensors[k[len("llm."):]] = f.get_tensor(k)
        name = f"model-{i + 1:05d}-of-{len(shards):05d}.safetensors"
        save_file(tensors, args.out / name, metadata={"format": "pt"})
        index.update({k: name for k in tensors})
        print(f"{shard}: {len(tensors)} tensors -> {name}")
    (args.out / "model.safetensors.index.json").write_text(json.dumps({"metadata": {}, "weight_map": index}, indent=1))

    for f in TOKENIZER_FILES:
        if (args.src / f).exists():
            shutil.copy(args.src / f, args.out / f)
    tok = json.loads((args.src / "tokenizer_config.json").read_text())
    tok.pop("auto_map", None)
    tok["tokenizer_class"] = "Qwen2TokenizerFast"
    (args.out / "tokenizer_config.json").write_text(json.dumps(tok, indent=1, ensure_ascii=False))
    print(f"{len(index)} tensors, lm_head present: {'lm_head.weight' in index}")


if __name__ == "__main__":
    main()
