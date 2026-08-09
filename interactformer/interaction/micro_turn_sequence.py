"""Time-aligned, interleaved micro-turn training sequences.

An interaction-native model must observe its own output before processing the
next input slice. A conventional ``all inputs -> all targets`` batch loses
that causal ordering. This module builds the chronological layout described by
Thinking Machines Lab::

    time_0, input_0, output_0, time_1, input_1, output_1, ...

Token ids remain in their native text/audio/video namespaces. ``stream_ids``
tell the model which embedding/codebook owns each id, while ``loss_mask``
supervises only assistant output tokens. A learned time-cell token keeps silent
200 ms cells in the sequence, which is necessary for timing and interruption.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence


class StreamKind(IntEnum):
    """Token namespace and causal role within one micro-turn."""

    TIME = 0
    INPUT_AUDIO = 1
    INPUT_VIDEO = 2
    INPUT_TEXT = 3
    OUTPUT_TEXT = 4
    OUTPUT_AUDIO = 5

    @property
    def is_output(self) -> bool:
        return self in (StreamKind.OUTPUT_TEXT, StreamKind.OUTPUT_AUDIO)


@dataclass(frozen=True)
class MicroTurn:
    """Native-modality token chunks aligned to one time cell."""

    cell_id: int
    input_audio: tuple[int, ...] = ()
    input_video: tuple[int, ...] = ()
    input_text: tuple[int, ...] = ()
    output_text: tuple[int, ...] = ()
    output_audio: tuple[int, ...] = ()


@dataclass(frozen=True)
class BlockSpan:
    """Half-open position range occupied by one modality block."""

    cell_id: int
    stream: StreamKind
    start: int
    end: int

    @property
    def supervised(self) -> bool:
        return self.stream.is_output


@dataclass(frozen=True)
class InterleavedSequence:
    """Flattened chronological sequence ready for multimodal collation."""

    token_ids: tuple[int, ...]
    stream_ids: tuple[int, ...]
    cell_ids: tuple[int, ...]
    loss_mask: tuple[bool, ...]
    blocks: tuple[BlockSpan, ...]

    def __post_init__(self) -> None:
        length = len(self.token_ids)
        if not (
            len(self.stream_ids)
            == len(self.cell_ids)
            == len(self.loss_mask)
            == length
        ):
            raise ValueError("interleaved sequence fields must have equal lengths")


class MicroTurnInterleaver:
    """Build a causal micro-turn sequence without mixing token namespaces."""

    _STREAM_FIELDS = (
        (StreamKind.INPUT_AUDIO, "input_audio"),
        (StreamKind.INPUT_VIDEO, "input_video"),
        (StreamKind.INPUT_TEXT, "input_text"),
        (StreamKind.OUTPUT_TEXT, "output_text"),
        (StreamKind.OUTPUT_AUDIO, "output_audio"),
    )

    def __init__(self, time_cell_token_id: int):
        _validate_tokens((time_cell_token_id,), "time_cell_token_id")
        self.time_cell_token_id = time_cell_token_id

    def build(self, turns: Iterable[MicroTurn]) -> InterleavedSequence:
        token_ids: list[int] = []
        stream_ids: list[int] = []
        cell_ids: list[int] = []
        loss_mask: list[bool] = []
        blocks: list[BlockSpan] = []
        previous_cell_id: int | None = None

        for turn in turns:
            if not isinstance(turn.cell_id, int) or isinstance(turn.cell_id, bool):
                raise TypeError("cell_id must be an integer")
            if turn.cell_id < 0:
                raise ValueError("cell_id must be non-negative")
            if previous_cell_id is not None and turn.cell_id <= previous_cell_id:
                raise ValueError("micro-turn cell_ids must be strictly increasing")
            previous_cell_id = turn.cell_id

            self._append_block(
                token_ids,
                stream_ids,
                cell_ids,
                loss_mask,
                blocks,
                turn.cell_id,
                StreamKind.TIME,
                (self.time_cell_token_id,),
            )
            for stream, field_name in self._STREAM_FIELDS:
                values = getattr(turn, field_name)
                _validate_tokens(values, field_name)
                if values:
                    self._append_block(
                        token_ids,
                        stream_ids,
                        cell_ids,
                        loss_mask,
                        blocks,
                        turn.cell_id,
                        stream,
                        values,
                    )

        return InterleavedSequence(
            token_ids=tuple(token_ids),
            stream_ids=tuple(stream_ids),
            cell_ids=tuple(cell_ids),
            loss_mask=tuple(loss_mask),
            blocks=tuple(blocks),
        )

    @staticmethod
    def _append_block(
        token_ids: list[int],
        stream_ids: list[int],
        cell_ids: list[int],
        loss_mask: list[bool],
        blocks: list[BlockSpan],
        cell_id: int,
        stream: StreamKind,
        values: Sequence[int],
    ) -> None:
        start = len(token_ids)
        token_ids.extend(values)
        stream_ids.extend([int(stream)] * len(values))
        cell_ids.extend([cell_id] * len(values))
        loss_mask.extend([stream.is_output] * len(values))
        blocks.append(BlockSpan(cell_id, stream, start, len(token_ids)))


def _validate_tokens(values: Sequence[int], name: str) -> None:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of non-negative integers")
    for token in values:
        if not isinstance(token, int) or isinstance(token, bool):
            raise TypeError(f"{name} must contain only integers")
        if token < 0:
            raise ValueError(f"{name} must contain only non-negative integers")
