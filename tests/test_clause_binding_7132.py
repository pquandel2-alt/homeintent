"""7.13.2 regression and metamorphic tests for coordinated media clauses."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation, house_entities, with_states  # noqa: E402
from homeintent.engine import NluEngine  # noqa: E402
from homeintent.nlu.clause_binding import (  # noqa: E402
    ClauseBindingAnalysis,
    ClauseCoverageStatus,
    SourceResolution,
    analyse_clause_bindings,
)
from homeintent.nlu.language_frontend import analyse_language  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import (  # noqa: E402
    CHANGED_SINCE_CONFIRMATION,
    async_execute_service_plan,
    confirmed_scope,
)
from test_batch_settle_7131 import _hass  # noqa: E402

RADIO = "media_player.kuechenradio"
SPEAKER = "media_player.lautsprecher_schlafzimmer"
TV = "media_player.wohnzimmer_tv"


def source_entities(*, ambiguous: bool = False):
    base = with_states(
        house_entities(),
        media_player__kuechenradio="playing",
        media_player__lautsprecher_schlafzimmer="playing",
        media_player__wohnzimmer_tv="playing",
    )
    result = []
    for entity in base:
        attrs = dict(entity.attributes)
        if entity.entity_id == RADIO:
            attrs["source_list"] = ["WDR 2", "Bayern 3", "One"]
            entity = replace(entity, attributes=attrs)
        elif ambiguous and entity.entity_id == TV:
            attrs["source_list"] = ["WDR 2"]
            entity = replace(
                entity,
                area_id="kuche",
                area_name="Küche",
                floor_id="erdgeschoss",
                floor_name="Erdgeschoss",
                attributes=attrs,
            )
        result.append(entity)
    return result


def run(monkeypatch, text: str, *, entities=None, area=None):
    house = HouseConversation(
        monkeypatch,
        entities=entities or source_entities(),
        area=area,
        options={"implicit_action_level": "low_risk_auto"},
    )
    return house.say(text)


def assert_full_refusal(turn):
    assert turn.calls == []
    assert turn.targets == set()
    assert "Planvorschau" not in turn.speech
    assert "Soll ich" not in turn.speech
    assert "nichts ausgeführt" in turn.speech


def media_calls(turn):
    return [
        (service, data.get("entity_id"), data.get("source"))
        for domain, service, data in turn.calls
        if domain in {"media_player", "music_assistant"}
    ]


@pytest.mark.parametrize("text", [
    "Spiel Quirlomat in der Küche und pausiere die Musik im Schlafzimmer.",
    "Pausiere die Musik im Schlafzimmer und spiel Quirlomat in der Küche.",
    "Spiel meinen Music Assistant Favoriten Entspannen in der Küche und pausiere die Musik im Schlafzimmer.",
])
def test_unresolved_source_clause_compounds_are_rejected_as_one_unit(monkeypatch, text):
    assert_full_refusal(run(monkeypatch, text))


@pytest.mark.parametrize(("text", "expected"), [
    (
        "Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.",
        [("select_source", RADIO), ("media_pause", SPEAKER)],
    ),
    (
        "Spiel Bayern 3 in der Küche und stoppe die Musik im Schlafzimmer.",
        [("select_source", RADIO), ("media_stop", SPEAKER)],
    ),
    (
        "Spiel Bayern 3 in der Küche und dann pausiere die Musik im Schlafzimmer.",
        [("select_source", RADIO), ("media_pause", SPEAKER)],
    ),
    (
        "Spiel One in der Küche und mach die Musik im Schlafzimmer aus.",
        [("select_source", RADIO), ("media_pause", SPEAKER)],
    ),
    (
        "Pausiere die Musik im Schlafzimmer und spiel WDR 2 in der Küche.",
        [("media_pause", SPEAKER), ("select_source", RADIO)],
    ),
])
def test_exact_source_compounds_execute_only_the_bound_operations(
    monkeypatch, text, expected
):
    turn = run(monkeypatch, text)
    assert [(service, data["entity_id"]) for _domain, service, data in turn.calls] == expected
    assert turn.targets == {RADIO, SPEAKER}
    assert TV not in turn.targets


@pytest.mark.parametrize("joiner", [",", ";", ".", "sowie", "danach", "anschließend", "außerdem"])
def test_equivalent_clause_boundaries_keep_source_operation_and_order(
    monkeypatch, joiner
):
    turn = run(
        monkeypatch,
        f"Spiel bitte WDR 2 in der Küche {joiner} pausiere die Musik im Schlafzimmer.",
    )
    assert media_calls(turn) == [
        ("select_source", RADIO, "WDR 2"),
        ("media_pause", SPEAKER, None),
    ]


def test_three_media_clauses_are_preflighted_and_executed_in_spoken_order(monkeypatch):
    turn = run(
        monkeypatch,
        "Spiel WDR 2 in der Küche; pausiere die Musik im Schlafzimmer; "
        "stoppe die Musik im Wohnzimmer.",
    )
    assert media_calls(turn) == [
        ("select_source", RADIO, "WDR 2"),
        ("media_pause", SPEAKER, None),
        ("media_stop", TV, None),
    ]


@pytest.mark.parametrize(("text", "expected"), [
    (
        "Schalte das Küchenlicht ein, mach die Stehlampe an und fahre den "
        "Esszimmer Rollladen runter.",
        [
            ("homeassistant", "turn_on", "light.kuechenlicht"),
            ("homeassistant", "turn_on", "light.stehlampe"),
            ("cover", "close_cover", "cover.esszimmer_rollladen"),
        ],
    ),
    (
        "Mach die Kaffeemaschine an, das Küchenlicht aus und fahr den "
        "Küchenrollladen runter.",
        [
            ("homeassistant", "turn_on", "switch.kaffeemaschine"),
            ("homeassistant", "turn_off", "light.kuechenlicht"),
            ("cover", "close_cover", "cover.kuechenrollladen"),
        ],
    ),
])
def test_comma_and_conjunction_keep_mixed_commands_in_source_order(
    monkeypatch, text, expected
):
    turn = run(monkeypatch, text)
    assert [
        (domain, service, data["entity_id"])
        for domain, service, data in turn.calls
    ] == expected


def test_unknown_source_discards_every_clause_and_yes_has_no_remainder(monkeypatch):
    house = HouseConversation(
        monkeypatch,
        entities=source_entities(),
        options={"implicit_action_level": "low_risk_auto"},
    )
    rejected = house.say(
        "Spiel Quirlomat in der Küche, pausiere die Musik im Schlafzimmer."
    )
    confirmed = house.say("Ja")
    assert_full_refusal(rejected)
    assert confirmed.calls == [] and confirmed.targets == set()
    assert "keine Frage offen" in confirmed.speech


def test_domain_handoff_is_explicit_and_never_covers_an_unknown_media_source():
    entities = source_entities()
    automation = analyse_language(
        "Bei Sonnenuntergang schalte das Wohnzimmerlicht ein und dann das Flurlicht.",
        entities,
    )
    delegated = analyse_clause_bindings(automation, entities, None)
    assert delegated.coordinated
    assert delegated.status is ClauseCoverageStatus.DELEGATED

    unknown = analyse_language(
        "Spiel Quirlomat in der Küche und pausiere die Musik im Schlafzimmer.",
        entities,
    )
    rejected = analyse_clause_bindings(unknown, entities, None)
    assert rejected.coordinated
    assert rejected.status is ClauseCoverageStatus.REJECTED


def test_unrelated_player_never_changes_bound_targets(monkeypatch):
    reference = run(
        monkeypatch,
        "Spiel WDR 2 in der Küche, pausiere die Musik im Schlafzimmer.",
    )
    extra = replace(
        next(entity for entity in source_entities() if entity.entity_id == TV),
        entity_id="media_player.bad_tv",
        friendly_name="Bad TV",
        area_id="bad",
        area_name="Bad",
    )
    expanded = run(
        monkeypatch,
        "Spiel WDR 2 in der Küche, pausiere die Musik im Schlafzimmer.",
        entities=[*source_entities(), extra],
    )
    assert media_calls(expanded) == media_calls(reference)
    assert expanded.targets == {RADIO, SPEAKER}


def test_media_source_dialog_keeps_slots_cancels_and_accepts_fresh_command(monkeypatch):
    house = HouseConversation(monkeypatch, entities=source_entities())
    first = house.say("Spiel WDR 2.")
    completed = house.say("In der Küche.")
    assert first.calls == [] and first.speech.startswith("Welches Gerät meinst du:")
    assert media_calls(completed) == [("select_source", RADIO, "WDR 2")]

    house = HouseConversation(monkeypatch, entities=source_entities())
    house.say("Spiel WDR 2.")
    cancelled = house.say("Nein, abbrechen.")
    assert cancelled.calls == [] and "verworfen" in cancelled.speech

    house = HouseConversation(monkeypatch, entities=source_entities())
    house.say("Spiel WDR 2.")
    fresh = house.say("Pausiere die Musik im Schlafzimmer.")
    assert media_calls(fresh) == [("media_pause", SPEAKER, None)]


def test_media_source_dialog_cannot_be_taken_over_by_another_user(monkeypatch):
    house = HouseConversation(monkeypatch, entities=source_entities(), user="admin")
    conversation_id = "shared-media-dialog"
    opened = house.say_input(house.conversation_input(
        "Spiel WDR 2.", user="admin", conversation_id=conversation_id
    ))
    foreign = house.say_input(house.conversation_input(
        "In der Küche.", user="anna", conversation_id=conversation_id
    ))
    completed = house.say_input(house.conversation_input(
        "In der Küche.", user="admin", conversation_id=conversation_id
    ))

    assert opened.calls == [] and opened.speech.startswith("Welches Gerät meinst du:")
    assert foreign.calls == [] and foreign.targets == set()
    assert "nicht verstanden" in foreign.speech
    assert media_calls(completed) == [("select_source", RADIO, "WDR 2")]


def test_completed_media_location_correction_never_builds_a_group(monkeypatch):
    entities = []
    for entity in source_entities():
        if entity.entity_id == SPEAKER:
            entity = replace(
                entity,
                attributes={**entity.attributes, "source_list": ["WDR 2"]},
            )
        entities.append(entity)
    house = HouseConversation(monkeypatch, entities=entities)
    first = house.say("Spiel WDR 2 in der Küche.")
    corrected = house.say("Doch im Schlafzimmer.")
    assert media_calls(first) == [("select_source", RADIO, "WDR 2")]
    assert media_calls(corrected) == [("select_source", SPEAKER, "WDR 2")]
    assert "bereits gestartet" in corrected.speech


@pytest.mark.parametrize("tail", [
    "pausiere die Musik im Schlafzimmer",
    "stoppe die Musik im Schlafzimmer",
    "mach die Musik im Schlafzimmer aus",
    "mach die Musik im Schlafzimmer lauter",
    "mach die Musik im Schlafzimmer leiser",
    "schalte die Musik im Schlafzimmer stumm",
    "spiele den nächsten Titel im Schlafzimmer",
    "mach das Licht im Büro aus",
    "öffne die Rollläden im Wohnzimmer",
])
def test_unknown_source_never_leaves_any_following_write(monkeypatch, tail):
    assert_full_refusal(run(monkeypatch, f"Spiel Quirlomat in der Küche und {tail}."))


@pytest.mark.parametrize(("tail", "service"), [
    ("pausiere die Musik im Schlafzimmer", "media_pause"),
    ("stoppe die Musik im Schlafzimmer", "media_stop"),
    ("mach die Musik im Schlafzimmer aus", "media_pause"),
    ("mach die Musik im Schlafzimmer lauter", "volume_up"),
    ("mach die Musik im Schlafzimmer leiser", "volume_down"),
    ("schalte die Musik im Schlafzimmer stumm", "volume_mute"),
    ("spiele den nächsten Titel im Schlafzimmer", "media_next_track"),
])
def test_known_source_keeps_the_exact_following_operation(
    monkeypatch, tail, service
):
    turn = run(monkeypatch, f"Spiel WDR 2 in der Küche und {tail}.")
    assert [(item[1], item[2]["entity_id"]) for item in turn.calls] == [
        ("select_source", RADIO),
        (service, SPEAKER),
    ]


def test_ambiguous_exact_source_is_rejected_without_question_or_write(monkeypatch):
    turn = run(
        monkeypatch,
        "Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.",
        entities=source_entities(ambiguous=True),
    )
    assert_full_refusal(turn)


def test_known_source_with_unknown_second_clause_is_rejected(monkeypatch):
    assert_full_refusal(run(monkeypatch, "Spiel WDR 2 in der Küche und quirlomate das Licht."))


def test_second_place_without_its_own_target_never_widens_source_clause(monkeypatch):
    turn = run(monkeypatch, "Spiel WDR 2 in der Küche und im Schlafzimmer.")
    assert turn.calls == [] and turn.targets == set()
    assert "Planvorschau" not in turn.speech and "Soll ich" not in turn.speech


def test_clause_bindings_keep_source_operation_target_and_place_separate():
    entities = source_entities()
    text = "Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer."
    document = analyse_language(text, entities)
    outcome = NluEngine().understand(text, entities, document=document)
    analysis = outcome.clause_bindings
    assert isinstance(analysis, ClauseBindingAnalysis)
    assert analysis.coordinated and not analysis.complete
    assert len(analysis.bindings) == 2
    source, pause = analysis.bindings
    assert source.operation == "play" and source.source == "WDR 2"
    assert source.source_resolution is SourceResolution.EXACT
    assert source.resolved_target_ids == (RADIO,)
    assert source.resolved_place_ids == ("area:kuche",)
    assert pause.operation == "pause"
    assert pause.resolved_target_ids == (SPEAKER,)
    assert pause.resolved_place_ids == ("area:schlafzimmer",)
    assert TV not in source.resolved_target_ids + pause.resolved_target_ids


def test_every_executed_action_has_one_complete_binding_in_its_own_place(monkeypatch):
    text = "Mach das Bürolicht im Büro aus und pausiere das Radio in der Küche."
    entities = source_entities()
    document = analyse_language(text, entities)
    outcome = NluEngine().understand(text, entities, document=document)
    analysis = outcome.clause_bindings
    assert isinstance(analysis, ClauseBindingAnalysis) and analysis.complete
    assert len(analysis.bindings) == 2

    office, kitchen = analysis.bindings
    assert office.resolved_target_ids == ("light.buerolicht",)
    assert office.resolved_place_ids == ("area:buro",)
    assert kitchen.resolved_target_ids == (RADIO,)
    assert kitchen.resolved_place_ids == ("area:kuche",)
    bound_plans = [plan for binding in analysis.bindings for plan in binding.plans]
    payload_plans = [
        item.plan
        for item in outcome.payload.commands
        if item.plan is not None
    ]
    assert bound_plans == payload_plans
    assert all(
        {
            entity_id
            for plan in binding.plans
            for entity_id in (
                (plan.entity_id,)
                if isinstance(plan.entity_id, str)
                else tuple(plan.entity_id)
            )
        }
        == set(binding.resolved_target_ids)
        for binding in analysis.bindings
    )

    turn = run(monkeypatch, text, entities=entities)
    assert turn.targets == {"light.buerolicht", RADIO}
    assert {call[:2] for call in turn.calls} == {
        ("homeassistant", "turn_off"),
        ("media_player", "media_pause"),
    }


def test_explicit_place_wins_over_satellite_area(monkeypatch):
    turn = run(
        monkeypatch,
        "Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.",
        area="wohnzimmer",
    )
    assert [(service, data["entity_id"]) for _domain, service, data in turn.calls] == [
        ("select_source", RADIO),
        ("media_pause", SPEAKER),
    ]


@settings(
    max_examples=300 if os.environ.get("HOMEINTENT_HYPOTHESIS_PROFILE") == "nightly" else 40,
    deadline=None,
    derandomize=os.environ.get("HOMEINTENT_HYPOTHESIS_PROFILE") != "nightly",
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    source=st.sampled_from(["Quirlomat", "Zappelding", "Blubberfix"]),
    polite=st.sampled_from(["", "bitte ", "doch mal "]),
    joiner=st.sampled_from([
        "und", "und dann", "danach", "anschließend", "sowie", "außerdem", ",", ";", "."
    ]),
)
def test_property_unknown_source_and_politeness_never_leave_a_partial_write(
    monkeypatch, source, polite, joiner
):
    text = f"Spiel {polite}{source} in der Küche {joiner} pausiere die Musik im Schlafzimmer."
    assert_full_refusal(run(monkeypatch, text))


def test_confirmation_scope_rejects_changed_media_source_or_operation():
    entities = source_entities()
    shown = ServiceCallPlan(
        "media_player", "select_source", RADIO, {"source": "WDR 2"}
    )
    scope_hass, _devices = _hass(entities, {})
    scope = confirmed_scope(scope_hass, (shown,), entities, {})
    for changed in (
        ServiceCallPlan("media_player", "select_source", RADIO, {"source": "Bayern 3"}),
        ServiceCallPlan("media_player", "media_pause", RADIO),
        ServiceCallPlan("media_player", "select_source", SPEAKER, {"source": "WDR 2"}),
    ):
        hass, _devices = _hass(entities, {})
        result = asyncio.run(async_execute_service_plan(
            hass, changed, entities, {}, is_admin=True, user_id="admin",
            confirmed=True, scope=scope,
        ))
        assert not result.executed and result.error == CHANGED_SINCE_CONFIRMATION
        hass.services.async_call.assert_not_awaited()


def test_operation_binding_rejects_a_plan_with_the_right_targets_but_wrong_service():
    entities = source_entities()
    text = "Mach das Licht im Büro aus und pausiere das Radio in der Küche."
    document = analyse_language(text, entities)
    valid = NluEngine().understand(text, entities, document=document).clause_bindings
    assert isinstance(valid, ClauseBindingAnalysis) and valid.complete
    first, second = valid.bindings
    wrong_payload = SimpleNamespace(commands=(
        SimpleNamespace(plan=ServiceCallPlan(
            "homeassistant", "turn_on", list(first.resolved_target_ids)
        )),
        SimpleNamespace(plan=ServiceCallPlan(
            "media_player", "media_pause", list(second.resolved_target_ids)
        )),
    ))
    rejected = analyse_clause_bindings(document, entities, wrong_payload)
    assert not rejected.complete
    assert rejected.bindings[0].rejection_reason == "plan_not_bound"
