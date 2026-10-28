"""Latent extractors.

P0 ships only StubExtractor, which needs no model weights: it derives a
deterministic pseudo-latent from the answer text so the whole Gateway path can
be exercised. P1 adds an extractor that runs a prefill of the conversation and
answer through the local Qwen3 weights and resamples the hidden states.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Any, List


@dataclass(frozen=True)
class Latent:
    payload: bytes
    tokens: int
    dim: int
    dtype: str
    model: str
    layer: int


class StubExtractor:
    """Deterministic float32 [tokens, dim] values in [-1, 1) from the answer."""

    model = "stub"

    def __init__(self, tokens: int = 16, dim: int = 64) -> None:
        if tokens <= 0 or dim <= 0:
            raise ValueError("tokens and dim must be positive")
        self.tokens = tokens
        self.dim = dim

    def extract(self, messages: List[Any], answer: str) -> Latent:
        count = self.tokens * self.dim
        seed = hashlib.sha256(answer.encode("utf-8")).digest()
        values = []
        block = 0
        while len(values) < count:
            digest = hashlib.sha256(seed + block.to_bytes(4, "little")).digest()
            for offset in range(0, len(digest), 4):
                word = int.from_bytes(digest[offset:offset + 4], "little")
                values.append(word / 2**31 - 1.0)
            block += 1
        payload = struct.pack(f"<{count}f", *values[:count])
        return Latent(payload, self.tokens, self.dim, "float32", self.model, -1)
