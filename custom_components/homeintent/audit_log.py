"""Privacy-minimised bounded execution history for spoken diagnostics."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditEntry:
    timestamp: datetime
    actor: str
    domain: str
    service: str
    entity_ids: tuple[str, ...]
    # 7.13.1: the final per-target status of an independent batch, decided
    # after the effect wait - ``(entity_id, status)`` in plan order.
    outcomes: tuple[tuple[str, str], ...] = ()


class AuditTrail:
    def __init__(self, limit: int = 250) -> None:
        self._entries: deque[AuditEntry] = deque(maxlen=limit)

    def record(self, now: datetime, user_id: str | None, plan) -> None:
        actor = (
            hashlib.sha256(user_id.encode()).hexdigest()[:10]
            if user_id else "voice"
        )
        raw_ids = plan.entity_id
        entity_ids = (raw_ids,) if isinstance(raw_ids, str) else tuple(raw_ids)
        self._entries.append(AuditEntry(
            now, actor, plan.domain, plan.service, entity_ids,
        ))

    def record_batch(
        self, now: datetime, user_id: str | None, result, called: tuple[str, ...]
    ) -> None:
        """One entry per batch with every target's final status.

        ``called`` are the targets whose service call was made; the statuses
        also name the targets refused before any call.
        """
        actor = (
            hashlib.sha256(user_id.encode()).hexdigest()[:10]
            if user_id else "voice"
        )
        self._entries.append(AuditEntry(
            now, actor, result.plan.domain, result.plan.service, called, result.statuses,
        ))

    def today(self, now: datetime) -> tuple[AuditEntry, ...]:
        return tuple(entry for entry in self._entries if entry.timestamp.date() == now.date())


def render_today(entries: tuple[AuditEntry, ...]) -> str:
    if not entries:
        return "Heute wurde noch nichts durch HomeIntent ausgeführt."
    recent = entries[-10:]
    parts = [
        f"{entry.timestamp:%H:%M} Uhr {entry.domain}.{entry.service} für "
        + ", ".join(entry.entity_ids)
        + _failed_suffix(entry)
        for entry in recent
    ]
    prefix = f"Heute wurden {len(entries)} Aktionen ausgeführt. "
    if len(entries) > len(recent):
        prefix += "Die letzten zehn sind: "
    return prefix + "; ".join(parts) + "."


def _failed_suffix(entry: AuditEntry) -> str:
    """Targets of a batch that did not end in a confirmed success."""
    failed = [entity_id for entity_id, status in entry.outcomes if status != "success"]
    return f" (nicht bestätigt: {', '.join(failed)})" if failed else ""
