"""Clause order and connectors (spec §6-§8, §24, §54, §78, §79)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_language_helpers import meaning  # noqa: E402

WINDOW = "state(binary_sensor.buero_fenster)=open => notify(me)"


@pytest.mark.parametrize("sentence", (
    "Benachrichtige mich, wenn das Bürofenster aufgeht.",
    "Benachrichtige mich wenn das Bürofenster aufgeht",
    "Wenn das Bürofenster aufgeht, benachrichtige mich.",
    "Wenn das Bürofenster aufgeht benachrichtige mich",
    "Wenn das Bürofenster aufgeht, dann benachrichtige mich.",
    "Wenn das Bürofenster aufgeht dann schick mir bitte eine Nachricht",
    "Schick mir eine Nachricht, sobald das Bürofenster aufgeht.",
    "Sobald das Bürofenster aufgeht, schick mir eine Nachricht.",
    "Sag mir Bescheid, falls das Bürofenster aufgeht.",
    "Falls das Bürofenster aufgeht, sag mir Bescheid.",
    "Informiere mich, sofern das Bürofenster aufgeht.",
    "Sofern das Bürofenster aufgeht, informiere mich.",
    "Benachrichtige mich immer wenn das Bürofenster aufgeht.",
    "Jedes Mal wenn das Bürofenster aufgeht, sag mir Bescheid.",
    "Immer wenn das Bürofenster aufgeht, schick mir eine Nachricht.",
    "Ich möchte benachrichtigt werden, wenn das Bürofenster aufgeht.",
    "Ich will eine Nachricht bekommen, sobald das Bürofenster aufgeht.",
    "Kannst du mir eine Nachricht schicken wenn das Bürofenster aufgeht?",
    "Kannst du mich benachrichtigen sobald das Bürofenster aufgeht?",
    "Ich hätte gerne eine Nachricht sobald das Bürofenster aufgeht.",
    "Meld dich bei mir wenn das Bürofenster aufgeht.",
    "BENACHRICHTIGE MICH WENN DAS BÜROFENSTER AUFGEHT",
    "benachrichtige mich wenn das bürofenster aufgeht",
    "Wenn im Büro das Fenster aufgeht, benachrichtige mich.",
    "Wenn das Fenster im Büro aufgeht, benachrichtige mich.",
))
def test_both_orders_and_all_connectors_mean_the_same(sentence):
    assert meaning(sentence) == WINDOW


@pytest.mark.parametrize("sentence", (
    "Wenn das Bürofenster aufgeht und niemand zuhause ist, benachrichtige mich.",
    "Benachrichtige mich wenn das Bürofenster aufgeht und niemand zuhause ist.",
    "Benachrichtige mich, wenn das Bürofenster aufgeht, aber nur wenn niemand zuhause ist.",
    "Sag mir Bescheid, wenn das Bürofenster aufgeht und keiner daheim ist.",
))
def test_trigger_condition_action_in_any_order(sentence):
    assert meaning(sentence) == "state(binary_sensor.buero_fenster)=open ; if: nobody_home => notify(me)"


def test_two_actions_are_both_kept():
    assert meaning("Wenn die Haustür aufgeht, mach das Flurlicht an und schick mir eine Nachricht.") == (
        "state(binary_sensor.haustuer)=open => turn_on(light.flur) + notify(me)"
    )


def test_alternative_triggers_stay_two_triggers():
    assert meaning(
        "Wenn das Bürofenster aufgeht oder wenn die Haustür aufgeht, benachrichtige mich."
    ) == "state(binary_sensor.buero_fenster)=open | state(binary_sensor.haustuer)=open => notify(me)"


def test_a_spoken_time_condition_is_never_dropped():
    assert meaning("Wenn die Haustür nach 21 Uhr aufgeht, mach das Flurlicht an.") == (
        "state(binary_sensor.haustuer)=open ; if: time>21:00 => turn_on(light.flur)"
    )
