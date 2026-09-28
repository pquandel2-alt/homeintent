#!/usr/bin/env python3
"""Shadow run of the one target resolution (7.4.0).

Wraps ``resolve_phrase`` - the one target resolution every caller uses since
7.4.0 - in every HomeIntent module: each call returns its own result, and the
historic name resolver (``resolve_entity_scored``) runs next to it on the same
arguments. Differences are classified per caller
(EQUIVALENT / REFINEMENT / OLD_BETTER / SAFETY_DRIFT). Nothing is executed.

    python scripts/resolver_shadow.py --check          # whole shadow corpus
    HOMEINTENT_RESOLVER_SHADOW=out.json pytest tests   # every test call too
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

RECORDS: collections.Counter = collections.Counter()
EXAMPLES: dict[tuple[str, str, str], list[str]] = collections.defaultdict(list)


def _caller() -> str:
    frame = sys._getframe(2)
    module = frame.f_globals.get("__name__", "?").replace("homeintent.", "")
    return f"{module}.{frame.f_code.co_name}"


def _ids(result) -> list[str]:
    return sorted(e.entity_id for e in ([result.entity] if result.entity else result.candidates))


def install() -> int:
    """Wrap every imported ``resolve_phrase``; returns the count."""
    import importlib
    import pkgutil

    import homeintent
    from homeintent import entities as entities_module
    from homeintent.nlu import target_resolution
    from homeintent.nlu.target_resolution import compare_resolutions

    for info in pkgutil.walk_packages(homeintent.__path__, "homeintent."):
        try:
            importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 - optional HA-only modules
            continue
    historic = entities_module.resolve_entity_scored
    active = target_resolution.resolve_phrase
    if getattr(active, "_shadowed", False):
        return 0

    def shadowed(name, entities, **kwargs):
        new = active(name, entities, **kwargs)
        try:
            old = historic(name, list(entities), **kwargs)
        except Exception as err:  # noqa: BLE001
            RECORDS[(_caller(), "SAFETY_DRIFT", f"error:{type(err).__name__}")] += 1
            return new
        drift, reason = compare_resolutions(old, new, name)
        key = (_caller(), drift, reason)
        RECORDS[key] += 1
        if drift != "EQUIVALENT" and len(EXAMPLES[key]) < 8:
            EXAMPLES[key].append(
                f"{name!r} kw={sorted(k for k, v in kwargs.items() if v is not None)} "
                f"alt={old.status.name}:{_ids(old)} neu={new.status.name}:{_ids(new)}"
            )
        return new

    shadowed._shadowed = True  # type: ignore[attr-defined]
    count = 0
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("homeintent") and getattr(
            module, "resolve_phrase", None
        ) is active:
            module.resolve_phrase = shadowed  # type: ignore[attr-defined]
            count += 1
    return count


def summary() -> dict:
    by_caller: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    totals: collections.Counter = collections.Counter()
    for (caller, drift, _reason), count in RECORDS.items():
        by_caller[caller][drift] += count
        totals[drift] += count
    return {
        "totals": dict(totals),
        "by_caller": {caller: dict(counts) for caller, counts in sorted(by_caller.items())},
        "examples": {" | ".join(key): examples for key, examples in sorted(EXAMPLES.items())},
    }


def report(result: dict) -> int:
    print(json.dumps(result["totals"]))
    for key, examples in result["examples"].items():
        if "SAFETY_DRIFT" in key or "OLD_BETTER" in key:
            print(key)
            for example in examples:
                print("   ", example)
    return result["totals"].get("SAFETY_DRIFT", 0) + result["totals"].get("OLD_BETTER", 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    import _ha_stub

    _ha_stub.install()
    install()
    import shadow_compare

    shadow_compare.run("identity")
    result = summary()
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = report(result)
    return 1 if args.check and bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
