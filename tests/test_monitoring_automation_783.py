"""7.8.3: monitoring requests become notification automations.

"Überwache X und melde dich, wenn es ...", "Beobachte die Fenster und warne
mich, wenn eins ...", "Achte darauf, ob|dass ..." - monitoring verbs, an
implied or explicit NOTIFY, an anaphor bound to the monitored object, a
duration modifier and trailing conditions.  Every draft still goes through
the validator, the spoken preview and an explicit "Ja" before anything is
written; ambiguous or unknown references ask instead of guessing.
"""

from __future__ import annotations

import pytest

from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation, house_entities, offers_setup

PHILIPP = PHONES[0]
WINDOWS = {
    "binary_sensor.badezimmerfenster", "binary_sensor.buerofenster",
    "binary_sensor.kinderzimmerfenster", "binary_sensor.kuechenfenster",
    "binary_sensor.schlafzimmerfenster", "binary_sensor.wohnzimmerfenster",
}
NOBODY_HOME = {
    "condition": "not",
    "conditions": [{
        "condition": "or",
        "conditions": [
            {"condition": "state", "entity_id": f"person.{name}", "state": "home"}
            for name in ("anna", "lena", "philipp")
        ],
    }],
}
NIGHT = {"condition": "or", "conditions": [
    {"condition": "time", "after": "22:00:00"}, {"condition": "time", "before": "06:00:00"},
]}


def _create(monkeypatch, tmp_path, text: str, entities=None) -> dict:
    house = HouseConversation(
        monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS
    )
    preview = house.say(text)
    assert preview.calls == [], text
    assert offers_setup(preview.speech), (text, preview.speech)
    assert house.automations() == [], "nothing is written before the confirmation"
    house.say("Ja.")
    [automation] = house.automations()
    return automation


def _entities(trigger: dict) -> set[str]:
    entity = trigger["entity_id"]
    return {entity} if isinstance(entity, str) else set(entity)


def _notifies_speaker(automation: dict) -> None:
    [action] = automation["actions"]
    assert action["action"] == "notify.send_message"
    assert action["target"]["entity_id"] == [PHILIPP]


# (sentence, trigger entities, to-state, for seconds, conditions)
_REGRESSION = [
    ("Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten offen ist.",
     {"cover.garagentor"}, "open", 600, []),
    ("Beobachte das Garagentor und sag mir Bescheid, wenn es 5 Minuten offen steht.",
     {"cover.garagentor"}, "open", 300, []),
    ("Achte darauf, ob das Garagentor länger als 15 Minuten geöffnet bleibt.",
     {"cover.garagentor"}, "open", 900, []),
    ("Warne mich, wenn das Garagentor länger als 10 Minuten offen ist.",
     {"cover.garagentor"}, "open", 600, []),
    ("Benachrichtige mich, sobald das Garagentor geöffnet wird.",
     {"cover.garagentor"}, "open", None, []),
    ("Informiere mich, wenn ein Fenster seit mehr als 20 Minuten offen ist.",
     WINDOWS, "on", 1200, []),
    ("Behalte die Haustür im Auge und melde dich, wenn sie nachts geöffnet wird.",
     {"binary_sensor.haustuer"}, "on", None, [NIGHT]),
]


@pytest.mark.parametrize("text,entities,state,seconds,conditions", _REGRESSION)
def test_monitoring_and_notification_requests(
    monkeypatch, tmp_path, text, entities, state, seconds, conditions
):
    automation = _create(monkeypatch, tmp_path, text)
    [trigger] = automation["triggers"]
    assert trigger["trigger"] == "state"
    assert _entities(trigger) == entities, text
    assert trigger["to"] == state
    assert trigger.get("for") == ({"seconds": seconds} if seconds else None), text
    assert automation.get("conditions", []) == conditions, text
    _notifies_speaker(automation)


# --- a window open while nobody is home: both orders -------------------------

_WINDOW_AND_AWAY = (
    "Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.",
    "Sag mir Bescheid, wenn irgendein Fenster offen ist und keiner zuhause ist.",
    "Achte darauf, dass kein Fenster offen bleibt, wenn niemand zuhause ist.",
    "Warne mich, wenn die Fenster offen sind und niemand zuhause ist.",
)
PEOPLE = ("person.anna", "person.lena", "person.philipp")


@pytest.mark.parametrize("text", _WINDOW_AND_AWAY)
def test_window_open_and_nobody_home_holds_in_both_orders(monkeypatch, tmp_path, text):
    automation = _create(monkeypatch, tmp_path, text)
    window_trigger, *leave_triggers = automation["triggers"]
    assert _entities(window_trigger) == WINDOWS and window_trigger["to"] == "on"
    assert [(item["entity_id"], item["from"]) for item in leave_triggers] == [
        (person, "home") for person in PEOPLE
    ]
    any_window, nobody = automation["conditions"]
    assert any_window["condition"] == "or"
    assert {leaf["entity_id"] for leaf in any_window["conditions"]} == WINDOWS
    assert nobody == NOBODY_HOME
    assert automation["actions"][0]["data"]["message"] == "Ein Fenster ist offen. Niemand ist zuhause."
    _notifies_speaker(automation)


def _fires(automation: dict, states: dict[str, str], changed: str, before: str) -> bool:
    """Minimal Home Assistant semantics for the generated state triggers and
    state/or/not conditions: does the change of ``changed`` from ``before``
    to its value in ``states`` run the actions?"""
    def triggered(trigger: dict) -> bool:
        if changed not in _entities(trigger):
            return False
        if "to" in trigger and states[changed] != trigger["to"]:
            return False
        if "from" in trigger and before != trigger["from"]:
            return False
        return states[changed] != before

    def holds(condition: dict) -> bool:
        kind = condition["condition"]
        if kind == "state":
            return all(states[entity] == condition["state"] for entity in _entities(condition))
        if kind == "or":
            return any(holds(child) for child in condition["conditions"])
        if kind == "not":
            return not any(holds(child) for child in condition["conditions"])
        raise AssertionError(kind)

    return any(triggered(item) for item in automation["triggers"]) and all(
        holds(item) for item in automation.get("conditions", [])
    )


def test_home_assistant_runs_it_whichever_part_begins_last(monkeypatch, tmp_path):
    automation = _create(monkeypatch, tmp_path, _WINDOW_AND_AWAY[0])
    closed = {window: "off" for window in WINDOWS}
    home = {person: "home" for person in PEOPLE}
    away = {person: "not_home" for person in PEOPLE}
    kitchen = "binary_sensor.kuechenfenster"
    # Everybody leaves while the kitchen window is still open.
    last_one_leaves = {**closed, kitchen: "on", **away}
    assert _fires(automation, last_one_leaves, "person.lena", "home")
    # A window opens while the house is empty.
    assert _fires(automation, {**closed, kitchen: "on", **away}, kitchen, "off")
    # Not while somebody is still home, and not with every window closed.
    assert not _fires(automation, {**closed, kitchen: "on", **home, "person.lena": "not_home"},
                      "person.lena", "home")
    assert not _fires(automation, {**closed, **away}, "person.lena", "home")


def test_a_moment_stays_a_moment(monkeypatch, tmp_path):
    automation = _create(
        monkeypatch, tmp_path,
        "Benachrichtige mich, wenn ein Fenster geöffnet wird und niemand zuhause ist.",
    )
    [trigger] = automation["triggers"]
    assert _entities(trigger) == WINDOWS
    assert automation["conditions"] == [NOBODY_HOME]


@pytest.mark.parametrize("nobody", ["keiner", "niemand"])
def test_a_duration_is_not_completed(monkeypatch, tmp_path, nobody):
    # Both words: the routing rule (7.8.3 Teil B) gives "niemand" the same
    # reading as "keiner" - the V10 monitor goal no longer claims it first.
    automation = _create(
        monkeypatch, tmp_path,
        f"Informiere mich, wenn ein Fenster seit 20 Minuten offen ist und {nobody} zuhause ist.",
    )
    [trigger] = automation["triggers"]
    assert trigger["for"] == {"seconds": 1200}
    assert automation["conditions"] == [NOBODY_HOME]


def test_two_device_states_hold_in_both_orders(monkeypatch, tmp_path):
    automation = _create(
        monkeypatch, tmp_path,
        "Melde dich, wenn das Garagentor offen ist und die Stehlampe an ist.",
    )
    assert [(item["entity_id"], item["to"]) for item in automation["triggers"]] == [
        ("cover.garagentor", "open"), ("light.stehlampe", "on"),
    ]
    assert automation["conditions"] == [
        {"condition": "state", "entity_id": "cover.garagentor", "state": "open"},
        {"condition": "state", "entity_id": "light.stehlampe", "state": "on"},
    ]


def test_preview_speaks_the_situation_not_the_trigger_list(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(_WINDOW_AND_AWAY[0])
    # Geänderte Erwartung (7.8.3 Teil B): "niemand zuhause" wird an genau eine
    # Personenmenge gebunden (presence_scope). Ohne bestätigten Haushalt sind
    # das alle person.*-Entitäten, und die Vorschau nennt sie, damit das "Ja"
    # wissentlich gegeben wird. Die Bedeutung ist dieselbe, nur ausdrücklich.
    assert preview.speech.startswith(
        "Sobald ein Fenster offen ist und keiner von Anna, Lena und Philipp zuhause ist, "
        "egal was davon zuletzt eintritt,"
    )
    assert "person." not in preview.speech and "binary_sensor" not in preview.speech


# --- constructions, not sentences --------------------------------------------

_HEADS = (
    "Überwache das Garagentor und {n}, wenn es {e}.",
    "Beobachte das Garagentor und {n}, wenn es {e}.",
    "Behalte das Garagentor im Auge und {n}, sobald es {e}.",
    "Behalte das Garagentor im Blick und {n}, falls es {e}.",
    "Hab ein Auge auf das Garagentor und {n}, wenn es {e}.",
    "Achte auf das Garagentor und {n}, wenn es {e}.",
    "Kannst du das Garagentor überwachen und {n}, wenn es {e}?",
    "Überwache das Garagentor und wenn es {e}, {n}.",
)
_NOTIFY = (
    "melde dich", "sag mir Bescheid", "gib mir Bescheid", "informiere mich",
    "benachrichtige mich", "warne mich",
)
_DURATIONS = {
    "länger als 10 Minuten offen ist": 600,
    "10 Minuten offen ist": 600,
    "seit 10 Minuten offen ist": 600,
    "mehr als 10 Minuten geöffnet ist": 600,
    "seit mehr als 10 Minuten offen ist": 600,
    "eine halbe Stunde offen bleibt": 1800,
}


# The modal frame takes the infinitive forms of the same notification verbs.
_MODAL_NOTIFY = ("mir Bescheid sagen", "mich benachrichtigen", "mich informieren")
_COMBINATIONS = [
    (head, notify)
    for head in _HEADS
    for notify in (_MODAL_NOTIFY if head.startswith("Kannst") else _NOTIFY)
]


@pytest.mark.parametrize("head,notify", _COMBINATIONS)
def test_monitoring_head_times_notification_verb(monkeypatch, tmp_path, head, notify):
    text = head.format(n=notify, e="länger als 10 Minuten offen ist")
    automation = _create(monkeypatch, tmp_path, text)
    [trigger] = automation["triggers"]
    assert _entities(trigger) == {"cover.garagentor"} and trigger["to"] == "open", text
    assert trigger["for"] == {"seconds": 600}, text
    _notifies_speaker(automation)


@pytest.mark.parametrize("event,seconds", list(_DURATIONS.items()))
def test_duration_state_forms(monkeypatch, tmp_path, event, seconds):
    automation = _create(
        monkeypatch, tmp_path, f"Überwache das Garagentor und melde dich, wenn es {event}."
    )
    [trigger] = automation["triggers"]
    assert trigger["for"] == {"seconds": seconds}, event


@pytest.mark.parametrize("member", ["eins", "eines", "eines davon", "irgendeins davon", "welches"])
def test_member_reference_is_the_monitored_set(monkeypatch, tmp_path, member):
    automation = _create(
        monkeypatch, tmp_path,
        f"Beobachte die Fenster und warne mich, wenn {member} offen ist.",
    )
    [trigger] = automation["triggers"]
    assert _entities(trigger) == WINDOWS, member


def test_member_reference_keeps_the_monitored_place(monkeypatch, tmp_path):
    automation = _create(
        monkeypatch, tmp_path,
        "Beobachte die Fenster im Obergeschoss und melde dich, wenn eins offen ist.",
    )
    [trigger] = automation["triggers"]
    assert _entities(trigger) == {
        "binary_sensor.badezimmerfenster", "binary_sensor.kinderzimmerfenster",
        "binary_sensor.schlafzimmerfenster",
    }


def test_prohibition_violation_with_duration(monkeypatch, tmp_path):
    automation = _create(
        monkeypatch, tmp_path,
        "Achte darauf, dass das Garagentor nicht länger als 10 Minuten offen bleibt.",
    )
    [trigger] = automation["triggers"]
    assert _entities(trigger) == {"cover.garagentor"} and trigger["for"] == {"seconds": 600}


def test_explicit_event_subject_is_kept(monkeypatch, tmp_path):
    automation = _create(
        monkeypatch, tmp_path,
        "Beobachte das Garagentor und warne mich, wenn die Haustür offen ist.",
    )
    [trigger] = automation["triggers"]
    assert _entities(trigger) == {"binary_sensor.haustuer"}


def test_monitoring_with_device_action_resolves_the_pronoun(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(
        "Beobachte das Küchenfenster und schalte das Küchenlicht aus, wenn es geöffnet wird."
    )
    assert preview.calls == []
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert _entities(trigger) == {"binary_sensor.kuechenfenster"}


def test_anna_is_notified_on_her_own_phone(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user="anna")
    house.say("Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten offen ist.")
    house.say("Ja.")
    [automation] = house.automations()
    [action] = automation["actions"]
    assert action["target"]["entity_id"] == [PHONES[1]]


def test_monitoring_and_plain_form_share_one_canonical_meaning():
    from homeintent.automation_composition import compose_event_automation
    from homeintent.engine import NluEngine

    entities = house_entities()
    engine = NluEngine()
    readers = engine._composition_readers(entities, None, None)
    canonical = {
        compose_event_automation(text, entities, readers).canonical
        for text in (
            "Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten offen ist.",
            "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.",
            "Achte darauf, ob das Garagentor länger als 10 Minuten offen ist.",
            "Behalte das Garagentor im Auge und benachrichtige mich, wenn es seit 10 Minuten offen ist.",
        )
    }
    assert len(canonical) == 1
    [meaning] = canonical
    assert meaning.event.entity_ids == ("cover.garagentor",)
    assert meaning.event.for_seconds == 600


# --- negative and ambiguity cases --------------------------------------------


def _refused(monkeypatch, tmp_path, text: str, entities=None) -> str:
    house = HouseConversation(
        monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS
    )
    turn = house.say(text)
    assert turn.calls == [], text
    assert not offers_setup(turn.speech), (text, turn.speech)
    house.say("Ja.")
    assert house.automations() == [], text
    return turn.speech


def test_pronoun_disagreeing_with_the_monitored_object_asks(monkeypatch, tmp_path):
    speech = _refused(
        monkeypatch, tmp_path, "Überwache das Garagentor und melde dich, wenn er offen ist."
    )
    assert "„er“" in speech


@pytest.mark.parametrize("text", [
    "Melde dich, wenn es offen ist.",
    "Warne mich, wenn eins offen ist.",
])
def test_pronoun_without_antecedent_asks(monkeypatch, tmp_path, text):
    assert "Welches Gerät" in _refused(monkeypatch, tmp_path, text)


def test_unknown_monitored_object_is_named(monkeypatch, tmp_path):
    speech = _refused(
        monkeypatch, tmp_path, "Überwache den Blumentopf und melde dich, wenn er offen ist."
    )
    assert "Blumentopf" in speech


@pytest.mark.parametrize("text", [
    "Prüfe, ob das Garagentor offen ist.",
    "Kontrolliere, ob ein Fenster offen ist.",
    "Überwache das Garagentor.",
    "Achte darauf, dass das Garagentor zu ist.",
    "Überwache die Haustür und schließ sie ab, wenn sie offen ist.",
    "Pass auf, dass keiner die Haustür öffnet.",
])
def test_no_automation_without_a_readable_event_and_notification(monkeypatch, tmp_path, text):
    _refused(monkeypatch, tmp_path, text)


def test_one_off_check_stays_a_query(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Ist das Garagentor offen?")
    assert "Garagentor" in turn.speech and "einrichten" not in turn.speech
    assert house.automations() == []


def _with_window_drive() -> list:
    from dataclasses import replace

    entities = house_entities()
    cover = next(entity for entity in entities if entity.entity_id == "cover.kuechenrollladen")
    drive = replace(
        cover,
        entity_id="cover.dachfenster",
        friendly_name="Dachfenster",
        device_class="window",
        area_id=None, area_name=None, aliases=(),
        attributes={**cover.attributes, "friendly_name": "Dachfenster", "device_class": "window"},
    )
    return [*entities, drive]


@pytest.mark.parametrize("text", [
    "Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.",
    "Informiere mich, wenn ein Fenster seit mehr als 20 Minuten offen ist.",
])
def test_window_contacts_and_window_drives_are_not_merged(monkeypatch, tmp_path, text):
    speech = _refused(monkeypatch, tmp_path, text, entities=_with_window_drive())
    assert "Sensoren" in speech and "Antriebe" in speech and "Dachfenster" in speech
