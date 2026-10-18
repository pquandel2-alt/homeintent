"""7.8: unit tests of the rules behind B1-B7 (sentences written for the
rules, not taken from the independent report)."""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation, house_entities
from homeintent.nlu.pragmatic_frames import strip_pragmatic_frames
from homeintent.nlu.short_commands import complete_value_unit, expand_short_command

E = house_entities()


@pytest.mark.parametrize("text,rest", [
    ("Wärst du so lieb und machst das Kellerlicht an", "mach das Kellerlicht an"),
    ("Würde es dir etwas ausmachen, das Bürolicht auszuschalten", "Schalte das Bürolicht aus"),
    ("Magst du den Esszimmer Rollladen hochfahren", "Fahr den Esszimmer Rollladen hoch"),
    ("Es wäre toll, wenn du die Stehlampe einschaltest", "Schalte die Stehlampe ein"),
    ("Mach die Kücheninsel an, danke schön", "Mach die Kücheninsel an"),
    ("Mach das Flurlicht aus, ich lese gleich", "Mach das Flurlicht aus"),
])
def test_frames_are_removed(text, rest):
    assert strip_pragmatic_frames(text, E).text == rest


@pytest.mark.parametrize("text,rest", [
    ("Dürfte ich dich bitten, den Badlüfter anzumachen?", "Mach den Badlüfter an"),
    ("Duerfte ich dich bitten, die Markise einzufahren?", "Fahr die Markise ein"),
    (
        "Könntest du freundlicherweise die Markise einfahren?",
        "Könntest du die Markise einfahren?",
    ),
])
def test_formal_politeness_is_a_structural_frame(text, rest):
    frames = strip_pragmatic_frames(text, E)
    assert frames.text == rest
    assert frames.politeness


@pytest.mark.parametrize("direct,polite", [
    ("Mach den Badlüfter an.", "Dürfte ich dich bitten, den Badlüfter anzumachen?"),
    ("Fahr die Markise ein.", "Könntest du freundlicherweise die Markise einfahren?"),
])
def test_formal_politeness_preserves_the_direct_request(monkeypatch, direct, polite):
    direct_turn = HouseConversation(monkeypatch).say(direct)
    polite_turn = HouseConversation(monkeypatch).say(polite)
    assert polite_turn.calls == direct_turn.calls
    assert polite_turn.targets == direct_turn.targets


@pytest.mark.parametrize("text", [
    "Dürfte ich dich bitten, den Badlüfter nicht anzumachen?",
    "Könntest du freundlicherweise die Markise nicht einfahren?",
])
def test_negation_inside_formal_politeness_never_executes(monkeypatch, text):
    turn = HouseConversation(monkeypatch).say(text)
    assert turn.calls == []


@pytest.mark.parametrize("text", [
    "Dürfte ich dich bitten zu erklären, ob der Badlüfter an ist?",
    "Könntest du freundlicherweise sagen, ob der Badlüfter an ist?",
])
def test_embedded_question_inside_formal_politeness_never_executes(monkeypatch, text):
    turn = HouseConversation(monkeypatch).say(text)
    assert turn.calls == []


@pytest.mark.parametrize("text", [
    "Um Mitternacht schalte alle Lichter aus",
    "Um Mitternacht mach die Lichter im Wohnzimmer aus",
])
def test_midnight_is_a_compositional_time_trigger(monkeypatch, text):
    turn = HouseConversation(monkeypatch).say(text)
    assert turn.calls == []
    assert "Automation erkannt" in turn.speech
    assert "00:00" in turn.speech


@pytest.mark.parametrize("text", [
    "Nicht um Mitternacht schalte alle Lichter aus",
    "Erkläre, was um Mitternacht mit den Lichtern passiert",
])
def test_midnight_does_not_turn_negation_or_questions_into_automations(monkeypatch, text):
    turn = HouseConversation(monkeypatch).say(text)
    assert turn.calls == []
    assert "Automation erkannt" not in turn.speech


def test_colloquial_support_verb_uses_the_structural_direction(monkeypatch):
    direct = HouseConversation(monkeypatch).say("Fahr die Rollläden im Wohnzimmer runter")
    colloquial = HouseConversation(monkeypatch).say("Hau mal die Rollläden im Wohnzimmer runter")
    assert colloquial.calls == direct.calls
    assert colloquial.targets == direct.targets


@pytest.mark.parametrize("text", [
    "Hau nicht die Rollläden im Wohnzimmer runter",
    "Hau auf den Rollladen im Wohnzimmer",
])
def test_colloquial_support_verb_without_safe_direction_never_executes(monkeypatch, text):
    assert HouseConversation(monkeypatch).say(text).calls == []


def test_ordering_adverbs_preserve_two_independently_grounded_targets(monkeypatch):
    turn = HouseConversation(monkeypatch).say(
        "Erst das Flurlicht an, dann das Küchenlicht"
    )
    assert turn.targets == {"light.flurlicht", "light.kuechenlicht"}


def test_ordering_adverb_does_not_erase_a_condition(monkeypatch):
    turn = HouseConversation(monkeypatch).say(
        "Erst wenn das Bürofenster offen ist, schalte das Bürolicht aus"
    )
    assert turn.calls == []


def test_ordering_never_uses_a_generic_ambiguous_target(monkeypatch):
    turn = HouseConversation(monkeypatch).say(
        "Erst das Licht an, dann das Küchenlicht"
    )
    assert turn.calls == []


@pytest.mark.parametrize("text", [
    "Wenn das Küchenfenster aufgeht, schalte den Badlüfter an",
    "Mach das Licht aus, das nicht",
    "Kannst du mir sagen, ob das Bürolicht an ist",
])
def test_conditions_negations_and_questions_are_no_frames(text):
    assert strip_pragmatic_frames(text, E).text == text


@pytest.mark.parametrize("text,expected", [
    ("Büro aus", "Schalte das Licht im Büro aus."),
    ("Markise rein", "Fahre Markise ein."),
    ("Saugroboter los", "Starte Saugroboter."),
    ("Heizung Kinderzimmer 20 Grad", "Stelle Heizung Kinderzimmer auf 20 Grad."),
    ("Wie warm ist es im Büro", None),
    ("Schlafzimmer Rollladen auf drei Viertel", None),
])
def test_short_commands(text, expected):
    assert expand_short_command(text, E) == expected


@pytest.mark.parametrize("text,expected", [
    ("Stell die Heizung im Büro auf 19", "Stell die Heizung im Büro auf 19 Grad"),
    ("Dimm die Stehlampe auf 25", "Dimm die Stehlampe auf 25 Prozent"),
    ("Fahre den Schlafzimmer Rollladen auf drei Viertel.", "Fahre den Schlafzimmer Rollladen auf drei Viertel."),
    ("Verschiebe den Auftrag auf 20 Uhr", "Verschiebe den Auftrag auf 20 Uhr"),
])
def test_unit_from_the_kind(text, expected):
    assert complete_value_unit(text, E) == expected


def _say(monkeypatch, *texts):
    house = HouseConversation(monkeypatch)
    return [house.say(text) for text in texts]


def test_rest_is_reported_not_a_capability(monkeypatch):
    (turn,) = _say(monkeypatch, "Mach das Garagenlicht an wupdiwup")
    assert turn.calls == [] and "Den Teil" in turn.speech and "lässt sich nur" not in turn.speech


def test_ellipsis_takes_over_the_setting(monkeypatch):
    _first, second = _say(monkeypatch, "Stell die Heizung im Kinderzimmer auf 21 Grad", "Im Büro ebenso")
    assert second.targets == {"climate.heizung_buero"}


def test_garage_means_the_door_with_confirmation(monkeypatch):
    (turn,) = _say(monkeypatch, "Mach die Garage zu")
    assert turn.calls == [] and "Garagentor" in turn.speech


def test_new_question_drops_the_open_confirmation(monkeypatch):
    _ask, question, yes = _say(monkeypatch, "Öffne das Garagentor", "Ist das Bürofenster offen?", "Ja")
    assert "verworfen" in question.speech and yes.calls == []


@pytest.mark.parametrize("reply", ["Ja, mach", "Los", "Gerne"])
def test_everyday_assent_confirms(monkeypatch, reply):
    _ask, answer = _say(monkeypatch, "Öffne das Garagentor", reply)
    assert answer.targets == {"cover.garagentor"}


def test_unknown_name_is_said_as_spoken(monkeypatch):
    (turn,) = _say(monkeypatch, "Mach das Leselicht an")
    assert "„Leselicht“" in turn.speech and "(" not in turn.speech
