# OpenInteraction

**OpenInteraction explores how real-time multimodal interaction and long-horizon intelligence can coexist in a single interactive system.**

Traditional AI agents are largely turn-based: the user provides an input, the model reasons, and only then produces a response. During long reasoning or tool execution, interaction effectively pauses.

OpenInteraction instead separates interaction into two cooperating loops:

* **S1 — Interaction Loop:** continuously sees, listens, and responds to the user with low latency.
* **S2 — Background Loop:** performs slower reasoning, retrieval, and tool execution asynchronously.

The goal is simple: **S1 should never stop interacting with the user just because S2 is thinking.**

The current web runtime uses **MiniCPM-o 4.5 Realtime** as the S1 backbone. The `interactformer` package implements the temporal coordination, S1/S2 orchestration, and streaming context exchange layers around it.

> OpenInteraction is inspired by Thinking Machines Lab's *Interaction Models*, while focusing on an open and extensible implementation of dual-loop multimodal interaction.

---

## Why OpenInteraction?

Most agent systems follow a sequential pipeline:

```text
User
  ↓
Perception
  ↓
Reasoning
  ↓
Tool / Retrieval
  ↓
Response
  ↓
User
```

This works for turn-based tasks, but becomes unnatural in continuous interaction.

Imagine a robot helping a user while watching the surrounding environment. A cloud model may need several seconds to search, reason, or call tools. During that time, the robot should still be able to:

* listen to new speech;
* notice visual changes;
* acknowledge the user;
* handle interruptions;
* continue a conversation;
* decide whether new information changes the background task.

OpenInteraction therefore treats **interaction** and **reasoning** as concurrent processes rather than consecutive stages.

---

## The Two Loops

### S1 — Real-Time Interaction

S1 maintains the live connection between the AI and the physical world.

```text
        Audio
          │
Video ──► S1 ◄── Text
          │
          ▼
   Speech / Text
```

S1 is responsible for:

* continuous audio and video perception;
* full-duplex interaction;
* interruption and turn management;
* immediate responses;
* maintaining short-horizon interaction context;
* deciding when deeper computation should be delegated to S2.

The current implementation uses **MiniCPM-o 4.5 Realtime** as the S1 backbone.

OpenInteraction places incoming interaction events onto an explicit **Temporal Grid**, allowing multimodal state, model output, and background events to be associated with the same evolving timeline.

---

### S2 — Asynchronous Intelligence

Some requests cannot be answered within the latency budget of natural interaction.

Instead of blocking S1, these tasks are delegated to S2:

```text
S1
 │
 │ delegate(context, task)
 ▼
S2
 ├── Reasoning
 ├── Retrieval
 └── Tools
 │
 │ streaming result
 ▼
S1
```

S2 may take hundreds of milliseconds or several seconds to complete a task.

During that time, **S1 continues seeing, listening, and interacting with the user**.

When S2 produces intermediate or final results, they are streamed back into the live interaction context rather than forcing a new turn.

---

## Temporal Coordination

The central systems problem in OpenInteraction is not simply connecting two models.

The two loops operate at different timescales:

```text
Real-time interaction

0ms       200       400       600       800       1000
│----------│----------│----------│----------│----------│
 S1         S1         S1         S1         S1

                 └──────── S2 reasoning ────────────┐
                                                    │
                                             result arrives
                                                    ▼
                                                   S1
```

OpenInteraction introduces three components around this problem:

### Temporal Grid

Represents the evolving multimodal interaction as time-aligned cells containing input, output, and system events.

### Orchestrator

Coordinates S1 sessions, background tasks, interruptions, delegation, and result delivery.

### Streaming Context Bridge

Transfers relevant interaction context from S1 to S2 and incrementally returns S2 results to the live interaction loop.

Together, these components allow the fast interaction loop and slow reasoning loop to evolve concurrently without collapsing back into a conventional request-response pipeline.

---

## Architecture

### Web Runtime

```text
Camera / Microphone / Video
            │
            ▼
  OpenInteraction Web UI
            │  HTTPS + WebSocket
            ▼
         Gateway
            │
            ▼
          Worker
            │
            ▼
 MiniCPM-o 4.5 Backend (S1)
            │
            ▼
   Streaming Text + Speech
```

### Interaction Architecture

```text
Multimodal Events
       │
       ▼
Temporal Grid ─────► Orchestrator
                          │
                    delegate task
                          ▼
                  Context Packager
                          │
                          ▼
               Background Model (S2)
               ├── Reasoner
               ├── Retriever
               └── Tool Executor
                          │
                   streaming result
                          ▼
               Streaming Context Bridge
                          │
                          └────────► S1 interaction context
```

---

## What OpenInteraction Adds

MiniCPM-o 4.5 provides the realtime omni-modal S1 capability.

OpenInteraction focuses on the system around it:

* **Dual-loop execution** — realtime interaction continues while background reasoning runs asynchronously.
* **Explicit temporal coordination** — multimodal interaction and background computation share an evolving timeline.
* **Streaming S1/S2 context exchange** — background results can enter an ongoing interaction instead of starting a new turn.
* **Model-independent orchestration** — S1 and S2 are exposed through adapters so that different interaction and reasoning models can be evaluated.
* **Research instrumentation** — timing and interaction events can be recorded for latency and coordination experiments.

---

## Quick Start

### Requirements

- Linux x86_64
- Docker Engine, Docker Compose v2, and NVIDIA Container Toolkit
- NVIDIA driver 570+
- One 40GB GPU; 48GB is recommended
- 32GB system RAM and 80GB free disk space
- Git, Python 3, and pip

### Download and Start

```bash
git clone https://github.com/Y4ng22/OpenInteraction.git
cd OpenInteraction

python3 -m pip install 'huggingface_hub[cli]'

bash scripts/deploy_minicpmo_docker.sh check
bash scripts/deploy_minicpmo_docker.sh prepare
bash scripts/deploy_minicpmo_docker.sh up
```

`prepare` clones `OpenBMB/MiniCPM-o-Demo`, downloads
`openbmb/MiniCPM-o-4_5` (about 20GB), and installs the OpenInteraction UI.

### Open the Web UI

For a server deployment:

```text
https://<server-host>:8006/omni
```

For a local deployment:

```text
https://localhost:8006/omni
```

Allow camera and microphone access, select **Live**, choose a preset, and click
**Start**. The conversation supports interruption, Force Listen, video files,
recording, and fullscreen mode. Headphones are recommended for duplex audio.

### Health, Logs, and Shutdown

```bash
curl -k https://localhost:8006/status
bash scripts/deploy_minicpmo_docker.sh logs
bash scripts/deploy_minicpmo_docker.sh down
```

The service is ready when `gateway_healthy` is `true` and `idle_workers` is
greater than zero. Only HTTPS gateway port `8006` needs to be reachable.

---

## Optional S2 Provider

```bash
cp .env.example .env
# Add the API key and set the endpoint and model for your provider plan.
python scripts/probe_s2.py --env-file .env
```

Agent Plan uses `/api/plan`; other plans should use the endpoint shown in the
provider console. `.env` is ignored by Git.

---
