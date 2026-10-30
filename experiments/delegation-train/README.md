# Delegation training: teaching the voice model when to hand off

**In short:** no public dataset labels "do it myself" versus "hand it to the backend", so we build one: 15,400
requests over 14,168 real tool definitions, with the voice model holding only fast tools (≤ 0.5 s) and handing everything else to the backend. This
follows how Qwen-Audio-Agent's cockpit app places tools (by hand, per domain), with expected time in place of
domain; the 0.5 s line is our own. Each tool is labeled fast or slow, each sample's target is
fixed first, and an outside model writes a request that fits it. All 154 batches are written and checked
(15,383 of 15,400 samples pass), and three fix passes for the problems the reviews found are done (14,076 samples remain, all spoken and laid out
as MiniCPM-o full-duplex sequences). A 10-sample smoke test showed LoRA training fits on the lab's 16 GB cards. Training has not
started: it waits for your go-ahead.

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
| 10. Third round: flag API values read aloud, Claude review of every ask and chat, drop, top up asks (DATA_SPEC 5) | `flag_api_speech.py`, `export_ask_topup.py`, `import_writer_answers.py --plans`, `apply_round3.py` | `check/fixround3/merged/delegation_v3.jsonl` |
| 11. Speak every user turn (Kokoro; spoken dates, pauses in 30% of requests) and build the duplex sequences | `duplex/tts.py --all`, `duplex/build_sequences.py` | `duplex_full/` (`audio/`, `seqs/`) |
| Smoke test of training | `make_smoke.py`, `run_smoke.sh`, `configs/` | `smoke/` |
| Untrained baseline: MiniCPM-o 4.5 text chat mode on the test split | `baseline/run_baseline.py`, `baseline/score_baseline.py` | `baseline/` |
| Duplex feasibility test: 200 spoken samples, LoRA, real duplex loop ([`duplex/README.md`](duplex/README.md)) | `duplex/` | `duplex/` |

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

### Untrained baseline (MiniCPM-o 4.5, 2026-10-29)

In full duplex, MiniCPM-o emitted no function call in 20 of 20 FDB-v3 runs (`fdb-chain/report/modules/talk.en.md`),
so its duplex baseline for call, handoff and split is zero; the numbers below show what its language model can do
when it is given the tools in turn-based form, which training has to carry into the duplex stream.
MiniCPM-o's full-duplex mode has no tool-call path, so the baseline uses its simplex chat mode: text input (TTS has
not run), the model's own chat template, the sample's fast tools plus a `spawn_thinking(objective)` handoff tool, and
a system prompt that states the five behaviors. Greedy decoding with the official model code on the 692 test samples
(held-out domains).

| Gold target | n | Right | Mostly confused with |
| --- | --- | --- | --- |
| call | 227 | 61.2% | handoff (75): hands off what its own tools can do |
| handoff | 194 | 84.5% | ask (14), call (9) |
| ask | 109 | 13.8% | handoff (59), call (34): almost never asks |
| split | 69 | 49.3% | handoff (22), call (13) |
| chat | 93 | 62.4% | handoff (25): sends small talk and stable knowledge to the backend |
| **All** | 692 | **59.2%** | |

- On items with the right target: tool names right 91.9% and names plus arguments exact 75.1% (call/split, 173);
  the objective keeps every user value in 79.8% (handoff/split, 198).
- When it hands off it says nothing (spoken acknowledgment present in 0 of 198), which in a voice turn is silence.
- 19 of 692 replies say it cannot help.
- Reporting a backend result is already there: on the 31 test handoffs with a backend result, the reply tells the
  user with a concrete value from the result in 31 of 31 when the result comes right after the handoff, and 30 of 31
  when one unrelated exchange comes in between. No reply hands off again.
- A first run through vLLM (the language model extracted by `baseline/extract_llm.py`) drifted to unrelated topics
  under concurrent requests (19.4% target accuracy) while the official code on the same prompts did not, so those
  outputs were set aside (`baseline/vllm_run/` on the server) and every number above comes from the official code.

### Duplex feasibility test (2026-10-29)

The duplex decoder masks `"\n"` among 6,965 banned tokens, so the chat-mode call format can never come out in full
duplex. A LoRA on 200 spoken samples, laid out as the duplex loop feeds and decodes them, with calls on one line,
was enough: on 60 held-out samples streamed through the real duplex loop, the target was right on 52 of 60 (greedy)
and 54 of 60 (sampling), against 20 of 60 untrained, which never called a tool. When the result was fed back as a
text unit, it told the user with a value from the result in 30 of 34 (untrained 15 of 34). Details in
[`duplex/README.md`](duplex/README.md).

### Model choice

The main line has since moved from Fun-Audio-Chat-8B to MiniCPM-o 4.5, which already runs in full duplex.
- A feasibility check of its duplex sequence format found that the language model (Qwen3-8B) can be trained with
  LoRA. The format is one unit per second of audio, with `<|listen|>` / `<|speak|>`, `<|chunk_eos|>` and
  `<|turn_eos|>`.
- The samples are stored in a model-neutral event format, so the same data serves either model.

## Next

1. The data is ready: 14,076 samples after three fix passes (`DATA_SPEC.md` sections 3 to 5), section C passes,
   every user turn is spoken (21,485 + 1,470 WAVs) and every sample is a duplex sequence in `duplex_full/seqs/`.
2. Each step only after a go-ahead:
   - LoRA SFT on the duplex sequences, as a learning curve (about 1,600, 3,300, 6,600 and all samples, one epoch
     each, with 20-30% general dialogue mixed in), two cards per run;
   - evaluation in the real duplex loop on a larger held-out test set (300+ spoken samples, with ask asking and
     chat never calling a tool as hard checks), a chat regression test, and FDB-v3;
   - the untrained duplex baseline on the same test set.
