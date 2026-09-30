"""Regression tests for session isolation, bounded streaming, and tool safety."""

from types import SimpleNamespace
import time

import numpy as np
import torch

from interactformer.background.background_model import BackgroundResult
from interactformer.background.tool_executor import ToolExecutor, ToolStatus
from interactformer.bridge.stream_injector import (
    BridgeMessage,
    InjectionPriority,
    InjectionScheduler,
    InjectionStrategy,
    StreamInjector,
)
from interactformer.utils.streaming import StreamingBuffer
from interactformer.interaction.talker import StreamingTalker
from interactformer.interaction.interaction_model import InteractionModel


def test_calculator_does_not_execute_python_objects():
    executor = ToolExecutor(max_retries=0)

    ok = executor.execute([
        {"name": "calculator", "arguments": {"expression": "2 ** 8 + sum([1, 2, 3])"}}
    ])
    assert ok.calls[0].status == ToolStatus.SUCCESS
    assert ok.calls[0].result == "262"

    exploit = executor.execute([
        {
            "name": "calculator",
            "arguments": {"expression": "().__class__.__mro__[1].__subclasses__()"},
        }
    ])
    assert exploit.calls[0].status == ToolStatus.SUCCESS
    assert exploit.calls[0].result.startswith("Calculation error:")


def test_tool_timeout_is_reported_without_blocking_the_batch():
    executor = ToolExecutor(default_timeout_ms=10, max_retries=0)
    executor.register_tool("slow", lambda _: time.sleep(0.1))
    started = time.monotonic()
    result = executor.execute([{"name": "slow", "arguments": {}}])
    assert result.calls[0].status == ToolStatus.TIMEOUT
    assert time.monotonic() - started < 0.08


def test_streaming_buffer_preserves_partial_packets():
    buffer = StreamingBuffer(micro_turn_ms=200, sample_rate=1000)
    assert buffer.push_audio(np.ones(75, dtype=np.float32)) == []
    assert buffer.push_audio(np.ones(75, dtype=np.float32)) == []
    turns = buffer.push_audio(np.ones(50, dtype=np.float32))
    assert len(turns) == 1
    assert turns[0].audio_in.num_samples == 200


def test_injection_scheduler_is_bounded_and_keeps_stream_order():
    scheduler = InjectionScheduler(
        strategy=InjectionStrategy.EAGER,
        max_queued_chunks=3,
        chunk_timeout_ms=60_000,
    )
    for index in range(6):
        scheduler.enqueue(BridgeMessage(
            message_id=f"m{index}",
            direction="s2_to_s1",
            content={"data": str(index)},
            stream_id="stream",
            chunk_index=index,
            priority=(InjectionPriority.HIGH if index == 5 else InjectionPriority.NORMAL),
        ))

    assert scheduler.pending_count == 3
    delivered = []
    while scheduler.pending_count:
        delivered.append(scheduler.get_next_injection(0).chunk_index)
    assert delivered == sorted(delivered)


def test_cancel_stream_reports_actual_count():
    scheduler = InjectionScheduler(chunk_timeout_ms=60_000)
    for index in range(2):
        scheduler.enqueue(BridgeMessage(
            message_id=f"cancel-{index}",
            direction="s2_to_s1",
            content={},
            stream_id="cancel-me",
            chunk_index=index,
        ))
    assert scheduler.cancel_stream("cancel-me") == 2
    assert scheduler.pending_count == 0


def test_final_only_background_result_is_not_dropped():
    injector = StreamInjector(d_model=32)
    result = BackgroundResult(
        task_id="task-1",
        session_id="session-a",
        final_answer="Background processing error: unavailable",
        confidence=0.0,
    )
    injector.receive_result(result)
    content = injector.get_context_for_cell(0, is_model_speaking=False)
    assert content == [{
        "type": "final_answer",
        "data": "Background processing error: unavailable",
        "confidence": 0.0,
    }]


def test_partial_and_final_results_are_deduplicated():
    injector = StreamInjector(d_model=32)
    step = SimpleNamespace(step_id=1, thought="same step", confidence=0.8, is_final=False)
    injector.receive_result(BackgroundResult(
        task_id="task-2", reasoning_steps=[step], partial=True, stream_id=1
    ))
    injector.receive_result(BackgroundResult(
        task_id="task-2", reasoning_steps=[step], final_answer="done",
        confidence=0.9, partial=False, stream_id=2,
    ))

    first = injector.get_context_for_cell(0, is_model_speaking=False)
    assert [item["type"] for item in first] == ["reasoning_step", "final_answer"]


def test_talker_returns_one_exact_micro_turn_and_can_resume():
    talker = StreamingTalker(
        d_model=32,
        num_codebooks=2,
        codebook_size=16,
        codebook_dim=8,
        sample_rate=24_000,
        frame_rate=12.5,
        chunk_duration_ms=200,
    )
    hidden = torch.randn(1, 1, 32)
    first, _ = talker(hidden)
    interrupted, _ = talker(hidden, is_interrupted=True)
    resumed, _ = talker(hidden)
    assert first.shape == interrupted.shape == resumed.shape == (1, 4800)
    assert torch.count_nonzero(interrupted) == 0


def test_interaction_runtime_state_is_isolated_per_session():
    model = InteractionModel(
        d_model=32,
        num_layers=1,
        num_heads=4,
        num_kv_heads=1,
        d_ff=64,
        num_experts=2,
        num_experts_per_tok=1,
        vocab_size=128,
        num_codebooks=2,
    )
    session_a = model.new_runtime_state()
    session_b = model.new_runtime_state()

    model.load_runtime_state(session_a)
    model.temporal_grid.create_cell(timestamp_ms=0)
    model._current_silence_ms = 400
    model.save_runtime_state(session_a)

    model.load_runtime_state(session_b)
    assert model.temporal_grid._current_cell_id == 0
    assert model.temporal_grid._cells == {}
    assert model._current_silence_ms == 0

    model.temporal_grid.create_cell(timestamp_ms=0)
    model.save_runtime_state(session_b)
    assert session_a["cells"] is not session_b["cells"]
    assert session_a["current_cell_id"] == session_b["current_cell_id"] == 1
