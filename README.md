# OpenInteraction

<h3 align="center">Real-Time Multimodal Interaction Framework with Streaming Dual-Model Architecture</h3>

---

## Overview

OpenInteraction is a research framework for **real-time, full-duplex human-AI interaction** across audio, video, and text modalities. The runnable S1 baseline uses the official **MiniCPM-o 4.5 Realtime service**, while the custom Interaction Model remains a trainable TML-style student. A Background Model (S2) performs asynchronous deep reasoning through the Streaming Context Bridge research path.

> **Project status:** this repository is an architecture prototype, not a
> pretrained custom interaction model. The student S1 modules and codec heads are randomly
> initialized unless you load trained weights. S2 defaults to a deterministic
> development backend, and the retriever/search/code-interpreter integrations
> are extension points. The Qwen identifier currently supplies the tokenizer;
> it does not automatically turn this implementation into Qwen3-Omni. For a
> runnable full-duplex baseline use the isolated MiniCPM-o Realtime backend. Use a
> pinned tokenizer/model cache and a real, isolated S2/tool backend before
> production deployment.

### Why OpenInteraction?

Existing approaches to real-time AI interaction fall short:

| Approach | Limitation |
|----------|-----------|
| Closed-source interaction models | Not reproducible; massive compute requirements |
| Marker-based S1/S2 injection | Coarse-grained; breaks speech flow |
| Single-model architecture | No async background reasoning |
| Audio-only models | No vision or multi-modal support |

**OpenInteraction addresses these gaps** with an open-source, modular architecture that:
1. Separates real-time interaction from deep reasoning (**dual-model**)
2. Uses **cross-attention fusion** instead of text markers for S1/S2 communication
3. Introduces an **Explicit Temporal Grid** for time-aware interaction
4. Supports **Multi-Background Ensemble** for parallel reasoning, retrieval, and tool use

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      OpenInteraction                         │
│                                                              │
│  ┌──────────────────────┐    ┌──────────────────────────┐  │
│  │  Interaction Model   │    │   Background Model (S2)   │  │
│  │  (S1) — Real-time    │    │   — Async Reasoning       │  │
│  │                      │    │                           │  │
│  │  Input Stream ──→    │    │   ┌── Reasoner (CoT)      │  │
│  │    │                 │    │   ├── Retriever (RAG)     │  │
│  │    ▼                 │    │   └── Tool Executor       │  │
│  │  Temporal Grid ──→   │    │         │                 │  │
│  │    │         ↑       │    │         ▼                 │  │
│  │    ▼         │       │    │   Fusion Layer            │  │
│  │  Thinker ──→ Talker  │    │         │                 │  │
│  │    │                 │    │         ▼                 │  │
│  │    ▼                 │◄───┼─── Streaming Context      │  │
│  │  Output Stream       │    │    Bridge (SCB)           │  │
│  └──────────────────────┘    └──────────────────────────┘  │
│                                                              │
│  200ms Micro-Turns  ·  Early Fusion  ·  Implicit Turn Mgmt  │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Role | Innovation |
|-----------|------|------------|
| **Interaction Model (S1)** | Real-time streaming interaction | Temporal Grid, implicit turn management |
| **Background Model (S2)** | Async reasoning, retrieval, tools | Multi-Background Ensemble |
| **Streaming Context Bridge** | S1 ↔ S2 communication | Cross-attention fusion (not text markers) |
| **Temporal Grid** | 200ms micro-turn management | Explicit time-aligned position encoding |
| **Orchestrator** | Session & scheduling coordination | Adaptive latency management |

---

## Quick Start

### Installation

```bash
git clone https://github.com/Y4ng22/OpenInteraction.git
cd OpenInteraction
pip install -e .
```

### Basic Usage

```python
from interactformer import Orchestrator

# Initialize
orch = Orchestrator(d_model=2048, micro_turn_ms=200)
orch.initialize()

# Create a session
session = orch.create_session(user_id="user_001")
session.start()

# Process streaming audio in 200ms chunks
for audio_chunk in audio_stream:
    output = orch.process_micro_turn(
        session_id=session.session_id,
        audio_chunk=audio_chunk,
    )
    if output.speech is not None:
        play_audio(output.speech)  # Streaming speech output

orch.end_session(session.session_id)
```

### Demo

```bash
python scripts/run_demo.py --duration 30 --with-background --verbose
```

### Tests

```bash
python -m pytest -q
```

HTTP demos bind to `127.0.0.1` by default. If `scripts/simple_server.py` is
exposed on a non-loopback address, set `INTERACTFORMER_API_KEY`; remote model
code loading remains disabled unless `INTERACTFORMER_TRUST_REMOTE_CODE=1` is
explicitly set.

For the TML-style reproduction plan and the safe DuplexOmni bootstrap strategy,
see [`docs/TML_REPRODUCTION.md`](docs/TML_REPRODUCTION.md). Check a local or
remote DuplexOmni `config.json` before downloading its weight shards:

```bash
python scripts/check_duplex_compat.py MuyeHuang/DuplexOmni
```

The selected deployment backend is MiniCPM-o 4.5. Environment, image, GPU and
deployment commands are in [`docs/MINICPMO_DEPLOYMENT.md`](docs/MINICPMO_DEPLOYMENT.md):

```bash
python scripts/check_minicpmo_environment.py --path .
pip install -r requirements-minicpmo-client.txt
```

---

## Project Structure

```
interactformer/
├── interaction/          # Interaction Model (S1)
│   ├── encoder.py        #   Early-fusion multimodal encoder (dMel + hMLP)
│   ├── temporal_grid.py  #   Explicit 200ms Temporal Grid ⭐
│   ├── thinker.py        #   MoE-based fast reasoning
│   ├── talker.py         #   Streaming speech synthesis
│   └── interaction_model.py
├── background/           # Background Model (S2)
│   ├── reasoner.py       #   Chain-of-thought reasoning
│   ├── retriever.py      #   Knowledge retrieval (RAG)
│   ├── tool_executor.py  #   External tool execution
│   └── background_model.py  # Multi-Background Ensemble ⭐
├── bridge/               # Streaming Context Bridge ⭐
│   ├── context_packager.py  # Rich context packaging (S1→S2)
│   ├── stream_injector.py   # Progressive chunk injection (S2→S1)
│   └── cross_attention.py   # Neural fusion mechanism
├── orchestrator/         # Coordination layer
│   ├── session.py        #   Session lifecycle management
│   ├── scheduler.py      #   Micro-turn scheduler (200ms heartbeat)
│   └── orchestrator.py   #   Main control loop
├── codec/                # Audio codec
│   ├── audio_encoder.py  #   dMel encoder (encoder-free early fusion)
│   └── audio_decoder.py  #   Flow matching decoder
└── utils/
    ├── config.py         # Configuration management
    └── streaming.py      # Streaming primitives
```

⭐ = Novel contribution over existing work

---

## Key Innovations

### 1. Streaming Context Bridge (SCB)
Instead of text-marker-based injection, OpenInteraction uses **cross-attention based fusion** at every transformer layer for progressive, time-aligned injection of background knowledge.

### 2. Explicit Temporal Grid
All interaction is structured through explicit 200ms time cells with learned position encodings, enabling **TimeSpeak** (proactive speech timing) and **CueSpeak** (verbal cue detection).

### 3. Multi-Background Ensemble
OpenInteraction supports **parallel heterogeneous background models** (reasoning + retrieval + tools) with confidence-weighted fusion.

### 4. Implicit Turn Management
No VAD, no explicit markers, no turn-prediction harness. Turn-taking and interruption are **learned from temporal patterns** in the grid.

---

## Hardware Requirements

| Configuration | GPU | Use Case |
|--------------|-----|----------|
| Minimal (4-bit) | 1× RTX 4090 (24GB) | Development & testing |
| Recommended (FP8) | 2× RTX 4090 (48GB) | Research & demos |
| Production (BF16) | 8× H20 / 4× A100 | Full performance |

---

## Citation

```bibtex
@software{openinteraction2026,
  title = {OpenInteraction: A Real-Time Multimodal Interaction Framework},
  year = {2026},
  url = {https://github.com/Y4ng22/OpenInteraction},
}
```

## License

Apache License 2.0.
