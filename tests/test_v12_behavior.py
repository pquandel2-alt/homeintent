"""V12 behavioral E2E (sections 50-60) on the real engine and real V10 path."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from v12_harness import (
    NOW,
    anna,
    ambiguous_room,
    build_world,
    entity,
    exact_room,
    garage_states,
    living_satellite,
    philipp,
)

from homeintent.context_forecast import HabitEvidence
from homeintent.proactive_engine import ProactiveConfig
from homeintent.proactive_model import (
    AutoOperator,
    CommunicationChannel,
    PermissionCondition,
    PriorityLevel,
    ProposalChoice,
    ProposalState,
    ProposedGoal,
    SituationKind,
    SituationState,
    StandingPermission,
    TargetState,
)
from homeintent.proactive_policy import QuietHoursPolicy, parse_quiet_window
from homeintent.proactive_execution import ProactiveExecutionStatus
from homeintent.situation_detection import (
    DetectorConfig,
    HabitCandidate,
    SituationDetector,
    habit_signal,
)


def _home_world(tmp_path, *, room=None, others_home=False, config=None, states=None):
    household = {"person.philipp": "home", "person.anna": "home" if others_home else "not_home"}
    return build_world(
        tmp_path, states or garage_states(),
        config=config,
        recipients={"philipp": philipp(), **({"anna": anna()} if others_home else {})},
        rooms={"person.philipp": room or exact_room("person.philipp", "living_room")},
        satellite_records=[living_satellite()],
        household=household,
    )


async def _open_garage(world, minutes: int = 15) -> None:
    await world.change("cover.garage", "open")
    await world.ports.advance(timedelta(minutes=minutes))


# --- 50 ambiguous room -------------------------------------------------------

def test_ambiguous_room_never_guesses_a_satellite(tmp_path):
    world = _home_world(tmp_path, room=ambiguous_room("person.philipp"))

    async def scenario():
        await _open_garage(world)
        assert len(world.ports.delivered) == 1
        message = world.ports.delivered[0]
        assert message.decision.channel is CommunicationChannel.INTERACTIVE_PUSH
        assert message.decision.satellite_entity_id is None
        assert world.spoken() == []
        assert "no_voice:room_ambiguous" in message.decision.reasons
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_unknown_room_and_missing_or_multiple_satellites_use_push(tmp_path):
    for records in ([], [living_satellite(), replace(living_satellite(), entity_id="assist_satellite.wz2", device_id="d2")]):
        world = _home_world(tmp_path)
        world.ports.satellite_records = records

        async def scenario(world=world):
            await _open_garage(world)
            assert world.spoken() == []
            assert world.ports.delivered[0].decision.channel is CommunicationChannel.INTERACTIVE_PUSH

        asyncio.run(scenario())


# --- 51 shared-room privacy --------------------------------------------------

def _habit_world(tmp_path, *, others_home: bool):
    states = garage_states() + [
        entity("cover.kitchen", "Küchenrollladen", "closed", area="kitchen", area_name="Küche"),
        entity("switch.coffee", "Kaffeemaschine", "off", area="kitchen", area_name="Küche"),
    ]
    world = _home_world(tmp_path, others_home=others_home, states=states)
    return world


def _habit_signal(world):
    candidate = HabitCandidate(
        "habit:abc", "philipp", "morning", 0,
        (("light.kitchen", "on"), ("cover.kitchen", "open"), ("light.living", "on")),
    )
    world.ports.states["light.kitchen"] = replace(world.ports.states["light.kitchen"], state="on")
    local = datetime(2026, 9, 21, 7, 5, tzinfo=timezone(timedelta(hours=2)))  # Monday
    signal = habit_signal(
        world.ports.states["light.kitchen"], (candidate,), now=NOW, local_now=local,
        band="morning", home_user_ids=frozenset({"philipp"}),
        entities=world.ports.states,
    )
    assert signal is not None
    world.ports.habits[signal.dedupe_key] = None
    world.ports.habits = {signal.dedupe_key: HabitEvidence("habit:abc", "philipp", True, True, False, False, 0.8)}
    return signal


def test_personal_habit_suggestion_is_never_broadcast_in_a_shared_home(tmp_path):
    world = _habit_world(tmp_path, others_home=True)

    async def scenario():
        signal = _habit_signal(world)
        await world.engine.async_process_signals((signal,), now=NOW)
        assert len(world.ports.delivered) == 1
        message = world.ports.delivered[0]
        assert message.decision.recipient_user_id == "philipp"
        assert message.decision.channel is CommunicationChannel.INTERACTIVE_PUSH
        assert world.spoken() == []
        assert "no_voice:personal_audience_may_be_shared" in message.decision.reasons
        assert message.text == (
            "Du machst werktags morgens häufig dieselbe Abfolge. Als Nächstes folgen meist: "
            "Küchenrollladen öffnen und Wohnzimmerlicht einschalten. Soll ich die Routine starten?"
        )
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_personal_habit_suggestion_may_be_spoken_when_alone_in_exact_room(tmp_path):
    world = _habit_world(tmp_path, others_home=False)

    async def scenario():
        await world.engine.async_process_signals((_habit_signal(world),), now=NOW)
        assert world.ports.delivered[0].decision.channel is CommunicationChannel.VOICE
        assert world.sink.device_calls == []
        reply = await world.engine.async_handle_reply("Ja", user_id="philipp", device_id="dev_living", is_admin=True)
        assert reply is not None and reply.handled
        # The remaining habit steps run through V10 as one verified plan.
        assert [call[:2] for call in world.sink.device_calls] == [
            ("cover", "open_cover"), ("homeassistant", "turn_on"),
        ]

    asyncio.run(scenario())


def test_stale_or_dismissed_habit_model_only_writes_history(tmp_path):
    world = _habit_world(tmp_path, others_home=False)

    async def scenario():
        signal = _habit_signal(world)
        world.ports.habits = {signal.dedupe_key: HabitEvidence("habit:abc", "philipp", True, True, False, True, 0.8)}
        await world.engine.async_process_signals((signal,), now=NOW)
        assert world.ports.delivered == []
        assert world.engine.history.records()[-1].result == "history_only"
        assert "habit_model_stale" in world.engine.history.records()[-1].reasons

    asyncio.run(scenario())


# --- 52 multiple proposals ---------------------------------------------------

def test_bare_yes_with_two_proposals_clarifies_and_executes_nothing(tmp_path):
    world = build_world(
        tmp_path, garage_states(),
        recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )

    async def scenario():
        await world.change("cover.garage", "open")
        await world.change("light.kitchen", "on")
        await world.ports.advance(timedelta(minutes=15))
        assert len(world.ports.delivered) == 2
        assert {item.decision.channel for item in world.ports.delivered} == {CommunicationChannel.INTERACTIVE_PUSH}
        reply = await world.engine.async_handle_reply("Ja", user_id="philipp", device_id=None, is_admin=True)
        assert reply is not None and reply.clarification_ids
        assert reply.speech == "Meinst du das Licht in der Küche oder die Garage?"
        assert world.sink.device_calls == []
        chosen = await world.engine.async_select_and_apply(
            reply.clarification_ids, "die Küche", ProposalChoice.ACCEPT,
            user_id="philipp", is_admin=True,
        )
        assert chosen is not None
        assert world.sink.device_calls == [("homeassistant", "turn_off", {"entity_id": "light.kitchen"})]
        garage = next(item for item in world.engine.proposals.all() if item.subject_label == "die Garage")
        assert garage.state is ProposalState.PENDING

    asyncio.run(scenario())


def test_wrong_user_cannot_confirm_and_anonymous_needs_origin_device(tmp_path):
    world = _home_world(tmp_path, others_home=True)
    world.ports.recipients = {"philipp": philipp()}

    async def scenario():
        await _open_garage(world)
        wrong = await world.engine.async_handle_reply("Ja", user_id="anna", device_id="dev_living", is_admin=True)
        assert wrong is not None and "anderen Benutzer" in wrong.speech
        elsewhere = await world.engine.async_handle_reply("Ja", user_id="anna", device_id="dev_kitchen", is_admin=True)
        assert elsewhere is None
        anonymous_other = await world.engine.async_handle_reply("Ja", user_id=None, device_id="dev_kitchen", is_admin=False)
        assert anonymous_other is None
        assert world.sink.device_calls == []
        # Anonymous answer on the satellite that asked: household proposal.
        anonymous_here = await world.engine.async_handle_reply("Ja", user_id=None, device_id="dev_living", is_admin=False)
        assert anonymous_here is not None
        # V10 policy still applies: critical garage close is admin-only.
        assert world.sink.device_calls == []
        assert "nicht" in anonymous_here.speech

    asyncio.run(scenario())


def test_cross_channel_voice_question_push_answer_and_expiry(tmp_path):
    world = _home_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        proposal_id = world.ports.delivered[0].proposal_id
        # Voice question answered later from the phone (authenticated user).
        await world.ports.advance(timedelta(minutes=31))
        expired = await world.engine.async_handle_push_action(
            f"HOMEINTENT_V12_ACCEPT_{proposal_id}", user_id="philipp", device_id=None,
        )
        # Voice-only question: no push token exists, and it is expired anyway.
        assert expired is not None and not expired.handled
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_voice_room_a_answer_room_b_requires_identity(tmp_path):
    world = _home_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        other_room = await world.engine.async_handle_reply("Ja", user_id="philipp", device_id="dev_kitchen", is_admin=True)
        assert other_room is not None and "geschlossen" in other_room.speech
        assert len(world.sink.device_calls) == 1

    asyncio.run(scenario())


# --- 53 interactive push -----------------------------------------------------

def _push_world(tmp_path):
    return build_world(
        tmp_path, garage_states(), recipients={"philipp": philipp(), "anna": anna()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )


def test_interactive_push_actions_are_opaque_and_authenticated(tmp_path):
    world = _push_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        message = world.ports.delivered[0]
        assert message.decision.channel is CommunicationChannel.INTERACTIVE_PUSH
        assert message.accept_label == "Schließen"
        pid = message.proposal_id
        handle = world.engine.async_handle_push_action
        for forged in (
            "HOMEINTENT_V12_ACCEPT_cover.garage",
            f"HOMEINTENT_V12_EXECUTE_{pid}",
            f"HOMEINTENT_V12_ACCEPT_{pid}|lock.unlock",
            "HOMEINTENT_V12_ACCEPT_p" + "0" * 32,
            f"HOMEINTENT_EXECUTE_{pid}",
        ):
            result = await handle(forged, user_id="philipp", device_id=None)
            assert result is None or not result.handled
        action, device = world.ports.push_action("ACCEPT", "philipp", pid)
        assert (await handle(action, user_id=None, device_id=device)).speech == "wrong_recipient"
        assert (await handle(action, user_id="mallory", device_id=device)).speech == "wrong_recipient"
        # Anna is a recipient, but this token was issued to Philipp's phone.
        assert (await handle(action, user_id="anna", device_id=device)).speech == "wrong_recipient"
        assert (await handle(f"HOMEINTENT_V12_ACCEPT_{pid}", user_id="philipp", device_id=device)).speech == "missing_device_identity"
        assert (await handle(action, user_id="philipp", device_id="phone_anna")).speech == "unexpected_device"
        assert world.sink.device_calls == []
        accepted = await handle(action, user_id="philipp", device_id=device)
        assert accepted.handled
        assert world.sink.device_calls == [("cover", "close_cover", {"entity_id": "cover.garage"})]
        replay = await handle(action, user_id="philipp", device_id=device)
        assert replay.speech == "already_resolved"
        anna_action, anna_device = world.ports.push_action("ACCEPT", "anna", pid)
        other = await handle(anna_action, user_id="anna", device_id=anna_device)
        assert other.speech == "already_resolved"
        assert len(world.sink.device_calls) == 1

    asyncio.run(scenario())


def test_push_later_snoozes_and_ignore_acknowledges(tmp_path):
    world = _push_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        pid = world.ports.delivered[0].proposal_id
        action, device = world.ports.push_action("LATER", "philipp", pid)
        later = await world.engine.async_handle_push_action(action, user_id="philipp", device_id=device)
        assert later.handled and "30 Minuten" in later.speech
        situation = world.engine.situations.get("entry_left_open:cover.garage")
        assert situation.state is SituationState.SNOOZED
        assert len(world.ports.delivered) == 2  # one shared proposal, pushed to both
        assert world.ports.delivered[0].proposal_id == world.ports.delivered[1].proposal_id
        await world.ports.advance(timedelta(minutes=30))
        assert len(world.ports.delivered) == 4  # live re-check: still open -> asked again
        pid2 = world.ports.delivered[3].proposal_id
        action, device = world.ports.push_action("IGNORE", "anna", pid2)
        ignored = await world.engine.async_handle_push_action(action, user_id="anna", device_id=device)
        assert ignored.handled
        assert world.engine.situations.get("entry_left_open:cover.garage").state is SituationState.ACKNOWLEDGED
        await world.ports.advance(timedelta(hours=3))
        assert len(world.ports.delivered) == 4
        # Ignoring is not a persistent preference.
        assert not world.engine.attention_state.is_muted("anna", SituationKind.ENTRY_LEFT_OPEN)
        assert world.sink.device_calls == []

    asyncio.run(scenario())


# --- 54/55/56 standing permissions -------------------------------------------

def _permission(**changes) -> StandingPermission:
    base = StandingPermission(
        "perm_1", "philipp", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
        AutoOperator.LIGHT_TURN_OFF, ("light.living", "light.living_floor"), "living_room",
        (PermissionCondition.NOBODY_HOME,), NOW - timedelta(days=1), NOW + timedelta(days=30), True,
    )
    return replace(base, **changes)


def _leaving_world(tmp_path, *, enabled: bool = True):
    return build_world(
        tmp_path, garage_states(),
        config=ProactiveConfig(enabled=True, standing_permissions_enabled=enabled),
        recipients={"philipp": philipp()},
        household={"person.philipp": "home", "person.anna": "not_home"},
    )


async def _leave_with_light_on(world, minutes: int = 5) -> None:
    await world.change("light.living", "on")
    await world.person("person.philipp", "not_home")
    await world.ports.advance(timedelta(minutes=minutes))


def test_confirmed_low_risk_permission_executes_exactly_one_v10_action(tmp_path):
    world = _leaving_world(tmp_path)
    world.engine.permissions.add(_permission())

    async def scenario():
        await _leave_with_light_on(world)
        assert world.sink.device_calls == [
            ("homeassistant", "turn_off", {"entity_id": "light.living"}),
        ]
        runs = await world.goal_runs.async_list()
        assert len(runs) == 1 and runs[0].steps[0].service_accepted is True
        assert any(item.startswith("proactive:standing_permission:") for item in runs[0].evidence)
        stored = await world.learning.experiences.async_list()
        assert len(stored) == 1
        assert world.ports.delivered == []  # no interactive confirmation needed
        # Idempotent: the light is off now, nothing more happens.
        await world.ports.advance(timedelta(minutes=30))
        assert len(world.sink.device_calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("variant", [
    {"confirmed": False}, {"revoked": True},
    {"expires_at": NOW - timedelta(minutes=1)}, {"owner_user_id": "mallory"},
    {"area_id": "kitchen"}, {"entity_ids": ("light.living_floor",)},
])
def test_invalid_permission_never_executes_and_asks_instead(tmp_path, variant):
    world = _leaving_world(tmp_path)
    permission = _permission(**variant)
    if permission.confirmed:
        world.engine.permissions.add(permission)
    else:
        world.engine.permissions._items[permission.permission_id] = permission  # corrupt/unconfirmed

    async def scenario():
        await _leave_with_light_on(world)
        assert world.sink.device_calls == []
        assert len(world.ports.delivered) == 1
        assert world.ports.delivered[0].decision.channel is CommunicationChannel.INTERACTIVE_PUSH

    asyncio.run(scenario())


def test_permission_disabled_or_someone_home_does_not_execute(tmp_path):
    world = _leaving_world(tmp_path, enabled=False)
    world.engine.permissions.add(_permission())

    async def scenario():
        await _leave_with_light_on(world)
        assert world.sink.device_calls == []

    asyncio.run(scenario())
    home = _leaving_world(tmp_path / "home")
    home.engine.permissions.add(_permission())

    async def scenario_home():
        await home.change("light.living", "on")
        await home.ports.advance(timedelta(minutes=10))
        assert home.sink.device_calls == []

    asyncio.run(scenario_home())


def test_garage_never_auto_even_with_a_permission_record(tmp_path):
    world = _home_world(tmp_path, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    forged = _permission(
        permission_id="perm_garage", situation_kind=SituationKind.ENTRY_LEFT_OPEN,
        entity_ids=("cover.garage",), area_id=None, conditions=(),
    )
    with pytest.raises(ValueError):
        world.engine.permissions.add(forged)
    # Simulate a tampered store that bypassed the boundary check.
    world.engine.permissions._items[forged.permission_id] = forged

    async def scenario():
        await _open_garage(world)
        assert world.sink.device_calls == []
        assert world.ports.delivered[0].text == "Die Garage ist noch offen. Soll ich sie schließen?"
        assert any("situation_kind_mismatch" in item.reasons for item in world.engine.history.records())

    asyncio.run(scenario())


def test_unlock_is_unreachable_from_v12(tmp_path):
    world = _leaving_world(tmp_path)
    world.ports.states["lock.front"] = replace(world.ports.states["lock.front"], state="locked")

    async def scenario():
        # Even a forged proposal asking for an unlock cannot be expressed by V10.
        result = await world.engine.runner.async_execute(
            ProposedGoal((TargetState("lock.front", "unlocked", "Haustür"),), "forged"),
            user_id="philipp", is_admin=True, person_entity_id=None,
            interactive_confirmed=True, provenance="forged", now=NOW,
        )
        assert result.status is ProactiveExecutionStatus.STALE
        # And the lock is NEVER_AUTO for any permission.
        assert world.engine.auto_policy.never_auto_reason(
            world.ports.states["lock.front"], AutoOperator.LIGHT_TURN_OFF,
        ) is not None
        assert [call for call in world.sink.calls if call[0] == "lock"] == []

    asyncio.run(scenario())


def test_auto_path_cannot_upgrade_a_confirm_policy_decision(tmp_path):
    world = _leaving_world(tmp_path)

    async def scenario():
        world.ports.states["cover.garage"] = replace(world.ports.states["cover.garage"], state="open")
        result = await world.engine.runner.async_execute(
            ProposedGoal((TargetState("cover.garage", "closed", "Garage"),), "forged"),
            user_id="philipp", is_admin=True, person_entity_id=None,
            interactive_confirmed=False, provenance="forged", now=NOW,
        )
        assert result.status is ProactiveExecutionStatus.BLOCKED
        assert world.sink.device_calls == []
        runs = await world.goal_runs.async_list()
        assert runs[-1].steps[0].service_accepted is False

    asyncio.run(scenario())


# --- 57 priority E2E ---------------------------------------------------------

def test_priority_end_to_end(tmp_path):
    states = garage_states() + [entity("sensor.washer", "Waschmaschine", "running", area="bath")]
    world = _home_world(tmp_path, states=states)
    world.engine.detector = SituationDetector(DetectorConfig(appliance_entity_ids=frozenset({"sensor.washer"})))

    async def scenario():
        await world.change("sensor.washer", "finished")
        await world.ports.advance(timedelta(minutes=5))
        await _open_garage(world, 16)
        await world.change("binary_sensor.smoke_hall", "on")
        await world.engine.async_process_signals((), now=world.ports.clock)
        by_text = {item.text: item.priority for item in world.ports.delivered}
        assert by_text["Die Waschmaschine ist fertig."] is PriorityLevel.INFO
        assert by_text["Die Garage ist noch offen. Soll ich sie schließen?"] is PriorityLevel.IMPORTANT
        assert by_text["Achtung: Das Gerät „Rauchmelder Flur“ meldet Rauch!"] is PriorityLevel.CRITICAL
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_effect_anomaly_report_cannot_become_critical(tmp_path):
    from homeintent.situation_detection import DetectionSignal
    from homeintent.proactive_model import SituationEvidence

    world = _home_world(tmp_path)

    async def scenario():
        await world.engine.async_report_situation(DetectionSignal(
            SituationKind.DEVICE_EFFECT_ANOMALY, "device_effect_anomaly:light.kitchen",
            ("light.kitchen",), None, True, NOW, (SituationEvidence("operator_id", "LIGHT_TURN_ON"),),
            "Küchenlicht",
        ))
        record = world.engine.history.records()[-1]
        assert record.priority is PriorityLevel.INFO
        assert world.sink.device_calls == []

    asyncio.run(scenario())


# --- 58 attention --------------------------------------------------------------

def test_low_priority_burst_is_grouped_and_smoke_bypasses(tmp_path):
    states = garage_states() + [
        entity(f"sensor.appliance_{index}", name, "running", area="bath")
        for index, name in enumerate(("Waschmaschine", "Trockner", "Geschirrspüler"))
    ]
    world = build_world(
        tmp_path, states, recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )
    world.engine.detector = SituationDetector(DetectorConfig(
        appliance_entity_ids=frozenset(f"sensor.appliance_{index}" for index in range(3)),
    ))

    async def scenario():
        for index in range(3):
            await world.change(f"sensor.appliance_{index}", "finished")
            await world.ports.advance(timedelta(seconds=10))
        assert len(world.ports.delivered) == 1
        await world.change("binary_sensor.smoke_hall", "on")
        assert len(world.ports.delivered) == 2
        assert world.ports.delivered[-1].priority is PriorityLevel.CRITICAL
        await world.ports.advance(timedelta(minutes=3))
        assert len(world.ports.delivered) == 3
        digest = world.ports.delivered[-1].text
        assert digest.startswith("Kurz zusammengefasst:")
        assert "Der Trockner ist fertig." in digest and "Der Geschirrspüler ist fertig." in digest
        assert ".." not in digest

    asyncio.run(scenario())


def test_notice_right_after_a_critical_alarm_is_delivered_not_grouped(tmp_path):
    """F28: a critical alarm must not use up the ordinary attention budget."""
    states = garage_states() + [entity("sensor.washer", "Waschmaschine", "running", area="bath")]
    world = build_world(
        tmp_path, states, recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )
    world.engine.detector = SituationDetector(DetectorConfig(
        appliance_entity_ids=frozenset({"sensor.washer"}),
    ))

    async def scenario():
        await world.change("binary_sensor.smoke_hall", "on")
        assert world.ports.delivered[-1].priority is PriorityLevel.CRITICAL
        await world.ports.advance(timedelta(seconds=20))
        await world.change("sensor.washer", "finished")
        assert len(world.ports.delivered) == 2
        assert "Waschmaschine" in world.ports.delivered[-1].text
        assert world.engine.history.records()[-1].result == "delivered"

    asyncio.run(scenario())


def test_grouped_notice_is_summarized_and_delivered(tmp_path):
    """F28: grouping summarizes; it never silently drops a notice."""
    states = garage_states() + [
        entity(f"sensor.appliance_{index}", name, "running", area="bath")
        for index, name in enumerate(("Waschmaschine", "Trockner"))
    ]
    world = build_world(
        tmp_path, states, recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )
    world.engine.detector = SituationDetector(DetectorConfig(
        appliance_entity_ids=frozenset({"sensor.appliance_0", "sensor.appliance_1"}),
    ))

    async def scenario():
        await world.change("sensor.appliance_0", "finished")
        await world.ports.advance(timedelta(seconds=10))
        await world.change("sensor.appliance_1", "finished")
        assert len(world.ports.delivered) == 1
        assert world.engine.history.records()[-1].result == "grouped"
        await world.ports.advance(timedelta(minutes=3))
        assert len(world.ports.delivered) == 2
        assert "Trockner" in world.ports.delivered[-1].text
        assert world.engine.history.records()[-1].result == "delivered"

    asyncio.run(scenario())


def test_undeliverable_digest_is_recorded_not_dropped(tmp_path):
    states = garage_states() + [
        entity(f"sensor.appliance_{index}", name, "running", area="bath")
        for index, name in enumerate(("Waschmaschine", "Trockner"))
    ]
    world = build_world(
        tmp_path, states, recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )
    world.engine.detector = SituationDetector(DetectorConfig(
        appliance_entity_ids=frozenset({"sensor.appliance_0", "sensor.appliance_1"}),
    ))

    async def scenario():
        await world.change("sensor.appliance_0", "finished")
        await world.ports.advance(timedelta(seconds=10))
        await world.change("sensor.appliance_1", "finished")
        world.ports.delivery_fails = True
        await world.ports.advance(timedelta(minutes=3))
        record = world.engine.history.records()[-1]
        assert record.subject_label and "Trockner" in record.subject_label
        assert record.result == "delivery_failed"
        assert "group_delivery_failed" in record.reasons

    asyncio.run(scenario())


# --- 59 quiet hours -------------------------------------------------------------

def test_quiet_hours_matrix_end_to_end(tmp_path):
    states = garage_states() + [entity("sensor.washer", "Waschmaschine", "running", area="bath")]
    config = ProactiveConfig(enabled=True, quiet=QuietHoursPolicy(parse_quiet_window("22:00-07:00")))
    world = _home_world(tmp_path, config=config, states=states)
    world.engine.detector = SituationDetector(DetectorConfig(appliance_entity_ids=frozenset({"sensor.washer"})))
    world.ports.clock = datetime(2026, 9, 24, 21, 30, tzinfo=timezone.utc)  # 23:30 local

    async def scenario():
        await world.change("sensor.washer", "finished")
        assert world.ports.delivered == []
        assert world.engine.history.records()[-1].result == "history_only"
        await _open_garage(world)
        garage = world.ports.delivered[-1]
        assert garage.decision.channel is CommunicationChannel.INTERACTIVE_PUSH
        assert world.spoken() == []
        await world.change("binary_sensor.smoke_hall", "on")
        smoke = world.ports.delivered[-1]
        assert smoke.priority is PriorityLevel.CRITICAL
        assert CommunicationChannel.VOICE in smoke.decision.channels
        assert CommunicationChannel.PUSH in smoke.decision.channels
        assert world.sink.device_calls == []

    asyncio.run(scenario())


# --- 60 snooze ------------------------------------------------------------------

def test_snooze_rechecks_live_state_and_stays_silent_when_resolved(tmp_path):
    world = _home_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        reply = await world.engine.async_handle_reply(
            "Erinnere mich in 20 Minuten.", user_id="philipp", device_id="dev_living", is_admin=True,
        )
        assert reply is not None and "20 Minuten" in reply.speech
        await world.ports.advance(timedelta(minutes=10))
        await world.change("cover.garage", "closed")
        await world.ports.advance(timedelta(minutes=10))
        assert len(world.ports.delivered) == 1
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_snooze_expiry_with_garage_still_open_asks_again(tmp_path):
    world = _home_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        await world.engine.async_handle_reply("Später", user_id="philipp", device_id="dev_living", is_admin=True)
        await world.ports.advance(timedelta(minutes=29))
        assert len(world.ports.delivered) == 1
        await world.ports.advance(timedelta(minutes=1))
        assert len(world.ports.delivered) == 2
        assert world.ports.delivered[-1].text == "Die Garage ist noch offen. Soll ich sie schließen?"
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_reject_is_contextual_and_not_persistent(tmp_path):
    world = _home_world(tmp_path)

    async def scenario():
        await _open_garage(world)
        reply = await world.engine.async_handle_reply("Nein", user_id="philipp", device_id="dev_living", is_admin=True)
        assert reply.speech == "In Ordnung. Ich lasse es so."
        assert world.engine.situations.get("entry_left_open:cover.garage").state is SituationState.ACKNOWLEDGED
        await world.change("cover.garage", "closed")
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))
        # A new occurrence is a new situation; the earlier "Nein" did not mute it.
        assert len(world.ports.delivered) == 2
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_habit_suggestion_never_bundles_security_targets(tmp_path):
    states = garage_states() + [
        entity("cover.kitchen", "Küchenrollladen", "closed", area="kitchen", area_name="Küche"),
    ]
    world = _home_world(tmp_path, states=states)

    async def scenario():
        candidate = HabitCandidate(
            "habit:sec", "philipp", "morning", 0,
            (("light.kitchen", "on"), ("cover.garage", "open"), ("lock.front", "unlocked"),
             ("cover.kitchen", "open")),
        )
        world.ports.states["light.kitchen"] = replace(world.ports.states["light.kitchen"], state="on")
        signal = habit_signal(
            world.ports.states["light.kitchen"], (candidate,), now=NOW,
            local_now=datetime(2026, 9, 21, 7, 5, tzinfo=timezone(timedelta(hours=2))),
            band="morning", home_user_ids=frozenset({"philipp"}), entities=world.ports.states,
        )
        assert [item.entity_id for item in signal.proposed_goal.targets] == ["cover.kitchen"]
        world.ports.habits = {signal.dedupe_key: HabitEvidence("habit:sec", "philipp", True, True, False, False, 0.9)}
        await world.engine.async_process_signals((signal,), now=NOW)
        assert "Garage" not in world.ports.delivered[-1].text
        await world.engine.async_handle_reply("Ja", user_id="philipp", device_id="dev_living", is_admin=True)
        assert [call[:2] for call in world.sink.device_calls] == [("cover", "open_cover")]
        assert world.sink.device_calls[0][2] == {"entity_id": "cover.kitchen"}

    asyncio.run(scenario())


def test_critical_alarm_without_any_recipient_uses_household_fallback(tmp_path):
    world = build_world(tmp_path, garage_states(), household={"person.philipp": "home"})

    async def scenario():
        await world.change("binary_sensor.smoke_hall", "on")
        assert len(world.ports.delivered) == 1
        message = world.ports.delivered[0]
        assert message.decision.recipient_user_id is None
        assert "critical_household_fallback" in message.decision.reasons
        assert world.sink.device_calls == []
        # A non-critical situation without recipients stays in history only.
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))
        assert len(world.ports.delivered) == 1

    asyncio.run(scenario())


def test_polite_command_is_not_a_standing_permission():
    from homeintent.standing_permission import looks_like_permission_request

    assert not looks_like_permission_request("Du darfst das Licht ausschalten.")
    assert not looks_like_permission_request("Darfst du das Licht ausschalten?")
    assert looks_like_permission_request("Du darfst künftig das Licht ausschalten.")
    assert looks_like_permission_request("Wenn ich gehe, darfst du das Licht ausschalten.")


# --- F15: power-metered and binary-sensor appliances ---------------------------

def _power_washer(watts: str):
    from dataclasses import replace as _replace

    return _replace(
        entity("sensor.washer_power", "Leistung Waschmaschine", watts, area="bath",
               device_class="power"),
        unit="W",
    )


def test_power_metered_washer_finishes_after_a_sustained_low_draw(tmp_path):
    states = garage_states() + [_power_washer("0")]
    world = _home_world(tmp_path, states=states)
    world.engine.detector = SituationDetector(
        DetectorConfig(appliance_entity_ids=frozenset({"sensor.washer_power"}))
    )

    async def scenario():
        await world.change("sensor.washer_power", "1850")
        await world.ports.advance(timedelta(minutes=30))
        await world.change("sensor.washer_power", "2")
        # A drop alone is not "finished" yet: nothing before the idle time.
        await world.ports.advance(timedelta(seconds=20))
        assert not any("Waschmaschine" in item.text for item in world.ports.delivered)
        await world.ports.advance(timedelta(minutes=1))
        assert any(item.text == "Die Waschmaschine ist fertig." for item in world.ports.delivered)
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_power_pause_shorter_than_idle_time_is_not_finished(tmp_path):
    states = garage_states() + [_power_washer("0")]
    world = _home_world(tmp_path, states=states)
    world.engine.detector = SituationDetector(
        DetectorConfig(appliance_entity_ids=frozenset({"sensor.washer_power"}))
    )

    async def scenario():
        await world.change("sensor.washer_power", "1850")
        await world.change("sensor.washer_power", "2")
        await world.ports.advance(timedelta(seconds=30))
        await world.change("sensor.washer_power", "1700")  # program continues
        await world.ports.advance(timedelta(minutes=5))
        assert not any("Waschmaschine" in item.text for item in world.ports.delivered)

    asyncio.run(scenario())


def test_binary_running_sensor_on_to_off_is_finished(tmp_path):
    states = garage_states() + [
        entity("binary_sensor.dishwasher_running", "Geschirrspüler", "on", area="kitchen",
               device_class="running"),
    ]
    world = _home_world(tmp_path, states=states)
    world.engine.detector = SituationDetector(
        DetectorConfig(appliance_entity_ids=frozenset({"binary_sensor.dishwasher_running"}))
    )

    async def scenario():
        await world.change("binary_sensor.dishwasher_running", "off")
        assert any("Geschirrspüler" in item.text for item in world.ports.delivered)

    asyncio.run(scenario())
