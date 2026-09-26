"""The HomeIntent 7.2.0 evaluation house (see tests/eval/automation_v72/README.md)."""

from __future__ import annotations

from homeintent.entities import EntitySnapshot

IPHONE = "notify.mobile_app_iphone_von_philipp"
JULIA_PHONE = "notify.mobile_app_handy_von_julia"
USER = "ha-user-philipp"
JULIA_USER = "ha-user-julia"


def _cover(entity_id: str, name: str, area_id: str, area: str, position: int) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "cover", "open" if position else "closed",
        area_id=area_id, area_name=area, device_class="shutter",
        capabilities=frozenset({"OPEN", "CLOSE", "POSITION"}),
        attributes={"current_position": position},
    )


def _window(entity_id: str, name: str, area_id: str, area: str, device_class: str = "window") -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "binary_sensor", "off", area_id=area_id, area_name=area,
        device_class=device_class,
    )


def _light(entity_id: str, name: str, area_id: str, area: str) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "light", "off", area_id=area_id, area_name=area,
        capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
        attributes={"brightness": None},
    )


BASE_COVERS = (
    _cover("cover.wohnzimmer_rollladen", "Wohnzimmer Rollladen", "wohnzimmer", "Wohnzimmer", 0),
    _cover("cover.kueche_rollladen", "Küche Rollladen", "kueche", "Küche", 100),
    _cover("cover.schlafzimmer_rollladen", "Schlafzimmer Rollladen", "schlafzimmer", "Schlafzimmer", 0),
    _cover("cover.bad_rollladen", "Bad Rollladen", "bad", "Bad", 100),
)
BUERO_COVER = _cover("cover.buero_rollladen", "Büro Rollladen", "buero", "Büro", 20)
BUERO_LEFT = _cover("cover.buero_links", "Büro Rollladen links", "buero", "Büro", 20)
BUERO_RIGHT = _cover("cover.buero_rechts", "Büro Rollladen rechts", "buero", "Büro", 20)

OTHER = (
    _window("binary_sensor.wohnzimmer_fenster", "Wohnzimmer Fenster", "wohnzimmer", "Wohnzimmer"),
    _window("binary_sensor.buero_fenster", "Bürofenster", "buero", "Büro"),
    _window("binary_sensor.kuechenfenster", "Küchenfenster", "kueche", "Küche"),
    _window("binary_sensor.schlafzimmer_fenster", "Schlafzimmer Fenster", "schlafzimmer", "Schlafzimmer"),
    _window("binary_sensor.haustuer", "Haustür", "flur", "Flur", "door"),
    _window("binary_sensor.terrassentuer", "Terrassentür", "wohnzimmer", "Wohnzimmer", "door"),
    _window("binary_sensor.flur_bewegung", "Bewegungsmelder Flur", "flur", "Flur", "motion"),
    EntitySnapshot(
        "sensor.wohnzimmer_temperatur", "Wohnzimmer Temperatur", "sensor", "21.5",
        area_id="wohnzimmer", area_name="Wohnzimmer", unit="°C",
        device_class="temperature", state_class="measurement",
    ),
    EntitySnapshot(
        "sensor.aussentemperatur", "Außentemperatur", "sensor", "12.0", unit="°C",
        device_class="temperature", state_class="measurement",
    ),
    EntitySnapshot(
        "sensor.buero_luftfeuchtigkeit", "Büro Luftfeuchtigkeit", "sensor", "48",
        area_id="buero", area_name="Büro", unit="%", device_class="humidity",
        state_class="measurement",
    ),
    EntitySnapshot(
        "sensor.handy_akku", "Handy Akku", "sensor", "64", unit="%",
        device_class="battery", state_class="measurement",
    ),
    _light("light.wohnzimmer", "Wohnzimmerlicht", "wohnzimmer", "Wohnzimmer"),
    _light("light.kueche", "Küchenlicht", "kueche", "Küche"),
    _light("light.buero", "Bürolampe", "buero", "Büro"),
    _light("light.flur", "Flurlicht", "flur", "Flur"),
    EntitySnapshot(
        "fan.schlafzimmer", "Schlafzimmer Ventilator", "fan", "off",
        area_id="schlafzimmer", area_name="Schlafzimmer",
        capabilities=frozenset({"TURN_ON", "TURN_OFF", "FAN_SPEED"}),
        attributes={"percentage": 0},
    ),
    EntitySnapshot(
        "media_player.wohnzimmer", "Wohnzimmer Lautsprecher", "media_player", "off",
        area_id="wohnzimmer", area_name="Wohnzimmer",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
    EntitySnapshot(
        "switch.kaffeemaschine", "Kaffeemaschine", "switch", "off",
        area_id="kueche", area_name="Küche", capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
    EntitySnapshot(
        "climate.wohnzimmer", "Wohnzimmer Heizung", "climate", "heat",
        area_id="wohnzimmer", area_name="Wohnzimmer",
        capabilities=frozenset({"TURN_ON", "TURN_OFF", "TEMPERATURE"}),
    ),
    EntitySnapshot("person.philipp", "Philipp", "person", "home"),
    EntitySnapshot("person.julia", "Julia", "person", "not_home"),
    EntitySnapshot(IPHONE, "iPhone von Philipp", "notify", "unknown"),
    EntitySnapshot(JULIA_PHONE, "Handy von Julia", "notify", "unknown"),
)

WORLD: tuple[EntitySnapshot, ...] = (BUERO_COVER, *BASE_COVERS, *OTHER)
AMBIGUOUS_WORLD: tuple[EntitySnapshot, ...] = (BUERO_LEFT, BUERO_RIGHT, *BASE_COVERS, *OTHER)
