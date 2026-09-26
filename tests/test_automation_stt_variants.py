"""Speech-to-text forms and spelling variants (spec §50, §51, §79, §80)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_language_helpers import meaning, question  # noqa: E402

COVER = "num(cover.buero_rollladen).cover_position==50 => notify(me)"


@pytest.mark.parametrize("sentence", (
    "benachrichtige mich wenn die roll lade im büro fünfzig prozent erreicht",
    "sag mir bescheid wenn der rollladen im büro fünf zig prozent erreicht",
    "schick mir eine push nachricht wenn der büro rollladen fünfzig prozent erreicht hat",
    "Benachrichtige mich wenn die Rollade im Buero 50 Prozent erreicht",
    "Benachrichtige mich wenn der Rolladen im Büro 50 Prozent erreicht",
    "Benachrichtige mich wenn das Rollo im Büro 50 Prozent erreicht",
    "Benachrichtige mich wenn die Jalousie im Büro 50 Prozent erreicht",
    "wenn der rollo im büro halb unten ist sag mir bescheid",
    "wenn die rollade im büro halb ist sag bescheid",
    "wenn der rollladen im büro bei fünfzig ist sag mir bescheid",
    "also äh wenn der rollo im büro irgendwann bei fünfzig ist kannst du mir dann kurz bescheid sagen",
))
def test_stt_and_spelling_variants(sentence):
    assert meaning(sentence) == COVER


def test_a_bare_number_is_a_percentage_only_for_covers():
    assert question("Sag mir Bescheid, wenn das Wohnzimmerlicht bei fünfzig ist.")
