"""Read-only situation views: questions about the house as a whole.

"Ist unten noch was an?", "Ist das Haus abgeschlossen?", "Muss ich
lüften?", "Warum ist es im Büro so kalt?", "Was ist los?", "Ist jemand im
Wohnzimmer?", "Was kann ich im Wohnzimmer steuern?", "Welche Räume gibt es
oben?", "Wie viele Fenster gibt es?", "Was macht das Skript Gute Nacht?",
"Wofür ist das Hauptwasserventil?" are aggregate questions.  Each view is a
typed query over the live registry and answers only with observed facts
(states, attributes, configured script steps).  Views never execute
anything.

Recognition is compositional: a question (interrogative, verb-first or
question mark) plus a view cue word (``VIEW_CUES``, data below) plus the
shared place and genus model.  Thresholds are documented constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Mapping, Sequence

from ..entities import EntitySnapshot, format_spoken_number, is_outdoor_entity, normalize_for_compare
from .device_ontology import GENERA, Gender, analyse_word, entity_genera, genus
from .grounded_answer import join_german
from .place_model import Place, PlaceKind, build_place_lexicon
from .target_resolution import genus_members

__all__ = (
    "CO2_VENTILATE_PPM",
    "HUMIDITY_VENTILATE_PERCENT",
    "SituationView",
    "ViewAnswer",
    "answer_situation_view",
)

# Documented thresholds for "Muss ich lüften?".
HUMIDITY_VENTILATE_PERCENT = 60.0
CO2_VENTILATE_PPM = 1000.0


class SituationView(Enum):
    STILL_ON = auto()
    SECURE = auto()
    VENTILATE = auto()
    WHY_TEMPERATURE = auto()
    OVERVIEW = auto()
    PRESENCE = auto()
    CONTROLLABLE = auto()
    ROOMS = auto()
    COUNT = auto()
    ROUTINE = auto()
    PURPOSE = auto()


@dataclass(frozen=True)
class ViewAnswer:
    view: SituationView
    text: str
    entities: tuple[EntitySnapshot, ...] = ()
    place: Place | None = None


_QUESTION_START = frozenset({
    "ist", "sind", "gibt", "hat", "haben", "habe", "muss", "muessen", "sollte", "soll",
    "was", "wie", "wo", "warum", "wieso", "weshalb", "welche", "welcher", "welches",
    "wer", "kann", "koennen", "wofuer", "wozu", "brennt", "laeuft", "laufen",
})
_ON_WORDS = frozenset({"an", "eingeschaltet", "laeuft", "laufen", "brennt", "brennen", "aktiv", "eingeschaltet"})
_STILL = frozenset({"noch", "immer"})
_FORGOT = frozenset({"vergessen", "vergass", "vergessen?"})
_OFF_INFINITIVES = frozenset({"auszuschalten", "auszumachen", "abzuschalten", "aus"})
_SECURE_WORDS = frozenset({
    "abgeschlossen", "abgesperrt", "zugesperrt", "verschlossen", "gesichert", "sicher",
    "verriegelt", "dicht",
})
_CLOSED_WORDS = frozenset({"zu", "geschlossen"})
_WHOLE = frozenset({"haus", "wohnung", "alles", "alle", "tueren", "fenster", "ueberall"})
_VENTILATE = frozenset({"lueften", "durchlueften", "stosslueften", "luefte"})
_WHY = frozenset({"warum", "wieso", "weshalb"})
_COLD = frozenset({"kalt", "kuehl", "frisch", "kaelter"})
_WARM = frozenset({"warm", "heiss", "waermer", "stickig"})
_OVERVIEW_PHRASES = (
    ("was", "ist", "los"), ("was", "gibt", "s", "neues"), ("was", "gibts", "neues"),
    ("wie", "ist", "die", "lage"), ("wie", "sieht", "es", "aus"), ("was", "tut", "sich"),
    ("statusbericht",), ("lagebericht",), ("ueberblick",),
)
_PRESENCE_WORDS = frozenset({"jemand", "wer", "leer", "besetzt", "niemand", "anwesend"})
_CONTROL_VERBS = frozenset({"steuern", "schalten", "bedienen", "regeln", "einstellen", "machen"})
_ROOM_NOUNS = frozenset({"raeume", "zimmer", "raum", "bereiche", "raeumen"})
_COUNT = ("wie", "viele")
_ROUTINE_NOUNS = frozenset({"skript", "script", "szene", "routine", "ablauf"})
_ROUTINE_VERBS = frozenset({"macht", "tut", "passiert", "bewirkt", "ausloest", "enthaelt"})
_PURPOSE = frozenset({"wofuer", "wozu"})

_ACTIVE_STATES = frozenset({"on", "playing", "paused", "cleaning", "mowing", "heat", "cool", "open", "opening"})
_EVERYDAY = frozenset(item.key for item in GENERA if item.in_everything)


def _words(text: str) -> list[str]:
    import re as _re  # local: one tokenizer for this module

    return [normalize_for_compare(word) for word in _re.findall(r"[\wäöüßÄÖÜ']+", text)]


_REQUEST_MODALS = frozenset({"kannst", "koenntest", "wuerdest", "willst", "magst"})


def _is_question(text: str, words: Sequence[str]) -> bool:
    if len(words) > 1 and words[0] in _REQUEST_MODALS and words[1] == "du":
        return False  # "Kannst du alles zumachen?" is a request, not a view
    return text.rstrip().endswith("?") or bool(words and words[0] in _QUESTION_START)


def _contains(words: Sequence[str], phrase: tuple[str, ...]) -> bool:
    return any(tuple(words[index:index + len(phrase)]) == phrase for index in range(len(words)))


def _place_of(words: Sequence[str], entities: Sequence[EntitySnapshot]) -> Place | None:
    lexicon = build_place_lexicon(entities)
    for mention in lexicon.scan(list(words)):
        if mention.place.kind is not PlaceKind.HERE:
            return mention.place
    return None


def _at(place: Place | None, entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [entity for entity in entities if place is None or place.contains(entity)]


def _where(place: Place | None, default: str = "im Haus") -> str:
    return place.label if place is not None else default


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


_LISTED = 8


def _names(entities: Sequence[EntitySnapshot]) -> str:
    names = sorted(entity.friendly_name for entity in entities)
    if len(names) > _LISTED:
        return ", ".join(names[:_LISTED]) + f" und {len(names) - _LISTED} weitere"
    return join_german(names)


def _kind(entity: EntitySnapshot) -> str | None:
    """The most specific genus of an entity; its name decides between peers."""
    kinds = sorted(entity_genera(entity) - {"device", "media"})
    if not kinds:
        return None
    name = normalize_for_compare(entity.friendly_name)
    named = [
        key for key in kinds
        if any(normalize_for_compare(lemma) in name for lemma in genus(key).lemmas)
    ]
    return (named or kinds)[0]


def _is_on(entity: EntitySnapshot) -> bool:
    if entity.domain == "media_player":
        return entity.state in {"on", "playing", "paused", "buffering"}
    return entity.state == "on"


def _view(text: str, words: list[str]) -> SituationView | None:
    ws = set(words)
    if not _is_question(text, words):
        return None
    if ws & _PURPOSE:
        return SituationView.PURPOSE
    if ws & _ROUTINE_NOUNS and ws & _ROUTINE_VERBS and words[0] == "was":
        return SituationView.ROUTINE
    if ws & _FORGOT and ws & _OFF_INFINITIVES:
        return SituationView.STILL_ON
    if ws & _STILL and ws & _ON_WORDS and ws & {"was", "etwas", "irgendwas", "irgendetwas", "alles", "licht", "lichter", "geraete", "lampen"}:
        return SituationView.STILL_ON
    if ws & _VENTILATE:
        return SituationView.VENTILATE
    if ws & _WHY and ws & (_COLD | _WARM):
        return SituationView.WHY_TEMPERATURE
    if ws & _SECURE_WORDS or (ws & _CLOSED_WORDS and ws & {"alles", "ueberall", "haus", "wohnung"}):
        return SituationView.SECURE
    if any(_contains(words, phrase) for phrase in _OVERVIEW_PHRASES):
        return SituationView.OVERVIEW
    if ws & _PRESENCE_WORDS and ("im" in ws or "in" in ws or "ist" in ws) and not ws & {"licht", "fenster", "tuer"}:
        return SituationView.PRESENCE
    if ws & _CONTROL_VERBS and ws & {"kann", "koennte", "lassen", "laesst"} and words[0] in {"was", "welche", "welches"}:
        return SituationView.CONTROLLABLE
    if ws & _ROOM_NOUNS and words[0] in {"welche", "was", "wie"}:
        return SituationView.ROOMS
    if _contains(words, _COUNT) and ws & {"gibt", "haben", "habe", "hab"}:
        return SituationView.COUNT
    return None


def answer_situation_view(
    text: str,
    entities: Sequence[EntitySnapshot],
    *,
    source_area_id: str | None = None,
    routine_steps: Callable[[str], Sequence[Mapping[str, object]] | None] | None = None,
) -> ViewAnswer | None:
    """Answer one situation question from observed facts, or ``None``."""
    words = _words(text)
    view = _view(text, words)
    if view is None:
        return None
    place = _place_of(words, entities)
    if place is None and source_area_id is not None and view in {
        SituationView.PRESENCE, SituationView.CONTROLLABLE, SituationView.WHY_TEMPERATURE,
    } and "hier" in words:
        place = build_place_lexicon(entities).place_for_area(source_area_id)
    handler = _HANDLERS[view]
    return handler(words, entities, place, routine_steps)


def _still_on(words, entities, place, _steps) -> ViewAnswer:
    on = [
        entity for entity in _at(place, entities)
        if entity_genera(entity) & _EVERYDAY and _is_on(entity)
    ]
    where = _where(place)
    if not on:
        return ViewAnswer(SituationView.STILL_ON, f"Nein, {where} ist nichts mehr an.", (), place)
    verb = "ist" if len(on) == 1 else "sind"
    return ViewAnswer(
        SituationView.STILL_ON, f"{_capital(where)} {verb} noch an: {_names(on)}.", tuple(on), place
    )


def _secure(words, entities, place, _steps) -> ViewAnswer:
    local = _at(place, entities)
    unlocked = [entity for entity in local if entity.domain == "lock" and entity.state != "locked"]
    open_doors = [
        entity for entity in local
        if entity.domain == "binary_sensor" and entity.device_class in {"door", "garage_door", "opening"}
        and entity.state == "on"
    ]
    open_windows = [
        entity for entity in local
        if entity.domain == "binary_sensor" and entity.device_class == "window" and entity.state == "on"
    ]
    open_gates = [
        entity for entity in local
        if entity.domain == "cover" and "garage_door" in entity_genera(entity) and entity.state != "closed"
    ]
    alarm = [entity for entity in local if entity.domain == "alarm_control_panel"]
    problems: list[str] = []
    if unlocked:
        problems.append(f"nicht abgeschlossen: {_names(unlocked)}")
    if open_doors or open_gates:
        problems.append(f"offen: {_names(open_doors + open_gates)}")
    if open_windows:
        problems.append(f"Fenster offen: {_names(open_windows)}")
    locked = [entity for entity in local if entity.domain == "lock" and entity.state == "locked"]
    alarm_text = ""
    if alarm:
        state = alarm[0].state
        alarm_text = (
            " Die Alarmanlage ist scharf." if state.startswith("armed")
            else " Die Alarmanlage ist nicht scharf." if state == "disarmed" else ""
        )
    if not problems:
        base = "Ja, alles ist zu"
        if locked:
            base += f" und abgeschlossen ({_names(locked)})"
        return ViewAnswer(SituationView.SECURE, base + "." + alarm_text, tuple(locked), place)
    return ViewAnswer(
        SituationView.SECURE,
        f"Nein. {_capital('; '.join(problems))}." + alarm_text,
        tuple(unlocked + open_doors + open_gates + open_windows),
        place,
    )


def _number(entity: EntitySnapshot) -> float | None:
    try:
        return float(str(entity.state).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _ventilate(words, entities, place, _steps) -> ViewAnswer:
    local = _at(place, entities)
    reasons: list[str] = []
    rooms: list[EntitySnapshot] = []
    for entity in local:
        if entity.domain != "sensor" or is_outdoor_entity(entity):
            continue
        value = _number(entity)
        if value is None:
            continue
        where = _capital(entity.area_name or entity.friendly_name)
        if entity.device_class == "humidity" and value >= HUMIDITY_VENTILATE_PERCENT:
            reasons.append(f"{where}: Luftfeuchtigkeit {format_spoken_number(value)} %")
            rooms.append(entity)
        elif entity.device_class == "carbon_dioxide" and value >= CO2_VENTILATE_PPM:
            reasons.append(f"{where}: CO2 {format_spoken_number(value)} ppm")
            rooms.append(entity)
    if not reasons:
        return ViewAnswer(
            SituationView.VENTILATE,
            "Nein, die Luftwerte sind im grünen Bereich "
            f"(Luftfeuchtigkeit unter {format_spoken_number(HUMIDITY_VENTILATE_PERCENT)} %, "
            f"CO2 unter {format_spoken_number(CO2_VENTILATE_PPM)} ppm).",
            (), place,
        )
    return ViewAnswer(
        SituationView.VENTILATE, f"Ja, lüften wäre gut – {'; '.join(reasons)}.", tuple(rooms), place
    )


def _why_temperature(words, entities, place, _steps) -> ViewAnswer:
    if place is None:
        return ViewAnswer(SituationView.WHY_TEMPERATURE, "In welchem Raum meinst du?")
    local = _at(place, entities)
    facts: list[str] = []
    measured = [
        entity for entity in local
        if entity.domain == "sensor" and entity.device_class == "temperature" and _number(entity) is not None
    ]
    heating = [entity for entity in local if entity.domain == "climate"]
    if measured:
        facts.append(f"{_capital(place.label)} sind es {format_spoken_number(_number(measured[0]) or 0)} Grad")
    for entity in heating:
        target = entity.attributes.get("temperature")
        current = entity.attributes.get("current_temperature")
        if not measured and isinstance(current, (int, float)):
            facts.append(f"{_capital(place.label)} sind es {format_spoken_number(float(current))} Grad")
        if entity.state == "off":
            facts.append(f"{entity.friendly_name} ist ausgeschaltet")
        elif isinstance(target, (int, float)):
            action = entity.attributes.get("hvac_action")
            doing = " und heizt gerade" if action == "heating" else " und heizt gerade nicht" if action in {"idle", "off"} else ""
            facts.append(f"{entity.friendly_name} ist auf {format_spoken_number(float(target))} Grad eingestellt{doing}")
    windows = [
        entity for entity in local
        if entity.domain == "binary_sensor" and entity.device_class in {"window", "door"} and entity.state == "on"
    ]
    if windows:
        facts.append(f"offen: {_names(windows)}")
    outdoor = [
        entity for entity in entities
        if entity.domain == "sensor" and entity.device_class == "temperature" and is_outdoor_entity(entity)
        and _number(entity) is not None
    ]
    if outdoor:
        facts.append(f"draußen hat es {format_spoken_number(_number(outdoor[0]) or 0)} Grad")
    if not facts:
        return ViewAnswer(SituationView.WHY_TEMPERATURE, f"{_capital(place.label)} habe ich keine Temperaturdaten.")
    return ViewAnswer(
        SituationView.WHY_TEMPERATURE,
        ". ".join(_capital(fact) for fact in facts) + ".",
        tuple(measured + heating + windows),
        place,
    )


def _overview(words, entities, place, _steps) -> ViewAnswer:
    local = _at(place, entities)
    parts: list[str] = []
    lights = [entity for entity in local if entity.domain == "light" and entity.state == "on"]
    if lights:
        parts.append(f"Licht an: {_names(lights)}")
    media = [entity for entity in local if entity.domain == "media_player" and entity.state == "playing"]
    if media:
        parts.append(f"es läuft: {_names(media)}")
    openings = [
        entity for entity in local
        if entity.domain == "binary_sensor" and entity.device_class in {"window", "door", "garage_door"}
        and entity.state == "on"
    ]
    if openings:
        parts.append(f"offen: {_names(openings)}")
    alarms = [
        entity for entity in local
        if entity.domain == "binary_sensor" and entity.device_class in {"smoke", "moisture", "gas", "carbon_monoxide"}
        and entity.state == "on"
    ]
    if alarms:
        parts.insert(0, f"Achtung, Alarm: {_names(alarms)}")
    running = [
        entity for entity in local
        if entity.domain in {"vacuum", "lawn_mower"} and entity.state in {"cleaning", "mowing"}
    ]
    if running:
        parts.append(f"unterwegs: {_names(running)}")
    home = [entity for entity in entities if entity.domain == "person" and entity.state == "home"]
    if home:
        parts.append(f"zu Hause: {_names(home)}")
    if not parts:
        return ViewAnswer(SituationView.OVERVIEW, f"{_capital(_where(place))} ist alles ruhig.", (), place)
    return ViewAnswer(
        SituationView.OVERVIEW, ". ".join(_capital(part) for part in parts) + ".",
        tuple(lights + media + openings + alarms), place,
    )


def _presence(words, entities, place, _steps) -> ViewAnswer | None:
    if place is None or place.kind is not PlaceKind.AREA:
        return None  # household presence ("Wer ist zu Hause?") has its own answer
    sensors = [
        entity for entity in _at(place, entities)
        if entity.domain == "binary_sensor" and entity.device_class in {"occupancy", "presence", "motion"}
    ]
    if not sensors:
        return ViewAnswer(
            SituationView.PRESENCE,
            f"{_capital(place.label)} gibt es keinen Präsenz- oder Bewegungsmelder; das kann ich nicht sagen.",
            (), place,
        )
    occupied = [entity for entity in sensors if entity.state == "on"]
    if occupied:
        return ViewAnswer(
            SituationView.PRESENCE,
            f"Ja, {place.label} ist jemand (laut {_names(occupied)}).", tuple(occupied), place,
        )
    return ViewAnswer(
        SituationView.PRESENCE,
        f"Nein, {place.label} ist gerade niemand (laut {_names(sensors)}).", tuple(sensors), place,
    )


_ACTUATOR_DOMAINS = frozenset({
    "light", "switch", "fan", "cover", "climate", "media_player", "vacuum", "lawn_mower",
    "lock", "valve", "humidifier", "water_heater", "scene", "script", "select", "number",
    "button", "alarm_control_panel", "input_boolean",
})


def _controllable(words, entities, place, _steps) -> ViewAnswer:
    local = [entity for entity in _at(place, entities) if entity.domain in _ACTUATOR_DOMAINS]
    if not local:
        return ViewAnswer(SituationView.CONTROLLABLE, f"{_capital(_where(place))} kann ich nichts steuern.", (), place)
    groups: dict[str, list[EntitySnapshot]] = {}
    for entity in local:
        groups.setdefault(_kind(entity) or entity.domain, []).append(entity)
    parts = []
    for key, members in sorted(groups.items()):
        try:
            label = genus(key).plural if len(members) > 1 else genus(key).lemmas[0]
        except KeyError:
            label = key
        parts.append(f"{label} ({_names(members)})")
    return ViewAnswer(
        SituationView.CONTROLLABLE,
        f"{_capital(_where(place))} kann ich steuern: {join_german(parts)}.", tuple(local), place,
    )


def _rooms(words, entities, place, _steps) -> ViewAnswer:
    areas: dict[str, str] = {}
    for entity in entities:
        if entity.area_id and entity.area_name and (place is None or place.contains(entity)):
            areas[entity.area_id] = entity.area_name
    if not areas:
        return ViewAnswer(SituationView.ROOMS, f"{_capital(_where(place))} kenne ich keine Räume.", (), place)
    return ViewAnswer(
        SituationView.ROOMS,
        f"{_capital(_where(place))} gibt es {len(areas)} Räume: {join_german(sorted(areas.values()))}.",
        (), place,
    )


def _count(words, entities, place, _steps) -> ViewAnswer | None:
    index = next(index for index in range(len(words) - 1) if tuple(words[index:index + 2]) == _COUNT)
    analysis = next(
        (analyse_word(word) for word in words[index + 2:] if analyse_word(word) is not None), None
    )
    if analysis is None:
        return None
    key = analysis.genera[0]
    members = [entity for entity in genus_members(key, entities) if place is None or place.contains(entity)]
    item = genus(key)
    if not members:
        return ViewAnswer(SituationView.COUNT, f"{_capital(_where(place))} gibt es keine {item.plural}.", (), place)
    noun = item.lemmas[0] if len(members) == 1 else item.plural
    return ViewAnswer(
        SituationView.COUNT,
        f"{_capital(_where(place))} gibt es {len(members)} {noun}: {_names(members)}.",
        tuple(members), place,
    )


_SERVICE_VERBS = {
    "turn_on": "einschalten", "turn_off": "ausschalten", "toggle": "umschalten",
    "open_cover": "öffnen", "close_cover": "schließen", "lock": "abschließen",
    "unlock": "aufschließen", "media_play": "abspielen", "media_pause": "pausieren",
    "set_temperature": "Temperatur einstellen", "select_source": "Quelle wählen",
    "send_message": "eine Nachricht senden",
}


def _describe_step(step: Mapping[str, object], entities: Sequence[EntitySnapshot]) -> str | None:
    action = str(step.get("action") or step.get("service") or "")
    if not action or "." not in action:
        return None
    domain, service = action.split(".", 1)
    target = step.get("target")
    names: list[str] = []
    if isinstance(target, Mapping):
        ids = target.get("entity_id")
        for entity_id in ([ids] if isinstance(ids, str) else list(ids) if isinstance(ids, list) else []):
            names.append(next((item.friendly_name for item in entities if item.entity_id == entity_id), str(entity_id)))
        areas = target.get("area_id")
        area_ids = [areas] if isinstance(areas, str) else list(areas) if isinstance(areas, list) else []
        if area_ids:
            area_names = sorted({
                item.area_name for item in entities
                if item.area_id in area_ids and item.area_name
            } or set(map(str, area_ids)))
            noun = {"light": "Licht", "cover": "Rollläden", "switch": "Schalter"}.get(domain, domain)
            names.append(f"{noun} in {join_german(area_names)}")
    verb = _SERVICE_VERBS.get(service, service.replace("_", " "))
    return f"{join_german(names)} {verb}".strip() if names else verb


def _routine(words, entities, place, steps) -> ViewAnswer | None:
    candidates = [entity for entity in entities if entity.domain in {"script", "scene"}]
    spoken = " ".join(words)
    named = [
        entity for entity in candidates
        if normalize_for_compare(entity.friendly_name) and normalize_for_compare(entity.friendly_name) in spoken
    ]
    if len(named) != 1:
        return None
    entity = named[0]
    if entity.domain == "scene":
        members = entity.attributes.get("entity_id") or []
        affected = [item for item in entities if item.entity_id in members]
        if not affected:
            return ViewAnswer(SituationView.ROUTINE, f"Die Szene {entity.friendly_name} enthält keine mir bekannten Geräte.")
        return ViewAnswer(
            SituationView.ROUTINE,
            f"Die Szene {entity.friendly_name} stellt {_names(affected)} auf gespeicherte Zustände.",
            tuple(affected),
        )
    sequence = steps(entity.entity_id) if steps is not None else None
    if not sequence:
        return ViewAnswer(
            SituationView.ROUTINE,
            f"Die Schritte des Skripts {entity.friendly_name} kann ich nicht einsehen.",
        )
    described = [text for text in (_describe_step(step, entities) for step in sequence) if text]
    if not described:
        return ViewAnswer(SituationView.ROUTINE, f"Das Skript {entity.friendly_name} hat keine Schritte, die ich beschreiben kann.")
    return ViewAnswer(
        SituationView.ROUTINE,
        f"Das Skript {entity.friendly_name}: " + ", dann ".join(described) + ".",
    )


def _purpose(words, entities, place, _steps) -> ViewAnswer | None:
    spoken = " ".join(words)
    named = sorted(
        (
            entity for entity in entities
            if normalize_for_compare(entity.friendly_name) and normalize_for_compare(entity.friendly_name) in spoken
        ),
        key=lambda entity: -len(entity.friendly_name),
    )
    if not named:
        return None
    entity = named[0]
    key = _kind(entity)
    if key is not None:
        item = genus(key)
        article = "eine" if item.gender is Gender.FEMININE else "ein"
        kind = f"{article} {item.lemmas[0]}"
    else:
        kind = f"ein Gerät vom Typ {entity.domain}"
    where = f" {place.label}" if place is not None else (
        f" im Bereich {entity.area_name}" if entity.area_name else ""
    )
    can = [_ABILITIES[item] for item in sorted(entity.capabilities) if item in _ABILITIES]
    if not can:
        can = list(_DOMAIN_ABILITIES.get(entity.domain, ()))
    ability = (
        f" Du kannst es {join_german(can)}." if can
        else " Es liefert nur Messwerte oder Zustände."
    )
    return ViewAnswer(SituationView.PURPOSE, f"{entity.friendly_name} ist {kind}{where}.{ability}", (entity,))


_ABILITIES = {
    "TURN_ON": "einschalten", "TURN_OFF": "ausschalten", "BRIGHTNESS": "dimmen",
    "POSITION": "auf eine Position fahren", "TEMPERATURE": "auf eine Temperatur stellen",
}
_DOMAIN_ABILITIES: dict[str, tuple[str, ...]] = {
    "light": ("einschalten", "ausschalten"), "switch": ("einschalten", "ausschalten"),
    "fan": ("einschalten", "ausschalten"), "cover": ("öffnen", "schließen"),
    "valve": ("öffnen", "schließen"), "lock": ("abschließen", "aufschließen"),
    "climate": ("auf eine Temperatur stellen",), "media_player": ("einschalten", "abspielen"),
    "scene": ("aktivieren",), "script": ("starten",), "vacuum": ("starten", "zurückschicken"),
}


_HANDLERS: dict[SituationView, Callable[..., ViewAnswer | None]] = {
    SituationView.STILL_ON: _still_on,
    SituationView.SECURE: _secure,
    SituationView.VENTILATE: _ventilate,
    SituationView.WHY_TEMPERATURE: _why_temperature,
    SituationView.OVERVIEW: _overview,
    SituationView.PRESENCE: _presence,
    SituationView.CONTROLLABLE: _controllable,
    SituationView.ROOMS: _rooms,
    SituationView.COUNT: _count,
    SituationView.ROUTINE: _routine,
    SituationView.PURPOSE: _purpose,
}
