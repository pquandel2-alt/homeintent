"""Read-only interpretation of German finite-verb state questions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..entities import EntitySnapshot, normalize_for_compare, spoken_state
from ..floors import FloorResolveStatus, resolve_floor_by_level_keyword
from ..query_target import resolve_query_targets
from .semantic_state import QUERYABLE_STATE_DOMAINS
from .grounded_answer import join_german, render_state_answer
from .meaning import TemporalPerspective


@dataclass(frozen=True)
class VerbStateQueryAnswer:
    response_text: str
    entities: tuple[EntitySnapshot, ...] = ()
    predicate: str | None = None
    explanation_text: str | None = None


_VERB_TO_PREDICATE = {
    "laeuft": "running", "laufen": "running",
    "arbeitet": "running", "arbeiten": "running",
    "spielt": "playing", "spielen": "playing",
    "pausiert": "paused", "pausieren": "paused",
    "startet": "starting", "starten": "starting",
    "stoppt": "stopping", "stoppen": "stopping",
    "oeffnet": "opening", "oeffnen": "opening",
    "schliesst": "closing", "schliessen": "closing",
    "faehrt": "moving", "fahren": "moving",
    "heizt": "heating", "heizen": "heating",
    "kuehlt": "cooling", "kuehlen": "cooling",
    "reinigt": "running", "reinigen": "running",
    "saugt": "running", "saugen": "running",
    "maeht": "running", "maehen": "running",
}
_PREDICATE_LABELS = {
    "running": "aktiv",
    "playing": "bei der Wiedergabe",
    "paused": "pausiert",
    "starting": "beim Starten",
    "stopping": "beim Stoppen",
    "opening": "beim Öffnen",
    "closing": "beim Schließen",
    "moving": "in Bewegung",
    "heating": "beim Heizen",
    "cooling": "beim Kühlen",
}
_VERB_RE = re.compile(r"(?<!\w)(?P<verb>[\wäöüß]+)(?!\w)", re.I)
_HISTORY_RE = re.compile(
    r"\b(?:gestern|vorgestern|früher|damals|letzte[snm]?|"
    r"heute\s+(?:morgen|früh|nacht)|vor\s+\d+\s+(?:minute|stunde|tag))\w*\b",
    re.I,
)
_NEGATED_RE = re.compile(r"\b(?:nicht|kein\w*|nie|niemals)\b", re.I)
_GROUP_RE = re.compile(
    r"^\s*(?:welch\w*|laufen|spielen|arbeiten|sind)\b.*\b(?:alle|die)\b|"
    r"\b(?:alle|welch\w*)\b",
    re.I,
)
_CONTEXT_REFERENCE_RE = re.compile(
    r"\b(?:er|sie|es|das|der|die|dieser|diese|dieses|davon|noch|auch|andere[nmrs]?)\b",
    re.I,
)
_OTHER_RE = re.compile(r"\b(?:andere[nmrs]?|sonstige[nmrs]?)\b", re.I)


def _predicate_from_text(text: str) -> str | None:
    for match in _VERB_RE.finditer(text):
        predicate = _VERB_TO_PREDICATE.get(
            normalize_for_compare(match.group("verb"))
        )
        if predicate is not None:
            return predicate
    return None


def _current_description(entity: EntitySnapshot) -> str:
    raw = normalize_for_compare(entity.state)
    by_domain = {
        "media_player": {
            "playing": "spielt", "buffering": "puffert", "paused": "ist pausiert",
            "idle": "ist inaktiv", "standby": "ist im Bereitschaftsmodus",
            "off": "ist ausgeschaltet",
        },
        "vacuum": {
            "cleaning": "reinigt gerade", "returning": "fährt zur Ladestation",
            "docked": "steht an der Ladestation", "paused": "ist pausiert",
            "idle": "ist inaktiv", "off": "ist ausgeschaltet",
        },
        "lawn_mower": {
            "mowing": "mäht gerade", "returning": "fährt zur Ladestation",
            "docked": "steht an der Ladestation", "paused": "ist pausiert",
            "idle": "ist inaktiv", "off": "ist ausgeschaltet",
        },
        "cover": {
            "opening": "öffnet gerade", "closing": "schließt gerade",
            "open": "ist geöffnet", "closed": "ist geschlossen",
        },
        "valve": {
            "opening": "öffnet gerade", "closing": "schließt gerade",
            "open": "ist geöffnet", "closed": "ist geschlossen",
        },
    }
    rendered = by_domain.get(entity.domain, {}).get(raw)
    if rendered is not None:
        return rendered
    if entity.domain == "climate":
        action = normalize_for_compare(str(entity.attributes.get("hvac_action", "")))
        described = {
            "heating": "heizt gerade", "cooling": "kühlt gerade",
            "idle": "ist im Leerlauf", "off": "ist ausgeschaltet",
        }.get(action)
        if described is not None:
            return described
    if raw == "on":
        return "ist eingeschaltet"
    if raw == "off":
        return "ist ausgeschaltet"
    return f"ist {spoken_state(entity.state)}"


def _evaluate(entity: EntitySnapshot, predicate: str) -> bool | None:
    state = normalize_for_compare(entity.state)
    if state in {"", "unknown", "unavailable"}:
        return None
    if predicate == "playing" and entity.domain == "media_player":
        return state in {"playing", "buffering"}
    if predicate == "paused" and entity.domain in {
        "media_player", "vacuum", "lawn_mower",
    }:
        return state == "paused"
    if predicate == "running":
        active = {
            "media_player": {"playing", "buffering"},
            "vacuum": {"cleaning", "returning"},
            "lawn_mower": {"mowing", "returning"},
            "fan": {"on"},
            "humidifier": {"on"},
        }.get(entity.domain)
        inactive = {
            "media_player": {"paused", "idle", "standby", "off"},
            "vacuum": {"paused", "docked", "idle", "off"},
            "lawn_mower": {"paused", "docked", "idle", "off"},
            "fan": {"off"},
            "humidifier": {"off"},
        }.get(entity.domain)
        if active is None or inactive is None:
            return None
        return True if state in active else False if state in inactive else None
    if predicate in {"opening", "closing", "moving"} and entity.domain in {
        "cover", "valve",
    }:
        if predicate == "moving":
            return state in {"opening", "closing"}
        return state == predicate
    if predicate in {"heating", "cooling"} and entity.domain == "climate":
        action = normalize_for_compare(str(entity.attributes.get("hvac_action", "")))
        if action in {"", "unknown", "unavailable"}:
            return None
        return action == predicate
    # HA exposes no portable start/stop transition for these domains.
    if predicate in {"starting", "stopping"}:
        return True if state == predicate else None
    return None


def _candidate_question(targets: tuple[EntitySnapshot, ...]) -> str:
    names = [entity.friendly_name for entity in targets[:4]]
    if len(names) == 2:
        choices = f"{names[0]} oder {names[1]}"
    else:
        choices = ", ".join(names[:-1]) + f" oder {names[-1]}"
    suffix = " oder ein anderes Gerät" if len(targets) > 4 else ""
    return f"Meinst du {choices}{suffix}?"


def _explanation(targets: tuple[EntitySnapshot, ...], predicate: str) -> str:
    states = ", ".join(
        f"{entity.friendly_name}: {spoken_state(entity.state)}" for entity in targets
    )
    label = _PREDICATE_LABELS.get(predicate, predicate)
    return (
        f"Ich habe den aktuellen Home-Assistant-Zustand ({states}) gelesen "
        f"und ihn mit dem Prädikat „{label}“ verglichen. "
        "Dabei wurde kein Dienst aufgerufen."
    )


def _contextual_location_targets(
    text: str,
    entities: list[EntitySnapshot],
    domains: frozenset[str],
) -> tuple[EntitySnapshot, ...]:
    """Apply a spoken area/floor to the domain inherited from context."""
    normalized = normalize_for_compare(text)
    candidates = tuple(entity for entity in entities if entity.domain in domains)
    area_ids = {
        entity.area_id
        for entity in candidates
        if entity.area_id
        and any(
            name
            and re.search(
                rf"(?<!\w){re.escape(normalize_for_compare(name))}(?!\w)",
                normalized,
            )
            for name in (entity.area_name, *entity.area_aliases)
        )
    }
    if len(area_ids) == 1:
        area_id = next(iter(area_ids))
        return tuple(entity for entity in candidates if entity.area_id == area_id)

    floor_ids = {
        entity.floor_id
        for entity in candidates
        if entity.floor_id
        and entity.floor_name
        and re.search(
            rf"(?<!\w){re.escape(normalize_for_compare(entity.floor_name))}(?!\w)",
            normalized,
        )
    }
    if len(floor_ids) == 1:
        floor_id = next(iter(floor_ids))
        return tuple(entity for entity in candidates if entity.floor_id == floor_id)

    level = None
    if re.search(r"\b(?:oben|oberste[nmrs]?|hoch)\b", normalized):
        level = resolve_floor_by_level_keyword("up", list(candidates))
    elif re.search(r"\b(?:unten|unterste[nmrs]?)\b", normalized):
        level = resolve_floor_by_level_keyword("down", list(candidates))
    if level is not None and level.status is FloorResolveStatus.OK:
        return tuple(entity for entity in candidates if entity.floor_id == level.floor_id)
    return ()


def _answer_targets(
    text: str,
    targets: tuple[EntitySnapshot, ...],
    predicate: str,
) -> VerbStateQueryAnswer:
    if len(targets) > 1 and not _GROUP_RE.search(text):
        return VerbStateQueryAnswer(
            _candidate_question(targets),
            targets,
            predicate,
            _explanation(targets, predicate),
        )

    evaluations = tuple(_evaluate(entity, predicate) for entity in targets)
    explanation = _explanation(targets, predicate)
    if len(targets) == 1:
        entity = targets[0]
        evaluation = evaluations[0]
        current = _current_description(entity)
        grounded = render_state_answer(
            entity.friendly_name,
            current,
            evaluation,
            negated_question=bool(_NEGATED_RE.search(text)),
        )
        return VerbStateQueryAnswer(
            grounded.speech,
            targets,
            predicate,
            explanation,
        )

    true_entities = [
        entity for entity, value in zip(targets, evaluations) if value is True
    ]
    unknown_entities = [
        entity for entity, value in zip(targets, evaluations) if value is None
    ]
    if text.lstrip().casefold().startswith("welch"):
        if not true_entities:
            speech = "Keines der passenden Geräte erfüllt diesen Zustand."
        else:
            speech = "Das trifft auf " + join_german(
                [entity.friendly_name for entity in true_entities]
            ) + " zu."
        if unknown_entities:
            speech += " Nicht sicher bestimmbar: " + join_german(
                [entity.friendly_name for entity in unknown_entities]
            ) + "."
    elif unknown_entities:
        speech = "Das kann ich nicht für alle sicher beantworten. Unklar sind: " + join_german(
            [entity.friendly_name for entity in unknown_entities]
        ) + "."
    elif len(true_entities) == len(targets):
        speech = f"Ja, das trifft auf alle {len(targets)} passenden Geräte zu."
    else:
        false_names = [
            entity.friendly_name
            for entity, value in zip(targets, evaluations)
            if value is False
        ]
        speech = "Nein. Nicht zutreffend: " + join_german(false_names) + "."
    return VerbStateQueryAnswer(
        speech, targets, predicate, explanation
    )


def match_verb_state_query(
    text: str,
    entities: list[EntitySnapshot],
    temporal_perspective: TemporalPerspective = TemporalPerspective.UNSPECIFIED,
) -> VerbStateQueryAnswer | None:
    """Answer one current-state query and never create an action."""
    if temporal_perspective is TemporalPerspective.PAST or _HISTORY_RE.search(text):
        return None
    predicate = _predicate_from_text(text)
    if predicate is None:
        return None
    targets = resolve_query_targets(text, entities, QUERYABLE_STATE_DOMAINS)
    if not targets:
        return None
    return _answer_targets(text, targets, predicate)


def match_contextual_verb_state_query(
    text: str,
    entities: list[EntitySnapshot],
    focus_entities: tuple[EntitySnapshot, ...],
    previous_predicate: str | None,
) -> VerbStateQueryAnswer | None:
    """Resolve pronouns, inherited predicates and location-only follow-ups."""
    if not focus_entities or _HISTORY_RE.search(text):
        return None
    predicate = _predicate_from_text(text) or previous_predicate
    if predicate is None:
        return None

    domains = frozenset(entity.domain for entity in focus_entities)
    explicit = resolve_query_targets(text, entities, domains)
    if explicit:
        return _answer_targets(text, explicit, predicate)

    located = _contextual_location_targets(text, entities, domains)
    if located:
        return _answer_targets(text, located, predicate)

    if _OTHER_RE.search(text):
        focused_ids = {entity.entity_id for entity in focus_entities}
        area_ids = {entity.area_id for entity in focus_entities if entity.area_id}
        domain_alternatives = tuple(
            entity for entity in entities
            if entity.domain in domains
            and entity.entity_id not in focused_ids
        )
        area_alternatives = tuple(
            entity for entity in domain_alternatives
            if not area_ids or entity.area_id in area_ids
        )
        # Prefer the bounded current area, but if it contains no other
        # candidate and exactly one domain-wide alternative exists, "der
        # andere" is still deterministic and therefore safe.
        alternatives = area_alternatives or domain_alternatives
        if alternatives:
            return _answer_targets(text, alternatives, predicate)

    if _CONTEXT_REFERENCE_RE.search(text):
        return _answer_targets(text, focus_entities, predicate)
    return None
