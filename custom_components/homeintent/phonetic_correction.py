"""Bounded ASR-error correction that always asks before executing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entities import EntitySnapshot, normalize_for_compare
from .nlu.device_ontology import analyse_word, entity_genera
from .name_similarity import bounded_name_similarity


@dataclass(frozen=True)
class PhoneticSuggestion:
    corrected_text: str
    entity: EntitySnapshot
    corrected_term: str


def phonetic_suggestions(
    text: str, entities: list[EntitySnapshot]
) -> tuple[PhoneticSuggestion, ...]:
    """Replace one likely misheard entity-name word, never command words."""
    words = list(re.finditer(r"[A-Za-zÄÖÜäöüß]{4,}", text))
    suggestions: dict[tuple[str, str], PhoneticSuggestion] = {}
    for entity in entities:
        candidate_words: set[str] = set()
        for name in (entity.friendly_name, *entity.aliases):
            candidate_words.update(
                word for word in normalize_for_compare(name).split() if len(word) >= 4
            )
        genera = entity_genera(entity)
        for match in words:
            spoken = normalize_for_compare(match.group())
            spoken_kind = analyse_word(spoken)
            if spoken_kind is not None and not set(spoken_kind.genera) & genera:
                # A correction never crosses kinds of devices: a spoken
                # "Wassermelder" is not a misheard "Bewegungsmelder" (S7).
                continue
            for candidate in candidate_words:
                if spoken == candidate:
                    continue
                similarity = bounded_name_similarity(spoken, candidate)
                if not similarity.accepted:
                    continue
                corrected = text[:match.start()] + candidate + text[match.end():]
                suggestions[(corrected.casefold(), entity.entity_id)] = PhoneticSuggestion(
                    corrected, entity, candidate
                )
    return tuple(suggestions.values())[:16]
