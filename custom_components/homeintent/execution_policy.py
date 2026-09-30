"""Central, configurable authorization policy for concrete service plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Mapping

from .const import (
    CONF_ADMIN_ONLY_ENTITIES,
    CONF_ALLOW_NON_ADMIN_CRITICAL,
    CONF_CONFIRMATION_LEVEL,
    CONF_CONTROL_USER_IDS,
    CONF_EFFECT_GRAPH_UNKNOWN,
    CONF_IMPLICIT_ACTION_LEVEL,
    DEFAULT_IMPLICIT_ACTION_LEVEL,
    IMPLICIT_ACTION_LEVELS,
    CONF_MAX_ACTION_TARGETS,
    CONF_READ_ONLY_ENTITIES,
    CONF_SELECTED_ENTITIES,
)
from .effect_graph import (
    PlanEffects,
    composite_targets,
    describe_followups,
    describe_unexposed,
    describe_unknown,
    is_composite_entity,
)
from .entities import EntitySnapshot
from .plan_origin import UNATTENDED_ORIGINS, PlanOrigin
from .risk import RiskLevel, classify_service_plan
from .service_call import ServiceCallPlan


def _exposure_hint(options: Mapping[str, object]) -> str:
    """Where the missing release is made (7.8.1): a fixed HomeIntent
    selection hides devices that are exposed to Assist later."""
    if options.get(CONF_SELECTED_ENTITIES):
        return (
            "HomeIntent nutzt eine feste Geräteauswahl, die diese Geräte nicht enthält, "
            "auch wenn sie in Assist freigegeben sind. Ergänze sie in den Optionen von "
            "HomeIntent unter der Geräteauswahl oder leere die Auswahl, dann gilt die "
            "Assist-Freigabe."
        )
    return (
        "Freigeben kannst du sie in Home Assistant unter Einstellungen, "
        "Sprachassistenten, Entitäten freigeben."
    )


class PolicyOutcome(Enum):
    ALLOW = auto()
    CONFIRM = auto()
    DENY = auto()


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    risk: RiskLevel
    reason: str | None = None
    # The transitive effect analysis the decision was based on (scripts,
    # scenes, groups). ``note`` carries hints for previews, e.g. possible
    # follow-up automations or an unverifiable step that needs confirming.
    effects: PlanEffects | None = None
    note: str | None = None


_RISK_BY_OPTION = {
    "medium": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
    "critical": RiskLevel.CRITICAL,
}


def _id_set(options: Mapping[str, object], key: str) -> set[str]:
    configured = options.get(key, ())
    return set(configured) if isinstance(configured, (list, tuple, set)) else set()


def plan_is_composite(
    plan: ServiceCallPlan, entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...]
) -> bool:
    """Whether a plan activates a script, scene, group or automation."""
    target_ids = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    attributes = {entity.entity_id: entity.attributes for entity in entities}
    return bool(composite_targets(
        plan.domain,
        plan.service,
        target_ids,
        lambda entity_id: (
            ("member",)
            if is_composite_entity(entity_id, attributes.get(entity_id))
            and entity_id.split(".", 1)[0] not in {"script", "scene", "automation"}
            else None
        ),
    ))


_EFFECT_VERBS = {
    ("lock", "unlock"): "entriegelt", ("lock", "lock"): "verriegelt", ("lock", "open"): "öffnet",
    ("cover", "open_cover"): "öffnet", ("cover", "close_cover"): "schließt",
    ("cover", "set_cover_position"): "bewegt",
    ("valve", "open_valve"): "öffnet", ("valve", "close_valve"): "schließt",
    ("alarm_control_panel", "alarm_disarm"): "schaltet unscharf:",
    ("button", "press"): "drückt",
}
_ROOT_KINDS = {
    "script": "Das Skript", "scene": "Die Szene", "automation": "Die Automation",
    "group": "Die Gruppe", "light": "Die Gruppe", "switch": "Die Gruppe", "cover": "Die Gruppe",
}


def describe_critical_effects(
    effects: PlanEffects, entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...]
) -> str | None:
    """"Das Skript Schlafen entriegelt dabei Haustürschloss." - every effect
    whose own risk is HIGH or CRITICAL, in the words of the question."""
    names = {entity.entity_id: entity.friendly_name for entity in entities}
    parts: list[tuple[str, str, str]] = []
    for effect in effects.effects:
        if effect.domain in {"script", "scene", "automation", "group"}:
            continue
        plan = ServiceCallPlan(effect.domain, effect.service, list(effect.entity_ids))
        if classify_service_plan(plan, entities) < RiskLevel.HIGH:
            continue
        targets = ", ".join(names.get(entity_id, entity_id) for entity_id in effect.entity_ids)
        verb = _EFFECT_VERBS.get((effect.domain, effect.service), f"führt {effect.domain}.{effect.service} aus für")
        suffix = ""
        if verb.endswith(":"):
            verb, suffix = "schaltet", " unscharf"
        part = (verb, targets, suffix)
        if part not in parts:
            parts.append(part)
    if not parts:
        return None
    root = effects.roots[0] if effects.roots else ""
    kind = _ROOT_KINDS.get(root.split(".", 1)[0], "Die Aktion")
    name = effects.names.get(root) or names.get(root) or root
    rendered = [
        f"{verb} dabei {targets}{suffix}" if index == 0 else f"{verb} {targets}{suffix}"
        for index, (verb, targets, suffix) in enumerate(parts)
    ]
    listed = rendered[0] if len(rendered) == 1 else ", ".join(rendered[:-1]) + " und " + rendered[-1]
    return f"{kind} {name} {listed}."


def evaluate_service_plan(
    plan: ServiceCallPlan,
    entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...],
    options: Mapping[str, object],
    *,
    is_admin: bool,
    user_id: str | None = None,
    effects: PlanEffects | None = None,
    origin: PlanOrigin = PlanOrigin.EXPLICIT_COMMAND,
    attended: bool = True,
    binding_confirmed: bool = False,
) -> PolicyDecision:
    """Evaluate one already-resolved plan; callers must not bypass it.

    ``effects`` is the transitive effect graph of every script, scene, group
    or automation the plan activates (``effect_graph.build_plan_effects``).
    Exposure, read-only, admin-only, target limits and risk apply to the
    *effective* targets. A composite plan without a graph fails closed.
    """
    target_ids = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    if effects is None and plan_is_composite(plan, entities):
        effects = PlanEffects.unchecked(target_ids)
    outer_risk = classify_service_plan(plan, entities)
    risk = outer_risk
    effect_targets: frozenset[str] = frozenset()
    if effects is not None:
        effect_targets = effects.effective_targets
        for effect in effects.effects:
            effect_plan = ServiceCallPlan(effect.domain, effect.service, list(effect.entity_ids))
            risk = max(risk, classify_service_plan(effect_plan, entities))
    roots = set(effects.roots) if effects is not None else set()
    effective_ids = {entity_id for entity_id in target_ids if entity_id not in roots} | set(effect_targets)
    all_ids = set(target_ids) | effective_ids

    def decide(outcome: PolicyOutcome, reason: str | None = None, note: str | None = None) -> PolicyDecision:
        return PolicyDecision(outcome, risk, reason, effects, note)

    allowed_users = _id_set(options, CONF_CONTROL_USER_IDS)
    if allowed_users and user_id not in allowed_users:
        return decide(
            PolicyOutcome.DENY,
            "Dieser Benutzer darf HomeIntent nicht zur Gerätesteuerung verwenden.",
        )
    if effects is not None and effect_targets:
        exposed = {entity.entity_id for entity in entities}
        # effective_targets leaves out entities Home Assistant does not know:
        # a step on them switches nothing (7.8.1).
        unexposed = sorted(effect_targets - exposed)
        if unexposed:
            # The exposure list is the user's configuration: no confirmation
            # can override it.
            return decide(
                PolicyOutcome.DENY,
                f"{describe_unexposed(effects, unexposed, (entity.friendly_name for entity in entities))} "
                f"{_exposure_hint(options)}",
            )
    admin_only_ids = _id_set(options, CONF_ADMIN_ONLY_ENTITIES)
    if not is_admin and all_ids & admin_only_ids:
        return decide(
            PolicyOutcome.DENY,
            "Mindestens ein Ziel darf nur von Administratoren gesteuert werden.",
        )
    configured_max = options.get(CONF_MAX_ACTION_TARGETS, 50)
    max_targets = configured_max if isinstance(configured_max, int) else 50
    if max_targets < 1:
        max_targets = 1
    if len(effective_ids or set(target_ids)) > max_targets:
        return decide(
            PolicyOutcome.DENY,
            f"Diese Aktion betrifft mehr als die erlaubten {max_targets} Ziele.",
        )
    read_only_ids = _id_set(options, CONF_READ_ONLY_ENTITIES)
    if all_ids & read_only_ids:
        return decide(
            PolicyOutcome.DENY,
            "Mindestens ein Ziel ist in HomeIntent nur zum Lesen freigegeben.",
        )
    note: str | None = None
    unknown_needs_confirmation = False
    if effects is not None and not effects.complete:
        # Fail closed: an unknown step is never LOW.
        unknown_text = describe_unknown(effects)
        mode = str(options.get(CONF_EFFECT_GRAPH_UNKNOWN, "deny"))
        if not attended or origin in UNATTENDED_ORIGINS or mode != "confirm":
            return decide(PolicyOutcome.DENY, f"{unknown_text} Ich habe nichts ausgeführt.")
        risk = max(risk, RiskLevel.HIGH)
        note = unknown_text
        unknown_needs_confirmation = True
    if effects is not None:
        # Informed consent (7.7.1 A6): a question about a script, scene,
        # group or routine names every effect from HIGH risk upwards.
        critical = describe_critical_effects(effects, entities)
        if critical:
            note = f"{note} {critical}" if note else critical
        followups = describe_followups(effects)
        if followups:
            note = f"{note} {followups}" if note else followups
    if (
        risk is RiskLevel.CRITICAL
        and user_id is None
    ):
        return decide(
            PolicyOutcome.DENY,
            "Diese sicherheitskritische Aktion benötigt einen authentifizierten Benutzer.",
            note,
        )
    if (
        risk is RiskLevel.CRITICAL
        and not is_admin
        and not bool(options.get(CONF_ALLOW_NON_ADMIN_CRITICAL, False))
    ):
        return decide(
            PolicyOutcome.DENY,
            "Diese sicherheitskritische Aktion ist nur für Administratoren erlaubt.",
            note,
        )
    implicit = origin in {PlanOrigin.IMPLICIT_NEED, PlanOrigin.INFERRED_ROUTINE}
    level = str(options.get(CONF_IMPLICIT_ACTION_LEVEL, DEFAULT_IMPLICIT_ACTION_LEVEL))
    if level not in IMPLICIT_ACTION_LEVELS:
        level = DEFAULT_IMPLICIT_ACTION_LEVEL
    if implicit and level == "understand_only":
        return decide(
            PolicyOutcome.DENY,
            "Ich habe dich verstanden. Bei indirekten Aussagen führe ich laut "
            "Einstellung nichts aus; sag mir ausdrücklich, was ich tun soll.",
            note,
        )
    configured_level = options.get(CONF_CONFIRMATION_LEVEL, "high")
    confirmation_level = _RISK_BY_OPTION.get(str(configured_level), RiskLevel.HIGH)
    if risk >= confirmation_level or unknown_needs_confirmation:
        return decide(PolicyOutcome.CONFIRM, note=note)
    # Implicit Action Policy (7.3.3): a non-explicit origin is never looser
    # than the same explicit command; everything below only adds confirmations.
    if origin is PlanOrigin.IMPLICIT_NEED and not (
        level in {"low_risk_auto", "bound_routines_auto"} and risk is RiskLevel.LOW
    ):
        return decide(PolicyOutcome.CONFIRM, note=note)
    if origin is PlanOrigin.INFERRED_ROUTINE and not (
        level == "bound_routines_auto" and binding_confirmed
    ):
        # A routine derived from "Ich gehe schlafen" stays a proposal unless
        # the user confirmed a binding and allowed bound routines to run.
        return decide(PolicyOutcome.CONFIRM, note=note)
    return decide(PolicyOutcome.ALLOW, note=note)


def validate_automation_action_targets(
    target_ids: frozenset[str] | set[str],
    options: Mapping[str, object],
    *,
    is_admin: bool = True,
    user_id: str | None = None,
    effects: PlanEffects | None = None,
    exposed_ids: frozenset[str] | set[str] | None = None,
) -> str | None:
    """Validate the concrete write scope of a confirmed automation.

    ``effects`` is the effect graph of scripts, scenes and groups the
    automation would run. An automation runs without anyone answering, so
    an unverifiable step is always refused. The check happens when the
    automation is created; a script edited later is not re-checked by
    HomeIntent (documented in the README).
    """
    if effects is not None:
        # An automation runs later: an entity that is missing today may
        # exist then, so every named entity counts here.
        if effects.referenced_targets and exposed_ids is not None:
            unexposed = sorted(effects.referenced_targets - set(exposed_ids))
            if unexposed:
                return describe_unexposed(effects, unexposed)
        if not effects.complete:
            return f"{describe_unknown(effects)} Die Automation habe ich nicht angelegt."
        target_ids = frozenset(
            {item for item in target_ids if item not in set(effects.roots)}
            | effects.referenced_targets
        )
    configured_users = options.get(CONF_CONTROL_USER_IDS, ())
    allowed_users = (
        set(configured_users)
        if isinstance(configured_users, (list, tuple, set))
        else set()
    )
    if allowed_users and user_id not in allowed_users:
        return "Dieser Benutzer darf HomeIntent nicht zur Gerätesteuerung verwenden."

    configured_admin_only = options.get(CONF_ADMIN_ONLY_ENTITIES, ())
    admin_only_ids = (
        set(configured_admin_only)
        if isinstance(configured_admin_only, (list, tuple, set))
        else set()
    )
    if not is_admin and target_ids & admin_only_ids:
        return "Die Automation würde ein nur für Administratoren freigegebenes Ziel steuern."

    configured_max = options.get(CONF_MAX_ACTION_TARGETS, 50)
    max_targets = configured_max if isinstance(configured_max, int) else 50
    max_targets = max(1, max_targets)
    if len(target_ids) > max_targets:
        return f"Die Automation würde mehr als die erlaubten {max_targets} Ziele steuern."

    configured_read_only = options.get(CONF_READ_ONLY_ENTITIES, ())
    read_only_ids = (
        set(configured_read_only)
        if isinstance(configured_read_only, (list, tuple, set))
        else set()
    )
    if target_ids & read_only_ids:
        return "Die Automation würde mindestens ein nur lesbar freigegebenes Ziel steuern."
    return None
