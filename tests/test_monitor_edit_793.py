"""7.9.3 B7: change a monitoring instead of creating it anew.

Generated phrasings × change kinds (duration, threshold, add/remove
recipient, time window).  The changed monitoring goes through validator and
preview ("Vorher: … Nachher: …") and is written only after "Ja"; the HA
evaluation model shows the new effect.  Rights like managing (owner, admin,
shared).  Several matching monitorings -> "Welche … meinst du?".
"""

from __future__ import annotations

import asyncio
import itertools

import pytest

import _ha_stub
from _ha_sim import World, fires, for_trigger_fires, run
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

GARAGE = "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist."
BATTERY = "Gib mir Bescheid, sobald irgendeine Batterie unter 25 Prozent fällt."
DOOR = "Sag mir Bescheid, wenn die Haustür geöffnet wird."


def _house(monkeypatch, tmp_path, user="admin"):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)
    asyncio.run(house.entity._runtime_data.user_contexts.async_set_household(
        ["person.philipp", "person.anna"], confirmed=True
    ))
    return house


def _create(house, sentence, reply="Ja."):
    house.say(sentence)
    assert house.say(reply).speech.startswith("Automation wurde erstellt.")


def _only(house):
    [automation] = house.automations()
    return automation


_DURATION = ("Ändere die Garagen-Meldung auf {n} Minuten.", "Stell die Garagen-Meldung auf {n} Minuten.",
             "Die Garagen-Meldung erst nach {n} Minuten.", "Mach die Garagen-Meldung auf {n} Minuten.",
             "Ändere die Meldung für das Garagentor auf {n} Minuten.")


@pytest.mark.parametrize("form,minutes", list(itertools.product(_DURATION, (15, 30))))
def test_duration(monkeypatch, tmp_path, form, minutes):
    house = _house(monkeypatch, tmp_path)
    _create(house, GARAGE)
    preview = house.say(form.format(n=minutes)).speech
    assert preview.endswith(f"Vorher: nach 10 Minuten. Nachher: nach {minutes} Minuten. Soll ich das so ändern?"), preview
    assert _only(house)["triggers"][0]["for"] == {"seconds": 600}  # nothing before "Ja"
    assert house.say("Ja.").speech.startswith("Geändert: ")
    automation = _only(house)
    assert automation["triggers"][0]["for"] == {"seconds": minutes * 60}
    assert automation["actions"][0]["data"]["message"] == f"Das Garagentor ist seit {minutes} Minuten offen."
    # HA evaluation: after 10 minutes nothing, after the new time one message.
    world = World({"cover.garagentor": "open"}, since={"cover.garagentor": 600})
    assert not for_trigger_fires(automation, world, "cover.garagentor")
    world.since["cover.garagentor"] = minutes * 60
    assert for_trigger_fires(automation, world, "cover.garagentor")


_THRESHOLD = ("Die Batterie-Meldung erst unter {n} Prozent.", "Ändere die Batterie-Meldung auf unter {n} Prozent.",
              "Stell die Batterie-Warnung auf {n} Prozent.", "Die Batterie-Meldung bitte erst unter {n} %.")


@pytest.mark.parametrize("form", _THRESHOLD)
def test_threshold(monkeypatch, tmp_path, form):
    house = _house(monkeypatch, tmp_path)
    _create(house, BATTERY, reply="Nein.")  # already-met offer: "Nein" = only set up
    preview = house.say(form.format(n=15)).speech
    assert "Vorher: unter 25" in preview and "Nachher: unter 15" in preview, preview
    house.say("Ja.")
    automation = _only(house)
    assert automation["triggers"][0]["below"] == 15.0
    world = World({"sensor.batterie_fenster_kueche": "20"}, names={"sensor.batterie_fenster_kueche": "Küche"})
    assert not fires(automation, world, "sensor.batterie_fenster_kueche", "88")
    world.states["sensor.batterie_fenster_kueche"] = "14"
    assert fires(automation, world, "sensor.batterie_fenster_kueche", "20")


_ADD = ("Schick die Fenster-Warnung auch an {p}.", "Sende die Fenster-Warnung auch an {p}.",
        "Die Fenster-Warnung bitte auch an {p}.", "Schick die Warnung für die Fenster auch an {p}.")


@pytest.mark.parametrize("form", _ADD)
def test_add_and_remove_a_recipient(monkeypatch, tmp_path, form):
    house = _house(monkeypatch, tmp_path)
    _create(house, "Sag mir Bescheid, wenn ein Fenster geöffnet wird.", reply="Nein.")
    preview = house.say(form.format(p="Anna")).speech
    assert "Vorher: an „Handy Philipp“. Nachher: an „Handy Anna“ und „Handy Philipp“." in preview, preview
    house.say("Ja.")
    automation = _only(house)
    assert automation["actions"][0]["target"]["entity_id"] == [
        "notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"]
    sent = run(automation["actions"], World({}))
    assert sent[0].target == ("notify.handy_philipp_nachricht", "notify.handy_anna_nachricht")
    removed = house.say("Nimm Anna aus der Fenster-Warnung raus.").speech
    assert "Nachher: an „Handy Philipp“." in removed, removed
    house.say("Ja.")
    assert _only(house)["actions"][0]["target"]["entity_id"] == ["notify.handy_philipp_nachricht"]


# "Schick die Fenster-Warnung nicht mehr an Anna" is stopped earlier by the
# negation lock (unchanged rule): "nicht" in a command never runs anything.
@pytest.mark.parametrize("text", ["Nimm Anna aus der Fenster-Warnung raus.", "Entferne Anna aus der Fenster-Warnung.",
                                  "Streiche Anna aus der Fenster-Warnung."])
def test_remove_forms_and_never_the_last_recipient(monkeypatch, tmp_path, text):
    house = _house(monkeypatch, tmp_path)
    _create(house, "Sag mir Bescheid, wenn ein Fenster geöffnet wird.", reply="Nein.")
    speech = house.say(text).speech
    assert speech == "Anna bekommt diese Meldung gar nicht. Ich habe nichts geändert.", speech
    assert len(house.automations()) == 1  # never deleted by "Entferne … aus"
    house.say("Schick die Fenster-Warnung auch an Anna.")
    house.say("Ja.")
    house.say("Nimm Philipp aus der Fenster-Warnung raus.")
    assert house.say("Ja.").speech.startswith("Geändert")
    assert _only(house)["actions"][0]["target"]["entity_id"] == ["notify.handy_anna_nachricht"]
    last = house.say(text).speech
    assert last == "Dann bekäme niemand mehr die Meldung; lösch sie lieber. Ich habe nichts geändert.", last


@pytest.mark.parametrize("form,expected", [
    ("Die Haustür-Meldung nur noch nachts.", ("22:00:00", "06:00:00")),
    ("Die Haustür-Meldung nur tagsüber.", ("06:00:00", "22:00:00")),
    ("Ändere die Haustür-Meldung auf nur zwischen 8 und 18 Uhr.", ("08:00:00", "18:00:00")),
    ("Die Haustür-Meldung nur von 23 bis 5 Uhr.", ("23:00:00", "05:00:00")),
])
def test_time_window(monkeypatch, tmp_path, form, expected):
    house = _house(monkeypatch, tmp_path)
    _create(house, DOOR)
    preview = house.say(form).speech
    assert "Vorher: jederzeit." in preview and f"Nachher: nur zwischen {expected[0][:5]} und {expected[1][:5]} Uhr" \
        in preview, preview
    house.say("Ja.")
    automation = _only(house)
    assert automation["conditions"] == [{"condition": "time", "after": expected[0], "before": expected[1]}]
    night = World({"binary_sensor.haustuer": "on"}, clock=(23, 30))
    noon = World({"binary_sensor.haustuer": "on"}, clock=(12, 0))
    inside = {"22:00:00": night, "06:00:00": noon, "08:00:00": noon, "23:00:00": night}[expected[0]]
    outside = noon if inside is night else night
    assert fires(automation, inside, "binary_sensor.haustuer", "off")
    assert not fires(automation, outside, "binary_sensor.haustuer", "off")


def test_no_is_nothing(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house, GARAGE)
    house.say("Ändere die Garagen-Meldung auf 15 Minuten.")
    assert house.say("Nein.").speech == "In Ordnung, die Überwachung bleibt, wie sie ist."
    assert _only(house)["triggers"][0]["for"] == {"seconds": 600}


def test_ambiguity_asks_which_one(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house, "Sag mir Bescheid, wenn das Küchenfenster geöffnet wird.", reply="Nein.")
    _create(house, "Sag mir Bescheid, wenn das Bürofenster länger als 10 Minuten offen ist.")
    question = house.say("Ändere die Fenster-Meldung auf 15 Minuten.").speech
    assert question.startswith("Welche Fenster-Meldung meinst du: „"), question
    preview = house.say("Die zweite.").speech
    assert "Vorher:" in preview and "Nachher: nach 15 Minuten" in preview, preview


def test_rights_like_managing(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house, GARAGE)
    house.user = "anna"
    speech = house.say("Ändere die Garagen-Meldung auf 15 Minuten.").speech
    assert "Soll ich das so ändern?" not in speech and _only(house)["triggers"][0]["for"] == {"seconds": 600}, speech


def test_unknown_monitoring_is_honest(monkeypatch, tmp_path):
    speech = _house(monkeypatch, tmp_path).say("Ändere die Garagen-Meldung auf 15 Minuten.").speech
    assert speech.startswith("Ich finde keine Überwachung") and "nichts geändert" in speech


def test_a_change_that_does_not_fit_is_honest(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house, DOOR)
    speech = house.say("Die Haustür-Meldung erst unter 15 Prozent.").speech
    assert speech == "Diese Überwachung hat keinen Grenzwert. Ich habe nichts geändert."


def test_validator_refuses_foreign_changes():
    from homeintent.monitor_edit import MonitorEdit, edit_config, validate_edit

    before = {"triggers": [{"trigger": "state", "entity_id": "cover.garagentor", "to": "open"}],
              "actions": [{"action": "notify.send_message", "target": {"entity_id": ["notify.a"]}}]}
    edit = MonitorEdit("duration", seconds=900)
    after, _ = edit_config(before, edit)
    assert validate_edit(before, after, edit, ["notify.a"]) is None
    sneaky = dict(after, actions=[{"action": "cover.open_cover", "target": {"entity_id": "cover.garagentor"}}])
    assert validate_edit(before, sneaky, edit, ["notify.a"]) is not None
    foreign = MonitorEdit("add_recipient", person="X")
    added, _ = edit_config(before, foreign, recipient_ids=("notify.fremd",))
    assert validate_edit(before, added, foreign, ["notify.a"]) is not None
    assert validate_edit(before, edit_config(before, MonitorEdit("duration", seconds=5))[0],
                         MonitorEdit("duration", seconds=5), ["notify.a"]) is not None


@pytest.mark.parametrize("text", ["Lösche die Garagen-Meldung.", "Welche Überwachungen laufen?",
                                  "Pausiere die Garagen-Meldung bis morgen um 7 Uhr.", "Stopp die Fensterüberwachung."])
def test_other_management_is_unchanged(text):
    from homeintent.monitoring_management import MonitoringOperation, parse_monitoring_management

    request = parse_monitoring_management(text)
    assert request is not None and request.operation is not MonitoringOperation.EDIT, request


def _goal_house(monkeypatch, tmp_path):
    from homeintent.monitor_goal import MonitorGoalStore

    house = _house(monkeypatch, tmp_path)
    house.entity._runtime_data.monitor_goals = MonitorGoalStore(tmp_path / "goals.json")
    house.say("Sag mir Bescheid, wenn die Außentemperatur innerhalb einer Stunde um 3 Grad fällt.")
    house.say("Ja.")
    return house


def _rule(house):
    from homeintent.monitor_goal import rate_rule_of

    [record] = asyncio.run(house.entity._runtime_data.monitor_goals.async_load())
    return rate_rule_of(record.goal)


def test_own_monitor_window_and_amount(monkeypatch, tmp_path):
    """HomeIntent's own monitors (goal store): window and amount."""
    house = _goal_house(monkeypatch, tmp_path)
    assert _rule(house).window_seconds == 3600
    preview = house.say("Ändere die Temperatur-Meldung auf 30 Minuten.").speech
    assert "Vorher: innerhalb von 1 Stunde. Nachher: innerhalb von 30 Minuten." in preview, preview
    house.say("Ja.")
    assert _rule(house).window_seconds == 1800
    preview = house.say("Ändere die Temperatur-Meldung auf 5 Grad.").speech
    assert "Vorher: um 3 °C. Nachher: um 5 °C." in preview or "Nachher: um 5" in preview, preview
    house.say("Ja.")
    assert _rule(house).delta == 5.0


def test_own_monitor_honest_for_other_changes(monkeypatch, tmp_path):
    house = _goal_house(monkeypatch, tmp_path)
    speech = house.say("Die Temperatur-Meldung nur nachts.").speech
    assert "nur den Zeitraum und den Betrag" in speech and "nichts geändert" in speech
