"""7.1 Learning Center: panel registration, WebSocket contract, history, assets."""

from __future__ import annotations

import ast
import json
import re
from datetime import timedelta
from pathlib import Path

from learning_center_harness import (
    ADMIN,
    NOW,
    USER_A,
    USER_B,
    make_env,
    make_hass,
    preference_model,
    run,
    thermal_model,
)

from homeintent import learning_center_ws
from homeintent.proactive_model import (
    CommunicationChannel,
    HistoryRecord,
    OpportunityOutcome,
    PriorityLevel,
    PrivacyLevel,
    SituationKind,
)

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "homeintent"
FRONTEND = INTEGRATION / "frontend" / "homeintent-learning-center.js"
DOC_COMMANDS = (
    "entries", "summary", "models/list", "models/get", "models/evidence", "models/forget",
    "models/reset", "preferences/confirm", "preferences/reject", "habits/preview",
    "habits/accept", "habits/reject", "permissions/list", "permissions/revoke",
    "mutes/list", "mutes/remove", "history/list", "tombstones/list", "traces/list", "bindings/list", "bindings/remove",
    "settings/implicit_action_level", "shadow/report", "subscribe",
)


# -- panel registration -------------------------------------------------------------------

def test_panel_is_registered_once_locally_without_yaml(tmp_path):
    async def _go():
        hass = make_hass()
        env = await make_env(tmp_path, hass=hass)
        panels = hass.data["frontend_panels"]
        assert list(panels) == ["homeintent"]
        panel = panels["homeintent"]
        assert panel["sidebar_title"] == "HomeIntent"
        assert panel["sidebar_icon"] == "mdi:brain"
        assert panel["webcomponent_name"] == "homeintent-learning-center"
        assert panel["require_admin"] is False
        assert panel["embed_iframe"] is False
        assert panel["config"] == {"api_version": 1}
        assert re.fullmatch(r"/homeintent_frontend/homeintent-learning-center\.js\?v=[0-9a-f]{16}", panel["module_url"])
        assert [item.url_path for item in hass.http.static] == ["/homeintent_frontend"]
        assert Path(hass.http.static[0].path) == INTEGRATION / "frontend"
        # A second entry neither re-registers the panel nor the static route.
        await make_env(tmp_path, hass=hass, entry_id="entry-2")
        assert list(hass.data["frontend_panels"]) == ["homeintent"]
        assert len(hass.http.static) == 1
        return hass, env
    run(_go())


def test_panel_removed_only_after_last_entry_unloads(tmp_path):
    async def _go():
        hass = make_hass()
        first = await make_env(tmp_path, hass=hass)
        second = await make_env(tmp_path, hass=hass, entry_id="entry-2")
        learning_center_ws.async_unload_learning_center(hass, first.entry)
        assert "homeintent" in hass.data["frontend_panels"]
        learning_center_ws.async_unload_learning_center(hass, second.entry)
        assert "homeintent" not in hass.data["frontend_panels"]
        # Reload re-registers the panel but never the (permanent) static route.
        await learning_center_ws.async_setup_learning_center(hass, first.entry)
        assert "homeintent" in hass.data["frontend_panels"]
        assert len(hass.http.static) == 1
    run(_go())


def test_static_registration_is_async_and_never_blocking():
    source = (INTEGRATION / "learning_center_ws.py").read_text(encoding="utf-8")
    assert "async_register_static_paths" in source
    assert "register_static_path(" not in source
    assert "async_add_executor_job(_asset_digest)" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            body = ast.unparse(node)
            assert "read_bytes(" not in body and "read_text(" not in body and "open(" not in body


def test_multiple_entries_require_explicit_selection(tmp_path):
    async def _go():
        hass = make_hass()
        first = await make_env(tmp_path, hass=hass)
        second = await make_env(tmp_path, hass=hass, entry_id="entry-2")
        await second.registry.async_upsert(thermal_model())
        entries, _ = await first.call(USER_A, "entries")
        assert [item["entry_id"] for item in entries["entries"]] == ["entry-1", "entry-2"]
        _result, error = await first.call(USER_A, "summary")
        assert error[0] == "entry_required"
        one, _ = await first.call(USER_A, "summary", entry_id="entry-1")
        two, _ = await first.call(USER_A, "summary", entry_id="entry-2")
        assert one["total_models"] == 0 and two["total_models"] == 1  # never merged
        _result, error = await first.call(USER_A, "summary", entry_id="unknown")
        assert error[0] == "entry_not_found"
    run(_go())


# -- WebSocket contract -------------------------------------------------------------------

def test_command_set_is_closed_and_typed(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        commands = sorted(env.hass.data["_ws_commands"])
        assert commands == sorted(f"homeintent/learning_center/{name}" for name in DOC_COMMANDS)
        assert not any(name.endswith("/action") for name in commands)
        result, error = await env.call(USER_A, "action", service="light.turn_on")
        assert result is None and error[0] == "unknown_command"
    run(_go())


def test_malformed_payloads_fail_safely(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(preference_model(USER_A))
        for command, payload in (
            ("models/get", {}),
            ("models/get", {"ref": 5}),
            ("models/list", {"limit": "many"}),
            ("models/list", {"offset": -1}),
            ("history/list", {"limit": 0}),
            ("permissions/revoke", {}),
            ("preferences/confirm", {"ref": "x", "service": "lock.unlock"}),
            ("models/forget", {"ref": "x", "user_id": USER_B}),
        ):
            result, error = await env.call(USER_A, command, **payload)
            assert result is None and error[0] == "invalid_format", command
        result, error = await env.call(USER_A, "models/get", ref="does-not-exist")
        assert error[0] == "not_found"
        assert len(await env.registry.async_list()) == 1
        assert env.service_calls() == 0
    run(_go())


def test_every_response_carries_the_api_version(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(thermal_model())
        for command in ("entries", "summary", "models/list", "permissions/list", "mutes/list", "history/list"):
            result, error = await env.call(ADMIN, command)
            assert error is None and result["api_version"] == 1, command
    run(_go())


def test_live_refresh_signal_carries_no_private_payload(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        subscribed, error = await env.call(USER_A, "subscribe")
        assert error is None
        start = subscribed["revision"]
        await env.registry.async_upsert(preference_model(USER_A))
        events = env.conn(USER_A).events
        assert events[-1]["event"] == {"entry_id": "entry-1", "revision": start + 1}
        listing, _ = await env.call(USER_A, "models/list")
        assert listing["revision"] == start + 1
        assert len(listing["models"]) == 1  # the new authorized card appears
        env.engine.permissions.revoke_all(USER_A)
        await env.engine.async_persist()  # voice-side V12 change
        assert events[-1]["event"]["revision"] == start + 2
        raw = json.dumps(events)
        assert "lampe" not in raw and USER_A not in raw
        # Unsubscribe via the HA connection bookkeeping.
        for unsubscribe in env.conn(USER_A).subscriptions.values():
            unsubscribe()
        assert env.runtime.learning_center_revision.listener_count == 0
    run(_go())


def test_subscription_survives_entry_reload(tmp_path):
    async def _go():
        from homeintent.runtime_data import HomeIntentRuntimeData

        env = await make_env(tmp_path)
        subscribed, _ = await env.call(USER_A, "subscribe")
        learning_center_ws.async_unload_learning_center(env.hass, env.entry)
        fresh = HomeIntentRuntimeData(learned_models=env.registry, learning_policy=env.runtime.learning_policy)
        env.entry.runtime_data = fresh
        await learning_center_ws.async_setup_learning_center(env.hass, env.entry)
        assert fresh.learning_center_revision is env.runtime.learning_center_revision
        events = env.conn(USER_A).events
        assert events[-1]["event"]["revision"] == subscribed["revision"] + 1
        await env.registry.async_upsert(thermal_model())
        assert events[-1]["event"]["revision"] >= subscribed["revision"] + 2
    run(_go())


def test_unloading_entry_detaches_change_listeners(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        assert env.engine.persist_listeners
        for callback in env.entry._on_unload:
            callback()
        assert env.engine.persist_listeners == []
        before = env.runtime.learning_center_revision.value
        await env.registry.async_upsert(thermal_model())
        assert env.runtime.learning_center_revision.value == before
    run(_go())


# -- history ---------------------------------------------------------------------------------

def _record(index: int, *, privacy: PrivacyLevel, recipient: str | None) -> HistoryRecord:
    return HistoryRecord(
        f"h{index}", f"s{index}", SituationKind.ENTRY_LEFT_OPEN, f"Garage {index}",
        OpportunityOutcome.COMMUNICATE, recipient, CommunicationChannel.PUSH,
        NOW - timedelta(minutes=200 - index), PriorityLevel.IMPORTANT, privacy, "delivered",
        ("attention_budget_exhausted",), "ok", None,
    )


def test_history_privacy_filtering_and_pagination(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        for index in range(60):
            env.engine.history.append(_record(index, privacy=PrivacyLevel.HOUSEHOLD, recipient=USER_B))
        env.engine.history.append(_record(100, privacy=PrivacyLevel.PERSONAL, recipient=USER_A))
        env.engine.history.append(_record(101, privacy=PrivacyLevel.SENSITIVE, recipient=USER_B))
        mine, _ = await env.call(USER_A, "history/list")
        assert len(mine["records"]) == 40 and mine["next_cursor"] == 40
        assert mine["records"][0]["record_id"] == "h100"  # newest first, own personal record
        assert mine["records"][0]["recipient_is_viewer"] is True
        assert all(item["record_id"] != "h101" for item in mine["records"])
        more, _ = await env.call(USER_A, "history/list", cursor=40)
        assert len(more["records"]) == 21 and more["next_cursor"] is None
        theirs, _ = await env.call(USER_B, "history/list", limit=100)
        ids = {item["record_id"] for item in theirs["records"]}
        assert "h100" not in ids and "h101" in ids
        admin, _ = await env.call(ADMIN, "history/list", limit=100)
        admin_ids = {item["record_id"] for item in admin["records"]}
        assert "h100" not in admin_ids and "h101" not in admin_ids  # no admin exception
        _result, error = await env.call(USER_A, "history/list", limit=500)
        assert error[0] == "invalid_format"
        allowed = {"record_id", "timestamp", "situation_kind", "subject_label", "decision", "channel",
                   "result", "reasons", "recipient_is_viewer", "recipient_label", "acknowledgement", "visibility"}
        assert set(mine["records"][0]) == allowed  # no transcript/audio/evidence fields
    run(_go())


def test_learning_center_adds_no_history_or_model_storage(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(thermal_model())
        before = sorted(path.name for path in tmp_path.iterdir())
        for command in ("summary", "models/list", "history/list", "permissions/list", "mutes/list"):
            await env.call(USER_A, command)
        assert sorted(path.name for path in tmp_path.iterdir()) == before
    run(_go())


# -- frontend asset -------------------------------------------------------------------------

def test_frontend_asset_is_local_and_inert():
    source = FRONTEND.read_text(encoding="utf-8")
    assert not re.search(r"https?://", source)
    assert "fetch(" not in source and "XMLHttpRequest" not in source
    assert not re.search(r"\bimport\s*\(", source) and not re.search(r"^\s*import\s", source, re.M)
    assert "eval(" not in source and "new Function" not in source
    assert "innerHTML" not in source and "outerHTML" not in source and "insertAdjacentHTML" not in source
    assert "localStorage" not in source and "sessionStorage" not in source
    assert "sendMessagePromise" in source and "subscribeMessage" in source
    assert "user_id" not in source  # the browser never supplies authority
    assert "setInterval" not in source  # no polling loop


def test_frontend_calls_only_documented_commands():
    source = FRONTEND.read_text(encoding="utf-8")
    used = set(re.findall(r'_call\("([a-z_/]+)"', source))
    used |= set(re.findall(r'\$\{PREFIX\}/([a-z_/]+)', source))
    used |= set(re.findall(r'_mutate\("([a-z_/]+)"', source))
    assert used and used <= set(DOC_COMMANDS)


def test_frontend_api_version_matches_backend():
    from homeintent.learning_center import API_VERSION

    source = FRONTEND.read_text(encoding="utf-8")
    assert f"const API_VERSION = {API_VERSION};" in source


def test_frontend_ships_german_and_english():
    source = FRONTEND.read_text(encoding="utf-8")
    assert re.search(r"^\s*de: \{", source, re.M) and re.search(r"^\s*en: \{", source, re.M)
    assert "Wissen & Autonomie" in source and "Knowledge & Autonomy" in source
    # Quality is never presented as a probability; it is explicitly denied.
    assert "keine Wahrscheinlichkeit" in source and "not a probability" in source
    assert not re.search(r"%\s*Wahrscheinlichkeit|% probability|AI confidence", source)


def test_frontend_is_packaged_inside_the_integration_for_hacs():
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs.get("content_in_root") is False
    assert FRONTEND.is_file()
    assert FRONTEND.parent.parent == INTEGRATION
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    for dependency in ("frontend", "http", "panel_custom", "websocket_api"):
        assert dependency in manifest["dependencies"]
    assert manifest["dependencies"] == sorted(manifest["dependencies"])


def test_traces_list_shows_admins_everything_and_users_their_own(tmp_path):
    from datetime import datetime

    from homeintent.execution_trace import (
        ContextIndex, ExecutionTraceStore, TraceRecord, actor_hash, register_trace,
    )

    async def _go():
        env = await make_env(tmp_path)
        store = ExecutionTraceStore()
        for execution_id, user in (("e1", USER_A), ("e2", USER_B)):
            store.add(TraceRecord(
                execution_id=execution_id, created_at=datetime(2026, 9, 28, 22, 13).isoformat(),
                actor=actor_hash(user), user_present=True, utterance="Aktiviere Schlafen",
                targets=("script.schlafen",), names={"script.schlafen": "Schlafen"},
            ))
        env.entry.runtime_data.trace = register_trace(env.hass, store, ContextIndex())
        own, error = await env.call(USER_A, "traces/list")
        assert error is None
        assert [item["execution_id"] for item in own["traces"]] == ["e1"]
        assert own["traces"][0]["targets"] == ["Schlafen"]
        everything, error = await env.call(ADMIN, "traces/list")
        assert error is None and len(everything["traces"]) == 2
    run(_go())


def test_bindings_are_listed_removable_and_level_is_admin_only(tmp_path):
    from datetime import datetime

    from homeintent.bindings import BindingKind, BindingScope

    async def _go():
        env = await make_env(tmp_path)
        store = env.entry.runtime_data.bindings
        now = datetime(2026, 9, 28, 22, 0)
        household = await store.async_bind(
            BindingKind.ROUTINE, "sleep", "script.gute_nacht", confirmed=True, now=now, created_by=ADMIN,
        )
        own = await store.async_bind(
            BindingKind.ROUTINE, "movie", "scene.film", confirmed=True, now=now,
            scope=BindingScope.USER, user_id=USER_A, created_by=USER_A,
        )
        await store.async_bind(
            BindingKind.ROUTINE, "read", "scene.lesen", confirmed=True, now=now,
            scope=BindingScope.USER, user_id=USER_B, created_by=USER_B,
        )
        listed, error = await env.call(USER_A, "bindings/list")
        assert error is None
        ids = {item["binding_id"] for item in listed["bindings"]}
        assert ids == {household.binding_id, own.binding_id}
        assert listed["implicit_action_level"] == "propose" and listed["can_change_level"] is False
        labels = {item["key_label"] for item in listed["bindings"]}
        assert "schlafen gehen" in labels
        _result, error = await env.call(USER_A, "bindings/remove", binding_id=household.binding_id)
        assert error is not None and error[0] == "not_authorized"
        removed, error = await env.call(USER_A, "bindings/remove", binding_id=own.binding_id)
        assert error is None and removed == {"removed": True}
        _result, error = await env.call(USER_A, "settings/implicit_action_level", level="low_risk_auto")
        assert error is not None and error[0] == "not_authorized"
    run(_go())
