"""HomeIntent 7.1.2 - push notifications through the real live path.

Every test drives ``NluConversationEntity._async_handle_message()`` with an
authenticated conversation user and counts calls at the notify service
boundary (``_notify_sink.NotifySink``), which validates each payload against
Home Assistant's ``notify.send_message`` schema.  Nothing here asserts on a
parser fixture alone.

The four sentences in ``SCREENSHOT_SENTENCES`` are verbatim real production
failures and are permanent regression tests.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
import yaml as pyyaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from _notify_sink import NotifySink  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_DELIVERY_CHANNELS,
    CONF_AGENT_ENABLED,
    CONF_AGENT_NOTIFY_TARGETS,
)
from homeintent.controllers.automations import AUTOMATION_CREATED_TEXT  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.user_context import (  # noqa: E402
    NotificationTarget,
    NotificationTargetKind,
    UserContextStore,
)
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

IPHONE = "notify.mobile_app_iphone_von_philipp"
IPAD = "notify.mobile_app_ipad_von_anna"
USER = "ha-user-philipp"
OTHER_USER = "ha-user-anna"

ENTITIES = [
    EntitySnapshot(
        "binary_sensor.wohnzimmer_fenster_links", "Wohnzimmer Fenster links",
        "binary_sensor", "off", area_id="wohnzimmer", area_name="Wohnzimmer",
        device_class="window",
    ),
    EntitySnapshot(
        "binary_sensor.wohnzimmer_fenster_rechts", "Wohnzimmer Fenster rechts",
        "binary_sensor", "off", area_id="wohnzimmer", area_name="Wohnzimmer",
        device_class="window",
    ),
    EntitySnapshot(
        "binary_sensor.badezimmerfenster", "Badezimmerfenster", "binary_sensor", "off",
        area_id="bad", area_name="Badezimmer", device_class="window",
    ),
    EntitySnapshot(
        "light.kueche", "Küchenlicht", "light", "off", area_id="kueche", area_name="Küche",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
    EntitySnapshot(IPHONE, "iPhone von Philipp", "notify", "unknown"),
    EntitySnapshot(IPAD, "iPad von Anna", "notify", "unknown"),
]

SCREENSHOT_SENTENCES = (
    "Kannst du mir in 10 Sekunden eine Test Benachrichtigung schicken?",
    "Kannst du mich benachrichtigen sobald im Wohnzimmer die Fenster auf ist?",
    "Sobald die Fenster im Wohnzimmer geöffnet werden, benachrichtige mich, "
    "dass im Wohnzimmer ein Fenster offen ist.",
    "Benachrichtige mich sobald ein Fenster geöffnet wird",
)
NOT_UNDERSTOOD = "Das habe ich nicht verstanden."
NO_TARGET = (
    "Ich habe noch kein eindeutiges Push-Ziel für dich. "
    "Bitte hinterlege dein Handy in den HomeIntent-Einstellungen."
)
AMBIGUOUS = (
    "Ich habe mehrere Push-Ziele gefunden. "
    "Bitte ordne dein Gerät deinem HomeIntent-Benutzer zu."
)


class Harness:
    def __init__(self, monkeypatch, tmp_path: Path, options: dict, *, notify_entities=(IPHONE, IPAD),
                 fail: bool = False) -> None:
        self.tmp_path = tmp_path
        self.entity = NluConversationEntity(ConfigEntry(options=options))
        self.entity.hass = HomeAssistant()
        self.entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
        monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: ENTITIES)
        self.sink = NotifySink.install(self.entity.hass, tuple(notify_entities), fail=fail)
        self.store = UserContextStore(tmp_path / "users.json")
        self.entity._runtime_data.user_contexts = self.store

    def say(self, text: str, *, user: str | None = USER, conversation_id: str = "c1") -> str:
        context = types.SimpleNamespace(user_id=user)
        result = asyncio.run(self.entity._async_handle_message(
            ConversationInput(text=text, conversation_id=conversation_id, context=context),
            chat_log=None,
        ))
        return result.response.speech

    def bind(self, user: str, *targets: str) -> None:
        asyncio.run(self.store.async_set_user(
            user, person_entity_id=None, confirmed=True,
            notification_targets=[
                NotificationTarget(target, NotificationTargetKind.ENTITY) for target in targets
            ],
        ))

    def automations(self) -> list[dict]:
        path = self.tmp_path / "automations.yaml"
        if not path.exists():
            return []
        return pyyaml.safe_load(path.read_text(encoding="utf-8")) or []


def one_phone(monkeypatch, tmp_path, **extra) -> Harness:
    return Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE], **extra})


# --- immediate push ------------------------------------------------------------


def test_configured_iphone_receives_exactly_one_test_push(monkeypatch, tmp_path):
    """Real scenario: Push ON, one configured iPhone -> that exact target."""
    harness = one_phone(monkeypatch, tmp_path)

    speech = harness.say("Schick mir eine Testbenachrichtigung.")

    assert speech == "Die Testbenachrichtigung wurde gesendet."
    assert harness.sink.notify_calls == [(
        "send_message",
        {"entity_id": [IPHONE], "title": "HomeIntent",
         "message": "Testbenachrichtigung von HomeIntent."},
    )]
    assert harness.automations() == []


@pytest.mark.parametrize("sentence", (
    "Schick mir eine Testbenachrichtigung",
    "Schicke mir eine Test Benachrichtigung",
    "Sende mir eine Testnachricht",
    "Kannst du mir bitte eine Testnachricht schicken",
    "Kannst du mir eine Test Benachrichtigung schicken?",
    "Schick mir aufs Handy eine Testnachricht.",
    "schick mir eine test-benachrichtigung",
))
def test_immediate_test_push_variants(monkeypatch, tmp_path, sentence):
    harness = one_phone(monkeypatch, tmp_path)
    assert harness.say(sentence) == "Die Testbenachrichtigung wurde gesendet."
    assert len(harness.sink.notify_calls) == 1
    assert harness.sink.notify_calls[0][1]["message"] == "Testbenachrichtigung von HomeIntent."


@pytest.mark.parametrize(("sentence", "message"), (
    ("Benachrichtige mich mit dem Text Test.", "Test"),
    ("Schick mir die Nachricht 'Test erfolgreich'.", "Test erfolgreich"),
    ("Schick mir eine Push Nachricht mit dem Text Hallo.", "Hallo"),
    ("Kannst du mir bitte eine Nachricht schicken?", "Benachrichtigung von HomeIntent."),
    ("Benachrichtige mich.", "Benachrichtigung von HomeIntent."),
    ("Benachrichtige mich bitte", "Benachrichtigung von HomeIntent."),
))
def test_immediate_push_messages(monkeypatch, tmp_path, sentence, message):
    harness = one_phone(monkeypatch, tmp_path)
    assert harness.say(sentence) == "Die Benachrichtigung wurde gesendet."
    assert harness.sink.notify_calls == [(
        "send_message", {"entity_id": [IPHONE], "title": "HomeIntent", "message": message},
    )]


def test_explicit_push_does_not_need_proactive_agent(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path, **{CONF_AGENT_ENABLED: False})
    assert harness.say("Schick mir eine Testbenachrichtigung.") == (
        "Die Testbenachrichtigung wurde gesendet."
    )
    assert len(harness.sink.notify_calls) == 1


def test_disabled_push_channel_is_honoured(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path, **{CONF_AGENT_DELIVERY_CHANNELS: ["tts"]})
    speech = harness.say("Schick mir eine Testbenachrichtigung.")
    assert speech == "Push-Benachrichtigungen sind in den HomeIntent-Einstellungen deaktiviert."
    assert harness.sink.notify_calls == []


def test_no_push_target_gives_guidance_and_zero_calls(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {})
    speech = harness.say("Schick mir eine Testbenachrichtigung.")
    assert speech == NO_TARGET
    assert speech != NOT_UNDERSTOOD
    assert harness.sink.notify_calls == []


def test_two_global_targets_without_binding_are_ambiguous_zero_calls(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, IPAD]})
    assert harness.say("Schick mir eine Testbenachrichtigung.") == AMBIGUOUS
    assert harness.sink.notify_calls == []


def test_explicit_user_binding_wins_over_global_targets(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, IPAD]})
    harness.bind(USER, IPHONE)
    harness.bind(OTHER_USER, IPAD)
    assert harness.say("Schick mir eine Testbenachrichtigung.") == (
        "Die Testbenachrichtigung wurde gesendet."
    )
    assert harness.say("Schick mir eine Testbenachrichtigung.", user=OTHER_USER) == (
        "Die Testbenachrichtigung wurde gesendet."
    )
    assert [call[1]["entity_id"] for call in harness.sink.notify_calls] == [[IPHONE], [IPAD]]


def test_another_users_device_is_never_mine(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPAD]})
    harness.bind(OTHER_USER, IPAD)
    assert harness.say("Schick mir eine Testbenachrichtigung.") == NO_TARGET
    assert harness.sink.notify_calls == []


def test_anonymous_caller_in_a_bound_household_fails_closed(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    harness.bind(USER, IPHONE)
    speech = harness.say("Schick mir eine Testbenachrichtigung.", user=None)
    assert "keinem Home-Assistant-Benutzer zuordnen" in speech
    assert harness.sink.notify_calls == []


def test_named_other_person_is_not_inferred_for_an_immediate_push(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    harness.say("Schick Anna eine Testbenachrichtigung.")
    assert harness.sink.notify_calls == []


def test_uns_requires_the_confirmed_household(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, IPAD]})
    speech = harness.say("Benachrichtige uns.")
    assert speech.startswith("Für „uns“ ist noch kein bestätigter Haushalt")
    assert harness.sink.notify_calls == []


def test_unavailable_target_is_a_delivery_stage_failure(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE]},
                      notify_entities=())
    speech = harness.say("Schick mir eine Testbenachrichtigung.")
    assert speech == "Dein Push-Ziel ist momentan nicht verfügbar."
    assert harness.sink.notify_calls == []


def test_rejected_service_call_never_reports_success(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE]}, fail=True)
    speech = harness.say("Schick mir eine Testbenachrichtigung.")
    assert speech == "Die Benachrichtigung konnte nicht gesendet werden."
    assert harness.sink.notify_calls == []


def test_message_text_is_inert(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    harness.say("Schick mir eine Nachricht mit dem Text light.turn_on kitchen")
    assert harness.sink.notify_calls == [(
        "send_message",
        {"entity_id": [IPHONE], "title": "HomeIntent", "message": "light.turn_on kitchen"},
    )]
    assert harness.sink.other_calls == []


# --- delayed push ----------------------------------------------------------------


def test_screenshot_a_delayed_test_push_full_lifecycle(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)

    preview = harness.say(SCREENSHOT_SENTENCES[0])
    assert preview == (
        "In 10 Sekunden sende ich dir eine Testbenachrichtigung an dein Gerät "
        "„iPhone von Philipp“. Soll ich das so einrichten?"
    )
    assert harness.sink.notify_calls == []

    assert harness.say("Ja") == AUTOMATION_CREATED_TEXT
    [automation] = harness.automations()
    # Before the trigger fires nothing has been sent - no sleep loop exists.
    assert harness.sink.notify_calls == []
    assert automation["triggers"][0]["trigger"] == "time"
    assert any(
        condition.get("condition") == "template" and "now().date()" in condition["value_template"]
        for condition in automation["conditions"]
    )
    serialized = json.dumps(automation)
    assert "persistent_notification" not in serialized
    assert "delay" not in serialized
    push, delete = automation["actions"]
    assert push == {
        "action": "notify.send_message",
        "target": {"entity_id": [IPHONE]},
        "data": {"message": "Testbenachrichtigung von HomeIntent.", "title": "HomeIntent"},
    }
    assert delete == {"action": "homeintent.delete_automation",
                      "data": {"automation_id": automation["id"]}}

    # HA fires the one-shot: exactly the stored target receives the push and
    # the automation removes itself.
    asyncio.run(harness.sink.async_run_automation_actions(automation["actions"]))
    assert harness.sink.notify_calls == [(
        "send_message",
        {"entity_id": [IPHONE], "message": "Testbenachrichtigung von HomeIntent.",
         "title": "HomeIntent"},
    )]
    assert harness.automations() == []


@pytest.mark.parametrize("sentence", (
    "Schick mir in 10 Sekunden eine Testnachricht.",
    "In 10 Sekunden schick mir eine Testnachricht.",
    "Kannst du mir in 10 Sekunden eine Testnachricht schicken?",
))
def test_temporal_word_order_is_one_meaning(monkeypatch, tmp_path, sentence):
    harness = one_phone(monkeypatch, tmp_path)
    assert harness.say(sentence).startswith("In 10 Sekunden sende ich dir eine Testbenachrichtigung")
    assert harness.sink.notify_calls == []


def test_notify_me_in_ten_seconds_without_text(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    assert harness.say("Benachrichtige mich bitte in 10 Sekunden.").startswith(
        "In 10 Sekunden sende ich dir eine Push-Benachrichtigung"
    )


@pytest.mark.parametrize("sentence", (
    "Erinnere mich in 10 Minuten daran, den Backofen zu prüfen.",
    "Benachrichtige mich in 10 Minuten, dass ich den Backofen prüfen soll.",
    "Schick mir in 10 Minuten eine Nachricht, dass ich den Backofen prüfen soll.",
    "Sag mir in 10 Minuten Bescheid, dass ich den Backofen prüfen soll.",
))
def test_reminder_phrasings_share_one_representation(monkeypatch, tmp_path, sentence):
    harness = one_phone(monkeypatch, tmp_path)
    harness.say(sentence)
    assert harness.say("Ja") == AUTOMATION_CREATED_TEXT
    [automation] = harness.automations()
    assert automation["actions"][0] == {
        "action": "notify.send_message",
        "target": {"entity_id": [IPHONE]},
        "data": {"message": "Erinnerung: den Backofen prüfen.", "title": "HomeIntent"},
    }


def test_delayed_push_without_target_is_refused_before_persistence(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, IPAD]})
    assert harness.say(SCREENSHOT_SENTENCES[0]) == AMBIGUOUS
    assert harness.say("Ja") != AUTOMATION_CREATED_TEXT
    assert harness.automations() == []


# --- event-triggered push automations ----------------------------------------------


def _confirmed_single_automation(harness: Harness, sentence: str) -> tuple[str, dict]:
    preview = harness.say(sentence)
    assert harness.say("Ja") == AUTOMATION_CREATED_TEXT, preview
    [automation] = harness.automations()
    assert "persistent_notification" not in json.dumps(automation)
    assert "proactive_message" not in json.dumps(automation)
    return preview, automation


def test_screenshot_b_wohnzimmer_window_notification(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    preview, automation = _confirmed_single_automation(harness, SCREENSHOT_SENTENCES[1])
    assert preview == (
        "Wenn im Wohnzimmer ein Fenster geöffnet wird, sende ich dir eine "
        "Push-Benachrichtigung an dein Gerät „iPhone von Philipp“: "
        "„Im Wohnzimmer wurde ein Fenster geöffnet.“ Soll ich das so einrichten?"
    )
    trigger = automation["triggers"][0]
    assert trigger["trigger"] == "state"
    assert sorted(trigger["entity_id"]) == [
        "binary_sensor.wohnzimmer_fenster_links", "binary_sensor.wohnzimmer_fenster_rechts",
    ]
    assert trigger["to"] == "on"  # ANY window becoming open, not all at once
    assert automation["actions"] == [{
        "action": "notify.send_message",
        "target": {"entity_id": [IPHONE]},
        "data": {"message": "Im Wohnzimmer wurde ein Fenster geöffnet.", "title": "HomeIntent"},
    }]


def test_screenshot_c_explicit_message_wins(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    _, automation = _confirmed_single_automation(harness, SCREENSHOT_SENTENCES[2])
    assert automation["actions"][0]["data"]["message"] == "Im Wohnzimmer ist ein Fenster offen."
    assert automation["actions"][0]["target"] == {"entity_id": [IPHONE]}


def test_screenshot_d_generic_window_trigger(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    _, automation = _confirmed_single_automation(harness, SCREENSHOT_SENTENCES[3])
    assert sorted(automation["triggers"][0]["entity_id"]) == [
        "binary_sensor.badezimmerfenster",
        "binary_sensor.wohnzimmer_fenster_links",
        "binary_sensor.wohnzimmer_fenster_rechts",
    ]
    assert automation["actions"][0]["data"]["message"] == "Ein Fenster wurde geöffnet."


def test_generated_area_automation_uses_exact_current_user_target(monkeypatch, tmp_path):
    """Spec §34 - the current user's own bound device, never a broadcast."""
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, IPAD]})
    harness.bind(USER, IPHONE)
    _, automation = _confirmed_single_automation(
        harness, "Benachrichtige mich sobald im Wohnzimmer ein Fenster geöffnet wird"
    )
    assert automation["actions"] == [{
        "action": "notify.send_message",
        "target": {"entity_id": [IPHONE]},
        "data": {"message": "Im Wohnzimmer wurde ein Fenster geöffnet.", "title": "HomeIntent"},
    }]
    # When the window opens, only the stored target gets the push.
    asyncio.run(harness.sink.async_run_automation_actions(automation["actions"]))
    assert [call[1]["entity_id"] for call in harness.sink.notify_calls] == [[IPHONE]]


@pytest.mark.parametrize(("sentence", "message"), (
    ("Benachrichtige mich, wenn ein Fenster geöffnet wird.", "Ein Fenster wurde geöffnet."),
    ("Benachrichtige mich, sobald im Wohnzimmer ein Fenster geöffnet wird.",
     "Im Wohnzimmer wurde ein Fenster geöffnet."),
    ("Kannst du mich benachrichtigen, sobald im Wohnzimmer ein Fenster aufgeht?",
     "Im Wohnzimmer wurde ein Fenster geöffnet."),
    ("Sag mir Bescheid, wenn im Wohnzimmer ein Fenster geöffnet wird.",
     "Im Wohnzimmer wurde ein Fenster geöffnet."),
    ("Sag mir bitte Bescheid sobald ein Fenster offen ist.", "Ein Fenster wurde geöffnet."),
    ("Schick mir eine Nachricht, wenn das Badezimmerfenster aufgeht.",
     "Das Badezimmerfenster wurde geöffnet."),
    ("Wenn das Badezimmerfenster geöffnet wird, benachrichtige mich.",
     "Das Badezimmerfenster wurde geöffnet."),
    ("Sobald das Badezimmerfenster aufgeht, schick mir eine Nachricht.",
     "Das Badezimmerfenster wurde geöffnet."),
    ("Sobald im Wohnzimmer ein Fenster geöffnet wird, schick mir eine Nachricht, "
     "dass dort ein Fenster offen ist.", "Dort ist ein Fenster offen."),
    ("Sag mir Bescheid wenn das Fenster aufgeht", "Ein Fenster wurde geöffnet."),
    ("Sag mir bitte Bescheid sobald das Fenster geöffnet wird", "Ein Fenster wurde geöffnet."),
    ("Wenn ein Fenster geöffnet wird, schick mir eine Push Nachricht",
     "Ein Fenster wurde geöffnet."),
    ("Wenn im Wohnzimmer ein Fenster aufgeht benachrichtige mich",
     "Im Wohnzimmer wurde ein Fenster geöffnet."),
    ("Sobald ein Fenster im Wohnzimmer offen ist schick mir eine Nachricht",
     "Im Wohnzimmer wurde ein Fenster geöffnet."),
    ("Informiere mich sobald ein Fenster geöffnet wird", "Ein Fenster wurde geöffnet."),
    ("Wenn das Fenster aufgeht, benachrichtige mich mit der Nachricht Fenster offen.",
     "Fenster offen"),
    ("Wenn das Fenster aufgeht, schick mir eine Nachricht dass das Fenster offen ist.",
     "Das Fenster ist offen."),
    ("Sobald das Fenster aufgeht, benachrichtige mich: Das Fenster ist offen.",
     "Das Fenster ist offen"),
))
def test_window_notification_family(monkeypatch, tmp_path, sentence, message):
    harness = one_phone(monkeypatch, tmp_path)
    _, automation = _confirmed_single_automation(harness, sentence)
    assert automation["triggers"][0]["trigger"] == "state"
    assert automation["actions"] == [{
        "action": "notify.send_message",
        "target": {"entity_id": [IPHONE]},
        "data": {"message": message, "title": "HomeIntent"},
    }]
    assert harness.sink.notify_calls == []


def test_automation_without_safe_target_is_not_offered(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {})
    speech = harness.say(SCREENSHOT_SENTENCES[3])
    assert speech == NO_TARGET
    assert harness.say("Ja") != AUTOMATION_CREATED_TEXT
    assert harness.automations() == []


def test_other_users_confirmation_cannot_redirect_the_target(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, {CONF_AGENT_NOTIFY_TARGETS: [IPHONE, IPAD]})
    harness.bind(USER, IPHONE)
    harness.bind(OTHER_USER, IPAD)
    harness.say(SCREENSHOT_SENTENCES[3])
    harness.say("Ja", user=OTHER_USER)
    assert harness.automations() == []


# --- the four screenshots are never "not understood" again ------------------------


@pytest.mark.parametrize("sentence", SCREENSHOT_SENTENCES)
def test_screenshot_sentences_are_understood(monkeypatch, tmp_path, sentence):
    harness = one_phone(monkeypatch, tmp_path)
    speech = harness.say(sentence)
    assert speech != NOT_UNDERSTOOD
    assert "nicht eindeutig erkennen" not in speech
    assert speech.endswith("Soll ich das so einrichten?")


# --- query vs notification -------------------------------------------------------


@pytest.mark.parametrize("sentence", (
    "Ist das Fenster offen?",
    "Sag mir ob das Fenster offen ist.",
    "Sag mir, ob das Fenster offen ist.",
    "Welche Fenster sind offen?",
    "Warum ist das Fenster offen?",
    "Das Fenster ist offen.",
    "Ich bekomme keine Benachrichtigungen.",
    "Benachrichtigungen sind aktiviert.",
    "Sag mir, dass das Fenster offen ist.",
))
def test_negative_examples_never_push_or_create(monkeypatch, tmp_path, sentence):
    harness = one_phone(monkeypatch, tmp_path)
    speech = harness.say(sentence)
    assert harness.sink.notify_calls == []
    assert "Soll ich das so einrichten?" not in speech
    harness.say("Ja")
    assert harness.automations() == []
    assert harness.sink.notify_calls == []


def test_open_the_window_is_a_device_command_not_a_notification(monkeypatch, tmp_path):
    harness = one_phone(monkeypatch, tmp_path)
    harness.say("Öffne das Fenster.")
    assert harness.sink.notify_calls == []
    assert harness.automations() == []
