"""Draw the stratified review sample (DATA_SPEC section D) from the final set.

50 samples per target (250) plus 30 speech-tagged ones (15 filler, 15 self-correction) not already drawn, each with
the descriptions of the tools the voice model had (fast) and the backend tools its label uses (slow), so a reviewer
can judge the target without opening other files.

    python make_review_sample.py --final delegation_final.jsonl --tools tools_split.jsonl --out review_sample.json
"""
import argparse
import json
import random
from pathlib import Path

PER_TARGET = 50
PER_SPEECH = 15


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261029)
    args = ap.parse_args()

    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines() if l.strip()]
    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    rng = random.Random(args.seed)
    picked = []
    for target in ("call", "handoff", "ask", "split", "chat"):
        picked += rng.sample([x for x in X if x["target"] == target], PER_TARGET)
    ids = {x["id"] for x in picked}
    for style in ("filler", "self_correction"):
        picked += rng.sample([x for x in X if x.get("speech") == style and x["id"] not in ids], PER_SPEECH)

    show = lambda i: {"name": tools[i]["name"], "description": tools[i]["description"][:220]}
    out = []
    for n, x in enumerate(picked):
        out.append({
            "n": n + 1, "id": x["id"], "split": x["split"], "target": x["target"], "full": x["full"],
            "speech": x.get("speech", "plain"), "fix": x.get("fix"), "scenario": x.get("scenario", ""),
            "revised": x.get("revised"), "events": x["events"],
            "fast_tools": [show(i) for i in x["frontend_tools"] if i in tools],
            "slow_tools": [show(i) for i in x["backend_tools"] if i in tools],
        })
    args.out.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out)} samples -> {args.out}")


if __name__ == "__main__":
    main()
