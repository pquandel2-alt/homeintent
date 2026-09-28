"""Explicit, confirmed and locally persisted spoken entity aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .customization import parse_custom_aliases
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.entity_resolution import ResolutionStatus, resolve_phrase


@dataclass(frozen=True)
class AliasLearningDraft:
    alias: str
    entity_id: str
    entity_name: str
    area_id: str | None = None


_PATTERNS = (
    re.compile(r"^\s*mit\s+(?P<alias>.+?)\s+meine\s+ich\s+(?P<target>.+?)\s*[.!?]*$", re.I),
    re.compile(r"^\s*nenn(?:e)?\s+(?P<target>.+?)\s+(?:kuenftig|künftig|ab\s+jetzt)\s+(?P<alias>.+?)\s*[.!?]*$", re.I),
    re.compile(r"^\s*(?P<alias>.+?)\s+(?:bedeutet|ist\s+mein\s+name\s+fuer|ist\s+mein\s+name\s+für)\s+(?P<target>.+?)\s*[.!?]*$", re.I),
)


def parse_alias_learning(
    text: str, entities: list[EntitySnapshot]
) -> AliasLearningDraft | None:
    """Parse only explicit teaching language with one existing target."""
    match = next((match for pattern in _PATTERNS if (match := pattern.match(text))), None)
    if match is None:
        return None
    alias = match.group("alias").strip(" \"„“'.!?")
    target = match.group("target").strip(" \"„“'.!?")
    area_id, target = _contextual_target(target, entities)
    if (
        len(alias) < 2 or len(alias) > 80 or "=" in alias or "\n" in alias
        or normalize_for_compare(alias) == normalize_for_compare(target)
    ):
        return None
    candidates = (
        [item for item in entities if item.area_id == area_id]
        if area_id is not None else entities
    )
    resolved = resolve_phrase(target, candidates)
    if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
        return None
    # Never shadow another selected entity's canonical name or alias.
    if area_id is None:
        collision = resolve_phrase(alias, entities)
        if (
            collision.status is ResolutionStatus.RESOLVED
            and collision.entity is not None
            and collision.entity.entity_id != resolved.entity.entity_id
        ) or collision.status is ResolutionStatus.AMBIGUOUS:
            return None
    return AliasLearningDraft(
        alias, resolved.entity.entity_id, resolved.entity.friendly_name, area_id
    )


def _contextual_target(
    target: str, entities: list[EntitySnapshot]
) -> tuple[str | None, str]:
    """Extract an explicit area qualifier without guessing from name scores."""
    normalized = normalize_for_compare(target)
    areas = sorted(
        {
            (item.area_id, item.area_name)
            for item in entities
            if item.area_id is not None and item.area_name is not None
        },
        key=lambda item: len(item[1] or ""),
        reverse=True,
    )
    for area_id, area_name in areas:
        assert area_id is not None and area_name is not None
        area = normalize_for_compare(area_name)
        prefix = next(
            (
                candidate
                for candidate in (f"im {area}", f"in der {area}", f"in {area}")
                if normalized.startswith(candidate + " ")
            ),
            None,
        )
        if prefix is None:
            continue
        remainder = normalized[len(prefix):].strip()
        remainder = re.sub(r"^(?:normalerweise|meistens|standardmaessig|standardmäßig)\s+", "", remainder)
        remainder = re.sub(r"^(?:der|die|das|den)\s+", "", remainder)
        return area_id, remainder
    return None, target


def append_alias_rule(existing: object, draft: AliasLearningDraft) -> str:
    """Append one validated rule idempotently; reject cross-entity conflicts."""
    current = str(existing or "").strip()
    parsed = parse_custom_aliases(current)
    alias_key = draft.alias.casefold()
    for entity_id, aliases in parsed.items():
        if any(alias.casefold() == alias_key for alias in aliases):
            if entity_id != draft.entity_id:
                raise ValueError(f"Der Alias „{draft.alias}“ gehört bereits zu {entity_id}.")
            return current
    line = f"{draft.alias} = {draft.entity_id}"
    return f"{current}\n{line}" if current else line


def remove_alias_rule(existing: object, *, alias: str, entity_id: str) -> str:
    """Remove exactly one confirmed global alias while preserving other lines."""
    if not isinstance(existing, str):
        return ""
    retained: list[str] = []
    for raw_line in existing.splitlines():
        line = raw_line.strip()
        if "=" in line:
            candidate, target = (part.strip() for part in line.split("=", 1))
            if candidate.casefold() == alias.casefold() and target == entity_id:
                continue
        retained.append(raw_line)
    return "\n".join(retained).strip()
