"""7.9 W7: "etwas Ungewöhnliches" - never invented.

"Ungewöhnlich" means exactly the situations the proactive detection (V12,
``situation_detection``) knows: the answer lists them, says whether the
detection is on, which options it needs, and offers to switch muted ones
back on for this user (Ja/Nein).  A concrete term ("wenn Wasser austritt")
takes the normal sentence-based path.
"""

from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation
from homeintent.attention_policy import AttentionStateStore
from homeintent.proactive_model import SituationKind

_VAGUE = (
    "Melde dich, wenn etwas Ungewöhnliches passiert.",
    "Sag mir Bescheid, wenn im Haus was Komisches ist.",
    "Warne mich, wenn irgendwas Seltsames los ist.",
    "Benachrichtige mich, falls etwas Auffälliges passiert.",
    "Pass auf, ob etwas Merkwürdiges passiert.",
)
_CATALOG = (
    "länger als 15 Minuten offen", "wenn niemand zuhause ist", "Rauch-, Wasser-, Gas- oder CO-Melder",
)


async def _nothing(*_args, **_kwargs):
    return None


class _Proactive:
    """The parts of ``ProactiveRuntime`` a conversation turn touches."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.persisted = 0
        self.engine = types.SimpleNamespace(attention_state=AttentionStateStore(), async_persist=self._persist)
        self.dialogs = types.SimpleNamespace(
            async_handle_owned_turn=_nothing, async_handle_bare_reply=_nothing,
        )

    def record_authenticated_turn(self, *_args) -> None:
        return None

    async def _persist(self) -> None:
        self.persisted += 1


def _house(monkeypatch, tmp_path, enabled: bool | None = None, options: dict | None = None) -> HouseConversation:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options={**PUSH_OPTIONS, **(options or {})})
    if enabled is not None:
        house.entity._runtime_data.proactive_context = _Proactive(enabled)
    return house


@pytest.mark.parametrize("text", _VAGUE)
def test_vague_requests_list_the_real_catalog(monkeypatch, tmp_path, text):
    house = _house(monkeypatch, tmp_path, enabled=False)
    turn = house.say(text)
    assert all(item in turn.speech for item in _CATALOG), turn.speech
    assert "ausgeschaltet" in turn.speech and "Proaktive Hinweise" in turn.speech
    assert turn.calls == []
    house.say("Ja.")
    assert house.automations() == []


def test_the_appliance_kind_only_when_configured(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, enabled=False)
    assert "Waschmaschine" not in house.say(_VAGUE[0]).speech
    house = _house(monkeypatch, tmp_path / "b", enabled=False,
                   options={"proactive_appliance_entities": ["sensor.leistung_waschmaschine"]})
    assert "Waschmaschine" in house.say(_VAGUE[0]).speech


def test_switched_on_says_so(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, enabled=True)
    turn = house.say(_VAGUE[0])
    assert "für dich eingeschaltet" in turn.speech


def test_muted_kinds_are_offered_back_and_only_yes_unmutes(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, enabled=True)
    proactive = house.entity._runtime_data.proactive_context
    proactive.engine.attention_state.mute("admin", SituationKind.ENTRY_LEFT_OPEN, datetime.now(timezone.utc))
    offer = house.say(_VAGUE[1])
    assert "stummgeschaltet" in offer.speech and "wieder einschalten?" in offer.speech
    assert proactive.engine.attention_state.is_muted("admin", SituationKind.ENTRY_LEFT_OPEN)
    assert "Eingeschaltet" in house.say("Ja.").speech
    assert not proactive.engine.attention_state.is_muted("admin", SituationKind.ENTRY_LEFT_OPEN)
    assert proactive.persisted == 1


def test_no_keeps_the_mute(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, enabled=True)
    proactive = house.entity._runtime_data.proactive_context
    proactive.engine.attention_state.mute("admin", SituationKind.ENTRY_LEFT_OPEN, datetime.now(timezone.utc))
    house.say(_VAGUE[0])
    house.say("Nein.")
    assert proactive.engine.attention_state.is_muted("admin", SituationKind.ENTRY_LEFT_OPEN)


@pytest.mark.parametrize("text", [
    "Melde dich, wenn Wasser austritt.",
    "Sag mir Bescheid, wenn im Keller Wasser austritt.",
    "Warne mich, wenn der Wassermelder anschlägt.",
])
def test_a_concrete_term_takes_the_normal_path(monkeypatch, tmp_path, text):
    house = _house(monkeypatch, tmp_path, enabled=False)
    preview = house.say(text)
    assert "Soll ich das so einrichten" in preview.speech, preview.speech
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["entity_id"] == "binary_sensor.wassermelder_keller"
