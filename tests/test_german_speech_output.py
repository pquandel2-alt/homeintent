"""F16: spoken answers never contain raw English HA states or enum names."""

from __future__ import annotations

import re

import pytest

from homeintent.entities import EntitySnapshot, spoken_state
from homeintent.extended_device_query import match_extended_device_query
from homeintent.nlu.verb_state_query import match_verb_state_query
from homeintent.proactive_messages import _ACKNOWLEDGEMENT_WORDS

ENGLISH = re.compile(r"\b(?:closed|open|paused|playing|idle|running|high|low|accepted|preference|unknown)\b")


@pytest.mark.parametrize(
    "state",
    ["closed", "open", "paused", "playing", "idle", "running", "locked", "unlocked",
     "docked", "cleaning", "heat", "armed_away", "disarmed", "unknown", "unavailable"],
)
def test_every_common_ha_state_has_a_german_word(state):
    spoken = spoken_state(state)
    assert spoken != state
    assert not ENGLISH.search(spoken)


def test_extended_device_status_is_spoken_in_german():
    radio = EntitySnapshot("select.modus", "Heizmodus", "select", "Komfort")
    answer = match_extended_device_query("Welchen Zustand hat der Heizmodus?", [radio])
    if answer is not None:
        assert not ENGLISH.search(answer.response_text)


def test_verb_state_answer_uses_german_state():
    cover = EntitySnapshot("cover.links", "Wohnzimmer Rollladen links", "cover", "closed")
    answer = match_verb_state_query("Fährt der Wohnzimmer Rollladen links?", [cover], None)
    assert answer is not None
    assert "closed" not in answer.response_text
    assert "closed" not in (answer.explanation_text or "")


def test_acknowledgement_codes_are_translated():
    for code in ("accepted", "accept", "later", "reject", "ignore", "snoozed"):
        assert not ENGLISH.search(_ACKNOWLEDGEMENT_WORDS[code])


def test_memory_listing_names_the_remembered_content():
    """F23: "Was hast du dir über mich gemerkt?" says what, not "1 preference"."""
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).parent))
    import _ha_stub

    _ha_stub.install()
    from homeintent.conversation import _describe_memory

    record = SimpleNamespace(
        kind=SimpleNamespace(value="preference"),
        content={"activity": "television", "entity_id": "light.stehlampe", "brightness_percent": 30},
    )
    text = _describe_memory(record, {"light.stehlampe": "Stehlampe"})
    assert text == "Vorliebe beim Fernsehen: Stehlampe auf 30 Prozent"
    assert not ENGLISH.search(text)
