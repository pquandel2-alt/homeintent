"""Who "niemand zuhause" means - one rule for every path (7.8.3).

"Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist" has
one meaning, whether it ends up as a Home Assistant automation or as a V10
monitor goal.  The set of people it quantifies over is decided here and
nowhere else:

* a confirmed household (``UserContextStore.household``) - exactly its
  people, for the condition and for the "leaves the house" triggers;
* no confirmed household - every ``person.*`` entity; the preview names
  them so the "Ja" is given knowingly;
* no ``person.*`` entity at all - there is nobody to quantify over; the
  caller says so and asks, it never guesses.

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .entities import EntitySnapshot
from .nlu.automation_model import AutomationModel, PresenceEvent, TriggerType
from .nlu.condition_model import ConditionModel, ConditionNode, ConditionType

NO_PEOPLE_TEXT = (
    "Für „niemand zuhause“ kenne ich keine Person: In Home Assistant gibt es keine "
    "person.*-Entität. Lege unter Einstellungen → Personen die Bewohner an "
    "(mit einem Gerät zur Anwesenheitserkennung), dann richte ich das ein."
)


@dataclass(frozen=True)
class PresenceScope:
    """The people "niemand/jemand zuhause" is about."""

    person_ids: tuple[str, ...]
    household_confirmed: bool
    # Confirmed household members without a ``person.*`` entity (renamed or
    # deleted): never silently dropped - the caller asks.
    missing: tuple[str, ...] = ()


def presence_scope(
    entities: Sequence[EntitySnapshot], household_person_ids: Sequence[str] = ()
) -> PresenceScope:
    people = sorted(entity.entity_id for entity in entities if entity.domain == "person")
    household = tuple(dict.fromkeys(household_person_ids))
    if household:
        known = set(people)
        return PresenceScope(
            tuple(sorted(person for person in household if person in known)),
            True,
            tuple(person for person in household if person not in known),
        )
    return PresenceScope(tuple(people), False)


def whole_house_presence(condition: ConditionModel | None) -> bool:
    """"jemand zuhause" (and, negated, "niemand zuhause") - no single person."""
    return (
        condition is not None
        and condition.type is ConditionType.PRESENCE
        and condition.target is None
    )


def _bind_node(node: ConditionNode, people: tuple[str, ...]) -> ConditionNode:
    if node.operator is None:
        condition = node.condition
        if whole_house_presence(condition) and condition is not None and not condition.person_entity_ids:
            return replace(node, condition=replace(condition, person_entity_ids=people))
        return node
    return replace(node, children=tuple(_bind_node(child, people) for child in node.children))


def uses_whole_house_presence(model: AutomationModel) -> bool:
    def visit(node: ConditionNode) -> bool:
        if node.operator is None:
            return whole_house_presence(node.condition)
        return any(visit(child) for child in node.children)

    return any(visit(node) for node in model.conditions)


def bind_presence_scope(model: AutomationModel, scope: PresenceScope) -> AutomationModel:
    """Make the model say exactly whom "niemand zuhause" quantifies over.

    Whole-house presence conditions get the scope's people; the "somebody
    leaves" triggers of a combined situation (``state_conjunction``) are
    limited to them.  Without a confirmed household the scope is every
    ``person.*`` - the same set the generator uses for an unbound
    condition - so binding never changes what Home Assistant evaluates,
    it only makes it explicit.
    """
    if not uses_whole_house_presence(model):
        return model
    conditions = tuple(_bind_node(node, scope.person_ids) for node in model.conditions)
    triggers = model.triggers
    if model.situation is not None:
        allowed = set(scope.person_ids)
        triggers = tuple(
            trigger for trigger in model.triggers
            if not (
                trigger.type is TriggerType.PRESENCE
                and trigger.presence_event is PresenceEvent.LEAVE
                and trigger.target is not None
                and trigger.target.domain == "person"
                and trigger.target.entity_id is not None
                and trigger.target.entity_id not in allowed
            )
        )
    return replace(model, conditions=conditions, triggers=triggers)


def scope_failure(model: AutomationModel, scope: PresenceScope) -> str | None:
    """The honest answer when "niemand zuhause" has nobody to be about."""
    if not uses_whole_house_presence(model):
        return None
    return unusable_scope_text(scope)


def unusable_scope_text(scope: PresenceScope) -> str | None:
    """Why ``scope`` cannot stand for "niemand zuhause", or ``None``."""
    if scope.missing:
        return (
            "Zum bestätigten Haushalt gehören "
            + ", ".join(scope.missing)
            + ", die es in Home Assistant nicht mehr gibt. Bitte bestätige den Haushalt neu."
        )
    if not scope.person_ids:
        return NO_PEOPLE_TEXT
    return None


__all__ = (
    "NO_PEOPLE_TEXT",
    "PresenceScope",
    "bind_presence_scope",
    "presence_scope",
    "scope_failure",
    "unusable_scope_text",
    "uses_whole_house_presence",
    "whole_house_presence",
)
