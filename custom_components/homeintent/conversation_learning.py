"""Dialog learning glue of the conversation agent (7.4.1).

The pure rules live in ``dialog_learning``; this mixin only connects them to
the dialog (questions, "Ja"/"Nein") and to the ``BindingStore``. It never
executes anything itself: a learned sentence (macro, preference, the command
after an unknown word) is run through ``_async_handle_message_inner`` - the
same pipeline, validator, EffectGraph and policy as a spoken command.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from .bindings import Binding, BindingKind, BindingScope, binding_state
from .dialog_learning import (
    DEFAULT_CHOICE_THRESHOLD,
    MacroDraft,
    activity_label,
    alias_rejection,
    choice_key,
    choice_label,
    describe_bindings,
    is_about_me_question,
    is_learned_question,
    macro_invoked,
    parse_activity_announcement,
    parse_forget,
    parse_macro_definition,
    parse_preference_question,
    parse_preference_statement,
    replace_word,
    unknown_device_noun,
)
from .dialog_manager import DialogPriority, DialogTaskKind
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from .nlu.device_ontology import analyse_word, lookup_genus_word
from .nlu.entity_clarification import CandidateReplyKind, resolve_candidate_reply
from .nlu.place_model import PlaceKind, build_place_lexicon
from .nlu.target_resolution import (
    apply_alias_bindings,
    default_choice_for,
    numbered_question,
    resolve_phrase,
)
from .security_control import conversation_user_id, user_is_admin

_ON_OFF_DOMAINS = frozenset({"light", "switch", "fan", "media_player", "input_boolean", "humidifier", "climate"})
_COVER_WORDS = {"auf", "zu", "hoch", "runter", "oeffne", "schliesse", "fahre", "fahr"}
_MAX_LISTED = 6


@dataclass(frozen=True)
class BindingOffer:
    """"Soll ich mir … merken?" - stored only after "Ja"."""

    kind: BindingKind
    key: str
    target: str
    scope: BindingScope
    user_id: str | None
    saved_text: str
    entity_ids: tuple[str, ...] = ()
    data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandOffer:
    """A remembered sentence previewed before its first use."""

    command: str
    binding_id: str
    label: str


@dataclass(frozen=True)
class UnknownWordQuestion:
    word: str
    text: str
    candidate_ids: tuple[str, ...]


def is_known_device_word(word: str) -> bool:
    if lookup_genus_word(word):
        return True
    analysis = analyse_word(word)
    return analysis is not None and bool(analysis.genera)


class DialogLearningMixin:
    """Methods of the conversation entity for learning from the dialog."""

    hass: Any
    entry: Any
    _runtime_data: Any
    _context_store: Any
    _engine: Any
    _world_model: Any

    # -- helpers -----------------------------------------------------------
    def _visible_bindings(self, user_id: str | None) -> tuple[Binding, ...]:
        """Household bindings plus the speaker's own ones."""
        store = self._runtime_data.bindings
        return tuple(
            item for item in store.all()
            if item.scope is BindingScope.HOUSEHOLD or (user_id is not None and item.user_id == user_id)
        )

    def _find_binding(self, kind: BindingKind, key: str, user_id: str | None) -> Binding | None:
        return self._runtime_data.bindings.find(kind, key, user_id)

    def _append_to_turn(self, conversation_id: str | None, text: str) -> None:
        suffixes = self.__dict__.setdefault("_turn_suffixes", {})
        if conversation_id is not None:
            suffixes[conversation_id] = text

    def _take_turn_suffix(self, conversation_id: str | None) -> str | None:
        suffixes = self.__dict__.setdefault("_turn_suffixes", {})
        return suffixes.pop(conversation_id, None) if conversation_id is not None else None

    def _offer(self, user_input: Any, payload: object, question: str, *, suffix: bool) -> None:
        self._runtime_data.dialog_manager.create(
            user_input.conversation_id,
            "learning-offer",
            DialogTaskKind.LEARNING_OFFER,
            DialogPriority.CONFIRMATION,
            reason="Gelerntes wird nur nach einem ausdrücklichen Ja gespeichert.",
            requested_by_user_id=conversation_user_id(user_input),
            payload=payload,
        )
        if suffix:
            self._append_to_turn(user_input.conversation_id, question)

    def _result(self, user_input: Any, response: Any) -> Any:
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def _async_run_sentence(self, user_input: Any, text: str) -> Any:
        """Run a (learned) sentence through the complete ordinary pipeline."""
        chat_log = self.__dict__.get("_current_chat_log")
        return await self._async_handle_message_inner(  # type: ignore[attr-defined]
            replace(user_input, text=text), chat_log
        )

    def learned_alias_view(
        self, entities: list[EntitySnapshot], *, area_id: str | None, user_id: str | None
    ) -> list[EntitySnapshot]:
        """Snapshots with the aliases this speaker may use (area-scoped ones
        only in their room)."""
        aliases = [
            item for item in self._visible_bindings(user_id)
            if item.kind is BindingKind.ALIAS
            and item.data.get("area_id") in (None, area_id)
        ]
        return apply_alias_bindings(entities, aliases)

    # -- answers to learning questions -------------------------------------
    async def _async_handle_learning_task(
        self, user_input: Any, response: Any, task: Any, entities: list[EntitySnapshot]
    ) -> Any:
        manager = self._runtime_data.dialog_manager
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        if task.requested_by_user_id is not None and task.requested_by_user_id != actor_id:
            response.async_set_speech("Diese Rückfrage gehört zu einem anderen Benutzer.")
            return self._result(user_input, response)
        payload = task.payload
        if isinstance(payload, UnknownWordQuestion):
            return await self._async_answer_unknown_word(user_input, response, task, payload, entities)
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                manager.cancel(conversation_id, task.task_id)
                return None  # a new request; the offer lapses
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return self._result(user_input, response)
        manager.cancel(conversation_id, task.task_id)
        if reply is ConfirmationReply.NO:
            response.async_set_speech("In Ordnung, ich merke mir nichts.")
            return self._result(user_input, response)
        if isinstance(payload, CommandOffer):
            store = self._runtime_data.bindings
            binding = store.get(payload.binding_id)
            if binding is not None:
                await store.async_bind(
                    binding.kind, binding.key, binding.target, confirmed=True,
                    scope=binding.scope, user_id=binding.user_id, created_by=binding.created_by,
                    now=dt_util.now(), data={**binding.data, "previewed": True},
                )
            return await self._async_run_sentence(user_input, payload.command)
        if isinstance(payload, BindingOffer):
            exposed = {entity.entity_id for entity in entities}
            if any(entity_id not in exposed for entity_id in payload.entity_ids):
                response.async_set_speech("Das Gerät ist nicht mehr freigegeben. Ich habe nichts gespeichert.")
                return self._result(user_input, response)
            await self._runtime_data.bindings.async_bind(
                payload.kind, payload.key, payload.target, confirmed=True,
                scope=payload.scope, user_id=payload.user_id, created_by=actor_id,
                now=dt_util.now(), data=dict(payload.data),
            )
            self._runtime_data.learning_center_revision.bump()
            response.async_set_speech(payload.saved_text)
            return self._result(user_input, response)
        return None

    # -- one-shot learning turns ------------------------------------------
    async def _async_handle_dialog_learning(
        self, user_input: Any, response: Any, entities: list[EntitySnapshot]
    ) -> Any:
        """About-me, forget, preferences, macros - before generic routing."""
        text = user_input.text
        user_id = conversation_user_id(user_input)
        bindings = self._visible_bindings(user_id)
        macro = macro_invoked(text, [item for item in bindings if item.kind is BindingKind.MACRO])
        if macro is not None:
            return await self._async_run_macro(user_input, response, macro, entities)
        if is_about_me_question(text) or (
            is_learned_question(text) and self._runtime_data.learned_models is None
        ):
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
            names = {entity.entity_id: entity.friendly_name for entity in entities}
            # Only exposed entities are known here: a binding whose target is
            # gone or no longer exposed is reported as inert.
            states = {
                item.binding_id: binding_state(item, names, names)
                for item in bindings if item.targets()
            }
            response.async_set_speech(describe_bindings(bindings, names, states))
            return self._result(user_input, response)
        forget = parse_forget(text)
        if forget is not None:
            for kind in forget.kinds:
                found = self._find_binding(kind, forget.key, user_id)
                if found is None:
                    continue
                is_admin = await user_is_admin(self.hass, user_input)
                if found.scope is BindingScope.HOUSEHOLD and not is_admin and found.created_by not in (None, user_id):
                    response.async_set_speech(
                        "Diesen Eintrag hat jemand anderes angelegt. Löschen kann ihn ein "
                        "Administrator im Learning Center."
                    )
                    return self._result(user_input, response)
                await self._runtime_data.bindings.async_remove(found.binding_id)
                self._runtime_data.learning_center_revision.bump()
                what = (
                    f"meine Einstellung fürs {activity_label(found.key)}"
                    if kind is BindingKind.PREFERENCE
                    else f"„{found.data.get('spoken', found.key)}“"
                )
                response.async_set_speech(f"Erledigt, ich habe {what} vergessen.")
                return self._result(user_input, response)
        activity = parse_preference_question(text)
        if activity is not None:
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
            found = self._find_binding(BindingKind.PREFERENCE, activity, user_id)
            response.async_set_speech(
                f"Beim {activity_label(activity)}: {found.data.get('command')}"
                if found is not None
                else f"Für das {activity_label(activity)} habe ich mir noch keine Einstellung gemerkt."
            )
            return self._result(user_input, response)
        draft = parse_preference_statement(text, entities, resolve_phrase)
        if draft is not None:
            personal = user_id is not None
            scope_text = "für dich" if personal else "für den ganzen Haushalt"
            self._offer(
                user_input,
                BindingOffer(
                    BindingKind.PREFERENCE, draft.activity, draft.entity.entity_id,
                    BindingScope.USER if personal else BindingScope.HOUSEHOLD, user_id,
                    f"Gespeichert. Wenn du sagst, dass du {activity_label(draft.activity).lower()} "
                    "willst, schlage ich das vor.",
                    (draft.entity.entity_id,),
                    {"command": draft.command, "previewed": False,
                     **({"brightness": draft.brightness} if draft.brightness is not None else {}),
                     **({"state": draft.state} if draft.state is not None else {})},
                ),
                "",
                suffix=False,
            )
            response.async_set_speech(
                f"Beim {activity_label(draft.activity)}: {draft.command} "
                f"Soll ich mir das {scope_text} merken?"
            )
            return self._result(user_input, response)
        announced = parse_activity_announcement(text)
        if announced is not None:
            found = self._find_binding(BindingKind.PREFERENCE, announced, user_id)
            if found is None:
                return None  # needs, routines and the rest keep answering
            if found.target not in {entity.entity_id for entity in entities}:
                response.async_set_speech(
                    "Das Gerät aus deiner gemerkten Einstellung ist nicht mehr freigegeben. "
                    "Ich habe nichts ausgeführt."
                )
                return self._result(user_input, response)
            command = str(found.data.get("command", ""))
            await self._runtime_data.bindings.async_record_use(found.binding_id, dt_util.now())
            if not found.data.get("previewed"):
                self._offer(user_input, CommandOffer(command, found.binding_id, activity_label(announced)), "", suffix=False)
                response.async_set_speech(f"Beim {activity_label(announced)} magst du: {command} Soll ich das machen?")
                return self._result(user_input, response)
            return await self._async_run_sentence(user_input, command)
        macro_draft = parse_macro_definition(text)
        if macro_draft is not None:
            return await self._async_offer_macro(user_input, response, macro_draft, entities)
        return None

    async def _async_run_macro(
        self, user_input: Any, response: Any, macro: Binding, entities: list[EntitySnapshot]
    ) -> Any:
        exposed = {entity.entity_id for entity in entities}
        if any(entity_id not in exposed for entity_id in macro.targets()):
            response.async_set_speech(
                f"Das Sprachmakro „{macro.data.get('spoken', macro.key)}“ ist wirkungslos, weil "
                "ein Gerät darin nicht mehr freigegeben ist. Ich habe nichts ausgeführt."
            )
            return self._result(user_input, response)
        await self._runtime_data.bindings.async_record_use(macro.binding_id, dt_util.now())
        return await self._async_run_sentence(user_input, str(macro.data.get("body", "")))

    async def _async_offer_macro(
        self, user_input: Any, response: Any, draft: MacroDraft, entities: list[EntitySnapshot]
    ) -> Any:
        from .shadow_runtime import signature_for

        name_key = normalize_for_compare(draft.name)
        taken = any(
            name_key == normalize_for_compare(name)
            for entity in entities
            for name in (entity.friendly_name, *entity.aliases, entity.area_name or "")
        ) or is_known_device_word(draft.name)
        if taken:
            response.async_set_speech(
                f"„{draft.name}“ ist schon ein Geräte-, Raum- oder Gattungsname. "
                "Bitte wähle für das Sprachmakro ein anderes Wort."
            )
            return self._result(user_input, response)
        payload = self._engine.understand(draft.body, entities, self._world_model).payload
        signature = signature_for(payload, entities)
        if not signature.writes or not signature.targets:
            response.async_set_speech(
                f"Den Teil nach „{draft.name}“ habe ich nicht als Befehl verstanden. "
                "Ich habe nichts gespeichert."
            )
            return self._result(user_input, response)
        user_id = conversation_user_id(user_input)
        self._offer(
            user_input,
            BindingOffer(
                BindingKind.MACRO, draft.name, "macro", BindingScope.HOUSEHOLD, None,
                f"Gespeichert. Sag „{draft.name}“, dann führe ich das aus.",
                tuple(sorted(signature.targets)),
                {"spoken": draft.name, "body": draft.body, "entity_ids": sorted(signature.targets),
                 "created_by": user_id or ""},
            ),
            "",
            suffix=False,
        )
        response.async_set_speech(
            f"Wenn du „{draft.name}“ sagst, führe ich aus: {draft.body} Das ist keine "
            "Automation; jeder Aufruf wird wie ein gesprochener Befehl geprüft. "
            "Soll ich das Sprachmakro speichern?"
        )
        return self._result(user_input, response)

    # -- unknown device nouns ---------------------------------------------
    async def _async_ask_unknown_word(
        self, user_input: Any, response: Any, entities: list[EntitySnapshot], area_id: str | None
    ) -> Any:
        word = unknown_device_noun(user_input.text, entities, is_known_device_word)
        if word is None:
            return None
        words = set(normalize_for_compare(user_input.text).split())
        domains = (
            frozenset({"cover"}) if words & _COVER_WORDS - {"auf"} or ("auf" in words and "prozent" not in words)
            else _ON_OFF_DOMAINS
        )
        candidates = [entity for entity in entities if entity.domain in domains]
        mentions = build_place_lexicon(entities).scan(normalize_for_compare(user_input.text).split())
        places = [mention.place for mention in mentions if mention.place.kind is not PlaceKind.HERE]
        if places:
            # A spoken place is never dropped (7.9.1 A4): nothing there, no offer.
            candidates = [entity for entity in candidates if places[0].contains(entity)]
        elif area_id is not None:
            candidates = [entity for entity in candidates if entity.area_id == area_id] or candidates
        if not candidates:
            return None
        self._runtime_data.dialog_manager.create(
            user_input.conversation_id,
            "unknown-word",
            DialogTaskKind.UNKNOWN_WORD,
            DialogPriority.SELECTION,
            reason="Ein unbekanntes Wort wird erfragt, nicht geraten.",
            requested_by_user_id=conversation_user_id(user_input),
            payload=UnknownWordQuestion(word, user_input.text, tuple(entity.entity_id for entity in candidates)),
        )
        if len(candidates) <= _MAX_LISTED:
            question = numbered_question(candidates).replace("Welches Gerät meinst du: ", "")
            text = f"Was meinst du mit „{word}“? {question}"
        else:
            text = f"Was meinst du mit „{word}“? Nenne bitte das Gerät."
        response.async_set_speech(text)
        return self._result(user_input, response)

    async def _async_answer_unknown_word(
        self, user_input: Any, response: Any, task: Any, question: UnknownWordQuestion,
        entities: list[EntitySnapshot],
    ) -> Any:
        manager = self._runtime_data.dialog_manager
        by_id = {entity.entity_id: entity for entity in entities}
        candidates = tuple(by_id[item] for item in question.candidate_ids if item in by_id)
        selection = resolve_candidate_reply(user_input.text, candidates, entities)
        if selection.kind is CandidateReplyKind.CANCELLED or classify_confirmation_reply(user_input.text) is ConfirmationReply.NO:
            manager.cancel(user_input.conversation_id, task.task_id)
            response.async_set_speech("In Ordnung, ich führe nichts aus.")
            return self._result(user_input, response)
        chosen = selection.entity if selection.kind is CandidateReplyKind.SELECTED else None
        if chosen is None:
            resolved = resolve_phrase(user_input.text.strip(" .!?"), list(candidates))
            chosen = resolved.entity
        if chosen is None:
            if len(user_input.text.split()) > 4:
                manager.cancel(user_input.conversation_id, task.task_id)
                return None
            response.async_set_speech(f"Welches Gerät meinst du mit „{question.word}“?")
            return self._result(user_input, response)
        manager.cancel(user_input.conversation_id, task.task_id)
        result = await self._async_run_sentence(
            user_input, replace_word(question.text, question.word, chosen.friendly_name)
        )
        is_admin = await user_is_admin(self.hass, user_input)
        rejection = alias_rejection(
            question.word, chosen, entities, is_admin=is_admin,
            genus_word=lambda word: bool(lookup_genus_word(word)),
        )
        if rejection is None:
            offer = f"Soll ich mir „{question.word}“ als Namen für {chosen.friendly_name} merken?"
            self._offer(
                user_input,
                BindingOffer(
                    BindingKind.ALIAS, question.word, chosen.entity_id, BindingScope.HOUSEHOLD, None,
                    f"Gespeichert. Mit „{question.word}“ meine ich künftig {chosen.friendly_name}.",
                    (chosen.entity_id,), {"spoken": question.word},
                ),
                offer,
                suffix=True,
            )
        return result

    # -- clarifications answered the same way twice ------------------------
    async def _async_observe_clarification_choice(
        self, user_input: Any, candidates: Sequence[EntitySnapshot], entity: EntitySnapshot,
        area_id: str | None,
    ) -> None:
        user_id = conversation_user_id(user_input)
        if user_id is None or len(candidates) < 2:
            return
        key = choice_key((item.entity_id for item in candidates), area_id)
        offer = await self._runtime_data.bindings.async_observe_choice(user_id, key, entity.entity_id)
        if not offer or self._runtime_data.bindings.choice_counts(user_id, key).get(entity.entity_id, 0) < DEFAULT_CHOICE_THRESHOLD:
            return
        label = choice_label(candidates)
        where = f" im Raum {entity.area_name}" if area_id is not None and entity.area_name else ""
        self._offer(
            user_input,
            BindingOffer(
                BindingKind.DEFAULT_CHOICE, key, entity.entity_id, BindingScope.USER, user_id,
                f"Gespeichert. Bei der Wahl zwischen {label}{where} nehme ich künftig "
                f"{entity.friendly_name}. Nennst du ein anderes Gerät ausdrücklich, gilt das.",
                (entity.entity_id,),
                {"spoken": label, **({"area_id": area_id, "area_name": entity.area_name} if area_id else {})},
            ),
            f"Du hast zweimal {entity.friendly_name} gewählt. Soll ich das künftig{where} "
            "ohne Rückfrage nehmen?",
            suffix=True,
        )

    async def _async_observe_correction(
        self, user_input: Any, undone: Sequence[Any], plan: Any,
        entities: list[EntitySnapshot], area_id: str | None,
    ) -> None:
        """A correction from one device to another is a choice between the
        two (or, after a default choice, among that choice's candidates)."""
        def ids(item: Any) -> list[str]:
            raw = getattr(item, "entity_id", ())
            return [raw] if isinstance(raw, str) else list(raw)

        before = {entity_id for item in undone for entity_id in ids(item)}
        after = set(ids(plan))
        if len(before) != 1 or len(after) != 1 or before == after:
            return
        by_id = {entity.entity_id: entity for entity in entities}
        previous, chosen = next(iter(before)), next(iter(after))
        if previous not in by_id or chosen not in by_id:
            return
        user_id = conversation_user_id(user_input)
        candidate_ids = {previous, chosen}
        for binding in self._visible_bindings(user_id):
            if (
                binding.kind is BindingKind.DEFAULT_CHOICE and binding.target == previous
                and chosen in binding.key.split()
            ):
                # The key lists every candidate of the original question.
                candidate_ids = {part for part in binding.key.split() if part in by_id}
        await self._async_observe_clarification_choice(
            user_input, [by_id[item] for item in sorted(candidate_ids)], by_id[chosen], area_id,
        )

    def _default_choice(
        self, user_input: Any, candidates: Sequence[EntitySnapshot], area_id: str | None
    ) -> EntitySnapshot | None:
        user_id = conversation_user_id(user_input)
        bindings = [
            item for item in self._visible_bindings(user_id)
            if item.kind is BindingKind.DEFAULT_CHOICE
        ]
        return default_choice_for(candidates, area_id, bindings)
