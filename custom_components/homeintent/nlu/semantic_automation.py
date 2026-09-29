"""Shared semantic compilation primitives for automation predicates."""

from __future__ import annotations

import re

from .automation_model import TriggerTarget
from .constraint_resolver import Constraints, resolve_candidates
from .parser import ParseContext
from .semantic_lexicon import SemanticKind, analyse_semantics
from .semantic_location import resolve_semantic_location
from .semantic_state import SemanticState
from ..entities import normalize_for_compare
from .locative import has_locative_cue




def compile_state_predicate(
    text: str, context: ParseContext
) -> tuple[TriggerTarget, SemanticState] | None:
    """Compile one state predicate without relying on a sentence template.

    Used by both triggers and conditions.  The caller decides whether the
    predicate means "start when" or "allow only while".
    """
    analysis = analyse_semantics(text)
    if analysis.values(SemanticKind.COMMAND_MARKER):
        return None

    states = analysis.values(SemanticKind.STATE)
    device_targets = {
        value
        for value in analysis.values(SemanticKind.DEVICE_CLASS)
        if isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and (value[1] is None or isinstance(value[1], str))
    }
    domain_targets = {
        value for value in analysis.values(SemanticKind.DOMAIN)
        if isinstance(value, str)
    }
    targets = list(device_targets) + [(domain, None) for domain in domain_targets]
    targets = list(dict.fromkeys(targets))
    if len(states) != 1 or len(targets) != 1:
        return None
    domain, device_class = targets[0]
    if not isinstance(domain, str) or not (
        device_class is None or isinstance(device_class, str)
    ):
        return None
    state = next(iter(states))
    if not isinstance(state, SemanticState):
        return None

    location = resolve_semantic_location(text, context.entities)
    if location is None and has_locative_cue(text, followed=True):
        return None
    location_text = location[0] if location else None
    area_id = location[1] if location else None
    floor_id = location[2] if location else None

    if context.world_model is not None:
        candidates = context.world_model.select_entities(
            domain=domain,
            device_class=device_class,
            area_id=area_id,
            floor_id=floor_id,
        )
    else:
        candidates = tuple(resolve_candidates(
            context.entities,
            Constraints(
                domain=domain,
                device_class=device_class,
                area_id=area_id,
                floor_id=floor_id,
            ),
        ))
    if not candidates:
        return None

    location_tokens = {
        normalize_for_compare(token)
        for token in re.findall(r"[\wäöüß]+", location_text or "", re.I)
    }
    unexplained = {
        normalize_for_compare(token) for token in analysis.unexplained_tokens
    } - location_tokens
    if unexplained:
        return None

    return (
        TriggerTarget(
            domain=domain,
            device_class=device_class,
            area_id=area_id,
            floor_id=floor_id,
        ),
        state,
    )
