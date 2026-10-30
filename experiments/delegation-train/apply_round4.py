"""Fourth fix round: changes that come from the duplex format and the training-target review (DATA_SPEC section 6).

- Split acknowledgments are now spoken before the calls run, so every split `say` was checked by Claude in three
  passes and rewritten where needed: one that presented a result or claimed something done
  (split_say_out_*.jsonl), one that added a detail the user never gave (split_detail_out_*.jsonl), and one that
  named only one of the two parts (split_both_out.jsonl). Later passes win.
- Handoff acknowledgments (and the acknowledgment after an ask's answer, when it hands off) were checked the same
  way for details the user never gave, claims of a result, or a human doing the work (say_detail_out_*.jsonl);
  writers had imagined a scene for each sample and its details leaked into these lines.
- A front-end call that puts a plain name into an ID argument the real tool cannot resolve is dropped
  (id_names_verdict.jsonl, Claude's verdict per candidate); a backend call may keep names, the backend looks them up.
- About 10% of handoffs with a backend result get a failed, partial or needs-the-user result instead, and an answer
  that says so (failure_out.jsonl, written by Claude); samples about biology or medicine were left out of this.

    python apply_round4.py --final check/fixround3/merged/delegation_v3.jsonl --dir check/fixround4 \
        --out check/fixround4/merged/delegation_v4.jsonl
"""
import argparse
import collections
import json
from pathlib import Path

from merge_fixes import c_report


def rows(paths):
    return [json.loads(l) for p in paths for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
    d = args.dir
    say = {}
    for o in rows(sorted(d.glob("split_say_out_*.jsonl"))) + rows(sorted(d.glob("split_detail_out_*.jsonl"))):
        if o["changed"]:
            say[o["id"]] = o["say"]
    for o in rows([d / "split_both_out.jsonl"]):
        say[o["id"]] = o["say"]
    ack = {o["id"]: o["say"] for o in rows(sorted(d.glob("say_detail_out_*.jsonl"))) if o["changed"]}
    drop = {v["id"] for v in rows([d / "id_names_verdict.jsonl"]) if v["drop"]}
    failure = {o["id"]: o for o in rows([d / "failure_out.jsonl"])}

    kept, counts = [], collections.Counter()
    for x in X:
        if x["id"] in drop:
            counts["dropped: name in an ID argument"] += 1
            continue
        if x["id"] in say:
            lab = next(e for e in x["events"] if e["role"] == "assistant" and not e.get("context"))
            lab["say"] = say[x["id"]]
            counts["split acknowledgment rewritten"] += 1
        if x["id"] in ack:
            next(e for e in x["events"] if e["role"] == "assistant" and e.get("objective") and e.get("say"))["say"] = ack[x["id"]]
            counts["handoff acknowledgment rewritten"] += 1
        if x["id"] in failure:
            f = failure[x["id"]]
            next(e for e in x["events"] if e["role"] == "backend")["text"] = f["backend_result"]
            [e for e in x["events"] if e["role"] == "assistant"][-1]["say"] = f["answer"]
            x["outcome"] = f["kind"]
            counts[f"backend result made {f['kind']}"] += 1
        kept.append(x)

    report = {**c_report(kept), "changes": dict(counts)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in kept), encoding="utf-8")
    (args.out.parent / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
