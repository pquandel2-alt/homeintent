"""7.9.3 B5: the confirmation tone as default and the second tone.

* New installations get ``response_style = tone`` (the config flow writes
  it); an entry created before 7.9.3 without a stored value keeps "spoken",
  a stored value is never changed.
* The second tone (``notice.mp3``) for exactly one case: everything ran but
  at least one device did not report back in the wait (no wrong direction,
  not unavailable).  Optionally a very short announcement names it.
  Wrong direction, unavailable, partial success (a write that did not run),
  errors, questions and the spoken style: speech.
* Both sounds can be replaced by local paths only.
"""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

import _ha_stub
from test_effect_wait_792 import WAIT, Report, _house, _speak_or_tone

_ha_stub.install()


def test_the_notice_sound_is_shipped_and_differs():
    root = Path(__file__).parent.parent / "custom_components" / "homeintent" / "sounds"
    notice = (root / "notice.mp3").read_bytes()
    assert notice[:3] == b"ID3" or notice[0] == 0xFF
    assert notice != (root / "confirm.mp3").read_bytes() and 1000 < len(notice) < 20000


def test_new_installation_gets_tone_and_old_entries_keep_their_style():
    from homeintent.const import DEFAULT_RESPONSE_STYLE, NEW_INSTALL_RESPONSE_STYLE
    from homeintent.response_style import style_of

    assert NEW_INSTALL_RESPONSE_STYLE == "tone"
    assert style_of({}) == DEFAULT_RESPONSE_STYLE == "spoken"  # before 7.9.3, nothing stored
    assert style_of({"response_style": "spoken"}) == "spoken"
    assert style_of({"response_style": "tone"}) == "tone"


def test_the_config_flow_writes_tone_for_a_new_entry():
    """The stub has no config flow base; the source is checked here, the
    real flow in ``tests_ha/test_options_flow.py`` (SETUP_OPTIONS)."""
    import ast

    source = (Path(__file__).parent.parent / "custom_components" / "homeintent" / "config_flow.py").read_text()
    step = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_step_user")
    options = next(keyword.value for node in ast.walk(step) if isinstance(node, ast.Call)
                   for keyword in node.keywords if keyword.arg == "options")
    pairs = {ast.unparse(key): ast.unparse(value) for key, value in zip(options.keys, options.values)}
    assert pairs["CONF_RESPONSE_STYLE"] == "NEW_INSTALL_RESPONSE_STYLE"


# Matrix: outcome × channel -> decision.
_CASES = {
    "executed": (["EXECUTED"], [], False, "tone"),
    "not_reported": (["UNCONFIRMED"], ["Stehlampe"], False, "notice"),
    "one_not_reported": (["EXECUTED", "UNCONFIRMED"], ["Flurlicht"], False, "notice"),
    "wrong_way": (["UNCONFIRMED"], [], True, "speak"),
    "unavailable": (["UNCONFIRMED"], [], True, "speak"),
    "silent_and_wrong_way": (["UNCONFIRMED", "UNCONFIRMED"], ["Flurlicht"], True, "speak"),
    "partial": (["EXECUTED", "NOT_DONE"], [], False, "speak"),
    "not_reported_and_not_done": (["UNCONFIRMED", "NOT_DONE"], ["Stehlampe"], False, "speak"),
    "nothing": ([], [], False, "speak"),
}


@pytest.mark.parametrize("channel", ["SATELLITE", "MEDIA_PLAYER", "TEXT", "NO_PLAYBACK"])
@pytest.mark.parametrize("case", list(_CASES))
@pytest.mark.parametrize("flags", list(itertools.product([False, True], repeat=3)))
def test_decision_matrix(case, channel, flags):
    from homeintent.response_style import ResponseChannel, ResponseDecision, decide_response
    from homeintent.turn_outcome import TurnOutcomeKind, TurnOutcomes

    kinds, silent, contrary, expected = _CASES[case]
    outcomes = TurnOutcomes([TurnOutcomeKind[kind] for kind in kinds], silent=list(silent), contrary=contrary)
    awaiting, error, query = flags
    decision = decide_response("tone", outcomes, query_answer=query, error=error, awaiting_answer=awaiting,
                               channel=ResponseChannel[channel])
    if awaiting or error or query or expected == "speak":
        assert decision is ResponseDecision.SPEAK
    elif expected == "notice":
        assert decision is (ResponseDecision.NOTICE if channel in {"SATELLITE", "MEDIA_PLAYER"}
                            else ResponseDecision.SPEAK)
    else:
        assert decision is {"SATELLITE": ResponseDecision.TONE, "MEDIA_PLAYER": ResponseDecision.TONE,
                            "TEXT": ResponseDecision.DONE_TEXT, "NO_PLAYBACK": ResponseDecision.SPEAK}[channel]
    spoken = decide_response("spoken", outcomes, query_answer=query, error=error, awaiting_answer=awaiting,
                             channel=ResponseChannel[channel])
    assert spoken is ResponseDecision.SPEAK


def test_live_turn_not_reported_is_the_notice_with_the_name(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, WAIT + 0.6)
    speech, _elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert speech == "" and tones == [{
        "entity_id": "assist_satellite.kueche", "message": "Stehlampe meldet sich nicht.",
        "preannounce": True, "preannounce_media_id": "/api/homeintent/static/notice.mp3",
    }]


def test_without_the_name_only_the_tone(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path)
    options = dict(house.entity.entry.options, notice_says_name=False)
    house.entity.entry.options = options
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, WAIT + 0.6)
    _speech, _elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert tones == [{"entity_id": "assist_satellite.kueche", "media_id": "/api/homeintent/static/notice.mp3",
                      "preannounce": False}]


@pytest.mark.parametrize("report", [Report("unavailable", {}, 0.05)])
def test_unavailable_stays_spoken(monkeypatch, tmp_path, report):
    house, devices = _house(monkeypatch, tmp_path)
    devices.reports[("light.stehlampe", "turn_on")] = report
    speech, _elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert tones == [] and "nicht erreichbar" in speech


def test_spoken_style_says_the_late_device(monkeypatch, tmp_path):
    house, devices = _house(monkeypatch, tmp_path, style="spoken")
    devices.reports[("light.stehlampe", "turn_on")] = Report("on", {}, WAIT + 0.6)
    speech, _elapsed, tones = _speak_or_tone(house, devices, "Schalte die Stehlampe ein.")
    assert tones == [] and "noch nicht zurückgemeldet" in speech


@pytest.mark.parametrize("value,expected", [
    ("", "/api/homeintent/static/notice.mp3"), ("/local/sounds/hm.mp3", "/local/sounds/hm.mp3"),
    ("media-source://media_source/local/hm.mp3", "media-source://media_source/local/hm.mp3"),
    ("https://example.com/x.mp3", "/api/homeintent/static/notice.mp3"),
])
def test_only_local_notice_sounds(value, expected):
    from homeintent.response_style import media_id_for

    assert media_id_for({"notice_media_id": value}, notice=True) == expected
    assert media_id_for({"notice_media_id": value}) == "/api/homeintent/static/confirm.mp3"


@pytest.mark.parametrize("names,text", [
    (["Stehlampe"], "Stehlampe meldet sich nicht."),
    (["Stehlampe", "Flurlicht"], "Stehlampe und Flurlicht melden sich nicht."),
    (["A", "B", "A"], "A und B melden sich nicht."),
])
def test_notice_text(names, text):
    from homeintent.response_style import notice_text

    assert notice_text(names) == text
