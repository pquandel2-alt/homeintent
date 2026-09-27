"""Compositional V10 goal understanding after the shared V8/V9 frontend."""

from __future__ import annotations

import hashlib
import uuid
from typing import Iterable, Mapping

from .goal_model import (
    DeliveryChannel, DesiredState, GoalCondition, GoalKind, GoalLifecycle,
    GoalModel, GoalProvenance, GoalScope, GoalTrigger, IntentClass,
    NotificationSeverity, SuccessCriterion, TemporalGoal,
)
from .nlu.german_structure import ClauseKind
from .nlu.language_frontend import LanguageDocument
from .nlu.temporal_semantics import TemporalKind
from .nlu.semantic_utterance import SpeechAct


_PREPARE = frozenset({"bereite", "vorbereiten", "mach", "mache", "fertig"})
_NOTIFY = frozenset({"sag", "sage", "informiere", "benachrichtige", "ping", "meld"})
_LEAVE = frozenset({"gehe", "weg", "verlasse", "verlaesst", "verlassen", "raus"})
_ARRIVE = frozenset({"kommt", "komme", "ankommt", "ankomme", "heimkommt", "zurueckkehrt"})
_OPEN = frozenset({"offen", "offene", "offener", "offenes", "offenen", "auf", "aufsteht"})
_WINDOW = frozenset({"fenster", "fenstern", "fensterkontakt", "fensterkontakte"})
_LIGHT = frozenset({"licht", "lichter", "lampe", "lampen", "leuchte", "leuchten"})


def classify_intent(
    document: LanguageDocument, routine_names: Mapping[str, str] | None = None
) -> IntentClass:
    """Classify the requested semantic product, not just its leading verb."""
    words = _words(document)
    has_condition = any(
        clause.kind is ClauseKind.CONDITION for clause in document.structure.clauses
    ) or bool(words & {"wenn", "sobald", "falls"})
    if has_condition and words & _NOTIFY:
        return IntentClass.GOAL
    if _is_result_temperature_goal(words):
        return IntentClass.GOAL
    if _is_failure_explanation(words):
        return IntentClass.GOAL
    if words & {"angenehmer", "gemuetlicher", "komfortabler"}:
        return IntentClass.GOAL
    if words & _PREPARE and words & {"schlafengehen", "filmabend", "nacht", "abwesenheit"}:
        return IntentClass.GOAL
    if routine_names and _stored_routine(document, routine_names) is not None and (
        words & _PREPARE or words & _START_ROUTINE
    ):
        return IntentClass.GOAL
    if document.utterance.speech_act is SpeechAct.QUERY:
        return IntentClass.QUERY
    if has_condition:
        return IntentClass.AUTOMATION
    return IntentClass.COMMAND


def interpret_goal(
    document: LanguageDocument,
    *,
    current_user_id: str | None = None,
    conversation_id: str | None = None,
    current_person_entity_id: str | None = None,
    voice_area_id: str | None = None,
    household_person_ids: Iterable[str] = (),
    person_name_bindings: Mapping[str, tuple[str, ...]] | None = None,
    routine_names: Mapping[str, str] | None = None,
    area_names: Mapping[str, str] | None = None,
) -> GoalModel | None:
    """Build one typed goal without selecting services or guessing bindings.

    ``routine_names`` maps normalized names of the caller's stored routines
    to their ids and ``area_names`` maps normalized area names and aliases of
    the Area Registry to area ids (F14); without them only the built-in
    vocabulary is known.
    """
    words = _words(document)
    stored_routine = _stored_routine(document, routine_names or {})
    if stored_routine is not None and (words & _PREPARE or words & _START_ROUTINE):
        return GoalModel(
            GoalKind.PREPARE_ROUTINE,
            {"routine_name": stored_routine, "excluded_area_names": _exclusion_names(document)},
            goal_id=f"goal_{uuid.uuid4().hex}",
            routine_id=stored_routine,
            confirmation_required=True,
            provenance=GoalProvenance(
                document.source_text,
                current_user_id,
                conversation_id,
                semantic_graph_ref=_semantic_reference(document),
            ),
        )
    provenance = GoalProvenance(
        document.source_text,
        current_user_id,
        conversation_id,
        semantic_graph_ref=_semantic_reference(document),
    )
    goal_id = f"goal_{uuid.uuid4().hex}"

    if _is_failure_explanation(words):
        routine_id = _routine_name(words)
        monitor_hint = bool(words & {"meldung", "benachrichtigung", "push", "fensterwarnung"})
        parameters: dict[str, object] = {"failed_only": not monitor_hint}
        if routine_id is not None:
            parameters["routine_id"] = routine_id
        if monitor_hint:
            parameters["goal_kind"] = GoalKind.MONITOR_AND_NOTIFY.value
        return GoalModel(
            GoalKind.EXPLAIN_FAILURE,
            parameters,
            goal_id=goal_id,
            routine_id=routine_id,
            provenance=provenance,
        )

    monitor = _monitor_goal(
        words,
        document,
        goal_id=goal_id,
        provenance=provenance,
        current_person_entity_id=current_person_entity_id,
        household_person_ids=tuple(household_person_ids),
        person_name_bindings=person_name_bindings or {},
    )
    if monitor is not None:
        return monitor

    if _is_result_temperature_goal(words):
        value = _temperature_value(document)
        scope = GoalScope(domain="climate", area_id=_spoken_area(words, document, area_names))
        criteria = () if value is None else (
            SuccessCriterion(
                "measured-room-temperature", "measured_property", scope,
                {"property": "temperature", "value": value, "unit": "°C"},
            ),
        )
        return GoalModel(
            GoalKind.SCHEDULED,
            goal_id=goal_id,
            scope=scope,
            desired_states=() if value is None else (DesiredState("temperature", value, "°C"),),
            temporal=_temporal_goal(document, result_deadline=True),
            confirmation_required=True,
            success_criteria=criteria,
            failure_handling="clarify_without_thermal_model",
            provenance=provenance,
        )

    if words & {"angenehmer", "gemuetlicher", "komfortabler"}:
        comfort_kind = (
            GoalKind.IMPROVE_COMFORT if "komfortabler" in words else GoalKind.COMFORT
        )
        return GoalModel(
            comfort_kind,
            goal_id=goal_id,
            scope=GoalScope(area_id=voice_area_id),
            profile_id=f"comfort:{voice_area_id}" if voice_area_id else None,
            confirmation_required=True,
            provenance=provenance,
        )

    routine = _routine_name(words)
    if routine is not None and words & _PREPARE:
        legacy_kind = {
            "schlafengehen": GoalKind.PREPARE_NIGHT,
            "filmabend": GoalKind.PREPARE_MOVIE,
            "abwesenheit": GoalKind.PREPARE_AWAY,
        }[routine]
        excluded_area_names = _exclusion_names(document)
        return GoalModel(
            legacy_kind,
            {"routine_name": routine, "excluded_area_names": excluded_area_names},
            goal_id=goal_id,
            routine_id=routine,
            confirmation_required=True,
            provenance=provenance,
        )

    if words & {"sichere", "verschliesse"} and words & {"haus", "wohnung", "tueren", "fenster"}:
        return GoalModel(GoalKind.SECURE_HOME, goal_id=goal_id, provenance=provenance)
    if words & {"pausiere", "stoppe", "mach"} and words & {"medien", "wiedergaben", "musik"}:
        return GoalModel(GoalKind.QUIET_MEDIA, goal_id=goal_id, provenance=provenance)
    if "unbesetzte" in words and words & {"bereiche", "raeume"} and "energiesparend" in words:
        return GoalModel(GoalKind.SAVE_UNOCCUPIED, goal_id=goal_id, provenance=provenance)
    if words & {"untersuche", "untersuchen"} and words & {"zustand", "ursache"}:
        return GoalModel(GoalKind.INVESTIGATE_STATE, goal_id=goal_id, provenance=provenance)
    return None


def _monitor_goal(
    words: frozenset[str],
    document: LanguageDocument,
    *,
    goal_id: str,
    provenance: GoalProvenance,
    current_person_entity_id: str | None,
    household_person_ids: tuple[str, ...],
    person_name_bindings: Mapping[str, tuple[str, ...]],
) -> GoalModel | None:
    has_condition = bool(words & {"wenn", "sobald", "falls"}) or any(
        clause.kind is ClauseKind.CONDITION for clause in document.structure.clauses
    )
    if not has_condition or not words & _NOTIFY:
        return None
    nobody = "niemand" in words and bool(words & {"zuhause", "daheim"})
    first_person = bool(words & {"ich", "mir", "mich"})
    leaving = bool(words & _LEAVE) or {"nicht", "zuhause"} <= words
    arriving = bool(words & _ARRIVE)
    named_person, ambiguous_name = _named_person(document, person_name_bindings)
    if nobody:
        trigger = GoalTrigger(
            "nobody_home", zone_id="home", household_person_ids=household_person_ids
        )
    elif named_person is not None and leaving:
        trigger = GoalTrigger(
            "person_leaves_zone", named_person, "home", "home", "not_home"
        )
    elif named_person is not None and arriving:
        trigger = GoalTrigger(
            "person_arrives_zone", named_person, "home", "not_home", "home"
        )
    elif ambiguous_name is not None and (leaving or arriving):
        trigger = GoalTrigger("person_reference_ambiguous")
    elif first_person and leaving:
        trigger = GoalTrigger(
            "person_leaves_zone", current_person_entity_id, "home", "home", "not_home"
        )
    elif first_person and arriving:
        trigger = GoalTrigger(
            "person_arrives_zone", current_person_entity_id, "home", "not_home", "home"
        )
    else:
        return None

    condition: GoalCondition | None = None
    severity = NotificationSeverity.INFO
    if words & _WINDOW and words & _OPEN:
        condition = GoalCondition(
            "open_entities", GoalScope(domain="binary_sensor", device_class="window"),
            "non_empty", True, True,
        )
        severity = NotificationSeverity.WARNING
    elif words & {"garage", "garagentor"} and words & _OPEN:
        condition = GoalCondition(
            "open_entities", GoalScope(domain="cover", device_class="garage"),
            "non_empty", True, True,
        )
        severity = NotificationSeverity.WARNING
    elif words & _LIGHT and words & {"an", "brennt", "brennen"}:
        condition = GoalCondition(
            "lights_on", GoalScope(domain="light"), "non_empty", True, True
        )
        severity = NotificationSeverity.WARNING
    elif words & {"haustuer", "tuer"} and words & {"verriegelt", "abgeschlossen"}:
        condition = GoalCondition(
            "state_not_equals", GoalScope(domain="lock"), "not_equals", "locked", True
        )
        severity = NotificationSeverity.WARNING
    if condition is None:
        return None
    recipients = ()
    if words & {"mir", "mich"} and current_person_entity_id is not None:
        recipients = (current_person_entity_id,)
    elif words & {"ihr", "ihm"} and named_person is not None:
        recipients = (named_person,)
    elif "uns" in words:
        recipients = household_person_ids
    elif first_person and current_person_entity_id is not None:
        recipients = (current_person_entity_id,)
    return GoalModel(
        GoalKind.MONITOR_AND_NOTIFY,
        {"ambiguous_person_name": ambiguous_name} if ambiguous_name else {},
        goal_id=goal_id,
        trigger=trigger,
        conditions=(condition,),
        recipient_person_ids=recipients,
        delivery_channel=DeliveryChannel.PUSH,
        notification_severity=severity,
        success_criteria=(
            SuccessCriterion("notification-if-condition", "conditional_delivery", expected=True),
        ),
        provenance=provenance,
        lifecycle=GoalLifecycle.MONITOR,
    )


def _named_person(
    document: LanguageDocument,
    bindings: Mapping[str, tuple[str, ...]],
) -> tuple[str | None, str | None]:
    normalized = f" {document.normalized_text.casefold()} "
    matches: list[tuple[str, tuple[str, ...]]] = []
    for name, entity_ids in bindings.items():
        candidate = name.casefold().strip()
        if candidate and f" {candidate} " in normalized:
            matches.append((candidate, entity_ids))
    resolved = {entity_id for _name, values in matches for entity_id in values}
    if len(resolved) == 1:
        return next(iter(resolved)), None
    if matches:
        return None, matches[0][0]
    return None, None


def _is_result_temperature_goal(words: frozenset[str]) -> bool:
    return (
        bool(words & {"sorge", "dafuer"})
        and bool(words & {"warm", "temperatur", "grad"})
        and bool(words & {"ist", "hat", "erreicht"})
    )


def _is_failure_explanation(words: frozenset[str]) -> bool:
    asks_why = bool(words & {"warum", "wieso"})
    failure_language = bool(
        words
        & {
            "funktioniert", "geklappt", "schiefgegangen", "fehler", "fehlgeschlagen",
            "meldung", "benachrichtigung", "ausgeloest", "zugestellt", "blockiert",
        }
    )
    negative_delivery = bool(words & {"keine", "nicht", "nie"}) and bool(
        words & {"meldung", "benachrichtigung", "push", "ausgeloest"}
    )
    failed_state = asks_why and "nicht" in words and bool(
        words & {"ging", "ginge", "blieb", "war", "wurde", "aus", "an"}
    )
    return (asks_why and failure_language) or negative_delivery or failed_state


def _temperature_value(document: LanguageDocument) -> float | None:
    tokens = document.tokens
    for index, token in enumerate(tokens[:-1]):
        if token.is_number and tokens[index + 1].canonical in {"grad", "°"}:
            try:
                return float(token.canonical.replace(",", "."))
            except ValueError:
                return None
    return None


def _temporal_goal(document: LanguageDocument, *, result_deadline: bool) -> TemporalGoal | None:
    absolute = next(
        (item.value for item in document.temporal if item.kind is TemporalKind.ABSOLUTE_TIME),
        None,
    )
    date = next(
        (item.value for item in document.temporal if item.kind is TemporalKind.DATE),
        None,
    )
    if absolute is None and date is None:
        return None
    return TemporalGoal(
        day_part=" ".join(item for item in (date, absolute) if item),
        must_be_achieved_by_deadline=result_deadline,
    )


def _routine_name(words: frozenset[str]) -> str | None:
    if words & {"schlafengehen", "nacht"}:
        return "schlafengehen"
    if "filmabend" in words or ({"film", "abend"} <= words):
        return "filmabend"
    if "abwesenheit" in words:
        return "abwesenheit"
    return None


def _spoken_area(
    words: frozenset[str],
    document: LanguageDocument | None = None,
    area_names: Mapping[str, str] | None = None,
) -> str | None:
    if area_names and document is not None:
        # Area Registry names and aliases, longest first ("Flur oben" before
        # "Flur"), matched on whole words of the utterance (F14).
        text = f" {' '.join(token.canonical for token in document.tokens if token.is_word)} "
        for name in sorted(area_names, key=len, reverse=True):
            if name and f" {name} " in text:
                return area_names[name]
        return None
    for candidate in ("wohnzimmer", "schlafzimmer", "kueche", "bad"):
        if candidate in words:
            return candidate
    return None


_START_ROUTINE = frozenset({"starte", "start", "fuehre", "aktiviere", "routine", "beginne"})


def _stored_routine(document: LanguageDocument, routine_names: Mapping[str, str]) -> str | None:
    """The id of the one stored routine named in the utterance, if any."""
    if not routine_names:
        return None
    text = f" {' '.join(token.canonical for token in document.tokens if token.is_word)} "
    matches = {
        routine_id
        for name, routine_id in routine_names.items()
        if name and f" {name} " in text
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _exclusion_names(document: LanguageDocument) -> tuple[str, ...]:
    result: list[str] = []
    tokens = document.tokens
    for index, token in enumerate(tokens[:-1]):
        if token.canonical == "ausser":
            tail = [
                item.canonical for item in tokens[index + 1:]
                if item.is_word and item.canonical not in {"dem", "der", "den", "das", "im"}
            ]
            if tail:
                result.append(" ".join(tail))
        if token.canonical == "kinderzimmer" and _preceded_by_exclusion(tokens, index):
            result.append("kinderzimmer")
    return tuple(dict.fromkeys(result))


def _preceded_by_exclusion(tokens: tuple[object, ...], index: int) -> bool:
    canonical = [getattr(item, "canonical", "") for item in tokens[max(0, index - 4):index]]
    return any(item in {"nicht", "ruhe", "ausser"} for item in canonical)


def _words(document: LanguageDocument) -> frozenset[str]:
    return frozenset(token.canonical for token in document.tokens if token.is_word)


def _semantic_reference(document: LanguageDocument) -> str:
    material = "|".join(
        (
            document.normalized_text,
            ",".join(clause.kind.name for clause in document.structure.clauses),
            ",".join(span.kind.name for span in document.semantics.spans),
        )
    )
    return "semantic:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


__all__ = ("classify_intent", "interpret_goal")
