"""German device-genus ontology: words -> kinds of devices, never names.

A *genus* is a kind of device a person talks about ("Licht", "Rollladen",
"Glotze", "Wassermelder").  Each genus is pure data: its German lemmas
(including colloquial words), plural and gender, the Home Assistant
domains and ``device_class`` values it covers, and optionally the
capability that is typical for it.  Surface forms (plural, dative plural,
genitive, diminutive) and compounds ("Wohnzimmer|licht", "Terrassen|tür",
"Decken|ventilator") are derived by general German morphology rules below,
so adding one lemma makes every inflected and compounded use of it work in
commands, questions, automations and notifications alike.

The module is Home-Assistant-free and deterministic.  It never decides which
concrete entity is meant; ``target_resolution`` combines a genus with place,
feature and quantity against the live registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Iterable, Mapping

from ..entities import EntitySnapshot, normalize_for_compare

__all__ = (
    "COMPOUND_LINKS",
    "GENERA",
    "Gender",
    "Genus",
    "GenusMatch",
    "WordAnalysis",
    "analyse_word",
    "entity_genera",
    "entity_has_genus",
    "genus",
    "genus_forms",
    "lookup_genus_word",
)


class Gender(Enum):
    MASCULINE = "m"
    FEMININE = "f"
    NEUTER = "n"


@dataclass(frozen=True)
class Genus:
    """One kind of device.

    ``lemmas`` are the singular dictionary forms (first = display form).
    ``plural`` is the display plural.  ``extra_forms`` holds irregular
    surface forms that general morphology cannot derive.
    ``domains``/``device_classes`` define membership; when
    ``device_classes`` is set, an entity without a matching class belongs
    to the genus only if its *name* contains one of the genus words
    (``name_evidence``), e.g. a ``media_player`` named "Wohnzimmer TV".
    ``parent`` links a specific genus (Raffstore) to its family
    (Rollladen); members of a child always belong to the parent.
    ``critical`` genera (locks, alarm, garage doors) never join an
    unspecific "alles"; ``in_everything`` marks switchable everyday devices.
    """

    key: str
    lemmas: tuple[str, ...]
    plural: str
    gender: Gender
    domains: frozenset[str]
    device_classes: frozenset[str] | None = None
    extra_forms: tuple[str, ...] = ()
    parent: str | None = None
    name_evidence: bool = True
    critical: bool = False
    in_everything: bool = False
    sensor: bool = False
    exclude_names: tuple[str, ...] = ()
    capability: str | None = None
    diminutive: bool = False
    # Mass nouns ("das Licht im Wohnzimmer", "die Beleuchtung") denote every
    # member at a named place, like Home Assistant's own agent (rule F20).
    mass_lemmas: tuple[str, ...] = ()
    # Operations this genus word cannot carry.  "Musik" names content, not
    # a device: it can be made quieter or stopped, but "Musik an" needs a
    # source and is never read as switching a speaker on.
    forbidden_actions: frozenset[str] = frozenset()

    @property
    def singular(self) -> str:
        return self.lemmas[0]


def _g(
    key: str,
    lemmas: str,
    plural: str,
    gender: Gender,
    domains: Iterable[str],
    device_classes: Iterable[str] | None = None,
    **kwargs: object,
) -> Genus:
    return Genus(
        key=key,
        lemmas=tuple(lemmas.split("|")),
        plural=plural,
        gender=gender,
        domains=frozenset(domains),
        device_classes=frozenset(device_classes) if device_classes is not None else None,
        **kwargs,  # type: ignore[arg-type]
    )


M, F, N = Gender.MASCULINE, Gender.FEMININE, Gender.NEUTER

# The ontology.  Order matters only for display; membership is data driven.
GENERA: tuple[Genus, ...] = (
    # --- Licht ---------------------------------------------------------
    _g("light", "Licht|Lampe|Leuchte|Beleuchtung|Funzel|Birne|Strahler|Spot",
       "Lichter", N, {"light"}, extra_forms=("Lichtern", "Lampen", "Leuchten", "Strahlern", "Spots"),
       in_everything=True, diminutive=True, mass_lemmas=("Licht", "Beleuchtung")),
    # --- Rollladen-Familie ----------------------------------------------
    _g("shutter", "Rollladen|Rolllade|Rolladen|Rollade|Rollo|Jalousie|Rollo|Verschattung|Beschattung",
       "Rollläden", M, {"cover"}, {"shutter", "blind", "curtain", "shade"},
       extra_forms=("Rolläden", "Rollos", "Jalousien", "Rollläden", "Rollaeden", "Rolllaeden"),
       capability="POSITION"),
    _g("raffstore", "Raffstore|Raffstoren|Lamellenstore", "Raffstores", M, {"cover"}, {"blind"},
       parent="shutter"),
    _g("awning", "Markise|Sonnensegel", "Markisen", F, {"cover"}, {"awning"}),
    _g("curtain", "Vorhang|Gardine", "Vorhänge", M, {"cover"}, {"curtain"},
       extra_forms=("Vorhaenge", "Gardinen"), parent="shutter"),
    _g("garage_door", "Garagentor|Tor|Rolltor|Garagentür|Hoftor|Einfahrtstor|Sektionaltor", "Tore", N,
       {"cover", "binary_sensor"}, {"garage", "garage_door", "gate"},
       extra_forms=("Garagentore",), critical=True),
    # --- Öffnungen (Kontakte) -------------------------------------------
    _g("window", "Fenster|Fensterkontakt", "Fenster", N,
       {"binary_sensor", "cover"}, {"window"}, extra_forms=("Fenstern", "Fensterkontakte"),
       sensor=True),
    _g("door", "Tür|Türe|Türkontakt",
       "Türen", F, {"binary_sensor"}, {"door", "opening"}, sensor=True),
    _g("lock", "Schloss|Türschloss|Türschloß|Schloß|Riegel", "Schlösser", N, {"lock"},
       extra_forms=("Schloesser", "Schlössern"), critical=True),
    # --- Klima ------------------------------------------------------------
    _g("heating", "Heizung|Thermostat|Heizkörper|Heizkörperthermostat|Radiator|Klimaanlage|Klima",
       "Heizungen", F, {"climate"}, extra_forms=("Heizkoerper", "Thermostate", "Radiatoren"),
       capability="TEMPERATURE"),
    _g("fan", "Ventilator|Lüfter|Lüftung|Propeller", "Ventilatoren", M, {"fan"},
       extra_forms=("Luefter",), in_everything=True),
    _g("humidifier", "Luftbefeuchter|Befeuchter|Entfeuchter|Luftentfeuchter", "Luftbefeuchter", M,
       {"humidifier"}, in_everything=True),
    _g("water_heater", "Warmwasser|Boiler|Warmwasserspeicher|Durchlauferhitzer", "Boiler", M,
       {"water_heater"}),
    # --- Medien ----------------------------------------------------------
    _g("music", "Musik|Wiedergabe|Song|Lied", "Musik", F, {"media_player"}, parent="media",
       name_evidence=False, device_classes=None,
       forbidden_actions=frozenset({"turn_on", "start", "play", "toggle"})),
    _g("media", "Lautsprecher|Box|Anlage|Stereoanlage|Musikanlage|Player|Medienplayer|Soundbar",
       "Lautsprecher", M, {"media_player"}, extra_forms=("Boxen", "Anlagen", "Musikbox", "Musikboxen"), in_everything=True),
    _g("tv", "Fernseher|TV|Glotze|Fernsehen|Flimmerkiste|Flachbildschirm|Television|Smart-TV",
       "Fernseher", M, {"media_player"}, {"tv"}, parent="media", in_everything=True),
    _g("radio", "Radio", "Radios", N, {"media_player"},
       {"speaker", "receiver"}, parent="media", in_everything=True),
    # --- Haushaltsroboter --------------------------------------------------
    _g("vacuum", "Staubsauger|Sauger|Saugroboter|Staubsaugerroboter|Roboter|Wischroboter|Robi",
       "Staubsauger", M, {"vacuum"}),
    _g("mower", "Mäher|Rasenmäher|Mähroboter|Rasenroboter", "Mäher", M, {"lawn_mower"},
       extra_forms=("Maeher", "Rasenmaeher", "Maehroboter")),
    # --- Schalter ---------------------------------------------------------
    _g("switch", "Schalter|Zwischenstecker|Aktor", "Schalter", M, {"switch"}, in_everything=True),
    _g("socket", "Steckdose|Stecker|Steckerleiste|Mehrfachsteckdose", "Steckdosen", F, {"switch"},
       {"outlet"}, parent="switch", in_everything=True),
    _g("valve", "Ventil|Bewässerung|Sprenger",
       "Ventile", N, {"valve"}, extra_forms=("Bewaesserung",)),
    # --- Melder -----------------------------------------------------------
    _g("smoke_detector", "Rauchmelder|Brandmelder|Feuermelder", "Rauchmelder", M,
       {"binary_sensor"}, {"smoke"}, sensor=True),
    _g("co_detector", "CO-Melder|Kohlenmonoxidmelder|Gasmelder", "Gasmelder", M,
       {"binary_sensor"}, {"carbon_monoxide", "gas"}, sensor=True),
    _g("water_detector", "Wassermelder|Wassersensor|Leckagesensor|Wasserleckmelder|Leckmelder|Überschwemmungsmelder|Feuchtemelder",
       "Wassermelder", M, {"binary_sensor"}, {"moisture"}, sensor=True),
    _g("motion_detector", "Bewegungsmelder|Bewegungssensor|Bewegung|Bewegungserkennung",
       "Bewegungsmelder", M, {"binary_sensor"}, {"motion"}, sensor=True),
    _g("presence_detector", "Präsenzmelder|Präsenzsensor|Anwesenheitssensor|Präsenz|Anwesenheit",
       "Präsenzmelder", M, {"binary_sensor"}, {"occupancy", "presence"},
       extra_forms=("Praesenzmelder", "Praesenz"), sensor=True),
    # --- Messgrößen -------------------------------------------------------
    _g("temperature_sensor", "Temperatur|Temperatursensor|Thermometer|Temperaturfühler",
       "Temperaturen", F, {"sensor"}, {"temperature"}, sensor=True),
    _g("humidity_sensor", "Luftfeuchtigkeit|Luftfeuchte|Feuchtigkeit|Feuchte|Hygrometer",
       "Luftfeuchtigkeiten", F, {"sensor"}, {"humidity"}, sensor=True),
    _g("co2_sensor", "CO2|Kohlendioxid|Luftqualität|CO2-Wert", "CO2-Werte", N, {"sensor"},
       {"carbon_dioxide"}, extra_forms=("Luftqualitaet",), sensor=True),
    _g("power_sensor", "Leistung|Stromaufnahme|Strom|Verbrauch|Stromverbrauch", "Leistungen", F,
       {"sensor"}, {"power"}, sensor=True),
    _g("energy_sensor", "Energie|Energieverbrauch|Zähler|Stromzähler|Energiezähler", "Energiezähler",
       F, {"sensor"}, {"energy"}, extra_forms=("Zaehler",), sensor=True),
    _g("battery_sensor", "Batterie|Akku|Batteriestand|Ladestand|Akkustand|Akkuladung", "Batterien", F, {"sensor"},
       {"battery"}, extra_forms=("Akkus",), sensor=True),
    _g("illuminance_sensor", "Helligkeit|Helligkeitssensor|Lichtsensor|Beleuchtungsstärke", "Helligkeiten",
       F, {"sensor"}, {"illuminance"}, sensor=True),
    # --- Sonstiges --------------------------------------------------------
    _g("alarm", "Alarmanlage|Alarm|Sicherheitsanlage", "Alarmanlagen", F, {"alarm_control_panel"},
       critical=True),
    _g("camera", "Kamera|Überwachungskamera|Webcam", "Kameras", F, {"camera"}),
    _g("scene", "Szene|Stimmung|Lichtszene", "Szenen", F, {"scene"}),
    _g("script", "Skript|Script|Routine|Ablauf", "Skripte", N, {"script"},
       extra_forms=("Routinen", "Abläufe", "Ablaeufe")),
    _g("automation", "Automation|Automatisierung", "Automationen", F, {"automation"},
       extra_forms=("Automatisierungen",)),
    _g("phone", "Handy|Smartphone|Telefon|Mobiltelefon", "Handys", N, {"notify", "device_tracker"}),
    # "Gerät" is the universal genus: every everyday switchable device.
    _g("device", "Gerät|Verbraucher", "Geräte", N,
       {"light", "switch", "fan", "media_player", "humidifier"},
       extra_forms=("Geraet", "Geraete", "Dinge", "Sachen", "Verbrauchern"), in_everything=True),
)

_BY_KEY: Mapping[str, Genus] = {item.key: item for item in GENERA}

# Linking elements (Fugenelemente) between compound parts, longest first.
COMPOUND_LINKS: tuple[str, ...] = ("es", "en", "er", "ns", "s", "n", "e", "")

# Words that look like compound heads but are ordinary German words.
_NON_DEVICE_WORDS = frozenset(normalize_for_compare(word) for word in (
    "Anlage Anlagen Kiste Ding Sache Sachen Dinge Box Boxen Tor Tore Alarm Klima "
    "Strom Spot Birne Stecker Roboter Player Ablauf Stimmung Bewegung Anwesenheit Präsenz "
    "Verbrauch Musik Fernsehen Lüftung Beschattung Verschattung"
).split())
_UNIVERSAL_WORDS = frozenset({"alles", "allem"})
# Indefinite pronouns ("ist noch was an?") denote any everyday device.
_INDEFINITE_UNIVERSAL_WORDS = frozenset({"etwas", "was", "irgendwas", "irgendetwas"})
_MIN_COMPOUND_HEAD = 4


def genus(key: str) -> Genus:
    return _BY_KEY[key]


def _umlaut_last(stem: str) -> str:
    for index in range(len(stem) - 1, -1, -1):
        char = stem[index]
        if char in "aou":
            if char == "u" and index > 0 and stem[index - 1] == "a":
                return stem[: index - 1] + "äu" + stem[index + 1:]
            return stem[:index] + {"a": "ä", "o": "ö", "u": "ü"}[char] + stem[index + 1:]
        if char in "äöüie":
            return stem
    return stem


def _inflections(lemma: str) -> set[str]:
    """Regular German noun surfaces of one lemma (case forms)."""
    word = lemma.casefold()
    forms = {word, word + "s", word + "n", word + "en", word + "e", word + "es", word + "er", word + "ern"}
    if word.endswith("e"):
        forms.update({word + "n"})
    return forms


def _diminutives(lemma: str) -> set[str]:
    word = lemma.casefold()
    stem = word[:-1] if word.endswith("e") else word
    umlauted = _umlaut_last(stem)
    return {umlauted + "chen", umlauted + "lein", stem + "chen"}


@lru_cache(maxsize=1)
def _form_index() -> Mapping[str, tuple[str, ...]]:
    """Normalized surface form -> genus keys (most specific first)."""
    index: dict[str, list[str]] = {}

    def add(surface: str, key: str) -> None:
        normalized = normalize_for_compare(surface).replace("-", "")
        if not normalized:
            return
        keys = index.setdefault(normalized, [])
        if key not in keys:
            keys.append(key)

    for item in GENERA:
        for lemma in item.lemmas:
            for form in _inflections(lemma):
                add(form, item.key)
            if item.diminutive:
                for form in _diminutives(lemma):
                    add(form, item.key)
        for form in (item.plural, *item.extra_forms):
            add(form, item.key)
    # A child genus (Raffstore) is more specific than its family; keep it
    # first so "Raffstore" means blinds, not every shutter.
    return {
        form: tuple(sorted(keys, key=lambda key: (_BY_KEY[key].parent is None, key)))
        for form, keys in index.items()
    }


def genus_forms(key: str) -> frozenset[str]:
    """Every normalized surface form that names ``key`` directly."""
    return frozenset(form for form, keys in _form_index().items() if key in keys)


@lru_cache(maxsize=1)
def _plural_forms() -> frozenset[str]:
    forms: set[str] = set()
    for item in GENERA:
        plural = normalize_for_compare(item.plural)
        forms.update({plural, plural + "n"} if not plural.endswith("n") else {plural})
        forms.update(normalize_for_compare(form) for form in item.extra_forms)
    # A plural identical to the singular (Fenster, Lautsprecher) is not
    # plural evidence on its own; the article decides.
    singulars = {
        normalize_for_compare(lemma) for item in GENERA for lemma in item.lemmas
    }
    return frozenset(form for form in forms if form not in singulars)


def lookup_genus_word(word: str) -> tuple[str, ...]:
    """Genus keys for one complete word (no compound splitting)."""
    return _form_index().get(normalize_for_compare(word).replace("-", ""), ())


@dataclass(frozen=True)
class WordAnalysis:
    """How one surface word relates to the ontology.

    ``genera`` are the genus keys of the word or of its compound head;
    ``modifier`` is the normalized non-head part of a compound
    ("wohnzimmer" in "Wohnzimmerlicht", "decken" in "Deckenventilator").
    ``plural`` reports a plural surface form of the head.
    """

    word: str
    genera: tuple[str, ...]
    modifier: str | None = None
    head: str | None = None
    plural: bool = False
    universal: bool = False
    indefinite: bool = False
    mass: bool = False


@lru_cache(maxsize=1)
def _mass_forms() -> frozenset[str]:
    return frozenset(
        form
        for item in GENERA
        for lemma in item.mass_lemmas
        for form in {normalize_for_compare(lemma), normalize_for_compare(lemma) + "s"}
    )


# Word formation for detectors: substance + detector head ("Leckage|melder",
# "Flut|sensor", "Brand|warner").  The substance decides the genus.
_DETECTOR_HEADS = ("melder", "sensor", "sensoren", "detektor", "warner", "fuehler", "alarm")
_DETECTOR_SUBSTANCES: Mapping[str, str] = {
    "wasser": "water_detector", "leck": "water_detector", "leckage": "water_detector",
    "flut": "water_detector", "ueberschwemmung": "water_detector", "ueberflutung": "water_detector",
    "feuchte": "water_detector", "nass": "water_detector",
    "rauch": "smoke_detector", "brand": "smoke_detector", "feuer": "smoke_detector",
    "gas": "co_detector", "co": "co_detector", "kohlenmonoxid": "co_detector",
    "bewegung": "motion_detector", "bewegungs": "motion_detector",
    "praesenz": "presence_detector", "anwesenheit": "presence_detector",
    "fenster": "window", "tuer": "door",
    "temperatur": "temperature_sensor", "feuchtigkeit": "humidity_sensor",
}


def _detector_genus(normalized: str) -> str | None:
    for head in _DETECTOR_HEADS:
        if normalized.endswith(head) and len(normalized) > len(head) + 1:
            modifier = normalized[: -len(head)].rstrip("-")
            for stem in modifier_stems(modifier):
                key = _DETECTOR_SUBSTANCES.get(stem)
                if key is not None:
                    return key
    return None


def analyse_word(word: str) -> WordAnalysis | None:
    """Analyse one word as genus word, compound of a genus word, or neither."""
    normalized = normalize_for_compare(word).replace("-", "")
    if not normalized or not normalized.isalnum():
        return None
    if normalized in _UNIVERSAL_WORDS:
        return WordAnalysis(normalized, ("device",), universal=True)
    if normalized in _INDEFINITE_UNIVERSAL_WORDS:
        return WordAnalysis(normalized, ("device",), universal=True, indefinite=True)
    index = _form_index()
    direct = index.get(normalized)
    if direct:
        return WordAnalysis(
            normalized, direct, head=normalized,
            plural=normalized in _plural_forms(),
            mass=normalized in _mass_forms(),
        )
    detector = _detector_genus(normalized)
    if detector is not None:
        return WordAnalysis(normalized, (detector,), head=normalized)
    # Compound: longest genus form that ends the word and leaves a
    # modifier of at least three letters.
    best: tuple[str, tuple[str, ...]] | None = None
    for form, keys in index.items():
        # Short heads ("tor", "box") hide inside ordinary words ("Monitor");
        # their compounds are listed as lemmas instead.
        if len(form) < _MIN_COMPOUND_HEAD or form in _NON_DEVICE_WORDS:
            continue
        if normalized.endswith(form) and len(normalized) - len(form) >= 3:
            if best is None or len(form) > len(best[0]):
                best = (form, keys)
    if best is None:
        return None
    head, keys = best
    modifier = normalized[: -len(head)]
    return WordAnalysis(
        normalized,
        keys,
        modifier=modifier,
        head=head,
        plural=head in _plural_forms(),
        mass=head in _mass_forms(),
    )


def modifier_stems(modifier: str) -> tuple[str, ...]:
    """Candidate base forms of a compound modifier ("kuechen" -> "kueche")."""
    stems: list[str] = [modifier]
    for link in COMPOUND_LINKS:
        if link and modifier.endswith(link) and len(modifier) - len(link) >= 2:
            base = modifier[: -len(link)]
            stems.extend((base, base + "e"))
    return tuple(dict.fromkeys(stems))


@dataclass(frozen=True)
class GenusMatch:
    genus: Genus
    by_class: bool
    by_name: bool


def _name_words(entity: EntitySnapshot) -> tuple[str, ...]:
    words: list[str] = []
    for name in (entity.friendly_name, *entity.aliases):
        for raw in normalize_for_compare(name).replace("-", " ").split():
            words.append(raw)
    return tuple(words)


def _name_mentions(entity: EntitySnapshot, key: str) -> bool:
    for word in _name_words(entity):
        analysis = analyse_word(word)
        if analysis is not None and key in analysis.genera:
            return True
    return False


def _direct_member(entity: EntitySnapshot, item: Genus) -> GenusMatch | None:
    if entity.domain not in item.domains:
        return None
    if any(
        normalize_for_compare(excluded) in normalize_for_compare(entity.friendly_name)
        for excluded in item.exclude_names
    ):
        return None
    by_name = _name_mentions(entity, item.key)
    if item.device_classes is None:
        return GenusMatch(item, by_class=True, by_name=by_name)
    if entity.device_class in item.device_classes:
        return GenusMatch(item, by_class=True, by_name=by_name)
    if item.name_evidence and by_name and entity.device_class in (None, ""):
        return GenusMatch(item, by_class=False, by_name=True)
    return None


@lru_cache(maxsize=8192)
def _entity_genera_cached(
    entity_id: str, domain: str, device_class: str | None, names: tuple[str, ...]
) -> frozenset[str]:
    probe = EntitySnapshot(
        entity_id, names[0], domain, "", device_class=device_class, aliases=names[1:]
    )
    found: set[str] = set()
    for item in GENERA:
        if _direct_member(probe, item) is not None:
            found.add(item.key)
    # Every member of a child genus belongs to its parent family.
    for key in tuple(found):
        parent = _BY_KEY[key].parent
        while parent is not None:
            found.add(parent)
            parent = _BY_KEY[parent].parent
    # A generic family match without class evidence is dropped when a more
    # specific sibling explains the entity: a switch named "Steckdose" is a
    # socket, still a switch; a media_player named "TV" is not "Musik".
    if "tv" in found:
        found.discard("radio")
    return frozenset(found)


def entity_genera(entity: EntitySnapshot) -> frozenset[str]:
    """All genus keys an entity belongs to (including parent families)."""
    return _entity_genera_cached(
        entity.entity_id,
        entity.domain,
        entity.device_class,
        (entity.friendly_name, *entity.aliases),
    )


def entity_has_genus(entity: EntitySnapshot, key: str) -> bool:
    return key in entity_genera(entity)


@dataclass(frozen=True)
class GenusPhrase:
    """German noun phrases for one genus (for honest answers)."""

    nominative: str
    accusative_negative: str
    plural: str
    gender: Gender = field(default=Gender.NEUTER)


def negative_phrase(key: str, *, plural: bool = False) -> str:
    """"keinen Ventilator", "keine Lampe", "kein Licht", "keine Rollläden"."""
    item = _BY_KEY[key]
    if plural:
        return f"keine {item.plural}"
    article = {Gender.MASCULINE: "keinen", Gender.FEMININE: "keine", Gender.NEUTER: "kein"}[item.gender]
    return f"{article} {item.singular}"


def definite_phrase(key: str, *, plural: bool = False, case: str = "nom") -> str:
    item = _BY_KEY[key]
    if plural:
        return f"die {item.plural}"
    article = {
        ("nom", Gender.MASCULINE): "der", ("nom", Gender.FEMININE): "die",
        ("nom", Gender.NEUTER): "das", ("acc", Gender.MASCULINE): "den",
        ("acc", Gender.FEMININE): "die", ("acc", Gender.NEUTER): "das",
    }[(case, item.gender)]
    return f"{article} {item.singular}"
