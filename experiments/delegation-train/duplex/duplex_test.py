"""Run the feasibility test items through MiniCPM-o 4.5's real full-duplex loop and score the first decision.

Each test item's first spoken turn is streamed in one-second chunks (MiniCPMODuplex.streaming_prefill and
streaming_generate, greedy unless --decode sampling, no audio output), followed by up to 8 s of silence; the run stops one unit after the
model ends its turn. Tool calls are cut out of the text stream before it would reach TTS (as in
fdb-chain/duplex_probe.py). With --lora, the smoke adapter from train_lora.py is merged into the language model
first. The four tokens the training targets use from the duplex decoder's banned list are allowed (--allow).

    python duplex_test.py --src <snapshot> --final delegation_final.jsonl --tools tools_split.jsonl \
        --manifest manifest.jsonl [--lora lora_smoke] --out results/base
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from build_sequences import SR, system_prompt

OPEN, CLOSE = "<tool_call>", "</tool_call>"
TARGETS = ["call", "handoff", "ask", "split", "chat"]


def intercept(texts):
    """Split the raw unit texts into what TTS would speak and the tool-call bodies held back from it."""
    spoken, calls, buf, inside = "", [], "", False
    for text in texts:
        buf += text
        while buf:
            if inside:
                end = buf.find(CLOSE)
                if end < 0:
                    break
                calls.append(buf[:end])
                buf, inside = buf[end + len(CLOSE):], False
            else:
                start = buf.find(OPEN)
                if start >= 0:
                    spoken, buf, inside = spoken + buf[:start], buf[start + len(OPEN):], True
                    continue
                keep = max((k for k in range(1, len(OPEN)) if buf.endswith(OPEN[:k])), default=0)
                spoken, buf = spoken + buf[:len(buf) - keep], buf[len(buf) - keep:]
                break
    if inside:
        calls.append(buf + "  [unclosed]")
    return spoken, calls


def first_turn(units):
    """The unit texts up to the first <|turn_eos|>: the reply to the user's turn. (Without a tool result coming
    back, the model may call again after a few silent seconds; that is not part of the scored decision.)"""
    out = []
    for u in units:
        raw = u["raw"]
        if "<|turn_eos|>" in raw:
            out.append(raw.split("<|turn_eos|>")[0])
            break
        out.append(raw)
    return out


def merge_lora(llm, path):
    from safetensors.torch import load_file
    cfg = json.loads((path / "adapter_config.json").read_text())
    scale = cfg["lora_alpha"] / cfg["r"]
    sd = load_file(path / "adapter_model.safetensors")
    mods = dict(llm.named_modules())
    n = 0
    for k, a in sd.items():
        if ".lora_A." not in k:
            continue
        b = sd[k.replace(".lora_A.", ".lora_B.")]
        name = k.split("base_model.model.", 1)[1].split(".lora_A.")[0]
        w = mods[name].weight
        w.data += (b.to(w.device, torch.float32) @ a.to(w.device, torch.float32) * scale).to(w.dtype)
        n += 1
    print(f"merged {n} LoRA pairs (scale {scale})")


def norm(v):
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [norm(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().lower()


def judge(x, spoken, calls, fast):
    parsed, bad = [], 0
    for c in calls:
        try:
            obj = json.loads(c.strip())
            parsed.append((obj["name"], obj.get("arguments", {})))
        except (ValueError, KeyError, TypeError):
            bad += 1
    names = [n for n, _ in parsed]
    s, f = "spawn_thinking" in names, any(n in fast for n in names)
    if s and f:
        pred = "split"
    elif s:
        pred = "handoff"
    elif f:
        pred = "call"
    elif names or bad:
        pred = "invalid" if names else "malformed"
    elif "?" in spoken:
        pred = "ask"
    elif spoken.strip():
        pred = "chat"
    else:
        pred = "silent"
    lab = next(e for e in x["events"] if e["role"] == "assistant" and not e.get("context"))
    gold_calls = sorted((c["name"], json.dumps(norm(c["arguments"]), sort_keys=True)) for c in lab.get("calls") or [])
    got = sorted((n, json.dumps(norm(a), sort_keys=True)) for n, a in parsed if n != "spawn_thinking")
    return {"pred": pred, "malformed_calls": bad, "calls": parsed,
            "calls_exact": gold_calls == got if pred == x["target"] and pred in ("call", "split") else None,
            "say_present": bool(spoken.strip()) if pred in ("handoff", "split") else None,
            "json_in_speech": '"name"' in spoken or '"arguments"' in spoken}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--lora", type=Path)
    ap.add_argument("--split", default="test")
    ap.add_argument("--allow", default="10896,11583,320,8", help="banned token ids the training targets use")
    ap.add_argument("--silence-s", type=int, default=8)
    ap.add_argument("--decode", choices=["greedy", "sampling"], default="greedy", help="sampling uses the duplex defaults")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rescore", action="store_true", help="score <out>.jsonl again without running the model")
    args = ap.parse_args()

    if args.rescore:
        report(args, [rescore(json.loads(l), gold_and_fast(args)) for l in args.out.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()])
        return
    from transformers import AutoModel, AutoTokenizer
    n_gpu = torch.cuda.device_count()
    model = AutoModel.from_pretrained(args.src, trust_remote_code=True, attn_implementation="sdpa", torch_dtype=torch.bfloat16,
                                      init_vision=False, init_audio=True, init_tts=True, device_map="auto",
                                      max_memory={g: f"{args.max_mem_gib}GiB" for g in range(n_gpu)}).eval()
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    if args.lora:
        merge_lora(model.llm, args.lora)
    model.init_tts = lambda *a, **k: None
    duplex = model.as_duplex(generate_audio=False)
    allow = {int(i) for i in args.allow.split(",") if i}
    duplex.decoder.forbidden_token_ids[:] = [i for i in duplex.decoder.forbidden_token_ids if i not in allow]

    gold = {x["id"]: x for x in map(json.loads, args.final.read_text(encoding="utf-8").splitlines())}
    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    items = [m for m in map(json.loads, args.manifest.read_text(encoding="utf-8").splitlines()) if m["split"] == args.split]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in items:
        x = gold[m["id"]]
        fast = {tools[i]["name"] for i in x["frontend_tools"] if i in tools}
        torch.manual_seed(0)
        duplex.prepare(prefix_system_prompt=system_prompt(x, tools))
        speech, _ = sf.read(m["wavs"][0]["path"], dtype="float32")
        n_speech = int(np.ceil(len(speech) / SR))
        chunks = [speech[i * SR:(i + 1) * SR] for i in range(n_speech)] + [np.zeros(SR, np.float32)] * args.silence_s
        units, ended_at = [], None
        for t, chunk in enumerate(chunks):
            chunk = np.pad(chunk, (0, SR - len(chunk))).astype(np.float32)
            if not duplex.streaming_prefill(audio_waveform=chunk)["success"]:
                continue
            before = len(duplex.res_ids)
            out = duplex.streaming_generate(decode_mode=args.decode)
            raw = tok.decode(duplex.res_ids[before:], skip_special_tokens=False)
            units.append({"t": t, "listen": out["is_listen"], "raw": raw, "end_of_turn": out["end_of_turn"]})
            if out["end_of_turn"] and ended_at is None:
                ended_at = t
            if ended_at is not None and t > ended_at:
                break
        spoken, calls = intercept(first_turn(units))
        spoke = [u["t"] for u in units if u["raw"].strip()]
        row = {"id": m["id"], "target": x["target"], "speech_s": n_speech, "first_output_t": spoke[0] if spoke else None,
               "interrupted": bool(spoke) and spoke[0] < n_speech - 1, "spoken": spoken, "raw_calls": calls,
               "units": units, **judge(x, spoken, calls, fast)}
        rows.append(row)
        print(m["id"], x["target"], "->", row["pred"], "|", [n for n, _ in row["calls"]], "|", spoken[:100], flush=True)
    args.out.with_suffix(".jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    report(args, rows)


def gold_and_fast(args):
    gold = {x["id"]: x for x in map(json.loads, args.final.read_text(encoding="utf-8").splitlines())}
    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    return gold, tools


def rescore(row, gt):
    gold, tools = gt
    x = gold[row["id"]]
    fast = {tools[i]["name"] for i in x["frontend_tools"] if i in tools}
    spoken, calls = intercept(first_turn(row["units"]))
    row.update(spoken=spoken, raw_calls=calls, **judge(x, spoken, calls, fast))
    return row


def report(args, rows):
    n = len(rows)
    lines = [f"## Duplex test: {args.lora or 'untrained'}, {args.decode} ({n} {args.split} items)", "",
             f"target accuracy: {sum(r['pred'] == r['target'] for r in rows)}/{n}", "",
             "| gold \\ predicted | " + " | ".join(TARGETS + ["invalid", "malformed", "silent"]) + " |",
             "|---" * 9 + "|"]
    for t in TARGETS:
        lines.append(f"| {t} | " + " | ".join(str(sum(r["target"] == t and r["pred"] == p for r in rows))
                                             for p in TARGETS + ["invalid", "malformed", "silent"]) + " |")
    with_calls = [r for r in rows if r["raw_calls"]]
    exact = [r["calls_exact"] for r in rows if r["calls_exact"] is not None]
    say = [r["say_present"] for r in rows if r["say_present"] is not None]
    lines += ["", f"items with a tool call: {len(with_calls)}; malformed call bodies: {sum(r['malformed_calls'] for r in rows)}",
              f"right target call/split with exact calls: {sum(exact)}/{len(exact)}",
              f"handoff/split with a spoken acknowledgment: {sum(say)}/{len(say)}",
              f"call JSON that would be spoken: {sum(r['json_in_speech'] for r in rows)}",
              f"spoke before the user finished: {sum(r['interrupted'] for r in rows)}"]
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
