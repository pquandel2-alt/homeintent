"""Consumption over a period (7.9.2 B5): energy, never power.

"Wie viel Strom hat die Waschmaschine heute verbraucht?", "Was hat heute am
meisten verbraucht?", "Wie viel Energie haben wir gestern verbraucht?".

* A period (heute, gestern, diese Woche, diesen Monat, letzte Nacht, letzte
  Woche) makes it a question about *energy* (kWh). "gerade/jetzt/aktuell"
  or no period stays the current *power* (W) of the established queries -
  the two are never confused.
* Source per device: an energy sensor (``device_class: energy``, kWh/Wh)
  from the recorder as the sum of its increases in the period (a meter
  reset is no negative consumption); a device with only a power sensor is
  integrated over its recorded history and the answer says "geschätzt aus
  der Leistung".
* Ranking: the three largest consumers in kWh (house meters excluded).
* Costs only with a configured price (option ``energy_price`` in €/kWh).
* Without recorder data: an honest answer, never a guessed number.

The parser and the arithmetic are Home-Assistant-free; ``async_answer``
reads the recorder through ``history_query.async_get_numeric_samples``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .entities import EntitySnapshot, format_spoken_number, normalize_for_compare, spoken_unit

__all__ = (
    "CONF_ENERGY_PRICE",
    "async_energy_dashboard_price",
    "fixed_grid_price",
    "EnergyQuery",
    "async_answer",
    "consumption_kwh",
    "current_power_answer",
    "parse_energy_query",
    "spoken_period",
)

CONF_ENERGY_PRICE = "energy_price"

_CONSUME_VERBS = frozenset({
    "verbraucht", "verbrauchte", "verbrauchten", "gebraucht", "gezogen", "verbrauch", "gekostet",
    "verbraucht hat",
})
_ENERGY_WORDS = frozenset({"strom", "energie", "kwh", "kilowattstunden", "stromverbrauch", "energieverbrauch"})
_NOW_WORDS = frozenset({"gerade", "jetzt", "aktuell", "momentan", "derzeit", "zurzeit"})
# A monitoring request ("Sag mir Bescheid, wenn heute mehr als …") is no question.
_MONITOR_WORDS = frozenset({"wenn", "sobald", "falls", "bescheid", "melde", "benachrichtige", "warne", "warn"})
# A statistics question about a sensor ("der höchste Wert vom Stromverbrauch
# Haus gestern", "im Mittel") stays with the history statistics.
_STAT_WORDS = frozenset({
    "wert", "werte", "durchschnitt", "durchschnittlich", "durchschnittliche", "durchschnittlichen",
    "mittel", "mittelwert", "minimum", "maximum", "minimal", "maximal", "niedrigste", "niedrigster",
    "niedrigsten", "hoechste", "hoechster", "spitzenwert", "veraendert", "veraenderung",
})
_RANK_WORDS = frozenset({"meisten", "meiste", "groessten", "groesste", "hoechsten"})
_HOUSE_WORDS = frozenset({"wir", "haus", "insgesamt", "gesamt", "ganze", "ganzen", "haushalt", "wohnung"})
# A whole-house meter is no "consumer" in a ranking.
_TOTAL_NAME_WORDS = frozenset({"haus", "gesamt", "netz", "zaehler", "energiezaehler", "stromzaehler", "hausanschluss"})
_FILLER = frozenset({
    "wie", "viel", "wieviel", "was", "hat", "haben", "habe", "die", "der", "das", "den", "dem", "des", "ein",
    "eine", "am", "an", "im", "in", "von", "vom", "strom", "energie", "heute", "gestern", "diese", "dieser",
    "diesen", "woche", "monat", "letzte", "letzten", "nacht", "verbraucht", "verbrauchte", "gebraucht",
    "gezogen", "gekostet", "mich", "uns", "wir", "ich", "meisten", "meiste", "sag", "mir", "bitte",
    "kilowattstunden", "kwh", "es", "sind", "wurde", "wurden", "denn", "eigentlich", "so", "seit", "morgen",
    "vergangene", "vergangenen", "kostet", "kosten", "welches", "welche", "geraet", "geraete",
})


@dataclass(frozen=True)
class EnergyQuery:
    start: datetime
    end: datetime
    label: str  # "heute", "gestern", "diese Woche", …
    device_words: tuple[str, ...]  # normalized words naming the device, () = house
    ranking: bool
    costs: bool


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def spoken_period(words: Sequence[str], now: datetime) -> tuple[datetime, datetime, str] | None:
    """The period of a consumption or summary question."""
    from .nlu.history_frame import period_key, period_range

    present = set(words)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if any(a in {"letzte", "vergangene", "heute"} and b == "nacht" for a, b in zip(words, words[1:])):
        start = midnight - timedelta(hours=2)  # 22:00 the evening before
        end = min(now, midnight + timedelta(hours=6))
        return start, end, "letzte Nacht"
    if "seit" in present and "morgen" in present and "heute" in present:
        return midnight + timedelta(hours=5), now, "seit heute Morgen"
    for index, word in enumerate(words[:-1]):
        if word == "seit" and words[index + 1].isdigit():
            hour = int(words[index + 1])
            if 0 <= hour <= 23:
                start = midnight + timedelta(hours=hour)
                if start > now:
                    start -= timedelta(days=1)
                return start, now, f"seit {hour} Uhr"
    key = period_key(words)
    if key in {"diese woche", "letzte woche", "dieser monat", "heute", "gestern", "vorgestern"}:
        found = period_range(key, now)
        if found is not None:
            return found[0], found[1], found[3]
    if "monat" in present and present & {"diesen", "diesem", "dieser"}:
        return midnight.replace(day=1), now, "diesen Monat"
    if "woche" in present and present & {"diese", "dieser", "diesen"}:
        return midnight - timedelta(days=now.weekday()), now, "diese Woche"
    return None


def current_power_answer(text: str, entities: Sequence[EntitySnapshot]) -> str | None:
    """"Wie viel verbraucht die Waschmaschine gerade?" - power in W, from
    the device's power sensor right now (never energy)."""
    words = _words(text)
    present = set(words)
    if not (present & _NOW_WORDS and present & {"verbraucht", "zieht", "braucht"} and present & {"wie", "wieviel", "was"}):
        return None
    device = {word for word in words if word not in _FILLER and word not in _NOW_WORDS
              and word not in {"verbraucht", "zieht", "braucht"}}
    if not device or device & _HOUSE_WORDS:
        return None  # the house's own power keeps its established answer
    named = [entity for entity in entities if _is_power(entity) and device <= _name_words(entity)]
    if len(named) != 1:
        return None
    entity = named[0]
    try:
        value = float(entity.state)
    except (TypeError, ValueError):
        return f"{entity.friendly_name} meldet gerade keinen Wert."
    return f"{_subject(_device_label(entity))} verbraucht gerade {format_spoken_number(value)} {spoken_unit(entity.unit or 'W')}."


def parse_energy_query(text: str, now: datetime) -> EnergyQuery | None:
    """A question about consumed energy in a period, else ``None``."""
    words = _words(text)
    present = set(words)
    if not words or present & _NOW_WORDS or present & _MONITOR_WORDS or present & _STAT_WORDS:
        return None
    asks = bool(present & {"wie", "wieviel", "was", "welches", "welche", "sag"})
    consumes = bool(present & _CONSUME_VERBS) or "verbrauch" in present or "stromverbrauch" in present
    if not (asks and consumes):
        return None
    period = spoken_period(words, now)
    if period is None:
        return None
    ranking = bool(present & _RANK_WORDS)
    if not ranking and not (present & _ENERGY_WORDS or "verbraucht" in present):
        return None
    device = tuple(word for word in words if word not in _FILLER and word not in _HOUSE_WORDS and not word.isdigit())
    if present & _HOUSE_WORDS and not ranking:
        device = ()
    return EnergyQuery(
        period[0], period[1], period[2], () if ranking else device, ranking,
        bool(present & {"gekostet", "kostet", "kosten"}),
    )


# --- sources -------------------------------------------------------------------

def _is_energy(entity: EntitySnapshot) -> bool:
    return entity.domain == "sensor" and (entity.device_class == "energy" or entity.unit in {"kWh", "Wh", "MWh"})


def _is_power(entity: EntitySnapshot) -> bool:
    return entity.domain == "sensor" and (entity.device_class == "power" or entity.unit in {"W", "kW"})


def _name_words(entity: EntitySnapshot) -> set[str]:
    found: set[str] = set()
    for name in (entity.friendly_name, *entity.aliases):
        found.update(_words(name))
    return found


def _subject(label: str) -> str:
    """"Waschmaschine" -> "Die Waschmaschine" (sentence-initial)."""
    from .nlu.german_morphology import definite_entity_phrase

    phrase = definite_entity_phrase(label)
    text = phrase[0] if phrase is not None else label
    return text[:1].upper() + text[1:]


def _is_total(entity: EntitySnapshot) -> bool:
    return bool(_name_words(entity) & _TOTAL_NAME_WORDS) or (
        entity.area_id is None and "stromverbrauch" in _name_words(entity)
    )


def _device_label(entity: EntitySnapshot) -> str:
    """"Leistung Waschmaschine" -> "Waschmaschine" (the consumer, not the meter)."""
    words = [
        word for word in entity.friendly_name.split()
        if normalize_for_compare(word) not in {"leistung", "energie", "verbrauch", "stromverbrauch", "zaehler"}
    ]
    return " ".join(words) or entity.friendly_name


def sources_for(query: EnergyQuery, entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    """The meters/sensors that answer ``query`` (energy before power)."""
    if query.ranking:
        consumers: dict[str, EntitySnapshot] = {}
        for entity in sorted(entities, key=lambda item: item.entity_id):
            if not (_is_energy(entity) or _is_power(entity)) or _is_total(entity):
                continue
            label = normalize_for_compare(_device_label(entity))
            known = consumers.get(label)
            if known is None or (_is_energy(entity) and not _is_energy(known)):
                consumers[label] = entity
        return list(consumers.values())
    if not query.device_words:
        totals = [entity for entity in entities if (_is_energy(entity) or _is_power(entity)) and _is_total(entity)]
        energy = [entity for entity in totals if _is_energy(entity)]
        return (energy or totals)[:1]
    wanted = set(query.device_words)
    named = [
        entity for entity in entities
        if (_is_energy(entity) or _is_power(entity)) and wanted <= _name_words(entity)
    ]
    energy = [entity for entity in named if _is_energy(entity)]
    return (energy or named)[:1] if len(energy or named) == 1 else (energy or named)


# --- arithmetic -------------------------------------------------------------------

def consumption_kwh(
    entity: EntitySnapshot, samples: Sequence[tuple[datetime, float]], end: datetime
) -> float | None:
    """kWh in the window: increases of an energy meter, or the step-hold
    integral of a power sensor. ``None`` without data."""
    if not samples:
        return None
    ordered = sorted(samples, key=lambda item: item[0])
    if _is_energy(entity):
        scale = {"Wh": 0.001, "MWh": 1000.0}.get(entity.unit or "", 1.0)
        total = 0.0
        for (_, before), (_, after) in zip(ordered, ordered[1:]):
            if after >= before:
                total += after - before
            else:
                total += after  # a reset: the meter counts again from 0
        return total * scale
    scale = 1.0 if entity.unit == "kW" else 0.001
    energy = 0.0
    for (moment, value), following in zip(ordered, [*ordered[1:], (end, ordered[-1][1])]):
        hours = max(0.0, (following[0] - moment).total_seconds()) / 3600
        energy += max(0.0, value) * scale * hours
    return energy


def _kwh(value: float) -> str:
    return f"{format_spoken_number(round(value, 1 if value >= 1 else 2))} kWh"


def _costs(kwh: float, price: float | None) -> str:
    if price is None:
        return ""
    return f" Das sind etwa {format_spoken_number(round(kwh * price, 2))} €."


def _period_prefix(label: str) -> str:
    return label[:1].upper() + label[1:]


def fixed_grid_price(prefs: Mapping[str, Any] | None) -> float | None:
    """The one fixed grid price (€/kWh) of the Home Assistant energy
    dashboard - ``None`` without one, or with several different prices
    (a tariff is never guessed)."""
    prices: set[float] = set()
    for source in (prefs or {}).get("energy_sources", ()) or ():
        if not isinstance(source, Mapping) or source.get("type") != "grid":
            continue
        flows = source.get("flow_from") or [source]
        for flow in flows:
            price = flow.get("number_energy_price") if isinstance(flow, Mapping) else None
            if isinstance(price, (int, float)) and price > 0:
                prices.add(float(price))
    return prices.pop() if len(prices) == 1 else None


async def async_energy_dashboard_price(hass: Any) -> float | None:
    """``fixed_grid_price`` of the configured energy dashboard."""
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
    except Exception:  # noqa: BLE001 - no energy dashboard: no price
        return None
    return fixed_grid_price(getattr(manager, "data", None))


async def async_answer(
    query: EnergyQuery,
    entities: Sequence[EntitySnapshot],
    samples_of: Callable[[str, datetime, datetime], Awaitable[list[tuple[datetime, float]] | None]],
    options: Mapping[str, object],
) -> str:
    """The spoken answer; ``samples_of`` returns ``None`` when the recorder
    cannot answer at all."""
    price_raw = options.get(CONF_ENERGY_PRICE)
    price = float(price_raw) if isinstance(price_raw, (int, float)) and price_raw > 0 else None
    sources = sources_for(query, entities)
    if not sources:
        if query.device_words:
            spoken = " ".join(query.device_words)
            return f"Für „{spoken}“ finde ich keinen Energie- oder Leistungssensor."
        return "Ich finde keinen Energie- oder Leistungssensor."
    if not query.ranking and len(sources) > 1:
        names = " oder ".join(entity.friendly_name for entity in sources[:4])
        return f"Welchen Verbrauch meinst du: {names}?"
    results: list[tuple[EntitySnapshot, float]] = []
    recorder_missing = True
    for entity in sources:
        samples = await samples_of(entity.entity_id, query.start, query.end)
        if samples is None:
            continue
        recorder_missing = False
        kwh = consumption_kwh(entity, samples, query.end)
        if kwh is not None:
            results.append((entity, kwh))
    if recorder_missing:
        return (
            "Dafür brauche ich den Verlauf von Home Assistant (Recorder); er ist gerade nicht "
            "verfügbar. Einen Wert rate ich nicht."
        )
    if not results:
        names = ", ".join(f"„{entity.friendly_name}“" for entity in sources[:3])
        return f"Für {query.label} liegen mir keine Verlaufsdaten von {names} vor."
    if query.ranking:
        top = sorted(results, key=lambda item: item[1], reverse=True)[:3]
        listed = [f"{_device_label(entity)} {_kwh(kwh)}" for entity, kwh in top]
        text = ", ".join(listed[:-1]) + " und " + listed[-1] if len(listed) > 1 else listed[0]
        estimated = any(not _is_energy(entity) for entity, _ in top)
        suffix = " (teils geschätzt aus der Leistung)" if estimated else ""
        return f"{_period_prefix(query.label)} am meisten verbraucht: {text}{suffix}." + (
            _costs(sum(kwh for _, kwh in top), price) if query.costs else ""
        )
    entity, kwh = results[0]
    estimated = not _is_energy(entity)
    label = _device_label(entity) if query.device_words else None
    how = " etwa" if estimated else ""
    tail = " (geschätzt aus der Leistung)" if estimated else ""
    if label is None:
        text = f"{_period_prefix(query.label)} wurden{how} {_kwh(kwh)} verbraucht ({entity.friendly_name}){tail}."
    else:
        text = f"{_subject(label)} hat {query.label}{how} {_kwh(kwh)} verbraucht{tail}."
    return text + _costs(kwh, price)


def samples_reader(hass: Any) -> Callable[[str, datetime, datetime], Awaitable[list[tuple[datetime, float]] | None]]:
    """Recorder samples, ``None`` when the recorder is not loaded."""
    from .history_query import async_read_numeric_samples

    async def read(entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, float]] | None:
        config = getattr(hass, "config", None)
        components = getattr(config, "components", None)
        if components is not None and "recorder" not in components:
            return None
        return await async_read_numeric_samples(hass, entity_id, start, end)

    return read
