"""7.9.2 A2 (owner decision T2, "Variante C"): only water/irrigation valves
may open by itself - and never without their end.

Matrix: valve kinds (device class x name x area) x triggers x spoken forms
of "open for a while". The expectation is derived from the valve's kind
(``_ALLOWED``), never from a sentence list:

* irrigation valve -> preview that says it opens automatically and when it
  closes again; the written automation opens, waits and closes;
* gas valve, main/supply valve, unknown valve -> refused like 7.9.1
  (notification offered instead), whatever the trigger.

Plus: scheduled irrigation is understood (incl. the verb "bewässern"),
"Wie lange?" when no duration is given, absence triggers stay allowed for
irrigation, ambiguity asks, validator = preview = write path.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

VALVE = "valve.bewaesserung"

# (device_class, name, area_id, area_name) -> may open by itself
_KINDS: dict[str, tuple[str | None, str, str, str, bool]] = {
    "water_garden": ("water", "Bewässerung Garten", "garten", "Garten", True),
    "genus_no_class": (None, "Bewässerung Garten", "garten", "Garten", True),
    "sprinkler": ("water", "Rasensprenger", "garten", "Garten", True),
    "raised_bed": ("water", "Ventil Hochbeet", "terrasse", "Terrasse", True),
    "garden_area": ("water", "Wasserventil", "garten", "Garten", True),
    "drip": ("water", "Tropfschlauch Beet", "terrasse", "Terrasse", True),
    "garden_no_class": (None, "Gartenventil", "garten", "Garten", False),
    "gas": ("gas", "Gasventil", "kellerraum", "Kellerraum", False),
    "gas_named_irrigation": ("gas", "Bewässerung", "garten", "Garten", False),
    "main_water": ("water", "Hauptwasserventil", "hauswirtschaftsraum", "Hauswirtschaftsraum", False),
    "supply_garden": ("water", "Zuleitung Garten", "garten", "Garten", False),
    "main_tap": ("water", "Haupthahn", "garten", "Garten", False),
    "unknown": (None, "Ventil Drei", "kellerraum", "Kellerraum", False),
    "water_no_hint": ("water", "Wasserventil Keller", "kellerraum", "Kellerraum", False),
}
_TRIGGERS = (
    "Jeden Morgen um 6 Uhr",
    "Wenn alle weg sind,",
    "Wenn die Haustür geöffnet wird,",
)
_FORMS = (
    "öffne {name} für 20 Minuten",
    "schalte {name} für 20 Minuten ein",
    "öffne {name} 20 Minuten lang",
)


def _entities(kind: str):
    device_class, name, area_id, area_name, _ = _KINDS[kind]
    entities = []
    for entity in house_entities():
        if entity.entity_id == "valve.hauptwasserventil" and kind != "main_water":
            continue  # one valve in the house: the one under test
        if entity.entity_id == VALVE:
            entity = replace(entity, friendly_name=name, device_class=device_class,
                             area_id=area_id, area_name=area_name)
        entities.append(entity)
    if kind == "main_water":
        entities = [entity for entity in entities if entity.entity_id != VALVE]
    return entities


def _valve_id(kind: str) -> str:
    return "valve.hauptwasserventil" if kind == "main_water" else VALVE


def _house(monkeypatch, tmp_path, kind: str = "water_garden") -> HouseConversation:
    return HouseConversation(monkeypatch, _entities(kind), tmp_path=tmp_path, options=PUSH_OPTIONS)


def _actions(house: HouseConversation) -> list[str]:
    found: list[str] = []

    def walk(steps):
        for step in steps or ():
            if isinstance(step, dict):
                if isinstance(step.get("action"), str):
                    found.append(step["action"])
                if "delay" in step:
                    found.append("delay")
                for value in step.values():
                    if isinstance(value, list):
                        walk(value)
    for automation in house.automations():
        walk(automation.get("actions"))
    return found


@pytest.mark.parametrize("form", _FORMS)
@pytest.mark.parametrize("trigger", _TRIGGERS)
@pytest.mark.parametrize("kind", list(_KINDS))
def test_only_irrigation_valves_open_by_themselves(monkeypatch, tmp_path, kind, trigger, form):
    name = _KINDS[kind][1]
    allowed = _KINDS[kind][4]
    house = _house(monkeypatch, tmp_path, kind)
    preview = house.say(f"{trigger} {form.format(name=name)}.")
    if allowed:
        assert "öffnet sich dabei automatisch" in preview.speech, preview.speech
        assert "schließt nach 20 Minuten wieder" in preview.speech
        if "Nur heute oder jeden Tag" in preview.speech:
            house.say("Jeden Tag.")
        house.say("Ja.")
        assert _actions(house) == ["valve.open_valve", "delay", "valve.close_valve"], house.automations()
    else:
        assert "öffne ich nicht automatisch" in preview.speech, preview.speech
        house.say("Ja.")
        assert "valve.open_valve" not in _actions(house)


@pytest.mark.parametrize(("text", "seconds"), [
    ("Jeden Morgen um 6 Uhr öffne die Bewässerung für 20 Minuten.", 1200),
    ("Jeden Morgen um 6 Uhr schalte die Bewässerung für 20 Minuten ein.", 1200),
    ("Jeden Morgen um 6 Uhr bewässere den Garten 15 Minuten.", 900),
    ("Bewässere jeden Abend um 20 Uhr den Rasen für eine halbe Stunde.", 1800),
    ("Jeden Tag um 5:30 Uhr beregne den Garten zehn Minuten lang.", 600),
    ("Wenn die Sonne untergeht, schalte die Bewässerung für 10 Minuten ein.", 600),
])
def test_scheduled_irrigation_is_understood(monkeypatch, tmp_path, text, seconds):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(text)
    assert "Bewässerung Garten öffnen und nach" in preview.speech, preview.speech
    assert "Achtung: „Bewässerung Garten“ öffnet sich dabei automatisch" in preview.speech
    if "Nur heute oder jeden Tag" in preview.speech:
        house.say("Jeden Tag.")
    house.say("Ja.")
    [automation] = house.automations()
    [sequence] = automation["actions"]
    assert sequence["sequence"][1] == {"delay": {"seconds": seconds}}
    assert sequence["sequence"][2]["action"] == "valve.close_valve"


@pytest.mark.parametrize("answer", ["20 Minuten", "eine halbe Stunde", "für 15 Minuten", "zwei Stunden"])
@pytest.mark.parametrize("text", [
    "Jeden Morgen um 6 Uhr öffne die Bewässerung.",
    "Öffne die Bewässerung, wenn alle weg sind.",
    "Wenn die Sonne untergeht, schalte die Bewässerung ein.",
])
def test_without_duration_it_asks_how_long(monkeypatch, tmp_path, text, answer):
    house = _house(monkeypatch, tmp_path)
    question = house.say(text)
    assert question.speech.startswith("Wie lange soll „Bewässerung Garten“"), question.speech
    assert house.automations() == []
    preview = house.say(answer)
    assert "wieder schließen" in preview.speech, preview.speech


def test_a_non_answer_asks_again_and_cancel_ends(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Jeden Morgen um 6 Uhr öffne die Bewässerung.")
    again = house.say("blau")
    assert "Dauer" in again.speech and "Wie lange" in again.speech
    house.say("Abbrechen.")
    assert house.say("Ja.").speech.startswith("Gerade ist keine Frage offen")
    assert house.automations() == []


def test_no_endless_irrigation_reaches_the_write_path(monkeypatch, tmp_path):
    """Defense in depth: a stored draft without end is never written."""
    from homeintent.nlu.action_model import ActionModel, ActionType
    from homeintent.nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType
    from homeintent.nlu.context import ConversationContext, PendingAutomationConfirmation

    house = _house(monkeypatch, tmp_path)
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.TIME, time_hour=6, time_minute=0),),
        actions=(ActionModel(ActionType.REGISTERED_SERVICE, target=TriggerTarget(entity_id=VALVE),
                             service_domain="valve", service_name="open_valve"),),
        source_text="x",
    )
    house.entity._context_store.set(house.conversation_id, ConversationContext(
        last_command=None, last_entities=(), last_area=None, pending_clarification=None,
        pending_automation_confirmation=PendingAutomationConfirmation(model=model, requested_by_user_id="admin"),
    ))
    turn = house.say("Ja.")
    assert "ohne Ende" in turn.speech
    assert house.automations() == []


def test_validator_preview_and_write_share_the_rule():
    from homeintent.nlu.action_model import ActionModel, ActionType
    from homeintent.nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType
    from homeintent.nlu.automation_preview import render_automation_preview
    from homeintent.nlu.automation_validator import AutomationValidationError, validate_automation

    entities = house_entities()
    trigger = TriggerModel(type=TriggerType.TIME, time_hour=6, time_minute=0)

    def open_valve(entity_id, seconds=None):
        return ActionModel(ActionType.REGISTERED_SERVICE, target=TriggerTarget(entity_id=entity_id),
                           service_domain="valve", service_name="open_valve", duration_seconds=seconds)

    timed = AutomationModel(triggers=(trigger,), actions=(open_valve(VALVE, 600),))
    assert validate_automation(timed, entities) is None
    assert "schließt nach 10 Minuten wieder" in render_automation_preview(timed, entities)
    endless = AutomationModel(triggers=(trigger,), actions=(open_valve(VALVE),))
    assert validate_automation(endless, entities) is AutomationValidationError.IRRIGATION_WITHOUT_END
    closed_later = AutomationModel(triggers=(trigger,), actions=(
        open_valve(VALVE),
        ActionModel(ActionType.DELAY, delay_seconds=300),
        ActionModel(ActionType.REGISTERED_SERVICE, target=TriggerTarget(entity_id=VALVE),
                    service_domain="valve", service_name="close_valve"),
    ))
    assert validate_automation(closed_later, entities) is None
    main = AutomationModel(triggers=(trigger,), actions=(open_valve("valve.hauptwasserventil", 600),))
    assert validate_automation(main, entities) is AutomationValidationError.UNSAFE_ACCESS_OPENING
    assert "öffne ich nicht automatisch" in render_automation_preview(main, entities)


@pytest.mark.parametrize("kind", list(_KINDS))
def test_irrigation_rule_matrix(kind):
    from homeintent.nlu.automation_access import AccessKind, access_kind, irrigation_valve

    entity = next(item for item in _entities(kind) if item.entity_id == _valve_id(kind))
    allowed = _KINDS[kind][4]
    assert irrigation_valve(entity) is allowed
    assert (access_kind(entity) is None) is allowed
    if not allowed:
        assert access_kind(entity) is AccessKind.VALVE


def test_gates_doors_and_locks_stay_access():
    from homeintent.nlu.automation_access import AccessKind, access_kind

    by_id = {entity.entity_id: entity for entity in house_entities()}
    assert access_kind(by_id["cover.garagentor"]) is AccessKind.GARAGE_DOOR
    assert access_kind(by_id["lock.haustuerschloss"]) is AccessKind.LOCK
    assert access_kind(None, "valve.unbekannt") is AccessKind.VALVE  # fail closed


def test_two_irrigation_valves_ask_which(monkeypatch, tmp_path):
    entities = house_entities()
    second = replace(next(e for e in entities if e.entity_id == VALVE),
                     entity_id="valve.bewaesserung_vorgarten", friendly_name="Bewässerung Vorgarten",
                     area_id="terrasse", area_name="Terrasse")
    house = HouseConversation(monkeypatch, [*entities, second], tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Jeden Morgen um 6 Uhr öffne die Bewässerung für 20 Minuten.")
    assert "öffnet sich dabei automatisch" not in turn.speech
    assert "Vorgarten" in turn.speech and "Garten" in turn.speech, turn.speech
    assert house.automations() == []


def test_a_duration_on_an_action_without_opposite_is_not_dropped(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Jeden Morgen um 6 Uhr stelle die Heizung im Büro für 20 Minuten auf 22 Grad.")
    assert "Soll diese Automation erstellt werden" not in turn.speech or "20 Minuten" in turn.speech


@pytest.mark.parametrize("text", [
    "Jeden Morgen um 6 Uhr schalte das Flurlicht für 20 Minuten ein.",
    "Wenn die Haustür geöffnet wird, schalte das Flurlicht für 5 Minuten ein.",
])
def test_duration_of_switchable_actions_is_kept(monkeypatch, tmp_path, text):
    """Found while fixing A2: "für N Minuten" was dropped for every action."""
    house = _house(monkeypatch, tmp_path)
    preview = house.say(text)
    assert "wieder ausschalten" in preview.speech, preview.speech
    if "Nur heute oder jeden Tag" in preview.speech:
        house.say("Jeden Tag.")
    house.say("Ja.")
    actions = _actions(house)
    assert actions[-3:] == ["homeassistant.turn_on", "delay", "homeassistant.turn_off"], actions
