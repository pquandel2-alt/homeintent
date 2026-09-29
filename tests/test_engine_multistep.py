"""HomeIntent plan V4.8, "Multi-Step Commands": "Mach das Licht an und fahr
die Rollläden hoch." -> ``CommandPlan(commands=(command1, command2))``.

Plan-mandated default: "validate entire plan first -> execute only if
complete plan is valid" - every "und"-joined action or query segment is
matched and validated through the normal single-command pipeline before
anything is returned; one failing or ambiguous segment fails the whole
sentence (``engine.match()`` returns ``None``), never a partial
``CommandPlan``.
"""

from __future__ import annotations

from homeintent.engine import CommandPlan, MatchResult
from homeintent.entities import EntitySnapshot

LAMP = EntitySnapshot(
    "light.wohnzimmer", "Wohnzimmerlampe", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
COVER = EntitySnapshot(
    "cover.buero", "Rollladen Büro", "cover", "closed",
    capabilities=frozenset({"POSITION"}),
)
WINDOW = EntitySnapshot(
    "binary_sensor.bad_fenster", "Badezimmer Fenster", "binary_sensor", "off",
    area_id="bad", area_name="Badezimmer", device_class="window",
)

ENTITIES = [LAMP, COVER, WINDOW]


def test_two_step_command_produces_command_plan(engine):
    result = engine.match("mach die Wohnzimmerlampe an und fahre den Rollladen Büro hoch", ENTITIES)
    assert isinstance(result, CommandPlan)
    assert len(result.commands) == 2

    first, second = result.commands
    assert first.plan.domain == "homeassistant"
    assert first.plan.service == "turn_on"
    assert first.plan.entity_id == "light.wohnzimmer"

    assert second.plan.domain == "cover"
    assert second.plan.service == "open_cover"
    assert second.plan.entity_id == "cover.buero"


def test_three_step_command_chain(engine):
    result = engine.match(
        "mach die Wohnzimmerlampe an und fahre den Rollladen Büro hoch und mach die Wohnzimmerlampe aus",
        ENTITIES,
    )
    assert isinstance(result, CommandPlan)
    assert len(result.commands) == 3
    assert result.commands[0].plan.service == "turn_on"
    assert result.commands[1].plan.service == "open_cover"
    assert result.commands[2].plan.service == "turn_off"


def test_each_command_individually_validated(engine):
    # Not just structurally split - each segment goes through the full
    # single-command pipeline (own entity resolution, own domain check).
    result = engine.match("mach die Wohnzimmerlampe an und fahre den Rollladen Büro hoch", ENTITIES)
    assert isinstance(result, CommandPlan)
    for sub in result.commands:
        assert sub.frame is not None
        assert sub.command is not None


def test_unresolvable_segment_fails_entire_plan_not_just_that_segment(engine):
    # "never guess"/"validate entire plan first": the first segment alone
    # would be perfectly valid, but the whole sentence must still fail
    # because the second segment doesn't resolve.
    result = engine.match("mach die Wohnzimmerlampe an und mach die Nichtvorhandene an", ENTITIES)
    assert result is None


def test_first_segment_failing_also_fails_entire_plan(engine):
    result = engine.match("mach die Nichtvorhandene an und mach die Wohnzimmerlampe an", ENTITIES)
    assert result is None


def test_action_and_read_only_query_form_one_validated_plan(engine):
    result = engine.match(
        "mach die Wohnzimmerlampe an und ist das Badezimmer Fenster geschlossen?",
        ENTITIES,
    )

    assert isinstance(result, CommandPlan)
    assert result.commands[0].plan.service == "turn_on"
    assert result.commands[1].plan is None
    assert "geschlossen" in result.commands[1].response_text


def test_action_and_query_on_same_entity_are_refused_as_stale(engine):
    result = engine.match(
        "mach die Wohnzimmerlampe an und ist die Wohnzimmerlampe an?", ENTITIES
    )

    assert result is None


def test_plain_sentence_without_und_still_returns_single_match_result(engine):
    # Regression: adding "und"-splitting must not affect ordinary
    # single-command sentences.
    result = engine.match("mach die Wohnzimmerlampe an", ENTITIES)
    assert isinstance(result, MatchResult)
    assert not isinstance(result, CommandPlan)
    assert result.plan.service == "turn_on"
