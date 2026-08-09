"""
Audio Encoder: dMel-based lightweight encoding for early fusion.

Implements the encoder-free early fusion approach from TML Interaction
Models. Instead of heavy preprocessing (Whisper encoder, etc.), audio is
converted to a dMel spectrogram and passed through a lightweight embedding
layer that is co-trained with the transformer.

Reference:
    Bai et al. (2024) "dMel: Differentiable Mel-Spectrogram for
    End-to-End Audio Processing"

Architecture:
    Raw Audio (24kHz) → STFT → Mel Filterbank → Log → dMel Embedding
"""

from typing import Optional, Tuple
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class MelFilterbank(nn.Module):
    """Differentiable mel filterbank.

    Converts linear-frequency STFT magnitudes to mel-scale energies.
    Uses triangular filters spaced uniformly on the mel scale.
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        n_fft: int = 512,
        n_mels: int = 80,
        f_min: float = 80.0,
        f_max: float = 7600.0,
    ):
        super().__init__()
        self.sample_rate = sample_rate
        self.n_fft = n_fft
        self.n_mels = n_mels

        # Create mel filterbank matrix
        mel_filters = self._create_mel_filters(
            n_fft, n_mels, sample_rate, f_min, f_max
        )
        self.register_buffer("mel_filters", mel_filters)

    @staticmethod
    def _create_mel_filters(
        n_fft: int, n_mels: int, sample_rate: int,
        f_min: float, f_max: float,
    ) -> torch.Tensor:
        """Create triangular mel filterbank matrix."""
        # Convert Hz to mel
        def hz_to_mel(hz):
            return 2595.0 * np.log10(1.0 + hz / 700.0)

        def mel_to_hz(mel):
            return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

        mel_min = hz_to_mel(f_min)
        mel_max = hz_to_mel(f_max)
        mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
        hz_points = mel_to_hz(mel_points)

        # Map to FFT bins
        bin_points = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

        filters = np.zeros((n_mels, n_fft // 2 + 1))
        for i in range(n_mels):
            left = bin_points[i]
            center = bin_points[i + 1]
            right = bin_points[i + 2]

            for j in range(left, center):
                filters[i, j] = (j - left) / max(center - left, 1)
            for j in range(center, right):
                filters[i, j] = (right - j) / max(right - center, 1)

        return torch.from_numpy(filters).float()

    def forward(self, spectrogram: torch.Tensor) -> torch.Tensor:
        """Apply mel filterbank to magnitude spectrogram.

        Args:
            spectrogram: [B, T, n_fft//2+1] magnitude spectrogram.

        Returns:
            [B, T, n_mels] mel spectrogram.
        """
        return torch.matmul(spectrogram, self.mel_filters.T)


class DMelEncoder(nn.Module):
    """dMel encoder: lightweight audio frontend for early fusion.

    Converts raw audio waveforms to learned mel-scale embeddings.
    Designed to be co-trained with the transformer backbone — there is
    no separate pre-trained audio encoder.

    Processing pipeline:
    1. STFT with 25ms Hann window, 10ms hop
    2. Magnitude computation
    3. Mel filterbank projection (80 bands)
    4. Log compression
    5. Lightweight Conv1D + Linear projection to model dimension

    At 24kHz with 200ms chunks:
        Input: 4800 samples → 20 time frames → 80 mel → d_model
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        n_fft: int = 512,
        hop_length: int = 240,  # 10ms at 24kHz
        win_length: int = 600,  # 25ms at 24kHz
        n_mels: int = 80,
        d_model: int = 2048,
        kernel_size: int = 3,
    ):
        super().__init__()
        self.sample_rate = sample_rate
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
        self.n_mels = n_mels

        # STFT window
        window = torch.hann_window(win_length)
        self.register_buffer("window", window)

        # Mel filterbank
        self.mel_filterbank = MelFilterbank(
            sample_rate=sample_rate,
            n_fft=n_fft,
            n_mels=n_mels,
        )

        # Lightweight convolutional embedding
        self.conv = nn.Conv1d(
            in_channels=n_mels,
            out_channels=d_model,
            kernel_size=kernel_size,
            padding=kernel_size // 2,
        )
        self.norm = nn.LayerNorm(d_model)

    def forward(
        self,
        audio: torch.Tensor,
        return_mel: bool = False,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """Encode audio waveform to embeddings.

        Args:
            audio: [B, T_samples] raw audio waveform.
            return_mel: If True, also return mel spectrogram.

        Returns:
            embeddings: [B, T_frames, d_model] audio embeddings.
            mel_spec: Optional [B, T_frames, n_mels] mel spectrogram.
        """
        # STFT
        stft = torch.stft(
            audio,
            n_fft=self.n_fft,
            hop_length=self.hop_length,
            win_length=self.win_length,
            window=self.window,
            return_complex=True,
        )
        magnitude = stft.abs()  # [B, n_fft//2+1, T_frames]

        # Mel filterbank
        mel_spec = self.mel_filterbank(
            magnitude.transpose(1, 2)
        )  # [B, T_frames, n_mels]

        # Log compression
        mel_spec = torch.log1p(mel_spec)

        # Convolutional embedding
        x = self.conv(mel_spec.transpose(1, 2))  # [B, d_model, T_frames]
        x = x.transpose(1, 2)  # [B, T_frames, d_model]
        x = self.norm(x)

        if return_mel:
            return x, mel_spec
        return x, None


class AudioEncoder(nn.Module):
    """Main audio encoder supporting multiple backends.

    Currently supports:
    - "dmel": TML-style encoder-free early fusion (default)
    - "whisper": Traditional Whisper-based encoder (legacy compatibility)

    The dmel encoder is preferred because it:
    1. Avoids the information bottleneck of a separate pre-trained encoder
    2. Allows the transformer to learn task-specific audio representations
    3. Reduces preprocessing latency (no encoder forward pass)
    """

    def __init__(
        self,
        encoder_type: str = "dmel",
        d_model: int = 2048,
        sample_rate: int = 24000,
        **kwargs,
    ):
        super().__init__()
        self.encoder_type = encoder_type

        if encoder_type == "dmel":
            self.encoder = DMelEncoder(
                sample_rate=sample_rate,
                d_model=d_model,
                **kwargs,
            )
        elif encoder_type == "whisper":
            # Placeholder for Whisper-based encoder
            # This would use a pre-trained Whisper encoder for compatibility
            raise NotImplementedError(
                "Whisper encoder placeholder: use 'dmel' for InteractFormer"
            )
        else:
            raise ValueError(f"Unknown encoder type: {encoder_type}")

    def forward(
        self, audio: torch.Tensor, **kwargs
    ) -> torch.Tensor:
        """Encode audio to embeddings."""
        embeddings, _ = self.encoder(audio, **kwargs)
        return embeddings

    @property
    def output_dim(self) -> int:
        if hasattr(self.encoder, 'd_model'):
            return self.encoder.d_model
        return self.encoder.n_mels
