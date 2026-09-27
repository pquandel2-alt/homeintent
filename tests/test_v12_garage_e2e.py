"""V12 section 49: the mandatory Garage end-to-end scenario."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from v12_harness import (
    NOW,
    build_world,
    exact_room,
    garage_states,
    living_satellite,
    philipp,
)

from homeintent.experience import extract_goal_run_experiences
from homeintent.goal_run import EffectEvidenceState, GoalRunStatus
from homeintent.proactive_model import (
    CommunicationChannel,
    OpportunityOutcome,
    PriorityLevel,
    PrivacyLevel,
    ProposalState,
    SituationKind,
    SituationState,
)


def _world(tmp_path):
    return build_world(
        tmp_path, garage_states(),
        recipients={"philipp": philipp()},
        rooms={"person.philipp": exact_room("person.philipp", "living_room")},
        satellite_records=[living_satellite()],
        household={"person.philipp": "home", "person.anna": "not_home"},
    )


def test_garage_open_fifteen_minutes_asks_by_voice_then_executes_through_v10(tmp_path):
    world = _world(tmp_path)

    async def scenario():
        await world.change("cover.garage", "open")
        situation = world.engine.situations.get("entry_left_open:cover.garage")
        assert situation is not None
        assert situation.kind is SituationKind.ENTRY_LEFT_OPEN
        assert situation.state is SituationState.ACTIVE
        # Below the 15-minute threshold: nothing is said, one check is scheduled.
        assert world.ports.delivered == []
        assert list(world.ports.scheduled) == ["check:entry_left_open:cover.garage"]

        await world.ports.advance(timedelta(minutes=15))
        assert len(world.ports.delivered) == 1
        message = world.ports.delivered[0]
        assert message.text == "Die Garage ist noch offen. Soll ich sie schließen?"
        assert message.decision.channel is CommunicationChannel.VOICE
        assert message.decision.satellite_entity_id == "assist_satellite.wohnzimmer"
        assert message.decision.continue_conversation is True
        assert message.priority is PriorityLevel.IMPORTANT
        assert message.proposal_id is not None
        proposal = world.engine.proposals.get(message.proposal_id)
        assert proposal is not None and proposal.state is ProposalState.PENDING
        assert proposal.privacy_level is PrivacyLevel.HOUSEHOLD
        assert proposal.origin_device_id == "dev_living"
        record = world.engine.history.records()[-1]
        assert record.decision is OpportunityOutcome.COMMUNICATE
        assert "exact_room_unique_satellite" in record.reasons
        # Asking is not acting.
        assert world.sink.device_calls == []

        reply = await world.engine.async_handle_reply(
            "Ja.", user_id="philipp", device_id="dev_living", is_admin=True,
        )
        assert reply is not None and reply.handled
        assert "geschlossen" in reply.speech
        assert world.sink.device_calls == [
            ("cover", "close_cover", {"entity_id": "cover.garage"}),
        ]
        assert world.engine.proposals.get(message.proposal_id).state is ProposalState.EXECUTED
        runs = await world.goal_runs.async_list()
        assert len(runs) == 1
        run = runs[0]
        assert run.status is GoalRunStatus.SUCCESS
        assert run.steps[0].service_accepted is True
        assert run.steps[0].operator_id == "COVER_CLOSE_COVER"
        assert any(item.startswith("proactive:proposal:") for item in run.evidence)
        experiences = extract_goal_run_experiences(run)
        assert experiences[0].effect.evidence_state is EffectEvidenceState.VERIFIED_SUCCESS
        stored = await world.learning.experiences.async_list()
        assert [item.run_id for item in stored] == [run.run_id]
        # A replayed "Ja" cannot execute twice.
        again = await world.engine.async_handle_reply(
            "Ja", user_id="philipp", device_id="dev_living", is_admin=True,
        )
        assert again is None
        assert len(world.sink.device_calls) == 1

    asyncio.run(scenario())


def test_garage_closed_before_threshold_never_prompts(tmp_path):
    world = _world(tmp_path)

    async def scenario():
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=5))
        await world.change("cover.garage", "closed")
        await world.ports.advance(timedelta(minutes=20))
        assert world.ports.delivered == []
        assert world.sink.device_calls == []
        situation = world.engine.situations.get("entry_left_open:cover.garage")
        assert situation is not None and situation.state is SituationState.RESOLVED

    asyncio.run(scenario())


def test_repeated_open_events_keep_one_situation(tmp_path):
    world = _world(tmp_path)

    async def scenario():
        await world.change("cover.garage", "open")
        first = world.engine.situations.get("entry_left_open:cover.garage")
        for _ in range(20):
            await world.change("cover.garage", "open")
        assert len(world.engine.situations) == 1
        assert world.engine.situations.get("entry_left_open:cover.garage").situation_id == first.situation_id

    asyncio.run(scenario())


def test_stale_accept_after_garage_closed_executes_nothing(tmp_path):
    world = _world(tmp_path)

    async def scenario():
        world.ports.clock = NOW
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))
        proposal_id = world.ports.delivered[0].proposal_id
        # Closed manually; the proposal is cancelled by resolution.
        await world.change("cover.garage", "closed")
        assert world.engine.proposals.get(proposal_id).state is ProposalState.CANCELLED
        reply = await world.engine.async_handle_reply(
            "Ja", user_id="philipp", device_id="dev_living", is_admin=True,
        )
        assert reply is None
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_slow_garage_effect_answers_at_once_and_verifies_in_background(tmp_path):
    """F17: "Ja" is answered immediately; the effect is still verified and
    recorded, and the proposal only reports if it fails."""
    from dataclasses import replace

    world = _world(tmp_path)
    world.engine.config = replace(world.engine.config, reply_budget=timedelta(0))
    original_execute = world.engine.runner.async_execute

    async def slow_execute(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await original_execute(*args, **kwargs)

    world.engine.runner.async_execute = slow_execute

    async def scenario():
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))
        proposal_id = world.ports.delivered[0].proposal_id
        reply = await world.engine.async_handle_reply(
            "Ja.", user_id="philipp", device_id="dev_living", is_admin=True,
        )
        assert reply is not None and reply.handled
        assert reply.speech.startswith("In Ordnung, ich schließe die Garage jetzt")
        assert "melde mich nur, falls es nicht klappt" in reply.speech
        await asyncio.sleep(0.2)
        assert world.sink.device_calls == [
            ("cover", "close_cover", {"entity_id": "cover.garage"}),
        ]
        assert world.engine.proposals.get(proposal_id).state is ProposalState.EXECUTED
        runs = await world.goal_runs.async_list()
        assert runs[-1].status is GoalRunStatus.SUCCESS
        # Success is not announced a second time.
        assert len(world.ports.delivered) == 1

    asyncio.run(scenario())
