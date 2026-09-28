"""Places in the house: areas, floors and their German names and aliases.

A place is anything a person can say to narrow a target: an area ("im
Büro"), an area alias ("im Arbeitszimmer", "im Bad"), a floor or floor
alias ("im Obergeschoss", "im OG", "oben"), a level word without a
registry alias ("im Keller", "draußen", "drinnen"), the whole home ("im
ganzen Haus", "überall") or the speaker's own room ("hier").

The lexicon is built from the live registry snapshot (``EntitySnapshot``
area/floor fields), the generic level words below are data, and one scanner
finds place mentions in any token sequence.  Commands, questions,
automations and notifications all use the same scanner, so a floor that
works in one path works in every path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from functools import lru_cache
from typing import Iterable, Mapping, Sequence

from ..entities import EntitySnapshot, is_outdoor_entity, normalize_for_compare
from .device_ontology import modifier_stems
from .german_morphology import dative_location_phrase

__all__ = (
    "LEVEL_WORDS",
    "Place",
    "PlaceKind",
    "PlaceLexicon",
    "PlaceMention",
    "build_place_lexicon",
)


class PlaceKind(Enum):
    AREA = auto()
    FLOOR = auto()
    HOUSE = auto()
    INDOOR = auto()
    OUTDOOR = auto()
    HERE = auto()


@dataclass(frozen=True)
class Place:
    kind: PlaceKind
    name: str
    area_ids: frozenset[str] = frozenset()
    floor_ids: frozenset[str] = frozenset()

    @property
    def label(self) -> str:
        """Dative location phrase: "im Büro", "in der Küche", "draußen"."""
        if self.kind is PlaceKind.HOUSE:
            return "im ganzen Haus"
        if self.kind is PlaceKind.OUTDOOR:
            return "draußen"
        if self.kind is PlaceKind.INDOOR:
            return "im Haus"
        if self.kind is PlaceKind.HERE:
            return "hier"
        if " und " in self.name:
            return " und ".join(
                dative_location_phrase(part) for part in self.name.split(" und ")
            )
        return dative_location_phrase(self.name)

    def contains(self, entity: EntitySnapshot) -> bool:
        if self.kind is PlaceKind.HOUSE:
            return True
        if self.kind is PlaceKind.OUTDOOR:
            return is_outdoor_entity(entity)
        if self.kind is PlaceKind.INDOOR:
            return entity.area_id is not None and not is_outdoor_entity(entity)
        if self.kind is PlaceKind.FLOOR:
            return entity.floor_id in self.floor_ids
        return entity.area_id in self.area_ids

    @property
    def specific(self) -> bool:
        return self.kind in {PlaceKind.AREA, PlaceKind.FLOOR, PlaceKind.HERE}


@dataclass(frozen=True)
class PlaceMention:
    place: Place
    token_start: int
    token_end: int
    explicit_preposition: bool


# Generic German level words.  A registry floor/area alias with the same
# word always wins; these only apply when the house did not name them.
LEVEL_WORDS: Mapping[str, str] = {
    "oben": "upper",
    "obergeschoss": "upper",
    "og": "upper",
    "obere etage": "upper",
    "oberen etage": "upper",
    "ersten stock": "upper",
    "erster stock": "upper",
    "1 stock": "upper",
    "dachgeschoss": "upper",
    "unten": "ground",
    "erdgeschoss": "ground",
    "eg": "ground",
    "parterre": "ground",
    "keller": "basement",
    "untergeschoss": "basement",
    "ug": "basement",
    "draussen": "outdoor",
    "aussen": "outdoor",
    "im freien": "outdoor",
    "ausserhalb": "outdoor",
    "drinnen": "indoor",
    "innen": "indoor",
    "ganzen haus": "house",
    "gesamten haus": "house",
    "ganzen wohnung": "house",
    "ganze haus": "house",
    "ueberall": "house",
    "allen raeumen": "house",
    "allen zimmern": "house",
    "haus": "house",
    "hier": "here",
    "hier drin": "here",
}
_LOCATIVE = frozenset({"im", "in", "am", "an", "beim", "bei", "auf", "vom", "von", "aus"})
_ARTICLES = frozenset({"der", "dem", "den", "die", "das"})
# "unten"/"oben" directly after these words is a direction, not a floor.
_DIRECTION_VERBS = frozenset({"nach", "fahr", "fahre", "fahren", "runter", "hoch"})
# "Sind alle Rollläden unten?": closing the sentence without a preposition,
# after a shading kind, "oben"/"unten" is its position, not a floor (7.6.0).
_POSITIONED_GENERA = frozenset({"shutter", "raffstore", "awning", "curtain", "garage_door"})


def level_is_position(text: str, start: int, end: int) -> bool:
    """The same rule on raw text: is the level word at ``start:end`` the
    position of a named shading device ("Sind alle Rollläden unten?")?"""
    if text[end:].strip(" ?.!"):
        return False
    import re as _re

    words = [normalize_for_compare(word) for word in _re.findall(r"[\wäöüß]+", text[:start])]
    return _is_position([*words, normalize_for_compare(text[start:end])], len(words))


def _is_position(words: Sequence[str], start: int) -> bool:
    from .device_ontology import analyse_word

    if start != len(words) - 1 or (start > 0 and words[start - 1] in _LOCATIVE):
        return False
    return any(
        (analysis := analyse_word(word)) is not None and set(analysis.genera) & _POSITIONED_GENERA
        for word in words[:start]
    )


@dataclass(frozen=True)
class PlaceLexicon:
    phrases: Mapping[str, Place]
    max_words: int
    areas: Mapping[str, str]
    floors: Mapping[str, tuple[str, int | None]]

    def place_for_area(self, area_id: str) -> Place | None:
        name = self.areas.get(area_id)
        if name is None:
            return None
        return Place(PlaceKind.AREA, name, frozenset({area_id}))

    def scan(self, words: Sequence[str]) -> tuple[PlaceMention, ...]:
        """Find place mentions in normalized words, longest phrases first."""
        found: list[PlaceMention] = []
        taken = [False] * len(words)
        for size in range(min(self.max_words, len(words)), 0, -1):
            for start in range(0, len(words) - size + 1):
                if any(taken[start:start + size]):
                    continue
                phrase = " ".join(words[start:start + size])
                place = self.phrases.get(phrase)
                if place is None:
                    continue
                previous = words[start - 1] if start > 0 else ""
                if phrase in {"oben", "unten"} and (
                    previous in _DIRECTION_VERBS or _is_position(words, start)
                ):
                    continue
                before = start - 1
                while before >= 0 and words[before] in _ARTICLES:
                    before -= 1
                preposition = before >= 0 and words[before] in _LOCATIVE
                if phrase == "haus" and not preposition:
                    continue
                for index in range(start, start + size):
                    taken[index] = True
                found.append(PlaceMention(place, start, start + size, preposition))
        return tuple(sorted(found, key=lambda item: item.token_start))

    def resolve_modifier(self, modifier: str) -> Place | None:
        """Place named by a compound modifier ("kuechen" -> Küche)."""
        for stem in modifier_stems(modifier):
            place = self.phrases.get(stem)
            if place is not None and place.kind in {PlaceKind.AREA, PlaceKind.FLOOR}:
                return place
        return None


def _level_place(
    kind: str,
    floors: Mapping[str, tuple[str, int | None]],
    areas_by_floor: Mapping[str, set[str]],
    area_names: Mapping[str, str],
) -> Place | None:
    if kind == "house":
        return Place(PlaceKind.HOUSE, "Haus")
    if kind in {"upper", "ground", "basement"} and not floors and kind != "basement":
        return None
    if kind == "outdoor":
        return Place(PlaceKind.OUTDOOR, "draußen")
    if kind == "indoor":
        return Place(PlaceKind.INDOOR, "drinnen")
    if kind == "here":
        return Place(PlaceKind.HERE, "hier")
    levels = {floor_id: level for floor_id, (_, level) in floors.items() if level is not None}
    # "oben"/"unten"/"Keller" name exactly one floor: the highest, the
    # ground and the lowest level.  Two floors on the same level make the
    # word ambiguous and it stays unresolved (never widened).
    if kind == "upper":
        extreme = max((level for level in levels.values() if level >= 1), default=None)
    elif kind == "ground":
        extreme = 0 if 0 in levels.values() else None
    else:
        extreme = min((level for level in levels.values() if level < 0), default=None)
    chosen = {floor_id for floor_id, level in levels.items() if level == extreme} if extreme is not None else set()
    if len(chosen) > 1:
        return None
        if not chosen:
            # A house without a basement floor may still have a "Keller" area.
            keller = {
                area_id for area_id, name in area_names.items()
                if "keller" in normalize_for_compare(name)
            }
            if keller:
                return Place(PlaceKind.AREA, "Keller", frozenset(keller))
    if not chosen:
        return None
    names = sorted(floors[floor_id][0] for floor_id in chosen)
    return Place(
        PlaceKind.FLOOR,
        names[0] if len(names) == 1 else " und ".join(names),
        frozenset(area for floor_id in chosen for area in areas_by_floor.get(floor_id, ())),
        frozenset(chosen),
    )


@lru_cache(maxsize=16)
def _build(
    areas: tuple[tuple[str, str, tuple[str, ...], str | None], ...],
    floors: tuple[tuple[str, str, int | None, tuple[str, ...]], ...],
) -> PlaceLexicon:
    phrases: dict[str, Place] = {}
    area_names = {area_id: name for area_id, name, _, _ in areas}
    floor_map = {floor_id: (name, level) for floor_id, name, level, _ in floors}
    areas_by_floor: dict[str, set[str]] = {}
    for area_id, _, _, floor_id in areas:
        if floor_id is not None:
            areas_by_floor.setdefault(floor_id, set()).add(area_id)

    def add(surface: str, place: Place) -> None:
        normalized = normalize_for_compare(surface)
        for key in {normalized, normalized.replace("-", " "), normalized.replace("-", "")}:
            if key and key not in phrases:
                phrases[key] = place

    # Registry names first: exact area/floor names and aliases.
    for area_id, name, aliases, _ in areas:
        place = Place(PlaceKind.AREA, name, frozenset({area_id}))
        for surface in (name, *aliases):
            add(surface, place)
    for floor_id, name, _, aliases in floors:
        place = Place(
            PlaceKind.FLOOR,
            name,
            frozenset(areas_by_floor.get(floor_id, ())),
            frozenset({floor_id}),
        )
        for surface in (name, *aliases):
            add(surface, place)
    # Generic level words only where the registry did not claim the word.
    for word, kind in LEVEL_WORDS.items():
        if word in phrases:
            continue
        place = _level_place(kind, floor_map, areas_by_floor, area_names)
        if place is not None:
            phrases[word] = place
    return PlaceLexicon(
        phrases=phrases,
        max_words=max((len(key.split()) for key in phrases), default=1),
        areas=area_names,
        floors=floor_map,
    )


def build_place_lexicon(entities: Iterable[EntitySnapshot]) -> PlaceLexicon:
    areas: dict[str, tuple[str, str, tuple[str, ...], str | None]] = {}
    floors: dict[str, tuple[str, str, int | None, tuple[str, ...]]] = {}
    for entity in entities:
        if entity.area_id and entity.area_name and entity.area_id not in areas:
            areas[entity.area_id] = (
                entity.area_id, entity.area_name, tuple(entity.area_aliases), entity.floor_id,
            )
        if entity.floor_id and entity.floor_name and entity.floor_id not in floors:
            floors[entity.floor_id] = (
                entity.floor_id, entity.floor_name, entity.floor_level,
                tuple(entity.floor_aliases),
            )
    return _build(tuple(sorted(areas.values())), tuple(sorted(floors.values())))


OUTDOOR_REFERENCE_WORDS = frozenset({
    "draussen", "aussen", "aussentemperatur", "ausserhalb", "freien", "garten",
    "terrasse", "balkon", "hof", "einfahrt",
})


def outdoor_area_ids(entities: Iterable[EntitySnapshot]) -> frozenset[str]:
    """Areas that are outdoors by their own or their floor's name."""
    found: set[str] = set()
    for entity in entities:
        if entity.area_id is None or entity.area_id in found:
            continue
        names = (entity.area_name or "", entity.floor_name or "", *entity.area_aliases)
        if any(
            marker in normalize_for_compare(name)
            for name in names
            for marker in ("aussen", "draussen", "garten", "terrasse", "balkon", "garage", "hof", "einfahrt")
        ):
            found.add(entity.area_id)
    return frozenset(found)
