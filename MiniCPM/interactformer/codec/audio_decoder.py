"""
Audio Decoder: Flow Matching based speech synthesis.

Implements streaming waveform generation using conditional flow matching,
following the approach described in TML Interaction Models.

Unlike traditional codec-based decoders (EnCodec, Mimi, SoundStream) that
use residual vector quantization, the flow matching approach:
1. Generates continuous mel-scale representations from model hidden states
2. Uses an ODE solver (flow) to transform noise → waveform
3. Operates frame-by-frame for streaming output

Reference:
    Lipman et al. (2022) "Flow Matching for Generative Modeling"
"""

from typing import Optional
import torch
import torch.nn as nn
import torch.nn.functional as F


class FlowMatchingDecoder(nn.Module):
    """Flow matching decoder for streaming waveform generation.

    Converts model hidden states to audio waveforms through:
    1. Hidden state → mel spectrogram prediction (condition network)
    2. Mel spectrogram → waveform via conditional flow matching (flow head)

    The flow operates in the mel-spectral domain for efficiency:
    - Forward: adds Gaussian noise to clean mel spectrograms
    - Reverse (inference): ODE integration from noise to clean mel
    - Griffin-Lim or neural vocoder: mel → waveform (final step)
    """

    def __init__(
        self,
        hidden_size: int = 2048,
        n_mels: int = 80,
        n_fft: int = 512,
        hop_length: int = 240,
        win_length: int = 600,
        sample_rate: int = 24000,
        flow_depth: int = 4,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.n_mels = n_mels
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
        self.sample_rate = sample_rate

        # Condition network: hidden states → mel features
        self.condition_proj = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.SiLU(),
            nn.Linear(hidden_size, n_mels),
        )

        # Flow network: predicts vector field v(x_t, t, condition)
        self.flow_net = nn.Sequential(
            nn.Linear(n_mels * 2 + 1, hidden_size),  # x_t + condition + t
            nn.SiLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.SiLU(),
            nn.Linear(hidden_size, n_mels),
        )

        # Neural vocoder: mel → waveform
        self.vocoder = nn.ConvTranspose1d(
            in_channels=n_mels,
            out_channels=1,
            kernel_size=hop_length * 4,
            stride=hop_length,
            padding=hop_length * 2 - hop_length // 2,
        )

        self.flow_depth = flow_depth

    def forward(
        self,
        hidden_states: torch.Tensor,
        num_inference_steps: int = 10,
    ) -> torch.Tensor:
        """Generate audio from hidden states using flow matching.

        Args:
            hidden_states: [B, T_frames, hidden_size] from Interaction Model.
            num_inference_steps: Number of ODE solver steps (more = higher
                quality, fewer = lower latency).

        Returns:
            waveform: [B, T_samples] generated audio waveform.
        """
        B, T, _ = hidden_states.shape
        device = hidden_states.device

        # Predict target mel spectrogram condition
        condition = self.condition_proj(hidden_states)  # [B, T, n_mels]

        # Flow matching inference: noise → mel
        mel = self._solve_ode(condition, num_inference_steps, device)

        # Vocoder: mel → waveform
        waveform = self.vocoder(mel.transpose(1, 2)).squeeze(1)  # [B, T_samples]

        return waveform

    def _solve_ode(
        self,
        condition: torch.Tensor,
        num_steps: int,
        device: torch.device,
    ) -> torch.Tensor:
        """Solve the probability flow ODE from noise to data.

        Uses Euler method for simplicity. For production, this would be
        replaced with a higher-order solver (RK4, DPM-Solver).

        Args:
            condition: [B, T, n_mels] mel condition.
            num_steps: Number of integration steps.

        Returns:
            mel: [B, T, n_mels] predicted mel spectrogram.
        """
        B, T, C = condition.shape
        dt = 1.0 / num_steps

        # Start from Gaussian noise
        x = torch.randn(B, T, C, device=device)

        for step in range(num_steps):
            t = step * dt
            t_tensor = torch.full((B, T, 1), t, device=device)

            # Concatenate [x, condition, t] along feature dimension
            net_input = torch.cat([x, condition, t_tensor], dim=-1)

            # Predict vector field
            v = self.flow_net(net_input)

            # Euler step
            x = x + v * dt

        return x

    def stream_forward(
        self,
        hidden_state: torch.Tensor,
        previous_mel: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Streaming forward pass for one micro-turn.

        Processes a single frame of hidden states (1 micro-turn = 200ms
        = ~20 mel frames) with optional conditioning on the previous
        frame's mel for temporal consistency.

        Args:
            hidden_state: [B, 1, hidden_size] single-frame hidden state.
            previous_mel: [B, 1, n_mels] previous frame's mel for continuity.

        Returns:
            waveform_chunk: [B, samples_per_frame] audio for this micro-turn.
        """
        mel_condition = self.condition_proj(hidden_state)

        if previous_mel is not None:
            # Blend with previous frame for smooth transitions
            mel_condition = 0.3 * previous_mel + 0.7 * mel_condition

        waveform = self.vocoder(
            mel_condition.transpose(1, 2)
        ).squeeze(1)

        return waveform


class AudioDecoder(nn.Module):
    """Main audio decoder with streaming support.

    Wraps the FlowMatchingDecoder and provides:
    - Frame-by-frame streaming generation
    - Temporal consistency across micro-turns
    - Latency-adaptive quality control (fewer ODE steps = faster)
    """

    def __init__(
        self,
        hidden_size: int = 2048,
        n_mels: int = 80,
        sample_rate: int = 24000,
        quality: str = "balanced",  # "fast", "balanced", "high"
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.sample_rate = sample_rate

        self.decoder = FlowMatchingDecoder(
            hidden_size=hidden_size,
            n_mels=n_mels,
            sample_rate=sample_rate,
        )

        # Quality preset → ODE steps
        self.quality_steps = {
            "fast": 5,
            "balanced": 10,
            "high": 20,
        }
        self.quality = quality

    @property
    def num_inference_steps(self) -> int:
        return self.quality_steps.get(self.quality, 10)

    def forward(
        self,
        hidden_states: torch.Tensor,
        **kwargs,
    ) -> torch.Tensor:
        """Generate audio waveform from hidden states."""
        return self.decoder(
            hidden_states,
            num_inference_steps=self.num_inference_steps,
            **kwargs,
        )

    def generate_frame(
        self,
        hidden_state: torch.Tensor,
        previous_mel: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Generate one frame of audio for streaming output."""
        return self.decoder.stream_forward(hidden_state, previous_mel)
