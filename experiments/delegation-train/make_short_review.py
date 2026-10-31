"""Draw the stratified sample of the short set for your review (DATA_SPEC section 7), with each item's target, label
and reason written out, and report spoken length from the TTS manifest.

    python make_short_review.py --final delegation_short.jsonl --manifest manifest_0.jsonl --tools tools_split.jsonl \
        --plans plans.jsonl --out review_sample.json
"""
import argparse
import collections
import json
import random
import statistics
from pathlib import Path

PER_TARGET, UNFINISHED = 30, 40


def call_line(c):
    return c["name"] + "(" + ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in c["arguments"].items()) + ")"


def reason(x, tool):
    if x["group"] == "unfinished":
        return "The person stops mid-sentence, so the model must keep listening through the pause and answer only after the rest."
    name = tool["name"] if tool else "a tool"
    return {"chat": "No tool or outside information is needed.",
            "call": f"The assistant's own fast tool {name} covers it, and every argument comes from what the person said.",
            "handoff": f"Only the backend has {name}, a slow tool, so the assistant acknowledges and hands off.",
            "ask": f"{name} needs a value only the person can decide, and they did not give it."}[x["target"]]


def item(x, tools, wavs, plans):
    live = [e for e in x["events"] if not e.get("context")]
    context = [e.get("text") or e.get("say") for e in x["events"] if e.get("context")]
    first = next(e for e in live if e["role"] == "user")
    wav = next(w for w in wavs if not w["context"])
    response = []
    for e in live[1:]:
        if e["role"] == "user":
            response.append(["User answers", e["text"]])
            continue
        if e.get("say"):
            response.append(["Says", e["say"]])
        response += [["Calls", call_line(c)] for c in e.get("calls") or []]
        if e.get("objective"):
            response.append(["Backend objective", e["objective"]])
    ids = x["backend_tools"] + [i for i in x["frontend_tools"] if any(tools[i]["name"] == c["name"] for e in live for c in e.get("calls") or [])]
    plan = plans.get(x["id"], {})
    ids = plan.get("fast_need", []) + plan.get("slow_need", []) or ids  # an ask names its tool only in its plan
    tool = tools[ids[0]] if ids and x["target"] != "chat" else None
    return {"id": x["id"], "target": x["target"], "group": x["group"], "seconds": wav["seconds"], "context": context,
            "request": first["text"], "pause_after": first.get("pause_after"), "pause_s": wav["pause_s"], "response": response,
            "tool": {"name": tool["name"], "description": tool["description"][:160], "required": tool["parameters"].get("required")} if tool else None,
            "reason": reason(x, tool)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--plans", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args()
    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
    wavs = {m["id"]: m["wavs"] for m in map(json.loads, args.manifest.read_text(encoding="utf-8").splitlines())}
    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    plans = {p["id"]: p for p in map(json.loads, args.plans.read_text(encoding="utf-8").splitlines())}
    rng = random.Random(args.seed)
    picked = []
    for target in ("call", "handoff", "ask", "chat"):
        pool = [x for x in X if x["group"] == "short" and x["target"] == target]
        picked += rng.sample(pool, PER_TARGET)
    picked += rng.sample([x for x in X if x["group"] == "unfinished"], UNFINISHED)
    args.out.write_text(json.dumps([item(x, tools, wavs[x["id"]], plans) for x in picked], ensure_ascii=False), encoding="utf-8")
    seconds = collections.defaultdict(list)
    for x in X:
        seconds[x["group"]].append(next(w for w in wavs[x["id"]] if not w["context"])["seconds"])
    for group, v in seconds.items():
        v.sort()
        print(group, len(v), "median", statistics.median(v), "p90", v[len(v) * 9 // 10], "max", v[-1], "over 3.5 s", sum(s > 3.5 for s in v))
    print(len(picked), "picked")


if __name__ == "__main__":
    main()
