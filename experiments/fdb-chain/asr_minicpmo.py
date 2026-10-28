"""Transcribe the last user turn of each FDB-v3 item with MiniCPM-o itself, the way the frontend hears it.

Writes one {"id", "asr"} line per item; run_chain.py --asr reads the file (fdb-delegate-words, backend-asr).
The audio is trimmed at the end of speech exactly as run_chain.py does for the frontend.

    python experiments/fdb-chain/asr_minicpmo.py --data-dir <fdb_v3_data_released> --out results/asr.jsonl --gpus 0,1
"""
import argparse
import json
from pathlib import Path

from run_chain import speech_end

PROMPT = "Please listen to the audio carefully and transcribe exactly what the speaker says. Output only the transcript."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="openbmb/MiniCPM-o-4_5")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    args = ap.parse_args()

    import librosa
    from minicpmo_delegate_tool import load_model
    model, tokenizer = load_model(args.model, "bf16", [int(g) for g in args.gpus.split(",")], args.max_mem_gib)
    done = set()
    if args.out.exists():  # resume
        done = {json.loads(l)["id"] for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as f:
        for d in sorted(p for p in args.data_dir.iterdir() if (p / "metadata.json").exists()):
            if d.name in done:
                continue
            audio, _ = librosa.load(d / "input.wav", sr=16000, mono=True)
            audio = audio[: speech_end(audio) * 16000]
            out = model.chat(msgs=[{"role": "user", "content": [PROMPT, audio]}], tokenizer=tokenizer, do_sample=False,
                             max_new_tokens=256, use_tts_template=False, generate_audio=False, omni_mode=False,
                             enable_thinking=False)
            text = out if isinstance(out, str) else getattr(out, "text", str(out))
            f.write(json.dumps({"id": d.name, "asr": text.strip()}, ensure_ascii=False) + "\n")
            f.flush()
            print(d.name, "|", text.strip()[:100])
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
