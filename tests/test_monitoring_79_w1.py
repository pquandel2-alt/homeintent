"""7.9 W1: whole-set states - "alle Fenster zu", "kein Licht mehr an".

The meaning: the whole set reaches a state.  Home Assistant gets one state
trigger over every member ("any member reaches the state") and one state
condition with the entity list ("all members are in it") - whichever member
is last.  "kein X mehr an" is the same as "alle X aus": the negation belongs
to the quantifier and inverts the state, it is never a negated command.
Paraphrases are generated from quantifier forms x notification verbs x
clause order; the effect is checked with a model of Home Assistant's
evaluation in both directions.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from _ha_sim import World, entities_of, fires
from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation, house_entities, offers_setup

WINDOWS = sorted({
    "binary_sensor.badezimmerfenster", "binary_sensor.buerofenster",
    "binary_sensor.kinderzimmerfenster", "binary_sensor.kuechenfenster",
    "binary_sensor.schlafzimmerfenster", "binary_sensor.wohnzimmerfenster",
})
UPSTAIRS = sorted({
    "binary_sensor.badezimmerfenster", "binary_sensor.kinderzimmerfenster",
    "binary_sensor.schlafzimmerfenster",
})

_NOTIFY = ("Sag mir Bescheid", "Melde dich", "Benachrichtige mich", "Warne mich", "Gib mir Bescheid")
# Every form means: all windows closed.
_ALL_WINDOWS_CLOSED = (
    "alle Fenster zu sind",
    "alle Fenster geschlossen sind",
    "sämtliche Fenster zu sind",
    "kein Fenster mehr offen ist",
    "kein Fenster offen ist",
    "keine Fenster mehr offen sind",
    "das letzte Fenster zugeht",
    "das letzte Fenster geschlossen wird",
)
_ALL_LIGHTS_OFF = ("alle Lichter aus sind", "kein Licht mehr an ist", "kein Licht an ist")
_ORDERS = {
    "hinten": lambda n, e: f"{n}, wenn {e}.",
    "sobald": lambda n, e: f"{n}, sobald {e}.",
    "vorn": lambda n, e: f"Wenn {e}, {n[0].lower() + n[1:]}.",
}


def _create(monkeypatch, tmp_path, text: str, entities=None) -> tuple[str, dict]:
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(text)
    assert preview.calls == [], text
    assert offers_setup(preview.speech), (text, preview.speech)
    assert house.automations() == [], "nothing is written before the confirmation"
    house.say("Ja.")
    [automation] = house.automations()
    return preview.speech, automation


def _meaning(automation: dict) -> str:
    return json.dumps({
        "triggers": [
            {"entity_id": sorted(entities_of(item)), "to": item.get("to"), "for": item.get("for")}
            for item in automation["triggers"]
        ],
        "conditions": automation.get("conditions", []),
        "target": automation["actions"][0]["target"]["entity_id"],
    }, sort_keys=True)


def _whole_set(automation: dict, members: list[str], state: str) -> None:
    [trigger] = automation["triggers"]
    assert sorted(entities_of(trigger)) == members and trigger["to"] == state
    [condition] = automation["conditions"]
    assert condition == {"condition": "state", "entity_id": members, "state": state}
    assert automation["actions"][0]["target"]["entity_id"] == [PHONES[0]]


_WINDOW_CASES = [
    (order, notify, event) for event in _ALL_WINDOWS_CLOSED for notify in _NOTIFY for order in _ORDERS
]


@pytest.mark.parametrize("order,notify,event", _WINDOW_CASES)
def test_all_windows_closed_has_one_meaning(monkeypatch, tmp_path, order, notify, event):
    text = _ORDERS[order](notify, event)
    speech, automation = _create(monkeypatch, tmp_path, text)
    _whole_set(automation, WINDOWS, "off")
    assert "alle 6 Fenster geschlossen sind" in speech, speech
    assert automation["actions"][0]["data"]["message"] == "Alle 6 Fenster sind geschlossen."


@pytest.mark.parametrize("event", _ALL_LIGHTS_OFF)
@pytest.mark.parametrize("notify", _NOTIFY)
def test_no_light_on_any_more_is_all_lights_off(monkeypatch, tmp_path, notify, event):
    _, automation = _create(monkeypatch, tmp_path, f"{notify}, sobald {event}.")
    lights = sorted(e.entity_id for e in house_entities() if e.domain == "light")
    _whole_set(automation, lights, "off")


@pytest.mark.parametrize("event", [
    "alle Rollläden unten sind", "alle Rollläden geschlossen sind", "kein Rollladen mehr offen ist",
])
def test_all_covers_down(monkeypatch, tmp_path, event):
    speech, automation = _create(monkeypatch, tmp_path, f"Benachrichtige mich, wenn {event}.")
    [trigger] = automation["triggers"]
    covers = sorted(entities_of(trigger))
    assert covers and all(item.startswith("cover.") for item in covers)
    assert "cover.garagentor" not in covers and "cover.markise" not in covers
    _whole_set(automation, covers, "closed")
    assert f"alle {len(covers)} Rollläden geschlossen sind" in speech


@pytest.mark.parametrize("text", [
    "Sag mir Bescheid, wenn im Obergeschoss alle Fenster geschlossen sind.",
    "Sag mir Bescheid, wenn alle Fenster im Obergeschoss zu sind.",
    "Sag mir Bescheid, wenn oben kein Fenster mehr offen ist.",
])
def test_the_set_keeps_its_place(monkeypatch, tmp_path, text):
    speech, automation = _create(monkeypatch, tmp_path, text)
    _whole_set(automation, UPSTAIRS, "off")
    assert "alle 3 Fenster im Obergeschoss" in speech


def test_home_assistant_fires_when_the_last_member_closes(monkeypatch, tmp_path):
    _, automation = _create(monkeypatch, tmp_path, "Sag mir Bescheid, wenn alle Fenster zu sind.")
    closed = {window: "off" for window in WINDOWS}
    kitchen = "binary_sensor.kuechenfenster"
    # The kitchen window was the last one open.
    assert fires(automation, World({**closed}), kitchen, "on")
    # Another window closes while the kitchen window is still open.
    assert not fires(automation, World({**closed, kitchen: "on"}), "binary_sensor.buerofenster", "on")
    # Opening a window never fires.
    assert not fires(automation, World({**closed, kitchen: "on"}), kitchen, "off")


def test_a_moment_and_a_state_are_the_same_situation(monkeypatch, tmp_path):
    meanings = {
        _meaning(_create(monkeypatch, tmp_path / str(index), text)[1])
        for index, text in enumerate((
            "Sag mir Bescheid, sobald das letzte Fenster zugeht.",
            "Sag mir Bescheid, wenn alle Fenster zu sind.",
            "Sag mir Bescheid, wenn kein Fenster mehr offen ist.",
        ))
    }
    assert len(meanings) == 1


def test_everybody_away_and_a_window_open_is_the_783_situation(monkeypatch, tmp_path):
    reference = _meaning(_create(
        monkeypatch, tmp_path / "a",
        "Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist.",
    )[1])
    for index, text in enumerate((
        "Sag mir Bescheid, wenn alle weg sind und noch ein Fenster offen ist.",
        "Wenn alle aus dem Haus sind und ein Fenster offen ist, sag mir Bescheid.",
        "Melde dich, wenn wir alle weg sind und noch ein Fenster offen ist.",
    )):
        automation = _create(monkeypatch, tmp_path / str(index), text)[1]
        assert json.loads(_meaning(automation))["triggers"] == json.loads(reference)["triggers"], text
        assert json.loads(_meaning(automation))["conditions"] == json.loads(reference)["conditions"], text


@pytest.mark.parametrize("text", [
    "Sag mir Bescheid, wenn niemand zuhause ist.",
    "Sag mir Bescheid, wenn alle weg sind.",
    "Melde dich, sobald keiner mehr daheim ist.",
    "Gib mir Bescheid, wenn alle das Haus verlassen haben.",
])
def test_nobody_home_alone_is_the_last_person_leaving(monkeypatch, tmp_path, text):
    speech, automation = _create(monkeypatch, tmp_path, text)
    assert [(item["entity_id"], item["from"]) for item in automation["triggers"]] == [
        ("person.anna", "home"), ("person.lena", "home"), ("person.philipp", "home"),
    ]
    [nobody] = automation["conditions"]
    assert nobody["condition"] == "not"
    assert "keiner von Anna, Lena und Philipp zuhause ist" in speech
    assert "egal was davon" not in speech
    world = World({"person.anna": "not_home", "person.lena": "unknown", "person.philipp": "not_home"})
    assert fires(automation, world, "person.philipp", "home")
    world.states["person.anna"] = "home"
    assert not fires(automation, world, "person.philipp", "home")


def test_a_whole_set_joined_with_nobody_home(monkeypatch, tmp_path):
    speech, automation = _create(
        monkeypatch, tmp_path, "Sag mir Bescheid, wenn niemand zuhause ist und alle Fenster zu sind."
    )
    assert "egal was davon zuletzt eintritt" in speech
    window_trigger, *leaves = automation["triggers"]
    assert sorted(entities_of(window_trigger)) == WINDOWS and len(leaves) == 3
    whole, nobody = automation["conditions"]
    assert whole == {"condition": "state", "entity_id": WINDOWS, "state": "off"}
    closed = {window: "off" for window in WINDOWS}
    away = {"person.anna": "not_home", "person.lena": "unknown", "person.philipp": "not_home"}
    # The last person leaves with every window closed.
    assert fires(automation, World({**closed, **away}), "person.anna", "home")
    # ... but not with one window open.
    assert not fires(automation, World({**closed, **away, WINDOWS[0]: "on"}), "person.anna", "home")


def test_a_large_set_is_counted_in_the_preview(monkeypatch, tmp_path):
    base = house_entities()
    lamp = next(entity for entity in base if entity.entity_id == "light.stehlampe")
    many = [
        replace(lamp, entity_id=f"light.lampe_{index}", friendly_name=f"Lampe {index}", aliases=(),
                attributes={**lamp.attributes, "friendly_name": f"Lampe {index}"})
        for index in range(40)
    ]
    entities = [*base, *many]
    total = sum(1 for entity in entities if entity.domain == "light")
    assert total > 50
    speech, _ = _create(monkeypatch, tmp_path, "Sag mir Bescheid, wenn kein Licht mehr an ist.", entities)
    assert f"alle {total} Lichter aus sind" in speech


# --- honest answers ------------------------------------------------------------------


def _refused(monkeypatch, tmp_path, text: str, entities=None) -> str:
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(text)
    assert turn.calls == [], text
    assert not offers_setup(turn.speech), (text, turn.speech)
    house.say("Ja.")
    assert house.automations() == [], text
    return turn.speech


@pytest.mark.parametrize("text", [
    # A whole set for a duration or above a value: no single HA trigger means it.
    "Sag mir Bescheid, wenn alle Fenster seit 10 Minuten zu sind.",
    "Sag mir Bescheid, wenn alle Temperaturen über 20 Grad sind.",
])
def test_whole_sets_without_a_trigger_are_honest(monkeypatch, tmp_path, text):
    assert "Gesamtzustand" in _refused(monkeypatch, tmp_path, text)


def test_an_empty_set_asks(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Sag mir Bescheid, wenn im Keller alle Fenster zu sind.")
    assert "Fenster" in speech and "?" in speech


def test_window_contacts_and_drives_are_not_merged(monkeypatch, tmp_path):
    entities = house_entities()
    cover = next(entity for entity in entities if entity.entity_id == "cover.kuechenrollladen")
    drive = replace(
        cover, entity_id="cover.dachfenster", friendly_name="Dachfenster", device_class="window",
        area_id=None, area_name=None, aliases=(),
        attributes={**cover.attributes, "friendly_name": "Dachfenster", "device_class": "window"},
    )
    speech = _refused(monkeypatch, tmp_path, "Sag mir Bescheid, wenn alle Fenster zu sind.", [*entities, drive])
    assert "Sensoren" in speech and "Antriebe" in speech


def test_a_whole_set_question_stays_a_question(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Sind alle Fenster zu?")
    assert "einrichten" not in turn.speech and turn.calls == []
    assert house.automations() == []


def test_a_negated_command_is_still_refused(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Schalte kein Licht an.")
    assert turn.calls == []
