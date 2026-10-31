# Raising duplex performance (2026-10-30)

Goal on the corrected duplex test (695 held-out samples): target right at least 85% overall and at least 80% on every
target, exact calls at least 80%, and no worse than the untrained model on duplex behavior (280/300 pass, 14/104
cut-ins overrun). These numbers are our own bar, not a benchmark.

Steps, in order. Each is a row in the notebook's Progress tab.

1. Fix the test (done): replay context turns as in training, wait up to 30 s for a reply instead of 8 s.
2. Test `runs/lc_1` on the fixed test (`ops/adapter_eval3.py`), then the untrained model on the context samples.
3. Read the errors per target. `<|tts_bos|>` (id 151703) appears inside 6% of replies, trained or not, and can break
   call JSON; if it explains errors, add it to the decoder's forbidden ids in `duplex_test.py` and rerun what it touched.
4. If the bar is not met and the errors are ones the model should have learned: train `runs/v2` on the same reviewed
   data, weak targets used twice (`--oversample`), two epochs. Same LoRA settings. Test it the same way.
5. If a target is still below the bar: write new samples for it and stop for the user's review before any training.

Stop and report at step 5, on any crash, or if the behavior check gets worse.
