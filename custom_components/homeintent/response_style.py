"""Confirmation tone instead of speech (7.9.1 Teil B).

With the option ``response_style = "tone"`` HomeIntent speaks only when it
asks something or something went wrong. When *everything* the user said was
done, the turn gets no speech and exactly one short confirmation sound on
the device the request came from.

The decision is made once, at the end of the turn, from typed facts
(``turn_outcome.TurnOutcomes``, the intent response type and error code,
and whether a question is open) - never from the reply text. In doubt,
HomeIntent speaks: a tone only ever means "Alles, was du gesagt hast, ist
passiert."

Why HomeIntent plays the sound itself (Home Assistant 2026.9.2):

* ``assist_pipeline/pipeline.py`` plays its own ``acknowledge.mp3``
  (``ACKNOWLEDGE_PATH``) only for the built-in agent's answers whose targets
  are all in the satellite's area (``_get_all_targets_in_satellite_area``);
  an empty reply of another agent makes the pipeline skip TTS altogether
  (``if all_targets_in_satellite_area or tts_input.strip()``) - silence,
  no sound. ``ConversationResult`` has no field for media.
* ``AssistSatelliteEntity.async_internal_announce`` (service
  ``assist_satellite.announce``, feature ``ANNOUNCE``) plays a ``media_id``
  with ``preannounce=False``, but first calls ``_cancel_running_pipeline()``.
  Called while the pipeline still runs (i.e. from inside this turn) it would
  cancel the very pipeline that delivers this answer.

Hence the tone is played *after* the turn: a background task waits until
the satellite reports ``idle`` again (the pipeline sets it on ``RUN_END``
when no TTS was produced), with a time limit, and then calls the official
``assist_satellite.announce`` service. A media player without satellite
gets ``media_player.play_media`` with ``announce: true``.

The sound is the integration's own ``sounds/confirm.mp3`` (synthesized for
this project, no third-party rights), served on a static path registered
like ``assist_satellite`` registers its ``preannounce.mp3``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .const import (
    CONF_CONFIRMATION_MEDIA_ID,
    CONF_RESPONSE_STYLE,
    DEFAULT_RESPONSE_STYLE,
    RESPONSE_STYLE_TONE,
)
from .execution_context import system_context
from .turn_outcome import TurnOutcomes

_LOGGER = logging.getLogger(__name__)

CONFIRM_SOUND_PATH = Path(__file__).parent / "sounds" / "confirm.mp3"
CONFIRM_SOUND_URL = "/api/homeintent/static/confirm.mp3"
DONE_TEXT = "Erledigt."
# The pipeline returns to idle right after RUN_END; wait at most this long.
IDLE_TIMEOUT_SECONDS = 15.0
# After idle the pipeline task finishes its last statements.
IDLE_GRACE_SECONDS = 0.2
STATS_KEY = "homeintent_response_tone_stats"

_SATELLITE_ANNOUNCE = 1  # AssistSatelliteEntityFeature.ANNOUNCE
_MEDIA_PLAY_MEDIA = 512  # MediaPlayerEntityFeature.PLAY_MEDIA
_MEDIA_ANNOUNCE = 1048576  # MediaPlayerEntityFeature.MEDIA_ANNOUNCE


class ResponseChannel(Enum):
    SATELLITE = "satellite"  # assist_satellite entity with ANNOUNCE
    MEDIA_PLAYER = "media_player"  # media player of the speaking device
    TEXT = "text"  # chat/websocket without a device
    NO_PLAYBACK = "no_playback"  # a device that cannot play a sound


class ResponseDecision(Enum):
    SPEAK = "speak"
    TONE = "tone"
    DONE_TEXT = "done_text"


@dataclass(frozen=True)
class ToneTarget:
    channel: ResponseChannel
    entity_id: str | None = None
    announce: bool = False  # media player supports MEDIA_ANNOUNCE


def decide_response(
    style: str,
    outcomes: TurnOutcomes,
    *,
    query_answer: bool,
    error: bool,
    awaiting_answer: bool,
    channel: ResponseChannel,
) -> ResponseDecision:
    """The one decision: speech or tone. Pure and typed."""
    if style != RESPONSE_STYLE_TONE:
        return ResponseDecision.SPEAK
    if awaiting_answer or error or query_answer or not outcomes.fully_executed:
        # Questions, failures, refusals, partial results, unconfirmed
        # effects, answers and learned facts are said.
        return ResponseDecision.SPEAK
    if channel is ResponseChannel.TEXT:
        return ResponseDecision.DONE_TEXT
    if channel is ResponseChannel.NO_PLAYBACK:
        return ResponseDecision.SPEAK
    return ResponseDecision.TONE


def _features(hass: Any, entity_id: str) -> int | None:
    state = hass.states.get(entity_id)
    if state is None or getattr(state, "state", None) in {"unavailable", "unknown"}:
        return None
    value = (getattr(state, "attributes", None) or {}).get("supported_features", 0)
    return int(value) if isinstance(value, int) else 0


def resolve_channel(hass: Any, user_input: Any) -> ToneTarget:
    """Where a tone for this turn could be played - the requesting device."""
    satellite_id = getattr(user_input, "satellite_id", None)
    device_id = getattr(user_input, "device_id", None)
    if isinstance(satellite_id, str) and satellite_id:
        features = _features(hass, satellite_id)
        if features is not None and features & _SATELLITE_ANNOUNCE:
            return ToneTarget(ResponseChannel.SATELLITE, satellite_id)
    if isinstance(device_id, str) and device_id:
        for entity_id in _device_entities(hass, device_id):
            domain = entity_id.split(".", 1)[0]
            features = _features(hass, entity_id)
            if features is None:
                continue
            if domain == "assist_satellite" and features & _SATELLITE_ANNOUNCE:
                return ToneTarget(ResponseChannel.SATELLITE, entity_id)
        for entity_id in _device_entities(hass, device_id):
            features = _features(hass, entity_id)
            if entity_id.startswith("media_player.") and features is not None and features & _MEDIA_PLAY_MEDIA:
                return ToneTarget(
                    ResponseChannel.MEDIA_PLAYER, entity_id, announce=bool(features & _MEDIA_ANNOUNCE)
                )
    if satellite_id or device_id:
        return ToneTarget(ResponseChannel.NO_PLAYBACK)
    return ToneTarget(ResponseChannel.TEXT)


def _device_entities(hass: Any, device_id: str) -> list[str]:
    try:
        from homeassistant.helpers import entity_registry as er
    except ImportError:  # pragma: no cover - stub without registry
        return []
    try:
        registry = er.async_get(hass)
        entries = er.async_entries_for_device(registry, device_id)
    except Exception:  # noqa: BLE001 - registry lookups never break a turn
        return []
    return sorted(entry.entity_id for entry in entries)


def _stats(hass: Any) -> dict[str, int]:
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return {}
    return data.setdefault(STATS_KEY, {"played": 0, "failed": 0, "spoken_fallback": 0})


async def _async_wait_idle(hass: Any, entity_id: str, timeout: float) -> bool:
    loop_time = asyncio.get_running_loop().time
    deadline = loop_time() + timeout
    while loop_time() < deadline:
        state = hass.states.get(entity_id)
        if state is not None and getattr(state, "state", None) == "idle":
            return True
        await asyncio.sleep(0.05)
    return False


async def async_play_tone(hass: Any, target: ToneTarget, media_id: str) -> bool:
    """Play exactly one tone on ``target``; on failure say "Erledigt." there.

    Runs after the turn returned. Returns whether the tone was played.
    """
    stats = _stats(hass)
    assert target.entity_id is not None
    try:
        if target.channel is ResponseChannel.SATELLITE:
            if not await _async_wait_idle(hass, target.entity_id, IDLE_TIMEOUT_SECONDS):
                raise TimeoutError(f"{target.entity_id} did not return to idle")
            await asyncio.sleep(IDLE_GRACE_SECONDS)
            await hass.services.async_call(
                "assist_satellite", "announce",
                {"entity_id": target.entity_id, "media_id": media_id, "preannounce": False},
                blocking=True, context=system_context(),
            )
        else:
            url = media_id
            if url.startswith("/"):
                try:
                    from homeassistant.components.media_player import async_process_play_media_url
                except ImportError:  # pragma: no cover - stub without media_player
                    pass
                else:
                    url = async_process_play_media_url(hass, url)
            data: dict[str, Any] = {
                "entity_id": target.entity_id,
                "media_content_id": url,
                "media_content_type": "music",
            }
            if target.announce:
                data["announce"] = True
            await hass.services.async_call(
                "media_player", "play_media", data, blocking=True, context=system_context()
            )
    except Exception as err:  # noqa: BLE001 - a missing tone must never stay silent
        stats["failed"] = stats.get("failed", 0) + 1
        _LOGGER.warning("HomeIntent-Bestätigungston auf %s fehlgeschlagen: %s", target.entity_id, err)
        if target.channel is ResponseChannel.SATELLITE:
            try:
                await hass.services.async_call(
                    "assist_satellite", "announce",
                    {"entity_id": target.entity_id, "message": DONE_TEXT, "preannounce": False},
                    blocking=True, context=system_context(),
                )
                stats["spoken_fallback"] = stats.get("spoken_fallback", 0) + 1
            except Exception as spoken_err:  # noqa: BLE001
                _LOGGER.warning("Auch die gesprochene Bestätigung schlug fehl: %s", spoken_err)
        return False
    stats["played"] = stats.get("played", 0) + 1
    return True


def media_id_for(options: Mapping[str, object]) -> str:
    """The configured local sound, else the built-in one - never an external
    URL (same rule as the timer chime)."""
    custom = options.get(CONF_CONFIRMATION_MEDIA_ID)
    if isinstance(custom, str):
        custom = custom.strip()
        if custom.startswith(("media-source://", "/local/", "/api/")):
            return custom
        if custom:
            _LOGGER.warning("Ignoring non-local confirmation sound %s", custom)
    return CONFIRM_SOUND_URL


def style_of(options: Mapping[str, object]) -> str:
    value = options.get(CONF_RESPONSE_STYLE, DEFAULT_RESPONSE_STYLE)
    return value if isinstance(value, str) else DEFAULT_RESPONSE_STYLE


def apply_response_style(
    hass: Any,
    options: Mapping[str, object],
    user_input: Any,
    result: Any,
    outcomes: TurnOutcomes,
    awaiting_answer: bool,
) -> ResponseDecision:
    """Apply the decision to the turn's reply; start the tone if chosen.

    The execution trace is untouched: only the output changes.
    """
    style = style_of(options)
    if style != RESPONSE_STYLE_TONE:
        return ResponseDecision.SPEAK
    response = result.response
    kind = getattr(response.response_type, "value", response.response_type)
    target = resolve_channel(hass, user_input)
    decision = decide_response(
        style, outcomes,
        query_answer=kind == "query_answer",
        error=kind == "error" or getattr(response, "error_code", None) is not None,
        awaiting_answer=awaiting_answer,
        channel=target.channel,
    )
    if decision is ResponseDecision.DONE_TEXT:
        response.async_set_speech(DONE_TEXT)
    elif decision is ResponseDecision.TONE:
        response.async_set_speech("")
        hass.async_create_background_task(
            async_play_tone(hass, target, media_id_for(options)), "homeintent_confirmation_tone"
        )
    return decision


async def async_register_sound(hass: Any) -> None:
    """Serve ``sounds/confirm.mp3`` once per Home Assistant instance."""
    data = getattr(hass, "data", None)
    if not isinstance(data, dict) or data.get(CONFIRM_SOUND_URL):
        return
    http = getattr(hass, "http", None)
    if http is None:
        return
    from homeassistant.components.http import StaticPathConfig

    await http.async_register_static_paths(
        [StaticPathConfig(CONFIRM_SOUND_URL, str(CONFIRM_SOUND_PATH), cache_headers=True)]
    )
    data[CONFIRM_SOUND_URL] = True


__all__ = (
    "CONFIRM_SOUND_URL",
    "apply_response_style",
    "DONE_TEXT",
    "ResponseChannel",
    "ResponseDecision",
    "ToneTarget",
    "async_play_tone",
    "async_register_sound",
    "decide_response",
    "media_id_for",
    "resolve_channel",
    "style_of",
)
