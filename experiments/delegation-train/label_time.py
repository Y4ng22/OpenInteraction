"""Expected execution time per tool, from Qwen3-14B, to decide which tools the voice frontend holds.

A tool either belongs to the voice frontend (it answers quickly) or to the backend (slow or multi-system work).
Qwen-Audio-Agent's cockpit app assigns whole domains by hand; our pool is too large for that and has no
measurements, so Qwen3-14B estimates each tool's typical time from call to result, and tools at or under --limit-s seconds go to
the frontend. The estimate is asked twice with different wording; tools whose two answers fall on different sides
of the limit are marked unsure and kept out of training. Resumable.

    python label_time.py --tools tools_split.jsonl --out tool_time.jsonl [--fdb-calibration]
"""
import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from label_tools import ask

ANCHORS = """Reference times (from the moment the call is made until its result is back):
- reading a stored value, a status, a setting or a single record by id; a unit or currency conversion; a simple calculation: about 0.1-0.2 s
- changing a simple setting or a field of the user's own record (a filter, a preference, a profile field, a device switch): about 0.1-0.3 s
- a search or list over a catalog or index (products, listings, flights, places), a route or distance estimate: about 0.3-0.8 s
- a transaction with an outside party that must confirm it (booking, purchase, payment, money transfer, changing a billing or payment arrangement): about 1-3 s
- heavy work (training or fitting a model, a simulation, generating a long document or media, analysing a large dataset): several seconds or more"""

PROMPTS = [
    """Estimate how long this API tool typically takes to return its result.

Tool:
{tool}

{anchors}

Answer with JSON only: {{"seconds": <number>}}""",
    """A voice assistant is about to call this tool and the user is waiting. Roughly how many seconds until the tool's result comes back?

Tool:
{tool}

{anchors}

Reply with JSON only: {{"seconds": <number>}}""",
]


def estimate(url, model, tool, which):
    shown = json.dumps({k: tool[k] for k in ("name", "description", "parameters")}, ensure_ascii=False)
    answer = ask(url, model, PROMPTS[which].format(tool=shown, anchors=ANCHORS))
    m = re.search(r"\{.*\}", answer, re.S)
    return float(json.loads(m.group(0))["seconds"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, help="tools_split.jsonl")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fdb-calibration", action="store_true",
                    help="label FDB-v3's 12 tools instead (calibration only; they never enter training)")
    ap.add_argument("--chain-dir", type=Path, default=Path("/local/data/xy2764/experiments/fdb-chain"))
    ap.add_argument("--limit-s", type=float, default=0.5)
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1")
    ap.add_argument("--model", default="qwen3-14b")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    if args.fdb_calibration:
        sys.path.insert(0, str(args.chain_dir))
        from fdb_tools import FDB_TOOLS
        tools = [{"id": t["function"]["name"], **{k: t["function"][k] for k in ("name", "description", "parameters")}}
                 for t in FDB_TOOLS]
    else:
        tools = [json.loads(l) for l in args.tools.read_text(encoding="utf-8").splitlines()]
    done = set()
    if args.out.exists():
        done = {json.loads(l)["id"] for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()}
    lock = threading.Lock()

    def work(tool):
        try:
            a, b = (estimate(args.url, args.model, tool, i) for i in (0, 1))
        except Exception:
            return
        side = {a <= args.limit_s, b <= args.limit_s}
        row = {"id": tool["id"], "name": tool["name"], "seconds": [a, b],
               "speed": ("fast" if True in side else "slow") if len(side) == 1 else "unsure"}
        with lock, args.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, [t for t in tools if t["id"] not in done]))
    rows = [json.loads(l) for l in args.out.read_text(encoding="utf-8").splitlines()]
    counts = {s: sum(r["speed"] == s for r in rows) for s in ("fast", "slow", "unsure")}
    print(f"labeled {len(rows)} of {len(tools)}: {counts}")


if __name__ == "__main__":
    main()
