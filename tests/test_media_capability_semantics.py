"""Media commands use shared places and evidenced player capabilities."""

from __future__ import annotations

import pytest

from homeintent.entities import EntitySnapshot
from homeintent.media import MediaRequest, parse_media_request, resolve_media


def _player(
    entity_id: str,
    name: str,
    area_id: str,
    area_name: str,
    *,
    state: str = "idle",
    capabilities: tuple[str, ...] = ("PLAY", "PAUSE", "STOP", "SELECT_SOURCE"),
    sources: tuple[str, ...] = (),
    floor_id: str = "ground",
    floor_name: str = "Erdgeschoss",
    floor_aliases: tuple[str, ...] = ("EG",),
) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "media_player", state,
        area_id=area_id, area_name=area_name,
        floor_id=floor_id, floor_name=floor_name, floor_aliases=floor_aliases,
        attributes={"source_list": list(sources)},
        capabilities=frozenset(capabilities), integration="sonos",
    )


@pytest.mark.parametrize(
    ("text", "op"),
    [
        ("Musik in der Küche an", "on"),
        ("Radio in der Küche an", "on"),
        ("Schalte das Radio in der Küche ein", "on"),
        ("Radio in der Küche aus", "off"),
        ("Musik in der Küche aus", "off"),
        ("Pause in der Küche", "pause"),
        ("Stoppe das Radio in der Küche", "stop"),
        ("Spiel WDR 2 in der Küche", "play"),
    ],
)
def test_media_language_composes_transport_and_source(text, op):
    request = parse_media_request(text)
    assert request is not None and request.op == op


@pytest.mark.parametrize("text", ["Ist das Radio aus?", "Welche Radios sind an?"])
def test_media_state_questions_are_never_parsed_as_power_commands(text):
    assert parse_media_request(text) is None


def test_place_is_a_hard_player_constraint_and_floor_alias_is_shared():
    kitchen = _player("media_player.kitchen", "Radio", "kitchen", "Küche", state="paused")
    office = _player("media_player.office", "Radio", "office", "Büro", state="playing")

    in_kitchen = resolve_media(
        parse_media_request("Pause in der Küche") or MediaRequest("pause"),
        [kitchen, office], satellite_area_id=None,
    )
    on_floor = resolve_media(
        parse_media_request("Pause im EG") or MediaRequest("pause"),
        [kitchen], satellite_area_id=None,
    )

    assert in_kitchen.plan is not None
    assert in_kitchen.plan.entity_id == kitchen.entity_id
    assert on_floor.plan is not None
    assert on_floor.plan.entity_id == kitchen.entity_id


def test_multiple_players_at_named_place_ask_and_absent_place_rejects():
    first = _player("media_player.one", "Sonos links", "kitchen", "Küche")
    second = _player("media_player.two", "Sonos rechts", "kitchen", "Küche")
    office_marker = EntitySnapshot(
        "light.office", "Bürolicht", "light", "off", area_id="office", area_name="Büro"
    )

    ambiguous = resolve_media(
        parse_media_request("Musik in der Küche an") or MediaRequest("on"),
        [first, second, office_marker], satellite_area_id=None,
    )
    absent = resolve_media(
        parse_media_request("Musik im Büro an") or MediaRequest("on"),
        [first, office_marker], satellite_area_id=None,
    )

    assert ambiguous.plan is None and ambiguous.question
    assert absent.plan is None and "kein Mediengerät" in absent.spoken


def test_absent_satellite_player_never_falls_back_to_playing_player_elsewhere():
    kitchen = _player(
        "media_player.kitchen", "Küchenradio", "kitchen", "Küche",
        state="playing",
    )

    resolved = resolve_media(
        MediaRequest("off", text="Mach das Radio aus"),
        [kitchen],
        satellite_area_id="bathroom",
    )

    assert resolved.plan is None
    assert "Sprachsatelliten" in resolved.spoken
    assert "nichts ausgeführt" in resolved.spoken


def test_generic_radio_does_not_retype_a_local_speaker():
    speaker = _player(
        "media_player.bedroom", "Lautsprecher Schlafzimmer", "bedroom",
        "Schlafzimmer", state="idle", sources=("Spotify",),
    )
    radio = _player(
        "media_player.kitchen", "Küchenradio", "kitchen", "Küche",
        state="playing", sources=("WDR 2",),
    )

    resolved = resolve_media(
        parse_media_request("Mach das Radio an") or MediaRequest("on"),
        [speaker, radio],
        satellite_area_id="bedroom",
    )

    assert resolved.plan is None
    assert "nichts ausgeführt" in resolved.spoken


def test_sonos_without_power_services_pauses_and_resumes_without_turn_off():
    playing = _player("media_player.kitchen", "Sonos Küche", "kitchen", "Küche", state="playing")
    paused = _player("media_player.kitchen", "Sonos Küche", "kitchen", "Küche", state="paused")

    off = resolve_media(MediaRequest("off"), [playing], satellite_area_id="kitchen")
    on = resolve_media(MediaRequest("on"), [paused], satellite_area_id="kitchen")

    assert off.plan is not None and off.plan.service == "media_pause"
    assert on.plan is not None and on.plan.service == "media_play"
    assert off.plan.service != "turn_off"


def test_explicit_power_on_uses_turn_on_only_when_evidenced():
    powerless = _player(
        "media_player.kitchen", "Sonos Küche", "kitchen", "Küche",
        state="off", capabilities=("PLAY", "PAUSE"),
    )
    powered = _player(
        "media_player.tv", "Fernseher", "living", "Wohnzimmer",
        state="off", capabilities=("TURN_ON",),
    )

    rejected = resolve_media(MediaRequest("on"), [powerless], satellite_area_id="kitchen")
    accepted = resolve_media(MediaRequest("on"), [powered], satellite_area_id="living")

    assert rejected.plan is None
    assert accepted.plan is not None and accepted.plan.service == "turn_on"


@pytest.mark.parametrize("said", ["WDR 2", "WDR2", "wdr zwei"])
def test_station_spelling_normalizes_only_to_one_evidenced_source(said):
    player = _player(
        "media_player.kitchen", "Sonos Küche", "kitchen", "Küche",
        sources=("WDR 2", "Bayern 3"),
    )
    request = parse_media_request(f"Spiel {said} in der Küche")
    assert request is not None

    resolved = resolve_media(request, [player], satellite_area_id=None)

    assert resolved.plan is not None
    assert resolved.plan.service == "select_source"
    assert resolved.plan.data == {"source": "WDR 2"}


def test_unknown_or_ambiguous_station_never_creates_a_source():
    player = _player(
        "media_player.kitchen", "Sonos Küche", "kitchen", "Küche",
        sources=("WDR 2", "WDR2"),
    )
    unknown = resolve_media(
        parse_media_request("Spiel Erfunden FM in der Küche") or MediaRequest("play"),
        [player], satellite_area_id=None,
    )
    ambiguous = resolve_media(
        parse_media_request("Spiel wdr zwei in der Küche") or MediaRequest("play"),
        [player], satellite_area_id=None,
    )

    assert unknown.plan is None
    assert ambiguous.plan is None


def test_start_exact_player_name_is_transport_not_an_invented_source():
    player = _player(
        "media_player.radio_kitchen", "Radio Küche", "kitchen", "Küche",
        state="idle", sources=("WDR 2",),
    )
    request = parse_media_request("Starte Radio Küche")
    assert request is not None

    resolved = resolve_media(request, [player], satellite_area_id=None)

    assert resolved.plan is not None
    assert resolved.plan.service == "media_play"
