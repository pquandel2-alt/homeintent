"""Managing existing automations by voice (7.7, B4).

List, explain, pause, switch on/off, rename, change, reschedule and delete
HomeIntent's own automations. Every change needs an explicit "Ja" and
runs through ``AutomationExecutor`` (its lock guards automations.yaml).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from homeassistant.components import conversation
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..automation_action_edit import (
    action_edit_operation,
    homeintent_candidates,
    reordered_actions,
    select_candidate_reply,
)
from ..automation_executor import AutomationExecutor
from ..automation_ownership import async_management_refusal
from ..automation_management import (
    AutomationManagementKind,
    AutomationManagementRequest,
    format_scheduled_time,
    READ_ONLY_MANAGEMENT_KINDS,
    select_automation_management,
)
from ..automation_simulation import render_automation_simulation
from ..automation_structure_edit import (
    AutomationEditOperation,
    AutomationEditSection,
    AutomationStructureEditRequest,
)
from .automations import access_openings_for
from ..engine import AutomationDeletionMatchResult, AutomationToggleMatchResult, NluEngine
from ..entities import EntitySnapshot
from ..execution_context import user_facing_error
from ..nlu.automation_access import describe_access_refusal
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.context import (
    ConversationContext,
    ConversationContextStore,
    PendingAutomationActionEdit,
    PendingAutomationDeletion,
    PendingAutomationManagement,
    PendingAutomationStructureEdit,
)
from ..nlu.ha_automation_generator import (
    generate_ha_action_configs,
    generate_ha_condition_configs,
    generate_ha_trigger_configs,
)
from ..nlu.response_generator import _automation_label
from ..world_model import WorldModel

_LOGGER = logging.getLogger(__name__)


# V5 Teil 8/10 (V5.28, "Automation Deletion") - same "small closed
# vocabulary" precedent as the creation texts above, mirrored one-to-one for
# the deletion confirmation dialog.
AUTOMATION_DELETED_TEXT = "Automation wurde gelöscht."


AUTOMATION_DELETION_CANCELLED_TEXT = "Abgebrochen. Die Automation wurde nicht gelöscht."


AUTOMATION_DELETION_CONFIRMATION_UNCLEAR_TEXT = (
    "Das habe ich nicht verstanden. Soll die Automation gelöscht werden? "
    "Bitte antworte mit Ja oder Nein."
)


def _structure_label(section: AutomationEditSection) -> str:
    return "Auslöser" if section is AutomationEditSection.TRIGGERS else "Bedingungen"


class AutomationManagementController:
    """Handles management requests and their confirmations."""

    def __init__(
        self,
        *,
        context_store: ConversationContextStore,
        executor: Callable[[], AutomationExecutor],
        engine: NluEngine,
        world_model: Callable[[], WorldModel | None],
        hass: Callable[[], Any] | None = None,
    ) -> None:
        self._context_store = context_store
        self._executor = executor
        self._engine = engine
        self._world_model_of = world_model
        self._hass_of = hass

    async def _async_refusal(
        self, user_input: conversation.ConversationInput, automation: Any
    ) -> str | None:
        """Owner or administrator only (7.9.1 A2); ``None`` when allowed."""
        hass = self._hass_of() if self._hass_of is not None else None
        return await async_management_refusal(
            hass, user_input, getattr(automation, "owner_user_id", None)
        )

    def _refuse(
        self, user_input: conversation.ConversationInput, response: intent.IntentResponse, text: str
    ) -> conversation.ConversationResult:
        self._context_store.clear(user_input.conversation_id)
        response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    @property
    def _world_model(self) -> WorldModel | None:
        return self._world_model_of()

    def _automation_store(self) -> AutomationExecutor:
        return self._executor()

    async def async_handle_management(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: AutomationManagementRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Execute the bounded query/reschedule/cleanup management language."""
        self._context_store.clear(user_input.conversation_id)
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

        automations = await self._automation_store().async_list_automations()
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
                    live = self._automation_store().automation_runtime_info(
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

    async def async_handle_management_confirmation(
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
        request = pending.request
        if pending.automation is not None:
            refusal = await self._async_refusal(user_input, pending.automation)
            if refusal is not None:
                return self._refuse(user_input, response, refusal)
        try:
            if request.kind is AutomationManagementKind.CLEAN_EXPIRED:
                removed = await self._automation_store().async_cleanup_expired_scheduled_automations(dt_util.now())
                response.async_set_speech(
                    "Es waren keine abgelaufenen HomeIntent-Aufträge vorhanden."
                    if not removed else f"{len(removed)} abgelaufene HomeIntent-Aufträge wurden gelöscht."
                )
            elif request.kind is AutomationManagementKind.ROLLBACK:
                operation = await self._automation_store().async_rollback_last_change()
                response.async_set_speech(
                    f"Die letzte HomeIntent-Änderung ({operation}) wurde rückgängig gemacht."
                )
            elif request.kind is AutomationManagementKind.RESCHEDULE:
                assert pending.automation is not None and pending.target is not None
                await self._automation_store().async_reschedule_automation(
                    pending.automation.automation_id, pending.target
                )
                response.async_set_speech(
                    "Der Auftrag wurde auf " + format_scheduled_time(pending.target.isoformat()) + " verschoben."
                )
            elif request.kind is AutomationManagementKind.DUPLICATE:
                assert pending.automation is not None
                await self._automation_store().async_duplicate_automation(
                    pending.automation.automation_id
                )
                response.async_set_speech(
                    f"{_automation_label(pending.automation)} wurde dupliziert."
                )
            elif request.kind is AutomationManagementKind.PAUSE_UNTIL:
                assert pending.automation is not None and pending.target is not None
                await self._automation_store().async_pause_automation_until(
                    pending.automation.automation_id, pending.target
                )
                response.async_set_speech(
                    f"{_automation_label(pending.automation)} ist bis "
                    f"{format_scheduled_time(pending.target.isoformat())} pausiert."
                )
            else:
                assert pending.automation is not None
                assert request.max_runs is not None
                await self._automation_store().async_set_max_runs(
                    pending.automation.automation_id, request.max_runs
                )
                response.async_set_speech(
                    f"{_automation_label(pending.automation)} wird nur noch {request.max_runs} Mal ausgeführt."
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Automation management failed: %s", err, exc_info=True)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ändern der Automation: {user_facing_error(err)}",
            )
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def async_handle_deletion_confirmation_reply(
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

        refusal = await self._async_refusal(user_input, deletion.automation)
        if refusal is not None:
            return self._refuse(user_input, response, refusal)
        try:
            await self._automation_store().async_delete_automation(
                deletion.automation.automation_id
            )
        except Exception as err:  # noqa: BLE001 - a YAML write + service call can fail in ways beyond HomeAssistantError; must not propagate as "Unexpected error during intent recognition"
            _LOGGER.error("Automation deletion failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Löschen der Automation: {user_facing_error(err)}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        response.async_set_speech(AUTOMATION_DELETED_TEXT)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_toggle_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationToggleMatchResult,
    ) -> conversation.ConversationResult:
        """Apply an unambiguous enable/disable result immediately."""
        self._context_store.clear(user_input.conversation_id)
        if result.automation is not None:
            refusal = await self._async_refusal(user_input, result.automation)
            if refusal is not None:
                return self._refuse(user_input, response, refusal)
            try:
                if result.enable:
                    await self._automation_store().async_enable_automation(
                        result.automation.automation_id
                    )
                else:
                    await self._automation_store().async_disable_automation(
                        result.automation.automation_id
                    )
            except Exception as err:  # noqa: BLE001 - HA/YAML failures are heterogeneous
                verb = "Aktivieren" if result.enable else "Deaktivieren"
                _LOGGER.error("Automation %s failed: %s", verb.lower(), err)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim {verb} der Automation: {user_facing_error(err)}",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_deletion_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: AutomationDeletionMatchResult,
    ) -> conversation.ConversationResult:
        """Store an unambiguous deletion candidate for confirmation - only
        for its owner or an administrator (7.9.1 A2)."""
        if result.automation is not None:
            refusal = await self._async_refusal(user_input, result.automation)
            if refusal is not None:
                return self._refuse(user_input, response, refusal)
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

    async def async_handle_structure_edit_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: AutomationStructureEditRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Resolve and prepare a trigger or condition edit."""
        automations = await self._automation_store().async_list_automations()
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
        if len(candidates) == 1:
            refusal = await self._async_refusal(user_input, candidates[0])
            if refusal is not None:
                return self._refuse(user_input, response, refusal)
        pending_edit = PendingAutomationStructureEdit(
            request=request,
            candidates=candidates,
            automation=candidates[0] if len(candidates) == 1 else None,
        )
        if len(candidates) > 1:
            self.store_structure_edit(user_input.conversation_id, pending_edit
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
            return await self.async_prepare_structure_edit(
                user_input,
                response,
                pending_edit,
                entities,
                request.payload,
            )
        self.store_structure_edit(user_input.conversation_id, pending_edit)
        response.async_set_speech(
            "Wie soll der neue Auslöser lauten?"
            if request.section.name == "TRIGGERS"
            else "Welche Bedingung soll gelten?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_action_edit_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot] | None = None,
    ) -> conversation.ConversationResult:
        """Resolve an automation and begin its action-only edit dialog."""
        automations = await self._automation_store().async_list_automations()
        candidates = homeintent_candidates(automations, user_input.text, entities)
        operation = action_edit_operation(user_input.text)
        if not candidates:
            response.async_set_speech("Ich finde keine änderbare HomeIntent-Automation.")
        elif len(candidates) > 1:
            self.store_action_edit(user_input.conversation_id,
                PendingAutomationActionEdit(
                    candidates=candidates, operation=operation
                ),
            )
            response.async_set_speech(
                "Welche Automation meinst du? "
                + ", ".join(_automation_label(item) for item in candidates)
                + "."
            )
        elif (refusal := await self._async_refusal(user_input, next(iter(candidates)))) is not None:
            return self._refuse(user_input, response, refusal)
        else:
            automation = next(iter(candidates))
            reordered = reordered_actions(automation.actions, operation)
            if operation.startswith("reorder:") and reordered is None:
                response.async_set_speech(
                    "Diese Automation hat nicht genügend Aktionen für diese Reihenfolge."
                )
            else:
                self.store_action_edit(user_input.conversation_id,
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

    def store_action_edit(
        self, conversation_id: str, pending: PendingAutomationActionEdit
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_automation_action_edit=pending,
            ),
        )

    def store_structure_edit(
        self, conversation_id: str, pending: PendingAutomationStructureEdit
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_automation_structure_edit=pending,
            ),
        )

    async def async_prepare_structure_edit(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingAutomationStructureEdit,
        entities: list[EntitySnapshot],
        edit_text: str | None,
    ) -> conversation.ConversationResult:
        request: AutomationStructureEditRequest = pending.request
        automation = pending.automation
        assert automation is not None
        section = request.section
        existing = list(
            automation.triggers
            if section is AutomationEditSection.TRIGGERS
            else automation.conditions
        )
        if request.operation is AutomationEditOperation.CLEAR:
            rendered: list[Mapping[str, Any]] = []
            spoken_edit = "alle Bedingungen entfernen"
        elif request.operation is AutomationEditOperation.REMOVE:
            remove_index = request.index
            if remove_index is None and request.payload is not None:
                matching = [
                    index for index, value in enumerate(existing)
                    if value.get("condition") == request.payload
                ]
                if len(matching) != 1:
                    response.async_set_speech(
                        "Ich konnte nicht genau eine passende Bedingung bestimmen. "
                        "Bitte nenne ihre Nummer."
                    )
                    return conversation.ConversationResult(
                        response=response, conversation_id=user_input.conversation_id
                    )
                remove_index = matching[0]
            assert remove_index is not None
            if remove_index >= len(existing):
                response.async_set_speech(
                    f"Diese Automation hat nur {len(existing)} Bedingungen."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            rendered = [value for index, value in enumerate(existing) if index != remove_index]
            spoken_edit = f"Bedingung {remove_index + 1} entfernen"
        else:
            if not edit_text:
                self.store_structure_edit(user_input.conversation_id, pending)
                response.async_set_speech(
                    "Wie soll der neue Auslöser lauten?"
                    if section is AutomationEditSection.TRIGGERS
                    else "Welche Bedingung soll gelten?"
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if section is AutomationEditSection.TRIGGERS:
                parsed = self._engine.parse_automation_trigger(
                    edit_text, entities, self._world_model
                )
                new_values, error = (
                    generate_ha_trigger_configs((parsed,), entities)
                    if parsed is not None else (None, None)
                )
            else:
                parsed = self._engine.parse_automation_condition(
                    edit_text, entities, self._world_model
                )
                new_values, error = (
                    generate_ha_condition_configs((parsed,), entities)
                    if parsed is not None else (None, None)
                )
            if parsed is None or error is not None or new_values is None:
                response.async_set_speech(
                    f"Den neuen {_structure_label(section)} habe ich nicht eindeutig verstanden. "
                    "Bitte formuliere ihn als vollständigen Satz."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            rendered = [
                dict(value) for value in (
                [*existing, *new_values]
                if request.operation is AutomationEditOperation.ADD
                else new_values
                )
            ]
            spoken_edit = edit_text.strip()
        ready = PendingAutomationStructureEdit(
            request=request,
            candidates=pending.candidates,
            automation=automation,
            rendered=tuple(rendered),
            edit_text=spoken_edit,
            ready=True,
        )
        self.store_structure_edit(user_input.conversation_id, ready)
        response.async_set_speech(
            f"Vorschau für „{_automation_label(automation)}“: "
            f"{_structure_label(section)} vorher {len(existing)}, danach {len(rendered)}. "
            f"Änderung: {spoken_edit}. Soll ich das übernehmen?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_structure_edit_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingAutomationStructureEdit,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        if pending.ready:
            reply = classify_confirmation_reply(user_input.text)
            if reply is ConfirmationReply.UNCLEAR:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            elif reply is ConfirmationReply.NO:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech("Abgebrochen. Die Automation wurde nicht verändert.")
            else:
                assert pending.automation is not None
                refusal = await self._async_refusal(user_input, pending.automation)
                if refusal is not None:
                    return self._refuse(user_input, response, refusal)
                request: AutomationStructureEditRequest = pending.request
                try:
                    await self._automation_store().async_replace_automation_section(
                        pending.automation.automation_id,
                        "triggers" if request.section is AutomationEditSection.TRIGGERS else "conditions",
                        [dict(value) for value in pending.rendered],
                        (pending.automation.source_text or pending.automation.alias)
                        + " | Änderung: " + (pending.edit_text or ""),
                    )
                except Exception as err:  # noqa: BLE001
                    response.async_set_error(
                        intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                        f"Fehler beim Ändern der Automation: {user_facing_error(err)}",
                    )
                else:
                    response.async_set_speech(
                        f"Die {_structure_label(request.section)} wurden geändert."
                    )
                self._context_store.clear(user_input.conversation_id)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        automation = pending.automation
        if automation is None:
            automation = select_candidate_reply(user_input.text, pending.candidates)
            if automation is None and len(user_input.text.split()) > 3:
                # A complete new request, not a choice between the candidates:
                # drop the selection question instead of repeating it (F9).
                self._context_store.clear(user_input.conversation_id)
                return None
            if automation is None:
                response.async_set_speech(
                    "Das ist nicht eindeutig. Bitte nenne genau eine Automation oder ihre Nummer: "
                    + ", ".join(_automation_label(item) for item in pending.candidates) + "."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            pending = replace(pending, automation=automation)
            payload = pending.request.payload
            if payload or pending.request.operation in {
                AutomationEditOperation.CLEAR, AutomationEditOperation.REMOVE
            }:
                return await self.async_prepare_structure_edit(
                    user_input, response, pending, entities, payload
                )
            self.store_structure_edit(user_input.conversation_id, pending)
            response.async_set_speech(
                "Wie soll der neue Auslöser lauten?"
                if pending.request.section is AutomationEditSection.TRIGGERS
                else "Welche Bedingung soll gelten?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self.async_prepare_structure_edit(
            user_input, response, pending, entities, user_input.text
        )

    async def async_handle_action_edit_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingAutomationActionEdit,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Resolve automation, replacement action and final consent in stages."""
        reply = classify_confirmation_reply(user_input.text)
        if pending.rendered_actions:
            if reply is ConfirmationReply.NO:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_speech("Abgebrochen. Die Automation wurde nicht verändert.")
            elif reply is not ConfirmationReply.YES:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            else:
                assert pending.automation is not None and pending.action_text is not None
                refusal = await self._async_refusal(user_input, pending.automation)
                if refusal is not None:
                    return self._refuse(user_input, response, refusal)
                try:
                    await self._automation_store().async_replace_automation_actions(
                        pending.automation.automation_id,
                        [dict(value) for value in pending.rendered_actions],
                        (
                            (pending.automation.source_text or pending.automation.alias)
                            + " | Neue Aktion: "
                            + pending.action_text
                        ),
                    )
                except Exception as err:  # noqa: BLE001
                    response.async_set_error(
                        intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                        f"Fehler beim Ändern der Automation: {user_facing_error(err)}",
                    )
                else:
                    response.async_set_speech(
                        "Die Aktion wurde geändert. Auslöser und Bedingungen blieben unverändert."
                    )
                self._context_store.clear(user_input.conversation_id)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        automation = pending.automation
        if automation is None:
            automation = select_candidate_reply(user_input.text, pending.candidates)
            if automation is None:
                response.async_set_speech(
                    "Das ist nicht eindeutig. Bitte nenne genau eine dieser Automationen: "
                    + ", ".join(_automation_label(item) for item in pending.candidates)
                    + "."
                )
            else:
                reordered = reordered_actions(automation.actions, pending.operation)
                if pending.operation.startswith("reorder:") and reordered is None:
                    response.async_set_speech(
                        "Diese Automation hat nicht genügend Aktionen für diese Reihenfolge."
                    )
                    return conversation.ConversationResult(
                        response=response, conversation_id=user_input.conversation_id
                    )
                self.store_action_edit(user_input.conversation_id,
                    PendingAutomationActionEdit(
                        candidates=pending.candidates,
                        automation=automation,
                        rendered_actions=reordered or (),
                        action_text=user_input.text if reordered else None,
                        operation=pending.operation,
                    ),
                )
                response.async_set_speech(
                    (
                        "Soll ich die Reihenfolge der Aktionen wie gewünscht ändern?"
                        if reordered
                        else "Welche Aktion soll zusätzlich danach ausgeführt werden?"
                        if pending.operation == "add"
                        else "Was soll stattdessen passieren? Auslöser und Bedingungen bleiben unverändert."
                    )
                )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        actions = self._engine.parse_automation_actions(
            user_input.text, entities, self._world_model
        )
        if not actions:
            response.async_set_speech(
                "Die neue Aktion habe ich nicht eindeutig verstanden. Bitte nenne eine vollständige Geräteaktion."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        openings = access_openings_for(
            self._hass_of() if self._hass_of is not None else None, tuple(actions), entities
        )
        if openings:
            # An edit never sneaks an opening into an automation (7.9.1 A1).
            response.async_set_speech(
                f"{describe_access_refusal(openings)} Die Automation bleibt unverändert."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        rendered, error = generate_ha_action_configs(actions, entities)
        if error is not None or rendered is None:
            response.async_set_speech("Diese Aktion kann ich nicht sicher in Home Assistant abbilden.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        rendered_actions = list(rendered)
        if pending.operation == "add":
            existing_user_actions = [
                dict(action) for action in automation.actions
                if not (
                    isinstance(action, dict)
                    and (
                        "delay" in action
                        or action.get("action") in {
                            "homeintent.delete_automation",
                            "homeintent.record_automation_run",
                        }
                    )
                )
            ]
            rendered_actions = [*existing_user_actions, *rendered_actions]
        self.store_action_edit(user_input.conversation_id,
            PendingAutomationActionEdit(
                candidates=pending.candidates,
                automation=automation,
                rendered_actions=tuple(rendered_actions),
                action_text=user_input.text,
                operation=pending.operation,
            ),
        )
        response.async_set_speech(
            (
                f"Vorschau für „{_automation_label(automation)}“: Aktionen vorher "
                f"{len(automation.actions)}, danach {len(rendered_actions)}. "
                f"Soll ich „{user_input.text.strip()}“ zusätzlich ausführen?"
                if pending.operation == "add"
                else f"Soll ich bei „{_automation_label(automation)}“ nur die Aktion durch "
                f"„{user_input.text.strip()}“ ersetzen?"
            )
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
