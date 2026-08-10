"""Offline tests for the MiniCPM-o 4.5 Realtime adapter."""

import base64
import json

import numpy as np
import pytest

from interactformer.backends.minicpmo_realtime import (
    MiniCPMOProtocol,
    MiniCPMORealtimeConfig,
    RealtimeMode,
    normalize_realtime_url,
)


def test_normalizes_gateway_url_without_losing_existing_query():
    assert normalize_realtime_url(
        "https://example.test:8006?region=cn", RealtimeMode.AUDIO
    ) == "wss://example.test:8006/v1/realtime?region=cn&mode=audio"
    assert normalize_realtime_url(
        "ws://127.0.0.1:8006/v1/realtime?mode=video", "audio"
    ) == "ws://127.0.0.1:8006/v1/realtime?mode=audio"


def test_url_rejects_embedded_credentials_and_unknown_schemes():
    with pytest.raises(ValueError, match="embedded credentials"):
        normalize_realtime_url("https://user:password@example.test", "audio")
    with pytest.raises(ValueError, match="must use"):
        normalize_realtime_url("file:///tmp/socket", "audio")


def test_five_200ms_24khz_turns_become_one_official_16khz_unit():
    config = MiniCPMORealtimeConfig(mode="audio")
    protocol = MiniCPMOProtocol(config)
    turn = np.linspace(-0.5, 0.5, 4_800, dtype=np.float32)

    for _ in range(4):
        assert protocol.append_audio(turn, sample_rate=24_000) == []
    messages = protocol.append_audio(turn, sample_rate=24_000)

    assert len(messages) == 1
    assert messages[0]["type"] == "input.append"
    decoded = base64.b64decode(messages[0]["input"]["audio"])
    assert len(decoded) == 16_000 * 4
    assert protocol.buffered_samples == 0


def test_video_mode_requires_and_sends_a_bounded_jpeg():
    protocol = MiniCPMOProtocol(MiniCPMORealtimeConfig(mode="video"))
    audio = np.zeros(16_000, dtype=np.float32)
    with pytest.raises(RuntimeError, match="requires a JPEG"):
        protocol.append_audio(audio, 16_000)

    protocol.set_video_frame(b"\xff\xd8frame\xff\xd9")
    message = protocol.append_audio(audio, 16_000)[0]
    assert message["input"]["video_frames"] == [
        base64.b64encode(b"\xff\xd8frame\xff\xd9").decode("ascii")
    ]
    assert message["input"]["max_slice_nums"] == 1


def test_flush_pads_only_the_pending_partial_unit():
    protocol = MiniCPMOProtocol(MiniCPMORealtimeConfig(mode="audio"))
    protocol.append_audio(np.ones(3_200, dtype=np.float32), 16_000)
    messages = protocol.flush_silence()
    assert len(messages) == 1
    assert protocol.flush_silence() == []


def test_parses_text_and_24khz_float_audio_events():
    protocol = MiniCPMOProtocol(MiniCPMORealtimeConfig(mode="audio"))
    text = protocol.parse_event(json.dumps({
        "type": "response.output.delta", "kind": "text", "text": "你好"
    }))
    assert text.text == "你好"

    samples = np.array([0.25, -0.25], dtype="<f4")
    audio = protocol.parse_event(json.dumps({
        "type": "response.output.delta",
        "kind": "audio",
        "audio": base64.b64encode(samples.tobytes()).decode("ascii"),
    }))
    np.testing.assert_array_equal(audio.audio, samples)


def test_protocol_rejects_invalid_output_and_server_errors():
    protocol = MiniCPMOProtocol(MiniCPMORealtimeConfig(mode="audio"))
    with pytest.raises(ValueError, match="float32-aligned"):
        protocol.parse_event(json.dumps({
            "type": "response.output.delta",
            "kind": "audio",
            "audio": base64.b64encode(b"123").decode("ascii"),
        }))
    with pytest.raises(RuntimeError, match="out of memory"):
        protocol.parse_event(json.dumps({
            "type": "error", "message": "out of memory"
        }))
