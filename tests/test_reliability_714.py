"""v7.14 reliability regressions through the real conversation pipeline."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

import _ha_stub

_ha_stub.install()

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from test_batch_settle_7131 import FakeBus
from test_clause_binding_7132 import source_entities
from test_music_assistant_710 import _player, _response
from test_review_regressions_7133 import house, inject_one_lamp_failure
from homeintent.service_call import ServiceCallPlan
from homeintent.undo import UndoPlan, async_bind_undo_when_observed


def _music_house(monkeypatch, *, options=None):
    players = [
        _player(),
        replace(
            _player(),
            entity_id="media_player.bedroom",
            friendly_name="Schlafzimmerbox",
            area_id="bedroom",
            area_name="Schlafzimmer",
        ),
        replace(
            _player(),
            entity_id="media_player.kitchen",
            friendly_name="Küchenbox",
            area_id="kitchen",
            area_name="Küche",
        ),
    ]
    h = house(monkeypatch, players)
    if options:
        h.entity.entry.options.update(options)
    h.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    h.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    h.entity.hass.services.async_call.return_value = _response(
        tracks=[{"name": "One", "uri": "library://track/1"}],
    )
    return h


def _force_match_confirmation(monkeypatch, h):
    """Route a parsed plan through the product's ordinary confirmation state."""

    from homeintent.plan_origin import PlanOrigin

    controller_type = type(h.entity._devices)
    original = controller_type.async_handle_match_result

    async def preview(self, user_input, response, result, entities, **kwargs):
        return await original(
            self,
            user_input,
            response,
            replace(result, origin=PlanOrigin.IMPLICIT_NEED),
            entities,
            **kwargs,
        )

    monkeypatch.setattr(controller_type, "async_handle_match_result", preview)


@pytest.mark.parametrize(
    ("initial", "reply", "mode"),
    [
        ("Spiel den Titel One.", "Spiel es im Wohnzimmer als Nächstes.", "next"),
        ("Spiel den Titel One.", "Spiel es im Wohnzimmer zur Warteschlange hinzu.", "add"),
        ("Spiel den Titel One.", "Spiel es im Wohnzimmer danach in die Warteschlange.", "add"),
        ("Spiel den Titel One.", "Füge es im Wohnzimmer der Queue hinzu.", "add"),
        ("Spiel den Titel One als Nächstes.", "Im Wohnzimmer.", "next"),
        ("Spiel den Titel One zur Warteschlange hinzu.", "Im Wohnzimmer.", "add"),
        (
            "Spiel den Titel One als Nächstes.",
            "Spiel es im Wohnzimmer ersetze die Warteschlange.",
            "replace",
        ),
        ("Spiel den Titel One im Wohnzimmer.", "Doch im Schlafzimmer als Nächstes.", "next"),
        (
            "Spiel den Titel One im Wohnzimmer.",
            "Doch im Schlafzimmer zur Warteschlange hinzu.",
            "add",
        ),
    ],
)
def test_queue_semantics_survive_or_amend_media_continuations(
    monkeypatch, initial, reply, mode
):
    h = _music_house(monkeypatch)
    h.say(initial)

    turn = h.say(reply)

    writes = [data for _domain, service, data in turn.calls if service == "play_media"]
    assert len(writes) == 1, (turn.speech, turn.calls)
    assert writes[0]["enqueue"] == mode


def test_queue_modifier_does_not_consume_the_player_location(monkeypatch):
    h = _music_house(monkeypatch)
    assert h.say("Spiel den Titel One.").calls == []

    turn = h.say("Spiel es in der Küche zur Warteschlange hinzu.")

    writes = [data for _domain, service, data in turn.calls if service == "play_media"]
    assert len(writes) == 1, (turn.speech, turn.calls)
    assert writes[0]["entity_id"] == "media_player.kitchen"
    assert writes[0]["enqueue"] == "add"


def test_queue_directive_coverage_keeps_separate_location_words():
    from homeintent.media import media_words, queue_directive_positions

    words = media_words("Füge es im Wohnzimmer der Queue hinzu.")
    consumed, mode = queue_directive_positions(words)

    assert mode == "add"
    assert [word for index, word in enumerate(words) if index not in consumed] == [
        "es", "im", "wohnzimmer"
    ]


@pytest.mark.parametrize(
    "reply",
    [
        "Spiel es im Wohnzimmer nicht als Nächstes.",
        "Spiel es im Wohnzimmer morgen als Nächstes.",
        "Spiel es im Wohnzimmer als Nächstes mit Quirlomat.",
    ],
)
def test_unsafe_queue_amendments_never_write(monkeypatch, reply):
    h = _music_house(monkeypatch)
    assert h.say("Spiel den Titel One.").calls == []

    assert h.say(reply).calls == []


def test_cancelled_media_draft_cannot_be_confirmed_later(monkeypatch):
    h = _music_house(monkeypatch)
    assert h.say("Spiel den Titel One.").calls == []
    assert h.say("Abbrechen.").calls == []

    assert h.say("Ja.").calls == []


def test_media_type_selection_can_amend_queue_mode(monkeypatch):
    h = _music_house(monkeypatch)
    h.entity.hass.services.async_call.return_value = _response(
        tracks=[{"name": "One", "uri": "library://track/1"}],
        albums=[{"name": "One", "uri": "library://album/1"}],
    )
    first = h.say("Spiel One im Wohnzimmer.")
    assert not any(service == "play_media" for _domain, service, _data in first.calls)

    turn = h.say("Den Titel als Nächstes.")

    writes = [data for _domain, service, data in turn.calls if service == "play_media"]
    assert len(writes) == 1
    assert writes[0]["media_type"] == "track"
    assert writes[0]["enqueue"] == "next"


def test_explicit_source_replacement_and_queue_mode_form_one_new_request(monkeypatch):
    h = _music_house(monkeypatch)
    assert h.say("Spiel den Titel One.").calls == []
    h.entity.hass.services.async_call.return_value = _response(
        tracks=[{"name": "Two", "uri": "library://track/2"}],
    )

    turn = h.say("Spiel den Titel Two im Wohnzimmer als Nächstes.")

    writes = [data for _domain, service, data in turn.calls if service == "play_media"]
    assert len(writes) == 1
    assert writes[0]["media_id"] == "library://track/2"
    assert writes[0]["enqueue"] == "next"


def test_confirmed_queue_amendment_executes_the_validated_merged_mode(monkeypatch):
    h = _music_house(monkeypatch)
    _force_match_confirmation(monkeypatch, h)
    assert h.say("Spiel den Titel One.").calls == []
    preview = h.say("Spiel es im Wohnzimmer als Nächstes.")
    assert not any(service == "play_media" for _domain, service, _data in preview.calls)

    accepted = h.say("Ja.")

    writes = [
        data for _domain, service, data in accepted.calls if service == "play_media"
    ]
    assert len(writes) == 1
    assert writes[0]["enqueue"] == "next"


@pytest.mark.parametrize(
    "text",
    [
        "Spiel WDR 2 in der Küche als Nächstes.",
        "Spiel WDR 2 in der Küche zur Warteschlange hinzu.",
    ],
)
def test_generic_media_player_never_silently_drops_queue_semantics(monkeypatch, text):
    h = house(monkeypatch)

    turn = h.say(text)

    assert turn.calls == []
    assert "Warteschlangenmodus" in turn.speech


def _light_house(monkeypatch, *, compound=False):
    ids = (
        {"light.buerolicht", "light.flurlicht"}
        if compound
        else {"light.buerolicht"}
    )
    entities = [
        replace(entity, state="off") if entity.entity_id in ids else entity
        for entity in source_entities()
    ]
    return house(monkeypatch, entities), ids


@pytest.mark.parametrize("compound", [False, True])
def test_undo_without_later_change_still_restores_all_observed_targets(
    monkeypatch, compound
):
    h, ids = _light_house(monkeypatch, compound=compound)
    command = (
        "Schalte das Bürolicht ein und schalte das Flurlicht ein."
        if compound
        else "Schalte das Bürolicht ein."
    )
    assert h.say(command).targets == ids

    assert h.say("Mach das rückgängig.").targets == ids
    assert h.say("Mach das rückgängig.").calls == []


@pytest.mark.parametrize("compound", [False, True])
@pytest.mark.parametrize("change", ["attributes", "context"])
def test_undo_never_overwrites_a_later_device_version(monkeypatch, compound, change):
    h, ids = _light_house(monkeypatch, compound=compound)
    command = (
        "Schalte das Bürolicht ein und schalte das Flurlicht ein."
        if compound
        else "Schalte das Bürolicht ein."
    )
    assert h.say(command).targets == ids
    changed = State("light.buerolicht", "on", {"brightness": 30})
    if change == "context":
        changed.attributes.clear()
        changed.context = SimpleNamespace(id="external-change")
    h.entity.hass.states._states["light.buerolicht"] = changed

    turn = h.say("Mach das rückgängig.")

    assert turn.calls == [], (turn.speech, turn.calls)
    assert "nichts zurückgenommen" in turn.speech


def test_confirmed_device_action_uses_the_same_state_bound_undo(monkeypatch):
    h, _ids = _light_house(monkeypatch)
    _force_match_confirmation(monkeypatch, h)
    assert h.say("Schalte das Bürolicht ein.").calls == []
    assert h.say("Ja.").targets == {"light.buerolicht"}
    h.entity.hass.states._states["light.buerolicht"] = State(
        "light.buerolicht", "on", {"brightness": 30}
    )

    turn = h.say("Mach das rückgängig.")

    assert turn.calls == []
    assert "nichts zurückgenommen" in turn.speech


@pytest.mark.parametrize("foreign", [False, True])
def test_delayed_cover_undo_binds_only_to_the_original_execution(foreign):
    """A moving cover becomes undoable at its exact, causal final report."""
    hass = HomeAssistant()
    hass.bus = FakeBus()
    entity_id = "cover.esszimmer_rollladen"
    moving = State(entity_id, "closing", {"current_position": 100})
    moving.context = SimpleNamespace(id="moving", parent_id="execution-1")
    hass.states._states[entity_id] = moving
    undo = UndoPlan(
        (
            ServiceCallPlan(
                "cover", "set_cover_position", entity_id, {"position": 100}
            ),
        ),
        "admin",
    )
    executed = ServiceCallPlan(
        "cover", "set_cover_position", entity_id, {"position": 40}
    )

    async def run():
        task = asyncio.create_task(
            async_bind_undo_when_observed(
                hass, undo, executed, "execution-1", timeout=0.2
            )
        )
        await asyncio.sleep(0)
        final = State(entity_id, "open", {"current_position": 40})
        final.context = SimpleNamespace(
            id="final",
            parent_id="external" if foreign else "execution-1",
        )
        hass.states._states[entity_id] = final
        hass.bus.fire(
            "state_changed",
            {"entity_id": entity_id, "old_state": moving, "new_state": final},
        )
        return await task

    observed = asyncio.run(run())
    assert (observed is None) is foreign
    if observed is not None:
        assert observed.observed_states[0][0] == entity_id


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("concise", "Lichter ausgeschaltet."),
        ("detailed", "3 Lichter ausgeschaltet."),
    ],
)
def test_response_detail_changes_only_success_rendering(monkeypatch, detail, expected):
    h = house(monkeypatch)
    h.entity.entry.options["response_detail_level"] = detail

    turn = h.say("Mach das Licht im Büro aus.")

    assert turn.speech == expected
    assert turn.targets == {
        "light.buerolicht",
        "light.deckenfluter_buero",
        "light.schreibtischlampe",
    }


@pytest.mark.parametrize("detail", ["concise", "detailed"])
def test_single_device_keeps_its_name_in_both_response_modes(monkeypatch, detail):
    h = house(monkeypatch)
    h.entity.entry.options["response_detail_level"] = detail

    turn = h.say("Schalte das Bürolicht ein.")

    assert turn.speech == "Bürolicht eingeschaltet."
    assert turn.targets == {"light.buerolicht"}


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("concise", "Lichter ausgeschaltet."),
        ("detailed", "11 Lichter ausgeschaltet."),
    ],
)
def test_floor_response_count_obeys_detail_mode(monkeypatch, detail, expected):
    h = house(monkeypatch)
    h.entity.entry.options["response_detail_level"] = detail

    turn = h.say("Mach alle Lichter im Erdgeschoss aus.")

    assert turn.speech == expected
    assert len(turn.targets) == 11


def test_named_group_effect_count_is_optional_without_losing_its_name(monkeypatch):
    from homeintent.controllers import replies

    monkeypatch.setattr(replies, "summarize_effects", lambda _effects: "3 Lichter")
    execution = SimpleNamespace(
        executed=True,
        failed=False,
        batch_result=None,
        decision=SimpleNamespace(effects=object()),
    )

    assert replies.with_effect_summary(
        "Ambiente eingeschaltet.", execution, {"response_detail_level": "concise"}
    ) == "Ambiente eingeschaltet."
    assert replies.with_effect_summary(
        "Ambiente eingeschaltet.", execution, {"response_detail_level": "detailed"}
    ) == "Ambiente eingeschaltet: 3 Lichter."
    assert replies.with_response_detail(
        "1 Licht eingeschaltet.", execution, {"response_detail_level": "concise"}
    ) == "Licht eingeschaltet."
    assert replies.with_response_detail(
        "5 Rollläden geschlossen.", execution, {"response_detail_level": "concise"}
    ) == "Rollläden geschlossen."


def test_existing_installations_keep_detailed_response_default():
    from homeintent.const import (
        DEFAULT_RESPONSE_DETAIL_LEVEL,
        NEW_INSTALL_RESPONSE_DETAIL_LEVEL,
    )
    from homeintent.controllers.replies import concise_responses

    assert DEFAULT_RESPONSE_DETAIL_LEVEL == "detailed"
    assert NEW_INSTALL_RESPONSE_DETAIL_LEVEL == "concise"
    assert not concise_responses({})


def test_new_installation_config_flow_stores_concise_default():
    import ast
    from pathlib import Path

    source = (
        Path(__file__).parent.parent
        / "custom_components"
        / "homeintent"
        / "config_flow.py"
    ).read_text(encoding="utf-8")
    step = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_step_user"
    )
    options = next(
        keyword.value
        for node in ast.walk(step)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "options"
    )
    pairs = {
        ast.unparse(key): ast.unparse(value)
        for key, value in zip(options.keys, options.values)
    }
    assert (
        pairs["CONF_RESPONSE_DETAIL_LEVEL"]
        == "NEW_INSTALL_RESPONSE_DETAIL_LEVEL"
    )


def test_concise_keeps_partial_failure_details(monkeypatch):
    h = house(monkeypatch)
    h.entity.entry.options["response_detail_level"] = "concise"
    inject_one_lamp_failure(h)

    turn = h.say("Mach das Licht im Büro aus.")

    assert "Deckenfluter" in turn.speech
    assert "fehlgeschlagen" in turn.speech
    assert turn.speech != "Lichter ausgeschaltet."


def test_concise_never_turns_a_complete_failure_into_success(monkeypatch):
    h = house(monkeypatch)
    h.entity.entry.options["response_detail_level"] = "concise"
    h.entity.hass.services.async_call.side_effect = HomeAssistantError(
        "Injected complete failure"
    )

    turn = h.say("Schalte das Bürolicht ein.")

    assert turn.speech != "Bürolicht eingeschaltet."
    assert "Fehler" in turn.speech or "nicht" in turn.speech


def test_concise_keeps_unconfirmed_effect_honest():
    from homeintent.controllers.replies import with_response_detail

    execution = SimpleNamespace(
        executed=True,
        failed=True,
        batch_result=None,
    )
    text = "Aufruf für Bürolicht angenommen; Wirkung noch nicht bestätigt."

    assert with_response_detail(
        text, execution, {"response_detail_level": "concise"}
    ) == text


def test_concise_does_not_remove_confirmation_target(monkeypatch):
    h = house(monkeypatch)
    h.entity.entry.options["response_detail_level"] = "concise"
    _force_match_confirmation(monkeypatch, h)

    preview = h.say("Schalte das Bürolicht ein.")

    assert preview.calls == []
    assert "Bürolicht" in preview.speech


def test_concise_does_not_change_status_query(monkeypatch):
    concise = house(monkeypatch)
    concise.entity.entry.options["response_detail_level"] = "concise"
    concise_answer = concise.say("Wie viele Lichter sind im Büro an?").speech

    detailed = house(monkeypatch)
    detailed.entity.entry.options["response_detail_level"] = "detailed"
    detailed_answer = detailed.say("Wie viele Lichter sind im Büro an?").speech

    assert concise_answer == detailed_answer
    assert "Licht" in concise_answer
