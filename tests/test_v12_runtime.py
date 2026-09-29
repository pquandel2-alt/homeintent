"""V12 Home Assistant glue: ProactiveRuntime ports, delivery payloads, hooks."""

from __future__ import annotations

from _ha_stub import ServiceMock  # noqa: E402
import asyncio
import re
import sys
import types
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from v12_harness import NOW, entity, garage_states

import homeintent.proactive_runtime as proactive_runtime
from homeintent.proactive_engine import OutgoingMessage
from homeintent.proactive_model import (
    CommunicationChannel,
    CommunicationDecision,
    PriorityLevel,
    ProposalState,
    SituationKind,
)
from homeintent.runtime_data import HomeIntentRuntimeData
from _notify_sink import LEGACY_NOTIFY_SCHEMA, SEND_MESSAGE_SCHEMA
from homeintent.user_context import NotificationTarget, NotificationTargetKind, UserContextStore
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State


class Clock:
    now = NOW


@pytest.fixture
def scheduled(monkeypatch):
    calls: list = []
    module = types.ModuleType("homeassistant.helpers.event")

    def track(hass, action, at):
        record = SimpleNamespace(action=action, at=at, cancelled=False)
        calls.append(record)

        def cancel():
            record.cancelled = True

        return cancel

    module.async_track_point_in_utc_time = track
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", module)
    return calls


def _registry(monkeypatch, entries):
    import homeassistant.helpers.device_registry as dr
    import homeassistant.helpers.entity_registry as er

    by_id = {item.entity_id: item for item in entries}
    fake = SimpleNamespace(entities=by_id, async_get=lambda entity_id: by_id.get(entity_id))
    monkeypatch.setattr(er, "async_get", lambda hass: fake)
    devices = SimpleNamespace(async_get=lambda device_id: SimpleNamespace(area_id="living_room") if device_id == "dev_area" else None)
    monkeypatch.setattr(dr, "async_get", lambda hass: devices)


async def _runtime(
    tmp_path, monkeypatch, *, options=None, states=None,
    philipp_kind=NotificationTargetKind.SERVICE,
):
    """``philipp_kind`` defaults to a legacy mobile_app *service* binding:
    only ``notify.<service>`` renders actionable buttons.  A notify entity
    (``notify.send_message``) accepts nothing but message/title."""
    hass = HomeAssistant()
    calls: list = []

    async def record(domain, service, data, blocking=False):
        if domain == "notify":
            # The exact schemas Home Assistant 2026.9 enforces - an extra
            # ``data`` key on send_message fails here as it does in HA.
            (SEND_MESSAGE_SCHEMA if service == "send_message" else LEGACY_NOTIFY_SCHEMA)(dict(data))
        calls.append((domain, service, dict(data)))

    hass.services.async_call = ServiceMock(side_effect=record)
    hass.services.has_service = lambda domain, service: True
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(
        side_effect=lambda user_id: SimpleNamespace(is_admin=user_id == "philipp")))
    for person, value in (("person.philipp", "home"), ("person.anna", "not_home")):
        hass.states._states[person] = State(person, value)
    entry = ConfigEntry()
    entry.options = {
        "proactive_context_enabled": True,
        "proactive_person_room_sensors": "person.philipp=sensor.philipp_area",
        "proactive_satellite_areas": "assist_satellite.office=office",
        "proactive_user_quiet_hours": "anna=13:00-15:00\nbroken",
        "proactive_entry_open_minutes": 10,
        "proactive_appliance_entities": ["sensor.washer"],
        **(options or {}),
    }
    data = HomeIntentRuntimeData()
    contexts = UserContextStore(tmp_path / "users.json")
    await contexts.async_set_user(
        "philipp", person_entity_id="person.philipp", confirmed=True,
        notification_targets=(NotificationTarget("notify.mobile_app_philipp", philipp_kind),),
    )
    await contexts.async_set_user(
        "anna", person_entity_id="person.anna", confirmed=True,
        notification_targets=(
            NotificationTarget("notify.a1", NotificationTargetKind.ENTITY),
            NotificationTarget("notify.a2", NotificationTargetKind.ENTITY),
        ),
    )
    await contexts.async_set_household(("person.philipp", "person.anna"), confirmed=True)
    data.user_contexts = contexts
    entities = {item.entity_id: item for item in (states or garage_states())}
    entities["sensor.philipp_area"] = replace(
        entity("sensor.philipp_area", "Philipp Raum", "Wohnzimmer"),
        last_changed=Clock.now - timedelta(minutes=1), last_updated=Clock.now,
    )
    monkeypatch.setattr(proactive_runtime, "build_entity_snapshots", lambda hass, entry: list(entities.values()))
    monkeypatch.setattr(proactive_runtime.dt_util, "utcnow", lambda: Clock.now)
    monkeypatch.setattr(proactive_runtime.dt_util, "now", lambda: Clock.now.astimezone(timezone(timedelta(hours=2))))
    runtime = proactive_runtime.ProactiveRuntime(hass, entry, data, str(tmp_path / "proactive.json"))
    return runtime, hass, calls, entities, data


def test_build_config_is_conservative_by_default():
    config = proactive_runtime.build_config({})
    assert config.enabled is False
    assert config.standing_permissions_enabled is False
    assert config.entry_open_minutes == 15
    assert config.router.voice_enabled and config.router.push_enabled
    assert config.quiet.default_window is not None
    assert proactive_runtime.build_config({"proactive_entry_open_minutes": True}).entry_open_minutes == 15
    assert proactive_runtime.build_config({"proactive_entry_open_minutes": 9999}).entry_open_minutes == 240
    assert proactive_runtime.build_config({"quiet_hours_enabled": False}).quiet.default_window is None
    speakers = proactive_runtime.build_config({"agent_tts_entity": "tts.x", "agent_media_players": ["media_player.y"]})
    assert speakers.router.house_speakers_configured


def test_ports_use_existing_authorities(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [
        SimpleNamespace(entity_id="assist_satellite.wz", area_id="living_room", device_id="dev_wz"),
        SimpleNamespace(entity_id="assist_satellite.flur", area_id=None, device_id="dev_area"),
        SimpleNamespace(entity_id="light.not_a_satellite", area_id="living_room", device_id="x"),
    ])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch)
        assert runtime.enabled
        assert runtime.nobody_home() is False
        from homeintent.proactive_model import ProactiveSituation, SituationState, PrivacyLevel
        situation = ProactiveSituation(
            "s", SituationKind.ENTRY_LEFT_OPEN, ("cover.garage",), None, NOW, NOW,
            SituationState.ACTIVE, (), (), (), PriorityLevel.IMPORTANT, PrivacyLevel.HOUSEHOLD, "k",
        )
        recipients = runtime.recipients_for(situation)
        assert [item.user_id for item in recipients] == ["philipp"]  # only who is home
        assert recipients[0].push_target_ids == ("notify.mobile_app_philipp",)
        hass.states._states["person.philipp"] = State("person.philipp", "not_home")
        everyone = runtime.recipients_for(situation)
        assert {item.user_id for item in everyone} == {"philipp", "anna"}
        anna_ctx = next(item for item in everyone if item.user_id == "anna")
        assert anna_ctx.push_ambiguous and anna_ctx.push_target_ids == ()
        owned = runtime.recipients_for(replace(situation, owner_user_id="anna"))
        assert [item.user_id for item in owned] == ["anna"]
        assert runtime.nobody_home() is True
        hass.states._states["person.philipp"] = State("person.philipp", "home")
        room = runtime.room_for("person.philipp")
        assert room.area_id == "living_room" and room.evidence_class.value == "exact"
        assert runtime.room_for(None) is None
        assert runtime.others_home("person.philipp") is False
        satellites = runtime.satellites()
        assert satellites.for_area("living_room").reason == "multiple_satellites_in_area"
        assert satellites.for_area("office").satellite.entity_id == "assist_satellite.office"
        assert runtime.owner_known("philipp") and not runtime.owner_known("mallory")
        assert runtime.person_for("anna") == "person.anna" and runtime.person_for(None) is None
        assert await runtime.async_is_admin("philipp") and not await runtime.async_is_admin("anna")
        assert not await runtime.async_is_admin(None)
        assert runtime.active_goal_subjects() == frozenset()
        assert runtime.engine.config.quiet.is_quiet("anna", datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc))
        assert runtime.fresh_entities()["cover.garage"].state == "closed"
        assert runtime.local_now().utcoffset() == timedelta(hours=2)

    asyncio.run(scenario())


def test_voice_and_push_delivery_payloads(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [
        SimpleNamespace(entity_id="assist_satellite.wz", area_id="living_room", device_id="dev_wz"),
        SimpleNamespace(entity_id="notify.mobile_app_philipp", area_id=None, device_id="phone_p"),
    ])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch)
        voice = OutgoingMessage(
            CommunicationDecision(CommunicationChannel.VOICE, (CommunicationChannel.VOICE,), "philipp",
                                  "assist_satellite.wz", (), True, True, ()),
            "HomeIntent", "Die Garage ist noch offen. Soll ich sie schließen?",
            PriorityLevel.IMPORTANT, "p" + "a" * 32, "Schließen",
        )
        receipt = await runtime.async_deliver(voice)
        assert receipt.delivered == (CommunicationChannel.VOICE,)
        assert receipt.origin_device_id == "dev_wz"
        assert calls[-1] == ("assist_satellite", "start_conversation", {
            "entity_id": "assist_satellite.wz",
            "start_message": "Die Garage ist noch offen. Soll ich sie schließen?",
        })
        info = replace(voice, proposal_id=None, text="Die Waschmaschine ist fertig.")
        await runtime.async_deliver(info)
        assert calls[-1][:2] == ("assist_satellite", "announce")
        push = OutgoingMessage(
            CommunicationDecision(CommunicationChannel.INTERACTIVE_PUSH, (CommunicationChannel.INTERACTIVE_PUSH,),
                                  "philipp", None, ("notify.mobile_app_philipp",), True, False, ()),
            "HomeIntent", "Die Garage ist noch offen. Soll ich sie schließen?",
            PriorityLevel.IMPORTANT, "p" + "b" * 32, "Schließen",
        )
        receipt = await runtime.async_deliver(push)
        assert receipt.delivered == (CommunicationChannel.INTERACTIVE_PUSH,)
        domain, service, payload = calls[-1]
        # Buttons travel only through the bound legacy mobile_app service.
        assert (domain, service) == ("notify", "mobile_app_philipp")
        assert "entity_id" not in payload
        actions = payload["data"]["actions"]
        assert [item["title"] for item in actions] == ["Schließen", "Später", "Ignorieren"]
        for item in actions:
            assert re.fullmatch(r"HOMEINTENT_V12_(ACCEPT|LATER|IGNORE)_p[b]{32}_t[0-9a-f]{16}", item["action"])
            assert "cover" not in item["action"] and "service" not in item
        assert receipt.push_bindings[0].device_id == "phone_p"
        assert receipt.push_bindings[0].user_id == "philipp"
        assert "entity_id" not in payload["data"]
        # Provider failure is reported, not raised.
        hass.services.async_call = ServiceMock(side_effect=RuntimeError("down"))
        failed = await runtime.async_deliver(push)
        assert failed.delivered == () and failed.errors == ("push:RuntimeError",)
        house = OutgoingMessage(
            CommunicationDecision(CommunicationChannel.VOICE, (CommunicationChannel.VOICE,), "philipp",
                                  None, (), False, False, ()),
            "HomeIntent", "Achtung", PriorityLevel.CRITICAL, None, None,
        )
        house_result = await runtime.async_deliver(house)
        assert house_result.errors == ("voice:ValueError",)  # no TTS configured -> no broadcast

    asyncio.run(scenario())


def test_entity_push_target_gets_a_schema_valid_plain_push(tmp_path, monkeypatch):
    """A notify *entity* cannot carry buttons: the proposal is still pushed
    as plain text (answerable by voice/dashboard), no action token is
    minted, and the payload passes HA's notify.send_message schema."""
    _registry(monkeypatch, [
        SimpleNamespace(entity_id="notify.mobile_app_philipp", area_id=None, device_id="phone_p"),
    ])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(
            tmp_path, monkeypatch, philipp_kind=NotificationTargetKind.ENTITY
        )
        push = OutgoingMessage(
            CommunicationDecision(CommunicationChannel.INTERACTIVE_PUSH, (CommunicationChannel.INTERACTIVE_PUSH,),
                                  "philipp", None, ("notify.mobile_app_philipp",), True, False, ()),
            "HomeIntent", "Die Garage ist noch offen. Soll ich sie schließen?",
            PriorityLevel.IMPORTANT, "p" + "c" * 32, "Schließen",
        )
        receipt = await runtime.async_deliver(push)
        assert receipt.delivered == (CommunicationChannel.PUSH,)
        assert receipt.push_bindings == ()
        assert calls[-1] == ("notify", "send_message", {
            "entity_id": ["notify.mobile_app_philipp"],
            "title": "HomeIntent",
            "message": "Die Garage ist noch offen. Soll ich sie schließen?",
        })

    asyncio.run(scenario())


def test_scheduler_is_bounded_and_replaces_per_key(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [])

    async def scenario():
        runtime, *_ = await _runtime(tmp_path, monkeypatch)
        fired: list[str] = []

        async def callback():
            fired.append("x")

        runtime.schedule("k", NOW, callback)
        runtime.schedule("k", NOW + timedelta(minutes=1), callback)
        assert scheduled[0].cancelled and not scheduled[1].cancelled
        runtime._hass.async_create_task = lambda coro, name=None: asyncio.ensure_future(coro)
        scheduled[1].action(NOW)
        await asyncio.sleep(0)
        assert fired == ["x"]
        for index in range(proactive_runtime.MAX_SCHEDULED + 5):
            runtime.schedule(f"key{index}", NOW, callback)
        assert len(runtime._timers) == proactive_runtime.MAX_SCHEDULED
        stop = await runtime.async_start()
        stop()
        assert runtime._timers == {}

    asyncio.run(scenario())


def test_hooks_are_inert_when_disabled(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(
            tmp_path, monkeypatch, options={"proactive_context_enabled": False})
        assert not runtime.enabled
        garage = replace(entities["cover.garage"], state="open")
        await runtime.async_observe_state(garage, "closed", tuple(entities.values()))
        await runtime.async_report_effect_anomaly("light.kitchen", "LIGHT_TURN_ON", "Küchenlicht")
        await runtime.async_report_thermal_risk(area_id="living_room", area_name="Wohnzimmer",
                                               goal_id="g", current=18.5, target=21.5, owner_user_id="philipp")
        await runtime.async_report_goal_failure(run_id="r", goal_label="x", owner_user_id="philipp")
        runtime.record_timer_finished("Nudeln")
        runtime.record_authenticated_turn("philipp", "dev")
        assert await runtime.async_handle_push_action("HOMEINTENT_V12_ACCEPT_p" + "0" * 32, user_id="philipp", device_id=None)
        assert not await runtime.async_handle_push_action("HOMEINTENT_EXECUTE_x", user_id="philipp", device_id=None)
        assert await runtime.async_handle_reply("Ja", user_id="philipp", device_id=None, is_admin=True, other_open_questions=0) is None
        assert len(runtime.engine.situations) == 0
        assert len(runtime.engine.history) == 0
        assert calls == []

    asyncio.run(scenario())


def test_enabled_hooks_feed_the_engine(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [SimpleNamespace(entity_id="assist_satellite.wz", area_id="living_room", device_id="dev_wz")])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch)
        stop = await runtime.async_start()
        garage = replace(entities["cover.garage"], state="open", last_changed=NOW)
        entities["cover.garage"] = garage
        await runtime.async_observe_state(garage, "closed", tuple(entities.values()))
        assert runtime.engine.situations.get("entry_left_open:cover.garage") is not None
        assert scheduled and scheduled[-1].at == NOW + timedelta(minutes=10)
        await runtime.async_report_effect_anomaly("light.kitchen", "LIGHT_TURN_ON", "Küchenlicht")
        assert runtime.engine.situations.get("device_effect_anomaly:light.kitchen") is not None
        await runtime.async_report_thermal_risk(area_id="living_room", area_name="Wohnzimmer",
                                               goal_id="g1", current=18.5, target=21.5, owner_user_id="philipp")
        thermal = runtime.engine.situations.get("thermal_goal_at_risk:g1")
        assert thermal.evidence_value("target_celsius") == "21.5"
        await runtime.async_report_goal_failure(run_id="r1", goal_label="Heizplan", owner_user_id="philipp")
        assert runtime.engine.situations.get("pending_goal_requires_attention:r1").owner_user_id == "philipp"
        await runtime.async_report_goal_failure(run_id="r2", goal_label="x", owner_user_id=None)
        assert runtime.engine.situations.get("pending_goal_requires_attention:r2") is None
        runtime.record_timer_finished("Nudeln")
        assert runtime.engine.history.records()[-1].situation_kind is SituationKind.TIMER_FINISHED
        runtime.record_authenticated_turn("philipp", "dev_wz")
        runtime.record_authenticated_turn("philipp", "unknown_device")
        runtime.record_authenticated_turn(None, "dev_wz")
        # Philipp is alone at home and exactly located: household and personal
        # notices may be spoken on the unique living-room satellite.  No device
        # service was called by any hook.
        spoken = [call for call in calls if call[0] == "assist_satellite"]
        assert {call[2]["entity_id"] for call in spoken} == {"assist_satellite.wz"}
        assert all(call[0] in {"notify", "assist_satellite"} for call in calls)
        stop()

    asyncio.run(scenario())


def test_push_action_feedback_and_habit_refresh(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [
        SimpleNamespace(entity_id="notify.mobile_app_philipp", area_id=None, device_id="phone_p"),
    ])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch)
        hass.states._states["person.philipp"] = State("person.philipp", "not_home")
        garage = replace(entities["cover.garage"], state="open", last_changed=NOW - timedelta(minutes=30))
        entities["cover.garage"] = garage
        await runtime.async_observe_state(garage, "closed", tuple(entities.values()))
        pushes = [call for call in calls if call[0] == "notify"]
        action = pushes[-1][2]["data"]["actions"][1]["action"]  # LATER
        assert action.startswith("HOMEINTENT_V12_LATER_")
        handled = await runtime.async_handle_push_action(action, user_id="philipp", device_id="phone_p")
        assert handled
        assert calls[-1][2]["message"].startswith("In Ordnung. Ich prüfe das in 30 Minuten")
        proposal = runtime.engine.proposals.all()[-1]
        assert proposal.state is ProposalState.SNOOZED
        await runtime.async_refresh_habits()
        assert runtime._habit_candidates == ()

    asyncio.run(scenario())


def test_restore_failure_never_blocks_setup(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [])
    (tmp_path / "proactive.json").write_text('{"schema_version": 1, "situations": [1, 2], "proposals": {"x": 1}}')

    async def scenario():
        runtime, *_ = await _runtime(tmp_path, monkeypatch)
        stop = await runtime.async_start()
        assert len(runtime.engine.situations) == 0
        stop()

    asyncio.run(scenario())


def test_habit_refresh_reads_v11_models_and_triggers_suggestions(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [SimpleNamespace(entity_id="assist_satellite.wz", area_id="living_room", device_id="dev_wz")])
    from homeintent.learning_policy import KnowledgeState, LearningMode, LearningPolicy
    from homeintent.model_registry import LearnedKind, LearnedModel, ModelHealth, ModelRegistry

    async def scenario():
        states = garage_states() + [entity("cover.kitchen", "Küchenrollladen", "closed", area="kitchen")]
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch, states=states)
        policy = LearningPolicy(learning_mode=LearningMode.ASK, habit_discovery_enabled=True,
                                predictive_models_enabled=True)
        registry = ModelRegistry(tmp_path / "models.json", policy)
        await registry.async_upsert(LearnedModel(
            "habit:h1", LearnedKind.HABIT, "philipp",
            {"time_band": "day", "weekday": 3, "sequence_family": "f"},
            {"sequence": "LIGHT_TURN_ON@light.kitchen=on|COVER_OPEN_COVER@cover.kitchen=open",
             "support": 0.8, "suggestion_status": "new", "creates_automation": False},
            KnowledgeState.INFERRED, 0.8, 12, NOW - timedelta(days=20), NOW, ("goal_run:x",),
            health=ModelHealth.VALID,
        ))
        await registry.async_upsert(LearnedModel(
            "habit:junk", LearnedKind.HABIT, "philipp", {"time_band": "day"},
            {"sequence": "LOCK_UNLOCK@lock.front=unlocked"}, KnowledgeState.INFERRED, 0.8, 12,
            NOW - timedelta(days=2), NOW, ("goal_run:y",),
        ))
        data.learned_models = registry
        data.learning_policy = policy
        await runtime.async_refresh_habits()
        assert [item.model_id for item in runtime._habit_candidates] == ["habit:h1"]
        assert runtime._habit_triggers == frozenset({"light.kitchen"})
        Clock.now = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)  # Thursday 13:00 local
        entities["sensor.philipp_area"] = replace(
            entities["sensor.philipp_area"], last_changed=Clock.now, last_updated=Clock.now,
        )
        try:
            kitchen = replace(entities["light.kitchen"], state="on")
            entities["light.kitchen"] = kitchen
            await runtime.async_observe_state(kitchen, "off", tuple(entities.values()))
        finally:
            Clock.now = NOW
        spoken = [call for call in calls if call[0] == "assist_satellite"]
        assert spoken and spoken[-1][2]["start_message"].endswith("Soll ich die Routine starten?")
        assert [call for call in calls if call[0] in {"cover", "light", "homeassistant"}] == []

    asyncio.run(scenario())


def test_v10_adapters_use_the_policy_executor_and_bounded_verification(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch)
        from homeintent.service_call import ServiceCallPlan
        fresh = await runtime._async_refresh()
        denied = await runtime._async_execute_service(
            ServiceCallPlan("cover", "close_cover", "cover.garage", {}), fresh, False, "anna", False,
        )
        assert not denied.executed and calls == []
        allowed = await runtime._async_execute_service(
            ServiceCallPlan("homeassistant", "turn_off", "light.kitchen", {}),
            [replace(item, state="on") if item.entity_id == "light.kitchen" else item for item in fresh],
            False, "philipp", True,
        )
        assert allowed.executed and calls[-1][:2] == ("homeassistant", "turn_off")
        data.effect_monitor.timeout = timedelta(milliseconds=20)
        assert await runtime._async_verify("light.kitchen", "off") is True
        assert await runtime._async_verify("light.kitchen", "on") is False
        await data.effect_monitor.async_close()

    asyncio.run(scenario())


def test_critical_fallback_creates_a_household_notification(tmp_path, monkeypatch, scheduled):
    _registry(monkeypatch, [])

    async def scenario():
        runtime, hass, calls, entities, data = await _runtime(tmp_path, monkeypatch)
        receipt = await runtime.async_deliver(OutgoingMessage(
            CommunicationDecision(CommunicationChannel.PUSH, (CommunicationChannel.PUSH,), None, None,
                                  (), False, False, ("critical_household_fallback",)),
            "HomeIntent: Achtung", "Achtung: Rauch!", PriorityLevel.CRITICAL, None, None,
        ))
        assert receipt.delivered == (CommunicationChannel.PUSH,)
        assert calls[-1][:2] == ("persistent_notification", "create")

    asyncio.run(scenario())
