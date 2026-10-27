"""Typed per-target outcomes for safe independent batch actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .service_call import ServiceCallPlan


class BatchTargetStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_CONFIRMED = "not_confirmed"
    UNVERIFIED = "unverified"


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
        BatchTargetStatus.UNVERIFIED: (
            "konnte nicht verifiziert werden",
            "konnten nicht verifiziert werden",
        ),
        BatchTargetStatus.FAILED: ("ist fehlgeschlagen", "sind fehlgeschlagen"),
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
    "BatchExecutionResult",
    "BatchTargetResult",
    "BatchTargetStatus",
    "batch_execution_speech",
)
