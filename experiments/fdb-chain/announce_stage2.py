"""Stage 2: what the frontend says around a handoff, on items it delegated in a run_chain.py result file.

Before the result: the frontend's speech in the run itself. spawn_thinking only answers "accepted" (with a task_id
and an explanation in -tid runs),
so anything specific it says about the outcome is made up.
After the result: the run is replayed up to that point, the backend's summary is handed back as a second tool
response, and the frontend's next reply is recorded.

A Qwen3-14B judge marks the speech before the result as fabricated or not, and the reply after it for whether it
conveys the result and states only facts the result supports. Writes <out>.jsonl and <out>.md.

    python experiments/fdb-chain/announce_stage2.py --run results/fdb-delegate-strict.jsonl --variant fdb-delegate-strict \
        --fdb-v3-dir <v3> --data-dir <fdb_v3_data_released> --harness-json <private export> \
        --judge-url http://127.0.0.1:8200/v1 --out results/stage2 --gpus 0,1
"""
import argparse
import json
import re
import urllib.request
from pathlib import Path

from fdb_tools import load_scenarios
from run_chain import TOOL_CALL, MiniCPMOFrontend, clean, frontend_system, load_items, tool_response

PRE_JUDGE = """A voice assistant handed the user's request to a backend agent and has NOT received any result yet.
User request: {request}
What the assistant said to the user while waiting: {speech}

Does the assistant state a specific outcome of the request as if it were already known, for example a status, a
price, an available item, or that a booking or change is done? Saying it will check, is checking, or has started
the task is not an outcome.
Reply with only JSON: {{"fabricated": true/false, "reason": "short"}}"""

POST_JUDGE = """A voice assistant handed the user's request to a backend agent, which has now finished.
User request: {request}
Backend result: {result}
Tool results the backend saw: {tools}
What the assistant then said to the user: {reply}

1. conveys: does the reply tell the user the main result?
2. faithful: is every specific fact in the reply (status, price, item, number) supported by the backend result or
   the tool results?
Reply with only JSON: {{"conveys": true/false, "faithful": true/false, "reason": "short"}}"""


def speech(raw):
    return clean(TOOL_CALL.sub("", raw or ""))


def judge(url, model, prompt):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 200,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = json.loads(r.read())["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {"error": text}
    except json.JSONDecodeError:
        return {"error": text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True, help="a run_chain.py result file")
    ap.add_argument("--variant", required=True, help="the variant that produced --run (rebuilds its system prompt)")
    ap.add_argument("--fdb-v3-dir", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--harness-json", type=Path, required=True)
    ap.add_argument("--judge-url", required=True)
    ap.add_argument("--judge-model", default="qwen3-14b")
    ap.add_argument("--out", type=Path, required=True, help="output path without extension")
    ap.add_argument("--model", default="openbmb/MiniCPM-o-4_5")
    ap.add_argument("--frontend", choices=["minicpmo", "funaudio"], default="minicpmo")
    ap.add_argument("--funaudio-repo", default="Fun-Audio-Chat")
    ap.add_argument("--funaudio-model", default="FunAudioLLM/Fun-Audio-Chat-8B")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    runs = {r["id"]: r for r in map(json.loads, args.run.read_text(encoding="utf-8").splitlines()) if r.get("handoffs")}
    if args.limit:
        runs = dict(sorted(runs.items())[: args.limit])
    harness = json.loads(args.harness_json.read_text(encoding="utf-8"))
    system = frontend_system(args.variant, harness)
    scenarios = load_scenarios(args.fdb_v3_dir / "benchmark_data_v2.json")
    loader = argparse.Namespace(context="text", fake=False, data_dir=args.data_dir, variant=args.variant, asr=None)
    items = {it["id"]: it for it in load_items(loader, scenarios) if it["id"] in runs}
    if args.frontend == "funaudio":
        from funaudio_frontend import FunAudioFrontend
        frontend = FunAudioFrontend(args)
    else:
        frontend = MiniCPMOFrontend(args)

    rows = []
    for item_id, run in sorted(runs.items()):
        item, front = items[item_id], run["frontend"]
        request = item["text"]
        before = " ".join(s for s in (speech(f["raw"]) for f in front) if s)
        handoff = run["handoffs"][-1]
        result = handoff["summary"] or "(the backend returned no summary)"

        # Replay the run up to the point the backend finishes, then hand its summary back. Each handoff gets the
        # reply it got in the run.
        replies = [h.get("reply", {"status": "accepted"}) for h in run["handoffs"]]
        msgs = [{"role": "system", "content": [system]}]
        msgs += [{"role": t["role"], "content": [t["text"]]} for t in item["context"]]
        msgs.append({"role": "user", "content": [item["audio"]]})
        for f in front:
            msgs.append({"role": "assistant", "content": [clean(f["raw"])]})
            if f["calls"]:
                msgs.append({"role": "user", "content": [tool_response(replies.pop(0) if replies else {"status": "accepted"})]})
        done = {"status": "completed", "result": result}
        if "task_id" in handoff.get("reply", {}):
            done = {"status": "completed", "task_id": handoff["reply"]["task_id"], "result": result}
        msgs.append({"role": "user", "content": [tool_response(done)]})
        raw, _, _ = frontend(msgs, item)
        after = speech(raw)

        tools = json.dumps([{"function": c["function"], "result": c["result"]} for c in run["calls"]])
        row = {"id": item_id, "request": request, "before": before, "result": result, "after": after,
               "after_raw": raw,
               "pre": judge(args.judge_url, args.judge_model, PRE_JUDGE.format(request=request, speech=before or "(silence)")),
               "post": judge(args.judge_url, args.judge_model,
                             POST_JUDGE.format(request=request, result=result, tools=tools, reply=after or "(silence)"))}
        rows.append(row)
        print(item_id, "| fabricated:", row["pre"].get("fabricated"), "| conveys:", row["post"].get("conveys"),
              "| faithful:", row["post"].get("faithful"))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                              encoding="utf-8")
    n = len(rows)
    count = lambda part, key: sum(1 for r in rows if r[part].get(key) is True)  # noqa: E731
    calls_again = sum(1 for r in rows if TOOL_CALL.search(r["after_raw"] or ""))
    lines = [f"## Stage 2: speech around a handoff ({args.run.name}, {n} delegated items)", "",
             "| measure | items |", "| --- | --- |",
             f"| before the result: states a made-up outcome | {count('pre', 'fabricated')}/{n} |",
             f"| after the result: tells the user the result | {count('post', 'conveys')}/{n} |",
             f"| after the result: every fact supported | {count('post', 'faithful')}/{n} |",
             f"| after the result: calls a tool again | {calls_again}/{n} |", "", "Made-up outcomes before the result:", ""]
    lines += [f"- {r['id']}: {r['before'][:200]}" for r in rows if r["pre"].get("fabricated") is True]
    lines += ["", "Unsupported facts after the result:", ""]
    lines += [f"- {r['id']}: result: {r['result'][:120]} | said: {r['after'][:160]}" for r in rows
              if r["post"].get("faithful") is False]
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:9]))


if __name__ == "__main__":
    main()
