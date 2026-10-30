"""Turn the spoken feasibility samples into MiniCPM-o 4.5 full-duplex training sequences.

Earlier context turns are spoken too (their assistant lines are in the stream but carry no loss). The layout
copies what MiniCPMODuplex feeds and decodes at inference (modeling_minicpmo.py, streaming_prefill and
streaming_generate, ls_mode "explicit"):

    <|im_start|>system\n{system}<|im_end|>
    listening, one per second of audio:   <unit> [audio] <|listen|> </unit>
    speaking:                             <unit> [audio] <|speak|> a few tokens <|chunk_eos|> </unit>
    writing a tool call (never spoken):   <unit> [audio] <|speak|> up to 18 tokens <|chunk_eos|> </unit>
    ending the turn:                      <unit> [audio] <|speak|> <|turn_eos|> <|chunk_eos|> </unit>
    a tool or backend result arrives:     <unit> <tool_response>...</tool_response> <|speak|> ... </unit>

Spoken text keeps the untrained model's own pace: it says 1 to 8 tokens a second (mostly 2 to 5, mean about 3.4,
measured on its own duplex replies), because each unit's text goes to TTS and must fit in a second of speech. It
ends a turn the way the model does, usually with a unit of its own (<|speak|><|turn_eos|><|chunk_eos|>), sometimes
right after the last words. Tool calls are not spoken, so they are written 18 tokens a unit.

Every user turn is followed by one second of silence, and the reply starts in the first silent unit. A handoff or
split reply speaks its acknowledgment first and then writes the calls, one line each as
<tool_call>{"name": ..., "arguments": {...}}</tool_call> (handoffs call spawn_thinking); a call the assistant makes
alone has no acknowledgment. Audio embeddings are taken from the official streaming path (the duplex
object's streaming_prefill with its LLM feed stubbed out), so they match what the model sees live. Loss is on the
model's own tokens only (listen/speak decisions, text, chunk and turn ends).

Writes one <id>.pt per sample ({"ids", "labels", "audio": [tensor], "audio_at": [position]}) and seq_index.jsonl.

    python build_sequences.py --src <MiniCPM-o-4_5 snapshot> --final delegation_final.jsonl --tools tools_split.jsonl \
        --manifest manifest.jsonl --out seqs/
"""
import argparse
import json
import math
import random
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

SYSTEM = """You are a real-time voice assistant in a full-duplex conversation. You listen while the user talks and answer when they are done. A slower backend agent can do long work for you.

On every user turn, do exactly one of these:
- Chat: if no tool and no outside information is needed, just answer briefly.
- Ask: if a value only the user can decide is missing, ask one short question.
- Call: if your own tools cover everything needed, call them with only values the user said.
- Hand off: if it needs anything your tools cannot do, say a short acknowledgment, then call spawn_thinking with a one-sentence objective that repeats every value the user gave. Never say the task is done.
- Split: two independent requests, one your tools cover and one they do not: acknowledge both briefly, then call your tool and spawn_thinking.

To call a tool, write <tool_call>{"name": <tool name>, "arguments": {...}}</tool_call> on one line. Tool calls are never spoken. When a result arrives in <tool_response>, tell the user briefly using its concrete values.

# Tools
"""
SPAWN = {"name": "spawn_thinking", "description": "Hand a task to the backend agent. Its result comes back later.",
         "parameters": {"type": "object", "properties": {"objective": {"type": "string"}}, "required": ["objective"]}}
CALL_PIECE, SR = 18, 16000
PACE = {1: 10, 2: 21, 3: 26, 4: 32, 5: 12, 6: 4, 7: 3, 8: 1}  # text tokens per spoken unit, untrained model
OWN_END_UNIT = 0.8  # share of turns ended by a unit of their own


def system_prompt(x, tools):
    lines = [json.dumps({"name": t["name"], "description": t["description"][:200], "parameters": t["parameters"]},
                        ensure_ascii=False) for t in (tools[i] for i in x["frontend_tools"] if i in tools)]
    return SYSTEM + "\n".join(lines + [json.dumps(SPAWN)])


def call_text(name, arguments):
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False) + "</tool_call>"


def steps(x, calls_first=False):
    """The sample as an ordered list: ("user", (id, k)) for its k-th spoken turn, ("context", segments) for an
    earlier assistant line, ("reply", segments) for what the model produces, ("result", text) for a tool or backend
    result. Segments are ("say", text) spoken and ("call", text) written.
    A reply that hands off speaks its acknowledgment first and then writes the calls, so the user hears something
    in the first second (calls_first=True restores the feasibility test's order)."""
    out, k = [], 0
    for e in x["events"]:
        if e["role"] == "user":  # earlier context turns are spoken too
            out.append(("user", (x["id"], k)))
            k += 1
        elif e["role"] == "assistant" and e.get("context"):
            out.append(("context", [("say", e["say"])]))
        elif e["role"] == "assistant":
            calls = [call_text(c["name"], c["arguments"]) for c in e.get("calls") or []]
            if e.get("objective"):
                calls.append(call_text("spawn_thinking", {"objective": e["objective"]}))
            say = [("say", e["say"])] if e.get("say") else []
            calls = [("call", c) for c in calls]
            ack_first = e.get("objective") and say and not calls_first
            out.append(("reply", say + calls if ack_first else calls + say))
        elif e["role"] == "tool":
            out.append(("result", json.dumps(e["result"], ensure_ascii=False)))
        elif e["role"] == "backend":
            out.append(("result", e["text"]))
    return out


def make_pairs(gold, n, seed):
    """Two-request episodes: a handoff whose backend result comes back only after the user has asked something
    else (any target) and been answered. Train only; each handoff is used once."""
    rng = random.Random(seed)
    first = [x for x in gold.values() if x["split"] == "train" and x["target"] == "handoff"
             and any(e["role"] == "backend" for e in x["events"])]
    second = [x for x in gold.values() if x["split"] == "train"]
    rng.shuffle(first)
    pairs = []
    for i, a in enumerate(first[:n]):
        b = rng.choice(second)
        while b["id"] == a["id"]:
            b = rng.choice(second)
        pairs.append({"id": f"pair-{i:05d}", "split": "train", "target": "pair", "a": a["id"], "b": b["id"]})
    return pairs


def pair_plan(a, b, calls_first=False):
    sa, sb = steps(a, calls_first), steps(b, calls_first)
    r = next(i for i, (kind, _) in enumerate(sa) if kind == "result")
    return sa[:r] + sb + sa[r:]


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
    ap.add_argument("--calls-first", action="store_true", help="the feasibility test's order: calls before speech")
    ap.add_argument("--say-override", type=Path, help="jsonl of {id, say}: acknowledgments rewritten to be spoken first")
    ap.add_argument("--pairs", type=int, default=0, help="also build this many two-request episodes (make_pairs)")
    ap.add_argument("--pair-seed", type=int, default=20261030)
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
    if args.say_override:
        for o in map(json.loads, args.say_override.read_text(encoding="utf-8").splitlines()):
            if o["id"] in gold:
                lab = next(e for e in gold[o["id"]]["events"] if e["role"] == "assistant" and not e.get("context"))
                lab["say"] = o["say"]
    wavs = {m["id"]: m["wavs"] for p in args.manifest for m in map(json.loads, p.read_text(encoding="utf-8").splitlines())}
    items = [{"id": i, "split": gold[i]["split"], "target": gold[i]["target"]} for i in gold if i in wavs]
    if args.pairs:
        pairs = make_pairs(gold, args.pairs, args.pair_seed)
        (args.out / "pairs.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pairs), encoding="utf-8")
        items += pairs
    i_shard, n_shard = map(int, args.shard.split("/"))
    items = items[i_shard::n_shard]
    for m in items[: args.limit or None]:
        if (args.out / f"{m['id']}.pt").exists():
            continue
        if m["target"] == "pair":
            a, b = gold[m["a"]], gold[m["b"]]
            x = {"frontend_tools": a["frontend_tools"] + [i for i in b["frontend_tools"] if i not in a["frontend_tools"]]}
            plan = pair_plan(a, b, args.calls_first)
        else:
            x = gold[m["id"]]
            plan = steps(x, args.calls_first)
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

        pace = random.Random(m["id"])

        def speak(segments):
            """Units for a reply: spoken text at the model's own pace, calls 18 tokens a unit, then the turn end."""
            units = []
            for kind, text in segments:
                body = tok.encode(text, add_special_tokens=False)
                for t_ in body:
                    if t_ in bad:
                        banned_seen[t_] = tok.decode([t_])
                i = 0
                while i < len(body):
                    n = CALL_PIECE if kind == "call" else pace.choices(list(PACE), weights=list(PACE.values()))[0]
                    units.append([T["<|speak|>"]] + body[i:i + n] + [T["<|chunk_eos|>"]])
                    i += n
            if units and segments[-1][0] == "say" and len(units[-1]) <= 18 and pace.random() > OWN_END_UNIT:
                units[-1] = units[-1][:-1] + [T["<|turn_eos|>"], T["<|chunk_eos|>"]]
            else:
                units.append([T["<|speak|>"], T["<|turn_eos|>"], T["<|chunk_eos|>"]])
            return units

        silence = np.zeros(SR, np.float32)
        pending = ""
        for kind, value in plan:
            if kind == "user":
                speech, _ = sf.read(wavs[value[0]][value[1]]["path"], dtype="float32")
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
        audio_unit(silence, [T["<|listen|>"]])  # back to listening after the last turn
        index.append({"id": m["id"], "split": m["split"], "target": m["target"], "tokens": len(ids)})
        torch.save({"ids": ids, "labels": labels, "audio": audio, "audio_at": audio_at}, args.out / f"{m['id']}.pt")
        print(m["id"], m["target"], len(ids), "tokens", len(audio), "audio units", flush=True)
    name = "seq_index.jsonl" if n_shard == 1 else f"seq_index_{i_shard}.jsonl"
    (args.out / name).write_text("".join(json.dumps(r) + "\n" for r in index), encoding="utf-8")
    print("banned tokens used by targets:", banned_seen)


if __name__ == "__main__":
    main()
