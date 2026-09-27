"""V12 persistence, restart safety, corruption handling, event storm."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import timedelta

from v12_harness import (
    NOW,
    build_world,
    entity,
    exact_room,
    garage_states,
    living_satellite,
    philipp,
)

from homeintent.proactive_engine import ProactiveConfig
from homeintent.proactive_model import (
    AutoOperator,
    PermissionCondition,
    ProposalState,
    SituationKind,
    SituationState,
    StandingPermission,
)
from homeintent.proactive_session import MAX_PROPOSALS, ProposalStore
from homeintent.proactive_store import (
    MAX_HISTORY,
    MAX_SITUATIONS,
    GenerationalJsonFile,
    SituationStore,
)
from homeintent.standing_permission import MAX_PERMISSIONS, StandingPermissionStore


def _world(tmp_path, **kwargs):
    return build_world(
        tmp_path, garage_states(),
        recipients={"philipp": philipp()},
        rooms={"person.philipp": exact_room("person.philipp", "living_room")},
        satellite_records=[living_satellite()],
        household={"person.philipp": "home", "person.anna": "not_home"},
        **kwargs,
    )


def _restart(tmp_path, old, **kwargs):
    new = _world(tmp_path, **kwargs)
    new.ports.states.update(old.ports.states)
    new.ports.clock = old.ports.clock
    return new


def test_restart_restores_pending_proposal_without_any_action(tmp_path):
    world = _world(tmp_path)

    async def before():
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))

    asyncio.run(before())
    proposal_id = world.ports.delivered[0].proposal_id
    restarted = _restart(tmp_path, world)

    async def after():
        restored = await restarted.engine.async_restore()
        assert restored == 1
        assert restarted.sink.device_calls == []
        proposal = restarted.engine.proposals.get(proposal_id)
        assert proposal is not None and proposal.state is ProposalState.PENDING
        # Firing the post-restore re-check never executes and does not re-ask
        # within the communication cooldown.
        await restarted.ports.advance(timedelta(seconds=1))
        assert restarted.sink.device_calls == []
        assert restarted.ports.delivered == []
        # The restored proposal can still be answered explicitly.
        reply = await restarted.engine.async_handle_reply("Ja", user_id="philipp", device_id="dev_living", is_admin=True)
        assert reply is not None
        assert restarted.sink.device_calls == [("cover", "close_cover", {"entity_id": "cover.garage"})]

    asyncio.run(after())


def test_restart_revalidates_live_state_and_cancels_stale_proposals(tmp_path):
    world = _world(tmp_path)

    async def before():
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))

    asyncio.run(before())
    proposal_id = world.ports.delivered[0].proposal_id
    world.ports.states["cover.garage"] = replace(world.ports.states["cover.garage"], state="closed")
    restarted = _restart(tmp_path, world)

    async def after():
        await restarted.engine.async_restore()
        assert restarted.engine.proposals.get(proposal_id).state is ProposalState.CANCELLED
        situation = restarted.engine.situations.get("entry_left_open:cover.garage")
        assert situation.state is SituationState.RESOLVED
        reply = await restarted.engine.async_handle_reply("Ja", user_id="philipp", device_id="dev_living", is_admin=True)
        assert reply is None
        assert restarted.sink.device_calls == []

    asyncio.run(after())


def test_restart_expires_old_proposals_and_drops_unknown_recipients(tmp_path):
    world = _world(tmp_path)

    async def before():
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))

    asyncio.run(before())
    proposal_id = world.ports.delivered[0].proposal_id
    late = _restart(tmp_path, world)
    late.ports.clock = world.ports.clock + timedelta(hours=1)

    async def expired():
        await late.engine.async_restore()
        assert late.engine.proposals.get(proposal_id).state is ProposalState.EXPIRED
        assert late.sink.device_calls == []

    asyncio.run(expired())
    unknown = _restart(tmp_path, world)
    unknown.ports.recipients = {}

    async def dropped():
        await unknown.engine.async_restore()
        assert unknown.engine.proposals.get(proposal_id).state in {
            ProposalState.CANCELLED, ProposalState.EXPIRED,
        }

    asyncio.run(dropped())


def test_restart_with_permission_never_auto_executes_during_restore(tmp_path):
    config = ProactiveConfig(enabled=True, standing_permissions_enabled=True)
    world = _world(tmp_path, config=config)
    world.engine.permissions.add(StandingPermission(
        "perm_1", "philipp", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
        AutoOperator.LIGHT_TURN_OFF, ("light.living",), "living_room",
        (PermissionCondition.NOBODY_HOME,), NOW, NOW + timedelta(days=30), True,
    ))

    async def before():
        await world.change("light.living", "on")
        world.ports.household["person.philipp"] = "not_home"
        await world.change("person.philipp", "not_home")
        await world.engine.async_persist()

    asyncio.run(before())
    assert world.sink.device_calls == []  # the 5-minute window has not elapsed
    restarted = _restart(tmp_path, world, config=config)
    restarted.ports.household["person.philipp"] = "not_home"
    restarted.ports.clock = world.ports.clock + timedelta(minutes=10)

    async def after():
        await restarted.engine.async_restore()
        assert len(restarted.engine.permissions.active(restarted.ports.clock)) == 1
        await restarted.ports.advance(timedelta(seconds=1))
        assert restarted.sink.device_calls == []

    asyncio.run(after())


def test_corrupt_documents_fail_closed(tmp_path):
    path = tmp_path / "proactive.json"
    path.write_text("{not json", encoding="utf-8")
    world = _world(tmp_path)

    async def corrupt():
        assert await world.engine.async_restore() == 0
        assert world.engine.permissions.all() == ()

    asyncio.run(corrupt())
    path.write_text(json.dumps({"schema_version": 99, "permissions": {}}), encoding="utf-8")
    assert asyncio.run(world.engine.async_restore()) == 0
    good = StandingPermissionStore()
    good.add(StandingPermission(
        "perm_ok", "philipp", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
        AutoOperator.LIGHT_TURN_OFF, ("light.living",), "living_room",
        (PermissionCondition.NOBODY_HOME,), NOW, NOW + timedelta(days=1), True,
    ))
    document = good.to_dict()
    record = document["permissions"][0]
    for field, value in (
        ("confirmed", False), ("entity_ids", ["*"]), ("entity_ids", ["light.*"]),
        ("operator", "lock_unlock"), ("situation_kind", "garage"), ("expires_at", "never"),
        ("entity_ids", ["lock.front"]), ("max_executions_per_day", 10_000),
    ):
        tampered = {**document, "permissions": [{**record, field: value}]}
        assert StandingPermissionStore.from_dict(tampered).all() == (), field
    assert len(StandingPermissionStore.from_dict(document).all()) == 1
    assert StandingPermissionStore.from_dict({**document, "schema_version": 2}).all() == ()


def test_corrupt_proposal_is_never_restored(tmp_path):
    store = ProposalStore()
    from homeintent.proactive_model import (
        CommunicationChannel, PrivacyLevel, ProposedGoal, TargetState,
    )

    proposal = store.create(
        situation_id="sit", recipient_user_ids=("philipp",), recipient_person_id=None,
        proposed_goal=ProposedGoal((TargetState("cover.garage", "closed", "Garage"),), "x"),
        channel=CommunicationChannel.VOICE, privacy_level=PrivacyLevel.HOUSEHOLD,
        subject_label="die Garage", question="?", now=NOW,
    )
    document = store.to_dict()
    raw = document["proposals"][0]
    for field, value in (
        ("targets", [{"entity_id": "lock.front", "desired_state": "unlocked"}]),
        ("targets", [{"entity_id": "cover.garage", "desired_state": "closed", "service": "unlock"}]),
        ("proposal_id", "../../etc"), ("expires_at", 3), ("channel", "telepathy"),
    ):
        tampered = {**document, "proposals": [{**raw, field: value}]}
        restored = ProposalStore.from_dict(tampered)
        if field == "targets" and value[0].get("service"):
            # Unknown keys are ignored; the closed state set is all that counts.
            assert restored.get(proposal.proposal_id).proposed_goal.targets[0].desired_state == "closed"
            continue
        assert restored.all() == (), field


def test_stores_are_bounded(tmp_path):
    from homeintent.situation_detection import DetectionSignal
    from homeintent.proactive_model import PriorityLevel, PrivacyLevel, CommunicationChannel, ProposedGoal, TargetState

    situations = SituationStore()
    for index in range(MAX_SITUATIONS + 100):
        situations.apply(
            DetectionSignal(SituationKind.APPLIANCE_FINISHED, f"k{index}", (f"sensor.a{index}",),
                            None, True, NOW),
            now=NOW, priority_hint=PriorityLevel.INFO, privacy=PrivacyLevel.HOUSEHOLD,
        )
    assert len(situations) == MAX_SITUATIONS
    proposals = ProposalStore()
    for index in range(MAX_PROPOSALS + 20):
        proposals.create(
            situation_id=f"s{index}", recipient_user_ids=("philipp",), recipient_person_id=None,
            proposed_goal=ProposedGoal((TargetState("light.a", "off"),), ""),
            channel=CommunicationChannel.PUSH, privacy_level=PrivacyLevel.HOUSEHOLD,
            subject_label="x", question="?", now=NOW,
        )
    assert len(proposals.all()) == MAX_PROPOSALS
    permissions = StandingPermissionStore()
    # Distinct instructions: identical ones are renewed, not stored twice.
    for index in range(MAX_PERMISSIONS):
        permissions.add(StandingPermission(
            f"p{index}", "philipp", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
            AutoOperator.LIGHT_TURN_OFF, (f"light.a{index}",), None, (), NOW, NOW + timedelta(days=1), True,
        ))
    try:
        permissions.add(StandingPermission(
            "overflow", "philipp", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
            AutoOperator.LIGHT_TURN_OFF, ("light.overflow",), None, (), NOW, NOW + timedelta(days=1), True,
        ))
    except ValueError:
        pass
    assert len(permissions.all()) == MAX_PERMISSIONS
    world = _world(tmp_path)
    for index in range(MAX_HISTORY + 50):
        world.engine.record_external_communication(SituationKind.TIMER_FINISHED, f"T{index}", owner="native_timer")
    assert len(world.engine.history) == MAX_HISTORY


def test_older_async_write_cannot_overwrite_newer_state(tmp_path):
    storage = GenerationalJsonFile(tmp_path / "state.json")
    older = storage.next_generation()
    newer = storage.next_generation()
    assert storage.write_generation(newer, {"schema_version": 1, "value": "new"})
    assert not storage.write_generation(older, {"schema_version": 1, "value": "old"})
    assert storage.read()["value"] == "new"
    # Concurrent writers finishing out of order keep the newest generation.
    storage2 = GenerationalJsonFile(tmp_path / "state2.json")
    generations = [storage2.next_generation() for _ in range(20)]
    threads = [
        threading.Thread(target=storage2.write_generation,
                         args=(generation, {"schema_version": 1, "value": generation}))
        for generation in reversed(generations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert storage2.read()["value"] == generations[-1]


def test_persistence_runs_off_the_event_loop(tmp_path):
    storage = GenerationalJsonFile(tmp_path / "slow.json")
    original = storage._write
    loop_threads: set[int] = set()

    def slow_write(document):
        loop_threads.add(threading.get_ident())
        time.sleep(0.05)
        original(document)

    storage._write = slow_write  # type: ignore[method-assign]

    async def scenario():
        main = threading.get_ident()
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.005)
                ticks += 1

        await asyncio.gather(storage.async_write({"schema_version": 1}), ticker())
        assert main not in loop_threads
        assert ticks == 5

    asyncio.run(scenario())


def test_event_storm_is_filtered_early_and_bounded(tmp_path):
    states = garage_states() + [
        entity(f"sensor.noise_{index}", f"Sensor {index}", "1") for index in range(1000)
    ]
    world = build_world(
        tmp_path, states, recipients={"philipp": philipp()},
        household={"person.philipp": "home", "person.anna": "not_home"},
    )

    async def scenario():
        started = time.perf_counter()
        tasks_before = len(asyncio.all_tasks())
        for index in range(1000):
            await world.change(f"sensor.noise_{index}", str(index))
        elapsed = time.perf_counter() - started
        assert world.engine.counters.events_seen == 1000
        assert world.engine.counters.events_relevant == 0
        assert len(world.engine.situations) == 0
        assert world.ports.delivered == []
        assert world.ports.scheduled == {}
        assert len(asyncio.all_tasks()) == tasks_before
        assert not (tmp_path / "proactive.json").exists()
        assert elapsed < 5.0
        # A storm of *relevant* but identical events keeps one situation.
        for _ in range(1000):
            await world.change("cover.garage", "open")
        assert len(world.engine.situations) == 1
        assert len(world.ports.scheduled) == 1
        assert world.ports.delivered == []

    asyncio.run(scenario())
