"""Score run_baseline.py outputs against the labels.

decide: the reply's target is read from what the model did (fast tool calls -> call, spawn_thinking -> handoff, both
-> split, no call with a "?" -> ask, otherwise chat; a call to a tool it was not given -> invalid; a <tool_call>
block that is not a JSON object with a name -> malformed). Then, on items
with the right target: are the calls the labeled ones with the same arguments, does the objective keep the user's
values, is there a spoken acknowledgment. Refusals ("I can't…", "I don't have access…") are counted on their own.

report: does the reply tell the user the result (no new tool call, at least one concrete value from the backend
result, at most 60 words), right after the handoff and after one unrelated exchange.

    python score_baseline.py --final delegation_final.jsonl --decide decide_test.jsonl --report report_test.jsonl
"""
import argparse
import collections
import json
import re
from pathlib import Path

TARGETS = ["call", "handoff", "ask", "split", "chat"]
REFUSE = re.compile(r"\b(i (can't|cannot|can not|am unable|'m unable|don't have|do not have)|unable to|not able to)\b", re.I)
VALUE = re.compile(r"\$?\d[\d,.:]*%?|\b[A-Z][A-Za-z]*(?:[- ][A-Z0-9][A-Za-z0-9]*)*\b")
STOP = {"I", "The", "A", "An", "And", "Or", "But", "If", "So", "Can", "Could", "Please", "What", "How", "Is", "Are", "Do", "Hand",
        "Set", "Get", "Find", "Check", "Make", "Send", "Run", "Book", "Show", "Tell", "Give", "Look", "Let", "My", "Our", "Also"}


def norm(v):
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [norm(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().lower()


def args_of(c):
    a = c.get("arguments")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except json.JSONDecodeError:
            return None
    return a


def values(text):
    return {v.strip(",.").lower() for v in VALUE.findall(text or "") if v not in STOP and len(v.strip(",.")) > 1}


def predicted(r, fast):
    if "error" in r:
        return "error"
    names = [c["name"] for c in r["calls"]]
    f = any(n in fast for n in names)
    s = "spawn_thinking" in names
    if s and f:
        return "split"
    if s:
        return "handoff"
    if f:
        return "call"
    if any(n is None for n in names):
        return "malformed"
    if names:
        return "invalid"
    return "ask" if "?" in r["content"] else "chat"


def label_of(x):
    return next(e for e in x["events"] if e["role"] == "assistant" and not e.get("context"))


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def score_decide(rows, gold):
    conf = collections.Counter()
    by_speech = collections.defaultdict(lambda: [0, 0])
    stats = collections.Counter()
    for r in rows:
        x = gold[r["id"]]
        lab = label_of(x)
        p = predicted(r, set(r["fast_names"]))
        conf[(x["target"], p)] += 1
        ok = p == x["target"]
        by_speech[x.get("speech", "plain")][0] += ok
        by_speech[x.get("speech", "plain")][1] += 1
        if "error" not in r and REFUSE.search(r["content"]):
            stats["refuse"] += 1
            stats["refuse_" + x["target"]] += 1
        if not ok:
            continue
        if p in ("call", "split"):
            gold_calls = sorted((c["name"], json.dumps(norm(c["arguments"]), sort_keys=True)) for c in lab["calls"])
            got = sorted((c["name"], json.dumps(norm(args_of(c)), sort_keys=True)) for c in r["calls"] if c["name"] != "spawn_thinking")
            stats["calls_n"] += 1
            stats["calls_name"] += sorted(n for n, _ in gold_calls) == sorted(n for n, _ in got)
            stats["calls_exact"] += gold_calls == got
        if p in ("handoff", "split"):
            spawn = next(c for c in r["calls"] if c["name"] == "spawn_thinking")
            a = args_of(spawn)
            obj = str(a.get("objective", "")) if isinstance(a, dict) else ""
            user = " ".join(e["text"] for e in x["events"] if e["role"] == "user")
            want = values(lab["objective"]) & values(user)
            stats["obj_n"] += 1
            stats["obj_all_values"] += want <= values(obj)
            stats["say_n"] += 1
            stats["say_present"] += bool(r["content"].strip())
    n = sum(conf.values())
    right = sum(v for (t, p), v in conf.items() if t == p)
    print(f"## decide ({n} items)\n")
    print(f"target accuracy: {right}/{n} = {pct(right, n)}\n")
    cols = TARGETS + ["invalid", "malformed", "error"]
    print("| gold \\ predicted | " + " | ".join(cols) + " | accuracy |")
    print("|---" * (len(cols) + 2) + "|")
    for t in TARGETS:
        row = [conf[(t, p)] for p in cols]
        print(f"| {t} | " + " | ".join(map(str, row)) + f" | {pct(conf[(t, t)], sum(row))} |")
    print("\nby speech style: " + ", ".join(f"{k} {pct(a, b)} ({b})" for k, (a, b) in by_speech.items()))
    print(f"refusals (says it cannot help): {stats['refuse']} of {n} "
          "(" + ", ".join(t + " " + str(stats["refuse_" + t]) for t in TARGETS if stats["refuse_" + t]) + ")")
    print(f"right target, call/split: tool names right {pct(stats['calls_name'], stats['calls_n'])}, "
          f"names and arguments exact {pct(stats['calls_exact'], stats['calls_n'])} (of {stats['calls_n']})")
    print(f"right target, handoff/split: objective keeps every user value {pct(stats['obj_all_values'], stats['obj_n'])}, "
          f"spoken acknowledgment present {pct(stats['say_present'], stats['say_n'])} (of {stats['obj_n']})")


def score_report(rows):
    print("\n## report\n")
    print("| result arrives | n | tells the user | uses a value from the result | ≤ 60 words | hands off again |")
    print("|---|---|---|---|---|---|")
    for when in ("now", "later"):
        rs = [r for r in rows if r["when"] == when and "error" not in r]
        told = [r for r in rs if r["content"].strip() and not r["calls"]]
        uses = [r for r in told if values(r["content"]) & values(r["messages"][-1]["content"])]
        short = [r for r in told if len(r["content"].split()) <= 60]
        again = [r for r in rs if any(c["name"] == "spawn_thinking" for c in r["calls"])]
        print(f"| {when} | {len(rs)} | {pct(len(told), len(rs))} | {pct(len(uses), len(rs))} | {pct(len(short), len(rs))} | {pct(len(again), len(rs))} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--decide", type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    gold = {x["id"]: x for x in map(json.loads, args.final.read_text(encoding="utf-8").splitlines())}
    tools = {t["id"]: t["name"] for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    if args.decide:
        rows = [json.loads(l) for l in args.decide.read_text(encoding="utf-8").splitlines()]
        for r in rows:
            r["fast_names"] = [tools[i] for i in gold[r["id"]]["frontend_tools"] if i in tools]
        score_decide(rows, gold)
    if args.report:
        score_report([json.loads(l) for l in args.report.read_text(encoding="utf-8").splitlines()])


if __name__ == "__main__":
    main()
