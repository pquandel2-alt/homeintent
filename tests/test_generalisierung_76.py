"""Phase 9 (7.6.0): generalisation on unseen paraphrases.

``eval/generalisierung_76.json`` is an own paraphrase corpus written for
7.6.0 (verb classes, needs, situation questions, discourse, politeness,
multi-commands).  Every case runs through the real conversation entity;
the rule tests below pin the individual language changes and their
safety boundaries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.nlu.language_frontend import bind_coordinated_deixis  # noqa: E402
from homeintent.nlu.normalize import is_polite_request, normalize  # noqa: E402
from homeintent.nlu.place_model import level_is_position  # noqa: E402
from homeintent.undo import is_undo_request  # noqa: E402

CORPUS = json.loads(
    (Path(__file__).parent / "eval" / "generalisierung_76.json").read_text(encoding="utf-8")
)
READ_ONLY = {("homeassistant", "update_entity")}


def _written(turn) -> set[str]:
    targets: set[str] = set()
    for domain, service, data in turn.calls:
        if (domain, service) in READ_ONLY:
            continue
        entity_ids = data.get("entity_id")
        if isinstance(entity_ids, str):
            targets.add(entity_ids)
        elif entity_ids:
            targets.update(entity_ids)
    return targets


@pytest.mark.parametrize(
    "case", CORPUS, ids=[f"{case['class']}-{index}" for index, case in enumerate(CORPUS)]
)
def test_unseen_paraphrase(case, monkeypatch):
    house = HouseConversation(monkeypatch, options=case.get("options"))
    turn = None
    for text in case["turns"]:
        turn = house.say(text)
    assert turn is not None
    expect = case["expect"]
    written = _written(turn)
    if "targets" in expect:
        assert written == set(expect["targets"]), turn.speech
    if expect.get("no_write"):
        assert not written, turn.speech
    if "any_target" in expect:
        assert any(target.startswith(expect["any_target"]) for target in written), turn.speech
    if "speech" in expect:
        assert any(word in turn.speech for word in expect["speech"]), turn.speech


# ------------------------------------------------------------ politeness
@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("Wärst du so lieb und machst die Stehlampe an?", "mach die stehlampe an"),
        ("Es wäre nett, wenn du das Küchenlicht ausmachst.", "mach das küchenlicht aus"),
        ("Kannst du das Küchenlicht an?", "das küchenlicht an"),
    ],
)
def test_politeness_shells_become_requests(text, normalized):
    assert is_polite_request(text)
    assert normalized in normalize(text).casefold()


@pytest.mark.parametrize(
    "text",
    [
        "Kannst du mir sagen, ob das Küchenlicht an ist?",
        "Ist das Küchenlicht an?",
        "Es wäre schlimm, wenn das Licht ausgeht.",
        "Wärst du auch der Meinung?",
    ],
)
def test_questions_and_real_conditionals_are_no_politeness_shell(text):
    assert not is_polite_request(text)


def test_negated_polite_shell_never_executes(monkeypatch):
    turn = HouseConversation(monkeypatch).say(
        "Es wäre nett, wenn du das Küchenlicht nicht ausmachst."
    )
    assert not _written(turn)


def test_state_question_with_can_you_is_answered_not_executed(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Kannst du mir sagen, ob das Küchenlicht an ist?")
    assert not _written(turn)


# ------------------------------------------------------------ "dort" in multi-commands
def test_dort_binds_to_the_place_named_before_und():
    bound = bind_coordinated_deixis(
        "Mach das Licht im Bad an und den Lüfter dort auch.", house_entities()
    )
    assert bound == "Mach das Licht im Bad an und den Lüfter im Bad auch."


@pytest.mark.parametrize(
    "text",
    [
        "Mach das Licht an und den Lüfter dort auch.",  # no place before "und"
        "Mach im Bad und in der Küche das Licht an und dort den Lüfter.",  # two places
        "Ist dort ein Fenster offen?",  # no conjunction
    ],
)
def test_dort_is_never_guessed(text):
    assert bind_coordinated_deixis(text, house_entities()) == text


def test_dort_and_explicit_place_behave_the_same(monkeypatch):
    with_dort = HouseConversation(monkeypatch).say("Mach das Licht im Bad an und den Lüfter dort auch.")
    explicit = HouseConversation(monkeypatch).say("Mach das Licht im Bad an und den Lüfter im Bad auch.")
    assert with_dort.speech == explicit.speech
    assert _written(with_dort) == _written(explicit)


# ------------------------------------------------------------ withdrawal after execution
def test_forget_it_after_an_action_says_it_already_ran(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Mach das Küchenlicht an.")
    turn = house.say("Vergiss es.")
    assert "schon ausgeführt" in turn.speech and "rückgängig" in turn.speech
    assert not _written(turn)  # nothing is undone without being asked
    undone = house.say("Rückgängig.")
    assert _written(undone) == {"light.kuechenlicht"}


def test_forget_it_after_a_question_has_nothing_to_cancel(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Wie warm ist es im Büro?")
    turn = house.say("Vergiss es.")
    assert "nichts offen" in turn.speech


def test_bare_back_is_not_an_undo():
    assert is_undo_request("Rückgängig.")
    assert is_undo_request("Mach das rückgängig!")
    assert not is_undo_request("Zurück.")  # media: previous track


# ------------------------------------------------------------ position vs. floor
def test_up_and_down_after_shading_is_a_position():
    text = "Sind alle Rollläden unten?"
    start = text.index("unten")
    assert level_is_position(text, start, start + len("unten"))


def test_up_and_down_with_preposition_stays_a_floor():
    text = "Sind die Rollläden nach unten gefahren oder ist oben jemand?"
    start = text.index("oben")
    assert not level_is_position(text, start, start + len("oben"))
