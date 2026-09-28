"""Live shadow mode (7.3.4): candidates are observed, never executed.

With ``shadow_mode: log`` every registered candidate pipeline is run on the
same turn *after* the active pipeline has produced its result. Only the
active result is ever executed; a candidate returns a payload that is turned
into a behaviour signature and logged (sentence hash, both signatures, drift
class). Nothing here has access to Home Assistant services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .entities import EntitySnapshot
from .nlu.understanding import BehaviorSignature, ShadowReport, behavior_signature
from .risk import classify_service_plan

_LOGGER = logging.getLogger(__name__)

CONF_SHADOW_MODE = "shadow_mode"
SHADOW_MODES = ("off", "log")
LIVE_LIMIT = 500

# (text, entities) -> payload (MatchResult/CommandPlan-like) or None
Candidate = Callable[[str, Sequence[EntitySnapshot]], object | None]


def signature_for(
    payload: object | None,
    entities: Sequence[EntitySnapshot],
    *,
    speech_act: str = "",
    detail: str = "",
) -> BehaviorSignature:
    """Signature with HomeIntent's own risk rules (same as the executor's)."""
    snapshots = tuple(entities)

    def risk_of(plan: object) -> int:
        return int(classify_service_plan(plan, snapshots))  # type: ignore[arg-type]

    return behavior_signature(
        payload,
        speech_act=speech_act,
        risk_of=risk_of,
        confirmation_of=lambda plan: risk_of(plan) >= 3,
        detail=detail,
    )


@dataclass
class ShadowRuntime:
    candidates: dict[str, Candidate] = field(default_factory=lambda: {})
    reports: dict[str, ShadowReport] = field(default_factory=lambda: {})

    def register(self, name: str, candidate: Candidate) -> None:
        self.candidates[name] = candidate
        self.reports.setdefault(name, ShadowReport(name, limit=LIVE_LIMIT))

    def observe(
        self,
        options: Mapping[str, object],
        text: str,
        entities: Sequence[EntitySnapshot],
        active_payload: object | None,
    ) -> None:
        """Log every candidate's behaviour next to the active one."""
        if str(options.get(CONF_SHADOW_MODE, "off")) != "log" or not self.candidates:
            return
        active = signature_for(active_payload, entities)
        for name, candidate in self.candidates.items():
            try:
                payload = candidate(text, entities)
            except Exception:  # noqa: BLE001 - a candidate must never break a turn
                _LOGGER.debug("Shadow candidate %s failed", name, exc_info=True)
                continue
            record = self.reports[name].add(
                text, active, signature_for(payload, entities), source="live"
            )
            if record.drift.name == "SAFETY_DRIFT":
                _LOGGER.info(
                    "HomeIntent shadow %s: SAFETY_DRIFT %s (%s)", name, record.text_hash,
                    ", ".join(record.reasons),
                )

    def summary(self) -> dict[str, Any]:
        return {name: report.to_dict(examples=5) for name, report in self.reports.items()}
