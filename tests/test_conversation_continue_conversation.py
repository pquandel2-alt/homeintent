"""``ConversationResult.continue_conversation``: keep the satellite listening
while a rückfrage is outstanding, and only then.

Without this flag a voice satellite (ESPHome/Assist) closes its microphone
after every reply, so answering "Soll die Automation erstellt werden?"
required saying the wake word again - which defeats every multi-turn dialog
this agent builds. The flag is set centrally in
``NluConversationEntity._apply_continue_conversation()`` from the pending
dialog state, not at the individual ``ConversationResult(...)`` sites.

The load-bearing test here is the *negative* one: an ordinary successful
command must leave the flag off. The follow-up context (``last_area``,
``last_command``, ``focus``) is written after every command, so a naive
"is there any stored context?" check would hold every satellite microphone in
the house open after "Mach das Licht an".

Reuses the ``_ha_stub``/``_make_entity``/``_run`` harness established by
tests/test_conversation_automation_delete.py (Regel 6).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402
from _users import as_user, install_users  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import (  # noqa: E402
    _CONTINUE_CONVERSATION_KINDS,
    NluConversationEntity,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.nlu.context import PendingDialogKind  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

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
    entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: ALL_ENTITIES
    )
    return entity


def _run(entity: NluConversationEntity, text: str, conversation_id: str = "conv-1"):
    # Managing needs the owner or an administrator (7.9.1 A2): the
    # management mechanics run as an administrator.
    install_users(entity.hass)
    user_input = ConversationInput(text=text, conversation_id=conversation_id, context=as_user("admin"))
    return asyncio.run(entity._async_handle_message(user_input, chat_log=None))


# --- a spoken question keeps the microphone open -------------------------


def test_automation_confirmation_question_continues_the_conversation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    result = _run(entity, AUTOMATION_SENTENCE)

    assert "erstellt werden?" in result.response.speech
    assert result.continue_conversation is True


def test_deletion_confirmation_question_continues_the_conversation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _run(entity, AUTOMATION_SENTENCE, conversation_id="create")
    _run(entity, "Ja", conversation_id="create")

    result = _run(entity, "Lösche die Automation für Küchenlicht.", conversation_id="delete")

    assert "gelöscht werden?" in result.response.speech
    assert result.continue_conversation is True


# --- and closes it again as soon as nothing is outstanding ---------------


def test_plain_successful_command_does_not_continue_the_conversation(monkeypatch, tmp_path):
    """The load-bearing negative case - see this module's docstring."""
    entity = _make_entity(monkeypatch, tmp_path)

    result = _run(entity, "Schalte das Küchenlicht ein.")

    assert result.response.error_code is None
    assert result.continue_conversation is False


def test_answering_the_question_ends_the_conversation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    question = _run(entity, AUTOMATION_SENTENCE)
    assert question.continue_conversation is True

    answer = _run(entity, "Ja")

    assert answer.continue_conversation is False


def test_cancelling_the_question_ends_the_conversation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _run(entity, AUTOMATION_SENTENCE)

    answer = _run(entity, "Nein")

    assert answer.continue_conversation is False


def test_unrecognised_sentence_does_not_continue_the_conversation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    result = _run(entity, "Wie hoch ist der Eiffelturm?")

    assert result.continue_conversation is False


# --- guard: a newly added pending kind must be decided on, not inherited --


def test_every_pending_dialog_kind_is_explicitly_decided():
    """Fails when a PendingDialogKind is added without classifying it.

    Membership in ``_CONTINUE_CONVERSATION_KINDS`` is the claim "this state
    means we just asked the user something out loud". Today that holds for
    every kind. If a future kind parks state *without* having asked anything,
    drop it from the set and from this assertion - deliberately, rather than
    letting it inherit an open microphone.
    """
    undecided = set(PendingDialogKind) - _CONTINUE_CONVERSATION_KINDS
    assert not undecided, (
        f"New PendingDialogKind(s) {sorted(k.name for k in undecided)} are not "
        "listed in _CONTINUE_CONVERSATION_KINDS. Decide per kind whether it "
        "means a question was spoken (satellite keeps listening) or not."
    )


# --- questions asked through the central DialogManager ---------------------

KUECHE_HEIZUNG = EntitySnapshot(
    "climate.kueche_heizung", "Küche Heizung", "climate", "heat",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "TEMPERATURE"}),
    attributes={"temperature": 20, "current_temperature": 19, "hvac_modes": ["heat", "off"]},
)


def _make_goal_entity(monkeypatch, tmp_path: Path) -> NluConversationEntity:
    entity = _make_entity(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ha_conversation,
        "build_entity_snapshots",
        lambda hass, entry: [*ALL_ENTITIES, KUECHE_HEIZUNG],
    )
    return entity


def test_routine_definition_question_continues_the_conversation(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    question = _run(entity, "Bereite das Haus für die Nacht vor.")

    assert "Was soll ich" in question.response.speech
    assert question.continue_conversation is True


def test_declining_the_routine_definition_cancels_it(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _run(entity, "Bereite das Haus für die Nacht vor.")

    declined = _run(entity, "Nein.")
    command = _run(entity, "Mach das Küchenlicht an.")

    assert "keine Routine" in declined.response.speech
    assert declined.continue_conversation is False
    assert command.response.speech == "Küchenlicht eingeschaltet."
    assert command.continue_conversation is False


def test_goal_clarification_and_plan_preview_continue_the_conversation(monkeypatch, tmp_path):
    entity = _make_goal_entity(monkeypatch, tmp_path)

    clarification = _run(
        entity, "Sorge dafür, dass es morgen um 7 Uhr in der Küche 21 Grad hat."
    )
    preview = _run(entity, "Ersteres.")
    declined = _run(entity, "Nein.")

    assert clarification.continue_conversation is True
    assert "Planvorschau" in preview.response.speech
    assert preview.continue_conversation is True
    assert declined.continue_conversation is False


def test_resolved_selection_does_not_keep_the_mirrored_task_listening(monkeypatch, tmp_path):
    # The DialogManager mirrors legacy pending state under a fixed task id at
    # the start of a turn; that mirror must not hold the microphone open after
    # the question has been answered in the same turn.
    laternen = [
        EntitySnapshot(
            f"light.{area}_laterne", f"{name} Laterne", "light", "off",
            area_id=area, area_name=name,
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        )
        for area, name in (("hof", "Hof"), ("turm", "Turm"))
    ]
    entity = _make_entity(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: laternen
    )

    question = _run(entity, "Schalte die Laterne ein.")
    answer = _run(entity, "Die im Turm.")

    assert question.continue_conversation is True
    assert "Turm Laterne" in answer.response.speech
    assert answer.continue_conversation is False
