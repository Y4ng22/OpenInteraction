"""
Audio Codec for InteractFormer.

Implements the audio encoding/decoding pipeline based on the dMel
approach described in TML Interaction Models (Bai et al., 2024),
combined with flow matching for speech synthesis (Lipman et al., 2022).

The codec is designed for minimal preprocessing:
- Encoding: Audio → dMel spectrogram → lightweight embedding
- Decoding: Hidden states → flow matching head → waveform

Unlike traditional codecs (Mimi, SoundStream, EnCodec) that use
multi-codebook RVQ, our dMel + flow approach provides:
- Lower latency (no codebook lookup)
- Simpler training (end-to-end differentiable)
- Encoder-free early fusion compatibility (per TML's design)
"""

from interactformer.codec.audio_encoder import AudioEncoder, DMelEncoder
from interactformer.codec.audio_decoder import AudioDecoder, FlowMatchingDecoder

__all__ = [
    "AudioEncoder",
    "DMelEncoder",
    "AudioDecoder",
    "FlowMatchingDecoder",
]
