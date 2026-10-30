"""Turn the spoken feasibility samples into MiniCPM-o 4.5 full-duplex training sequences.

Earlier context turns are spoken too (their assistant lines are in the stream but carry no loss). The layout
copies what MiniCPMODuplex feeds and decodes at inference (modeling_minicpmo.py, streaming_prefill and
streaming_generate, ls_mode "explicit"):

    <|im_start|>system\n{system}<|im_end|>
    listening, one per second of audio:   <unit> [audio] <|listen|> </unit>
    speaking, more to say:                <unit> [audio] <|speak|> up to 18 tokens <|chunk_eos|> </unit>
    speaking, last piece:                 <unit> [audio] <|speak|> up to 17 tokens <|turn_eos|> <|listen|> </unit>
    a tool or backend result arrives:     <unit> <tool_response>...</tool_response> <|speak|> ... </unit>

Every user turn is followed by one second of silence, and the reply starts in the first silent unit. The reply is
the tool calls, one line each as <tool_call>{"name": ..., "arguments": {...}}</tool_call> (handoffs call
spawn_thinking), then the spoken text. Audio embeddings are taken from the official streaming path (the duplex
object's streaming_prefill with its LLM feed stubbed out), so they match what the model sees live. Loss is on the
model's own tokens only (listen/speak decisions, text, chunk and turn ends).

Writes one <id>.pt per sample ({"ids", "labels", "audio": [tensor], "audio_at": [position]}) and seq_index.jsonl.

    python build_sequences.py --src <MiniCPM-o-4_5 snapshot> --final delegation_final.jsonl --tools tools_split.jsonl \
        --manifest manifest.jsonl --out seqs/
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

SYSTEM = """You are a real-time voice assistant in a full-duplex conversation. You listen while the user talks and answer when they are done. A slower backend agent can do long work for you.

On every user turn, do exactly one of these:
- Chat: if no tool and no outside information is needed, just answer briefly.
- Ask: if a value only the user can decide is missing, ask one short question.
- Call: if your own tools cover everything needed, call them with only values the user said.
- Hand off: if it needs anything your tools cannot do, call spawn_thinking with a one-sentence objective that repeats every value the user gave, then say a short acknowledgment. Never say the task is done.
- Split: two independent requests, one your tools cover and one they do not: call your tool and spawn_thinking, then say a short acknowledgment.

To call a tool, write <tool_call>{"name": <tool name>, "arguments": {...}}</tool_call> on one line before you speak. Tool calls are never spoken. When a result arrives in <tool_response>, tell the user briefly using its concrete values.

# Tools
"""
SPAWN = {"name": "spawn_thinking", "description": "Hand a task to the backend agent. Its result comes back later.",
         "parameters": {"type": "object", "properties": {"objective": {"type": "string"}}, "required": ["objective"]}}
PIECE, LAST_PIECE, SR = 18, 17, 16000


def system_prompt(x, tools):
    lines = [json.dumps({"name": t["name"], "description": t["description"][:200], "parameters": t["parameters"]},
                        ensure_ascii=False) for t in (tools[i] for i in x["frontend_tools"] if i in tools)]
    return SYSTEM + "\n".join(lines + [json.dumps(SPAWN)])


def call_text(name, arguments):
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False) + "</tool_call>"


def steps(x):
    """The sample as an ordered list: ("user", k) for the k-th spoken turn, ("context", text) for an earlier
    assistant line, ("reply", text) for what the model produces (tool calls, then speech), ("result", text) for a
    tool or backend result coming back."""
    out, k = [], 0
    for e in x["events"]:
        if e["role"] == "user":  # earlier context turns are spoken too
            out.append(("user", k))
            k += 1
        elif e["role"] == "assistant" and e.get("context"):
            out.append(("context", e["say"]))
        elif e["role"] == "assistant":
            parts = [call_text(c["name"], c["arguments"]) for c in e.get("calls") or []]
            if e.get("objective"):
                parts.append(call_text("spawn_thinking", {"objective": e["objective"]}))
            out.append(("reply", "".join(parts) + (e.get("say") or "")))
        elif e["role"] == "tool":
            out.append(("result", json.dumps(e["result"], ensure_ascii=False)))
        elif e["role"] == "backend":
            out.append(("result", e["text"]))
    return out


class Recorder:
    """Stands in for the duplex decoder's LLM feed: keeps the audio embeddings, runs nothing."""
    def __init__(self, decoder):
        self.audio = None
        decoder.feed = self.feed

    def feed(self, embeds, return_logits=False):
        if embeds.dim() == 2 and embeds.shape[0] > 1:
            self.audio = embeds.detach().to("cpu", torch.bfloat16)
        return (None, None) if return_logits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", default="0/1", help="i/n: build every n-th sample starting at i")
    args = ap.parse_args()

    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    model = AutoModel.from_pretrained(args.src, trust_remote_code=True, torch_dtype=torch.bfloat16,
                                      attn_implementation="sdpa", init_vision=False, init_audio=True, init_tts=True).eval()
    model.init_tts = lambda *a, **k: None
    duplex = model.as_duplex(generate_audio=False)
    if torch.cuda.is_available():  # the audio path on the GPU, the 8B language model (unused here) stays on the CPU
        for name, module in model.named_children():
            if name not in ("llm", "tts"):
                module.to("cuda")
        duplex.device = torch.device("cuda")
    T = {t: tok.convert_tokens_to_ids(t) for t in ["<unit>", "</unit>", "<|listen|>", "<|speak|>", "<|chunk_eos|>", "<|turn_eos|>"]}
    bad = set(tok.bad_token_ids)
    rec = Recorder(duplex.decoder)

    gold = {x["id"]: x for x in map(json.loads, args.final.read_text(encoding="utf-8").splitlines())}
    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    args.out.mkdir(parents=True, exist_ok=True)
    index, banned_seen = [], {}
    manifest = [json.loads(l) for p in args.manifest for l in p.read_text(encoding="utf-8").splitlines()]
    i_shard, n_shard = map(int, args.shard.split("/"))
    manifest = manifest[i_shard::n_shard]
    for m in manifest[: args.limit or None]:
        if (args.out / f"{m['id']}.pt").exists():
            continue
        x = gold[m["id"]]
        plan = steps(x)
        assert sum(s == "user" for s, _ in plan) == len(m["wavs"]), m["id"]
        duplex._reset_streaming_state()  # what prepare() does, minus running the LLM on the system prompt
        duplex.decoder.reset()
        duplex.model.init_streaming_processor()
        ids, labels, audio, audio_at = [], [], [], []

        def put(token_ids, learn):
            ids.extend(token_ids)
            labels.extend(token_ids if learn else [-100] * len(token_ids))

        put(tok.encode("<|im_start|>system\n" + system_prompt(x, tools) + "<|im_end|>", add_special_tokens=False), False)

        def audio_unit(chunk, outputs, learn=True):
            assert duplex.streaming_prefill(audio_waveform=chunk.astype(np.float32))["success"]
            put([T["<unit>"]], False)
            audio_at.append(len(ids))
            audio.append(rec.audio)
            put([0] * rec.audio.shape[0], False)  # placeholders, replaced by the embeddings at training time
            put(outputs, learn)
            put([T["</unit>"]], False)

        def speak(text):
            body = tok.encode(text, add_special_tokens=False)
            for t in body:
                if t in bad:
                    banned_seen[t] = tok.decode([t])
            pieces = [body[i:i + PIECE] for i in range(0, len(body), PIECE)] or [[]]
            if len(pieces[-1]) > LAST_PIECE:
                pieces.append(pieces[-1][LAST_PIECE:])
                pieces[-2] = pieces[-2][:LAST_PIECE]
            return [[T["<|speak|>"]] + p + ([T["<|chunk_eos|>"]] if i < len(pieces) - 1 else [T["<|turn_eos|>"], T["<|listen|>"]])
                    for i, p in enumerate(pieces)]

        silence = np.zeros(SR, np.float32)
        pending = ""
        for kind, value in plan:
            if kind == "user":
                speech, _ = sf.read(m["wavs"][value]["path"], dtype="float32")
                n = math.ceil(len(speech) / SR)
                speech = np.concatenate([speech, np.zeros(n * SR - len(speech), np.float32)])
                for i in range(n):
                    audio_unit(speech[i * SR:(i + 1) * SR], [T["<|listen|>"]])
            elif kind == "context":  # an earlier assistant line: in the stream, but not a training target
                for out in speak(value):
                    audio_unit(silence, out, learn=False)
            elif kind == "result":
                audio_unit(silence, [T["<|listen|>"]])  # one second of waiting before the result lands
                pending += "<tool_response>" + value + "</tool_response>"
            else:
                outs = speak(value)
                if pending:  # the result arrives as a text unit and the answer starts right after it
                    put([T["<unit>"]] + tok.encode(pending, add_special_tokens=False), False)
                    put(outs[0], True)
                    put([T["</unit>"]], False)
                    outs, pending = outs[1:], ""
                for out in outs:
                    audio_unit(silence, out)
        index.append({"id": m["id"], "split": m["split"], "target": m["target"], "tokens": len(ids)})
        torch.save({"ids": ids, "labels": labels, "audio": audio, "audio_at": audio_at}, args.out / f"{m['id']}.pt")
        print(m["id"], m["target"], len(ids), "tokens", len(audio), "audio units", flush=True)
    name = "seq_index.jsonl" if n_shard == 1 else f"seq_index_{i_shard}.jsonl"
    (args.out / name).write_text("".join(json.dumps(r) + "\n" for r in index), encoding="utf-8")
    print("banned tokens used by targets:", banned_seen)


if __name__ == "__main__":
    main()
