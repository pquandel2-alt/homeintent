"""Release 7.3.0 section 2: need semantics, generated.

Sensation words x sentence frames x places (spoken, satellite) are combined
freely; the effect must reach exactly the devices of the right kind at the
right place, in the right direction.  Questions, negations and hypotheticals
never operate.  Sentences are built here, not taken from ``sim/``.
"""

from __future__ import annotations


import pytest

from _testhaus import HouseConversation as _HouseConversation, house_entities, with_states
from custom_components.homeintent.nlu.german_morphology import dative_location_phrase
from custom_components.homeintent.nlu.target_resolution import genus_members



def HouseConversation(monkeypatch, *args, options=None, **kwargs):
    """The need semantics below are checked with ``low_risk_auto`` (LOW needs
    act directly). The default ``propose`` asks first; see
    ``test_needs_are_proposed_by_default`` and ``tests/test_bindings.py``."""
    return _HouseConversation(
        monkeypatch, *args, options={"implicit_action_level": "low_risk_auto", **(options or {})},
        **kwargs,
    )


_COLD = ["kalt", "zu kalt", "ziemlich frisch", "eisig"]
_WARM = ["warm", "zu warm", "heiß", "schwül"]
_FRAMES = ["Mir ist {a}{p}.", "Es ist {a}{p}.", "{P} ist es {a}."]
_ENTITIES = house_entities()
_HEATED_ROOMS = sorted({
    (e.area_id, e.area_name) for e in genus_members("heating", _ENTITIES) if e.area_id
})


def _sentence(frame: str, adjective: str, area_name: str | None) -> str:
    place = dative_location_phrase(area_name) if area_name else ""
    if "{P}" in frame:
        return frame.format(a=adjective, P=place[:1].upper() + place[1:])
    return frame.format(a=adjective, p=f" {place}" if place else "")


def _heating_at(area_id):
    return {e.entity_id for e in genus_members("heating", _ENTITIES) if e.area_id == area_id}


def _setpoint_delta(house, turn):
    before = {e.entity_id: e.attributes.get("temperature") for e in house.entities}
    return [data["temperature"] - before[data.get("entity_id") if isinstance(data.get("entity_id"), str) else data["entity_id"][0]]
            for _, service, data in turn.calls if service == "set_temperature"]


@pytest.mark.parametrize("words,sign", [(_COLD, 1), (_WARM, -1)])
@pytest.mark.parametrize("frame", _FRAMES)
@pytest.mark.parametrize("room", _HEATED_ROOMS)
def test_temperature_need_at_spoken_place(monkeypatch, words, sign, frame, room):
    area_id, area_name = room
    for adjective in words:
        house = HouseConversation(monkeypatch)
        text = _sentence(frame, adjective, area_name)
        turn = house.say(text)
        assert turn.targets == _heating_at(area_id), (text, turn.speech)
        assert all(delta * sign > 0 for delta in _setpoint_delta(house, turn)), (text, turn.speech)


@pytest.mark.parametrize("sentence,sign", [
    ("Mir ist kalt.", 1), ("Ich friere.", 1), ("Ich fröstele.", 1),
    ("Mir ist zu warm.", -1), ("Ich schwitze.", -1),
])
@pytest.mark.parametrize("room", _HEATED_ROOMS)
def test_temperature_need_at_satellite_place(monkeypatch, sentence, sign, room):
    area_id, _ = room
    house = HouseConversation(monkeypatch, area=area_id)
    turn = house.say(sentence)
    assert turn.targets == _heating_at(area_id), (sentence, turn.speech)
    assert all(delta * sign > 0 for delta in _setpoint_delta(house, turn))


_LIT_ROOMS = sorted({
    (e.area_id, e.area_name) for e in genus_members("light", _ENTITIES)
    if e.area_id and e.area_id not in {"garten", "terrasse"}
})


@pytest.mark.parametrize("sentence", ["Es ist zu dunkel.", "Ich sehe nichts.", "Hier ist es finster."])
@pytest.mark.parametrize("room", _LIT_ROOMS)
def test_darkness_brightens_only_lights_of_the_room(monkeypatch, sentence, room):
    area_id, _ = room
    lights = {e.entity_id for e in genus_members("light", _ENTITIES) if e.area_id == area_id}
    turn = HouseConversation(monkeypatch, area=area_id).say(sentence)
    assert turn.targets and turn.targets <= lights, (sentence, area_id, turn.speech)


@pytest.mark.parametrize("sentence", ["Es ist zu laut.", "Das ist mir zu laut."])
def test_loudness_lowers_the_playing_player(monkeypatch, sentence):
    house = HouseConversation(
        monkeypatch, with_states(house_entities(), media_player__kuechenradio="playing"), area="kuche",
    )
    turn = house.say(sentence)
    assert turn.targets == {"media_player.kuechenradio"}
    assert all(service == "volume_down" for _, service, _ in turn.calls)


@pytest.mark.parametrize("sentence", ["Hier ist es stickig.", "Es ist muffig hier.", "Die Luft ist verbraucht."])
def test_stale_air_runs_the_fan_of_the_room(monkeypatch, sentence):
    turn = HouseConversation(monkeypatch, area="badezimmer").say(sentence)
    assert turn.targets == {"fan.badluefter"}, turn.speech


@pytest.mark.parametrize("sentence", [
    "Ist dir kalt?", "Mir ist nicht kalt.", "Wäre es kalt, würde ich frieren.",
    "Gestern war mir kalt.", "Ist es im Büro warm?", "Mir ist überhaupt nicht warm.",
])
def test_non_assertive_needs_never_operate(monkeypatch, sentence):
    turn = HouseConversation(monkeypatch, area="buro").say(sentence)
    assert turn.calls == [], (sentence, turn.speech)


@pytest.mark.parametrize("sentence,routine", [
    ("Ich gehe schlafen.", "Gute Nacht"), ("Ich gehe jetzt ins Bett.", "Gute Nacht"),
    ("Ich verlasse das Haus.", "Abwesend"), ("Ich bin dann mal weg.", "Abwesend"),
])
def test_routine_statements_propose_the_matching_routine(monkeypatch, sentence, routine):
    turn = HouseConversation(monkeypatch).say(sentence)
    assert turn.calls == [] and routine in turn.speech and "?" in turn.speech, turn.speech


@pytest.mark.parametrize("sentence", ["Ich will fernsehen.", "Ich möchte einen Film schauen."])
def test_movie_need_proposes_the_movie_scene(monkeypatch, sentence):
    # 7.3.1 (S5): a routine derived from a statement is a proposal, scenes
    # included; only the "Ja" starts it.
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert turn.calls == [] and "Filmabend" in turn.speech and "?" in turn.speech, turn.speech
    assert house.say("Ja.").targets == {"scene.filmabend"}


@pytest.mark.parametrize("sentence", ["Ich hätte gern etwas mehr Wärme im Büro.", "Ich hätte gerne mehr Wärme im Büro."])
def test_wish_for_more_warmth_is_a_need(monkeypatch, sentence):
    assert HouseConversation(monkeypatch).say(sentence).targets == {"climate.heizung_buero"}


def test_needs_are_proposed_by_default(monkeypatch):
    # 7.3.3: implicit_action_level defaults to "propose": understood, asked,
    # executed only after "Ja".
    house = _HouseConversation(monkeypatch, area="buro")
    turn = house.say("Mir ist kalt.")
    assert turn.calls == [], turn.speech
    assert turn.speech.startswith("Soll ich") and "Büro" in turn.speech and turn.speech.endswith("?"), turn.speech
    assert house.say("Ja.").targets == {"climate.heizung_buero"}


def test_understand_only_never_acts(monkeypatch):
    house = _HouseConversation(monkeypatch, area="buro", options={"implicit_action_level": "understand_only"})
    turn = house.say("Mir ist kalt.")
    assert turn.calls == [] and "verstanden" in turn.speech, turn.speech
