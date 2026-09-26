"""F25: one notice to several people is one history entry, and the history
names only the channel that was really delivered (no buttons on a notify
entity)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from v12_harness import NOW, anna, build_world, garage_states, philipp, run

from homeintent.proactive_engine import DeliveryReceipt, _delivered_channel
from homeintent.proactive_messages import explain_record
from homeintent.proactive_model import (
    CommunicationChannel,
    HistoryRecord,
    OpportunityOutcome,
    PriorityLevel,
    PrivacyLevel,
    SituationKind,
)
from homeintent.proactive_store import ProactiveHistoryStore


def _record(record_id: str, user: str, *, reasons: tuple[str, ...] = ("priority_important",)) -> HistoryRecord:
    return HistoryRecord(
        record_id, "sit_1", SituationKind.ENTRY_LEFT_OPEN, "Garage",
        OpportunityOutcome.COMMUNICATE, user, CommunicationChannel.PUSH, NOW,
        PriorityLevel.IMPORTANT, PrivacyLevel.HOUSEHOLD, "delivered", reasons,
    )


def test_identical_decisions_for_several_recipients_are_one_entry():
    store = ProactiveHistoryStore()
    store.append(_record("h1", "philipp"))
    store.append(_record("h2", "anna"))

    records = store.records()
    assert len(records) == 1
    assert records[0].recipient_user_ids == ("philipp", "anna")
    assert records[0].addressed_to("anna") and records[0].addressed_to("philipp")
    assert not records[0].addressed_to("gast")

    restored = ProactiveHistoryStore.from_list(store.to_list())
    assert restored.records() == records


def test_records_of_one_routing_pass_microseconds_apart_are_merged():
    # Live Home Assistant stamps every record with its own ``now``.
    store = ProactiveHistoryStore()
    store.append(_record("h1", "philipp"))
    store.append(replace(_record("h2", "anna"), timestamp=NOW + timedelta(microseconds=180)))
    later = replace(_record("h3", "gast"), timestamp=NOW + timedelta(minutes=2))
    store.append(later)

    assert [item.recipient_user_ids for item in store.records()] == [
        ("philipp", "anna"), ("gast",),
    ]


def test_different_decisions_stay_separate_entries():
    store = ProactiveHistoryStore()
    store.append(_record("h1", "philipp"))
    store.append(_record("h2", "anna", reasons=("no_voice:recipient_not_home",)))
    store.append(_record("h3", "philipp"))

    assert [item.recipient_user_id for item in store.records()] == ["philipp", "anna", "philipp"]


def test_engine_records_a_household_notice_once_for_both_recipients(tmp_path):
    world = build_world(
        tmp_path, garage_states(),
        recipients={"philipp": philipp(), "anna": anna()},
        household={"person.philipp": "home", "person.anna": "home"},
    )

    async def scenario() -> None:
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))

    run(scenario())
    delivered = [item for item in world.engine.history.records() if item.result == "delivered"]
    assert len(world.ports.delivered) == 2
    assert len(delivered) == 1
    assert set(delivered[0].recipient_user_ids) == {"philipp", "anna"}
    assert "Garage" in world.engine.explain_latest(("garage",), user_id="anna")


def test_plain_push_is_not_explained_as_push_with_reply_buttons():
    plain = DeliveryReceipt((CommunicationChannel.PUSH,))
    interactive = DeliveryReceipt((CommunicationChannel.INTERACTIVE_PUSH,))

    assert _delivered_channel(CommunicationChannel.INTERACTIVE_PUSH, plain) is CommunicationChannel.PUSH
    assert (
        _delivered_channel(CommunicationChannel.INTERACTIVE_PUSH, interactive)
        is CommunicationChannel.INTERACTIVE_PUSH
    )
    assert _delivered_channel(CommunicationChannel.PUSH, DeliveryReceipt(())) is (
        CommunicationChannel.HISTORY_ONLY
    )
    record = replace(_record("h1", "philipp"), channel=_delivered_channel(
        CommunicationChannel.INTERACTIVE_PUSH, plain
    ))
    explanation = explain_record(record, local_time=NOW)
    assert "per Push-Nachricht" in explanation
    assert "Antwortknöpfen" not in explanation


def test_accepted_proposal_from_a_plain_push_is_not_explained_with_buttons(tmp_path):
    # A notify entity gets no reply buttons: the history must not claim any.
    world = build_world(
        tmp_path, garage_states(),
        recipients={"philipp": philipp()},
        household={"person.philipp": "home", "person.anna": "not_home"},
        companion_devices={},
    )

    async def scenario() -> None:
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))
        reply = await world.engine.async_handle_reply(
            "Ja", user_id="philipp", device_id=None, is_admin=True,
        )
        assert reply is not None

    run(scenario())
    record = world.engine.history.records()[-1]
    assert record.result.startswith("proposal_")
    assert record.channel is CommunicationChannel.PUSH
    assert "Antwortknöpfen" not in world.engine.explain_latest(("garage",), user_id="philipp")
