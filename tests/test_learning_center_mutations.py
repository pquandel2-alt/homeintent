"""7.1 Learning Center knowledge control through the existing authorities.

Required end-to-end scenarios 1-8 plus privacy/API-security E2E.  Every
scenario asserts that no device service call happened.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from learning_center_harness import (
    ADMIN,
    NOW,
    USER_A,
    USER_B,
    goal_run,
    habit_model,
    learn_reliability,
    make_env,
    preference_model,
    run,
    standing_permission,
    thermal_model,
)

import homeintent.conversation as ha_conversation
from homeintent.conversation import NluConversationEntity
from homeintent.entities import EntitySnapshot
from homeintent.learning_center import model_ref
from homeintent.learning_control import (
    ControlErrorCode,
    LearningControlError,
    async_confirm_preference,
)
from homeintent.learning_policy import KnowledgeState
from homeintent.model_registry import LearnedKind, ModelHealth, SuppressionKind
from homeintent.proactive_model import SituationKind
from homeintent.standing_permission import AutoExecutionPolicy

from homeassistant.components.conversation import ConversationInput
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


def _voice_agent(env, monkeypatch):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    runtime = agent._runtime_data
    runtime.learning_policy = env.runtime.learning_policy
    runtime.learned_models = env.registry
    runtime.experiences = env.experiences
    runtime.predictive_house = env.house
    runtime.learning_manager = env.manager
    runtime.profiles = env.profiles
    entities = [
        EntitySnapshot("light.kitchen", "Küchenlicht", "light", "off", area_id="kitchen",
                       capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
        EntitySnapshot("light.floor_lamp", "Stehlampe", "light", "off", area_id="living",
                       capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
    ]
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: entities)
    monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])
    return agent


async def _say(agent, text, user=USER_A, conversation_id="lc"):
    result = await agent._async_handle_message(
        ConversationInput(text=text, conversation_id=conversation_id,
                          context=SimpleNamespace(user_id=user)),
        None,
    )
    return result.response.speech


# -- Scenario 1: reliability overview/detail, no ServiceCalls ------------------------------

def test_scenario_1_reliability_overview_and_detail(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await learn_reliability(env, successes=8, failures=1)
        listing, _ = await env.call(USER_A, "models/list")
        card = next(item for item in listing["models"] if item["kind"] == "reliability")
        assert card["subject_label"] == "Küchenlicht"
        assert card["sample_count"] == 9
        assert round(card["headline"]["value"] * 100, 1) == 88.9
        # Real V11 thresholds: 9 samples are below `usable_model_samples`.
        assert card["status"] == "learning"
        evidence, _ = await env.call(USER_A, "models/evidence", ref=card["ref"])
        assert evidence["evidence"]["provenance_count"] == 9
        assert env.service_calls() == 0
    run(_go())


# -- Scenario 2: confirm personal preference ------------------------------------------

def test_scenario_2_preference_confirmation_is_equivalent_to_voice(tmp_path, monkeypatch):
    async def _go():
        env = await make_env(tmp_path)
        pref = preference_model(USER_A)
        await env.registry.async_upsert(pref)
        listing, _ = await env.call(USER_B, "models/list")
        assert listing["models"] == []
        ref = model_ref(pref.model_id)
        result, error = await env.call(USER_A, "preferences/confirm", ref=ref)
        assert error is None and result == {"confirmed": True}
        ui_state = await env.registry.async_get(pref.model_id)
        assert ui_state.knowledge_state is KnowledgeState.CONFIRMED
        assert ui_state.confirmed_by == USER_A
        assert dict(ui_state.context) == dict(pref.context)  # same scope
        assert ui_state.parameters["entity_id"] == pref.parameters["entity_id"]
        _result, error = await env.call(USER_A, "preferences/confirm", ref=ref)
        assert error[0] == "invalid_state"
        assert env.service_calls() == 0

        # Voice path on an identical, separate model produces identical state.
        voice_env = await make_env(tmp_path / "voice", entry_id="voice")
        await voice_env.registry.async_upsert(pref)
        agent = _voice_agent(voice_env, monkeypatch)
        speech = await _say(agent, "Was weißt du über meine Lichtpräferenzen?")
        assert "Soll das" in speech
        assert (await _say(agent, "Ja")).startswith("Gespeichert")
        voice_state = await voice_env.registry.async_get(pref.model_id)
        ignore = {"model_version", "parameters"}
        assert {k: v for k, v in voice_state.to_dict().items() if k not in ignore} == {
            k: v for k, v in ui_state.to_dict().items() if k not in ignore
        }
        # Voice additionally marks "shown" first; the terminal state agrees.
        assert voice_state.parameters["suggestion_status"] == ui_state.parameters["suggestion_status"] == "accepted"
        agent.hass.services.async_call.assert_not_awaited()
    run(_go())


def test_preference_reject_keeps_observation_non_binding(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        pref = preference_model(USER_A)
        await env.registry.async_upsert(pref)
        result, error = await env.call(USER_A, "preferences/reject", ref=model_ref(pref.model_id))
        assert error is None and result["rejected"]
        stored = await env.registry.async_get(pref.model_id)
        assert stored.knowledge_state is KnowledgeState.INFERRED
        assert stored.parameters["suggestion_status"] == "rejected"
        listing, _ = await env.call(USER_A, "models/list")
        assert listing["models"][0]["actions"] == ["forget"]
    run(_go())


# -- Scenario 3: reject habit durably ---------------------------------------------------

def test_scenario_3_habit_rejection_is_durable(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        habit = habit_model(USER_A)
        await env.registry.async_upsert(habit)
        result, error = await env.call(USER_A, "habits/reject", ref=model_ref(habit.model_id))
        assert error is None and result["rejected"]
        assert await env.registry.async_get(habit.model_id) is None
        tombstones = await env.registry.async_list_tombstones()
        assert tombstones[0].suppression_kind is SuppressionKind.REJECTED_HABIT
        assert tombstones[0].durable
        # Old or new equivalent evidence cannot resurrect it; GC keeps it.
        assert (await env.registry.async_upsert(habit)).value == "suppressed"
        assert await env.registry.async_gc_tombstones(oldest_retained_evidence_at=None) == 0
        listing, _ = await env.call(USER_A, "models/list")
        assert listing["models"] == []
        assert env.service_calls() == 0
    run(_go())


def test_habit_accept_saves_routine_without_executing(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        habit = habit_model(USER_A)
        await env.registry.async_upsert(habit)
        preview, error = await env.call(USER_A, "habits/preview", ref=model_ref(habit.model_id))
        assert error is None
        assert [step["entity_label"] for step in preview["steps"]] == [
            "Küchenlicht", "Rollladen Küche", "Kaffeemaschine",
        ]
        result, error = await env.call(USER_A, "habits/accept", ref=model_ref(habit.model_id))
        assert error is None and result["accepted"] and result["step_count"] == 3
        routine = env.profiles.routine(habit.model_id.replace(":", "_"), user_id=USER_A)
        assert routine is not None and routine.confirmed and routine.owner_user_id == USER_A
        stored = await env.registry.async_get(habit.model_id)
        assert stored.parameters["suggestion_status"] == "accepted"
        _result, error = await env.call(USER_A, "habits/accept", ref=model_ref(habit.model_id))
        assert error[0] == "invalid_state"
        assert env.service_calls() == 0
    run(_go())


# -- Scenario 4: thermal drift in overview ----------------------------------------------

def test_scenario_4_thermal_drift_needs_attention(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(thermal_model(health=ModelHealth.DRIFT_DETECTED))
        summary, _ = await env.call(USER_A, "summary")
        assert summary["needs_attention"] == 1
        assert summary["attention"][0]["kind"] == "model_drift"
        detail, _ = await env.call(USER_A, "models/get", ref=model_ref("thermal:living"))
        assert detail["model"]["status"] == "drift"
        assert detail["model"]["health"] == "drift_detected"
        assert "holdout_mae_seconds" in {fact["key"] for fact in detail["model"]["facts"]}
    run(_go())


def test_low_confidence_is_learning_not_a_warning(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(replace(thermal_model(), health=ModelHealth.LOW_CONFIDENCE))
        summary, _ = await env.call(USER_A, "summary")
        assert summary["needs_attention"] == 0
        assert summary["learning_models"] == 1
    run(_go())


# -- Scenario 5: revoke standing permission --------------------------------------------

def test_scenario_5_permission_revoke(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        env.engine.permissions.add(standing_permission(USER_A))
        listing, _ = await env.call(USER_A, "permissions/list")
        view = listing["permissions"][0]
        assert view["owned_by_viewer"] and view["can_revoke"]
        assert view["entities"] == [{"label": "Wohnzimmerlicht", "missing": False}]
        assert view["conditions"] == ["nobody_home"]
        assert view["attempts_today"] == 0 and view["max_per_day"] == 6
        _result, error = await env.call(USER_B, "permissions/revoke", permission_id=view["permission_id"])
        assert error[0] == "not_found"
        other, _ = await env.call(USER_B, "permissions/list")
        assert other["permissions"] == []
        result, error = await env.call(USER_A, "permissions/revoke", permission_id=view["permission_id"])
        assert error is None and result["revoked"]
        permission = env.engine.permissions.all()[0]
        assert permission.revoked is True
        assert env.engine.persist_count == 1
        assert env.engine.permissions.matching(
            SimpleNamespace(kind=SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING, area_id="living"), NOW,
        ) is None
        decision = AutoExecutionPolicy().evaluate(
            permission, enabled=True, situation=SimpleNamespace(), owner_known=True,
            entities={}, nobody_home=True, now=NOW, attempts_today=0,
        )
        assert decision.allowed is False and decision.reasons == ("permission_revoked",)
        _result, error = await env.call(USER_A, "permissions/revoke", permission_id=view["permission_id"])
        assert error[0] == "invalid_state"
        assert env.service_calls() == 0
    run(_go())


def test_admin_can_revoke_any_permission_for_safety(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        env.engine.permissions.add(standing_permission(USER_A))
        listing, _ = await env.call(ADMIN, "permissions/list")
        assert listing["permissions"][0]["owner_label"] == "Anna"
        assert listing["permissions"][0]["can_revoke"]
        result, error = await env.call(ADMIN, "permissions/revoke", permission_id="perm_user_a")
        assert error is None and result["revoked"]
        assert env.service_calls() == 0
    run(_go())


def test_permission_expiring_soon_is_an_attention_item(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        env.engine.permissions.add(standing_permission(USER_A, expires_in=timedelta(days=3)))
        summary, _ = await env.call(USER_A, "summary")
        assert [item["kind"] for item in summary["attention"]] == ["permission_expiring"]
        other, _ = await env.call(USER_B, "summary")
        assert other["attention"] == []
    run(_go())


# -- mutes --------------------------------------------------------------------------------

def test_mutes_are_personal_and_removed_through_attention_store(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        env.engine.attention_state.mute(USER_A, SituationKind.ENTRY_LEFT_OPEN, NOW)
        mine, _ = await env.call(USER_A, "mutes/list")
        assert mine["mutes"] == [{"situation_kind": "entry_left_open", "confirmed_at": NOW.isoformat(), "can_remove": True}]
        assert mine["non_mutable_situations"] == ["critical_safety_event"]
        theirs, _ = await env.call(USER_B, "mutes/list")
        admin, _ = await env.call(ADMIN, "mutes/list")
        assert theirs["mutes"] == [] and admin["mutes"] == []
        _result, error = await env.call(USER_B, "mutes/remove", situation_kind="entry_left_open")
        assert error[0] == "not_found"
        assert env.engine.attention_state.is_muted(USER_A, SituationKind.ENTRY_LEFT_OPEN)
        result, error = await env.call(USER_A, "mutes/remove", situation_kind="entry_left_open")
        assert error is None and result["removed"]
        assert not env.engine.attention_state.is_muted(USER_A, SituationKind.ENTRY_LEFT_OPEN)
        _result, error = await env.call(USER_A, "mutes/remove", situation_kind="made_up")
        assert error[0] == "not_found"
        assert env.service_calls() == 0
    run(_go())


# -- Scenario 6: admin reset -----------------------------------------------------------------

def test_scenario_6_admin_reset_keeps_other_authorities(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await learn_reliability(env, successes=6, failures=0)
        await env.registry.async_upsert(thermal_model())
        await env.registry.async_upsert(preference_model(USER_A))
        env.house.install_thermal  # active view populated by the registry binding
        assert env.house.effect_timing_model("LIGHT_TURN_ON", "light.kitchen") is not None
        env.engine.permissions.add(standing_permission(USER_A))
        env.engine.attention_state.mute(USER_A, SituationKind.ENTRY_LEFT_OPEN, NOW)
        from homeintent.proactive_model import (
            CommunicationChannel, HistoryRecord, OpportunityOutcome, PriorityLevel, PrivacyLevel,
        )
        env.engine.history.append(HistoryRecord(
            "h1", "s1", SituationKind.ENTRY_LEFT_OPEN, "Garage", OpportunityOutcome.COMMUNICATE,
            USER_A, CommunicationChannel.PUSH, NOW, PriorityLevel.IMPORTANT, PrivacyLevel.HOUSEHOLD,
            "delivered",
        ))
        before = len(await env.registry.async_list())
        result, error = await env.call(ADMIN, "models/reset", confirm=True)
        assert error is None and result["deleted"] == before
        assert await env.registry.async_list() == ()
        assert env.house.effect_timing_model("LIGHT_TURN_ON", "light.kitchen") is None
        assert env.house.thermal_model("living") is None
        assert len(await env.registry.async_list_tombstones()) == before
        assert len(env.engine.permissions.all()) == 1 and not env.engine.permissions.all()[0].revoked
        assert env.engine.attention_state.is_muted(USER_A, SituationKind.ENTRY_LEFT_OPEN)
        assert len(env.engine.history) == 1
        summary, _ = await env.call(ADMIN, "summary")
        assert summary["total_models"] == 0
        assert env.service_calls() == 0
    run(_go())


# -- Scenarios 7/8: Voice <-> UI one truth ----------------------------------------------

def test_scenario_7_voice_forget_refreshes_panel(tmp_path, monkeypatch):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(thermal_model())
        subscribed, _ = await env.call(ADMIN, "subscribe")
        start = subscribed["revision"]
        agent = _voice_agent(env, monkeypatch)
        speech = await _say(agent, "Vergiss das gelernte Modell der Heizung", user=ADMIN)
        assert "wirklich löschen" in speech
        assert "gelöscht" in await _say(agent, "Ja", user=ADMIN)
        events = env.conn(ADMIN).events
        assert events and events[-1]["event"]["revision"] > start
        assert set(events[-1]["event"]) == {"entry_id", "revision"}
        listing, _ = await env.call(ADMIN, "models/list")
        assert listing["models"] == []
        tombstone = (await env.registry.async_list_tombstones())[0]
        assert tombstone.suppression_kind is SuppressionKind.FORGET
        agent.hass.services.async_call.assert_not_awaited()
    run(_go())


def test_scenario_8_ui_forget_is_immediately_visible_to_voice(tmp_path, monkeypatch):
    async def _go():
        env = await make_env(tmp_path)
        await learn_reliability(env, successes=6, failures=0)
        agent = _voice_agent(env, monkeypatch)
        before = await _say(agent, "Was hast du gelernt?")
        assert "Zuverlässigkeit" in before
        assert before.endswith("im HomeIntent Learning Center.")
        ref = model_ref("reliability:LIGHT_TURN_ON:light.kitchen")
        result, error = await env.call(ADMIN, "models/forget", ref=ref)
        assert error is None and result["forgotten"]
        after = await _say(agent, "Was hast du gelernt?", conversation_id="lc2")
        assert "Zuverlässigkeit" not in after
        # Retained old evidence cannot resurrect the model immediately.
        await env.manager.async_observe_goal_run(goal_run("late", user_id=None, at=NOW - timedelta(hours=3)))
        assert await env.registry.async_get("reliability:LIGHT_TURN_ON:light.kitchen") is None
        assert env.house.predict_reliability("LIGHT_TURN_ON", "light.kitchen").model_id is None
        agent.hass.services.async_call.assert_not_awaited()
    run(_go())


def test_voice_listing_never_describes_another_users_personal_model(tmp_path, monkeypatch):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(preference_model(USER_A))
        agent = _voice_agent(env, monkeypatch)
        speech = await _say(agent, "Was hast du gelernt?", user=USER_B)
        assert "lampe" not in speech.casefold()
        speech = await _say(agent, "Was weißt du über meine Lichtpräferenzen?", user=USER_B, conversation_id="b2")
        assert "Soll das" not in speech
    run(_go())


def test_control_layer_refuses_wrong_owner_directly(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        pref = preference_model(USER_A)
        await env.registry.async_upsert(pref)
        with pytest.raises(LearningControlError) as raised:
            await async_confirm_preference(env.registry, pref.model_id, USER_B)
        assert raised.value.code is ControlErrorCode.WRONG_OWNER
    run(_go())


# -- privacy / API security E2E ------------------------------------------------------------

def test_privacy_e2e_personal_habit_omitted_from_backend_response(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(habit_model(USER_A))
        summary, _ = await env.call(USER_B, "summary")
        listing, _ = await env.call(USER_B, "models/list")
        assert summary["habit_count"] == 0 and summary["total_models"] == 0
        assert summary["category_counts"] == {} and summary["attention"] == []
        assert listing["models"] == [] and listing["total"] == 0
        for result in (summary, listing):
            raw = json.dumps(result)
            assert "light.kitchen" not in raw and "Küchenlicht" not in raw
            assert "habit:" not in raw and USER_A not in raw
    run(_go())


def test_api_security_e2e_foreign_model_id_mutation_is_denied(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        habit = habit_model(USER_A)
        pref = preference_model(USER_A)
        await env.registry.async_upsert(habit)
        await env.registry.async_upsert(pref)
        before = [item.to_dict() for item in await env.registry.async_list()]
        for command, ref in (
            ("habits/reject", model_ref(habit.model_id)),
            ("habits/accept", model_ref(habit.model_id)),
            ("preferences/confirm", model_ref(pref.model_id)),
            ("models/forget", model_ref(pref.model_id)),
            ("models/forget", habit.model_id),  # raw ids are not refs either
        ):
            result, error = await env.call(USER_B, command, ref=ref)
            assert result is None and error[0] in {"not_found", "not_authorized"}
        assert [item.to_dict() for item in await env.registry.async_list()] == before
        assert await env.registry.async_list_tombstones() == ()
        denials = [item for item in env.runtime.learning_center_audit.entries() if item.result.startswith("denied")]
        assert len(denials) == 5
        assert env.service_calls() == 0
    run(_go())


# -- forget semantics / concurrency -----------------------------------------------------------

def test_forget_deactivates_predictive_view_and_tombstones(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await learn_reliability(env, successes=6, failures=0)
        assert env.house.effect_timing_model("LIGHT_TURN_ON", "light.kitchen") is not None
        ref = model_ref("effect_latency:LIGHT_TURN_ON:light.kitchen")
        result, error = await env.call(ADMIN, "models/forget", ref=ref)
        assert error is None
        assert env.house.effect_timing_model("LIGHT_TURN_ON", "light.kitchen") is None
        assert await env.registry.async_is_suppressed("effect_latency:LIGHT_TURN_ON:light.kitchen")
        _result, error = await env.call(ADMIN, "models/forget", ref=ref)
        assert error[0] == "not_found"
    run(_go())


def test_forgetting_global_preference_removes_its_alias_rule(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        env.entry.options = {"custom_aliases": "lampe = light.floor_lamp\nsofa = light.living"}
        pref = replace(preference_model(USER_A, area=None, state=KnowledgeState.CONFIRMED),
                       model_id="preference:global")
        await env.registry.async_upsert(pref)
        result, error = await env.call(USER_A, "models/forget", ref=model_ref(pref.model_id))
        assert error is None and result["forgotten"]
        assert env.entry.options["custom_aliases"] == "sofa = light.living"
    run(_go())


def test_concurrent_forget_and_learning_upsert_respect_registry_lock(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await learn_reliability(env, successes=6, failures=0)
        ref = model_ref("reliability:LIGHT_TURN_ON:light.kitchen")
        await asyncio.gather(
            env.call(ADMIN, "models/forget", ref=ref),
            env.manager.async_observe_goal_run(goal_run("concurrent", user_id=None)),
        )
        assert await env.registry.async_get("reliability:LIGHT_TURN_ON:light.kitchen") is None
        assert await env.registry.async_is_suppressed("reliability:LIGHT_TURN_ON:light.kitchen")
    run(_go())


def test_concurrent_double_confirm_changes_state_once(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        pref = preference_model(USER_A)
        await env.registry.async_upsert(pref)
        ref = model_ref(pref.model_id)
        outcomes = await asyncio.gather(
            env.call(USER_A, "preferences/confirm", ref=ref),
            env.call(USER_A, "preferences/confirm", ref=ref),
        )
        assert sorted(error is None for _result, error in outcomes) == [False, True]
        stored = await env.registry.async_get(pref.model_id)
        assert stored.knowledge_state is KnowledgeState.CONFIRMED
    run(_go())


def test_kinds_are_checked_per_operation(tmp_path):
    async def _go():
        env = await make_env(tmp_path)
        await env.registry.async_upsert(thermal_model())
        ref = model_ref("thermal:living")
        _r, error = await env.call(ADMIN, "preferences/confirm", ref=ref)
        assert error[0] in {"unsupported_operation", "wrong_owner"}
        _r, error = await env.call(ADMIN, "habits/reject", ref=ref)
        assert error[0] in {"unsupported_operation", "wrong_owner"}
        assert (await env.registry.async_get("thermal:living")).kind is LearnedKind.THERMAL_MODEL
    run(_go())
