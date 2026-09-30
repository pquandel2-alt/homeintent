"""The shared surface preparation of the language frontend (7.7.1/7.8).

One place turns the spoken sentence into the surface every reader of the
turn sees - conversation, arbiter and shadow tools alike:

1. self correction (retraction + replacement, abort, unclear: A1),
2. politeness, thanks, reasons and urgency as frames (B2),
3. verbless short commands and verbless automation actions, values without
   a unit, the kind word before a group name (B3, B5, B7),
4. the operable device before a room or a contact (B6),
5. coordination: hyphen ellipsis, shared head, one place for all parts (A4).

Each step is a rule over the lexicon, the place lexicon, the device ontology
and registry names. Nothing here resolves a target for execution or decides
about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot
from .coordination import expand_coordination
from .operable_target import choose_operable_target
from .pragmatic_frames import PragmaticFrames, strip_pragmatic_frames
from .self_correction import CorrectionKind, SelfCorrection, analyse_self_correction
from .short_commands import (
    complete_value_unit,
    drop_group_kind_word,
    expand_short_command,
    expand_verbless_action,
)

__all__ = ("PreparedSurface", "prepare_surface")


@dataclass(frozen=True)
class PreparedSurface:
    text: str
    correction: SelfCorrection
    frames: PragmaticFrames

    @property
    def stops(self) -> bool:
        """An abort or an unclear correction: nothing may run this turn."""
        return self.correction.kind in {CorrectionKind.CANCELLED, CorrectionKind.AMBIGUOUS}


def prepare_surface(text: str, entities: Sequence[EntitySnapshot]) -> PreparedSurface:
    entity_list = entities if isinstance(entities, list) else list(entities)
    correction = analyse_self_correction(text, entity_list)
    if correction.kind in {CorrectionKind.CANCELLED, CorrectionKind.AMBIGUOUS}:
        return PreparedSurface(text, correction, PragmaticFrames(text))
    if correction.kind is CorrectionKind.REPLACED:
        text = correction.text
    frames = strip_pragmatic_frames(text, entity_list)
    if frames.found and frames.text:
        text = frames.text
    short = expand_short_command(text, entity_list) or expand_verbless_action(text, entity_list)
    text = short if short is not None else complete_value_unit(text, entity_list)
    text = drop_group_kind_word(text, entity_list)
    operable = choose_operable_target(text, entity_list)
    if operable is not None:
        text = operable
    text = expand_coordination(text, entity_list)
    return PreparedSurface(text, correction, frames)
