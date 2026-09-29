"""The shared surface preparation of the language frontend (7.7.1).

One place turns the spoken sentence into the surface every reader of the
turn sees - conversation, arbiter and shadow tools alike:

1. self correction (retraction + replacement, abort, unclear: A1),
2. coordination: hyphen ellipsis, shared head, one place for all parts (A4).

Each step is a rule over the lexicon, the place lexicon, the device ontology
and registry names. Nothing here resolves a target for execution or decides
about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot
from .coordination import expand_coordination
from .self_correction import CorrectionKind, SelfCorrection, analyse_self_correction

__all__ = ("PreparedSurface", "prepare_surface")


@dataclass(frozen=True)
class PreparedSurface:
    text: str
    correction: SelfCorrection

    @property
    def stops(self) -> bool:
        """An abort or an unclear correction: nothing may run this turn."""
        return self.correction.kind in {CorrectionKind.CANCELLED, CorrectionKind.AMBIGUOUS}


def prepare_surface(text: str, entities: Sequence[EntitySnapshot]) -> PreparedSurface:
    entity_list = list(entities)
    correction = analyse_self_correction(text, entity_list)
    if correction.kind in {CorrectionKind.CANCELLED, CorrectionKind.AMBIGUOUS}:
        return PreparedSurface(text, correction)
    if correction.kind is CorrectionKind.REPLACED:
        text = correction.text
    return PreparedSurface(expand_coordination(text, entity_list), correction)
