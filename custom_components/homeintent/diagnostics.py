"""Privacy-preserving, user-triggered Home Assistant diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AGENT_COOLDOWN_SECONDS,
    CONF_AGENT_DELIVERY_CHANNELS,
    CONF_AGENT_ENABLED,
    CONF_AGENT_MEDIA_PLAYERS,
    CONF_AGENT_NOTIFY_TARGETS,
    CONF_AGENT_TTS_ENTITY,
    CONF_TIMER_CHIME_MEDIA_ID,
    CONF_AGENT_AUTO_ENABLED,
    CONF_ANOMALY_THRESHOLD_PERCENT,
    CONF_BANTER_LEVEL,
    CONF_ADMIN_ONLY_ENTITIES,
    CONF_ALLOW_NON_ADMIN_AUTOMATIONS,
    CONF_ALLOW_NON_ADMIN_CRITICAL,
    CONF_CONFIRMATION_LEVEL,
    CONF_CONTEXT_TTL_SECONDS,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_RETENTION_DAYS,
    CONF_EXPERIENCE_LEARNING_ENABLED,
    CONF_PREDICTIVE_MODELS_ENABLED,
    CONF_HABIT_DISCOVERY_ENABLED,
    CONF_PROACTIVE_SUGGESTIONS_ENABLED,
    CONF_LEARNING_RETENTION_COUNT,
    CONF_MINIMUM_PREDICTION_CONFIDENCE,
    CONF_PERSONA_STYLE,
    CONF_ROUTINE_DETECTION_ENABLED,
    CONF_ROUTINE_MIN_OBSERVATIONS,
    CONF_CONTROL_USER_IDS,
    CONF_DOCUMENTS_ENABLED,
    CONF_HOUSE_RELATIONS,
    CONF_CUSTOM_ALIASES,
    CONF_MAX_ACTION_TARGETS,
    CONF_READ_ONLY_ENTITIES,
    CONF_SELECTED_ENTITIES,
    DOMAIN,
)
from .customization import parse_custom_aliases
from .house_graph import parse_relation_specs


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return configuration facts without utterances, states or entity IDs.

    Home Assistant only calls this function when the user explicitly
    downloads diagnostics. HomeIntent does not persist conversation text.
    """

    selected = entry.options.get(CONF_SELECTED_ENTITIES)
    read_only = entry.options.get(CONF_READ_ONLY_ENTITIES, [])
    admin_only = entry.options.get(CONF_ADMIN_ONLY_ENTITIES, [])
    control_users = entry.options.get(CONF_CONTROL_USER_IDS, [])
    try:
        alias_count = sum(
            len(aliases)
            for aliases in parse_custom_aliases(
                entry.options.get(CONF_CUSTOM_ALIASES)
            ).values()
        )
    except ValueError:
        alias_count = "invalid"
    relation_kinds: dict[str, int] = {}
    try:
        configured_relations = parse_relation_specs(
            entry.options.get(CONF_HOUSE_RELATIONS)
        )
        relation_count: int | str = len(configured_relations)
        for relation in configured_relations:
            relation_kinds[relation.kind.value] = (
                relation_kinds.get(relation.kind.value, 0) + 1
            )
    except ValueError:
        relation_count = "invalid"
    runtime_data = getattr(entry, "runtime_data", None)
    memory = getattr(runtime_data, "memory", None)
    memory_summary = (
        await memory.async_redacted_export()
        if memory is not None and getattr(memory, "enabled", False)
        else {
            "schema_version": None,
            "record_counts": {},
            "provenance_counts": {},
            "contains_transcripts": False,
            "person_ids_included": False,
            "content_included": False,
        }
    )
    routine_statistics = getattr(runtime_data, "routine_statistics", {})
    routine_series_count = (
        len(routine_statistics) if isinstance(routine_statistics, dict) else 0
    )
    routine_observation_count = (
        sum(item.observation_count for item in routine_statistics.values())
        if isinstance(routine_statistics, dict)
        else 0
    )
    goal_runs = getattr(runtime_data, "goal_runs", None)
    run_records = await goal_runs.async_list() if goal_runs is not None else ()
    monitor_goals = getattr(runtime_data, "monitor_goals", None)
    monitor_records = await monitor_goals.async_load() if monitor_goals is not None else ()
    experiences = getattr(runtime_data, "experiences", None)
    experience_records = await experiences.async_list() if experiences is not None else ()
    learned_models = getattr(runtime_data, "learned_models", None)
    learned_summary = (
        await learned_models.async_redacted_summary()
        if learned_models is not None else {"model_count": 0}
    )
    run_status_counts: dict[str, int] = {}
    verification_failure_count = 0
    for run in run_records:
        run_status_counts[run.status.value] = run_status_counts.get(run.status.value, 0) + 1
        verification_failure_count += sum(
            not verification.success
            for step in run.steps
            for verification in step.verification
        )
    shadow = getattr(runtime_data, "shadow", None)
    return {
        "shadow": shadow.summary() if shadow is not None else {},
        "domain": DOMAIN,
        "entry_version": entry.version,
        "selection_mode": "explicit" if selected is not None else "assist_exposure",
        "selected_entity_count": len(selected) if selected is not None else None,
        "read_only_entity_count": len(read_only) if isinstance(read_only, list) else 0,
        "admin_only_entity_count": (
            len(admin_only) if isinstance(admin_only, list) else 0
        ),
        "control_user_count": (
            len(control_users) if isinstance(control_users, list) else 0
        ),
        "custom_alias_count": alias_count,
        "confirmation_level": entry.options.get(CONF_CONFIRMATION_LEVEL, "high"),
        "max_action_targets": entry.options.get(CONF_MAX_ACTION_TARGETS, 50),
        "allow_non_admin_critical": entry.options.get(
            CONF_ALLOW_NON_ADMIN_CRITICAL, False
        ),
        "allow_non_admin_automations": entry.options.get(
            CONF_ALLOW_NON_ADMIN_AUTOMATIONS, True
        ),
        "context_ttl_seconds": entry.options.get(CONF_CONTEXT_TTL_SECONDS, 30),
        "proactive_agent_enabled": entry.options.get(CONF_AGENT_ENABLED, True),
        "agent_delivery_channels": entry.options.get(
            CONF_AGENT_DELIVERY_CHANNELS, ["push"]
        ),
        "agent_notify_target_count": len(
            entry.options.get(CONF_AGENT_NOTIFY_TARGETS, [])
        ),
        "agent_tts_configured": bool(entry.options.get(CONF_AGENT_TTS_ENTITY)),
        "agent_media_player_count": len(
            entry.options.get(CONF_AGENT_MEDIA_PLAYERS, [])
        ),
        "timer_chime_configured": bool(
            entry.options.get(CONF_TIMER_CHIME_MEDIA_ID)
        ),
        "agent_cooldown_seconds": entry.options.get(
            CONF_AGENT_COOLDOWN_SECONDS, 1800
        ),
        "memory_enabled": entry.options.get(CONF_MEMORY_ENABLED, False),
        "memory_retention_days": entry.options.get(CONF_MEMORY_RETENTION_DAYS, 90),
        "experience_learning_enabled": entry.options.get(
            CONF_EXPERIENCE_LEARNING_ENABLED, False
        ),
        "predictive_models_enabled": entry.options.get(
            CONF_PREDICTIVE_MODELS_ENABLED, False
        ),
        "habit_discovery_enabled": entry.options.get(
            CONF_HABIT_DISCOVERY_ENABLED, False
        ),
        "proactive_suggestions_enabled": entry.options.get(
            CONF_PROACTIVE_SUGGESTIONS_ENABLED, False
        ),
        "learning_retention_count": entry.options.get(
            CONF_LEARNING_RETENTION_COUNT, 5000
        ),
        "minimum_prediction_confidence": entry.options.get(
            CONF_MINIMUM_PREDICTION_CONFIDENCE, 0.75
        ),
        "experience_count": len(experience_records),
        "learned_models": learned_summary,
        "persona_style": entry.options.get(CONF_PERSONA_STYLE, "neutral"),
        "banter_level": entry.options.get(CONF_BANTER_LEVEL, 0),
        "routine_detection_enabled": entry.options.get(
            CONF_ROUTINE_DETECTION_ENABLED, False
        ),
        "routine_min_observations": entry.options.get(
            CONF_ROUTINE_MIN_OBSERVATIONS, 10
        ),
        "anomaly_threshold_percent": entry.options.get(
            CONF_ANOMALY_THRESHOLD_PERCENT, 5
        ),
        "agent_auto_enabled": entry.options.get(CONF_AGENT_AUTO_ENABLED, False),
        "documents_enabled": entry.options.get(CONF_DOCUMENTS_ENABLED, False),
        "house_relation_count": relation_count,
        "house_relation_kind_counts": relation_kinds,
        "learned_memory": memory_summary,
        "routine_series_count": routine_series_count,
        "routine_observation_count": routine_observation_count,
        "conversation_text_stored": False,
        "world_model_persisted": False,
        "goal_run_count": len(run_records),
        "goal_run_status_counts": run_status_counts,
        "monitor_goal_count": len(monitor_records),
        "verification_failure_count": verification_failure_count,
        "goal_ids_included": False,
        "notification_content_included": False,
        "person_history_included": False,
    }
