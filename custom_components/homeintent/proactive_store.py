"""Bounded V12 operational state: situations, history and persistence.

Only operational state needed for a safe restart is persisted (active
situations, snoozes, attention dedupe, proposals/sessions, standing
permissions and bounded history).  No HA state machine copy, transcripts,
audio or room-movement history is ever written.

Persistence reuses the thermal tracker's generation rule: the document is
built on the event loop, written in a worker thread, and a write whose
generation is not newer than the last written one is dropped, so an older
asynchronous write can never overwrite newer state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
from collections import OrderedDict, deque
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Sequence, cast

from .proactive_model import (
    CommunicationChannel,
    HistoryRecord,
    OpportunityOutcome,
    PriorityLevel,
    PrivacyLevel,
    ProactiveSituation,
    QUIET_SITUATION_STATES,
    SituationEvidence,
    SituationKind,
    SituationState,
    TERMINAL_SITUATION_STATES,
)
from .situation_detection import DetectionSignal


SCHEMA_VERSION = 1
MAX_SITUATIONS = 256
MAX_HISTORY = 500
MAX_EVIDENCE = 8
TERMINAL_RETENTION = timedelta(hours=24)


class SituationStore:
    """One lifecycle per stable dedupe key; bounded, oldest-terminal evicted."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, ProactiveSituation] = OrderedDict()

    def get(self, dedupe_key: str) -> ProactiveSituation | None:
        return self._items.get(dedupe_key)

    def by_id(self, situation_id: str) -> ProactiveSituation | None:
        return next((item for item in self._items.values() if item.situation_id == situation_id), None)

    def active(self) -> tuple[ProactiveSituation, ...]:
        return tuple(
            item for item in self._items.values()
            if item.state not in TERMINAL_SITUATION_STATES
        )

    def all(self) -> tuple[ProactiveSituation, ...]:
        return tuple(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def apply(
        self,
        signal: DetectionSignal,
        *,
        now: datetime,
        priority_hint: PriorityLevel,
        privacy: PrivacyLevel,
    ) -> tuple[ProactiveSituation | None, bool]:
        """Return (situation, changed).  Repeated identical events change nothing."""
        current = self._items.get(signal.dedupe_key)
        if not signal.active:
            if current is None or current.state in TERMINAL_SITUATION_STATES:
                return current, False
            resolved = replace(current, state=SituationState.RESOLVED, updated_at=now)
            self._items[signal.dedupe_key] = resolved
            return resolved, True
        if current is not None and current.state not in TERMINAL_SITUATION_STATES:
            updated = replace(
                current,
                subject_ids=signal.subject_ids or current.subject_ids,
                evidence=tuple(signal.evidence[:MAX_EVIDENCE]) or current.evidence,
                subject_name=signal.subject_name or current.subject_name,
            )
            if updated == current:
                return current, False
            updated = replace(updated, updated_at=now)
            self._items[signal.dedupe_key] = updated
            return updated, True
        situation_id = "sit_" + hashlib.sha256(
            f"{signal.dedupe_key}\0{signal.started_at.isoformat()}".encode()
        ).hexdigest()[:20]
        created = ProactiveSituation(
            situation_id, signal.kind, signal.subject_ids, signal.area_id,
            signal.started_at, now, SituationState.ACTIVE,
            tuple(signal.evidence[:MAX_EVIDENCE]), signal.persons, (),
            priority_hint, privacy, signal.dedupe_key,
            owner_user_id=signal.owner_user_id, subject_name=signal.subject_name,
            anticipation_ref=signal.anticipation_ref,
        )
        self._items.pop(signal.dedupe_key, None)
        self._items[signal.dedupe_key] = created
        self._evict(now)
        return created, True

    def update(self, situation: ProactiveSituation) -> None:
        if situation.dedupe_key in self._items:
            self._items[situation.dedupe_key] = situation

    def set_state(
        self, dedupe_key: str, state: SituationState, *, now: datetime,
        snooze_until: datetime | None = None,
    ) -> ProactiveSituation | None:
        current = self._items.get(dedupe_key)
        if current is None or current.state in TERMINAL_SITUATION_STATES:
            return None
        updated = replace(current, state=state, updated_at=now, snooze_until=snooze_until)
        self._items[dedupe_key] = updated
        return updated

    def mark_communicated(self, dedupe_key: str, now: datetime) -> ProactiveSituation | None:
        current = self._items.get(dedupe_key)
        if current is None or current.state in TERMINAL_SITUATION_STATES:
            return None
        state = current.state if current.state in QUIET_SITUATION_STATES else SituationState.COMMUNICATED
        updated = replace(
            current, state=state, updated_at=now, communicated_at=now,
            communication_count=current.communication_count + 1,
            snooze_until=None,
        )
        self._items[dedupe_key] = updated
        return updated

    def expire_older_than(self, now: datetime, max_age: Mapping[SituationKind, timedelta]) -> int:
        count = 0
        for key, item in tuple(self._items.items()):
            limit = max_age.get(item.kind)
            if limit is not None and item.state not in TERMINAL_SITUATION_STATES and now - item.started_at > limit:
                self._items[key] = replace(item, state=SituationState.EXPIRED, updated_at=now)
                count += 1
        return count

    def _evict(self, now: datetime) -> None:
        for key, item in tuple(self._items.items()):
            if item.state in TERMINAL_SITUATION_STATES and now - item.updated_at > TERMINAL_RETENTION:
                del self._items[key]
        while len(self._items) > MAX_SITUATIONS:
            victim = next(
                (key for key, item in self._items.items() if item.state in TERMINAL_SITUATION_STATES),
                next(iter(self._items)),
            )
            del self._items[victim]

    def to_dict(self) -> list[dict[str, object]]:
        return [_situation_dict(item) for item in self._items.values()]

    @classmethod
    def from_list(cls, raw: object) -> "SituationStore":
        store = cls()
        if not isinstance(raw, list):
            return store
        for item in cast(Sequence[object], raw)[-MAX_SITUATIONS:]:
            parsed = _situation_from(item)
            if parsed is not None:
                store._items[parsed.dedupe_key] = parsed
        return store


# Records of one routing pass are written microseconds apart.
_SAME_DECISION_SECONDS = 1.0


class ProactiveHistoryStore:
    """Bounded explainability log; decisions and reason codes only."""

    def __init__(self) -> None:
        self._items: deque[HistoryRecord] = deque(maxlen=MAX_HISTORY)
        self._counter = 0

    def append(self, record: HistoryRecord) -> None:
        last = self._items[-1] if self._items else None
        if (
            last is not None
            and record.recipient_user_id is not None
            and last.recipient_user_id is not None
            and not last.addressed_to(record.recipient_user_id)
            and abs((record.timestamp - last.timestamp).total_seconds()) <= _SAME_DECISION_SECONDS
            and replace(
                last,
                record_id=record.record_id,
                recipient_user_id=record.recipient_user_id,
                other_recipient_user_ids=(),
                timestamp=record.timestamp,
            ) == record
        ):
            # The same decision for another household member (same
            # situation, channel, result, reasons and moment, i.e. one
            # routing pass) extends the existing entry instead of
            # duplicating it (F25).
            self._items[-1] = replace(
                last,
                other_recipient_user_ids=(
                    *last.other_recipient_user_ids, record.recipient_user_id,
                ),
            )
            return
        self._items.append(record)

    def next_id(self, now: datetime) -> str:
        self._counter += 1
        return f"h{int(now.timestamp())}_{self._counter}"

    def records(self) -> tuple[HistoryRecord, ...]:
        return tuple(self._items)

    def latest_for_subject(self, words: Iterable[str]) -> HistoryRecord | None:
        wanted = [item for item in words if item]
        for record in reversed(self._items):
            label = record.subject_label.casefold()
            if record.decision in {OpportunityOutcome.COMMUNICATE, OpportunityOutcome.ESCALATE} and (
                not wanted or any(word in label for word in wanted)
            ):
                return record
        return None

    def since(self, when: datetime) -> tuple[HistoryRecord, ...]:
        return tuple(item for item in self._items if item.timestamp >= when)

    def __len__(self) -> int:
        return len(self._items)

    def to_list(self) -> list[dict[str, object]]:
        return [_history_dict(item) for item in self._items]

    @classmethod
    def from_list(cls, raw: object) -> "ProactiveHistoryStore":
        store = cls()
        if not isinstance(raw, list):
            return store
        for item in cast(Sequence[object], raw)[-MAX_HISTORY:]:
            parsed = _history_from(item)
            if parsed is not None:
                store._items.append(parsed)
        return store


class GenerationalJsonFile:
    """Atomic JSON persistence whose writes are ordered by generation."""

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._generation = 0
        self._written_generation = 0

    @property
    def written_generation(self) -> int:
        return self._written_generation

    def next_generation(self) -> int:
        self._generation += 1
        return self._generation

    def write_generation(self, generation: int, document: Mapping[str, object]) -> bool:
        with self._lock:
            if generation <= self._written_generation:
                return False
            self._write(document)
            self._written_generation = generation
            return True

    async def async_write(self, document: Mapping[str, object]) -> bool:
        # The document is already built on the loop; the thread only writes.
        generation = self.next_generation()
        return await asyncio.to_thread(self.write_generation, generation, document)

    def _write(self, document: Mapping[str, object]) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".homeintent_proactive_", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def read(self) -> Mapping[str, object] | None:
        if self._path is None:
            return None
        try:
            raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None
        if not isinstance(raw, Mapping):
            return None
        document = cast(Mapping[str, object], raw)
        if document.get("schema_version") != SCHEMA_VERSION:
            return None
        return document

    async def async_read(self) -> Mapping[str, object] | None:
        return await asyncio.to_thread(self.read)


# -- serialization helpers ------------------------------------------------------

def _situation_dict(item: ProactiveSituation) -> dict[str, object]:
    return {
        "situation_id": item.situation_id,
        "kind": item.kind.value,
        "subject_ids": list(item.subject_ids),
        "area_id": item.area_id,
        "started_at": item.started_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "state": item.state.value,
        "evidence": [[value.code, value.value] for value in item.evidence],
        "persons": list(item.persons),
        "related_goal_ids": list(item.related_goal_ids),
        "priority_hint": int(item.priority_hint),
        "privacy_level": int(item.privacy_level),
        "dedupe_key": item.dedupe_key,
        "snooze_until": item.snooze_until.isoformat() if item.snooze_until else None,
        "communicated_at": item.communicated_at.isoformat() if item.communicated_at else None,
        "communication_count": item.communication_count,
        "owner_user_id": item.owner_user_id,
        "subject_name": item.subject_name,
        "anticipation_ref": item.anticipation_ref,
    }


def _situation_from(raw: object) -> ProactiveSituation | None:
    if not isinstance(raw, Mapping):
        return None
    value = cast(Mapping[str, object], raw)
    try:
        started = _aware(value["started_at"])
        updated = _aware(value["updated_at"])
        if started is None or updated is None:
            return None
        return ProactiveSituation(
            str(value["situation_id"]), SituationKind(str(value["kind"])),
            _strings(value.get("subject_ids")), _text(value.get("area_id")),
            started, updated, SituationState(str(value["state"])),
            _evidence(value.get("evidence")), _strings(value.get("persons")),
            _strings(value.get("related_goal_ids")),
            PriorityLevel(int(cast(int, value["priority_hint"]))),
            PrivacyLevel(int(cast(int, value["privacy_level"]))),
            str(value["dedupe_key"]),
            _aware(value.get("snooze_until")),
            _aware(value.get("communicated_at")),
            int(cast(int, value.get("communication_count", 0))),
            _text(value.get("owner_user_id")),
            str(value.get("subject_name", ""))[:200],
            _text(value.get("anticipation_ref")),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _history_dict(item: HistoryRecord) -> dict[str, object]:
    return {
        "record_id": item.record_id,
        "situation_id": item.situation_id,
        "situation_kind": item.situation_kind.value,
        "subject_label": item.subject_label,
        "decision": item.decision.value,
        "recipient_user_id": item.recipient_user_id,
        "other_recipient_user_ids": list(item.other_recipient_user_ids),
        "channel": item.channel.value,
        "timestamp": item.timestamp.isoformat(),
        "priority": int(item.priority),
        "privacy": int(item.privacy),
        "result": item.result,
        "reasons": list(item.reasons),
        "acknowledgement": item.acknowledgement,
        "related_run_id": item.related_run_id,
        "evidence": [[value.code, value.value] for value in item.evidence],
        "model_ref": item.model_ref,
    }


def _history_from(raw: object) -> HistoryRecord | None:
    if not isinstance(raw, Mapping):
        return None
    value = cast(Mapping[str, object], raw)
    try:
        stamp = _aware(value["timestamp"])
        if stamp is None:
            return None
        return HistoryRecord(
            str(value["record_id"]), str(value["situation_id"]),
            SituationKind(str(value["situation_kind"])),
            str(value.get("subject_label", ""))[:200],
            OpportunityOutcome(str(value["decision"])),
            _text(value.get("recipient_user_id")),
            CommunicationChannel(str(value["channel"])), stamp,
            PriorityLevel(int(cast(int, value["priority"]))),
            PrivacyLevel(int(cast(int, value["privacy"]))),
            str(value.get("result", ""))[:200], _strings(value.get("reasons"))[:12],
            _text(value.get("acknowledgement")), _text(value.get("related_run_id")),
            _evidence(value.get("evidence")), _text(value.get("model_ref")),
            _strings(value.get("other_recipient_user_ids"))[:16],
        )
    except (KeyError, ValueError, TypeError):
        return None


def _evidence(value: object) -> tuple[SituationEvidence, ...]:
    if not isinstance(value, list):
        return ()
    result: list[SituationEvidence] = []
    for item in cast(Sequence[object], value)[:MAX_EVIDENCE]:
        if isinstance(item, list) and len(cast(Sequence[object], item)) == 2:
            code, text = cast(Sequence[object], item)
            if isinstance(code, str) and isinstance(text, str):
                result.append(SituationEvidence(code[:64], text[:200]))
    return tuple(result)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in cast(Sequence[object], value) if isinstance(item, str))[:64]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


__all__ = (
    "GenerationalJsonFile",
    "MAX_HISTORY",
    "MAX_SITUATIONS",
    "ProactiveHistoryStore",
    "SCHEMA_VERSION",
    "SituationStore",
)
