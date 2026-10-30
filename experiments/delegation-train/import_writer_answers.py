"""Check answers written by an outside model for export_writer_tasks.py and keep the ones that pass.

Plans are rebuilt with the same seed, each answer goes through make_requests_qwen.build (the same checks as the
local generator), and passing samples are written in the same model-neutral event format. Failures are counted
by task type and reason, with a few examples per reason in --report.

    python import_writer_answers.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl \
        --answers delegation_samples.jsonl --out requests_writer.jsonl --report import_report.json

Fix pass: add --fixes fixes.jsonl --originals answers_*.jsonl. Each corrected answer must also pass check_fix (the
fields it may not change, and what its fix must change); full_split and ask_lookup plans are adjusted to match.
Second round (export_fix_round2.py): add --avoid-chats chat_requests_kept.txt, and a new_chat request on the topic of
any of those lines is rejected.
Ask top-up (export_ask_topup.py): add --plans plans.jsonl to read the plans from that file instead of rebuilding
them. The writer picks the missing value from `missing_candidates` and returns it as "missing", or returns
{"id", "skip": "<reason>"} when no required value is one only the user can decide; skips are counted, not kept.
"""
import argparse
import collections
import json
import re
from pathlib import Path

from export_fix_round2 import chat_topic, same_topic
from export_writer_tasks import speech_style
from make_requests import scalars, shown
from make_requests_qwen import SIZES, build, load_tools, plan

FILLER = re.compile(r"\b(um+|uh+|hmm+|erm|i mean|let me think|you know)\b")
HESITATION = re.compile(r"\b(um+|uh+|hmm+|erm)\b")
FILLER_ADD = re.compile(r"\b(you know|i mean|like|kind of|sort of|well)\b")

# Fix pass (export_fix_tasks.py): the writer returns the whole corrected answer; these fields must come back as they were
UNCHANGED = {
    "full_split": ("context", "request", "calls", "backend_calls", "objective"),
    "ask_lookup": ("context", "request"),
    "context": ("calls", "backend_calls", "objective", "question"),
    "numbers": (),
    "filler_add": ("context", "calls", "backend_calls", "objective", "say", "question", "reply", "answer"),
    "new_chat": (),
}


def check_fix(fix, a, old):
    """What each fix must change, beyond the usual checks."""
    for k in UNCHANGED[fix["fix"]]:
        if a.get(k) != old.get(k):
            raise ValueError(f"fix changed {k}")
    request = (a.get("request") or "").lower()
    if fix["fix"] == "context":
        if not a.get("context") or a["context"][-1]["assistant"].strip().endswith("?"):
            raise ValueError("context line still ends with a question")
    elif fix["fix"] == "numbers":
        if fix["old"] in json.dumps(a, ensure_ascii=False):
            raise ValueError("old number still there")
    elif fix["fix"] == "filler_add":
        if len(FILLER_ADD.findall(request)) <= len(FILLER_ADD.findall((old.get("request") or "").lower())):
            raise ValueError("no filler added")
    elif fix["fix"] == "new_chat":
        new, was = set(re.findall(r"[a-z]{3,}", request)), set(re.findall(r"[a-z]{3,}", (old.get("request") or "").lower()))
        if len(new & was) >= 0.5 * max(len(new | was), 1):
            raise ValueError("chat topic not changed")


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
    ap.add_argument("--fixes", type=Path, help="fix pass: fixes.jsonl from export_fix_tasks.py")
    ap.add_argument("--originals", type=Path, nargs="*", default=[], help="fix pass: the original answers_*.jsonl")
    ap.add_argument("--avoid-chats", type=Path, help="second round: chat requests whose topics a new_chat must avoid")
    ap.add_argument("--plans", type=Path, help="ask top-up: plans.jsonl from export_ask_topup.py")
    args = ap.parse_args()

    tools = load_tools(args.tools, args.time)
    plans, styles = {}, {}
    if args.plans:
        plans = {p["id"]: p for p in map(json.loads, args.plans.read_text(encoding="utf-8").splitlines())}
        styles = {i: p["speech"] for i, p in plans.items()}
    else:
        for k, (split, size) in enumerate(SIZES.items()):
            plans.update({p["id"]: p for p in plan(tools, split, size, args.seed + k)})
        for n, p in enumerate(plans.values()):  # same order as export_writer_tasks
            styles[p["id"]] = speech_style(n, p["id"], p["target"])
    fixes, originals = {}, {}
    if args.fixes:
        fixes = {f["id"]: f for f in map(json.loads, args.fixes.read_text(encoding="utf-8").splitlines())}
        for path in args.originals:
            originals.update({a["id"]: a for a in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if a.get("id")})
        for i, f in fixes.items():
            if f["fix"] == "full_split":
                plans[i] = {**plans[i], "full": True}
            elif f["fix"] == "ask_lookup":
                plans[i] = {**plans[i], "lookup": True}
            elif f["fix"] == "filler_add":
                styles[i] = "filler"
    avoid = [(l, chat_topic(l)) for l in args.avoid_chats.read_text(encoding="utf-8").splitlines() if l.strip()] if args.avoid_chats else []
    answers = [json.loads(l) for l in args.answers.read_text(encoding="utf-8").splitlines() if l.strip()]

    kept, failures, examples = [], collections.Counter(), collections.defaultdict(list)
    seen = collections.Counter()
    for a in answers:
        p = plans.get(a.get("id"))
        if p is None:
            failures["?", "unknown id"] += 1
            continue
        if a.get("skip"):
            failures[p["target"], "skipped by the writer"] += 1
            continue
        try:
            if p.get("candidates") is not None:  # ask top-up: the writer picked the missing value
                if a.get("missing") not in p["candidates"]:
                    raise ValueError("missing is not one of the candidates")
                p = {**p, "missing": a["missing"]}
            if fixes:
                if p["id"] not in fixes:
                    raise ValueError("not a fix task")
                check_fix(fixes[p["id"]], a, originals[p["id"]])
                if avoid and fixes[p["id"]]["fix"] == "new_chat":
                    t = chat_topic(a.get("request") or "")
                    hit = next((l for l, u in avoid if same_topic(t, u)), None)
                    if hit:
                        raise ValueError(f"chat topic already used: {hit}")
            row = build(p, a, tools)
            if fixes.get(p["id"], {}).get("fix") != "filler_add":
                check_speech(styles[p["id"]], a)
            elif HESITATION.search(" ".join(str(v) for v in (a.get("objective"), a.get("say"))).lower()):
                raise ValueError("filler in the targets")
            text = " ".join(a.get("request", "").lower().split())
            seen[text] += 1
            if seen[text] > 1:
                raise ValueError("duplicate request")
            kept.append({**row, "writer": "outside", "scenario": a.get("scenario", ""),
                         "speech": styles[p["id"]], **({"revised": a["revised"]} if a.get("revised") else {}),
                         **({"fix": fixes[p["id"]]["fix"]} if fixes else {})})
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
