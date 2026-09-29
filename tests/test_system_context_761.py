"""7.6.1 A1: non-admins create automations again (regression from 7.3.2).

Home Assistant treats ``automation.reload`` as an admin service. Since 7.3.2
every HomeIntent call carried the speaking user's context, so a non-admin
with ``allow_non_admin_automations`` got a raw "Unauthorized". HomeIntent
authorizes its *own* automation management itself; the management calls run
in ``system_context_for_turn()`` (no user, parent = the turn), device writes
stay in the user's context.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import PUSH_OPTIONS, HouseConversation  # noqa: E402
from homeintent.execution_context import (  # noqa: E402
    SYSTEM_CONTEXT_SERVICES,
    UNAUTHORIZED_TEXT,
    current_turn,
    user_facing_error,
)
from homeintent.execution_trace import (  # noqa: E402
    ContextIndex,
    ExecutionTraceStore,
    register_trace,
)

ROOT = Path(__file__).parent.parent / "custom_components" / "homeintent"
ADMINS = {"admin"}
SENTENCE = "Benachrichtige mich, wenn das Küchenfenster aufgeht."


class Unauthorized(Exception):
    """Stand-in for ``homeassistant.exceptions.Unauthorized``."""


def _house(monkeypatch, tmp_path, user="anna", **options):
    house = HouseConversation(
        monkeypatch, user=user, tmp_path=tmp_path, options={**PUSH_OPTIONS, **options}
    )
    calls: list[tuple[str, str, object, object]] = []
    sink_call = house.sink.async_call

    async def ha_call(domain, service, data=None, blocking=False, **kwargs):
        context = kwargs.get("context")
        turn = current_turn()
        calls.append((domain, service, context, turn.context if turn else None))
        # Home Assistant's own admin check for ``automation.reload``.
        if (domain, service) == ("automation", "reload"):
            user_id = getattr(context, "user_id", None)
            if user_id is not None and user_id not in ADMINS:
                raise Unauthorized("Unauthorized")
        return await sink_call(domain, service, data, blocking, **kwargs)

    house.entity.hass.services.async_call = ha_call
    house.calls = calls
    return house


def test_non_admin_with_option_creates_the_push_automation(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(SENTENCE)
    answer = house.say("Ja.").speech
    assert answer == "Automation wurde erstellt."
    automations = house.automations()
    assert len(automations) == 1
    reloads = [call for call in house.calls if call[:2] == ("automation", "reload")]
    assert reloads
    for _, _, context, turn_context in reloads:
        assert context.user_id is None
        assert turn_context is not None and context.parent_id == turn_context.id


def test_the_created_push_automation_reaches_annas_phone(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say(SENTENCE)
    house.say("Ja.")
    (automation,) = house.automations()
    assert automation["triggers"][0]["entity_id"] == "binary_sensor.kuechenfenster"
    asyncio.run(house.sink.async_run_automation_actions(automation["actions"]))
    (_, payload), = house.sink.notify_calls
    assert payload["entity_id"] == ["notify.handy_anna_nachricht"]


def test_non_admin_without_option_is_refused_by_homeintent(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, allow_non_admin_automations=False)
    house.say(SENTENCE)
    assert house.say("Ja.").speech == (
        "Das Erstellen von Automationen ist nur für Administratoren erlaubt."
    )
    assert house.automations() == []
    assert not [call for call in house.calls if call[:2] == ("automation", "reload")]


def test_trace_contains_the_turn(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    store = ExecutionTraceStore()
    register_trace(house.entity.hass, store, ContextIndex())
    house.say(SENTENCE)
    house.say("Ja.")
    (_, _, reload_context, turn_context), *_ = [
        call for call in house.calls if call[:2] == ("automation", "reload")
    ]
    record = store.get(str(turn_context.id))
    assert record is not None
    assert record.utterance == "Ja."
    assert reload_context.parent_id == record.execution_id


def test_home_assistant_refusal_is_never_raw_or_english(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    sink_call = house.sink.async_call

    async def refusing(domain, service, data=None, blocking=False, **kwargs):
        if (domain, service) == ("automation", "reload"):
            raise Unauthorized("Unauthorized")
        return await sink_call(domain, service, data, blocking, **kwargs)

    house.entity.hass.services.async_call = refusing
    house.say(SENTENCE)
    answer = house.say("Ja.").speech
    assert "Unauthorized" not in answer
    assert UNAUTHORIZED_TEXT in answer
    assert user_facing_error(Unauthorized("Unauthorized")) == UNAUTHORIZED_TEXT


def test_device_writes_keep_the_user_context(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Schalte das Küchenlicht ein.")
    writes = [call for call in house.calls if call[1] == "turn_on"]
    assert writes
    assert all(call[2].user_id == "anna" for call in writes)


def test_only_listed_services_use_the_system_context():
    """Architecture rule: ``system_context_for_turn`` only for own management."""
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "async_call"
            ):
                continue
            uses_system = any(
                keyword.arg == "context"
                and isinstance(keyword.value, ast.Call)
                and getattr(keyword.value.func, "id", None) == "system_context_for_turn"
                for keyword in node.keywords
            )
            if not uses_system:
                continue
            literal = tuple(
                arg.value for arg in node.args[:2] if isinstance(arg, ast.Constant)
            )
            if literal not in SYSTEM_CONTEXT_SERVICES:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {literal}")
    assert offenders == []
    assert SYSTEM_CONTEXT_SERVICES == frozenset({("automation", "reload")})
