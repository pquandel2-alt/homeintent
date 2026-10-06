"""Bounded, event-driven wait for a device's confirmation (7.9.2 A1).

Real devices (Zigbee, Z-Wave, Matter, WLAN) report their new state 0.1-2 s
after the service call; covers and awnings report ``opening``/``closing``
while they travel. Checking the target state right after the call made the
confirmation tone rare and "Fahre den Rollladen runter" always spoken.

The rule, decided per target from typed facts only:

* ``REACHED``     - the requested end state (or set point, volume, position);
* ``MOVING``      - a movement *in the requested direction* (``opening`` for
  "auf/hoch", ``closing`` for "zu/runter", towards a requested position);
  this counts as success ("es fährt");
* ``PENDING``     - nothing to see yet (may still come within the wait);
* ``CONTRARY``    - the opposite direction or the opposite end state;
* ``UNAVAILABLE`` - ``unavailable``/``unknown``.

``REACHED`` and ``MOVING`` are success (``EXECUTED``); everything else after
the wait is ``UNCONFIRMED`` and spoken.

The executor judges every target right after the write. Targets that are
not yet confirmed are registered with the running turn; at the end of the
turn ``async_settle`` waits for ``state_changed`` of *all* of them in
parallel - one listener, no polling - at most ``effect_wait_seconds``
(option, 0-5 s, default 2 s). A turn without pending targets never waits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from .const import CONF_EFFECT_WAIT_SECONDS, DEFAULT_EFFECT_WAIT_SECONDS
from .entities import STATELESS_ACTION_DOMAINS
from .service_call import ServiceCallPlan

_LOGGER = logging.getLogger(__name__)

MIN_EFFECT_WAIT_SECONDS = 0.0
MAX_EFFECT_WAIT_SECONDS = 5.0
STATS_KEY = "homeintent_effect_wait_stats"
_STATS_SAMPLES = 500
_TOLERANCE = 0.05

__all__ = (
    "CONF_EFFECT_WAIT_SECONDS",
    "DEFAULT_EFFECT_WAIT_SECONDS",
    "EffectExpectation",
    "EffectVerdict",
    "PendingEffect",
    "SettleResult",
    "append_speech",
    "async_settle",
    "async_settle_turn",
    "expectations_for",
    "judge",
    "wait_seconds",
    "wait_statistics",
)


class EffectVerdict(Enum):
    REACHED = "reached"
    MOVING = "moving"
    PENDING = "pending"
    CONTRARY = "contrary"
    UNAVAILABLE = "unavailable"

    @property
    def success(self) -> bool:
        return self in (EffectVerdict.REACHED, EffectVerdict.MOVING)


@dataclass(frozen=True)
class EffectExpectation:
    """What one target must show after one write."""

    entity_id: str
    name: str
    domain: str
    service: str
    data: Mapping[str, object]
    # Position/volume before the call: the direction of a set_position.
    prior_position: float | None = None


@dataclass
class PendingEffect:
    """One write of the running turn whose effect is not confirmed yet."""

    expectations: tuple[EffectExpectation, ...]
    verdicts: dict[str, EffectVerdict] = field(default_factory=lambda: {})

    @property
    def confirmed(self) -> bool:
        return all(self.verdicts.get(item.entity_id, EffectVerdict.PENDING).success for item in self.expectations)


@dataclass(frozen=True)
class SettleResult:
    """Outcome of the end-of-turn wait."""

    confirmed: tuple[bool, ...]
    waited_ms: float
    notes: tuple[str, ...]
    observed: bool


def wait_seconds(options: Mapping[str, object]) -> float:
    value = options.get(CONF_EFFECT_WAIT_SECONDS, DEFAULT_EFFECT_WAIT_SECONDS)
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_EFFECT_WAIT_SECONDS
    return min(MAX_EFFECT_WAIT_SECONDS, max(MIN_EFFECT_WAIT_SECONDS, seconds))


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _prior_position(domain: str, service: str, attributes: Mapping[str, Any] | None) -> float | None:
    if attributes is None:
        return None
    if service in {"set_cover_position", "set_valve_position"}:
        return _number(attributes.get("current_position"))
    if service == "volume_set":
        return _number(attributes.get("volume_level"))
    return None


def expectations_for(
    plan: ServiceCallPlan,
    prior: Callable[[str], tuple[str | None, Mapping[str, Any] | None]],
    names: Mapping[str, str],
) -> tuple[EffectExpectation, ...]:
    """One expectation per target that has a checkable effect."""
    if not _has_effect(plan):
        return ()
    target_ids = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    found: list[EffectExpectation] = []
    for entity_id in target_ids:
        domain = entity_id.split(".", 1)[0]
        if domain in STATELESS_ACTION_DOMAINS:
            continue
        _state, attributes = prior(entity_id)
        found.append(
            EffectExpectation(
                entity_id,
                names.get(entity_id, entity_id),
                domain,
                plan.service,
                dict(plan.data),
                _prior_position(domain, plan.service, attributes),
            )
        )
    return tuple(found)


_END_STATES: dict[str, tuple[str, str | None, str | None, str | None]] = {
    # service: (end state, moving state, contrary moving state, contrary end state)
    "open_cover": ("open", "opening", "closing", None),
    "close_cover": ("closed", "closing", "opening", None),
    "open_valve": ("open", "opening", "closing", None),
    "close_valve": ("closed", "closing", "opening", None),
    "lock": ("locked", "locking", "unlocking", None),
    "unlock": ("unlocked", "unlocking", "locking", None),
    "open": ("open", "opening", "locking", None),
    "alarm_disarm": ("disarmed", None, None, None),
    "media_play": ("playing", None, None, None),
    "media_pause": ("paused", None, None, None),
}

_JUDGED_SERVICES = frozenset(
    set(_END_STATES)
    | {"turn_on", "turn_off", "set_cover_position", "set_valve_position", "set_temperature",
       "set_hvac_mode", "volume_set", "volume_mute", "media_stop"}
)


def _has_effect(plan: ServiceCallPlan) -> bool:
    return plan.service in _JUDGED_SERVICES


def judge(expectation: EffectExpectation, state: str | None, attributes: Mapping[str, Any] | None) -> EffectVerdict:
    """Classify one target's current state against what was requested."""
    if state is None or state in {"unavailable", "unknown"}:
        return EffectVerdict.UNAVAILABLE
    attrs: Mapping[str, Any] = attributes or {}
    service = expectation.service
    domain = expectation.domain
    data = expectation.data
    if service in _END_STATES:
        end, moving, contrary_moving, _ = _END_STATES[service]
        if state == end:
            return EffectVerdict.REACHED
        if moving is not None and state == moving:
            return EffectVerdict.MOVING
        if contrary_moving is not None and state == contrary_moving:
            return EffectVerdict.CONTRARY
        return EffectVerdict.PENDING
    if service == "turn_on":
        if domain == "climate":
            return EffectVerdict.PENDING if state == "off" else EffectVerdict.REACHED
        if domain == "media_player":
            return EffectVerdict.PENDING if state in {"off", "standby"} else EffectVerdict.REACHED
        return EffectVerdict.REACHED if state == "on" else EffectVerdict.PENDING
    if service == "turn_off":
        if domain == "media_player":
            return EffectVerdict.REACHED if state in {"off", "standby"} else EffectVerdict.PENDING
        return EffectVerdict.REACHED if state == "off" else EffectVerdict.PENDING
    if service in {"set_cover_position", "set_valve_position"}:
        target = _number(data.get("position"))
        current = _number(attrs.get("current_position"))
        if target is None:
            return EffectVerdict.REACHED
        if current is not None and abs(current - target) < 0.5:
            return EffectVerdict.REACHED
        prior = expectation.prior_position
        rising = prior is not None and target > prior
        falling = prior is not None and target < prior
        if prior is None and current is not None:
            rising, falling = target > current, target < current
        if state == "opening":
            return EffectVerdict.MOVING if rising else EffectVerdict.CONTRARY
        if state == "closing":
            return EffectVerdict.MOVING if falling else EffectVerdict.CONTRARY
        if current is None and ((target >= 100 and state == "open") or (target <= 0 and state == "closed")):
            return EffectVerdict.REACHED
        return EffectVerdict.PENDING
    if service == "set_temperature":
        # The new set point in the attribute is the effect; the room does
        # not have to be warm yet.
        checks = [(key, _number(data.get(key))) for key in ("temperature", "target_temp_low", "target_temp_high")]
        wanted = [(key, value) for key, value in checks if value is not None]
        if not wanted:
            return EffectVerdict.REACHED
        for key, value in wanted:
            have = _number(attrs.get(key))
            if have is None or value is None or abs(have - value) > _TOLERANCE:
                return EffectVerdict.PENDING
        mode = data.get("hvac_mode")
        if isinstance(mode, str) and state != mode:
            return EffectVerdict.PENDING
        return EffectVerdict.REACHED
    if service == "set_hvac_mode":
        mode = data.get("hvac_mode")
        return EffectVerdict.REACHED if not isinstance(mode, str) or state == mode else EffectVerdict.PENDING
    if service == "volume_set":
        wanted_volume = _number(data.get("volume_level"))
        have_volume = _number(attrs.get("volume_level"))
        if wanted_volume is None:
            return EffectVerdict.REACHED
        if have_volume is not None and abs(have_volume - wanted_volume) <= 0.011:
            return EffectVerdict.REACHED
        return EffectVerdict.PENDING
    if service == "volume_mute":
        wanted_mute = data.get("is_volume_muted")
        if not isinstance(wanted_mute, bool):
            return EffectVerdict.REACHED
        return EffectVerdict.REACHED if attrs.get("is_volume_muted") is wanted_mute else EffectVerdict.PENDING
    if service == "media_stop":
        return EffectVerdict.PENDING if state == "playing" else EffectVerdict.REACHED
    return EffectVerdict.REACHED


def _current(hass: Any, entity_id: str) -> tuple[str | None, Mapping[str, Any] | None]:
    states = getattr(hass, "states", None)
    state = states.get(entity_id) if states is not None else None
    if state is None:
        return None, None
    return getattr(state, "state", None), getattr(state, "attributes", None)


def judge_now(hass: Any, pending: PendingEffect) -> None:
    for item in pending.expectations:
        state, attributes = _current(hass, item.entity_id)
        verdict = judge(item, state, attributes)
        if pending.verdicts.get(item.entity_id) is EffectVerdict.CONTRARY and not verdict.success:
            # A movement the wrong way stays the finding even when the
            # device has already stopped at the wrong end before the wait
            # ends (a cover reversing from half height).
            verdict = EffectVerdict.CONTRARY
        pending.verdicts[item.entity_id] = verdict


def _note(item: EffectExpectation, verdict: EffectVerdict) -> str:
    if verdict is EffectVerdict.UNAVAILABLE:
        return f"{item.name} ist nicht erreichbar."
    if verdict is EffectVerdict.CONTRARY:
        if item.domain in {"cover", "valve"}:
            return f"{item.name} fährt in die Gegenrichtung."
        return f"{item.name} meldet das Gegenteil."
    return f"{item.name} hat sich noch nicht zurückgemeldet."


def _record(hass: Any, waited_ms: float) -> None:
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return
    samples: deque[float] = data.setdefault(STATS_KEY, deque(maxlen=_STATS_SAMPLES))  # type: ignore[assignment]
    samples.append(round(waited_ms, 1))


def _listen(hass: Any, entity_ids: Sequence[str], on_change: Callable[[], None]) -> Callable[[], None] | None:
    """One state listener for every pending target; ``None`` without a bus."""
    if getattr(hass, "bus", None) is None:
        return None
    try:
        from homeassistant.helpers.event import async_track_state_change_event
    except ImportError:
        async_track_state_change_event = None
    if async_track_state_change_event is not None:
        return async_track_state_change_event(hass, list(entity_ids), lambda _event: on_change())
    wanted = frozenset(entity_ids)

    def _filtered(event: Any) -> None:
        if (getattr(event, "data", None) or {}).get("entity_id") in wanted:
            on_change()

    return hass.bus.async_listen("state_changed", _filtered)


def wait_statistics(hass: Any) -> dict[str, float | int]:
    """p50/p95 of the extra wait per turn that waited (diagnostics, live run)."""
    data = getattr(hass, "data", None)
    samples = sorted(data.get(STATS_KEY, ())) if isinstance(data, dict) else []
    if not samples:
        return {"samples": 0}

    def pct(fraction: float) -> float:
        index = min(len(samples) - 1, max(0, int(round(fraction * (len(samples) - 1)))))
        return float(samples[index])

    return {"samples": len(samples), "p50_ms": pct(0.5), "p95_ms": pct(0.95), "max_ms": float(samples[-1])}


async def async_settle(hass: Any, pending: Sequence[PendingEffect], timeout: float) -> SettleResult:
    """Wait (event-driven, bounded) until every pending write is confirmed.

    Without an event bus (nothing to observe) or with ``timeout`` 0 the
    current states decide at once and no note is added.
    """
    if not pending:
        return SettleResult((), 0.0, (), False)
    for item in pending:
        judge_now(hass, item)
    if all(item.confirmed for item in pending):
        return SettleResult(tuple(True for _ in pending), 0.0, (), True)
    entity_ids = sorted({exp.entity_id for item in pending for exp in item.expectations})
    done = asyncio.Event()

    def _on_change() -> None:
        for item in pending:
            judge_now(hass, item)
        if all(item.confirmed for item in pending):
            done.set()

    started = time.monotonic()
    unsubscribe = _listen(hass, entity_ids, _on_change) if timeout > 0 else None
    observed = unsubscribe is not None
    try:
        if observed:
            try:
                await asyncio.wait_for(done.wait(), timeout)
            except asyncio.TimeoutError:
                pass
    finally:
        if unsubscribe is not None:
            unsubscribe()
    waited_ms = (time.monotonic() - started) * 1000 if observed else 0.0
    if observed:
        _record(hass, waited_ms)
    for item in pending:
        judge_now(hass, item)
    notes: list[str] = []
    if observed:
        for item in pending:
            for exp in item.expectations:
                verdict = item.verdicts.get(exp.entity_id, EffectVerdict.PENDING)
                if not verdict.success:
                    notes.append(_note(exp, verdict))
    return SettleResult(tuple(item.confirmed for item in pending), waited_ms, tuple(notes), observed)


async def async_settle_turn(hass: Any, outcomes: Any, options: Mapping[str, object]) -> tuple[str, ...]:
    """Settle the running turn's pending writes into typed outcomes.

    Called once at the end of every turn, before the reply style is
    decided; returns the honest notes for targets that did not confirm.
    """
    from .turn_outcome import TurnOutcomeKind

    pending = [item for item in outcomes.pending if isinstance(item, PendingEffect)]
    settled = await async_settle(hass, pending, wait_seconds(options))
    outcomes.pending.clear()
    outcomes.kinds.extend(
        TurnOutcomeKind.EXECUTED if ok else TurnOutcomeKind.UNCONFIRMED for ok in settled.confirmed
    )
    return settled.notes


def append_speech(response: Any, notes: Sequence[str]) -> None:
    """Append ``notes`` to the reply's plain speech."""
    if not notes:
        return
    speech = response.speech
    spoken = speech.get("plain", {}).get("speech", "") if isinstance(speech, dict) else str(speech or "")
    response.async_set_speech(f"{spoken} {' '.join(notes)}".strip())
