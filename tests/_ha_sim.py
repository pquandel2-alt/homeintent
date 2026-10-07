"""A small, explicit model of how Home Assistant evaluates the automations
HomeIntent generates - used to check the *effect* of a monitoring request in
both directions (it fires / it does not fire), not only its preview.

Covered (exactly what the generator emits for monitoring requests):

* triggers: ``state`` (``to``/``from``/``not_from``/``for``, entity lists =
  any member), ``time`` (``at``), ``numeric_state`` (``above``/``below``,
  fires on the crossing, 7.9.2), ``time_pattern`` (``minutes: /N``, 7.9.3),
  ``homeassistant`` start (7.9.3);
* conditions: ``state`` (entity list = *all* members), ``or``/``and``/``not``,
  ``time`` (``after``/``before``), ``template`` for the typed inactivity
  condition (see ``_template_holds``);
* actions: ``notify.send_message`` (a message template HomeIntent built
  itself is rendered with Jinja, ``is_state`` and ``trigger.to_state``,
  7.9.2), ``delay``, ``condition``,
  ``wait_for_trigger`` with ``timeout``/``continue_on_timeout``, ``repeat``
  with ``until`` (evaluated on a timeline, see ``run``);
* 7.9.3: ``weather.get_forecasts`` with ``response_variable`` (the forecast
  comes from ``World.forecasts``), template conditions over that variable,
  ``is_state``/``states()``/``states.<id>.last_changed``/``now()`` rendered
  with Jinja like Home Assistant; device actions (``cover.*``, ``valve.*``,
  ``light.*``, ``switch.*``, ``media_player.*``) are recorded in
  ``World.service_calls``.

Anything else raises, so a test can never pass on semantics this model does
not know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


def entities_of(item: dict) -> set[str]:
    entity = item["entity_id"]
    return {entity} if isinstance(entity, str) else set(entity)


def _seconds(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, dict):
        return (
            int(value.get("hours", 0)) * 3600 + int(value.get("minutes", 0)) * 60
            + int(value.get("seconds", 0))
        )
    if isinstance(value, str):
        hours, minutes, seconds = (int(part) for part in value.split(":"))
        return hours * 3600 + minutes * 60 + seconds
    return int(value)


@dataclass
class World:
    """States plus how long each entity has had its state (seconds)."""

    states: dict[str, str]
    since: dict[str, int] = field(default_factory=dict)  # seconds in current state
    names: dict[str, str] = field(default_factory=dict)  # friendly names (7.9.2)
    clock: tuple[int, int] = (12, 0)  # local hour, minute
    changed_today: dict[str, bool] = field(default_factory=dict)  # left rest state since midnight
    # 7.9.3: forecasts per (weather entity, "daily"|"hourly"); device calls made;
    # seconds since each entity's last change (``states.x.last_changed``).
    forecasts: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    service_calls: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    changed_seconds_ago: dict[str, float] = field(default_factory=dict)

    def held_for(self, entity: str) -> int:
        return self.since.get(entity, 10**9)


def condition_holds(condition: dict, world: World) -> bool:
    kind = condition["condition"]
    if kind == "state":
        wanted = condition["state"]
        wanted = wanted if isinstance(wanted, list) else [wanted]
        return all(world.states[entity] in wanted for entity in entities_of(condition))
    if kind == "or":
        return any(condition_holds(child, world) for child in condition["conditions"])
    if kind == "and":
        return all(condition_holds(child, world) for child in condition["conditions"])
    if kind == "not":
        return not any(condition_holds(child, world) for child in condition["conditions"])
    if kind == "time":
        minutes = world.clock[0] * 60 + world.clock[1]

        def at(text: str) -> int:
            hour, minute, _ = (int(part) for part in text.split(":"))
            return hour * 60 + minute

        after = at(condition["after"]) if "after" in condition else None
        before = at(condition["before"]) if "before" in condition else None
        if after is not None and before is not None and after > before:
            return minutes >= after or minutes < before
        return (after is None or minutes >= after) and (before is None or minutes < before)
    if kind == "template":
        return _template_holds(condition, world)
    raise AssertionError(f"unmodelled condition {kind}")


_TEMPLATE_PART = re.compile(
    r"^(?:is_state\('(?P<entity>[a-z_]+\.[a-z0-9_]+)', '(?P<state>[a-z_]+)'\)|"
    r"states\.(?P<changed>[a-z_]+\.[a-z0-9_]+)\.last_changed < today_at\('00:00'\))$"
)


def _template_holds(condition: dict, world: World) -> bool:
    """The typed inactivity template (7.9 W2) and nothing else: a conjunction
    of ``is_state(e, s)`` and ``states.e.last_changed < today_at('00:00')``."""
    text = condition["value_template"].strip()
    if not (text.startswith("{{") and text.endswith("}}")):
        raise AssertionError("unmodelled template")
    for part in text[2:-2].strip().split(" and "):
        match = _TEMPLATE_PART.match(part.strip())
        if match is None:
            raise AssertionError(f"unmodelled template part {part!r}")
        if match.group("entity") and world.states[match.group("entity")] != match.group("state"):
            return False
        if match.group("changed") and world.changed_today.get(match.group("changed"), False):
            return False
    return True


def trigger_fires(trigger: dict, world: World, changed: str | None, before: str | None) -> bool:
    kind = trigger["trigger"]
    if kind == "state":
        if changed is None or changed not in entities_of(trigger):
            return False
        now = world.states[changed]
        if now == before:
            return False
        wanted = trigger.get("to")
        if wanted is not None and now not in (wanted if isinstance(wanted, list) else [wanted]):
            return False
        if "from" in trigger and before != trigger["from"]:
            return False
        if before in trigger.get("not_from", ()):  # 7.9.3: e.g. a restart (unknown -> home)
            return False
        return _seconds(trigger.get("for")) == 0
    if kind == "time":
        return changed is None and world.clock == tuple(int(p) for p in trigger["at"].split(":")[:2])
    if kind == "numeric_state":
        if changed is None or changed not in entities_of(trigger):
            return False

        def inside(value: str | None) -> bool:
            try:
                number = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False
            if "above" in trigger and not number > float(trigger["above"]):
                return False
            return not ("below" in trigger and not number < float(trigger["below"]))

        # Home Assistant fires when the value crosses into the range.
        return inside(world.states[changed]) and not inside(before)
    if kind == "time_pattern":
        step = int(str(trigger.get("minutes", "/60")).lstrip("/"))
        return changed is None and world.clock[1] % step == 0
    if kind == "homeassistant":
        # Fires only when Home Assistant starts (7.9.3), modelled as the
        # pseudo change ``"homeassistant.start"``.
        return trigger.get("event") == "start" and changed == "homeassistant.start"
    raise AssertionError(f"unmodelled trigger {kind}")


def fires(automation: dict, world: World, changed: str | None = None, before: str | None = None) -> bool:
    """Does this change (or this clock tick, ``changed=None``) run the actions?"""
    return any(trigger_fires(item, world, changed, before) for item in automation["triggers"]) and all(
        condition_holds(item, world) for item in automation.get("conditions", [])
    )


def for_trigger_fires(automation: dict, world: World, entity: str) -> bool:
    """A ``for:`` trigger fires once its entity has held the state long enough."""
    for trigger in automation["triggers"]:
        if trigger["trigger"] != "state" or entity not in entities_of(trigger):
            continue
        wanted = trigger.get("to")
        if wanted is not None and world.states[entity] not in (wanted if isinstance(wanted, list) else [wanted]):
            continue
        needed = _seconds(trigger.get("for"))
        if needed and world.held_for(entity) >= needed:
            return all(condition_holds(item, world) for item in automation.get("conditions", []))
    return False


@dataclass
class Sent:
    at: int
    target: tuple[str, ...]
    message: str


_IS_STATE = re.compile(r"^is_state\('(?P<entity>[a-z_]+\.[a-z0-9_]+)', '(?P<state>[a-z_]+)'\)$")


def _state_template(text: str, world: World) -> bool:
    """``is_state(e, s)`` joined by ``and`` - the wait templates the
    generator emits; anything else raises."""
    body = text.strip()
    if body.startswith("{{") and body.endswith("}}"):
        body = body[2:-2]
    result = True
    for part in body.strip().split(" and "):
        match = _IS_STATE.match(part.strip())
        if match is None:
            raise AssertionError(f"unmodelled wait template {part!r}")
        result = result and world.states[match.group("entity")] == match.group("state")
    return result


def render_message(message: str, world: World, trigger_entity: str | None = None) -> str:
    """A message template as Home Assistant renders it (7.9.2): Jinja with
    ``is_state``, ``states`` and ``trigger.to_state`` and Home Assistant's
    ``float(default)`` filter - nothing else is provided, so a template
    using more fails the test."""
    if "{{" not in message and "{%" not in message:
        return message
    from types import SimpleNamespace

    import jinja2

    env = jinja2.Environment(undefined=jinja2.StrictUndefined, extensions=["jinja2.ext.loopcontrols"])

    def to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    env.filters["float"] = to_float
    to_state = (
        SimpleNamespace(name=world.names.get(trigger_entity, trigger_entity), state=world.states[trigger_entity])
        if trigger_entity is not None else None
    )
    return env.from_string(message).render(
        is_state=lambda entity, state: world.states.get(entity) == state,
        states=lambda entity: world.states.get(entity, "unknown"),
        trigger=SimpleNamespace(to_state=to_state),
        namespace=jinja2.utils.Namespace,
    )


_DEVICE_DOMAINS = frozenset({"cover", "valve", "light", "switch", "media_player", "fan"})


def _rendered_template(text: str) -> bool:
    """7.9.3 templates are rendered with Jinja (forecast variable, last change)."""
    return "homeintent_" in text or "last_changed" in text or "{{ not (" in text


def _render_template(text: str, world: World, variables: dict[str, Any]) -> str:
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    import jinja2

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)

    def to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    env.filters["float"] = to_float
    moment = datetime(2026, 10, 7, *world.clock)

    class _States:
        def __call__(self, entity: str) -> str:
            return world.states.get(entity, "unknown")

        def __getattr__(self, domain: str) -> Any:
            return SimpleNamespace(**{
                entity.split(".", 1)[1]: SimpleNamespace(
                    state=state,
                    last_changed=moment - timedelta(seconds=world.changed_seconds_ago.get(entity, 10**7)),
                )
                for entity, state in world.states.items() if entity.startswith(domain + ".")
            })

    return env.from_string(text).render(
        is_state=lambda entity, state: world.states.get(entity) == state,
        states=_States(), now=lambda: moment, namespace=jinja2.utils.Namespace,
        **{key: value for key, value in variables.items() if key.startswith("homeintent_")},
    ).strip()


def run(
    actions: list[dict],
    world: World,
    events: dict[int, Callable[[World], None]] | None = None,
    horizon: int = 24 * 3600,
    trigger_entity: str | None = None,
) -> list[Sent]:
    """Run an action sequence on a timeline (seconds from the trigger).

    ``events`` change the world at given seconds (a door closes after 20
    minutes ...).  Returns every notification sent, with its time.
    """
    events = dict(events or {})
    sent: list[Sent] = []
    now = 0
    variables: dict[str, Any] = {}

    def advance(until: int, stop: Callable[[], bool] | None = None) -> bool:
        nonlocal now
        if stop is not None and stop():
            return True
        for moment in sorted(t for t in events if now < t <= until):
            events.pop(moment)(world)
            now = moment
            if stop is not None and stop():
                return True
        now = until
        return False

    def holds(condition: dict) -> bool:
        if condition.get("condition") == "template" and _rendered_template(condition["value_template"]):
            return _render_template(condition["value_template"], world, variables) == "True"
        if condition.get("condition") == "template":
            text = condition["value_template"].replace(" ", "")
            if text.startswith("{{repeat.index<="):
                return variables["repeat_index"] <= int(text[len("{{repeat.index<="):-2])
            if text == "{{notwait.completed}}":
                return not variables.get("wait_completed", False)
        return condition_holds(condition, world)

    def execute(steps: list[dict]) -> bool:
        """False stops the sequence (a failed condition)."""
        for step in steps:
            if now > horizon:
                return False
            if step.get("action") == "notify.send_message":
                sent.append(Sent(
                    now, tuple(step["target"]["entity_id"]),
                    render_message(step["data"]["message"], world, trigger_entity),
                ))
            elif step.get("action") == "homeintent.delete_automation":
                continue
            elif step.get("action") == "weather.get_forecasts":
                entity = step["target"]["entity_id"]
                variables[step["response_variable"]] = {
                    entity: {"forecast": world.forecasts.get((entity, step["data"]["type"]), [])}
                }
            elif str(step.get("action", "")).split(".", 1)[0] in _DEVICE_DOMAINS:
                domain, service = step["action"].split(".", 1)
                target = step.get("target", {}).get("entity_id")
                ids = (target,) if isinstance(target, str) else tuple(target or ())
                world.service_calls.append((domain, service, ids))
            elif "delay" in step:
                advance(now + _seconds(step["delay"]))
            elif "sequence" in step and len(step) == 1:
                if not execute(step["sequence"]):
                    return False
            elif "condition" in step:
                if not holds(step):
                    return False
            elif "wait_template" in step:
                timeout = _seconds(step.get("timeout"))
                completed = advance(now + timeout, lambda: _state_template(step["wait_template"], world))
                variables["wait_completed"] = completed
                if not completed and not step.get("continue_on_timeout", True):
                    return False
            elif "repeat" in step:
                repeat = step["repeat"]
                if "while" not in repeat:
                    raise AssertionError("unmodelled repeat")
                variables["repeat_index"] = 1
                while all(holds(item) for item in repeat["while"]):
                    if not execute(repeat["sequence"]) or now > horizon:
                        break
                    variables["repeat_index"] += 1
            elif "if" in step:
                branch = step["then"] if all(holds(c) for c in step["if"]) else step.get("else", [])
                if not execute(branch):
                    return False
            else:
                raise AssertionError(f"unmodelled action {step}")
        return True

    execute(actions)
    return sent
