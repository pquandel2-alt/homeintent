"""V12 conversation turns, arbitrated by the central ``DialogManager``.

Dialog authority rule: a reply belongs to exactly one active dialog.

* Any pending legacy dialog (timer naming/selection/delete-all, automation,
  security/service confirmation, alias, clarification) or any open
  ``DialogManager`` task owned by another feature is answered by that
  feature first.  V12 never sees such a turn.
* Only an otherwise unclaimed bare reply ("Ja", "Nein", "Später",
  "Ignorieren", "Erinnere mich in 20 Minuten") may address a pending V12
  proposal, and only when it identifies exactly one proposal for this
  caller; otherwise V12 asks which one is meant.
* V12's own follow-ups (which proposal, permission preview, mute preview)
  are ``DialogManager`` tasks with explicit owners, so "Nein" is always
  interpreted in the context of the dialog that asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence, cast

from .dialog_manager import DialogManager, DialogPriority, DialogTask, DialogTaskKind
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from .nlu.grounded_answer import join_german
from .proactive_engine import ProactiveContextEngine, ReplyResult
from .proactive_model import ProposalChoice, SituationKind
from .proactive_messages import finish_sentence
from .proactive_session import classify_proposal_reply
from .standing_permission import (
    PermissionDraft,
    looks_like_permission_request,
    parse_permission_request,
)


V12_TASK_KINDS = frozenset({
    DialogTaskKind.PROACTIVE_CLARIFICATION,
    DialogTaskKind.STANDING_PERMISSION_CONFIRMATION,
    DialogTaskKind.PROACTIVE_MUTE_CONFIRMATION,
})
_CLARIFY_TASK = "v12-proposal-clarification"
_PERMISSION_TASK = "v12-standing-permission"
_MUTE_TASK = "v12-mute"

_EXPLAIN_RE = re.compile(
    r"^warum\s+hast\s+du\s+(?:mich|uns)\s+(?:(?:wegen|zu|ueber)\s+(?P<subject>.+?)\s+)?"
    r"(?:angesprochen|informiert|benachrichtigt|gefragt|gewarnt|erinnert)\s*\??$"
)
_HISTORY_RE = re.compile(
    r"^(?:welche\s+(?:hinweise|meldungen|warnungen)\s+(?:gab\s+es|hattest\s+du|hast\s+du\s+(?:mir\s+)?gegeben)"
    r"|was\s+hast\s+du\s+(?:mir\s+)?(?:heute\s+)?(?:proaktiv\s+)?gemeldet)"
    r"(?:\s+heute)?\s*\??$"
)
_LIST_PERMISSIONS_RE = re.compile(
    r"^(?:welche\s+(?:automatischen\s+)?(?:erlaubnisse|daueranweisungen|dauererlaubnisse)\s+"
    r"(?:gibt\s+es|hast\s+du|sind\s+(?:aktiv|gespeichert))"
    r"|was\s+(?:darfst|machst|tust)\s+du\s+(?:alles\s+)?(?:ohne\s+(?:rueckfrage|nachfrage|"
    r"(?:mich\s+)?(?:zu\s+)?fragen)|automatisch|von\s+selbst|selbststaendig))\s*\??$"
)
_REVOKE_ALL_RE = re.compile(
    r"^(?:widerrufe|loesche|entferne)\s+alle\s+(?:automatischen\s+)?"
    r"(?:erlaubnisse|daueranweisungen|dauererlaubnisse)\s*[.!]?$"
)
_MUTE_RE = re.compile(
    r"^(?:sag|melde|erzaehl)\s+(?:mir\s+)?(?:das|so\s+etwas|sowas|solche\s+hinweise)\s+"
    r"(?:kuenftig|in\s+zukunft|zukuenftig)\s+nicht\s+mehr\s*[.!]?$"
)
_STOP_WORDS = frozenset({"der", "die", "das", "dem", "den", "des", "wegen", "zu", "ueber", "im", "in"})


@dataclass(frozen=True)
class DialogOutcome:
    speech: str
    # Read-only answers (instructions, history, explanations) are questions
    # for clients and satellites, not executed actions (F22).
    query: bool = False


class ProactiveDialogHandler:
    def __init__(self, engine: ProactiveContextEngine, dialogs: DialogManager) -> None:
        self._engine = engine
        self._dialogs = dialogs

    # -- turns V12 owns ------------------------------------------------------
    async def async_handle_owned_turn(
        self,
        text: str,
        *,
        conversation_id: str,
        user_id: str | None,
        is_admin: bool,
        entities: Iterable[EntitySnapshot],
        area_lookup: Mapping[str, str],
    ) -> DialogOutcome | None:
        """Answer an open V12 task or a fresh explicit V12 command.

        Returns ``None`` when the turn is not V12's (including when another
        feature owns the active dialog task).
        """
        local_now = self._engine.ports.local_now()
        task = self._dialogs.active(conversation_id)
        if task is not None:
            if task.kind not in V12_TASK_KINDS:
                return None
            if task.requested_by_user_id is not None and task.requested_by_user_id != user_id:
                return DialogOutcome("Diese Rückfrage gehört zu einem anderen Benutzer.")
            if task.kind is DialogTaskKind.PROACTIVE_CLARIFICATION:
                return await self._answer_clarification(task, text, conversation_id, user_id, is_admin)
            if task.kind is DialogTaskKind.STANDING_PERMISSION_CONFIRMATION:
                return await self._answer_permission(task, text, conversation_id, local_now)
            return await self._answer_mute(task, text, conversation_id, local_now)
        if not self._engine.config.enabled:
            return None
        normalized = normalize_for_compare(text).strip()
        normalized = re.sub(r"[,;]+", " ", normalized)
        normalized = " ".join(normalized.split())
        if looks_like_permission_request(text):
            return self._start_permission(text, conversation_id, user_id, entities, area_lookup, local_now)
        if _LIST_PERMISSIONS_RE.fullmatch(normalized):
            return DialogOutcome(self._list_permissions(user_id, local_now), query=True)
        if _REVOKE_ALL_RE.fullmatch(normalized):
            if user_id is None:
                return DialogOutcome("Ich kann Daueranweisungen nur einem angemeldeten Benutzer zuordnen.")
            count = self._engine.permissions.revoke_all(user_id)
            await self._engine.async_persist()
            noun = "Daueranweisung" if count == 1 else "Daueranweisungen"
            return DialogOutcome(finish_sentence(f"Ich habe {count} {noun} widerrufen"))
        match = _EXPLAIN_RE.fullmatch(normalized)
        if match is not None:
            subject = match.group("subject") or ""
            words = tuple(
                word for word in re.split(r"[^a-z0-9]+", subject)
                if word and word not in _STOP_WORDS
            )
            return DialogOutcome(self._engine.explain_latest(words, user_id=user_id), query=True)
        if _HISTORY_RE.fullmatch(normalized):
            midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            return DialogOutcome(
                self._engine.history_summary(since=midnight, user_id=user_id), query=True
            )
        if _MUTE_RE.fullmatch(normalized):
            return self._start_mute(conversation_id, user_id)
        return None

    # -- bare replies to proposals -----------------------------------------
    async def async_handle_bare_reply(
        self,
        text: str,
        *,
        conversation_id: str,
        user_id: str | None,
        device_id: str | None,
        is_admin: bool,
        other_open_questions: int,
    ) -> DialogOutcome | None:
        """Called only when no other dialog claimed the turn."""
        if self._dialogs.has_open_question(conversation_id):
            return None
        result = await self._engine.async_handle_reply(
            text, user_id=user_id, device_id=device_id, is_admin=is_admin,
            other_open_questions=other_open_questions,
        )
        if result is None:
            return None
        if result.clarification_ids:
            reply = classify_proposal_reply(text)
            self._dialogs.create(
                conversation_id, _CLARIFY_TASK, DialogTaskKind.PROACTIVE_CLARIFICATION,
                DialogPriority.SELECTION,
                slots={
                    "proposal_ids": result.clarification_ids,
                    "choice": (reply.choice if reply is not None else ProposalChoice.ACCEPT).value,
                },
                reason="Mehrere proaktive Rückfragen sind offen.",
                requested_by_user_id=user_id,
            )
        return DialogOutcome(result.speech)

    # -- internals -------------------------------------------------------------
    async def _answer_clarification(
        self, task: DialogTask, text: str, conversation_id: str, user_id: str | None, is_admin: bool,
    ) -> DialogOutcome | None:
        ids = tuple(
            item for item in cast(Sequence[object], task.slots.get("proposal_ids", ()))
            if isinstance(item, str)
        )
        confirmation = classify_confirmation_reply(text)
        if confirmation is ConfirmationReply.NO or normalize_for_compare(text).strip(" .!") in {"keine", "keins", "keinen"}:
            self._dialogs.cancel(conversation_id, task.task_id)
            return DialogOutcome("In Ordnung. Ich habe nichts ausgeführt; die Rückfragen bleiben offen.")
        try:
            choice = ProposalChoice(str(task.slots.get("choice", ProposalChoice.ACCEPT.value)))
        except ValueError:
            choice = ProposalChoice.ACCEPT
        result: ReplyResult | None = await self._engine.async_select_and_apply(
            ids, text, choice, user_id=user_id, is_admin=is_admin,
        )
        if result is None:
            if len(text.split()) > 3:
                # A full new sentence replaces the V12 question instead of being
                # misread as a selection.
                self._dialogs.cancel(conversation_id, task.task_id)
                return None
            return DialogOutcome("Das ist noch nicht eindeutig. Bitte nenne das Gerät, zum Beispiel „die Garage“.")
        self._dialogs.cancel(conversation_id, task.task_id)
        return DialogOutcome(result.speech)

    def _start_permission(
        self, text: str, conversation_id: str, user_id: str | None,
        entities: Iterable[EntitySnapshot], area_lookup: Mapping[str, str], local_now: datetime,
    ) -> DialogOutcome:
        if not self._engine.config.standing_permissions_enabled:
            return DialogOutcome(
                "Daueranweisungen sind in den HomeIntent-Optionen deaktiviert. Ich habe nichts gespeichert."
            )
        parsed = parse_permission_request(
            text, owner_user_id=user_id, entities=entities, area_lookup=area_lookup,
            now=local_now,
        )
        if parsed.error == "never_auto":
            return DialogOutcome(
                "Tore, Garagen, Türen, Schlösser, Alarmanlagen und ähnlich kritische Geräte schalte ich "
                "nie automatisch. Ich frage dich in solchen Situationen stattdessen. Ich habe nichts gespeichert."
            )
        if parsed.error == "owner_unknown":
            return DialogOutcome("Eine Daueranweisung braucht einen angemeldeten Benutzer. Ich habe nichts gespeichert.")
        if parsed.error == "area_unknown":
            return DialogOutcome("Den Raum konnte ich nicht eindeutig zuordnen. Ich habe nichts gespeichert.")
        if parsed.error == "no_lights_in_area":
            return DialogOutcome("In diesem Raum kenne ich kein Licht. Ich habe nichts gespeichert.")
        if parsed.draft is None:
            return DialogOutcome(
                "Diese Daueranweisung kann ich nicht vollständig und sicher abbilden. "
                "Ich habe weder eine Erlaubnis noch eine Automation gespeichert."
            )
        draft = parsed.draft
        self._dialogs.create(
            conversation_id, _PERMISSION_TASK, DialogTaskKind.STANDING_PERMISSION_CONFIRMATION,
            DialogPriority.CONFIRMATION, slots={"draft": draft},
            reason="Eine Daueranweisung wartet auf ausdrückliche Bestätigung.",
            requested_by_user_id=user_id,
        )
        days = (draft.expires_at - local_now).days
        return DialogOutcome(
            f"Vorschau: Wenn niemand zu Hause ist und {_location(draft)} noch Licht an ist, "
            f"schalte ich {join_german(draft.entity_names)} automatisch aus. "
            "Jede Ausführung prüft vorher den aktuellen Zustand und die Sicherheitsregeln. "
            f"Die Erlaubnis gilt {days} Tage. Soll ich das so speichern?"
        )

    async def _answer_permission(
        self, task: DialogTask, text: str, conversation_id: str, local_now: datetime,
    ) -> DialogOutcome | None:
        reply = classify_confirmation_reply(text)
        draft = task.slots.get("draft")
        if reply is ConfirmationReply.NO:
            self._dialogs.cancel(conversation_id, task.task_id)
            return DialogOutcome("In Ordnung. Die Daueranweisung wurde nicht gespeichert.")
        if reply is not ConfirmationReply.YES or not isinstance(draft, PermissionDraft):
            if len(text.split()) > 3:
                self._dialogs.cancel(conversation_id, task.task_id)
                return None
            return DialogOutcome("Bitte bestätige die Daueranweisung eindeutig mit Ja oder Nein.")
        self._dialogs.cancel(conversation_id, task.task_id)
        permission = draft.to_permission(self._engine.ports.now())
        try:
            stored = self._engine.permissions.add(permission)
        except ValueError as err:
            return DialogOutcome(finish_sentence(str(err)))
        await self._engine.async_persist()
        if stored.permission_id != permission.permission_id:
            return DialogOutcome(
                "Diese Daueranweisung gab es schon; ich habe sie verlängert. Ich schalte das "
                "Licht nur aus, wenn zu diesem Zeitpunkt wirklich niemand zu Hause ist, "
                "und prüfe die Wirkung."
            )
        return DialogOutcome(
            "Gespeichert. Ich schalte das Licht nur aus, wenn zu diesem Zeitpunkt wirklich niemand "
            "zu Hause ist, und prüfe die Wirkung."
        )

    def _start_mute(self, conversation_id: str, user_id: str | None) -> DialogOutcome:
        if user_id is None:
            return DialogOutcome("Ich kann diese Einstellung nur einem angemeldeten Benutzer zuordnen.")
        record = next(
            (item for item in reversed(self._engine.history.records())
             if item.addressed_to(user_id) and item.result == "delivered"),
            None,
        )
        if record is None:
            return DialogOutcome("Ich habe dich zuletzt auf nichts hingewiesen, das ich abschalten könnte.")
        if record.situation_kind is SituationKind.CRITICAL_SAFETY_EVENT:
            return DialogOutcome("Sicherheitswarnungen kann ich nicht abschalten.")
        self._dialogs.create(
            conversation_id, _MUTE_TASK, DialogTaskKind.PROACTIVE_MUTE_CONFIRMATION,
            DialogPriority.CONFIRMATION, slots={"kind": record.situation_kind.value},
            reason="Eine dauerhafte Hinweis-Einstellung wartet auf Bestätigung.",
            requested_by_user_id=user_id,
        )
        return DialogOutcome(
            f"Soll ich dich künftig nicht mehr auf Situationen wie „{record.subject_label}“ hinweisen? "
            "Sicherheitswarnungen bleiben immer aktiv."
        )

    async def _answer_mute(
        self, task: DialogTask, text: str, conversation_id: str, local_now: datetime,
    ) -> DialogOutcome | None:
        reply = classify_confirmation_reply(text)
        if reply is ConfirmationReply.NO:
            self._dialogs.cancel(conversation_id, task.task_id)
            return DialogOutcome("In Ordnung. Die Hinweise bleiben aktiv.")
        if reply is not ConfirmationReply.YES or task.requested_by_user_id is None:
            if len(text.split()) > 3:
                self._dialogs.cancel(conversation_id, task.task_id)
                return None
            return DialogOutcome("Bitte antworte mit Ja oder Nein.")
        self._dialogs.cancel(conversation_id, task.task_id)
        kind = SituationKind(str(task.slots.get("kind")))
        self._engine.attention_state.mute(task.requested_by_user_id, kind, self._engine.ports.now())
        await self._engine.async_persist()
        return DialogOutcome("Gespeichert. Solche Hinweise bekommst du künftig nur noch im Verlauf.")

    def _list_permissions(self, user_id: str | None, local_now: datetime) -> str:
        active = [
            item for item in self._engine.permissions.active(self._engine.ports.now())
            if user_id is None or item.owner_user_id == user_id
        ]
        if not active:
            return "Es sind keine Daueranweisungen gespeichert."
        parts = [
            item.description or "Licht ausschalten, wenn niemand zu Hause ist"
            for item in active
        ]
        noun = "Daueranweisung" if len(parts) == 1 else "Daueranweisungen"
        return finish_sentence(f"{len(parts)} {noun} aktiv: {join_german(tuple(parts))}")


def _location(draft: PermissionDraft) -> str:
    from .nlu.german_morphology import dative_location_phrase

    return dative_location_phrase(draft.area_name)


__all__ = ("DialogOutcome", "ProactiveDialogHandler", "V12_TASK_KINDS")
