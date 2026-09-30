#!/usr/bin/env python3
"""Latency of whole conversation turns (7.7).

``scripts/benchmark_v6_baseline.py`` measures ``engine.understand``; this
measures ``NluConversationEntity._async_handle_message`` - the cascade, the
arbiter, the domain controllers - on the stub test house enlarged to a
registry size (copies of the house in further, uniquely named areas).
``--root`` runs an older tree (git worktree) for a before/after comparison.

    python scripts/benchmark_turn.py --entities 5000
    python scripts/benchmark_turn.py --entities 5000 --root /tmp/old
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
SENTENCES = (
    "Schalte das Küchenlicht ein.",
    "Mach die Stehlampe etwas heller.",
    "Ist das Fenster im Bad offen?",
    "Wie warm ist es im Wohnzimmer?",
    "Mir ist kalt.",
    "Fahr oben alle Rollläden runter.",
    "Schalte in zehn Minuten das Flurlicht aus.",
    "Welche Lichter sind an?",
    "Setze Milch auf die Einkaufsliste.",
    "Ich gehe schlafen.",
    "Mach die Heizung im Bad zwei Grad wärmer.",
    "Könntest du vielleicht die Markise einfahren?",
)


def _enlarged(entities: list, size: int) -> list:
    result = list(entities)
    copy = 0
    while len(result) < size:
        copy += 1
        for entity in entities:
            if len(result) >= size:
                break
            if entity.area_id is None:
                continue
            object_id = entity.entity_id.split(".", 1)[1]
            result.append(replace(
                entity,
                entity_id=f"{entity.domain}.{object_id}_kopie{copy}",
                friendly_name=f"{entity.friendly_name} Anbau{copy}",
                area_id=f"{entity.area_id}_anbau{copy}",
                area_name=f"{entity.area_name} Anbau{copy}",
                aliases=(),
            ))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=SCRIPT_ROOT)
    parser.add_argument("--entities", type=int, default=5000)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument(
        "--fresh-agent", action="store_true",
        help="build a new conversation agent for every turn (the 7.7 method); "
        "by default one agent answers all turns, as in Home Assistant",
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.root.resolve() / "custom_components"))
    sys.path.insert(0, str(SCRIPT_ROOT / "tests"))
    import _ha_stub

    _ha_stub.install()
    logging.disable(logging.CRITICAL)
    from _testhaus import HouseConversation, house_entities

    class _MonkeyPatch:
        def setattr(self, target: object, name: str, value: object) -> None:
            setattr(target, name, value)

    entities = _enlarged(house_entities(), args.entities)
    samples: list[float] = []
    shared = None if args.fresh_agent else HouseConversation(_MonkeyPatch(), entities=entities)
    for iteration in range(args.iterations + 1):
        for index, sentence in enumerate(SENTENCES):
            house = shared or HouseConversation(_MonkeyPatch(), entities=entities)
            house.conversation_id = f"bench-{iteration}-{index}"
            start = time.perf_counter()
            house.say(sentence)
            elapsed = (time.perf_counter() - start) * 1000
            if iteration:  # the first round warms caches
                samples.append(elapsed)
    samples.sort()

    def percentile(share: float) -> float:
        return samples[max(0, int(len(samples) * share + 0.5) - 1)]

    p95 = percentile(0.95)
    print(
        f"{len(entities)} Entitäten, {len(samples)} Turns "
        f"({'neuer Agent je Turn' if args.fresh_agent else 'ein Agent'}): "
        f"p50 {statistics.median(samples):.1f} ms, p90 {percentile(0.90):.1f} ms, "
        f"p95 {p95:.1f} ms, p99 {percentile(0.99):.1f} ms, max {samples[-1]:.1f} ms"
    )
    return 1 if args.max_p95_ms is not None and p95 > args.max_p95_ms else 0


if __name__ == "__main__":
    raise SystemExit(main())
