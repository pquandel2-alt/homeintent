"""Verbless short commands and values without a unit (7.8 B3).

"Büro an", "Markise raus", "Saugroboter los", "Esszimmer Rollladen halb",
"Heizung Schlafzimmer 18 Grad", "heizung büro auf einundzwanzig": a
command is formed from *kind/room/device* + a direction or state particle
(an/aus/zu/auf/raus/rein/hoch/runter/halb/los) + an optional value.

* A room alone with an/aus means the light of the room.
* A device, a number and no question sign form a command, never a state
  question.
* The unit follows from the kind: heating -> °C, light, shutter and
  volume -> %.

The result is an explicit imperative surface the ordinary pipeline reads;
validator, capabilities and policy decide as for any typed command.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .device_ontology import GENERA, Gender, analyse_word
from .normalize import german_number

__all__ = ("expand_short_command", "complete_value_unit")

_DOMAINS = {item.key: item.domains for item in GENERA}
_GENDER = {item.key: item.gender for item in GENERA}
_ACCUSATIVE = {Gender.MASCULINE: "den", Gender.FEMININE: "die", Gender.NEUTER: "das"}
_ARTICLE_WORDS = frozenset({"der", "die", "das", "den", "dem", "alle", "beide", "mein", "meine", "meinen"})
_FRACTIONS = frozenset({"viertel", "drittel", "haelfte", "achtel", "fuenftel", "zehntel", "mal", "stufen", "stufe"})
_QUESTION_WORDS = frozenset({
    "wie", "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches", "ist", "sind",
    "ob", "gibt", "hat", "haben", "steht", "stehen",
})
_FILLER = frozenset({"bitte", "mal", "jetzt", "sofort", "noch", "doch", "gleich", "danke"})
_SPOKEN_UNITS = frozenset({
    "sekunde", "sekunden", "minute", "minuten", "stunde", "stunden", "tag", "tage", "tagen", "grad", "prozent",
    "watt", "kilowatt", "lux", "ppm", "uhr",
})


def _domains_of(field: object, entities: Sequence[EntitySnapshot]) -> set[str]:
    ids = getattr(field, "registry_ids", ())
    if ids:
        return {entity.domain for entity in entities if entity.entity_id in ids}
    domains: set[str] = set()
    for key in getattr(field, "genera", ()):
        domains |= set(_DOMAINS.get(key, ()))
    return domains


def _unit_for(domains: set[str]) -> str | None:
    if domains & {"climate", "water_heater"}:
        return "Grad"
    if domains & {"light", "cover", "media_player"}:
        return "Prozent"
    return None


def _number(text: str) -> str | None:
    words = re.findall(r"\d+(?:[,.]\d+)?|[\wäöüß]+", text)
    for word in words:
        if word[:1].isdigit():
            return word
        value = german_number(normalize_for_compare(word))
        if value is not None and normalize_for_compare(word) not in {"ein", "eine", "einen"}:
            return str(value)
    return None


def _phrase(particle: str, domains: set[str]) -> tuple[str, str] | None:
    """(verb, particle) of the imperative for a kind of device."""
    particle = normalize_for_compare(particle)
    cover = bool(domains & {"cover"})
    runner = bool(domains & {"vacuum", "lawn_mower"})
    if runner and particle in {"los", "raus", "an", "ein"}:
        return "Starte", ""
    if runner and particle in {"rein", "heim", "zurueck", "aus"}:
        return "Schicke", "zur Basis" if particle != "aus" else ""
    if cover:
        return {
            "auf": ("Öffne", ""), "zu": ("Schließe", ""), "hoch": ("Fahre", "hoch"),
            "rauf": ("Fahre", "hoch"), "runter": ("Fahre", "runter"), "raus": ("Fahre", "aus"),
            "rein": ("Fahre", "ein"), "aus": ("Fahre", "aus"), "ein": ("Fahre", "ein"),
            "halb": ("Fahre", "auf 50 Prozent"),
        }.get(particle)
    if particle in {"an", "ein"}:
        return "Schalte", "ein"
    if particle == "aus":
        return "Schalte", "aus"
    if particle in {"auf", "zu"} and domains & {"lock", "valve"}:
        return ("Öffne" if particle == "auf" else "Schließe"), ""
    if particle == "los":
        return "Starte", ""
    return None


def expand_short_command(text: str, entities: Iterable[EntitySnapshot]) -> str | None:
    """An imperative for a verbless short command, else ``None``."""
    from .self_correction import utterance_fields

    if text.rstrip().endswith("?"):
        return None
    words = [normalize_for_compare(word) for word in re.findall(r"[\wäöüß]+", text)]
    if not words or words[0] in _QUESTION_WORDS or len(words) > 8:
        return None
    entity_list = entities if isinstance(entities, list) else list(entities)
    fields, rest = utterance_fields(text, entity_list)
    if rest or any(field.kind in {"verb", "time", "reference"} for field in fields):
        return None
    targets = [field for field in fields if field.kind == "target"]
    places = [field for field in fields if field.kind == "place"]
    particles = [field for field in fields if field.kind == "particle"]
    values = [field for field in fields if field.kind == "value"]
    features = [field for field in fields if field.kind == "feature"]
    if len(targets) > 1 or len(particles) > 1 or len(values) > 1 or len(places) > 1 or features:
        return None
    place = f" {places[0].place_label}" if places else ""
    if not targets:
        # "Büro an": the light of the room.
        if places and particles and normalize_for_compare(particles[0].text) in {"an", "aus", "ein"} and not values:
            verb, particle = _phrase(particles[0].text, {"light"}) or ("Schalte", "ein")
            return f"{verb} das Licht{place} {particle}".strip() + "."
        return None
    target = targets[0]
    domains = _domains_of(target, entity_list)
    noun = target.text
    if not target.registry_ids and target.genera and noun.split()[0].casefold() not in _ARTICLE_WORDS:
        # A kind word is a noun phrase: "Licht im Büro an" -> "das Licht".
        gender = _GENDER.get(target.genera[0], Gender.FEMININE)
        spoken = analyse_word(normalize_for_compare(noun.split()[-1]))
        article = "die" if spoken is not None and spoken.plural else _ACCUSATIVE.get(gender, "die")
        noun = f"{article} {noun}"
    if values:
        following = text[values[0].end:].split()
        if following and normalize_for_compare(following[0].strip(".,!?")) in _FRACTIONS:
            return None
        number = _number(values[0].text)
        if number is None:
            return None
        spoken_unit = re.search(r"(?i)grad|prozent|%|°", values[0].text)
        unit = "Prozent" if spoken_unit and spoken_unit.group(0) in {"%", "prozent", "Prozent"} else (
            "Grad" if spoken_unit else _unit_for(domains)
        )
        if unit is None:
            return None
        if particles and normalize_for_compare(particles[0].text) not in {"auf"}:
            return None
        return f"Stelle {noun}{place} auf {number} {unit}."
    if not particles:
        return None
    phrase = _phrase(particles[0].text, domains)
    if phrase is None:
        return None
    verb, particle = phrase
    return f"{verb} {noun}{place} {particle}".strip() + "."


def complete_value_unit(text: str, entities: Iterable[EntitySnapshot]) -> str:
    """"Stell die Heizung im Bad auf 23" -> "… auf 23 Grad" (unit from the kind)."""
    from .self_correction import utterance_fields

    if text.rstrip().endswith("?") or not re.search(r"\d|zig|zehn|eins|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun", text, re.I):
        return text
    entity_list = entities if isinstance(entities, list) else list(entities)
    fields, _rest = utterance_fields(text, entity_list)
    values = [field for field in fields if field.kind == "value"]
    targets = [field for field in fields if field.kind == "target"]
    if any(field.kind == "time" for field in fields) or re.search(r"(?i)\buhr\b", text):
        return text
    if len(values) != 1 or len(targets) != 1 or re.search(r"(?i)grad|prozent|%|°|uhr|minute|stunde", values[0].text):
        return text
    if not values[0].text.casefold().startswith("auf"):
        return text
    following = text[values[0].end:].split()
    if following and normalize_for_compare(following[0].strip(".,!?")) in _FRACTIONS:
        # "auf drei Viertel": a fraction, not a number with a missing unit.
        return text
    if following and normalize_for_compare(following[0].strip(".,!?")) in _SPOKEN_UNITS:
        # "… auf 15 Minuten" already names its unit (7.9.3 B7).
        return text
    unit = _unit_for(_domains_of(targets[0], entity_list))
    number = _number(values[0].text)
    if unit is None or number is None:
        return text
    return f"{text[:values[0].start]}auf {number} {unit}{text[values[0].end:]}"


_TRIGGER_LEADS = frozenset({
    "wenn", "sobald", "falls", "sofern", "immer", "jedes", "jeden", "jede", "bei", "taeglich",
    "werktags", "wochentags", "morgens", "abends", "nachts", "mittags", "um", "am", "sonntags",
    "samstags", "montags", "dienstags", "mittwochs", "donnerstags", "freitags", "wochenends",
})
_TEMPORAL_WORDS = frozenset({
    "uhr", "morgen", "morgens", "abend", "abends", "nacht", "nachts", "mittag", "mittags",
    "sonnenuntergang", "sonnenaufgang", "taeglich", "werktags", "wochentags", "halb", "viertel",
})


def expand_verbless_action(text: str, entities: Iterable[EntitySnapshot]) -> str | None:
    """"Jeden Morgen um sieben die Kaffeemaschine an", "…fällt, Heizung
    Kinderzimmer auf 21": the action part of an automation is read with the
    same short-command analysis as a direct command (7.8 B5)."""
    entity_list = entities if isinstance(entities, list) else list(entities)
    words = text.strip().split()
    if not words or normalize_for_compare(words[0].strip(",")) not in _TRIGGER_LEADS:
        return None
    if "," in text:
        # the last comma separates the action ("Wenn …, …, Heizung auf 21")
        lead, _, action = text.rpartition(",")
        imperative = expand_short_command(action.strip(), entity_list)
        if imperative is None:
            return None
        return f"{lead}, {imperative[:1].lower()}{imperative[1:]}"
    for split in range(2, len(words) - 1):
        lead_words = [normalize_for_compare(word) for word in words[:split]]
        if not set(lead_words) & _TEMPORAL_WORDS:
            continue
        head = normalize_for_compare(words[split])
        if head not in {"der", "die", "das", "den", "alle"} and analyse_word(head) is None:
            continue
        imperative = expand_short_command(" ".join(words[split:]), entity_list)
        if imperative is not None:
            return f"{' '.join(words[:split])} {imperative[:1].lower()}{imperative[1:]}"
    return None


_GROUP_WORDS = frozenset({"gruppe", "lichtgruppe", "schaltergruppe", "gerätegruppe", "geraetegruppe"})


def drop_group_kind_word(text: str, entities: Iterable[EntitySnapshot]) -> str:
    """"Schalte die Gruppe Treppe ein": the kind word names what the group
    *is*; the registry name that follows is the target (7.8 B7)."""
    lowered = text.casefold()
    if not any(word in lowered for word in ("gruppe",)):
        return text
    from .self_correction import utterance_fields

    entity_list = entities if isinstance(entities, list) else list(entities)
    by_id = {entity.entity_id: entity for entity in entity_list}
    match = re.search(r"(?i)\b(?:die\s+|der\s+)?(\w*gruppe)\s+", text)
    if match is None or normalize_for_compare(match.group(1)) not in {normalize_for_compare(w) for w in _GROUP_WORDS}:
        return text
    rest = text[match.end():]
    fields, _rest = utterance_fields(rest, entity_list)
    first = next((field for field in fields if field.kind == "target"), None)
    if first is None or first.start != 0 or not first.registry_ids:
        return text
    named = [by_id[entity_id] for entity_id in first.registry_ids if entity_id in by_id]
    if not named or not all(
        entity.domain == "group" or isinstance(entity.attributes.get("entity_id"), (list, tuple))
        for entity in named
    ):
        return text
    return f"{text[:match.start()]}{rest}"
