"""Plan short requests, the gap the first demo found (DATA_SPEC section 7).

Only 2 of the 15,479 single-turn training clips are 2 s or shorter and 160 are 3 s, and after such requests the trained
model keeps listening for good. Here each plan is one request of at most 8 words, for four targets (a split needs two
requests, so it has none). Plans follow export_ask_topup.ask_plans: train plans use train-domain tools, dev/test plans
the held-out ones, and the assistant's own tool list mixes tools near and far from the domain. Tools with a required
id, coordinate, URL, key or paging value are left out, since nobody says those in eight words. SIZES are what the
writers should keep; plans are 20% over, because writers may skip a task that cannot be short and natural.

Writes plans.jsonl (for import_short.py) and tasks_###.jsonl batches for the writers, one target per batch.

    python export_short_tasks.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl --out check/short
"""
import argparse
import collections
import json
import random
import re
from pathlib import Path

from make_requests_qwen import load_tools

SIZES = {"train": {"call": 400, "handoff": 300, "ask": 200, "chat": 400},
         "dev": {"call": 20, "handoff": 20, "ask": 20, "chat": 20},
         "test": {"call": 40, "handoff": 40, "ask": 40, "chat": 40}}
SPARE, CONTEXT, FILLER, BATCH = 1.2, 0.15, 0.1, 300
UNSAYABLE = re.compile(r"(^|_)(id|ids|uuid|lat|latitude|lon|lng|longitude|url|uri|key|token|hash|cursor|page|offset|limit|code)$",
                       re.I)


def sayable(t):
    need = t["parameters"].get("required") or []
    return len(need) <= 2 and not any(UNSAYABLE.search(re.sub(r"(?<=[a-z])(?=[A-Z])", "_", p)) for p in need)


def plans_for(tools, split, target, n, seed):
    rng = random.Random(seed)
    pool = [t for t in tools.values() if t["split"] == ("train" if split == "train" else "heldout")]
    by = collections.defaultdict(list)
    for t in pool:
        by[t["speed"]].append(t)
        by[t["speed"], t["domain"]].append(t)
    needs = {"call": [t for t in by["fast"] if sayable(t)], "handoff": [t for t in by["slow"] if sayable(t)],
             "ask": [t for t in pool if t["parameters"].get("required") and sayable(t)], "chat": by["fast"]}[target]
    rng.shuffle(needs)
    out = []
    for i in range(n):
        need = needs[i % len(needs)]
        fast_need = [need] if target == "call" or (target == "ask" and need["speed"] == "fast") else []
        slow_need = [need] if target == "handoff" or (target == "ask" and need["speed"] == "slow") else []
        k = rng.randint(2, 6)
        near = [t for t in by["fast", need["domain"]] if t not in fast_need]
        far = [t for t in by["fast"] if t["domain"] != need["domain"] and t not in fast_need]
        listed = fast_need + rng.sample(near, min(len(near), k // 2)) + rng.sample(far, k - min(len(near), k // 2))
        rng.shuffle(listed)
        out.append({"id": f"{split}-s{target[:2]}{i:04d}", "split": split, "target": target, "context": rng.random() < CONTEXT,
                    "speech": "filler" if rng.random() < FILLER else "plain", "followup": target == "ask" and i % 2 == 0,
                    "fast_need": [t["id"] for t in fast_need], "slow_need": [t["id"] for t in slow_need],
                    "listed": [t["id"] for t in listed]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args()
    tools = load_tools(args.tools, args.time)
    show = lambda i: {k: tools[i][k] for k in ("name", "description", "parameters")}
    plans = []
    for a, (split, sizes) in enumerate(SIZES.items()):
        for b, (target, n) in enumerate(sizes.items()):
            plans += plans_for(tools, split, target, round(n * SPARE), args.seed + 10 * a + b)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "plans.jsonl").write_text("".join(json.dumps(p) + "\n" for p in plans), encoding="utf-8")
    tasks = collections.defaultdict(list)
    for p in plans:
        task = {"id": p["id"], "type": p["target"], "with_context": p["context"], "speech": p["speech"]}
        if p["target"] != "chat":
            task |= {"assistant_tools": [tools[i]["name"] for i in p["listed"]], "needs": [show(i) for i in p["fast_need"]],
                     "backend_needs": [show(i) for i in p["slow_need"]], "keeps_going": p["followup"]}
        tasks[p["target"]].append(task)
    rng = random.Random(args.seed)
    n = 0
    for target, batch in tasks.items():
        rng.shuffle(batch)
        for k in range(0, len(batch), BATCH):
            (args.out / f"tasks_{n:03d}_{target}.jsonl").write_text(
                "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in batch[k:k + BATCH]), encoding="utf-8")
            n += 1
    print(len(plans), "plans", collections.Counter((p["split"], p["target"]) for p in plans), n, "batches")
    print("usable tools", {s: sum(sayable(t) and t["split"] == s for t in tools.values()) for s in ("train", "heldout")})


if __name__ == "__main__":
    main()
