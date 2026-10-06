"""7.9.2 A1: a device that moves in the requested direction is a success,
and HomeIntent waits (bounded, event-driven) for the device's report.

Matrix: device kinds (light on/off, switch, cover up/down/position,
climate set point, media volume) x report delay (immediate, short, longer
than the wait) x what the device reports (target, movement in the requested
direction, opposite direction, ``unavailable``, nothing). Checked: tone or
speech, the spoken note, the measured extra wait, parallel waiting for
several targets, partial success, and that turns without pending effects
never wait.

The fake Home Assistant has a real event bus double: a device reports by
writing its state and firing ``state_changed`` after ``delay`` seconds, the
way Zigbee/Z-Wave/Matter devices do.
"""

from __future__ import annotations

import asyncio
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

SATELLITE = "assist_satellite.kueche"
WAIT = 0.4  # effect_wait_seconds for the tests (production default 2 s)


class FakeBus:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, Callable[[Any], None]]] = []

    def async_listen(self, event_type: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        entry = (event_type, callback)
        self.listeners.append(entry)

        def remove() -> None:
            if entry in self.listeners:
                self.listeners.remove(entry)

        return remove

    def fire(self, event_type: str, data: dict[str, Any]) -> None:
        event = types.SimpleNamespace(event_type=event_type, data=data)
        for kind, callback in list(self.listeners):
            if kind == event_type:
                callback(event)


@dataclass
class Report:
    """What a device reports after a call, and when."""

    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    delay: float = 0.0


@dataclass
class Devices:
    hass: Any
    sink_call: Any
    reports: dict[tuple[str, str], Report] = field(default_factory=dict)
    tones: list[dict[str, Any]] = field(default_factory=list)

    async def async_call(self, domain, service, data=None, blocking=False, **kwargs):
        data = dict(data or {})
        if (domain, service) == ("assist_satellite", "announce"):
            self.tones.append(data)
            return
        await self.sink_call(domain, service, data, blocking=blocking, **kwargs)
        ids = data.get("entity_id")
        for entity_id in [ids] if isinstance(ids, str) else list(ids or []):
            report = self.reports.get((entity_id, service))
            if report is None:
                continue
            if report.delay <= 0:
                self.write(entity_id, report)
            else:
                asyncio.get_running_loop().call_later(report.delay, self.write, entity_id, report)

    def write(self, entity_id: str, report: Report) -> None:
        from homeassistant.core import State

        old = self.hass.states.get(entity_id)
        attributes = {**(getattr(old, "attributes", None) or {}), **report.attributes}
        new = State(entity_id, report.state, attributes)
        self.hass.states._states[entity_id] = new
        self.hass.bus.fire("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new})


def _house(monkeypatch, tmp_path, style: str = "tone", wait: float = WAIT) -> tuple[HouseConversation, Devices]:
    import homeintent.response_style as response_style
    from homeassistant.core import State

    options = {**PUSH_OPTIONS, "response_style": style, "effect_wait_seconds": wait}
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=options)
    hass = house.entity.hass
    hass.bus = FakeBus()
    devices = Devices(hass, hass.services.async_call)
    hass.services.async_call = devices.async_call
    for entity in house.entities:
        attributes: dict[str, Any] = {}
        if entity.domain == "cover":
            attributes["current_position"] = 100 if entity.state == "open" else 0
        if entity.domain == "climate":
            attributes["temperature"] = 20.0
        if entity.domain == "media_player":
            attributes["volume_level"] = 0.3
        hass.states._states.setdefault(entity.entity_id, State(entity.entity_id, entity.state, attributes))
    # The kitchen light is on, so "aus" has something to report.
    hass.states._states["light.kuechenlicht"] = State("light.kuechenlicht", "on", {})
    hass.states._states[SATELLITE] = State(SATELLITE, "idle", {"supported_features": 1})
    monkeypatch.setattr(response_style, "_device_entities", lambda hass, device_id: [SATELLITE])
    monkeypatch.setattr(response_style, "IDLE_GRACE_SECONDS", 0)
    return house, devices


def _say(house: HouseConversation, text: str) -> tuple[str, float]:
    from homeassistant.components.conversation import ConversationInput

    async def turn() -> tuple[str, float]:
        started = time.monotonic()
        result = await house.entity._async_handle_message(
            ConversationInput(
                text=text, conversation_id=house.conversation_id,
                context=types.SimpleNamespace(user_id=house.user),
                satellite_id=SATELLITE, device_id="dev-satellite",
            ),
            chat_log=None,
        )
        elapsed = time.monotonic() - started
        pending = list(house.entity.hass._tasks)
        house.entity.hass._tasks.clear()
        await asyncio.gather(*pending)
        return result.response.speech or "", elapsed

    return asyncio.run(turn())


# (sentence, entity, service, success report, moving report, contrary report)
_KINDS: dict[str, tuple[str, str, str, Report, Report | None, Report | None]] = {
    "light_on": ("Schalte die Stehlampe ein.", "light.stehlampe", "turn_on", Report("on"), None, None),
    "light_off": ("Schalte das Küchenlicht aus.", "light.kuechenlicht", "turn_off", Report("off"), None, None),
    "cover_down": (
        "Fahre den Küchenrollladen runter.", "cover.kuechenrollladen", "close_cover",
        Report("closed", {"current_position": 0}), Report("closing", {"current_position": 90}),
        Report("opening", {"current_position": 100}),
    ),
    "cover_up": (
        "Fahre die Markise aus.", "cover.markise", "open_cover",
        Report("open", {"current_position": 100}), Report("opening", {"current_position": 10}),
        Report("closing", {"current_position": 0}),
    ),
    "cover_position": (
        "Stelle den Küchenrollladen auf 40 Prozent.", "cover.kuechenrollladen", "set_cover_position",
        Report("open", {"current_position": 40}), Report("closing", {"current_position": 80}),
        Report("opening", {"current_position": 100}),
    ),
    "climate": (
        "Stelle die Heizung im Büro auf 22 Grad.", "climate.heizung_buero", "set_temperature",
        Report("heat", {"temperature": 22.0, "current_temperature": 19.0}), None, None,
    ),
}


def _speak_or_tone(house, devices, text):
    devices.tones.clear()
    speech, elapsed = _say(house, text)
    return speech, elapsed, list(devices.tones)


@pytest.mark.parametrize("delay", [0.0, 0.1])
@pytest.mark.parametrize("kind", list(_KINDS))
def test_reported_within_the_wait_is_a_tone(monkeypatch, tmp_path, kind, delay):
    text, entity_id, service, success, _moving, _contrary = _KINDS[kind]
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[(entity_id, service)] = Report(success.state, success.attributes, delay)
    speech, elapsed, tones = _speak_or_tone(house, devices, text)
    assert speech == "" and len(tones) == 1, (kind, speech)
    assert elapsed < WAIT + 0.3


@pytest.mark.parametrize("delay", [0.0, 0.1])
@pytest.mark.parametrize("kind", [k for k, v in _KINDS.items() if v[4] is not None])
def test_moving_in_the_requested_direction_is_a_tone(monkeypatch, tmp_path, kind, delay):
    """T1: "es fährt" is success - ``closing`` for "runter" etc."""
    text, entity_id, service, _success, moving, _contrary = _KINDS[kind]
    assert moving is not None
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[(entity_id, service)] = Report(moving.state, moving.attributes, delay)
    speech, _elapsed, tones = _speak_or_tone(house, devices, text)
    assert speech == "" and len(tones) == 1, (kind, speech)


@pytest.mark.parametrize("kind", [k for k, v in _KINDS.items() if v[5] is not None])
def test_the_opposite_direction_is_spoken(monkeypatch, tmp_path, kind):
    text, entity_id, service, _success, _moving, contrary = _KINDS[kind]
    assert contrary is not None
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[(entity_id, service)] = Report(contrary.state, contrary.attributes, 0.05)
    speech, elapsed, tones = _speak_or_tone(house, devices, text)
    assert tones == [] and "Gegenrichtung" in speech, speech
    assert elapsed >= WAIT - 0.05  # it waited for a correction until the limit


@pytest.mark.parametrize("kind", list(_KINDS))
def test_unavailable_is_spoken(monkeypatch, tmp_path, kind):
    text, entity_id, service, *_ = _KINDS[kind]
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[(entity_id, service)] = Report("unavailable", {}, 0.05)
    speech, _elapsed, tones = _speak_or_tone(house, devices, text)
    assert tones == [] and "nicht erreichbar" in speech, speech


@pytest.mark.parametrize("kind", list(_KINDS))
def test_too_late_is_spoken_as_unconfirmed(monkeypatch, tmp_path, kind):
    text, entity_id, service, success, *_ = _KINDS[kind]
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[(entity_id, service)] = Report(success.state, success.attributes, WAIT + 0.6)
    speech, elapsed, tones = _speak_or_tone(house, devices, text)
    assert tones == [] and "noch nicht zurückgemeldet" in speech, speech
    assert WAIT - 0.05 <= elapsed < WAIT + 0.4  # bounded


@pytest.mark.parametrize("style", ["spoken", "tone"])
def test_no_pending_effect_never_waits(monkeypatch, tmp_path, style):
    house, devices = _house(monkeypatch, tmp_path, style=style, wait=5.0)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on")
    speech, elapsed, _tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert elapsed < 1.0
    _speech, elapsed, _tones = _speak_or_tone(house, devices, "Ist das Garagentor offen?")
    assert elapsed < 1.0


def test_spoken_style_waits_at_most_the_limit_and_says_the_late_device(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path, style="spoken")
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, 3.0)
    speech, elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert tones == [] and "Stehlampe" in speech and "noch nicht zurückgemeldet" in speech
    assert elapsed < WAIT + 0.4


def test_several_targets_are_awaited_in_parallel(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, 0.25)
    devices.reports[("light.flurlicht", "turn_on")] = Report("on", {}, 0.25)
    speech, elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe und das Flurlicht ein.")
    assert speech == "" and len(tones) == 1
    assert elapsed < 0.25 * 2  # parallel, not one after the other


def test_partial_confirmation_stays_partial(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, 0.05)
    devices.reports[("light.flurlicht", "turn_on")] = Report("on", {}, WAIT + 1.0)
    speech, _elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe und das Flurlicht ein.")
    assert tones == []
    assert "Flurlicht hat sich noch nicht zurückgemeldet" in speech
    assert "Stehlampe hat" not in speech


def test_wait_zero_decides_at_once(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path, wait=0.0)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, 0.1)
    speech, elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert tones == [] and speech and elapsed < 0.2


def test_the_extra_wait_is_measured(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, 0.1)
    _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    samples = list(house.entity.hass.data["homeintent_effect_wait_stats"])
    assert len(samples) == 1 and 80 <= samples[0] <= WAIT * 1000 + 50


def test_the_listener_is_removed(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, 0.05)
    _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert house.entity.hass.bus.listeners == []


@pytest.mark.parametrize(("value", "expected"), [(None, 2.0), (0, 0.0), (1.5, 1.5), (9, 5.0), (-1, 0.0), ("x", 2.0)])
def test_option_range(value, expected):
    from homeintent.effect_wait import wait_seconds

    options = {} if value is None else {"effect_wait_seconds": value}
    assert wait_seconds(options) == expected


def _expectation(service: str, domain: str, data=None, prior=None):
    from homeintent.effect_wait import EffectExpectation

    return EffectExpectation(f"{domain}.x", "X", domain, service, data or {}, prior)


@pytest.mark.parametrize(("service", "domain", "data", "prior", "state", "attrs", "verdict"), [
    ("open_cover", "cover", {}, None, "open", {}, "reached"),
    ("open_cover", "cover", {}, None, "opening", {}, "moving"),
    ("open_cover", "cover", {}, None, "closing", {}, "contrary"),
    ("open_cover", "cover", {}, None, "closed", {}, "pending"),
    ("close_cover", "cover", {}, None, "closing", {}, "moving"),
    ("close_cover", "cover", {}, None, "opening", {}, "contrary"),
    ("set_cover_position", "cover", {"position": 40}, 100, "closing", {"current_position": 90}, "moving"),
    ("set_cover_position", "cover", {"position": 40}, 100, "opening", {"current_position": 100}, "contrary"),
    ("set_cover_position", "cover", {"position": 60}, 0, "opening", {"current_position": 10}, "moving"),
    ("set_cover_position", "cover", {"position": 40}, 100, "open", {"current_position": 40}, "reached"),
    ("open_valve", "valve", {}, None, "opening", {}, "moving"),
    ("close_valve", "valve", {}, None, "closed", {}, "reached"),
    ("lock", "lock", {}, None, "locking", {}, "moving"),
    ("lock", "lock", {}, None, "unlocking", {}, "contrary"),
    ("set_temperature", "climate", {"temperature": 22}, None, "heat", {"temperature": 22.0, "current_temperature": 18}, "reached"),
    ("set_temperature", "climate", {"temperature": 22}, None, "heat", {"temperature": 20.0}, "pending"),
    ("volume_set", "media_player", {"volume_level": 0.4}, 0.3, "playing", {"volume_level": 0.4}, "reached"),
    ("volume_set", "media_player", {"volume_level": 0.4}, 0.3, "playing", {"volume_level": 0.3}, "pending"),
    ("media_pause", "media_player", {}, None, "paused", {}, "reached"),
    ("turn_on", "light", {}, None, "unavailable", {}, "unavailable"),
    ("turn_on", "light", {}, None, "unknown", {}, "unavailable"),
    ("turn_on", "climate", {}, None, "heat", {}, "reached"),
    ("turn_off", "media_player", {}, None, "standby", {}, "reached"),
])
def test_judge_matrix(service, domain, data, prior, state, attrs, verdict):
    from homeintent.effect_wait import judge

    assert judge(_expectation(service, domain, data, prior), state, attrs).value == verdict


def test_wait_statistics():
    from collections import deque

    from homeintent.effect_wait import STATS_KEY, wait_statistics

    hass = types.SimpleNamespace(data={})
    assert wait_statistics(hass) == {"samples": 0}
    hass.data[STATS_KEY] = deque([100.0, 300.0, 200.0, 2000.0])
    stats = wait_statistics(hass)
    assert stats["samples"] == 4 and stats["p50_ms"] == 300.0 and stats["p95_ms"] == 2000.0
