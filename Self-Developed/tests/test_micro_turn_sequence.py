"""Tests for TML-style chronological micro-turn layout."""

import pytest

from interactformer.interaction.micro_turn_sequence import (
    MicroTurn,
    MicroTurnInterleaver,
    StreamKind,
)


def test_interleaves_each_output_before_the_next_input():
    sequence = MicroTurnInterleaver(time_cell_token_id=999).build(
        [
            MicroTurn(cell_id=0, input_audio=(10, 11), output_audio=(20,)),
            MicroTurn(cell_id=1, input_text=(30,), output_text=(40, 41)),
        ]
    )

    assert sequence.token_ids == (999, 10, 11, 20, 999, 30, 40, 41)
    assert sequence.stream_ids == (
        StreamKind.TIME,
        StreamKind.INPUT_AUDIO,
        StreamKind.INPUT_AUDIO,
        StreamKind.OUTPUT_AUDIO,
        StreamKind.TIME,
        StreamKind.INPUT_TEXT,
        StreamKind.OUTPUT_TEXT,
        StreamKind.OUTPUT_TEXT,
    )
    assert sequence.cell_ids == (0, 0, 0, 0, 1, 1, 1, 1)
    assert sequence.loss_mask == (
        False,
        False,
        False,
        True,
        False,
        False,
        True,
        True,
    )


def test_silent_micro_turn_is_preserved_by_time_cell_token():
    sequence = MicroTurnInterleaver(time_cell_token_id=7).build(
        [MicroTurn(cell_id=4)]
    )

    assert sequence.token_ids == (7,)
    assert sequence.stream_ids == (StreamKind.TIME,)
    assert sequence.cell_ids == (4,)
    assert sequence.loss_mask == (False,)


def test_rejects_non_monotonic_cells_and_invalid_tokens():
    interleaver = MicroTurnInterleaver(time_cell_token_id=7)

    with pytest.raises(ValueError, match="strictly increasing"):
        interleaver.build([MicroTurn(cell_id=2), MicroTurn(cell_id=2)])
    with pytest.raises(ValueError, match="non-negative"):
        interleaver.build([MicroTurn(cell_id=0, output_text=(-1,))])
