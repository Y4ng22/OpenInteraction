#!/usr/bin/env python3
"""Send one safe probe through the configured InteractFormer S2 backend."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from interactformer.background.reasoner import reasoning_backend_from_env


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the external S2 LLM")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--query", default="只回答：S2 豆包连接正常")
    args = parser.parse_args()

    _load_env_file(Path(args.env_file))
    backend = reasoning_backend_from_env()
    print(f"provider={os.environ.get('S2_LLM_PROVIDER', 'deterministic')}")
    print(f"model={getattr(backend, 'model', 'deterministic')}")
    for step in backend.generate_stream(
        args.query,
        {"probe": True, "source": "scripts/probe_s2.py"},
    ):
        if step.is_final:
            print(step.thought)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
