"""HomeIntent V5 Teil 7/10 (V5.23-V5.24, "Dry Run"):
nlu/automation_preview.py's render_automation_preview() - the spoken German
"Automation erkannt: Wenn ..., dann ... Soll diese Automation erstellt
werden?" text a user actually confirms.

Hass-free, pure-function module (see its own docstring) - tests build
AutomationModel/TriggerModel/ConditionNode/ActionModel instances directly
and call render_automation_preview() directly, same "exercise the pure
function directly" style tests/test_automation_validator.py already
established. entities is only ever a lookup table here (see the module
docstring: never re-resolves, never guesses a new entity).
"""

from __future__ import annotations

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
from homeintent.nlu.automation_preview import render_automation_preview
from homeintent.nlu.condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator, TimeComparator
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
ALL_ENTITIES = [KUECHE_FENSTER, KUECHE_LICHT]


def _preview(model: AutomationModel) -> str:
    return render_automation_preview(model, ALL_ENTITIES)


# --- envelope: every preview starts/ends the same way, regardless of body --


def test_preview_envelope_wraps_the_sentence():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert text.startswith("Automation erkannt: Wenn ")
    assert text.endswith(" Soll diese Automation erstellt werden?")


# --- triggers ---------------------------------------------------------------


def test_state_trigger_uses_entity_friendly_name_and_state():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "Küchenfenster wird geöffnet" in text


def test_numeric_state_trigger_above():
    model = AutomationModel(
        triggers=(
            TriggerModel(
                type=TriggerType.NUMERIC_STATE,
                target=TriggerTarget(domain="sensor", area_id="kueche"),
                comparator=NumericComparator.ABOVE,
                threshold=25,
            ),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "über 25 liegt" in text


def test_device_trigger_never_invents_a_friendlier_description():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.DEVICE, device_trigger_type="turned_on"),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "„turned_on“" in text


def test_presence_trigger_is_direction_neutral_never_guesses_arrive_or_leave():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.PRESENCE),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "sich der Anwesenheitsstatus von jemand ändert" in text
    assert "Ankunft" not in text
    assert "Verlassen" not in text


def test_sun_trigger_with_offset():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.SUN, sun_event=SunEvent.SUNSET, offset_minutes=-15),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "15 Minuten vor dem Sonnenuntergang" in text


def test_time_trigger():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.TIME, time_hour=7, time_minute=30),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "07:30 Uhr" in text


def test_time_trigger_with_seconds_speaks_seconds():
    model = AutomationModel(
        triggers=(
            TriggerModel(
                type=TriggerType.TIME,
                time_hour=7,
                time_minute=30,
                time_second=42,
            ),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    assert "07:30:42 Uhr" in _preview(model)


def test_relative_time_preview_keeps_the_spoken_offset():
    model = AutomationModel(
        triggers=(
            TriggerModel(
                type=TriggerType.RELATIVE_TIME,
                relative_offset_seconds=30,
            ),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
        once=True,
    )
    assert "30 Sekunden vergangen sind" in _preview(model)


def test_weekday_trigger():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.WEEKDAY, weekdays=("sat", "sun")),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "Samstag oder Sonntag ist" in text


def test_trigger_with_delay_seconds_appends_wait_clause():
    model = AutomationModel(
        triggers=(
            TriggerModel(
                type=TriggerType.STATE,
                target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"),
                state=SemanticState.OPEN,
                delay_seconds=600,
            ),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "10 Minuten lang gewartet wird" in text


def test_multiple_triggers_are_joined_with_oder():
    # Home Assistant fires an automation on *any* of its triggers; 7.3.0
    # speaks that as "oder" (the former "und" misdescribed the automation).
    model = AutomationModel(
        triggers=(
            TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),
            TriggerModel(type=TriggerType.TIME, time_hour=8, time_minute=0),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "Küchenfenster wird geöffnet oder es 08:00 Uhr ist" in text


# --- conditions ---------------------------------------------------------------


def test_condition_is_appended_with_und_after_the_trigger():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        conditions=(
            ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home")),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "zuhause ist" in text
    assert ", dann " in text


def test_negated_presence_home_uses_the_idiomatic_niemand_zuhause_phrasing():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        conditions=(
            ConditionNode(
                operator=LogicalOperator.NOT,
                children=(ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home")),),
            ),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "niemand zuhause ist" in text
    assert "nicht (" not in text


def test_and_or_conditions_are_parenthesized():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        conditions=(
            ConditionNode(
                operator=LogicalOperator.OR,
                children=(
                    ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=6, time_comparator=TimeComparator.AFTER)),
                    ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=22, time_comparator=TimeComparator.BEFORE)),
                ),
            ),
        ),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert " oder " in text
    assert text.count("(") >= 2


# --- actions ---------------------------------------------------------------


def test_notify_action_quotes_the_message():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        actions=(ActionModel(type=ActionType.NOTIFY, message="Fenster ist offen"),),
    )
    text = _preview(model)
    assert "„Fenster ist offen“" in text


def test_wait_action_renders_its_nested_condition():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        actions=(
            ActionModel(
                type=ActionType.WAIT,
                wait_condition=ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home")),
                timeout_seconds=1800,
            ),
        ),
    )
    text = _preview(model)
    assert "warten, bis" in text
    assert "zuhause ist" in text
    assert "maximal 30 Minuten" in text


def test_parallel_action_group_is_joined_with_gleichzeitig_und():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
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
    text = _preview(model)
    assert "gleichzeitig" in text
    assert " und " in text


def test_sequential_actions_are_joined_with_dann():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id="binary_sensor.kueche_fenster"), state=SemanticState.OPEN),),
        actions=(
            ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),
            ActionModel(type=ActionType.TURN_OFF, target=TriggerTarget(entity_id="light.kueche_licht")),
        ),
    )
    text = _preview(model)
    assert "einschalten, dann" in text
    assert "ausschalten" in text


def test_unresolved_target_falls_back_to_unbekanntes_geraet_not_a_crash():
    model = AutomationModel(
        triggers=(TriggerModel(type=TriggerType.STATE, target=None, state=SemanticState.OPEN),),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche_licht")),),
    )
    text = _preview(model)
    assert "unbekanntes Gerät wird geöffnet" in text
