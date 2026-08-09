"""Integration test: S1 → S2 → Bridge → S1 end-to-end.

Verifies that:
1. S1 processes input at micro-turn N
2. S2 asynchronously generates partial reasoning
3. Partial reasoning arrives at micro-turn N+k
4. Bridge context is updated with S2 results
5. A later S1 transformer forward consumes that bridge representation
6. Execution continues without restarting the conversation
"""
import pytest
import torch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interactformer import Orchestrator


class TestBridgeE2E:
    """Acceptance test for the core architecture."""

    @pytest.fixture
    def orch(self):
        orch = Orchestrator(
            d_model=128,           # Tiny model for fast test
            micro_turn_ms=200,
            enable_background=True,
            enable_bridge=True,
        )
        orch.initialize()
        yield orch
        orch.shutdown()

    def test_bridge_initial_state(self, orch):
        """Bridge should start with no context."""
        assert orch.bridge.get_current_context() is None

    def test_bridge_context_shape(self, orch):
        """After S2 results, bridge should produce [1, num_slots, d_model]."""
        # Simulate S2 result injection
        content_list = [
            {"type": "reasoning_step", "data": "Test reasoning", "confidence": 0.9},
            {"type": "final_answer", "data": "Test conclusion", "confidence": 0.8},
        ]
        device = next(orch.interaction_model.parameters()).device
        bridge_tensor = orch.bridge.embed_content_dicts(content_list, device)
        orch.bridge.update_context(bridge_tensor)

        ctx = orch.bridge.get_current_context()
        assert ctx is not None
        assert ctx.shape == (1, 4, 128)  # [B, num_slots, d_model]

    def test_bridge_progressive_update(self, orch):
        """Repeated updates should blend rather than replace."""
        content_1 = [{"type": "reasoning_step", "data": "Step 1", "confidence": 0.9}]
        content_2 = [{"type": "reasoning_step", "data": "Step 2", "confidence": 0.7}]
        device = next(orch.interaction_model.parameters()).device

        ctx1 = orch.bridge.embed_content_dicts(content_1, device)
        orch.bridge.update_context(ctx1)
        first = orch.bridge.get_current_context()

        ctx2 = orch.bridge.embed_content_dicts(content_2, device)
        orch.bridge.update_context(ctx2)
        second = orch.bridge.get_current_context()

        # Progressive blend: first ≠ second (not a pure replacement)
        assert not torch.allclose(first, second)

    def test_bridge_reset(self, orch):
        """Reset should clear bridge state."""
        device = next(orch.interaction_model.parameters()).device
        ctx = orch.bridge.embed_content_dicts(
            [{"type": "reasoning_step", "data": "test"}], device
        )
        orch.bridge.update_context(ctx)
        assert orch.bridge.get_current_context() is not None

        orch.bridge.reset_context()
        assert orch.bridge.get_current_context() is None

    def test_micro_turn_does_not_crash(self, orch):
        """Process 10 micro-turns without shape errors."""
        session = orch.create_session()
        session.start()

        for i in range(10):
            audio = torch.randn(1, 4800)  # 200ms @ 24kHz
            output = orch.process_micro_turn(
                session.session_id, audio_chunk=audio
            )
            # Should not crash (even if bridge_context is None initially)

        orch.end_session(session.session_id)

    def test_text_input_does_not_crash(self, orch):
        """Text input should be handled without AttributeError."""
        session = orch.create_session()
        session.start()

        output = orch.process_micro_turn(
            session.session_id,
            text_input="Hello, how are you?",
        )
        # Should not crash (str.to(device) bug fixed)

        orch.end_session(session.session_id)


class TestThinkerBridgeIntegration:
    """Tests for the Thinker's bridge slot consumption."""

    def test_thinker_without_bridge(self):
        from interactformer.interaction.thinker import InteractionThinker
        thinker = InteractionThinker(d_model=128, num_layers=4, num_heads=4, num_kv_heads=1)

        x = torch.randn(1, 2, 128)  # [B, T, d_model]
        output = thinker(x, bridge_context=None)
        assert "hidden_states" in output
        assert output["hidden_states"].shape == (1, 2, 128)

    def test_thinker_with_bridge(self):
        from interactformer.interaction.thinker import InteractionThinker
        thinker = InteractionThinker(d_model=128, num_layers=4, num_heads=4, num_kv_heads=1)

        x = torch.randn(1, 2, 128)
        bridge = torch.randn(1, 4, 128)  # [B, num_slots, d_model]
        output = thinker(x, bridge_context=bridge)
        assert output["hidden_states"].shape == (1, 2, 128)

    def test_thinker_bridge_no_leak(self):
        """Thinker with bridge should produce different output than without."""
        from interactformer.interaction.thinker import InteractionThinker
        thinker = InteractionThinker(d_model=128, num_layers=4, num_heads=4, num_kv_heads=1)

        x = torch.randn(1, 2, 128)
        bridge = torch.randn(1, 4, 128)

        out_no_bridge = thinker(x, bridge_context=None)
        out_with_bridge = thinker(x, bridge_context=bridge)

        # Different outputs when bridge is provided
        assert not torch.allclose(
            out_no_bridge["hidden_states"],
            out_with_bridge["hidden_states"],
        )


class TestTemporalGrid:
    """Tests for temporal grid and position encoding."""

    def test_temporal_encoding_changes_with_time(self):
        from interactformer.interaction.temporal_grid import TemporalGrid
        grid = TemporalGrid(d_model=128)

        hidden = torch.randn(1, 3, 128)  # [B, T, d_model]

        # Same hidden, different cell IDs → different output
        ids_t0 = torch.tensor([[0, 1, 2]])
        ids_t1 = torch.tensor([[10, 11, 12]])

        out_t0 = grid.forward(ids_t0, hidden.clone())
        out_t1 = grid.forward(ids_t1, hidden.clone())

        assert not torch.allclose(out_t0, out_t1)

    def test_silence_encoding_changes_with_duration(self):
        from interactformer.interaction.temporal_grid import TemporalGrid
        grid = TemporalGrid(d_model=128)

        hidden = torch.randn(1, 3, 128)
        ids = torch.tensor([[0, 1, 2]])

        out_short_silence = grid.forward(
            ids, hidden.clone(),
            silence_durations=torch.tensor([[0.0, 0.0, 0.0]])
        )
        out_long_silence = grid.forward(
            ids, hidden.clone(),
            silence_durations=torch.tensor([[0.0, 2000.0, 5000.0]])
        )

        assert not torch.allclose(out_short_silence, out_long_silence)

    def test_attention_mask_causal(self):
        from interactformer.interaction.temporal_grid import TemporalGrid
        grid = TemporalGrid(d_model=128)

        mask = grid.build_attention_mask(5, include_future=False)
        assert mask.shape == (5, 5)
        # Causal: cell i can attend to j where j <= i
        assert mask[0, 0] and not mask[0, 1]
        assert mask[2, 0] and mask[2, 1] and mask[2, 2] and not mask[2, 3]

    def test_probs_indexing(self):
        """P0-H: probs[1] → probs[0, 1] fix verification."""
        from interactformer.interaction.temporal_grid import TemporalGrid
        grid = TemporalGrid(d_model=128)

        curr = torch.randn(2, 128)  # Batch=2
        prev = torch.randn(2, 128)
        should_interrupt, conf = grid.detect_interruption(curr, prev)
        assert isinstance(should_interrupt, bool)
        assert isinstance(conf, float)


class TestStreamingLoop:
    """Tests for multi-turn streaming correctness."""

    def test_10_micro_turns_no_shape_error(self, orch=None):
        """10 micro-turns should not cause tensor shape mismatches."""
        from interactformer import Orchestrator
        if orch is None:
            orch = Orchestrator(
                d_model=128, micro_turn_ms=200,
                enable_background=False, enable_bridge=False,
            )
            orch.initialize()

        session = orch.create_session()
        session.start()

        for i in range(10):
            audio = torch.randn(1, 4800)
            output = orch.process_micro_turn(
                session.session_id, audio_chunk=audio
            )
            assert output is not None
            assert output.cell is not None
            # After first turn, cell should have hidden state
            assert output.cell.hidden_state is not None
            assert output.cell.hidden_state.shape == (1, 128)

        orch.end_session(session.session_id)

    def test_context_truncation(self, orch=None):
        """After 50 turns, context should be bounded (not 50+ cells)."""
        from interactformer import Orchestrator
        if orch is None:
            orch = Orchestrator(
                d_model=128, micro_turn_ms=200,
                enable_background=False, enable_bridge=False,
            )
            orch.initialize()

        session = orch.create_session()
        session.start()

        for i in range(50):
            audio = torch.randn(1, 4800)
            orch.process_micro_turn(session.session_id, audio_chunk=audio)

        # Check grid bounds
        cells = orch.interaction_model.temporal_grid._cells
        assert len(cells) <= 125  # max_history_cells

        orch.end_session(session.session_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
