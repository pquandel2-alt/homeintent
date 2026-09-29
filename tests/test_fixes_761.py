"""7.6.1: findings of the independent re-test of 7.6.0 (A2-A8).

Each block belongs to one finding; every sentence runs through the real
conversation entity on the stub test house (``tests/_testhaus.py``).
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.nlu.entity_clarification import which_question  # noqa: E402
from homeintent.nlu.language_frontend import analyse_language  # noqa: E402


def _writes(turn) -> list[tuple[str, str, dict]]:
    return [call for call in turn.calls if call[1] not in {"get_items", "get_events"}]


# --------------------------------------------------------------- A2 greetings
@pytest.mark.parametrize("sentence", [
    "Aktiviere Guten Morgen.",
    "Aktiviere die Szene Guten Morgen.",
    "Aktiviere bitte die Szene Guten Morgen.",
])
def test_a2_a_greeting_name_is_activatable(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert _writes(turn) == [("scene", "turn_on", {"entity_id": "scene.guten_morgen"})]
    assert "Guten Morgen aktiviert" in turn.speech


def test_a2_words_inside_a_spoken_name_are_no_time():
    entities = house_entities()
    assert analyse_language("Aktiviere Guten Morgen.", entities).temporal == ()
    # Outside a name "morgen" keeps its meaning.
    assert analyse_language("Schalte morgen das Küchenlicht an.", entities).temporal


def test_a2_time_outside_the_name_still_schedules(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Schalte morgen früh das Küchenlicht an.")
    assert _writes(turn) == []


@pytest.mark.parametrize("sentence", ["Guten Morgen.", "Guten Morgen!", "guten morgen"])
def test_a2_a_bare_greeting_starts_nothing(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    assert _writes(house.say(sentence)) == []


def test_a2_the_question_names_the_genus(monkeypatch):
    base = house_entities()
    scene = next(entity for entity in base if entity.entity_id == "scene.guten_morgen")
    entities = [entity for entity in base if entity.entity_id != "scene.guten_morgen"] + [
        replace(scene, entity_id="scene.guten_morgen_bad", friendly_name="Guten Morgen Bad"),
        replace(scene, entity_id="scene.guten_morgen_kueche", friendly_name="Guten Morgen Küche"),
    ]
    house = HouseConversation(monkeypatch, entities=entities)
    turn = house.say("Aktiviere die Szene Guten Morgen.")
    assert _writes(turn) == []
    assert "Szenen" in turn.speech and "Gerät" not in turn.speech
    scenes = tuple(entity for entity in base if entity.domain == "scene")
    assert which_question(scenes).startswith("Welche Szene meinst du: ")
    mixed = scenes[:1] + tuple(entity for entity in base if entity.domain == "light")[:1]
    assert which_question(mixed).startswith("Welches Gerät meinst du: ")


# ------------------------------------------------------ A3 relative amounts
@pytest.mark.parametrize(("sentence", "temperature"), [
    ("Mach die Heizung im Bad zwei Grad wärmer.", 24.0),
    ("Mach die Heizung im Bad 2 Grad wärmer.", 24.0),
    ("Mach die Heizung im Bad um zwei Grad wärmer.", 24.0),
    ("Mach die Heizung im Bad drei Grad kälter.", 19.0),
    ("Dreh die Heizung im Bad um drei Grad hoch.", 25.0),
    ("Heizung im Bad zwei Grad höher.", 24.0),
    ("Mach die Heizung im Bad 1,5 Grad kälter.", 20.5),
    ("Mach die Heizung im Bad ein halbes Grad wärmer.", 22.5),
    ("Mach die Heizung im Bad wärmer.", 23.0),
])
def test_a3_amount_sets_the_climate_step(monkeypatch, sentence, temperature):
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert _writes(turn) == [(
        "climate", "set_temperature",
        {"temperature": temperature, "entity_id": "climate.heizung_badezimmer"},
    )]


def test_a3_device_limits_still_apply(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Mach die Heizung im Bad zwanzig Grad wärmer.")
    ((_, _, data),) = _writes(turn)
    assert data["temperature"] == 30.0
    assert "Grenzwert" in turn.speech


@pytest.mark.parametrize(("sentence", "step"), [
    ("Mach die Stehlampe 20 Prozent heller.", 20),
    ("Mach die Stehlampe zwanzig Prozent heller.", 20),
    ("Mach die Stehlampe um dreißig Prozent dunkler.", -30),
    ("Mach die Stehlampe etwas dunkler.", -5),
])
def test_a3_amount_sets_the_brightness_step(monkeypatch, sentence, step):
    house = HouseConversation(monkeypatch)
    ((domain, _, data),) = _writes(house.say(sentence))
    assert domain in {"light", "homeassistant"} and data["brightness_step_pct"] == step


def test_a3_amount_changes_the_volume_relative_to_now(monkeypatch):
    house = HouseConversation(monkeypatch)
    radio = next(e for e in house.entities if e.entity_id == "media_player.kuechenradio")
    now = float(radio.attributes["volume_level"])
    ((_, service, data),) = _writes(house.say("Mach das Radio in der Küche zehn Prozent lauter."))
    assert service == "volume_set" and data["volume_level"] == round(now + 0.1, 2)
    ((_, service, data),) = _writes(house.say("Mach das Radio in der Küche fünf Prozent leiser."))
    assert service == "volume_set" and data["volume_level"] == round(now - 0.05, 2)
    ((_, service, _),) = _writes(house.say("Mach das Radio in der Küche lauter."))
    assert service == "volume_up"


def test_a3_amount_moves_a_cover_relative_to_now(monkeypatch):
    house = HouseConversation(monkeypatch)
    cover = next(e for e in house.entities if e.entity_id == "cover.wohnzimmer_rollladen_links")
    now = int(cover.attributes["current_position"])
    turn = house.say("Fahr den linken Rollladen im Wohnzimmer um 20 Prozent runter.")
    ((_, _, data),) = _writes(turn)
    assert data["position"] == max(0, now - 20)
    # Asked back for the cover, the step still applies to the chosen one.
    house = HouseConversation(monkeypatch)
    assert _writes(house.say("Fahr den Rollladen im Wohnzimmer um 20 Prozent runter.")) == []
    ((_, _, data),) = _writes(house.say("Den linken."))
    assert data["position"] == max(0, now - 20)


def test_a3_an_absolute_value_after_auf_is_no_step(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Fahr den linken Rollladen im Wohnzimmer auf 20 Prozent runter.")
    ((_, _, data),) = _writes(turn)
    assert data["position"] == 20


@pytest.mark.parametrize("sentence", [
    "Dreh die Heizung im Bad um drei Grad hoch.",
    "Fahr den linken Rollladen im Wohnzimmer um 20 Prozent runter.",
])
def test_a3_um_with_a_unit_is_no_clock_time(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert "Uhr" not in turn.speech and "Automation" not in turn.speech


# ------------------------------------------------------- A4 "oben"/"unten"
UPPER_SHUTTERS = ("Badezimmer Rollladen", "Kinderzimmer Rollladen", "Schlafzimmer Rollladen")


@pytest.mark.parametrize("sentence", [
    "Wie viele Rollläden gibt es oben?",
    "Welche Rollläden gibt es oben?",
])
def test_a4_counting_and_listing_keep_the_floor(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    answer = house.say(sentence).speech
    assert all(name in answer for name in UPPER_SHUTTERS)
    assert "Wohnzimmer" not in answer and "Küche" not in answer


def test_a4_state_question_reads_the_position(monkeypatch):
    house = HouseConversation(monkeypatch)
    answer = house.say("Sind alle Rollläden unten?").speech
    assert "geschlossen" in answer


def test_a4_floor_and_position_in_one_question(monkeypatch):
    house = HouseConversation(monkeypatch)
    answer = house.say("Sind oben alle Rollläden unten?").speech
    assert answer.startswith("Nein") and "geschlossen" in answer
    assert all(name in answer for name in UPPER_SHUTTERS)
    assert "Wohnzimmer" not in answer


def test_a4_command_with_place_keeps_the_floor(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Fahr oben alle Rollläden runter.")
    ((domain, service, data),) = _writes(turn)
    assert (domain, service) == ("cover", "close_cover")
    assert sorted(data["entity_id"]) == [
        "cover.badezimmer_rollladen", "cover.kinderzimmer_rollladen", "cover.schlafzimmer_rollladen",
    ]


def test_a4_direction_after_nach_stays_a_direction(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Fahr den Rollladen im Büro nach unten.")
    assert _writes(turn) == [("cover", "close_cover", {"entity_id": "cover.buero_raffstore"})]


def _house_without(monkeypatch, *hidden_ids, user="admin", options=None):
    """Test house where ``hidden_ids`` exist in HA but are not exposed."""
    from homeassistant.core import State

    entities = house_entities()
    house = HouseConversation(
        monkeypatch,
        entities=[entity for entity in entities if entity.entity_id not in hidden_ids],
        user=user,
        options=options,
    )
    for entity in entities:
        house.entity.hass.states._states[entity.entity_id] = State(
            entity.entity_id, entity.state, {"friendly_name": entity.friendly_name}
        )
    return house


# ------------------------------------------------- A5 non-exposed devices
def test_a5_a_hidden_vacuum_is_named_as_not_released(monkeypatch):
    house = _house_without(monkeypatch, "vacuum.saugroboter")
    turn = house.say("Starte den Saugroboter.")
    assert _writes(turn) == []
    assert turn.speech.startswith("Saugroboter ist für HomeIntent nicht freigegeben.")
    assert "Einstellungen" in turn.speech  # admins learn where to change it


def test_a5_non_admins_get_no_settings_hint(monkeypatch):
    house = _house_without(monkeypatch, "vacuum.saugroboter", user="anna")
    turn = house.say("Starte den Saugroboter.")
    assert turn.speech == "Saugroboter ist für HomeIntent nicht freigegeben."


def test_a5_side_entities_are_no_substitute(monkeypatch):
    house = _house_without(monkeypatch, "switch.kaffeemaschine")
    turn = house.say("Schalte die Kaffeemaschine ein.")
    assert _writes(turn) == []
    assert turn.speech.startswith("Kaffeemaschine ist für HomeIntent nicht freigegeben.")
    assert "entkalken" not in turn.speech


def test_a5_an_exposed_side_entity_by_its_own_name_still_works(monkeypatch):
    house = _house_without(monkeypatch, "switch.kaffeemaschine")
    turn = house.say("Drücke Kaffeemaschine entkalken.")
    assert "nicht freigegeben" not in turn.speech


def test_a5_a_genus_word_with_exposed_members_is_no_hidden_name():
    from homeintent.nlu.target_resolution import hidden_name_mentions

    entities = house_entities()
    assert hidden_name_mentions(
        "Schalte das Licht im Flur ein.", entities, [("light", "Licht")]
    ) == ()
    assert hidden_name_mentions(
        "Starte den Saugroboter.",
        [entity for entity in entities if entity.domain != "vacuum"],
        [("vacuum", "Saugroboter")],
    ) == ("Saugroboter",)


# ------------------------------------------------------ A6 contractions
def test_a6_contractions_are_shared_morphology():
    from homeintent.nlu.normalize import expand_clitics, normalize

    assert expand_clitics("Welche Routine nutzt du fürs Schlafen?") == (
        "Welche Routine nutzt du für das Schlafen?"
    )
    assert normalize("Schick mir das aufs Handy") == "Schick mir das auf das Handy"
    assert "in das" in normalize("Ich gehe ins Bett")
    # The canonical dative forms stay as the lexicon reads them.
    assert normalize("Beim Lesen zum Schlafen") == "Beim Lesen zum Schlafen"


def test_a6_welche_routine_fuers_schlafen(monkeypatch):
    house = HouseConversation(monkeypatch)
    plain = house.say("Welche Routine nutzt du für das Schlafen?").speech
    house = HouseConversation(monkeypatch)
    fused = house.say("Welche Routine nutzt du fürs Schlafen?").speech
    assert fused == plain
    assert "nicht verstanden" not in fused


# ------------------------------------------------ A7 preparing the night
def test_a7_preparing_the_night_offers_the_routines(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Mach alles für die Nacht fertig.")
    assert _writes(turn) == []
    expected = HouseConversation(monkeypatch).say("Ich gehe schlafen.").speech
    assert turn.speech == expected
    assert "erledigen" not in turn.speech


def test_a7_without_routines_the_definition_dialog_has_good_grammar(monkeypatch):
    entities = [entity for entity in house_entities() if entity.domain not in {"script", "scene"}]
    house = HouseConversation(monkeypatch, entities=entities)
    turn = house.say("Mach alles für die Nacht fertig.")
    assert _writes(turn) == []
    assert "beim Schlafengehen" in turn.speech
    assert "bei schlafengehen" not in turn.speech


# ------------------------------------------------ A8 softening particles
@pytest.mark.parametrize("sentence", [
    "Könntest du vielleicht irgendwann mal die Markise einfahren?",
    "Könntest du mal vielleicht die Markise einfahren?",
    "Kannst du eventuell eben mal kurz die Markise einfahren?",
    "Würdest du bitte vielleicht die Markise einfahren?",
])
def test_a8_softening_particles_keep_the_request(monkeypatch, sentence):
    from homeintent.nlu.semantic_utterance import SpeechAct, analyse_utterance

    assert analyse_utterance(sentence).speech_act is SpeechAct.COMMAND
    reference = HouseConversation(monkeypatch).say("Fahr die Markise ein.")
    house = HouseConversation(monkeypatch)
    assert _writes(house.say(sentence)) == _writes(reference)
    assert _writes(reference)


def test_a8_embedded_questions_stay_questions(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Kannst du mir vielleicht sagen, ob die Markise eingefahren ist?")
    assert _writes(turn) == []


def test_a4_one_rule_for_the_level_words():
    from homeintent.nlu.place_model import level_for_keyword, level_word_role

    assert level_for_keyword("upper", (-1, 0, 1)) == 1
    assert level_for_keyword("ground", (-1, 0, 1)) == 0
    assert level_for_keyword("basement", (-1, 0, 1)) == -1
    assert level_for_keyword("upper", (0,)) is None
    words = "sind oben alle rolllaeden unten".split()
    assert level_word_role(words, 1) == "floor"
    assert level_word_role(words, 4) == "position"
    assert level_word_role("wie viele rolllaeden gibt es oben".split(), 5) == "floor"
    assert level_word_role("fahr nach oben".split(), 2) == "direction"
    assert level_word_role("fahr oben alle rolllaeden runter".split(), 1) == "floor"
