"""Central typed coordinator for multiple pending household dialogs.

The manager owns priority, replacement, correction and explanation.  It does
not parse device commands and cannot execute actions; it consumes already
typed slots produced by the V7 understanding boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import IntEnum, StrEnum
from typing import Mapping


class DialogTaskKind(StrEnum):
    ENTITY_SELECTION = "entity_selection"
    MISSING_SLOT = "missing_slot"
    SAFETY_CONFIRMATION = "safety_confirmation"
    AUTOMATION = "automation"
    MEMORY_CONFIRMATION = "memory_confirmation"
    COMFORT_CONFIRMATION = "comfort_confirmation"
    COMFORT_PROFILE_DEFINITION = "comfort_profile_definition"
    PLAN_CONFIRMATION = "plan_confirmation"
    ROUTINE_DEFINITION = "routine_definition"
    ALIAS_CONFIRMATION = "alias_confirmation"
    GOAL_SEMANTIC_CLARIFICATION = "goal_semantic_clarification"
    GOAL_RUN_CLARIFICATION = "goal_run_clarification"
    LEARNING_CONFIRMATION = "learning_confirmation"
    HABIT_SUGGESTION = "habit_suggestion"
    PREFERENCE_CONFIRMATION = "preference_confirmation"
    MODEL_RESET_CONFIRMATION = "model_reset_confirmation"
    CONFLICT_RESOLUTION = "conflict_resolution"
    PROACTIVE_CLARIFICATION = "proactive_clarification"
    STANDING_PERMISSION_CONFIRMATION = "standing_permission_confirmation"
    PROACTIVE_MUTE_CONFIRMATION = "proactive_mute_confirmation"
    ROUTINE_BINDING = "routine_binding"
    RECURRENCE_CHOICE = "recurrence_choice"
    LEARNING_OFFER = "learning_offer"
    UNKNOWN_WORD = "unknown_word"


class DialogPriority(IntEnum):
    FOLLOWUP = 10
    SELECTION = 20
    CONFIRMATION = 30
    SAFETY = 40


@dataclass(frozen=True)
class DialogTask:
    task_id: str
    kind: DialogTaskKind
    priority: DialogPriority
    created_at: datetime
    expires_at: datetime
    slots: Mapping[str, object] = field(default_factory=lambda: _empty_mapping())
    missing_slots: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    reason: str = ""
    requested_by_user_id: str | None = None
    payload: object | None = None


@dataclass(frozen=True)
class DialogDecision:
    task: DialogTask | None
    response: str | None
    completed: bool = False
    cancelled: bool = False
    replaced: bool = False


# Task id under which legacy ``pending_*`` context state is mirrored at the
# start of a turn. The mirror can outlive the question by one turn, so it does
# not count as a question of its own (see ``has_open_question``).
CONTEXT_MIRROR_TASK_ID = "context-payload"


class DialogManager:
    """Conversation-scoped task queue with one deterministic active task."""

    def __init__(self, *, ttl_seconds: int = 120) -> None:
        self.ttl = timedelta(seconds=max(10, ttl_seconds))
        self._tasks: dict[str, dict[str, DialogTask]] = {}

    def clear_all(self) -> None:
        """Drop every open dialog task (test reset, 7.6.0)."""
        self._tasks.clear()

    def add(self, conversation_id: str, task: DialogTask) -> None:
        self._tasks.setdefault(conversation_id, {})[task.task_id] = task

    def create(
        self,
        conversation_id: str,
        task_id: str,
        kind: DialogTaskKind,
        priority: DialogPriority,
        *,
        slots: Mapping[str, object] | None = None,
        missing_slots: tuple[str, ...] = (),
        candidates: tuple[str, ...] = (),
        reason: str = "",
        requested_by_user_id: str | None = None,
        payload: object | None = None,
        now: datetime | None = None,
    ) -> DialogTask:
        current = now or datetime.now(timezone.utc)
        task = DialogTask(
            task_id,
            kind,
            priority,
            current,
            current + self.ttl,
            dict(slots or {}),
            missing_slots,
            candidates,
            reason,
            requested_by_user_id,
            payload,
        )
        self.add(conversation_id, task)
        return task

    def active(
        self, conversation_id: str, *, now: datetime | None = None
    ) -> DialogTask | None:
        current = now or datetime.now(timezone.utc)
        tasks = self._tasks.get(conversation_id, {})
        expired = [task_id for task_id, task in tasks.items() if task.expires_at <= current]
        for task_id in expired:
            tasks.pop(task_id, None)
        return max(
            tasks.values(),
            key=lambda item: (item.priority, item.created_at, item.task_id),
            default=None,
        )

    def has_open_question(
        self, conversation_id: str, *, now: datetime | None = None
    ) -> bool:
        """Whether a task created by a handler still waits for the user.

        Mirrored legacy state is excluded: it is refreshed only at the start of
        the next turn and is answered by the legacy context check instead.
        """
        self.active(conversation_id, now=now)
        return any(
            task_id != CONTEXT_MIRROR_TASK_ID
            for task_id in self._tasks.get(conversation_id, {})
        )

    def cancel(self, conversation_id: str, task_id: str | None = None) -> int:
        tasks = self._tasks.get(conversation_id, {})
        if task_id is None:
            count = len(tasks)
            tasks.clear()
            return count
        return int(tasks.pop(task_id, None) is not None)

    def explain(self, conversation_id: str) -> str:
        task = self.active(conversation_id)
        if task is None:
            return "Es ist keine Rückfrage offen."
        missing = ", ".join(task.missing_slots)
        detail = f" Mir fehlt: {missing}." if missing else ""
        reason = task.reason or "Die Bedeutung ist noch nicht eindeutig."
        return f"{reason}{detail} Ich führe bis zur Klärung nichts aus."

    def understood(self, conversation_id: str) -> str:
        task = self.active(conversation_id)
        if task is None:
            return "Es ist kein unvollständiger Auftrag offen."
        visible = ", ".join(
            f"{key}={value}" for key, value in sorted(task.slots.items())
        )
        return f"Offener Auftrag: {visible or 'noch keine sicheren Angaben'}."

    def replace_with_complete_command(self, conversation_id: str) -> DialogDecision:
        replaced = self.cancel(conversation_id) > 0
        return DialogDecision(None, None, completed=True, replaced=replaced)

    def select(
        self,
        conversation_id: str,
        selection: str | int,
        *,
        user_id: str | None,
    ) -> DialogDecision:
        task = self.active(conversation_id)
        if task is None:
            return DialogDecision(None, "Dazu ist keine eindeutige Auswahl offen.")
        if task.requested_by_user_id is not None and task.requested_by_user_id != user_id:
            return DialogDecision(task, "Diese Rückfrage gehört zu einem anderen Benutzer.")
        selected: str | None = None
        if isinstance(selection, int):
            if 1 <= selection <= len(task.candidates):
                selected = task.candidates[selection - 1]
        else:
            matches = [item for item in task.candidates if item.casefold() == selection.casefold()]
            if len(matches) == 1:
                selected = matches[0]
        if selected is None:
            return DialogDecision(task, "Die Auswahl ist nicht eindeutig; der Dialog bleibt offen.")
        slots = {**task.slots, "selection": selected}
        updated = replace(task, slots=slots, missing_slots=tuple(slot for slot in task.missing_slots if slot != "selection"))
        self.add(conversation_id, updated)
        completed = not updated.missing_slots
        if completed:
            self.cancel(conversation_id, updated.task_id)
        return DialogDecision(updated, None, completed=completed)

    def correct(
        self,
        conversation_id: str,
        corrections: Mapping[str, object],
    ) -> DialogDecision:
        task = self.active(conversation_id)
        if task is None:
            return DialogDecision(None, "Es gibt keinen relevanten Auftrag zum Korrigieren.")
        updated = replace(
            task,
            slots={**task.slots, **corrections},
            missing_slots=tuple(slot for slot in task.missing_slots if slot not in corrections),
        )
        self.add(conversation_id, updated)
        return DialogDecision(updated, None, completed=not updated.missing_slots)

    def synchronize_context_task(
        self,
        conversation_id: str,
        *,
        kind: DialogTaskKind | None,
        priority: DialogPriority = DialogPriority.FOLLOWUP,
        slots: Mapping[str, object] | None = None,
        candidates: tuple[str, ...] = (),
        reason: str = "",
        requested_by_user_id: str | None = None,
        payload: object | None = None,
    ) -> DialogTask | None:
        """Synchronize one typed context payload into the central task queue.

        ``ConversationContext`` remains the storage envelope, while typed
        payload, selection, ownership, priority and replacement live in the
        central coordinator.
        """
        task_id = CONTEXT_MIRROR_TASK_ID
        if kind is None:
            self.cancel(conversation_id, task_id)
            return None
        existing = self._tasks.get(conversation_id, {}).get(task_id)
        desired_slots = dict(slots or {})
        if (
            existing is not None
            and existing.kind is kind
            and existing.priority == priority
            and existing.slots == desired_slots
            and existing.candidates == candidates
            and existing.requested_by_user_id == requested_by_user_id
            and existing.payload == payload
        ):
            return existing
        self.cancel(conversation_id, task_id)
        return self.create(
            conversation_id,
            task_id,
            kind,
            priority,
            slots=desired_slots,
            candidates=candidates,
            reason=reason,
            requested_by_user_id=requested_by_user_id,
            payload=payload,
        )

    def synchronize_pending_payload(
        self,
        conversation_id: str,
        kind_name: str | None,
        payload: object | None,
    ) -> DialogTask | None:
        """Mirror one historical typed payload at the state-store boundary.

        This is the only compatibility adapter for old ``pending_*`` fields.
        Priority, ownership, selection and expiry immediately become manager
        state; conversation routing no longer has to wait one turn to see an
        updated payload.
        """
        if kind_name is None or payload is None:
            return self.synchronize_context_task(conversation_id, kind=None)
        if kind_name == "CLARIFICATION":
            kind, priority = DialogTaskKind.ENTITY_SELECTION, DialogPriority.SELECTION
        elif kind_name == "SERVICE_CONFIRMATION":
            kind, priority = DialogTaskKind.SAFETY_CONFIRMATION, DialogPriority.SAFETY
        elif kind_name in {
            "AUTOMATION_CONFIRMATION",
            "AUTOMATION_DELETION",
            "CALENDAR_MUTATION",
            "AUTOMATION_MANAGEMENT",
            "ALIAS_LEARNING",
        }:
            kind, priority = DialogTaskKind.SAFETY_CONFIRMATION, DialogPriority.CONFIRMATION
        elif kind_name in {
            "AUTOMATION_DRAFT",
            "AUTOMATION_ACTION_EDIT",
            "AUTOMATION_STRUCTURE_EDIT",
            "AUTOMATION_WIZARD",
        }:
            kind, priority = DialogTaskKind.AUTOMATION, DialogPriority.FOLLOWUP
        else:
            kind, priority = DialogTaskKind.MISSING_SLOT, DialogPriority.FOLLOWUP
        raw_candidates = getattr(payload, "candidates", ())
        candidates = tuple(
            value
            for item in raw_candidates
            if isinstance((value := getattr(item, "entity_id", None)), str)
        )
        owner = getattr(payload, "requested_by_user_id", None)
        return self.synchronize_context_task(
            conversation_id,
            kind=kind,
            priority=priority,
            slots={"context_kind": kind_name},
            candidates=candidates,
            reason="Ein bestehender Dialog benötigt eine eindeutige Fortsetzung.",
            requested_by_user_id=owner if isinstance(owner, str) else None,
            payload=payload,
        )


def _empty_mapping() -> dict[str, object]:
    return {}
