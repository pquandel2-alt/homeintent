"""Verb position, perfect and copular constructions (spec §8, §58, §59)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_language_helpers import meaning  # noqa: E402


@pytest.mark.parametrize(("clause", "expected"), (
    ("die Rolllade im Büro 50 Prozent erreicht", "num(cover.buero_rollladen).cover_position==50"),
    ("die Rolllade im Büro 50 Prozent erreicht hat", "num(cover.buero_rollladen).cover_position==50"),
    ("die Rolllade im Büro bei 50 Prozent ist", "num(cover.buero_rollladen).cover_position==50"),
    ("die Rolllade im Büro auf 50 Prozent steht", "num(cover.buero_rollladen).cover_position==50"),
    ("die Rolllade im Büro bei 50 Prozent steht", "num(cover.buero_rollladen).cover_position==50"),
    ("die Temperatur im Wohnzimmer 25 Grad erreicht hat", "num(sensor.wohnzimmer_temperatur).state==25"),
    ("die Temperatur im Wohnzimmer bei 22 Grad ist", "num(sensor.wohnzimmer_temperatur).state==22"),
    ("der Handy Akku 20 Prozent erreicht hat", "num(sensor.handy_akku).state==20"),
    ("der Akkustand vom Handy bei 20 Prozent liegt", "num(sensor.handy_akku).state==20"),
    ("die Außentemperatur 25 Grad beträgt", "num(sensor.aussentemperatur).state==25"),
    ("das Küchenfenster aufgeht", "state(binary_sensor.kuechenfenster)=open"),
    ("das Küchenfenster geöffnet wird", "state(binary_sensor.kuechenfenster)=open"),
    ("das Küchenfenster offen ist", "state(binary_sensor.kuechenfenster)=open"),
    ("das Küchenfenster auf ist", "state(binary_sensor.kuechenfenster)=open"),
    ("das Küchenfenster aufgegangen ist", "state(binary_sensor.kuechenfenster)=open"),
    ("jemand das Küchenfenster öffnet", "state(binary_sensor.kuechenfenster)=open"),
    ("das Küchenfenster zugeht", "state(binary_sensor.kuechenfenster)=closed"),
    ("das Küchenfenster zugemacht wird", "state(binary_sensor.kuechenfenster)=closed"),
    ("das Küchenfenster seit zehn Minuten offen ist", "state(binary_sensor.kuechenfenster)=open/for=600"),
    ("das Küchenfenster länger als zehn Minuten offen ist", "state(binary_sensor.kuechenfenster)=open/for=600"),
    ("das Küchenfenster zehn Minuten lang offen ist", "state(binary_sensor.kuechenfenster)=open/for=600"),
    ("es dunkel wird", "sun=sunset"),
    ("die Sonne untergeht", "sun=sunset"),
    ("Julia nach Hause kommt", "arrive(person.julia)"),
    ("Julia das Haus verlässt", "leave(person.julia)"),
))
def test_clause_forms(clause, expected):
    assert meaning(f"Benachrichtige mich, wenn {clause}.") == f"{expected} => notify(me)"
    assert meaning(f"Wenn {clause}, sag mir Bescheid.") == f"{expected} => notify(me)"


@pytest.mark.parametrize(("sentence", "expected"), (
    ("Sag mir bei Sonnenuntergang Bescheid.", "sun=sunset"),
    ("Benachrichtige mich bei Sonnenaufgang.", "sun=sunrise"),
    ("Benachrichtige mich 30 Minuten vor Sonnenuntergang.", "sun=sunset-30"),
    ("Schick mir jeden Tag um 7 Uhr eine Nachricht.", "time=07:00"),
))
def test_temporal_phrases_without_a_connector(sentence, expected):
    assert meaning(sentence) == f"{expected} => notify(me)"
