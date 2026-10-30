"""Stable structural and relative performance guards for 7.13.2."""

from __future__ import annotations

import gc
from dataclasses import replace
from pathlib import Path
import sys
import weakref

import _ha_stub

_ha_stub.install()

from _testhaus import house_entities  # noqa: E402
from homeintent.nlu.self_correction import (  # noqa: E402
    _cached_names,
    registry_name_table,
)
from homeintent.nlu.target_resolution import name_index  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_v6_baseline import (  # noqa: E402
    MAX_COORDINATION_REGRESSION_RATIO,
    Stats,
    coordination_regression_ratios,
)


def _stats(label: str, p95: float) -> Stats:
    return Stats(label, 5000, 20, p95, p95, p95, p95, p95)


def test_relative_gate_detects_7131_scale_coordination_regression():
    regressed = [
        _stats("light_on", 3.09),
        _stats("light_off", 3.0),
        _stats("multi_target", 46.61),
        _stats("multi_clause_command", 49.19),
    ]
    ratios = coordination_regression_ratios(regressed)
    assert ratios["multi_target"] > 8
    assert ratios["multi_clause_command"] > 8


def test_relative_gate_accepts_target_coordination_cost():
    improved = [
        _stats("light_on", 3.1),
        _stats("light_off", 3.0),
        _stats("multi_target", 18.0),
        _stats("multi_clause_command", 19.0),
    ]
    assert (
        max(coordination_regression_ratios(improved).values())
        < MAX_COORDINATION_REGRESSION_RATIO
    )


def test_relative_gate_rejects_even_a_sevenfold_regression():
    regressed = [
        _stats("light_on", 3.0),
        _stats("light_off", 3.0),
        _stats("multi_target", 21.0),
        _stats("multi_clause_command", 21.0),
    ]
    assert all(
        ratio > MAX_COORDINATION_REGRESSION_RATIO
        for ratio in coordination_regression_ratios(regressed).values()
    )


def test_registry_name_cache_is_bounded_structural_and_retains_no_snapshots():
    _cached_names.cache_clear()
    entities = house_entities()
    watched = weakref.ref(entities[0])
    names = registry_name_table(entities)
    assert all(
        isinstance(entity_id, str)
        for entity_ids in names.by_words.values()
        for entity_id in entity_ids
    )

    # A new turn with the same registry structure reuses only the id table.
    registry_name_table(list(entities))
    assert _cached_names.cache_info().currsize == 1

    # A registry name change invalidates the structure immediately.
    changed = [replace(entities[0], friendly_name="Umbenannt"), *entities[1:]]
    registry_name_table(changed)
    assert _cached_names.cache_info().currsize == 2
    assert _cached_names.cache_info().maxsize == 8

    del names, changed, entities
    gc.collect()
    assert watched() is None


def test_target_name_index_rebinds_the_cached_structure_to_fresh_snapshots():
    _cached_names.cache_clear()
    original = house_entities()[0]
    watched = weakref.ref(original)
    phrase = original.friendly_name.casefold()
    first = name_index([original])
    assert first.phrases[phrase] == (original,)

    # State/place/device metadata may change without changing the registry
    # name signature. Resolution must still return this turn's object.
    current = replace(
        original,
        state="on",
        area_id="fresh_area",
        area_name="Fresh Area",
    )
    second = name_index([current])
    assert second.phrases[phrase] == (current,)
    assert second.phrases[phrase][0] is current
    assert _cached_names.cache_info().currsize == 1

    del first, second, original
    gc.collect()
    assert watched() is None
