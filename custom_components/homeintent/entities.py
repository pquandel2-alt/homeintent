"""Entity resolution: spoken/friendly name -> entity_id.

Algorithm ported from xiaozhi_entity_mcp's ``_name_map()``/``_resolve()``
(https://github.com/pquandel2-alt/xiaozhi_entity_mcp): exact match on the
lowercased friendly name first, then an unambiguous "contains" match,
otherwise an explicit AMBIGUOUS/NOT_FOUND result. Kept hass-free (works on
plain ``EntitySnapshot`` values) so it is unit-testable without a running
Home Assistant instance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Mapping

from .name_similarity import bounded_name_similarity


@dataclass(frozen=True)
class EntitySnapshot:
    """Hass-free view of a Home Assistant entity.

    ``aliases`` and ``capabilities`` are structurally present from here on
    (v2 plan Phase 4) but populated empty until their own phases build the
    logic that fills them - the alias system (Phase 5) and the Capability
    concept (Phase 9). ``attributes`` holds the raw HA state attributes for
    later domain-specific extensions (e.g. Phase 14+) to read from, without
    needing new EntitySnapshot fields per attribute.
    """

    entity_id: str
    friendly_name: str
    domain: str
    state: str
    area_id: str | None = None
    area_name: str | None = None
    floor_id: str | None = None
    floor_name: str | None = None
    floor_level: int | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    aliases: tuple[str, ...] = ()
    # HA Area Registry aliases (e.g. "draußen" for area "Garten") - the
    # area-level counterpart to ``aliases`` above (entity-level). Carried per
    # entity (not a separate area->aliases map) since EntitySnapshot is
    # already the only hass-free channel areas.py's resolver reads from - see
    # hass_entities.py::_area_aliases() for how this is populated live.
    area_aliases: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    last_changed: datetime | None = None
    last_updated: datetime | None = None


class ResolveStatus(Enum):
    OK = auto()
    AMBIGUOUS = auto()
    NOT_FOUND = auto()


@dataclass(frozen=True)
class EntityAlias:
    """One alternate name candidate for an entity, with its provenance.

    ``source`` is kept (rather than discarded once generated) because Phase
    6 (Entity Resolver 2.0) scores candidates differently per source - a
    configured alias and a derived entity-id name are not equally strong
    evidence.
    """

    entity_id: str
    text: str
    source: str


def _humanize_object_id(entity_id: str) -> str:
    """'light.wohnzimmer_decke' -> 'Wohnzimmer Decke'.

    Pure formatting (underscore -> space, title case), not a translation -
    safe to apply to any entity_id without risking a wrong guess.
    """
    object_id = entity_id.split(".", 1)[1]
    return object_id.replace("_", " ").strip().title()


def generate_aliases(entity: EntitySnapshot) -> tuple[EntityAlias, ...]:
    """Alternate name candidates for an entity, beyond its friendly_name.

    Sources: the entity_id itself (raw and humanized "Entity Name" form) and
    the aliases configured in HA's entity registry (``EntitySnapshot.aliases``,
    wired from ``entry.aliases`` in hass_entities.py - the same field HA's
    own Assist feature uses). Area and Device Class are deliberately *not*
    turned into text candidates here: Phase 6 (Entity Resolver 2.0) already
    scores them as their own dimensions (matching area +30, matching device
    class +15), and translating a device_class into German text for an
    unknown/future HA device class would be a guess, not a lookup.
    """
    seen: set[str] = {entity.friendly_name.strip().lower()}
    aliases: list[EntityAlias] = []

    def add(text: str | None, source: str) -> None:
        if not text:
            return
        text = text.strip()
        key = text.lower()
        if not text or key in seen:
            return
        seen.add(key)
        aliases.append(EntityAlias(entity_id=entity.entity_id, text=text, source=source))

    add(_humanize_object_id(entity.entity_id), "entity_name")
    add(entity.entity_id, "entity_id")
    for configured in entity.aliases:
        add(configured, "configured")

    # Common spoken device words are derived from the entity's real name,
    # preserving its distinguishing suffix (for example "Rollo Büro").
    if entity.domain == "cover":
        for source in (entity.friendly_name, *entity.aliases):
            if re.search(r"\brolll?ad(?:e|en)\b", source, re.IGNORECASE):
                for replacement in ("Rollladen", "Rollade", "Rollo", "Jalousie"):
                    add(
                        re.sub(r"\brolll?ad(?:e|en)\b", replacement, source, flags=re.IGNORECASE),
                        "derived",
                    )
    elif entity.domain == "light":
        for source in (entity.friendly_name, *entity.aliases):
            if re.search(r"\b(?:lampe|leuchte|licht)\b", source, re.IGNORECASE):
                for replacement in ("Lampe", "Leuchte", "Licht"):
                    add(
                        re.sub(r"\b(?:lampe|leuchte|licht)\b", replacement, source, flags=re.IGNORECASE),
                        "derived",
                    )

    return tuple(aliases)


class ResolutionStatus(Enum):
    RESOLVED = auto()
    AMBIGUOUS = auto()
    CONFIRMATION_REQUIRED = auto()
    NOT_FOUND = auto()


class EntityMatchSource(Enum):
    FRIENDLY_NAME = auto()
    CONFIGURED_ALIAS = auto()
    GENERATED_ALIAS = auto()
    ENTITY_ID = auto()
    CONTAINS = auto()
    FUZZY = auto()


@dataclass(frozen=True)
class EntityCandidate:
    """One explainable, stable registry-name candidate."""

    entity: EntitySnapshot
    score: float
    source: EntityMatchSource
    matched_name: str
    distance: int = 0


@dataclass(frozen=True)
class EntityCandidateSet:
    """Ranked candidates and the safety margin used for one resolution."""

    query: str
    ranked: tuple[EntityCandidate, ...]
    margin: float | None = None
    correction_required: bool = False

    @property
    def entities(self) -> tuple[EntitySnapshot, ...]:
        return tuple(candidate.entity for candidate in self.ranked)


@dataclass(frozen=True)
class ResolveResult:
    status: ResolveStatus
    entity: EntitySnapshot | None = None
    candidates: tuple[EntitySnapshot, ...] = ()
    candidate_set: EntityCandidateSet | None = None


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    entity: EntitySnapshot | None = None
    candidates: tuple[EntitySnapshot, ...] = ()
    score: float | None = None
    # Diagnostic explanation does not alter resolution equality. Indexed
    # and full-scan calls may observe a different runner-up margin while
    # producing the same authoritative result.
    candidate_set: EntityCandidateSet | None = field(default=None, compare=False)


# Scoring weights (v2 plan Phase 6, "Beispielwerte, experimentell, durch
# Tests zu validieren" - kept exactly as specified, validated by
# tests/test_resolver.py rather than tuned further).
_SCORE_EXACT_FRIENDLY_NAME = 100
_SCORE_EXACT_ALIAS = 90
_SCORE_NORMALIZED_EXACT = 85
_SCORE_ENTITY_ID_MATCH = 70
_SCORE_CONTAINS = 50
_SCORE_REVERSE_CONTAINS = 40
_SCORE_MATCHING_AREA = 30
_SCORE_MATCHING_DOMAIN = 20
_SCORE_MATCHING_DEVICE_CLASS = 15

# Two candidates within this many points of each other are "practically
# equivalent" (plan's Schritt 4, Ambiguity Detection) - report AMBIGUOUS
# instead of guessing the higher-scored one.
_AMBIGUITY_MARGIN = 5


def format_spoken_number(value: object) -> str:
    """Numeric value with a German decimal comma ("19,5", "78" not "78.0").

    Non-numeric values are returned unchanged.
    """
    if isinstance(value, bool):
        return str(value)
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return str(value)
    if number != number or number in (float("inf"), float("-inf")):
        return str(value)
    rendered = f"{number:.2f}".rstrip("0").rstrip(".")
    if rendered == "-0":
        rendered = "0"
    return rendered.replace(".", ",")


# Starter set of commonly seen HA units spoken in German; unknown units are
# still spoken raw (e.g. "42 mbar"), never crash.
SPOKEN_UNITS_DE = {
    "°C": "Grad", "°F": "Grad Fahrenheit", "%": "Prozent", "hPa": "Hektopascal",
    "lx": "Lux", "W": "Watt", "kW": "Kilowatt", "Wh": "Wattstunden",
    "kWh": "Kilowattstunden", "ppm": "ppm",
}


def spoken_unit(unit: str | None) -> str | None:
    """German spoken form of a Home Assistant unit of measurement."""
    if not unit:
        return None
    return SPOKEN_UNITS_DE.get(unit, unit)


# German words for raw Home Assistant states, so that no English enum value
# ("closed", "paused", "running") is ever spoken (F16).
SPOKEN_STATES_DE: dict[str, str] = {
    "on": "eingeschaltet", "off": "ausgeschaltet", "open": "geöffnet",
    "closed": "geschlossen", "opening": "öffnet gerade", "closing": "schließt gerade",
    "locked": "verriegelt", "unlocked": "entriegelt", "locking": "wird verriegelt",
    "unlocking": "wird entriegelt", "jammed": "blockiert", "playing": "spielt",
    "paused": "pausiert", "idle": "inaktiv", "standby": "im Bereitschaftsmodus",
    "buffering": "puffert", "cleaning": "reinigt", "docked": "an der Ladestation",
    "returning": "fährt zur Ladestation", "mowing": "mäht", "error": "meldet einen Fehler",
    "home": "zuhause", "not_home": "nicht zuhause", "away": "abwesend",
    "heat": "im Heizbetrieb", "cool": "im Kühlbetrieb", "heat_cool": "im Automatikbetrieb",
    "auto": "im Automatikbetrieb", "dry": "im Entfeuchtungsbetrieb", "fan_only": "im Lüfterbetrieb",
    "eco": "im Eco-Modus", "performance": "im Leistungsmodus",
    "armed_away": "scharf (abwesend)", "armed_home": "scharf (zuhause)",
    "armed_night": "scharf (Nacht)", "disarmed": "unscharf", "triggered": "ausgelöst",
    "pending": "wartet", "arming": "wird scharf geschaltet",
    "active": "aktiv", "running": "läuft", "finished": "fertig", "stopped": "gestoppt",
    "unknown": "unbekannt", "unavailable": "nicht verfügbar",
    "above_horizon": "über dem Horizont", "below_horizon": "unter dem Horizont",
}


def spoken_state(state: str) -> str:
    """German spoken form of a raw HA state; unknown values stay as given."""
    return SPOKEN_STATES_DE.get(state.casefold(), state)


# Domains whose state is the timestamp of their last activation. "unknown" is
# their normal state until they are triggered for the first time and says
# nothing about the device being unreachable.
ACTIVATION_TIMESTAMP_DOMAINS = frozenset({"scene", "button", "input_button"})

# Domains whose state never describes a current device condition: activation
# timestamps above, notify entities (timestamp of the last message, "unknown"
# until the first one) and scripts. "unknown" must not block an action there.
STATELESS_ACTION_DOMAINS = ACTIVATION_TIMESTAMP_DOMAINS | frozenset({"notify", "script"})


def normalize_for_compare(text: str) -> str:
    """Lowercase + collapse whitespace, for the 'Normalized exact' tier.

    Deliberately does not fold umlauts/punctuation - normalize.py already
    documents that boundary (semantic decisions belong in resolution, not
    normalization, but *guessing* a folding of "ß"/"ü" etc. would itself be
    exactly the kind of guess this project avoids).
    """
    folded = text.strip().casefold()
    folded = folded.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return " ".join(folded.split())



OUTDOOR_MARKERS = ("aussen", "draussen", "garten", "terrasse", "balkon")


def is_outdoor_entity(entity: EntitySnapshot) -> bool:
    """Whether a device is outdoors by its name, area or floor ("Außentemperatur")."""
    names = (
        entity.friendly_name, entity.area_name or "", entity.floor_name or "",
        *entity.area_aliases,
    )
    return any(
        marker in normalize_for_compare(name)
        for name in names
        for marker in OUTDOOR_MARKERS
    )

def _score_name_pair(
    spoken: str,
    spoken_norm: str,
    candidate_text: str,
    source: str,
    *,
    allow_fuzzy: bool,
) -> tuple[float, EntityMatchSource, int] | None:
    """Best score, provenance and distance for one name pair."""
    if not candidate_text or not spoken_norm:
        return None
    candidate_norm = normalize_for_compare(candidate_text)
    if not candidate_norm:
        return None

    if source == "entity_id":
        # Raw entity_ids are lowercase by HA convention - no natural-language
        # "contains" fragment matching for them, just a dedicated tier.
        return (
            (_SCORE_ENTITY_ID_MATCH, EntityMatchSource.ENTITY_ID, 0)
            if candidate_norm == spoken_norm
            else None
        )

    if candidate_text == spoken:
        return (
            _SCORE_EXACT_FRIENDLY_NAME
            if source == "friendly_name"
            else _SCORE_EXACT_ALIAS,
            EntityMatchSource.FRIENDLY_NAME
            if source == "friendly_name"
            else EntityMatchSource.CONFIGURED_ALIAS
            if source == "configured"
            else EntityMatchSource.GENERATED_ALIAS,
            0,
        )
    if candidate_norm == spoken_norm:
        return (
            _SCORE_NORMALIZED_EXACT,
            EntityMatchSource.FRIENDLY_NAME
            if source == "friendly_name"
            else EntityMatchSource.CONFIGURED_ALIAS
            if source == "configured"
            else EntityMatchSource.GENERATED_ALIAS,
            0,
        )
    if len(spoken_norm) >= 3 and spoken_norm in candidate_norm:
        return _SCORE_CONTAINS, EntityMatchSource.CONTAINS, 0
    if len(candidate_norm) >= 3 and candidate_norm in spoken_norm:
        return _SCORE_REVERSE_CONTAINS, EntityMatchSource.CONTAINS, 0
    if not allow_fuzzy:
        return None
    similarity = bounded_name_similarity(spoken_norm, candidate_norm)
    if similarity.accepted:
        # Fuzzy evidence never outranks a literal contains match.  A sole
        # fuzzy candidate requires confirmation; several near-equal fuzzy
        # candidates become an ordinary clarification.
        score = 30.0 + similarity.ratio * 10.0 - similarity.distance * 2.0
        return score, EntityMatchSource.FUZZY, similarity.distance
    return None


def _best_name_candidate(
    spoken: str,
    spoken_norm: str,
    entity: EntitySnapshot,
    *,
    allow_fuzzy: bool,
) -> EntityCandidate | None:
    """Highest tier score across friendly_name and all generated aliases -
    Schritt 1+2 of the plan (Candidate Generation + Scoring) for one entity."""
    best: EntityCandidate | None = None
    for text, source in [(entity.friendly_name, "friendly_name"), *(
        (a.text, a.source) for a in generate_aliases(entity)
    )]:
        match = _score_name_pair(
            spoken, spoken_norm, text, source, allow_fuzzy=allow_fuzzy
        )
        if match is None:
            continue
        score, match_source, distance = match
        candidate = EntityCandidate(entity, score, match_source, text, distance)
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def resolve_entity_scored(
    name: str,
    entities: list[EntitySnapshot],
    *,
    area_id: str | None = None,
    domain: str | None = None,
    device_class: str | None = None,
    index: EntityIndex | None = None,
) -> ResolutionResult:
    """Multi-stage scored entity resolution (v2 plan Phase 6, "Entity
    Resolver 2.0"): candidate generation + scoring across friendly_name/
    aliases/entity_id, optional context bonuses (Schritt 3, Context
    Filtering - only applied when the caller actually knows the area/domain/
    device_class it's looking for), then ambiguity detection (Schritt 4) on
    the final ranking.

    ``index`` (World Model Wave 1, building on Phase 23's ``EntityIndex``):
    an optional prebuilt index (see ``build_entity_index()``) used to narrow
    the candidate list scanned below via ``by_domain``/``by_area`` *before*
    scoring runs, for callers that already know the ``domain``/``area_id``
    they're resolving within and can build one index per conversation turn
    instead of paying an O(n) scan per resolution call. Omitted (the
    default): behaves exactly as before, scanning the full ``entities``
    list - fully backward compatible for every existing caller.

    Caveat: unlike the ``area_id``/``domain`` *bonuses* applied below (which
    only nudge a candidate's score, never exclude it), the ``index``-based
    prefilter is a hard filter - an entity outside the requested domain/area
    is dropped before scoring even runs, so it can never be picked no matter
    how well its name matches. Every current caller that passes both
    ``index`` and ``domain``/``area_id`` already only wants matches within
    that domain/area (see test_entity_index.py's
    ``test_index_prefilter_matches_full_scan_result`` for a worked example
    where prefiltering and full-scan agree), but this is a real, documented
    semantic narrowing versus the index-less path for the pathological case
    of a strong name match sitting entirely outside the given domain/area -
    flagged here rather than silently accepted.
    """
    spoken = (name or "").strip()
    if not spoken:
        return ResolutionResult(status=ResolutionStatus.NOT_FOUND)
    spoken_norm = normalize_for_compare(spoken)

    candidates: list[EntitySnapshot] = entities
    if index is not None and (domain is not None or area_id is not None):
        if domain is not None:
            candidates = list(index.by_domain.get(domain, ()))
            if area_id is not None:
                candidates = [e for e in candidates if e.area_id == area_id]
        elif area_id is not None:
            candidates = list(index.by_area.get(area_id, ()))

    # An exact normalized registry name always outranks contains/fuzzy tiers
    # by more than the ambiguity margin. Reuse the per-turn index for that
    # decisive tier instead of regenerating aliases for the complete HA
    # registry. A narrowed caller-provided candidate list remains a hard
    # boundary: indexed hits outside it are discarded before scoring.
    if index is not None:
        exact_by_id = {
            entity.entity_id: entity
            for entity in (
                *index.by_normalized_name.get(spoken_norm, ()),
                *index.by_normalized_alias.get(spoken_norm, ()),
            )
        }
        exact_indexed = tuple(exact_by_id.values())
        if exact_indexed:
            if candidates is not entities:
                exact_indexed = tuple(
                    entity for entity in exact_indexed if entity in candidates
                )
            if exact_indexed:
                candidates = list(exact_indexed)

    ranked: list[EntityCandidate] = []
    for allow_fuzzy in (False, True):
        for entity in candidates:
            candidate = _best_name_candidate(
                spoken, spoken_norm, entity, allow_fuzzy=allow_fuzzy
            )
            if candidate is None:
                continue
            total = candidate.score
            if area_id is not None and entity.area_id == area_id:
                total += _SCORE_MATCHING_AREA
            if domain is not None and entity.domain == domain:
                total += _SCORE_MATCHING_DOMAIN
            if device_class is not None and entity.device_class == device_class:
                total += _SCORE_MATCHING_DEVICE_CLASS
            ranked.append(EntityCandidate(
                candidate.entity,
                total,
                candidate.source,
                candidate.matched_name,
                candidate.distance,
            ))
        if ranked:
            break

    if not ranked:
        return ResolutionResult(status=ResolutionStatus.NOT_FOUND)

    ranked.sort(key=lambda item: (-item.score, item.entity.entity_id))
    top_score = ranked[0].score
    top = [
        candidate
        for candidate in ranked
        if top_score - candidate.score <= _AMBIGUITY_MARGIN
    ]
    margin = top_score - ranked[len(top)].score if len(ranked) > len(top) else None
    candidate_set = EntityCandidateSet(
        query=spoken,
        ranked=tuple(top),
        margin=margin,
        correction_required=all(
            candidate.source is EntityMatchSource.FUZZY for candidate in top
        ),
    )

    if len(top) > 1:
        return ResolutionResult(
            status=ResolutionStatus.AMBIGUOUS,
            candidates=candidate_set.entities,
            score=top_score,
            candidate_set=candidate_set,
        )
    if top[0].source is EntityMatchSource.FUZZY:
        return ResolutionResult(
            status=ResolutionStatus.CONFIRMATION_REQUIRED,
            candidates=candidate_set.entities,
            score=top_score,
            candidate_set=candidate_set,
        )
    return ResolutionResult(
        status=ResolutionStatus.RESOLVED,
        entity=top[0].entity,
        score=top_score,
        candidate_set=candidate_set,
    )


def resolve_entity(
    name: str,
    entities: list[EntitySnapshot],
    *,
    index: EntityIndex | None = None,
) -> ResolveResult:
    """Resolve a spoken name to an entity within the given entity set.

    Thin, context-free wrapper around ``resolve_entity_scored`` (v2 plan
    Phase 6) for callers that don't need scores/context bonuses - exact and
    normalized-exact matches win outright (their score gap to any
    contains-tier competitor always exceeds the ambiguity margin), duplicate
    names/aliases surface as AMBIGUOUS rather than "first one wins".
    """
    result = resolve_entity_scored(name, entities, index=index)
    if result.status is ResolutionStatus.RESOLVED:
        return ResolveResult(
            status=ResolveStatus.OK,
            entity=result.entity,
            candidate_set=result.candidate_set,
        )
    if result.status is ResolutionStatus.AMBIGUOUS:
        return ResolveResult(
            status=ResolveStatus.AMBIGUOUS,
            candidates=result.candidates,
            candidate_set=result.candidate_set,
        )
    return ResolveResult(
        status=ResolveStatus.NOT_FOUND,
        candidates=result.candidates,
        candidate_set=result.candidate_set,
    )


def resolve_entities_by_domain(
    domain: str,
    entities: list[EntitySnapshot],
    area_id: str | None = None,
    floor_id: str | None = None,
) -> list[EntitySnapshot]:
    """All entities of a domain, optionally filtered to one area and/or floor.

    Unlike ``resolve_entity`` there is no ambiguity concept here - a
    quantifier ("alle"/"beide") is explicitly asking for every matching
    entity, so this returns everything that matches rather than requiring
    a single unambiguous hit. Sorted by entity_id for deterministic output.
    """
    matches = [
        e for e in entities
        if e.domain == domain
        and (area_id is None or e.area_id == area_id)
        and (floor_id is None or e.floor_id == floor_id)
    ]
    return sorted(matches, key=lambda e: e.entity_id)


@dataclass(frozen=True)
class EntityIndex:
    """Precomputed candidate lookups (v2 plan Phase 23, "Performance"):
    normalized_name/alias/area/domain -> matching entities.

    Building this costs the same O(n) scan ``resolve_entity_scored()``
    already does per call, so it only pays off once a single conversation
    turn needs *multiple* resolutions against the same entity list - not yet
    the case, since every parser today (see ``engine.py``'s
    ``_select_parser()``) calls ``resolve_entity``/``resolve_entities_by_domain``/
    ``resolve_area_name`` at most once per utterance. Built and tested now,
    ahead of its consumer - the same additive pattern already established
    for ``SemanticFrame``/``SemanticCommand``/``respond()`` (see their
    phases' docstrings): a future multi-target phase (Pronomen/Referenzen,
    Hierarchische Orte) can build one ``EntityIndex`` per turn and reuse it
    instead of rescanning ``entities`` for every candidate, rather than
    every future phase inventing its own indexing scheme ad hoc.
    """

    by_domain: Mapping[str, tuple[EntitySnapshot, ...]]
    by_area: Mapping[str, tuple[EntitySnapshot, ...]]
    by_normalized_name: Mapping[str, tuple[EntitySnapshot, ...]]
    by_normalized_alias: Mapping[str, tuple[EntitySnapshot, ...]]
    by_normalized_name_token: Mapping[str, tuple[EntitySnapshot, ...]]


def build_entity_index(entities: list[EntitySnapshot]) -> EntityIndex:
    """Group ``entities`` into the four lookups ``EntityIndex`` exposes.

    ``by_normalized_name`` covers the friendly name; ``by_normalized_alias``
    covers every ``generate_aliases()`` candidate (entity_id, humanized
    entity_id, configured aliases) - ``generate_aliases()`` already excludes
    the friendly name via its own ``seen`` set, so the two indices together,
    without overlap, cover every name tier ``resolve_entity_scored`` scores -
    just grouped for O(1) exact-tier lookup instead of a fresh scan.
    """
    by_domain: dict[str, list[EntitySnapshot]] = {}
    by_area: dict[str, list[EntitySnapshot]] = {}
    by_normalized_name: dict[str, list[EntitySnapshot]] = {}
    by_normalized_alias: dict[str, list[EntitySnapshot]] = {}
    by_normalized_name_token: dict[str, list[EntitySnapshot]] = {}

    for entity in entities:
        by_domain.setdefault(entity.domain, []).append(entity)
        if entity.area_id is not None:
            by_area.setdefault(entity.area_id, []).append(entity)
        normalized_name = normalize_for_compare(entity.friendly_name)
        by_normalized_name.setdefault(normalized_name, []).append(entity)
        aliases = generate_aliases(entity)
        normalized_aliases = tuple(normalize_for_compare(alias.text) for alias in aliases)
        for normalized_alias in normalized_aliases:
            by_normalized_alias.setdefault(normalized_alias, []).append(entity)
        for token in {
            token
            for candidate in (normalized_name, *normalized_aliases)
            for token in candidate.split()
        }:
            by_normalized_name_token.setdefault(token, []).append(entity)

    return EntityIndex(
        by_domain={key: tuple(value) for key, value in by_domain.items()},
        by_area={key: tuple(value) for key, value in by_area.items()},
        by_normalized_name={key: tuple(value) for key, value in by_normalized_name.items()},
        by_normalized_alias={key: tuple(value) for key, value in by_normalized_alias.items()},
        by_normalized_name_token={
            key: tuple(value) for key, value in by_normalized_name_token.items()
        },
    )
