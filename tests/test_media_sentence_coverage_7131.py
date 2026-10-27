"""7.13.1: a write media command runs only when the whole sentence is understood.

Counterexample of the nightly safety run of 7.13.0:

    Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat.

The bounded media reader recognised "Radio … aus" and paused the radio; the
unknown second clause was ignored.  Since 7.13.1 the media fast path takes a
write only when ``media_coverage`` explains every word (operation of exactly
that operation, media target, one place, the free-text name of a play
command, value, queue directive, fillers).  A coordinated sentence goes to the
shared multi-command pipeline, which compiles every clause before the first
write and refuses the whole sentence when one part is not understood.

The sentences are composed from building blocks (operations, places,
sources, joiners, unknown words); no fixed sentence list decides anything in
the production code.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation, house_entities, with_states  # noqa: E402
from homeintent.media import parse_media_request  # noqa: E402
from homeintent.media_coverage import analyse_media_coverage  # noqa: E402

AUTO = {"implicit_action_level": "low_risk_auto"}
READS = frozenset({("recorder", "get_statistics"), ("todo", "get_items"), ("calendar", "get_events"),
                   ("persistent_notification", "create"), ("automation", "reload")})
NIGHTLY = os.environ.get("HOMEINTENT_HYPOTHESIS_PROFILE") == "nightly"
PROPERTY = settings(
    derandomize=not NIGHTLY, max_examples=250 if NIGHTLY else 30, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

RADIO = "media_player.kuechenradio"
SPEAKER = "media_player.lautsprecher_schlafzimmer"
TV = "media_player.wohnzimmer_tv"
COUNTEREXAMPLE = "Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat."

# All three players play, so every operation has a target in every room.
_ENTITIES = with_states(
    house_entities(),
    media_player__kuechenradio="playing",
    media_player__lautsprecher_schlafzimmer="playing",
    media_player__wohnzimmer_tv="playing",
)
_PLAYERS = {
    "in der Küche": RADIO,
    "im Schlafzimmer": SPEAKER,
    "im Wohnzimmer": TV,
}
_SOURCES = {
    entity.entity_id: tuple(entity.attributes["source_list"])
    for entity in _ENTITIES if entity.domain == "media_player"
}
# (template, service): operations × the media content noun "Musik".
_OPERATIONS = (
    ("Pausiere die Musik {place}", "media_pause"),
    ("Mach die Musik {place} aus", "media_pause"),
    ("Stoppe die Musik {place}", "media_stop"),
    ("Mach die Musik {place} lauter", "volume_up"),
    ("Mach die Musik {place} leiser", "volume_down"),
    ("Stell die Lautstärke {place} auf 30 Prozent", "volume_set"),
    ("Nächstes Lied {place}", "media_next_track"),
    ("Schalte die Musik {place} stumm", "volume_mute"),
    ("Spiel {source} {place}", "select_source"),
)
JOINERS = st.sampled_from(["und", "und dann", "danach", "dann", "außerdem", "anschließend", "und danach"])
UNKNOWN = st.sampled_from(["Quirlomat", "Zappelding", "Blubberfix", "Knorpeltron", "Wusel"])

_HOUSE: HouseConversation | None = None
_COUNTER = [0]


def _house() -> HouseConversation:
    global _HOUSE
    if _HOUSE is None:
        _HOUSE = HouseConversation(pytest.MonkeyPatch(), entities=_ENTITIES, options=AUTO)
    return _HOUSE


def say(text: str):
    house = _house()
    _COUNTER[0] += 1
    house.conversation_id = f"media-coverage-{_COUNTER[0]}"
    return house.say(text)


def writes(turn) -> list[tuple[str, str, str]]:
    found = []
    for domain, service, data in turn.calls:
        if (domain, service) in READS:
            continue
        ids = data.get("entity_id")
        for entity_id in [ids] if isinstance(ids, str) else list(ids or ()):
            found.append((domain, service, entity_id))
    return sorted(found)


@st.composite
def media_command(draw):
    """(sentence, expected writes) of one fully understood media command."""
    place = draw(st.sampled_from(sorted(_PLAYERS)))
    player = _PLAYERS[place]
    template, service = draw(st.sampled_from(_OPERATIONS))
    source = draw(st.sampled_from(_SOURCES[player]))
    text = template.format(place=place, source=source)
    return text, [("media_player", service, player)]


# ------------------------------------------------------------ fixed regression
def test_counterexample_of_the_nightly_run_never_writes():
    turn = say(COUNTEREXAMPLE)
    assert writes(turn) == [], turn.speech
    assert turn.calls == [], turn.calls
    assert "nicht verstanden" in turn.speech and "nichts ausgeführt" in turn.speech, turn.speech


def test_counterexample_first_clause_alone_still_works():
    turn = say("Mach das Radio in der Küche aus.")
    assert writes(turn) == [("media_player", "media_pause", RADIO)], turn.speech


@pytest.mark.parametrize("text", [
    # "und dann" / "danach" with an unknown verb and an unknown target
    "Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat.",
    "Pausiere das Radio in der Küche danach Zappelding.",
    "Pausiere das Radio in der Küche, danach Zappelding den Blubberfix.",
    # unknown second verb, known target
    "Pausiere das Radio in der Küche und dann quirlomate das Licht im Büro.",
    # unknown second target, known verb
    "Pausiere das Radio in der Küche und dann schalte den Quirlomat aus.",
    # unknown exception
    "Pausiere die Musik in der Küche außer dem Quirlomat.",
    "Pausiere die Musik überall außer im Quirlomat.",
    # incomplete second clause
    "Pausiere das Radio in der Küche und dann das.",
    "Mach das Radio in der Küche aus und dann.",
    "Pausiere das Radio in der Küche und dann den.",
    # a second place that no target lies in
    "Pausiere das Radio in der Küche und im Wohnzimmer.",
    # valid first, invalid second media command
    "Pausiere das Radio in der Küche und spiel Quirlomat im Schlafzimmer.",
    "Mach die Musik in der Küche leiser und dann Quirlomat die Musik.",
    "Spiel Bayern 3 in der Küche und dann Quirlomat.",
    "Stell die Lautstärke in der Küche auf 30 Prozent und dann Zappelding.",
    # invalid first, valid second media command
    "Quirlomat den Quirlomat und dann pausiere das Radio in der Küche.",
    "Zappelding und dann mach die Musik in der Küche lauter.",
    # an unknown word inside the single clause
    "Pausiere das Radio in der Küche Quirlomat.",
    "Mach die Musik in der Küche zappelig lauter.",
])
def test_partially_understood_media_sentence_never_writes(text):
    turn = say(text)
    assert writes(turn) == [], (text, turn.speech)


@pytest.mark.parametrize("text,expected", [
    (
        "Pausiere das Radio in der Küche und mach das Licht im Büro aus.",
        [("homeassistant", "turn_off", "light.buerolicht"),
         ("homeassistant", "turn_off", "light.deckenfluter_buero"),
         ("homeassistant", "turn_off", "light.schreibtischlampe"),
         ("media_player", "media_pause", RADIO)],
    ),
    (
        "Mach das Licht im Büro aus und pausiere das Radio in der Küche.",
        [("homeassistant", "turn_off", "light.buerolicht"),
         ("homeassistant", "turn_off", "light.deckenfluter_buero"),
         ("homeassistant", "turn_off", "light.schreibtischlampe"),
         ("media_player", "media_pause", RADIO)],
    ),
    (
        "Mach die Musik im Schlafzimmer leiser und pausiere das Radio in der Küche.",
        [("media_player", "media_pause", RADIO), ("media_player", "volume_down", SPEAKER)],
    ),
    (
        "Mach das Radio in der Küche lauter und dann leiser.",
        [("media_player", "volume_down", RADIO), ("media_player", "volume_up", RADIO)],
    ),
])
def test_fully_understood_multi_command_runs_all_clauses(text, expected):
    turn = say(text)
    assert writes(turn) == sorted(expected), (text, turn.speech)


# ------------------------------------------------------------ coverage analysis
def _coverage(text: str, entities=_ENTITIES):
    request = parse_media_request(text)
    assert request is not None, text
    return analyse_media_coverage(text, entities, request)


def test_coverage_reports_spans_residue_and_coordination():
    coverage = _coverage(COUNTEREXAMPLE)
    assert coverage.operation == "off"
    assert not coverage.complete
    assert coverage.target_phrases == ("das", "Radio")
    assert coverage.place_phrases == ("in", "der", "Küche")
    assert coverage.coordinations == ("und", "dann")
    assert coverage.residue == ("Quirlomat", "Quirlomat")


@pytest.mark.parametrize("text", [
    "Mach bitte das Radio in der Küche aus.",
    "Pausiere doch mal die Musik in der Küche.",
    "Mach die Musik im Schlafzimmer ein bisschen leiser.",
    "Stell die Lautstärke im Wohnzimmer auf 30 Prozent.",
    "Spiel meine Playlist Entspannen in der Küche.",
    "Spiele den Titel One auf dem Küchenradio.",
    "Spiel den Titel One zur Warteschlange hinzu.",
    "Spiel den Titel One und ersetze die Warteschlange.",
    "Spiel Simon und Garfunkel in der Küche.",
])
def test_fully_explained_media_commands_are_complete(text):
    coverage = _coverage(text)
    assert coverage.complete, (coverage.residue, coverage.coordinations, coverage.conflicts)


def test_station_names_are_the_play_name_not_residue():
    coverage = _coverage("Spiel bitte WDR 2 in der Küche.")
    assert coverage.complete
    assert coverage.source_phrase == "WDR 2"


def test_a_saved_station_with_digits_plays(monkeypatch, tmp_path):
    radio = next(entity for entity in _ENTITIES if entity.entity_id == RADIO)
    entities = [
        replace(entity, attributes={**entity.attributes, "source_list": ["WDR 2", "Bayern 3"]})
        if entity is radio else entity
        for entity in _ENTITIES
    ]
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=AUTO)
    turn = house.say("Spiel WDR 2 in der Küche.")
    assert turn.calls == [("media_player", "select_source", {"source": "WDR 2", "entity_id": RADIO})], turn.speech
    house.conversation_id = "second"
    turn = house.say("Spiel WDR 2 in der Küche und dann Quirlomat.")
    assert writes(turn) == [], turn.speech


@pytest.mark.parametrize("text,reason", [
    ("Pausiere die Musik im Wohnzimmer in der Küche.", "place"),
    ("Mach das Radio den Fernseher in der Küche aus.", "target"),
])
def test_contradicting_places_or_targets_are_incomplete(text, reason):
    coverage = _coverage(text)
    assert reason in coverage.conflicts and not coverage.complete


def test_a_different_operation_lexeme_stays_residue():
    coverage = _coverage("Pausiere die Musik in der Küche lauter.")
    assert coverage.operation == "pause"
    assert coverage.residue == ("lauter",)


def test_questions_stay_read_only():
    for text in ("Was läuft gerade in der Küche?", "Was läuft gerade und mach das Licht im Büro aus?"):
        turn = say(text)
        assert [call for call in writes(turn) if call[0] == "media_player"] == [], turn.speech


# ------------------------------------------------------------ properties
@PROPERTY
@given(media_command())
def test_property_complete_media_command_writes_exactly_its_target(command):
    text, expected = command
    assert writes(say(text + ".")) == expected, text


@PROPERTY
@given(media_command(), JOINERS, UNKNOWN, st.sampled_from([
    "{u} den {u}", "{u}", "das", "", "mach den {u} an", "pausiere den {u}", "spiel {u}",
]))
def test_property_unknown_following_clause_never_leaves_a_write(command, joiner, unknown, tail):
    text, _expected = command
    sentence = f"{text} {joiner} {tail.format(u=unknown)}".strip() + "."
    assert writes(say(sentence)) == [], sentence


@PROPERTY
@given(media_command(), JOINERS, UNKNOWN)
def test_property_unknown_leading_clause_never_leaves_a_write(command, joiner, unknown):
    text, _expected = command
    sentence = f"{unknown} den {unknown} {joiner} {text[0].lower()}{text[1:]}."
    assert writes(say(sentence)) == [], sentence


@PROPERTY
@given(media_command(), UNKNOWN)
def test_property_unknown_exception_never_widens_or_writes(command, unknown):
    text, expected = command
    turn = say(f"{text} außer dem {unknown}.")
    found = writes(turn)
    assert found == [] or set(found) <= set(expected), text


@PROPERTY
@given(media_command(), st.sampled_from(["bitte ", "doch mal ", "mal eben "]), st.booleans())
def test_property_politeness_does_not_change_meaning(command, polite, at_end):
    text, expected = command
    first, rest = text.split(" ", 1)
    variant = f"{text}, bitte." if at_end else f"{first} {polite}{rest}."
    assert writes(say(variant)) == expected, variant


@pytest.mark.parametrize("base,reordered", [
    ("Pausiere das Radio in der Küche.", "Pausiere in der Küche das Radio."),
    ("Mach die Musik im Schlafzimmer leiser.", "Mach im Schlafzimmer die Musik leiser."),
    ("Stell die Lautstärke im Wohnzimmer auf 30 Prozent.", "Stell im Wohnzimmer die Lautstärke auf 30 Prozent."),
])
def test_reordering_does_not_create_a_new_target_set(base, reordered):
    expected = writes(say(base))
    assert expected and writes(say(reordered)) == expected
