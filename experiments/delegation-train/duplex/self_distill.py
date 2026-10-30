"""Self-distillation data that keeps MiniCPM-o's own full-duplex behavior while it learns delegation.

The delegation samples only show one rhythm: the user finishes, then the model answers. Trained on those alone, the
model could lose what it already does in duplex (listening through pauses, stopping when the user cuts in, ordinary
chat). Here the untrained model talks with scripted users in its real duplex loop, and every unit it produces is kept
as a training target, so the mix "replays" its own behavior.

prepare: user turns from DailyDialog (the first speaker's lines of each dialogue, 2 to 4 turns, 3 to 40 words) as
    samples in the delegation event format, so duplex/tts.py --all can speak them.
run: streams each conversation into MiniCPMODuplex (default duplex system prompt, default sampling) with one of two
    timings: turn-taking (the next user turn starts one second after the model ends its turn, two thirds) or barge-in
    (the next user turn starts after the model has spoken 1 to 3 units, one third). A third timing, two user lines
    joined by a 1 to 2 s pause, is not used: the untrained model starts answering in that pause, while the delegation
    data teaches it to keep listening through pauses. It records what each unit was fed and what the model produced, and saves the sequence in
    build_sequences.py's format, with loss on every token the model produced.

    python self_distill.py prepare --dailydialog train.parquet -n 3500 --out sd/
    python self_distill.py run --src <snapshot> --scripts sd/scripts.jsonl --manifest sd/manifest_*.jsonl \
        --out sd/seqs --shard 0/2

The same loop measures duplex behavior after training: prepare held-out conversations from DailyDialog's validation
split (--prefix sdeval --split test), run them with and without --lora, and compare filter_selfdistill.py's counts.
"""
import argparse
import json
import random
import re
from pathlib import Path

import numpy as np

SR = 16000


def clean(u):
    u = re.sub(r"\s+([,.?!;:'])", r"\1", u.strip())
    u = re.sub(r"([.?!,])(?=[A-Za-z])", r"\1 ", u)
    return re.sub(r"\s+", " ", u).replace(" ' ", "'").replace(" n't", "n't")


def prepare(args):
    import pyarrow.parquet as pq
    rows = pq.read_table(args.dailydialog).to_pylist()
    dialogs = {}
    for r in rows:
        dialogs.setdefault(r["dialog_id"], []).append(clean(r["utterance"]))
    rng = random.Random(args.seed)
    ids = sorted(dialogs)
    rng.shuffle(ids)
    out = []
    for d in ids:
        users = [u for u in dialogs[d][0::2] if 3 <= len(u.split()) <= 40][:4]
        if len(users) < 2:
            continue
        timing = rng.choices(["turns", "barge_in"], weights=[2, 1])[0]
        out.append({"id": f"{args.prefix}-{len(out):05d}", "split": args.split, "target": "selfdistill", "timing": timing,
                    "events": [{"role": "user", "text": u} for u in users]})
        if len(out) == args.n:
            break
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scripts.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(f"{len(out)} conversations -> {args.out / 'scripts.jsonl'}")


class Tap:
    """Wraps the duplex decoder's feed: runs it as usual and keeps the last audio embeddings fed."""
    def __init__(self, decoder):
        self.audio, self.inner = None, decoder.feed
        decoder.feed = self.feed

    def feed(self, embeds, return_logits=False):
        if embeds.dim() == 2 and embeds.shape[0] > 1:
            self.audio = embeds.detach().to("cpu")
        return self.inner(embeds, return_logits=return_logits)


def run(args):
    import soundfile as sf
    import torch
    from transformers import AutoModel, AutoTokenizer
    n_gpu = torch.cuda.device_count()
    model = AutoModel.from_pretrained(args.src, trust_remote_code=True, attn_implementation="sdpa", torch_dtype=torch.bfloat16,
                                      init_vision=False, init_audio=True, init_tts=True, device_map="auto",
                                      max_memory={g: "14GiB" for g in range(n_gpu)}).eval()
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    if args.lora:
        from duplex_test import merge_lora
        merge_lora(model.llm, args.lora)
    model.init_tts = lambda *a, **k: None
    duplex = model.as_duplex(generate_audio=False)
    tap = Tap(duplex.decoder)
    unit_id, unit_end = tok.convert_tokens_to_ids("<unit>"), tok.convert_tokens_to_ids("</unit>")
    turn_eos = tok.convert_tokens_to_ids("<|turn_eos|>")

    scripts = [json.loads(l) for l in args.scripts.read_text(encoding="utf-8").splitlines()]
    wavs = {m["id"]: m["wavs"] for p in args.manifest for m in map(json.loads, p.read_text(encoding="utf-8").splitlines())}
    i_shard, n_shard = map(int, args.shard.split("/"))
    args.out.mkdir(parents=True, exist_ok=True)
    for s in scripts[i_shard::n_shard][: args.limit or None]:
        if (args.out / f"{s['id']}.pt").exists() or s["id"] not in wavs:
            continue
        rng = random.Random(s["id"])
        torch.manual_seed(rng.randrange(2**31))
        system = "Streaming Omni Conversation."
        duplex.prepare(prefix_system_prompt=system)
        ids = tok.encode("<|im_start|>system\n" + system + "<|im_end|>", add_special_tokens=False)
        labels = [-100] * len(ids)
        audio, audio_at = [], []
        turns = [sf.read(w["path"], dtype="float32")[0] for w in wavs[s["id"]]]
        if s["timing"] == "long_turn":  # two lines said as one turn, with a pause between
            gap = np.zeros(int(rng.uniform(1.0, 2.0) * SR), np.float32)
            turns = [np.concatenate([turns[0], gap, turns[1]])] + turns[2:]

        def unit(chunk):
            before = len(duplex.total_ids)
            if not duplex.streaming_prefill(audio_waveform=chunk)["success"]:
                return None
            duplex.streaming_generate()
            produced = [t for t in duplex.total_ids[before:] if t != unit_end]
            ids.append(unit_id)
            labels.append(-100)
            audio_at.append(len(ids))
            audio.append(tap.audio.to(torch.bfloat16))
            ids.extend([0] * tap.audio.shape[0])
            labels.extend([-100] * tap.audio.shape[0])
            ids.extend(produced)
            labels.extend(produced)
            ids.append(unit_end)
            labels.append(-100)
            return produced

        silence = np.zeros(SR, np.float32)
        units = 0
        for k, speech in enumerate(turns):
            n = int(np.ceil(len(speech) / SR))
            speech = np.concatenate([speech, np.zeros(n * SR - len(speech), np.float32)])
            for i in range(n):
                unit(speech[i * SR:(i + 1) * SR])
                units += 1
            # the model's reply: wait for it to start, then either let it finish or cut in
            last = k == len(turns) - 1
            cut_after = rng.randint(1, 3) if s["timing"] == "barge_in" and not last else None
            spoken, waited = 0, 0
            while waited < args.max_wait and units < args.max_units:
                produced = unit(silence)
                units += 1
                if produced is None:
                    break
                spoken += duplex.speak_token_id in produced
                waited += 1
                if turn_eos in produced or (cut_after and spoken >= cut_after):
                    break
            unit(silence)  # one quiet second before the next turn (or the end)
            units += 1
        torch.save({"ids": ids, "labels": labels, "audio": audio, "audio_at": audio_at,
                    "timing": s["timing"]}, args.out / f"{s['id']}.pt")
        print(s["id"], s["timing"], len(ids), "tokens", len(audio), "units", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--dailydialog", type=Path, required=True)
    p.add_argument("-n", type=int, default=3500)
    p.add_argument("--seed", type=int, default=20261030)
    p.add_argument("--prefix", default="sd")
    p.add_argument("--split", default="train")
    p.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--src", type=Path, required=True)
    r.add_argument("--scripts", type=Path, required=True)
    r.add_argument("--manifest", type=Path, nargs="+", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--shard", default="0/1")
    r.add_argument("--lora", type=Path, help="merge this train_lora.py adapter first (behavior eval)")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--max-wait", type=int, default=12, help="seconds of silence to wait for the reply to end")
    r.add_argument("--max-units", type=int, default=120)
    args = ap.parse_args()
    prepare(args) if args.cmd == "prepare" else run(args)


if __name__ == "__main__":
    main()
