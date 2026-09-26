"""Same-turn self repair (spec §26, §49) - only the corrected meaning survives."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_language_helpers import meaning  # noqa: E402
from homeintent.nlu.automation_lexicon import resolve_repairs  # noqa: E402


@pytest.mark.parametrize(("sentence", "expected"), (
    ("Benachrichtige mich wenn die Rolllade im Büro 60, äh 50 Prozent erreicht.",
     "num(cover.buero_rollladen).cover_position==50 => notify(me)"),
    ("Benachrichtige mich wenn die Rolllade im Büro 60... nein 50 Prozent erreicht.",
     "num(cover.buero_rollladen).cover_position==50 => notify(me)"),
    ("Wenn das Küchenfenster, nein das Bürofenster geöffnet wird, sag mir Bescheid.",
     "state(binary_sensor.buero_fenster)=open => notify(me)"),
    ("Wenn das Küchenfenster... äh, ich meine das Bürofenster geöffnet wird, sag mir Bescheid.",
     "state(binary_sensor.buero_fenster)=open => notify(me)"),
    ("Schick Julia, nein mir eine Nachricht wenn das Bürofenster aufgeht.",
     "state(binary_sensor.buero_fenster)=open => notify(me)"),
    ("Sag mir Bescheid, wenn der Rollladen in der Küche, nein im Wohnzimmer 50 Prozent erreicht.",
     "num(cover.wohnzimmer_rollladen).cover_position==50 => notify(me)"),
    ("Benachrichtige mich, wenn die Außentemperatur unter 5, nein unter 3 Grad fällt.",
     "num(sensor.aussentemperatur).state<3 => notify(me)"),
))
def test_only_the_corrected_value_reaches_the_model(sentence, expected):
    assert meaning(sentence) == expected


def test_repairs_do_not_touch_ordinary_sentences():
    for text in ("Mach das Licht an, nein aus", "Nein, keine Automation.", "Die linke Rolllade im Büro"):
        assert resolve_repairs(text).repaired is False
