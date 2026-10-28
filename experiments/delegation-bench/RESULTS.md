# Delegation test set: results

**In short:** on the 200 spoken cases in `cases.jsonl` (typed-text input here), MiniCPM-o 4.5 holding the harness's
English instructions and tools makes the right call on 82% of cases: it hands off 82% of the requests that need the
backend and answers 58% of the must-answer-directly requests itself. Fun-Audio-Chat-8B with the same prompt hands off
almost everything: it misses few handoffs (9%) but also hands off 74% of the requests it should answer itself, and
asks a clarifying question on none of the 16 cases that need one.

See `README.md` for the cases and labeling rules, and `runners/README.md` for how the runs are made. Each run's full
report, with every error, is in `results/<run>.report.md`; raw predictions (`*.jsonl`) and the objective-rating sheets
(`*.objectives.csv`) are not committed.

## Method

- **Cases:** 200 English cases (90 must hand off, 63 either way, 31 must answer directly, 16 need a clarifying
  question). `score.py` assigns the tiers; `python3 score.py --tiers` prints them.
- **Input:** the utterance as typed text (`--input text`), so speech recognition is not part of the test.
- **Prompt:** the harness's real frontend instructions and tools (English, web search on), exported with
  `runners/dump_harness_prompt.mjs`. The export is private and is not committed.
- **Models:** MiniCPM-o 4.5 bf16 (`runners/minicpmo_delegate_tool.py`), Fun-Audio-Chat-8B
  (`runners/funaudio_delegate_tool.py`), offline and turn-based on the lab server.

## Results (2026-10-21 to 2026-10-23)

| Measure | MiniCPM-o 4.5 | Fun-Audio-Chat-8B |
| --- | --- | --- |
| Run | `minicpmo45-bf16-harness-en-text` | `funaudiochat8b-harness-en-search-text` |
| Accuracy | **164/200 (82%)** | 139/200 (70%) |
| Missed handoffs (of 90 must hand off) | 16 (18%) | **8 (9%)** |
| Wrong handoffs (of 31 must answer directly) | **8 (26%)** | 23 (74%) |
| Clarifying question (of 16) | **13 (81%)** | 0 (0%) |
| Invented tools | 0 | 0 |
| Latency, median / p90 | 4.30 s / 7.93 s | not measured |

The "clarifying question" check only looks for a "?" in a reply with no tool call; confirm by hand.

Typical errors:

- **MiniCPM-o** says it cannot do things instead of handing off ("I don't have access to real-time financial
  data…"), and calls an unrelated harness tool (`notes`) for small talk or creative requests.
- **Fun-Audio-Chat** hands off short creative requests and chit-chat ("Tell me a riddle", "Give me a fun name for a
  goldfish").

`results/minicpmo45-bf16-harness-text.report.md` is an earlier MiniCPM-o run with the first harness export and the
first version of the scorer (134/200 correct); it is kept for reference.

`review/harness-en-errors.csv` lists the MiniCPM-o errors for review by hand (keep, relabel or drop the case).
