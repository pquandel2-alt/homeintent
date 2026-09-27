"""Automation summary: a read-only, hass-free view of one entry in
``automations.yaml`` plus its sidecar metadata (HomeIntent V5 Teil 9/10,
V5.29 "Automation Query").

Mirrors ``devices.DeviceSnapshot``: a plain, frozen dataclass populated once
per conversation turn from live HA data (see ``automation_executor.py``'s
``async_list_automations()``), never a persisted second database. Deliberately
flat and hass/YAML-shape-free - the ``nlu/`` query pipeline (``QueryExecutor``/
``ResponseGenerator``) never needs to know that ``automations.yaml``/the
metadata sidecar even exist, same layering precedent ``EntitySnapshot``/
``DeviceSnapshot`` already set for the entity/device registries.

Entity references are retained both as a union and separated by trigger,
condition and action. This lets management queries distinguish "what reacts
to X?" from "what controls X?" without reverse-engineering arbitrary YAML
into a second automation AST.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CREATED_BY_HOMEINTENT = "homeintent"


@dataclass(frozen=True)
class AutomationSummary:
    """Hass-free view of one ``automations.yaml`` entry.

    ``alias`` is always present (Home Assistant assigns automations created
    through its own UI editor a default alias too, and ``AutomationExecutor``
    always writes one - see ``ha_automation_generator.py``). ``source_text``/
    ``created_by`` are ``None`` for automations this integration didn't
    create (no sidecar metadata entry exists for them) - the plan's own
    "created_by tells HomeIntent-made apart from hand-made" distinction
    (see ``automation_metadata_store.py``'s ``CREATED_BY_HOMEINTENT`` docstring).
    """

    automation_id: str
    alias: str
    source_text: str | None = None
    created_by: str | None = None
    referenced_entity_ids: frozenset[str] = frozenset()
    trigger_entity_ids: frozenset[str] = frozenset()
    condition_entity_ids: frozenset[str] = frozenset()
    action_entity_ids: frozenset[str] = frozenset()
    # HomeIntent V5 Teil 8/10 (Wave 11, "Automation Disable/Enable"): mirrors
    # the automation's own ``initial_state`` YAML key (defaults to enabled
    # when the key is absent, same default Home Assistant's own automation
    # config schema uses - see ``AutomationExecutor``'s ``async_disable_automation()``
    # docstring for why this key, not ``automation.turn_on``/``turn_off``,
    # is this integration's actual enable/disable mechanism).
    enabled: bool = True
    scheduled_for: str | None = None
    once: bool = False
    max_runs: int | None = None
    run_count: int = 0
    triggers: tuple[Mapping[str, Any], ...] = ()
    conditions: tuple[Mapping[str, Any], ...] = ()
    actions: tuple[Mapping[str, Any], ...] = ()


def automations_named(
    spoken_name: str, automations: tuple["AutomationSummary", ...]
) -> tuple["AutomationSummary", ...]:
    """Automations whose alias *is* the spoken name ("Flurlicht bei Bewegung").

    Only an exact (normalized) alias match counts: a name that merely starts
    with a device name must never be mistaken for that device (F9).
    """
    from .entities import normalize_for_compare

    wanted = " ".join(normalize_for_compare(spoken_name).split())
    if not wanted:
        return ()
    return tuple(
        automation for automation in automations
        if " ".join(normalize_for_compare(automation.alias).split()) == wanted
    )
