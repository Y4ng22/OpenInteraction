"""Client adapter for the official MiniCPM-o 4.5 Realtime API.

The official service accepts 16 kHz mono float32 PCM, normally in one-second
TDM units, and emits 24 kHz float32 PCM plus text over a persistent WebSocket.
InteractFormer keeps its 200 ms scheduler: this adapter resamples incoming
micro-turns and accumulates five of them before sending ``input.append``.

Only the lightweight client protocol lives here. Model loading and remote
repository code stay inside the separately deployed official service.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
import json
import ssl
from typing import Any, AsyncIterator, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import numpy as np


class RealtimeMode(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"


@dataclass(frozen=True)
class MiniCPMORealtimeConfig:
    base_url: str = "http://127.0.0.1:8006"
    mode: RealtimeMode = RealtimeMode.VIDEO
    system_prompt: str = "你是一个自然、简洁、可靠的全双工多模态助手。"
    input_sample_rate: int = 16_000
    output_sample_rate: int = 24_000
    local_micro_turn_ms: int = 200
    upstream_chunk_ms: int = 1_000
    open_timeout_seconds: float = 30.0
    max_message_bytes: int = 16 * 1024 * 1024
    verify_tls: bool = True
    bearer_token: Optional[str] = None
    max_slice_nums: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", RealtimeMode(self.mode))
        if self.input_sample_rate <= 0 or self.output_sample_rate <= 0:
            raise ValueError("sample rates must be positive")
        if self.local_micro_turn_ms <= 0 or self.upstream_chunk_ms <= 0:
            raise ValueError("chunk durations must be positive")
        if self.upstream_chunk_ms % self.local_micro_turn_ms:
            raise ValueError("upstream_chunk_ms must be a multiple of local_micro_turn_ms")
        if self.max_message_bytes < 1024:
            raise ValueError("max_message_bytes is too small")
        if self.max_slice_nums < 1:
            raise ValueError("max_slice_nums must be at least 1")

    @property
    def websocket_url(self) -> str:
        return normalize_realtime_url(self.base_url, self.mode)


@dataclass(frozen=True)
class MiniCPMOEvent:
    type: str
    kind: Optional[str] = None
    text: Optional[str] = None
    audio: Optional[np.ndarray] = None
    session_id: Optional[str] = None
    metrics: Optional[Mapping[str, Any]] = None
    raw: Optional[Mapping[str, Any]] = None


def normalize_realtime_url(url: str, mode: RealtimeMode | str) -> str:
    """Convert an HTTP host or WebSocket endpoint to the official endpoint."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "ws", "wss"):
        raise ValueError("MiniCPM-o URL must use http, https, ws, or wss")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MiniCPM-o URL must contain a host and no embedded credentials")
    if parsed.fragment:
        raise ValueError("MiniCPM-o URL fragments are not supported")

    mode_value = RealtimeMode(mode).value
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        path = "/v1/realtime"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["mode"] = mode_value
    return urlunparse((scheme, parsed.netloc, path, "", urlencode(query), ""))


class MiniCPMOProtocol:
    """State-free protocol encoding plus a bounded 200ms-to-1s accumulator."""

    def __init__(self, config: MiniCPMORealtimeConfig):
        self.config = config
        self._audio_buffer = np.empty(0, dtype=np.float32)
        self._latest_video_frame: Optional[str] = None

    @property
    def buffered_samples(self) -> int:
        return int(self._audio_buffer.size)

    @property
    def upstream_samples(self) -> int:
        return int(
            self.config.input_sample_rate * self.config.upstream_chunk_ms / 1000
        )

    def session_init(self) -> dict[str, Any]:
        return {
            "type": "session.init",
            "payload": {"system_prompt": self.config.system_prompt},
        }

    @staticmethod
    def session_close(reason: str = "user_stop") -> dict[str, str]:
        return {"type": "session.close", "reason": reason[:128]}

    def set_video_frame(self, jpeg: bytes) -> None:
        if self.config.mode is not RealtimeMode.VIDEO:
            raise RuntimeError("video frames require mode=video")
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("video frame must be complete JPEG bytes")
        if len(jpeg) > self.config.max_message_bytes // 2:
            raise ValueError("JPEG frame exceeds configured message limit")
        self._latest_video_frame = base64.b64encode(jpeg).decode("ascii")

    def append_audio(
        self,
        samples: Sequence[float] | np.ndarray,
        sample_rate: int,
        *,
        force_listen: bool = False,
    ) -> list[dict[str, Any]]:
        """Append arbitrary mono samples and return complete 1-second events."""
        audio = _mono_float32(samples)
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if sample_rate != self.config.input_sample_rate:
            audio = _resample_linear(audio, sample_rate, self.config.input_sample_rate)
        if audio.size:
            self._audio_buffer = np.concatenate((self._audio_buffer, audio))

        messages = []
        while self._audio_buffer.size >= self.upstream_samples:
            chunk = self._audio_buffer[: self.upstream_samples]
            self._audio_buffer = self._audio_buffer[self.upstream_samples :]
            input_payload: dict[str, Any] = {
                "audio": _encode_float32(chunk),
                "force_listen": bool(force_listen),
            }
            if self.config.mode is RealtimeMode.VIDEO:
                if self._latest_video_frame is None:
                    raise RuntimeError("mode=video requires a JPEG frame before audio")
                input_payload.update({
                    "video_frames": [self._latest_video_frame],
                    "max_slice_nums": self.config.max_slice_nums,
                })
            messages.append({"type": "input.append", "input": input_payload})
        return messages

    def flush_silence(self, *, force_listen: bool = False) -> list[dict[str, Any]]:
        """Pad the pending partial unit with silence and emit it once."""
        if not self._audio_buffer.size:
            return []
        missing = self.upstream_samples - self._audio_buffer.size
        return self.append_audio(
            np.zeros(missing, dtype=np.float32),
            self.config.input_sample_rate,
            force_listen=force_listen,
        )

    def parse_event(self, message: str | bytes) -> MiniCPMOEvent:
        if isinstance(message, bytes):
            raw_bytes = message
            message = message.decode("utf-8")
        else:
            raw_bytes = message.encode("utf-8")
        if len(raw_bytes) > self.config.max_message_bytes:
            raise ValueError("MiniCPM-o message exceeds configured size limit")
        payload = json.loads(message)
        if not isinstance(payload, dict):
            raise ValueError("MiniCPM-o message must be a JSON object")
        event_type = payload.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("MiniCPM-o message is missing a string type")
        if event_type == "error":
            raise RuntimeError(json.dumps(payload, ensure_ascii=False))

        kind = payload.get("kind") if isinstance(payload.get("kind"), str) else None
        text = payload.get("text") if isinstance(payload.get("text"), str) else None
        session_id = (
            payload.get("session_id")
            if isinstance(payload.get("session_id"), str)
            else None
        )
        audio = None
        if kind == "audio" and payload.get("audio"):
            audio = _decode_float32(
                payload["audio"],
                max_bytes=min(self.config.max_message_bytes, 8 * 1024 * 1024),
            )
        metrics = payload.get("metrics")
        return MiniCPMOEvent(
            type=event_type,
            kind=kind,
            text=text,
            audio=audio,
            session_id=session_id,
            metrics=metrics if isinstance(metrics, dict) else None,
            raw=payload,
        )


class MiniCPMORealtimeClient:
    """Small async client for one persistent official gateway session."""

    def __init__(self, config: Optional[MiniCPMORealtimeConfig] = None):
        self.config = config or MiniCPMORealtimeConfig()
        self.protocol = MiniCPMOProtocol(self.config)
        self._websocket: Any = None
        self.session_id: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._websocket is not None

    async def connect(self) -> str:
        if self.connected:
            return self.session_id or ""
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "Install the MiniCPM-o client extra: pip install 'interactformer[minicpmo]'"
            ) from exc

        parsed = urlparse(self.config.websocket_url)
        ssl_context = None
        if parsed.scheme == "wss":
            ssl_context = ssl.create_default_context()
            if not self.config.verify_tls:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        headers = None
        if self.config.bearer_token:
            headers = {"Authorization": f"Bearer {self.config.bearer_token}"}
        self._websocket = await websockets.connect(
            self.config.websocket_url,
            ssl=ssl_context,
            open_timeout=self.config.open_timeout_seconds,
            max_size=self.config.max_message_bytes,
            additional_headers=headers,
        )
        try:
            while True:
                event = self.protocol.parse_event(await self._websocket.recv())
                if event.type in ("session.queue_done", "queue_done"):
                    await self._send(self.protocol.session_init())
                elif event.type == "session.created":
                    self.session_id = event.session_id or ""
                    return self.session_id
                elif event.type == "session.closed":
                    raise RuntimeError("MiniCPM-o session closed during initialization")
        except Exception:
            await self.close("initialization_failed")
            raise

    async def send_micro_turn(
        self,
        audio: Sequence[float] | np.ndarray,
        sample_rate: int,
        *,
        jpeg_frame: Optional[bytes] = None,
        force_listen: bool = False,
    ) -> int:
        if not self.connected:
            raise RuntimeError("connect() must complete before sending audio")
        if jpeg_frame is not None:
            self.protocol.set_video_frame(jpeg_frame)
        messages = self.protocol.append_audio(
            audio, sample_rate, force_listen=force_listen
        )
        for message in messages:
            await self._send(message)
        return len(messages)

    async def flush(self, *, force_listen: bool = False) -> int:
        messages = self.protocol.flush_silence(force_listen=force_listen)
        for message in messages:
            await self._send(message)
        return len(messages)

    async def events(self) -> AsyncIterator[MiniCPMOEvent]:
        if not self.connected:
            raise RuntimeError("connect() must complete before receiving events")
        while self.connected:
            event = self.protocol.parse_event(await self._websocket.recv())
            yield event
            if event.type == "session.closed":
                self._websocket = None
                return

    async def close(self, reason: str = "user_stop") -> None:
        websocket, self._websocket = self._websocket, None
        self.session_id = None
        if websocket is None:
            return
        try:
            await websocket.send(json.dumps(self.protocol.session_close(reason)))
        except Exception:
            pass
        finally:
            await websocket.close()

    async def _send(self, payload: Mapping[str, Any]) -> None:
        if not self.connected:
            raise RuntimeError("MiniCPM-o WebSocket is not connected")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > self.config.max_message_bytes:
            raise ValueError("outbound MiniCPM-o message exceeds configured limit")
        await self._websocket.send(encoded)


def _mono_float32(samples: Sequence[float] | np.ndarray) -> np.ndarray:
    if hasattr(samples, "detach") and hasattr(samples, "cpu"):
        samples = samples.detach().cpu().numpy()
    audio = np.asarray(samples)
    if audio.ndim == 2:
        if 1 not in audio.shape:
            raise ValueError("audio must be mono")
        audio = audio.reshape(-1)
    if audio.ndim != 1:
        raise ValueError("audio must be a one-dimensional mono sequence")
    if np.issubdtype(audio.dtype, np.integer):
        info = np.iinfo(audio.dtype)
        scale = float(max(abs(info.min), info.max))
        audio = audio.astype(np.float32) / scale
    else:
        audio = audio.astype(np.float32, copy=False)
    return np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0).clip(-1.0, 1.0)


def _resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if not audio.size or source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    target_length = int(round(audio.size * target_rate / source_rate))
    if target_length <= 0:
        return np.empty(0, dtype=np.float32)
    source_positions = np.arange(audio.size, dtype=np.float64)
    target_positions = np.arange(target_length, dtype=np.float64) * source_rate / target_rate
    target_positions = np.minimum(target_positions, max(0, audio.size - 1))
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def _encode_float32(audio: np.ndarray) -> str:
    return base64.b64encode(audio.astype("<f4", copy=False).tobytes()).decode("ascii")


def _decode_float32(value: Any, max_bytes: int) -> np.ndarray:
    if not isinstance(value, str):
        raise ValueError("audio output must be base64 text")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("audio output is not valid base64") from exc
    if len(raw) > max_bytes:
        raise ValueError("decoded audio output exceeds configured size limit")
    if len(raw) % 4:
        raise ValueError("decoded audio output is not float32-aligned")
    return np.frombuffer(raw, dtype="<f4").copy()
