"""Setup-owned storage paths with lossless legacy adoption.

Path migration is deliberately synchronous: ``async_setup_entry`` runs the
whole resolver once in Home Assistant's executor and every request-time
collaborator receives the resulting immutable strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from .storage_migration import HassWithConfigPath, resolve_storage_path

STORAGE_PATHS_DATA_KEY = "homeintent_resolved_storage_paths"


class _HassWithConfig(HassWithConfigPath, Protocol):
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class HomeIntentStoragePaths:
    """Every persistent path owned or consumed by one HomeIntent entry."""

    memory: str
    users: str
    profiles: str
    goal_runs: str
    monitor_goals: str
    experiences: str
    models: str
    thermal_cycles: str
    thermal_checkpoints: str
    trace: str
    bindings: str
    documents: str
    proactive: str
    automation_metadata: str
    agent_events: str
    automation_transaction: str
    automation_history: str
    music_aliases: str
    reports: str
    vacation: str
    habit_suggestions: str
    automations_yaml: str

    @classmethod
    def unresolved(cls, hass: _HassWithConfig) -> "HomeIntentStoragePaths":
        """Build canonical strings without inspecting the filesystem.

        This is a safe compatibility fallback for isolated unit construction.
        Productive setup always installs fully migrated paths first.
        """

        path = hass.config.path

        def storage(name: str) -> str:
            return path(".storage", name)
        return cls(
            memory=storage("homeintent_memory.sqlite3"),
            users=storage("homeintent_users.json"),
            profiles=storage("homeintent_profiles.json"),
            goal_runs=storage("homeintent_goal_runs.json"),
            monitor_goals=storage("homeintent_monitor_goals.json"),
            experiences=storage("homeintent_experiences.json"),
            models=storage("homeintent_models.json"),
            thermal_cycles=storage("homeintent_thermal_cycles.json"),
            thermal_checkpoints=storage("homeintent_thermal_checkpoints.json"),
            trace=storage("homeintent_trace.json"),
            bindings=storage("homeintent_bindings.json"),
            documents=storage("homeintent_documents.sqlite3"),
            proactive=storage("homeintent_proactive.json"),
            automation_metadata=path("homeintent_automation_metadata.json"),
            agent_events=path("homeintent_agent_events.json"),
            automation_transaction=path("homeintent_automation_transaction.json"),
            automation_history=storage("homeintent_automation_history.json"),
            music_aliases=storage("homeintent_music_aliases.json"),
            reports=path("homeintent_reports.json"),
            vacation=path("homeintent_vacation.json"),
            habit_suggestions=path("homeintent_habit_suggestions.json"),
            automations_yaml=path("automations.yaml"),
        )


def resolve_homeintent_storage_paths(
    hass: _HassWithConfig,
) -> HomeIntentStoragePaths:
    """Resolve and migrate all legacy pairs in one executor operation."""

    path = hass.config.path

    def storage(name: str) -> str:
        return path(".storage", name)

    def migrated(current: str, legacy: str) -> str:
        return resolve_storage_path(
            hass, f".storage/{current}", f".storage/{legacy}"
        )

    return HomeIntentStoragePaths(
        memory=migrated("homeintent_memory.sqlite3", "ha_nlu_memory.sqlite3"),
        users=migrated("homeintent_users.json", "ha_nlu_users.json"),
        profiles=migrated("homeintent_profiles.json", "ha_nlu_profiles.json"),
        goal_runs=migrated("homeintent_goal_runs.json", "ha_nlu_goal_runs.json"),
        monitor_goals=migrated(
            "homeintent_monitor_goals.json", "ha_nlu_monitor_goals.json"
        ),
        experiences=migrated(
            "homeintent_experiences.json", "ha_nlu_experiences.json"
        ),
        models=migrated("homeintent_models.json", "ha_nlu_models.json"),
        thermal_cycles=migrated(
            "homeintent_thermal_cycles.json", "ha_nlu_thermal_cycles.json"
        ),
        thermal_checkpoints=migrated(
            "homeintent_thermal_checkpoints.json",
            "ha_nlu_thermal_checkpoints.json",
        ),
        trace=storage("homeintent_trace.json"),
        bindings=migrated("homeintent_bindings.json", "ha_nlu_bindings.json"),
        documents=migrated(
            "homeintent_documents.sqlite3", "ha_nlu_documents.sqlite3"
        ),
        proactive=migrated("homeintent_proactive.json", "ha_nlu_proactive.json"),
        automation_metadata=resolve_storage_path(
            hass,
            "homeintent_automation_metadata.json",
            "ha_nlu_automation_metadata.json",
        ),
        agent_events=resolve_storage_path(
            hass, "homeintent_agent_events.json", "ha_nlu_agent_events.json"
        ),
        automation_transaction=resolve_storage_path(
            hass,
            "homeintent_automation_transaction.json",
            "ha_nlu_automation_transaction.json",
        ),
        automation_history=resolve_storage_path(
            hass,
            ".storage/homeintent_automation_history.json",
            ".storage/ha_nlu_automation_history.json",
        ),
        music_aliases=storage("homeintent_music_aliases.json"),
        reports=path("homeintent_reports.json"),
        vacation=path("homeintent_vacation.json"),
        habit_suggestions=path("homeintent_habit_suggestions.json"),
        automations_yaml=path("automations.yaml"),
    )


def storage_paths_for_hass(hass: _HassWithConfig) -> HomeIntentStoragePaths:
    """Return setup-owned paths, with an I/O-free unit-test fallback."""

    value = hass.data.get(STORAGE_PATHS_DATA_KEY)
    if isinstance(value, HomeIntentStoragePaths):
        return value
    return HomeIntentStoragePaths.unresolved(hass)


def install_storage_paths(
    hass: _HassWithConfig, paths: HomeIntentStoragePaths
) -> None:
    """Expose the single-entry paths to globally registered services."""

    hass.data[STORAGE_PATHS_DATA_KEY] = cast(object, paths)


__all__ = (
    "HomeIntentStoragePaths",
    "STORAGE_PATHS_DATA_KEY",
    "install_storage_paths",
    "resolve_homeintent_storage_paths",
    "storage_paths_for_hass",
)
