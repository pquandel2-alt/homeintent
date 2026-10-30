from __future__ import annotations

from homeintent.entities import EntitySnapshot
from homeintent.execution_policy import (
    PolicyOutcome,
    evaluate_service_plan,
    validate_automation_action_targets,
)
from homeintent.service_call import ServiceCallPlan
from homeintent.nlu.context import PendingServiceConfirmation


def test_critical_action_can_be_restricted_to_admins():
    lock = EntitySnapshot("lock.front", "Haustür", "lock", "locked")
    plan = ServiceCallPlan("lock", "unlock", lock.entity_id)

    denied = evaluate_service_plan(
        plan, [lock], {"allow_non_admin_critical": False}, is_admin=False
    )
    allowed = evaluate_service_plan(
        plan, [lock], {}, is_admin=True, user_id="owner"
    )

    assert denied.outcome is PolicyOutcome.DENY
    assert allowed.outcome is PolicyOutcome.CONFIRM


def test_critical_action_requires_an_authenticated_actor_even_when_option_is_enabled():
    lock = EntitySnapshot("lock.front", "Haustür", "lock", "locked")
    plan = ServiceCallPlan("lock", "unlock", lock.entity_id)

    decision = evaluate_service_plan(
        plan,
        [lock],
        {"allow_non_admin_critical": True},
        is_admin=False,
        user_id=None,
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert "authentifizierten Benutzer" in (decision.reason or "")


def test_authenticated_non_admin_critical_is_denied_by_default():
    lock = EntitySnapshot("lock.front", "Haustür", "lock", "locked")
    plan = ServiceCallPlan("lock", "unlock", lock.entity_id)

    decision = evaluate_service_plan(
        plan, [lock], {}, is_admin=False, user_id="resident"
    )

    assert decision.outcome is PolicyOutcome.DENY


def test_confirmation_threshold_is_configurable():
    cover = EntitySnapshot(
        "cover.office", "Bürorollo", "cover", "closed", device_class="shutter"
    )
    plan = ServiceCallPlan("cover", "open_cover", cover.entity_id)

    assert evaluate_service_plan(
        plan, [cover], {"confirmation_level": "medium"}, is_admin=True
    ).outcome is PolicyOutcome.CONFIRM
    assert evaluate_service_plan(
        plan, [cover], {"confirmation_level": "high"}, is_admin=True
    ).outcome is PolicyOutcome.ALLOW


def test_target_limit_denies_before_execution():
    entities = [
        EntitySnapshot(f"light.{index}", f"Licht {index}", "light", "off")
        for index in range(4)
    ]
    plan = ServiceCallPlan("homeassistant", "turn_on", [item.entity_id for item in entities])

    decision = evaluate_service_plan(
        plan, entities, {"max_action_targets": 3}, is_admin=True
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert "3 Ziele" in decision.reason


def test_policy_denies_control_of_read_only_entity():
    plan = ServiceCallPlan("light", "turn_on", "light.lesen", {})
    decision = evaluate_service_plan(
        plan,
        [],
        {"read_only_entities": ["light.lesen"]},
        is_admin=True,
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert "nur zum Lesen" in (decision.reason or "")


def test_policy_can_limit_control_to_selected_users():
    plan = ServiceCallPlan("light", "turn_on", "light.office", {})

    denied = evaluate_service_plan(
        plan,
        [],
        {"control_user_ids": ["owner"]},
        is_admin=True,
        user_id="guest",
    )
    allowed = evaluate_service_plan(
        plan,
        [],
        {"control_user_ids": ["owner"]},
        is_admin=False,
        user_id="owner",
    )

    assert denied.outcome is PolicyOutcome.DENY
    assert allowed.outcome is PolicyOutcome.ALLOW


def test_policy_can_reserve_individual_entities_for_admins():
    plan = ServiceCallPlan("switch", "turn_off", "switch.server", {})
    options = {"admin_only_entities": ["switch.server"]}

    assert evaluate_service_plan(
        plan, [], options, is_admin=False, user_id="guest"
    ).outcome is PolicyOutcome.DENY
    assert evaluate_service_plan(
        plan, [], options, is_admin=True, user_id="owner"
    ).outcome is PolicyOutcome.ALLOW

    assert "Administratoren" in validate_automation_action_targets(
        {"switch.server"}, options, is_admin=False, user_id="guest"
    )


def test_pending_confirmation_carries_requesting_actor():
    pending = PendingServiceConfirmation(
        ServiceCallPlan("lock", "unlock", "lock.front"),
        "Haustür entriegeln.",
        requested_by_user_id="user-1",
    )
    assert pending.requested_by_user_id == "user-1"


def test_automation_target_policy_applies_read_only_and_size_limits():
    assert "nur lesbar" in validate_automation_action_targets(
        {"light.read_only"}, {"read_only_entities": ["light.read_only"]}
    )
    assert "2 Ziele" in validate_automation_action_targets(
        {"light.one", "light.two", "light.three"}, {"max_action_targets": 2}
    )
    assert validate_automation_action_targets(
        {"light.one"}, {"max_action_targets": 2}
    ) is None
