"""HomeIntent V5 Teil 7/10 (V5.25): nlu/ha_automation_generator.py's
generate_ha_automation_config() - the pure translator from an
AutomationModel into the Home Assistant-native automation config dict.

Hass-free, pure-function module (see its own docstring) - tests build
AutomationModel/TriggerModel/ConditionNode/ActionModel instances directly
and call generate_ha_automation_config() directly, same style
tests/test_automation_validator.py already established. Covers every
supported Trigger/Condition/Action type's translation plus every
GenerationError refusal case the module docstring names (Regel 4: DEVICE/
bare WEEKDAY triggers, DEVICE/DATE conditions, WAIT actions,
ENTITY_NOT_FOUND, UNSUPPORTED_STATE).
"""

from __future__ import annotations

from datetime import datetime, timezone

from homeintent.entities import EntitySnapshot
from homeintent.nlu.action_model import ActionGroup, ActionModel, ActionType, ExecutionMode
from homeintent.nlu.automation_model import (
    AutomationModel,
    NumericComparator,
    SunEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from homeintent.nlu.condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator, TimeComparator
from homeintent.nlu.ha_automation_generator import (
    GenerationError,
    generate_ha_automation_config,
    resolve_automation_action_entity_ids,
)
from homeintent.nlu.semantic_state import SemanticState

KUECHE_FENSTER = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küchenfenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
KUECHE_LICHT = EntitySnapshot(
    "light.kueche_licht", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
WOHNZIMMER_ROLLO = EntitySnapshot(
    "cover.wohnzimmer_rollo", "Wohnzimmer Rollo", "cover", "closed",
    area_id="wohnzimmer", area_name="Wohnzimmer",
)
HEIZUNG = EntitySnapshot(
    "climate.heizung", "Heizung", "climate", "heat",
    area_id="wohnzimmer", area_name="Wohnzimmer",
)
BUERO_LUEFTER = EntitySnapshot(
    "fan.buero", "Bürolüfter", "fan", "off",
    area_id="buero", area_name="Büro",
)
NOTIFY_PHILIPP = EntitySnapshot(
    "notify.mobile_app_philipp", "Philipp Handy", "notify", "unknown"
)
PHILIPP = EntitySnapshot("person.philipp", "Philipp", "person", "home")
TEMPERATUR_SENSOR = EntitySnapshot(
    "sensor.temperatur", "Temperatur", "sensor", "21.5",
    area_id="wohnzimmer", area_name="Wohnzimmer",
)

ALL_ENTITIES = [
    KUECHE_FENSTER, KUECHE_LICHT, WOHNZIMMER_ROLLO, HEIZUNG, BUERO_LUEFTER,
    PHILIPP, TEMPERATUR_SENSOR, NOTIFY_PHILIPP,
]


def test_action_entity_ids_include_groups_and_both_choose_branches():
    choose = ActionModel(
        type=ActionType.CHOOSE,
        if_condition=ConditionNode(
            condition=ConditionModel(
                type=ConditionType.ENTITY,
                target=TriggerTarget(entity_id="light.kueche_licht"),
                raw_state="on",
            )
        ),
        then_steps=(
            ActionModel(
                type=ActionType.TURN_ON,
                target=TriggerTarget(entity_id="fan.buero"),
            ),
        ),
        else_steps=(
            ActionModel(
                type=ActionType.SET_POSITION,
                target=TriggerTarget(entity_id="cover.wohnzimmer_rollo"),
                value=50,
            ),
        ),
    )
    model = AutomationModel(
        actions=(
            ActionGroup(
                mode=ExecutionMode.PARALLEL,
                steps=(
                    ActionModel(
                        type=ActionType.TURN_ON,
                        target=TriggerTarget(entity_id="light.kueche_licht"),
                    ),
                    choose,
                ),
            ),
        )
    )

    assert resolve_automation_action_entity_ids(model, ALL_ENTITIES) == {
        "light.kueche_licht",
        "fan.buero",
        "cover.wohnzimmer_rollo",
    }


def _model(trigger: TriggerModel, *, conditions=(), actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),)) -> AutomationModel:
    return AutomationModel(triggers=(trigger,), conditions=conditions, actions=actions, source_text="Testsatz")


# --- triggers: supported translations ---------------------------------------


def test_state_trigger_translates_to_state_platform():
    model = _model(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "state", "entity_id": "binary_sensor.kueche_fenster", "to": "on"}]


def test_state_trigger_on_a_plain_light_domain_translates_on_off():
    model = _model(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="light.kueche_licht"), state=SemanticState.ON))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"][0]["to"] == "on"


def test_state_trigger_on_a_cover_translates_open_closed():
    model = _model(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="cover.wohnzimmer_rollo"), state=SemanticState.OPEN))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"][0]["to"] == "open"


def test_numeric_state_trigger_above():
    model = _model(TriggerModel(type=TriggerType.NUMERIC_STATE, target=TriggerTarget(entity_id="sensor.temperatur"), comparator=NumericComparator.ABOVE, threshold=25))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "numeric_state", "entity_id": "sensor.temperatur", "above": 25}]


def test_numeric_state_trigger_below():
    model = _model(TriggerModel(type=TriggerType.NUMERIC_STATE, target=TriggerTarget(entity_id="sensor.temperatur"), comparator=NumericComparator.BELOW, threshold=18))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"][0]["below"] == 18


def test_presence_trigger_is_a_direction_neutral_state_trigger():
    model = _model(TriggerModel(type=TriggerType.PRESENCE, target=TriggerTarget(entity_id="person.philipp")))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "state", "entity_id": "person.philipp"}]


def test_sun_trigger_with_offset():
    model = _model(TriggerModel(type=TriggerType.SUN, sun_event=SunEvent.SUNSET, offset_minutes=-15))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "sun", "event": "sunset", "offset": "-00:15:00"}]


def test_sun_trigger_without_offset_omits_the_key():
    model = _model(TriggerModel(type=TriggerType.SUN, sun_event=SunEvent.SUNRISE))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert "offset" not in result.config["triggers"][0]


def test_time_trigger():
    model = _model(TriggerModel(type=TriggerType.TIME, time_hour=7, time_minute=30))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "time", "at": "07:30:00"}]


def test_time_trigger_with_explicit_second_wave_13():
    # Wave 13 ("Relative-Zeit-Automationen"): time_second is only ever set by
    # match_relative_time_automation() (a spoken absolute clock time like "um
    # 20 Uhr" never sets it, see TriggerModel's own comment) - real seconds
    # must reach the generated YAML unchanged, not get silently rounded away.
    model = _model(TriggerModel(type=TriggerType.TIME, time_hour=12, time_minute=5, time_second=42))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "time", "at": "12:05:42"}]


def test_time_trigger_without_explicit_second_still_emits_00_regression():
    # Regression guard for the Wave 13 edit: a spoken absolute-time sentence
    # never populates time_second, so the generator must keep emitting ":00"
    # for it exactly as before this wave.
    model = _model(TriggerModel(type=TriggerType.TIME, time_hour=20, time_minute=None))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["triggers"] == [{"trigger": "time", "at": "20:00:00"}]


def test_confirmed_relative_time_adds_a_full_date_guard():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.TIME, time_hour=0, time_minute=0, time_second=15),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
        scheduled_for=datetime(2026, 8, 22, 0, 0, 15, tzinfo=timezone.utc),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)

    assert result.error is None
    assert result.config["conditions"] == [
        {
            "condition": "template",
            "value_template": "{{ now().date().isoformat() == '2026-08-22' }}",
        }
    ]


def test_trigger_delay_seconds_becomes_the_first_action():
    model = _model(
        TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN, delay_seconds=600),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0] == {"delay": {"seconds": 600}}
    assert result.config["actions"][1]["action"] == "homeassistant.turn_on"


# --- triggers: refusals -------------------------------------------------------


def test_device_trigger_is_unsupported():
    model = _model(TriggerModel(type=TriggerType.DEVICE, device_trigger_type="turned_on"))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.UNSUPPORTED_TRIGGER_TYPE


def test_bare_weekday_trigger_is_unsupported():
    model = _model(TriggerModel(type=TriggerType.WEEKDAY, weekdays=("sat", "sun")))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.UNSUPPORTED_TRIGGER_TYPE


def test_state_trigger_with_unresolvable_target_is_entity_not_found():
    model = _model(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="light.does_not_exist"), state=SemanticState.ON))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.ENTITY_NOT_FOUND


def test_state_trigger_with_unsupported_semantic_state_for_the_domain():
    # sensor.temperatur is a plain sensor: not cover, not binary_sensor,
    # not one of the on/off domains - no SemanticState has a faithful
    # raw-state translation for it (Regel 4: refuse, don't guess a state).
    model = _model(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="sensor.temperatur"), state=SemanticState.ON))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.UNSUPPORTED_STATE


# --- conditions: supported translations ---------------------------------------


def _trigger() -> TriggerModel:
    return TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN)


def test_state_condition():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.STATE, target=TriggerTarget(entity_id="light.kueche_licht"), state=SemanticState.ON)),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "state", "entity_id": "light.kueche_licht", "state": "on"}]


def test_numeric_condition():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.NUMERIC, target=TriggerTarget(entity_id="sensor.temperatur"), comparator=NumericComparator.ABOVE, threshold=20)),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "numeric_state", "entity_id": "sensor.temperatur", "above": 20}]


def test_time_condition():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=22, time_comparator=TimeComparator.BEFORE)),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "time", "before": "22:00:00"}]


def test_weekday_condition_translates_to_time_condition_with_weekday_field():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.WEEKDAY, weekdays=("sat", "sun"))),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "time", "weekday": ["sat", "sun"]}]


def test_sun_condition_with_offset():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.SUN, sun_event=SunEvent.SUNSET, sun_comparator=TimeComparator.AFTER, offset_minutes=30)),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "sun", "after": "sunset", "after_offset": "00:30:00"}]


def test_presence_condition_with_target():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, target=TriggerTarget(entity_id="person.philipp"), raw_state="home")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "state", "entity_id": "person.philipp", "state": "home"}]


def test_presence_condition_without_target_ors_across_every_person():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [
        {"condition": "or", "conditions": [{"condition": "state", "entity_id": "person.philipp", "state": "home"}]}
    ]


def test_entity_condition_raw_state():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.ENTITY, target=TriggerTarget(entity_id="cover.wohnzimmer_rollo"), raw_state="closed")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{"condition": "state", "entity_id": "cover.wohnzimmer_rollo", "state": "closed"}]


def test_and_condition_tree():
    model = _model(
        _trigger(),
        conditions=(
            ConditionNode(
                operator=LogicalOperator.AND,
                children=(
                    ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=6, time_comparator=TimeComparator.AFTER)),
                    ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=22, time_comparator=TimeComparator.BEFORE)),
                ),
            ),
        ),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [
        {
            "condition": "and",
            "conditions": [{"condition": "time", "after": "06:00:00"}, {"condition": "time", "before": "22:00:00"}],
        }
    ]


def test_not_condition_tree():
    model = _model(
        _trigger(),
        conditions=(
            ConditionNode(
                operator=LogicalOperator.NOT,
                children=(ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, target=TriggerTarget(entity_id="person.philipp"), raw_state="home")),),
            ),
        ),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"][0]["condition"] == "not"


def test_no_conditions_omits_the_key_entirely():
    model = _model(_trigger())
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert "conditions" not in result.config


# --- conditions: refusals -----------------------------------------------------


def test_device_condition_is_unsupported():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.DEVICE, device_condition_type="is_open")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.UNSUPPORTED_CONDITION_TYPE


def test_date_condition_uses_bounded_local_time_template():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.DATE, date_month=12, date_day=24)),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["conditions"] == [{
        "condition": "template",
        "value_template": "{{ now().month == 12 and now().day == 24 }}",
    }]


def test_condition_with_unresolvable_target_is_entity_not_found():
    model = _model(_trigger(), conditions=(ConditionNode(condition=ConditionModel(type=ConditionType.STATE, target=TriggerTarget(entity_id="light.does_not_exist"), state=SemanticState.ON)),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.ENTITY_NOT_FOUND


# --- actions: supported translations -------------------------------------------


def test_turn_on_light_uses_homeassistant_turn_on():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"] == [{"action": "homeassistant.turn_on", "target": {"entity_id": "light.kueche_licht"}}]


def test_turn_on_cover_uses_cover_open_cover():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="cover.wohnzimmer_rollo")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0]["action"] == "cover.open_cover"


def test_turn_off_cover_uses_cover_close_cover():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.TURN_OFF, target=TriggerTarget(entity_id="cover.wohnzimmer_rollo")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0]["action"] == "cover.close_cover"


def test_set_brightness():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.SET_BRIGHTNESS, target=TriggerTarget(entity_id="light.kueche_licht"), value=60),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"] == [{"action": "light.turn_on", "target": {"entity_id": "light.kueche_licht"}, "data": {"brightness_pct": 60}}]


def test_set_color():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.SET_COLOR, target=TriggerTarget(entity_id="light.kueche_licht"), color_name="red"),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0]["data"] == {"color_name": "red"}


def test_set_color_temperature():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.SET_COLOR_TEMPERATURE, target=TriggerTarget(entity_id="light.kueche_licht"), color_temp_kelvin=2700),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0]["data"] == {"color_temp_kelvin": 2700}


def test_set_position():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.SET_POSITION, target=TriggerTarget(entity_id="cover.wohnzimmer_rollo"), value=50),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0] == {"action": "cover.set_cover_position", "target": {"entity_id": "cover.wohnzimmer_rollo"}, "data": {"position": 50}}


def test_floor_scoped_action_resolves_only_entities_on_that_floor():
    downstairs = EntitySnapshot(
        "cover.eg", "Rollladen unten", "cover", "open", floor_id="eg", floor_name="Erdgeschoss"
    )
    upstairs = EntitySnapshot(
        "cover.dg", "Rollladen oben", "cover", "open", floor_id="dg", floor_name="Dachgeschoss"
    )
    model = _model(
        _trigger(),
        actions=(
            ActionModel(
                type=ActionType.SET_POSITION,
                target=TriggerTarget(domain="cover", floor_id="dg", quantifier="all"),
                value=50,
            ),
        ),
    )

    result = generate_ha_automation_config(model, [*ALL_ENTITIES, downstairs, upstairs])

    assert result.error is None
    assert result.config["actions"][0]["target"] == {"entity_id": "cover.dg"}


def test_set_temperature():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.SET_TEMPERATURE, target=TriggerTarget(entity_id="climate.heizung"), value=21),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0] == {"action": "climate.set_temperature", "target": {"entity_id": "climate.heizung"}, "data": {"temperature": 21}}


def test_set_fan_speed():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.SET_FAN_SPEED, target=TriggerTarget(entity_id="fan.buero"), value=75),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"][0] == {"action": "fan.turn_on", "target": {"entity_id": "fan.buero"}, "data": {"percentage": 75}}


def test_notify_uses_persistent_notification():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.NOTIFY, message="Fenster ist offen"),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"] == [{"action": "persistent_notification.create", "data": {"message": "Fenster ist offen"}}]


def test_targetless_notify_with_stable_automation_id_uses_agent_runtime():
    model = AutomationModel(
        triggers=(TriggerModel(
            type=TriggerType.STATE,
            target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"),
            state=SemanticState.OPEN,
        ),),
        actions=(ActionModel(type=ActionType.NOTIFY, message="Fenster offen"),),
    )
    result = generate_ha_automation_config(
        model, ALL_ENTITIES, automation_id="stable-rule-id"
    )

    assert result.error is None
    assert result.config is not None
    assert result.config["actions"] == [{
        "action": "homeintent.proactive_message",
        "data": {
            "rule_id": "stable-rule-id",
            "title": "HomeIntent",
            "message": "Fenster offen",
            "mode": "inform",
            "source_entity_id": "binary_sensor.kueche_fenster",
            "expected_source_state": "on",
        },
    }]


def test_numeric_agent_rule_carries_recheck_predicate():
    model = AutomationModel(
        triggers=(TriggerModel(
            type=TriggerType.NUMERIC_STATE,
            target=TriggerTarget(entity_id="sensor.temperatur"),
            comparator=NumericComparator.ABOVE,
            threshold=28,
        ),),
        actions=(ActionModel(type=ActionType.NOTIFY, message="Zu warm"),),
    )

    result = generate_ha_automation_config(model, ALL_ENTITIES, automation_id="hot")

    assert result.config is not None
    assert result.config["actions"][0]["data"] == {
        "rule_id": "hot",
        "title": "HomeIntent",
        "message": "Zu warm",
        "mode": "inform",
        "source_entity_id": "sensor.temperatur",
        "source_comparator": "above",
        "source_threshold": 28,
    }


def test_notify_can_use_one_explicit_notify_entity():
    model = _model(
        _trigger(),
        actions=(
            ActionModel(
                type=ActionType.NOTIFY,
                target=TriggerTarget(entity_id="notify.mobile_app_philipp"),
                message="Außentemperatur beträgt 28 Grad.",
            ),
        ),
    )

    result = generate_ha_automation_config(model, ALL_ENTITIES)

    assert result.error is None
    assert result.config["actions"] == [
        {
            "action": "notify.send_message",
            "target": {"entity_id": "notify.mobile_app_philipp"},
            "data": {"message": "Außentemperatur beträgt 28 Grad."},
        }
    ]


def test_numeric_equality_uses_an_exact_template_trigger():
    model = _model(
        TriggerModel(
            type=TriggerType.NUMERIC_STATE,
            target=TriggerTarget(entity_id="sensor.temperatur"),
            comparator=NumericComparator.EQUAL,
            threshold=28,
        )
    )

    result = generate_ha_automation_config(model, ALL_ENTITIES)

    assert result.error is None
    assert result.config["triggers"] == [
        {
            "trigger": "template",
            "value_template": "{{ states('sensor.temperatur') | float(none) == 28 }}",
        }
    ]


def test_delay_action():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.DELAY, delay_seconds=120),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["actions"] == [{"delay": {"seconds": 120}}]


def test_sequential_action_group():
    model = _model(
        _trigger(),
        actions=(
            ActionGroup(
                mode=ExecutionMode.SEQUENTIAL,
                steps=(
                    ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),
                    ActionModel(type=ActionType.TURN_OFF, target=TriggerTarget(entity_id="light.kueche_licht")),
                ),
            ),
        ),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert "sequence" in result.config["actions"][0]
    assert len(result.config["actions"][0]["sequence"]) == 2


def test_parallel_action_group():
    model = _model(
        _trigger(),
        actions=(
            ActionGroup(
                mode=ExecutionMode.PARALLEL,
                steps=(
                    ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),
                    ActionModel(type=ActionType.NOTIFY, message="Los geht's"),
                ),
            ),
        ),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert "parallel" in result.config["actions"][0]
    assert len(result.config["actions"][0]["parallel"]) == 2


# --- actions: refusals ---------------------------------------------------------


def test_wait_action_supports_whole_house_presence():
    people = [
        EntitySnapshot("person.philipp", "Philipp", "person", "not_home"),
        EntitySnapshot("person.gast", "Gast", "person", "home"),
    ]
    model = _model(_trigger(), actions=(ActionModel(
        type=ActionType.WAIT,
        wait_condition=ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home")),
        timeout_seconds=1800,
    ),))
    result = generate_ha_automation_config(model, [*ALL_ENTITIES, *people])
    assert result.error is None
    wait = result.config["actions"][0]
    assert "is_state('person.philipp', \"home\")" in wait["wait_template"]
    assert "is_state('person.gast', \"home\")" in wait["wait_template"]
    assert wait["timeout"] == {"seconds": 1800}


def test_wait_action_supports_sun_condition_without_offset():
    condition = ConditionModel(
        type=ConditionType.SUN,
        sun_event=SunEvent.SUNSET,
        sun_comparator=TimeComparator.AFTER,
    )
    model = _model(_trigger(), actions=(ActionModel(
        type=ActionType.WAIT,
        wait_condition=ConditionNode(condition=condition),
    ),))

    result = generate_ha_automation_config(model, ALL_ENTITIES)

    assert result.error is None
    assert result.config["actions"][0]["wait_template"] == (
        "{{ is_state('sun.sun', 'below_horizon') }}"
    )


def test_action_with_unresolvable_target_is_entity_not_found():
    model = _model(_trigger(), actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.does_not_exist")),))
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.ENTITY_NOT_FOUND


def test_action_error_short_circuits_before_later_actions_are_translated():
    model = _model(
        _trigger(),
        actions=(
            ActionModel(type=ActionType.WAIT, wait_condition=ConditionNode(condition=ConditionModel(type=ConditionType.DEVICE, device_condition_type="vendor_specific"))),
            ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),
        ),
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.config is None
    assert result.error is GenerationError.UNSUPPORTED_ACTION_TYPE


# --- overall config shape -------------------------------------------------------


def test_config_alias_is_the_source_text_and_has_no_id_key():
    model = AutomationModel(
        triggers=(_trigger(),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
        source_text="Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.",
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert result.config["alias"] == "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein."
    assert "id" not in result.config


# --- Wave 12, "Einmalige Automation" (fire-once, then self-delete) --------


def test_once_false_appends_no_delete_action():
    model = _model(_trigger())
    result = generate_ha_automation_config(model, ALL_ENTITIES)
    assert result.error is None
    assert all(a.get("action") != "homeintent.delete_automation" for a in result.config["actions"])


def test_once_true_appends_the_self_delete_action_as_the_last_step():
    model = AutomationModel(
        triggers=(_trigger(),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
        source_text="Testsatz",
        once=True,
    )
    result = generate_ha_automation_config(model, ALL_ENTITIES, automation_id="abc123")
    assert result.error is None
    assert result.config["actions"][-1] == {
        "action": "homeintent.delete_automation",
        "data": {"automation_id": "abc123"},
    }
    # The automation's own real action(s) still run first, unaffected.
    assert result.config["actions"][0]["action"] == "homeassistant.turn_on"


def test_once_true_without_an_automation_id_asserts_rather_than_silently_omitting_it():
    model = AutomationModel(
        triggers=(_trigger(),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
        source_text="Testsatz",
        once=True,
    )
    try:
        generate_ha_automation_config(model, ALL_ENTITIES)
    except AssertionError:
        pass
    else:
        raise AssertionError("expected an AssertionError when once=True but automation_id is None")
