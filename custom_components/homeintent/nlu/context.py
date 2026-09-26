"""Home-Assistant-free conversation state and pending-dialog state machine.

The state supports clarification, follow-up merging and reference resolution.
``active_pending_dialog()`` turns the historical optional ``pending_*`` fields
into one explicit, deterministic active node and reports conflicting stored
nodes for diagnostics.  ``conversation.py`` consumes that node before routing
a fresh utterance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from enum import Enum, auto
from typing import Any, TYPE_CHECKING, Callable, Mapping

from ..areas import AreaSnapshot
from ..automation_summary import AutomationSummary
from ..entities import EntitySnapshot
from ..floors import FloorSnapshot
from ..service_call import ServiceCallPlan
from .automation_model import AutomationModel, TriggerModel
from .command import SemanticCommand
from .dialog_focus import DialogFocus
from .discourse import DiscourseState
from .parser import ClarificationRequest
from .primitives import SemanticProperty
from .semantic_state import SemanticState

if TYPE_CHECKING:
    from ..alias_learning import AliasLearningDraft
    from ..automation_composition import EventClarification
    from ..automation_wizard import AutomationWizardState
    from ..automation_management import AutomationManagementRequest
    from ..automation_structure_edit import AutomationStructureEditRequest
    from ..calendar_event import CalendarEventDraft
    from ..calendar_management import (
        CalendarEventSummary,
        CalendarManagementKind,
        CalendarManagementRequest,
    )
    from ..productivity import ProductivityRequest
    from ..undo import UndoPlan

__all__ = [
    "ClarificationRequest",
    "ConversationContext",
    "ConversationContextStore",
    "DialogTurnMemory",
    "DEFAULT_CONTEXT_TTL_SECONDS",
    "AUTOMATION_CONFIRMATION_TTL_SECONDS",
    "PendingAutomationConfirmation",
    "PendingAutomationDeletion",
    "PendingServiceConfirmation",
    "PendingAutomationDraft",
    "PendingAutomationActionEdit",
    "PendingAutomationStructureEdit",
    "PendingAutomationManagement",
    "PendingCalendarEvent",
    "PendingCalendarMutation",
    "PendingSemanticCommand",
    "PendingProductivityCommand",
    "PendingAutomationWizard",
    "PendingAliasLearning",
    "PendingDialogKind",
    "ActivePendingDialog",
    "active_pending_dialog",
]


class PendingDialogKind(Enum):
    ALIAS_LEARNING = auto()
    AUTOMATION_WIZARD = auto()
    PRODUCTIVITY = auto()
    CALENDAR_EVENT = auto()
    CALENDAR_MUTATION = auto()
    AUTOMATION_CONFIRMATION = auto()
    AUTOMATION_ACTION_EDIT = auto()
    AUTOMATION_STRUCTURE_EDIT = auto()
    AUTOMATION_MANAGEMENT = auto()
    AUTOMATION_DELETION = auto()
    SERVICE_CONFIRMATION = auto()
    SEMANTIC_COMMAND = auto()
    AUTOMATION_DRAFT = auto()
    AUTOMATION_EVENT_CLARIFICATION = auto()
    CLARIFICATION = auto()


@dataclass(frozen=True)
class ActivePendingDialog:
    """The single explicit state-machine node active for a conversation."""

    kind: PendingDialogKind
    payload: object
    conflicting_kinds: tuple[PendingDialogKind, ...] = ()


@dataclass(frozen=True)
class DialogTurnMemory:
    """Canonical, read-only memory of the last understood turn.

    The older ``last_*`` fields remain as a compatibility surface for pending
    dialogs.  New discourse features consume this coherent snapshot instead
    of reconstructing one turn from several independently optional fields.
    """

    source_text: str
    entities: tuple[EntitySnapshot, ...] = ()
    predicate: str | None = None
    explanation: str | None = None
    command: SemanticCommand | None = None


@dataclass(frozen=True)
class PendingAutomationConfirmation:
    """A pending "Soll diese Automation erstellt werden?" Dry-Run gate
    (HomeIntent V5 Teil 7/10, V5.23) - the automation-creation counterpart
    to ``ClarificationRequest`` above, stored on ``ConversationContext`` the
    same way and resolved the same way (a dedicated reply-classification
    step, see ``nlu/automation_confirmation.py``, run before any fresh
    sentence is even attempted - same priority ``pending_clarification``
    already has in ``conversation.py``).

    Carries only the already-``validate_automation()``-clean
    ``AutomationModel`` itself - not a copy of the entities it was resolved
    against. The confirming reply turn already re-fetches a fresh entity
    list the same way every other turn does (``conversation.py``'s
    ``build_entity_snapshots()``), so the later HA Generator (V5.25) always
    resolves any still-abstract domain/area/quantifier target (see
    ``automation_action_parser.py``'s ``_build_quantified_target()``
    docstring: "re-validation against live HA state at execution time is a
    future HA-generator wave's job") against current state, not a
    confirmation-time snapshot that could already be stale.
    """

    model: AutomationModel
    requested_by_user_id: str | None = None


@dataclass(frozen=True)
class PendingAutomationDeletion:
    """A pending "Soll die Automation X gelöscht werden?" gate (HomeIntent
    V5 Teil 8/10, V5.28 "Automation Deletion") - the deletion counterpart to
    ``PendingAutomationConfirmation`` above, stored on ``ConversationContext``
    the same way and resolved through the exact same yes/no reply
    classification (``nlu/automation_confirmation.py``) - Regel 6, one
    confirmation vocabulary, not a second one for deletion.

    Carries the already-resolved ``AutomationSummary`` to delete (from a
    live ``AutomationExecutor.async_list_automations()`` read at match time,
    see ``engine.py``'s ``match_automation_delete()``) rather than just its
    ``automation_id`` - so the confirmation reply's own response text can
    still name the automation without a second read.
    """

    automation: AutomationSummary


@dataclass(frozen=True)
class PendingServiceConfirmation:
    """A high-risk lock/garage service call awaiting explicit consent."""

    plan: ServiceCallPlan
    success_text: str
    requested_by_user_id: str | None = None
    undo: "UndoPlan | None" = None


@dataclass(frozen=True)
class PendingAutomationDraft:
    """A valid spoken trigger waiting for the action in the next turn."""

    trigger: TriggerModel
    source_text: str


@dataclass(frozen=True)
class PendingAutomationEventClarification:
    """An event-notification draft waiting for the user's device choice
    ("Welche Rolllade im Büro meinst du?" -> "Die linke.").

    Nothing is persisted or executed; the answer only completes the same
    draft into an ordinary confirmation preview.
    """

    clarification: "EventClarification"
    requested_by_user_id: str | None = None


@dataclass(frozen=True)
class PendingAutomationActionEdit:
    """Selects an automation/action and gates the action-only replacement."""

    candidates: tuple[AutomationSummary, ...]
    automation: AutomationSummary | None = None
    rendered_actions: tuple[Mapping[str, Any], ...] = ()
    action_text: str | None = None
    operation: str = "replace"


@dataclass(frozen=True)
class PendingAutomationStructureEdit:
    """Select, preview and confirm a trigger/condition edit."""

    request: "AutomationStructureEditRequest"
    candidates: tuple[AutomationSummary, ...]
    automation: AutomationSummary | None = None
    rendered: tuple[Mapping[str, Any], ...] = ()
    edit_text: str | None = None
    ready: bool = False


@dataclass(frozen=True)
class PendingAutomationManagement:
    """A state-changing management operation awaiting explicit consent."""

    request: "AutomationManagementRequest"
    automation: AutomationSummary | None = None
    target: datetime | None = None
    candidates: tuple[AutomationSummary, ...] = ()


@dataclass(frozen=True)
class PendingCalendarEvent:
    """A calendar event being completed or awaiting final confirmation."""

    draft: "CalendarEventDraft"
    awaiting_confirmation: bool = False


@dataclass(frozen=True)
class PendingCalendarMutation:
    """One uniquely identified calendar event awaiting destructive consent."""

    kind: "CalendarManagementKind"
    event: "CalendarEventSummary | None" = None
    new_start_time: dt_time | None = None
    candidates: tuple["CalendarEventSummary", ...] = ()
    request: "CalendarManagementRequest | None" = None
    recurrence_scope: str | None = None  # this / all / future
    new_date: date | None = None
    new_title: str | None = None
    new_duration_minutes: int | None = None


@dataclass(frozen=True)
class PendingSemanticCommand:
    """A partially understood command waiting for a target, value or selection."""

    missing: str
    candidates: tuple[EntitySnapshot, ...]
    service_domain: str
    service: str
    data: dict
    response_verb: str
    requires_confirmation: bool = False
    value_kind: str | None = None


@dataclass(frozen=True)
class PendingProductivityCommand:
    """A list/timer command awaiting target selection or confirmation."""

    request: "ProductivityRequest"
    awaiting_confirmation: bool = False
    # A native timer start waiting for the user to name the timer.
    awaiting_timer_name: bool = False
    # Running timers the user must choose between (native timer labels).
    timer_choices: tuple[object, ...] = ()


@dataclass(frozen=True)
class PendingAutomationWizard:
    """Structured multi-turn creation dialog, never a free-form transcript."""

    state: "AutomationWizardState"


@dataclass(frozen=True)
class PendingAliasLearning:
    """One explicit local alias rule awaiting yes/no confirmation."""

    draft: "AliasLearningDraft"

# No TTL value is specified anywhere in the plan (only the test case name
# "context expiration", brain node 9ced4390, section 26). 30s covers a
# natural spoken follow-up ("Mach das Licht an." <pause> "Etwas heller.")
# without keeping stale state around for unrelated later commands in the
# same HA conversation_id. Revisit if Phase 26/27 wiring shows it's wrong.
DEFAULT_CONTEXT_TTL_SECONDS = 30.0
AUTOMATION_CONFIRMATION_TTL_SECONDS = 120.0


@dataclass(frozen=True)
class ConversationContext:
    """Per plan brain node e2946a55, section 24 - field names match exactly
    for the original four fields.

    V6 architecture plan, V6.12 ("Semantic Context", Brain node
    2a0871f8-05a4-4e0d-8cb6-80a8ff939480): the plan's minimum field list adds
    last_intent/last_action/last_target/last_query on top of the four fields
    above - deliberately **not** added here as separate fields, since all
    four are already fully recoverable from ``last_command`` (``.intent``,
    ``.parameters`` for query_command, etc. - see ``engine.py``'s
    ``match_query_followup()`` reading ``last_command.parameters.get(
    "query_command")`` today) and duplicating them here would be a second,
    driftable copy of the same state (Regel 6, "no parallel search/state
    system"). Only genuinely new, not-yet-tracked-anywhere information is
    added: ``last_floor``/``last_property``/``last_value``/``last_state``
    (the plan's remaining minimum-list fields) and ``pending_reference``
    (an unresolved *reference*, e.g. an ambiguous "die andere" with no
    matching candidate - distinct from ``pending_clarification``, which is
    entity-candidate ambiguity from Phase 25's ``ClarificationRequest``).

    All five are ``None``-defaulted and, same as ``SemanticFrame``'s V6.2
    primitive fields, not populated by any pipeline step yet - populating
    them is the Reasoning Engine's job (V6.25, later Wave), which is the
    first component that will actually compute a resolved property/value/
    state per turn. Every existing ``ConversationContext(...)`` call site
    uses keyword args (verified across conversation.py and all context/
    reference/query-followup tests), so this stays fully backward
    compatible.
    """

    last_command: SemanticCommand | None
    last_entities: tuple[EntitySnapshot, ...]
    last_area: AreaSnapshot | None
    pending_clarification: ClarificationRequest | None
    last_floor: FloorSnapshot | None = None
    last_property: SemanticProperty | None = None
    last_value: float | None = None
    last_state: SemanticState | None = None
    pending_reference: str | None = None
    # V5 Teil 7/10 (V5.23, "Dry Run") - a spoken automation awaiting its
    # "ja"/"nein" confirmation before it may ever be persisted to Home
    # Assistant (see PendingAutomationConfirmation's own docstring).
    pending_automation_confirmation: PendingAutomationConfirmation | None = None
    # V5 Teil 8/10 (V5.28, "Automation Deletion") - a spoken deletion request
    # awaiting its own "ja"/"nein" confirmation before anything is removed
    # from Home Assistant (see PendingAutomationDeletion's own docstring).
    pending_automation_deletion: PendingAutomationDeletion | None = None
    pending_service_confirmation: PendingServiceConfirmation | None = None
    # Explicit semantic focus for general follow-ups. This complements the
    # legacy last_* fields and avoids repeatedly reverse-engineering scope
    # and property from free-form parser parameters.
    focus: DialogFocus | None = None
    pending_automation_draft: PendingAutomationDraft | None = None
    pending_automation_event_clarification: PendingAutomationEventClarification | None = None
    pending_automation_action_edit: PendingAutomationActionEdit | None = None
    pending_automation_structure_edit: PendingAutomationStructureEdit | None = None
    pending_automation_management: PendingAutomationManagement | None = None
    pending_calendar_event: PendingCalendarEvent | None = None
    pending_calendar_mutation: PendingCalendarMutation | None = None
    pending_semantic_command: PendingSemanticCommand | None = None
    pending_productivity_command: PendingProductivityCommand | None = None
    pending_automation_wizard: PendingAutomationWizard | None = None
    pending_alias_learning: PendingAliasLearning | None = None
    pending_undo: "UndoPlan | None" = None
    # Read-only, non-command answers (for example "Spielt das Radio?") use
    # these fields to support "Läuft es noch?", "Und das andere?" and
    # "Woher weißt du das?" without manufacturing an executable command.
    last_query_predicate: str | None = None
    last_explanation: str | None = None
    memory: DialogTurnMemory | None = None
    discourse: DiscourseState | None = None


_PENDING_DIALOG_FIELDS: tuple[tuple[PendingDialogKind, str], ...] = (
    (PendingDialogKind.ALIAS_LEARNING, "pending_alias_learning"),
    (PendingDialogKind.AUTOMATION_WIZARD, "pending_automation_wizard"),
    (PendingDialogKind.PRODUCTIVITY, "pending_productivity_command"),
    (PendingDialogKind.CALENDAR_EVENT, "pending_calendar_event"),
    (PendingDialogKind.CALENDAR_MUTATION, "pending_calendar_mutation"),
    (PendingDialogKind.AUTOMATION_CONFIRMATION, "pending_automation_confirmation"),
    (PendingDialogKind.AUTOMATION_ACTION_EDIT, "pending_automation_action_edit"),
    (PendingDialogKind.AUTOMATION_STRUCTURE_EDIT, "pending_automation_structure_edit"),
    (PendingDialogKind.AUTOMATION_MANAGEMENT, "pending_automation_management"),
    (PendingDialogKind.AUTOMATION_DELETION, "pending_automation_deletion"),
    (PendingDialogKind.SERVICE_CONFIRMATION, "pending_service_confirmation"),
    (PendingDialogKind.SEMANTIC_COMMAND, "pending_semantic_command"),
    (PendingDialogKind.AUTOMATION_DRAFT, "pending_automation_draft"),
    (PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION, "pending_automation_event_clarification"),
    (PendingDialogKind.CLARIFICATION, "pending_clarification"),
)


def active_pending_dialog(
    context: ConversationContext | None,
) -> ActivePendingDialog | None:
    """Return the deterministic active state without inspecting user text.

    Context constructors should normally set one pending field.  Historical
    states can contain more; those are surfaced in ``conflicting_kinds`` and
    resolved by the documented state priority instead of accidental chains
    of ``if`` statements spread through the conversation router.
    """
    if context is None:
        return None
    found = tuple(
        (kind, payload)
        for kind, field_name in _PENDING_DIALOG_FIELDS
        if (payload := getattr(context, field_name)) is not None
    )
    if not found:
        return None
    kind, payload = found[0]
    return ActivePendingDialog(
        kind=kind,
        payload=payload,
        conflicting_kinds=tuple(other_kind for other_kind, _ in found[1:]),
    )


class ConversationContextStore:
    """In-memory ``ConversationContext`` storage keyed by HA's
    ``conversation_id`` (already available as ``user_input.conversation_id``
    in ``conversation.py``, just not consumed yet), with TTL-based expiration
    so an old, unrelated context never leaks into a much later turn.

    Not thread-safety-hardened beyond what HA's single-event-loop model
    already guarantees (same assumption the rest of this integration makes -
    no other module here uses locks either).
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_CONTEXT_TTL_SECONDS,
        automation_confirmation_ttl_seconds: float = AUTOMATION_CONFIRMATION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._automation_confirmation_ttl_seconds = automation_confirmation_ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, float, ConversationContext]] = {}
        self._dialog_listener: Callable[[str, ActivePendingDialog | None], None] | None = None

    def bind_dialog_listener(
        self, listener: Callable[[str, ActivePendingDialog | None], None] | None
    ) -> None:
        """Bind the central dialog owner to every pending-state mutation."""
        self._dialog_listener = listener
        if listener is not None:
            for conversation_id, (_, _, context) in self._entries.items():
                listener(conversation_id, active_pending_dialog(context))

    def get(self, conversation_id: str) -> ConversationContext | None:
        entry = self._entries.get(conversation_id)
        if entry is None:
            return None
        stored_at, ttl_seconds, context = entry
        if self._clock() - stored_at > ttl_seconds:
            del self._entries[conversation_id]
            if self._dialog_listener is not None:
                self._dialog_listener(conversation_id, None)
            return None
        return context

    def set(self, conversation_id: str, context: ConversationContext) -> None:
        ttl_seconds = (
            self._automation_confirmation_ttl_seconds
            if context.pending_automation_confirmation is not None
            or context.pending_automation_deletion is not None
            or context.pending_service_confirmation is not None
            or context.pending_automation_draft is not None
            or context.pending_automation_event_clarification is not None
            or context.pending_automation_action_edit is not None
            or context.pending_automation_structure_edit is not None
            or context.pending_automation_management is not None
            or context.pending_calendar_event is not None
            or context.pending_calendar_mutation is not None
            or context.pending_semantic_command is not None
            or context.pending_productivity_command is not None
            or context.pending_automation_wizard is not None
            or context.pending_alias_learning is not None
            else self._ttl_seconds
        )
        self._entries[conversation_id] = (self._clock(), ttl_seconds, context)
        if self._dialog_listener is not None:
            self._dialog_listener(conversation_id, active_pending_dialog(context))

    def clear(self, conversation_id: str) -> None:
        self._entries.pop(conversation_id, None)
        if self._dialog_listener is not None:
            self._dialog_listener(conversation_id, None)
