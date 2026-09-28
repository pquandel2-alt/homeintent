"""Home Assistant glue for the 7.1 Learning Center.

* registers the local frontend asset (async static path, no CDN),
* registers exactly one ``HomeIntent`` sidebar panel for all entries,
* exposes closed, typed, authenticated WebSocket commands.

The authenticated ``connection.user`` is the only authority for who is
asking.  The browser never supplies a user id, never selects a service and
never receives data it may not see: every filter runs here, server-side.
No command in this module issues a device service call.
"""

from __future__ import annotations

import hashlib
import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import CONF_CUSTOM_ALIASES, DOMAIN
from .learning_center import (
    API_VERSION,
    DEFAULT_EVIDENCE,
    DEFAULT_HISTORY_PAGE,
    DEFAULT_MODEL_PAGE,
    MAX_EVIDENCE,
    MAX_HISTORY_PAGE,
    MAX_MODEL_PAGE,
    NON_MUTABLE_SITUATIONS,
    LearningCenterError,
    LearningCenterRevision,
    LearningCenterService,
    LearningCenterSources,
    ProactiveStatus,
    Viewer,
    can_forget_model,
    model_ref,
)
from .learning_control import (
    ControlErrorCode,
    LearningControlError,
    async_accept_habit,
    async_confirm_preference,
    async_forget_model,
    async_reject_habit,
    async_reject_preference,
    async_reset_models,
    model_owner,
    options_without_preference_aliases,
)
from .habit_discovery import routine_from_habit_model
from .proactive_model import SituationKind
from .execution_trace import actor_hash, trace_view

_LOGGER = logging.getLogger(__name__)

DATA_LEARNING_CENTER = f"{DOMAIN}_learning_center"
PANEL_URL_PATH = "homeintent"
PANEL_COMPONENT = "homeintent-learning-center"
PANEL_TITLE = "HomeIntent"
PANEL_ICON = "mdi:brain"
STATIC_URL = "/homeintent_frontend"
FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_FILE = "homeintent-learning-center.js"
AUDIT_LIMIT = 200
COMMAND_PREFIX = "homeintent/learning_center"


@dataclass(frozen=True)
class LearningCenterAuditEntry:
    """Content-free record of one Learning Center mutation attempt."""

    timestamp: datetime
    action: str
    actor: str
    target: str
    result: str


class LearningCenterAudit:
    """Bounded runtime audit; actors are hashed like ``AuditTrail`` does."""

    def __init__(self, limit: int = AUDIT_LIMIT) -> None:
        self._entries: deque[LearningCenterAuditEntry] = deque(maxlen=limit)

    def record(self, action: str, actor_id: str, target: str, result: str) -> None:
        actor = hashlib.sha256(actor_id.encode()).hexdigest()[:10]
        entry = LearningCenterAuditEntry(
            datetime.now(timezone.utc), action, actor, target[:64], result
        )
        self._entries.append(entry)
        _LOGGER.info(
            "HomeIntent Learning Center %s by %s on %s: %s",
            action, actor, entry.target, result,
        )

    def entries(self) -> tuple[LearningCenterAuditEntry, ...]:
        return tuple(self._entries)


@dataclass
class _LearningCenterState:
    commands_registered: bool = False
    static_registered: bool = False
    panel_registered: bool = False
    module_url: str | None = None
    entry_ids: set[str] = field(default_factory=set)
    # One counter per entry id that outlives entry reloads, so an open
    # panel's subscription keeps working after an options change.
    revisions: dict[str, LearningCenterRevision] = field(default_factory=dict)


def _asset_digest() -> str:
    """Content hash for cache busting (runs in the executor)."""
    return hashlib.sha256((FRONTEND_DIR / FRONTEND_FILE).read_bytes()).hexdigest()[:16]


def _state(hass: HomeAssistant) -> _LearningCenterState:
    state = hass.data.get(DATA_LEARNING_CENTER)
    if not isinstance(state, _LearningCenterState):
        state = _LearningCenterState()
        hass.data[DATA_LEARNING_CENTER] = state
    return state


async def async_setup_learning_center(hass: HomeAssistant, entry: Any) -> None:
    """Register commands/assets/panel once; wire this entry's change signal."""
    state = _state(hass)
    state.entry_ids.add(entry.entry_id)
    if not state.commands_registered:
        for handler in _COMMANDS:
            websocket_api.async_register_command(hass, handler)
        state.commands_registered = True
    if getattr(hass, "http", None) is not None:
        if not state.static_registered:
            from homeassistant.components.http import StaticPathConfig

            digest = await hass.async_add_executor_job(_asset_digest)
            await hass.http.async_register_static_paths([
                StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), True)
            ])
            state.static_registered = True
            state.module_url = f"{STATIC_URL}/{FRONTEND_FILE}?v={digest}"
        if not state.panel_registered and state.module_url is not None:
            from homeassistant.components import panel_custom

            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=PANEL_URL_PATH,
                webcomponent_name=PANEL_COMPONENT,
                sidebar_title=PANEL_TITLE,
                sidebar_icon=PANEL_ICON,
                module_url=state.module_url,
                embed_iframe=False,
                require_admin=False,
                config={"api_version": API_VERSION},
            )
            state.panel_registered = True
    runtime = entry.runtime_data
    revision = state.revisions.get(entry.entry_id)
    if revision is None:
        revision = runtime.learning_center_revision
        state.revisions[entry.entry_id] = revision
    else:
        runtime.learning_center_revision = revision
        revision.bump()  # a reloaded entry may have changed what is visible

    @callback
    def _changed() -> None:
        revision.bump()

    registry = runtime.learned_models
    if registry is not None:
        entry.async_on_unload(registry.add_change_listener(_changed))
    proactive = getattr(runtime.proactive_context, "engine", None)
    listeners = getattr(proactive, "persist_listeners", None)
    if isinstance(listeners, list):
        listeners.append(_changed)

        def _remove_persist_listener() -> None:
            if _changed in listeners:
                listeners.remove(_changed)

        entry.async_on_unload(_remove_persist_listener)


def async_unload_learning_center(hass: HomeAssistant, entry: Any) -> None:
    """Remove the sidebar panel once the last HomeIntent entry unloads."""
    state = _state(hass)
    state.entry_ids.discard(entry.entry_id)
    if state.entry_ids or not state.panel_registered:
        return
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    state.panel_registered = False


# -- request context ------------------------------------------------------------------

class _HassLabels:
    """``LabelSource`` over live HA state/registries; nothing is persisted."""

    def __init__(self, hass: HomeAssistant, users: dict[str, str]) -> None:
        self._hass = hass
        self._users = users
        self._entity_registry: Any = None
        self._area_registry: Any = None
        try:
            from homeassistant.helpers import area_registry, entity_registry

            self._entity_registry = entity_registry.async_get(hass)
            self._area_registry = area_registry.async_get(hass)
        except Exception:  # noqa: BLE001 - registries are optional for labels
            pass

    def entity_name(self, entity_id: str) -> str | None:
        state = self._hass.states.get(entity_id)
        if state is not None:
            name = getattr(state, "name", None)
            return str(name) if name else entity_id
        registry = self._entity_registry
        entry = registry.async_get(entity_id) if registry is not None else None
        if entry is not None:
            return entry.name or entry.original_name or entity_id
        return None

    def area_name(self, area_id: str) -> str | None:
        registry = self._area_registry
        area = registry.async_get_area(area_id) if registry is not None else None
        return getattr(area, "name", None) if area is not None else None

    def user_name(self, user_id: str) -> str | None:
        return self._users.get(user_id)


def _entry_for(hass: HomeAssistant, entry_id: str | None) -> Any:
    getter = getattr(hass.config_entries, "async_loaded_entries", None)
    entries = list(getter(DOMAIN)) if getter is not None else list(
        hass.config_entries.async_entries(DOMAIN)
    )
    entries = [item for item in entries if getattr(item, "runtime_data", None) is not None]
    if entry_id is None:
        if len(entries) == 1:
            return entries[0]
        raise LearningCenterError("entry_required" if entries else "entry_not_found")
    for item in entries:
        if item.entry_id == entry_id:
            return item
    raise LearningCenterError("entry_not_found")


def _viewer(connection: Any) -> Viewer:
    user = getattr(connection, "user", None)
    user_id = getattr(user, "id", None)
    if not isinstance(user_id, str) or not user_id:
        raise LearningCenterError("not_authorized")
    return Viewer(user_id, bool(getattr(user, "is_admin", False)))


async def _users(hass: HomeAssistant) -> dict[str, str]:
    try:
        users = await hass.auth.async_get_users()
    except Exception:  # noqa: BLE001 - names are cosmetic only
        return {}
    return {user.id: user.name for user in users if getattr(user, "name", None)}


def _proactive_engine(runtime: Any) -> Any:
    return getattr(runtime.proactive_context, "engine", None)


def _proactive_status(engine: Any) -> ProactiveStatus | None:
    config = getattr(engine, "config", None)
    if config is None:
        return None
    quiet = config.quiet
    return ProactiveStatus(
        bool(config.enabled), bool(config.standing_permissions_enabled),
        quiet.default_window, dict(quiet.user_windows),
    )


async def _service(hass: HomeAssistant, entry: Any) -> LearningCenterService:
    runtime = entry.runtime_data
    engine = _proactive_engine(runtime)
    sources = LearningCenterSources(
        runtime.learned_models, runtime.learning_policy, runtime.experiences,
        engine, _proactive_status(engine),
    )
    return LearningCenterService(
        entry.entry_id, sources, _HassLabels(hass, await _users(hass)),
        now=datetime.now(timezone.utc),
        revision=runtime.learning_center_revision.value,
    )


def _audit(entry: Any) -> LearningCenterAudit:
    audit = getattr(entry.runtime_data, "learning_center_audit", None)
    if not isinstance(audit, LearningCenterAudit):
        audit = LearningCenterAudit()
        entry.runtime_data.learning_center_audit = audit
    return audit


def _send_error(connection: Any, msg: dict[str, Any], code: str) -> None:
    connection.send_error(msg["id"], code, code)


async def _run(
    hass: HomeAssistant,
    connection: Any,
    msg: dict[str, Any],
    handler: Callable[[HomeAssistant, Any, Viewer, dict[str, Any]], Any],
    *,
    audit_action: str | None = None,
) -> None:
    """Resolve entry + viewer, run, and map refusals to stable error codes."""
    try:
        viewer = _viewer(connection)
        entry = _entry_for(hass, msg.get("entry_id"))
    except LearningCenterError as err:
        _send_error(connection, msg, err.code)
        return
    target = str(msg.get("ref") or msg.get("permission_id") or msg.get("situation_kind") or "*")
    try:
        result = await handler(hass, entry, viewer, msg)
    except LearningCenterError as err:
        if audit_action is not None:
            _audit(entry).record(audit_action, viewer.user_id, target, f"denied:{err.code}")
        _send_error(connection, msg, err.code)
        return
    except LearningControlError as err:
        if audit_action is not None:
            _audit(entry).record(audit_action, viewer.user_id, target, f"denied:{err.code.value}")
        _send_error(connection, msg, err.code.value)
        return
    if audit_action is not None:
        _audit(entry).record(audit_action, viewer.user_id, target, "ok")
    connection.send_result(msg["id"], result)


_ENTRY = vol.Optional("entry_id")
_REF = vol.Required("ref")


def _cmd(name: str, schema: dict[Any, Any] | None = None) -> dict[Any, Any]:
    return {vol.Required("type"): f"{COMMAND_PREFIX}/{name}", _ENTRY: str, **(schema or {})}


# -- read handlers ------------------------------------------------------------------------

async def _h_summary(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    return (await service.async_summary(viewer)).to_dict()


async def _h_models_list(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    items, total, next_offset = await service.async_list(
        viewer, offset=msg.get("offset", 0), limit=msg.get("limit", DEFAULT_MODEL_PAGE)
    )
    policy = entry.runtime_data.learning_policy
    return {
        "api_version": API_VERSION,
        "revision": service.revision,
        "total": total,
        "next_offset": next_offset,
        "learning_enabled": policy is not None and policy.learning_mode.value != "off",
        "models": [item.to_dict() for item in items],
    }


async def _h_models_get(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    return {"api_version": API_VERSION, "model": service.detail(model, viewer).to_dict()}


async def _h_models_evidence(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    evidence = await service.async_evidence(model, viewer, limit=msg.get("limit", DEFAULT_EVIDENCE))
    return {"api_version": API_VERSION, "evidence": evidence.to_dict()}


async def _h_habit_preview(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    if model_owner(model) != viewer.user_id:
        raise LearningControlError(ControlErrorCode.WRONG_OWNER)
    routine = routine_from_habit_model(model, viewer.user_id)
    if routine is None:
        raise LearningControlError(ControlErrorCode.UNSUPPORTED_OPERATION)
    labels = service.labels
    return {
        "api_version": API_VERSION,
        "name": routine.name,
        "steps": [
            {
                "entity_label": labels.entity_name(entity_id) or entity_id,
                "property": step.desired_state.property_name,
                "expected": str(step.desired_state.value),
            }
            for step in routine.steps
            for entity_id in step.scope.entity_ids[:1]
        ],
    }


async def _h_permissions_list(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    return {
        "api_version": API_VERSION,
        "permissions": [item.to_dict() for item in service.permissions(viewer)],
        "features": service.features(viewer).to_dict(),
    }


async def _h_mutes_list(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    return {
        "api_version": API_VERSION,
        "mutes": [item.to_dict() for item in service.mutes(viewer)],
        "non_mutable_situations": [item.value for item in NON_MUTABLE_SITUATIONS],
    }


async def _h_history_list(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    records, next_cursor = service.history(
        viewer, cursor=msg.get("cursor", 0), limit=msg.get("limit", DEFAULT_HISTORY_PAGE)
    )
    return {
        "api_version": API_VERSION,
        "records": [item.to_dict() for item in records],
        "next_cursor": next_cursor,
    }


async def _h_tombstones(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    return {
        "api_version": API_VERSION,
        "tombstones": [item.to_dict() for item in await service.async_tombstones(viewer)],
    }


# -- mutation handlers (closed operations, zero device calls) ----------------------------

def _registry(entry: Any) -> Any:
    registry = entry.runtime_data.learned_models
    if registry is None:
        raise LearningCenterError("unavailable")
    return registry


def _cleanup_aliases(hass: HomeAssistant, entry: Any, models: tuple[Any, ...]) -> None:
    updated = options_without_preference_aliases(entry.options, models, CONF_CUSTOM_ALIASES)
    if updated is not None:
        hass.config_entries.async_update_entry(entry, options=updated)


async def _h_forget(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    if not can_forget_model(model, viewer):
        raise LearningCenterError("admin_required" if model_owner(model) is None else "not_authorized")
    forgotten = await async_forget_model(
        _registry(entry), entry.runtime_data.predictive_house, model.model_id
    )
    if forgotten is None:
        raise LearningCenterError("not_found")
    _cleanup_aliases(hass, entry, (forgotten,))
    return {"forgotten": True, "ref": model_ref(model.model_id)}


async def _h_reset(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    if not viewer.is_admin:
        raise LearningCenterError("admin_required")
    deleted, removed = await async_reset_models(
        _registry(entry), entry.runtime_data.predictive_house
    )
    _cleanup_aliases(hass, entry, removed)
    return {"deleted": deleted}


async def _h_confirm_preference(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    await async_confirm_preference(_registry(entry), model.model_id, viewer.user_id)
    return {"confirmed": True}


async def _h_reject_preference(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    await async_reject_preference(_registry(entry), model.model_id, viewer.user_id)
    return {"rejected": True}


async def _h_accept_habit(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    profiles = entry.runtime_data.profiles
    if profiles is None:
        raise LearningCenterError("unavailable")
    if model_owner(model) != viewer.user_id:
        raise LearningControlError(ControlErrorCode.WRONG_OWNER)
    draft = await async_accept_habit(_registry(entry), model.model_id, viewer.user_id)
    # The panel dialog showed this exact typed preview and the owner
    # confirmed it; the routine is stored, never executed.
    confirmed = replace(draft, confirmed=True)
    await profiles.async_save_routine(confirmed, confirmed=True)
    return {"accepted": True, "routine_name": confirmed.name, "step_count": len(confirmed.steps)}


async def _h_reject_habit(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    service = await _service(hass, entry)
    model = await service.async_resolve(msg["ref"], viewer)
    await async_reject_habit(_registry(entry), model.model_id, viewer.user_id)
    return {"rejected": True}


async def _h_revoke_permission(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    engine = _proactive_engine(entry.runtime_data)
    if engine is None:
        raise LearningCenterError("unavailable")
    permission = next(
        (item for item in engine.permissions.all() if item.permission_id == msg["permission_id"]),
        None,
    )
    if permission is None or (permission.owner_user_id != viewer.user_id and not viewer.is_admin):
        # Another user's permission is indistinguishable from a missing one.
        raise LearningCenterError("not_found")
    if permission.revoked:
        raise LearningCenterError("invalid_state")
    if engine.permissions.revoke(permission.permission_id) is None:
        raise LearningCenterError("invalid_state")
    await engine.async_persist()
    return {"revoked": True}


async def _h_remove_mute(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    engine = _proactive_engine(entry.runtime_data)
    if engine is None:
        raise LearningCenterError("unavailable")
    try:
        kind = SituationKind(msg["situation_kind"])
    except ValueError as err:
        raise LearningCenterError("not_found") from err
    if not engine.attention_state.unmute(viewer.user_id, kind):
        raise LearningCenterError("not_found")
    await engine.async_persist()
    return {"removed": True}


# -- command wrappers -------------------------------------------------------------------

def _limit(maximum: int) -> Any:
    return vol.All(int, vol.Range(min=1, max=maximum))


_OFFSET = vol.All(int, vol.Range(min=0, max=100_000))


@websocket_api.websocket_command(_cmd("entries"))
@websocket_api.async_response
async def ws_entries(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    try:
        _viewer(connection)
    except LearningCenterError as err:
        _send_error(connection, msg, err.code)
        return
    getter = getattr(hass.config_entries, "async_loaded_entries", None)
    entries = list(getter(DOMAIN)) if getter is not None else list(
        hass.config_entries.async_entries(DOMAIN)
    )
    connection.send_result(msg["id"], {
        "api_version": API_VERSION,
        "entries": [
            {"entry_id": item.entry_id, "title": item.title}
            for item in entries if getattr(item, "runtime_data", None) is not None
        ],
    })


@websocket_api.websocket_command(_cmd("summary"))
@websocket_api.async_response
async def ws_summary(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_summary)


@websocket_api.websocket_command(_cmd("models/list", {
    vol.Optional("offset", default=0): _OFFSET,
    vol.Optional("limit", default=DEFAULT_MODEL_PAGE): _limit(MAX_MODEL_PAGE),
}))
@websocket_api.async_response
async def ws_models_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_models_list)


@websocket_api.websocket_command(_cmd("models/get", {_REF: str}))
@websocket_api.async_response
async def ws_models_get(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_models_get)


@websocket_api.websocket_command(_cmd("models/evidence", {
    _REF: str,
    vol.Optional("limit", default=DEFAULT_EVIDENCE): _limit(MAX_EVIDENCE),
}))
@websocket_api.async_response
async def ws_models_evidence(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_models_evidence)


@websocket_api.websocket_command(_cmd("models/forget", {_REF: str}))
@websocket_api.async_response
async def ws_models_forget(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_forget, audit_action="forget_model")


@websocket_api.websocket_command(_cmd("models/reset", {vol.Required("confirm"): True}))
@websocket_api.async_response
async def ws_models_reset(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_reset, audit_action="reset_models")


@websocket_api.websocket_command(_cmd("preferences/confirm", {_REF: str}))
@websocket_api.async_response
async def ws_preferences_confirm(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_confirm_preference, audit_action="confirm_preference")


@websocket_api.websocket_command(_cmd("preferences/reject", {_REF: str}))
@websocket_api.async_response
async def ws_preferences_reject(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_reject_preference, audit_action="reject_preference")


@websocket_api.websocket_command(_cmd("habits/preview", {_REF: str}))
@websocket_api.async_response
async def ws_habits_preview(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_habit_preview)


@websocket_api.websocket_command(_cmd("habits/accept", {_REF: str}))
@websocket_api.async_response
async def ws_habits_accept(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_accept_habit, audit_action="accept_habit")


@websocket_api.websocket_command(_cmd("habits/reject", {_REF: str}))
@websocket_api.async_response
async def ws_habits_reject(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_reject_habit, audit_action="reject_habit")


@websocket_api.websocket_command(_cmd("permissions/list"))
@websocket_api.async_response
async def ws_permissions_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_permissions_list)


@websocket_api.websocket_command(_cmd("permissions/revoke", {vol.Required("permission_id"): str}))
@websocket_api.async_response
async def ws_permissions_revoke(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_revoke_permission, audit_action="revoke_permission")


@websocket_api.websocket_command(_cmd("mutes/list"))
@websocket_api.async_response
async def ws_mutes_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_mutes_list)


@websocket_api.websocket_command(_cmd("mutes/remove", {vol.Required("situation_kind"): str}))
@websocket_api.async_response
async def ws_mutes_remove(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_remove_mute, audit_action="remove_mute")


@websocket_api.websocket_command(_cmd("history/list", {
    vol.Optional("cursor", default=0): _OFFSET,
    vol.Optional("limit", default=DEFAULT_HISTORY_PAGE): _limit(MAX_HISTORY_PAGE),
}))
@websocket_api.async_response
async def ws_history_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_history_list)


async def _h_traces_list(hass: HomeAssistant, entry: Any, viewer: Viewer, msg: dict[str, Any]) -> Any:
    """"Was hat HomeIntent ausgelöst?": the execution trace, read-only.

    Administrators see the household; everybody else only their own turns.
    """
    trace = getattr(entry.runtime_data, "trace", None)
    records = trace.store.recent() if trace is not None else ()
    own = actor_hash(viewer.user_id)
    visible = [item for item in records if viewer.is_admin or item.actor == own]
    limit = int(msg.get("limit", 40))
    return {
        "api_version": API_VERSION,
        "traces": [trace_view(item) for item in visible[:limit]],
    }


@websocket_api.websocket_command(_cmd("traces/list", {
    vol.Optional("limit", default=40): _limit(200),
}))
@websocket_api.async_response
async def ws_traces_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_traces_list)


@websocket_api.websocket_command(_cmd("tombstones/list"))
@websocket_api.async_response
async def ws_tombstones_list(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, _h_tombstones)


@websocket_api.websocket_command(_cmd("subscribe"))
@callback
def ws_subscribe(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Push ``{entry_id, revision}`` only; the panel re-reads what it may see."""
    try:
        _viewer(connection)
        entry = _entry_for(hass, msg.get("entry_id"))
    except LearningCenterError as err:
        _send_error(connection, msg, err.code)
        return
    revision: LearningCenterRevision = entry.runtime_data.learning_center_revision
    entry_id = entry.entry_id

    @callback
    def _forward(value: int) -> None:
        connection.send_message(websocket_api.event_message(
            msg["id"], {"entry_id": entry_id, "revision": value}
        ))

    connection.subscriptions[msg["id"]] = revision.subscribe(_forward)
    connection.send_result(msg["id"], {"entry_id": entry_id, "revision": revision.value})


_COMMANDS = (
    ws_entries, ws_summary, ws_models_list, ws_models_get, ws_models_evidence,
    ws_models_forget, ws_models_reset, ws_preferences_confirm, ws_preferences_reject,
    ws_habits_preview, ws_habits_accept, ws_habits_reject, ws_permissions_list,
    ws_permissions_revoke, ws_mutes_list, ws_mutes_remove, ws_history_list,
    ws_tombstones_list, ws_traces_list, ws_subscribe,
)


__all__ = (
    "DATA_LEARNING_CENTER",
    "FRONTEND_DIR",
    "FRONTEND_FILE",
    "LearningCenterAudit",
    "PANEL_URL_PATH",
    "STATIC_URL",
    "async_setup_learning_center",
    "async_unload_learning_center",
)
