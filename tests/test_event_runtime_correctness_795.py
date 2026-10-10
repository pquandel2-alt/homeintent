"""7.9.5: the single EventRuntime worker evaluates each event correctly.

7.9.4 removed the task per ``state_changed`` (P0) and kept that fix intact
here. Its worker, however, built one snapshot per batch and evaluated every
event of the batch against it. That snapshot is the house *after* the whole
batch, so within one batch

- an entity that changed twice was seen in its final state both times,
- a person who left and came back produced no "left" transition,
- a sensor value change was reported with the final value,
- the thermal tracker saw later window and temperature states,
- events still queued behind the batch leaked into it.

Further robustness findings of the same review:

- a failing snapshot build ended the worker and lost the batch,
- a worker that outlived its cancellation could drain the queue of a
  restarted runtime next to the new worker,
- the fixed-selection filter scanned the stored list for every event in
  the house.

"Before red": the same file against 7.9.4 (``517d76c``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent import event_runtime, hass_entities  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_SELECTED_ENTITIES,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.event_runtime import SituationRuntime  # noqa: E402
from homeintent.runtime_data import HomeIntentRuntimeData  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, State  # noqa: E402

T0 = datetime(2026, 10, 9, 6, tzinfo=timezone.utc)
DOOR = "binary_sensor.haustuer"
WINDOW = "binary_sensor.fenster"
PERSON = "person.anna"
TEMP = "sensor.temperatur"
LIGHT = "light.flur"


class _Bus:
    """Runs a ``@callback`` inline like Home Assistant does."""

    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def async_listen(self, event_type: str, listener: Any) -> Any:
        assert event_type == "state_changed"
        assert not inspect.iscoroutinefunction(listener)
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def fire(self, event: Any) -> None:
        for listener in list(self.listeners):
            listener(event)


class _House:
    """A minimal state machine that fires ``state_changed`` like HA."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._offset = 0

    def set(self, entity_id: str, state: str, **attributes: Any) -> None:
        old = self._hass.states.get(entity_id)
        new = State(entity_id, state, attributes)
        self._hass.states._states[entity_id] = new
        self._offset += 1
        self._hass.bus.fire(SimpleNamespace(
            data={"entity_id": entity_id, "old_state": old, "new_state": new},
            time_fired=T0 + timedelta(seconds=self._offset),
            context=SimpleNamespace(id=f"ctx-{self._offset}"),
        ))


def _snapshot_builder(hass: HomeAssistant, entity_ids: list[str]) -> Any:
    """``build_entity_snapshots`` reading the stub state machine *now*."""
    area = {DOOR: "flur", WINDOW: "wohnzimmer", TEMP: "wohnzimmer", LIGHT: "flur"}

    def _build(*_args: Any) -> list[EntitySnapshot]:
        snapshots = []
        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            if state is None:
                continue
            domain = entity_id.split(".", 1)[0]
            snapshots.append(EntitySnapshot(
                entity_id, state.attributes.get("friendly_name", entity_id), domain,
                state.state, area_id=area.get(entity_id),
                device_class=state.attributes.get("device_class"),
                unit=state.attributes.get("unit_of_measurement"),
                attributes=state.attributes,
            ))
        return snapshots

    return _build


def _setup(
    monkeypatch: Any, entity_ids: list[str], **runtime_fields: Any
) -> tuple[HomeAssistant, _House, SituationRuntime]:
    hass = HomeAssistant()
    hass.bus = _Bus()
    options = {CONF_SELECTED_ENTITIES: list(entity_ids), CONF_AGENT_EVENT_CATEGORIES: ""}
    runtime = SituationRuntime(
        hass, ConfigEntry(options=options), HomeIntentRuntimeData(**runtime_fields)
    )
    monkeypatch.setattr(
        event_runtime, "build_entity_snapshots", _snapshot_builder(hass, entity_ids)
    )
    return hass, _House(hass), runtime


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(50):
        pending = [task for task in hass._tasks if not task.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


class _RecordingContext:
    """Stands in for the V12 context: records what each event saw."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str | None, dict[str, str]]] = []

    async def async_observe_state(
        self, entity: EntitySnapshot, previous: str | None, entities: Any
    ) -> None:
        self.seen.append((
            entity.entity_id, entity.state, previous,
            {item.entity_id: item.state for item in entities},
        ))


def test_each_event_sees_its_own_new_state_within_one_batch(monkeypatch):
    context = _RecordingContext()
    hass, house, runtime = _setup(monkeypatch, [DOOR, LIGHT], proactive_context=context)
    house.set(DOOR, "off")
    house.set(LIGHT, "off")

    async def scenario() -> None:
        stop = runtime.async_start()
        # One burst, no loop turn in between: one batch.
        house.set(DOOR, "on")
        house.set(LIGHT, "on")
        house.set(DOOR, "off")
        house.set(LIGHT, "off")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert [(entity, state, previous) for entity, state, previous, _ in context.seen] == [
        (DOOR, "on", "off"),
        (LIGHT, "on", "off"),
        (DOOR, "off", "on"),
        (LIGHT, "off", "on"),
    ]
    # The rest of the house is the house at that moment, not after the batch.
    assert [house_view for *_, house_view in context.seen] == [
        {DOOR: "on", LIGHT: "off"},
        {DOOR: "on", LIGHT: "on"},
        {DOOR: "off", LIGHT: "on"},
        {DOOR: "off", LIGHT: "off"},
    ]


def test_events_still_queued_behind_the_batch_do_not_leak_into_it(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_BATCH_EVENTS", 2)
    context = _RecordingContext()
    hass, house, runtime = _setup(monkeypatch, [DOOR, LIGHT], proactive_context=context)
    house.set(DOOR, "off")
    house.set(LIGHT, "off")

    async def scenario() -> None:
        stop = runtime.async_start()
        house.set(DOOR, "on")
        house.set(DOOR, "off")
        house.set(LIGHT, "on")  # queued behind the first batch
        house.set(DOOR, "on")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert [house_view for *_, house_view in context.seen] == [
        {DOOR: "on", LIGHT: "off"},
        {DOOR: "off", LIGHT: "off"},
        {DOOR: "off", LIGHT: "on"},
        {DOOR: "on", LIGHT: "on"},
    ]


def test_person_leaving_and_returning_in_one_batch_makes_both_transitions(monkeypatch):
    monitor = SimpleNamespace(
        async_process_person_transition=AsyncMock(return_value=()),
        async_process_value_change=AsyncMock(return_value=()),
    )
    hass, house, runtime = _setup(monkeypatch, [PERSON], monitor_runtime=monitor)
    house.set(PERSON, "home")

    async def scenario() -> None:
        stop = runtime.async_start()
        house.set(PERSON, "not_home")
        house.set(PERSON, "home")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    transitions = [
        call.args for call in monitor.async_process_person_transition.await_args_list
    ]
    assert transitions == [(PERSON, "home", "not_home"), (PERSON, "not_home", "home")]
    occurred = [
        call.kwargs["occurred_at"]
        for call in monitor.async_process_person_transition.await_args_list
    ]
    assert occurred == sorted(occurred) and len(set(occurred)) == 2


def test_value_changes_report_each_events_own_value(monkeypatch):
    monitor = SimpleNamespace(
        async_process_person_transition=AsyncMock(return_value=()),
        async_process_value_change=AsyncMock(return_value=()),
    )
    hass, house, runtime = _setup(monkeypatch, [TEMP], monitor_runtime=monitor)
    house.set(TEMP, "20.0")

    async def scenario() -> None:
        stop = runtime.async_start()
        house.set(TEMP, "unavailable")
        house.set(TEMP, "25.0")
        house.set(TEMP, "21.0")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    values = [call.args for call in monitor.async_process_value_change.await_args_list]
    assert values == [(TEMP, 25.0), (TEMP, 21.0)]


def test_effect_monitor_observes_every_intermediate_state(monkeypatch):
    hass, house, runtime = _setup(monkeypatch, [LIGHT])
    effects = runtime._runtime_data.effect_monitor
    observed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        effects, "observe",
        lambda entity_id, state: observed.append((entity_id, state)),
    )
    house.set(LIGHT, "off")

    async def scenario() -> None:
        # 7.9.6: the effect monitor is a consumer while an effect is pending.
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        stop = runtime.async_start()
        house.set(LIGHT, "on")
        house.set(LIGHT, "off")
        await _settle(hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert observed == [(LIGHT, "on"), (LIGHT, "off")]


class _ThermalRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, dict[str, str]]] = []

    async def async_observe_states(self, entities: Any, *, occurred_at: datetime) -> None:
        self.calls.append((occurred_at, {item.entity_id: item.state for item in entities}))


def test_thermal_tracker_sees_the_house_at_each_event_time(monkeypatch):
    tracker = _ThermalRecorder()
    hass, house, runtime = _setup(monkeypatch, [WINDOW, TEMP], thermal_tracker=tracker)
    house.set(WINDOW, "off", device_class="window")
    house.set(TEMP, "19.0", device_class="temperature")

    async def scenario() -> None:
        stop = runtime.async_start()
        house.set(WINDOW, "on", device_class="window")
        house.set(TEMP, "19.5", device_class="temperature")
        house.set(WINDOW, "off", device_class="window")
        house.set(TEMP, "22.0", device_class="temperature")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert [view for _, view in tracker.calls] == [
        {WINDOW: "on", TEMP: "19.0"},
        {WINDOW: "on", TEMP: "19.5"},
        {WINDOW: "off", TEMP: "19.5"},
        {WINDOW: "off", TEMP: "22.0"},
    ]
    stamps = [stamp for stamp, _ in tracker.calls]
    assert stamps == sorted(stamps)


def test_attributes_and_derived_fields_follow_the_event_state(monkeypatch):
    seen: list[tuple[str, str | None, Any]] = []

    async def _observe(entity: EntitySnapshot, previous: Any, entities: Any) -> None:
        seen.append((entity.state, entity.unit, entity.attributes.get("brightness")))

    context = SimpleNamespace(async_observe_state=_observe)
    hass, house, runtime = _setup(monkeypatch, [LIGHT], proactive_context=context)
    house.set(LIGHT, "off")

    async def scenario() -> None:
        stop = runtime.async_start()
        house.set(LIGHT, "on", brightness=40)
        house.set(LIGHT, "on", brightness=255)
        house.set(LIGHT, "off")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert seen == [("on", None, 40), ("on", None, 255), ("off", None, None)]


def test_entity_created_within_a_batch_is_absent_before_its_event(monkeypatch):
    context = _RecordingContext()
    hass, house, runtime = _setup(monkeypatch, [DOOR, LIGHT], proactive_context=context)
    house.set(DOOR, "off")

    async def scenario() -> None:
        stop = runtime.async_start()
        house.set(DOOR, "on")
        house.set(LIGHT, "on")  # first appearance: old_state is None
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert [house_view for *_, house_view in context.seen] == [
        {DOOR: "on"},
        {DOOR: "on", LIGHT: "on"},
    ]


def test_direct_entry_uses_the_events_state_not_the_current_one(monkeypatch):
    context = _RecordingContext()
    hass, house, runtime = _setup(monkeypatch, [DOOR], proactive_context=context)
    house.set(DOOR, "off")
    opened = SimpleNamespace(
        data={
            "entity_id": DOOR,
            "old_state": State(DOOR, "off"),
            "new_state": State(DOOR, "on"),
        },
        time_fired=T0,
    )
    hass.states._states[DOOR] = State(DOOR, "off")  # closed again meanwhile

    asyncio.run(runtime.async_handle_state_changed(opened))
    assert [(entity, state) for entity, state, *_ in context.seen] == [(DOOR, "on")]


def test_night_opening_closed_in_the_same_batch_is_still_signalled(monkeypatch):
    # The notice itself is unchanged; 7.9.4 saw the window closed already.
    agent = SimpleNamespace(async_signal=AsyncMock())
    hass, house, runtime = _setup(monkeypatch, [WINDOW], proactive_agent=agent)
    runtime._entry.options[CONF_AGENT_EVENT_CATEGORIES] = "opening_while_away"
    hass.states._states[WINDOW] = State(WINDOW, "off", {"device_class": "window"})

    def _fire(state: str, old: str, hour: int, minute: int) -> None:
        new = State(WINDOW, state, {"device_class": "window"})
        hass.states._states[WINDOW] = new
        hass.bus.fire(SimpleNamespace(
            data={
                "entity_id": WINDOW,
                "old_state": State(WINDOW, old, {"device_class": "window"}),
                "new_state": new,
            },
            time_fired=datetime(2026, 9, 1, hour, minute, tzinfo=timezone.utc),
        ))

    async def scenario() -> None:
        stop = runtime.async_start()
        _fire("on", "off", 23, 0)
        _fire("off", "on", 23, 1)
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    agent.async_signal.assert_awaited_once()
    payload = agent.async_signal.await_args.args[0]
    assert payload["source_entity_id"] == WINDOW
    assert payload["expected_source_state"] == "on"


def test_failing_snapshot_build_neither_ends_the_worker_nor_hides_it(monkeypatch, caplog):
    # 7.9.5 still lost the batch the failed build was for ("2 state changes
    # not evaluated"); 7.9.6 keeps it queued and evaluates it on the retry.
    monkeypatch.setattr(event_runtime, "MAX_BATCH_EVENTS", 2)
    context = _RecordingContext()
    hass, house, runtime = _setup(monkeypatch, [DOOR], proactive_context=context)
    house.set(DOOR, "off")
    build = event_runtime.build_entity_snapshots
    calls: list[int] = []

    def _flaky(*args: Any) -> list[EntitySnapshot]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Registry nicht bereit")
        return build(*args)

    monkeypatch.setattr(event_runtime, "build_entity_snapshots", _flaky)

    async def scenario() -> None:
        stop = runtime.async_start()
        with caplog.at_level(logging.ERROR, logger=event_runtime.__name__):
            house.set(DOOR, "on")
            house.set(DOOR, "off")
            house.set(DOOR, "on")  # second batch, same worker
            await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert len(hass._tasks) == 1
    assert [(entity, state) for entity, state, *_ in context.seen] == [
        (DOOR, "on"), (DOOR, "off"), (DOOR, "on"),
    ]
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "3 state changes stay queued" in errors[0].getMessage()


def test_a_worker_surviving_its_cancellation_never_drains_a_new_queue(monkeypatch):
    hass, house, runtime = _setup(monkeypatch, [DOOR])
    # An undeclared future consumer is handled conservatively, without making
    # a configured category house-wide.
    runtime._runtime_data.proactive_context = SimpleNamespace(
        enabled="legacy", async_observe_state=AsyncMock()
    )
    house.set(DOOR, "off")
    processed: list[str] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def _process(raw_event: Any, entities: Any, by_id: Any) -> None:
            processed.append(raw_event.data["new_state"].state)
            if raw_event.data["new_state"].state == "hangs":
                # A callee that swallows the cancellation (e.g. a shielded call).
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    await gate.wait()

        monkeypatch.setattr(runtime, "_async_process_state_changed", _process)
        stop = runtime.async_start()
        house.set(DOOR, "hangs")
        house.set(DOOR, "lost")  # still queued when the runtime stops
        for _ in range(3):
            await asyncio.sleep(0)
        stop()
        await asyncio.sleep(0)
        restarted_stop = runtime.async_start()
        for state in ("1", "2", "3"):
            house.set(DOOR, state)
        for _ in range(3):
            await asyncio.sleep(0)
        gate.set()
        await _settle(hass)
        restarted_stop()

    asyncio.run(scenario())
    assert processed == ["hangs", "1", "2", "3"]


def test_fixed_selection_filter_is_a_set_lookup_and_follows_option_changes():
    class _CountingList(list):  # type: ignore[type-arg]
        contains = 0

        def __contains__(self, item: object) -> bool:
            type(self).contains += 1
            return super().__contains__(item)

    selected = _CountingList(f"sensor.s_{index}" for index in range(3000))
    entry = ConfigEntry(options={CONF_SELECTED_ENTITIES: selected})
    hass = HomeAssistant()
    check = hass_entities.SelectedEntityFilter(hass, entry)
    for index in range(10_000):
        entity_id = f"sensor.s_{index}"
        assert check(entity_id) is hass_entities.is_selected_entity(hass, entry, entity_id)
    # Only the reference rule above scanned the list.
    assert _CountingList.contains == 10_000
    entry.options[CONF_SELECTED_ENTITIES] = ["light.neu"]
    assert check("light.neu") is True
    assert check("sensor.s_1") is False


def test_runtime_uses_the_set_filter_for_every_house_event(monkeypatch):
    hass = HomeAssistant()
    hass.bus = _Bus()
    selected = [f"sensor.s_{index}" for index in range(3000)]
    # 7.9.6: a selected event is queued only for an active consumer.
    runtime = SituationRuntime(
        hass, ConfigEntry(options={
            CONF_SELECTED_ENTITIES: selected,
            CONF_AGENT_EVENT_CATEGORIES: "device_unavailable",
        }),
        HomeIntentRuntimeData(),
    )
    def _linear(*_args: Any) -> bool:
        raise AssertionError("linear check of the stored selection")

    monkeypatch.setattr(hass_entities, "is_selected_entity", _linear)
    monkeypatch.setattr(event_runtime, "is_selected_entity", _linear, raising=False)
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", lambda *_: [])

    async def scenario() -> int:
        stop = runtime.async_start()
        for index in range(5000):
            hass.bus.fire(SimpleNamespace(data={
                "entity_id": f"sensor.fremd_{index}",
                "old_state": None, "new_state": State("x", "1"),
            }))
        hass.bus.fire(SimpleNamespace(data={
            "entity_id": "sensor.s_2999", "old_state": State("x", "1"),
            "new_state": State("x", "unavailable"),
        }))
        pending: Any = runtime._pending
        queued = 0 if pending is None else len(pending)
        await _settle(hass)
        stop()
        return queued

    assert asyncio.run(scenario()) == 1


def test_snapshot_at_state_keeps_registry_data_and_takes_state_fields():
    base = EntitySnapshot(
        TEMP, "Temperatur", "sensor", "19.0", area_id="wohnzimmer",
        area_name="Wohnzimmer", aliases=("Raumtemperatur",), unit="°C",
        device_class="temperature", attributes={"unit_of_measurement": "°C"},
    )
    changed = hass_entities.snapshot_at_state(base, State(TEMP, "unavailable", {
        "friendly_name": "Temperatur Wohnzimmer",
    }))
    assert changed.state == "unavailable"
    assert changed.friendly_name == "Temperatur Wohnzimmer"
    assert changed.unit is None and changed.device_class is None
    assert (changed.area_id, changed.area_name, changed.aliases) == (
        "wohnzimmer", "Wohnzimmer", ("Raumtemperatur",),
    )
    # A state without attributes changes only the value.
    bare = hass_entities.snapshot_at_state(base, SimpleNamespace(state="21.0"))
    assert (bare.state, bare.unit, bare.friendly_name) == ("21.0", "°C", "Temperatur")
    assert hass_entities.snapshot_at_state(base, SimpleNamespace()) is base
