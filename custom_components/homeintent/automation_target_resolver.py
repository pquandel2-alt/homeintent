"""Shared target resolution for automation triggers and conditions."""

from __future__ import annotations

from .areas import AreaResolutionStatus, resolve_area_scored
from .nlu.entity_resolution import ResolutionStatus, resolve_phrase
from .nlu.automation_model import TriggerTarget
from .nlu.constraint_resolver import Constraints, resolve_candidates
from .nlu.parser import ParseContext
from .parsers import strip_locative_prepositions


_PRONOUN_WORDS = frozenset({"es"})


def resolve_automation_area(
    slots: dict, context: ParseContext
) -> tuple[str | None, str | None] | None:
    area_slot = slots.get("area")
    if area_slot is None:
        return None, None
    area_text = strip_locative_prepositions(str(area_slot.value))
    if not area_text:
        return None, None
    resolved = resolve_area_scored(area_text, context.entities)
    if resolved.status is not AreaResolutionStatus.RESOLVED or resolved.area is None:
        return None
    return resolved.area.area_id, resolved.area.name


def build_device_class_target(
    device_class_value: str, slots: dict, context: ParseContext
) -> TriggerTarget | None:
    resolved_area = resolve_automation_area(slots, context)
    if resolved_area is None:
        return None
    area_id, _area_name = resolved_area
    domain, _, raw_device_class = device_class_value.partition(":")
    device_class = raw_device_class if raw_device_class != "None" else None
    return TriggerTarget(domain=domain, device_class=device_class, area_id=area_id)


def build_pronoun_target(context: ParseContext) -> TriggerTarget | None:
    """Resolve ``es`` from the established per-conversation entity focus."""
    last_entities = context.last_entities
    if not last_entities:
        return None
    if len(last_entities) == 1:
        entity = last_entities[0]
        candidates = resolve_candidates(
            context.entities,
            Constraints(
                domain=entity.domain,
                device_class=entity.device_class,
                area_id=entity.area_id,
            ),
        )
        if len(candidates) == 1:
            return TriggerTarget(
                domain=entity.domain,
                device_class=entity.device_class,
                area_id=entity.area_id,
            )
        return TriggerTarget(
            domain=entity.domain,
            device_class=entity.device_class,
            area_id=entity.area_id,
            entity_id=entity.entity_id,
        )

    domains = {entity.domain for entity in last_entities}
    device_classes = {entity.device_class for entity in last_entities}
    area_ids = {entity.area_id for entity in last_entities}
    if len(domains) != 1 or len(device_classes) != 1 or len(area_ids) != 1:
        return None
    return TriggerTarget(
        domain=next(iter(domains)),
        device_class=next(iter(device_classes)),
        area_id=next(iter(area_ids)),
    )


def build_named_target(name_slot, context: ParseContext) -> TriggerTarget | None:
    """Resolve one spoken target, retaining entity_id only when necessary."""
    name = strip_locative_prepositions(str(name_slot.value))
    if name.strip().casefold() in _PRONOUN_WORDS:
        return build_pronoun_target(context)
    resolved = resolve_phrase(name, context.entities, index=context.index)
    if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
        return None
    entity = resolved.entity
    candidates = resolve_candidates(
        context.entities,
        Constraints(
            domain=entity.domain,
            device_class=entity.device_class,
            area_id=entity.area_id,
        ),
    )
    if len(candidates) == 1:
        return TriggerTarget(
            domain=entity.domain,
            device_class=entity.device_class,
            area_id=entity.area_id,
        )
    return TriggerTarget(
        domain=entity.domain,
        device_class=entity.device_class,
        area_id=entity.area_id,
        entity_id=entity.entity_id,
    )
