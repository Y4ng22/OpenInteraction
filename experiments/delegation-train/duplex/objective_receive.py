"""Score the frontend's turns on BFCL-Single: its own tool calls as they are, its objectives through Qwen3-14B.

Same request as Matthew's cascade baseline (experiments/asr-cascade/run_cascade.py): the item's tool list in the system
prompt, thinking off, temperature 0, 512 tokens. A turn with neither a call nor an objective has an empty prediction
and counts as wrong. qwen-gold sends the correct text of every item, as the ceiling. Scored by Fun-Audio-Chat's
SpeechFCEval scorer; correct counts come out per tool speed.

    python objective_receive.py --objectives ../latent/objective_dev.jsonl --data <SpeechFCEval>/SpeechFC_BFCL_Single \
        --scorer <Fun-Audio-Chat>/evaluate/SpeechFCEval --out ../latent/results
"""
import argparse
import json
import sys
import types
import urllib.request
from pathlib import Path

DATASET = "SpeechFC-BFCL-Single"
TOOLS_PROMPT = """# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{tools}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": <function-name>, "arguments": <args-json-object>}}
</tool_call>"""


def ask(url, item, text):
    system = TOOLS_PROMPT.format(tools="\n".join(json.dumps(t, ensure_ascii=False) for t in json.loads(item["function"])))
    if item.get("time"):
        system += "\n\n" + item["time"]
    body = {"model": "qwen3-14b", "temperature": 0.0, "max_tokens": 512, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"]["content"] or ""


def own_calls(row):
    """The frontend's own well-formed calls, in the layout Qwen3-14B writes them."""
    return "\n".join(f"<tool_call>\n{c.strip()}\n</tool_call>" for c in row["raw_calls"]
                     if '"spawn_thinking"' not in c and n_ok(c))


def n_ok(call):
    try:
        return bool(json.loads(call.strip()).get("name"))
    except (ValueError, AttributeError):
        return False


def turn(url, item, row):
    backend = ask(url, item, row["objective"]) if row["objective"] else ""
    return "\n".join(p for p in (own_calls(row), backend) if p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--objectives", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--scorer", type=Path, required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1/chat/completions")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    sys.path.insert(0, str(args.scorer))
    sys.modules.setdefault("xlsxwriter", types.ModuleType("xlsxwriter"))  # only the scorer's spreadsheet uses it
    from utils import compute_metrics_bfcl as bfcl
    bfcl.output_record_table_v1 = lambda *a: None

    meta = {r["index"]: r for r in map(json.loads, (args.data / "metadata.jsonl").read_text(encoding="utf-8").splitlines()) if r}
    rows = [json.loads(l) for l in args.objectives.read_text(encoding="utf-8").splitlines()]
    runs = [("qwen-gold", lambda r, item: ask(args.url, item, r["utterance"])), ("frontend", lambda r, item: turn(args.url, item, r))]
    for name, predict in runs:
        out = args.out / name / DATASET / f"predict-{name}-{DATASET}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                item = meta[r["index"]]
                f.write(json.dumps({**item, "gold_speed": r["gold_speed"], "input_text": r["objective"], "predict": predict(r, item)}, ensure_ascii=False) + "\n")
        print("==", name, flush=True)
        bfcl.compute_metrics_bfcl_v1(name, DATASET, "multiple", str(args.out) + "/")


if __name__ == "__main__":
    main()
