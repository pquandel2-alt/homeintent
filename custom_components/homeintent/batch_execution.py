"""Typed per-target outcomes for safe independent batch actions.

A batch runs in two typed phases (7.13.1):

1. ``PendingBatchExecution`` - the complete preflight is done, every allowed
   target's service call was made, and the targets whose call Home Assistant
   accepted wait for their effect (``BatchTargetStatus.PENDING``).
2. ``PendingBatchExecution.finalize`` - after all pending effects were
   observed together in one bounded, event-driven window
   (``effect_wait.async_settle``), the immutable ``BatchExecutionResult`` is
   built.  Reply, audit and Undo are derived only from this final result, so
   a device that reports within ``effect_wait_seconds`` is a success in all
   three, and a device that did not report is in none of them.

A service call accepted by Home Assistant is never itself proof of success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from .service_call import ServiceCallPlan
from .cover_safety import COVER_MOVEMENT_SERVICES, INDEPENDENT_COVER_CLASSES


class BatchTargetStatus(StrEnum):
    # Effect confirmed: the requested state, set point or a movement in the
    # requested direction was observed.
    SUCCESS = "success"
    # Call accepted, effect not judged yet.  Only inside a pending batch;
    # ``finalize`` never leaves a target in this state.
    PENDING = "pending"
    # The service call itself failed (exception).
    FAILED = "failed"
    # Rejected immediately before the call (the target changed after the
    # preflight, e.g. no longer exposed or no reliable state).
    REJECTED = "rejected"
    # The device reports the opposite state or moves the wrong way.
    CONTRARY = "contrary"
    # Observed for the complete wait, no report.
    TIMEOUT = "timeout"
    # The effect could not be observed at all (no event bus or no wait) and
    # the current state does not show it.
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_CONFIRMED = "not_confirmed"


# Statuses decided before any service call of the target.
PREFLIGHT_STATUSES = frozenset({
    BatchTargetStatus.UNSUPPORTED,
    BatchTargetStatus.UNAVAILABLE,
    BatchTargetStatus.UNAUTHORIZED,
    BatchTargetStatus.NOT_CONFIRMED,
})


@dataclass(frozen=True)
class BatchTargetResult:
    entity_id: str
    name: str
    status: BatchTargetStatus
    error: str | None = None


@dataclass(frozen=True)
class BatchExecutionResult:
    plan: ServiceCallPlan
    targets: tuple[BatchTargetResult, ...]

    def __post_init__(self) -> None:
        if any(item.status is BatchTargetStatus.PENDING for item in self.targets):
            raise ValueError("a final batch result never contains a pending target")

    @property
    def succeeded_entity_ids(self) -> frozenset[str]:
        return frozenset(
            item.entity_id
            for item in self.targets
            if item.status is BatchTargetStatus.SUCCESS
        )

    @property
    def fully_successful(self) -> bool:
        return bool(self.targets) and all(
            item.status is BatchTargetStatus.SUCCESS for item in self.targets
        )

    @property
    def statuses(self) -> tuple[tuple[str, str], ...]:
        """``(entity_id, status)`` per target, in plan order (audit)."""
        return tuple((item.entity_id, item.status.value) for item in self.targets)


# What the observed effect of one accepted call turns into.
_VERDICT_STATUS: Mapping[str, BatchTargetStatus] = {
    "reached": BatchTargetStatus.SUCCESS,
    "moving": BatchTargetStatus.SUCCESS,
    "contrary": BatchTargetStatus.CONTRARY,
    "unavailable": BatchTargetStatus.UNAVAILABLE,
}


@dataclass(frozen=True)
class PendingBatchExecution:
    """A batch whose accepted calls still wait for their effect."""

    plan: ServiceCallPlan
    targets: tuple[BatchTargetResult, ...]

    @property
    def pending_entity_ids(self) -> tuple[str, ...]:
        return tuple(
            item.entity_id for item in self.targets
            if item.status is BatchTargetStatus.PENDING
        )

    def finalize(
        self, verdicts: Mapping[str, str], *, observed: bool
    ) -> BatchExecutionResult:
        """The immutable final result after the one bounded wait.

        ``verdicts`` maps each pending entity id to its final
        ``EffectVerdict`` value; ``observed`` says whether the wait could
        listen for reports at all.  A pending target without a success
        verdict is a timeout when it was observed, otherwise unverified.
        """
        final: list[BatchTargetResult] = []
        for item in self.targets:
            if item.status is not BatchTargetStatus.PENDING:
                final.append(item)
                continue
            verdict = verdicts.get(item.entity_id, "pending")
            status = _VERDICT_STATUS.get(verdict)
            if status is None:
                status = (
                    BatchTargetStatus.TIMEOUT if observed else BatchTargetStatus.UNVERIFIED
                )
            final.append(BatchTargetResult(item.entity_id, item.name, status, item.error))
        return BatchExecutionResult(self.plan, tuple(final))


class BatchEligibility(StrEnum):
    """Whether a multi-target plan may run as independent per-target calls.

    Deliberately separate from ``RiskLevel``: risk decides confirmation and
    authority, eligibility decides whether one target's failure is
    independent of the others.  A standard cover stays ``MEDIUM`` risk and is
    still eligible; a garage door is neither.
    """

    ELIGIBLE = "eligible"
    SINGLE_TARGET = "single_target"
    MIXED_DOMAINS = "mixed_domains"
    UNSUPPORTED_SERVICE = "unsupported_service"
    # A cover whose class is safety relevant (garage, gate, door, window,
    # damper, awning) or not known at all.
    DEPENDENT_TARGET = "dependent_target"


# Covers that only shade or darken one opening and move independently of
# each other.  Every other or missing device class keeps the established
# all-or-nothing path.
BATCH_SERVICES: Mapping[str, frozenset[str]] = {
    "light": frozenset({"turn_on", "turn_off"}),
    "cover": COVER_MOVEMENT_SERVICES,
}


@dataclass(frozen=True)
class BatchTarget:
    """What eligibility needs to know about one target (HA-free)."""

    entity_id: str
    domain: str
    device_class: str | None
    known: bool


def batch_eligibility(
    service: str, targets: tuple[BatchTarget, ...]
) -> BatchEligibility:
    """Classify a plan's target set for independent per-target execution."""
    if len(targets) < 2:
        return BatchEligibility.SINGLE_TARGET
    domains = {item.domain for item in targets}
    if len(domains) != 1:
        return BatchEligibility.MIXED_DOMAINS
    domain = next(iter(domains))
    if service not in BATCH_SERVICES.get(domain, frozenset()):
        return BatchEligibility.UNSUPPORTED_SERVICE
    if domain == "cover" and any(
        not item.known
        or (item.device_class or "").casefold() not in INDEPENDENT_COVER_CLASSES
        for item in targets
    ):
        return BatchEligibility.DEPENDENT_TARGET
    return BatchEligibility.ELIGIBLE


_COUNT = {
    1: "Ein",
    2: "Zwei",
    3: "Drei",
    4: "Vier",
    5: "Fünf",
    6: "Sechs",
    7: "Sieben",
    8: "Acht",
    9: "Neun",
    10: "Zehn",
}
_NOUNS = {"light": ("Licht", "Lichter"), "cover": ("Rollladen", "Rollläden")}
_DONE = {
    "turn_on": "eingeschaltet",
    "turn_off": "ausgeschaltet",
    "open_cover": "geöffnet",
    "close_cover": "geschlossen",
    "set_cover_position": "eingestellt",
}


def batch_execution_speech(result: BatchExecutionResult) -> str:
    """Aggregate only recorded facts; never count a failed target as done."""

    succeeded = [
        item for item in result.targets if item.status is BatchTargetStatus.SUCCESS
    ]
    target_domains = {
        item.entity_id.partition(".")[0] for item in result.targets
    }
    effective_domain = (
        next(iter(target_domains))
        if len(target_domains) == 1
        else result.plan.domain
    )
    singular, plural = _NOUNS.get(effective_domain, ("Gerät", "Geräte"))
    parts: list[str] = []
    if succeeded:
        count = len(succeeded)
        noun = singular if count == 1 else plural
        parts.append(
            f"{_COUNT.get(count, str(count))} {noun} "
            f"{'wurde' if count == 1 else 'wurden'} "
            f"{_DONE.get(result.plan.service, 'ausgeführt')}."
        )
    messages = {
        BatchTargetStatus.UNSUPPORTED: (
            "unterstützt diese Aktion nicht",
            "unterstützen diese Aktion nicht",
        ),
        BatchTargetStatus.UNAVAILABLE: ("ist nicht verfügbar", "sind nicht verfügbar"),
        BatchTargetStatus.UNAUTHORIZED: (
            "darf nicht gesteuert werden",
            "dürfen nicht gesteuert werden",
        ),
        BatchTargetStatus.NOT_CONFIRMED: (
            "wurde nicht bestätigt",
            "wurden nicht bestätigt",
        ),
        BatchTargetStatus.REJECTED: (
            "wurde vor dem Schalten erneut geprüft und nicht ausgeführt",
            "wurden vor dem Schalten erneut geprüft und nicht ausgeführt",
        ),
        BatchTargetStatus.FAILED: ("ist fehlgeschlagen", "sind fehlgeschlagen"),
        BatchTargetStatus.CONTRARY: (
            "meldet einen anderen Zustand",
            "melden einen anderen Zustand",
        ),
        BatchTargetStatus.TIMEOUT: (
            "hat sich nicht rechtzeitig zurückgemeldet",
            "haben sich nicht rechtzeitig zurückgemeldet",
        ),
        BatchTargetStatus.UNVERIFIED: (
            "konnte nicht verifiziert werden",
            "konnten nicht verifiziert werden",
        ),
    }
    for status, (singular_message, plural_message) in messages.items():
        names = [item.name for item in result.targets if item.status is status]
        if names:
            joined = ", ".join(names[:-1]) + (
                f" und {names[-1]}" if len(names) > 1 else names[-1]
            )
            message = singular_message if len(names) == 1 else plural_message
            parts.append(f"{joined} {message}.")
    return " ".join(parts) or "Kein Ziel wurde ausgeführt."


__all__ = (
    "BATCH_SERVICES",
    "INDEPENDENT_COVER_CLASSES",
    "BatchEligibility",
    "BatchTarget",
    "batch_eligibility",
    "BatchExecutionResult",
    "BatchTargetResult",
    "BatchTargetStatus",
    "PREFLIGHT_STATUSES",
    "PendingBatchExecution",
    "batch_execution_speech",
)
