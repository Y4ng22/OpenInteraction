"""Build the final sample set from the kept samples, the drop list and the checked fix batches.

A sample on the drop list is removed. A fix that passed its checks replaces the sample. A fix that is missing or
failed keeps the original, except ask_lookup and new_chat, whose originals are the problem and are removed. Then
requests that repeat (case and punctuation ignored) are removed again across the whole set, train first, and the
DATA_SPEC C numbers are reported.

    python merge_fixes.py --checked requests_writer_*.jsonl --drop fixes/drop.jsonl --fixes fixes/fixes.jsonl \
        --fixed fixes/checked/requests_fix_*.jsonl --out delegation_final.jsonl --report merge_report.json
"""
import argparse
import collections
import json
import re
from pathlib import Path

PLAN = {"call": 35, "handoff": 30, "ask": 15, "chat": 10, "split": 10}
REMOVE_IF_UNFIXED = ("ask_lookup", "new_chat")


def rows(paths):
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def request_of(x):
    return next(e["text"] for e in x["events"] if e["role"] == "user" and not e.get("context"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checked", type=Path, nargs="+", required=True)
    ap.add_argument("--drop", type=Path, required=True)
    ap.add_argument("--fixes", type=Path, required=True)
    ap.add_argument("--fixed", type=Path, nargs="*", default=[])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    samples = {x["id"]: x for x in rows(args.checked)}
    drop = {d["id"]: d["reason"] for d in rows([args.drop])}
    fixes = {f["id"]: f["fix"] for f in rows([args.fixes])}
    fixed = {x["id"]: x for x in rows(args.fixed)}

    applied, unfixed, removed = collections.Counter(), collections.Counter(), collections.Counter()
    final = []
    for i, x in sorted(samples.items(), key=lambda kv: (kv[1]["split"] != "train", kv[0])):
        if i in drop:
            removed["dropped: " + drop[i].split(" ")[0]] += 1
            continue
        if i in fixes:
            if i in fixed:
                applied[fixes[i]] += 1
                x = fixed[i]
            else:
                unfixed[fixes[i]] += 1
                if fixes[i] in REMOVE_IF_UNFIXED:
                    removed["unfixed " + fixes[i]] += 1
                    continue
        final.append(x)

    seen, kept = set(), []
    for x in final:  # train first, so a dev/test request that repeats a train one is the one removed
        key = re.sub(r"[^a-z0-9 ]", "", " ".join(request_of(x).lower().split()))
        if key in seen:
            removed["repeated request"] += 1
            continue
        seen.add(key)
        kept.append(x)

    n = len(kept)
    split = collections.Counter(x["split"] for x in kept)
    target = collections.Counter(x["target"] for x in kept)
    speech = collections.Counter(x.get("speech", "plain") for x in kept)
    full = collections.Counter(x["target"] for x in kept if x["full"])
    with_context = [x for x in kept if any(e.get("context") for e in x["events"])]
    asking = sum(1 for x in with_context
                 if [e for e in x["events"] if e.get("context") and e["role"] == "assistant"][-1]["say"].strip().endswith("?"))
    lines = collections.Counter(e.get("say") or e.get("text") for x in kept for e in x["events"]
                                if e.get("context") or (e["role"] == "assistant" and e.get("objective")))
    share = {t: round(100 * target[t] / n, 1) for t in PLAN}
    checks = {
        "at least 13,000 kept": n >= 13000,
        "train >= 11,800, dev >= 600, test >= 600": split["train"] >= 11800 and split["dev"] >= 600 and split["test"] >= 600,
        "each target within 3 points of the plan": all(abs(share[t] - PLAN[t]) <= 3 for t in PLAN),
        "filler >= 8% and self-correction >= 4%": speech["filler"] >= 0.08 * n and speech["self_correction"] >= 0.04 * n,
        "no line 20 or more times": not any(c >= 20 for c in lines.values()),
    }
    report = {"kept": n, "split": dict(split), "target_share": share, "speech": dict(speech), "full": dict(full),
              "context_lines_ending_with_question": f"{asking} of {len(with_context)}",
              "fixes_applied": dict(applied), "fixes_missing_or_failed": dict(unfixed), "removed": dict(removed),
              "data_spec_c": checks}
    args.out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in kept), encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
