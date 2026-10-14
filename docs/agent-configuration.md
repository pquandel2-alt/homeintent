# Agenten-Konfiguration

Alle Optionen liegen unter **Einstellungen → Geräte & Dienste → HomeIntent
→ Konfigurieren**. Bestehende Config Entries behalten ihre bisherigen
Proaktivregeln. Neue Einträge starten mit deaktiviertem Gedächtnis,
deaktivierter Routineerkennung, deaktiviertem AUTO und leerer AUTO-Allowlist.

- `memory_enabled`: aktiviert die lokale SQLite-Persistenz.
- `memory_retention_days`: maximale Standardaufbewahrung.
- `persona_style`: `neutral`, `precise` oder `jarvis`.
- `banter_level`: 0 bis 3; bei Sicherheitskategorien immer wirkungslos.
- `agent_enabled`: bestehende INFORM-/ASK-/AUTO-Ausgabe insgesamt.
- `agent_quiet_start` / `agent_quiet_end`: lokales TTS-Ruhefenster.
- `agent_event_categories`: kommagetrennte, ausdrücklich aktive
  Situationskategorien; erlaubt sind `safety`, `safety_alarm`,
  `device_unavailable`, `opening_while_away`, `window_heating`,
  `light_unoccupied`, `long_running_state`, `routine_anomaly` und
  `expected_effect_missing`. Leer
  bedeutet keine automatische State-Change-Ausgabe; unbekannte Werte werden
  abgelehnt.
- `agent_long_running_minutes`: globale Schwelle für
  `long_running_state` in ganzen Minuten (0 bis 10080). Standard `0`
  deaktiviert die Regel; die Kategorie muss zusätzlich aktiviert sein.
- `routine_detection_enabled`: klassische lokale Statistik.
- `routine_min_observations`: Gate vor einer Musterbewertung.
- `anomaly_threshold_percent`: seltenes Zeitfenster für eine Abweichung.
- `agent_delivery_channels`: erlaubte Push-/TTS-Kanäle.
- `control_user_ids`: Benutzer-Allowlist für Steuerung und Bestätigung.
- `read_only_entities` / `admin_only_entities`: unveränderte Policy-Grenzen.
- `agent_auto_enabled`: globaler AUTO-Kill-Switch.
- `agent_auto_entity_ids`: explizite Ziel-Allowlist; leer erlaubt nichts.
- `documents_enabled`: aktiviert den read-only FTS5-Index.
- `documents_directory`: relatives, freigegebenes Unterverzeichnis unterhalb
  des Home-Assistant-Konfigurationsordners; absolute Pfade und `..` werden
  abgelehnt.
- `frigate_enabled`: aktiviert ausschließlich strukturierte Frigate-Ereignis-
  und MQTT-Metadaten. Standard ist aus.
- `frigate_mqtt_topic`: konkretes Topic, standardmäßig `frigate/events`;
  Wildcards, leere und überlange Topics werden abgelehnt.
- `ha_sources_enabled`: übernimmt feldbegrenzt Zustandsänderungen vorhandener
  Kalender-, Wetter-, Anwesenheits-, Todo- und Timer-Entitäten als lokale
  Evidenz. Standard ist aus.
- `house_relations`: eine Beziehung pro Zeile als
  `source_id | relation | target_id`. Zulässige Typen stehen in der
  Hausgraph-Dokumentation; ungültige oder unbekannte stabile IDs werden nicht
  als Fakten übernommen.

Eine Optionsänderung lädt den Config Entry neu. Listener, Timer und
Conversation Agent werden dabei über Home Assistants Unload-Callbacks
entfernt und anschließend aus der neuen Konfiguration aufgebaut.
Ruhezeiten müssen als gültiges `HH:MM` angegeben werden. Die globale
Gedächtnisfrist setzt für neue Einträge ein Ablaufdatum und bereinigt beim
Setup auch ältere, migrationsübernommene Einträge kontrolliert.
