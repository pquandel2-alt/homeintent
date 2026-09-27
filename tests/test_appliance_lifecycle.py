from datetime import datetime, timezone

from homeintent.appliance_lifecycle import (
    ApplianceQuestion,
    match_appliance_lifecycle_query,
)
from homeintent.entities import EntitySnapshot


NOW = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)


def _entity(suffix: str, state: str, *, device_class=None, unit=None):
    return EntitySnapshot(
        f"sensor.waschmaschine_{suffix}",
        f"Waschmaschine {suffix.replace('_', ' ').title()}",
        "sensor",
        state,
        device_class=device_class,
        unit=unit,
    )


def test_reports_observed_appliance_status_without_action():
    answer = match_appliance_lifecycle_query(
        "Läuft die Waschmaschine noch?", [_entity("Status", "running")], NOW
    )

    assert answer is not None
    assert answer.question is ApplianceQuestion.STATUS
    # F16: the raw HA state is spoken in German.
    assert answer.text == "Waschmaschine meldet den Status „läuft“."
    assert answer.evidence[0].observed


def test_reports_progress_and_remaining_duration():
    progress = match_appliance_lifecycle_query(
        "Wie weit ist die Waschmaschine?",
        [_entity("Fortschritt", "62", unit="%")],
        NOW,
    )
    remaining = match_appliance_lifecycle_query(
        "Wie lange läuft die Waschmaschine noch?",
        [_entity("Restzeit", "4500", device_class="duration", unit="s")],
        NOW,
    )

    assert progress is not None and "62 Prozent Fortschritt" in progress.text
    assert remaining is not None and "1 Stunde und 15 Minuten" in remaining.text


def test_does_not_infer_running_state_from_power():
    answer = match_appliance_lifecycle_query(
        "Läuft die Waschmaschine?", [_entity("Leistung", "850", unit="W")], NOW
    )

    assert answer is not None
    assert "keine eindeutige, direkt beobachtete Information" in answer.text
    assert answer.evidence == ()


def test_similar_appliances_are_never_first_matched():
    answer = match_appliance_lifecycle_query(
        "Läuft die Waschmaschine?",
        [
            EntitySnapshot("sensor.a", "Waschmaschine Keller Status", "sensor", "running"),
            EntitySnapshot("sensor.b", "Waschmaschine Bad Status", "sensor", "idle"),
        ],
        NOW,
    )

    assert answer is not None and answer.ambiguous
    assert answer.text == "Welche Maschine meinst du? Ich habe nichts ausgeführt."
