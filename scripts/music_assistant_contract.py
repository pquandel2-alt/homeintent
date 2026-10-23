#!/usr/bin/env python3
"""Validate HomeIntent's MA calls against Home Assistant's real component.

No Music Assistant server is contacted.  The real HA integration constructs
its own action schemas and response schema; this probe validates the exact
payload shapes HomeIntent relies on against those schemas.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import patch


def validate_contract() -> dict[str, object]:
    """Return a summary after validating the installed HA MA component."""
    from homeassistant.const import __version__ as ha_version

    services = importlib.import_module("homeassistant.components.music_assistant.services")
    schemas = importlib.import_module("homeassistant.components.music_assistant.schemas")
    captured_actions: dict[str, object] = {}
    captured_entity_actions: dict[str, object] = {}

    class Registry:
        def async_register(self, domain, service, handler, schema=None, **kwargs):
            assert domain == "music_assistant"
            captured_actions[service] = schema

    def register_entity_action(_hass, domain, service, **kwargs):
        assert domain == "music_assistant"
        captured_entity_actions[service] = kwargs.get("schema")

    setup = getattr(services, "async_setup_services", None) or getattr(
        services, "register_actions"
    )
    with patch.object(
        services.service,
        "async_register_platform_entity_service",
        side_effect=register_entity_action,
    ):
        setup(SimpleNamespace(services=Registry()))

    assert services.DOMAIN == "music_assistant"
    assert services.SERVICE_SEARCH == "search"
    assert services.SERVICE_PLAY_MEDIA_ADVANCED == "play_media"

    search_schema = captured_actions["search"]
    for media_type in ("radio", ["artist", "album", "track", "playlist", "radio"]):
        validated = search_schema(
            {
                "config_entry_id": "01TESTENTRY",
                "name": "Radio Bob",
                "media_type": media_type,
                "limit": 10,
                "library_only": True,
            }
        )
        assert validated["config_entry_id"] == "01TESTENTRY"
        assert validated["name"] == "Radio Bob"
        assert validated["limit"] == 10
        assert validated["library_only"] is True

    schema_library = getattr(services, "probatio", None) or getattr(services, "vol")
    play_schema = schema_library.Schema(captured_entity_actions["play_media"])
    validated_play = play_schema(
        {
            "media_id": "library://radio/42",
            "media_type": "radio",
            "enqueue": "replace",
        }
    )
    assert list(validated_play["media_id"]) == ["library://radio/42"]
    assert str(validated_play["media_type"]) == "radio"
    assert str(validated_play["enqueue"]) == "replace"

    empty_item = {
        "media_type": "track",
        "uri": "library://track/1",
        "name": "One",
        "version": "",
        "image": None,
    }
    response = schemas.SEARCH_RESULT_SCHEMA(
        {
            "artists": [{**empty_item, "media_type": "artist", "uri": "library://artist/1"}],
            "albums": [{**empty_item, "media_type": "album", "uri": "library://album/1"}],
            "tracks": [empty_item],
            "playlists": [{**empty_item, "media_type": "playlist", "uri": "library://playlist/1"}],
            "radio": [{**empty_item, "media_type": "radio", "uri": "library://radio/1"}],
            "audiobooks": [],
            "podcasts": [],
        }
    )
    for key in ("artists", "albums", "tracks", "playlists", "radio"):
        assert response[key][0]["name"] == "One"
        assert response[key][0]["uri"].startswith("library://")

    return {
        "home_assistant": ha_version,
        "domain": services.DOMAIN,
        "search": services.SERVICE_SEARCH,
        "play_media": services.SERVICE_PLAY_MEDIA_ADVANCED,
        "real_component_schema": True,
        "music_assistant_server": False,
    }


def main() -> int:
    import json

    print(json.dumps(validate_contract(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
