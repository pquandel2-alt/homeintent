"""7.7 B8: development benchmark corpus, STT compound repair, repetition guard."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from _testhaus import house_entities
from homeintent.engine import NluEngine
from homeintent.nlu.stt_repair import join_split_compounds

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dev_benchmark  # noqa: E402

HOUSE = house_entities()
REQUIRED_CATEGORIES = {
    "befehl", "frage", "beduerfnis", "automation", "benachrichtigung", "kalender_timer",
    "mehrturn", "ellipse", "umgangssprache", "stt", "negation", "vergangenheit",
    "hypothetisch", "hoeflichkeit", "mehrfach", "mehrdeutig", "adversarial", "selbstkorrektur",
}


def test_corpus_has_at_least_500_cases_in_every_category_with_a_heldout_part():
    cases = dev_benchmark.load()
    assert len(cases) >= 500
    assert {case.category for case in cases} >= REQUIRED_CATEGORIES
    heldout = [case for case in cases if case.heldout]
    assert 0.15 * len(cases) <= len(heldout) <= 0.3 * len(cases)


@pytest.mark.parametrize(("spoken", "repaired"), [
    ("schalte die außen beleuchtung an", "schalte die außenbeleuchtung an"),
    ("schalte das flur licht oben ein", "schalte das flurlicht oben ein"),
    ("schalte das kinder zimmer licht ein", "schalte das kinderzimmerlicht ein"),
    ("wie warm ist es im wohn zimmer", "wie warm ist es im wohnzimmer"),
    ("mach die nacht tisch lampe links an", "mach die nachttischlampe links an"),
    ("fahr den ess zimmer rollladen runter", "fahr den esszimmer rollladen runter"),
])
def test_stt_split_compounds_join_into_registry_words(spoken, repaired):
    assert join_split_compounds(spoken, HOUSE) == repaired


@pytest.mark.parametrize("text", [
    "Schalte das Licht im Wohnzimmer an",
    "Wohnzimmer TV aus",
    "Gute Nacht",
    "Mach das Licht an",
    "Schalte die Nachttischlampe links an",
    "mach das bad licht an",  # "badlicht" is no registry word: never guessed
])
def test_stt_repair_leaves_other_sentences_alone(text):
    assert join_split_compounds(text, HOUSE) == text


@pytest.mark.parametrize("text", [
    "Schalte das Küchenlicht 1000 Mal ein",
    "Schalte das Küchenlicht drei Mal ein",
])
def test_a_repetition_count_is_never_dropped_from_a_command(text):
    payload = NluEngine().understand(text, HOUSE).payload
    assert getattr(payload, "plan", None) is None
    assert not getattr(payload, "commands", ())


@pytest.mark.parametrize("text", ["Schalte das Küchenlicht noch mal ein", "Mach mal das Küchenlicht an"])
def test_mal_as_particle_still_executes(text):
    payload = NluEngine().understand(text, HOUSE).payload
    assert payload is not None and payload.plan is not None
