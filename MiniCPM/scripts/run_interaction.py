#!/usr/bin/env python
"""
Run InteractFormer interaction model in a simple loop.

More detailed runner than the demo — intended for testing
and development with actual audio I/O.

Usage:
    python scripts/run_interaction.py
    python scripts/run_interaction.py --config configs/model_config.yaml
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(
        description="Run InteractFormer Interaction Model"
    )
    parser.add_argument(
        "--config", type=str, default="configs/model_config.yaml",
        help="Path to model configuration file",
    )
    parser.add_argument(
        "--inference-config", type=str, default="configs/inference_config.yaml",
        help="Path to inference configuration file",
    )
    parser.add_argument(
        "--device", type=str, default="cuda",
        help="Device to run on (cuda, cpu)",
    )
    parser.add_argument(
        "--model-path", type=str, default=None,
        help="Path to pre-trained model weights",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  InteractFormer — Interaction Model Runner")
    print("=" * 60)
    print()

    # Load config
    from interactformer.utils.config import Config
    try:
        config = Config.load(args.config)
        print(f"Loaded model config from: {args.config}")
    except FileNotFoundError:
        print(f"Config file not found: {args.config}")
        print("Using default configuration.")
        config = Config()

    # Create Interaction Model
    from interactformer.interaction.interaction_model import InteractionModel

    model_cfg = config.model.interaction
    print()
    print("Creating Interaction Model with:")
    print(f"  d_model: {model_cfg.hidden_size}")
    print(f"  num_layers: {model_cfg.num_layers}")
    print(f"  micro_turn_ms: {model_cfg.micro_turn_ms}")
    print(f"  audio_encoder: {model_cfg.audio_encoder_type}")
    print(f"  num_experts: {model_cfg.num_experts}")
    print(f"  num_experts_per_tok: {model_cfg.num_experts_per_tok}")

    model = InteractionModel(
        d_model=model_cfg.hidden_size,
        num_layers=model_cfg.num_layers,
        num_experts=model_cfg.num_experts,
        num_experts_per_tok=model_cfg.num_experts_per_tok,
        audio_sample_rate=model_cfg.audio_sample_rate,
        micro_turn_ms=model_cfg.micro_turn_ms,
    )

    device = args.device
    if device == "cuda" and not __import__("torch").cuda.is_available():
        print("CUDA not available, falling back to CPU.")
        device = "cpu"

    model = model.to(device)
    model.eval()

    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(
        p.numel() for p in model.parameters() if p.requires_grad
    )
    print()
    print(f"Model parameters: {total_params/1e9:.2f}B total, "
          f"{trainable_params/1e9:.2f}B trainable")
    print(f"Device: {device}")
    print()

    # Load weights if provided
    if args.model_path:
        print(f"Loading weights from: {args.model_path}")
        # checkpoint = torch.load(args.model_path, map_location=device)
        # model.load_state_dict(checkpoint)
        print("  (Weight loading not implemented in this placeholder)")

    print()
    print("Interaction Model ready.")
    print("Run scripts/run_demo.py for the interactive demo.")
    print()


if __name__ == "__main__":
    main()
