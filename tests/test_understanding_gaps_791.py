"""7.9.1 A7: gaps in understanding (Nachtest 7.9.0 B7, all safely refused).

Each construction is generated over paraphrases; none is a listed sentence.
"""

from __future__ import annotations

import asyncio

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, offers_setup


def _house(monkeypatch, tmp_path) -> HouseConversation:
    from homeintent.monitor_goal import MonitorGoalStore

    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    house.entity._runtime_data.monitor_goals = MonitorGoalStore(tmp_path / "goals.json")
    return house


def _created(house: HouseConversation, text: str, *answers: str) -> dict:
    turn = house.say(text)
    for answer in answers:
        if "Nur jetzt oder jedes Mal" in turn.speech:
            # Asked only when the sentence did not say it ("jede Minute" does).
            turn = house.say(answer)
    assert offers_setup(turn.speech) or "Soll diese Automation" in turn.speech, turn.speech
    house.say("Ja.")
    return house.automations()[-1]


# --- repetition without a "wenn" clause; intervals below a minute ----------


@pytest.mark.parametrize("verb", ["Schick mir", "Sende mir", "Schreib mir"])
@pytest.mark.parametrize("interval", ["alle 5 Minuten", "jede Minute", "alle zwei Minuten"])
@pytest.mark.parametrize("bound", ["solange die Haustür offen ist", "bis die Haustür zu ist"])
def test_repetition_with_the_notification_between(monkeypatch, tmp_path, verb, interval, bound):
    house = _house(monkeypatch, tmp_path)
    automation = _created(house, f"{verb} {interval} eine Nachricht, {bound}.", "Jedes Mal.")
    [step] = automation["actions"]
    assert "repeat" in step
    assert step["repeat"]["while"][0]["entity_id"] == "binary_sensor.haustuer"


@pytest.mark.parametrize("interval", ["alle 30 Sekunden", "jede Sekunde", "alle 10 Sekunden", "sekündlich"])
@pytest.mark.parametrize("template", [
    "Erinnere mich {interval}, bis das Garagentor zu ist.",
    "Schick mir {interval} eine Nachricht, solange die Haustür offen ist.",
])
def test_too_short_an_interval_is_said(monkeypatch, tmp_path, interval, template):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(template.format(interval=interval))
    assert "höchstens einmal pro Minute" in turn.speech, turn.speech
    assert house.automations() == []


# --- days and weeks -----------------------------------------------------------


@pytest.mark.parametrize(("span", "seconds"), [
    ("eine Woche", 604800), ("zwei Wochen", 1209600), ("einen Tag", 86400), ("drei Tage", 259200),
    ("2 Wochen", 1209600),
])
@pytest.mark.parametrize("template", [
    "Melde dich, wenn die Haustür {span} nicht geöffnet wurde.",
    "Melde dich, wenn das Garagentor {span} offen ist.",
])
def test_days_and_weeks(monkeypatch, tmp_path, span, seconds, template):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(template.format(span=span))
    assert "kein passendes Gerät" not in turn.speech, turn.speech
    house.say("Ja.")
    [trigger] = house.automations()[-1]["triggers"]
    assert trigger["for"]["seconds"] == seconds


# --- power with consumption verbs ------------------------------------------------


@pytest.mark.parametrize("verb", ["zieht", "verbraucht", "gerade verbraucht", "nimmt", "braucht"])
@pytest.mark.parametrize(("subject", "entity_id", "value", "threshold"), [
    ("die Waschmaschine", "sensor.leistung_waschmaschine", "2000 Watt", 2000),
    ("der Trockner", "sensor.leistung_trockner", "1 kW", 1000),
    ("das Haus", "sensor.stromverbrauch_haus", "5 kW", 5000),
])
def test_consumption_verbs_mean_power(monkeypatch, tmp_path, verb, subject, entity_id, value, threshold):
    house = _house(monkeypatch, tmp_path)
    automation = _created(house, f"Melde dich, wenn {subject} mehr als {value} {verb}.")
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == entity_id and trigger["above"] == threshold


# --- presence detector of a room as inactivity ----------------------------------


@pytest.mark.parametrize("template", [
    "Melde dich, wenn im Wohnzimmer {span} niemand war.",
    "Melde dich, wenn {span} lang niemand im Wohnzimmer war.",
    "Sag mir Bescheid, wenn im Wohnzimmer {span} keiner mehr ist.",
])
@pytest.mark.parametrize("span", ["2 Stunden", "30 Minuten"])
def test_nobody_in_a_room_uses_its_presence_detector(monkeypatch, tmp_path, template, span):
    house = _house(monkeypatch, tmp_path)
    automation = _created(house, template.format(span=span))
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == "binary_sensor.praesenz_wohnzimmer"
    assert trigger["to"] == "off"


# --- travel participles as states -----------------------------------------------


@pytest.mark.parametrize(("phrase", "entity_id", "state"), [
    ("die Markise eingefahren ist", "cover.markise", "closed"),
    ("die Markise ausgefahren ist", "cover.markise", "open"),
    ("der Rollladen im Schlafzimmer hochgefahren ist", "cover.schlafzimmer_rollladen", "open"),
    ("der Rollladen im Schlafzimmer heruntergefahren ist", "cover.schlafzimmer_rollladen", "closed"),
])
def test_travel_participles_are_states(monkeypatch, tmp_path, phrase, entity_id, state):
    house = _house(monkeypatch, tmp_path)
    automation = _created(house, f"Sag mir Bescheid, wenn {phrase}.")
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == entity_id and trigger["to"] == state


def test_until_retracted_is_a_repetition(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Erinnere mich alle 10 Minuten, bis die Markise eingefahren ist.")
    assert turn.speech.startswith("Nur jetzt oder jedes Mal"), turn.speech


# --- "noch Licht an": any light ---------------------------------------------------


@pytest.mark.parametrize("phrase", ["noch Licht an ist", "irgendwo Licht an ist", "Licht brennt"])
def test_a_bare_mass_noun_means_any(monkeypatch, tmp_path, phrase):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(f"Sag mir Bescheid, wenn niemand zuhause ist und {phrase}.")
    assert "Welches Licht" not in turn.speech, turn.speech
    house.say("Ja.")
    automation = house.automations()[-1]
    lights = [
        entity_id
        for trigger in automation["triggers"]
        for entity_id in ([trigger["entity_id"]] if isinstance(trigger["entity_id"], str) else trigger["entity_id"])
        if entity_id.startswith("light.")
    ]
    assert len(lights) > 1


# --- meter reading threshold ------------------------------------------------------


@pytest.mark.parametrize("noun", ["der Energiezähler", "der Zählerstand", "der Stromzähler"])
@pytest.mark.parametrize("verb", ["steigt", "liegt"])
def test_a_meter_reading_is_a_threshold(monkeypatch, tmp_path, noun, verb):
    house = _house(monkeypatch, tmp_path)
    automation = _created(house, f"Melde dich, wenn {noun} über 12000 kWh {verb}.")
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == "sensor.energiezaehler" and trigger["above"] == 12000


def test_consumption_still_needs_a_period(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    assert "Ab wann soll ich den Verbrauch zählen" in house.say(
        "Sag mir Bescheid, wenn der Stromverbrauch über 10 kWh liegt."
    ).speech


# --- "Prüfe, ob …": a question now --------------------------------------------------


@pytest.mark.parametrize("verb", ["Prüfe", "Überprüfe", "Kontrolliere", "Schau mal nach", "Prüf bitte",
                                  "Kannst du prüfen"])
@pytest.mark.parametrize(("clause", "word"), [
    ("das Garagentor offen ist", "Garagentor"),
    ("die Haustür abgeschlossen ist", "Haustürschloss"),
    ("das Licht im Flur an ist", "Flurlicht"),
])
def test_check_whether_is_answered_now(monkeypatch, tmp_path, verb, clause, word):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(f"{verb}, ob {clause}.")
    assert word in turn.speech, turn.speech
    assert turn.speech.startswith(("Ja", "Nein")), turn.speech
    assert turn.calls == [] and house.automations() == []


# --- vague situation; "Beobachtest du …?"; short list -------------------------------


@pytest.mark.parametrize("noun", ["Auffälligkeiten", "Unregelmäßigkeiten", "Ungewöhnlichem"])
@pytest.mark.parametrize("verb", ["Melde dich", "Sag mir Bescheid", "Warne mich"])
def test_vague_nouns_answer_from_the_catalog(monkeypatch, tmp_path, noun, verb):
    turn = _house(monkeypatch, tmp_path).say(f"{verb} bei {noun}.")
    assert "Situationserkennung" in turn.speech, turn.speech


@pytest.mark.parametrize("question", [
    "Beobachtest du das Garagentor?", "Überwachst du das Garagentor?", "Beobachtest du gerade das Garagentor?",
])
def test_watching_question_answers_from_the_list(monkeypatch, tmp_path, question):
    house = _house(monkeypatch, tmp_path)
    assert house.say(question).speech.startswith("Nein")
    _created(house, "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.")
    answer = house.say(question).speech
    assert answer.startswith("Ja: wenn das Garagentor länger als 10 Minuten offen ist"), answer
    assert "Push-Benachrichtigung" not in answer
    house.say("Stopp die Garagen-Meldung.")
    assert house.say(question).speech.startswith("Nein, gerade nicht")


def test_the_list_is_short_and_the_detail_comes_on_request(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _created(house, "Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.")
    _created(house, "Sag mir Bescheid, wenn die Haustür geöffnet wird.")
    listed = house.say("Welche Überwachungen laufen?").speech
    assert listed.startswith("Es laufen 2 Überwachungen:"), listed
    assert "Push-Benachrichtigung" not in listed and "Handy" not in listed
    detail = house.say("Was genau macht die erste?").speech
    assert "Push-Benachrichtigung" in detail and "Garagentor" in detail, detail
    house.say("Welche Überwachungen laufen?")
    assert "Haustür" in house.say("Und die letzte?").speech


def test_the_rate_monitor_keeps_its_runtime_hint(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.")
    house.say("Ja.")
    assert len(asyncio.run(house.entity._runtime_data.monitor_goals.async_load())) == 1
    assert "das überwache ich selbst" in house.say("Welche Überwachungen laufen?").speech
