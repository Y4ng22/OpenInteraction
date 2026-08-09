#!/usr/bin/env python
"""
Run InteractFormer in interactive mode.

This script demonstrates the streaming interaction loop with
simulated audio input (sine waves instead of microphone).

Usage:
    python scripts/run_demo.py
    python scripts/run_demo.py --duration 30  # Run for 30 seconds
    python scripts/run_demo.py --with-background  # Enable Background Model
"""

import argparse
import time
import sys
import os

import numpy as np
import torch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interactformer import Orchestrator, InteractionModel, BackgroundModel
from interactformer.orchestrator.session import StreamingSession


def generate_synthetic_audio(
    duration_ms: int = 200,
    sample_rate: int = 24000,
    frequency: float = 440.0,
) -> torch.Tensor:
    """Generate a synthetic audio chunk (sine wave).

    This simulates microphone input for demonstration purposes.
    In production, this would be replaced with actual audio capture.
    """
    num_samples = int(sample_rate * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, num_samples, endpoint=False)
    samples = np.sin(2 * np.pi * frequency * t).astype(np.float32)
    # Add slight noise to simulate real audio
    samples += np.random.randn(num_samples).astype(np.float32) * 0.01
    return torch.from_numpy(samples)


def main():
    parser = argparse.ArgumentParser(
        description="InteractFormer Interactive Demo"
    )
    parser.add_argument(
        "--duration", type=int, default=10,
        help="Demo duration in seconds",
    )
    parser.add_argument(
        "--with-background", action="store_true",
        help="Enable Background Model (S2)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print detailed output for each micro-turn",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  InteractFormer — Interactive Demo")
    print("  Real-Time Multimodal Interaction Framework")
    print("=" * 60)
    print()
    print(f"Configuration:")
    print(f"  Duration: {args.duration}s")
    print(f"  Micro-turn: 200ms ({args.duration * 5} ticks)")
    print(f"  Background Model: {'Enabled' if args.with_background else 'Disabled'}")
    print(f"  Sample Rate: 24000 Hz")
    print()
    print("Initializing orchestrator...")

    # Initialize
    orchestrator = Orchestrator(
        d_model=2048,
        micro_turn_ms=200,
        enable_background=args.with_background,
    )
    orchestrator.initialize()

    # Create session
    session = orchestrator.create_session(user_id="demo_user")
    session.start()

    print(f"Session {session.session_id} started.")
    print()
    print("Streaming interaction loop:")
    print("-" * 60)

    # Main loop
    num_ticks = args.duration * 5  # 5 ticks per second at 200ms
    total_speech_frames = 0
    total_delegations = 0
    total_injections = 0

    try:
        for tick in range(num_ticks):
            # Generate synthetic audio for this tick
            # Vary frequency to simulate different speech patterns
            if tick % 10 < 5:  # Simulate speaking in bursts
                audio = generate_synthetic_audio(
                    frequency=220 + (tick % 5) * 110,  # Vary pitch
                )
            else:
                audio = generate_synthetic_audio(
                    frequency=0,  # Silence
                ) * 0.01

            # Process micro-turn
            output = orchestrator.process_micro_turn(
                session_id=session.session_id,
                audio_chunk=audio.unsqueeze(0),  # Add batch dim
            )

            if output is None:
                continue

            # Track statistics
            if output.cell.is_model_speaking:
                total_speech_frames += 1
            if output.should_delegate:
                total_delegations += 1

            if args.verbose:
                status = []
                if output.cell.is_user_speaking:
                    status.append("🎤 User speaking")
                if output.cell.is_model_speaking:
                    status.append("🔊 Model speaking")
                if output.should_delegate:
                    status.append("📤 Delegating to S2")
                if output.should_interrupt:
                    status.append("⚠️ Interrupting")
                if output.silence_duration_ms > 500:
                    status.append(f"🔇 Silent: {output.silence_duration_ms/1000:.1f}s")

                print(
                    f"  [{tick:4d}] "
                    f"t={tick*200}ms | "
                    f"{' | '.join(status) if status else '⏸️  Idle'}"
                    f" | speech_conf={output.speech_confidence:.2f}"
                    f" | del_score={output.delegation_score:.2f}"
                )

        print("-" * 60)
        print()
        print("Demo complete!")
        print()
        print("Session Summary:")
        summary = orchestrator.get_session_summary(session.session_id)
        if summary:
            for key, value in summary.items():
                print(f"  {key}: {value}")
        print()
        print(f"  Total speech frames: {total_speech_frames}")
        print(f"  Total delegations: {total_delegations}")
        print(f"  Speech rate: {total_speech_frames / num_ticks * 100:.1f}%")

        if args.with_background:
            stats = orchestrator.bridge_stats
            if stats:
                print()
                print("Bridge Statistics:")
                for key, value in stats.items():
                    print(f"  {key}: {value}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    finally:
        orchestrator.end_session(session.session_id)
        orchestrator.shutdown()

    print()
    print("Goodbye!")


if __name__ == "__main__":
    main()
