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
| speaking, more to come | `<unit>` [audio] `<\|speak\|>` up to 18 tokens `<\|chunk_eos\|>` `</unit>` |
| speaking, last piece | `<unit>` [audio] `<\|speak\|>` up to 17 tokens `<\|turn_eos\|>` `<\|listen\|>` `</unit>` |
| a tool or backend result arrives | `<unit>` `<tool_response>…</tool_response>` `<\|speak\|>` … `</unit>` (text unit, already supported by `streaming_prefill(text_list=…)`) |

- The reply starts in the first silent second after the user stops.
- A reply is its tool calls, one line each, `<tool_call>{"name": …, "arguments": {…}}</tool_call>` (a handoff calls
  `spawn_thinking` with the objective), followed by the spoken text. A call longer than 18 tokens runs over several
  units, so speech starts one to three seconds later; acknowledging first is the alternative if that is too slow.
- The harness cuts `<tool_call>…</tool_call>` out of the stream before TTS (`intercept` in `duplex_test.py`, as in
  `fdb-chain/duplex_probe.py`).
- The training targets use four tokens from the banned list (`" ("`, `")"`, `"(H"`, `')",'`, all inside call
  arguments); `duplex_test.py` allows them at inference.
- Loss is on the model's own tokens: every `<|listen|>`/`<|speak|>` decision, the text, and the chunk and turn ends.
  Audio embeddings come from the official streaming path, so training sees what the live loop sees.

## Pipeline

| Step | File | Output (server, `/local/data/xy2764/delegation-train/duplex/`) |
| --- | --- | --- |
| Pick 200 train + 60 test samples, speak the user turns (Kokoro, 18 English voices, 16 kHz) | `tts.py` | `audio/`, `manifest.jsonl` |
| Build the duplex sequences | `build_sequences.py` | `seqs/` |
| LoRA smoke training (4-bit on one card, or `--bf16` across two) | `train_lora.py` | `lora_smoke/`, `lora_smoke_bf16/` |
| Run the test items through the real duplex loop and score the first decision | `duplex_test.py` | `results/` |

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
