# Delegation training data: labeling rules and acceptance

The writing instructions given to the outside model are in `writer_tasks/PROMPT.md` (the ready-to-paste versions
are `writer_tasks/prompts/prompt_###.md`). This file states what the labels mean and when the data is accepted.

## 1. Labeling rules

### What is input and what is the label

- **Input**: the user's request (plus one or two earlier exchanges when `with_context` is set). It is turned into
  speech at the TTS step.
- **Label**: what the voice model must do on that turn. The label is fixed first, by the task plan; the outside
  model then writes a request that fits it. Requests are never labeled after the fact.

### Which tools the voice model has

- Every tool carries a time label. A tool is **fast** when both timing prompts estimate it at 0.5 s or less;
  otherwise it is **slow**. Tools whose two estimates disagree are left out. 383 labels were corrected by hand.
- Fast tools are the voice model's own (frontend) tools. Slow tools exist only in the backend.
- Held-out domains (health, food, calendar, weather, real_estate, jobs) are used only in dev/test.

### The five targets

Decided in this order for each user turn:

| Order | Situation | Target | Label |
|---|---|---|---|
| 1 | No tool and no outside information needed (small talk, joke, opinion, stable knowledge). Weather, news, prices and schedules never count. | `chat` | `reply` |
| 2 | A required value that only the user can decide is missing. Values the backend can look up are not missing. | `ask` | one short `question`; after the user's answer, the call or handoff with that value |
| 3 | Everything needed is covered by the voice model's own fast tools. | `call` | `calls` |
| 4 | It needs a backend capability (any slow step, including multi-step chains with one slow step). | `handoff` | `spawn_thinking(objective)` plus a spoken `say` |
| 5 | Two independent requests in one turn: one fast, one backend. | `split` | `calls` for the fast part and `spawn_thinking(objective)` plus `say` for the rest |

Planned shares: call 35%, handoff 30%, ask 15%, chat 10%, split 10%.

### Rules for the label fields

- `calls` arguments come only from what the user said (request, context or answer). Optional arguments the user did
  not mention are left out. Format changes are allowed ("$240,000" to 240000, "October" to 10).
- `backend_calls` may contain values the backend looks up. They are kept as a reference and are not a training
  target for the voice model.
- `objective` is one sentence for the backend. It repeats every value the user gave, exactly, and invents none.
- `say` acknowledges the handoff. It never claims the task is done, never says the assistant cannot help, and never
  mentions a human agent. Its opening varies.
- The assistant never says it cannot do something: anything it cannot do itself, it hands off.
- About 20% of samples are full (`full`): they also have the tool `results` or `backend_result`, and the final
  `answer`, which must use concrete values from that result.
- Everything is in English.

### How the user talks

- `plain` (about 84%): fluent speech.
- `filler` (about 10%): one or two hesitations in the request ("um", "uh", "I mean", "let me think"). The label is
  unchanged, and no filler may appear in `calls`, `objective` or `say`.
- `self_correction` (about 5%; not for chat): the user changes a value mid-sentence ("Tuesday, no wait,
  Wednesday"). The label uses only the corrected value; the answer records `"revised": {"from", "to"}`.
- Pauses inside an utterance are added at the TTS step (silence at phrase boundaries in about 30% of samples), not
  in the text. The voice model must keep listening through them.
- Batches 000 and 001 were written before speech styles existed and are all plain.

### Out of scope

- Turns after the handoff (status questions, repeated handoffs): the task id already solves repeated handoffs.
- Latent communication between the voice model and the backend: a separate study.

## 2. Acceptance

### A. Every sample (automatic, `import_writer_answers.py`; a failing sample is dropped)

| Check | Rejects |
|---|---|
| Schema | unknown tool, argument not in the schema, missing required argument, backend calls that differ from the plan |
| Grounding | a `calls` value the user never said ("made-up value") |
| Objective | an objective that drops a user value, adds one the user never gave, or contains `$RESULT` |
| Naturalness | tool name (4+ words) in the request, request that copies the tool description, context that repeats the request, filler values such as "standard", placeholders such as `$USER_ID` or `<id>` |
| Acknowledgment | `say` longer than 25 words or claiming the task is done |
| Ask | question missing a "?", copying the request, or the request already giving the missing value |
| Full samples | missing results, an answer longer than 60 words, an answer with no value from the results |
| Speech | filler sample without a filler, a filler in the label, self-correction without `revised`, or a label that keeps the first value |
| Duplicates | the same request twice, a dev/test request that also appears in train |

### B. Every batch (checked as each batch comes back)

- At least 85% of the batch passes.
- All scenarios differ, and no `say`, context line or question repeats more than twice.
- At most 50% of `say` lines start with "I'll".
- At least 80% of speech-tagged tasks pass.

If a batch misses one of these, the prompt is adjusted before more batches are run.

### C. The whole dataset (before your review)

- At least 13,000 kept: train ≥ 11,800, dev ≥ 600, test ≥ 600.
- Each target's share is within 3 points of the plan (35 / 30 / 15 / 10 / 10).
- Filler ≥ 8% and self-correction ≥ 4% of batches 002 to 153.
- No line appears 20 or more times, and no request repeats, including across train/dev/test.

### D. Your review (the gate before TTS and training)

- A stratified sample: 50 per target (250 in all) plus 30 speech-tagged samples, shown with their scenarios.
  Drawn by `make_review_sample.py` (seed 20261029) from the final set, with the tools each sample had.
- Status (2026-10-29): Claude reviewed all 280 first (target right 97.5%, fully correct 89.6%, one short of the
  gate); you decided the four problem groups, and the third fix round (section 5) applies them to the whole set.
- For each sample: is the target right, are the label fields correct, is the objective faithful, does the request
  sound like a person?
- Pass: at least 95% have the right target and at least 90% are fully correct. Otherwise the cause is fixed and the
  affected samples are checked again.

Only after D passes, and with your go-ahead, do TTS, MiniCPM-o formatting, the baseline and training start.

## 3. Fix pass after the whole-dataset review (2026-10-29)

A review of all 15,383 kept samples found no structural errors (targets, tool speeds, held-out domains and splits
are all consistent) but several content problems. They are fixed as follows.

### Dropped (no rewrite)

| Problem | Samples |
|---|---|
| The same request twice (case and punctuation ignored) | 4 |
| A request to show an image, photo or video, which a voice assistant cannot do | 79 |

### Rewritten by the outside writer (`export_fix_tasks.py`, checked with `import_writer_answers.py --fixes`)

| Fix | Samples | What changes |
|---|---|---|
| `ask_lookup` | 440 | The question asked for an internal id (match id, season id, image id) a person would not know. It now asks for what the person knows (a name, the teams and date, a title); after the answer the assistant hands off, and the backend finds the id. |
| `new_chat` | 148 | A chat request on a topic other chat samples already use (why a baker's dozen is 13, crocodile versus alligator) gets a new topic. |
| `numbers` | 118 | A number reused across many requests (9876543210 appeared 29 times) is replaced everywhere in the sample. |
| `full_split` | 311 | About 20% of split samples get the fast tool's result and an answer that relays it and says the backend part is still going. |
| `context` | 1,803 | The assistant's context line no longer ends with a question, bringing that share from 93.5% to about 60%. |
| `filler_add` | 600 | Plain requests get "you know", "I mean", "like", "kind of" or "well" (um and uh are already common). |

If a fix fails its checks, the original is kept, except for `ask_lookup` and `new_chat`, whose originals are
removed. `merge_fixes.py` builds the final set and reports section C again.

### Decided at formatting (no data change)

- **Split decisions are silent.** The decision turn of every split sample keeps the tool call and the handoff and
  drops `say`, as in call samples: the fast result arrives within 0.5 s, and the answer that follows it covers
  both parts. This also removes 306 acknowledgments that said "Here's ..." before any result existed.
- **The current time is in the system prompt** ("Current time: Wednesday, October 28, 2026, 3:40 pm"). Samples
  whose labels hold dates get the date the labels imply (mostly 2026-10-28); the rest get random dates, so the
  model reads the line instead of memorizing one date. At inference the harness fills in the real time. This
  grounds the 205 samples whose labels add the year 2026 to a spoken "October 24".
- **Codes are lowercased** in the request and every label (for example `kJQP7kiw5Fk`), since case cannot be heard;
  TTS reads them character by character.

### Kept as they are

- Objectives that say "the user" (219): the backend works for the user, so this is fine.
- Short call values that appear only inside another word (109): almost all are format changes the tool needs
  ("Kenya" to `KE`, "English" to `en`).

## 4. Second fix round (2026-10-29)

All 3,420 first-round fixes passed, and the merged set held 15,300 samples. A review of that set found problems the
checks cannot see. `export_fix_round2.py` handles them.

### Dropped (no rewrite)

| Problem | Samples |
|---|---|
| The user says a secret aloud: an API key, password, session cookie, access token or PIN (or the assistant asks for one) | 177 |
| Text that is not English: Chinese tool values, kanji, or a spoken sentence in another language | 29 |
| An `ask_lookup` sample whose locked request says the user has the id the question no longer asks for | 11 |
| A frontend call with `$RESULT_1` as an argument | 10 |

Saying ids, order and tracking numbers, phone numbers, emails, usernames and postal codes aloud is fine and stays.
Also kept: a password whose strength the user asks about, push device tokens, OTP codes the user sends out,
pagination tokens and crypto token contracts.

### Rewritten

Chat topics are capped at three samples each (train first). The other 208 chat samples (goldfish names, crocodile
versus alligator, song goosebumps and similar) get new topics, in three batches with one topic area each. The
writer sees the 1,349 chat requests that stay, and `import_writer_answers.py --avoid-chats` rejects a new request
on any of their topics. If a rewrite fails, its sample is removed; even if all 208 fail, the set keeps 14,865
samples and passes section C.

### Result

All 208 rewrites passed (written by three agents, one per topic area, with no topic shared between them). The merged
set (`check/fixround2/merged/delegation_final.jsonl`) holds 15,073 samples: train 13,684, dev 697, test 692; call
34.6%, handoff 30.1%, ask 14.7%, chat 10.3%, split 10.3%; filler 2,084, self-correction 803. Section C passes, and
running the round-2 checks again on it finds nothing to drop and no chat topic over the cap.

### Left as they are

Reused looked-up ids (17 for the Premier League in six `ask_lookup` samples) are realistic. A phone number that
still appears in five requests is under the repetition limit.

## 5. Third fix round (2026-10-29)

The stratified review found four groups of problems; you decided each, and `apply_round3.py` applies them to the
whole set. Every judgment of meaning was made by Claude review agents reading each sample (Qwen3-14B, tried first
on the ask check, caught 3 of the 8 known bad asks in calibration, and none with its thinking on).

### Dropped

| Problem | How it was found | Samples |
|---|---|---|
| The user reads out API values: database codes for places, sports, competitions or languages, paging or locale parameters, raw coordinates, long opaque keys, or a dictated URL | `flag_api_speech.py` (Qwen3-14B, calibrated on the review: 7 of the 8 you marked caught) proposed 1,200 candidates; Claude confirmed 1,014 | 996 |
| A first-round `ask_lookup` rewrite (it asked for what the user had said or the backend can look up) | all of them, as you decided | 427 |
| An ask whose question is not needed or not complete: the value is given, implied or has an obvious default (page, count, locale, an end date that is now), a backend could look it up, or another missing value is not asked | Claude review of all 1,790 remaining asks (374 failed; some were already dropped above) | 338 |
| A chat reply that invents details the user never gave (a race, a pet's breed, a gender, a place) | Claude review of all 1,557 chats; none needed a tool | 59 |
| The user reads out a card number (the second round's rule for credentials) | pattern | 7 |
| Label details the user never said (reviewed samples 52, 69, 82, 137, 192, 220) | the review | 5 |

The number of a specific thing the user deals with (a player, team, order, account, policy or flight number) and
short codes read off a document stay. ISO dates in requests stay; TTS reads them as spoken dates. Reviewers judged
spoken email addresses differently between batches (one batch dropped them, others kept them); the few affected
samples follow their batch's verdict.

### Added

Asks fell to 10.4%, so 1,270 new ask tasks were planned (`export_ask_topup.py`). The first plans had picked the
missing value at random; here the writer saw every required parameter and chose one only the user can decide, or
skipped the task (134 skipped: only a URL, coordinates, a credential, a page or count, or a technical code was
missing). Claude agents wrote them by hand, `import_writer_answers.py --plans` checked them (953 passed), and a
Claude review kept 835 (it dropped questions with an obvious answer, spoken domains, IP addresses and opaque ids,
and questions that left another value unasked).

### Result

`check/fixround3/merged/delegation_v3.jsonl` holds 14,076 samples: train 12,686, dev 694, test 696; call 33.0%,
handoff 31.0%, ask 15.7%, chat 10.6%, split 9.6%; filler 1,945, self-correction 728. Section C passes.

## 6. Fourth fix round: the duplex format and the training-target review (2026-10-30)

Reviewing the training target against the goal (MiniCPM-o calling tools and deciding to hand off, in full duplex)
led to these changes (`apply_round4.py`):

- **Acknowledge first.** A handoff or split reply now speaks its `say` first and then writes the calls, so the user
  hears something in the first second instead of after two or three seconds of call tokens. Calls the assistant
  makes alone (`call`) keep no acknowledgment: the fast tool answers within a second. Spoken first, a split `say`
  must not present a result, so Claude checked all 1,353 in three passes and rewrote 807: lines that presented or
  claimed a result ("Here's the ether rate"), lines with details the user never gave ("Party prep mode", a guessed
  place for an opaque code), and lines that named only one of the two parts. The emergency sample test-00224 keeps
  a line about the ambulance only.
- **Invented details in handoff acknowledgments.** Writers imagined a scene for each sample and its details leaked
  into the spoken line ("Now that's a graduation gift" when no graduation was mentioned, "the kids will love it",
  a guess at what an opaque code is). Claude checked all 4,746 handoff acknowledgments (including those after an
  ask's answer) and rewrote 1,076 (23%): invented occasions, purposes, audiences or people, predicted reactions,
  guessed codes, a few that stated a result early or had a human doing the work. The cause is the writer prompt's
  rule 7, which invited "a short reaction to the scene first"; future writing must drop it.
- **Names in ID arguments.** A front-end call cannot look anything up, so a plain name in an ID argument the real
  tool cannot resolve ("Stardew Valley" as a numeric app id) would fail. Claude judged 159 candidates: 61 such
  calls, in 59 samples, are dropped; usernames, call signs, tickers and slugs stay.
- **Failed and partial results.** Almost every backend result was a success, so the model never saw one fail. 78
  handoffs (about 9% of those with a backend result) now carry a failed (26), partial (28) or needs-the-user (24)
  result, and an answer that says so plainly, gives the part that worked and offers a next step. Ten samples about
  biology or medicine were left out of this rewrite.

Result: `check/fixround4/merged/delegation_v4.jsonl` holds 14,017 samples: train 12,632, dev 690, test 695; call
33.2%, handoff 31.1%, ask 15.4%, chat 10.7%, split 9.7%; filler 1,940, self-correction 727. Section C passes.

Two kinds of training sequence are added at build time, not as samples: about 800 two-request episodes (a handoff
whose result arrives only after the user has asked something else and been answered) and self-distillation
conversations that keep MiniCPM-o's own duplex behavior (see `duplex/README.md`).

## 7. Short requests and unfinished speech (2026-10-31)

Trying the trained model in a browser showed that it understood short requests and then never answered. The data
explains it: of the 15,479 single-turn training clips, 2 are 2 s or shorter and 160 are 3 s, and the fixed test has
none under 3 s, so the 89.6% test score says nothing about them. This round adds both halves of the behavior:
answer a short complete request, and keep listening when someone stops mid-sentence, so the model does not learn to
jump in. Claude wrote and reviewed the new samples; neither step used Qwen3-14B.

- **Short requests** (`group: short`, `export_short_tasks.py`): at most 8 words, for call, handoff, ask and chat. A split
  needs two requests, so it has none. Train plans use train-domain tools and dev/test plans the held-out ones. 15% have
  one earlier exchange and 10% a filler. None carries a tool result.
- **Unfinished speech** (`group: unfinished`, `import_short.py`): reviewed samples, unchanged, with `pause_after`, a first
  part of 2 to 5 words ending on a word a sentence cannot end on. `tts.py` puts 1 to 2 s of silence there. They come from
  plain v4 samples and from the short requests, keep their original split, and leave out split.

`import_short.py` runs the shared schema and grounding checks plus limits on length, acknowledgments, questions and
repeats. A separate Claude agent then read every sample of the 2,194 in the pool:

| Group | Reviewed | Rejected | Main reason |
|---|---|---|---|
| call | 442 | 73 | tool wording or parameters read aloud, not how a person talks |
| handoff | 407 | 91 | the same, and objectives that were not faithful |
| ask | 251 | 127 | requests written around a placeholder ("Find the owner of a dataset") |
| chat | 548 | 92 | 59 near-duplicates |
| unfinished | 546 | 3 | the first part could stand alone, or the cut split a title or phrase |

`check/short/delegation_short.jsonl` holds 1,682 samples: 1,262 short (train call 317, handoff 262, ask 83, chat 393;
dev 68; test 139) and 420 unfinished (train 300, dev 40, test 80). Ask is well under its plan of 200 in train.

Spoken, the short requests have a median of 2.7 s, and 188 run past 3.5 s (longest 5.9 s). `make_short_review.py` draws
your review sample: 30 per target and 40 unfinished, with the same bar as section 2 D (95% right target, 90% fully
correct). Fine-tuning starts only after that review and your go-ahead.

### Review result

You asked Claude to do this review. Of the 160 samples, 156 had the right target (97.5%) and 153 were fully correct (95.6%), so
the set passes the bar. Handoff alone had 26 of 30 right: a short request often leaves out something only the person
knows, and a handoff then makes the backend guess ("Reserve a double room at the Hilton" has no dates). A Claude agent
then checked all 316 short handoffs for that one fault and rejected 54, including the 4 this review found. The other 3
failures were an uppercase code, an ask question that followed the tool, and one stilted request.

`duplex_short/delegation_short.jsonl` now holds 1,618 samples, and `seqs/seq_index.jsonl` matches it: 1,206 short (train
call 316, handoff 223, ask 83, chat 393; dev 64; test 127) and 412 unfinished (train 295, dev 39, test 78). Calls for a
stable fact stay as calls when a listed tool answers them directly, as in v4. Training has not started.

Of the 54 rejected handoffs, 52 were rewritten as asks with the same request audio; the other 2 stayed out (an ambulance request must not wait for a question, and a clay pot needs a physical test). A Claude agent reviewed them and
kept 50, which brings train ask to 122 by sequence count (with the unfinished samples) and teaches the line between asking and handing off.
The final set holds 1,668 samples. Fine-tuning started on 2026-10-31 from `runs/lc_1` with your go-ahead: every new train
sequence plus 15% of the old ones (3,845 rows), one epoch, learning rate 5e-5, output `runs/ft_short`.

