"""Automations spoken by the user (7.7, B4: from ``conversation.py``).

Creating, confirming, editing, pausing and deleting HomeIntent's own
automations, the automation assistant and the "only today or every day?"
choice. Every write goes through ``AutomationExecutor`` after an explicit
confirmation; the management calls run in the turn's system context.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import replace
from typing import Any, Callable, Protocol

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..automation_executor import AutomationExecutor
from ..automation_grounding import looks_like_selection_reply
from ..automation_wizard import AutomationWizardStage, AutomationWizardState, parse_lifetime
from ..const import CONF_ALLOW_NON_ADMIN_AUTOMATIONS
from ..controllers.notifications import NotificationController
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..effect_graph import build_plan_effects, is_composite_entity
from ..engine import (
    AutomationClarificationResult,
    AutomationDraftMatchResult,
    AutomationMatchResult,
    NluEngine,
)
from ..entities import EntitySnapshot, normalize_for_compare
from ..execution_context import current_turn, user_facing_error
from ..execution_policy import validate_automation_action_targets
from ..execution_trace import record_execution
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.automation_model import (
    AutomationModel,
    CalendarReference,
    CalendarSchedule,
    resolve_pending_schedule,
    TriggerModel,
    TriggerType,
)
from ..missing_part import MissingPart, PartRequest
from ..device_health import parse_health_report, report_message
from ..nlu.action_model import ActionModel, ActionType, NotificationRecipient, NotificationRecipientKind
from ..nlu.condition_model import ConditionModel, ConditionNode, ConditionType
from ..nlu.automation_model import render_automation_tree
from ..automation_ownership import HOUSEHOLD_OWNER, shared_turn_text, turn_is_shared
from ..nlu.automation_access import (
    open_ended_irrigation,
    AccessOpening,
    access_openings,
    describe_access_refusal,
    notice_instead_of_opening,
)
from ..nlu.automation_preview import render_automation_preview
from ..notification_language import describe_event
from ..nlu.automation_validator import validate_automation
from ..nlu.context import (
    ConversationContext,
    ConversationContextStore,
    PendingAutomationConfirmation,
    PendingAutomationDraft,
    PendingAutomationEventClarification,
    PendingAutomationWizard,
)
from ..nlu.ha_automation_generator import (
    generate_ha_automation_config,
    GenerationError,
    resolve_automation_action_entity_ids,
)
from ..nlu.recurrence import (
    answer_recurrence,
    is_conditional,
    Recurrence,
    recurrence_of,
    trigger_kinds,
)
from ..nlu.word_cues import has_word
from ..security_control import conversation_user_id, user_is_admin
from ..service_call import ServiceCallPlan
from ..turn_outcome import TurnOutcomeKind, report_outcome
from ..world_model import WorldModel

_LOGGER = logging.getLogger(__name__)


_GENERIC_UNKNOWN_TARGET = "Ich habe die Aktion erkannt, aber kein eindeutig passendes"
# The interval of a repeated reminder ("jede Minute", "alle 5 Minuten").
_INTERVAL_HEADS = frozenset({"jede", "jeden", "jedes", "alle"})
_INTERVAL_UNITS = ("sekunde", "minute", "stunde", "viertelstunde")


def _without_repeat_interval(text: str) -> str:
    words = text.split()
    kept: list[str] = []
    index = 0
    while index < len(words):
        if words[index].casefold() in _INTERVAL_HEADS:
            for length in (2, 3):
                unit = words[index + length - 1].casefold().strip(",.") if index + length - 1 < len(words) else ""
                if unit.startswith(_INTERVAL_UNITS):
                    index += length
                    break
            else:
                kept.append(words[index])
                index += 1
            continue
        kept.append(words[index])
        index += 1
    return " ".join(kept)


# Words that mark an automation sentence's trigger (7.9.2 A2).
_TRIGGER_WORD_RE = re.compile(
    r"\b(?:wenn|sobald|falls|jeden|jede|jedes|täglich|taeglich|morgens|abends|nachts|"
    r"um\s+\d|bei\s+sonnen\w*|werktags|wochenends)\b",
    re.IGNORECASE,
)

class AutomationRuntime(Protocol):
    """The one runtime service this controller uses."""

    dialog_manager: Any


AUTOMATION_CANCELLED_TEXT = "Abgebrochen. Die Automation wurde nicht erstellt."


AUTOMATION_CONFIRMATION_UNCLEAR_TEXT = (
    "Das habe ich nicht verstanden. Soll die Automation erstellt werden? "
    "Bitte antworte mit Ja oder Nein."
)


# V5 Teil 7/10 (V5.23/V5.26) - the confirmation dialog's own fixed spoken
# replies, same "small closed vocabulary" precedent NOT_UNDERSTOOD_TEXT
# already sets in const.py.
AUTOMATION_CREATED_TEXT = "Automation wurde erstellt."


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


_NO_CONDITION_RE = re.compile(
    r"(?:keine|keins|nein\s*,?\s*keine|ohne|keine\s+(?:bedingung|bedingungen)|"
    r"ohne\s+(?:bedingung|bedingungen)|keine\s+weitere(?:n)?(?:\s+bedingung(?:en)?)?|"
    r"nichts|brauche\s+ich\s+nicht)"
)


def spoken_summary(preview: str) -> str:
    """The preview without its framing question."""
    text = preview.removeprefix("Automation erkannt: ")
    for question in ("Soll ich das so einrichten?", "Soll diese Automation erstellt werden?"):
        text = text.removesuffix(question)
    return text.strip()


def access_openings_for(
    hass: HomeAssistant | None,
    actions: tuple[Any, ...] | list[Any],
    entities: list[EntitySnapshot],
) -> tuple[AccessOpening, ...]:
    """Every access the automation ``actions`` would open (7.9.1 A1),
    including through the scripts and scenes they run (static effect
    graph). Shared by new automations and action edits. Without ``hass``
    only the direct actions are checked."""
    probe = AutomationModel(triggers=(), actions=tuple(actions))
    controlled_ids = resolve_automation_action_entity_ids(probe, entities)
    composite_ids = sorted(
        entity.entity_id for entity in entities
        if entity.entity_id in controlled_ids
        and is_composite_entity(entity.entity_id, entity.attributes)
    )
    effects = (
        build_plan_effects(hass, ServiceCallPlan("homeassistant", "turn_on", composite_ids))
        if composite_ids and hass is not None else None
    )
    roots: dict[str, str] = {}
    names = {entity.entity_id: entity.friendly_name for entity in entities}
    if effects is not None:
        for graph in effects.graphs:
            for effect in graph.effects:
                for entity_id in effect.entity_ids:
                    roots.setdefault(entity_id, names.get(graph.root, graph.root))
    return access_openings(
        probe.actions, entities, effects.effects if effects is not None else (), roots
    )


def start_now(model: AutomationModel) -> AutomationModel:
    """The same reminder, once, starting in a few seconds (7.9 W5)."""
    return replace(
        model,
        triggers=(TriggerModel(type=TriggerType.RELATIVE_TIME, relative_offset_seconds=5),),
        once=True,
        ask_start=False,
    )


class AutomationController:
    """Handles every automation turn and pending automation dialog."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        context_store: ConversationContextStore,
        engine: NluEngine,
        world_model: Callable[[], WorldModel | None],
        executor: Callable[[], AutomationExecutor],
        runtime: AutomationRuntime,
        notifications: NotificationController,
        record_execution: Callable[[Any, ServiceCallPlan], None],
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._context_store = context_store
        self._engine = engine
        self._world_model_of = world_model
        self._executor = executor
        self._runtime = runtime
        self._notifications = notifications
        self._record_execution = record_execution

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    @property
    def _world_model(self) -> WorldModel | None:
        return self._world_model_of()

    def _automation_store(self) -> AutomationExecutor:
        return self._executor()

    def access_openings_of(
        self, model: AutomationModel, entities: list[EntitySnapshot]
    ) -> tuple[AccessOpening, ...]:
        """Every access this automation would open (7.9.1 A1)."""
        return access_openings_for(self.hass, model.actions, entities)

    def _guard_access(
        self, model: AutomationModel, entities: list[EntitySnapshot]
    ) -> tuple[AutomationModel | None, str | None]:
        """``(model, None)`` when nothing opens an access; otherwise
        ``(offer, refusal)`` - the notification offered instead (or
        ``None`` when nothing sensible remains) and the spoken refusal."""
        openings = self.access_openings_of(model, entities)
        if not openings:
            return model, None
        offer = notice_instead_of_opening(model, entities)
        return offer, describe_access_refusal(openings)

    def offer_preview(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        model: AutomationModel,
        entities: list[EntitySnapshot],
        requested_by_user_id: str | None,
    ) -> None:
        """Store ``model`` for a "Ja" and speak its preview - the one place
        every confirmed automation passes before it may be offered.

        An automation that would open an access is never offered (7.9.1
        A1): HomeIntent says so and offers a notification instead.
        """
        endless = open_ended_irrigation(model.actions, entities)
        if endless:
            # 7.9.2 A2: an irrigation valve may open by itself, never
            # without its end - ask for the duration, nothing is stored.
            self._context_store.clear(user_input.conversation_id)
            names = " und ".join(f"„{item.name}“" for item in endless)
            question = (
                f"Wie lange soll {names} jeweils laufen? Eine Bewässerung ohne Ende lege ich nicht an."
            )
            self._runtime.dialog_manager.create(
                user_input.conversation_id,
                "monitor-part",
                DialogTaskKind.MONITOR_PART,
                DialogPriority.FOLLOWUP,
                reason="Eine Rückfrage nach der Dauer ist offen.",
                requested_by_user_id=conversation_user_id(user_input),
                payload=PartRequest(
                    MissingPart.DURATION, question, original_text=shared_turn_text() or user_input.text
                ),
            )
            response.async_set_speech(question)
            return
        offer, refusal = self._guard_access(model, entities)
        prefix = ""
        if refusal is not None:
            if offer is None:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech(
                    f"{refusal} Ich habe nichts angelegt. Ich kann dich stattdessen "
                    "benachrichtigen, dann entscheidest du selbst."
                )
                return
            offer, failure = self._notifications.materialize_recipients(offer, user_input, entities)
            if failure is not None:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech(f"{refusal} Ich habe nichts angelegt.")
                return
            model = offer
            prefix = f"{refusal} Stattdessen melde ich es dir, dann entscheidest du selbst. "
        shared = turn_is_shared(self.hass, self.entry.options, user_input)
        self._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_automation_confirmation=PendingAutomationConfirmation(
                    model=model,
                    requested_by_user_id=requested_by_user_id,
                    shared=shared,
                ),
            ),
        )
        preview = render_automation_preview(model, entities)
        if shared:
            # 7.9.2 A3: said before the "Ja", not discovered later.
            preview = preview.replace(
                " Soll ", " Sie gehört dem ganzen Haushalt (gemeinsam). Soll ", 1
            )
        response.async_set_speech(prefix + preview)

    def decide_recurrence(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationMatchResult,
        entities: list[EntitySnapshot],
        pending: ConversationContext | None,
    ) -> AutomationMatchResult | conversation.ConversationResult:
        """Once or recurring - never guessed (7.3.3, Q5)."""
        model = result.model
        if model.ask_start and result.validation_error is None:
            return self._decide_start(user_input, response, result, entities)
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
        if self.access_openings_of(model, entities) or open_ended_irrigation(model.actions, entities):
            # Refused (or turned into a notification), or the duration is
            # asked first (7.9.2 A2) by offer_preview -
            # nothing to ask about "nur heute oder jeden Tag" (7.9.1 A1).
            return result
        self._runtime.dialog_manager.create(
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

    def _decide_start(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationMatchResult,
        entities: list[EntitySnapshot],
    ) -> AutomationMatchResult | conversation.ConversationResult:
        """"Erinnere mich alle 10 Minuten, bis das Tor zu ist" (7.9 W5): only
        now, or every time the situation starts - asked, never guessed."""
        # "jede Minute" is the reminder's interval, not "jedes Mal" (7.9.2
        # A6): the same question as for "alle 5 Minuten".
        said = recurrence_of(_without_repeat_interval(user_input.text))
        if said is Recurrence.RECURRING:
            return replace(result, model=replace(result.model, ask_start=False))
        if said is Recurrence.ONCE:
            return replace(result, model=start_now(result.model))
        if self.access_openings_of(result.model, entities):
            return result
        self._runtime.dialog_manager.create(
            user_input.conversation_id,
            "recurrence-choice",
            DialogTaskKind.RECURRENCE_CHOICE,
            DialogPriority.SELECTION,
            reason="Ob eine Erinnerung nur jetzt oder jedes Mal gilt, rate ich nicht.",
            requested_by_user_id=conversation_user_id(user_input),
            payload=result,
        )
        trigger = result.model.triggers[0] if result.model.triggers else None
        phrase = describe_event(trigger, entities) if trigger is not None else None
        situation = f"jedes Mal, wenn {phrase.subordinate}" if phrase is not None else "jedes Mal"
        response.async_set_speech(f"Nur jetzt oder {situation}?")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def handle_recurrence_choice(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        manager = self._runtime.dialog_manager
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
        if result.model.ask_start:
            result = replace(
                result,
                model=start_now(result.model) if answer is Recurrence.ONCE
                else replace(result.model, ask_start=False),
            )
            result = replace(result, validation_error=validate_automation(result.model))
        elif answer is Recurrence.ONCE:
            result = replace(result, model=replace(result.model, max_runs=1))
        return self.handle_match_result(user_input, response, result, entities)


    async def async_may_create(self, user_input: Any) -> bool:
        """Whether this user may create automations at all (7.8 B5)."""
        if bool(self.entry.options.get(CONF_ALLOW_NON_ADMIN_AUTOMATIONS, True)):
            return True
        return await user_is_admin(self.hass, user_input)

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

        openings = self.access_openings_of(confirmation.model, entities)
        if openings:
            # Defense in depth (7.9.1 A1): whatever path stored this draft,
            # an automation that opens an access is never written.
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"{describe_access_refusal(openings)} Ich habe nichts angelegt.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if open_ended_irrigation(confirmation.model.actions, entities):
            # Defense in depth (7.9.2 A2): never an irrigation without end.
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Eine Bewässerung ohne Ende lege ich nicht an. Ich habe nichts angelegt.",
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

        materialized, failure = self._notifications.materialize_recipients(
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
        # The spoken preview becomes the automation's description: visible in
        # Home Assistant, and what "Welche Überwachungen laufen?" reads (7.9 W8).
        described = dict(generation_result.config)
        described.setdefault("description", spoken_summary(render_automation_preview(model, entities)))

        try:
            created_id = await self._automation_store().async_create_automation(
                described,
                automation_id=once_automation_id,
                scheduled_for=model.scheduled_for,
                once=model.once,
                max_runs=model.max_runs,
                owner_user_id=HOUSEHOLD_OWNER if confirmation.shared else current_user_id,
            )
        except Exception as err:  # noqa: BLE001 - a YAML write + service call can fail in ways beyond HomeAssistantError; must not propagate as "Unexpected error during intent recognition"
            _LOGGER.error("Automation creation failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Erstellen der Automation: {user_facing_error(err)}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        created_plan = ServiceCallPlan("homeintent", "create_automation", created_id, {})
        self._record_execution(user_input, created_plan)
        turn = current_turn()
        if turn is not None:
            # The turn that authorized the automation stays in the trace;
            # the reload itself ran in a user-less child context (7.6.1).
            record_execution(
                self.hass, context=turn.context, plan=created_plan, decision=None,
                user_id=turn.user_id, utterance=turn.utterance, origin=None,
                attended=True, now=dt_util.now(),
            )
        report_outcome(TurnOutcomeKind.EXECUTED)
        response.async_set_speech(AUTOMATION_CREATED_TEXT)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )


    def handle_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationMatchResult,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Store a valid automation preview, or report its validation error."""
        if result.validation_error is None:
            speaker_bound, failure = self._notifications.materialize_presence_speaker(result.model, user_input)
            if failure is None:
                speaker_bound, failure = self._notifications.materialize_presence_scope(
                    speaker_bound, entities
                )
            if failure is None:
                materialized, failure = self._notifications.materialize_recipients(
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
            self.offer_preview(
                user_input, response, materialized, entities, conversation_user_id(user_input)
            )
        else:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def handle_health_report(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """"Sag mir jeden Sonntag, welche Batterien unter 30 % sind" (7.9.2
        B3): a recurring report - preview and "Ja", the time asked if
        missing. Never answered right away (the schedule would be lost)."""
        report = parse_health_report(user_input.text)
        if report is None:
            return None
        if report.hour is None:
            question = "Um wie viel Uhr soll ich dir den Bericht schicken? Sag zum Beispiel: „um 10 Uhr“."
            self._runtime.dialog_manager.create(
                user_input.conversation_id,
                "monitor-part",
                DialogTaskKind.MONITOR_PART,
                DialogPriority.FOLLOWUP,
                reason="Eine Rückfrage nach der Uhrzeit ist offen.",
                requested_by_user_id=conversation_user_id(user_input),
                payload=PartRequest(MissingPart.CLOCK, question, original_text=user_input.text),
            )
            response.async_set_speech(question)
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        message = report_message(report, entities)
        if message is None:
            what = "Batteriesensor" if report.kind == "battery" else "freigegebenes Gerät"
            response.async_set_speech(f"Ich sehe kein {what}; einen Bericht darüber kann ich nicht einrichten.")
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        conditions = (
            (ConditionNode(condition=ConditionModel(type=ConditionType.WEEKDAY, weekdays=report.weekdays)),)
            if report.weekdays else ()
        )
        model = AutomationModel(
            triggers=(TriggerModel(type=TriggerType.TIME, time_hour=report.hour, time_minute=report.minute),),
            conditions=conditions,
            actions=(ActionModel(
                type=ActionType.NOTIFY, message=message[0], message_template=message[1],
                recipient=NotificationRecipient(NotificationRecipientKind.CURRENT_USER),
            ),),
            source_text=user_input.text,
        )
        error = validate_automation(model)
        return self.handle_match_result(
            user_input, response, AutomationMatchResult(model, render_automation_tree(model), error), entities
        )

    def action_ambiguity_question(
        self, text: str, entities: list[EntitySnapshot]
    ) -> AutomationClarificationResult | None:
        """An automation whose action named several devices of one kind
        asks which one (7.9.2 A2) - only when the sentence has a trigger,
        never for a plain command, and only where the device path has
        nothing better to say than "kein eindeutig passendes Gerät".
        Answered as the one missing device."""
        ambiguity = self._engine.take_action_ambiguity()
        if ambiguity is None or not ambiguity.kind_words or not _TRIGGER_WORD_RE.search(text):
            return None
        feedback = self._engine.failure_feedback(text, entities)
        if feedback is not None and not feedback.startswith(_GENERIC_UNKNOWN_TARGET):
            return None
        return AutomationClarificationResult(
            response_text=ambiguity.question,
            part=PartRequest(
                MissingPart.DEVICE, ambiguity.question,
                replaces=ambiguity.kind_words, choices=ambiguity.choices,
            ),
        )

    def handle_clarification_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationClarificationResult,
    ) -> conversation.ConversationResult:
        """Ask the one open question of an automation draft - nothing runs.

        A device choice keeps the draft; the answer ("Die linke.")
        continues exactly this automation and nothing else. A question for
        one missing part ("In welchem Zeitraum?") opens a typed dialog
        whose answer is read only as that part (7.9.1 A6).
        """
        if result.clarification is None and result.part is not None:
            self._context_store.clear(user_input.conversation_id)
            self._runtime.dialog_manager.create(
                user_input.conversation_id,
                "monitor-part",
                DialogTaskKind.MONITOR_PART,
                DialogPriority.FOLLOWUP,
                reason=f"Eine Rückfrage nach dem {result.part.spoken_part} ist offen.",
                requested_by_user_id=conversation_user_id(user_input),
                payload=replace(result.part, original_text=shared_turn_text() or user_input.text),
            )
            response.async_set_speech(result.response_text)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
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

    def handle_pending_event_clarification(
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
            return self.handle_clarification_result(user_input, response, result)
        if not isinstance(result, AutomationMatchResult):
            # A device answer never turns a draft into a HomeIntent monitor.
            self._context_store.clear(user_input.conversation_id)
            return None
        return self.handle_match_result(user_input, response, result, entities)

    def handle_draft_match_result(
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


    async def async_handle_pending_confirmation_turn(
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
            self.offer_preview(
                user_input, response, revised.model, entities, pending.requested_by_user_id
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self._async_handle_automation_confirmation_reply(
            user_input, response, pending, entities
        )

    async def async_handle_pending_draft(
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
            self.offer_preview(
                user_input, response, completed.model, entities, conversation_user_id(user_input)
            )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def start_wizard(
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

    async def async_handle_wizard(
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
            self.offer_preview(user_input, response, model, entities, conversation_user_id(user_input))
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )


