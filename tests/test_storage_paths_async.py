from __future__ import annotations

import asyncio
import builtins
import os
import sys
import tempfile
import threading
from functools import partial, wraps
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub

_ha_stub.install()

from homeintent.agent_event import AgentEvent, AgentEventState, AgentMode
from homeintent.agent_event_store import AgentEventStore
from homeintent.automation_executor import AutomationExecutor
from homeintent.automation_history import (
    AutomationHistoryStore,
    canonical_state_digest,
)
from homeintent.automation_metadata_store import (
    AutomationMetadata,
    AutomationMetadataStore,
)
from homeintent.automation_transaction import (
    AutomationTransaction,
    AutomationTransactionJournal,
)
from homeintent.storage_paths import (
    HomeIntentStoragePaths,
    resolve_homeintent_storage_paths,
)


class _Config:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, *parts: str) -> str:
        return str(self.root.joinpath(*parts))


class _Hass:
    def __init__(self, root: Path) -> None:
        self.config = _Config(root)
        self.data: dict[str, object] = {}

    async def async_add_executor_job(self, func, *args):
        return await asyncio.to_thread(func, *args)


def test_resolves_every_storage_path_in_one_synchronous_setup_operation(tmp_path):
    legacy = tmp_path / ".storage" / "ha_nlu_goal_runs.json"
    legacy.parent.mkdir()
    legacy.write_text('{"runs": []}', encoding="utf-8")

    paths = resolve_homeintent_storage_paths(_Hass(tmp_path))

    assert isinstance(paths, HomeIntentStoragePaths)
    assert paths.goal_runs == str(
        tmp_path / ".storage" / "homeintent_goal_runs.json"
    )
    assert Path(paths.goal_runs).read_text(encoding="utf-8") == '{"runs": []}'
    assert paths.automation_metadata == str(
        tmp_path / "homeintent_automation_metadata.json"
    )
    assert paths.agent_events == str(tmp_path / "homeintent_agent_events.json")
    assert paths.automation_history == str(
        tmp_path / ".storage" / "homeintent_automation_history.json"
    )


def test_failed_migration_keeps_using_nonempty_legacy_store(tmp_path):
    legacy = tmp_path / "ha_nlu_agent_events.json"
    legacy.write_text('{"kept": true}', encoding="utf-8")

    with patch("homeintent.storage_migration.os.replace", side_effect=OSError("no")):
        paths = resolve_homeintent_storage_paths(_Hass(tmp_path))

    assert paths.agent_events == str(legacy)
    assert legacy.read_text(encoding="utf-8") == '{"kept": true}'


def test_store_and_executor_constructors_do_not_touch_filesystem(tmp_path):
    hass = _Hass(tmp_path)
    paths = HomeIntentStoragePaths.unresolved(hass)

    with (
        patch("os.path.exists", side_effect=AssertionError("exists on loop")),
        patch("os.path.isdir", side_effect=AssertionError("isdir on loop")),
        patch("os.makedirs", side_effect=AssertionError("makedirs on loop")),
        patch("os.replace", side_effect=AssertionError("replace on loop")),
        patch("builtins.open", side_effect=AssertionError("open on loop")),
    ):
        AutomationMetadataStore(hass, paths.automation_metadata)
        AgentEventStore(hass, paths.agent_events)
        AutomationTransactionJournal(paths.automation_transaction)
        AutomationHistoryStore(paths.automation_history)
        AutomationExecutor(hass, paths)


def test_history_append_if_available_checks_and_writes_in_worker_thread(tmp_path):
    missing = AutomationHistoryStore(str(tmp_path / "missing" / "history.json"))
    assert missing.append_if_available(
        operation="create",
        automation_id="one",
        created_at="2026-10-25T00:00:00+00:00",
        automations=[],
    ) is False

    directory = tmp_path / "ready"
    directory.mkdir()
    ready = AutomationHistoryStore(str(directory / "history.json"))
    assert ready.append_if_available(
        operation="create",
        automation_id="one",
        created_at="2026-10-25T00:00:00+00:00",
        automations=[],
    ) is True
    assert len(ready.read()) == 1


def test_setup_and_request_storage_io_never_runs_on_event_loop(tmp_path):
    """Every filesystem primitive is either setup-batched or executor-owned."""

    legacy = tmp_path / "ha_nlu_agent_events.json"
    legacy.write_text("{}", encoding="utf-8")
    storage_directory = tmp_path / ".storage"
    storage_directory.mkdir()
    (storage_directory / "ha_nlu_goal_runs.json").write_text(
        '{"runs": []}', encoding="utf-8"
    )
    loop_thread = threading.get_ident()
    seen: set[str] = set()

    def guarded(name, original):
        @wraps(original)
        def wrapper(*args, **kwargs):
            assert threading.get_ident() != loop_thread, f"{name} ran on event loop"
            seen.add(name)
            return original(*args, **kwargs)

        return wrapper

    async def scenario() -> None:
        hass = _Hass(tmp_path)
        # Avoid counting the executor's own lazy bootstrap as product I/O.
        await hass.async_add_executor_job(lambda: None)
        targets = (
            (builtins, "open"),
            (os.path, "exists"),
            (os.path, "isdir"),
            (os, "makedirs"),
            (os, "replace"),
            (os, "unlink"),
            (os, "fsync"),
            (tempfile, "mkstemp"),
        )
        patches = [
            patch.object(owner, name, guarded(name, getattr(owner, name)))
            for owner, name in targets
        ]
        for active_patch in patches:
            active_patch.start()
        try:
            paths = await hass.async_add_executor_job(
                resolve_homeintent_storage_paths, hass
            )
            metadata_store = AutomationMetadataStore(
                hass, paths.automation_metadata
            )
            events = AgentEventStore(hass, paths.agent_events)
            journal = AutomationTransactionJournal(paths.automation_transaction)
            history = AutomationHistoryStore(paths.automation_history)

            metadata = AutomationMetadata(
                automation_id="one",
                source_text="Wenn es dunkel ist, schalte das Licht ein",
                created_at="2026-10-25T00:00:00+00:00",
            )
            await metadata_store.async_save(metadata)
            assert (await metadata_store.async_load_all())["one"]["version"] == 1
            _, file_digest = await metadata_store.async_snapshot()
            entry, entry_digest = await metadata_store.async_entry_snapshot("one")
            assert entry is not None
            await metadata_store.async_update("one", run_count=1)
            current, current_digest = await metadata_store.async_entry_snapshot("one")
            assert current is not None
            replacement = {**current, "run_count": 2}
            await metadata_store.async_replace_entry_if_unchanged(
                "one", replacement, current_digest
            )
            replacement_two = {**replacement, "run_count": 3}
            await metadata_store.async_replace_entries_if_unchanged(
                {"one": replacement_two},
                {"one": canonical_state_digest(replacement)},
            )
            await metadata_store.async_replace_all({"one": replacement_two})
            _, latest_digest = await metadata_store.async_snapshot()
            await metadata_store.async_replace_all_if_unchanged(
                {"one": replacement_two}, latest_digest
            )
            await metadata_store.async_delete("one")
            assert await metadata_store.async_load_all() == {}
            assert file_digest.startswith("sha256:")
            assert entry_digest.startswith("sha256:")

            event = AgentEvent(
                event_id="event-one",
                rule_id="rule-one",
                dedupe_key="dedupe-one",
                title="HomeIntent",
                message="Hinweis",
                mode=AgentMode.INFORM,
                state=AgentEventState.ACTIVE,
                created_at="2026-10-25T00:00:00+00:00",
                updated_at="2026-10-25T00:00:00+00:00",
                expires_at="2026-10-25T01:00:00+00:00",
            )
            await events.async_save(event)
            assert (await events.async_load_all())[event.event_id] == event
            await events.async_delete(event.event_id)
            assert await events.async_load_all() == {}

            transaction = AutomationTransaction(
                operation="create",
                automation_id="one",
                created_at="2026-10-25T00:00:00+00:00",
                before_digest="missing",
                after_digest=None,
                before_automations=[],
                after_automations=[],
            )
            await hass.async_add_executor_job(journal.write, transaction)
            recovered = await hass.async_add_executor_job(journal.read)
            assert recovered is not None
            assert recovered.automation_id == transaction.automation_id
            await hass.async_add_executor_job(journal.clear)

            appended = await hass.async_add_executor_job(
                partial(
                    history.append_if_available,
                    operation="create",
                    automation_id="one",
                    created_at="2026-10-25T00:00:00+00:00",
                    automations=[],
                ),
            )
            assert appended is True
            assert await hass.async_add_executor_job(
                partial(
                    history.finalize_if_available,
                    "2026-10-25T00:00:00+00:00",
                    post_metadata_digest="sha256:done",
                ),
            )
            assert await hass.async_add_executor_job(
                history.pop_if_available, "2026-10-25T00:00:00+00:00"
            )
        finally:
            for active_patch in reversed(patches):
                active_patch.stop()

    asyncio.run(scenario())

    assert {"open", "exists", "isdir", "makedirs", "replace", "unlink"} <= seen
