"""HTTP facade between DeepSeek Harness and an OpenAI-compatible model server.

Routes (relative to the proxy base URL ``http://host:port/v1``):

    POST /v1/chat/completions   forwarded with markers stripped; streamed or not
    GET  /v1/models             forwarded unchanged

A response counts as a final answer when it finishes with ``stop`` and carries
no tool call. Only then is a latent extracted and written, before the last
byte reaches the Harness, so the Gateway finds the latent as soon as the Task
completes. Extraction or store failures are logged and never alter the
forwarded response.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from .extractors import StubExtractor
from .markers import find_task_id, strip_markers
from .store import FileLatentStore

FORWARDED_HEADERS = ("authorization", "content-type", "accept", "user-agent")


@dataclass
class ProxyConfig:
    upstream: str
    store: FileLatentStore
    extractor: Any = field(default_factory=StubExtractor)
    timeout_s: float = 600.0
    # Test-only: hold each final answer of a marked Task this long before its
    # last byte, so voice e2e tests have time to steer or cancel the Task.
    final_delay_s: float = 0.0

    def upstream_url(self, suffix: str) -> str:
        return self.upstream.rstrip("/") + suffix


@dataclass
class TurnRecorder:
    """Collects one assistant turn and writes its latent when it is final."""

    config: ProxyConfig
    task_id: Optional[str]
    messages: List[Any]
    answer: List[str] = field(default_factory=list)
    finish_reason: Optional[str] = None
    saw_tool_call: bool = False
    written: Optional[Dict[str, Any]] = None

    def observe_choice(self, choice: Dict[str, Any], key: str) -> None:
        part = choice.get(key) or {}
        content = part.get("content")
        if isinstance(content, str):
            self.answer.append(content)
        if part.get("tool_calls"):
            self.saw_tool_call = True
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]

    def is_final(self) -> bool:
        return bool(
            self.task_id
            and self.finish_reason == "stop"
            and not self.saw_tool_call
            and "".join(self.answer).strip()
        )

    def hold_final(self) -> None:
        if self.config.final_delay_s > 0 and self.is_final():
            time.sleep(self.config.final_delay_s)

    def finish(self) -> None:
        answer = "".join(self.answer).strip()
        if self.written is not None or not self.is_final():
            return
        try:
            latent = self.config.extractor.extract(self.messages, answer)
            self.written = self.config.store.write(
                self.task_id,
                latent.payload,
                tokens=latent.tokens,
                dim=latent.dim,
                dtype=latent.dtype,
                model=latent.model,
                layer=latent.layer,
                final=True,
            )
        except Exception as error:  # noqa: BLE001 - never break the response
            log(f"latent write failed for {self.task_id}: {error}")


def log(message: str) -> None:
    print(f"[latent-proxy] {message}", file=sys.stderr, flush=True)


def make_handler(config: ProxyConfig):
    class Handler(BaseHTTPRequestHandler):
        server_version = "latent-proxy/0.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log(format % args)

        def _forward_headers(self) -> Dict[str, str]:
            return {
                name: value
                for name, value in self.headers.items()
                if name.lower() in FORWARDED_HEADERS
            }

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _relay_error(self, error: urllib.error.HTTPError) -> None:
            body = error.read()
            self._send_bytes(
                error.code,
                body,
                error.headers.get("Content-Type", "application/json"),
            )

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/v1/models":
                self._send_bytes(404, b'{"error":"not found"}', "application/json")
                return
            request = urllib.request.Request(
                config.upstream_url("/models"),
                headers=self._forward_headers(),
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=config.timeout_s) as upstream:
                    self._send_bytes(
                        upstream.status,
                        upstream.read(),
                        upstream.headers.get("Content-Type", "application/json"),
                    )
            except urllib.error.HTTPError as error:
                self._relay_error(error)
            except (urllib.error.URLError, OSError) as error:
                self._send_bytes(502, json.dumps({"error": str(error)}).encode(), "application/json")

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._send_bytes(404, b'{"error":"not found"}', "application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_bytes(400, b'{"error":"invalid JSON"}', "application/json")
                return
            messages = body.get("messages") if isinstance(body.get("messages"), list) else []
            task_id = find_task_id(messages)
            body["messages"] = strip_markers(messages)
            recorder = TurnRecorder(config, task_id, body["messages"])
            request = urllib.request.Request(
                config.upstream_url("/chat/completions"),
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={**self._forward_headers(), "Content-Type": "application/json"},
                method="POST",
            )
            try:
                upstream = urllib.request.urlopen(request, timeout=config.timeout_s)
            except urllib.error.HTTPError as error:
                self._relay_error(error)
                return
            except (urllib.error.URLError, OSError) as error:
                self._send_bytes(502, json.dumps({"error": str(error)}).encode(), "application/json")
                return
            with upstream:
                if body.get("stream"):
                    self._relay_stream(upstream, recorder)
                else:
                    self._relay_json(upstream, recorder)

        def _relay_json(self, upstream: Any, recorder: TurnRecorder) -> None:
            raw = upstream.read()
            try:
                for choice in json.loads(raw).get("choices") or []:
                    recorder.observe_choice(choice, "message")
            except (json.JSONDecodeError, AttributeError):
                pass
            recorder.finish()
            recorder.hold_final()
            self._send_bytes(
                upstream.status,
                raw,
                upstream.headers.get("Content-Type", "application/json"),
            )

        def _relay_stream(self, upstream: Any, recorder: TurnRecorder) -> None:
            # HTTP/1.0 responses end at connection close, so no length is needed.
            self.send_response(upstream.status)
            self.send_header(
                "Content-Type",
                upstream.headers.get("Content-Type", "text/event-stream"),
            )
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                line = upstream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("data:"):
                    data = text[len("data:"):].strip()
                    if data == "[DONE]":
                        recorder.finish()
                        recorder.hold_final()
                    else:
                        try:
                            for choice in json.loads(data).get("choices") or []:
                                recorder.observe_choice(choice, "delta")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                self.wfile.write(line)
                self.wfile.flush()
            recorder.finish()

    return Handler


def create_server(config: ProxyConfig, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(config))
    server.daemon_threads = True
    return server
