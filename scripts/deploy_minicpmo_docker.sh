#!/usr/bin/env bash
set -euo pipefail

# Reproducible wrapper around the maintained official MiniCPM-o Demo Compose.
# Usage: bash scripts/deploy_minicpmo_docker.sh check|prepare|up|logs|down

ACTION="${1:-check}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="${MINICPMO_RUNTIME_ROOT:-${PROJECT_ROOT}/.runtime/minicpmo}"
DEMO_DIR="${MINICPMO_DEMO_DIR:-${RUNTIME_ROOT}/MiniCPM-o-Demo}"
MODEL_DIR="${MINICPMO_MODEL_DIR:-${RUNTIME_ROOT}/models/MiniCPM-o-4_5}"
DEMO_REF="${MINICPMO_DEMO_REF:-main}"
MODEL_REVISION="${MINICPMO_MODEL_REVISION:-main}"

case "${ACTION}" in
  check)
    python3 "${PROJECT_ROOT}/scripts/check_minicpmo_environment.py" --path "${PROJECT_ROOT}"
    ;;
  prepare)
    mkdir -p "${RUNTIME_ROOT}/models"
    if [ ! -d "${DEMO_DIR}/.git" ]; then
      git clone https://github.com/OpenBMB/MiniCPM-o-Demo.git "${DEMO_DIR}"
    fi
    git -C "${DEMO_DIR}" checkout "${DEMO_REF}"
    if [ ! -f "${MODEL_DIR}/config.json" ]; then
      if ! command -v hf >/dev/null 2>&1; then
        echo "Missing 'hf' CLI. Install with: python3 -m pip install 'huggingface_hub[cli]'" >&2
        exit 2
      fi
      hf download openbmb/MiniCPM-o-4_5 \
        --revision "${MODEL_REVISION}" \
        --local-dir "${MODEL_DIR}"
    fi
    PROJECT_DIR="${PROJECT_ROOT}" DEMO_DIR="${DEMO_DIR}" \
      bash "${PROJECT_ROOT}/scripts/apply_minicpmo_ui_patches.sh"
    echo "Prepared demo: ${DEMO_DIR}"
    echo "Prepared model: ${MODEL_DIR}"
    ;;
  up)
    test -f "${DEMO_DIR}/docker-compose.yml"
    test -f "${MODEL_DIR}/config.json"
    cd "${DEMO_DIR}"
    MODEL_HOST_PATH="${MODEL_DIR}" docker compose up -d --build
    ;;
  logs)
    cd "${DEMO_DIR}"
    docker compose logs -f gateway worker-backend-0
    ;;
  down)
    cd "${DEMO_DIR}"
    docker compose down
    ;;
  *)
    echo "Unknown action: ${ACTION}; use check, prepare, up, logs, or down" >&2
    exit 2
    ;;
esac
