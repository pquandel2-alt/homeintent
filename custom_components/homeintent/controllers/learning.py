"""Learning and memory by voice (7.7, B4: from ``conversation.py``).

What HomeIntent has learned (V11 models, habits), "Merk dir ..." and
"Vergiss ...", routine feedback and confirmed preferences. Learning never
authorises: learned aliases and preferences only extend the snapshot the
one target resolution sees; every command still passes the policy.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
)

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..alias_learning import AliasLearningDraft
from ..bindings import BindingKind, BindingScope
from ..const import CONF_CUSTOM_ALIASES
from ..dialog_learning import MEMORY_DISABLED_TEXT
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..entities import EntitySnapshot
from ..house_graph import FactProvenance
from ..learning_control import (
    async_accept_habit,
    async_confirm_preference,
    async_forget_model,
    async_reject_habit,
    async_reject_preference,
    async_reset_models,
    LearningControlError,
    model_owner,
    options_without_preference_aliases,
)
from ..learning_dialog import LearningDialogOperation, LearningDialogPayload
from ..learning_intent import interpret_learning_request, LearningOperation
from ..learning_policy import KnowledgeState
from ..memory import MemoryKind
from ..memory_intent import interpret_memory_intent, MemoryOperation
from ..model_registry import (
    explain_learned_model,
    LearnedKind,
    LearnedModel,
    ModelHealth,
)
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.german_morphology import counted_passive
from ..nlu.language_frontend import LanguageDocument
from ..profiles import RoutineDefinition
from ..routine_intent import interpret_routine_feedback
from ..security_control import conversation_user_id, user_is_admin


class LearningRuntime(Protocol):
    """Runtime services of learning and memory."""

    bindings: Any
    dialog_manager: Any
    learned_models: Any | None
    learning_center_revision: Any
    learning_policy: Any | None
    memory: Any | None
    predictive_house: Any | None
    routine_statistics: Any


_MEMORY_KIND_DE = {
    "episode": "Ereignisse", "household_fact": "Haushaltsfakten",
    "preference": "Vorlieben", "procedure": "Abläufe", "decision": "Entscheidungen",
    "routine_grant": "Routinen-Freigaben",
}


_TIME_BAND_DE = {"morning": "morgens", "day": "tagsüber", "evening": "abends", "night": "nachts"}


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


def _learning_control_speech(error: LearningControlError) -> str:
    return {
        "wrong_owner": "Dieses gelernte Wissen gehört zu einem anderen Benutzer.",
        "invalid_state": "Dieser Vorschlag ist bereits entschieden.",
        "not_found": "Der Kandidat ist nicht mehr verfügbar.",
    }.get(error.code.value, "Diese Änderung ist für dieses Modell nicht möglich.")


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


_MEMORY_KIND_SINGULAR_DE = {
    "episode": "Ereignis", "household_fact": "Haushaltsfakt", "preference": "Vorliebe",
    "procedure": "Ablauf", "decision": "Entscheidung", "routine_grant": "Routinen-Freigabe",
}


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


_MODEL_HEALTH_DE = {
    "valid": "gültig",
    "low_confidence": "noch unsicher",
    "unreliable": "unzuverlässig",
    "stale": "veraltet",
    "drift_detected": "Abweichung erkannt",
    "invalid": "ungültig",
}



class LearningController:
    """Handles learning, memory and preference turns."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        runtime: LearningRuntime,
        learned_alias_view: Callable[..., list[EntitySnapshot]],
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._runtime = runtime
        self.learned_alias_view = learned_alias_view

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    async def async_handle_learning_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
    ) -> conversation.ConversationResult | None:
        """Review/explain/delete V11 knowledge without exposing raw history."""
        registry = self._runtime.learned_models
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
        manager = self._runtime.dialog_manager
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
                    predictive = self._runtime.predictive_house
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
                policy = self._runtime.learning_policy
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

    async def async_handle_memory_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Handle explicit memory meanings after the shared language frontend."""
        store = self._runtime.memory
        manager = self._runtime.dialog_manager
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

    async def async_handle_routine_feedback_turn(
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
            for entity_id, stats in self._runtime.routine_statistics.items()
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
            store = self._runtime.memory
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

    async def async_confirm_alias_learning(
        self, draft: AliasLearningDraft, actor_id: str | None
    ) -> None:
        """Store a confirmed alias as a binding (7.4.1).

        A plain alias belongs to the household; an alias taught for one room
        ("Mit Lampe meine ich im Wohnzimmer die Stehlampe") is the speaker's
        own and only applies in that room.
        """
        personal = draft.area_id is not None and actor_id is not None
        await self._runtime.bindings.async_bind(
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
        self._runtime.learning_center_revision.bump()

    async def async_apply_confirmed_preferences(
        self,
        entities: list[EntitySnapshot],
        *,
        area_id: str | None,
        user_id: str | None,
    ) -> list[EntitySnapshot]:
        """Expose confirmed preferences as aliases only in their exact context."""
        entities = self.learned_alias_view(entities, area_id=area_id, user_id=user_id)
        registry = self._runtime.learned_models
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
