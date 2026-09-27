"""German realization for V12 proactive messages.

Uses the shared response utilities (``nlu.german_morphology`` for articles
and pronouns, ``entities.format_spoken_number`` for decimal commas,
``productivity.format_duration`` for singular/plural durations and
``nlu.grounded_answer.join_german`` for enumerations).  No V12-specific
grammar or number formatting exists; unknown genders fall back to a
gender-neutral construction instead of a guessed article.
"""

from __future__ import annotations

import re
from datetime import datetime

from .nlu.german_morphology import (
    GrammaticalGender,
    area_gender,
    definite_entity_phrase,
    dative_location_phrase,
    sentence_initial,
)
from .nlu.grounded_answer import join_german
from .productivity import format_duration
from .proactive_model import (
    CommunicationChannel,
    HistoryRecord,
    PriorityLevel,
    ProactiveSituation,
    ProposedGoal,
    SituationKind,
)


_HAZARD_WORDS = {
    "smoke": "Rauch",
    "carbon_monoxide": "Kohlenmonoxid",
    "gas": "Gas",
    "water_leak": "Wasser",
}
_ACCEPT_LABEL = {
    "closed": "Schließen",
    "off": "Ausschalten",
    "on": "Einschalten",
    "open": "Öffnen",
}
_STEP_VERB = {
    "closed": "schließen",
    "off": "ausschalten",
    "on": "einschalten",
    "open": "öffnen",
}
_ACCEPT_VERB = {
    "closed": "schließen",
    "off": "ausschalten",
    "on": "einschalten",
    "open": "öffnen",
}


def finish_sentence(text: str) -> str:
    """Exactly one terminal punctuation mark, no doubled punctuation."""
    stripped = re.sub(r"\s+", " ", text).strip()
    stripped = re.sub(r"([.!?])[.!?]+$", r"\1", stripped)
    if not stripped:
        return ""
    return stripped if stripped[-1] in ".!?" else f"{stripped}."


def join_sentences(parts: list[str] | tuple[str, ...]) -> str:
    return " ".join(finish_sentence(part) for part in parts if part.strip())


def _entity_nominative(name: str) -> tuple[str, str]:
    """Return (sentence-initial nominative phrase, accusative pronoun)."""
    phrase = definite_entity_phrase(name)
    if phrase is None:
        return f"Das Gerät „{name.strip()}“", "es"
    return sentence_initial(phrase[0]), phrase[2]


def _area_nominative(name: str) -> str:
    gender = area_gender(name)
    article = {
        GrammaticalGender.MASCULINE: "Der",
        GrammaticalGender.FEMININE: "Die",
        GrammaticalGender.NEUTER: "Das",
    }.get(gender) if gender is not None else None
    return f"{article} {name.strip()}" if article else f"Der Bereich „{name.strip()}“"


def situation_message(
    situation: ProactiveSituation,
    proposal: ProposedGoal | None,
    *,
    area_name: str | None = None,
) -> tuple[str, str | None]:
    """Return (statement, question or None) for one situation."""
    name = situation.subject_name or (situation.subject_ids[0] if situation.subject_ids else "")
    kind = situation.kind
    if kind is SituationKind.ENTRY_LEFT_OPEN:
        subject, pronoun = _entity_nominative(name)
        statement = f"{subject} ist noch offen"
        question = f"Soll ich {pronoun} schließen?" if proposal is not None else None
        return finish_sentence(statement), question
    if kind is SituationKind.APPLIANCE_FINISHED:
        subject, _pronoun = _entity_nominative(name)
        return finish_sentence(f"{subject} ist fertig"), None
    if kind is SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING:
        location = sentence_initial(dative_location_phrase(area_name or name))
        statement = f"{location} ist noch Licht an, obwohl niemand zu Hause ist"
        question = "Soll ich es ausschalten?" if proposal is not None else None
        return finish_sentence(statement), question
    if kind is SituationKind.THERMAL_GOAL_AT_RISK:
        subject = _area_nominative(area_name or name)
        return finish_sentence(f"{subject} wird voraussichtlich nicht rechtzeitig warm"), None
    if kind is SituationKind.DEVICE_EFFECT_ANOMALY:
        subject, _pronoun = _entity_nominative(name)
        return finish_sentence(f"{subject} reagiert ungewöhnlich langsam oder gar nicht"), None
    if kind is SituationKind.HABIT_OPPORTUNITY:
        band = situation.evidence_value("habit_phrase") or "zu dieser Zeit"
        statement = f"Du machst {band} häufig dieselbe Abfolge"
        if proposal is None:
            return finish_sentence(statement), None
        # The question names every step so "Ja" can never approve a hidden action.
        steps = tuple(
            f"{target.name or target.entity_id} {_STEP_VERB.get(target.desired_state, 'anpassen')}"
            for target in proposal.targets
        )
        statement = join_sentences((statement, f"Als Nächstes folgen meist: {join_german(steps)}"))
        return statement, "Soll ich die Routine starten?"
    if kind is SituationKind.PENDING_GOAL_REQUIRES_ATTENTION:
        label = situation.subject_name or "Ein geplantes Ziel"
        return finish_sentence(f"„{label}“ konnte nicht wie geplant erreicht werden"), None
    hazard = _HAZARD_WORDS.get(situation.evidence_value("hazard") or "", "einen Alarm")
    subject, _pronoun = _entity_nominative(name)
    return finish_sentence(f"Achtung: {subject} meldet {hazard}!"), None


def proposal_label(situation: ProactiveSituation, *, area_name: str | None = None) -> str:
    """Accusative noun phrase naming what a proposal is about."""
    name = situation.subject_name or (situation.subject_ids[0] if situation.subject_ids else "")
    if situation.kind is SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING:
        return f"das Licht {dative_location_phrase(area_name or name)}"
    if situation.kind is SituationKind.HABIT_OPPORTUNITY:
        return "die Routine"
    phrase = definite_entity_phrase(name)
    return phrase[1] if phrase is not None else f"„{name.strip()}“"


def full_message(statement: str, question: str | None) -> str:
    return join_sentences((statement, question) if question else (statement,))


def accept_label(goal: ProposedGoal) -> str:
    states = {target.desired_state for target in goal.targets}
    if len(states) == 1:
        return _ACCEPT_LABEL.get(next(iter(states)), "Ausführen")
    return "Starten"


def grouped_message(items: list[str] | tuple[str, ...]) -> str:
    if len(items) == 1:
        return finish_sentence(items[0])
    return join_sentences(("Kurz zusammengefasst", *items)).replace(
        "Kurz zusammengefasst.", "Kurz zusammengefasst:", 1
    )


def clarification_question(labels: list[str] | tuple[str, ...]) -> str:
    """``labels`` are accusative phrases from ``proposal_label``."""
    phrased = [label for label in labels if label]
    if len(phrased) == 2:
        return finish_sentence(f"Meinst du {phrased[0]} oder {phrased[1]}?")
    return finish_sentence(f"Welche Rückfrage meinst du: {join_german(tuple(phrased))}?")


def outcome_message(goal: ProposedGoal, *, success: bool, status: str) -> str:
    names = tuple(target.name or target.entity_id for target in goal.targets)
    verb = _ACCEPT_VERB.get(goal.targets[0].desired_state, "ausführen") if goal.targets else "ausführen"
    if success:
        if len(names) == 1:
            phrase = definite_entity_phrase(names[0])
            object_text = phrase[1] if phrase is not None else f"„{names[0]}“"
            participle = {
                "schließen": "geschlossen", "ausschalten": "ausgeschaltet",
                "einschalten": "eingeschaltet", "öffnen": "geöffnet",
            }.get(verb, "erledigt")
            return finish_sentence(f"Erledigt. Ich habe {object_text} {participle} und die Wirkung geprüft")
        return finish_sentence("Erledigt. Alle Schritte wurden ausgeführt und geprüft")
    if status == "blocked":
        return finish_sentence("Das darf ich gerade nicht ausführen. Ich habe nichts geschaltet")
    if status == "stale":
        return finish_sentence("Die Situation hat sich inzwischen geändert. Ich habe nichts ausgeführt")
    if status == "conflict":
        return finish_sentence("Ein anderes Ziel verändert gerade dasselbe Gerät. Ich habe nichts ausgeführt")
    return finish_sentence("Die Aktion wurde nicht wie erwartet bestätigt. Ich wiederhole sie nicht automatisch")


def running_message(goal: ProposedGoal) -> str:
    """Immediate answer while the effect is still being verified (F17)."""
    names = tuple(target.name or target.entity_id for target in goal.targets)
    verb = _ACCEPT_VERB.get(goal.targets[0].desired_state, "ausführen") if goal.targets else "ausführen"
    if len(names) == 1:
        phrase = definite_entity_phrase(names[0])
        object_text = phrase[1] if phrase is not None else f"„{names[0]}“"
        particle = _PARTICLE.get(verb, "aus")
        return finish_sentence(
            f"In Ordnung, ich {_FIRST_PERSON.get(verb, 'führe')} {object_text} jetzt"
            f"{' ' + particle if particle else ''}, prüfe die Wirkung und melde mich nur, "
            "falls es nicht klappt"
        )
    return finish_sentence(
        "In Ordnung, ich führe die Schritte jetzt aus, prüfe die Wirkung und melde mich nur, "
        "falls etwas nicht klappt"
    )


_FIRST_PERSON = {"schließen": "schließe", "ausschalten": "schalte", "einschalten": "schalte", "öffnen": "öffne"}
_PARTICLE = {"schließen": "", "ausschalten": "aus", "einschalten": "ein", "öffnen": ""}


_PRIORITY_WORDS = {
    PriorityLevel.INFO: "Information",
    PriorityLevel.SUGGESTION: "Vorschlag",
    PriorityLevel.IMPORTANT: "wichtig",
    PriorityLevel.URGENT: "dringend",
    PriorityLevel.CRITICAL: "kritisch",
}
_CHANNEL_WORDS = {
    CommunicationChannel.VOICE: "per Sprache im Raum",
    CommunicationChannel.PUSH: "per Push-Nachricht",
    CommunicationChannel.INTERACTIVE_PUSH: "per Push-Nachricht mit Antwortknöpfen",
    CommunicationChannel.MULTI_CHANNEL: "per Sprache und Push-Nachricht",
    CommunicationChannel.HISTORY_ONLY: "nur im Verlauf",
    CommunicationChannel.SUPPRESS: "gar nicht",
}
_REASON_WORDS = {
    "exact_room_unique_satellite": "du warst eindeutig in einem Raum mit genau einem Sprachsatelliten",
    "private_push_fallback": "ein Raum war nicht sicher bestimmbar, deshalb privat",
    "no_voice:room_ambiguous": "der Raum war nicht eindeutig",
    "no_voice:room_unknown": "der Raum war unbekannt",
    "no_voice:recipient_not_home": "du warst nicht zu Hause",
    "no_voice:quiet_hours": "es war Ruhezeit",
    "no_voice:personal_audience_may_be_shared": "der Inhalt ist persönlich und weitere Personen waren zu Hause",
    "no_voice:multiple_satellites_in_area": "im Raum gibt es mehrere Sprachsatelliten",
    "no_voice:no_satellite_in_area": "im Raum gibt es keinen Sprachsatelliten",
    "within_attention_budget": "es gab zuletzt nicht zu viele Hinweise",
    "critical_bypasses_attention": "kritische Hinweise umgehen jede Hinweisbegrenzung",
    "priority_escalation": "die Priorität ist dringend oder kritisch",
    "quiet_hours_private_route": "es war Ruhezeit, deshalb nur privat",
}


def explain_record(record: HistoryRecord, *, local_time: datetime) -> str:
    """Explain a past decision strictly from stored evidence and reason codes."""
    parts: list[str] = []
    subject = record.subject_label or "unbekannt"
    parts.append(
        f"Ich habe dich um {local_time:%H:%M} Uhr {_CHANNEL_WORDS.get(record.channel, '')} "
        f"auf die Situation „{subject}“ hingewiesen"
    )
    open_since = next((item.value for item in record.evidence if item.code == "open_since"), None)
    if open_since is not None:
        try:
            started = datetime.fromisoformat(open_since)
            seconds = int((record.timestamp - started).total_seconds())
            if seconds > 0:
                parts.append(f"Der Zustand bestand zu dem Zeitpunkt seit {format_duration(seconds)}")
        except ValueError:
            pass
    parts.append(f"Priorität: {_PRIORITY_WORDS.get(record.priority, 'unbekannt')}")
    readable = [_REASON_WORDS[item] for item in record.reasons if item in _REASON_WORDS]
    if readable:
        parts.append("Gründe: " + "; ".join(dict.fromkeys(readable)))
    if record.model_ref:
        parts.append(f"Verwendete Lernevidenz: {record.model_ref}")
    if record.acknowledgement:
        answer = _ACKNOWLEDGEMENT_WORDS.get(record.acknowledgement, record.acknowledgement)
        parts.append(f"Antwort: {answer}")
    return join_sentences(parts)


# Stored acknowledgement codes spoken in German (F16).
_ACKNOWLEDGEMENT_WORDS = {
    "accepted": "angenommen", "accept": "angenommen", "declined": "abgelehnt",
    "rejected": "abgelehnt", "reject": "abgelehnt", "ignore": "ignoriert",
    "snoozed": "später erinnern", "later": "später erinnern", "dismissed": "verworfen",
    "muted": "künftig nicht mehr melden", "expired": "abgelaufen", "cancelled": "abgebrochen",
    "acknowledged": "zur Kenntnis genommen",
}


__all__ = (
    "accept_label",
    "clarification_question",
    "explain_record",
    "finish_sentence",
    "full_message",
    "grouped_message",
    "join_sentences",
    "outcome_message",
    "running_message",
    "proposal_label",
    "situation_message",
)
