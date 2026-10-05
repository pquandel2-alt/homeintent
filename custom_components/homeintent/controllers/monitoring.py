"""Monitoring requests HomeIntent runs itself, and their dialogs (7.9).

The meaning of a monitoring request comes from the sentence-based event
reader (7.8.3).  Where Home Assistant cannot express it without new helpers
(a change by an amount within a window, W3), HomeIntent's own monitor
runtime runs it - staged here for an explicit "Ja" exactly like an
automation preview, never stored before.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from homeassistant.components import conversation
from homeassistant.helpers import intent

from ..automation_results import MonitorProposalResult
from ..const import (
    CONF_PROACTIVE_APPLIANCE_ENTITIES,
    CONF_PROACTIVE_ENTRY_OPEN_MINUTES,
    CONF_PUSH_PROACTIVE_ENABLED,
)
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..goal_model import (
    DeliveryChannel,
    GoalKind,
    GoalLifecycle,
    GoalModel,
    GoalProvenance,
    GoalTrigger,
    NotificationSeverity,
)
from ..automation_metadata_store import CREATED_BY_HOMEINTENT
from ..entities import EntitySnapshot, normalize_for_compare
from ..monitor_goal import MonitorRecord, rate_rule_of
from ..monitoring_management import MonitoringOperation, MonitoringRequest
from ..rate_monitor import describe_rule
from ..nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from ..proactive_model import SituationKind
from ..automation_ownership import async_management_refusal, async_owner_name, may_manage
from ..missing_part import MissingPart, PartRequest, complete_request, read_part_answer
from ..security_control import conversation_user_id, user_is_admin
from ..turn_outcome import TurnOutcomeKind, report_outcome
from ..user_context import BindingStatus


# "bis gestern um 7 Uhr" etc.: the stale time is replaced by the answer.
_CLOCK_TAIL_RE = re.compile(r"\s+bis\b.*$", re.IGNORECASE)


class MonitoringController:
    """Stages and confirms HomeIntent-run monitors."""

    def __init__(
        self,
        *,
        runtime: Any,
        automation_store: Callable[[], Any] | None = None,
        hass: Callable[[], Any] | None = None,
    ) -> None:
        self._runtime = runtime
        self._automation_store = automation_store
        self._hass_of = hass

    @property
    def _hass(self) -> Any:
        return self._hass_of() if self._hass_of is not None else None

    def stage_value_monitor(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MonitorProposalResult,
    ) -> conversation.ConversationResult:
        """A change by an amount (7.9 W3): HomeIntent's own monitor, staged for
        an explicit "Ja" exactly like an automation preview."""
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        contexts = self._runtime.user_contexts
        binding = contexts.resolve_current_person(actor_id) if contexts is not None else None
        if binding is None or binding.status is not BindingStatus.RESOLVED or binding.person_entity_id is None:
            response.async_set_speech(
                "Ich weiß noch nicht, welche Person du bist. Bitte ordne deinem "
                "HomeIntent-Benutzer eine Person zu, dann kann ich dir solche Meldungen schicken."
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        assert contexts is not None
        targets = contexts.resolve_notification_targets(binding.person_entity_id)
        if targets.status is not BindingStatus.RESOLVED:
            response.async_set_speech(
                # A statement, not a question: the answer is a setting, not a
                # reply in this conversation (7.9.1 A6).
                "Für dich sind mehrere Push-Geräte bestätigt. Lege im Learning Center fest, "
                "welches ich für Meldungen nehmen soll; ich habe nichts eingerichtet."
                if targets.status is BindingStatus.AMBIGUOUS
                else "Für dich ist noch kein bestätigtes Push-Ziel konfiguriert."
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if self._runtime.monitor_goals is None:
            response.async_set_speech("Die lokale Goal-Persistenz ist nicht verfügbar.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        rule = result.proposal.rule
        goal = GoalModel(
            GoalKind.MONITOR_AND_NOTIFY,
            goal_id=f"value-change-{uuid.uuid4().hex[:12]}",
            trigger=GoalTrigger(
                "value_change", entity_id=rule.entity_id, delta=rule.delta, unit=rule.unit,
                direction=rule.direction.value, window_seconds=rule.window_seconds,
            ),
            recipient_person_ids=(binding.person_entity_id,),
            delivery_channel=DeliveryChannel.PUSH,
            notification_severity=NotificationSeverity.WARNING,
            provenance=GoalProvenance(
                source_utterance=user_input.text, user_id=actor_id, conversation_id=conversation_id,
            ),
            lifecycle=GoalLifecycle.MONITOR,
        )
        self._runtime.dialog_manager.create(
            conversation_id,
            "monitor-confirmation",
            DialogTaskKind.MONITOR_CONFIRMATION,
            DialogPriority.CONFIRMATION,
            reason="Eine Überwachung wartet auf ausdrückliche Bestätigung.",
            requested_by_user_id=actor_id,
            payload=(goal, result.proposal),
        )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def async_handle_monitor_confirmation(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: object,
    ) -> conversation.ConversationResult | None:
        """"Ja" stores the monitor, "Nein" discards it; nothing before."""
        manager = self._runtime.dialog_manager
        conversation_id = user_input.conversation_id
        payload = getattr(task, "payload", None)
        task_id = getattr(task, "task_id", "")
        if not (isinstance(payload, tuple) and len(payload) == 2 and isinstance(payload[0], GoalModel)):
            return None
        goal, proposal = payload
        if getattr(task, "requested_by_user_id", None) not in {None, conversation_user_id(user_input)}:
            response.async_set_speech("Diese Bestätigung gehört zu einem anderen Benutzer.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                # Another complete request ends the open question, without effect.
                manager.cancel(conversation_id, task_id)
                return None
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        manager.cancel(conversation_id, task_id)
        if reply is ConfirmationReply.NO:
            response.async_set_speech("In Ordnung, ich richte nichts ein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        store = self._runtime.monitor_goals
        if store is None:
            response.async_set_speech("Die lokale Goal-Persistenz ist nicht verfügbar.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        confirmed = replace(goal, provenance=replace(goal.provenance, confirmed=True))
        window = goal.trigger.window_seconds if goal.trigger is not None else None
        await store.async_save(MonitorRecord(confirmed, cooldown_seconds=window or 300))
        report_outcome(TurnOutcomeKind.EXECUTED)
        response.async_set_speech(
            f"Eingerichtet. Ich überwache {proposal.subject} selbst und melde mich, sobald die "
            "Änderung eintritt."
        )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def async_handle_management(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: MonitoringRequest,
        entities: list[EntitySnapshot],
        now: datetime,
    ) -> conversation.ConversationResult:
        """"Welche Überwachungen laufen?", "Stopp die Fensterüberwachung",
        "Pausiere die Garagen-Meldung bis morgen um 7 Uhr" (7.9 W8)."""
        conversation_id = user_input.conversation_id
        store = self._automation_store() if self._automation_store is not None else None
        automations = await store.async_list_automations() if store is not None else ()
        goals = self._runtime.monitor_goals
        records = await goals.async_load() if goals is not None else ()
        monitors = collect_monitors(automations, records, entities)
        is_admin = await user_is_admin(self._hass, user_input)
        actor = conversation_user_id(user_input)
        if request.operation is MonitoringOperation.LIST and not is_admin:
            # Non-administrators see their own monitors (7.9.1 A2).
            monitors = [item for item in monitors if may_manage(item.owner_user_id, actor, False)]
        chosen = matching(monitors, request.subject, entities)
        said = request.spoken_subject or request.subject
        spoken_subject = f" für „{said}“" if said else ""

        def say(text: str, query: bool = False) -> conversation.ConversationResult:
            if query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
            response.async_set_speech(text)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if request.operation is MonitoringOperation.ASK:
            # "Beobachtest du das Garagentor?" - from the list (7.9.1 A7).
            if not chosen:
                watched = f"„{said}“" if said else "das"
                return say(f"Nein, {watched} überwache ich gerade nicht.", query=True)
            running = [item for item in chosen if item.enabled]
            if not running:
                return say(
                    f"Nein, gerade nicht: {_count_word(len(chosen), 'Überwachung')}"
                    f"{spoken_subject} ist ausgeschaltet.", query=True,
                )
            return say(
                "Ja: " + "; ".join(short_label(item) for item in running) + ".", query=True
            )
        if request.operation is MonitoringOperation.LIST:
            if not chosen:
                return say(f"Gerade läuft keine Überwachung{spoken_subject}.", query=True)
            owners: dict[str | None, str] = {}
            if is_admin:
                for item in chosen:
                    if item.owner_user_id not in owners:
                        owners[item.owner_user_id] = (
                            await async_owner_name(self._hass, item.owner_user_id) or "ohne Eigentümer"
                        )
            # Spoken short form (7.9.1 A7): what each one watches; the full
            # preview only when asked ("Was macht die erste?").
            parts = [
                short_label(item)
                + (f" (von {owners[item.owner_user_id]})" if is_admin and item.owner_user_id != actor else "")
                + ("" if item.enabled else " (ausgeschaltet)")
                for item in chosen
            ]
            head = "Es läuft eine Überwachung" if len(chosen) == 1 else f"Es laufen {len(chosen)} Überwachungen"
            self._runtime.dialog_manager.create(
                conversation_id,
                "monitor-list",
                DialogTaskKind.MONITOR_LIST,
                DialogPriority.FOLLOWUP,
                reason="Die Liste der Überwachungen kann genauer erklärt werden.",
                requested_by_user_id=actor,
                payload=tuple(chosen),
            )
            return say(f"{head}: " + "; ".join(parts) + ".", query=True)
        if not chosen:
            return say(f"Ich finde keine Überwachung{spoken_subject}.")
        if len(chosen) > 1:
            return say(
                f"Das passt zu {len(chosen)} Überwachungen: "
                + "; ".join(f"„{item.label.rstrip('.')}“" for item in chosen[:4])
                + ". Welche meinst du? Nenne sie bitte genauer."
            )
        monitor = chosen[0]
        refusal = await async_management_refusal(
            self._hass, user_input, monitor.owner_user_id, noun="Überwachung"
        )
        if refusal is not None:
            return say(refusal)
        if request.operation is MonitoringOperation.STOP:
            if monitor.automation_id is not None and store is not None:
                await store.async_disable_automation(monitor.automation_id)
            elif monitor.goal_id is not None and goals is not None:
                record = next(item for item in records if item.goal.goal_id == monitor.goal_id)
                await goals.async_save(replace(record, enabled=False))
            report_outcome(TurnOutcomeKind.EXECUTED)
            return say(
                f"Ausgeschaltet: „{monitor.label.rstrip('.')}“. Sie bleibt gespeichert, bis du sie löschst."
            )
        if request.operation is MonitoringOperation.DELETE:
            self._runtime.dialog_manager.create(
                conversation_id,
                "monitor-delete",
                DialogTaskKind.MONITOR_DELETE,
                DialogPriority.CONFIRMATION,
                reason="Das Löschen einer Überwachung wartet auf Bestätigung.",
                requested_by_user_id=conversation_user_id(user_input),
                payload=monitor,
            )
            return say(f"Soll ich die Überwachung „{monitor.label.rstrip('.')}“ löschen?")
        # PAUSE
        if request.hour is None or request.day_offset is None:
            return say(self._ask_until(user_input, "Bis wann? Sag zum Beispiel: „bis morgen um 7 Uhr“."))
        if monitor.automation_id is None or store is None:
            return say(
                "Überwachungen, die ich selbst ausführe, kann ich ausschalten, aber nicht zeitlich "
                "pausieren."
            )
        resume = (now + timedelta(days=request.day_offset)).replace(
            hour=request.hour, minute=request.minute, second=0, microsecond=0
        )
        if resume <= now:
            return say(self._ask_until(
                user_input, "Dieser Zeitpunkt liegt schon in der Vergangenheit. Bis wann soll ich pausieren?"
            ))
        await store.async_pause_automation_until(monitor.automation_id, resume)
        report_outcome(TurnOutcomeKind.EXECUTED)
        day = "morgen" if request.day_offset == 1 else "heute" if request.day_offset == 0 else resume.strftime("%d.%m.")
        return say(
            f"Pausiert bis {day} um {resume:%H:%M} Uhr: „{monitor.label.rstrip('.')}“. Danach schalte ich "
            "sie automatisch wieder ein."
        )

    def answer_open_question(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
    ) -> conversation.ConversationResult | str | None:
        """The turn after a monitor list or a one-part question: a result,
        the completed request text to run again, or ``None`` (not consumed)."""
        manager = self._runtime.dialog_manager
        if task.kind is DialogTaskKind.MONITOR_LIST:
            manager.cancel(user_input.conversation_id, task.task_id)
            return self.answer_list_detail(user_input, response, task)
        request = getattr(task, "payload", None)
        if not isinstance(request, PartRequest):
            return None
        if task.requested_by_user_id not in {None, conversation_user_id(user_input)}:
            return None
        phrase = read_part_answer(request.part, user_input.text)
        if phrase is None:
            if len(user_input.text.split()) <= 3:
                # A short reply that is no such part: ask again, never guess.
                response.async_set_speech(
                    f"Das habe ich nicht als {request.spoken_part} verstanden. {request.question}"
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            # A complete new request ends the question without effect.
            manager.cancel(user_input.conversation_id, task.task_id)
            return None
        manager.cancel(user_input.conversation_id, task.task_id)
        return complete_request(request, phrase)

    def answer_list_detail(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
    ) -> conversation.ConversationResult | None:
        """"Was genau macht die erste?" after the short list (7.9.1 A7)."""
        monitors = getattr(task, "payload", None)
        if not isinstance(monitors, tuple) or not monitors:
            return None
        if getattr(task, "requested_by_user_id", None) not in {None, conversation_user_id(user_input)}:
            return None
        index = ordinal_index(user_input.text, len(monitors))
        if index is None:
            return None
        monitor = monitors[index]
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(monitor.label.rstrip(".") + ".")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    def _ask_until(self, user_input: conversation.ConversationInput, question: str) -> str:
        """"Bis wann?" opens a typed dialog: the answer is the time (7.9.1 A6)."""
        original = _CLOCK_TAIL_RE.sub("", user_input.text).strip().rstrip(".!?")
        self._runtime.dialog_manager.create(
            user_input.conversation_id,
            "monitor-part",
            DialogTaskKind.MONITOR_PART,
            DialogPriority.FOLLOWUP,
            reason="Eine Rückfrage nach der Uhrzeit ist offen.",
            requested_by_user_id=conversation_user_id(user_input),
            payload=PartRequest(MissingPart.UNTIL, question, original_text=f"{original}."),
        )
        return question

    async def async_handle_monitor_delete(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
    ) -> conversation.ConversationResult | None:
        monitor = getattr(task, "payload", None)
        if not isinstance(monitor, Monitor):
            return None
        conversation_id = user_input.conversation_id
        if getattr(task, "requested_by_user_id", None) != conversation_user_id(user_input):
            return None
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                self._runtime.dialog_manager.cancel(conversation_id, getattr(task, "task_id", ""))
                return None
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        self._runtime.dialog_manager.cancel(conversation_id, getattr(task, "task_id", ""))
        if reply is ConfirmationReply.NO:
            response.async_set_speech("In Ordnung, die Überwachung bleibt.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        refusal = await async_management_refusal(
            self._hass, user_input, monitor.owner_user_id, noun="Überwachung"
        )
        if refusal is not None:
            response.async_set_speech(refusal)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if monitor.automation_id is not None and self._automation_store is not None:
            await self._automation_store().async_delete_automation(monitor.automation_id)
        elif monitor.goal_id is not None and self._runtime.monitor_goals is not None:
            await self._runtime.monitor_goals.async_delete(monitor.goal_id)
        report_outcome(TurnOutcomeKind.EXECUTED)
        response.async_set_speech(f"Gelöscht: „{monitor.label.rstrip('.')}“.")
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    def stage_open_monitor(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        words: tuple[str, ...],
        question: str,
    ) -> conversation.ConversationResult:
        """"Überwache das Garagentor." (7.9 W6): remember the object for the
        next turn of the same user in this conversation, and ask."""
        self._runtime.dialog_manager.create(
            user_input.conversation_id,
            "monitor-event",
            DialogTaskKind.MONITOR_EVENT,
            DialogPriority.FOLLOWUP,
            reason="Ein Überwachungsauftrag wartet darauf, wann ich mich melden soll.",
            requested_by_user_id=conversation_user_id(user_input),
            payload=words,
        )
        response.async_set_speech(question)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def open_monitor_text(self, user_input: conversation.ConversationInput, task: Any) -> str | None:
        """The answer read as the rest of the open request, or ``None`` when
        the task belongs to someone else."""
        words = getattr(task, "payload", None)
        if not isinstance(words, tuple) or not words:
            return None
        if getattr(task, "requested_by_user_id", None) not in {None, conversation_user_id(user_input)}:
            return None
        answer = user_input.text.strip().rstrip(".!?").strip()
        if not answer:
            return None
        head = f"Überwache {' '.join(str(word) for word in words)} und"
        lowered = answer[:1].casefold() + answer[1:]
        if lowered.split()[0] in {"wenn", "sobald", "falls", "ob"}:
            if "," in answer:
                return f"{head} {lowered}."
            return f"{head} melde dich, {lowered}."
        return f"{head} {lowered}."

    def cancel_open_monitor(self, user_input: conversation.ConversationInput, task: Any) -> None:
        self._runtime.dialog_manager.cancel(user_input.conversation_id, getattr(task, "task_id", ""))

    # --- "etwas Ungewöhnliches" (7.9 W7) -------------------------------------------

    def answer_unusual(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        options: Mapping[str, object],
    ) -> conversation.ConversationResult:
        """What "ungewöhnlich" can mean here: exactly the situations the
        proactive detection (V12) knows - listed, with its state for this
        user; an offer to switch muted ones back on.  Nothing invented."""
        conversation_id = user_input.conversation_id
        kinds = catalog(options)
        listed = "; ".join(label for _kind, label in kinds)
        proactive = self._runtime.proactive_context
        enabled = proactive is not None and proactive.enabled
        if not enabled or not bool(options.get(CONF_PUSH_PROACTIVE_ENABLED, True)):
            response.async_set_speech(
                "„Ungewöhnlich“ kann ich nur so verstehen, wie es meine Situationserkennung kennt: "
                f"{listed}. Die proaktive Erkennung ist gerade ausgeschaltet. Ein Administrator kann "
                "sie unter Einstellungen → Geräte & Dienste → HomeIntent → Konfigurieren einschalten "
                "(„Proaktive Hinweise“ und „Push-Hinweise“). Etwas Bestimmtes überwache ich sofort, "
                "zum Beispiel: „Melde dich, wenn nachts die Haustür aufgeht.“"
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        user_id = conversation_user_id(user_input)
        assert proactive is not None
        attention = proactive.engine.attention_state
        muted = [(kind, label) for kind, label in kinds if attention.is_muted(user_id, kind)]
        if muted and user_id is not None:
            self._runtime.dialog_manager.create(
                conversation_id,
                "unusual-opt-in",
                DialogTaskKind.UNUSUAL_OPT_IN,
                DialogPriority.CONFIRMATION,
                reason="Stummgeschaltete Hinweise warten auf ausdrückliche Bestätigung.",
                requested_by_user_id=user_id,
                payload=tuple(kind for kind, _label in muted),
            )
            response.async_set_speech(
                f"Als ungewöhnlich erkenne ich: {listed}. Für dich stummgeschaltet: "
                + "; ".join(label for _kind, label in muted)
                + ". Soll ich diese Hinweise für dich wieder einschalten?"
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        response.async_set_speech(
            f"Als ungewöhnlich erkenne ich: {listed}. Das ist für dich eingeschaltet – "
            "ich melde mich, wenn so etwas passiert. Etwas Bestimmtes überwache ich zusätzlich, "
            "zum Beispiel: „Melde dich, wenn nachts die Haustür aufgeht.“"
        )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def async_handle_unusual_opt_in(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
    ) -> conversation.ConversationResult | None:
        conversation_id = user_input.conversation_id
        kinds = getattr(task, "payload", None)
        if not isinstance(kinds, tuple):
            return None
        if getattr(task, "requested_by_user_id", None) != conversation_user_id(user_input):
            return None
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                self._runtime.dialog_manager.cancel(conversation_id, getattr(task, "task_id", ""))
                return None
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        self._runtime.dialog_manager.cancel(conversation_id, getattr(task, "task_id", ""))
        proactive = self._runtime.proactive_context
        if reply is ConfirmationReply.NO or proactive is None:
            response.async_set_speech("In Ordnung, es bleibt, wie es ist.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        user_id = conversation_user_id(user_input)
        assert user_id is not None
        for kind in kinds:
            proactive.engine.attention_state.unmute(user_id, kind)
        await proactive.engine.async_persist()
        response.async_set_speech("Eingeschaltet. Solche Hinweise bekommst du wieder.")
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)


def catalog(options: Mapping[str, object]) -> list[tuple[SituationKind, str]]:
    """The situations V12 really detects, in plain words (7.9 W7)."""
    minutes = options.get(CONF_PROACTIVE_ENTRY_OPEN_MINUTES, 15)
    kinds: list[tuple[SituationKind, str]] = [
        (
            SituationKind.ENTRY_LEFT_OPEN,
            f"eine Tür, ein Fenster oder das Garagentor bleibt länger als {minutes} Minuten offen",
        ),
        (SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING, "Licht oder Geräte bleiben an, wenn niemand zuhause ist"),
        (SituationKind.CRITICAL_SAFETY_EVENT, "ein Rauch-, Wasser-, Gas- oder CO-Melder schlägt an"),
    ]
    if options.get(CONF_PROACTIVE_APPLIANCE_ENTITIES):
        kinds.append((SituationKind.APPLIANCE_FINISHED, "ein ausgewähltes Gerät wie die Waschmaschine ist fertig"))
    return kinds


# --- managing monitors (7.9 W8) ------------------------------------------------------


@dataclass(frozen=True)
class Monitor:
    """One running monitor, HA automation or HomeIntent goal, in plain words."""

    label: str
    enabled: bool
    entity_ids: frozenset[str]
    automation_id: str | None = None
    goal_id: str | None = None
    # 7.9.1 A2: who set it up (``None`` for older ones: administrators only).
    owner_user_id: str | None = None


def _notifies(actions: Any) -> bool:
    for step in actions or ():
        if not isinstance(step, Mapping):
            continue
        if str(step.get("action", "")).startswith("notify."):
            return True
        nested = step.get("repeat", {})
        if isinstance(nested, Mapping) and _notifies(nested.get("sequence")):
            return True
        if _notifies(step.get("sequence")) or _notifies(step.get("then")):
            return True
    return False


def collect_monitors(
    automations: Any, records: Any, entities: list[EntitySnapshot]
) -> list[Monitor]:
    """HomeIntent-made notification automations and monitor goals."""
    names = {entity.entity_id: entity.friendly_name for entity in entities}
    found: list[Monitor] = []
    for automation in automations:
        if automation.created_by != CREATED_BY_HOMEINTENT or not _notifies(automation.actions):
            continue
        label = automation.description or automation.source_text or automation.alias
        found.append(Monitor(
            label, bool(automation.enabled), frozenset(automation.referenced_entity_ids),
            automation_id=automation.automation_id,
            owner_user_id=getattr(automation, "owner_user_id", None),
        ))
    for record in records:
        rule = rate_rule_of(record.goal)
        if rule is not None:
            label = "Wenn " + describe_rule(rule, f"„{names.get(rule.entity_id, rule.entity_id)}“") + (
                ", melde ich mich (das überwache ich selbst)"
            )
            found.append(Monitor(
                label, record.enabled, frozenset({rule.entity_id}), goal_id=record.goal.goal_id,
                owner_user_id=record.goal.provenance.user_id,
            ))
        elif record.goal.provenance.source_utterance:
            found.append(Monitor(
                record.goal.provenance.source_utterance, record.enabled, frozenset(),
                goal_id=record.goal.goal_id, owner_user_id=record.goal.provenance.user_id,
            ))
    return found


_COUNT_WORDS = ("keine", "eine", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn")


def _count_word(count: int, noun: str) -> str:
    return f"{_COUNT_WORDS[count] if count < len(_COUNT_WORDS) else count} {noun}"


def short_label(monitor: Monitor) -> str:
    """What a monitor watches, without its full preview: the conditional
    clause up to the main clause ("…, sende ich dir …", "…, dann …")."""
    text = monitor.label.strip().rstrip(".")
    words = text.split()
    short = text
    if words and words[0].casefold() in {"wenn", "sobald", "falls"}:
        for index, word in enumerate(words[:-1]):
            if not word.endswith(","):
                continue
            following = words[index + 1].casefold()
            after = words[index + 2].casefold() if index + 2 < len(words) else ""
            if following == "dann" or after == "ich":
                short = " ".join(words[: index + 1]).rstrip(",")
                break
        short = short[:1].casefold() + short[1:]
    if monitor.goal_id is not None and "selbst" not in short:
        short += " (das überwache ich selbst)"
    return short


_ORDINALS = {
    "erste": 0, "ersten": 0, "zweite": 1, "zweiten": 1, "dritte": 2, "dritten": 2,
    "vierte": 3, "vierten": 3, "fünfte": 4, "fünften": 4,
}


def ordinal_index(text: str, count: int) -> int | None:
    """"Was macht die erste?", "die letzte", "Nummer 2" -> index, or ``None``."""
    keys = [word.strip(",.;:!?").casefold() for word in text.split()]
    if len(keys) > 8:
        return None
    for position, key in enumerate(keys):
        if key in _ORDINALS and _ORDINALS[key] < count:
            return _ORDINALS[key]
        if key in {"letzte", "letzten"}:
            return count - 1
        if key == "nummer" and position + 1 < len(keys) and keys[position + 1].isdigit():
            number = int(keys[position + 1])
            return number - 1 if 1 <= number <= count else None
    return None


def _stem(word: str) -> str:
    key = normalize_for_compare(word)
    for suffix in ("en", "n", "s"):
        if key.endswith(suffix) and len(key) - len(suffix) >= 4:
            candidate = key[: -len(suffix)]
            if candidate.endswith(("ag", "ter", "ür", "tür", "tor")) or suffix != "s":
                return candidate
    return key


def matching(monitors: list[Monitor], subject: str | None, entities: list[EntitySnapshot]) -> list[Monitor]:
    """Monitors whose watched devices or wording contain the subject."""
    if not subject:
        return monitors
    stems = [_stem(word) for word in subject.split()]
    by_id = {entity.entity_id: entity for entity in entities}

    def mentions(monitor: Monitor) -> bool:
        texts = [normalize_for_compare(monitor.label)]
        for entity_id in monitor.entity_ids:
            entity = by_id.get(entity_id)
            if entity is not None:
                texts.append(normalize_for_compare(f"{entity.friendly_name} {entity.area_name or ''}"))
        return all(any(stem in text for text in texts) for stem in stems)

    return [monitor for monitor in monitors if mentions(monitor)]

