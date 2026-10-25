"""Persistent transaction journal for HomeIntent automation YAML writes."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.util.file import write_utf8_file_atomic

TRANSACTION_JOURNAL_FILENAME = "homeintent_automation_transaction.json"
LEGACY_TRANSACTION_JOURNAL_FILENAME = "ha_nlu_automation_transaction.json"


class ConcurrentAutomationUpdateError(RuntimeError):
    """Raised when automations.yaml changed outside HomeIntent's lock."""

    def __init__(self, message: str, *, write_started: bool = False) -> None:
        super().__init__(message)
        self.write_started = write_started


@dataclass(frozen=True)
class AutomationTransaction:
    """Enough durable state to recognize, finish, or roll back a write."""

    operation: str
    automation_id: str
    created_at: str
    before_digest: str
    after_digest: str | None
    before_automations: list[dict[str, Any]]
    after_automations: list[dict[str, Any]]
    state: str = "prepared"
    history_target_ids: tuple[str, ...] = ()
    history_target_roles: tuple[tuple[str, str], ...] = ()
    # Only affected sidecar entries, never the complete metadata store. These
    # optional values let startup recovery roll back a crash between the YAML
    # and sidecar halves of a composite pause/resume transaction.
    before_metadata_by_id: dict[str, dict[str, Any] | None] | None = None
    after_metadata_by_id: dict[str, dict[str, Any] | None] | None = None


class AutomationTransactionJournal:
    """Atomic single-transaction journal stored beside automations.yaml."""

    def __init__(self, path: str) -> None:
        self.path = path

    def write(self, transaction: AutomationTransaction) -> None:
        write_utf8_file_atomic(
            self.path,
            json.dumps(asdict(transaction), ensure_ascii=False, indent=2),
        )

    def read(self) -> AutomationTransaction | None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            return None
        if not isinstance(value, dict):
            raise ValueError("HomeIntent transaction journal must contain an object")
        return AutomationTransaction(**value)

    def clear(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
