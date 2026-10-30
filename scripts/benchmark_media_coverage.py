#!/usr/bin/env python3
"""Latency of the 7.13.2 completeness and clause-binding checks.

Measures, per turn and with a fresh entity list each time (as in
production, where every turn builds its own snapshot list):

* ``media_coverage.analyse_media_coverage`` for single and coordinated
  media commands;
* ``clause_completeness.analyse_write_completeness`` for coordinated write
  sentences.
* ``clause_binding.analyse_clause_bindings`` for the typed coordinated
  fail-closed boundary.

Deterministic, no Home Assistant, no writes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "custom_components"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import house_entities  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.media import parse_media_request  # noqa: E402
from homeintent.media_coverage import analyse_media_coverage  # noqa: E402
from homeintent.nlu.clause_completeness import analyse_write_completeness  # noqa: E402
from homeintent.nlu.clause_binding import analyse_clause_bindings  # noqa: E402
from homeintent.nlu.language_frontend import analyse_language  # noqa: E402

MEDIA = (
    "Mach das Radio in der Küche aus.",
    "Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat.",
    "Spiel bitte Bayern 3 in der Küche.",
    "Stell die Lautstärke im Wohnzimmer auf 30 Prozent.",
)
COORDINATED = (
    "Mach das Licht im Büro aus und dann das Licht in der Küche an.",
    "Pausiere das Radio in der Küche und im Wohnzimmer.",
)
CLAUSE_BOUND = (
    "Mach das Licht im Büro aus und dann das Licht in der Küche an.",
    "Spiel Quirlomat in der Küche und pausiere die Musik im Schlafzimmer.",
)


def _registry(size: int) -> list[EntitySnapshot]:
    house = house_entities()
    filler = [
        EntitySnapshot(
            f"light.benchmark_{index}", f"Benchmarklicht {index}", "light", "off",
            area_id=f"bench_{index % 50}", area_name=f"Benchraum {index % 50}",
        )
        for index in range(max(0, size - len(house)))
    ]
    return [*house, *filler]


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-size", type=int, default=5000)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--max-p95-ms", type=float, default=100.0)
    args = parser.parse_args()
    registry = _registry(args.registry_size)
    targets = [entity for entity in registry if entity.area_id in {"buro", "kuche"}]
    # The engine builds one LanguageDocument at the turn boundary and hands
    # that exact object to the binding check.  End-to-end document creation
    # is covered by benchmark_v6_baseline; this script measures only the
    # incremental safety checks and must mirror that reuse contract.
    documents = {
        sentence: analyse_language(sentence, registry)
        for sentence in CLAUSE_BOUND
    }
    failed = False
    for label, sentences, check in (
        ("media_coverage", MEDIA, "media"),
        ("write_completeness", COORDINATED, "write"),
        ("clause_binding", CLAUSE_BOUND, "binding"),
    ):
        samples: list[float] = []
        for iteration in range(args.warmup + args.iterations):
            for sentence in sentences:
                fresh = list(registry)  # a new snapshot list per turn
                started = time.perf_counter()
                if check == "media":
                    request = parse_media_request(sentence)
                    assert request is not None, sentence
                    analyse_media_coverage(sentence, fresh, request)
                elif check == "write":
                    analyse_write_completeness(sentence, fresh, targets)
                else:
                    analyse_clause_bindings(documents[sentence], fresh)
                duration = (time.perf_counter() - started) * 1000
                if iteration >= args.warmup:
                    samples.append(duration)
        p95 = _percentile(samples, 0.95)
        print(
            f"{label:20s} registry={args.registry_size} n={len(samples)} "
            f"median={statistics.median(samples):.2f}ms p95={p95:.2f}ms "
            f"budget={args.max_p95_ms:.0f}ms"
        )
        failed = failed or p95 > args.max_p95_ms
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
