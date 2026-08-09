#!/usr/bin/env python
"""
InteractFormer Interactive Demo Client
========================================
Connects to the vLLM server (running in Docker or locally) and provides
an interactive chat interface that demonstrates the Interaction Model
concepts: continuous streaming, micro-turn processing, and real-time
audio interaction.

Usage:
    # With Docker vLLM server running:
    python scripts/interactive_client.py

    # Or with custom endpoint:
    python scripts/interactive_client.py --api http://localhost:8080/v1
"""

import argparse
import base64
import io
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class InteractFormerClient:
    """Client that demonstrates InteractFormer's interaction paradigm.

    Maps to our architecture:
    - This client = Interaction Model (S1) frontend
    - vLLM server = Thinker + Talker
    - Background Model (S2) = simulated with async requests
    """

    def __init__(self, api_base: str = "http://localhost:8080/v1"):
        self.api_base = api_base
        self.model_name = "interactformer"
        self.conversation_history = []
        self.micro_turn_count = 0

    def check_health(self) -> bool:
        """Check if the vLLM server is ready."""
        import urllib.request

        health_url = f"{self.api_base}/../health"
        try:
            with urllib.request.urlopen(health_url, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def chat(self, message: str, stream: bool = True) -> str:
        """Send a chat message and get the response.

        This simulates one "macro-turn" in InteractFormer's interaction
        paradigm. In production, this would be 200ms micro-turns with
        continuous audio streaming.
        """
        self.micro_turn_count += 1
        self.conversation_history.append({
            "role": "user",
            "content": message,
        })

        import urllib.request

        payload = {
            "model": self.model_name,
            "messages": self.conversation_history[-20:],  # Last 20 turns
            "stream": stream,
            "temperature": 0.7,
            "max_tokens": 1024,
            # Simulate our Bridge-aware generation
            "extra_body": {
                "interactformer_bridge": {
                    "micro_turn_id": self.micro_turn_count,
                    "background_context": None,  # Would be filled by S2
                }
            },
        }

        req = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer dummy",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if stream:
                    return self._handle_stream(resp)
                else:
                    data = json.loads(resp.read())
                    return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            print(f"\n[Server Error] {e.code}: {error_body[:500]}")
            return None
        except urllib.error.URLError as e:
            print(f"\n[Connection Error] {e.reason}")
            print("Is the vLLM server running?")
            return None

    def _handle_stream(self, response) -> str:
        """Handle SSE streaming response.

        Each chunk simulates one micro-turn (200ms) of InteractFormer's
        streaming output. The full response is a sequence of micro-turns.
        """
        full_response = []
        print("\n🤖 Assistant: ", end="", flush=True)

        for line in response:
            line = line.decode().strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if "choices" in data and data["choices"]:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            print(content, end="", flush=True)
                            full_response.append(content)
                except json.JSONDecodeError:
                    continue

        print()
        response_text = "".join(full_response)

        if response_text:
            self.conversation_history.append({
                "role": "assistant",
                "content": response_text,
            })

        return response_text

    def chat_with_audio(
        self,
        audio_path: Optional[str] = None,
        text: Optional[str] = None,
    ) -> str:
        """Chat with audio input support.

        This demonstrates InteractFormer's multimodal interaction:
        audio + text input → streaming speech/text output.

        Args:
            audio_path: Path to a WAV file (24kHz, mono).
            text: Optional text to accompany audio.

        Returns:
            Model response text.
        """
        messages = self.conversation_history[-20:].copy()

        content_parts = []
        if text:
            content_parts.append({"type": "text", "text": text})

        if audio_path and os.path.exists(audio_path):
            # Read audio and encode as base64
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            audio_b64 = base64.b64encode(audio_bytes).decode()

            content_parts.append({
                "type": "audio_url",
                "audio_url": {
                    "url": f"data:audio/wav;base64,{audio_b64}",
                },
            })

        messages.append({
            "role": "user",
            "content": content_parts if len(content_parts) > 1 else content_parts[0],
        })

        self.conversation_history.append(messages[-1])

        import urllib.request

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 1024,
        }

        req = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer dummy",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return self._handle_stream(resp)
        except Exception as e:
            print(f"\n[Error] {e}")
            return None

    def record_audio(
        self,
        duration_seconds: int = 3,
        sample_rate: int = 24000,
    ) -> str:
        """Record audio from microphone.

        Uses PyAudio if available, otherwise generates synthetic audio.
        Returns path to saved WAV file.
        """
        try:
            import pyaudio
        except ImportError:
            print("  PyAudio not installed. Generating synthetic audio...")
            return self._generate_synthetic_audio(duration_seconds, sample_rate)

        p = pyaudio.PyAudio()
        chunk_size = int(sample_rate * 0.2)  # 200ms chunks (micro-turns!)
        channels = 1

        print(f"\n🎤 Recording {duration_seconds}s of audio...")
        print(f"   (InteractFormer processes this as {duration_seconds * 5} micro-turns)")
        print("   Speak now!")

        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk_size,
        )

        frames = []
        for i in range(int(sample_rate / chunk_size * duration_seconds)):
            data = stream.read(chunk_size)
            frames.append(data)
            # Show micro-turn progress
            if i % 5 == 0:
                print(f"   █{'█' * (i // 5)}{'░' * (duration_seconds - i // 5)} "
                      f"turn {i}/{duration_seconds * 5}", end="\r")

        print()
        stream.stop_stream()
        stream.close()
        p.terminate()

        # Save to WAV
        output_path = "/tmp/interactformer_recording.wav"
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))

        print(f"   ✓ Recorded {len(frames)} micro-turns to {output_path}")
        return output_path

    def _generate_synthetic_audio(
        self, duration: int, sample_rate: int
    ) -> str:
        """Generate synthetic audio for testing."""
        num_samples = int(sample_rate * duration)
        t = np.linspace(0, duration, num_samples, endpoint=False)
        samples = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        samples = (samples * 32767).astype(np.int16)

        output_path = "/tmp/interactformer_synthetic.wav"
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(samples.tobytes())

        print(f"   Generated synthetic audio: {output_path}")
        return output_path

    def interactive_loop(self):
        """Main interactive loop.

        Demonstrates InteractFormer's interaction paradigm:
        - Continuous streaming (no turn boundaries)
        - Real-time text + audio interaction
        - Background delegation simulation
        """
        print("=" * 60)
        print("  InteractFormer — Interactive Demo")
        print("  vLLM Backend: Qwen3-Omni 30B W4A16")
        print("=" * 60)
        print()
        print("Commands:")
        print("  /text <message>    — Text chat")
        print("  /speak             — Record 3s audio + chat")
        print("  /file <audio.wav>  — Send audio file")
        print("  /delegate <query>  — Simulate S2 delegation")
        print("  /history           — Show conversation")
        print("  /stats             — Show interaction stats")
        print("  /quit              — Exit")
        print()

        while True:
            try:
                user_input = input("👤 You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not user_input:
                continue

            if user_input == "/quit":
                break

            elif user_input == "/history":
                print("\n📜 Conversation History:")
                for i, msg in enumerate(self.conversation_history):
                    role = "👤" if msg["role"] == "user" else "🤖"
                    content = str(msg.get("content", ""))[:200]
                    print(f"  {i}: {role} {content}")
                print()

            elif user_input == "/stats":
                print(f"\n📊 Interaction Stats:")
                print(f"  Micro-turns processed: {self.micro_turn_count}")
                print(f"  Conversation turns: {len(self.conversation_history)}")
                print(f"  Estimated session duration: "
                      f"{self.micro_turn_count * 0.2:.1f}s")
                print(f"  API endpoint: {self.api_base}")
                print(f"  Server healthy: {self.check_health()}")
                print()

            elif user_input.startswith("/text "):
                message = user_input[6:]
                print(f"\n🧠 Processing through InteractFormer pipeline...")
                print(f"   Step 1: Audio/text encoding (simulated)")
                print(f"   Step 2: Temporal Grid cell #{self.micro_turn_count}")
                print(f"   Step 3: Thinker processing...")
                self.chat(message)
                print(f"   Step 4: Talker output ↑")

            elif user_input == "/speak":
                audio_path = self.record_audio(duration_seconds=3)
                text = input("👤 Optional text prompt: ").strip() or None
                self.chat_with_audio(audio_path, text)

            elif user_input.startswith("/file "):
                audio_path = user_input[6:]
                if os.path.exists(audio_path):
                    text = input("👤 Optional text prompt: ").strip() or None
                    self.chat_with_audio(audio_path, text)
                else:
                    print(f"  File not found: {audio_path}")

            elif user_input.startswith("/delegate "):
                query = user_input[10:]
                print(f"\n📤 Delegating to Background Model (S2)...")
                print(f"   1. ContextPackager: building rich context...")
                print(f"   2. BackgroundModel: processing {len(self.conversation_history)} turns of history")
                print(f"   3. Multi-Ensemble: Reasoner + Retriever running...")
                # In production, this would actually call S2 via the Bridge
                print(f"   4. Bridge injects results into S1 via cross-attention")
                print(f"\n   (S2 delegation is simulated. Sending to S1 instead...)")
                self.chat(f"[Background context] Please answer: {query}")

            else:
                self.chat(user_input)


def main():
    parser = argparse.ArgumentParser(
        description="InteractFormer Interactive Demo Client"
    )
    parser.add_argument(
        "--api", default="http://localhost:8080/v1",
        help="vLLM API endpoint",
    )
    parser.add_argument(
        "--no-interactive", action="store_true",
        help="Run a single test query instead of interactive loop",
    )
    parser.add_argument(
        "--query", default="Hello! Introduce yourself and explain what interaction models are.",
        help="Test query for non-interactive mode",
    )
    args = parser.parse_args()

    client = InteractFormerClient(api_base=args.api)

    # Check server
    print("Checking vLLM server...")
    if not client.check_health():
        print()
        print("⚠ vLLM server is NOT ready at", args.api)
        print()
        print("Start the server first:")
        print("  bash scripts/run_demo_docker.sh")
        print()
        print("Or if running vLLM manually:")
        print(f"  vllm serve 88plug/Qwen3-Omni-30B-W4A16 \\")
        print(f"    --kv-cache-dtype fp8 \\")
        print(f"    --max-model-len 32768 \\")
        print(f"    --gpu-memory-utilization 0.92 \\")
        print(f"    --port 8080")
        sys.exit(1)

    print("✓ Server is ready!")
    print()

    if args.no_interactive:
        print(f"Test query: {args.query}")
        client.chat(args.query)
    else:
        client.interactive_loop()


if __name__ == "__main__":
    main()
