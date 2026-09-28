"""The one authority for "who receives this notification".

Immediate spoken notifications, delayed notifications, notification
automations and reminders all resolve their recipient here, so "mich" means
the same thing on every path:

1. an explicit, confirmed ``UserContextStore`` binding of the authenticated
   Home Assistant user wins;
2. without such a binding, exactly one configured HomeIntent push target is
   a safe fallback - unless another user explicitly bound that very device;
3. several possible targets -> ambiguity, the user must bind a device;
4. no target -> configuration guidance.

Nothing is ever inferred from a person's friendly name, a notify entity
substring, a device name or the voice satellite.  ``uns`` resolves only
through the explicitly confirmed household.  A resolution never falls back to
``persistent_notification`` and never broadcasts.

Home-Assistant-free and strictly typed; the HA boundary lives in
``agent_delivery.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable, Mapping, Sequence, cast

from .entities import normalize_for_compare
from .const import (
    AGENT_CHANNEL_PUSH,
    CONF_AGENT_DELIVERY_CHANNELS,
    CONF_AGENT_NOTIFY_TARGETS,
)
from .nlu.action_model import NotificationRecipientKind
from .user_context import (
    BindingStatus,
    NotificationTarget,
    NotificationTargetKind,
    UserContextStore,
)


class NotificationResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    PUSH_DISABLED = "push_disabled"
    NO_TARGET = "no_target"
    AMBIGUOUS = "ambiguous"
    UNKNOWN_USER = "unknown_user"
    HOUSEHOLD_UNBOUND = "household_unbound"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class NotificationTargetResolution:
    status: NotificationResolutionStatus
    targets: tuple[NotificationTarget, ...] = ()
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.status is NotificationResolutionStatus.RESOLVED and bool(self.targets)

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(
            target.target_id for target in self.targets
            if target.kind is not NotificationTargetKind.SERVICE
        )

    @property
    def service_ids(self) -> tuple[str, ...]:
        return tuple(
            target.target_id for target in self.targets
            if target.kind is NotificationTargetKind.SERVICE
        )

    @property
    def label(self) -> str:
        return ", ".join(target.label or "dein Gerät" for target in self.targets)


_FAILURE_TEXT: dict[NotificationResolutionStatus, str] = {
    NotificationResolutionStatus.PUSH_DISABLED: (
        "Push-Benachrichtigungen sind in den HomeIntent-Einstellungen deaktiviert."
    ),
    NotificationResolutionStatus.NO_TARGET: (
        "Ich habe noch kein eindeutiges Push-Ziel für dich. "
        "Bitte hinterlege dein Handy in den HomeIntent-Einstellungen."
    ),
    NotificationResolutionStatus.AMBIGUOUS: (
        "Ich habe mehrere Push-Ziele gefunden. "
        "Bitte ordne dein Gerät deinem HomeIntent-Benutzer zu."
    ),
    NotificationResolutionStatus.UNKNOWN_USER: (
        "Ich kann dich keinem Home-Assistant-Benutzer zuordnen und schicke deshalb "
        "keine Push-Benachrichtigung. Bitte ordne dein Gerät deinem "
        "HomeIntent-Benutzer zu."
    ),
    NotificationResolutionStatus.HOUSEHOLD_UNBOUND: (
        "Für „uns“ ist noch kein bestätigter Haushalt mit eindeutigen Push-Zielen "
        "hinterlegt."
    ),
    NotificationResolutionStatus.UNSUPPORTED: (
        "Diesen Empfänger kann ich noch nicht eindeutig zuordnen."
    ),
}


def resolution_failure_text(resolution: NotificationTargetResolution) -> str:
    """User-facing text for a recipient that could not be resolved."""
    return _FAILURE_TEXT.get(
        resolution.status, _FAILURE_TEXT[NotificationResolutionStatus.NO_TARGET]
    )


def push_channel_enabled(options: Mapping[str, object]) -> bool:
    """Whether the HomeIntent push channel itself is switched on.

    This is independent of proactive situation detection: an explicit user
    request only needs the push channel, not proactive permission.
    """
    raw = options.get(CONF_AGENT_DELIVERY_CHANNELS, (AGENT_CHANNEL_PUSH,))
    if not isinstance(raw, (list, tuple, set)):
        return True
    return AGENT_CHANNEL_PUSH in {str(item) for item in cast(Iterable[object], raw)}


def configured_push_targets(options: Mapping[str, object]) -> tuple[str, ...]:
    """Exact configured notify entities, in configured order, deduplicated."""
    raw = options.get(CONF_AGENT_NOTIFY_TARGETS, ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(
        item for item in cast(Iterable[object], raw)
        if isinstance(item, str) and item.startswith("notify.") and len(item) > 7
    ))


class NotificationTargetResolver:
    """Resolve a semantic recipient into exact notify targets."""

    def __init__(
        self,
        *,
        user_contexts: UserContextStore | None,
        configured_targets: Sequence[str],
        push_enabled: bool = True,
        label_for: Callable[[str], str] | None = None,
        named_targets: Mapping[str, tuple[NotificationTarget, ...]] | None = None,
    ) -> None:
        self._user_contexts = user_contexts
        self._configured = tuple(dict.fromkeys(configured_targets))
        self._push_enabled = push_enabled
        self._label_for = label_for
        # Normalized person name -> that person's own bound devices.
        self._named = dict(named_targets or {})

    @classmethod
    def from_options(
        cls,
        options: Mapping[str, object],
        user_contexts: UserContextStore | None,
        label_for: Callable[[str], str] | None = None,
        named_targets: Mapping[str, tuple[NotificationTarget, ...]] | None = None,
    ) -> NotificationTargetResolver:
        return cls(
            user_contexts=user_contexts,
            configured_targets=configured_push_targets(options),
            push_enabled=push_channel_enabled(options),
            label_for=label_for,
            named_targets=named_targets,
        )

    def resolve(
        self,
        kind: NotificationRecipientKind,
        user_id: str | None,
        *,
        name: str | None = None,
    ) -> NotificationTargetResolution:
        if not self._push_enabled:
            return NotificationTargetResolution(
                NotificationResolutionStatus.PUSH_DISABLED, reason="push_channel_disabled"
            )
        if kind is NotificationRecipientKind.CURRENT_USER:
            return self._resolve_current_user(user_id)
        if kind is NotificationRecipientKind.HOUSEHOLD:
            return self._resolve_household()
        if kind is NotificationRecipientKind.EXPLICIT_TARGET and name:
            targets = self._named.get(normalize_for_compare(name), ())
            if not targets:
                return NotificationTargetResolution(
                    NotificationResolutionStatus.NO_TARGET, reason="named_recipient_unbound"
                )
            return NotificationTargetResolution(
                NotificationResolutionStatus.RESOLVED,
                tuple(self._labelled(target) for target in targets),
                "named_person_binding",
            )
        return NotificationTargetResolution(
            NotificationResolutionStatus.UNSUPPORTED, reason="explicit_target_not_semantic"
        )

    def _resolve_current_user(self, user_id: str | None) -> NotificationTargetResolution:
        store = self._user_contexts
        owners: dict[str, tuple[str, ...]] = (
            store.notification_target_owners() if store is not None else {}
        )
        if store is not None and user_id is not None:
            binding = store.resolve_user_notification_targets(user_id)
            if binding.status is BindingStatus.RESOLVED:
                return NotificationTargetResolution(
                    NotificationResolutionStatus.RESOLVED,
                    tuple(self._labelled(target) for target in binding.targets),
                    "user_binding",
                )
            if binding.status is BindingStatus.AMBIGUOUS:
                return NotificationTargetResolution(
                    NotificationResolutionStatus.AMBIGUOUS,
                    reason="user_binding_ambiguous",
                )
        if user_id is None and owners:
            # Several people bound devices; an anonymous caller cannot be one
            # of them safely.
            return NotificationTargetResolution(
                NotificationResolutionStatus.UNKNOWN_USER, reason="caller_identity_missing"
            )
        # Only a household with exactly one configured phone has an
        # unambiguous fallback; excluding other people's devices from a longer
        # list would still be a guess about who owns the remainder.
        if len(self._configured) > 1:
            return NotificationTargetResolution(
                NotificationResolutionStatus.AMBIGUOUS, reason="multiple_configured_targets"
            )
        if not self._configured:
            return NotificationTargetResolution(
                NotificationResolutionStatus.NO_TARGET, reason="no_configured_target"
            )
        only = self._configured[0]
        if any(owner != user_id for owner in owners.get(only, ())):
            # A device explicitly bound to another user is never "mine".
            return NotificationTargetResolution(
                NotificationResolutionStatus.NO_TARGET,
                reason="configured_target_bound_to_other_user",
            )
        return NotificationTargetResolution(
            NotificationResolutionStatus.RESOLVED,
            (self._labelled(NotificationTarget(only, NotificationTargetKind.ENTITY)),),
            "single_configured_target",
        )

    def _resolve_household(self) -> NotificationTargetResolution:
        store = self._user_contexts
        persons = store.household.person_entity_ids if store is not None else ()
        if store is None or not persons:
            return NotificationTargetResolution(
                NotificationResolutionStatus.HOUSEHOLD_UNBOUND, reason="household_missing"
            )
        targets: list[NotificationTarget] = []
        for person in persons:
            binding = store.resolve_notification_targets(person)
            if binding.status is not BindingStatus.RESOLVED:
                return NotificationTargetResolution(
                    NotificationResolutionStatus.HOUSEHOLD_UNBOUND,
                    reason=f"household_member_{binding.status.value}",
                )
            targets.extend(self._labelled(target) for target in binding.targets)
        unique = tuple({target.target_id: target for target in targets}.values())
        return NotificationTargetResolution(
            NotificationResolutionStatus.RESOLVED, unique, "household_binding"
        )

    def _labelled(self, target: NotificationTarget) -> NotificationTarget:
        if target.label or self._label_for is None:
            return target
        return NotificationTarget(
            target.target_id,
            target.kind,
            target.channel,
            self._label_for(target.target_id),
            target.preferred,
        )


__all__ = (
    "named_notification_targets",
    "NotificationResolutionStatus",
    "NotificationTargetResolution",
    "NotificationTargetResolver",
    "configured_push_targets",
    "push_channel_enabled",
    "resolution_failure_text",
)


def named_notification_targets(
    entities: Iterable[object],
    user_contexts: UserContextStore | None,
) -> dict[str, tuple[NotificationTarget, ...]]:
    """Person name -> that person's own confirmed push devices.

    A spoken name ("Schreib Anna ...") reaches exactly the devices bound to
    the ``person`` of that name.  Without a binding, a single notify entity
    whose name contains the person's name ("Handy Anna") is used; two such
    entities stay unresolved instead of guessing.
    """
    named: dict[str, tuple[NotificationTarget, ...]] = {}
    items = list(entities)
    notify = [
        item for item in items if getattr(item, "domain", None) == "notify"
    ]
    for item in items:
        if getattr(item, "domain", None) != "person":
            continue
        name = normalize_for_compare(str(getattr(item, "friendly_name", "")))
        if not name:
            continue
        targets: tuple[NotificationTarget, ...] = ()
        if user_contexts is not None:
            binding = user_contexts.resolve_notification_targets(str(getattr(item, "entity_id")))
            if binding.status is BindingStatus.RESOLVED:
                targets = tuple(binding.targets)
        if not targets:
            owned = [
                entity for entity in notify
                if name in normalize_for_compare(str(getattr(entity, "friendly_name", ""))).split()
            ]
            if len(owned) == 1:
                targets = (
                    NotificationTarget(str(getattr(owned[0], "entity_id")), NotificationTargetKind.ENTITY),
                )
        if targets:
            named[name] = targets
            first = name.split()[0]
            named.setdefault(first, targets)
    return named
