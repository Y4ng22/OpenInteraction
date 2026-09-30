"""
Reasoner: Deep chain-of-thought reasoning for the Background Model.

The Reasoner performs multi-step reasoning on complex queries delegated
by the Interaction Model. It runs asynchronously and streams intermediate
results back to S1 via the Streaming Context Bridge.

Unlike a standard LLM call, the Reasoner:
1. Receives a rich context package (not a standalone query)
2. Performs multi-step chain-of-thought
3. Streams intermediate conclusions (not just final answer)
4. Can be interrupted if S1 gets a more urgent user input
"""

from typing import Optional, Generator, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod
import json
import os
from urllib import error, request


class ReasoningDepth(Enum):
    """Depth of reasoning chain."""
    SHALLOW = "shallow"    # Quick fact lookup / simple reasoning
    DEEP = "deep"          # Multi-step chain-of-thought
    ADAPTIVE = "adaptive"  # Model decides depth based on complexity


@dataclass
class ReasoningStep:
    """A single step in the reasoning chain.

    Each step is streamed back to S1 as it's generated, allowing the
    Interaction Model to interleave partial results into the conversation
    (e.g., "Let me think about that..." or showing intermediate steps).
    """
    step_id: int
    thought: str
    confidence: float
    is_final: bool = False
    references: list[str] = None

    def __post_init__(self):
        if self.references is None:
            self.references = []


class ReasoningBackend(ABC):
    """Abstract interface for pluggable S2 reasoning backends.

    Implementations can range from lightweight deterministic algorithms
    to full LLM-based chain-of-thought engines.

    All backends must support streaming partial outputs so S1 can
    interleave intermediate results into the interaction.
    """

    @abstractmethod
    def generate_stream(
        self, query: str, context: Dict[str, Any]
    ) -> Generator[ReasoningStep, None, None]:
        """Generate reasoning steps as a stream.

        Args:
            query: The reasoning query.
            context: Rich context from S1 (conversation, temporal state, etc.).

        Yields:
            ReasoningStep objects. The last step should have is_final=True.
        """
        ...


class DeterministicBackend(ReasoningBackend):
    """Lightweight deterministic backend for development and testing.

    Produces structured reasoning steps without requiring an LLM.
    Useful for validating the S1→S2→S1 pipeline during development.
    """

    def generate_stream(
        self, query: str, context: Dict[str, Any]
    ) -> Generator[ReasoningStep, None, None]:
        silence_s = context.get("silence_duration_ms", 0) / 1000
        num_cells = context.get("num_context_cells", 1)

        steps = [
            ReasoningStep(
                step_id=0,
                thought=f"Analyzing query in context of {num_cells} interaction cells...",
                confidence=0.9,
            ),
            ReasoningStep(
                step_id=1,
                thought=f"User has been silent for {silence_s:.1f}s. "
                        f"Considering temporal context for turn-taking implications.",
                confidence=0.8,
            ),
            ReasoningStep(
                step_id=2,
                thought=f"Conclusion: The query appears to be about '{query[:80]}...'. "
                        f"Further analysis would require a trained LLM backend.",
                confidence=0.7,
                is_final=True,
            ),
        ]
        yield from steps


class OpenAICompatibleBackend(ReasoningBackend):
    """Streaming S2 backend for OpenAI-compatible chat APIs.

    The default settings target Volcengine Ark (Doubao). Credentials are read
    lazily from an environment variable so importing or constructing the model
    never requires, logs, or serializes the API key.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        api_key_env: str = "ARK_API_KEY",
        timeout_seconds: float = 60.0,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        stream_chunk_chars: int = 160,
        max_context_chars: int = 24000,
        opener: Optional[Callable[..., Any]] = None,
    ):
        if not model:
            raise ValueError("S2 model ID must not be empty")
        if not base_url.startswith("https://"):
            raise ValueError("S2 base URL must use HTTPS")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stream_chunk_chars = max(1, stream_chunk_chars)
        self.max_context_chars = max(1000, max_context_chars)
        self._opener = opener or request.urlopen

    def generate_stream(
        self, query: str, context: Dict[str, Any]
    ) -> Generator[ReasoningStep, None, None]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"S2 API key is missing; set the {self.api_key_env} environment variable"
            )

        context_json = json.dumps(
            context,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        if len(context_json) > self.max_context_chars:
            context_json = context_json[-self.max_context_chars:]

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the asynchronous S2 reasoner inside the "
                        "OpenInteraction realtime multimodal system. Produce "
                        "supporting knowledge for the OpenInteraction assistant "
                        "and preserve that identity in any user-facing text. Use "
                        "the supplied interaction context to produce a concise, "
                        "actionable answer. Do not expose hidden chain-of-thought; "
                        "provide conclusions and brief supporting facts only."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Task:\n{query}\n\nInteraction context:\n{context_json}",
                },
            ],
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        http_request = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        step_id = 0
        complete_text = ""
        pending_text = ""
        try:
            with self._opener(http_request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    content = self._extract_content(choices[0].get("delta") or {})
                    if not content:
                        continue
                    complete_text += content
                    pending_text += content
                    if len(pending_text) >= self.stream_chunk_chars:
                        yield ReasoningStep(
                            step_id=step_id,
                            thought=pending_text,
                            confidence=0.85,
                        )
                        step_id += 1
                        pending_text = ""
        except error.HTTPError as exc:
            body = exc.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"S2 provider returned HTTP {exc.code}: {body}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(f"S2 provider connection failed: {exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("S2 provider returned an invalid streaming response") from exc

        if not complete_text.strip():
            raise RuntimeError("S2 provider returned an empty response")

        yield ReasoningStep(
            step_id=step_id,
            thought=complete_text.strip(),
            confidence=0.9,
            is_final=True,
        )

    @staticmethod
    def _extract_content(delta: Dict[str, Any]) -> str:
        content = delta.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""


class AnthropicCompatibleBackend(ReasoningBackend):
    """Streaming S2 backend for Anthropic Messages-compatible APIs."""

    def __init__(
        self,
        model: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/compatible",
        api_key_env: str = "ANTHROPIC_AUTH_TOKEN",
        timeout_seconds: float = 60.0,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        stream_chunk_chars: int = 160,
        max_context_chars: int = 24000,
        opener: Optional[Callable[..., Any]] = None,
    ):
        if not model:
            raise ValueError("S2 model ID must not be empty")
        if not base_url.startswith("https://"):
            raise ValueError("S2 base URL must use HTTPS")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stream_chunk_chars = max(1, stream_chunk_chars)
        self.max_context_chars = max(1000, max_context_chars)
        self._opener = opener or request.urlopen

    def generate_stream(
        self, query: str, context: Dict[str, Any]
    ) -> Generator[ReasoningStep, None, None]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            # Preserve compatibility with installations configured before the
            # Anthropic endpoint was selected, without logging either secret.
            api_key = os.environ.get("ARK_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"S2 API key is missing; set the {self.api_key_env} environment variable"
            )

        context_json = json.dumps(
            context,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        if len(context_json) > self.max_context_chars:
            context_json = context_json[-self.max_context_chars:]

        system_prompt = (
            "You are the asynchronous S2 reasoner inside the OpenInteraction "
            "realtime multimodal system. Produce supporting knowledge for the "
            "OpenInteraction assistant and preserve that identity in any "
            "user-facing text. Return concise conclusions and brief supporting "
            "facts only; never expose hidden chain-of-thought."
        )
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": f"Task:\n{query}\n\nInteraction context:\n{context_json}",
                }
            ],
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        # Claude-compatible clients normally append /v1/messages. A small
        # number of gateways expose /messages directly, so retry only on 404.
        endpoints = [f"{self.base_url}/v1/messages", f"{self.base_url}/messages"]
        response = None
        last_error = None
        for endpoint in endpoints:
            http_request = request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )
            try:
                response = self._opener(http_request, timeout=self.timeout_seconds)
                break
            except error.HTTPError as exc:
                last_error = exc
                if exc.code == 404 and endpoint != endpoints[-1]:
                    continue
                body = exc.read(1000).decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"S2 provider returned HTTP {exc.code}: {body}"
                ) from exc
            except error.URLError as exc:
                raise RuntimeError(f"S2 provider connection failed: {exc.reason}") from exc

        if response is None:
            raise RuntimeError(f"S2 provider endpoint was not found: {last_error}")

        step_id = 0
        complete_text = ""
        pending_text = ""
        try:
            with response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    content = self._extract_event_text(event)
                    if not content:
                        continue
                    complete_text += content
                    pending_text += content
                    if len(pending_text) >= self.stream_chunk_chars:
                        yield ReasoningStep(
                            step_id=step_id,
                            thought=pending_text,
                            confidence=0.85,
                        )
                        step_id += 1
                        pending_text = ""
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("S2 provider returned an invalid streaming response") from exc

        if not complete_text.strip():
            raise RuntimeError("S2 provider returned an empty response")
        yield ReasoningStep(
            step_id=step_id,
            thought=complete_text.strip(),
            confidence=0.9,
            is_final=True,
        )

    @staticmethod
    def _extract_event_text(event: Dict[str, Any]) -> str:
        delta = event.get("delta") or {}
        text = delta.get("text")
        if isinstance(text, str):
            return text
        # Some compatibility gateways emit OpenAI-style delta events.
        choices = event.get("choices") or []
        if choices:
            return OpenAICompatibleBackend._extract_content(
                choices[0].get("delta") or {}
            )
        return ""


def reasoning_backend_from_env(
    fallback_model: str = "doubao-seed-evolving",
) -> ReasoningBackend:
    """Build the configured S2 backend without embedding credentials in code."""
    provider = os.environ.get("S2_LLM_PROVIDER", "deterministic").strip().lower()
    if provider in {"", "deterministic", "disabled", "none"}:
        return DeterministicBackend()
    if provider in {"volcengine_ark", "ark", "doubao", "openai_compatible"}:
        return OpenAICompatibleBackend(
            model=os.environ.get("ARK_MODEL_ID", fallback_model),
            base_url=os.environ.get(
                "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
            ),
            api_key_env=os.environ.get("S2_API_KEY_ENV", "ARK_API_KEY"),
            timeout_seconds=float(os.environ.get("S2_TIMEOUT_SECONDS", "60")),
            max_tokens=int(os.environ.get("S2_MAX_TOKENS", "2048")),
        )
    if provider in {"anthropic", "anthropic_compatible", "doubao_anthropic"}:
        return AnthropicCompatibleBackend(
            model=os.environ.get("ANTHROPIC_MODEL", fallback_model),
            base_url=os.environ.get(
                "ANTHROPIC_BASE_URL",
                "https://ark.cn-beijing.volces.com/api/compatible",
            ),
            api_key_env="ANTHROPIC_AUTH_TOKEN",
            timeout_seconds=float(os.environ.get("S2_TIMEOUT_SECONDS", "60")),
            max_tokens=int(os.environ.get("S2_MAX_TOKENS", "2048")),
        )
    raise ValueError(f"Unsupported S2_LLM_PROVIDER: {provider}")


class Reasoner:
    """Deep reasoning engine for the Background Model.

    Performs chain-of-thought reasoning on delegated queries. Results
    are streamed incrementally so S1 can provide real-time feedback.

    Uses a pluggable ReasoningBackend for actual generation.
    Defaults to DeterministicBackend for development.
    """

    def __init__(
        self,
        backend: Optional[ReasoningBackend] = None,
        max_steps: int = 10,
    ):
        self.backend = backend or DeterministicBackend()
        self.max_steps = max_steps

    def reason(
        self,
        query: str,
        context: Dict[str, Any],
        depth: ReasoningDepth = ReasoningDepth.ADAPTIVE,
    ) -> Generator[ReasoningStep, None, None]:
        """Perform reasoning on a delegated query.

        Delegates to the pluggable backend for step generation.

        Yields:
            ReasoningStep objects as reasoning progresses.
        """
        # Normalize context keys: ContextPackager nests values inside
        # 'temporal_state', but Reasoner expects them at top level
        normalized = self._normalize_context(context)

        step_count = 0
        for step in self.backend.generate_stream(query, normalized):
            yield step
            step_count += 1
            if step_count >= self.max_steps:
                break

    def _normalize_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize context key structure between ContextPackager and Reasoner.

        ContextPackager produces: {temporal_state: {silence_duration_ms: ...}, ...}
        Reasoner expects:       {silence_duration_ms: ..., ...}

        Handle both formats.
        """
        result = dict(context)
        # Flatten temporal_state if present
        temporal = context.get("temporal_state", {})
        if isinstance(temporal, dict):
            for k, v in temporal.items():
                if k not in result:
                    result[k] = v
        # Flatten interaction_state if present
        inter = context.get("interaction_state", {})
        if isinstance(inter, dict):
            for k, v in inter.items():
                if k not in result:
                    result[k] = v
        # Build conversation_summary from conversation list if present
        conversation = context.get("conversation", [])
        if conversation and "conversation_summary" not in result:
            turns = []
            for turn in conversation[-5:]:  # Last 5 turns
                speaker = turn.get("speaker", "unknown")
                content = turn.get("content", [])
                if isinstance(content, list):
                    content = " ".join(str(c) for c in content[:50])
                turns.append(f"{speaker}: {content}")
            result["conversation_summary"] = " | ".join(turns)
        return result
