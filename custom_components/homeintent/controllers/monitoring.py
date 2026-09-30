"""Monitoring requests HomeIntent runs itself, and their dialogs (7.9).

The meaning of a monitoring request comes from the sentence-based event
reader (7.8.3).  Where Home Assistant cannot express it without new helpers
(a change by an amount within a window, W3), HomeIntent's own monitor
runtime runs it - staged here for an explicit "Ja" exactly like an
automation preview, never stored before.
"""

from __future__ import annotations

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
from ..security_control import conversation_user_id
from ..user_context import BindingStatus


class MonitoringController:
    """Stages and confirms HomeIntent-run monitors."""

    def __init__(self, *, runtime: Any, automation_store: Callable[[], Any] | None = None) -> None:
        self._runtime = runtime
        self._automation_store = automation_store

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
                "Welches bestätigte Gerät soll ich für diese Push-Benachrichtigung verwenden?"
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
        chosen = matching(monitors, request.subject, entities)
        spoken_subject = f" für „{request.subject}“" if request.subject else ""

        def say(text: str, query: bool = False) -> conversation.ConversationResult:
            if query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
            response.async_set_speech(text)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if request.operation is MonitoringOperation.LIST:
            if not chosen:
                return say(f"Gerade läuft keine Überwachung{spoken_subject}.", query=True)
            parts = [
                f"„{item.label.rstrip('.')}“" + ("" if item.enabled else " (ausgeschaltet)")
                for item in chosen
            ]
            head = "Es läuft eine Überwachung" if len(chosen) == 1 else f"Es laufen {len(chosen)} Überwachungen"
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
        if request.operation is MonitoringOperation.STOP:
            if monitor.automation_id is not None and store is not None:
                await store.async_disable_automation(monitor.automation_id)
            elif monitor.goal_id is not None and goals is not None:
                record = next(item for item in records if item.goal.goal_id == monitor.goal_id)
                await goals.async_save(replace(record, enabled=False))
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
            return say("Bis wann? Sag zum Beispiel: „Pausiere die Garagen-Meldung bis morgen um 7 Uhr.“")
        if monitor.automation_id is None or store is None:
            return say(
                "Überwachungen, die ich selbst ausführe, kann ich ausschalten, aber nicht zeitlich "
                "pausieren."
            )
        resume = (now + timedelta(days=request.day_offset)).replace(
            hour=request.hour, minute=request.minute, second=0, microsecond=0
        )
        if resume <= now:
            return say("Dieser Zeitpunkt liegt schon in der Vergangenheit. Bis wann soll ich pausieren?")
        await store.async_pause_automation_until(monitor.automation_id, resume)
        day = "morgen" if request.day_offset == 1 else "heute" if request.day_offset == 0 else resume.strftime("%d.%m.")
        return say(
            f"Pausiert bis {day} um {resume:%H:%M} Uhr: „{monitor.label.rstrip('.')}“. Danach schalte ich "
            "sie automatisch wieder ein."
        )

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
        if monitor.automation_id is not None and self._automation_store is not None:
            await self._automation_store().async_delete_automation(monitor.automation_id)
        elif monitor.goal_id is not None and self._runtime.monitor_goals is not None:
            await self._runtime.monitor_goals.async_delete(monitor.goal_id)
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
        ))
    for record in records:
        rule = rate_rule_of(record.goal)
        if rule is not None:
            label = "Wenn " + describe_rule(rule, f"„{names.get(rule.entity_id, rule.entity_id)}“") + (
                ", melde ich mich (das überwache ich selbst)"
            )
            found.append(Monitor(
                label, record.enabled, frozenset({rule.entity_id}), goal_id=record.goal.goal_id,
            ))
        elif record.goal.provenance.source_utterance:
            found.append(Monitor(
                record.goal.provenance.source_utterance, record.enabled, frozenset(),
                goal_id=record.goal.goal_id,
            ))
    return found


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

