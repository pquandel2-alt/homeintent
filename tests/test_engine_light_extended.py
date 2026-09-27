"""Phase 14 (v2 plan): Licht-NLU erweitern - HassLightBrighten/HassLightDim/
HassLightSetColor/HassLightSetColorTemp, the engine's 4th separately-
compiled hassil grammar (see engine.py's _LIGHT_EXTENDED_RE routing,
checked before _PERCENT_RE since "20 Prozent heller" contains both
"Prozent" and "heller"). User-decided design (AskUserQuestion): the default
dimming step without an explicit percent is 10%, colour vocabulary is the
"Basis-Set" (rot/grün/blau/gelb/orange/lila/violett/weiß/pink/rosa/
türkis/cyan)."""

from __future__ import annotations

from homeintent.entities import EntitySnapshot

DIMMABLE_LIGHT = EntitySnapshot(
    "light.dimmbar", "Dimmbare Lampe", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
)
FULL_COLOR_LIGHT = EntitySnapshot(
    "light.vollfarbig", "Vollfarblampe", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS", "COLOR", "COLOR_TEMPERATURE"}),
)
PLAIN_LIGHT = EntitySnapshot(
    "light.einfach", "Einfache Lampe", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)

ENTITIES = [DIMMABLE_LIGHT, FULL_COLOR_LIGHT, PLAIN_LIGHT]


def test_brighten_without_percent_defaults_to_ten_percent(engine):
    result = engine.match("mach die Dimmbare Lampe heller", ENTITIES)
    assert result.plan.domain == "light"
    assert result.plan.service == "turn_on"
    assert result.plan.data == {"brightness_step_pct": 10}


def test_brighten_with_explicit_percent(engine):
    result = engine.match("mach die Dimmbare Lampe um 20 Prozent heller", ENTITIES)
    assert result.plan.data == {"brightness_step_pct": 20}


def test_dim_without_percent_defaults_to_ten_percent(engine):
    result = engine.match("mach die Dimmbare Lampe dunkler", ENTITIES)
    assert result.plan.data == {"brightness_step_pct": -10}


def test_dim_with_explicit_percent(engine):
    result = engine.match("mach die Dimmbare Lampe um 30 Prozent dunkler", ENTITIES)
    assert result.plan.data == {"brightness_step_pct": -30}


def test_degree_words_control_relative_brightness_step(engine):
    slight = engine.match("mach die Dimmbare Lampe etwas heller", ENTITIES)
    moderate = engine.match("mach die Dimmbare Lampe deutlich heller", ENTITIES)
    large = engine.match("mach die Dimmbare Lampe viel dunkler", ENTITIES)
    assert slight.plan.data == {"brightness_step_pct": 5}
    assert moderate.plan.data == {"brightness_step_pct": 15}
    assert large.plan.data == {"brightness_step_pct": -25}
    assert slight.frame.degree.name == "SLIGHT"
    assert moderate.frame.degree.name == "MODERATE"
    assert large.frame.degree.name == "LARGE"


def test_set_color(engine):
    result = engine.match("mach die Vollfarblampe rot", ENTITIES)
    assert result.plan.domain == "light"
    assert result.plan.service == "turn_on"
    assert result.plan.data == {"color_name": "red"}
    assert "rot" in result.response_text


def test_set_color_verb_after_form(engine):
    result = engine.match("stelle die Vollfarblampe auf blau", ENTITIES)
    assert result.plan.data == {"color_name": "blue"}


def test_set_color_temp_warm(engine):
    result = engine.match("mach die Vollfarblampe warmweiß", ENTITIES)
    assert result.plan.data == {"color_temp_kelvin": 2700}
    assert "warmweiß" in result.response_text


def test_set_color_temp_cold(engine):
    result = engine.match("stelle die Vollfarblampe auf kaltweiß", ENTITIES)
    assert result.plan.data == {"color_temp_kelvin": 6500}
    assert "kaltweiß" in result.response_text


def test_brighten_rejected_without_brightness_capability(engine):
    assert engine.match("mach die Einfache Lampe heller", ENTITIES) is None


def test_set_color_rejected_without_color_capability(engine):
    assert engine.match("mach die Dimmbare Lampe rot", ENTITIES) is None


def test_set_color_temp_rejected_without_color_temperature_capability(engine):
    assert engine.match("mach die Dimmbare Lampe warmweiß", ENTITIES) is None
