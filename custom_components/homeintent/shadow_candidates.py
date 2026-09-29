"""Named candidate pipelines for shadow comparisons (7.3.4).

A candidate maps (engine, text, entities) to a payload (``MatchResult`` /
``CommandPlan``) or ``None``. Candidates are compared against the active
pipeline offline (``scripts/shadow_compare.py``) and optionally live
(``shadow_mode: log``); they never execute anything.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from .entities import EntitySnapshot

CandidateFactory = Callable[[Any, str, Sequence[EntitySnapshot]], object | None]


def active_pipeline(engine: Any, text: str, entities: Sequence[EntitySnapshot]) -> object | None:
    """The production direct-command pipeline (``engine.understand``)."""
    return engine.understand(text, list(entities)).payload


def _identity(engine: Any, text: str, entities: Sequence[EntitySnapshot]) -> object | None:
    """Sanity candidate: must be EQUIVALENT everywhere."""
    return active_pipeline(engine, text, entities)


CANDIDATES: dict[str, CandidateFactory] = {
    "identity": _identity,
}


def register_candidate(name: str, factory: CandidateFactory) -> None:
    CANDIDATES[name] = factory
