"""Group 1: can MiniCPM-o 4.5 decide by itself when to hand a request to the backend?

The model receives tool definitions in Qwen3's `<tools>` / `<tool_call>` format (the format in
MiniCPM-o's chat template) and either answers directly or calls a tool. Prompt sources:

  harness   the harness's own frontend instructions and full tool list (spawn_thinking is the
            handoff), exported by dump_harness_prompt.mjs. The export is private: pass its path
            with --harness-json and keep it outside this folder.
  rules     one stand-in tool, `delegate(objective)`, whose description spells out the README rules
  minimal   the same stand-in tool with a one-line description

Offline and half-duplex: the whole clip is given at once and the full reply is decoded.
Model loading and decoding follow Matthew's run_speechfc.py (greedy, vision and TTS off).

On the server, pin GPUs with CUDA_VISIBLE_DEVICES; --gpus then refers to the visible ids:
    CUDA_VISIBLE_DEVICES=6,7 python runners/minicpmo_delegate_tool.py --gpus 0,1 \
        --prompt harness --harness-json /local/data/xy2764/harness-private/harness_frontend.json --limit 10
"""
import argparse
import json
import re
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # experiments/delegation-bench

STAND_IN_DESCRIPTIONS = {
    "rules": (
        "Hand the user's request to the backend assistant. The backend can search the web, get live "
        "or up-to-date information (news, weather, prices, scores, schedules), act in the world "
        "(bookings, orders, reminders, calendars, email), and do multi-step research or long writing. "
        "Call this only when you cannot answer well yourself. Answer directly, without calling it, for "
        "chit-chat, general knowledge, explanations, advice, opinions, translation, simple arithmetic "
        "and short creative requests. If the request does not say what it refers to and the "
        "conversation does not tell you either, ask the user instead of calling it."
    ),
    "minimal": "Hand the user's request to the backend assistant, which has tools, live information and time for long tasks.",
}
OBJECTIVE_DESCRIPTION = (
    "A complete, self-contained restatement of what the user wants, keeping every name, number "
    "and detail, with words like 'it' or 'there' resolved from the conversation."
)
PERSONA = "You are a voice assistant talking with a user. Keep spoken answers short."

# Qwen3's standard tool prompt, as in run_speechfc.py.
TOOLS_PROMPT = """# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{tools}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": <function-name>, "arguments": <args-json-object>}}
</tool_call>"""

TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
END_MARKERS = re.compile(r"<\|tts_eos\|>|<\|im_end\|>")


def stand_in_tool(variant):
    return {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": STAND_IN_DESCRIPTIONS[variant],
            "parameters": {
                "type": "object",
                "properties": {"objective": {"type": "string", "description": OBJECTIVE_DESCRIPTION}},
                "required": ["objective"],
            },
        },
    }


def build_prompt(variant, harness_json=None):
    """Return (system prompt, handoff tool name, names of all offered tools)."""
    if variant == "harness":
        exported = json.loads(Path(harness_json).read_text(encoding="utf-8"))
        tools = exported["tools"]
        lines = "\n".join(json.dumps(t, ensure_ascii=False) for t in tools)
        system = exported["instructions"] + "\n\n" + TOOLS_PROMPT.format(tools=lines)
        return system, "spawn_thinking", {t["function"]["name"] for t in tools}
    tool = stand_in_tool(variant)
    system = PERSONA + "\n\n" + TOOLS_PROMPT.format(tools=json.dumps(tool, ensure_ascii=False))
    return system, "delegate", {"delegate"}


def parse_reply(text, handoff="delegate", offered=frozenset({"delegate"})):
    """Classify a raw reply.

    decision:
      self           no tool call
      delegate       a well-formed call to the handoff tool with a non-empty objective
      frontend_tool  a well-formed call to another offered tool (handled by the frontend)
      invented       a call to a tool that was not offered
      malformed      a tool call that cannot be read (bad JSON, unclosed tag, missing objective)
    """
    text = text or ""
    calls = TOOL_CALL.findall(text)
    spoken = END_MARKERS.sub("", TOOL_CALL.sub("", text)).strip()
    result = {"decision": "self", "tool": None, "objective": None, "spoken": spoken, "tools_called": []}
    if not calls:
        if "<tool_call>" in text:
            result["decision"] = "malformed"
        return result
    parsed = []
    for body in calls:
        try:
            call = json.loads(body)
        except json.JSONDecodeError:
            parsed.append(None)
            continue
        if not isinstance(call, dict):
            parsed.append(None)
            continue
        args = call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = None
        parsed.append((str(call.get("name") or ""), args if isinstance(args, dict) else None))
    result["tools_called"] = [p[0] if p else None for p in parsed]
    for p in parsed:  # a handoff anywhere in the reply counts as a handoff
        if p and p[0] == handoff:
            objective = (p[1] or {}).get("objective")
            if isinstance(objective, str) and objective.strip():
                result.update(decision="delegate", tool=handoff, objective=objective.strip())
            else:
                result.update(decision="malformed", tool=handoff)
            return result
    first = parsed[0]
    if first is None:
        result["decision"] = "malformed"
    elif first[0] in offered:
        result.update(decision="frontend_tool", tool=first[0])
    else:
        result.update(decision="invented", tool=first[0])
    return result


def load_model(path, precision, gpus, max_mem_gib):
    import torch
    from transformers import AutoModel, AutoTokenizer

    kwargs = dict(
        trust_remote_code=True,
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16 if precision == "bf16" else torch.float16,
        init_vision=False,
        init_audio=True,
        init_tts=False,
    )
    if len(gpus) > 1:
        kwargs["device_map"] = "auto"
        kwargs["max_memory"] = {g: f"{max_mem_gib}GiB" for g in gpus}
    else:
        kwargs["device_map"] = f"cuda:{gpus[0]}"
    model = AutoModel.from_pretrained(path, **kwargs).eval()
    tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    return model, tokenizer


def keep_raw_output(model, tokenizer, use_tts_template):
    """Fix the reply template and keep the raw decoded text (chat() may cut it at <|tts_eos|>)."""
    last = {}
    orig_template = tokenizer.apply_chat_template

    def apply_chat_template(*a, **kw):
        kw["use_tts_template"] = use_tts_template
        last["prompt"] = orig_template(*a, **kw)
        return last["prompt"]

    tokenizer.apply_chat_template = apply_chat_template
    orig_generate = model.generate

    def generate(*a, **kw):
        res, outputs = orig_generate(*a, **kw)
        last["raw"] = res[0] if res else None
        return res, outputs

    model.generate = generate
    return last


def time_first_token(model, last):
    """Record when the logits for the first reply token are ready.

    chat() decodes through model.llm.generate, whose first forward pass is the prefill; the first
    token is chosen from its logits, so the end of that pass marks the first token.
    """
    import torch

    def hook(module, inputs, output):
        if "first_token_at" not in last:
            torch.cuda.synchronize()
            last["first_token_at"] = time.perf_counter()

    model.llm.register_forward_hook(hook)


def run_case(model, tokenizer, case, audio_dir, system, max_new_tokens, text_input, last):
    import librosa

    msgs = [{"role": "system", "content": [system]}]
    for turn in case["context"]:
        msgs.append({"role": turn["role"], "content": [turn["text"]]})
    audio, _ = librosa.load(audio_dir / f"{case['id']}.wav", sr=16000, mono=True)
    msgs.append({"role": "user", "content": [case["utterance"] if text_input else audio]})
    last.pop("first_token_at", None)
    started = time.perf_counter()
    out = model.chat(
        msgs=msgs,
        tokenizer=tokenizer,
        do_sample=False,
        max_new_tokens=max_new_tokens,
        num_beams=1,
        repetition_penalty=1.0,
        use_tts_template=False,
        generate_audio=False,
        omni_mode=False,
        enable_thinking=False,
    )
    elapsed = time.perf_counter() - started
    first_token = last["first_token_at"] - started if "first_token_at" in last else None
    text = out if isinstance(out, str) else getattr(out, "text", str(out))
    return text, first_token, elapsed, len(audio) / 16000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openbmb/MiniCPM-o-4_5",
                    help="weights path or Hugging Face id (resolved from HF_HUB_CACHE)")
    ap.add_argument("--precision", choices=["bf16", "awq"], default="bf16")
    ap.add_argument("--gpus", default="0,1", help="visible GPU ids; >1 splits the model")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--prompt", choices=["harness", "rules", "minimal"], default="harness")
    ap.add_argument("--harness-json", type=Path, help="private export from dump_harness_prompt.mjs")
    ap.add_argument("--mode", choices=["text", "speech"], default="text", help="reply template")
    ap.add_argument("--input", choices=["audio", "text"], default="audio",
                    help="text = control with the typed utterance instead of the audio")
    ap.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    ap.add_argument("--audio-dir", type=Path, default=HERE / "audio")
    ap.add_argument("--ids", nargs="+", help="run only these case ids")
    ap.add_argument("--limit", type=int, help="run only the first N cases")
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--results", type=Path, default=HERE / "results")
    ap.add_argument("--tag", help="appended to the result name, e.g. to rerun a prompt without resuming")
    args = ap.parse_args()
    if args.prompt == "harness" and not args.harness_json:
        ap.error("--prompt harness needs --harness-json")

    system, handoff, offered = build_prompt(args.prompt, args.harness_json)
    cases = [json.loads(l) for l in args.cases.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.ids:
        cases = [c for c in cases if c["id"] in set(args.ids)]
    if args.limit:
        cases = cases[: args.limit]

    prompt_label = args.prompt
    if args.prompt == "harness":
        # harness_frontend.json -> "harness", harness_frontend_en.json -> "harness-en"
        suffix = args.harness_json.stem.removeprefix("harness_frontend").strip("_-")
        prompt_label = f"harness-{suffix}" if suffix else "harness"
    name = f"minicpmo45-{args.precision}-{prompt_label}-{args.mode}" + ("-textin" if args.input == "text" else "")
    if args.tag:
        name += f"-{args.tag}"
    args.results.mkdir(parents=True, exist_ok=True)
    out_file = args.results / f"{name}.jsonl"
    done = set()
    if out_file.exists():  # resume
        for line in out_file.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                pass

    gpus = [int(g) for g in args.gpus.split(",")]
    model, tokenizer = load_model(args.model, args.precision, gpus, args.max_mem_gib)
    last = keep_raw_output(model, tokenizer, args.mode == "speech")
    time_first_token(model, last)

    with out_file.open("a", encoding="utf-8") as f:
        for case in cases:
            if case["id"] in done:
                continue
            last.clear()
            try:
                text, first_token, elapsed, audio_s = run_case(model, tokenizer, case, args.audio_dir, system,
                                                               args.max_new_tokens, args.input == "text", last)
                raw = last.get("raw") or text
                record = {"id": case["id"], **parse_reply(raw, handoff, offered), "raw": raw,
                          "first_token_s": None if first_token is None else round(first_token, 3),
                          "latency_s": round(elapsed, 3), "audio_s": round(audio_s, 2)}
            except Exception as e:  # recorded and scored as an error, then the run goes on
                traceback.print_exc()
                import torch
                torch.cuda.empty_cache()
                record = {"id": case["id"], "decision": "error", "tool": None, "objective": None,
                          "spoken": "", "tools_called": [], "raw": None, "error": f"{type(e).__name__}: {e}"}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            shown = record["objective"] or (f"[{record['tool']}] " if record["tool"] else "") + record["spoken"]
            print(f"[{case['id']}] expected={case['expected']:8s} got={record['decision']:13s} "
                  f"first={record.get('first_token_s') or 0:.2f}s full={record.get('latency_s', 0):.2f}s | {shown[:100]!r}")
    print(f"wrote {out_file}")


if __name__ == "__main__":
    main()
