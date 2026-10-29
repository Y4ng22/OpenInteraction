"""Untrained baseline: MiniCPM-o 4.5 in text chat mode on the delegation test set.

Full duplex has no tool-call path, so the baseline uses the model's simplex chat mode with its own chat template and
text input (TTS has not run). Each item gets the sample's fast tools plus `spawn_thinking`, the handoff tool, and a
system prompt that states the five behaviors. Two tasks:

- `decide`: the turn up to the first decision (with any context turns). The reply is scored by score_baseline.py.
- `report`: handoff samples with a backend result. The result arrives as the spawn_thinking tool response, either
  right after the handoff (`now`) or after one unrelated exchange (`later`); the model must tell the user.

The default backend is the official model code (`--backend hf`, greedy). A vLLM server of the extracted language
model (`extract_llm.py`) is also supported, but on 2026-10-29 its outputs under concurrent load drifted to other
topics while the official code's did not, so the reported numbers use `hf`. Tool calls are read from the text
(`<tool_call>{...}</tool_call>`) the same way for both.

    python run_baseline.py --src <snapshot> --final delegation_final.jsonl --tools tools_split.jsonl --task decide \
        --shard 0/2 --out decide.0.jsonl
"""
import argparse
import concurrent.futures as cf
import json
import random
import re
import urllib.request
from pathlib import Path

TOOL_CALL = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)

SYSTEM = """You are a real-time voice assistant. You talk with the user while a slower backend agent does long work.

On every user turn, do exactly one of these:
- Chat: if no tool and no outside information is needed (small talk, opinions, stable knowledge), just answer briefly.
- Ask: if a value only the user can decide is missing, ask one short question. Do not ask for anything the backend can look up.
- Call: if your own tools cover everything needed, call them with only values the user said.
- Hand off: if it needs anything your tools cannot do, call spawn_thinking with a one-sentence objective that repeats every value the user gave, and say a short acknowledgment. Never say the task is done and never say you cannot help.
- Split: if the turn has two independent requests, one your tools cover and one they do not, call your tool for the first and spawn_thinking for the second.

When a spawn_thinking result comes back, tell the user the result briefly using its concrete values."""

SPAWN = {"name": "spawn_thinking", "description": "Hand a task to the backend agent, which has many more tools and can take time. The result comes back later as this tool's response.",
         "parameters": {"type": "object", "properties": {"objective": {"type": "string", "description": "One sentence for the backend with every value the user gave."}},
                        "required": ["objective"]}}

SMALL_TALK = [("By the way, do you have a favorite season?", "I'd pick autumn: cool air and good light."),
              ("Unrelated, but what's a good name for a goldfish?", "Bubbles is a classic, or Captain Fin if you want flair."),
              ("Quick one, how do you say thanks in Italian?", "Grazie."),
              ("Any tip for remembering names?", "Repeat the name back once when you hear it, it really helps."),
              ("Is it pronounced gif or jif?", "Its creator said jif, but most people say gif. Both are understood.")]


def tool_spec(t):
    return {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}


def decision_messages(x):
    msgs = []
    for e in x["events"]:
        if e["role"] == "user":
            msgs.append({"role": "user", "content": e["text"]})
            if not e.get("context"):
                return msgs
        elif e["role"] == "assistant" and e.get("context"):
            msgs.append({"role": "assistant", "content": e["say"]})
    raise ValueError("no request turn")


def report_messages(x, when, rng):
    msgs = decision_messages(x)
    hand = next(e for e in x["events"] if e["role"] == "assistant" and e.get("objective"))
    backend = next(e for e in x["events"] if e["role"] == "backend")
    msgs.append({"role": "assistant", "content": hand["say"], "tool_calls": [
        {"id": "call_0", "type": "function", "function": {"name": "spawn_thinking", "arguments": json.dumps({"objective": hand["objective"]})}}]})
    if when == "later":
        u, a = rng.choice(SMALL_TALK)
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    msgs.append({"role": "tool", "tool_call_id": "call_0", "content": backend["text"]})
    return msgs


def parse(text):
    """Split raw output into spoken text and tool calls. A block that is not a JSON object with a string "name" is
    kept as a malformed call (name None), so the scorer can count it."""
    calls = []
    for block in TOOL_CALL.findall(text):
        block = block.strip()
        try:
            obj = json.loads(block[block.index("{"): block.rindex("}") + 1])
        except ValueError:
            obj = None
        name = obj.get("name") if isinstance(obj, dict) else None
        calls.append({"name": name if isinstance(name, str) else None,
                      "arguments": obj.get("arguments", obj.get("parameters")) if isinstance(obj, dict) else None,
                      "raw": block[:500]})
    content = TOOL_CALL.sub("", text).replace("<|im_end|>", "").replace("<think>", "").replace("</think>", "").strip()
    return {"raw": text, "content": content, "calls": calls}


def ask_vllm(url, model, messages, tools, max_tokens):
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}] + messages, "tools": tools,
            "temperature": 0, "max_tokens": max_tokens, "skip_special_tokens": False,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        m = json.loads(r.read())["choices"][0]["message"]
    raw = (m.get("content") or "") + "".join(
        f'<tool_call>{{"name": {json.dumps(c["function"]["name"])}, "arguments": {c["function"]["arguments"]}}}</tool_call>'
        for c in m.get("tool_calls") or [])
    return parse(raw)


class HF:
    def __init__(self, src):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(src, trust_remote_code=True)
        model = AutoModel.from_pretrained(src, trust_remote_code=True, torch_dtype=torch.bfloat16, attn_implementation="sdpa",
                                          init_vision=False, init_audio=False, init_tts=False, device_map="auto").eval()
        self.llm = model.llm

    def __call__(self, messages, tools, max_tokens):
        ids = self.tok.apply_chat_template([{"role": "system", "content": SYSTEM}] + messages, tools=tools,
                                           add_generation_prompt=True, enable_thinking=False, return_tensors="pt").to(self.llm.device)
        with self.torch.no_grad():
            out = self.llm.generate(ids, attention_mask=self.torch.ones_like(ids), max_new_tokens=max_tokens,
                                    do_sample=False, pad_token_id=self.tok.eos_token_id)
        return parse(self.tok.decode(out[0, ids.shape[1]:], skip_special_tokens=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--task", choices=["decide", "report"], required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("-n", type=int, default=0, help="limit (report: number of samples)")
    ap.add_argument("--backend", choices=["hf", "vllm"], default="hf")
    ap.add_argument("--src", type=Path, help="MiniCPM-o-4_5 snapshot (hf backend)")
    ap.add_argument("--shard", default="0/1", help="i/n: run every n-th job starting at i")
    ap.add_argument("--url", default="http://127.0.0.1:8201/v1/chat/completions")
    ap.add_argument("--model", default="minicpmo45-llm")
    ap.add_argument("--workers", type=int, default=4, help="vllm backend only")
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    X = [x for x in map(json.loads, args.final.read_text(encoding="utf-8").splitlines()) if x["split"] == args.split]
    rng = random.Random(args.seed)
    jobs = []
    if args.task == "decide":
        X = X[: args.n] if args.n else X
        for x in X:
            jobs.append((x, None, decision_messages(x)))
    else:
        X = [x for x in X if x["target"] == "handoff" and any(e["role"] == "backend" for e in x["events"])]
        X = rng.sample(X, min(args.n or len(X), len(X)))
        for x in X:
            for when in ("now", "later"):
                jobs.append((x, when, report_messages(x, when, rng)))

    i, n = map(int, args.shard.split("/"))
    jobs = jobs[i::n]
    if args.backend == "hf":
        hf = HF(args.src)
        generate = hf
    else:
        generate = lambda m, t, k: ask_vllm(args.url, args.model, m, t, k)

    def run(job):
        x, when, msgs = job
        spec = [tool_spec(tools[i]) for i in x["frontend_tools"] if i in tools] + [{"type": "function", "function": SPAWN}]
        try:
            out = generate(msgs, spec, 160 if args.task == "report" else 256)
        except Exception as e:  # keep going; the scorer counts errors
            out = {"error": repr(e)}
        return {"id": x["id"], "target": x["target"], "speech": x.get("speech", "plain"), "when": when, "messages": msgs, **out}

    with cf.ThreadPoolExecutor(1 if args.backend == "hf" else args.workers) as pool, args.out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(pool.map(run, jobs)):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if (i + 1) % 100 == 0:
                print(f"{i + 1}/{len(jobs)}", flush=True)
    print(f"{len(jobs)} -> {args.out}")


if __name__ == "__main__":
    main()
