"""Automation Action Semantic Model (HomeIntent V5 plan, Teil 4/10,
V5.7-V5.12): ``ActionModel``/``ActionGroup`` are the AST for a (possibly
sequenced, possibly parallel) automation action list ("mach das Flurlicht
an, fahr die Rollläden hoch und stelle die Heizung auf 21 Grad"). Mirrors
``condition_model.py``'s "one type-discriminated leaf dataclass + one
composition wrapper" shape.

Kept free of Home Assistant/hassil imports, same boundary as
``automation_model.py``/``condition_model.py`` - only
``automation_action_parser.py`` (top-level, hassil-aware) constructs these
types from spoken text.

Reuses ``TriggerTarget`` from ``automation_model.py`` verbatim (Regel 6, no
twin type) for "what does this action act on" - the exact same
domain/device_class/area/entity_id shape a trigger's/condition's target
already is. ``ConditionNode`` (``condition_model.py``) is reused verbatim
for WAIT's condition - ``automation_action_parser.py`` delegates straight to
``AutomationConditionParser`` for it (see that parser's own docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Mapping

from .automation_model import TriggerTarget
from .condition_model import ConditionNode


class ActionType(Enum):
    """The 11 action types HomeIntent V5 Teil 4/10 (V5.7) names."""

    TURN_ON = auto()
    TURN_OFF = auto()
    SET_BRIGHTNESS = auto()
    SET_COLOR = auto()
    SET_COLOR_TEMPERATURE = auto()
    SET_POSITION = auto()
    SET_TEMPERATURE = auto()
    SET_FAN_SPEED = auto()
    NOTIFY = auto()
    DELAY = auto()
    WAIT = auto()
    CHOOSE = auto()
    REGISTERED_SERVICE = auto()
    # 7.9 W5: "alle 10 Minuten, bis das Tor zu ist" - ``then_steps`` repeated
    # every ``delay_seconds`` while ``if_condition`` holds, at most
    # ``max_repeats`` times (always bounded).
    REPEAT = auto()
    # 7.9 W5: "und wenn sie nach 15 Minuten immer noch offen ist, sag Anna
    # Bescheid" - wait up to ``timeout_seconds`` for ``wait_condition`` (the
    # end of the situation); only if it did not come, run ``then_steps``.
    ESCALATE = auto()


class ExecutionMode(Enum):
    """SEQUENTIAL/PARALLEL (V5.9) - the two ways sibling steps in an
    ``ActionGroup`` may run. A bare top-level action list (no "gleichzeitig")
    is SEQUENTIAL by default, matching Home Assistant's own action-list
    semantics - never wrapped in an explicit top-level ``ActionGroup`` for
    that default case (see ``AutomationModel.actions``' own field comment)."""

    SEQUENTIAL = auto()
    PARALLEL = auto()


class NotificationRecipientKind(Enum):
    """Who a NOTIFY action addresses - kept separate from ``target`` so
    "the authenticated caller", "a named notify target" and "the confirmed
    household" never collapse into one magic ``target=None``.

    ``CURRENT_USER`` ("mich"/"mir") and ``HOUSEHOLD`` ("uns") are semantic
    references that must be materialized into exact notify targets before an
    automation is persisted: the automation runs later, without a live
    conversation user. ``EXPLICIT_TARGET`` is an already exact target.
    """

    CURRENT_USER = auto()
    EXPLICIT_TARGET = auto()
    HOUSEHOLD = auto()


@dataclass(frozen=True)
class NotificationRecipient:
    """Typed NOTIFY recipient; exact ids are filled in by materialization."""

    kind: NotificationRecipientKind
    entity_ids: tuple[str, ...] = ()  # notify.* entities -> notify.send_message
    service_ids: tuple[str, ...] = ()  # legacy notify.<service> ids
    label: str = ""  # spoken device label, never an id

    @property
    def materialized(self) -> bool:
        return bool(self.entity_ids or self.service_ids)


@dataclass(frozen=True)
class ActionModel:
    """One leaf action, discriminated by ``type`` - same rationale
    ``TriggerModel``/``ConditionModel`` already document. Only the fields
    relevant to ``type`` are ever populated; the rest stay at their
    ``None`` default.
    """

    type: ActionType
    target: TriggerTarget | None = None  # TURN_ON / TURN_OFF / SET_* - not NOTIFY/DELAY/WAIT
    value: float | None = None  # SET_BRIGHTNESS/SET_POSITION/SET_FAN_SPEED (percent 0-100), SET_TEMPERATURE (degrees)
    color_name: str | None = None  # SET_COLOR - HA color_name vocabulary, reused from lexicon.py's _COLOR_SLOT_LIST
    color_temp_kelvin: int | None = None  # SET_COLOR_TEMPERATURE
    duration_seconds: int | None = None  # TURN_ON - Duration Engine (V5.11): auto-revert after this many seconds
    message: str | None = None  # NOTIFY
    recipient: NotificationRecipient | None = None  # NOTIFY - None only for legacy unaddressed notifications
    delay_seconds: int | None = None  # DELAY
    wait_condition: ConditionNode | None = None  # WAIT - reuses ConditionNode, see module docstring
    timeout_seconds: int | None = None  # WAIT - optional ("warte maximal 30 Minuten")
    if_condition: ConditionNode | None = None  # CHOOSE
    then_steps: tuple["ActionModel | ActionGroup", ...] = ()  # CHOOSE
    else_steps: tuple["ActionModel | ActionGroup", ...] = ()  # CHOOSE
    service_domain: str | None = None  # REGISTERED_SERVICE, closed allow-list
    service_name: str | None = None  # REGISTERED_SERVICE, closed allow-list
    service_data: Mapping[str, object] = field(default_factory=dict)
    max_repeats: int | None = None  # REPEAT - the upper bound the preview names
    # NOTIFY (7.9.2 A6): HomeIntent's own Home Assistant template that names
    # at run time what happened (device and value, rooms with light on).
    # Built from validated entity ids only, never from user text; ``message``
    # holds its spoken form for the preview.
    message_template: str | None = None


@dataclass(frozen=True)
class ActionGroup:
    """A composed group of steps that all run under the same
    ``ExecutionMode`` - explicit parallel branches (V5.9) are the only
    reason this wraps individual ``ActionModel``/``ActionGroup`` steps
    rather than actions living in a single flat tuple everywhere.
    ``steps`` may itself contain nested ``ActionGroup``s (e.g. a parallel
    branch containing its own short sequential sub-sequence) - no separate
    leaf-vs-branch discriminator field is needed the way ``ConditionNode``
    needs one, since ``ActionModel``/``ActionGroup`` are already distinct
    types.
    """

    mode: ExecutionMode
    steps: tuple["ActionModel | ActionGroup", ...] = ()


def _render_target(target: TriggerTarget) -> str:
    parts = [
        f"{field}={value}"
        for field, value in (
            ("domain", target.domain),
            ("device_class", target.device_class),
            ("area_id", target.area_id),
            ("floor_id", target.floor_id),
            ("entity_id", target.entity_id),
            ("quantifier", target.quantifier),
            ("quantifier_count", target.quantifier_count),
        )
        if value is not None
    ]
    if target.exclude_entity_ids:
        parts.append(f"exclude_entity_ids={','.join(target.exclude_entity_ids)}")
    if target.entity_ids:
        parts.append(f"entity_ids={','.join(target.entity_ids)}")
    return "{" + ", ".join(parts) + "}"


def _render_action(action: ActionModel) -> str:
    parts = []
    if action.target is not None:
        parts.append(f"target={_render_target(action.target)}")
    if action.value is not None:
        parts.append(f"value={action.value}")
    if action.color_name is not None:
        parts.append(f"color_name={action.color_name}")
    if action.color_temp_kelvin is not None:
        parts.append(f"color_temp_kelvin={action.color_temp_kelvin}")
    if action.duration_seconds is not None:
        parts.append(f"duration_seconds={action.duration_seconds}")
    if action.message is not None:
        parts.append(f"message={action.message!r}")
    if action.recipient is not None:
        recipient = f"recipient={action.recipient.kind.name}"
        exact = (*action.recipient.entity_ids, *action.recipient.service_ids)
        if exact:
            recipient += "[" + ",".join(exact) + "]"
        parts.append(recipient)
    if action.delay_seconds is not None:
        parts.append(f"delay_seconds={action.delay_seconds}")
    if action.service_domain is not None and action.service_name is not None:
        parts.append(f"service={action.service_domain}.{action.service_name}")
        if action.service_data:
            parts.append(f"service_data={dict(action.service_data)!r}")
    if action.wait_condition is not None:
        # Function-local import (mirrors automation_model.py's own
        # render_automation_tree() import of render_condition_tree) - avoids
        # a module-level cycle back to condition_model.py.
        from .condition_model import render_condition_tree

        rendered = render_condition_tree(action.wait_condition).strip()
        parts.append(f"wait_condition=({rendered})")
    if action.timeout_seconds is not None:
        parts.append(f"timeout_seconds={action.timeout_seconds}")
    return f"{action.type.name}(" + ", ".join(parts) + ")"


def render_action_step(step: "ActionModel | ActionGroup", indent: int = 0) -> str:
    """Plain-text debug tree, not a spoken preview (V5.23's Dry-Run text is
    a later, separate wave) - mirrors ``condition_model.py``'s
    ``render_condition_tree()``/``automation_model.py``'s
    ``render_automation_tree()``."""
    pad = "  " * indent
    if isinstance(step, ActionGroup):
        lines = [f"{pad}{step.mode.name}"]
        for child in step.steps:
            lines.append(render_action_step(child, indent + 1))
        return "\n".join(lines)
    return f"{pad}{_render_action(step)}"
