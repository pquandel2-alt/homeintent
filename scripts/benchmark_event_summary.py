#!/usr/bin/env python3
"""7.9.3 A4: latency budget of the event summary in a large house.

Builds a synthetic house of ``--registry-size`` entities (doors, windows,
detectors, motion, locks, persons, many irrelevant sensors and lights) and
a synthetic recorder history of ``--days`` days for every *read* entity
(one change every ``--change-minutes`` minutes), then measures the
HomeIntent part of "Was hab ich verpasst?": choosing the entities to read
(``history_entities``, only relevant classes, bounded), turning the rows
into events (``summarize``, bounded) and rendering the answer.

The recorder read itself runs in Home Assistant's executor and is bounded
by the same entity limit; it is not part of this budget (no recorder here).
Exit code 1 when the p95 exceeds ``--max-p95-ms``.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.event_summary import (  # noqa: E402
    MAX_HISTORY_ENTITIES,
    history_entities,
    render_summary,
    summarize,
)

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
_KINDS = (
    ("binary_sensor", "door", "Tür"), ("binary_sensor", "window", "Fenster"), ("binary_sensor", "motion", "Bewegung"),
    ("binary_sensor", "smoke", "Rauchmelder"), ("lock", None, "Schloss"), ("cover", "garage", "Tor"),
    ("sensor", "temperature", "Temperatur"), ("sensor", "power", "Leistung"), ("light", None, "Licht"),
    ("switch", None, "Schalter"), ("sensor", None, "Status"),
)


def _house(size: int) -> list[EntitySnapshot]:
    entities = []
    for index in range(size):
        domain, device_class, label = _KINDS[index % len(_KINDS)]
        unit = {"temperature": "°C", "power": "W"}.get(device_class or "")
        entities.append(EntitySnapshot(
            f"{domain}.e{index}", f"{label} {index}", domain, "off", device_class=device_class, unit=unit,
        ))
    entities.append(EntitySnapshot("person.p0", "Person", "person", "home"))
    return entities


def _history(ids: list[str], days: int, every: int) -> dict[str, list[tuple[datetime, str]]]:
    start = NOW - timedelta(days=days)
    steps = days * 24 * 60 // every
    history: dict[str, list[tuple[datetime, str]]] = {}
    for offset, entity_id in enumerate(ids):
        domain = entity_id.split(".", 1)[0]
        on, off = {"lock": ("unlocked", "locked"), "cover": ("open", "closed"),
                   "person": ("home", "not_home")}.get(domain, ("on", "off"))
        history[entity_id] = [
            (start + timedelta(minutes=every * step + offset % every), on if step % 2 else off)
            for step in range(steps)
        ]
    return history


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-size", type=int, default=5000)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--change-minutes", type=int, default=60)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    args = parser.parse_args()
    entities = _house(args.registry_size)
    ids = history_entities(entities, None)
    history = _history(ids, args.days, args.change_minutes)
    rows = sum(len(items) for items in history.values())
    timings = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        chosen = history_entities(entities, None)
        events = summarize(
            {key: history[key] for key in chosen}, entities, NOW - timedelta(days=args.days), NOW,
            away=True, speaker_person="person.p0", speaker_is_admin=True,
        )
        render_summary(events, "Während du weg warst", NOW.date())
        timings.append((time.perf_counter() - started) * 1000)
    ordered = sorted(timings)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    print(
        f"Zusammenfassung: {args.registry_size} Entitäten, {len(ids)} gelesen (Grenze {MAX_HISTORY_ENTITIES}), "
        f"{args.days} Tage, {rows} Verlaufszeilen, {len(events)} Ereignisse; "
        f"p50 {statistics.median(timings):.1f} ms, p95 {p95:.1f} ms (Budget {args.max_p95_ms:.0f} ms)"
    )
    return 0 if p95 <= args.max_p95_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())
