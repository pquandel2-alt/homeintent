"""Floor name resolution: spoken/friendly floor name -> floor_id.

Phase 28 (v2 plan, "Hierarchical Locations"): mirrors ``areas.py``'s scored
resolution (exact, normalized exact, contains, reverse contains) for floors,
plus a resolver for the "oben"/"unten" (up/down) keywords - the plan's own
example is "alle Lichter oben". "oben"/"unten" resolve to the floor with the
highest/lowest ``level`` among the floors actually referenced by the given
entity list, never against some global/assumed house layout - same "never
guess, only resolve within what's actually present" principle as the rest of
this engine. Built from the already-fetched entity list's floor_id/floor_name/
floor_level (no separate registry access), so this stays hass-free like
``areas.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .entities import EntitySnapshot, normalize_for_compare


class FloorResolveStatus(Enum):
    OK = auto()
    AMBIGUOUS = auto()
    NOT_FOUND = auto()


@dataclass(frozen=True)
class FloorResolveResult:
    status: FloorResolveStatus
    floor_id: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class FloorSnapshot:
    floor_id: str
    name: str
    level: int | None = None


class FloorResolutionStatus(Enum):
    RESOLVED = auto()
    AMBIGUOUS = auto()
    NOT_FOUND = auto()


@dataclass(frozen=True)
class FloorResolutionResult:
    status: FloorResolutionStatus
    floor: FloorSnapshot | None = None
    candidates: tuple[FloorSnapshot, ...] = ()
    score: float | None = None


# Same tier shape as areas.resolve_area_scored (Phase 7) - floors have no
# alias dimension, so only the four name tiers apply.
_SCORE_EXACT = 100
_SCORE_NORMALIZED_EXACT = 85
_SCORE_CONTAINS = 50
_SCORE_REVERSE_CONTAINS = 40
_AMBIGUITY_MARGIN = 5


def floor_snapshots(entities: list[EntitySnapshot]) -> tuple[FloorSnapshot, ...]:
    """Distinct floors referenced by the given entities, deduplicated by floor_id."""
    seen: dict[str, FloorSnapshot] = {}
    for e in entities:
        if e.floor_id is None or e.floor_name is None:
            continue
        seen.setdefault(
            e.floor_id, FloorSnapshot(floor_id=e.floor_id, name=e.floor_name, level=e.floor_level)
        )
    return tuple(seen.values())


def _score_floor_name(spoken: str, spoken_norm: str, floor_name: str) -> float | None:
    floor_norm = normalize_for_compare(floor_name)
    if not floor_norm or not spoken_norm:
        return None
    if floor_name == spoken:
        return _SCORE_EXACT
    if floor_norm == spoken_norm:
        return _SCORE_NORMALIZED_EXACT
    if spoken_norm in floor_norm:
        return _SCORE_CONTAINS
    if floor_norm in spoken_norm:
        return _SCORE_REVERSE_CONTAINS
    return None


def resolve_floor_scored(
    name: str,
    entities: list[EntitySnapshot],
    *,
    snapshots: tuple[FloorSnapshot, ...] | None = None,
) -> FloorResolutionResult:
    """Scored floor resolution (v2 plan Phase 28): same candidate generation +
    scoring + ambiguity detection pattern as resolve_area_scored (Phase 7),
    just for floor names instead of room names.
    """
    spoken = (name or "").strip()
    if not spoken:
        return FloorResolutionResult(status=FloorResolutionStatus.NOT_FOUND)
    spoken_norm = normalize_for_compare(spoken)

    ranked: list[tuple[FloorSnapshot, float]] = []
    for floor in snapshots if snapshots is not None else floor_snapshots(entities):
        score = _score_floor_name(spoken, spoken_norm, floor.name)
        if score is not None:
            ranked.append((floor, score))

    if not ranked:
        return FloorResolutionResult(status=FloorResolutionStatus.NOT_FOUND)

    ranked.sort(key=lambda pair: pair[1], reverse=True)
    top_score = ranked[0][1]
    tied = [floor for floor, score in ranked if top_score - score <= _AMBIGUITY_MARGIN]

    if len(tied) > 1:
        return FloorResolutionResult(status=FloorResolutionStatus.AMBIGUOUS, candidates=tuple(tied))
    return FloorResolutionResult(status=FloorResolutionStatus.RESOLVED, floor=tied[0], score=top_score)


def resolve_floor_name(name: str, entities: list[EntitySnapshot]) -> FloorResolveResult:
    """Resolve a spoken floor name to a floor_id within the given entity set.

    Thin wrapper around ``resolve_floor_scored``, mirroring
    ``areas.resolve_area_name`` for callers that only need the floor_id.
    """
    result = resolve_floor_scored(name, entities)
    if result.status is FloorResolutionStatus.RESOLVED and result.floor is not None:
        return FloorResolveResult(status=FloorResolveStatus.OK, floor_id=result.floor.floor_id)
    if result.status is FloorResolutionStatus.AMBIGUOUS:
        return FloorResolveResult(
            status=FloorResolveStatus.AMBIGUOUS,
            candidates=tuple(f.floor_id for f in result.candidates),
        )
    return FloorResolveResult(status=FloorResolveStatus.NOT_FOUND)


def resolve_floor_by_level_keyword(keyword: str, entities: list[EntitySnapshot]) -> FloorResolveResult:
    """Resolve "oben"/"unten" to the upper/ground floor actually
    referenced by ``entities`` (v2 plan Phase 28's own example, "alle Lichter
    oben").

    Deliberately scoped to the floors present in the given entity list, not
    some assumed global house layout - a single-floor home (or one where no
    entity carries floor data) has no "oben"/"unten" to resolve, and that's
    NOT_FOUND rather than a guess. Ties (two or more floors sharing the
    extreme level, or any floor missing a ``level`` value) are AMBIGUOUS/
    NOT_FOUND rather than picking one arbitrarily.
    """
    floors = floor_snapshots(entities)
    levels = [floor.level for floor in floors if floor.level is not None]
    if not levels:
        return FloorResolveResult(status=FloorResolveStatus.NOT_FOUND)

    from .nlu.place_model import level_for_keyword

    extreme = level_for_keyword(keyword, levels) if keyword in {"up", "down"} else None
    if extreme is None:
        return FloorResolveResult(status=FloorResolveStatus.NOT_FOUND)

    matching = [f for f in floors if f.level == extreme]
    if len(matching) > 1:
        return FloorResolveResult(
            status=FloorResolveStatus.AMBIGUOUS, candidates=tuple(f.floor_id for f in matching)
        )
    return FloorResolveResult(status=FloorResolveStatus.OK, floor_id=matching[0].floor_id)
