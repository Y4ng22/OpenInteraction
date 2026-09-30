"""
Multimodal Encoder: Early-fusion encoding for audio, vision, and text.

Implements the encoder-free early fusion approach from TML Interaction
Models. All modalities are encoded through lightweight, co-trained
transformations rather than separate pre-trained encoders.

Design philosophy (from TML's "bitter lesson" argument):
    Hand-crafted encoder pipelines create information bottlenecks.
    Lightweight, co-trained encoders allow the transformer to learn
    modality fusion directly from data.

Modalities:
    - Audio: dMel spectrogram → Conv1D embedding (see codec/)
    - Vision: 40×40 patches → hMLP encoding (Touvron et al., 2022)
    - Text: Token embedding (standard, shared with Thinker)
"""

from typing import Optional, Dict
import torch
import torch.nn as nn

from interactformer.codec.audio_encoder import AudioEncoder


class hMLPEncoder(nn.Module):
    """Hierarchical MLP for vision patch encoding.

    A lightweight alternative to ViT-style patch encoding. Instead of
    using self-attention over patches, hMLP uses a simple hierarchical
    MLP that processes patches independently, then fuses across spatial
    dimensions with a lightweight mixer.

    Reference:
        Touvron et al. (2022) "ResMLP: Feedforward networks for image
        classification with data-efficient training"

    Architecture:
        Patches (40×40×3) → PatchEmbed → hMLP blocks → Global pooling
    """

    def __init__(
        self,
        patch_size: int = 40,
        in_channels: int = 3,
        d_model: int = 2048,
        num_blocks: int = 4,
    ):
        super().__init__()
        self.patch_size = patch_size
        self.patch_dim = patch_size * patch_size * in_channels

        # Patch embedding
        self.patch_embed = nn.Linear(self.patch_dim, d_model)

        # Hierarchical MLP blocks
        self.blocks = nn.ModuleList([
            hMLPBlock(d_model) for _ in range(num_blocks)
        ])

        self.norm = nn.LayerNorm(d_model)

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        """Encode images to patch embeddings.

        Args:
            images: [B, C, H, W] input images.

        Returns:
            embeddings: [B, num_patches, d_model] patch embeddings.
        """
        B, C, H, W = images.shape

        # Extract patches
        patches = self._extract_patches(images)  # [B, N, patch_dim]
        x = self.patch_embed(patches)  # [B, N, d_model]

        # hMLP blocks
        for block in self.blocks:
            x = block(x)

        return self.norm(x)

    def _extract_patches(self, images: torch.Tensor) -> torch.Tensor:
        """Extract non-overlapping patches from images.

        Args:
            images: [B, C, H, W]

        Returns:
            patches: [B, num_patches, C*patch_size*patch_size]
        """
        B, C, H, W = images.shape
        p = self.patch_size

        # Ensure divisible
        assert H % p == 0 and W % p == 0, (
            f"Image dimensions ({H}, {W}) must be divisible by "
            f"patch size ({p})"
        )

        patches = images.unfold(2, p, p).unfold(3, p, p)
        patches = patches.permute(0, 2, 3, 1, 4, 5).contiguous()
        patches = patches.view(B, -1, C * p * p)

        return patches


class hMLPBlock(nn.Module):
    """Single hMLP block: cross-patch mixing + cross-channel mixing.

    Unlike transformer blocks that use self-attention, hMLP uses:
    1. Transpose-MLP for cross-patch interaction (spatial mixing)
    2. MLP for cross-channel interaction (channel mixing)
    """

    def __init__(self, d_model: int, expansion: int = 4):
        super().__init__()
        self.d_model = d_model

        # Cross-patch mixing (operates on transposed features)
        self.spatial_mixer = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model),
            nn.GELU(),
        )

        # Cross-channel mixing
        self.channel_mixer = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model * expansion),
            nn.GELU(),
            nn.Linear(d_model * expansion, d_model),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Apply hMLP transformations.

        Args:
            x: [B, N, d_model]

        Returns:
            [B, N, d_model]
        """
        # Cross-patch mixing (transpose → mix → transpose back)
        residual = x
        x = x.transpose(1, 2)  # [B, d_model, N]
        x = self.spatial_mixer(x.transpose(1, 2))
        x = x + residual

        # Cross-channel mixing
        x = x + self.channel_mixer(x)

        return x


class MultimodalEncoder(nn.Module):
    """Early-fusion multimodal encoder.

    Processes audio, vision, and text streams through lightweight,
    co-trained encoders and fuses them at the embedding level.

    This is the "encoder-free" approach: no Whisper, no ViT, no CLIP.
    All modality-specific processing is done by simple transformations
    that are trained end-to-end with the transformer.

    Fusion strategy:
        1. Each modality is independently embedded
        2. Modality-type embeddings are added (so the model knows what's what)
        3. All embeddings are concatenated along the time axis
        4. A lightweight fusion layer produces the final representation
    """

    def __init__(
        self,
        d_model: int = 2048,
        audio_sample_rate: int = 24000,
        vision_patch_size: int = 40,
        audio_encoder_type: str = "dmel",
        max_text_len: int = 2048,
    ):
        super().__init__()
        self.d_model = d_model

        # Modality encoders
        self.audio_encoder = AudioEncoder(
            encoder_type=audio_encoder_type,
            d_model=d_model,
            sample_rate=audio_sample_rate,
        )

        self.vision_encoder = hMLPEncoder(
            patch_size=vision_patch_size,
            in_channels=3,
            d_model=d_model,
        )

        # Text is tokenized externally; just a projection for alignment
        self.text_proj = nn.Linear(d_model, d_model)

        # Modality type embeddings
        self.modality_embeddings = nn.Embedding(3, d_model)  # audio=0, vision=1, text=2

        # Lightweight fusion transformer
        self.fusion = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=16,
            dim_feedforward=d_model * 4,
            dropout=0.1,
            batch_first=True,
        )

        self.output_norm = nn.LayerNorm(d_model)

    def forward(
        self,
        audio: Optional[torch.Tensor] = None,
        images: Optional[torch.Tensor] = None,
        text_tokens: Optional[torch.Tensor] = None,
        text_embeddings: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Encode and fuse multimodal inputs.

        At least one modality must be provided.

        Args:
            audio: [B, T_audio_samples] raw audio waveform.
            images: [B, C, H, W] images.
            text_tokens: [B, T_text] token IDs (delegated to Thinker's
                embedding table if available).
            text_embeddings: [B, T_text, d_model] pre-computed text embeddings.

        Returns:
            fused: [B, total_length, d_model] fused multimodal embeddings.
        """
        embeddings = []
        B = None

        if audio is not None:
            audio_emb = self.audio_encoder(audio)  # [B, T_audio, d_model]
            audio_emb = audio_emb + self.modality_embeddings(
                torch.zeros(audio_emb.size(0), device=audio_emb.device, dtype=torch.long)
            ).unsqueeze(1)
            embeddings.append(audio_emb)
            B = audio_emb.size(0)

        if images is not None:
            vision_emb = self.vision_encoder(images)  # [B, N_patches, d_model]
            vision_emb = vision_emb + self.modality_embeddings(
                torch.ones(vision_emb.size(0), device=vision_emb.device, dtype=torch.long)
            ).unsqueeze(1)
            embeddings.append(vision_emb)
            B = vision_emb.size(0)

        if text_embeddings is not None:
            text_emb = self.text_proj(text_embeddings)
            text_emb = text_emb + self.modality_embeddings(
                2 * torch.ones(text_emb.size(0), device=text_emb.device, dtype=torch.long)
            ).unsqueeze(1)
            embeddings.append(text_emb)

        if not embeddings:
            raise ValueError("At least one modality must be provided")

        # Concatenate along time dimension
        fused = torch.cat(embeddings, dim=1)  # [B, total_len, d_model]

        # Lightweight fusion
        fused = self.fusion(fused)
        fused = self.output_norm(fused)

        return fused
