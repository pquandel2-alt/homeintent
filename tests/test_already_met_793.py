"""7.9.3 A1: a monitoring whose condition is already true when it is set up.

Matrix: monitor kind × none / some / all entities already met × reply
(Ja / Nein / Abbrechen).  "Ja" creates the monitoring and sends exactly one
message about the already met devices (to the notify targets of the
created automation), "Nein" only creates, "Abbrechen" does nothing.  The
HA evaluation model (``tests/_ha_sim.py``) shows that a device reaching the
condition *later* sends exactly one message, per device.
"""

from __future__ import annotations

import pytest

import _ha_stub
from _ha_sim import World, fires, run
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities, with_states

_ha_stub.install()

BATTERIES = ("sensor.batterie_bewegungsmelder_flur", "sensor.batterie_fenster_bad",
             "sensor.batterie_fenster_kueche", "sensor.batterie_rauchmelder_oben")
WINDOWS = ("binary_sensor.badezimmerfenster", "binary_sensor.buerofenster", "binary_sensor.kinderzimmerfenster",
           "binary_sensor.kuechenfenster", "binary_sensor.schlafzimmerfenster", "binary_sensor.wohnzimmerfenster")
PEOPLE = ("person.anna", "person.lena", "person.philipp")


def _states(**pairs: str) -> dict[str, str]:
    return {key.replace(".", "__", 1): value for key, value in pairs.items()}


def _set(ids, value, rest=None):
    states = {entity_id: value for entity_id in ids}
    for entity_id in rest or ():
        states.setdefault(entity_id, "")
    return states


# kind -> (sentence, {level: (states, met entity ids)})
KINDS = {
    "battery": (
        "Gib mir Bescheid, sobald irgendeine Batterie unter 25 Prozent fällt.",
        {
            "none": ({e: "80" for e in BATTERIES}, ()),
            "some": ({**{e: "80" for e in BATTERIES}, BATTERIES[1]: "14", BATTERIES[3]: "9"},
                     (BATTERIES[1], BATTERIES[3])),
            "all": ({e: "10" for e in BATTERIES}, BATTERIES),
        },
    ),
    "window": (
        "Sag mir Bescheid, wenn ein Fenster offen ist.",
        {
            "none": ({e: "off" for e in WINDOWS}, ()),
            "some": ({**{e: "off" for e in WINDOWS}, WINDOWS[0]: "on", WINDOWS[3]: "on"}, (WINDOWS[0], WINDOWS[3])),
            "all": ({e: "on" for e in WINDOWS}, WINDOWS),
        },
    ),
    "power": (
        "Benachrichtige mich, wenn die Waschmaschine mehr als 1000 Watt verbraucht.",
        {
            "none": ({"sensor.leistung_waschmaschine": "3"}, ()),
            "all": ({"sensor.leistung_waschmaschine": "1840"}, ("sensor.leistung_waschmaschine",)),
        },
    ),
    "unavailable": (
        "Melde dich, wenn ein Gerät nicht mehr erreichbar ist.",
        {
            "none": ({}, ()),
            "some": ({"light.stehlampe": "unavailable", "binary_sensor.bewegung_flur": "unavailable"},
                     ("binary_sensor.bewegung_flur", "light.stehlampe")),
        },
    ),
    "combination": (
        "Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist.",
        {
            "none": ({**{e: "on" for e in WINDOWS[:2]}, **{p: "home" for p in PEOPLE}}, ()),
            "some": ({**{e: "off" for e in WINDOWS}, WINDOWS[3]: "on", **{p: "not_home" for p in PEOPLE}},
                     (WINDOWS[3],)),
        },
    ),
    "total": (
        "Sag mir Bescheid, wenn alle Fenster geschlossen sind.",
        {
            "none": ({**{e: "off" for e in WINDOWS}, WINDOWS[2]: "on"}, ()),
            "all": ({e: "off" for e in WINDOWS}, WINDOWS),
        },
    ),
}
CASES = [(kind, level) for kind, (_, levels) in KINDS.items() for level in levels]
REPLIES = ("Ja.", "Nein.", "Abbrechen.")


def _house(monkeypatch, tmp_path, states):
    entities = with_states(house_entities(), **_states(**states))
    return HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)


def _names(ids):
    by_id = {entity.entity_id: entity.friendly_name for entity in house_entities()}
    return [by_id[entity_id] for entity_id in ids]


@pytest.mark.parametrize("reply", REPLIES)
@pytest.mark.parametrize("kind,level", CASES)
def test_matrix(monkeypatch, tmp_path, kind, level, reply):
    sentence, levels = KINDS[kind]
    states, met = levels[level]
    house = _house(monkeypatch, tmp_path, states)
    preview = house.say(sentence).speech
    assert house.automations() == []
    if not met:
        assert preview.endswith("Soll ich das so einrichten?"), preview
        assert "schon" not in preview.split("„")[0]
        turn = house.say(reply)
        created = reply == "Ja."
        assert bool(house.automations()) is created, (reply, turn.speech)
        assert not [call for call in turn.calls if call[0] == "notify"]
        return
    named = sorted(_names(met), key=lambda name: name.casefold().replace("ü", "ue"))
    if len(named) > 4:  # a whole group is named short in speech
        for name in named[:3]:
            assert name in preview, (name, preview)
        assert f"und {len(named) - 3} weitere" in preview, preview
    else:
        for name in named:
            assert name in preview, (name, preview)
    assert "Soll ich dir das jetzt gleich schicken?" in preview
    assert "„Nein“ (nur einrichten)" in preview and "„Abbrechen“" in preview
    turn = house.say(reply)
    pushes = [call for call in turn.calls if call[0] == "notify"]
    if reply == "Abbrechen.":
        assert house.automations() == [] and pushes == [], turn
        return
    assert len(house.automations()) == 1, turn.speech
    if reply == "Nein.":
        assert pushes == [] and turn.speech == "Automation wurde erstellt."
        return
    [(_, service, data)] = pushes
    assert service == "send_message"
    assert data["entity_id"] == ["notify.handy_philipp_nachricht"]
    assert data["message"].startswith("Schon beim Einrichten der Überwachung erfüllt: ")
    for name in _names(met):
        assert name in data["message"]
    others = set(house.automations()[0]["triggers"][0]["entity_id"]) - set(met) if isinstance(
        house.automations()[0]["triggers"][0]["entity_id"], list) else set()
    for name in _names(sorted(others)):
        assert f"{name} (" not in data["message"], (name, data["message"])
    assert "habe ich dir gerade geschickt" in turn.speech


@pytest.mark.parametrize("text", ["Ja", "ja bitte", "Ja, schick.", "Nein", "nur einrichten", "Nein, nur anlegen.",
                                  "Abbrechen", "Vergiss es", "lieber nicht", "vielleicht"])
def test_reply_vocabulary(text):
    from homeintent.already_met import MetReply, classify_met_reply

    expected = {
        "Ja": MetReply.SEND, "ja bitte": MetReply.SEND, "Ja, schick.": MetReply.SEND,
        "Nein": MetReply.CREATE, "nur einrichten": MetReply.CREATE, "Nein, nur anlegen.": MetReply.CREATE,
        "Abbrechen": MetReply.CANCEL, "Vergiss es": MetReply.CANCEL, "lieber nicht": MetReply.CANCEL,
        "vielleicht": None,
    }[text]
    assert classify_met_reply(text) == expected


def test_unclear_reply_asks_again_and_keeps_the_question(monkeypatch, tmp_path):
    sentence, levels = KINDS["battery"]
    house = _house(monkeypatch, tmp_path, levels["some"][0])
    house.say(sentence)
    again = house.say("vielleicht")
    assert "„Nein“ (nur einrichten)" in again.speech and house.automations() == []
    assert len([c for c in house.say("Ja.").calls if c[0] == "notify"]) == 1


def test_a_later_device_reports_exactly_once_per_device(monkeypatch, tmp_path):
    """HA evaluation: after "Ja" a battery that falls later sends one
    message about itself; the already low ones do not repeat."""
    sentence, levels = KINDS["battery"]
    states, _ = levels["some"]
    house = _house(monkeypatch, tmp_path, states)
    house.say(sentence)
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["mode"] == "queued"  # two at once are both reported
    names = dict(zip(BATTERIES, _names(BATTERIES)))
    world = World(dict(states), names=names)
    sent = []
    for entity_id, value in ((BATTERIES[1], "13"), (BATTERIES[3], "8"), (BATTERIES[2], "20"), (BATTERIES[2], "19"),
                             (BATTERIES[0], "24")):
        before = world.states[entity_id]
        world.states[entity_id] = value
        if fires(automation, world, entity_id, before):
            sent += run(automation["actions"], world, trigger_entity=entity_id)
    assert [item.message for item in sent] == [
        f"{names[BATTERIES[2]]}: 20 %", f"{names[BATTERIES[0]]}: 24 %",
    ]


def test_a_later_window_reports_once_per_window(monkeypatch, tmp_path):
    sentence, levels = KINDS["window"]
    states, _ = levels["some"]
    house = _house(monkeypatch, tmp_path, states)
    house.say(sentence)
    house.say("Nein.")
    [automation] = house.automations()
    world = World(dict(states))
    count = 0
    for entity_id in (WINDOWS[1], WINDOWS[2]):
        before = world.states[entity_id]
        world.states[entity_id] = "on"
        count += fires(automation, world, entity_id, before)
    assert count == 2


@pytest.mark.parametrize("states,met", [
    ({"person.anna": "home"}, False), ({"person.anna": "not_home"}, True),
])
def test_combination_counts_only_when_every_condition_holds(monkeypatch, tmp_path, states, met):
    windows = {**{e: "off" for e in WINDOWS}, WINDOWS[3]: "on"}
    others = {"person.lena": "not_home", "person.philipp": "not_home"}
    house = _house(monkeypatch, tmp_path, {**windows, **others, **states})
    preview = house.say(KINDS["combination"][0]).speech
    assert ("Soll ich dir das jetzt gleich schicken?" in preview) is met, preview


def test_states_changed_before_yes_send_nothing(monkeypatch, tmp_path):
    """The check is repeated with the confirming turn's states."""
    sentence, levels = KINDS["power"]
    house = _house(monkeypatch, tmp_path, levels["all"][0])
    house.say(sentence)
    house.entities = with_states(house.entities, sensor__leistung_waschmaschine="2")
    turn = house.say("Ja.")
    assert len(house.automations()) == 1
    assert not [c for c in turn.calls if c[0] == "notify"]
    assert "ich habe nichts geschickt" in turn.speech


def test_event_sensors_and_transitions_are_never_already_met():
    from homeintent.already_met import already_met

    entities = with_states(house_entities(), binary_sensor__bewegung_flur="on", person__anna="not_home")
    motion = {"triggers": [{"trigger": "state", "entity_id": ["binary_sensor.bewegung_flur"], "to": "on"}],
              "actions": [{"action": "notify.send_message", "target": {"entity_id": ["notify.x"]}}]}
    assert already_met(motion, entities) is None
    leaving = {"triggers": [{"trigger": "state", "entity_id": "person.anna", "from": "home"}],
               "actions": [{"action": "notify.send_message", "target": {"entity_id": ["notify.x"]}}]}
    assert already_met(leaving, entities) is None


def test_device_actions_and_unknown_conditions_are_not_offered():
    from homeintent.already_met import already_met

    entities = with_states(house_entities(), binary_sensor__kuechenfenster="on")
    trigger = [{"trigger": "state", "entity_id": "binary_sensor.kuechenfenster", "to": "on"}]
    switching = {"triggers": trigger, "actions": [{"action": "climate.turn_off",
                                                  "target": {"entity_id": "climate.heizung_kueche"}}]}
    assert already_met(switching, entities) is None
    templated = {"triggers": trigger, "conditions": [{"condition": "template", "value_template": "{{ true }}"}],
                 "actions": [{"action": "notify.send_message", "target": {"entity_id": ["notify.x"]}}]}
    assert already_met(templated, entities) is None
