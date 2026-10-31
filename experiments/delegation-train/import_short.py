"""Check the short requests written for export_short_tasks.py, add the unfinished-speech samples, and build the set
for DATA_SPEC section 7.

Answers failing a check are dropped and counted by reason. The rest are cut to export_short_tasks.SIZES. Unfinished
samples teach the other half: a person who stops mid-sentence is not done. They reuse reviewed samples unchanged and
add `pause_after`, a first part of the request that ends on a word a sentence cannot end on, so tts.py puts 1 to 2 s of
silence there and the model must keep listening. They come from delegation_v4 and from the short requests themselves.

    python import_short.py --plans short/plans.jsonl --answers short/answers/*.jsonl --tools tools_split.jsonl \
        --time tool_time_reviewed.jsonl --final fixround4/merged/delegation_v4.jsonl --out short/delegation_short.jsonl
"""
import argparse
import collections
import json
import random
import re
from pathlib import Path

from export_short_tasks import SIZES
from import_writer_answers import check_speech
from make_requests import check_calls, check_grounded, check_objective, check_request
from make_requests_qwen import load_tools

MAX_WORDS, MAX_SAY, MAX_REPLY, MAX_CONTEXT = 8, 12, 25, 12
CLAIMS = re.compile(r"\b(done|finished|completed|booked|sent|here's|here is|here are|found|can't|cannot|unable|human|agent)\b", re.I)
IDENTITY = re.compile(r"\b(qwen|alibaba|openai|anthropic|claude|chatgpt|openbmb|minicpm)\b", re.I)
CHANGES = re.compile(r"\b(weather|forecast|news|price|stock)\b", re.I)
LONG_NUMBER = re.compile(r"\d{5}")
OPEN = set("the a an my your our his her their this that these those to for of in on at with about from and or but "
           "is are was can could would should will i'm i've we're what's how's me".split())
UNFINISHED = {"train": 240, "dev": 30, "test": 60}
UNFINISHED_SHORT = {"train": 60, "dev": 10, "test": 20}
SPARE = 1.3


WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                    "fifteen sixteen seventeen eighteen nineteen".split())}
WORDS |= {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
NUMBER = r"\b(?:" + "|".join(WORDS) + r")(?:[ -](?:" + "|".join(WORDS) + r"))?\b"


def fail(why):
    raise ValueError(why)


def words(text):
    return len((text or "").split())


def norm(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def build(p, a, by_name):
    r, ctx = a.get("request") or "", a.get("context")
    if not 2 <= words(r) <= MAX_WORDS:
        fail("request over 8 words")
    if LONG_NUMBER.search(r):
        fail("long number")
    names = [t for t in p["names"]]
    if words(r) >= 3:
        check_request(r, names)
    if bool(ctx) != p["context"]:
        fail("context does not match the task")
    if ctx and (not ctx.get("user") or not ctx.get("assistant") or max(words(ctx["user"]), words(ctx["assistant"])) > MAX_CONTEXT):
        fail("context line missing or long")
    said = with_digits(" ".join([r] + ([ctx["user"], ctx["assistant"]] if ctx else []) + [a.get("user_answer") or ""]).lower())
    needed = {by_name[i]["name"]: by_name[i] for i in p["fast_need"]}  # names repeat across the catalog
    calls = a.get("calls") or []
    say = a.get("say")

    def finish():
        if p["fast_need"]:
            check_calls(calls, needed, set(needed))
            check_grounded(calls, said, needed)
            return {"role": "assistant", "calls": calls, "objective": None, "say": None}
        if not say or words(say) > MAX_SAY or CLAIMS.search(say):
            fail("bad acknowledgment")
        check_objective(a.get("objective"), [], said)
        return {"role": "assistant", "calls": [], "objective": a["objective"], "say": say}

    events = []
    if ctx:
        events += [{"role": "user", "text": ctx["user"], "context": True}, {"role": "assistant", "say": ctx["assistant"], "context": True}]
    events.append({"role": "user", "text": r})
    if p["target"] == "chat":
        reply = a.get("reply") or ""
        if not 1 <= words(reply) <= MAX_REPLY or IDENTITY.search(reply) or CHANGES.search(r):
            fail("bad chat reply")
        events.append({"role": "assistant", "calls": [], "objective": None, "say": reply, "reply_source": "teacher"})
    elif p["target"] == "ask":
        q = a.get("question") or ""
        if not q.endswith("?") or words(q) > MAX_SAY:
            fail("bad question")
        events.append({"role": "assistant", "calls": [], "objective": None, "say": q})
        if p["followup"]:
            if not 1 <= words(a.get("user_answer")) <= MAX_WORDS:
                fail("bad user answer")
            events.append({"role": "user", "text": a["user_answer"]})
            events.append(finish())
    else:
        events.append(finish())
    check_speech(p["speech"], {"request": r, "calls": calls, "objective": a.get("objective"), "say": say})
    return {"id": p["id"], "split": p["split"], "target": p["target"], "full": False, "frontend_tools": p["listed"],
            "backend_tools": p["slow_need"], "events": events, "writer": "claude", "scenario": "", "speech": p["speech"],
            "group": "short"}


def with_digits(said):
    """The text plus the digits of its spoken numbers, so "three" grounds an argument of 3."""
    found = []
    for m in re.finditer(NUMBER, said):
        n = sum(WORDS[w] for w in re.split(r"[ -]", m.group(0)) if w)
        found += [str(n), str(n * 100), str(n * 1000), str(n * 1000000), str(n * 1000000000)]
    return " ".join([said] + found)


def cut(text, rng):
    """A first part of 2 to 5 words that ends on a word a sentence cannot end on, or None."""
    w = text.split()
    spots = [i for i in range(2, min(6, len(w) - 1)) if w[i - 1].lower() in OPEN]
    return " ".join(w[:rng.choice(spots)]) if spots else None


def unfinished(x, rng):
    """The sample with a pause inside its first scored request, or None if it has no place for one."""
    y = json.loads(json.dumps(x))
    e = next(e for e in y["events"] if e["role"] == "user" and not e.get("context"))
    at = cut(e["text"], rng)
    if not at:
        return None
    e["pause_after"] = at
    y |= {"id": x["id"] + "-p", "group": "unfinished"}
    return y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", type=Path, required=True)
    ap.add_argument("--answers", type=Path, nargs="+", required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True)
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--verdicts", type=Path, nargs="*", default=[], help="Claude review: jsonl of {id, ok, why}")
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args()
    by_id = load_tools(args.tools, args.time)
    plans = {p["id"]: p for p in map(json.loads, args.plans.read_text(encoding="utf-8").splitlines())}
    for p in plans.values():
        p["names"] = [by_id[i]["name"] for i in p["listed"] + p["slow_need"]]
    old = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
    seen = {norm(e["text"]) for x in old for e in x["events"] if e["role"] == "user"}

    answers, why, examples, kept = {}, collections.Counter(), collections.defaultdict(list), []
    for f in args.answers:
        for a in map(json.loads, f.read_text(encoding="utf-8").splitlines()):
            # The writers saw ids with one target letter, which call and chat share; their file names tell them apart.
            answers[re.sub(r"-s[a-z](\d{4})$", rf"-s{f.name[:2]}\1", a["id"])] = a
    for sid, p in plans.items():
        a = answers.get(sid)
        if a is None or a.get("skip"):
            why["unanswered" if a is None else "skipped"] += 1
            continue
        try:
            x = build(p, a, by_id)
            key = norm(a["request"])
            if key in seen:
                fail("request already in the set")
            seen.add(key)
            kept.append(x)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            reason = str(e).split("'")[0].strip() or "malformed"
            why[reason] += 1
            examples[reason].append({"id": sid, "request": a.get("request"), "error": str(e)})

    rng = random.Random(args.seed)
    rng.shuffle(kept)
    pool = list(kept)
    for source, sizes in (([x for x in old if x["speech"] == "plain" and x["target"] != "split"], UNFINISHED), (kept[:], UNFINISHED_SHORT)):
        rng.shuffle(source)
        taken = collections.Counter()
        for x in source:
            if taken[x["split"]] < SPARE * sizes[x["split"]] and (y := unfinished(x, rng)):
                taken[x["split"]] += 1
                pool.append(y)
    pool_file = args.out.with_suffix(".pool.jsonl")
    pool_file.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in pool), encoding="utf-8")

    # Claude's review drops ids from the pool; what is left is cut to size.
    bad = {v["id"] for f in args.verdicts for v in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if not v["ok"]}
    out, count = [], collections.Counter()
    for x in pool:
        short, base = "-s" in x["id"], x["id"].removesuffix("-p")
        key = x["group"], short, x["split"], x["target"] if x["group"] == "short" else ""
        limit = (SIZES[x["split"]][x["target"]] if x["group"] == "short" else
                 (UNFINISHED_SHORT if short else UNFINISHED)[x["split"]])
        if x["id"] not in bad and base not in bad and count[key] < limit:
            count[key] += 1
            out.append(x)
    args.out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    report = {"kept": dict(sorted((" ".join(map(str, k)), n) for k, n in count.items())), "pool": len(pool), "rejected": len(bad),
              "dropped": dict(why.most_common()), "examples": {k: v[:6] for k, v in examples.items()}}
    args.out.with_suffix(".report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "examples"}))


if __name__ == "__main__":
    main()
