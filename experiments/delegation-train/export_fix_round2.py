"""Second fix round, on the merged set from merge_fixes.py, after the review of the first fix pass (2026-10-29).

Drops (no writer needed):
  credential      the user says a secret aloud (API key, password, session cookie, access token, PIN) or the assistant
                  asks for one. Ids, order numbers, phone numbers, emails, usernames and postal codes stay. Kept on
                  purpose: a password whose strength the user asks about, a push device token, OTP codes the user sends
                  out, pagination tokens, crypto token contracts.
  non_english     any non-Latin script, or a spoken line with a sentence in another language (translation tasks)
  id_contradiction  an ask_lookup sample whose locked request says the user has the id the question no longer asks
                  for (reviewed by hand)
  chained_call    a frontend call whose argument is $RESULT_1 (the voice model would have to output a placeholder)
Fix:
  new_chat        chat topics are capped at 3 samples each (train first); the rest get a new topic. The writer gets
                  the full list of chat requests that stay (chat_requests_kept.txt), and import_writer_answers.py
                  --avoid-chats rejects a new request on any of those topics.

    python export_fix_round2.py --final delegation_final.jsonl --answers answers_*.jsonl fix_answers_*.jsonl --out round2/
"""
import argparse
import collections
import json
import re
from pathlib import Path

BATCH = 70
TOPIC_CAP = 3
# one area per batch, so writers working in parallel do not land on the same new topics
AREAS = [
    "everyday objects and how they work, home and kitchen technique, sports and game rules, transport, instruments",
    "the human body (no medical advice), animals not in the list, plants and gardening, weather and earth, space",
    "language and etiquette, social situations and advice, opinions and would-you-rather, jokes, toasts and little "
    "poems, work and study habits",
]

GENERIC = set("""a an the is are was were be been do does did why how what when where who which whom of to in on at for
and or but so if my me i i'm i've you you're your it it's its this that these those they them their there here we our
us he she his her him can could would should will just really actually even ever always never still also too very quite
pretty kind sort like know mean um uh hmm erm well okay ok oh yeah yes no not any some something anything thing things
one get got make makes tell give say said ask asked asking want need help think question quick random fun good great
cool best simple easy weird interesting surprising wild fact facts idea ideas trick tricks tip tips way ways keep keeps
much many more most than then from with about into out up down over off through while before after again every all each
other another time times day today tonight night morning now new old little big lot lots bit sure guess wonder
wondering curious difference between true thing whats what's doesn't don't isn't can't anyway exactly honestly year
years seven six four eight five three two nine better right look looks needs have has hey normal feel come comes called
group actual bad stuff near someone people version finally fast faster sometimes everyone somebody heard swear swears
real kid kids son daughter mom dad friend friends wife husband grandson granddaughter toddler niece nephew boss coworker
coworkers""".split())

SECRET_ARG = re.compile(r"(api_?key|apikey|rapidapi|password|passwd|passcode|secret|token|cookie|(^|_)auth($|_|orization)"
                        r"|^pin$|private_?key|credential)", re.I)
NOT_SECRET = re.compile(r"(page|next|cursor|device|push|continuation|contract|^tokens$)", re.I)
SECRET_SAID = re.compile(r"\b(api[ -]?key|(my|our|the) key is|with (my|our) key|secret is|(access|auth) token|my token is|"
                         r"the token is|session cookie|cookie is|cookie (sid|sess)\w*|sid=)", re.I)
SECRET_ASKED = re.compile(r"\b(api key|password|passcode|(login|session) cookie|your cookie|session id|access token|auth token|PIN|"
                          r"secret key)\b")
KEEP_PASSWORD_TOOLS = re.compile(r"password_strength", re.I)
NON_LATIN = re.compile(r"[Ѐ-ӿ֐-ۿऀ-ॿ぀-ヿ㐀-鿿가-힯]")
FOREIGN = re.compile(r"\b(je|suis|vous|très|voudrais|dónde|está|der|die|das|und|ist|ich|della|sono|arigatou|gozaimasu|"
                     r"mille|cents|allé|marché)\b|[ẹọṣ]", re.I)
ID_CONTRADICTION = {  # ask_lookup: the request says the user has the id; reviewed by hand
    "train-02644", "train-03064", "train-08315", "train-09529", "train-09775", "train-11152", "train-11314",
    "train-11349", "train-13311", "dev-00295", "test-00112",
}


def request_of(x):
    return next(e["text"] for e in x["events"] if e["role"] == "user" and not e.get("context"))


def said(x):
    return " ".join(e.get("text", "") for e in x["events"] if e["role"] == "user")


def scalars(v, key=""):
    if isinstance(v, dict):
        for k, w in v.items():
            yield from scalars(w, k)
    elif isinstance(v, list):
        for w in v:
            yield from scalars(w, key)
    else:
        yield key, v


def chat_topic(s):
    stem = lambda w: next((w[:-len(x)] for x in ("ing", "es", "s") if w.endswith(x) and len(w) - len(x) >= 4), w)
    return frozenset(stem(w) for w in re.findall(r"[a-z]+", s.lower()) if w not in GENERIC and len(w) > 2)


def same_topic(a, b):
    n = len(a & b)
    return bool(a and b) and n >= min(2, len(a), len(b)) and n / min(len(a), len(b)) >= 0.5


def drop_reason(x):
    s = said(x).lower()
    calls = [c for e in x["events"] for c in (e.get("calls") or []) + (e.get("backend_calls") or [])]
    for c in calls:
        for k, v in scalars(c.get("arguments") or {}):
            if (isinstance(v, (str, int)) and len(str(v)) >= 4 and str(v).lower() in s and SECRET_ARG.search(k)
                    and not NOT_SECRET.search(k) and not KEEP_PASSWORD_TOOLS.search(c.get("name", ""))):
                return f"credential: the user says {k}"
    if SECRET_SAID.search(said(x)):
        return "credential: the user says a key, token or cookie"
    for e in x["events"]:
        if e["role"] == "assistant" and not e.get("context") and x["target"] == "ask" and SECRET_ASKED.search(e.get("say") or ""):
            return "credential: the assistant asks for one"
    if NON_LATIN.search(json.dumps(x["events"], ensure_ascii=False)):
        return "non_english: non-Latin script"
    for e in x["events"]:
        t = e.get("text") if e["role"] == "user" else e.get("say") if e["role"] == "assistant" else None
        if t and (len(FOREIGN.findall(t)) >= 2 or re.search(r"[ẹọṣ]", t)):
            return "non_english: a sentence in another language"
    if x["id"] in ID_CONTRADICTION:
        return "id_contradiction: the request says the user has the id"
    if any("$RESULT" in json.dumps(e.get("calls") or []) for e in x["events"]):
        return "chained_call: $RESULT in a frontend call"
    return None


PROMPT = """You are writing replacement chat samples for a voice assistant, by hand, one sample at a time.

IMPORTANT: Do not write or run any code, script, template or fill-in pattern. Read each task and write its sample
yourself, the way a careful human writer would. Every line you write must be worded differently from the others.

## What you get and what you return

Each task has `id`, `fix` (always `new_chat`), `current` (the sample as it is now), `avoid` (samples on the same
topic, which stay) and `area` (where to look for a new topic). Return one JSON line per task: the whole new sample,
with its `id` and the same keys as `current`. Return JSON lines only, in English.

## The fix

`new_chat`: this chat request repeats a topic that several other samples already use. Write a new chat sample on a
clearly different topic, taken from `area`: new `"scenario"`, `"request"` and `"reply"`, and new `"context"` if the
sample has context (the same number of exchanges). The topic must not be one in `avoid` or in the list of chat
requests that stay (`chat_requests_kept.txt`): the checker rejects a request that shares most of its content words
with any of them. No tool and no outside information needed (never weather, news, prices or schedules); the reply
is brief and warm and never says what the assistant cannot do. If the old request had a filler ("um", "uh", "I
mean", "you know"), the new one keeps one.

## Tasks

"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True, help="delegation_final.jsonl from merge_fixes.py")
    ap.add_argument("--answers", type=Path, nargs="+", required=True, help="original answers, then the fix answers")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines() if l.strip()]
    answers = {}
    for path in args.answers:  # later files win, so a first-round fix replaces the original answer
        for a in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
            if a.get("id"):
                answers[a["id"]] = a

    drop = {x["id"]: r for x in X if (r := drop_reason(x))}

    chats = sorted((x for x in X if x["target"] == "chat" and x["id"] not in drop),
                   key=lambda x: (x["split"] != "train", x["id"]))
    groups = []  # [topic of the first sample, members]
    for x in chats:
        t = chat_topic(request_of(x))
        g = next((g for g in groups if same_topic(t, g[0])), None)
        if g:
            g[1].append(x)
        else:
            groups.append([t, [x]])
    tasks, kept = [], []
    for t, members in groups:
        kept += members[:TOPIC_CAP]
        for x in members[TOPIC_CAP:]:
            tasks.append({"id": x["id"], "fix": "new_chat", "current": answers[x["id"]],
                          "avoid": [request_of(y) for y in members[:TOPIC_CAP]]})
    tasks.sort(key=lambda t: t["id"])
    per = -(-len(tasks) // len(AREAS))
    for n, t in enumerate(tasks):
        t["area"] = AREAS[n // per]

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "drop.jsonl").write_text("".join(json.dumps({"id": i, "reason": r}) + "\n" for i, r in sorted(drop.items())))
    (args.out / "fixes.jsonl").write_text("".join(json.dumps({"id": t["id"], "fix": "new_chat"}) + "\n" for t in tasks))
    (args.out / "chat_requests_kept.txt").write_text("".join(request_of(x) + "\n" for x in sorted(kept, key=lambda x: x["id"])),
                                                     encoding="utf-8")
    (args.out / "PROMPT.md").write_text(PROMPT, encoding="utf-8")
    (args.out / "prompts").mkdir(exist_ok=True)
    for k in range(0, len(tasks), BATCH):
        lines = "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks[k:k + BATCH])
        (args.out / "prompts" / f"chat2_prompt_{k // BATCH:03d}.md").write_text(PROMPT + lines, encoding="utf-8")

    print(f"samples {len(X)}  drop {len(drop)}  new_chat {len(tasks)}  batches {-(-len(tasks) // BATCH)}")
    print("  drop:", dict(collections.Counter(r.split(":")[0] for r in drop.values())))
    print(f"  chat topics {len(groups)}, chat samples kept {len(kept)}")


if __name__ == "__main__":
    main()
