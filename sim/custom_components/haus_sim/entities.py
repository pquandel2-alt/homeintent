"""Simulated devices with realistic capabilities and physics."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.components.humidifier import HumidifierEntity, HumidifierEntityFeature
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.components.lock import LockEntity
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.notify import NotifyEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.tts import TextToSpeechEntity
from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.components.valve import ValveEntity, ValveEntityFeature
from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .house import HOUSE

_LOGGER = logging.getLogger(__name__)
DOMAIN = "haus_sim"


def log_call(hass: HomeAssistant, entity_id: str, action: str, data: Any = None) -> None:
    """Record every service call a simulated device receives."""
    hass.data[DOMAIN]["calls"].append(
        {
            "time": dt_util.utcnow().isoformat(),
            "entity_id": entity_id,
            "action": action,
            "data": data,
        }
    )


class SimEntity:
    """Mixin: stable ids, device, call log."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def _sim_init(self, hass: HomeAssistant, domain: str, key: str, name: str, opts: dict[str, Any]) -> None:
        self.hass = hass
        self.entity_id = f"{domain}.{key}"
        self._attr_unique_id = f"haus_sim_{domain}_{key}"
        self._attr_name = name
        self._opts = opts
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{domain}_{key}")},
            name=name,
            manufacturer="Haus-Simulation",
            model=domain,
        )
        hass.data[DOMAIN]["entities"][self.entity_id] = self

    def _log(self, action: str, data: Any = None) -> None:
        log_call(self.hass, self.entity_id, action, data)


# --------------------------------------------------------------------- light
class SimLight(SimEntity, LightEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "light", key, name, opts)
        modes: set[ColorMode] = set()
        if opts.get("color"):
            modes = {ColorMode.HS, ColorMode.COLOR_TEMP}
        elif opts.get("color_temp"):
            modes = {ColorMode.COLOR_TEMP}
        elif opts.get("dimmable"):
            modes = {ColorMode.BRIGHTNESS}
        else:
            modes = {ColorMode.ONOFF}
        self._attr_supported_color_modes = modes
        self._attr_color_mode = next(iter(sorted(modes)))
        if ColorMode.HS in modes:
            self._attr_color_mode = ColorMode.HS
        self._attr_is_on = False
        self._attr_brightness = 180 if modes != {ColorMode.ONOFF} else None
        self._attr_min_color_temp_kelvin = 2200
        self._attr_max_color_temp_kelvin = 6500
        self._attr_color_temp_kelvin = 3000 if ColorMode.COLOR_TEMP in modes else None
        self._attr_hs_color = (30.0, 60.0) if ColorMode.HS in modes else None

    async def async_turn_on(self, **kwargs):
        self._log("turn_on", {k: v for k, v in kwargs.items()})
        self._attr_is_on = True
        if "brightness" in kwargs and self._attr_brightness is not None:
            self._attr_brightness = kwargs["brightness"]
        if "hs_color" in kwargs and ColorMode.HS in self._attr_supported_color_modes:
            self._attr_hs_color = kwargs["hs_color"]
            self._attr_color_mode = ColorMode.HS
        if "color_temp_kelvin" in kwargs and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._attr_color_temp_kelvin = kwargs["color_temp_kelvin"]
            self._attr_color_mode = ColorMode.COLOR_TEMP
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._log("turn_off", kwargs)
        self._attr_is_on = False
        self.async_write_ha_state()


# -------------------------------------------------------------------- switch
class SimSwitch(SimEntity, SwitchEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "switch", key, name, opts)
        self._attr_is_on = key == "fernseher_steckdose"

    async def async_turn_on(self, **kwargs):
        self._log("turn_on")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._log("turn_off")
        self._attr_is_on = False
        self.async_write_ha_state()


# --------------------------------------------------------------------- cover
class SimCover(SimEntity, CoverEntity):
    """Covers travel realistically (default 3 s end-to-end)."""

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "cover", key, name, opts)
        self._attr_device_class = opts.get("class")
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if opts.get("position"):
            features |= CoverEntityFeature.SET_POSITION
        if opts.get("tilt"):
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
            self._attr_current_cover_tilt_position = 50
        self._attr_supported_features = features
        self._pos = 0 if opts.get("class") == "garage" else 100
        if key == "markise":
            self._pos = 0
        self._travel = float(opts.get("travel", 3.0))
        self._task: asyncio.Task | None = None
        self._attr_is_opening = False
        self._attr_is_closing = False

    @property
    def current_cover_position(self):
        return self._pos if self._opts.get("position") else None

    @property
    def is_closed(self):
        return self._pos == 0

    async def _move(self, target: int) -> None:
        self._attr_is_opening = target > self._pos
        self._attr_is_closing = target < self._pos
        self.async_write_ha_state()
        step_time = self._travel / 10
        while self._pos != target:
            await asyncio.sleep(step_time)
            if target > self._pos:
                self._pos = min(self._pos + 10, target)
            else:
                self._pos = max(self._pos - 10, target)
            self.async_write_ha_state()
        self._attr_is_opening = self._attr_is_closing = False
        self.async_write_ha_state()

    def _start(self, target: int) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = self.hass.async_create_background_task(self._move(target), f"move {self.entity_id}")

    async def async_open_cover(self, **kwargs):
        self._log("open_cover")
        self._start(100)

    async def async_close_cover(self, **kwargs):
        self._log("close_cover")
        self._start(0)

    async def async_set_cover_position(self, **kwargs):
        self._log("set_cover_position", kwargs)
        self._start(int(kwargs["position"]))

    async def async_stop_cover(self, **kwargs):
        self._log("stop_cover")
        if self._task:
            self._task.cancel()
        self._attr_is_opening = self._attr_is_closing = False
        self.async_write_ha_state()

    async def async_open_cover_tilt(self, **kwargs):
        self._log("open_cover_tilt")
        self._attr_current_cover_tilt_position = 100
        self.async_write_ha_state()

    async def async_close_cover_tilt(self, **kwargs):
        self._log("close_cover_tilt")
        self._attr_current_cover_tilt_position = 0
        self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs):
        self._log("set_cover_tilt_position", kwargs)
        self._attr_current_cover_tilt_position = int(kwargs["tilt_position"])
        self.async_write_ha_state()


# ------------------------------------------------------------------- climate
class SimClimate(SimEntity, ClimateEntity):
    """Room heats 0.1 K per tick towards target, cools towards 17 °C when off."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]
    _attr_preset_modes = ["comfort", "eco", "boost", "away"]
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "climate", key, name, opts)
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._attr_current_temperature = float(opts["current"])
        self._attr_target_temperature = float(opts["target"])
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_preset_mode = "comfort"

    @property
    def hvac_action(self):
        if self._attr_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._attr_current_temperature < self._attr_target_temperature - 0.2:
            return HVACAction.HEATING
        return HVACAction.IDLE

    def tick(self) -> None:
        cur = self._attr_current_temperature
        if self._attr_hvac_mode != HVACMode.OFF and cur < self._attr_target_temperature - 0.05:
            cur = min(cur + 0.1, self._attr_target_temperature)
        elif cur > (self._attr_target_temperature if self._attr_hvac_mode != HVACMode.OFF else 17.0):
            cur = cur - 0.05
        self._attr_current_temperature = round(cur, 2)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        self._log("set_temperature", kwargs)
        if "temperature" in kwargs:
            self._attr_target_temperature = float(kwargs["temperature"])
        if "hvac_mode" in kwargs:
            self._attr_hvac_mode = kwargs["hvac_mode"]
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        self._log("set_hvac_mode", hvac_mode)
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode):
        self._log("set_preset_mode", preset_mode)
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)


# -------------------------------------------------------------------- sensor
class SimSensor(SimEntity, SensorEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "sensor", key, name, opts)
        self._attr_device_class = opts.get("class")
        self._attr_native_unit_of_measurement = opts.get("unit")
        if opts.get("class") not in ("timestamp", None):
            self._attr_state_class = (
                SensorStateClass.TOTAL_INCREASING
                if opts.get("state_class") == "total_increasing"
                else SensorStateClass.MEASUREMENT
            )
        if opts.get("class") == "timestamp":
            self._attr_native_value = dt_util.utcnow().replace(microsecond=0) + timedelta(
                minutes=opts.get("offset_minutes", 30)
            )
        else:
            self._attr_native_value = opts.get("value")

    def tick(self) -> None:
        mirror = self._opts.get("mirror")
        if mirror and (ent := self.hass.data[DOMAIN]["entities"].get(mirror)):
            self._attr_native_value = round(ent.current_temperature, 1)
            self.async_write_ha_state()

    def set_value(self, value: Any) -> None:
        if self._attr_device_class == "timestamp" and isinstance(value, str):
            value = dt_util.parse_datetime(value)
        self._attr_native_value = value
        self.async_write_ha_state()


class SimBinarySensor(SimEntity, BinarySensorEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "binary_sensor", key, name, opts)
        self._attr_device_class = opts.get("class")
        self._attr_is_on = bool(opts.get("on"))

    def set_value(self, value: Any) -> None:
        self._attr_is_on = value in (True, "on", "true", 1, "1")
        self.async_write_ha_state()


# -------------------------------------------------------------- media player
class SimMediaPlayer(SimEntity, MediaPlayerEntity):
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "media_player", key, name, opts)
        self._attr_source_list = opts["sources"]
        self._attr_source = opts["sources"][0]
        self._attr_state = MediaPlayerState(opts.get("state", "off"))
        self._attr_volume_level = 0.3
        self._attr_is_volume_muted = False
        self._attr_media_title = "Morgenmagazin" if key == "kuechenradio" else None

    def _set(self, state):
        self._attr_state = state
        self.async_write_ha_state()

    async def async_turn_on(self):
        self._log("turn_on")
        self._set(MediaPlayerState.ON)

    async def async_turn_off(self):
        self._log("turn_off")
        self._set(MediaPlayerState.OFF)

    async def async_media_play(self):
        self._log("media_play")
        self._set(MediaPlayerState.PLAYING)

    async def async_media_pause(self):
        self._log("media_pause")
        self._set(MediaPlayerState.PAUSED)

    async def async_media_stop(self):
        self._log("media_stop")
        self._set(MediaPlayerState.IDLE)

    async def async_media_next_track(self):
        self._log("media_next_track")

    async def async_media_previous_track(self):
        self._log("media_previous_track")

    async def async_set_volume_level(self, volume):
        self._log("volume_set", volume)
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_mute_volume(self, mute):
        self._log("volume_mute", mute)
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_select_source(self, source):
        self._log("select_source", source)
        self._attr_source = source
        self._set(MediaPlayerState.PLAYING)

    async def async_play_media(self, media_type, media_id, **kwargs):
        self._log("play_media", {"media_type": media_type, "media_id": media_id})
        self.hass.data[DOMAIN]["played_media"].append(
            {"entity_id": self.entity_id, "media_id": media_id, "time": dt_util.utcnow().isoformat()}
        )


# ----------------------------------------------------------------------- fan
class SimFan(SimEntity, FanEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "fan", key, name, opts)
        features = FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if opts.get("presets"):
            features |= FanEntityFeature.PRESET_MODE | FanEntityFeature.OSCILLATE | FanEntityFeature.DIRECTION
            self._attr_preset_modes = opts["presets"]
            self._attr_oscillating = False
            self._attr_current_direction = "forward"
        self._attr_supported_features = features
        self._attr_speed_count = opts.get("speed_count", 3)
        self._attr_percentage = 0
        self._attr_preset_mode = None

    @property
    def is_on(self):
        return bool(self._attr_percentage) or self._attr_preset_mode is not None

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        self._log("turn_on", {"percentage": percentage, "preset_mode": preset_mode})
        self._attr_percentage = percentage if percentage is not None else (self._attr_percentage or 40)
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._log("turn_off")
        self._attr_percentage = 0
        self._attr_preset_mode = None
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage):
        self._log("set_percentage", percentage)
        self._attr_percentage = percentage
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode):
        self._log("set_preset_mode", preset_mode)
        self._attr_preset_mode = preset_mode
        self._attr_percentage = self._attr_percentage or 60
        self.async_write_ha_state()

    async def async_oscillate(self, oscillating):
        self._log("oscillate", oscillating)
        self._attr_oscillating = oscillating
        self.async_write_ha_state()

    async def async_set_direction(self, direction):
        self._log("set_direction", direction)
        self._attr_current_direction = direction
        self.async_write_ha_state()


# -------------------------------------------------------------------- vacuum
class SimVacuum(SimEntity, StateVacuumEntity):
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.LOCATE
    )

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "vacuum", key, name, opts)
        self._attr_fan_speed_list = opts["fan_speeds"]
        self._attr_fan_speed = "normal"
        self._attr_activity = VacuumActivity.DOCKED

    def _set(self, act):
        self._attr_activity = act
        self.async_write_ha_state()

    async def async_start(self):
        self._log("start")
        self._set(VacuumActivity.CLEANING)

    async def async_pause(self):
        self._log("pause")
        self._set(VacuumActivity.PAUSED)

    async def async_stop(self, **kwargs):
        self._log("stop")
        self._set(VacuumActivity.IDLE)

    async def async_return_to_base(self, **kwargs):
        self._log("return_to_base")
        self._set(VacuumActivity.RETURNING)

    async def async_set_fan_speed(self, fan_speed, **kwargs):
        self._log("set_fan_speed", fan_speed)
        self._attr_fan_speed = fan_speed
        self.async_write_ha_state()

    async def async_locate(self, **kwargs):
        self._log("locate")


class SimLawnMower(SimEntity, LawnMowerEntity):
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING | LawnMowerEntityFeature.PAUSE | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "lawn_mower", key, name, opts)
        self._attr_activity = LawnMowerActivity.DOCKED

    def _set(self, act):
        self._attr_activity = act
        self.async_write_ha_state()

    async def async_start_mowing(self):
        self._log("start_mowing")
        self._set(LawnMowerActivity.MOWING)

    async def async_pause(self):
        self._log("pause")
        self._set(LawnMowerActivity.PAUSED)

    async def async_dock(self):
        self._log("dock")
        self._set(LawnMowerActivity.RETURNING)


# ---------------------------------------------------------------------- lock
class SimLock(SimEntity, LockEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "lock", key, name, opts)
        self._attr_is_locked = True

    async def async_lock(self, **kwargs):
        self._log("lock")
        self._attr_is_locked = True
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs):
        self._log("unlock")
        self._attr_is_locked = False
        self.async_write_ha_state()


class SimValve(SimEntity, ValveEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "valve", key, name, opts)
        self._attr_reports_position = bool(opts.get("position"))
        feats = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
        if opts.get("position"):
            feats |= ValveEntityFeature.SET_POSITION | ValveEntityFeature.STOP
        self._attr_supported_features = feats
        self._pos = 0 if key == "bewaesserung" else 100

    @property
    def current_valve_position(self):
        return self._pos if self._attr_reports_position else None

    @property
    def is_closed(self):
        return self._pos == 0

    async def async_open_valve(self):
        self._log("open_valve")
        self._pos = 100
        self.async_write_ha_state()

    async def async_close_valve(self):
        self._log("close_valve")
        self._pos = 0
        self.async_write_ha_state()

    async def async_set_valve_position(self, position):
        self._log("set_valve_position", position)
        self._pos = position
        self.async_write_ha_state()

    async def async_stop_valve(self):
        self._log("stop_valve")


class SimHumidifier(SimEntity, HumidifierEntity):
    _attr_supported_features = HumidifierEntityFeature.MODES

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "humidifier", key, name, opts)
        self._attr_available_modes = opts["modes"]
        self._attr_mode = opts["modes"][0]
        self._attr_is_on = False
        self._attr_target_humidity = 45
        self._attr_current_humidity = 38
        self._attr_min_humidity = 30
        self._attr_max_humidity = 70

    async def async_turn_on(self, **kwargs):
        self._log("turn_on")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._log("turn_off")
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity):
        self._log("set_humidity", humidity)
        self._attr_target_humidity = humidity
        self.async_write_ha_state()

    async def async_set_mode(self, mode):
        self._log("set_mode", mode)
        self._attr_mode = mode
        self.async_write_ha_state()


class SimWaterHeater(SimEntity, WaterHeaterEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )
    _attr_min_temp = 35
    _attr_max_temp = 65

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "water_heater", key, name, opts)
        self._attr_operation_list = opts["modes"]
        self._attr_current_operation = "eco"
        self._attr_target_temperature = 50
        self._attr_current_temperature = 48

    async def async_set_temperature(self, **kwargs):
        self._log("set_temperature", kwargs)
        self._attr_target_temperature = kwargs.get("temperature", self._attr_target_temperature)
        self.async_write_ha_state()

    async def async_set_operation_mode(self, operation_mode):
        self._log("set_operation_mode", operation_mode)
        self._attr_current_operation = operation_mode
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self.async_set_operation_mode("eco")

    async def async_turn_off(self, **kwargs):
        await self.async_set_operation_mode("off")


class SimAlarm(SimEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.TRIGGER
    )

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "alarm_control_panel", key, name, opts)
        self._attr_alarm_state = AlarmControlPanelState.DISARMED

    def _set(self, st):
        self._attr_alarm_state = st
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code=None):
        self._log("disarm")
        self._set(AlarmControlPanelState.DISARMED)

    async def async_alarm_arm_home(self, code=None):
        self._log("arm_home")
        self._set(AlarmControlPanelState.ARMED_HOME)

    async def async_alarm_arm_away(self, code=None):
        self._log("arm_away")
        self._set(AlarmControlPanelState.ARMED_AWAY)

    async def async_alarm_arm_night(self, code=None):
        self._log("arm_night")
        self._set(AlarmControlPanelState.ARMED_NIGHT)

    async def async_alarm_trigger(self, code=None):
        self._log("trigger")
        self._set(AlarmControlPanelState.TRIGGERED)


class SimSelect(SimEntity, SelectEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "select", key, name, opts)
        self._attr_options = opts["options"]
        self._attr_current_option = opts["value"]

    async def async_select_option(self, option):
        self._log("select_option", option)
        self._attr_current_option = option
        self.async_write_ha_state()


class SimNumber(SimEntity, NumberEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "number", key, name, opts)
        self._attr_native_min_value = opts["min"]
        self._attr_native_max_value = opts["max"]
        self._attr_native_step = opts["step"]
        self._attr_native_value = opts["value"]
        self._attr_native_unit_of_measurement = opts.get("unit")

    async def async_set_native_value(self, value):
        self._log("set_value", value)
        self._attr_native_value = value
        self.async_write_ha_state()


class SimButton(SimEntity, ButtonEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "button", key, name, opts)

    async def async_press(self):
        self._log("press")


class SimTracker(SimEntity, ScannerEntity):
    _attr_source_type = SourceType.ROUTER

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "device_tracker", key, name, opts)
        self._home = bool(opts.get("home"))

    @property
    def is_connected(self):
        return self._home

    @property
    def mac_address(self):
        return None

    def set_value(self, value: Any) -> None:
        self._home = value in (True, "home", "on", "true")
        self.async_write_ha_state()


class SimNotify(SimEntity, NotifyEntity):
    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "notify", key, name, opts)

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        self._log("send_message", {"message": message, "title": title})
        self.hass.data[DOMAIN]["notifications"].append(
            {"target": self.entity_id, "title": title, "message": message, "time": dt_util.utcnow().isoformat()}
        )


class SimTTS(SimEntity, TextToSpeechEntity):
    _attr_supported_languages = ["de", "de-DE", "en"]
    _attr_default_language = "de"

    def __init__(self, hass, key, name, opts):
        self._sim_init(hass, "tts", key, name, opts)

    def get_tts_audio(self, message, language, options):
        self.hass.data[DOMAIN]["spoken"].append({"message": message, "time": dt_util.utcnow().isoformat()})
        # minimal valid MP3 frame header + padding
        return "mp3", b"\xff\xfb\x90\x64" + b"\x00" * 413


CLASSES = {
    "light": SimLight,
    "switch": SimSwitch,
    "cover": SimCover,
    "climate": SimClimate,
    "sensor": SimSensor,
    "binary_sensor": SimBinarySensor,
    "media_player": SimMediaPlayer,
    "fan": SimFan,
    "vacuum": SimVacuum,
    "lawn_mower": SimLawnMower,
    "lock": SimLock,
    "valve": SimValve,
    "humidifier": SimHumidifier,
    "water_heater": SimWaterHeater,
    "alarm_control_panel": SimAlarm,
    "select": SimSelect,
    "number": SimNumber,
    "button": SimButton,
    "device_tracker": SimTracker,
    "notify": SimNotify,
    "tts": SimTTS,
}


def build(hass: HomeAssistant, domain: str) -> list[Any]:
    cls = CLASSES[domain]
    return [cls(hass, key, name, opts) for d, key, name, _area, opts in HOUSE if d == domain]


def platform_setup(domain: str):
    async def async_setup_entry(hass, entry, async_add_entities):
        async_add_entities(build(hass, domain))

    return async_setup_entry
