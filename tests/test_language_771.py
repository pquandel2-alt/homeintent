"""7.7.1: unit tests of the rules behind A1-A6 (the invariants live in
test_safety_properties.py)."""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation, house_entities
from homeintent.nlu.coordination import expand_coordination
from homeintent.nlu.self_correction import CorrectionKind, analyse_self_correction
from homeintent.nlu.semantic_utterance import Modality, analyse_utterance

E = house_entities()


@pytest.mark.parametrize("text,expected", [
    ("Schalte das Radio aus, ich meine den Fernseher.", "Schalte den Fernseher aus."),
    ("Licht im Kinderzimmer an, halt, im Schlafzimmer.", "Licht im Schlafzimmer an."),
    ("Küchenlicht an, nein, das im Flur", "Licht im Flur an."),
    ("Den rechten Rollladen hoch, nee die linke, ja die linke", "Den linken Rollladen hoch."),
    ("Mach in zehn Minuten das Licht an, nein in fünf", "Mach in fünf Minuten das Licht an."),
    ("Stell die Heizung auf 21 Grad, ähm, 22", "Stell die Heizung auf 22 Grad."),
    ("Schließ die Haustür ab, ähm, das Gartentor meine ich", "Schließ das Gartentor ab."),
])
def test_replacement_replaces_only_named_fields(text, expected):
    correction = analyse_self_correction(text, E)
    assert correction.kind is CorrectionKind.REPLACED
    assert correction.text == expected


@pytest.mark.parametrize("text", [
    "Mach das Licht an, nein, doch nicht", "Mach die Stehlampe an, stopp.",
    "Mach das Licht im Bad an, lass mal", "Mach das Licht an – ach nee, doch nicht",
])
def test_abort_cancels(text):
    assert analyse_self_correction(text, E).kind is CorrectionKind.CANCELLED


@pytest.mark.parametrize("text", ["Mach das Licht im Bad an, oder im Flur", "Mach die Stehlampe an, nein, das Ding da"])
def test_unclear_correction_asks(text):
    assert analyse_self_correction(text, E).kind is CorrectionKind.AMBIGUOUS


@pytest.mark.parametrize("text", [
    "Mach halt das Licht an", "Mach doch mal das Licht an", "Nein, mach das Licht an",
    "Es ist zu hell im Wohnzimmer, oder?",  # tag question (7.8)
])
def test_particles_are_no_markers(text):
    assert analyse_self_correction(text, E).kind is CorrectionKind.NONE


@pytest.mark.parametrize("text,modality", [
    ("Hätte ich doch die Heizung im Büro ausgeschaltet.", Modality.IRREALIS),
    ("Ich hätte die Stehlampe heller machen sollen.", Modality.IRREALIS),
    ("Wäre das Licht doch aus gewesen.", Modality.IRREALIS),
    ("Ich überlege, ob ich den Mähroboter starten soll.", Modality.DELIBERATION),
    ("Vielleicht sollte ich das Licht ausmachen.", Modality.DELIBERATION),
])
def test_irrealis_and_deliberation(text, modality):
    assert analyse_utterance(text).modality is modality


@pytest.mark.parametrize("text", ["Ich hätte gerne das Licht an.", "Ich frage mich, ob das Küchenfenster offen ist."])
def test_wish_and_embedded_question_are_no_irrealis(text):
    assert analyse_utterance(text).modality not in {Modality.IRREALIS, Modality.DELIBERATION}


@pytest.mark.parametrize("text,expected", [
    ("Schalte Garten- und Terrassenlicht ein.", "Schalte Gartenlicht und Terrassenlicht ein."),
    ("Fahr Küche und Esszimmer Rollladen runter.",
     "Fahr den Rollladen in der Küche und den Rollladen im Esszimmer runter."),
    ("Mach im Wohnzimmer das Licht aus und fahr die Rollläden runter.",
     "Mach im Wohnzimmer das Licht aus und fahr die Rollläden im Wohnzimmer runter."),
    ("Schalte Flurlicht oben und Gäste-WC Licht ein.", "Schalte Flurlicht oben und Gäste-WC Licht ein."),
])
def test_coordination(text, expected):
    assert expand_coordination(text, E) == expected


def test_informed_confirmation_names_the_critical_effect(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Starte das Skript Gute Nacht.")
    assert turn.calls == [] and "verriegelt dabei Haustürschloss" in turn.speech


def test_maintenance_is_confirmed(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Den Fernseher lass bitte aus.")
    assert turn.calls == [] and "ich lasse den Fernseher aus" in turn.speech
