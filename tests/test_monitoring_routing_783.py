"""7.8.3 Teil B: one meaning for monitoring requests, whatever the wording.

The sentence-based event reader (``compose_event_automation``) is the single
source of a monitoring request's meaning; the V10 monitor goal keeps only
sentences that reader cannot read (``_event_reading_claims`` in
``conversation.py``).  "niemand zuhause" quantifies over one set of people
for both paths (``presence_scope``): the confirmed household, else every
``person.*`` - named in the preview so the "Ja" is given knowingly.

Paraphrases are generated from building blocks (notification verb x absence
word x sentence form x object); every combination must produce the same Home
Assistant automation: the same trigger entities, conditions and recipient.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation, house_entities

PEOPLE = ("person.anna", "person.lena", "person.philipp")

# --- building blocks -----------------------------------------------------------

# (imperative after a leading event clause, form after a leading imperative)
_NOTIFY = (
    "sag mir Bescheid", "gib mir Bescheid", "melde dich", "informiere mich",
    "benachrichtige mich", "warne mich",
)
_ABSENT = ("niemand zuhause ist", "keiner zuhause ist", "niemand mehr zuhause ist")
# object -> (plain state clause, monitored noun phrase, anaphoric state clause)
_OBJECTS = {
    "Fenster": ("ein Fenster offen ist", "die Fenster", "eins offen ist"),
    "Garagentor": ("das Garagentor offen ist", "das Garagentor", "es offen ist"),
    "Licht": ("ein Licht an ist", "die Lichter", "eins an ist"),
}
_FORMS = {
    "wenn_hinten": lambda n, o, a: f"{n.capitalize()}, wenn {o[0]} und {a}.",
    "wenn_vorn": lambda n, o, a: f"Wenn {o[0]} und {a}, {n}.",
    "bedingung_vorn": lambda n, o, a: f"{n.capitalize()}, wenn {a} und {o[0]}.",
    "ueberwachungsverb": lambda n, o, a: f"Überwache {o[1]} und {n}, wenn {o[2]} und {a}.",
    "beobachtungsverb_wenn_vorn": lambda n, o, a: f"Beobachte {o[1]} und wenn {o[2]} und {a}, {n}.",
}


def _meaning(automation: dict) -> str:
    """Trigger entities, conditions and recipient - order-independent."""
    triggers = sorted(
        json.dumps({
            "entity_id": sorted([item["entity_id"]] if isinstance(item["entity_id"], str) else item["entity_id"]),
            "to": item.get("to"), "from": item.get("from"), "for": item.get("for"),
        }, sort_keys=True)
        for item in automation["triggers"]
    )

    def canonical(condition):
        if isinstance(condition, dict):
            if "conditions" in condition:
                children = sorted(json.dumps(canonical(c), sort_keys=True) for c in condition["conditions"])
                return {"condition": condition["condition"], "conditions": children}
            return {key: canonical(value) for key, value in condition.items()}
        if isinstance(condition, list):
            return sorted(json.dumps(canonical(c), sort_keys=True) for c in condition)
        return condition

    conditions = sorted(json.dumps(canonical(c), sort_keys=True) for c in automation.get("conditions", []))
    recipients = sorted(
        tuple(action["target"]["entity_id"]) for action in automation["actions"]
    )
    return json.dumps([triggers, conditions, recipients], sort_keys=True)


def _create(monkeypatch, tmp_path, text: str, household: tuple[str, ...] = ()) -> tuple[str, dict]:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    if household:
        asyncio.run(house.entity._runtime_data.user_contexts.async_set_household(list(household), confirmed=True))
    preview = house.say(text)
    assert preview.calls == [], text
    assert "Soll ich das so einrichten" in preview.speech, (text, preview.speech)
    assert house.automations() == [], "nothing is written before the confirmation"
    house.say("Ja.")
    [automation] = house.automations()
    return preview.speech, automation


_CASES = [
    (form, notify, absent, obj)
    for obj in _OBJECTS
    for form in _FORMS
    for notify in _NOTIFY
    for absent in _ABSENT
]


@pytest.fixture(scope="module")
def _reference_meanings(tmp_path_factory):
    references: dict[str, str] = {}
    mp = pytest.MonkeyPatch()
    try:
        for obj, parts in _OBJECTS.items():
            text = _FORMS["wenn_hinten"]("sag mir Bescheid", parts, "niemand zuhause ist")
            _, automation = _create(mp, tmp_path_factory.mktemp(obj), text)
            references[obj] = _meaning(automation)
    finally:
        mp.undo()
    return references


@pytest.mark.parametrize("form,notify,absent,obj", _CASES)
def test_every_paraphrase_has_the_same_meaning(
    monkeypatch, tmp_path, _reference_meanings, form, notify, absent, obj
):
    text = _FORMS[form](notify, _OBJECTS[obj], absent)
    _, automation = _create(monkeypatch, tmp_path, text)
    assert _meaning(automation) == _reference_meanings[obj], text


@pytest.mark.parametrize("obj", list(_OBJECTS))
def test_the_meaning_holds_whichever_part_begins_last(monkeypatch, tmp_path, obj):
    """Every person leaving is a trigger, the object's own state is one too."""
    text = _FORMS["wenn_hinten"]("sag mir Bescheid", _OBJECTS[obj], "niemand zuhause ist")
    _, automation = _create(monkeypatch, tmp_path, text)
    leaves = [item["entity_id"] for item in automation["triggers"] if item.get("from") == "home"]
    assert leaves == list(PEOPLE), text
    states = [item for item in automation["triggers"] if "to" in item]
    assert len(states) == 1, text
    assert automation["actions"][0]["target"]["entity_id"] == [PHONES[0]]


# --- the same people for both paths -------------------------------------------------


def test_without_household_the_preview_names_every_person(monkeypatch, tmp_path):
    speech, automation = _create(
        monkeypatch, tmp_path, "Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist."
    )
    assert "keiner von Anna, Lena und Philipp zuhause ist" in speech
    [nobody] = [c for c in automation["conditions"] if c["condition"] == "not"]
    assert [leaf["entity_id"] for leaf in nobody["conditions"][0]["conditions"]] == list(PEOPLE)


def test_a_confirmed_household_is_exactly_the_people(monkeypatch, tmp_path):
    speech, automation = _create(
        monkeypatch, tmp_path,
        "Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist.",
        household=("person.anna", "person.philipp"),
    )
    assert "keiner von Anna und Philipp zuhause ist" in speech
    leaves = [item["entity_id"] for item in automation["triggers"] if item.get("from") == "home"]
    assert leaves == ["person.anna", "person.philipp"]
    [nobody] = [c for c in automation["conditions"] if c["condition"] == "not"]
    assert [leaf["entity_id"] for leaf in nobody["conditions"][0]["conditions"]] == [
        "person.anna", "person.philipp",
    ]


def test_a_moment_with_nobody_home_uses_the_household_too(monkeypatch, tmp_path):
    _, automation = _create(
        monkeypatch, tmp_path,
        "Benachrichtige mich, wenn ein Fenster geöffnet wird und niemand zuhause ist.",
        household=("person.lena",),
    )
    assert automation["conditions"] == [{
        "condition": "not",
        "conditions": [{"condition": "or", "conditions": [
            {"condition": "state", "entity_id": "person.lena", "state": "home"},
        ]}],
    }]


def test_without_any_person_entity_nothing_is_guessed(monkeypatch, tmp_path):
    entities = [entity for entity in house_entities() if entity.domain != "person"]
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist.")
    assert "person.*" in turn.speech and "Personen" in turn.speech, turn.speech
    assert "Soll ich" not in turn.speech
    house.say("Ja.")
    assert house.automations() == []


# --- what the monitor goals keep -------------------------------------------------


def test_the_reader_claims_what_it_understands():
    from homeintent.automation_composition import OutcomeKind
    from homeintent.engine import NluEngine

    engine = NluEngine()
    entities = house_entities()
    for text in (
        "Sag mir Bescheid, wenn ein Fenster offen ist und niemand zuhause ist.",
        "Wenn ich gehe und noch Licht an ist, sag mir Bescheid.",
        "Überwache das Garagentor und melde dich, wenn es offen ist und keiner zuhause ist.",
    ):
        assert engine.event_reading_kind(text, entities) is OutcomeKind.AUTOMATION, text
    for text in (
        "Wie viele Fenster sind offen?",
        "Benachrichtige mich nicht, wenn ein Fenster offen ist.",
        "Schalte das Licht an.",
    ):
        assert engine.event_reading_kind(text, entities) is not OutcomeKind.AUTOMATION, text


def test_leaving_with_a_light_still_on_is_one_automation(monkeypatch, tmp_path):
    """"Wenn ich gehe und noch Licht an ist" was a V10 monitor goal; the
    reader now understands it with the same meaning: the speaker leaves,
    any light is on (an OR - never "all lights")."""
    speech, automation = _create(
        monkeypatch, tmp_path, "Wenn ich gehe und noch Licht an ist, sag mir Bescheid."
    )
    assert "ein Licht an ist" in speech
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == "person.philipp" and trigger["from"] == "home"
    [any_light] = automation["conditions"]
    assert any_light["condition"] == "or"
    assert all(leaf["entity_id"].startswith("light.") for leaf in any_light["conditions"])
    assert len(any_light["conditions"]) > 1


def test_an_unreadable_request_still_reaches_the_monitor_goal(monkeypatch, tmp_path):
    """The goal path is not removed: what the reader cannot read stays there."""
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Wenn ich gehe und die Haustür nicht verriegelt ist, sag mir Bescheid.")
    assert "Soll ich das so einrichten" not in turn.speech
    # The test house has no monitor-goal store: the goal path answers.
    assert "Goal-Persistenz" in turn.speech, turn.speech
