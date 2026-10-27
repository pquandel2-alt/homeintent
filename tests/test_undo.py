from __future__ import annotations

from homeintent.entities import EntitySnapshot
from homeintent.service_call import ServiceCallPlan
from homeintent.undo import build_undo_plan, is_undo_request, restrict_undo_plan


def test_undo_request_is_deliberately_narrow():
    assert is_undo_request("Mach das rückgängig")
    assert is_undo_request("Setze es zurück.")
    assert not is_undo_request("Mach das Licht zurück an")


def test_light_snapshot_restores_power_and_brightness():
    light = EntitySnapshot(
        "light.office",
        "Bürolicht",
        "light",
        "on",
        attributes={"brightness": 123, "rgb_color": (1, 2, 3)},
    )
    undo = build_undo_plan(
        ServiceCallPlan("light", "turn_off", light.entity_id),
        [light],
        requested_by_user_id="user-1",
    )

    assert undo is not None
    assert undo.requested_by_user_id == "user-1"
    assert undo.plans == (
        ServiceCallPlan(
            "light", "turn_on", light.entity_id,
            {"brightness": 123, "rgb_color": (1, 2, 3)},
        ),
    )


def test_cover_snapshot_uses_exact_position():
    cover = EntitySnapshot(
        "cover.office",
        "Bürorollo",
        "cover",
        "open",
        attributes={"current_position": 37},
    )
    undo = build_undo_plan(
        ServiceCallPlan(
            "cover", "set_cover_position", cover.entity_id, {"position": 80}
        ),
        [cover],
    )

    assert undo is not None
    assert undo.plans[0].service == "set_cover_position"
    assert undo.plans[0].data == {"position": 37}


def test_irreversible_or_missing_target_refuses_whole_undo():
    script = EntitySnapshot("script.night", "Gute Nacht", "script", "off")
    assert build_undo_plan(
        ServiceCallPlan("script", "turn_on", script.entity_id), [script]
    ) is None
    assert build_undo_plan(
        ServiceCallPlan("light", "turn_on", "light.missing"), [script]
    ) is None


def test_partial_batch_undo_contains_only_confirmed_successes():
    first = EntitySnapshot("light.first", "Erstes Licht", "light", "on")
    second = EntitySnapshot("light.second", "Zweites Licht", "light", "on")
    undo = build_undo_plan(
        ServiceCallPlan("light", "turn_off", [first.entity_id, second.entity_id]),
        [first, second],
        requested_by_user_id="user-1",
    )

    restricted = restrict_undo_plan(undo, frozenset({second.entity_id}))

    assert restricted is not None
    assert restricted.requested_by_user_id == "user-1"
    assert restricted.plans == (
        ServiceCallPlan("light", "turn_on", second.entity_id),
    )
