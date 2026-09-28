"""Conversation platform for the homeintent integration.

A deterministic, LLM-free Assist conversation agent: hassil matches the
sentence structure, ``entities.resolve_entity`` resolves the name, and a
match either executes exactly one Home Assistant service call or - on any
kind of mismatch (no template match, ambiguous/unknown name, wrong domain) -
returns a fixed "not understood" response. No fallback, no scoring; see the
project plan ("predictability over coverage") for why.

Deliberately does not use ``chat_log``/``homeassistant.helpers.llm`` at all -
there is no model round-trip here, so the response is built directly as an
``intent.IntentResponse``, the same way HA core's own built-in (hassil-based)
default conversation agent does it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal, Mapping, Sequence

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .automation_grounding import looks_like_selection_reply
from .automation_executor import AutomationExecutor
from .alias_learning import (
    AliasLearningDraft,
    parse_alias_learning,
)
from .agent_action_policy import validate_agent_service_plan
from .automation_action_edit import (
    action_edit_operation,
    homeintent_candidates,
    is_action_edit_request,
    reordered_actions,
    select_candidate_reply,
)
from .automation_scenarios import interpret_downstairs_shutdown
from .automation_management import (
    AutomationManagementKind,
    AutomationManagementRequest,
    READ_ONLY_MANAGEMENT_KINDS,
    format_scheduled_time,
    parse_automation_management,
    select_automation_management,
)
from .automation_simulation import render_automation_simulation
from .automation_structure_edit import (
    AutomationEditOperation,
    AutomationStructureEditRequest,
    parse_automation_structure_edit,
)
from .automation_wizard import (
    AutomationWizardStage,
    AutomationWizardState,
    parse_lifetime,
    starts_automation_wizard,
)
from .advanced_queries import match_advanced_query
from .audit_log import render_today
from .calendar_event import (
    CalendarEventDraft,
    build_calendar_event_service_call,
    calendar_event_question,
    render_calendar_event_preview,
    start_calendar_event_draft,
    update_calendar_event_draft,
    writable_calendars,
)
from .calendar_management import CalendarManagementRequest
from .capability_audit import match_capability_audit_query
from .comfort_intent import is_comfort_request
from .conversation_location import (
    materialize_local_reference,
    resolve_conversation_area,
)
from .const import (
    CONF_ALLOW_NON_ADMIN_AUTOMATIONS,
    CONF_CUSTOM_ALIASES,
    CONF_HOUSE_RELATIONS,
    DOMAIN,
    NOT_UNDERSTOOD_TEXT,
)
from .dialog_manager import DialogPriority, DialogTaskKind
from .document_intent import interpret_document_search
from .device_result import DeviceControlResult
from .engine import (
    AutomationClarificationResult,
    AutomationDraftMatchResult,
    AutomationDeletionMatchResult,
    AutomationMatchResult,
    AutomationToggleMatchResult,
    CommandPlan,
    MatchResult,
    _AUTOMATION_DELETE_RE,
    _AUTOMATION_DISABLE_RE,
    _AUTOMATION_ENABLE_RE,
    _AUTOMATION_QUERY_RE,
)
from .entities import EntitySnapshot, normalize_for_compare
from .hass_entities import build_device_snapshots, build_entity_snapshots
from .history_query import (
    ComparativeHistoryQuery,
    HistoryQuery,
    StateHistoryQuery,
    async_execute_history_query,
    async_get_transition_evidence,
    parse_history_query,
)
from .household_query import match_household_query
from .management_dialogs import (
    async_handle_automation_action_edit_turn,
    async_handle_automation_structure_edit_turn,
    async_prepare_automation_structure_edit,
    async_handle_calendar_management,
    async_handle_calendar_mutation_confirmation,
    store_automation_action_edit,
    store_automation_structure_edit,
)
from .house_graph import FactProvenance, HouseGraph, parse_relation_specs
from .goal_intent import interpret_goal
from .goal_model import (
    DesiredState,
    GoalKind as V10GoalKind,
    GoalScope,
    GoalSemanticChoice,
    PendingGoalSemanticClarification,
    TemporalGoal,
)
from .goal_run import (
    GoalRun,
    GoalRunClarification,
    GoalRunQuery,
    explain_goal_run,
)
from .memory import MemoryKind
from .memory_intent import MemoryOperation, interpret_memory_intent
from .learning_intent import LearningOperation, interpret_learning_request
from .learning_dialog import LearningDialogOperation, LearningDialogPayload
from .model_registry import (
    LearnedKind,
    LearnedModel,
    ModelHealth,
    explain_learned_model,
)
from .learning_policy import KnowledgeState
from .learning_control import (
    LearningControlError,
    async_accept_habit,
    async_confirm_preference,
    async_forget_model,
    async_reject_habit,
    async_reject_preference,
    async_reset_models,
    model_owner,
    options_without_preference_aliases,
)
from .preferences import (
    LearnedPreference, PreferenceContext, resolve_preferences,
)
from .predictive_house_model import PredictiveHouseModel
from .management_understanding import understand_management
from .proactive_dialog import V12_TASK_KINDS
from .proactive_session import classify_proposal_reply
from .room_presence import build_area_lookup
from .planner import (
    Goal,
    GoalKind,
    MaterializedPlan,
    PlanExecutor,
    PlanResult,
    PlanStatus,
    StepKind,
    effect_satisfied,
    materialize_goal,
    materialize_comfort_profile,
    materialize_routine,
    goal_run_from_plan_result,
)
from .adaptive_planning import AdaptivePlanningAdvice, advise_deadline_goal
from .thermal_deadline import (
    PendingThermalCheckpointStore,
    ThermalCheckpointPhase,
    ThermalDeadlineCheckpoint,
    append_start_checkpoint,
    checkpoint_automation_config,
)
from .thermal_question import answer_thermal_question
from .nlu.primitives import SemanticProperty
from .nlu.clock_language import normalize_clock_expressions, wake_request
from .nlu.semantic_exclusion import canonical_exception_words
from .nlu.normalize import expand_clitics
from .nlu.german_morphology import dative_location_phrase
from .nlu.place_model import build_place_lexicon
from .nlu.device_ontology import analyse_word, lookup_genus_word
from .nlu.target_resolution import genus_members
from .nlu.situation_views import answer_situation_view
from .nlu.utterance_meaning import render_maintain
from .nlu.german_morphology import counted_passive
from .nlu.unit_reasoning import normalize_measurement
from .monitor_goal import MonitorRecord
from .nlu.temporal_semantics import resolve_history_window, resolve_scheduled_datetime
from .plan_modification import apply_plan_modification
from .profiles import ComfortProfile, RoutineDefinition, RoutineStepDefinition
from .user_context import BindingStatus
from .procedure_intent import ProcedureOperation, interpret_procedure_intent
from .routine_intent import interpret_routine_feedback
from .nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from .nlu.action_model import (
    ActionGroup,
    ActionModel,
    ActionType,
    NotificationRecipient,
    NotificationRecipientKind,
)
from .agent_delivery import AgentDelivery
from .notification_request import NotificationRequest, async_deliver_notification_request
from .notification_target import (
    named_notification_targets,
    NotificationTargetResolver,
    resolution_failure_text,
)
from .nlu.automation_model import (
    AutomationModel,
    CalendarReference,
    CalendarSchedule,
    TriggerModel,
    TriggerTarget,
    TriggerType,
    resolve_pending_schedule,
)
from .nlu.automation_validator import validate_automation
from .nlu.automation_preview import render_automation_preview
from .nlu.context import (
    active_pending_dialog,
    ConversationContext,
    DialogTurnMemory,
    PendingAutomationConfirmation,
    PendingAutomationDeletion,
    PendingAutomationDraft,
    PendingAutomationEventClarification,
    PendingAutomationActionEdit,
    PendingAutomationManagement,
    PendingAutomationStructureEdit,
    PendingAutomationWizard,
    PendingAliasLearning,
    PendingCalendarEvent,
    PendingCalendarMutation,
    PendingServiceConfirmation,
    PendingSemanticCommand,
    PendingProductivityCommand,
    PendingDialogKind,
)
from .nlu.dialog_focus import DialogFocus, derive_dialog_focus
from .nlu.discourse import DiscourseRole, remember_entities, remember_query_group
from .nlu.query_command import QueryResult
from .nlu.entity_clarification import (
    CandidateReplyKind,
    render_candidate_question,
    resolve_candidate_reply,
)
from .nlu.explanation import explain_command, is_explanation_request
from .nlu.ha_automation_generator import (
    GenerationError,
    generate_ha_automation_config,
    resolve_automation_action_entity_ids,
)
from .nlu.language_frontend import LanguageDocument, analyse_language
from .nlu.response_generator import _automation_label
from .nlu.semantic_utterance import (
    Modality,
    SpeechAct,
    analyse_utterance,
    is_contextual_followup,
)
from .nlu.understanding import UnderstandingAuthority
from .nlu.understanding_context import UnderstandingContext
from .service_call import QUERY_INTENTS, ServiceCallPlan
from .service_executor import async_execute_service_plan
from .effect_graph import build_plan_effects, is_composite_entity, summarize_effects
from .semantic_dialog import continue_semantic_dialog, start_semantic_dialog
from .reminder import (
    reminder_automation_text,
    reminder_quiet_hours,
    reminder_recipient,
)
from .security_control import (
    conversation_user_id,
    match_alarm_control,
    user_display_name,
    user_is_admin,
)
from .nlu.word_cues import has_word
from .extended_device_query import match_extended_device_query
from .execution_policy import (
    PolicyOutcome,
    evaluate_service_plan,
    validate_automation_action_targets,
)
from .world_model import WorldModel, build_world_model as assemble_world_model
from .undo import UndoPlan, build_undo_plan, is_undo_request
from .runtime_data import HomeIntentRuntimeData
from .execution_context import begin_turn, call_context, end_turn
from .execution_trace import (
    CauseExplanation,
    ContextIndex,
    Evidence,
    ExecutionTraceStore,
    explain_change,
)
from .nlu.causal_question import interpret_cause_question
from .nlu.recurrence import (
    Recurrence,
    answer_recurrence,
    is_conditional,
    recurrence_of,
    trigger_kinds,
)
from .bindings import BindingKind, BindingScope
from .conversation_learning import DialogLearningMixin, is_known_device_word
from .arbitration import DecisionKind, arbitrate
from .arbitration_candidates import need_query_candidates
from .dialog_learning import MEMORY_DISABLED_TEXT, alias_rejection, unknown_device_noun
from .nlu.need_semantics import ROUTINE_CONCEPTS, routine_concept_by_key
from .routine_binding_intent import (
    RoutineBindingOperation,
    RoutineBindingRequest,
    choose_candidate,
    interpret_routine_binding,
)


@dataclass(frozen=True)
class RoutineSelection:
    """Open question "Welche Routine meinst du: A, B oder C?"."""

    concept_key: str
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class RoutineBindConfirmation:
    """Open question "Soll ich für „…“ künftig X nehmen?"."""

    concept_key: str
    entity_id: str
    personal: bool = False


@dataclass(frozen=True)
class _ScheduledOutcome:
    executed: bool
    error: str | None = None
from .productivity import (
    ProductivityRequest,
    TimerOperation,
    TimerRequest,
    TodoOperation,
    TodoRequest,
    format_duration,
    parse_productivity_request,
    select_productivity_candidate,
    timer_choice_ordinal,
    timer_name_reply,
)
from .native_timer import (
    NativeTimerInfo,
    describe_timers,
    join_timer_labels,
    match_timer_name,
)
from .phonetic_correction import PhoneticSuggestion, phonetic_suggestions

_LOGGER = logging.getLogger(__name__)

# Universal way out of any open follow-up question (F10). Matched against
# the normalized utterance as a whole, so "Vergiss meine Vorliebe" is not a
# cancellation.
def _already_done_text(pending: ConversationContext | None) -> str | None:
    """Honest answer to a withdrawal right after a reversible action ran."""
    if pending is None or pending.pending_undo is None or not pending.last_entities:
        return None
    names = [entity.friendly_name for entity in pending.last_entities[:4]]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
    return (
        f"Das habe ich schon ausgeführt ({listed}); abbrechen geht nicht mehr. "
        "Sag „Mach das rückgängig“, dann nehme ich es zurück."
    )


_UNIVERSAL_CANCEL_RE = re.compile(
    r"\s*(?:(?:nein\s*,?\s*)?(?:lass\s+(?:das|es|gut\s+sein)|abbrechen|abbruch|brich\s+ab|"
    r"stopp|stop|vergiss\s+(?:es|das)|egal|schon\s+gut|nicht\s+mehr\s+noetig|"
    r"nicht\s+mehr\s+nötig)(?:\s+bitte)?)[.!]?\s*"
)
_MEMORY_KIND_DE = {
    "episode": "Ereignisse", "household_fact": "Haushaltsfakten",
    "preference": "Vorlieben", "procedure": "Abläufe", "decision": "Entscheidungen",
    "routine_grant": "Routinen-Freigaben",
}


_MEMORY_KIND_SINGULAR_DE = {
    "episode": "Ereignis", "household_fact": "Haushaltsfakt", "preference": "Vorliebe",
    "procedure": "Ablauf", "decision": "Entscheidung", "routine_grant": "Routinen-Freigabe",
}


def _describe_memory(record: Any, labels: Mapping[str, str]) -> str:
    """One remembered record in plain German, without internal keys."""
    content = record.content if isinstance(record.content, Mapping) else {}
    entity_name = labels.get(str(content.get("entity_id", "")), "")
    percent = content.get("brightness_percent")
    activity = content.get("activity")
    if record.kind.value == "preference":
        situation = " beim Fernsehen" if activity == "television" else ""
        if isinstance(percent, int) and entity_name:
            return f"Vorliebe{situation}: {entity_name} auf {percent} Prozent"
        if isinstance(percent, int):
            return f"Vorliebe{situation}: Helligkeit {percent} Prozent"
        if entity_name:
            return f"Vorliebe{situation}: {entity_name}"
        return "eine Vorliebe ohne Details"
    text = content.get("text") or content.get("summary") or content.get("fact")
    kind = _MEMORY_KIND_SINGULAR_DE.get(record.kind.value, "Eintrag")
    return f"{kind}: {text}" if isinstance(text, str) and text else f"ein Eintrag ({kind})"


# How long a spoken plan confirmation may wait for the plan's verified
# result before replying and continuing in the background (F17).
_PLAN_REPLY_BUDGET_SECONDS = 2.0

# Open questions whose expected answer is itself a command (a routine being
# defined step by step, an automation action): a command answers them.
_COMMAND_ANSWER_TASK_KINDS = frozenset({
    DialogTaskKind.ROUTINE_DEFINITION,
    DialogTaskKind.AUTOMATION,
})
_NO_CONDITION_RE = re.compile(
    r"(?:keine|keins|nein\s*,?\s*keine|ohne|keine\s+(?:bedingung|bedingungen)|"
    r"ohne\s+(?:bedingung|bedingungen)|keine\s+weitere(?:n)?(?:\s+bedingung(?:en)?)?|"
    r"nichts|brauche\s+ich\s+nicht)"
)


# Single source of truth for "which intents are queries" (V4.2) - read state
# and speak, never call a service - so Assist shows them as a QUERY_ANSWER
# rather than the default ACTION_DONE.
QUERY_INTENT_NAMES = frozenset(QUERY_INTENTS)

# V5 Teil 7/10 (V5.23/V5.26) - the confirmation dialog's own fixed spoken
# replies, same "small closed vocabulary" precedent NOT_UNDERSTOOD_TEXT
# already sets in const.py.
AUTOMATION_CREATED_TEXT = "Automation wurde erstellt."
AUTOMATION_CANCELLED_TEXT = "Abgebrochen. Die Automation wurde nicht erstellt."
AUTOMATION_CONFIRMATION_UNCLEAR_TEXT = (
    "Das habe ich nicht verstanden. Soll die Automation erstellt werden? "
    "Bitte antworte mit Ja oder Nein."
)

# V5 Teil 8/10 (V5.28, "Automation Deletion") - same "small closed
# vocabulary" precedent as the creation texts above, mirrored one-to-one for
# the deletion confirmation dialog.
AUTOMATION_DELETED_TEXT = "Automation wurde gelöscht."
AUTOMATION_DELETION_CANCELLED_TEXT = "Abgebrochen. Die Automation wurde nicht gelöscht."
AUTOMATION_DELETION_CONFIRMATION_UNCLEAR_TEXT = (
    "Das habe ich nicht verstanden. Soll die Automation gelöscht werden? "
    "Bitte antworte mit Ja oder Nein."
)

# HomeIntent V5 Teil 8/10 (Wave 11, "Automation Disable/Enable") - no
# confirmation-round-trip vocabulary here (see ``AutomationToggleMatchResult``'s
# own docstring for why): a single-match toggle executes immediately, so
# only an error text is needed here, mirroring the ordinary command path's
# own ``FAILED_TO_HANDLE`` wording.

# One spoken sentence per GenerationError member (nlu/ha_automation_generator.py) -
# every one of these is a "niemals raten" refusal Regel 4 already established
# one stage earlier (automation_validator.py); a validate_automation()-clean
# model can still hit one of these at confirmation time (e.g. the target
# entity disappeared between preview and "ja", or the model uses a
# TriggerType/ConditionType/ActionType the validator itself never rejects
# but the HA-native schema has no faithful translation for - see that
# module's own docstring for the full "why" per member).
_GENERATION_ERROR_SPOKEN_DE = {
    GenerationError.ENTITY_NOT_FOUND: (
        "Das passende Gerät wurde nicht mehr gefunden. Die Automation wurde nicht erstellt."
    ),
    GenerationError.UNSUPPORTED_STATE: (
        "Dieser Zustand lässt sich für dieses Gerät nicht in eine Automation übersetzen. "
        "Die Automation wurde nicht erstellt."
    ),
    GenerationError.UNSUPPORTED_TRIGGER_TYPE: (
        "Dieser Auslöser wird von Home Assistant nicht unterstützt. Die Automation wurde nicht erstellt."
    ),
    GenerationError.UNSUPPORTED_CONDITION_TYPE: (
        "Diese Bedingung wird von Home Assistant nicht unterstützt. Die Automation wurde nicht erstellt."
    ),
    GenerationError.UNSUPPORTED_ACTION_TYPE: (
        "Diese Aktion wird von Home Assistant nicht unterstützt. Die Automation wurde nicht erstellt."
    ),
    GenerationError.NOTIFY_RECIPIENT_UNRESOLVED: (
        "Für diese Benachrichtigung fehlt ein eindeutiges Push-Ziel. "
        "Die Automation wurde nicht erstellt."
    ),
}


# Past-tense or passive endings of service_call.py's response texts and the
# infinitive a confirmation question needs ("Burgtor wird geöffnet" ->
# "Soll ich wirklich Burgtor öffnen?"). Longer endings come first.
_CONFIRMATION_INFINITIVES: tuple[tuple[str, str], ...] = (
    ("wird geöffnet", "öffnen"),
    ("wird geschlossen", "schließen"),
    ("aufgeschlossen", "aufschließen"),
    ("abgeschlossen", "abschließen"),
    ("eingeschaltet", "einschalten"),
    ("ausgeschaltet", "ausschalten"),
    ("geschlossen", "schließen"),
    ("ausgeführt", "ausführen"),
    ("geöffnet", "öffnen"),
    ("gedrückt", "drücken"),
    ("aktiviert", "aktivieren"),
    ("gestartet", "starten"),
    ("gestoppt", "stoppen"),
)


def _with_effect_summary(text: str, execution: Any) -> str:
    """„Gute Nacht ausgeführt: 9 Rollläden.“ after a script/scene/group."""
    effects = getattr(getattr(execution, "decision", None), "effects", None)
    summary = summarize_effects(effects) if effects is not None else None
    if not summary or not text:
        return text
    return f"{text.rstrip().rstrip('.')}: {summary}."


def _confirmation_question(response_text: str, note: str | None = None) -> str:
    """Turn a device response text into a grammatical safety question.

    ``note`` is the policy's hint (an unverifiable script step, possible
    follow-up automations); it is said before the question.
    """
    text = response_text.rstrip(".")
    for ending, infinitive in _CONFIRMATION_INFINITIVES:
        if text.endswith(f" {ending}"):
            text = f"{text[: -len(ending)]}{infinitive}"
            break
    question = f"Soll ich wirklich {text}?"
    return f"{note} {question}" if note else question


def _helper_timer_seconds_left(entity: EntitySnapshot) -> int | None:
    """Remaining seconds of a ``timer.*`` helper.

    Home Assistant refreshes ``remaining`` only when a timer is paused, so a
    running timer is measured against its ``finishes_at`` timestamp.
    """
    if entity.state == "active":
        finishes_at = entity.attributes.get("finishes_at")
        if isinstance(finishes_at, str):
            try:
                end = datetime.fromisoformat(finishes_at)
            except ValueError:
                end = None
            if end is not None and end.tzinfo is not None:
                return max(0, round((end - datetime.now(end.tzinfo)).total_seconds()))
    for key in ("remaining", "duration"):
        value = entity.attributes.get(key)
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})\s*", value)
            if match:
                hours, minutes, seconds = (int(part) for part in match.groups())
                return hours * 3600 + minutes * 60 + seconds
    return None


def _with_session_conversation_id(
    user_input: conversation.ConversationInput, chat_log: object
) -> conversation.ConversationInput:
    """Key a turn without caller-supplied id by Home Assistant's chat session.

    The REST API and ``conversation.process`` may pass ``conversation_id=None``
    while Home Assistant still opens a chat session with its own id. All dialog
    state here is keyed by conversation id, so without this fallback every such
    caller shared one state and an open question of one caller captured the
    next command of another.
    """
    if user_input.conversation_id:
        return user_input
    session_id = getattr(chat_log, "conversation_id", None)
    if not isinstance(session_id, str) or not session_id:
        return user_input
    return replace(user_input, conversation_id=session_id)


def _is_complete_actionable_understanding(
    payload: MatchResult | CommandPlan | None,
) -> bool:
    """Whether a fresh V7 turn is complete enough to replace a dialog."""
    if isinstance(payload, MatchResult):
        return payload.plan is not None and payload.clarification is None
    if isinstance(payload, CommandPlan):
        return bool(payload.commands) and all(
            command.clarification is None for command in payload.commands
        ) and any(command.plan is not None for command in payload.commands)
    return False


def _dialog_manager_kind(
    kind: PendingDialogKind,
) -> tuple[DialogTaskKind, DialogPriority]:
    """Map every historical pending state onto the central coordinator."""
    if kind is PendingDialogKind.CLARIFICATION:
        return DialogTaskKind.ENTITY_SELECTION, DialogPriority.SELECTION
    if kind is PendingDialogKind.SERVICE_CONFIRMATION:
        return DialogTaskKind.SAFETY_CONFIRMATION, DialogPriority.SAFETY
    if kind in {
        PendingDialogKind.AUTOMATION_CONFIRMATION,
        PendingDialogKind.AUTOMATION_DELETION,
        PendingDialogKind.CALENDAR_MUTATION,
        PendingDialogKind.AUTOMATION_MANAGEMENT,
        PendingDialogKind.ALIAS_LEARNING,
    }:
        return DialogTaskKind.SAFETY_CONFIRMATION, DialogPriority.CONFIRMATION
    if kind in {
        PendingDialogKind.AUTOMATION_DRAFT,
        PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION,
        PendingDialogKind.AUTOMATION_ACTION_EDIT,
        PendingDialogKind.AUTOMATION_STRUCTURE_EDIT,
        PendingDialogKind.AUTOMATION_WIZARD,
    }:
        return DialogTaskKind.AUTOMATION, DialogPriority.FOLLOWUP
    return DialogTaskKind.MISSING_SLOT, DialogPriority.FOLLOWUP


# Pending dialog kinds after which a voice satellite should keep listening.
#
# Every kind currently defined means "a question was spoken and the user's
# answer is expected next" - each payload dataclass in nlu/context.py says so
# in its own docstring ("awaiting explicit consent", "waiting for a target,
# value or selection", 'A pending "Soll diese Automation erstellt werden?"').
# So this set is, today, all of PendingDialogKind.
#
# They are still listed one by one rather than derived from the enum: a future
# kind that parks state *without* having asked anything would otherwise be
# opted in silently and hold satellite microphones open after an ordinary
# command. Membership here is the claim "we just asked something out loud",
# and that claim should be made deliberately per kind.
_CONTINUE_CONVERSATION_KINDS: frozenset[PendingDialogKind] = frozenset(
    {
        PendingDialogKind.ALIAS_LEARNING,
        PendingDialogKind.AUTOMATION_WIZARD,
        PendingDialogKind.PRODUCTIVITY,
        PendingDialogKind.CALENDAR_EVENT,
        PendingDialogKind.CALENDAR_MUTATION,
        PendingDialogKind.AUTOMATION_CONFIRMATION,
        PendingDialogKind.AUTOMATION_ACTION_EDIT,
        PendingDialogKind.AUTOMATION_STRUCTURE_EDIT,
        PendingDialogKind.AUTOMATION_MANAGEMENT,
        PendingDialogKind.AUTOMATION_DELETION,
        PendingDialogKind.SERVICE_CONFIRMATION,
        PendingDialogKind.SEMANTIC_COMMAND,
        PendingDialogKind.AUTOMATION_DRAFT,
        PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION,
        PendingDialogKind.CLARIFICATION,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the conversation entity for this config entry."""
    async_add_entities([NluConversationEntity(config_entry)])


_GENERIC_UNKNOWN_TARGET = "Ich habe die Aktion erkannt, aber kein eindeutig passendes"


class NluConversationEntity(
    DialogLearningMixin, conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """Deterministic, rule-based Assist conversation agent (German, v1)."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="homeintent (local, deterministic)",
            model="hassil v1",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        runtime = getattr(entry, "runtime_data", None)
        if not isinstance(runtime, HomeIntentRuntimeData):
            # Direct unit construction and upgrades from an older entry do
            # not necessarily pass through integration setup first.
            runtime = HomeIntentRuntimeData()
            try:
                entry.runtime_data = runtime
            except AttributeError:
                pass
        self._engine = runtime.engine
        self._context_store = runtime.context_store
        self._audit_trail = runtime.audit_trail
        self._runtime_data = runtime
        # Last todo list per conversation, for follow-ups without a list name.
        self._last_todo_lists: dict[str, str] = {}
        # Rebuilt every turn in _async_handle_message() (World Model Wave,
        # 2026-08-14); None only until the first turn.
        self._world_model: WorldModel | None = None
        self._house_graph: HouseGraph | None = None
        # Lazily constructed on first use in _async_handle_message() (V5.26)
        # rather than here: self.hass isn't set yet at __init__ time (HA's
        # entity platform assigns it before async_added_to_hass() runs, not
        # before __init__()). Built once and kept for this entity's whole
        # lifetime, not per-turn - its asyncio.Lock() must persist across
        # turns to actually guard automations.yaml's read-modify-write cycle
        # against two concurrent confirmations (see AutomationExecutor's own
        # docstring).
        self._automation_executor: AutomationExecutor | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        # v1 intent YAMLs are German-only (see intents/de/).
        return ["de"]

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Answer the turn, then tell the client whether to keep listening.

        A voice satellite closes its microphone after every reply unless the
        result carries ``continue_conversation``. Without it, a spoken
        rückfrage ("Welchen Rollladen meinst du?") forces the user to say the
        wake word again before answering - which defeats every multi-turn
        dialog this agent builds.

        The flag is derived in this one place instead of at the ~130
        ``ConversationResult(...)`` construction sites below, because "is a
        question outstanding?" is already modelled exactly once, by
        ``active_pending_dialog()``. That reads only the explicit ``pending_*``
        state-machine fields. The follow-up context (``last_area``,
        ``last_command``, ``focus``) is deliberately *not* consulted: it is
        populated after every successful command, so keying off it would hold
        satellite microphones open after a plain "Mach das Licht an".
        """
        user_input = _with_session_conversation_id(user_input, chat_log)
        proactive = self._runtime_data.proactive_context
        if proactive is not None:
            # An authenticated turn on a mapped satellite is short-lived room
            # evidence; an anonymous turn is never identity evidence.
            proactive.record_authenticated_turn(
                conversation_user_id(user_input), getattr(user_input, "device_id", None)
            )
        # One Home Assistant context per turn: every execution in this turn
        # shares one execution id (7.3.2).
        turn = begin_turn(user_input, conversation_user_id(user_input), user_input.text)
        self._current_chat_log = chat_log
        try:
            result = await self._async_handle_message_inner(user_input, chat_log)
        finally:
            end_turn(turn)
        suffix = self._take_turn_suffix(user_input.conversation_id)
        if suffix:
            # "Soll ich mir … merken?" after an executed command (7.4.1).
            speech = result.response.speech
            spoken = (
                speech.get("plain", {}).get("speech", "")
                if isinstance(speech, dict) else str(speech or "")
            )
            result.response.async_set_speech(f"{spoken} {suffix}".strip())
        self._apply_continue_conversation(user_input, result)
        return result

    def _decide_recurrence(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationMatchResult,
        entities: list[EntitySnapshot],
        pending: ConversationContext | None,
    ) -> AutomationMatchResult | conversation.ConversationResult:
        """Once or recurring - never guessed (7.3.3, Q5)."""
        model = result.model
        if result.validation_error is not None or model.once or model.max_runs is not None:
            return result
        kinds = trigger_kinds(model.triggers)
        recurrence = recurrence_of(user_input.text)
        if recurrence is Recurrence.RECURRING or not kinds or not kinds <= {"TIME", "SUN"}:
            return result
        conditional = is_conditional(user_input.text)
        time_triggers = [item for item in model.triggers if item.time_hour is not None]
        if kinds == {"TIME"} and not conditional and len(time_triggers) == 1 == len(model.triggers):
            # "Schalte um 22 Uhr das Licht aus": a one-time command at the
            # next occurrence of that clock time, not a daily automation.
            trigger = time_triggers[0]
            hour, minute = trigger.time_hour or 0, trigger.time_minute or 0
            once_model = replace(
                model,
                triggers=(TriggerModel(type=TriggerType.CALENDAR_TIME),),
                once=True,
                calendar_schedule=CalendarSchedule(
                    CalendarReference.NEXT_OCCURRENCE, hour, minute,
                    spoken=f"um {hour:02d}:{minute:02d} Uhr",
                ),
            )
            validation = validate_automation(once_model)
            if validation is None:
                return replace(result, model=once_model, validation_error=None)
        if recurrence is Recurrence.ONCE:
            return replace(result, model=replace(model, max_runs=1))
        self._runtime_data.dialog_manager.create(
            user_input.conversation_id,
            "recurrence-choice",
            DialogTaskKind.RECURRENCE_CHOICE,
            DialogPriority.SELECTION,
            reason="Ob ein Auftrag einmalig oder wiederkehrend gilt, rate ich nicht.",
            requested_by_user_id=conversation_user_id(user_input),
            payload=result,
        )
        preview = render_automation_preview(model, entities)
        understood = preview.removesuffix("Soll diese Automation erstellt werden?").strip()
        response.async_set_speech(f"{understood} Nur heute oder jeden Tag?".strip())
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_recurrence_choice(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        manager = self._runtime_data.dialog_manager
        if not isinstance(task.payload, AutomationMatchResult):
            return None
        if task.requested_by_user_id not in {None, conversation_user_id(user_input)}:
            response.async_set_speech("Diese Rückfrage gehört zu einem anderen Benutzer.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        answer = answer_recurrence(user_input.text)
        if classify_confirmation_reply(user_input.text) is ConfirmationReply.NO:
            manager.cancel(user_input.conversation_id, task.task_id)
            response.async_set_speech("In Ordnung, ich lege nichts an.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if answer is Recurrence.UNSPECIFIED:
            if len(user_input.text.split()) > 4:
                manager.cancel(user_input.conversation_id, task.task_id)
                return None
            response.async_set_speech("Bitte sag „nur heute“ oder „jeden Tag“.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        manager.cancel(user_input.conversation_id, task.task_id)
        result = task.payload
        if answer is Recurrence.ONCE:
            result = replace(result, model=replace(result.model, max_runs=1))
        return self._handle_automation_match_result(user_input, response, result, entities)

    def _routine_bindings_for(self, user_id: str | None) -> dict[str, str]:
        """Concept -> bound script/scene for this speaker (own before household)."""
        store = self._runtime_data.bindings
        found: dict[str, str] = {}
        for concept in ROUTINE_CONCEPTS:
            binding = store.find(BindingKind.ROUTINE, concept.key, user_id)
            if binding is not None:
                found[concept.key] = binding.target
        return found

    async def _async_store_routine_binding(
        self, concept_key: str, entity_id: str, user_id: str | None, *, personal: bool = False
    ) -> str:
        """Store after an explicit "Ja"/choice; returns the spoken note."""
        concept = routine_concept_by_key(concept_key)
        await self._runtime_data.bindings.async_bind(
            BindingKind.ROUTINE,
            concept_key,
            entity_id,
            confirmed=True,
            scope=BindingScope.USER if personal and user_id else BindingScope.HOUSEHOLD,
            user_id=user_id if personal else None,
            created_by=user_id,
            now=dt_util.now(),
        )
        self._runtime_data.learning_center_revision.bump()
        label = concept.label if concept is not None else concept_key
        return f"Das merke ich mir für „{label}“."

    async def _async_handle_routine_binding_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: RoutineBindingRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """"Vergiss die Schlafroutine" / "Schlafen ist ab jetzt das Skript X"."""
        user_id = conversation_user_id(user_input)
        store = self._runtime_data.bindings
        label = request.concept.label
        if request.operation is RoutineBindingOperation.SHOW:
            binding = store.find(BindingKind.ROUTINE, request.concept.key, user_id)
            name = (
                next(
                    (item.friendly_name for item in entities if item.entity_id == binding.target),
                    binding.target,
                )
                if binding is not None else None
            )
            response.async_set_speech(
                f"Für „{label}“ nehme ich {name}." if name
                else f"Für „{label}“ ist noch keine Routine hinterlegt."
            )
        elif request.operation is RoutineBindingOperation.FORGET:
            binding = store.find(BindingKind.ROUTINE, request.concept.key, user_id)
            is_admin = await user_is_admin(self.hass, user_input)
            if binding is None:
                response.async_set_speech(f"Für „{label}“ war keine Routine hinterlegt.")
            elif (
                binding.scope is BindingScope.HOUSEHOLD
                and not is_admin
                and binding.created_by not in {None, user_id}
            ):
                response.async_set_speech(
                    f"Die Routine für „{label}“ gilt für den ganzen Haushalt; "
                    "ändern darf sie nur, wer sie angelegt hat, oder ein Administrator."
                )
            else:
                await store.async_remove(binding.binding_id)
                self._runtime_data.learning_center_revision.bump()
                response.async_set_speech(
                    f"Erledigt. Für „{label}“ ist keine Routine mehr hinterlegt; "
                    "beim nächsten Mal frage ich wieder nach."
                )
        elif request.target is None:
            names = ", ".join(entity.friendly_name for entity in request.candidates)
            response.async_set_speech(
                f"Welche Routine meinst du genau: {names}?" if request.candidates
                else "Ein Skript oder eine Szene mit diesem Namen finde ich nicht."
            )
        else:
            kind = "die Szene" if request.target.domain == "scene" else "das Skript"
            self._runtime_data.dialog_manager.create(
                user_input.conversation_id,
                "routine-binding",
                DialogTaskKind.ROUTINE_BINDING,
                DialogPriority.CONFIRMATION,
                reason="Eine Routine-Bindung muss ausdrücklich bestätigt werden.",
                requested_by_user_id=user_id,
                payload=RoutineBindConfirmation(
                    request.concept.key, request.target.entity_id, request.personal
                ),
            )
            response.async_set_speech(
                f"Soll ich für „{label}“ künftig {kind} {request.target.friendly_name} nehmen?"
            )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_routine_binding_task(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Answer to a routine choice or a spoken binding confirmation."""
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        if task.requested_by_user_id is not None and task.requested_by_user_id != actor_id:
            response.async_set_speech("Diese Rückfrage gehört zu einem anderen Benutzer.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        payload = task.payload
        reply = classify_confirmation_reply(user_input.text)
        if isinstance(payload, RoutineBindConfirmation):
            manager.cancel(conversation_id, task.task_id)
            if reply is ConfirmationReply.YES:
                if not any(entity.entity_id == payload.entity_id for entity in entities):
                    response.async_set_speech(
                        "Diese Routine ist nicht mehr freigegeben. Ich habe nichts gespeichert."
                    )
                else:
                    note = await self._async_store_routine_binding(
                        payload.concept_key, payload.entity_id, actor_id, personal=payload.personal
                    )
                    response.async_set_speech(f"Gespeichert. {note}")
            elif reply is ConfirmationReply.NO:
                response.async_set_speech("In Ordnung, ich habe nichts gespeichert.")
            else:
                return None
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if not isinstance(payload, RoutineSelection):
            return None
        if reply is ConfirmationReply.NO:
            manager.cancel(conversation_id, task.task_id)
            response.async_set_speech("In Ordnung, ich habe nichts gestartet.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        candidates = [
            entity for candidate_id in payload.candidate_ids
            for entity in entities if entity.entity_id == candidate_id
        ]
        chosen = choose_candidate(user_input.text, candidates)
        if chosen is None:
            if len(user_input.text.split()) > 4:
                manager.cancel(conversation_id, task.task_id)
                return None  # a new request, not an answer
            names = ", ".join(entity.friendly_name for entity in candidates)
            response.async_set_speech(f"Welche meinst du: {names}?")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        manager.cancel(conversation_id, task.task_id)
        # The user named the routine: evaluated like the explicit command.
        plan = ServiceCallPlan(chosen.domain, "turn_on", chosen.entity_id)
        is_admin = await user_is_admin(self.hass, user_input)
        policy = evaluate_service_plan(
            plan, entities, self.entry.options, is_admin=is_admin, user_id=actor_id,
            effects=build_plan_effects(self.hass, plan),
        )
        success = f"{chosen.friendly_name} ausgeführt."
        if policy.outcome is PolicyOutcome.DENY:
            response.async_set_speech(
                (policy.reason or "Diese Routine darf ich nicht ausführen.")
                + " Ich habe nichts gespeichert."
            )
        elif policy.outcome is PolicyOutcome.CONFIRM:
            self._context_store.set(
                conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_service_confirmation=PendingServiceConfirmation(
                        plan, success, actor_id,
                        binding_offer=(payload.concept_key, chosen.entity_id),
                    ),
                ),
            )
            question = _confirmation_question(success)
            response.async_set_speech(f"{policy.note} {question}" if policy.note else question)
        else:
            execution = await async_execute_service_plan(
                self.hass, plan, entities, self.entry.options,
                is_admin=is_admin, user_id=actor_id, confirmed=False,
                audit_trail=self._audit_trail, audit_actor_id=actor_id,
                effect_monitor=self._runtime_data.effect_monitor,
            )
            if not execution.executed:
                response.async_set_speech(
                    f"{execution.error or 'Das hat nicht geklappt.'} Ich habe nichts gespeichert."
                )
            else:
                note = await self._async_store_routine_binding(
                    payload.concept_key, chosen.entity_id, actor_id
                )
                response.async_set_speech(f"{_with_effect_summary(success, execution)} {note}")
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def _async_explain_cause(self, entity: EntitySnapshot) -> CauseExplanation:
        """Cause of a device's current state, strictly from evidence."""
        trace = self._runtime_data.trace
        store = trace.store if trace is not None else ExecutionTraceStore()
        index = trace.index if trace is not None else ContextIndex()
        state = self.hass.states.get(entity.entity_id)
        users: dict[str, str] = {}
        try:
            users = {
                user.id: user.name
                for user in await self.hass.auth.async_get_users()
                if getattr(user, "name", None)
            }
        except Exception:  # noqa: BLE001 - names are cosmetic only
            users = {}
        cause = explain_change(
            entity.entity_id,
            entity.friendly_name,
            getattr(state, "context", None),
            getattr(state, "last_changed", None) or entity.last_changed,
            store,
            index,
            users,
            name_of=lambda entity_id: (
                str(item.attributes.get("friendly_name"))
                if (item := self.hass.states.get(entity_id)) is not None
                and item.attributes.get("friendly_name")
                else None
            ),
        )
        return cause

    def _apply_continue_conversation(
        self,
        user_input: conversation.ConversationInput,
        result: conversation.ConversationResult,
    ) -> None:
        """Flag the turn as awaiting an answer, for clients that can listen on.

        Read back from the context store rather than from anything the turn
        returned: the pending state is written by whichever handler asked the
        question, so this stays correct for handlers that do not exist yet.
        """
        conversation_id = result.conversation_id or user_input.conversation_id
        if conversation_id is None:
            return
        active = active_pending_dialog(self._context_store.get(conversation_id))
        awaiting_answer = (
            active is not None and active.kind in _CONTINUE_CONVERSATION_KINDS
        ) or self._runtime_data.dialog_manager.has_open_question(conversation_id)
        if not awaiting_answer:
            return
        # ConversationResult grew this field in Home Assistant 2025.2 and is a
        # slots dataclass, so on an older core the assignment raises instead of
        # silently adding an attribute. Setting it after construction (rather
        # than passing a constructor keyword) confines that to a no-op here,
        # instead of a TypeError on every single turn.
        try:
            result.continue_conversation = True
        except AttributeError:  # pragma: no cover - pre-2025.2 cores only
            pass

    async def _async_handle_message_inner(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Match the utterance and either act on it or say "not understood".

        A pending clarification (v2 plan Phase 25) for this
        ``conversation_id`` takes priority over a fresh match: the reply is
        resolved against the pending candidates instead of parsed as a new
        sentence (see ``NluEngine.resolve_clarification()``'s docstring for
        why - a reply like "Das im Wohnzimmer" isn't itself a full command).

        Otherwise, an elliptical follow-up without its own target ("Etwas
        heller.", v2 plan Phase 26), a pronoun/relative reference ("Mach es
        aus.", "Die Rollläden dort runter.", v2 plan Phase 27), or a
        cross-sentence query follow-up ("Und in der Küche?", HomeIntent
        plan V4.9) is tried against the stored context before a fresh
        ``match()`` - see ``NluEngine.match_followup()``/
        ``match_reference()``/``match_query_followup()``'s docstrings for
        their scope decisions.
        """
        response = intent.IntentResponse(language=user_input.language)

        # entities stays the exact list every match()/match_followup()/etc.
        # call below already took before the World Model Wave - devices are
        # fetched and bundled alongside it (World Model Wave, 2026-08-14) so
        # a real WorldModel is now built every live turn, not just in tests.
        # Threaded into match() below (WorldModelQuery wave) so query parsers
        # can resolve device-/area-level data - see docs/architecture-v7.md.
        entities = build_entity_snapshots(self.hass, self.entry)
        devices = build_device_snapshots(self.hass, self.entry)
        pending = self._context_store.get(user_input.conversation_id)
        conversation_area = resolve_conversation_area(self.hass, user_input)
        entities = await self._async_apply_confirmed_preferences(
            entities,
            area_id=(conversation_area.area_id if conversation_area is not None else None),
            user_id=conversation_user_id(user_input),
        )
        # Preferences only add a confirmed contextual alias to the existing
        # snapshots. Rebuild the same authoritative NOW view; no second
        # resolver or WorldModel is introduced.
        self._world_model = assemble_world_model(entities, devices)
        try:
            configured_relations = parse_relation_specs(
                self.entry.options.get(CONF_HOUSE_RELATIONS)
            )
            self._house_graph = self._world_model.build_house_graph(
                configured_relations
            )
        except ValueError:
            # Invalid migrated configuration never weakens language safety.
            self._house_graph = self._world_model.build_house_graph()
        self._world_model = self._world_model.with_house_graph(self._house_graph)
        understanding_context = UnderstandingContext(source_area=conversation_area)
        localized_text = materialize_local_reference(
            canonical_exception_words(normalize_clock_expressions(expand_clitics(user_input.text))),
            conversation_area,
        )
        explicit_topic_switch = False
        if pending is not None:
            replacement = re.search(
                r"\b(?:lass\s+das\s*,?\s*)?(?P<verb>mach|schalt|schalte|stell|stelle|fahr|fahre)\s+lieber\s+(?P<rest>.+)",
                localized_text,
                re.IGNORECASE,
            )
            if replacement is not None:
                explicit_topic_switch = True
                localized_text = (
                    f"{replacement.group('verb')} {replacement.group('rest')}"
                )
        wake = wake_request(localized_text)
        if wake is not None:
            # "Weck mich um sieben mit Licht": a wake request is a timed
            # switch-on of its instrument at the speaker's place.
            clock, instrument = wake
            words = [
                normalize_for_compare(part.strip(".,!?;:")) for part in instrument.split()
            ]
            named = any(
                normalize_for_compare(entity.friendly_name) in normalize_for_compare(instrument)
                for entity in entities
            )
            has_place = bool(build_place_lexicon(entities).scan(words))
            kinds = [analysis for word in words if (analysis := analyse_word(word)) is not None]
            unique = len(kinds) == 1 and len(genus_members(kinds[0].genera[0], entities)) == 1
            if not named and not has_place and not unique:
                if conversation_area is None:
                    article, _, noun = instrument.partition(" ")
                    dative = {"das": "dem", "die": "der"}.get(article.casefold(), article)
                    response.async_set_speech(
                        f"In welchem Raum soll ich dich mit {dative} {noun} wecken? "
                        f"Sag zum Beispiel: Weck mich {clock} mit {dative} {noun} im Schlafzimmer."
                    )
                    return conversation.ConversationResult(
                        response=response, conversation_id=user_input.conversation_id
                    )
                instrument = f"{instrument} {dative_location_phrase(conversation_area.name)}"
            localized_text = f"{clock[:1].upper()}{clock[1:]} schalte {instrument} ein."
        if localized_text != user_input.text:
            user_input = replace(user_input, text=localized_text)
        language_document = analyse_language(user_input.text, entities)
        if conversation_area is not None and (
            pending is None or pending.last_area is None
        ):
            pending = (
                replace(pending, last_area=conversation_area)
                if pending is not None
                else ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=conversation_area,
                    pending_clarification=None,
                )
            )

        active_dialog = active_pending_dialog(pending)
        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.SERVICE_CONFIRMATION
            and classify_confirmation_reply(user_input.text) is ConfirmationReply.UNCLEAR
            and sum(1 for token in language_document.tokens if token.is_word) >= 3
            and language_document.utterance.speech_act is not SpeechAct.QUERY
            and not any(
                token.canonical in {"warum", "wieso", "was", "welche", "welches", "wie"}
                for token in language_document.tokens[:2]
            )
        ):
            # A full new sentence instead of "Ja"/"Nein" drops the open
            # proposal (nothing runs) and is understood on its own.
            self._context_store.clear(user_input.conversation_id)
            pending = (
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=pending.last_area,
                    pending_clarification=None,
                )
                if pending is not None and pending.last_area is not None
                else None
            )
            active_dialog = None
        direct_understanding = None
        manager = self._runtime_data.dialog_manager
        if active_dialog is None:
            manager.synchronize_context_task(user_input.conversation_id, kind=None)
        else:
            manager_kind, manager_priority = _dialog_manager_kind(active_dialog.kind)
            raw_candidates = (
                getattr(active_dialog.payload, "candidates", ())
                if active_dialog.kind is PendingDialogKind.CLARIFICATION
                else ()
            )
            candidates = tuple(
                item.entity_id
                for item in raw_candidates
                if isinstance(item, EntitySnapshot)
            )
            dialog_owner = getattr(
                active_dialog.payload, "requested_by_user_id", None
            )
            manager.synchronize_context_task(
                user_input.conversation_id,
                kind=manager_kind,
                priority=manager_priority,
                slots={"context_kind": active_dialog.kind.name},
                candidates=candidates,
                reason="Ein bestehender Dialog benötigt eine eindeutige Fortsetzung.",
                requested_by_user_id=(
                    dialog_owner if isinstance(dialog_owner, str) else None
                ),
                payload=active_dialog.payload,
            )
            if (
                language_document.utterance.speech_act is SpeechAct.COMMAND
                and language_document.utterance.safe_to_execute_directly
                and not is_contextual_followup(user_input.text)
                and (
                    active_dialog.kind not in {
                        PendingDialogKind.AUTOMATION_DRAFT,
                        PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION,
                        PendingDialogKind.AUTOMATION_ACTION_EDIT,
                        PendingDialogKind.AUTOMATION_STRUCTURE_EDIT,
                        PendingDialogKind.AUTOMATION_WIZARD,
                    }
                    or explicit_topic_switch
                )
            ):
                candidate = self._engine.understand(
                    user_input.text,
                    entities,
                    self._world_model,
                    language_document,
                    context=understanding_context,
                )
                if _is_complete_actionable_understanding(candidate.payload):
                    self._context_store.clear(user_input.conversation_id)
                    manager.replace_with_complete_command(user_input.conversation_id)
                    pending = None
                    active_dialog = None
                    direct_understanding = candidate

        active_task = manager.active(user_input.conversation_id)
        # A complete, directly executable new command ends any open follow-up
        # question (alias confirmation, comfort conflict, proactive
        # clarification ...) instead of being swallowed as its answer (F10).
        # The superseded question is discarded, never executed.
        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind not in _COMMAND_ANSWER_TASK_KINDS
            and language_document.utterance.speech_act is SpeechAct.COMMAND
            and language_document.utterance.safe_to_execute_directly
            and not is_contextual_followup(user_input.text)
        ):
            candidate = self._engine.understand(
                user_input.text,
                entities,
                self._world_model,
                language_document,
                context=understanding_context,
            )
            if _is_complete_actionable_understanding(candidate.payload):
                manager.replace_with_complete_command(user_input.conversation_id)
                active_task = None
                direct_understanding = candidate

        if active_dialog is not None or active_task is not None:
            normalized_meta = language_document.normalized_text.casefold()
            actor_id = conversation_user_id(user_input)
            is_meta_turn = bool(
                re.search(
                    r"\b(?:was\s+hast\s+du\s+verstanden|warum\s+fragst\s+du)\b",
                    normalized_meta,
                )
                or _UNIVERSAL_CANCEL_RE.fullmatch(normalized_meta)
            )
            if (
                is_meta_turn
                and active_task is not None
                and active_task.requested_by_user_id is not None
                and active_task.requested_by_user_id != actor_id
            ):
                response.async_set_speech(
                    "Diese Rückfrage gehört zu einem anderen Benutzer."
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
            if re.search(r"\bwas\s+hast\s+du\s+verstanden\b", normalized_meta):
                response.async_set_speech(manager.understood(user_input.conversation_id))
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if re.search(r"\bwarum\s+fragst\s+du\b", normalized_meta):
                response.async_set_speech(manager.explain(user_input.conversation_id))
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if _UNIVERSAL_CANCEL_RE.fullmatch(normalized_meta):
                self._context_store.clear(user_input.conversation_id)
                manager.cancel(user_input.conversation_id)
                response.async_set_speech(
                    "In Ordnung. Ich habe den offenen Auftrag verworfen."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.RECURRENCE_CHOICE
        ):
            handled = self._handle_recurrence_choice(
                user_input, response, active_task, entities
            )
            if handled is not None:
                return handled

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind in {DialogTaskKind.LEARNING_OFFER, DialogTaskKind.UNKNOWN_WORD}
        ):
            handled = await self._async_handle_learning_task(
                user_input, response, active_task, entities
            )
            if handled is not None:
                return handled

        if active_dialog is None:
            # Learned language (7.4.1): macros, activity preferences,
            # "Was weißt du über mich?", "Vergiss …" - before any router
            # could read "Ich will lesen" as a routine or need.
            learned_turn = await self._async_handle_dialog_learning(
                user_input, response, entities
            )
            if learned_turn is not None:
                return learned_turn

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.ROUTINE_BINDING
        ):
            handled = await self._async_handle_routine_binding_task(
                user_input, response, active_task, entities
            )
            if handled is not None:
                return handled

        if (
            active_dialog is None
            and language_document.utterance.modality is Modality.MAINTAIN
            and language_document.maintained
        ):
            # "Lass das Licht an": keeping a state is never an operation,
            # whatever router would otherwise read the particle "an".
            response.async_set_speech(render_maintain(language_document.maintained))
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if active_dialog is None:
            # "Warum ist der Saugroboter angegangen?": answered only from HA's
            # context chain and the execution trace (7.3.2).
            cause_question = interpret_cause_question(language_document, entities)
            cause = (
                await self._async_explain_cause(cause_question.entity)
                if cause_question is not None and cause_question.entity is not None
                else None
            )
            # Without any evidence the existing answers (automation
            # references, history) keep answering, always hedged.
            if cause is not None and cause.evidence is not Evidence.UNKNOWN:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
                response.async_set_speech(cause.text)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

        if active_dialog is None:
            # Need ↔ question (7.5.0): both interpreters propose, the arbiter
            # decides. An explicit question is answered from observed states
            # ("Ist unten noch was an?"); a need statement ("Mir ist kalt")
            # is grounded before read-only routers could answer it with values.
            question_shaped = (
                language_document.utterance.speech_act is SpeechAct.QUERY
                or user_input.text.rstrip().endswith("?")
            )
            view = answer_situation_view(
                user_input.text,
                entities,
                source_area_id=(
                    conversation_area.area_id if conversation_area is not None else None
                ),
                routine_steps=self._script_steps,
            ) if question_shaped else None
            need = self._engine.understand_need(
                language_document,
                entities,
                source_area_id=(
                    conversation_area.area_id if conversation_area is not None else None
                ),
                context_area_id=(
                    pending.last_area.area_id
                    if pending is not None and pending.last_area is not None
                    else None
                ),
                routine_bindings=self._routine_bindings_for(conversation_user_id(user_input)),
            )
            decision = arbitrate(
                need_query_candidates(view, need, language_document.utterance.speech_act.name),
                explicit_question=question_shaped,
            )
            if decision.kind is DecisionKind.ANSWER and view is not None:
                return await self._async_handle_match_result(
                    user_input,
                    response,
                    MatchResult(
                        plan=None, response_text=view.text, context_entities=view.entities
                    ),
                    entities,
                )
            if isinstance(need, CommandPlan):
                return await self._async_handle_command_plan(
                    user_input, response, need, entities
                )
            if (
                need is not None and need.plan is None and need.routine_key is not None
                and len(need.routine_candidates) > 1
            ):
                # "Welche Routine meinst du: A, B oder C?" - the answer binds.
                manager.create(
                    user_input.conversation_id,
                    "routine-binding",
                    DialogTaskKind.ROUTINE_BINDING,
                    DialogPriority.SELECTION,
                    candidates=need.routine_candidates,
                    reason="Die Routine für einen Anlass muss ausdrücklich gewählt werden.",
                    requested_by_user_id=conversation_user_id(user_input),
                    payload=RoutineSelection(need.routine_key, need.routine_candidates),
                )
            if need is not None and need.plan is None:
                response.async_set_speech(need.response_text)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if need is not None:
                return await self._async_handle_match_result(
                    user_input, response, need, entities
                )
            if pending is not None and pending.pending_clarification is None:
                bound = self._engine.understand_discourse(
                    language_document, entities, pending, understanding=understanding_context
                )
                if bound is not None:
                    return await self._async_handle_bound_result(
                        user_input, response, bound, entities
                    )
            released = self._engine.understand_release(
                language_document, entities, context=understanding_context
            )
            if isinstance(released, CommandPlan):
                return await self._async_handle_command_plan(
                    user_input, response, released, entities
                )
            if released is not None and released.failure_text is not None:
                response.async_set_error(
                    intent.IntentResponseErrorCode.NO_VALID_TARGETS, released.failure_text
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if released is not None and released.clarification is not None:
                return await self._async_clarify(user_input, response, released, entities)
            if released is not None:
                return await self._async_handle_match_result(
                    user_input, response, released, entities
                )

        withdrawal = active_dialog is None and active_task is None and (
            _UNIVERSAL_CANCEL_RE.fullmatch(language_document.normalized_text.casefold()) is not None
            or (
                len(user_input.text.split()) <= 3
                and classify_confirmation_reply(user_input.text) is ConfirmationReply.NO
            )
        )
        if withdrawal and (done := _already_done_text(pending)) is not None:
            # "Vergiss es" right after an action: nothing is open to cancel,
            # so say that it already ran and how to take it back (7.6.0).
            response.async_set_speech(done)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if (
            active_dialog is None
            and active_task is None
            and _UNIVERSAL_CANCEL_RE.fullmatch(language_document.normalized_text.casefold())
        ):
            response.async_set_speech("Es ist gerade nichts offen, das ich abbrechen könnte.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.ALIAS_CONFIRMATION
            and isinstance(active_task.payload, AliasLearningDraft)
        ):
            actor_id = conversation_user_id(user_input)
            if (
                active_task.requested_by_user_id is not None
                and active_task.requested_by_user_id != actor_id
            ):
                response.async_set_speech(
                    "Diese Rückfrage gehört zu einem anderen Benutzer."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            reply = classify_confirmation_reply(user_input.text)
            draft = active_task.payload
            if reply is ConfirmationReply.NO:
                manager.cancel(user_input.conversation_id, active_task.task_id)
                response.async_set_speech(
                    "Abgebrochen. Der Alias wurde nicht gespeichert."
                )
            elif reply is not ConfirmationReply.YES:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            else:
                try:
                    await self._async_confirm_alias_learning(draft, actor_id)
                except ValueError as err:
                    response.async_set_speech(str(err))
                else:
                    response.async_set_speech(
                        f"Gespeichert. Mit „{draft.alias}“ meine ich künftig "
                        f"{draft.entity_name}."
                    )
                manager.cancel(user_input.conversation_id, active_task.task_id)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if active_dialog is None:
            proactive = self._runtime_data.proactive_context
            if proactive is not None and (
                proactive.enabled
                or (active_task is not None and active_task.kind in V12_TASK_KINDS)
            ):
                owned = await proactive.dialogs.async_handle_owned_turn(
                    user_input.text,
                    conversation_id=user_input.conversation_id,
                    user_id=conversation_user_id(user_input),
                    is_admin=await user_is_admin(self.hass, user_input),
                    entities=entities,
                    area_lookup=build_area_lookup(entities),
                )
                if owned is not None:
                    if owned.query:
                        response.response_type = intent.IntentResponseType.QUERY_ANSWER
                    response.async_set_speech(owned.speech)
                    return conversation.ConversationResult(
                        response=response, conversation_id=user_input.conversation_id
                    )
            procedure_result = await self._async_handle_procedure_turn(
                user_input, response, language_document, entities
            )
            if procedure_result is not None:
                return procedure_result
            routine_feedback_result = await self._async_handle_routine_feedback_turn(
                user_input, response, language_document, entities
            )
            if routine_feedback_result is not None:
                return routine_feedback_result
            scenario = interpret_downstairs_shutdown(language_document, entities)
            if scenario is not None:
                return self._handle_automation_match_result(
                    user_input, response, scenario, entities
                )
            comfort_result = await self._async_handle_comfort_turn(
                user_input,
                response,
                language_document,
                entities,
                area_id=(conversation_area.area_id if conversation_area is not None else None),
            )
            if comfort_result is not None:
                return comfort_result
            document_result = await self._async_handle_document_turn(
                user_input, response, language_document
            )
            if document_result is not None:
                return document_result
            thermal_answer = answer_thermal_question(
                user_input.text,
                entities,
                self._runtime_data.predictive_house,
                dt_util.now(),
            )
            if thermal_answer is not None:
                # Heating-time questions get a model-backed estimate or the
                # documented honest no-model answer, never a guess (F24).
                self._context_store.clear(user_input.conversation_id)
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
                response.async_set_speech(thermal_answer)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            learning_result = await self._async_handle_learning_turn(
                user_input, response, language_document
            )
            if learning_result is not None:
                return learning_result
            plan_result = await self._async_handle_goal_turn(
                user_input, response, language_document, entities, direct_understanding
            )
            if plan_result is not None:
                return plan_result
            memory_result = await self._async_handle_memory_turn(
                user_input, response, language_document, entities
            )
            if memory_result is not None:
                return memory_result

        # A normal pending conversation dialog always wins. Only an otherwise
        # unclaimed bare reply may address one unique V12 proposal for this
        # caller; with several open questions V12 asks which one is meant.
        proactive = self._runtime_data.proactive_context
        if (
            active_dialog is None
            and proactive is not None
            and proactive.enabled
            and classify_proposal_reply(user_input.text) is not None
        ):
            other_questions = (
                await self._runtime_data.proactive_agent.async_open_voice_question_count()
                if self._runtime_data.proactive_agent is not None
                else 0
            )
            proposal_reply = await proactive.dialogs.async_handle_bare_reply(
                user_input.text,
                conversation_id=user_input.conversation_id,
                user_id=conversation_user_id(user_input),
                device_id=getattr(user_input, "device_id", None),
                is_admin=await user_is_admin(self.hass, user_input),
                other_open_questions=other_questions,
            )
            if proposal_reply is not None:
                response.async_set_speech(proposal_reply.speech)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

        # Only an otherwise unclaimed yes/no reply may address one unique,
        # unexpired ASK event that was actually spoken over TTS; ambiguity is
        # never guessed.
        if active_dialog is None and self._runtime_data.proactive_agent is not None:
            agent_reply = await self._runtime_data.proactive_agent.async_handle_voice_reply(
                user_input.text,
                user_id=conversation_user_id(user_input),
                is_admin=await user_is_admin(self.hass, user_input),
            )
            if agent_reply is not None:
                response.async_set_speech(agent_reply)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.ALIAS_LEARNING
            and active_task is not None
            and isinstance(active_task.payload, PendingAliasLearning)
        ):
            reply = classify_confirmation_reply(user_input.text)
            draft = active_task.payload.draft
            if reply is ConfirmationReply.NO:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech("Abgebrochen. Der Alias wurde nicht gespeichert.")
            elif reply is not ConfirmationReply.YES:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            else:
                try:
                    await self._async_confirm_alias_learning(
                        draft, conversation_user_id(user_input)
                    )
                except ValueError as err:
                    response.async_set_speech(str(err))
                else:
                    response.async_set_speech(
                        f"Gespeichert. Mit „{draft.alias}“ meine ich künftig "
                        f"{draft.entity_name}."
                    )
                self._context_store.clear(user_input.conversation_id)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if is_undo_request(user_input.text):
            return await self._async_handle_undo_request(
                user_input, response, pending, entities
            )

        if is_explanation_request(user_input.text):
            return self._handle_explanation_request(
                user_input, response, pending, entities
            )

        all_calendars = tuple(entity for entity in entities if entity.domain == "calendar")
        calendars = writable_calendars(entities)
        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_WIZARD
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationWizard)
        ):
            return await self._async_handle_automation_wizard(
                user_input,
                response,
                active_task.payload.state,
                entities,
            )
        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.PRODUCTIVITY
            and active_task is not None
            and isinstance(active_task.payload, PendingProductivityCommand)
        ):
            return await self._async_handle_pending_productivity(
                user_input,
                response,
                active_task.payload,
                entities,
            )
        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.CALENDAR_EVENT
            and active_task is not None
            and isinstance(active_task.payload, PendingCalendarEvent)
        ):
            return await self._async_handle_calendar_event_turn(
                user_input, response, active_task.payload, calendars
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.CALENDAR_MUTATION
            and active_task is not None
            and isinstance(active_task.payload, PendingCalendarMutation)
        ):
            return await async_handle_calendar_mutation_confirmation(
                self, user_input, response, active_task.payload
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_CONFIRMATION
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationConfirmation)
        ):
            return await self._async_handle_pending_automation_confirmation_turn(
                user_input,
                response,
                active_task.payload,
                entities,
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_ACTION_EDIT
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationActionEdit)
        ):
            return await async_handle_automation_action_edit_turn(
                self,
                user_input,
                response,
                active_task.payload,
                entities,
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_STRUCTURE_EDIT
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationStructureEdit)
        ):
            structure_turn = await async_handle_automation_structure_edit_turn(
                self,
                user_input,
                response,
                active_task.payload,
                entities,
            )
            if structure_turn is not None:
                return structure_turn
            # The reply was a new request, not a choice: continue without
            # the discarded selection question (F9).
            pending = None
            active_dialog = None

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_MANAGEMENT
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationManagement)
        ):
            return await self._async_handle_automation_management_confirmation(
                user_input, response, active_task.payload
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_DELETION
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationDeletion)
        ):
            return await self._async_handle_automation_deletion_confirmation_reply(
                user_input, response, active_task.payload
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.SERVICE_CONFIRMATION
            and active_task is not None
            and isinstance(active_task.payload, PendingServiceConfirmation)
        ):
            return await self._async_handle_service_confirmation_reply(
                user_input,
                response,
                active_task.payload,
                entities,
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.SEMANTIC_COMMAND
            and active_task is not None
            and isinstance(active_task.payload, PendingSemanticCommand)
        ):
            return await self._async_handle_pending_semantic_command(
                user_input, response, active_task.payload, entities
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationEventClarification)
        ):
            handled = self._handle_pending_event_clarification(
                user_input, response, active_task.payload, entities
            )
            if handled is not None:
                return handled

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_DRAFT
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationDraft)
            and pending is not None
        ):
            return await self._async_handle_pending_automation_draft(
                user_input, response, active_task.payload, pending, entities
            )

        # One language/safety gate now protects every fresh domain router,
        # including historical calendar/device/productivity matchers.  This
        # is especially important for a misspelled negation: legacy wildcard
        # grammars must not absorb it into an entity name and execute anyway.
        if (
            language_document.utterance.speech_act is SpeechAct.COMMAND
            and not language_document.utterance.safe_to_execute_directly
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe den Satz als unsicheren oder verneinten Befehl "
                "erkannt und führe nichts aus.",
            )
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
            )

        routine_request = interpret_routine_binding(user_input.text, entities)
        if routine_request is not None:
            return await self._async_handle_routine_binding_request(
                user_input, response, routine_request, entities
            )

        alias_draft = parse_alias_learning(user_input.text, entities)
        if alias_draft is not None:
            target = next(
                (item for item in entities if item.entity_id == alias_draft.entity_id), None
            )
            rejection = (
                alias_rejection(
                    alias_draft.alias, target, entities,
                    is_admin=await user_is_admin(self.hass, user_input),
                    genus_word=lambda word: bool(lookup_genus_word(word)),
                )
                if target is not None else None
            )
            if rejection is not None:
                response.async_set_speech(rejection + " Ich habe nichts gespeichert.")
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            self._context_store.clear(user_input.conversation_id)
            manager.create(
                user_input.conversation_id,
                "alias-confirmation",
                DialogTaskKind.ALIAS_CONFIRMATION,
                DialogPriority.CONFIRMATION,
                slots={
                    "alias": alias_draft.alias,
                    "entity_name": alias_draft.entity_name,
                },
                reason="Ein neuer lokaler Alias muss ausdrücklich bestätigt werden.",
                requested_by_user_id=conversation_user_id(user_input),
                payload=alias_draft,
            )
            response.async_set_speech(
                f"Soll ich „{alias_draft.alias}“ lokal als Alias für "
                f"{alias_draft.entity_name} speichern?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if starts_automation_wizard(user_input.text):
            return self._start_automation_wizard(user_input, response)

        if re.search(
            r"\bwas\s+wurde\s+heute\s+(?:durch|von)\s+homeintent\s+ausgefuehrt\b",
            normalize_for_compare(user_input.text),
        ):
            return self._handle_audit_query(user_input, response)

        # A trigger/notification request ("Benachrichtige mich, wenn der Akku
        # unter 20 Prozent fällt") shares words with read-only queries but is
        # never answered as one.
        automation_turn = language_document.utterance.speech_act is SpeechAct.AUTOMATION
        history_query = (
            None if automation_turn
            else parse_history_query(user_input.text, entities, dt_util.now())
        )
        if history_query is not None:
            return await self._async_handle_history_query_result(
                user_input, response, history_query
            )

        advanced_answer = (
            None if automation_turn
            else match_advanced_query(user_input.text, entities, dt_util.now())
        )
        if advanced_answer is not None:
            return self._handle_advanced_answer(user_input, response, advanced_answer)

        if not automation_turn and re.search(
            r"\b(?:alarm|alarmanlage|sicherung|scharf|unscharf)\b",
            user_input.text, re.IGNORECASE,
        ):
            alarm = match_alarm_control(
                user_input.text, entities,
                allow_disarm=await user_is_admin(self.hass, user_input),
            )
            if alarm is not None:
                return await self._async_handle_device_control_result(
                    user_input, response, alarm
                )

        productivity_entities = entities
        if re.search(r"\bmein(?:e|er|en|em)?\b", user_input.text, re.IGNORECASE):
            owner = await user_display_name(self.hass, user_input)
            if owner:
                owner_key = normalize_for_compare(owner)
                personal_todos = {
                    entity.entity_id for entity in entities
                    if entity.domain == "todo" and owner_key in normalize_for_compare(entity.friendly_name)
                }
                if personal_todos:
                    productivity_entities = [
                        entity for entity in entities
                        if entity.domain != "todo" or entity.entity_id in personal_todos
                    ]
        management = understand_management(
            language_document,
            productivity_entities,
            all_calendars,
            dt_util.now(),
        )
        if management is not None and management.payload is None:
            response.async_set_speech(
                management.speech or "Diese Verwaltungsanfrage ist mehrdeutig."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if management is not None and isinstance(
            management.payload, (TodoRequest, TimerRequest)
        ):
            return await self._async_handle_productivity_request(
                user_input, response, management.payload, entities
            )
        if management is not None and isinstance(
            management.payload, CalendarManagementRequest
        ):
            return await async_handle_calendar_management(
                self, user_input, response, management.payload, all_calendars
            )

        calendar_draft = start_calendar_event_draft(
            user_input.text, calendars, dt_util.now()
        )
        if calendar_draft is not None:
            return self._handle_calendar_draft(
                user_input, response, calendar_draft, calendars
            )

        structure_edit = (
            None
            if is_action_edit_request(user_input.text)
            else parse_automation_structure_edit(user_input.text)
        )
        if structure_edit is not None:
            return await self._async_handle_structure_edit_request(
                user_input, response, structure_edit, entities
            )

        if is_action_edit_request(user_input.text):
            return await self._async_handle_action_edit_request(
                user_input, response, entities
            )

        management_request = parse_automation_management(user_input.text)
        if management_request is not None:
            return await self._async_handle_automation_management(
                user_input, response, management_request, entities
            )

        # "Deaktiviere/Aktiviere/Lösche die Automation <Name>" names an
        # automation, not the device its name starts with (F9): resolve the
        # explicit automation noun before the direct device command can
        # read "Aktiviere ... Flurlicht" as "turn on the Flurlicht".
        early_automation_result: (
            AutomationDeletionMatchResult | AutomationToggleMatchResult | None
        ) = None
        if has_word(user_input.text, "automation") and (
            _AUTOMATION_DELETE_RE.search(user_input.text)
            or _AUTOMATION_DISABLE_RE.search(user_input.text)
            or _AUTOMATION_ENABLE_RE.search(user_input.text)
        ):
            if self._automation_executor is None:
                self._automation_executor = AutomationExecutor(self.hass)
            automations = await self._automation_executor.async_list_automations()
            early_automation_result = (
                self._engine.match_automation_delete(user_input.text, entities, automations)
                or self._engine.match_automation_disable(user_input.text, entities, automations)
                or self._engine.match_automation_enable(user_input.text, entities, automations)
            )
        if early_automation_result is not None:
            direct_understanding = None

        # Analyse command-shaped turns before the historical device matcher
        # group. Only explicitly migrated capabilities may consume this
        # early result. Automation turns stay lazy so they do not pay both
        # the direct and automation interpreters; non-command fallbacks are
        # computed below only if the established routers did not match.
        direct_understanding = None if early_automation_result is not None else direct_understanding or (
            self._engine.understand(
                user_input.text,
                entities,
                self._world_model,
                language_document,
                context=understanding_context,
            )
            if language_document.utterance.speech_act in {
                SpeechAct.COMMAND,
                SpeechAct.QUERY,
            }
            and not (
                pending is not None and is_contextual_followup(user_input.text)
            )
            else None
        )
        result = early_automation_result or (
            direct_understanding.payload
            if direct_understanding is not None
            and direct_understanding.authority
            is UnderstandingAuthority.V8_SEMANTIC
            and not _AUTOMATION_QUERY_RE.search(user_input.text)
            and not (
                isinstance(direct_understanding.payload, MatchResult)
                and direct_understanding.payload.frame is not None
                and "temporal" in direct_understanding.payload.frame.parameters
            )
            else None
        )
        if result is None:
            device_control = match_capability_audit_query(
                user_input.text, entities, language_document
            )
            if device_control is None:
                device_control = match_household_query(
                    user_input.text,
                    entities,
                    dt_util.now(),
                    language_document,
                    self._world_model,
                )
            if device_control is None:
                device_control = match_extended_device_query(
                    user_input.text, entities, language_document
                )
            if device_control is not None:
                return await self._async_handle_device_control_result(
                    user_input, response, device_control
                )

        if pending is not None and pending.pending_clarification is not None:
            # A complete new command supersedes an open clarification.  Try
            # the ordinary grammar first so e.g. "Fahre alle Rollläden im
            # Esszimmer hoch" cannot be mistaken for the short answer to
            # "Welche Rollläden meinst du?".  Elliptical answers such as
            # "die linke" do not match a complete command and therefore
            # continue through the established clarification resolver.
            if result is None:
                if direct_understanding is None:
                    direct_understanding = self._engine.understand(
                        user_input.text,
                        entities,
                        self._world_model,
                        language_document,
                        context=understanding_context,
                    )
                result = direct_understanding.payload
            if result is None:
                clarification = pending.pending_clarification
                selection = resolve_candidate_reply(
                    user_input.text, clarification.candidates, entities
                )
                if selection.kind is CandidateReplyKind.CANCELLED:
                    self._context_store.clear(user_input.conversation_id)
                    response.async_set_speech("Abgebrochen. Ich führe nichts aus.")
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )

                current_by_id = {entity.entity_id: entity for entity in entities}
                refreshed_candidates = tuple(
                    current_by_id[candidate.entity_id]
                    for candidate in clarification.candidates
                    if candidate.entity_id in current_by_id
                )
                refreshed = replace(
                    clarification,
                    candidates=refreshed_candidates,
                    candidate_matches=(),
                )
                if selection.kind is CandidateReplyKind.SELECTED:
                    selected = selection.entity
                    current = (
                        current_by_id.get(selected.entity_id)
                        if selected is not None
                        else None
                    )
                    if current is not None:
                        result = self._engine.resolve_clarification(
                            current.entity_id, refreshed, entities
                        )
                    else:
                        result = None
                    if result is not None and current is not None:
                        await self._async_observe_clarification_choice(
                            user_input, refreshed_candidates, current,
                            conversation_area.area_id if conversation_area is not None else None,
                        )
                    if result is not None:
                        learning_manager = self._runtime_data.learning_manager
                        actor_id = conversation_user_id(user_input)
                        if (
                            learning_manager is not None
                            and actor_id is not None
                            and clarification.pending_target is not None
                            and current is not None
                        ):
                            await learning_manager.async_observe_preference_selection(
                                user_id=actor_id,
                                concept=clarification.pending_target,
                                area_id=current.area_id,
                                entity_id=current.entity_id,
                                observed_at=dt_util.utcnow(),
                            )
                        self._context_store.clear(user_input.conversation_id)
                    else:
                        self._context_store.clear(user_input.conversation_id)
                        response.async_set_speech(
                            "Das ausgewählte Gerät ist nicht mehr verfügbar. "
                            "Ich führe nichts aus."
                        )
                        return conversation.ConversationResult(
                            response=response,
                            conversation_id=user_input.conversation_id,
                        )
                else:
                    if not refreshed_candidates:
                        self._context_store.clear(user_input.conversation_id)
                        response.async_set_speech(
                            "Keines der zuvor gefundenen Geräte ist noch verfügbar. "
                            "Ich führe nichts aus."
                        )
                    else:
                        self._context_store.set(
                            user_input.conversation_id,
                            replace(pending, pending_clarification=refreshed),
                        )
                        response.async_set_speech(
                            "Das konnte ich keinem der angebotenen Geräte "
                            "eindeutig zuordnen. "
                            + render_candidate_question(refreshed_candidates)
                        )
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
        elif result is None:
            correction = self._engine.match_correction_followup(
                user_input.text, entities, pending
            )
            if correction is not None and correction.plan is not None:
                undo = pending.pending_undo if pending is not None else None
                actor_id = conversation_user_id(user_input)
                if undo is None:
                    response.async_set_speech(
                        "Die vorige Aktion kann ich nicht sicher rückgängig machen. Bitte korrigiere sie mit einem vollständigen neuen Befehl."
                    )
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                if undo.requested_by_user_id is not None and undo.requested_by_user_id != actor_id:
                    response.async_set_error(
                        intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                        "Diese Korrektur gehört zu einem anderen Benutzer.",
                    )
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                is_admin = await user_is_admin(self.hass, user_input)
                for inverse in undo.plans:
                    fresh = build_entity_snapshots(self.hass, self.entry)
                    compensated = await async_execute_service_plan(
                        self.hass,
                        inverse,
                        fresh,
                        self.entry.options,
                        is_admin=is_admin,
                        user_id=actor_id,
                        confirmed=True,
                        audit_trail=self._audit_trail,
                        audit_actor_id=actor_id,
                        effect_monitor=self._runtime_data.effect_monitor,
                    )
                    if not compensated.executed:
                        self._context_store.clear(user_input.conversation_id)
                        response.async_set_error(
                            intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                            "Die vorige Aktion konnte nicht sicher zurückgenommen werden; die Korrektur wurde nicht ausgeführt.",
                        )
                        return conversation.ConversationResult(
                            response=response,
                            conversation_id=user_input.conversation_id,
                        )
                    expected_state = {
                        "turn_on": "on",
                        "turn_off": "off",
                        "open_cover": "open",
                        "close_cover": "closed",
                    }.get(inverse.service)
                    if expected_state is not None:
                        target_ids = (
                            (inverse.entity_id,)
                            if isinstance(inverse.entity_id, str)
                            else tuple(inverse.entity_id)
                        )
                        observed = {
                            item.entity_id: item.state
                            for item in build_entity_snapshots(self.hass, self.entry)
                            if item.entity_id in target_ids
                        }
                        if any(observed.get(item) != expected_state for item in target_ids):
                            self._context_store.clear(user_input.conversation_id)
                            response.async_set_error(
                                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                                "Die Rücknahme wurde nicht im aktuellen Zustand beobachtet; die Korrektur wurde sicher gestoppt.",
                            )
                            return conversation.ConversationResult(
                                response=response,
                                conversation_id=user_input.conversation_id,
                            )
                # The correction is a new action after compensation, so it
                # must receive another fresh snapshot and policy evaluation.
                entities = build_entity_snapshots(self.hass, self.entry)
                # "Nein, ich meinte X" counts like an answer to the same
                # choice (7.4.1): twice the same correction offers a default.
                await self._async_observe_correction(
                    user_input, undo.plans, correction.plan, entities,
                    conversation_area.area_id if conversation_area is not None else None,
                )
                result = correction
            else:
                result = correction
            if result is None:
                notification_clause = self._engine.match_immediate_notification(
                    user_input.text
                )
                if notification_clause is not None:
                    return await self._async_handle_immediate_notification(
                        user_input, response, NotificationRequest.from_clause(notification_clause),
                        entities,
                    )
            # A command naming its own, unknown device ("Mach den
            # Zauberkasten an") is never completed from the context: that
            # would silently substitute the previous device (7.4.1).
            names_unknown = (
                unknown_device_noun(user_input.text, entities, is_known_device_word)
                is not None
            )
            if result is None and not names_unknown:
                result = self._engine.match_followup(user_input.text, pending)
            if result is None and not names_unknown:
                result = self._engine.match_contextual_property_followup(
                    user_input.text, entities, pending
                )
            if result is None and not names_unknown:
                result = self._engine.match_reference(
                    user_input.text, entities, pending, self._world_model
                )
            if result is None and not names_unknown:
                result = self._engine.match_query_followup(
                    user_input.text, entities, pending, self._world_model
                )
            if result is None and not names_unknown:
                result = self._engine.match_command_followup(user_input.text, entities, pending)
            if result is None and _AUTOMATION_DELETE_RE.search(user_input.text):
                # V5.28 "Automation Deletion": checked before the query gate
                # below - "Lösche die Automation für X" also contains the
                # word "Automation" and would otherwise trigger an
                # unnecessary second automations.yaml read via the query
                # gate first (that grammar just wouldn't match it). Same
                # lazily-constructed, entity-lifetime executor instance the
                # query/creation paths already use.
                if self._automation_executor is None:
                    self._automation_executor = AutomationExecutor(self.hass)
                automations = await self._automation_executor.async_list_automations()
                result = self._engine.match_automation_delete(user_input.text, entities, automations)
            if result is None and _AUTOMATION_DISABLE_RE.search(user_input.text):
                # Wave 11 "Automation Disable/Enable": same "checked before
                # the query gate" reasoning as the delete gate above -
                # "Deaktiviere die Automation für X" also contains the word
                # "Automation".
                if self._automation_executor is None:
                    self._automation_executor = AutomationExecutor(self.hass)
                automations = await self._automation_executor.async_list_automations()
                result = self._engine.match_automation_disable(user_input.text, entities, automations)
            if result is None and _AUTOMATION_ENABLE_RE.search(user_input.text):
                if self._automation_executor is None:
                    self._automation_executor = AutomationExecutor(self.hass)
                automations = await self._automation_executor.async_list_automations()
                result = self._engine.match_automation_enable(user_input.text, entities, automations)
            if result is None and _AUTOMATION_QUERY_RE.search(user_input.text):
                # V5.29 "Automation Query": the same cheap pre-check
                # ``match_automation_query()`` itself re-checks is applied
                # here first too, since it is what decides whether the real,
                # blocking ``automations.yaml``/metadata-sidecar read below
                # happens at all - an ordinary command/query turn must never
                # pay that I/O cost. ``self._automation_executor`` is the
                # same lazily-constructed, entity-lifetime instance
                # ``_async_handle_automation_confirmation_reply`` already
                # uses for automation creation (see below) - one executor,
                # one lock, shared across both read and write paths.
                if self._automation_executor is None:
                    self._automation_executor = AutomationExecutor(self.hass)
                automations = await self._automation_executor.async_list_automations()
                result = self._engine.match_automation_query(
                    user_input.text, entities, pending, automations
                )
            if result is None:
                result = self._engine.match_repeated_event_automation(
                    user_input.text, entities, self._world_model, pending
                )
            if result is None:
                result = self._engine.match_persistent_state_automation(
                    user_input.text, entities, self._world_model, pending
                )
            if result is None:
                result = self._engine.match_recurring_time_automation(
                    user_input.text, entities, dt_util.now(), self._world_model, pending
                )
            if result is None:
                result = self._engine.understand_automation(
                    user_input.text,
                    entities,
                    self._world_model,
                    pending,
                    language_document,
                ).payload
            if result is None:
                result = self._engine.match_calendar_event_automation(
                    user_input.text, entities, self._world_model, pending
                )
            if result is None:
                result = self._engine.match_calendar_time_automation(
                    user_input.text, entities, self._world_model, pending
                )
            if result is None:
                result = self._engine.match_automation_draft_start(
                    user_input.text, entities, self._world_model, pending
                )
            if result is None:
                reminder_text = reminder_automation_text(user_input.text)
                if reminder_text is not None:
                    result = self._engine.match_calendar_time_automation(
                        reminder_text, entities, self._world_model, pending
                    )
                    if result is None:
                        result = self._engine.match_relative_time_automation(
                            reminder_text, entities, self._world_model, pending
                        )
                    named_recipient = reminder_recipient(user_input.text)
                    if result is None and named_recipient is not None:
                        response.async_set_speech(
                            f"Ich finde kein eindeutig ausgewähltes Benachrichtigungsziel für {named_recipient}."
                        )
                        return conversation.ConversationResult(
                            response=response,
                            conversation_id=user_input.conversation_id,
                        )
                    if isinstance(result, AutomationMatchResult):
                        recipient = reminder_recipient(user_input.text)
                        quiet_hours = reminder_quiet_hours(user_input.text)
                        actions = result.model.actions
                        if recipient is not None:
                            recipient_key = normalize_for_compare(recipient)
                            targets = [
                                entity for entity in entities
                                if entity.domain == "notify" and any(
                                    recipient_key in normalize_for_compare(name)
                                    for name in (entity.friendly_name, *entity.aliases)
                                )
                            ]
                            if len(targets) != 1:
                                response.async_set_speech(
                                    f"Ich finde kein eindeutig ausgewähltes Benachrichtigungsziel für {recipient}."
                                )
                                return conversation.ConversationResult(
                                    response=response,
                                    conversation_id=user_input.conversation_id,
                                )
                            updated_actions = list(actions)
                            updated_actions[0] = replace(
                                updated_actions[0],
                                target=TriggerTarget(
                                    domain="notify", entity_id=targets[0].entity_id
                                ),
                                recipient=NotificationRecipient(
                                    NotificationRecipientKind.EXPLICIT_TARGET,
                                    entity_ids=(targets[0].entity_id,),
                                    label=targets[0].friendly_name,
                                ),
                            )
                            actions = tuple(updated_actions)
                        result = replace(
                            result,
                            model=replace(
                                result.model,
                                source_text=user_input.text,
                                actions=actions,
                                quiet_start_hour=(quiet_hours[0] if quiet_hours else None),
                                quiet_end_hour=(quiet_hours[1] if quiet_hours else None),
                            ),
                        )
            if result is None:
                # Wave 13 ("Relative-Zeit-Automationen") - a single-clause
                # command with a relative-time offset ("Fahre in 5 Minuten
                # ..."), tried after the two-clause match_automation() (which
                # can never split this comma-less sentence, see
                # relative_time_command.yaml's own comment) and before the
                # plain match() fallback (which would otherwise route it to
                # TemporalParser, which only parses-and-discards the "in N
                # Minuten" phrase and executes immediately - the original bug
                # this wave fixes). ``dt_util.now()`` (local time, not UTC)
                # is fetched here - the only layer allowed to import
                # homeassistant.util.dt - and passed into the engine so it
                # stays Home-Assistant-import-free.
                result = self._engine.match_relative_time_automation(
                    user_input.text, entities, self._world_model, pending
                )
            if result is None:
                if direct_understanding is None:
                    direct_understanding = self._engine.understand(
                        user_input.text,
                        entities,
                        self._world_model,
                        language_document,
                        context=understanding_context,
                    )
                result = direct_understanding.payload

        if result is None:
            return await self._async_handle_no_match(user_input, response, entities)

        if isinstance(result, MatchResult) and result.failure_text is not None:
            # Understood, but honestly not groundable ("Im Büro gibt es
            # keinen Ventilator."): say exactly that, never execute.
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_VALID_TARGETS,
                result.failure_text,
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if (
            isinstance(result, MatchResult)
            and result.plan is None
            and result.command is None
            and result.clarification is None
            and analyse_utterance(user_input.text).speech_act is SpeechAct.COMMAND
        ):
            # Diagnostic feedback such as "unknown location" is still a
            # structural no-match.  Give the bounded, confirm-before-action
            # ASR correction a chance before returning that feedback.
            return await self._async_handle_no_match(
                user_input, response, entities
            )

        if isinstance(result, CommandPlan):
            return await self._async_handle_command_plan(
                user_input, response, result, entities
            )

        if isinstance(result, AutomationMatchResult):
            decided = self._decide_recurrence(user_input, response, result, entities, pending)
            if isinstance(decided, conversation.ConversationResult):
                return decided
            return self._handle_automation_match_result(
                user_input, response, decided, entities
            )

        if isinstance(result, AutomationDraftMatchResult):
            return self._handle_automation_draft_match_result(
                user_input, response, result
            )

        if isinstance(result, AutomationClarificationResult):
            return self._handle_automation_clarification_result(
                user_input, response, result
            )

        if isinstance(result, AutomationDeletionMatchResult):
            return self._handle_automation_deletion_match_result(
                user_input, response, result
            )

        if isinstance(result, AutomationToggleMatchResult):
            return await self._async_handle_automation_toggle_result(
                user_input, response, result
            )

        if result.clarification is not None:
            return await self._async_clarify(user_input, response, result, entities)

        return await self._async_handle_match_result(
            user_input, response, result, entities
        )

    async def _async_handle_bound_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult | CommandPlan,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Run one meaning-model result through the ordinary handlers."""
        if user_input.text.strip().casefold().startswith("und ") and user_input.text.rstrip().endswith("?"):
            # An elliptical "Und im Bad?" after an action repeats it; the
            # question mark is not a request for information here.
            user_input = replace(user_input, text=user_input.text.rstrip(" ?") + ".")
        if isinstance(result, CommandPlan):
            return await self._async_handle_command_plan(user_input, response, result, entities)
        if result.failure_text is not None:
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_VALID_TARGETS, result.failure_text
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if result.clarification is not None:
            return await self._async_clarify(user_input, response, result, entities)
        return await self._async_handle_match_result(user_input, response, result, entities)

    async def _async_handle_procedure_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Manage named goals; execution always rematerializes a fresh plan."""
        request = interpret_procedure_intent(language_document)
        if request is None:
            return None
        store = self._runtime_data.memory
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        if store is None or not store.enabled:
            response.async_set_speech(MEMORY_DISABLED_TEXT)
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )
        if actor_id is None:
            response.async_set_speech(
                "Benannte Prozeduren verwalte ich nur für einen authentifizierten Benutzer."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )
        records = await store.async_list(
            person_id=actor_id, kinds=(MemoryKind.PROCEDURE,)
        )
        if request.operation is ProcedureOperation.LIST:
            names = sorted(
                str(record.content.get("name"))
                for record in records
                if isinstance(record.content.get("name"), str)
            )
            if not names:
                speech = "Für dich sind keine benannten Prozeduren gespeichert."
            else:
                visible = ", ".join(names[:3])
                suffix = f" und {len(names) - 3} weitere" if len(names) > 3 else ""
                speech = f"Gespeicherte Prozeduren: {visible}{suffix}."
            response.async_set_speech(speech)
        elif request.operation is ProcedureOperation.SAVE_ACTIVE_PLAN:
            active = manager.active(conversation_id)
            plan = active.slots.get("plan") if active is not None else None
            if not isinstance(plan, MaterializedPlan) or request.name is None:
                response.async_set_speech(
                    "Es ist kein vollständiger Plan offen, den ich benennen könnte."
                )
            elif any(
                normalize_for_compare(str(record.content.get("name", "")))
                == request.name
                for record in records
            ):
                response.async_set_speech(
                    "Eine Prozedur mit diesem Namen existiert bereits. Bitte lösche oder benenne sie zuerst um."
                )
            else:
                manager.cancel(conversation_id)
                manager.create(
                    conversation_id,
                    "procedure-save",
                    DialogTaskKind.MEMORY_CONFIRMATION,
                    DialogPriority.CONFIRMATION,
                    slots={
                        "operation": ProcedureOperation.SAVE_ACTIVE_PLAN.value,
                        "kind": MemoryKind.PROCEDURE.value,
                        "content": {
                            "name": request.name,
                            "goal_kind": plan.goal.kind.value,
                            "parameters": dict(plan.goal.parameters),
                        },
                        "person_id": actor_id,
                    },
                    reason="Ein benannter Mehrschrittplan soll dauerhaft gespeichert werden.",
                    requested_by_user_id=actor_id,
                )
                response.async_set_speech(
                    f"Soll ich die Prozedur {request.name} dauerhaft speichern?"
                )
        else:
            matches = [
                record
                for record in records
                if request.name is not None
                and normalize_for_compare(str(record.content.get("name", "")))
                == request.name
            ]
            if len(matches) != 1:
                response.async_set_speech(
                    "Diese Prozedur ist nicht eindeutig gespeichert. Ich habe nichts geändert."
                )
            elif request.operation is ProcedureOperation.FORGET:
                deleted = await store.async_forget(matches[0].memory_id)
                response.async_set_speech(
                    "Die Prozedur wurde kontrolliert gelöscht."
                    if deleted
                    else "Die Prozedur konnte nicht gelöscht werden."
                )
            else:
                record = matches[0]
                raw_goal = record.content.get("goal_kind")
                raw_parameters = record.content.get("parameters")
                try:
                    goal_kind = GoalKind(str(raw_goal))
                    if not isinstance(raw_parameters, dict):
                        raise ValueError("ungültige Parameter")
                    plan = materialize_goal(
                        Goal(goal_kind, raw_parameters),
                        entities,
                        options=self.entry.options,
                        is_admin=await user_is_admin(self.hass, user_input),
                        user_id=actor_id,
                    )
                except (PermissionError, ValueError):
                    response.async_set_speech(
                        "Die Prozedur ist mit dem aktuellen Hauszustand nicht mehr sicher ausführbar."
                    )
                else:
                    manager.create(
                        conversation_id,
                        "procedure-plan",
                        DialogTaskKind.PLAN_CONFIRMATION,
                        DialogPriority.CONFIRMATION,
                        slots={"plan": plan},
                        reason="Die gespeicherte Prozedur wurde frisch materialisiert und wartet auf Bestätigung.",
                        requested_by_user_id=actor_id,
                    )
                    response.async_set_speech(
                        f"Die Prozedur {request.name} ergibt aktuell {plan.summary} Soll ich sie ausführen?"
                    )
        return conversation.ConversationResult(
            response=response, conversation_id=conversation_id
        )

    async def _async_handle_routine_feedback_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Bind explicit feedback to exactly one recent routine signal."""
        request = interpret_routine_feedback(language_document)
        if request is None:
            return None
        now = dt_util.utcnow()
        recent = [
            (entity_id, stats)
            for entity_id, stats in self._runtime_data.routine_statistics.items()
            if stats.last_alert_at is not None
            and timedelta(0) <= now - stats.last_alert_at <= timedelta(minutes=15)
        ]
        if not recent:
            response.async_set_speech(
                "Es gibt keinen eindeutigen aktuellen Routinehinweis für dieses Feedback."
            )
        elif len(recent) > 1:
            names_by_id = {entity.entity_id: entity.friendly_name for entity in entities}
            names = ", ".join(
                names_by_id.get(entity_id, "unbekanntes Gerät")
                for entity_id, _ in recent[:3]
            )
            response.async_set_speech(
                f"Mehrere Routinehinweise sind offen: {names}. Bitte beziehe dich eindeutig auf einen."
            )
        else:
            entity_id, stats = recent[0]
            stats.record_feedback(request.feedback, now=now)
            actor_id = conversation_user_id(user_input)
            store = self._runtime_data.memory
            if store is not None and store.enabled and actor_id is not None:
                await store.async_remember(
                    MemoryKind.DECISION,
                    {
                        "routine_entity_id": entity_id,
                        "feedback": request.feedback.value,
                    },
                    provenance=FactProvenance.CONFIRMED_MEMORY,
                    confirmed=True,
                    person_id=actor_id,
                )
            messages = {
                "helpful": "Danke. Ich habe den Hinweis als hilfreich bewertet.",
                "unnecessary": "Verstanden. Ich habe den Hinweis als unnötig bewertet.",
                "wrong": "Verstanden. Ich habe den Hinweis als falsch bewertet.",
                "ignore": "Verstanden. Dieses lokale Muster wird künftig nicht mehr gemeldet.",
                "later": "Verstanden. Ich frage zu diesem Muster frühestens später erneut.",
            }
            response.async_set_speech(messages[request.feedback.value])
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_comfort_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
        *,
        area_id: str | None,
    ) -> conversation.ConversationResult | None:
        """Turn a vague comfort command into one explicit, confirmed ASK."""
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        active = manager.active(conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.CONFLICT_RESOLUTION
            and active.task_id == "comfort-household-conflict"
        ):
            actor_id = conversation_user_id(user_input)
            stored_area = active.slots.get("area_id")
            raw_users = active.slots.get("present_user_ids")
            users = tuple(
                item for item in raw_users if isinstance(item, str)
            ) if isinstance(raw_users, tuple) else ()
            draft = (
                _comfort_profile_from_document(
                    language_document, stored_area, actor_id
                )
                if isinstance(stored_area, str) and actor_id is not None
                else None
            )
            if draft is None or len(users) < 2 or self._runtime_data.profiles is None:
                response.async_set_speech(
                    "Bitte nenne einen konkreten gemeinsamen Temperaturwert in Grad."
                )
            else:
                shared = replace(
                    draft,
                    profile_id=("comfort:shared:" + hashlib.sha256(
                        repr((stored_area, tuple(sorted(users)))).encode()
                    ).hexdigest()[:24]),
                    owner_user_id="shared",
                    confirmed=True,
                    household_user_ids=tuple(sorted(users)),
                )
                await self._runtime_data.profiles.async_save_comfort_profile(
                    shared, confirmed=True
                )
                manager.cancel(conversation_id)
                response.async_set_speech(
                    "Gespeichert. Dieses gemeinsame Komfortprofil gilt nur für "
                    "diesen Bereich und genau diese anwesende Benutzergruppe."
                )
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )
        if active is not None and active.kind is DialogTaskKind.COMFORT_PROFILE_DEFINITION:
            actor_id = conversation_user_id(user_input)
            if active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Komfortprofil-Definition gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            draft = active.slots.get("profile")
            reply = classify_confirmation_reply(language_document.source_text)
            if reply is ConfirmationReply.NO:
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Das Komfortprofil wurde nicht gespeichert.")
            elif reply is not ConfirmationReply.YES:
                response.async_set_speech("Bitte bestätige das Komfortprofil eindeutig mit Ja oder Nein.")
            elif not isinstance(draft, ComfortProfile) or self._runtime_data.profiles is None:
                manager.cancel(conversation_id)
                response.async_set_speech("Die Komfortprofil-Definition ist nicht mehr vollständig.")
            else:
                confirmed_profile = replace(draft, confirmed=True)
                await self._runtime_data.profiles.async_save_comfort_profile(
                    confirmed_profile, confirmed=True
                )
                manager.cancel(conversation_id)
                response.async_set_speech("Gespeichert. Das bestätigte Komfortprofil ist jetzt lokal verfügbar.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.MISSING_SLOT
            and active.task_id == "comfort-missing-action"
        ):
            normalized = language_document.normalized_text.casefold()
            if re.search(r"\bwarum\s+fragst\s+du\b", normalized):
                response.async_set_speech(manager.explain(conversation_id))
            elif re.search(r"\bwas\s+hast\s+du\s+verstanden\b", normalized):
                response.async_set_speech(manager.understood(conversation_id))
            elif re.search(r"\b(?:abbrechen|vergiss\s+es|lass\s+das)\b", normalized):
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Ich ändere nichts.")
            else:
                actor_id = conversation_user_id(user_input)
                stored_area = active.slots.get("area_id")
                profile = (
                    _comfort_profile_from_document(
                        language_document, stored_area, actor_id
                    )
                    if isinstance(stored_area, str) and actor_id is not None
                    else None
                )
                if profile is None:
                    response.async_set_speech(
                        "Bitte nenne einen konkreten Temperaturbereich in Grad, einen Helligkeitsbereich in Prozent oder beides."
                    )
                else:
                    manager.create(
                        conversation_id,
                        "comfort-profile-definition",
                        DialogTaskKind.COMFORT_PROFILE_DEFINITION,
                        DialogPriority.CONFIRMATION,
                        slots={"profile": profile},
                        reason="Ein typisiertes Komfortprofil wartet auf ausdrückliche Bestätigung.",
                        requested_by_user_id=actor_id,
                    )
                    response.async_set_speech(
                        f"Als Komfortprofil habe ich verstanden: {_comfort_profile_preview(profile)}. Soll ich diese Werte lokal speichern?"
                    )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if active is not None and active.kind is DialogTaskKind.COMFORT_CONFIRMATION:
            actor_id = conversation_user_id(user_input)
            if active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Komfort-Rückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            reply = classify_confirmation_reply(language_document.source_text)
            if reply is ConfirmationReply.NO:
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Ich ändere nichts.")
            elif reply is ConfirmationReply.YES:
                proposed = active.slots.get("plan")
                if not isinstance(proposed, ServiceCallPlan):
                    manager.cancel(conversation_id)
                    response.async_set_speech("Der Vorschlag ist nicht mehr vollständig. Ich ändere nichts.")
                else:
                    fresh = build_entity_snapshots(self.hass, self.entry)
                    validation_error = validate_agent_service_plan(proposed)
                    if validation_error is not None:
                        manager.cancel(conversation_id)
                        response.async_set_speech(f"Ich habe nichts geändert: {validation_error}")
                    else:
                        execution = await async_execute_service_plan(
                            self.hass,
                            proposed,
                            fresh,
                            self.entry.options,
                            is_admin=await user_is_admin(self.hass, user_input),
                            user_id=actor_id,
                            confirmed=True,
                            audit_trail=self._audit_trail,
                            audit_actor_id=actor_id or "voice",
                            effect_monitor=self._runtime_data.effect_monitor,
                        )
                        manager.cancel(conversation_id)
                        response.async_set_speech(
                            "Die bestätigte Komfortänderung wurde ausgeführt."
                            if execution.executed
                            else f"Ich habe nichts geändert: {execution.error or 'Policy abgelehnt.'}"
                        )
            elif len(language_document.tokens) <= 3:
                response.async_set_speech("Bitte antworte eindeutig mit Ja oder Nein.")
            else:
                manager.cancel(conversation_id)
                return None
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if not is_comfort_request(language_document):
            return None
        # V10 consolidates comfort under the confirmed ProfileStore and the
        # same bounded planner used by every other goal.
        manager.cancel(conversation_id)
        actor_id = conversation_user_id(user_input)
        profiles = self._runtime_data.profiles
        profile: ComfortProfile | None = None
        if profiles is not None and actor_id is not None and area_id is not None:
            states = {item.entity_id: item.state for item in entities}
            present_user_ids = (
                self._runtime_data.user_contexts.present_user_ids(states)
                if self._runtime_data.user_contexts is not None else ()
            )
            if not present_user_ids:
                present_user_ids = (actor_id,)
            candidates = profiles.comfort_profiles(
                area_id=area_id, user_ids=present_user_ids
            )
            shared = profiles.shared_comfort(
                area_id=area_id, user_ids=present_user_ids
            )
            typed = tuple(
                LearnedPreference(
                    item.profile_id,
                    PreferenceContext(
                        item.owner_user_id, "comfortable_environment", area_id,
                        presence_set=present_user_ids,
                    ),
                    _comfort_value_signature(item), KnowledgeState.CONFIRMED,
                    1.0, 1, 1, item.owner_user_id,
                )
                for item in candidates
            )
            typed_shared = (
                LearnedPreference(
                    shared.profile_id,
                    PreferenceContext(
                        "shared", "comfortable_environment", area_id,
                        presence_set=tuple(sorted(present_user_ids)),
                    ),
                    _comfort_value_signature(shared), KnowledgeState.CONFIRMED,
                    1.0, 1, 1, shared.owner_user_id,
                )
                if shared is not None else None
            )
            resolution = resolve_preferences(
                typed, present_user_ids=present_user_ids,
                shared_preference=typed_shared,
                concept="comfortable_environment", area_id=area_id,
            )
            if resolution.requires_clarification and len(present_user_ids) > 1:
                manager.create(
                    conversation_id, "comfort-household-conflict",
                    DialogTaskKind.CONFLICT_RESOLUTION,
                    DialogPriority.SELECTION,
                    slots={"area_id": area_id,
                           "present_user_ids": present_user_ids},
                    reason="Bestätigte Komfortprofile anwesender Benutzer widersprechen sich.",
                    requested_by_user_id=actor_id,
                )
                response.async_set_speech(
                    "Für euch sind unterschiedliche Komfortwerte gespeichert. "
                    "Welche Temperatur soll gelten, wenn ihr beide zuhause seid?"
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=conversation_id
                )
            if typed_shared is not None and resolution.source_preference_ids == (typed_shared.preference_id,):
                profile = shared
            elif resolution.source_preference_ids:
                selected_id = resolution.source_preference_ids[0]
                profile = next(
                    (item for item in candidates if item.profile_id == selected_id), None
                )
        if profile is None:
            if area_id is None:
                response.async_set_speech(
                    "Ich kann „hier“ keinem eindeutigen Home-Assistant-Bereich zuordnen. Bitte nutze einen Sprachsatelliten mit Bereich oder nenne den Raum."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=conversation_id
                )
            manager.create(
                conversation_id,
                "comfort-missing-action",
                DialogTaskKind.MISSING_SLOT,
                DialogPriority.FOLLOWUP,
                slots={"area_id": area_id},
                missing_slots=("konkrete Aktion",),
                reason="Gemütlicher ist ohne eine eindeutige bestätigte Präferenz mehrdeutig.",
                requested_by_user_id=actor_id,
            )
            response.async_set_speech(
                "Was bedeutet angenehm für dich hier? Soll ich Temperatur, Licht oder beides berücksichtigen? Ich speichere nur ausdrücklich bestätigte Werte."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )
        goal = interpret_goal(
            language_document,
            current_user_id=actor_id,
            conversation_id=conversation_id,
            voice_area_id=area_id,
        )
        if goal is None:
            return None
        try:
            plan = materialize_comfort_profile(
                goal,
                profile,
                entities,
                options=self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=actor_id,
            )
        except (PermissionError, ValueError) as err:
            response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        return self._stage_goal_plan(user_input, response, plan, actor_id)

    async def _async_handle_document_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
    ) -> conversation.ConversationResult | None:
        query = interpret_document_search(language_document)
        if query is None:
            return None
        index = self._runtime_data.document_index
        if index is None:
            response.async_set_speech("Der lokale Dokumentindex ist deaktiviert.")
        else:
            hits = await index.async_search(query, limit=3)
            if not hits:
                response.async_set_speech("Dazu habe ich in den freigegebenen lokalen Dokumenten keinen Treffer gefunden.")
            else:
                rendered = "; ".join(
                    f"{hit.title} ({hit.relative_path}): {hit.excerpt}"
                    for hit in hits
                )
                response.async_set_speech(f"Lokale Dokumenttreffer: {rendered}")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_goal_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
        direct_understanding: object | None = None,
    ) -> conversation.ConversationResult | None:
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        active = manager.active(conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.GOAL_RUN_CLARIFICATION
            and isinstance(active.payload, GoalRunClarification)
        ):
            actor_id = conversation_user_id(user_input)
            pending_runs = active.payload
            if pending_runs.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Verlaufs-Rückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            selected_id = _select_goal_run_reply(
                language_document, pending_runs.run_ids, pending_runs.labels
            )
            if selected_id is None:
                response.async_set_speech(
                    "Bitte nenne eines der Ziele: " + _german_goal_labels(pending_runs.labels) + "."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            store = self._runtime_data.goal_runs
            matches = (
                await store.async_query(GoalRunQuery(user_id=actor_id, run_id=selected_id))
                if store is not None
                else ()
            )
            manager.cancel(conversation_id, active.task_id)
            response.async_set_speech(explain_goal_run(matches[-1] if matches else None).message)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if (
            active is not None
            and active.kind is DialogTaskKind.GOAL_SEMANTIC_CLARIFICATION
            and isinstance(active.payload, PendingGoalSemanticClarification)
        ):
            actor_id = conversation_user_id(user_input)
            pending_goal = active.payload
            if pending_goal.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Ziel-Rückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            choice = _goal_semantic_choice(language_document)
            if choice is None or choice not in pending_goal.choices:
                response.async_set_speech(
                    "Bitte wähle eindeutig: Sollwert zum Zeitpunkt setzen oder bis dahin erreichen."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            manager.cancel(conversation_id, active.task_id)
            if choice is GoalSemanticChoice.ACHIEVE_BY_DEADLINE:
                original_document = analyse_language(
                    pending_goal.goal.provenance.source_utterance, entities
                )
                deadline = resolve_scheduled_datetime(
                    original_document.temporal, dt_util.now()
                )
                goal_for_advice = (
                    replace(
                        pending_goal.goal,
                        temporal=replace(pending_goal.goal.temporal, deadline=deadline),
                    )
                    if pending_goal.goal.temporal is not None and deadline is not None
                    else pending_goal.goal
                )
                advice = _thermal_advice_for_goal(
                    goal_for_advice, entities, self._runtime_data.predictive_house
                )
                if advice is None:
                    response.async_set_speech(
                        "Für dieses Ergebnisziel fehlt mir ein ausreichend validiertes thermisches Modell. "
                        "Ich kann den Sollwert zu einem festen Zeitpunkt setzen oder du sammelst weitere belegte Heizvorgänge."
                    )
                    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
                try:
                    plan = materialize_goal(
                        goal_for_advice, entities, options=self.entry.options,
                        is_admin=await user_is_admin(self.hass, user_input),
                        user_id=actor_id, adaptive_advice=advice,
                    )
                except (PermissionError, ValueError) as err:
                    response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
                return self._stage_goal_plan(user_input, response, plan, actor_id)
            original_document = analyse_language(
                pending_goal.goal.provenance.source_utterance, entities
            )
            scheduled_for = resolve_scheduled_datetime(
                original_document.temporal, dt_util.now()
            )
            if scheduled_for is None:
                response.async_set_speech(
                    "Der geplante Zeitpunkt ist nicht mehr vollständig. Ich habe nichts geplant."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            clarified = replace(
                pending_goal.goal,
                temporal=TemporalGoal(
                    execute_at=scheduled_for,
                    day_part=scheduled_for.strftime("%Y-%m-%d %H:%M"),
                    must_be_achieved_by_deadline=False,
                ),
                failure_handling="report",
            )
            try:
                plan = materialize_goal(
                    clarified,
                    entities,
                    options=self.entry.options,
                    is_admin=await user_is_admin(self.hass, user_input),
                    user_id=actor_id,
                )
            except (PermissionError, ValueError) as err:
                response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            return self._stage_goal_plan(user_input, response, plan, actor_id)

        if active is not None and active.kind is DialogTaskKind.ROUTINE_DEFINITION:
            actor_id = conversation_user_id(user_input)
            if active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Routinen-Definition gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            draft = active.slots.get("routine")
            if (
                not isinstance(draft, RoutineDefinition)
                and classify_confirmation_reply(language_document.source_text)
                is ConfirmationReply.NO
            ):
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Ich lege keine Routine an.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            if isinstance(draft, RoutineDefinition):
                reply = classify_confirmation_reply(language_document.source_text)
                if reply is ConfirmationReply.NO:
                    manager.cancel(conversation_id)
                    response.async_set_speech("In Ordnung. Die Routine wurde nicht gespeichert.")
                elif reply is not ConfirmationReply.YES:
                    response.async_set_speech("Bitte bestätige die gezeigte Routine eindeutig mit Ja oder Nein.")
                elif self._runtime_data.profiles is None:
                    manager.cancel(conversation_id)
                    response.async_set_speech("Die lokale Profil-Persistenz ist nicht verfügbar.")
                else:
                    confirmed_routine = replace(draft, confirmed=True)
                    await self._runtime_data.profiles.async_save_routine(
                        confirmed_routine, confirmed=True
                    )
                    manager.cancel(conversation_id)
                    response.async_set_speech(
                        f"Gespeichert. Die Routine {confirmed_routine.name} enthält "
                        f"{len(confirmed_routine.steps)} bestätigte Schritte."
                    )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)

            understood = direct_understanding
            if understood is None:
                understood = self._engine.understand(
                    user_input.text,
                    entities,
                    self._world_model,
                    language_document,
                )
            payload = getattr(understood, "payload", None)
            routine_id = active.slots.get("routine_id")
            if not isinstance(routine_id, str) or actor_id is None:
                manager.cancel(conversation_id)
                response.async_set_speech("Die Routinen-Definition ist nicht mehr vollständig.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            routine = _routine_definition_from_payload(
                routine_id, actor_id, payload, entities
            )
            if routine is None:
                response.async_set_speech(
                    "Ich konnte daraus keine vollständige Folge unterstützter Gerätezustände bilden. Bitte nenne konkrete Geräte und Zielzustände."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            manager.create(
                conversation_id,
                active.task_id,
                DialogTaskKind.ROUTINE_DEFINITION,
                DialogPriority.CONFIRMATION,
                slots={"routine_id": routine_id, "routine": routine},
                reason="Eine typisierte Routinen-Definition wartet auf ausdrückliche Bestätigung.",
                requested_by_user_id=actor_id,
            )
            preview = "; ".join(step.description for step in routine.steps)
            response.async_set_speech(
                f"Als Routine {routine.name} habe ich verstanden: {preview}. Soll ich diese Definition lokal speichern?"
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if active is not None and active.kind is DialogTaskKind.PLAN_CONFIRMATION:
            actor_id = conversation_user_id(user_input)
            if active.requested_by_user_id is not None and active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Planbestätigung gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            pending_plan = active.slots.get("plan")
            if isinstance(pending_plan, MaterializedPlan):
                modified = apply_plan_modification(
                    pending_plan, language_document, entities
                )
                if modified is not None:
                    manager.create(
                        conversation_id,
                        active.task_id,
                        DialogTaskKind.PLAN_CONFIRMATION,
                        DialogPriority.CONFIRMATION,
                        slots={"plan": modified},
                        reason="Der strukturierte Plan wurde geändert und wartet erneut auf Bestätigung.",
                        requested_by_user_id=actor_id,
                    )
                    actions = [
                        step.description for step in modified.steps
                        if step.kind is StepKind.ACTION
                    ]
                    response.async_set_speech(
                        f"Plan angepasst: {'; '.join(actions[:3]) or 'keine sofortige Aktion'}. Soll ich diesen Plan ausführen?"
                    )
                    return conversation.ConversationResult(
                        response=response, conversation_id=conversation_id
                    )
            reply = classify_confirmation_reply(language_document.source_text)
            if reply is ConfirmationReply.NO:
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Der Plan wurde nicht ausgeführt.")
            elif reply is ConfirmationReply.UNCLEAR:
                if len(language_document.tokens) > 3:
                    manager.cancel(conversation_id)
                    return None
                response.async_set_speech("Bitte bestätige den gesamten Plan eindeutig mit Ja oder Nein.")
            else:
                stored_plan = active.slots.get("plan")
                if not isinstance(stored_plan, MaterializedPlan):
                    manager.cancel(conversation_id)
                    response.async_set_speech("Der Plan ist nicht mehr vollständig. Ich habe nichts ausgeführt.")
                else:
                    is_admin = await user_is_admin(self.hass, user_input)

                    async def refresh() -> list[EntitySnapshot]:
                        return build_entity_snapshots(self.hass, self.entry)

                    async def execute(plan, fresh, confirmed):
                        return await async_execute_service_plan(
                            self.hass,
                            plan,
                            fresh,
                            self.entry.options,
                            is_admin=is_admin,
                            user_id=actor_id,
                            confirmed=confirmed,
                            audit_trail=self._audit_trail,
                            audit_actor_id=actor_id or "voice",
                            effect_monitor=self._runtime_data.effect_monitor,
                        )

                    async def verify(entity_id: str, expected: str) -> bool:
                        loop = asyncio.get_running_loop()
                        deadline = loop.time() + min(
                            10.0,
                            self._runtime_data.effect_monitor.timeout.total_seconds(),
                        )
                        while True:
                            current = next(
                                (
                                    item
                                    for item in build_entity_snapshots(self.hass, self.entry)
                                    if item.entity_id == entity_id
                                ),
                                None,
                            )
                            if current is not None and effect_satisfied(current, expected):
                                return True
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                return False
                            await asyncio.sleep(min(0.1, remaining))

                    async def schedule(step, fresh, confirmed):
                        action = step.action
                        if action is None or step.execute_at_local_time is None:
                            return _ScheduledOutcome(False, "Der terminierte Schritt ist unvollständig.")
                        decision = evaluate_service_plan(
                            action,
                            fresh,
                            self.entry.options,
                            is_admin=is_admin,
                            user_id=actor_id,
                            effects=build_plan_effects(self.hass, action),
                            attended=False,
                        )
                        if decision.outcome is PolicyOutcome.DENY:
                            return _ScheduledOutcome(False, decision.reason)
                        if decision.outcome is PolicyOutcome.CONFIRM and not confirmed:
                            return _ScheduledOutcome(False, "Die Aktion benötigt eine Bestätigung.")
                        automation_action = _scheduled_action_model(action)
                        if automation_action is None:
                            return _ScheduledOutcome(False, "Für diese Aktion gibt es keinen geschlossenen Zeitplan-Operator.")
                        try:
                            hour_text, minute_text = step.execute_at_local_time.split(":", 1)
                            hour, minute = int(hour_text), int(minute_text)
                        except (TypeError, ValueError):
                            return _ScheduledOutcome(False, "Der geplante Zeitpunkt ist ungültig.")
                        now = dt_util.now()
                        scheduled_for = step.scheduled_for
                        if scheduled_for is None:
                            scheduled_for = now.replace(
                                hour=hour, minute=minute, second=0, microsecond=0
                            )
                            if scheduled_for <= now:
                                scheduled_for += timedelta(days=1)
                        elif scheduled_for <= now:
                            return _ScheduledOutcome(False, "Der geplante Zeitpunkt liegt bereits in der Vergangenheit.")
                        automation_id = uuid.uuid4().hex
                        model = AutomationModel(
                            triggers=(TriggerModel(
                                TriggerType.TIME,
                                time_hour=hour,
                                time_minute=minute,
                                time_second=0,
                            ),),
                            actions=(automation_action,),
                            source_text=(
                                f"HomeIntent: {step.description} um "
                                f"{step.execute_at_local_time} Uhr"
                            ),
                            once=True,
                            scheduled_for=scheduled_for,
                        )
                        validation_error = validate_automation(model)
                        if validation_error is not None:
                            return _ScheduledOutcome(
                                False,
                                f"Der terminierte Schritt ist nicht sicher: {validation_error.name}",
                            )
                        generation = generate_ha_automation_config(
                            model, fresh, automation_id=automation_id
                        )
                        if generation.error is not None or generation.config is None:
                            return _ScheduledOutcome(
                                False,
                                "Der terminierte Schritt konnte nicht sicher erzeugt werden.",
                            )
                        automation_configs: list[tuple[str, dict[str, object], datetime]] = [
                            (automation_id, generation.config, scheduled_for)
                        ]
                        checkpoint_records: list[
                            tuple[ThermalDeadlineCheckpoint, datetime]
                        ] = []
                        advice = stored_plan.adaptive_advice
                        if advice is not None:
                            area_id = stored_plan.goal.scope.area_id
                            thermal_model = (
                                self._runtime_data.predictive_house.thermal_model(area_id)
                                if self._runtime_data.predictive_house is not None
                                and area_id is not None else None
                            )
                            target_value = action.data.get("temperature")
                            climate_id = (
                                action.entity_id
                                if isinstance(action.entity_id, str) else None
                            )
                            if (
                                thermal_model is None or climate_id is None
                                or not isinstance(target_value, (int, float))
                                or thermal_model.model_id != advice.model_id
                            ):
                                return _ScheduledOutcome(
                                    False,
                                    "Die thermische Modellbindung ist nicht mehr eindeutig.",
                                )
                            base_checkpoint = ThermalDeadlineCheckpoint(
                                ThermalCheckpointPhase.START,
                                stored_plan.goal.goal_id,
                                thermal_model.binding.area_id,
                                climate_id,
                                thermal_model.binding.temperature_entity_id,
                                float(target_value),
                                advice.model_id,
                                advice.predicted_duration.total_seconds(),
                                advice.uncertainty_buffer.total_seconds(),
                                checkpoint_id=f"thermal-checkpoint-{uuid.uuid4().hex}",
                                token=secrets.token_urlsafe(32),
                                run_id=execution_run_id,
                                scheduled_for=scheduled_for.isoformat(),
                                deadline=advice.final_verification_at.isoformat(),
                            )
                            enriched = append_start_checkpoint(
                                generation.config, base_checkpoint
                            )
                            if enriched is None:
                                return _ScheduledOutcome(
                                    False, "Die thermische Startprüfung konnte nicht geplant werden."
                                )
                            automation_configs[0] = (automation_id, enriched, scheduled_for)
                            checkpoint_records = [(base_checkpoint, scheduled_for)]
                            for phase, checkpoint_at in (
                                (ThermalCheckpointPhase.INTERMEDIATE,
                                 advice.intermediate_check_at),
                                (ThermalCheckpointPhase.FINAL,
                                 advice.final_verification_at),
                            ):
                                checkpoint_id = uuid.uuid4().hex
                                phase_checkpoint = replace(
                                    base_checkpoint, phase=phase,
                                    checkpoint_id=f"thermal-checkpoint-{uuid.uuid4().hex}",
                                    token=secrets.token_urlsafe(32),
                                    scheduled_for=checkpoint_at.isoformat(),
                                )
                                checkpoint_config = checkpoint_automation_config(
                                    phase_checkpoint,
                                    scheduled_for=checkpoint_at,
                                    automation_id=checkpoint_id,
                                )
                                if checkpoint_config is None:
                                    return _ScheduledOutcome(
                                        False, "Ein thermischer Prüfzeitpunkt ist ungültig."
                                    )
                                automation_configs.append(
                                    (checkpoint_id, checkpoint_config, checkpoint_at)
                                )
                                checkpoint_records.append((phase_checkpoint, checkpoint_at))
                            checkpoint_store = self._runtime_data.thermal_checkpoints
                            if checkpoint_store is None:
                                checkpoint_store = PendingThermalCheckpointStore(
                                    self.hass.config.path(
                                        ".storage/homeintent_thermal_checkpoints.json"
                                    )
                                )
                                self._runtime_data.thermal_checkpoints = checkpoint_store
                            for checkpoint_record, checkpoint_at in checkpoint_records:
                                registered = await checkpoint_store.async_register(
                                    checkpoint_record, scheduled_for=checkpoint_at,
                                    deadline=advice.final_verification_at,
                                )
                                if not registered:
                                    return _ScheduledOutcome(
                                        False, "Ein thermischer Prüfpunkt konnte nicht authentifiziert werden."
                                    )
                        if self._automation_executor is None:
                            self._automation_executor = AutomationExecutor(self.hass)
                        created_ids: list[str] = []
                        try:
                            for created_id, config, execute_at in automation_configs:
                                await self._automation_executor.async_create_automation(
                                    config,
                                    automation_id=created_id,
                                    scheduled_for=execute_at,
                                    once=True,
                                )
                                created_ids.append(created_id)
                        except Exception as err:  # noqa: BLE001 - transactional executor reports heterogeneous HA/I/O failures
                            for created_id in reversed(created_ids):
                                try:
                                    await self._automation_executor.async_delete_automation(
                                        created_id
                                    )
                                except Exception:  # noqa: BLE001 - best-effort multi-object rollback
                                    _LOGGER.exception(
                                        "Could not roll back thermal checkpoint %s",
                                        created_id,
                                    )
                            if advice is not None and self._runtime_data.thermal_checkpoints is not None:
                                for checkpoint_record, _checkpoint_at in checkpoint_records:
                                    await self._runtime_data.thermal_checkpoints.async_delete(
                                        checkpoint_record.checkpoint_id
                                    )
                            return _ScheduledOutcome(False, str(err))
                        return _ScheduledOutcome(True)

                    execution_run_id = f"run_{uuid.uuid4().hex}"
                    reservation = await self._runtime_data.execution_coordinator.async_acquire(
                        execution_run_id, stored_plan
                    )
                    if reservation.outcome.value == "conflict":
                        manager.cancel(conversation_id)
                        response.async_set_speech(
                            "Ein anderes Ziel verändert gerade mindestens dasselbe Gerät. Ich habe diesen Plan nicht nondeterministisch parallel ausgeführt."
                        )
                        return conversation.ConversationResult(
                            response=response, conversation_id=conversation_id
                        )
                    goal_runs = self._runtime_data.goal_runs
                    user_contexts = self._runtime_data.user_contexts

                    async def run_plan() -> PlanResult:
                        try:
                            plan_result = await PlanExecutor(
                                refresh, execute, verify, schedule
                            ).execute(
                                stored_plan, confirmed=True
                            )
                        finally:
                            await self._runtime_data.execution_coordinator.async_release(
                                execution_run_id
                            )
                        if goal_runs is not None:
                            current_entities = {
                                item.entity_id: item
                                for item in build_entity_snapshots(self.hass, self.entry)
                            }
                            run = goal_run_from_plan_result(
                                stored_plan, plan_result, current_entities,
                                run_id=execution_run_id,
                                user_id=actor_id,
                                person_entity_id=(
                                    user_contexts.resolve_current_person(actor_id).person_entity_id
                                    if user_contexts is not None
                                    else None
                                ),
                                updated_at=dt_util.utcnow().isoformat(),
                            )
                            await goal_runs.async_append(run)
                        return plan_result

                    # Service calls are accepted within milliseconds, but
                    # verifying slow effects (a garage door, several locks)
                    # can take much longer than a voice satellite waits (F17).
                    # Answer with the final result when it is quick; otherwise
                    # confirm immediately, keep verifying in the background and
                    # report only a failure. The GoalRun keeps every piece of
                    # evidence for "Warum?".
                    plan_task = self.hass.async_create_task(
                        run_plan(), name=f"HomeIntent plan {execution_run_id}"
                    )
                    done, _pending = await asyncio.wait(
                        {plan_task}, timeout=_PLAN_REPLY_BUDGET_SECONDS
                    )
                    manager.cancel(conversation_id)
                    if plan_task not in done:
                        label = (stored_plan.goal.provenance.source_utterance if stored_plan.goal.provenance else "") or "Der bestätigte Plan"
                        plan_task.add_done_callback(
                            lambda task: self.hass.async_create_task(
                                self._async_report_background_plan(
                                    task, execution_run_id, label[:80], actor_id
                                ),
                                name=f"HomeIntent plan report {execution_run_id}",
                            )
                        )
                        steps = sum(
                            1 for step in stored_plan.steps
                            if step.kind in {StepKind.ACTION, StepKind.NOTIFY}
                        )
                        response.async_set_speech(
                            f"In Ordnung, ich führe den Plan jetzt aus ({steps} "
                            f"{'Schritt' if steps == 1 else 'Schritte'}) und prüfe die Wirkung. "
                            "Ich melde mich nur, falls etwas nicht klappt."
                        )
                        return conversation.ConversationResult(
                            response=response, conversation_id=conversation_id
                        )
                    result = plan_task.result()
                    if result.status is PlanStatus.COMPLETED:
                        response.async_set_speech("Der Plan wurde vollständig ausgeführt und verifiziert.")
                    elif result.status is PlanStatus.SCHEDULED:
                        response.async_set_speech(
                            "Die sofortigen Schritte wurden verifiziert; die verschobenen Schritte sind als persistente einmalige Home-Assistant-Automation geplant."
                        )
                    elif result.status is PlanStatus.PARTIAL_FAILURE:
                        response.async_set_speech(
                            "Der Plan wurde nur teilweise erfüllt. Mindestens ein früherer Schritt ist verifiziert, aber ein weiterer Schritt ist fehlgeschlagen."
                        )
                    else:
                        response.async_set_speech("Der Plan wurde sicher gestoppt, weil ein Schritt fehlschlug oder nicht verifiziert werden konnte.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        actor_id = conversation_user_id(user_input)
        person_id = None
        user_contexts = self._runtime_data.user_contexts
        if user_contexts is not None:
            person_binding = user_contexts.resolve_current_person(actor_id)
            if person_binding.status is BindingStatus.RESOLVED:
                person_id = person_binding.person_entity_id
        area = resolve_conversation_area(self.hass, user_input)
        household = (
            user_contexts.household.person_entity_ids if user_contexts is not None else ()
        )
        person_names: dict[str, list[str]] = {}
        for entity in entities:
            if entity.domain != "person":
                continue
            aliases = {
                normalize_for_compare(entity.friendly_name),
                normalize_for_compare(entity.entity_id.partition(".")[2]),
            }
            for alias in aliases:
                if alias:
                    person_names.setdefault(alias, []).append(entity.entity_id)
        profiles = self._runtime_data.profiles
        routine_names: dict[str, str] = {}
        if profiles is not None and actor_id is not None:
            for routine in profiles.routines_for(actor_id):
                for spoken in (routine.name, routine.routine_id.replace("_", " ")):
                    if key := normalize_for_compare(spoken).strip():
                        routine_names[key] = routine.routine_id
        area_names = {
            key: entity.area_id
            for entity in entities
            if entity.area_id is not None
            for name in (entity.area_name or "", *entity.area_aliases)
            if (key := normalize_for_compare(name).strip())
        }
        goal = interpret_goal(
            language_document,
            current_user_id=actor_id,
            conversation_id=conversation_id,
            current_person_entity_id=person_id,
            voice_area_id=area.area_id if area is not None else None,
            household_person_ids=household,
            person_name_bindings={
                name: tuple(dict.fromkeys(entity_ids))
                for name, entity_ids in person_names.items()
            },
            routine_names=routine_names,
            area_names=area_names,
        )
        if goal is None:
            return None

        if goal.kind is V10GoalKind.EXPLAIN_FAILURE:
            # Explicit automation diagnostics belong to the existing
            # authoritative automation-management path.  V10 run-history
            # explanations handle anaphoric/general failure questions only.
            if parse_automation_management(user_input.text) is not None:
                return None
            if actor_id is None:
                response.async_set_speech(
                    "Ohne eindeutige Home-Assistant-Benutzerzuordnung kann ich keinen "
                    "persönlichen Zielverlauf erklären."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=conversation_id
                )
            store = self._runtime_data.goal_runs
            window = resolve_history_window(language_document.tokens, dt_util.now())
            goal_kind_value = goal.parameters.get("goal_kind")
            goal_kind = None
            if isinstance(goal_kind_value, str):
                try:
                    goal_kind = V10GoalKind(goal_kind_value)
                except ValueError:
                    goal_kind = None
            entity_id = _mentioned_goal_run_entity(language_document, entities)
            query = GoalRunQuery(
                user_id=actor_id,
                start_time=window.start if window is not None else None,
                end_time=window.end if window is not None else None,
                failed_only=goal.parameters.get("failed_only") is not False,
                goal_kind=goal_kind,
                routine_id=goal.routine_id,
                entity_id=entity_id,
            )
            matches = await store.async_query(query) if store else ()
            if window is not None and len(matches) > 1:
                labels = tuple(_goal_run_label(item) for item in matches)
                manager.create(
                    conversation_id,
                    "goal-run-clarification",
                    DialogTaskKind.GOAL_RUN_CLARIFICATION,
                    DialogPriority.SELECTION,
                    candidates=tuple(item.run_id for item in matches),
                    reason="Mehrere historische Ziele passen zum genannten Zeitraum.",
                    requested_by_user_id=actor_id,
                    payload=GoalRunClarification(
                        tuple(item.run_id for item in matches), labels, actor_id
                    ),
                )
                response.async_set_speech(
                    f"{window.label.capitalize()} sind {_german_count(len(matches))} passende Ziele fehlgeschlagen: "
                    + _german_goal_labels(labels)
                    + ". Welches meinst du?"
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            run = matches[-1] if matches else None
            explanation = explain_goal_run(run)
            if run is None and window is not None and window.label.startswith("gestern"):
                records = (
                    await self._runtime_data.monitor_goals.async_load()
                    if self._runtime_data.monitor_goals is not None
                    else ()
                )
                candidates = [
                    item.goal
                    for item in records
                    if item.enabled
                    and item.goal.provenance.user_id == actor_id
                    and item.goal.trigger is not None
                    and item.goal.trigger.person_entity_id is not None
                ]
                if len(candidates) == 1:
                    trigger = candidates[0].trigger
                    assert trigger is not None and trigger.person_entity_id is not None
                    evidence = await async_get_transition_evidence(
                        self.hass,
                        trigger.person_entity_id,
                        start=window.start,
                        end=window.end,
                        from_state=trigger.from_state or "home",
                        to_state=trigger.to_state or "not_home",
                    )
                    if evidence.available and evidence.occurred is False:
                        explanation = replace(
                            explanation,
                            message=(
                                f"Die Erinnerung wurde gestern nicht ausgelöst, weil "
                                f"{trigger.person_entity_id} laut Home-Assistant-Verlauf "
                                "nicht vom Ausgangszustand in den erwarteten Zielzustand wechselte."
                            ),
                        )
            response.async_set_speech(explanation.message)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if goal.kind is V10GoalKind.MONITOR_AND_NOTIFY:
            if goal.parameters.get("ambiguous_person_name"):
                response.async_set_speech(
                    "Die genannte Person ist nicht eindeutig. Bitte wähle eine konkrete person.*-Entität."
                )
            elif goal.trigger is None:
                response.async_set_speech("Der Auslöser ist nicht eindeutig. Es wurde nichts gespeichert.")
            elif goal.trigger.kind.startswith("person_") and goal.trigger.person_entity_id is None:
                response.async_set_speech(
                    "Ich kann „ich“ noch keinem Home-Assistant-Benutzer und keiner person.*-Entität eindeutig zuordnen. Bitte konfiguriere diese Zuordnung einmalig."
                )
            elif goal.trigger.kind == "nobody_home" and not household:
                response.async_set_speech(
                    "Für „niemand zuhause“ ist noch kein bestätigter Haushalt aus person.*-Entitäten konfiguriert."
                )
            elif not goal.recipient_person_ids:
                response.async_set_speech(
                    "Für den Empfänger fehlt eine eindeutige Personenzuordnung. Es wurde nichts gespeichert."
                )
            elif user_contexts is None or self._runtime_data.monitor_goals is None:
                response.async_set_speech("Die lokale Goal-Persistenz ist nicht verfügbar.")
            else:
                unresolved = [
                    user_contexts.resolve_notification_targets(recipient)
                    for recipient in goal.recipient_person_ids
                    if user_contexts.resolve_notification_targets(recipient).status
                    is not BindingStatus.RESOLVED
                ]
                if unresolved:
                    ambiguous = any(item.status is BindingStatus.AMBIGUOUS for item in unresolved)
                    response.async_set_speech(
                        "Welches bestätigte Gerät soll ich für diese Push-Benachrichtigung verwenden?"
                        if ambiguous
                        else "Für diese Person ist noch kein bestätigtes Push-Ziel konfiguriert."
                    )
                else:
                    confirmed_goal = replace(
                        goal, provenance=replace(goal.provenance, confirmed=True)
                    )
                    await self._runtime_data.monitor_goals.async_save(
                        MonitorRecord(confirmed_goal)
                    )
                    response.async_set_speech(
                        "Das Monitor-Ziel ist lokal gespeichert. Die Bedingung wird beim tatsächlichen Auslöser mit einem frischen Hauszustand geprüft."
                    )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if goal.kind is V10GoalKind.SCHEDULED and goal.temporal is not None and goal.temporal.must_be_achieved_by_deadline:
            resolved_deadline = resolve_scheduled_datetime(
                language_document.temporal, dt_util.now()
            )
            goal_for_advice = (
                replace(goal, temporal=replace(goal.temporal, deadline=resolved_deadline))
                if resolved_deadline is not None else goal
            )
            advice = _thermal_advice_for_goal(
                goal_for_advice, entities, self._runtime_data.predictive_house
            )
            if advice is not None:
                try:
                    plan = materialize_goal(
                        goal_for_advice, entities, options=self.entry.options,
                        is_admin=await user_is_admin(self.hass, user_input),
                        user_id=actor_id, adaptive_advice=advice,
                    )
                except (PermissionError, ValueError) as err:
                    response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
                return self._stage_goal_plan(user_input, response, plan, actor_id)
            manager.create(
                conversation_id,
                "goal-semantic-clarification",
                DialogTaskKind.GOAL_SEMANTIC_CLARIFICATION,
                DialogPriority.SELECTION,
                slots={
                    "goal_id": goal.goal_id,
                    "area_id": goal.scope.area_id,
                    "desired_states": goal.desired_states,
                    "temporal": goal.temporal,
                },
                missing_slots=("goal_semantic_choice",),
                candidates=tuple(item.value for item in GoalSemanticChoice),
                reason="Ein Temperatur-Ergebnisziel muss semantisch geklärt werden.",
                requested_by_user_id=actor_id,
                payload=PendingGoalSemanticClarification(
                    goal, actor_id, conversation_id
                ),
            )
            response.async_set_speech(
                "Soll die Heizung zu diesem Zeitpunkt auf den Zielwert gestellt werden oder soll der Raum ihn dann bereits erreicht haben? Ohne bestätigtes thermisches Modell kann ich keine Vorheizzeit garantieren."
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if goal.kind in {V10GoalKind.COMFORT, V10GoalKind.IMPROVE_COMFORT}:
            if actor_id is None or goal.scope.area_id is None:
                response.async_set_speech(
                    "Was bedeutet angenehm für dich hier? Soll ich Temperatur, Licht oder beides berücksichtigen?"
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            profiles = self._runtime_data.profiles
            profile = profiles.comfort(area_id=goal.scope.area_id, user_id=actor_id) if profiles else None
            if profile is None:
                response.async_set_speech(
                    "Was bedeutet angenehm für dich hier? Soll ich Temperatur, Licht oder beides berücksichtigen?"
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            try:
                plan = materialize_comfort_profile(
                    goal, profile, entities, options=self.entry.options,
                    is_admin=await user_is_admin(self.hass, user_input), user_id=actor_id,
                )
            except (PermissionError, ValueError) as err:
                response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            return self._stage_goal_plan(user_input, response, plan, actor_id)

        if goal.routine_id is not None:
            profiles = self._runtime_data.profiles
            routine = profiles.routine(goal.routine_id, user_id=actor_id) if profiles and actor_id else None
            if routine is None:
                manager.create(
                    conversation_id,
                    "routine-definition",
                    DialogTaskKind.ROUTINE_DEFINITION,
                    DialogPriority.FOLLOWUP,
                    slots={"routine_id": goal.routine_id},
                    missing_slots=("typisierte Routinen-Schritte",),
                    reason="Der Begriff hat noch keine ausdrücklich bestätigte lokale Bedeutung.",
                    requested_by_user_id=actor_id,
                )
                response.async_set_speech(
                    f"Was soll ich bei {goal.routine_id} erledigen? Ich speichere die Routine erst nach deiner ausdrücklichen Bestätigung."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            excluded_names = goal.parameters.get("excluded_area_names", ())
            if isinstance(excluded_names, (list, tuple)):
                excluded = {
                    entity.area_id for entity in entities
                    if entity.area_id is not None and any(
                        normalize_for_compare(str(name)) in {
                            normalize_for_compare(entity.area_id),
                            normalize_for_compare(entity.area_name or ""),
                        }
                        for name in excluded_names
                    )
                }
                if excluded:
                    goal = replace(goal, exclusions=GoalScope(excluded_area_ids=tuple(sorted(excluded))))
            try:
                plan = materialize_routine(
                    goal, routine, entities, options=self.entry.options,
                    is_admin=await user_is_admin(self.hass, user_input), user_id=actor_id,
                )
            except (PermissionError, ValueError) as err:
                response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            return self._stage_goal_plan(user_input, response, plan, actor_id)

        if goal.kind is GoalKind.PREPARE_MOVIE:
            store = self._runtime_data.memory
            preferences = (
                await store.async_list(
                    person_id=actor_id, kinds=(MemoryKind.PREFERENCE,)
                )
                if store is not None and store.enabled and actor_id is not None
                else ()
            )
            matching_preferences = [
                record
                for record in preferences
                if record.content.get("activity") == "television"
                and isinstance(record.content.get("entity_id"), str)
                and isinstance(record.content.get("brightness_percent"), int)
            ]
            if len(matching_preferences) == 1:
                preference = matching_preferences[0].content
                goal = Goal(
                    GoalKind.PREPARE_MOVIE,
                    {
                        "entity_ids": [preference["entity_id"]],
                        "brightness_percent": preference["brightness_percent"],
                    },
                )
        try:
            plan = materialize_goal(
                goal,
                entities,
                options=self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=actor_id,
            )
        except (PermissionError, ValueError) as err:
            response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        actions = [step.description for step in plan.steps if step.kind is StepKind.ACTION]
        details = "; ".join(actions[:3])
        if len(actions) > 3:
            details += f"; sowie {len(actions) - 3} weitere geprüfte Schritte"
        if not plan.requires_confirmation:
            response.async_set_speech(plan.summary)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        manager.create(
            conversation_id,
            "goal-plan",
            DialogTaskKind.PLAN_CONFIRMATION,
            DialogPriority.CONFIRMATION,
            slots={"plan": plan},
            reason="Ein vollständiger Mehrschrittplan wartet auf Bestätigung.",
            requested_by_user_id=actor_id,
        )
        response.async_set_speech(
            f"Planvorschau: {details}. {plan.summary} Soll ich den gesamten Plan ausführen?"
        )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def _async_report_background_plan(
        self,
        task: "asyncio.Task[PlanResult]",
        run_id: str,
        label: str,
        actor_id: str | None,
    ) -> None:
        """Report a plan that finished after the spoken reply - only if it failed."""
        try:
            result = task.result()
        except asyncio.CancelledError:
            failed = True
        except Exception:  # noqa: BLE001 - reported below, never swallowed silently
            _LOGGER.exception("HomeIntent background plan %s failed", run_id)
            failed = True
        else:
            failed = result.status not in {PlanStatus.COMPLETED, PlanStatus.SCHEDULED}
        if not failed:
            return
        proactive = self._runtime_data.proactive_context
        if proactive is not None and proactive.enabled and actor_id is not None:
            await proactive.async_report_goal_failure(
                run_id=run_id, goal_label=label, owner_user_id=actor_id,
            )
            return
        # Without the proactive layer the failure still has to reach the
        # user: a Home Assistant notification names what did not work.
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "HomeIntent",
                    "message": (
                        f"„{label}“ wurde nicht vollständig ausgeführt. "
                        "Frag „Warum hat das nicht funktioniert?“ für die Details."
                    ),
                    "notification_id": f"homeintent_plan_{run_id}",
                },
                blocking=False,
                context=call_context(),
            )
        except Exception:  # noqa: BLE001 - reporting must never raise
            _LOGGER.warning("HomeIntent could not report a failed plan", exc_info=True)

    def _stage_goal_plan(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        plan: MaterializedPlan,
        actor_id: str | None,
    ) -> conversation.ConversationResult:
        actions = [step.description for step in plan.steps if step.kind is StepKind.ACTION]
        details = "; ".join(actions[:3]) or "keine Änderung nötig"
        if len(actions) > 3:
            details += f"; sowie {len(actions) - 3} weitere geprüfte Schritte"
        if not plan.requires_confirmation:
            response.async_set_speech(plan.summary)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        self._runtime_data.dialog_manager.create(
            user_input.conversation_id,
            "goal-plan",
            DialogTaskKind.PLAN_CONFIRMATION,
            DialogPriority.CONFIRMATION,
            slots={"plan": plan},
            reason="Ein vollständiger Mehrschrittplan wartet auf Bestätigung.",
            requested_by_user_id=actor_id,
        )
        response.async_set_speech(
            f"Planvorschau: {details}. {plan.summary} Soll ich den gesamten Plan ausführen?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_learning_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
    ) -> conversation.ConversationResult | None:
        """Review/explain/delete V11 knowledge without exposing raw history."""
        registry = self._runtime_data.learned_models
        if registry is None:
            return None
        normalized_request = language_document.normalized_text.casefold()
        if re.search(
            # Only predictive wording. A bare "wie lange" also asks for a
            # running timer, an appliance's remaining time or recorder history,
            # which have their own authoritative read paths.
            r"\b(?:normalerweise|typischerweise|vermutlich|trend|wann.*leer)\b",
            normalized_request,
        ):
            if re.search(r"\b(?:strom|energie|verbrauch)\b", normalized_request):
                response.async_set_speech(
                    "Für dieses Gerät habe ich noch kein belastbares Verbrauchsmodell."
                )
            elif re.search(r"\b(?:batterie|akku)\b", normalized_request):
                response.async_set_speech(
                    "Für dieses Gerät habe ich noch kein belastbares Batterie-Trendmodell."
                )
            elif re.search(r"\b(?:dauer|lange|fertig|laufzeit)\b", normalized_request):
                response.async_set_speech(
                    "Für dieses Gerät habe ich noch kein belastbares Dauermodell."
                )
            else:
                return None
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        active = manager.active(conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.PREFERENCE_CONFIRMATION
            and isinstance(active.payload, LearningDialogPayload)
        ):
            if active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Präferenzrückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            model = (
                await registry.async_get(active.payload.preference_id)
                if active.payload.preference_id is not None else None
            )
            reply = classify_confirmation_reply(language_document.source_text)
            if reply is ConfirmationReply.NO:
                if model is not None and actor_id is not None:
                    try:
                        await async_reject_preference(registry, model.model_id, actor_id)
                    except LearningControlError:
                        pass
                manager.cancel(conversation_id)
                response.async_set_speech(
                    "In Ordnung. Die beobachtete Auswahl bleibt unverbindlich."
                )
            elif reply is not ConfirmationReply.YES:
                response.async_set_speech("Bitte antworte eindeutig mit Ja oder Nein.")
            elif model is None or actor_id is None:
                manager.cancel(conversation_id)
                response.async_set_speech("Der Präferenzkandidat ist nicht mehr verfügbar.")
            else:
                manager.cancel(conversation_id)
                try:
                    await async_confirm_preference(registry, model.model_id, actor_id)
                except LearningControlError as err:
                    response.async_set_speech(_learning_control_speech(err))
                else:
                    response.async_set_speech(
                        "Gespeichert. Diese Präferenz gilt nur im bestätigten Kontext."
                    )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.HABIT_SUGGESTION
            and isinstance(active.payload, LearningDialogPayload)
        ):
            if active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Gewohnheitsrückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            model = (
                await registry.async_get(active.payload.habit_id)
                if active.payload.habit_id is not None else None
            )
            reply = classify_confirmation_reply(language_document.source_text)
            if reply is ConfirmationReply.NO:
                if model is not None and actor_id is not None:
                    try:
                        await async_reject_habit(registry, model.model_id, actor_id)
                    except LearningControlError:
                        pass
                manager.cancel(conversation_id)
                response.async_set_speech(
                    "In Ordnung. Dieses unveränderte Muster schlage ich nicht erneut vor."
                )
            elif reply is not ConfirmationReply.YES:
                response.async_set_speech("Bitte antworte eindeutig mit Ja oder Nein.")
            elif model is None or actor_id is None:
                manager.cancel(conversation_id)
                response.async_set_speech("Der Gewohnheitskandidat ist nicht mehr verfügbar.")
            else:
                try:
                    routine: RoutineDefinition | None = await async_accept_habit(
                        registry, model.model_id, actor_id
                    )
                except LearningControlError:
                    routine = None
                if routine is None:
                    manager.cancel(conversation_id)
                    response.async_set_speech(
                        "Aus dem Kandidaten lässt sich keine sichere typisierte Routine bilden."
                    )
                else:
                    manager.create(
                        conversation_id, "habit-routine-preview",
                        DialogTaskKind.ROUTINE_DEFINITION,
                        DialogPriority.CONFIRMATION,
                        slots={"routine_id": routine.routine_id, "routine": routine},
                        reason="Ein bestätigter Habit-Kandidat wartet als V10-Routine auf Bestätigung.",
                        requested_by_user_id=actor_id,
                    )
                    preview = "; ".join(step.description for step in routine.steps)
                    response.async_set_speech(
                        f"Routinenvorschau {routine.name}: {preview}. Soll ich diese V10-Routine speichern?"
                    )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.MODEL_RESET_CONFIRMATION
            and isinstance(active.payload, LearningDialogPayload)
        ):
            if active.requested_by_user_id is not None and active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Lernrückfrage gehört zu einem anderen Benutzer.")
            else:
                reply = classify_confirmation_reply(language_document.source_text)
                if reply is ConfirmationReply.YES:
                    model_id = active.payload.model_id
                    predictive = self._runtime_data.predictive_house
                    if active.payload.operation is LearningDialogOperation.DELETE_MODEL and model_id is not None:
                        forgotten = await async_forget_model(registry, predictive, model_id)
                        deleted = int(forgotten is not None)
                        removed: tuple[LearnedModel, ...] = (
                            (forgotten,) if forgotten is not None else ()
                        )
                    else:
                        deleted, removed = await async_reset_models(registry, predictive)
                    updated_options = options_without_preference_aliases(
                        self.entry.options, removed, CONF_CUSTOM_ALIASES
                    )
                    if updated_options is not None:
                        self.hass.config_entries.async_update_entry(
                            self.entry, options=updated_options
                        )
                    response.async_set_speech(
                        f"{deleted} gelernte Modelle wurden gelöscht und für alte Evidenz unterdrückt."
                    )
                    manager.cancel(conversation_id)
                elif reply is ConfirmationReply.NO:
                    manager.cancel(conversation_id)
                    response.async_set_speech("Abgebrochen. Es wurde kein gelerntes Modell gelöscht.")
                else:
                    response.async_set_speech("Bitte antworte eindeutig mit Ja oder Nein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        request = interpret_learning_request(language_document.source_text)
        if request is None:
            return None
        # Personal knowledge (preferences, habits) is only ever described to
        # its authenticated owner - the same server-side visibility rule the
        # Learning Center applies.
        models = tuple(
            item for item in await registry.async_list()
            if (owner := model_owner(item)) is None or owner == actor_id
        )
        matched = tuple(item for item in models if _model_matches_hint(item, request.subject_hint))
        if request.operation is LearningOperation.LIST:
            if not matched:
                response.async_set_speech("Dazu ist kein aktives gelerntes Wissen gespeichert.")
            else:
                habit = next((
                    item for item in matched
                    if item.kind is LearnedKind.HABIT
                    and item.health is ModelHealth.VALID
                    and item.parameters.get("suggestion_status") == "new"
                ), None)
                policy = self._runtime_data.learning_policy
                preference = next((
                    item for item in matched
                    if item.kind is LearnedKind.PREFERENCE
                    and item.knowledge_state is KnowledgeState.INFERRED
                    and item.parameters.get("suggestion_status") == "new"
                ), None)
                if preference is not None and policy is not None and policy.suggestions_enabled:
                    await registry.async_upsert(replace(
                        preference,
                        parameters={**preference.parameters, "suggestion_status": "shown"},
                        model_version=preference.model_version + 1,
                    ))
                    manager.create(
                        conversation_id, "preference-suggestion",
                        DialogTaskKind.PREFERENCE_CONFIRMATION,
                        DialogPriority.CONFIRMATION,
                        reason="Eine statistische Auswahl benötigt ausdrückliche Autorität.",
                        requested_by_user_id=actor_id,
                        payload=LearningDialogPayload(
                            LearningDialogOperation.CONFIRM_PREFERENCE,
                            preference_id=preference.model_id,
                            requested_by_user_id=actor_id,
                        ),
                    )
                    response.async_set_speech(
                        f"In {preference.parameters.get('support_count', 0)} von "
                        f"{preference.sample_count} bestätigten Auswahlen hast du "
                        f"{preference.parameters.get('entity_id')} gewählt. "
                        f"Soll das in diesem Kontext dein Standard für {preference.subject} sein?"
                    )
                elif habit is not None and policy is not None and policy.suggestions_enabled:
                    await registry.async_upsert(replace(
                        habit,
                        parameters={**habit.parameters, "suggestion_status": "shown"},
                        model_version=habit.model_version + 1,
                    ))
                    manager.create(
                        conversation_id, "habit-suggestion",
                        DialogTaskKind.HABIT_SUGGESTION,
                        DialogPriority.CONFIRMATION,
                        reason="Ein belegter Ablauf kann nur nach Zustimmung zur Routine werden.",
                        requested_by_user_id=actor_id,
                        payload=LearningDialogPayload(
                            LearningDialogOperation.ACCEPT_HABIT,
                            habit_id=habit.model_id,
                            requested_by_user_id=actor_id,
                        ),
                    )
                    response.async_set_speech(
                        f"Du führst {_TIME_BAND_DE.get(str(habit.parameters.get('time_band', habit.context.get('time_band', ''))), 'regelmäßig')} "
                        f"häufig dieselbe Folge aus ({habit.sample_count} Belege). "
                        "Soll ich daraus eine Routine vorschlagen?"
                    )
                else:
                    descriptions = "; ".join(_learned_model_summary(item) for item in matched[:5])
                    # Assist cannot navigate the UI; it only points to the panel.
                    response.async_set_speech(
                        f"{descriptions}. Die vollständige Übersicht findest du "
                        "im HomeIntent Learning Center."
                    )
        elif request.operation is LearningOperation.EXPLAIN:
            if len(matched) != 1:
                response.async_set_speech("Dazu ist kein einzelnes belegtes Modell eindeutig.")
            else:
                response.async_set_speech(explain_learned_model(matched[0]))
        elif request.operation is LearningOperation.RESET:
            if actor_id is None or not await user_is_admin(self.hass, user_input):
                response.async_set_speech("Alle Lernmodelle darf nur ein authentifizierter Administrator zurücksetzen.")
            else:
                manager.create(
                    conversation_id, "learning-reset",
                    DialogTaskKind.MODEL_RESET_CONFIRMATION, DialogPriority.SAFETY,
                    reason="Alle lokalen Lernmodelle sollen persistent gelöscht werden.",
                    requested_by_user_id=actor_id,
                    payload=LearningDialogPayload(
                        LearningDialogOperation.RESET_MODELS,
                        requested_by_user_id=actor_id,
                    ),
                )
                response.async_set_speech("Soll ich wirklich alle lokalen Lernmodelle zurücksetzen?")
        else:
            if len(matched) != 1:
                response.async_set_speech("Das zu löschende Modell ist nicht eindeutig.")
            else:
                manager.create(
                    conversation_id, "learning-delete-model",
                    DialogTaskKind.MODEL_RESET_CONFIRMATION, DialogPriority.SAFETY,
                    reason="Ein gelerntes Modell soll persistent gelöscht werden.",
                    requested_by_user_id=actor_id,
                    payload=LearningDialogPayload(
                        LearningDialogOperation.DELETE_MODEL,
                        model_id=matched[0].model_id,
                        requested_by_user_id=actor_id,
                    ),
                )
                response.async_set_speech(
                    f"Soll ich das Modell {matched[0].model_id} wirklich löschen?"
                )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def _async_confirm_alias_learning(
        self, draft: AliasLearningDraft, actor_id: str | None
    ) -> None:
        """Store a confirmed alias as a binding (7.4.1).

        A plain alias belongs to the household; an alias taught for one room
        ("Mit Lampe meine ich im Wohnzimmer die Stehlampe") is the speaker's
        own and only applies in that room.
        """
        personal = draft.area_id is not None and actor_id is not None
        await self._runtime_data.bindings.async_bind(
            BindingKind.ALIAS,
            draft.alias,
            draft.entity_id,
            confirmed=True,
            scope=BindingScope.USER if personal else BindingScope.HOUSEHOLD,
            user_id=actor_id if personal else None,
            created_by=actor_id,
            now=dt_util.now(),
            data={
                "spoken": draft.alias,
                **({"area_id": draft.area_id} if draft.area_id is not None else {}),
            },
        )
        self._runtime_data.learning_center_revision.bump()

    async def _async_apply_confirmed_preferences(
        self,
        entities: list[EntitySnapshot],
        *,
        area_id: str | None,
        user_id: str | None,
    ) -> list[EntitySnapshot]:
        """Expose confirmed preferences as aliases only in their exact context."""
        entities = self.learned_alias_view(entities, area_id=area_id, user_id=user_id)
        registry = self._runtime_data.learned_models
        if registry is None or user_id is None:
            return entities
        models = await registry.async_list(kind=LearnedKind.PREFERENCE)
        aliases_by_entity: dict[str, list[str]] = {}
        for model in models:
            if model.knowledge_state is not KnowledgeState.CONFIRMED:
                continue
            if model.context.get("user_id") != user_id:
                continue
            model_area = model.context.get("area_id")
            if model_area is not None and model_area != area_id:
                continue
            entity_id = model.parameters.get("entity_id")
            if isinstance(entity_id, str):
                aliases_by_entity.setdefault(entity_id, []).append(model.subject)
        return [
            replace(
                item,
                aliases=tuple(dict.fromkeys((*item.aliases, *aliases_by_entity[item.entity_id]))),
            )
            if item.entity_id in aliases_by_entity
            and any(
                model.knowledge_state is KnowledgeState.CONFIRMED
                and model.context.get("user_id") == user_id
                and model.parameters.get("entity_id") == item.entity_id
                and (
                    model.context.get("area_id") is None
                    or model.context.get("area_id") == item.area_id == area_id
                )
                for model in models
            )
            else item
            for item in entities
        ]

    async def _async_handle_memory_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Handle explicit memory meanings after the shared language frontend."""
        store = self._runtime_data.memory
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        active = manager.active(conversation_id)
        if active is not None and active.kind is DialogTaskKind.MEMORY_CONFIRMATION:
            actor_id = conversation_user_id(user_input)
            if (
                active.requested_by_user_id is not None
                and active.requested_by_user_id != actor_id
            ):
                response.async_set_speech("Diese Gedächtnisrückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(
                    response=response, conversation_id=conversation_id
                )
            normalized = language_document.normalized_text.casefold()
            if re.search(r"\bwas\s+hast\s+du\s+verstanden\b", normalized):
                response.async_set_speech(manager.understood(conversation_id))
            elif re.search(r"\bwarum\s+fragst\s+du\b", normalized):
                response.async_set_speech(manager.explain(conversation_id))
            elif re.search(r"\b(?:abbrechen|vergiss\s+es|lass\s+das)\b", normalized):
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Ich speichere und lösche nichts.")
            else:
                reply = classify_confirmation_reply(language_document.source_text)
                if reply is ConfirmationReply.NO:
                    manager.cancel(conversation_id)
                    response.async_set_speech("In Ordnung. Es wurde nichts dauerhaft geändert.")
                elif reply is ConfirmationReply.YES:
                    operation = active.slots.get("operation")
                    if operation == MemoryOperation.RESET.value:
                        deleted = await store.async_reset() if store is not None else 0
                        response.async_set_speech(
                            counted_passive(
                                deleted, "gespeicherter Eintrag", "gespeicherte Einträge",
                                "kontrolliert gelöscht",
                            )
                        )
                    elif operation == MemoryOperation.FORGET_PERSON.value:
                        person_id = active.slots.get("person_id")
                        deleted = (
                            await store.async_forget_person(person_id)
                            if store is not None and isinstance(person_id, str)
                            else 0
                        )
                        response.async_set_speech(
                            counted_passive(
                                deleted, "dir zugeordneter Eintrag", "dir zugeordnete Einträge",
                                "kontrolliert gelöscht",
                            )
                        )
                    elif operation == MemoryOperation.FORGET_PREFERENCE.value:
                        memory_id = active.slots.get("memory_id")
                        deleted = (
                            await store.async_forget(memory_id)
                            if store is not None and isinstance(memory_id, str)
                            else False
                        )
                        response.async_set_speech(
                            "Die Präferenz wurde kontrolliert gelöscht."
                            if deleted
                            else "Die Präferenz war nicht mehr vorhanden."
                        )
                    elif operation == MemoryOperation.CORRECT_PREFERENCE.value:
                        memory_id = active.slots.get("memory_id")
                        content = active.slots.get("content")
                        corrected = (
                            await store.async_correct(memory_id, content, confirmed=True)
                            if store is not None
                            and isinstance(memory_id, str)
                            and isinstance(content, dict)
                            else None
                        )
                        response.async_set_speech(
                            "Die Präferenz wurde korrigiert."
                            if corrected is not None
                            else "Die Präferenz war nicht mehr vorhanden."
                        )
                    elif store is None or not store.enabled:
                        response.async_set_speech(MEMORY_DISABLED_TEXT)
                    else:
                        content = active.slots.get("content")
                        person_id = active.slots.get("person_id")
                        if not isinstance(content, dict) or not isinstance(person_id, str):
                            response.async_set_speech("Die Erinnerung ist nicht mehr vollständig. Ich speichere nichts.")
                        else:
                            raw_kind = active.slots.get("kind")
                            try:
                                kind = (
                                    MemoryKind(str(raw_kind))
                                    if raw_kind is not None
                                    else MemoryKind.PREFERENCE
                                )
                            except ValueError:
                                response.async_set_speech(
                                    "Der Erinnerungstyp ist ungültig. Ich speichere nichts."
                                )
                                manager.cancel(conversation_id)
                                return conversation.ConversationResult(
                                    response=response,
                                    conversation_id=conversation_id,
                                )
                            await store.async_remember(
                                kind,
                                content,
                                provenance=FactProvenance.CONFIRMED_MEMORY,
                                confirmed=True,
                                person_id=person_id,
                            )
                            response.async_set_speech("Gespeichert. Du kannst diese Erinnerung jederzeit kontrolliert löschen.")
                    manager.cancel(conversation_id)
                elif len(language_document.tokens) <= 3:
                    response.async_set_speech("Bitte antworte eindeutig mit Ja oder Nein.")
                else:
                    # A complete new command replaces this optional memory dialog.
                    manager.cancel(conversation_id)
                    return None
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        request = interpret_memory_intent(language_document, entities)
        if request is None:
            return None
        if store is None or not store.enabled:
            response.async_set_speech(MEMORY_DISABLED_TEXT)
        elif request.operation is MemoryOperation.LIST:
            records = await store.async_list(person_id=conversation_user_id(user_input))
            # Say what is remembered, in German - not internal kind names
            # ("1 preference") (F16/F23).
            labels = {entity.entity_id: entity.friendly_name for entity in entities}
            described = [_describe_memory(record, labels) for record in records[:8]]
            more = f" und {len(records) - 8} weitere Einträge" if len(records) > 8 else ""
            response.async_set_speech(
                f"Ich habe mir gemerkt: {'; '.join(described)}{more}."
                if described else "Für dich sind keine dauerhaften Erinnerungen gespeichert."
            )
        elif request.operation is MemoryOperation.EXPORT_REDACTED:
            exported = await store.async_redacted_export()
            exported_counts = exported.get("record_counts", {})
            summary = ", ".join(
                f"{count} {_MEMORY_KIND_DE.get(str(kind), str(kind))}"
                for kind, count in sorted(exported_counts.items())
            ) if isinstance(exported_counts, dict) else ""
            response.async_set_speech(
                "Der redigierte Export enthält nur Zähler und Herkunftsklassen"
                + (f": {summary}." if summary else ". Es sind keine aktiven Einträge vorhanden.")
            )
        elif request.operation is MemoryOperation.FORGET_ROUTINES:
            person_id = conversation_user_id(user_input)
            if person_id is None:
                response.async_set_speech("Persönliche Routinen kann ich nur einem authentifizierten Benutzer zuordnen.")
            else:
                deleted = await store.async_forget_person(
                    person_id,
                    kinds=(MemoryKind.ROUTINE_GRANT, MemoryKind.DECISION),
                )
                response.async_set_speech(f"{deleted} persönliche Routinen und Routineentscheidungen wurden gelöscht.")
        elif request.operation is MemoryOperation.FORGET_PERSON:
            person_id = conversation_user_id(user_input)
            if person_id is None:
                response.async_set_speech(
                    "Persönliche Daten kann ich nur einem authentifizierten Benutzer zuordnen."
                )
            else:
                manager.create(
                    conversation_id,
                    "memory-forget-person",
                    DialogTaskKind.MEMORY_CONFIRMATION,
                    DialogPriority.SAFETY,
                    slots={
                        "operation": MemoryOperation.FORGET_PERSON.value,
                        "person_id": person_id,
                    },
                    reason="Alle diesem Benutzer zugeordneten Erinnerungen sollen gelöscht werden.",
                    requested_by_user_id=person_id,
                )
                response.async_set_speech(
                    "Soll ich wirklich alle dir zugeordneten HomeIntent-Erinnerungen löschen?"
                )
        elif request.operation is MemoryOperation.RESET:
            reset_user_id = conversation_user_id(user_input)
            if reset_user_id is None or not await user_is_admin(self.hass, user_input):
                response.async_set_speech("Das vollständige Gedächtnis darf nur ein authentifizierter Administrator zurücksetzen.")
                return conversation.ConversationResult(
                    response=response, conversation_id=conversation_id
                )
            manager.create(
                conversation_id,
                "memory-reset",
                DialogTaskKind.MEMORY_CONFIRMATION,
                DialogPriority.SAFETY,
                slots={"operation": MemoryOperation.RESET.value},
                reason="Das würde alle dauerhaften HomeIntent-Erinnerungen löschen.",
                requested_by_user_id=reset_user_id,
            )
            response.async_set_speech("Soll ich wirklich alle dauerhaften HomeIntent-Erinnerungen löschen?")
        elif request.target_entity_id is None:
            if request.ambiguous_entity_ids:
                response.async_set_speech("Mehrere Geräte passen zur genannten Präferenz. Bitte nenne eines eindeutig.")
            else:
                response.async_set_speech("Welches eindeutige Gerät soll Teil dieser Präferenz sein?")
        elif request.operation in {
            MemoryOperation.CORRECT_PREFERENCE,
            MemoryOperation.FORGET_PREFERENCE,
        }:
            person_id = conversation_user_id(user_input)
            records = (
                await store.async_list(
                    person_id=person_id, kinds=(MemoryKind.PREFERENCE,)
                )
                if person_id is not None
                else ()
            )
            matches = [
                record for record in records
                if record.content.get("entity_id") == request.target_entity_id
            ]
            if len(matches) != 1:
                response.async_set_speech(
                    "Zu diesem Gerät ist keine eindeutige persönliche Präferenz gespeichert."
                )
            else:
                record = matches[0]
                corrected_content = {
                    **record.content,
                    **request.content,
                    "entity_id": request.target_entity_id,
                }
                manager.create(
                    conversation_id,
                    "memory-preference-change",
                    DialogTaskKind.MEMORY_CONFIRMATION,
                    DialogPriority.CONFIRMATION,
                    slots={
                        "operation": request.operation.value,
                        "memory_id": record.memory_id,
                        "content": corrected_content,
                        "person_id": person_id,
                    },
                    reason="Eine dauerhafte persönliche Präferenz soll geändert werden.",
                    requested_by_user_id=person_id,
                )
                response.async_set_speech(
                    "Soll ich diese Präferenz dauerhaft korrigieren?"
                    if request.operation is MemoryOperation.CORRECT_PREFERENCE
                    else "Soll ich diese Präferenz kontrolliert löschen?"
                )
        else:
            person_id = conversation_user_id(user_input)
            if person_id is None:
                response.async_set_speech("Persönliche Präferenzen speichere ich nur für einen authentifizierten Benutzer.")
            else:
                content = {**request.content, "entity_id": request.target_entity_id}
                manager.create(
                    conversation_id,
                    "memory-preference",
                    DialogTaskKind.MEMORY_CONFIRMATION,
                    DialogPriority.CONFIRMATION,
                    slots={
                        "operation": request.operation.value,
                        "content": content,
                        "person_id": person_id,
                    },
                    reason="Eine persönliche Präferenz soll dauerhaft gespeichert werden.",
                    requested_by_user_id=person_id,
                )
                response.async_set_speech("Soll ich mir diese persönliche Präferenz dauerhaft merken?")
        return conversation.ConversationResult(
            response=response, conversation_id=conversation_id
        )

    async def _async_handle_undo_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: ConversationContext | None,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Undo the most recent safely reversible action for this user."""
        undo = pending.pending_undo if pending is not None else None
        current_user_id = conversation_user_id(user_input)
        if undo is None:
            response.async_set_speech(
                "Es gibt keine kürzlich ausgeführte, sicher rückgängig machbare Aktion."
            )
        elif (
            undo.requested_by_user_id is not None
            and undo.requested_by_user_id != current_user_id
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Diese Rücknahme gehört zu einem anderen Benutzer.",
            )
        else:
            is_admin = await user_is_admin(self.hass, user_input)
            denial = next(
                (
                    decision.reason
                    for plan in undo.plans
                    if (
                        decision := evaluate_service_plan(
                            plan,
                            entities,
                            self.entry.options,
                            is_admin=is_admin,
                            user_id=current_user_id,
                            effects=build_plan_effects(self.hass, plan),
                        )
                    ).outcome
                    is PolicyOutcome.DENY
                ),
                None,
            )
            if denial is not None:
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    denial,
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
            self._context_store.clear(user_input.conversation_id)
            try:
                for plan in undo.plans:
                    execution = await async_execute_service_plan(
                        self.hass,
                        plan,
                        entities,
                        self.entry.options,
                        is_admin=is_admin,
                        user_id=current_user_id,
                        confirmed=True,
                        audit_trail=self._audit_trail,
                        audit_actor_id=current_user_id,
                        effect_monitor=self._runtime_data.effect_monitor,
                    )
                    if not execution.executed:
                        raise RuntimeError(execution.error or "Rücknahme nicht erlaubt")
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Undo service call failed: %s", err, exc_info=True)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Rückgängigmachen: {err}",
                )
            else:
                response.async_set_speech("Die letzte Aktion wurde rückgängig gemacht.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_explanation_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: ConversationContext | None,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Explain the pending automation or the last understood command."""
        if pending is not None and pending.pending_automation_confirmation is not None:
            speech = (
                "Ich habe folgende Automation verstanden:\n"
                + render_automation_preview(
                    pending.pending_automation_confirmation.model, entities
                )
            )
        elif pending is not None and pending.last_command is not None:
            speech = explain_command(pending.last_command)
        elif pending is not None and (
            pending.memory is not None and pending.memory.explanation is not None
            or pending.last_explanation is not None
        ):
            speech = (
                pending.memory.explanation
                if pending.memory is not None and pending.memory.explanation is not None
                else pending.last_explanation
            )
        else:
            speech = "In diesem Gespräch gibt es noch keinen verstandenen Befehl."
        response.async_set_speech(speech)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _start_automation_wizard(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
    ) -> conversation.ConversationResult:
        """Start the bounded automation wizard."""
        state = AutomationWizardState()
        self._store_automation_wizard(user_input.conversation_id, state)
        response.async_set_speech(
            "Was soll die Automation auslösen? Bitte nenne einen vollständigen Auslöser."
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_audit_query(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
    ) -> conversation.ConversationResult:
        """Render today's bounded HomeIntent execution audit."""
        response.async_set_speech(render_today(self._audit_trail.today(dt_util.now())))
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_history_query_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        history_query: HistoryQuery | StateHistoryQuery | ComparativeHistoryQuery,
    ) -> conversation.ConversationResult:
        """Execute a read-only recorder query and render its answer."""
        self._context_store.clear(user_input.conversation_id)
        response.async_set_speech(
            await async_execute_history_query(self.hass, history_query)
        )
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_advanced_answer(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        answer: str,
    ) -> conversation.ConversationResult:
        """Return an already composed advanced-query answer."""
        self._context_store.clear(user_input.conversation_id)
        response.async_set_speech(answer)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_calendar_draft(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        draft: CalendarEventDraft,
        calendars: tuple[EntitySnapshot, ...],
    ) -> conversation.ConversationResult:
        """Store a calendar draft and ask for its next missing value."""
        if not calendars:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(
                "Ich finde keinen für HomeIntent freigegebenen, beschreibbaren Kalender."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        question = calendar_event_question(draft, calendars)
        awaiting_confirmation = question is None
        self._store_calendar_event(
            user_input.conversation_id, draft, awaiting_confirmation
        )
        response.async_set_speech(
            render_calendar_event_preview(draft, calendars)
            if awaiting_confirmation
            else question
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_structure_edit_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: AutomationStructureEditRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Resolve and prepare a trigger or condition edit."""
        if self._automation_executor is None:
            self._automation_executor = AutomationExecutor(self.hass)
        automations = await self._automation_executor.async_list_automations()
        candidates = tuple(
            item
            for item in homeintent_candidates(automations, user_input.text, entities)
            if not item.once
        )
        if not candidates:
            response.async_set_speech(
                "Ich finde keine passende dauerhafte HomeIntent-Automation. "
                "Einmalige Aufträge werden aus Sicherheitsgründen nicht strukturell geändert."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        pending_edit = PendingAutomationStructureEdit(
            request=request,
            candidates=candidates,
            automation=candidates[0] if len(candidates) == 1 else None,
        )
        if len(candidates) > 1:
            store_automation_structure_edit(
                self, user_input.conversation_id, pending_edit
            )
            response.async_set_speech(
                "Welche Automation meinst du? "
                + ", ".join(_automation_label(item) for item in candidates)
                + "."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if request.payload or request.operation in {
            AutomationEditOperation.CLEAR,
            AutomationEditOperation.REMOVE,
        }:
            return await async_prepare_automation_structure_edit(
                self,
                user_input,
                response,
                pending_edit,
                entities,
                request.payload,
            )
        store_automation_structure_edit(self, user_input.conversation_id, pending_edit)
        response.async_set_speech(
            "Wie soll der neue Auslöser lauten?"
            if request.section.name == "TRIGGERS"
            else "Welche Bedingung soll gelten?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_action_edit_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot] | None = None,
    ) -> conversation.ConversationResult:
        """Resolve an automation and begin its action-only edit dialog."""
        if self._automation_executor is None:
            self._automation_executor = AutomationExecutor(self.hass)
        automations = await self._automation_executor.async_list_automations()
        candidates = homeintent_candidates(automations, user_input.text, entities)
        operation = action_edit_operation(user_input.text)
        if not candidates:
            response.async_set_speech("Ich finde keine änderbare HomeIntent-Automation.")
        elif len(candidates) > 1:
            store_automation_action_edit(
                self,
                user_input.conversation_id,
                PendingAutomationActionEdit(
                    candidates=candidates, operation=operation
                ),
            )
            response.async_set_speech(
                "Welche Automation meinst du? "
                + ", ".join(_automation_label(item) for item in candidates)
                + "."
            )
        else:
            automation = next(iter(candidates))
            reordered = reordered_actions(automation.actions, operation)
            if operation.startswith("reorder:") and reordered is None:
                response.async_set_speech(
                    "Diese Automation hat nicht genügend Aktionen für diese Reihenfolge."
                )
            else:
                store_automation_action_edit(
                    self,
                    user_input.conversation_id,
                    PendingAutomationActionEdit(
                        candidates=candidates,
                        automation=automation,
                        rendered_actions=reordered or (),
                        action_text=user_input.text if reordered else None,
                        operation=operation,
                    ),
                )
                response.async_set_speech(
                    "Soll ich die Reihenfolge der Aktionen wie gewünscht ändern?"
                    if reordered
                    else "Welche Aktion soll zusätzlich danach ausgeführt werden?"
                    if operation == "add"
                    else "Was soll stattdessen passieren? Auslöser und Bedingungen bleiben unverändert."
                )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_pending_automation_confirmation_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingAutomationConfirmation,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Revise a pending automation or interpret its confirmation reply."""
        revised = self._engine.revise_pending_automation(
            user_input.text,
            pending.model,
            entities,
            self._world_model,
        )
        if revised is not None and revised.validation_error is None:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_confirmation=PendingAutomationConfirmation(
                        model=revised.model,
                        requested_by_user_id=pending.requested_by_user_id,
                    ),
                ),
            )
            response.async_set_speech(render_automation_preview(revised.model, entities))
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self._async_handle_automation_confirmation_reply(
            user_input, response, pending, entities
        )

    async def _async_handle_pending_semantic_command(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingSemanticCommand,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Continue a deterministic slot-filling device dialog."""
        # A complete new turn supersedes the open slot dialog.  Otherwise a
        # command such as "Fahre die Rollläden ..." is interpreted as an
        # attempted thermostat name merely because the preceding turn asked
        # "Welche Heizung?".  Only a fully parsed fresh command/query (or a
        # clearly command-shaped no-match that may enter bounded ASR
        # correction) escapes; short answers such as "Küche" continue below.
        fresh = self._engine.understand(
            user_input.text,
            entities,
            self._world_model,
            context=UnderstandingContext(
                source_area=resolve_conversation_area(self.hass, user_input)
            ),
        ).payload
        if isinstance(fresh, CommandPlan):
            self._context_store.clear(user_input.conversation_id)
            return await self._async_handle_command_plan(
                user_input, response, fresh, entities
            )
        if isinstance(fresh, MatchResult) and (
            fresh.command is not None or fresh.clarification is not None
        ):
            self._context_store.clear(user_input.conversation_id)
            if fresh.clarification is not None:
                return await self._async_clarify(
                    user_input, response, fresh, entities
                )
            return await self._async_handle_match_result(
                user_input, response, fresh, entities
            )
        if analyse_utterance(user_input.text).speech_act is SpeechAct.COMMAND:
            self._context_store.clear(user_input.conversation_id)
            return await self._async_handle_no_match(
                user_input, response, entities
            )

        dialog = continue_semantic_dialog(user_input.text, pending)
        if dialog.result is not None:
            self._context_store.clear(user_input.conversation_id)
            return await self._async_handle_device_control_result(
                user_input, response, dialog.result
            )
        self._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_semantic_command=dialog.pending,
            ),
        )
        response.async_set_speech(dialog.question)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_pending_automation_draft(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        draft: PendingAutomationDraft,
        pending: ConversationContext,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Complete the action missing from a pending automation draft."""
        if classify_confirmation_reply(user_input.text) is ConfirmationReply.NO:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech("In Ordnung. Ich lege keine Automation an.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        completed = self._engine.complete_automation_draft(
            user_input.text,
            draft.trigger,
            draft.source_text,
            entities,
            self._world_model,
            pending,
        )
        if completed is None:
            response.async_set_speech(
                "Was soll dann passieren? Bitte nenne eine vollständige Aktion."
            )
        elif completed.validation_error is not None:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(completed.response_text)
        else:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_confirmation=PendingAutomationConfirmation(
                        model=completed.model,
                        requested_by_user_id=conversation_user_id(user_input),
                    ),
                ),
            )
            response.async_set_speech(render_automation_preview(completed.model, entities))
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _script_steps(self, entity_id: str) -> list[dict[str, object]] | None:
        """The configured action sequence of one script entity, if readable."""
        component = self.hass.data.get("script")
        get_entity = getattr(component, "get_entity", None)
        if get_entity is None:
            return None
        script_entity = get_entity(entity_id)
        config = getattr(script_entity, "raw_config", None)
        sequence = config.get("sequence") if isinstance(config, dict) else None
        if not isinstance(sequence, list):
            return None
        return [step for step in sequence if isinstance(step, dict)]

    async def _async_handle_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Authorize, execute and remember one regular engine match."""
        self._runtime_data.shadow.observe(self.entry.options, user_input.text, entities, result)
        if (
            result.plan is not None
            and analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
        ):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe eine Frage erkannt und führe deshalb keine Aktion aus.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if result.plan is not None:
            policy = evaluate_service_plan(
                result.plan,
                entities,
                self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=conversation_user_id(user_input),
                effects=build_plan_effects(self.hass, result.plan),
                origin=result.origin,
                binding_confirmed=result.binding_confirmed,
            )
            if policy.outcome is PolicyOutcome.DENY:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    policy.reason or "Diese Aktion ist nicht erlaubt.",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if policy.outcome is PolicyOutcome.CONFIRM:
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_service_confirmation=PendingServiceConfirmation(
                            result.plan,
                            result.response_text,
                            conversation_user_id(user_input),
                            build_undo_plan(
                                result.plan,
                                entities,
                                requested_by_user_id=conversation_user_id(user_input),
                            ),
                            origin=result.origin,
                            binding_confirmed=result.binding_confirmed,
                        ),
                    ),
                )
                question = result.proposal_text or _confirmation_question(result.response_text)
                response.async_set_speech(f"{policy.note} {question}" if policy.note else question)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

        previous_context = self._context_store.get(user_input.conversation_id)
        undo_plan = (
            build_undo_plan(
                result.plan,
                entities,
                requested_by_user_id=conversation_user_id(user_input),
            )
            if result.plan is not None
            else None
        )
        if result.command is not None:
            focus = derive_dialog_focus(result.command)
            discourse = remember_entities(
                previous_context.discourse if previous_context else None,
                result.command.entities,
                role=(
                    DiscourseRole.ACTION_TARGET
                    if result.plan is not None
                    else DiscourseRole.QUERY_RESULT
                ),
                active_property=focus.property,
                active_action=(
                    result.command.intent if result.plan is not None else None
                ),
                semantic_graph=result.command.source_frame.semantic_graph,
            )
            query_result = result.command.parameters.get("query_result")
            if isinstance(query_result, QueryResult) and (
                query_result.member_ids
                or query_result.entities
                or query_result.devices
                or query_result.areas
                or query_result.floors
            ):
                discourse = remember_query_group(
                    discourse,
                    query_result,
                    semantic_graph=result.command.source_frame.semantic_graph,
                )
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=result.command,
                    last_entities=tuple(result.command.entities),
                    last_area=result.command.area,
                    pending_clarification=None,
                    focus=focus,
                    discourse=discourse,
                    pending_undo=undo_plan,
                    last_explanation=result.explanation_text,
                    memory=DialogTurnMemory(
                        source_text=user_input.text,
                        entities=tuple(result.command.entities),
                        explanation=result.explanation_text,
                        command=result.command,
                    ),
                ),
            )
        elif result.context_entities:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=result.context_entities,
                    last_area=None,
                    pending_clarification=None,
                    last_query_predicate=result.context_predicate,
                    last_explanation=result.explanation_text,
                    discourse=remember_entities(
                        previous_context.discourse if previous_context else None,
                        result.context_entities,
                        role=DiscourseRole.QUERY_RESULT,
                    ),
                    memory=DialogTurnMemory(
                        source_text=user_input.text,
                        entities=result.context_entities,
                        predicate=result.context_predicate,
                        explanation=result.explanation_text,
                    ),
                ),
            )
        else:
            self._context_store.clear(user_input.conversation_id)

        if result.plan is not None:
            execution = await async_execute_service_plan(
                self.hass,
                result.plan,
                entities,
                self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=conversation_user_id(user_input),
                confirmed=False,
                audit_trail=self._audit_trail,
                audit_actor_id=conversation_user_id(user_input),
                effect_monitor=self._runtime_data.effect_monitor,
                origin=result.origin,
                binding_confirmed=result.binding_confirmed,
            )
            if not execution.executed:
                self._context_store.clear(user_input.conversation_id)
                _LOGGER.error(
                    "Service call %s.%s on %s failed: %s",
                    result.plan.domain,
                    result.plan.service,
                    result.plan.entity_id,
                    execution.error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ausführen: {execution.error}",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            result = replace(
                result, response_text=_with_effect_summary(result.response_text, execution)
            )

        if (
            result.command is not None and result.command.intent in QUERY_INTENT_NAMES
        ) or (
            analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
            or result.context_predicate is not None
        ):
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_automation_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationMatchResult,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Store a valid automation preview, or report its validation error."""
        if result.validation_error is None:
            speaker_bound, failure = self._materialize_presence_speaker(result.model, user_input)
            if failure is None:
                materialized, failure = self._materialize_notification_recipients(
                    speaker_bound, user_input, entities
                )
            else:
                materialized = result.model
            if failure is not None:
                # Understood, but nobody to deliver to: say so instead of
                # silently degrading into an HA persistent notification.
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech(failure)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            result = replace(result, model=materialized)
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_confirmation=PendingAutomationConfirmation(
                        model=result.model,
                        requested_by_user_id=conversation_user_id(user_input),
                    ),
                ),
            )
            response.async_set_speech(render_automation_preview(result.model, entities))
        else:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _notification_target_resolver(
        self, entities: list[EntitySnapshot]
    ) -> NotificationTargetResolver:
        labels = {item.entity_id: item.friendly_name for item in entities}
        return NotificationTargetResolver.from_options(
            self.entry.options,
            self._runtime_data.user_contexts,
            label_for=lambda target_id: labels.get(target_id, ""),
            named_targets=named_notification_targets(
                entities, self._runtime_data.user_contexts
            ),
        )

    def _materialize_presence_speaker(
        self,
        model: AutomationModel,
        user_input: conversation.ConversationInput,
    ) -> tuple[AutomationModel, str | None]:
        """Bind "ich komme nach Hause" to the speaker's own person entity.

        Only an explicit, confirmed user/person binding is used - never a
        person guessed from a similar name.
        """
        if not any(trigger.presence_of_speaker for trigger in model.triggers):
            return model, None
        store = self._runtime_data.user_contexts
        binding = (
            store.resolve_current_person(conversation_user_id(user_input))
            if store is not None else None
        )
        if binding is None or binding.person_entity_id is None:
            return model, (
                "Ich weiß noch nicht, welche Person du bist. Bitte ordne deinem "
                "HomeIntent-Benutzer eine Person zu."
            )
        triggers = tuple(
            replace(trigger, target=TriggerTarget(domain="person", entity_id=binding.person_entity_id))
            if trigger.presence_of_speaker else trigger
            for trigger in model.triggers
        )
        return replace(model, triggers=triggers), None

    def _materialize_notification_recipients(
        self,
        model: AutomationModel,
        user_input: conversation.ConversationInput,
        entities: list[EntitySnapshot],
    ) -> tuple[AutomationModel, str | None]:
        """Turn "mich"/"uns" into the exact authorized notify targets.

        The automation runs later without a live conversation user, so the
        semantic recipient is resolved now - through the single
        ``NotificationTargetResolver`` - and the exact targets are persisted.
        """
        resolver: NotificationTargetResolver | None = None
        failure: str | None = None

        def materialize(step: ActionModel | ActionGroup) -> ActionModel | ActionGroup:
            nonlocal resolver, failure
            if isinstance(step, ActionGroup):
                return replace(step, steps=tuple(materialize(child) for child in step.steps))
            recipient = step.recipient
            if (
                step.type is not ActionType.NOTIFY
                or recipient is None
                or recipient.materialized
                or failure is not None
            ):
                return step
            if resolver is None:
                resolver = self._notification_target_resolver(entities)
            resolution = resolver.resolve(recipient.kind, conversation_user_id(user_input))
            if not resolution.resolved:
                failure = resolution_failure_text(resolution)
                return step
            return replace(step, recipient=NotificationRecipient(
                recipient.kind,
                entity_ids=resolution.entity_ids,
                service_ids=resolution.service_ids,
                label=resolution.label,
            ))

        actions = tuple(materialize(step) for step in model.actions)
        if failure is not None:
            return model, failure
        return replace(model, actions=actions), None

    async def _async_handle_immediate_notification(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: NotificationRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Explicit push the user asked for right now.

        Independent of proactive situation detection and V12 opportunity
        policies; only the push channel and a safely resolved target matter.
        """
        self._context_store.clear(user_input.conversation_id)
        outcome = await async_deliver_notification_request(
            request,
            resolver=self._notification_target_resolver(entities),
            delivery=AgentDelivery(self.hass),
            user_id=conversation_user_id(user_input),
        )
        response.async_set_speech(outcome.spoken())
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_automation_clarification_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationClarificationResult,
    ) -> conversation.ConversationResult:
        """Ask the one open question of an automation draft - nothing runs.

        Only a device choice keeps the draft; the answer ("Die linke.")
        continues exactly this automation and nothing else.
        """
        if result.clarification is not None:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_event_clarification=PendingAutomationEventClarification(
                        clarification=result.clarification,
                        requested_by_user_id=conversation_user_id(user_input),
                    ),
                ),
            )
        else:
            self._context_store.clear(user_input.conversation_id)
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_pending_event_clarification(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingAutomationEventClarification,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Resolve "Die linke." against exactly the open draft.

        Another user's answer, or a reply that selects none/several of the
        offered devices, never continues the draft: the draft is dropped and
        the turn is processed as a fresh utterance (``None``).
        """
        if pending.requested_by_user_id not in {None, conversation_user_id(user_input)}:
            return None
        result = self._engine.resolve_event_clarification(
            user_input.text, pending.clarification, entities
        )
        if result is None:
            if looks_like_selection_reply(user_input.text):
                # A short answer that names no offered device is still an
                # answer to this question - ask again, never guess.
                response.async_set_speech(
                    "Das konnte ich keiner der Möglichkeiten zuordnen. "
                    + (pending.clarification.grounded.question or "Welches Gerät meinst du?")
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            self._context_store.clear(user_input.conversation_id)
            return None
        if isinstance(result, AutomationClarificationResult):
            return self._handle_automation_clarification_result(user_input, response, result)
        return self._handle_automation_match_result(user_input, response, result, entities)

    def _handle_automation_draft_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationDraftMatchResult,
    ) -> conversation.ConversationResult:
        """Persist a partial automation until its action is supplied."""
        self._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_automation_draft=PendingAutomationDraft(
                    trigger=result.trigger,
                    source_text=result.source_text,
                ),
            ),
        )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _handle_automation_deletion_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationDeletionMatchResult,
    ) -> conversation.ConversationResult:
        """Store an unambiguous deletion candidate for confirmation."""
        if result.automation is not None:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_deletion=PendingAutomationDeletion(
                        automation=result.automation
                    ),
                ),
            )
        else:
            self._context_store.clear(user_input.conversation_id)
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_automation_toggle_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationToggleMatchResult,
    ) -> conversation.ConversationResult:
        """Apply an unambiguous enable/disable result immediately."""
        self._context_store.clear(user_input.conversation_id)
        if result.automation is not None:
            if self._automation_executor is None:
                self._automation_executor = AutomationExecutor(self.hass)
            try:
                if result.enable:
                    await self._automation_executor.async_enable_automation(
                        result.automation.automation_id
                    )
                else:
                    await self._automation_executor.async_disable_automation(
                        result.automation.automation_id
                    )
            except Exception as err:  # noqa: BLE001 - HA/YAML failures are heterogeneous
                verb = "Aktivieren" if result.enable else "Deaktivieren"
                _LOGGER.error("Automation %s failed: %s", verb.lower(), err)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim {verb} der Automation: {err}",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_clarify(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Ask - unless the speaker confirmed a default choice for exactly
        this question (7.4.1); the chosen candidate is one the question
        itself offered and runs through the ordinary handlers."""
        clarification = result.clarification
        if clarification is not None:
            area = resolve_conversation_area(self.hass, user_input)
            chosen = self._default_choice(
                user_input, clarification.candidates,
                area.area_id if area is not None else None,
            )
            if chosen is not None:
                resolved = self._engine.resolve_clarification(
                    chosen.entity_id, clarification, entities
                )
                if resolved is not None:
                    return await self._async_handle_match_result(
                        user_input, response, resolved, entities
                    )
        return self._handle_clarification_result(user_input, response, result)

    def _handle_clarification_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult,
    ) -> conversation.ConversationResult:
        """Store an ambiguous match until the user selects a candidate."""
        self._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=result.clarification,
            ),
        )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_no_match(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Try bounded correction/dialog fallbacks for an unmatched turn."""
        is_query = analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
        corrected_plans: dict[
            tuple, tuple[ServiceCallPlan, str, PhoneticSuggestion]
        ] = {}
        for suggestion in (
            () if is_query else phonetic_suggestions(user_input.text, entities)
        ):
            corrected = self._engine.understand(
                suggestion.corrected_text,
                entities,
                self._world_model,
                context=UnderstandingContext(
                    source_area=resolve_conversation_area(self.hass, user_input)
                ),
            ).payload
            plan = getattr(corrected, "plan", None)
            success = getattr(corrected, "response_text", None)
            if plan is None or not success:
                continue
            entity_ids = (
                tuple(plan.entity_id)
                if isinstance(plan.entity_id, list)
                else (plan.entity_id,)
            )
            key = (plan.domain, plan.service, entity_ids, repr(sorted(plan.data.items())))
            corrected_plans[key] = (plan, success, suggestion)
        if len(corrected_plans) == 1:
            plan, success, correction = next(iter(corrected_plans.values()))
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_service_confirmation=PendingServiceConfirmation(
                        plan,
                        success,
                        conversation_user_id(user_input),
                        build_undo_plan(
                            plan,
                            entities,
                            requested_by_user_id=conversation_user_id(user_input),
                        ),
                    ),
                ),
            )
            response.async_set_speech(
                f"Meintest du „{correction.corrected_term}“? "
                f"Soll ich {success.rstrip('.')}?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        dialog = None if is_query else start_semantic_dialog(user_input.text, entities)
        if dialog is not None and dialog.result is not None:
            return await self._async_handle_device_control_result(
                user_input, response, dialog.result
            )
        if dialog is not None and dialog.pending is not None:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_semantic_command=dialog.pending,
                ),
            )
            response.async_set_speech(dialog.question)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        feedback = self._engine.failure_feedback(user_input.text, entities)
        if not is_query and (feedback is None or feedback.startswith(_GENERIC_UNKNOWN_TARGET)):
            # An unknown device word is asked about, never guessed (7.4.1);
            # specific explanations (unknown floor, capabilities) still win.
            area = resolve_conversation_area(self.hass, user_input)
            asked = await self._async_ask_unknown_word(
                user_input, response, entities, area.area_id if area is not None else None
            )
            if asked is not None:
                return asked
        bare_reply = (
            len(user_input.text.split()) <= 3
            and classify_confirmation_reply(user_input.text)
            in {ConfirmationReply.YES, ConfirmationReply.NO}
        )
        response.async_set_error(
            intent.IntentResponseErrorCode.NO_INTENT_MATCH,
            feedback
            or (
                # A bare "Ja"/"Nein" with nothing open (for example after a
                # refusal) is answered honestly (7.3.3).
                "Gerade ist keine Frage offen, auf die sich das beziehen könnte. "
                "Ich habe nichts ausgeführt."
                if bare_reply else NOT_UNDERSTOOD_TEXT
            ),
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_command_plan(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: CommandPlan,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Authorize and execute an already validated multi-command plan."""
        self._runtime_data.shadow.observe(self.entry.options, user_input.text, entities, result)
        if (
            analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
            and any(command.plan is not None for command in result.commands)
        ):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe eine Frage erkannt und führe deshalb keine Aktion aus.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        # Every sub-command was validated together before this point. A HA-side
        # runtime failure remains fail-fast because service calls are not
        # transactional and already executed calls cannot be rolled back safely.
        is_admin = await user_is_admin(self.hass, user_input)
        actor_id = conversation_user_id(user_input)
        for sub_result in result.commands:
            if sub_result.plan is None:
                continue
            policy = evaluate_service_plan(
                sub_result.plan,
                entities,
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                effects=build_plan_effects(self.hass, sub_result.plan),
                origin=result.origin,
                binding_confirmed=result.binding_confirmed,
            )
            if policy.outcome is PolicyOutcome.DENY:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    policy.reason or "Mindestens eine Aktion ist nicht erlaubt.",
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
            if (
                policy.outcome is PolicyOutcome.CONFIRM
                and result.confirmation_text is None
                and result.proposal_text is not None
            ):
                # An implicit need proposed as a whole (implicit_action_level).
                result = replace(result, confirmation_text=result.proposal_text)
            if (
                policy.outcome is PolicyOutcome.CONFIRM
                and result.confirmation_text is None
            ):
                # A previewed group plan is confirmed as a whole below; every
                # other multi-command plan needs one confirmation per action.
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    "Sicherheitskritische Aktionen müssen einzeln bestätigt werden.",
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
        if result.confirmation_text is not None:
            plans = [
                sub_result.plan for sub_result in result.commands
                if sub_result.plan is not None
            ]
            if plans:
                success = " ".join(
                    sub_result.response_text for sub_result in result.commands
                )
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_service_confirmation=PendingServiceConfirmation(
                            plans[0],
                            success,
                            actor_id,
                            None,
                            tuple(plans[1:]),
                            origin=result.origin,
                            binding_confirmed=result.binding_confirmed,
                            binding_offer=(
                                (result.routine_key, result.routine_candidates[0])
                                if result.routine_key is not None
                                and not result.binding_confirmed
                                and len(result.routine_candidates) == 1
                                else None
                            ),
                        ),
                    ),
                )
                response.async_set_speech(result.confirmation_text)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
        self._context_store.clear(user_input.conversation_id)
        response_parts: list[str] = []
        multi_undo_parts: list[UndoPlan] = []
        multi_undo_supported = True
        for sub_result in result.commands:
            if sub_result.plan is None:
                response_parts.append(sub_result.response_text)
                continue
            inverse = build_undo_plan(
                sub_result.plan,
                entities,
                requested_by_user_id=actor_id,
            )
            if inverse is None:
                multi_undo_supported = False
            else:
                multi_undo_parts.append(inverse)
            execution = await async_execute_service_plan(
                self.hass,
                sub_result.plan,
                entities,
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                confirmed=False,
                audit_trail=self._audit_trail,
                audit_actor_id=actor_id,
                effect_monitor=self._runtime_data.effect_monitor,
                origin=result.origin,
            )
            if not execution.executed:
                error = execution.error or "Die Aktion konnte nicht ausgeführt werden."
                _LOGGER.error(
                    "Service call %s.%s on %s failed: %s",
                    sub_result.plan.domain,
                    sub_result.plan.service,
                    sub_result.plan.entity_id,
                    error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ausführen: {error}",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            response_parts.append(sub_result.response_text)
        if multi_undo_supported and multi_undo_parts:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_undo=UndoPlan(
                        tuple(
                            plan
                            for undo in reversed(multi_undo_parts)
                            for plan in undo.plans
                        ),
                        actor_id,
                    ),
                ),
            )
        response.async_set_speech(" ".join(response_parts))
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _store_automation_wizard(
        self, conversation_id: str, state: AutomationWizardState
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_automation_wizard=PendingAutomationWizard(state),
            ),
        )

    async def _async_handle_automation_wizard(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        state: AutomationWizardState,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        text = user_input.text.strip()
        if has_word(text, "abbrechen", "abbruch", "stopp", "stop", "vergiss"):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Der Automationsentwurf wurde verworfen.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if state.stage is AutomationWizardStage.TRIGGER:
            trigger = self._engine.parse_automation_trigger(
                text, entities, self._world_model
            )
            if trigger is None:
                response.async_set_speech(
                    "Den Auslöser habe ich nicht eindeutig verstanden. "
                    "Zum Beispiel: Wenn das Küchenfenster geöffnet wird."
                )
            else:
                state = replace(
                    state,
                    stage=AutomationWizardStage.CONDITION_DECISION,
                    trigger=trigger,
                    source_parts=(*state.source_parts, text),
                )
                self._store_automation_wizard(user_input.conversation_id, state)
                response.async_set_speech(
                    "Soll zusätzlich eine Bedingung gelten? Bitte antworte mit Ja oder Nein."
                )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if state.stage is AutomationWizardStage.CONDITION_DECISION:
            reply = classify_confirmation_reply(text)
            if reply is ConfirmationReply.UNCLEAR and _NO_CONDITION_RE.fullmatch(
                normalize_for_compare(text).strip(" .!")
            ):
                # "Keine Bedingung" / "ohne Bedingung" / "keine" answer the
                # yes/no question with no (F10).
                reply = ConfirmationReply.NO
            if reply is ConfirmationReply.UNCLEAR:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            else:
                next_stage = (
                    AutomationWizardStage.CONDITION
                    if reply is ConfirmationReply.YES
                    else AutomationWizardStage.ACTION
                )
                state = replace(state, stage=next_stage)
                self._store_automation_wizard(user_input.conversation_id, state)
                response.async_set_speech(
                    "Welche Bedingung soll gelten?"
                    if next_stage is AutomationWizardStage.CONDITION
                    else "Was soll dann passieren?"
                )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if state.stage is AutomationWizardStage.CONDITION:
            condition = self._engine.parse_automation_condition(
                text, entities, self._world_model
            )
            if condition is None:
                response.async_set_speech(
                    "Die Bedingung habe ich nicht eindeutig verstanden. "
                    "Zum Beispiel: Nur wenn jemand zuhause ist."
                )
            else:
                state = replace(
                    state,
                    stage=AutomationWizardStage.ACTION,
                    condition=condition,
                    source_parts=(*state.source_parts, text),
                )
                self._store_automation_wizard(user_input.conversation_id, state)
                response.async_set_speech("Was soll dann passieren?")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if state.stage is AutomationWizardStage.ACTION:
            actions = self._engine.parse_automation_actions(
                text, entities, self._world_model
            )
            if not actions:
                response.async_set_speech(
                    "Die Aktion habe ich nicht eindeutig verstanden. "
                    "Bitte nenne eine vollständige Geräteaktion."
                )
            else:
                state = replace(
                    state,
                    stage=AutomationWizardStage.LIFETIME,
                    actions=actions,
                    source_parts=(*state.source_parts, text),
                )
                self._store_automation_wizard(user_input.conversation_id, state)
                response.async_set_speech(
                    "Soll die Automation dauerhaft, einmalig oder nur eine bestimmte Anzahl Mal gelten?"
                )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        lifetime = parse_lifetime(text)
        if lifetime is None:
            response.async_set_speech(
                "Bitte sage dauerhaft, einmalig oder zum Beispiel nur dreimal."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        once, max_runs = lifetime
        assert state.trigger is not None and state.actions
        model = AutomationModel(
            triggers=(state.trigger,),
            conditions=(state.condition,) if state.condition is not None else (),
            actions=state.actions,
            source_text="; ".join((*state.source_parts, text)),
            once=once,
            max_runs=max_runs,
        )
        validation_error = validate_automation(model)
        if validation_error is not None:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(
                "Der vollständige Entwurf ist strukturell nicht sicher ausführbar: "
                + validation_error.name
            )
        else:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_confirmation=PendingAutomationConfirmation(
                        model, conversation_user_id(user_input)
                    ),
                ),
            )
            response.async_set_speech(render_automation_preview(model, entities))
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _store_productivity(
        self,
        conversation_id: str,
        request: ProductivityRequest,
        *,
        awaiting_confirmation: bool = False,
        awaiting_timer_name: bool = False,
        timer_choices: tuple[NativeTimerInfo, ...] = (),
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_productivity_command=PendingProductivityCommand(
                    request=request,
                    awaiting_confirmation=awaiting_confirmation,
                    awaiting_timer_name=awaiting_timer_name,
                    timer_choices=timer_choices,
                ),
            ),
        )

    async def _async_handle_native_timer_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: TimerRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Name, select and confirm native Assist timers before acting."""
        native_timer = self._runtime_data.native_timer
        assert native_timer is not None
        conversation_id = user_input.conversation_id

        def done(speech: str, *, query: bool = False) -> conversation.ConversationResult:
            response.async_set_speech(speech)
            if query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        try:
            if request.operation is TimerOperation.START:
                # Fail before asking for a name when nothing could be heard.
                await native_timer.async_ensure_audible(user_input)
                if not request.name:
                    self._store_productivity(
                        conversation_id, request, awaiting_timer_name=True
                    )
                    return done("Wie soll der Timer heißen?")
                timers = await native_timer.async_list_timers(user_input)
                if match_timer_name(request.name, timers):
                    self._store_productivity(
                        conversation_id, request, awaiting_timer_name=True
                    )
                    return done(
                        f"Es läuft schon ein Timer {request.name}. "
                        "Wie soll der neue Timer heißen?"
                    )
                return await self._async_execute_productivity(
                    user_input, response, request, entities
                )
            timers = await native_timer.async_list_timers(user_input)
        except Exception as err:  # noqa: BLE001 - HA intent errors are heterogeneous
            _LOGGER.error("Timer command failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ausführen: {err}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        note = native_timer.consume_restart_note() if request.operation in {
            TimerOperation.LIST, TimerOperation.STATUS,
        } else None
        prefix = f"{note} " if note else ""
        if not timers:
            return done(f"{prefix}Es läuft kein Timer.", query=True)
        if request.operation is TimerOperation.LIST:
            return done(prefix + describe_timers(timers), query=True)
        if request.operation is TimerOperation.CANCEL_ALL:
            self._store_productivity(conversation_id, request, awaiting_confirmation=True)
            if len(timers) == 1:
                return done(f"Soll ich wirklich den Timer {timers[0].label} löschen?")
            return done(f"Soll ich wirklich alle {len(timers)} Timer löschen?")

        candidates = timers
        if request.name:
            candidates = match_timer_name(request.name, timers)
            if not candidates:
                return done(
                    f"Ich finde keinen Timer {request.name}. "
                    + describe_timers(timers)
                )
        if len(candidates) == 1:
            return await self._async_execute_native_timer(
                user_input, response, request, candidates[0], entities
            )
        if request.operation is TimerOperation.STATUS and not request.name:
            return done(describe_timers(timers), query=True)
        self._store_productivity(conversation_id, request, timer_choices=candidates)
        return done(f"Welchen Timer meinst du: {join_timer_labels(candidates)}?")

    async def _async_execute_native_timer(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: TimerRequest,
        target: NativeTimerInfo,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        self._context_store.clear(user_input.conversation_id)
        if request.operation is TimerOperation.STATUS:
            response.async_set_speech(describe_timers((target,)).split(". ", 1)[-1])
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self._async_execute_productivity(
            user_input, response, request, entities, timer_target=target
        )

    async def _async_handle_pending_timer_reply(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingProductivityCommand,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        request = pending.request
        assert isinstance(request, TimerRequest)
        conversation_id = user_input.conversation_id

        def ask(speech: str) -> conversation.ConversationResult:
            response.async_set_speech(speech)
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        if classify_confirmation_reply(user_input.text) is ConfirmationReply.NO or re.fullmatch(
            r"\s*(?:abbrechen|abbruch|vergiss\s+es|stopp?)[.!]?\s*",
            user_input.text,
            re.IGNORECASE,
        ):
            self._context_store.clear(conversation_id)
            return ask("Abgebrochen. Ich führe nichts aus.")

        # A new timer or list command is not an answer to the open question
        # ("Pausiere den Küchentimer" must never become a timer name).
        new_request = parse_productivity_request(user_input.text, entities)
        if new_request is not None:
            self._context_store.clear(conversation_id)
            return await self._async_handle_productivity_request(
                user_input, response, new_request, entities
            )

        if pending.awaiting_timer_name:
            name = timer_name_reply(user_input.text)
            if name is None:
                return ask("Bitte nenne einen kurzen Namen für den Timer, zum Beispiel Nudeln.")
            if name:
                native_timer = self._runtime_data.native_timer
                assert native_timer is not None
                timers = await native_timer.async_list_timers(user_input)
                if match_timer_name(name, timers):
                    return ask(
                        f"Es läuft schon ein Timer {name}. Wie soll der neue Timer heißen?"
                    )
            self._context_store.clear(conversation_id)
            return await self._async_execute_productivity(
                user_input, response, replace(request, name=name or None), entities
            )

        choices = tuple(
            item for item in pending.timer_choices if isinstance(item, NativeTimerInfo)
        )
        selected: NativeTimerInfo | None = None
        position = timer_choice_ordinal(user_input.text)
        if position is not None and choices:
            index = len(choices) - 1 if position == -1 else position - 1
            if 0 <= index < len(choices):
                selected = choices[index]
        if selected is None:
            matched = match_timer_name(user_input.text.strip(" .!?"), choices)
            if len(matched) == 1:
                selected = matched[0]
        if selected is None:
            return ask(f"Welchen Timer meinst du: {join_timer_labels(choices)}?")
        return await self._async_execute_native_timer(
            user_input, response, request, selected, entities
        )

    async def _async_handle_pending_productivity(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingProductivityCommand,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        if pending.awaiting_timer_name or pending.timer_choices:
            return await self._async_handle_pending_timer_reply(
                user_input, response, pending, entities
            )
        if pending.awaiting_confirmation:
            reply = classify_confirmation_reply(user_input.text)
            if reply is ConfirmationReply.UNCLEAR:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            self._context_store.clear(user_input.conversation_id)
            if reply is ConfirmationReply.NO:
                response.async_set_speech(
                    "Abgebrochen. Die Timer laufen weiter."
                    if isinstance(pending.request, TimerRequest)
                    else "Abgebrochen. Die Liste wurde nicht verändert."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            return await self._async_execute_productivity(
                user_input, response, pending.request, entities
            )

        candidate_reply = resolve_candidate_reply(
            user_input.text, pending.request.candidates, entities
        )
        if candidate_reply.kind is CandidateReplyKind.CANCELLED:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Ich führe nichts aus.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        selected = select_productivity_candidate(pending.request, user_input.text)
        if selected is None:
            response.async_set_speech(
                render_candidate_question(pending.request.candidates)
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if not any(entity.entity_id == selected.entity_id for entity in entities):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(
                "Das ausgewählte Ziel ist nicht mehr verfügbar. "
                "Ich führe nichts aus."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self._async_handle_productivity_request(
            user_input, response, selected, entities
        )

    async def _async_handle_productivity_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: ProductivityRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        last_list = self._last_todo_lists.get(user_input.conversation_id)
        if (
            isinstance(request, TodoRequest)
            and request.entity_id is None
            and last_list is not None
            and any(item.entity_id == last_list for item in request.candidates)
        ):
            # "Markiere Milch und Brot als erledigt" right after using the
            # Einkaufsliste continues on that list (F27).
            request = replace(request, entity_id=last_list, candidates=())
        if isinstance(request, TodoRequest) and request.entity_id is not None:
            self._last_todo_lists[user_input.conversation_id] = request.entity_id
            while len(self._last_todo_lists) > 64:
                self._last_todo_lists.pop(next(iter(self._last_todo_lists)))
        if request.entity_id is None:
            if request.candidates:
                self._store_productivity(user_input.conversation_id, request)
                response.async_set_speech(
                    render_candidate_question(request.candidates)
                )
            elif isinstance(request, TimerRequest) and self._runtime_data.native_timer is not None:
                self._context_store.clear(user_input.conversation_id)
                return await self._async_handle_native_timer_request(
                    user_input, response, request, entities
                )
            else:
                noun = "keine für HomeIntent freigegebene Liste" if isinstance(request, TodoRequest) else "keinen für HomeIntent freigegebenen Timer"
                response.async_set_speech(f"Ich finde {noun}.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if (
            isinstance(request, TodoRequest)
            and request.operation is TodoOperation.CLEAR_COMPLETED
        ):
            self._store_productivity(
                user_input.conversation_id, request, awaiting_confirmation=True
            )
            response.async_set_speech(
                "Soll ich wirklich alle erledigten Einträge aus der Liste löschen?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        self._context_store.clear(user_input.conversation_id)
        return await self._async_execute_productivity(
            user_input, response, request, entities
        )

    async def _async_execute_productivity(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: ProductivityRequest,
        entities: list[EntitySnapshot],
        *,
        timer_target: NativeTimerInfo | None = None,
    ) -> conversation.ConversationResult:
        try:
            if isinstance(request, TodoRequest):
                speech = await self._async_execute_todo(request)
            else:
                speech = await self._async_execute_timer(
                    request, entities, user_input, timer_target=timer_target
                )
        except Exception as err:  # noqa: BLE001 - HA service errors are heterogeneous
            _LOGGER.error("Productivity command failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ausführen: {err}",
            )
        else:
            response.async_set_speech(speech)
            if isinstance(request, TodoRequest) and request.operation is not TodoOperation.LIST:
                if request.entity_id is None:
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                todo_service = {
                    TodoOperation.ADD: "add_item",
                    TodoOperation.COMPLETE: "update_item",
                    TodoOperation.REMOVE: "remove_item",
                    TodoOperation.CLEAR_COMPLETED: "remove_completed_items",
                    TodoOperation.MOVE: "move_items",
                }[request.operation]
                self._record_execution(
                    user_input,
                    ServiceCallPlan("todo", todo_service, request.entity_id, {}),
                )
            elif (
                isinstance(request, TimerRequest)
                and request.entity_id is not None
                and request.operation is not TimerOperation.STATUS
            ):
                timer_service = {
                    TimerOperation.START: "start",
                    TimerOperation.CHANGE: "change",
                    TimerOperation.PAUSE: "pause",
                    TimerOperation.RESUME: "start",
                    TimerOperation.CANCEL: "cancel",
                    TimerOperation.FINISH: "finish",
                }[request.operation]
                self._record_execution(
                    user_input,
                    ServiceCallPlan("timer", timer_service, request.entity_id, {}),
                )
            if (
                isinstance(request, TimerRequest)
                and request.operation is TimerOperation.STATUS
            ) or (
                isinstance(request, TodoRequest)
                and request.operation is TodoOperation.LIST
            ):
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_get_todo_items(self, entity_id: str) -> list[dict]:
        result = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"status": ["needs_action", "completed"]},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
            context=call_context(),
        )
        container = result.get(entity_id, result) if isinstance(result, dict) else {}
        items = container.get("items", []) if isinstance(container, dict) else []
        return [item for item in items if isinstance(item, dict)]

    async def _async_execute_todo(self, request: TodoRequest) -> str:
        assert request.entity_id is not None
        if request.operation is TodoOperation.LIST:
            items = await self._async_get_todo_items(request.entity_id)
            open_items = [
                str(item.get("summary") or item.get("item") or "").strip()
                for item in items
                if item.get("status", "needs_action") == "needs_action"
            ]
            open_items = [item for item in open_items if item]
            return (
                "Auf der Liste steht nichts Offenes."
                if not open_items
                else "Auf der Liste stehen: " + ", ".join(open_items) + "."
            )

        if request.operation is TodoOperation.CLEAR_COMPLETED:
            items = await self._async_get_todo_items(request.entity_id)
            completed = [
                item.get("uid") or item.get("summary") or item.get("item")
                for item in items
                if item.get("status") == "completed"
            ]
            completed = [item for item in completed if item]
            if completed:
                await self.hass.services.async_call(
                    "todo", "remove_completed_items", {},
                    target={"entity_id": request.entity_id}, blocking=True,
                    context=call_context(),
                )
            return (
                "Es gab keine erledigten Einträge."
                if not completed
                else counted_passive(
                    len(completed), "erledigter Eintrag", "erledigte Einträge", "gelöscht"
                )
            )

        def matching_items(
            available: list[dict], requested: tuple[str, ...]
        ) -> list[dict]:
            """Resolve spoken summaries to stable todo UIDs without guessing."""
            selected: list[dict] = []
            for spoken in requested:
                key = normalize_for_compare(spoken)
                matches = []
                for candidate in available:
                    summary = str(
                        candidate.get("summary") or candidate.get("item") or ""
                    ).strip()
                    # Home Assistant has no portable priority field. HomeIntent
                    # stores it as a visible prefix, but users need not repeat it.
                    plain_summary = re.sub(
                        r"^\[(?:hoch|mittel|niedrig)\]\s*", "", summary,
                        flags=re.IGNORECASE,
                    )
                    if normalize_for_compare(plain_summary) == key:
                        matches.append(candidate)
                if not matches:
                    raise ValueError(f"Eintrag „{spoken}“ wurde nicht gefunden")
                if len(matches) > 1:
                    raise ValueError(
                        f"Eintrag „{spoken}“ ist mehrfach vorhanden. "
                        "Bitte mache ihn zuerst eindeutig"
                    )
                selected.append(matches[0])
            return selected

        if request.operation in {TodoOperation.COMPLETE, TodoOperation.REMOVE}:
            available = await self._async_get_todo_items(request.entity_id)
            selected = matching_items(available, request.items)
            service = (
                "update_item"
                if request.operation is TodoOperation.COMPLETE
                else "remove_item"
            )
            for item in selected:
                uid = item.get("uid")
                if not uid:
                    raise ValueError("Die Liste liefert keine stabile Eintrags-ID")
                data: dict[str, object] = {"item": uid}
                if request.operation is TodoOperation.COMPLETE:
                    data["status"] = "completed"
                await self.hass.services.async_call(
                    "todo", service, data,
                    target={"entity_id": request.entity_id}, blocking=True,
                    context=call_context(),
                )
            count = len(selected)
            if request.operation is TodoOperation.COMPLETE:
                return counted_passive(count, "Eintrag", "Einträge", "als erledigt markiert")
            return counted_passive(count, "Eintrag", "Einträge", "aus der Liste entfernt")

        if request.operation is TodoOperation.MOVE:
            if request.destination_entity_id is None:
                raise ValueError("Die Zielliste fehlt")
            available = await self._async_get_todo_items(request.entity_id)
            selected = matching_items(available, request.items)
            # Resolve every source item before the first write. A partial move
            # can therefore only result from an external HA service failure.
            added: list[dict] = []
            try:
                for item in selected:
                    summary = str(item.get("summary") or item.get("item") or "")
                    data: dict[str, object] = {"item": summary}
                    due = item.get("due") or item.get("due_date") or item.get("due_datetime")
                    if due:
                        data["due_datetime" if "T" in str(due) else "due_date"] = due
                    if item.get("description"):
                        data["description"] = item["description"]
                    await self.hass.services.async_call(
                        "todo", "add_item", data,
                        target={"entity_id": request.destination_entity_id}, blocking=True,
                        context=call_context(),
                    )
                    added.append(item)
                for item in selected:
                    uid = item.get("uid")
                    if not uid:
                        raise ValueError("Die Liste liefert keine stabile Eintrags-ID")
                    await self.hass.services.async_call(
                        "todo", "remove_item", {"item": uid},
                        target={"entity_id": request.entity_id}, blocking=True,
                        context=call_context(),
                    )
            except Exception:
                _LOGGER.warning(
                    "Todo move failed after %d destination writes; source items "
                    "were retained where possible", len(added)
                )
                raise
            count = len(selected)
            return f"{count} Eintrag" + (" wurde" if count == 1 else "e wurden") + " verschoben."

        assert request.operation is TodoOperation.ADD
        for item in request.items:
            stored_item = (
                f"[{request.priority.capitalize()}] {item}"
                if request.priority else item
            )
            data: dict[str, object] = {"item": stored_item}
            if request.due_date:
                data["due_date"] = request.due_date
            if request.description:
                data["description"] = request.description
            await self.hass.services.async_call(
                "todo", "add_item", data,
                target={"entity_id": request.entity_id}, blocking=True,
                context=call_context(),
            )
        count = len(request.items)
        return (
            f"{request.items[0]} wurde zur Liste hinzugefügt."
            if count == 1
            else f"{count} Einträge wurden zur Liste hinzugefügt: "
            + ", ".join(request.items) + "."
        )

    async def _async_execute_timer(
        self,
        request: TimerRequest,
        entities: list[EntitySnapshot],
        user_input: conversation.ConversationInput,
        *,
        timer_target: NativeTimerInfo | None = None,
    ) -> str:
        if request.entity_id is None:
            native_timer = self._runtime_data.native_timer
            if native_timer is None:
                raise ValueError("Home Assistants native Timerverwaltung ist nicht verfügbar.")
            if request.operation is TimerOperation.CANCEL_ALL:
                canceled = await native_timer.async_cancel_all(user_input)
                if canceled == 1:
                    return "Der Timer wurde gelöscht."
                return f"Alle {canceled} Timer wurden gelöscht."
            return await native_timer.async_execute(
                request, user_input, target=timer_target
            )
        assert request.entity_id is not None
        entity = next((item for item in entities if item.entity_id == request.entity_id), None)
        if request.operation is TimerOperation.STATUS:
            if entity is None:
                return "Der Timer ist nicht mehr verfügbar."
            if entity.state == "idle":
                return "Der Timer ist nicht aktiv."
            seconds_left = _helper_timer_seconds_left(entity)
            state = "pausiert" if entity.state == "paused" else "aktiv"
            if seconds_left is None:
                return f"Der Timer ist {state}."
            return f"Der Timer ist {state}. Verbleibende Zeit: {format_duration(seconds_left)}."

        service = {
            TimerOperation.START: "start",
            TimerOperation.CHANGE: "change",
            TimerOperation.PAUSE: "pause",
            TimerOperation.RESUME: "start",
            TimerOperation.CANCEL: "cancel",
            TimerOperation.FINISH: "finish",
        }[request.operation]
        data: dict[str, str | int] = {}
        if request.operation is TimerOperation.START:
            assert request.duration_seconds is not None
            data["duration"] = self._timer_duration_value(request.duration_seconds)
        elif request.operation is TimerOperation.CHANGE:
            assert request.change_seconds is not None
            # timer.change explicitly accepts signed seconds; using the
            # integer form avoids ambiguity around a negative HH:MM string.
            data["duration"] = request.change_seconds
        await self.hass.services.async_call(
            "timer", service, data,
            target={"entity_id": request.entity_id}, blocking=True,
            context=call_context(),
        )
        if request.operation is TimerOperation.START:
            return f"Timer für {format_duration(request.duration_seconds or 0)} gestartet."
        if request.operation is TimerOperation.CHANGE:
            verb = "verlängert" if (request.change_seconds or 0) > 0 else "verkürzt"
            return f"Timer um {format_duration(request.change_seconds or 0)} {verb}."
        return {
            TimerOperation.PAUSE: "Timer pausiert.",
            TimerOperation.RESUME: "Timer fortgesetzt.",
            TimerOperation.CANCEL: "Timer abgebrochen.",
            TimerOperation.FINISH: "Timer beendet.",
        }[request.operation]

    @staticmethod
    def _timer_duration_value(seconds: int) -> str:
        sign = "-" if seconds < 0 else ""
        hours, remainder = divmod(abs(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"

    async def _async_handle_automation_confirmation_reply(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        confirmation: PendingAutomationConfirmation,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """V5 Teil 7/10 (V5.23/V5.25/V5.26): resolves this turn's text
        against the closed yes/no vocabulary (see
        ``nlu/automation_confirmation.py``) instead of parsing it as a fresh
        sentence - a reply like "Ja" isn't itself a command.

        ``UNCLEAR`` keeps the same pending confirmation in place (re-asks,
        never guesses) rather than clearing it - same "caller decides how to
        re-ask" split ``resolve_clarification()``'s own ``None`` case
        already uses. ``NO`` and any generation/persistence failure both
        clear the pending state and persist nothing; only a clean ``YES`` ->
        ``generate_ha_automation_config()`` -> ``AutomationExecutor`` path
        ever reaches Home Assistant.
        """
        current_user_id = conversation_user_id(user_input)
        if (
            confirmation.requested_by_user_id is not None
            and confirmation.requested_by_user_id != current_user_id
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Diese Bestätigung gehört zu einem anderen Benutzer.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        reply = classify_confirmation_reply(user_input.text)

        if reply is ConfirmationReply.UNCLEAR:
            response.async_set_speech(AUTOMATION_CONFIRMATION_UNCLEAR_TEXT)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        self._context_store.clear(user_input.conversation_id)

        if reply is ConfirmationReply.NO:
            response.async_set_speech(AUTOMATION_CANCELLED_TEXT)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if (
            not bool(
                self.entry.options.get(CONF_ALLOW_NON_ADMIN_AUTOMATIONS, True)
            )
            and not await user_is_admin(self.hass, user_input)
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Das Erstellen von Automationen ist nur für Administratoren erlaubt.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        controlled_ids = resolve_automation_action_entity_ids(
            confirmation.model, entities
        )
        composite_ids = sorted(
            entity.entity_id for entity in entities
            if entity.entity_id in controlled_ids
            and is_composite_entity(entity.entity_id, entity.attributes)
        )
        target_policy_error = validate_automation_action_targets(
            controlled_ids,
            self.entry.options,
            is_admin=await user_is_admin(self.hass, user_input),
            user_id=conversation_user_id(user_input),
            effects=(
                build_plan_effects(
                    self.hass, ServiceCallPlan("homeassistant", "turn_on", composite_ids)
                )
                if composite_ids else None
            ),
            exposed_ids=frozenset(entity.entity_id for entity in entities),
        )
        if target_policy_error is not None:
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                target_policy_error,
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        materialized, failure = self._materialize_notification_recipients(
            confirmation.model, user_input, entities
        )
        if failure is not None:
            response.async_set_speech(failure)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        try:
            model = resolve_pending_schedule(materialized, dt_util.now())
        except ValueError as err:
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Der Zeitpunkt kann nicht geplant werden: {err}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        # Wave 12 ("Einmalige Automation"): a self-deleting automation's own
        # action list needs to reference its own future id (see
        # ha_automation_generator.py's generate_ha_automation_config()
        # docstring for why) - pre-generated here, before persistence, and
        # threaded into both the generator and the executor so they agree.
        # None/unused for every ordinary (non-once) automation.
        # Every generated automation receives its stable id before rendering.
        # Targetless notification actions embed this id as their proactive
        # rule identity; one-shot lifecycle actions use the same id.
        once_automation_id = uuid.uuid4().hex

        generation_result = generate_ha_automation_config(
            model, entities, automation_id=once_automation_id
        )
        if generation_result.error is not None:
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                _GENERATION_ERROR_SPOKEN_DE[generation_result.error],
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        assert generation_result.config is not None

        if self._automation_executor is None:
            self._automation_executor = AutomationExecutor(self.hass)
        try:
            created_id = await self._automation_executor.async_create_automation(
                generation_result.config,
                automation_id=once_automation_id,
                scheduled_for=model.scheduled_for,
                once=model.once,
                max_runs=model.max_runs,
            )
        except Exception as err:  # noqa: BLE001 - a YAML write + service call can fail in ways beyond HomeAssistantError; must not propagate as "Unexpected error during intent recognition"
            _LOGGER.error("Automation creation failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Erstellen der Automation: {err}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        self._record_execution(
            user_input,
            ServiceCallPlan("homeintent", "create_automation", created_id, {}),
        )
        response.async_set_speech(AUTOMATION_CREATED_TEXT)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_device_control_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        device_control: DeviceControlResult,
    ) -> conversation.ConversationResult:
        """Apply the common safety, execution and context policy once."""
        if (
            device_control.plan is not None
            and analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
        ):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe eine Frage erkannt und führe deshalb keine Aktion aus.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if device_control.plan is None:
            self._context_store.clear(user_input.conversation_id)
            if device_control.is_query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
            response.async_set_speech(device_control.response_text)
        else:
            resolved_entities = list(device_control.resolved_entities)
            actor_id = conversation_user_id(user_input)
            is_admin = await user_is_admin(self.hass, user_input)
            policy = evaluate_service_plan(
                device_control.plan,
                build_entity_snapshots(self.hass, self.entry),
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                effects=build_plan_effects(self.hass, device_control.plan),
            )
            if policy.outcome is PolicyOutcome.DENY:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    policy.reason or "Diese Aktion ist nicht erlaubt.",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            needs_confirmation = (
                device_control.requires_confirmation
                or policy.outcome is PolicyOutcome.CONFIRM
            )
            if needs_confirmation:
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_service_confirmation=PendingServiceConfirmation(
                            device_control.plan,
                            device_control.response_text,
                            actor_id,
                            build_undo_plan(
                                device_control.plan,
                                resolved_entities,
                                requested_by_user_id=actor_id,
                            ),
                        ),
                    ),
                )
                response.async_set_speech(
                    _confirmation_question(device_control.response_text, policy.note)
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            execution = await async_execute_service_plan(
                self.hass,
                device_control.plan,
                build_entity_snapshots(self.hass, self.entry),
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                confirmed=False,
                audit_trail=self._audit_trail,
                audit_actor_id=actor_id,
                effect_monitor=self._runtime_data.effect_monitor,
            )
            if not execution.executed:
                self._context_store.clear(user_input.conversation_id)
                error = execution.error or "Die Aktion konnte nicht ausgeführt werden."
                _LOGGER.error(
                    "Device-control service call %s.%s on %s failed: %s",
                    device_control.plan.domain,
                    device_control.plan.service,
                    device_control.plan.entity_id,
                    error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ausführen: {error}",
                )
            else:
                response.async_set_speech(device_control.response_text)
                if resolved_entities:
                    controlled = resolved_entities[0]
                    self._context_store.set(
                        user_input.conversation_id,
                        ConversationContext(
                            last_command=None,
                            last_entities=tuple(resolved_entities),
                            last_area=None,
                            pending_clarification=None,
                            focus=DialogFocus(
                                property=None,
                                scope_kind=("entity" if len(resolved_entities) == 1 else None),
                                scope_id=(controlled.entity_id if len(resolved_entities) == 1 else None),
                                candidate_entity_ids=tuple(
                                    entity.entity_id for entity in resolved_entities
                                ),
                                source_intent="DeviceControl:" + device_control.plan.service,
                            ),
                            pending_undo=build_undo_plan(
                                device_control.plan,
                                resolved_entities,
                                requested_by_user_id=actor_id,
                            ),
                        ),
                    )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def _record_execution(self, user_input, plan) -> None:
        context = getattr(user_input, "context", None)
        self._audit_trail.record(
            dt_util.now(), getattr(context, "user_id", None), plan
        )

    def _store_calendar_event(
        self,
        conversation_id: str,
        draft: CalendarEventDraft,
        awaiting_confirmation: bool,
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_calendar_event=PendingCalendarEvent(
                    draft=draft, awaiting_confirmation=awaiting_confirmation
                ),
            ),
        )

    async def _async_handle_calendar_event_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingCalendarEvent,
        calendars: tuple[EntitySnapshot, ...],
    ) -> conversation.ConversationResult:
        """Complete, revise, confirm, and finally create one calendar event."""
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.NO:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Der Termin wurde nicht eingetragen.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if not pending.awaiting_confirmation and reply is ConfirmationReply.YES:
            response.async_set_speech(
                calendar_event_question(pending.draft, calendars)
                or "Bitte ergänze die noch fehlende Terminangabe."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if pending.awaiting_confirmation and reply is ConfirmationReply.YES:
            try:
                call = build_calendar_event_service_call(
                    pending.draft, calendars, dt_util.now()
                )
            except ValueError as err:
                response.async_set_speech(
                    f"Der Termin kann so nicht eingetragen werden: {err}. Bitte korrigiere die Angabe."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

            self._context_store.clear(user_input.conversation_id)
            try:
                await self.hass.services.async_call(
                    "calendar",
                    "create_event",
                    call.data,
                    target={"entity_id": call.entity_id},
                    blocking=True,
                    context=call_context(),
                )
            except Exception as err:  # noqa: BLE001 - HA calendar integrations raise heterogeneous errors
                _LOGGER.error("Calendar event creation failed: %s", err)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Eintragen des Termins: {err}",
                )
            else:
                self._record_execution(
                    user_input,
                    ServiceCallPlan("calendar", "create_event", call.entity_id, {}),
                )
                response.async_set_speech("Der Termin wurde eingetragen.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        update = update_calendar_event_draft(
            user_input.text, pending.draft, calendars, dt_util.now()
        )
        if update.error_text is not None:
            response.async_set_speech(update.error_text)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if pending.awaiting_confirmation and not update.changed:
            response.async_set_speech(
                "Bitte antworte mit Ja oder Nein. Du kannst Datum, Uhrzeit, Dauer, Titel oder Kalender auch noch korrigieren."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        question = calendar_event_question(update.draft, calendars)
        awaiting_confirmation = question is None
        self._store_calendar_event(
            user_input.conversation_id, update.draft, awaiting_confirmation
        )
        response.async_set_speech(
            render_calendar_event_preview(update.draft, calendars)
            if awaiting_confirmation
            else question
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_automation_management(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: AutomationManagementRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Execute the bounded query/reschedule/cleanup management language."""
        self._context_store.clear(user_input.conversation_id)
        if self._automation_executor is None:
            self._automation_executor = AutomationExecutor(self.hass)
        now = dt_util.now()
        if request.kind in {
            AutomationManagementKind.CLEAN_EXPIRED,
            AutomationManagementKind.ROLLBACK,
        }:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_automation_management=PendingAutomationManagement(request),
                ),
            )
            response.async_set_speech(
                "Soll ich die letzte HomeIntent-Automationsänderung wirklich rückgängig machen?"
                if request.kind is AutomationManagementKind.ROLLBACK
                else "Soll ich alle abgelaufenen HomeIntent-Aufträge wirklich löschen?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        automations = await self._automation_executor.async_list_automations()
        selection = select_automation_management(request, entities, automations, now)
        if request.kind in READ_ONLY_MANAGEMENT_KINDS:
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
        if selection.error_text is not None:
            response.async_set_speech(selection.error_text)
        elif request.kind is AutomationManagementKind.LIST_HOMEINTENT:
            labels = [_automation_label(item) for item in selection.automations]
            response.async_set_speech(
                "Es sind keine HomeIntent-Automationen vorhanden."
                if not labels
                else "HomeIntent-Automationen: " + ", ".join(labels) + "."
            )
        elif request.kind in {
            AutomationManagementKind.COUNT_ACTIVE,
            AutomationManagementKind.COUNT_DISABLED,
        }:
            state = (
                "aktiv"
                if request.kind is AutomationManagementKind.COUNT_ACTIVE
                else "deaktiviert"
            )
            response.async_set_speech(
                f"{len(selection.automations)} HomeIntent-Automationen sind {state}."
            )
        elif request.kind in {
            AutomationManagementKind.EXPLAIN_TRIGGER,
            AutomationManagementKind.CONTROLS_ENTITY,
            AutomationManagementKind.DETAIL,
            AutomationManagementKind.DIAGNOSE,
            AutomationManagementKind.SIMULATE,
        }:
            if not selection.automations:
                response.async_set_speech(
                    "Ich finde keine passende Automation."
                    if request.kind in {
                        AutomationManagementKind.EXPLAIN_TRIGGER,
                        AutomationManagementKind.CONTROLS_ENTITY,
                    }
                    else "Ich finde keine passende HomeIntent-Automation."
                )
            else:
                details = [
                    (item.source_text or item.alias).rstrip(" .")
                    for item in selection.automations
                ]
                prefix = (
                    "Auf diesen Auslöser reagieren: "
                    if request.kind is AutomationManagementKind.EXPLAIN_TRIGGER
                    else "Dieses Gerät wird gesteuert durch: "
                )
                if request.kind is AutomationManagementKind.DIAGNOSE:
                    item = selection.automations[0]
                    live = self._automation_executor.automation_runtime_info(
                        item.automation_id
                    )
                    enabled = item.enabled and (
                        live is None or live.get("state") != "off"
                    )
                    last = live.get("last_triggered") if live else None
                    response.async_set_speech(
                        f"{_automation_label(item)} ist "
                        f"{'aktiv' if enabled else 'deaktiviert'}, hat "
                        f"{len(item.triggers)} Auslöser und {len(item.conditions)} Bedingungen. "
                        + (
                            f"Zuletzt ausgelöst: {last}. " if last else
                            "Home Assistant meldet keine letzte Auslösung. "
                        )
                        + "Ohne gespeicherte Home-Assistant-Ablaufverfolgung kann ich die "
                        "genaue Ursache nicht beweisen; häufig sind Auslöser nicht eingetreten "
                        "oder eine Bedingung war zu diesem Zeitpunkt falsch."
                    )
                elif request.kind is AutomationManagementKind.SIMULATE:
                    item = selection.automations[0]
                    response.async_set_speech(
                        f"Simulation für {_automation_label(item)}: "
                        + render_automation_simulation(item, entities)
                    )
                elif request.kind is AutomationManagementKind.DETAIL:
                    item = selection.automations[0]
                    response.async_set_speech(
                        f"{_automation_label(item)}: {len(item.triggers)} Auslöser, "
                        f"{len(item.conditions)} Bedingungen und {len(item.actions)} Aktionen. "
                        f"Quelle: {item.source_text or item.alias}."
                    )
                else:
                    response.async_set_speech(prefix + "; ".join(details) + ".")
        elif request.kind in (
            AutomationManagementKind.LIST_SCHEDULED,
            AutomationManagementKind.WHEN,
        ):
            if not selection.automations:
                response.async_set_speech("Es sind keine passenden einmaligen Aufträge geplant.")
            else:
                details = [
                    f"{_automation_label(item)} – {format_scheduled_time(item.scheduled_for)}"
                    for item in selection.automations
                    if item.scheduled_for is not None
                ]
                response.async_set_speech("Geplant sind: " + "; ".join(details) + ".")
        elif request.kind is AutomationManagementKind.RESCHEDULE:
            if not selection.automations:
                response.async_set_speech("Ich habe keinen passenden geplanten Auftrag gefunden.")
            elif len(selection.automations) > 1:
                labels = ", ".join(_automation_label(item) for item in selection.automations)
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None, last_entities=(), last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request=request, candidates=selection.automations
                        ),
                    ),
                )
                response.async_set_speech(
                    "Mehrere Aufträge passen. Bitte nenne das Gerät oder wähle "
                    "einen Auftrag per Name oder Nummer: "
                    + labels
                    + "."
                )
            else:
                automation = next(iter(selection.automations))
                if automation.scheduled_for is None:
                    response.async_set_speech(
                        "Der passende Auftrag hat keine sichere geplante Zeit."
                    )
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                target = datetime.fromisoformat(automation.scheduled_for)
                if request.hour is None:
                    response.async_set_speech("Die neue Uhrzeit ist unvollständig.")
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                target = target.replace(hour=request.hour, minute=request.minute, second=0)
                comparable_now = now
                if target.tzinfo is None and now.tzinfo is not None:
                    comparable_now = now.replace(tzinfo=None)
                elif target.tzinfo is not None and now.tzinfo is None:
                    comparable_now = now.replace(tzinfo=target.tzinfo)
                if target <= comparable_now:
                    response.async_set_speech(
                        "Die neue Uhrzeit liegt am geplanten Tag bereits in der Vergangenheit."
                    )
                else:
                    self._context_store.set(
                        user_input.conversation_id,
                        ConversationContext(
                            last_command=None,
                            last_entities=(),
                            last_area=None,
                            pending_clarification=None,
                            pending_automation_management=PendingAutomationManagement(
                                request, automation, target
                            ),
                        ),
                    )
                    response.async_set_speech(
                        f"Soll ich {_automation_label(automation)} wirklich auf "
                        f"{format_scheduled_time(target.isoformat())} verschieben?"
                    )
        elif request.kind is AutomationManagementKind.SET_MAX_RUNS:
            if not selection.automations:
                response.async_set_speech(
                    "Ich habe keine passende dauerhafte HomeIntent-Automation gefunden."
                )
            elif len(selection.automations) > 1:
                labels = ", ".join(
                    _automation_label(item) for item in selection.automations
                )
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None, last_entities=(), last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request=request, candidates=selection.automations
                        ),
                    ),
                )
                response.async_set_speech(
                    "Mehrere Automationen passen. Welche meinst du? "
                    + labels
                    + "."
                )
            else:
                automation = next(iter(selection.automations))
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request, automation
                        ),
                    ),
                )
                response.async_set_speech(
                    f"Soll {_automation_label(automation)} wirklich auf "
                    f"{request.max_runs} Ausführungen begrenzt werden?"
                )
        elif request.kind is AutomationManagementKind.DUPLICATE:
            if not selection.automations:
                response.async_set_speech("Ich finde keine passende HomeIntent-Automation.")
            elif len(selection.automations) > 1:
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None, last_entities=(), last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request=request, candidates=selection.automations
                        ),
                    ),
                )
                response.async_set_speech(
                    "Mehrere Automationen passen. Welche soll kopiert werden? "
                    + ", ".join(_automation_label(item) for item in selection.automations)
                    + "."
                )
            else:
                automation = next(iter(selection.automations))
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None, last_entities=(), last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request=request, automation=automation
                        ),
                    ),
                )
                response.async_set_speech(
                    f"Soll ich {_automation_label(automation)} wirklich duplizieren?"
                )
        elif request.kind is AutomationManagementKind.PAUSE_UNTIL:
            if not selection.automations:
                response.async_set_speech("Ich finde keine passende HomeIntent-Automation.")
            elif len(selection.automations) > 1:
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None, last_entities=(), last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request=request, candidates=selection.automations
                        ),
                    ),
                )
                response.async_set_speech(
                    "Mehrere Automationen passen. Welche soll pausiert werden? "
                    + ", ".join(_automation_label(item) for item in selection.automations)
                    + "."
                )
            else:
                if request.hour is None:
                    response.async_set_speech("Die Uhrzeit ist nicht vollständig.")
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                target = (now + timedelta(days=request.day_offset)).replace(
                    hour=request.hour, minute=request.minute, second=0, microsecond=0
                )
                if request.day_offset == 0 and target <= now:
                    target += timedelta(days=1)
                automation = next(iter(selection.automations))
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None, last_entities=(), last_area=None,
                        pending_clarification=None,
                        pending_automation_management=PendingAutomationManagement(
                            request=request, automation=automation, target=target
                        ),
                    ),
                )
                response.async_set_speech(
                    f"Soll ich {_automation_label(automation)} bis "
                    f"{format_scheduled_time(target.isoformat())} pausieren?"
                )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_automation_management_confirmation(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingAutomationManagement,
    ) -> conversation.ConversationResult:
        if pending.candidates:
            automation = select_candidate_reply(user_input.text, pending.candidates)
            if automation is None:
                response.async_set_speech(
                    "Das ist nicht eindeutig. Bitte nenne eine Automation oder sage "
                    "die erste, die zweite oder die dritte: "
                    + ", ".join(_automation_label(item) for item in pending.candidates)
                    + "."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            request = pending.request
            if request.kind is AutomationManagementKind.RESCHEDULE:
                now = dt_util.now()
                if automation.scheduled_for is None:
                    self._context_store.clear(user_input.conversation_id)
                    response.async_set_speech(
                        "Der passende Auftrag hat keine sichere geplante Zeit."
                    )
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                target = datetime.fromisoformat(automation.scheduled_for)
                if request.hour is None:
                    self._context_store.clear(user_input.conversation_id)
                    response.async_set_speech("Die neue Uhrzeit ist unvollständig.")
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                target = target.replace(hour=request.hour, minute=request.minute, second=0)
                comparable_now = now
                if target.tzinfo is None and now.tzinfo is not None:
                    comparable_now = now.replace(tzinfo=None)
                elif target.tzinfo is not None and now.tzinfo is None:
                    comparable_now = now.replace(tzinfo=target.tzinfo)
                if target <= comparable_now:
                    self._context_store.clear(user_input.conversation_id)
                    response.async_set_speech("Die neue Uhrzeit liegt bereits in der Vergangenheit.")
                    return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
                replacement = PendingAutomationManagement(request, automation, target)
                question = (
                    f"Soll ich {_automation_label(automation)} wirklich auf "
                    f"{format_scheduled_time(target.isoformat())} verschieben?"
                )
            elif request.kind is AutomationManagementKind.DUPLICATE:
                replacement = PendingAutomationManagement(request, automation)
                question = f"Soll ich {_automation_label(automation)} wirklich duplizieren?"
            elif request.kind is AutomationManagementKind.PAUSE_UNTIL:
                now = dt_util.now()
                if request.hour is None:
                    self._context_store.clear(user_input.conversation_id)
                    response.async_set_speech("Die Uhrzeit ist nicht vollständig.")
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                target = (now + timedelta(days=request.day_offset)).replace(
                    hour=request.hour, minute=request.minute, second=0, microsecond=0
                )
                if request.day_offset == 0 and target <= now:
                    target += timedelta(days=1)
                replacement = PendingAutomationManagement(request, automation, target)
                question = (
                    f"Soll ich {_automation_label(automation)} bis "
                    f"{format_scheduled_time(target.isoformat())} pausieren?"
                )
            else:
                replacement = PendingAutomationManagement(request, automation)
                question = (
                    f"Soll {_automation_label(automation)} wirklich auf "
                    f"{request.max_runs} Ausführungen begrenzt werden?"
                )
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None, last_entities=(), last_area=None,
                    pending_clarification=None,
                    pending_automation_management=replacement,
                ),
            )
            response.async_set_speech(question)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        self._context_store.clear(user_input.conversation_id)
        if reply is ConfirmationReply.NO:
            response.async_set_speech("Abgebrochen. Es wurde nichts verändert.")
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        assert self._automation_executor is not None
        request = pending.request
        try:
            if request.kind is AutomationManagementKind.CLEAN_EXPIRED:
                removed = await self._automation_executor.async_cleanup_expired_scheduled_automations(dt_util.now())
                response.async_set_speech(
                    "Es waren keine abgelaufenen HomeIntent-Aufträge vorhanden."
                    if not removed else f"{len(removed)} abgelaufene HomeIntent-Aufträge wurden gelöscht."
                )
            elif request.kind is AutomationManagementKind.ROLLBACK:
                operation = await self._automation_executor.async_rollback_last_change()
                response.async_set_speech(
                    f"Die letzte HomeIntent-Änderung ({operation}) wurde rückgängig gemacht."
                )
            elif request.kind is AutomationManagementKind.RESCHEDULE:
                assert pending.automation is not None and pending.target is not None
                await self._automation_executor.async_reschedule_automation(
                    pending.automation.automation_id, pending.target
                )
                response.async_set_speech(
                    "Der Auftrag wurde auf " + format_scheduled_time(pending.target.isoformat()) + " verschoben."
                )
            elif request.kind is AutomationManagementKind.DUPLICATE:
                assert pending.automation is not None
                await self._automation_executor.async_duplicate_automation(
                    pending.automation.automation_id
                )
                response.async_set_speech(
                    f"{_automation_label(pending.automation)} wurde dupliziert."
                )
            elif request.kind is AutomationManagementKind.PAUSE_UNTIL:
                assert pending.automation is not None and pending.target is not None
                await self._automation_executor.async_pause_automation_until(
                    pending.automation.automation_id, pending.target
                )
                response.async_set_speech(
                    f"{_automation_label(pending.automation)} ist bis "
                    f"{format_scheduled_time(pending.target.isoformat())} pausiert."
                )
            else:
                assert pending.automation is not None
                assert request.max_runs is not None
                await self._automation_executor.async_set_max_runs(
                    pending.automation.automation_id, request.max_runs
                )
                response.async_set_speech(
                    f"{_automation_label(pending.automation)} wird nur noch {request.max_runs} Mal ausgeführt."
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Automation management failed: %s", err, exc_info=True)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ändern der Automation: {err}",
            )
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def _async_handle_service_confirmation_reply(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        confirmation: PendingServiceConfirmation,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Confirm a high-risk lock or garage movement before execution."""
        current_user_id = conversation_user_id(user_input)
        if (
            confirmation.requested_by_user_id is not None
            and confirmation.requested_by_user_id != current_user_id
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Diese Bestätigung gehört zu einem anderen Benutzer.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        self._context_store.clear(user_input.conversation_id)
        if reply is ConfirmationReply.NO:
            response.async_set_speech("Abgebrochen. Es wurde nichts ausgeführt.")
        else:
            undo = build_undo_plan(
                confirmation.plan,
                entities,
                requested_by_user_id=current_user_id,
            )
            is_admin = await user_is_admin(self.hass, user_input)
            execution = await async_execute_service_plan(
                self.hass,
                confirmation.plan,
                entities,
                self.entry.options,
                is_admin=is_admin,
                user_id=current_user_id,
                confirmed=True,
                audit_trail=self._audit_trail,
                audit_actor_id=current_user_id,
                effect_monitor=self._runtime_data.effect_monitor,
                origin=confirmation.origin,
                binding_confirmed=confirmation.binding_confirmed,
            )
            for additional in confirmation.additional_plans:
                if not execution.executed:
                    break
                execution = await async_execute_service_plan(
                    self.hass,
                    additional,
                    entities,
                    self.entry.options,
                    is_admin=is_admin,
                    user_id=current_user_id,
                    confirmed=True,
                    audit_trail=self._audit_trail,
                    audit_actor_id=current_user_id,
                    effect_monitor=self._runtime_data.effect_monitor,
                    origin=confirmation.origin,
                )
            if not execution.executed:
                _LOGGER.error(
                    "Confirmed service call %s.%s on %s failed: %s",
                    confirmation.plan.domain,
                    confirmation.plan.service,
                    confirmation.plan.entity_id,
                    execution.error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ausführen: {execution.error}",
                )
            else:
                spoken = _with_effect_summary(confirmation.success_text, execution)
                if confirmation.binding_offer is not None:
                    spoken = f"{spoken} " + await self._async_store_routine_binding(
                        *confirmation.binding_offer, current_user_id
                    )
                response.async_set_speech(spoken)
                if undo is not None:
                    self._context_store.set(
                        user_input.conversation_id,
                        ConversationContext(
                            last_command=None,
                            last_entities=(),
                            last_area=None,
                            pending_clarification=None,
                            pending_undo=undo,
                        ),
                    )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_handle_automation_deletion_confirmation_reply(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        deletion: PendingAutomationDeletion,
    ) -> conversation.ConversationResult:
        """V5 Teil 8/10 (V5.28, "Automation Deletion"): the deletion
        counterpart to ``_async_handle_automation_confirmation_reply()``
        above - same closed yes/no vocabulary, same ``UNCLEAR``-keeps-
        pending/``NO``-and-failure-clear-and-persist-nothing shape. No
        ``entities`` parameter is needed here (unlike the creation reply):
        deletion doesn't regenerate anything against live HA state, it just
        removes the already-resolved ``deletion.automation`` by id.
        """
        reply = classify_confirmation_reply(user_input.text)

        if reply is ConfirmationReply.UNCLEAR:
            response.async_set_speech(AUTOMATION_DELETION_CONFIRMATION_UNCLEAR_TEXT)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        self._context_store.clear(user_input.conversation_id)

        if reply is ConfirmationReply.NO:
            response.async_set_speech(AUTOMATION_DELETION_CANCELLED_TEXT)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if self._automation_executor is None:
            self._automation_executor = AutomationExecutor(self.hass)
        try:
            await self._automation_executor.async_delete_automation(
                deletion.automation.automation_id
            )
        except Exception as err:  # noqa: BLE001 - a YAML write + service call can fail in ways beyond HomeAssistantError; must not propagate as "Unexpected error during intent recognition"
            _LOGGER.error("Automation deletion failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Löschen der Automation: {err}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        response.async_set_speech(AUTOMATION_DELETED_TEXT)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )


def _routine_definition_from_payload(
    routine_id: str,
    owner_user_id: str,
    payload: object,
    entities: list[EntitySnapshot],
) -> RoutineDefinition | None:
    """Convert already validated V8 actions into a non-executable routine draft."""
    matches = (
        (payload,)
        if isinstance(payload, MatchResult)
        else payload.commands
        if isinstance(payload, CommandPlan)
        else ()
    )
    plans = [item.plan for item in matches if item.plan is not None]
    if not plans or len(plans) != len(matches):
        return None
    by_id = {item.entity_id: item for item in entities}
    steps: list[RoutineStepDefinition] = []
    for plan in plans:
        desired = _desired_state_from_service_plan(plan)
        if desired is None:
            return None
        raw_targets = (
            (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
        )
        for entity_id in raw_targets:
            entity = by_id.get(entity_id)
            if entity is None:
                return None
            steps.append(
                RoutineStepDefinition(
                    f"definition-{len(steps) + 1}",
                    GoalScope(entity_ids=(entity_id,)),
                    desired,
                    f"{entity.friendly_name}: {desired.property_name} = {desired.value}",
                )
            )
    return RoutineDefinition(
        routine_id,
        routine_id.replace("_", " ").capitalize(),
        owner_user_id,
        tuple(steps),
        False,
    )


def _desired_state_from_service_plan(plan: ServiceCallPlan) -> DesiredState | None:
    if plan.service in {"turn_on", "turn_off"}:
        brightness = plan.data.get("brightness_pct")
        if plan.service == "turn_on" and isinstance(brightness, (int, float)):
            return DesiredState("brightness", float(brightness), "%")
        return DesiredState("state", "on" if plan.service == "turn_on" else "off")
    if plan.service == "close_cover":
        return DesiredState("state", "closed")
    if plan.service == "lock":
        return DesiredState("state", "locked")
    if plan.service == "set_temperature":
        temperature = plan.data.get("temperature")
        if isinstance(temperature, (int, float)):
            return DesiredState("temperature", float(temperature), "°C")
    if plan.service == "set_cover_position":
        position = plan.data.get("position")
        if isinstance(position, (int, float)):
            return DesiredState("position", float(position), "%")
    return None


def _comfort_profile_from_document(
    document: LanguageDocument,
    area_id: str,
    owner_user_id: str,
) -> ComfortProfile | None:
    """Extract explicit numeric ranges; absent dimensions remain unset."""
    tokens = document.tokens
    unit_positions = [
        index
        for index, token in enumerate(tokens)
        if token.canonical in {"grad", "prozent", "%"}
    ]

    def values_before(position: int) -> tuple[float, ...]:
        previous_unit = max((item for item in unit_positions if item < position), default=-1)
        values: list[float] = []
        for token in tokens[previous_unit + 1 : position]:
            if not token.is_number:
                continue
            try:
                values.append(float(token.canonical.replace(",", ".")))
            except ValueError:
                continue
        return tuple(values[-2:])

    temperature: tuple[float, ...] = ()
    brightness: tuple[float, ...] = ()
    for position in unit_positions:
        unit = tokens[position].canonical
        if unit == "grad" and not temperature:
            temperature = values_before(position)
        elif unit in {"prozent", "%"} and not brightness:
            brightness = values_before(position)
    if not temperature and not brightness:
        return None
    temperature_min = min(temperature) if temperature else None
    temperature_max = max(temperature) if temperature else None
    brightness_min = round(min(brightness)) if brightness else None
    brightness_max = round(max(brightness)) if brightness else None
    return ComfortProfile(
        f"comfort:{owner_user_id}:{area_id}",
        owner_user_id,
        area_id,
        temperature_min,
        temperature_max,
        brightness_min,
        brightness_max,
        confirmed=False,
    )


def _comfort_profile_preview(profile: ComfortProfile) -> str:
    parts: list[str] = []
    if profile.temperature_min is not None and profile.temperature_max is not None:
        parts.append(
            f"Temperatur {profile.temperature_min:g} bis {profile.temperature_max:g} Grad"
        )
    if profile.brightness_min is not None and profile.brightness_max is not None:
        parts.append(
            f"Helligkeit {profile.brightness_min} bis {profile.brightness_max} Prozent"
        )
    return ", ".join(parts)


def _comfort_value_signature(profile: ComfortProfile) -> str:
    """Comparable typed value; never averages conflicting user profiles."""
    return repr((
        profile.temperature_min, profile.temperature_max,
        profile.brightness_min, profile.brightness_max,
        profile.color_temperature_kelvin, profile.humidity_min,
        profile.humidity_max, profile.cover_position,
    ))


def _scheduled_action_model(plan: ServiceCallPlan) -> ActionModel | None:
    """Lift a closed service plan into the existing typed automation model."""
    entity_ids = (
        (plan.entity_id,)
        if isinstance(plan.entity_id, str)
        else tuple(plan.entity_id)
    )
    if not entity_ids:
        return None
    target = TriggerTarget(
        domain=entity_ids[0].partition(".")[0],
        entity_id=entity_ids[0] if len(entity_ids) == 1 else None,
        entity_ids=entity_ids if len(entity_ids) > 1 else (),
    )
    if plan.service == "set_temperature" and plan.domain == "climate":
        value = plan.data.get("temperature")
        if isinstance(value, (int, float)):
            return ActionModel(ActionType.SET_TEMPERATURE, target=target, value=float(value))
    if plan.service in {"turn_on", "turn_off"} and plan.domain == "homeassistant":
        return ActionModel(
            ActionType.TURN_ON if plan.service == "turn_on" else ActionType.TURN_OFF,
            target=target,
        )
    return None


def _goal_semantic_choice(document: LanguageDocument) -> GoalSemanticChoice | None:
    words = frozenset(token.canonical for token in document.tokens if token.is_word)
    normalized = document.normalized_text.casefold()
    if words & {"ersteres", "erstes"}:
        return GoalSemanticChoice.SETPOINT_AT_TIME
    if words & {"zweiteres", "zweites"}:
        return GoalSemanticChoice.ACHIEVE_BY_DEADLINE
    setpoint = bool(words & {"sollwert", "einstellen", "setz", "setzen", "stell"}) and bool(
        words & {"dann", "zeitpunkt", "uhr", "einfach"}
    )
    achieved = bool(words & {"erreicht", "warm", "sein", "haben"}) and bool(
        words & {"bis", "dahin", "schon"}
    )
    if setpoint and not achieved:
        return GoalSemanticChoice.SETPOINT_AT_TIME
    if achieved and not setpoint:
        return GoalSemanticChoice.ACHIEVE_BY_DEADLINE
    if "zum zeitpunkt" in normalized:
        return GoalSemanticChoice.SETPOINT_AT_TIME
    return None


def _mentioned_goal_run_entity(
    document: LanguageDocument, entities: Sequence[EntitySnapshot]
) -> str | None:
    normalized = normalize_for_compare(document.source_text)
    matches = {
        entity.entity_id
        for entity in entities
        if any(
            candidate and candidate in normalized
            for candidate in (
                normalize_for_compare(entity.friendly_name),
                normalize_for_compare(entity.entity_id.partition(".")[2]),
            )
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _goal_run_label(run: GoalRun) -> str:
    if run.goal.routine_id:
        return run.goal.routine_id.replace("_", " ").capitalize()
    labels = {
        V10GoalKind.MONITOR_AND_NOTIFY: "Monitor-Ziel",
        V10GoalKind.SCHEDULED: "terminiertes Ziel",
        V10GoalKind.COMFORT: "Komfortziel",
    }
    return labels.get(run.goal.kind, run.source_utterance.strip() or run.goal.kind.value)


def _model_matches_hint(model: LearnedModel, hint: str | None) -> bool:
    if hint is None:
        return True
    searchable = " ".join(
        (model.model_id, model.subject, *(str(value) for value in model.context.values()))
    ).casefold()
    aliases = {
        "heizung": ("thermal", "climate", "heizung"),
        "garage": ("garage", "cover"),
        "licht": ("light", "licht", "lampe"),
        "lampe": ("light", "licht", "lampe"),
        "morgenroutine": ("habit", "morning", "morgen"),
    }
    return any(term in searchable for term in aliases.get(hint, (hint,)))


def _thermal_advice_for_goal(
    goal: Goal,
    entities: Sequence[EntitySnapshot],
    predictive_house: PredictiveHouseModel | None,
) -> AdaptivePlanningAdvice | None:
    """Use only an installed model's exact confirmed measurement binding."""
    if predictive_house is None or goal.scope.area_id is None or not goal.desired_states:
        return None
    model = predictive_house.thermal_model(goal.scope.area_id)
    if model is None or not model.binding.confirmed:
        return None
    by_id = {item.entity_id: item for item in entities}
    temperature = by_id.get(model.binding.temperature_entity_id)
    if temperature is None or temperature.state in {"unknown", "unavailable"}:
        return None
    try:
        raw_current = float(temperature.state)
    except ValueError:
        return None
    normalized_current = normalize_measurement(
        raw_current, temperature.unit, SemanticProperty.TEMPERATURE
    )
    if normalized_current is None:
        return None
    desired = goal.desired_states[0]
    if desired.property_name != "temperature" or not isinstance(desired.value, (int, float)):
        return None
    normalized_target = normalize_measurement(
        float(desired.value), desired.unit, SemanticProperty.TEMPERATURE
    )
    if normalized_target is None:
        return None
    outdoor_value: float | None = None
    outdoor_id = model.binding.outdoor_temperature_entity_id
    if outdoor_id is not None:
        outdoor = by_id.get(outdoor_id)
        if outdoor is None or outdoor.state in {"unknown", "unavailable"}:
            return None
        try:
            raw_outdoor = float(outdoor.state)
        except ValueError:
            return None
        normalized_outdoor = normalize_measurement(
            raw_outdoor, outdoor.unit, SemanticProperty.TEMPERATURE
        )
        if normalized_outdoor is None:
            return None
        outdoor_value = normalized_outdoor.value
    prediction = predictive_house.predict_thermal(
        goal.scope.area_id, current_celsius=normalized_current.value,
        target_celsius=normalized_target.value, outdoor_celsius=outdoor_value,
        now=dt_util.now(),
    )
    return advise_deadline_goal(goal, prediction)


def _learning_control_speech(error: LearningControlError) -> str:
    return {
        "wrong_owner": "Dieses gelernte Wissen gehört zu einem anderen Benutzer.",
        "invalid_state": "Dieser Vorschlag ist bereits entschieden.",
        "not_found": "Der Kandidat ist nicht mehr verfügbar.",
    }.get(error.code.value, "Diese Änderung ist für dieses Modell nicht möglich.")


_LEARNED_KIND_DE = {
    "fact": "Fakt",
    "preference": "Vorliebe",
    "thermal_model": "Wärmemodell",
    "effect_timing": "Wirkungsdauer",
    "reliability": "Zuverlässigkeit",
    "habit": "Gewohnheit",
    "duration": "Laufzeit",
    "energy": "Energieverbrauch",
    "battery_trend": "Batterieverlauf",
}
_TIME_BAND_DE = {"morning": "morgens", "day": "tagsüber", "evening": "abends", "night": "nachts"}
_MODEL_HEALTH_DE = {
    "valid": "gültig",
    "low_confidence": "noch unsicher",
    "unreliable": "unzuverlässig",
    "stale": "veraltet",
    "drift_detected": "Abweichung erkannt",
    "invalid": "ungültig",
}


def _learned_model_summary(model: LearnedModel) -> str:
    """German summary; no English enum values reach the speech (7.4.1)."""
    state = {
        "observed": "beobachtet",
        "inferred": "vermutet",
        "confirmed": "bestätigt",
    }[model.knowledge_state.value]
    kind = _LEARNED_KIND_DE.get(model.kind.value, model.kind.value)
    health = _MODEL_HEALTH_DE.get(model.health.value, model.health.value)
    confidence = f"{model.confidence:.2f}".replace(".", ",")
    return (
        f"{kind} für {model.subject}: {state}, "
        f"{model.sample_count} Belege, Konfidenz {confidence}, Status {health}"
    )


def _german_goal_labels(labels: Sequence[str]) -> str:
    values = tuple(dict.fromkeys(labels))
    if not values:
        return "keine benannten Ziele"
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " und " + values[-1]


def _german_count(value: int) -> str:
    return {2: "zwei", 3: "drei"}.get(value, str(value))


def _select_goal_run_reply(
    document: LanguageDocument,
    run_ids: Sequence[str],
    labels: Sequence[str],
) -> str | None:
    words = frozenset(token.canonical for token in document.tokens if token.is_word)
    if words & {"erstes", "ersteres", "erste"} and run_ids:
        return run_ids[0]
    if words & {"zweites", "zweiteres", "zweite"} and len(run_ids) >= 2:
        return run_ids[1]
    normalized = normalize_for_compare(document.source_text)
    matches = {
        run_id
        for run_id, label in zip(run_ids, labels, strict=True)
        if normalize_for_compare(label) in normalized
    }
    return next(iter(matches)) if len(matches) == 1 else None
