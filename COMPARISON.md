# InteractFormer vs. Prior Work

> A detailed comparison of InteractFormer with existing real-time interaction models.

---

## Overview

| | TML Interaction Models | DuplexOmni | Qwen3-Omni | Moshi | **InteractFormer** |
|---|---|---|---|---|---|
| **Year** | 2026 | 2025 | 2025 | 2024 | **2026** |
| **Organization** | Thinking Machines Lab | Independent | Alibaba | Kyutai | **This Project** |
| **License** | Closed | Apache 2.0 | Apache 2.0 | CC-BY 4.0 | **Apache 2.0** |
| **Model Size** | 276B / 12B | 35B | 30B / 3B | 7B | **Configurable** |
| **Modalities** | Audio + Video + Text | Audio + Video + Text | Audio + Image + Video + Text | Audio only | **Audio + Video + Text** |

---

## Architecture Deep Dive

### 1. Model Topology

```
TML:                   DuplexOmni:            InteractFormer:
┌──────┐ ┌──────┐     ┌──────┐ ┌──────┐     ┌──────┐ ═══Bridge═══ ┌──────┐
│  IM  │ │  BM  │     │  S1  │ │  S2  │     │  IM  │◄──Fusion──►│  BM  │
└──────┘ └──────┘     └──────┘ └──────┘     │(S1)  │  per-layer  │(S2)  │
    ?        ?          「...」   pluggable    │Grid  │  +schedule   │Multi │
                                              └──────┘              └──────┘
```

**Key difference**: InteractFormer makes the S1↔S2 communication an explicit, designed component (the Bridge) rather than an ad-hoc mechanism.

### 2. S1/S2 Communication

| | TML | DuplexOmni | **InteractFormer** |
|---|---|---|---|
| **Mechanism** | Not specified | Text markers (`「...」`) | **Cross-attention fusion** |
| **Granularity** | "Package" | Whole result at once | **200ms chunks** |
| **Partial Results** | ? | ❌ No | **✅ Progressive streaming** |
| **Cancellation** | ? | ❌ No | **✅ Topic-change aware** |
| **Multiple Streams** | ? | Single endpoint | **✅ Multi-Ensemble** |
| **Layer Integration** | ? | Input only | **Every transformer layer** |

### 3. Temporal Modeling

| | TML | DuplexOmni | Qwen3-Omni | **InteractFormer** |
|---|---|---|---|---|
| **Time Unit** | 200ms micro-turns | Continuous stream | None | **200ms Grid Cells** |
| **Position Encoding** | Not disclosed | Standard RoPE | TMRoPE | **Temporal PE + Silence PE** |
| **Proactive Speech** | TimeSpeak (64.7) | ❌ | ❌ | **Grid-based prediction** |
| **Cue Detection** | CueSpeak (81.7) | ❌ | ❌ | **Silence duration encoder** |
| **Turn Boundaries** | Learned | `[CUT]` markers | Thinker-speak | **Learned (speech gate)** |

### 4. Modality Processing

| | TML | DuplexOmni | Qwen3-Omni | **InteractFormer** |
|---|---|---|---|---|
| **Audio Encoder** | dMel (co-trained) | AuT (~650M) | AuT (~650M) | **dMel (configurable)** |
| **Vision Encoder** | hMLP | Qwen ViT | Qwen ViT | **hMLP (lightweight)** |
| **Early Fusion** | Encoder-free | Encoder-based | Encoder-based | **Encoder-free** |
| **Co-training** | All from scratch | Fine-tuned | Pre-trained + fine-tune | **From checkpoint** |

### 5. Turn Management

| | Traditional | DuplexOmni | **InteractFormer** |
|---|---|---|---|
| **VAD** | External VAD module | No external VAD | **No external VAD** |
| **Markers** | Turn indicators | `^` `[CUT]` `[WAIT]` `[PENDXS]` | **None** |
| **Interruption** | Hard rules | Learned from markers | **Learned from temporal patterns** |
| **Backchanneling** | Scripted | Marker-based | **Temporal grid pattern** |

### 6. Deployment & Hardware

| | TML | DuplexOmni | Qwen3-Omni | **InteractFormer** |
|---|---|---|---|---|
| **Minimal GPU** | Unknown (cloud only) | 8×H20 (768GB) | 1×A100 (40GB) | **1×4090 (4-bit)** |
| **Quantization** | Not available | Not available | AWQ/GPTQ community | **Planned AWQ + GGUF** |
| **Streaming Server** | SGLang fork | vLLM fork + WebSocket | vLLM | **Planned WebSocket** |
| **On-device** | ❌ | ❌ | ❌ | **Future (MLX)** |

---

## Novel Contributions of InteractFormer

### Contribution 1: Streaming Context Bridge

**Problem**: Existing approaches use ad-hoc mechanisms for S1/S2 communication.

**Solution**: A three-component Bridge architecture:
1. **ContextPackager**: Structured S1→S2 delegation (not just a text query)
2. **StreamInjector**: Adaptive scheduling of S2→S1 injection
3. **CrossAttentionFusion**: Neural mechanism for per-layer, gated fusion

**Prior art gap**: No previous system has all three. DuplexOmni has structured delegation but uses text markers for injection. TML describes "rich context packages" but the mechanism is unspecified.

### Contribution 2: Explicit Temporal Grid

**Problem**: Real-time models lack explicit temporal awareness. They process streams as token sequences, losing the notion of "when."

**Solution**: A structured grid where each 200ms cell has:
- Learned temporal position encoding
- Silence duration embedding
- State machine lifecycle
- Attention masking with lookahead

**Prior art gap**: TML mentions micro-turns conceptually. DuplexOmni has no temporal structure. Our Grid makes time a first-class citizen of the architecture.

### Contribution 3: Multi-Background Ensemble

**Problem**: Single S2 models (DuplexOmni) are monolithic and can't parallelize different types of background work.

**Solution**: Parallel heterogeneous background models with confidence-weighted fusion:
- Reasoner (chain-of-thought)
- Retriever (RAG)
- Tool Executor (API calls)

**Prior art gap**: No existing system supports parallel, heterogeneous background processing with streaming partial results.

### Contribution 4: Implicit Turn Management

**Problem**: Turn management traditionally relies on external VAD (Moshi), explicit markers (DuplexOmni), or not handled at all (Qwen3-Omni).

**Solution**: Learned from temporal grid patterns:
- Speech gate: Predicts speaking opportunities from temporal context
- Interruption detector: Learned from co-occurrence of input + output
- No explicit markers or rules

**Prior art gap**: TML also uses implicit (learned) turn management but the mechanism is not disclosed. DuplexOmni uses explicit markers. Our approach is both implicit AND documented.

---

## Performance Targets

Based on TML's benchmarks and existing open-source baselines:

| Benchmark | GPT-realtime-2.0 | TML Interaction-Small | DuplexOmni | **InteractFormer Target** |
|-----------|-----------------|----------------------|------------|--------------------------|
| FD-bench v1 latency | 1.18s | 0.40s | 0.51s | **<0.50s** |
| FD-bench v1.5 avg | 46.8 | 77.8 | 72.6 | **>72.0** |
| Big Bench Audio | — | — | 77.2 | **>75.0** |
| TimeSpeak | 4.3 | 64.7 | — | **>50.0** |
| CueSpeak | 2.9 | 81.7 | — | **>60.0** |

*Note: InteractFormer is currently an architecture framework. Performance numbers are targets.*

---

## Summary

InteractFormer's position in the landscape:

```
Feature Completeness
    ↑
    │                    ● TML (closed)
    │              ● InteractFormer
    │         ● DuplexOmni
    │    ● Qwen3-Omni
    │ ● Moshi
    └──────────────────────────→ Openness (Apache 2.0)
```

InteractFormer aims to combine the architectural sophistication of TML's Interaction Models with the openness of Apache-licensed projects, while introducing novel contributions in bridge communication, temporal modeling, and ensemble background processing.
