---
title: Handoff traces
order: 2
date: 2026-10-26
status: done
kind: traces
---
MiniCPM-o 4.5 or Fun-Audio-Chat-8B as the frontend, Qwen3-14B as the backend, on 100 Full-Duplex-Bench v3 items. Pass rates use FDB-v3's strict scorer with a Qwen3-14B judge; a $RESULT reference in the expected arguments counts as matched when the call used a value an earlier call in the same run returned (`score_chain.py --resolve-refs`). The column “benchmark errors counted” also counts as passed the 10 items whose gold answer is wrong (`report/benchmark_errors.json`); those items carry a flag in their trace. Pick a setup, then an item, to see every step in the order it happened.
