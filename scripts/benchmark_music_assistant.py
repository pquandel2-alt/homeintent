#!/usr/bin/env python3
"""Deterministic Music Assistant adapter benchmark without network or writes."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "custom_components"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.media import MediaRequest  # noqa: E402
from homeintent.music_assistant import async_resolve_music_assistant  # noqa: E402


class _Services:
    """Immediate read-only service response matching HA's response contract."""

    def __init__(self) -> None:
        self.calls = 0

    def has_service(self, domain: str, service: str) -> bool:
        return domain == "music_assistant" and service in {"search", "play_media"}

    async def async_call(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        self.calls += 1
        return {"artists": [{"name": "Queen", "uri": "library://artist/queen"}]}


class _Hass:
    def __init__(self) -> None:
        self.services = _Services()


def _entities(size: int) -> tuple[EntitySnapshot, ...]:
    filler = tuple(
        EntitySnapshot(f"sensor.benchmark_{index}", f"Sensor {index}", "sensor", "0")
        for index in range(max(0, size - 1))
    )
    player = EntitySnapshot(
        "media_player.wohnzimmer",
        "Wohnzimmer Lautsprecher",
        "media_player",
        "idle",
        area_id="living",
        area_name="Wohnzimmer",
        integration="music_assistant",
        config_entry_id="mass-entry",
    )
    return (*filler, player)


async def _measure(registry_size: int, iterations: int, warmup: int) -> list[float]:
    hass = _Hass()
    entities = _entities(registry_size)
    request = MediaRequest(
        "play",
        ("queen",),
        text="Spiele Queen im Wohnzimmer.",
        media_type="artist",
    )
    samples: list[float] = []
    for index in range(warmup + iterations):
        started = time.perf_counter()
        result = await async_resolve_music_assistant(
            hass, request, entities, satellite_area_id="living"
        )
        elapsed = (time.perf_counter() - started) * 1000
        if result is None or result.plan is None or result.question:
            raise RuntimeError("benchmark request did not produce one safe typed plan")
        if index >= warmup:
            samples.append(elapsed)
    if hass.services.calls != warmup + iterations:
        raise RuntimeError("benchmark performed an unexpected number of library reads")
    return samples


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-size", type=int, default=5000)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--max-p95-ms", type=float, default=100.0)
    args = parser.parse_args()
    if args.registry_size < 1 or args.iterations < 1 or args.warmup < 0:
        parser.error("registry size and iterations must be positive; warmup must be non-negative")

    samples = asyncio.run(_measure(args.registry_size, args.iterations, args.warmup))
    median = statistics.median(samples)
    p95 = _percentile(samples, 0.95)
    print(
        f"music_assistant_resolution registry={args.registry_size} "
        f"median={median:.3f}ms p95={p95:.3f}ms budget={args.max_p95_ms:.0f}ms"
    )
    return int(p95 > args.max_p95_ms)


if __name__ == "__main__":
    raise SystemExit(main())
