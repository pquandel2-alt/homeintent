"""V5 Wave 1 (V5.1-V5.2): nlu/automation_model.py's AutomationModel/
TriggerModel type system. Hass-free, no engine involved (mirrors
tests/test_query_command.py's style).

Pure construction/equality/render tests only - nothing here is wired into
engine.py/automation_trigger_parser.py's caller graph beyond direct
construction (this wave has no engine.py entry point at all, see
automation_trigger_parser.py's module docstring). These tests pin the
dataclass shapes/defaults and the render_automation_tree() debug output so
later Condition-/Action-Engine waves can extend AutomationModel without
re-deriving what each field means.
"""

from __future__ import annotations

from homeintent.nlu.automation_model import (
    AutomationModel,
    NumericComparator,
    SunEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
    render_automation_tree,
)
from homeintent.nlu.semantic_state import SemanticState


def test_trigger_type_covers_all_documented_types():
    assert {t.name for t in TriggerType} == {
        "STATE",
        "NUMERIC_STATE",
        "DEVICE",
        "PRESENCE",
        "SUN",
        "TIME",
        "RELATIVE_TIME",
        "CALENDAR_TIME",
        "WEEKDAY",
        "CALENDAR",
    }


def test_numeric_comparator_covers_above_and_below():
    # 7.2.0: inclusive bounds are their own members, never narrowed to ABOVE/BELOW.
    assert {c.name for c in NumericComparator} == {
        "ABOVE", "BELOW", "EQUAL", "AT_LEAST", "AT_MOST",
    }


def test_sun_event_covers_sunrise_and_sunset():
    assert {e.name for e in SunEvent} == {"SUNRISE", "SUNSET"}


def test_trigger_target_defaults_to_no_device_class_area_or_entity():
    target = TriggerTarget(domain="binary_sensor")
    assert target.device_class is None
    assert target.area_id is None
    assert target.entity_id is None


def test_trigger_target_can_carry_device_class_area_and_entity_id():
    target = TriggerTarget(domain="binary_sensor", device_class="window", area_id="kueche", entity_id="binary_sensor.fenster")
    assert target.domain == "binary_sensor"
    assert target.device_class == "window"
    assert target.area_id == "kueche"
    assert target.entity_id == "binary_sensor.fenster"


def test_trigger_target_defaults_to_no_quantifier_or_exclusions():
    target = TriggerTarget(domain="light")
    assert target.quantifier is None
    assert target.quantifier_count is None
    assert target.exclude_entity_ids == ()


def test_trigger_target_can_carry_quantifier_count_and_exclusions():
    target = TriggerTarget(
        domain="light", quantifier="count", quantifier_count=3, exclude_entity_ids=("light.flur_lampe",)
    )
    assert target.quantifier == "count"
    assert target.quantifier_count == 3
    assert target.exclude_entity_ids == ("light.flur_lampe",)


def test_trigger_model_only_requires_type_everything_else_defaults_to_none_or_empty():
    trigger = TriggerModel(type=TriggerType.SUN)
    assert trigger.target is None
    assert trigger.state is None
    assert trigger.comparator is None
    assert trigger.threshold is None
    assert trigger.device_id is None
    assert trigger.device_trigger_type is None
    assert trigger.zone_id is None
    assert trigger.sun_event is None
    assert trigger.offset_minutes is None
    assert trigger.time_hour is None
    assert trigger.time_minute is None
    assert trigger.weekdays == ()
    assert trigger.delay_seconds is None


def test_trigger_model_can_carry_a_delay_alongside_an_event_based_type():
    target = TriggerTarget(domain="person", entity_id="person.philipp")
    trigger = TriggerModel(type=TriggerType.PRESENCE, target=target, zone_id="home", delay_seconds=600)
    assert trigger.delay_seconds == 600


def test_trigger_model_state_trigger_carries_target_and_semantic_state():
    target = TriggerTarget(domain="binary_sensor", device_class="window", area_id="kueche")
    trigger = TriggerModel(type=TriggerType.STATE, target=target, state=SemanticState.OPEN)
    assert trigger.type is TriggerType.STATE
    assert trigger.target is target
    assert trigger.state is SemanticState.OPEN


def test_trigger_model_numeric_state_trigger_carries_target_comparator_and_threshold():
    target = TriggerTarget(domain="sensor", device_class="temperature", area_id="wohnzimmer")
    trigger = TriggerModel(type=TriggerType.NUMERIC_STATE, target=target, comparator=NumericComparator.ABOVE, threshold=25.0)
    assert trigger.type is TriggerType.NUMERIC_STATE
    assert trigger.target is target
    assert trigger.comparator is NumericComparator.ABOVE
    assert trigger.threshold == 25.0


def test_trigger_model_device_trigger_carries_device_id_and_press_type():
    trigger = TriggerModel(type=TriggerType.DEVICE, device_id="dev_taster", device_trigger_type="double_pressed")
    assert trigger.type is TriggerType.DEVICE
    assert trigger.device_id == "dev_taster"
    assert trigger.device_trigger_type == "double_pressed"


def test_trigger_model_presence_trigger_carries_target_and_zone_id():
    target = TriggerTarget(domain="person", entity_id="person.philipp")
    trigger = TriggerModel(type=TriggerType.PRESENCE, target=target, zone_id="home")
    assert trigger.type is TriggerType.PRESENCE
    assert trigger.target is target
    assert trigger.zone_id == "home"


def test_trigger_model_sun_trigger_carries_event_and_optional_offset():
    trigger = TriggerModel(type=TriggerType.SUN, sun_event=SunEvent.SUNSET, offset_minutes=-20)
    assert trigger.type is TriggerType.SUN
    assert trigger.sun_event is SunEvent.SUNSET
    assert trigger.offset_minutes == -20


def test_trigger_model_time_trigger_carries_hour_and_minute():
    trigger = TriggerModel(type=TriggerType.TIME, time_hour=20, time_minute=30)
    assert trigger.type is TriggerType.TIME
    assert trigger.time_hour == 20
    assert trigger.time_minute == 30


def test_trigger_model_weekday_trigger_carries_a_weekday_tuple():
    trigger = TriggerModel(type=TriggerType.WEEKDAY, weekdays=("sat", "sun"))
    assert trigger.type is TriggerType.WEEKDAY
    assert trigger.weekdays == ("sat", "sun")


def test_automation_model_defaults_to_no_triggers_conditions_or_actions():
    model = AutomationModel()
    assert model.triggers == ()
    assert model.conditions == ()
    assert model.actions == ()
    assert model.source_text == ""
    # New feature (Wave 12, "Einmalige Automation"): defaults to a normal,
    # persistent automation - never fire-once unless explicitly requested.
    assert model.once is False


def test_automation_model_carries_its_triggers_and_source_text():
    trigger = TriggerModel(type=TriggerType.TIME, time_hour=20, time_minute=0)
    model = AutomationModel(triggers=(trigger,), source_text="um 20 Uhr")
    assert model.triggers == (trigger,)
    assert model.source_text == "um 20 Uhr"
    # V5 Wave 1 scope: conditions/actions stay reserved-but-empty (Teil 3-4/10 waves fill them later)
    assert model.conditions == ()
    assert model.actions == ()


def test_dataclasses_are_frozen():
    target = TriggerTarget(domain="light")
    try:
        target.domain = "switch"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("TriggerTarget must be frozen")

    trigger = TriggerModel(type=TriggerType.SUN)
    try:
        trigger.type = TriggerType.TIME  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("TriggerModel must be frozen")

    model = AutomationModel()
    try:
        model.source_text = "x"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("AutomationModel must be frozen")


def test_render_automation_tree_reports_no_triggers_for_an_empty_model():
    tree = render_automation_tree(AutomationModel(source_text="leer"))
    assert "source_text='leer'" in tree
    assert "triggers: (none)" in tree
    assert "conditions: 0" in tree
    assert "actions: (none)" in tree


def test_render_automation_tree_renders_a_state_trigger_with_its_target_and_state():
    target = TriggerTarget(domain="binary_sensor", device_class="window", area_id="kueche")
    trigger = TriggerModel(type=TriggerType.STATE, target=target, state=SemanticState.OPEN)
    tree = render_automation_tree(AutomationModel(triggers=(trigger,), source_text="wenn das Küchenfenster geöffnet wird"))
    assert "triggers (1):" in tree
    assert "STATE(" in tree
    assert "domain=binary_sensor" in tree
    assert "device_class=window" in tree
    assert "area_id=kueche" in tree
    assert "state=OPEN" in tree


def test_render_automation_tree_renders_a_numeric_state_trigger_with_comparator_and_threshold():
    target = TriggerTarget(domain="sensor", device_class="temperature", area_id="wohnzimmer")
    trigger = TriggerModel(type=TriggerType.NUMERIC_STATE, target=target, comparator=NumericComparator.ABOVE, threshold=25.0)
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "NUMERIC_STATE(" in tree
    assert "comparator=ABOVE" in tree
    assert "threshold=25.0" in tree


def test_render_automation_tree_renders_a_device_trigger():
    trigger = TriggerModel(type=TriggerType.DEVICE, device_id="dev_taster", device_trigger_type="pressed")
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "DEVICE(" in tree
    assert "device_id=dev_taster" in tree
    assert "device_trigger_type=pressed" in tree


def test_render_automation_tree_renders_a_presence_trigger():
    target = TriggerTarget(domain="person", entity_id="person.philipp")
    trigger = TriggerModel(type=TriggerType.PRESENCE, target=target, zone_id="home")
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "PRESENCE(" in tree
    assert "entity_id=person.philipp" in tree
    assert "zone_id=home" in tree


def test_render_automation_tree_renders_a_sun_trigger_with_offset():
    trigger = TriggerModel(type=TriggerType.SUN, sun_event=SunEvent.SUNSET, offset_minutes=-20)
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "SUN(" in tree
    assert "sun_event=SUNSET" in tree
    assert "offset_minutes=-20" in tree


def test_render_automation_tree_renders_a_time_trigger():
    trigger = TriggerModel(type=TriggerType.TIME, time_hour=20, time_minute=30)
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "TIME(" in tree
    assert "time=20:30" in tree


def test_render_automation_tree_renders_a_weekday_trigger():
    trigger = TriggerModel(type=TriggerType.WEEKDAY, weekdays=("sat", "sun"))
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "WEEKDAY(" in tree
    assert "weekdays=sat,sun" in tree


def test_render_automation_tree_renders_multiple_triggers_and_counts_reserved_fields():
    state_trigger = TriggerModel(type=TriggerType.STATE, target=TriggerTarget(domain="binary_sensor"), state=SemanticState.OPEN)
    time_trigger = TriggerModel(type=TriggerType.TIME, time_hour=7, time_minute=0)
    tree = render_automation_tree(AutomationModel(triggers=(state_trigger, time_trigger)))
    assert "triggers (2):" in tree
    assert "STATE(" in tree
    assert "TIME(" in tree
    # V5 Wave 2 (conditions) / Wave 3 (actions) now render real content when
    # populated; an empty AutomationModel still reports both as absent -
    # "conditions: 0" (Wave 2's own idiom) and "actions: (none)" (Wave 3,
    # matching triggers' own "(none)" wording - see render_automation_tree).
    assert "conditions: 0" in tree
    assert "actions: (none)" in tree


def test_render_automation_tree_renders_a_delay_wrapped_presence_trigger():
    target = TriggerTarget(domain="person", entity_id="person.philipp")
    trigger = TriggerModel(type=TriggerType.PRESENCE, target=target, zone_id="home", delay_seconds=600)
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "PRESENCE(" in tree
    assert "delay_seconds=600" in tree


def test_render_automation_tree_renders_a_quantified_exclusion_target():
    target = TriggerTarget(domain="light", quantifier="all", exclude_entity_ids=("light.flur_lampe",))
    trigger = TriggerModel(type=TriggerType.STATE, target=target, state=SemanticState.OFF)
    tree = render_automation_tree(AutomationModel(triggers=(trigger,)))
    assert "quantifier=all" in tree
    assert "exclude_entity_ids=light.flur_lampe" in tree


# --- Wave 12, "Einmalige Automation" (fire-once, then self-delete) ---------


def test_automation_model_carries_the_once_flag():
    model = AutomationModel(once=True)
    assert model.once is True


def test_render_automation_tree_reports_once_true():
    tree = render_automation_tree(AutomationModel(source_text="einmalig", once=True))
    assert "once=True" in tree


def test_render_automation_tree_reports_once_false_by_default():
    tree = render_automation_tree(AutomationModel(source_text="normal"))
    assert "once=False" in tree
