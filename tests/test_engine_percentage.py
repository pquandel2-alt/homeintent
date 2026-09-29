"""HassSetPercentage: percent-target control for cover position / light
brightness. One grammar for both - which service call applies is decided
by the *resolved entity's domain*, not by the verb (see PERCENT_INTENTS in
service_call.py). This was a deliberate redesign after the original
verb-split grammar (HassSetPosition vs. HassLightSet) turned out to reject
valid generic-verb phrasings ("Stelle die Rollade auf 30 Prozent") for
covers, since only the light grammar accepted "stelle"."""

from __future__ import annotations

import pytest

from homeintent.entities import EntitySnapshot


def test_fahre_rolllade_buro_auf_30_prozent_runter(engine, entities):
    # Regression case from the real-world requirement ("Fahre die Rolllade
    # im Büro auf 30 Prozent runter/hoch"), spelled here to match this
    # fixture's actual friendly name "Rollladen Büro" - the colloquial
    # "Rolllade" (missing the final n) not matching it is a pre-existing,
    # correct resolve_entity behaviour (no letter may go missing in a
    # contains-match), unrelated to percent support itself.
    result = engine.match("Fahre den Rollladen im Büro auf 30 Prozent runter", entities)
    assert result is not None
    assert result.plan.domain == "cover"
    assert result.plan.service == "set_cover_position"
    assert result.plan.entity_id == "cover.rolllade_buro"
    assert result.plan.data == {"position": 30}


def test_mach_licht_auf_50_prozent(engine, entities):
    result = engine.match("mach das Treppenlicht auf 50 Prozent", entities)
    assert result is not None
    assert result.plan.domain == "light"
    assert result.plan.service == "turn_on"
    assert result.plan.entity_id == "light.treppen_licht_gruppe"
    assert result.plan.data == {"brightness_pct": 50}


def test_setze_rollladen_auf_100_prozent(engine, entities):
    result = engine.match("setze den Rollladen Wohnzimmer auf 100 Prozent", entities)
    assert result is not None
    assert result.plan.entity_id == "cover.rolllade_wohnzimmer"
    assert result.plan.data == {"position": 100}


def test_stelle_rollladen_auf_prozent_generic_verb_works_for_cover(engine, entities):
    # Real-world regression: "stelle"/"mach" are generic German verbs used
    # for both covers and lights - a cover-position sentence using the
    # "light-flavoured" verb must still resolve correctly by domain.
    result = engine.match("Stelle den Rollladen Wohnzimmer auf 40 Prozent", entities)
    assert result is not None
    assert result.plan.domain == "cover"
    assert result.plan.service == "set_cover_position"
    assert result.plan.data == {"position": 40}


def test_percent_symbol_instead_of_word(engine, entities):
    # Real-world regression: users type "30%" in the Assist chat UI, not
    # the word "Prozent".
    result = engine.match("Stelle den Rollladen Wohnzimmer auf 30%", entities)
    assert result is not None
    assert result.plan.data == {"position": 30}


def test_percent_symbol_no_space(engine, entities):
    result = engine.match("mach das Treppenlicht auf 50%", entities)
    assert result is not None
    assert result.plan.data == {"brightness_pct": 50}


def test_trailing_verb_phrasing(engine, entities):
    # "kannst du <name> auf <percent> Prozent fahren/dimmen/einstellen/stellen"
    result = engine.match("kannst du den Rollladen Wohnzimmer auf 60 Prozent fahren", entities)
    assert result is not None
    assert result.plan.data == {"position": 60}


def test_bare_number_no_unit_word_means_percent(engine, entities):
    # HomeIntent plan V4.2 ("Number Normalization"): "50" alone must
    # normalize to the same semantic value as "50 Prozent"/"50%" - safe to
    # infer here since the resolved entity's domain (cover/light) has no
    # other numeric parameter this grammar could mean.
    result = engine.match("Stelle den Rollladen Wohnzimmer auf 50", entities)
    assert result is not None
    assert result.plan.data == {"position": 50}


def test_bare_number_trailing_verb_phrasing(engine, entities):
    result = engine.match("kannst du das Treppenlicht auf 30 dimmen", entities)
    assert result is not None
    assert result.plan.data == {"brightness_pct": 30}


@pytest.mark.parametrize(
    "text",
    [
        "Fahre alle Rolladen im Erdgeschoss auf 90%",
        "Stelle im Erdgeschoss alle Rollläden auf 90 Prozent",
        "Fahre die Rollläden im Erdgeschoss alle auf 90 Prozent",
    ],
)
def test_all_covers_on_floor_set_to_percentage(engine, text):
    entities = [
        EntitySnapshot(
            "cover.wohnzimmer", "Rollladen Wohnzimmer", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.kueche", "Rollladen Küche", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.buero", "Rollladen Büro", "cover", "closed",
            floor_id="og", floor_name="Obergeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    result = engine.match(text, entities)

    assert result is not None
    assert result.plan.domain == "cover"
    assert result.plan.service == "set_cover_position"
    assert sorted(result.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]
    assert result.plan.data == {"position": 90}
    assert result.response_text == "2 Rollläden auf 90 Prozent gefahren."


def test_both_percentage_targets_require_exactly_two_entities(engine):
    entities = [
        EntitySnapshot(
            f"cover.{index}", f"Rollladen {index}", "cover", "closed",
            capabilities=frozenset({"POSITION"}),
        )
        for index in range(3)
    ]
    assert engine.match("Fahre beide Rolladen auf 90 Prozent", entities) is None


def test_percentage_group_ignores_non_position_cover_on_same_floor(engine):
    entities = [
        EntitySnapshot(
            "cover.wohnzimmer", "Rollladen Wohnzimmer", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.kueche", "Rollladen Küche", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.garage", "Garagentor", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
        ),
    ]

    result = engine.match("Fahre die Rolladen im Erdgeschoss halb runter", entities)

    assert result is not None
    assert result.plan.service == "set_cover_position"
    assert sorted(result.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]
    assert result.plan.data == {"position": 50}


def test_percentage_group_explains_missing_floor_assignment(engine):
    entities = [
        EntitySnapshot(
            "cover.buero", "Rollladen Büro", "cover", "closed",
            area_id="buero", area_name="Büro",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    text = "Fahre alle Rolladen im Erdgeschoss halb runter"

    assert engine.match(text, entities) is None
    assert engine.failure_feedback(text, entities) == (
        "Ich kenne den Bereich oder die Etage „Erdgeschoss“ nicht. "
        "Bitte ordne die Geräte in Home Assistant einem Bereich und den Bereich einer Etage zu."
    )


def test_percentage_group_explains_when_floor_has_no_selected_covers(engine):
    entities = [
        EntitySnapshot(
            "light.flur", "Flurlicht", "light", "off",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"BRIGHTNESS"}),
        ),
    ]

    text = "Fahre alle Rolladen im Erdgeschoss halb runter"

    assert engine.match(text, entities) is None
    assert engine.failure_feedback(text, entities) == (
        "Ich finde keine für HomeIntent ausgewählten Rollläden in „Erdgeschoss“."
    )


def test_percentage_group_explains_unsupported_position_control(engine):
    entities = [
        EntitySnapshot(
            "cover.markise", "Markise", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
        ),
    ]

    text = "Fahre alle Rolladen im Erdgeschoss halb runter"

    assert engine.match(text, entities) is None
    assert engine.failure_feedback(text, entities) == (
        "Die Rollläden in „Erdgeschoss“ melden keine unterstützte Positionssteuerung."
    )


def test_single_cover_natural_fixed_positions(engine, entities):
    half = engine.match("Fahre den Rollladen Wohnzimmer halb runter", entities)
    assert half is not None
    assert half.plan.service == "set_cover_position"
    assert half.plan.data == {"position": 50}

    closed = engine.match("Fahre den Rollladen Wohnzimmer komplett runter", entities)
    assert closed is not None
    assert closed.plan.service == "close_cover"

    opened = engine.match("Fahre den Rollladen Wohnzimmer hoch", entities)
    assert opened is not None
    assert opened.plan.service == "open_cover"


def test_all_covers_on_floor_natural_fixed_positions(engine):
    entities = [
        EntitySnapshot(
            "cover.wohnzimmer", "Rollladen Wohnzimmer", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.kueche", "Rollladen Küche", "cover", "closed",
            floor_id="eg", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.buero", "Rollladen Büro", "cover", "closed",
            floor_id="og", floor_name="Obergeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    half = engine.match("Fahre alle Rolladen im Erdgeschoss halb runter", entities)
    assert half is not None
    assert half.plan.service == "set_cover_position"
    assert sorted(half.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]
    assert half.plan.data == {"position": 50}

    implicit_all = engine.match(
        "Fahre die Rolladen im Erdgeschoss halb runter", entities
    )
    assert implicit_all is not None
    assert implicit_all.plan.service == "set_cover_position"
    assert sorted(implicit_all.plan.entity_id) == [
        "cover.kueche", "cover.wohnzimmer"
    ]
    assert implicit_all.plan.data == {"position": 50}

    implicit_percent = engine.match(
        "Fahre die Rolladen im Erdgeschoss auf 90 Prozent", entities
    )
    assert implicit_percent is not None
    assert implicit_percent.plan.service == "set_cover_position"
    assert sorted(implicit_percent.plan.entity_id) == [
        "cover.kueche", "cover.wohnzimmer"
    ]
    assert implicit_percent.plan.data == {"position": 90}

    closed = engine.match(
        "Fahre alle Rolladen im Erdgeschoss komplett runter", entities
    )
    assert closed is not None
    assert closed.plan.service == "close_cover"
    assert sorted(closed.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]

    opened = engine.match("Fahre alle Rolladen im Erdgeschoss hoch", entities)
    assert opened is not None
    assert opened.plan.service == "open_cover"
    assert sorted(opened.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]


def test_floor_wins_over_same_named_area_for_percentage_group(engine):
    """Regression for a real HA setup containing both named Erdgeschoss."""
    entities = [
        EntitySnapshot(
            "sensor.erdgeschoss", "Temperatur Erdgeschoss", "sensor", "21.6",
            area_id="erdgeschoss", area_name="Erdgeschoss",
            floor_id="erdgeschoss", floor_name="Erdgeschoss",
        ),
        EntitySnapshot(
            "cover.wohnzimmer", "Rollladen Wohnzimmer", "cover", "open",
            area_id="wohnzimmer", area_name="Wohnzimmer",
            floor_id="erdgeschoss", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.kueche", "Rollladen Küche", "cover", "open",
            area_id="kueche", area_name="Küche",
            floor_id="erdgeschoss", floor_name="Erdgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    result = engine.match(
        "Fahre alle Rolladen im Erdgeschoss halb runter", entities
    )

    assert result is not None
    assert result.plan.service == "set_cover_position"
    assert sorted(result.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]
    assert result.plan.data == {"position": 50}


def test_short_imperative_percentage_group_on_dachgeschoss(engine):
    entities = [
        EntitySnapshot(
            "sensor.dachgeschoss", "Temperatur Dachgeschoss", "sensor", "22",
            area_id="dachgeschoss", area_name="Dachgeschoss",
            floor_id="dachgeschoss", floor_name="Dachgeschoss",
        ),
        EntitySnapshot(
            "cover.buero", "Rollladen Büro", "cover", "open",
            area_id="buero", area_name="Büro",
            floor_id="dachgeschoss", floor_name="Dachgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.studio", "Rollladen Studio", "cover", "open",
            area_id="studio", area_name="Studio",
            floor_id="dachgeschoss", floor_name="Dachgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    result = engine.match(
        "Fahr die Rolladen im Dachgeschoss halb runter", entities
    )

    assert result is not None
    assert result.plan.service == "set_cover_position"
    assert sorted(result.plan.entity_id) == ["cover.buero", "cover.studio"]
    assert result.plan.data == {"position": 50}


def test_percentage_group_semantics_accept_free_word_order(engine):
    entities = [
        EntitySnapshot(
            "cover.buero", "Rollladen Büro", "cover", "open",
            area_id="buero", area_name="Büro",
            floor_id="dg", floor_name="Dachgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.studio", "Rollladen Studio", "cover", "open",
            area_id="studio", area_name="Studio",
            floor_id="dg", floor_name="Dachgeschoss",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    result = engine.match(
        "Im Dachgeschoss stelle bitte alle vorhandenen Rolladen halb", entities
    )

    assert result is not None
    assert sorted(result.plan.entity_id) == ["cover.buero", "cover.studio"]
    assert result.plan.data == {"position": 50}


def test_bare_number_on_switch_domain_still_returns_none(engine, entities):
    # A bare number is only unambiguous for the two percent-capable domains
    # (cover/light) - switch has no percent semantics at all, same as the
    # "mach den Garagentor auf 50 Prozent" case below.
    assert engine.match("mach den Garagentor auf 50", entities) is None


@pytest.mark.parametrize(
    "text",
    [
        "mach das Unbekanntgerät auf 50 Prozent",  # unknown name
        "mach den Garagentor auf 50 Prozent",  # switch domain has no percent semantics
    ],
)
def test_unmatched_percentage_returns_none(engine, entities, text):
    assert engine.match(text, entities) is None


def test_verbless_target_with_unit_value_is_a_command(engine, entities):
    # 7.6.0: "<Ziel> auf <Zahl> Prozent" names target, value and unit; the
    # only reading is "set to", so the short form is a command (no guess).
    result = engine.match("Rollladen Wohnzimmer auf 50 Prozent", entities)
    assert result is not None and result.plan is not None
    assert result.plan.service == "set_cover_position"
    assert result.plan.data["position"] == 50
