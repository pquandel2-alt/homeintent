"""7.9.3 A5: no placeholders in spoken previews.

Every preview producer that fills a message at run time (several sensors,
"nicht erreichbar", lights on when leaving, the battery and availability
reports) names a real example from the current house, introduced with
"zum Beispiel".  Over all preview producers no spoken answer contains "<"
or ">": generated monitoring requests (kinds × phrasings) plus every
sentence of the automation corpora in ``tests/eval/automation_v72``.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

_HEADS = ("Gib mir Bescheid, {c}.", "Sag mir Bescheid, {c}.", "Schick mir eine Nachricht, {c}.",
          "Benachrichtige mich, {c}.")
_CONDITIONS = (
    "sobald irgendeine Batterie unter 25 Prozent fällt", "wenn eine Batterie unter 20 % liegt",
    "wenn ein Gerät nicht mehr erreichbar ist", "wenn irgendein Gerät länger als 5 Minuten nicht erreichbar ist",
    "wenn ich gehe und noch Licht an ist", "wenn wir gehen und noch irgendwo Licht brennt",
    "wenn eine Temperatur über 25 Grad steigt", "wenn die Luftfeuchtigkeit irgendwo über 60 Prozent liegt",
    "wenn ein Fenster offen ist", "wenn die Waschmaschine fertig ist",
)
_REPORTS = (
    "Sag mir jeden Sonntag um 10 Uhr, welche Batterien unter 30 % sind.",
    "Sag mir werktags um 7 Uhr, welche Geräte nicht erreichbar sind.",
)


def _speeches(monkeypatch, tmp_path, text):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    first = house.say(text).speech
    return first


@pytest.mark.parametrize("head,condition", list(itertools.product(_HEADS, _CONDITIONS)))
def test_generated_monitoring_previews(monkeypatch, tmp_path, head, condition):
    speech = _speeches(monkeypatch, tmp_path, head.format(c=condition))
    assert "<" not in speech and ">" not in speech, speech


@pytest.mark.parametrize("text", _REPORTS)
def test_report_previews_name_the_current_state(monkeypatch, tmp_path, text):
    speech = _speeches(monkeypatch, tmp_path, text)
    assert "<" not in speech and ">" not in speech, speech
    assert "zum Beispiel „" in speech, speech


def test_battery_example_is_a_real_sensor(monkeypatch, tmp_path):
    speech = _speeches(monkeypatch, tmp_path, "Gib mir Bescheid, sobald irgendeine Batterie unter 25 Prozent fällt.")
    assert "zum Beispiel „Batterie Rauchmelder oben: 9 %“" in speech, speech


def test_unavailable_example_is_a_real_device(monkeypatch, tmp_path):
    speech = _speeches(monkeypatch, tmp_path, "Melde dich, wenn ein Gerät nicht mehr erreichbar ist.")
    assert "zum Beispiel „Alarmanlage ist seit 10 Minuten nicht erreichbar.“" in speech, speech


def test_leaving_example_names_a_real_room(monkeypatch, tmp_path):
    speech = _speeches(monkeypatch, tmp_path, "Wenn ich gehe und noch Licht an ist, sag mir Bescheid.")
    assert "zum Beispiel „Du hast das Haus verlassen; im Badezimmer ist noch Licht an.“" in speech, speech


def test_the_runtime_template_is_unchanged(monkeypatch, tmp_path):
    """Only the spoken example changed; the push still names the device at
    run time (template from entity ids)."""
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    house.say("Gib mir Bescheid, sobald irgendeine Batterie unter 25 Prozent fällt.")
    house.say("Nein.")
    [automation] = house.automations()
    assert automation["actions"][0]["data"]["message"] == "{{ trigger.to_state.name }}: {{ trigger.to_state.state }} %"
    assert "<" not in automation["description"] and ">" not in automation["description"]


def _corpus() -> list[str]:
    sentences = []
    for path in sorted(Path(__file__).parent.joinpath("eval", "automation_v72").glob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "::" in line and not line.lstrip().startswith("#"):
                sentences.append(line.split("::", 1)[1].strip())
    return sentences


def test_no_spoken_answer_of_the_automation_corpora_has_placeholders(monkeypatch, tmp_path):
    sentences = _corpus()
    assert len(sentences) > 200
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    bad = []
    for index, text in enumerate(sentences):
        house.conversation_id = f"c{index}"
        for turn in text.split(">>"):  # ">>" separates the turns of a dialog
            speech = house.say(turn.strip()).speech
            if "<" in speech or ">" in speech:
                bad.append((turn, speech))
    assert bad == []
