"""HomeIntent V5 Teil 7/10 (V5.23/V5.25/V5.26, "Dry Run"): the full spoken
confirm/cancel/unclear round-trip for automation creation, exercised through
the real ``NluConversationEntity._async_handle_message()`` orchestration -
not a second reimplementation of ``render_automation_preview()``/
``classify_confirmation_reply()``/``generate_ha_automation_config()``/
``AutomationExecutor`` (each already has its own dedicated pure-function
test file; this file's job is to prove the whole live pipeline wires them
together correctly turn-to-turn).

Reuses ``tests/_ha_stub.py`` and the ``_make_entity``/``_run`` harness
pattern ``tests/test_integration_wave_e2e.py`` already established (Regel 6)
- including its real PyYAML-backed ``homeassistant.util.yaml``/``.file``
stubs, so the automations.yaml read-modify-write round-trip is exercised for
real, just redirected at a per-test ``tmp_path`` instead of ``/tmp`` so nothing
leaks between test runs (see ``_make_entity``'s ``config.path`` override
below).

``test_case_6_automation_sentence_never_calls_a_service`` in
``test_integration_wave_e2e.py`` already covers the describing/previewing
first turn in isolation; this file picks up from there and covers the
second (confirming) turn.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml as pyyaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.controllers.automations import (  # noqa: E402
    AUTOMATION_CANCELLED_TEXT,
    AUTOMATION_CONFIRMATION_UNCLEAR_TEXT,
    AUTOMATION_CREATED_TEXT,
)
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import intent  # noqa: E402

KUECHE_FENSTER_ZU = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küchenfenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
KUECHE_LICHT = EntitySnapshot(
    "light.kueche_licht", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
ALL_ENTITIES = [KUECHE_FENSTER_ZU, KUECHE_LICHT]

AUTOMATION_SENTENCE = "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein."


def _make_entity(monkeypatch, tmp_path: Path) -> NluConversationEntity:
    entry = ConfigEntry()
    entity = NluConversationEntity(entry)
    entity.hass = HomeAssistant()
    # Redirect the stub's config dir at a per-test tmp_path instead of the
    # shared "/tmp" _ha_stub.py's own HomeAssistant() defaults to - so
    # automations.yaml round-trips for real (same PyYAML-backed stub every
    # AutomationExecutor test already relies on) without leaking state
    # between test functions or requiring manual /tmp cleanup.
    entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: ALL_ENTITIES
    )
    return entity


def _run(entity: NluConversationEntity, text: str, conversation_id: str = "conv-1"):
    user_input = ConversationInput(text=text, conversation_id=conversation_id)
    return asyncio.run(entity._async_handle_message(user_input, chat_log=None))


def _automations_yaml(tmp_path: Path) -> list[dict]:
    path = tmp_path / "automations.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return pyyaml.safe_load(handle) or []


def _metadata_json(tmp_path: Path) -> dict:
    path = tmp_path / "homeintent_automation_metadata.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --- full YES round-trip: preview turn, then "Ja" persists + reloads -------


def test_automation_turn_does_not_pay_direct_understanding_pipeline(
    monkeypatch, tmp_path
):
    entity = _make_entity(monkeypatch, tmp_path)

    def fail_direct_understanding(*args, **kwargs):
        raise AssertionError("automation turn entered direct understanding")

    monkeypatch.setattr(entity._engine, "understand", fail_direct_understanding)

    result = _run(entity, AUTOMATION_SENTENCE, conversation_id="single-analysis")

    assert "Soll diese Automation erstellt werden?" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_yes_reply_creates_the_automation_and_calls_automation_reload(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    preview_result = _run(entity, AUTOMATION_SENTENCE, conversation_id="wave7")
    assert "Soll diese Automation erstellt werden?" in preview_result.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    confirm_result = _run(entity, "Ja", conversation_id="wave7")

    assert confirm_result.response.speech == AUTOMATION_CREATED_TEXT
    assert confirm_result.response.error_code is None
    entity.hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)

    automations = _automations_yaml(tmp_path)
    assert len(automations) == 1
    assert automations[0]["alias"] == AUTOMATION_SENTENCE
    assert "id" in automations[0]


def test_yes_reply_also_records_a_metadata_entry_for_the_new_automation(monkeypatch, tmp_path):
    """V5.30/V5.31: the sidecar metadata written by ``AutomationExecutor``
    (see ``test_automation_executor.py`` for its own dedicated, mocked-I/O
    tests) must actually reach disk through the real conversation turn too -
    not just when the executor is driven directly."""
    entity = _make_entity(monkeypatch, tmp_path)

    _run(entity, AUTOMATION_SENTENCE, conversation_id="wave8a")
    _run(entity, "Ja", conversation_id="wave8a")

    automation_id = _automations_yaml(tmp_path)[0]["id"]
    metadata = _metadata_json(tmp_path)
    assert set(metadata) == {automation_id}
    entry = metadata[automation_id]
    assert entry["automation_id"] == automation_id
    assert entry["source_text"] == AUTOMATION_SENTENCE
    assert entry["created_by"] == "homeintent"
    assert entry["version"] == 1


def test_a_stray_ja_with_no_pending_confirmation_is_a_fresh_not_understood(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    # No preceding automation sentence on this conversation_id - "Ja" alone
    # is not a sentence any grammar matches, and must never be silently
    # treated as a leftover confirmation (Regel 4).
    result = _run(entity, "Ja", conversation_id="fresh")

    assert result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH
    entity.hass.services.async_call.assert_not_awaited()


# --- NO / cancel round-trip -------------------------------------------------


def test_no_reply_cancels_and_writes_nothing(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    _run(entity, AUTOMATION_SENTENCE, conversation_id="wave7")
    cancel_result = _run(entity, "Nein", conversation_id="wave7")

    assert cancel_result.response.speech == AUTOMATION_CANCELLED_TEXT
    entity.hass.services.async_call.assert_not_awaited()
    assert _automations_yaml(tmp_path) == []


def test_after_a_no_reply_the_pending_confirmation_is_cleared(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    _run(entity, AUTOMATION_SENTENCE, conversation_id="wave7")
    _run(entity, "Nein", conversation_id="wave7")

    # A later "Ja" on the same conversation_id must not resurrect the
    # cancelled confirmation - it's just another unmatched sentence now.
    stray_result = _run(entity, "Ja", conversation_id="wave7")

    assert stray_result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH
    entity.hass.services.async_call.assert_not_awaited()


# --- UNCLEAR round-trip: re-asks, keeps the pending state ------------------


def test_unclear_reply_re_asks_and_keeps_the_pending_confirmation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    _run(entity, AUTOMATION_SENTENCE, conversation_id="wave7")
    unclear_result = _run(entity, "Wie ist das Wetter?", conversation_id="wave7")

    assert unclear_result.response.speech == AUTOMATION_CONFIRMATION_UNCLEAR_TEXT
    entity.hass.services.async_call.assert_not_awaited()
    assert _automations_yaml(tmp_path) == []

    # The pending confirmation survived the unclear reply - a "Ja" now still
    # confirms the original automation, not a fresh sentence.
    confirm_result = _run(entity, "Ja", conversation_id="wave7")
    assert confirm_result.response.speech == AUTOMATION_CREATED_TEXT
    assert len(_automations_yaml(tmp_path)) == 1


def test_complete_device_command_replaces_pending_automation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    conversation_id = "topic-switch"
    _run(entity, AUTOMATION_SENTENCE, conversation_id)

    replacement = _run(
        entity,
        "Lass das, mach lieber das Küchenlicht an.",
        conversation_id,
    )

    assert replacement.response.error_code is None
    entity.hass.services.async_call.assert_awaited_once_with(
        "homeassistant",
        "turn_on",
        {"entity_id": KUECHE_LICHT.entity_id},
        blocking=True,
    )
    assert _automations_yaml(tmp_path) == []
    later_yes = _run(entity, "Ja", conversation_id)
    assert later_yes.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH


# --- two automations in a row append, never overwrite -----------------------


def test_a_second_confirmed_automation_appends_rather_than_overwrites(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    _run(entity, AUTOMATION_SENTENCE, conversation_id="first")
    _run(entity, "Ja", conversation_id="first")

    _run(entity, AUTOMATION_SENTENCE, conversation_id="second")
    _run(entity, "Ja", conversation_id="second")

    automations = _automations_yaml(tmp_path)
    assert len(automations) == 2
    assert automations[0]["id"] != automations[1]["id"]
