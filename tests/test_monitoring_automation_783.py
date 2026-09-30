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

from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation, house_entities

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
    assert "Soll ich das so einrichten" in preview.speech, (text, preview.speech)
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
    ("Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.",
     WINDOWS, "on", None, [NOBODY_HOME]),
    ("Sag mir Bescheid, wenn irgendein Fenster offen ist und keiner zuhause ist.",
     WINDOWS, "on", None, [NOBODY_HOME]),
    ("Achte darauf, dass kein Fenster offen bleibt, wenn niemand zuhause ist.",
     WINDOWS, "on", None, [NOBODY_HOME]),
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
    assert "Soll ich das so einrichten" not in turn.speech, (text, turn.speech)
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
