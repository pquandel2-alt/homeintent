"""V12 StandingPermission and the central AutoExecutionPolicy (NEVER_AUTO).

SAFE CLASSIFICATION != STANDING PERMISSION != EXECUTION AUTHORITY.

A StandingPermission only records prior, explicit, previewed and confirmed
user consent for one closed operator on explicit entity ids under explicit
conditions.  It never bypasses V10: the proactive runner still materializes a
fresh plan, runs the Validator, re-evaluates ExecutionPolicy *without* a
confirmation flag, reserves the ExecutionCoordinator and verifies the effect.

``AutoExecutionPolicy`` owns the NEVER_AUTO rules.  Locks, garage doors,
gates, doors, alarm panels, sirens, valves, stove/oven-like switches and any
unknown actuator can never be executed without an interactive answer, no
matter what a permission says.
"""

from __future__ import annotations

import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence, cast

from .entities import EntitySnapshot, normalize_for_compare
from .proactive_model import (
    AutoExecutionDecision,
    AutoOperator,
    PermissionCondition,
    ProactiveSituation,
    SituationKind,
    SituationState,
    StandingPermission,
)


SCHEMA_VERSION = 1
MAX_PERMISSIONS = 32
DEFAULT_PERMISSION_TTL = timedelta(days=180)

OPERATOR_DOMAIN: Mapping[AutoOperator, str] = {
    AutoOperator.LIGHT_TURN_OFF: "light",
    AutoOperator.SWITCH_TURN_OFF: "switch",
    AutoOperator.FAN_TURN_OFF: "fan",
}
OPERATOR_DESIRED_STATE: Mapping[AutoOperator, str] = {
    AutoOperator.LIGHT_TURN_OFF: "off",
    AutoOperator.SWITCH_TURN_OFF: "off",
    AutoOperator.FAN_TURN_OFF: "off",
}
# Situation kinds for which unattended execution may ever be considered.
AUTO_ELIGIBLE_KINDS = frozenset({SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING})

NEVER_AUTO_DOMAINS = frozenset({
    "lock", "alarm_control_panel", "siren", "valve", "cover", "climate",
    "water_heater", "button", "script", "automation", "vacuum", "lawn_mower",
    "camera", "humidifier",
})
NEVER_AUTO_DEVICE_CLASSES = frozenset({"garage", "garage_door", "gate", "door", "lock"})
# Conservative blocklist on stove/oven-like or security switches.  This can
# only *forbid* automation; a name never enables anything.
_NEVER_AUTO_NAME_HEADS = (
    "herd", "ofen", "kochfeld", "grill", "buegeleisen", "heizluefter",
    "alarm", "sirene", "garage", "tor", "schloss", "tuer", "safe",
)


class AutoExecutionPolicy:
    """Decides only whether unattended execution may even be *attempted*."""

    def never_auto_reason(self, entity: EntitySnapshot | None, operator: AutoOperator | None) -> str | None:
        if entity is None:
            return "unknown_actuator"
        if entity.domain in NEVER_AUTO_DOMAINS:
            return f"never_auto_domain:{entity.domain}"
        if (entity.device_class or "").casefold() in NEVER_AUTO_DEVICE_CLASSES:
            return f"never_auto_device_class:{entity.device_class}"
        if operator is None or OPERATOR_DOMAIN.get(operator) != entity.domain:
            return "operator_not_auto_eligible"
        name = normalize_for_compare(f"{entity.friendly_name} {entity.entity_id.partition('.')[2]}")
        tokens = re.split(r"[^a-z0-9]+", name)
        if any(token.endswith(head) or token.startswith(head) for token in tokens for head in _NEVER_AUTO_NAME_HEADS if token):
            return "never_auto_dangerous_or_security_name"
        return None

    def evaluate(
        self,
        permission: StandingPermission | None,
        *,
        enabled: bool,
        situation: ProactiveSituation,
        owner_known: bool,
        entities: Mapping[str, EntitySnapshot],
        nobody_home: bool | None,
        now: datetime,
        attempts_today: int,
    ) -> AutoExecutionDecision:
        """Checks 1-8 of the V12 contract; V10 enforces 9-13 afterwards."""
        reasons: list[str] = []
        if not enabled:
            return AutoExecutionDecision(False, ("standing_permissions_disabled",))
        if permission is None:
            return AutoExecutionDecision(False, ("no_exact_standing_permission",))
        pid = permission.permission_id
        if not permission.confirmed:
            return AutoExecutionDecision(False, ("permission_not_confirmed",), pid)
        if permission.revoked:
            return AutoExecutionDecision(False, ("permission_revoked",), pid)
        if now >= permission.expires_at:
            return AutoExecutionDecision(False, ("permission_expired",), pid)
        if not owner_known:
            return AutoExecutionDecision(False, ("permission_owner_not_in_scope",), pid)
        if situation.kind not in AUTO_ELIGIBLE_KINDS or situation.kind is not permission.situation_kind:
            return AutoExecutionDecision(False, ("situation_kind_mismatch",), pid)
        if situation.state in {SituationState.RESOLVED, SituationState.EXPIRED}:
            return AutoExecutionDecision(False, ("situation_no_longer_active",), pid)
        if permission.area_id is not None and permission.area_id != situation.area_id:
            return AutoExecutionDecision(False, ("area_mismatch",), pid)
        # The daily budget counts *attempts* (every automatic V10 run, verified
        # or not), so failing, rejected or conflicting runs cannot retry
        # without bound.  ``max_executions_per_day`` keeps its stored name.
        if attempts_today >= permission.max_executions_per_day:
            return AutoExecutionDecision(False, ("daily_attempt_limit",), pid)
        for condition in permission.conditions:
            if condition is PermissionCondition.NOBODY_HOME and nobody_home is not True:
                return AutoExecutionDecision(False, ("condition_nobody_home_not_met",), pid)
        uncovered = [item for item in situation.subject_ids
                     if item not in permission.entity_ids
                     and item in entities and entities[item].state == "on"]
        if uncovered:
            return AutoExecutionDecision(False, ("permission_does_not_cover_all_targets",), pid)
        targets = auto_targets(permission, situation, entities)
        if not targets:
            return AutoExecutionDecision(False, ("current_state_no_longer_matches",), pid)
        for entity_id in targets:
            blocked = self.never_auto_reason(entities.get(entity_id), permission.operator)
            if blocked is not None:
                return AutoExecutionDecision(False, (blocked,), pid)
        reasons.append("exact_confirmed_permission")
        return AutoExecutionDecision(True, tuple(reasons), pid)


def auto_targets(
    permission: StandingPermission,
    situation: ProactiveSituation,
    entities: Mapping[str, EntitySnapshot],
) -> tuple[str, ...]:
    """Fresh-state intersection: permitted, part of the situation, still on."""
    return tuple(
        entity_id for entity_id in permission.entity_ids
        if entity_id in situation.subject_ids
        and entity_id in entities
        and entities[entity_id].state == "on"
    )


_HISTORY_LIMIT = 32


def _append(target: dict[str, list[datetime]], permission_id: str, at: datetime) -> None:
    history = [item for item in target.get(permission_id, []) if at - item < timedelta(days=1)]
    history.append(at)
    target[permission_id] = history[-_HISTORY_LIMIT:]


def _within_day(source: Mapping[str, list[datetime]], permission_id: str, now: datetime) -> int:
    return sum(1 for item in source.get(permission_id, ()) if now - item < timedelta(days=1))


def _stamp_map(raw: object, known: Mapping[str, object]) -> dict[str, list[datetime]]:
    result: dict[str, list[datetime]] = {}
    if not isinstance(raw, Mapping):
        return result
    for key, values in cast(Mapping[object, object], raw).items():
        if isinstance(key, str) and key in known and isinstance(values, list):
            result[key] = [
                stamp for value in cast(Sequence[object], values)
                if (stamp := _aware(value)) is not None
            ][-_HISTORY_LIMIT:]
    return result


class StandingPermissionStore:
    """Bounded, fail-closed permission storage.  Nothing exists by default."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, StandingPermission] = OrderedDict()
        # Stored under the legacy key ``executions``: 7.0.0 recorded every
        # automatic run there, i.e. attempts.  Verified runs are tracked apart.
        self._attempts: dict[str, list[datetime]] = {}
        self._verified: dict[str, list[datetime]] = {}

    def add(self, permission: StandingPermission) -> StandingPermission:
        """Store *permission*; an identical active one is renewed instead.

        Confirming the same standing instruction again (same owner,
        situation, operator, area, entities and conditions) must not create
        a second record: ``matching()`` deliberately refuses to choose
        between several candidates, so duplicates silently disabled
        automatic execution (F7). Returns the stored permission.
        """
        if not permission.confirmed:
            raise ValueError("Eine Daueranweisung muss ausdrücklich bestätigt sein")
        if permission.situation_kind not in AUTO_ELIGIBLE_KINDS:
            raise ValueError("Für diese Situation sind keine Daueranweisungen erlaubt")
        if not permission.entity_ids or any("*" in item for item in permission.entity_ids):
            raise ValueError("Daueranweisungen benötigen explizite Entitäten ohne Platzhalter")
        existing = next(
            (
                item for item in self._items.values()
                if not item.revoked and item.confirmed
                and _same_instruction(item, permission)
            ),
            None,
        )
        if existing is not None:
            renewed = replace(
                existing,
                expires_at=max(existing.expires_at, permission.expires_at),
                description=permission.description or existing.description,
            )
            self._items[existing.permission_id] = renewed
            self._items.move_to_end(existing.permission_id)
            return renewed
        if len(self._items) >= MAX_PERMISSIONS and permission.permission_id not in self._items:
            # Drop the oldest revoked/expired first, else refuse to grow.
            victim = next((key for key, item in self._items.items() if item.revoked), None)
            if victim is None:
                raise ValueError("Es sind bereits zu viele Daueranweisungen gespeichert")
            del self._items[victim]
        self._items[permission.permission_id] = permission
        return permission

    def revoke(self, permission_id: str) -> StandingPermission | None:
        current = self._items.get(permission_id)
        if current is None or current.revoked:
            return None
        updated = replace(current, revoked=True)
        self._items[permission_id] = updated
        return updated

    def revoke_all(self, owner_user_id: str | None = None) -> int:
        count = 0
        for key, item in tuple(self._items.items()):
            if item.revoked or (owner_user_id is not None and item.owner_user_id != owner_user_id):
                continue
            self._items[key] = replace(item, revoked=True)
            count += 1
        return count

    def active(self, now: datetime) -> tuple[StandingPermission, ...]:
        return tuple(
            item for item in self._items.values()
            if item.confirmed and not item.revoked and item.expires_at > now
        )

    def all(self) -> tuple[StandingPermission, ...]:
        return tuple(self._items.values())

    def matching(self, situation: ProactiveSituation, now: datetime) -> StandingPermission | None:
        """The one exact permission for this situation, or None if 0 or >1."""
        candidates = [
            item for item in self.active(now)
            if item.situation_kind is situation.kind
            and (item.area_id is None or item.area_id == situation.area_id)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def record_attempt(self, permission_id: str, at: datetime, *, verified: bool) -> None:
        """Record one automatic V10 run; ``verified`` only for a verified effect."""
        _append(self._attempts, permission_id, at)
        if verified:
            _append(self._verified, permission_id, at)

    def attempts_today(self, permission_id: str, now: datetime) -> int:
        return _within_day(self._attempts, permission_id, now)

    def verified_executions_today(self, permission_id: str, now: datetime) -> int:
        return _within_day(self._verified, permission_id, now)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "permissions": [_permission_dict(item) for item in self._items.values()],
            "executions": self._stamps(self._attempts),
            "verified_executions": self._stamps(self._verified),
        }

    def _stamps(self, source: dict[str, list[datetime]]) -> dict[str, list[str]]:
        return {
            key: [item.isoformat() for item in value]
            for key, value in source.items() if key in self._items
        }

    @classmethod
    def from_dict(cls, raw: object) -> "StandingPermissionStore":
        """Unknown schema or any malformed record yields *no* permission."""
        store = cls()
        if not isinstance(raw, Mapping):
            return store
        document = cast(Mapping[str, object], raw)
        if document.get("schema_version") != SCHEMA_VERSION:
            return store
        permissions = document.get("permissions")
        if not isinstance(permissions, list):
            return store
        merged_into: dict[str, str] = {}
        for item in cast(Sequence[object], permissions)[-MAX_PERMISSIONS:]:
            parsed = _permission_from(item)
            if parsed is None:
                continue
            # 7.1.2 stored every re-confirmation as a new record; merge such
            # duplicates into the oldest one, keeping the latest expiry.
            twin = next(
                (
                    kept for kept in store._items.values()
                    if not parsed.revoked and not kept.revoked
                    and _same_instruction(kept, parsed)
                ),
                None,
            )
            if twin is not None:
                store._items[twin.permission_id] = replace(
                    twin, expires_at=max(twin.expires_at, parsed.expires_at)
                )
                merged_into[parsed.permission_id] = twin.permission_id
                continue
            store._items[parsed.permission_id] = parsed
        known: dict[str, object] = {**store._items, **{key: True for key in merged_into}}
        store._attempts = _merge_stamps(_stamp_map(document.get("executions"), known), merged_into)
        store._verified = _merge_stamps(
            _stamp_map(document.get("verified_executions"), known), merged_into
        )
        return store


def _same_instruction(left: StandingPermission, right: StandingPermission) -> bool:
    return (
        left.owner_user_id == right.owner_user_id
        and left.situation_kind is right.situation_kind
        and left.operator is right.operator
        and left.area_id == right.area_id
        and frozenset(left.entity_ids) == frozenset(right.entity_ids)
        and frozenset(left.conditions) == frozenset(right.conditions)
    )


def _merge_stamps(
    stamps: dict[str, list[datetime]], merged_into: Mapping[str, str]
) -> dict[str, list[datetime]]:
    result: dict[str, list[datetime]] = {}
    for key, values in stamps.items():
        target = merged_into.get(key, key)
        result[target] = sorted([*result.get(target, []), *values])[-_HISTORY_LIMIT:]
    return result


def _permission_dict(item: StandingPermission) -> dict[str, object]:
    return {
        "permission_id": item.permission_id,
        "owner_user_id": item.owner_user_id,
        "situation_kind": item.situation_kind.value,
        "operator": item.operator.value,
        "entity_ids": list(item.entity_ids),
        "area_id": item.area_id,
        "conditions": [value.value for value in item.conditions],
        "created_at": item.created_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "confirmed": item.confirmed,
        "revoked": item.revoked,
        "description": item.description,
        "max_executions_per_day": item.max_executions_per_day,
    }


def _permission_from(raw: object) -> StandingPermission | None:
    if not isinstance(raw, Mapping):
        return None
    value = cast(Mapping[str, object], raw)
    try:
        permission_id = value["permission_id"]
        owner = value["owner_user_id"]
        entity_ids = value["entity_ids"]
        conditions = value["conditions"]
        confirmed = value["confirmed"]
        revoked = value.get("revoked", False)
        if (
            not isinstance(permission_id, str) or not permission_id
            or not isinstance(owner, str) or not owner
            or not isinstance(entity_ids, list) or not entity_ids
            or not isinstance(conditions, list)
            or confirmed is not True
            or not isinstance(revoked, bool)
        ):
            return None
        ids = tuple(item for item in cast(Sequence[object], entity_ids) if isinstance(item, str))
        if len(ids) != len(cast(Sequence[object], entity_ids)) or any(
            "*" in item or "." not in item for item in ids
        ):
            return None
        created = _aware(value["created_at"])
        expires = _aware(value["expires_at"])
        if created is None or expires is None:
            return None
        area = value.get("area_id")
        limit = value.get("max_executions_per_day", 6)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 24:
            return None
        permission = StandingPermission(
            permission_id, owner, SituationKind(str(value["situation_kind"])),
            AutoOperator(str(value["operator"])), ids,
            area if isinstance(area, str) else None,
            tuple(PermissionCondition(str(item)) for item in cast(Sequence[object], conditions)),
            created, expires, True, revoked,
            str(value.get("description", ""))[:300], limit,
        )
    except (KeyError, ValueError, TypeError):
        return None
    if any(OPERATOR_DOMAIN[permission.operator] != item.partition(".")[0] for item in ids):
        return None
    if permission.situation_kind not in AUTO_ELIGIBLE_KINDS:
        return None
    return permission


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# -- German permission request parsing ---------------------------------------

_NOBODY = r"(?:niemand|keiner|keine\s+person)(?:\s+mehr)?\s+(?:zu\s*hause|zuhause|daheim|im\s+haus)"
_PERMISSION_RE = re.compile(
    r"^wenn\s+" + _NOBODY + r"\s+ist\s*,?\s*und\s+"
    r"(?:im|in\s+der|in\s+dem)\s+(?P<area>[a-z0-9 \-]+?)\s+(?:noch\s+)?"
    r"(?:ein\s+|das\s+|die\s+)?(?:licht|lichter|lampe|lampen|beleuchtung)\s+"
    r"(?:noch\s+)?(?:an|eingeschaltet|brennt|brennen)(?:\s+(?:ist|sind))?\s*,?\s*"
    r"(?:dann\s+)?(?:darfst\s+du|du\s+darfst)\s+(?:es|sie|das\s+licht|die\s+lichter|die\s+lampen)\s+"
    r"(?:automatisch\s+|selbststaendig\s+|von\s+selbst\s+)?"
    r"(?:ausschalten|ausmachen|abschalten)\s*[.!]?$"
)
# A permission is consent for *future* situations: it needs a condition
# ("wenn ...") or an explicit standing word.  "Du darfst das Licht
# ausschalten." is an ordinary polite command and stays with V8/V10.
_PERMISSION_INTENT_RE = re.compile(
    r"\b(?:darfst\s+du|du\s+darfst|erlaube\s+ich\s+dir|erlaubnis)\b.*"
    r"\b(?:automatisch|selbststaendig|von\s+selbst|kuenftig|zukuenftig|immer)\b"
    r"|^wenn\b.*\b(?:darfst\s+du|du\s+darfst)\b"
)
# Matched anywhere inside a word: German compounds ("Hauptventil",
# "Garagentor", "Holzofen") must be caught.  Over-matching only refuses.
_NEVER_AUTO_WORDS = re.compile(
    r"(?:garage|tor\b|tore\b|tuer|schloss|entriegel|aufschliess|aufsperr|"
    r"alarm|sirene|herd\b|ofen\b|ventil|kochfeld|grill)"
)


@dataclass(frozen=True)
class PermissionDraft:
    owner_user_id: str
    situation_kind: SituationKind
    operator: AutoOperator
    entity_ids: tuple[str, ...]
    entity_names: tuple[str, ...]
    area_id: str
    area_name: str
    conditions: tuple[PermissionCondition, ...]
    expires_at: datetime

    def to_permission(self, now: datetime) -> StandingPermission:
        return StandingPermission(
            f"perm_{secrets.token_hex(12)}", self.owner_user_id, self.situation_kind,
            self.operator, self.entity_ids, self.area_id, self.conditions, now,
            self.expires_at, True,
            description=f"Licht im Bereich {self.area_name} ausschalten, wenn niemand zu Hause ist",
        )


@dataclass(frozen=True)
class PermissionParse:
    draft: PermissionDraft | None
    error: str | None


def looks_like_permission_request(text: str) -> bool:
    return bool(_PERMISSION_INTENT_RE.search(normalize_for_compare(text)))


def parse_permission_request(
    text: str,
    *,
    owner_user_id: str | None,
    entities: Iterable[EntitySnapshot],
    area_lookup: Mapping[str, str],
    now: datetime,
) -> PermissionParse:
    """Accept exactly one supported full sentence; never a partial rule."""
    normalized = normalize_for_compare(text).rstrip(" .!")
    if _NEVER_AUTO_WORDS.search(normalized):
        return PermissionParse(None, "never_auto")
    match = _PERMISSION_RE.fullmatch(normalized + ".") or _PERMISSION_RE.fullmatch(normalized)
    if match is None:
        return PermissionParse(None, "unsupported")
    if owner_user_id is None:
        return PermissionParse(None, "owner_unknown")
    area_text = match.group("area").strip()
    area_id = area_lookup.get(area_text) or area_lookup.get(normalize_for_compare(area_text))
    if area_id is None:
        return PermissionParse(None, "area_unknown")
    lights = sorted(
        (item for item in entities if item.domain == "light" and item.area_id == area_id),
        key=lambda item: item.entity_id,
    )
    if not lights:
        return PermissionParse(None, "no_lights_in_area")
    policy = AutoExecutionPolicy()
    if any(policy.never_auto_reason(item, AutoOperator.LIGHT_TURN_OFF) for item in lights):
        return PermissionParse(None, "never_auto")
    area_name = next((item.area_name for item in lights if item.area_name), area_id) or area_id
    return PermissionParse(PermissionDraft(
        owner_user_id, SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
        AutoOperator.LIGHT_TURN_OFF,
        tuple(item.entity_id for item in lights),
        tuple(item.friendly_name for item in lights),
        area_id, area_name, (PermissionCondition.NOBODY_HOME,),
        now + DEFAULT_PERMISSION_TTL,
    ), None)


__all__ = (
    "AUTO_ELIGIBLE_KINDS",
    "AutoExecutionPolicy",
    "DEFAULT_PERMISSION_TTL",
    "MAX_PERMISSIONS",
    "NEVER_AUTO_DOMAINS",
    "OPERATOR_DESIRED_STATE",
    "OPERATOR_DOMAIN",
    "PermissionDraft",
    "PermissionParse",
    "StandingPermissionStore",
    "auto_targets",
    "looks_like_permission_request",
    "parse_permission_request",
)
