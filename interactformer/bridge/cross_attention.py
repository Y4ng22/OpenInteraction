"""
Cross-Attention Fusion: Neural mechanism for S2→S1 context injection.

This is the neural "hardware" that implements the Streaming Context
Bridge. Instead of text markers (DuplexOmni) or discrete packages (TML),
we use cross-attention to fuse S2 results into S1's ongoing computation.

How it works:
1. S2 results are embedded into a set of "bridge vectors"
2. The Interaction Thinker computes cross-attention between its
   hidden states and these bridge vectors
3. A learned gate controls how much bridge information to incorporate
4. The fusion happens at EVERY transformer layer (not just the input)

This design allows:
- Smooth, continuous integration of S2 knowledge
- The model to "ignore" bridge context when it's not relevant
- Differential integration: some layers use more bridge info than others
- Multiple concurrent S2 streams to coexist (different bridge slots)
"""

from typing import Optional, Tuple
import torch
import torch.nn as nn
import torch.nn.functional as F


class BridgeProjector(nn.Module):
    """Projects S2 results into bridge vectors for S1 cross-attention.

    Takes heterogeneous S2 outputs (text, retrieval results, tool outputs)
    and projects them into a unified embedding space that S1 can attend to.
    """

    def __init__(
        self,
        d_model: int = 2048,
        d_bridge: int = 512,
        num_bridge_slots: int = 4,
    ):
        super().__init__()
        self.d_model = d_model
        self.d_bridge = d_bridge
        self.num_bridge_slots = num_bridge_slots

        # Text encoder for S2 text output
        self.text_encoder = nn.Sequential(
            nn.Linear(d_model, d_bridge),
            nn.LayerNorm(d_bridge),
            nn.SiLU(),
        )

        # Retrieval encoder
        self.retrieval_encoder = nn.Sequential(
            nn.Linear(d_model, d_bridge),
            nn.LayerNorm(d_bridge),
            nn.SiLU(),
        )

        # Tool result encoder
        self.tool_encoder = nn.Sequential(
            nn.Linear(d_model, d_bridge),
            nn.LayerNorm(d_bridge),
            nn.SiLU(),
        )

        # Bridge slot embeddings (learnable "query" positions for S2)
        self.slot_embeddings = nn.Parameter(
            torch.randn(num_bridge_slots, d_bridge) * 0.02
        )

        # Project bridge vectors to model dimension
        self.output_proj = nn.Linear(d_bridge, d_model)

    def forward(
        self,
        s2_text: Optional[torch.Tensor] = None,
        s2_retrieval: Optional[torch.Tensor] = None,
        s2_tool: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Project S2 results into bridge vectors.

        Args:
            s2_text: [B, d_model] encoded S2 text output.
            s2_retrieval: [B, d_model] encoded retrieval results.
            s2_tool: [B, d_model] encoded tool results.

        Returns:
            [B, num_bridge_slots, d_model] bridge context vectors.
        """
        B = 1
        if s2_text is not None:
            B = s2_text.size(0)
        elif s2_retrieval is not None:
            B = s2_retrieval.size(0)
        elif s2_tool is not None:
            B = s2_tool.size(0)

        device = self.slot_embeddings.device
        bridge_vectors = []

        # Encode each S2 output type into its bridge slot
        if s2_text is not None:
            text_vec = self.text_encoder(s2_text)  # [B, d_bridge]
            bridge_vectors.append(text_vec)
        else:
            bridge_vectors.append(
                torch.zeros(B, self.d_bridge, device=device)
            )

        if s2_retrieval is not None:
            ret_vec = self.retrieval_encoder(s2_retrieval)
            bridge_vectors.append(ret_vec)
        else:
            bridge_vectors.append(
                torch.zeros(B, self.d_bridge, device=device)
            )

        if s2_tool is not None:
            tool_vec = self.tool_encoder(s2_tool)
            bridge_vectors.append(tool_vec)
        else:
            bridge_vectors.append(
                torch.zeros(B, self.d_bridge, device=device)
            )

        # Additional learnable slots (for future S2 outputs)
        for i in range(3, self.num_bridge_slots):
            slot = self.slot_embeddings[i].unsqueeze(0).expand(B, -1)
            bridge_vectors.append(slot)

        # Stack and project
        bridge = torch.stack(bridge_vectors, dim=1)  # [B, num_slots, d_bridge]
        bridge = self.output_proj(bridge)  # [B, num_slots, d_model]

        return bridge


class CrossAttentionFusion(nn.Module):
    """Cross-attention based fusion of S2 context into S1.

    This is the neural core of the Streaming Context Bridge.
    For each S1 hidden state, we compute how much attention it
    should pay to each S2 bridge vector, then fuse accordingly.

    Key properties:
    1. **Layer-specific**: Each transformer layer has its own fusion
       module, allowing different layers to use different amounts
       of background knowledge.
    2. **Gated**: A learned gate controls the fusion strength,
       allowing the model to ignore irrelevant S2 context.
    3. **Multi-slot**: Multiple bridge slots allow concurrent S2
       streams (reasoning, retrieval, tools) to coexist.
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_heads: int = 8,
        num_bridge_slots: int = 4,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.num_heads = num_heads
        self.head_dim = d_model // num_heads

        # Cross-attention: S1 attends to S2 bridge vectors
        self.q_proj = nn.Linear(d_model, d_model, bias=False)
        self.k_proj = nn.Linear(d_model, d_model, bias=False)
        self.v_proj = nn.Linear(d_model, d_model, bias=False)
        self.o_proj = nn.Linear(d_model, d_model, bias=False)

        # Fusion gate: learned control of how much S2 info to use
        self.gate = nn.Sequential(
            nn.Linear(d_model * 2, d_model // 4),
            nn.SiLU(),
            nn.Linear(d_model // 4, d_model),
            nn.Sigmoid(),
        )

        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(d_model)

    def forward(
        self,
        s1_hidden: torch.Tensor,
        bridge_context: torch.Tensor,
        layer_idx: int = 0,
    ) -> torch.Tensor:
        """Fuse S2 bridge context into S1 hidden states.

        Args:
            s1_hidden: [B, T, d_model] S1 hidden states.
            bridge_context: [B, num_slots, d_model] S2 bridge vectors.
            layer_idx: Which transformer layer this is (for
                layer-specific gating behavior).

        Returns:
            [B, T, d_model] fused hidden states.
        """
        B, T, D = s1_hidden.shape
        num_slots = bridge_context.size(1)

        # Cross-attention: S1 queries, S2 keys/values
        q = self.q_proj(s1_hidden).view(
            B, T, self.num_heads, self.head_dim
        ).transpose(1, 2)  # [B, num_heads, T, head_dim]

        k = self.k_proj(bridge_context).view(
            B, num_slots, self.num_heads, self.head_dim
        ).transpose(1, 2)  # [B, num_heads, num_slots, head_dim]

        v = self.v_proj(bridge_context).view(
            B, num_slots, self.num_heads, self.head_dim
        ).transpose(1, 2)

        # Scaled dot-product cross-attention
        scale = self.head_dim ** -0.5
        attn = torch.matmul(q, k.transpose(-2, -1)) * scale
        attn = F.softmax(attn, dim=-1)
        attn = self.dropout(attn)

        bridge_output = torch.matmul(attn, v)  # [B, num_heads, T, head_dim]
        bridge_output = bridge_output.transpose(1, 2).contiguous().view(B, T, D)
        bridge_output = self.o_proj(bridge_output)

        # Gated fusion: how much S2 context to incorporate?
        gate_input = torch.cat([s1_hidden, bridge_output], dim=-1)
        gate = self.gate(gate_input)

        # Fuse: gate * bridge + (1 - gate) * original
        # Gate near 0 → ignore S2, gate near 1 → fully incorporate S2
        fused = gate * bridge_output + (1 - gate) * s1_hidden

        return self.norm(fused)

    @staticmethod
    def build_bridge_mask(
        num_slots: int,
        active_slots: list[int],
    ) -> torch.Tensor:
        """Build an attention mask for bridge slots.

        Only active slots (those with actual S2 content) are attended to.
        Empty slots are masked out.

        Args:
            num_slots: Total number of bridge slots.
            active_slots: Indices of slots with content.

        Returns:
            [num_slots] boolean mask (True = attend).
        """
        mask = torch.zeros(num_slots, dtype=torch.bool)
        for slot_idx in active_slots:
            if 0 <= slot_idx < num_slots:
                mask[slot_idx] = True
        return mask


class StreamingContextBridge(nn.Module):
    """Complete Streaming Context Bridge module.

    This is the top-level bridge component that combines:
    - BridgeProjector: S2 results → bridge vectors
    - CrossAttentionFusion: bridge vectors → S1 hidden states
    - StreamInjector: manages injection timing (not a nn.Module)

    This module is what gets imported and used by the Orchestrator.
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_bridge_slots: int = 4,
        num_layers: int = 24,
        num_heads: int = 8,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.num_bridge_slots = num_bridge_slots
        self.num_layers = num_layers

        # S2 → bridge vectors
        self.projector = BridgeProjector(
            d_model=d_model,
            num_bridge_slots=num_bridge_slots,
        )

        # Per-layer cross-attention fusion
        self.fusion_layers = nn.ModuleList([
            CrossAttentionFusion(
                d_model=d_model,
                num_heads=num_heads,
                num_bridge_slots=num_bridge_slots,
                dropout=dropout,
            )
            for _ in range(num_layers)
        ])

    def project_s2_results(
        self,
        s2_text: Optional[torch.Tensor] = None,
        s2_retrieval: Optional[torch.Tensor] = None,
        s2_tool: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Convert S2 results to bridge context vectors.

        Args:
            s2_text: [B, d_model] encoded S2 text output.
            s2_retrieval: [B, d_model] encoded retrieval.
            s2_tool: [B, d_model] encoded tool results.

        Returns:
            [B, num_bridge_slots, d_model] bridge context.
        """
        return self.projector(s2_text, s2_retrieval, s2_tool)

    def fuse_layer(
        self,
        layer_idx: int,
        s1_hidden: torch.Tensor,
        bridge_context: torch.Tensor,
    ) -> torch.Tensor:
        """Apply bridge fusion at a specific transformer layer.

        Args:
            layer_idx: Which layer to apply fusion at.
            s1_hidden: [B, T, d_model] S1 hidden states at this layer.
            bridge_context: [B, num_slots, d_model] bridge vectors.

        Returns:
            [B, T, d_model] fused hidden states.
        """
        if bridge_context is None:
            return s1_hidden

        return self.fusion_layers[layer_idx](
            s1_hidden, bridge_context, layer_idx=layer_idx,
        )
