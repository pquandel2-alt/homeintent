"""Realistic single-family house used as HomeIntent test bed.

Every entry is ``(domain, object_id, friendly name, area, options)``. The
object id becomes the entity id, the area is wired into the area/floor
registry on start, and ``options`` configures the simulated capabilities.
"""

from __future__ import annotations

from typing import Any

FLOORS: dict[str, dict[str, Any]] = {
    "Keller": {"level": -1, "aliases": {"Untergeschoss"}},
    "Erdgeschoss": {"level": 0, "aliases": {"EG", "unten"}},
    "Obergeschoss": {"level": 1, "aliases": {"OG", "oben"}},
    "Außenbereich": {"level": None, "aliases": {"draußen", "außen"}},
}

AREAS: dict[str, dict[str, Any]] = {
    "Wohnzimmer": {"floor": "Erdgeschoss", "aliases": {"Stube"}},
    "Küche": {"floor": "Erdgeschoss", "aliases": set()},
    "Esszimmer": {"floor": "Erdgeschoss", "aliases": set()},
    "Flur": {"floor": "Erdgeschoss", "aliases": {"Diele", "Eingang"}},
    "Gäste-WC": {"floor": "Erdgeschoss", "aliases": {"Gästeklo"}},
    "Büro": {"floor": "Erdgeschoss", "aliases": {"Arbeitszimmer"}},
    "Schlafzimmer": {"floor": "Obergeschoss", "aliases": set()},
    "Kinderzimmer": {"floor": "Obergeschoss", "aliases": {"Zimmer von Lena"}},
    "Badezimmer": {"floor": "Obergeschoss", "aliases": {"Bad"}},
    "Flur Obergeschoss": {"floor": "Obergeschoss", "aliases": {"Flur oben"}},
    "Hauswirtschaftsraum": {"floor": "Keller", "aliases": {"Waschküche"}},
    "Kellerraum": {"floor": "Keller", "aliases": set()},
    "Garage": {"floor": "Außenbereich", "aliases": set()},
    "Garten": {"floor": "Außenbereich", "aliases": set()},
    "Terrasse": {"floor": "Außenbereich", "aliases": set()},
}

DIM = {"dimmable": True}
CT = {"dimmable": True, "color_temp": True}
RGB = {"dimmable": True, "color": True, "color_temp": True}

HOUSE: list[tuple[str, str, str, str | None, dict[str, Any]]] = [
    # --- Lichter -------------------------------------------------------
    ("light", "wohnzimmer_deckenlicht", "Wohnzimmer Deckenlicht", "Wohnzimmer", CT),
    ("light", "stehlampe", "Stehlampe", "Wohnzimmer", {**DIM, "aliases": ["Leselampe"]}),
    ("light", "wohnzimmer_led_streifen", "LED-Streifen Wohnzimmer", "Wohnzimmer", RGB),
    ("light", "kuechenlicht", "Küchenlicht", "Küche", DIM),
    ("light", "kuecheninsel", "Kücheninsel", "Küche", DIM),
    ("light", "esszimmer_pendelleuchte", "Esszimmer Pendelleuchte", "Esszimmer", CT),
    ("light", "flurlicht", "Flurlicht", "Flur", {}),
    ("light", "gaeste_wc_licht", "Gäste-WC Licht", "Gäste-WC", {}),
    ("light", "buerolicht", "Bürolicht", "Büro", CT),
    ("light", "schreibtischlampe", "Schreibtischlampe", "Büro", DIM),
    ("light", "schlafzimmerlicht", "Schlafzimmerlicht", "Schlafzimmer", DIM),
    ("light", "nachttischlampe_links", "Nachttischlampe links", "Schlafzimmer", DIM),
    ("light", "nachttischlampe_rechts", "Nachttischlampe rechts", "Schlafzimmer", DIM),
    ("light", "kinderzimmerlicht", "Kinderzimmerlicht", "Kinderzimmer", RGB),
    ("light", "nachtlicht", "Nachtlicht", "Kinderzimmer", DIM),
    ("light", "badezimmerlicht", "Badezimmerlicht", "Badezimmer", DIM),
    ("light", "spiegelschrank", "Spiegelschrank", "Badezimmer", {}),
    ("light", "flurlicht_oben", "Flurlicht oben", "Flur Obergeschoss", DIM),
    ("light", "kellerlicht", "Kellerlicht", "Kellerraum", {}),
    ("light", "waschkuechenlicht", "Waschküchenlicht", "Hauswirtschaftsraum", {}),
    ("light", "garagenlicht", "Garagenlicht", "Garage", {}),
    ("light", "aussenbeleuchtung", "Außenbeleuchtung", "Garten", DIM),
    ("light", "terrassenlicht", "Terrassenlicht", "Terrasse", DIM),
    # --- Schalter ------------------------------------------------------
    ("switch", "kaffeemaschine", "Kaffeemaschine", "Küche", {}),
    ("switch", "fernseher_steckdose", "Steckdose Fernseher", "Wohnzimmer", {}),
    ("switch", "gartenpumpe", "Gartenpumpe", "Garten", {}),
    ("switch", "heizluefter_bad", "Heizlüfter", "Badezimmer", {}),
    # --- Rollläden / Tore -------------------------------------------------
    ("cover", "wohnzimmer_rollladen_links", "Wohnzimmer Rollladen links", "Wohnzimmer", {"class": "shutter", "position": True}),
    ("cover", "wohnzimmer_rollladen_rechts", "Wohnzimmer Rollladen rechts", "Wohnzimmer", {"class": "shutter", "position": True}),
    ("cover", "kuechenrollladen", "Küchenrollladen", "Küche", {"class": "shutter", "position": True}),
    ("cover", "esszimmer_rollladen", "Esszimmer Rollladen", "Esszimmer", {"class": "shutter", "position": True}),
    ("cover", "buero_raffstore", "Büro Raffstore", "Büro", {"class": "blind", "position": True, "tilt": True}),
    ("cover", "schlafzimmer_rollladen", "Schlafzimmer Rollladen", "Schlafzimmer", {"class": "shutter", "position": True}),
    ("cover", "kinderzimmer_rollladen", "Kinderzimmer Rollladen", "Kinderzimmer", {"class": "shutter", "position": True}),
    ("cover", "badezimmer_rollladen", "Badezimmer Rollladen", "Badezimmer", {"class": "shutter", "position": True}),
    ("cover", "garagentor", "Garagentor", "Garage", {"class": "garage", "position": False, "travel": 6.0}),
    ("cover", "markise", "Markise", "Terrasse", {"class": "awning", "position": True}),
    # --- Heizung -------------------------------------------------------
    ("climate", "heizung_wohnzimmer", "Heizung Wohnzimmer", "Wohnzimmer", {"current": 20.4, "target": 21.0}),
    ("climate", "heizung_kueche", "Heizung Küche", "Küche", {"current": 20.9, "target": 20.0}),
    ("climate", "heizung_buero", "Heizung Büro", "Büro", {"current": 19.1, "target": 20.5}),
    ("climate", "heizung_schlafzimmer", "Heizung Schlafzimmer", "Schlafzimmer", {"current": 18.2, "target": 18.0}),
    ("climate", "heizung_kinderzimmer", "Heizung Kinderzimmer", "Kinderzimmer", {"current": 20.1, "target": 20.0}),
    ("climate", "heizung_badezimmer", "Heizung Badezimmer", "Badezimmer", {"current": 21.8, "target": 22.0}),
    # --- Sensoren ------------------------------------------------------
    ("sensor", "temperatur_wohnzimmer", "Temperatur Wohnzimmer", "Wohnzimmer", {"class": "temperature", "unit": "°C", "mirror": "climate.heizung_wohnzimmer"}),
    ("sensor", "temperatur_kueche", "Temperatur Küche", "Küche", {"class": "temperature", "unit": "°C", "mirror": "climate.heizung_kueche"}),
    ("sensor", "temperatur_buero", "Temperatur Büro", "Büro", {"class": "temperature", "unit": "°C", "mirror": "climate.heizung_buero"}),
    ("sensor", "temperatur_schlafzimmer", "Temperatur Schlafzimmer", "Schlafzimmer", {"class": "temperature", "unit": "°C", "mirror": "climate.heizung_schlafzimmer"}),
    ("sensor", "temperatur_kinderzimmer", "Temperatur Kinderzimmer", "Kinderzimmer", {"class": "temperature", "unit": "°C", "mirror": "climate.heizung_kinderzimmer"}),
    ("sensor", "temperatur_badezimmer", "Temperatur Badezimmer", "Badezimmer", {"class": "temperature", "unit": "°C", "mirror": "climate.heizung_badezimmer"}),
    ("sensor", "temperatur_keller", "Temperatur Keller", "Kellerraum", {"class": "temperature", "unit": "°C", "value": 14.6}),
    ("sensor", "aussentemperatur", "Außentemperatur", "Garten", {"class": "temperature", "unit": "°C", "value": 12.3}),
    ("sensor", "luftfeuchtigkeit_badezimmer", "Luftfeuchtigkeit Badezimmer", "Badezimmer", {"class": "humidity", "unit": "%", "value": 68}),
    ("sensor", "luftfeuchtigkeit_wohnzimmer", "Luftfeuchtigkeit Wohnzimmer", "Wohnzimmer", {"class": "humidity", "unit": "%", "value": 47}),
    ("sensor", "luftfeuchtigkeit_schlafzimmer", "Luftfeuchtigkeit Schlafzimmer", "Schlafzimmer", {"class": "humidity", "unit": "%", "value": 52}),
    ("sensor", "co2_wohnzimmer", "CO2 Wohnzimmer", "Wohnzimmer", {"class": "carbon_dioxide", "unit": "ppm", "value": 812}),
    ("sensor", "stromverbrauch_haus", "Stromverbrauch Haus", None, {"class": "power", "unit": "W", "value": 432}),
    ("sensor", "energiezaehler", "Energiezähler", "Hauswirtschaftsraum", {"class": "energy", "unit": "kWh", "value": 18234.7, "state_class": "total_increasing"}),
    ("sensor", "leistung_waschmaschine", "Leistung Waschmaschine", "Hauswirtschaftsraum", {"class": "power", "unit": "W", "value": 1840}),
    ("sensor", "leistung_trockner", "Leistung Trockner", "Hauswirtschaftsraum", {"class": "power", "unit": "W", "value": 0}),
    ("sensor", "leistung_kaffeemaschine", "Leistung Kaffeemaschine", "Küche", {"class": "power", "unit": "W", "value": 3}),
    ("sensor", "waschmaschine_status", "Waschmaschine Status", "Hauswirtschaftsraum", {"value": "running"}),
    ("sensor", "waschmaschine_fertig", "Waschmaschine fertig um", "Hauswirtschaftsraum", {"class": "timestamp", "offset_minutes": 42}),
    ("sensor", "helligkeit_aussen", "Helligkeit außen", "Garten", {"class": "illuminance", "unit": "lx", "value": 5400}),
    ("sensor", "batterie_fenster_bad", "Batterie Fenstersensor Bad", "Badezimmer", {"class": "battery", "unit": "%", "value": 14}),
    ("sensor", "batterie_fenster_kueche", "Batterie Fenstersensor Küche", "Küche", {"class": "battery", "unit": "%", "value": 88}),
    ("sensor", "batterie_bewegungsmelder_flur", "Batterie Bewegungsmelder Flur", "Flur", {"class": "battery", "unit": "%", "value": 61}),
    ("sensor", "batterie_rauchmelder_oben", "Batterie Rauchmelder oben", "Flur Obergeschoss", {"class": "battery", "unit": "%", "value": 9}),
    # --- Binärsensoren ---------------------------------------------------
    ("binary_sensor", "wohnzimmerfenster", "Wohnzimmerfenster", "Wohnzimmer", {"class": "window", "on": False}),
    ("binary_sensor", "kuechenfenster", "Küchenfenster", "Küche", {"class": "window", "on": True}),
    ("binary_sensor", "buerofenster", "Bürofenster", "Büro", {"class": "window", "on": False}),
    ("binary_sensor", "badezimmerfenster", "Badezimmerfenster", "Badezimmer", {"class": "window", "on": True}),
    ("binary_sensor", "schlafzimmerfenster", "Schlafzimmerfenster", "Schlafzimmer", {"class": "window", "on": False}),
    ("binary_sensor", "kinderzimmerfenster", "Kinderzimmerfenster", "Kinderzimmer", {"class": "window", "on": False}),
    ("binary_sensor", "haustuer", "Haustür", "Flur", {"class": "door", "on": False}),
    ("binary_sensor", "terrassentuer", "Terrassentür", "Wohnzimmer", {"class": "door", "on": False}),
    ("binary_sensor", "bewegung_flur", "Bewegungsmelder Flur", "Flur", {"class": "motion", "on": False}),
    ("binary_sensor", "praesenz_wohnzimmer", "Präsenz Wohnzimmer", "Wohnzimmer", {"class": "occupancy", "on": True}),
    ("binary_sensor", "praesenz_buero", "Präsenz Büro", "Büro", {"class": "occupancy", "on": False}),
    ("binary_sensor", "praesenz_schlafzimmer", "Präsenz Schlafzimmer", "Schlafzimmer", {"class": "occupancy", "on": False}),
    ("binary_sensor", "rauchmelder_flur", "Rauchmelder Flur", "Flur Obergeschoss", {"class": "smoke", "on": False}),
    ("binary_sensor", "wassermelder_keller", "Wassermelder Keller", "Hauswirtschaftsraum", {"class": "moisture", "on": False}),
    # --- Medien ----------------------------------------------------------
    ("media_player", "wohnzimmer_tv", "Wohnzimmer TV", "Wohnzimmer", {"sources": ["HDMI 1", "Netflix", "YouTube", "Tagesschau"], "state": "off"}),
    ("media_player", "kuechenradio", "Küchenradio", "Küche", {"sources": ["Radio Bob", "Bayern 3", "Deutschlandfunk"], "state": "playing"}),
    ("media_player", "lautsprecher_schlafzimmer", "Lautsprecher Schlafzimmer", "Schlafzimmer", {"sources": ["Spotify", "Einschlafgeräusche"], "state": "idle"}),
    # --- Sonstige Geräte ----------------------------------------------------
    ("fan", "deckenventilator", "Deckenventilator", "Schlafzimmer", {"presets": ["Nacht", "Turbo", "Natur"], "speed_count": 5}),
    ("fan", "badluefter", "Badlüfter", "Badezimmer", {"speed_count": 3}),
    ("vacuum", "saugroboter", "Saugroboter", "Wohnzimmer", {"fan_speeds": ["leise", "normal", "stark", "maximal"]}),
    ("lawn_mower", "maehroboter", "Mähroboter", "Garten", {}),
    ("lock", "haustuerschloss", "Haustürschloss", "Flur", {}),
    ("lock", "gartentor_schloss", "Gartentor", "Garten", {}),
    ("valve", "bewaesserung", "Bewässerung Garten", "Garten", {"position": True}),
    ("valve", "hauptwasserventil", "Hauptwasserventil", "Hauswirtschaftsraum", {"position": False}),
    ("humidifier", "luftbefeuchter", "Luftbefeuchter", "Schlafzimmer", {"modes": ["normal", "eco", "Schlaf"]}),
    ("water_heater", "warmwasserspeicher", "Warmwasserspeicher", "Hauswirtschaftsraum", {"modes": ["eco", "performance", "off"]}),
    ("alarm_control_panel", "alarmanlage", "Alarmanlage", "Flur", {}),
    ("select", "heizprogramm", "Heizprogramm", None, {"options": ["Komfort", "Eco", "Abwesend", "Urlaub"], "value": "Komfort"}),
    ("number", "poolpumpe_drehzahl", "Poolpumpe Drehzahl", "Garten", {"min": 0, "max": 3000, "step": 100, "value": 1200, "unit": "U/min"}),
    ("button", "kaffeemaschine_entkalken", "Kaffeemaschine entkalken", "Küche", {}),
    ("button", "tuerklingel_stumm", "Türklingel stummschalten", "Flur", {}),
    # --- Personen / Kommunikation ---------------------------------------
    ("device_tracker", "handy_philipp", "Handy Philipp", None, {"home": True}),
    ("device_tracker", "handy_anna", "Handy Anna", None, {"home": True}),
    ("notify", "handy_philipp_nachricht", "Handy Philipp", None, {}),
    ("notify", "handy_anna_nachricht", "Handy Anna", None, {}),
    ("tts", "sprachausgabe", "Haus Sprachausgabe", None, {}),
]
