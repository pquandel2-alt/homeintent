"""Source-bound media commands and their existing dialog continuations.

This module is a controller mixin, not another router.  It reuses the turn's
``LanguageDocument`` and calls the owning controller's established result,
device-policy, alias and dialog boundaries.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import intent

from ..entities import EntitySnapshot, normalize_for_compare
from ..arbitration_candidates import complete_command_candidate
from ..media import (
    PendingMediaCommand,
    claims_other_target,
    merge_media_continuation,
    parse_media_request,
    resolve_media,
)
from ..media_coverage import analyse_media_coverage
from ..music_aliases import (
    PendingMediaAliasInstanceSelection,
    async_resolve_media_alias,
    select_music_assistant_instance,
)
from ..music_assistant import PendingMusicAssistant, async_resolve_music_assistant
from ..nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from ..nlu.clause_binding import analyse_clause_bindings
from ..nlu.composition import independent_predicate_clauses
from ..nlu.language_frontend import LanguageDocument, analyse_language
from ..nlu.semantic_utterance import analyse_utterance
from ..security_control import conversation_user_id


def words_of(text: str) -> list[str]:
    """Canonical words shared by media selection and the parent controller."""

    return "".join(
        char if char.isalnum() else " " for char in normalize_for_compare(text)
    ).split()


_MEDIA_TYPE_SELECTIONS = {
    "artist": frozenset({"kuenstler", "kuenstlerin", "interpret", "interpretin"}),
    "album": frozenset({"album"}),
    "playlist": frozenset({"playlist", "wiedergabeliste"}),
    "track": frozenset({"titel", "lied", "song", "track", "stueck"}),
    "radio": frozenset({"radio", "sender", "radiosender", "station"}),
}


def _selected_media_type(text: str, allowed: tuple[str, ...]) -> str | None:
    words = set(words_of(text))
    matches = [
        kind
        for kind in allowed
        if words & _MEDIA_TYPE_SELECTIONS.get(kind, frozenset())
    ]
    return matches[0] if len(matches) == 1 else None


def _media_selection_matches(text: str, entity: EntitySnapshot) -> bool:
    words = set(words_of(text))
    names = (entity.friendly_name, *entity.aliases)
    if any(set(words_of(name)) <= words for name in names if name):
        return True
    place_names = (entity.area_name or "", *entity.area_aliases)
    return any(set(words_of(name)) <= words for name in place_names if name)


def media_reading_complete(text: str, request: Any, entities: list[EntitySnapshot],
                           *, selection: bool = False,
                           selection_words: frozenset[str] = frozenset()) -> bool:
    """Bind all amendment words before choosing a target or changing a draft.

    An elliptical selection has no free-text source slot: an unknown suffix
    cannot become part of the previously resolved source name.
    """
    document = analyse_language(text, entities)
    reading = replace(request, source_words=()) if selection else request
    coverage = analyse_media_coverage(text, entities, reading)
    residue = set(words_of(" ".join(coverage.residue)))
    return (
        not document.temporal
        and (selection or analyse_utterance(text).allows_bounded_parser_action)
        and not coverage.coordinations
        and not coverage.conflicts
        and not (residue - selection_words)
    )


class MediaCommandSupport:
    """Cohesive media preflight and continuation methods for Insights."""

    def _media_location_correction(
        self: Any,
        user_input: conversation.ConversationInput,
        entities: list[EntitySnapshot],
    ) -> Any | None:
        """Apply ``Doch im Schlafzimmer`` only to the last media request."""

        words = words_of(user_input.text)
        if words[:1] == ["nein"]:
            words = words[1:]
        locative_start = (
            words[1:2] in (["im"], ["am"])
            or words[1:3] in (["in", "der"], ["in", "dem"])
        )
        if words[:1] != ["doch"] or not locative_start:
            return None
        store = getattr(self.devices, "_context_store", None)
        context = store.get(user_input.conversation_id) if store is not None else None
        memory = getattr(context, "memory", None)
        request = getattr(memory, "media_request", None)
        if (
            request is None
            or getattr(memory, "media_owner", None) != conversation_user_id(user_input)
            or getattr(request, "op", None) != "play"
            or not getattr(request, "source_words", ())
        ):
            return None
        return merge_media_continuation(request, user_input.text)

    async def _async_resolve_media_request(
        self: Any,
        user_input: conversation.ConversationInput,
        request: Any,
        entities: list[EntitySnapshot],
    ) -> Any:
        """Resolve one media request without executing it."""

        resolved = await async_resolve_media_alias(
            self.hass,
            request,
            entities,
            owner_user_id=conversation_user_id(user_input),
            satellite_area_id=self._area_id,
            context=getattr(user_input, "context", None),
        )
        if resolved is None:
            resolved = await async_resolve_music_assistant(
                self.hass,
                request,
                entities,
                satellite_area_id=self._area_id,
                context=getattr(user_input, "context", None),
            )
        if resolved is None:
            resolved = resolve_media(
                request, entities, satellite_area_id=self._area_id
            )
        return resolved

    async def _async_compound_media(
        self: Any,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
        document: LanguageDocument,
    ) -> conversation.ConversationResult | None:
        """Preflight a source-containing command chain before its first write."""

        clauses = independent_predicate_clauses(document)
        if len(clauses) < 2:
            return None
        requests = tuple(parse_media_request(clause) for clause in clauses)
        if not any(
            request is not None
            and request.op == "play"
            and bool(request.source_words)
            for request in requests
        ):
            return None
        if self.devices is None:
            return self._result(
                user_input,
                response,
                "Den Mehrfachauftrag kann ich gerade nicht sicher ausführen; "
                "ich habe nichts gestartet.",
            )

        from ..engine import CommandPlan, MatchResult

        commands: list[MatchResult] = []
        for clause, request in zip(clauses, requests, strict=True):
            if request is not None:
                clause_document = analyse_language(clause, entities)
                coverage = analyse_media_coverage(clause, entities, request)
                if (
                    request.op == "query"
                    or claims_other_target(clause, entities, request)
                    or clause_document.temporal
                    or not clause_document.utterance.allows_bounded_parser_action
                    or not coverage.complete
                ):
                    return self._result(
                        user_input,
                        response,
                        f"Den Teil „{clause.strip(' ,.!?')}“ kann ich nicht vollständig "
                        "als Medienaktion binden; ich habe nichts ausgeführt.",
                    )
                resolved = await self._async_resolve_media_request(
                    replace(user_input, text=clause), request, entities
                )
                if resolved.plan is None:
                    return self._result(
                        user_input,
                        response,
                        f"{resolved.spoken} Der gesamte Mehrfachauftrag wurde verworfen; "
                        "ich habe nichts ausgeführt.",
                    )
                commands.append(
                    MatchResult(plan=resolved.plan, response_text=resolved.spoken)
                )
                continue

            engine = getattr(self.devices, "_engine", None)
            world_model = getattr(self.devices, "_world_model", None)
            if engine is None:
                return self._result(
                    user_input,
                    response,
                    "Mindestens einen Teil des Mehrfachauftrags kann ich nicht sicher "
                    "prüfen; ich habe nichts ausgeführt.",
                )
            understood = engine.understand(clause, entities, world_model)
            item = understood.payload
            if not isinstance(item, MatchResult) or item.plan is None:
                return self._result(
                    user_input,
                    response,
                    f"Den Teil „{clause.strip(' ,.!?')}“ habe ich nicht vollständig "
                    "verstanden; ich habe nichts ausgeführt.",
                )
            commands.append(item)

        plan = CommandPlan(tuple(commands))
        binding = analyse_clause_bindings(document, entities, plan)
        if not binding.complete or len(binding.bindings) != len(commands):
            rejected = " und ".join(
                f"„{item.text.strip(' ,.!?')}“"
                for item in binding.bindings
                if not item.complete
            ) or "mindestens eine Klausel"
            return self._result(
                user_input,
                response,
                f"{rejected} ist nicht vollständig an den geplanten Aufruf gebunden; "
                "ich habe nichts ausgeführt.",
            )
        return await self.devices.async_handle_command_plan(
            user_input, response, plan, entities
        )

    async def _async_media_selection(
        self: Any,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        pending = getattr(task, "payload", None)
        if isinstance(pending, PendingMediaAliasInstanceSelection):
            actor = conversation_user_id(user_input)
            if actor is None or pending.requesting_user_id != actor:
                return self._result(
                    user_input,
                    response,
                    "Diese Rückfrage gehört zu einem anderen Benutzer.",
                )
            if not media_reading_complete(
                user_input.text, parse_media_request("Spiel Musik"), entities,
                selection=True,
            ):
                self._runtime.dialog_manager.cancel(user_input.conversation_id, task.task_id)
                return self._result(user_input, response,
                    "Die gesamte Auswahl ist nicht eindeutig verstanden. Der Entwurf wurde verworfen.")
            resolution = select_music_assistant_instance(
                user_input.text, pending, entities
            )
            if resolution.config_entry_id is None:
                return self._result(
                    user_input,
                    response,
                    "Die Auswahl ist noch nicht eindeutig; ich habe nichts gespeichert. "
                    "Bitte nenne genau einen der angebotenen Player oder Räume.",
                )
            self._runtime.dialog_manager.cancel(
                user_input.conversation_id, task.task_id
            )
            return await self._async_prepare_media_alias_create(
                user_input,
                response,
                pending.request,
                actor,
                resolution.config_entry_id,
                resolution.player_entity_id,
            )
        if isinstance(pending, (PendingMusicAssistant, PendingMediaCommand)):
            if task.requested_by_user_id not in {
                None,
                conversation_user_id(user_input),
            }:
                return self._result(
                    user_input,
                    response,
                    "Diese Rückfrage gehört zu einem anderen Benutzer.",
                )
            reply = classify_confirmation_reply(user_input.text)
            words = set(words_of(user_input.text))
            if reply is ConfirmationReply.NO or words & {
                "abbrechen",
                "abbruch",
                "vergiss",
            }:
                self._runtime.dialog_manager.cancel(
                    user_input.conversation_id, task.task_id
                )
                return self._result(
                    user_input,
                    response,
                    "In Ordnung, der offene Medienentwurf wurde vollständig verworfen.",
                )
            fresh_request = parse_media_request(user_input.text)
            amended_request = merge_media_continuation(
                pending.request, user_input.text
            )
            if fresh_request is not None and not (
                set(words_of(user_input.text)) & {"es", "dort", "da", "hier"}
            ):
                self._runtime.dialog_manager.cancel(
                    user_input.conversation_id, task.task_id
                )
                return await self._async_media(
                    user_input,
                    response,
                    entities,
                    analyse_language(user_input.text, entities),
                )
            if fresh_request is None:
                understood = self.devices._engine.understand(
                    user_input.text, entities, self.devices._world_model
                )
                if complete_command_candidate(understood.payload) is not None:
                    self._runtime.dialog_manager.cancel(user_input.conversation_id, task.task_id)
                    return await self._async_rerun(user_input)
        if isinstance(pending, (PendingMusicAssistant, PendingMediaCommand)):
            request = amended_request
            if request is None:
                self._runtime.dialog_manager.cancel(
                    user_input.conversation_id, task.task_id
                )
                self.devices._context_store.clear(user_input.conversation_id)
                return self._result(
                    user_input,
                    response,
                    "Die Medienänderung widerspricht dem offenen Auftrag. "
                    "Ich habe nichts gestartet und den Entwurf verworfen.",
                )
            selection_words = frozenset({"es"})
            if isinstance(pending, PendingMusicAssistant) and pending.stage == "media_type":
                selection_words |= frozenset(
                    word for kind in pending.media_types
                    for word in _MEDIA_TYPE_SELECTIONS.get(kind, ())
                )
            if not media_reading_complete(
                user_input.text, request, entities, selection=True,
                selection_words=selection_words,
            ):
                self._runtime.dialog_manager.cancel(user_input.conversation_id, task.task_id)
                self.devices._context_store.clear(user_input.conversation_id)
                return self._result(user_input, response,
                    "Die gesamte Ergänzung kann ich nicht sicher als sofortigen Medienauftrag "
                    "verstehen. Ausschlüsse, Zeitbedingungen oder weitere Angaben benötigen "
                    "einen vollständigen neuen Auftrag. Der offene Entwurf wurde verworfen.")
        if isinstance(pending, PendingMediaCommand):
            candidates = [
                entity
                for entity in entities
                if entity.entity_id in pending.player_ids
            ]
            matches = [
                entity
                for entity in candidates
                if _media_selection_matches(user_input.text, entity)
            ]
            if len(matches) != 1:
                return self._result(
                    user_input,
                    response,
                    "Die Auswahl ist noch nicht eindeutig; ich habe nichts gestartet. "
                    "Bitte nenne genau einen Player oder Raum.",
                )
            chosen = matches[0]
            self._runtime.dialog_manager.cancel(
                user_input.conversation_id, task.task_id
            )
            resolved = await self._async_resolve_media_request(
                replace(user_input, text=request.text),
                request,
                [chosen],
            )
            return await self._async_finish_media(
                user_input,
                response,
                resolved,
                entities,
                request=request,
            )
        if not isinstance(pending, PendingMusicAssistant):
            return None
        if task.requested_by_user_id not in {
            None,
            conversation_user_id(user_input),
        }:
            return self._result(
                user_input,
                response,
                "Diese Rückfrage gehört zu einem anderen Benutzer.",
            )
        chosen: EntitySnapshot | None = None
        request = amended_request
        if request is None:
            return self._result(
                user_input,
                response,
                "Die Medienänderung widerspricht dem offenen Auftrag; ich habe nichts gestartet.",
            )
        if pending.stage == "player":
            candidates = [
                entity
                for entity in entities
                if entity.entity_id in pending.player_ids
            ]
            matches = [
                entity
                for entity in candidates
                if _media_selection_matches(user_input.text, entity)
            ]
            if len(matches) == 1:
                chosen = matches[0]
        elif pending.stage == "media_type" and pending.player_id is not None:
            media_type = _selected_media_type(user_input.text, pending.media_types)
            if media_type is not None:
                request = replace(request, media_type=media_type)
                chosen = next(
                    (
                        entity
                        for entity in entities
                        if entity.entity_id == pending.player_id
                    ),
                    None,
                )
        if chosen is None:
            return self._result(
                user_input,
                response,
                "Die Auswahl ist noch nicht eindeutig; ich habe nichts gestartet. "
                "Bitte nenne den Player oder Medientyp.",
            )
        self._runtime.dialog_manager.cancel(user_input.conversation_id, task.task_id)
        resolved = await async_resolve_music_assistant(
            self.hass,
            request,
            [chosen],
            satellite_area_id=chosen.area_id,
            context=getattr(user_input, "context", None),
        )
        if resolved is None:
            return self._answer(
                user_input,
                response,
                "Music Assistant ist nicht mehr verfügbar; ich habe nichts gestartet.",
            )
        return await self._async_finish_media(
            user_input, response, resolved, entities, request=request
        )
