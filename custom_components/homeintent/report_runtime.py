"""Run-time side of summaries and reports (7.9.3 A4/A6/B6).

``async_summary_text`` answers "Was hab ich verpasst?" and builds the
arrival push; ``async_send_report`` is the body of the service
``homeintent.send_report``: it looks the report up in HomeIntent's own
store, builds the content now, resolves the recipients through the same
resolver as every monitor (the owner's bound devices, or the confirmed
household for a shared report) and sends exactly one push through
``AgentDelivery.async_send_notification``.  It never switches anything.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Sequence

from .entities import EntitySnapshot
from .event_summary import (
    MAX_SUMMARY_DAYS,
    SummaryEvent,
    SummaryQuery,
    history_entities,
    last_absence,
    period_label,
    render_summary,
    states_of,
    summarize,
)
from .execution_trace import TRACE_DATA_KEY
from .history_query import async_read_state_rows
from .house_report import ReportRecord, ReportStore, WeeklyFacts, render_weekly

_LOGGER = logging.getLogger(__name__)

NO_RECORDER = (
    "Dafür brauche ich den Verlauf von Home Assistant (Recorder); er ist gerade nicht verfügbar. "
    "Ich kann dir nur den jetzigen Zustand sagen."
)
REPORT_STORE_KEY = "homeintent_report_store"

RowReader = Callable[..., Awaitable[dict[str, list[tuple[datetime, str, dict[str, Any]]]] | None]]


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def async_automation_runs(
    hass: Any, start: datetime, end: datetime, read_rows: RowReader = async_read_state_rows
) -> list[tuple[datetime, str]]:
    """Monitors and automations that ran in the window (``last_triggered``)."""
    automations = [state.entity_id for state in states_of(hass, "automation")]
    rows = await read_rows(hass, automations, start, end, attributes=True) or {}
    runs: dict[tuple[str, str], tuple[datetime, str]] = {}
    for entity_id, items in rows.items():
        for _moment, _state, attributes in items:
            raw = attributes.get("last_triggered")
            moment = raw if isinstance(raw, datetime) else _parse_time(raw)
            if moment is None or not start <= moment <= end:
                continue
            name = str(attributes.get("friendly_name") or entity_id)
            runs[(entity_id, moment.isoformat())] = (moment, name)
    return sorted(runs.values())


def executions(hass: Any, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    """What HomeIntent executed unattended (its trace)."""
    data = getattr(hass, "data", None)
    runtime = data.get(TRACE_DATA_KEY) if isinstance(data, dict) else None
    store = getattr(runtime, "store", None)
    found: list[tuple[datetime, str]] = []
    for record in store.recent() if store is not None else ():
        if record.user_present or not record.executed:
            continue
        moment = record.time
        if not start <= moment <= end:
            continue
        names = [record.names.get(target, target) for target in record.targets]
        found.append((moment, "HomeIntent hat " + ", ".join(names) + " geschaltet"))
    return found


async def async_summary_text(
    hass: Any,
    entities: Sequence[EntitySnapshot],
    summary: SummaryQuery,
    *,
    person: str | None,
    is_admin: bool,
    now: datetime,
    read_rows: RowReader = async_read_state_rows,
) -> tuple[str, tuple[SummaryEvent, ...]]:
    """The spoken summary and the events left for "Was noch?"."""
    start, end, label = summary.start, summary.end, summary.label
    if summary.away:
        assert person is not None
        rows = await read_rows(hass, [person], now - timedelta(days=MAX_SUMMARY_DAYS), now)
        if rows is None:
            return NO_RECORDER, ()
        absence = last_absence([(moment, state) for moment, state, _ in rows.get(person, [])], now)
        if absence is None:
            return f"Laut Verlauf warst du in den letzten {MAX_SUMMARY_DAYS} Tagen nicht weg.", ()
        start, end = absence
        label = f"Während du weg warst ({period_label(start, end, now.date())})"
    assert start is not None and end is not None
    if start < now - timedelta(days=MAX_SUMMARY_DAYS):
        start = now - timedelta(days=MAX_SUMMARY_DAYS)
        label += f" (nur die letzten {MAX_SUMMARY_DAYS} Tage)"
    ids = history_entities(entities, hass)
    rows = await read_rows(hass, ids, start, end)
    if rows is None:
        return NO_RECORDER, ()
    runs = await async_automation_runs(hass, start, end, read_rows)
    names = {
        state.entity_id: str(state.attributes.get("friendly_name") or state.entity_id)
        for state in states_of(hass, "person")
    }
    events = summarize(
        {key: [(moment, state) for moment, state, _ in value] for key, value in rows.items()},
        entities, start, end,
        away=summary.away, speaker_person=person, speaker_is_admin=is_admin,
        automation_runs=runs, executions=executions(hass, start, end), person_names=names,
    )
    return render_summary(events, label, now.date())


def report_store(hass: Any) -> ReportStore:
    """The one report store of this Home Assistant (loaded in the executor)."""
    data = hass.data if isinstance(getattr(hass, "data", None), dict) else {}
    store = data.get(REPORT_STORE_KEY)
    if not isinstance(store, ReportStore):
        store = ReportStore(hass.config.path("homeintent_reports.json"))
        data[REPORT_STORE_KEY] = store
    return store


async def async_report_store(hass: Any) -> ReportStore:
    store = report_store(hass)
    if not store.loaded:
        await hass.async_add_executor_job(store.load)
    return store


async def async_weekly_facts(
    hass: Any, entities: Sequence[EntitySnapshot], now: datetime,
    read_rows: RowReader = async_read_state_rows, findings: Sequence[str] = (),
) -> WeeklyFacts:
    """The facts of the weekly report, read now."""
    from .device_health import LOW_BATTERY_PERCENT, battery_entities, device_entities
    from .energy_query import EnergyQuery, consumption_kwh, sources_for
    from .history_query import async_read_numeric_samples, recorder_loaded

    batteries: list[tuple[str, float]] = []
    for entity in battery_entities(entities):
        try:
            value = float(entity.state)
        except (TypeError, ValueError):
            continue
        if 0 <= value < LOW_BATTERY_PERCENT:
            batteries.append((entity.friendly_name, value))
    batteries.sort(key=lambda item: item[1])
    unavailable = tuple(sorted(entity.friendly_name for entity in device_entities(entities)
                               if entity.state == "unavailable"))
    start = now - timedelta(days=7)
    energy: float | None = None
    source: str | None = None
    recorder = recorder_loaded(hass)
    if recorder:
        query = EnergyQuery(start, now, "in den letzten 7 Tagen", (), False, False)
        for entity in sources_for(query, entities):
            samples = await async_read_numeric_samples(hass, entity.entity_id, start, now)
            value = consumption_kwh(entity, samples or [], now) if samples else None
            if value is not None:
                energy, source = value, entity.friendly_name
                break
    fired: dict[str, int] = {}
    if recorder:
        for _moment, name in await async_automation_runs(hass, start, now, read_rows):
            fired[name] = fired.get(name, 0) + 1
    return WeeklyFacts(
        tuple(batteries), unavailable, energy, source,
        tuple(sorted(fired.items(), key=lambda item: (-item[1], item[0]))[:8]), tuple(findings), recorder,
    )


async def async_monitor_findings(hass: Any, now: datetime) -> list[str]:
    """Notable situations the monitor runtime raised in the last week (the
    proactive agent's stored events), newest first, at most five."""
    from .agent_event_store import AgentEventStore

    try:
        events = await AgentEventStore(hass).async_load_all()
    except Exception:  # noqa: BLE001 - no store yet: no findings
        return []
    since = now - timedelta(days=7)
    found: list[tuple[datetime, str]] = []
    for event in events.values():
        created = _parse_time(event.created_at)
        if created is None or created < since:
            continue
        found.append((created, event.title or event.message))
    ordered = [text for _, text in sorted(found, reverse=True) if text]
    return list(dict.fromkeys(ordered))[:5]


async def async_compose(
    hass: Any, record: ReportRecord, entities: Sequence[EntitySnapshot], now: datetime, *, is_admin: bool,
    read_rows: RowReader = async_read_state_rows,
) -> tuple[str, str] | None:
    """(short push text, full text) of a report, built now."""
    if record.kind == "arrival":
        if record.person_entity_id is None:
            return None
        text, rest = await async_summary_text(
            hass, entities, SummaryQuery(True, None, None, "während du weg warst"),
            person=record.person_entity_id, is_admin=is_admin and not record.shared, now=now, read_rows=read_rows,
        )
        full = text if not rest else text.split(" Dazu kommen")[0] + " " + "; ".join(
            f"um {event.at:%H:%M} {event.text}" for event in rest[:10]
        ) + "."
        return text.replace("frag „Was noch?“", "frag mich „Was hab ich verpasst?“"), full
    facts = await async_weekly_facts(hass, entities, now, read_rows, await async_monitor_findings(hass, now))
    return render_weekly(facts)


async def async_send_report(
    hass: Any, entry: Any, report_id: str, entities: Sequence[EntitySnapshot], now: datetime,
    read_rows: RowReader = async_read_state_rows,
) -> bool:
    """The service body: build, resolve the recipients like a monitor, send
    once.  ``False`` (and a log line) when nothing could be sent."""
    from .agent_delivery import AgentDelivery
    from .nlu.action_model import NotificationRecipientKind
    from .notification_target import NotificationTargetResolver

    store = await async_report_store(hass)
    record = store.records.get(report_id)
    if record is None:
        _LOGGER.warning("Unbekannter HomeIntent-Bericht %s: nichts gesendet", report_id)
        return False
    runtime = getattr(entry, "runtime_data", None)
    resolver = NotificationTargetResolver.from_options(entry.options, getattr(runtime, "user_contexts", None))
    kind = NotificationRecipientKind.HOUSEHOLD if record.shared else NotificationRecipientKind.CURRENT_USER
    resolution = resolver.resolve(kind, record.owner_user_id)
    if not resolution.resolved or not resolution.targets:
        _LOGGER.warning("HomeIntent-Bericht %s: kein bestätigtes Push-Ziel, nichts gesendet", report_id)
        return False
    user = await hass.auth.async_get_user(record.owner_user_id) if record.owner_user_id else None
    composed = await async_compose(
        hass, record, entities, now, is_admin=bool(getattr(user, "is_admin", False)), read_rows=read_rows
    )
    if composed is None:
        return False
    short, full = composed
    result = await AgentDelivery(hass).async_send_notification(
        tuple(resolution.targets), title="HomeIntent", message=short
    )
    record.last_short, record.last_full, record.last_sent = short, full, now.isoformat()
    await hass.async_add_executor_job(store.save)
    return bool(result.delivered)
