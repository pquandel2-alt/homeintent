"""Issue #38 regressions for the shared place and target model."""

from __future__ import annotations

from homeintent.entities import EntitySnapshot
from homeintent.nlu.place_model import PlaceKind, build_place_lexicon
from homeintent.nlu.semantic_location import resolve_semantic_place, whole_home_phrase
from homeintent.nlu.target_resolution import hidden_name_mentions


def _entity(
    entity_id: str,
    name: str,
    domain: str,
    state: str,
    area_id: str,
    area_name: str,
    *,
    floor_id: str = "eg",
    floor_name: str = "Erdgeschoss",
    area_aliases: tuple[str, ...] = (),
    floor_aliases: tuple[str, ...] = ("EG",),
    capabilities: tuple[str, ...] = (),
) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id,
        name,
        domain,
        state,
        area_id=area_id,
        area_name=area_name,
        floor_id=floor_id,
        floor_name=floor_name,
        area_aliases=area_aliases,
        floor_aliases=floor_aliases,
        capabilities=frozenset(capabilities),
    )


def issue_38_house() -> list[EntitySnapshot]:
    entities = [
        _entity(
            f"cover.buero_{side}",
            f"Rollladen Büro {side}",
            "cover",
            "open",
            "buero",
            "Büro",
            capabilities=("POSITION",),
        )
        for side in ("links", "mitte", "rechts")
    ]
    entities.extend(
        (
            _entity("sensor.buero", "Büro", "sensor", "1", "buero", "Büro"),
            _entity(
                "light.kuechenlicht",
                "Küchenlicht",
                "light",
                "on",
                "kueche",
                "Küche",
                capabilities=(
                    "TURN_ON",
                    "TURN_OFF",
                    "BRIGHTNESS",
                    "COLOR",
                    "COLOR_TEMPERATURE",
                ),
            ),
            _entity(
                "light.farblicht",
                "Farblicht",
                "light",
                "on",
                "kueche",
                "Küche",
                capabilities=("TURN_ON", "TURN_OFF", "BRIGHTNESS", "COLOR"),
            ),
            _entity(
                "cover.esszimmer",
                "Esszimmerrollladen",
                "cover",
                "closed",
                "esszimmer",
                "Esszimmer",
                capabilities=("POSITION",),
            ),
            _entity(
                "cover.garage",
                "Garagenrollladen",
                "cover",
                "open",
                "garage",
                "Garage",
                floor_id="aussen",
                floor_name="Außen",
                floor_aliases=("Draußen",),
                capabilities=("POSITION",),
            ),
            _entity(
                "light.garten",
                "Gartenlicht",
                "light",
                "on",
                "garten",
                "Garten",
                floor_id="aussen",
                floor_name="Außen",
                area_aliases=("Außenbereich",),
                floor_aliases=("Draußen",),
                capabilities=("TURN_ON", "TURN_OFF", "BRIGHTNESS"),
            ),
            _entity(
                "light.wohnzimmer",
                "Wohnzimmerlicht",
                "light",
                "on",
                "wohnzimmer",
                "Wohnzimmer",
                capabilities=("TURN_ON", "TURN_OFF", "BRIGHTNESS"),
            ),
        )
    )
    return entities


def _ids(result) -> set[str]:
    assert result is not None
    assert result.command is not None
    return {entity.entity_id for entity in result.command.entities}


def test_house_indoor_outdoor_and_floor_aliases_share_one_scope(engine):
    entities = issue_38_house()
    cases = {
        "Wie viele Lichter sind im Haus an?": 4,
        "Wie viele Lichter sind im ganzen Haus an?": 4,
        "Wie viele Lichter sind überall an?": 4,
        "Wie viele Lichter sind zu Hause an?": 4,
        "Wie viele Lichter sind draußen an?": 1,
        "Wie viele Lichter sind drinnen an?": 3,
        "Wie viele Lichter sind im Außenbereich an?": 1,
        "Wie viele Lichter sind im EG an?": 3,
    }

    for question, count in cases.items():
        result = engine.match(question, entities)
        assert result is not None, question
        assert result.plan is None, question
        assert result.response_text.startswith(str(count)), question


def test_home_scope_does_not_consume_a_presence_predicate():
    entities = issue_38_house()
    lexicon = build_place_lexicon(entities)

    assert lexicon.scan("jemand zuhause ist".split()) == ()
    assert lexicon.scan("zuhause ist".split()) == ()
    resolved = resolve_semantic_place("Lichter zuhause an", entities)
    assert resolved is not None
    assert resolved[1].kind is PlaceKind.HOUSE
    assert whole_home_phrase("wenn jemand zuhause ist", entities) is None
    assert whole_home_phrase("Ich bin zu Hause, mach das Licht an.", entities) is None
    assert whole_home_phrase("Wir sind zuhause.", entities) is None
    assert whole_home_phrase("Wenn ich zu Hause bin, mach das Licht an.", entities) is None
    assert lexicon.scan("ich bin zu hause mach das licht an".split()) == ()
    assert lexicon.scan("wenn wir zuhause sind mach das licht an".split()) == ()
    assert whole_home_phrase("Wie viele Lichter sind zu Hause an?", entities) == "zu Hause"
    assert whole_home_phrase("Öffne zu Hause das Garagentor.", entities) == "zu Hause"


def test_whole_home_quantifier_keeps_the_spoken_cover_kind(engine):
    entities = [
        EntitySnapshot(
            "cover.rollladen",
            "Rollladen Wohnzimmer",
            "cover",
            "closed",
            device_class="shutter",
            capabilities=frozenset({"OPEN", "CLOSE", "POSITION"}),
        ),
        EntitySnapshot(
            "cover.garagentor",
            "Garagentor",
            "cover",
            "closed",
            device_class="garage",
            capabilities=frozenset({"OPEN", "CLOSE", "POSITION"}),
        ),
        EntitySnapshot(
            "cover.markise",
            "Markise",
            "cover",
            "closed",
            device_class="awning",
            capabilities=frozenset({"OPEN", "CLOSE", "POSITION"}),
        ),
        EntitySnapshot(
            "cover.fenster",
            "Terrassenfenster",
            "cover",
            "closed",
            device_class="window",
            capabilities=frozenset({"OPEN", "CLOSE", "POSITION"}),
        ),
        EntitySnapshot("binary_sensor.fenster", "Fensterkontakt", "binary_sensor", "off"),
    ]

    cases = {
        "Öffne zu Hause alle Rollläden.": {"cover.rollladen"},
        "Öffne zu Hause alle Garagentore.": {"cover.garagentor"},
        "Öffne zu Hause alle Markisen.": {"cover.markise"},
        "Öffne zu Hause alle Fenster.": {"cover.fenster"},
    }
    for sentence, expected in cases.items():
        assert _ids(engine.match(sentence, entities)) == expected, sentence


def test_whole_home_unknown_typed_target_never_falls_back_to_domain(engine):
    entities = [
        EntitySnapshot(
            "cover.garagentor",
            "Garagentor",
            "cover",
            "closed",
            device_class="garage",
            capabilities=frozenset({"OPEN", "CLOSE"}),
        )
    ]

    assert engine.match("Öffne zu Hause alle Rollläden.", entities) is None


def test_locative_cover_genus_is_never_widened_outside_the_office(engine):
    entities = issue_38_house()

    singular = engine.match("Rollladen im Büro runter", entities)
    assert singular is not None
    assert singular.plan is None
    assert singular.clarification is not None
    assert {item.entity_id for item in singular.clarification.candidates} == {
        "cover.buero_links",
        "cover.buero_mitte",
        "cover.buero_rechts",
    }

    expected = {"cover.buero_links", "cover.buero_mitte", "cover.buero_rechts"}
    for command in (
        "Rollläden im Büro auf 50 Prozent",
        "Alle Rollläden im Büro runter",
    ):
        assert _ids(engine.match(command, entities)) == expected


def test_out_of_scope_entities_do_not_change_a_locative_resolution(engine):
    entities = issue_38_house()
    sentence = "Alle Rollläden im Büro runter"
    before = _ids(engine.match(sentence, entities))
    expanded = [
        *entities,
        *(
            _entity(
                f"cover.fremd_{index}",
                f"Rollladen Garage {index}",
                "cover",
                "open",
                "garage",
                "Garage",
                capabilities=("POSITION",),
            )
            for index in range(20)
        ),
    ]

    assert _ids(engine.match(sentence, expanded)) == before


def test_complete_cover_name_and_compounds_keep_exact_precedence(engine):
    entities = issue_38_house()
    exact = engine.match("Rollladen Büro rechts auf 50 Prozent", entities)
    compound = engine.match("Fahr den Rollladen im Esszimmer wieder hoch", entities)

    assert _ids(exact) == {"cover.buero_rechts"}
    assert _ids(compound) == {"cover.esszimmer"}


def test_elliptical_light_properties_require_an_exact_grounded_name(engine):
    entities = issue_38_house()
    cases = {
        "Küchenlicht blau": ("light.kuechenlicht", "blue"),
        "Küchenlicht heller": ("light.kuechenlicht", 10),
        "Küchenlicht 50 Prozent": ("light.kuechenlicht", 50),
        "Küchenlicht warmweiß": ("light.kuechenlicht", 2700),
        "Farblicht in der Küche heller": ("light.farblicht", 10),
    }

    for sentence, (entity_id, expected_value) in cases.items():
        result = engine.match(sentence, entities)
        assert _ids(result) == {entity_id}, sentence
        assert result.plan is not None
        assert expected_value in result.plan.data.values(), sentence

    assert engine.match("Küchenlicht blau gefährlich", entities) is None


def test_hidden_place_name_is_ignored_only_inside_a_grounded_place_span():
    exposed = issue_38_house()

    assert hidden_name_mentions(
        "Alle Rollläden im Büro runter", exposed, [("sensor", "Büro")]
    ) == ()
    assert hidden_name_mentions(
        "Wie viele Lichter sind im Haus an?", exposed, [("switch", "Haus")]
    ) == ()
    assert hidden_name_mentions(
        "Schalte Haus ein", exposed, [("switch", "Haus")]
    ) == ("Haus",)
