"""7.9.1 A2: automations and monitors belong to someone.

Nachtest 7.9.0 B2: Anna (no administrator) paused, switched off and deleted
Philipp's garage monitor and saw it in her list. Now the confirming user is
stored as owner (metadata sidecar, goal provenance); pausing, switching,
editing and deleting is allowed to the owner or an administrator only - for
the W8 language ("Pausiere die Garagen-Meldung …") and for the older
automation management ("Lösche die Automation für …") alike. Automations
without an owner (written before 7.9.1) only an administrator manages; the
sidecar is read as it is, nothing is lost.
"""

from __future__ import annotations

import asyncio
import json
import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation

MONITOR = "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist."
_MANAGE = (
    "Pausiere die Garagen-Meldung bis morgen um 7 Uhr.",
    "Stopp die Garagen-Meldung.",
    "Schalte die Garagen-Überwachung aus.",
    "Lösche die Garagen-Meldung.",
    "Deaktiviere die Automation für das Garagentor.",
    "Lösche die Automation für das Garagentor.",
    "Entferne die Automation für das Garagentor.",
)


def _house(monkeypatch, tmp_path, allow_non_admin: bool = True) -> HouseConversation:
    options = {**PUSH_OPTIONS, "allow_non_admin_automations": allow_non_admin}
    return HouseConversation(monkeypatch, tmp_path=tmp_path, options=options, user="admin")


def _metadata(tmp_path) -> dict:
    path = tmp_path / "homeintent_automation_metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _create(house: HouseConversation, user: str = "admin", text: str = MONITOR) -> str:
    house.user = user
    house.say(text)
    assert house.say("Ja.").speech == "Automation wurde erstellt."
    return house.automations()[-1]["id"]


def test_the_owner_is_recorded(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    automation_id = _create(house)
    assert _metadata(tmp_path)[automation_id]["owner_user_id"] == "admin"


@pytest.mark.parametrize("allow_non_admin", [False, True])
@pytest.mark.parametrize("request_text", _MANAGE)
def test_another_user_cannot_manage_it(monkeypatch, tmp_path, allow_non_admin, request_text):
    house = _house(monkeypatch, tmp_path, allow_non_admin)
    _create(house)
    before = house.automations()
    house.user = "anna"
    turn = house.say(request_text)
    assert "hat Philipp angelegt" in turn.speech, turn.speech
    assert "nur Philipp oder ein Administrator" in turn.speech
    # A following "Ja" changes nothing either.
    house.say("Ja.")
    assert house.automations() == before


@pytest.mark.parametrize("request_text", _MANAGE[:3])
def test_the_owner_may_manage_it(monkeypatch, tmp_path, request_text):
    house = _house(monkeypatch, tmp_path)
    _create(house, user="anna")
    turn = house.say(request_text)
    assert "angelegt" not in turn.speech, turn.speech
    [automation] = [item for item in house.automations() if "Garagentor" in item.get("alias", "")]
    assert automation.get("initial_state") is False


def test_an_administrator_may_manage_any(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house, user="anna")
    house.user = "admin"
    turn = house.say("Lösche die Garagen-Meldung.")
    assert turn.speech.startswith("Soll ich die Überwachung"), turn.speech
    house.say("Ja.")
    assert house.automations() == []


def test_a_voice_turn_without_user_is_neither_owner_nor_admin(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house)
    house.user = None
    turn = house.say("Stopp die Garagen-Meldung.")
    assert "weiß ich nicht, wer spricht" in turn.speech, turn.speech
    assert house.automations()[0].get("initial_state", True) is True


def test_the_list_shows_own_monitors_to_non_administrators(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _create(house)
    _create(house, user="anna", text="Sag mir Bescheid, wenn die Haustür geöffnet wird.")
    house.user = "anna"
    listed = house.say("Welche Überwachungen laufen?").speech
    assert "Haustür" in listed and "Garagentor" not in listed, listed
    house.user = "admin"
    listed = house.say("Welche Überwachungen laufen?").speech
    assert "Haustür" in listed and "Garagentor" in listed, listed
    assert "(von Anna)" in listed


def test_an_automation_without_owner_is_for_administrators_only(monkeypatch, tmp_path):
    """Migration: a sidecar written before 7.9.1 has no ``owner_user_id``.
    Nothing is rewritten; only an administrator manages such an automation."""
    house = _house(monkeypatch, tmp_path)
    automation_id = _create(house)
    path = tmp_path / "homeintent_automation_metadata.json"
    legacy = _metadata(tmp_path)
    del legacy[automation_id]["owner_user_id"]
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    house.user = "anna"
    turn = house.say("Stopp die Garagen-Meldung.")
    assert "kein Eigentümer eingetragen" in turn.speech, turn.speech
    assert "nur ein Administrator" in turn.speech
    assert _metadata(tmp_path) == legacy  # no data lost, nothing rewritten
    house.user = "admin"
    turn = house.say("Stopp die Garagen-Meldung.")
    assert turn.speech.startswith("Ausgeschaltet"), turn.speech
    assert {key: value for key, value in _metadata(tmp_path)[automation_id].items()} == legacy[automation_id]


def test_a_value_change_monitor_belongs_to_its_creator(monkeypatch, tmp_path):
    """W3 runs in HomeIntent: the goal provenance carries the owner."""
    from homeintent.monitor_goal import MonitorGoalStore

    house = _house(monkeypatch, tmp_path)
    store = MonitorGoalStore(tmp_path / "goals.json")
    house.entity._runtime_data.monitor_goals = store
    house.say("Sag mir Bescheid, wenn die Luftfeuchtigkeit im Badezimmer innerhalb von 10 Minuten um 15 Prozent steigt.")
    assert house.say("Ja.").speech.startswith("Eingerichtet")
    house.user = "anna"
    turn = house.say("Lösche die Überwachung für die Luftfeuchtigkeit.")
    assert "hat Philipp angelegt" in turn.speech, turn.speech
    assert len(asyncio.run(store.async_load())) == 1


@pytest.mark.parametrize(("owner", "actor", "admin", "allowed"), [
    ("admin", "admin", True, True),
    ("anna", "admin", True, True),
    (None, "admin", True, True),
    ("anna", "anna", False, True),
    ("admin", "anna", False, False),
    (None, "anna", False, False),
    ("anna", None, False, False),
    (None, None, False, False),
])
def test_the_rule(owner, actor, admin, allowed):
    from homeintent.automation_ownership import may_manage

    assert may_manage(owner, actor, admin) is allowed


def test_a_copy_keeps_its_owner(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    automation_id = _create(house, user="anna")
    copy_id = asyncio.run(house.entity._automation_store().async_duplicate_automation(automation_id))
    assert _metadata(tmp_path)[copy_id]["owner_user_id"] == "anna"
