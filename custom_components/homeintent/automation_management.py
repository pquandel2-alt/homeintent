"""Deterministic parsing and read-only resolution for automation management."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

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


_RESCHEDULE_RE = re.compile(
    r"\bverschieb\w*\s+(?:den|die)\s+(?:auftrag|automation)"
    r"(?:\s+für\s+(?P<name>.+?))?\s+auf\s+(?P<hour>\d{1,2})"
    r"(?::(?P<minute>\d{1,2}))?\s*uhr\b",
    re.IGNORECASE,
)
_WHEN_RE = re.compile(
    r"\bwann\s+wird\s+(?P<name>.+?)\s+"
    r"(?:gefahren|geschaltet|eingeschaltet|ausgeschaltet|gestartet|ausgeführt)\b",
    re.IGNORECASE,
)
_EXPLAIN_RE = re.compile(
    r"\b(?:was\s+passiert|welche\s+automationen?\s+(?:reagier\w*|start\w*))\s*,?\s*"
    r"(?:wenn\s+|sobald\s+)?(?P<name>.+?)(?:\s+(?:geöffnet|geoeffnet|geschlossen|an|aus|"
    r"erkannt|ausgelöst|ausgeloest|gemeldet|eingeschaltet|ausgeschaltet|aktiv)\s+wird)?$",
    re.IGNORECASE,
)
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
_CONTROLS_RE = re.compile(
    r"\b(?:welche\s+automation\s+(?:steuert|schaltet)|was\s+steuert)\s+(?P<name>.+)$",
    re.IGNORECASE,
)
_SET_MAX_RUNS_RE = re.compile(
    r"\bwiederhol\w*\s+(?:die\s+)?automation"
    r"(?:\s+für\s+(?P<name>.+?))?\s+nur\s+"
    r"(?P<count>zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|[2-9]|10)\s*mal\b",
    re.IGNORECASE,
)
_DETAIL_RE = re.compile(
    r"\b(?:zeig\w*|erklaer\w*|erklär\w*)\s+(?:mir\s+)?(?:die\s+)?"
    r"(?:details(?:\s+der\s+automation)?|automation)\s+"
    r"(?:von|fuer|für|zu)\s+(?P<name>.+)$",
    re.I,
)
_RUN_COUNTS = {
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
}


def parse_automation_management(text: str) -> AutomationManagementRequest | None:
    """Recognize the bounded management vocabulary without fuzzy matching."""
    normalized = re.sub(r"[?.!]", "", text).strip()
    simulation = re.search(
        r"\b(?:simulier\w*|was\s+würde)\s+(?:die\s+)?automation"
        r"(?:\s+(?:von|für|fuer|zu))?\s+(?P<name>.+?)(?:\s+jetzt\s+tun)?$",
        normalized, re.IGNORECASE,
    )
    if simulation is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.SIMULATE,
            entity_name=simulation.group("name").strip(),
        )
    duplicate = re.search(
        r"\b(?:duplizier\w*|kopier\w*)\s+(?:die\s+)?automation(?:\s+(?:von|für|fuer|zu)\s+)?(?P<name>.+)$",
        normalized, re.IGNORECASE,
    )
    if duplicate is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.DUPLICATE,
            entity_name=duplicate.group("name").strip(),
        )
    diagnose = re.search(
        r"\bwarum\s+wurde\s+(?:die\s+)?automation(?:\s+(?:von|für|fuer|zu)\s+)?(?P<name>.+?)\s+"
        r"nicht\s+(?:ausgelöst|ausgeloest|ausgeführt|ausgefuehrt)\b",
        normalized, re.IGNORECASE,
    )
    if diagnose is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.DIAGNOSE,
            entity_name=diagnose.group("name").strip(),
        )
    pause = re.search(
        r"\bpausier\w*\s+(?:die\s+)?automation(?:\s+(?:von|für|fuer|zu)\s+(?P<name>.+?))?\s+"
        r"bis\s+(?:(?P<day>morgen|übermorgen|uebermorgen)\s+)?"
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{1,2}))?\s*(?:uhr)?\b",
        normalized, re.IGNORECASE,
    )
    if pause is not None:
        hour, minute = int(pause.group("hour")), int(pause.group("minute") or 0)
        if hour > 23 or minute > 59:
            return None
        day = (pause.group("day") or "").casefold()
        return AutomationManagementRequest(
            AutomationManagementKind.PAUSE_UNTIL,
            entity_name=pause.group("name"), hour=hour, minute=minute,
            day_offset=2 if day in {"übermorgen", "uebermorgen"} else 1 if day else 0,
        )
    count_match = re.search(
        r"\bwie\s+viele\s+homeintent[- ]automationen\s+sind\s+"
        r"(?P<state>aktiv|eingeschaltet|deaktiviert|ausgeschaltet)\b",
        normalized,
        re.IGNORECASE,
    )
    if count_match is not None:
        kind = (
            AutomationManagementKind.COUNT_ACTIVE
            if count_match.group("state").casefold() in {"aktiv", "eingeschaltet"}
            else AutomationManagementKind.COUNT_DISABLED
        )
        return AutomationManagementRequest(kind)
    if re.search(r"\blösch\w*\s+alle\s+abgelaufenen\b", normalized, re.IGNORECASE):
        return AutomationManagementRequest(AutomationManagementKind.CLEAN_EXPIRED)
    if re.search(
        r"\b(?:mach\w*|nimm\w*|setz\w*)\b.*\b(?:letzte|letzten)\b.*"
        r"\b(?:homeintent[- ])?automations?(?:aenderung|änderung)\b.*"
        r"\b(?:rueckgaengig|rückgängig|zurueck|zurück)\b",
        normalized,
        re.IGNORECASE,
    ):
        return AutomationManagementRequest(AutomationManagementKind.ROLLBACK)
    match = _SET_MAX_RUNS_RE.search(normalized)
    if match is not None:
        raw_count = match.group("count").casefold()
        return AutomationManagementRequest(
            AutomationManagementKind.SET_MAX_RUNS,
            entity_name=match.group("name"),
            max_runs=int(raw_count) if raw_count.isdigit() else _RUN_COUNTS[raw_count],
        )
    match = _RESCHEDULE_RE.search(normalized)
    if match is not None:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return AutomationManagementRequest(
                AutomationManagementKind.RESCHEDULE,
                entity_name=match.group("name"),
                hour=hour,
                minute=minute,
            )
        return None
    match = _WHEN_RE.search(normalized)
    if match is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.WHEN,
            entity_name=match.group("name"),
        )
    match = _CONTROLS_RE.search(normalized)
    if match is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.CONTROLS_ENTITY,
            entity_name=match.group("name"),
        )
    match = _DETAIL_RE.search(normalized)
    if match is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.DETAIL, entity_name=match.group("name")
        )
    match = _EXPLAIN_RE.search(normalized)
    if match is not None:
        return AutomationManagementRequest(
            AutomationManagementKind.EXPLAIN_TRIGGER,
            entity_name=match.group("name"),
        )
    if re.search(
        r"\b(?:welche|zeige)\b.*\bhomeintent[- ]automationen\b",
        normalized,
        re.IGNORECASE,
    ) or re.search(
        r"\bzeige\s+nur\s+homeintent\s+automationen\b",
        normalized,
        re.IGNORECASE,
    ):
        scope = re.search(r"\b(?:im|in der|in den)\s+(.+)$", normalized, re.IGNORECASE)
        return AutomationManagementRequest(
            AutomationManagementKind.LIST_HOMEINTENT,
            scope_name=scope.group(1).strip() if scope else None,
        )
    if re.search(
        r"\bwelche\s+(?:einmaligen\s+)?(?:aufträge|automationen)\s+sind\s+(?:noch\s+)?geplant\b",
        normalized,
        re.IGNORECASE,
    ):
        return AutomationManagementRequest(AutomationManagementKind.LIST_SCHEDULED)
    return None


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
