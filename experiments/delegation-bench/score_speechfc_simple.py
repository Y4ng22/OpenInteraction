"""Score speechfc_harness.py predictions without Fun-Audio-Chat's scorer.

For each item: did the reply call a tool, which one (the item's own function, a harness tool such
as spawn_thinking, or none), and does a call to the item's function match the ground truth?
A match follows BFCL's single-call rule: the function name is right, every argument given is one of
the allowed values, an argument may be left out only when "" is allowed, and no extra arguments.
This approximates the official scorer; use his score_speechfc.py for numbers comparable to
Fun-Audio-Chat's published results.

    python score_speechfc_simple.py results/speechfc/*/SpeechFC-BFCL-Single/predict-*.json
"""
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def calls(text):
    out = []
    for body in TOOL_CALL.findall(text or ""):
        try:
            call = json.loads(body)
        except json.JSONDecodeError:
            out.append((None, None))
            continue
        args = call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = None
        out.append((call.get("name"), args if isinstance(args, dict) else None))
    return out


def same(value, allowed):
    norm = lambda v: str(v).strip().lower()
    return any(norm(value) == norm(a) for a in allowed if a != "")


def matches(call, truth):
    name, args = call
    (want_name, want_args), = truth.items()
    if name != want_name or args is None:
        return False
    if set(args) - set(want_args):
        return False
    return all(same(args[k], allowed) if k in args else "" in allowed for k, allowed in want_args.items())


def score(path):
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    kinds, exact = Counter(), 0
    for r in rows:
        truth = json.loads(r["ground_truth"])[0]
        item_fns = {t["function"]["name"] for t in json.loads(r["function"])}
        cs = calls(r.get("raw") or r.get("predict"))
        if not cs:
            kinds["no call"] += 1
            continue
        name = cs[0][0]
        kinds["item function" if name in item_fns else f"harness: {name}" if name else "unreadable"] += 1
        exact += any(matches(c, truth) for c in cs)
    first = [r["first_token_s"] for r in rows if isinstance(r.get("first_token_s"), (int, float))]
    n = len(rows)
    print(f"{Path(path).parent.parent.name}: {n} items | called a tool {n - kinds['no call']}/{n} | "
          f"exact match {exact}/{n}" + (f" | first token median {statistics.median(first):.2f} s" if first else ""))
    for k, v in kinds.most_common():
        print(f"    {k}: {v}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        score(p)
