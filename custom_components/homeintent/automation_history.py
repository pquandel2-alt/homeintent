"""Small bounded version history for recoverable automation mutations."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from typing import Any, cast


def _write_atomic(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(prefix=".homeintent_history_", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


AUTOMATION_HISTORY_FILENAME = ".storage/homeintent_automation_history.json"
LEGACY_AUTOMATION_HISTORY_FILENAME = ".storage/ha_nlu_automation_history.json"
HISTORY_SCHEMA_VERSION = 2


def canonical_state_digest(value: object) -> str:
    """Digest one automation/metadata value, independent of file formatting."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _automation_state(
    automations: list[dict[str, Any]], automation_id: str
) -> tuple[dict[str, Any] | None, int | None]:
    matches = [
        (index, item)
        for index, item in enumerate(automations)
        if item.get("id") == automation_id
    ]
    if len(matches) > 1:
        raise ValueError("Die Automations-ID ist in automations.yaml nicht eindeutig")
    if not matches:
        return None, None
    index, item = matches[0]
    return item, index


class AutomationHistoryStore:
    def __init__(self, path: str, limit: int = 10) -> None:
        self._path = path
        self._limit = limit

    @property
    def available(self) -> bool:
        return os.path.isdir(os.path.dirname(self._path) or ".")

    def read(self) -> list[dict[str, Any]]:
        try:
            with open(self._path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        return cast(list[dict[str, Any]], value) if isinstance(value, list) else []

    def append(
        self,
        *,
        operation: str,
        automation_id: str,
        created_at: str,
        automations: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        post_write_digest: str | None = None,
        post_metadata_digest: str | None = None,
        owner_user_id: str | None = None,
        after_automations: list[dict[str, Any]] | None = None,
    ) -> None:
        entries = self.read()
        entry: dict[str, Any] = {
            "operation": operation,
            "automation_id": automation_id,
            "created_at": created_at,
            "automations": automations,
            "metadata": metadata or {},
            "post_write_digest": post_write_digest,
            "post_metadata_digest": post_metadata_digest,
            "owner_user_id": owner_user_id,
        }
        if after_automations is not None:
            before_automation, before_position = _automation_state(
                automations, automation_id
            )
            after_automation, after_position = _automation_state(
                after_automations, automation_id
            )
            before_metadata = (metadata or {}).get(automation_id)
            entry.update(
                {
                    "schema_version": HISTORY_SCHEMA_VERSION,
                    "before_automation": before_automation,
                    "after_automation": after_automation,
                    "before_automation_digest": canonical_state_digest(
                        before_automation
                    ),
                    "after_automation_digest": canonical_state_digest(
                        after_automation
                    ),
                    "before_metadata": before_metadata,
                    "before_metadata_digest": canonical_state_digest(
                        before_metadata
                    ),
                    "after_metadata": None,
                    "after_metadata_digest": None,
                    "before_position": before_position,
                    "after_position": after_position,
                }
            )
        entries.append(entry)
        _write_atomic(
            self._path,
            json.dumps(
                entries[-self._limit :],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )

    def finalize(
        self,
        created_at: str,
        *,
        post_metadata_digest: str,
        after_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Complete the newest prepared entry after all side effects commit."""
        entries = self.read()
        if not entries or entries[-1].get("created_at") != created_at:
            raise RuntimeError("Der Automationsverlauf hat sich unerwartet geändert")
        entries[-1]["post_metadata_digest"] = post_metadata_digest
        if entries[-1].get("schema_version") == HISTORY_SCHEMA_VERSION:
            entries[-1]["after_metadata"] = after_metadata
            entries[-1]["after_metadata_digest"] = canonical_state_digest(
                after_metadata
            )
        _write_atomic(
            self._path,
            json.dumps(
                entries[-self._limit :],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )

    def pop(self) -> dict[str, Any] | None:
        entries = self.read()
        if not entries:
            return None
        result = entries.pop()
        _write_atomic(
            self._path, json.dumps(entries, ensure_ascii=False, indent=2, default=str)
        )
        return result

    def peek(self) -> dict[str, Any] | None:
        entries = self.read()
        return entries[-1] if entries else None

    def pop_if_created_at(self, created_at: str) -> None:
        latest = self.peek()
        if latest is not None and latest.get("created_at") == created_at:
            self.pop()
