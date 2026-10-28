"""Score a delegation run against cases.jsonl.

A prediction file is JSON lines with at least `id`, `decision` (self | delegate | frontend_tool |
invented | malformed | error), `objective`, `spoken`, and optionally `tool`, `first_token_s` and
`latency_s` (seconds to the first reply token and to the full reply). Any
runner that writes this layout can be scored, so different frontends are compared on one report.

Each case gets a tier (see tier()):
  must_delegate  live data, post-cutoff facts, external actions, long research or writing;
                 correct: handoff, or a harness tool the routing rules allow (TOOLS_OK)
  either         stable knowledge, explanations, math, advice, translation, troubleshooting;
                 correct: answer directly or hand off
  must_self      chit-chat, conversation control, emotional support, emergencies, short creative
                 requests, opinions, withdrawn requests; correct: answer directly
  clarify        the request lacks its object; correct: answer without a tool call

Six metrics are reported:
  accuracy            correct cases / all cases
  missed delegation   must_delegate cases not handed off (or routed to an allowed tool) / must_delegate cases
  wrong delegation    must_self cases handed off / must_self cases
  invented tools      calls to a tool that was not offered / all tool calls
  clarifying question clarify cases answered without a tool call and with a "?" / clarify cases
  first-token latency median and p90 of seconds from input to the first reply token

    python3 score.py results/minicpmo45-bf16-harness-en-text.jsonl
    python3 score.py --tiers          # print the tier of every case

Writes <prediction>.report.md and <prediction>.objectives.csv (for rating objective fidelity by
hand: faithful / changed / wrong) next to the prediction file.
"""
import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Self-labeled subtypes where handing off adds latency without a better answer.
MUST_SELF_SUBTYPES = {
    "chitchat", "interaction_control", "emotional_support", "game", "emergency",
    "withdrawn_request", "mentions_search", "mentions_news", "short_creative", "opinion",
}
TIERS = ["must_delegate", "either", "must_self", "clarify"]

# Harness tools that the harness routing rules allow for a case instead of the handoff.
AMAP_PLACE_TOOLS = ("amap_geocode", "amap_nearby_search", "amap_reverse_geocode")
TOOLS_OK = {
    "e10": ("amap_weather",),        # weather in a city in China goes to the weather tool
    "d43": ("schedule_reminder",),   # a recurring reminder is the reminder tool's job
    **{case_id: AMAP_PLACE_TOOLS for case_id in ("d09", "d20", "d33", "d35", "d41", "d42", "e35", "e56")},
}
TOOL_DECISIONS = ("delegate", "frontend_tool", "invented", "malformed")


def load_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def tier(case):
    if case["expected"] == "delegate":
        return "must_delegate"
    if case["expected"] == "clarify":
        return "clarify"
    return "must_self" if case["subtype"] in MUST_SELF_SUBTYPES else "either"


def allowed_tool(case, pred):
    return pred["decision"] == "frontend_tool" and pred.get("tool") in TOOLS_OK.get(case["id"], ())


def is_correct(case, pred):
    t, d = tier(case), pred["decision"]
    if t == "must_delegate":
        return d == "delegate" or allowed_tool(case, pred)
    if t == "either":
        return d in ("self", "delegate") or allowed_tool(case, pred)
    return d == "self" or allowed_tool(case, pred)  # must_self, clarify


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def shown_output(pred):
    text = pred.get("objective") or ((f"[{pred['tool']}] " if pred.get("tool") else "") + (pred.get("spoken") or pred.get("error") or ""))
    return text.replace("|", "/").replace("\n", " ")[:120]


def first_tokens(group):
    return sorted(p["first_token_s"] for _, p in group if isinstance(p.get("first_token_s"), (int, float)))


def median_p90(values):
    if not values:
        return "n/a"
    return f"{statistics.median(values):.2f} / {values[int(0.9 * (len(values) - 1))]:.2f}"


def metrics(rows):
    by_tier = defaultdict(list)
    for c, p in rows:
        by_tier[tier(c)].append((c, p))
    calls = [p for _, p in rows if p["decision"] in TOOL_DECISIONS]
    clarify = by_tier["clarify"]
    return {
        "accuracy": pct(sum(is_correct(c, p) for c, p in rows), len(rows)),
        "missed delegation": pct(sum(not is_correct(c, p) for c, p in by_tier["must_delegate"]), len(by_tier["must_delegate"])),
        "wrong delegation": pct(sum(p["decision"] == "delegate" for _, p in by_tier["must_self"]), len(by_tier["must_self"])),
        "invented tools": pct(sum(p["decision"] == "invented" for p in calls), len(calls)),
        "clarifying question": pct(sum(p["decision"] == "self" and "?" in (p.get("spoken") or "") for _, p in clarify), len(clarify)),
        "first-token latency (s), median / p90": median_p90(first_tokens(rows)),
    }, by_tier


def report(cases, preds, name):
    by_id = {p["id"]: p for p in preds}
    rows = [(c, by_id[c["id"]]) for c in cases if c["id"] in by_id]
    missing = [c["id"] for c in cases if c["id"] not in by_id]
    lines = [f"# Delegation report: {name}", ""]
    if missing:
        lines += [f"Missing predictions: {len(missing)} ({', '.join(missing[:10])}{'…' if len(missing) > 10 else ''})", ""]

    values, by_tier = metrics(rows)
    lines += ["## Metrics", "", "| metric | value |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in values.items()]
    lines += ["", "Clarifying question checks only for a \"?\"; confirm by hand.", ""]

    decisions = ["delegate", "frontend_tool", "self", "invented", "malformed", "error"]
    lines += ["## By tier", "",
              "`delegate` = handoff tool called; `frontend_tool` = another offered tool; "
              "`invented` = a tool that was not offered.", "",
              "| tier | cases | correct | " + " | ".join(decisions) + " | first token (s), median / p90 |",
              "| --- | --- | --- |" + " --- |" * len(decisions) + " --- |"]
    for t in TIERS:
        g = by_tier[t]
        counts = Counter(p["decision"] for _, p in g)
        lines.append(f"| {t} | {len(g)} | {pct(sum(is_correct(c, p) for c, p in g), len(g))} | "
                     + " | ".join(str(counts[d]) for d in decisions) + f" | {median_p90(first_tokens(g))} |")
    lines.append("")

    groups = defaultdict(list)
    for c, p in rows:
        groups[c["scenario"]].append((c, p))
    lines += ["## By scenario", "", "| scenario | cases | correct |", "| --- | --- | --- |"]
    for name_ in sorted(groups, key=lambda s: -sum(is_correct(c, p) for c, p in groups[s]) / len(groups[s])):
        g = groups[name_]
        lines.append(f"| {name_} | {len(g)} | {pct(sum(is_correct(c, p) for c, p in g), len(g))} |")
    lines.append("")

    lines += ["## Errors", "", "| id | tier | model | first token (s) | utterance | model output |",
              "| --- | --- | --- | --- | --- | --- |"]
    for c, p in rows:
        if not is_correct(c, p):
            lines.append(f"| {c['id']} | {tier(c)} | {p['decision']} | {p.get('first_token_s') or 'n/a'} | "
                         f"{c['utterance']} | {shown_output(p)} |")
    lines += ["", "## Optional delegations (either-tier cases handed off; counted correct)", "",
              "| id | utterance | objective |", "| --- | --- | --- |"]
    for c, p in by_tier["either"]:
        if p["decision"] == "delegate":
            lines.append(f"| {c['id']} | {c['utterance']} | {shown_output(p)} |")
    return "\n".join(lines) + "\n", rows


def print_tiers(cases):
    print("| id | expected | subtype | tier | harness tools allowed | utterance |")
    print("| --- | --- | --- | --- | --- | --- |")
    for c in cases:
        print(f"| {c['id']} | {c['expected']} | {c['subtype']} | {tier(c)} | "
              f"{', '.join(TOOLS_OK.get(c['id'], ())) or '—'} | {c['utterance']} |")
    print(dict(Counter(tier(c) for c in cases)), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions", type=Path, nargs="?")
    ap.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    ap.add_argument("--tiers", action="store_true", help="print the tier of every case and exit")
    args = ap.parse_args()

    cases = load_jsonl(args.cases)
    if args.tiers:
        print_tiers(cases)
        return
    if not args.predictions:
        ap.error("predictions file required unless --tiers")
    preds = load_jsonl(args.predictions)
    text, rows = report(cases, preds, args.predictions.stem)
    report_path = args.predictions.with_suffix(".report.md")
    report_path.write_text(text, encoding="utf-8")

    csv_path = args.predictions.with_suffix(".objectives.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "scenario", "utterance", "reference_objective", "model_objective", "rating"])
        for c, p in rows:
            if c["expected"] == "delegate" and p["decision"] == "delegate":
                writer.writerow([c["id"], c["scenario"], c["utterance"], c["reference_objective"], p["objective"], ""])
    print(text)
    print(f"wrote {report_path} and {csv_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
