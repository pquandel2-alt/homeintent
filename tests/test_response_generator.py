"""HomeIntent v4.2.1 plan, Phase 6 (Section 12): nlu/response_generator.py's
ResponseGenerator - QueryResult -> German text. Hass-free (mirrors
tests/test_query_executor.py's style).

WorldModelQuery wave: ResponseGenerator is now the live response source for
StateQueryParser's intents (engine.py::_build_match_result calls respond()
whenever frame.parameters["query_result"] is set) - the wording here is no
longer required to mirror service_call.py's old lambdas, it is the primary
source.
"""

from __future__ import annotations

from homeintent.areas import AreaSnapshot
from homeintent.automation_summary import AutomationSummary
from homeintent.devices import DeviceSnapshot
from homeintent.entities import EntitySnapshot
from homeintent.nlu.query_command import (
    QueryCommand,
    QueryFilter,
    QueryResult,
    QueryResultStatus,
    QueryScope,
    QueryTarget,
    QueryTargetKind,
)
from homeintent.nlu.response_generator import ResponseGenerator
from homeintent.response_planner import QueryAnswerKind
from homeintent.nlu.semantic_state import SemanticState

respond = ResponseGenerator().respond


def test_generator_builds_a_structured_plan_before_realization():
    command = _list_command(QueryScope.LIST)
    entity = _window("binary_sensor.a", "Küchenfenster", "on")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(entity,),
        command=command,
    )

    plan = ResponseGenerator().plan(result)

    assert plan.result is None
    assert plan.query is not None
    assert plan.query.kind is QueryAnswerKind.FILTERED_LIST
    assert plan.query.names == ("Küchenfenster",)
    assert plan.query.state == "geöffnet"


def _window(entity_id: str, name: str, state: str) -> EntitySnapshot:
    return EntitySnapshot(entity_id, name, "binary_sensor", state, device_class="window")


def _list_command(scope: QueryScope, device_class: str = "window") -> QueryCommand:
    return QueryCommand(
        intent="HassStateQuery",
        scope=scope,
        target=QueryTarget(domain="binary_sensor", device_class=device_class),
        filter=QueryFilter(state=SemanticState.OPEN),
    )


# --- TARGET_NOT_FOUND / AMBIGUOUS (no equivalent in today's pipeline) ----


def test_target_not_found_gets_its_own_text():
    command = _list_command(QueryScope.LIST)
    result = QueryResult(status=QueryResultStatus.TARGET_NOT_FOUND, command=command)
    assert respond(result) == "Ich konnte keine passenden Fenster finden."


def test_ambiguous_gets_its_own_text():
    command = _list_command(QueryScope.LIST)
    a = _window("binary_sensor.a", "A", "on")
    b = _window("binary_sensor.b", "B", "off")
    result = QueryResult(status=QueryResultStatus.AMBIGUOUS, entities=(a, b), command=command)
    assert respond(result) == "Ich habe mehrere passende Fenster gefunden."


# --- LIST -----------------------------------------------------------------


def test_list_empty_is_a_normal_sentence_not_an_error():
    command = _list_command(QueryScope.LIST)
    result = QueryResult(status=QueryResultStatus.EMPTY, entities=(), command=command)
    assert respond(result) == "Es sind keine Fenster geöffnet."


def test_list_single_match_names_the_entity():
    command = _list_command(QueryScope.LIST)
    entity = _window("binary_sensor.a", "Küchenfenster", "on")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(entity,), command=command)
    assert respond(result) == "Küchenfenster ist geöffnet."


def test_list_multiple_matches_lists_names():
    command = _list_command(QueryScope.LIST)
    a = _window("binary_sensor.a", "Küchenfenster", "on")
    b = _window("binary_sensor.b", "Badfenster", "on")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(a, b), command=command)
    assert respond(result) == "Küchenfenster und Badfenster sind geöffnet."


def test_long_list_speaks_a_bounded_grounded_prefix_and_remaining_count():
    command = _list_command(QueryScope.LIST)
    entities = tuple(
        _window(f"binary_sensor.{index}", name, "on")
        for index, name in enumerate(
            ("Büro rechts", "Esszimmer", "Schlafzimmer", "Küche", "Garage")
        )
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=entities,
        command=command,
    )

    assert respond(result) == (
        "Geöffnet sind Büro rechts, Esszimmer und Schlafzimmer. "
        "Weitere 2 findest du in Home Assistant."
    )


def test_complete_candidate_set_is_aggregated_without_listing_names():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light"),
        filter=QueryFilter(state=SemanticState.ON),
    )
    lights = tuple(
        EntitySnapshot(f"light.{index}", name, "light", "on")
        for index, name in enumerate(("Küchenlicht", "Flurlicht", "Badlicht"))
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=lights,
        considered_entities=lights,
        command=command,
    )

    assert respond(result) == "Alle drei Lichter sind an."


def test_single_match_in_complete_candidate_set_is_contrasted():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light"),
        filter=QueryFilter(state=SemanticState.ON),
    )
    on = EntitySnapshot("light.bad", "Badlicht", "light", "on")
    off = (
        EntitySnapshot("light.kueche", "Küchenlicht", "light", "off"),
        EntitySnapshot("light.flur", "Flurlicht", "light", "off"),
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(on,),
        considered_entities=(on, *off),
        command=command,
    )

    assert respond(result) == "Nur Badlicht ist an."


def test_single_exception_in_complete_candidate_set_is_named():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light"),
        filter=QueryFilter(state=SemanticState.OFF),
    )
    off = (
        EntitySnapshot("light.kueche", "Küchenlicht", "light", "off"),
        EntitySnapshot("light.flur", "Flurlicht", "light", "off"),
    )
    on = EntitySnapshot("light.bad", "Badlicht", "light", "on")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=off,
        considered_entities=(*off, on),
        command=command,
    )

    assert respond(result) == "Bis auf Badlicht sind alle drei Lichter aus."


def test_long_list_is_capped_for_tts():
    command = _list_command(QueryScope.LIST)
    windows = tuple(
        _window(f"binary_sensor.{index}", f"Fenster {index}", "on")
        for index in range(5)
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=windows,
        command=command,
    )

    assert respond(result) == (
        "Geöffnet sind Fenster 0, Fenster 1 und Fenster 2. "
        "Weitere 2 findest du in Home Assistant."
    )


# --- COUNT ------------------------------------------------------------------


def test_count_single_match_answers_with_the_number():
    command = _list_command(QueryScope.COUNT)
    entity = _window("binary_sensor.a", "Küchenfenster", "on")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(entity,), command=command)
    assert respond(result) == "1 Fenster ist geöffnet."


def test_count_multiple_matches_speaks_a_count_not_names():
    command = _list_command(QueryScope.COUNT)
    a = _window("binary_sensor.a", "Küchenfenster", "on")
    b = _window("binary_sensor.b", "Badfenster", "on")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(a, b), command=command)
    assert respond(result) == "2 Fenster sind geöffnet."


# --- EXISTS -------------------------------------------------------------


def test_exists_empty_is_a_clean_no():
    command = QueryCommand(
        intent="HassExistsQuery",
        scope=QueryScope.EXISTS,
        target=QueryTarget(domain="binary_sensor", device_class="window"),
        filter=QueryFilter(state=SemanticState.OPEN),
    )
    result = QueryResult(status=QueryResultStatus.EMPTY, entities=(), command=command)
    assert respond(result) == "Nein, es gibt keine Fenster."


def test_exists_with_matches_speaks_a_count():
    command = QueryCommand(
        intent="HassExistsQuery",
        scope=QueryScope.EXISTS,
        target=QueryTarget(domain="binary_sensor", device_class="window"),
        filter=QueryFilter(state=None),
    )
    a = _window("binary_sensor.a", "Küchenfenster", "on")
    b = _window("binary_sensor.b", "Badfenster", "off")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(a, b), command=command)
    assert respond(result) == "Ja, es gibt 2 Fenster."


# --- SINGLE (HassCheckState) ----------------------------------------------


def test_single_matched_entity_currently_satisfying_filter_says_ja():
    command = QueryCommand(
        intent="HassCheckState",
        scope=QueryScope.SINGLE,
        target=QueryTarget(domain="binary_sensor", entity_id="binary_sensor.a"),
        filter=QueryFilter(state=SemanticState.OPEN),
    )
    entity = _window("binary_sensor.a", "Küchenfenster", "on")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(entity,), command=command)
    assert respond(result) == "Ja, Küchenfenster ist geöffnet."


def test_single_matched_entity_not_currently_satisfying_filter_says_nein():
    command = QueryCommand(
        intent="HassCheckState",
        scope=QueryScope.SINGLE,
        target=QueryTarget(domain="binary_sensor", entity_id="binary_sensor.a"),
        filter=QueryFilter(state=SemanticState.OPEN),
    )
    entity = _window("binary_sensor.a", "Küchenfenster", "off")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(entity,), command=command)
    assert respond(result) == "Nein, Küchenfenster ist nicht geöffnet."


# --- device_class-less domain falls back to the generic domain noun ------


def test_list_falls_back_to_domain_plural_noun_when_no_device_class():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light"),
        filter=QueryFilter(state=SemanticState.ON),
    )
    result = QueryResult(status=QueryResultStatus.EMPTY, entities=(), command=command)
    assert respond(result) == "Es sind keine Lichter eingeschaltet."


# --- DEVICE scope (HassDeviceQuery, "welche Geräte sind im Büro?") -------


_BUERO = AreaSnapshot(area_id="area.buero", name="Büro")


def _device_command() -> QueryCommand:
    return QueryCommand(
        intent="HassDeviceQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(kind=QueryTargetKind.DEVICE, area=_BUERO),
        filter=QueryFilter(),
    )


def test_device_list_empty_names_the_area():
    command = _device_command()
    result = QueryResult(status=QueryResultStatus.EMPTY, devices=(), command=command)
    assert respond(result) == "Im Büro sind keine Geräte bekannt."


def test_device_list_single_device():
    command = _device_command()
    device = DeviceSnapshot(device_id="device.a", name="Schreibtischlampe")
    result = QueryResult(status=QueryResultStatus.MATCHED, devices=(device,), command=command)
    assert respond(result) == "Schreibtischlampe ist im Büro."


def test_device_list_multiple_devices():
    command = _device_command()
    a = DeviceSnapshot(device_id="device.a", name="Schreibtischlampe")
    b = DeviceSnapshot(device_id="device.b", name="Drucker")
    result = QueryResult(status=QueryResultStatus.MATCHED, devices=(a, b), command=command)
    assert respond(result) == "Schreibtischlampe und Drucker sind im Büro."


def test_long_state_filtered_device_list_is_capped_for_tts():
    command = QueryCommand(
        intent="HassDeviceQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(kind=QueryTargetKind.DEVICE, area=_BUERO),
        filter=QueryFilter(state=SemanticState.ON),
    )
    devices = tuple(
        DeviceSnapshot(device_id=f"device.{index}", name=f"Gerät {index}")
        for index in range(5)
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        devices=devices,
        command=command,
    )

    assert respond(result) == (
        "5 Geräte sind eingeschaltet. "
        "Weitere Details sind in Home Assistant sichtbar."
    )


def test_device_list_uses_feminine_area_preposition():
    command = QueryCommand(
        intent="HassDeviceQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(
            kind=QueryTargetKind.DEVICE,
            area=AreaSnapshot(area_id="area.kueche", name="Küche"),
        ),
        filter=QueryFilter(),
    )
    device = DeviceSnapshot(device_id="device.a", name="Küchenlicht")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        devices=(device,),
        command=command,
    )

    response = respond(result)

    assert response == "Küchenlicht ist in der Küche."
    assert "im Küche" not in response


# --- stateless listing (HassStateQuery without a {state} slot) -----------


# --- AUTOMATION scope (V5.29, "Automation Query") -------------------------


KUECHE_LICHT = EntitySnapshot("light.kueche_licht", "Küchenlicht", "light", "off")

KUECHE_AUTOMATION_WITH_SOURCE = AutomationSummary(
    automation_id="a1",
    alias="homeintent_a1",
    source_text="Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.",
    referenced_entity_ids=frozenset({"light.kueche_licht"}),
)
BUERO_AUTOMATION_NO_SOURCE = AutomationSummary(
    automation_id="a2",
    alias="Von Hand erstellte Automation",
    referenced_entity_ids=frozenset({"switch.buero_steckdose"}),
)


def _automation_list_command() -> QueryCommand:
    return QueryCommand(
        intent="HassAutomationQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(kind=QueryTargetKind.AUTOMATION),
        filter=QueryFilter(),
    )


def _automation_entity_command(intent: str) -> QueryCommand:
    return QueryCommand(
        intent=intent,
        scope=QueryScope.LIST,
        target=QueryTarget(kind=QueryTargetKind.AUTOMATION, entity_id="light.kueche_licht"),
        filter=QueryFilter(),
    )


def test_automation_list_empty():
    command = _automation_list_command()
    result = QueryResult(status=QueryResultStatus.EMPTY, automations=(), command=command)
    assert respond(result) == "Es sind keine Automationen vorhanden."


def test_automation_list_prefers_source_text_over_alias():
    command = _automation_list_command()
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        automations=(KUECHE_AUTOMATION_WITH_SOURCE,),
        command=command,
    )
    assert respond(result) == (
        "Es gibt folgende Automationen: Wenn das Küchenfenster geöffnet wird, "
        "schalte das Küchenlicht ein."
    )


def test_automation_list_falls_back_to_alias_when_no_source_text():
    command = _automation_list_command()
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        automations=(BUERO_AUTOMATION_NO_SOURCE,),
        command=command,
    )
    assert respond(result) == "Es gibt folgende Automationen: Von Hand erstellte Automation."


def test_automation_list_multiple_automations_are_joined():
    command = _automation_list_command()
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        automations=(KUECHE_AUTOMATION_WITH_SOURCE, BUERO_AUTOMATION_NO_SOURCE),
        command=command,
    )
    assert respond(result) == (
        "Es gibt folgende Automationen: Wenn das Küchenfenster geöffnet wird, "
        "schalte das Küchenlicht ein und Von Hand erstellte Automation."
    )


def test_automation_by_entity_none_found_plain_query():
    command = _automation_entity_command("HassAutomationQuery")
    result = QueryResult(
        status=QueryResultStatus.EMPTY, entities=(KUECHE_LICHT,), automations=(), command=command
    )
    assert respond(result) == "Ich habe keine Automation gefunden, die Küchenlicht steuert."


def test_automation_by_entity_none_found_causal_query_is_hedged():
    command = _automation_entity_command("HassAutomationWhyQuery")
    result = QueryResult(
        status=QueryResultStatus.EMPTY, entities=(KUECHE_LICHT,), automations=(), command=command
    )
    assert respond(result) == (
        "Ich habe keine Automation gefunden, die Küchenlicht beeinflussen könnte."
    )


def test_automation_by_entity_single_match_plain_query():
    command = _automation_entity_command("HassAutomationQuery")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(KUECHE_LICHT,),
        automations=(KUECHE_AUTOMATION_WITH_SOURCE,),
        command=command,
    )
    assert respond(result) == (
        "Küchenlicht wird von folgender Automation gesteuert: Wenn das "
        "Küchenfenster geöffnet wird, schalte das Küchenlicht ein."
    )


def test_automation_by_entity_single_match_causal_query_is_hedged():
    command = _automation_entity_command("HassAutomationWhyQuery")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(KUECHE_LICHT,),
        automations=(KUECHE_AUTOMATION_WITH_SOURCE,),
        command=command,
    )
    assert respond(result) == (
        "Küchenlicht könnte durch folgende Automation beeinflusst werden: Wenn "
        "das Küchenfenster geöffnet wird, schalte das Küchenlicht ein."
    )


def test_automation_by_entity_multiple_matches_are_joined_plural():
    command = _automation_entity_command("HassAutomationQuery")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(KUECHE_LICHT,),
        automations=(KUECHE_AUTOMATION_WITH_SOURCE, BUERO_AUTOMATION_NO_SOURCE),
        command=command,
    )
    assert respond(result) == (
        "Küchenlicht wird von folgenden Automationen gesteuert: Wenn das "
        "Küchenfenster geöffnet wird, schalte das Küchenlicht ein und Von "
        "Hand erstellte Automation."
    )


def test_list_no_state_empty_without_area():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light"),
        filter=QueryFilter(state=None),
    )
    result = QueryResult(status=QueryResultStatus.EMPTY, entities=(), command=command)
    assert respond(result) == "Keine Lichter gefunden."


def test_list_no_state_single_match_with_area():
    wohnzimmer = AreaSnapshot(area_id="area.wohnzimmer", name="Wohnzimmer")
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light", area=wohnzimmer),
        filter=QueryFilter(state=None),
    )
    entity = EntitySnapshot("light.a", "Stehlampe", "light", "on")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(entity,), command=command)
    assert respond(result) == "Stehlampe ist im Wohnzimmer."


def test_list_no_state_multiple_matches_with_area():
    wohnzimmer = AreaSnapshot(area_id="area.wohnzimmer", name="Wohnzimmer")
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain="light", area=wohnzimmer),
        filter=QueryFilter(state=None),
    )
    a = EntitySnapshot("light.a", "Stehlampe", "light", "on")
    b = EntitySnapshot("light.b", "Deckenlampe", "light", "off")
    result = QueryResult(status=QueryResultStatus.MATCHED, entities=(a, b), command=command)
    assert respond(result) == "Stehlampe und Deckenlampe sind im Wohnzimmer."


def test_list_no_state_uses_feminine_area_preposition():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(
            domain="light",
            area=AreaSnapshot(area_id="area.kueche", name="Küche"),
        ),
        filter=QueryFilter(state=None),
    )
    entity = EntitySnapshot("light.a", "Küchenlicht", "light", "on")
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(entity,),
        command=command,
    )

    response = respond(result)

    assert response == "Küchenlicht ist in der Küche."
    assert "im Küche" not in response


def test_location_answer_uses_feminine_area_preposition():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LOCATIONS,
        target=QueryTarget(domain="binary_sensor", device_class="window"),
        filter=QueryFilter(state=SemanticState.OPEN),
    )
    entity = EntitySnapshot(
        "binary_sensor.a",
        "Küchenfenster",
        "binary_sensor",
        "on",
        area_id="area.kueche",
        area_name="Küche",
        device_class="window",
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=(entity,),
        command=command,
    )

    response = respond(result)

    assert response == "In der Küche sind Fenster geöffnet."
    assert "im Küche" not in response


def test_location_answer_realizes_each_area_with_its_own_article():
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LOCATIONS,
        target=QueryTarget(domain="binary_sensor", device_class="window"),
        filter=QueryFilter(state=SemanticState.OPEN),
    )
    entities = (
        EntitySnapshot(
            "binary_sensor.bad",
            "Badfenster",
            "binary_sensor",
            "on",
            area_id="area.bad",
            area_name="Bad",
            device_class="window",
        ),
        EntitySnapshot(
            "binary_sensor.kueche",
            "Küchenfenster",
            "binary_sensor",
            "on",
            area_id="area.kueche",
            area_name="Küche",
            device_class="window",
        ),
    )
    result = QueryResult(
        status=QueryResultStatus.MATCHED,
        entities=entities,
        command=command,
    )

    assert respond(result) == "Im Bad und in der Küche sind Fenster geöffnet."
