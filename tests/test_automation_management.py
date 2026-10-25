"""Automation management vocabulary and deterministic selection."""

from __future__ import annotations

from datetime import datetime, timezone

from homeintent.automation_management import (
    AutomationManagementKind,
    parse_automation_management,
    select_automation_management,
)
from homeintent.automation_summary import AutomationSummary
from homeintent.entities import EntitySnapshot

ROLLLADE = EntitySnapshot(
    "cover.buero_rollladen", "Büro Rollladen", "cover", "closed",
    area_id="buero", area_name="Büro", floor_id="eg", floor_name="Erdgeschoss",
)
SCHEDULED = AutomationSummary(
    "scheduled",
    "Rolllade später",
    created_by="homeintent",
    referenced_entity_ids=frozenset({ROLLLADE.entity_id}),
    scheduled_for="2026-08-22T08:00:00+00:00",
    once=True,
)
MANUAL = AutomationSummary("manual", "Von Hand")
PERSISTENT = AutomationSummary(
    "persistent",
    "Küche dauerhaft",
    created_by="homeintent",
    referenced_entity_ids=frozenset({ROLLLADE.entity_id}),
    action_entity_ids=frozenset({ROLLLADE.entity_id}),
)
TRIGGERED = AutomationSummary(
    "triggered",
    "Bei Rollladenstatus Licht schalten",
    created_by="homeintent",
    referenced_entity_ids=frozenset({ROLLLADE.entity_id}),
    trigger_entity_ids=frozenset({ROLLLADE.entity_id}),
)


def test_management_phrases_are_classified():
    assert parse_automation_management(
        "Zeige nur HomeIntent-Automationen"
    ).kind is AutomationManagementKind.LIST_HOMEINTENT
    assert parse_automation_management(
        "Welche einmaligen Aufträge sind noch geplant?"
    ).kind is AutomationManagementKind.LIST_SCHEDULED
    assert parse_automation_management(
        "Wann wird Büro Rollladen gefahren?"
    ).kind is AutomationManagementKind.WHEN
    move = parse_automation_management(
        "Verschiebe den Auftrag für Büro Rollladen auf 20:15 Uhr"
    )
    assert (move.kind, move.hour, move.minute) == (
        AutomationManagementKind.RESCHEDULE,
        20,
        15,
    )
    assert parse_automation_management(
        "Lösche alle abgelaufenen HomeIntent-Automationen"
    ).kind is AutomationManagementKind.CLEAN_EXPIRED
    repeat = parse_automation_management("Wiederhole die Automation nur dreimal")
    assert (repeat.kind, repeat.max_runs) == (
        AutomationManagementKind.SET_MAX_RUNS,
        3,
    )
    assert parse_automation_management(
        "Wie viele HomeIntent-Automationen sind aktiv?"
    ).kind is AutomationManagementKind.COUNT_ACTIVE
    assert parse_automation_management(
        "Welche Automation steuert Büro Rollladen?"
    ).kind is AutomationManagementKind.CONTROLS_ENTITY
    assert parse_automation_management(
        "Was passiert, wenn Büro Rollladen geöffnet wird?"
    ).kind is AutomationManagementKind.EXPLAIN_TRIGGER


def test_repeat_management_uses_persistent_homeintent_automations_only():
    request = parse_automation_management(
        "Wiederhole die Automation für Büro Rollladen nur 4 mal"
    )
    selection = select_automation_management(
        request,
        [ROLLLADE],
        (SCHEDULED, PERSISTENT, MANUAL),
    )
    assert selection.automations == (PERSISTENT,)


def test_homeintent_and_scheduled_filters_never_include_manual_automations():
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    home_request = parse_automation_management("Zeige nur HomeIntent-Automationen")
    scheduled_request = parse_automation_management(
        "Welche einmaligen Aufträge sind noch geplant?"
    )
    assert select_automation_management(
        home_request, [ROLLLADE], (SCHEDULED, MANUAL), now
    ).automations == (SCHEDULED,)
    assert select_automation_management(
        scheduled_request, [ROLLLADE], (SCHEDULED, MANUAL), now
    ).automations == (SCHEDULED,)


def test_when_query_resolves_the_entity_before_filtering():
    request = parse_automation_management("Wann wird Büro Rollladen gefahren?")
    selection = select_automation_management(
        request,
        [ROLLLADE],
        (SCHEDULED, MANUAL),
        datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert selection.entity == ROLLLADE
    assert selection.automations == (SCHEDULED,)


def test_trigger_and_action_queries_do_not_mix_reference_roles():
    controls = select_automation_management(
        parse_automation_management("Welche Automation steuert Büro Rollladen?"),
        [ROLLLADE],
        (PERSISTENT, TRIGGERED),
    )
    reacts = select_automation_management(
        parse_automation_management("Was passiert, wenn Büro Rollladen geöffnet wird?"),
        [ROLLLADE],
        (PERSISTENT, TRIGGERED),
    )
    assert controls.automations == (PERSISTENT,)
    assert reacts.automations == (TRIGGERED,)


def test_homeintent_listing_can_be_filtered_by_floor_name():
    request = parse_automation_management(
        "Zeige nur HomeIntent-Automationen im Erdgeschoss"
    )
    selection = select_automation_management(
        request, [ROLLLADE], (PERSISTENT, MANUAL)
    )
    assert selection.automations == (PERSISTENT,)


def test_detail_selection_accepts_automation_alias_or_area():
    by_alias = select_automation_management(
        parse_automation_management("Zeige die Details der Automation zu Küche dauerhaft"),
        [ROLLLADE],
        (PERSISTENT, TRIGGERED),
    )
    by_area = select_automation_management(
        parse_automation_management("Zeige die Details der Automation zu Büro"),
        [ROLLLADE],
        (PERSISTENT, TRIGGERED),
    )
    assert by_alias.automations == (PERSISTENT,)
    assert by_area.automations == (PERSISTENT, TRIGGERED)


def test_rollback_phrase_is_classified():
    request = parse_automation_management(
        "Mache die letzte HomeIntent-Automationsänderung rückgängig"
    )
    assert request is not None and request.kind is AutomationManagementKind.ROLLBACK


def test_explicit_discard_of_unsupported_history_head_is_classified():
    request = parse_automation_management(
        "Verwirf den letzten nicht rückgängig machbaren Verlaufseintrag."
    )

    assert request is not None
    assert request.kind is AutomationManagementKind.DISCARD_UNDO_ENTRY


def test_duplicate_and_durable_pause_are_classified():
    duplicate = parse_automation_management(
        "Dupliziere die Automation für Büro Rollladen"
    )
    pause = parse_automation_management(
        "Pausiere die Automation für Büro Rollladen bis morgen 20:15 Uhr"
    )
    assert duplicate.kind is AutomationManagementKind.DUPLICATE
    assert duplicate.entity_name == "Büro Rollladen"
    assert pause.kind is AutomationManagementKind.PAUSE_UNTIL
    assert (pause.hour, pause.minute, pause.day_offset) == (20, 15, 1)


def test_failed_execution_diagnostic_is_classified_without_claiming_a_cause():
    request = parse_automation_management(
        "Warum wurde die Automation für Büro Rollladen nicht ausgelöst?"
    )
    assert request.kind is AutomationManagementKind.DIAGNOSE
    assert request.entity_name == "Büro Rollladen"
