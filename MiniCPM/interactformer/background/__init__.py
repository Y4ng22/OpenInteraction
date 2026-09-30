"""
Background Model (S2) — Asynchronous deep reasoning and tool use.

The Background Model is the "deep thinking" backend of InteractFormer.
It runs asynchronously, handling complex reasoning, knowledge retrieval,
and tool execution without blocking the real-time Interaction Model.

Architecture:
    Delegation from S1 → Background Orchestrator
                            ├── Reasoner (deep chain-of-thought)
                            ├── Retriever (RAG, knowledge search)
                            └── Tool Executor (API calls, code execution)
                            ↓
    Results → Streaming Context Bridge → S1

Key innovation vs. DuplexOmni S2:
- Multi-Background Ensemble: parallel heterogeneous models instead of
  a single pluggable endpoint
- Progressive streaming of results (not batch return)
- Confidence-weighted fusion of multiple reasoning paths
"""

from interactformer.background.reasoner import (
    AnthropicCompatibleBackend,
    OpenAICompatibleBackend,
    Reasoner,
    reasoning_backend_from_env,
)
from interactformer.background.retriever import Retriever
from interactformer.background.tool_executor import ToolExecutor
from interactformer.background.background_model import BackgroundModel

__all__ = [
    "Reasoner",
    "AnthropicCompatibleBackend",
    "OpenAICompatibleBackend",
    "reasoning_backend_from_env",
    "Retriever",
    "ToolExecutor",
    "BackgroundModel",
]
