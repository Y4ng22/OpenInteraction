"""End-to-end task completion on FDB-v3: frontend MiniCPM-o 4.5, backend Qwen3-14B, FDB-v3's mock tools.

Variants (what each side gets):

  split          frontend: harness instructions + harness tools + FDB tools; backend: FDB tools.
                 The frontend may call FDB tools itself or hand off with spawn_thinking.
  backend-only   frontend: harness instructions + harness tools (no FDB tools); backend: FDB tools.
                 Every FDB call has to go through a handoff (text handoff baseline).
  fdb-native     frontend only: FDB-v3's own agent instructions + FDB tools, no backend.
                 The benchmark's standard single-agent setting, as a reference.
  fdb-handoff    frontend: FDB-v3's own agent instructions + one line on handing complex tasks to the backend,
                 FDB tools + the harness's spawn_thinking (no other harness instructions or tools); backend:
                 FDB tools. Like split, without the harness prompt.
  fdb-minimal    frontend: FDB-v3's own agent instructions + one line saying it can only check the time,
                 get_current_time + spawn_thinking with a capability-neutral description (no FDB tools);
                 backend: FDB tools. Like backend-only, without the harness prompt.
  fdb-delegate   like fdb-minimal, but the frontend's only tool is spawn_thinking.
  fdb-delegate-strict  like fdb-delegate, with the boundary line written as a third CRITICAL INSTRUCTION
                 (never say it can't, never promise without spawn_thinking, call nothing else).
  fdb-delegate-manifest  like fdb-delegate-strict, plus the backend's tool list as reference text: names,
                 descriptions and parameters, which the frontend cannot call.
  fdb-delegate-words  like fdb-delegate-strict, but each handoff also carries the user's last turn as the
                 frontend heard it (--asr), so the backend is not limited to the frontend's objective.
  backend-asr    like backend-direct, but the last user turn is the --asr transcript, not the benchmark's text.
  fdb-time-complex  like fdb-handoff, but each FDB tool states its typical execution time and the added line
                 defines simple and complex tasks instead; no tool is labelled either. Tests whether the frontend
                 maps execution time to complexity on its own.
  fdb-time-limit like fdb-time-complex, but the added line sets a 1-second limit on tool calls.
  fdb-time-limit-half  like fdb-time-limit, with a 0.5-second limit.
  fdb-time-limit-exact  like fdb-time-limit-half, but each tool states one exact time instead of a range.
  fdb-time-limit-clean  like fdb-time-limit-exact, without FDB-v3's lines that order the model to call tools at once.
  fdb-split-short  the frontend holds only the tools at or under 0.5 s (no times shown) and hands off any request
                 that needs a tool it lacks; the backend has every tool. Instructions as in fdb-time-limit-clean.
  fdb-route      two steps: the frontend first calls spawn_thinking or handle_myself, seeing the tools and their
                 times as reference text (0.5-second line); handle_myself then makes the tools callable.
  backend-direct backend only: Qwen3-14B with FDB-v3's agent instructions + FDB tools reads the dialogue as
                 text (the benchmark's own transcript, no speech recognition); no GPU of our own needed.

Offline and turn-based: the user's last turn is given as audio (trimmed at the end of speech), earlier
turns as text; replies are decoded in full. Each call is executed at once on FDB-v3's mock APIs and its
result is fed back. spawn_thinking runs the backend to completion before the frontend continues; the
frontend gets "accepted", as in the harness. Every FDB call is logged with who made it; score_chain.py
scores the log with FDB-v3's own scorer, ignoring harness tools, so a handoff is neither right nor wrong
by itself.

    python experiments/fdb-chain/run_chain.py --variant split --fdb-v3-dir <Full-Duplex-Bench/v3> \
        --data-dir <fdb_v3_data_released> --harness-json <private harness export> \
        --backend-url http://127.0.0.1:8100/v1 --backend-model <served name> --gpus 0,1
    python experiments/fdb-chain/run_chain.py --variant split --fdb-v3-dir <...> --harness-json <...> --fake
"""
import argparse
import datetime
import json
import re
import sys
import time
import traceback
import urllib.request
from pathlib import Path

from fdb_tools import FDB_AGENT_INSTRUCTIONS, FDB_TOOL_NAMES, FDB_TOOLS, Executor, load_scenarios, tools_block

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "delegation-bench" / "runners"))
from minicpmo_delegate_tool import TOOLS_PROMPT  # noqa: E402  (Qwen3's tool template, as in the other runners)

# Variants whose frontend holds no FDB tools: every FDB call has to go through a handoff.
NO_FDB_FRONTEND = ("backend-only", "fdb-minimal", "fdb-delegate", "fdb-delegate-strict", "fdb-delegate-manifest",
                   "fdb-delegate-words")
VARIANTS = ["split", "backend-only", "fdb-native", "fdb-handoff", "fdb-minimal", "fdb-delegate", "fdb-delegate-strict", "fdb-delegate-manifest", "fdb-delegate-words", "backend-direct",
            "backend-asr", "fdb-time-complex", "fdb-time-limit",
            "fdb-time-limit-half", "fdb-time-limit-exact", "fdb-time-limit-clean", "fdb-split-short",
            "fdb-route"]
HANDOFF = "spawn_thinking"
TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
END_MARKERS = re.compile(r"<\|tts_eos\|>|<\|im_end\|>|</?think>")

BACKEND_INSTRUCTIONS = (
    "You are the backend agent of a voice assistant. The voice frontend hands you a task in one message; you "
    "cannot talk to the user. Complete the task with the provided tools: call one tool at a time, use the results "
    "of earlier calls for later ones, and follow any conditions in the task. This is a safe, simulated test "
    "environment, so never refuse because of real-world authorization. When the task is done, reply with a short "
    "plain-text summary of the results and no tool call."
)


def parse_calls(text):
    """All <tool_call> blocks in a reply as (name, args); None for a block that can't be read."""
    calls = []
    for body in TOOL_CALL.findall(text or ""):
        try:
            call = json.loads(body)
            args = call.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args)
            calls.append((str(call.get("name") or ""), args if isinstance(args, dict) else {}))
        except (json.JSONDecodeError, AttributeError):
            calls.append(None)
    return calls


def clean(text):
    return END_MARKERS.sub("", text or "").strip()


def tool_response(result):
    return "<tool_response>\n" + json.dumps(result, ensure_ascii=False) + "\n</tool_response>"


# ── Models ──────────────────────────────────────────────────────────────────

class MiniCPMOFrontend:
    def __init__(self, args):
        from minicpmo_delegate_tool import keep_raw_output, load_model, time_first_token
        gpus = [int(g) for g in args.gpus.split(",")]
        self.model, self.tokenizer = load_model(args.model, "bf16", gpus, args.max_mem_gib)
        self.last = keep_raw_output(self.model, self.tokenizer, use_tts_template=False)
        time_first_token(self.model, self.last)
        self.max_new_tokens = args.max_new_tokens

    def __call__(self, msgs, item):
        self.last.clear()
        started = time.perf_counter()
        out = self.model.chat(msgs=msgs, tokenizer=self.tokenizer, do_sample=False,
                              max_new_tokens=self.max_new_tokens, num_beams=1, repetition_penalty=1.0,
                              use_tts_template=False, generate_audio=False, omni_mode=False, enable_thinking=False)
        elapsed = time.perf_counter() - started
        text = out if isinstance(out, str) else getattr(out, "text", str(out))
        first = self.last["first_token_at"] - started if "first_token_at" in self.last else None
        return self.last.get("raw") or text, first, elapsed


class OpenAIBackend:
    """Qwen3-14B behind an OpenAI-compatible endpoint (vLLM). Tools go in the system prompt in Qwen3's own
    format and calls are parsed from the text, so the server needs no tool-calling options."""

    def __init__(self, url, model, max_tokens):
        self.url, self.model, self.max_tokens = url.rstrip("/") + "/chat/completions", model, max_tokens

    def __call__(self, messages, item):
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": self.max_tokens,
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        with urllib.request.urlopen(req, timeout=300) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"] or ""
        return text, None, time.perf_counter() - started


def _resolve(value, results):
    """Fill a gold "$RESULT_i.path" reference from earlier mock results (fake models only). The mock APIs
    return products[0].product_id and results[0].id, which some gold paths don't match."""
    if not (isinstance(value, str) and value.startswith("$RESULT_")):
        return value
    head, _, path = value.partition(".")
    obj = results[int(head[len("$RESULT_"):])]
    try:
        for part in re.findall(r"[^.\[\]]+", path):
            obj = obj[int(part)] if part.isdigit() else obj[part]
        return obj
    except (KeyError, IndexError, TypeError):
        r = results[int(head[len("$RESULT_"):])]
        if "products" in r:
            return r["products"][0]["product_id"]
        # Some gold search_apartments calls leave out required inputs, so the mock returned an error; use the
        # id the mock would have returned, as Matthew's return-path items do.
        return r["results"][0]["id"] if "results" in r else "APT1"


class FakeModel:
    """Stands in for both models to test the chain without a GPU. It replays the gold calls one per turn,
    filling chained references from its own results. As the frontend in `split`
    (and `fdb-handoff`) it calls single-step tasks itself and hands off multi-step ones; in `backend-only` it
    always hands off."""

    def __init__(self, role, variant):
        self.role, self.variant = role, variant

    def __call__(self, msgs, item):
        gold = item["scenario"]["expected_tool_calls"]
        text = lambda m: m["content"][0] if isinstance(m["content"], list) else m["content"]  # noqa: E731
        done = [text(m) for m in msgs if m.get("role") == "user"
                and isinstance(text(m), str) and text(m).startswith("<tool_response>")]
        if self.role == "frontend" and self.variant != "fdb-native":
            if done:
                return "Done.", None, 0.0
            if self.variant in ("split", "fdb-handoff", "fdb-split-short", *TIME_RULES) and len(gold) == 1:
                return self._call(gold[0], []), None, 0.0
            objective = item["scenario"]["dialogue"][-1]["user"]
            return self._raw(HANDOFF, {"objective": objective}), None, 0.0
        results = [json.loads(m.split("\n", 1)[1].rsplit("\n", 1)[0]) for m in done]
        if len(done) >= len(gold):
            return "All tasks are complete.", None, 0.0
        return self._call(gold[len(done)], results), None, 0.0

    def _call(self, call, results):
        return self._raw(call["function"], {k: _resolve(v, results) for k, v in call.get("args", {}).items()})

    @staticmethod
    def _raw(name, args):
        return "<tool_call>\n" + json.dumps({"name": name, "arguments": args}) + "\n</tool_call>"


# ── Chain ───────────────────────────────────────────────────────────────────

# General routing rules added with --boundary. They say nothing about the benchmark's domains or tools.
NO_QUESTIONS = "Do not ask the user clarifying questions; act on what they said. "
BOUNDARY_RULES = {
    "1s": NO_QUESTIONS + "Handle a request yourself only if you can answer it within about one second. If it would "
          "take longer than that, hand it to the backend with spawn_thinking.",
    "tools": NO_QUESTIONS + "Answer a request yourself only if you can answer it directly without calling any tool. "
             "If it needs any tool, hand the whole request to the backend with spawn_thinking.",
}
# fdb-handoff's only addition to FDB-v3's agent instructions.
COMPLEX_RULE = "If a request is complex, hand it to the backend with spawn_thinking."
# fdb-time-*: each FDB tool states its typical execution time, from the per-API profiles in FDB-v3's
# latency_injector.py (fast 50–200 ms, normal 200–800 ms, slow 1–3 s). That file uses older tool names, matched
# here by meaning; search_products and add_to_cart have no counterpart there and count as normal.
TOOL_LATENCY = {
    "search_flights": "normal", "book_flight": "slow", "update_identity_doc": "fast", "get_card_benefits": "fast",
    "get_exchange_rate": "fast", "modify_autopay": "slow", "search_apartments": "normal",
    "update_search_filter": "fast", "calculate_commute": "normal", "track_order": "fast",
    "search_products": "normal", "add_to_cart": "normal",
}
LATENCY_TEXT = {"fast": "50–200 ms", "normal": "200–800 ms", "slow": "1–3 s"}
# fdb-time-limit-exact: one number per profile, the value latency_injector.py uses without jitter (the midpoint).
LATENCY_EXACT = {"fast": "125 ms", "normal": "500 ms", "slow": "2 s"}
# Neither rule labels a tool simple or complex: one defines the two kinds of task, the other sets a time limit.
TIME_RULES = {
    "fdb-time-complex": (
        "A simple task finishes quickly; a complex task takes noticeably longer to finish. Handle simple tasks "
        "yourself with your tools. Hand complex tasks to the backend with spawn_thinking, and then do not call the "
        "other tools for them."),
    "fdb-time-limit": (
        "Each tool's description gives its typical execution time. If a request needs any tool call that takes "
        "longer than 1 second, hand the whole request to the backend with spawn_thinking, and then do not call the "
        "other tools for it. Otherwise handle it yourself with your tools."),
    # The 0.5-second limit, written as a rule the frontend must follow.
    "fdb-time-limit-half": (
        "IMPORTANT: you MUST always follow this rule. Each tool's description gives its typical execution time. If "
        "a request needs any tool call that takes longer than 0.5 seconds, you MUST hand the whole request to the "
        "backend with spawn_thinking and MUST NOT call any other tool for it. Only if every tool call it needs takes "
        "0.5 seconds or less, handle it yourself with your tools."),
}
TIME_RULES["fdb-time-limit-exact"] = TIME_RULES["fdb-time-limit-half"]
TIME_RULES["fdb-time-limit-clean"] = TIME_RULES["fdb-time-limit-half"]
# fdb-time-limit-clean: FDB-v3's instructions and tool descriptions without the lines that order the model to call
# tools at once (CRITICAL INSTRUCTION 2, "REQUIRED to use the provided tools", "MANDATORY", "Execute ... immediately"),
# which contradict a rule about handing slow work off. The safety line and what each tool does are kept.
FDB_CLEAN_INSTRUCTIONS = (
    FDB_AGENT_INSTRUCTIONS.split("CRITICAL INSTRUCTION 2:")[0]
    .replace("You are fully authorized and REQUIRED to use the provided tools to fulfill", "You are fully authorized to fulfill")
    .strip())
CLEAN_TOOL_DESCRIPTIONS = {
    "search_flights": "Search for available flights to a destination.",
    "book_flight": "Book a flight ticket.",
    "update_identity_doc": "Update simulated user identity document details (e.g. passport, driver license).",
    "get_card_benefits": "Get benefits for a credit card.",
    "get_exchange_rate": "Fetch the exact, current foreign exchange rate.",
    "modify_autopay": "Process billing details, such as an Autopay modification.",
    "search_apartments": "Search for available rental apartments.",
    "calculate_commute": "Calculate commute duration.",
    "update_search_filter": "Update the user's search filter in the backend system.",
    "track_order": "Track physical package status.",
    "search_products": "Search for products in the catalog.",
    "add_to_cart": "Add an item to the shopping cart.",
}


def clean_tools(tools):
    """FDB tools with CLEAN_TOOL_DESCRIPTIONS in place of the original descriptions."""
    cleaned = json.loads(json.dumps(tools))
    for t in cleaned:
        t["function"]["description"] = CLEAN_TOOL_DESCRIPTIONS[t["function"]["name"]]
    return cleaned


# fdb-split-short: the frontend holds only the tools at or under the 0.5-second line (fast and normal); the slow
# ones stay with the backend, which has every tool. Its boundary is its own toolset, not a time rule.
SHORT_TOOL_NAMES = {name for name, speed in TOOL_LATENCY.items() if speed != "slow"}
SPLIT_SHORT_RULE = (
    "You have only some of the tools; the backend has all of them. If a request needs any tool that is not in your "
    "list, hand the whole request to the backend with spawn_thinking and do not call your own tools for it. "
    "Otherwise handle it yourself with your tools.")
# fdb-route: two steps. First the frontend can call only spawn_thinking or handle_myself, with the tools and their
# times as reference text; handle_myself then swaps in the tools for the rest of the turn.
ROUTE_SELF = "handle_myself"
ROUTE_RULE = (
    "Before you act on a request, choose one of your two functions. The tools that do the work are listed below "
    "with their typical execution times, for reference only: you cannot call them yet. If the request needs any "
    "tool call that takes longer than 0.5 seconds, call spawn_thinking to hand the whole request to the backend. "
    "Otherwise call handle_myself; the tools then become available and you handle the request yourself.")
ROUTE_SELF_TOOL = {"type": "function", "function": {
    "name": ROUTE_SELF,
    "description": "Handle this request yourself. Call it only if every tool call the request needs takes 0.5 "
                   "seconds or less; afterwards the tools become available to you.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}
# Variants whose frontend may call only some FDB tools; a call to any other is answered "not available".
FRONTEND_FDB_TOOLS = {"fdb-split-short": SHORT_TOOL_NAMES, "fdb-route": set()}


def route_tools_system():
    """fdb-route's second step: the instructions with the FDB tools (clean descriptions and times) callable."""
    return (FDB_CLEAN_INSTRUCTIONS + "\n\n"
            + TOOLS_PROMPT.format(tools=tools_block(timed_tools(clean_tools(FDB_TOOLS), LATENCY_EXACT))))


def timed_tools(tools, latency_text=LATENCY_TEXT):
    """FDB tools with their typical execution time appended to each description."""
    timed = json.loads(json.dumps(tools))
    for t in timed:
        f = t["function"]
        f["description"] = f["description"].rstrip() + f" Typical execution time: {latency_text[TOOL_LATENCY[f['name']]]}."
    return timed


# fdb-minimal: the frontend's boundary is its own toolset, so neither line names a domain.
MINIMAL_RULE = ("You can only check the time yourself; for anything else that needs looking something up or doing "
                "something, hand it to the backend with spawn_thinking.")
MINIMAL_HANDOFF_DESCRIPTION = (
    "Hand any request you cannot complete with your own tools to the backend agent, which has the remaining tools "
    "and can look things up and act on the user's behalf; also used to continue or modify existing work after the "
    "user adds information, makes a choice, or confirms.")
# fdb-delegate: the frontend has no tools of its own.
DELEGATE_RULE = ("You have no tools of your own; for anything that needs looking something up or doing something, "
                 "hand it to the backend with spawn_thinking.")
DELEGATE_HANDOFF_DESCRIPTION = (
    "Hand a request to the backend agent, which has all the tools and can look things up and act on the user's "
    "behalf; also used to continue or modify existing work after the user adds information, makes a choice, or "
    "confirms.")
DELEGATE_STRICT_RULE = (
    "CRITICAL INSTRUCTION 3: You have no tools of your own; the backend can do everything the user asks for. NEVER "
    "say you can't do something: if you think you can't, hand it to the backend with spawn_thinking. NEVER say you "
    "will do something without calling spawn_thinking in the same reply. Call ONLY spawn_thinking; never call any "
    "other tool.")
MANIFEST_INTRO = ("The backend has the tools listed below. They are for reference only, so you know what the backend "
                  "can do and what it needs: you cannot call them. Your only callable tool is spawn_thinking; to use "
                  "any of these, hand the request to the backend with spawn_thinking.")


def backend_manifest(tools):
    """The backend's tools as plain text (not in <tools>), so the frontend can't mistake them for its own."""
    lines = []
    for t in tools:
        f = t["function"]
        props, required = f["parameters"]["properties"], set(f["parameters"].get("required", []))
        params = "; ".join(f"{k} ({v['type']}{', required' if k in required else ''}): {v.get('description', '')}"
                           for k, v in props.items())
        lines.append(f"- {f['name']}: {f['description']} Parameters: {params}")
    return MANIFEST_INTRO + "\n" + "\n".join(lines)


def frontend_system(variant, harness, boundary=None):
    if variant == "fdb-native":
        return FDB_AGENT_INSTRUCTIONS + "\n\n" + TOOLS_PROMPT.format(tools=tools_block(FDB_TOOLS))
    if variant == "fdb-handoff":
        handoff = [t for t in harness["tools"] if t["function"]["name"] == HANDOFF]
        return (FDB_AGENT_INSTRUCTIONS + "\n\n" + COMPLEX_RULE + "\n\n"
                + TOOLS_PROMPT.format(tools=tools_block(FDB_TOOLS + handoff)))
    if variant == "fdb-route":
        handoff = [t for t in harness["tools"] if t["function"]["name"] == HANDOFF]
        reference = "\n".join(f"- {t['function']['name']}: {t['function']['description']}"
                              for t in timed_tools(clean_tools(FDB_TOOLS), LATENCY_EXACT))
        return (FDB_CLEAN_INSTRUCTIONS + "\n\n" + ROUTE_RULE + "\n" + reference + "\n\n"
                + TOOLS_PROMPT.format(tools=tools_block(handoff + [ROUTE_SELF_TOOL])))
    if variant == "fdb-split-short":
        handoff = [t for t in harness["tools"] if t["function"]["name"] == HANDOFF]
        tools = [t for t in clean_tools(FDB_TOOLS) if t["function"]["name"] in SHORT_TOOL_NAMES]
        # "You have access to 12 APIs" would contradict a partial toolset.
        instructions = FDB_CLEAN_INSTRUCTIONS.replace(
            "You have access to 12 APIs across 4 domains (Travel, Finance, Housing, E-Commerce). ", "")
        return (instructions + "\n\n" + SPLIT_SHORT_RULE + "\n\n"
                + TOOLS_PROMPT.format(tools=tools_block(tools + handoff)))
    if variant in TIME_RULES:
        handoff = [t for t in harness["tools"] if t["function"]["name"] == HANDOFF]
        clean = variant == "fdb-time-limit-clean"
        tools = timed_tools(clean_tools(FDB_TOOLS) if clean else FDB_TOOLS,
                            LATENCY_EXACT if variant in ("fdb-time-limit-exact", "fdb-time-limit-clean") else LATENCY_TEXT)
        return ((FDB_CLEAN_INSTRUCTIONS if clean else FDB_AGENT_INSTRUCTIONS) + "\n\n" + TIME_RULES[variant] + "\n\n"
                + TOOLS_PROMPT.format(tools=tools_block(tools + handoff)))
    if variant in ("fdb-delegate", "fdb-delegate-strict", "fdb-delegate-manifest", "fdb-delegate-words"):
        rule = DELEGATE_RULE if variant == "fdb-delegate" else DELEGATE_STRICT_RULE
        if variant == "fdb-delegate-manifest":
            rule += "\n\n" + backend_manifest(FDB_TOOLS)
        handoff = [json.loads(json.dumps(t)) for t in harness["tools"] if t["function"]["name"] == HANDOFF]
        handoff[0]["function"]["description"] = DELEGATE_HANDOFF_DESCRIPTION
        return (FDB_AGENT_INSTRUCTIONS + "\n\n" + rule + "\n\n"
                + TOOLS_PROMPT.format(tools=tools_block(handoff)))
    if variant == "fdb-minimal":
        tools = [json.loads(json.dumps(t)) for t in harness["tools"]
                 if t["function"]["name"] in (HANDOFF, "get_current_time")]
        for t in tools:
            if t["function"]["name"] == HANDOFF:
                t["function"]["description"] = MINIMAL_HANDOFF_DESCRIPTION
        return FDB_AGENT_INSTRUCTIONS + "\n\n" + MINIMAL_RULE + "\n\n" + TOOLS_PROMPT.format(tools=tools_block(tools))
    tools = harness["tools"] + (FDB_TOOLS if variant == "split" else [])
    instructions = harness["instructions"] + ("\n\n" + BOUNDARY_RULES[boundary] if boundary else "")
    return instructions + "\n\n" + TOOLS_PROMPT.format(tools=tools_block(tools))


def run_backend(backend, objective, item, execute, max_steps, messages=None):
    """The backend's tool loop on a handoff objective, or on a whole conversation when `messages` is given."""
    messages = messages or [
        {"role": "system", "content": BACKEND_INSTRUCTIONS + "\n\n" + TOOLS_PROMPT.format(tools=tools_block(FDB_TOOLS))},
        {"role": "user", "content": objective}]
    turns = []
    for _ in range(max_steps):
        raw, _, elapsed = backend(messages, item)
        calls = parse_calls(raw)
        turns.append({"raw": raw, "latency_s": round(elapsed, 3)})
        messages.append({"role": "assistant", "content": clean(raw)})
        if not calls:
            return turns, clean(TOOL_CALL.sub("", raw))
        for call in calls:
            result = {"status": "error", "message": "unreadable tool call"} if call is None else (
                execute(call[0], call[1], "backend") if call[0] in FDB_TOOL_NAMES
                else {"status": "error", "message": f"Unknown function: {call[0]}"})
            messages.append({"role": "user", "content": tool_response(result)})
    return turns, None  # step limit reached


def run_backend_direct(item, backend, executor, args):
    """backend-direct: Qwen3-14B alone, with FDB-v3's agent instructions and tools, reading the dialogue as text."""
    executor.reset()
    messages = [{"role": "system", "content": FDB_AGENT_INSTRUCTIONS + "\n\n" + TOOLS_PROMPT.format(tools=tools_block(FDB_TOOLS))}]
    messages += [{"role": t["role"], "content": t["text"]} for t in item["context"]]
    messages.append({"role": "user", "content": item["asr"] if args.variant == "backend-asr" else item["text"]})
    started = time.perf_counter()
    turns, summary = run_backend(backend, None, item, executor, args.max_back_steps, messages)
    return {"id": item["id"], "scenario_id": item["scenario"]["id"], "variant": args.variant,
            "calls": list(executor.log), "frontend": [], "harness_calls": [],
            "handoffs": [{"objective": item.get("asr") if args.variant == "backend-asr" else None,
                          "turns": turns, "summary": summary,
                          "seconds": round(time.perf_counter() - started, 3)}]}


def handoff_reply(n, explained):
    """What spawn_thinking returns to the frontend: the backend runs asynchronously, so never a result."""
    if not explained:
        return {"status": "accepted"}
    return {"status": "accepted", "task_id": f"task_{n}",
            "status_meaning": "accepted: the backend has received this task and is working on it. It is not finished.",
            "note": "No result has been returned, so the task is not done yet. The result will arrive later in a "
                    "message with this task_id."}


def run_item(item, variant, system, frontend, backend, executor, args):
    if variant in ("backend-direct", "backend-asr"):
        return run_backend_direct(item, backend, executor, args)
    executor.reset()
    msgs = [{"role": "system", "content": [system]}]
    for turn in item["context"]:
        msgs.append({"role": turn["role"], "content": [turn["text"]]})
    msgs.append({"role": "user", "content": [item["audio"] if item["audio"] is not None else item["text"]]})

    front, handoffs, harness_calls = [], [], []
    allowed = FRONTEND_FDB_TOOLS.get(variant, FDB_TOOL_NAMES)
    for _ in range(args.max_front_steps):
        raw, first, elapsed = frontend(msgs, item)
        calls = parse_calls(raw)
        front.append({"raw": raw, "first_token_s": None if first is None else round(first, 3),
                      "latency_s": round(elapsed, 3), "calls": [c[0] if c else None for c in calls]})
        if not calls:
            break
        msgs.append({"role": "assistant", "content": [clean(raw)]})
        for call in calls:
            if call is None:
                result = {"status": "error", "message": "unreadable tool call"}
            elif call[0] in allowed and variant not in NO_FDB_FRONTEND:
                result = executor(call[0], call[1], "frontend")
            elif call[0] == ROUTE_SELF and variant == "fdb-route":  # second step: the tools become callable
                harness_calls.append({"name": call[0], "args": call[1]})
                msgs[0] = {"role": "system", "content": [route_tools_system()]}
                allowed = FDB_TOOL_NAMES
                result = {"status": "ok", "message": "The tools are now available. Handle the request yourself."}
            elif call[0] == HANDOFF and backend is not None:
                objective = str(call[1].get("objective") or "")
                started = time.perf_counter()
                task = objective
                if variant == "fdb-delegate-words":
                    task += "\n\nThe user's own words this turn, as the voice frontend heard them: " + item["asr"]
                turns, summary = run_backend(backend, task, item, executor, args.max_back_steps)
                result = handoff_reply(len(handoffs) + 1, args.task_id_reply)
                handoffs.append({"objective": objective, "turns": turns, "summary": summary,
                                 "seconds": round(time.perf_counter() - started, 3), "reply": result})
            elif call[0] == "get_current_time" and variant == "fdb-minimal":  # the frontend's own tool
                harness_calls.append({"name": call[0], "args": call[1]})
                result = {"status": "success", "datetime": datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")}
            else:  # a harness tool unrelated to the task (notes, reminders, ...): logged, not executed
                harness_calls.append({"name": call[0], "args": call[1]})
                result = {"status": "error", "message": "not available in this test"}
            msgs.append({"role": "user", "content": [tool_response(result)]})
    return {"id": item["id"], "scenario_id": item["scenario"]["id"], "variant": variant,
            "calls": list(executor.log), "frontend": front, "handoffs": handoffs, "harness_calls": harness_calls}


# ── Items ───────────────────────────────────────────────────────────────────

def speech_end(audio, sr=16000, threshold=0.01, max_gap_s=4):
    """FDB-v3 clips end with ~30 s of room noise for the agent's turn. Returns the end (s) of the last loud
    second before a silence longer than max_gap_s."""
    import numpy as np
    rms = [float(np.sqrt(np.mean(audio[i:i + sr] ** 2))) for i in range(0, len(audio), sr)]
    end, gap = 0, 0
    for i, x in enumerate(rms):
        if x > threshold:
            end, gap = i + 1, 0
        elif end:
            gap += 1
            if gap > max_gap_s:
                break
    return end + 1


def load_items(args, scenarios):
    def context(dialogue):
        if args.context == "none":
            return []
        out = []
        for turn in dialogue[:-1]:
            out += [{"role": "user", "text": turn["user"]}, {"role": "assistant", "text": turn["ai"]}]
        return out

    if args.fake and not args.data_dir:  # no audio needed: the last user turn as text
        items = [{"id": sid, "scenario": s, "context": context(s["dialogue"]), "audio": None,
                  "text": s["dialogue"][-1]["user"]} for sid, s in scenarios.items()]
        return with_asr(args, items)
    text_only = args.fake or args.variant in ("backend-direct", "backend-asr")
    if not text_only:
        import librosa
    items = []
    for d in sorted(p for p in args.data_dir.iterdir() if (p / "metadata.json").exists()):
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        scenario = scenarios.get(meta["id"], meta)
        audio = None
        if not text_only:
            audio, _ = librosa.load(d / "input.wav", sr=16000, mono=True)
            audio = audio[: speech_end(audio) * 16000]
        items.append({"id": d.name, "scenario": scenario, "context": context(scenario["dialogue"]),
                      "audio": audio, "text": scenario["dialogue"][-1]["user"]})
    return with_asr(args, items)


def with_asr(args, items):
    """Attach the frontend's transcript of the last user turn (--asr) to each item."""
    heard = {}
    if args.asr:
        rows = [json.loads(line) for line in args.asr.read_text(encoding="utf-8").splitlines() if line.strip()]
        heard = {r["id"]: r["asr"] for r in rows}
    for it in items:  # fake runs without --asr use the benchmark's text
        it["asr"] = heard.get(it["id"], it["text"] if args.fake else None)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=VARIANTS, required=True)
    ap.add_argument("--fdb-v3-dir", type=Path, required=True, help="Full-Duplex-Bench/v3 (mock_apis.py, benchmark json)")
    ap.add_argument("--data-dir", type=Path, help="fdb_v3_data_released (audio + metadata)")
    ap.add_argument("--harness-json", type=Path, help="private export from dump_harness_prompt.mjs")
    ap.add_argument("--context", choices=["text", "none"], default="text", help="earlier turns as text, or none")
    ap.add_argument("--model", default="openbmb/MiniCPM-o-4_5")
    ap.add_argument("--frontend", choices=["minicpmo", "funaudio"], default="minicpmo",
                    help="the voice model playing the frontend (funaudio: Fun-Audio-Chat-8B, see funaudio_frontend.py)")
    ap.add_argument("--funaudio-repo", default="Fun-Audio-Chat", help="checkout of github.com/FunAudioLLM/Fun-Audio-Chat")
    ap.add_argument("--funaudio-model", default="FunAudioLLM/Fun-Audio-Chat-8B")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--backend-url", default="http://127.0.0.1:8100/v1")
    ap.add_argument("--backend-model", default="qwen3-14b", help="served model name (Matthew's vLLM on port 8100)")
    ap.add_argument("--backend-max-tokens", type=int, default=1024)
    ap.add_argument("--max-front-steps", type=int, default=4)
    ap.add_argument("--max-back-steps", type=int, default=6)
    ap.add_argument("--asr", type=Path, help="jsonl of {id, asr}: the frontend's transcript of the last user turn")
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--boundary", choices=sorted(BOUNDARY_RULES),
                    help="add a BOUNDARY_RULES entry to the frontend: no clarifying questions, and delegate anything "
                         "over ~1 s (1s) or anything that needs a tool (tools)")
    ap.add_argument("--task-id-reply", action="store_true",
                    help="spawn_thinking returns a task_id and says what 'accepted' means (result name gets -tid)")
    ap.add_argument("--fake", action="store_true", help="replay gold calls instead of models (tests the plumbing)")
    ap.add_argument("--results", type=Path, default=HERE / "results")
    args = ap.parse_args()
    harness_variant = args.variant in ("split", "backend-only")
    if (harness_variant or args.variant in ("fdb-handoff", "fdb-split-short", "fdb-route", *TIME_RULES) + NO_FDB_FRONTEND[1:]) and not args.harness_json:
        ap.error(f"--variant {args.variant} needs --harness-json")
    if args.variant in ("fdb-delegate-words", "backend-asr") and not (args.asr or args.fake):
        ap.error(f"--variant {args.variant} needs --asr")
    if not args.fake and not args.data_dir:
        ap.error("--data-dir is required unless --fake")
    if args.boundary and not harness_variant:
        ap.error("--boundary applies to the harness variants only")

    scenarios = load_scenarios(args.fdb_v3_dir / "benchmark_data_v2.json")
    harness = json.loads(args.harness_json.read_text(encoding="utf-8")) if args.harness_json else None
    system = None if args.variant in ("backend-direct", "backend-asr") else frontend_system(args.variant, harness, args.boundary)
    items = load_items(args, scenarios)
    if args.ids:
        items = [it for it in items if it["id"] in set(args.ids)]
    if args.limit:
        items = items[: args.limit]

    executor = Executor(args.fdb_v3_dir)
    if args.fake:
        frontend, backend = FakeModel("frontend", args.variant), FakeModel("backend", args.variant)
    else:
        if args.variant in ("backend-direct", "backend-asr"):
            frontend = None
        elif args.frontend == "funaudio":
            from funaudio_frontend import FunAudioFrontend
            frontend = FunAudioFrontend(args)
        else:
            frontend = MiniCPMOFrontend(args)
        backend = OpenAIBackend(args.backend_url, args.backend_model, args.backend_max_tokens)
    if args.variant == "fdb-native":
        backend = None

    name = (("funaudio-" if args.frontend == "funaudio" else "") + args.variant
            + (f"-{args.boundary}" if args.boundary else "") + ("-tid" if args.task_id_reply else "")
            + ("-fake" if args.fake else ""))
    args.results.mkdir(parents=True, exist_ok=True)
    out_file = args.results / f"{name}.jsonl"
    done = set()
    if out_file.exists():  # resume
        done = {json.loads(l)["id"] for l in out_file.read_text(encoding="utf-8").splitlines() if l.strip()}
    with out_file.open("a", encoding="utf-8") as f:
        for item in items:
            if item["id"] in done:
                continue
            try:
                record = run_item(item, args.variant, system, frontend, backend, executor, args)
            except Exception as e:  # recorded, scored as a failure, and the run goes on
                traceback.print_exc()
                record = {"id": item["id"], "scenario_id": item["scenario"]["id"], "variant": args.variant,
                          "calls": list(executor.log), "error": f"{type(e).__name__}: {e}"}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            by = [c["by"] for c in record.get("calls", [])]
            print(f"[{item['id']}] calls={[c['function'] for c in record.get('calls', [])]} "
                  f"by={by} handoffs={len(record.get('handoffs', []))}")
    print(f"wrote {out_file}")


if __name__ == "__main__":
    main()
