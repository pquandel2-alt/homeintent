"""7.9.1 Teil B: confirmation tone instead of speech.

Matrix: every kind of turn result x both styles x every channel
(satellite, media player of the device, text without device, device
without playback). Checked: speech empty or not, exactly 0 or 1 tone (never
two), and the tone's target device. Partial success, an unconfirmed effect
and "nothing executed" always speak, also with ``tone`` (mandatory).

The tone runs after the turn (the satellite must be idle again); the test
awaits the background task like Home Assistant's loop would.
"""

from __future__ import annotations

import asyncio
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

SATELLITE = "assist_satellite.kueche"
SPEAKER = "media_player.kuechenradio"
EXPECTED = {
    "turn_on": "on", "turn_off": "off", "open_cover": "open", "close_cover": "closed",
}


@dataclass
class Device:
    """Records tones and announcements; devices follow unless told not to."""

    hass: Any
    sink_call: Any
    stuck: set[str] = field(default_factory=set)
    failing: set[str] = field(default_factory=set)
    tone_fails: bool = False
    tones: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    async def async_call(self, domain, service, data=None, blocking=False, **kwargs):
        data = dict(data or {})
        if (domain, service) in {("assist_satellite", "announce"), ("media_player", "play_media")}:
            if self.tone_fails and "message" not in data:
                raise RuntimeError("satellite busy")
            self.tones.append((domain, service, data))
            return
        ids = data.get("entity_id")
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        if any(entity_id in self.failing for entity_id in ids):
            raise RuntimeError("device offline")
        await self.sink_call(domain, service, data, blocking=blocking, **kwargs)
        expected = EXPECTED.get(service)
        from homeassistant.core import State

        for entity_id in ids:
            if expected is not None and entity_id not in self.stuck:
                self.hass.states._states[entity_id] = State(entity_id, expected)


def _house(monkeypatch, tmp_path, style: str, *, channel: str) -> tuple[HouseConversation, Device]:
    import homeintent.response_style as response_style
    from homeassistant.core import State

    options = {**PUSH_OPTIONS, "response_style": style}
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=options)
    hass = house.entity.hass
    device = Device(hass, hass.services.async_call)
    hass.services.async_call = device.async_call
    for entity in house.entities:
        hass.states._states.setdefault(entity.entity_id, State(entity.entity_id, entity.state))
    hass.states._states[SATELLITE] = State(SATELLITE, "idle", {"supported_features": 1})
    hass.states._states[SPEAKER] = State(SPEAKER, "idle", {"supported_features": 512 | 1048576})
    devices = {"dev-satellite": [SATELLITE, SPEAKER], "dev-radio": [SPEAKER], "dev-button": ["sensor.knopf"]}
    monkeypatch.setattr(response_style, "_device_entities", lambda hass, device_id: devices.get(device_id, []))
    monkeypatch.setattr(response_style, "IDLE_GRACE_SECONDS", 0)
    house.channel = channel  # type: ignore[attr-defined]
    return house, device


def _say(house: HouseConversation, text: str) -> str:
    from homeassistant.components.conversation import ConversationInput

    channel = house.channel  # type: ignore[attr-defined]
    extra = {
        "satellite": {"satellite_id": SATELLITE, "device_id": "dev-satellite"},
        "media_player": {"device_id": "dev-radio"},
        "text": {},
        "no_playback": {"device_id": "dev-button"},
    }[channel]

    async def turn() -> str:
        result = await house.entity._async_handle_message(
            ConversationInput(
                text=text, conversation_id=house.conversation_id,
                context=types.SimpleNamespace(user_id=house.user), **extra,
            ),
            chat_log=None,
        )
        pending = list(house.entity.hass._tasks)
        house.entity.hass._tasks.clear()
        await asyncio.gather(*pending)
        return result.response.speech or ""

    return asyncio.run(turn())


# (setup turns, final turn, kind) - the kind decides speech vs tone.
_RESULTS = {
    "executed": ((), "Schalte das Flurlicht ein."),
    "executed_two": ((), "Schalte das Flurlicht und die Stehlampe ein."),
    "scene": ((), "Aktiviere die Szene Filmabend."),
    "automation_yes": (("Wenn die Haustür geöffnet wird, schalte das Flurlicht ein.",), "Ja."),
    "monitor_stop": (
        ("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.", "Ja."), "Stopp die Garagen-Meldung."
    ),
    "question": ((), "Schalte das Licht ein."),
    "confirmation_question": ((), "Öffne das Garagentor."),
    "preview": ((), "Wenn die Haustür geöffnet wird, schalte das Flurlicht ein."),
    "refusal": ((), "Schalte den Fernseher im Keller ein."),
    "no_after_preview": (("Wenn die Haustür geöffnet wird, schalte das Flurlicht ein.",), "Nein."),
    "query": ((), "Ist das Garagentor offen?"),
    "partial": ((), "Schalte das Flurlicht und die Stehlampe ein."),
    "unconfirmed": ((), "Schalte das Flurlicht ein."),
}
_TONE_KINDS = {"executed", "executed_two", "scene", "automation_yes", "monitor_stop"}


def _prepare(device: Device, kind: str) -> None:
    if kind == "partial":
        device.failing.add("light.stehlampe")
    if kind == "unconfirmed":
        device.stuck.add("light.flurlicht")


@pytest.mark.parametrize("channel", ["satellite", "media_player", "text", "no_playback"])
@pytest.mark.parametrize("kind", list(_RESULTS))
def test_tone_style_matrix(monkeypatch, tmp_path, kind, channel):
    house, device = _house(monkeypatch, tmp_path, "tone", channel=channel)
    _prepare(device, kind)
    setup, final = _RESULTS[kind]
    for text in setup:
        _say(house, text)
    device.tones.clear()
    speech = _say(house, final)
    if kind in _TONE_KINDS and channel in {"satellite", "media_player"}:
        assert speech == "", (kind, speech)
        assert len(device.tones) == 1, device.tones
        domain, service, data = device.tones[0]
        if channel == "satellite":
            assert (domain, service) == ("assist_satellite", "announce")
            assert data["entity_id"] == SATELLITE and data["preannounce"] is False
            assert data["media_id"] == "/api/homeintent/static/confirm.mp3"
        else:
            assert (domain, service) == ("media_player", "play_media")
            assert data["entity_id"] == SPEAKER and data.get("announce") is True
    elif kind in _TONE_KINDS and channel == "text":
        assert speech == "Erledigt." and device.tones == []
    else:
        assert speech.strip(), (kind, channel)
        assert speech != "Erledigt."
        assert device.tones == []


@pytest.mark.parametrize("channel", ["satellite", "media_player", "text", "no_playback"])
@pytest.mark.parametrize("kind", list(_RESULTS))
def test_spoken_style_never_plays_a_tone(monkeypatch, tmp_path, kind, channel):
    house, device = _house(monkeypatch, tmp_path, "spoken", channel=channel)
    _prepare(device, kind)
    setup, final = _RESULTS[kind]
    for text in setup:
        _say(house, text)
    speech = _say(house, final)
    assert speech.strip() and speech != "Erledigt."
    assert device.tones == []


@pytest.mark.parametrize("kind", ["partial", "unconfirmed", "refusal", "no_after_preview"])
def test_partial_unconfirmed_and_nothing_done_always_speak(monkeypatch, tmp_path, kind):
    house, device = _house(monkeypatch, tmp_path, "tone", channel="satellite")
    _prepare(device, kind)
    setup, final = _RESULTS[kind]
    for text in setup:
        _say(house, text)
    device.tones.clear()
    speech = _say(house, final)
    assert speech.strip() and device.tones == []
    if kind == "partial":
        assert "Stehlampe" in speech  # the part that was not done is named


def test_a_failed_tone_is_said_instead(monkeypatch, tmp_path):
    house, device = _house(monkeypatch, tmp_path, "tone", channel="satellite")
    device.tone_fails = True
    assert _say(house, "Schalte das Flurlicht ein.") == ""
    [spoken] = device.tones
    assert spoken[2]["message"] == "Erledigt." and spoken[2]["entity_id"] == SATELLITE
    assert house.entity.hass.data["homeintent_response_tone_stats"]["failed"] == 1


def test_the_tone_waits_until_the_satellite_is_idle(monkeypatch, tmp_path):
    from homeassistant.core import State

    house, device = _house(monkeypatch, tmp_path, "tone", channel="satellite")
    hass = house.entity.hass
    hass.states._states[SATELLITE] = State(SATELLITE, "processing", {"supported_features": 1})

    async def pipeline_ends() -> None:
        await asyncio.sleep(0.2)
        assert device.tones == []  # nothing during the running pipeline
        hass.states._states[SATELLITE] = State(SATELLITE, "idle", {"supported_features": 1})

    from homeassistant.components.conversation import ConversationInput

    async def turn() -> None:
        await house.entity._async_handle_message(
            ConversationInput(
                text="Schalte das Flurlicht ein.", conversation_id="c",
                context=types.SimpleNamespace(user_id="admin"), satellite_id=SATELLITE,
            ),
            chat_log=None,
        )
        await asyncio.gather(pipeline_ends(), *hass._tasks)

    asyncio.run(turn())
    assert len(device.tones) == 1


def test_the_trace_is_unchanged_by_the_style(monkeypatch, tmp_path):
    """Only the output changes: the execution is recorded the same way."""
    traces = []
    for style in ("spoken", "tone"):
        house, _ = _house(monkeypatch, tmp_path / style, style, channel="satellite")
        _say(house, "Schalte das Flurlicht ein.")
        store = house.entity.hass.data.get("homeintent_execution_trace")
        traces.append(len(getattr(store, "records", ()) or ()) if store is not None else None)
    assert traces[0] == traces[1]


@pytest.mark.parametrize(("custom", "expected"), [
    ("", "/api/homeintent/static/confirm.mp3"),
    ("media-source://media_source/local/ping.mp3", "media-source://media_source/local/ping.mp3"),
    ("/local/ping.mp3", "/local/ping.mp3"),
    ("https://example.com/ping.mp3", "/api/homeintent/static/confirm.mp3"),
])
def test_only_local_sounds(custom, expected):
    from homeintent.response_style import media_id_for

    assert media_id_for({"confirmation_media_id": custom}) == expected


def test_the_decision_is_typed():
    from homeintent.response_style import ResponseChannel, ResponseDecision, decide_response
    from homeintent.turn_outcome import TurnOutcomeKind as K, TurnOutcomes

    def decide(kinds, **flags):
        values = dict(query_answer=False, error=False, awaiting_answer=False, channel=ResponseChannel.SATELLITE)
        values.update(flags)
        return decide_response("tone", TurnOutcomes(list(kinds)), **values)

    assert decide([K.EXECUTED]) is ResponseDecision.TONE
    assert decide([K.EXECUTED, K.EXECUTED]) is ResponseDecision.TONE
    assert decide([]) is ResponseDecision.SPEAK
    for kinds in ([K.EXECUTED, K.NOT_DONE], [K.UNCONFIRMED], [K.NOT_DONE], [K.LEARNED], [K.EXECUTED, K.LEARNED]):
        assert decide(kinds) is ResponseDecision.SPEAK
    assert decide([K.EXECUTED], awaiting_answer=True) is ResponseDecision.SPEAK
    assert decide([K.EXECUTED], error=True) is ResponseDecision.SPEAK
    assert decide([K.EXECUTED], query_answer=True) is ResponseDecision.SPEAK
    assert decide([K.EXECUTED], channel=ResponseChannel.TEXT) is ResponseDecision.DONE_TEXT
    assert decide([K.EXECUTED], channel=ResponseChannel.NO_PLAYBACK) is ResponseDecision.SPEAK
    assert decide_response(
        "spoken", TurnOutcomes([K.EXECUTED]), query_answer=False, error=False, awaiting_answer=False,
        channel=ResponseChannel.SATELLITE,
    ) is ResponseDecision.SPEAK
