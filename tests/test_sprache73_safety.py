"""Release 7.3.0 safety regressions S1-S7 against the stub test house.

Each test drives the real conversation entity (``tests/_testhaus.py``) with
sentences of its own; the live acceptance corpora in ``sim/`` are holdouts
and are deliberately not reused here.
"""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation, house_entities, with_states


# --- S1: "lass X an/zu/offen" keeps a state and never operates -------------

@pytest.mark.parametrize("text", [
    "Lass das Licht in der Küche an.",
    "Lass die Stehlampe bitte an.",
    "Lasst das Bürolicht an.",
    "Kannst du die Schreibtischlampe anlassen?",
    "Lass das Küchenfenster offen.",
    "Lass den Rollladen im Schlafzimmer oben.",
    "Lass die Markise zu.",
    "Lass das Radio in der Küche laufen.",
])
def test_s1_maintain_never_calls_a_service(monkeypatch, text):
    house = HouseConversation(monkeypatch, with_states(
        house_entities(), light__kuechenlicht="on", light__stehlampe="on",
    ))
    turn = house.say(text)
    assert turn.calls == []
    assert "ändere nichts" in turn.speech


def test_s1_herunterlassen_remains_an_operation(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Lass den Rollladen im Schlafzimmer runter.")
    assert turn.targets == {"cover.schlafzimmer_rollladen"}


# --- S2: "alles" in a room = every everyday switchable device, previewed ---

def _living_room_on():
    return with_states(
        house_entities(),
        light__stehlampe="on", light__wohnzimmer_deckenlicht="on",
        light__wohnzimmer_led_streifen="on", media_player__wohnzimmer_tv="on",
        switch__fernseher_steckdose="on",
    )


@pytest.mark.parametrize("text", [
    "Mach im Wohnzimmer alles aus.",
    "Schalte alles im Wohnzimmer aus.",
    "Mach bitte alle Geräte im Wohnzimmer aus.",
])
def test_s2_everything_in_room_is_previewed_and_keeps_heating(monkeypatch, text):
    house = HouseConversation(monkeypatch, _living_room_on())
    preview = house.say(text)
    assert preview.calls == []
    for name in ("Stehlampe", "Wohnzimmer Deckenlicht", "LED-Streifen Wohnzimmer", "Wohnzimmer TV"):
        assert name in preview.speech
    assert "Unverändert bleiben: Heizung Wohnzimmer" in preview.speech
    confirmed = house.say("Ja.")
    assert confirmed.targets == {
        "light.stehlampe", "light.wohnzimmer_deckenlicht",
        "light.wohnzimmer_led_streifen", "media_player.wohnzimmer_tv",
        "switch.fernseher_steckdose",
    }
    assert not any(target.startswith(("climate.", "lock.", "cover.")) for target in confirmed.targets)


def test_s2_everything_never_includes_locks_or_garage(monkeypatch):
    house = HouseConversation(monkeypatch, with_states(
        house_entities(), light__flurlicht="on",
    ))
    preview = house.say("Mach im Flur alles aus.")
    assert "Haustürschloss" not in preview.speech.split("Unverändert")[0]
    confirmed = house.say("Ja.")
    assert "lock.haustuerschloss" not in confirmed.targets
    assert "alarm_control_panel.alarmanlage" not in confirmed.targets


def test_s2_plural_genus_never_reaches_other_genera(monkeypatch):
    """"Rollos" are shutters: never the garage door or the awning."""
    house = HouseConversation(monkeypatch)
    preview = house.say("Rollos runter.")
    assert "Garagentor" not in preview.speech and "Markise" not in preview.speech
    confirmed = house.say("Ja.")
    assert "cover.garagentor" not in confirmed.targets
    assert "cover.markise" not in confirmed.targets
    assert "cover.buero_raffstore" in confirmed.targets


# --- S3: never drop a clause silently ----------------------------------------

@pytest.mark.parametrize("text,targets", [
    (
        "Mach das Bürolicht an, stell die Heizung im Büro auf 21 Grad und fahr den Raffstore hoch.",
        {"light.buerolicht", "climate.heizung_buero", "cover.buero_raffstore"},
    ),
    (
        "Mach die Kaffeemaschine an, das Küchenlicht aus und fahr den Küchenrollladen runter.",
        {"switch.kaffeemaschine", "light.kuechenlicht", "cover.kuechenrollladen"},
    ),
    (
        "Schalte die Stehlampe ein, den Deckenventilator aus und die Heizung im Bad auf 22 Grad.",
        {"light.stehlampe", "fan.deckenventilator", "climate.heizung_badezimmer"},
    ),
])
def test_s3_every_clause_is_executed(monkeypatch, text, targets):
    house = HouseConversation(monkeypatch)
    assert house.say(text).targets == targets


def test_s3_unclear_clause_blocks_everything_and_is_named(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Mach das Bürolicht an und flausche den Teppich.")
    assert turn.calls == []
    assert "flausche den Teppich" in turn.speech
