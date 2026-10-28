---
title: Tool timing
order: 3
date: 2026-10-26
status: done
---
# Does tool execution time teach the frontend when to hand off?

When the frontend holds the 12 FDB tools and spawn_thinking, Fun-Audio-Chat handed off once in 100 items. Here each tool's description also gives its execution time, and the prompt says when to hand off. Tool descriptions never say which tasks are simple or complex.

**Scoring.** A request should be handed off when it needs a slow tool: book_flight or modify_autopay, about 2 s each. 16 of FDB-v3's 100 items need one; the other 84 need only quick tools (125 ms to 500 ms) and should stay with the frontend. Times follow FDB-v3's own latency classes.

## Fun-Audio-Chat-8B, all 100 items

| Prompt | Slow handed off (of 16) | Quick kept (of 84) | Pass (judge) | Pass, benchmark errors counted |
| --- | --- | --- | --- | --- |
| No times | 0 | 83 | 51 | 61 |
| Time ranges, plus simple/complex definition | 0 | 83 | 53 | 63 |
| Time ranges, plus a 1 s limit | 0 | 83 | 50 | 60 |

## Fun-Audio-Chat-8B, six-item probes

Two slow items (finance_03, travel_11) and four quick ones.

| Change to the prompt | Slow handed off (of 2) | Quick kept (of 4) |
| --- | --- | --- |
| 0.5 s limit, written as IMPORTANT and MUST | 0 | 4 |
| Exact times: 125 ms, 500 ms, 2 s | 0 | 4 |
| FDB's lines that require using the tools removed | 0 | 4 |
| Frontend holds only the quick tools | 0 | 4 |
| Two steps: choose "hand off" or "handle myself", then get tools | 1 | 1 |
| Our own harness prompt | 1 | 4 |

With only the quick tools, it did part of the slow request with the tools it had (for travel_11, search_flights and update_identity_doc) and never handed the booking off.

## Qwen Omni realtime models, 26 items

All 16 slow items plus 10 quick ones, through the Bailian realtime API, text output. Same timed tools and 0.5 s limit as above.

| Model | Prompt | Slow handed off (of 16) | Quick kept (of 10) | Routed correctly |
| --- | --- | --- | --- | --- |
| Qwen3.8 Omni Flash Realtime | Exact times, 0.5 s limit | 13 | 10 | 23 / 26 |
| Qwen3.8 Omni Flash Realtime | Simple/complex definition, no times | 4 | 10 | 14 / 26 |
| Qwen3-Omni Flash Realtime (2025-09-15) | Exact times, 0.5 s limit | 0 | 10 | no function calls at all |
| Qwen3-Omni Flash Realtime (2025-09-15) | Simple/complex definition | 0 | 10 | no function calls at all |

## What this shows

- Explicit times and a limit work on Qwen3.8, a closed model trained for tool use: 23 of 26 routed correctly.
- A definition of simple versus complex does not carry the boundary, even for Qwen3.8: it handed off 4 of 16 slow requests.
- Fun-Audio-Chat, the open frontend we can modify, handed off none of the 16 slow items in the full runs and at most one of the two in any probe. Prompting does not give it the boundary, so the next step is to train it ([Delegation training](#training)).
