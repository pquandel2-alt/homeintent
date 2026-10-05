"""Who may manage an automation or a HomeIntent monitor (7.9.1 A2).

Every automation HomeIntent writes records the Home Assistant user who
confirmed it (``AutomationMetadata.owner_user_id``); a HomeIntent-run
monitor records it in its goal provenance (``GoalProvenance.user_id``).
Pausing, switching off or on, editing and deleting is allowed only to that
owner or to an administrator. Everyone else gets an honest answer naming
the owner. Automations without a recorded owner - written before 7.9.1 or
made by hand in Home Assistant - only an administrator manages; nothing is
rewritten, so the migration loses no data.

A voice turn without an authenticated user is neither owner nor
administrator: a monitor that guards the house must not be switched off by
whoever stands next to a speaker.
"""

from __future__ import annotations

from typing import Any

from .security_control import conversation_user_id, user_is_admin

__all__ = ("async_management_refusal", "async_owner_name", "may_manage")


def may_manage(owner_user_id: str | None, actor_user_id: str | None, is_admin: bool) -> bool:
    """The one rule: owner or administrator; no owner -> administrator."""
    if is_admin:
        return True
    return owner_user_id is not None and actor_user_id is not None and owner_user_id == actor_user_id


async def async_owner_name(hass: Any, owner_user_id: str | None) -> str | None:
    """The owner's display name in Home Assistant, ``None`` if unknown."""
    if owner_user_id is None:
        return None
    getter = getattr(getattr(hass, "auth", None), "async_get_user", None)
    if getter is None:
        return None
    user = await getter(owner_user_id)
    name = getattr(user, "name", None) if user is not None else None
    return str(name).strip() if name else None


async def async_management_refusal(
    hass: Any, user_input: Any, owner_user_id: str | None, noun: str = "Automation"
) -> str | None:
    """``None`` when the speaker may manage it, else the honest answer."""
    is_admin = await user_is_admin(hass, user_input)
    actor = conversation_user_id(user_input)
    if may_manage(owner_user_id, actor, is_admin):
        return None
    if owner_user_id is None:
        return (
            f"Für diese {noun} ist kein Eigentümer eingetragen; ändern kann sie nur ein "
            "Administrator."
        )
    name = await async_owner_name(hass, owner_user_id) or "ein anderer Benutzer"
    if actor is None:
        return (
            f"Diese {noun} hat {name} angelegt. Über dieses Gerät weiß ich nicht, wer spricht; "
            f"ändern kann sie nur {name} oder ein Administrator."
        )
    return f"Diese {noun} hat {name} angelegt; ändern kann sie nur {name} oder ein Administrator."
