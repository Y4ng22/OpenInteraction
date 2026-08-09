# MiniCPM-o 4.5 deployment for InteractFormer

The selected production S1 baseline is the official MiniCPM-o 4.5 Realtime
service. InteractFormer connects to its `/v1/realtime` WebSocket instead of
loading the 9B checkpoint into the custom research `InteractionModel` class.

## Image decision

### Recommended: official Docker Compose

Choose a Linux rental with Docker support and a recent NVIDIA driver. The
official worker image builds from `python:3.12-slim-bookworm` and installs its
own CUDA-enabled PyTorch wheels, so the host does not need a CUDA Toolkit.

Required host components:

- Linux x86_64 (Ubuntu 22.04 or 24.04 is convenient);
- NVIDIA driver 570+ recommended for the CUDA 12.8 wheel path;
- Docker Engine with Compose v2;
- NVIDIA Container Toolkit;
- one GPU per worker;
- 80GB free disk, 32GB system RAM minimum, and 8 CPU cores recommended.

Do not pay extra for a vLLM image. MiniCPM-o full duplex uses the official
Gateway -> Worker -> PyTorch Backend Realtime stack, not the normal vLLM
OpenAI-compatible text endpoint. An existing vLLM installation may stay on the
machine, but stop it before MiniCPM-o starts and never install both stacks in
the same Python virtual environment.

### If the rental platform does not allow Docker

Choose this image, in descending order:

1. PyTorch 2.8.0 + CUDA 12.8 + Python 3.10/3.12 + Ubuntu 22.04;
2. CUDA 12.8 development/runtime image + Ubuntu 22.04, then create Python 3.10
   virtualenv;
3. a clean Ubuntu 22.04 image with driver 570+, then install the cu128 wheels.

Install the worker stack in an isolated virtual environment:

```bash
python3.10 -m venv .venv/minicpmo
source .venv/minicpmo/bin/activate
pip install --upgrade pip
pip install torch==2.8.0 torchaudio==2.8.0 \
  --index-url https://download.pytorch.org/whl/cu128
pip install \
  transformers==4.51.0 \
  accelerate==1.12.0 \
  'safetensors>=0.7.0' \
  'minicpmo-utils[all]>=1.0.5'
```

The remaining official service dependencies are:

```text
fastapi>=0.128.0       uvicorn>=0.40.0
httpx>=0.28.0          websockets>=16.0
pydantic>=2.11.0       numpy>=2.2.0
PyYAML>=6.0            librosa>=0.10.2
soundfile>=0.12.1      python-multipart
markdown>=3.6          Pygments>=2.18.0
```

System packages:

```bash
sudo apt-get update
sudo apt-get install -y git ffmpeg libsndfile1 curl
```

FlashAttention is optional. The maintained official installer currently uses
SDPA by default. First make the service pass its smoke test with SDPA; only
then enable `torch.compile`.

## GPU choice

| GPU | Recommendation | Notes |
|---|---|---|
| RTX 4090 24GB | Experimental/local | Initialized usage is about 21.5GB, but the current service asks for more than 28GB; close every other GPU process and use one worker. |
| L40S 48GB | Best rental choice | Comfortable memory, Ada architecture, suitable for `torch.compile`. |
| RTX 6000 Ada 48GB | Recommended | Similar deployment profile to L40S. |
| A100 40GB/80GB | Supported | 40GB is enough; compile helps keep one-second units real-time. |
| H100/H200 | Supported/overkill | Useful for concurrency, not required for one session. |
| 12-16GB GPU | C++ quantized only | Use llama.cpp-omni/GGUF, not the full PyTorch service. |

For the first rental, select one L40S 48GB, at least 32GB RAM, 8 vCPU, and
100GB disk. Do not rent multiple GPUs until one end-to-end session passes.

## Repository deployment flow

On the rented Linux machine, upload or clone this repository and run the
read-only check before downloading weights:

```bash
python3 scripts/check_minicpmo_environment.py --path .
```

Then prepare the maintained official demo and the approximately 20GB model:

```bash
python3 -m pip install 'huggingface_hub[cli]'
bash scripts/deploy_minicpmo_docker.sh prepare
```

Optional reproducibility pins:

```bash
export MINICPMO_DEMO_REF='<tested-git-commit>'
export MINICPMO_MODEL_REVISION='<tested-huggingface-commit>'
```

Start and inspect the service:

```bash
bash scripts/deploy_minicpmo_docker.sh up
bash scripts/deploy_minicpmo_docker.sh logs
```

Only Gateway port 8006 should be reachable. Worker port 22400 and backend port
22500 stay private. Initially use an SSH tunnel instead of opening the service:

```bash
ssh -L 8006:127.0.0.1:8006 user@server
```

Install the lightweight client separately and run an audio smoke test:

```bash
pip install -r requirements-minicpmo-client.txt
python scripts/probe_minicpmo_realtime.py \
  --url http://127.0.0.1:8006 \
  --input-wav test_16k_or_24k.wav \
  --output-wav response.wav
```

Add `--jpeg frame.jpg` to test omnimodal video mode. The adapter accepts 200ms
local chunks at 16kHz or 24kHz, resamples them to 16kHz, and combines five into
the official one-second input unit. Output audio is decoded at 24kHz.

## Security and browser access

- Do not expose port 8006 over plain HTTP on the public internet.
- Use HTTPS/WSS with a valid certificate for browser microphone/camera access.
- Put authentication and rate limits in a reverse proxy; set
  `MINICPMO_API_TOKEN` when the proxy uses Bearer authentication.
- Keep Hugging Face tokens in environment variables, never in YAML.
- Use headphones during duplex testing; documented echo-cancellation limits
  can otherwise reduce interruption accuracy.

## S2 Bridge boundary

MiniCPM-o's public Realtime API exposes audio/video input and text/audio output,
but not internal hidden-state slots. InteractFormer therefore keeps S2 task
routing outside the model. Mid-session S2 hidden-state injection requires an
official-backend extension or a trained student; the existing custom Bridge
remains the research/distillation path.
