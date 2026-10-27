"""One-time provenance proofs for public automation lifecycle services."""

from __future__ import annotations

import copy
import secrets
from typing import Any

from .proactive_authority import proactive_action_fingerprint

LIFECYCLE_TOKEN_FIELD = "lifecycle_token"
LIFECYCLE_OWNER_FIELD = "lifecycle_id"
LIFECYCLE_OPERATIONS = {
    "homeintent.delete_automation": "delete",
    "ha_nlu.delete_automation": "delete",
    "homeintent.record_automation_run": "record_run",
    "ha_nlu.record_automation_run": "record_run",
    "homeintent.proactive_message": "proactive_message",
    "ha_nlu.proactive_message": "proactive_message",
}


def prepare_lifecycle_actions(
    config: dict[str, Any],
    lifecycle_id: str,
    existing_orders: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Copy config and bind every internal lifecycle action to a secret order."""
    prepared = copy.deepcopy(config)
    orders: list[dict[str, Any]] = copy.deepcopy(existing_orders or [])

    def visit(value: object) -> None:
        if isinstance(value, dict):
            action = value.get("action")
            operation = LIFECYCLE_OPERATIONS.get(action) if isinstance(action, str) else None
            data = value.get("data")
            if operation is not None and isinstance(data, dict):
                target = (
                    data.get("rule_id")
                    if operation == "proactive_message"
                    else data.get("automation_id")
                )
                if isinstance(target, str) and target:
                    embedded_owner = data.get(LIFECYCLE_OWNER_FIELD)
                    embedded_token = data.get(LIFECYCLE_TOKEN_FIELD)
                    if isinstance(embedded_token, str) and embedded_owner == lifecycle_id:
                        if not any(
                            order.get("operation") == operation
                            and order.get("automation_id") == target
                            and order.get("token") == embedded_token
                            for order in orders
                        ):
                            raise ValueError(
                                "Eingebetteter Lifecycle-Nachweis fehlt im Sidecar"
                            )
                    elif embedded_owner is None and embedded_token is None:
                        token = secrets.token_urlsafe(32)
                        data[LIFECYCLE_OWNER_FIELD] = lifecycle_id
                        data[LIFECYCLE_TOKEN_FIELD] = token
                        orders.append(
                            {
                                "operation": operation,
                                "automation_id": target,
                                "token": token,
                                "status": "open",
                                **(
                                    {
                                        "action_fingerprint":
                                            proactive_action_fingerprint(data)
                                    }
                                    if operation == "proactive_message"
                                    else {}
                                ),
                            }
                        )
                    else:
                        raise ValueError("Unvollständiger Lifecycle-Nachweis im YAML")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(prepared.get("actions", []))
    return prepared, orders


def replace_lifecycle_token(
    value: object, *, operation: str, old_token: str, new_token: str
) -> bool:
    """Replace exactly one matching embedded proof in an automation config."""
    replacements = 0

    def visit(child: object) -> None:
        nonlocal replacements
        if isinstance(child, dict):
            action = child.get("action")
            data = child.get("data")
            if (
                isinstance(action, str)
                and LIFECYCLE_OPERATIONS.get(action) == operation
                and isinstance(data, dict)
                and data.get(LIFECYCLE_TOKEN_FIELD) == old_token
            ):
                data[LIFECYCLE_TOKEN_FIELD] = new_token
                replacements += 1
            for nested in child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)

    visit(value)
    if replacements > 1:
        raise ValueError("Der Lifecycle-Nachweis ist nicht eindeutig eingebettet")
    return replacements == 1


def has_exact_lifecycle_action(
    value: object,
    *,
    operation: str,
    automation_id: str,
    lifecycle_id: str,
    token: str,
) -> bool:
    """Return true only for one exact embedded lifecycle action."""
    matches = 0

    def visit(child: object) -> None:
        nonlocal matches
        if isinstance(child, dict):
            action = child.get("action")
            data = child.get("data")
            if (
                isinstance(action, str)
                and LIFECYCLE_OPERATIONS.get(action) == operation
                and isinstance(data, dict)
                and data.get("automation_id") == automation_id
                and data.get(LIFECYCLE_OWNER_FIELD) == lifecycle_id
                and data.get(LIFECYCLE_TOKEN_FIELD) == token
            ):
                matches += 1
            for nested in child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)

    visit(value)
    return matches == 1
