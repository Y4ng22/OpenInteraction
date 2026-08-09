#!/usr/bin/env python3
"""End-to-end WAV/JPEG probe through the InteractFormer MiniCPM-o adapter."""

import argparse
import asyncio
import os
from pathlib import Path
import sys
import wave

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interactformer.backends.minicpmo_realtime import (  # noqa: E402
    MiniCPMORealtimeClient,
    MiniCPMORealtimeConfig,
    RealtimeMode,
)


async def run(args):
    mode = RealtimeMode.VIDEO if args.jpeg else RealtimeMode.AUDIO
    client = MiniCPMORealtimeClient(MiniCPMORealtimeConfig(
        base_url=args.url,
        mode=mode,
        system_prompt=args.system_prompt,
        verify_tls=not args.insecure,
        bearer_token=os.environ.get("MINICPMO_API_TOKEN"),
    ))
    samples, sample_rate = sf.read(args.input_wav, dtype="float32", always_2d=False)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    jpeg = Path(args.jpeg).read_bytes() if args.jpeg else None
    turn_samples = max(1, int(sample_rate * 0.2))
    output_audio = []
    output_text = []
    got_output = asyncio.Event()
    end_of_turn = asyncio.Event()

    await client.connect()
    print(f"connected session={client.session_id or '(server omitted id)'}", file=sys.stderr)

    async def receive():
        async for event in client.events():
            if event.kind == "text" and event.text:
                output_text.append(event.text)
                got_output.set()
                print(event.text, end="", flush=True)
            elif event.kind == "audio" and event.audio is not None:
                output_audio.append(event.audio)
                got_output.set()
            elif event.kind == "listen" and got_output.is_set():
                end_of_turn.set()
                return
            elif event.type == "response.done":
                if event.text and not output_text:
                    output_text.append(event.text)
                    print(event.text, end="", flush=True)
                end_of_turn.set()
                return

    receiver = asyncio.create_task(receive())
    try:
        for start in range(0, len(samples), turn_samples):
            await client.send_micro_turn(
                samples[start : start + turn_samples],
                sample_rate,
                jpeg_frame=jpeg if start == 0 else None,
            )
            await asyncio.sleep(0.2)
        silence = np.zeros(turn_samples, dtype=np.float32)
        for _ in range(int(args.tail_silence_seconds / 0.2)):
            if end_of_turn.is_set():
                break
            await client.send_micro_turn(silence, sample_rate)
            await asyncio.sleep(0.2)
        await client.flush()
        await asyncio.wait_for(end_of_turn.wait(), timeout=args.response_timeout)
    finally:
        await client.close()
        if not receiver.done():
            receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)

    print()
    if args.output_wav and output_audio:
        audio = np.concatenate(output_audio).clip(-1.0, 1.0)
        pcm16 = (audio * 32767.0).astype("<i2")
        with wave.open(args.output_wav, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24_000)
            handle.writeframes(pcm16.tobytes())
        print(f"saved 24kHz output: {args.output_wav}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8006")
    parser.add_argument("--input-wav", required=True)
    parser.add_argument("--jpeg", help="Optional JPEG enables omni/video mode")
    parser.add_argument("--output-wav", default="minicpmo_output.wav")
    parser.add_argument(
        "--system-prompt",
        default="你是一个自然、简洁、可靠的全双工多模态助手。",
    )
    parser.add_argument("--tail-silence-seconds", type=float, default=3.0)
    parser.add_argument("--response-timeout", type=float, default=60.0)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate validation (development only)",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"MiniCPM-o probe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
