"""Phase 4 (7.3.4): general shadow infrastructure and drift classes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.engine import MatchResult  # noqa: E402
from homeintent.nlu.understanding import (  # noqa: E402
    BehaviorSignature,
    DriftClass,
    ShadowReport,
    classify_drift,
)
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.shadow_runtime import signature_for  # noqa: E402

ENTITIES = house_entities()


def sig(**kwargs) -> BehaviorSignature:
    base = dict(
        writes=True, operations=frozenset({"light.turn_on"}), domains=frozenset({"light"}),
        targets=frozenset({"light.a"}), risk=1, plan=(("light", "turn_on", "light.a", ()),),
    )
    base.update(kwargs)
    return BehaviorSignature(**base)


def test_equivalent_and_refinement():
    assert classify_drift(sig(), sig())[0] is DriftClass.EQUIVALENT
    assert classify_drift(sig(detail="a"), sig(detail="b"))[0] is DriftClass.REFINEMENT


def test_behavior_change_within_the_same_kind():
    fewer = sig(targets=frozenset(), writes=False, plan=())
    assert classify_drift(sig(), fewer)[0] is DriftClass.BEHAVIOR_CHANGE
    other_service = sig(operations=frozenset({"light.turn_off"}), plan=(("light", "turn_off", "light.a", ()),))
    assert classify_drift(sig(), other_service)[0] is DriftClass.BEHAVIOR_CHANGE


def test_every_safety_drift_reason():
    active = sig(targets=frozenset({"light.a"}), risk=3, confirmation=True)
    cases = {
        "write_instead_of_non_write": (sig(writes=False, targets=frozenset(), plan=()), sig()),
        "more_or_other_targets": (active, sig(targets=frozenset({"light.a", "light.b"}), risk=3, confirmation=True)),
        "domain_change": (active, sig(domains=frozenset({"vacuum"}), risk=3, confirmation=True)),
        "lower_risk": (active, sig(risk=1, confirmation=True)),
        "confirmation_dropped": (active, sig(risk=3, confirmation=False)),
    }
    for reason, (left, right) in cases.items():
        drift, reasons = classify_drift(left, right)
        assert drift is DriftClass.SAFETY_DRIFT and reason in reasons, reason


def test_faulty_candidate_light_to_vacuum_is_safety_drift():
    """A deliberately wrong candidate turns the kitchen light into the vacuum."""
    report = ShadowReport("faulty")
    light = MatchResult(plan=ServiceCallPlan("homeassistant", "turn_on", "light.kuechenlicht"), response_text="")
    vacuum = MatchResult(plan=ServiceCallPlan("vacuum", "start", "vacuum.saugroboter"), response_text="")
    record = report.add(
        "Schalte das Küchenlicht ein.", signature_for(light, ENTITIES), signature_for(vacuum, ENTITIES)
    )
    assert record.drift is DriftClass.SAFETY_DRIFT
    assert "domain_change" in record.reasons and "more_or_other_targets" in record.reasons
    assert report.switch_allowed is False


def test_live_shadow_candidate_never_writes(monkeypatch):
    house = HouseConversation(monkeypatch, options={"shadow_mode": "log"})
    called = []

    def faulty(text, entities):
        called.append(text)
        return MatchResult(plan=ServiceCallPlan("vacuum", "start", "vacuum.saugroboter"), response_text="")

    shadow = house.entity._runtime_data.shadow
    shadow.register("faulty", faulty)
    turn = house.say("Schalte das Küchenlicht ein.")
    assert called == ["Schalte das Küchenlicht ein."]
    assert turn.targets == {"light.kuechenlicht"}
    assert all(domain != "vacuum" for domain, _service, _data in turn.calls)
    report = shadow.reports["faulty"]
    assert report.counts()["SAFETY_DRIFT"] == 1 and not report.switch_allowed
    # The log keeps a sentence hash, never the sentence itself.
    assert "Küchenlicht" not in str(report.to_dict())


def test_shadow_off_never_runs_candidates(monkeypatch):
    house = HouseConversation(monkeypatch)
    called = []
    house.entity._runtime_data.shadow.register("any", lambda text, entities: called.append(text))
    house.say("Schalte das Küchenlicht ein.")
    assert called == []


def test_offline_identity_candidate_is_equivalent_everywhere():
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import shadow_compare

    report = shadow_compare.run("identity")
    assert report.records and report.counts()["EQUIVALENT"] == len(report.records)
