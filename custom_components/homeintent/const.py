"""Shared constants for the homeintent custom component."""

from __future__ import annotations

DOMAIN = "homeintent"

CONF_SELECTED_ENTITIES = "selected_entities"
CONF_READ_ONLY_ENTITIES = "read_only_entities"
CONF_CONFIRMATION_LEVEL = "confirmation_level"
CONF_MAX_ACTION_TARGETS = "max_action_targets"
# What to do when a script/scene/group contains a step whose effect cannot
# be determined statically: "deny" (default) or "confirm".
CONF_EFFECT_GRAPH_UNKNOWN = "effect_graph_unknown"
# 7.8.1: a named, exposed script/scene/group whose steps switch devices
# that are not exposed (allow: run, confirm: ask first, deny: refuse).
CONF_ROUTINE_UNEXPOSED = "routine_unexposed_effects"
ROUTINE_UNEXPOSED_MODES = ("allow", "confirm", "deny")
DEFAULT_ROUTINE_UNEXPOSED = "allow"
EFFECT_GRAPH_UNKNOWN_OPTIONS = ("deny", "confirm")
# 7.3.2 execution trace (ring buffer of executions, see execution_trace.py).
CONF_TRACE_LIMIT = "trace_limit"
CONF_TRACE_DAYS = "trace_days"
CONF_TRACE_STORE_TEXT = "trace_store_text"
# 7.3.3 what HomeIntent may do with implicit needs and inferred routines.
CONF_IMPLICIT_ACTION_LEVEL = "implicit_action_level"
IMPLICIT_ACTION_LEVELS = (
    "understand_only", "propose", "low_risk_auto", "bound_routines_auto",
)
DEFAULT_IMPLICIT_ACTION_LEVEL = "propose"
CONF_ALLOW_NON_ADMIN_CRITICAL = "allow_non_admin_critical"
CONF_ALLOW_NON_ADMIN_AUTOMATIONS = "allow_non_admin_automations"
# 7.9.1 Teil B: "spoken" answers every turn aloud (default); "tone" plays a
# short confirmation sound instead when everything said was done.
CONF_RESPONSE_STYLE = "response_style"
RESPONSE_STYLE_SPOKEN = "spoken"
RESPONSE_STYLE_TONE = "tone"
RESPONSE_STYLES = (RESPONSE_STYLE_SPOKEN, RESPONSE_STYLE_TONE)
# 7.9.3 B5: new installations get "tone" (written into their options by
# the config flow).  An entry created before 7.9.3 without a stored value
# keeps the behaviour it had: a missing key still means "spoken".
DEFAULT_RESPONSE_STYLE = RESPONSE_STYLE_SPOKEN
NEW_INSTALL_RESPONSE_STYLE = RESPONSE_STYLE_TONE
# Optional own sound (media-source:// or a local /local/... path); empty:
# the integration's own confirm.mp3.
CONF_CONFIRMATION_MEDIA_ID = "confirmation_media_id"
# 7.9.3 B5: the second sound for "everything ran, but a device has not
# reported back"; empty: the integration's own notice.mp3.  With
# ``notice_says_name`` the tone is followed by a very short announcement
# ("Stehlampe meldet sich nicht.").
CONF_NOTICE_MEDIA_ID = "notice_media_id"
CONF_NOTICE_SAYS_NAME = "notice_says_name"
# 7.9.2 A1: bounded wait for a device's state report after a write.
CONF_EFFECT_WAIT_SECONDS = "effect_wait_seconds"
DEFAULT_EFFECT_WAIT_SECONDS = 2.0
# 7.9.2 A3: voice devices without a signed-in user speak for the household
# (manage shared monitors/automations only). Off by default.
CONF_HOUSEHOLD_VOICE_DEVICES = "household_voice_devices"
# 7.9.3 B2: zones of other people for non-administrators (default: only
# "zuhause"/"unterwegs"); never coordinates.
CONF_SHARE_HOUSEHOLD_LOCATION = "share_household_location"
CONF_CONTEXT_TTL_SECONDS = "context_ttl_seconds"
CONF_CUSTOM_ALIASES = "custom_aliases"
CONF_CONTROL_USER_IDS = "control_user_ids"
CONF_ADMIN_ONLY_ENTITIES = "admin_only_entities"
CONF_AGENT_ENABLED = "agent_enabled"
CONF_AGENT_DELIVERY_CHANNELS = "agent_delivery_channels"
CONF_AGENT_NOTIFY_TARGETS = "agent_notify_targets"
CONF_AGENT_TTS_ENTITY = "agent_tts_entity"
CONF_AGENT_MEDIA_PLAYERS = "agent_media_players"
CONF_TIMER_CHIME_MEDIA_ID = "timer_chime_media_id"
CONF_AGENT_COOLDOWN_SECONDS = "agent_cooldown_seconds"
CONF_MEMORY_ENABLED = "memory_enabled"
CONF_MEMORY_RETENTION_DAYS = "memory_retention_days"
CONF_EXPERIENCE_LEARNING_ENABLED = "experience_learning_enabled"
CONF_PREDICTIVE_MODELS_ENABLED = "predictive_models_enabled"
CONF_HABIT_DISCOVERY_ENABLED = "habit_discovery_enabled"
CONF_PROACTIVE_SUGGESTIONS_ENABLED = "proactive_suggestions_enabled"
CONF_LEARNING_RETENTION_COUNT = "learning_retention_count"
CONF_MINIMUM_PREDICTION_CONFIDENCE = "minimum_prediction_confidence"
CONF_PERSONA_STYLE = "persona_style"
CONF_BANTER_LEVEL = "banter_level"
CONF_AGENT_QUIET_START = "agent_quiet_start"
CONF_AGENT_QUIET_END = "agent_quiet_end"
CONF_AGENT_EVENT_CATEGORIES = "agent_event_categories"
CONF_ROUTINE_DETECTION_ENABLED = "routine_detection_enabled"
CONF_ROUTINE_MIN_OBSERVATIONS = "routine_min_observations"
CONF_ANOMALY_THRESHOLD_PERCENT = "anomaly_threshold_percent"
CONF_AGENT_AUTO_ENABLED = "agent_auto_enabled"
CONF_AGENT_AUTO_ENTITY_IDS = "agent_auto_entity_ids"
CONF_DOCUMENTS_ENABLED = "documents_enabled"
CONF_DOCUMENTS_DIRECTORY = "documents_directory"
CONF_HOUSE_RELATIONS = "house_relations"
CONF_FRIGATE_ENABLED = "frigate_enabled"
CONF_FRIGATE_MQTT_TOPIC = "frigate_mqtt_topic"
CONF_HA_SOURCES_ENABLED = "ha_sources_enabled"
# V12 proactive context intelligence (all conservative / opt-in by default).
CONF_PROACTIVE_CONTEXT_ENABLED = "proactive_context_enabled"
CONF_VOICE_PROACTIVE_ENABLED = "voice_proactive_enabled"
CONF_PUSH_PROACTIVE_ENABLED = "push_proactive_enabled"
CONF_ROOM_AWARE_VOICE_ENABLED = "room_aware_voice_enabled"
CONF_ATTENTION_BUDGET_ENABLED = "attention_budget_enabled"
CONF_QUIET_HOURS_ENABLED = "quiet_hours_enabled"
CONF_STANDING_PERMISSIONS_ENABLED = "standing_permissions_enabled"
CONF_CRITICAL_MULTI_CHANNEL_ENABLED = "critical_multi_channel_enabled"
CONF_PROACTIVE_ENTRY_OPEN_MINUTES = "proactive_entry_open_minutes"
CONF_PROACTIVE_APPLIANCE_ENTITIES = "proactive_appliance_entities"
CONF_PROACTIVE_PERSON_ROOM_SENSORS = "proactive_person_room_sensors"
CONF_PROACTIVE_SATELLITE_AREAS = "proactive_satellite_areas"
CONF_PROACTIVE_USER_QUIET_HOURS = "proactive_user_quiet_hours"

AGENT_CHANNEL_PUSH = "push"
AGENT_CHANNEL_TTS = "tts"
DEFAULT_AGENT_COOLDOWN_SECONDS = 1800

# Domains the intent set can act on or (for "sensor"/"binary_sensor") read
# state from. Mirrors service_call.py's IntentSpec/QueryIntentSpec.allowed_domains.
# "binary_sensor" (V4.2, Semantic Query Engine) is exposed as a full domain,
# not filtered to window/door device classes - it becomes selectable in
# config_flow.py's entity picker like any other domain, same as "sensor".
# "script" (Skript-Aktivierung, 2026-08-20): scripts were never selectable at
# all before this, so build_entity_snapshots() silently produced zero
# script.* entities - the root cause "aktiviere Skript X" never worked, not
# a missing intent/parser (see service_call.py's HassRunScript).
SELECTABLE_DOMAINS = (
    "light", "switch", "fan", "cover", "sensor", "binary_sensor", "script",
    "climate", "media_player", "vacuum", "scene", "lock", "calendar",
    "humidifier", "water_heater", "select", "number", "input_number",
    "input_boolean", "button", "valve", "lawn_mower", "camera", "notify",
    "todo", "timer", "alarm_control_panel", "group", "person", "weather",
    "sun",
)

NOT_UNDERSTOOD_TEXT = "Das habe ich nicht verstanden."
