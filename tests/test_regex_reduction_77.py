"""7.7 B6: shared word-level readings that replaced sentence patterns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homeintent.nlu.dialog_meta import MetaQuestion, meta_questions
from homeintent.nlu.locative import (
    drop_locative_prepositions,
    has_locative_cue,
    names_place_after_locative,
    strip_locative_prepositions,
)
from homeintent.routine_intent import interpret_routine_feedback
from homeintent.situation import RoutineFeedback

ROOT = Path(__file__).parent.parent


@pytest.mark.parametrize(("text", "cue", "followed"), [
    ("Licht im Bad an", True, True),
    ("Wie warm ist es in der Küche", True, True),
    ("in dem Flur", True, True),
    ("beim Eingang", True, True),
    ("Ist es warm im", True, False),
    ("Schalte das Licht ein", False, False),
    ("Wie viel Grad sind es innen", False, False),
    ("in den Keller", False, False),
])
def test_locative_cue(text, cue, followed):
    assert has_locative_cue(text) is cue
    assert has_locative_cue(text, followed=True) is followed


def test_locative_strip_and_drop():
    assert strip_locative_prepositions("im  Wohnzimmer") == "Wohnzimmer"
    assert strip_locative_prepositions("Licht in der Küche") == "Licht Küche"
    only = frozenset({"im", "in der", "in dem"})
    assert drop_locative_prepositions("Licht im Bad am Fenster", spoken=only) == "Licht Bad am Fenster"
    assert drop_locative_prepositions("Licht im", spoken=only) == "Licht im"


def test_place_named_after_locative():
    assert names_place_after_locative("heizung im wohnzimmer", ["wohnzimmer"])
    assert not names_place_after_locative("heizung im wohnzimmerschrank", ["wohnzimmer"])
    assert not names_place_after_locative("wohnzimmer heizung", ["wohnzimmer"])


@pytest.mark.parametrize(("text", "expected"), [
    ("Was hast du verstanden?", {MetaQuestion.UNDERSTOOD}),
    ("Warum fragst du?", {MetaQuestion.WHY_ASKING}),
    ("Abbrechen.", {MetaQuestion.CANCEL}),
    ("Vergiss es", {MetaQuestion.CANCEL}),
    ("Lass das", {MetaQuestion.CANCEL}),
    ("Lass dasselbe", set()),
    ("Das Wohnzimmer", set()),
])
def test_dialog_meta_questions(text, expected):
    assert meta_questions(text) == expected


class _Document:
    def __init__(self, text: str) -> None:
        self.normalized_text = text


@pytest.mark.parametrize(("text", "expected"), [
    ("Das war hilfreich", RoutineFeedback.HELPFUL),
    ("Diese Meldung ist nicht hilfreich", RoutineFeedback.UNNECESSARY),
    ("Dieser Hinweis war falsch", RoutineFeedback.WRONG),
    ("Das künftig ignorieren bitte", RoutineFeedback.IGNORE),
    ("Frag das später", RoutineFeedback.LATER),
    ("Frag das war hilfreich", None),
    ("Hilfreich", None),
    ("Das war super", None),
])
def test_routine_feedback_reference_and_predicate(text, expected):
    result = interpret_routine_feedback(_Document(text))  # type: ignore[arg-type]
    assert (result.feedback if result else None) is expected


def test_sentence_patterns_below_the_77_bound():
    counts = json.loads((ROOT / "docs/regex-klassifikation.json").read_text(encoding="utf-8"))["anzahl"]
    assert counts["SEMANTIC_SENTENCE_PATTERN"] < 180
