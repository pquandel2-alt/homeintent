"""End-to-end composition of scope, property, degree and discourse."""

from __future__ import annotations

import _ha_stub
from _testhaus import HouseConversation

_ha_stub.install()


def test_room_light_degree_and_pronoun_compose_across_turns(monkeypatch):
    house = HouseConversation(monkeypatch)

    brighter = house.say("Mach bitte im Wohnzimmer das Licht etwas heller.")
    off = house.say("Mach es dort wieder aus.")

    expected = {
        "light.stehlampe",
        "light.wohnzimmer_deckenlicht",
        "light.wohnzimmer_led_streifen",
    }
    assert brighter.speech == "3 Lichter um 5 Prozent heller gestellt."
    assert set(brighter.calls[0][2]["entity_id"]) == expected
    assert brighter.calls[0][2]["brightness_step_pct"] == 5
    assert off.speech == "3 Lichter ausgeschaltet."
    assert set(off.calls[0][2]["entity_id"]) == expected
