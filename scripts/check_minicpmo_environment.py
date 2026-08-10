#!/usr/bin/env python3
"""Read-only preflight check for a MiniCPM-o 4.5 deployment host."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interactformer.deployment.minicpmo import (  # noqa: E402
    assess_minicpmo_environment,
    parse_nvidia_smi,
)


def _run(command):
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=".", help="Disk path used for free-space check")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    gpu_ok, gpu_output = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    try:
        gpus = parse_nvidia_smi(gpu_output) if gpu_ok else []
    except ValueError as exc:
        print(f"nvidia-smi parse error: {exc}", file=sys.stderr)
        gpus = []
    docker_ok, _ = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    compose_ok, _ = _run(["docker", "compose", "version", "--short"])
    disk = shutil.disk_usage(Path(args.path).resolve())
    assessment = assess_minicpmo_environment(
        gpus,
        disk_free_gb=disk.free / (1024 ** 3),
        docker_available=docker_ok,
        compose_available=compose_ok,
        linux=platform.system() == "Linux",
    )

    output = assessment.to_dict()
    output["disk_free_gb"] = round(disk.free / (1024 ** 3), 1)
    output["platform"] = platform.platform()
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {assessment.profile}")
        print(f"ready: {assessment.ready}")
        print(f"disk_free_gb: {output['disk_free_gb']}")
        for gpu in assessment.gpus:
            print(f"gpu: {gpu.name} | {gpu.memory_total_mb} MiB | driver {gpu.driver_version}")
        for label, values in (
            ("ERROR", assessment.errors),
            ("WARN", assessment.warnings),
            ("NEXT", assessment.recommendations),
        ):
            for value in values:
                print(f"{label}: {value}")
    return 0 if assessment.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
