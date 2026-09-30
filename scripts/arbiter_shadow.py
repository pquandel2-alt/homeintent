#!/usr/bin/env python3
"""Shadow run of the arbiter against the conversation cascade (7.5.0).

For every sentence of the published corpora and of the collision corpus
(``tests/eval/collisions_v75.json``) the real conversation agent answers on
the stub test house; what the cascade hands to the execution policy is
recorded (nothing is executed for real). Next to it the arbiter decides
between the interpreter candidates. Both are compared as behaviour
signatures (EQUIVALENT / REFINEMENT / BEHAVIOR_CHANGE / SAFETY_DRIFT).

    python scripts/arbiter_shadow.py --check
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
sys.path.insert(0, str(ROOT / "scripts"))

import _ha_stub  # noqa: E402

_ha_stub.install()

import pytest  # noqa: E402
from _testhaus import HouseConversation  # noqa: E402
from homeintent.arbitration import arbitrate  # noqa: E402
from homeintent.arbitration_candidates import collect_candidates  # noqa: E402
from homeintent.engine import CommandPlan, MatchResult  # noqa: E402
from homeintent.nlu.understanding import DriftClass, classify_drift  # noqa: E402
from homeintent.shadow_runtime import signature_for  # noqa: E402

COLLISIONS = ROOT / "tests" / "eval" / "collisions_v75.json"


def collision_corpus() -> list[tuple[str, str, str]]:
    return [
        (item["pair"], item["text"], item["expected"])
        for item in json.loads(COLLISIONS.read_text(encoding="utf-8"))
    ]


class Cascade:
    """The conversation agent with the policy hand-over recorded."""

    def __init__(self) -> None:
        self.house = HouseConversation(pytest.MonkeyPatch())
        self.handed: list = []
        # Since 7.7 B4 the controllers hand plans to the policy; record the
        # hand-over wherever the conversation layer calls it.
        import importlib

        for name in ("controllers.devices", "controllers.goals", "controllers.routines"):
            module = importlib.import_module(f"homeintent.{name}")
            original = module.evaluate_service_plan

            def recording(plan, *args, _original=original, **kwargs):
                self.handed.append(plan)
                return _original(plan, *args, **kwargs)

            module.evaluate_service_plan = recording
        self.counter = 0

    def run(self, text: str):
        self.handed = []
        self.counter += 1
        self.house.conversation_id = f"arbiter-{self.counter}"
        turn = self.house.say(text)
        # Plans handed to the policy but nothing switched: the cascade asked
        # for confirmation (preview, need proposal, critical action).
        asked = "asked" if self.handed and not turn.calls else None
        commands = tuple(MatchResult(plan=plan, response_text="") for plan in self.handed)
        payload = CommandPlan(commands, confirmation_text=asked) if commands else None
        return turn, payload


def decide(engine, text, entities, hass=None, options=None):
    """The arbiter's choice after the same execution policy the cascade uses."""
    from homeintent.effect_graph import build_plan_effects
    from homeintent.execution_policy import PolicyOutcome, evaluate_service_plan
    from homeintent.plan_origin import PlanOrigin

    from homeintent.arbitration import Decision, DecisionKind
    from homeintent.nlu.surface import prepare_surface

    # The arbiter reads the same frontend surface as the cascade (7.7.1).
    surface = prepare_surface(text, list(entities))
    if surface.stops:
        return Decision(DecisionKind.NOTHING, (), "correction_stops"), None
    text = surface.text
    candidates, question = collect_candidates(engine, text, entities)
    decision = arbitrate(candidates, explicit_question=question)
    payload = decision.chosen[0].payload if decision.writes else None
    plans = [
        command.plan for command in getattr(payload, "commands", ()) if command.plan is not None
    ] or ([payload.plan] if getattr(payload, "plan", None) is not None else [])
    if not plans:
        return decision, None
    origin = getattr(payload, "origin", None) or PlanOrigin.EXPLICIT_COMMAND
    outcomes = [
        evaluate_service_plan(
            plan, list(entities), options or {}, is_admin=True, user_id="admin",
            effects=build_plan_effects(hass, plan) if hass is not None else None,
            origin=origin, binding_confirmed=getattr(payload, "binding_confirmed", False),
        ).outcome
        for plan in plans
    ]
    if any(outcome is PolicyOutcome.DENY for outcome in outcomes):
        return decision, None
    asked = "asked" if (
        getattr(payload, "confirmation_text", None) or any(outcome is PolicyOutcome.CONFIRM for outcome in outcomes)
    ) else None
    return decision, CommandPlan(tuple(MatchResult(plan=plan, response_text="") for plan in plans), confirmation_text=asked)


def run(extra: list[tuple[str, str]] | None = None) -> dict:
    import shadow_compare

    cascade = Cascade()
    entities = cascade.house.entities
    engine = cascade.house.entity._engine
    items = [(source, text) for source, text in shadow_compare.corpus()] + [
        (f"collision:{pair}", text) for pair, text, _expected in collision_corpus()
    ] + list(extra or [])
    counts: collections.Counter = collections.Counter()
    by_source: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for source, text in items:
        try:
            _turn, active = cascade.run(text)
        except Exception as err:  # noqa: BLE001 - stub cannot answer some reads (calendar)
            counts["NOT_MEASURABLE"] += 1
            examples["NOT_MEASURABLE"].append(f"[{source}] {text!r} {type(err).__name__}")
            continue
        decision, chosen = decide(engine, text, entities, cascade.house.entity.hass)
        drift, reasons = classify_drift(signature_for(active, entities), signature_for(chosen, entities))
        counts[drift.name] += 1
        by_source[source.split(":")[0]][drift.name] += 1
        if drift is not DriftClass.EQUIVALENT and len(examples[drift.name]) < 40:
            examples[drift.name].append(f"[{source}] {text!r} arbiter={decision.kind.name}/{decision.reason} {reasons}")
    return {
        "total": sum(counts.values()),
        "counts": dict(counts),
        "by_source": {key: dict(value) for key, value in by_source.items()},
        "examples": dict(examples),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = run()
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"arbiter: {result['total']} Sätze, {result['counts']}")
    for line in result["examples"].get("SAFETY_DRIFT", [])[:20]:
        print("  SAFETY_DRIFT", line)
    return 1 if args.check and result["counts"].get("SAFETY_DRIFT") else 0


if __name__ == "__main__":
    raise SystemExit(main())
