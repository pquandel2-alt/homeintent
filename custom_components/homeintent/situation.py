"""Normalization and deterministic assessment of Home Assistant events."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Iterable, Mapping

from .entities import EntitySnapshot, spoken_state


class EventType(StrEnum):
    STATE_CHANGED = "state_changed"
    OPENED = "opened"
    CLOSED = "closed"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"
    UNAVAILABLE = "unavailable"
    SAFETY_ALARM = "safety_alarm"


class EventQuality(StrEnum):
    GOOD = "good"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class SituationSeverity(StrEnum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    CRITICAL = "critical"


class RoutineFeedback(StrEnum):
    """Explicit user feedback for a local statistical pattern."""

    HELPFUL = "helpful"
    UNNECESSARY = "unnecessary"
    WRONG = "wrong"
    IGNORE = "ignore"
    LATER = "later"


SAFETY_CLASSES = frozenset({"smoke", "carbon_monoxide", "moisture", "gas"})
OPEN_CLASSES = frozenset({"door", "window", "garage_door", "opening"})
ACTIVE_STATES = frozenset({"on", "open", "opening", "heating", "cooling", "playing"})


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: str
    event_type: EventType
    entity_id: str
    previous_state: str | None
    state: str
    occurred_at: datetime
    area_id: str | None
    person_ids: tuple[str, ...]
    occupied: bool | None
    operating_mode: str
    source: str
    quality: EventQuality
    device_class: str | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class Situation:
    situation_id: str
    category: str
    severity: SituationSeverity
    summary: str
    facts: tuple[str, ...]
    source_event_id: str
    relevant: bool = True
    suggested_entity_id: str | None = None
    suggested_operation: str | None = None


def normalize_state_change(
    entity: EntitySnapshot,
    previous_state: str | None,
    *,
    occurred_at: datetime,
    source: str = "home_assistant",
    person_ids: Iterable[str] = (),
    occupied: bool | None = None,
    operating_mode: str = "normal",
) -> NormalizedEvent:
    """Create one stable semantic event without interpreting raw text."""
    if occurred_at.tzinfo is None:
        raise ValueError("Ereigniszeit benötigt eine Zeitzone")
    state = entity.state.casefold()
    quality = (
        EventQuality.UNAVAILABLE
        if state == "unavailable"
        else EventQuality.UNKNOWN
        if state == "unknown"
        else EventQuality.GOOD
    )
    event_type = EventType.STATE_CHANGED
    if quality is EventQuality.UNAVAILABLE:
        event_type = EventType.UNAVAILABLE
    elif entity.device_class in SAFETY_CLASSES and state in {"on", "detected", "alarm"}:
        event_type = EventType.SAFETY_ALARM
    elif entity.device_class in OPEN_CLASSES:
        event_type = EventType.OPENED if state in {"on", "open"} else EventType.CLOSED
    elif state in ACTIVE_STATES:
        event_type = EventType.ACTIVATED
    elif state in {"off", "closed", "idle", "paused"}:
        event_type = EventType.DEACTIVATED
    stamp = occurred_at.astimezone(timezone.utc)
    event_id = f"evt:{entity.entity_id}:{int(stamp.timestamp() * 1_000_000)}:{state}"
    duration = None
    if entity.last_changed is not None:
        changed = entity.last_changed
        if changed.tzinfo is not None:
            duration = max(0.0, (occurred_at - changed).total_seconds())
    return NormalizedEvent(
        event_id,
        event_type,
        entity.entity_id,
        previous_state,
        entity.state,
        stamp,
        entity.area_id,
        tuple(sorted(set(person_ids))),
        occupied,
        operating_mode,
        source,
        quality,
        entity.device_class,
        duration,
    )


class SituationEvaluator:
    """Closed rules over normalized events and fresh entity snapshots."""

    def evaluate(
        self,
        event: NormalizedEvent,
        entities: Iterable[EntitySnapshot],
        *,
        night_start: time = time(22, 0),
        night_end: time = time(6, 0),
        long_state_seconds: Mapping[str, float] | None = None,
    ) -> tuple[Situation, ...]:
        if event.quality in {EventQuality.UNKNOWN, EventQuality.STALE}:
            return ()
        by_id = {entity.entity_id: entity for entity in entities}
        situations: list[Situation] = []
        if event.event_type is EventType.SAFETY_ALARM:
            situations.append(
                self._situation(
                    event,
                    "safety_alarm",
                    SituationSeverity.CRITICAL,
                    "Sicherheitsalarm erkannt.",
                    (f"Sensorqualität: {event.quality.value}", f"Zustand: {spoken_state(event.state)}"),
                )
            )
            return tuple(situations)
        if event.event_type is EventType.UNAVAILABLE:
            situations.append(
                self._situation(
                    event,
                    "device_unavailable",
                    SituationSeverity.WARNING,
                    "Ein Gerät ist nicht verfügbar.",
                    ("Home Assistant meldet unavailable",),
                )
            )
        local_time = event.occurred_at.timetz().replace(tzinfo=None)
        at_night = local_time >= night_start or local_time < night_end
        if (
            event.event_type is EventType.OPENED
            and at_night
            and event.occupied is False
        ):
            situations.append(
                self._situation(
                    event,
                    "opening_while_away",
                    SituationSeverity.WARNING,
                    "Eine Öffnung wurde nachts bei Abwesenheit erkannt.",
                    ("Zeitfenster: Nacht", "Anwesenheit: niemand erkannt", f"Qualität: {event.quality.value}"),
                )
            )
        if event.area_id and event.event_type is EventType.OPENED:
            heating = tuple(
                entity
                for entity in by_id.values()
                if entity.area_id == event.area_id
                and entity.domain == "climate"
                and entity.state in {"heat", "heating", "auto"}
            )
            if heating:
                situations.append(
                    self._situation(
                        event,
                        "window_heating",
                        SituationSeverity.NOTICE,
                        "Ein Fenster ist offen, während in diesem Raum geheizt wird.",
                        (f"{len(heating)} aktive Heizungsentität(en)", "gleicher Bereich"),
                    )
                )
        if (
            event.occupied is False
            and event.event_type is EventType.ACTIVATED
            and event.entity_id.startswith("light.")
        ):
            situations.append(
                self._situation(
                    event,
                    "light_unoccupied",
                    SituationSeverity.INFO,
                    "Licht ist in einem unbesetzten Bereich aktiv.",
                    ("Anwesenheit: niemand erkannt",),
                    suggested_entity_id=event.entity_id,
                    suggested_operation="turn_off",
                )
            )
        limit = (long_state_seconds or {}).get(event.entity_id)
        if limit is not None and event.duration_seconds is not None and event.duration_seconds > limit:
            situations.append(
                self._situation(
                    event,
                    "long_running_state",
                    SituationSeverity.NOTICE,
                    "Ein Gerätezustand dauert ungewöhnlich lange.",
                    (f"beobachtet: {event.duration_seconds:.0f} s", f"Grenze: {limit:.0f} s"),
                )
            )
        return tuple(situations)

    @staticmethod
    def _situation(
        event: NormalizedEvent,
        category: str,
        severity: SituationSeverity,
        summary: str,
        facts: tuple[str, ...],
        *,
        suggested_entity_id: str | None = None,
        suggested_operation: str | None = None,
    ) -> Situation:
        return Situation(
            f"sit:{event.event_id}:{category}",
            category,
            severity,
            summary,
            facts,
            event.event_id,
            suggested_entity_id=suggested_entity_id,
            suggested_operation=suggested_operation,
        )


@dataclass(frozen=True)
class PatternAssessment:
    unusual: bool
    observed: str
    normal: str
    observation_count: int
    relevance: str
    confidence: float
    uncertainty: str


@dataclass(frozen=True)
class RoutineDriftAssessment:
    drifted: bool
    score: float
    recent_observations: int
    baseline_observations: int
    explanation: str


@dataclass(frozen=True)
class RoutineCorrelationAssessment:
    paired_observations: int
    source_observations: int
    support: float
    confidence: float
    explanation: str


@dataclass(frozen=True)
class RoutineObservation:
    """One bounded statistical sample without transcript or authority data."""

    occurred_at: datetime
    weekday: int
    hour: int
    event_type: str
    duration_seconds: float | None
    occupied: bool | None
    operating_mode: str


@dataclass
class RoutineStatistics:
    """Explainable classical statistics; no result grants autonomy."""

    minimum_observations: int = 10
    anomaly_threshold: float = 0.05
    retention: timedelta = timedelta(days=180)
    maximum_observations: int = 4096
    moving_window: int = 20
    _hour_weekday: Counter[tuple[int, int]] = field(
        default_factory=lambda: Counter[tuple[int, int]]()
    )
    _durations: dict[tuple[int, int], list[float]] = field(default_factory=lambda: defaultdict(list))
    _sequences: Counter[tuple[str, str]] = field(
        default_factory=lambda: Counter[tuple[str, str]]()
    )
    _last_event: str | None = None
    _last_alert_at: datetime | None = None
    _ignored: bool = False
    _suppressed_until: datetime | None = None
    _feedback: Counter[RoutineFeedback] = field(
        default_factory=lambda: Counter[RoutineFeedback]()
    )
    _observations: deque[RoutineObservation] = field(
        default_factory=lambda: deque[RoutineObservation]()
    )
    _anomaly_active: bool = False

    def observe(self, event: NormalizedEvent) -> None:
        key = (event.occurred_at.weekday(), event.occurred_at.hour)
        self._hour_weekday[key] += 1
        if event.duration_seconds is not None and math.isfinite(event.duration_seconds):
            self._durations[key].append(max(0.0, event.duration_seconds))
        if self._last_event is not None:
            self._sequences[(self._last_event, event.event_type.value)] += 1
        self._last_event = event.event_type.value
        self._observations.append(
            RoutineObservation(
                event.occurred_at,
                key[0],
                key[1],
                event.event_type.value,
                (
                    max(0.0, event.duration_seconds)
                    if event.duration_seconds is not None
                    and math.isfinite(event.duration_seconds)
                    else None
                ),
                event.occupied,
                event.operating_mode,
            )
        )
        self._prune(event.occurred_at)

    @property
    def observation_count(self) -> int:
        return len(self._observations) or sum(self._hour_weekday.values())

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.retention
        limit = max(1, self.maximum_observations)
        while self._observations and (
            self._observations[0].occurred_at < cutoff
            or len(self._observations) > limit
        ):
            self._observations.popleft()

    @staticmethod
    def _quantile(values: list[float], fraction: float) -> float:
        ordered = sorted(values)
        if not ordered:
            raise ValueError("Quantil benötigt Beobachtungen")
        index = round(fraction * (len(ordered) - 1))
        return ordered[max(0, min(len(ordered) - 1, index))]

    def robust_duration_summary(
        self, observations: Iterable[RoutineObservation]
    ) -> str:
        """Median/IQR and bounded moving mean for explainable duration norms."""
        durations = [
            item.duration_seconds
            for item in observations
            if item.duration_seconds is not None
        ]
        if not durations:
            return "keine belastbare Zustandsdauer"
        median = statistics.median(durations)
        q1 = self._quantile(durations, 0.25)
        q3 = self._quantile(durations, 0.75)
        recent = durations[-max(1, self.moving_window):]
        moving = statistics.fmean(recent)
        return (
            f"Median {median:.0f} s; robuste Spanne {q1:.0f}-{q3:.0f} s; "
            f"gleitender Mittelwert {moving:.0f} s"
        )

    @staticmethod
    def _seasonally_near(month: int, reference: int) -> bool:
        distance = abs(month - reference)
        return min(distance, 12 - distance) <= 1

    def _context_window(self, event: NormalizedEvent) -> tuple[RoutineObservation, ...]:
        """Prefer same season/mode/presence, falling back only if too sparse."""
        retained = tuple(
            item
            for item in self._observations
            if event.occurred_at - item.occurred_at <= self.retention
        )
        seasonal = tuple(
            item
            for item in retained
            if self._seasonally_near(item.occurred_at.month, event.occurred_at.month)
            and item.operating_mode == event.operating_mode
            and (
                event.occupied is None
                or item.occupied is None
                or item.occupied is event.occupied
            )
        )
        return seasonal if len(seasonal) >= self.minimum_observations else retained

    def record_feedback(
        self,
        feedback: RoutineFeedback,
        *,
        now: datetime,
        remind_after: timedelta = timedelta(days=7),
    ) -> None:
        """Apply explicit feedback without ever changing execution authority."""
        if now.tzinfo is None:
            raise ValueError("Feedback-Zeit benötigt eine Zeitzone")
        self._feedback[feedback] += 1
        if feedback is RoutineFeedback.IGNORE:
            self._ignored = True
        elif feedback is RoutineFeedback.LATER:
            self._suppressed_until = now + remind_after

    def feedback_counts(self) -> Mapping[str, int]:
        """Return content-free decision-history counters for diagnostics."""
        return {feedback.value: count for feedback, count in self._feedback.items()}

    def drift_assessment(
        self, *, recent_window: int = 20, threshold: float = 0.35
    ) -> RoutineDriftAssessment:
        """Compare recent and older hour distributions without claiming causality."""
        observations = tuple(self._observations)
        window = max(3, recent_window)
        if len(observations) < window * 2:
            return RoutineDriftAssessment(
                False,
                0.0,
                min(len(observations), window),
                max(0, len(observations) - window),
                "Für eine Driftbewertung sind zwei ausreichend große Zeitfenster nötig.",
            )
        recent = observations[-window:]
        baseline = observations[:-window]
        recent_hours = Counter(item.hour for item in recent)
        baseline_hours = Counter(item.hour for item in baseline)
        hours = set(recent_hours) | set(baseline_hours)
        score = 0.5 * sum(
            abs(recent_hours[hour] / len(recent) - baseline_hours[hour] / len(baseline))
            for hour in hours
        )
        return RoutineDriftAssessment(
            score >= max(0.0, min(1.0, threshold)),
            score,
            len(recent),
            len(baseline),
            (
                f"Verteilungsabstand {score:.1%} zwischen den letzten "
                f"{len(recent)} und den vorherigen {len(baseline)} Beobachtungen; "
                "statistische Vermutung, keine Verhaltensbehauptung."
            ),
        )

    def correlation_with(
        self,
        other: "RoutineStatistics",
        *,
        maximum_gap: timedelta = timedelta(minutes=10),
    ) -> RoutineCorrelationAssessment:
        """Measure temporal co-occurrence; never infer cause or authority."""
        left = tuple(self._observations)
        right = tuple(other._observations)
        paired = sum(
            1
            for item in left
            if any(
                abs((candidate.occurred_at - item.occurred_at).total_seconds())
                <= maximum_gap.total_seconds()
                for candidate in right
            )
        )
        support = paired / len(left) if left else 0.0
        sample = min(len(left), len(right))
        confidence = min(0.99, sample / (sample + max(1, self.minimum_observations)))
        return RoutineCorrelationAssessment(
            paired,
            len(left),
            support,
            confidence,
            (
                f"{paired} von {len(left)} Ereignissen lagen höchstens "
                f"{int(maximum_gap.total_seconds() // 60)} Minuten auseinander; "
                "zeitliche Korrelation beweist keine Ursache."
            ),
        )

    @property
    def last_alert_at(self) -> datetime | None:
        """Timestamp used only to bind explicit feedback to a recent signal."""
        return self._last_alert_at

    def assess(
        self,
        event: NormalizedEvent,
        *,
        cooldown: timedelta = timedelta(hours=1),
        hysteresis: float = 0.02,
        commit: bool = True,
    ) -> PatternAssessment:
        self._prune(event.occurred_at)
        window = self._context_window(event)
        count = len(window) if window else self.observation_count
        key = (event.occurred_at.weekday(), event.occurred_at.hour)
        if self._ignored:
            return PatternAssessment(
                False,
                "lokales Ereignismuster",
                "vom Benutzer dauerhaft ignoriert",
                count,
                "keine proaktive Ausgabe",
                0.0,
                "Benutzerfeedback erweitert keine Aktionsberechtigung.",
            )
        if self._suppressed_until is not None and event.occurred_at < self._suppressed_until:
            return PatternAssessment(
                False,
                "lokales Ereignismuster",
                f"zurueckgestellt bis {self._suppressed_until.isoformat()}",
                count,
                "spaeter erneut bewerten",
                0.0,
                "Benutzerfeedback erweitert keine Aktionsberechtigung.",
            )
        bin_count = (
            sum(1 for item in window if (item.weekday, item.hour) == key)
            if window
            else self._hour_weekday[key]
        )
        if count < self.minimum_observations:
            return PatternAssessment(
                False,
                f"Ereignis am Wochentag {key[0]} um {key[1]} Uhr",
                "noch keine belastbare Routine",
                count,
                "Mindestbeobachtungszahl nicht erreicht",
                0.0,
                f"Es fehlen {self.minimum_observations - count} Beobachtungen.",
            )
        frequency = bin_count / count
        enter_threshold = max(0.0, self.anomaly_threshold)
        exit_threshold = min(1.0, enter_threshold + max(0.0, hysteresis))
        unusual = (
            frequency < exit_threshold
            if self._anomaly_active
            else frequency < enter_threshold
        )
        if self._last_alert_at is not None and event.occurred_at - self._last_alert_at < cooldown:
            unusual = False
        confidence = min(0.99, count / (count + self.minimum_observations))
        if unusual and commit:
            self._last_alert_at = event.occurred_at
        if commit:
            self._anomaly_active = unusual
        relevant_durations = tuple(
            item for item in window if (item.weekday, item.hour) == key
        )
        normal_duration = self.robust_duration_summary(relevant_durations)
        transition_count = self._sequences.get(
            (self._last_event or "", event.event_type.value), 0
        )
        return PatternAssessment(
            unusual,
            f"Zeitfenster-Anteil {frequency:.1%}",
            (
                f"Erwartungsgrenze {enter_threshold:.1%}, "
                f"Hysterese bis {exit_threshold:.1%}; {normal_duration}; "
                f"bekannte Folge {transition_count}-mal"
            ),
            count,
            "zeitliche Abweichung von der lokalen Routine" if unusual else "im erwarteten Bereich",
            confidence,
            "statistische Vermutung, kein sicherer Fakt",
        )

    def simulate(self, event: NormalizedEvent) -> PatternAssessment:
        """Preview an assessment without changing cooldown or hysteresis state."""
        return self.assess(event, commit=False)
