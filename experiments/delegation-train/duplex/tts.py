"""Speak the user turns with Kokoro (16 kHz mono WAV), for the feasibility test or for the whole set.

Feasibility test (default): 200 train samples (call 60, handoff 50, ask 30, split 30, chat 30) and 60 test samples
(12 per target), without earlier context turns, ISO dates or first-round ask rewrites. --results-test: 34 test
samples that carry a tool or backend result (call 12, handoff 12, split 10), written to manifest_results.jsonl.

--all: every sample of --final, every user turn including earlier context turns, in shards (--shard i/n, one
manifest per shard). ISO dates are read as spoken dates ("2026-10-27" -> "October 27, 2026"), and about 30% of
requests get one pause of 0.5 to 2 s at a phrase boundary (DATA_SPEC 1, "How the user talks"), so the model learns
to keep listening. One voice per sample from 18 English voices, chosen from the sample id.

    python tts.py --final delegation_final.jsonl --out /local/data/xy2764/delegation-train/duplex
    python tts.py --all --shard 0/4 --final delegation_v3.jsonl --out /local/data/xy2764/delegation-train/duplex_full
"""
import argparse
import hashlib
import json
import random
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

PLAN = {"train": {"call": 60, "handoff": 50, "ask": 30, "split": 30, "chat": 30},
        "test": {"call": 12, "handoff": 12, "ask": 12, "split": 12, "chat": 12}}
RESULTS_PLAN = {"test": {"call": 12, "handoff": 12, "split": 10}}
VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_aoede", "af_kore", "am_adam", "am_michael",
          "am_fenrir", "am_puck", "am_echo", "bf_emma", "bf_isabella", "bf_alice", "bm_george", "bm_lewis", "bm_daniel"]
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
PAUSE_SHARE, PAUSE_S = 0.3, (0.5, 2.0)
SR = 16000


def usable(x):
    users = [e["text"] for e in x["events"] if e["role"] == "user"]
    return (not any(e.get("context") for e in x["events"]) and x.get("fix") != "ask_lookup"
            and not any(ISO_DATE.search(u) for u in users))


def spoken_form(text):
    def date(m):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{MONTHS[mo - 1]} {d}, {y}" if 1 <= mo <= 12 and 1 <= d <= 31 else m.group(0)
    return ISO_DATE.sub(date, text)


def synth(pipe, voice, text):
    audio = np.concatenate([r.audio.numpy() for r in pipe[voice[0]](text, voice=voice)])
    return resample_poly(audio, 2, 3).astype(np.float32)  # 24 kHz -> 16 kHz


def pause_split(text, rng):
    """Split at one phrase boundary (after a comma, or between sentences), or None if there is none."""
    cuts = [m.end() for m in re.finditer(r"[,;:.?!]\s", text) if 8 <= m.end() <= len(text) - 8]
    if not cuts:
        return None
    at = rng.choice(cuts)
    return text[:at].strip(), text[at:].strip()


def speak_all(args, X, pipe):
    i, n = map(int, args.shard.split("/"))
    X = [x for x in X if int(hashlib.md5(x["id"].encode()).hexdigest(), 16) % n == i]
    (args.out / "audio").mkdir(parents=True, exist_ok=True)
    manifest = []
    for x in X:
        rng = random.Random(x["id"])
        voice = rng.choice(VOICES)
        wavs = []
        for k, e in enumerate(e for e in x["events"] if e["role"] == "user"):
            path = args.out / "audio" / f"{x['id']}_{k}.wav"
            text = spoken_form(e["text"])
            parts = pause_split(text, rng) if not e.get("context") and rng.random() < PAUSE_SHARE else None
            pause = round(rng.uniform(*PAUSE_S), 2) if parts else 0.0
            if not path.exists():
                audio = (synth(pipe, voice, text) if not parts else
                         np.concatenate([synth(pipe, voice, parts[0]), np.zeros(int(pause * SR), np.float32),
                                         synth(pipe, voice, parts[1])]))
                sf.write(path, audio, SR)
            wavs.append({"path": str(path), "seconds": round(sf.info(path).duration, 2), "text": text,
                         "context": bool(e.get("context")), "pause_s": pause})
        manifest.append({"id": x["id"], "split": x["split"], "target": x["target"], "voice": voice, "wavs": wavs})
        if len(manifest) % 200 == 0:
            print(f"{len(manifest)}/{len(X)}", flush=True)
    name = f"manifest_{i}.jsonl"
    (args.out / name).write_text("".join(json.dumps(m) + "\n" for m in manifest), encoding="utf-8")
    print(f"{len(manifest)} samples -> {args.out / name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--results-test", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--shard", default="0/1")
    args = ap.parse_args()

    from kokoro import KPipeline
    pipe = {"a": KPipeline(lang_code="a"), "b": KPipeline(lang_code="b")}
    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
    if args.all:
        return speak_all(args, X, pipe)

    plan = RESULTS_PLAN if args.results_test else PLAN
    has_result = lambda x: any(e["role"] in ("tool", "backend") for e in x["events"])
    rng = random.Random(args.seed)
    (args.out / "audio").mkdir(parents=True, exist_ok=True)
    manifest = []
    for split, want in plan.items():
        for target, n in want.items():
            pool = [x for x in X if x["split"] == split and x["target"] == target and usable(x)
                    and (has_result(x) or not args.results_test)]
            for x in rng.sample(pool, n):
                voice = rng.choice(VOICES)
                wavs = []
                for k, text in enumerate(e["text"] for e in x["events"] if e["role"] == "user"):
                    path = args.out / "audio" / f"{x['id']}_{k}.wav"
                    if not path.exists():  # spoken already for the first test
                        sf.write(path, synth(pipe, voice, text), SR)
                    wavs.append({"path": str(path), "seconds": round(sf.info(path).duration, 2), "text": text})
                manifest.append({"id": x["id"], "split": split, "target": target, "voice": voice, "wavs": wavs})
                print(x["id"], target, voice, [w["seconds"] for w in wavs], flush=True)
    name = "manifest_results.jsonl" if args.results_test else "manifest.jsonl"
    (args.out / name).write_text("".join(json.dumps(m) + "\n" for m in manifest), encoding="utf-8")
    print(f"{len(manifest)} samples -> {args.out / name}")


if __name__ == "__main__":
    main()
