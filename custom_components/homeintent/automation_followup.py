"""Repetition and escalation inside a monitoring request (7.9 W5).

Two reusable constructions, read into spans only - the meaning comes from
the established readers:

``REPEAT``
    "(erinnere mich|melde dich|…) alle N Minuten, bis|solange BOUND":
    the notification is repeated every N minutes while the situation BOUND
    names lasts ("bis es zu ist" = while it is open; "solange es offen ist").
``ESCALATE``
    "BASE, und wenn REF nach N Minuten (immer) noch STATE ist, NOTIFY2":
    the base request, then - only if the situation still holds after N
    minutes - a second notification (usually to another person).

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Sequence

from .missing_part import MissingPart, PartRequest
from .automation_composition import (
    CompositionOutcome,
    OutcomeKind,
    Readers,
    compose_event_automation,
    interpret_event_clause,
)
from .automation_grounding import read_subject, subject_candidates
from .automation_language import EventReference
from .automation_monitoring import open_monitoring_object
from .automation_notification import notification_action
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.action_model import ActionModel, ActionType
from .nlu.automation_model import AutomationModel, TriggerModel, TriggerType
from .nlu.automation_validator import validate_automation
from .nlu.condition_model import ConditionModel, ConditionNode, ConditionType
from .nlu.ha_automation_generator import resolve_target_entities
from .nlu.semantic_state import SemanticState
from .notification_language import (
    describe_event,
    describe_holding_state,
    parse_notification_clause,
)
from .nlu.normalize import german_number

DEFAULT_MAX_REPEATS = 12

_UNIT = r"(?P<unit>minuten?|stunden?)"
# A second notification verb joined by "und" before the interval repeats the
# first one ("Melde dich, wenn …, und erinnere mich alle 10 Minuten, …").
_JOINED_VERB = (
    r"(?:\s*,?\s*und\s+(?P<joined>erinnere\s+mich|melde\s+dich|sag\s+mir(?:\s+wieder)?\s+bescheid|"
    r"benachrichtige\s+mich|warne\s+mich|informiere\s+mich)(?:\s+(?:dann|danach|wieder|weiter))?\s+)?"
)
_REPEAT_RE = re.compile(
    _JOINED_VERB
    + r"\s*,?\s*(?:und\s+)?(?:(?:dann|danach)\s+)?(?:weiter(?:hin)?\s+)?"
    r"(?:alle\s+(?P<count>\d+|[a-zäöüß]+)\s+" + _UNIT + r"|jede(?:n)?\s+(?P<single>minute|stunde)"
    r"|(?P<adverb>minütlich|stündlich))(?:\s+(?:wieder|erneut))?\s*,?\s+"
    r"(?P<kind>bis|solange)\s+(?P<bound>[^,.!?]+)",
    re.IGNORECASE,
)
_VAGUE_RE = re.compile(
    r"\b(?:etwas|was|irgendwas|irgendetwas|irgendwas)\s+(?:ungewöhnliche|komische|seltsame|"
    r"merkwürdige|auffällige|verdächtige|unerwartete|besondere|eigenartige|sonderbare)s\b",
    re.IGNORECASE,
)
# "bei Auffälligkeiten" (7.9.1 A7): the same vague situation, as a noun
# after "bei" - a closed word class, read token by token.
_VAGUE_NOUNS = frozenset({
    "auffälligkeiten", "unregelmäßigkeiten", "besonderheiten", "ungewöhnlichem", "auffälligem",
    "seltsamem", "komischem", "merkwürdigem", "verdächtigem",
})


def _names_vague_situation(text: str) -> bool:
    if _VAGUE_RE.search(text):
        return True
    keys = [word.strip(",.;:!?").lower() for word in text.split()]
    return any(key == "bei" and following in _VAGUE_NOUNS for key, following in zip(keys, keys[1:]))
_WATCH_WORD_RE = re.compile(r"\b(?:überwach|beobacht|acht|pass\s+auf|behalt)\w*", re.IGNORECASE)
_LOCK_ACTION_RE = re.compile(
    r"\b(?:schließ\w*|sperr\w*)\s+(?:\S+\s+){0,3}?ab\b|\bverriegel\w*|\bverriegle\b|\babschließen\b",
    re.IGNORECASE,
)
_INTERVAL_RE = re.compile(
    r"\balle\s+(?P<count>\d+|[a-zäöüß]+)\s+(?P<unit>sekunden?|minuten?|stunden?)\b"
    r"|\bjede[n]?\s+(?P<single>sekunde|minute|stunde)\b"
    r"|\b(?P<adverb>sekündlich|minütlich|stündlich)\b",
    re.IGNORECASE,
)
# The bound of a repetition, anywhere after the interval: "…, solange die
# Haustür offen ist" (7.9.1 A7) - the interval and the bound need not touch.
_BOUND_RE = re.compile(r"\b(?P<kind>bis|solange)\s+(?P<bound>[^,.!?]+)", re.IGNORECASE)
_EVENT_WORD_RE = re.compile(r"\b(?:wenn|sobald|falls|bis|solange)\b", re.IGNORECASE)
_REMIND_VERB_RE = re.compile(r"\berinnere\s+mich\b", re.IGNORECASE)
_ESCALATE_RE = re.compile(
    r"^(?P<base>.+?)\s*,?\s+und\s+(?:wenn|falls)\s+(?P<ref>.+?)\s+nach\s+"
    r"(?P<count>\d+|[a-zäöüß]+)\s+" + _UNIT + r"\s+(?:immer\s+)?noch\s+(?P<state>[^,]+?)\s*"
    r"(?:ist|sind|steht|bleibt)\s*,\s*(?P<notify>.+?)[.!]?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RepeatSpan:
    text_without: str  # the request with the repetition removed
    interval_seconds: int
    until: bool  # "bis BOUND" (True) or "solange BOUND" (False)
    bound: str


@dataclass(frozen=True)
class EscalationSpan:
    base: str
    reference: str
    seconds: int
    state: str
    notification: str


def _seconds(count: str, unit: str) -> int | None:
    raw = count.casefold()
    amount = 1 if raw in {"ein", "eine", "einer", "einem"} else (
        int(raw) if raw.isdigit() else german_number(raw)
    )
    if amount is None or amount <= 0:
        return None
    key = unit.casefold()
    return int(amount) * (3600 if key.startswith("stunde") else 1 if key.startswith("sekunde") else 60)


def _interval_seconds(match: re.Match[str]) -> int | None:
    single = (match.group("single") or match.group("adverb") or "").casefold()
    if single:
        return 3600 if single.startswith(("stunde", "stünd")) else 1 if single.startswith(("sekund", "sekünd")) else 60
    return _seconds(match.group("count"), match.group("unit"))


def split_repeat(text: str) -> RepeatSpan | None:
    match = _REPEAT_RE.search(text)
    if match is None:
        return _split_separated_repeat(text)
    single = (match.group("single") or match.group("adverb") or "").casefold()
    seconds = (
        3600 if single.startswith(("stunde", "stünd")) else 60 if single
        else _seconds(match.group("count"), match.group("unit"))
    )
    if seconds is None:
        return None
    rest = (text[:match.start()] + text[match.end():]).strip(" ,")
    if match.group("joined") is not None and parse_notification_clause_start(rest) is None:
        # The joined verb was the only notification: keep it.
        rest = f"{rest}, {match.group('joined')}"
    # "erinnere mich" in a repetition is the notification itself, not a
    # timed reminder with its own text.
    rest = _REMIND_VERB_RE.sub("melde dich", rest)
    return RepeatSpan(rest, seconds, match.group("kind").casefold() == "bis", match.group("bound").strip())


def _split_separated_repeat(text: str) -> RepeatSpan | None:
    """"Schick mir alle 5 Minuten eine Nachricht, solange die Haustür offen
    ist": interval and bound with the notification between them (7.9.1 A7).
    Also reports intervals below a minute, which the caller refuses."""
    interval = _INTERVAL_RE.search(text)
    if interval is None:
        return None
    bound = _BOUND_RE.search(text, interval.end())
    if bound is None:
        return None
    seconds = _interval_seconds(interval)
    if seconds is None:
        return None
    rest = (text[:interval.start()] + text[interval.end():bound.start()] + text[bound.end():])
    rest = " ".join(rest.split()).strip(" ,")
    rest = _REMIND_VERB_RE.sub("melde dich", rest)
    return RepeatSpan(rest, seconds, bound.group("kind").casefold() == "bis", bound.group("bound").strip())


_NOTIFY_WORD_RE = re.compile(
    r"\b(?:melde|meld|sag|gib|benachrichtige|informiere|warne|erinnere|schick|schreib)\w*\b",
    re.IGNORECASE,
)


def parse_notification_clause_start(text: str) -> str | None:
    """The first notification verb of a request, if it has one."""
    match = _NOTIFY_WORD_RE.search(text)
    return match.group(0) if match is not None else None


def split_escalation(text: str) -> EscalationSpan | None:
    match = _ESCALATE_RE.match(text.strip())
    if match is None:
        return None
    seconds = _seconds(match.group("count"), match.group("unit"))
    if seconds is None:
        return None
    return EscalationSpan(
        match.group("base").strip(" ,"), match.group("ref").strip(), seconds,
        match.group("state").strip(), match.group("notify").strip(" ,."),
    )


# --- composition ----------------------------------------------------------------------
# The builders reuse the sentence-based reader for the base request and the
# bound, so every word keeps its one meaning.

_COMPLEMENT = {
    SemanticState.ON: SemanticState.OFF, SemanticState.OFF: SemanticState.ON,
    SemanticState.OPEN: SemanticState.CLOSED, SemanticState.CLOSED: SemanticState.OPEN,
}


def _antecedent(model: AutomationModel, entities: Sequence[EntitySnapshot]) -> EventReference | None:
    """The base event's device as the antecedent of "es/sie/er"."""
    trigger = model.triggers[0] if model.triggers else None
    if trigger is None or trigger.target is None:
        return None
    members = resolve_target_entities(trigger.target, list(entities))
    if len(members) != 1:
        return None
    return EventReference(tuple(members[0].friendly_name.split()))


def _state_reading(
    text: str, reference: EventReference | None, entities: Sequence[EntitySnapshot], readers: Readers
) -> TriggerModel | CompositionOutcome:
    """"es zu ist" / "das Garagentor offen ist" -> one typed state."""
    reading = interpret_event_clause(text, "wenn", entities, readers.trigger, readers.condition, reference)
    trigger = reading.trigger
    if (
        trigger is None or trigger.type is not TriggerType.STATE or trigger.state not in _COMPLEMENT
        or reading.conditions or trigger.target is None
    ):
        grounded = reading.grounded
        question = grounded.question if grounded is not None else None
        return CompositionOutcome(
            OutcomeKind.CLARIFY if question else OutcomeKind.UNSUPPORTED,
            speech=question or f"„{text}“ kann ich keinem Gerätezustand zuordnen.",
            part=(
                PartRequest(grounded.missing, question or "", replaces=grounded.roles.subject_words)
                if grounded is not None and grounded.missing is not None and grounded.roles is not None
                else None
            ),
        )
    return trigger


def _holds(trigger: TriggerModel, state: SemanticState) -> ConditionNode:
    return ConditionNode(condition=ConditionModel(type=ConditionType.STATE, target=trigger.target, state=state))


def compose_with_followups(
    raw_text: str, entities: Sequence[EntitySnapshot], readers: Readers
) -> CompositionOutcome | None:
    """The sentence reader plus repetition and escalation (7.9 W5) and the
    open monitoring request (W6)."""
    monitored = open_monitoring_object(raw_text)
    if monitored is not None:
        return _open_request(monitored, entities)
    if _names_vague_situation(raw_text) and (
        _NOTIFY_WORD_RE.search(raw_text) or _WATCH_WORD_RE.search(raw_text)
    ):
        # "Melde dich, wenn etwas Ungewöhnliches passiert" (7.9 W7): an
        # indefinite pronoun with an evaluative adjective names no event.
        return CompositionOutcome(OutcomeKind.CLARIFY, speech="", vague_situation=True)
    escalation = split_escalation(raw_text)
    if escalation is not None:
        return _compose_escalation(escalation, entities, readers)
    repeat = split_repeat(raw_text)
    if repeat is not None and repeat.interval_seconds < 60:
        # Never faster than once a minute (7.9 W5) - said, not "nicht verstanden".
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED,
            speech=(
                "So oft melde ich mich nicht: höchstens einmal pro Minute. Sag zum Beispiel "
                "„jede Minute“ oder „alle 5 Minuten“."
            ),
        )
    if repeat is not None:
        return _compose_repeat(repeat, raw_text, entities, readers)
    if _INTERVAL_RE.search(raw_text) and _EVENT_WORD_RE.search(raw_text):
        # "schalte das Licht alle 10 Minuten ein, bis …": a spoken interval
        # the readers would not use is never dropped silently.
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED,
            speech=(
                "Wiederholen kann ich nur Benachrichtigungen, zum Beispiel: „Wenn das Garagentor "
                "offen ist, erinnere mich alle 10 Minuten, bis es zu ist.“"
            ),
        )
    outcome = compose_event_automation(raw_text, entities, readers)
    if (outcome is None or outcome.kind is OutcomeKind.UNSUPPORTED) and _LOCK_ACTION_RE.search(raw_text):
        # "… und schließ sie ab, wenn …": locks stay confirmed in person.
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED,
            speech=(
                "Schlösser schließe oder öffne ich nicht in einer Automation – das bestätigst du "
                "immer selbst. Ich kann dich stattdessen benachrichtigen, zum Beispiel: „Überwache "
                "die Haustür und melde dich, wenn sie offen ist.“"
            ),
        )
    return outcome


def _open_request(words: tuple[str, ...], entities: Sequence[EntitySnapshot]) -> CompositionOutcome:
    """"Überwache das Garagentor." - ask for the event (7.9 W6)."""
    subject = read_subject(words, entities)
    if subject.noun is not None:
        found = subject_candidates(subject, entities)
    else:
        found = [
            entity for entity in entities
            if normalize_for_compare(entity.friendly_name) == normalize_for_compare(" ".join(
                word for word in words if word.casefold() not in {"der", "die", "das", "den", "dem"}
            ))
        ]
    spoken = " ".join(word for word in words if word.casefold() not in {"der", "die", "das", "den", "dem"})
    if not found:
        return CompositionOutcome(
            OutcomeKind.CLARIFY,
            speech=f"Ich finde kein Gerät „{spoken}“. Welches Gerät soll ich überwachen?",
        )
    return CompositionOutcome(
        OutcomeKind.CLARIFY,
        speech=(
            f"Wann soll ich mich zu „{spoken}“ melden? Sag zum Beispiel: „Wenn es länger als "
            "10 Minuten offen ist.“"
        ),
        monitored_object=words,
    )


def _base(text: str, entities: Sequence[EntitySnapshot], readers: Readers) -> CompositionOutcome | None:
    outcome = compose_event_automation(text, entities, readers)
    if outcome is None or outcome.kind is not OutcomeKind.AUTOMATION or outcome.model is None:
        return outcome
    notifies = [
        step for step in outcome.model.actions
        if isinstance(step, ActionModel) and step.type is ActionType.NOTIFY
    ]
    if len(notifies) != len(outcome.model.actions) or not notifies:
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED,
            speech="Wiederholen und Nachfassen kann ich nur Benachrichtigungen, keine Geräteaktionen.",
        )
    return outcome


def _finish(model: AutomationModel, outcome: CompositionOutcome) -> CompositionOutcome:
    """The completed model; the canonical meaning gains the follow-ups."""
    canonical = outcome.canonical
    if canonical is not None:
        for step in model.actions:
            if not isinstance(step, ActionModel):
                continue
            if step.type is ActionType.REPEAT:
                canonical = replace(
                    canonical, repeat_interval_seconds=step.delay_seconds, max_repeats=step.max_repeats
                )
            if step.type is ActionType.ESCALATE:
                second = next((s for s in step.then_steps if isinstance(s, ActionModel)), None)
                recipient = second.recipient if second is not None else None
                canonical = replace(
                    canonical, escalation_seconds=step.timeout_seconds,
                    escalation_recipient=(
                        (recipient.label or recipient.kind.name).casefold() if recipient is not None else None
                    ),
                )
    return replace(outcome, model=model, validation_error=validate_automation(model), canonical=canonical)


def _compose_repeat(
    span: RepeatSpan, raw_text: str, entities: Sequence[EntitySnapshot], readers: Readers
) -> CompositionOutcome | None:
    has_event = any(
        f" {word} " in f" {span.text_without.casefold()} " for word in ("wenn", "sobald", "falls")
    ) or span.text_without.casefold().startswith(("wenn ", "sobald ", "falls "))
    if has_event:
        base = _base(span.text_without, entities, readers)
        if base is None or base.kind is not OutcomeKind.AUTOMATION or base.model is None:
            return base
        model = base.model
        reference = _antecedent(model, entities)
    else:
        base, model, reference = None, None, None
    bound = _state_reading(span.bound, reference, entities, readers)
    if isinstance(bound, CompositionOutcome):
        return bound
    assert bound.state is not None
    lasting = _COMPLEMENT[bound.state] if span.until else bound.state
    if model is None or base is None:
        # "Erinnere mich alle 10 Minuten, bis das Garagentor zu ist": the
        # situation starts when the device reaches the lasting state; whether
        # only now or every time is asked before the preview.
        clause = parse_notification_clause("melde dich")
        assert clause is not None
        start = TriggerModel(type=TriggerType.STATE, target=bound.target, state=lasting)
        phrase = describe_event(start, entities)
        message = phrase.sentence if phrase is not None else "Erinnerung."
        notify = notification_action(clause, message, tuple(entities))
        if notify is None:
            return None
        model = AutomationModel(triggers=(start,), actions=(notify,), source_text=raw_text, ask_start=True)
        base = CompositionOutcome(OutcomeKind.AUTOMATION, model=model)
    # A default message describes the lasting state, not the moment it
    # began: it is sent again and again ("Das Garagentor ist noch offen.").
    lasting_trigger = TriggerModel(type=TriggerType.STATE, target=bound.target, state=lasting)
    holding = describe_holding_state(lasting_trigger, entities)
    started = describe_event(model.triggers[0], entities) if model.triggers else None
    steps = tuple(
        replace(step, message=holding.sentence.replace(" ist ", " ist noch ", 1))
        if holding is not None and isinstance(step, ActionModel)
        and (started is None or step.message == started.sentence)
        else step
        for step in model.actions
    )
    repeat = ActionModel(
        type=ActionType.REPEAT,
        if_condition=_holds(bound, lasting),
        then_steps=steps,
        delay_seconds=span.interval_seconds,
        max_repeats=DEFAULT_MAX_REPEATS,
    )
    repeated = replace(model, actions=(repeat,), source_text=raw_text)
    return _finish(repeated, base)


def _compose_escalation(
    span: EscalationSpan, entities: Sequence[EntitySnapshot], readers: Readers
) -> CompositionOutcome | None:
    base = _base(span.base, entities, readers)
    if base is None or base.kind is not OutcomeKind.AUTOMATION or base.model is None:
        return base
    model = base.model
    still = _state_reading(f"{span.reference} {span.state} ist", _antecedent(model, entities), entities, readers)
    if isinstance(still, CompositionOutcome):
        return still
    assert still.state is not None
    clause = parse_notification_clause(span.notification)
    if clause is None or clause.reminder or clause.test:
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED,
            speech=f"„{span.notification}“ verstehe ich nicht als Benachrichtigung.",
        )
    lasting = replace(still, for_seconds=span.seconds)
    phrase = describe_event(lasting, entities)
    message = clause.message or (phrase.sentence if phrase is not None else "Der Zustand hält noch an.")
    second = notification_action(clause, message, tuple(entities))
    if second is None:
        who = clause.recipient_name or "diese Person"
        question = f"Ich finde kein eindeutiges Benachrichtigungsziel für {who}. Wen soll ich benachrichtigen?"
        return CompositionOutcome(
            OutcomeKind.CLARIFY,
            speech=question,
            part=(
                PartRequest(MissingPart.RECIPIENT, question, replaces=tuple(who.split()))
                if clause.recipient_name else None
            ),
        )
    escalate = ActionModel(
        type=ActionType.ESCALATE,
        wait_condition=_holds(still, _COMPLEMENT[still.state]),
        timeout_seconds=span.seconds,
        then_steps=(second,),
    )
    return _finish(replace(model, actions=(*model.actions, escalate)), base)


__all__ = (
    "DEFAULT_MAX_REPEATS", "EscalationSpan", "RepeatSpan", "compose_with_followups",
    "split_escalation", "split_repeat",
)
