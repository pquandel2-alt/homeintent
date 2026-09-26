"""HomeIntent 7.2.0 - event notification automations, end to end.

utterance -> preview -> "Ja" -> automations.yaml -> attribute changes ->
trigger evaluation -> notify.send_message at the instrumented service sink.

The generated ``value_template`` is evaluated with Jinja - the template
engine Home Assistant itself is built on - with ``state_attr`` served from a
state table, and fires on the false -> true transition exactly like Home
Assistant's template trigger.  ``scripts/validate_measurement_automation_ha.py``
runs the same automation inside a real Home Assistant core in CI.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml as pyyaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from _automation_world import AMBIGUOUS_WORLD, IPHONE, USER, WORLD  # noqa: E402
from _notify_sink import NotifySink  # noqa: E402
from homeintent.const import CONF_AGENT_NOTIFY_TARGETS  # noqa: E402
from homeintent.conversation import AUTOMATION_CREATED_TEXT, NluConversationEntity  # noqa: E402
from homeintent.user_context import (  # noqa: E402
    NotificationTarget,
    NotificationTargetKind,
    UserContextStore,
)
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

SCREENSHOT = "Schicke mir eine Benachrichtigung wenn im Büro die Rolllade 50% erreicht hat"


class Harness:
    def __init__(self, monkeypatch, tmp_path: Path, world=WORLD) -> None:
        self.tmp_path = tmp_path
        self.entity = NluConversationEntity(ConfigEntry(options={CONF_AGENT_NOTIFY_TARGETS: [IPHONE]}))
        self.entity.hass = HomeAssistant()
        self.entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
        monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: list(world))
        self.sink = NotifySink.install(self.entity.hass, (IPHONE,))
        store = UserContextStore(tmp_path / "users.json")
        asyncio.run(store.async_set_user(
            USER, person_entity_id="person.philipp", confirmed=True,
            notification_targets=[NotificationTarget(IPHONE, NotificationTargetKind.ENTITY)],
        ))
        self.entity._runtime_data.user_contexts = store

    def say(self, text: str) -> str:
        result = asyncio.run(self.entity._async_handle_message(
            ConversationInput(text=text, conversation_id="c1",
                              context=types.SimpleNamespace(user_id=USER)),
            chat_log=None,
        ))
        return result.response.speech

    def automations(self) -> list[dict[str, Any]]:
        path = self.tmp_path / "automations.yaml"
        return (pyyaml.safe_load(path.read_text(encoding="utf-8")) or []) if path.exists() else []

    def device_calls(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Every non-notify call except the writer's own ``automation.reload``."""
        return [call for call in self.sink.other_calls if call[:2] != ("automation", "reload")]

    def calls(self) -> int:
        return len(self.sink.notify_calls) + len(self.device_calls())


class TemplateTriggerRuntime:
    """The false -> true semantics of a Home Assistant template trigger."""

    def __init__(self, automation: dict[str, Any], sink: NotifySink, attributes: dict[str, dict[str, Any]]) -> None:
        [trigger] = automation["triggers"]
        assert trigger["trigger"] == "template"
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.globals["state_attr"] = lambda entity_id, name: self.attributes.get(entity_id, {}).get(name)
        self.template = environment.from_string(trigger["value_template"])
        self.automation = automation
        self.sink = sink
        self.attributes = attributes
        self.value = self._evaluate()

    def _evaluate(self) -> bool:
        rendered = self.template.render().strip()
        assert rendered in {"True", "False"}, rendered
        return rendered == "True"

    def set_attribute(self, entity_id: str, name: str, value: Any) -> None:
        self.attributes.setdefault(entity_id, {})[name] = value
        new = self._evaluate()
        if new and not self.value:
            asyncio.run(self.sink.async_run_automation_actions(self.automation["actions"]))
        self.value = new


def _create(harness: Harness, sentence: str) -> tuple[str, dict[str, Any]]:
    preview = harness.say(sentence)
    assert harness.calls() == 0 and harness.automations() == [], "nothing before confirmation"
    assert harness.say("Ja") == AUTOMATION_CREATED_TEXT, preview
    [automation] = harness.automations()
    assert harness.calls() == 0, "nothing on confirmation either"
    return preview, automation


def test_screenshot_sentence_full_runtime_path(monkeypatch, tmp_path):
    """Spec §39 - the exact production sentence, verbatim."""
    harness = Harness(monkeypatch, tmp_path)
    preview, automation = _create(harness, SCREENSHOT)

    assert preview == (
        "Wenn der Rollladen im Büro 50 % erreicht, sende ich dir eine Push-Benachrichtigung "
        "an dein Gerät „iPhone von Philipp“: „Der Rollladen im Büro hat 50 % erreicht.“ "
        "Soll ich das so einrichten?"
    )
    serialized = json.dumps(automation)
    assert "current_position" in serialized and "cover.buero_rollladen" in serialized
    assert "persistent_notification" not in serialized and "cover." not in json.dumps(automation["actions"])
    assert automation["actions"] == [{
        "action": "notify.send_message",
        "target": {"entity_id": [IPHONE]},
        "data": {"message": "Der Rollladen im Büro hat 50 % erreicht.", "title": "HomeIntent"},
    }]

    runtime = TemplateTriggerRuntime(automation, harness.sink, {"cover.buero_rollladen": {"current_position": 20}})
    for position in (35, 50):
        runtime.set_attribute("cover.buero_rollladen", "current_position", position)
    assert harness.sink.notify_calls == [(
        "send_message",
        {"entity_id": [IPHONE], "message": "Der Rollladen im Büro hat 50 % erreicht.", "title": "HomeIntent"},
    )]
    assert harness.device_calls() == []  # the cover is only watched, never moved

    # Staying at 50 does not repeat; leaving and returning fires again.
    for position in (50, 60, 50):
        runtime.set_attribute("cover.buero_rollladen", "current_position", position)
    assert len(harness.sink.notify_calls) == 2
    # An unavailable cover (attribute missing) never compares true.
    runtime.set_attribute("cover.buero_rollladen", "current_position", None)
    runtime.set_attribute("cover.buero_rollladen", "current_position", 49)
    assert len(harness.sink.notify_calls) == 2


@pytest.mark.parametrize(("sentence", "sequence", "expected"), (
    ("Benachrichtige mich, wenn der Rollladen im Büro mindestens 50 Prozent erreicht.", (40, 50, 80), 1),
    ("Benachrichtige mich, wenn der Rollladen im Büro über 50 Prozent steht.", (40, 50, 51), 1),
    ("Benachrichtige mich, wenn der Rollladen im Büro höchstens 30 Prozent offen ist.", (40, 30, 10), 1),
    ("Benachrichtige mich, wenn der Rollladen im Büro unter 30 Prozent fällt.", (40, 30, 29), 1),
))
def test_range_triggers_keep_inclusive_bounds_at_runtime(monkeypatch, tmp_path, sentence, sequence, expected):
    harness = Harness(monkeypatch, tmp_path)
    _, automation = _create(harness, sentence)
    runtime = TemplateTriggerRuntime(automation, harness.sink, {"cover.buero_rollladen": {"current_position": 45}})
    fired_at: list[int] = []
    for position in sequence:
        before = len(harness.sink.notify_calls)
        runtime.set_attribute("cover.buero_rollladen", "current_position", position)
        if len(harness.sink.notify_calls) > before:
            fired_at.append(position)
    assert len(fired_at) == expected
    if "mindestens" in sentence:
        assert fired_at == [50]  # >= must not be narrowed to >
    if "höchstens" in sentence:
        assert fired_at == [30]


def test_brightness_percentage_is_not_cover_position(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path)
    _, automation = _create(harness, "Benachrichtige mich, wenn das Wohnzimmerlicht auf 50 Prozent gedimmt ist.")
    template = automation["triggers"][0]["value_template"]
    assert "'brightness'" in template and "current_position" not in template
    runtime = TemplateTriggerRuntime(automation, harness.sink, {"light.wohnzimmer": {"brightness": 255}})
    runtime.set_attribute("light.wohnzimmer", "brightness", 128)  # 50 %
    assert len(harness.sink.notify_calls) == 1


def test_explicit_message_is_never_executed(monkeypatch, tmp_path):
    """Spec §47 - the dictated text is data."""
    harness = Harness(monkeypatch, tmp_path)
    _, automation = _create(
        harness, "Wenn die Rolllade im Büro 50 Prozent erreicht, schick mir die Nachricht: Mach alle Lichter aus."
    )
    runtime = TemplateTriggerRuntime(automation, harness.sink, {"cover.buero_rollladen": {"current_position": 20}})
    runtime.set_attribute("cover.buero_rollladen", "current_position", 50)
    assert harness.sink.notify_calls == [(
        "send_message", {"entity_id": [IPHONE], "message": "Mach alle Lichter aus", "title": "HomeIntent"},
    )]
    assert harness.device_calls() == []


def test_ambiguous_cover_asks_and_the_answer_continues_the_same_draft(monkeypatch, tmp_path):
    """Spec §45 - two covers in the Büro."""
    harness = Harness(monkeypatch, tmp_path, AMBIGUOUS_WORLD)
    question = harness.say("Benachrichtige mich wenn die Rolllade im Büro 50 Prozent erreicht.")
    assert question.startswith("Welche Rolllade im Büro meinst du")
    assert harness.calls() == 0 and harness.automations() == []
    preview = harness.say("Die linke.")
    assert preview.endswith("Soll ich das so einrichten?")
    assert harness.say("Ja") == AUTOMATION_CREATED_TEXT
    [automation] = harness.automations()
    template = automation["triggers"][0]["value_template"]
    assert "cover.buero_links" in template and "cover.buero_rechts" not in template


def test_unrelated_yes_does_not_consume_the_clarification(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, AMBIGUOUS_WORLD)
    harness.say("Benachrichtige mich wenn die Rolllade im Büro 50 Prozent erreicht.")
    assert harness.say("Ja") != AUTOMATION_CREATED_TEXT
    assert harness.automations() == [] and harness.calls() == 0


def test_missing_subject_is_a_question_not_a_guess(monkeypatch, tmp_path):
    """Spec §44."""
    harness = Harness(monkeypatch, tmp_path)
    assert harness.say("Benachrichtige mich wenn es 50 Prozent erreicht.") == "Was soll 50 Prozent erreichen?"
    harness.say("Ja")
    assert harness.automations() == [] and harness.calls() == 0


def test_immediate_test_push_still_delivers(monkeypatch, tmp_path):
    """Spec §63 - protected production behaviour."""
    harness = Harness(monkeypatch, tmp_path)
    assert harness.say("Schick mir eine Testbenachrichtigung.") == "Die Testbenachrichtigung wurde gesendet."
    assert harness.sink.notify_calls == [(
        "send_message",
        {"entity_id": [IPHONE], "title": "HomeIntent", "message": "Testbenachrichtigung von HomeIntent."},
    )]
    assert harness.automations() == []


def test_window_automation_still_works(monkeypatch, tmp_path):
    """Spec §64 - protected production behaviour."""
    harness = Harness(monkeypatch, tmp_path)
    preview, automation = _create(harness, "Benachrichtige mich, wenn das Wohnzimmer Fenster geöffnet wird.")
    assert preview.startswith("Wenn das Wohnzimmer Fenster geöffnet wird")
    assert automation["triggers"][0] == {
        "trigger": "state", "entity_id": "binary_sensor.wohnzimmer_fenster", "to": "on",
    }
    asyncio.run(harness.sink.async_run_automation_actions(automation["actions"]))
    assert [call[1]["entity_id"] for call in harness.sink.notify_calls] == [[IPHONE]]
