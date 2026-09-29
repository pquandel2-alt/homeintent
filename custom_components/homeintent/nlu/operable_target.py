"""The device before the room or the contact (7.8 B6).

"Mach die Garage auf", "Mach die Haustür zu": the spoken object is a room
("Garage") or a read-only contact ("Haustür"), but the operation fits only
one controllable device of the same meaning - the garage door in that room,
the lock whose name continues the contact's name. That device is chosen;
critical devices keep their confirmation through the ordinary policy.
Anything else stays as it is (and is answered or asked back as before).

"auf/zu" on a lock means unlock/lock; the result is an explicit imperative
("Schließe Haustürschloss auf") for the ordinary pipeline.
"""

from __future__ import annotations

from typing import Iterable

from ..entities import EntitySnapshot, normalize_for_compare

__all__ = ("choose_operable_target",)

_OPERABLE = frozenset({"cover", "lock", "valve"})
_READ_ONLY = frozenset({"binary_sensor", "sensor"})
_OPEN = frozenset({"auf", "oeffne", "oeffnen", "aufmachen", "aufsperren"})
_CLOSE = frozenset({"zu", "schliesse", "schliess", "schliessen", "zumachen", "zusperren"})
_QUESTION = frozenset({"ist", "sind", "wie", "was", "wer", "wo", "ob", "welche", "welcher", "welches"})


def _imperative(entity: EntitySnapshot, opening: bool) -> str:
    if entity.domain == "lock":
        return f"Schließe {entity.friendly_name} {'auf' if opening else 'ab'}."
    return f"{'Öffne' if opening else 'Schließe'} {entity.friendly_name}."


def choose_operable_target(text: str, entities: Iterable[EntitySnapshot]) -> str | None:
    """An explicit command on the one operable device, else ``None``."""
    from .self_correction import utterance_fields

    if text.rstrip().endswith("?"):
        return None
    words = [normalize_for_compare(word.strip(".,!?")) for word in text.split()]
    if not words or words[0] in _QUESTION:
        return None
    opening = bool(set(words) & _OPEN)
    closing = bool(set(words) & _CLOSE)
    if opening == closing:
        return None
    entity_list = entities if isinstance(entities, list) else list(entities)
    fields, rest = utterance_fields(text, entity_list)
    if rest or any(item.kind in {"value", "time"} for item in fields):
        return None
    targets = [item for item in fields if item.kind == "target"]
    places = [item for item in fields if item.kind == "place"]
    by_id = {entity.entity_id: entity for entity in entity_list}
    candidates: list[EntitySnapshot] = []
    if len(targets) == 1 and targets[0].registry_ids:
        named = [by_id[entity_id] for entity_id in targets[0].registry_ids if entity_id in by_id]
        if named and all(entity.domain in _READ_ONLY for entity in named):
            # "Haustür" is a contact; "Haustürschloss" continues its name.
            stems = {normalize_for_compare(entity.friendly_name).replace(" ", "") for entity in named}
            candidates = [
                entity for entity in entity_list
                if entity.domain in _OPERABLE
                and any(stem and stem in normalize_for_compare(entity.friendly_name).replace(" ", "") for stem in stems)
            ]
        elif len(named) == 1 and named[0].domain == "lock":
            # "Mach das Haustürschloss auf": auf/zu on a lock.
            candidates = named
    elif not targets and len(places) == 1 and not places[0].text.split()[0].casefold() in {
        "im", "in", "am", "an", "beim", "bei", "auf", "vom", "von", "zum", "zur",
    }:
        # "die Garage" as object: the one operable device of that room.
        place = places[0].place
        candidates = [
            entity for entity in entity_list
            if entity.domain in _OPERABLE and place is not None and place.contains(entity)
        ]
    if len(candidates) != 1:
        return None
    return _imperative(candidates[0], opening)
