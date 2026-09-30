"""Automation Condition Semantic Model (HomeIntent V5 plan, Teil 3/10,
V5.4-V5.6): ``ConditionNode`` is the AST for a (possibly logically combined,
possibly nested) automation condition ("wenn das Fenster offen ist und
jemand zuhause ist, ..."). Mirrors ``automation_model.py``'s "one
type-discriminated dataclass instead of N subclasses" shape - here applied
twice: once for the 9 leaf condition kinds (``ConditionModel``), once for
the AND/OR/NOT tree structure around them (``ConditionNode``).

Kept free of Home Assistant/hassil imports, same boundary as
``automation_model.py``/``nlu/frame.py`` - only
``automation_condition_parser.py`` (top-level, hassil-aware) constructs
these types from spoken text.

Reuses ``TriggerTarget``/``NumericComparator``/``SunEvent`` from
``automation_model.py`` verbatim (Regel 6, no twin types) rather than
redefining them - a condition's "what does this watch" question is the
exact same domain/device_class/area/entity_id shape a trigger's is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .automation_model import NumericComparator, SunEvent, TriggerTarget
from .semantic_state import SemanticState


class ConditionType(Enum):
    """The 9 condition types HomeIntent V5 Teil 3/10 (V5.4) names."""

    STATE = auto()
    NUMERIC = auto()
    TIME = auto()
    DATE = auto()
    WEEKDAY = auto()
    SUN = auto()
    PRESENCE = auto()
    DEVICE = auto()
    ENTITY = auto()
    CALENDAR_EVENT = auto()
    TEMPLATE = auto()
    # 7.9 W2: the entity has stayed in its rest ``state`` since midnight
    # ("bis 10 Uhr keine Bewegung im Bad") - generated as a closed template
    # from the entity id and state, never from user text.
    UNCHANGED_TODAY = auto()


class TimeComparator(Enum):
    """"before"/"after" - Home Assistant's own ``time``/``sun`` condition
    vocabulary (``before``/``after`` keys), reused for both TIME and SUN
    conditions rather than inventing a second before/after enum."""

    BEFORE = auto()
    AFTER = auto()


class LogicalOperator(Enum):
    AND = auto()
    OR = auto()
    NOT = auto()


@dataclass(frozen=True)
class ConditionModel:
    """One leaf condition, discriminated by ``type`` - same rationale
    ``TriggerModel`` already documents: easier to inspect/compare in tests
    and eventually serialize than a 9-way class hierarchy. Only the fields
    relevant to ``type`` are ever populated; the rest stay at their
    ``None``/``()`` default.
    """

    type: ConditionType
    target: TriggerTarget | None = None  # STATE / NUMERIC / PRESENCE / DEVICE / ENTITY
    state: SemanticState | None = None  # STATE - reuses the existing enum, no new state vocabulary
    comparator: NumericComparator | None = None  # NUMERIC
    threshold: float | None = None  # NUMERIC
    unit: str | None = None  # NUMERIC - e.g. "°C"/"W"/"%"/"kWh" (matches service_call.py's _UNIT_SPOKEN_DE keys)
    time_hour: int | None = None  # TIME
    time_minute: int | None = None  # TIME
    time_comparator: TimeComparator | None = None  # TIME
    date_month: int | None = None  # DATE - 1-12
    date_day: int | None = None  # DATE - 1-31
    weekdays: tuple[str, ...] = ()  # WEEKDAY - HA weekday abbreviations, e.g. ("sat", "sun")
    sun_event: SunEvent | None = None  # SUN - reused, no new sun-event vocabulary
    sun_comparator: TimeComparator | None = None  # SUN
    offset_minutes: int | None = None  # SUN
    device_id: str | None = None  # DEVICE
    device_condition_type: str | None = None  # DEVICE
    raw_state: str | None = None  # ENTITY - closed vocabulary for non-binary domains (locks/vacuums/media players)
    # PRESENCE without a target ("jemand/niemand zuhause"): exactly these
    # people (``presence_scope``, 7.8.3). Empty = every ``person.*`` entity.
    person_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConditionNode:
    """Leaf xor logical branch, discriminated by ``operator is None``.

    A bare single condition (no "und"/"oder"/"nicht" in the sentence) is
    still wrapped as a depth-1 leaf node - there is no separate
    ``ConditionModel``-only return type to keep track of downstream, every
    ``AutomationConditionParser.parse()`` call returns exactly one
    ``ConditionNode`` or ``None``.

    AND/OR are n-ary (flattened - "A und B und C" is one AND node with 3
    children, not binary-nested); NOT always has exactly one child.
    """

    operator: LogicalOperator | None = None
    condition: ConditionModel | None = None  # leaf only
    children: tuple["ConditionNode", ...] = ()  # branch only


def condition_tree_depth(node: ConditionNode) -> int:
    """Leaf = 1, branch = 1 + the deepest child - the metric
    ``AutomationConditionParser``'s configurable ``max_depth`` guard checks
    against the fully-built tree (see its own module docstring for why this
    is post-validation, not a live recursion counter)."""
    if not node.children:
        return 1
    return 1 + max(condition_tree_depth(child) for child in node.children)


def _render_condition(condition: ConditionModel) -> str:
    parts = []
    if condition.target is not None:
        target_parts = [
            f"{field}={value}"
            for field, value in (
                ("domain", condition.target.domain),
                ("device_class", condition.target.device_class),
                ("area_id", condition.target.area_id),
                ("floor_id", condition.target.floor_id),
                ("entity_id", condition.target.entity_id),
            )
            if value is not None
        ]
        parts.append("target={" + ", ".join(target_parts) + "}")
    if condition.state is not None:
        parts.append(f"state={condition.state.name}")
    if condition.comparator is not None:
        parts.append(f"comparator={condition.comparator.name}")
    if condition.threshold is not None:
        parts.append(f"threshold={condition.threshold}")
    if condition.unit is not None:
        parts.append(f"unit={condition.unit}")
    if condition.time_hour is not None:
        parts.append(f"time={condition.time_hour:02d}:{(condition.time_minute or 0):02d}")
    if condition.time_comparator is not None:
        parts.append(f"time_comparator={condition.time_comparator.name}")
    if condition.date_month is not None:
        parts.append(f"date={(condition.date_day or 0):02d}.{condition.date_month:02d}")
    if condition.weekdays:
        parts.append(f"weekdays={','.join(condition.weekdays)}")
    if condition.sun_event is not None:
        parts.append(f"sun_event={condition.sun_event.name}")
    if condition.sun_comparator is not None:
        parts.append(f"sun_comparator={condition.sun_comparator.name}")
    if condition.offset_minutes is not None:
        parts.append(f"offset_minutes={condition.offset_minutes}")
    if condition.device_id is not None:
        parts.append(f"device_id={condition.device_id}")
    if condition.device_condition_type is not None:
        parts.append(f"device_condition_type={condition.device_condition_type}")
    if condition.raw_state is not None:
        parts.append(f"raw_state={condition.raw_state}")
    return f"{condition.type.name}(" + ", ".join(parts) + ")"


def render_condition_tree(node: ConditionNode, indent: int = 0) -> str:
    """Plain-text debug tree, not a spoken preview (V5.23's Dry-Run text is
    a later, separate wave with its own German wording) - only for
    logs/tests/manual inspection, mirrors ``automation_model.py``'s
    ``render_automation_tree()``."""
    pad = "  " * indent
    if node.operator is None:
        assert node.condition is not None
        return f"{pad}{_render_condition(node.condition)}"
    lines = [f"{pad}{node.operator.name}"]
    for child in node.children:
        lines.append(render_condition_tree(child, indent + 1))
    return "\n".join(lines)
