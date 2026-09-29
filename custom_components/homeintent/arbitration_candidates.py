"""Candidates of the interpreters for the arbiter (7.5.0).

Collects what each interpreter would propose for one sentence - the parser
(``engine.understand``), the need interpreter (``engine.understand_need``)
and the situation view - as ``Candidate`` objects. Nothing is executed; the
payloads are exactly the objects the conversation would hand to validator
and execution policy.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .arbitration import Authority, Candidate, DialogEvidence, DialogReply, Effect
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


_QUESTION_OPENERS = frozenset({"warum", "wieso", "was", "welche", "welches", "wie"})


def dialog_evidence(
    kind: str,
    document: Any,
    reply: DialogReply,
    *,
    contextual_followup: bool,
    supersedable: bool,
    drops_on_new_sentence: bool = False,
) -> DialogEvidence:
    """How this turn relates to the open question (7.7, B3)."""
    utterance = document.utterance
    words = sum(1 for token in document.tokens if token.is_word)
    new_sentence = (
        reply is DialogReply.UNCLEAR
        and words >= 3
        and utterance.speech_act is not SpeechAct.QUERY
        and not any(token.canonical in _QUESTION_OPENERS for token in document.tokens[:2])
    )
    command_shaped = (
        utterance.speech_act is SpeechAct.COMMAND
        and utterance.safe_to_execute_directly
        and not contextual_followup
    )
    return DialogEvidence(
        kind, reply, new_sentence, command_shaped, supersedable, drops_on_new_sentence
    )


def complete_command_candidate(payload: Any) -> Candidate | None:
    """A fresh command reading that could replace an open question.

    Complete means: at least one plan and no clarification anywhere.
    """
    commands = getattr(payload, "commands", None)
    if commands is not None:
        complete = bool(commands) and all(
            getattr(command, "clarification", None) is None for command in commands
        ) and any(getattr(command, "plan", None) is not None for command in commands)
    else:
        complete = (
            getattr(payload, "plan", None) is not None
            and getattr(payload, "clarification", None) is None
        )
    if not complete:
        return None
    return _candidate("parser", SpeechAct.COMMAND.name, Authority.PARSER, payload, explicit=False)


def _authority_of(payload: Any, default: Authority) -> Authority:
    """A confirmed binding is BINDING; a routine only inferred from similar
    words is not "the words of this sentence" (at most a need)."""
    from .plan_origin import PlanOrigin

    if getattr(payload, "binding_confirmed", False):
        return Authority.BINDING
    origin = getattr(payload, "origin", None)
    if origin is None:
        commands = getattr(payload, "commands", None) or ()
        origin = next((getattr(item, "origin", None) for item in commands), None)
    if origin is PlanOrigin.INFERRED_ROUTINE:
        return min(default, Authority.NEED)
    return default


def names_its_targets(text: str, payload: Any, entities: Sequence[EntitySnapshot]) -> bool:
    """Whether the sentence says a target's name or alias verbatim."""
    from .nlu.need_compiler import routine_named_explicitly

    by_id = {entity.entity_id: entity for entity in entities}
    targets = [by_id[item] for plan in _plans(payload) for item in _ids(plan) if item in by_id]
    return bool(targets) and all(routine_named_explicitly(text, entity) for entity in targets)


def context_candidates(readings: Sequence[tuple[str, Any]]) -> list[Candidate]:
    """Writing readings of the context connections (ellipsis, reference,
    query and command follow-ups), all with discourse authority."""
    candidates: list[Candidate] = []
    for source, payload in readings:
        candidate = _candidate(source, "", Authority.DISCOURSE, payload, explicit=False)
        if candidate is not None and candidate.executable:
            candidates.append(candidate)
    return candidates


def need_query_candidates(
    view: Any,
    need: Any,
    speech_act: str,
    *,
    parser: Any = None,
    discourse: Any = None,
    release: Any = None,
    deferred: bool = False,
    text: str = "",
    entities: Sequence[EntitySnapshot] = (),
) -> list[Candidate]:
    """Situation question, need/routine and the direct command reading of
    one turn (need ↔ question since 7.5.0; routine ↔ scene name and the
    direct command since 7.7). An explicitly named device is evidence."""
    candidates: list[Candidate] = []
    if view is not None:
        candidates.append(Candidate("situation", speech_act, Authority.PARSER, Effect.READ, payload=view))
    need_candidate = _candidate("need", speech_act, _authority_of(need, Authority.NEED), need, explicit=False)
    if need_candidate is not None:
        candidates.append(need_candidate)
    parser_candidate = (
        _candidate(
            "parser", speech_act, _authority_of(parser, Authority.PARSER), parser,
            explicit=names_its_targets(text, parser, entities),
        )
        if parser is not None and _plans(parser)
        else None
    )
    if parser_candidate is not None:
        candidates.append(parser_candidate)
    for source, payload, authority in (
        ("discourse", discourse, Authority.DISCOURSE),
        ("release", release, Authority.PARSER),
    ):
        candidate = _candidate(source, speech_act, authority, payload, explicit=False)
        if candidate is not None and candidate.executable:
            candidates.append(candidate)
    return _deferred(candidates) if deferred else candidates


def _deferred(candidates: list[Candidate]) -> list[Candidate]:
    """Time-bound or conditional meaning never writes now: every executable
    reading becomes a deferred one (automation, reminder, timer)."""
    return [
        Candidate(item.source, item.speech_act, item.authority, Effect.DEFER, item.targets,
                  item.operations, item.risk, item.residue, item.explicit_device, item.evidence,
                  item.payload)
        if item.effect is Effect.WRITE else item
        for item in candidates
    ]


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
    parsed = engine.understand(text, entity_list).payload
    parser = _candidate("parser", speech_act, _authority_of(parsed, Authority.PARSER), parsed, explicit=explicit)
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
    need_candidate = _candidate("need", speech_act, _authority_of(need, Authority.NEED), need, explicit=False)
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
        # Time-bound or conditional meaning never writes now (7.3.3 Q5).
        candidates = _deferred(candidates)
    return candidates, explicit_question


__all__ = (
    "collect_candidates", "complete_command_candidate", "context_candidates", "dialog_evidence",
    "need_query_candidates",
)
