"""Changes and rates: "um 3 Grad innerhalb einer Stunde" (7.9 W3).

Home Assistant cannot express "the value changed by at least D within W"
without new helpers (derivative/trend sensors), and HomeIntent never creates
helpers the user did not ask for.  Such a request therefore runs in
HomeIntent's own monitor runtime (V10 ``MonitorGoalStore``): on every state
change of the sensor, the window's values come from the recorder and are
compared with the current value.

The meaning, exactly: the current value differs from the window's maximum
(falling) or minimum (rising) by at least ``delta``, in the spoken
direction.  At most one message per window.

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Sequence


class ChangeDirection(StrEnum):
    FALL = "fall"
    RISE = "rise"
    EITHER = "either"


@dataclass(frozen=True)
class RateRule:
    """One watched sensor: at least ``delta`` ``unit`` within ``window_seconds``."""

    entity_id: str
    delta: float
    unit: str
    direction: ChangeDirection
    window_seconds: int


@dataclass(frozen=True)
class RateFinding:
    start_value: float
    current: float
    change: float  # signed: current - start_value


def evaluate_rate(
    rule: RateRule,
    samples: Sequence[tuple[datetime, float]],
    current: float,
    now: datetime,
) -> RateFinding | None:
    """The change the rule describes, if it happened within the window.

    ``samples`` are (time, value) pairs from the recorder, including the
    state valid at the window's start; values outside the window are
    ignored.  An empty window is no evidence - never a finding.
    """
    start = now - timedelta(seconds=rule.window_seconds)
    values = [value for moment, value in samples if start <= moment <= now]
    if not values:
        return None
    highest, lowest = max(values), min(values)
    if rule.direction in {ChangeDirection.FALL, ChangeDirection.EITHER} and highest - current >= rule.delta:
        return RateFinding(highest, current, current - highest)
    if rule.direction in {ChangeDirection.RISE, ChangeDirection.EITHER} and current - lowest >= rule.delta:
        return RateFinding(lowest, current, current - lowest)
    return None


def _number(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def spoken_window(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "einer Stunde" if hours == 1 else f"{hours} Stunden"
    minutes = max(1, seconds // 60)
    return "einer Minute" if minutes == 1 else f"{minutes} Minuten"


def describe_rule(rule: RateRule, subject: str) -> str:
    """"die Temperatur Keller innerhalb einer Stunde um mindestens 3 °C fällt"."""
    verb = {
        ChangeDirection.FALL: "fällt", ChangeDirection.RISE: "steigt",
        ChangeDirection.EITHER: "sich ändert",
    }[rule.direction]
    return (
        f"{subject} innerhalb von {spoken_window(rule.window_seconds)} um mindestens "
        f"{_number(rule.delta)} {rule.unit} {verb}"
    )


def finding_message(rule: RateRule, subject: str, finding: RateFinding) -> str:
    verb = "gefallen" if finding.change < 0 else "gestiegen"
    return (
        f"{subject[:1].upper()}{subject[1:]} ist innerhalb von {spoken_window(rule.window_seconds)} "
        f"um {_number(abs(finding.change))} {rule.unit} {verb} "
        f"(von {_number(finding.start_value)} auf {_number(finding.current)} {rule.unit})."
    )


RUNTIME_NOTE = "Das überwache ich selbst; es läuft, solange HomeIntent läuft."
RESTART_NOTE = (
    "Nach einem Neustart prüfe ich ab der nächsten Änderung weiter; die Werte des Zeitraums "
    "kommen aus dem Verlauf von Home Assistant."
)


@dataclass(frozen=True)
class MonitorProposal:
    """A change request HomeIntent runs itself, before the "Ja"."""

    rule: RateRule
    subject: str  # „Temperatur Keller“
    preview: str


def propose(rule: RateRule, subject: str) -> MonitorProposal:
    preview = (
        f"Wenn {describe_rule(rule, subject)}, sende ich dir eine Push-Benachrichtigung – "
        f"höchstens einmal in {spoken_window(rule.window_seconds)}. {RUNTIME_NOTE} {RESTART_NOTE} "
        "Soll ich das so einrichten?"
    )
    return MonitorProposal(rule, subject, preview)


__all__ = (
    "ChangeDirection", "MonitorProposal", "RESTART_NOTE", "RUNTIME_NOTE", "RateFinding", "RateRule",
    "propose",
    "describe_rule", "evaluate_rate", "finding_message", "spoken_window",
)
