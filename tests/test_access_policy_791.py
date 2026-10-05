"""7.9.1 A1: an automation never opens an access (garage door, gate, door
drive, valve, lock) - whatever the trigger is.

Nachtest 7.9.0 B1: "Wenn alle weg sind, öffne das Garagentor." was offered
as an automation and its preview called the gate "Rollladen im Bereich
Garage". The rule lives in ``nlu/automation_access.access_openings``;
validator, preview and the write path all use it. HomeIntent refuses
honestly and offers a notification instead; closing stays allowed.

Paraphrases are generated: trigger phrases x opening verbs x access devices.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

GATE = "cover.garagentor"
VALVE = "valve.bewaesserung"

_TRIGGERS = (
    "Wenn alle weg sind",
    "Wenn niemand zuhause ist",
    "Wenn Philipp das Haus verlässt",
    "Wenn ich nach Hause komme",
    "Wenn Anna nach Hause kommt",
    "Wenn die Haustür geöffnet wird",
)
_OPENINGS = {
    GATE: ("öffne das Garagentor", "mach das Garagentor auf", "fahr das Garagentor hoch"),
    VALVE: ("öffne die Bewässerung im Garten", "mach die Bewässerung im Garten auf"),
}
_OPENING_SERVICES = {"cover.open_cover", "valve.open_valve", "lock.unlock", "lock.open",
                     "cover.set_cover_position", "valve.set_valve_position"}


def _house(monkeypatch, tmp_path, user: str = "admin") -> HouseConversation:
    return HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)


def _written_services(house: HouseConversation) -> set[str]:
    found: set[str] = set()

    def walk(steps):
        for step in steps or ():
            if isinstance(step, dict):
                if isinstance(step.get("action"), str):
                    found.add(step["action"])
                for value in step.values():
                    if isinstance(value, list):
                        walk(value)
                    elif isinstance(value, dict):
                        walk([value])

    for automation in house.automations():
        walk(automation.get("actions"))
    return found


@pytest.mark.parametrize("trigger", _TRIGGERS)
@pytest.mark.parametrize(("entity_id", "action"), [
    (entity_id, action) for entity_id, actions in _OPENINGS.items() for action in actions
])
def test_an_access_never_opens_by_itself(monkeypatch, tmp_path, trigger, entity_id, action):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(f"{trigger}, {action}.")
    assert "öffne ich nicht automatisch" in preview.speech, preview.speech
    assert "Rollladen" not in preview.speech
    # The offer is a notification; "Ja" writes nothing that opens.
    house.say("Ja.")
    written = _written_services(house)
    assert not written & _OPENING_SERVICES, written
    assert written <= {"notify.send_message"}, written


@pytest.mark.parametrize("trigger", _TRIGGERS[:3])
def test_closing_stays_allowed_and_names_the_kind(monkeypatch, tmp_path, trigger):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(f"{trigger}, schließe das Garagentor.")
    assert "Garagentor" in preview.speech and "Rollladen" not in preview.speech, preview.speech
    assert "Soll diese Automation erstellt werden?" in preview.speech
    house.say("Ja.")
    assert "cover.close_cover" in _written_services(house)


def test_the_notification_offer_reaches_the_speaker(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    preview = house.say("Wenn alle weg sind, öffne das Garagentor.")
    assert "Stattdessen melde ich es dir" in preview.speech
    assert "„Handy Philipp“" in preview.speech
    house.say("Ja.")
    [automation] = house.automations()
    assert "öffne" not in automation["alias"].split("(")[-1]
    assert "kein automatisches Öffnen" in automation["alias"]
    [action] = automation["actions"]
    assert action["target"]["entity_id"] == ["notify.handy_philipp_nachricht"]


def test_no_is_cancelled(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Wenn Philipp das Haus verlässt, öffne das Garagentor.")
    assert "Abgebrochen" in house.say("Nein.").speech
    assert house.automations() == []


def test_a_scheduled_opening_is_refused_too(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    preview = house.say("Öffne um 7 Uhr das Garagentor.")
    assert "öffne ich nicht automatisch" in preview.speech
    house.say("Ja.")
    assert not _written_services(house) & _OPENING_SERVICES


def test_a_script_that_opens_the_gate_is_an_opening(monkeypatch, tmp_path):
    import _ha_stub

    house = _house(monkeypatch, tmp_path)
    _ha_stub.register_script(house.entity.hass, "script.gute_nacht", [
        {"action": "cover.open_cover", "target": {"entity_id": GATE}},
    ])
    preview = house.say("Wenn ich nach Hause komme, starte das Skript Gute Nacht.")
    assert "öffne ich nicht automatisch" in preview.speech, preview.speech
    assert "über „Gute Nacht“" in preview.speech
    house.say("Ja.")
    assert house.automations() == []


def test_a_scene_that_opens_the_gate_is_an_opening(monkeypatch, tmp_path):
    import _ha_stub

    house = _house(monkeypatch, tmp_path)
    _ha_stub.register_scene(house.entity.hass, "scene.abwesend", {GATE: "open"})
    preview = house.say("Wenn ich nach Hause komme, aktiviere die Szene Abwesend.")
    assert "öffne ich nicht automatisch" in preview.speech, preview.speech
    house.say("Ja.")
    assert house.automations() == []


def test_the_write_path_refuses_a_stored_opening(monkeypatch, tmp_path):
    """Defense in depth: whatever stored the draft, "Ja" never writes it."""
    from homeintent.nlu.action_model import ActionModel, ActionType
    from homeintent.nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType
    from homeintent.nlu.context import ConversationContext, PendingAutomationConfirmation

    house = _house(monkeypatch, tmp_path)
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.TIME, time_hour=7, time_minute=0),),
        actions=(ActionModel(ActionType.TURN_ON, target=TriggerTarget(entity_id=GATE)),),
        source_text="test",
    )
    house.entity._context_store.set(house.conversation_id, ConversationContext(
        last_command=None, last_entities=(), last_area=None, pending_clarification=None,
        pending_automation_confirmation=PendingAutomationConfirmation(model, "admin"),
    ))
    turn = house.say("Ja.")
    assert "öffne ich nicht automatisch" in turn.speech
    assert house.automations() == []


# --- the policy function itself -----------------------------------------


def _entity(entity_id: str):
    return next(entity for entity in house_entities() if entity.entity_id == entity_id)


@pytest.mark.parametrize(("device_class", "kind"), [
    ("garage", "garage_door"), ("gate", "gate"), ("door", "door"),
    ("shutter", None), ("awning", None), ("blind", None), ("window", None),
])
def test_access_kind_of_covers(device_class, kind):
    from homeintent.nlu.automation_access import access_kind

    entity = replace(_entity("cover.markise"), friendly_name="Antrieb", aliases=(), device_class=device_class)
    found = access_kind(entity)
    assert (found.value if found is not None else None) == kind


def test_valves_and_locks_are_access():
    from homeintent.nlu.automation_access import AccessKind, access_kind

    assert access_kind(_entity(VALVE)) is AccessKind.VALVE
    assert access_kind(_entity("lock.haustuerschloss")) is AccessKind.LOCK
    assert access_kind(None, "cover.unbekannt") is AccessKind.GATE  # fail closed


@pytest.mark.parametrize(("domain", "service", "opens"), [
    ("cover", "open_cover", True), ("cover", "set_cover_position", True),
    ("cover", "close_cover", False), ("valve", "open_valve", True),
    ("valve", "close_valve", False), ("lock", "unlock", True), ("lock", "lock", False),
])
def test_effects_of_scripts_and_scenes(domain, service, opens):
    from homeintent.effect_graph import Effect
    from homeintent.nlu.automation_access import access_openings

    entity_id = {"cover": GATE, "valve": VALVE, "lock": "lock.haustuerschloss"}[domain]
    found = access_openings((), house_entities(), (Effect(domain, service, (entity_id,)),), {entity_id: "Skript"})
    assert bool(found) is opens
    if opens:
        assert found[0].via == "Skript"


def test_validator_and_preview_share_the_rule():
    from homeintent.nlu.action_model import ActionModel, ActionType
    from homeintent.nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType
    from homeintent.nlu.automation_preview import render_automation_preview
    from homeintent.nlu.automation_validator import AutomationValidationError, validate_automation

    entities = house_entities()
    trigger = TriggerModel(type=TriggerType.TIME, time_hour=7, time_minute=0)
    for target, opens in (
        (TriggerTarget(entity_id=GATE), True),
        (TriggerTarget(domain="cover", area_id="garage"), True),
        (TriggerTarget(entity_id=VALVE), True),
        (TriggerTarget(entity_id="cover.markise"), False),
    ):
        model = AutomationModel(triggers=(trigger,), actions=(ActionModel(ActionType.TURN_ON, target=target),))
        assert validate_automation(model) is None  # entity-free stages unchanged
        error = validate_automation(model, entities)
        assert (error is AutomationValidationError.UNSAFE_ACCESS_OPENING) is opens
        preview = render_automation_preview(model, entities)
        assert ("öffne ich nicht automatisch" in preview) is opens
        assert ("Soll diese Automation erstellt werden?" in preview) is not opens


@pytest.mark.parametrize(("target", "noun"), [
    ({"domain": "cover", "area_id": "garage"}, "Garagentor"),
    ({"domain": "cover", "area_id": "terrasse"}, "Markise"),
    ({"domain": "cover", "area_id": "schlafzimmer"}, "Rollladen"),
    ({"domain": "cover", "area_id": "buro"}, "Jalousie"),
])
def test_preview_names_the_kind_of_cover(target, noun):
    from homeintent.nlu.action_model import ActionModel, ActionType
    from homeintent.nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType
    from homeintent.nlu.automation_preview import render_automation_preview

    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.TIME, time_hour=7, time_minute=0),),
        actions=(ActionModel(ActionType.TURN_OFF, target=TriggerTarget(**target)),),
    )
    preview = render_automation_preview(model, house_entities())
    assert f"{noun} im Bereich" in preview, preview
