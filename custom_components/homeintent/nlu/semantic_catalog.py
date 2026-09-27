"""Single declarative catalogue for reusable German semantic expressions.

Values in this module describe meanings, never complete sentences.  The
language frontend, semantic interpreter and drift tests consume the same
catalogue so a synonym cannot accidentally work only for one router.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain_operations import (
    ACTION_EXPRESSIONS,
    COMMAND_MARKER_EXPRESSIONS,
    DOMAIN_EXPRESSIONS,
    DOMAIN_WORDS,
    INTENT_BY_DOMAIN_ACTION,
    regex_union,
)
from .semantic_state import QUERYABLE_STATE_DOMAINS, SemanticState


@dataclass(frozen=True)
class CatalogueEntry:
    value: object
    expressions: tuple[str, ...]


DEVICE_CLASS_ENTRIES = (
    CatalogueEntry(("binary_sensor", "window"), (r"fenster(?:n|kontakte?)?",)),
    CatalogueEntry(("binary_sensor", "door"), (r"tür(?:en)?",)),
    CatalogueEntry(("binary_sensor", "garage_door"), (r"garagentor(?:e)?",)),
    CatalogueEntry(
        ("binary_sensor", "motion"),
        (r"bewegungsmelder", r"bewegungssensor(?:en)?"),
    ),
)

# German "ein" is both the separable particle of "einschalten" ("schalte das
# Licht ein") and the indefinite article ("wenn ein Fenster geöffnet wird").
# Directly in front of a device noun it is always the article, so it must not
# contribute an ON state there.  Likewise "auf" is a preposition everywhere
# except as a predicate before a copula ("wenn das Fenster auf ist").
_DEVICE_NOUN = regex_union([
    expression
    for entry in DEVICE_CLASS_ENTRIES
    for expression in entry.expressions
] + [
    expression
    for expressions in DOMAIN_EXPRESSIONS.values()
    for expression in expressions
])
_COPULA = r"(?:ist|sind|steht|stehen|bleibt|bleiben)"

STATE_ENTRIES = (
    CatalogueEntry(
        SemanticState.OPEN,
        (
            r"offen\w*",
            r"geöffnet\w*",
            r"hochgefahren\w*",
            r"oben",
            r"aufgeh(?:t|en)",
            r"aufgegangen",
            r"aufgemacht",
            r"auf(?=\s+" + _COPULA + r"\b)",
        ),
    ),
    CatalogueEntry(
        SemanticState.CLOSED,
        (
            r"geschlossen\w*",
            r"runtergefahren\w*",
            r"heruntergefahren\w*",
            r"unten",
            r"zu",
            r"zugeh(?:t|en)",
            r"zugemacht",
        ),
    ),
    CatalogueEntry(
        SemanticState.ON,
        (
            r"an",
            r"ein(?!\s+" + _DEVICE_NOUN + r"\b)",
            r"eingeschaltet\w*",
            r"angeschaltet\w*",
        ),
    ),
    CatalogueEntry(SemanticState.OFF, (r"aus", r"ausgeschaltet\w*")),
    CatalogueEntry(
        SemanticState.ACTIVE,
        (r"läuft", r"laeuft", r"laufend", r"aktiv", r"in\s+betrieb"),
    ),
    CatalogueEntry(
        SemanticState.INACTIVE,
        (r"inaktiv", r"steht", r"gestoppt", r"pausiert"),
    ),
)

# Resultative complements that describe a state an object should *keep*
# after a verb of letting/leaving ("lass das Licht an", "lass die Tür zu").
# Keys are compare-normalized single words; the value is the state that is
# preserved.  Directional particles ("runter", "herunter", "hoch") are not
# listed: "lass die Rollläden runter" is the separable verb herunterlassen,
# i.e. an operation, not a maintenance.
STATE_COMPLEMENT_WORDS: dict[str, SemanticState] = {
    "an": SemanticState.ON,
    "eingeschaltet": SemanticState.ON,
    "brennen": SemanticState.ON,
    "laufen": SemanticState.ACTIVE,
    "zu": SemanticState.CLOSED,
    "geschlossen": SemanticState.CLOSED,
    "unten": SemanticState.CLOSED,
    "auf": SemanticState.OPEN,
    "offen": SemanticState.OPEN,
    "geoeffnet": SemanticState.OPEN,
    "oben": SemanticState.OPEN,
    "so": SemanticState.UNKNOWN,
}

# Imperative/hortative forms of "lassen" (to leave/keep).  "lass uns" is
# the first-person hortative ("let us ...") and never a maintenance.
MAINTAIN_VERB_FORMS = frozenset({"lass", "lasse", "lasst", "lassen"})
MAINTAIN_BLOCKING_OBJECTS = frozenset({"uns", "mich"})

QUANTIFIER_ENTRIES = (
    CatalogueEntry("all", (r"alle\w*", r"sämtliche\w*", r"jede\w*", r"überall", r"die\s+ganzen")),
    CatalogueEntry("both", (r"beide\w*",)),
    CatalogueEntry("any", (r"irgendein\w*", r"mindestens\s+ein\w*")),
    CatalogueEntry("none", (r"kein\w*", r"keiner\s+davon")),
    CatalogueEntry("some", (r"einige\w*", r"mehrere\w*")),
)

QUERY_SCOPE_ENTRIES = (
    CatalogueEntry("count", (r"wie\s+viele",)),
    CatalogueEntry(
        "locations", (
            r"wo",
            r"in\s+welchen\s+(?:räumen|zimmern)",
            r"welche\s+(?:räume|zimmer)",
        )
    ),
    CatalogueEntry(
        "exists", (r"gibt\s+es", r"haben\s+wir", r"irgendein\w*", r"irgendwelche")
    ),
    CatalogueEntry("none", (r"kein\w*", r"ohne")),
    CatalogueEntry("average", (r"durchschnitt\w*",)),
    CatalogueEntry(
        "measurement", (r"wie\s+viel", r"aktuell\w*", r"momentan", r"gerade")
    ),
)

AUTOMATION_CUE_ENTRIES = (
    CatalogueEntry("predicate", (r"wenn", r"sobald", r"falls", r"sofern", r"nur\s+wenn")),
)

PROPERTY_ENTRIES = (
    CatalogueEntry(
        "temperature",
        (r"temperatur\w*", r"wärme", r"warm", r"kalt", r"wärm\w*", r"waerm\w*", r"kält\w*", r"kaelt\w*", r"grad"),
    ),
    CatalogueEntry(
        "humidity",
        (r"luftfeuchtigkeit\w*", r"feuchtigkeit\w*", r"feucht\w*"),
    ),
    CatalogueEntry("battery", (r"batteriestand", r"batterie", r"batterien")),
    CatalogueEntry("power", (r"leistung", r"strom(?:aufnahme)?")),
    CatalogueEntry(
        "energy", (r"stromverbrauch", r"energieverbrauch", r"energie")
    ),
    CatalogueEntry("brightness", (r"helligkeit", r"hell", r"heller", r"dunkler")),
    CatalogueEntry("speed", (r"stufe", r"geschwindigkeit", r"schneller", r"langsamer")),
    CatalogueEntry(
        "color",
        (r"rot", r"grün", r"blau", r"gelb", r"orange", r"lila", r"violett", r"weiß", r"pink", r"rosa", r"türkis", r"cyan", r"warmweiß", r"kaltweiß"),
    ),
    CatalogueEntry("position", (r"position", r"höhe", r"oeffnung", r"öffnung")),
    CatalogueEntry("volume", (r"lautstärke", r"lautstaerke", r"laut", r"leise")),
)

COMPARATOR_ENTRIES = (
    CatalogueEntry(
        "lt",
        (
            r"unter", r"weniger\s+als", r"dunkler\s+als",
            r"kälter\s+als", r"kaelter\s+als", r"niedriger\s+als",
        ),
    ),
    CatalogueEntry(
        "gt",
        (
            r"über", r"mehr\s+als", r"heller\s+als",
            r"wärmer\s+als", r"waermer\s+als", r"höher\s+als", r"hoeher\s+als",
        ),
    ),
    CatalogueEntry("lte", (r"höchstens", r"maximal", r"nicht\s+höher\s+als")),
    CatalogueEntry("gte", (r"mindestens", r"wenigstens", r"\w+\s+oder\s+mehr")),
    CatalogueEntry("eq", (r"genau",)),
)

RELATION_ENTRIES = (
    CatalogueEntry("same_area", (r"(?:im\s+)?selben\s+raum\s+wie", r"gleichen\s+raum\s+wie")),
)

MEASUREMENT_PROPERTY_SPECS = {
    "temperature": ("sensor", "temperature", "Temperatur", "temperatur"),
    "humidity": ("sensor", "humidity", "Luftfeuchtigkeit", "luftfeuchtigkeit"),
    "battery": ("sensor", "battery", "Batteriestand", "batterie"),
    "power": ("sensor", "power", "Leistung", "leistung"),
    "energy": ("sensor", "energy", "Energieverbrauch", "energieverbrauch"),
    "brightness": ("light", None, "Helligkeit", "helligkeit"),
}

# Typed numeric command properties.  This is the sole registry connecting a
# domain/property pair with the executable intent it may produce.
VALUE_INTENT_BY_DOMAIN_PROPERTY = {
    ("cover", "position"): "HassSetPercentage",
    ("light", "brightness"): "HassSetPercentage",
    ("climate", "temperature"): "HassClimateSetTemperature",
}

# Literal canonical surfaces used only for bounded spelling candidates. Regex
# expressions above remain the semantic source; this set intentionally lists
# their ordinary dictionary forms, not sentence shapes.
CANONICAL_SPELLING_FORMS = frozenset({
    "anmachen", "ausmachen", "einschalten", "ausschalten", "umschalten",
    "öffnen", "schließen", "hochfahren", "runterfahren", "herunterfahren",
    "stellen", "setzen", "starten", "stoppen", "pausieren", "spielen",
    "aktivieren", "deaktivieren", "erhöhen", "senken", "dimmen",
    "temperatur", "helligkeit", "lautstärke", "luftfeuchtigkeit", "position",
    "prozent", "automatik", "wiedergabe",
    *(word for words in DOMAIN_WORDS.values() for word in words if " " not in word),
})

# Tokens ignored while registry names are ranked.  These are semantic
# operation/value words, not sentence templates.  Keeping them here prevents
# the entity resolver and compiler from growing independent action lists.
SEMANTIC_RESOLUTION_WORDS = frozenset({
    "mache", "machen", "mach", "schalte", "schalten", "stelle", "stellen",
    "setze", "setzen", "fahre", "fahren", "fahr", "gefahren", "öffne",
    "öffnen", "schließe", "schließen", "drehe", "drehen", "lass", "lassen",
    "hoch", "runter", "herunter", "halb", "halbe", "halber", "halben",
    "hälfte", "höhe", "prozent", "komplett", "ganz", "vollständig",
    "einschalten", "ausschalten", "anmachen", "ausmachen", "aktivieren",
    "aktiviere", "starten", "starte", "ausführen", "führe", "umschalten",
    "toggle", "spiele", "spielen", "spiel", "weiter", "weiterspielen",
    "pausiere", "pausieren", "pausiert", "halte", "halten", "stoppe",
    "stoppen", "gestoppt",
})

# Entries become authoritative only after the versioned shadow report proves
# equivalence for their complete compositional matrix.  Keeping every pair
# explicit makes the migration boundary reviewable and prevents a newly
# registered operation from becoming authoritative by accident.
V7_AUTHORITATIVE_COMMAND_CAPABILITIES = frozenset({
    ("light", "HassTurnOn"),
    ("light", "HassTurnOff"),
    ("switch", "HassTurnOn"),
    ("switch", "HassTurnOff"),
    ("fan", "HassTurnOn"),
    ("fan", "HassTurnOff"),
    ("cover", "HassOpenCover"),
    ("cover", "HassCloseCover"),
    ("climate", "HassTurnOn"),
    ("climate", "HassTurnOff"),
    ("media_player", "HassMediaPlay"),
    ("media_player", "HassMediaPause"),
    ("vacuum", "HassVacuumStart"),
    ("vacuum", "HassVacuumStop"),
    ("humidifier", "HassTurnOn"),
    ("humidifier", "HassTurnOff"),
    ("valve", "HassOpenValve"),
    ("valve", "HassCloseValve"),
    ("input_boolean", "HassTurnOn"),
    ("input_boolean", "HassTurnOff"),
    ("scene", "HassActivateScene"),
    ("cover", "HassSetPercentage"),
    ("light", "HassSetPercentage"),
    ("climate", "HassClimateSetTemperature"),
})
_V7_AUTHORITATIVE_STATE_QUERY_INTENTS = frozenset({
    "HassCheckState",
    "HassStateQuery",
    "HassExistsQuery",
})
V7_AUTHORITATIVE_QUERY_CAPABILITIES = frozenset({
    *((domain, intent) for domain in QUERYABLE_STATE_DOMAINS
      for intent in _V7_AUTHORITATIVE_STATE_QUERY_INTENTS),
    *((domain, "HassVerbStateQuery") for domain in QUERYABLE_STATE_DOMAINS),
    ("sensor", "HassGetState"),
    ("sensor", "HassLocationPropertyQuery"),
    ("light", "HassGetState"),
    ("light", "HassLocationPropertyQuery"),
})
V7_AUTHORITATIVE_DIRECT_CAPABILITIES = frozenset({
    *V7_AUTHORITATIVE_COMMAND_CAPABILITIES,
    *V7_AUTHORITATIVE_QUERY_CAPABILITIES,
})
V7_AUTHORITY_MIN_MARGIN = 10.0
V7_ENTITY_AMBIGUITY_MARGIN = 5


def catalogue_expression_groups() -> dict[str, tuple[CatalogueEntry, ...]]:
    """Return every non-domain semantic group for tooling and drift checks."""
    return {
        "device_class": DEVICE_CLASS_ENTRIES,
        "state": STATE_ENTRIES,
        "quantifier": QUANTIFIER_ENTRIES,
        "query_scope": QUERY_SCOPE_ENTRIES,
        "automation_cue": AUTOMATION_CUE_ENTRIES,
        "property": PROPERTY_ENTRIES,
        "comparator": COMPARATOR_ENTRIES,
        "relation": RELATION_ENTRIES,
    }


__all__ = [
    "ACTION_EXPRESSIONS",
    "AUTOMATION_CUE_ENTRIES",
    "COMMAND_MARKER_EXPRESSIONS",
    "COMPARATOR_ENTRIES",
    "RELATION_ENTRIES",
    "CatalogueEntry",
    "CANONICAL_SPELLING_FORMS",
    "DEVICE_CLASS_ENTRIES",
    "DOMAIN_EXPRESSIONS",
    "DOMAIN_WORDS",
    "INTENT_BY_DOMAIN_ACTION",
    "MEASUREMENT_PROPERTY_SPECS",
    "PROPERTY_ENTRIES",
    "QUANTIFIER_ENTRIES",
    "QUERY_SCOPE_ENTRIES",
    "SEMANTIC_RESOLUTION_WORDS",
    "STATE_ENTRIES",
    "STATE_COMPLEMENT_WORDS",
    "MAINTAIN_VERB_FORMS",
    "MAINTAIN_BLOCKING_OBJECTS",
    "V7_AUTHORITATIVE_DIRECT_CAPABILITIES",
    "V7_AUTHORITATIVE_COMMAND_CAPABILITIES",
    "V7_AUTHORITATIVE_QUERY_CAPABILITIES",
    "V7_AUTHORITY_MIN_MARGIN",
    "V7_ENTITY_AMBIGUITY_MARGIN",
    "VALUE_INTENT_BY_DOMAIN_PROPERTY",
    "catalogue_expression_groups",
    "regex_union",
]
