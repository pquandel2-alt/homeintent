"""F11: extended device sentences from the live test house (HA 2026.9 attributes)."""

from __future__ import annotations

import pytest

from homeintent.engine import MatchResult
from homeintent.entities import EntitySnapshot
from homeintent.nlu.capabilities import derive_capabilities
from homeintent.semantic_dialog import continue_semantic_dialog, start_semantic_dialog


def _entity(entity_id, name, state, area=None, **attributes):
    domain = entity_id.split(".", 1)[0]
    attributes = {"friendly_name": name, **attributes}
    return EntitySnapshot(
        entity_id, name, domain, state,
        area_id=area.casefold() if area else None, area_name=area,
        device_class=attributes.get("device_class"), attributes=attributes,
        capabilities=frozenset(
            c.name for c in derive_capabilities(domain, attributes.get("device_class"), attributes)
        ),
    )


CLIMATE = dict(
    hvac_modes=["heat", "auto", "off"], min_temp=5, max_temp=30, target_temp_step=0.5,
    preset_modes=["comfort", "eco", "boost", "away"], current_temperature=19.0,
    temperature=18.0, preset_mode="comfort", supported_features=401,
)
HOUSE = [
    _entity("climate.heizung_kueche", "Heizung Küche", "heat", "Küche", **CLIMATE),
    _entity("climate.heizung_schlafzimmer", "Heizung Schlafzimmer", "heat", "Schlafzimmer", **CLIMATE),
    _entity("climate.heizung_buero", "Heizung Büro", "heat", "Büro", **CLIMATE),
    _entity("sensor.temperatur_buero", "Temperatur Büro", "19.4", "Büro", device_class="temperature"),
    _entity("select.heizprogramm", "Heizprogramm", "Komfort", options=["Komfort", "Eco", "Abwesend", "Urlaub"]),
    _entity("number.poolpumpe_drehzahl", "Poolpumpe Drehzahl", "1200", "Garten", min=0, max=3000, step=100),
    _entity(
        "water_heater.warmwasserspeicher", "Warmwasserspeicher", "eco", min_temp=35, max_temp=65,
        operation_list=["eco", "performance", "off"], temperature=50, supported_features=11,
    ),
    _entity(
        "humidifier.luftbefeuchter", "Luftbefeuchter", "off", "Schlafzimmer", min_humidity=30,
        max_humidity=70, available_modes=["normal", "eco", "Schlaf"], humidity=45, supported_features=1,
    ),
    _entity(
        "vacuum.saugroboter", "Saugroboter", "docked", "Wohnzimmer",
        fan_speed_list=["leise", "normal", "stark", "maximal"], supported_features=12860,
    ),
    _entity(
        "media_player.wohnzimmer_tv", "Wohnzimmer TV", "off", "Wohnzimmer",
        source_list=["HDMI 1", "Netflix", "YouTube", "Tagesschau"], supported_features=24509,
    ),
    _entity(
        "fan.deckenventilator", "Deckenventilator", "off", "Schlafzimmer",
        preset_modes=["Nacht", "Turbo", "Natur"], oscillating=False, percentage=0,
        percentage_step=20.0, supported_features=63,
    ),
    _entity(
        "light.wohnzimmer_led_streifen", "LED-Streifen Wohnzimmer", "off", "Wohnzimmer",
        supported_color_modes=["color_temp", "hs"], min_color_temp_kelvin=2200, max_color_temp_kelvin=6500,
    ),
    _entity("light.nachttischlampe_links", "Nachttischlampe links", "off", "Schlafzimmer", supported_color_modes=["brightness"]),
    _entity("light.nachttischlampe_rechts", "Nachttischlampe rechts", "off", "Schlafzimmer", supported_color_modes=["brightness"]),
    _entity("timer.waschgang", "Waschgang", "idle", duration="1:30:00"),
    _entity("lock.haustuerschloss", "Haustürschloss", "unlocked", "Flur"),
    _entity("binary_sensor.haustuer", "Haustür", "off", "Flur", device_class="door"),
]


@pytest.mark.parametrize(
    ("text", "domain", "service", "entity_id", "data"),
    [
        ("Mach die Heizung im Schlafzimmer stark wärmer.", "climate", "set_temperature", "climate.heizung_schlafzimmer", None),
        ("Stelle die Heizung in der Küche auf Eco.", "climate", "set_preset_mode", "climate.heizung_kueche", {"preset_mode": "eco"}),
        ("Wähle beim Heizprogramm Eco.", "select", "select_option", "select.heizprogramm", {"option": "Eco"}),
        ("Stelle das Heizprogramm auf Eco.", "select", "select_option", "select.heizprogramm", {"option": "Eco"}),
        ("Stelle die Poolpumpe Drehzahl auf 1800.", "number", "set_value", "number.poolpumpe_drehzahl", {"value": 1800.0}),
        ("Stelle den Warmwasserspeicher auf 55 Grad.", "water_heater", "set_temperature", "water_heater.warmwasserspeicher", {"temperature": 55.0}),
        ("Stelle den Warmwasserspeicher auf performance.", "water_heater", "set_operation_mode", "water_heater.warmwasserspeicher", {"operation_mode": "performance"}),
        ("Stelle den Luftbefeuchter auf 50 Prozent.", "humidifier", "set_humidity", "humidifier.luftbefeuchter", {"humidity": 50}),
        ("Stelle beim Saugroboter die Saugstufe auf stark.", "vacuum", "set_fan_speed", "vacuum.saugroboter", {"fan_speed": "stark"}),
        ("Schalte den Wohnzimmer TV auf Netflix.", "media_player", "select_source", "media_player.wohnzimmer_tv", {"source": "Netflix"}),
        ("Schalte beim Deckenventilator die Oszillation ein.", "fan", "oscillate", "fan.deckenventilator", {"oscillating": True}),
        ("Lass den Deckenventilator oszillieren.", "fan", "oscillate", "fan.deckenventilator", {"oscillating": True}),
        ("Stelle den Deckenventilator auf Nacht.", "fan", "set_preset_mode", "fan.deckenventilator", {"preset_mode": "Nacht"}),
        ("Mach den LED-Streifen blau.", "light", "turn_on", "light.wohnzimmer_led_streifen", {"color_name": "blue"}),
        ("Starte den Timer Waschgang.", "timer", "start", "timer.waschgang", {}),
        ("Verriegle die Haustür.", "lock", "lock", "lock.haustuerschloss", {}),
    ],
)
def test_live_house_sentence_compiles_to_the_expected_operation(engine, text, domain, service, entity_id, data):
    result = engine.understand(text, HOUSE).payload

    assert isinstance(result, MatchResult) and result.plan is not None, text
    assert (result.plan.domain, result.plan.service) == (domain, service)
    assert result.plan.entity_id in (entity_id, [entity_id])
    if data is not None:
        assert result.plan.data == data


def test_unlock_by_door_name_still_targets_only_the_lock(engine):
    result = engine.understand("Entriegle die Haustür.", HOUSE).payload
    assert isinstance(result, MatchResult) and result.plan is not None
    assert (result.plan.service, result.plan.entity_id) == ("unlock", "lock.haustuerschloss")


def test_setting_up_a_heater_without_value_asks_for_the_temperature(engine):
    outcome = engine.understand("Stelle die Heizung im Büro ein.", HOUSE)
    assert outcome.payload is None or getattr(outcome.payload, "plan", None) is None

    dialog = start_semantic_dialog("Stelle die Heizung im Büro ein.", HOUSE)
    assert dialog is not None and dialog.question == "Auf welche Temperatur soll ich Heizung Büro stellen?"
    answer = continue_semantic_dialog("Auf 21 Grad.", dialog.pending)
    assert answer.result is not None
    assert answer.result.plan.data == {"temperature": 21.0}


def test_found_but_unsupported_device_is_not_reported_as_missing(engine):
    speech = engine.understand("Mach die Nachttischlampe rot.", HOUSE).speech or ""
    assert "freigegebenes Gerät gefunden" not in speech
    assert "Nachttischlampe links" in speech
