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

import logging
import re
from functools import partial
from dataclasses import dataclass, replace
from typing import Any, Literal, Sequence

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .automation_executor import AutomationExecutor
from .automation_composition import OutcomeKind
from .alias_learning import (
    AliasLearningDraft,
    parse_alias_learning,
)
from .automation_action_edit import is_action_edit_request
from .automation_scenarios import interpret_downstairs_shutdown
from .automation_management import parse_automation_management
from .automation_structure_edit import parse_automation_structure_edit
from .automation_wizard import starts_automation_wizard
from .advanced_queries import match_advanced_query
from .calendar_event import start_calendar_event_draft, writable_calendars
from .calendar_management import CalendarManagementRequest
from .capability_audit import match_capability_audit_query
from .conversation_location import (
    materialize_local_reference,
    resolve_conversation_area,
)
from .const import CONF_HOUSE_RELATIONS, DOMAIN
from .dialog_manager import DialogPriority, DialogTaskKind
from .engine import (
    AutomationClarificationResult,
    AutomationDraftMatchResult,
    AutomationDeletionMatchResult,
    AutomationMatchResult,
    AutomationToggleMatchResult,
    CommandPlan,
    MonitorProposalResult,
    MatchResult,
    _AUTOMATION_DELETE_RE,
    _AUTOMATION_DISABLE_RE,
    _AUTOMATION_ENABLE_RE,
    _AUTOMATION_QUERY_RE,
)
from .entities import EntitySnapshot, normalize_for_compare
from .hass_entities import (
    build_device_snapshots,
    build_entity_snapshots,
    exposure_hint,
    hidden_entity_names,
)
from .history_query import parse_history_query
from .household_query import match_household_query
from .embedded_question import embedded_check_question
from .response_style import apply_response_style
from .turn_outcome import begin_outcomes, end_outcomes
from .effect_wait import append_speech, async_settle_turn
from .automation_ownership import begin_shared_turn
from .monitoring_management import names_managed_object, parse_monitoring_management
from .house_graph import HouseGraph, parse_relation_specs
from .management_understanding import understand_management
from .proactive_dialog import V12_TASK_KINDS
from .proactive_session import classify_proposal_reply
from .room_presence import build_area_lookup
from .thermal_question import answer_thermal_question
from .nlu.clock_language import normalize_clock_expressions, wake_request
from .nlu.semantic_exclusion import canonical_exception_words
from .nlu.normalize import expand_clitics
from .nlu.german_morphology import dative_location_phrase
from .nlu.place_model import build_place_lexicon
from .nlu.device_ontology import analyse_word, lookup_genus_word
from .nlu.target_resolution import genus_members, hidden_device_text, hidden_name_mentions
from .nlu.situation_views import answer_situation_view
from .nlu.utterance_meaning import render_maintain, render_non_executable
from .nlu.self_correction import render_correction, utterance_fields
from .nlu.surface import prepare_surface
from .nlu.ellipsis_contract import EllipsisFields, ellipsis_fields, violation
from .nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from .nlu.action_model import NotificationRecipient, NotificationRecipientKind
from .notification_request import NotificationRequest
from .nlu.automation_model import TriggerTarget
from .nlu.context import (
    active_pending_dialog,
    ConversationContext,
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
from .nlu.entity_clarification import (
    CandidateReplyKind,
    render_candidate_question,
    resolve_candidate_reply,
)
from .nlu.explanation import is_explanation_request
from .nlu.language_frontend import analyse_language
from .nlu.semantic_utterance import (
    Modality,
    SpeechAct,
    analyse_utterance,
    is_contextual_followup,
)
from .nlu.understanding import UnderstandingAuthority
from .nlu.understanding_context import UnderstandingContext
from .service_executor import async_execute_service_plan
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
from .structure_cache import SHARED as STRUCTURE_CACHE, structure_key
from .world_model import WorldModel
from .undo import is_undo_request
from .runtime_data import HomeIntentRuntimeData
from .execution_context import begin_turn, end_turn
from .execution_trace import Evidence
from .nlu.causal_question import interpret_cause_question
from .conversation_learning import DialogLearningMixin, is_known_device_word
from .nlu.meaning_ir import is_deferred
from .controllers.comfort import ComfortController
from .controllers.devices import DeviceController
from .nlu.dialog_meta import MetaQuestion, meta_questions
from .nlu.stt_repair import join_split_compounds
from .controllers.learning import LearningController
from .controllers.routines import RoutineController, RoutineSelection
from .controllers.goals import GoalController
from .controllers.monitoring import MonitoringController
from .controllers.automation_management import AutomationManagementController
from .controllers.automations import AutomationController
from .controllers.notifications import NotificationController
from .controllers.productivity import ProductivityController
from .controllers.queries import QueryController
from .controllers.replies import ambiguous_reading_text
from .arbitration import (
    DecisionKind,
    DialogReply,
    arbitrate,
    arbitrate_dialog,
    needs_command_reading,
)
from .arbitration_candidates import (
    complete_command_candidate,
    context_candidates,
    dialog_evidence,
    need_query_candidates,
)
from .dialog_learning import alias_rejection, unknown_device_noun
from .routine_binding_intent import interpret_routine_binding


from .productivity import TimerRequest, TodoRequest


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


# Open questions whose expected answer is itself a command (a routine being
# defined step by step, an automation action): a command answers them.
# Dialogs that compose an automation: a complete command is their content,
# not a new topic (unless the user switches explicitly, "mach lieber ...").
_COMPOSING_DIALOG_KINDS = frozenset({
    PendingDialogKind.AUTOMATION_DRAFT,
    PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION,
    PendingDialogKind.AUTOMATION_ACTION_EDIT,
    PendingDialogKind.AUTOMATION_STRUCTURE_EDIT,
    PendingDialogKind.AUTOMATION_WIZARD,
})
_DIALOG_REPLY = {
    ConfirmationReply.YES: DialogReply.YES,
    ConfirmationReply.NO: DialogReply.NO,
    ConfirmationReply.UNCLEAR: DialogReply.UNCLEAR,
}
_COMMAND_ANSWER_TASK_KINDS = frozenset({
    DialogTaskKind.ROUTINE_DEFINITION,
    DialogTaskKind.AUTOMATION,
})


# HomeIntent V5 Teil 8/10 (Wave 11, "Automation Disable/Enable") - no
# confirmation-round-trip vocabulary here (see ``AutomationToggleMatchResult``'s
# own docstring for why): a single-match toggle executes immediately, so
# only an error text is needed here, mirroring the ordinary command path's
# own ``FAILED_TO_HANDLE`` wording.


@dataclass(frozen=True)
class _TimedFollowup:
    """A follow-up whose own time must not run now (7.7.1 A3)."""

    payload: Any


def _written_entities(payload: Any, entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    """The devices a context reading would write to."""
    plans = []
    if isinstance(payload, CommandPlan):
        plans = [item.plan for item in payload.commands if getattr(item, "plan", None) is not None]
    elif getattr(payload, "plan", None) is not None:
        plans = [payload.plan]
    ids: set[str] = set()
    for plan in plans:
        value = getattr(plan, "entity_id", None)
        if isinstance(value, str):
            ids.add(value)
        elif isinstance(value, (list, tuple)):
            ids.update(item for item in value if isinstance(item, str))
    return [entity for entity in entities if entity.entity_id in ids]


_TIMED_PHRASES = {
    "turn_on": ("Schalte", "ein"), "turn_off": ("Schalte", "aus"),
    "open_cover": ("Öffne", ""), "close_cover": ("Schließe", ""),
}


def timed_followup_text(payload: Any, text: str, entities: Sequence[EntitySnapshot]) -> str | None:
    """"Morgen früh wieder an" after the hall light -> a complete time-bound
    command for the ordinary scheduling path; never an immediate write."""
    plan = getattr(payload, "plan", None)
    phrase = _TIMED_PHRASES.get(getattr(plan, "service", ""))
    written = _written_entities(payload, entities)
    time = next((item.text for item in utterance_fields(text, entities)[0] if item.kind == "time"), None)
    if phrase is None or not written or time is None:
        return None
    names = " und ".join(entity.friendly_name for entity in written)
    verb, particle = phrase
    return f"{verb} {names} {time} {particle}".strip() + "."


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
        # Domain controllers (7.7, B4): each gets only what it needs.
        self._productivity = ProductivityController(
            hass=lambda: self.hass,
            context_store=self._context_store,
            runtime=runtime,
            record_execution=self._record_execution,
            world_model=lambda: self._world_model,
        )
        self._notifications = NotificationController(
            hass=lambda: self.hass,
            entry=entry,
            context_store=self._context_store,
            runtime=runtime,
        )
        self._routines = RoutineController(
            hass=lambda: self.hass,
            entry=entry,
            context_store=self._context_store,
            audit_trail=self._audit_trail,
            runtime=runtime,
            pending_confirmation=lambda *args, **kwargs: self._devices.pending_confirmation(
                *args, **kwargs
            ),
        )
        self._devices = DeviceController(
            hass=lambda: self.hass,
            entry=entry,
            context_store=self._context_store,
            engine=self._engine,
            world_model=lambda: self._world_model,
            audit_trail=self._audit_trail,
            runtime=runtime,
            entities=lambda: build_entity_snapshots(self.hass, self.entry),
            conversation_area=lambda user_input: resolve_conversation_area(self.hass, user_input),
            ask_unknown_word=self._async_ask_unknown_word,
            default_choice=self._default_choice,
            store_routine_binding=self._routines.async_store_binding,
        )
        self._goals = GoalController(
            hass=lambda: self.hass,
            entry=entry,
            engine=self._engine,
            world_model=lambda: self._world_model,
            executor=self._automation_store,
            audit_trail=self._audit_trail,
            runtime=runtime,
            entities=lambda: build_entity_snapshots(self.hass, self.entry),
            conversation_area=lambda user_input: resolve_conversation_area(self.hass, user_input),
        )
        self._monitoring = MonitoringController(
            runtime=runtime, automation_store=self._automation_store, hass=lambda: self.hass,
            options=lambda: self.entry.options,
        )
        self._comfort = ComfortController(
            hass=lambda: self.hass,
            entry=entry,
            audit_trail=self._audit_trail,
            runtime=runtime,
            entities=lambda: build_entity_snapshots(self.hass, self.entry),
            stage_plan=self._goals.stage_plan,
        )
        self._learning = LearningController(
            hass=lambda: self.hass,
            entry=entry,
            runtime=runtime,
            learned_alias_view=self.learned_alias_view,
        )
        self._queries = QueryController(
            hass=lambda: self.hass,
            context_store=self._context_store,
            audit_trail=self._audit_trail,
            runtime=runtime,
        )
        self._management = AutomationManagementController(
            context_store=self._context_store,
            executor=self._automation_store,
            engine=self._engine,
            world_model=lambda: self._world_model,
            hass=lambda: self.hass, options=lambda: self.entry.options,
        )
        self._automations = AutomationController(
            hass=lambda: self.hass,
            entry=entry,
            context_store=self._context_store,
            engine=self._engine,
            world_model=lambda: self._world_model,
            executor=self._automation_store,
            runtime=runtime,
            notifications=self._notifications,
            record_execution=self._record_execution,
        )
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
        user_input = begin_shared_turn(user_input)  # "… für uns alle" (7.9.2 A3)
        turn = begin_turn(user_input, conversation_user_id(user_input), user_input.text)
        self._engine.take_action_ambiguity()  # nothing stale from an earlier turn
        outcomes, outcome_token = begin_outcomes()
        self._current_chat_log = chat_log
        try:
            result = await self._async_handle_message_inner(user_input, chat_log)
            notes = await async_settle_turn(self.hass, outcomes, self.entry.options)
        finally:
            end_outcomes(outcome_token)
            end_turn(turn)
        append_speech(result.response, notes)
        suffix = self._take_turn_suffix(user_input.conversation_id)
        if suffix:
            # "Soll ich mir … merken?" after an executed command (7.4.1).
            speech = result.response.speech
            spoken = (
                speech.get("plain", {}).get("speech", "")
                if isinstance(speech, dict) else str(speech or "")
            )
            result.response.async_set_speech(f"{spoken} {suffix}".strip())
        awaiting_answer = self._apply_continue_conversation(user_input, result) or bool(suffix)
        # Speech or confirmation tone: decided here, once, from typed facts.
        apply_response_style(self.hass, self.entry.options, user_input, result, outcomes, awaiting_answer)
        return result

    def _apply_continue_conversation(
        self,
        user_input: conversation.ConversationInput,
        result: conversation.ConversationResult,
    ) -> bool:
        """Flag the turn as awaiting an answer, for clients that can listen on.

        Read back from the context store rather than from anything the turn
        returned: the pending state is written by whichever handler asked the
        question, so this stays correct for handlers that do not exist yet.
        """
        conversation_id = result.conversation_id or user_input.conversation_id
        if conversation_id is None:
            return False
        active = active_pending_dialog(self._context_store.get(conversation_id))
        awaiting_answer = (
            active is not None and active.kind in _CONTINUE_CONVERSATION_KINDS
        ) or self._runtime_data.dialog_manager.has_open_question(conversation_id)
        if not awaiting_answer:
            return False
        # ConversationResult grew this field in Home Assistant 2025.2 and is a
        # slots dataclass, so on an older core the assignment raises instead of
        # silently adding an attribute. Setting it after construction (rather
        # than passing a constructor keyword) confines that to a no-op here,
        # instead of a TypeError on every single turn.
        try:
            result.continue_conversation = True
        except AttributeError:  # pragma: no cover - pre-2025.2 cores only
            pass
        return True

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
        entities = await self._learning.async_apply_confirmed_preferences(
            entities,
            area_id=(conversation_area.area_id if conversation_area is not None else None),
            user_id=conversation_user_id(user_input),
        )
        # Preferences only add a confirmed contextual alias to the existing
        # snapshots. Rebuild the same authoritative NOW view; no second
        # resolver or WorldModel is introduced.
        # Index and house graph depend only on the registry/exposure/alias
        # structure; they are rebuilt when it changes, states stay live (7.8 B8).
        structure = structure_key(entities, devices)
        self._world_model = STRUCTURE_CACHE.world_model(entities, devices, structure)
        world_model = self._world_model
        try:
            configured_relations = parse_relation_specs(
                self.entry.options.get(CONF_HOUSE_RELATIONS)
            )
        except ValueError:
            # Invalid migrated configuration never weakens language safety.
            configured_relations = ()
        self._house_graph = STRUCTURE_CACHE.house_graph(
            hash((structure, configured_relations)),
            lambda: world_model.build_house_graph(configured_relations),
            entities,
        )
        self._world_model = self._world_model.with_house_graph(self._house_graph)
        understanding_context = UnderstandingContext(source_area=conversation_area)
        localized_text = materialize_local_reference(
            canonical_exception_words(normalize_clock_expressions(expand_clitics(
                # Speech recognition splits compounds ("küchen licht"); join
                # them only into exact registry/vocabulary words (7.7 B8).
                join_split_compounds(user_input.text, entities)
            ))),
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
        # One shared surface for every reader of the turn (7.7.1/7.8):
        # self correction, frames, short commands, operable device,
        # coordination. An abort or an unclear correction runs nothing.
        surface = prepare_surface(localized_text, entities)
        if surface.stops:
            response.async_set_speech(render_correction(surface.correction))
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        localized_text = surface.text
        frames = surface.frames
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
        if frames.found:
            language_document = replace(language_document, pragmatics=frames)
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
        # Open questions are typed evidence for the arbiter (7.7, B3): it
        # decides whether this turn answers them or supersedes them.
        reply = _DIALOG_REPLY[classify_confirmation_reply(user_input.text)]
        contextual = is_contextual_followup(user_input.text)
        dialog_evidence_for = partial(
            dialog_evidence, document=language_document, reply=reply,
            contextual_followup=contextual,
        )
        opening_decision = (
            arbitrate_dialog(dialog_evidence_for(
                active_dialog.kind.name,
                supersedable=False,
                drops_on_new_sentence=active_dialog.kind is PendingDialogKind.SERVICE_CONFIRMATION,
            ))
            if active_dialog is not None
            else None
        )
        if opening_decision is not None and opening_decision.kind is DecisionKind.SUPERSEDE_DIALOG:
            if opening_decision.reason == "new_question_drops_question":
                # Answer the question, say that the open one is gone (7.8 B7).
                self._append_to_turn(
                    user_input.conversation_id,
                    "Die offene Rückfrage habe ich verworfen; es wurde nichts ausgeführt.",
                )
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
            evidence = dialog_evidence_for(
                active_dialog.kind.name,
                supersedable=(
                    active_dialog.kind not in _COMPOSING_DIALOG_KINDS or explicit_topic_switch
                ),
            )
            if needs_command_reading(evidence):
                candidate = self._engine.understand(
                    user_input.text,
                    entities,
                    self._world_model,
                    language_document,
                    context=understanding_context,
                )
                if arbitrate_dialog(
                    evidence, complete_command_candidate(candidate.payload)
                ).kind is DecisionKind.SUPERSEDE_DIALOG:
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
        task_evidence = (
            dialog_evidence_for(
                active_task.kind.name,
                supersedable=active_task.kind not in _COMMAND_ANSWER_TASK_KINDS,
            )
            if active_dialog is None and active_task is not None
            else None
        )
        if task_evidence is not None and needs_command_reading(task_evidence):
            candidate = self._engine.understand(
                user_input.text,
                entities,
                self._world_model,
                language_document,
                context=understanding_context,
            )
            if arbitrate_dialog(
                task_evidence, complete_command_candidate(candidate.payload)
            ).kind is DecisionKind.SUPERSEDE_DIALOG:
                manager.replace_with_complete_command(user_input.conversation_id)
                active_task = None
                direct_understanding = candidate

        if active_dialog is not None or active_task is not None:
            normalized_meta = language_document.normalized_text.casefold()
            actor_id = conversation_user_id(user_input)
            asked = meta_questions(normalized_meta)
            is_meta_turn = bool(
                asked & {MetaQuestion.UNDERSTOOD, MetaQuestion.WHY_ASKING}
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
            if MetaQuestion.UNDERSTOOD in asked:
                response.async_set_speech(manager.understood(user_input.conversation_id))
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if MetaQuestion.WHY_ASKING in asked:
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
            and active_task.kind is DialogTaskKind.MONITOR_EVENT
        ):
            # "Überwache das Garagentor." -> "Wenn es offen ist." (7.9 W6):
            # the answer is read with the object as antecedent.  A complete
            # other request ends the open question without effect.
            combined = self._monitoring.open_monitor_text(user_input, active_task)
            if combined is not None:
                self._monitoring.cancel_open_monitor(user_input, active_task)
                if self._event_reading_claims(combined, entities):
                    return await self._async_handle_message_inner(
                        replace(user_input, text=combined), chat_log
                    )

        if active_dialog is None and active_task is not None and active_task.kind in {
            DialogTaskKind.MONITOR_LIST, DialogTaskKind.MONITOR_PART,
        }:
            # "Was macht die erste?" after the short list (7.9.1 A7); "In
            # welchem Zeitraum?" -> "Innerhalb von 10 Minuten." (A6): read
            # only as the asked part, the completed request runs again.
            answered = self._monitoring.answer_open_question(user_input, response, active_task)
            if isinstance(answered, str):
                return await self._async_handle_message_inner(replace(user_input, text=answered), chat_log)
            if answered is not None:
                return answered

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.MONITOR_DELETE
        ):
            handled = await self._monitoring.async_handle_monitor_delete(
                user_input, response, active_task
            )
            if handled is not None:
                return handled

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.UNUSUAL_OPT_IN
        ):
            handled = await self._monitoring.async_handle_unusual_opt_in(
                user_input, response, active_task
            )
            if handled is not None:
                return handled

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.MONITOR_CONFIRMATION
        ):
            handled = await self._monitoring.async_handle_monitor_confirmation(
                user_input, response, active_task
            )
            if handled is not None:
                return handled

        if (
            active_dialog is None
            and active_task is not None
            and active_task.kind is DialogTaskKind.RECURRENCE_CHOICE
        ):
            handled = self._automations.handle_recurrence_choice(
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
            handled = await self._routines.async_handle_task(
                user_input, response, active_task, entities
            )
            if handled is not None:
                return handled

        if language_document.utterance.modality in {Modality.IRREALIS, Modality.DELIBERATION}:
            # "Hätte ich doch …" / "Ich überlege, ob …" (7.7.1 A2): a past that
            # did not happen or thinking aloud is never an operation, whatever
            # router would read the verb.
            response.async_set_speech(render_non_executable(language_document.utterance.modality))
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
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
                await self._queries.async_explain_cause(cause_question.entity)
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
                routine_bindings=self._routines.bindings_for(conversation_user_id(user_input)),
            )
            # The direct command reading competes with need and question
            # (7.7, B3): routine ↔ scene name is decided here by evidence,
            # not by which interpreter runs first.
            if (
                direct_understanding is None
                and language_document.utterance.speech_act is SpeechAct.COMMAND
                and language_document.utterance.safe_to_execute_directly
                and not (pending is not None and contextual)
            ):
                direct_understanding = self._engine.understand(
                    user_input.text,
                    entities,
                    self._world_model,
                    language_document,
                    context=understanding_context,
                )
            # A reference to the conversation ("Und im Bad auch") and a
            # release ("Das Licht kann aus") are readings of their own.
            bound = (
                self._engine.understand_discourse(
                    language_document, entities, pending, understanding=understanding_context
                )
                if pending is not None and pending.pending_clarification is None
                else None
            )
            released = self._engine.understand_release(
                language_document, entities, context=understanding_context
            )
            decision = arbitrate(
                need_query_candidates(
                    view, need, language_document.utterance.speech_act.name,
                    parser=direct_understanding.payload if direct_understanding is not None else None,
                    discourse=bound, release=released,
                    deferred=is_deferred(language_document),
                    text=user_input.text, entities=entities,
                ),
                explicit_question=question_shaped,
            )
            if decision.kind is DecisionKind.DEFER:
                # Automation ↔ time-shifted command (7.7): a time-bound or
                # conditional meaning is compiled by the automation and
                # reminder contracts below, never run now.
                need = bound = released = None
            if decision.writes:
                # The arbiter decided which meaning applies; the other
                # readings are dropped. A parser meaning runs at the
                # direct-command step below with the same payload.
                source = decision.chosen[0].source
                need = need if source == "need" else None
                bound = bound if source == "discourse" else None
                released = released if source == "release" else None
            elif decision.kind is DecisionKind.ASK:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech(ambiguous_reading_text(decision))
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if decision.kind is DecisionKind.ANSWER and view is not None:
                return await self._devices.async_handle_match_result(
                    user_input,
                    response,
                    MatchResult(
                        plan=None, response_text=view.text, context_entities=view.entities
                    ),
                    entities,
                )
            if isinstance(need, CommandPlan):
                return await self._devices.async_handle_command_plan(
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
                return await self._devices.async_handle_match_result(
                    user_input, response, need, entities
                )
            if bound is not None:
                return await self._devices.async_handle_bound_result(
                    user_input, response, bound, entities
                )
            if isinstance(released, CommandPlan):
                return await self._devices.async_handle_command_plan(
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
                return await self._devices.async_clarify(user_input, response, released, entities)
            if released is not None:
                return await self._devices.async_handle_match_result(
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
                    await self._learning.async_confirm_alias_learning(draft, actor_id)
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
            procedure_result = await self._comfort.async_handle_procedure_turn(
                user_input, response, language_document, entities
            )
            if procedure_result is not None:
                return procedure_result
            routine_feedback_result = await self._learning.async_handle_routine_feedback_turn(
                user_input, response, language_document, entities
            )
            if routine_feedback_result is not None:
                return routine_feedback_result
            scenario = interpret_downstairs_shutdown(language_document, entities)
            if scenario is not None:
                return self._automations.handle_match_result(
                    user_input, response, scenario, entities
                )
            comfort_result = await self._comfort.async_handle_comfort_turn(
                user_input,
                response,
                language_document,
                entities,
                area_id=(conversation_area.area_id if conversation_area is not None else None),
            )
            if comfort_result is not None:
                return comfort_result
            document_result = await self._comfort.async_handle_document_turn(
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
            learning_result = await self._learning.async_handle_learning_turn(
                user_input, response, language_document
            )
            if learning_result is not None:
                return learning_result
            plan_result = await self._goals.async_handle_goal_turn(
                user_input, response, language_document, entities, direct_understanding,
                event_reading_claims=lambda: self._event_reading_claims(
                    user_input.text, entities
                ),
            )
            if plan_result is not None:
                return plan_result
            memory_result = await self._learning.async_handle_memory_turn(
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
                    await self._learning.async_confirm_alias_learning(
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
            return await self._devices.async_handle_undo_request(
                user_input, response, pending, entities
            )

        if is_explanation_request(user_input.text):
            return self._queries.handle_explanation_request(
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
            return await self._automations.async_handle_wizard(
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
            return await self._productivity.async_handle_pending(
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
            return await self._productivity.async_handle_calendar_event_turn(
                user_input, response, active_task.payload, calendars
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.CALENDAR_MUTATION
            and active_task is not None
            and isinstance(active_task.payload, PendingCalendarMutation)
        ):
            return await self._productivity.async_handle_calendar_mutation_confirmation(
                user_input, response, active_task.payload
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_CONFIRMATION
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationConfirmation)
        ):
            return await self._automations.async_handle_pending_confirmation_turn(
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
            return await self._management.async_handle_action_edit_turn(
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
            structure_turn = await self._management.async_handle_structure_edit_turn(
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
            return await self._management.async_handle_management_confirmation(
                user_input, response, active_task.payload
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_DELETION
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationDeletion)
        ):
            return await self._management.async_handle_deletion_confirmation_reply(
                user_input, response, active_task.payload
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.SERVICE_CONFIRMATION
            and active_task is not None
            and isinstance(active_task.payload, PendingServiceConfirmation)
        ):
            return await self._devices.async_handle_service_confirmation_reply(
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
            return await self._devices.async_handle_pending_semantic_command(
                user_input, response, active_task.payload, entities
            )

        if (
            active_dialog is not None
            and active_dialog.kind is PendingDialogKind.AUTOMATION_EVENT_CLARIFICATION
            and active_task is not None
            and isinstance(active_task.payload, PendingAutomationEventClarification)
        ):
            handled = self._automations.handle_pending_event_clarification(
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
            return await self._automations.async_handle_pending_draft(
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

        hidden = hidden_name_mentions(
            user_input.text,
            entities,
            hidden_entity_names(self.hass, {entity.entity_id for entity in entities}),
        )
        if hidden:
            # A device HomeIntent may not use is named: say so instead of
            # "gibt es nicht" or offering its side entities (7.6.1).
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(hidden_device_text(
                hidden,
                admin_hint=(
                    exposure_hint(self.entry)
                    if await user_is_admin(self.hass, user_input) else None
                ),
            ))
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        checked = embedded_check_question(user_input.text)
        if checked is not None:
            # "Prüfe, ob das Garagentor offen ist" (7.9.1 A7): a question
            # answered now, never an automation.
            return await self._async_handle_message_inner(replace(user_input, text=checked), chat_log)

        monitoring_request = parse_monitoring_management(user_input.text)
        if monitoring_request is not None:
            # "Welche Überwachungen laufen?", "Stopp die Fensterüberwachung"
            # (7.9 W8): automations and HomeIntent monitors, in plain words.
            return await self._monitoring.async_handle_management(
                user_input, response, monitoring_request, entities, dt_util.now()
            )

        routine_request = interpret_routine_binding(user_input.text, entities)
        if routine_request is not None:
            return await self._routines.async_handle_request(
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
            return self._automations.start_wizard(user_input, response)

        if re.search(
            r"\bwas\s+wurde\s+heute\s+(?:durch|von)\s+homeintent\s+ausgefuehrt\b",
            normalize_for_compare(user_input.text),
        ):
            return self._queries.handle_audit_query(user_input, response)

        # A trigger/notification request ("Benachrichtige mich, wenn der Akku
        # unter 20 Prozent fällt") shares words with read-only queries but is
        # never answered as one.
        automation_turn = language_document.utterance.speech_act is SpeechAct.AUTOMATION
        history_query = (
            None if automation_turn
            else parse_history_query(user_input.text, entities, dt_util.now())
        )
        if history_query is not None:
            return await self._queries.async_handle_history_query_result(
                user_input, response, history_query
            )

        advanced_answer = (
            None if automation_turn
            else match_advanced_query(user_input.text, entities, dt_util.now())
        )
        if advanced_answer is not None:
            return self._queries.handle_advanced_answer(user_input, response, advanced_answer)

        if not automation_turn and re.search(
            r"\b(?:alarm|alarmanlage|sicherung|scharf|unscharf)\b",
            user_input.text, re.IGNORECASE,
        ):
            alarm = match_alarm_control(
                user_input.text, entities,
                allow_disarm=await user_is_admin(self.hass, user_input),
            )
            if alarm is not None:
                return await self._devices.async_handle_device_control_result(
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
        # A management verb on a monitor or automation never reaches the
        # calendar, a list or a reminder (7.9.1 A5).
        managed_object = names_managed_object(user_input.text)
        management = None if managed_object else understand_management(
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
            return await self._productivity.async_handle_request(
                user_input, response, management.payload, entities
            )
        if management is not None and isinstance(
            management.payload, CalendarManagementRequest
        ):
            return await self._productivity.async_handle_calendar_management(
                user_input, response, management.payload, all_calendars
            )

        calendar_draft = None if managed_object else start_calendar_event_draft(
            user_input.text, calendars, dt_util.now()
        )
        if calendar_draft is not None:
            return self._productivity.handle_calendar_draft(
                user_input, response, calendar_draft, calendars
            )

        structure_edit = (
            None
            if is_action_edit_request(user_input.text)
            else parse_automation_structure_edit(user_input.text)
        )
        if structure_edit is not None:
            return await self._management.async_handle_structure_edit_request(
                user_input, response, structure_edit, entities
            )

        if is_action_edit_request(user_input.text):
            return await self._management.async_handle_action_edit_request(
                user_input, response, entities
            )

        management_request = parse_automation_management(user_input.text)
        if management_request is not None:
            return await self._management.async_handle_management(
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
            automations = await self._automation_store().async_list_automations()
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
                return await self._devices.async_handle_device_control_result(
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
                    return await self._notifications.async_handle_immediate(
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
                # Context connections (7.7, B3): every reader proposes, the
                # arbiter decides between writing readings; an answer or a
                # question keeps the established order.
                readings = (
                    ("followup", lambda: self._engine.match_followup(user_input.text, pending)),
                    ("property", lambda: self._engine.match_contextual_property_followup(
                        user_input.text, entities, pending
                    )),
                    ("reference", lambda: self._engine.match_reference(
                        user_input.text, entities, pending, self._world_model
                    )),
                    ("query", lambda: self._engine.match_query_followup(
                        user_input.text, entities, pending, self._world_model
                    )),
                    ("command", lambda: self._engine.match_command_followup(
                        user_input.text, entities, pending
                    )),
                )
                result = self._arbitrate_context_readings(
                    readings,
                    contract=(
                        ellipsis_fields(user_input.text, entities),
                        entities,
                        pending.last_entities if pending is not None else (),
                    ),
                )
                if isinstance(result, _TimedFollowup):
                    timed_text = timed_followup_text(result.payload, user_input.text, entities)
                    if timed_text is None:
                        response.async_set_speech(
                            "Mit der Zeitangabe kann ich den Folgeauftrag nicht sicher bilden. "
                            "Sag ihn bitte vollständig, zum Beispiel: Schalte das Flurlicht morgen um 7 Uhr ein."
                        )
                        return conversation.ConversationResult(
                            response=response, conversation_id=user_input.conversation_id
                        )
                    # The rebuilt sentence names its target itself; it is
                    # read without the context, so it cannot loop back here.
                    self._context_store.clear(user_input.conversation_id)
                    return await self._async_handle_message_inner(
                        replace(user_input, text=timed_text), chat_log
                    )
            if result is None and _AUTOMATION_DELETE_RE.search(user_input.text):
                # V5.28 "Automation Deletion": checked before the query gate
                # below - "Lösche die Automation für X" also contains the
                # word "Automation" and would otherwise trigger an
                # unnecessary second automations.yaml read via the query
                # gate first (that grammar just wouldn't match it). Same
                # lazily-constructed, entity-lifetime executor instance the
                # query/creation paths already use.
                automations = await self._automation_store().async_list_automations()
                result = self._engine.match_automation_delete(user_input.text, entities, automations)
            if result is None and _AUTOMATION_DISABLE_RE.search(user_input.text):
                # Wave 11 "Automation Disable/Enable": same "checked before
                # the query gate" reasoning as the delete gate above -
                # "Deaktiviere die Automation für X" also contains the word
                # "Automation".
                automations = await self._automation_store().async_list_automations()
                result = self._engine.match_automation_disable(user_input.text, entities, automations)
            if result is None and _AUTOMATION_ENABLE_RE.search(user_input.text):
                automations = await self._automation_store().async_list_automations()
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
                automations = await self._automation_store().async_list_automations()
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
            # 7.9.2 A2: an automation whose action named several devices.
            result = self._automations.action_ambiguity_question(user_input.text, entities)
        if result is None:
            return await self._devices.async_handle_no_match(user_input, response, entities)

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
            return await self._devices.async_handle_no_match(
                user_input, response, entities
            )

        if isinstance(result, CommandPlan):
            return await self._devices.async_handle_command_plan(
                user_input, response, result, entities
            )

        if isinstance(
            result, (AutomationMatchResult, AutomationDraftMatchResult, AutomationClarificationResult)
        ) and not await self._automations.async_may_create(user_input):
            # Refused before any preview, not after "Ja" (7.8 B5).
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Das Erstellen von Automationen ist nur für Administratoren erlaubt.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if isinstance(result, AutomationMatchResult):
            decided = self._automations.decide_recurrence(user_input, response, result, entities, pending)
            if isinstance(decided, conversation.ConversationResult):
                return decided
            return self._automations.handle_match_result(
                user_input, response, decided, entities
            )

        if isinstance(result, AutomationDraftMatchResult):
            return self._automations.handle_draft_match_result(
                user_input, response, result
            )

        if isinstance(result, MonitorProposalResult):
            return self._monitoring.stage_value_monitor(user_input, response, result)

        if isinstance(result, AutomationClarificationResult) and result.vague_situation:
            return self._monitoring.answer_unusual(user_input, response, self.entry.options)

        if isinstance(result, AutomationClarificationResult) and result.monitored_object:
            return self._monitoring.stage_open_monitor(
                user_input, response, result.monitored_object, result.response_text
            )

        if isinstance(result, AutomationClarificationResult):
            return self._automations.handle_clarification_result(
                user_input, response, result
            )

        if isinstance(result, AutomationDeletionMatchResult):
            return await self._management.async_handle_deletion_match_result(
                user_input, response, result
            )

        if isinstance(result, AutomationToggleMatchResult):
            return await self._management.async_handle_toggle_result(
                user_input, response, result
            )

        if result.clarification is not None:
            return await self._devices.async_clarify(user_input, response, result, entities)

        return await self._devices.async_handle_match_result(
            user_input, response, result, entities
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


    def _arbitrate_context_readings(
        self,
        readings: Sequence[tuple[str, Any]],
        contract: tuple[EllipsisFields, list[EntitySnapshot], Sequence[EntitySnapshot]] | None = None,
    ) -> Any:
        """Discourse connections as arbiter candidates (7.7, B3).

        Every writing reading must keep the ellipsis contract (7.7.1 A3): a
        newly named object, side or time is never replaced by the previous
        target, and the target set never widens.
        """
        payloads = [(source, read()) for source, read in readings]
        if contract is not None:
            fields, entities, previous = contract
            timed: Any = None
            unknown_object = False
            kept: list[tuple[str, Any]] = []
            for source, payload in payloads:
                written = _written_entities(payload, entities)
                reason = violation(fields, written, previous) if written else None
                if reason == "time" and timed is None:
                    timed = payload
                if reason == "object" and fields.unknown and not fields.targets:
                    unknown_object = True
                kept.append((source, None if reason else payload))
            payloads = kept
            if timed is not None and all(payload is None for _source, payload in payloads):
                return _TimedFollowup(timed)
            if unknown_object and all(payload is None for _source, payload in payloads):
                # "Und Deckenfluter aus." with an unknown Deckenfluter (7.8.1).
                return MatchResult(
                    plan=None,
                    response_text=(
                        f"Ein Gerät „{' '.join(fields.unknown)}“ finde ich nicht. "
                        "Ich habe nichts ausgeführt."
                    ),
                )
        candidates = context_candidates(payloads)
        decision = arbitrate(candidates)
        if decision.writes:
            return decision.chosen[0].payload
        if decision.kind is DecisionKind.ASK:
            return MatchResult(plan=None, response_text=ambiguous_reading_text(decision))
        return next((payload for _source, payload in payloads if payload is not None), None)


    def _event_reading_claims(self, text: str, entities: list[EntitySnapshot]) -> bool:
        """Routing rule for monitoring requests (7.8.3).

        One meaning, one source: when the sentence-based event reader
        understands a request completely or asks a targeted device question
        about it, the automation path owns it - never a second reading with
        a different meaning.  Structural: it asks the reader, it keeps no
        list of sentences or words for either side.
        """
        return self._engine.event_reading_kind(text, entities, self._world_model) in (
            OutcomeKind.AUTOMATION,
            OutcomeKind.CLARIFY,
            OutcomeKind.MONITOR,
        )

    def _automation_store(self) -> AutomationExecutor:
        """The one executor of automations.yaml (its lock guards every write)."""
        if self._automation_executor is None:
            self._automation_executor = AutomationExecutor(self.hass)
        return self._automation_executor

    def _record_execution(self, user_input, plan) -> None:
        context = getattr(user_input, "context", None)
        self._audit_trail.record(
            dt_util.now(), getattr(context, "user_id", None), plan
        )


