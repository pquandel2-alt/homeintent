"""F1: wildcards must never start or end inside a word, whatever hassil does.

hassil 3.12 (Home Assistant 2026.9) matched "Minute" out of "Minuten" and let
the command wildcard begin with the stray "n". These tests pin the decomposed
command for every unit with the command both after and before the offset, so
the suite fails under any hassil version that reintroduces the split.
"""

from __future__ import annotations

import pytest
from hassil import Intents, RangeSlotList, RangeType, TextSlotList, WildcardSlotList

from homeintent.hassil_compat import is_word_aligned, recognize_aligned


@pytest.mark.parametrize(
    ("text", "command", "seconds"),
    [
        ("in 30 Minuten erinnere mich an die Waschmaschine", "erinnere mich an die Waschmaschine", 1800),
        ("In 10 Sekunden schick mir eine Testnachricht", "schick mir eine Testnachricht", 10),
        ("in 2 Stunden schalte das Küchenlicht ein", "schalte das Küchenlicht ein", 7200),
        ("In 1 Minute schalte das Küchenlicht ein", "schalte das Küchenlicht ein", 60),
        ("Schalte in 15 Sekunden das Küchenlicht ein", "Schalte das Küchenlicht ein", 15),
        ("schalte das Küchenlicht ein in 5 Minuten", "schalte das Küchenlicht ein", 300),
    ],
)
def test_relative_time_decompose_keeps_whole_words(engine, text, command, seconds):
    decomposed = engine._relative_time_command_parser.decompose(text)

    assert decomposed is not None
    assert decomposed[0].casefold() == command.casefold()
    assert decomposed[1] == seconds


def _unit_intents() -> tuple[Intents, dict]:
    intents = Intents.from_dict(
        {
            "language": "de",
            "intents": {"X": {"data": [{"sentences": ["in {amount} {unit} {cmd}"]}]}},
        }
    )
    lists = {
        "amount": RangeSlotList(name="amount", start=1, stop=59, type=RangeType.NUMBER),
        # Shorter form first: the order hassil 3.12 effectively prefers.
        "unit": TextSlotList.from_tuples([("Minute", "m"), ("Minuten", "m")], name="unit"),
        "cmd": WildcardSlotList(name="cmd"),
    }
    return intents, lists


def test_recognize_aligned_rejects_word_splitting_wildcards():
    intents, lists = _unit_intents()

    result = recognize_aligned("in 30 Minuten tu das", intents, slot_lists=lists)

    assert result is not None
    assert result.entities["cmd"].value == "tu das"
    assert is_word_aligned("in 30 Minuten tu das", result)


def test_word_alignment_ignores_stripped_punctuation():
    intents, lists = _unit_intents()

    result = recognize_aligned("in 3 Minuten tu das, bitte.", intents, slot_lists=lists)

    assert result is not None
    assert str(result.entities["cmd"].value).strip(" .") in {"tu das, bitte", "tu das bitte"}

