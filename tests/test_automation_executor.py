"""HomeIntent V5 Teil 7/10 + Teil 8/10 (V5.26, V5.30-V5.32, V5.35, V5.36):
automation_executor.py's AutomationExecutor - the sole HA-persistence path
for a confirmed AutomationModel (read-modify-write automations.yaml, then
``automation.reload``, then record identity/metadata in the sidecar store -
all as one transaction, rolled back on any failure).

Needs the real ``homeassistant.core``/``homeassistant.util`` module names to
exist for this module's own top-level imports to succeed - installs
``tests/_ha_stub.py`` first, same reason every ``conversation.py``-importing
test file already does (see that stub's own docstring). Unlike those tests,
every actual file/service touchpoint here is mocked directly
(``yaml_util.load_yaml``/``dump``, both modules' own
``write_utf8_file_atomic`` bindings, ``hass.services.async_call``) so no
test in this file ever touches a real file or calls a real HA service -
task #70's explicit requirement for the executor's tests.
"""

from __future__ import annotations

from _ha_stub import ServiceMock  # noqa: E402
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.automation_executor as automation_executor  # noqa: E402
import homeintent.automation_metadata_store as automation_metadata_store  # noqa: E402
import homeintent.automation_transaction as automation_transaction  # noqa: E402
from homeintent.automation_executor import AutomationExecutor  # noqa: E402

SAMPLE_CONFIG = {
    "alias": "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.kueche_fenster", "to": "on"}],
    "actions": [{"action": "homeassistant.turn_on", "target": {"entity_id": "light.kueche_licht"}}],
}


def _make_hass(config_dir: str = "/config") -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    # Joins parts like the real ``hass.config.path()`` - automations.yaml
    # and the metadata sidecar file must resolve to distinct paths, same as
    # in a real HA config dir.
    hass.config.path.side_effect = lambda *parts: "/".join([config_dir, *parts])

    async def _run_in_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run_in_executor)
    hass.services.async_call = ServiceMock()
    return hass


def _patch_metadata_write():
    """Every test in this file mocks the metadata sidecar's own write too -
    its read side already degrades to an empty dict on ``FileNotFoundError``
    (see ``AutomationMetadataStore._read``), so a real, harmless failed open()
    against the mocked hass's fake path needs no separate patch."""
    return patch.object(automation_metadata_store, "write_utf8_file_atomic")


@pytest.fixture(autouse=True)
def _patch_transaction_journal_write(monkeypatch):
    """Ordinary executor unit tests keep all journal I/O in memory."""
    monkeypatch.setattr(
        automation_transaction, "write_utf8_file_atomic", MagicMock()
    )


def test_create_automation_appends_to_an_empty_file_and_returns_the_new_id():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]) as load_mock,
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic") as write_mock,
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        automation_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    assert isinstance(automation_id, str) and automation_id
    load_mock.assert_called_once_with("/config/automations.yaml")
    dump_mock.assert_called_once()
    written_automations = dump_mock.call_args.args[0]
    assert written_automations == [{"id": automation_id, **SAMPLE_CONFIG}]
    write_mock.assert_called_once_with("/config/automations.yaml", "dumped-yaml")


def test_create_automation_appends_to_an_existing_non_empty_file():
    hass = _make_hass()
    existing = [{"id": "abc123", "alias": "Bestehende Automation", "triggers": [], "actions": []}]
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=list(existing)),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        automation_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    written_automations = dump_mock.call_args.args[0]
    assert len(written_automations) == 2
    assert written_automations[0] == existing[0]
    assert written_automations[1] == {"id": automation_id, **SAMPLE_CONFIG}


def test_a_missing_automations_yaml_is_treated_as_an_empty_list_not_an_error():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", side_effect=FileNotFoundError),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    written_automations = dump_mock.call_args.args[0]
    assert len(written_automations) == 1


def test_create_automation_calls_automation_reload_with_no_arguments():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)


def test_each_created_automation_gets_a_distinct_id():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        first_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))
        second_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    assert first_id != second_id


def test_file_io_runs_via_async_add_executor_job_not_directly_on_the_event_loop():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    # Stable YAML snapshot + journal preflight + off-loop serialization + two
    # durable journal writes + guarded YAML write + metadata read/write +
    # journal cleanup: every blocking operation stays off the event loop.
    assert hass.async_add_executor_job.await_count == 9


def test_two_executor_instances_share_one_lock_and_serialize_creations():
    hass = _make_hass()
    call_order: list[str] = []
    state: list[dict] = []

    def fake_load(path):
        call_order.append("read")
        return list(state)

    def fake_write(automations):
        call_order.append("write")
        state[:] = automations
        return "dumped-yaml"

    async def _run_in_executor_with_a_real_suspension_point(func, *args):
        # A plain synchronous side_effect never actually yields to the event
        # loop, so both tasks would run start-to-finish back to back even
        # without the lock - this ``asyncio.sleep(0)`` forces a genuine
        # task switch after the read, so the test can actually distinguish
        # "the lock serializes read+write" from "asyncio just happened not
        # to interleave them".
        await asyncio.sleep(0)
        return func(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run_in_executor_with_a_real_suspension_point)

    with (
        patch.object(automation_executor.yaml_util, "load_yaml", side_effect=fake_load),
        patch.object(automation_executor.yaml_util, "dump", side_effect=fake_write),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor_a = AutomationExecutor(hass)
        executor_b = AutomationExecutor(hass)
        assert executor_a._lock is executor_b._lock

        async def _run_both():
            await asyncio.gather(
                executor_a.async_create_automation(SAMPLE_CONFIG),
                executor_b.async_create_automation(SAMPLE_CONFIG),
            )

        asyncio.run(_run_both())

    # Each creation's read must be immediately followed by its own write -
    # a race (both reads before either write) would produce ["read", "read",
    # "write", "write"] and silently drop one automation.
    assert call_order == ["read", "write", "read", "write"]
    assert len(state) == 2


# --- V5.30/V5.31/V5.32: identity/metadata sidecar ---------------------------


def test_a_successful_creation_records_schedule_metadata():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        patch.object(automation_metadata_store, "write_utf8_file_atomic") as metadata_write_mock,
    ):
        executor = AutomationExecutor(hass)
        target = datetime(2026, 8, 21, 12, 5, tzinfo=timezone.utc)
        automation_id = asyncio.run(
            executor.async_create_automation(
                SAMPLE_CONFIG, scheduled_for=target, once=True
            )
        )

    metadata_write_mock.assert_called_once()
    written_path, written_json = metadata_write_mock.call_args.args
    assert written_path == "/config/homeintent_automation_metadata.json"
    entries = json.loads(written_json)
    assert set(entries) == {automation_id}
    entry = entries[automation_id]
    assert entry["automation_id"] == automation_id
    assert entry["source_text"] == SAMPLE_CONFIG["alias"]
    assert entry["created_by"] == "homeintent"
    assert entry["version"] == 1
    assert entry["source_language"] == "de"
    assert "created_at" in entry and entry["created_at"]
    assert entry["scheduled_for"] == "2026-08-21T12:05:00+00:00"
    assert entry["once"] is True


def test_cleanup_removes_expired_scheduled_once_but_keeps_future_and_ordinary():
    hass = _make_hass()
    expired = {"id": "expired", "alias": "expired", "triggers": [], "actions": []}
    future = {"id": "future", "alias": "future", "triggers": [], "actions": []}
    ordinary = {"id": "ordinary", "alias": "ordinary", "triggers": [], "actions": []}
    with (
        patch.object(
            automation_executor.yaml_util,
            "load_yaml",
            return_value=[expired, future, ordinary],
        ),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        executor._metadata_store.async_load_all = AsyncMock(
            return_value={
                "expired": {
                    "once": True,
                    "scheduled_for": "2026-08-21T11:59:00+00:00",
                },
                "future": {
                    "once": True,
                    "scheduled_for": "2026-08-21T12:01:00+00:00",
                },
                "ordinary": {"once": False},
            }
        )
        executor._metadata_store.async_delete = AsyncMock()

        removed = asyncio.run(
            executor.async_cleanup_expired_scheduled_automations(
                datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
            )
        )

    assert removed == ("expired",)
    assert dump_mock.call_args.args[0] == [future, ordinary]
    executor._metadata_store.async_delete.assert_awaited_once_with("expired")
    hass.services.async_call.assert_awaited_once_with(
        "automation", "reload", {}, blocking=True
    )


# --- V5.35/V5.36: transactional creation with rollback -----------------------


def test_a_reload_failure_rolls_back_the_automations_yaml_write():
    hass = _make_hass()
    hass.services.async_call = ServiceMock(side_effect=RuntimeError("reload failed"))
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        patch.object(automation_metadata_store, "write_utf8_file_atomic") as metadata_write_mock,
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(RuntimeError, match="reload failed"):
            asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    # First dump appended the new automation, second dump rolled back to the
    # empty list that was there before this call - the file must end up
    # exactly as if the call had never happened.
    assert [call.args[0] for call in dump_mock.call_args_list] == [
        [{"id": dump_mock.call_args_list[0].args[0][0]["id"], **SAMPLE_CONFIG}],
        [],
    ]
    # Metadata is only recorded after a successful reload - a failed reload
    # must never leave an orphaned metadata entry either.
    metadata_write_mock.assert_not_called()


def test_a_metadata_save_failure_rolls_back_the_yaml_write_and_reloads_again():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        patch.object(
            automation_metadata_store, "write_utf8_file_atomic", side_effect=OSError("disk full")
        ),
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(OSError, match="disk full"):
            asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    # Rolled back to the empty list, same as the reload-failure case.
    assert [call.args[0] for call in dump_mock.call_args_list] == [
        [{"id": dump_mock.call_args_list[0].args[0][0]["id"], **SAMPLE_CONFIG}],
        [],
    ]
    # Reloaded once for the (now rolled-back) append, and a second time so
    # HA's live config matches the restored file - "no half-created
    # automations" (V5.36) applies to HA's in-memory state too, not just
    # the file on disk.
    assert hass.services.async_call.await_count == 2


def test_a_second_reload_failure_during_rollback_still_surfaces_the_original_error():
    hass = _make_hass()
    hass.services.async_call = ServiceMock(side_effect=[None, RuntimeError("reload #2 also failed")])
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        patch.object(
            automation_metadata_store, "write_utf8_file_atomic", side_effect=OSError("disk full")
        ),
    ):
        executor = AutomationExecutor(hass)
        # The metadata failure is what the caller actually needs to see and
        # speak back to the user - a second, unrelated reload failure while
        # rolling back must not mask it.
        with pytest.raises(OSError, match="disk full"):
            asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))


# --- V5.28 (Wave 10): async_delete_automation() -------------------------------


EXISTING_AUTOMATION = {"id": "abc123", "alias": "Bestehende Automation", "triggers": [], "actions": []}
OTHER_AUTOMATION = {"id": "def456", "alias": "Andere Automation", "triggers": [], "actions": []}


def test_delete_automation_removes_the_matching_entry_and_keeps_the_rest():
    hass = _make_hass()
    with (
        patch.object(
            automation_executor.yaml_util,
            "load_yaml",
            return_value=[EXISTING_AUTOMATION, OTHER_AUTOMATION],
        ),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic") as write_mock,
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_delete_automation("abc123"))

    written_automations = dump_mock.call_args.args[0]
    assert written_automations == [OTHER_AUTOMATION]
    write_mock.assert_called_once_with("/config/automations.yaml", "dumped-yaml")


def test_delete_automation_calls_automation_reload_with_no_arguments():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[EXISTING_AUTOMATION]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_delete_automation("abc123"))

    hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)


def test_delete_automation_raises_value_error_when_the_id_is_not_found():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[EXISTING_AUTOMATION]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(ValueError, match="no-such-id"):
            asyncio.run(executor.async_delete_automation("no-such-id"))

    # Nothing was ever written - a not-found lookup must not touch the file.
    dump_mock.assert_not_called()
    hass.services.async_call.assert_not_awaited()


def test_delete_automation_removes_the_metadata_entry():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[EXISTING_AUTOMATION]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        patch.object(
            automation_metadata_store.AutomationMetadataStore,
            "async_delete",
            new_callable=AsyncMock,
        ) as delete_mock,
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_delete_automation("abc123"))

    delete_mock.assert_awaited_once_with("abc123")


def test_a_delete_reload_failure_rolls_back_the_automations_yaml_write():
    hass = _make_hass()
    hass.services.async_call = ServiceMock(side_effect=RuntimeError("reload failed"))
    with (
        patch.object(
            automation_executor.yaml_util,
            "load_yaml",
            return_value=[EXISTING_AUTOMATION, OTHER_AUTOMATION],
        ),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        patch.object(
            automation_metadata_store.AutomationMetadataStore,
            "async_delete",
            new_callable=AsyncMock,
        ) as delete_mock,
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(RuntimeError, match="reload failed"):
            asyncio.run(executor.async_delete_automation("abc123"))

    # First dump removed the entry, second dump rolled back to the original
    # two-automation list - the file must end up exactly as if the call had
    # never happened.
    assert [call.args[0] for call in dump_mock.call_args_list] == [
        [OTHER_AUTOMATION],
        [EXISTING_AUTOMATION, OTHER_AUTOMATION],
    ]
    # Metadata is only removed after a successful reload - a failed reload
    # must never leave the metadata entry dangling out of sync with a file
    # that (post-rollback) still has the automation.
    delete_mock.assert_not_awaited()


# --- Wave 11: async_disable_automation()/async_enable_automation() -----------


def test_disable_automation_sets_initial_state_false_and_keeps_the_rest():
    hass = _make_hass()
    with (
        patch.object(
            automation_executor.yaml_util,
            "load_yaml",
            return_value=[EXISTING_AUTOMATION, OTHER_AUTOMATION],
        ),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic") as write_mock,
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_disable_automation("abc123"))

    written_automations = dump_mock.call_args.args[0]
    assert written_automations == [
        {**EXISTING_AUTOMATION, "initial_state": False},
        OTHER_AUTOMATION,
    ]
    write_mock.assert_called_once_with("/config/automations.yaml", "dumped-yaml")


def test_enable_automation_sets_initial_state_true_explicitly():
    hass = _make_hass()
    disabled = {**EXISTING_AUTOMATION, "initial_state": False}
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[disabled]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_enable_automation("abc123"))

    written_automations = dump_mock.call_args.args[0]
    assert written_automations == [{**EXISTING_AUTOMATION, "initial_state": True}]


def test_disable_automation_calls_automation_reload_with_no_arguments():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[EXISTING_AUTOMATION]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        asyncio.run(executor.async_disable_automation("abc123"))

    hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)


def test_disable_automation_raises_value_error_when_the_id_is_not_found():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[EXISTING_AUTOMATION]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(ValueError, match="no-such-id"):
            asyncio.run(executor.async_disable_automation("no-such-id"))

    # Nothing was ever written - a not-found lookup must not touch the file.
    dump_mock.assert_not_called()
    hass.services.async_call.assert_not_awaited()


def test_a_disable_reload_failure_rolls_back_the_automations_yaml_write():
    hass = _make_hass()
    hass.services.async_call = ServiceMock(side_effect=RuntimeError("reload failed"))
    with (
        patch.object(
            automation_executor.yaml_util,
            "load_yaml",
            return_value=[EXISTING_AUTOMATION, OTHER_AUTOMATION],
        ),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(RuntimeError, match="reload failed"):
            asyncio.run(executor.async_disable_automation("abc123"))

    # First dump set initial_state False, second dump rolled back to the
    # original two-automation list - the file must end up exactly as if the
    # call had never happened.
    assert [call.args[0] for call in dump_mock.call_args_list] == [
        [{**EXISTING_AUTOMATION, "initial_state": False}, OTHER_AUTOMATION],
        [EXISTING_AUTOMATION, OTHER_AUTOMATION],
    ]


def test_an_enable_reload_failure_rolls_back_the_automations_yaml_write():
    hass = _make_hass()
    hass.services.async_call = ServiceMock(side_effect=RuntimeError("reload failed"))
    disabled = {**EXISTING_AUTOMATION, "initial_state": False}
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[disabled]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(RuntimeError, match="reload failed"):
            asyncio.run(executor.async_enable_automation("abc123"))

    assert [call.args[0] for call in dump_mock.call_args_list] == [
        [{**EXISTING_AUTOMATION, "initial_state": True}],
        [disabled],
    ]


def test_async_list_automations_reports_the_enabled_field_from_initial_state():
    hass = _make_hass()
    disabled = {**OTHER_AUTOMATION, "initial_state": False}
    with patch.object(
        automation_executor.yaml_util,
        "load_yaml",
        return_value=[EXISTING_AUTOMATION, disabled],
    ):
        executor = AutomationExecutor(hass)
        summaries = asyncio.run(executor.async_list_automations())

    by_id = {s.automation_id: s for s in summaries}
    # No ``initial_state`` key at all defaults to enabled, same default HA's
    # own automation config schema uses.
    assert by_id["abc123"].enabled is True
    assert by_id["def456"].enabled is False


def test_replace_actions_preserves_trigger_conditions_delay_and_housekeeping():
    hass = _make_hass()
    existing = {
        "id": "abc123",
        "alias": "Test",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.window"}],
        "conditions": [{"condition": "state", "entity_id": "person.philipp", "state": "home"}],
        "actions": [
            {"delay": {"seconds": 30}},
            {"action": "light.turn_on", "target": {"entity_id": "light.old"}},
            {"action": "homeintent.delete_automation", "data": {"automation_id": "abc123"}},
        ],
    }
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[existing]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        executor._metadata_store.async_load_all = AsyncMock(return_value={
            "abc123": {"created_by": "homeintent", "version": 2}
        })
        executor._metadata_store.async_update = AsyncMock()
        asyncio.run(executor.async_replace_automation_actions(
            "abc123",
            [{"action": "cover.close_cover", "target": {"entity_id": "cover.new"}}],
            "Neue Aktion",
        ))

    written = dump_mock.call_args.args[0][0]
    assert written["triggers"] == existing["triggers"]
    assert written["conditions"] == existing["conditions"]
    assert written["actions"] == [
        existing["actions"][0],
        {"action": "cover.close_cover", "target": {"entity_id": "cover.new"}},
        existing["actions"][2],
    ]


def test_replace_trigger_section_preserves_conditions_and_actions():
    hass = _make_hass()
    existing = {
        "id": "abc123",
        "alias": "Test",
        "triggers": [{"trigger": "time", "at": "20:00:00"}],
        "conditions": [{"condition": "state", "entity_id": "person.philipp", "state": "home"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.flur"}}],
    }
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[existing]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        executor._metadata_store.async_load_all = AsyncMock(return_value={
            "abc123": {"created_by": "homeintent", "version": 3, "once": False}
        })
        executor._metadata_store.async_update = AsyncMock()
        asyncio.run(executor.async_replace_automation_section(
            "abc123",
            "triggers",
            [{"trigger": "sun", "event": "sunset"}],
            "Neuer Auslöser",
        ))

    written = dump_mock.call_args.args[0][0]
    assert written == {
        **existing,
        "triggers": [{"trigger": "sun", "event": "sunset"}],
    }
    executor._metadata_store.async_update.assert_awaited_once_with(
        "abc123", source_text="Neuer Auslöser", version=4
    )


def test_structural_edit_refuses_one_shot_automation_before_write():
    hass = _make_hass()
    existing = {"id": "abc123", **SAMPLE_CONFIG}
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[existing]),
        patch.object(automation_executor.yaml_util, "dump") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
    ):
        executor = AutomationExecutor(hass)
        executor._metadata_store.async_load_all = AsyncMock(return_value={
            "abc123": {"created_by": "homeintent", "version": 1, "once": True}
        })
        with pytest.raises(ValueError, match="einmaliger Aufträge"):
            asyncio.run(executor.async_replace_automation_section(
                "abc123", "conditions", [], "Nicht erlaubt"
            ))

    dump_mock.assert_not_called()


# --- Wave 12, "Einmalige Automation" (fire-once, then self-delete) --------


def test_create_automation_uses_the_provided_automation_id_when_given():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        automation_id = asyncio.run(
            executor.async_create_automation(SAMPLE_CONFIG, automation_id="preassigned-id")
        )

    assert automation_id == "preassigned-id"
    written_automations = dump_mock.call_args.args[0]
    assert written_automations == [{"id": "preassigned-id", **SAMPLE_CONFIG}]


def test_create_automation_still_generates_its_own_id_when_none_is_given():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        automation_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    assert automation_id != "preassigned-id"
    assert isinstance(automation_id, str) and automation_id


def test_every_created_automation_is_assigned_to_the_homeintent_category():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        automation_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    category_registry = automation_executor.cr.async_get(hass)
    categories = list(category_registry.async_list_categories(scope="automation"))
    assert [(category.name, category.category_id) for category in categories] == [
        ("HomeIntent", "category-1")
    ]

    entity_registry = automation_executor.er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id("automation", "automation", automation_id)
    assert entity_registry.async_get(entity_id).categories == {
        "automation": categories[0].category_id
    }


def test_existing_homeintent_category_is_reused_case_insensitively():
    hass = _make_hass()
    category_registry = automation_executor.cr.async_get(hass)
    existing = category_registry.async_create(name="homeintent", scope="automation")
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        executor = AutomationExecutor(hass)
        automation_id = asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    assert len(list(category_registry.async_list_categories(scope="automation"))) == 1
    entity_registry = automation_executor.er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id("automation", "automation", automation_id)
    assert entity_registry.async_get(entity_id).categories == {
        "automation": existing.category_id
    }


def test_a_category_assignment_failure_rolls_back_yaml_and_live_automation():
    hass = _make_hass()
    with (
        patch.object(automation_executor.yaml_util, "load_yaml", return_value=[]),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml") as dump_mock,
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
        patch.object(
            AutomationExecutor,
            "_assign_homeintent_category",
            side_effect=RuntimeError("category assignment failed"),
        ),
    ):
        executor = AutomationExecutor(hass)
        with pytest.raises(RuntimeError, match="category assignment failed"):
            asyncio.run(executor.async_create_automation(SAMPLE_CONFIG))

    assert [call.args[0] for call in dump_mock.call_args_list] == [
        [{"id": dump_mock.call_args_list[0].args[0][0]["id"], **SAMPLE_CONFIG}],
        [],
    ]
    assert hass.services.async_call.await_count == 2


def test_delete_automation_removes_its_entity_registry_entry():
    hass = _make_hass()
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("automation", "automation", "abc123")
    assert registry.async_get(entity_id) is not None
    with (
        patch.object(
            automation_executor.yaml_util,
            "load_yaml",
            return_value=[EXISTING_AUTOMATION, OTHER_AUTOMATION],
        ),
        patch.object(automation_executor.yaml_util, "dump", return_value="dumped-yaml"),
        patch.object(automation_executor, "write_utf8_file_atomic"),
        _patch_metadata_write(),
    ):
        asyncio.run(AutomationExecutor(hass).async_delete_automation("abc123"))

    assert entity_id not in registry.entries
