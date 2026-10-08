"""7.9.3 B3: music and media.

Generated commands × target choice (room named, device named, satellite
room, the playing one, ambiguous) × player states.  Sources only from
``source_list``; "Musik" uses the last source or asks.  Every write goes
through the one device path (policy, ``service_executor``), the effect is
judged like every other command (``effect_wait``).  "Was läuft gerade?"
reads title and artist from the attributes.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities, with_states

_ha_stub.install()

RADIO = "media_player.kuechenradio"
SPEAKER = "media_player.lautsprecher_schlafzimmer"
TV = "media_player.wohnzimmer_tv"


def _house(monkeypatch, tmp_path, entities=None, area=None):
    return HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS, area=area)


def _media_calls(turn):
    return [(service, data) for domain, service, data in turn.calls if domain == "media_player"]


_PLAIN = {
    "pause": ("Pause.", "Pausiere die Musik.", "Mach mal Pause.", "Pausiere das Radio."),
    "resume": ("Weiter.", "Weiterspielen.", "Mach weiter mit der Musik.", "Weiter mit der Musik."),
    "next": ("Nächstes Lied.", "Nächster Titel.", "Spiel das nächste Lied.", "Überspring das Lied.",
             "Den nächsten Song bitte."),
    "previous": ("Vorheriges Lied.", "Spiel den vorherigen Titel."),
    "louder": ("Lauter.", "Mach lauter.", "Etwas lauter.", "Mach die Musik lauter.", "Bitte lauter."),
    "quieter": ("Leiser.", "Mach leiser.", "Etwas leiser bitte.", "Mach die Musik leiser."),
    "off": ("Mach die Musik aus.", "Musik aus.", "Schalte die Musik aus."),
}
_SERVICES = {"pause": "media_pause", "resume": "media_play", "next": "media_next_track",
             "previous": "media_previous_track", "louder": "volume_up", "quieter": "volume_down", "off": "turn_off"}


@pytest.mark.parametrize("op,text", [(op, text) for op, texts in _PLAIN.items() for text in texts])
def test_short_commands_reach_the_playing_player(monkeypatch, tmp_path, op, text):
    turn = _house(monkeypatch, tmp_path).say(text)
    assert _media_calls(turn) == [(_SERVICES[op], {"entity_id": RADIO})], (text, turn.speech)


@pytest.mark.parametrize("op,text", [(op, texts[0]) for op, texts in _PLAIN.items()])
def test_the_satellite_room_wins(monkeypatch, tmp_path, op, text):
    entities = with_states(house_entities(), media_player__lautsprecher_schlafzimmer="playing")
    turn = _house(monkeypatch, tmp_path, entities=entities, area="schlafzimmer").say(text)
    assert _media_calls(turn) == [(_SERVICES[op], {"entity_id": SPEAKER})], (text, turn.speech)


@pytest.mark.parametrize("text", ["Pause.", "Lauter.", "Nächstes Lied."])
def test_two_playing_without_room_ask(monkeypatch, tmp_path, text):
    entities = with_states(house_entities(), media_player__lautsprecher_schlafzimmer="playing")
    turn = _house(monkeypatch, tmp_path, entities=entities).say(text)
    assert turn.speech.startswith("Welches Gerät meinst du: ") and _media_calls(turn) == [], turn.speech


@pytest.mark.parametrize("text", ["Pause.", "Leiser.", "Nächstes Lied."])
def test_nothing_playing_is_honest(monkeypatch, tmp_path, text):
    entities = with_states(house_entities(), media_player__kuechenradio="paused")
    turn = _house(monkeypatch, tmp_path, entities=entities).say(text)
    assert turn.speech == "Gerade spielt nichts." and _media_calls(turn) == []


@pytest.mark.parametrize("value", [0, 25, 30, 100])
@pytest.mark.parametrize("form", ["Lautstärke {v}.", "Lautstärke auf {v}.", "Stell die Lautstärke auf {v} Prozent."])
def test_volume(monkeypatch, tmp_path, value, form):
    turn = _house(monkeypatch, tmp_path).say(form.format(v=value))
    assert _media_calls(turn) == [("volume_set", {"volume_level": value / 100, "entity_id": RADIO})], turn.speech


_SOURCES = (("Bayern 3", "in der Küche", RADIO), ("Deutschlandfunk", "in der Küche", RADIO),
            ("Spotify", "im Schlafzimmer", SPEAKER), ("Tagesschau", "im Wohnzimmer", TV))


@pytest.mark.parametrize("verb", ["Spiel", "Spiele", "Spiel bitte", "Leg"])
@pytest.mark.parametrize("source,room,player", _SOURCES)
def test_play_a_source_in_a_room(monkeypatch, tmp_path, verb, source, room, player):
    turn = _house(monkeypatch, tmp_path).say(f"{verb} {source} {room}.")
    assert _media_calls(turn) == [("select_source", {"source": source, "entity_id": player})], turn.speech


@pytest.mark.parametrize("source,player", [("Bayern 3", RADIO), ("Einschlafgeräusche", SPEAKER), ("Netflix", TV)])
def test_a_source_without_room_finds_its_player(monkeypatch, tmp_path, source, player):
    turn = _house(monkeypatch, tmp_path).say(f"Spiel {source}.")
    assert _media_calls(turn) == [("select_source", {"source": source, "entity_id": player})], turn.speech


@pytest.mark.parametrize("text", ["Spiel Antenne 1 in der Küche.", "Spiel Radio Gaga im Schlafzimmer."])
def test_an_unknown_source_is_never_guessed(monkeypatch, tmp_path, text):
    turn = _house(monkeypatch, tmp_path).say(text)
    assert "finde ich bei" in turn.speech and "Verfügbar:" in turn.speech and _media_calls(turn) == []
    assert ("Antenne 1" in turn.speech) or ("Radio Gaga" in turn.speech)


@pytest.mark.parametrize("form", ["Spiel Musik {r}.", "Spiel was {r}.", "Mach Musik an {r}.", "Spiele Musik {r} ab."])
def test_music_uses_the_last_source(monkeypatch, tmp_path, form):
    entities = house_entities()
    speaker = next(entity for entity in entities if entity.entity_id == SPEAKER)
    entities = [replace(speaker, attributes={**speaker.attributes, "source": "Spotify"}) if entity is speaker
                else entity for entity in entities]
    turn = _house(monkeypatch, tmp_path, entities=entities).say(form.format(r="im Schlafzimmer"))
    assert _media_calls(turn) == [("media_play", {"entity_id": SPEAKER})], turn.speech


def test_music_without_a_last_source_asks(monkeypatch, tmp_path):
    turn = _house(monkeypatch, tmp_path).say("Spiel Musik im Wohnzimmer.")
    assert turn.speech.startswith("Was soll Wohnzimmer TV spielen? Zum Beispiel: ") and _media_calls(turn) == []


@pytest.mark.parametrize("text", ["Was läuft gerade?", "Was läuft?", "Welches Lied läuft gerade?",
                                  "Was spielt das Radio gerade?", "Was läuft in der Küche?", "Wer singt das gerade?"])
def test_now_playing(monkeypatch, tmp_path, text):
    turn = _house(monkeypatch, tmp_path).say(text)
    assert turn.speech == "Auf Küchenradio läuft „Morgenmagazin“ von Radio Bob.", turn.speech
    assert turn.calls == []


def test_now_playing_nothing(monkeypatch, tmp_path):
    entities = with_states(house_entities(), media_player__kuechenradio="paused")
    assert _house(monkeypatch, tmp_path, entities=entities).say("Was läuft gerade?").speech == "Gerade läuft nichts."


def test_device_named_commands_keep_their_path(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Mach das Küchenradio lauter.")
    assert _media_calls(turn) == [("volume_up", {"entity_id": RADIO})]
    turn = house.say("Nächstes Lied im Schlafzimmer.")
    assert _media_calls(turn) == [("media_next_track", {"entity_id": SPEAKER})]


@pytest.mark.parametrize("text", [
    "Wenn die Musik läuft, mach das Licht aus.", "Spiel nicht so laut.", "Stopp.", "Halt.",
    "Weiter mit dem Plan.", "Was läuft im Garten schief?", "Lauter Unsinn.", "Pausiere alle Medien.",
])
def test_no_media_command(text):
    from homeintent.media import parse_media_request

    request = parse_media_request(text)
    assert request is None or text.startswith("Was läuft im Garten"), (text, request)


def test_media_effects_are_judged():
    from homeintent.effect_wait import EffectExpectation, EffectVerdict, judge

    source = EffectExpectation(RADIO, "Küchenradio", "media_player", "select_source", {"source": "Bayern 3"})
    assert judge(source, "playing", {"source": "Bayern 3"}) is EffectVerdict.REACHED
    assert judge(source, "playing", {"source": "Radio Bob"}) is EffectVerdict.PENDING
    louder = EffectExpectation(RADIO, "Küchenradio", "media_player", "volume_up", {}, prior_position=0.3)
    assert judge(louder, "playing", {"volume_level": 0.4}) is EffectVerdict.REACHED
    assert judge(louder, "playing", {"volume_level": 0.3}) is EffectVerdict.PENDING
    assert judge(louder, "playing", {"volume_level": 0.2}) is EffectVerdict.CONTRARY
    full = EffectExpectation(RADIO, "Küchenradio", "media_player", "volume_up", {}, prior_position=1.0)
    assert judge(full, "playing", {"volume_level": 1.0}) is EffectVerdict.REACHED


def test_play_at_the_satellite(monkeypatch, tmp_path):
    entities = house_entities()
    kitchen = next(entity.area_id for entity in entities if entity.entity_id == RADIO)
    bedroom = next(entity.area_id for entity in entities if entity.entity_id == SPEAKER)
    turn = _house(monkeypatch, tmp_path, area=kitchen).say("Spiel Musik.")
    assert turn.speech == "Küchenradio spielt schon Radio Bob." and _media_calls(turn) == []
    speaker = next(entity for entity in entities if entity.entity_id == SPEAKER)
    entities = [replace(speaker, attributes={**speaker.attributes, "source": "Spotify"}) if entity is speaker
                else entity for entity in entities]
    turn = _house(monkeypatch, tmp_path, entities=entities, area=bedroom).say("Spiel Musik.")
    assert _media_calls(turn) == [("media_play", {"entity_id": SPEAKER})], turn.speech


@pytest.mark.parametrize("text", _PLAIN["resume"])
def test_resume_continues_the_paused_player(monkeypatch, tmp_path, text):
    """Live finding 7.9.3: "Weiter." right after "Pause." asked which device."""
    entities = with_states(house_entities(), media_player__kuechenradio="paused")
    turn = _house(monkeypatch, tmp_path, entities=entities).say(text)
    assert _media_calls(turn) == [("media_play", {"entity_id": RADIO})], turn.speech


def test_resume_with_two_paused_asks(monkeypatch, tmp_path):
    entities = with_states(house_entities(), media_player__kuechenradio="paused",
                           media_player__lautsprecher_schlafzimmer="paused")
    turn = _house(monkeypatch, tmp_path, entities=entities).say("Weiter.")
    assert turn.speech.startswith("Welches Gerät meinst du: ") and _media_calls(turn) == [], turn.speech
