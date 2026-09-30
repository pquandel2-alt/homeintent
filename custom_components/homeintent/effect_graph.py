"""Static, read-only transitive effect analysis for scripts, scenes and groups.

A service plan that activates a script, a scene, a group or an automation
changes far more than its one outer entity: a script may press every button
of a floor (real incident, 7.1.2: ``button.press`` with ``floor_id`` started
the vacuum and the smoke detector self test). The exposure list, the
read-only/admin-only lists and the risk classification therefore have to be
applied to the *effective* targets, not only to the outer entity.

This module only reads. It never calls a service. It has two layers:

* a hass-free core (``build_effect_graph``) that walks action configurations
  through a small ``EffectSources`` protocol, so every rule is unit-testable;
* ``HassEffectSources``/``build_plan_effects``, the adapter that reads the
  real script, scene, group and automation configuration and resolves
  ``device_id``/``area_id``/``floor_id``/``label_id`` targets exactly as Home
  Assistant does (``helpers.target.async_extract_referenced_entity_ids``).

Everything that cannot be determined statically is reported as an
``UnknownStep``; ``complete`` is then ``False`` and the policy fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .entities import normalize_for_compare

MAX_DEPTH = 8

TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")

# Services that never change a device and carry no entity target.
_HARMLESS_SERVICE_DOMAINS = frozenset({"persistent_notification", "logbook", "system_log"})
_HARMLESS_SERVICES: frozenset[tuple[str, str]] = frozenset()
# Services whose effect cannot be determined statically at all.
_OPAQUE_SERVICE_DOMAINS = frozenset({
    "python_script", "shell_command", "rest_command", "pyscript", "command_line",
    "hassio", "mqtt", "conversation", "intent_script", "script_runner",
})
# Control-flow and bookkeeping steps without an effect of their own.
_NEUTRAL_STEP_KEYS = frozenset({
    "delay", "wait_template", "wait_for_trigger", "variables", "condition",
    "stop", "set_conversation_response",
})
# Known data keys that name a second, real target of a foreign service.
_DATA_TARGETS: Mapping[str, tuple[str, str]] = {
    "media_player_entity_id": ("media_player", "play_media"),
}
# Domains whose entities are containers: activating them runs other things.
COMPOSITE_DOMAINS = frozenset({"script", "scene", "automation", "group"})

_REGISTRY_UUID = re.compile(r"^[0-9a-f]{32}$")
_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


@dataclass(frozen=True)
class Effect:
    """One statically determined write: ``domain.service`` on ``entity_ids``."""

    domain: str
    service: str
    entity_ids: tuple[str, ...]
    kind: str = "service"  # service | scene | group | device_action
    step_alias: str | None = None
    path: tuple[str, ...] = ()
    # How the step selected its targets, e.g. (("floor_id", "erdgeschoss"),).
    selector: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class UnknownStep:
    reason: str
    step_alias: str | None = None
    path: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectGraph:
    root: str
    effects: tuple[Effect, ...] = ()
    nested: tuple[str, ...] = ()
    unknown: tuple[UnknownStep, ...] = ()
    possible_followups: tuple[str, ...] = ()
    # Friendly names for every entity/area/floor mentioned, for answers.
    names: Mapping[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def effective_targets(self) -> frozenset[str]:
        return frozenset(entity_id for effect in self.effects for entity_id in effect.entity_ids)


@dataclass(frozen=True)
class PlanEffects:
    """The merged effect graphs of every composite target of one plan."""

    roots: tuple[str, ...]
    graphs: tuple[EffectGraph, ...] = ()
    # Targets Home Assistant does not know right now (deleted, renamed or
    # disabled entities): a step on them switches nothing (7.8.1).
    missing: frozenset[str] = frozenset()

    @property
    def effects(self) -> tuple[Effect, ...]:
        return tuple(effect for graph in self.graphs for effect in graph.effects)

    @property
    def unknown(self) -> tuple[UnknownStep, ...]:
        return tuple(step for graph in self.graphs for step in graph.unknown)

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def effective_targets(self) -> frozenset[str]:
        """What the plan switches now; unknown entities switch nothing."""
        return self.referenced_targets - self.missing

    @property
    def referenced_targets(self) -> frozenset[str]:
        """Every entity the steps name, including ones HA does not know."""
        return frozenset(entity_id for graph in self.graphs for entity_id in graph.effective_targets)

    @property
    def possible_followups(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item for graph in self.graphs for item in graph.possible_followups))

    @property
    def names(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for graph in self.graphs:
            merged.update(graph.names)
        return merged

    @classmethod
    def unchecked(cls, roots: Iterable[str]) -> "PlanEffects":
        """Fail-closed placeholder when a caller could not build the graph."""
        root_ids = tuple(roots)
        return cls(
            root_ids,
            tuple(
                EffectGraph(root, unknown=(UnknownStep("Die Wirkung wurde nicht geprüft."),))
                for root in root_ids
            ),
        )


class EffectSources(Protocol):
    """Read-only access to the configuration the graph is built from."""

    def script_sequence(self, entity_id: str) -> Sequence[Any] | None: ...

    def automation_sequence(self, entity_id: str) -> Sequence[Any] | None: ...

    def scene_states(self, entity_id: str) -> Mapping[str, Any] | None: ...

    def group_members(self, entity_id: str) -> tuple[str, ...] | None: ...

    def resolve_target(self, selector: Mapping[str, Any]) -> frozenset[str] | None: ...

    def resolve_registry_id(self, value: str) -> str | None: ...

    def entity_exists(self, entity_id: str) -> bool: ...

    def entities_of_domain(self, domain: str) -> tuple[str, ...]: ...

    def followups(self, entity_ids: frozenset[str]) -> tuple[str, ...]: ...

    def name(self, kind: str, item_id: str) -> str | None: ...


def is_template(value: object) -> bool:
    """Template objects (validated config) and raw Jinja strings."""
    if isinstance(value, str):
        return "{{" in value or "{%" in value
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(is_template(item) for item in value)
    if isinstance(value, Mapping):
        return False
    return hasattr(value, "async_render") or type(value).__name__ == "Template"


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value]
    return [str(value)]


def is_composite_entity(entity_id: str, attributes: Mapping[str, Any] | None = None) -> bool:
    domain = entity_id.split(".", 1)[0]
    if domain in COMPOSITE_DOMAINS:
        return True
    members = (attributes or {}).get("entity_id")
    return isinstance(members, (list, tuple)) and bool(members)


_ACTIVATING_SERVICES = frozenset({"turn_on", "toggle"})


def _scene_state_service(domain: str, target_state: str) -> str:
    """The direct service a scene state corresponds to (for classification)."""
    state = target_state.casefold()
    if domain == "lock":
        return {"unlocked": "unlock", "open": "open", "locked": "lock"}.get(state, "set_state")
    if domain in {"cover", "valve"}:
        suffix = "cover" if domain == "cover" else "valve"
        if state in {"open", "opening"}:
            return f"open_{suffix}"
        if state in {"closed", "closing"}:
            return f"close_{suffix}"
        return "set_state"
    if domain == "alarm_control_panel":
        return "alarm_disarm" if state == "disarmed" else "alarm_arm"
    if state == "off":
        return "turn_off"
    if state == "on":
        return "turn_on"
    return "set_state"


class _Builder:
    def __init__(self, root: str, sources: EffectSources, max_depth: int) -> None:
        self.root = root
        self.sources = sources
        self.max_depth = max_depth
        self.effects: list[Effect] = []
        self.nested: list[str] = []
        self.unknown: list[UnknownStep] = []
        self.names: dict[str, str] = {}
        self._active: set[str] = set()
        # Scenes created by an earlier step (``scene.create``): id -> states,
        # or ``None`` for a snapshot whose states are not known statically.
        self._created_scenes: dict[str, Mapping[str, Any] | None] = {}

    # -------------------------------------------------------------- helpers
    def _name(self, kind: str, item_id: str) -> None:
        if item_id in self.names:
            return
        name = self.sources.name(kind, item_id)
        if name:
            self.names[item_id] = name

    def _unknown(self, reason: str, alias: str | None, path: tuple[str, ...]) -> None:
        self.unknown.append(UnknownStep(reason, alias, path))

    def _add_effect(
        self,
        domain: str,
        service: str,
        entity_ids: Iterable[str],
        *,
        kind: str,
        alias: str | None,
        path: tuple[str, ...],
        selector: tuple[tuple[str, str], ...] = (),
    ) -> None:
        ids = tuple(dict.fromkeys(entity_ids))
        if not ids:
            return
        for entity_id in ids:
            self._name("entity", entity_id)
        self.effects.append(Effect(domain, service, ids, kind, alias, path, selector))

    # ------------------------------------------------------------- entities
    def activate(
        self,
        entity_id: str,
        service: str,
        path: tuple[str, ...],
        depth: int,
        alias: str | None = None,
    ) -> None:
        """What calling ``service`` on ``entity_id`` does, transitively."""
        domain = entity_id.split(".", 1)[0]
        self._name("entity", entity_id)
        if entity_id in self._active:
            return  # cycle: its effects are already being collected
        if depth > self.max_depth:
            self._unknown(
                f"„{self.names.get(entity_id, entity_id)}“ ist tiefer als {self.max_depth} Ebenen verschachtelt.",
                alias, path,
            )
            return
        self._active.add(entity_id)
        try:
            if domain == "script" and service in _ACTIVATING_SERVICES:
                self._activate_script(entity_id, path, depth, alias)
            elif domain == "scene" and service in _ACTIVATING_SERVICES:
                self._activate_scene(entity_id, path, depth, alias)
            elif domain == "automation" and service == "trigger":
                sequence = self.sources.automation_sequence(entity_id)
                if sequence is None:
                    self._unknown(
                        f"Die Aktionen der Automation „{self.names.get(entity_id, entity_id)}“ sind nicht lesbar.",
                        alias, path,
                    )
                    return
                self.nested.append(entity_id)
                self.walk(sequence, (*path, entity_id), depth + 1)
            else:
                members = self.sources.group_members(entity_id)
                if members is not None:
                    self.nested.append(entity_id)
                    self.apply_to(
                        members, service, (*path, entity_id), depth + 1, alias, kind="group"
                    )
        finally:
            self._active.discard(entity_id)

    def _activate_script(
        self, entity_id: str, path: tuple[str, ...], depth: int, alias: str | None
    ) -> None:
        sequence = self.sources.script_sequence(entity_id)
        if sequence is None:
            self._unknown(
                f"Der Inhalt des Skripts „{self.names.get(entity_id, entity_id)}“ ist nicht lesbar.",
                alias, path,
            )
            return
        self.nested.append(entity_id)
        self.walk(sequence, (*path, entity_id), depth + 1)

    def _activate_scene(
        self, entity_id: str, path: tuple[str, ...], depth: int, alias: str | None
    ) -> None:
        if entity_id in self._created_scenes:
            created = self._created_scenes[entity_id]
            if created is None:
                self._unknown(
                    f"Die Szene „{entity_id}“ stellt einen gespeicherten Zustand wieder her; "
                    "dessen Wirkung kann ich nicht prüfen.",
                    alias, path,
                )
                return
            self.nested.append(entity_id)
            self._apply_states(created, (*path, entity_id), depth + 1, alias, kind="scene")
            return
        states = self.sources.scene_states(entity_id)
        if states is None:
            self._unknown(
                f"Der Inhalt der Szene „{self.names.get(entity_id, entity_id)}“ ist nicht lesbar.",
                alias, path,
            )
            return
        self.nested.append(entity_id)
        self._apply_states(states, (*path, entity_id), depth + 1, alias, kind="scene")

    def _apply_states(
        self,
        states: Mapping[str, Any],
        path: tuple[str, ...],
        depth: int,
        alias: str | None,
        *,
        kind: str,
    ) -> None:
        for member, wanted in states.items():
            member_domain = member.split(".", 1)[0]
            if isinstance(wanted, Mapping):
                target_state = str(wanted.get("state", ""))
            else:
                target_state = str(getattr(wanted, "state", wanted))
            service = _scene_state_service(member_domain, target_state)
            self.apply_to((member,), service, path, depth, alias, kind=kind)

    def is_container(self, entity_id: str, service: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        if domain in {"script", "scene"}:
            return service in _ACTIVATING_SERVICES
        if domain == "automation":
            return service == "trigger"
        return self.sources.group_members(entity_id) is not None

    def apply_to(
        self,
        entity_ids: Iterable[str],
        service: str,
        path: tuple[str, ...],
        depth: int,
        alias: str | None,
        *,
        kind: str,
        call_domain: str | None = None,
        selector: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Record plain targets as effects and expand containers."""
        plain: list[str] = []
        for entity_id in entity_ids:
            if self.is_container(entity_id, service):
                self.activate(entity_id, service, path, depth, alias)
            else:
                plain.append(entity_id)
        by_domain: dict[str, list[str]] = {}
        for entity_id in plain:
            effect_domain = call_domain or entity_id.split(".", 1)[0]
            by_domain.setdefault(effect_domain, []).append(entity_id)
        for effect_domain, ids in by_domain.items():
            self._add_effect(
                effect_domain, service, ids, kind=kind, alias=alias, path=path, selector=selector
            )

    # -------------------------------------------------------------- actions
    def walk(self, actions: Sequence[Any] | Any, path: tuple[str, ...], depth: int) -> None:
        if isinstance(actions, Mapping):
            actions = [actions]
        if not isinstance(actions, (list, tuple)):
            self._unknown("Eine Aktionsfolge hat eine unbekannte Form.", None, path)
            return
        for action in actions:
            self.step(action, path, depth)

    def step(self, action: Any, path: tuple[str, ...], depth: int) -> None:
        if not isinstance(action, Mapping):
            self._unknown("Ein Schritt hat eine unbekannte Form.", None, path)
            return
        alias = action.get("alias")
        alias = str(alias) if isinstance(alias, str) and alias else None
        if action.get("enabled") is False:
            return
        if "action" in action or "service" in action:
            self._service_step(action, alias, path, depth)
        elif "scene" in action:
            scene = action.get("scene")
            if not isinstance(scene, str) or is_template(scene):
                self._unknown("Die Szene des Schritts ist nicht statisch bestimmbar.", alias, path)
            else:
                self.activate(scene, "turn_on", path, depth, alias)
        elif "device_id" in action and "domain" in action and "type" in action:
            self._device_step(action, alias, path)
        elif "choose" in action:
            options = action.get("choose") or []
            if isinstance(options, Mapping):
                options = [options]
            for option in options if isinstance(options, (list, tuple)) else []:
                if isinstance(option, Mapping):
                    self.walk(option.get("sequence") or [], path, depth)
            if action.get("default"):
                self.walk(action.get("default") or [], path, depth)
        elif "if" in action:
            self.walk(action.get("then") or [], path, depth)
            if action.get("else"):
                self.walk(action.get("else") or [], path, depth)
        elif "parallel" in action:
            branches = action.get("parallel") or []
            if isinstance(branches, Mapping):
                branches = [branches]
            for branch in branches if isinstance(branches, (list, tuple)) else []:
                if isinstance(branch, Mapping) and "sequence" in branch and len(branch) <= 2:
                    self.walk(branch.get("sequence") or [], path, depth)
                else:
                    self.walk(branch, path, depth)
        elif "repeat" in action:
            repeat = action.get("repeat")
            if isinstance(repeat, Mapping):
                self.walk(repeat.get("sequence") or [], path, depth)
            else:
                self._unknown("Eine Wiederholung hat eine unbekannte Form.", alias, path)
        elif "sequence" in action:
            self.walk(action.get("sequence") or [], path, depth)
        elif "event" in action:
            self._unknown(
                f"Der Schritt löst das Ereignis „{action.get('event')}“ aus; dessen Folgen kann ich nicht prüfen.",
                alias, path,
            )
        elif _NEUTRAL_STEP_KEYS & set(action):
            return
        else:
            self._unknown("Ein Schritt hat eine unbekannte Art.", alias, path)

    def _names_existing_entity(self, value: Any) -> bool:
        return any(
            _ENTITY_ID.match(text.strip()) and self.sources.entity_exists(text.strip())
            for text in _nested_strings(value)
        )

    def _create_scene(
        self, data_maps: Sequence[Mapping[str, Any]], alias: str | None, path: tuple[str, ...]
    ) -> None:
        """``scene.create`` changes nothing now; its later activation might."""
        data: dict[str, Any] = {}
        for item in data_maps:
            data.update(item)
        scene_id = data.get("scene_id")
        if not isinstance(scene_id, str) or is_template(scene_id):
            self._unknown("Die erzeugte Szene ist nicht statisch bestimmbar.", alias, path)
            return
        entities = data.get("entities")
        if "snapshot_entities" in data or (entities is not None and not isinstance(entities, Mapping)):
            self._created_scenes[f"scene.{scene_id}"] = None
            return
        if isinstance(entities, Mapping) and any(
            is_template(key) or is_template(value) for key, value in entities.items()
        ):
            self._created_scenes[f"scene.{scene_id}"] = None
            return
        self._created_scenes[f"scene.{scene_id}"] = dict(entities or {})

    def _device_step(self, action: Mapping[str, Any], alias: str | None, path: tuple[str, ...]) -> None:
        domain = str(action.get("domain"))
        action_type = str(action.get("type"))
        raw_entity = action.get("entity_id")
        entity_ids: list[str] = []
        if isinstance(raw_entity, str) and raw_entity:
            resolved = (
                self.sources.resolve_registry_id(raw_entity)
                if _REGISTRY_UUID.match(raw_entity) else raw_entity
            )
            if resolved is None:
                self._unknown("Das Gerät einer Geräte-Aktion ist nicht auflösbar.", alias, path)
                return
            entity_ids.append(resolved)
        else:
            device = action.get("device_id")
            resolved_set = self.sources.resolve_target({"device_id": device})
            if resolved_set is None:
                self._unknown("Das Gerät einer Geräte-Aktion ist nicht auflösbar.", alias, path)
                return
            entity_ids.extend(
                sorted(entity for entity in resolved_set if entity.split(".", 1)[0] == domain)
            )
            if not entity_ids:
                self._unknown("Eine Geräte-Aktion wirkt ohne bestimmbare Entität.", alias, path)
                return
        self._add_effect(
            domain, _device_action_service(action_type), entity_ids,
            kind="device_action", alias=alias, path=path,
        )

    def _service_step(
        self, action: Mapping[str, Any], alias: str | None, path: tuple[str, ...], depth: int
    ) -> None:
        name = action.get("action", action.get("service"))
        if not isinstance(name, str) or is_template(name) or "." not in name:
            self._unknown("Der Dienst des Schritts ist nicht statisch bestimmbar.", alias, path)
            return
        domain, service = name.split(".", 1)
        if domain == "script" and service not in {"turn_on", "toggle", "turn_off", "reload"}:
            # ``action: script.gute_nacht`` runs that script directly.
            self.activate(f"script.{service}", "turn_on", path, depth, alias)
            return
        if domain in _HARMLESS_SERVICE_DOMAINS or (domain, service) in _HARMLESS_SERVICES:
            return
        if domain in _OPAQUE_SERVICE_DOMAINS or (domain == "homeassistant" and service in {
            "restart", "stop", "reload_all", "reload_core_config", "set_location",
        }):
            self._unknown(f"Der Dienst {name} ist nicht statisch prüfbar.", alias, path)
            return

        selector: dict[str, Any] = {}
        sources_of_target: list[Mapping[str, Any]] = [action]
        if isinstance(action.get("target"), Mapping):
            sources_of_target.append(action["target"])
        data = action.get("data")
        data_template = action.get("data_template")
        data_maps = [item for item in (data, data_template) if isinstance(item, Mapping)]
        if any(item is not None and not isinstance(item, Mapping) for item in (data, data_template)):
            if action.get("target") is None and not any(key in action for key in TARGET_KEYS):
                self._unknown("Die Dienstdaten des Schritts sind nicht statisch bestimmbar.", alias, path)
                return
        sources_of_target.extend(data_maps)
        if action.get("target") is not None and not isinstance(action.get("target"), Mapping):
            self._unknown("Das Ziel des Schritts ist eine Vorlage.", alias, path)
            return
        for source in sources_of_target:
            for key in TARGET_KEYS:
                if key not in source:
                    continue
                value = source[key]
                if is_template(value):
                    self._unknown("Das Ziel des Schritts ist eine Vorlage.", alias, path)
                    return
                selector.setdefault(key, [])
                selector[key].extend(_as_list(value))

        if (domain, service) == ("scene", "create"):
            self._create_scene(data_maps, alias, path)
            return

        # Second targets hidden in the data of a foreign service, at any
        # depth (script ``variables``, lists of mappings ...): a known one is
        # an effect, any other existing entity makes the step unknown (7.7).
        scene_states = (domain, service) == ("scene", "apply")
        for data_map in data_maps:
            for key, value in data_map.items():
                if key in TARGET_KEYS or (key == "entities" and scene_states):
                    continue
                if key in _DATA_TARGETS:
                    if is_template(value):
                        self._unknown("Ein Ziel in den Dienstdaten ist eine Vorlage.", alias, path)
                        return
                    extra_domain, extra_service = _DATA_TARGETS[key]
                    self._add_effect(
                        extra_domain, extra_service, _as_list(value),
                        kind="service", alias=alias, path=path,
                    )
                    continue
                if self._names_existing_entity(value):
                    self._unknown(
                        f"Der Dienst {name} nennt in „{key}“ ein weiteres Ziel.", alias, path
                    )
                    return

        if (domain, service) == ("scene", "apply"):
            entities = next((item.get("entities") for item in data_maps if "entities" in item), None)
            if not isinstance(entities, Mapping):
                self._unknown("Die Zustände von scene.apply sind nicht statisch bestimmbar.", alias, path)
                return
            self._apply_states(entities, path, depth, alias, kind="scene")
            return

        entity_ids = [item for item in selector.get("entity_id", []) if item.casefold() != "none"]
        resolved: list[str] = []
        for entity_id in entity_ids:
            if entity_id.casefold() == "all":
                if domain == "homeassistant":
                    self._unknown("„alle Entitäten“ ist nicht prüfbar.", alias, path)
                    return
                resolved.extend(self.sources.entities_of_domain(domain))
            elif _REGISTRY_UUID.match(entity_id):
                real = self.sources.resolve_registry_id(entity_id)
                if real is None:
                    self._unknown("Ein Ziel des Schritts ist nicht auflösbar.", alias, path)
                    return
                resolved.append(real)
            else:
                resolved.append(entity_id)
        indirect = {key: selector[key] for key in TARGET_KEYS[1:] if selector.get(key)}
        selector_label: tuple[tuple[str, str], ...] = tuple(
            (key, item) for key, items in indirect.items() for item in items
        )
        for key, item in selector_label:
            self._name(key.removesuffix("_id"), item)
        if indirect:
            expanded = self.sources.resolve_target(indirect)
            if expanded is None:
                self._unknown("Das Ziel des Schritts ist nicht auflösbar.", alias, path)
                return
            if domain == "homeassistant":
                resolved.extend(sorted(expanded))
            else:
                resolved.extend(sorted(item for item in expanded if item.split(".", 1)[0] == domain))
        if not selector:
            if domain == "notify":
                return  # legacy notify service without entity: a message only
            self._unknown(f"Der Dienst {name} hat kein prüfbares Ziel.", alias, path)
            return

        resolved = list(dict.fromkeys(resolved))
        self.apply_to(
            resolved, service, path, depth, alias, kind="service",
            call_domain=None if domain == "homeassistant" else domain, selector=selector_label,
        )


def _nested_strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _nested_strings(item, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _nested_strings(item, depth + 1)


def _device_action_service(action_type: str) -> str:
    return {
        "turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle",
        "lock": "lock", "unlock": "unlock", "open": "open",
        "press": "press", "arm_away": "alarm_arm", "arm_home": "alarm_arm",
        "arm_night": "alarm_arm", "disarm": "alarm_disarm", "trigger": "alarm_trigger",
    }.get(action_type, action_type)


def build_effect_graph(
    root: str,
    sources: EffectSources,
    *,
    service: str = "turn_on",
    max_depth: int = MAX_DEPTH,
) -> EffectGraph:
    """Transitive effects of calling ``service`` on ``root`` (read-only)."""
    builder = _Builder(root, sources, max_depth)
    builder.activate(root, service, (), 0)
    targets = frozenset(entity_id for effect in builder.effects for entity_id in effect.entity_ids)
    followups: tuple[str, ...] = ()
    if targets:
        followups = tuple(
            item for item in sources.followups(targets) if item not in builder.nested
        )
        for item in followups:
            builder._name("entity", item)
    return EffectGraph(
        root=root,
        effects=tuple(builder.effects),
        nested=tuple(dict.fromkeys(builder.nested)),
        unknown=tuple(builder.unknown),
        possible_followups=followups,
        names=dict(builder.names),
    )


def composite_targets(
    domain: str,
    service: str,
    target_ids: Sequence[str],
    member_lookup: "GroupLookup",
) -> list[tuple[str, str]]:
    """(entity_id, effective service) for every composite target of a plan."""
    found: list[tuple[str, str]] = []
    for entity_id in target_ids:
        entity_domain = entity_id.split(".", 1)[0]
        if entity_domain == "script":
            if domain == "script" and service not in {"turn_on", "toggle", "turn_off", "reload"}:
                found.append((f"script.{service}", "turn_on"))
            elif service in _ACTIVATING_SERVICES:
                found.append((entity_id, "turn_on"))
        elif entity_domain == "scene":
            if service in _ACTIVATING_SERVICES:
                found.append((entity_id, "turn_on"))
        elif entity_domain == "automation":
            if service == "trigger":
                found.append((entity_id, "trigger"))
        elif member_lookup(entity_id) is not None:
            found.append((entity_id, service))
    return found


class GroupLookup(Protocol):
    def __call__(self, entity_id: str) -> tuple[str, ...] | None: ...


def build_plan_effects_from_sources(
    domain: str, service: str, target_ids: Sequence[str], sources: EffectSources
) -> PlanEffects | None:
    """Merged effect graph of one plan; ``None`` when nothing is composite."""
    composites = composite_targets(domain, service, target_ids, sources.group_members)
    if not composites:
        return None
    graphs = tuple(
        build_effect_graph(entity_id, sources, service=effective_service)
        for entity_id, effective_service in composites
    )
    return PlanEffects(tuple(entity_id for entity_id, _ in composites), graphs)


# ------------------------------------------------------------------ answers
def _join_names(names: Sequence[str], limit: int = 5) -> str:
    shown = list(dict.fromkeys(names))
    rest = len(shown) - limit
    shown = shown[:limit]
    if rest > 0:
        return ", ".join(shown) + f" und {rest} weitere"
    if len(shown) > 1:
        return ", ".join(shown[:-1]) + " und " + shown[-1]
    return shown[0] if shown else ""


_DOMAIN_VERB_PLURAL = {
    "button": ("drückt alle Buttons", "drückt"),
    "light": ("schaltet alle Lichter", "schaltet"),
    "switch": ("schaltet alle Schalter", "schaltet"),
    "cover": ("fährt alle Rollläden", "fährt"),
    "lock": ("schaltet alle Schlösser", "schaltet"),
}


def _root_phrase(root: str, names: Mapping[str, str]) -> str:
    domain = root.split(".", 1)[0]
    name = names.get(root, root)
    if domain == "script":
        return f"Das Skript „{name}“"
    if domain == "scene":
        return f"Die Szene „{name}“"
    if domain == "automation":
        return f"Die Automation „{name}“"
    return f"Die Gruppe „{name}“"


def _selector_phrase(selector: tuple[tuple[str, str], ...], names: Mapping[str, str]) -> str:
    parts: list[str] = []
    for key, item in selector:
        name = names.get(item, item)
        if key == "floor_id":
            parts.append(f"im {name}" if not name.lower().startswith(("im ", "in ")) else name)
        elif key == "area_id":
            parts.append(f"im Bereich {name}")
        elif key == "label_id":
            parts.append(f"mit dem Label {name}")
        elif key == "device_id":
            parts.append(f"des Geräts {name}")
    return _join_names(parts, limit=3)


def describe_unexposed(
    effects: PlanEffects,
    unexposed: Iterable[str],
    exposed_names: Iterable[str] = (),
    *,
    refused: bool = True,
) -> str:
    """User-facing denial: which foreign devices a routine would switch.

    A hidden entity that shares its name with an exposed one (a second
    entity of the same device, a group and its lamp) is named with its
    entity id, so the user sees which one is missing (7.8.1)."""
    names = effects.names
    blocked = set(unexposed)
    shared = {normalize_for_compare(name) for name in exposed_names}

    def label(entity_id: str) -> str:
        name = names.get(entity_id)
        if name is None:
            return entity_id
        return f"{name} ({entity_id})" if normalize_for_compare(name) in shared else name

    labels: list[str] = []
    broad_steps: list[str] = []
    for effect in effects.effects:
        hit = [entity_id for entity_id in effect.entity_ids if entity_id in blocked]
        if not hit:
            continue
        labels.extend(label(entity_id) for entity_id in hit)
        if effect.selector and effect.step_alias:
            verb = _DOMAIN_VERB_PLURAL.get(effect.domain, (f"wirkt auf alle Geräte ({effect.domain})", ""))[0]
            broad_steps.append(
                f"Der Schritt ‚{effect.step_alias}‘ {verb} {_selector_phrase(effect.selector, names)}."
            )
        elif effect.selector:
            verb = _DOMAIN_VERB_PLURAL.get(effect.domain, (f"wirkt auf alle Geräte ({effect.domain})", ""))[0]
            broad_steps.append(f"Ein Schritt {verb} {_selector_phrase(effect.selector, names)}.")
    root = effects.roots[0] if effects.roots else ""
    subject = _root_phrase(root, names) if root else "Diese Aktion"
    text = (
        f"{subject} schaltet auch Geräte, die für HomeIntent nicht freigegeben sind: "
        f"{_join_names(labels)}."
    )
    for sentence in dict.fromkeys(broad_steps):
        text += f" {sentence}"
    return text + " Ich habe nichts ausgeführt." if refused else text


def describe_unknown(effects: PlanEffects) -> str:
    step = effects.unknown[0]
    label = f"Schritt ‚{step.step_alias}‘" if step.step_alias else "Einen Schritt"
    if step.step_alias:
        return f"{label} kann ich nicht prüfen: {step.reason}"
    return f"{label} kann ich nicht prüfen: {step.reason}"


def describe_followups(effects: PlanEffects) -> str | None:
    followups = effects.possible_followups
    if not followups:
        return None
    names = [effects.names.get(item, item) for item in followups]
    return f"Kann Automation {_join_names(names, limit=3)} auslösen."


def summarize_effects(effects: PlanEffects) -> str | None:
    """Short success summary, e.g. „9 Rollläden“."""
    counts: dict[str, set[str]] = {}
    for effect in effects.effects:
        if effect.domain in {"script", "scene", "automation", "group"}:
            continue
        present = set(effect.entity_ids) - effects.missing
        if present:
            counts.setdefault(effect.domain, set()).update(present)
    if not counts:
        return None
    plural = {
        "light": ("Licht", "Lichter"), "cover": ("Rollladen", "Rollläden"),
        "switch": ("Schalter", "Schalter"), "lock": ("Schloss", "Schlösser"),
        "button": ("Button", "Buttons"), "climate": ("Heizung", "Heizungen"),
        "media_player": ("Medienplayer", "Medienplayer"), "fan": ("Ventilator", "Ventilatoren"),
        "vacuum": ("Saugroboter", "Saugroboter"),
    }
    parts = []
    for domain, ids in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0])):
        singular, many = plural.get(domain, ("Gerät", "Geräte"))
        parts.append(f"{len(ids)} {singular if len(ids) == 1 else many}")
    return _join_names(parts, limit=4)


# ------------------------------------------------------------ HA adapter
class HassEffectSources:
    """Reads the live Home Assistant configuration; never writes."""

    def __init__(self, hass: Any) -> None:
        self._hass = hass

    def _state(self, entity_id: str) -> Any:
        states = getattr(self._hass, "states", None)
        getter = getattr(states, "get", None)
        if getter is None:
            return None
        try:
            return getter(entity_id)
        except Exception:  # noqa: BLE001 - defensive read
            return None

    def _component_entity(self, domain: str, entity_id: str) -> Any:
        data = getattr(self._hass, "data", None)
        component = data.get(domain) if isinstance(data, dict) else None
        getter = getattr(component, "get_entity", None)
        if getter is None:
            return None
        try:
            return getter(entity_id)
        except Exception:  # noqa: BLE001 - defensive: treat as unreadable
            return None

    def script_sequence(self, entity_id: str) -> Sequence[Any] | None:
        entity = self._component_entity("script", entity_id)
        if entity is None:
            return None
        script = getattr(entity, "script", None)
        sequence = getattr(script, "sequence", None)
        if isinstance(sequence, (list, tuple)):
            return sequence
        raw = getattr(entity, "raw_config", None)
        if isinstance(raw, Mapping) and isinstance(raw.get("sequence"), (list, tuple)):
            return raw["sequence"]
        return None

    def automation_sequence(self, entity_id: str) -> Sequence[Any] | None:
        entity = self._component_entity("automation", entity_id)
        if entity is None:
            return None
        script = getattr(entity, "action_script", None)
        sequence = getattr(script, "sequence", None)
        if isinstance(sequence, (list, tuple)):
            return sequence
        raw = getattr(entity, "raw_config", None)
        if isinstance(raw, Mapping):
            for key in ("actions", "action"):
                if isinstance(raw.get(key), (list, tuple)):
                    return raw[key]
        return None

    def scene_states(self, entity_id: str) -> Mapping[str, Any] | None:
        entity = self._component_entity("scene", entity_id)
        config = getattr(entity, "scene_config", None)
        states = getattr(config, "states", None)
        if isinstance(states, Mapping):
            return states
        return None

    def group_members(self, entity_id: str) -> tuple[str, ...] | None:
        domain = entity_id.split(".", 1)[0]
        if domain in {"script", "scene", "automation"}:
            return None
        state = self._state(entity_id)
        members = getattr(state, "attributes", {}).get("entity_id") if state is not None else None
        if isinstance(members, (list, tuple)) and members:
            return tuple(str(member) for member in members)
        return None

    def resolve_target(self, selector: Mapping[str, Any]) -> frozenset[str] | None:
        try:
            from homeassistant.helpers import target as target_helper
        except ImportError:
            return None
        selection_class = getattr(target_helper, "TargetSelection", None) or getattr(
            target_helper, "TargetSelectorData", None
        )
        extract = getattr(target_helper, "async_extract_referenced_entity_ids", None)
        if selection_class is None or extract is None:
            return None
        try:
            selected = extract(self._hass, selection_class(dict(selector)), expand_group=False)
        except Exception:  # noqa: BLE001 - unresolvable counts as unknown
            return None
        return frozenset(selected.referenced | selected.indirectly_referenced)

    def resolve_registry_id(self, value: str) -> str | None:
        try:
            from homeassistant.helpers import entity_registry as er

            return er.async_resolve_entity_id(er.async_get(self._hass), value)
        except Exception:  # noqa: BLE001
            return None

    def entity_exists(self, entity_id: str) -> bool:
        return self._state(entity_id) is not None

    def entities_of_domain(self, domain: str) -> tuple[str, ...]:
        states = getattr(self._hass, "states", None)
        all_states = getattr(states, "async_all", None)
        if all_states is None:
            return ()
        return tuple(
            state.entity_id for state in all_states()
            if state.entity_id.split(".", 1)[0] == domain
        )

    def followups(self, entity_ids: frozenset[str]) -> tuple[str, ...]:
        try:
            from homeassistant.components.automation import automations_with_entity
        except ImportError:
            return ()
        found: list[str] = []
        for entity_id in sorted(entity_ids):
            try:
                found.extend(automations_with_entity(self._hass, entity_id))
            except Exception:  # noqa: BLE001 - a hint only
                continue
        return tuple(dict.fromkeys(found))[:20]

    def name(self, kind: str, item_id: str) -> str | None:
        if kind == "entity":
            state = self._state(item_id)
            if state is None:
                return None
            name = getattr(state, "attributes", {}).get("friendly_name")
            return str(name) if name else None
        try:
            if kind == "area":
                from homeassistant.helpers import area_registry as ar

                area = ar.async_get(self._hass).async_get_area(item_id)
                return getattr(area, "name", None)
            if kind == "floor":
                from homeassistant.helpers import floor_registry as fr

                floor = fr.async_get(self._hass).async_get_floor(item_id)
                return getattr(floor, "name", None)
            if kind == "label":
                from homeassistant.helpers import label_registry as lr

                label = lr.async_get(self._hass).async_get_label(item_id)
                return getattr(label, "name", None)
            if kind == "device":
                from homeassistant.helpers import device_registry as dr

                device = dr.async_get(self._hass).async_get(item_id)
                return getattr(device, "name_by_user", None) or getattr(device, "name", None)
        except Exception:  # noqa: BLE001
            return None
        return None


def build_plan_effects(hass: Any, plan: Any) -> PlanEffects | None:
    """Merged effect graph of a ``ServiceCallPlan`` (read-only, event loop).

    Runs synchronously so that every preview, the executor directly before
    the write, and the agent/proactive paths can all use it.
    """
    targets = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    sources = HassEffectSources(hass)
    effects = build_plan_effects_from_sources(plan.domain, plan.service, targets, sources)
    if effects is None:
        return None
    missing = frozenset(
        entity_id for entity_id in effects.referenced_targets if not sources.entity_exists(entity_id)
    )
    return replace(effects, missing=missing) if missing else effects
