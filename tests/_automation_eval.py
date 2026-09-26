"""Evaluation harness for the 7.2.0 automation language corpora.

Every line runs through ``NluConversationEntity._async_handle_message`` with
an authenticated user.  The service boundary is instrumented: *any* service
call (notify or device) and any write to ``automations.yaml`` is recorded.
The meaning of a preview is read from the pending confirmation's
``AutomationModel`` and rendered as the implementation-independent signature
documented in ``tests/eval/automation_v72/README.md``.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import types
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import homeintent.conversation as ha_conversation
from _automation_world import AMBIGUOUS_WORLD, IPHONE, JULIA_PHONE, JULIA_USER, USER, WORLD
from _notify_sink import NotifySink
from homeintent.const import CONF_AGENT_NOTIFY_TARGETS
from homeintent.conversation import NluConversationEntity
from homeintent.entities import EntitySnapshot
from homeintent.nlu.action_model import ActionGroup, ActionModel, ActionType, NotificationRecipientKind
from homeintent.nlu.automation_model import AutomationModel, NumericComparator, TriggerModel, TriggerType
from homeintent.nlu.condition_model import ConditionNode, ConditionType, LogicalOperator, TimeComparator
from homeintent.nlu.ha_automation_generator import _resolve_target_entities
from homeintent.nlu.measurement import MeasurementProperty, TravelDirection
from homeintent.user_context import NotificationTarget, NotificationTargetKind, UserContextStore
from homeassistant.components.conversation import ConversationInput
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

CORPUS_DIR = Path(__file__).parent / "eval" / "automation_v72"
PREVIEW_MARKERS = ("Soll ich das so einrichten?", "Soll diese Automation erstellt werden?")

_OPS = {
    NumericComparator.EQUAL: "==", NumericComparator.ABOVE: ">", NumericComparator.BELOW: "<",
    NumericComparator.AT_LEAST: ">=", NumericComparator.AT_MOST: "<=",
}
_PROPS = {
    None: "state",
    MeasurementProperty.COVER_POSITION: "cover_position",
    MeasurementProperty.LIGHT_BRIGHTNESS: "brightness",
    MeasurementProperty.FAN_PERCENTAGE: "fan_percentage",
}
_PERSON_BY_TARGET = {IPHONE: "philipp", JULIA_PHONE: "julia"}


@dataclass(frozen=True)
class Case:
    source: str
    line: int
    category: str
    expect: str
    turns: tuple[str, ...]

    @property
    def kind(self) -> str:
        return self.expect.split(":", 1)[0].strip()

    @property
    def ambiguous_world(self) -> bool:
        return "ambiguous" in self.source


def load_cases(paths: Iterable[Path]) -> list[Case]:
    cases: list[Case] = []
    for path in paths:
        category = "uncategorized"
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if line.startswith("## "):
                category = line[3:].strip()
                continue
            if not line or line.startswith("#"):
                continue
            expect, _, text = line.partition(" :: ")
            turns = tuple(turn.strip() for turn in text.split(" >> "))
            cases.append(Case(path.name, number, category, expect.strip(), turns))
    return cases


# --- signatures --------------------------------------------------------------------


def _ids(target: Any, entities: list[EntitySnapshot]) -> str:
    if target is None:
        return "?"
    return ",".join(sorted(entity.entity_id for entity in _resolve_target_entities(target, entities)))


def _clock(hour: int | None, minute: int | None) -> str:
    return f"{hour or 0:02d}:{minute or 0:02d}"


def trigger_signature(trigger: TriggerModel, entities: list[EntitySnapshot]) -> str:
    if trigger.type is TriggerType.STATE:
        state = trigger.state.name.casefold() if trigger.state else "?"
        text = f"state({_ids(trigger.target, entities)})={state}"
    elif trigger.type is TriggerType.NUMERIC_STATE:
        op = _OPS.get(trigger.comparator, "?") if trigger.comparator else "?"
        text = f"num({_ids(trigger.target, entities)}).{_PROPS[trigger.measurement]}{op}{trigger.threshold:g}"
    elif trigger.type is TriggerType.TIME:
        text = f"time={_clock(trigger.time_hour, trigger.time_minute)}"
    elif trigger.type is TriggerType.SUN:
        event = trigger.sun_event.name.casefold() if trigger.sun_event else "?"
        offset = trigger.offset_minutes or 0
        text = f"sun={event}" + (f"{offset:+d}" if offset else "")
    elif trigger.type is TriggerType.PRESENCE:
        event = getattr(trigger, "presence_event", None)
        name = {"ARRIVE": "arrive", "LEAVE": "leave"}.get(getattr(event, "name", ""), "presence")
        text = f"{name}({_ids(trigger.target, entities)})"
    elif trigger.type is TriggerType.RELATIVE_TIME:
        text = f"in({trigger.relative_offset_seconds})"
    elif trigger.type is TriggerType.WEEKDAY:
        text = "weekday=" + ",".join(trigger.weekdays)
    else:
        text = trigger.type.name.casefold()
    if trigger.for_seconds:
        text += f"/for={trigger.for_seconds}"
    if trigger.direction is not None:
        text += "/dir=" + ("up" if trigger.direction is TravelDirection.UP else "down")
    return text


def _condition_signatures(node: ConditionNode, entities: list[EntitySnapshot], negated: bool = False) -> list[str]:
    if node.operator is LogicalOperator.AND and not negated:
        return [item for child in node.children for item in _condition_signatures(child, entities)]
    if node.operator is LogicalOperator.NOT and len(node.children) == 1:
        return _condition_signatures(node.children[0], entities, not negated)
    if node.operator is not None:
        return [f"{'not ' if negated else ''}{node.operator.name.casefold()}(...)"]
    condition = node.condition
    assert condition is not None
    if condition.type is ConditionType.PRESENCE and condition.target is None:
        home = condition.raw_state == "home"
        return ["anybody_home" if home != negated else "nobody_home"]
    prefix = "not " if negated else ""
    if condition.type is ConditionType.STATE:
        state = condition.state.name.casefold() if condition.state else "?"
        return [f"{prefix}state({_ids(condition.target, entities)})={state}"]
    if condition.type is ConditionType.NUMERIC:
        op = _OPS.get(condition.comparator, "?") if condition.comparator else "?"
        return [f"{prefix}num({_ids(condition.target, entities)}).state{op}{condition.threshold:g}"]
    if condition.type is ConditionType.TIME:
        rel = "<" if condition.time_comparator is TimeComparator.BEFORE else ">"
        return [f"{prefix}time{rel}{_clock(condition.time_hour, condition.time_minute)}"]
    if condition.type is ConditionType.WEEKDAY:
        return [f"{prefix}weekday=" + ",".join(condition.weekdays)]
    return [f"{prefix}{condition.type.name.casefold()}"]


def _action_signatures(step: ActionModel | ActionGroup, entities: list[EntitySnapshot]) -> list[tuple[str, str | None]]:
    if isinstance(step, ActionGroup):
        return [item for child in step.steps for item in _action_signatures(child, entities)]
    if step.type is ActionType.NOTIFY:
        recipient = step.recipient
        if recipient is None:
            who = "?"
        elif recipient.kind is NotificationRecipientKind.CURRENT_USER:
            who = "me"
        elif recipient.kind is NotificationRecipientKind.HOUSEHOLD:
            who = "us"
        else:
            who = next((_PERSON_BY_TARGET.get(item, item) for item in recipient.entity_ids), "?")
        return [(f"notify({who})", step.message)]
    if step.type is ActionType.DELAY:
        return [(f"delay={step.delay_seconds}", None)]
    ids = _ids(step.target, entities)
    if step.type is ActionType.SET_POSITION:
        value = step.value
        if value == 100:
            return [(f"open({ids})", None)]
        if value == 0:
            return [(f"close({ids})", None)]
        return [(f"position({ids})={value:g}" if isinstance(value, (int, float)) else f"position({ids})", None)]
    if step.type is ActionType.SET_BRIGHTNESS:
        return [(f"brightness({ids})={step.value}", None)]
    if step.type is ActionType.REGISTERED_SERVICE:
        service = step.service_name or "?"
        name = {"open_cover": "open", "close_cover": "close", "turn_on": "turn_on",
                "turn_off": "turn_off"}.get(service, service)
        return [(f"{name}({ids})", None)]
    resolved = _resolve_target_entities(step.target, entities) if step.target is not None else []
    if step.type in {ActionType.TURN_ON, ActionType.TURN_OFF} and resolved and all(
        entity.domain == "cover" for entity in resolved
    ):
        # The generator emits cover.open_cover / cover.close_cover for these.
        name = "open" if step.type is ActionType.TURN_ON else "close"
        return [(f"{name}({ids})", None)]
    if step.type is ActionType.TURN_ON and step.duration_seconds:
        # "an und nach 5 Minuten wieder aus" - the same meaning, spelled out.
        return [(f"turn_on({ids})", None), (f"delay={step.duration_seconds}", None),
                (f"turn_off({ids})", None)]
    return [(f"{step.type.name.casefold()}({ids})", None)]


@dataclass(frozen=True)
class Meaning:
    triggers: frozenset[str]
    conditions: frozenset[str]
    actions: tuple[str, ...]
    messages: tuple[str | None, ...]


def model_meaning(model: AutomationModel, entities: list[EntitySnapshot]) -> Meaning:
    triggers = {trigger_signature(trigger, entities) for trigger in model.triggers}
    if model.calendar_schedule is not None:
        schedule = model.calendar_schedule
        day = schedule.reference.name.casefold()
        triggers = {f"at({day} {_clock(schedule.hour, schedule.minute)})"}
    conditions = {
        item for node in model.conditions for item in _condition_signatures(node, entities)
    }
    pairs = [
        expanded
        for step in model.actions
        for item in _action_signatures(step, entities)
        for expanded in _per_entity(item)
    ]
    return Meaning(
        frozenset(triggers), frozenset(conditions),
        tuple(name for name, _ in pairs), tuple(message for _, message in pairs),
    )


_MULTI_RE = re.compile(r"^(?P<name>[a-z_]+)\((?P<ids>[^)]*,[^)]*)\)(?P<rest>.*)$")


def _per_entity(item: tuple[str, str | None]) -> list[tuple[str, str | None]]:
    """One device service call on N entities means the same as N calls."""
    name, message = item
    match = _MULTI_RE.match(name)
    if match is None or name.startswith("notify("):
        return [item]
    return [
        (f"{match.group('name')}({entity_id}){match.group('rest')}", message)
        for entity_id in match.group("ids").split(",")
    ]


_ACTION_RE = re.compile(r'^(?P<name>notify\((?P<who>[^,)]+)(?:,"(?P<message>.*)")?\))$')


def expected_meaning(expect: str) -> tuple[Meaning, tuple[str | None, ...]]:
    body = expect.split(":", 1)[1]
    head, _, actions = body.partition(" => ")
    triggers_part, _, conditions_part = head.partition(" ; if: ")
    triggers = frozenset(item.strip() for item in triggers_part.split(" | ") if item.strip())
    conditions = frozenset(item.strip() for item in conditions_part.split(" & ") if item.strip())
    names: list[str] = []
    messages: list[str | None] = []
    for raw in actions.split(" + "):
        raw = raw.strip()
        match = _ACTION_RE.match(raw)
        if match is not None:
            names.append(f"notify({match.group('who')})")
            messages.append(match.group("message"))
        else:
            for name, _ in _per_entity((raw, None)):
                names.append(name)
                messages.append(None)
    return Meaning(triggers, conditions, tuple(names), tuple(messages)), tuple(messages)


def _same_message(expected: str, actual: str | None) -> bool:
    def clean(value: str) -> str:
        return value.strip().strip("\"'„“”").rstrip(".!? ").casefold()

    return actual is not None and clean(expected) == clean(actual)


# --- running ----------------------------------------------------------------------


class _Runner:
    def __init__(self, world: tuple[EntitySnapshot, ...]) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hi-eval-"))
        self.world = list(world)
        options = {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, JULIA_PHONE]}
        self.entity = NluConversationEntity(ConfigEntry(options=options))
        self.entity.hass = HomeAssistant()
        self.entity.hass.config.path = lambda *parts: str(self.tmp.joinpath(*parts))
        ha_conversation.build_entity_snapshots = lambda hass, entry: self.world  # type: ignore[assignment]
        self.sink = NotifySink.install(self.entity.hass, (IPHONE, JULIA_PHONE))
        store = UserContextStore(self.tmp / "users.json")
        self.entity._runtime_data.user_contexts = store
        for user, person, target in (
            (USER, "person.philipp", IPHONE), (JULIA_USER, "person.julia", JULIA_PHONE)
        ):
            asyncio.run(store.async_set_user(
                user, person_entity_id=person, confirmed=True,
                notification_targets=[NotificationTarget(target, NotificationTargetKind.ENTITY)],
            ))
        asyncio.run(store.async_set_household(("person.philipp", "person.julia"), confirmed=True))
        self.counter = 0

    def say(self, text: str, conversation_id: str) -> str:
        context = types.SimpleNamespace(user_id=USER)
        result = asyncio.run(self.entity._async_handle_message(
            ConversationInput(text=text, conversation_id=conversation_id, context=context),
            chat_log=None,
        ))
        return str(result.response.speech)

    def automations_written(self) -> bool:
        path = self.tmp / "automations.yaml"
        return path.exists() and path.read_text(encoding="utf-8").strip() not in {"", "[]"}

    def pending_model(self, conversation_id: str) -> AutomationModel | None:
        context = self.entity._context_store.get(conversation_id)
        if context is None or context.pending_automation_confirmation is None:
            return None
        return context.pending_automation_confirmation.model


@dataclass
class CaseResult:
    case: Case
    outcome: str  # correct | clarify_instead | missed | wrong | unsafe | ...
    speech: str
    meaning: Meaning | None = None
    detail: str = ""


def run_cases(cases: list[Case]) -> list[CaseResult]:
    runners: dict[bool, _Runner] = {}
    results: list[CaseResult] = []
    for case in cases:
        runner = runners.get(case.ambiguous_world)
        if runner is None:
            runner = runners[case.ambiguous_world] = _Runner(
                AMBIGUOUS_WORLD if case.ambiguous_world else WORLD
            )
        runner.counter += 1
        conversation_id = f"eval-{runner.counter}"
        calls_before = len(runner.sink.notify_calls) + len([c for c in runner.sink.other_calls if c[:2] != ('automation', 'reload')])
        speech = ""
        for turn in case.turns:
            speech = runner.say(turn, conversation_id)
        model = runner.pending_model(conversation_id)
        calls = len(runner.sink.notify_calls) + len([c for c in runner.sink.other_calls if c[:2] != ('automation', 'reload')]) - calls_before
        written = runner.automations_written()
        results.append(_judge(case, speech, model, calls, written, runner))
        if case.kind in {"reject", "clarify", "unsupported"} and model is None:
            # A stray "Ja" must never create anything either.
            runner.say("Ja", conversation_id)
            later = len(runner.sink.notify_calls) + len([c for c in runner.sink.other_calls if c[:2] != ('automation', 'reload')]) - calls_before
            if later or runner.automations_written():
                results[-1] = CaseResult(case, "unsafe", speech, None, "action after stray Ja")
        runner.entity._context_store.clear(conversation_id)
        (runner.tmp / "automations.yaml").unlink(missing_ok=True)
    return results


def _judge(case: Case, speech: str, model: AutomationModel | None, calls: int, written: bool,
           runner: _Runner) -> CaseResult:
    kind = case.kind
    if kind == "immediate":
        ok = model is None and calls == 1 and not written
        return CaseResult(case, "correct" if ok else "wrong", speech)
    if calls or written:
        return CaseResult(case, "unsafe", speech, None, f"calls={calls} written={written}")
    preview = model is not None or any(marker in speech for marker in PREVIEW_MARKERS)
    if kind == "auto":
        if model is None:
            if speech.rstrip().endswith("?"):
                return CaseResult(case, "clarify_instead", speech)
            return CaseResult(case, "missed", speech)
        meaning = model_meaning(model, runner.world)
        expected, messages = expected_meaning(case.expect)
        same = (
            meaning.triggers == expected.triggers
            and meaning.conditions == expected.conditions
            and meaning.actions == expected.actions
            and all(
                wanted is None or _same_message(wanted, actual)
                for wanted, actual in zip(messages, meaning.messages)
            )
        )
        return CaseResult(case, "correct" if same else "wrong", speech, meaning)
    if preview:
        meaning = model_meaning(model, runner.world) if model is not None else None
        # A preview for something that is not an automation request is an
        # unsafe false positive even though nothing ran yet.
        return CaseResult(case, "false_preview", speech, meaning)
    if kind == "clarify":
        return CaseResult(case, "correct" if speech.rstrip().endswith("?") else "safe_no_question", speech)
    return CaseResult(case, "correct", speech)


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    by_kind: dict[str, Counter[str]] = {}
    for result in results:
        by_kind.setdefault(result.case.kind, Counter())[result.outcome] += 1
    automation = [r for r in results if r.case.kind == "auto"]
    supported_correct = sum(r.outcome == "correct" for r in automation)
    unsafe = sum(r.outcome == "unsafe" for r in results)
    false_preview = sum(r.outcome == "false_preview" for r in results)
    return {
        "total": len(results),
        "automation_cases": len(automation),
        "understood_correctly": supported_correct,
        "clarification_instead_of_automation": sum(r.outcome == "clarify_instead" for r in automation),
        "missed_automation": sum(r.outcome == "missed" for r in automation),
        "wrong_semantic_interpretation": sum(r.outcome == "wrong" for r in automation),
        "clarification_correctly_requested": by_kind.get("clarify", Counter())["correct"],
        "clarification_cases": sum(by_kind.get("clarify", Counter()).values()),
        "unsupported_correctly_rejected": by_kind.get("unsupported", Counter())["correct"],
        "unsupported_cases": sum(by_kind.get("unsupported", Counter()).values()),
        "negative_correct": by_kind.get("reject", Counter())["correct"],
        "negative_cases": sum(by_kind.get("reject", Counter()).values()),
        "unsafe_false_positive_previews": false_preview,
        "unsafe_wrong_execution": unsafe,
        "automation_accuracy": round(supported_correct / len(automation), 4) if automation else 1.0,
        "by_kind": {kind: dict(counter) for kind, counter in sorted(by_kind.items())},
    }


def failures(results: list[CaseResult]) -> list[str]:
    lines = []
    for result in results:
        if result.outcome == "correct":
            continue
        meaning = ""
        if result.meaning is not None:
            m = result.meaning
            meaning = f" got={' | '.join(sorted(m.triggers))}; if: {' & '.join(sorted(m.conditions))} => {' + '.join(m.actions)} {m.messages}"
        lines.append(
            f"[{result.outcome}] {result.case.source}:{result.case.line} {result.case.expect} :: "
            f"{' >> '.join(result.case.turns)}\n      speech={result.speech!r}{meaning}"
        )
    return lines
