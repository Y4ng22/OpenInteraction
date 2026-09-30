# Reproducing the TML Interaction-Model Direction

This repository can reproduce the public *design direction* of Thinking
Machines Lab's interaction model, but not their unreleased weights or exact
training recipe. The public target has five defining properties:

1. continuous audio, video, and text are aligned into 200ms micro-turns;
2. input and output chunks are interleaved in one persistent sequence;
3. the model learns silence, overlap, interruption, and proactive output rather
   than delegating turn-taking to a VAD/dialog harness;
4. audio uses dMel and images use 40x40 patches with lightweight early fusion;
5. a real-time interaction model exchanges rich context and streaming results
   with an asynchronous background model.

The repository now includes `MicroTurnInterleaver`, which materializes the
chronological `input_0 -> output_0 -> input_1 -> output_1` training layout and
retains completely silent 200 ms cells with a learned time-cell token. Token
ids stay in separate modality namespaces through explicit stream ids; output
text/audio positions alone are selected by the loss mask.

## Can DuplexOmni weights be used?

For actual deployment, MiniCPM-o 4.5 is now the selected S1 baseline because
its official service already exposes audio/video full-duplex Realtime I/O on a
single-GPU scale. The DuplexOmni section below remains a compatibility record
and optional teacher comparison, not the default deployment path. See
`docs/MINICPMO_DEPLOYMENT.md`.

Yes, but as a complete bootstrap model or teacher—not as a direct checkpoint
for the custom `InteractionModel` classes.

The released DuplexOmni config and this prototype overlap at a few boundaries:

| Field | DuplexOmni | Current student | Consequence |
|---|---:|---:|---|
| Thinker hidden size | 2048 | 2048 | embedding rows are shape-compatible |
| Vocabulary | 152064 | 152064 | tokenizer assets can be reused |
| Thinker layers | 48 | 24 | transformer blocks cannot load |
| Attention heads | 32 | 16 | Q/K/V tensor layouts differ |
| KV heads | 4 | 4 | this one field matches, but is insufficient |
| Experts / active | 128 / 8 | 8 / 2 | MoE weights and routers cannot load |
| Audio frontend | 32-layer Qwen encoder, 128 mel | dMel embedding | incompatible |
| Vision patches | 16 | 40 | incompatible |
| Talker hidden/layers | 1024 / 20 | custom lightweight MTP | incompatible |
| Codec groups/size | 16 / 2048 | 32 / 4096 | codec tokens are not interchangeable |

Run the compatibility check before downloading model shards:

```bash
python scripts/check_duplex_compat.py MuyeHuang/DuplexOmni
```

The script downloads only `config.json`, never checkpoint shards, unless the
config is already present locally.

## Recommended implementation path

### Phase A: runnable Duplex baseline

Run the complete DuplexOmni Thinker, Talker, codec, tokenizer, and its modified
vLLM serving stack unchanged. Treat it as an external S1 backend owned by one
persistent session. The local orchestrator should own session authorization,
200ms client cadence, S2 task routing, cancellation, metrics, and safety.

Do not force the released model to accept the local prototype's tensors. Its
published config uses two-second model chunks and 13 position IDs per second;
the 200ms boundary should initially be a transport/scheduling boundary with an
audio accumulator until the model has been fine-tuned for finer chunks.

### Phase B: interaction fine-tuning

Reuse DuplexOmni's public data pipeline and checkpoint, but rebuild examples as
a time-indexed event stream. Every example needs synchronized user audio,
assistant audio, optional video, silence, overlap labels, interruption/cut
events, tool/delegation events, and target output timing. LoRA is appropriate
for an initial behavior experiment; it is not enough to replace the audio or
vision frontend.

The next-token objective must include *when not to emit*. A training sample
that only contains successful spoken turns cannot teach stable silence,
backchanneling, or visual proactivity.

### Phase C: asynchronous background coordination

Train delegation and result reintegration separately from ordinary response
generation. S2 receives a versioned context snapshot. Every streamed result
must carry session ID, task ID, source micro-turn, topic/version, confidence,
and expiry. Stale results are discarded rather than blended into a new topic.

### Phase D: TML-style student

Only after the Duplex baseline is measurable should the project replace its
frontends with dMel, 40x40 hMLP vision, and a flow audio head. Distill at four
levels:

- semantic logits and selected hidden states;
- speech/no-speech and interruption policy;
- codec or acoustic targets through an explicit learned adapter;
- temporal behavior on TimeSpeak/CueSpeak-style timing windows.

The student's new frontend, temporal embeddings, Bridge slots, and audio head
must be trained. Copying a matching token embedding does not train those paths.

## Non-negotiable serving work

A real 200ms system needs conversation-lifetime KV state. Recomputing the last
five seconds on every call is useful for correctness tests but not a viable
latency design. The serving layer needs persistent GPU sequences, append-only
micro-prefills, per-session KV quotas, cancellation/barge-in, codec state, and
backpressure. Measure P50/P95/P99 first-packet and micro-turn latency under
concurrent sessions, not only offline generation speed.

## Validation gates

1. No dropped or duplicated audio samples across arbitrary network packet sizes.
2. No state, Bridge result, codec buffer, or KV cache crosses session IDs.
3. Output timing is graded independently from semantic correctness.
4. Long silence and overlapping speech appear in both training and evaluation.
5. Tool and retrieval results are versioned, expiring, and cancellable.
6. Checkpoints are pinned by revision and loaded without arbitrary remote code
   unless the deployment explicitly accepts that trust boundary.
