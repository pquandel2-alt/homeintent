"""Release 7.3.0 section 1: compositional target resolution, generated.

Targets are genus x place x quantity, never names.  The matrix below is
generated from the ontology and the test house: every switchable genus, at
every room where it exists, in every shell (command, place-first, polite
question, verbless request) and with negation.  Expectations are computed
from the registry: singular with one member -> that device; singular with
several -> a question; plural -> all members at the place; negation ->
nothing.  Sentences are built here, not taken from ``sim/``.
"""

from __future__ import annotations

import itertools
from functools import lru_cache

import pytest

from _testhaus import HouseConversation, house_entities
from custom_components.homeintent.nlu.device_ontology import Gender, genus
from custom_components.homeintent.nlu.german_morphology import dative_location_phrase
from custom_components.homeintent.nlu.target_resolution import genus_members

# genus key -> (noun lemma used, on particle, off particle, on service, off service)
_OPERATIONS = {
    "light": [("an", "turn_on"), ("aus", "turn_off")],
    "fan": [("an", "turn_on"), ("aus", "turn_off")],
    "tv": [("an", "turn_on"), ("aus", "turn_off")],
    "radio": [("an", "turn_on"), ("aus", "turn_off")],
    "socket": [("an", "turn_on"), ("aus", "turn_off")],
    "shutter": [("auf", "open_cover"), ("zu", "close_cover")],
}
_NOUNS = {
    "light": ["Lampe", "Leuchte"],
    "fan": ["Ventilator", "Lüfter"],
    "tv": ["Fernseher", "TV", "Glotze"],
    "radio": ["Radio"],
    "socket": ["Steckdose"],
    "shutter": ["Rollladen", "Rollo", "Jalousie"],
}
_PLURALS = {
    "Lampe": "Lampen", "Leuchte": "Leuchten", "Ventilator": "Ventilatoren", "Lüfter": "Lüfter",
    "Fernseher": "Fernseher", "Radio": "Radios", "Steckdose": "Steckdosen", "Rollladen": "Rollläden",
    "Rollo": "Rollos", "Jalousie": "Jalousien", "TV": "TVs", "Glotze": "Glotzen",
}
_GENDER = {"Rollo": Gender.NEUTER, "Jalousie": Gender.FEMININE, "Leuchte": Gender.FEMININE,
           "Lampe": Gender.FEMININE, "Glotze": Gender.FEMININE, "Lüfter": Gender.MASCULINE}
_ARTICLE = {Gender.MASCULINE: "den", Gender.FEMININE: "die", Gender.NEUTER: "das"}


@lru_cache(maxsize=1)
def _entities():
    return tuple(house_entities())


def _rooms(key):
    rooms: dict[str, list] = {}
    for entity in genus_members(key, list(_entities())):
        if entity.area_id is not None:
            rooms.setdefault(entity.area_id, []).append(entity)
    return rooms


def _cases():
    for key, operations in _OPERATIONS.items():
        for area_id, members in sorted(_rooms(key).items()):
            area_name = members[0].area_name
            for noun in _NOUNS[key]:
                for particle, service in operations:
                    yield key, area_id, area_name, noun, particle, service, tuple(
                        sorted(entity.entity_id for entity in members)
                    )


_CASES = list(_cases())
_SHELLS = {
    "command": "Mach {np} {place} {p}.",
    "place_first": "{Place} mach {np} {p}.",
    "polite": "Kannst du {np} {place} {p}machen?",
    "verbless": "Bitte {np} {place} {p}.",
}


def _phrase(noun: str, plural: bool) -> str:
    if plural:
        return f"alle {_PLURALS[noun]}"
    gender = _GENDER.get(noun) or genus_gender(noun)
    return f"{_ARTICLE[gender]} {noun}"


def genus_gender(noun: str) -> Gender:
    for key in _NOUNS:
        if noun in _NOUNS[key]:
            return genus(key).gender
    return Gender.NEUTER


def _say(monkeypatch, shell, case, plural):
    key, area_id, area_name, noun, particle, service, members = case
    place = dative_location_phrase(area_name)
    text = _SHELLS[shell].format(
        np=_phrase(noun, plural), place=place, Place=place[:1].upper() + place[1:], p=particle,
    )
    return text, HouseConversation(monkeypatch).say(text)


@pytest.mark.parametrize("shell,case", list(itertools.product(_SHELLS, _CASES)))
def test_singular_genus_at_place(monkeypatch, shell, case):
    members = case[-1]
    text, turn = _say(monkeypatch, shell, case, plural=False)
    if len(members) == 1:
        assert turn.targets == set(members), (text, turn.speech)
        assert all(service == case[5] or service.endswith(case[5].split("_")[-1]) for _, service, _ in turn.calls), text
    else:
        assert turn.calls == [], (text, turn.speech)
        assert "?" in turn.speech, (text, turn.speech)


@pytest.mark.parametrize("shell,case", list(itertools.product(["command", "place_first"], _CASES)))
def test_plural_genus_at_place_is_every_member(monkeypatch, shell, case):
    text, turn = _say(monkeypatch, shell, case, plural=True)
    assert turn.targets == set(case[-1]), (text, turn.speech)


@pytest.mark.parametrize("case", _CASES)
def test_negated_command_never_operates(monkeypatch, case):
    key, area_id, area_name, noun, particle, service, members = case
    text = f"Mach {_phrase(noun, False)} {dative_location_phrase(area_name)} nicht {particle}."
    assert HouseConversation(monkeypatch).say(text).calls == [], text


@pytest.mark.parametrize("key,noun", [
    ("fan", "Ventilator"), ("tv", "Fernseher"), ("shutter", "Rollladen"), ("radio", "Radio"),
])
def test_missing_genus_at_place_names_genus_and_place(monkeypatch, key, noun):
    rooms = _rooms(key)
    area = next(
        entity for entity in _entities()
        if entity.area_id is not None and entity.area_id not in rooms and entity.domain == "light"
    )
    place = dative_location_phrase(area.area_name)
    turn = HouseConversation(monkeypatch).say(f"Mach {_phrase(noun, False)} {place} an.")
    assert turn.calls == []
    assert noun in turn.speech and area.area_name in turn.speech, turn.speech
    assert "freigegebenes Gerät" not in turn.speech


@pytest.mark.parametrize("level,floor_id", [("oben", "obergeschoss"), ("unten", "erdgeschoss"), ("im Keller", "keller")])
@pytest.mark.parametrize("shape", ["Mach alle Lampen {l} aus.", "Mach die Lichter {l} aus.", "{L} alle Lichter aus."])
def test_floor_words_follow_the_house_floor_aliases(monkeypatch, level, floor_id, shape):
    expected = {
        entity.entity_id for entity in genus_members("light", list(_entities()))
        if entity.floor_id == floor_id
    }
    names = {e.entity_id: e.friendly_name for e in _entities()}
    text = shape.format(l=level, L=level[:1].upper() + level[1:])
    from custom_components.homeintent.nlu.semantic_catalog import GROUP_PREVIEW_THRESHOLD

    house = HouseConversation(monkeypatch)
    turn = house.say(text)
    if turn.calls == [] and len(expected) > GROUP_PREVIEW_THRESHOLD:
        # Large groups may be previewed first; the preview names every member.
        assert all(names[item] in turn.speech for item in expected), (text, turn.speech)
        turn = house.say("Ja")
    assert turn.targets == expected, (text, turn.speech)


@pytest.mark.parametrize("quantifier", ["beide", "alle", "die zwei"])
def test_quantified_kind_word_is_a_kind_even_if_it_is_a_name(quantifier):
    from custom_components.homeintent.entities import EntitySnapshot
    from custom_components.homeintent.nlu.language_frontend import analyse_language
    from custom_components.homeintent.nlu.ontology_compiler import compile_ontology_command

    entities = [
        EntitySnapshot("cover.rollladen", "Rollladen", "cover", "closed", area_id="flur", area_name="Flur"),
        EntitySnapshot("cover.a", "Rolllade Poleraum links", "cover", "closed", area_id="poleraum", area_name="Poleraum"),
        EntitySnapshot("cover.b", "Rolllade Poleraum rechts", "cover", "closed", area_id="poleraum", area_name="Poleraum"),
    ]
    document = analyse_language(f"Fahre {quantifier} Rollladen im Poleraum hoch", entities)
    compiled = compile_ontology_command(document, entities)
    assert compiled is not None
    assert {e.entity_id for r in compiled.results for e in r.resolved_entities} == {"cover.a", "cover.b"}


@pytest.mark.parametrize("tone,kelvin", [
    ("warmweiß", 2700), ("neutralweiß", 4000), ("neutral weiß", 4000),
    ("tageslichtweiß", 5500), ("kaltweiß", 6500),
])
@pytest.mark.parametrize("shape", ["Stell {t} auf {w}.", "Mach {t} {w}."])
@pytest.mark.parametrize("target,expected", [
    ("das Bürolicht", {"light.buerolicht"}),
    ("das Licht im Wohnzimmer", {"light.wohnzimmer_deckenlicht", "light.wohnzimmer_led_streifen"}),
    ("das Licht im Büro", {"light.buerolicht"}),
])
def test_white_tone_reaches_every_capable_light(monkeypatch, tone, kelvin, shape, target, expected):
    turn = HouseConversation(monkeypatch).say(shape.format(t=target, w=tone))
    assert turn.targets == expected, turn.speech
    assert all(data.get("color_temp_kelvin") == kelvin for _, _, data in turn.calls)


def test_white_tone_on_incapable_light_says_so(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Stell die Stehlampe auf warmweiß.")
    assert turn.calls == [] and "Stehlampe" in turn.speech and "warmweiß" in turn.speech
