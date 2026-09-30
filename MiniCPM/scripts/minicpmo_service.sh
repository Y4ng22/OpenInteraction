#!/usr/bin/env bash
# Manage the official MiniCPM-o 4.5 bare-metal realtime service.
#
# Usage:
#   bash scripts/minicpmo_service.sh start
#   bash scripts/minicpmo_service.sh status
#   bash scripts/minicpmo_service.sh stop

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MINICPMO_BASE:-/root/autodl-tmp/minicpmo}"
REPO="${MINICPMO_DEMO_REPO:-$BASE/MiniCPM-o-Demo}"
MODEL_DIR="${MINICPMO_MODEL_DIR:-$BASE/models/MiniCPM-o-4_5}"
VENV="${MINICPMO_VENV:-$REPO/.venv/base}"
BIND_HOST="${MINICPMO_BIND_HOST:-127.0.0.1}"
BACKEND_PORT="${MINICPMO_BACKEND_PORT:-22500}"
WORKER_PORT="${MINICPMO_WORKER_PORT:-22400}"
GATEWAY_PORT="${MINICPMO_GATEWAY_PORT:-8006}"
INTERNAL_PORT="${MINICPMO_INTERNAL_PORT:-8007}"
LOG_DIR="$BASE/logs"
RUN_DIR="$BASE/run"
PYTHON="$VENV/bin/python"

export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="${HF_HOME:-$BASE/cache/huggingface}"
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-$BASE/cache/torch_compile}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

mkdir -p "$LOG_DIR" "$RUN_DIR" "$TORCHINDUCTOR_CACHE_DIR"

pid_file() {
    printf '%s/%s.pid' "$RUN_DIR" "$1"
}

expected_command() {
    case "$1" in
        backend) printf '%s' 'py_backend.server' ;;
        worker) printf '%s' 'worker.py' ;;
        gateway) printf '%s' 'gateway.py' ;;
        *) return 1 ;;
    esac
}

service_alive() {
    local name="$1" file pid expected command_line
    file="$(pid_file "$name")"
    [ -s "$file" ] || return 1
    pid="$(cat "$file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    expected="$(expected_command "$name")"
    command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    [[ "$command_line" == *"$expected"* ]]
}

tail_failure_log() {
    local name="$1"
    echo "---- $name log ----" >&2
    tail -80 "$LOG_DIR/$name.log" 2>/dev/null >&2 || true
}

wait_http() {
    local name="$1" url="$2" attempts="$3" insecure="${4:-0}" pid
    pid="$(cat "$(pid_file "$name")")"
    for ((i=1; i<=attempts; i++)); do
        if [ "$insecure" = "1" ]; then
            curl -ksf "$url" >/dev/null 2>&1 && return 0
        else
            curl -sf "$url" >/dev/null 2>&1 && return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            tail_failure_log "$name"
            return 1
        fi
        sleep 2
    done
    tail_failure_log "$name"
    return 1
}

stop_one() {
    local name="$1" file pid
    file="$(pid_file "$name")"
    if ! service_alive "$name"; then
        rm -f "$file"
        return 0
    fi
    pid="$(cat "$file")"
    kill -TERM "$pid"
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "$name did not stop within 20 seconds (PID $pid)." >&2
        return 1
    fi
    rm -f "$file"
}

stop_all() {
    local rc=0
    stop_one gateway || rc=1
    stop_one worker || rc=1
    stop_one backend || rc=1
    return "$rc"
}

status_all() {
    local name pid
    for name in backend worker gateway; do
        if service_alive "$name"; then
            pid="$(cat "$(pid_file "$name")")"
            echo "$name: running (PID $pid)"
        else
            echo "$name: stopped"
        fi
    done
    curl -sf "http://$BIND_HOST:$BACKEND_PORT/health" 2>/dev/null || true
    curl -sf "http://$BIND_HOST:$WORKER_PORT/health" 2>/dev/null || true
    curl -ksf "https://$BIND_HOST:$GATEWAY_PORT/workers" 2>/dev/null || true
}

require_runtime() {
    [ -x "$PYTHON" ] || { echo "Missing Python environment: $PYTHON" >&2; return 1; }
    [ -d "$REPO" ] || { echo "Missing official demo repository: $REPO" >&2; return 1; }
    [ -f "$REPO/certs/cert.pem" ] || { echo "Missing TLS certificate in $REPO/certs" >&2; return 1; }
    [ -f "$MODEL_DIR/model.safetensors.index.json" ] || { echo "Model download is incomplete: $MODEL_DIR" >&2; return 1; }
    local shard
    for shard in "$MODEL_DIR"/model-0000{1,2,3,4}-of-00004.safetensors; do
        [ -s "$shard" ] || { echo "Missing model shard: $shard" >&2; return 1; }
    done
}

start_all() {
    require_runtime
    if [ -f "$SCRIPT_DIR/apply_minicpmo_ui_patches.sh" ]; then
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")" DEMO_DIR="$REPO" \
            bash "$SCRIPT_DIR/apply_minicpmo_ui_patches.sh"
    fi
    local name
    for name in backend worker gateway; do
        if service_alive "$name"; then
            echo "$name is already running; refusing to start a duplicate." >&2
            return 1
        fi
    done

    cd "$REPO"
    [ -f config.json ] || cp config.example.json config.json

    echo "Starting backend; first model load can take several minutes..."
    nohup "$PYTHON" -m py_backend.server \
        --host "$BIND_HOST" --port "$BACKEND_PORT" \
        --gpu-id 0 --model-path "$MODEL_DIR" \
        >"$LOG_DIR/backend.log" 2>&1 < /dev/null &
    echo "$!" > "$(pid_file backend)"
    wait_http backend "http://$BIND_HOST:$BACKEND_PORT/health" 600 || {
        stop_all || true
        return 1
    }

    echo "Starting worker..."
    nohup "$PYTHON" worker.py \
        --host "$BIND_HOST" --port "$WORKER_PORT" \
        --gpu-id 0 --backend-server-url "http://$BIND_HOST:$BACKEND_PORT" \
        >"$LOG_DIR/worker.log" 2>&1 < /dev/null &
    echo "$!" > "$(pid_file worker)"
    wait_http worker "http://$BIND_HOST:$WORKER_PORT/health" 60 || {
        stop_all || true
        return 1
    }

    echo "Starting HTTPS gateway..."
    nohup "$PYTHON" gateway.py \
        --host "$BIND_HOST" --port "$GATEWAY_PORT" \
        --internal-port "$INTERNAL_PORT" --https \
        --ssl-certfile "$REPO/certs/cert.pem" \
        --ssl-keyfile "$REPO/certs/key.pem" \
        >"$LOG_DIR/gateway.log" 2>&1 < /dev/null &
    echo "$!" > "$(pid_file gateway)"
    wait_http gateway "https://$BIND_HOST:$GATEWAY_PORT/health" 60 1 || {
        stop_all || true
        return 1
    }
    wait_http gateway "http://$BIND_HOST:$INTERNAL_PORT/health" 60 || {
        stop_all || true
        return 1
    }

    curl -sf -X PUT \
        -H 'content-type: application/json' \
        --data "{\"endpoint\":\"$BIND_HOST:$WORKER_PORT\",\"gpu_group\":\"gpu-0\"}" \
        "http://$BIND_HOST:$INTERNAL_PORT/internal/workers/worker-0" >/dev/null

    echo "MiniCPM-o 4.5 realtime service is ready."
    echo "Gateway: https://$BIND_HOST:$GATEWAY_PORT"
    curl -ksf "https://$BIND_HOST:$GATEWAY_PORT/workers"
    echo
}

case "${1:-status}" in
    start) start_all ;;
    stop) stop_all ;;
    restart) stop_all && start_all ;;
    status) status_all ;;
    *) echo "Usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
