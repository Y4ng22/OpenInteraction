"""Validate cases.jsonl and synthesize one 16 kHz mono WAV per utterance.

Uses the macOS `say` voices, so run it on a Mac and copy the audio
directory to the server. Only the utterance is spoken; context turns are
passed to the model as text history.

    python3 synthesize.py            # validate and synthesize into audio/
    python3 synthesize.py --check    # validate only
"""

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATEGORIES = {"self", "delegate", "edge"}
EXPECTED = {"self", "delegate", "clarify"}
SCENARIOS = {
    "daily_chat", "knowledge", "weather", "travel", "food", "shopping_finance", "health",
    "work_study", "tech", "entertainment", "news", "home_family", "sports",
}
VOICES = {"zh": "Tingting", "en": "Samantha"}


def load_cases(path):
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            cases.append((number, json.loads(line)))
    return cases


def validate(cases):
    errors, seen = [], set()
    for number, case in cases:
        where = f"line {number} ({case.get('id')})"
        if case.get("id") in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(case.get("id"))
        if case.get("category") not in CATEGORIES:
            errors.append(f"{where}: bad category")
        if case.get("expected") not in EXPECTED:
            errors.append(f"{where}: bad expected")
        if case.get("scenario") not in SCENARIOS:
            errors.append(f"{where}: bad scenario")
        if case.get("lang") not in VOICES:
            errors.append(f"{where}: bad lang")
        if not str(case.get("utterance") or "").strip():
            errors.append(f"{where}: empty utterance")
        if (case.get("expected") == "delegate") != bool(case.get("reference_objective")):
            errors.append(f"{where}: reference_objective is required exactly when expected is delegate")
        for turn in case.get("context") or []:
            if turn.get("role") not in {"user", "assistant"} or not turn.get("text"):
                errors.append(f"{where}: bad context turn")
        if case.get("category") != "edge" and case.get("category") != case.get("expected"):
            errors.append(f"{where}: non-edge category must equal expected")
    return errors


def synthesize(case, out_dir):
    target = out_dir / f"{case['id']}.wav"
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "speech.aiff"
        subprocess.run(["say", "-v", VOICES[case["lang"]], "-o", str(aiff), case["utterance"]], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(target)], check=True)
    with wave.open(str(target)) as audio:
        seconds = audio.getnframes() / audio.getframerate()
    return {"id": case["id"], "audio": target.name, "seconds": round(seconds, 2),
            "voice": VOICES[case["lang"]], "sample_rate": 16000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--out", type=Path, default=HERE / "audio")
    parser.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    errors = validate(cases)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    counts = {}
    for _, case in cases:
        counts[case["expected"]] = counts.get(case["expected"], 0) + 1
    print(f"{len(cases)} cases valid; expected: {counts}")
    if args.check:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = [synthesize(case, args.out) for _, case in cases]
    (args.out / "manifest.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in manifest), encoding="utf-8")
    print(f"wrote {len(manifest)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
