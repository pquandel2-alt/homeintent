"""Release 7.3.0: one notification meaning for immediate and event pushes.

Generated combinations, not a sentence list: every notification head ×
recipient × content marker must reach exactly the right phone with exactly
the dictated text, and every event genus × place × connector × word order
must produce one automation that notifies only the speaker's phone.  All
sentences are built here; the live holdouts in ``sim/`` are not reused.
"""

from __future__ import annotations

import itertools

import pytest

from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation

PHILIPP, ANNA = PHONES

# --- immediate push with content ------------------------------------------

_HEADS = {
    # (head with {r} for the dative/accusative recipient, content joiner)
    "schick": ("Schick {r} aufs Handy", ": "),
    "schreib": ("Schreib {r}", ", dass "),
    "bescheid": ("Sag {r} Bescheid", ", dass "),
    "nachricht": ("Schicke {r} eine Nachricht", ": "),
    "push_an": ("Push an {a}", ": "),
}
_RECIPIENTS = {
    # dative form, "an" form, expected phone
    "self": ("mir", "mich", PHILIPP),
    "anna": ("Anna", "Anna", ANNA),
}
_CONTENT = {
    ": ": ("Der Kuchen ist im Ofen", "Der Kuchen ist im Ofen"),
    ", dass ": ("der Kuchen im Ofen ist", "Der Kuchen ist im Ofen."),
}


@pytest.mark.parametrize(
    "head,recipient",
    list(itertools.product(_HEADS, _RECIPIENTS)),
)
def test_immediate_push_with_content_reaches_exactly_one_phone(
    monkeypatch, tmp_path, head, recipient
):
    template, joiner = _HEADS[head]
    dative, accusative, phone = _RECIPIENTS[recipient]
    spoken, expected = _CONTENT[joiner]
    text = template.format(r=dative, a=accusative) + joiner + spoken + "."
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(text)
    notify = [
        (data["entity_id"], data["message"]) for domain, _, data in turn.calls if domain == "notify"
    ]
    assert notify == [([phone], expected)], text
    assert house.automations() == []


def test_person_without_phone_is_named_and_nothing_is_sent(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Schreib Lena, dass das Essen fertig ist.")
    assert turn.calls == []
    assert "Lena" in turn.speech and "nichts gesendet" in turn.speech


# --- event notifications ----------------------------------------------------

_EVENTS = {
    # event clause -> expected trigger entity ids (subset check)
    "garage": ("das Garagentor aufgeht", {"cover.garagentor"}),
    "water": ("der Wassermelder Alarm schlägt", {"binary_sensor.wassermelder_keller"}),
    "terrace": ("jemand die Terrassentür öffnet", {"binary_sensor.terrassentuer"}),
    "upstairs": (
        "im Obergeschoss irgendein Fenster geöffnet wird",
        {"binary_sensor.badezimmerfenster", "binary_sensor.kinderzimmerfenster",
         "binary_sensor.schlafzimmerfenster"},
    ),
    "cellar_temp": ("die Temperatur im Keller unter 12 Grad fällt", {"sensor.temperatur_keller"}),
    "washer": ("die Waschmaschine fertig ist", {"sensor.waschmaschine_status"}),
}
_FRAMES = {
    "notify_first": "Benachrichtige mich, {c} {e}.",
    "notify_first_no_comma": "Gib mir Bescheid {c} {e}",
    "event_first": "{C} {e}, sag mir Bescheid.",
    "push_first": "Push an mich, {c} {e}.",
    "wish": "Ich will eine Nachricht aufs Handy, {c} {e}.",
}


def _trigger_entities(automation: dict) -> set[str]:
    found: set[str] = set()
    for trigger in automation.get("triggers") or automation.get("trigger") or []:
        entity = trigger.get("entity_id")
        if isinstance(entity, str):
            found.add(entity)
        elif isinstance(entity, list):
            found.update(entity)
        template = trigger.get("value_template", "")
        found.update(
            part.split("'")[0]
            for part in template.split("states('")[1:]
        )
    return found


@pytest.mark.parametrize(
    "event,frame,connector",
    [
        (event, frame, connector)
        for event, frame in itertools.product(_EVENTS, _FRAMES)
        for connector in ("wenn", "sobald")
    ],
)
def test_event_notification_matrix(monkeypatch, tmp_path, event, frame, connector):
    clause, expected = _EVENTS[event]
    text = _FRAMES[frame].format(c=connector, C=connector.capitalize(), e=clause)
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(text)
    assert preview.calls == [], text
    assert "Soll ich das so einrichten" in preview.speech, (text, preview.speech)
    house.say("Ja.")
    [automation] = house.automations()
    assert _trigger_entities(automation) == expected, (text, automation)
    actions = automation.get("actions") or automation.get("action")
    targets = [action["target"]["entity_id"] for action in actions if action.get("action") == "notify.send_message"]
    assert targets == [[PHILIPP]], (text, actions)


def test_event_notification_for_anna_goes_to_annas_phone(monkeypatch, tmp_path):
    house = HouseConversation(
        monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user="anna"
    )
    house.say("Sag mir Bescheid, sobald das Garagentor aufgeht.")
    house.say("Ja.")
    [automation] = house.automations()
    actions = automation.get("actions") or automation.get("action")
    assert actions[0]["target"]["entity_id"] == [ANNA]


def test_dictated_text_is_not_a_device(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(
        "Benachrichtige mich, sobald das Badezimmerfenster geöffnet wird, dass die Heizung aus soll."
    )
    assert preview.calls == []
    house.say("Ja.")
    [automation] = house.automations()
    assert _trigger_entities(automation) == {"binary_sensor.badezimmerfenster"}
    actions = automation.get("actions") or automation.get("action")
    assert all(action.get("action") == "notify.send_message" for action in actions)


def test_duration_is_kept_in_preview_and_trigger(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say("Sag mir Bescheid, wenn die Haustür länger als 5 Minuten offen ist.")
    assert "5 Minuten" in preview.speech
    house.say("Ja.")
    [automation] = house.automations()
    triggers = automation.get("triggers") or automation.get("trigger")
    assert triggers[0]["for"] == {"seconds": 300}


def test_negated_notification_creates_nothing(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Benachrichtige mich nicht, wenn das Garagentor aufgeht.")
    house.say("Ja.")
    assert turn.calls == []
    assert house.automations() == []
