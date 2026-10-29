"""Pick the kept samples that need a fix after the whole-dataset review, and export them as writing tasks.

Drops (no writer needed): exact repeats of a request (case and punctuation ignored) and requests to show an image,
photo or video, which a voice assistant cannot do. Fixes, one per sample, in this order of priority:
  ask_lookup  the question asks for an internal id (match id, season id, image id ...): ask for what a person knows
              (a name, the teams and date) and hand off, so the backend finds the id
  new_chat    a chat request on a topic other chat samples already use: a new topic
  numbers     a 7+ digit number reused in 3 or more requests: a different number everywhere in the sample
  full_split  about 20% of split samples: add the fast tool's result and the answer that relays it
  context     a context line that ends with a question, until about 60% of context samples do (now 93.5%)
  filler_add  600 plain requests get "you know", "I mean", "like" and similar
The writer gets the current answer and returns it corrected; import_writer_answers.py --fixes checks it.

    python export_fix_tasks.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl \
        --checked requests_writer_*.jsonl --answers answers_*.jsonl --out fixes/
"""
import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

from make_requests_qwen import ASKS_ID, SIZES, load_tools, plan

BATCH = 100
FULL_SPLIT_SHARE = 0.2
CONTEXT_QUESTION_SHARE = 0.6
FILLER_ADD = 600
VISUAL = re.compile(r"\b(show|display|pull up|bring up|let me see|can i see)\b"
                    r"(?:(?!\b(?:comments?|data|quality|stats|statistics|details|info|information|views|likes|"
                    r"transcript|rules|report|contests?|playlists?|list|titles?|captions?)\b)[^.?!,]){0,40}?"
                    r"\b(image|images|photo|photos|picture|pictures|pic|wallpaper|wallpapers|gif|gifs|thumbnail|logo|"
                    r"chart image|qr code|screenshot|map|meme|icon|avatar|video|clip|selfie)\b"
                    r"(?! (?:data|quality|contests?|comments?|stats|details|titles?|captions?))", re.I)
STOP = set("a an the is are was were do does did why how what when where who which of to in on for and or my me i you "
           "your it its this that be can could would should about really actually just so any some tell know there "
           "their they them have has with from at as by".split())

PROMPT = """You are correcting existing training samples for a voice assistant, by hand, one sample at a time.

IMPORTANT: Do not write or run any code, script, template or fill-in pattern. Read each task and correct its sample
yourself, the way a careful human writer would. Every line you write must be worded differently from the others.

## What you get and what you return

Each task has `id`, `fix` (what to change), `current` (the sample as it is now) and sometimes `tool`, `missing`,
`old` or `avoid`. Return one JSON line per task: the whole corrected sample, with its `id` and the same keys as
`current` plus any keys the fix adds. Change only what the fix asks for; every other field comes back exactly as it
was. Everything stays in English. Return JSON lines only.

## The fixes

`full_split`: the assistant's own tool call in `calls` answers in under half a second. Add `"results"`: a list
with one plausible JSON result per call, with concrete values (`tool` describes the tool). Add `"answer"`: what
the assistant says right after that result, one or two short sentences that give the user the result with
concrete values from it, and say that the other part (the `objective`) is still being worked on ("still
checking", "working on", "underway"). Never say the other part is done.

`ask_lookup`: the assistant asked the user for an internal id (`missing`), which a real person would not know.
Rewrite `"question"` so it asks for what the person does know and the backend can use to find the id: a name, a
title, the teams and the date, a place. Rewrite `"answer"` (the user's reply) to give that, without any id. Then
the assistant hands the task to the backend: `"backend_calls"` is the same call as before (same tool, same
arguments), with `missing` set to the id the backend would find (a realistic value the user never says);
`"objective"` is one sentence telling the backend to find that id from what the user said and then do the task,
repeating every value the user gave exactly as written; `"say"` is a short spoken acknowledgment that claims
nothing is done yet. Remove `"calls"`. Keep `context` and `request` exactly as they are.

`context`: in the last `context` exchange the assistant ends with a question, and far too many samples do. Rewrite
that assistant line so it does not end with a question: a short reaction or remark that still leads naturally to
the request. If the request opens by answering that question ("Yes please", "Yeah"), change only that opening so
it still fits; keep every value in the request.

`numbers`: the number in `old` is reused in many samples. Replace it everywhere it appears in the sample (request,
context, revised, calls, backend_calls, objective, results, backend_result, answer) with a different realistic
number of the same kind and length.

`filler_add`: add one or two natural spoken fillers to `"request"` only, where a person would really say them:
"you know", "I mean", "like", "kind of", "sort of" or "well" (not "um" or "uh"). Change no value and add nothing
to any other field.

`new_chat`: this chat request repeats a topic other samples already use (listed in `avoid`). Write a new chat
sample on a clearly different topic: new `"scenario"`, `"request"` and `"reply"`, and new `"context"` if the
sample has context. As before: no tool and no outside information needed (never weather, news, prices or
schedules); the reply is brief and warm and never says what the assistant cannot do.

## Tasks

"""


def h(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16)


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.lower())).strip()


def request_of(x):
    return next(e["text"] for e in x["events"] if e["role"] == "user" and not e.get("context"))


def decision(x):
    return next(e for e in x["events"] if e["role"] == "assistant" and not e.get("context"))


def shingles(s):
    w = norm(s).split()
    return {" ".join(w[i:i + 3]) for i in range(max(1, len(w) - 2))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True)
    ap.add_argument("--checked", type=Path, nargs="+", required=True)
    ap.add_argument("--answers", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261028)
    args = ap.parse_args()

    tools = load_tools(args.tools, args.time)
    by_name = {t["name"]: t for t in tools.values()}
    plans = {}
    for k, (split, size) in enumerate(SIZES.items()):
        plans.update({p["id"]: p for p in plan(tools, split, size, args.seed + k)})
    show = lambda t: {k: t[k] for k in ("name", "description", "parameters")}
    S = {}
    for path in args.checked:
        for x in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
            S[x["id"]] = x
    answers = {}
    for path in args.answers:
        for a in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
            if a.get("id") in S:
                answers[a["id"]] = a
    ids = sorted(S)

    drop = {}
    groups = collections.defaultdict(list)
    for i in ids:
        groups[norm(request_of(S[i]))].append(i)
    for same in groups.values():
        for i in same[1:]:
            drop[i] = f"repeats {same[0]}"
    for i in ids:
        if i not in drop and VISUAL.search(request_of(S[i])):
            drop[i] = "asks to show an image, photo or video"

    fixes = {}
    def add(i, fix, **extra):
        if i not in drop and i not in fixes and i in answers:
            fixes[i] = {"id": i, "fix": fix, **extra}

    for i in ids:
        x = S[i]
        if x["target"] == "ask" and ASKS_ID.search(decision(x)["say"]):
            add(i, "ask_lookup")

    # chat topics: the same content words, or word 3-gram overlap >= 0.5 with an earlier chat request
    chats = [i for i in ids if S[i]["target"] == "chat"]
    core = lambda s: frozenset(w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOP and len(w) > 3)
    first_of, sh = {}, {i: shingles(request_of(S[i])) for i in chats}
    for n, i in enumerate(chats):
        c = core(request_of(S[i]))
        twins = [j for j in chats[:n] if len(sh[i] & sh[j]) / max(len(sh[i] | sh[j]), 1) >= 0.5]
        if c in first_of or twins:
            same = [first_of.get(c)] + twins if c in first_of else twins
            add(i, "new_chat", avoid=[request_of(S[j]) for j in same if j][:10])
        first_of.setdefault(c, i)

    seen = collections.defaultdict(list)
    for i in ids:
        for num in sorted(set(re.findall(r"\b\d{7,}\b", request_of(S[i])))):
            seen[num].append(i)
    for num, users in seen.items():
        if len(users) >= 3:
            for i in users[1:]:
                add(i, "numbers", old=num)

    splits = sorted((i for i in ids if S[i]["target"] == "split" and i not in drop), key=lambda i: h("full" + i))
    for i in splits[:round(FULL_SPLIT_SHARE * len(splits))]:
        add(i, "full_split")

    with_context = [i for i in ids if i not in drop and any(e.get("context") for e in S[i]["events"])]
    asking = [i for i in with_context if (answers.get(i, {}).get("context") or [{}])[-1].get("assistant", "").strip().endswith("?")]
    need = len(asking) - round(CONTEXT_QUESTION_SHARE * len(with_context))
    for i in sorted(asking, key=lambda i: h("context" + i)):
        if need <= 0:
            break
        if i not in fixes:
            add(i, "context")
            need -= 1

    plain = sorted((i for i in ids if S[i].get("speech", "plain") == "plain"), key=lambda i: h("filler" + i))
    for i in [i for i in plain if i not in drop and i not in fixes][:FILLER_ADD]:
        add(i, "filler_add")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "drop.jsonl").write_text("".join(json.dumps({"id": i, "reason": r}) + "\n" for i, r in sorted(drop.items())))
    (args.out / "fixes.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in fixes.values()))
    tasks = []
    for f in sorted(fixes.values(), key=lambda f: (f["fix"], f["id"])):
        task = {"id": f["id"], "fix": f["fix"], "current": answers[f["id"]]}
        x = S[f["id"]]
        if f["fix"] == "full_split":
            task["tool"] = [show(by_name[c["name"]]) for c in decision(x)["calls"]]
        elif f["fix"] == "ask_lookup":
            calls = task["current"].get("calls") or task["current"].get("backend_calls") or []
            task["tool"] = [show(by_name[c["name"]]) for c in calls if c.get("name") in by_name]
            task["missing"] = plans[f["id"]]["missing"]
        for k in ("old", "avoid"):
            if k in f:
                task[k] = f[k]
        tasks.append({k: v for k, v in task.items() if v is not None})
    (args.out / "PROMPT.md").write_text(PROMPT, encoding="utf-8")
    (args.out / "batches").mkdir(exist_ok=True)
    (args.out / "prompts").mkdir(exist_ok=True)
    for k in range(0, len(tasks), BATCH):
        lines = "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks[k:k + BATCH])
        (args.out / "batches" / f"fix_{k // BATCH:03d}.jsonl").write_text(lines, encoding="utf-8")
        (args.out / "prompts" / f"fix_prompt_{k // BATCH:03d}.md").write_text(PROMPT + lines, encoding="utf-8")

    print(f"samples {len(S)}  drop {len(drop)}  fix {len(fixes)}  batches {-(-len(tasks) // BATCH)}")
    print("  drop:", dict(collections.Counter(r.split(" ")[0] if r.startswith("repeats") else r for r in drop.values())))
    print("  fix: ", dict(collections.Counter(f["fix"] for f in fixes.values())))
    after = len(asking) - sum(1 for f in fixes.values() if f["fix"] == "context")
    print(f"  context lines ending with '?': {len(asking)} of {len(with_context)} now, {after} after the fix")


if __name__ == "__main__":
    main()
