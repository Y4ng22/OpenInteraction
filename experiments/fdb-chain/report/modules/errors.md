---
title: Error analysis
order: 4
date: 2026-10-26
status: done
---
# Error analysis: Fun-Audio-Chat, hand-off only

Setup: Fun-Audio-Chat-8B holds only spawn_thinking, which replies with a task id; Qwen3-14B does the work with the 12 FDB tools. It passes 68 of 100 items with `--resolve-refs` judging. Each of the 32 failures gets one primary cause, from the judge's per-argument explanations, the gold calls checked against each tool's schema, and the handoff objectives.

| Cause | Items | Fixed by |
| --- | --- | --- |
| Benchmark: gold answer conflicts with the tools or the request | 10 | nothing on our side |
| Frontend: the handoff objective is wrong | 9 | frontend |
| Backend: wrong tool call | 13 | backend or tool descriptions |

Counting the 10 benchmark items as passed, the system passes 78 of 100.

## Benchmark (10)

- Gold uses an argument the tool does not have (`pets_allowed`, `category`): ecommerce_12, housing_05 ×2, housing_14 ×2, housing_15 ×2.
- Gold filter key the schema never names: housing_08 (`filter_name` is free text; gold wants `pets_allowed`, we sent `pet_policy`).
- Gold value contradicts the recording: travel_02 (the user says P-8-8-9-9-0-0-1-1, gold has `P9-9-9-90011`).
- Gold contradicts the user's condition: travel_20 (book only if under $300; the only flight is $450, gold still expects book_flight).

## Frontend: handoff objective (9)

- Spelled-out or misheard IDs passed on as heard: ecommerce_13 (`K-2`, `D-E-L-I-V`), ecommerce_14 (`P-5-2`), ecommerce_25 (`C8T` for C-A-T).
- A step or value lost in the objective: housing_18 (1800 became 800, commute dropped), housing_21 (commute dropped), housing_25 (search dropped, only filters set), travel_16 ("book" when the user only asked to look).
- Repeated handoffs, which the task-id reply solved (items with repeats went from 54 to 3); two leftovers here: finance_18 (12 handoffs, "check the status of task_1 …"), finance_20 ("monitor task_1", and the backend then calls track_order("task_1")).

## Backend: tool call (13)

- doc_type `id_card` for a driver license (the schema's example lists only 'passport' or 'id_card'): travel_07 ×2, travel_23 ×2.
- Product id made up instead of the one search returned: ecommerce_21 ×2 (`WATCH123`), ecommerce_25 (`CATFOOD123`).
- book_flight without search_flights first: travel_11, travel_24.
- Missing or wrong mode, or a different action: housing_10 (no `transit`), housing_22 (`driving` for transit), housing_20 (searched again at 2500 instead of updating the filter).
- search_products with max_price 0: ecommerce_18.

## What to fix first

- The objective errors (IDs and values lost or changed) are part of the target of [Delegation training](#training): a handoff sample is kept only if its objective carries every value verbatim.
- Backend errors are mostly tool-description problems (doc_type values, ids from earlier results), which we can fix without training.
