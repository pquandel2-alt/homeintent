"""Authenticated test users (7.9.1 A2).

Managing an automation (pause, switch, edit, delete) needs its owner or an
administrator. Tests that exercise the management *mechanics* run as an
administrator; the ownership rule itself is tested in
``test_automation_ownership_791.py``.
"""

from __future__ import annotations

import types
from typing import Any

USERS = {
    "admin": types.SimpleNamespace(id="admin", name="Philipp", is_admin=True),
    "anna": types.SimpleNamespace(id="anna", name="Anna", is_admin=False),
}


def install_users(hass: Any) -> None:
    async def get_user(user_id: str) -> Any:
        return USERS.get(user_id)

    hass.auth = types.SimpleNamespace(async_get_user=get_user)


def as_user(user_id: str | None) -> Any:
    return types.SimpleNamespace(user_id=user_id) if user_id else None
