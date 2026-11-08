"""Conversation and adversarial boundary regressions; no physical hardware."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from hypothesis import given, settings, strategies as st

import _ha_stub
_ha_stub.install()

from homeassistant.core import State
from homeintent.controllers.devices import DeviceController
from homeintent.nlu.clause_binding import analyse_clause_bindings
from homeintent.nlu.language_frontend import analyse_language
from homeintent.service_call import ServiceCallPlan
from test_review_regressions_7133 import house, both_sources, inject_one_lamp_failure, JOINERS
from test_clause_binding_7132 import source_entities, RADIO, SPEAKER, TV


@pytest.mark.parametrize("joiner", JOINERS)
@pytest.mark.parametrize("prefix", ["In der Küche", "Doch im Schlafzimmer"])
def test_every_amendment_clause_is_consumed_and_rejection_clears_yes(monkeypatch, joiner, prefix):
    h = house(monkeypatch, both_sources())
    h.say("Spiel WDR 2 in der Küche." if prefix.startswith("Doch") else "Spiel WDR 2.")
    assert h.say(f"{prefix} {joiner} Quirlomat.").calls == []
    assert h.say("Ja").calls == []


@pytest.mark.parametrize("reply", ["Nein, abbrechen.", "In der Küche, aber morgen.", "Nicht in der Küche."])
def test_discarded_selection_cannot_be_reactivated(monkeypatch, reply):
    h = house(monkeypatch)
    h.say("Spiel WDR 2.")
    assert h.say(reply).calls == []
    assert h.say("Ja").calls == []


def test_positive_selection_and_new_device_command(monkeypatch):
    h = house(monkeypatch)
    h.say("Spiel WDR 2.")
    assert h.say("In der Küche.").targets == {RADIO}
    h.say("Spiel WDR 2.")
    turn = h.say("Mach das Bürolicht aus.")
    assert turn.targets == {"light.buerolicht"}
    assert h.say("Ja").calls == []


def confirmation_preview(monkeypatch):
    """Offer the existing plan confirmation, retaining all product validators."""
    original = DeviceController.async_handle_command_plan

    async def preview(self, user_input, response, result, entities):
        return await original(self, user_input, response,
                              replace(result, confirmation_text="Soll ich den Auftrag ausführen?"), entities)
    monkeypatch.setattr(DeviceController, "async_handle_command_plan", preview)


CHAIN = "Spiel WDR 2 in der Küche und mach das Licht im Büro aus und pausiere die Musik im Schlafzimmer."


@pytest.mark.parametrize("confirmed", [False, True])
def test_partial_batch_fail_fast_and_no_speculative_undo(monkeypatch, confirmed):
    if confirmed:
        confirmation_preview(monkeypatch)
    h = house(monkeypatch)
    inject_one_lamp_failure(h)
    turn = h.say(CHAIN)
    if confirmed:
        assert turn.calls == []
        turn = h.say("Ja")
    assert RADIO in turn.targets
    assert SPEAKER not in turn.targets
    assert "3 Lichter ausgeschaltet" not in turn.speech
    assert "Deckenfluter" in turn.speech
    assert "nicht mehr ausgeführt" in turn.speech
    assert h.say("Ja").calls == []
    assert h.say("Mach das rückgängig.").calls == []


def test_confirmed_sequence_rechecks_all_clauses_before_first_write(monkeypatch):
    confirmation_preview(monkeypatch)
    h = house(monkeypatch)
    assert h.say(CHAIN).calls == []
    h.entities = [replace(e, state="unavailable") if e.entity_id == SPEAKER else e
                  for e in h.entities]
    assert h.say("Ja").calls == []
    assert h.say("Ja").calls == []


def test_state_change_during_execution_stops_later_clauses(monkeypatch):
    h = house(monkeypatch)
    original = h.entity.hass.services.async_call.side_effect

    async def fail_later(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "select_source":
            h.entity.hass.states._states[SPEAKER] = State(SPEAKER, "unavailable", {})
        return result

    h.entity.hass.services.async_call.side_effect = fail_later
    turn = h.say("Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer und mach das Bürolicht aus.")
    assert turn.targets == {RADIO}
    assert "nicht mehr ausgeführt" in turn.speech


@pytest.mark.parametrize("failure", ["capability", "permission", "service", "schema"])
def test_common_preflight_rejects_known_second_clause_failure(monkeypatch, failure):
    import voluptuous as vol
    h = house(monkeypatch)
    if failure == "capability":
        h.entities = [replace(e, capabilities=frozenset({"PLAY"})) if e.entity_id == SPEAKER else e
                      for e in h.entities]
    elif failure == "permission":
        async def user(_):
            return SimpleNamespace(is_admin=True, permissions=SimpleNamespace(
                check_entity=lambda entity_id, policy: entity_id != SPEAKER))
        h.entity.hass.auth.async_get_user = user
    else:
        for entity in h.entities:
            h.entity.hass.states._states[entity.entity_id] = State(entity.entity_id, entity.state, dict(entity.attributes))
        def services(domain):
            schema = vol.Schema({vol.Required("entity_id"): str}) if failure == "schema" else None
            return {"select_source": SimpleNamespace(schema=None), **(
                {} if failure == "service" else {"media_pause": SimpleNamespace(
                    schema=vol.Schema({vol.Required("missing_parameter"): str}) if schema else None)})}
        h.entity.hass.services.async_services_for_domain = services
    turn = h.say("Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.")
    assert turn.calls == []
    if failure == "service":
        assert "Dienst" in turn.speech
    elif failure == "schema":
        assert "Aktionsparameter" in turn.speech
    assert h.say("Ja").calls == []


@given(target=st.sampled_from([TV, RADIO, "light.buerolicht"]),
       extra=st.booleans(), data=st.sampled_from([{}, {"volume_level": 0.7}, {"source": "WDR 2"}]),
       joiner=st.sampled_from(JOINERS))
@settings(max_examples=55, deadline=None)
def test_validator_never_accepts_foreign_media_targets(target, extra, data, joiner):
    entities = source_entities()
    doc = analyse_language(f"Spiel WDR 2 in der Küche {joiner} pausiere die Musik im Schlafzimmer.", entities)
    bad = [SPEAKER, target] if extra else target
    payload = SimpleNamespace(commands=(
        SimpleNamespace(plan=ServiceCallPlan("media_player", "select_source", RADIO, {"source": "WDR 2"})),
        SimpleNamespace(plan=ServiceCallPlan("media_player", "media_pause", bad, data)),
    ))
    assert not analyse_clause_bindings(doc, entities, payload).complete


@pytest.mark.parametrize("service,data", [("media_stop", {}), ("media_pause", {"volume_level": 0.3})])
def test_validator_rejects_wrong_operation_and_unrequested_parameters(service, data):
    entities = source_entities()
    doc = analyse_language("Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.", entities)
    payload = SimpleNamespace(commands=(
        SimpleNamespace(plan=ServiceCallPlan("media_player", "select_source", RADIO, {"source": "WDR 2"})),
        SimpleNamespace(plan=ServiceCallPlan("media_player", service, SPEAKER, data)),
    ))
    assert not analyse_clause_bindings(doc, entities, payload).complete


@pytest.mark.parametrize("reply", ["Im Wohnzimmer, aber nicht jetzt.", "Nicht im Wohnzimmer.",
                                   "Im Wohnzimmer und Quirlomat."])
def test_music_assistant_player_selection_cannot_ignore_amendments(monkeypatch, reply):
    from test_music_assistant_710 import _player, _response
    h = house(monkeypatch, [_player(), replace(_player(), entity_id="media_player.other",
                                             friendly_name="Büroplayer", area_id="office", area_name="Büro")])
    h.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    h.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    h.entity.hass.services.async_call.return_value = _response(
        artists=[{"name": "Queen", "uri": "library://artist/1"}])
    h.say("Spiel Queen.")
    assert not any(service == "play_media" for _, service, _ in h.say(reply).calls)
    assert not any(service == "play_media" for _, service, _ in h.say("Ja").calls)


def test_unconfirmed_history_is_not_reported_as_effect(monkeypatch):
    h = house(monkeypatch, both_sources())
    h.say("Spiel WDR 2 in der Küche.")
    turn = h.say("Doch im Schlafzimmer.")
    assert turn.targets == {SPEAKER}
    assert "nicht bestätigt" in turn.speech
    assert "erste Wiedergabe war bereits gestartet" not in turn.speech


@pytest.mark.parametrize("reply", ["Den Künstler, aber nicht jetzt.", "Nicht den Künstler.",
                                   "Den Künstler und Quirlomat."])
def test_music_assistant_type_selection_is_complete(monkeypatch, reply):
    from test_music_assistant_710 import _player, _response
    h = house(monkeypatch, [_player()])
    h.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    h.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    h.entity.hass.services.async_call.return_value = _response(
        artists=[{"name": "Queen", "uri": "library://artist/1"}],
        tracks=[{"name": "Queen", "uri": "library://track/2"}])
    first = h.say("Spiele Queen im Wohnzimmer.")
    assert "Welche meinst du" in first.speech
    assert not any(service == "play_media" for _, service, _ in h.say(reply).calls)
    assert not any(service == "play_media" for _, service, _ in h.say("Ja").calls)


@pytest.mark.parametrize("suffix", ["aber nicht jetzt", "aber erst in zehn Minuten", "und Quirlomat"])
def test_direct_source_command_has_same_no_partial_write_contract(monkeypatch, suffix):
    h = house(monkeypatch)
    assert h.say("Spiel WDR 2 in der Küche, " + suffix + ".").calls == []
    assert h.say("Ja").calls == []


def test_confirmation_and_denial_are_not_stored_as_started(monkeypatch):
    h = house(monkeypatch, both_sources())
    h.entity.entry.options["read_only_entities"] = [RADIO]
    assert h.say("Spiel WDR 2 in der Küche.").calls == []
    corrected = h.say("Doch im Schlafzimmer.")
    assert corrected.targets == {SPEAKER}
    assert "erste Wiedergabe war bereits gestartet" not in corrected.speech


def test_media_source_effect_is_not_proof_of_playback():
    from homeintent.effect_wait import EffectExpectation, EffectVerdict, judge
    expectation = EffectExpectation(RADIO, "Testplayer", "media_player", "play_media",
                                    {"media_id": "library://track/1"}, None)
    assert judge(expectation, "playing", {"media_content_id": "library://track/other"}) is EffectVerdict.PENDING
    assert judge(expectation, "paused", {"media_content_id": "library://track/1"}) is EffectVerdict.PENDING
    assert judge(expectation, "playing", {"media_content_id": "library://track/1"}) is EffectVerdict.REACHED


def test_expired_media_selection_cannot_run(monkeypatch):
    from datetime import timedelta
    h = house(monkeypatch)
    h.say("Spiel WDR 2.")
    manager = h.entity._runtime_data.dialog_manager
    from homeintent.conversation_scope import conversation_scope_key_for_input
    scope = conversation_scope_key_for_input(h.conversation_input("Spiel WDR 2."))
    task = manager.active(scope)
    assert task is not None
    manager.add(scope, replace(task, expires_at=task.created_at - timedelta(seconds=1)))
    assert h.say("In der Küche.").calls == []
    assert h.say("Ja").calls == []


def test_music_assistant_selection_preserves_queue_mode(monkeypatch):
    from test_music_assistant_710 import _player, _response
    h = house(monkeypatch, [_player(), _player("media_player.kueche", area="Küche")])
    h.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    h.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    h.entity.hass.services.async_call.return_value = _response(
        tracks=[{"name": "One", "uri": "library://track/1"}])
    assert not any(service == "play_media" for _, service, _ in h.say("Spiele den Titel One als Nächstes.").calls)
    writes = [(domain, service, data) for domain, service, data in h.say("Im Wohnzimmer.").calls
              if service == "play_media"]
    assert len(writes) == 1
    assert writes[0][2]["enqueue"] == "next"


@pytest.mark.parametrize("tail,targets", [
    ("pausiere die Musik im Schlafzimmer und pausiere die Musik im Wohnzimmer", {RADIO, SPEAKER, TV}),
    ("mach das Licht im Büro aus", {RADIO, "light.buerolicht", "light.deckenfluter_buero", "light.schreibtischlampe"}),
])
def test_valid_multiroom_and_batch_commands_remain_usable(monkeypatch, tail, targets):
    h = house(monkeypatch)
    assert h.say("Spiel WDR 2 in der Küche und " + tail + ".").targets == targets


def test_music_assistant_type_and_queue_are_preserved_through_location_correction(monkeypatch):
    from test_music_assistant_710 import _player, _response
    players = [_player(), replace(_player(), entity_id=SPEAKER, friendly_name="Schlafzimmerbox",
                                 area_id="schlafzimmer", area_name="Schlafzimmer")]
    h = house(monkeypatch, players)
    h.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    h.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    h.entity.hass.services.async_call.return_value = _response(tracks=[{"name": "One", "uri": "library://track/1"}])
    first = h.say("Spiele den Titel One im Wohnzimmer als Nächstes.")
    assert any(service == "play_media" for _, service, _ in first.calls)
    corrected = h.say("Doch im Schlafzimmer.")
    writes = [data for _, service, data in corrected.calls if service == "play_media"]
    assert len(writes) == 1
    assert writes[0] == {"entity_id": SPEAKER, "media_id": "library://track/1", "media_type": "track", "enqueue": "next"}


def test_partial_standalone_batch_undo_does_not_overwrite_later_changes(monkeypatch):
    h = house(monkeypatch)
    inject_one_lamp_failure(h)
    first = h.say("Mach das Licht im Büro aus.")
    assert "3 Lichter ausgeschaltet" not in first.speech
    h.entity.hass.states._states["light.buerolicht"] = State("light.buerolicht", "on", {"brightness": 71})
    assert h.say("Mach das rückgängig.").calls == []


def test_partial_standalone_batch_undo_only_contains_successful_unchanged_targets(monkeypatch):
    h = house(monkeypatch)
    inject_one_lamp_failure(h)
    h.say("Mach das Licht im Büro aus.")
    undone = h.say("Mach das rückgängig.")
    assert undone.targets == {"light.buerolicht", "light.schreibtischlampe"}
    assert h.say("Mach das rückgängig.").calls == []



def media_confirmation_preview(monkeypatch):
    """Exercise the normal policy-confirmation boundary for a media plan."""
    from homeintent.plan_origin import PlanOrigin
    original = DeviceController.async_handle_match_result

    async def preview(self, user_input, response, result, entities, **kwargs):
        return await original(self, user_input, response,
            replace(result, origin=PlanOrigin.IMPLICIT_NEED), entities, **kwargs)
    monkeypatch.setattr(DeviceController, "async_handle_match_result", preview)

def test_confirmed_media_call_does_not_claim_unobserved_effect(monkeypatch):
    h = house(monkeypatch, both_sources())
    media_confirmation_preview(monkeypatch)
    assert h.say("Spiel WDR 2 in der Küche.").calls == []
    accepted = h.say("Ja")
    assert accepted.targets == {RADIO}
    assert "angenommen" in accepted.speech and "nicht bestätigt" in accepted.speech
    corrected = h.say("Doch im Schlafzimmer.")
    assert corrected.calls == []  # The correction also needs its own confirmation.
    assert "noch nicht bestätigt" in corrected.speech
    assert "erste Wiedergabe war bereits gestartet" not in corrected.speech
    assert h.say("Nein, abbrechen.").calls == []
    assert h.say("Ja").calls == []


def test_unexecuted_media_preview_is_remembered_as_preview(monkeypatch):
    h = house(monkeypatch, both_sources())
    media_confirmation_preview(monkeypatch)
    assert h.say("Spiel WDR 2 in der Küche.").calls == []
    corrected = h.say("Doch im Schlafzimmer.")
    assert corrected.calls == []
    assert "noch nicht ausgeführt" in corrected.speech
    assert h.say("Ja").targets == {SPEAKER}


@pytest.mark.parametrize("suffix", ["aber nicht jetzt", "aber erst in zehn Minuten", "und Quirlomat"])
def test_rejected_media_preview_correction_discards_confirmation(monkeypatch, suffix):
    media_confirmation_preview(monkeypatch)
    h = house(monkeypatch, both_sources())
    assert h.say("Spiel WDR 2 in der Küche.").calls == []
    assert h.say("Doch im Schlafzimmer, " + suffix + ".").calls == []
    assert h.say("Ja").calls == []


@pytest.mark.parametrize("confirmed", [False, True])
def test_observed_failure_after_accepted_call_stops_following_clause(monkeypatch, confirmed):
    if confirmed:
        confirmation_preview(monkeypatch)
    h = house(monkeypatch)
    original = h.entity.hass.services.async_call.side_effect

    async def unavailable_after_call(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if (data or {}).get("entity_id") == "light.buerolicht":
            h.entity.hass.states._states["light.buerolicht"] = State("light.buerolicht", "unavailable", {})
        return result

    h.entity.hass.services.async_call.side_effect = unavailable_after_call
    turn = h.say("Spiel WDR 2 in der Küche und mach das Bürolicht aus und pausiere die Musik im Schlafzimmer.")
    if confirmed:
        assert turn.calls == []
        turn = h.say("Ja")
    assert turn.targets == {RADIO, "light.buerolicht"}
    assert "nicht mehr ausgeführt" in turn.speech
    assert "Bürolicht ausgeschaltet" not in turn.speech


@pytest.mark.parametrize("confirmed", [False, True])
def test_observed_source_selection_is_not_reported_as_playback(monkeypatch, confirmed):
    if confirmed:
        media_confirmation_preview(monkeypatch)
    h = house(monkeypatch, both_sources())
    original = h.entity.hass.services.async_call.side_effect

    async def select_without_playback(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "select_source":
            h.entity.hass.states._states[RADIO] = State(
                RADIO, "paused", {"source": data["source"], "source_list": ["WDR 2"]})
        return result

    h.entity.hass.services.async_call.side_effect = select_without_playback
    turn = h.say("Spiel WDR 2 in der Küche.")
    if confirmed:
        assert turn.calls == []
        turn = h.say("Ja")
    assert turn.targets == {RADIO}
    assert "spielt" not in turn.speech
    assert "ausgewählt" in turn.speech and "WDR 2" in turn.speech


def test_current_track_does_not_confirm_queue_mutation():
    from homeintent.effect_wait import EffectExpectation, EffectVerdict, judge
    expectation = EffectExpectation(RADIO, "Testplayer", "music_assistant", "play_media",
        {"media_id": "library://track/1", "enqueue": "next"}, None)
    assert judge(expectation, "playing", {"media_content_id": "library://track/1"}) is EffectVerdict.PENDING


@pytest.mark.parametrize("confirmed", [False, True])
def test_media_observed_failure_is_reported_and_remembered(monkeypatch, confirmed):
    from homeassistant.helpers import intent
    errors = []
    original_error = intent.IntentResponse.async_set_error

    def record_error(self, code, message):
        errors.append(code)
        return original_error(self, code, message)

    # The lightweight HA response double records error_code but does not
    # update response_type; observe the real controller's error boundary.
    monkeypatch.setattr(intent.IntentResponse, "async_set_error", record_error)
    if confirmed:
        media_confirmation_preview(monkeypatch)
    h = house(monkeypatch, both_sources())
    original = h.entity.hass.services.async_call.side_effect

    async def unavailable_after_call(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if (data or {}).get("entity_id") == RADIO:
            h.entity.hass.states._states[RADIO] = State(RADIO, "unavailable", {})
        return result

    h.entity.hass.services.async_call.side_effect = unavailable_after_call
    turn = h.say("Spiel WDR 2 in der Küche.")
    if confirmed:
        assert turn.calls == []
        turn = h.say("Ja")
    assert turn.targets == {RADIO}
    assert "nicht verfügbaren Zustand" in turn.speech
    assert errors[-1] == intent.IntentResponseErrorCode.FAILED_TO_HANDLE
    corrected = h.say("Doch im Schlafzimmer.")
    assert "Fehler" in corrected.speech
    assert "erste Wiedergabe war bereits gestartet" not in corrected.speech


@pytest.mark.parametrize("supported", [False, True])
def test_preflight_checks_known_transitive_empty_capabilities(monkeypatch, supported):
    import asyncio
    from homeintent import service_executor
    from homeintent.effect_graph import build_plan_effects_from_sources
    from test_effect_graph import FakeSources, snap

    player = snap(SPEAKER, state="playing", attributes={"supported_features": int(supported)},
                  capabilities=frozenset({"PAUSE"}) if supported else frozenset())
    script = snap("script.pause_music")
    h = house(monkeypatch, [script, player])
    sources = FakeSources(scripts={script.entity_id: [
        {"action": "media_player.media_pause", "target": {"entity_id": SPEAKER}}
    ]}, existing={script.entity_id, SPEAKER})
    plan = ServiceCallPlan("script", "turn_on", script.entity_id)
    effects = build_plan_effects_from_sources(plan.domain, plan.service, (script.entity_id,), sources)
    monkeypatch.setattr(service_executor, "build_plan_effects", lambda *_: effects)
    result = asyncio.run(service_executor.async_preflight_service_plan(
        h.entity.hass, plan, h.entities, {}, is_admin=True, user_id=None, confirmed=True))
    assert (result.error is None) is supported
    h.entity.hass.services.async_call.assert_not_awaited()
