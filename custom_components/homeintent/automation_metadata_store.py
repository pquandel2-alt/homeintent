"""Automation Metadata Store (HomeIntent V5 Teil 8/10, V5.30/V5.31/V5.32):
per-automation identity/metadata for automations this integration created,
kept in a sidecar JSON file physically separate from ``automations.yaml``.

Cannot live as extra keys on the automation dict itself (the obvious first
idea): confirmed against the real upstream source
(``homeassistant/components/automation/config.py``'s ``PLATFORM_SCHEMA``,
built via ``script.make_script_schema({...}, script.SCRIPT_MODE_SINGLE)``
in ``homeassistant/helpers/script.py``) that this schema is built with its
default ``extra=vol.PREVENT_EXTRA`` - no ``extra=vol.ALLOW_EXTRA`` override
anywhere on that call path. An unrecognized top-level key on an automation
config makes ``PLATFORM_SCHEMA(config)`` raise ``vol.Invalid``, and Home
Assistant disables that automation with ``ValidationStatus.FAILED_SCHEMA``
instead of loading it - the exact "silently do something wrong" outcome
Regel 4 exists to prevent. A sidecar file is therefore not a stylistic
choice but the only way to attach metadata without risking every automation
this integration creates failing to load.

Same physical-separation precedent ``automation_executor.py``'s own
docstring already sets one layer up (that module's job is real HA I/O
kept out of ``nlu/``; this module's job is metadata kept out of the
schema-governed ``automations.yaml`` HA itself owns and validates).

V5.32 (Automation Versioning) only needs a ``version`` field to exist and
start at 1 today - there is no V5.27 (Automation Modification) yet to ever
write anything but 1, so incrementing it is future work, not simulated
here.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.file import write_utf8_file_atomic

from .automation_summary import CREATED_BY_HOMEINTENT
from .automation_history import canonical_state_digest
from .storage_migration import resolve_storage_path

METADATA_FILENAME = "homeintent_automation_metadata.json"
LEGACY_METADATA_FILENAME = "ha_nlu_automation_metadata.json"

# The only value this integration itself ever writes - a future V5.29
# (Automation Query) needs to tell "created by HomeIntent" apart from
# "created by hand in the UI", which is exactly what ``created_by`` records.
# v1 intent YAMLs are German-only (see ``conversation.py``'s own
# ``supported_languages`` property) - no language-detection mechanism
# exists anywhere in this project, so this is the one truthful value,
# not an invented default.
SOURCE_LANGUAGE_DE = "de"


@dataclass(frozen=True)
class AutomationMetadata:
    """One entry in the sidecar store, keyed by ``automation_id`` (the same
    id ``AutomationExecutor`` assigns as the automation's ``id`` field in
    ``automations.yaml`` - Regel 6: one identity, not a second parallel id
    scheme). ``created_at`` is an ISO 8601 UTC timestamp string (JSON has no
    native datetime type)."""

    automation_id: str
    source_text: str
    created_at: str
    created_by: str = CREATED_BY_HOMEINTENT
    version: int = 1
    source_language: str = SOURCE_LANGUAGE_DE
    scheduled_for: str | None = None
    once: bool = False
    max_runs: int | None = None
    run_count: int = 0
    # 7.9.1 A2: the Home Assistant user who confirmed the automation. Only
    # this user or an administrator may pause, switch, edit or delete it.
    # Entries written before 7.9.1 have no owner (key absent -> None): they
    # stay untouched and only an administrator may manage them.
    owner_user_id: str | None = None
    # Durable proof for a temporary pause helper. These fields are populated
    # only on the one-shot helper automation, never inferred from its alias or
    # action text. Older helpers do not have this proof and are intentionally
    # not eligible for automatic resume.
    pause_id: str | None = None
    pause_target_automation_id: str | None = None
    pause_resume_at: str | None = None
    pause_status: str | None = None
    pause_consumed_at: str | None = None
    # One-time proof records for lifecycle actions embedded in this
    # automation. Tokens are never rendered in normal Assist output/logs.
    lifecycle_orders: list[dict[str, Any]] | None = None


class AutomationMetadataStore:
    """Owned exclusively by ``AutomationExecutor`` (private collaborator, not
    a second top-level entry point conversation.py talks to directly) -
    called under ``AutomationExecutor``'s HA-instance-wide lock so two
    concurrent writes cannot race each other's read-modify-write of the
    metadata file either.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _path(self) -> str:
        return resolve_storage_path(
            self._hass, METADATA_FILENAME, LEGACY_METADATA_FILENAME
        )

    async def async_save(self, metadata: AutomationMetadata) -> None:
        """Adds or overwrites ``metadata``'s entry, keyed by its
        ``automation_id``. Must be called under the caller's own lock (the
        same lock guarding the paired ``automations.yaml`` write) - this
        method has no lock of its own, since it must never be interleaved
        with the automation-file half of the same transaction."""
        path = self._path()
        entries = await self._hass.async_add_executor_job(self._read, path)
        entries[metadata.automation_id] = asdict(metadata)
        await self._hass.async_add_executor_job(self._write, path, entries)

    async def async_load_all(self) -> dict[str, Any]:
        """Every stored entry, keyed by ``automation_id`` - the read half of
        this store's job (V5.29, Automation Query), same "no lock needed"
        reasoning ``AutomationExecutor.async_list_automations()`` documents
        for its own read: nothing here writes, so this can never race
        ``async_save``/``async_delete`` into a torn read (each of those is
        itself a single atomic file write)."""
        path = self._path()
        return await self._hass.async_add_executor_job(self._read, path)

    async def async_snapshot(self) -> tuple[dict[str, Any], str]:
        """Read entries and the exact byte digest from one stable state."""
        path = self._path()
        return await self._hass.async_add_executor_job(
            self._read_snapshot, path
        )

    async def async_entry_snapshot(
        self, automation_id: str
    ) -> tuple[dict[str, Any] | None, str]:
        """Read one sidecar entry and its canonical target-only digest."""
        entries = await self.async_load_all()
        value = entries.get(automation_id)
        if automation_id in entries and not isinstance(value, dict):
            raise ValueError("Der Metadateneintrag der Automation ist beschädigt")
        entry = value if isinstance(value, dict) else None
        return entry, canonical_state_digest(entry)

    async def async_replace_entry_if_unchanged(
        self,
        automation_id: str,
        replacement: dict[str, Any] | None,
        expected_entry_digest: str,
    ) -> str:
        """Replace only one sidecar entry after a target-scoped CAS check."""
        path = self._path()
        return await self._hass.async_add_executor_job(
            self._replace_entry_if_unchanged,
            path,
            automation_id,
            replacement,
            expected_entry_digest,
        )

    async def async_delete(self, automation_id: str) -> None:
        """Removes ``automation_id``'s entry if present - a no-op otherwise
        (rollback calling this for an id whose metadata write never
        succeeded must not itself raise)."""
        path = self._path()
        entries = await self._hass.async_add_executor_job(self._read, path)
        if automation_id in entries:
            del entries[automation_id]
            await self._hass.async_add_executor_job(self._write, path, entries)

    async def async_update(self, automation_id: str, **changes: Any) -> dict[str, Any]:
        """Update selected fields without discarding forward-compatible data."""
        path = self._path()
        entries = await self._hass.async_add_executor_job(self._read, path)
        entry = entries.get(automation_id)
        if not isinstance(entry, dict):
            raise ValueError(f"No HomeIntent metadata for {automation_id!r}")
        updated = {**entry, **changes}
        entries[automation_id] = updated
        await self._hass.async_add_executor_job(self._write, path, entries)
        return updated

    async def async_replace_all(self, entries: dict[str, Any]) -> None:
        """Atomically restore a version-history metadata snapshot."""
        path = self._path()
        await self._hass.async_add_executor_job(self._write, path, entries)

    async def async_replace_all_if_unchanged(
        self, entries: dict[str, Any], expected_digest: str
    ) -> None:
        """Restore a snapshot only if no newer sidecar bytes exist."""
        path = self._path()
        await self._hass.async_add_executor_job(
            self._replace_all_if_unchanged, path, entries, expected_digest
        )

    @staticmethod
    def _read(path: str) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as handle:
                content = json.load(handle)
        except FileNotFoundError:
            return {}
        if content is None:
            return {}
        if not isinstance(content, dict):
            raise ValueError("Die HomeIntent-Automationsmetadaten sind beschädigt")
        return content

    @classmethod
    def _read_snapshot(cls, path: str) -> tuple[dict[str, Any], str]:
        for _attempt in range(3):
            before = cls._file_digest(path)
            entries = cls._read(path)
            after = cls._file_digest(path)
            if before == after:
                return entries, after
        raise ValueError(
            "Die HomeIntent-Automationsmetadaten wurden während des Lesens geändert"
        )

    @staticmethod
    def _write(path: str, entries: dict[str, Any]) -> None:
        write_utf8_file_atomic(path, json.dumps(entries, ensure_ascii=False, indent=2))

    @classmethod
    def _replace_all_if_unchanged(
        cls, path: str, entries: dict[str, Any], expected_digest: str
    ) -> None:
        if cls._file_digest(path) != expected_digest:
            raise ValueError(
                "Die HomeIntent-Automationsmetadaten wurden inzwischen geändert"
            )
        cls._write(path, entries)

    @classmethod
    def _replace_entry_if_unchanged(
        cls,
        path: str,
        automation_id: str,
        replacement: dict[str, Any] | None,
        expected_entry_digest: str,
    ) -> str:
        entries = cls._read(path)
        current = entries.get(automation_id)
        if automation_id in entries and not isinstance(current, dict):
            raise ValueError("Der Metadateneintrag der Automation ist beschädigt")
        current_entry = current if isinstance(current, dict) else None
        if canonical_state_digest(current_entry) != expected_entry_digest:
            raise ValueError(
                "Die HomeIntent-Metadaten der Automation wurden inzwischen geändert"
            )
        if replacement is None:
            entries.pop(automation_id, None)
        else:
            entries[automation_id] = replacement
        cls._write(path, entries)
        return canonical_state_digest(replacement)

    @staticmethod
    def _file_digest(path: str) -> str:
        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except FileNotFoundError:
            return "missing"
        return "sha256:" + hashlib.sha256(content).hexdigest()


def utcnow_isoformat() -> str:
    """Small wrapper so ``AutomationExecutor`` doesn't need its own
    ``dt_util`` import just for this one call site."""
    return dt_util.utcnow().isoformat()
