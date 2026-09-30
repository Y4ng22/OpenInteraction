"""Compatibility helpers for external interaction-model checkpoints."""

from interactformer.compat.duplex import (
    CompatibilityItem,
    DuplexCompatibilityReport,
    TargetArchitecture,
    inspect_duplex_config,
    load_json_config,
)

__all__ = [
    "CompatibilityItem",
    "DuplexCompatibilityReport",
    "TargetArchitecture",
    "inspect_duplex_config",
    "load_json_config",
]

