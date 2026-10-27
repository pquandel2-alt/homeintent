"""Bounded observation of Home Assistant script runs through public APIs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Awaitable, Callable
from typing import Coroutine, Mapping, Protocol


class _ScriptState(Protocol):
    state: str
    attributes: Mapping[str, object]


class _StateMachine(Protocol):
    def get(self, entity_id: str) -> _ScriptState | None: ...


class _ServiceRegistry(Protocol):
    def has_service(self, domain: str, service: str) -> bool: ...


class _HomeAssistant(Protocol):
    states: _StateMachine
    services: _ServiceRegistry

    def async_create_task(
        self,
        target: Coroutine[object, object, object],
        *,
        name: str | None = None,
    ) -> asyncio.Task[object]: ...


class ScriptExecutionStatus(StrEnum):
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ScriptExecutionResult:
    status: ScriptExecutionStatus
    entity_id: str
    direct_action: bool
    error: BaseException | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {
            ScriptExecutionStatus.ACCEPTED,
            ScriptExecutionStatus.RUNNING,
            ScriptExecutionStatus.COMPLETED,
            ScriptExecutionStatus.TIMED_OUT,
        }


def _script_state(
    hass: _HomeAssistant, entity_id: str
) -> tuple[str | None, Mapping[str, object]]:
    state = hass.states.get(entity_id)
    if state is None:
        return None, {}
    return state.state, state.attributes


def _consume_late_result(task: asyncio.Task[object]) -> None:
    """Retrieve a post-timeout exception so no orphan task warns the loop."""

    if task.cancelled():
        return
    task.exception()


async def async_run_and_observe_script(
    hass: _HomeAssistant,
    entity_id: str,
    data: Mapping[str, object],
    *,
    context: object,
    wait_seconds: float,
    service_runner: Callable[
        [str, str, Mapping[str, object], object], Awaitable[object]
    ],
) -> ScriptExecutionResult:
    """Run one script and observe completion for a bounded interval.

    Home Assistant documents two public forms: ``script.turn_on`` starts in
    the background, while calling ``script.<object_id>`` waits and propagates
    an in-script failure. We prefer the latter when HA registered it. Older
    or reduced test hosts fall back to ``script.turn_on`` and are reported as
    accepted, never falsely as completed.
    """

    state, attributes = _script_state(hass, entity_id)
    if state == "on" and attributes.get("mode", "single") == "single":
        return ScriptExecutionResult(
            ScriptExecutionStatus.REJECTED, entity_id, direct_action=False
        )

    object_id = entity_id.split(".", 1)[1]
    direct = hass.services.has_service("script", object_id)
    domain = "script"
    service = object_id if direct else "turn_on"
    call_data = dict(data) if direct else {**data, "entity_id": entity_id}

    async def _call() -> object:
        return await service_runner(domain, service, call_data, context)

    task = hass.async_create_task(
        _call(), name=f"homeintent script outcome {entity_id}"
    )
    done, _pending = await asyncio.wait(
        {task}, timeout=max(0.0, min(30.0, wait_seconds))
    )
    if task in done:
        if task.cancelled():
            return ScriptExecutionResult(
                ScriptExecutionStatus.FAILED,
                entity_id,
                direct,
                asyncio.CancelledError(),
            )
        if (error := task.exception()) is not None:
            return ScriptExecutionResult(
                ScriptExecutionStatus.FAILED, entity_id, direct, error
            )
        return ScriptExecutionResult(
            ScriptExecutionStatus.COMPLETED if direct else ScriptExecutionStatus.ACCEPTED,
            entity_id,
            direct,
        )

    task.add_done_callback(_consume_late_result)
    current_state, _ = _script_state(hass, entity_id)
    return ScriptExecutionResult(
        ScriptExecutionStatus.RUNNING
        if current_state == "on"
        else ScriptExecutionStatus.TIMED_OUT,
        entity_id,
        direct,
    )


def script_execution_speech(
    name: str, result: ScriptExecutionResult
) -> str:
    """Truthful response text for the observed script status."""

    if result.status is ScriptExecutionStatus.COMPLETED:
        return f"{name} wurde ausgeführt."
    if result.status is ScriptExecutionStatus.RUNNING:
        return f"{name} wurde gestartet und läuft noch."
    if result.status is ScriptExecutionStatus.ACCEPTED:
        return (
            f"{name} wurde gestartet. Ob es erfolgreich beendet wurde, "
            "konnte ich nicht verifizieren."
        )
    if result.status is ScriptExecutionStatus.TIMED_OUT:
        return (
            f"{name} wurde gestartet. Das Ergebnis konnte ich innerhalb "
            "der Wartezeit nicht verifizieren."
        )
    if result.status is ScriptExecutionStatus.REJECTED:
        return f"{name} läuft bereits und wurde nicht erneut gestartet."
    return f"{name} ist fehlgeschlagen."


__all__ = (
    "ScriptExecutionResult",
    "ScriptExecutionStatus",
    "async_run_and_observe_script",
    "script_execution_speech",
)
