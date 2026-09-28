"""Wave 12 ("Einmalige Automation"): ``homeintent/__init__.py``'s
``homeintent.delete_automation`` custom service registration - the action step
a self-deleting generated automation calls on itself after it fires (see
``nlu/ha_automation_generator.py``'s ``generate_ha_automation_config()``
docstring).

Uses ``tests/_ha_stub.py``'s fake ``HomeAssistant``/``ConfigEntry`` the same
way ``tests/test_conversation_automation_toggle.py`` etc. already do -
registers the service through the real ``async_setup_entry()``, then
invokes the stored handler directly (mirroring how HA itself would dispatch
a real ``homeintent.delete_automation`` service call), asserting against the
real ``AutomationExecutor``/``automations.yaml`` round trip rather than a
second reimplementation of ``async_delete_automation()`` (already covered
in isolation by ``tests/test_automation_executor.py``).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml as pyyaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent as homeintent_init  # noqa: E402
from homeintent.const import DOMAIN  # noqa: E402
from homeintent.thermal_deadline import (  # noqa: E402
    ThermalCheckpointPhase,
    ThermalDeadlineCheckpoint,
)
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, ServiceCall, State  # noqa: E402


def _make_hass(tmp_path: Path) -> HomeAssistant:
    hass = HomeAssistant()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    hass.config_entries = type(
        "FakeConfigEntries", (), {
            "async_forward_entry_setups": AsyncMock(),
            "async_unload_platforms": AsyncMock(return_value=True),
            "async_reload": AsyncMock(),
        },
    )()
    return hass


def _automations_yaml(tmp_path: Path) -> list[dict]:
    path = tmp_path / "automations.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return pyyaml.safe_load(handle) or []


def _write_automations_yaml(tmp_path: Path, automations: list[dict]) -> None:
    with open(tmp_path / "automations.yaml", "w", encoding="utf-8") as handle:
        pyyaml.safe_dump(automations, handle)


async def _call_service_and_wait(hass, handler, call) -> None:
    """Dispatch like HA, then wait for HomeIntent's scheduled delete task."""
    await handler(call)
    if hass._tasks:
        await asyncio.gather(*hass._tasks)


def test_async_setup_entry_registers_the_delete_automation_service(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()

    asyncio.run(homeintent_init.async_setup_entry(hass, entry))

    assert hass.services.has_service(DOMAIN, "delete_automation") is True
    assert hass.services.has_service(DOMAIN, "record_automation_run") is True
    assert hass.services.has_service(DOMAIN, "proactive_message") is True
    assert hass.services.has_service(DOMAIN, "recheck_agent_event") is True
    assert hass.services.has_service(DOMAIN, "bind_user_context") is True
    assert hass.services.has_service(DOMAIN, "set_household") is True
    assert hass.services.has_service(DOMAIN, "save_routine") is True
    assert hass.services.has_service(DOMAIN, "save_comfort_profile") is True
    assert hass.services.has_service(DOMAIN, "delete_monitor_goal") is True


def test_async_setup_entry_uses_configured_context_ttl(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry(options={"context_ttl_seconds": 90})

    asyncio.run(homeintent_init.async_setup_entry(hass, entry))

    assert entry.runtime_data.context_store._ttl_seconds == 90.0


def test_bind_user_context_accepts_exact_notify_entity_and_service(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
    )
    hass.states._states["person.owner"] = State("person.owner", "home")
    hass.states._states["notify.owner"] = State("notify.owner", "unknown")
    hass.services.async_register("notify", "mobile_app_owner", AsyncMock())
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    handler = hass.services._handlers[(DOMAIN, "bind_user_context")]
    call = ServiceCall({
        "user_id": "owner",
        "person_entity_id": "person.owner",
        "notification_targets": ["notify.owner"],
        "notification_services": ["notify.mobile_app_owner"],
        "preferred_notification_service": "notify.mobile_app_owner",
        "confirmed": True,
    })
    call.context = SimpleNamespace(user_id="admin")

    asyncio.run(handler(call))

    binding = entry.runtime_data.user_contexts.resolve_notification_targets(
        "person.owner"
    )
    assert binding.status.value == "resolved"
    assert [target.target_id for target in binding.targets] == [
        "notify.mobile_app_owner"
    ]
    assert binding.targets[0].kind.value == "service"


def test_bind_user_context_rejects_unregistered_notify_service(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
    )
    hass.states._states["person.owner"] = State("person.owner", "home")
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    handler = hass.services._handlers[(DOMAIN, "bind_user_context")]
    call = ServiceCall({
        "user_id": "owner",
        "person_entity_id": "person.owner",
        "notification_services": ["notify.mobile_app_missing"],
        "confirmed": True,
    })
    call.context = SimpleNamespace(user_id="admin")

    with pytest.raises(ValueError, match="existing notify"):
        asyncio.run(handler(call))


def test_async_setup_entry_does_not_double_register_the_service(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()

    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    handler_after_first = hass.services._handlers[(DOMAIN, "delete_automation")]
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    handler_after_second = hass.services._handlers[(DOMAIN, "delete_automation")]

    assert handler_after_first is handler_after_second


def test_setup_schedules_expired_automation_reconciliation_when_reload_exists(
    monkeypatch, tmp_path
):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    hass.services.async_register("automation", "reload", AsyncMock())
    cleanup = AsyncMock()
    monkeypatch.setattr(
        homeintent_init, "_async_cleanup_expired_scheduled_automations", cleanup
    )

    async def scenario() -> None:
        await homeintent_init.async_setup_entry(hass, entry)
        await asyncio.gather(*hass._tasks)

    asyncio.run(scenario())

    cleanup.assert_awaited_once_with(hass)


def test_async_unload_entry_removes_the_service(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    assert hass.services.has_service(DOMAIN, "delete_automation") is True

    asyncio.run(homeintent_init.async_unload_entry(hass, entry))

    assert hass.services.has_service(DOMAIN, "delete_automation") is False
    assert hass.services.has_service(DOMAIN, "record_automation_run") is False
    assert hass.services.has_service(DOMAIN, "proactive_message") is False
    assert hass.services.has_service(DOMAIN, "recheck_agent_event") is False
    assert hass.services.has_service(DOMAIN, "bind_user_context") is False
    assert hass.services.has_service(DOMAIN, "set_household") is False
    assert hass.services.has_service(DOMAIN, "save_routine") is False
    assert hass.services.has_service(DOMAIN, "save_comfort_profile") is False
    assert hass.services.has_service(DOMAIN, "delete_monitor_goal") is False
    assert hass.services.has_service(DOMAIN, "reset_test_state") is False


def test_learning_tasks_are_cleaned_up_on_unload(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()

    async def scenario() -> None:
        await homeintent_init.async_setup_entry(hass, entry)
        await asyncio.gather(*hass._tasks)
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        entry.runtime_data.learning_tasks.add(task)
        assert entry.runtime_data.remove_learning_listener is not None
        assert entry.runtime_data.goal_runs._append_listeners
        await homeintent_init.async_unload_entry(hass, entry)
        assert task.cancelled()
        assert entry.runtime_data.learning_tasks == set()
        assert entry.runtime_data.remove_learning_listener is None
        assert entry.runtime_data.goal_runs._append_listeners == []

    asyncio.run(scenario())


def test_forged_thermal_checkpoint_changes_no_authoritative_state(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()

    async def scenario() -> None:
        await homeintent_init.async_setup_entry(hass, entry)
        if hass._tasks:
            await asyncio.gather(*hass._tasks)
        now = datetime.now(timezone.utc)
        checkpoint = ThermalDeadlineCheckpoint(
            ThermalCheckpointPhase.INTERMEDIATE, "goal", "living",
            "climate.living", "sensor.living", 21, "thermal:living",
            1800, 300, "checkpoint", "secret", "run",
            now.isoformat(), (now + timedelta(hours=1)).isoformat(),
        )
        assert await entry.runtime_data.thermal_checkpoints.async_register(
            checkpoint, scheduled_for=now, deadline=now + timedelta(hours=1)
        )
        before_runs = await entry.runtime_data.goal_runs.async_list()
        before_experiences = await entry.runtime_data.experiences.async_list()
        hass.services.async_call.reset_mock()
        handler = hass.services._handlers[(DOMAIN, "thermal_deadline_checkpoint")]
        await handler(ServiceCall(replace(checkpoint, token="forged").to_service_data()))
        assert await entry.runtime_data.goal_runs.async_list() == before_runs
        assert await entry.runtime_data.experiences.async_list() == before_experiences
        assert hass.services.async_call.await_count == 0

    asyncio.run(scenario())


def test_calling_the_service_deletes_the_matching_automation(monkeypatch, tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    _write_automations_yaml(
        tmp_path,
        [
            {"id": "keep-me", "alias": "Bleibt", "triggers": [], "actions": []},
            {"id": "delete-me", "alias": "Wird gelöscht", "triggers": [], "actions": []},
        ],
    )

    handler = hass.services._handlers[(DOMAIN, "delete_automation")]
    monkeypatch.setattr(homeintent_init, "SELF_DELETE_GRACE_SECONDS", 0)
    asyncio.run(_call_service_and_wait(hass, handler, ServiceCall({"automation_id": "delete-me"})))

    remaining = _automations_yaml(tmp_path)
    assert [a["id"] for a in remaining] == ["keep-me"]
    hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)


def test_calling_the_service_for_an_already_gone_automation_id_does_not_raise(monkeypatch, tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    _write_automations_yaml(tmp_path, [{"id": "still-here", "alias": "x", "triggers": [], "actions": []}])

    handler = hass.services._handlers[(DOMAIN, "delete_automation")]
    # Must not raise even though "already-gone" was never in the file -
    # the documented ValueError race is swallowed (see __init__.py's
    # _async_delete_automation docstring).
    monkeypatch.setattr(homeintent_init, "SELF_DELETE_GRACE_SECONDS", 0)
    asyncio.run(_call_service_and_wait(hass, handler, ServiceCall({"automation_id": "already-gone"})))

    remaining = _automations_yaml(tmp_path)
    assert [a["id"] for a in remaining] == ["still-here"]


def test_calling_the_service_without_an_automation_id_is_a_no_op(tmp_path):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    _write_automations_yaml(tmp_path, [{"id": "still-here", "alias": "x", "triggers": [], "actions": []}])

    handler = hass.services._handlers[(DOMAIN, "delete_automation")]
    asyncio.run(handler(ServiceCall({})))

    remaining = _automations_yaml(tmp_path)
    assert [a["id"] for a in remaining] == ["still-here"]


def test_service_handler_returns_before_the_delete_starts(monkeypatch, tmp_path):
    """The calling automation must be able to finish before reload begins."""
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()

    async def scenario() -> None:
        await homeintent_init.async_setup_entry(hass, entry)
        delete_started = asyncio.Event()

        async def record_delete(_hass, _automation_id):
            delete_started.set()

        monkeypatch.setattr(
            homeintent_init, "_async_delete_automation_by_id", record_delete
        )
        monkeypatch.setattr(homeintent_init, "SELF_DELETE_GRACE_SECONDS", 0)
        handler = hass.services._handlers[(DOMAIN, "delete_automation")]

        await handler(ServiceCall({"automation_id": "delete-me"}))

        # The handler has returned, but the scheduled deletion has not run
        # inline as part of the service action.
        assert delete_started.is_set() is False
        await asyncio.gather(*hass._tasks)
        assert delete_started.is_set() is True

    asyncio.run(scenario())


def test_delayed_self_delete_retries_transient_failures(monkeypatch):
    hass = HomeAssistant()
    delete = AsyncMock(
        side_effect=[RuntimeError("reload 1"), RuntimeError("reload 2"), None]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(homeintent_init, "_async_delete_automation_by_id", delete)
    monkeypatch.setattr(homeintent_init.asyncio, "sleep", sleep)
    monkeypatch.setattr(homeintent_init, "SELF_DELETE_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(homeintent_init, "SELF_DELETE_RETRY_SECONDS", (1.0, 5.0))

    asyncio.run(
        homeintent_init._async_delete_automation_after_action(hass, "retry-me")
    )

    assert delete.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [0.1, 1.0, 5.0]


def _reset_setup(tmp_path, *, admin: bool):
    hass = _make_hass(tmp_path)
    entry = ConfigEntry()
    hass.config_entries.async_entries = lambda domain: [entry] if domain == DOMAIN else []
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=admin))
    )
    asyncio.run(homeintent_init.async_setup_entry(hass, entry))
    from homeintent.nlu.context import ConversationContext

    runtime = entry.runtime_data
    runtime.context_store.set(
        "c1", ConversationContext(last_command=None, last_entities=(), last_area=None, pending_clarification=None)
    )
    call = ServiceCall({})
    call.context = SimpleNamespace(user_id="admin" if admin else "anna")
    return hass, runtime, hass.services._handlers[(DOMAIN, "reset_test_state")], call


def test_reset_test_state_forgets_discourse_but_keeps_bindings(tmp_path):
    """7.6.0: measurement series start from the same HomeIntent state."""
    hass, runtime, handler, call = _reset_setup(tmp_path, admin=True)
    runtime.bindings.async_clear = AsyncMock()

    asyncio.run(handler(call))

    assert runtime.context_store.get("c1") is None
    runtime.bindings.async_clear.assert_not_awaited()


def test_reset_test_state_clears_bindings_only_on_request(tmp_path):
    hass, runtime, handler, call = _reset_setup(tmp_path, admin=True)
    runtime.bindings.async_clear = AsyncMock()
    call = ServiceCall({"include_bindings": True})
    call.context = SimpleNamespace(user_id="admin")

    asyncio.run(handler(call))

    runtime.bindings.async_clear.assert_awaited_once()


def test_reset_test_state_is_admin_only(tmp_path):
    hass, runtime, handler, call = _reset_setup(tmp_path, admin=False)

    with pytest.raises(PermissionError):
        asyncio.run(handler(call))
    assert runtime.context_store.get("c1") is not None
