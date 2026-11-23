"""Natural, compositional radio playback regressions for 7.15.1.

These are not production sentence templates.  The matrix varies action,
source spelling and word order while asserting the same typed PLAY meaning;
the runtime still grounds the exact station and exactly one MA player before
the first write.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation  # noqa: E402
from homeintent.media import parse_media_request  # noqa: E402
from homeintent.media_coverage import analyse_media_coverage  # noqa: E402
from test_music_assistant_710 import _player, _response  # noqa: E402


def _studio_player(**changes):
    values = {
        "entity_id": "media_player.ma_studio",
        "friendly_name": "Studio-Lautsprecher",
        "aliases": ("Studiolautsprecher",),
        "area_id": "polraum",
        "area_name": "Polraum",
        **changes,
    }
    return replace(_player(), **values)


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("Spiele eins live im Polraum", ("eins", "live")),
        ("Spiele 1live im Polraum", ("1live",)),
        ("Spiele wdr2 im Polraum", ("wdr2",)),
        ("Spiele 1live über den Studio Lautsprecher", ("1live",)),
        ("Mach Radio Energy im Polraum an", ("radio", "energy")),
        ("Ich möchte im Polraum 1Live hören", ("1live",)),
        ("Lass im Polraum Radio Energy laufen", ("radio", "energy")),
        ("Auf dem Studio-Lautsprecher bitte WDR zwei", ("wdr", "zwei")),
        ("Kannst du dort Eins Live abspielen?", ("eins", "live")),
        ("Wechsel auf WDR zwei", ("wdr", "zwei")),
    ],
)
def test_natural_radio_word_orders_have_one_complete_typed_meaning(text, source):
    player = _studio_player()

    request = parse_media_request(text, [player])

    assert request is not None
    assert request.op == "play"
    assert request.source_words == source
    coverage = analyse_media_coverage(text, [player], request)
    assert coverage.complete, (coverage.residue, coverage.coordinations, coverage.conflicts)


@pytest.mark.parametrize(
    ("text", "media_id"),
    [
        ("Spiele 1live im Polraum", "library://radio/1live"),
        ("Spiele wdr2 im Polraum", "library://radio/wdr2"),
        ("Mach Radio Energy im Polraum an", "library://radio/energy"),
        ("Ich möchte im Polraum 1Live hören", "library://radio/1live"),
        ("Lass im Polraum Radio Energy laufen", "library://radio/energy"),
        ("Kannst du dort Eins Live abspielen?", "library://radio/1live"),
    ],
)
def test_natural_radio_commands_search_then_play_exact_station(monkeypatch, text, media_id):
    player = _studio_player()
    house = HouseConversation(monkeypatch, entities=[player], area="polraum")
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = _response(
        radio=[
            {"name": "1LIVE", "uri": "library://radio/1live"},
            {"name": "WDR 2", "uri": "library://radio/wdr2"},
            {"name": "ENERGY", "uri": "library://radio/energy"},
        ]
    )

    turn = house.say(text)

    assert turn.calls[-1][:2] == ("music_assistant", "play_media")
    assert turn.calls[-1][2]["media_id"] == media_id
    assert turn.calls[-1][2]["entity_id"] == player.entity_id


def test_explicit_native_sonos_name_links_to_unique_ma_player_in_same_area(monkeypatch):
    native = _studio_player(
        entity_id="media_player.sonos_studio",
        integration="sonos",
        config_entry_id=None,
    )
    ma_player = _studio_player(
        friendly_name="Studio via Music Assistant",
        aliases=(),
    )
    house = HouseConversation(monkeypatch, entities=[native, ma_player])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = _response(
        radio=[{"name": "1LIVE", "uri": "library://radio/1live"}]
    )

    turn = house.say("Spiele 1live über den Studio-Lautsprecher")

    assert turn.calls[-1][:2] == ("music_assistant", "play_media")
    assert turn.calls[-1][2]["entity_id"] == ma_player.entity_id


@pytest.mark.parametrize(
    "text",
    [
        "Spiele 1live auf Studio",
        "Spiele 1live auf dem Studio Lautsprecher",
        "Spiele Radio 1live auf dem Studio Lautsprecher",
    ],
)
def test_registry_name_plus_media_genus_is_one_player_phrase(monkeypatch, text):
    """``Studio`` + ``Lautsprecher`` qualifies one target, not two.

    A common HA setup names the native Sonos entity only after its room and
    lets the spoken device noun supply the type.  The natural compound must
    retain the exact same target evidence as the bare registry name and then
    link to the unique MA player in that area.
    """
    native = _studio_player(
        entity_id="media_player.sonos_studio",
        friendly_name="Studio",
        aliases=(),
        integration="sonos",
        config_entry_id=None,
    )
    ma_player = _studio_player(
        friendly_name="Studio via Music Assistant",
        aliases=(),
    )
    request = parse_media_request(text, [native, ma_player])

    assert request is not None and request.source_words in {
        ("1live",),
        ("radio", "1live"),
    }
    coverage = analyse_media_coverage(text, [native, ma_player], request)
    assert coverage.complete, (coverage.residue, coverage.conflicts)

    house = HouseConversation(monkeypatch, entities=[native, ma_player])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = _response(
        radio=[{"name": "1LIVE", "uri": "library://radio/1live"}]
    )

    turn = house.say(text)

    assert turn.calls[-1][:2] == ("music_assistant", "play_media")
    assert turn.calls[-1][2]["media_id"] == "library://radio/1live"
    assert turn.calls[-1][2]["entity_id"] == ma_player.entity_id


def test_registry_name_plus_genus_never_merges_a_second_registry_target():
    studio = _studio_player(friendly_name="Studio", aliases=())
    kitchen = _studio_player(
        entity_id="media_player.ma_kueche",
        friendly_name="Küche",
        aliases=(),
        area_id="kueche",
        area_name="Küche",
    )
    text = "Spiele 1live auf Studio Küche Lautsprecher"
    request = parse_media_request(text, [studio, kitchen])

    assert request is not None
    coverage = analyse_media_coverage(text, [studio, kitchen], request)

    assert not coverage.complete
    assert coverage.conflicts == ("target",)


def test_native_sonos_mapping_never_guesses_between_two_ma_players(monkeypatch):
    native = _studio_player(
        entity_id="media_player.sonos_studio",
        integration="sonos",
        config_entry_id=None,
    )
    first = _studio_player(friendly_name="MA Studio links", aliases=())
    second = _studio_player(
        entity_id="media_player.ma_studio_rechts",
        friendly_name="MA Studio rechts",
        aliases=(),
    )
    house = HouseConversation(monkeypatch, entities=[native, first, second])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)

    turn = house.say("Spiele 1live über den Studio-Lautsprecher")

    assert "mehrere Music-Assistant-Player" in turn.speech
    assert not any(call[:2] == ("music_assistant", "play_media") for call in turn.calls)


def test_unknown_location_tail_is_residue_and_never_part_of_station_name():
    player = _studio_player()
    text = "Spiele 1live im Quirlomat"

    request = parse_media_request(text, [player])

    assert request is not None and request.source_words == ("1live",)
    coverage = analyse_media_coverage(text, [player], request)
    assert not coverage.complete
    assert coverage.residue == ("im", "Quirlomat")


def test_second_clause_is_never_swallowed_by_free_text_station_name():
    player = _studio_player()
    text = "Spiele 1live im Polraum und schalte danach das Licht aus"

    request = parse_media_request(text, [player])

    assert request is not None and request.source_words == ("1live",)
    coverage = analyse_media_coverage(text, [player], request)
    assert not coverage.complete
    assert coverage.coordinations


@pytest.mark.parametrize(
    "text",
    [
        "Bitte auf dem Studio-Lautsprecher weiterspielen",
        "Wäre es möglich, auf dem Studio-Lautsprecher weiterzuspielen?",
        "Sorge bitte dafür, dass der Studio-Lautsprecher weiterspielt",
    ],
)
def test_resume_morphology_is_never_mistaken_for_a_library_source(text):
    assert parse_media_request(text, [_studio_player()]) is None


@pytest.mark.parametrize(
    "text",
    [
        "Dreh das Radio im Polraum runter",
        "Drehe den Studio-Lautsprecher hoch",
    ],
)
def test_colloquial_volume_particles_are_not_library_sources(text):
    assert parse_media_request(text, [_studio_player()]) is None
