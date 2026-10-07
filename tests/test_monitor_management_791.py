"""7.9.1 A5: managing a monitor never reaches the calendar.

Nachtest 7.9.0 B5: "Lösch die Überwachung vom Garagentor." answered "Ich
finde keinen eindeutig änderbaren passenden Termin." The W8 reader knew only
compounds ("Garagen-Überwachung") and initial full imperatives. Now the
same object forms hold for every management verb (prepositional objects,
short imperatives, modal frames), and a management verb with a monitor or
automation noun as object never goes to calendar, lists or reminders.

Generated: verbs x object nouns x prepositions x devices. The calendar,
list and reminder handlers are replaced by recorders that must stay empty.
"""

from __future__ import annotations

import itertools

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation

_VERBS = {
    "Lösche {obj}.": "delete",
    "Lösch {obj}.": "delete",
    "Entferne {obj}.": "delete",
    "Kannst du {obj} löschen?": "delete",
    "Bitte lösche {obj}.": "delete",
    "Stopp {obj}.": "stop",
    "Schalte {obj} aus.": "stop",
    "Kannst du {obj} ausschalten?": "stop",
    "Pausiere {obj} bis morgen um 7 Uhr.": "pause",
}
_OBJECTS = ("die Überwachung", "die Meldung", "die Benachrichtigung", "die Warnung")
_PREPOSITIONS = ("vom Garagentor", "für das Garagentor", "fürs Garagentor", "zum Garagentor", "des Garagentors")


def _house(monkeypatch, tmp_path) -> tuple[HouseConversation, list]:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    touched: list = []
    productivity = house.entity._productivity

    async def record_async(*args, **kwargs):
        touched.append(args)
        raise AssertionError("calendar/list handler reached")

    def record(*args, **kwargs):
        touched.append(args)
        raise AssertionError("calendar draft reached")

    monkeypatch.setattr(productivity, "async_handle_calendar_management", record_async)
    monkeypatch.setattr(productivity, "async_handle_request", record_async)
    monkeypatch.setattr(productivity, "handle_calendar_draft", record)
    return house, touched


def _with_garage_monitor(house: HouseConversation) -> None:
    house.say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.")
    assert house.say("Ja.").speech == "Automation wurde erstellt."


@pytest.mark.parametrize(("template", "obj", "prep"), list(itertools.product(_VERBS, _OBJECTS, _PREPOSITIONS)))
def test_the_monitor_is_found_never_the_calendar(monkeypatch, tmp_path, template, obj, prep):
    house, touched = _house(monkeypatch, tmp_path)
    _with_garage_monitor(house)
    turn = house.say(template.format(obj=f"{obj} {prep}"))
    assert touched == []
    assert "Termin" not in turn.speech and "Kalender" not in turn.speech, turn.speech
    operation = _VERBS[template]
    if operation == "delete":
        assert turn.speech.startswith("Soll ich die Überwachung"), turn.speech
        house.say("Ja.")
        assert house.automations() == []
    elif operation == "stop":
        assert turn.speech.startswith("Ausgeschaltet"), turn.speech
    else:
        assert turn.speech.startswith("Pausiert bis morgen"), turn.speech


@pytest.mark.parametrize("template", list(_VERBS))
@pytest.mark.parametrize("obj", _OBJECTS + ("die Automation",))
def test_without_a_match_the_answer_is_honest(monkeypatch, tmp_path, template, obj):
    house, touched = _house(monkeypatch, tmp_path)
    turn = house.say(template.format(obj=f"{obj} für die Haustür"))
    assert touched == []
    assert "Termin" not in turn.speech and "nicht verstanden" not in turn.speech, turn.speech
    assert any(word in turn.speech for word in ("Überwachung", "Automation")), turn.speech


@pytest.mark.parametrize("text", [
    "Lösch die Automation vom Flurlicht.",
    "Lösche die Automation fürs Flurlicht.",
    "Entfern die Automation für das Flurlicht.",
])
def test_automation_deletion_takes_the_same_object_forms(monkeypatch, tmp_path, text):
    house, touched = _house(monkeypatch, tmp_path)
    house.say("Wenn die Haustür geöffnet wird, schalte das Flurlicht ein.")
    house.say("Ja.")
    turn = house.say(text)
    assert touched == []
    assert turn.speech.startswith("Soll die Automation"), turn.speech
    house.say("Ja.")
    assert house.automations() == []


def test_the_answer_uses_the_spoken_words(monkeypatch, tmp_path):
    house, _ = _house(monkeypatch, tmp_path)
    turn = house.say("Lösche die Meldung zur Haustür.")
    assert "„Haustür“" in turn.speech, turn.speech


@pytest.mark.parametrize("text", [
    "Lösche den Termin morgen um 9.",
    "Lösche Milch von der Einkaufsliste.",
])
def test_calendar_and_lists_keep_their_sentences(text):
    from homeintent.monitoring_management import names_managed_object, parse_monitoring_management

    assert not names_managed_object(text)
    assert parse_monitoring_management(text) is None
