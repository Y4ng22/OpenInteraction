"""Third fix round: apply the decisions from the stratified review (DATA_SPEC section 5) and report DATA_SPEC C.

Dropped:
- every first-round ask rewrite (fix "ask_lookup"): the review found them asking for what the user had already said
  or what the backend can look up;
- every sample that reads API values aloud: flag_api_speech.py (Qwen3-14B) proposes candidates, a Claude review of
  each candidate (claude_review/api_verdict_*.jsonl) decides; plus the reviewed ones the user marked unnatural, and
  the two the user passed (player IDs) are kept whatever the reviews said;
- every ask whose question is not needed or not complete, and every chat that needs a tool or whose reply invents
  details the user never gave, from a Claude review of all ask and chat samples (claude_review/verdict_*.jsonl);
- the six reviewed samples whose labels carry details the user never said;
- every sample where the user reads out a card number (the second round's rule for credentials said aloud).
ISO dates stay; TTS reads them as spoken dates.

Added (--extra): new ask samples from export_ask_topup.py that passed import_writer_answers.py and a Claude review
(--extra-verdicts, same rules as the ask review), to bring ask back to its planned share.

    python apply_round3.py --final delegation_final.jsonl --api check/fixround3/api_speech.jsonl \
        --claude check/fixround3/claude_review --review check/fixround2/review/claude_first_pass.json \
        --extra ask_topup/checked.jsonl --extra-verdicts ask_topup/review \
        --out check/fixround3/merged/delegation_v3.jsonl
"""
import argparse
import collections
import json
import re
from pathlib import Path

from merge_fixes import c_report

REVIEW_UNNATURAL = [3, 31, 139, 170, 174, 175, 262, 275]  # review sample numbers, decided 2026-10-29
REVIEW_KEEP = [38, 171]
REVIEW_LEAK = [52, 69, 82, 192, 220, 137]
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
CARD_WORDS = re.compile(r"card|visa|mastercard|amex|cvv|credit|debit", re.I)


def says_card_number(x):
    return any(e["role"] == "user" and CARD.search(e["text"]) and CARD_WORDS.search(e["text"]) for e in x["events"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--api", type=Path, required=True)
    ap.add_argument("--claude", type=Path, required=True, help="folder with verdict_*.jsonl and api_verdict_*.jsonl")
    ap.add_argument("--review", type=Path, required=True)
    ap.add_argument("--extra", type=Path, help="checked new ask samples")
    ap.add_argument("--extra-verdicts", type=Path, help="folder with the Claude review of --extra (verdict_*.jsonl)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
    by_n = {v["n"]: v["id"] for v in json.loads(args.review.read_text(encoding="utf-8"))}
    judged = [json.loads(l) for l in args.api.read_text(encoding="utf-8").splitlines()]
    missing = {x["id"] for x in X} - {r["id"] for r in judged}
    errors = [r for r in judged if r["unnatural"] is None]
    assert not missing and not errors, (len(missing), len(errors))
    read = lambda pattern: [json.loads(l) for p in sorted(args.claude.glob(pattern)) for l in p.read_text(encoding="utf-8").splitlines()]
    candidates = {r["id"] for r in judged if r["unnatural"]}
    api_verdicts = {v["id"]: v["unnatural"] for v in read("api_verdict_*.jsonl")}
    assert candidates == set(api_verdicts), (len(candidates), len(api_verdicts))
    ask_chat = {v["id"]: v for v in read("verdict_*.jsonl")}
    expected = {x["id"] for x in X if x["target"] in ("ask", "chat") and x.get("fix") != "ask_lookup"}
    assert expected == set(ask_chat), (len(expected), len(ask_chat))
    api = {i for i, bad in api_verdicts.items() if bad} | {by_n[n] for n in REVIEW_UNNATURAL}
    api -= {by_n[n] for n in REVIEW_KEEP}
    leak = {by_n[n] for n in REVIEW_LEAK}

    kept, removed, drops = [], collections.Counter(), []
    for x in X:
        reason = ("first-round ask rewrite" if x.get("fix") == "ask_lookup" else
                  "API values read aloud" if x["id"] in api else
                  f"{x['target']} failed review" if x["id"] in ask_chat and not ask_chat[x["id"]]["ok"] else
                  "label detail the user never said" if x["id"] in leak else
                  "card number read aloud" if says_card_number(x) else None)
        if reason:
            removed[reason] += 1
            drops.append({"id": x["id"], "target": x["target"], "split": x["split"], "reason": reason})
            continue
        kept.append(x)

    added = collections.Counter()
    if args.extra:
        verdicts = {v["id"]: v for p in sorted(args.extra_verdicts.glob("verdict_*.jsonl"))
                    for v in map(json.loads, p.read_text(encoding="utf-8").splitlines())}
        extra = [json.loads(l) for l in args.extra.read_text(encoding="utf-8").splitlines()]
        assert set(verdicts) == {x["id"] for x in extra}, "every new sample needs a review verdict"
        seen = {" ".join(e["text"].lower().split()) for x in kept for e in x["events"] if e["role"] == "user"}
        for x in extra:
            request = " ".join(next(e["text"] for e in x["events"] if e["role"] == "user" and not e.get("context")).lower().split())
            if not verdicts[x["id"]]["ok"]:
                added["new ask failed review"] += 1
            elif request in seen:
                added["new ask repeats a request"] += 1
            elif says_card_number(x):
                added["new ask reads a card number"] += 1
            else:
                added["new ask added"] += 1
                kept.append(x)
    report = {**c_report(kept), "removed": dict(removed), "added": dict(added),
              "removed_by_target": dict(collections.Counter(d["target"] for d in drops))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in kept), encoding="utf-8")
    (args.out.parent / "drop.jsonl").write_text("".join(json.dumps(d) + "\n" for d in drops), encoding="utf-8")
    (args.out.parent / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
