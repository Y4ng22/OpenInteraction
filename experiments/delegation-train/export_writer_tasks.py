"""Export the delegation sample plans as a writing task for any outside model.

Writes tasks.jsonl (one plan per line, with only the tool definitions the writer needs) and WRITER_GUIDE.md (the
rules and the JSON each task type must return). The writer's answers come back as a JSONL file of
{"id": ..., <fields>} lines and go through the same checks as make_requests_qwen.py (import_writer_answers.py).
No harness text is exported: the writer only sees "the backend" by name.

    python export_writer_tasks.py --tools tools_split.jsonl --time tool_time_reviewed.jsonl --out writer_tasks/
"""
import argparse
import hashlib
import json
from pathlib import Path

from make_requests_qwen import SIZES, load_tools, plan

GUIDE = """# Writing guide: voice assistant delegation samples

Each line of `tasks.jsonl` is one task. Write the JSON object asked for by its `type` and return one line per task:
`{"id": "<task id>", ...fields...}`. Return JSON only.

## The setting

A voice assistant can call a few tools itself (`assistant_tools`). For anything else it hands the request to a
**backend** agent with many more tools; the backend capability a task needs is given in `backend_needs`. The
assistant never says it cannot do something: what it cannot do itself, it hands to the backend.

## Write every sample yourself

Write each sample individually, as a person would. **Do not write or run a program, template or fill-in pattern**
to produce them: answers built that way are rejected (the first batch was: 47% of requests contained the tool
name, there were only 6 different context lines and 4 different acknowledgments, and ids like 4821 repeated
thousands of times). Work in small batches and keep every line different.

## Rules for every task

- The user talks the way people talk to a voice assistant: casual, one or two sentences, with concrete values
  (names, places, dates, amounts, ids). Never mention tool or function names and never reword a tool description.
  - Bad: "Create a personalized workout schedule based on user preferences and fitness goals."
  - Good: "I want to build some muscle, I can get to the gym three times a week. Can you put a plan together?"
- Never say the tool's name or a rewording of it ("cruise ship capacity planner", "eco system impact forecast").
  Say what the person actually wants ("How many crew would we need for a 2,000-passenger, 10-day cruise?").
- No placeholders such as `$USER_ID`, `<id>`, `{date}`, and no filler values such as "standard", "default",
  "test" or "example": invent realistic, varied values (different ids, names, cities, dates, amounts each time).
- No two requests may be the same, and dev/test requests must not repeat training ones.
- Vary the wording: openings, context lines, acknowledgments and questions should all differ from sample to
  sample, not follow a few fixed phrasings.
- Argument values in `calls` must come from what the user says (in the request, the context, or the answer).
  Leave out optional arguments the user did not mention. In a second call, write `"$RESULT_1"` for a value that can
  only come from the first call's result.
- `backend_calls` may contain values the user did not give (the backend looks them up), but `objective` never does.
- `objective`: one sentence telling the backend what to do; it repeats **every value the user gave**, exactly as
  written (ids, numbers, names, dates).
- `say`: a short spoken acknowledgment after handing off. It must not claim the task is done (no "booked",
  "ordered", "done", "all set"...), must not say it cannot help, and must not mention a human agent or specialist.
- If `with_context` is true, add `context`: one or two earlier exchanges `[{"user": "...", "assistant": "..."}]`, a
  natural lead-in that neither gives the answer nor repeats the request. Otherwise `context` is `[]`.

## Fields per task type

`call` (the assistant's own tools cover it; `needs` lists them; two tools means two calls):
```
{"id", "context", "request", "calls": [{"name", "arguments"}]}
```
plus, when `full` is true: `"results"` (one plausible JSON result per call) and `"answer"` (what the assistant tells
the user from those results, using concrete values from them).

`handoff` (needs a backend capability; with two `backend_needs` entries, chain them with `$RESULT_1` only if the
second naturally needs the first's output; otherwise the user asks for both things and the calls are independent):
```
{"id", "context", "request", "backend_calls": [{"name", "arguments"}], "objective", "say"}
```
plus, when `full` is true: `"backend_result"` (the backend's final report, one or two sentences with concrete
outcomes) and `"answer"` (what the assistant then tells the user).

`split` (two unrelated requests in one turn: one for the assistant's own tool, one for the backend):
```
{"id", "context", "request", "calls": [...], "backend_calls": [...], "objective", "say"}
```
`objective` covers only the backend part.

`ask` (the user leaves out the required value `missing`, which only they can decide; the assistant asks for it,
then the user answers):
```
{"id", "context", "request", "question", "answer", "calls" or "backend_calls": [...]}
```
Use `backend_calls` when the needed tool is in `backend_needs`, and then also give `objective` and `say`. The
calls include the missing value from the answer. The request must not contain the missing value.

`chat` (no tool and no outside information needed: small talk, a joke, an opinion, stable general knowledge;
never weather, news, prices or schedules):
```
{"id", "context", "request", "reply"}
```
`reply` is brief and warm and never says what the assistant cannot do.
"""


SPEECH_FROM = 200  # batches 000-001 were written before speech styles existed
BATCH = 100


def speech_style(index, task_id, target):
    """How the user talks: 'plain', 'filler' (um, uh: input only) or 'self_correction' (the user changes a value
    mid-sentence, so the targets must use the corrected one). About 10% filler and 6% self-correction, fixed by id."""
    if index < SPEECH_FROM:
        return "plain"
    r = int(hashlib.md5(task_id.encode()).hexdigest(), 16) % 100
    if r < 6 and target != "chat":
        return "self_correction"
    return "filler" if r < 16 else "plain"


def write_batches(out):
    """Split tasks.jsonl into batches of 100 and pair each with PROMPT.md as a ready-to-paste prompt."""
    lines = (out / "tasks.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    text = (out / "PROMPT.md").read_text(encoding="utf-8")
    head = text[: text.index("## Tasks") + len("## Tasks\n\n")]
    (out / "batches").mkdir(exist_ok=True)
    (out / "prompts").mkdir(exist_ok=True)
    for k in range(0, len(lines), BATCH):
        batch = "".join(lines[k:k + BATCH])
        (out / "batches" / f"batch_{k // BATCH:03d}.jsonl").write_text(batch, encoding="utf-8")
        (out / "prompts" / f"prompt_{k // BATCH:03d}.md").write_text(head + batch, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--time", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261028)
    args = ap.parse_args()

    tools = load_tools(args.tools, args.time)
    show = lambda i: {k: tools[i][k] for k in ("name", "description", "parameters")}
    args.out.mkdir(parents=True, exist_ok=True)
    n = 0
    with (args.out / "tasks.jsonl").open("w", encoding="utf-8") as f:
        for k, (split, size) in enumerate(SIZES.items()):
            for p in plan(tools, split, size, args.seed + k):
                task = {"id": p["id"], "type": p["target"], "with_context": p["context"], "full": p["full"],
                        "assistant_tools": [tools[i]["name"] for i in p["listed"]],
                        "needs": [show(i) for i in p["fast_need"]],
                        "backend_needs": [show(i) for i in p["slow_need"]]}
                if p["target"] == "ask":
                    task["missing"] = p["missing"]
                style = speech_style(n, p["id"], p["target"])
                if style != "plain":
                    task["speech"] = style
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
                n += 1
    (args.out / "WRITER_GUIDE.md").write_text(GUIDE, encoding="utf-8")
    if (args.out / "PROMPT.md").exists():
        write_batches(args.out)
    print(f"wrote {n} tasks to {args.out}")


if __name__ == "__main__":
    main()
