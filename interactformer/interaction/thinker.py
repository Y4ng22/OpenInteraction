"""
Interaction Thinker: MoE-based fast reasoning for real-time interaction.

The Thinker is the "brain" of the Interaction Model. It's a Mixture-of-Experts
decoder transformer optimized for low-latency inference. Based on Qwen3-Omni's
Thinker architecture but modified for:

1. **Temporal grid conditioning**: Accepts temporal position encodings from
   the Explicit Temporal Grid, enabling time-aware reasoning.
2. **Bridge-aware attention**: Reserved attention slots for background model
   injections (Streaming Context Bridge).
3. **Speech-gated output**: Hidden states are gated before being passed to
   the Talker, enabling implicit turn management.

Architecture:
    Input Embeddings + Temporal PE
        ↓
    MoE Decoder (N layers, K experts, top-M routing)
        ↓
    Hidden States → Talker (for speech)
                 → Text Head (for text output)
                 → Bridge Head (for S2 delegation)
"""

from typing import Optional, Tuple
import torch
import torch.nn as nn
import torch.nn.functional as F


class MoELayer(nn.Module):
    """Mixture-of-Experts feedforward layer.

    Implements top-K expert routing with load-balancing loss.
    Based on the architecture used in Qwen3-Omni and TML Interaction Models.

    Key difference: adds a temporal bias to routing logits so that
    expert selection can be influenced by the time of day / session
    duration, enabling the model to adapt its behavior over long sessions.
    """

    def __init__(
        self,
        d_model: int = 2048,
        d_ff: int = 5632,
        num_experts: int = 8,
        num_experts_per_tok: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.num_experts = num_experts
        self.num_experts_per_tok = num_experts_per_tok

        # Router: predicts which experts to use
        self.router = nn.Linear(d_model, num_experts, bias=False)

        # Expert FFNs
        self.experts = nn.ModuleList([
            nn.Sequential(
                nn.Linear(d_model, d_ff),
                nn.SiLU(),
                nn.Dropout(dropout),
                nn.Linear(d_ff, d_model),
                nn.Dropout(dropout),
            )
            for _ in range(num_experts)
        ])

        # Load balancing parameters
        self.register_buffer(
            "expert_bias", torch.zeros(num_experts)
        )

    def forward(
        self, x: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """MoE forward with top-K routing.

        Args:
            x: [B, T, d_model]

        Returns:
            output: [B, T, d_model]
            routing_weights: [B, T, num_experts] for load balancing loss
        """
        B, T, D = x.shape
        x_flat = x.view(-1, D)  # [B*T, d_model]

        # Compute routing logits
        logits = self.router(x_flat) + self.expert_bias  # [B*T, num_experts]

        # Top-K selection
        top_k_logits, top_k_indices = torch.topk(
            logits, self.num_experts_per_tok, dim=-1
        )
        routing_weights = F.softmax(top_k_logits, dim=-1)  # [B*T, K]

        # Weighted sum of expert outputs
        output = torch.zeros_like(x_flat)
        for k in range(self.num_experts_per_tok):
            expert_idx = top_k_indices[:, k]  # [B*T]
            expert_weight = routing_weights[:, k].unsqueeze(-1)  # [B*T, 1]

            # Process each expert's tokens
            for e in range(self.num_experts):
                mask = (expert_idx == e)
                if mask.any():
                    expert_out = self.experts[e](x_flat[mask])
                    output[mask] += expert_out * expert_weight[mask]

        return output.view(B, T, D), F.softmax(logits, dim=-1)


class BridgeAttentionSlot(nn.Module):
    """Attention mechanism with reserved slots for background model results.

    This is how the Interaction Model "listens" to the Background Model.
    Instead of injecting S2 results as text markers (DuplexOmni's 「...」),
    we use reserved attention slots that the Bridge writes to and the
    Thinker reads from during self-attention.

    Each transformer layer has a few "bridge slots" that act as a
    communication channel between S1 and S2. S2 writes its results
    into these slots, and S1 attends to them alongside its own tokens.
    """

    def __init__(self, d_model: int = 2048, num_bridge_slots: int = 4):
        super().__init__()
        self.num_bridge_slots = num_bridge_slots

        # Bridge slot embeddings (learnable "query" positions)
        self.slot_queries = nn.Parameter(
            torch.randn(num_bridge_slots, d_model) * 0.02
        )

        # Gate for controlling how much bridge info to use
        self.bridge_gate = nn.Sequential(
            nn.Linear(d_model, d_model // 4),
            nn.SiLU(),
            nn.Linear(d_model // 4, 1),
            nn.Sigmoid(),
        )

    def forward(
        self,
        hidden_states: torch.Tensor,
        bridge_context: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Attend to bridge slots if context is provided.

        Args:
            hidden_states: [B, T, d_model] current hidden states.
            bridge_context: [B, num_bridge_slots, d_model] background
                model output to attend to.

        Returns:
            [B, T, d_model] hidden states with bridge information.
        """
        if bridge_context is None:
            return hidden_states

        # Compute attention between hidden states and bridge slots
        # (simplified: dot-product attention with learned gate)
        attn = torch.matmul(
            hidden_states, bridge_context.transpose(1, 2)
        )  # [B, T, num_slots]
        attn = F.softmax(attn / (self.slot_queries.size(-1) ** 0.5), dim=-1)

        bridge_info = torch.matmul(attn, bridge_context)  # [B, T, d_model]
        gate = self.bridge_gate(hidden_states)

        return hidden_states + gate * bridge_info


class InteractionThinker(nn.Module):
    """Fast-response MoE Thinker for the Interaction Model.

    This is the S1 "brain" — optimized for low latency and continuous
    streaming rather than deep reasoning. It processes each 200ms
    micro-turn with minimal delay.

    The Thinker can:
    1. Generate immediate responses (turn-by-turn conversation)
    2. Delegate complex queries to the Background Model via the Bridge
    3. Detect when it should speak (implicit turn management)
    4. Handle interruptions gracefully
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_layers: int = 24,
        num_heads: int = 16,
        num_kv_heads: int = 4,
        d_ff: int = 5632,
        num_experts: int = 8,
        num_experts_per_tok: int = 2,
        vocab_size: int = 152064,  # Qwen3 vocabulary
        num_bridge_slots: int = 4,
        max_seq_len: int = 32768,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.num_layers = num_layers
        self.vocab_size = vocab_size

        # Token embedding
        self.token_embedding = nn.Embedding(vocab_size, d_model)

        # Transformer layers with MoE FFN
        self.layers = nn.ModuleList([
            ThinkerLayer(
                d_model=d_model,
                num_heads=num_heads,
                num_kv_heads=num_kv_heads,
                d_ff=d_ff,
                num_experts=num_experts,
                num_experts_per_tok=num_experts_per_tok,
                dropout=dropout,
            )
            for _ in range(num_layers)
        ])

        # Bridge attention slots (distributed across layers)
        self.bridge_slots = nn.ModuleList([
            BridgeAttentionSlot(d_model, num_bridge_slots)
            for _ in range(num_layers)
        ])

        # Output heads
        self.text_head = nn.Linear(d_model, vocab_size, bias=False)

        # Delegation head: predicts when to delegate to S2
        self.delegation_head = nn.Sequential(
            nn.Linear(d_model, d_model // 4),
            nn.SiLU(),
            nn.Linear(d_model // 4, 1),
            nn.Sigmoid(),
        )

        # Final layer norm
        self.output_norm = nn.LayerNorm(d_model)

        # Rotary position embeddings
        self.rotary_emb = RotaryEmbedding(
            dim=d_model // num_heads,
            max_position=max_seq_len,
        )

    def forward(
        self,
        input_embeddings: torch.Tensor,
        bridge_context: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        return_hidden: bool = True,
    ) -> dict:
        """Process embeddings through the Thinker.

        Args:
            input_embeddings: [B, T, d_model] multimodal embeddings.
            bridge_context: [B, num_slots, d_model] S2 context for bridge.
            attention_mask: [T, T] causal attention mask.
            return_hidden: Whether to return all hidden states.

        Returns:
            dict with keys: 'hidden_states', 'logits', 'delegation_score',
            'routing_weights'.
        """
        B, T, D = input_embeddings.shape
        device = input_embeddings.device

        x = input_embeddings
        routing_weights = []

        for i, (layer, bridge_slot) in enumerate(
            zip(self.layers, self.bridge_slots)
        ):
            # Transformer layer
            x, routing = layer(x, attention_mask)
            routing_weights.append(routing)

            # Bridge attention (inject S2 context)
            layer_bridge = bridge_context[i] if bridge_context is not None \
                and i < len(bridge_context) else None
            x = bridge_slot(x, layer_bridge)

        x = self.output_norm(x)

        # Outputs
        logits = self.text_head(x)  # [B, T, vocab_size]

        # Delegation score: should this be sent to Background Model?
        delegation_score = self.delegation_head(x.mean(dim=1))  # [B, 1]

        return {
            "hidden_states": x,
            "logits": logits,
            "delegation_score": delegation_score,
            "routing_weights": routing_weights,
        }

    def embed_tokens(self, token_ids: torch.Tensor) -> torch.Tensor:
        """Convert token IDs to embeddings."""
        return self.token_embedding(token_ids)


class ThinkerLayer(nn.Module):
    """Single transformer layer for the Interaction Thinker.

    Uses Grouped Query Attention (GQA) and MoE FFN for efficiency.
    """

    def __init__(
        self,
        d_model: int = 2048,
        num_heads: int = 16,
        num_kv_heads: int = 4,
        d_ff: int = 5632,
        num_experts: int = 8,
        num_experts_per_tok: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.num_heads = num_heads
        self.num_kv_heads = num_kv_heads
        self.head_dim = d_model // num_heads

        # Self-attention (GQA)
        self.q_proj = nn.Linear(d_model, num_heads * self.head_dim, bias=False)
        self.k_proj = nn.Linear(d_model, num_kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(d_model, num_kv_heads * self.head_dim, bias=False)
        self.o_proj = nn.Linear(d_model, d_model, bias=False)

        # MoE FFN
        self.moe = MoELayer(
            d_model=d_model,
            d_ff=d_ff,
            num_experts=num_experts,
            num_experts_per_tok=num_experts_per_tok,
            dropout=dropout,
        )

        # Layer norms
        self.attn_norm = nn.LayerNorm(d_model)
        self.ffn_norm = nn.LayerNorm(d_model)

        self.dropout = nn.Dropout(dropout)

    def forward(
        self,
        x: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Process through attention + MoE FFN.

        Args:
            x: [B, T, d_model]
            attention_mask: [T, T] or [B, 1, T, T]

        Returns:
            output: [B, T, d_model]
            routing_weights: [B, T, num_experts]
        """
        # Self-attention with GQA
        residual = x
        x = self.attn_norm(x)
        x = self._attention(x, attention_mask)
        x = self.dropout(x) + residual

        # MoE FFN
        residual = x
        x = self.ffn_norm(x)
        x, routing = self.moe(x)
        x = self.dropout(x) + residual

        return x, routing

    def _attention(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Grouped Query Attention.

        Args:
            x: [B, T, d_model]
            mask: Optional attention mask.

        Returns:
            [B, T, d_model]
        """
        B, T, D = x.shape

        q = self.q_proj(x).view(B, T, self.num_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, T, self.num_kv_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, T, self.num_kv_heads, self.head_dim).transpose(1, 2)

        # Expand KV heads to match Q heads (GQA)
        k = k.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        v = v.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)

        # Scaled dot-product attention
        scale = self.head_dim ** -0.5
        attn = torch.matmul(q, k.transpose(-2, -1)) * scale

        if mask is not None:
            attn = attn.masked_fill(~mask, float('-inf'))

        attn = F.softmax(attn, dim=-1)
        attn = torch.matmul(attn, v)

        # Merge heads
        attn = attn.transpose(1, 2).contiguous().view(B, T, D)
        return self.o_proj(attn)


class RotaryEmbedding(nn.Module):
    """Rotary position embedding (RoPE).

    Standard RoPE implementation used across Qwen and most modern LLMs.
    """

    def __init__(self, dim: int, max_position: int = 32768, base: float = 10000.0):
        super().__init__()
        self.dim = dim
        self.max_position = max_position
        self.base = base

        # Precompute frequencies
        inv_freq = 1.0 / (
            base ** (torch.arange(0, dim, 2).float() / dim)
        )
        self.register_buffer("inv_freq", inv_freq)

        # Precomputed cos/sin cache
        self._build_cache(max_position)

    def _build_cache(self, max_position: int):
        t = torch.arange(max_position, dtype=torch.float)
        freqs = torch.einsum("i,j->ij", t, self.inv_freq)
        emb = torch.cat([freqs, freqs], dim=-1)
        self.register_buffer("cos_cached", emb.cos())
        self.register_buffer("sin_cached", emb.sin())

    def forward(
        self, x: torch.Tensor, position_ids: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Get cos and sin for given positions."""
        cos = self.cos_cached[position_ids]
        sin = self.sin_cached[position_ids]
        return cos, sin
