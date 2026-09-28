"""Behavioral V12 harness: fake Home Assistant *ports*, real V10 execution.

Every device action a test observes went through the production path:
``V10ProposalRunner`` -> ``planner.materialize_target_states`` (Validator,
ExecutionPolicy) -> ``ExecutionCoordinator`` -> ``PlanExecutor`` ->
``service_executor.async_execute_service_plan`` -> ``hass.services.async_call``.
``ServiceSink`` instruments that last call, so ``sink.device_calls`` counts
real device writes; notification/TTS/satellite calls are counted separately.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping

import _ha_stub

_ha_stub.install()

from homeintent.context_forecast import HabitEvidence  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_coordinator import ExecutionCoordinator  # noqa: E402
from homeintent.experience_store import ExperienceStore  # noqa: E402
from homeintent.goal_run import GoalRunStore  # noqa: E402
from homeintent.learning_manager import LearningManager  # noqa: E402
from homeintent.learning_policy import LearningMode, LearningPolicy  # noqa: E402
from homeintent.model_registry import ModelRegistry  # noqa: E402
from homeintent.predictive_house_model import PredictiveHouseModel  # noqa: E402
from homeintent.proactive_engine import (  # noqa: E402
    DeliveryReceipt,
    OutgoingMessage,
    ProactiveConfig,
    ProactiveContextEngine,
)
from homeintent.proactive_execution import V10ProposalRunner  # noqa: E402
from homeintent.proactive_model import (  # noqa: E402
    CommunicationChannel,
    ProactiveSituation,
    PushActionBinding,
    RecipientContext,
    RoomEvidenceClass,
    RoomPresenceResult,
    SatelliteRecord,
)
from homeintent.proactive_store import GenerationalJsonFile  # noqa: E402
from homeintent.room_presence import SatelliteRegistry  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import async_execute_service_plan  # noqa: E402


NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
DEVICE_DOMAINS = frozenset({
    "light", "switch", "fan", "cover", "lock", "climate", "homeassistant",
    "alarm_control_panel", "siren", "valve", "media_player", "vacuum",
})
_EFFECT = {
    "turn_on": "on", "turn_off": "off", "close_cover": "closed",
    "open_cover": "open", "lock": "locked", "unlock": "unlocked",
}


class ServiceSink:
    """Instrumented ``hass.services``; applies the effect unless told not to."""

    def __init__(self, states: dict[str, EntitySnapshot]) -> None:
        self.states = states
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail_entities: set[str] = set()
        self.no_effect_entities: set[str] = set()

    async def async_call(self, domain: str, service: str, data: dict[str, Any], blocking: bool = False, context: Any = None) -> None:
        self.calls.append((domain, service, dict(data)))
        raw = data.get("entity_id")
        targets = [raw] if isinstance(raw, str) else list(raw or [])
        if any(item in self.fail_entities for item in targets):
            raise RuntimeError("Dienst abgelehnt")
        effect = _EFFECT.get(service)
        if effect is None:
            return
        for entity_id in targets:
            if entity_id in self.states and entity_id not in self.no_effect_entities:
                self.states[entity_id] = replace(self.states[entity_id], state=effect)

    def has_service(self, domain: str, service: str) -> bool:
        return True

    @property
    def device_calls(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [item for item in self.calls if item[0] in DEVICE_DOMAINS]


@dataclass
class Scheduled:
    key: str
    at: datetime
    callback: Callable[[], Awaitable[None]]


@dataclass
class FakePorts:
    states: dict[str, EntitySnapshot]
    recipients: dict[str, RecipientContext] = field(default_factory=dict)
    rooms: dict[str, RoomPresenceResult] = field(default_factory=dict)
    satellite_records: list[SatelliteRecord] = field(default_factory=list)
    admins: set[str] = field(default_factory=lambda: {"philipp"})
    household: dict[str, str] = field(default_factory=dict)  # person -> state
    local_offset: timedelta = timedelta(hours=2)
    clock: datetime = NOW
    delivered: list[OutgoingMessage] = field(default_factory=list)
    scheduled: dict[str, Scheduled] = field(default_factory=dict)
    habits: dict[str, HabitEvidence] = field(default_factory=dict)
    active_subjects: frozenset[str] = frozenset()
    delivery_fails: bool = False
    # notify target -> HA device registry id (the authoritative binding).
    companion_devices: dict[str, str] = field(default_factory=lambda: {
        "notify.mobile_app_philipp": "phone_philipp",
        "notify.mobile_app_anna": "phone_anna",
    })
    issued: dict[tuple[str, str], PushActionBinding] = field(default_factory=dict)
    frozen_rooms: set[str] = field(default_factory=set)

    def now(self) -> datetime:
        return self.clock

    def local_now(self) -> datetime:
        return self.clock.astimezone(timezone(self.local_offset))

    def fresh_entities(self) -> Mapping[str, EntitySnapshot]:
        return dict(self.states)

    def nobody_home(self) -> bool | None:
        if not self.household:
            return None
        return all(state != "home" for state in self.household.values())

    def recipients_for(self, situation: ProactiveSituation) -> tuple[RecipientContext, ...]:
        values = []
        for recipient in self.recipients.values():
            person = recipient.person_id
            home = self.household.get(person or "") == "home" if person else None
            values.append(replace(recipient, home=home))
        if situation.owner_user_id is not None:
            values = [item for item in values if item.user_id == situation.owner_user_id]
        home_values = [item for item in values if item.home]
        return tuple(home_values or values)

    def room_for(self, person_id: str | None) -> RoomPresenceResult | None:
        """Configured rooms behave like a live sensor re-observed now, unless
        the person is in ``frozen_rooms`` (evidence returned exactly as given)."""
        room = self.rooms.get(person_id or "")
        if room is None or person_id in self.frozen_rooms or room.area_id is None:
            return room
        return replace(room, observed_at=self.clock, valid_until=self.clock + timedelta(minutes=10))

    def others_home(self, person_id: str | None) -> bool:
        return any(state == "home" for person, state in self.household.items() if person != person_id)

    def satellites(self) -> SatelliteRegistry:
        return SatelliteRegistry(self.satellite_records)

    def owner_known(self, user_id: str) -> bool:
        return user_id in self.recipients

    def person_for(self, user_id: str | None) -> str | None:
        recipient = self.recipients.get(user_id or "")
        return recipient.person_id if recipient is not None else None

    def active_goal_subjects(self) -> frozenset[str]:
        return self.active_subjects

    def habit_evidence(self, situation: ProactiveSituation) -> HabitEvidence | None:
        return self.habits.get(situation.dedupe_key)

    async def async_is_admin(self, user_id: str | None) -> bool:
        return user_id in self.admins

    async def async_deliver(self, message: OutgoingMessage) -> DeliveryReceipt:
        if self.delivery_fails:
            return DeliveryReceipt((), None, ("delivery_failed",))
        self.delivered.append(message)
        channels = message.decision.channels or (message.decision.channel,)
        origin = None
        if message.decision.satellite_entity_id is not None:
            record = next((item for item in self.satellite_records
                           if item.entity_id == message.decision.satellite_entity_id), None)
            origin = record.device_id if record is not None else None
        bindings: list[PushActionBinding] = []
        user = message.decision.recipient_user_id
        if message.proposal_id is not None and user is not None:
            for target in message.decision.push_target_ids:
                device = self.companion_devices.get(target)
                if device is None:
                    continue
                binding = PushActionBinding(f"t{secrets.token_hex(8)}", user, device)
                bindings.append(binding)
                self.issued[(message.proposal_id, user)] = binding
        return DeliveryReceipt(tuple(channels), origin, (), tuple(bindings))

    def push_action(self, choice: str, user: str = "philipp", proposal_id: str | None = None) -> tuple[str, str]:
        """(action id, device id) exactly as that user's Companion app sends it."""
        proposal = proposal_id or next(
            item.proposal_id for item in reversed(self.delivered) if item.proposal_id
        )
        binding = self.issued[(proposal, user)]
        return f"HOMEINTENT_V12_{choice}_{proposal}_{binding.token}", binding.device_id

    def schedule(self, key: str, at: datetime, callback: Callable[[], Awaitable[None]]) -> None:
        self.scheduled[key] = Scheduled(key, at, callback)

    def cancel(self, key: str) -> None:
        self.scheduled.pop(key, None)

    async def advance(self, delta: timedelta) -> None:
        """Move the clock and fire due callbacks in time order (event-driven)."""
        self.clock = self.clock + delta
        while True:
            due = sorted((item for item in self.scheduled.values() if item.at <= self.clock),
                         key=lambda item: item.at)
            if not due:
                return
            item = due[0]
            self.scheduled.pop(item.key, None)
            await item.callback()


def entity(entity_id: str, name: str, state: str, *, area: str | None = None,
           area_name: str | None = None, device_class: str | None = None,
           last_changed: datetime | None = None, capabilities: frozenset[str] = frozenset()) -> EntitySnapshot:
    domain = entity_id.partition(".")[0]
    caps = capabilities or (
        frozenset({"TURN_ON", "TURN_OFF"}) if domain in {"light", "switch", "fan"} else frozenset()
    )
    return EntitySnapshot(
        entity_id, name, domain, state, area_id=area, area_name=area_name,
        device_class=device_class, capabilities=caps, last_changed=last_changed,
    )


@dataclass
class World:
    engine: ProactiveContextEngine
    ports: FakePorts
    sink: ServiceSink
    goal_runs: GoalRunStore
    learning: LearningManager
    tmp: Path

    def entities(self) -> tuple[EntitySnapshot, ...]:
        return tuple(self.ports.states.values())

    async def change(self, entity_id: str, state: str) -> None:
        previous = self.ports.states[entity_id].state
        self.ports.states[entity_id] = replace(
            self.ports.states[entity_id], state=state, last_changed=self.ports.clock,
        )
        await self.engine.async_observe_state(
            self.ports.states[entity_id], previous, self.entities(),
            nobody_home=self.ports.nobody_home(),
        )

    async def person(self, person_id: str, state: str) -> None:
        self.ports.household[person_id] = state
        if person_id in self.ports.states:
            await self.change(person_id, state)

    def spoken(self) -> list[OutgoingMessage]:
        return [item for item in self.ports.delivered
                if item.decision.satellite_entity_id is not None]

    def pushes(self) -> list[OutgoingMessage]:
        return [item for item in self.ports.delivered if item.decision.push_target_ids]


def build_world(
    tmp: Path,
    states: list[EntitySnapshot],
    *,
    config: ProactiveConfig | None = None,
    options: Mapping[str, object] | None = None,
    **ports_kwargs: Any,
) -> World:
    by_id = {item.entity_id: item for item in states}
    sink = ServiceSink(by_id)
    hass = SimpleNamespace(services=sink)
    ports = FakePorts(by_id, **ports_kwargs)
    policy = replace(LearningPolicy(
        learning_mode=LearningMode.ASK, predictive_models_enabled=True,
        habit_discovery_enabled=True,
    ), minimum_model_samples=2, usable_model_samples=2)
    house = PredictiveHouseModel(policy)
    learning = LearningManager(
        ExperienceStore(tmp / "experiences.json", policy),
        ModelRegistry(tmp / "models.json", policy), house, policy,
    )
    goal_runs = GoalRunStore(tmp / "runs.json")

    async def _learn(run) -> None:
        await learning.async_observe_goal_run(run)

    goal_runs.add_append_listener(_learn)
    resolved_options: Mapping[str, object] = dict(options or {})

    async def refresh() -> list[EntitySnapshot]:
        return list(by_id.values())

    async def execute(plan: ServiceCallPlan, fresh: list[EntitySnapshot], confirmed: bool,
                      user_id: str | None, is_admin: bool):
        return await async_execute_service_plan(
            hass,  # type: ignore[arg-type]
            plan, fresh, resolved_options, is_admin=is_admin, user_id=user_id,
            confirmed=confirmed,
        )

    async def verify(entity_id: str, expected: str) -> bool:
        current = by_id.get(entity_id)
        return current is not None and (current.state == expected or current.state == expected.partition("=")[2])

    runner = V10ProposalRunner(
        refresh=refresh, execute_service=execute, verify=verify,
        coordinator=ExecutionCoordinator(), goal_runs=goal_runs, options=resolved_options,
    )
    engine = ProactiveContextEngine(
        ports, runner, config=config or ProactiveConfig(enabled=True),
        predictive_house=house, storage=GenerationalJsonFile(tmp / "proactive.json"),
    )
    return World(engine, ports, sink, goal_runs, learning, tmp)


def philipp(*, push: tuple[str, ...] = ("notify.mobile_app_philipp",)) -> RecipientContext:
    return RecipientContext("philipp", "person.philipp", True, push)


def anna(*, push: tuple[str, ...] = ("notify.mobile_app_anna",)) -> RecipientContext:
    return RecipientContext("anna", "person.anna", True, push)


def exact_room(person: str, area: str) -> RoomPresenceResult:
    return RoomPresenceResult(person, area, RoomEvidenceClass.EXACT, NOW, NOW + timedelta(minutes=10))


def ambiguous_room(person: str) -> RoomPresenceResult:
    return RoomPresenceResult(person, None, RoomEvidenceClass.AMBIGUOUS, NOW, None)


def living_satellite() -> SatelliteRecord:
    return SatelliteRecord("assist_satellite.wohnzimmer", "living_room", "dev_living")


def garage_states(*, open_since: datetime | None = None) -> list[EntitySnapshot]:
    return [
        entity("cover.garage", "Garage", "closed", area="garage", area_name="Garage",
               device_class="garage", last_changed=open_since),
        entity("light.kitchen", "Küchenlicht", "off", area="kitchen", area_name="Küche"),
        entity("light.living", "Wohnzimmerlicht", "off", area="living_room", area_name="Wohnzimmer"),
        entity("light.living_floor", "Stehlampe", "off", area="living_room", area_name="Wohnzimmer"),
        entity("lock.front", "Haustür", "locked", area="hall", area_name="Flur"),
        entity("binary_sensor.smoke_hall", "Rauchmelder Flur", "off", area="hall",
               area_name="Flur", device_class="smoke"),
        entity("binary_sensor.leak_bath", "Wassermelder Bad", "off", area="bath",
               area_name="Bad", device_class="moisture"),
        entity("person.philipp", "Philipp", "home"),
        entity("person.anna", "Anna", "not_home"),
    ]


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(_wrap(coro))


async def _wrap(coro: Awaitable[Any]) -> Any:
    return await coro


CHANNEL = CommunicationChannel
