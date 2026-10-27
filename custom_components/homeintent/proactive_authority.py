"""Authority and immutable action binding for proactive messages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence, cast

from .service_call import ServiceCallPlan

PROACTIVE_PROOF_ID_FIELD = "lifecycle_id"
PROACTIVE_PROOF_TOKEN_FIELD = "lifecycle_token"
SENSITIVE_PROACTIVE_ACTIONS = frozenset(
    {("lock", "unlock"), ("alarm_control_panel", "alarm_disarm")}
)


class ProactiveOrigin(StrEnum):
    PUBLIC_SERVICE = "public_service"
    HOMEINTENT_AUTOMATION = "homeintent_automation"
    INTERNAL_RUNTIME = "internal_runtime"


@dataclass(frozen=True, slots=True)
class ProactiveAuthority:
    origin: ProactiveOrigin
    created_by_user_id: str | None
    creator_was_admin: bool
    provenance: str


@dataclass(frozen=True, slots=True)
class VerifiedProactiveProof:
    automation_id: str
    owner_user_id: str | None
    owner_was_admin: bool
    action_fingerprint: str


def public_authority(
    user_id: str | None, *, is_admin: bool
) -> ProactiveAuthority:
    return ProactiveAuthority(
        ProactiveOrigin.PUBLIC_SERVICE,
        user_id,
        is_admin,
        "authenticated_admin" if is_admin else "unprivileged_public_call",
    )


def internal_runtime_authority() -> ProactiveAuthority:
    """Authority for calls made directly by entry-owned runtime code."""

    return ProactiveAuthority(
        ProactiveOrigin.INTERNAL_RUNTIME,
        None,
        True,
        "entry_owned_runtime",
    )


def automation_authority(proof: VerifiedProactiveProof) -> ProactiveAuthority:
    return ProactiveAuthority(
        ProactiveOrigin.HOMEINTENT_AUTOMATION,
        proof.owner_user_id,
        proof.owner_was_admin,
        f"automation:{proof.automation_id}",
    )


def proactive_action_fingerprint(data: Mapping[str, object]) -> str:
    """Canonical digest of the action and exact targets, excluding its proof."""

    action = {
        "domain": data.get("action_domain"),
        "service": data.get("action_service"),
        "entity_ids": _canonical_entity_ids(data.get("action_entity_id")),
        "data": _canonical_action_data(data.get("action_data", {})),
        "mode": str(data.get("mode", "inform")).casefold(),
        "rule_id": data.get("rule_id"),
    }
    encoded = json.dumps(
        action, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def plan_fingerprint(plan: ServiceCallPlan, *, mode: str, rule_id: str) -> str:
    return proactive_action_fingerprint(
        {
            "action_domain": plan.domain,
            "action_service": plan.service,
            "action_entity_id": plan.entity_id,
            "action_data": dict(plan.data),
            "mode": mode,
            "rule_id": rule_id,
        }
    )


def proactive_message_fingerprint(message: str) -> str:
    """Bind the exact visible ASK text without persisting secret material."""
    return "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()


def exact_proactive_action_count(
    value: object,
    *,
    automation_id: str,
    token: str,
    action_fingerprint: str,
) -> int:
    """Count exact proof/action bindings embedded in one automation."""

    matches = 0

    def visit(child: object) -> None:
        nonlocal matches
        if isinstance(child, dict):
            typed_child = cast(dict[object, object], child)
            action = typed_child.get("action")
            raw_data = typed_child.get("data")
            if (
                action in {"homeintent.proactive_message", "ha_nlu.proactive_message"}
                and isinstance(raw_data, Mapping)
            ):
                data = cast(Mapping[str, object], raw_data)
                if (
                    data.get("rule_id") == automation_id
                    and data.get(PROACTIVE_PROOF_ID_FIELD) == automation_id
                    and data.get(PROACTIVE_PROOF_TOKEN_FIELD) == token
                    and proactive_action_fingerprint(data) == action_fingerprint
                ):
                    matches += 1
            for nested in typed_child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in cast(list[object], child):
                visit(nested)

    visit(value)
    return matches


def describe_action(
    plan: ServiceCallPlan, entity_names: Mapping[str, str]
) -> str:
    """Render the stored action, real targets and parameters for ASK output."""

    labels = {
        ("homeassistant", "turn_on"): "einschalten",
        ("light", "turn_on"): "einschalten",
        ("homeassistant", "turn_off"): "ausschalten",
        ("homeassistant", "toggle"): "umschalten",
        ("cover", "open_cover"): "öffnen",
        ("cover", "close_cover"): "schließen",
        ("cover", "stop_cover"): "stoppen",
        ("lock", "lock"): "verriegeln",
        ("lock", "unlock"): "aufschließen",
        ("alarm_control_panel", "alarm_disarm"): "Alarmanlage unscharf schalten",
    }
    raw_ids = plan.entity_id
    ids = (raw_ids,) if isinstance(raw_ids, str) else tuple(raw_ids)
    names = ", ".join(entity_names.get(item, item) for item in ids)
    action = labels.get((plan.domain, plan.service), f"{plan.domain}.{plan.service}")
    parameters = ", ".join(
        f"{key}={value}" for key, value in sorted(plan.data.items())
    )
    suffix = f"; Parameter: {parameters}" if parameters else ""
    warning = (
        " Sicherheitskritische Aktion."
        if (plan.domain, plan.service) in SENSITIVE_PROACTIVE_ACTIONS
        else ""
    )
    return f"Tatsächliche Aktion: {names} {action}{suffix}.{warning}".strip()


def _canonical_entity_ids(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in cast(Sequence[object], value))
    return ()


def _canonical_action_data(value: object) -> object:
    if isinstance(value, str):
        if not value:
            return {}
        try:
            decoded: object = cast(object, json.loads(value))
        except json.JSONDecodeError:
            return value
        return decoded
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, object], value))
    return value
