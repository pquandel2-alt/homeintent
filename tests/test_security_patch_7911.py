"""Regression tests for the HACS security and event-loop review."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _ha_stub import ServiceMock  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeintent.automation_executor import AutomationExecutor  # noqa: E402
from homeintent.automation_metadata_store import METADATA_FILENAME  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.goal_run import GoalRunStore  # noqa: E402
from homeintent.learning_policy import LearningMode, LearningPolicy  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.thermal_tracker import ThermalExperienceTracker  # noqa: E402


def _hass(tmp_path: Path) -> HomeAssistant:
    hass = HomeAssistant()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    hass.services.async_call = ServiceMock()
    return hass


def _write_yaml(tmp_path: Path, values: list[dict[str, object]]) -> None:
    (tmp_path / "automations.yaml").write_text(
        yaml.safe_dump(values, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_yaml(tmp_path: Path) -> list[dict[str, object]]:
    return yaml.safe_load((tmp_path / "automations.yaml").read_text(encoding="utf-8")) or []


def _write_metadata(tmp_path: Path, values: dict[str, object]) -> None:
    (tmp_path / METADATA_FILENAME).write_text(json.dumps(values), encoding="utf-8")


def _owned_metadata(automation_id: str, owner: str = "owner") -> dict[str, object]:
    return {
        "automation_id": automation_id,
        "created_by": "homeintent",
        "source_text": automation_id,
        "created_at": "2026-10-19T00:00:00+00:00",
        "version": 1,
        "owner_user_id": owner,
    }


def test_delete_refuses_manual_automation_without_any_side_effect(tmp_path):
    hass = _hass(tmp_path)
    manual = {"id": "manual", "alias": "Im UI erstellt"}
    _write_yaml(tmp_path, [manual])

    with pytest.raises(ValueError, match="HomeIntent"):
        asyncio.run(AutomationExecutor(hass).async_delete_automation("manual"))

    assert _read_yaml(tmp_path) == [manual]
    hass.services.async_call.assert_not_awaited()


def test_delete_refuses_foreign_sidecar_without_any_side_effect(tmp_path):
    hass = _hass(tmp_path)
    foreign = {"id": "foreign", "alias": "Fremd"}
    _write_yaml(tmp_path, [foreign])
    _write_metadata(
        tmp_path,
        {"foreign": {"automation_id": "foreign", "created_by": "another-integration"}},
    )

    with pytest.raises(ValueError, match="HomeIntent"):
        asyncio.run(AutomationExecutor(hass).async_delete_automation("foreign"))

    assert _read_yaml(tmp_path) == [foreign]
    hass.services.async_call.assert_not_awaited()


def test_delete_refuses_corrupt_metadata_and_keeps_registry(tmp_path):
    hass = _hass(tmp_path)
    automation = {"id": "owned", "alias": "Nicht anfassen"}
    _write_yaml(tmp_path, [automation])
    (tmp_path / METADATA_FILENAME).write_text("{not-json", encoding="utf-8")
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("automation", "automation", "owned")

    with pytest.raises(ValueError):
        asyncio.run(AutomationExecutor(hass).async_delete_automation("owned"))

    assert _read_yaml(tmp_path) == [automation]
    assert registry.async_get(entity_id) is not None
    hass.services.async_call.assert_not_awaited()


def test_delete_refuses_contradictory_sidecar_without_any_side_effect(tmp_path):
    hass = _hass(tmp_path)
    automation = {"id": "owned", "alias": "Nicht anfassen"}
    _write_yaml(tmp_path, [automation])
    _write_metadata(
        tmp_path,
        {"owned": {**_owned_metadata("different"), "created_by": "homeintent"}},
    )

    with pytest.raises(ValueError, match="widersprechen"):
        asyncio.run(AutomationExecutor(hass).async_delete_automation("owned"))

    assert _read_yaml(tmp_path) == [automation]
    hass.services.async_call.assert_not_awaited()


def test_delete_allows_a_proven_homeintent_one_shot(tmp_path):
    hass = _hass(tmp_path)
    automation = {"id": "once", "alias": "Einmal", "actions": []}
    _write_yaml(tmp_path, [automation])
    _write_metadata(tmp_path, {"once": {**_owned_metadata("once"), "once": True}})

    asyncio.run(AutomationExecutor(hass).async_delete_automation("once"))

    assert _read_yaml(tmp_path) == []
    assert json.loads((tmp_path / METADATA_FILENAME).read_text(encoding="utf-8")) == {}
    hass.services.async_call.assert_awaited_once()


def test_resume_requires_a_matching_open_pause_record(tmp_path):
    hass = _hass(tmp_path)
    target = {"id": "target", "initial_state": False}
    _write_yaml(tmp_path, [target])
    _write_metadata(
        tmp_path,
        {"target": {"automation_id": "target", "created_by": "homeintent"}},
    )

    with pytest.raises(ValueError, match="Pausenauftrag"):
        asyncio.run(
            AutomationExecutor(hass).async_resume_paused_automation(
                "target", helper_id="forged", pause_id="forged"
            )
        )

    assert _read_yaml(tmp_path) == [target]
    hass.services.async_call.assert_not_awaited()


def test_pause_resume_survives_restart_and_is_single_use(tmp_path):
    (tmp_path / ".storage").mkdir()
    hass = _hass(tmp_path)
    target = {"id": "target", "alias": "Ziel", "initial_state": True}
    _write_yaml(tmp_path, [target])
    _write_metadata(tmp_path, {"target": _owned_metadata("target")})
    resume_at = datetime.now(timezone.utc) + timedelta(hours=1)

    helper_id = asyncio.run(
        AutomationExecutor(hass).async_pause_automation_until("target", resume_at)
    )
    paused = _read_yaml(tmp_path)
    helper = next(item for item in paused if item["id"] == helper_id)
    proof = helper["actions"][0]["data"]
    assert next(item for item in paused if item["id"] == "target")["initial_state"] is False
    metadata = json.loads((tmp_path / METADATA_FILENAME).read_text(encoding="utf-8"))
    metadata[helper_id]["pause_resume_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    _write_metadata(tmp_path, metadata)

    restarted_hass = _hass(tmp_path)
    restarted = AutomationExecutor(restarted_hass)
    asyncio.run(
        restarted.async_resume_paused_automation(
            "target", helper_id=helper_id, pause_id=proof["pause_id"]
        )
    )
    assert next(
        item for item in _read_yaml(tmp_path) if item["id"] == "target"
    )["initial_state"] is True
    metadata = json.loads((tmp_path / METADATA_FILENAME).read_text(encoding="utf-8"))
    assert metadata[helper_id]["pause_status"] == "consumed"

    restarted_hass.services.async_call.reset_mock()
    before = _read_yaml(tmp_path)
    with pytest.raises(ValueError, match="offener.*Pausenauftrag"):
        asyncio.run(
            restarted.async_resume_paused_automation(
                "target", helper_id=helper_id, pause_id=proof["pause_id"]
            )
        )
    assert _read_yaml(tmp_path) == before
    restarted_hass.services.async_call.assert_not_awaited()


def test_resume_rejects_wrong_helper_and_pause_id_without_write(tmp_path):
    hass = _hass(tmp_path)
    target = {"id": "target", "initial_state": False}
    helper = {"id": "helper", "actions": []}
    _write_yaml(tmp_path, [target, helper])
    _write_metadata(
        tmp_path,
        {
            "target": _owned_metadata("target"),
            "helper": {
                **_owned_metadata("helper"),
                "once": True,
                "pause_id": "real-pause",
                "pause_target_automation_id": "target",
                "pause_resume_at": datetime.now(timezone.utc).isoformat(),
                "pause_status": "open",
            },
        },
    )
    before = _read_yaml(tmp_path)

    for helper_id, pause_id in (("other", "real-pause"), ("helper", "wrong")):
        with pytest.raises(ValueError, match="Pausenauftrag"):
            asyncio.run(
                AutomationExecutor(hass).async_resume_paused_automation(
                    "target", helper_id=helper_id, pause_id=pause_id
                )
            )

    assert _read_yaml(tmp_path) == before
    hass.services.async_call.assert_not_awaited()


def test_resume_rejects_an_open_pause_before_its_due_time(tmp_path):
    hass = _hass(tmp_path)
    target = {"id": "target", "initial_state": False}
    helper = {"id": "helper", "actions": []}
    _write_yaml(tmp_path, [target, helper])
    _write_metadata(
        tmp_path,
        {
            "target": _owned_metadata("target"),
            "helper": {
                **_owned_metadata("helper"),
                "once": True,
                "pause_id": "pause",
                "pause_target_automation_id": "target",
                "pause_resume_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "pause_status": "open",
            },
        },
    )

    with pytest.raises(ValueError, match="noch nicht.*fällig"):
        asyncio.run(
            AutomationExecutor(hass).async_resume_paused_automation(
                "target", helper_id="helper", pause_id="pause"
            )
        )

    assert _read_yaml(tmp_path) == [target, helper]
    hass.services.async_call.assert_not_awaited()


def test_resume_reload_failure_restores_disabled_state_and_open_record(tmp_path):
    hass = _hass(tmp_path)
    hass.services.async_call = ServiceMock(
        side_effect=[RuntimeError("reload failed"), None]
    )
    target = {"id": "target", "initial_state": False}
    helper = {"id": "helper", "actions": []}
    _write_yaml(tmp_path, [target, helper])
    helper_metadata = {
        **_owned_metadata("helper"),
        "once": True,
        "pause_id": "pause",
        "pause_target_automation_id": "target",
        "pause_resume_at": datetime.now(timezone.utc).isoformat(),
        "pause_status": "open",
    }
    _write_metadata(
        tmp_path,
        {"target": _owned_metadata("target"), "helper": helper_metadata},
    )

    with pytest.raises(RuntimeError, match="reload failed"):
        asyncio.run(
            AutomationExecutor(hass).async_resume_paused_automation(
                "target", helper_id="helper", pause_id="pause"
            )
        )

    assert _read_yaml(tmp_path) == [target, helper]
    metadata = json.loads((tmp_path / METADATA_FILENAME).read_text(encoding="utf-8"))
    assert metadata["helper"]["pause_status"] == "open"


def test_rollback_refuses_external_yaml_change_and_keeps_history(tmp_path):
    hass = _hass(tmp_path)
    (tmp_path / ".storage").mkdir()
    before = [{"id": "owned", "alias": "Vorher"}]
    after = [{"id": "owned", "alias": "Nachher"}]
    external = [*after, {"id": "manual", "alias": "Neu im Editor"}]
    _write_yaml(tmp_path, external)
    history = AutomationExecutor(hass)._history
    history.append(
        operation="edit",
        automation_id="owned",
        created_at="2026-10-19T00:00:00+00:00",
        automations=before,
        metadata={"owned": {"automation_id": "owned", "created_by": "homeintent"}},
        post_write_digest="sha256:not-the-current-file",
        post_metadata_digest="missing",
        owner_user_id="owner",
    )

    with pytest.raises(ValueError, match="inzwischen geändert"):
        asyncio.run(
            AutomationExecutor(hass).async_rollback_last_change(
                requesting_user_id="owner", is_admin=False
            )
        )

    assert _read_yaml(tmp_path) == external
    assert history.peek() is not None
    hass.services.async_call.assert_not_awaited()


def test_rollback_requires_owner_but_allows_owner_and_admin(tmp_path):
    async def created_world(base: Path):
        (base / ".storage").mkdir()
        hass = _hass(base)
        executor = AutomationExecutor(hass)
        await executor.async_create_automation(
            {"alias": "Owned", "triggers": [], "actions": []},
            automation_id="owned",
            owner_user_id="owner",
        )
        return hass, executor

    owner_dir = tmp_path / "owner"
    owner_dir.mkdir()
    owner_hass, owner_executor = asyncio.run(created_world(owner_dir))
    before = _read_yaml(owner_dir)
    with pytest.raises(PermissionError):
        asyncio.run(
            owner_executor.async_rollback_last_change(
                requesting_user_id="intruder", is_admin=False
            )
        )
    assert _read_yaml(owner_dir) == before
    owner_hass.services.async_call.reset_mock()
    asyncio.run(
        owner_executor.async_rollback_last_change(
            requesting_user_id="owner", is_admin=False
        )
    )
    assert _read_yaml(owner_dir) == []

    admin_dir = tmp_path / "admin"
    admin_dir.mkdir()
    _admin_hass, admin_executor = asyncio.run(created_world(admin_dir))
    asyncio.run(
        admin_executor.async_rollback_last_change(
            requesting_user_id="different", is_admin=True
        )
    )
    assert _read_yaml(admin_dir) == []


def test_rollback_refuses_external_metadata_change_and_keeps_history(tmp_path):
    (tmp_path / ".storage").mkdir()
    hass = _hass(tmp_path)
    executor = AutomationExecutor(hass)
    asyncio.run(
        executor.async_create_automation(
            {"alias": "Owned", "triggers": [], "actions": []},
            automation_id="owned",
            owner_user_id="owner",
        )
    )
    before_yaml = _read_yaml(tmp_path)
    metadata = json.loads((tmp_path / METADATA_FILENAME).read_text(encoding="utf-8"))
    metadata["owned"]["external_note"] = "editor change"
    _write_metadata(tmp_path, metadata)
    hass.services.async_call.reset_mock()

    with pytest.raises(ValueError, match="Metadaten wurden inzwischen geändert"):
        asyncio.run(
            executor.async_rollback_last_change(
                requesting_user_id="owner", is_admin=False
            )
        )

    assert _read_yaml(tmp_path) == before_yaml
    assert json.loads(
        (tmp_path / METADATA_FILENAME).read_text(encoding="utf-8")
    ) == metadata
    assert executor._history.peek() is not None
    hass.services.async_call.assert_not_awaited()


def test_old_history_without_digests_is_rejected_and_retained(tmp_path):
    (tmp_path / ".storage").mkdir()
    hass = _hass(tmp_path)
    _write_yaml(tmp_path, [{"id": "owned"}])
    history = AutomationExecutor(hass)._history
    history.append(
        operation="create",
        automation_id="owned",
        created_at="old",
        automations=[],
        metadata={},
        owner_user_id="owner",
    )

    with pytest.raises(ValueError, match="keine sicheren Zustandsprüfsummen"):
        asyncio.run(
            AutomationExecutor(hass).async_rollback_last_change(
                requesting_user_id="owner", is_admin=False
            )
        )

    assert _read_yaml(tmp_path) == [{"id": "owned"}]
    assert history.peek() is not None
    hass.services.async_call.assert_not_awaited()


def test_repeated_undo_remains_consistent_across_executor_restarts(tmp_path):
    (tmp_path / ".storage").mkdir()
    hass = _hass(tmp_path)
    executor = AutomationExecutor(hass)
    asyncio.run(
        executor.async_create_automation(
            {"alias": "Owned", "triggers": [], "actions": []},
            automation_id="owned",
            owner_user_id="owner",
        )
    )
    asyncio.run(executor.async_disable_automation("owned"))
    assert _read_yaml(tmp_path)[0]["initial_state"] is False

    restarted = AutomationExecutor(_hass(tmp_path))
    asyncio.run(
        restarted.async_rollback_last_change(
            requesting_user_id="owner", is_admin=False
        )
    )
    assert "initial_state" not in _read_yaml(tmp_path)[0]

    restarted_again = AutomationExecutor(_hass(tmp_path))
    asyncio.run(
        restarted_again.async_rollback_last_change(
            requesting_user_id="owner", is_admin=False
        )
    )
    assert _read_yaml(tmp_path) == []
    assert restarted_again._history.peek() is None


def test_goal_run_without_thermal_cycle_does_not_write(tmp_path):
    manager = SimpleNamespace(policy=LearningPolicy(learning_mode=LearningMode.ASK))
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    run = SimpleNamespace(steps=(), goal_id="goal", run_id="run", user_id="owner")

    asyncio.run(tracker.async_associate_goal_run(run))

    assert not (tmp_path / "thermal.json").exists()
    assert tracker._generation == 0


def test_learning_off_restore_does_not_create_thermal_document(tmp_path):
    manager = SimpleNamespace(policy=LearningPolicy(learning_mode=LearningMode.OFF))
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    runs = GoalRunStore(tmp_path / "runs.json")

    restored = asyncio.run(
        tracker.async_restore((), runs, now=datetime.now(timezone.utc))
    )

    assert restored == 0
    assert not (tmp_path / "thermal.json").exists()
    assert tracker._generation == 0


def test_observe_action_never_waits_for_the_filesystem(tmp_path):
    policy = LearningPolicy(learning_mode=LearningMode.ASK)
    manager = SimpleNamespace(
        policy=policy,
        predictive_house=SimpleNamespace(thermal_model=lambda _area_id: None),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living"
    )
    sensor = EntitySnapshot(
        "sensor.living", "Temperatur", "sensor", "19", area_id="living",
        unit="°C", device_class="temperature",
    )
    plan = ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    )
    original_write = tracker._write_document

    def slow_write(document):
        time.sleep(0.15)
        original_write(document)

    tracker._write_document = slow_write

    async def scenario() -> float:
        started = time.perf_counter()
        tracker.observe_action(plan, (climate, sensor), occurred_at=datetime.now(timezone.utc))
        elapsed = time.perf_counter() - started
        await tracker.async_close()
        return elapsed

    assert asyncio.run(scenario()) < 0.05


def test_learning_off_observe_action_never_persists(tmp_path):
    manager = SimpleNamespace(
        policy=LearningPolicy(learning_mode=LearningMode.OFF),
        predictive_house=SimpleNamespace(thermal_model=lambda _area_id: None),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living"
    )
    sensor = EntitySnapshot(
        "sensor.living", "Temperatur", "sensor", "19", area_id="living",
        unit="°C", device_class="temperature",
    )

    async def scenario() -> None:
        tracker.observe_action(
            ServiceCallPlan(
                "climate", "set_temperature", "climate.living", {"temperature": 21}
            ),
            (climate, sensor),
            occurred_at=datetime.now(timezone.utc),
        )
        await tracker.async_close()

    asyncio.run(scenario())
    assert not (tmp_path / "thermal.json").exists()
    assert tracker._generation == 0


def test_rapid_thermal_changes_share_one_worker_and_latest_generation_wins(tmp_path):
    manager = SimpleNamespace(
        policy=LearningPolicy(learning_mode=LearningMode.ASK),
        predictive_house=SimpleNamespace(thermal_model=lambda _area_id: None),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living"
    )
    sensor = EntitySnapshot(
        "sensor.living", "Temperatur", "sensor", "19", area_id="living",
        unit="°C", device_class="temperature",
    )
    plan = ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    )
    writes: list[dict[str, object]] = []
    original_write = tracker._write_document

    def counted_write(document):
        writes.append(document)
        original_write(document)

    tracker._write_document = counted_write

    async def scenario() -> None:
        first_task = None
        for index in range(10):
            tracker.observe_action(
                plan,
                (climate, sensor),
                occurred_at=datetime(2026, 10, 19, 10, index, tzinfo=timezone.utc),
            )
            if first_task is None:
                first_task = tracker._persist_task
            assert tracker._persist_task is first_task
        await tracker.async_close()

    asyncio.run(scenario())
    document = json.loads((tmp_path / "thermal.json").read_text(encoding="utf-8"))
    assert len(writes) == 1
    assert document["cycles"][0]["started_at"].startswith("2026-10-19T10:09")
    assert tracker._written_generation == tracker._generation == 10
    assert tracker._persist_task is None


def test_thermal_write_failure_keeps_previous_file_and_leaves_no_task(
    tmp_path, caplog
):
    path = tmp_path / "thermal.json"
    previous = '{"schema_version": 1, "cycles": [{"safe": true}]}'
    path.write_text(previous, encoding="utf-8")
    manager = SimpleNamespace(
        policy=LearningPolicy(learning_mode=LearningMode.ASK),
        predictive_house=SimpleNamespace(thermal_model=lambda _area_id: None),
    )
    tracker = ThermalExperienceTracker(manager, state_path=path)
    tracker._write_document = lambda _document: (_ for _ in ()).throw(
        OSError("disk full")
    )
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living"
    )
    sensor = EntitySnapshot(
        "sensor.living", "Temperatur", "sensor", "19", area_id="living",
        unit="°C", device_class="temperature",
    )

    async def scenario() -> None:
        tracker.observe_action(
            ServiceCallPlan(
                "climate", "set_temperature", "climate.living", {"temperature": 21}
            ),
            (climate, sensor),
            occurred_at=datetime.now(timezone.utc),
        )
        await tracker.async_close()

    asyncio.run(scenario())
    assert path.read_text(encoding="utf-8") == previous
    assert tracker._persist_task is None
    assert "thermal persistence failed" in caplog.text
