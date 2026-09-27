"""Release 7.3.0 section 4: discourse binding over the conversation context.

Each case is an opener (establishing a referent) combined with generated
follow-up forms; the follow-up must operate on exactly the referent the
context provides.  Sentences are built here, not taken from ``sim/``.
"""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation, house_entities, with_states

_LIGHTS_ON = dict(
    light__stehlampe="on", light__buerolicht="on", light__nachttischlampe_links="off",
    light__nachttischlampe_rechts="off", light__schlafzimmerlicht="off",
)


@pytest.mark.parametrize("opener,target", [
    ("Schalte die Stehlampe ein.", "light.stehlampe"),
    ("Mach das Bürolicht an.", "light.buerolicht"),
])
@pytest.mark.parametrize("followup,step_sign", [
    ("Etwas heller bitte.", 1),
    ("Heller.", 1),
    ("Ein bisschen dunkler.", -1),
    ("Mach es dunkler.", -1),
])
def test_elliptical_degree_binds_to_last_target(monkeypatch, opener, target, followup, step_sign):
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_LIGHTS_ON))
    house.say(opener)
    turn = house.say(followup)
    assert turn.targets == {target}, followup
    assert all((data.get("brightness_step_pct", 0) > 0) == (step_sign > 0) for _, _, data in turn.calls)


def test_repeat_step_uses_previous_direction(monkeypatch):
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_LIGHTS_ON))
    house.say("Mach die Stehlampe dunkler.")
    turn = house.say("Noch mehr.")
    assert turn.targets == {"light.stehlampe"}
    assert turn.calls[0][2]["brightness_step_pct"] < 0


@pytest.mark.parametrize("followup", ["Die andere auch.", "Und die andere auch.", "Die andere bitte auch."])
def test_the_other_one_is_the_named_partner(monkeypatch, followup):
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_LIGHTS_ON))
    house.say("Mach die Nachttischlampe links an.")
    assert house.say(followup).targets == {"light.nachttischlampe_rechts"}


@pytest.mark.parametrize("question,expected", [
    ("Welche Lichter sind im Schlafzimmer?", {
        "light.nachttischlampe_links", "light.nachttischlampe_rechts", "light.schlafzimmerlicht",
    }),
])
@pytest.mark.parametrize("followup", ["Mach alle aus.", "Schalte sie aus.", "Mach die alle aus."])
def test_result_set_of_a_question_is_a_referent(monkeypatch, question, expected, followup):
    house = HouseConversation(monkeypatch)
    house.say(question)
    turn = house.say(followup)
    assert turn.targets == expected, followup


@pytest.mark.parametrize("followup", ["Mach's da wärmer.", "Dort bitte wärmer.", "Mach es dort etwas wärmer."])
def test_deictic_place_binds_to_last_place(monkeypatch, followup):
    house = HouseConversation(monkeypatch)
    house.say("Wie warm ist es im Büro?")
    assert house.say(followup).targets == {"climate.heizung_buero"}


@pytest.mark.parametrize("followup,expected", [
    ("Und im Bad?", {"climate.heizung_badezimmer"}),
    ("Und im Schlafzimmer?", {"climate.heizung_schlafzimmer"}),
])
def test_und_place_repeats_the_last_action_elsewhere(monkeypatch, followup, expected):
    house = HouseConversation(monkeypatch)
    house.say("Mach es im Büro wärmer.")
    assert house.say(followup).targets == expected


def test_und_target_auch_repeats_the_last_operation(monkeypatch):
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_LIGHTS_ON))
    house.say("Schalte die Kaffeemaschine ein.")
    turn = house.say("Und das Küchenlicht auch.")
    assert turn.targets == {"light.kuechenlicht"}
    assert turn.calls[0][1] == "turn_on"


def test_discourse_never_crosses_conversations(monkeypatch):
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_LIGHTS_ON))
    house.say("Schalte die Stehlampe ein.")
    house.conversation_id = "another-conversation"
    assert house.say("Etwas heller bitte.").calls == []


@pytest.mark.parametrize("followup,expected", [
    ("Mach es im Kinderzimmer etwas kühler.", {"climate.heizung_kinderzimmer"}),
    ("Mach es im Schlafzimmer wärmer.", {"climate.heizung_schlafzimmer"}),
    ("Mach das im Bad aus.", {"climate.heizung_badezimmer"}),
])
def test_explicit_new_place_outranks_the_remembered_referent(monkeypatch, followup, expected):
    house = HouseConversation(monkeypatch, area="buro")
    house.say("Mach es etwas kühler.")
    assert house.say(followup).targets == expected


def test_bare_other_repeats_the_last_operation(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Mach die Nachttischlampe rechts an.")
    assert house.say("Die andere.").targets == {"light.nachttischlampe_links"}
