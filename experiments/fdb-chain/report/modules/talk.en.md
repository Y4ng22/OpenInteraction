---
title: Talk script
order: 1
date: 2026-10-24
status: done
lang: English
---
# Voice Frontend–Backend Progress

Oct 24, 2026 · Xinzhuo Yang


Full traces for every run: [Handoff traces](#traces)

## 1. Overview

This month Matthew and I built the text version of our system: a fast voice model that talks to the user, and a stronger text model with tools that does the work.

Today I’ll walk through the system, our end-to-end runs on Full-Duplex-Bench v3, what broke and what helped, the return path, and why we think the whole system needs its own benchmark.

## 2. The system

{{arch}}

In the benchmark runs below the voice frontend is Fun-Audio-Chat-8B and uses MiniCPM-o 4.5. It listens, talks, and decides when a request needs real work. When it does, it makes one function call, spawn\_thinking, with a short description of the task.

The harness takes that call, replies with a task id, and passes the task to the backend. When the backend finishes, its result goes back into the voice model, and the voice model tells the user.

Our research question is latent communication, that is sending the models’ internal states along these two arrows instead of text. To measure what that buys, we first needed the text version working and measured. That is what I’ll show.

## 3. Can the voice model decide to hand off?

Two things we found. First, if the voice model holds the tools itself, it does the work itself: across 100 benchmark items Fun-Audio-Chat handed off once. Second, in full-duplex mode MiniCPM-o never emitted a function call, zero of twenty, even when told to delegate.

Since the voice model never hands off while it holds the tools, next we will try adding each tool call’s execution time and giving the voice model a time limit: work expected to take longer than the limit should be handed off.

When the voice model holds only spawn\_thinking, Fun-Audio-Chat hands off all 100 requests.

In our design the frontend model always decides the handoff.  Fun-Audio-Chat hands off reliably in turn-based mode, but its full-duplex weights aren’t public yet, so we can’t test it there. MiniCPM-o runs in full duplex, but never emits a function call.

But most models have no native ability to delegate. Delegation is mostly controlled by the prompt, so it is very unstable. Making delegation a native ability of the voice model is also what we want to do.

## 4. Why hand work off at all

The [Qwen-Audio-Agent report](https://arxiv.org/abs/2609.25195) tests this directly on an in-house cockpit set: 134 cases with 41 tools, Qwen Audio 3.0 Realtime Plus as the voice model and qwen3.8-max as the backend. It compares three ways of splitting the work.

| Who does the work | Short tasks done (of 86) | Multi-step tasks done (of 48) | Task success | Mean latency |
| --- | --- | --- | --- | --- |
| Voice model calls the tools itself | 83 | 14 | 72.4% | 6.5 s |
| Everything handed to the backend | 71 | 37 | 80.6% | 6.8 s |
| Voice model decides per request | 84 | 38 | 91.0% | 4.7 s |

The voice model alone is fast and accurate on short commands but finishes only 14 of 48 multi-step tasks. Handing everything off fixes multi-step work but loses short commands and is the slowest. Letting the voice model decide gets the best of both: the highest success and the lowest latency. That is why we keep a voice model in front and let it hand off the work that needs it.

## 5. End-to-end results on FDB-v3

Full-Duplex-Bench v3 has 100 spoken requests across shopping, travel, finance and housing, with twelve mock tools. A request passes only if every expected tool call is made with the right arguments.

The best setup lets the backend do the work: Fun-Audio-Chat as a hand-off-only frontend passes 68, against 51 when it also holds the 12 tools.

**Setups on FDB-v3, turn-based** (100 recordings; “hand-off only” means the voice model has only spawn\_thinking and Qwen3-14B does the work; “judge” is FDB-v3’s judging prompt run on Qwen3-14B, with $RESULT references resolved against the run’s own earlier results)

| Setup | Voice model | Pass (judge) | Pass (judge), benchmark errors counted | Pass (exact match) | Items handed off |
| --- | --- | --- | --- | --- | --- |
| Hand-off only | Fun-Audio-Chat | 68 | 78 | 51 | 100 |
| Hand-off only | MiniCPM-o | 22 | 32 | 18 | 41 |
| The 12 tools and spawn\_thinking | Fun-Audio-Chat | 51 | 61 | 47 | 1 |
| The 12 tools and spawn\_thinking | MiniCPM-o | 59 | 67 | 50 | 0 |

**MiniCPM-o in full duplex** (FDB-v3 recordings streamed in, 10 items per prompt)

| Setup | Items | Items with a function call |
| --- | --- | --- |
| Alone with the 12 tools | 10 | 0 |
| Hand-off only, strict prompt | 10 | 0 |

In full duplex it answers in speech with no call behind it, for example “I will track your order using the ID you provided.”

## 6. What broke, and what helped

The biggest single fix was not wording. With a bare “accepted” reply, Fun-Audio-Chat thought the call had failed and handed the same task off again, up to four times. Once spawn\_thinking replied with a task id and said the task isn’t done until its result arrives, repeats disappeared and the pass rate went from 36 to 68.

**Example: ecommerce\_02, Fun-Audio-Chat, hand-off only.** User: “…I’m looking for a pair of wireless headphones… under 100 dollars if possible. What do you have?”

| Turn | Plain “accepted” reply | Reply with a task id |
| --- | --- | --- |
| 1 | spawn\_thinking: “Find wireless headphones under $100” | spawn\_thinking: “Find wireless headphones under $100” |
| 2 | spawn\_thinking: “Retrieve wireless headphones under $100” | “I’m looking for wireless headphones under $100 for you. Just a moment while I find the best options!” |
| 3 | spawn\_thinking: “Fetch wireless headphones under $100” | — |
| 4 | spawn\_thinking: “Get wireless headphones under $100” | — |
| Backend searches | 4 | 1 |

## 7. Return path

Once the backend’s result arrives, the voice model relays it well. In Matthew’s full-duplex runs, writing the result into MiniCPM-o’s memory gets 93 to 97 percent of the tool values said to the user, against 7 percent with no return path. In my turn-based test, 95 percent of Fun-Audio-Chat’s replies after the result state only facts the result supports.

Verbatim injection has a flow problem: the voice model often replies first, for example “let me check that”, and the backend’s result only arrives after that turn, so a result dropped in word for word does not fit the conversation. I solved this with the task id and added fields: the handoff reply carries a task id and says the task is not finished yet, and the result later arrives with the same task id, marked completed, so the voice model can tie the late result back to its own earlier reply.

The open problem is the wait. Before the result comes back, the voice model often announces an outcome that hasn’t happened, like “I’ve added two items to your cart.” That happens in 6 to 15 percent of Matthew’s duplex runs, and in 17 percent of Fun-Audio-Chat’s turn-based handoffs, checked by hand. Writing the note in immediately, Matthew’s sys\_now, cuts it the most.

**Example: ecommerce\_14, Fun-Audio-Chat, hand-off only, task-id reply.** User: “…Can you add, like, 2 of item P-5-2 to my cart? I want to get one for myself and one as a gift.”

| When | What was said |
| --- | --- |
| Before the result | Voice model: “I’ve added two items of product P-5-2 to your cart, one for yourself and one as a gift.” Nothing has been added yet. |
| Backend result | “Two items of product P-5-2 have been added to the user’s cart. The cart total is now $199.98.” |
| After the result | Voice model: “Great! Two items of product P-5-2 have been added to your cart, and the total is now $199.98.” |

## 8. Caveats

A few things to keep in mind with these numbers.

The benchmark itself has 10 items whose expected answers are wrong: they conflict with its own tool definitions or with the recording. For example, three expect a pets\_allowed argument that the apartment search does not have. The column “benchmark errors counted” counts these 10 items as passed.

## 9. Why a system-level benchmark

Every benchmark we used so far scores a single model. Full-Duplex-Bench and τ-Voice ask whether one voice model makes the right tool call. Our system is a voice model plus a backend, and the abilities we care about sit between the two: whether a task gets handed off, whether a correction or a cancel reaches a task that is already running, and whether the result that reaches the user is right. We can run our system on these benchmarks, as I just showed, but they can’t show those abilities.

FDB-v3 does include 21 self-correction items, such as “flights to Paris — actually, no, make that Berlin.” But the correction comes in the same sentence, before any task exists, so there is only ever one task. These items test understanding, not interaction.&#32;

Voice agents are moving toward this kind of interaction model. I worked on early research in this direction during my internship. There are some open-souce projects are doing this field, such as qwen-audio-agent, dsh-voice-agent, TML and DupelxOmni. What is missing is a shared way to measure how well a whole system like this performs. I know qwen test the delegation task and some other bench verify the task understanding. But none of them test the whole system, i mean interaction performance.

## 10. Next step

Your 134-case cockpit set tests the whole system’s routing and final results. Qwen-Audio-Agent added update\_agent\_task on Oct 15 for changing a running task; before that, I added a function call like it to our harness myself. In my own API tests, tasks were completed very well. But I haven’t seen tests of what happens once a task is running — corrections, cancels, and the asynchronous process — so we still want to build this benchmark, with an agent as a judge: an evaluator agent plays the user, changes or cancels the request once the task has been handed off, and then judges whether the final result is right.
