"""Export the live test house as a stub-suite fixture.

Usage: python export_house_fixture.py [--out ../tests/data/testhaus.json]

Writes every exposed entity with state, attributes, area, floor and aliases
exactly as Home Assistant's registries report them, so ``tests/_testhaus.py``
can rebuild the same ``EntitySnapshot`` list that HomeIntent sees live.
Volatile attributes (timestamps, context ids) are dropped; the fixture is
regenerated only when ``custom_components/haus_sim/house.py`` changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import aiohttp

from haclient import WS, load_tokens

HERE = Path(__file__).resolve().parent
SKIP_DOMAINS = {
    "sun", "zone", "conversation", "tts", "event", "update", "stt",
    "assist_satellite", "wake_word", "ai_task",
}
VOLATILE = {"entity_picture", "access_token", "context", "last_triggered"}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE.parent / "tests" / "data" / "testhaus.json"))
    args = parser.parse_args()
    tokens = load_tokens()
    async with aiohttp.ClientSession() as session, WS(session, tokens["admin"]) as ws:
        states = await ws.call("get_states")
        entities = {item["entity_id"]: item for item in await ws.call("config/entity_registry/list")}
        devices = {item["id"]: item for item in await ws.call("config/device_registry/list")}
        areas = {item["area_id"]: item for item in await ws.call("config/area_registry/list")}
        floors = {item["floor_id"]: item for item in await ws.call("config/floor_registry/list")}
        out = []
        for state in sorted(states, key=lambda item: item["entity_id"]):
            entity_id = state["entity_id"]
            if entity_id.split(".", 1)[0] in SKIP_DOMAINS:
                continue
            registry = entities.get(entity_id, {})
            area_id = registry.get("area_id")
            if area_id is None and registry.get("device_id") in devices:
                area_id = devices[registry["device_id"]].get("area_id")
            area = areas.get(area_id) if area_id else None
            floor = floors.get(area.get("floor_id")) if area and area.get("floor_id") else None
            out.append({
                "entity_id": entity_id,
                "state": state["state"],
                "attributes": {
                    key: value for key, value in state["attributes"].items()
                    if key not in VOLATILE
                },
                "aliases": sorted(registry.get("aliases") or ()),
                "area_id": area_id,
                "area_name": area["name"] if area else None,
                "area_aliases": sorted(area.get("aliases") or ()) if area else [],
                "floor_id": floor["floor_id"] if floor else None,
                "floor_name": floor["name"] if floor else None,
                "floor_level": floor.get("level") if floor else None,
                "floor_aliases": sorted(floor.get("aliases") or ()) if floor else [],
            })
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(out)} entities -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
