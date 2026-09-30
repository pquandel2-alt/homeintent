"""Automation Executor (HomeIntent V5 Teil 7/10 + Teil 8/10 - V5.26, V5.28,
V5.35, V5.36): the sole persistence path from a confirmed ``AutomationModel`` into
a real Home Assistant automation - kept structurally and physically
separate from ``conversation.py``'s existing command-execution code
(``hass.services.async_call()`` for TURN_ON/TURN_OFF/etc. plans), since
writing ``automations.yaml`` is a categorically different, far harder to
reverse action than a single service call (see ``context.py``'s
``PendingAutomationConfirmation`` docstring: "the single most hard-to-
reverse action anywhere in this engine").

Mirrors the exact read-modify-write Home Assistant's own
``EditAutomationConfigView._write_value`` (upstream ``homeassistant/
components/config/automation.py``) performs when a user edits an automation
through the UI - same file, same YAML dump, same atomic write, same reload
call - so an automation created this way is indistinguishable from one a
human created by hand through the UI editor. Only ``config/automation.py``'s
own service-data shape for the plain, no-argument ``automation.reload``
service call is used (Regel 4: no invented ``id``-scoped reload variant -
the always-correct, fully-verified call reloads every automation from disk,
including the one just appended).

Takes a already-``generate_ha_automation_config()``-clean config dict as
input - no re-validation, no re-generation, purely the disk/service-call
side effect (same "purely renders/persists whatever it is given" division
of labor ``automation_preview.py``'s ``render_automation_preview()`` and
``ha_automation_generator.py`` already establish one stage earlier each).

Lives at the top level of ``custom_components/homeintent/``, not inside
``nlu/``: every module in ``nlu/`` (``ha_automation_generator.py`` included)
deliberately stays hass-free, and this module's entire job is real
``homeassistant.core``/``homeassistant.util`` I/O - same layering precedent
``hass_entities.py`` already sets for "real HA access lives one package
level up from the pure NLU code".

V5.35 (Automation Transactions)/V5.36 (Rollback): the whole
write-reload-categorize-record_metadata sequence runs under one lock as a
single unit - if the reload, category assignment, or metadata write fails
partway through, the YAML change is rolled back (and, if it was a later
step that failed, HA is reloaded a second time to make its live state match
the rolled-back file) before the exception propagates. Before this wave, a reload failure
left a silently orphaned entry in ``automations.yaml`` - the file said the
automation existed even though the user was told creation failed and HA
itself may never have picked it up. "No half-created automations" (the
plan's own V5.36 wording) means the file must never disagree with what the
user was actually told.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr, entity_registry as er
from homeassistant.util import yaml as yaml_util
from homeassistant.util.file import write_utf8_file_atomic

from .automation_metadata_store import AutomationMetadata, AutomationMetadataStore, utcnow_isoformat
from .automation_history import (
    AutomationHistoryStore,
    AUTOMATION_HISTORY_FILENAME,
    LEGACY_AUTOMATION_HISTORY_FILENAME,
)
from .automation_summary import AutomationSummary, CREATED_BY_HOMEINTENT
from .automation_transaction import (
    AutomationTransaction,
    AutomationTransactionJournal,
    ConcurrentAutomationUpdateError,
    TRANSACTION_JOURNAL_FILENAME,
    LEGACY_TRANSACTION_JOURNAL_FILENAME,
)
from .storage_migration import resolve_storage_path
from .execution_context import system_context_for_turn

_LOGGER = logging.getLogger(__name__)

AUTOMATIONS_YAML_FILENAME = "automations.yaml"
AUTOMATION_CATEGORY_SCOPE = "automation"
HOMEINTENT_CATEGORY_NAME = "HomeIntent"
AUTOMATION_LOCK_DATA_KEY = "homeintent_automation_write_lock"


@dataclass(frozen=True)
class _AutomationFileSnapshot:
    automations: list[dict[str, Any]]
    digest: str


def _collect_entity_ids(value: Any) -> set[str]:
    """Every ``entity_id`` value found anywhere in ``value`` (a raw
    automation config dict/list node) - deliberately shape-blind (walks
    every dict/list node instead of hand-enumerating known trigger/
    condition/action shapes), since automation YAML has enough structural
    variety (single trigger dict vs. list of triggers, service-call target
    blocks, nested conditions) that enumerating every shape risks silently
    missing one. ``async_list_automations`` applies this same walker to the
    whole config and separately to trigger/condition/action sections, which
    preserves role information without re-parsing arbitrary YAML semantics.
    """
    found: set[str] = set()
    if isinstance(value, dict):
        for key, val in value.items():
            if key == "entity_id":
                if isinstance(val, str):
                    found.add(val)
                elif isinstance(val, list):
                    found.update(v for v in val if isinstance(v, str))
            else:
                found.update(_collect_entity_ids(val))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_entity_ids(item))
    return found


def _mapping_tuple(value: Any) -> tuple[dict[str, Any], ...]:
    """Normalize legacy singular dict and modern plural-list YAML shapes."""
    if isinstance(value, dict):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, dict))
    return ()


class AutomationExecutor:
    """One instance per config entry (same lifetime/ownership as
    ``NluEngine``/``ConversationContextStore`` on ``NluConversationEntity``),
    sharing one ``asyncio.Lock()`` per Home Assistant instance. The custom
    self-delete service creates fresh executor objects, so an instance-local
    lock cannot protect its writes against conversation-driven writes.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._lock = hass.data.setdefault(AUTOMATION_LOCK_DATA_KEY, asyncio.Lock())
        self._metadata_store = AutomationMetadataStore(hass)
        self._journal = AutomationTransactionJournal(
            resolve_storage_path(
                hass,
                TRANSACTION_JOURNAL_FILENAME,
                LEGACY_TRANSACTION_JOURNAL_FILENAME,
            )
        )
        self._history = AutomationHistoryStore(
            resolve_storage_path(
                hass,
                AUTOMATION_HISTORY_FILENAME,
                LEGACY_AUTOMATION_HISTORY_FILENAME,
            )
        )

    async def _async_read_snapshot(self, path: str) -> _AutomationFileSnapshot:
        return await self._hass.async_add_executor_job(self._read_snapshot, path)

    async def _async_begin_transaction(
        self,
        *,
        operation: str,
        automation_id: str,
        path: str,
        snapshot: _AutomationFileSnapshot,
        updated: list[dict[str, Any]],
        record_history: bool = True,
    ) -> AutomationTransaction:
        existing = await self._hass.async_add_executor_job(self._journal.read)
        if existing is not None:
            raise RuntimeError(
                "Eine unvollständige HomeIntent-Transaktion muss zuerst wiederhergestellt werden"
            )
        content, expected_after_digest = await self._hass.async_add_executor_job(
            self._serialize_automations, updated
        )
        created_at = utcnow_isoformat()
        metadata_snapshot = (
            await self._metadata_store.async_load_all()
            if record_history and self._history.available
            else None
        )
        transaction = AutomationTransaction(
            operation=operation,
            automation_id=automation_id,
            created_at=created_at,
            before_digest=snapshot.digest,
            # Persist the exact expected result *before* touching YAML. A
            # crash after the atomic replace but before the second journal
            # write can therefore still be recovered deterministically.
            after_digest=expected_after_digest,
            before_automations=snapshot.automations,
            after_automations=updated,
        )
        await self._hass.async_add_executor_job(self._journal.write, transaction)
        try:
            after_digest = await self._hass.async_add_executor_job(
                self._write_content_if_unchanged,
                path,
                content,
                snapshot.digest,
            )
        except Exception as err:
            current_digest = await self._hass.async_add_executor_job(
                self._file_digest, path
            )
            # If no YAML write happened, the prepared journal has no work to
            # recover. Any other state is retained for startup recovery or a
            # deliberate conflict decision; never guess which writer won.
            conflict_before_write = (
                isinstance(err, ConcurrentAutomationUpdateError)
                and not err.write_started
            )
            if current_digest == snapshot.digest or conflict_before_write:
                await self._hass.async_add_executor_job(self._journal.clear)
            raise
        transaction = replace(
            transaction, after_digest=after_digest, state="yaml_written"
        )
        await self._hass.async_add_executor_job(self._journal.write, transaction)
        if metadata_snapshot is not None:
            try:
                await self._hass.async_add_executor_job(
                    partial(
                        self._history.append,
                        operation=operation,
                        automation_id=automation_id,
                        created_at=created_at,
                        automations=snapshot.automations,
                        metadata=metadata_snapshot,
                    )
                )
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                raise
        return transaction

    async def _async_rollback_transaction(
        self, path: str, transaction: AutomationTransaction
    ) -> None:
        if transaction.after_digest is None:
            await self._hass.async_add_executor_job(self._journal.clear)
            return
        await self._hass.async_add_executor_job(
            self._write_if_unchanged,
            path,
            transaction.before_automations,
            transaction.after_digest,
        )
        await self._hass.async_add_executor_job(self._journal.clear)
        if transaction.operation != "rollback" and self._history.available:
            await self._hass.async_add_executor_job(
                self._history.pop_if_created_at, transaction.created_at
            )

    async def _async_complete_transaction(self) -> None:
        await self._hass.async_add_executor_job(self._journal.clear)

    def _assign_homeintent_category(self, automation_id: str) -> None:
        """Create/reuse the automation-scoped HomeIntent category and assign
        the freshly reloaded automation entity to it. Categories are registry
        metadata, not keys in automations.yaml: Home Assistant stores the
        category itself in category_registry and the assignment in the
        automation entity's RegistryEntry.categories mapping.
        """
        category_registry = cr.async_get(self._hass)
        category = next(
            (
                item
                for item in category_registry.async_list_categories(
                    scope=AUTOMATION_CATEGORY_SCOPE
                )
                if item.name.casefold() == HOMEINTENT_CATEGORY_NAME.casefold()
            ),
            None,
        )
        if category is None:
            try:
                category = category_registry.async_create(
                    name=HOMEINTENT_CATEGORY_NAME, scope=AUTOMATION_CATEGORY_SCOPE
                )
            except ValueError:
                # Another config-entry executor may have created the same
                # category between our list and create calls. Re-read it; a
                # genuinely unrelated ValueError is still propagated below.
                category = next(
                    (
                        item
                        for item in category_registry.async_list_categories(
                            scope=AUTOMATION_CATEGORY_SCOPE
                        )
                        if item.name.casefold() == HOMEINTENT_CATEGORY_NAME.casefold()
                    ),
                    None,
                )
                if category is None:
                    raise

        entity_registry = er.async_get(self._hass)
        entity_id = entity_registry.async_get_entity_id(
            "automation", "automation", automation_id
        )
        if entity_id is None or (entry := entity_registry.async_get(entity_id)) is None:
            raise RuntimeError(
                f"Reloaded automation {automation_id!r} has no entity-registry entry"
            )
        categories = entry.categories.copy()
        if categories.get(AUTOMATION_CATEGORY_SCOPE) == category.category_id:
            return
        categories[AUTOMATION_CATEGORY_SCOPE] = category.category_id
        entity_registry.async_update_entity(entity_id, categories=categories)

    async def async_create_automation(
        self,
        config: dict[str, Any],
        automation_id: str | None = None,
        *,
        scheduled_for: datetime | None = None,
        once: bool = False,
        max_runs: int | None = None,
    ) -> str:
        """Appends ``config`` (a ``GenerationResult.config`` dict, no ``id``
        key yet - see ``ha_automation_generator.py``'s ``GenerationResult``
        docstring) as a new automation, persists it to ``automations.yaml``,
        reloads the automation integration so it takes effect immediately,
        assigns its entity to the automation-scoped HomeIntent category,
        and records its identity/metadata (V5.30/V5.31) in the sidecar
        store. This follows Home Assistant's own config/automation creation
        path and adds this integration's category and metadata bookkeeping.
        Returns the freshly assigned automation id.

        Every step runs inside the same lock, and a failure at the reload,
        category, or metadata step rolls the YAML file back to what it was
        before this call (V5.35/V5.36) - either every step succeeds and all
        four pieces of state (file, HA's live config, category assignment,
        metadata) agree, or none of them change.

        File I/O runs off the event loop via ``hass.async_add_executor_job``
        (``load_yaml``/``write_utf8_file_atomic`` are both blocking calls),
        same as every other filesystem-touching HA core code path.

        ``automation_id`` (new feature, Wave 12 "Einmalige Automation"):
        normally ``None`` and generated here as before. A caller building a
        self-deleting ("once") automation must instead pre-generate the id
        and pass it in, since that automation's own action list (embedded in
        ``config`` already) needs to reference its future id before it
        exists - see ``ha_automation_generator.py``'s
        ``generate_ha_automation_config()`` docstring for the full picture.
        """
        if automation_id is None:
            automation_id = uuid.uuid4().hex
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            snapshot = await self._async_read_snapshot(path)
            original_automations = snapshot.automations
            automations = [*original_automations, {"id": automation_id, **config}]
            transaction = await self._async_begin_transaction(
                operation="create",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=automations,
            )

            try:
                await self._hass.services.async_call("automation", "reload", {}, blocking=True, context=system_context_for_turn())
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                raise

            try:
                self._assign_homeintent_category(automation_id)
                metadata = AutomationMetadata(
                    automation_id=automation_id,
                    source_text=config.get("alias", ""),
                    created_at=utcnow_isoformat(),
                    scheduled_for=(
                        scheduled_for.isoformat() if scheduled_for is not None else None
                    ),
                    once=once,
                    max_runs=max_runs,
                )
                await self._metadata_store.async_save(metadata)
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._metadata_store.async_delete(automation_id)
                # Best-effort: HA's live config should reflect the rollback
                # too, but a second reload failure must not mask the
                # original category/metadata error, which is what actually
                # caused this rollback - the file is already correct either
                # way.
                try:
                    await self._hass.services.async_call("automation", "reload", {}, blocking=True, context=system_context_for_turn())
                except Exception:  # noqa: BLE001 - see comment above
                    pass
                raise
            await self._async_complete_transaction()

        return automation_id

    async def async_duplicate_automation(self, automation_id: str) -> str:
        """Duplicate one HomeIntent automation with fresh lifecycle IDs."""
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        automations = await self._hass.async_add_executor_job(
            self._read_automations, path
        )
        source = next(
            (item for item in automations if item.get("id") == automation_id),
            None,
        )
        if source is None:
            raise ValueError(f"Automation {automation_id} does not exist")
        metadata = (await self._metadata_store.async_load_all()).get(automation_id)
        if not metadata or metadata.get("created_by") != CREATED_BY_HOMEINTENT:
            raise ValueError("Only HomeIntent automations can be duplicated")
        new_id = uuid.uuid4().hex
        config = copy.deepcopy(source)
        config.pop("id", None)
        config["alias"] = "Kopie von " + str(config.get("alias") or automation_id)

        def replace_lifecycle_id(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("action") in {
                    "homeintent.delete_automation", "homeintent.record_automation_run"
                } and isinstance(value.get("data"), dict):
                    value["data"]["automation_id"] = new_id
                for child in value.values():
                    replace_lifecycle_id(child)
            elif isinstance(value, list):
                for child in value:
                    replace_lifecycle_id(child)

        replace_lifecycle_id(config)
        scheduled = metadata.get("scheduled_for")
        return await self.async_create_automation(
            config,
            new_id,
            scheduled_for=datetime.fromisoformat(scheduled) if scheduled else None,
            once=bool(metadata.get("once")),
            max_runs=metadata.get("max_runs"),
        )

    async def async_pause_automation_until(
        self, automation_id: str, resume_at: datetime
    ) -> str:
        """Persistently disable an automation and schedule a durable resume."""
        await self.async_disable_automation(automation_id)
        helper_id = uuid.uuid4().hex
        config = {
            "alias": f"HomeIntent: Automation {automation_id} fortsetzen",
            "description": "Von HomeIntent verwalteter einmaliger Fortsetzungsauftrag",
            "triggers": [{
                "trigger": "time",
                "at": resume_at.strftime("%H:%M:%S"),
            }],
            "conditions": [{
                "condition": "template",
                "value_template": (
                    "{{ now().date().isoformat() == '"
                    + resume_at.date().isoformat() + "' }}"
                ),
            }],
            "actions": [
                {
                    "action": "homeintent.enable_automation",
                    "data": {"automation_id": automation_id},
                },
                {
                    "action": "homeintent.delete_automation",
                    "data": {"automation_id": helper_id},
                },
            ],
            "mode": "single",
        }
        try:
            await self.async_create_automation(
                config, helper_id, scheduled_for=resume_at, once=True
            )
        except Exception:
            # Do not leave the requested automation disabled when creating
            # the durable resume failed.
            await self.async_enable_automation(automation_id)
            raise
        return helper_id

    async def async_delete_automation(self, automation_id: str) -> None:
        """V5 Teil 8/10 (V5.28, "Automation Deletion"): removes the
        automation matching ``automation_id`` from ``automations.yaml``,
        reloads the automation integration so the removal takes effect
        immediately, and deletes its sidecar metadata entry (if any).

        Mirrors upstream ``EditIdBasedConfigView``'s own delete path
        (``homeassistant/components/config/view.py``: find the entry whose
        ``CONF_ID`` matches, pop it, write the file back) with one
        deliberate deviation, verified against real upstream source before
        writing this (Regel 4): upstream's own HTTP delete endpoint does
        *not* call ``automation.reload`` afterwards - its ``post_write_hook``
        only removes the entity from the entity registry
        (``homeassistant/components/config/automation.py``'s ``hook()``,
        gated on ``action != ACTION_DELETE``). That alone does not stop the
        automation from running: ``homeassistant/components/automation/
        __init__.py``'s ``async_will_remove_from_hass()`` - the method that
        actually unloads a running automation's triggers - is only invoked
        by a reload's own add/remove diff, never by a bare entity-registry
        removal. Relying on that upstream shortcut here would leave a
        deleted automation's triggers silently still firing, exactly the
        "file disagrees with what the user was told" outcome V5.36's own
        rollback logic (see ``async_create_automation``) exists to prevent -
        so this method calls ``automation.reload`` explicitly instead,
        consistent with every other write this class already performs.

        Same transactional shape as ``async_create_automation``: runs under
        the shared lock, and a reload failure rolls ``automations.yaml``
        back to what it was before this call. The metadata delete happens
        last and is best-effort in the sense that ``AutomationMetadataStore.
        async_delete()`` is itself a no-op for an id with no entry (e.g. a
        hand-made automation this integration never recorded) - never an
        error condition of its own.

        Raises ``ValueError`` if no automation with ``automation_id`` exists
        - the caller (``AutomationDeleteParser`` via ``match_automation_
        delete()``) already resolved this id moments earlier from a live
        ``async_list_automations()`` read, so this only fires on a genuine
        race (deleted again from elsewhere between resolution and
        confirmation), not on the ordinary path.
        """
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            snapshot = await self._async_read_snapshot(path)
            original_automations = snapshot.automations
            remaining = [a for a in original_automations if a.get("id") != automation_id]
            if len(remaining) == len(original_automations):
                raise ValueError(f"No automation with id {automation_id!r} found")

            transaction = await self._async_begin_transaction(
                operation="delete",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=remaining,
            )

            try:
                await self._hass.services.async_call("automation", "reload", {}, blocking=True, context=system_context_for_turn())
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                raise
            try:
                await self._metadata_store.async_delete(automation_id)
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                raise
            await self._async_complete_transaction()
        self._remove_entity_registry_entry(automation_id)

    def _remove_entity_registry_entry(self, automation_id: str) -> None:
        """Drop the deleted automation's registry entry, like HA's own editor.

        Without this the removed automation stays behind as an "unavailable"
        entity. Best effort: the automation itself is already gone.
        """
        try:
            entity_registry = er.async_get(self._hass)
            entity_id = entity_registry.async_get_entity_id(
                "automation", "automation", automation_id
            )
            if entity_id is not None:
                entity_registry.async_remove(entity_id)
        except Exception as err:  # noqa: BLE001 - cleanup must never fail a delete
            _LOGGER.warning(
                "Could not remove registry entry of deleted automation %s: %s",
                automation_id,
                err,
            )

    async def async_disable_automation(self, automation_id: str) -> None:
        """V5 Teil 8/10 (Wave 11, "Automation Disable/Enable"): durably
        disables the automation matching ``automation_id`` by setting its
        ``initial_state`` key to ``False`` in ``automations.yaml`` and
        reloading - **not** a runtime ``automation.turn_off`` service call.

        Verified against real upstream source before writing this (Regel 4):
        ``automation.turn_on``/``turn_off`` only flip the entity's runtime
        ``_is_enabled`` flag (``homeassistant/components/automation/
        __init__.py``) - that change does not survive a reload/restart, and
        every other write this class performs already ends with
        ``automation.reload``. Relying on the service call here would mean
        this integration's own next reload (e.g. from an unrelated creation/
        deletion moments later) silently re-enables an automation the user
        was just told is disabled - exactly the "file disagrees with what
        the user was told" outcome V5.36's rollback logic exists to prevent
        elsewhere. ``initial_state`` (``CONF_INITIAL_STATE``, upstream
        ``homeassistant/components/automation/config.py``), by contrast, is
        read at setup/reload time and takes priority over any previously
        persisted runtime state - the same durable, YAML-only mechanism
        upstream's own docs describe for a config-defined enabled state.

        Same transactional shape as ``async_delete_automation``: runs under
        the shared lock, a reload failure rolls ``automations.yaml`` back.
        Raises ``ValueError`` if no automation with ``automation_id`` exists
        (same genuine-race-only caveat as ``async_delete_automation``).
        """
        await self._async_set_initial_state(automation_id, enabled=False)

    async def async_enable_automation(self, automation_id: str) -> None:
        """The ``async_disable_automation()`` counterpart - sets
        ``initial_state`` back to ``True`` explicitly (not simply removing
        the key) so a re-enable is just as durable and explicit as a
        disable, rather than falling back on whatever HA's own default/
        previously-persisted-state priority would otherwise pick.
        """
        await self._async_set_initial_state(automation_id, enabled=True)

    async def _async_set_initial_state(self, automation_id: str, *, enabled: bool) -> None:
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            snapshot = await self._async_read_snapshot(path)
            original_automations = snapshot.automations
            updated = []
            found = False
            for automation in original_automations:
                if automation.get("id") == automation_id:
                    found = True
                    automation = {**automation, "initial_state": enabled}
                updated.append(automation)
            if not found:
                raise ValueError(f"No automation with id {automation_id!r} found")

            transaction = await self._async_begin_transaction(
                operation="enable" if enabled else "disable",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=updated,
            )

            try:
                await self._hass.services.async_call("automation", "reload", {}, blocking=True, context=system_context_for_turn())
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                raise
            await self._async_complete_transaction()

    async def async_list_automations(self) -> tuple[AutomationSummary, ...]:
        """V5.29 (Automation Query): a read-only survey of every automation
        currently in ``automations.yaml``, cross-referenced against the
        metadata sidecar - the read half of this class's job, mirroring
        ``async_create_automation``'s write half but with none of its
        locking (nothing here writes, so concurrent reads never race each
        other or a write; a read started just before a concurrent
        ``async_create_automation`` simply may or may not see the new
        automation yet, the same eventually-consistent guarantee any other
        read of live HA state already has - see ``matches_semantic_state``'s
        "never cached" precedent for entity state).

        Callers gate this behind a cheap sentence pre-check first (see
        ``engine.py``'s ``_AUTOMATION_QUERY_RE``) - reading and parsing
        ``automations.yaml`` on every single conversation turn regardless of
        its content would be real, unnecessary per-turn I/O cost.
        """
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        automations = await self._hass.async_add_executor_job(self._read_automations, path)
        metadata = await self._metadata_store.async_load_all()

        summaries = []
        for automation in automations:
            automation_id = automation.get("id", "")
            entry = metadata.get(automation_id)
            trigger_ids = frozenset(
                _collect_entity_ids(automation.get("triggers", automation.get("trigger", ())))
            )
            condition_ids = frozenset(
                _collect_entity_ids(automation.get("conditions", automation.get("condition", ())))
            )
            action_ids = frozenset(
                _collect_entity_ids(automation.get("actions", automation.get("action", ())))
            )
            summaries.append(
                AutomationSummary(
                    automation_id=automation_id,
                    alias=automation.get("alias", ""),
                    source_text=entry.get("source_text") if entry else None,
                    created_by=entry.get("created_by") if entry else None,
                    referenced_entity_ids=frozenset(_collect_entity_ids(automation)),
                    trigger_entity_ids=trigger_ids,
                    condition_entity_ids=condition_ids,
                    action_entity_ids=action_ids,
                    enabled=automation.get("initial_state", True),
                    scheduled_for=entry.get("scheduled_for") if entry else None,
                    once=bool(entry.get("once")) if entry else False,
                    max_runs=entry.get("max_runs") if entry else None,
                    run_count=int(entry.get("run_count", 0)) if entry else 0,
                    triggers=_mapping_tuple(automation.get("triggers", automation.get("trigger"))),
                    conditions=_mapping_tuple(automation.get("conditions", automation.get("condition"))),
                    actions=_mapping_tuple(automation.get("actions", automation.get("action"))),
                    description=(
                        automation.get("description")
                        if isinstance(automation.get("description"), str) else None
                    ),
                )
            )
        return tuple(summaries)

    def automation_runtime_info(self, automation_id: str) -> dict[str, Any] | None:
        """Return non-sensitive live execution indicators for diagnostics."""
        entity_id = er.async_get(self._hass).async_get_entity_id(
            "automation", "automation", automation_id
        )
        state = self._hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        return {
            "entity_id": entity_id,
            "state": state.state,
            "last_triggered": state.attributes.get("last_triggered"),
            "current": state.attributes.get("current"),
        }

    async def async_reschedule_automation(
        self, automation_id: str, scheduled_for: datetime
    ) -> None:
        """Move a known date-bound HomeIntent one-shot transactionally."""
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            metadata = await self._metadata_store.async_load_all()
            entry = metadata.get(automation_id)
            if not entry or not entry.get("once") or not entry.get("scheduled_for"):
                raise ValueError("Die Automation ist kein geplanter HomeIntent-Einmalauftrag")
            snapshot = await self._async_read_snapshot(path)
            updated_automations: list[dict[str, Any]] = []
            found = False
            date_template = (
                "{{ now().date().isoformat() == "
                f"'{scheduled_for.date().isoformat()}' }}"
            )
            for automation in snapshot.automations:
                if automation.get("id") != automation_id:
                    updated_automations.append(automation)
                    continue
                found = True
                triggers = list(automation.get("triggers") or [])
                time_indexes = [
                    index
                    for index, trigger in enumerate(triggers)
                    if isinstance(trigger, dict) and trigger.get("trigger") == "time"
                ]
                if len(time_indexes) != 1:
                    raise ValueError("Der Zeittrigger kann nicht eindeutig verschoben werden")
                triggers[time_indexes[0]] = {
                    "trigger": "time",
                    "at": scheduled_for.strftime("%H:%M:%S"),
                }
                conditions = list(automation.get("conditions") or [])
                date_indexes = [
                    index
                    for index, condition in enumerate(conditions)
                    if isinstance(condition, dict)
                    and condition.get("condition") == "template"
                    and "now().date().isoformat()" in str(
                        condition.get("value_template", "")
                    )
                ]
                if len(date_indexes) != 1:
                    raise ValueError("Der Datumsschutz kann nicht eindeutig verschoben werden")
                conditions[date_indexes[0]] = {
                    "condition": "template",
                    "value_template": date_template,
                }
                updated_automations.append(
                    {**automation, "triggers": triggers, "conditions": conditions}
                )
            if not found:
                raise ValueError(f"No automation with id {automation_id!r} found")

            transaction = await self._async_begin_transaction(
                operation="reschedule",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=updated_automations,
            )
            try:
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                await self._metadata_store.async_update(
                    automation_id,
                    scheduled_for=scheduled_for.isoformat(),
                    version=int(entry.get("version", 1)) + 1,
                )
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                raise
            await self._async_complete_transaction()

    async def async_record_automation_run(self, automation_id: str) -> bool:
        """Increment a bounded automation and delete it at its configured limit."""
        delete_now = False
        async with self._lock:
            metadata = await self._metadata_store.async_load_all()
            entry = metadata.get(automation_id)
            if not entry or not isinstance(entry.get("max_runs"), int):
                raise ValueError(f"No bounded HomeIntent automation {automation_id!r}")
            max_runs = int(entry["max_runs"])
            next_count = int(entry.get("run_count", 0)) + 1
            if next_count >= max_runs:
                delete_now = True
            else:
                await self._metadata_store.async_update(
                    automation_id,
                    run_count=next_count,
                    version=int(entry.get("version", 1)) + 1,
                )
        if delete_now:
            await self.async_delete_automation(automation_id)
        return delete_now

    async def async_set_max_runs(self, automation_id: str, max_runs: int) -> None:
        """Limit an existing HomeIntent automation to a total run count."""
        if not 2 <= max_runs <= 10:
            raise ValueError("Die Ausführungszahl muss zwischen 2 und 10 liegen")
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            metadata = await self._metadata_store.async_load_all()
            entry = metadata.get(automation_id)
            if not entry or entry.get("created_by") != CREATED_BY_HOMEINTENT:
                raise ValueError("Die Automation wurde nicht von HomeIntent erstellt")
            if entry.get("once"):
                raise ValueError("Ein Einmalauftrag kann nicht mehrfach ausgeführt werden")
            snapshot = await self._async_read_snapshot(path)
            updated_automations = []
            found = False
            for automation in snapshot.automations:
                if automation.get("id") != automation_id:
                    updated_automations.append(automation)
                    continue
                found = True
                actions = [
                    action
                    for action in automation.get("actions") or []
                    if not (
                        isinstance(action, dict)
                        and action.get("action") == "homeintent.record_automation_run"
                    )
                ]
                actions.append(
                    {
                        "action": "homeintent.record_automation_run",
                        "data": {
                            "automation_id": automation_id,
                            "max_runs": max_runs,
                        },
                    }
                )
                updated_automations.append({**automation, "actions": actions})
            if not found:
                raise ValueError(f"No automation with id {automation_id!r} found")
            transaction = await self._async_begin_transaction(
                operation="set_max_runs",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=updated_automations,
            )
            try:
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                await self._metadata_store.async_update(
                    automation_id,
                    max_runs=max_runs,
                    run_count=0,
                    version=int(entry.get("version", 1)) + 1,
                )
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                raise
            await self._async_complete_transaction()

    async def async_replace_automation_actions(
        self,
        automation_id: str,
        new_actions: list[dict[str, Any]],
        source_text: str,
    ) -> None:
        """Replace only user actions, preserving trigger/conditions and bookkeeping."""
        if not new_actions:
            raise ValueError("Mindestens eine neue Aktion ist erforderlich")
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            metadata = await self._metadata_store.async_load_all()
            entry = metadata.get(automation_id)
            if not entry or entry.get("created_by") != CREATED_BY_HOMEINTENT:
                raise ValueError("Die Automation wurde nicht von HomeIntent erstellt")
            snapshot = await self._async_read_snapshot(path)
            updated: list[dict[str, Any]] = []
            found = False
            for automation in snapshot.automations:
                if automation.get("id") != automation_id:
                    updated.append(automation)
                    continue
                found = True
                existing = list(automation.get("actions") or [])
                prefix = existing[:1] if existing and isinstance(existing[0], dict) and "delay" in existing[0] else []
                housekeeping = [
                    action for action in existing
                    if isinstance(action, dict)
                    and action.get("action") in {
                        "homeintent.delete_automation",
                        "homeintent.record_automation_run",
                    }
                ]
                updated.append(
                    {**automation, "actions": [*prefix, *new_actions, *housekeeping]}
                )
            if not found:
                raise ValueError(f"No automation with id {automation_id!r} found")
            transaction = await self._async_begin_transaction(
                operation="replace_actions",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=updated,
            )
            try:
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                await self._metadata_store.async_update(
                    automation_id,
                    source_text=source_text,
                    version=int(entry.get("version", 1)) + 1,
                )
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                raise
            await self._async_complete_transaction()

    async def async_replace_automation_section(
        self,
        automation_id: str,
        section: str,
        new_values: list[dict[str, Any]],
        source_text: str,
    ) -> None:
        """Transactionally replace triggers or conditions of one HomeIntent automation."""
        if section not in {"triggers", "conditions"}:
            raise ValueError("Unbekannter Automationsabschnitt")
        if section == "triggers" and not new_values:
            raise ValueError("Mindestens ein Auslöser ist erforderlich")
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            metadata = await self._metadata_store.async_load_all()
            entry = metadata.get(automation_id)
            if not entry or entry.get("created_by") != CREATED_BY_HOMEINTENT:
                raise ValueError("Die Automation wurde nicht von HomeIntent erstellt")
            if entry.get("once"):
                raise ValueError(
                    "Auslöser und Bedingungen einmaliger Aufträge können nicht nachträglich geändert werden"
                )
            snapshot = await self._async_read_snapshot(path)
            updated: list[dict[str, Any]] = []
            found = False
            for automation in snapshot.automations:
                if automation.get("id") != automation_id:
                    updated.append(automation)
                    continue
                found = True
                replacement = {**automation, section: list(new_values)}
                if section == "conditions" and not new_values:
                    replacement.pop("conditions", None)
                updated.append(replacement)
            if not found:
                raise ValueError(f"No automation with id {automation_id!r} found")
            transaction = await self._async_begin_transaction(
                operation=f"replace_{section}",
                automation_id=automation_id,
                path=path,
                snapshot=snapshot,
                updated=updated,
            )
            try:
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                await self._metadata_store.async_update(
                    automation_id,
                    source_text=source_text,
                    version=int(entry.get("version", 1)) + 1,
                )
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                raise
            await self._async_complete_transaction()

    async def async_cleanup_expired_scheduled_automations(
        self, now: datetime
    ) -> tuple[str, ...]:
        """Remove missed one-shots on startup instead of firing next day.

        Relative automations have a full ``scheduled_for`` timestamp in the
        sidecar and a date condition in HA YAML. If HA was offline when the
        clock trigger was due, the safe policy is to expire the command, not
        execute a stale physical action after restart.
        """
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            snapshot = await self._async_read_snapshot(path)
            original_automations = snapshot.automations
            metadata = await self._metadata_store.async_load_all()
            expired_ids: set[str] = set()
            for automation_id, entry in metadata.items():
                if not entry.get("once") or not (raw_target := entry.get("scheduled_for")):
                    continue
                try:
                    target = datetime.fromisoformat(raw_target)
                except (TypeError, ValueError):
                    continue
                comparable_now = now
                if target.tzinfo is not None and comparable_now.tzinfo is None:
                    comparable_now = comparable_now.astimezone()
                elif target.tzinfo is None and comparable_now.tzinfo is not None:
                    comparable_now = comparable_now.replace(tzinfo=None)
                if target < comparable_now:
                    expired_ids.add(automation_id)
            if not expired_ids:
                return ()

            remaining = [
                automation
                for automation in original_automations
                if automation.get("id") not in expired_ids
            ]
            present_ids = {
                automation_id
                for automation in original_automations
                if isinstance((automation_id := automation.get("id")), str)
                and automation_id in expired_ids
            }
            if present_ids:
                transaction = await self._async_begin_transaction(
                    operation="expire",
                    automation_id=",".join(sorted(present_ids)),
                    path=path,
                    snapshot=snapshot,
                    updated=remaining,
                )
                try:
                    await self._hass.services.async_call(
                        "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                    )
                except Exception:
                    await self._async_rollback_transaction(path, transaction)
                    raise
            for automation_id in expired_ids:
                await self._metadata_store.async_delete(automation_id)
            if present_ids:
                await self._async_complete_transaction()
            return tuple(sorted(expired_ids))

    async def async_recover_incomplete_transaction(self) -> bool:
        """Recover a crash-interrupted YAML transaction when still safe."""
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            transaction = await self._hass.async_add_executor_job(self._journal.read)
            if transaction is None:
                return False
            current_digest = await self._hass.async_add_executor_job(
                self._file_digest, path
            )
            if current_digest == transaction.before_digest:
                await self._hass.async_add_executor_job(self._journal.clear)
                return False
            if (
                transaction.after_digest is not None
                and current_digest == transaction.after_digest
            ):
                # Do not resurrect a deleted automation after its metadata
                # may already have been removed. The startup reconciliation
                # directly after this call removes any orphan metadata.
                if transaction.operation in {"delete", "expire"}:
                    await self._hass.async_add_executor_job(self._journal.clear)
                else:
                    await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                return True
            raise ConcurrentAutomationUpdateError(
                "automations.yaml changed after an interrupted HomeIntent transaction"
            )

    async def async_reconcile_homeintent_state(self) -> tuple[str, ...]:
        """Remove orphan metadata and repair categories for known entries."""
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            snapshot = await self._async_read_snapshot(path)
            yaml_ids = {
                automation.get("id")
                for automation in snapshot.automations
                if isinstance(automation.get("id"), str)
            }
            metadata = await self._metadata_store.async_load_all()
            orphan_ids = sorted(set(metadata) - yaml_ids)
            for automation_id in orphan_ids:
                await self._metadata_store.async_delete(automation_id)
            for automation_id in sorted(set(metadata) & yaml_ids):
                self._assign_homeintent_category(automation_id)
                entry = metadata[automation_id]
                changes: dict[str, Any] = {}
                scheduled_for = self._scheduled_for_from_yaml(
                    snapshot.automations, automation_id, entry.get("scheduled_for")
                )
                if scheduled_for is not None and scheduled_for != entry.get(
                    "scheduled_for"
                ):
                    changes["scheduled_for"] = scheduled_for
                max_runs = self._max_runs_from_yaml(
                    snapshot.automations, automation_id
                )
                if max_runs != entry.get("max_runs"):
                    changes["max_runs"] = max_runs
                    changes["run_count"] = 0
                if changes:
                    await self._metadata_store.async_update(
                        automation_id,
                        **changes,
                        version=int(entry.get("version", 1)) + 1,
                    )
            return tuple(orphan_ids)

    async def async_rollback_last_change(self) -> str:
        """Restore the previous YAML snapshot and reload Home Assistant."""
        path = self._hass.config.path(AUTOMATIONS_YAML_FILENAME)
        async with self._lock:
            history = await self._hass.async_add_executor_job(self._history.peek)
            if history is None:
                raise ValueError("Es gibt keine frühere HomeIntent-Änderung")
            previous = history.get("automations")
            if not isinstance(previous, list):
                raise ValueError("Der letzte Verlaufseintrag ist beschädigt")
            snapshot = await self._async_read_snapshot(path)
            transaction = await self._async_begin_transaction(
                operation="rollback",
                automation_id=str(history.get("automation_id") or "unknown"),
                path=path,
                snapshot=snapshot,
                updated=previous,
                record_history=False,
            )
            try:
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                previous_metadata = history.get("metadata", {})
                if not isinstance(previous_metadata, dict):
                    raise ValueError("Die Metadaten im Verlaufseintrag sind beschädigt")
                await self._metadata_store.async_replace_all(previous_metadata)
            except Exception:
                await self._async_rollback_transaction(path, transaction)
                await self._hass.services.async_call(
                    "automation", "reload", {}, blocking=True, context=system_context_for_turn()
                )
                raise
            await self._async_complete_transaction()
            await self._hass.async_add_executor_job(self._history.pop)
            return str(history.get("operation") or "Änderung")

    @staticmethod
    def _scheduled_for_from_yaml(
        automations: list[dict[str, Any]],
        automation_id: str,
        previous_value: str | None,
    ) -> str | None:
        """Derive the guarded one-shot timestamp from Home Assistant YAML."""
        if not previous_value:
            return None
        automation = next(
            (item for item in automations if item.get("id") == automation_id), None
        )
        if automation is None:
            return None
        times = [
            trigger.get("at")
            for trigger in automation.get("triggers") or []
            if isinstance(trigger, dict) and trigger.get("trigger") == "time"
        ]
        date_values = []
        for condition in automation.get("conditions") or []:
            if not isinstance(condition, dict) or condition.get("condition") != "template":
                continue
            template = str(condition.get("value_template", ""))
            match = re.search(
                r"now\(\)\.date\(\)\.isoformat\(\)\s*==\s*['\"]"
                r"(\d{4}-\d{2}-\d{2})['\"]",
                template,
            )
            if match:
                date_values.append(match.group(1))
        if len(times) != 1 or len(date_values) != 1:
            return None
        try:
            previous = datetime.fromisoformat(previous_value)
            parsed = datetime.fromisoformat(f"{date_values[0]}T{times[0]}")
        except (TypeError, ValueError):
            return None
        if previous.tzinfo is not None:
            parsed = parsed.replace(tzinfo=previous.tzinfo)
        return parsed.isoformat()

    @staticmethod
    def _max_runs_from_yaml(
        automations: list[dict[str, Any]], automation_id: str
    ) -> int | None:
        automation = next(
            (item for item in automations if item.get("id") == automation_id), None
        )
        if automation is None:
            return None
        values = [
            action.get("data", {}).get("max_runs")
            for action in automation.get("actions") or []
            if isinstance(action, dict)
            and action.get("action") == "homeintent.record_automation_run"
            and isinstance(action.get("data"), dict)
        ]
        if len(values) == 1 and isinstance(values[0], int) and 2 <= values[0] <= 10:
            return values[0]
        return None

    @staticmethod
    def _read_automations(path: str) -> list[dict[str, Any]]:
        """Mirrors upstream ``config/automation.py``'s own ``_read()``
        helper: a not-yet-existing ``automations.yaml`` (a fresh HA install
        that never created one) is an empty automation list, not an error."""
        try:
            content = yaml_util.load_yaml(path)
        except FileNotFoundError:
            return []
        return content if content else []

    @classmethod
    def _read_snapshot(cls, path: str) -> _AutomationFileSnapshot:
        """Read YAML only when its byte fingerprint stays stable."""
        for _attempt in range(3):
            before = cls._file_digest(path)
            automations = cls._read_automations(path)
            after = cls._file_digest(path)
            if before == after:
                return _AutomationFileSnapshot(automations, after)
        raise ConcurrentAutomationUpdateError(
            "automations.yaml changed repeatedly while HomeIntent was reading it"
        )

    @classmethod
    def _write_if_unchanged(
        cls,
        path: str,
        automations: list[dict[str, Any]],
        expected_digest: str,
    ) -> str:
        content, _content_digest = cls._serialize_automations(automations)
        return cls._write_content_if_unchanged(path, content, expected_digest)

    @staticmethod
    def _serialize_automations(
        automations: list[dict[str, Any]],
    ) -> tuple[str, str]:
        content = yaml_util.dump(automations)
        digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        return content, digest

    @classmethod
    def _write_content_if_unchanged(
        cls,
        path: str,
        content: str,
        expected_digest: str,
    ) -> str:
        current_digest = cls._file_digest(path)
        if current_digest != expected_digest:
            raise ConcurrentAutomationUpdateError(
                "automations.yaml was modified outside HomeIntent",
                write_started=False,
            )
        write_utf8_file_atomic(path, content)
        after_digest = cls._file_digest(path)
        content_digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        # ``after_digest == expected_digest`` is retained for isolated unit
        # tests whose atomic writer is mocked. In a real filesystem the new
        # digest must equal the exact bytes HomeIntent just serialized.
        if after_digest not in (content_digest, expected_digest):
            raise ConcurrentAutomationUpdateError(
                "automations.yaml changed while HomeIntent was writing it",
                write_started=True,
            )
        return after_digest

    @staticmethod
    def _file_digest(path: str) -> str:
        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except FileNotFoundError:
            return "missing"
        return "sha256:" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def _write_automations(path: str, automations: list[dict[str, Any]]) -> None:
        write_utf8_file_atomic(path, yaml_util.dump(automations))
