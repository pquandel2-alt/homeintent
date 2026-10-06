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

from .missing_part import MissingPart, PartRequest
from .automation_grounding import (
    GroundedEvent,
    GroundingStatus,
    Quantifier,
    choose_candidate,
    ground_event,
    read_subject,
    subject_candidates,
    restrict_to,
    target_for,
)
from .automation_language import (
    ConditionSpan,
    EventReference,
    EventRoles,
    EventActionFrame,
    TemporalEvent,
    ChangeSense,
    ValueUnit,
    and_reversed_candidates,
    condition_split_candidates,
    is_nobody_home_phrase,
    is_notification_text,
    looks_like_device_action,
    prepare_automation_text,
    protected_message_spans,
    read_event_roles,
    resolve_reference,
    segment_event_automation,
)
from .automation_monitoring import segment_monitoring
from .automation_notification import notification_action
from .rate_monitor import ChangeDirection, MonitorProposal, RateRule, propose
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.action_model import ActionGroup, ActionModel, ActionType, NotificationRecipientKind
from .nlu.automation_model import (
    AutomationModel,
    NumericComparator,
    PresenceEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from .nlu.automation_validator import AutomationValidationError, validate_automation
from .nlu.condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator
from .nlu.measurement import MeasurementProperty, TravelDirection
from .nlu.semantic_state import SemanticState
from .nlu.normalize import german_number
from .notification_language import (
    NotificationClause,
    describe_holding_state,
    describe_unchanged_today,
    describe_whole_set_state,
    parse_notification_clause,
    runtime_message,
    trigger_message,
)

# "Achte darauf, ob ...": the monitoring verb itself asks to tell the speaker.
_IMPLICIT_NOTIFICATION = NotificationClause(NotificationRecipientKind.CURRENT_USER)

_LOGGER = logging.getLogger(__name__)

TriggerReader = Callable[[str], "TriggerModel | None"]
ConditionReader = Callable[[str], "ConditionNode | None"]


class OutcomeKind(Enum):
    AUTOMATION = auto()
    CLARIFY = auto()
    UNSUPPORTED = auto()
    # Understood, but only HomeIntent's own monitor runtime can run it
    # without new Home Assistant helpers (7.9 W3).
    MONITOR = auto()


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
    # 7.9: the whole set (W1), the state that did not occur (W2) and a
    # change by an amount within a window (W3).
    quantifier_all: bool = False
    absent_state: SemanticState | None = None
    change_delta: float | None = None
    change_window_seconds: int | None = None


@dataclass(frozen=True)
class CanonicalEventNotification:
    """EVENT_NOTIFICATION: who is told what, when."""

    recipient: NotificationRecipientKind
    recipient_name: str | None
    event: CanonicalEvent
    condition_count: int
    explicit_message: str | None
    # 7.9 W5: repetition and escalation.
    repeat_interval_seconds: int | None = None
    max_repeats: int | None = None
    escalation_seconds: int | None = None
    escalation_recipient: str | None = None


@dataclass(frozen=True)
class EventClarification:
    """Enough state to finish the same draft after "Die linke."."""

    grounded: GroundedEvent
    action_text: str
    trailing_message: str | None
    conditions: tuple[ConditionNode, ...]
    source_text: str
    implicit_notification: bool = False


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
    monitor: MonitorProposal | None = None
    # "Überwache das Garagentor." (7.9 W6): the object of an open request.
    monitored_object: tuple[str, ...] | None = None
    # "wenn etwas Ungewöhnliches passiert" (7.9 W7): no event of its own -
    # answered from the proactive situation catalog, never invented.
    vague_situation: bool = False
    # The one part a question asks for (7.9.1 A6).
    part: PartRequest | None = None


@dataclass(frozen=True)
class EventInterpretation:
    """One event clause read into a trigger plus embedded conditions."""

    trigger: TriggerModel | None
    conditions: tuple[ConditionNode, ...] = ()
    grounded: GroundedEvent | None = None
    typed: bool = False
    alternatives: tuple[TriggerModel, ...] = ()
    situation: str | None = None  # spoken combined state (``state_conjunction``)
    situation_parts: int = 1  # several parts: whichever begins last
    situation_message: str | None = None  # default push text of the situation


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


def _existential_condition(
    node: ConditionNode, text: str, entities: Sequence[EntitySnapshot]
) -> ConditionNode | None:
    """A device-state condition over several devices means what the same
    phrase means as an event (7.8.3): "ein Fenster offen ist" holds while
    *any* window is open.  One Home Assistant state condition with an
    entity list would require *all* of them, so the reading becomes an OR
    of one state condition per device.  A bare noun without determiner
    ("wenn noch Licht an ist") is indefinite as well.  A definite phrase the
    typed grounding finds ambiguous ("das Licht" with 24 lights) is not
    understood - it never silently becomes "all of them"."""
    leaf = node.condition
    if (
        node.operator is not None
        or leaf is None
        or leaf.type is not ConditionType.STATE
        or leaf.target is None
        or leaf.target.entity_id is not None
        or leaf.target.entity_ids
        or leaf.target.quantifier is not None
        or not entities
    ):
        return node
    grounded = ground_event(read_event_roles(text), entities)
    if grounded.aggregate and grounded.trigger is not None:
        # "alle Fenster zu sind": the whole set - the list condition's "all".
        return whole_set_condition(grounded.trigger)
    if grounded.status is GroundingStatus.AMBIGUOUS:
        subject = grounded.subject
        if subject is None or subject.quantifier is not Quantifier.BARE:
            return None
    elif grounded.status is not GroundingStatus.RESOLVED or len(grounded.candidates) < 2:
        return node
    return ConditionNode(operator=LogicalOperator.OR, children=tuple(
        ConditionNode(condition=replace(leaf, target=target_for([entity], entities)))
        for entity in sorted(grounded.candidates, key=lambda item: item.entity_id)
    ))


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
        node = (
            parse_condition(span.text)
            or _typed_nobody_home(span.text)
            or _typed_condition(span.text, entities)
        )
        if node is not None:
            node = _existential_condition(node, span.text, entities)
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
    reference: EventReference | None = None,
) -> EventInterpretation:
    """Typed reading first; established parsers only for untyped event kinds.

    ``reference`` is the monitored object of a monitoring frame: a pronoun,
    partitive or missing subject of the event is bound to it (7.8.3).
    """
    roles = resolve_reference(read_event_roles(event_text), reference)
    if is_nobody_home_phrase(roles.source) and not roles.conditions:
        return nobody_home_interpretation(entities)
    embedded = _conditions_for(roles.conditions, parse_condition, entities)
    if embedded is None:
        return EventInterpretation(None)
    grounded = ground_event(roles, entities)
    if grounded.status is GroundingStatus.RESOLVED and roles.until is not None:
        return until_interpretation(grounded, roles.until, embedded, entities)
    if grounded.status is GroundingStatus.RESOLVED:
        return EventInterpretation(grounded.trigger, embedded, grounded, typed=True)
    # "X und niemand zuhause ist" / "X, aber nur wenn Y": the event plus a
    # condition the established condition parser has to accept verbatim.
    for left, span in condition_split_candidates(roles.source):
        condition = _conditions_for((span,), parse_condition, entities)
        if condition is None:
            continue
        left_roles = resolve_reference(read_event_roles(left), reference)
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
    # "wenn niemand zuhause ist und noch ein Licht an ist": "und" joins a
    # condition and the event in either order (7.8.3).  Only a typed,
    # fully grounded event behind a condition the established parser
    # accepts verbatim - the first order above always wins.
    for condition_text, event_part in and_reversed_candidates(roles.source):
        condition = _conditions_for((ConditionSpan(condition_text),), parse_condition, entities)
        if condition is None:
            continue
        right_roles = resolve_reference(read_event_roles(event_part), reference)
        right_conditions = _conditions_for(right_roles.conditions, parse_condition, entities)
        if right_conditions is None:
            continue
        right_grounded = ground_event(right_roles, entities)
        if right_grounded.status is GroundingStatus.RESOLVED:
            return EventInterpretation(
                right_grounded.trigger, (*condition, *right_conditions), right_grounded, typed=True
            )
        if right_grounded.status is not GroundingStatus.NOT_APPLICABLE:
            return EventInterpretation(None, (*condition, *right_conditions), right_grounded)
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


NOBODY_HOME_CONDITION = ConditionNode(
    operator=LogicalOperator.NOT,
    children=(ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home")),),
)


def _leave_triggers(entities: Sequence[EntitySnapshot]) -> list[TriggerModel]:
    """One "leaves the house" trigger per person (``presence_scope`` later
    limits them to a confirmed household)."""
    return [
        TriggerModel(
            type=TriggerType.PRESENCE,
            target=TriggerTarget(domain="person", entity_id=person.entity_id),
            zone_id="home",
            presence_event=PresenceEvent.LEAVE,
        )
        for person in sorted(
            (entity for entity in entities if entity.domain == "person"),
            key=lambda entity: entity.entity_id,
        )
    ]


def nobody_home_interpretation(entities: Sequence[EntitySnapshot]) -> EventInterpretation:
    """"wenn niemand zuhause ist" / "wenn alle weg sind" (7.9 W1): the moment
    the last person leaves - every leave is a trigger, the condition says
    nobody is home.  Without any ``person.*`` there is nothing to read."""
    leaves = _leave_triggers(entities)
    if not leaves:
        return EventInterpretation(None)
    return EventInterpretation(
        leaves[0], (NOBODY_HOME_CONDITION,), None, typed=True,
        alternatives=tuple(leaves),
        situation="niemand zuhause ist",
        situation_message="Niemand ist zuhause.",
    )


def _typed_nobody_home(text: str) -> ConditionNode | None:
    return NOBODY_HOME_CONDITION if is_nobody_home_phrase(text) else None


def _nobody_home(node: ConditionNode) -> bool:
    """"niemand zuhause": NOT(any person at home)."""
    if node.operator is not LogicalOperator.NOT or len(node.children) != 1:
        return False
    leaf = node.children[0].condition
    return (
        leaf is not None
        and leaf.type is ConditionType.PRESENCE
        and leaf.raw_state == "home"
        and (leaf.target is None or leaf.target.entity_id is None)
    )


def state_conjunction(
    interpreted: EventInterpretation, entities: Sequence[EntitySnapshot]
) -> tuple[EventInterpretation, str | None]:
    """"wenn ein Fenster offen ist und niemand zuhause ist" (7.8.3).

    Two lasting states joined by "und" hold whenever both are true - also
    when the *second* one begins (everybody leaves while a window is still
    open).  Home Assistant needs that as: one trigger per state becoming
    true, and every state as a condition.  Only states with a deterministic
    "becoming true" trigger are completed (nobody home: each person leaves;
    an entity state); a moment ("geöffnet wird"), a duration ("seit 10
    Minuten") or a time window stays exactly as spoken.

    Returns the completed interpretation and a message describing the
    combined situation, or the unchanged interpretation and ``None``.
    """
    trigger = interpreted.trigger
    grounded = interpreted.grounded
    if (
        trigger is None
        or trigger.type is not TriggerType.STATE
        or trigger.for_seconds is not None
        or trigger.state is None
        or interpreted.alternatives
        or not interpreted.conditions
        or grounded is None
        or grounded.roles is None
        or not grounded.roles.stative
        or not grounded.candidates
    ):
        return interpreted, None
    extra: list[TriggerModel] = []
    situation: list[tuple[str, str]] = []
    for node in interpreted.conditions:
        members = _any_member_state(node)
        if members is not None:
            # "und ein Fenster offen ist": any member becoming so is a part.
            state_trigger = TriggerModel(
                type=TriggerType.STATE,
                target=target_for(_resolve(members[0], entities), entities),
                state=members[1],
            )
            extra.append(state_trigger)
            holding = describe_holding_state(state_trigger, entities)
            if holding is None:
                return interpreted, None
            situation.append((holding.subordinate, holding.sentence))
            continue
        if _nobody_home(node):
            leaves = _leave_triggers(entities)
            if not leaves:
                continue
            extra.extend(leaves)
            situation.append(("niemand zuhause ist", "Niemand ist zuhause."))
            continue
        leaf = node.condition
        if (
            node.operator is None
            and leaf is not None
            and leaf.type is ConditionType.STATE
            and leaf.target is not None
            and leaf.state is not None
        ):
            state_trigger = TriggerModel(type=TriggerType.STATE, target=leaf.target, state=leaf.state)
            extra.append(state_trigger)
            holding = (
                describe_whole_set_state(state_trigger, entities)
                if leaf.target.quantifier == "all"
                else describe_holding_state(state_trigger, entities)
            )
            if holding is None:
                return interpreted, None
            situation.append((holding.subordinate, holding.sentence))
    if not extra:
        return interpreted, None
    first = (
        describe_whole_set_state(trigger, entities)
        if grounded.aggregate
        else describe_holding_state(trigger, entities)
    )
    if first is None:
        return interpreted, None
    own = (
        whole_set_condition(trigger)
        if grounded.aggregate
        else any_member_condition(grounded.candidates, trigger.state, entities)
    )
    parts = (first.subordinate, *(item[0] for item in situation))
    completed = replace(
        interpreted,
        conditions=(own, *interpreted.conditions),
        alternatives=(trigger, *extra),
        situation=" und ".join(parts),
        situation_parts=len(parts),
    )
    return completed, " ".join((first.sentence, *(item[1] for item in situation)))


def until_interpretation(
    grounded: GroundedEvent,
    until: tuple[int, int],
    conditions: tuple[ConditionNode, ...],
    entities: Sequence[EntitySnapshot],
) -> EventInterpretation:
    """"bis 10 Uhr keine Bewegung im Bad" (7.9 W2): at the check time, every
    detector has stayed in its rest state since midnight."""
    trigger = grounded.trigger
    if trigger is None or trigger.state is None or trigger.target is None or trigger.absent_state is None:
        return EventInterpretation(None, conditions, grounded)
    unchanged = tuple(
        ConditionNode(condition=ConditionModel(
            type=ConditionType.UNCHANGED_TODAY, target=target_for([entity], entities),
            state=trigger.state,
        ))
        for entity in sorted(grounded.candidates, key=lambda item: item.entity_id)
    )
    at = TriggerModel(type=TriggerType.TIME, time_hour=until[0], time_minute=until[1], time_second=0)
    phrase = describe_unchanged_today(trigger.target, trigger.state, until, entities)
    return EventInterpretation(
        at, (*unchanged, *conditions), grounded, typed=True,
        situation_message=phrase.sentence if phrase is not None else None,
    )


RESTART_FOR_NOTE = "Startet Home Assistant neu, beginnt die Wartezeit von vorn."
RESTART_TODAY_NOTE = (
    "Startet Home Assistant an diesem Tag neu, zählt der Neustart als Änderung – "
    "dann melde ich mich an diesem Tag nicht."
)


def honesty_notes(interpreted: EventInterpretation) -> tuple[str, ...]:
    """What the preview must say about limits Home Assistant has (7.9 W2)."""
    notes: list[str] = []
    triggers = interpreted.alternatives or ((interpreted.trigger,) if interpreted.trigger else ())
    if any(item.absent_state is not None for item in triggers):
        notes.append(RESTART_FOR_NOTE)

    def unchanged(node: ConditionNode) -> bool:
        if node.operator is None:
            return node.condition is not None and node.condition.type is ConditionType.UNCHANGED_TODAY
        return any(unchanged(child) for child in node.children)

    if any(unchanged(node) for node in interpreted.conditions):
        notes.append(RESTART_TODAY_NOTE)
    if interpreted.grounded is not None:
        # What the grounding found the spoken place cannot cover (7.9.2 A4).
        notes.extend(interpreted.grounded.notes)
    roles = interpreted.grounded.roles if interpreted.grounded is not None else None
    if roles is not None and roles.agent:
        notes.append(
            f"Ich erkenne nur Bewegung, nicht, wer sich bewegt – also auch nicht, ob es {roles.agent} ist."
        )
    return tuple(notes)


def whole_set_condition(trigger: TriggerModel) -> ConditionNode:
    """Every member of the trigger's set is in its state: one Home Assistant
    state condition with the entity list, which means "all" (7.9 W1)."""
    assert trigger.target is not None and trigger.state is not None
    return ConditionNode(condition=ConditionModel(
        type=ConditionType.STATE, target=replace(trigger.target, quantifier="all"), state=trigger.state,
    ))


def any_member_condition(
    candidates: Sequence[EntitySnapshot], state: SemanticState, entities: Sequence[EntitySnapshot]
) -> ConditionNode:
    """Any one member is in ``state``: an OR of one condition per entity (a
    list in one Home Assistant state condition would require *all*)."""
    holds = tuple(
        ConditionNode(condition=ConditionModel(
            type=ConditionType.STATE, target=target_for([entity], entities), state=state,
        ))
        for entity in sorted(candidates, key=lambda item: item.entity_id)
    )
    return holds[0] if len(holds) == 1 else ConditionNode(operator=LogicalOperator.OR, children=holds)


def _any_member_state(node: ConditionNode) -> tuple[list[TriggerTarget], SemanticState] | None:
    """The targets and state of an ``any_member_condition`` OR, else ``None``."""
    if node.operator is not LogicalOperator.OR or len(node.children) < 2:
        return None
    targets: list[TriggerTarget] = []
    states: set[SemanticState] = set()
    for child in node.children:
        leaf = child.condition
        if child.operator is not None or leaf is None or leaf.type is not ConditionType.STATE:
            return None
        if leaf.target is None or leaf.state is None:
            return None
        targets.append(leaf.target)
        states.add(leaf.state)
    if len(states) != 1:
        return None
    return targets, next(iter(states))


def _resolve(targets: Sequence[TriggerTarget], entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    found: dict[str, EntitySnapshot] = {}
    for target in targets:
        for entity in entities:
            if target.entity_id is not None:
                if entity.entity_id == target.entity_id:
                    found[entity.entity_id] = entity
            elif (
                entity.domain == target.domain
                and (target.device_class is None or entity.device_class == target.device_class)
                and (target.area_id is None or entity.area_id == target.area_id)
                and (target.floor_id is None or entity.floor_id == target.floor_id)
            ):
                found[entity.entity_id] = entity
    return list(found.values())


def whole_set_state(
    interpreted: EventInterpretation, entities: Sequence[EntitySnapshot]
) -> tuple[EventInterpretation, str | None]:
    """"wenn alle Fenster zu sind" (7.9 W1): the trigger fires when any member
    reaches the state, the condition requires every member to be in it -
    whichever member is last.  Returns the completed interpretation and the
    default message, or the unchanged interpretation and ``None``."""
    trigger = interpreted.trigger
    grounded = interpreted.grounded
    if (
        trigger is None or grounded is None or not grounded.aggregate
        or interpreted.situation is not None or trigger.target is None
    ):
        return interpreted, None
    marked = replace(trigger, target=replace(trigger.target, quantifier="all"))
    phrase = describe_whole_set_state(marked, entities)
    if phrase is None:
        return interpreted, None
    completed = replace(
        interpreted,
        trigger=marked,
        conditions=(whole_set_condition(marked), *interpreted.conditions),
        situation=phrase.subordinate,
    )
    return completed, phrase.sentence


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
            quantifier_all=bool(grounded is not None and grounded.aggregate),
            absent_state=trigger.absent_state,
        ),
        condition_count=len(conditions),
        explicit_message=clause.message,
    )


def rate_canonical(proposal: MonitorProposal) -> CanonicalEventNotification:
    """The canonical meaning of a change request (7.9 W3)."""
    rule = proposal.rule
    return CanonicalEventNotification(
        recipient=NotificationRecipientKind.CURRENT_USER,
        recipient_name=None,
        event=CanonicalEvent(
            trigger_type=TriggerType.NUMERIC_STATE,
            entity_ids=(rule.entity_id,),
            state=None,
            change_delta=rule.delta if rule.direction is not ChangeDirection.FALL else -rule.delta,
            change_window_seconds=rule.window_seconds,
        ),
        condition_count=0,
        explicit_message=None,
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
    candidates = subject_candidates(subject, entities)
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
    default_message: str | None = None,
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
            message = (
                clause.message or trailing_message or default_message
                or trigger_message(trigger, "", entities)
            )
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


def implicit_notification_reading(
    trigger: TriggerModel | None,
    entities: Sequence[EntitySnapshot],
    default_message: str | None = None,
) -> ActionReading:
    """NOTIFY the speaker - the action a monitoring verb implies (7.8.3)."""
    message = default_message or trigger_message(trigger, "", entities)
    action = notification_action(_IMPLICIT_NOTIFICATION, message, tuple(entities))
    if action is None:
        return ActionReading((), _IMPLICIT_NOTIFICATION, "dich")
    return ActionReading((action,), _IMPLICIT_NOTIFICATION)


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
    parts: Sequence[str],
    connector: str,
    entities: Sequence[EntitySnapshot],
    readers: Readers,
    reference: EventReference | None = None,
) -> EventInterpretation:
    """"Wenn X oder wenn Y": each alternative is its own complete trigger."""
    triggers: list[TriggerModel] = []
    for part in parts:
        reading = interpret_event_clause(
            part, connector, entities, readers.trigger, readers.condition, reference
        )
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
        question = (
            f"Ich finde kein eindeutiges Benachrichtigungsziel für {reading.unresolved_recipient}. "
            "Wen soll ich benachrichtigen?"
        )
        return CompositionOutcome(
            OutcomeKind.CLARIFY,
            speech=question,
            trace=replace(trace, grounding="recipient", reason="recipient_unresolved"),
            part=PartRequest(
                MissingPart.RECIPIENT, question, replaces=tuple(reading.unresolved_recipient.split())
            ),
        )
    triggers = (
        tuple(
            replace(trigger, trigger_id=f"ausloeser_{index}")
            for index, trigger in enumerate(interpreted.alternatives, start=1)
        )
        if interpreted.alternatives
        else (interpreted.trigger,)
    )
    steps = reading.steps
    if reading.notification is not None and not reading.notification.message and len(triggers) == 1:
        # 7.9.2 A6: the push names the device/rooms at run time.
        detail = runtime_message(triggers[0], interpreted.conditions, entities)
        if detail is not None:
            steps = tuple(
                replace(step, message=detail[0], message_template=detail[1])
                if isinstance(step, ActionModel) and step.type is ActionType.NOTIFY else step
                for step in steps
            )
    model = AutomationModel(
        triggers=triggers, conditions=interpreted.conditions,
        actions=steps, source_text=source_text, situation=interpreted.situation,
        situation_parts=interpreted.situation_parts, notes=honesty_notes(interpreted),
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

    frame: EventActionFrame | None = (
        segment_monitoring(prepared.text, action_ok)
        or segment_event_automation(prepared.text, action_ok)
    )
    if frame is None:
        return None
    notification_only = frame.implicit_notification or is_notification_text(frame.action_text)
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
        interpreted = _alternative_triggers(
            alternatives, frame.connector, entities, readers, frame.reference
        )
    else:
        interpreted = interpret_event_clause(
            frame.event_text, frame.connector, entities, readers.trigger, readers.condition,
            frame.reference,
        )
    if frame.extra_conditions:
        extra = _conditions_for(frame.extra_conditions, readers.condition, entities)
        if extra is None:
            # A spoken condition that is not understood is never dropped.
            return None
        interpreted = replace(interpreted, conditions=(*interpreted.conditions, *extra))
    change = _change_outcome(interpreted, frame, notification_only, trace)
    if change is not None:
        return change
    if interpreted.trigger is None:
        grounded = interpreted.grounded
        missing_meter = (
            # "Bei Wind über 40 km/h …" in a house without a wind sensor
            # (7.9.2 A6): the measured quantity is understood, the device
            # does not exist - say so instead of "nicht erkannt".
            grounded is not None and grounded.status is GroundingStatus.NOT_FOUND
            and grounded.roles is not None and grounded.roles.value is not None
            and grounded.subject is not None and grounded.subject.noun is not None
            and grounded.subject.noun.domain == "sensor"
        )
        if not notification_only and not missing_meter and (
            grounded is None
            or grounded.status in {GroundingStatus.NOT_APPLICABLE, GroundingStatus.NOT_FOUND,
                                   GroundingStatus.AMBIGUOUS}
        ):
            # Device automations keep their established contract: an event
            # that cannot be grounded is no automation (never a guess).
            return None
        return failure_outcome(
            grounded, frame.action_text, frame.trailing_message, raw_text, trace, interpreted.conditions,
            implicit_notification=frame.implicit_notification,
        )
    interpreted, holding_message = state_conjunction(interpreted, entities)
    if holding_message is None:
        interpreted, holding_message = whole_set_state(interpreted, entities)
    if holding_message is None:
        holding_message = interpreted.situation_message
    reading = (
        implicit_notification_reading(interpreted.trigger, entities, holding_message)
        if frame.implicit_notification
        else read_actions(
            frame.action_text, interpreted.trigger, entities, readers, frame.trailing_message,
            holding_message,
        )
    )
    if reading is None:
        return None
    trace = replace(trace, grounding="typed" if interpreted.typed else "established_parser")
    return build_automation(interpreted, reading, entities, raw_text, trace)


def _change_outcome(
    interpreted: EventInterpretation,
    frame: EventActionFrame,
    notification_only: bool,
    trace: CompositionTrace,
) -> CompositionOutcome | None:
    """A change by an amount (7.9 W3) runs in HomeIntent's monitor runtime."""
    grounded = interpreted.grounded
    roles = grounded.roles if grounded is not None else None
    if (
        grounded is None or roles is None or roles.change is None
        or grounded.status is not GroundingStatus.RESOLVED or len(grounded.candidates) != 1
    ):
        return None
    failed = replace(trace, grounding="change", reason="change_action")
    if not notification_only:
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED, trace=failed,
            speech=(
                "Eine Änderung um einen Betrag kann ich überwachen und melden, aber keine "
                "Geräte dazu schalten."
            ),
        )
    clause = (
        _IMPLICIT_NOTIFICATION if frame.implicit_notification
        else parse_notification_clause(frame.action_text.strip(" ,."))
    )
    if clause is None or clause.recipient_kind is not NotificationRecipientKind.CURRENT_USER or clause.message:
        return CompositionOutcome(
            OutcomeKind.UNSUPPORTED, trace=failed,
            speech="Solche Änderungen melde ich nur dir selbst, mit meinem eigenen Text.",
        )
    change = roles.change
    assert change.window_seconds is not None
    sensor = grounded.candidates[0]
    rule = RateRule(
        entity_id=sensor.entity_id,
        delta=change.delta,
        unit=sensor.unit or ("%" if change.unit is ValueUnit.PERCENT else "°C"),
        direction={
            ChangeSense.FALL: ChangeDirection.FALL, ChangeSense.RISE: ChangeDirection.RISE,
            ChangeSense.EITHER: ChangeDirection.EITHER,
        }[change.sense],
        window_seconds=change.window_seconds,
    )
    proposal = propose(rule, f"„{sensor.friendly_name}“")
    return CompositionOutcome(
        OutcomeKind.MONITOR, speech=proposal.preview, monitor=proposal,
        canonical=rate_canonical(proposal), trace=replace(trace, grounding="change"),
    )


def failure_outcome(
    grounded: GroundedEvent | None,
    action_text: str,
    trailing_message: str | None,
    source_text: str,
    trace: CompositionTrace,
    conditions: tuple[ConditionNode, ...] = (),
    *,
    implicit_notification: bool = False,
) -> CompositionOutcome:
    status = grounded.status if grounded is not None else GroundingStatus.NOT_APPLICABLE
    reason = grounded.reason if grounded is not None else None
    failed = replace(trace, grounding=status.name, reason=reason)
    if grounded is not None and status is GroundingStatus.AMBIGUOUS:
        return CompositionOutcome(
            OutcomeKind.CLARIFY,
            speech=grounded.question,
            clarification=EventClarification(
                grounded, action_text, trailing_message, conditions, source_text,
                implicit_notification,
            ),
            trace=failed,
        )
    if grounded is not None and (
        status in {GroundingStatus.MISSING_SUBJECT, GroundingStatus.NOT_FOUND}
        or (status is GroundingStatus.UNSUPPORTED and grounded.question)
    ):
        part = (
            PartRequest(
                grounded.missing, grounded.question or "",
                replaces=grounded.roles.subject_words if grounded.roles is not None else (),
            )
            if grounded.missing is not None else None
        )
        return CompositionOutcome(OutcomeKind.CLARIFY, speech=grounded.question, trace=failed, part=part)
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
    reading = (
        implicit_notification_reading(grounded.trigger, entities)
        if pending.implicit_notification
        else read_actions(
            pending.action_text, grounded.trigger, entities, readers, pending.trailing_message
        )
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
