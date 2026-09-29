from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub

_ha_stub.install()

import homeintent.conversation as ha_conversation
import homeintent.profiles as profile_module
from _notify_sink import LEGACY_NOTIFY_SCHEMA, SEND_MESSAGE_SCHEMA
from homeintent.agent_delivery import AgentDelivery
from homeintent.agent_event import AgentEvent, AgentEventState, AgentMode
from homeintent.agent_runtime import ProactiveAgentRuntime
from homeintent.automation_executor import AutomationExecutor
from homeintent.conversation import NluConversationEntity
from homeintent.dialog_manager import DialogTaskKind
from homeintent.entities import EntitySnapshot
from homeintent.execution_coordinator import ConflictOutcome, ExecutionCoordinator
from homeintent.goal_model import (
    DesiredState,
    GoalCondition,
    GoalKind,
    GoalLifecycle,
    GoalModel,
    GoalProvenance,
    GoalScope,
    GoalSemanticChoice,
    GoalTrigger,
)
from homeintent.goal_run import (
    FailureCode,
    GoalRun,
    GoalRunQuery,
    GoalRunStatus,
    GoalRunStore,
    StepExecutionRecord,
    VerificationRecord,
)
from homeintent.nlu.language_frontend import analyse_language
from homeintent.nlu.temporal_semantics import resolve_history_window
from homeintent.monitor_goal import MonitorGoalRuntime, MonitorGoalStore, MonitorRecord
from homeintent.planner import MaterializedPlan, PlanStep, StepKind
from homeintent.risk import RiskLevel
from homeintent.runtime_data import HomeIntentRuntimeData
from homeintent.profiles import (
    ComfortProfile,
    ProfileStore,
    RoutineDefinition,
    RoutineStepDefinition,
)
from homeintent.service_call import ServiceCallPlan
from homeintent.user_context import (
    BindingStatus,
    NotificationTarget,
    NotificationTargetKind,
    UserContextStore,
)
from homeassistant.components.conversation import ConversationInput
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State


LOCAL = timezone.utc


def _failed_run(
    goal: GoalModel,
    when: datetime,
    *,
    entity_id: str = "light.living",
    observed: str = "on",
) -> GoalRun:
    return replace(
        GoalRun.start(
            goal,
            user_id="owner",
            person_entity_id="person.owner",
            now=when,
        ),
        status=GoalRunStatus.FAILURE,
        failures=(FailureCode.WRONG_STATE,),
        selected_targets=(entity_id,),
        steps=(
            StepExecutionRecord(
                "step",
                "HOMEASSISTANT_TURN_OFF",
                (entity_id,),
                (),
                True,
                (
                    VerificationRecord(
                        entity_id,
                        "off",
                        observed,
                        False,
                        FailureCode.WRONG_STATE,
                    ),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("text", "start", "end"),
    (
        ("heute", "2026-09-20T00:00:00+00:00", "2026-09-21T00:00:00+00:00"),
        ("gestern", "2026-09-19T00:00:00+00:00", "2026-09-20T00:00:00+00:00"),
        ("vorgestern", "2026-09-18T00:00:00+00:00", "2026-09-19T00:00:00+00:00"),
        ("heute Morgen", "2026-09-20T05:00:00+00:00", "2026-09-20T12:00:00+00:00"),
        ("heute Abend", "2026-09-20T18:00:00+00:00", "2026-09-21T00:00:00+00:00"),
        ("gestern Morgen", "2026-09-19T05:00:00+00:00", "2026-09-19T12:00:00+00:00"),
        ("gestern Abend", "2026-09-19T18:00:00+00:00", "2026-09-20T00:00:00+00:00"),
        ("letzte Nacht", "2026-09-19T20:00:00+00:00", "2026-09-20T06:00:00+00:00"),
    ),
)
def test_history_windows_are_local_half_open_intervals(text, start, end):
    document = analyse_language(text)
    window = resolve_history_window(
        document.tokens, datetime(2026, 9, 20, 12, tzinfo=LOCAL)
    )
    assert window is not None
    assert window.start.isoformat() == start
    assert window.end.isoformat() == end


def test_goal_run_query_composes_time_routine_entity_and_user(tmp_path):
    store = GoalRunStore(tmp_path / "runs.json")
    movie = GoalModel(
        GoalKind.PREPARE_MOVIE,
        goal_id="movie",
        routine_id="filmabend",
        provenance=GoalProvenance("Filmabend", "owner"),
    )
    sleep = GoalModel(
        GoalKind.PREPARE_NIGHT,
        goal_id="sleep",
        routine_id="schlafengehen",
        provenance=GoalProvenance("Schlafengehen", "owner"),
    )
    asyncio.run(store.async_append(_failed_run(movie, datetime(2026, 9, 19, 19, tzinfo=LOCAL))))
    asyncio.run(store.async_append(_failed_run(sleep, datetime(2026, 9, 19, 22, tzinfo=LOCAL), entity_id="light.bed")))
    asyncio.run(store.async_append(replace(
        _failed_run(movie, datetime(2026, 9, 20, 12, tzinfo=LOCAL)),
        status=GoalRunStatus.SUCCESS,
        failures=(),
    )))
    asyncio.run(store.async_append(replace(
        _failed_run(movie, datetime(2026, 9, 19, 20, tzinfo=LOCAL)),
        run_id="legacy-naive",
        created_at="2026-09-19T20:00:00",
    )))

    matches = asyncio.run(store.async_query(GoalRunQuery(
        user_id="owner",
        start_time=datetime(2026, 9, 19, tzinfo=LOCAL),
        end_time=datetime(2026, 9, 20, tzinfo=LOCAL),
        failed_only=True,
        routine_id="filmabend",
        entity_id="light.living",
    )))
    assert [run.goal_id for run in matches] == ["movie"]


def test_historical_conversation_uses_yesterday_and_clarifies_multiple(
    monkeypatch, tmp_path
):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    store = GoalRunStore(tmp_path / "runs.json")
    entity._runtime_data.goal_runs = store
    movie = GoalModel(
        GoalKind.PREPARE_MOVIE,
        goal_id="movie",
        routine_id="filmabend",
        provenance=GoalProvenance("Filmabend", "owner"),
    )
    sleep = GoalModel(
        GoalKind.PREPARE_NIGHT,
        goal_id="sleep",
        routine_id="schlafengehen",
        provenance=GoalProvenance("Schlafengehen", "owner"),
    )
    asyncio.run(store.async_append(_failed_run(movie, datetime(2026, 9, 19, 19, tzinfo=LOCAL))))
    asyncio.run(store.async_append(_failed_run(sleep, datetime(2026, 9, 19, 22, tzinfo=LOCAL), entity_id="light.bed")))
    asyncio.run(store.async_append(replace(
        _failed_run(movie, datetime(2026, 9, 20, 12, tzinfo=LOCAL)),
        status=GoalRunStatus.SUCCESS,
        failures=(),
    )))
    monkeypatch.setattr(ha_conversation.dt_util, "now", lambda: datetime(2026, 9, 20, 15, tzinfo=LOCAL))
    history_entities = [
        EntitySnapshot("light.living", "Wohnzimmerlicht", "light", "on"),
        EntitySnapshot("light.bed", "Schlafzimmerlicht", "light", "on"),
    ]
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: history_entities
    )

    async def turn(text: str, conversation_id: str):
        return await entity._async_handle_message(
            ConversationInput(text=text, conversation_id=conversation_id, context=SimpleNamespace(user_id="owner")),
            None,
        )

    broad = asyncio.run(turn("Warum hat das gestern nicht funktioniert?", "history-many"))
    assert "zwei" in broad.response.speech
    assert "Filmabend" in broad.response.speech
    assert "Schlafengehen" in broad.response.speech
    active = entity._runtime_data.dialog_manager.active("history-many")
    assert active is not None and active.kind is DialogTaskKind.GOAL_RUN_CLARIFICATION

    selected = asyncio.run(turn("Warum hat gestern der Filmabend nicht funktioniert?", "history-one"))
    assert "light.living" in selected.response.speech
    assert "light.bed" not in selected.response.speech

    entity_selected = asyncio.run(turn(
        "Warum ging gestern beim Filmabend das Wohnzimmerlicht nicht aus?",
        "history-entity",
    ))
    assert "light.living" in entity_selected.response.speech

    anonymous = asyncio.run(entity._async_handle_message(
        ConversationInput(
            text="Warum hat das gestern nicht funktioniert?",
            conversation_id="history-anonymous",
            context=SimpleNamespace(user_id=None),
        ),
        None,
    ))
    assert "Benutzerzuordnung" in anonymous.response.speech


@pytest.mark.parametrize(
    ("reply", "expected"),
    (
        ("Ersteres.", GoalSemanticChoice.SETPOINT_AT_TIME),
        ("Zum Zeitpunkt den Sollwert setzen.", GoalSemanticChoice.SETPOINT_AT_TIME),
        ("Stell sie dann einfach auf 21 Grad.", GoalSemanticChoice.SETPOINT_AT_TIME),
        ("Zweiteres.", GoalSemanticChoice.ACHIEVE_BY_DEADLINE),
        ("Bis dahin soll es warm sein.", GoalSemanticChoice.ACHIEVE_BY_DEADLINE),
    ),
)
def test_temperature_clarification_replies_are_structured(
    reply: str, expected: GoalSemanticChoice
):
    import homeintent.controllers.goals as goal_controller

    assert goal_controller._goal_semantic_choice(analyse_language(reply)) is expected


def test_temperature_goal_semantic_followup_keeps_goal_and_builds_scheduled_plan(
    monkeypatch, tmp_path,
):
    climate = EntitySnapshot(
        "climate.living",
        "Wohnzimmerheizung",
        "climate",
        "heat",
        area_id="wohnzimmer",
        area_name="Wohnzimmer",
        attributes={"temperature": 19.0},
    )
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    entity._runtime_data.goal_runs = GoalRunStore(tmp_path / "temperature-runs.json")
    entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        AutomationExecutor, "_assign_homeintent_category", lambda *_args: None
    )
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [climate])
    monkeypatch.setattr(ha_conversation.dt_util, "now", lambda: datetime(2026, 9, 20, 12, tzinfo=LOCAL))

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(text=text, conversation_id="temperature-goal", context=SimpleNamespace(user_id="owner")),
            None,
        )

    question = asyncio.run(turn("Sorge dafür, dass es morgen um 7 Uhr im Wohnzimmer 21 Grad hat."))
    assert "Soll die Heizung" in question.response.speech
    task = entity._runtime_data.dialog_manager.active("temperature-goal")
    assert task is not None and task.kind is DialogTaskKind.GOAL_SEMANTIC_CLARIFICATION
    assert task.payload.goal.scope.area_id == "wohnzimmer"
    assert task.payload.goal.desired_states[0].value == 21

    preview = asyncio.run(turn("Ersteres."))
    assert "Planvorschau" in preview.response.speech
    plan_task = entity._runtime_data.dialog_manager.active("temperature-goal")
    assert plan_task is not None and plan_task.kind is DialogTaskKind.PLAN_CONFIRMATION
    plan = plan_task.slots["plan"]
    scheduled = next(step for step in plan.steps if step.kind is StepKind.ACTION)
    assert scheduled.action.entity_id == "climate.living"
    assert scheduled.scheduled_for == datetime(2026, 9, 21, 7, tzinfo=LOCAL)
    entity.hass.services.async_call.assert_not_awaited()

    confirmed = asyncio.run(turn("Ja."))
    recorded = asyncio.run(entity._runtime_data.goal_runs.async_list())
    assert "persistent" in confirmed.response.speech.casefold(), repr(recorded)
    persisted = (tmp_path / "automations.yaml").read_text(encoding="utf-8")
    assert "climate.set_temperature" in persisted
    assert "07:00:00" in persisted
    assert "temperature: 21.0" in persisted
    metadata = (tmp_path / "homeintent_automation_metadata.json").read_text(
        encoding="utf-8"
    )
    assert "2026-09-21T07:00:00+00:00" in metadata


def test_temperature_achieve_by_deadline_without_model_is_safe(monkeypatch):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="wohnzimmer"
    )
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [climate])

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(text=text, conversation_id="temperature-deadline", context=SimpleNamespace(user_id="owner")),
            None,
        )

    asyncio.run(turn("Sorge dafür, dass es morgen um 7 Uhr im Wohnzimmer 21 Grad hat."))
    answer = asyncio.run(turn("Zweiteres."))
    assert "thermisches Modell" in answer.response.speech
    assert entity._runtime_data.dialog_manager.active("temperature-deadline") is None
    entity.hass.services.async_call.assert_not_awaited()


def _plan(*targets: str) -> MaterializedPlan:
    steps = tuple(
        PlanStep(
            str(index),
            StepKind.ACTION,
            target,
            action=ServiceCallPlan("homeassistant", "turn_off", target, {}),
        )
        for index, target in enumerate(targets)
    )
    return MaterializedPlan(
        "plan", GoalModel(GoalKind.ACHIEVE_STATE), steps, RiskLevel.LOW, True, "test"
    )


def test_execution_coordinator_conflicts_deduplicates_and_releases():
    coordinator = ExecutionCoordinator()

    async def scenario():
        first = await coordinator.async_acquire("run-a", _plan("light.a", "light.a"))
        same = await coordinator.async_acquire("run-a", _plan("light.a"))
        conflict = await coordinator.async_acquire("run-b", _plan("light.a", "cover.b"))
        independent = await coordinator.async_acquire("run-c", _plan("cover.b"))
        try:
            raise RuntimeError("execution failed")
        except RuntimeError:
            await coordinator.async_release("run-a")
        recovered = await coordinator.async_acquire("run-b", _plan("light.a"))
        return first, same, conflict, independent, recovered

    first, same, conflict, independent, recovered = asyncio.run(scenario())
    assert first.entity_ids == ("light.a",)
    assert first.outcome is same.outcome is ConflictOutcome.ACQUIRED
    assert conflict.outcome is ConflictOutcome.CONFLICT
    assert independent.outcome is ConflictOutcome.ACQUIRED
    assert recovered.outcome is ConflictOutcome.ACQUIRED


def _agent_event() -> AgentEvent:
    stamp = datetime(2026, 9, 20, tzinfo=LOCAL).isoformat()
    return AgentEvent(
        "event-id", "rule", "key", "HomeIntent", "Message", AgentMode.ASK,
        AgentEventState.ACTIVE, stamp, stamp, stamp,
        proposed_action=None,
    )


def test_rule_push_to_notify_entities_matches_send_message_schema():
    """Configured targets are notify entities; ``notify.send_message``
    rejects any key but message/title, so no tag or button payload is sent
    (the event stays answerable in the dashboard and by voice)."""
    hass = HomeAssistant()
    delivery = AgentDelivery(hass)
    event = _agent_event()
    asyncio.run(delivery.async_deliver(event, {
        "agent_delivery_channels": ["push"],
        "agent_notify_targets": ["notify.phone"],
    }))
    args = hass.services.async_call.await_args.args
    assert args[:2] == ("notify", "send_message")
    assert SEND_MESSAGE_SCHEMA(args[2]) == {
        "entity_id": ["notify.phone"], "title": event.title, "message": event.message,
    }


@pytest.mark.parametrize("prefix", ("HOMEINTENT_IGNORE_", "HA_NLU_IGNORE_"))
def test_canonical_and_legacy_notification_actions_are_accepted(tmp_path, prefix):
    hass = HomeAssistant()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    runtime = ProactiveAgentRuntime(hass, ConfigEntry(), HomeIntentRuntimeData())

    async def scenario():
        created = await runtime.async_signal({"rule_id": prefix, "message": "Message"})
        assert created is not None
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"{prefix}{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    assert asyncio.run(scenario()).state is AgentEventState.ACKNOWLEDGED


def test_typed_notification_entity_and_service_paths_are_exact():
    hass = HomeAssistant()
    hass.states._states["notify.phone"] = State("notify.phone", "unknown")
    hass.services.async_register("notify", "mobile_app_phone", lambda _call: None)
    delivery = AgentDelivery(hass)

    asyncio.run(delivery.async_deliver_typed_notification(
        "notify.phone", target_kind=NotificationTargetKind.ENTITY,
        title="Title", message="Message", dedupe_key="one", severity="warning",
        goal_id="goal", run_id="run",
    ))
    asyncio.run(delivery.async_deliver_typed_notification(
        "notify.mobile_app_phone", target_kind=NotificationTargetKind.SERVICE,
        title="Title", message="Message", dedupe_key="two", severity="warning",
        goal_id="goal", run_id="run",
    ))
    entity_call, service_call = hass.services.async_call.await_args_list
    assert entity_call.args[:2] == ("notify", "send_message")
    assert SEND_MESSAGE_SCHEMA(entity_call.args[2]) == {
        "entity_id": ["notify.phone"], "title": "Title", "message": "Message",
    }
    assert service_call.args[:2] == ("notify", "mobile_app_phone")
    assert LEGACY_NOTIFY_SCHEMA(service_call.args[2])["data"]["tag"] == "two"

    # Buttons cannot be carried by an entity target: refused, not dropped.
    assert delivery.supports_actions("notify.phone", NotificationTargetKind.ENTITY) is False
    assert delivery.supports_actions("notify.mobile_app_phone", NotificationTargetKind.SERVICE) is True
    with pytest.raises(ValueError):
        asyncio.run(delivery.async_deliver_typed_notification(
            "notify.phone", target_kind=NotificationTargetKind.ENTITY,
            title="Title", message="Message", dedupe_key="x", severity="warning",
            goal_id="goal", run_id="run", actions=(("HOMEINTENT_V12_ACCEPT_x", "Ok"),),
        ))

    with pytest.raises(ValueError):
        asyncio.run(delivery.async_deliver_typed_notification(
            "notify.missing", target_kind=NotificationTargetKind.SERVICE,
            title="Title", message="Message", dedupe_key="three", severity="warning",
            goal_id="goal", run_id="run",
        ))


def test_user_context_validation_shared_binding_restart_and_old_store(tmp_path):
    path = tmp_path / "users.json"
    store = UserContextStore(path)
    assert store.resolve_current_person(None).status is BindingStatus.MISSING
    with pytest.raises(ValueError):
        asyncio.run(store.async_set_user("", person_entity_id=None, confirmed=True))
    with pytest.raises(ValueError):
        asyncio.run(store.async_set_user("one", person_entity_id="device.phone", confirmed=True))
    with pytest.raises(ValueError):
        asyncio.run(store.async_set_user(
            "one", person_entity_id="person.one",
            notification_targets=(NotificationTarget("light.invalid"),), confirmed=True,
        ))
    asyncio.run(store.async_set_user(
        "one", person_entity_id="person.one",
        notification_targets=(
            NotificationTarget("notify.phone", NotificationTargetKind.ENTITY, preferred=True),
            NotificationTarget("notify.mobile_app_phone", NotificationTargetKind.SERVICE),
        ),
        confirmed=True,
    ))
    with pytest.raises(ValueError):
        asyncio.run(store.async_set_user("two", person_entity_id="person.one", confirmed=True))
    asyncio.run(store.async_set_user(
        "two", person_entity_id="person.one", confirmed=True, allow_shared_person=True
    ))
    assert store.nobody_home({"person.one": "not_home"}) is None
    asyncio.run(store.async_set_household(("person.one",), confirmed=True))
    assert store.nobody_home({"person.one": "not_home"}) is True
    assert store.nobody_home({"person.one": "home"}) is False
    asyncio.run(store.async_set_household(
        ("person.one", "person.two", "person.three"), confirmed=True
    ))
    assert store.nobody_home({
        "person.one": "not_home", "person.two": "not_home", "person.three": "not_home"
    }) is True
    assert store.nobody_home({
        "person.one": "not_home", "person.two": "home", "person.three": "not_home"
    }) is False

    reloaded = UserContextStore(path)
    asyncio.run(reloaded.async_load())
    targets = reloaded.resolve_notification_targets("person.one")
    assert targets.status is BindingStatus.RESOLVED
    assert targets.targets[0].kind is NotificationTargetKind.ENTITY

    path.write_text('{"schema_version":0,"users":{"bad":42},"household_person_ids":["device.bad"]}')
    asyncio.run(reloaded.async_load())
    assert reloaded.resolve_current_person("bad").status is BindingStatus.MISSING
    assert reloaded.household.person_entity_ids == ()


def test_profiles_validate_confirm_reload_ambiguity_and_corrupt_records(
    monkeypatch, tmp_path
):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    step = RoutineStepDefinition(
        "one", GoalScope(entity_ids=("light.one",)), DesiredState("state", "off")
    )
    with pytest.raises(ValueError):
        asyncio.run(store.async_save_routine(
            RoutineDefinition("night", "Night", "owner", (step,), False), confirmed=True
        ))
    with pytest.raises(ValueError):
        asyncio.run(store.async_save_comfort_profile(
            ComfortProfile("bad", "owner", "living", brightness_min=101, confirmed=True),
            confirmed=True,
        ))
    with pytest.raises(ValueError):
        asyncio.run(store.async_save_comfort_profile(
            ComfortProfile("bad", "owner", "living", temperature_min=22, temperature_max=20, confirmed=True),
            confirmed=True,
        ))
    with pytest.raises(ValueError):
        asyncio.run(store.async_save_comfort_profile(
            ComfortProfile("bad", "owner", "living", temperature_min=-10, confirmed=True),
            confirmed=True,
        ))
    with pytest.raises(ValueError):
        asyncio.run(store.async_save_comfort_profile(
            ComfortProfile("bad", "owner", "living", cover_position=-1, confirmed=True),
            confirmed=True,
        ))

    first = RoutineDefinition("night-one", "Night", "owner", (step,), True)
    second = RoutineDefinition("night-two", "Night", "owner", (step,), True)
    asyncio.run(store.async_save_routine(first, confirmed=True))
    asyncio.run(store.async_save_routine(second, confirmed=True))
    profile = ComfortProfile(
        "comfort", "owner", "living", temperature_min=20, temperature_max=22,
        brightness_min=20, brightness_max=60, cover_position=50, confirmed=True,
    )
    asyncio.run(store.async_save_comfort_profile(profile, confirmed=True))
    assert store.routine("Night", user_id="owner") is None
    assert store.routine("night-one", user_id="other") is None

    unchanged = path.read_text(encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated atomic replace failure")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(profile_module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="atomic replace"):
            asyncio.run(store.async_save_routine(
                RoutineDefinition("new", "New", "owner", (step,), True),
                confirmed=True,
            ))
    assert path.read_text(encoding="utf-8") == unchanged

    reloaded = ProfileStore(path)
    asyncio.run(reloaded.async_load())
    assert reloaded.routine("night-one", user_id="owner") == first
    assert reloaded.comfort(area_id="living", user_id="owner") == profile
    path.write_text('{"routines":[{"broken":true}],"comfort_profiles":[{"profile_id":"x"}]}')
    asyncio.run(reloaded.async_load())
    assert reloaded.routine("night-one", user_id="owner") is None


def _monitor(goal_id: str, recipients=("person.one",)) -> GoalModel:
    return GoalModel(
        GoalKind.MONITOR_AND_NOTIFY,
        goal_id=goal_id,
        trigger=GoalTrigger("person_leaves_zone", "person.one", "home", "home", "not_home"),
        conditions=(
            GoalCondition(
                "open_entities", GoalScope(domain="binary_sensor", device_class="window"),
                "non_empty", True, True,
            ),
        ),
        recipient_person_ids=recipients,
        provenance=GoalProvenance("Fensterwarnung", "owner", confirmed=True),
        lifecycle=GoalLifecycle.MONITOR,
    )


def test_monitor_restart_multiple_goals_recipients_missing_and_removed_entities(tmp_path):
    users = UserContextStore(tmp_path / "users.json")
    asyncio.run(users.async_set_user(
        "one", person_entity_id="person.one",
        notification_targets=(NotificationTarget("notify.one", NotificationTargetKind.ENTITY),),
        confirmed=True,
    ))
    asyncio.run(users.async_set_user(
        "two", person_entity_id="person.two", notification_targets=(), confirmed=True,
    ))
    goals = MonitorGoalStore(tmp_path / "goals.json")
    asyncio.run(goals.async_save(MonitorRecord(_monitor("one"), cooldown_seconds=0)))
    asyncio.run(goals.async_save(MonitorRecord(_monitor("two", ("person.one",)), cooldown_seconds=0)))
    reloaded = MonitorGoalStore(tmp_path / "goals.json")
    assert len(asyncio.run(reloaded.async_load())) == 2
    runs = GoalRunStore(tmp_path / "runs.json")
    current = [
        EntitySnapshot("person.one", "One", "person", "not_home"),
        EntitySnapshot(
            "binary_sensor.window", "Window", "binary_sensor", "on", device_class="window"
        ),
    ]
    deliveries: list[str] = []

    async def fresh():
        return list(current)

    async def deliver(model, _rendered):
        deliveries.append(model.goal_id)
        return True

    runtime = MonitorGoalRuntime(reloaded, runs, users, fresh, deliver)
    results = asyncio.run(runtime.async_process_person_transition(
        "person.one", "home", "not_home",
        occurred_at=datetime(2026, 9, 20, 12, tzinfo=LOCAL), occurrence_id="event-one",
    ))
    assert len(results) == 2
    assert set(deliveries) == {"one", "two"}

    current.pop()
    empty = asyncio.run(runtime.async_process_person_transition(
        "person.one", "home", "not_home",
        occurred_at=datetime(2026, 9, 20, 13, tzinfo=LOCAL), occurrence_id="event-two",
    ))
    assert len(empty) == 2
    assert all(run.evidence == ("fresh_runtime_query_empty",) for run in empty)

    missing_goal = _monitor("missing", ("person.one", "person.two"))
    asyncio.run(reloaded.async_save(MonitorRecord(missing_goal, cooldown_seconds=0)))
    current.append(EntitySnapshot(
        "binary_sensor.new_window", "New Window", "binary_sensor", "on", device_class="window"
    ))
    failed = asyncio.run(runtime.async_process_person_transition(
        "person.one", "home", "not_home",
        occurred_at=datetime(2026, 9, 20, 14, tzinfo=LOCAL), occurrence_id="event-three",
    ))
    missing = next(run for run in failed if run.goal_id == "missing")
    assert missing.failures == (FailureCode.NOTIFICATION_TARGET_MISSING,)
    assert "missing" not in deliveries

    duplicate = asyncio.run(runtime.async_process_person_transition(
        "person.one", "home", "not_home",
        occurred_at=datetime(2026, 9, 20, 14, tzinfo=LOCAL), occurrence_id="event-three",
    ))
    assert duplicate == ()
    returned_home = asyncio.run(runtime.async_process_person_transition(
        "person.one", "not_home", "home",
        occurred_at=datetime(2026, 9, 20, 14, 1, tzinfo=LOCAL), occurrence_id="event-four",
    ))
    assert returned_home == ()


def test_monitor_store_rejects_wrong_kind_and_deletes(tmp_path):
    store = MonitorGoalStore(tmp_path / "goals.json")
    with pytest.raises(ValueError):
        asyncio.run(store.async_save(MonitorRecord(GoalModel(GoalKind.ACHIEVE_STATE, goal_id="x"))))
    with pytest.raises(ValueError):
        asyncio.run(store.async_save(MonitorRecord(GoalModel(GoalKind.MONITOR_AND_NOTIFY))))
    assert asyncio.run(store.async_delete("missing")) is False
    asyncio.run(store.async_save(MonitorRecord(_monitor("stored"))))
    assert asyncio.run(store.async_delete("stored")) is True
