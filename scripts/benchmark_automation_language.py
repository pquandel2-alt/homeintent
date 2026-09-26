"""Latency of the 7.2.0 automation language path (spec §70).

Measures ``NluEngine.match_automation`` - segmentation, typed grounding,
action reading, validation - for every development-corpus utterance, on the
evaluation house and on a house padded to ``--registry-size`` entities.

    python scripts/benchmark_automation_language.py --registry-size 5000 --max-p95-ms 100
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_eval import CORPUS_DIR, load_cases  # noqa: E402
from _automation_world import WORLD  # noqa: E402
from homeintent.engine import NluEngine  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.world_model import WorldModel, build_world_model  # noqa: E402


def _padded(size: int) -> list[EntitySnapshot]:
    entities = list(WORLD)
    index = 0
    while len(entities) < size:
        area = f"raum{index % 200}"
        entities.append(EntitySnapshot(
            f"sensor.messwert_{index}", f"Messwert {index}", "sensor", "1",
            area_id=area, area_name=f"Raum {index % 200}", unit="W",
        ))
        index += 1
    return entities


def _measure(engine: NluEngine, sentences: list[str], entities: list[EntitySnapshot]) -> list[float]:
    # Production passes the per-turn WorldModel, whose entity index is built once.
    world: WorldModel = build_world_model(entities, [])
    for sentence in sentences[:20]:
        engine.match_automation(sentence, entities, world)  # warm-up
    samples: list[float] = []
    for sentence in sentences:
        started = time.perf_counter()
        engine.match_automation(sentence, entities, world)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-size", type=int, default=5000)
    parser.add_argument("--max-p95-ms", type=float, default=100.0)
    args = parser.parse_args()
    cases = load_cases(sorted(CORPUS_DIR.glob("dev_*.txt")))
    sentences = [turn for case in cases for turn in case.turns[:1]]
    engine = NluEngine()
    failed = False
    for label, entities in (("house", list(WORLD)), (f"registry={args.registry_size}", _padded(args.registry_size))):
        samples = _measure(engine, sentences, entities)
        p95 = _p95(samples)
        print(
            f"automation_language {label:>15}  n={len(samples)}  mean={statistics.mean(samples):7.2f}ms  "
            f"p50={statistics.median(samples):7.2f}ms  p95={p95:7.2f}ms  max={max(samples):7.2f}ms"
        )
        failed = failed or p95 > args.max_p95_ms
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
