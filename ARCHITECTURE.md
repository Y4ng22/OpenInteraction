# InteractFormer Architecture Design

> Detailed architecture documentation for the InteractFormer framework.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [System Architecture](#system-architecture)
3. [Interaction Model (S1)](#interaction-model-s1)
4. [Background Model (S2)](#background-model-s2)
5. [Streaming Context Bridge](#streaming-context-bridge)
6. [Temporal Grid](#temporal-grid)
7. [Orchestrator & Scheduling](#orchestrator--scheduling)
8. [Data Flow](#data-flow)
9. [Comparison with TML & DuplexOmni](#comparison)

---

## Design Philosophy

InteractFormer is guided by three principles:

### 1. The Bitter Lesson
> "Hand-crafted systems will be outpaced by scalable, learned approaches."

Following Sutton's "bitter lesson" and TML's design philosophy, we minimize hand-crafted components:
- **No external VAD** — speech detection is learned from temporal patterns
- **No explicit turn markers** — `[CUT]`, `[WAIT]`, `[PENDXS]` are replaced by learned temporal attention
- **No pre-trained encoders** — dMel + hMLP are co-trained with the transformer

### 2. Separation of Concerns
Real-time interaction and deep reasoning have fundamentally different requirements:

| | Interaction (S1) | Background (S2) |
|---|---|---|
| **Latency** | <200ms | Seconds to minutes |
| **State** | Continuous streaming | Stateless queries |
| **Output** | Speech, gestures, text | Reasoning, facts, actions |
| **Interruptibility** | Immediate | Deferrable |

### 3. Progressive Information Flow
Information should flow continuously, not in discrete batches:
- S2 results stream back incrementally
- S1 can use partial S2 results before completion
- The Bridge manages timing, ordering, and cancellation

---

## System Architecture

```
                          ┌──────────────────┐
                          │    Orchestrator   │
                          │  (Session + Tick) │
                          └────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼────────┐  ┌───────▼────────┐
     │  Interaction     │  │   Streaming    │  │   Background   │
     │  Model (S1)      │◄─┤   Context      ├─►│   Model (S2)   │
     │                  │  │   Bridge       │  │                │
     │  ┌────────────┐  │  │                │  │  ┌──────────┐  │
     │  │  Encoder   │  │  │ ┌────────────┐ │  │  │ Reasoner │  │
     │  │  (dMel+    │  │  │ │ Context    │ │  │  └──────────┘  │
     │  │   hMLP)    │  │  │ │ Packager   │ │  │  ┌──────────┐  │
     │  └─────┬──────┘  │  │ │ (S1 → S2)  ├─┼──┼─►│Retriever │  │
     │        │         │  │ └────────────┘ │  │  └──────────┘  │
     │  ┌─────▼──────┐  │  │ ┌────────────┐ │  │  ┌──────────┐  │
     │  │  Temporal  │  │  │ │ Stream     │ │  │  │  Tool    │  │
     │  │  Grid      │  │  │ │ Injector   │◄┼──┼──┤ Executor │  │
     │  │  (200ms)   │  │  │ │ (S2 → S1)  │ │  │  └──────────┘  │
     │  └─────┬──────┘  │  │ └────────────┘ │  │  ┌──────────┐  │
     │        │         │  │ ┌────────────┐ │  │  │  Fusion  │  │
     │  ┌─────▼──────┐  │  │ │Cross-Attn  │ │  │  │  Layer   │  │
     │  │  Thinker   │◄─┼──┤ │Fusion      │◄┼──┼──┤          │  │
     │  │  (MoE)     │  │  │ │(Per-layer) │ │  │  └──────────┘  │
     │  └─────┬──────┘  │  │ └────────────┘ │  │                │
     │        │         │  │                │  │                │
     │  ┌─────▼──────┐  │  └────────────────┘  └────────────────┘
     │  │  Talker    │  │
     │  │  (Stream)  │  │
     │  └─────┬──────┘  │
     │        │         │
     │   Output Stream  │
     └──────────────────┘
```

---

## Interaction Model (S1)

The Interaction Model is the real-time face of InteractFormer. It maintains continuous presence and handles all direct user interaction.

### Components

#### Multimodal Encoder
```
Input (Audio/Video/Text) → Modality-specific encoding → Early Fusion
```

- **Audio**: Raw 24kHz → dMel spectrogram (80 mel bands) → Conv1D embedding → d_model
- **Vision**: 40×40 patches → hMLP blocks → patch embeddings → d_model
- **Text**: Token IDs → learned embedding table → d_model
- **Fusion**: Concatenation along time axis + modality type embeddings + lightweight transformer layer

**Why dMel instead of Whisper?**
- No pre-training needed (encoder-free, co-trained)
- Lower latency (no encoder forward pass)
- The "bitter lesson" argument: let the transformer learn audio features

#### Temporal Grid
*See [Temporal Grid](#temporal-grid) section below.*

#### Thinker (MoE Transformer)
```
Embeddings + Temporal PE → MoE Decoder (24 layers) → Hidden States
```

- **Architecture**: 24 transformer layers with Grouped Query Attention (GQA)
- **FFN**: Mixture-of-Experts (8 experts, top-2 routing)
- **Bridge Slots**: 4 reserved attention slots per layer for S2 context
- **Heads**: Text prediction, Delegation prediction, Speech gate

#### Talker (Streaming Speech Generator)
```
Hidden States → State-to-Codec → MTP → Code2Wav → Waveform
```

- **MTP**: Multi-Token Prediction for efficient multi-codebook generation
- **Code2Wav**: Convolutional decoder with overlap-add for streaming
- **Streaming**: Frame-by-frame generation (80ms frames, 12.5Hz)
- **Interruptible**: Can stop mid-utterance

### Processing Flow (One Micro-Turn)

```
1. Audio chunk (200ms, 4800 samples @ 24kHz) arrives
2. Encoder: dMel → [20 frames, 80 mel, d_model]
3. Temporal Grid: Create cell, compute position encoding
4. Thinker: Process through MoE layers, attend to bridge slots
5. Speech Gate: Decide whether to speak
6. Talker: If speaking, generate one frame of audio (1920 samples)
7. Delegation Check: If complex query detected, delegate to S2
8. Output: Speech audio + text + metadata
```

---

## Background Model (S2)

The Background Model handles everything that doesn't need sub-200ms latency.

### Multi-Background Ensemble

Unlike DuplexOmni's single pluggable S2, InteractFormer runs multiple background models in parallel:

```
Delegation from S1
    │
    ├── Reasoner (deep chain-of-thought)
    │   └── Yields: ReasoningStep objects (streamed)
    │
    ├── Retriever (knowledge search)
    │   └── Yields: RetrievalResult objects
    │
    └── Tool Executor (API calls, code)
        └── Yields: ToolResult objects
        │
        ▼
    Fusion Layer (confidence-weighted)
        │
        ▼
    Streaming Context Bridge → S1
```

### Fusion Strategy
```
confidence = w_r * avg_reasoning_conf +
             w_k * avg_retrieval_score +
             w_t * tool_success_rate

final_answer = best reasoning path +
               top-k retrieval facts +
               tool execution summary
```

### Streaming Results
Instead of waiting for all reasoning to complete, intermediate steps are streamed:
- Step 1: "Let me think about this..." → S1 can say "Hmm, let me think..."
- Step 2: "The user is asking about Tokyo's population..." → S1 can show progress
- Step 3: "[FINAL] Tokyo has 14 million people..." → S1 speaks the answer

---

## Streaming Context Bridge

**The Bridge is InteractFormer's core architectural innovation.**

### The Problem
- **DuplexOmni**: S2 results injected as `「...」` text markers → breaks speech flow, coarse-grained
- **TML**: "Rich context packages" mentioned but mechanism not specified
- **Both**: No support for progressive streaming of partial results

### Our Solution: Cross-Attention Fusion

Instead of text markers, S2 results are encoded as **bridge vectors** that S1 attends to via cross-attention:

```
For each transformer layer i:
    S1_hidden_i → CrossAttn(Q=S1, K=BridgeVectors, V=BridgeVectors)
                → Gate(S1_hidden_i, BridgeOutput) → S1_hidden_i+1
```

**Advantages:**
1. **Smooth**: No text markers disrupting speech flow
2. **Progressive**: Partial S2 results can be used immediately
3. **Selective**: Learned gate can ignore irrelevant S2 context
4. **Multi-stream**: Different bridge slots for reasoning, retrieval, tools
5. **Per-layer**: Different layers can use different amounts of bridge info

### Injection Scheduling

The `InjectionScheduler` decides when to inject each chunk:

| Strategy | Behavior |
|----------|----------|
| **Eager** | Inject immediately on arrival |
| **Scheduled** | Wait for turn boundary (model not speaking) |
| **Adaptive** | Critical info immediately; normal info at boundaries |

### Bidirectional Communication

```
S1 → S2: ContextPackager.build_package()
         → Rich context: conversation, temporal state, multimodal snapshot

S2 → S1: StreamInjector.receive_result()
         → Chunked into 200ms-aligned BridgeMessages
         → Injected via CrossAttentionFusion
```

---

## Temporal Grid

The Explicit Temporal Grid structures all interaction through 200ms time cells.

### Why Explicit?

TML describes micro-turns conceptually. DuplexOmni uses continuous streams. InteractFormer makes time **explicit** through:

1. **Grid Cells**: Each 200ms slice is a discrete `GridCell` with its own state machine
2. **Position Encoding**: Learned time embeddings (not just token positions)
3. **Silence Encoding**: The model learns that longer silences = different meanings
4. **Attention Masking**: Causal masking with limited lookahead for planning

### Cell Lifecycle

```
pending → encoding → thinking → talking → injecting → complete
                         ↑           │
                         └───────────┘ (can loop: thinking ↔ talking)
```

### Time-Awareness Capabilities

The Temporal Grid enables:

| Capability | Description | TML Benchmark |
|-----------|-------------|---------------|
| **TimeSpeak** | Proactive speech at the right time | 64.7 (vs GPT-realtime 4.3) |
| **CueSpeak** | Respond to verbal/nonverbal cues | 81.7 (vs GPT-realtime 2.9) |
| **Turn-taking** | Natural conversational rhythm | Learned from grid patterns |
| **Interruption** | Graceful mid-speech interruption | No `[CUT]` markers needed |

---

## Orchestrator & Scheduling

### Micro-Turn Scheduler

The scheduler fires at 200ms intervals, executing this tick pipeline:

```
Tick N:
  ├── INPUT_COLLECT:   Gather audio/video from input streams
  ├── ENCODE:          Multimodal encoding (dMel + hMLP)
  ├── THINK:           Thinker forward pass
  ├── BRIDGE_CHECK:    Check for pending S2 injections
  ├── DECIDE:          Speech gate + delegation check
  ├── TALK:            Talker forward pass (if speaking)
  └── OUTPUT_DISPATCH: Send speech/text to client
```

### Latency Management

If a tick exceeds the 200ms budget:
1. Talker quality is reduced (fewer ODE steps)
2. Non-critical bridge checks are skipped
3. Overflow input is queued for next tick

### Session Lifecycle

```
INITIALIZING → ACTIVE ⇄ IDLE ⇄ BACKGROUND_PROCESSING
                  │
                  ├── PAUSED → ACTIVE (resume)
                  │
                  ├── RECONNECTING → ACTIVE (reconnected)
                  │
                  └── ENDING → ENDED
```

---

## Data Flow

### Delegation Flow (S1 → S2 → S1)

```
1. S1 detects complex query (delegation_score > 0.5)
2. ContextPackager builds rich context:
   - Conversation history (last 50 turns)
   - Temporal state (silence duration, speaking ratios)
   - Multimodal snapshot (latest visual context)
   - Interaction state (current cell, pending injections)
3. BackgroundModel receives task → distributes to ensemble
4. Results stream back via Bridge:
   - Intermediate reasoning steps (partial results)
   - Retrieval results (as they arrive)
   - Tool execution results
5. StreamInjector chunks results into 200ms-aligned messages
6. CrossAttentionFusion injects at appropriate temporal grid cells
7. S1 incorporates S2 knowledge into ongoing interaction
```

### Streaming Audio Flow

```
Microphone (24kHz)
    │
    ▼
chunk_audio_stream() → 200ms chunks (4800 samples)
    │
    ▼
AudioEncoder.dmel() → mel spectrogram → embeddings
    │
    ▼
TemporalGrid.create_cell() → GridCell
    │
    ▼
Thinker.forward() → hidden states
    │
    ▼
Talker.forward() → speech waveform (streaming)
    │
    ▼
Speaker output
```

---

## Comparison with TML & DuplexOmni

### Architecture Comparison

```
TML Interaction Models:
┌─────────────┐     ┌─────────────┐
│ Interaction │ ←?→ │ Background  │
│ Model       │     │ Model       │
└─────────────┘     └─────────────┘
Communication: "Rich context packages" (unspecified mechanism)

DuplexOmni:
┌─────────────┐ 「...」markers  ┌─────────────┐
│ S1 (Inter-  │◄──────────────►│ S2 (Think-  │
│ action)     │                │ ing Layer)  │
└─────────────┘                └─────────────┘
Communication: Text marker injection + [CUT]/[WAIT] conventions

InteractFormer:
┌─────────────┐ Cross-Attn     ┌─────────────┐
│ Interaction │◄──────────────►│ Background  │
│ Model (S1)  │   per layer    │ Model (S2)  │
│             │   + gate       │             │
│  Temporal   │   + schedule   │  Multi-     │
│  Grid       │                │  Ensemble   │
└─────────────┘                └─────────────┘
Communication: Streaming Context Bridge (cross-attention fusion)
```

### Detailed Comparison

| Aspect | TML | DuplexOmni | InteractFormer |
|--------|-----|------------|----------------|
| **S1/S2 Communication** | Rich context package (unspecified) | `「...」` text markers | Cross-attention fusion |
| **Injection Timing** | Not specified | Immediate (marker insertion) | Adaptive (scheduled by priority) |
| **Partial Results** | Not specified | No | Yes (progressive streaming) |
| **Multi-S2 Support** | Not specified | Single pluggable | Multi-Ensemble |
| **Time-Awareness** | Micro-turns (conceptual) | None | Explicit Temporal Grid |
| **Turn Management** | Implicit (learned) | `[CUT]` + `[WAIT]` markers | Implicit (learned from grid) |
| **Audio Frontend** | dMel (co-trained) | Qwen's AuT (~650M) | dMel (configurable) |
| **Vision Frontend** | hMLP | Qwen's ViT | hMLP (lightweight) |
| **Codec** | Flow matching | Qwen codec + Mimi | Flow matching + configurable |
| **Training Data** | Proprietary | Writer-Director pipeline (~9TB) | Same pipeline, open-source |
| **Model Size** | 276B / 12B active | 35B | Configurable (30B/7B/etc) |
| **Open Source** | ❌ No | ✅ Apache 2.0 | ✅ Apache 2.0 |
| **Production Ready** | Research preview | Yes (8×H20) | WIP |

---

## References

1. Thinking Machines Lab. "Interaction Models: A Scalable Approach to Human-AI Collaboration." (2026)
2. Huang et al. "DuplexOmni: Real-Time Listening, Seeing, Thinking, and Speaking for Full-Duplex Interaction." (2025)
3. Qwen Team. "Qwen3-Omni: Natively Omni-Modal Foundation Models." Alibaba Cloud. (2025)
4. Kyutai. "Moshi: A Speech-Text Foundation Model for Real-Time Dialogue." (2024)
5. Bai et al. "dMel: Differentiable Mel-Spectrogram." (2024)
6. Touvron et al. "ResMLP: Feedforward Networks for Image Classification." (2022)
7. Lipman et al. "Flow Matching for Generative Modeling." (2022)
