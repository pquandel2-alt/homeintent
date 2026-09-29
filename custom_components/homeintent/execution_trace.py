"""Read-only cause-and-effect chain of HomeIntent executions.

Each execution (one user turn that led to a write) is recorded once under its
``execution_id`` (= the Home Assistant ``Context.id`` passed to every service
call). The trace refers to Home Assistant data instead of copying it: the
answer to "Warum ist der Saugroboter angegangen?" walks the entity's current
state context through HA's own chain (``Context.id``/``parent_id``, the
``automation_triggered``/``script_started`` events) back to a HomeIntent
execution, an automation, or a person.

Evidence levels are deliberately only three:

* ``VERIFIED`` – proven by the HA context chain;
* ``POSSIBLE`` – only temporal proximity plus a relation in an effect graph;
  always phrased as a guess;
* ``UNKNOWN`` – no relation at all.

Nothing here can authorise an execution; tracing is never a permission.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

DEFAULT_TRACE_LIMIT = 500
DEFAULT_TRACE_DAYS = 14
_CONTEXT_INDEX_LIMIT = 2000
_POSSIBLE_WINDOW = timedelta(minutes=2)
_MAX_HOPS = 8


class EffectKind(str, Enum):
    DIRECT_EFFECT = "direct"
    SCRIPT_EFFECT = "script"
    SCENE_EFFECT = "scene"
    AUTOMATION_EFFECT = "automation"
    SECONDARY_EFFECT = "secondary"
    EXTERNAL_EFFECT = "external"
    UNKNOWN_CAUSE = "unknown"


class Evidence(str, Enum):
    VERIFIED = "verified"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


def actor_hash(user_id: str | None) -> str:
    """Same privacy-minimised actor id as ``audit_log.AuditTrail``."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:10] if user_id else "voice"


def utterance_for_trace(text: str, *, store_text: bool) -> str:
    """Shortened text, or only a hash when the privacy option asks for it."""
    if not store_text:
        return "#" + hashlib.sha256(text.encode()).hexdigest()[:10]
    text = " ".join(text.split())
    return text if len(text) <= 120 else text[:117] + "…"


@dataclass(frozen=True)
class TraceEffect:
    """One effect of a plan as recorded (from the executed EffectGraph)."""

    domain: str
    service: str
    entity_ids: tuple[str, ...]
    step_alias: str | None = None
    via: str | None = None  # script/scene/group the effect belongs to


@dataclass(frozen=True)
class TraceRecord:
    execution_id: str
    created_at: str
    actor: str
    user_present: bool
    utterance: str | None = None
    meaning: str | None = None
    origin: str | None = None
    binding: str | None = None
    plans: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    effects: tuple[TraceEffect, ...] = ()
    possible_followups: tuple[str, ...] = ()
    policy: str | None = None
    risk: str | None = None
    executed: bool = True
    names: Mapping[str, str] = field(default_factory=dict)
    parent_id: str | None = None

    @property
    def time(self) -> datetime:
        return datetime.fromisoformat(self.created_at)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["names"] = dict(self.names)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraceRecord":
        effects = tuple(
            TraceEffect(
                str(item.get("domain", "")), str(item.get("service", "")),
                tuple(item.get("entity_ids") or ()), item.get("step_alias"), item.get("via"),
            )
            for item in data.get("effects") or ()
            if isinstance(item, Mapping)
        )
        return cls(
            execution_id=str(data["execution_id"]),
            created_at=str(data["created_at"]),
            actor=str(data.get("actor", "voice")),
            user_present=bool(data.get("user_present", True)),
            utterance=data.get("utterance"),
            meaning=data.get("meaning"),
            origin=data.get("origin"),
            binding=data.get("binding"),
            plans=tuple(data.get("plans") or ()),
            targets=tuple(data.get("targets") or ()),
            effects=effects,
            possible_followups=tuple(data.get("possible_followups") or ()),
            policy=data.get("policy"),
            risk=data.get("risk"),
            executed=bool(data.get("executed", True)),
            names=dict(data.get("names") or {}),
            parent_id=data.get("parent_id"),
        )


def effects_from_plan_effects(effects: Any) -> tuple[TraceEffect, ...]:
    """Flatten a ``PlanEffects`` for the trace (names only by reference)."""
    if effects is None:
        return ()
    return tuple(
        TraceEffect(
            effect.domain, effect.service, tuple(effect.entity_ids), effect.step_alias,
            effect.path[-1] if effect.path else None,
        )
        for effect in effects.effects
    )


class ExecutionTraceStore:
    """Bounded ring buffer of executions (count and age)."""

    def __init__(self, limit: int = DEFAULT_TRACE_LIMIT, days: int = DEFAULT_TRACE_DAYS) -> None:
        self._records: deque[TraceRecord] = deque(maxlen=max(1, limit))
        self._days = max(1, days)

    def configure(self, limit: int, days: int) -> None:
        if limit != self._records.maxlen:
            self._records = deque(self._records, maxlen=max(1, limit))
        self._days = max(1, days)

    def add(self, record: TraceRecord) -> None:
        self._records = deque(
            (item for item in self._records if item.execution_id != record.execution_id),
            maxlen=self._records.maxlen,
        )
        self._records.append(record)

    def merge(self, execution_id: str, **changes: Any) -> None:
        for index, item in enumerate(self._records):
            if item.execution_id == execution_id:
                self._records[index] = TraceRecord(**{**item.__dict__, **changes})
                return

    def get(self, execution_id: str | None) -> TraceRecord | None:
        if not execution_id:
            return None
        return next((item for item in self._records if item.execution_id == execution_id), None)

    def recent(self, limit: int | None = None) -> tuple[TraceRecord, ...]:
        items = tuple(reversed(self._records))
        return items[:limit] if limit is not None else items

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=self._days)
        kept = [item for item in self._records if _safe_time(item) >= cutoff]
        if len(kept) != len(self._records):
            self._records = deque(kept, maxlen=self._records.maxlen)

    def clear(self) -> None:
        self._records.clear()

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._records]

    def load(self, items: Iterable[Mapping[str, Any]]) -> None:
        for item in items:
            try:
                self._records.append(TraceRecord.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue


def _safe_time(record: TraceRecord) -> datetime:
    try:
        return record.time
    except ValueError:
        return datetime.min.replace(tzinfo=None)


@dataclass(frozen=True)
class ContextEvent:
    """An automation or script run observed on the HA event bus."""

    context_id: str
    kind: str  # automation | script
    entity_id: str
    name: str
    parent_id: str | None
    user_id: str | None
    time: str
    source: str | None = None


class ContextIndex:
    """Bounded map ``context_id`` -> automation/script run (HA chain hops)."""

    def __init__(self, limit: int = _CONTEXT_INDEX_LIMIT) -> None:
        self._items: OrderedDict[str, ContextEvent] = OrderedDict()
        self._limit = limit

    def add(self, event: ContextEvent) -> None:
        self._items[event.context_id] = event
        self._items.move_to_end(event.context_id)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def get(self, context_id: str | None) -> ContextEvent | None:
        return self._items.get(context_id) if context_id else None

    def clear(self) -> None:
        self._items.clear()


@dataclass(frozen=True)
class CauseExplanation:
    kind: EffectKind
    evidence: Evidence
    text: str
    execution_id: str | None = None
    chain: tuple[str, ...] = ()


_LOCAL_TIMEZONE: Any = None


def set_local_timezone(timezone: Any) -> None:
    """Home Assistant's configured zone for spoken clock times."""
    global _LOCAL_TIMEZONE
    _LOCAL_TIMEZONE = timezone


def _clock(value: str | datetime | None) -> str:
    if value is None:
        return ""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if moment.tzinfo is not None and _LOCAL_TIMEZONE is not None:
        moment = moment.astimezone(_LOCAL_TIMEZONE)
    return f"{moment:%H:%M}"


def _source_phrase(source: str | None, name_of: Callable[[str], str | None]) -> str:
    """HA's English trigger description in German ("state of X")."""
    if not source:
        return ""
    lowered = source.strip()
    for prefix, template in (
        ("state of ", "eine Zustandsänderung von {name}"),
        ("numeric state of ", "einen Messwert von {name}"),
    ):
        if lowered.startswith(prefix):
            entity_id = lowered[len(prefix):].strip()
            return template.format(name=name_of(entity_id) or entity_id)
    if lowered.startswith("time"):
        return "den Zeitplan"
    if lowered.startswith("sun"):
        return "den Sonnenstand"
    return lowered


def _names(ids: Iterable[str], names: Mapping[str, str]) -> str:
    labels = [names.get(item, item) for item in ids]
    if len(labels) > 1:
        return ", ".join(labels[:-1]) + " und " + labels[-1]
    return labels[0] if labels else ""


_KIND_WORD = {"script": "das Skript", "scene": "die Szene", "automation": "die Automation"}
_SERVICE_VERB = {
    "turn_on": "eingeschaltet", "turn_off": "ausgeschaltet", "toggle": "umgeschaltet",
    "start": "gestartet", "stop": "gestoppt", "press": "ausgelöst",
    "open_cover": "geöffnet", "close_cover": "geschlossen", "open_valve": "geöffnet",
    "close_valve": "geschlossen", "lock": "verriegelt", "unlock": "entriegelt",
    "start_mowing": "gestartet", "return_to_base": "zurückgeschickt", "dock": "zurückgeschickt",
}


def _verb(service: str) -> str:
    return _SERVICE_VERB.get(service, "geschaltet")


def _said(record: TraceRecord) -> str:
    return (
        f"Du hast um {_clock(record.created_at)} „{record.utterance}“ gesagt."
        if record.user_present and record.utterance and not record.utterance.startswith("#")
        else (
            f"Um {_clock(record.created_at)} hast du HomeIntent einen Befehl gegeben."
            if record.user_present else
            f"Um {_clock(record.created_at)} hat HomeIntent ohne Rückfrage gehandelt"
            f" ({'Daueranweisung' if record.origin == 'standing_permission' else 'proaktiv'})."
        )
    )


def _homeintent_sentence(
    record: TraceRecord, entity_id: str, entity_name: str
) -> tuple[EffectKind, str]:
    said = _said(record)
    names = record.names
    if entity_id in record.targets:
        service = next(
            (
                plan.split(" ", 1)[0].split(".", 1)[-1] for plan in record.plans
                if entity_id in plan.split(" ", 1)[-1].split(", ")
            ),
            "",
        )
        return EffectKind.DIRECT_EFFECT, f"{said} Ich habe daraufhin {entity_name} {_verb(service)}."
    effect = next((item for item in record.effects if entity_id in item.entity_ids), None)
    if effect is not None and effect.via:
        domain = effect.via.split(".", 1)[0]
        kind = EffectKind.SCENE_EFFECT if domain == "scene" else EffectKind.SCRIPT_EFFECT
        started = f"Ich habe {_KIND_WORD.get(domain, 'die Routine')} {names.get(effect.via, effect.via)} gestartet."
        step = f"Dessen Schritt ‚{effect.step_alias}‘" if effect.step_alias else (
            "Diese Szene" if domain == "scene" else "Das Skript"
        )
        return kind, f"{said} {started} {step} hat {entity_name} {_verb(effect.service)}."
    targets = _names(record.targets, names)
    return EffectKind.SCRIPT_EFFECT, f"{said} Ich habe {targets} ausgeführt; dabei wurde {entity_name} geschaltet."


def explain_change(
    entity_id: str,
    entity_name: str,
    state_context: Any,
    changed_at: datetime | None,
    store: ExecutionTraceStore,
    index: ContextIndex,
    user_names: Mapping[str, str] | None = None,
    name_of: Callable[[str], str | None] | None = None,
) -> CauseExplanation:
    """Why ``entity_id`` has its current state, strictly from evidence."""
    user_names = user_names or {}
    resolve_name: Callable[[str], str | None] = name_of or (lambda _entity_id: None)
    context_id = getattr(state_context, "id", None)
    user_id = getattr(state_context, "user_id", None)
    hops: list[str] = []
    automation_parts: list[str] = []
    current = context_id
    for _ in range(_MAX_HOPS):
        record = store.get(current)
        if record is not None:
            if automation_parts:
                # HomeIntent changed something that triggered an automation.
                done = _names(record.targets, record.names) or "eine Aktion"
                sentence = f"{_said(record)} Ich habe {done} ausgeführt. " + " ".join(automation_parts)
                return CauseExplanation(
                    EffectKind.SECONDARY_EFFECT, Evidence.VERIFIED, sentence.strip(),
                    record.execution_id, tuple(hops),
                )
            kind, sentence = _homeintent_sentence(record, entity_id, entity_name)
            return CauseExplanation(kind, Evidence.VERIFIED, sentence.strip(), record.execution_id, tuple(hops))
        event = index.get(current)
        if event is None:
            break
        hops.append(event.entity_id)
        if event.kind == "automation":
            source = _source_phrase(event.source, resolve_name)
            trigger = f", ausgelöst durch {source}," if source else ""
            automation_parts.insert(
                0, f"Automation „{event.name}“{trigger} hat daraufhin {entity_name} geschaltet."
                if not automation_parts else f"Automation „{event.name}“{trigger} lief danach."
            )
        else:
            automation_parts.insert(0, f"Das Skript „{event.name}“ hat {entity_name} geschaltet.")
        if event.user_id and not event.parent_id:
            person = user_names.get(event.user_id, "ein Benutzer")
            return CauseExplanation(
                EffectKind.EXTERNAL_EFFECT, Evidence.VERIFIED,
                f"{person} hat um {_clock(event.time)} das Skript „{event.name}“ gestartet. "
                + " ".join(automation_parts[1:]),
                chain=tuple(hops),
            )
        current = event.parent_id
    if automation_parts:
        when = f" um {_clock(changed_at)}" if changed_at else ""
        first = automation_parts[0]
        if first.startswith("Automation"):
            return CauseExplanation(
                EffectKind.AUTOMATION_EFFECT, Evidence.VERIFIED,
                f"{first.replace(' hat daraufhin', f' hat{when}')}".strip(), chain=tuple(hops),
            )
        return CauseExplanation(EffectKind.SCRIPT_EFFECT, Evidence.VERIFIED, first, chain=tuple(hops))
    if user_id:
        person = user_names.get(user_id, "Ein Benutzer")
        when = f" um {_clock(changed_at)}" if changed_at else ""
        return CauseExplanation(
            EffectKind.EXTERNAL_EFFECT, Evidence.VERIFIED,
            f"{person} hat {entity_name}{when} selbst geschaltet, zum Beispiel in der App.",
        )
    # No evidence: at most a guess from temporal proximity.
    if changed_at is not None:
        nearby = [
            record for record in store.recent()
            if abs(_safe_aware(record.time, changed_at) - changed_at) <= _POSSIBLE_WINDOW
        ]
        related = [
            record for record in nearby
            if entity_id in record.possible_followups
            or any(entity_id in effect.entity_ids for effect in record.effects)
        ]
        candidates = related or nearby
        if candidates:
            record = candidates[0]
            what = _names(record.targets, record.names) or "eine Aktion"
            return CauseExplanation(
                EffectKind.UNKNOWN_CAUSE, Evidence.POSSIBLE,
                f"Dafür finde ich keine Ursache, die ich belegen kann; zeitgleich lief um "
                f"{_clock(record.created_at)} {what}. Das könnte zusammenhängen.",
                record.execution_id,
            )
    return CauseExplanation(
        EffectKind.UNKNOWN_CAUSE, Evidence.UNKNOWN,
        "Dafür finde ich keine Ursache, die ich belegen kann.",
    )


def _safe_aware(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def context_event_from_bus(event: Any, kind: str) -> ContextEvent | None:
    """Build a ``ContextEvent`` from an ``automation_triggered``/``script_started`` event."""
    context = getattr(event, "context", None)
    data = getattr(event, "data", {}) or {}
    context_id = getattr(context, "id", None)
    entity_id = data.get("entity_id")
    if not context_id or not entity_id:
        return None
    fired = getattr(event, "time_fired", None)
    return ContextEvent(
        context_id=str(context_id),
        kind=kind,
        entity_id=str(entity_id),
        name=str(data.get("name") or entity_id),
        parent_id=getattr(context, "parent_id", None),
        user_id=getattr(context, "user_id", None),
        time=(fired.isoformat() if isinstance(fired, datetime) else datetime.now().isoformat()),
        source=data.get("source"),
    )


# ------------------------------------------------------------ wiring
TRACE_DATA_KEY = "homeintent_execution_trace"


@dataclass
class TraceRuntime:
    store: ExecutionTraceStore
    index: ContextIndex
    store_text: bool = True


def register_trace(
    hass: Any, store: ExecutionTraceStore, index: ContextIndex, *, store_text: bool = True
) -> TraceRuntime:
    runtime = TraceRuntime(store, index, store_text)
    data = getattr(hass, "data", None)
    if isinstance(data, dict):
        data[TRACE_DATA_KEY] = runtime
    return runtime


def trace_for(hass: Any) -> TraceRuntime | None:
    data = getattr(hass, "data", None)
    runtime = data.get(TRACE_DATA_KEY) if isinstance(data, dict) else None
    return runtime if isinstance(runtime, TraceRuntime) else None


def record_execution(
    hass: Any,
    *,
    context: Any,
    plan: Any,
    decision: Any,
    user_id: str | None,
    utterance: str | None,
    origin: str | None,
    attended: bool,
    now: datetime,
    entity_names: Mapping[str, str] | None = None,
) -> None:
    """Add one executed plan to the turn's trace record (one per execution_id)."""
    runtime = trace_for(hass)
    execution = getattr(context, "id", None)
    if runtime is None or not execution:
        return
    targets = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    effects = getattr(decision, "effects", None)
    names = dict(entity_names or {})
    if effects is not None:
        names.update(effects.names)
    states = getattr(hass, "states", None)
    for entity_id in targets:
        state = states.get(entity_id) if states is not None and hasattr(states, "get") else None
        name = getattr(state, "attributes", {}).get("friendly_name") if state is not None else None
        if name:
            names.setdefault(entity_id, str(name))
    new_effects = effects_from_plan_effects(effects)
    relevant = set(targets) | {
        item for effect in new_effects for item in (*effect.entity_ids, effect.via or "")
    } | set(effects.possible_followups if effects is not None else ())
    names = {key: value for key, value in names.items() if key in relevant}
    existing = runtime.store.get(str(execution))
    plan_text = f"{plan.domain}.{plan.service} {', '.join(targets)}"
    outcome = getattr(getattr(decision, "outcome", None), "name", None)
    risk = getattr(getattr(decision, "risk", None), "name", None)
    if existing is None:
        runtime.store.add(TraceRecord(
            execution_id=str(execution),
            created_at=now.isoformat(),
            actor=actor_hash(user_id),
            user_present=attended,
            utterance=(
                utterance_for_trace(utterance, store_text=runtime.store_text) if utterance else None
            ),
            origin=origin,
            plans=(plan_text,),
            targets=targets,
            effects=new_effects,
            possible_followups=tuple(effects.possible_followups) if effects is not None else (),
            policy=outcome,
            risk=risk,
            names=names,
            parent_id=getattr(context, "parent_id", None),
        ))
    else:
        runtime.store.merge(
            str(execution),
            plans=(*existing.plans, plan_text),
            targets=tuple(dict.fromkeys((*existing.targets, *targets))),
            effects=(*existing.effects, *new_effects),
            possible_followups=tuple(dict.fromkeys((
                *existing.possible_followups,
                *(effects.possible_followups if effects is not None else ()),
            ))),
            names={**existing.names, **names},
        )
    runtime.store.prune(now)


# ------------------------------------------------------------ persistence
def read_trace_file(path: str) -> list[dict[str, Any]]:
    import json

    try:
        with open(path, encoding="utf-8") as handle:
            content = json.load(handle)
    except (FileNotFoundError, ValueError, OSError):
        return []
    items = content.get("records") if isinstance(content, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def write_trace_file(path: str, records: list[dict[str, Any]]) -> None:
    import json
    import os
    import tempfile

    directory = os.path.dirname(path) or "."
    handle, temp = tempfile.mkstemp(dir=directory, prefix=".homeintent_trace")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump({"version": 1, "records": records}, file, ensure_ascii=False)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


async def async_setup_trace(
    hass: Any, path: str, *, limit: int, days: int, store_text: bool
) -> tuple[TraceRuntime, Any]:
    """Load the ring buffer, follow HA's automation/script runs, persist.

    Returns the runtime and a callable that stops listening and saves.
    """
    from datetime import timezone

    try:
        from homeassistant.util import dt as dt_util

        set_local_timezone(dt_util.get_default_time_zone())
    except (ImportError, AttributeError):  # pragma: no cover - stub environments
        pass
    store = ExecutionTraceStore(limit, days)
    store.load(await hass.async_add_executor_job(read_trace_file, path))
    store.prune(datetime.now(timezone.utc))
    index = ContextIndex()
    runtime = register_trace(hass, store, index, store_text=store_text)
    removers: list[Any] = []
    saved = {"snapshot": store.to_list()}

    def _on_automation(event: Any) -> None:
        item = context_event_from_bus(event, "automation")
        if item is not None:
            index.add(item)

    def _on_script(event: Any) -> None:
        item = context_event_from_bus(event, "script")
        if item is not None:
            index.add(item)

    bus = getattr(hass, "bus", None)
    if bus is not None:
        removers.append(bus.async_listen("automation_triggered", _on_automation))
        removers.append(bus.async_listen("script_started", _on_script))

    async def _save(*_args: Any) -> None:
        snapshot = store.to_list()
        if snapshot == saved["snapshot"]:
            return
        saved["snapshot"] = snapshot
        await hass.async_add_executor_job(write_trace_file, path, snapshot)

    try:
        from homeassistant.helpers.event import async_track_time_interval

        removers.append(async_track_time_interval(hass, _save, timedelta(minutes=1)))
    except ImportError:  # pragma: no cover - stub environments
        pass

    async def _stop() -> None:
        for remove in removers:
            remove()
        await _save()

    return runtime, _stop


def trace_view(record: TraceRecord) -> dict[str, Any]:
    """Learning Center row: names instead of ids, no HA data copied."""
    names = record.names
    effects = [
        {
            "label": names.get(entity_id, entity_id),
            "via": names.get(effect.via, effect.via) if effect.via else None,
            "step": effect.step_alias,
            "service": f"{effect.domain}.{effect.service}",
        }
        for effect in record.effects
        for entity_id in effect.entity_ids
    ]
    return {
        "execution_id": record.execution_id,
        "timestamp": record.created_at,
        "utterance": record.utterance if record.utterance and not record.utterance.startswith("#") else None,
        "origin": record.origin,
        "user_present": record.user_present,
        "targets": [names.get(item, item) for item in record.targets],
        "effects": effects[:50],
        "effect_count": len(effects),
        "possible_followups": [names.get(item, item) for item in record.possible_followups],
        "risk": record.risk,
    }
