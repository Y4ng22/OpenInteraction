import io
import json

import pytest

from interactformer.background.reasoner import (
    AnthropicCompatibleBackend,
    OpenAICompatibleBackend,
)


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_ark_backend_streams_and_keeps_key_out_of_payload(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "secret-test-key")
    captured = {}

    def opener(http_request, timeout):
        captured["headers"] = dict(http_request.header_items())
        captured["payload"] = json.loads(http_request.data)
        captured["timeout"] = timeout
        body = (
            'data: {"choices":[{"delta":{"content":"连接"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"正常"}}]}\n\n'
            'data: [DONE]\n\n'
        ).encode()
        return _FakeResponse(body)

    backend = OpenAICompatibleBackend(
        model="doubao-test",
        opener=opener,
        stream_chunk_chars=2,
    )
    steps = list(backend.generate_stream("probe", {"session": "test"}))

    assert steps[-1].is_final
    assert steps[-1].thought == "连接正常"
    assert captured["payload"]["model"] == "doubao-test"
    assert captured["payload"]["stream"] is True
    assert "secret-test-key" not in json.dumps(captured["payload"])


def test_ark_backend_requires_environment_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    backend = OpenAICompatibleBackend(model="doubao-test", opener=lambda *_: None)

    with pytest.raises(RuntimeError, match="ARK_API_KEY"):
        list(backend.generate_stream("probe", {}))


def test_ark_backend_rejects_plain_http():
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleBackend(model="doubao-test", base_url="http://example.com")


def test_anthropic_backend_streams_messages_protocol(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-anthropic-key")
    captured = {}

    def opener(http_request, timeout):
        captured["url"] = http_request.full_url
        captured["headers"] = dict(http_request.header_items())
        captured["payload"] = json.loads(http_request.data)
        body = (
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"S2 "}}\n\n'
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"正常"}}\n\n'
            'event: message_stop\n'
            'data: {"type":"message_stop"}\n\n'
        ).encode()
        return _FakeResponse(body)

    backend = AnthropicCompatibleBackend(
        model="doubao-seed-evolving",
        opener=opener,
        stream_chunk_chars=2,
    )
    steps = list(backend.generate_stream("probe", {"session": "test"}))

    assert captured["url"].endswith("/api/compatible/v1/messages")
    assert captured["payload"]["model"] == "doubao-seed-evolving"
    assert captured["payload"]["stream"] is True
    assert "secret-anthropic-key" not in json.dumps(captured["payload"])
    assert steps[-1].thought == "S2 正常"
    assert steps[-1].is_final
