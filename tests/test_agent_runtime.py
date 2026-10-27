from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub

_ha_stub.install()

from _notify_sink import SEND_MESSAGE_SCHEMA  # noqa: E402
from homeintent.agent_event import AgentEventState, AgentMode, StoredServicePlan  # noqa: E402
from homeintent.agent_runtime import ProactiveAgentRuntime  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_DELIVERY_CHANNELS,
    CONF_AGENT_ENABLED,
    CONF_AGENT_MEDIA_PLAYERS,
    CONF_AGENT_NOTIFY_TARGETS,
    CONF_AGENT_TTS_ENTITY,
    CONF_ADMIN_ONLY_ENTITIES,
    CONF_CONTROL_USER_IDS,
)
from homeintent.runtime_data import HomeIntentRuntimeData  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_context import TECHNICAL_FAILURE_TEXT  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, State  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402


def _runtime(tmp_path: Path, options: dict | None = None):
    hass = HomeAssistant()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    entry = ConfigEntry(options=options or {})
    runtime = ProactiveAgentRuntime(hass, entry, HomeIntentRuntimeData())
    return hass, runtime


def test_disabled_agent_is_a_strict_no_op(tmp_path):
    hass, runtime = _runtime(tmp_path, {CONF_AGENT_ENABLED: False})
    result = asyncio.run(runtime.async_signal({"rule_id": "r", "message": "m"}))
    assert result is None
    hass.services.async_call.assert_not_awaited()


def test_signal_delivers_push_and_deduplicates(tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )

    async def scenario():
        first = await runtime.async_signal(
            {"rule_id": "temperature-rule", "message": "Grenzwert erreicht"}
        )
        second = await runtime.async_signal(
            {"rule_id": "temperature-rule", "message": "Grenzwert erreicht"}
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first is not None and first.state is AgentEventState.NOTIFIED
    assert second is not None and second.event_id == first.event_id
    hass.services.async_call.assert_awaited_once()
    call = hass.services.async_call.await_args
    assert call.args[:2] == ("notify", "send_message")
    assert call.args[2]["entity_id"] == ["notify.mobile_app_phone"]


def test_multi_channel_delivery_uses_tts_and_push(tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push", "tts"],
            CONF_AGENT_TTS_ENTITY: "tts.piper",
            CONF_AGENT_MEDIA_PLAYERS: ["media_player.kueche"],
        },
    )
    event = asyncio.run(
        runtime.async_signal({"rule_id": "door", "message": "Die Tür ist offen"})
    )
    assert event is not None
    assert event.delivered_channels == ("push", "tts")
    assert hass.services.async_call.await_count == 2
    assert hass.services.async_call.await_args_list[1].args[:2] == ("tts", "speak")


def test_notification_ignore_acknowledges_event(tmp_path):
    _hass, runtime = _runtime(tmp_path)

    async def scenario():
        created = await runtime.async_signal({"rule_id": "door", "message": "Offen"})
        assert created is not None
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_IGNORE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.ACKNOWLEDGED


def test_stale_source_is_resolved_without_delivery(tmp_path):
    hass, runtime = _runtime(tmp_path)
    hass.states._states["binary_sensor.door"] = State("binary_sensor.door", "off")
    event = asyncio.run(runtime.async_signal({
        "rule_id": "door",
        "message": "Offen",
        "source_entity_id": "binary_sensor.door",
        "expected_source_state": "on",
    }))
    assert event is not None and event.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_not_awaited()


def test_numeric_source_is_rechecked_before_delivery(tmp_path):
    hass, runtime = _runtime(tmp_path)
    hass.states._states["sensor.temperature"] = State("sensor.temperature", "25")
    event = asyncio.run(runtime.async_signal({
        "rule_id": "heat",
        "message": "Zu warm",
        "source_entity_id": "sensor.temperature",
        "source_comparator": "above",
        "source_threshold": 28,
    }))
    assert event is not None and event.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_not_awaited()


def test_ask_notification_action_revalidates_and_executes(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    entities = [
        EntitySnapshot(
            "switch.pump",
            "Pumpe",
            "switch",
            "on",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        )
    ]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Soll ich die Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_off", {"entity_id": "switch.pump"}, blocking=True
    )


def test_critical_ask_requires_authenticated_admin_by_default(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    entities = [EntitySnapshot("lock.front", "Haustür", "lock", "locked")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "lock-rule",
            "message": "Soll ich aufschließen?",
            "mode": "ask",
            "action_domain": "lock",
            "action_service": "unlock",
            "action_entity_id": "lock.front",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert "authentifizierten Benutzer" in (stored.last_error or "")
    assert all(
        call.args[:2] != ("lock", "unlock")
        for call in hass.services.async_call.await_args_list
    )
    # The denial is visible over the configured delivery channel.
    assert hass.services.async_call.await_count == 1
    assert "nicht ausgeführt" in hass.services.async_call.await_args.args[2]["title"]


def test_authenticated_admin_can_confirm_critical_ask(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path)
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
    )
    entities = [EntitySnapshot("lock.front", "Haustür", "lock", "locked")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "lock-rule",
            "message": "Soll ich aufschließen?",
            "mode": "ask",
            "action_domain": "lock",
            "action_service": "unlock",
            "action_entity_id": "lock.front",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(
                data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"},
                context=SimpleNamespace(user_id="owner"),
            )
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_awaited_once_with(
        "lock", "unlock", {"entity_id": "lock.front"}, blocking=True
    )
    assert runtime._runtime_data.audit_trail._entries[-1].actor != "voice"


def test_alarm_disarm_push_is_denied_without_authenticated_actor(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path)
    entities = [
        EntitySnapshot(
            "alarm_control_panel.home", "Alarmanlage", "alarm_control_panel", "armed_away"
        )
    ]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "alarm-rule",
            "message": "Alarmanlage deaktivieren?",
            "mode": "ask",
            "action_domain": "alarm_control_panel",
            "action_service": "alarm_disarm",
            "action_entity_id": "alarm_control_panel.home",
            "action_data": {"code": "1234"},
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert all(
        call.args[:2] != ("alarm_control_panel", "alarm_disarm")
        for call in hass.services.async_call.await_args_list
    )


def test_authenticated_non_admin_cannot_inherit_proactive_critical_authority(
    monkeypatch, tmp_path
):
    hass, runtime = _runtime(tmp_path, {"allow_non_admin_critical": True})
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
    )
    entities = [EntitySnapshot("lock.front", "Haustür", "lock", "locked")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "lock-rule",
            "message": "Aufschließen?",
            "mode": "ask",
            "action_domain": "lock",
            "action_service": "unlock",
            "action_entity_id": "lock.front",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(
                data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"},
                context=SimpleNamespace(user_id="resident"),
            )
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert all(
        call.args[:2] != ("lock", "unlock")
        for call in hass.services.async_call.await_args_list
    )


def test_push_actor_must_match_configured_control_user(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path, {CONF_CONTROL_USER_IDS: ["owner"]})
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(
                data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"},
                context=SimpleNamespace(user_id="owner"),
            )
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_off", {"entity_id": "switch.pump"}, blocking=True
    )


def test_missing_push_actor_is_visibly_denied_when_control_allowlist_exists(
    monkeypatch, tmp_path
):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_CONTROL_USER_IDS: ["owner"],
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert "Benutzer" in (stored.last_error or "")
    assert all(
        call.args[:2] != ("homeassistant", "turn_off")
        for call in hass.services.async_call.await_args_list
    )
    assert hass.services.async_call.await_count == 1


def test_foreign_push_actor_is_denied_by_control_allowlist(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_CONTROL_USER_IDS: ["owner"],
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(
                data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"},
                context=SimpleNamespace(user_id="guest"),
            )
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert all(
        call.args[:2] != ("homeassistant", "turn_off")
        for call in hass.services.async_call.await_args_list
    )


def test_notification_action_from_unconfigured_device_is_ignored(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    er.async_get(hass).entries["notify.mobile_app_phone"] = er.RegistryEntry(
        device_id="phone-device"
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        assert created.notification_device_ids == ("phone-device",)
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(
                data={
                    "action": f"HOMEINTENT_EXECUTE_{created.event_id}",
                    "device_id": "other-device",
                }
            )
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.NOTIFIED
    hass.services.async_call.assert_not_awaited()


def test_notification_action_without_bound_device_id_is_ignored(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    er.async_get(hass).entries["notify.mobile_app_phone"] = er.RegistryEntry(
        device_id="phone-device"
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(
                data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"}
            )
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.NOTIFIED
    hass.services.async_call.assert_not_awaited()


def test_stored_proactive_target_tampering_is_denied(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path)
    entities = [
        EntitySnapshot("switch.pump", "Pumpe", "switch", "on"),
        EntitySnapshot("switch.other", "Andere", "switch", "on"),
    ]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        await runtime._store.async_save(
            replace(
                created,
                proposed_action=StoredServicePlan(
                    "homeassistant", "turn_off", ("switch.other",), {}
                ),
            )
        )
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert "verändert" in (stored.last_error or "")
    assert all(
        item.args[:2] != ("homeassistant", "turn_off")
        for item in hass.services.async_call.await_args_list
    )


def test_stored_proactive_message_tampering_breaks_action_binding(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path)
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Das Licht ist an.",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        await runtime._store.async_save(replace(created, message="Harmloser anderer Text"))
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.DENIED
    assert all(
        call.args[:2] != ("homeassistant", "turn_off")
        for call in hass.services.async_call.await_args_list
    )


def test_expired_event_and_replay_never_execute_twice(monkeypatch, tmp_path):
    from homeassistant.util import dt as dt_util

    hass, runtime = _runtime(tmp_path)
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        expired = await runtime.async_signal({
            "rule_id": "expired", "message": "Pumpe?", "mode": "ask",
            "action_domain": "homeassistant", "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert expired is not None
        await runtime._store.async_save(
            replace(expired, expires_at="2000-01-01T00:00:00+00:00")
        )
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{expired.event_id}"})
        )
        expired_state = (await runtime._store.async_load_all())[expired.event_id]

        fresh = await runtime.async_signal({
            "rule_id": "fresh", "message": "Pumpe?", "mode": "ask",
            "action_domain": "homeassistant", "action_service": "turn_off",
            "action_entity_id": "switch.pump", "dedupe_key": dt_util.utcnow().isoformat(),
        })
        assert fresh is not None
        hass.services.async_call.reset_mock()
        click = SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{fresh.event_id}"})
        await runtime.async_handle_notification_action(click)
        await runtime.async_handle_notification_action(click)
        return expired_state

    expired_state = asyncio.run(scenario())
    assert expired_state.state is AgentEventState.EXPIRED
    assert hass.services.async_call.await_count == 1


def test_policy_and_exposure_are_rechecked_immediately_before_click(
    monkeypatch, tmp_path
):
    hass, runtime = _runtime(tmp_path)
    current = [[EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]]
    monkeypatch.setattr(
        "homeintent.agent_runtime.build_entity_snapshots", lambda *_: current[0]
    )

    async def scenario():
        removed = await runtime.async_signal({
            "rule_id": "removed", "message": "Pumpe?", "mode": "ask",
            "action_domain": "homeassistant", "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert removed is not None
        current[0] = []
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{removed.event_id}"})
        )
        removed_state = (await runtime._store.async_load_all())[removed.event_id]

        current[0] = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
        tightened = await runtime.async_signal({
            "rule_id": "tightened", "message": "Pumpe?", "mode": "ask",
            "action_domain": "homeassistant", "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert tightened is not None
        runtime._entry.options[CONF_ADMIN_ONLY_ENTITIES] = ["switch.pump"]
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{tightened.event_id}"})
        )
        tightened_state = (await runtime._store.async_load_all())[tightened.event_id]
        return removed_state, tightened_state

    removed, tightened = asyncio.run(scenario())
    assert removed.state in {AgentEventState.DENIED, AgentEventState.FAILED}
    assert tightened.state is AgentEventState.DENIED
    assert all(
        call.args[:2] != ("homeassistant", "turn_off")
        for call in hass.services.async_call.await_args_list
    )


def test_execute_event_cannot_promote_an_inform_event(tmp_path):
    hass, runtime = _runtime(tmp_path)

    async def scenario():
        created = await runtime.async_signal({"rule_id": "info", "message": "Nur Info"})
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.NOTIFIED
    hass.services.async_call.assert_not_awaited()


def test_parallel_execute_clicks_call_the_physical_service_once(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path)
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        action = SimpleNamespace(
            data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"}
        )
        await asyncio.gather(
            runtime.async_handle_notification_action(action),
            runtime.async_handle_notification_action(action),
        )

    asyncio.run(scenario())
    calls = [
        call for call in hass.services.async_call.await_args_list
        if call.args[:2] == ("homeassistant", "turn_off")
    ]
    assert len(calls) == 1


def test_service_failure_is_recorded_as_failed_and_reported(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "pump-rule",
            "message": "Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        hass.services.async_call.side_effect = [RuntimeError("service unavailable"), None]
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.FAILED
    assert stored.last_error == TECHNICAL_FAILURE_TEXT
    assert "service unavailable" not in stored.last_error
    assert hass.services.async_call.await_count == 2


def test_source_change_between_notification_and_tap_prevents_execution(
    monkeypatch, tmp_path
):
    hass, runtime = _runtime(tmp_path)
    hass.states._states["binary_sensor.door"] = State("binary_sensor.door", "on")
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "door-rule",
            "message": "Tür offen; Pumpe ausschalten?",
            "mode": "ask",
            "source_entity_id": "binary_sensor.door",
            "expected_source_state": "on",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.states._states["binary_sensor.door"] = State("binary_sensor.door", "off")
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_EXECUTE_{created.event_id}"})
        )
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_not_awaited()


def test_recovery_quarantines_persisted_plan_outside_allowlist(tmp_path):
    _hass, runtime = _runtime(tmp_path)

    async def scenario():
        created = await runtime.async_signal({"rule_id": "info", "message": "Info"})
        assert created is not None
        corrupted = replace(
            created,
            mode=AgentMode.ASK,
            proposed_action=StoredServicePlan(
                "shell_command", "arbitrary", ("switch.pump",), {}
            ),
        )
        await runtime._store.async_save(corrupted)
        await runtime.async_recover()
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.EXPIRED
    assert "nicht freigegeben" in (stored.last_error or "")


def test_auto_never_executes_medium_risk_cover_action(monkeypatch, tmp_path):
    hass, runtime = _runtime(tmp_path)
    entities = [EntitySnapshot("cover.blind", "Rollo", "cover", "open")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    event = asyncio.run(runtime.async_signal({
        "rule_id": "blind-rule",
        "message": "Das Rollo ist noch offen.",
        "mode": "auto",
        "action_domain": "cover",
        "action_service": "close_cover",
        "action_entity_id": "cover.blind",
    }))

    assert event is not None
    assert event.mode.value == "ask"
    assert "Tatsächliche Aktion:" in event.message
    assert "Das Rollo ist noch offen." not in event.message
    assert all(
        call.args[:2] != ("cover", "close_cover")
        for call in hass.services.async_call.await_args_list
    )


def test_auto_uses_the_same_non_admin_policy_for_gate_and_executor(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path, {CONF_ADMIN_ONLY_ENTITIES: ["switch.server"]}
    )
    entities = [EntitySnapshot("switch.server", "Server", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    event = asyncio.run(runtime.async_signal({
        "rule_id": "server-rule",
        "message": "Server ausschalten",
        "mode": "auto",
        "action_domain": "homeassistant",
        "action_service": "turn_off",
        "action_entity_id": "switch.server",
    }))

    assert event is not None and event.mode is AgentMode.ASK
    assert all(
        call.args[:2] != ("homeassistant", "turn_off")
        for call in hass.services.async_call.await_args_list
    )


def test_agent_rejects_target_override_before_ask_or_auto_event_is_created(tmp_path):
    _hass, runtime = _runtime(tmp_path)

    with pytest.raises(ValueError, match="validierte Ziel"):
        asyncio.run(runtime.async_signal({
            "rule_id": "override-rule",
            "message": "Manipuliert",
            "mode": "auto",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.allowed",
            "action_data": {"entity_id": "switch.hidden"},
        }))


def test_low_risk_auto_executes_then_pushes_without_stale_buttons(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_NOTIFY_TARGETS: ["notify.mobile_app_phone"],
            CONF_AGENT_DELIVERY_CHANNELS: ["push"],
        },
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    event = asyncio.run(runtime.async_signal({
        "rule_id": "auto-pump",
        "message": "Ich habe die Pumpe ausgeschaltet.",
        "mode": "auto",
        "action_domain": "homeassistant",
        "action_service": "turn_off",
        "action_entity_id": "switch.pump",
    }))

    assert event is not None and event.state is AgentEventState.RESOLVED
    assert hass.services.async_call.await_args_list[0].args[:2] == (
        "homeassistant", "turn_off"
    )
    push = hass.services.async_call.await_args_list[1].args
    # A notify entity takes message/title only (no stale buttons, no data).
    assert push[:2] == ("notify", "send_message")
    assert SEND_MESSAGE_SCHEMA(push[2]) == {
        "entity_id": ["notify.mobile_app_phone"],
        "title": push[2]["title"],
        "message": "Ich habe die Pumpe ausgeschaltet.",
    }
    assert "data" not in push[2]


def test_unique_tts_question_accepts_voice_confirmation(monkeypatch, tmp_path):
    hass, runtime = _runtime(
        tmp_path,
        {
            CONF_AGENT_DELIVERY_CHANNELS: ["tts"],
            CONF_AGENT_TTS_ENTITY: "tts.piper",
            CONF_AGENT_MEDIA_PLAYERS: ["media_player.kueche"],
        },
    )
    entities = [EntitySnapshot("switch.pump", "Pumpe", "switch", "on")]
    monkeypatch.setattr("homeintent.agent_runtime.build_entity_snapshots", lambda *_: entities)

    async def scenario():
        created = await runtime.async_signal({
            "rule_id": "voice-pump",
            "message": "Soll ich die Pumpe ausschalten?",
            "mode": "ask",
            "action_domain": "homeassistant",
            "action_service": "turn_off",
            "action_entity_id": "switch.pump",
        })
        assert created is not None
        hass.services.async_call.reset_mock()
        reply = await runtime.async_handle_voice_reply(
            "Ja", user_id=None, is_admin=False
        )
        return reply, (await runtime._store.async_load_all())[created.event_id]

    reply, stored = asyncio.run(scenario())
    assert reply == "Erledigt."
    assert stored.state is AgentEventState.RESOLVED
    hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_off", {"entity_id": "switch.pump"}, blocking=True
    )


def test_snooze_uses_durable_helper_and_rechecks_original_conditions(tmp_path):
    hass, runtime = _runtime(tmp_path)

    async def scenario():
        created = await runtime.async_signal({"rule_id": "door-rule", "message": "Offen"})
        assert created is not None
        hass.services.async_call.reset_mock()
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_SNOOZE_{created.event_id}"})
        )
        snoozed = (await runtime._store.async_load_all())[created.event_id]
        hass.services.async_call.reset_mock()
        await runtime.async_recheck(created.event_id)
        rechecked = (await runtime._store.async_load_all())[created.event_id]
        return snoozed, rechecked

    snoozed, rechecked = asyncio.run(scenario())
    assert snoozed.state is AgentEventState.SNOOZED
    assert snoozed.reminder_automation_id is not None
    assert rechecked.state is AgentEventState.EXPIRED
    hass.services.async_call.assert_awaited_once_with(
        "automation",
        "trigger",
        {"entity_id": "automation.door-rule", "skip_condition": False},
        blocking=True,
    )


def test_recheck_provider_details_are_not_persisted_or_notified(tmp_path, caplog):
    secret = "/config/secrets.yaml token=super-secret api_key=123456"
    hass, runtime = _runtime(tmp_path)

    async def scenario():
        created = await runtime.async_signal({"rule_id": "door-rule", "message": "Offen"})
        assert created is not None
        await runtime.async_handle_notification_action(
            SimpleNamespace(data={"action": f"HOMEINTENT_SNOOZE_{created.event_id}"})
        )
        hass.services.async_call.reset_mock()
        hass.services.async_call.side_effect = RuntimeError(secret)
        await runtime.async_recheck(created.event_id)
        return (await runtime._store.async_load_all())[created.event_id]

    stored = asyncio.run(scenario())
    assert stored.state is AgentEventState.SNOOZED
    assert stored.last_error == TECHNICAL_FAILURE_TEXT
    assert secret not in repr(stored)
    assert secret not in caplog.text
