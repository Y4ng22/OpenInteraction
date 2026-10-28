---
title: Delegation training
order: 5
date: 2026-10-27
status: planned
---
# Training the frontend to know when to hand off

**Goal.** Teach Fun-Audio-Chat-8B to choose between calling a tool itself and handing the request to the backend, with no execution times in the tool descriptions. It should learn from what a tool does (searching, booking, paying) and generalize to tools it has never seen. [Tool timing](#timing) showed prompting cannot do this for Fun-Audio-Chat.

## Data

No public dataset labels "handle it myself" versus "hand it to the backend". Public function-calling data supplies tools and requests; we generate the labels and the speech.

| Source | Size | License | Use |
| --- | --- | --- | --- |
| When2Call (NVIDIA) | 15k SFT, 9k preference pairs | CC BY 4.0 | Ask-for-info and cannot-answer samples; lookup tools (3,300 tools, few that change outside state); preference pairs |
| ToolACE | 11,300 dialogues, 16,134 tools | Apache-2.0 | Main source of tools that change outside state (about 400 by name: booking, sending, cancelling) |
| APIGen-MT-5k | 5k multi-turn dialogues | CC BY-NC 4.0 | Dependent multi-step calls, for handoff samples |
| SpeechFCEval | 1,914 recordings, 5 subsets | Apache-2.0 | Evaluation only |
| FDB-v3 | 100 real recordings | | Final test only |

When2Call's SFT split has no tool-call targets at all (7,109 ask-for-info, 5,668 cannot-answer, 2,223 with no tools), so handoff and self-call samples must be built. None of its tool names match FDB's 12; 7 close names (such as `search_return_flights`) are removed.

## Building the samples

1. **Tool pool.** When2Call and ToolACE tools, minus anything close to FDB's tools. 15% of tools, split by API category, are held out for testing.
2. **Tool weight labels.** Qwen3-14B sorts each tool into quick lookup, search, or action that changes outside state (booking, payment). These labels only produce the training targets; the model never sees them.
3. **Requests.** Qwen3-14B writes a user request and its gold calls from a chosen set of tools plus 2 to 12 distractors: single-step, dependent multi-step, and mixed quick-and-slow requests.
4. **Targets.** Hand off the whole request when it needs an action tool or a dependent chain; otherwise call the tool directly. A handoff objective is kept only if every gold argument value (ids, amounts, dates) appears in it verbatim.
5. **Speech.** Requests are rewritten as spoken (ids spelled out, about 20% with fillers and self-corrections) and synthesized with CosyVoice3-0.5B using a few hundred LibriTTS-R speaker prompts, with speed changes, MUSAN noise and room impulse responses.
6. **Format.** LLaMA-Factory sharegpt JSONL with Fun's function-calling prompt, exactly as at inference. Every sample ends in one decision on the last user turn: a direct tool call, a spawn_thinking handoff, a question for missing information, or a reply that it cannot be done. A request that first needs a question also appears a second time with the question and the user's answer as earlier turns, and a call or handoff as the target. Like every FDB-v3 item, many samples carry a few earlier turns as text context, but only the last decision is trained.

Planned size: about 10k SFT samples with four targets trained together: handoff 35%, direct tool call 35%, ask for missing information 20%, say it cannot be done 10%. About 3k preference pairs: handoff over direct call on slow requests, direct call over handoff on quick ones, asking over calling when a value is missing. Asking and cannot-do are trained in the same round because they are the counter-examples that stop the model from acting on every request. Accuracy is reported per target, so the handoff boundary is still measured on its own; a two-target run is an optional ablation. Repeated handoffs are already solved by the task-id reply (items with repeats went from 54 to 3), so training covers only the handoff decision.

## Model and compute

Fun-Audio-Chat-8B is 18.9 GB in bf16: a Qwen3 language model (36 layers, about 8.2B parameters), a 32-layer audio encoder and a speech head. Only the language model is trained; the audio encoder is frozen and the speech head is switched off for text output.

The official recipe is full fine-tuning on 4 × 80 GB. The lab server has 8 × A4000 16 GB, which cannot hold full fine-tuning.

| Option | Memory per card | Plan |
| --- | --- | --- |
| LoRA (bf16) with ZeRO-3 on 8 cards | about 2.4 GB frozen weights, under 1 GB LoRA and optimizer, 4–6 GB activations | Main option |
| QLoRA (4-bit) with data parallel on 8 cards | about 11 GB | Fallback |

SFT settings: LoRA rank 64, alpha 128, on q/k/v/o/gate/up/down of the language model; learning rate 1e-4, cosine, 3% warmup; 2 epochs; effective batch 32; cutoff 3072 tokens after checking the length distribution. Then optional DPO (beta 0.1, learning rate 5e-6, 1 epoch).

## Evaluation

| Test | Measures |
| --- | --- |
| FDB-v3, 100 real recordings, no times in the prompt, and FDB's instructions without the lines that require tool use and forbid questions | Pass rate and routing accuracy, against untrained Fun-Audio-Chat (rerun with the same prompt), Fun-Audio-Chat with times, and Qwen3.8 |
| Held-out tools, text and synthesized speech | Whether the boundary generalizes, and the gap between text and speech |
| When2Call test (3,652), SpeechFCEval | No loss of general function calling |

Checkpoints are chosen on the held-out-tool dev set, never on FDB-v3.

Because asking is trained, eight FDB-v3 housing scenarios (housing_04, 05, 15, 20, 21, 22, 24, 25) expect search_apartments without a required value the user never gave, such as bedrooms or a budget. When testing, a question asking for that value also counts as correct (`report/ask_ok.json`). housing_05 and housing_15 are already among the 10 benchmark errors. FDB's own instructions say "REQUIRED to use the provided tools" and "DO NOT ASK CLARIFYING QUESTIONS … EXECUTE THE TOOL IMMEDIATELY", which contradict asking and handing off, so tests use FDB's instructions with those lines removed (the `FDB_CLEAN_INSTRUCTIONS` prompt in run_chain.py). Training prompts never contain them.

## Steps

| Step | Status |
| --- | --- |
| Download When2Call and read its format | Done |
| Check Fun-Audio-Chat's training code | Done: LLaMA-Factory based, in `training/` of QwenAudio/Fun-Audio-Chat |
| Set up training on the server; 10-sample smoke test of LoRA + ZeRO-3 and QLoRA | Next |
| Build the data | Running |
| Your review of the data; training waits for your go-ahead | To do |
| SFT, then DPO | To do |
| Evaluate | To do |

## Risks

- Fun's custom model may not run with LoRA + ZeRO-3 or with 4-bit loading; the last resort is full fine-tuning with CPU offload, which is slow.
- The A4000s have no NVLink, so ZeRO-3 traffic will slow training.
- The model may not learn the boundary from tool meaning alone. The held-out-tool result answers that question either way.
- This is turn-based text output; full duplex is a separate direction.
