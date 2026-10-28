"""Ten speech-to-text samples in Fun-Audio-Chat's LLaMA-Factory format, only to check that training runs.

Taken from SpeechFCEval's BFCL single-call subset (an evaluation set): the weights a smoke test produces are thrown
away, never used for results. Each sample is the function-calling system prompt with the item's tools, the user's
recording, and the gold call as the assistant's <tool_call> text.

    python make_smoke.py --speechfc /local/data/xy2764/data/SpeechFCEval --fun-repo /local/data/xy2764/Fun-Audio-Chat \
        --out /local/data/xy2764/delegation-train/smoke
"""
import argparse
import json
import sys
from pathlib import Path

import soundfile as sf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speechfc", type=Path, required=True)
    ap.add_argument("--fun-repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("-n", type=int, default=10)
    args = ap.parse_args()

    sys.path.insert(0, str(args.fun_repo))
    from utils.constant import AUDIO_PAD_TOKEN, AUDIO_TEMPLATE, FUNCTION_CALLING_PROMPT, TOKEN_FPS

    subset = args.speechfc / "SpeechFC_BFCL_Single"
    rows = [json.loads(l) for l in (subset / "metadata.jsonl").read_text(encoding="utf-8").splitlines()[: args.n]]
    samples = []
    for r in rows:
        tools = [t["function"] for t in json.loads(r["function"])]
        name, arguments = next(iter(json.loads(r["ground_truth"])[0].items()))
        call = {"name": name, "arguments": {k: v[0] for k, v in arguments.items() if v and v[0] != ""}}
        wav = subset / r["file_name"]
        info = sf.info(str(wav))
        audio = {"path": str(wav.resolve()), "text": "", "ref_path": "", "ref_text": r["utterance"],
                 "token": AUDIO_PAD_TOKEN * int(info.frames / info.samplerate * TOKEN_FPS)}
        samples.append({
            "system": FUNCTION_CALLING_PROMPT.replace("{tools_definition}", "\n".join(json.dumps(t) for t in tools)),
            "messages": [{"role": "user", "content": AUDIO_TEMPLATE},
                         {"role": "assistant", "content": "<tool_call>\n" + json.dumps(call) + "\n</tool_call>"}],
            "audio": [json.dumps(audio, ensure_ascii=False, sort_keys=True)],
        })

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "smoke.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), encoding="utf-8")
    info = {"smoke": {"file_name": "smoke.jsonl", "formatting": "sharegpt",
                      "columns": {"system": "system", "messages": "messages", "audios": "audio"},
                      "tags": {"role_tag": "role", "content_tag": "content", "user_tag": "user", "assistant_tag": "assistant"}}}
    (args.out / "dataset_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"wrote {len(samples)} samples to {args.out}")


if __name__ == "__main__":
    main()
