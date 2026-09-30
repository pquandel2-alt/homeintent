"""7.9: the canonical meaning carries the new monitoring fields.

``CanonicalEventNotification`` is word-order and wording independent: every
paraphrase of one request has one canonical meaning, now including the whole
set (W1), inactivity (W2), change and window (W3), repetition and escalation
(W5).
"""

from __future__ import annotations

import pytest

from _testhaus import house_entities
from homeintent.automation_followup import compose_with_followups
from homeintent.engine import NluEngine
from homeintent.nlu.semantic_state import SemanticState

ENTITIES = house_entities()
READERS = NluEngine()._composition_readers(ENTITIES, None, None)


def _canonical(text: str):
    outcome = compose_with_followups(text, ENTITIES, READERS)
    assert outcome is not None and outcome.canonical is not None, text
    return outcome.canonical


_GROUPS = {
    "whole_set": (
        "Sag mir Bescheid, wenn alle Fenster zu sind.",
        "Wenn kein Fenster mehr offen ist, melde dich.",
        "Benachrichtige mich, sobald das letzte Fenster zugeht.",
    ),
    "inactivity": (
        "Melde dich, wenn sich im Flur 12 Stunden nichts bewegt.",
        "Wenn im Flur zwölf Stunden lang keine Bewegung erkannt wird, sag mir Bescheid.",
    ),
    "change": (
        "Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.",
        "Wenn die Kellertemperatur binnen 60 Minuten um drei Grad sinkt, warne mich.",
    ),
    "repeat": (
        "Wenn das Garagentor offen ist, erinnere mich alle 10 Minuten, bis es zu ist.",
        "Melde dich, wenn das Garagentor offen ist, und erinnere mich alle zehn Minuten, bis es zu ist.",
    ),
    "escalation": (
        "Melde dich, wenn die Haustür offen ist, und wenn sie nach 15 Minuten immer noch offen ist, sag Anna Bescheid.",
        "Wenn die Haustür offen ist, sag mir Bescheid, und wenn sie nach 15 Minuten noch offen ist, benachrichtige Anna.",
    ),
}


@pytest.mark.parametrize("group", list(_GROUPS))
def test_paraphrases_share_one_canonical_meaning(group):
    assert len({_canonical(text) for text in _GROUPS[group]}) == 1, group


def test_the_new_fields():
    assert _canonical(_GROUPS["whole_set"][0]).event.quantifier_all
    inactive = _canonical(_GROUPS["inactivity"][0]).event
    assert inactive.absent_state is SemanticState.ON and inactive.for_seconds == 12 * 3600
    change = _canonical(_GROUPS["change"][0]).event
    assert change.change_delta == -3.0 and change.change_window_seconds == 3600
    repeat = _canonical(_GROUPS["repeat"][0])
    assert repeat.repeat_interval_seconds == 600 and repeat.max_repeats == 12
    escalation = _canonical(_GROUPS["escalation"][0])
    assert escalation.escalation_seconds == 900 and escalation.escalation_recipient is not None


def test_different_meanings_stay_different():
    assert _canonical(_GROUPS["whole_set"][0]) != _canonical("Sag mir Bescheid, wenn ein Fenster zu ist.")
