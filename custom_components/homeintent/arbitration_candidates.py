"""Candidates of the interpreters for the arbiter (7.5.0).

Collects what each interpreter would propose for one sentence - the parser
(``engine.understand``), the need interpreter (``engine.understand_need``)
and the situation view - as ``Candidate`` objects. Nothing is executed; the
payloads are exactly the objects the conversation would hand to validator
and execution policy.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .arbitration import Authority, Candidate, Effect
from .automation_results import AutomationMatchResult
from .entities import EntitySnapshot
from .nlu.language_frontend import analyse_language
from .nlu.meaning_ir import ground_meaning
from .nlu.semantic_utterance import SpeechAct, TimeKind
from .nlu.situation_views import answer_situation_view


def _plans(payload: Any) -> list[Any]:
    commands = getattr(payload, "commands", None)
    if commands is not None:
        return [command.plan for command in commands if getattr(command, "plan", None) is not None]
    plan = getattr(payload, "plan", None)
    return [plan] if plan is not None else []


def _ids(plan: Any) -> set[str]:
    raw = getattr(plan, "entity_id", ())
    return {raw} if isinstance(raw, str) else set(raw or ())


def _candidate(source: str, speech_act: str, authority: Authority, payload: Any, *, explicit: bool) -> Candidate | None:
    if payload is None:
        return None
    if isinstance(payload, AutomationMatchResult):
        return Candidate(source, speech_act, authority, Effect.DEFER, payload=payload)
    plans = _plans(payload)
    if plans:
        return Candidate(
            source, speech_act, authority, Effect.WRITE,
            targets=frozenset(item for plan in plans for item in _ids(plan)),
            operations=frozenset(f"{plan.domain}.{plan.service}" for plan in plans),
            explicit_device=explicit, payload=payload,
        )
    if getattr(payload, "clarification", None) is not None or getattr(payload, "failure_text", None):
        return Candidate(source, speech_act, authority, Effect.NONE, payload=payload)
    if getattr(payload, "response_text", None):
        effect = Effect.READ if speech_act == SpeechAct.QUERY.name else Effect.NONE
        return Candidate(source, speech_act, authority, effect, payload=payload)
    return Candidate(source, speech_act, authority, Effect.NONE, payload=payload)


def need_query_candidates(view: Any, need: Any, speech_act: str) -> list[Candidate]:
    """The need ↔ question pair as the conversation sees it (switched to
    the arbiter in 7.5.0)."""
    candidates: list[Candidate] = []
    if view is not None:
        candidates.append(Candidate("situation", speech_act, Authority.PARSER, Effect.READ, payload=view))
    need_candidate = _candidate("need", speech_act, Authority.NEED, need, explicit=False)
    if need_candidate is not None:
        candidates.append(need_candidate)
    return candidates


def collect_candidates(
    engine: Any,
    text: str,
    entities: Sequence[EntitySnapshot],
    *,
    source_area_id: str | None = None,
    routine_bindings: Mapping[str, str] | None = None,
) -> tuple[list[Candidate], bool]:
    """All interpreter candidates for ``text`` plus "explicit question"."""
    entity_list = list(entities)
    document = analyse_language(text, entity_list)
    meaning = ground_meaning(document, entity_list)
    speech_act = meaning.speech_act.name
    explicit = any(target.explicit for clause in meaning.clauses for target in clause.targets)
    # The same question shape the conversation's need ↔ question step uses.
    need_statement = any(clause.origin == "implicit_need" for clause in meaning.clauses)
    explicit_question = (
        meaning.speech_act is SpeechAct.QUERY or text.rstrip().endswith("?")
    ) and not (need_statement and not text.rstrip().endswith("?"))
    candidates: list[Candidate] = []
    parser = _candidate("parser", speech_act, Authority.PARSER, engine.understand(text, entity_list).payload, explicit=explicit)
    if parser is not None:
        candidates.append(parser)
    from .security_control import match_alarm_control

    alarm = match_alarm_control(text, entity_list, allow_disarm=True)
    alarm_candidate = _candidate("alarm", speech_act, Authority.PARSER, alarm, explicit=True)
    if alarm_candidate is not None:
        candidates.append(alarm_candidate)
    released = _candidate("release", speech_act, Authority.PARSER, engine.understand_release(document, entity_list), explicit=explicit)
    if released is not None:
        candidates.append(released)
    need = engine.understand_need(
        document, entity_list, source_area_id=source_area_id, context_area_id=None,
        routine_bindings=dict(routine_bindings or {}),
    )
    need_candidate = _candidate("need", speech_act, Authority.NEED, need, explicit=False)
    if need_candidate is not None:
        candidates.append(need_candidate)
    if meaning.speech_act is SpeechAct.QUERY or text.rstrip().endswith("?"):
        view = answer_situation_view(text, entity_list, source_area_id=source_area_id, routine_steps=None)
        if view is not None:
            candidates.append(Candidate("situation", speech_act, Authority.PARSER, Effect.READ, payload=view))
    later = meaning.speech_act is SpeechAct.AUTOMATION or any(
        clause.time is not TimeKind.NOW or clause.conditions for clause in meaning.clauses
    )
    if later:
        # Time-bound or conditional meaning never writes now (7.3.3 Q5):
        # every executable reading becomes a deferred one.
        candidates = [
            Candidate(item.source, item.speech_act, item.authority, Effect.DEFER, item.targets,
                      item.operations, item.risk, item.residue, item.explicit_device, item.evidence,
                      item.payload)
            if item.effect is Effect.WRITE else item
            for item in candidates
        ]
    return candidates, explicit_question


__all__ = ("collect_candidates", "need_query_candidates")
