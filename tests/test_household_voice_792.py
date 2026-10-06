"""7.9.2 A3: voice devices without a signed-in user speak for the household.

Nachtest 7.9.1 T3: a wall satellite without a user could not manage any
monitor. With the admin option "Sprachgeräte im Haus sprechen für den
Haushalt" (off by default) it may manage *shared* monitors and automations
(owner ``household``) - never personal ones - and create shared ones when
``allow_non_admin_automations`` allows it. Marking as shared ("… für alle",
"… gemeinsam") is for the owner or an administrator; "Überwache … für uns
alle" creates a shared one. Shared monitors notify the confirmed
household. Without the option everything stays as in 7.9.1.

Matrix: option on/off x owner (household, Philipp, none) x management forms.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

SATELLITE = "assist_satellite.flur"
MONITOR = "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist."
_MANAGE = (
    "Stopp die Garagen-Meldung.",
    "Schalte die Garagen-Überwachung aus.",
    "Pausiere die Garagen-Meldung bis morgen um 7 Uhr.",
    "Lösche die Garagen-Meldung.",
    "Deaktiviere die Automation für das Garagentor.",
)
_SHARE = (
    "Mach die Garagen-Meldung für alle.",
    "Mach die Garagen-Meldung gemeinsam.",
    "Mach die Überwachung vom Garagentor für uns alle.",
    "Markiere die Garagen-Überwachung für den ganzen Haushalt.",
)
_CREATE_SHARED = (
    "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist, für uns alle.",
    "Melde dich für uns alle, wenn das Garagentor länger als 10 Minuten offen ist.",
    "Wenn das Garagentor länger als 10 Minuten offen ist, melde dich gemeinsam.",
)


def _house(monkeypatch, tmp_path, *, voice: bool, allow_non_admin: bool = True) -> HouseConversation:
    from homeassistant.core import State

    options = {**PUSH_OPTIONS, "allow_non_admin_automations": allow_non_admin,
               "household_voice_devices": voice}
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=options, user="admin")
    hass = house.entity.hass
    hass.states._states[SATELLITE] = State(SATELLITE, "idle", {"supported_features": 1})
    asyncio.run(house.entity._runtime_data.user_contexts.async_set_household(
        ["person.philipp", "person.anna"], confirmed=True
    ))
    return house


def _satellite(house: HouseConversation, text: str) -> str:
    """One turn spoken on the wall satellite, nobody signed in."""
    from homeassistant.components.conversation import ConversationInput

    result = asyncio.run(house.entity._async_handle_message(
        ConversationInput(text=text, conversation_id="flur", context=None, satellite_id=SATELLITE),
        chat_log=None,
    ))
    return result.response.speech or ""


def _metadata(tmp_path) -> dict:
    path = tmp_path / "homeintent_automation_metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _create(house: HouseConversation, user: str = "admin", text: str = MONITOR) -> str:
    house.user = user
    house.say(text)
    assert house.say("Ja.").speech == "Automation wurde erstellt."
    return house.automations()[-1]["id"]


def _owner(tmp_path, automation_id: str) -> str | None:
    return _metadata(tmp_path)[automation_id].get("owner_user_id")


def _enabled(house: HouseConversation) -> bool:
    [automation] = house.automations()
    return automation.get("initial_state") is not False


@pytest.mark.parametrize("request_text", _MANAGE[:2])
@pytest.mark.parametrize("voice", [True, False])
@pytest.mark.parametrize("owner", ["household", "admin", "none"])
def test_satellite_manages_only_shared_with_the_option(monkeypatch, tmp_path, voice, owner, request_text):
    house = _house(monkeypatch, tmp_path, voice=voice)
    automation_id = _create(house)
    if owner == "household":
        assert "gilt jetzt für den ganzen Haushalt" in house.say("Mach die Garagen-Meldung für alle.").speech
    if owner == "none":
        # A sidecar entry written before 7.9.1: no owner key at all.
        path = tmp_path / "homeintent_automation_metadata.json"
        data = _metadata(tmp_path)
        data[automation_id].pop("owner_user_id", None)
        path.write_text(json.dumps(data), encoding="utf-8")
    speech = _satellite(house, request_text)
    allowed = voice and owner == "household"
    assert _enabled(house) is not allowed, speech
    if not allowed:
        assert "Administrator" in speech or "nur gemeinsame" in speech, speech


@pytest.mark.parametrize("request_text", _MANAGE)
def test_satellite_with_option_manages_shared_in_every_form(monkeypatch, tmp_path, request_text):
    house = _house(monkeypatch, tmp_path, voice=True)
    _create(house)
    house.say("Mach die Garagen-Meldung gemeinsam.")
    speech = _satellite(house, request_text)
    assert "Administrator" not in speech and "angelegt" not in speech, speech
    if speech.startswith("Soll ich"):
        _satellite(house, "Ja.")
        assert house.automations() == []


@pytest.mark.parametrize("share", _SHARE)
def test_marking_as_shared_owner_or_admin(monkeypatch, tmp_path, share):
    house = _house(monkeypatch, tmp_path, voice=True)
    automation_id = _create(house, user="anna")
    house.user = "admin"
    speech = house.say(share).speech
    assert "gilt jetzt für den ganzen Haushalt" in speech, speech
    assert _owner(tmp_path, automation_id) == "household"
    # Shared monitors notify the confirmed household.
    [automation] = house.automations()
    assert set(automation["actions"][0]["target"]["entity_id"]) == {
        "notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"
    }


def test_only_owner_or_admin_marks_and_the_satellite_never(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, voice=True)
    automation_id = _create(house, user="admin")
    house.user = "anna"
    assert "nur Philipp oder ein Administrator" in house.say(_SHARE[0]).speech
    assert "Gemeinsam machen" in _satellite(house, _SHARE[0])
    assert _owner(tmp_path, automation_id) == "admin"


def test_the_list_shows_shared(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, voice=True)
    _create(house)
    _create(house, user="admin", text="Melde dich, wenn die Haustür länger als 5 Minuten offen ist.")
    house.say("Mach die Garagen-Meldung für alle.")
    house.user = "anna"
    listing = house.say("Welche Überwachungen laufen?").speech
    assert "Garagentor" in listing and "(gemeinsam)" in listing and "Haustür" not in listing, listing
    satellite = _satellite(house, "Welche Überwachungen laufen?")
    assert "(gemeinsam)" in satellite and "Haustür" not in satellite, satellite


@pytest.mark.parametrize("text", _CREATE_SHARED)
def test_creating_for_everyone(monkeypatch, tmp_path, text):
    house = _house(monkeypatch, tmp_path, voice=False)
    preview = house.say(text).speech
    assert "dem ganzen Haushalt (gemeinsam)" in preview, preview
    assert "für uns alle" not in preview and "gemeinsam." not in preview.split("Soll")[0][:-30]
    house.say("Ja.")
    [automation] = house.automations()
    assert _owner(tmp_path, automation["id"]) == "household"
    assert set(automation["actions"][0]["target"]["entity_id"]) == {
        "notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"
    }


def test_open_monitor_for_everyone_keeps_it_shared(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, voice=False)
    question = house.say("Überwache die Haustür für uns alle.").speech
    assert "für uns alle" not in question
    preview = house.say("Wenn sie länger als 5 Minuten offen ist.").speech
    assert "(gemeinsam)" in preview, preview
    house.say("Ja.")
    assert _owner(tmp_path, house.automations()[0]["id"]) == "household"


@pytest.mark.parametrize(("voice", "allow", "created"), [(True, True, True), (True, False, False), (False, True, False)])
def test_satellite_creates_shared_only_when_allowed(monkeypatch, tmp_path, voice, allow, created):
    house = _house(monkeypatch, tmp_path, voice=voice, allow_non_admin=allow)
    preview = _satellite(house, MONITOR)
    _satellite(house, "Ja.")
    automations = house.automations()
    if created:
        assert "(gemeinsam)" in preview and "euch" in preview, preview
        [automation] = automations
        assert _owner(tmp_path, automation["id"]) == "household"
    else:
        assert all(_owner(tmp_path, item["id"]) != "household" for item in automations)


def test_without_household_the_answer_is_honest(monkeypatch, tmp_path):
    options = {**PUSH_OPTIONS, "household_voice_devices": True}
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=options, user="admin")
    automation_id = _create(house)
    speech = house.say("Mach die Garagen-Meldung für alle.").speech
    assert "Haushalt" in speech and "nichts geändert" in speech, speech
    assert _owner(tmp_path, automation_id) == "admin"


def test_text_chat_without_device_is_no_house_voice():
    from homeintent.automation_ownership import household_voice

    hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda entity_id: object()))
    options = {"household_voice_devices": True}
    assert household_voice(hass, options, types.SimpleNamespace(context=None)) is False
    assert household_voice(hass, options, types.SimpleNamespace(context=None, satellite_id=SATELLITE)) is True
    assert household_voice(hass, {}, types.SimpleNamespace(context=None, satellite_id=SATELLITE)) is False
    signed_in = types.SimpleNamespace(context=types.SimpleNamespace(user_id="anna"), satellite_id=SATELLITE)
    assert household_voice(hass, options, signed_in) is False


@pytest.mark.parametrize(("owner", "actor", "admin", "voice", "allowed"), [
    ("household", None, False, True, True),
    ("household", None, False, False, False),
    ("household", "anna", False, False, True),
    ("admin", None, False, True, False),
    (None, None, False, True, False),
    ("admin", "anna", False, False, False),
    ("admin", "admin", False, False, True),
    (None, "x", True, False, True),
])
def test_rule_matrix(owner, actor, admin, voice, allowed):
    from homeintent.automation_ownership import may_manage

    assert may_manage(owner, actor, admin, household_voice=voice) is allowed
