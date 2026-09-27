"""Release 7.3.0 section 5: modality and negation as composable meaning.

Targets (names, genus + place) are combined with every modal frame; each
frame must produce the same operation for every target.  Sentences are
generated here, not taken from the live holdouts.
"""

from __future__ import annotations

import itertools

import pytest

from _testhaus import HouseConversation, house_entities, with_states

# (spoken noun phrase, entity id) - explicit names and genus x place.
_TARGETS = [
    ("die Stehlampe", "light.stehlampe"),
    ("das Licht im Gäste-WC", "light.gaeste_wc_licht"),
    ("die Kaffeemaschine", "switch.kaffeemaschine"),
    ("der Ventilator im Schlafzimmer", "fan.deckenventilator"),
    ("der Fernseher", "media_player.wohnzimmer_tv"),
]
_ALL_ON = dict(
    light__stehlampe="on", light__gaeste_wc_licht="on", switch__kaffeemaschine="on",
    fan__deckenventilator="on", media_player__wohnzimmer_tv="on",
)

_RELEASE = [
    "{X} muss nicht an sein.",
    "{X} braucht nicht mehr an sein.",
    "{X} kann aus.",
    "{X} darf jetzt aus.",
    "Ich brauche {x} nicht mehr.",
]
_MAINTAIN = [
    "Lass {x} an.",
    "Lass {x} bitte an.",
    "Kannst du {x} anlassen?",
]


def _accusative(phrase: str) -> str:
    article, _, rest = phrase.partition(" ")
    return {"der": "den"}.get(article, article) + " " + rest


def _fill(template: str, phrase: str) -> str:
    return template.format(X=phrase[:1].upper() + phrase[1:], x=_accusative(phrase))


@pytest.mark.parametrize("template,target", list(itertools.product(_RELEASE, _TARGETS)))
def test_release_switches_exactly_the_target_off(monkeypatch, template, target):
    phrase, entity_id = target
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_ALL_ON))
    turn = house.say(_fill(template, phrase))
    assert turn.targets == {entity_id}, (template, phrase, turn.speech)
    assert all("off" in service or service in {"close_cover"} for _, service, _ in turn.calls)


@pytest.mark.parametrize("template,target", list(itertools.product(_MAINTAIN, _TARGETS)))
def test_maintain_never_operates(monkeypatch, template, target):
    phrase, _ = target
    house = HouseConversation(monkeypatch, with_states(house_entities(), **_ALL_ON))
    turn = house.say(_fill(template, phrase))
    assert turn.calls == [], (template, phrase)


@pytest.mark.parametrize("shell", [
    "Könntest du vielleicht irgendwann mal {x} einschalten?",
    "Kannst du vielleicht {x} einschalten?",
    "Würdest du bitte eventuell {x} einschalten?",
])
@pytest.mark.parametrize("target", _TARGETS[:3])
def test_polite_hedge_is_a_normal_request(monkeypatch, shell, target):
    phrase, entity_id = target
    turn = HouseConversation(monkeypatch).say(_fill(shell, phrase))
    assert turn.targets == {entity_id}


@pytest.mark.parametrize("target", _TARGETS[:3])
def test_uncertainty_without_request_shell_never_operates(monkeypatch, target):
    phrase, _ = target
    turn = HouseConversation(monkeypatch).say(_fill("Mach vielleicht {x} an.", phrase))
    assert turn.calls == []


@pytest.mark.parametrize("shell", [
    "Ich frage mich, ob {q}.",
    "Weißt du, ob {q}?",
    "Ich wüsste gern, ob {q}.",
])
@pytest.mark.parametrize("question,answer", [
    ("das Küchenfenster offen ist", "Küchenfenster"),
    ("die Haustür geschlossen ist", "Haustür"),
])
def test_embedded_questions_are_questions(monkeypatch, shell, question, answer):
    turn = HouseConversation(monkeypatch).say(shell.format(q=question))
    assert turn.calls == []
    assert answer in turn.speech


@pytest.mark.parametrize("correction", [
    "Nicht das Küchenlicht, die Kücheninsel meine ich.",
    "Die Kücheninsel meine ich, nicht das Küchenlicht.",
    "Nicht das Küchenlicht, sondern die Kücheninsel.",
])
def test_correction_retargets_the_previous_action(monkeypatch, correction):
    house = HouseConversation(monkeypatch, with_states(
        house_entities(), light__kuechenlicht="off", light__kuecheninsel="off",
    ))
    house.say("Schalte das Küchenlicht ein.")
    turn = house.say(correction)
    assert "light.kuecheninsel" in turn.targets
