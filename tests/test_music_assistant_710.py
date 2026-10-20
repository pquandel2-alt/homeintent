"""Optional Music Assistant integration: deterministic and fail closed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import _ha_stub

_ha_stub.install()

from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.media import MediaRequest, parse_media_request, resolve_media  # noqa: E402
from homeintent.music_assistant import (  # noqa: E402
    MusicAssistantPlan,
    async_resolve_music_assistant,
)


def _player(
    entity_id: str = "media_player.wohnzimmer",
    *,
    area: str = "Wohnzimmer",
    state: str = "idle",
    integration: str | None = "music_assistant",
    config_entry_id: str | None = "mass-entry",
) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id,
        "Wohnzimmer Lautsprecher",
        "media_player",
        state,
        area_id="living",
        area_name=area,
        integration=integration,
        config_entry_id=config_entry_id,
    )


class Services:
    def __init__(self, response=None, *, search=True, play=True):
        self._available = set()
        if search:
            self._available.add(("music_assistant", "search"))
        if play:
            self._available.add(("music_assistant", "play_media"))
        self.async_call = AsyncMock(return_value=response or {})

    def has_service(self, domain, service):
        return (domain, service) in self._available


def _hass(response=None, *, search=True, play=True):
    return SimpleNamespace(services=Services(response, search=search, play=play))


def _resolve(hass, request, entities, *, satellite_area_id):
    return asyncio.run(async_resolve_music_assistant(
        hass, request, entities, satellite_area_id=satellite_area_id
    ))


@pytest.mark.parametrize(
    ("text", "media_type", "name", "library_only"),
    [
        ("Spiel meine Playlist Entspannen in der Küche.", "playlist", ("entspannen",), True),
        ("Spiel den gespeicherten Sender Radio Bob.", "radio", ("radio", "bob"), True),
        ("Spiele das Album Jazz Night im Wohnzimmer.", "album", ("jazz", "night"), False),
        ("Spiele den Titel One.", "track", ("one",), False),
        ("Spiele Queen im Wohnzimmer.", None, ("queen",), False),
    ],
)
def test_parser_composes_media_type_and_name(text, media_type, name, library_only):
    request = parse_media_request(text)
    assert request is not None
    assert (request.media_type, request.source_words, request.library_only) == (
        media_type,
        name,
        library_only,
    )


@pytest.mark.parametrize(
    ("text", "queue_mode"),
    [
        ("Spiel als Nächstes den Titel One im Wohnzimmer.", "next"),
        ("Spiel den Titel One zur Warteschlange hinzu.", "add"),
        ("Spiel den Titel One und ersetze die Warteschlange.", "replace"),
    ],
)
def test_parser_composes_closed_queue_modes(text, queue_mode):
    request = parse_media_request(text)

    assert request is not None
    assert request.source_words == ("one",)
    assert request.media_type == "track"
    assert request.enqueue == queue_mode


def test_typed_music_plan_rejects_unknown_queue_mode():
    with pytest.raises(ValueError, match="queue"):
        MusicAssistantPlan(
            operation="play",
            media_type="track",
            query="One",
            media_id="library://track/1",
            target_player="media_player.wohnzimmer",
            queue_mode="unsafe",
            resolution_source="test",
            unique=True,
        )


@pytest.mark.parametrize(
    ("text", "op"),
    [
        ("Stoppe die Musik.", "stop"),
        ("Schalte die Musik stumm.", "mute"),
        ("Mach den Ton wieder an.", "unmute"),
        ("Spiel dort weiter.", "resume"),
    ],
)
def test_transport_meanings_are_composed(text, op):
    request = parse_media_request(text)
    assert request is not None and request.op == op


@pytest.mark.parametrize(
    ("op", "service", "data"),
    [
        ("stop", "media_stop", {}),
        ("mute", "volume_mute", {"is_volume_muted": True}),
        ("unmute", "volume_mute", {"is_volume_muted": False}),
    ],
)
def test_transport_plans_use_standard_media_player_services(op, service, data):
    result = resolve_media(MediaRequest(op), [_player(state="playing")], satellite_area_id="living")
    assert result.plan is not None
    assert (result.plan.domain, result.plan.service, result.plan.data) == ("media_player", service, data)


def test_music_assistant_is_optional_and_missing_service_never_writes():
    hass = _hass(search=False, play=False)
    result = _resolve(
        hass,
        MediaRequest("play", ("queen",), media_type="artist"),
        [_player()],
        satellite_area_id="living",
    )
    assert result is not None and result.plan is None
    assert "nicht verfügbar" in result.spoken
    hass.services.async_call.assert_not_awaited()


def test_missing_play_capability_never_even_searches():
    hass = _hass(search=True, play=False)
    result = _resolve(
        hass,
        MediaRequest("play", ("queen",), media_type="artist"),
        [_player()],
        satellite_area_id="living",
    )
    assert result is not None and result.plan is None
    hass.services.async_call.assert_not_awaited()


def test_exact_saved_radio_becomes_typed_plan_not_a_direct_url():
    hass = _hass({"radio": [{"name": "Radio Bob", "uri": "library://radio/42"}]})
    request = MediaRequest(
        "play", ("radio", "bob"), text="Spiel den gespeicherten Sender Radio Bob.",
        media_type="radio", library_only=True,
    )
    result = _resolve(
        hass, request, [_player()], satellite_area_id="living"
    )
    assert result is not None and result.plan is not None
    assert (result.plan.domain, result.plan.service, result.plan.entity_id) == (
        "music_assistant", "play_media", "media_player.wohnzimmer"
    )
    assert result.plan.data == {
        "media_id": "library://radio/42", "media_type": "radio", "enqueue": "replace"
    }
    call = hass.services.async_call.await_args
    assert call.args == (
        "music_assistant", "search",
        {
            "config_entry_id": "mass-entry", "name": "Radio Bob", "media_type": "radio",
            "limit": 10, "library_only": True,
        },
    )
    assert call.kwargs == {
        "blocking": True, "return_response": True, "context": None,
    }


def test_queue_mode_reaches_typed_plan_and_service_data():
    hass = _hass({"tracks": [{"name": "One", "uri": "library://track/1"}]})
    request = MediaRequest(
        "play", ("one",), text="Spiel als Nächstes den Titel One.",
        media_type="track", enqueue="next",
    )

    result = _resolve(hass, request, [_player()], satellite_area_id="living")

    assert result is not None and result.plan is not None and result.media_plan is not None
    assert result.media_plan.queue_mode == "next"
    assert result.plan.data["enqueue"] == "next"


def test_unknown_queue_mode_fails_before_search():
    hass = _hass({"tracks": [{"name": "One", "uri": "library://track/1"}]})

    result = _resolve(
        hass,
        MediaRequest("play", ("one",), media_type="track", enqueue="unsafe"),
        [_player()],
        satellite_area_id="living",
    )

    assert result is not None and result.plan is None
    assert "nichts gestartet" in result.spoken
    hass.services.async_call.assert_not_awaited()


def test_similar_radio_is_not_selected():
    hass = _hass({"radio": [{"name": "Radio Bobby", "uri": "library://radio/43"}]})
    request = MediaRequest("play", ("radio", "bob"), media_type="radio", library_only=True)
    result = _resolve(
        hass, request, [_player()], satellite_area_id="living"
    )
    assert result is not None and result.plan is None
    assert "nicht" in result.spoken


def test_artist_and_track_with_same_name_are_ambiguous():
    hass = _hass({
        "artists": [{"name": "Queen", "uri": "library://artist/1"}],
        "tracks": [{"name": "Queen", "uri": "library://track/2"}],
    })
    result = _resolve(
        hass, MediaRequest("play", ("queen",), text="Spiele Queen."), [_player()],
        satellite_area_id="living",
    )
    assert result is not None and result.plan is None and result.question
    assert "Künstler" in result.spoken and "Titel" in result.spoken
    assert result.pending is not None and result.pending.stage == "media_type"


def test_two_exact_tracks_never_start_one_arbitrarily():
    hass = _hass({
        "tracks": [
            {"name": "One", "uri": "library://track/1"},
            {"name": "One", "uri": "library://track/2"},
        ],
    })
    result = _resolve(
        hass, MediaRequest("play", ("one",), media_type="track"), [_player()],
        satellite_area_id="living",
    )
    assert result is not None and result.plan is None and result.question


def test_multiple_players_in_room_never_pick_first():
    hass = _hass()
    players = [_player(), _player("media_player.wohnzimmer_zwei")]
    result = _resolve(
        hass, MediaRequest("play", ("queen",), text="Spiele Queen im Wohnzimmer."), players,
        satellite_area_id=None,
    )
    assert result is not None and result.plan is None and result.question
    hass.services.async_call.assert_not_awaited()


def test_unavailable_player_and_untrusted_uri_fail_closed():
    unavailable = _resolve(
        _hass(), MediaRequest("play", ("queen",)), [_player(state="unavailable")],
        satellite_area_id="living",
    )
    assert unavailable is not None and unavailable.plan is None

    hass = _hass({"tracks": [{"name": "Queen", "uri": "https://evil.invalid/live"}]})
    unsafe = _resolve(
        hass, MediaRequest("play", ("queen",), media_type="track"), [_player()],
        satellite_area_id="living",
    )
    assert unsafe is not None and unsafe.plan is None
    assert "sichere Media-ID" in unsafe.spoken


def test_direct_url_is_never_searched_or_played():
    hass = _hass()
    result = _resolve(
        hass,
        MediaRequest("play", ("https", "evil", "invalid", "live"), text="Spiel https://evil.invalid/live"),
        [_player()],
        satellite_area_id="living",
    )
    assert result is not None and result.plan is None
    hass.services.async_call.assert_not_awaited()


def test_conversation_searches_then_executes_typed_plan(monkeypatch, tmp_path):
    from dataclasses import replace

    from _testhaus import HouseConversation, house_entities

    entities = [
        replace(item, integration="music_assistant", config_entry_id="mass-entry")
        if item.entity_id == "media_player.kuechenradio" else item
        for item in house_entities()
    ]
    house = HouseConversation(monkeypatch, entities=entities)
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = {
        "playlists": [{"name": "Entspannen", "uri": "library://playlist/7"}]
    }

    turn = house.say("Spiel meine Playlist Entspannen in der Küche.")

    assert [(domain, service) for domain, service, _data in turn.calls] == [
        ("music_assistant", "search"),
        ("music_assistant", "play_media"),
    ]
    assert turn.calls[1][2] == {
        "media_id": "library://playlist/7",
        "media_type": "playlist",
        "enqueue": "replace",
        "entity_id": "media_player.kuechenradio",
    }


def test_named_music_assistant_player_is_a_target_not_a_routing_blocker(monkeypatch):
    from _testhaus import HouseConversation

    house = HouseConversation(monkeypatch, entities=[_player()])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = {
        "tracks": [{"name": "One", "uri": "library://track/1"}]
    }

    turn = house.say("Spiele den Titel One auf Wohnzimmer Lautsprecher.")

    assert turn.calls[-1][:2] == ("music_assistant", "play_media")
    assert turn.calls[-1][2]["entity_id"] == "media_player.wohnzimmer"


def test_dialog_selects_artist_without_reusing_ambiguous_result(monkeypatch):
    from _testhaus import HouseConversation

    house = HouseConversation(monkeypatch, entities=[_player()])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = {
        "artists": [{"name": "Queen", "uri": "library://artist/1"}],
        "tracks": [{"name": "Queen", "uri": "library://track/2"}],
    }

    first = house.say("Spiele Queen im Wohnzimmer.")
    second = house.say("Den Künstler.")

    assert "Welche meinst du?" in first.speech
    assert [(domain, service) for domain, service, _data in first.calls] == [
        ("music_assistant", "search")
    ]
    assert [(domain, service) for domain, service, _data in second.calls] == [
        ("music_assistant", "search"), ("music_assistant", "play_media")
    ]
    assert second.calls[-1][2]["media_id"] == "library://artist/1"


def test_dialog_resolves_pronoun_plus_room_to_one_player(monkeypatch):
    from dataclasses import replace

    from _testhaus import HouseConversation

    living = replace(_player(), friendly_name="Wohnzimmer Box")
    kitchen = replace(
        _player("media_player.kueche", area="Küche"),
        friendly_name="Küchenbox",
        area_id="kitchen",
    )
    house = HouseConversation(monkeypatch, entities=[living, kitchen])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = {
        "tracks": [{"name": "One", "uri": "library://track/1"}]
    }

    first = house.say("Spiele den Titel One.")
    second = house.say("Spiel es im Wohnzimmer.")

    assert first.speech.startswith("Welches Gerät meinst du:") and len(first.calls) == 0
    assert second.calls[-1][:2] == ("music_assistant", "play_media")
    assert second.calls[-1][2]["entity_id"] == "media_player.wohnzimmer"


def test_media_selection_cannot_be_taken_over_by_another_user(monkeypatch):
    from _testhaus import HouseConversation

    house = HouseConversation(monkeypatch, entities=[_player()])
    house.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    house.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    house.entity.hass.services.async_call.return_value = {
        "artists": [{"name": "Queen", "uri": "library://artist/1"}],
        "tracks": [{"name": "Queen", "uri": "library://track/2"}],
    }
    assert "Welche meinst du?" in house.say("Spiele Queen im Wohnzimmer.").speech

    house.user = "anna"
    foreign = house.say("Den Künstler.")
    assert not any(service == "play_media" for _domain, service, _data in foreign.calls)

    house.user = "admin"
    owner = house.say("Den Künstler.")
    assert owner.calls[-1][:2] == ("music_assistant", "play_media")
