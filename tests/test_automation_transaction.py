"""Conflict detection, crash recovery, and startup reconciliation tests."""

from __future__ import annotations

from _ha_stub import ServiceMock  # noqa: E402
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.automation_executor import AutomationExecutor  # noqa: E402
from homeintent.automation_metadata_store import (  # noqa: E402
    AutomationMetadata,
    AutomationMetadataStore,
)
from homeintent.automation_transaction import (  # noqa: E402
    ConcurrentAutomationUpdateError,
    TRANSACTION_JOURNAL_FILENAME,
)
from homeassistant.core import HomeAssistant  # noqa: E402


def _hass(tmp_path: Path) -> HomeAssistant:
    hass = HomeAssistant()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    hass.services.async_call = ServiceMock()
    return hass


def _write_yaml(path: Path, automations: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump(automations, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_yaml(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def test_external_edit_between_read_and_write_is_preserved(tmp_path):
    hass = _hass(tmp_path)
    executor = AutomationExecutor(hass)
    path = tmp_path / "automations.yaml"
    original = [{"id": "old", "alias": "Alt"}]
    external = [*original, {"id": "ui", "alias": "Im UI erstellt"}]
    _write_yaml(path, original)

    snapshot = asyncio.run(executor._async_read_snapshot(str(path)))
    _write_yaml(path, external)

    with pytest.raises(ConcurrentAutomationUpdateError):
        asyncio.run(
            executor._async_begin_transaction(
                operation="create",
                automation_id="homeintent",
                path=str(path),
                snapshot=snapshot,
                updated=[*original, {"id": "homeintent", "alias": "Sprachbefehl"}],
            )
        )

    assert _read_yaml(path) == external
    assert not (tmp_path / TRANSACTION_JOURNAL_FILENAME).exists()


def test_startup_recovery_rolls_back_an_incomplete_own_write(tmp_path):
    hass = _hass(tmp_path)
    executor = AutomationExecutor(hass)
    path = tmp_path / "automations.yaml"
    original = [{"id": "old", "alias": "Alt"}]
    updated = [*original, {"id": "new", "alias": "Neu"}]
    _write_yaml(path, original)

    snapshot = asyncio.run(executor._async_read_snapshot(str(path)))
    asyncio.run(
        executor._async_begin_transaction(
            operation="create",
            automation_id="new",
            path=str(path),
            snapshot=snapshot,
            updated=updated,
        )
    )
    assert _read_yaml(path) == updated

    recovered = asyncio.run(
        AutomationExecutor(hass).async_recover_incomplete_transaction()
    )

    assert recovered is True
    assert _read_yaml(path) == original
    assert not (tmp_path / TRANSACTION_JOURNAL_FILENAME).exists()
    hass.services.async_call.assert_awaited_once_with(
        "automation", "reload", {}, blocking=True
    )


def test_prepared_journal_already_recovers_crash_after_yaml_replace(tmp_path):
    hass = _hass(tmp_path)
    executor = AutomationExecutor(hass)
    path = tmp_path / "automations.yaml"
    original = [{"id": "old", "alias": "Alt"}]
    updated = [*original, {"id": "new", "alias": "Neu"}]
    _write_yaml(path, original)
    snapshot = asyncio.run(executor._async_read_snapshot(str(path)))
    real_write = executor._journal.write
    writes = 0

    def crash_on_second_journal_write(transaction):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulierter Absturz vor Journal-Abschluss")
        real_write(transaction)

    executor._journal.write = crash_on_second_journal_write
    with pytest.raises(OSError):
        asyncio.run(
            executor._async_begin_transaction(
                operation="create",
                automation_id="new",
                path=str(path),
                snapshot=snapshot,
                updated=updated,
            )
        )

    assert _read_yaml(path) == updated
    assert asyncio.run(
        AutomationExecutor(hass).async_recover_incomplete_transaction()
    ) is True
    assert _read_yaml(path) == original


def test_startup_reconciliation_removes_orphan_metadata_and_repairs_category(
    tmp_path,
):
    hass = _hass(tmp_path)
    _write_yaml(
        tmp_path / "automations.yaml",
        [{"id": "valid", "alias": "HomeIntent gültig"}],
    )
    store = AutomationMetadataStore(hass)

    async def seed() -> None:
        await store.async_save(
            AutomationMetadata("valid", "gültig", "2026-08-21T08:00:00+00:00")
        )
        await store.async_save(
            AutomationMetadata("orphan", "verwaist", "2026-08-21T08:00:00+00:00")
        )

    asyncio.run(seed())
    removed = asyncio.run(
        AutomationExecutor(hass).async_reconcile_homeintent_state()
    )

    assert removed == ("orphan",)
    assert set(asyncio.run(store.async_load_all())) == {"valid"}
    entity_registry = __import__(
        "homeassistant.helpers.entity_registry", fromlist=["async_get"]
    ).async_get(hass)
    entry = entity_registry.async_get("automation.valid")
    assert entry.categories.keys() == {"automation"}


def test_reschedule_updates_time_date_and_metadata_as_one_transaction(tmp_path):
    hass = _hass(tmp_path)
    automation_id = "scheduled"
    _write_yaml(
        tmp_path / "automations.yaml",
        [
            {
                "id": automation_id,
                "alias": "Rolllade später",
                "triggers": [{"trigger": "time", "at": "08:00:00"}],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ now().date().isoformat() == '2026-08-22' }}",
                    }
                ],
                "actions": [],
            }
        ],
    )
    store = AutomationMetadataStore(hass)
    asyncio.run(
        store.async_save(
            AutomationMetadata(
                automation_id,
                "Rolllade später",
                "2026-08-21T08:00:00+00:00",
                scheduled_for="2026-08-22T08:00:00+00:00",
                once=True,
            )
        )
    )

    asyncio.run(
        AutomationExecutor(hass).async_reschedule_automation(
            automation_id,
            datetime.fromisoformat("2026-08-22T20:15:00+00:00"),
        )
    )

    automation = _read_yaml(tmp_path / "automations.yaml")[0]
    assert automation["triggers"] == [{"trigger": "time", "at": "20:15:00"}]
    assert "2026-08-22" in automation["conditions"][0]["value_template"]
    metadata = asyncio.run(store.async_load_all())[automation_id]
    assert metadata["scheduled_for"] == "2026-08-22T20:15:00+00:00"
    assert metadata["version"] == 2


def test_bounded_automation_is_deleted_exactly_at_its_run_limit(tmp_path):
    hass = _hass(tmp_path)
    automation_id = "bounded"
    _write_yaml(
        tmp_path / "automations.yaml",
        [{"id": automation_id, "alias": "Dreimal", "triggers": [], "actions": []}],
    )
    store = AutomationMetadataStore(hass)
    asyncio.run(
        store.async_save(
            AutomationMetadata(
                automation_id,
                "Dreimal",
                "2026-08-21T08:00:00+00:00",
                max_runs=3,
            )
        )
    )
    executor = AutomationExecutor(hass)

    assert asyncio.run(executor.async_record_automation_run(automation_id)) is False
    assert asyncio.run(executor.async_record_automation_run(automation_id)) is False
    assert asyncio.run(store.async_load_all())[automation_id]["run_count"] == 2
    assert asyncio.run(executor.async_record_automation_run(automation_id)) is True

    assert _read_yaml(tmp_path / "automations.yaml") == []
    assert automation_id not in asyncio.run(store.async_load_all())


def test_existing_homeintent_automation_can_be_limited_transactionally(tmp_path):
    hass = _hass(tmp_path)
    automation_id = "persistent"
    _write_yaml(
        tmp_path / "automations.yaml",
        [
            {
                "id": automation_id,
                "alias": "Dauerhaft",
                "triggers": [{"trigger": "state", "entity_id": "binary_sensor.fenster"}],
                "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kueche"}}],
            }
        ],
    )
    store = AutomationMetadataStore(hass)
    asyncio.run(
        store.async_save(
            AutomationMetadata(
                automation_id,
                "Dauerhaft",
                "2026-08-21T08:00:00+00:00",
            )
        )
    )

    asyncio.run(AutomationExecutor(hass).async_set_max_runs(automation_id, 3))

    automation = _read_yaml(tmp_path / "automations.yaml")[0]
    assert automation["actions"][-1] == {
        "action": "homeintent.record_automation_run",
        "data": {"automation_id": automation_id, "max_runs": 3},
    }
    metadata = asyncio.run(store.async_load_all())[automation_id]
    assert (metadata["max_runs"], metadata["run_count"]) == (3, 0)
