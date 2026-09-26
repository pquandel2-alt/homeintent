"""Event clause + notification clause -> one canonical automation (7.2.0).

This is the projection step of the compositional automation reader::

    utterance
      -> prepare_automation_text()          (repairs, STT rejoins, normalize)
      -> segment_event_automation()         (EVENT clause + ACTION clause)
      -> read_event_roles() / ground_event() (typed roles -> TriggerModel)
      -> read_actions()                     (notification authority / action parsers)
      -> AutomationModel                    (existing validator / preview / writer)

The *canonical meaning* (:class:`CanonicalEventNotification`) is what every
paraphrase of one request must share; paraphrase tests compare it
directly.  Nothing here synthesizes German text for another parser: the
established trigger and condition parsers only ever receive unchanged
source spans (a connector plus its clause) for the event kinds this module
does not type itself - time, sun, presence, weekday.

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Callable, Sequence

from .automation_grounding import (
    GroundedEvent,
    GroundingStatus,
    choose_candidate,
    ground_event,
    read_subject,
    restrict_to,
    target_for,
)
from .automation_language import (
    ConditionSpan,
    EventRoles,
    EventActionFrame,
    TemporalEvent,
    condition_split_candidates,
    is_notification_text,
    looks_like_device_action,
    prepare_automation_text,
    protected_message_spans,
    read_event_roles,
    segment_event_automation,
)
from .automation_notification import notification_action
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.action_model import ActionGroup, ActionModel, ActionType, NotificationRecipientKind
from .nlu.automation_model import AutomationModel, NumericComparator, TriggerModel, TriggerType
from .nlu.automation_validator import AutomationValidationError, validate_automation
from .nlu.condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator
from .nlu.measurement import MeasurementProperty, TravelDirection
from .nlu.semantic_state import SemanticState
from .nlu.normalize import german_number
from .notification_language import NotificationClause, parse_notification_clause, trigger_message

_LOGGER = logging.getLogger(__name__)

TriggerReader = Callable[[str], "TriggerModel | None"]
ConditionReader = Callable[[str], "ConditionNode | None"]


class OutcomeKind(Enum):
    AUTOMATION = auto()
    CLARIFY = auto()
    UNSUPPORTED = auto()


@dataclass(frozen=True)
class CanonicalEvent:
    """Word-order independent meaning of the event."""

    trigger_type: TriggerType
    entity_ids: tuple[str, ...]
    measurement: MeasurementProperty | None = None
    comparator: NumericComparator | None = None
    value: float | None = None
    state: SemanticState | None = None
    direction: TravelDirection | None = None
    for_seconds: int | None = None


@dataclass(frozen=True)
class CanonicalEventNotification:
    """EVENT_NOTIFICATION: who is told what, when."""

    recipient: NotificationRecipientKind
    recipient_name: str | None
    event: CanonicalEvent
    condition_count: int
    explicit_message: str | None


@dataclass(frozen=True)
class EventClarification:
    """Enough state to finish the same draft after "Die linke."."""

    grounded: GroundedEvent
    action_text: str
    trailing_message: str | None
    conditions: tuple[ConditionNode, ...]
    source_text: str


@dataclass(frozen=True)
class CompositionTrace:
    """Privacy-safe diagnostics: structure, never the utterance itself."""

    route: str
    order: str | None = None
    connector: str | None = None
    grounding: str | None = None
    reason: str | None = None
    repaired: bool = False
    event_words: int = 0


@dataclass(frozen=True)
class CompositionOutcome:
    kind: OutcomeKind
    model: AutomationModel | None = None
    validation_error: AutomationValidationError | None = None
    speech: str | None = None
    clarification: EventClarification | None = None
    canonical: CanonicalEventNotification | None = None
    trace: CompositionTrace = field(default_factory=lambda: CompositionTrace("none"))


@dataclass(frozen=True)
class EventInterpretation:
    """One event clause read into a trigger plus embedded conditions."""

    trigger: TriggerModel | None
    conditions: tuple[ConditionNode, ...] = ()
    grounded: GroundedEvent | None = None
    typed: bool = False
    alternatives: tuple[TriggerModel, ...] = ()


_UNSUPPORTED_TEXT = {
    "relative_change": (
        "Ich habe verstanden, dass du bei einer relativen Änderung benachrichtigt werden "
        "möchtest (zum Beispiel „um 2 Grad“). Solche Änderungs-Auslöser kann ich noch nicht "
        "sicher erstellen; nenne mir bitte einen festen Wert."
    ),
    "aggregate": (
        "„Alle …“ beschreibt einen Gesamtzustand und kein einzelnes Ereignis. Diesen Auslöser "
        "kann ich noch nicht sicher erstellen."
    ),
    "no_numeric_property": (
        "Für dieses Gerät kenne ich keinen sicheren Prozent- oder Messwert, auf den ich "
        "reagieren könnte."
    ),
    "unit_mismatch": "Die genannte Einheit passt nicht zu diesem Sensor.",
    "half_without_cover": "„Halb“ kann ich nur für Rollläden als Position verstehen.",
    "bare_number": "Bitte nenne die Einheit, zum Beispiel „50 Prozent“.",
    "out_of_range": "Dieser Wert liegt außerhalb des möglichen Bereichs von 0 bis 100 Prozent.",
    "direction_without_position": (
        "Eine Fahrtrichtung kann ich nur zusammen mit einer Rollladenposition auswerten."
    ),
    "mixed_domains": "Die genannten Geräte haben keinen gemeinsamen Messwert.",
}
_GENERIC_UNSUPPORTED = (
    "Ich habe verstanden, dass du bei einem Ereignis benachrichtigt werden möchtest, "
    "konnte das Ereignis aber keinem Gerät oder Zeitpunkt sicher zuordnen."
)


def log_composition_trace(trace: CompositionTrace) -> None:
    """Developer diagnostics (spec §62): structure only, never the utterance.

    Enabled with ``logger: logs: custom_components.homeintent.automation_composition: debug``.
    """
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "automation composition route=%s order=%s connector=%s grounding=%s "
            "reason=%s repaired=%s event_words=%d",
            trace.route, trace.order, trace.connector, trace.grounding,
            trace.reason, trace.repaired, trace.event_words,
        )


def unsupported_text(reason: str | None) -> str:
    return _UNSUPPORTED_TEXT.get(reason or "", _GENERIC_UNSUPPORTED)


def _typed_condition(text: str, entities: Sequence[EntitySnapshot]) -> ConditionNode | None:
    """"das Wohnzimmerlicht aus ist": the same typed grounding as triggers,
    projected into a state or strict numeric condition."""
    grounded = ground_event(read_event_roles(text), entities)
    trigger = grounded.trigger
    if grounded.status is not GroundingStatus.RESOLVED or trigger is None:
        return None
    if trigger.type is TriggerType.STATE and trigger.for_seconds is None:
        return ConditionNode(condition=ConditionModel(
            type=ConditionType.STATE, target=trigger.target, state=trigger.state
        ))
    if (
        trigger.type is TriggerType.NUMERIC_STATE
        and trigger.measurement is None
        and trigger.comparator in {NumericComparator.ABOVE, NumericComparator.BELOW}
    ):
        return ConditionNode(condition=ConditionModel(
            type=ConditionType.NUMERIC, target=trigger.target,
            comparator=trigger.comparator, threshold=trigger.threshold,
        ))
    return None


def _conditions_for(
    spans: Sequence[ConditionSpan],
    parse_condition: ConditionReader,
    entities: Sequence[EntitySnapshot] = (),
) -> tuple[ConditionNode, ...] | None:
    nodes: list[ConditionNode] = []
    for span in spans:
        if span.weekdays:
            nodes.append(ConditionNode(condition=ConditionModel(
                type=ConditionType.WEEKDAY, weekdays=span.weekdays
            )))
            continue
        node = parse_condition(span.text) or _typed_condition(span.text, entities)
        if node is None:
            return None
        nodes.append(ConditionNode(operator=LogicalOperator.NOT, children=(node,)) if span.negated else node)
    return tuple(nodes)


def _untyped_event(trigger: TriggerModel | None) -> TriggerModel | None:
    """The grammar parser may read time, sun and presence phrasings; a device
    state or number it finds through its free name slot is discarded - the
    typed reader already rejected that device reading."""
    if trigger is None or trigger.type in {TriggerType.STATE, TriggerType.NUMERIC_STATE}:
        return None
    return trigger


def interpret_event_clause(
    event_text: str,
    connector: str,
    entities: Sequence[EntitySnapshot],
    parse_trigger: TriggerReader,
    parse_condition: ConditionReader,
) -> EventInterpretation:
    """Typed reading first; established parsers only for untyped event kinds."""
    roles = read_event_roles(event_text)
    embedded = _conditions_for(roles.conditions, parse_condition, entities)
    if embedded is None:
        return EventInterpretation(None)
    grounded = ground_event(roles, entities)
    if grounded.status is GroundingStatus.RESOLVED:
        return EventInterpretation(grounded.trigger, embedded, grounded, typed=True)
    # "X und niemand zuhause ist" / "X, aber nur wenn Y": the event plus a
    # condition the established condition parser has to accept verbatim.
    for left, span in condition_split_candidates(roles.source):
        condition = _conditions_for((span,), parse_condition, entities)
        if condition is None:
            continue
        left_roles = read_event_roles(left)
        left_conditions = _conditions_for(left_roles.conditions, parse_condition, entities)
        if left_conditions is None:
            continue
        left_grounded = ground_event(left_roles, entities)
        if left_grounded.status is GroundingStatus.RESOLVED:
            return EventInterpretation(
                left_grounded.trigger, (*left_conditions, *condition), left_grounded, typed=True
            )
        if left_grounded.status is GroundingStatus.NOT_APPLICABLE:
            legacy = _untyped_event(parse_trigger(f"{connector} {left}"))
            if legacy is not None:
                return EventInterpretation(legacy, (*left_conditions, *condition))
        elif left_grounded.status is not GroundingStatus.NOT_FOUND:
            return EventInterpretation(
                None, (*left_conditions, *condition), left_grounded
            )
    if grounded.status is GroundingStatus.NOT_APPLICABLE:
        legacy = _untyped_event(parse_trigger(f"{connector} {roles.source}"))
        if legacy is not None:
            return EventInterpretation(legacy, embedded)
        if roles.conditions:
            # The prepositional condition may belong to the untyped trigger.
            legacy = _untyped_event(parse_trigger(f"{connector} {event_text.strip(' ,.!?')}"))
            if legacy is not None:
                return EventInterpretation(legacy, ())
    return EventInterpretation(None, embedded, grounded)


def temporal_interpretation(temporal: TemporalEvent) -> EventInterpretation:
    """Project a sun/clock phrase straight into the existing trigger model."""
    if temporal.sun_event is not None:
        trigger = TriggerModel(
            type=TriggerType.SUN, sun_event=temporal.sun_event, offset_minutes=temporal.offset_minutes
        )
    else:
        trigger = TriggerModel(
            type=TriggerType.TIME, time_hour=temporal.hour, time_minute=temporal.minute or 0
        )
    conditions: tuple[ConditionNode, ...] = ()
    if temporal.weekdays:
        conditions = (ConditionNode(condition=ConditionModel(
            type=ConditionType.WEEKDAY, weekdays=temporal.weekdays
        )),)
    return EventInterpretation(trigger, conditions, typed=True)


def _canonical(
    trigger: TriggerModel,
    clause: NotificationClause,
    conditions: tuple[ConditionNode, ...],
    grounded: GroundedEvent | None,
) -> CanonicalEventNotification:
    entity_ids = tuple(sorted(entity.entity_id for entity in grounded.candidates)) if grounded else ()
    return CanonicalEventNotification(
        recipient=clause.recipient_kind,
        recipient_name=(clause.recipient_name or "").casefold() or None,
        event=CanonicalEvent(
            trigger_type=trigger.type,
            entity_ids=entity_ids,
            measurement=trigger.measurement,
            comparator=trigger.comparator,
            value=trigger.threshold,
            state=trigger.state,
            direction=trigger.direction,
            for_seconds=trigger.for_seconds,
        ),
        condition_count=len(conditions),
        explicit_message=clause.message,
    )


ActionStep = "ActionModel | ActionGroup"


@dataclass(frozen=True)
class Readers:
    """The established parsers, bound to the caller's context.

    They only ever receive unchanged source spans.
    """

    trigger: TriggerReader
    condition: ConditionReader
    action: Callable[[str], "tuple[ActionModel | ActionGroup, ...] | None"]


_CHUNK_SPLIT_RE = re.compile(
    r"\s*,?\s+(?:und\s+dann|und|dann|danach|anschließend)\s+(?!dass\b)", re.IGNORECASE
)
_REVERT_RE = re.compile(
    r"^(?:(?:und\s+)?(?:nach|in)\s+(?P<amount>\d+|[a-zäöüß]+)\s+(?P<unit>sekunden?|minuten?|stunden?))"
    r"\s+(?:wieder\s+)?(?P<state>aus|an|ein|zu|auf)(?:schalten|machen)?$",
    re.IGNORECASE,
)


def _flatten(steps: Sequence[ActionModel | ActionGroup]) -> list[ActionModel]:
    flat: list[ActionModel] = []
    for step in steps:
        if isinstance(step, ActionGroup):
            flat.extend(_flatten(step.steps))
        else:
            flat.append(step)
    return flat


def split_action_chunks(text: str) -> list[str]:
    """Top-level coordinated action clauses; dictated message text is inert."""
    protected = protected_message_spans(text)
    chunks: list[str] = []
    last = 0
    for match in _CHUNK_SPLIT_RE.finditer(text):
        if any(begin <= match.start() < end for begin, end in protected):
            continue
        chunks.append(text[last:match.start()])
        last = match.end()
    chunks.append(text[last:])
    return [chunk.strip(" ,.") for chunk in chunks if chunk.strip(" ,.")]


def _revert_chunk(
    chunk: str, previous: ActionModel | None
) -> tuple[ActionModel, ActionModel] | None:
    """"nach 3 Minuten wieder aus" after a device action: delay + inverse."""
    match = _REVERT_RE.match(chunk)
    if match is None or previous is None or previous.target is None:
        return None
    raw = match.group("amount")
    amount = int(raw) if raw.isdigit() else german_number(raw)
    if amount is None or amount <= 0:
        return None
    unit = match.group("unit").casefold()
    seconds = amount * (1 if unit.startswith("sekunde") else 60 if unit.startswith("minute") else 3600)
    state = match.group("state").casefold()
    kind = ActionType.TURN_OFF if state in {"aus", "zu"} else ActionType.TURN_ON
    return (
        ActionModel(type=ActionType.DELAY, delay_seconds=seconds),
        ActionModel(type=kind, target=previous.target),
    )


def _elliptic_chunk(
    chunk: str, previous: ActionModel | None, entities: Sequence[EntitySnapshot]
) -> ActionModel | None:
    """"... und den Wohnzimmer Rollladen": the previous verb, a new object."""
    if previous is None or previous.target is None or previous.type not in {
        ActionType.TURN_ON, ActionType.TURN_OFF, ActionType.SET_POSITION, ActionType.SET_BRIGHTNESS,
    }:
        return None
    words = chunk.split()
    if not words or any(word.casefold() in _NON_NOUN_PHRASE for word in words):
        return None
    subject = read_subject(words, entities)
    if subject.noun is None or subject.unknown_location is not None:
        return None
    candidates = [
        entity for entity in entities
        if entity.domain == subject.noun.domain
        and (subject.noun.device_class is None or entity.device_class == subject.noun.device_class)
        and (subject.area_id is None or entity.area_id == subject.area_id)
    ]
    if subject.modifiers:
        candidates = [
            entity for entity in candidates
            if all(any(modifier in name for name in _names(entity)) for modifier in subject.modifiers)
        ]
    if len(candidates) != 1:
        return None
    return replace(previous, target=target_for(candidates, entities))


_NON_NOUN_PHRASE = frozenset({
    "an", "aus", "ein", "auf", "zu", "hoch", "runter", "wieder", "nach", "mach", "schalte",
    "fahre", "öffne", "schließe", "bitte", "mir", "mich",
})


def _names(entity: EntitySnapshot) -> tuple[str, ...]:
    return tuple(normalize_for_compare(name) for name in (entity.friendly_name, *entity.aliases) if name)


@dataclass(frozen=True)
class ActionReading:
    steps: tuple[ActionModel | ActionGroup, ...]
    notification: NotificationClause | None  # set when the clause is one notification
    unresolved_recipient: str | None = None


def read_actions(
    text: str,
    trigger: TriggerModel | None,
    entities: Sequence[EntitySnapshot],
    readers: Readers,
    trailing_message: str | None = None,
) -> ActionReading | None:
    """Every coordinated action chunk must be understood - none is dropped."""
    whole_notification = parse_notification_clause(text.strip(" ,.")) is not None
    chunks = [text.strip(" ,.")] if whole_notification else split_action_chunks(text)
    if not chunks:
        return None
    steps: list[ActionModel | ActionGroup] = []
    only_clause: NotificationClause | None = None
    previous: ActionModel | None = None
    for chunk in chunks:
        clause = parse_notification_clause(chunk)
        if clause is not None:
            if clause.reminder or clause.test:
                return None
            message = clause.message or trailing_message or trigger_message(trigger, "", entities)
            action = notification_action(clause, message, tuple(entities))
            if action is None:
                return ActionReading((), clause, clause.recipient_name or "diese Person")
            steps.append(action)
            only_clause = clause if len(chunks) == 1 else None
            continue
        revert = _revert_chunk(chunk, previous)
        if revert is not None:
            steps.extend(revert)
            previous = revert[1]
            continue
        elliptic = _elliptic_chunk(chunk, previous, entities)
        if elliptic is not None:
            steps.append(elliptic)
            previous = elliptic
            continue
        parsed = readers.action(chunk) if looks_like_device_action(chunk) else None
        if not parsed:
            return _whole_clause(text, chunks, readers)
        steps.extend(parsed)
        flat = _flatten(parsed)
        previous = flat[-1] if flat else None
    return ActionReading(tuple(steps), only_clause)


def _whole_clause(text: str, chunks: list[str], readers: Readers) -> ActionReading | None:
    """"Schalte das Flurlicht und das Wohnzimmerlicht ein" - one verb bracket.

    Accepted only when the action parser demonstrably read the coordination
    (several steps or several targets); a single-target reading of a
    coordinated clause would silently drop a part.
    """
    if not looks_like_device_action(text):
        return None
    parsed = readers.action(text)
    if not parsed:
        return None
    flat = _flatten(parsed)
    if len(chunks) > 1 and not (
        len(flat) >= len(chunks)
        or any(step.target is not None and len(step.target.entity_ids) >= len(chunks) for step in flat)
    ):
        return None
    return ActionReading(tuple(parsed), None)


_OR_SPLIT_RE = re.compile(r"\s*,?\s+oder\s+(?:wenn|sobald|falls|sofern)\s+", re.IGNORECASE)


def _alternative_triggers(
    parts: Sequence[str], connector: str, entities: Sequence[EntitySnapshot], readers: Readers
) -> EventInterpretation:
    """"Wenn X oder wenn Y": each alternative is its own complete trigger."""
    triggers: list[TriggerModel] = []
    for part in parts:
        reading = interpret_event_clause(part, connector, entities, readers.trigger, readers.condition)
        if reading.trigger is None or reading.conditions:
            return EventInterpretation(None, (), reading.grounded)
        triggers.append(reading.trigger)
    return EventInterpretation(triggers[0], (), None, typed=True, alternatives=tuple(triggers))


def build_automation(
    interpreted: EventInterpretation,
    reading: ActionReading,
    entities: Sequence[EntitySnapshot],
    source_text: str,
    trace: CompositionTrace,
) -> CompositionOutcome:
    assert interpreted.trigger is not None
    if reading.unresolved_recipient is not None:
        return CompositionOutcome(
            OutcomeKind.CLARIFY,
            speech=(
                f"Ich finde kein eindeutiges Benachrichtigungsziel für {reading.unresolved_recipient}. "
                "Wen soll ich benachrichtigen?"
            ),
            trace=replace(trace, grounding="recipient", reason="recipient_unresolved"),
        )
    triggers = (
        tuple(
            replace(trigger, trigger_id=f"ausloeser_{index}")
            for index, trigger in enumerate(interpreted.alternatives, start=1)
        )
        if interpreted.alternatives
        else (interpreted.trigger,)
    )
    model = AutomationModel(
        triggers=triggers, conditions=interpreted.conditions,
        actions=reading.steps, source_text=source_text,
    )
    canonical = (
        _canonical(interpreted.trigger, reading.notification, interpreted.conditions, interpreted.grounded)
        if reading.notification is not None else None
    )
    return CompositionOutcome(
        OutcomeKind.AUTOMATION,
        model=model,
        validation_error=validate_automation(model),
        canonical=canonical,
        trace=trace,
    )


def compose_event_automation(
    raw_text: str,
    entities: Sequence[EntitySnapshot],
    readers: Readers,
) -> CompositionOutcome | None:
    """``None`` unless the utterance is one EVENT clause + one ACTION clause."""
    prepared = prepare_automation_text(raw_text)
    memo: dict[str, bool] = {}

    def action_ok(text: str) -> bool:
        if text not in memo:
            memo[text] = read_actions(text, None, entities, readers) is not None
        return memo[text]

    frame: EventActionFrame | None = segment_event_automation(prepared.text, action_ok)
    if frame is None:
        return None
    notification_only = is_notification_text(frame.action_text)
    trace = CompositionTrace(
        route="event_notification" if notification_only else "event_action",
        order=frame.order.name,
        connector=frame.connector,
        repaired=prepared.repaired,
        event_words=len(frame.event_text.split()),
    )
    alternatives = _OR_SPLIT_RE.split(frame.event_text) if frame.temporal is None else [frame.event_text]
    if frame.temporal is not None:
        interpreted = temporal_interpretation(frame.temporal)
    elif len(alternatives) > 1:
        interpreted = _alternative_triggers(alternatives, frame.connector, entities, readers)
    else:
        interpreted = interpret_event_clause(
            frame.event_text, frame.connector, entities, readers.trigger, readers.condition
        )
    if interpreted.trigger is None:
        grounded = interpreted.grounded
        if not notification_only and (
            grounded is None
            or grounded.status in {GroundingStatus.NOT_APPLICABLE, GroundingStatus.NOT_FOUND,
                                   GroundingStatus.AMBIGUOUS}
        ):
            # Device automations keep their established contract: an event
            # that cannot be grounded is no automation (never a guess).
            return None
        return failure_outcome(
            grounded, frame.action_text, frame.trailing_message, raw_text, trace, interpreted.conditions
        )
    reading = read_actions(frame.action_text, interpreted.trigger, entities, readers, frame.trailing_message)
    if reading is None:
        return None
    trace = replace(trace, grounding="typed" if interpreted.typed else "established_parser")
    return build_automation(interpreted, reading, entities, raw_text, trace)


def failure_outcome(
    grounded: GroundedEvent | None,
    action_text: str,
    trailing_message: str | None,
    source_text: str,
    trace: CompositionTrace,
    conditions: tuple[ConditionNode, ...] = (),
) -> CompositionOutcome:
    status = grounded.status if grounded is not None else GroundingStatus.NOT_APPLICABLE
    reason = grounded.reason if grounded is not None else None
    failed = replace(trace, grounding=status.name, reason=reason)
    if grounded is not None and status is GroundingStatus.AMBIGUOUS:
        return CompositionOutcome(
            OutcomeKind.CLARIFY,
            speech=grounded.question,
            clarification=EventClarification(
                grounded, action_text, trailing_message, conditions, source_text
            ),
            trace=failed,
        )
    if grounded is not None and status in {GroundingStatus.MISSING_SUBJECT, GroundingStatus.NOT_FOUND}:
        return CompositionOutcome(OutcomeKind.CLARIFY, speech=grounded.question, trace=failed)
    return CompositionOutcome(OutcomeKind.UNSUPPORTED, speech=unsupported_text(reason), trace=failed)


def resolve_event_clarification(
    reply: str,
    pending: EventClarification,
    entities: Sequence[EntitySnapshot],
    readers: Readers,
) -> CompositionOutcome | None:
    """Finish the same draft with the chosen candidate; ``None`` if the reply
    does not pick exactly one of them (then it is not an answer)."""
    chosen = choose_candidate(reply, pending.grounded.candidates)
    if chosen is None:
        return None
    grounded = restrict_to(pending.grounded, chosen, entities)
    if grounded.trigger is None:
        return None
    interpreted = EventInterpretation(grounded.trigger, pending.conditions, grounded, typed=True)
    reading = read_actions(
        pending.action_text, grounded.trigger, entities, readers, pending.trailing_message
    )
    if reading is None:
        return None
    return build_automation(
        interpreted, reading, entities, pending.source_text,
        CompositionTrace("event_clarification_followup", grounding="typed"),
    )


__all__ = (
    "ActionReading",
    "CanonicalEvent",
    "CanonicalEventNotification",
    "CompositionOutcome",
    "CompositionTrace",
    "EventClarification",
    "EventInterpretation",
    "EventRoles",
    "OutcomeKind",
    "Readers",
    "compose_event_automation",
    "failure_outcome",
    "interpret_event_clause",
    "log_composition_trace",
    "read_actions",
    "resolve_event_clarification",
    "split_action_chunks",
    "unsupported_text",
)
