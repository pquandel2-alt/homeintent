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

7.9.2 A3: a monitor or automation can belong to the household
(``HOUSEHOLD_OWNER``). With the admin option "Sprachgeräte im Haus sprechen
für den Haushalt" (off by default) a voice device of the house without a
signed-in user may manage those shared ones - never personal ones. Every
signed-in member of the household may manage shared ones, too. Marking as
shared ("für alle", "gemeinsam") is for the owner or an administrator.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Mapping

from .const import CONF_HOUSEHOLD_VOICE_DEVICES
from .security_control import conversation_user_id, user_is_admin

__all__ = (
    "HOUSEHOLD_OWNER",
    "async_management_refusal",
    "async_owner_name",
    "household_voice",
    "may_manage",
    "may_share",
    "shared_request",
    "strip_shared_marker",
    "begin_shared_turn",
    "turn_is_shared",
    "mark_shared_turn",
    "shared_turn_text",
)

# The owner of shared monitors and automations (7.9.2 A3).
HOUSEHOLD_OWNER = "household"

# "für uns alle", "für alle", "gemeinsam", "für den (ganzen) Haushalt",
# "für die ganze Familie" - a construction, the request is for everyone.
_SHARED_RE = re.compile(
    r"[,]?\s*\b(?:für\s+(?:uns\s+)?alle|fuer\s+(?:uns\s+)?alle|gemeinsam|"
    r"für\s+(?:den\s+(?:ganzen\s+)?haushalt|die\s+ganze\s+familie|das\s+ganze\s+haus))\b",
    re.IGNORECASE,
)


def may_manage(
    owner_user_id: str | None, actor_user_id: str | None, is_admin: bool, *, household_voice: bool = False
) -> bool:
    """The one rule: owner or administrator; no owner -> administrator;
    shared -> every member, and a house voice device if the admin allowed it."""
    if is_admin:
        return True
    if owner_user_id == HOUSEHOLD_OWNER:
        return actor_user_id is not None or household_voice
    return owner_user_id is not None and actor_user_id is not None and owner_user_id == actor_user_id


def may_share(owner_user_id: str | None, actor_user_id: str | None, is_admin: bool) -> bool:
    """Marking as shared: only the owner or an administrator."""
    if is_admin:
        return True
    return owner_user_id is not None and actor_user_id is not None and owner_user_id == actor_user_id


def shared_request(text: str) -> bool:
    return _SHARED_RE.search(text) is not None


def strip_shared_marker(text: str) -> str:
    """The request without "für uns alle"/"gemeinsam" (it names the owner)."""
    return re.sub(r"\s{2,}", " ", _SHARED_RE.sub("", text)).strip()


def household_voice(hass: Any, options: Mapping[str, object], user_input: Any) -> bool:
    """A voice device of the house without a signed-in user, with the admin
    option on (7.9.2 A3). Text chat without a device never is."""
    if not options.get(CONF_HOUSEHOLD_VOICE_DEVICES, False):
        return False
    if conversation_user_id(user_input) is not None:
        return False
    satellite_id = getattr(user_input, "satellite_id", None)
    device_id = getattr(user_input, "device_id", None)
    if isinstance(satellite_id, str) and satellite_id:
        states = getattr(hass, "states", None)
        if states is not None and states.get(satellite_id) is not None:
            return True
    if isinstance(device_id, str) and device_id:
        try:
            from homeassistant.helpers import device_registry as dr

            return dr.async_get(hass).async_get(device_id) is not None
        except Exception:  # noqa: BLE001 - an unknown device is not the house's
            return False
    return False


async def async_owner_name(hass: Any, owner_user_id: str | None) -> str | None:
    """The owner's display name in Home Assistant, ``None`` if unknown."""
    if owner_user_id is None:
        return None
    if owner_user_id == HOUSEHOLD_OWNER:
        return "der Haushalt"
    getter = getattr(getattr(hass, "auth", None), "async_get_user", None)
    if getter is None:
        return None
    user = await getter(owner_user_id)
    name = getattr(user, "name", None) if user is not None else None
    return str(name).strip() if name else None


async def async_management_refusal(
    hass: Any,
    user_input: Any,
    owner_user_id: str | None,
    noun: str = "Automation",
    options: Mapping[str, object] | None = None,
) -> str | None:
    """``None`` when the speaker may manage it, else the honest answer."""
    is_admin = await user_is_admin(hass, user_input)
    actor = conversation_user_id(user_input)
    voice = household_voice(hass, options or {}, user_input)
    if may_manage(owner_user_id, actor, is_admin, household_voice=voice):
        return None
    if owner_user_id == HOUSEHOLD_OWNER:
        return (
            f"Diese {noun} gehört dem ganzen Haushalt. Über dieses Gerät weiß ich nicht, wer "
            "spricht; ein Administrator kann erlauben, dass Sprachgeräte im Haus für den "
            "Haushalt sprechen."
        )
    if owner_user_id is None:
        return (
            f"Für diese {noun} ist kein Eigentümer eingetragen; ändern kann sie nur ein "
            "Administrator."
        )
    name = await async_owner_name(hass, owner_user_id) or "ein anderer Benutzer"
    if voice:
        return (
            f"Diese {noun} hat {name} für sich angelegt; über ein Sprachgerät ohne Anmeldung "
            "kann ich nur gemeinsame Überwachungen und Automationen ändern."
        )
    if actor is None:
        return (
            f"Diese {noun} hat {name} angelegt. Über dieses Gerät weiß ich nicht, wer spricht; "
            f"ändern kann sie nur {name} oder ein Administrator."
        )
    return f"Diese {noun} hat {name} angelegt; ändern kann sie nur {name} oder ein Administrator."


# The raw text of a turn that asked for a shared monitor/automation
# ("… für uns alle"); ``None`` otherwise (7.9.2 A3).
_SHARED_TURN: ContextVar[str | None] = ContextVar("homeintent_shared_turn", default=None)
_MONITORING_CUE_RE = re.compile(
    r"\b(?:wenn|sobald|falls|jeden|jede|jedes|täglich|überwach\w*|ueberwach\w*|beobacht\w*|"
    r"meld\w*|benachrichtig\w*|warn\w*|erinner\w*|bescheid|ping\w*)\b",
    re.IGNORECASE,
)


def begin_shared_turn(user_input: Any) -> Any:
    """A monitoring/automation request "für uns alle" is read without the
    marker; the turn remembers that it is shared. Managing an existing
    monitor ("Mach die Fensterüberwachung für alle") keeps its words."""
    text = getattr(user_input, "text", "") or ""
    if not shared_request(text) or not _MONITORING_CUE_RE.search(text):
        _SHARED_TURN.set(None)
        return user_input
    from .monitoring_management import MonitoringOperation, parse_monitoring_management

    managed = parse_monitoring_management(text)
    if managed is not None and managed.operation is MonitoringOperation.SHARE:
        _SHARED_TURN.set(None)
        return user_input
    _SHARED_TURN.set(text)
    return replace(user_input, text=strip_shared_marker(text))


def shared_turn_text() -> str | None:
    """The raw text of the running turn if it asked for "für uns alle"."""
    return _SHARED_TURN.get()


def turn_is_shared(hass: Any, options: Mapping[str, object], user_input: Any) -> bool:
    """What this turn creates belongs to the household: asked "für uns
    alle", or spoken on a house voice device without a user (option on)."""
    return shared_turn_text() is not None or household_voice(hass, options, user_input)


def mark_shared_turn(text: str) -> None:
    """Continue a shared request in a follow-up turn ("Überwache die Haustür
    für uns alle." -> "Wenn sie offen ist.")."""
    _SHARED_TURN.set(text)
