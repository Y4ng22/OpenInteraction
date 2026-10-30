"""Plan new ask samples to bring ask back to its planned 15% after the third fix round (DATA_SPEC section 5).

The first plans picked the missing value at random from a tool's required parameters, so many asks were for a page,
a locale, an internal ID or something a backend could look up; the review dropped 21% of them. Here the writer is
given every required parameter of the tool (`missing_candidates`) and picks the one only the user can decide, or
skips the task when there is none. Otherwise plans follow make_requests_qwen.plan: 30% of the needed tools are
backend tools, train plans use train-domain tools and dev/test plans the held-out ones, the assistant's own tool
list mixes tools near and far from the domain, 35% have earlier context, half keep the follow-up turn, and speech
styles come from export_writer_tasks.speech_style. Ids are new ("train-q0000"), so nothing collides with the set.

Writes plans.jsonl (for import_writer_answers.py --plans) and tasks_###.jsonl batches for the writers.

    python export_ask_topup.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl --out check/fixround3/ask_topup
"""
import argparse
import collections
import json
import random
from pathlib import Path

from export_writer_tasks import speech_style
from make_requests_qwen import load_tools

SIZES = {"train": 1150, "dev": 60, "test": 60}
BATCH = 160


def ask_plans(tools, split, n, seed):
    rng = random.Random(seed)
    pool = [t for t in tools.values() if t["split"] == ("train" if split == "train" else "heldout")]
    by = collections.defaultdict(list)
    for t in pool:
        by[t["speed"]].append(t)
        by[t["speed"], t["domain"]].append(t)
    plans = []
    for i in range(n):
        backend = rng.random() < 0.3
        need = rng.choice([t for t in by["slow" if backend else "fast"] if t["parameters"]["required"]])
        fast_need, slow_need = ([], [need]) if backend else ([need], [])
        k = rng.randint(2, 9)
        near = [t for t in by["fast", need["domain"]] if t not in fast_need]
        far = [t for t in by["fast"] if t["domain"] != need["domain"] and t not in fast_need]
        listed = fast_need + rng.sample(near, min(len(near), k // 2)) + rng.sample(far, k - min(len(near), k // 2))
        rng.shuffle(listed)
        plans.append({"id": f"{split}-q{i:04d}", "split": split, "target": "ask", "context": rng.random() < 0.35,
                      "full": False, "followup": i % 2 == 0, "missing": None,
                      "candidates": list(need["parameters"]["required"]),
                      "fast_need": [t["id"] for t in fast_need], "slow_need": [t["id"] for t in slow_need],
                      "listed": [t["id"] for t in listed]})
    return plans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261030)
    args = ap.parse_args()
    tools = load_tools(args.tools, args.time)
    show = lambda i: {k: tools[i][k] for k in ("name", "description", "parameters")}
    plans = [p for k, (split, n) in enumerate(SIZES.items()) for p in ask_plans(tools, split, n, args.seed + k)]
    tasks = []
    for p in plans:
        p["speech"] = speech_style(10_000, p["id"], "ask")  # an index past the plain-only first batches
        task = {"id": p["id"], "type": "ask", "with_context": p["context"],
                "assistant_tools": [tools[i]["name"] for i in p["listed"]],
                "needs": [show(i) for i in p["fast_need"]], "backend_needs": [show(i) for i in p["slow_need"]],
                "missing_candidates": p["candidates"]}
        if p["speech"] != "plain":
            task["speech"] = p["speech"]
        tasks.append(task)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "plans.jsonl").write_text("".join(json.dumps(p) + "\n" for p in plans), encoding="utf-8")
    rng = random.Random(args.seed)
    rng.shuffle(tasks)  # every batch mixes splits and tools
    for k in range(0, len(tasks), BATCH):
        (args.out / f"tasks_{k // BATCH:03d}.jsonl").write_text(
            "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks[k:k + BATCH]), encoding="utf-8")
    print(f"{len(plans)} plans, {collections.Counter(p['speech'] for p in plans)}, {-(-len(tasks) // BATCH)} batches")


if __name__ == "__main__":
    main()
