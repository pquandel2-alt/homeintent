"""V12 decision matrices: every AutoExecutionPolicy refusal, store edges, dialog paths."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from v12_harness import NOW, build_world, entity, garage_states, philipp

from homeintent.dialog_manager import DialogManager, DialogPriority, DialogTaskKind
from homeintent.proactive_dialog import ProactiveDialogHandler
from homeintent.proactive_engine import ProactiveConfig
from homeintent.proactive_model import (
    AutoOperator,
    CommunicationChannel,
    HistoryRecord,
    OpportunityOutcome,
    PermissionCondition,
    PriorityLevel,
    PrivacyLevel,
    ProactiveSituation,
    SituationKind,
    SituationState,
    StandingPermission,
)
from homeintent.proactive_store import GenerationalJsonFile, ProactiveHistoryStore, SituationStore
from homeintent.room_presence import build_area_lookup
from homeintent.situation_detection import DetectionSignal
from homeintent.standing_permission import AutoExecutionPolicy, StandingPermissionStore

STATES = {item.entity_id: item for item in garage_states()}
LEFT_ON = ProactiveSituation(
    "sit", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING, ("light.living",), "living_room",
    NOW - timedelta(minutes=10), NOW, SituationState.COMMUNICATED, (), (), (),
    PriorityLevel.SUGGESTION, PrivacyLevel.HOUSEHOLD, "device_left_on_when_leaving:living_room",
)
PERMISSION = StandingPermission(
    "perm", "philipp", SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING, AutoOperator.LIGHT_TURN_OFF,
    ("light.living",), "living_room", (PermissionCondition.NOBODY_HOME,),
    NOW - timedelta(days=1), NOW + timedelta(days=1), True,
)
ON = {**STATES, "light.living": replace(STATES["light.living"], state="on")}


def _evaluate(permission=PERMISSION, *, enabled=True, situation=LEFT_ON, owner_known=True,
              entities=ON, nobody_home=True, attempts=0):
    return AutoExecutionPolicy().evaluate(
        permission, enabled=enabled, situation=situation, owner_known=owner_known,
        entities=entities, nobody_home=nobody_home, now=NOW, attempts_today=attempts,
    )


@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"enabled": False}, "standing_permissions_disabled"),
    ({"permission": None}, "no_exact_standing_permission"),
    ({"permission": replace(PERMISSION, confirmed=False)}, "permission_not_confirmed"),
    ({"permission": replace(PERMISSION, revoked=True)}, "permission_revoked"),
    ({"permission": replace(PERMISSION, expires_at=NOW)}, "permission_expired"),
    ({"owner_known": False}, "permission_owner_not_in_scope"),
    ({"situation": replace(LEFT_ON, kind=SituationKind.ENTRY_LEFT_OPEN)}, "situation_kind_mismatch"),
    ({"situation": replace(LEFT_ON, state=SituationState.RESOLVED)}, "situation_no_longer_active"),
    ({"situation": replace(LEFT_ON, area_id="kitchen")}, "area_mismatch"),
    ({"attempts": 6}, "daily_attempt_limit"),
    ({"nobody_home": False}, "condition_nobody_home_not_met"),
    ({"nobody_home": None}, "condition_nobody_home_not_met"),
    ({"entities": STATES}, "current_state_no_longer_matches"),
    ({"permission": replace(PERMISSION, entity_ids=("light.living_floor",)),
      "situation": replace(LEFT_ON, subject_ids=("light.living", "light.living_floor"))},
     "permission_does_not_cover_all_targets"),
])
def test_every_auto_check_refuses_on_its_own(kwargs, reason):
    decision = _evaluate(**kwargs)
    assert not decision.allowed
    assert decision.reasons == (reason,)


def test_auto_allowed_only_for_the_exact_case_and_never_for_dangerous_names():
    assert _evaluate().allowed
    herd = {**ON, "light.living": replace(ON["light.living"], friendly_name="Herdlicht")}
    assert _evaluate(entities=herd).reasons == ("never_auto_dangerous_or_security_name",)


def test_permission_store_rules():
    store = StandingPermissionStore()
    with pytest.raises(ValueError):
        store.add(replace(PERMISSION, confirmed=False))
    with pytest.raises(ValueError):
        store.add(replace(PERMISSION, entity_ids=("light.*",)))
    with pytest.raises(ValueError):
        store.add(replace(PERMISSION, entity_ids=()))
    store.add(PERMISSION)
    assert store.matching(LEFT_ON, NOW) == PERMISSION
    # An identical re-confirmation renews the existing instruction (F7).
    assert store.add(replace(PERMISSION, permission_id="perm-again")).permission_id == "perm"
    assert store.matching(LEFT_ON, NOW) == PERMISSION
    # Two *different* instructions for one situation: still no guessing.
    store.add(replace(PERMISSION, permission_id="perm2", entity_ids=("light.other",)))
    assert store.matching(LEFT_ON, NOW) is None  # two candidates: no guessing
    assert store.revoke("perm2").revoked
    assert store.revoke("perm2") is None
    assert store.revoke("missing") is None
    assert store.revoke_all("anna") == 0
    assert store.revoke_all() == 1
    store.record_attempt("perm", NOW, verified=True)
    store.record_attempt("perm", NOW + timedelta(days=2), verified=False)
    assert store.attempts_today("perm", NOW + timedelta(days=2)) == 1
    assert store.verified_executions_today("perm", NOW + timedelta(days=2)) == 0
    round_trip = StandingPermissionStore.from_dict(store.to_dict())
    assert len(round_trip.all()) == 2 and round_trip.active(NOW) == ()


def test_situation_and_history_store_edges():
    store = SituationStore()
    signal = DetectionSignal(SituationKind.APPLIANCE_FINISHED, "k", ("sensor.w",), None, True, NOW)
    created, changed = store.apply(signal, now=NOW, priority_hint=PriorityLevel.INFO, privacy=PrivacyLevel.HOUSEHOLD)
    assert changed and store.apply(signal, now=NOW, priority_hint=PriorityLevel.INFO, privacy=PrivacyLevel.HOUSEHOLD)[1] is False
    assert store.set_state("missing", SituationState.SNOOZED, now=NOW) is None
    assert store.mark_communicated("missing", NOW) is None
    assert store.expire_older_than(NOW + timedelta(hours=5), {SituationKind.APPLIANCE_FINISHED: timedelta(hours=4)}) == 1
    assert store.get("k").state is SituationState.EXPIRED
    assert store.set_state("k", SituationState.SNOOZED, now=NOW) is None
    inactive = replace(signal, active=False)
    assert store.apply(inactive, now=NOW, priority_hint=PriorityLevel.INFO, privacy=PrivacyLevel.HOUSEHOLD)[1] is False
    assert SituationStore.from_list("junk").all() == ()
    assert SituationStore.from_list([{"kind": "nope"}, 5]).all() == ()
    history = ProactiveHistoryStore()
    assert history.latest_for_subject(("garage",)) is None
    record = HistoryRecord(
        "h1", "s", SituationKind.ENTRY_LEFT_OPEN, "Garage", OpportunityOutcome.HISTORY_ONLY,
        None, CommunicationChannel.HISTORY_ONLY, NOW, PriorityLevel.IMPORTANT, PrivacyLevel.HOUSEHOLD, "x",
    )
    history.append(record)
    assert history.latest_for_subject(("garage",)) is None  # history-only is not a notice
    assert history.since(NOW) == (record,)
    assert ProactiveHistoryStore.from_list([{"timestamp": "bad"}, "x"]).records() == ()
    assert GenerationalJsonFile(None).read() is None
    assert GenerationalJsonFile(None).write_generation(1, {"schema_version": 1})


def test_generational_file_rejects_non_mapping(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("[1, 2]")
    assert GenerationalJsonFile(path).read() is None


def _dialog_world(tmp_path, **config):
    world = build_world(
        tmp_path, garage_states(), config=ProactiveConfig(enabled=True, **config),
        recipients={"philipp": philipp()},
        household={"person.philipp": "home", "person.anna": "not_home"},
    )
    dialogs = DialogManager()
    return world, dialogs, ProactiveDialogHandler(world.engine, dialogs)


def test_dialog_error_paths(tmp_path):
    world, dialogs, handler = _dialog_world(tmp_path, standing_permissions_enabled=True)
    lookup = build_area_lookup(garage_states())

    async def say(text, user="philipp", conversation="c"):
        return await handler.async_handle_owned_turn(
            text, conversation_id=conversation, user_id=user, is_admin=True,
            entities=garage_states(), area_lookup=lookup,
        )

    async def scenario():
        base = "Wenn niemand zuhause ist und {} noch Licht an ist, darfst du es automatisch ausschalten."
        assert "nicht eindeutig zuordnen" in (await say(base.format("im Keller"))).speech
        assert "kein Licht" in (await say(base.format("im Flur"))).speech
        assert "angemeldeten Benutzer" in (await say(base.format("im Wohnzimmer"), user=None)).speech
        assert (await say("Welche Daueranweisungen gibt es?")).speech == "Es sind keine Daueranweisungen gespeichert."
        assert "angemeldeten Benutzer" in (await say("Widerrufe alle Daueranweisungen.", user=None)).speech
        assert "nichts hingewiesen" in (await say("Sag mir das künftig nicht mehr.")).speech
        assert "angemeldeten Benutzer" in (await say("Sag mir das künftig nicht mehr.", user=None)).speech
        assert await say("Mach das Licht an.") is None
        # Permission preview: unclear short reply keeps asking, long reply releases the turn.
        await say(base.format("im Wohnzimmer"))
        assert "eindeutig mit Ja oder Nein" in (await say("vielleicht")).speech
        assert "anderen Benutzer" in (await say("Ja", user="anna")).speech
        assert await say("Mach bitte das Licht im Flur an") is None
        assert dialogs.active("c") is None
        # A task owned by another feature is never answered by V12.
        dialogs.create("c", "other", DialogTaskKind.PLAN_CONFIRMATION, DialogPriority.CONFIRMATION)
        assert await say("Ja") is None

    asyncio.run(scenario())


def test_mute_dialog_paths_and_critical_is_never_muted(tmp_path):
    world, dialogs, handler = _dialog_world(tmp_path)
    world.ports.rooms = {}

    async def say(text):
        return await handler.async_handle_owned_turn(
            text, conversation_id="c", user_id="philipp", is_admin=True,
            entities=garage_states(), area_lookup={},
        )

    async def scenario():
        await world.change("binary_sensor.smoke_hall", "on")
        assert (await say("Sag mir das künftig nicht mehr.")).speech == "Sicherheitswarnungen kann ich nicht abschalten."
        world.engine.history.append(replace(
            world.engine.history.records()[-1], situation_kind=SituationKind.APPLIANCE_FINISHED,
            subject_label="Waschmaschine", record_id="h-new",
        ))
        assert "Waschmaschine" in (await say("Sag mir das künftig nicht mehr.")).speech
        assert (await say("hm")).speech == "Bitte antworte mit Ja oder Nein."
        assert (await say("Nein")).speech == "In Ordnung. Die Hinweise bleiben aktiv."
        assert not world.engine.attention_state.is_muted("philipp", SituationKind.APPLIANCE_FINISHED)
        await say("Sag mir das künftig nicht mehr.")
        assert await say("Das ist doch eine ganz andere Frage") is None

    asyncio.run(scenario())


def test_disabled_engine_ignores_commands_but_finishes_open_tasks(tmp_path):
    world, dialogs, handler = _dialog_world(tmp_path)
    world.engine.config = replace(world.engine.config, enabled=False)

    async def scenario():
        result = await handler.async_handle_owned_turn(
            "Welche Hinweise gab es heute?", conversation_id="c", user_id="philipp",
            is_admin=True, entities=(), area_lookup={},
        )
        assert result is None
        assert await handler.async_handle_bare_reply(
            "Ja", conversation_id="c", user_id="philipp", device_id=None, is_admin=True,
            other_open_questions=0,
        ) is None

    asyncio.run(scenario())


def test_other_open_questions_force_clarification(tmp_path):
    world = build_world(
        tmp_path, garage_states(), recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )

    async def scenario():
        await world.change("cover.garage", "open")
        await world.ports.advance(timedelta(minutes=15))
        result = await world.engine.async_handle_reply(
            "Ja", user_id="philipp", device_id=None, is_admin=True, other_open_questions=1,
        )
        assert result.clarification_ids and "mehrere Fragen offen" in result.speech
        assert world.sink.device_calls == []

    asyncio.run(scenario())


def test_auto_waits_then_executes_and_reports_failures(tmp_path):
    world = build_world(
        tmp_path, garage_states() + [entity("light.herd", "Herdlicht", "off", area="living_room")],
        config=ProactiveConfig(enabled=True, standing_permissions_enabled=True),
        recipients={"philipp": philipp()},
        household={"person.philipp": "home", "person.anna": "not_home"},
    )
    world.engine.permissions.add(replace(PERMISSION, entity_ids=("light.living", "light.herd")))

    async def scenario():
        await world.change("light.herd", "on")
        await world.person("person.philipp", "not_home")
        await world.ports.advance(timedelta(minutes=5))
        # A dangerous name inside the permission scope blocks auto; V12 asks instead.
        assert world.sink.device_calls == []
        assert any("never_auto_dangerous_or_security_name" in item.reasons for item in world.engine.history.records())
        assert world.ports.delivered and world.ports.delivered[-1].proposal_id

    asyncio.run(scenario())


def test_personal_history_is_only_explained_to_its_recipient(tmp_path):
    world = build_world(
        tmp_path, garage_states(), recipients={"philipp": philipp()},
        household={"person.philipp": "home", "person.anna": "not_home"},
    )

    async def scenario():
        from homeintent.situation_detection import DetectionSignal as Signal

        await world.engine.async_report_situation(Signal(
            SituationKind.PENDING_GOAL_REQUIRES_ATTENTION, "pending_goal_requires_attention:r",
            (), None, True, NOW, (), "Arzttermin vorbereiten", owner_user_id="philipp",
        ))
        assert "Arzttermin" in world.engine.explain_latest(("arzttermin",), user_id="philipp")
        for other in ("anna", None):
            assert world.engine.explain_latest(("arzttermin",), user_id=other) == (
                "Dazu habe ich in letzter Zeit keinen Hinweis gegeben."
            )
            assert "Arzttermin" not in world.engine.history_summary(since=NOW - timedelta(days=1), user_id=other)

    asyncio.run(scenario())


def test_model_sourced_situations_expire_and_cancel_their_proposals(tmp_path):
    world = build_world(
        tmp_path, garage_states(), recipients={"philipp": philipp()},
        household={"person.philipp": "not_home", "person.anna": "not_home"},
    )

    async def scenario():
        await world.engine.async_report_situation(DetectionSignal(
            SituationKind.PENDING_GOAL_REQUIRES_ATTENTION, "pending_goal_requires_attention:r",
            (), None, True, NOW, (), "Heizplan", owner_user_id="philipp",
        ))
        assert world.engine.situations.get("pending_goal_requires_attention:r").state is SituationState.COMMUNICATED
        world.ports.clock = NOW + timedelta(hours=13)
        await world.engine.async_process_signals((), now=world.ports.clock)
        assert world.engine.situations.get("pending_goal_requires_attention:r").state is SituationState.EXPIRED
        assert world.engine.situations.active() == ()

    asyncio.run(scenario())


def test_duplicate_standing_permissions_are_merged_on_load():
    """F7: stores written by 7.1.2 contain one record per re-confirmation."""
    store = StandingPermissionStore()
    store.add(PERMISSION)
    document = store.to_dict()
    duplicate = dict(document["permissions"][0])
    duplicate["permission_id"] = "perm-dup"
    duplicate["expires_at"] = (PERMISSION.expires_at + timedelta(days=30)).isoformat()
    document["permissions"].append(duplicate)
    document["executions"] = {"perm-dup": [NOW.isoformat()]}

    loaded = StandingPermissionStore.from_dict(document)

    assert [item.permission_id for item in loaded.all()] == ["perm"]
    assert loaded.all()[0].expires_at == PERMISSION.expires_at + timedelta(days=30)
    assert loaded.matching(LEFT_ON, NOW) is not None
    assert loaded.attempts_today("perm", NOW) == 1
