---
title: Delegation training
order: 5
date: 2026-10-28
status: running
---
# Training the voice model to know when to hand off

**Goal.** Teach MiniCPM-o 4.5 to decide, on every user turn and in full duplex, between answering directly, calling one of its own fast tools, asking for a missing value, and handing the request to the backend with `spawn_thinking`. MiniCPM-o already holds full-duplex conversations, but in our tests it never emitted a function call (0 of 20), so tool calling and the handoff decision are trained together.

## The split: Qwen-Audio-Agent's design

The voice model keeps only fast tools, those that answer in 0.5 s or less, and hands everything else to the backend. Independent requests in one turn are split: the fast part is done at once, the rest is handed off. The voice model never says it cannot do something. This follows the [Qwen-Audio-Agent report](https://arxiv.org/abs/2609.25195), where letting the voice model decide per request gave the highest task success (91.0%) and the lowest latency.

## Data

No public dataset labels "do it myself" versus "hand it off", so we build one: 15,400 samples (train 14,000, dev 700, test 700) over 14,168 real tool definitions from When2Call and ToolACE.

1. **Tool time labels.** Each tool is labeled fast or slow from its expected execution time: Qwen3-14B estimates it under two prompts; on FDB-v3's 12 tools the labels were 12 of 12 correct, and 383 labels were corrected by hand. Six whole domains (health, food, calendar, weather, real_estate, jobs) are held out for dev and test.
2. **Targets first.** Each sample's target and tools are fixed before any text is written: call 35%, handoff 30%, ask 15%, chat 10%, split 10%. About 20% also carry the tool or backend result and the final spoken answer.
3. **Requests written to fit.** An outside model imagines a concrete scene and writes the request, earlier turns, objective and acknowledgment. About 10% of requests carry fillers ("um", "I mean") and 5% a self-correction ("Tuesday, no wait, Wednesday"), where the label uses only the corrected value.
4. **Checks.** Every sample passes automatic checks: call values must come from what the user said, objectives repeat every value and invent none, acknowledgments never claim the task is done, no tool names in requests, no duplicates across splits. Full rules and acceptance criteria are in `DATA_SPEC.md` on the experiments branch.
5. **Speech.** TTS with many voices; pauses of 0.5 to 2 s inside about 30% of requests, so the model learns to keep listening until the user is done.

Repeated handoffs are already handled by the task-id reply (items with repeats went from 54 to 3), so training covers only the decision on each turn, not the turns after a handoff.

## Model and training

MiniCPM-o's full-duplex stream runs in one-second units of about 10 audio embeddings, each followed by `<|listen|>` or `<|speak|>` and up to 20 text tokens, with `<|chunk_eos|>` and `<|turn_eos|>` markers. Its language model is Qwen3-8B.

| Setting | Plan |
| --- | --- |
| Method | LoRA on the language model; audio encoder frozen; audio embeddings precomputed |
| Loss | Only on the decision tokens (tool call, handoff, question, reply); the user's words are not trained |
| Keeping its chat ability | 20–30% general dialogue mixed into every batch, and a chat regression test after training |
| Data size | Learning curve on 25%, 50% and 100% of the data before deciding whether more is needed |

A smoke test on Fun-Audio-Chat-8B, the first candidate, showed LoRA fits on the lab's 16 GB cards (10.5 GB per card with ZeRO-3). MiniCPM-o needs its own smoke test.

## Evaluation

| Test | Measures |
| --- | --- |
| FDB-v3, 100 real recordings, in full duplex | Pass rate and routing, against untrained MiniCPM-o with the same prompt |
| Held-out tools (six unseen domains) | Whether the fast/slow boundary generalizes beyond the training tools |
| Delegation test set (200 spoken cases) | Hand off, answer directly, or ask |
| General conversation | No loss of chat quality |

Checkpoints are chosen on the held-out-tool dev set, never on FDB-v3. Eight FDB-v3 housing items expect a search without a value the user never gave; a question asking for it also counts as correct (`report/ask_ok.json`).

## Gate

Nothing that uses the training data (TTS, formatting, baseline, training) starts until you have reviewed a sample of the data and said go.
