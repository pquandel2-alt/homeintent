"""Semantic Lexicon (V6.3): pins the closed-vocabulary hassil TextSlotList
definitions extracted from parsers.py into nlu/lexicon.py, and that
parsers.py re-exposes the exact same objects it used to define itself -
purely additive/extraction, no value changes."""

from __future__ import annotations

from hassil import TextSlotList

from homeintent.nlu import lexicon


def _values(slot_list: TextSlotList) -> list[tuple[str, str]]:
    return [(v.text_in.text, v.value_out) for v in slot_list.values]


def test_domain_slot_list_values_pinned():
    assert _values(lexicon._DOMAIN_SLOT_LIST) == [
        ("Lichter", "light"), ("Licht", "light"), ("Lampen", "light"), ("Lampe", "light"),
        ("Schalter", "switch"), ("Steckdosen", "switch"), ("Steckdose", "switch"),
        ("Rollladen", "cover"), ("Rollläden", "cover"), ("Rolladen", "cover"), ("Rolläden", "cover"),
        ("Rollos", "cover"), ("Rollo", "cover"),
        ("Ventilatoren", "fan"), ("Ventilator", "fan"),
    ]


def test_quantifier_slot_list_values_pinned():
    assert _values(lexicon._QUANTIFIER_SLOT_LIST) == [
        ("alle", "all"), ("beide", "both"), ("beiden", "both"), ("beider", "both"), ("nur", "all"),
    ]


def test_count_slot_list_values_pinned():
    assert _values(lexicon._COUNT_SLOT_LIST) == [
        ("zwei", "2"), ("drei", "3"), ("vier", "4"), ("fünf", "5"),
        ("sechs", "6"), ("sieben", "7"), ("acht", "8"), ("neun", "9"), ("zehn", "10"),
    ]


def test_level_slot_list_values_pinned():
    assert _values(lexicon._LEVEL_SLOT_LIST) == [("oben", "up"), ("unten", "down")]


def test_color_slot_list_values_pinned():
    assert _values(lexicon._COLOR_SLOT_LIST) == [
        ("rot", "red"), ("grün", "green"), ("blau", "blue"), ("gelb", "yellow"),
        ("orange", "orange"), ("lila", "purple"), ("violett", "purple"), ("weiß", "white"),
        ("pink", "pink"), ("rosa", "pink"), ("türkis", "turquoise"), ("cyan", "cyan"),
    ]


def test_color_temp_slot_list_values_pinned():
    assert _values(lexicon._COLOR_TEMP_SLOT_LIST) == [
        ("warmweiß", "2700"), ("neutralweiß", "4000"),
        ("tageslichtweiß", "5500"), ("kaltweiß", "6500"),
    ]


def test_comparator_slot_list_values_pinned():
    assert _values(lexicon._COMPARATOR_SLOT_LIST) == [
        ("mindestens", "gte"),
        ("höchstens", "lte"),
        ("nicht höher als", "lte"),
        ("über", "gt"),
        ("unter", "lt"),
        ("heller als", "gt"),
        ("mehr als", "gt"),
        ("dunkler als", "lt"),
        ("weniger als", "lt"),
    ]


def test_comparison_domain_slot_list_values_pinned():
    assert _values(lexicon._COMPARISON_DOMAIN_SLOT_LIST) == [
        ("Lichter", "light"), ("Licht", "light"), ("Lampen", "light"), ("Lampe", "light"),
        ("Rollläden", "cover"), ("Rollladen", "cover"), ("Rolladen", "cover"), ("Rolläden", "cover"),
        ("Rollos", "cover"), ("Rollo", "cover"),
        ("Heizungen", "climate"), ("Heizung", "climate"),
        ("Thermostate", "climate"), ("Thermostat", "climate"),
        ("Räume", "climate"), ("Raum", "climate"),
    ]


def test_temporal_kind_slot_list_values_pinned():
    assert _values(lexicon._TEMPORAL_KIND_SLOT_LIST) == [("in", "delay"), ("für", "duration")]


def test_temporal_unit_slot_list_values_pinned():
    assert _values(lexicon._TEMPORAL_UNIT_SLOT_LIST) == [
        ("Minuten", "minutes"), ("Minute", "minutes"), ("Stunden", "hours"), ("Stunde", "hours"),
    ]


def test_temporal_relative_slot_list_values_pinned():
    assert _values(lexicon._TEMPORAL_RELATIVE_SLOT_LIST) == [
        ("morgen früh", "tomorrow_morning"),
        ("morgen abend", "tomorrow_evening"),
        ("heute abend", "today_evening"),
        ("heute früh", "today_morning"),
    ]


def test_device_class_slot_list_values_pinned():
    assert _values(lexicon._DEVICE_CLASS_SLOT_LIST) == [
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
        ("Luftbefeuchter", "humidifier:None"), ("Helfer", "input_boolean:None"),
        ("Ventile", "valve:None"), ("Ventil", "valve:None"),
        ("Mähroboter", "lawn_mower:None"), ("Maehroboter", "lawn_mower:None"),
    ]


def test_state_slot_list_values_pinned():
    assert _values(lexicon._STATE_SLOT_LIST) == [
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
    ]


def test_state_adj_slot_list_values_pinned():
    assert _values(lexicon._STATE_ADJ_SLOT_LIST) == [
        ("geöffnete", "OPEN"), ("geöffnetes", "OPEN"), ("geöffneter", "OPEN"), ("geöffneten", "OPEN"),
        ("offene", "OPEN"), ("offenes", "OPEN"), ("offener", "OPEN"), ("offenen", "OPEN"),
        ("geschlossene", "CLOSED"), ("geschlossenes", "CLOSED"), ("geschlossener", "CLOSED"), ("geschlossenen", "CLOSED"),
        ("eingeschaltete", "ON"), ("eingeschaltetes", "ON"), ("eingeschalteter", "ON"), ("eingeschalteten", "ON"),
        ("angeschaltete", "ON"), ("angeschaltetes", "ON"), ("angeschalteter", "ON"), ("angeschalteten", "ON"),
        ("ausgeschaltete", "OFF"), ("ausgeschaltetes", "OFF"), ("ausgeschalteter", "OFF"), ("ausgeschalteten", "OFF"),
        ("aktive", "ACTIVE"), ("aktives", "ACTIVE"), ("aktiver", "ACTIVE"), ("aktiven", "ACTIVE"),
        ("inaktive", "INACTIVE"), ("inaktives", "INACTIVE"), ("inaktiver", "INACTIVE"), ("inaktiven", "INACTIVE"),
    ]


def test_semantic_property_candidates_cover_every_color_and_color_temp_word():
    from homeintent.nlu.primitives import SemanticProperty

    for word, _ in _values(lexicon._COLOR_SLOT_LIST):
        assert lexicon.SEMANTIC_PROPERTY_CANDIDATES[word] is SemanticProperty.COLOR
    for word, _ in _values(lexicon._COLOR_TEMP_SLOT_LIST):
        assert lexicon.SEMANTIC_PROPERTY_CANDIDATES[word] is SemanticProperty.COLOR_TEMPERATURE


def test_semantic_quantity_candidates_all_and_both():
    from homeintent.nlu.primitives import SemanticQuantity, SemanticQuantityKind

    assert lexicon.SEMANTIC_QUANTITY_CANDIDATES["alle"] == SemanticQuantity.all()
    assert lexicon.SEMANTIC_QUANTITY_CANDIDATES["nur"] == SemanticQuantity.all()
    for word in ("beide", "beiden", "beider"):
        assert lexicon.SEMANTIC_QUANTITY_CANDIDATES[word] == SemanticQuantity.exactly(2)
    assert lexicon.SEMANTIC_QUANTITY_CANDIDATES["fünf"] == SemanticQuantity.exactly(5)
    assert lexicon.SEMANTIC_QUANTITY_CANDIDATES["zehn"].kind is SemanticQuantityKind.EXACTLY
