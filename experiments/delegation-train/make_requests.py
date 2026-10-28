"""Step 4 of the delegation data: requests and target replies, written by Qwen3-14B and checked against the schemas.

Each sample is one decision on the user's last turn, with one of four targets:
  call     the request needs only lookup or search tools: the reply is the <tool_call> block(s)
  handoff  the request needs an action tool (alone, or after a search it depends on): the reply is spawn_thinking
           with an objective that carries every value the user gave, verbatim
  ask      a required value is missing: the reply asks for it; half of these also yield a follow-up sample whose
           earlier turns are the request and the question, whose last turn gives the value, and whose target is the
           call or handoff
  cannot   no listed tool can do it: the reply says so
The tool list is the needed tools plus 2-10 distractors (half from the same domain), shuffled, with spawn_thinking
from the harness; no execution times. About a third of samples start with one or two earlier turns as text, as
FDB-v3 items do. Plans are drawn with a fixed seed, so sample ids are stable and the script resumes.

    python make_requests.py --tools tools_split.jsonl --harness harness_frontend_en-search.json \
        --chain-dir /local/data/xy2764/experiments/fdb-chain --out requests.jsonl [--limit 20]
"""
import argparse
import collections
import json
import random
import re
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TARGETS = {"call": 0.35, "handoff": 0.35, "ask": 0.20, "cannot": 0.10}
SIZES = {"train": 14000, "dev": 700, "test": 700}  # plans; about 70% pass the checks
S2T_PROMPT = "You are asked to generate text tokens."  # Fun-Audio-Chat's DEFAULT_S2T_PROMPT, as funaudio_frontend.py prefixes it
INSTRUCTIONS = [
    "You are a helpful voice AI assistant. Keep your responses concise and conversational since they will be spoken "
    "aloud. Respond naturally to whatever the user says or asks.",
    "You are a voice assistant. Answers are spoken aloud, so keep them short and natural.",
    "You are a friendly voice assistant that helps the user get things done. Keep replies brief; they are read out "
    "loud.",
]

TEACHER = """You write training data for a voice assistant. Write one natural spoken request from a user, and the correct response, as JSON only.

The assistant has these tools:
{tools}

{task}

Rules:
- The request sounds like something a person says out loud: casual, specific, with concrete values (names, dates, amounts, ids) where the tools need them. Never mention tool or function names.
- Argument values must follow each parameter's description and type, and every value must come from what the user says. Leave out optional arguments the user did not mention.
{context_rule}
Answer with JSON only."""

TASKS = {
    "call": """Task: the user asks for something that needs {need}. Every required argument must be given in the request.
JSON: {{"context": [...], "request": "...", "calls": [{{"name": "...", "arguments": {{...}}}}]}}""",
    "handoff_single": """Task: the user asks the assistant to do something with {tool}, giving every required value.
JSON: {{"context": [...], "request": "...", "calls": [{{"name": "{tool}", "arguments": {{...}}}}], "objective": "..."}}
"objective" is a one-sentence task description for a backend agent that will do the work; it must repeat every value the user gave exactly as written in "arguments".""",
    "handoff_chain": """Task: the user asks for something that first needs {first} and then, using its result, {second}. Give every value the user knows.
JSON: {{"context": [...], "request": "...", "calls": [{{"name": "{first}", "arguments": {{...}}}}, {{"name": "{second}", "arguments": {{...}}}}], "objective": "..."}}
In the second call, write "$RESULT_1" for any value that can only come from the first call's result. "objective" is a one-sentence task description for a backend agent; it must repeat every other argument value exactly as written in "arguments".""",
    "ask": """Task: the user asks for something that needs {tool}, but leaves out the required value "{missing}" (gives the other required values). The assistant asks for that one value in a short spoken question. Then the user answers with the value. The context must not mention or ask for "{missing}".
JSON: {{"context": [...], "request": "...", "question": "...", "answer": "...", "calls": [{{"name": "{tool}", "arguments": {{... including {missing} from the answer ...}}}}], "objective": "..."}}
"objective" is a one-sentence task description that repeats every argument value exactly as written in "arguments".""",
    "cannot": """Task: the user asks for something that would need this other tool, which the assistant does NOT have: {absent}
None of the assistant's tools can do it. The assistant replies in one or two short spoken sentences that it can't do that here.
JSON: {{"context": [...], "request": "...", "reply": "..."}}""",
}
CONTEXT_RULES = {
    True: '- "context" is one or two earlier exchanges before the request, as [{"user": "...", "assistant": "..."}]: a short, natural lead-in that does not already give the answer.',
    False: '- "context" is [].',
}


def fn(tool):
    return {"type": "function", "function": {k: tool[k] for k in ("name", "description", "parameters")}}


def scalars(v):
    if isinstance(v, dict):
        return [s for x in v.values() for s in scalars(x)]
    if isinstance(v, list):
        return [s for x in v for s in scalars(x)]
    return [] if isinstance(v, bool) or v is None else [v]


def shown(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().lower()


def check_calls(calls, tools_by_name, allowed):
    if not isinstance(calls, list) or not calls:
        raise ValueError("no calls")
    for c in calls:
        tool = tools_by_name.get(c.get("name"))
        if c.get("name") not in allowed or tool is None:
            raise ValueError(f"unexpected tool {c.get('name')}")
        args, props = c.get("arguments") or {}, tool["parameters"]["properties"]
        if any(k not in props for k in args):
            raise ValueError("argument not in schema")
        if any(k not in args for k in tool["parameters"]["required"]):
            raise ValueError("required argument missing")


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def grounded(v, said, enums):
    """True if the value came from the user, is an allowed enum or trivial default, or is a date/time/code the
    user said in words (the teacher normalizes those)."""
    s = shown(v)
    if s in said or v in enums or (isinstance(v, (int, float)) and v in (0, 1)):
        return True
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        # "$240,000" for 240000, "October" for month 10
        if f"{v:,}".removesuffix(".0") in said or (isinstance(v, int) and 1 <= v <= 12 and MONTHS[v - 1] in said):
            return True
    if isinstance(v, str) and "," in v and all(p.strip().lower() in said for p in v.split(",") if p.strip()):
        return True  # "profitability,growth" from "profitability and growth"
    return isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}.*|\d{1,2}:\d{2}.*|[A-Z]{2,3}", v.strip()))


def check_grounded(calls, said, tools_by_name):
    """Reject a sample whose gold arguments contain values the user never gave (the teacher made them up)."""
    for c in calls:
        enums = [e for p in tools_by_name[c["name"]]["parameters"]["properties"].values() for e in p.get("enum", [])]
        for v in scalars(c["arguments"]):
            if not str(v).startswith("$") and not grounded(v, said, enums):
                raise ValueError(f"made-up value {v!r}")


def check_objective(objective, calls, said):
    text = (objective or "").lower()
    if len(text) < 10 or "$result" in text:
        raise ValueError("objective too short or has a reference")
    for c in calls:
        for v in scalars(c["arguments"]):
            if not str(v).startswith("$") and shown(v) in said and shown(v) not in text:
                raise ValueError(f"objective drops {v!r}")


def check_request(request, names):
    words = len((request or "").split())
    if words < 3 or words > 70:
        raise ValueError("request length")
    if any(n in request for n in names if "_" in n or n[:1].islower() and any(ch.isupper() for ch in n)):
        raise ValueError("request names a tool")


def tool_call(name, arguments):
    return "<tool_call>\n" + json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False) + "\n</tool_call>"


def plan(tools, split, n, seed):
    """Deterministic sample plans: target, needed tools, listed tools, whether to write earlier turns."""
    rng = random.Random(seed)
    pool = [t for t in tools if t["split"] == ("train" if split == "train" else "heldout")]
    by = collections.defaultdict(list)
    for t in pool:
        by[t["kind"]].append(t)
        by[t["kind"], t["domain"]].append(t)
        by[t["domain"]].append(t)
    quick = by["lookup"] + by["search"]
    plans = []
    for i in range(n):
        target = rng.choices(list(TARGETS), weights=list(TARGETS.values()))[0]
        p = {"id": f"{split}-{i:05d}", "split": split, "target": target, "context": rng.random() < 0.35}
        if target == "call":
            first = rng.choice(quick)
            need = [first]
            same = [t for t in by["lookup", first["domain"]] + by["search", first["domain"]] if t is not first]
            if same and rng.random() < 0.25:
                need.append(rng.choice(same))
        elif target == "handoff":
            act = rng.choice(by["action"])
            feeders = by["search", act["domain"]] + by["lookup", act["domain"]]
            need = [rng.choice(feeders), act] if feeders and rng.random() < 0.4 else [act]
        elif target == "ask":
            need = [rng.choice([t for t in pool if t["parameters"]["required"]])]
            p["missing"] = rng.choice(need[0]["parameters"]["required"])
        else:
            absent = rng.choice(pool)
            p["absent"] = absent["id"]
            need = []
        domain = need[0]["domain"] if need else rng.choice([d for d in {t["domain"] for t in pool} if d != absent["domain"]])
        k = rng.randint(2, 10)
        near = [t for t in by[domain] if t not in need and (target != "cannot" or t["domain"] != absent["domain"])]
        far = [t for t in pool if t not in need and t["domain"] != domain
               and (target != "cannot" or t["domain"] != absent["domain"])]
        listed = need + rng.sample(near, min(len(near), k // 2)) + rng.sample(far, k - min(len(near), k // 2))
        rng.shuffle(listed)
        p["need"], p["listed"] = [t["id"] for t in need], [t["id"] for t in listed]
        p["instructions"] = rng.randrange(len(INSTRUCTIONS))
        plans.append(p)
    return plans


def ask_teacher(url, model, text, temperature):
    body = {"model": model, "messages": [{"role": "user", "content": text}], "temperature": temperature,
            "max_tokens": 700, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0))


def build(p, out, tools, spawn, tools_prompt, tools_block):
    """Checked sample rows (one, or two for an ask with a follow-up) from a plan and the teacher's JSON."""
    by_name = {tools[i]["name"]: tools[i] for i in p["listed"]}
    listed = [tools[i] for i in p["listed"]]
    names = list(by_name)
    system = (S2T_PROMPT + "\n\n" + INSTRUCTIONS[p["instructions"]] + "\n\n"
              + tools_prompt.format(tools=tools_block([fn(t) for t in listed] + [spawn])))
    context = out.get("context") or []
    if not p["context"]:
        context = []
    if not isinstance(context, list) or any(not isinstance(c, dict) or not c.get("user") or not c.get("assistant")
                                            for c in context):
        raise ValueError("bad context")
    turns = [m for c in context for m in ({"role": "user", "content": c["user"]}, {"role": "assistant", "content": c["assistant"]})]
    request = out.get("request", "")
    check_request(request, names)
    said = " ".join([c["user"] for c in context] + [request]).lower()
    need = [tools[i]["name"] for i in p["need"]]
    base = {"split": p["split"], "system": system, "tools": names}
    if p["target"] == "call":
        check_calls(out.get("calls"), by_name, need)
        check_grounded(out["calls"], said, by_name)
        reply = "\n".join(tool_call(c["name"], c["arguments"]) for c in out["calls"])
        return [{**base, "id": p["id"], "target": "call", "context": turns, "request": request, "reply": reply,
                 "calls": out["calls"]}]
    if p["target"] == "handoff":
        check_calls(out.get("calls"), by_name, need)
        if [c["name"] for c in out["calls"]] != need:
            raise ValueError("calls differ from the plan")
        check_grounded(out["calls"], said, by_name)
        check_objective(out.get("objective"), out["calls"], said)
        return [{**base, "id": p["id"], "target": "handoff", "context": turns, "request": request,
                 "reply": tool_call("spawn_thinking", {"objective": out["objective"]}), "calls": out["calls"]}]
    if p["target"] == "ask":
        question, answer = out.get("question", ""), out.get("answer", "")
        if not question.strip().endswith("?") or len(question.split()) > 30:
            raise ValueError("question")
        q, r = set(re.findall(r"\w+", question.lower())), set(re.findall(r"\w+", request.lower()))
        if len(q & r) > 0.8 * len(q):
            raise ValueError("question copies the request")
        check_calls(out.get("calls"), by_name, need)
        if p["missing"] not in out["calls"][0]["arguments"]:
            raise ValueError("follow-up call lacks the missing value")
        if shown(out["calls"][0]["arguments"][p["missing"]]) in request.lower():
            raise ValueError("request already gives the missing value")
        rows = [{**base, "id": p["id"], "target": "ask", "context": turns, "request": request, "reply": question,
                 "missing": p["missing"]}]
        if int(p["id"][-5:]) % 2 == 0 and answer:  # half the ask plans also give a follow-up sample
            check_request(answer, names)
            check_grounded(out["calls"], said + " " + answer.lower(), by_name)
            kind = tools[p["need"][0]]["kind"]
            follow = turns + [{"role": "user", "content": request}, {"role": "assistant", "content": question}]
            if kind == "action":
                check_objective(out.get("objective"), out["calls"], said + " " + answer.lower())
                reply, target = tool_call("spawn_thinking", {"objective": out["objective"]}), "handoff"
            else:
                reply, target = tool_call(out["calls"][0]["name"], out["calls"][0]["arguments"]), "call"
            rows.append({**base, "id": p["id"] + "-f", "target": target, "context": follow, "request": answer,
                         "reply": reply, "calls": out["calls"], "followup": True})
        return rows
    reply = out.get("reply", "")
    if not reply or len(reply.split()) > 40:
        raise ValueError("reply")
    return [{**base, "id": p["id"], "target": "cannot", "context": turns, "request": request, "reply": reply}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--harness", type=Path, required=True)
    ap.add_argument("--chain-dir", type=Path, required=True, help="experiments/fdb-chain, for the frontend's tool prompt")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1")
    ap.add_argument("--model", default="qwen3-14b")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--limit", type=int, help="only the first N plans of each split (trial runs)")
    args = ap.parse_args()

    sys.path[:0] = [str(args.chain_dir), str(args.chain_dir.parent / "delegation-bench" / "runners")]
    from fdb_tools import tools_block
    from minicpmo_delegate_tool import TOOLS_PROMPT

    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    spawn = next(t for t in json.loads(args.harness.read_text(encoding="utf-8"))["tools"]
                 if t["function"]["name"] == "spawn_thinking")
    plans = []
    for k, (split, n) in enumerate(SIZES.items()):
        plans += plan(list(tools.values()), split, n, args.seed + k)[: args.limit]
    done = set()
    if args.out.exists():
        done = {json.loads(l)["plan"] for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [p for p in plans if p["id"] not in done]
    lock, failures = threading.Lock(), collections.Counter()

    def work(p):
        listed = "\n".join(json.dumps(fn(tools[i])["function"], ensure_ascii=False) for i in p["listed"])
        need = [tools[i]["name"] for i in p["need"]]
        if p["target"] == "call":
            task = TASKS["call"].format(need=" and ".join(need) + (" (two separate calls)" if len(need) > 1 else ""))
        elif p["target"] == "handoff":
            task = (TASKS["handoff_chain"].format(first=need[0], second=need[1]) if len(need) > 1
                    else TASKS["handoff_single"].format(tool=need[0]))
        elif p["target"] == "ask":
            task = TASKS["ask"].format(tool=need[0], missing=p["missing"])
        else:
            absent = tools[p["absent"]]
            task = TASKS["cannot"].format(absent=json.dumps({"name": absent["name"], "description": absent["description"]}))
        prompt = TEACHER.format(tools=listed, task=task, context_rule=CONTEXT_RULES[p["context"]])
        for attempt in range(3):
            try:
                rows = build(p, ask_teacher(args.url, args.model, prompt, 0.8 + 0.1 * attempt), tools, spawn,
                             TOOLS_PROMPT, tools_block)
                break
            except Exception as e:  # unreadable JSON or a failed check: try again, then give up on this plan
                reason = re.sub(r"[0-9'\"].*", "", str(e))[:40] or type(e).__name__
        else:
            with lock:
                failures[reason] += 1
            return
        with lock, args.out.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({"plan": p["id"], **row}, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, todo))
    print(f"plans {len(todo)}, failed {sum(failures.values())}: {dict(failures.most_common(8))}")


if __name__ == "__main__":
    main()
