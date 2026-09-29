"""Shared, order-independent entity/area/floor scope resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entities import EntitySnapshot, normalize_for_compare
from .nlu.domain_operations import DOMAIN_WORDS as SHARED_DOMAIN_WORDS


# This resolver belongs to the extended-device router. Core domains keep
# their established WorldModel resolver and must not be intercepted here.
_SCOPE_DOMAINS = frozenset({
    "humidifier", "water_heater", "select", "number", "input_number",
    "input_boolean", "valve", "lawn_mower", "vacuum", "media_player", "camera",
})
DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    domain: SHARED_DOMAIN_WORDS[domain] for domain in _SCOPE_DOMAINS
}


@dataclass(frozen=True)
class EntityScope:
    entities: tuple[EntitySnapshot, ...]
    kind: str
    label: str | None = None


def _contains(text: str, candidate: str | None) -> bool:
    normalized = normalize_for_compare(candidate or "")
    # Literal pre-check: the bounded pattern only runs when the name occurs.
    return bool(normalized) and normalized in text and re.search(
        rf"(?<!\w){re.escape(normalized)}(?!\w)", text
    ) is not None


def mentioned_domains(text: str, allowed: frozenset[str]) -> frozenset[str]:
    """Return domains explicitly named by a generic device noun."""
    normalized = normalize_for_compare(text)
    return frozenset(
        domain
        for domain in allowed
        if any(_contains(normalized, word) for word in DOMAIN_WORDS.get(domain, ()))
    )


def resolve_entity_scope(
    text: str,
    entities: list[EntitySnapshot],
    allowed_domains: frozenset[str],
) -> EntityScope | None:
    """Resolve one explicit entity or a homogeneous area/floor/all group.

    Meaning components are found independently, so word order is irrelevant.
    Ambiguous mixed-domain groups are rejected instead of guessed.
    """
    normalized = normalize_for_compare(text)
    candidates = [entity for entity in entities if entity.domain in allowed_domains]

    named: list[EntitySnapshot] = []
    for entity in candidates:
        if any(_contains(normalized, name) for name in (entity.friendly_name, *entity.aliases)):
            named.append(entity)
    if len(named) == 1:
        return EntityScope((named[0],), "entity", named[0].friendly_name)
    if len(named) > 1:
        return None

    domains = mentioned_domains(text, allowed_domains)
    if len(domains) != 1:
        return None
    domain = next(iter(domains))
    domain_entities = [entity for entity in candidates if entity.domain == domain]

    area_matches: dict[str, str] = {}
    for entity in domain_entities:
        if entity.area_id and (
            _contains(normalized, entity.area_name)
            or any(_contains(normalized, alias) for alias in entity.area_aliases)
        ):
            area_matches[entity.area_id] = entity.area_name or entity.area_id
    if len(area_matches) == 1:
        area_id, label = next(iter(area_matches.items()))
        scoped = tuple(entity for entity in domain_entities if entity.area_id == area_id)
        return EntityScope(scoped, "area", label) if scoped else None
    if len(area_matches) > 1:
        return None

    floor_matches: dict[str, str] = {}
    for entity in domain_entities:
        if entity.floor_id and _contains(normalized, entity.floor_name):
            floor_matches[entity.floor_id] = entity.floor_name or entity.floor_id
    if len(floor_matches) == 1:
        floor_id, label = next(iter(floor_matches.items()))
        scoped = tuple(entity for entity in domain_entities if entity.floor_id == floor_id)
        return EntityScope(scoped, "floor", label) if scoped else None
    if len(floor_matches) > 1:
        return None

    if re.search(r"\b(?:alle|saemtliche|gesamt)\w*\b", normalized):
        return EntityScope(tuple(domain_entities), "all", None) if domain_entities else None
    return None
