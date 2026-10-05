"""7.9.1 A6: HomeIntent's own question is answered.

Nachtest 7.9.0 B6: "Melde dich, wenn die Temperatur im Büro um 2 Grad
fällt." -> "In welchem Zeitraum?" -> "Innerhalb von 10 Minuten." gave "Das
habe ich nicht verstanden." Every question for exactly one part (period,
time, counting start, device, recipient, pause time) now opens a typed
dialog (``DialogTaskKind.MONITOR_PART``); the next turn is read only as that
part and completes the original request, which runs the normal path again
(preview, "Ja"). "Abbrechen" ends it; a new complete request ends it
without side effect; another user cannot answer it.

Answer forms are generated: prepositions x amounts x units.
"""

from __future__ import annotations

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation

RATE = "Melde dich, wenn die Temperatur im Büro um 2 Grad fällt."


def _house(monkeypatch, tmp_path) -> HouseConversation:
    from homeintent.monitor_goal import MonitorGoalStore

    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    house.entity._runtime_data.monitor_goals = MonitorGoalStore(tmp_path / "goals.json")
    return house


_AMOUNTS = {"10": 600, "zehn": 600, "eine": 3600, "einer": 3600, "2": 7200}
_UNITS = {"10": "Minuten", "zehn": "Minuten", "eine": "Stunde", "einer": "Stunde", "2": "Stunden"}
_PREFIXES = ("Innerhalb von", "In", "Binnen", "")


@pytest.mark.parametrize("prefix", _PREFIXES)
@pytest.mark.parametrize("amount", list(_AMOUNTS))
def test_the_period_answer_completes_the_rate_monitor(monkeypatch, tmp_path, prefix, amount):
    house = _house(monkeypatch, tmp_path)
    assert house.say(RATE).speech.startswith("In welchem Zeitraum?")
    if prefix in {"Innerhalb von", ""} and amount == "einer":
        amount = "eine"
    answer = f"{prefix} {amount} {_UNITS[amount]}.".strip()
    preview = house.say(answer)
    assert "Soll ich das so einrichten?" in preview.speech, (answer, preview.speech)
    assert house.say("Ja.").speech.startswith("Eingerichtet")
    import asyncio

    [record] = asyncio.run(house.entity._runtime_data.monitor_goals.async_load())
    assert record.goal.trigger.window_seconds == _AMOUNTS[amount]
    assert record.goal.trigger.delta == 2


def test_a_short_non_answer_asks_again(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(RATE)
    again = house.say("Keine Ahnung.")
    assert again.speech.startswith("Das habe ich nicht als Zeitraum verstanden. In welchem Zeitraum?")
    assert "Soll ich das so einrichten?" in house.say("Eine Stunde.").speech


def test_too_short_a_period_is_not_taken(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(RATE)
    assert "nicht als Zeitraum verstanden" in house.say("30 Sekunden.").speech


def test_cancel_ends_the_question(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(RATE)
    assert "verworfen" in house.say("Abbrechen.").speech
    assert "Soll ich das so einrichten?" not in house.say("Eine Stunde.").speech


def test_a_new_request_ends_it_without_side_effect(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(RATE)
    turn = house.say("Schalte das Licht im Flur ein.")
    assert turn.targets == {"light.flurlicht"}
    assert "Soll ich das so einrichten?" not in house.say("Eine Stunde.").speech


def test_another_user_does_not_answer_it(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(RATE)
    house.user = "anna"
    assert "Soll ich das so einrichten?" not in house.say("Eine Stunde.").speech
    house.user = "admin"
    assert "Soll ich das so einrichten?" in house.say("Eine Stunde.").speech


@pytest.mark.parametrize(("answer", "hour"), [
    ("Bis 20 Uhr.", 20), ("20 Uhr", 20), ("Um 18 Uhr.", 18), ("bis 9 Uhr abends", 21), ("Bis 19:30", 19),
])
def test_the_time_answer_completes_the_appliance_check(monkeypatch, tmp_path, answer, hour):
    house = _house(monkeypatch, tmp_path)
    question = house.say("Melde dich, wenn die Waschmaschine heute nicht gelaufen ist.")
    assert question.speech.startswith("Bis wann soll ich prüfen")
    preview = house.say(answer)
    assert "Soll ich das so einrichten?" in preview.speech, preview.speech
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert trigger["at"].startswith(f"{hour:02d}:")


@pytest.mark.parametrize("answer", ["im Flur", "Im Flur.", "den Bewegungsmelder im Flur"])
def test_the_device_answer_replaces_the_missing_detector(monkeypatch, tmp_path, answer):
    house = _house(monkeypatch, tmp_path)
    question = house.say("Melde dich, wenn sich im Keller fünf Stunden nichts bewegt.")
    assert question.speech.endswith("Welchen Melder soll ich stattdessen nehmen?")
    preview = house.say(answer)
    assert "Soll ich das so einrichten?" in preview.speech, preview.speech
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["entity_id"] == "binary_sensor.bewegung_flur"


@pytest.mark.parametrize("answer", ["Anna", "an Anna", "Anna."])
def test_the_recipient_answer_replaces_the_unknown_person(monkeypatch, tmp_path, answer):
    house = _house(monkeypatch, tmp_path)
    question = house.say(
        "Melde dich, wenn die Haustür offen ist, und wenn sie nach 15 Minuten immer noch offen ist, "
        "sag Lena Bescheid."
    )
    assert question.speech.endswith("Wen soll ich benachrichtigen?")
    preview = house.say(answer)
    assert "„Handy Anna“" in preview.speech, preview.speech


@pytest.mark.parametrize("answer", ["bis morgen um 7", "Bis morgen um 7 Uhr.", "morgen 7 Uhr"])
def test_the_pause_time_answer(monkeypatch, tmp_path, answer):
    house = _house(monkeypatch, tmp_path)
    house.say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.")
    house.say("Ja.")
    assert house.say("Pausiere die Garagen-Meldung.").speech.startswith("Bis wann?")
    turn = house.say(answer)
    assert turn.speech.startswith("Pausiert bis morgen um 07:00 Uhr"), turn.speech


@pytest.mark.parametrize(("part", "answer", "phrase"), [
    ("WINDOW", "in 10 Minuten", "innerhalb von 10 minuten"),
    ("WINDOW", "eine halbe Stunde", "innerhalb von 30 Minuten"),
    ("WINDOW", "zwei Tage", "innerhalb von 2 tage"),
    ("WINDOW", "Licht an", None),
    ("UNTIL", "bis 9", "bis 9 Uhr"),
    ("UNTIL", "gestern", None),
    ("PERIOD", "diese Woche", "diese Woche"),
    ("PERIOD", "morgen", None),
    ("DEVICE", "wenn das Licht an ist dann melde dich bitte sofort", None),
])
def test_the_part_reader(part, answer, phrase):
    from homeintent.missing_part import MissingPart, read_part_answer

    assert read_part_answer(MissingPart[part], answer) == phrase
