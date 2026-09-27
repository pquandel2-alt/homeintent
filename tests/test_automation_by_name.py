"""F9: automations are managed by their own name, not the device it starts with."""

from __future__ import annotations

import pytest

from homeintent.automation_action_edit import homeintent_candidates
from homeintent.automation_management import (
    AutomationManagementKind,
    parse_automation_management,
    select_automation_management,
)
from homeintent.automation_summary import CREATED_BY_HOMEINTENT, AutomationSummary
from homeintent.entities import EntitySnapshot

ENTITIES = [
    EntitySnapshot("light.flurlicht", "Flurlicht", "light", "off", area_id="flur", area_name="Flur"),
    EntitySnapshot("light.aussenbeleuchtung", "Außenbeleuchtung", "light", "off", area_id="garten", area_name="Garten"),
    EntitySnapshot("light.kuechenlicht", "Küchenlicht", "light", "off", area_id="kueche", area_name="Küche"),
    EntitySnapshot(
        "binary_sensor.bewegung_flur", "Bewegungsmelder Flur", "binary_sensor", "off",
        area_id="flur", area_name="Flur", device_class="motion",
    ),
]
USER_AUTOMATION = AutomationSummary(
    "1700000000001", "Flurlicht bei Bewegung",
    referenced_entity_ids=frozenset({"binary_sensor.bewegung_flur", "light.flurlicht"}),
    trigger_entity_ids=frozenset({"binary_sensor.bewegung_flur"}),
    action_entity_ids=frozenset({"light.flurlicht"}),
)
FLUR_OTHER = AutomationSummary(
    "hi-flur", "Flurlicht nachts aus", created_by=CREATED_BY_HOMEINTENT,
    referenced_entity_ids=frozenset({"light.flurlicht"}),
    action_entity_ids=frozenset({"light.flurlicht"}),
)
OUTDOOR = AutomationSummary(
    "hi-outdoor", "Bei Sonnenuntergang schalte die Außenbeleuchtung ein",
    created_by=CREATED_BY_HOMEINTENT,
    referenced_entity_ids=frozenset({"light.aussenbeleuchtung"}),
    action_entity_ids=frozenset({"light.aussenbeleuchtung"}),
)
KITCHEN = AutomationSummary(
    "hi-kitchen", "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein",
    created_by=CREATED_BY_HOMEINTENT,
    referenced_entity_ids=frozenset({"light.kuechenlicht"}),
)
AUTOMATIONS = (USER_AUTOMATION, FLUR_OTHER, OUTDOOR, KITCHEN)


@pytest.mark.parametrize(
    ("text", "enable"),
    [
        ("Deaktiviere die Automation Flurlicht bei Bewegung.", False),
        ("Aktiviere die Automation Flurlicht bei Bewegung.", True),
        ("Deaktiviere die Automation für Flurlicht bei Bewegung.", False),
    ],
)
def test_toggle_by_automation_name(engine, text, enable):
    method = engine.match_automation_enable if enable else engine.match_automation_disable
    result = method(text, ENTITIES, AUTOMATIONS)

    assert result is not None
    assert result.automation == USER_AUTOMATION
    assert result.enable is enable


def test_delete_by_automation_name_asks_for_confirmation(engine):
    result = engine.match_automation_delete(
        "Lösche die Automation Flurlicht bei Bewegung.", ENTITIES, AUTOMATIONS
    )
    assert result is not None and result.automation == USER_AUTOMATION


def test_device_named_edit_target_narrows_candidates():
    text = "Füge der Automation für Außenbeleuchtung die Bedingung hinzu, dass jemand zuhause ist."
    assert homeintent_candidates(AUTOMATIONS, text, ENTITIES) == (OUTDOOR,)


def test_explain_trigger_covers_user_automation_and_sensor_by_area():
    request = parse_automation_management("Was passiert, wenn die Bewegung im Flur erkannt wird?")
    assert request is not None
    assert request.kind is AutomationManagementKind.EXPLAIN_TRIGGER

    selection = select_automation_management(request, ENTITIES, AUTOMATIONS)

    assert selection.automations == (USER_AUTOMATION,)
