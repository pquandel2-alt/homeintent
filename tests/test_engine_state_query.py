"""HomeIntent V4.2, "Semantic Query & State Resolution": HassStateQuery
(list/count), HassCheckState (singular boolean), HassExistsQuery (existence)
- the three intents StateQueryParser produces. Mirrors
tests/test_engine_comparisons.py's style: direct ``engine.match()`` calls
against synthetic ``EntitySnapshot`` fixtures.

Covers each operation x each result cardinality (0/1/N), area scoping, the
Milestone-1 ``allows_empty`` validator gating (0 matches for
list/count/exists is SUCCESS + EMPTY, never an error - Regel from the
spec), and the ``_STATE_QUERY_RE``/narrowed ``_COMPARISON_QUERY_RE`` routing
boundary that motivated this session's regex changes.
"""

from __future__ import annotations

from homeintent.entities import EntitySnapshot
from homeintent.nlu.query_command import QueryCommand, QueryScope
from homeintent.nlu.semantic_state import SemanticState

FENSTER_KELLER = EntitySnapshot(
    "binary_sensor.fenster_keller", "Fenster Keller", "binary_sensor", "on",
    area_id="keller", area_name="Keller", device_class="window",
)
FENSTER_BAD = EntitySnapshot(
    "binary_sensor.fenster_bad", "Fenster Bad", "binary_sensor", "off",
    area_id="bad", area_name="Bad", device_class="window",
)
TUER_EINGANG = EntitySnapshot(
    "binary_sensor.tuer_eingang", "Tür Eingang", "binary_sensor", "on",
    area_id="eingang", area_name="Eingang", device_class="door",
)
MOTION_FLUR = EntitySnapshot(
    "binary_sensor.bewegung_flur", "Bewegung Flur", "binary_sensor", "on",
    area_id="flur", area_name="Flur", device_class="motion",
)
LICHT_WOHNZIMMER = EntitySnapshot(
    "light.wohnzimmer", "Wohnzimmerlicht", "light", "on",
    area_id="wohnzimmer", area_name="Wohnzimmer",
)
LICHT_KUECHE = EntitySnapshot(
    "light.kueche", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
)

WINDOWS = [FENSTER_KELLER, FENSTER_BAD]
ALL_SENSORS = [FENSTER_KELLER, FENSTER_BAD, TUER_EINGANG, MOTION_FLUR]
LIGHTS = [LICHT_WOHNZIMMER, LICHT_KUECHE]

FENSTER_BADEZIMMER = EntitySnapshot(
    "binary_sensor.fensterkontakt_dusche",
    "Fensterkontakt Dusche",
    "binary_sensor",
    "off",
    area_id="badezimmer",
    area_name="Badezimmer",
    device_class="window",
)
ROLLLADEN_BAD = EntitySnapshot(
    "cover.rollladen_bad", "Rollladen Bad", "cover", "open",
    area_id="badezimmer", area_name="Badezimmer",
)
ROLLLADEN_BUERO = EntitySnapshot(
    "cover.rollladen_buero", "Rollladen Büro", "cover", "open",
    area_id="buero", area_name="Büro",
)

TEMPERATUR_BAD = EntitySnapshot(
    "sensor.temperatur_bad", "Temperatur Bad", "sensor", "21.5",
    area_id="badezimmer", area_name="Badezimmer",
    device_class="temperature", unit="°C",
)


# --- HassStateQuery (list/count) ------------------------------------------


def test_state_query_list_single_match(engine):
    result = engine.match("welche Fenster sind offen", WINDOWS)
    assert result is not None
    assert result.plan is None  # query, never calls a service (Regel 3)
    assert result.command.entities == (FENSTER_KELLER,)
    assert result.response_text == "Nur Fenster Keller ist offen."


def test_compound_area_window_question_does_not_require_exact_entity_name(engine):
    result = engine.match(
        "Ist das Badezimmer Fenster geschlossen?", [FENSTER_BADEZIMMER]
    )

    assert result is not None
    assert result.plan is None
    assert result.command.entities == (FENSTER_BADEZIMMER,)
    assert result.response_text == "Ja, Fensterkontakt Dusche ist geschlossen."


def test_open_entity_state_question_reports_the_live_semantic_state(engine):
    result = engine.match(
        "Welchen Zustand hat das Badezimmer Fenster?", [FENSTER_BADEZIMMER]
    )

    assert result is not None
    assert result.plan is None
    assert result.command.intent == "HassEntityStateQuery"
    assert result.response_text == "Fensterkontakt Dusche ist geschlossen."


def test_all_rolladen_hochgefahren_is_a_universal_state_question(engine):
    result = engine.match(
        "Sind alle Rolladen hochgefahren?", [ROLLLADEN_BAD, ROLLLADEN_BUERO]
    )

    assert result is not None
    assert result.plan is None
    assert result.response_text == "Ja, alle Rollläden sind geöffnet."


def test_all_rolladen_question_answers_no_if_one_is_down(engine):
    closed = EntitySnapshot(
        "cover.rollladen_kueche", "Rollladen Küche", "cover", "closed",
        area_id="kueche", area_name="Küche",
    )
    result = engine.match(
        "Sind alle Rolladen hochgefahren?", [ROLLLADEN_BAD, closed]
    )

    assert result is not None
    assert result.response_text == "Nein, nicht alle Rollläden sind geöffnet. Nicht geöffnet: Rollladen Küche."


def test_room_possessive_window_question_never_resolves_to_another_device(engine):
    result = engine.match(
        "Ist das Fenster vom Badezimmer geschlossen?",
        [FENSTER_BADEZIMMER, ROLLLADEN_BAD],
    )
    assert result is not None
    assert result.command.entities == (FENSTER_BADEZIMMER,)
    assert result.response_text == "Ja, Fensterkontakt Dusche ist geschlossen."


def test_common_open_state_question_wordings(engine):
    questions = (
        "Wie ist der Zustand vom Badezimmer Fenster?",
        "Was ist der Status vom Badezimmer Fenster?",
        "Zeige mir den Zustand vom Badezimmer Fenster.",
    )
    for question in questions:
        result = engine.match(question, [FENSTER_BADEZIMMER])
        assert result is not None, question
        assert result.plan is None
        assert result.response_text == "Fensterkontakt Dusche ist geschlossen."


def test_universal_quantifier_wordings(engine):
    for question in (
        "Sind sämtliche Rollläden oben?",
        "Sind beide Rollläden oben?",
        "Sind die Rollläden alle oben?",
    ):
        result = engine.match(question, [ROLLLADEN_BAD, ROLLLADEN_BUERO])
        assert result is not None, question
        assert result.response_text == "Ja, alle Rollläden sind geöffnet."


def test_beide_refuses_when_target_count_is_not_two(engine):
    third = EntitySnapshot("cover.kueche", "Rollladen Küche", "cover", "open")
    assert engine.match(
        "Sind beide Rollläden oben?", [ROLLLADEN_BAD, ROLLLADEN_BUERO, third]
    ) is None


def test_exists_query_common_wordings(engine):
    for question in (
        "Ist irgendein Fenster offen?",
        "Sind irgendwelche Fenster offen?",
        "Gibt es ein offenes Fenster?",
        "Haben wir offene Fenster?",
    ):
        result = engine.match(question, [FENSTER_KELLER, FENSTER_BAD])
        assert result is not None, question
        assert result.plan is None
        assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_negative_existence_query(engine):
    result = engine.match("Ist kein Fenster offen?", [FENSTER_BAD])
    assert result is not None
    assert result.plan is None
    assert result.response_text == "Ja, es sind keine Fenster geöffnet."


def test_singular_interrogative_lists_matching_entity(engine):
    result = engine.match("Welches Fenster ist offen?", WINDOWS)
    assert result is not None
    assert result.response_text == "Nur Fenster Keller ist offen."


def test_floor_scoped_state_question(engine):
    upstairs_open = EntitySnapshot(
        "binary_sensor.fenster_oben", "Fenster Galerie", "binary_sensor", "on",
        floor_id="og", floor_name="Obergeschoss", device_class="window",
    )
    downstairs_closed = EntitySnapshot(
        "binary_sensor.fenster_unten", "Fenster Küche", "binary_sensor", "off",
        floor_id="eg", floor_name="Erdgeschoss", device_class="window",
    )
    result = engine.match(
        "Welche Fenster sind im Obergeschoss offen?",
        [upstairs_open, downstairs_closed],
    )
    assert result is not None
    assert result.command.entities == (upstairs_open,)


def test_count_query_always_answers_with_a_number_for_one_match(engine):
    result = engine.match("Wie viele Fenster sind offen?", WINDOWS)
    assert result is not None
    assert result.response_text == "1 Fenster ist geöffnet."


def test_location_question_wordings_group_matches_by_room(engine):
    for question in (
        "Wo sind Fenster offen?",
        "In welchen Räumen sind Fenster offen?",
        "Welche Räume haben offene Fenster?",
    ):
        result = engine.match(question, WINDOWS)
        assert result is not None, question
        assert result.plan is None
        assert result.response_text == "Im Keller sind Fenster geöffnet."


def test_cross_domain_room_state_question(engine):
    light = EntitySnapshot(
        "light.bad", "Badezimmerlicht", "light", "on",
        area_id="badezimmer", area_name="Badezimmer",
    )
    result = engine.match(
        "Was ist im Badezimmer eingeschaltet?", [FENSTER_BADEZIMMER, light]
    )
    assert result is not None
    assert result.plan is None
    assert result.command.entities == (light,)
    assert result.response_text == "Badezimmerlicht ist eingeschaltet."


def test_common_room_temperature_question_wordings(engine):
    for question in (
        "Wie viel Grad sind im Badezimmer?",
        "Wie viele Grad hat das Badezimmer?",
        "Wie ist die Temperatur im Badezimmer?",
        "Was ist die Temperatur im Badezimmer?",
    ):
        result = engine.match(question, [TEMPERATUR_BAD])
        assert result is not None, question
        assert result.plan is None
        assert result.response_text == "21,5 Grad."


def test_state_query_list_multiple_matches(engine):
    result = engine.match("welche Fenster sind geschlossen", [FENSTER_BAD, TUER_EINGANG])
    # Only FENSTER_BAD (window, off) is closed - TUER_EINGANG is a door, on.
    assert result is not None
    assert result.command.entities == (FENSTER_BAD,)


def test_state_query_list_empty_is_success_not_error(engine):
    # Regel: 0 matches for LIST_ENTITIES is SUCCESS + EMPTY, not an error.
    result = engine.match("welche Fenster sind geschlossen", [FENSTER_KELLER])
    assert result is not None
    assert result.command.entities == ()
    assert result.response_text == "Es sind keine Fenster geschlossen."


def test_state_query_count_only_phrasing(engine):
    result = engine.match(
        "wie viele Fenster sind offen",
        [FENSTER_KELLER, EntitySnapshot(
            "binary_sensor.fenster_buero", "Fenster Büro", "binary_sensor", "on",
            area_id="buero", area_name="Büro", device_class="window",
        )],
    )
    assert result is not None
    assert len(result.command.entities) == 2
    assert result.response_text == "2 Fenster sind geöffnet."


def test_state_query_area_scoped(engine):
    # HassStateQuery's grammar puts the area before the state word
    # ("welche {device_class} sind [area] {state}"), matching how this
    # phrasing reads most naturally in German.
    result = engine.match("welche Fenster sind im Keller offen", WINDOWS)
    assert result is not None
    assert result.command.entities == (FENSTER_KELLER,)


def test_state_query_area_scoped_no_matches_still_empty_success(engine):
    result = engine.match("welche Fenster sind im Bad offen", WINDOWS)
    assert result is not None
    assert result.command.entities == ()


def test_state_query_unknown_area_returns_none(engine):
    assert engine.match("welche Fenster sind im Bunker offen", WINDOWS) is None


def test_state_query_noch_filler_word(engine):
    # Live incident (v4.2 Milestone 7): "noch" (still/yet) is idiomatic
    # German and was one of the two originally-reported failing sentences
    # ("Welche Fenster sind noch geöffnet?") - the grammar had no optional
    # filler for it and returned NOT_UNDERSTOOD. Carries no semantic weight
    # (SemanticState unchanged), so this must resolve identically to the
    # "noch"-less phrasing.
    result = engine.match("welche Fenster sind noch offen", WINDOWS)
    assert result is not None
    assert result.command.entities == (FENSTER_KELLER,)


def test_state_query_area_scoped_noch_filler_word(engine):
    result = engine.match("welche Fenster sind im Keller noch offen", WINDOWS)
    assert result is not None
    assert result.command.entities == (FENSTER_KELLER,)


def test_state_query_denn_filler_word(engine):
    # Spec gap #2: "denn" (rhetorical-question filler, "Sind denn Fenster
    # offen?") carries no semantic weight, same as "noch" above.
    result = engine.match("welche Fenster sind denn offen", WINDOWS)
    assert result is not None
    assert result.command.entities == (FENSTER_KELLER,)


def test_state_query_bare_sentence_no_welche_or_wie_viele(engine):
    # Spec gap #3: "Sind Fenster noch offen?" has neither "welche" nor "wie
    # viele" - a third, equally natural German phrasing. Routing already
    # worked ("sind" is an existing _STATE_QUERY_RE trigger word); only the
    # grammar template itself was missing.
    result = engine.match("sind Fenster noch offen", WINDOWS)
    assert result is not None
    assert result.command.entities == (FENSTER_KELLER,)


def test_state_query_bare_sentence_area_scoped(engine):
    result = engine.match("sind im Keller Fenster offen", WINDOWS)
    assert result is not None
    assert result.command.entities == (FENSTER_KELLER,)


def test_state_query_motion_binary_sensor_uses_on_off_not_open_closed(engine):
    # SemanticState branching: a motion binary_sensor's "an"/"aus" maps to
    # ON/OFF, not OPEN/CLOSED - a "welche ... sind offen" query must not
    # match it even though its raw HA state is "on".
    result = engine.match("welche Bewegungsmelder sind eingeschaltet", ALL_SENSORS)
    assert result is not None
    assert result.command.entities == (MOTION_FLUR,)


# --- HassCheckState (singular boolean) -------------------------------------


def test_check_state_positive(engine):
    result = engine.match("ist das Fenster Keller offen", WINDOWS)
    assert result is not None
    assert result.plan is None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_check_state_negative(engine):
    result = engine.match("ist das Fenster Bad offen", WINDOWS)
    assert result is not None
    assert result.response_text == "Nein, Fenster Bad ist nicht geöffnet."


def test_check_state_area_scoped(engine):
    result = engine.match("ist die Tür Eingang im Eingang offen", ALL_SENSORS)
    assert result is not None
    assert result.response_text == "Ja, Tür Eingang ist geöffnet."


def test_check_state_noch_filler_word(engine):
    # Same "noch" gap as HassStateQuery (see test_state_query_noch_filler_word)
    # - "Ist das Fenster Keller noch offen?" is equally idiomatic German.
    result = engine.match("ist das Fenster Keller noch offen", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_check_state_area_scoped_noch_filler_word(engine):
    result = engine.match("ist die Tür Eingang im Eingang noch offen", ALL_SENSORS)
    assert result is not None
    assert result.response_text == "Ja, Tür Eingang ist geöffnet."


def test_check_state_area_scoped_word_order_does_not_pick_decoy_entity(engine):
    # Live incident (v4.2 Milestone 7): the area-scoped HassCheckState
    # sentence must be tried before the bare one in state_query.yaml, or
    # {name} (an open wildcard) swallows "{name} im {area}" whole and never
    # resolves {area} at all. That alone is silent - the real danger is a
    # second entity whose friendly_name equals just the area word ("Fenster
    # im Schlafzimmer" spoken text still fuzzy-matches an entity literally
    # named "Schlafzimmer" via the resolver's substring scoring), which then
    # gets answered instead of the actually-asked-about entity - a Regel 4
    # violation (never guess) that a "does this return a result at all"
    # check wouldn't catch. Names are in "item area" spoken order but
    # "area item" friendly_name order specifically because that mismatch is
    # what made the decoy win before the grammar fix.
    schlafzimmer_fenster = EntitySnapshot(
        "binary_sensor.schlafzimmer_fenster", "Schlafzimmer Fenster", "binary_sensor", "off",
        area_id="schlafzimmer", area_name="Schlafzimmer", device_class="window",
    )
    schlafzimmer_rollladen_decoy = EntitySnapshot(
        "cover.schlafzimmer_decoy", "Schlafzimmer", "cover", "open",
        area_id="schlafzimmer", area_name="Schlafzimmer",
    )
    result = engine.match(
        "ist das Fenster im Schlafzimmer geöffnet",
        [schlafzimmer_fenster, schlafzimmer_rollladen_decoy],
    )
    assert result is not None
    assert result.command.entities == (schlafzimmer_fenster,)
    assert result.response_text == "Nein, Schlafzimmer Fenster ist nicht geöffnet."


def test_check_state_unresolved_name_returns_none(engine):
    # Never guess (Regel 4): an unknown/ambiguous singular name never falls
    # back to a clarification round-trip here, unlike SingleTargetParser.
    assert engine.match("ist das Fenster Garage offen", WINDOWS) is None


def test_check_state_ambiguous_name_returns_none(engine):
    duplicate_named = [
        EntitySnapshot("binary_sensor.f1", "Fenster", "binary_sensor", "on", device_class="window"),
        EntitySnapshot("binary_sensor.f2", "Fenster", "binary_sensor", "off", device_class="window"),
    ]
    assert engine.match("ist das Fenster offen", duplicate_named) is None


# --- HassExistsQuery (existence) --------------------------------------------


def test_exists_query_true_with_state_filter(engine):
    result = engine.match("gibt es offene Fenster", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_exists_query_false_with_state_filter(engine):
    result = engine.match("gibt es offene Fenster", [FENSTER_BAD])
    assert result is not None
    assert result.response_text == "Nein, es gibt keine Fenster."


def test_exists_query_bare_no_state_word(engine):
    # HassExistsQuery's bare "gibt es {device_class}" sentence carries no
    # state word at all - must still route to StateQueryParser, not fall
    # through to "not understood" (the second of the two live routing bugs
    # fixed this session).
    result = engine.match("gibt es Fenster im Keller", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, es gibt 1 Fenster."


def test_exists_query_device_class_before_area_word_order(engine):
    # Both German word orders are natural for HassExistsQuery - "gibt es
    # Fenster im Keller" (device_class before area) must work, not just
    # "gibt es im Keller Fenster" (area before device_class).
    result = engine.match("gibt es geschlossene Fenster im Bad", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Bad ist geschlossen."


def test_exists_query_area_scoped_false(engine):
    result = engine.match("gibt es offene Fenster im Bad", WINDOWS)
    assert result is not None
    assert result.response_text == "Nein, es gibt keine Fenster."


def test_exists_query_noch_filler_word(engine):
    # Spec gap #1: "gibt es noch offene Fenster?" (still/yet) - HassExistsQuery
    # had no "[noch]" while HassStateQuery/HassCheckState already did.
    result = engine.match("gibt es noch offene Fenster", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_exists_query_denn_filler_word(engine):
    result = engine.match("gibt es denn offene Fenster", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_exists_query_ist_ein_word_order(engine):
    # Spec gap #4: "Ist ein Fenster offen?" - existence phrased with "ist"/
    # indefinite article instead of "gibt es". Still EXISTS semantics (no
    # specific named entity requested), not HassCheckState.
    result = engine.match("ist ein Fenster offen", WINDOWS)
    assert result is not None
    assert result.frame.intent == "HassExistsQuery"
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_exists_query_ist_ein_area_scoped_word_order(engine):
    result = engine.match("ist im Bad ein Fenster offen", WINDOWS)
    assert result is not None
    assert result.frame.intent == "HassExistsQuery"
    assert result.response_text == "Nein, es gibt keine Fenster."


def test_exists_query_ist_ein_does_not_shadow_check_state(engine):
    # The "ist ein {device_class} ... {state}" grammar (added for gap #4)
    # must not swallow a real named-entity HassCheckState question just
    # because HassExistsQuery is now declared first in the YAML file for
    # ordering reasons (see state_query.yaml's comment) - a real "die/der/das"
    # article must still resolve to the named entity.
    result = engine.match("ist das Fenster Keller offen", WINDOWS)
    assert result is not None
    assert result.frame.intent == "HassCheckState"
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


# --- Routing boundary -------------------------------------------------------


def test_plain_on_off_command_not_misrouted_to_state_query(engine):
    # "Schalte das Licht aus" contains "aus" but none of _STATE_QUERY_RE's
    # trigger words - must still reach the plain on/off command pipeline.
    result = engine.match("schalte das Küchenlicht aus", LIGHTS)
    assert result is not None
    assert result.plan is not None
    assert result.plan.service == "turn_off"


def test_welche_without_comparator_or_state_word_not_misrouted(engine):
    # A bare "welche" sentence with neither a comparator word (narrowed
    # _COMPARISON_QUERY_RE) nor a state word (_STATE_QUERY_RE) falls through
    # to "not understood" rather than being swallowed by either grammar.
    assert engine.match("welche Lichter gibt es normalerweise", LIGHTS) is None


def test_comparison_query_still_routes_to_comparison_parser_not_state_query(engine):
    # "welche Lichter sind mindestens 50 Prozent" has no state word at all,
    # so it must stay on ComparisonQueryParser's grammar, not get swallowed
    # by _STATE_QUERY_RE.
    bright_light = EntitySnapshot(
        "light.hell", "Helles Licht", "light", "on", attributes={"brightness": 255},
    )
    result = engine.match("welche Lichter sind mindestens 50 Prozent", [bright_light])
    assert result is not None
    assert result.frame.intent == "HassQueryComparison"


# --- Negative tests (v4.2.1 plan Section 35) --------------------------------
#
# StateQueryParser must not swallow sentences it structurally shouldn't
# answer, and plain commands must not be misread as queries just because
# they happen to share a state word with _STATE_QUERY_RE's trigger list.


def test_wie_lange_noch_offen_not_misread_as_simple_state_query(engine):
    # "Wie lange ist das Fenster noch offen?" asks for a *duration*, not a
    # yes/no state - it contains the trigger word "ist" and the state word
    # "offen", so it does reach _STATE_QUERY_RE/StateQueryParser, but no
    # HassCheckState/HassStateQuery/HassExistsQuery sentence template
    # accounts for a leading "wie lange" (none of them have a wildcard slot
    # to absorb it) - so it must fall through to "not understood" rather
    # than be silently misanswered as "Ja, Fenster ist offen."
    result = engine.match("wie lange ist das Fenster noch offen", WINDOWS)
    assert result is None


def test_wie_viel_ist_auf_dem_stromzaehler_not_misread_as_open_query(engine):
    # "Wie viel ist noch auf dem Stromzähler?" has the trigger word "ist"
    # but no state word from _STATE_QUERY_RE's vocabulary ("auf" is not
    # "offen"/"an"/...) - must never be routed to StateQueryParser (which
    # would have no way to interpret a sensor reading as OPEN/CLOSED).
    stromzaehler = EntitySnapshot(
        "sensor.stromzaehler", "Stromzähler", "sensor", "4321", unit="kWh",
    )
    result = engine.match("wie viel ist noch auf dem Stromzähler", [stromzaehler])
    assert result is None or result.frame is None or result.frame.intent != "HassCheckState"


def test_mach_das_fenster_auf_remains_a_command(engine):
    # "Mach das Fenster auf." must stay a COMMAND (HassOpenCover) - it has
    # neither of _STATE_QUERY_RE's trigger words ("welche"/"wie viele"/
    # "ist"/"sind"), so it can't be misrouted to StateQueryParser in the
    # first place; pinned here as an explicit regression guard anyway.
    fenster_cover = EntitySnapshot(
        "cover.fenster", "Fenster", "cover", "closed", capabilities=frozenset({"OPEN_CLOSE"}),
    )
    result = engine.match("mach das Fenster auf", [fenster_cover])
    assert result is not None
    assert result.plan is not None
    assert result.plan.service == "open_cover"


def test_mach_das_licht_aus_remains_a_command(engine):
    # "Mach das Licht aus." contains the state word "aus" but no trigger
    # word - must stay a COMMAND (HassTurnOff), not a QUERY.
    result = engine.match("mach das Küchenlicht aus", LIGHTS)
    assert result is not None
    assert result.plan is not None
    assert result.plan.service == "turn_off"


def test_mach_das_licht_noch_heller_remains_a_command(engine):
    # "Mach das Licht noch heller." contains "noch" (a StateQueryParser
    # filler word elsewhere) but no _STATE_QUERY_RE trigger word - must
    # stay a COMMAND (HassLightBrighten), not be damaged by any "noch"
    # handling (Section 5: "noch" must never be globally stripped).
    dimmable_light = EntitySnapshot(
        "light.wohnzimmer", "Wohnzimmerlicht", "light", "on",
        capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
    )
    result = engine.match("mach das Licht noch heller", [dimmable_light])
    assert result is not None
    assert result.frame.intent == "HassLightBrighten"
    assert result.plan is not None


# --- WorldModelQuery wave: ResponseGenerator is the live response source -
#
# engine.match()'s response_text for these three intents now flows through
# frame.parameters["query_result"] -> ResponseGenerator.respond() (Phase D),
# not through service_call.py's old per-intent lambdas - verified here
# end-to-end (not just against QueryExecutor/ResponseGenerator in isolation).


def test_state_query_end_to_end_response_comes_from_response_generator(engine):
    result = engine.match("welche Fenster sind offen", WINDOWS)
    assert result is not None
    assert result.response_text == "Nur Fenster Keller ist offen."


def test_check_state_end_to_end_response_comes_from_response_generator(engine):
    result = engine.match("ist das Fenster Keller offen", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


def test_exists_query_end_to_end_response_comes_from_response_generator(engine):
    result = engine.match("gibt es offene Fenster", WINDOWS)
    assert result is not None
    assert result.response_text == "Ja, Fenster Keller ist geöffnet."


# --- Phase 7 (HomeIntent v4.2.1 plan): frame.parameters["query_command"] --
#
# The new, additive observable surface Phase 7's non-destructive migration
# introduces: every StateQueryParser._parse_* now also stashes the exact
# QueryCommand it built (and ran through QueryExecutor internally) into
# frame.parameters, reachable here via result.command.parameters - a future
# V5/Phase 8 caller can read it back without re-deriving anything from the
# frame's raw text references. plan stays None (queries never call a
# service); response_text is now driven by ResponseGenerator (WorldModelQuery
# wave), not asserted by the tests in this block.


def test_state_query_list_populates_query_command_parameter(engine):
    result = engine.match("welche Fenster sind offen", WINDOWS)
    assert result is not None
    query_command = result.command.parameters["query_command"]
    assert isinstance(query_command, QueryCommand)
    assert query_command.intent == "HassStateQuery"
    assert query_command.scope is QueryScope.LIST
    assert query_command.target.domain == "binary_sensor"
    assert query_command.target.device_class == "window"
    assert query_command.filter.state is SemanticState.OPEN


def test_state_query_count_populates_count_scope(engine):
    result = engine.match(
        "wie viele Fenster sind offen",
        [FENSTER_KELLER, EntitySnapshot(
            "binary_sensor.fenster_buero", "Fenster Büro", "binary_sensor", "on",
            area_id="buero", area_name="Büro", device_class="window",
        )],
    )
    assert result is not None
    query_command = result.command.parameters["query_command"]
    assert query_command.scope is QueryScope.COUNT


def test_state_query_area_scoped_populates_target_area(engine):
    result = engine.match("welche Fenster sind im Keller offen", WINDOWS)
    assert result is not None
    query_command = result.command.parameters["query_command"]
    assert query_command.target.area is not None
    assert query_command.target.area.area_id == "keller"


def test_exists_query_populates_exists_scope(engine):
    result = engine.match("gibt es offene Fenster", WINDOWS)
    assert result is not None
    query_command = result.command.parameters["query_command"]
    assert query_command.intent == "HassExistsQuery"
    assert query_command.scope is QueryScope.EXISTS


def test_check_state_populates_single_scope_with_resolved_entity_id(engine):
    result = engine.match("ist das Fenster Keller offen", WINDOWS)
    assert result is not None
    query_command = result.command.parameters["query_command"]
    assert query_command.intent == "HassCheckState"
    assert query_command.scope is QueryScope.SINGLE
    assert query_command.target.entity_id == "binary_sensor.fenster_keller"
    assert query_command.filter.state is SemanticState.OPEN


# --- WorldModelQuery wave, Phase C: device query + stateless listing -----
#
# Both new sentences only resolve to a match with a WorldModel passed
# (Phase A) - devices come from WorldModel.devices_in_area, never from the
# entities list directly. Few targeted end-to-end tests only, per the "no
# big new test suite" constraint - unit coverage for the 0/1/N cardinality
# split already lives in test_response_generator.py/test_query_executor.py.

SCHREIBTISCHLAMPE_BUERO = EntitySnapshot(
    "light.schreibtischlampe", "Schreibtischlampe", "light", "on",
    area_id="buero", area_name="Büro",
)


def _world_model_with_buero_device():
    from homeintent.devices import DeviceSnapshot
    from homeintent.world_model import build_world_model

    device = DeviceSnapshot(
        device_id="device.schreibtischlampe", name="Schreibtischlampe",
        area_id="buero", area_name="Büro", entity_ids=("light.schreibtischlampe",),
    )
    return build_world_model([SCHREIBTISCHLAMPE_BUERO], [device])


def test_device_query_end_to_end_lists_devices_in_area(engine):
    world_model = _world_model_with_buero_device()
    result = engine.match("welche Geräte sind im Büro", [SCHREIBTISCHLAMPE_BUERO], world_model=world_model)
    assert result is not None
    assert result.plan is None
    assert result.response_text == "Schreibtischlampe ist im Büro."


def test_device_query_without_world_model_does_not_crash(engine):
    result = engine.match("welche Geräte sind im Büro", [SCHREIBTISCHLAMPE_BUERO])
    assert result is not None
    assert result.response_text == "Im Büro sind keine Geräte bekannt."


def test_stateless_state_query_end_to_end_lists_area_scoped_matches(engine):
    result = engine.match("welche Lichter sind im Büro", [SCHREIBTISCHLAMPE_BUERO])
    assert result is not None
    assert result.plan is None
    assert result.response_text == "Schreibtischlampe ist im Büro."


# --- Live incident (2026-08): "Sind Fenster offen?" etc. always returned ---
# empty on a real HA instance despite real open/closed window/door entities
# existing and being correctly exposed. Root cause turned out to be entirely
# outside this pipeline (config_flow.py froze a one-time entity snapshot at
# config-entry setup - see its module docstring) - these five cases pin down
# that the query pipeline itself was never at fault, given a properly
# populated entity list, so a regression here would mean a *new* bug, not
# a recurrence of the live one.

WOHNZIMMER_FENSTER_OFFEN = EntitySnapshot(
    "binary_sensor.wohnzimmer_fenster", "Wohnzimmer Fenster", "binary_sensor", "on",
    area_id="wohnzimmer", area_name="Wohnzimmer", device_class="window",
)
KUECHE_FENSTER_ZU = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küche Fenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
HAUSTUER = EntitySnapshot(
    "binary_sensor.haustuer", "Haustür", "binary_sensor", "on", device_class="door",
)
WINDOW_DOOR_ENTITIES = [WOHNZIMMER_FENSTER_OFFEN, KUECHE_FENSTER_ZU, HAUSTUER]


def test_live_incident_sind_fenster_offen(engine):
    result = engine.match("sind Fenster offen", WINDOW_DOOR_ENTITIES)
    assert result is not None
    assert result.response_text == "Nur Wohnzimmer Fenster ist offen."


def test_live_incident_welche_fenster_sind_im_wohnzimmer_offen(engine):
    result = engine.match("welche Fenster sind im Wohnzimmer offen", WINDOW_DOOR_ENTITIES)
    assert result is not None
    assert result.response_text == "Wohnzimmer Fenster ist geöffnet."


def test_live_incident_welche_fenster_sind_geschlossen(engine):
    result = engine.match("welche Fenster sind geschlossen", WINDOW_DOOR_ENTITIES)
    assert result is not None
    assert result.response_text == "Nur Küche Fenster ist geschlossen."


def test_live_incident_gibt_es_fenster(engine):
    result = engine.match("gibt es Fenster", WINDOW_DOOR_ENTITIES)
    assert result is not None
    assert result.response_text == "Ja, es gibt 2 Fenster."


def test_live_incident_gibt_es_tueren(engine):
    result = engine.match("gibt es Türen", WINDOW_DOOR_ENTITIES)
    assert result is not None
    assert result.response_text == "Ja, es gibt 1 Tür."


# --- Follow-up wave, Phase 4: state-filtered device queries ("Welche -------
# Geräte sind eingeschaltet im Büro?") - the aggregation rule is "at least 1
# of the device's entities matches", not "all of them" (user decision, see
# plan). LAMPE_BUERO (single entity, ON) proves the plain 1-device case;
# DOPPELSTECKDOSE_BUERO (two entities, only one ON) is the concrete proof of
# the "at least 1" rule itself, not just the 0/N cardinality split.

LAMPE_BUERO = EntitySnapshot(
    "light.lampe_buero", "Lampe", "light", "on", area_id="buero", area_name="Büro",
)
STECKDOSE_BUERO_A = EntitySnapshot(
    "switch.doppelsteckdose_a", "Doppelsteckdose A", "switch", "on",
    area_id="buero", area_name="Büro",
)
STECKDOSE_BUERO_B = EntitySnapshot(
    "switch.doppelsteckdose_b", "Doppelsteckdose B", "switch", "off",
    area_id="buero", area_name="Büro",
)
FERNSEHER_BUERO = EntitySnapshot(
    "switch.fernseher", "Fernseher", "switch", "off", area_id="buero", area_name="Büro",
)


def _world_model_with_state_filtered_devices():
    from homeintent.devices import DeviceSnapshot
    from homeintent.world_model import build_world_model

    entities = [LAMPE_BUERO, STECKDOSE_BUERO_A, STECKDOSE_BUERO_B, FERNSEHER_BUERO]
    devices = [
        DeviceSnapshot(
            device_id="device.lampe", name="Lampe", area_id="buero", area_name="Büro",
            entity_ids=("light.lampe_buero",),
        ),
        DeviceSnapshot(
            device_id="device.doppelsteckdose", name="Doppelsteckdose", area_id="buero", area_name="Büro",
            entity_ids=("switch.doppelsteckdose_a", "switch.doppelsteckdose_b"),
        ),
        DeviceSnapshot(
            device_id="device.fernseher", name="Fernseher", area_id="buero", area_name="Büro",
            entity_ids=("switch.fernseher",),
        ),
    ]
    return build_world_model(entities, devices)


def test_device_query_state_filter_lists_devices_with_at_least_one_matching_entity(engine):
    world_model = _world_model_with_state_filtered_devices()
    result = engine.match(
        "welche Geräte sind eingeschaltet im Büro",
        [LAMPE_BUERO, STECKDOSE_BUERO_A, STECKDOSE_BUERO_B, FERNSEHER_BUERO],
        world_model=world_model,
    )
    assert result is not None
    # Doppelsteckdose qualifies despite only one of its two entities being ON.
    assert result.response_text == "Lampe und Doppelsteckdose sind eingeschaltet."


def test_device_query_state_filter_empty_is_a_normal_answer(engine):
    entities = [FERNSEHER_BUERO]
    from homeintent.devices import DeviceSnapshot
    from homeintent.world_model import build_world_model

    world_model = build_world_model(
        entities,
        [DeviceSnapshot(
            device_id="device.fernseher", name="Fernseher", area_id="buero", area_name="Büro",
            entity_ids=("switch.fernseher",),
        )],
    )
    result = engine.match("welche Geräte sind eingeschaltet im Büro", entities, world_model=world_model)
    assert result is not None
    assert result.response_text == "Im Büro sind keine Geräte eingeschaltet."


def test_device_query_without_state_filter_still_uses_area_phrasing(engine):
    world_model = _world_model_with_state_filtered_devices()
    result = engine.match(
        "welche Geräte sind im Büro",
        [LAMPE_BUERO, STECKDOSE_BUERO_A, STECKDOSE_BUERO_B, FERNSEHER_BUERO],
        world_model=world_model,
    )
    assert result is not None
    assert result.response_text == "Lampe, Doppelsteckdose und Fernseher sind im Büro."
