# Delegation training: teaching the voice model when to hand off

**In short:** no public dataset labels "do it myself" versus "hand it to the backend", so we build one: 15,400
requests over 14,168 real tool definitions, with the voice model holding only fast tools (≤ 0.5 s) and handing everything else to the backend. This
follows how Qwen-Audio-Agent's cockpit app places tools (by hand, per domain), with expected time in place of
domain; the 0.5 s line is our own. Each tool is labeled fast or slow, each sample's target is
fixed first, and an outside model writes a request that fits it. All 154 batches are written and checked
(15,383 of 15,400 samples pass), and two fix passes for the problems the reviews found are done (15,073 samples remain). A 10-sample smoke test showed LoRA training fits on the lab's 16 GB cards. Training has not
started: it waits until the data passes review.

The labeling rules and acceptance criteria are in [`DATA_SPEC.md`](DATA_SPEC.md). The writing prompt is
`writer_tasks/PROMPT.md` (not committed, with the rest of `writer_tasks/`).

## Pipeline

| Step | File | Output (on the lab server, `/local/data/xy2764/delegation-train/data/`) |
| --- | --- | --- |
| 1. Collect tools from When2Call and ToolACE, minus anything close to FDB-v3's 12 tools | `collect_tools.py` | `tools.jsonl` |
| 2. Label what each tool does (lookup, search, action) and recheck the action labels | `label_tools.py`, `recheck_actions.py` | `tool_labels.jsonl`, `action_check.jsonl` |
| 3. Hold out six domains for dev/test | `split_tools.py` | `tools_split.jsonl` |
| 4. Label each tool's expected execution time, fast (≤ 0.5 s) or slow | `label_time.py` | `tool_time.jsonl`, `tool_time_reviewed.jsonl` |
| 5. Plan every sample (target, tools, context, speech style) and export writing tasks | `make_requests_qwen.py`, `export_writer_tasks.py` | `writer_tasks/` (tasks, batches, ready-to-paste prompts) |
| 6. Check the written answers and keep the ones that pass | `import_writer_answers.py` | `requests_writer_*.jsonl`, `import_report_*.json` |
| 7. Fix pass: pick samples to drop or rewrite, check the rewrites, merge | `export_fix_tasks.py`, `import_writer_answers.py --fixes`, `merge_fixes.py` | `fixes/`, `delegation_final.jsonl` |
| 8. Second round: drop secrets said aloud and non-English text, give repeated chat topics new ones | `export_fix_round2.py`, `import_writer_answers.py --avoid-chats`, `merge_fixes.py` | `check/fixround2/` |
| 9. Draw the stratified review sample (DATA_SPEC D) | `make_review_sample.py` | `check/fixround2/review/review_sample.json` |
| Smoke test of training | `make_smoke.py`, `run_smoke.sh`, `configs/` | `smoke/` |

`make_requests.py` is the first, Qwen3-14B-written generator (v1). It was replaced by the plan-then-write pipeline
above after its requests turned out to name tools and make up values; its checks are reused.

## Results so far (2026-10-27 to 2026-10-28)

### Tools and their time labels

| | Tools | Fast | Slow | Unsure (left out) |
| --- | --- | --- | --- | --- |
| All | 14,168 | 12,418 | 1,068 | 682 |
| Train domains | 12,506 | 11,055 | 860 | rest |
| Held-out domains (health, food, calendar, weather, real_estate, jobs) | 1,662 | 1,363 | 208 | rest |

- A tool is fast when Qwen3-14B estimates it at 0.5 s or less under both of two prompts.
- On FDB-v3's 12 tools, whose times are known, the labels were 12 of 12 correct.
- After review by hand, 383 tools labeled slow were changed to fast. The automatic recheck had been too strict.

### Writing the samples

| Batch | Written by | Samples | Passed the checks |
| --- | --- | --- | --- |
| 000 | outside model, scene-first prompt | 100 | 96 |
| 001 | outside model, revised prompt (varied acknowledgments, no forced chaining) | 100 | 91 |

- An earlier outside batch (all 15,400 tasks) came back filled from templates and was dropped:
  - 47% of requests contained the tool name;
  - it had 6 different context lines and 4 different acknowledgments;
  - "standard" was used as a value 4,946 times.
- The remaining failures are mostly front-end call values the user never said, and objectives that drop a value.
- Seven of batch 000's first-run failures were the checker's own mistakes ($ amounts, "240,000", month names, short
  tool names), and were fixed.
- From batch 002 on, about 10% of requests carry fillers and 5% self-corrections.

### Training smoke test (Fun-Audio-Chat-8B, 4 × A4000 16 GB, 20 steps on 10 samples)

| Setup | Memory per card | Time per step |
| --- | --- | --- |
| LoRA (bf16) + ZeRO-3 | 10.5 GB | 3.5 s |
| QLoRA (4-bit), data parallel | 11.5 GB | 1.1 s |

- The first run's loss was NaN: with text-only targets, the speech head averages its loss over zero tokens.
- Switching the speech head off in the model config (`enable_audio_invert_tower: false`) fixed it.

### Model choice

The main line has since moved from Fun-Audio-Chat-8B to MiniCPM-o 4.5, which already runs in full duplex.
- A feasibility check of its duplex sequence format found that the language model (Qwen3-8B) can be trained with
  LoRA. The format is one unit per second of audio, with `<|listen|>` / `<|speak|>`, `<|chunk_eos|>` and
  `<|turn_eos|>`.
- The samples are stored in a model-neutral event format, so the same data serves either model.

## Next

1. All 154 batches are written and checked (15,383 kept). The first fix pass (`DATA_SPEC.md` section 3) dropped
   83 samples and rewrote 3,420, all of which passed (15,300 kept). The second round (section 4) dropped 227 more
   and gave 208 repeated chat samples new topics; the final set holds 15,073 samples and passes section C.
2. Review a stratified sample by hand (the gate in `DATA_SPEC.md` section D). The 280-sample review page is ready
   (verdicts are saved with the page); 0 of 280 reviewed as of 2026-10-29.
3. Then, each step only after a go-ahead:
   - TTS, with pauses at phrase boundaries;
   - MiniCPM-o duplex formatting;
   - the untrained baseline;
   - LoRA SFT;
   - evaluation on FDB-v3 and the held-out tools.
