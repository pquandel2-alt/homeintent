"""Shared rendering and resolution for entity clarification turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from ..areas import AreaResolveStatus, resolve_area_name
from ..entities import EntitySnapshot, ResolveStatus, normalize_for_compare, resolve_entity


_DOMAIN_LABELS = {
    "light": ("Lichter", "Welches meinst du?"),
    "switch": ("Schalter", "Welchen meinst du?"),
    "cover": ("Rollläden", "Welchen meinst du?"),
    "fan": ("Ventilatoren", "Welchen meinst du?"),
    "climate": ("Heizungen", "Welche meinst du?"),
    "sensor": ("Sensoren", "Welchen meinst du?"),
    "media_player": ("Mediengeräte", "Welches meinst du?"),
    "script": ("Skripte", "Welches meinst du?"),
    "vacuum": ("Saugroboter", "Welchen meinst du?"),
    "scene": ("Szenen", "Welche meinst du?"),
    "todo": ("Listen", "Welche meinst du?"),
    "timer": ("Timer", "Welchen meinst du?"),
    "calendar": ("Kalender", "Welchen meinst du?"),
}
# Singular genus for "Welche Szene meinst du: …?" (7.6.1).
_DOMAIN_SINGULAR = {
    "light": ("Welches", "Licht"),
    "switch": ("Welchen", "Schalter"),
    "cover": ("Welchen", "Rollladen"),
    "fan": ("Welchen", "Ventilator"),
    "climate": ("Welche", "Heizung"),
    "sensor": ("Welchen", "Sensor"),
    "media_player": ("Welches", "Mediengerät"),
    "script": ("Welches", "Skript"),
    "vacuum": ("Welchen", "Saugroboter"),
    "scene": ("Welche", "Szene"),
    "todo": ("Welche", "Liste"),
    "timer": ("Welchen", "Timer"),
    "calendar": ("Welchen", "Kalender"),
    "automation": ("Welche", "Automation"),
    "lock": ("Welches", "Schloss"),
}
_CANCEL_RE = re.compile(
    r"^\s*(?:nein|abbrechen|abbruch|(?:keins|keine|keiner)(?:\s+davon)?|"
    r"nichts|vergiss es|lass es)\s*[.!?]*$",
    re.I,
)
_CONFIRM_RE = re.compile(
    r"^\s*(?:ja|jawohl|genau|richtig|das ist es|den meine ich|die meine ich|"
    r"das meine ich)\s*[.!?]*$",
    re.I,
)
_ORDINAL_WORDS = {
    "erste": 0, "ersten": 0, "erstes": 0, "eins": 0,
    "zweite": 1, "zweiten": 1, "zweites": 1, "zwei": 1,
    "dritte": 2, "dritten": 2, "drittes": 2, "drei": 2,
    "vierte": 3, "vierten": 3, "viertes": 3, "vier": 3,
    "fuenfte": 4, "fünfte": 4, "fuenften": 4, "fünften": 4, "fünf": 4,
}
_DESCRIPTOR_FORMS = {
    "linke": "links", "linken": "links", "linkes": "links",
    "rechte": "rechts", "rechten": "rechts", "rechtes": "rechts",
    "obere": "oben", "oberen": "oben", "oberes": "oben",
    "untere": "unten", "unteren": "unten", "unteres": "unten",
}


class CandidateReplyKind(Enum):
    SELECTED = auto()
    RETRY = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class CandidateReply:
    kind: CandidateReplyKind
    entity: EntitySnapshot | None = None


def _duplicates(values: list[str]) -> set[str]:
    return {value for value in values if values.count(value) > 1}


def candidate_labels(
    candidates: tuple[EntitySnapshot, ...],
) -> tuple[str, ...]:
    """Build deterministic labels that actually distinguish every entity."""
    labels = [candidate.friendly_name for candidate in candidates]
    duplicate_names = _duplicates([normalize_for_compare(label) for label in labels])
    for index, candidate in enumerate(candidates):
        if normalize_for_compare(labels[index]) in duplicate_names and candidate.area_name:
            labels[index] += f" im Bereich {candidate.area_name}"

    duplicate_labels = _duplicates([normalize_for_compare(label) for label in labels])
    for index, candidate in enumerate(candidates):
        if normalize_for_compare(labels[index]) in duplicate_labels and candidate.floor_name:
            labels[index] += f" auf der Etage {candidate.floor_name}"

    duplicate_labels = _duplicates([normalize_for_compare(label) for label in labels])
    for index, candidate in enumerate(candidates):
        if normalize_for_compare(labels[index]) not in duplicate_labels:
            continue
        alias = next(
            (
                item for item in candidate.aliases
                if normalize_for_compare(item) != normalize_for_compare(candidate.friendly_name)
            ),
            None,
        )
        if alias:
            labels[index] += f" ({alias})"

    duplicate_labels = _duplicates([normalize_for_compare(label) for label in labels])
    for index, candidate in enumerate(candidates):
        if normalize_for_compare(labels[index]) in duplicate_labels:
            labels[index] += f" ({candidate.entity_id})"
    return tuple(labels)


def which_question(candidates: tuple[EntitySnapshot, ...] | list[EntitySnapshot]) -> str:
    """"Welche Szene meinst du: A, B oder C?" - the genus when all share it."""
    names = [candidate.friendly_name for candidate in candidates]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " oder " + names[-1]
    domains = {candidate.domain for candidate in candidates}
    article, noun = (
        _DOMAIN_SINGULAR.get(next(iter(domains)), ("Welches", "Gerät"))
        if len(domains) == 1
        else ("Welches", "Gerät")
    )
    return f"{article} {noun} meinst du: {listed}?"


def render_candidate_question(candidates: tuple[EntitySnapshot, ...]) -> str:
    """Name a bounded candidate list and ask for a natural selection."""
    if not candidates:
        return "Das war nicht eindeutig. Welches Gerät meinst du?"
    labels = candidate_labels(candidates)
    if len(labels) == 1:
        return f"Meintest du {labels[0]}?"
    shown = labels[:5]
    domains = {candidate.domain for candidate in candidates}
    noun, followup = (
        _DOMAIN_LABELS.get(next(iter(domains)), ("Geräte", "Welches meinst du?"))
        if len(domains) == 1
        else ("Geräte", "Welches meinst du?")
    )
    listing = "; ".join(
        f"{index}. {label}" for index, label in enumerate(shown, 1)
    )
    remainder = len(labels) - len(shown)
    suffix = (
        f" Außerdem gibt es {remainder} weitere. Bitte grenze nach Bereich oder Etage ein."
        if remainder
        else f" {followup}"
    )
    return f"Ich habe mehrere passende {noun} gefunden: {listing}.{suffix}"


def _ordinal_index(text: str) -> int | None:
    normalized = normalize_for_compare(text)
    numeric = re.search(r"\b(?:nummer\s*)?([1-5])(?:\.|\b)", normalized)
    if numeric is not None:
        return int(numeric.group(1)) - 1
    for word, index in _ORDINAL_WORDS.items():
        if re.search(rf"\b{re.escape(normalize_for_compare(word))}\b", normalized):
            return index
    return None


def resolve_candidate_reply(
    reply_text: str,
    candidates: tuple[EntitySnapshot, ...],
    all_entities: list[EntitySnapshot],
) -> CandidateReply:
    """Resolve only inside the offered set; fuzzy-only replies never execute."""
    if _CANCEL_RE.fullmatch(reply_text):
        return CandidateReply(CandidateReplyKind.CANCELLED)
    if len(candidates) == 1 and _CONFIRM_RE.fullmatch(reply_text):
        return CandidateReply(CandidateReplyKind.SELECTED, candidates[0])

    index = _ordinal_index(reply_text)
    if index is not None:
        return (
            CandidateReply(CandidateReplyKind.SELECTED, candidates[index])
            if index < len(candidates)
            else CandidateReply(CandidateReplyKind.RETRY)
        )

    resolved = resolve_entity(reply_text, list(candidates))
    if resolved.status is ResolveStatus.OK and resolved.entity is not None:
        return CandidateReply(CandidateReplyKind.SELECTED, resolved.entity)

    area = resolve_area_name(reply_text, all_entities)
    if area.status is AreaResolveStatus.OK:
        matches = tuple(
            candidate for candidate in candidates if candidate.area_id == area.area_id
        )
        if len(matches) == 1:
            return CandidateReply(CandidateReplyKind.SELECTED, matches[0])

    normalized = normalize_for_compare(reply_text)
    words = {
        _DESCRIPTOR_FORMS.get(word, word)
        for word in re.findall(r"[a-z0-9]+", normalized)
    }
    descriptor_matches = tuple(
        candidate
        for candidate in candidates
        if words & {
            _DESCRIPTOR_FORMS.get(word, word)
            for word in re.findall(
                r"[a-z0-9]+",
                normalize_for_compare(" ".join((
                    candidate.friendly_name,
                    *candidate.aliases,
                    candidate.area_name or "",
                    candidate.floor_name or "",
                ))),
            )
        }
    )
    if len(descriptor_matches) == 1:
        return CandidateReply(CandidateReplyKind.SELECTED, descriptor_matches[0])
    return CandidateReply(CandidateReplyKind.RETRY)
