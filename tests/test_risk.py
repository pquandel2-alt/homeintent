from homeintent.entities import EntitySnapshot
from homeintent.risk import RiskLevel, classify_service_plan, requires_confirmation
from homeintent.service_call import ServiceCallPlan


def test_unlock_and_garage_are_critical():
    lock = EntitySnapshot("lock.front", "Haustür", "lock", "locked")
    garage = EntitySnapshot(
        "cover.garage", "Garage", "cover", "closed", device_class="garage"
    )
    assert classify_service_plan(
        ServiceCallPlan("lock", "unlock", lock.entity_id), [lock]
    ) is RiskLevel.CRITICAL
    assert requires_confirmation(
        ServiceCallPlan("cover", "open_cover", garage.entity_id), [garage]
    )


def test_large_physical_group_is_escalated():
    covers = [
        EntitySnapshot(
            f"cover.c{index}",
            f"Rollladen {index}",
            "cover",
            "closed",
            device_class="shutter",
        )
        for index in range(6)
    ]
    plan = ServiceCallPlan("cover", "open_cover", [item.entity_id for item in covers])
    assert classify_service_plan(plan, covers) is RiskLevel.HIGH


def test_light_remains_low_risk():
    light = EntitySnapshot("light.desk", "Schreibtisch", "light", "off")
    plan = ServiceCallPlan("homeassistant", "turn_on", light.entity_id)
    assert classify_service_plan(plan, [light]) is RiskLevel.LOW
    assert not requires_confirmation(plan, [light])


def test_gate_open_and_close_are_critical_like_garage_doors():
    gate = EntitySnapshot("cover.hoftor", "Hoftor", "cover", "closed", device_class="gate")
    for service in ("open_cover", "close_cover"):
        plan = ServiceCallPlan("cover", service, gate.entity_id)
        assert classify_service_plan(plan, [gate]) is RiskLevel.CRITICAL, service
        assert requires_confirmation(plan, [gate])


def test_ordinary_shutter_stays_medium_risk():
    shutter = EntitySnapshot(
        "cover.rollladen", "Rollladen", "cover", "closed", device_class="shutter"
    )
    plan = ServiceCallPlan("cover", "open_cover", shutter.entity_id)
    assert classify_service_plan(plan, [shutter]) is RiskLevel.MEDIUM
    assert not requires_confirmation(plan, [shutter])
