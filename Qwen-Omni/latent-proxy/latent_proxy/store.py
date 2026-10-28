"""Directory store for background latents.

Layout (read by gateway/server/src/latent/latent-store.mjs):

    <root>/<task_id>/<seq>.bin   row-major [tokens, dim] tensor, little endian
    <root>/<task_id>/<seq>.json  manifest describing the payload

The payload is written before the manifest and each file is moved into place
with a rename, so a reader that sees a manifest always finds a full payload.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict

from .markers import is_task_id

STORE_VERSION = 1
DTYPE_BYTES = {"float32": 4, "float16": 2, "bfloat16": 2}
MANIFEST_NAME = re.compile(r"^([1-9][0-9]{0,8})\.json$")


def _write_atomic(path: str, data: bytes) -> None:
    temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class FileLatentStore:
    def __init__(self, root: str) -> None:
        if not root:
            raise ValueError("FileLatentStore requires a root directory")
        self.root = os.path.abspath(root)
        self._lock = threading.Lock()

    def _next_seq(self, task_directory: str) -> int:
        seqs = [
            int(match.group(1))
            for match in (MANIFEST_NAME.match(name) for name in os.listdir(task_directory))
            if match
        ]
        return max(seqs, default=0) + 1

    def write(
        self,
        task_id: str,
        payload: bytes,
        *,
        tokens: int,
        dim: int,
        dtype: str,
        model: str,
        layer: int,
        final: bool,
    ) -> Dict[str, Any]:
        if not is_task_id(task_id):
            raise ValueError(f"invalid task id: {task_id!r}")
        if dtype not in DTYPE_BYTES:
            raise ValueError(f"unsupported dtype: {dtype!r}")
        if tokens <= 0 or dim <= 0:
            raise ValueError("tokens and dim must be positive")
        expected = tokens * dim * DTYPE_BYTES[dtype]
        if len(payload) != expected:
            raise ValueError(f"payload has {len(payload)} bytes, expected {expected}")
        task_directory = os.path.join(self.root, task_id)
        with self._lock:
            os.makedirs(task_directory, exist_ok=True)
            seq = self._next_seq(task_directory)
            manifest = {
                "version": STORE_VERSION,
                "task_id": task_id,
                "seq": seq,
                "final": bool(final),
                "model": model,
                "layer": int(layer),
                "tokens": int(tokens),
                "dim": int(dim),
                "dtype": dtype,
                "payload": f"{seq}.bin",
                "byte_length": expected,
                "created_at_ms": int(time.time() * 1000),
            }
            _write_atomic(os.path.join(task_directory, f"{seq}.bin"), payload)
            _write_atomic(
                os.path.join(task_directory, f"{seq}.json"),
                json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            )
        return manifest
