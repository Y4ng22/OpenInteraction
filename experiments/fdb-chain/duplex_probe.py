"""Does MiniCPM-o 4.5 in full-duplex mode emit <tool_call>, and can it be caught before TTS?

Feeds FDB-v3 audio to MiniCPMODuplex in 1 s chunks (then a few seconds of silence) with a tool-bearing system
prompt, and records each unit's generated tokens. Text-only (generate_audio=False): what matters is which tokens
would reach TTS. intercept() is the worker-side filter: it holds tokens back from TTS from <tool_call> to
</tool_call> and returns the captured calls. Writes <out>.jsonl and <out>.md.

    python experiments/fdb-chain/duplex_probe.py --fdb-v3-dir <v3> --data-dir <fdb_v3_data_released> \
        --harness-json <private export> --variants fdb-native fdb-delegate-strict --every 10 --out results/duplex --gpus 0,1
"""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from fdb_tools import load_scenarios
from run_chain import TOOL_CALL, frontend_system, load_items, parse_calls

OPEN, CLOSE = "<tool_call>", "</tool_call>"


def intercept(unit_texts):
    """Split a duplex text stream into what TTS would speak and the tool calls held back from it."""
    spoken, calls, buf, inside = [], [], "", False
    for text in unit_texts:
        out = ""
        buf += text
        while buf:
            if inside:
                end = buf.find(CLOSE)
                if end < 0:
                    break  # keep holding until the call closes
                calls.append(buf[: end + len(CLOSE)])
                buf, inside = buf[end + len(CLOSE):], False
            else:
                start = buf.find(OPEN)
                if start >= 0:
                    out, buf, inside = out + buf[:start], buf[start:], True
                    continue
                # keep a possible partial "<tool_call>" at the end in the buffer
                keep = max((k for k in range(1, len(OPEN)) if buf.endswith(OPEN[:k])), default=0)
                out, buf = out + buf[: len(buf) - keep], buf[len(buf) - keep:]
                break
        spoken.append(out)
    return spoken, calls


def run_one(duplex, tokenizer, system, audio, silence_s, seed):
    torch.manual_seed(seed)
    duplex.prepare(prefix_system_prompt=system)
    chunks = [audio[i:i + 16000] for i in range(0, len(audio), 16000)] + [np.zeros(16000, np.float32)] * silence_s
    units = []
    for t, chunk in enumerate(chunks):
        if not duplex.streaming_prefill(audio_waveform=chunk.astype(np.float32))["success"]:
            continue
        before = len(duplex.res_ids)
        out = duplex.streaming_generate()
        raw = tokenizer.decode(duplex.res_ids[before:], skip_special_tokens=False)
        units.append({"t": t, "listen": out["is_listen"], "text": out["text"], "raw": raw,
                      "end_of_turn": out["end_of_turn"]})
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fdb-v3-dir", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--harness-json", type=Path, required=True)
    ap.add_argument("--variants", nargs="+", default=["fdb-native", "fdb-delegate-strict"])
    ap.add_argument("--every", type=int, default=10, help="take every n-th item")
    ap.add_argument("--silence-s", type=int, default=8)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="openbmb/MiniCPM-o-4_5")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    args = ap.parse_args()

    # Same loading as minicpmo_delegate_tool.load_model, but with the TTS module: as_duplex() initialises it even
    # when no audio is generated.
    from transformers import AutoModel, AutoTokenizer
    gpus = [int(g) for g in args.gpus.split(",")]
    model = AutoModel.from_pretrained(args.model, trust_remote_code=True, attn_implementation="sdpa",
                                      torch_dtype=torch.bfloat16, init_vision=False, init_audio=True, init_tts=True,
                                      device_map="auto", max_memory={g: f"{args.max_mem_gib}GiB" for g in gpus}).eval()
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    # init_tts() also loads the waveform decoder (Token2wav, needs onnxruntime); no audio is generated here, so the
    # TTS module loaded above is enough.
    model.init_tts = lambda *a, **k: None
    duplex = model.as_duplex(generate_audio=False)
    harness = json.loads(args.harness_json.read_text(encoding="utf-8"))
    scenarios = load_scenarios(args.fdb_v3_dir / "benchmark_data_v2.json")
    loader = argparse.Namespace(context="none", fake=False, data_dir=args.data_dir, variant="fdb-native", asr=None)
    items = load_items(loader, scenarios)[:: args.every]

    rows = []
    for variant in args.variants:
        system = frontend_system(variant, harness)
        for i, item in enumerate(items):
            units = run_one(duplex, tokenizer, system, item["audio"], args.silence_s, seed=i)
            raw_stream = [u["raw"] for u in units]
            spoken, calls = intercept(raw_stream)
            leaked = [u["text"] for u in units if OPEN in u["raw"] or CLOSE in u["raw"] or '"arguments"' in u["text"]]
            row = {"variant": variant, "id": item["id"], "units": units, "raw": "".join(raw_stream),
                   "spoken_after_intercept": "".join(spoken), "calls": calls,
                   "parsed": [c[0] if c else None for c in parse_calls("".join(calls))],
                   "call_text_in_tts_input": leaked}
            rows.append(row)
            print(variant, item["id"], "| calls:", row["parsed"], "| said:", row["spoken_after_intercept"][:100])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                              encoding="utf-8")
    lines = ["## Full-duplex probe: tool calls in MiniCPM-o's duplex stream", "",
             "| prompt | items | emits <tool_call> | call text that would reach TTS without the filter | after the filter |",
             "| --- | --- | --- | --- | --- |"]
    for variant in args.variants:
        vr = [r for r in rows if r["variant"] == variant]
        emits = sum(1 for r in vr if r["calls"] or OPEN in r["raw"])
        leaks = sum(1 for r in vr if r["call_text_in_tts_input"])
        after = sum(1 for r in vr if TOOL_CALL.search(r["spoken_after_intercept"]) or OPEN in r["spoken_after_intercept"])
        lines.append(f"| {variant} | {len(vr)} | {emits} | {leaks} | {after} |")
    lines += ["", "Examples:", ""]
    for r in rows[:: max(1, len(rows) // 8)]:
        lines.append(f"- {r['variant']} {r['id']}: raw: {r['raw'][:220]!r}")
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
