"""Delegation samples (fast tools in the frontend, the rest handed off), written by Qwen3-14B and checked.

The voice frontend holds only fast tools (expected time 0.5 s or less, label_time.py plus the hand review) and
spawn_thinking; slow tools belong to the backend and never appear in the frontend's list. Each sample is one of:
  call     the frontend's tools cover the request (one or two calls, possibly dependent): it calls them
  handoff  the request needs something the frontend's tools cannot do (a slow tool, alone or after a step it
           depends on): spawn_thinking with an objective that keeps every value the user gave, plus a short
           spoken acknowledgment that claims nothing is done yet
  split    two independent requests in one turn, one the frontend can do and one it cannot: it calls its tool
           and hands the other part off
  ask      a value only the user can decide is missing: a short question; half of these also carry the user's
           answer and the call or handoff that follows
  chat     no tool is needed: a short spoken reply
Each sample takes two calls to the teacher: the first sees the tools and fixes the structure (calls, values,
results); the second writes every spoken line from a short intent and the values alone, never seeing a tool
description, so requests cannot be reworded descriptions. About 20% of call and handoff samples are "full": they go on past the decision with a mock tool result (or the
backend's result) and the spoken answer that relays it. The rest end at the decision.

Samples are stored model-neutral as a list of events, so the same data converts to Fun-Audio-Chat or to
MiniCPM-o's duplex streams later:
  {"role": "user", "text"}                       earlier turns carry "context": true
  {"role": "assistant", "calls": [...], "objective": str|None, "say": str|None}
  {"role": "tool", "name", "result"}             full samples only
  {"role": "backend", "text"}                    full handoff samples only: the backend's final result
Plans are drawn with a fixed seed; the script resumes.

    python make_requests_qwen.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl --out requests_qwen.jsonl [--limit 10]
"""
import argparse
import collections
import json
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from make_requests import ask_teacher, check_calls, check_grounded, check_objective, check_request, fn, scalars, shown

TARGETS = {"call": 0.35, "handoff": 0.30, "split": 0.10, "ask": 0.15, "chat": 0.10}
SIZES = {"train": 14000, "dev": 700, "test": 700}  # plans; about 70% pass the checks
FULL_SHARE = 0.2
# an acknowledgment must not announce the user's task as finished before the backend reports back
CLAIMS_DONE = re.compile(r"\b(done|completed|booked|ordered|reserved|added|scheduled|placed|paid|transferred|"
                         r"cancel+ed|submitted|all set)\b", re.I)

TEACHER = """You write training data for a voice assistant. Output JSON only.

The assistant can call only these tools itself:
{tools}
For anything else it hands the request to a backend agent with spawn_thinking, which has many more tools.

{task}

Rules:
- The user speaks naturally and casually, the way people talk to a voice assistant, with concrete values (names, places, dates, amounts, ids) where needed. Never mention tool or function names, and never reword a tool's description into the request.
  Bad: "Schedule a property inspection based on the property details."  Good: "Can you book an inspection for my place at 42 Elm Street next Tuesday morning?"
  Bad: "Simulate the outcome of a gene editing sequence."  Good: "What happens if we knock out BRCA1 exon 11 with the guide I sent you?"
- Never use placeholders such as $USER_ID or <id>; invent a realistic value the user would say instead.
- Every argument value of the assistant's own calls must come from what the user says; leave out optional arguments the user did not mention. In "backend_calls" the backend may fill in values the user did not give, but "objective" never contains them. In a second call, write "$RESULT_1" for a value that can only come from the first call's result.
- Spoken lines are short (one or two sentences) and sound natural aloud.
{context_rule}
- Also give "intent": what the user wants in 3 to 8 plain words, without the tool's wording (for a split, "intent" covers both parts).
Answer with JSON only."""

# Stage 2 writes every spoken line without seeing any tool description, so it cannot reword one.
SURFACE = """Write how a real person would say this to a voice assistant, and the assistant's lines. JSON only.

What the user wants: {intent}
Details the user gives: {details}
{extra}
Rules:
- Casual spoken English, one or two sentences per line. Keep every detail exactly as written above (numbers, ids, names, dates); add no other facts.
{context_rule}
JSON: {keys}"""

CONTEXT_RULES = {
    True: '- "context" is one or two earlier exchanges before the request, as [{"user": "...", "assistant": "..."}]: a short natural lead-in that does not already give the answer and never repeats the request.',
    False: '- "context" is [].',
}

FULL_CALL = ''' Also write "results": one plausible JSON result per call, and "answer": what the assistant says to the user using those results.'''
FULL_HANDOFF = ''' Also write "backend_result": the backend's final report after doing the task (one or two sentences with concrete outcomes), and "answer": what the assistant then tells the user.'''

TASKS = {
    "call": """Task: the user asks for something the assistant's own tool(s) {need} can do{two}.
JSON: {{"context": [...], "request": "...", "calls": [{{"name": "...", "arguments": {{...}}}}]{full_keys}}}""",
    "handoff": """Task: the user asks for something that needs this backend capability, which the assistant does NOT have: {absent}{chain}
The assistant hands it to the backend and says a short acknowledgment that does not claim anything is done yet.
JSON: {{"context": [...], "request": "...", "backend_calls": [{backend_calls}], "objective": "...", "say": "..."{full_keys}}}
"backend_calls" are the calls the backend will make. "objective" is a one-sentence task description for the backend that repeats every value the user gave exactly as written in "backend_calls".""",
    "split": """Task: in one turn the user asks for two unrelated things: one the assistant's own tool {fast} can do, and one that needs this backend capability the assistant does NOT have: {absent}
The two must not depend on each other. The assistant calls its tool, hands the other part to the backend, and says a short acknowledgment that claims nothing is done yet.
JSON: {{"context": [...], "request": "...", "calls": [{{"name": "{fast}", "arguments": {{...}}}}], "backend_calls": [{{"name": "{slow}", "arguments": {{...}}}}], "objective": "...", "say": "..."}}
"objective" covers only the backend part and repeats every value the user gave for it exactly as written in "backend_calls".""",
    "ask": """Task: the user asks for something that needs {tool_desc}, but leaves out the required value "{missing}", which only the user can decide. The assistant asks one short question for it. Then the user answers. The context must not mention "{missing}".
JSON: {{"context": [...], "request": "...", "question": "...", "answer": "...", "{calls_key}": [{{"name": "{tool}", "arguments": {{... including {missing} from the answer ...}}}}]{handoff_keys}}}""",
    "chat": """Task: the user says something that needs no tool and no outside information at all: small talk, feelings, a joke, an opinion, or a stable general-knowledge question (not weather, news, prices, schedules or anything that changes). The assistant replies briefly and warmly, without saying what it cannot do.
JSON: {{"context": [...], "request": "...", "reply": "..."}}""",
}


def load_tools(tools_path, time_path):
    speed = {r["id"]: r["speed"] for r in map(json.loads, time_path.read_text(encoding="utf-8").splitlines())}
    tools = {}
    for t in map(json.loads, tools_path.read_text(encoding="utf-8").splitlines()):
        if speed.get(t["id"]) in ("fast", "slow"):
            tools[t["id"]] = {**t, "speed": speed[t["id"]]}
    return tools


def plan(tools, split, n, seed):
    """Deterministic plans: target, the tools the request needs, the frontend's tool list, full or not."""
    rng = random.Random(seed)
    pool = [t for t in tools.values() if t["split"] == ("train" if split == "train" else "heldout")]
    by = collections.defaultdict(list)
    for t in pool:
        by[t["speed"]].append(t)
        by[t["speed"], t["domain"]].append(t)
    plans = []
    for i in range(n):
        target = rng.choices(list(TARGETS), weights=list(TARGETS.values()))[0]
        p = {"id": f"{split}-{i:05d}", "split": split, "target": target, "context": rng.random() < 0.35,
             "full": target in ("call", "handoff") and rng.random() < FULL_SHARE}
        fast_need, slow_need = [], []
        if target == "call":
            fast_need = [rng.choice(by["fast"])]
            same = [t for t in by["fast", fast_need[0]["domain"]] if t is not fast_need[0]]
            if same and rng.random() < 0.25:
                fast_need.append(rng.choice(same))
        elif target == "handoff":
            slow_need = [rng.choice(by["slow"])]
            feeders = [t for t in by["fast", slow_need[0]["domain"]] + by["slow", slow_need[0]["domain"]]
                       if t is not slow_need[0]]
            if feeders and rng.random() < 0.3:
                slow_need.insert(0, rng.choice(feeders))  # a step the backend task depends on
        elif target == "split":
            fast_need, slow_need = [rng.choice(by["fast"])], [rng.choice(by["slow"])]
        elif target == "ask":
            needs_backend = rng.random() < 0.3
            choices = [t for t in by["slow" if needs_backend else "fast"] if t["parameters"]["required"]]
            (slow_need if needs_backend else fast_need).append(rng.choice(choices))
            need = (slow_need or fast_need)[0]
            p["missing"] = rng.choice(need["parameters"]["required"])
            p["followup"] = i % 2 == 0
        domain = (fast_need or slow_need or [rng.choice(by["fast"])])[0]["domain"]
        k = rng.randint(2, 9)
        near = [t for t in by["fast", domain] if t not in fast_need]
        far = [t for t in by["fast"] if t["domain"] != domain and t not in fast_need]
        listed = fast_need + rng.sample(near, min(len(near), k // 2)) + rng.sample(far, k - min(len(near), k // 2))
        rng.shuffle(listed)
        p["fast_need"], p["slow_need"], p["listed"] = [t["id"] for t in fast_need], [t["id"] for t in slow_need], [t["id"] for t in listed]
        plans.append(p)
    return plans


def prompt_for(p, tools):
    listed = "\n".join(json.dumps(fn(tools[i])["function"], ensure_ascii=False) for i in p["listed"])
    fast = [tools[i]["name"] for i in p["fast_need"]]
    slow = [tools[i] for i in p["slow_need"]]
    show = lambda t: json.dumps({"name": t["name"], "description": t["description"], "parameters": t["parameters"]}, ensure_ascii=False)
    if p["target"] == "call":
        task = TASKS["call"].format(need=" and ".join(fast), two=" (two calls)" if len(fast) > 1 else "",
                                    full_keys=', "results": [...], "answer": "..."' if p["full"] else "")
        task += FULL_CALL if p["full"] else ""
    elif p["target"] == "handoff":
        chain = (f"\nThe backend will first need {slow[0]['name']} ({slow[0]['description']}) and then use its result."
                 if len(slow) > 1 else "")
        task = TASKS["handoff"].format(absent=show(slow[-1]), chain=chain,
                                       backend_calls=", ".join(f'{{"name": "{t["name"]}", "arguments": {{...}}}}' for t in slow),
                                       full_keys=', "backend_result": "...", "answer": "..."' if p["full"] else "")
        task += FULL_HANDOFF if p["full"] else ""
    elif p["target"] == "split":
        task = TASKS["split"].format(fast=fast[0], absent=show(slow[0]), slow=slow[0]["name"])
    elif p["target"] == "ask":
        backend = bool(slow)
        need = (slow or [tools[p["fast_need"][0]]])[0]
        task = TASKS["ask"].format(
            tool_desc=(f"this backend capability the assistant does NOT have: {show(need)}" if backend
                       else f"the assistant's own tool {need['name']}"),
            tool=need["name"], missing=p["missing"], calls_key="backend_calls" if backend else "calls",
            handoff_keys=', "objective": "...", "say": "..."' if backend else "")
        if backend:
            task += '\nAfter the answer the assistant hands the task to the backend: "objective" repeats every value the user gave exactly as written, and "say" is a short acknowledgment that claims nothing is done yet.'
    else:
        task = TASKS["chat"]
    return TEACHER.format(tools=listed, task=task, context_rule=CONTEXT_RULES[p["context"]])


def humanize(name):
    return re.sub(r"[_\s]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)).strip().lower()


def details_of(calls, keep=None, skip=()):
    """'name: value' for each argument value (top level and nested), except $RESULT references and skipped keys."""
    out = []
    for c in calls or []:
        for k, v in (c.get("arguments") or {}).items():
            if k in skip:
                continue
            vals = [x for x in scalars(v) if not str(x).startswith("$")]
            vals = [x for x in vals if keep is None or shown(x) in keep]
            if vals:
                out.append(f"{humanize(k)}: {', '.join(str(x) for x in vals)}")
    return "; ".join(out) or "none"


def surface_prompt(p, out1, tools):
    """Stage 2 prompt from stage 1's structure: intent and values only."""
    intent = out1.get("intent") or ""
    said1 = (out1.get("request") or "").lower()
    extra, keys = "", '{"context": [...], "request": "..."'
    if p["target"] == "call":
        details = details_of(out1.get("calls"))
        if p["full"]:
            extra = f"The tool results came back: {json.dumps(out1.get('results'), ensure_ascii=False)}"
            keys += ', "answer": "what the assistant tells the user from those results"'
    elif p["target"] == "handoff":
        details = details_of(out1.get("backend_calls"), keep=said1)
        extra = "The assistant cannot do this itself and hands it to a backend agent."
        keys += (', "objective": "one-sentence task for the backend that repeats every detail exactly", '
                 '"say": "short acknowledgment that claims nothing is done yet"')
        if p["full"]:
            extra += f"\nLater the backend reports: {out1.get('backend_result', '')}"
            keys += ', "answer": "what the assistant then tells the user"'
    elif p["target"] == "split":
        details = (f"(part the assistant does itself) {details_of(out1.get('calls'))} | "
                   f"(part for the backend) {details_of(out1.get('backend_calls'), keep=said1)}")
        extra = "The user asks for both parts in one turn. The assistant does the first part itself and hands the second to a backend agent."
        keys += (', "objective": "one-sentence task for the backend part only, repeating its details exactly", '
                 '"say": "short acknowledgment that claims nothing is done yet"')
    else:  # ask
        key = "backend_calls" if p["slow_need"] else "calls"
        calls = out1.get(key) or []
        missing_value = (calls[0].get("arguments") or {}).get(p["missing"]) if calls else None
        details = details_of(calls, keep=None if key == "calls" else said1, skip=(p["missing"],))
        extra = (f'The user does not say the {humanize(p["missing"])}. The assistant asks one short question for it, '
                 f'then the user answers with: {missing_value}')
        keys += ', "question": "...", "answer": "the user\'s reply"'
        if p["slow_need"]:
            keys += (', "objective": "one-sentence task for the backend repeating every detail exactly", '
                     '"say": "short acknowledgment that claims nothing is done yet"')
    return SURFACE.format(intent=intent, details=details, extra=extra, context_rule=CONTEXT_RULES[p["context"]],
                          keys=keys + "}")


def check_backend_objective(objective, backend_calls, said):
    """The backend fills in what the user did not say (as in Qwen-Audio-Agent, what tools can find out is
    left to the backend), so backend_calls may hold values the user never gave. The objective must not: it carries the
    user's values verbatim and nothing invented."""
    check_objective(objective, backend_calls, said)
    text = objective.lower()
    for c in backend_calls:
        for v in scalars(c["arguments"]):
            s = shown(v)
            if not str(v).startswith("$") and len(s) >= 3 and s not in said and s in text:
                raise ValueError(f"objective has a made-up value {v!r}")


def words(text):
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def check_natural(request, context, needed):
    """Reject requests that reword a tool description or name, repeat the context, or carry filler values."""
    req = words(request)
    low = " ".join(request.lower().split())
    for t in needed:
        name = " ".join(humanize(t["name"]).replace(".", " ").split())
        # two or three words ("growth estimates", "apply for grant") is how people say it; longer is a tool name
        if len(name.split()) >= 4 and name in low:
            raise ValueError("request contains the tool name")
    if re.search(r"\b\w+ standard\b(?! (shipping|delivery|room|plan|deviation|time))", low):
        raise ValueError("filler value standard")
    for t in needed:
        desc = words(t["description"])
        if req and len(req & desc) >= 0.6 * len(req):
            raise ValueError("request copies the tool description")
    if context and len(req & words(context[-1]["user"])) >= 0.8 * max(len(req), 1):
        raise ValueError("context repeats the request")


def check_placeholders(*values):
    for v in values:
        for s in scalars(v):
            if isinstance(s, str) and re.search(r"\$(?!RESULT)[A-Za-z_]|<[a-z_]+>|\{[a-z_]+\}", s):
                raise ValueError("placeholder value")


def check_say(say):
    if not say or len(say.split()) > 25:
        raise ValueError("acknowledgment length")
    if CLAIMS_DONE.search(say):
        raise ValueError("acknowledgment claims it is done")


def check_answer(answer, results):
    if not answer or len(answer.split()) > 60:
        raise ValueError("answer length")
    values = [shown(v) for v in scalars(results) if len(shown(v)) >= 2]
    if values and not any(v in answer.lower() for v in values):
        raise ValueError("answer uses nothing from the result")


def build(p, out, tools):
    by_name = {t["name"]: t for t in (tools[i] for i in p["listed"] + p["slow_need"])}
    names = [tools[i]["name"] for i in p["listed"]]
    context = out.get("context") or [] if p["context"] else []
    if not isinstance(context, list) or any(not isinstance(c, dict) or not c.get("user") or not c.get("assistant") for c in context):
        raise ValueError("bad context")
    request = out.get("request", "")
    check_request(request, names + [tools[i]["name"] for i in p["slow_need"]])
    said = " ".join([c["user"] for c in context] + [request]).lower()
    check_natural(request, context, [tools[i] for i in p["fast_need"] + p["slow_need"]])
    check_placeholders(out.get("calls"), out.get("backend_calls"), out.get("objective"))
    events = [e for c in context for e in ({"role": "user", "text": c["user"], "context": True},
                                           {"role": "assistant", "say": c["assistant"], "context": True})]
    events.append({"role": "user", "text": request})
    fast = [tools[i]["name"] for i in p["fast_need"]]
    slow = [tools[i]["name"] for i in p["slow_need"]]

    if p["target"] == "call":
        check_calls(out.get("calls"), by_name, fast)
        check_grounded(out["calls"], said, by_name)
        events.append({"role": "assistant", "calls": out["calls"], "objective": None, "say": None})
        if p["full"]:
            results = out.get("results")
            if not isinstance(results, list) or len(results) != len(out["calls"]):
                raise ValueError("results")
            check_answer(out.get("answer"), results)
            events += [{"role": "tool", "name": c["name"], "result": r} for c, r in zip(out["calls"], results)]
            events.append({"role": "assistant", "calls": [], "objective": None, "say": out["answer"]})
    elif p["target"] in ("handoff", "split"):
        if p["target"] == "split":
            check_calls(out.get("calls"), by_name, fast)
            check_grounded(out["calls"], said, by_name)
        check_calls(out.get("backend_calls"), by_name, slow)
        if [c["name"] for c in out["backend_calls"]] != slow:
            raise ValueError("backend calls differ from the plan")
        check_backend_objective(out.get("objective"), out["backend_calls"], said)
        check_say(out.get("say"))
        events.append({"role": "assistant", "calls": out.get("calls", []) if p["target"] == "split" else [],
                       "objective": out["objective"], "say": out["say"], "backend_calls": out["backend_calls"]})
        if p["full"]:
            backend_result = out.get("backend_result", "")
            if len(backend_result.split()) < 4:
                raise ValueError("backend result")
            if not out.get("answer") or len(out["answer"].split()) > 60:
                raise ValueError("answer length")
            events += [{"role": "backend", "text": backend_result},
                       {"role": "assistant", "calls": [], "objective": None, "say": out["answer"]}]
    elif p["target"] == "ask":
        question, answer = out.get("question", ""), out.get("answer", "")
        if not question.strip().endswith("?") or len(question.split()) > 25:
            raise ValueError("question")
        q, r = set(re.findall(r"\w+", question.lower())), set(re.findall(r"\w+", request.lower()))
        if len(q & r) > 0.8 * len(q):
            raise ValueError("question copies the request")
        key = "backend_calls" if slow else "calls"
        check_calls(out.get(key), by_name, slow or fast)
        if p["missing"] not in out[key][0]["arguments"]:
            raise ValueError("follow-up lacks the missing value")
        if shown(out[key][0]["arguments"][p["missing"]]) in said:
            raise ValueError("request already gives the missing value")
        events.append({"role": "assistant", "calls": [], "objective": None, "say": question})
        if p["followup"]:
            if not answer.strip() or len(answer.split()) > 70:  # a reply to a question may be one word ("Seattle.")
                raise ValueError("answer length")
            events.append({"role": "user", "text": answer})
            if slow:
                check_backend_objective(out.get("objective"), out[key], said + " " + answer.lower())
                check_say(out.get("say"))
                events.append({"role": "assistant", "calls": [], "objective": out["objective"], "say": out["say"],
                               "backend_calls": out[key]})
            else:
                check_grounded(out[key], said + " " + answer.lower(), by_name)
                events.append({"role": "assistant", "calls": out[key], "objective": None, "say": None})
    else:
        reply = out.get("reply", "")
        if not reply or len(reply.split()) > 40:
            raise ValueError("reply")
        if re.search(r"real-time|don't have|do not have|can't|cannot|unable|as an ai|i'm just", reply, re.I):
            raise ValueError("chat reply says what it cannot do")
        events.append({"role": "assistant", "calls": [], "objective": None, "say": reply, "reply_source": "teacher"})
    return {"id": p["id"], "split": p["split"], "target": p["target"], "full": p["full"],
            "frontend_tools": p["listed"], "backend_tools": p["slow_need"], "events": events}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True, help="tool_time_reviewed.jsonl")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1")
    ap.add_argument("--model", default="qwen3-14b")
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--limit", type=int, help="only the first N plans of each split (trial runs)")
    args = ap.parse_args()

    tools = load_tools(args.tools, args.time)
    plans = []
    for k, (split, n) in enumerate(SIZES.items()):
        plans += plan(tools, split, n, args.seed + k)[: args.limit]
    done = set()
    if args.out.exists():
        done = {json.loads(l)["id"] for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [p for p in plans if p["id"] not in done]
    lock, failures = threading.Lock(), collections.Counter()

    def work(p):
        prompt = prompt_for(p, tools)
        for attempt in range(3):
            try:
                out = ask_teacher(args.url, args.model, prompt, 0.7 + 0.1 * attempt)
                if p["target"] != "chat":  # stage 2: the spoken lines, written without tool descriptions
                    out.update(ask_teacher(args.url, args.model, surface_prompt(p, out, tools), 0.8 + 0.1 * attempt))
                row = build(p, out, tools)
                break
            except Exception as e:  # unreadable JSON or a failed check: try again, then give up on this plan
                reason = re.sub(r"[0-9'\"].*", "", str(e))[:40] or type(e).__name__
        else:
            with lock:
                failures[p["target"], reason] += 1
            return
        with lock, args.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, todo))
    print(f"plans {len(todo)}, failed {sum(failures.values())}: {dict(failures.most_common(10))}")


if __name__ == "__main__":
    main()
