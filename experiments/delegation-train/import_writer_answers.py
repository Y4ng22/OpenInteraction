"""Check answers written by an outside model for export_writer_tasks.py and keep the ones that pass.

Plans are rebuilt with the same seed, each answer goes through make_requests_qwen.build (the same checks as the
local generator), and passing samples are written in the same model-neutral event format. Failures are counted
by task type and reason, with a few examples per reason in --report.

    python import_writer_answers.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl \
        --answers delegation_samples.jsonl --out requests_writer.jsonl --report import_report.json
"""
import argparse
import collections
import json
import re
from pathlib import Path

from export_writer_tasks import speech_style
from make_requests import scalars, shown
from make_requests_qwen import SIZES, build, load_tools, plan

FILLER = re.compile(r"\b(um+|uh+|hmm+|erm|i mean|let me think|you know)\b")
HESITATION = re.compile(r"\b(um+|uh+|hmm+|erm)\b")


def check_speech(style, a):
    """Fillers stay in the request; a self-correction's first value must not reach the targets."""
    targets = [a.get("objective") or "", a.get("say") or ""] + [str(v) for v in scalars(a.get("calls")) + scalars(a.get("backend_calls"))]
    if any(HESITATION.search(t.lower()) for t in targets):
        raise ValueError("filler in the targets")
    request = (a.get("request") or "").lower()
    if style == "filler" and not FILLER.search(request):
        raise ValueError("speech filler missing")
    if style == "self_correction":
        rev = a.get("revised") or {}
        old, new = shown(rev.get("from", "")), shown(rev.get("to", ""))
        if not old or not new or old == new or old not in request or new not in request:
            raise ValueError("self-correction missing")
        if request.index(old) > request.rindex(new):
            raise ValueError("self-correction out of order")
        word = re.compile(rf"\b{re.escape(old)}\b")
        if any(word.search(shown(t)) for t in targets[:1] + targets[2:]):
            raise ValueError("self-correction keeps the first value")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True)
    ap.add_argument("--answers", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261028)
    args = ap.parse_args()

    tools = load_tools(args.tools, args.time)
    plans, styles = {}, {}
    for k, (split, size) in enumerate(SIZES.items()):
        plans.update({p["id"]: p for p in plan(tools, split, size, args.seed + k)})
    for n, p in enumerate(plans.values()):  # same order as export_writer_tasks
        styles[p["id"]] = speech_style(n, p["id"], p["target"])
    answers = [json.loads(l) for l in args.answers.read_text(encoding="utf-8").splitlines() if l.strip()]

    kept, failures, examples = [], collections.Counter(), collections.defaultdict(list)
    seen = collections.Counter()
    for a in answers:
        p = plans.get(a.get("id"))
        if p is None:
            failures["?", "unknown id"] += 1
            continue
        try:
            row = build(p, a, tools)
            check_speech(styles[p["id"]], a)
            text = " ".join(a.get("request", "").lower().split())
            seen[text] += 1
            if seen[text] > 1:
                raise ValueError("duplicate request")
            kept.append({**row, "writer": "outside", "scenario": a.get("scenario", ""),
                         "speech": styles[p["id"]], **({"revised": a["revised"]} if a.get("revised") else {})})
        except Exception as e:
            reason = re.sub(r"[0-9'\"].*", "", str(e)).strip()[:50] or type(e).__name__
            failures[p["target"], reason] += 1
            if len(examples[p["target"], reason]) < 3:
                examples[p["target"], reason].append({"error": str(e)[:200], "answer": a})

    # dev and test must not share a request with train
    train_requests = {" ".join(a.get("request", "").lower().split()) for a in answers if str(a.get("id")).startswith("train")}
    leaked = [r for r in kept if r["split"] != "train" and " ".join(
        [e for e in r["events"] if e["role"] == "user" and not e.get("context")][0]["text"].lower().split()) in train_requests]
    kept = [r for r in kept if r not in leaked]
    failures["dev/test", "request also in train"] += len(leaked)
    # repetition: lines a template would produce
    lines = collections.Counter(e.get("say") or e.get("text") for r in kept for e in r["events"]
                                if e.get("context") or (e["role"] == "assistant" and e.get("objective")))
    repeated = [{"line": s, "count": n} for s, n in lines.most_common(10) if n >= 20]
    scenes = collections.Counter(" ".join(r["scenario"].lower().split()) for r in kept if r["scenario"])
    repeated += [{"line": "scenario: " + s, "count": n} for s, n in scenes.most_common(5) if n >= 5]
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    by_type = collections.Counter(plans[a["id"]]["target"] for a in answers if a.get("id") in plans)
    kept_type = collections.Counter(r["target"] for r in kept)
    report = {"answers": len(answers), "kept": len(kept),
              "by_type": {t: {"answers": by_type[t], "kept": kept_type[t]} for t in by_type},
              "repeated_lines": repeated,
              "failures": [{"type": t, "reason": r, "count": n, "examples": examples[t, r]}
                           for (t, r), n in failures.most_common()]}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"kept {len(kept)} of {len(answers)}")
    for t in by_type:
        print(f"  {t:8s} {kept_type[t]:5d} / {by_type[t]}")
    for (t, r), n in failures.most_common(15):
        print(f"  fail {t:8s} {n:5d}  {r}")
    for x in repeated:
        print(f"  repeated {x['count']:5d}x  {x['line'][:80]}")


if __name__ == "__main__":
    main()
