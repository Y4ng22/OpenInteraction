#!/usr/bin/env python
"""Inspect DuplexOmni config compatibility without downloading model shards."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interactformer.compat.duplex import inspect_duplex_config, load_json_config


def _resolve_config(source: str, revision: str, local_files_only: bool) -> Path:
    path = Path(source)
    if path.is_dir():
        path = path / "config.json"
    if path.is_file():
        return path

    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(
        repo_id=source,
        filename="config.json",
        revision=revision,
        local_files_only=local_files_only,
    ))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare a DuplexOmni config with InteractFormer's S1 shapes."
    )
    parser.add_argument(
        "source", nargs="?", default="MuyeHuang/DuplexOmni",
        help="Local model directory/config.json or Hugging Face repository ID.",
    )
    parser.add_argument("--revision", default="main")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    config_path = _resolve_config(args.source, args.revision, args.local_files_only)
    report = inspect_duplex_config(load_json_config(config_path))
    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_markdown())


if __name__ == "__main__":
    main()
