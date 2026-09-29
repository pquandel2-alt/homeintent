"""Comfort profiles, procedures and household documents (7.7, B4).

"Mach es gemütlich" uses the user's confirmed comfort profile and becomes
a previewed goal plan; procedures ("Was muss ich beim Lüften beachten?")
and document answers only read. From ``conversation.py``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any, Callable, Protocol

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ..agent_action_policy import validate_agent_service_plan
from ..audit_log import AuditTrail
from ..comfort_intent import is_comfort_request
from ..dialog_learning import MEMORY_DISABLED_TEXT
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..document_intent import interpret_document_search
from ..entities import EntitySnapshot, normalize_for_compare
from ..goal_intent import interpret_goal
from ..learning_policy import KnowledgeState
from ..memory import MemoryKind
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.language_frontend import LanguageDocument
from ..planner import (
    Goal,
    GoalKind,
    materialize_comfort_profile,
    materialize_goal,
    MaterializedPlan,
)
from ..preferences import LearnedPreference, PreferenceContext, resolve_preferences
from ..procedure_intent import interpret_procedure_intent, ProcedureOperation
from ..profiles import ComfortProfile
from ..security_control import conversation_user_id, user_is_admin
from ..service_call import ServiceCallPlan
from ..service_executor import async_execute_service_plan


class ComfortRuntime(Protocol):
    """Runtime services of comfort, procedures and documents."""

    dialog_manager: Any
    document_index: Any | None
    effect_monitor: Any
    memory: Any | None
    profiles: Any | None
    user_contexts: Any | None


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


class ComfortController:
    """Handles comfort, procedure and document turns."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        audit_trail: AuditTrail,
        runtime: ComfortRuntime,
        entities: Callable[[], list[EntitySnapshot]],
        stage_plan: Callable[..., Any],
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._audit_trail = audit_trail
        self._runtime = runtime
        self._entities = entities
        self._stage_plan = stage_plan

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    async def async_handle_comfort_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
        *,
        area_id: str | None,
    ) -> conversation.ConversationResult | None:
        """Turn a vague comfort command into one explicit, confirmed ASK."""
        manager = self._runtime.dialog_manager
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
            if draft is None or len(users) < 2 or self._runtime.profiles is None:
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
                await self._runtime.profiles.async_save_comfort_profile(
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
            elif not isinstance(draft, ComfortProfile) or self._runtime.profiles is None:
                manager.cancel(conversation_id)
                response.async_set_speech("Die Komfortprofil-Definition ist nicht mehr vollständig.")
            else:
                confirmed_profile = replace(draft, confirmed=True)
                await self._runtime.profiles.async_save_comfort_profile(
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
                    fresh = self._entities()
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
                            effect_monitor=self._runtime.effect_monitor,
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
        profiles = self._runtime.profiles
        profile: ComfortProfile | None = None
        if profiles is not None and actor_id is not None and area_id is not None:
            states = {item.entity_id: item.state for item in entities}
            present_user_ids = (
                self._runtime.user_contexts.present_user_ids(states)
                if self._runtime.user_contexts is not None else ()
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
        return self._stage_plan(user_input, response, plan, actor_id)

    async def async_handle_procedure_turn(
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
        store = self._runtime.memory
        manager = self._runtime.dialog_manager
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

    async def async_handle_document_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
    ) -> conversation.ConversationResult | None:
        query = interpret_document_search(language_document)
        if query is None:
            return None
        index = self._runtime.document_index
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
