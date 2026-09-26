"""Paraphrase invariance (spec §30/§40/§75/§90).

Every paraphrase of "notify me when the Büro cover reaches 50 %" must produce
the *identical* semantic model - trigger, measurement, comparator, value,
target, recipient and derived message - not merely "some" automation.
The same canonical test runs for every room (no area-specific logic).
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_world import WORLD  # noqa: E402
from homeintent.engine import AutomationMatchResult, NluEngine  # noqa: E402
from homeintent.nlu.action_model import ActionModel, ActionType, NotificationRecipientKind  # noqa: E402
from homeintent.nlu.automation_model import NumericComparator, TriggerType  # noqa: E402
from homeintent.nlu.ha_automation_generator import _resolve_target_entities  # noqa: E402
from homeintent.nlu.measurement import MeasurementProperty  # noqa: E402

ENGINE = NluEngine()
ENTITIES = list(WORLD)

PARAPHRASES = (
    "Schicke mir eine Benachrichtigung wenn im Büro die Rolllade 50% erreicht hat",
    "Schick mir eine Benachrichtigung wenn die Rolllade im Büro 50 % erreicht.",
    "Benachrichtige mich sobald die Rolllade im Büro bei 50 Prozent steht.",
    "Wenn der Rollladen im Büro 50 Prozent erreicht hat sag mir Bescheid.",
    "Wenn im Büro der Rollo halb geöffnet ist schick mir eine Nachricht.",
    "Ich möchte benachrichtigt werden sobald die Büro-Rolllade 50 % erreicht.",
    "Kannst du mir bitte Bescheid sagen wenn der Rollladen im Büro auf 50 Prozent steht?",
    "Benachrichtige mich, wenn die Rolllade im Büro 50 Prozent erreicht.",
    "Benachrichtige mich, wenn die Rolllade im Büro 50 Prozent erreicht hat.",
    "Schick mir eine Nachricht, sobald die Rolllade im Büro bei 50 Prozent ist.",
    "Wenn die Rolllade im Büro 50 Prozent erreicht, benachrichtige mich.",
    "Sobald die Rolllade im Büro auf 50 Prozent steht, sag mir Bescheid.",
    "Sag mir Bescheid, wenn die Rolllade im Büro halb geöffnet ist.",
    "Ich möchte eine Nachricht bekommen, wenn die Rolllade im Büro bei 50 % ist.",
    "Informiere mich, sobald der Rollladen im Büro 50 Prozent erreicht.",
    "Wenn im Büro der Rollladen 50 % erreicht hat, schick mir eine Push-Nachricht.",
    "Wenn im Büro die Rolllade bei 50 Prozent steht, melde dich bei mir.",
    "Benachrichtige mich wenn der Rollladen im Büro 50 Prozent erreicht.",
    "Wenn im Büro die Rolllade auf 50 % steht, schick mir eine Nachricht.",
    "Sag mir Bescheid sobald der Rollladen im Büro halb offen ist.",
    "Ich möchte informiert werden wenn die Büro-Rolllade 50 Prozent erreicht.",
    "Kannst du mir eine Push-Nachricht schicken sobald die Rolllade im Büro bei 50 Prozent ist?",
    "Gib mir Bescheid, sobald der Büro Rollladen 50 Prozent erreicht.",
    "Wenn der Rollladen im Büro fünfzig Prozent erreicht hat, informiere mich.",
    "sobald das rollo im büro auf fünfzig prozent steht sag mir bescheid",
    "Benachrichtige mich jedes Mal wenn der Büro-Rollladen 50 % erreicht",
    "Immer wenn die Rollade im Büro 50 Prozent erreicht, will ich eine Meldung bekommen.",
    "Schick mir eine Mitteilung sobald der Rolladen im Büro halb offen ist",
    "Sag mir bitte Bescheid wenn die Büro Rolllade zur Hälfte geöffnet ist",
    "Wenn im Büro der Rollladen halb geöffnet ist, dann schick mir eine Push-Benachrichtigung.",
    "Ich möchte informiert werden, sobald der Rollladen im Büro bei 50 % liegt.",
    "Kannst du mir eine Nachricht aufs Handy schicken wenn der Rollladen im Büro 50 Prozent erreicht hat?",
    "Wenn der Rollo im Büro bei 50 % ist, benachrichtige mich per Push.",
    "Schick mir auf mein iPhone eine Benachrichtigung sobald die Rolllade im Büro 50 Prozent erreicht.",
    "Also wenn die Rolllade im Büro irgendwann 50 Prozent erreicht, kannst du mir dann Bescheid sagen?",
    "Falls die Rolllade im Büro 50 Prozent erreicht, schick mir eine Nachricht.",
    "Sofern der Rollladen im Büro 50 Prozent erreicht, informiere mich.",
    "Benachrichtige mich sofern das Rollo im Büro 50 Prozent erreicht.",
    "Wenn der Rollladen im Büro 50 Prozent erreicht, dann benachrichtige mich bitte.",
    "Rolllade Büro bei 50 Prozent, dann Nachricht an mich.",
    "Benachrichtige mich, wenn die Jalousie im Büro 50 Prozent erreicht.",
    "Meld dich bei mir wenn der Büro Rollladen bei 50 Prozent ist",
    "Ich hätte gerne eine Nachricht sobald die Rolllade im Büro 50 Prozent erreicht",
    "Schick mir als Push-Nachricht Bescheid, wenn der Rollladen im Büro 50 Prozent erreicht.",
    "benachrichtige mich wenn die roll lade im büro fünfzig prozent erreicht",
    "sag mir bescheid wenn der rollladen im büro fünf zig prozent erreicht",
    "Benachrichtige mich wenn die Rolllade im Büro 60, äh 50 Prozent erreicht.",
    "Benachrichtige mich wenn die Rolllade im Büro 60 nein 50 Prozent erreicht",
    "Wenn die Rolllade im Büro auf halber Höhe ist, gib mir Bescheid.",
    "Wenn das Büro Rollo bei 50 Prozent angekommen ist, schick mir eine Push.",
    "Informier mich, wenn der Rolladen im Büro bei 50 % steht.",
    "Wenn im Büro das Rollo zur Hälfte offen ist, sag mir Bescheid.",
    "Könntest du mich benachrichtigen, sobald die Rolllade im Büro 50 Prozent erreicht hat?",
    "Wenn die Rolllade im Buero 50 Prozent erreicht benachrichtige mich",
    "ÄH WENN DER ROLLLADEN IM BÜRO 50 PROZENT ERREICHT SAG MIR BESCHEID",
)


def _canonical(sentence: str):
    result = ENGINE.match_automation(sentence, ENTITIES)
    assert isinstance(result, AutomationMatchResult), f"{sentence!r} -> {result!r}"
    assert result.validation_error is None
    return replace(result.model, source_text="")


REFERENCE = _canonical(PARAPHRASES[0])


def test_there_are_at_least_fifty_paraphrases():
    assert len(set(PARAPHRASES)) >= 50


def test_reference_meaning_is_the_intended_one():
    [trigger] = REFERENCE.triggers
    assert trigger.type is TriggerType.NUMERIC_STATE
    assert trigger.measurement is MeasurementProperty.COVER_POSITION
    assert trigger.comparator is NumericComparator.EQUAL
    assert trigger.threshold == 50
    assert [e.entity_id for e in _resolve_target_entities(trigger.target, ENTITIES)] == ["cover.buero_rollladen"]
    [action] = REFERENCE.actions
    assert isinstance(action, ActionModel) and action.type is ActionType.NOTIFY
    assert action.recipient is not None and action.recipient.kind is NotificationRecipientKind.CURRENT_USER
    assert action.message == "Der Rollladen im Büro hat 50 % erreicht."
    assert REFERENCE.conditions == ()


@pytest.mark.parametrize("sentence", PARAPHRASES)
def test_paraphrase_has_exactly_the_reference_meaning(sentence):
    assert _canonical(sentence) == REFERENCE


ROOMS = (
    ("Wohnzimmer", "im Wohnzimmer", "cover.wohnzimmer_rollladen"),
    ("Küche", "in der Küche", "cover.kueche_rollladen"),
    ("Schlafzimmer", "im Schlafzimmer", "cover.schlafzimmer_rollladen"),
    ("Bad", "im Bad", "cover.bad_rollladen"),
    ("Büro", "im Büro", "cover.buero_rollladen"),
)
TEMPLATES = (
    "Schicke mir eine Benachrichtigung wenn {loc} die Rolllade 50% erreicht hat",
    "Benachrichtige mich, sobald der Rollladen {loc} bei 50 Prozent steht.",
    "Wenn {loc} das Rollo halb offen ist, sag mir Bescheid.",
    "Ich möchte informiert werden, wenn die Rolllade {loc} 50 % erreicht.",
)


@pytest.mark.parametrize(("area", "loc", "entity_id"), ROOMS)
def test_every_room_canonicalizes_the_same_way(area, loc, entity_id):
    models = [_canonical(template.format(loc=loc)) for template in TEMPLATES]
    assert all(model == models[0] for model in models)
    model = models[0]
    [trigger] = model.triggers
    assert [e.entity_id for e in _resolve_target_entities(trigger.target, ENTITIES)] == [entity_id]
    assert trigger.measurement is MeasurementProperty.COVER_POSITION and trigger.threshold == 50
    assert area in model.actions[0].message
