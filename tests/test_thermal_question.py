"""F24: heating-time questions get an estimate or the honest no-model answer."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from homeintent.entities import EntitySnapshot
from homeintent.prediction import PredictionResult, PredictionStatus
from homeintent.thermal_question import answer_thermal_question, is_thermal_question

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
ENTITIES = [
    EntitySnapshot(
        "climate.heizung_buero", "Heizung Büro", "climate", "heat",
        area_id="buero", area_name="Büro",
        attributes={"current_temperature": 19.4, "temperature": 21.0},
    ),
    EntitySnapshot(
        "sensor.temperatur_wohnzimmer", "Temperatur Wohnzimmer", "sensor", "20.64",
        area_id="wohnzimmer", area_name="Wohnzimmer", unit="°C", device_class="temperature",
    ),
]


class _House:
    """Duck-typed ``PredictiveHouseModel`` with one confirmed thermal model."""

    def __init__(self, status: PredictionStatus) -> None:
        self.status = status
        self.calls: list[tuple[str, float, float]] = []

    def thermal_model(self, area_id: str):
        if area_id != "buero":
            return None
        return SimpleNamespace(
            binding=SimpleNamespace(confirmed=True, outdoor_temperature_entity_id=None)
        )

    def predict_thermal(self, area_id, *, current_celsius, target_celsius, outdoor_celsius, now):
        self.calls.append((area_id, current_celsius, target_celsius))
        value = 1500.0 if self.status is PredictionStatus.OK else None
        return PredictionResult(
            self.status, value, None, 0.9, "thermal:buero", 1, 12, now, None, None, (), ""
        )


@pytest.mark.parametrize(
    "question",
    ["Wann ist das Büro warm?", "Wie lange braucht das Wohnzimmer bis 23 Grad?"],
)
def test_question_without_model_gets_the_honest_no_model_answer(question):
    answer = answer_thermal_question(question, ENTITIES, None, NOW)
    assert answer is not None
    assert "kein belastbares Modell" in answer
    assert "Aktuell sind es" in answer


def test_confirmed_model_gives_an_estimate_for_the_room_target():
    house = _House(PredictionStatus.OK)
    answer = answer_thermal_question("Wann ist das Büro warm?", ENTITIES, house, NOW)  # type: ignore[arg-type]
    assert answer == "Im Büro sind 21 Grad voraussichtlich in etwa 25 Minuten erreicht."
    assert house.calls == [("buero", 19.4, 21.0)]


def test_unusable_model_still_answers_honestly():
    house = _House(PredictionStatus.LOW_CONFIDENCE)
    answer = answer_thermal_question("Wann ist das Büro warm?", ENTITIES, house, NOW)  # type: ignore[arg-type]
    assert answer is not None and "kein belastbares Modell" in answer


@pytest.mark.parametrize(
    "text",
    ["Wie warm ist es im Büro?", "Wann ist die Waschmaschine fertig?", "Mach das Büro warm."],
)
def test_other_sentences_are_not_heating_time_questions(text):
    assert not is_thermal_question(text)
