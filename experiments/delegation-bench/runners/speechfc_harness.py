"""Matthew's SpeechFCEval smoke test, rerun with the harness prompt.

Same items, model and decoding as his run_speechfc.py; only the system prompt changes:

  bare          his prompt: Qwen3's tool template with the item's own functions, nothing else
  harness-item  the harness frontend instructions + the template with the item's own functions
  harness-full  the harness frontend instructions + the template with the harness's 11 tools and
                the item's functions (what the frontend would see if the item's tool were registered)

Predictions are written in the layout of Fun-Audio-Chat's SpeechFCEval scorer
(<results>/<model>/<Dataset-Name>/predict-<model>-<Dataset-Name>.json), so both his
score_speechfc.py and score_speechfc_simple.py can read them.

    CUDA_VISIBLE_DEVICES=6,7 python runners/speechfc_harness.py --gpus 0,1 \
        --data-root <SpeechFCEval dir> --harness-json <private harness export> --limit 20
"""
import argparse
import json
import time
import traceback
from pathlib import Path

from minicpmo_delegate_tool import TOOLS_PROMPT, keep_raw_output, load_model, time_first_token

HERE = Path(__file__).resolve().parent.parent  # experiments/delegation-bench
PROMPTS = ["bare", "harness-item", "harness-full"]


def system_prompt(variant, item, harness):
    item_tools = json.loads(item["function"])
    if variant == "bare":
        return TOOLS_PROMPT.format(tools="\n".join(json.dumps(t, ensure_ascii=False) for t in item_tools))
    tools = item_tools if variant == "harness-item" else harness["tools"] + item_tools
    return harness["instructions"] + "\n\n" + TOOLS_PROMPT.format(
        tools="\n".join(json.dumps(t, ensure_ascii=False) for t in tools))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openbmb/MiniCPM-o-4_5")
    ap.add_argument("--precision", choices=["bf16", "awq"], default="bf16")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--data-root", type=Path, required=True, help="SpeechFCEval directory")
    ap.add_argument("--subset", default="SpeechFC_BFCL_Single")
    ap.add_argument("--limit", type=int, default=20, help="his smoke test used the first 20 items")
    ap.add_argument("--prompts", nargs="+", choices=PROMPTS, default=PROMPTS)
    ap.add_argument("--harness-json", type=Path, required=True, help="private export from dump_harness_prompt.mjs")
    ap.add_argument("--max-new-tokens", type=int, default=512, help="his default")
    ap.add_argument("--results", type=Path, default=HERE / "results" / "speechfc")
    args = ap.parse_args()

    import librosa

    harness = json.loads(args.harness_json.read_text(encoding="utf-8"))
    meta = [json.loads(l) for l in (args.data_root / args.subset / "metadata.jsonl").read_text().splitlines() if l.strip()]
    meta = meta[: args.limit] if args.limit else meta
    dataset_name = args.subset.replace("_", "-")

    gpus = [int(g) for g in args.gpus.split(",")]
    model, tokenizer = load_model(args.model, args.precision, gpus, args.max_mem_gib)
    last = keep_raw_output(model, tokenizer, use_tts_template=False)
    time_first_token(model, last)

    for variant in args.prompts:
        model_name = f"minicpmo45-{args.precision}-{variant}"
        out_dir = args.results / model_name / dataset_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"predict-{model_name}-{dataset_name}.json"
        done = set()
        if out_file.exists():  # resume
            for line in out_file.read_text().splitlines():
                try:
                    done.add(json.loads(line)["index"])
                except (json.JSONDecodeError, KeyError):
                    pass
        with out_file.open("a", encoding="utf-8") as f:
            for item in meta:
                if item["index"] in done:
                    continue
                last.clear()
                try:
                    audio, _ = librosa.load(args.data_root / args.subset / item["file_name"], sr=16000, mono=True)
                    msgs = [{"role": "system", "content": [system_prompt(variant, item, harness)]},
                            {"role": "user", "content": [audio]}]
                    started = time.perf_counter()
                    out = model.chat(msgs=msgs, tokenizer=tokenizer, do_sample=False,
                                     max_new_tokens=args.max_new_tokens, num_beams=1, repetition_penalty=1.0,
                                     use_tts_template=False, generate_audio=False, omni_mode=False,
                                     enable_thinking=False)
                    elapsed = time.perf_counter() - started
                    text = out if isinstance(out, str) else getattr(out, "text", str(out))
                    first = last["first_token_at"] - started if "first_token_at" in last else None
                    record = {**item, "predict": text, "raw": last.get("raw"), "latency_s": round(elapsed, 3),
                              "first_token_s": None if first is None else round(first, 3)}
                except Exception as e:  # recorded and scored as wrong, then the run goes on
                    traceback.print_exc()
                    import torch
                    torch.cuda.empty_cache()
                    text, record = "", {**item, "predict": "", "error": f"{type(e).__name__}: {e}"}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                print(f"[{variant} {item['index']}] {record.get('latency_s', 0):.2f}s | {text[:110]!r}")
        print(f"wrote {out_file}")


if __name__ == "__main__":
    main()
