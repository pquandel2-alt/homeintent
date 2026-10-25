"""Typed, compositional state for incomplete automation conversations.

The draft stores semantic values and provenance only.  It never concatenates
utterances, writes Home Assistant state, or bypasses the normal automation
validator/preview/confirmation/executor path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from enum import Enum, auto
from typing import Iterable

from ..entities import EntitySnapshot, normalize_for_compare
from .action_model import ActionGroup, ActionModel, ActionType
from .automation_model import (
    AutomationModel,
    CalendarReference,
    CalendarSchedule,
    SunEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from .condition_model import ConditionNode
from .entity_clarification import CandidateReplyKind, resolve_candidate_reply
from .german_structure import ClauseKind
from .language_frontend import LanguageDocument
from .semantic_state import SemanticState
from .semantic_utterance import Modality, Polarity
from .temporal_semantics import TemporalKind


class AutomationIntentKind(Enum):
    DEVICE_ACTION = auto()
    HEATING_SETPOINT = auto()
    STATE_MONITOR = auto()
    RECURRING = auto()
    CONDITIONAL = auto()


class RequiredAutomationSlot(Enum):
    INTENT = auto()
    TRIGGER = auto()
    TARGET = auto()
    OPERATION = auto()
    ACTION_VALUE = auto()
    OBSERVED_ENTITY = auto()
    OBSERVED_STATE = auto()
    REACTION = auto()
    REPETITION = auto()
    CONDITION = auto()


@dataclass(frozen=True)
class SlotProvenance:
    """Auditable evidence for one slot without retaining the utterance."""

    slot: str
    turn: int
    token_start: int
    token_end: int
    evidence: str


@dataclass(frozen=True)
class PendingAutomationSlotDraft:
    requesting_user_id: str | None
    conversation_scope: str
    created_at: datetime
    expires_at: datetime
    intent: AutomationIntentKind
    intent_confirmed: bool = True
    triggers: tuple[TriggerModel, ...] = ()
    date_reference: str | None = None
    scheduled_for: datetime | None = None
    clock: tuple[int, int] | None = None
    sun_event: SunEvent | None = None
    sun_offset_minutes: int | None = None
    duration_seconds: int | None = None
    repetition: str | None = None
    validity: str | None = None
    action_kind: ActionType | None = None
    action_target: TriggerTarget | None = None
    desired_state: SemanticState | None = None
    action_value: float | None = None
    actions: tuple[ActionModel | ActionGroup, ...] = ()
    conditions: tuple[ConditionNode, ...] = ()
    condition_announced: bool = False
    exceptions: tuple[str, ...] = ()
    polarity: Polarity = Polarity.POSITIVE
    modality: Modality = Modality.DIRECT
    provenance: tuple[SlotProvenance, ...] = ()
    missing_slots: tuple[RequiredAutomationSlot, ...] = ()
    ambiguities: tuple[str, ...] = ()
    ambiguous_entity_ids: tuple[str, ...] = ()
    unknown_terms: tuple[str, ...] = ()
    resolved_entity_ids: tuple[str, ...] = ()
    turn_count: int = 1


_DOMAIN_WORDS = {
    "licht": "light",
    "lichter": "light",
    "lampe": "light",
    "lampen": "light",
    "heizung": "climate",
    "heizungen": "climate",
    "thermostat": "climate",
    "thermostate": "climate",
    "waschmaschine": "switch",
}
_NUMBER_WORDS = {
    "null": 0,
    "eins": 1,
    "ein": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fuenf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,
    "zwoelf": 12,
    "dreizehn": 13,
    "vierzehn": 14,
    "fuenfzehn": 15,
    "sechzehn": 16,
    "siebzehn": 17,
    "achtzehn": 18,
    "neunzehn": 19,
    "zwanzig": 20,
    "einundzwanzig": 21,
    "zweiundzwanzig": 22,
    "dreiundzwanzig": 23,
    "vierundzwanzig": 24,
    "fuenfundzwanzig": 25,
    "dreissig": 30,
}
_SETPOINT_ACTIONS = frozenset({
    ActionType.SET_TEMPERATURE,
    ActionType.SET_BRIGHTNESS,
    ActionType.SET_POSITION,
    ActionType.SET_FAN_SPEED,
})
_SAFE_UNEXPLAINED_WORDS = frozenset({
    "ab", "am", "an", "bis", "falls", "fuer", "heute", "in", "ist",
    "jede", "jeden", "jedes", "mein", "meine", "meinen", "mit", "morgen",
    "minute", "minuten", "nach", "noch", "nur", "sekunde", "sekunden",
    "seit", "sofern", "sonnenaufgang", "sonnenuntergang", "stunde", "stunden",
    "taeglich", "tag", "tage", "uhr", "um", "uebermorgen", "vor",
    "vorausgesetzt", "waehrend", "wenn", "weiterhin", "wird", "woechentlich",
    "zuhause",
}) | frozenset(_NUMBER_WORDS)


def _words(document: LanguageDocument) -> tuple[str, ...]:
    return tuple(token.canonical for token in document.tokens if token.is_word)


def _provenance(document: LanguageDocument, slot: str, turn: int, evidence: str) -> SlotProvenance:
    word_tokens = [token for token in document.tokens if token.is_word]
    return SlotProvenance(
        slot,
        turn,
        word_tokens[0].start if word_tokens else 0,
        word_tokens[-1].end if word_tokens else 0,
        evidence,
    )


def _domain(document: LanguageDocument) -> str | None:
    found = {_DOMAIN_WORDS[word] for word in _words(document) if word in _DOMAIN_WORDS}
    return next(iter(found)) if len(found) == 1 else None


def _area_target(
    document: LanguageDocument,
    entities: Iterable[EntitySnapshot],
    *,
    domain: str | None,
) -> tuple[TriggerTarget | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    spoken_words = _words(document)
    areas: dict[tuple[str, str], list[EntitySnapshot]] = {}
    for entity in entities:
        if not entity.area_id or not entity.area_name:
            continue
        parts = tuple(normalize_for_compare(entity.area_name).split())
        if parts and any(
            spoken_words[index:index + len(parts)] == parts
            for index in range(len(spoken_words) - len(parts) + 1)
        ):
            areas.setdefault((entity.area_id, entity.area_name), []).append(entity)
    if len(areas) != 1:
        return (
            None,
            (),
            (() if not areas else tuple(name for _id, name in areas)),
            (),
        )
    (area_id, area_name), _mentioned = next(iter(areas.items()))
    candidates = [
        entity for entity in entities
        if entity.area_id == area_id and (domain is None or entity.domain == domain)
    ]
    if not candidates:
        return None, (), (f"Im {area_name} gibt es kein passendes Gerät.",), ()
    if len(candidates) == 1:
        entity = candidates[0]
        return (
            TriggerTarget(domain=entity.domain, area_id=area_id, entity_id=entity.entity_id),
            (entity.entity_id,),
            (),
            (),
        )
    return (
        TriggerTarget(domain=domain, area_id=area_id),
        (),
        tuple(entity.friendly_name for entity in candidates),
        tuple(entity.entity_id for entity in candidates),
    )


def _number_before_unit(document: LanguageDocument, units: frozenset[str]) -> float | None:
    words = _words(document)
    for index, word in enumerate(words):
        if word not in units or index == 0:
            continue
        raw = words[index - 1].replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            value = _NUMBER_WORDS.get(raw)
            return float(value) if value is not None else None
    return None


def _clock_value(document: LanguageDocument) -> tuple[int, int] | None:
    clock_text = next(
        (item.value for item in document.temporal if item.kind is TemporalKind.ABSOLUTE_TIME),
        None,
    )
    if clock_text is not None:
        hour, minute = (int(part) for part in clock_text.split(":", 1))
        return hour, minute
    words = _words(document)
    for index, word in enumerate(words[:-1]):
        if word != "um":
            continue
        raw = words[index + 1]
        hour = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw)
        if hour is not None and 0 <= hour <= 23:
            return hour, 0
    return None


def _schedule_spoken(date_reference: str | None, hour: int, minute: int) -> str:
    day = {
        "heute": "heute",
        "morgen": "morgen",
        "uebermorgen": "übermorgen",
    }.get(date_reference or "", "zum nächsten passenden Zeitpunkt")
    return f"{day} um {hour:02d}:{minute:02d} Uhr"


def _unknown_content_terms(
    document: LanguageDocument, entities: Iterable[EntitySnapshot]
) -> tuple[str, ...]:
    registry_words = {
        word
        for entity in entities
        for name in (
            entity.friendly_name,
            *entity.aliases,
            entity.area_name or "",
            *entity.area_aliases,
            entity.floor_name or "",
        )
        for word in normalize_for_compare(name).split()
    }
    unknown: list[str] = []
    for surface in document.semantics.unexplained_tokens:
        canonical = normalize_for_compare(surface)
        if canonical in _SAFE_UNEXPLAINED_WORDS or canonical in registry_words:
            continue
        if canonical not in unknown:
            unknown.append(canonical)
    return tuple(unknown)


def _temporal_trigger(
    document: LanguageDocument, now: datetime
) -> tuple[tuple[TriggerModel, ...], str | None, datetime | None, tuple[int, int] | None, SunEvent | None, int | None]:
    date_reference = next(
        (item.value for item in document.temporal if item.kind is TemporalKind.DATE), None
    )
    clock = _clock_value(document)
    sun_text = next(
        (item.value for item in document.temporal if item.kind is TemporalKind.SUN_EVENT), None
    )
    scheduled_for = None
    if date_reference in {"heute", "morgen", "uebermorgen"}:
        offset = {"heute": 0, "morgen": 1, "uebermorgen": 2}[date_reference]
        scheduled_for = datetime.combine(now.date() + timedelta(days=offset), time.min, tzinfo=now.tzinfo)
    if sun_text is not None:
        event = SunEvent.SUNSET if sun_text == "sonnenuntergang" else SunEvent.SUNRISE
        duration = next(
            (item.seconds for item in document.temporal if item.kind is TemporalKind.DURATION and item.seconds),
            None,
        )
        relation = next(
            (item.kind for item in document.temporal if item.kind in {TemporalKind.BEFORE, TemporalKind.AFTER}),
            None,
        )
        if duration is None:
            words = _words(document)
            for index, word in enumerate(words[:-1]):
                if words[index + 1] not in {"minute", "minuten", "stunde", "stunden"}:
                    continue
                amount = int(word) if word.isdigit() else _NUMBER_WORDS.get(word)
                if amount is None:
                    continue
                factor = 60 if words[index + 1].startswith("minute") else 3600
                duration = amount * factor
                if index + 2 < len(words):
                    relation = {
                        "vor": TemporalKind.BEFORE,
                        "nach": TemporalKind.AFTER,
                    }.get(words[index + 2], relation)
                break
        minutes = int(duration // 60) if duration is not None else 0
        if relation is TemporalKind.BEFORE:
            minutes = -minutes
        return (
            (TriggerModel(type=TriggerType.SUN, sun_event=event, offset_minutes=minutes),),
            date_reference,
            scheduled_for,
            None,
            event,
            minutes,
        )
    if clock is not None:
        hour, minute = clock
        return (
            (TriggerModel(type=TriggerType.CALENDAR_TIME),),
            date_reference,
            None,
            (hour, minute),
            None,
            None,
        )
    return (), date_reference, scheduled_for, None, None, None


def required_slots(draft: PendingAutomationSlotDraft) -> tuple[RequiredAutomationSlot, ...]:
    missing: list[RequiredAutomationSlot] = []
    if not draft.triggers:
        missing.append(RequiredAutomationSlot.TRIGGER)
    if not draft.intent_confirmed:
        missing.append(RequiredAutomationSlot.INTENT)
    if draft.intent is AutomationIntentKind.HEATING_SETPOINT:
        if draft.action_target is None or (
            draft.action_target.entity_id is None and draft.action_target.area_id is None
        ):
            missing.append(RequiredAutomationSlot.TARGET)
        if draft.action_value is None:
            missing.append(RequiredAutomationSlot.ACTION_VALUE)
    elif draft.intent is AutomationIntentKind.STATE_MONITOR:
        trigger = draft.triggers[0] if draft.triggers else None
        if trigger is None or trigger.target is None:
            missing.append(RequiredAutomationSlot.OBSERVED_ENTITY)
        if trigger is None or trigger.state is None:
            missing.append(RequiredAutomationSlot.OBSERVED_STATE)
        if not draft.actions:
            missing.append(RequiredAutomationSlot.REACTION)
    else:
        if not draft.actions:
            missing.append(RequiredAutomationSlot.OPERATION)
        if not _target_is_specific(draft.action_target) and not _actions_have_specific_targets(
            draft.actions
        ):
            missing.append(RequiredAutomationSlot.TARGET)
        if draft.action_kind in _SETPOINT_ACTIONS and draft.action_value is None:
            missing.append(RequiredAutomationSlot.ACTION_VALUE)
    if draft.intent is AutomationIntentKind.RECURRING and draft.repetition is None:
        missing.append(RequiredAutomationSlot.REPETITION)
    if draft.condition_announced and not draft.conditions:
        missing.append(RequiredAutomationSlot.CONDITION)
    return tuple(missing)


def _target_is_specific(target: TriggerTarget | None) -> bool:
    return target is not None and bool(
        target.entity_id or target.entity_ids or target.area_id or target.floor_id
    )


def _actions_have_specific_targets(
    actions: tuple[ActionModel | ActionGroup, ...],
) -> bool:
    leaves: list[ActionModel] = []

    def visit(action: ActionModel | ActionGroup) -> None:
        if isinstance(action, ActionGroup):
            for child in action.steps:
                visit(child)
        else:
            leaves.append(action)

    for action in actions:
        visit(action)
    controlled = [action for action in leaves if action.target is not None]
    return bool(controlled) and all(_target_is_specific(action.target) for action in controlled)


def new_slot_draft(
    document: LanguageDocument,
    entities: Iterable[EntitySnapshot],
    *,
    requesting_user_id: str | None,
    conversation_scope: str,
    now: datetime,
    ttl_seconds: int,
) -> PendingAutomationSlotDraft:
    words = _words(document)
    domain = _domain(document)
    heating = domain == "climate" or any(word in {"warm", "waermer", "kalt"} for word in words)
    effective_domain = domain or ("climate" if heating else None)
    intent = AutomationIntentKind.HEATING_SETPOINT if heating else AutomationIntentKind.DEVICE_ACTION
    if any(word in {"jede", "jeden", "jedes", "taeglich", "woechentlich"} for word in words):
        intent = AutomationIntentKind.RECURRING
    triggers, date_ref, scheduled, clock, sun, sun_offset = _temporal_trigger(document, now)
    target, resolved, ambiguities, ambiguous_ids = _area_target(
        document, entities, domain=effective_domain
    )
    unknown_terms = _unknown_content_terms(document, entities)
    if target is None and effective_domain is not None:
        target = TriggerTarget(domain=effective_domain)
    value = _number_before_unit(document, frozenset({"grad"}))
    action_kind = ActionType.SET_TEMPERATURE if heating else None
    evidence: list[SlotProvenance] = []
    if triggers:
        evidence.append(_provenance(document, "trigger", 1, triggers[0].type.name))
    if target is not None:
        evidence.append(_provenance(document, "target", 1, target.area_id or target.domain or "entity"))
    if value is not None:
        evidence.append(_provenance(document, "action_value", 1, str(value)))
    draft = PendingAutomationSlotDraft(
        requesting_user_id=requesting_user_id,
        conversation_scope=conversation_scope,
        created_at=now,
        expires_at=now + timedelta(seconds=max(10, ttl_seconds)),
        intent=intent,
        intent_confirmed=not heating,
        triggers=triggers,
        date_reference=date_ref,
        scheduled_for=scheduled,
        clock=clock,
        sun_event=sun,
        sun_offset_minutes=sun_offset,
        repetition="recurring" if intent is AutomationIntentKind.RECURRING else None,
        validity=date_ref,
        action_kind=action_kind,
        action_target=target,
        action_value=value,
        condition_announced=any(
            clause.kind is ClauseKind.CONDITION
            for clause in document.structure.clauses
        ),
        polarity=document.utterance.polarity,
        modality=document.utterance.modality,
        provenance=tuple(evidence),
        ambiguities=ambiguities,
        ambiguous_entity_ids=ambiguous_ids,
        unknown_terms=unknown_terms,
        resolved_entity_ids=resolved,
    )
    return replace(draft, missing_slots=required_slots(draft))


def project_followup(
    draft: PendingAutomationSlotDraft,
    document: LanguageDocument,
    entities: Iterable[EntitySnapshot],
) -> PendingAutomationSlotDraft:
    domain = draft.action_target.domain if draft.action_target is not None else _domain(document)
    entity_list = list(entities)
    target = None
    resolved: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    ambiguous_ids: tuple[str, ...] = ()
    if draft.ambiguous_entity_ids:
        candidates = tuple(
            entity for entity in entity_list
            if entity.entity_id in draft.ambiguous_entity_ids
        )
        selection = resolve_candidate_reply(document.source_text, candidates, entity_list)
        if selection.kind is CandidateReplyKind.SELECTED and selection.entity is not None:
            entity = selection.entity
            target = TriggerTarget(
                domain=entity.domain,
                area_id=entity.area_id,
                entity_id=entity.entity_id,
            )
            resolved = (entity.entity_id,)
        else:
            ambiguities = draft.ambiguities
            ambiguous_ids = draft.ambiguous_entity_ids
    if target is None and not ambiguities:
        target, resolved, ambiguities, ambiguous_ids = _area_target(
            document, entity_list, domain=domain
        )
    value = _number_before_unit(document, frozenset({"grad"}))
    evidence = list(draft.provenance)
    turn = draft.turn_count + 1
    updated = draft
    if target is not None:
        updated = replace(
            updated,
            intent_confirmed=True,
            action_target=target,
            resolved_entity_ids=resolved,
            ambiguities=ambiguities,
            ambiguous_entity_ids=ambiguous_ids,
        )
        evidence.append(_provenance(document, "target", turn, target.area_id or target.domain or "entity"))
    elif ambiguities:
        updated = replace(
            updated,
            ambiguities=ambiguities,
            ambiguous_entity_ids=ambiguous_ids,
        )
    if value is not None:
        updated = replace(updated, action_value=value, intent_confirmed=True)
        evidence.append(_provenance(document, "action_value", turn, str(value)))
    updated = replace(updated, provenance=tuple(evidence), turn_count=turn)
    return replace(updated, missing_slots=required_slots(updated))


def with_actions(
    draft: PendingAutomationSlotDraft,
    actions: tuple[ActionModel | ActionGroup, ...],
    document: LanguageDocument,
) -> PendingAutomationSlotDraft:
    first = actions[0] if len(actions) == 1 and isinstance(actions[0], ActionModel) else None
    target = draft.action_target
    if (
        first is not None
        and _target_is_specific(first.target)
        and not (
            draft.action_target is not None
            and draft.action_target.entity_id is not None
            and first.target is not None
            and first.target.entity_id is None
        )
    ):
        target = first.target
    typed_actions = actions
    if first is not None and target is not None and first.target != target:
        typed_actions = (replace(first, target=target),)
    value = first.value if first is not None and first.value is not None else draft.action_value
    kind = first.type if first is not None else draft.action_kind
    updated = replace(
        draft,
        actions=typed_actions,
        action_target=target,
        action_value=value,
        action_kind=kind,
        ambiguities=(),
        ambiguous_entity_ids=(),
        provenance=draft.provenance + (
            _provenance(document, "actions", draft.turn_count, ",".join(
                action.type.name for action in typed_actions if isinstance(action, ActionModel)
            ) or "group"),
        ),
    )
    return replace(updated, missing_slots=required_slots(updated))


def with_condition(
    draft: PendingAutomationSlotDraft,
    condition: ConditionNode,
    document: LanguageDocument,
) -> PendingAutomationSlotDraft:
    """Fill the explicitly requested condition slot with typed meaning."""
    turn = draft.turn_count
    updated = replace(
        draft,
        conditions=(condition,),
        turn_count=turn,
        provenance=draft.provenance
        + (_provenance(document, "condition", turn, "typed-condition"),),
    )
    return replace(updated, missing_slots=required_slots(updated))


def compile_slot_draft(draft: PendingAutomationSlotDraft) -> AutomationModel | None:
    if draft.missing_slots or draft.ambiguities or draft.unknown_terms or not draft.triggers:
        return None
    actions = draft.actions
    if not actions and draft.intent is AutomationIntentKind.HEATING_SETPOINT:
        if draft.action_target is None or draft.action_value is None:
            return None
        actions = (
            ActionModel(
                type=ActionType.SET_TEMPERATURE,
                target=draft.action_target,
                value=draft.action_value,
            ),
        )
    schedule = None
    if draft.clock is not None and draft.triggers[0].type is TriggerType.CALENDAR_TIME:
        reference = CalendarReference.NEXT_OCCURRENCE
        if draft.date_reference == "heute":
            reference = CalendarReference.TODAY
        elif draft.date_reference == "morgen":
            reference = CalendarReference.TOMORROW
        elif draft.date_reference == "uebermorgen":
            reference = CalendarReference.DAY_AFTER_TOMORROW
        schedule = CalendarSchedule(
            reference,
            draft.clock[0],
            draft.clock[1],
            spoken=_schedule_spoken(
                draft.date_reference, draft.clock[0], draft.clock[1]
            ),
        )
    return AutomationModel(
        triggers=draft.triggers,
        conditions=draft.conditions,
        actions=actions,
        source_text="typisierter Mehrturn-Auftrag",
        once=draft.date_reference is not None,
        scheduled_for=draft.scheduled_for,
        calendar_schedule=schedule,
    )


def revise_preview_model(
    document: LanguageDocument,
    model: AutomationModel,
    entities: Iterable[EntitySnapshot],
) -> AutomationModel | None:
    """Apply a narrow, typed correction to an unconfirmed model.

    A correction replaces only the explicitly evidenced slot.  No old/new
    utterance concatenation occurs, and ambiguous area grounding returns
    ``None`` so the existing confirmation remains unchanged.
    """
    words = _words(document)
    if not set(words) & {"nein", "lieber", "stattdessen", "aendere", "korrigiere"}:
        return None
    updated = model
    changed = False
    value = _number_before_unit(document, frozenset({"grad"}))
    if value is not None:
        candidates = [
            index for index, action in enumerate(updated.actions)
            if isinstance(action, ActionModel) and action.type is ActionType.SET_TEMPERATURE
        ]
        if len(candidates) != 1:
            return None
        actions = list(updated.actions)
        actions[candidates[0]] = replace(actions[candidates[0]], value=value)
        updated = replace(updated, actions=tuple(actions))
        changed = True

    clock = _clock_value(document)
    if clock is not None:
        hour, minute = clock
        if updated.calendar_schedule is not None:
            day = updated.calendar_schedule.spoken.split(" um ", 1)[0]
            updated = replace(
                updated,
                calendar_schedule=replace(
                    updated.calendar_schedule,
                    hour=hour,
                    minute=minute,
                    spoken=f"{day} um {hour:02d}:{minute:02d} Uhr",
                ),
            )
            changed = True
        elif len(updated.triggers) == 1 and updated.triggers[0].type is TriggerType.TIME:
            updated = replace(
                updated,
                triggers=(replace(
                    updated.triggers[0],
                    time_hour=hour,
                    time_minute=minute,
                    time_second=0,
                ),),
            )
            changed = True

    action_candidates = [
        (index, action) for index, action in enumerate(updated.actions)
        if isinstance(action, ActionModel) and action.target is not None
    ]
    domains = {action.target.domain for _index, action in action_candidates if action.target is not None}
    domain = next(iter(domains)) if len(domains) == 1 else None
    target, _resolved, ambiguities, _ambiguous_ids = _area_target(
        document, entities, domain=domain
    )
    if target is not None:
        if ambiguities or len(action_candidates) != 1:
            return None
        index, action = action_candidates[0]
        actions = list(updated.actions)
        actions[index] = replace(action, target=target)
        updated = replace(updated, actions=tuple(actions))
        changed = True
    return updated if changed else None


def next_question(draft: PendingAutomationSlotDraft) -> str:
    if draft.unknown_terms:
        return (
            "Was bedeutet „"
            + " ".join(draft.unknown_terms[:3])
            + "“ in diesem Automationsauftrag? Ich rate diese Angabe nicht."
        )
    if draft.ambiguities:
        if len(draft.ambiguities) == 1 and draft.ambiguities[0].startswith("Im "):
            return draft.ambiguities[0]
        return "Welche davon meinst du: " + " oder ".join(draft.ambiguities[:5]) + "?"
    missing = draft.missing_slots
    if RequiredAutomationSlot.INTENT in missing:
        return "Meinst du damit eine neue Automation? Was soll zu diesem Zeitpunkt passieren?"
    if RequiredAutomationSlot.REACTION in missing:
        return "Was soll dann passieren?"
    if RequiredAutomationSlot.OPERATION in missing:
        if draft.action_target is not None and draft.action_target.domain == "light":
            return "Was soll zu diesem Zeitpunkt mit dem Licht passieren?"
        if draft.sun_event is not None:
            return "Was soll nach dem Sonnenereignis passieren?"
        return "Was soll zu diesem Zeitpunkt passieren?"
    if RequiredAutomationSlot.TARGET in missing:
        if draft.intent is AutomationIntentKind.HEATING_SETPOINT:
            return "Welcher Raum soll warm sein?"
        return "Welches Gerät oder welchen Raum meinst du?"
    if RequiredAutomationSlot.ACTION_VALUE in missing:
        if draft.action_kind is ActionType.SET_TEMPERATURE:
            return "Auf welche Temperatur soll ich die Heizung stellen?"
        return "Welchen Wert soll ich einstellen?"
    if RequiredAutomationSlot.REPETITION in missing:
        return "Wie oft soll das gelten?"
    if RequiredAutomationSlot.CONDITION in missing:
        return "Welche zusätzliche Bedingung soll gelten?"
    return "Welche Angabe fehlt noch?"
