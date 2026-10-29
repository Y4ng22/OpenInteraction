"""Pick the duplex feasibility samples and speak their user turns with Kokoro (16 kHz mono WAV).

Train: 200 samples from the train split (call 60, handoff 50, ask 30, split 30, chat 30). Test: 60 from the test
split (12 per target, held-out domains). Only samples without earlier context turns, without ISO dates in the
request and without the first-round ask rewrites (to be dropped). Each user turn gets its own WAV; one voice per
sample, picked at random from Kokoro's English voices.

    python tts.py --final delegation_final.jsonl --out /local/data/xy2764/delegation-train/duplex
"""
import argparse
import json
import random
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

PLAN = {"train": {"call": 60, "handoff": 50, "ask": 30, "split": 30, "chat": 30},
        "test": {"call": 12, "handoff": 12, "ask": 12, "split": 12, "chat": 12}}
VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_aoede", "af_kore", "am_adam", "am_michael",
          "am_fenrir", "am_puck", "am_echo", "bf_emma", "bf_isabella", "bf_alice", "bm_george", "bm_lewis", "bm_daniel"]
RESULTS_PLAN = {"test": {"call": 12, "handoff": 12, "split": 10}}
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def usable(x):
    users = [e["text"] for e in x["events"] if e["role"] == "user"]
    return (not any(e.get("context") for e in x["events"]) and x.get("fix") != "ask_lookup"
            and not any(ISO_DATE.search(u) for u in users))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--results-test", action="store_true")
    args = ap.parse_args()
    plan = RESULTS_PLAN if args.results_test else PLAN
    has_result = lambda x: any(e["role"] in ("tool", "backend") for e in x["events"])

    from kokoro import KPipeline
    pipe = {"a": KPipeline(lang_code="a"), "b": KPipeline(lang_code="b")}
    rng = random.Random(args.seed)
    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
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
                    audio = np.concatenate([r.audio.numpy() for r in pipe[voice[0]](text, voice=voice)])
                    audio = resample_poly(audio, 2, 3).astype(np.float32)  # 24 kHz -> 16 kHz
                    path = args.out / "audio" / f"{x['id']}_{k}.wav"
                    if path.exists():  # spoken already for the first test; same voice is not needed
                        wavs.append({"path": str(path), "seconds": round(sf.info(path).duration, 2), "text": text})
                        continue
                    sf.write(path, audio, 16000)
                    wavs.append({"path": str(path), "seconds": round(len(audio) / 16000, 2), "text": text})
                manifest.append({"id": x["id"], "split": split, "target": target, "voice": voice, "wavs": wavs})
                print(x["id"], target, voice, [w["seconds"] for w in wavs], flush=True)
    name = "manifest_results.jsonl" if args.results_test else "manifest.jsonl"
    (args.out / name).write_text("".join(json.dumps(m) + "\n" for m in manifest), encoding="utf-8")
    print(f"{len(manifest)} samples -> {args.out / name}")


if __name__ == "__main__":
    main()
