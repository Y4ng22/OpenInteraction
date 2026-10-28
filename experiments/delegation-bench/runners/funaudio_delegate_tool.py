"""Run the delegation cases with Fun-Audio-Chat-8B as the frontend instead of MiniCPM-o.

Same prompt, cases, audio and output fields as minicpmo_delegate_tool.py, so score.py scores the result as is.
The model wrapper is experiments/fdb-chain/funaudio_frontend.py.

    python experiments/delegation-bench/runners/funaudio_delegate_tool.py --prompt harness \
        --harness-json <private export> --funaudio-repo <Fun-Audio-Chat checkout> --gpus 0,1
"""
import argparse
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE.parent / "fdb-chain"))
from minicpmo_delegate_tool import build_prompt, parse_reply  # noqa: E402
from funaudio_frontend import FunAudioFrontend  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--funaudio-repo", required=True, help="checkout of github.com/FunAudioLLM/Fun-Audio-Chat")
    ap.add_argument("--funaudio-model", default="FunAudioLLM/Fun-Audio-Chat-8B")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--prompt", choices=["harness", "rules", "minimal"], default="harness")
    ap.add_argument("--harness-json", type=Path, help="private export from dump_harness_prompt.mjs")
    ap.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    ap.add_argument("--audio-dir", type=Path, default=HERE / "audio")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--results", type=Path, default=HERE / "results")
    args = ap.parse_args()
    if args.prompt == "harness" and not args.harness_json:
        ap.error("--prompt harness needs --harness-json")

    import librosa
    system, handoff, offered = build_prompt(args.prompt, args.harness_json)
    cases = [json.loads(l) for l in args.cases.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        cases = cases[: args.limit]
    label = args.prompt
    if args.prompt == "harness":
        suffix = args.harness_json.stem.removeprefix("harness_frontend").strip("_-")
        label = f"harness-{suffix}" if suffix else "harness"
    args.results.mkdir(parents=True, exist_ok=True)
    out_file = args.results / f"funaudiochat8b-{label}-text.jsonl"
    done = set()
    if out_file.exists():  # resume
        done = {json.loads(l)["id"] for l in out_file.read_text(encoding="utf-8").splitlines() if l.strip()}

    frontend = FunAudioFrontend(args)
    with out_file.open("a", encoding="utf-8") as f:
        for case in cases:
            if case["id"] in done:
                continue
            try:
                msgs = [{"role": "system", "content": [system]}]
                msgs += [{"role": t["role"], "content": [t["text"]]} for t in case["context"]]
                audio, _ = librosa.load(args.audio_dir / f"{case['id']}.wav", sr=16000, mono=True)
                msgs.append({"role": "user", "content": [audio]})
                raw, _, elapsed = frontend(msgs)
                record = {"id": case["id"], **parse_reply(raw, handoff, offered), "raw": raw,
                          "first_token_s": None, "latency_s": round(elapsed, 3), "audio_s": round(len(audio) / 16000, 2)}
            except Exception as e:  # recorded and scored as an error, then the run goes on
                traceback.print_exc()
                record = {"id": case["id"], "decision": "error", "tool": None, "objective": None,
                          "spoken": "", "tools_called": [], "raw": None, "error": f"{type(e).__name__}: {e}"}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{case['id']}] expected={case['expected']:8s} got={record['decision']:13s} | {(record['raw'] or '')[:100]!r}")
    print(f"wrote {out_file}")


if __name__ == "__main__":
    main()
