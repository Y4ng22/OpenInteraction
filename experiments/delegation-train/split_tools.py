"""Step 3 of the delegation data: join the pool with its labels and hold out whole domains for testing.

Only voice-plausible tools are kept. Tools in the held-out domains never reach training; their requests form the
held-out-tool dev and test sets, which measure whether the handoff boundary carries over to tools and domains the
model has never seen.

    python split_tools.py --tools tools.jsonl --labels tool_labels.jsonl --action-check action_check.jsonl \
        --out tools_split.jsonl
"""
import argparse
import collections
import json
from pathlib import Path

# About 12% of voice-plausible tools and 20% of action tools; real_estate also covers FDB-v3's housing domain.
HELD_OUT = ["health", "food", "calendar", "weather", "real_estate", "jobs"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--action-check", type=Path, help="recheck_actions.py output: actions answered 'no' become lookup")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    labels = {l["id"]: l for l in map(json.loads, args.labels.read_text(encoding="utf-8").splitlines())}
    if args.action_check:
        for c in map(json.loads, args.action_check.read_text(encoding="utf-8").splitlines()):
            if not c["is_action"] and c["id"] in labels:
                labels[c["id"]] = {**labels[c["id"]], "kind": "lookup"}
    rows, counts = [], collections.Counter()
    for tool in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines()):
        label = labels.get(tool["id"])
        if not label or not label["voice_ok"]:
            continue
        split = "heldout" if label["domain"] in HELD_OUT else "train"
        rows.append({**tool, "kind": label["kind"], "domain": label["domain"], "split": split})
        counts[split, label["kind"]] += 1
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    for split in ("train", "heldout"):
        n = {k: counts[split, k] for k in ("lookup", "search", "action")}
        print(f"{split:8s} {sum(n.values()):6d} tools  {n}")
    print(f"wrote {len(rows)} tools to {args.out}")


if __name__ == "__main__":
    main()
