# Duplex feasibility test: can MiniCPM-o 4.5 learn to call tools in full duplex?

**In short:** untrained, MiniCPM-o 4.5 never emits a tool call in full duplex (0 of 20 FDB-v3 runs, and see the
results below). This test trains a small LoRA on 200 spoken samples laid out exactly as the duplex loop feeds and
decodes them, then runs 60 held-out samples through the real duplex loop to see whether calls come out, parse, and
pick the right target.

## Why the untrained model cannot call tools in duplex

Found in the official duplex code (`modeling_minicpmo.py`, `utils.py` of the 4.5 snapshot):

- The duplex decoder masks 6,965 `bad_token_ids` from the tokenizer config, among them `"\n"`, `"("`, `")"`,
  `"{\n"` and `'"}}\n'`. The chat-mode call format `<tool_call>\n{...}\n</tool_call>` needs `"\n"`, so the model can
  never produce the format it learned in chat mode.
- Each one-second unit produces at most 20 tokens, and the text goes straight to TTS.

## Format

Each second of audio is one unit. The layout copies what `MiniCPMODuplex.streaming_prefill` feeds and
`streaming_generate` decodes (`ls_mode` explicit):

| Unit | Tokens |
| --- | --- |
| listening | `<unit>` [10 audio embeddings] `<\|listen\|>` `</unit>` |
| speaking, more to come | `<unit>` [audio] `<\|speak\|>` a few tokens `<\|chunk_eos\|>` `</unit>` |
| speaking, turn ends | `<unit>` [audio] `<\|speak\|>` `<\|turn_eos\|>` `<\|chunk_eos\|>` `</unit>` (usually its own unit, sometimes after the last words) |
| a tool or backend result arrives | `<unit>` `<tool_response>…</tool_response>` `<\|speak\|>` … `</unit>` (text unit, already supported by `streaming_prefill(text_list=…)`) |

- The reply starts in the first silent second after the user stops.
- Spoken text goes out at the untrained model's own pace: tokens per unit are drawn from what it produces in duplex
  (`PACE` in `build_sequences.py`, mostly 2 to 4, mean about 3), and the turn usually ends in a unit of its own. The
  feasibility test packed 18 tokens a unit, about six times faster than the model speaks.
- A handoff or split reply speaks its short acknowledgment first, then writes its calls, one line each
  (`<tool_call>{"name": …, "arguments": {…}}</tool_call>`, a handoff calls `spawn_thinking` with the objective), 18
  tokens a unit. A plain call has no acknowledgment: the result comes back within a second or two.
  (The feasibility test below put the calls first; `--calls-first` keeps that order.)
- The harness cuts `<tool_call>…</tool_call>` out of the stream before TTS (`intercept` in `duplex_test.py`, as in
  `fdb-chain/duplex_probe.py`).
- The training targets use four tokens from the banned list (`" ("`, `")"`, `"(H"`, `')",'`, all inside call
  arguments); `duplex_test.py` allows them at inference.
- Loss is on the model's own tokens: every `<|listen|>`/`<|speak|>` decision, the text, and the chunk and turn ends.
  Audio embeddings come from the official streaming path, so training sees what the live loop sees.

## Full set (2026-10-29)

All 14,076 samples of the third-round set (`DATA_SPEC.md` section 5) are spoken and built the same way, with three
additions: earlier context turns are spoken too (their assistant lines are in the stream without loss), ISO dates
are read as spoken dates, and 30% of requests get one 0.5-2 s pause at a phrase boundary. Output on the server in
`duplex_full/`: `audio/` (21,485 WAVs) and `new/audio/` (1,470 for the 835 new asks), `seqs/` (one sequence per
sample, 16 GB), `seq_index.jsonl` (split, target, speech, tokens: median 1,202, 95th percentile 1,779, max 3,874).
The full targets use 34 tokens from the duplex decoder's banned list (brackets and the bytes of non-English
characters in a few tool values); `allowed_banned_ids.json` lists them for `duplex_test.py --allow`.

Building ran 8 shards with `OMP_NUM_THREADS` set: without it, four shards each took every CPU thread and the build
crawled at 34 samples a minute.

## Training set (2026-10-30)

The fourth-round set (`DATA_SPEC.md` section 6, 14,017 samples) was rebuilt in the format above into
`duplex_full/seqs_v4/` (28 GB): acknowledgment first, the model's own speaking pace, and 800 two-request episodes
(`--pairs 800`: a handoff whose backend result arrives only after the user asked something else and was answered;
median 2,555 tokens). `seq_index.jsonl` there: 13,432 train (call 4,185, handoff 3,962, ask 1,919, chat 1,347,
split 1,219, pair 800), 690 dev, 695 test; tokens median 1,330, 95th percentile 2,246, max 4,186. The targets use 33
tokens from the decoder's banned list (`allowed_banned_ids_v4.json`).

Self-distillation (`self_distill.py`) keeps the model's own duplex behavior in the mix: 3,500 DailyDialog
conversations (two thirds turn-taking, one third cut in after 1 to 3 spoken units) run through the untrained model,
its own outputs as targets. `filter_selfdistill.py` keeps 3,229 and drops 202 where it kept talking 3 s into a
cut-in (17% of the cut-ins), 37 where it never spoke, and 32 where it called itself Qwen or Alibaba's. Answering
before the user has finished is kept: the model decides every second whether it has heard enough.

Training (`train_lora.py`, started 2026-10-30): LoRA r16 on every projection, bf16 across two cards, lr 2e-4,
one epoch per learning-curve point (`--fraction` 0.125, 0.25, 0.5, 1: about 2.1k, 4.2k, 8.3k and 16.7k sequences, the
same share of every target including self-distillation), eval loss per target on 300 dev sequences.

Evaluation, base model now and each adapter later (`eval_v4/`):
- all 695 test samples through the real duplex loop with the result fed back (`duplex_test.py --with-result`, sharded);
- duplex behavior on 300 held-out DailyDialog validation conversations (`self_distill.py prepare --prefix sdeval`,
  then `run` with and without `--lora`), scored with `filter_selfdistill.py`'s counts per timing.

Correction (2026-10-30): this first version of the test played only the first spoken turn. About 35% of the test
samples start with a context turn, so for those the model never heard the request it was scored on, and a trained
model stayed silent on 56 of 232 items. `duplex_test.py` now replays the context turns as in training, and the base
model is being rerun on those samples (`--context-only`, merged into `eval_v4/base/test_v2.jsonl`). The numbers
below are from the first version.

Base model in duplex on the fourth-round test set (`eval_v4/base/`, same system prompt and tools as training, greedy):

| gold \ predicted | call | handoff | ask | split | chat | invalid | malformed | silent |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| call (223) | 5 | 0 | 84 | 0 | 113 | 0 | 13 | 8 |
| handoff (193) | 1 | 0 | 110 | 0 | 64 | 0 | 4 | 14 |
| ask (119) | 0 | 0 | 94 | 0 | 19 | 0 | 1 | 5 |
| split (69) | 1 | 0 | 28 | 0 | 35 | 1 | 2 | 2 |
| chat (91) | 0 | 0 | 45 | 0 | 43 | 0 | 2 | 1 |

- Target right 142/695 (20.4%). It says "I'll get the forecast…" without calling anything, or asks for details it does
  not need. 30 items contain a call, 22 of them unclosed; never a `spawn_thinking` that parses as a handoff.
- Spoke before the user finished on 186 items.
- 101 test samples carry a result (call 55, handoff 31, split 15). After it comes back the model tells the user on
  39 (26, 8, 5), with a value from the result on 31 (24, 4, 3), and never calls again. Items without a result are no
  longer scored in this table, and from now on no empty text unit is fed for them.
- Duplex behavior on the 300 held-out conversations: 280 pass the filter (93.3%). 14 of 104 cut-ins kept speaking 3 s
  in (13.5%), 4 never spoke, 2 said Qwen or Alibaba. These are the numbers a trained adapter must not get worse on.

## Pipeline

| Step | File | Output (server, `/local/data/xy2764/delegation-train/duplex/`) |
| --- | --- | --- |
| Pick 200 train + 60 test samples, speak the user turns (Kokoro, 18 English voices, 16 kHz); `--all` for the whole set | `tts.py` | `audio/`, `manifest.jsonl` |
| Build the duplex sequences | `build_sequences.py` | `seqs/` |
| LoRA smoke training (4-bit on one card, or `--bf16` across two) | `train_lora.py` | `lora_smoke/`, `lora_smoke_bf16/` |
| Run the test items through the real duplex loop and score the first decision | `duplex_test.py` | `results/` |
| Self-distillation conversations, and their filter (also the duplex-behavior eval) | `self_distill.py`, `filter_selfdistill.py` | `../duplex_sd/` (`seqs/`, `keep.jsonl`), `../duplex_sdeval/` |
| Full training set and base-model evals | `build_sequences.py --pairs 800`, `duplex_test.py --shard` | `../duplex_full/seqs_v4/`, `../eval_v4/` |

## Results (2026-10-29)

200 training samples (call 60, handoff 50, ask 30, split 30, chat 30), bf16 LoRA (rank 16, all attention and MLP
projections) on two 16 GB cards, 3 epochs, 150 optimizer steps, 20 minutes. Test: 60 samples from the held-out
domains (12 per target), each first spoken turn streamed through `MiniCPMODuplex`, scored on the first reply (up to
the first `<|turn_eos|>`).

| | Untrained | LoRA, greedy | LoRA, sampling (duplex defaults) |
| --- | --- | --- | --- |
| Target right | 20/60 | **52/60** | **54/60** |
| call | 0/12 | 11/12 | 11/12 |
| handoff | 0/12 | 9/12 | 9/12 |
| ask | 10/12 | 10/12 | 12/12 |
| split | 0/12 | 10/12 | 10/12 |
| chat | 10/12 | 12/12 | 12/12 |
| Items with a tool call | 0 | 35 | 32 |
| Call bodies that are not valid JSON | – | 1 | 1 |
| Right target, calls exactly right (call/split) | – | 16/21 | 15/21 |
| Handoff/split with a spoken acknowledgment | – | 20/20 | 20/20 |
| Call JSON that would reach TTS | – | 0 | 0 |
| Spoke before the user finished | 10 | 3 | 2 |

- Test loss on the 60 sequences went from 2.17 to 0.78.
- The untrained model talks about doing the task ("I will search for data analyst jobs…") without calling anything.
- Remaining errors: three handoffs asked a question instead, one call body lost a closing brace, and calls longer
  than 18 tokens delay speech by one to two seconds.
- With no tool result coming back, the trained model repeats its call after a few silent seconds (the result test
  below feeds the result one second after the reply, as in training).

### Result fed back as a text unit

34 test samples that carry a tool or backend result (call 12, handoff 12, split 10), from the same held-out domains.
After the reply and one silent second, the result goes into the stream as `<tool_response>…</tool_response>`
(`streaming_prefill(text_list=…)`), and the reply to it is scored (`duplex_test.py --with-result`).

| | Untrained | LoRA |
| --- | --- | --- |
| First reply: target right | 0/34 | 29/34 |
| After the result: tells the user | 19/34 | **33/34** |
| … with a concrete value from the result | 15/34 | **30/34** |
| … calls again instead | 0 | 1 |

- Untrained, a handoff result was told in only 5 of 12, and once the model put a Denver forecast in San Francisco.
- Trained, the reply reads like "Dental hygiene jobs in Ohio rose 9% over the past year. The median salary is
  $78,400, with the most openings in Columbus and Cincinnati." Split replies give the fast result and say the
  backend part is still in progress.
- Of the four without a value, two do use the result but word it so the value check misses it, one stops after
  "Here are some interview questions to practice:", and one hands off again.
- A 4-bit QLoRA run on one card reached a similar training loss but ran out of memory in its final evaluation
  before saving (fixed: the adapter is now saved first); it was not tested.
