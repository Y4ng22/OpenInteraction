"""Second pass over the tools labeled "action": ask Qwen3-14B one yes/no question, does the call change something
outside the assistant? Tools that only compute, generate or plan (a feeding schedule, a distribution plan, a CPR
guide) answer "no" and become "lookup" in split_tools.py. Resumable.

    python recheck_actions.py --tools tools.jsonl --labels tool_labels.jsonl --out action_check.jsonl
"""
import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from label_tools import ask

QUESTION = """Tool:
{tool}

Does calling this tool change something in the outside world: book, reserve, order, buy, pay, transfer, send, post, cancel, delete, schedule, register, control a device, or create or update a stored record or account?
A tool that only computes, generates, plans, analyzes or returns information is "no", even if its name says create or generate.
Answer only "yes" or "no"."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1")
    ap.add_argument("--model", default="qwen3-14b")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    actions = {l["id"] for l in map(json.loads, args.labels.read_text(encoding="utf-8").splitlines()) if l["kind"] == "action"}
    tools = [t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines()) if t["id"] in actions]
    done = set()
    if args.out.exists():
        done = {json.loads(l)["id"] for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()}
    lock = threading.Lock()

    def work(tool):
        shown = json.dumps({k: tool[k] for k in ("name", "description", "parameters")}, ensure_ascii=False)
        try:
            answer = ask(args.url, args.model, QUESTION.format(tool=shown)).strip().lower()
        except Exception:
            return
        if answer.startswith(("yes", "no")):
            with lock, args.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"id": tool["id"], "name": tool["name"], "is_action": answer.startswith("yes")}) + "\n")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, [t for t in tools if t["id"] not in done]))
    rows = [json.loads(l) for l in args.out.read_text(encoding="utf-8").splitlines()]
    print(f"checked {len(rows)} of {len(tools)} action tools; still action: {sum(r['is_action'] for r in rows)}")


if __name__ == "__main__":
    main()
