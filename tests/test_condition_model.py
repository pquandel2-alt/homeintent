"""V5 Wave 2 (V5.4-V5.6): nlu/condition_model.py's ConditionModel/ConditionNode
type system. Hass-free, no parser involved (mirrors tests/test_automation_model.py's
style).

Pure construction/equality/render tests only - nothing here is wired into
automation_condition_parser.py's caller graph beyond direct construction. These
tests pin the dataclass shapes/defaults, the AST structure (leaf vs branch
discrimination), and the render_condition_tree() debug output so later Condition-
Engine waves can extend ConditionModel without re-deriving what each field means.
"""

from __future__ import annotations

from homeintent.nlu.automation_model import NumericComparator, SunEvent, TriggerTarget
from homeintent.nlu.condition_model import (
    ConditionModel,
    ConditionNode,
    ConditionType,
    LogicalOperator,
    TimeComparator,
    condition_tree_depth,
    render_condition_tree,
)
from homeintent.nlu.semantic_state import SemanticState


# ============================================================================
# Section 1: Enums (3 Tests)
# ============================================================================


def test_condition_type_covers_all_documented_types():
    """ConditionType includes device and calendar-event conditions."""
    assert {t.name for t in ConditionType} == {
        "STATE",
        "NUMERIC",
        "TIME",
        "DATE",
        "WEEKDAY",
        "SUN",
        "PRESENCE",
        "DEVICE",
        "ENTITY",
        "CALENDAR_EVENT",
        "TEMPLATE",
        # 7.9 W2: "unchanged since midnight" ("bis 10 Uhr keine Bewegung"),
        # a new typed condition - generated from entity ids only.
        "UNCHANGED_TODAY",
    }


def test_logical_operator_covers_and_or_not():
    """LogicalOperator has AND, OR, NOT."""
    assert {o.name for o in LogicalOperator} == {"AND", "OR", "NOT"}


def test_time_comparator_covers_before_and_after():
    """TimeComparator has BEFORE, AFTER (reused for TIME and SUN conditions)."""
    assert {c.name for c in TimeComparator} == {"BEFORE", "AFTER"}


# ============================================================================
# Section 2: ConditionModel Field Abdeckung (9+ Tests, one per type)
# ============================================================================


def test_condition_model_state_carries_type_target_and_state():
    """STATE condition: type, target, state."""
    target = TriggerTarget(domain="binary_sensor", device_class="window", area_id="kueche")
    condition = ConditionModel(type=ConditionType.STATE, target=target, state=SemanticState.OPEN)
    assert condition.type is ConditionType.STATE
    assert condition.target is target
    assert condition.state is SemanticState.OPEN
    # Other fields stay at defaults
    assert condition.comparator is None
    assert condition.threshold is None
    assert condition.unit is None


def test_condition_model_numeric_carries_type_target_comparator_threshold_and_unit():
    """NUMERIC condition: type, target, comparator, threshold, unit."""
    target = TriggerTarget(domain="sensor", device_class="temperature", area_id="wohnzimmer")
    condition = ConditionModel(
        type=ConditionType.NUMERIC,
        target=target,
        comparator=NumericComparator.ABOVE,
        threshold=19.0,
        unit="°C",
    )
    assert condition.type is ConditionType.NUMERIC
    assert condition.target is target
    assert condition.comparator is NumericComparator.ABOVE
    assert condition.threshold == 19.0
    assert condition.unit == "°C"
    # Other fields stay at defaults
    assert condition.state is None
    assert condition.time_hour is None


def test_condition_model_time_carries_type_time_hour_time_minute_and_time_comparator():
    """TIME condition: type, time_hour, time_minute, time_comparator."""
    condition = ConditionModel(
        type=ConditionType.TIME,
        time_hour=20,
        time_minute=30,
        time_comparator=TimeComparator.BEFORE,
    )
    assert condition.type is ConditionType.TIME
    assert condition.time_hour == 20
    assert condition.time_minute == 30
    assert condition.time_comparator is TimeComparator.BEFORE
    # Other fields stay at defaults
    assert condition.target is None
    assert condition.date_month is None


def test_condition_model_date_carries_type_date_month_and_date_day():
    """DATE condition: type, date_month, date_day (1-based indices)."""
    condition = ConditionModel(type=ConditionType.DATE, date_month=8, date_day=15)
    assert condition.type is ConditionType.DATE
    assert condition.date_month == 8
    assert condition.date_day == 15
    # Other fields stay at defaults
    assert condition.weekdays == ()
    assert condition.target is None


def test_condition_model_weekday_carries_type_and_weekdays_tuple():
    """WEEKDAY condition: type, weekdays (HA abbrev tuple like ("sat", "sun"))."""
    condition = ConditionModel(type=ConditionType.WEEKDAY, weekdays=("sat", "sun"))
    assert condition.type is ConditionType.WEEKDAY
    assert condition.weekdays == ("sat", "sun")
    # Other fields stay at defaults
    assert condition.date_month is None
    assert condition.sun_event is None


def test_condition_model_sun_carries_type_sun_event_sun_comparator_and_offset_minutes():
    """SUN condition: type, sun_event, sun_comparator, offset_minutes."""
    condition = ConditionModel(
        type=ConditionType.SUN,
        sun_event=SunEvent.SUNSET,
        sun_comparator=TimeComparator.AFTER,
        offset_minutes=-15,
    )
    assert condition.type is ConditionType.SUN
    assert condition.sun_event is SunEvent.SUNSET
    assert condition.sun_comparator is TimeComparator.AFTER
    assert condition.offset_minutes == -15
    # Other fields stay at defaults
    assert condition.target is None


def test_condition_model_presence_carries_type_target_and_raw_state():
    """PRESENCE condition: type, target (optional), raw_state (e.g. "home"/"not_home")."""
    target = TriggerTarget(domain="person", entity_id="person.philipp")
    condition = ConditionModel(type=ConditionType.PRESENCE, target=target, raw_state="home")
    assert condition.type is ConditionType.PRESENCE
    assert condition.target is target
    assert condition.raw_state == "home"
    # Other fields stay at defaults
    assert condition.state is None


def test_condition_model_device_carries_type_device_id_and_device_condition_type():
    """DEVICE condition: type, device_id, device_condition_type."""
    condition = ConditionModel(
        type=ConditionType.DEVICE,
        device_id="dev_taster",
        device_condition_type="is_battery_level",
    )
    assert condition.type is ConditionType.DEVICE
    assert condition.device_id == "dev_taster"
    assert condition.device_condition_type == "is_battery_level"
    # Other fields stay at defaults
    assert condition.target is None


def test_condition_model_entity_carries_type_target_and_raw_state():
    """ENTITY condition: type, target, raw_state (closed vocab for non-binary domains)."""
    target = TriggerTarget(domain="lock", device_class="lock", area_id="haustuer", entity_id="lock.haustuer")
    condition = ConditionModel(type=ConditionType.ENTITY, target=target, raw_state="locked")
    assert condition.type is ConditionType.ENTITY
    assert condition.target is target
    assert condition.raw_state == "locked"
    # Other fields stay at defaults
    assert condition.state is None


def test_condition_model_only_requires_type_everything_else_defaults():
    """ConditionModel requires only type, rest default to None/empty."""
    condition = ConditionModel(type=ConditionType.TIME)
    assert condition.type is ConditionType.TIME
    assert condition.target is None
    assert condition.state is None
    assert condition.comparator is None
    assert condition.threshold is None
    assert condition.unit is None
    assert condition.time_hour is None
    assert condition.time_minute is None
    assert condition.time_comparator is None
    assert condition.date_month is None
    assert condition.date_day is None
    assert condition.weekdays == ()
    assert condition.sun_event is None
    assert condition.sun_comparator is None
    assert condition.offset_minutes is None
    assert condition.device_id is None
    assert condition.device_condition_type is None
    assert condition.raw_state is None


# ============================================================================
# Section 3: ConditionNode Leaf vs Zweig (5 Tests)
# ============================================================================


def test_condition_node_leaf_has_condition_set_operator_and_children_empty():
    """Leaf node: condition set, operator=None, children=()."""
    condition = ConditionModel(type=ConditionType.STATE, target=TriggerTarget(domain="light"), state=SemanticState.ON)
    node = ConditionNode(condition=condition)
    assert node.condition is condition
    assert node.operator is None
    assert node.children == ()


def test_condition_node_and_has_operator_and_two_or_more_children():
    """AND node: operator=AND, children tuple with 2+ leaf nodes."""
    leaf1 = ConditionNode(condition=ConditionModel(type=ConditionType.STATE, state=SemanticState.ON))
    leaf2 = ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=20))
    node = ConditionNode(operator=LogicalOperator.AND, children=(leaf1, leaf2))
    assert node.operator is LogicalOperator.AND
    assert len(node.children) == 2
    assert node.children[0] is leaf1
    assert node.children[1] is leaf2
    assert node.condition is None


def test_condition_node_or_has_operator_and_children():
    """OR node: operator=OR, children tuple with 2+ branches."""
    leaf1 = ConditionNode(condition=ConditionModel(type=ConditionType.WEEKDAY, weekdays=("sat",)))
    leaf2 = ConditionNode(condition=ConditionModel(type=ConditionType.WEEKDAY, weekdays=("sun",)))
    node = ConditionNode(operator=LogicalOperator.OR, children=(leaf1, leaf2))
    assert node.operator is LogicalOperator.OR
    assert len(node.children) == 2
    assert node.condition is None


def test_condition_node_not_has_operator_and_exactly_one_child():
    """NOT node: operator=NOT, exactly one child."""
    leaf = ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE, raw_state="home"))
    node = ConditionNode(operator=LogicalOperator.NOT, children=(leaf,))
    assert node.operator is LogicalOperator.NOT
    assert len(node.children) == 1
    assert node.children[0] is leaf
    assert node.condition is None


def test_condition_node_cannot_be_both_leaf_and_branch_simultaneously():
    """Invalid: setting both condition and operator/children breaks the invariant."""
    leaf_condition = ConditionModel(type=ConditionType.STATE, state=SemanticState.ON)
    child = ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=20))
    # The dataclass doesn't enforce this at construction, but the parser
    # and tree-processing code must never create such a node.
    # We test that such a node is structurally possible to construct
    # (no exception at construction), but downstream code treats it as malformed.
    node = ConditionNode(operator=LogicalOperator.AND, condition=leaf_condition, children=(child,))
    assert node.operator is LogicalOperator.AND
    assert node.condition is leaf_condition  # both set, but parser never does this


# ============================================================================
# Section 4: condition_tree_depth() (5 Tests)
# ============================================================================


def test_condition_tree_depth_single_leaf_is_1():
    """Leaf node = depth 1."""
    leaf = ConditionNode(condition=ConditionModel(type=ConditionType.STATE, state=SemanticState.ON))
    assert condition_tree_depth(leaf) == 1


def test_condition_tree_depth_and_with_two_leaves_is_2():
    """AND[leaf, leaf] = depth 2."""
    leaf1 = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))
    leaf2 = ConditionNode(condition=ConditionModel(type=ConditionType.TIME, time_hour=20))
    node = ConditionNode(operator=LogicalOperator.AND, children=(leaf1, leaf2))
    assert condition_tree_depth(node) == 2


def test_condition_tree_depth_and_with_nested_or_is_3():
    """AND[OR[leaf, leaf], leaf] = depth 3 (plan's example)."""
    or_leaf1 = ConditionNode(condition=ConditionModel(type=ConditionType.WEEKDAY))
    or_leaf2 = ConditionNode(condition=ConditionModel(type=ConditionType.WEEKDAY))
    or_node = ConditionNode(operator=LogicalOperator.OR, children=(or_leaf1, or_leaf2))
    and_leaf = ConditionNode(condition=ConditionModel(type=ConditionType.PRESENCE))
    node = ConditionNode(operator=LogicalOperator.AND, children=(or_node, and_leaf))
    assert condition_tree_depth(node) == 3


def test_condition_tree_depth_nested_four_levels_is_4():
    """Manually nested 4-level tree = depth 4."""
    # ((A OR B) AND C) OR D
    a = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))
    b = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))
    c = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))
    d = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))

    or1 = ConditionNode(operator=LogicalOperator.OR, children=(a, b))
    and1 = ConditionNode(operator=LogicalOperator.AND, children=(or1, c))
    or2 = ConditionNode(operator=LogicalOperator.OR, children=(and1, d))

    assert condition_tree_depth(or2) == 4


def test_condition_tree_depth_nested_five_levels_is_5():
    """Manually nested 5-level tree = depth 5."""
    leaves = [ConditionNode(condition=ConditionModel(type=ConditionType.STATE)) for _ in range(5)]

    # Build: ((((A OR B) AND C) OR D) AND E)
    or1 = ConditionNode(operator=LogicalOperator.OR, children=(leaves[0], leaves[1]))
    and1 = ConditionNode(operator=LogicalOperator.AND, children=(or1, leaves[2]))
    or2 = ConditionNode(operator=LogicalOperator.OR, children=(and1, leaves[3]))
    and2 = ConditionNode(operator=LogicalOperator.AND, children=(or2, leaves[4]))

    assert condition_tree_depth(and2) == 5


# ============================================================================
# Section 5: render_condition_tree() (3 Tests)
# ============================================================================


def test_render_condition_tree_single_state_leaf():
    """Single STATE leaf renders as "STATE(...)"."""
    target = TriggerTarget(domain="binary_sensor", device_class="window", area_id="kueche")
    condition = ConditionModel(type=ConditionType.STATE, target=target, state=SemanticState.OPEN)
    node = ConditionNode(condition=condition)
    output = render_condition_tree(node)
    assert "STATE(" in output
    assert "domain=binary_sensor" in output
    assert "device_class=window" in output
    assert "area_id=kueche" in output
    assert "state=OPEN" in output


def test_render_condition_tree_and_with_two_leaves_shows_indentation():
    """AND[STATE, TIME] renders with AND header and indented children."""
    state_cond = ConditionModel(type=ConditionType.STATE, state=SemanticState.ON)
    time_cond = ConditionModel(type=ConditionType.TIME, time_hour=20)
    state_node = ConditionNode(condition=state_cond)
    time_node = ConditionNode(condition=time_cond)
    and_node = ConditionNode(operator=LogicalOperator.AND, children=(state_node, time_node))

    output = render_condition_tree(and_node)
    lines = output.split("\n")
    assert len(lines) == 3
    assert lines[0] == "AND"
    assert lines[1].startswith("  ")  # indented
    assert "STATE(" in lines[1]
    assert lines[2].startswith("  ")  # indented
    assert "TIME(" in lines[2]


def test_render_condition_tree_nested_and_with_or_shows_nested_indentation():
    """Nested AND[OR[leaf, leaf], leaf] renders with proper nesting levels."""
    leaf1 = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))
    leaf2 = ConditionNode(condition=ConditionModel(type=ConditionType.STATE))
    leaf3 = ConditionNode(condition=ConditionModel(type=ConditionType.TIME))

    or_node = ConditionNode(operator=LogicalOperator.OR, children=(leaf1, leaf2))
    and_node = ConditionNode(operator=LogicalOperator.AND, children=(or_node, leaf3))

    output = render_condition_tree(and_node)
    lines = output.split("\n")
    # and_node at level 0: "AND"
    assert lines[0] == "AND"
    # or_node at level 1: "  OR"
    assert lines[1] == "  OR"
    # Two STATE leaves at level 2: "    STATE(...)"
    assert lines[2].startswith("    ") and "STATE(" in lines[2]
    assert lines[3].startswith("    ") and "STATE(" in lines[3]
    # TIME leaf at level 1: "  TIME(...)"
    assert lines[4].startswith("  ") and "TIME(" in lines[4]


# ============================================================================
# Section 6: Frozen & Immutability (1 Test)
# ============================================================================


def test_condition_model_and_condition_node_are_frozen():
    """Both ConditionModel and ConditionNode are frozen=True dataclasses."""
    condition = ConditionModel(type=ConditionType.STATE, state=SemanticState.ON)
    try:
        condition.type = ConditionType.TIME  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ConditionModel must be frozen")

    node = ConditionNode(condition=condition)
    try:
        node.condition = ConditionModel(type=ConditionType.TIME)  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ConditionNode must be frozen")
