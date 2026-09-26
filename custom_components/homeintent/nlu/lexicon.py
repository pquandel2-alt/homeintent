"""Semantic Lexicon: the closed-vocabulary ``hassil`` ``TextSlotList``
definitions shared by ``parsers.py``'s grammars (domains, colors, comparison
operators, temporal expressions, states, ...).

First building block of the planned "Semantic Lexicon" (V6 architecture plan,
V6.3) - a single place for all vocabulary lists instead of them being
scattered across ``parsers.py``. Purely additive/extraction: the lists below
are moved here verbatim (same values, same order, same tuples) from
``parsers.py``, which now imports them from here instead of defining them
itself.

Note this is a deliberate, narrow exception to the "no hassil imports in
nlu/" boundary the rest of this package follows (see nlu/frame.py's
docstring): this module holds only inert vocabulary data, not pipeline
logic, so importing the ``TextSlotList`` container type here doesn't pull
hassil's matching/recognition machinery into the parser-agnostic layers.
"""

from __future__ import annotations

from hassil import TextChunk, TextSlotList

from .primitives import SemanticProperty, SemanticQuantity

# German word -> canonical HA domain, including the "Rolladen" (one l)
# misspelling seen in real user speech.
#
# Plural forms are listed *before* their singular counterpart wherever the
# singular is a literal text prefix of the plural (Licht/Lichter, Lampe/
# Lampen, Steckdose/Steckdosen, Rollo/Rollos, Ventilator/Ventilatoren): hassil
# matches TextSlotList values in declaration order and doesn't require a
# trailing word boundary, so with the singular listed first "Lichter" would
# match as "Licht" + a leftover "er" that silently gets swallowed by the
# {area} wildcard in the same sentence - the bug that broke the {level}
# ("oben"/"unten") sentences in Phase 28 until this reorder.
_DOMAIN_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Lichter", "light"), ("Licht", "light"), ("Lampen", "light"), ("Lampe", "light"),
        ("Schalter", "switch"), ("Steckdosen", "switch"), ("Steckdose", "switch"),
        ("Rollladen", "cover"), ("Rollläden", "cover"), ("Rolladen", "cover"), ("Rolläden", "cover"),
        ("Rollos", "cover"), ("Rollo", "cover"),
        ("Ventilatoren", "fan"), ("Ventilator", "fan"),
    ],
    name="domain",
)

# Maps straight to "all"/"both" (rather than the raw German word) so
# QuantifierParser can apply the "beide" == exactly 2 hits rule directly.
# "nur" (v2 plan Phase 29, "Natural Quantifiers") also maps to "all" - "nur
# die Wohnzimmerlampen" restricts to a domain/area filter exactly like
# "alle", it just doesn't additionally claim "every matching light in the
# house" the way a bare "alle" does; the engine can't tell the two apart
# once the filter is applied, so there's no separate "only" kind.
_QUANTIFIER_SLOT_LIST = TextSlotList.from_tuples(
    [("alle", "all"), ("beide", "both"), ("beiden", "both"), ("beider", "both"), ("nur", "all")],
    name="quantifier",
)

# "zwei".."zehn" (v2 plan Phase 29) generalizes "beide"'s exact-count rule
# ("beide" == exactly 2) to arbitrary N ("die drei Lichter" == exactly 3).
# Same fixed, closed-vocabulary pattern as the slot lists above - string
# values (cast to int in QuantifierParser.parse()), same precedent
# _COLOR_TEMP_SLOT_LIST already established.
_COUNT_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("zwei", "2"), ("drei", "3"), ("vier", "4"), ("fünf", "5"),
        ("sechs", "6"), ("sieben", "7"), ("acht", "8"), ("neun", "9"), ("zehn", "10"),
    ],
    name="count",
)

# "oben"/"unten" (v2 plan Phase 28, "Hierarchische Orte") - a fixed, closed
# vocabulary like domain/quantifier above, not a {area}-style wildcard: these
# words never take a preposition ("mach alle Lichter oben an", not "... im
# oben an"), so they get their own slot/sentence shape rather than being
# folded into the {area} grammar.
_LEVEL_SLOT_LIST = TextSlotList.from_tuples(
    [("oben", "up"), ("unten", "down")],
    name="level",
)

# German colour words -> HA's native `color_name` service parameter (CSS
# colour names, so HA itself validates/converts them - not RGB triples
# invented here). "Basis-Set" per user decision (v2 plan Phase 14).
_COLOR_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("rot", "red"),
        ("grün", "green"),
        ("blau", "blue"),
        ("gelb", "yellow"),
        ("orange", "orange"),
        ("lila", "purple"),
        ("violett", "purple"),
        ("weiß", "white"),
        ("pink", "pink"),
        ("rosa", "pink"),
        ("türkis", "turquoise"),
        ("cyan", "cyan"),
    ],
    name="color",
)

# String values (cast to int by LightExtendedParser), same pattern as the
# domain/quantifier slot lists above - standard lighting-industry Kelvin
# reference values, not a UX decision requiring user input.
_COLOR_TEMP_SLOT_LIST = TextSlotList.from_tuples(
    [("warmweiß", "2700"), ("kaltweiß", "6500")],
    name="color_temp",
)

# "mindestens"/"höchstens"/"nicht höher als"/"über"/"unter" (HomeIntent plan
# V4.6, "Comparisons") -> Comparison.operator. "nicht höher als" is a
# multi-word text key - hassil's TextSlotList.from_tuples supports this
# (empirically verified against hassil==3.11.0 before this list was written,
# same discipline as _COLOR_TEMP_SLOT_LIST/_DOMAIN_SLOT_LIST). Both
# "höchstens" and "nicht höher als" map to "lte" - two spoken phrasings, one
# operator, same precedent _QUANTIFIER_SLOT_LIST's "nur"->"all" already set.
#
# "heller als"/"dunkler als"/"mehr als"/"weniger als" (V4.2 spec gap #5,
# "Welche Lampen sind heller als 70 Prozent?") map onto the same "gt"/"lt"
# operators "über"/"unter" already use - "heller"/"dunkler" are brightness-
# specific synonyms for "more"/"less" that read naturally in a percent
# comparison, not a new comparison semantic. engine.py's
# _COMPARISON_QUERY_RE requires "welche" + one of these literal phrases
# together, checked before _LIGHT_EXTENDED_RE, so "heller als" here can't
# collide with the plain "heller" (no "als") LightExtendedParser keyword.
_COMPARATOR_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("mindestens", "gte"),
        ("höchstens", "lte"),
        ("nicht höher als", "lte"),
        ("über", "gt"),
        ("unter", "lt"),
        ("heller als", "gt"),
        ("mehr als", "gt"),
        ("dunkler als", "lt"),
        ("weniger als", "lt"),
    ],
    name="comparator",
)

# Dedicated {domain} vocabulary for ComparisonQueryParser's grammar only -
# not merged into _DOMAIN_SLOT_LIST (shared by QuantifierParser/
# ReferenceParser for every other command), since "Heizung"/"Raum" as
# synonyms for the climate domain only make sense in a comparison-query
# context ("welche Räume sind unter 20 Grad") and adding them to the shared
# list would silently enable "mach alle Räume an" etc. elsewhere - out of
# V4.6's scope. Plural before singular, same reordering reason
# _DOMAIN_SLOT_LIST's docstring documents.
_COMPARISON_DOMAIN_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Lichter", "light"), ("Licht", "light"), ("Lampen", "light"), ("Lampe", "light"),
        ("Rollläden", "cover"), ("Rollladen", "cover"), ("Rolladen", "cover"), ("Rolläden", "cover"),
        ("Rollos", "cover"), ("Rollo", "cover"),
        ("Heizungen", "climate"), ("Heizung", "climate"),
        ("Thermostate", "climate"), ("Thermostat", "climate"),
        ("Räume", "climate"), ("Raum", "climate"),
    ],
    name="domain",
)

# "in"/"für" (HomeIntent plan V4.7, "Temporal Expressions") -> which of the
# two numeric temporal kinds a sentence expresses. Both share the same
# {amount}/{unit} slots ("in fünf Minuten" vs. "für eine Stunde" only differ
# in this one preposition), so capturing it as its own closed-vocabulary slot
# - rather than duplicating the sentence per kind - avoids the YAML
# combinatorics V4.1's own filler-word decision already argued against.
_TEMPORAL_KIND_SLOT_LIST = TextSlotList.from_tuples([("in", "delay"), ("für", "duration")], name="temporal_kind")

# "Minute(n)"/"Stunde(n)" -> TemporalParser converts to minutes uniformly
# (Stunden * 60) rather than storing the raw unit, so every consumer of
# TemporalExpression.minutes only ever has to deal with one unit.
_TEMPORAL_UNIT_SLOT_LIST = TextSlotList.from_tuples(
    [("Minuten", "minutes"), ("Minute", "minutes"), ("Stunden", "hours"), ("Stunde", "hours")],
    name="unit",
)

# "morgen früh"/"heute Abend" (the plan's own two examples) plus their
# "heute früh"/"morgen Abend" mirror images - the 2x2 cross product of the
# exact two day-words and two day-part-words the plan text gives, nothing
# beyond that invented. Multi-word TextSlotList keys, same as
# _COMPARATOR_SLOT_LIST's "nicht höher als" (verified there already).
_TEMPORAL_RELATIVE_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("morgen früh", "tomorrow_morning"),
        ("morgen abend", "tomorrow_evening"),
        ("heute abend", "today_evening"),
        ("heute früh", "today_morning"),
    ],
    name="relative",
)

# {device_class} vocabulary for StateQueryParser's grammar (HomeIntent V4.2)
# - values are "domain:device_class" composite strings, not a plain domain or
# device_class alone: resolving "welche Fenster" needs both together
# (binary_sensor + window), and hassil TextSlotList values must be plain
# strings, so a second, separate {device_class}-only slot can't be cross-
# referenced against {domain} the way _COMPARISON_DOMAIN_SLOT_LIST's
# domain-only vocabulary can get away with. The literal string "None" means
# "no device_class filter, the domain alone is the target" (light/switch/
# cover) - StateQueryParser splits on ":" and treats that literal as "no
# filter", never as HA's real ``device_class is None`` case (already covered
# structurally: an entity with no device_class simply never matches a filter
# that isn't "None"). Plural before singular wherever the singular is a
# literal prefix of the plural (Tür/Türen, Garagentor/Garagentore), same
# reordering reason _DOMAIN_SLOT_LIST's own docstring documents - "Fenster"
# is identical in singular and plural German, so no ordering concern there.
_DEVICE_CLASS_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Fenster", "binary_sensor:window"),
        ("Türen", "binary_sensor:door"), ("Tür", "binary_sensor:door"),
        ("Garagentore", "binary_sensor:garage_door"), ("Garagentor", "binary_sensor:garage_door"),
        ("Lichter", "light:None"), ("Licht", "light:None"), ("Lampen", "light:None"), ("Lampe", "light:None"),
        ("Schalter", "switch:None"), ("Steckdosen", "switch:None"), ("Steckdose", "switch:None"),
        ("Rollläden", "cover:None"), ("Rollladen", "cover:None"), ("Rolladen", "cover:None"), ("Rolläden", "cover:None"),
        ("Rollos", "cover:None"), ("Rollo", "cover:None"),
        ("Bewegungsmelder", "binary_sensor:motion"), ("Bewegungssensoren", "binary_sensor:motion"),
        ("Bewegungssensor", "binary_sensor:motion"),
        ("Ventilatoren", "fan:None"), ("Ventilator", "fan:None"), ("Lüfter", "fan:None"),
        ("Heizungen", "climate:None"), ("Heizung", "climate:None"),
        ("Thermostate", "climate:None"), ("Thermostat", "climate:None"),
        ("Medienplayer", "media_player:None"), ("Radios", "media_player:None"),
        ("Radio", "media_player:None"), ("Fernseher", "media_player:None"),
        ("Lautsprecher", "media_player:None"),
        ("Saugroboter", "vacuum:None"), ("Staubsauger", "vacuum:None"),
        ("Luftbefeuchter", "humidifier:None"),
        ("Helfer", "input_boolean:None"),
        ("Ventile", "valve:None"), ("Ventil", "valve:None"),
        ("Mähroboter", "lawn_mower:None"), ("Maehroboter", "lawn_mower:None"),
    ],
    name="device_class",
)

# Predicate-position state words ("die Fenster sind {state}") -> the
# SemanticState member *name* (a plain string, cast back to the enum member
# via _STATE_NAME_TO_SEMANTIC in parsers.py - hassil TextSlotList values must
# be strings, same constraint _COMPARATOR_SLOT_LIST's operator strings work
# around).
_STATE_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("geöffnet", "OPEN"), ("offen", "OPEN"),
        ("hochgefahren", "OPEN"), ("oben", "OPEN"),
        ("geschlossen", "CLOSED"), ("zu", "CLOSED"),
        ("runtergefahren", "CLOSED"), ("heruntergefahren", "CLOSED"),
        ("unten", "CLOSED"),
        ("eingeschaltet", "ON"), ("angeschaltet", "ON"), ("an", "ON"),
        ("ausgeschaltet", "OFF"), ("aus", "OFF"),
        ("aktiv", "ACTIVE"), ("in Betrieb", "ACTIVE"),
        ("läuft", "ACTIVE"), ("laeuft", "ACTIVE"),
        ("inaktiv", "INACTIVE"), ("gestoppt", "INACTIVE"),
    ],
    name="state",
)

# Attributive-position state words ("gibt es {state_adj} Fenster") - German
# inflects the adjective here (offen -> offene), a different word than the
# predicate form above, so this is its own slot rather than reusing {state}.
_STATE_ADJ_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("geöffnete", "OPEN"), ("geöffnetes", "OPEN"), ("geöffneter", "OPEN"), ("geöffneten", "OPEN"),
        ("offene", "OPEN"), ("offenes", "OPEN"), ("offener", "OPEN"), ("offenen", "OPEN"),
        ("geschlossene", "CLOSED"), ("geschlossenes", "CLOSED"), ("geschlossener", "CLOSED"), ("geschlossenen", "CLOSED"),
        ("eingeschaltete", "ON"), ("eingeschaltetes", "ON"), ("eingeschalteter", "ON"), ("eingeschalteten", "ON"),
        ("angeschaltete", "ON"), ("angeschaltetes", "ON"), ("angeschalteter", "ON"), ("angeschalteten", "ON"),
        ("ausgeschaltete", "OFF"), ("ausgeschaltetes", "OFF"), ("ausgeschalteter", "OFF"), ("ausgeschalteten", "OFF"),
        ("aktive", "ACTIVE"), ("aktives", "ACTIVE"), ("aktiver", "ACTIVE"), ("aktiven", "ACTIVE"),
        ("inaktive", "INACTIVE"), ("inaktives", "INACTIVE"), ("inaktiver", "INACTIVE"), ("inaktiven", "INACTIVE"),
    ],
    name="state_adj",
)


# --- Automation Trigger Engine vocabulary (V5 Wave 1, V5.3) -----------------
#
# Numeric Trigger comparators ("über"/"unter"/"mehr als"/"weniger als")
# deliberately reuse _COMPARATOR_SLOT_LIST above verbatim (Regel 6, checked
# before adding a second list) rather than building a narrower duplicate -
# automation_trigger_parser.py maps "gt"/"lt" onto NumericComparator.ABOVE/
# BELOW; "gte"/"lte" ("mindestens"/"höchstens"/"nicht höher als") still
# match the {comparator} slot grammatically (same shared list), but the
# parser itself refuses them (returns None) rather than guessing an
# above/below approximation - HA's own numeric_state trigger has no
# inclusive comparator to map them onto.

# {weekday} vocabulary for the Weekday Trigger - values are HA's own
# weekday abbreviations (mon/tue/.../sun, matching the `weekday` condition's
# vocabulary) so a later HA generator (separate future wave) can pass them
# through unchanged. "Wochenende"/"Werktag(s)" are composite comma-joined
# values (same trick _DEVICE_CLASS_SLOT_LIST's "domain:device_class" already
# established) rather than a second slot - "am Wochenende" is one spoken
# concept, not two separately-spoken days.
_WEEKDAY_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Montags", "mon"), ("Montag", "mon"),
        ("Dienstags", "tue"), ("Dienstag", "tue"),
        ("Mittwochs", "wed"), ("Mittwoch", "wed"),
        ("Donnerstags", "thu"), ("Donnerstag", "thu"),
        ("Freitags", "fri"), ("Freitag", "fri"),
        ("Samstags", "sat"), ("Samstag", "sat"), ("Sonnabends", "sat"), ("Sonnabend", "sat"),
        ("Sonntags", "sun"), ("Sonntag", "sun"),
        ("Wochenende", "sat,sun"),
        ("Werktags", "mon,tue,wed,thu,fri"), ("Werktag", "mon,tue,wed,thu,fri"),
    ],
    name="weekday",
)

def weekday_vocabulary() -> dict[str, tuple[str, ...]]:
    """Spoken weekday word -> HA weekday abbreviations (same list as above)."""
    table: dict[str, tuple[str, ...]] = {}
    for value in _WEEKDAY_SLOT_LIST.values:
        spoken = str(getattr(value.text_in, "text", "")).casefold()
        if spoken:
            table[spoken] = tuple(str(value.value_out).split(","))
    return table


# {sun_event} vocabulary for the Sun Trigger - values match HA's own `sun`
# trigger `event` parameter (`sunrise`/`sunset`) directly, no translation
# layer needed downstream.
_SUN_EVENT_SLOT_LIST = TextSlotList.from_tuples(
    [("Sonnenaufgang", "sunrise"), ("Sonnenuntergang", "sunset")],
    name="sun_event",
)

# {presence_event} vocabulary for the Presence Trigger - "kommt nach Hause"/
# "kommt heim" (arrival) vs. "verlässt das Haus"/"geht weg" (departure), the
# two person-level events HomeIntent V5 Teil 2/10's examples name. Deliberately
# only these two outcomes in this wave - "niemand mehr zuhause" (all-persons-
# away) is a structurally different, zone-count-based sentence handled as its
# own literal YAML sentence, not a third {presence_event} value, since it has
# no verb phrase of its own to slot in here.
_PRESENCE_EVENT_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("nach Hause kommt", "arrive"), ("nach Hause komme", "arrive"),
        ("heimkommt", "arrive"), ("heimkomme", "arrive"),
        ("das Haus verlässt", "leave"), ("das Haus verlasse", "leave"),
        ("weggeht", "leave"), ("weggehe", "leave"),
    ],
    name="presence_event",
)

# {state_verb} vocabulary for the State Trigger - addendum point 2 names
# "offen"/"geöffnet"/"geht auf"/"aufgeht" explicitly as word variants that
# must normalize onto the same SemanticState value. _STATE_SLOT_LIST above
# already covers the adjective form used after "wird" ("wird geöffnet");
# this is the genuinely new piece - one-word/verb-phrase forms that don't
# fit the "wird {state}" template at all ("das Fenster geht auf", not "das
# Fenster wird geht auf"). Same OPEN/CLOSED/ON/OFF string values as
# _STATE_SLOT_LIST so automation_trigger_parser.py can cast both back via
# the same _STATE_NAME_TO_SEMANTIC dict (parsers.py) - no second mapping.
_STATE_VERB_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("aufgeht", "OPEN"), ("geht auf", "OPEN"), ("gehen auf", "OPEN"),
        ("zugeht", "CLOSED"), ("geht zu", "CLOSED"), ("gehen zu", "CLOSED"),
        ("angeht", "ON"), ("geht an", "ON"), ("gehen an", "ON"),
        ("ausgeht", "OFF"), ("geht aus", "OFF"), ("gehen aus", "OFF"),
    ],
    name="state_verb",
)

# {device_press_type} vocabulary for the Device Trigger - Home Assistant's
# own `device` trigger platform (distinct from `state`: it fires on a
# device-level event like a Zigbee/Z-Wave button press, addressed via
# device_id + type, not entity_id + to). Values are a small, generation-
# friendly canonical set ("pressed"/"double_pressed"/"long_pressed") -
# mapping these onto a specific integration's real subtype vocabulary (e.g.
# zha's "remote_button_short_press") is HA-generator work, a separate future
# wave (see automation_model.py's TriggerModel.device_trigger_type comment).
# Includes the trailing "wird" so the grammar sentence needs no separate
# literal for it (same one-slot-carries-the-whole-verb-phrase trick
# _PRESENCE_EVENT_SLOT_LIST above already uses).
#
# Ordering matters here (confirmed live): the sentence template is
# "{name} {device_press_type}" with {name} an open-ended WildcardSlotList
# immediately before this slot - same trap state_query.yaml's HassCheckState
# comment documents for *sentences*, but here it bites *within one slot
# list*. With "gedrückt wird" listed first, hassil accepts it as soon as
# it's found (matching only the tail of "doppelt gedrückt wird") and lets
# {name} swallow the leading "doppelt"/"zweimal"/"lange" as if it were part
# of the entity name - "double_pressed"/"long_pressed" would then be
# unreachable. Longer/more-specific entries must come first so hassil tries
# them before the short suffix that's contained in all of them.
_DEVICE_PRESS_TYPE_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("doppelt gedrückt wird", "double_pressed"), ("zweimal gedrückt wird", "double_pressed"),
        ("lange gedrückt wird", "long_pressed"), ("gehalten wird", "long_pressed"),
        ("gedrückt wird", "pressed"),
    ],
    name="device_press_type",
)

# {offset_direction} vocabulary for the Sun Trigger's offset phrasing ("20
# Minuten vor Sonnenuntergang"/"... nach Sonnenaufgang") - no existing
# before/after slot anywhere else in this codebase to reuse.
_TIME_OFFSET_DIRECTION_SLOT_LIST = TextSlotList.from_tuples(
    [("vor", "before"), ("nach", "after")],
    name="offset_direction",
)


# --- Automation Condition Engine vocabulary (V5 Wave 2, V5.4-V5.6) ---------
#
# STATE/WEEKDAY/SUN conditions reuse _STATE_SLOT_LIST/_STATE_VERB_SLOT_LIST/
# _WEEKDAY_SLOT_LIST/_SUN_EVENT_SLOT_LIST above verbatim (Regel 6); NUMERIC/
# TIME conditions reuse _COMPARATOR_SLOT_LIST/_TIME_OFFSET_DIRECTION_SLOT_LIST.
# The four lists below are the genuinely new vocabulary this wave needs.

# {month} vocabulary for the DATE condition - no calendar-date vocabulary
# existed anywhere in this codebase before this wave (verified via grep).
# Values are plain "1".."12" strings (cast to int by the parser), same
# string-value convention _COUNT_SLOT_LIST/_COLOR_TEMP_SLOT_LIST already use.
_MONTH_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Januar", "1"), ("Februar", "2"), ("März", "3"), ("April", "4"),
        ("Mai", "5"), ("Juni", "6"), ("Juli", "7"), ("August", "8"),
        ("September", "9"), ("Oktober", "10"), ("November", "11"), ("Dezember", "12"),
    ],
    name="month",
)

# {numeric_unit} vocabulary for the NUMERIC condition ("... unter 19 Grad
# liegt") - values are the exact HA unit symbols service_call.py's
# _UNIT_SPOKEN_DE already uses as *keys* (this list is that dict's reverse:
# spoken German word -> HA unit symbol), not reinvented here.
_NUMERIC_UNIT_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Grad Celsius", "°C"), ("Grad", "°C"),
        ("Prozent", "%"),
        ("Watt", "W"),
        ("Kilowattstunden", "kWh"),
        ("Hektopascal", "hPa"),
        ("Lux", "lx"),
    ],
    name="numeric_unit",
)

# {presence_state} vocabulary for the PRESENCE condition - a *current-state*
# reading ("jemand ist zuhause"/"weg"), distinct from _PRESENCE_EVENT_SLOT_LIST's
# arrival/departure *event* verbs above (which don't fit a stative condition
# sentence). Values match HA's own `person`/`device_tracker` state strings
# (`home`/`not_home`) directly.
_PRESENCE_STATE_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("zuhause", "home"), ("daheim", "home"), ("da", "home"),
        ("weg", "not_home"), ("unterwegs", "not_home"),
    ],
    name="presence_state",
)

# {raw_entity_state} vocabulary for the ENTITY condition - closed-vocabulary
# raw HA state equality for domains STATE's OPEN/CLOSED/ON/OFF vocabulary
# structurally cannot address (locks/vacuums/media players don't speak
# open/closed/on/off). Values match HA's own state strings for these
# domains directly (`lock`: locked/unlocked, `vacuum`: docked/cleaning/
# paused, `media_player`: playing/paused) - "pausiert" is deliberately one
# entry shared by vacuum and media_player, since both domains use the
# identical HA state string "paused" for it.
_RAW_ENTITY_STATE_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("verriegelt", "locked"), ("entriegelt", "unlocked"),
        ("dockt", "docked"), ("angedockt", "docked"),
        ("reinigt", "cleaning"), ("läuft", "cleaning"),
        ("pausiert", "paused"),
        ("spielt", "playing"),
    ],
    name="raw_entity_state",
)


# --- Automation Action Engine vocabulary (V5 Wave 3, V5.7-V5.12) -----------
#
# SET_COLOR/SET_COLOR_TEMPERATURE reuse _COLOR_SLOT_LIST/_COLOR_TEMP_SLOT_LIST
# above verbatim (Regel 6). Percent/temperature RangeSlotLists are defined
# inline in automation_action_parser.py (not here), same precedent
# PercentageParser/ClimateExtendedParser already set in parsers.py - a
# RangeSlotList's start/stop/type is call-site configuration, not shared
# closed vocabulary.

# {action_temporal_unit} for DELAY ("warte 5 Minuten")/TURN_ON's Duration
# Engine ("... für 30 Minuten an")/WAIT's timeout ("warte maximal 30
# Minuten bis ..."). Deliberately its own list, NOT merged into the existing
# _TEMPORAL_UNIT_SLOT_LIST (V4.7, TemporalParser) even though both cover
# Minuten/Stunden: that list's own consumer (TemporalParser._build_temporal)
# only ever special-cases "hours" and otherwise treats the raw amount as
# minutes - adding "Sekunden" there would silently make TemporalParser's
# *existing*, already-shipped "in 5 Sekunden"-shaped sentences parse as "5
# minutes" instead of failing to match, a regression in unrelated, already-
# verified behaviour. Same "don't leak a widened vocabulary into an existing
# grammar's shared slot" reasoning _COMPARISON_DOMAIN_SLOT_LIST's own
# docstring documents for why *that* list stays separate from
# _DOMAIN_SLOT_LIST. Values are the literal second-level unit the automation
# action parser itself converts to seconds (1/60/3600 multiplier), not
# reused across any other grammar.
_ACTION_TEMPORAL_UNIT_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Sekunden", "seconds"), ("Sekunde", "seconds"),
        ("Minuten", "minutes"), ("Minute", "minutes"),
        ("Stunden", "hours"), ("Stunde", "hours"),
    ],
    name="action_temporal_unit",
)

# V5 Wave 4 (V5.13, "Temporal Engine") - {day_part} vocabulary for the Time
# Trigger/Condition, a fixed canonical-hour mapping (same "conventional
# fixed value, not a UX decision" precedent _COLOR_TEMP_SLOT_LIST's own
# warmweiß=2700K/kaltweiß=6500K docstring already sets). Since HA's own time
# trigger platform fires daily at a wall-clock hour:minute (no date
# component), "morgen früh"/"heute Abend" carry no distinguishable meaning
# beyond their day-part word alone here - both "heute" and "morgen" collapse
# onto the same recurring canonical hour (stated plainly, not hidden: this
# is a deliberately narrower reading than frame.py's own plain-command
# TemporalExpression, which *does* keep "heute"/"morgen" apart - that system
# answers a different question, "what happens right now", not "what daily
# time does this automation recur at").
_DAY_PART_HOUR_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("morgens", "7"), ("früh", "7"), ("heute früh", "7"), ("morgen früh", "7"),
        ("vormittags", "10"),
        ("mittags", "12"),
        ("nachmittags", "15"),
        ("abends", "19"), ("heute abend", "19"), ("morgen abend", "19"),
        ("nachts", "22"),
    ],
    name="day_part",
)

# V5 Wave 4 (V5.15, "Time Windows") - {day_part_window} vocabulary for the
# Time Window Condition ("nur nachts", "morgens"). Comma-composite
# "start,end" values, same packing convention _WEEKDAY_SLOT_LIST's own
# "Wochenende"->"sat,sun" already established - the handler
# (automation_condition_parser.py's _parse_time_window_condition) splits on
# "," and builds an AND(after start, before end) node, or an OR node when
# the window wraps past midnight (start > end, e.g. "nachts" 22->6) - see
# that function's own comment for why OR is the structurally correct
# composition there, not a special case invented ad hoc.
_DAY_PART_WINDOW_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("morgens", "6,10"),
        ("vormittags", "9,12"),
        ("mittags", "11,14"),
        ("nachmittags", "14,18"),
        ("abends", "18,22"),
        ("nachts", "22,6"),
    ],
    name="day_part_window",
)


# --- Semantic candidate mapping (V6.3, second half) -------------------------
#
# The slot lists above are hassil's raw grammar vocabulary (word -> match
# value). The mappings below add a second, semantic layer on top: normalized
# German word -> semantic PRIMITIVE CANDIDATE (see nlu/primitives.py). They
# are candidates only, not decisions - final interpretation still requires
# the Semantic Composer + Reasoning Engine (V6.4+) combining these with
# context/World Model/capabilities, per the plan's own "liefert Kandidaten,
# entscheidet nicht allein" rule (V6.3, Brain node
# 52ae9a3a-c380-4a26-9a49-ce8b1db94367).
#
# Deliberately incomplete: only two of SemanticAction/SemanticProperty/
# SemanticDirection/SemanticQuantity/SemanticDegree have real, already-listed
# vocabulary in this module to derive candidates from without guessing.
# SemanticAction ("mach"/"schalte"/"an"/"aus") and SemanticDirection
# ("heller"/"dunkler") are carried today as literal words in the hassil YAML
# sentence templates (custom_components/homeintent/intents/de/**/*.yaml) and in
# parsers.py's own regexes (e.g. _LIGHT_EXTENDED_RE), not as a TextSlotList
# vocabulary constant here - building their candidate mapping means first
# inventorying those YAML/regex literals, which is real, separate work left
# for Wave 2b (Semantic Composer) rather than invented here ahead of it.
# SemanticDegree has no lexicon entry at all yet (see primitives.py's own
# docstring - "etwas"/"ein bisschen" are stripped as filler before any parser
# sees them), so no candidate mapping is possible for it today.

# _COLOR_SLOT_LIST's keys all indicate the same property regardless of which
# colour word was said; _COLOR_TEMP_SLOT_LIST's keys indicate the sibling
# property. Every value is the SAME candidate per list on purpose - the word
# only tells you *that* a colour/colour-temperature is being set, not which
# property-free reading would make sense otherwise.
def _literal_slot_text(value: object) -> str:
    """Return text only for the literal slot entries defined in this module."""
    text_in = getattr(value, "text_in", None)
    if not isinstance(text_in, TextChunk):
        raise TypeError("Semantic candidate slot must contain literal text")
    text = getattr(text_in, "text", None)
    if not isinstance(text, str):
        raise TypeError("Semantic candidate slot must contain non-empty literal text")
    return text


SEMANTIC_PROPERTY_CANDIDATES: dict[str, SemanticProperty] = {
    **{_literal_slot_text(v): SemanticProperty.COLOR for v in _COLOR_SLOT_LIST.values},
    **{_literal_slot_text(v): SemanticProperty.COLOR_TEMPERATURE for v in _COLOR_TEMP_SLOT_LIST.values},
}

# _QUANTIFIER_SLOT_LIST's "all"/"both" and _COUNT_SLOT_LIST's "2".."10"
# already carry the exact values SemanticQuantity's classmethods take -
# reuse them 1:1 instead of re-encoding the same words differently here.
SEMANTIC_QUANTITY_CANDIDATES: dict[str, SemanticQuantity] = {
    **{
        _literal_slot_text(v): (SemanticQuantity.all() if v.value_out == "all" else SemanticQuantity.exactly(2))
        for v in _QUANTIFIER_SLOT_LIST.values
    },
    **{_literal_slot_text(v): SemanticQuantity.exactly(int(v.value_out)) for v in _COUNT_SLOT_LIST.values},
}
