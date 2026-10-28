# Chain: end-to-end task completion on FDB-v3

**In short:** a voice frontend and a Qwen3-14B backend share Full-Duplex-Bench v3's 100 spoken requests and 12 mock
tools, and FDB-v3's own scorer checks the tool calls made by either side. The best setup lets the backend do the
work: Fun-Audio-Chat-8B holding only `spawn_thinking` passes 68 of 100 (78 when the 10 benchmark errors count as
passed). When the frontend also holds the 12 tools, it hands off almost nothing (1 of 100) and passes 51. The
biggest single fix was the handoff reply: once `spawn_thinking` returned a task id and said the task is not done
until its result arrives, repeated handoffs stopped and the pass rate went from 36 to 68. MiniCPM-o in full duplex
made no function call at all (0 of 20).

Routing is left entirely to the models: there is no rule-based routing. Regular expressions only read
`<tool_call>` blocks out of model text.

## Files

| File | Purpose |
| --- | --- |
| `fdb_tools.py` | FDB-v3's 12 tools (names, descriptions and parameters copied from its `lk_agent_tool.py`), its agent instructions, and an executor on its `mock_apis.py` |
| `run_chain.py` | Runs the chain for one variant (see its docstring for every variant); `--fake` replays gold calls to test the plumbing without models |
| `funaudio_frontend.py` | Fun-Audio-Chat-8B as the frontend (`--frontend funaudio`) |
| `score_chain.py` | Scores the logs with FDB-v3's `evaluate_pass_rate.py` and `evaluate_tool_calls.py`; harness tools such as `spawn_thinking` are left out. `--count-as-pass` adds a pass rate with the known benchmark errors counted as passed |
| `announce_stage2.py` | Stage 2: what the frontend says before and after the backend's result arrives |
| `duplex_probe.py` | Streams FDB-v3 recordings into MiniCPM-o's full-duplex loop and checks for `<tool_call>` output |
| `asr_minicpmo.py` | Transcribes the last user turn (for `fdb-delegate-words` and `backend-asr`) |
| `build_viz.py` | Builds the trace and report page from the results and `report/` |
| `report/` | `benchmark_errors.json` (10 FDB-v3 items with wrong gold answers), `ask_ok.json` (8 housing items where asking is correct), talk scripts and page modules |
| `results/` | Score reports (`*.judge.md`, `*.exact.md`); `*.refs.md` are the current scores, with `$RESULT` references resolved. Raw run logs (`*.jsonl`) are not committed |

Needs: the Full-Duplex-Bench repository (`v3/`), FDB-v3's audio (`fdb_v3_data_released/`, from the Google Drive
link in its README), the private harness export (`--harness-json`, see `../delegation-bench/runners/README.md`;
never committed), and an OpenAI-compatible Qwen3-14B endpoint.

## Method

- **Data:** all 100 FDB-v3 recordings. The user's last turn is given as audio, trimmed where the speech ends;
  earlier turns are given as text.
- **Models:** frontend MiniCPM-o 4.5 (bf16) or Fun-Audio-Chat-8B; backend Qwen3-14B (vLLM, bf16).
- **Loop:** offline and turn-based. Each call runs at once on FDB-v3's mock APIs and its result is fed back.
  `spawn_thinking` runs the backend to completion before the frontend continues.
- **Scoring:** FDB-v3's scorer. "Judge" is FDB-v3's argument-judging prompt run on Qwen3-14B instead of gpt-4o;
  "exact" is exact argument match.

Differences from FDB-v3's standard setting, to report with any result: harness instructions replace FDB-v3's
agent instructions (except the `fdb-*` variants); work is split between a frontend and a backend; runs are offline
and turn-based, not streamed through LiveKit, so latency is not comparable; the argument judge is Qwen3-14B.

## Results (2026-10-22 to 2026-10-27)

### Main setups

| Setup | Frontend | Run | Pass (judge) | Pass, benchmark errors counted | Pass (exact) | Items handed off |
| --- | --- | --- | --- | --- | --- | --- |
| Hand-off only, task-id reply | Fun-Audio-Chat | `funaudio-fdb-delegate-tid` | **68** | **78** | 51 | 100 |
| Hand-off only, "accepted" reply | Fun-Audio-Chat | `funaudio-fdb-delegate` | 36 | 46 | 27 | 100 |
| 12 tools + `spawn_thinking` | Fun-Audio-Chat | `funaudio-fdb-handoff` | 51 | 61 | 47 | 1 |
| 12 tools, no backend | Fun-Audio-Chat | `funaudio-fdb-native` | 48 | 58 | 46 | 0 |
| Hand-off only | MiniCPM-o | `fdb-delegate` | 22 | 32 | 18 | 41 |
| 12 tools + `spawn_thinking` | MiniCPM-o | `fdb-handoff` | 59 | 67 | 50 | 0 |
| 12 tools, no backend | MiniCPM-o | `fdb-native` | 62 | 70 | 51 | 0 |
| Backend alone, reads the transcript | — | `backend-direct` | 66 | 76 | 41 | — |
| Backend alone, reads the ASR transcript | — | `backend-asr` | 70 | 80 | 47 | — |

Every run is on 100 items. "Benchmark errors counted" counts the 10 items in `report/benchmark_errors.json` as
passed.

### Telling the frontend each tool's execution time (Fun-Audio-Chat, task-id reply)

| Run | What the frontend is told | Pass (judge) | Errors counted | Handed off |
| --- | --- | --- | --- | --- |
| `funaudio-fdb-time-complex-tid` | each tool's typical time, plus what counts as a simple or complex task | 53 | 63 | 1 |
| `funaudio-fdb-time-limit-tid` | each tool's typical time, plus a 1-second limit for its own calls | 50 | 60 | 1 |

Holding the tools, the frontend still did the work itself. Execution times in the prompt did not make it hand off.

### All MiniCPM-o prompt variants

| Run | Pass (judge) | Errors counted | Pass (exact) | Handed off |
| --- | --- | --- | --- | --- |
| `split` (harness prompt + harness tools + FDB tools) | 28 | — | 21 | 0 |
| `backend-only` (harness prompt + harness tools) | 10 | — | 5 | 17 |
| `fdb-minimal` | 19 | — | 16 | 35 |
| `fdb-delegate-strict` | 33 | 43 | 28 | 58 |
| `fdb-delegate-words` | 34 | 44 | 28 | 58 |
| `fdb-delegate-manifest` | 5 | 15 | 4 | 11 |

The first three rows were not rescored with the benchmark errors counted, and their exact-match numbers come from
the earlier scoring, before `$RESULT` references were resolved. `split-1s`, `split-tools`, `backend-only-1s` and
`backend-only-tools` are reruns of `split` and `backend-only` with
a changed setting; their reports are in `results/`.

### Full duplex (MiniCPM-o, `duplex_probe.py`)

| Prompt | Items | Items with a function call |
| --- | --- | --- |
| `fdb-native` (12 tools) | 10 | 0 |
| `fdb-delegate-strict` (hand-off only) | 10 | 0 |

It answers in speech with no call behind it, for example "I will track your order using the ID you provided."

### What the frontend says around a handoff (`announce_stage2.py`, `fdb-delegate-strict`, 58 delegated items)

| Measure | Items |
| --- | --- |
| Before the result: states a made-up outcome | 34/58 |
| After the result: tells the user the result | 58/58 |
| After the result: every fact supported | 53/58 |
| After the result: calls a tool again | 0/58 |

`results/stage2-funaudio-tid.md` has the same measures for Fun-Audio-Chat with the task-id reply.

### Benchmark errors

10 FDB-v3 items have gold answers that conflict with the tool definitions or the recording, for example three
expect a `pets_allowed` argument that the apartment search does not have. They are listed in
`report/benchmark_errors.json`, with the reasoning in `report/modules/errors.md`.

## How to run

```sh
# the best setup: Fun-Audio-Chat, hand-off only, task-id reply
python experiments/fdb-chain/run_chain.py --variant fdb-delegate --frontend funaudio --task-id-reply \
    --fdb-v3-dir <Full-Duplex-Bench/v3> --data-dir <fdb_v3_data_released> \
    --harness-json <private harness export> --backend-url http://127.0.0.1:8200/v1 --gpus 0,1 --results results
python experiments/fdb-chain/score_chain.py --fdb-v3-dir <Full-Duplex-Bench/v3> \
    --judge-url http://127.0.0.1:8200/v1 --resolve-refs \
    --count-as-pass experiments/fdb-chain/report/benchmark_errors.json results/*.jsonl
```
