"""Shared German strength modifiers and amounts for relative adjustments.

"etwas wärmer" (a degree word) and "zwei Grad wärmer" / "um 20 Prozent
heller" (quantity × unit, 7.6.1) are one meaning: the size of a relative
step. Every compiler that turns a comparative into a step reads it here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import german_number
from .primitives import SemanticDegree


@dataclass(frozen=True)
class RelativeAmount:
    """A spoken step: value and its unit (``"degree"``, ``"percent"``)."""

    value: float
    unit: str


@dataclass(frozen=True)
class DegreeAdjustment:
    text: str
    degree: SemanticDegree | None
    light_percent: int
    climate_degrees: float
    amount: RelativeAmount | None = None

    @property
    def percent_step(self) -> int:
        """Step in percent for brightness, volume and position."""
        return self.light_percent


_DEGREES = (
    (re.compile(r"\b(?:etwas|ein\s+bisschen|leicht)\b", re.I), SemanticDegree.SLIGHT, 5, 0.5),
    (re.compile(r"\b(?:deutlich|merklich)\b", re.I), SemanticDegree.MODERATE, 15, 1.0),
    (re.compile(r"\b(?:viel|stark)\b", re.I), SemanticDegree.LARGE, 25, 2.0),
)
_UNITS = {"grad": "degree", "°": "degree", "prozent": "percent", "%": "percent"}
_AMOUNT_RE = re.compile(
    r"(?<![\wäöüß])(?:um\s+)?(?P<number>\d+(?:[,.]\d+)?|[a-zäöüß]+)\s*(?P<unit>grad\b|°|prozent\b|%)",
    re.I,
)
_HALF_RE = re.compile(r"(?<![\wäöüß])(?:um\s+)?(?:ein\s+)?halbe[sn]?\s+grad\b", re.I)


def _number(raw: str) -> float | None:
    if raw[:1].isdigit():
        return float(raw.replace(",", "."))
    if raw.casefold() in {"einen", "einem"}:
        return 1.0
    value = german_number(raw)
    return float(value) if value is not None else None


def relative_amount(text: str) -> tuple[RelativeAmount, str] | None:
    """The one spoken amount of a relative step and the text without it.

    Only a number directly followed by its unit counts ("zwei Grad", "um
    20 Prozent", "1,5 Grad", "ein halbes Grad"). A value after "auf" is an
    absolute target, not a step. Two amounts are no amount (never guess).
    """
    found: list[tuple[re.Match[str], RelativeAmount]] = []
    for match in _AMOUNT_RE.finditer(text):
        value = _number(match.group("number"))
        if value is None:
            continue
        before = text[:match.start()].rstrip().casefold()
        if before.endswith("auf") or before.endswith("bei"):
            continue
        found.append((match, RelativeAmount(value, _UNITS[match.group("unit").casefold()])))
    half = _HALF_RE.search(text)
    if half is not None and not found:
        found.append((half, RelativeAmount(0.5, "degree")))
    if len(found) != 1:
        return None
    match, amount = found[0]
    rest = re.sub(r"\s+", " ", text[:match.start()] + " " + text[match.end():]).strip()
    return amount, rest


def extract_degree(text: str) -> DegreeAdjustment:
    """Remove the degree modifier or spoken amount and return its steps.

    A spoken amount wins over a modifier word; without either the default
    step applies (10 % light, 1 degree).
    """
    spoken = relative_amount(text)
    if spoken is not None:
        amount, rest = spoken
        light_percent = int(round(amount.value)) if amount.unit == "percent" else 10
        climate = amount.value if amount.unit == "degree" else 1.0
        return DegreeAdjustment(rest, None, light_percent, climate, amount)
    matches = [entry for entry in _DEGREES if entry[0].search(text)]
    if len(matches) != 1:
        return DegreeAdjustment(text, None, 10, 1.0)
    pattern, degree, light_percent, climate_degrees = matches[0]
    cleaned = re.sub(r"\s+", " ", pattern.sub(" ", text)).strip()
    return DegreeAdjustment(cleaned, degree, light_percent, climate_degrees)
