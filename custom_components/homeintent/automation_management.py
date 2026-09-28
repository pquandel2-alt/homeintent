"""Deterministic parsing and read-only resolution for automation management."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import cast

from .automation_summary import AutomationSummary, CREATED_BY_HOMEINTENT
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.entity_resolution import ResolveStatus, resolve_entity


class AutomationManagementKind(Enum):
    LIST_HOMEINTENT = auto()
    LIST_SCHEDULED = auto()
    WHEN = auto()
    RESCHEDULE = auto()
    CLEAN_EXPIRED = auto()
    SET_MAX_RUNS = auto()
    COUNT_ACTIVE = auto()
    COUNT_DISABLED = auto()
    EXPLAIN_TRIGGER = auto()
    CONTROLS_ENTITY = auto()
    DETAIL = auto()
    ROLLBACK = auto()
    DUPLICATE = auto()
    PAUSE_UNTIL = auto()
    DIAGNOSE = auto()
    SIMULATE = auto()



# Management requests that only read automations: answered as a question
# (QUERY_ANSWER) for clients and satellites, never as an action (F22).
READ_ONLY_MANAGEMENT_KINDS = frozenset({
    AutomationManagementKind.LIST_HOMEINTENT,
    AutomationManagementKind.LIST_SCHEDULED,
    AutomationManagementKind.WHEN,
    AutomationManagementKind.COUNT_ACTIVE,
    AutomationManagementKind.COUNT_DISABLED,
    AutomationManagementKind.EXPLAIN_TRIGGER,
    AutomationManagementKind.CONTROLS_ENTITY,
    AutomationManagementKind.DETAIL,
    AutomationManagementKind.DIAGNOSE,
    AutomationManagementKind.SIMULATE,
})

@dataclass(frozen=True)
class AutomationManagementRequest:
    kind: AutomationManagementKind
    entity_name: str | None = None
    hour: int | None = None
    minute: int = 0
    max_runs: int | None = None
    scope_name: str | None = None
    day_offset: int = 0


@dataclass(frozen=True)
class AutomationManagementSelection:
    request: AutomationManagementRequest
    automations: tuple[AutomationSummary, ...]
    entity: EntitySnapshot | None = None
    error_text: str | None = None


# Spoken trigger subject -> binary sensor device classes, for "wenn die
# Bewegung im Flur erkannt wird" where no entity is named (F9).
_SUBJECT_CLASSES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"\b(?:bewegung|bewegungsmelder)\b"), frozenset({"motion", "occupancy", "presence"})),
    (re.compile(r"\b(?:praesenz|anwesenheit)\b"), frozenset({"occupancy", "presence", "motion"})),
    (re.compile(r"\bfenster\b"), frozenset({"window"})),
    (re.compile(r"\b(?:tuer|haustuer)\b"), frozenset({"door"})),
    (re.compile(r"\brauch\w*\b"), frozenset({"smoke"})),
    (re.compile(r"\b(?:wasser|leck)\w*\b"), frozenset({"moisture"})),
)


def _subject_by_class_and_area(
    spoken_name: str, entities: list[EntitySnapshot]
) -> EntitySnapshot | None:
    """Resolve "die Bewegung im Flur" to the unique motion sensor there."""
    value = normalize_for_compare(spoken_name)
    classes = next(
        (device_classes for pattern, device_classes in _SUBJECT_CLASSES if pattern.search(value)),
        None,
    )
    if classes is None:
        return None
    candidates = [
        entity for entity in entities
        if entity.domain == "binary_sensor" and entity.device_class in classes
    ]
    located = [
        entity for entity in candidates
        if any(
            name and re.search(rf"\b{re.escape(normalize_for_compare(name))}\b", value)
            for name in (entity.area_name, *entity.area_aliases)
        )
    ]
    pool = located or (candidates if len(candidates) == 1 else [])
    # Prefer the most specific area name ("Flur Obergeschoss" over "Flur").
    if len(pool) > 1:
        longest = max(len(entity.area_name or "") for entity in pool)
        pool = [entity for entity in pool if len(entity.area_name or "") == longest]
    return pool[0] if len(pool) == 1 else None


def parse_automation_management(text: str) -> AutomationManagementRequest | None:
    """Recognize the bounded management vocabulary without fuzzy matching.

    Since 7.5.2 the language island "Automationsverwaltung" derives the
    request from the frame table (``derive_automation_management``); the
    historic sentence patterns are gone.
    """
    return derive_automation_management(text)


def derive_automation_management(text: str) -> AutomationManagementRequest | None:
    """Language island "Automationsverwaltung" (7.5.2): the canonical
    request from the frame table ``nlu.management_frame``."""
    from .nlu.management_frame import management_frame

    found = management_frame(text)
    if found is None:
        return None
    kind, slots = found
    if slots.get("invalid"):
        return None
    if kind == "COUNT":
        return AutomationManagementRequest(
            AutomationManagementKind.COUNT_ACTIVE
            if slots["state"] == "active" else AutomationManagementKind.COUNT_DISABLED
        )
    return AutomationManagementRequest(
        AutomationManagementKind[kind],
        entity_name=slots.get("entity_name"),  # type: ignore[arg-type]
        hour=slots.get("hour"),  # type: ignore[arg-type]
        minute=int(cast(int, slots.get("minute", 0) or 0)),
        max_runs=slots.get("max_runs"),  # type: ignore[arg-type]
        scope_name=slots.get("scope_name"),  # type: ignore[arg-type]
        day_offset=int(cast(int, slots.get("day_offset", 0) or 0)),
    )


def select_automation_management(
    request: AutomationManagementRequest,
    entities: list[EntitySnapshot],
    automations: tuple[AutomationSummary, ...],
    now: datetime | None = None,
) -> AutomationManagementSelection:
    """Apply HomeIntent/schedule/entity filters and preserve ambiguity."""
    homeintent = tuple(
        automation
        for automation in automations
        if automation.created_by == CREATED_BY_HOMEINTENT
    )
    scheduled = tuple(
        automation
        for automation in homeintent
        if automation.once and automation.scheduled_for is not None
        and (
            now is None
            or _comparable_target(automation.scheduled_for, now) > now
        )
    )
    if request.kind is AutomationManagementKind.LIST_HOMEINTENT:
        if request.scope_name:
            wanted = request.scope_name.casefold().replace(" ", "_")
            scoped_entity_ids = {
                entity.entity_id
                for entity in entities
                if any(
                    wanted in (value or "").casefold().replace(" ", "_")
                    for value in (
                        entity.area_id,
                        entity.area_name,
                        entity.floor_id,
                        entity.floor_name,
                    )
                )
            }
            homeintent = tuple(
                item for item in homeintent
                if item.referenced_entity_ids & scoped_entity_ids
            )
        return AutomationManagementSelection(request, homeintent)
    if request.kind in {
        AutomationManagementKind.COUNT_ACTIVE,
        AutomationManagementKind.COUNT_DISABLED,
    }:
        enabled = request.kind is AutomationManagementKind.COUNT_ACTIVE
        return AutomationManagementSelection(
            request, tuple(item for item in homeintent if item.enabled is enabled)
        )
    if request.kind in (
        AutomationManagementKind.LIST_SCHEDULED,
        AutomationManagementKind.CLEAN_EXPIRED,
    ):
        return AutomationManagementSelection(request, scheduled)

    candidates = (
        homeintent
        if request.kind in {
            AutomationManagementKind.DETAIL, AutomationManagementKind.DUPLICATE,
            AutomationManagementKind.PAUSE_UNTIL,
            AutomationManagementKind.DIAGNOSE,
            AutomationManagementKind.SIMULATE,
        }
        else tuple(automation for automation in homeintent if not automation.once)
        if request.kind is AutomationManagementKind.SET_MAX_RUNS
        else scheduled
    )
    if request.kind in {
        AutomationManagementKind.EXPLAIN_TRIGGER,
        AutomationManagementKind.CONTROLS_ENTITY,
    }:
        # Explaining is read-only: user-made automations count too (F9).
        homeintent = automations
    if request.entity_name:
        spoken_name = request.entity_name.strip()
        resolved = resolve_entity(spoken_name, entities)
        if (
            (resolved.status is not ResolveStatus.OK or resolved.entity is None)
            and request.kind is AutomationManagementKind.EXPLAIN_TRIGGER
        ):
            subject = _subject_by_class_and_area(spoken_name, entities)
            if subject is not None:
                return AutomationManagementSelection(
                    request,
                    tuple(item for item in automations if subject.entity_id in item.trigger_entity_ids),
                    entity=subject,
                )
        if resolved.status is not ResolveStatus.OK or resolved.entity is None:
            wanted = normalize_for_compare(spoken_name)
            by_identity = tuple(
                automation
                for automation in candidates
                if wanted in normalize_for_compare(automation.alias)
                or (
                    automation.source_text is not None
                    and wanted in normalize_for_compare(automation.source_text)
                )
            )
            if by_identity:
                return AutomationManagementSelection(request, by_identity)
            scoped_ids = {
                entity.entity_id
                for entity in entities
                if any(
                    wanted == normalize_for_compare(label or "")
                    for label in (
                        entity.area_name,
                        entity.area_id,
                        entity.floor_name,
                        entity.floor_id,
                    )
                )
            }
            if scoped_ids:
                return AutomationManagementSelection(
                    request,
                    tuple(
                        automation
                        for automation in candidates
                        if automation.referenced_entity_ids & scoped_ids
                    ),
                )
            described_ids = _described_entity_ids(spoken_name, entities)
            if described_ids:
                # "die Büro Rolllade": a kind at a place, not a name.
                return AutomationManagementSelection(
                    request,
                    tuple(
                        automation
                        for automation in candidates
                        if automation.referenced_entity_ids & described_ids
                    ),
                )
            return AutomationManagementSelection(
                request,
                (),
                error_text=(
                    "Das Gerät ist nicht eindeutig. Bitte nenne den vollständigen Gerätenamen."
                    if resolved.status is ResolveStatus.AMBIGUOUS
                    else "Ich habe das genannte Gerät nicht gefunden."
                ),
            )
        if request.kind is AutomationManagementKind.DETAIL:
            candidates = homeintent
            reference_field = "referenced_entity_ids"
        elif request.kind is AutomationManagementKind.EXPLAIN_TRIGGER:
            candidates = homeintent
            reference_field = "trigger_entity_ids"
        elif request.kind is AutomationManagementKind.CONTROLS_ENTITY:
            candidates = homeintent
            reference_field = "action_entity_ids"
        else:
            reference_field = "referenced_entity_ids"
        matched = tuple(
            automation
            for automation in candidates
            if resolved.entity.entity_id in getattr(automation, reference_field)
        )
        return AutomationManagementSelection(request, matched, entity=resolved.entity)
    return AutomationManagementSelection(request, candidates)


def _comparable_target(value: str, now: datetime) -> datetime:
    target = datetime.fromisoformat(value)
    if target.tzinfo is None and now.tzinfo is not None:
        return target.replace(tzinfo=now.tzinfo)
    if target.tzinfo is not None and now.tzinfo is None:
        return target.replace(tzinfo=None)
    return target


def format_scheduled_time(value: str) -> str:
    target = datetime.fromisoformat(value)
    return target.strftime("%d.%m.%Y um %H:%M Uhr")


def _described_entity_ids(spoken: str, entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...]) -> set[str]:
    """Entities a kind (+ place) description names, e.g. "Büro Rolllade"."""
    from .nlu.language_frontend import tokenize_language
    from .nlu.target_resolution import ResolutionOutcome, describe_with_residue, resolve_description

    descriptions, residue = describe_with_residue(tokenize_language(spoken), list(entities))
    if residue or len(descriptions) != 1 or descriptions[0].explicit:
        return set()
    resolution = resolve_description(descriptions[0], list(entities))
    if resolution.outcome not in {ResolutionOutcome.RESOLVED, ResolutionOutcome.AMBIGUOUS}:
        return set()
    return {entity.entity_id for entity in resolution.entities}
