/*
 * HomeIntent 7.1 Learning Center — local Home Assistant custom panel.
 *
 * Transparency + control over existing V11/V12 authorities.  This file is
 * served by the integration itself; it loads nothing from the network and
 * talks only to the authenticated Home Assistant WebSocket connection
 * (`hass.connection`).  All data is filtered server-side; the browser never
 * sends a user id and never selects a service.
 *
 * Rendering never assigns HTML markup: every model/entity/user supplied string is
 * inserted as a text node.
 */

const API_VERSION = 1;
const PREFIX = "homeintent/learning_center";
const PANEL_PATH = "/homeintent";

/**
 * @typedef {{key: string, value: (number|string), unit: ("count"|"ratio"|"seconds"|"celsius"|"datetime"|"text")}} ModelFact
 * @typedef {{ref: string, kind: string, category: string, visibility: ("personal"|"household"|"system"),
 *   owned_by_viewer: boolean, owner_label: (string|null), redacted: boolean, subject_label: string,
 *   subject_missing: boolean, area_label: (string|null), action_key: (string|null), status: string,
 *   health: string, knowledge_state: string, sample_count: number, headline: (ModelFact|null),
 *   last_observed: string, needs_attention: boolean, actions: string[]}} ModelListItem
 * @typedef {ModelListItem & {quality_band: string, first_observed: string, expires_at: (string|null),
 *   confirmed_by_label: (string|null), confirmed_by_viewer: boolean, invalidation_reason: (string|null),
 *   facts: ModelFact[], habit_steps: {entity_label: string, entity_missing: boolean, action_key: string, expected: string}[],
 *   preference_choices: {entity_label: string, entity_missing: boolean, count: number, preferred: boolean}[],
 *   evidence_available: boolean, technical: Object}} ModelDetail
 * @typedef {{ref: string, basis: string, facts: ModelFact[], rows: {timestamp: string, entity_label: string,
 *   action_key: string, evidence_state: string, latency_seconds: (number|null)}[], provenance_count: number,
 *   truncated: boolean}} Evidence
 * @typedef {{id: string, kind: string, severity: ("info"|"action"|"warning"), subject_label: string,
 *   model_ref: (string|null), permission_id: (string|null), action_available: boolean, visibility: string}} AttentionItem
 * @typedef {{api_version: number, entry_id: string, revision: number, is_admin: boolean, total_models: number,
 *   valid_models: number, learning_models: number, stale_models: number, invalid_models: number,
 *   needs_attention: number, category_counts: Object<string, number>, standing_permission_count: number,
 *   mute_count: number, recent_activity_count: number, features: Object, attention: AttentionItem[],
 *   non_mutable_situations: string[]}} Summary
 * @typedef {{permission_id: string, owner_label: string, owned_by_viewer: boolean, description: string,
 *   situation_kind: string, operator: string, entities: {label: string, missing: boolean}[],
 *   area_label: (string|null), conditions: string[], created_at: string, expires_at: string, revoked: boolean,
 *   expired: boolean, attempts_today: number, verified_today: number, max_per_day: number, can_revoke: boolean}} Permission
 * @typedef {{situation_kind: string, confirmed_at: string, can_remove: boolean}} Mute
 * @typedef {{record_id: string, timestamp: string, situation_kind: string, subject_label: string, decision: string,
 *   channel: string, result: string, reasons: string[], recipient_is_viewer: boolean,
 *   recipient_label: (string|null), acknowledgement: (string|null), visibility: string}} HistoryRecord
 */

// ---------------------------------------------------------------- i18n

const STRINGS = {
  de: {
    title: "HomeIntent",
    subtitle: "Wissen & Autonomie",
    menu: "Menü",
    tabs: { overview: "Übersicht", knowledge: "Wissen", autonomy: "Autonomie", activity: "Aktivität" },
    loading: "Wird geladen …",
    unavailable: "Learning Center ist derzeit nicht erreichbar.",
    retry: "Erneut versuchen",
    incompatible: "Diese Version des Learning Centers passt nicht zum Backend. Bitte die Seite neu laden.",
    entry: "HomeIntent-Instanz",
    models: (n) => (n === 1 ? "1 gelerntes Modell" : `${n} gelernte Modelle`),
    modelCount: (n) => (n === 1 ? "1 Modell" : `${n} Modelle`),
    observations: (n) => (n === 1 ? "1 Beobachtung" : `${n} Beobachtungen`),
    validCount: (n) => `${n} gültig`,
    learningCount: (n) => (n === 1 ? "1 lernt noch" : `${n} lernen noch`),
    checkCount: (n) => (n === 1 ? "1 Sache prüfen" : `${n} Dinge prüfen`),
    permissionsCount: (n) => (n === 1 ? "1 Daueranweisung" : `${n} Daueranweisungen`),
    mutesCount: (n) => (n === 1 ? "1 stummgeschalteter Hinweis" : `${n} stummgeschaltete Hinweise`),
    activityCount: (n) => (n === 1 ? "1 Ereignis in 24 Stunden" : `${n} Ereignisse in 24 Stunden`),
    attention: "Aufmerksamkeit",
    nothingToCheck: "Gerade gibt es nichts zu prüfen.",
    autonomy: "Autonomie",
    communication: "Kommunikation",
    activity: "Aktivität",
    knowledge: "Wissen",
    open: "Öffnen",
    details: "Details",
    back: "Zurück",
    search: "Suchen",
    searchPlaceholder: "Name, Raum oder Art suchen",
    filterCategory: "Art",
    filterStatus: "Status",
    all: "Alle",
    categories: { habits: "Gewohnheiten", preferences: "Präferenzen", heating: "Heizung", devices: "Geräte", other: "Sonstiges" },
    statusFilter: { valid: "✓ Gültig", learning: "? Lernt noch", confirmed: "Bestätigt", inferred: "Vermutet", stale: "Veraltet", check: "⚠ Prüfen" },
    status: {
      valid: "Gültig", confirmed: "Bestätigt", inferred: "Vermutet", learning: "Lernt noch", stale: "Veraltet",
      drift: "Verhalten geändert", unreliable: "Unzuverlässig", invalid: "Ungültig", unknown: "Unbekannter Zustand",
    },
    knowledgeState: { observed: "Beobachtet", inferred: "Vermutet – noch nicht bestätigt", confirmed: "Von dir bestätigt" },
    confirmedBy: (name) => `Bestätigt von ${name}`,
    kind: {
      reliability: "Gerätezuverlässigkeit", effect_timing: "Reaktionszeit", thermal_model: "Heizverhalten",
      preference: "Präferenz", habit: "Gewohnheit", fact: "Fakt", duration: "Dauer", energy: "Energie",
      battery_trend: "Batterietrend",
    },
    band: { morning: "Morgenroutine", day: "Tagesablauf", evening: "Abendroutine", night: "Nachtroutine" },
    bandText: { morning: "morgens", day: "tagsüber", evening: "abends", night: "nachts" },
    personalPreference: "Persönliche Präferenz",
    personalOf: (name) => `Persönlich · ${name}`,
    action: {
      turn_on: "einschalten", turn_off: "ausschalten", toggle: "umschalten", open_cover: "öffnen",
      close_cover: "schließen", stop_cover: "anhalten", set_cover_position: "Position setzen",
      set_temperature: "Temperatur setzen", set_hvac_mode: "Modus setzen", set_percentage: "Stufe setzen",
      select_option: "Option wählen", set_value: "Wert setzen", volume_set: "Lautstärke setzen",
      media_play: "abspielen", media_pause: "pausieren", return_to_base: "zur Station", lock: "verriegeln",
      unlock: "entriegeln", open: "öffnen", close: "schließen", press: "drücken", start: "starten", other: "Aktion",
    },
    entityMissing: "Entität nicht mehr vorhanden",
    visibility: {
      personal_own: "Nur du kannst diese persönlichen Daten sehen.",
      personal_admin: "Persönliches Modell eines anderen Benutzers. Als Administrator kannst du es entfernen, aber nicht bestätigen. Inhalte werden nicht angezeigt.",
      household: "Dieses Modell beschreibt ein Gerät oder einen Raum im Haushalt und ist für Haushaltsmitglieder sichtbar.",
      system: "Systemwissen – nur für Administratoren sichtbar.",
    },
    purposeTitle: "Wofür wird das verwendet?",
    purpose: {
      thermal_model: "HomeIntent verwendet dieses Modell, um abzuschätzen, wann ein Temperaturziel gestartet werden sollte und ob ein Ziel verspätet ist.",
      reliability: "HomeIntent verwendet diese Statistik, um die Zuverlässigkeit beobachteter Gerätewirkungen zu beurteilen. Sie ist keine Ausführungserlaubnis.",
      effect_timing: "HomeIntent verwendet dieses Modell, um die Wartezeit auf eine Gerätewirkung anzupassen und ungewöhnlich lange Gerätereaktionen zu erkennen. Die Werte sind beobachtete Reaktionszeiten, keine Garantie.",
      habit: "Diese Beobachtung kann als Routine vorgeschlagen werden. HomeIntent führt diese Gewohnheit nicht automatisch aus.",
      preference: "Eine bestätigte Präferenz hilft bei mehrdeutigen Namen – nur für dich und nur in ihrem Gültigkeitsbereich. Eine vermutete Präferenz wird nicht verwendet, bis du sie bestätigst.",
      other: "Dieses Wissen wird nur in seinem gespeicherten Gültigkeitsbereich verwendet.",
    },
    facts: {
      observed_actions: "Angenommene, verifizierte Aktionen", verified_successes: "Wirkungen bestätigt",
      verified_failures: "Wirkungen ausgeblieben", success_rate: "Beobachtete Erfolgsrate",
      observations: "Beobachtungen", median_seconds: "Median", p90_seconds: "P90", p95_seconds: "P95",
      mad_seconds: "Streuung (MAD)", heating_cycles: "Abgeschlossene Heizzyklen",
      holdout_mae_seconds: "Validierungsfehler (MAE)", holdout_median_absolute_error_seconds: "Validierung Median-Fehler",
      holdout_p90_absolute_error_seconds: "Validierung P90-Fehler", validation_sample_count: "Validierungszyklen",
      min_start_temperature: "Trainierte Starttemperatur ab", max_start_temperature: "Trainierte Starttemperatur bis",
      min_temperature_delta: "Temperaturdifferenz ab", max_temperature_delta: "Temperaturdifferenz bis",
      min_outdoor_gap: "Außendifferenz ab", max_outdoor_gap: "Außendifferenz bis",
      occurrences: "Belege", opportunities: "Vergleichbare Gelegenheiten", support: "Support",
      time_band: "Zeitraum", suggestion_status: "Vorschlag", selections: "Beobachtete Auswahlen",
      support_count: "Davon bevorzugte Auswahl", preferred_entity: "Bevorzugt", samples: "Belege",
      training_mae_seconds: "Trainingsfehler (MAE)", residual_mad_seconds: "Residuen-Streuung",
    },
    suggestion: { new: "Vorschlag möglich", shown: "Vorgeschlagen", accepted: "Übernommen", rejected: "Verworfen", snoozed: "Zurückgestellt", expired: "Abgelaufen" },
    quality: { very_low: "Sehr niedrig", low: "Niedrig", medium: "Mittel", high: "Hoch", unknown: "Unbekannt" },
    qualityLabel: "Qualität",
    qualityHint: "Die Qualität ist eine Bewertung der Datenlage, keine Wahrscheinlichkeit.",
    learnedSince: "Gelernt seit",
    lastEvidence: "Letzter Beleg",
    expiresAt: "Gültig bis",
    invalidation: "Grund der Ungültigkeit",
    driftText: "Das Verhalten hat sich gegenüber dem gelernten Modell verändert. HomeIntent verwendet es nicht für Planungen, bis genug neue Belege vorliegen.",
    staleText: "Die letzten Belege sind älter als die Gültigkeitsgrenze. Das Modell wird nicht mehr für Vorhersagen verwendet.",
    invalidText: "Das Modell ist ungültig und wird nicht verwendet.",
    unreliableText: "Die gemessene Abweichung ist zu groß. Das Modell wird nicht für Planungen verwendet.",
    learningText: "Noch nicht genug Belege. Das ist normal – HomeIntent lernt weiter.",
    sequence: "Beobachtete Abfolge",
    choices: "Beobachtete Auswahlen",
    choiceOf: (count, total) => `${count} von ${total}`,
    evidenceButton: "Woher weißt du das?",
    evidenceTitle: "Woher weiß HomeIntent das?",
    evidence: {
      verified_actions: (f) => `Dieses Modell basiert auf ${f.observed_actions ?? 0} tatsächlich von Home Assistant angenommenen Aktionen. Bei ${f.verified_successes ?? 0} wurde der erwartete Zustand anschließend beobachtet, bei ${f.verified_failures ?? 0} blieb die erwartete Wirkung aus. Bereits erfüllte Zustände und unverifizierte Aktionen wurden nicht als erfolgreiche Geräteaktionen gezählt.`,
      verified_timings: (f) => `Dieses Modell basiert auf ${f.observations ?? 0} gemessenen Reaktionszeiten erfolgreicher, verifizierter Aktionen.`,
      heating_cycles: (f) => `Dieses Modell basiert auf ${f.heating_cycles ?? 0} abgeschlossenen, nicht kontaminierten Heizzyklen. Zyklen mit offenem Fenster, gewechselter Messquelle oder parallelen Eingriffen wurden verworfen.`,
      goal_runs: (f) => `Diese Abfolge wurde ${f.occurrences ?? 0}-mal erfolgreich und verifiziert ausgeführt${f.opportunities ? `, bei ${f.opportunities} vergleichbaren Gelegenheiten` : ""}.`,
      clarification_selections: (f) => `Diese Präferenz basiert auf ${f.selections ?? 0} Auswahlen, die du bei Rückfragen getroffen hast.`,
      explicit_feedback: () => "Diese Präferenz hast du HomeIntent ausdrücklich beigebracht.",
      samples: (f) => `Dieses Wissen basiert auf ${f.samples ?? 0} lokalen Belegen.`,
    },
    evidenceRows: "Letzte Belege",
    evidenceTruncated: (shown, total) => `${shown} von ${total} Belegen angezeigt.`,
    evidenceState: { verified_success: "Wirkung bestätigt", verified_failure: "Wirkung ausgeblieben", unverified: "Nicht verifiziert", invalid: "Ungültig" },
    latency: "Reaktionszeit",
    technical: "Technische Details",
    tech: {
      model_id: "Modell-ID", model_version: "Modellversion", kind: "Art", health: "Health", knowledge_state: "KnowledgeState",
      quality_score: "Qualitätswert", first_observed: "Erster Beleg", last_observed: "Letzter Beleg", expires_at: "Läuft ab",
      provenance_count: "Verknüpfte Belege", invalidation_reason: "Invalidierungsgrund",
    },
    actions: {
      forget: "🗑 Modell vergessen", confirm_preference: "Präferenz bestätigen", reject_preference: "Verwerfen",
      accept_habit: "Als Routine übernehmen", reject_habit: "Nicht mehr vorschlagen",
    },
    cancel: "Abbrechen",
    forgetTitle: "Modell vergessen?",
    forgetBody: "Dieses gelernte Modell wird entfernt. Alte Erfahrungen dürfen es nicht sofort wiederherstellen. Neue zukünftige Erfahrungen können später erneut neues Wissen bilden. Die zugrunde liegenden Erfahrungen werden dabei nicht umgeschrieben.",
    forgetConfirm: "Modell vergessen",
    rejectHabitTitle: "Nicht mehr vorschlagen?",
    rejectHabitBody: "HomeIntent schlägt genau diese Gewohnheit dauerhaft nicht mehr vor. Das ist stärker als „vergessen“: auch spätere gleiche Belege erzeugen diesen Vorschlag nicht erneut.",
    rejectHabitConfirm: "Nicht mehr vorschlagen",
    acceptHabitTitle: "Als Routine übernehmen?",
    acceptHabitBody: "Diese Schritte werden als bestätigte Routine gespeichert. Die Routine wird jetzt nicht ausgeführt – nur, wenn du sie später aufrufst.",
    acceptHabitConfirm: "Routine speichern",
    confirmPrefTitle: "Präferenz bestätigen?",
    confirmPrefBody: (choice, subject) => `„${subject}“ bedeutet für dich künftig ${choice} – nur für dich und nur in diesem Kontext.`,
    confirmPrefConfirm: "Bestätigen",
    rejectPrefTitle: "Präferenz verwerfen?",
    rejectPrefBody: "Die beobachtete Auswahl bleibt unverbindlich und wird dir nicht mehr vorgeschlagen.",
    rejectPrefConfirm: "Verwerfen",
    resetTitle: "Alle lokalen Lernmodelle wirklich zurücksetzen?",
    resetBody: "Gewohnheiten, Präferenzen, Zuverlässigkeits-, Timing- und Heizmodelle werden entfernt. Daueranweisungen, stummgeschaltete Hinweise, Ruhezeiten, Automationen und die Aktivitätshistorie bleiben unverändert.",
    resetTypeHint: (word) => `Zum Bestätigen „${word}“ eingeben`,
    resetWord: "ZURÜCKSETZEN",
    resetConfirm: "Alle zurücksetzen",
    resetButton: "Alle Lernmodelle zurücksetzen",
    admin: "Verwaltung",
    success: {
      forget: "Das Modell wurde vergessen.", reset: (n) => `${n} Modelle wurden zurückgesetzt.`,
      confirm_preference: "Präferenz bestätigt.", reject_preference: "Präferenz verworfen.",
      accept_habit: "Routine gespeichert. Sie wurde nicht ausgeführt.", reject_habit: "Wird nicht mehr vorgeschlagen.",
      revoke: "Daueranweisung widerrufen.", unmute: "Hinweise sind wieder aktiv.",
    },
    errors: {
      not_found: "Das Element existiert nicht mehr.", not_authorized: "Dafür fehlt dir die Berechtigung.",
      wrong_owner: "Dieses Wissen gehört einem anderen Benutzer. Nur der Eigentümer kann es bestätigen.",
      invalid_state: "Dieser Vorschlag wurde bereits entschieden.", admin_required: "Dafür sind Administratorrechte nötig.",
      unsupported_operation: "Das ist für dieses Modell nicht möglich.", entry_not_found: "Die HomeIntent-Instanz ist nicht geladen.",
      entry_required: "Bitte zuerst eine HomeIntent-Instanz wählen.", unavailable: "Diese Funktion ist gerade nicht verfügbar.",
      unknown: "Die Aktion ist fehlgeschlagen.",
    },
    emptyKnowledgeTitle: "Noch nichts gelernt",
    emptyKnowledge: "HomeIntent hat noch keine ausreichend belegten Modelle gespeichert. Das ist normal, wenn Lernen deaktiviert ist oder noch nicht genug verifizierte Erfahrungen vorliegen.",
    learningDisabled: "Lernen ist derzeit deaktiviert.",
    noMatches: "Keine Modelle passen zu diesem Filter.",
    features: {
      title: "Lernfunktionen", learning: "Erfahrungslernen", predictive: "Vorhersagemodelle", habits: "Gewohnheitserkennung",
      suggestions: "Vorschläge", proactive: "Proaktive Hinweise", permissions: "Daueranweisungen", on: "Ein", off: "Aus",
    },
    settingsPath: "Einstellungen → Geräte & Dienste → HomeIntent → Konfigurieren",
    settingsOpen: "Einstellungen öffnen",
    settingsHint: "Das Learning Center zeigt, was HomeIntent weiß und darf. Wie HomeIntent arbeitet, stellst du in den Integrationsoptionen ein:",
    permissionsTitle: "Daueranweisungen",
    permissionsEmpty: "Keine Daueranweisungen gespeichert. HomeIntent handelt ohne Rückfrage nur mit einer ausdrücklich bestätigten Daueranweisung. Neue Daueranweisungen werden im Gespräch mit HomeIntent erstellt und bestätigt.",
    permission: {
      situation: { device_left_on_when_leaving: "Wenn niemand zuhause ist und ein Gerät noch an ist" },
      operator: { light_turn_off: "Licht ausschalten", switch_turn_off: "Schalter ausschalten", fan_turn_off: "Ventilator ausschalten" },
      condition: { nobody_home: "niemand zuhause" },
      createdBy: "Erstellt von", validUntil: "Gültig bis", usageToday: "Heutige Verwendung", entities: "Geräte",
      area: "Bereich", revoke: "Widerrufen", revoked: "Widerrufen", expired: "Abgelaufen", you: "dir",
      revokeTitle: "Daueranweisung widerrufen?",
      revokeBody: "HomeIntent darf diese Aktion danach nicht mehr ohne Rückfrage ausführen. Es wird dabei kein Gerät geschaltet.",
    },
    mutesTitle: "Stummgeschaltete Hinweise",
    mutesEmpty: "Keine Hinweise stummgeschaltet.",
    mutedSince: "Seit",
    unmute: "Stummschaltung aufheben",
    safetyNote: "🔥 Kritische Sicherheitswarnungen (z. B. Rauch oder CO) können nicht stummgeschaltet werden.",
    quietTitle: "Ruhezeit",
    quietNone: "Keine Ruhezeit aktiv.",
    quietPersonal: "persönlich",
    situation: {
      entry_left_open: "Tür/Fenster/Tor länger offen", appliance_finished: "Gerät fertig",
      device_left_on_when_leaving: "Gerät beim Verlassen noch an", thermal_goal_at_risk: "Heizziel gefährdet",
      device_effect_anomaly: "Ungewöhnliche Gerätereaktion", habit_opportunity: "Gewohnheit erkannt",
      pending_goal_requires_attention: "Geplantes Ziel braucht Aufmerksamkeit", critical_safety_event: "Sicherheitswarnung",
      timer_finished: "Timer abgelaufen",
    },
    decision: { suppress: "nicht gemeldet", history_only: "nur protokolliert", communicate: "gemeldet", escalate: "eskaliert" },
    channel: { voice: "per Sprache", push: "per Push", interactive_push: "per Push mit Antwort", history_only: "im Verlauf", multi_channel: "über mehrere Kanäle", suppress: "nicht zugestellt" },
    result: { delivered: "Zugestellt", delivery_failed: "Zustellung fehlgeschlagen" },
    ack: "Antwort",
    reason: "Grund",
    reasons: {
      attention_budget_exhausted: "Hinweis-Budget ausgeschöpft", recently_dismissed: "kürzlich abgelehnt",
      duplicate_within_window: "bereits gemeldet", muted_by_confirmed_preference: "stummgeschaltet",
      quiet_hours: "Ruhezeit", announced_by_native_timer: "vom Timer angesagt",
    },
    to: (name) => `→ ${name}`,
    toYou: "→ dich",
    activityEmpty: "Noch keine proaktive Aktivität aufgezeichnet.",
    bindingsTitle: "Gelernte Zuordnungen",
    bindingsEmpty: "Noch keine bestätigten Zuordnungen. HomeIntent speichert sie erst nach deinem „Ja“.",
    bindingState: { valid: "aktiv", not_exposed: "wirkungslos: Ziel nicht freigegeben", target_missing: "wirkungslos: Ziel fehlt" },
    bindingScope: { household: "Haushalt", user: "nur für dich" },
    bindingUses: (n) => (n === 1 ? "1-mal genutzt" : `${n}-mal genutzt`),
    bindingRemove: "Vergessen",
    implicitTitle: "Indirekte Aussagen und abgeleitete Routinen",
    implicitLevels: {
      understand_only: "Nur verstehen, nichts ausführen",
      propose: "Vorschlagen und nachfragen (Standard)",
      low_risk_auto: "Harmloses direkt ausführen, sonst nachfragen",
      bound_routines_auto: "Zusätzlich bestätigte Routinen direkt ausführen",
    },
    tracesTitle: "Was hat HomeIntent ausgelöst?",
    tracesEmpty: "HomeIntent hat noch nichts ausgelöst.",
    tracesUnattended: "ohne Rückfrage (Daueranweisung oder proaktiv)",
    tracesVia: (via, step) => (step ? `über ${via}, Schritt „${step}“` : `über ${via}`),
    tracesFollowups: "Kann ausgelöst haben:",
    loadMore: "Weitere laden",
    tombstonesTitle: "Vergessen & unterdrückt",
    tombstonesShow: "Anzeigen",
    tombstonesEmpty: "Keine Einträge.",
    tombstoneDurable: "Dauerhaft nicht mehr als Gewohnheit vorschlagen",
    tombstoneForget: "Vor altem Belegmaterial geschützt",
    tombstoneDeleted: "Entfernt am",
    tombstoneExplain: "„Vergessen“ schützt nur vor der Wiederherstellung aus alten Belegen – spätere neue Erfahrungen können neues Wissen bilden. „Nicht mehr vorschlagen“ gilt dauerhaft für genau diese Gewohnheit.",
    now: "gerade eben",
    today: "Heute",
    yesterday: "Gestern",
    yes: "Ja",
    no: "Nein",
    close: "Schließen",
  },
  en: {
    title: "HomeIntent",
    subtitle: "Knowledge & Autonomy",
    menu: "Menu",
    tabs: { overview: "Overview", knowledge: "Knowledge", autonomy: "Autonomy", activity: "Activity" },
    loading: "Loading …",
    unavailable: "The Learning Center is currently unavailable.",
    retry: "Retry",
    incompatible: "This Learning Center version does not match the backend. Please reload the page.",
    entry: "HomeIntent instance",
    models: (n) => (n === 1 ? "1 learned model" : `${n} learned models`),
    modelCount: (n) => (n === 1 ? "1 model" : `${n} models`),
    observations: (n) => (n === 1 ? "1 observation" : `${n} observations`),
    validCount: (n) => `${n} valid`,
    learningCount: (n) => `${n} still learning`,
    checkCount: (n) => (n === 1 ? "1 thing to review" : `${n} things to review`),
    permissionsCount: (n) => (n === 1 ? "1 standing permission" : `${n} standing permissions`),
    mutesCount: (n) => (n === 1 ? "1 muted notice" : `${n} muted notices`),
    activityCount: (n) => (n === 1 ? "1 event in 24 hours" : `${n} events in 24 hours`),
    attention: "Attention",
    nothingToCheck: "Nothing to review right now.",
    autonomy: "Autonomy",
    communication: "Communication",
    activity: "Activity",
    knowledge: "Knowledge",
    open: "Open",
    details: "Details",
    back: "Back",
    search: "Search",
    searchPlaceholder: "Search name, room or type",
    filterCategory: "Type",
    filterStatus: "Status",
    all: "All",
    categories: { habits: "Habits", preferences: "Preferences", heating: "Heating", devices: "Devices", other: "Other" },
    statusFilter: { valid: "✓ Valid", learning: "? Learning", confirmed: "Confirmed", inferred: "Inferred", stale: "Stale", check: "⚠ Review" },
    status: {
      valid: "Valid", confirmed: "Confirmed", inferred: "Inferred", learning: "Still learning", stale: "Stale",
      drift: "Behaviour changed", unreliable: "Unreliable", invalid: "Invalid", unknown: "Unknown state",
    },
    knowledgeState: { observed: "Observed", inferred: "Inferred – not confirmed yet", confirmed: "Confirmed by you" },
    confirmedBy: (name) => `Confirmed by ${name}`,
    kind: {
      reliability: "Device reliability", effect_timing: "Reaction time", thermal_model: "Heating behaviour",
      preference: "Preference", habit: "Habit", fact: "Fact", duration: "Duration", energy: "Energy",
      battery_trend: "Battery trend",
    },
    band: { morning: "Morning routine", day: "Daytime routine", evening: "Evening routine", night: "Night routine" },
    bandText: { morning: "in the morning", day: "during the day", evening: "in the evening", night: "at night" },
    personalPreference: "Personal preference",
    personalOf: (name) => `Personal · ${name}`,
    action: {
      turn_on: "turn on", turn_off: "turn off", toggle: "toggle", open_cover: "open", close_cover: "close",
      stop_cover: "stop", set_cover_position: "set position", set_temperature: "set temperature",
      set_hvac_mode: "set mode", set_percentage: "set level", select_option: "select option", set_value: "set value",
      volume_set: "set volume", media_play: "play", media_pause: "pause", return_to_base: "return to base",
      lock: "lock", unlock: "unlock", open: "open", close: "close", press: "press", start: "start", other: "action",
    },
    entityMissing: "Entity no longer exists",
    visibility: {
      personal_own: "Only you can see this personal data.",
      personal_admin: "Another user's personal model. As an administrator you may remove it, but not confirm it. Its content is not shown.",
      household: "This model describes a device or room of the household and is visible to household members.",
      system: "System knowledge – visible to administrators only.",
    },
    purposeTitle: "What is this used for?",
    purpose: {
      thermal_model: "HomeIntent uses this model to estimate when a temperature goal should start and whether a goal is running late.",
      reliability: "HomeIntent uses this statistic to judge how reliably device effects were observed. It is not a permission to act.",
      effect_timing: "HomeIntent uses this model to adapt how long it waits for a device effect and to detect unusually slow reactions. The values are observed reaction times, not guarantees.",
      habit: "This observation may be suggested as a routine. HomeIntent never runs this habit automatically.",
      preference: "A confirmed preference helps with ambiguous names – only for you and only in its scope. An inferred preference is not used until you confirm it.",
      other: "This knowledge is only used within its stored scope.",
    },
    facts: {
      observed_actions: "Accepted, verified actions", verified_successes: "Effects confirmed", verified_failures: "Effects missing",
      success_rate: "Observed success rate", observations: "Observations", median_seconds: "Median", p90_seconds: "P90",
      p95_seconds: "P95", mad_seconds: "Spread (MAD)", heating_cycles: "Completed heating cycles",
      holdout_mae_seconds: "Validation error (MAE)", holdout_median_absolute_error_seconds: "Validation median error",
      holdout_p90_absolute_error_seconds: "Validation P90 error", validation_sample_count: "Validation cycles",
      min_start_temperature: "Trained start temperature from", max_start_temperature: "Trained start temperature to",
      min_temperature_delta: "Temperature difference from", max_temperature_delta: "Temperature difference to",
      min_outdoor_gap: "Outdoor gap from", max_outdoor_gap: "Outdoor gap to", occurrences: "Occurrences",
      opportunities: "Comparable opportunities", support: "Support", time_band: "Time of day", suggestion_status: "Suggestion",
      selections: "Observed selections", support_count: "Preferred selections", preferred_entity: "Preferred", samples: "Samples",
      training_mae_seconds: "Training error (MAE)", residual_mad_seconds: "Residual spread",
    },
    suggestion: { new: "Can be suggested", shown: "Suggested", accepted: "Accepted", rejected: "Rejected", snoozed: "Snoozed", expired: "Expired" },
    quality: { very_low: "Very low", low: "Low", medium: "Medium", high: "High", unknown: "Unknown" },
    qualityLabel: "Quality",
    qualityHint: "Quality rates the evidence; it is not a probability.",
    learnedSince: "Learned since",
    lastEvidence: "Latest evidence",
    expiresAt: "Valid until",
    invalidation: "Invalidation reason",
    driftText: "Behaviour has changed compared with the learned model. HomeIntent will not plan with it until enough new evidence exists.",
    staleText: "The latest evidence is older than the validity limit. The model is no longer used for predictions.",
    invalidText: "The model is invalid and not used.",
    unreliableText: "The measured error is too large. The model is not used for planning.",
    learningText: "Not enough evidence yet. That is normal – HomeIntent keeps learning.",
    sequence: "Observed sequence",
    choices: "Observed selections",
    choiceOf: (count, total) => `${count} of ${total}`,
    evidenceButton: "How do you know?",
    evidenceTitle: "How does HomeIntent know this?",
    evidence: {
      verified_actions: (f) => `This model is based on ${f.observed_actions ?? 0} actions actually accepted by Home Assistant. For ${f.verified_successes ?? 0} the expected state was observed afterwards; for ${f.verified_failures ?? 0} the expected effect did not occur. Already-satisfied states and unverified actions were not counted as successful device actions.`,
      verified_timings: (f) => `This model is based on ${f.observations ?? 0} measured reaction times of successful, verified actions.`,
      heating_cycles: (f) => `This model is based on ${f.heating_cycles ?? 0} completed, uncontaminated heating cycles. Cycles with an open window, a changed sensor or concurrent actions were discarded.`,
      goal_runs: (f) => `This sequence was completed and verified ${f.occurrences ?? 0} times${f.opportunities ? ` out of ${f.opportunities} comparable opportunities` : ""}.`,
      clarification_selections: (f) => `This preference is based on ${f.selections ?? 0} choices you made when asked to clarify.`,
      explicit_feedback: () => "You taught HomeIntent this preference explicitly.",
      samples: (f) => `This knowledge is based on ${f.samples ?? 0} local samples.`,
    },
    evidenceRows: "Latest evidence",
    evidenceTruncated: (shown, total) => `Showing ${shown} of ${total} pieces of evidence.`,
    evidenceState: { verified_success: "Effect confirmed", verified_failure: "Effect missing", unverified: "Not verified", invalid: "Invalid" },
    latency: "Reaction time",
    technical: "Technical details",
    tech: {
      model_id: "Model ID", model_version: "Model version", kind: "Kind", health: "Health", knowledge_state: "KnowledgeState",
      quality_score: "Quality score", first_observed: "First evidence", last_observed: "Latest evidence", expires_at: "Expires",
      provenance_count: "Linked evidence", invalidation_reason: "Invalidation reason",
    },
    actions: {
      forget: "🗑 Forget model", confirm_preference: "Confirm preference", reject_preference: "Discard",
      accept_habit: "Save as routine", reject_habit: "Don't suggest again",
    },
    cancel: "Cancel",
    forgetTitle: "Forget model?",
    forgetBody: "This learned model will be removed. Old experiences may not restore it immediately. New future experiences may later form new knowledge. The underlying experiences are not rewritten.",
    forgetConfirm: "Forget model",
    rejectHabitTitle: "Don't suggest again?",
    rejectHabitBody: "HomeIntent will permanently stop suggesting exactly this habit. This is stronger than forgetting: later identical evidence will not bring the suggestion back.",
    rejectHabitConfirm: "Don't suggest again",
    acceptHabitTitle: "Save as routine?",
    acceptHabitBody: "These steps are stored as a confirmed routine. The routine is not run now – only when you invoke it later.",
    acceptHabitConfirm: "Save routine",
    confirmPrefTitle: "Confirm preference?",
    confirmPrefBody: (choice, subject) => `“${subject}” will mean ${choice} for you – only for you and only in this context.`,
    confirmPrefConfirm: "Confirm",
    rejectPrefTitle: "Discard preference?",
    rejectPrefBody: "The observed choice stays non-binding and will no longer be suggested.",
    rejectPrefConfirm: "Discard",
    resetTitle: "Really reset all local learned models?",
    resetBody: "Habits, preferences, reliability, timing and heating models will be removed. Standing permissions, muted notices, quiet hours, automations and activity history stay unchanged.",
    resetTypeHint: (word) => `Type “${word}” to confirm`,
    resetWord: "RESET",
    resetConfirm: "Reset all",
    resetButton: "Reset all learned models",
    admin: "Administration",
    success: {
      forget: "The model was forgotten.", reset: (n) => `${n} models were reset.`,
      confirm_preference: "Preference confirmed.", reject_preference: "Preference discarded.",
      accept_habit: "Routine saved. It was not run.", reject_habit: "Will not be suggested again.",
      revoke: "Standing permission revoked.", unmute: "Notices are active again.",
    },
    errors: {
      not_found: "This item no longer exists.", not_authorized: "You are not allowed to do this.",
      wrong_owner: "This knowledge belongs to another user. Only its owner can confirm it.",
      invalid_state: "This suggestion has already been decided.", admin_required: "Administrator rights are required.",
      unsupported_operation: "This is not possible for this model.", entry_not_found: "The HomeIntent instance is not loaded.",
      entry_required: "Please choose a HomeIntent instance first.", unavailable: "This feature is currently unavailable.",
      unknown: "The action failed.",
    },
    emptyKnowledgeTitle: "Nothing learned yet",
    emptyKnowledge: "HomeIntent has not stored sufficiently supported models yet. That is normal while learning is disabled or not enough verified experiences exist.",
    learningDisabled: "Learning is currently disabled.",
    noMatches: "No models match this filter.",
    features: {
      title: "Learning features", learning: "Experience learning", predictive: "Predictive models", habits: "Habit discovery",
      suggestions: "Suggestions", proactive: "Proactive notices", permissions: "Standing permissions", on: "On", off: "Off",
    },
    settingsPath: "Settings → Devices & services → HomeIntent → Configure",
    settingsOpen: "Open settings",
    settingsHint: "The Learning Center shows what HomeIntent knows and may do. How HomeIntent works is configured in the integration options:",
    permissionsTitle: "Standing permissions",
    permissionsEmpty: "No standing permissions stored. HomeIntent only acts without asking under an explicitly confirmed standing permission. New ones are created and confirmed in conversation with HomeIntent.",
    permission: {
      situation: { device_left_on_when_leaving: "When nobody is home and a device is still on" },
      operator: { light_turn_off: "Turn light off", switch_turn_off: "Turn switch off", fan_turn_off: "Turn fan off" },
      condition: { nobody_home: "nobody home" },
      createdBy: "Created by", validUntil: "Valid until", usageToday: "Used today", entities: "Devices", area: "Area",
      revoke: "Revoke", revoked: "Revoked", expired: "Expired", you: "you",
      revokeTitle: "Revoke standing permission?",
      revokeBody: "HomeIntent may then no longer perform this action without asking. No device is switched by revoking.",
    },
    mutesTitle: "Muted notices",
    mutesEmpty: "No notices are muted.",
    mutedSince: "Since",
    unmute: "Unmute",
    safetyNote: "🔥 Critical safety warnings (e.g. smoke or CO) cannot be muted.",
    quietTitle: "Quiet hours",
    quietNone: "No quiet hours active.",
    quietPersonal: "personal",
    situation: {
      entry_left_open: "Door/window/gate open for a long time", appliance_finished: "Appliance finished",
      device_left_on_when_leaving: "Device left on when leaving", thermal_goal_at_risk: "Heating goal at risk",
      device_effect_anomaly: "Unusual device reaction", habit_opportunity: "Habit detected",
      pending_goal_requires_attention: "Scheduled goal needs attention", critical_safety_event: "Safety warning",
      timer_finished: "Timer finished",
    },
    decision: { suppress: "not reported", history_only: "logged only", communicate: "reported", escalate: "escalated" },
    channel: { voice: "by voice", push: "by push", interactive_push: "by interactive push", history_only: "in history", multi_channel: "via several channels", suppress: "not delivered" },
    result: { delivered: "Delivered", delivery_failed: "Delivery failed" },
    ack: "Reply",
    reason: "Reason",
    reasons: {
      attention_budget_exhausted: "attention budget exhausted", recently_dismissed: "recently dismissed",
      duplicate_within_window: "already reported", muted_by_confirmed_preference: "muted", quiet_hours: "quiet hours",
      announced_by_native_timer: "announced by timer",
    },
    to: (name) => `→ ${name}`,
    toYou: "→ you",
    activityEmpty: "No proactive activity recorded yet.",
    bindingsTitle: "Learned bindings",
    bindingsEmpty: "No confirmed bindings yet. HomeIntent stores them only after your “yes”.",
    bindingState: { valid: "active", not_exposed: "inactive: target not exposed", target_missing: "inactive: target missing" },
    bindingScope: { household: "household", user: "only you" },
    bindingUses: (n) => (n === 1 ? "used once" : `used ${n} times`),
    bindingRemove: "Forget",
    implicitTitle: "Implicit statements and inferred routines",
    implicitLevels: {
      understand_only: "Only understand, never act",
      propose: "Propose and ask (default)",
      low_risk_auto: "Run harmless actions directly, otherwise ask",
      bound_routines_auto: "Also run confirmed routines directly",
    },
    tracesTitle: "What did HomeIntent trigger?",
    tracesEmpty: "HomeIntent has not triggered anything yet.",
    tracesUnattended: "without asking (standing permission or proactive)",
    tracesVia: (via, step) => (step ? `via ${via}, step “${step}”` : `via ${via}`),
    tracesFollowups: "May have triggered:",
    loadMore: "Load more",
    tombstonesTitle: "Forgotten & suppressed",
    tombstonesShow: "Show",
    tombstonesEmpty: "No entries.",
    tombstoneDurable: "Permanently not suggested as a habit",
    tombstoneForget: "Protected against old evidence",
    tombstoneDeleted: "Removed on",
    tombstoneExplain: "“Forget” only prevents restoring from old evidence – later new experiences may form new knowledge. “Don't suggest again” is permanent for exactly that habit.",
    now: "just now",
    today: "Today",
    yesterday: "Yesterday",
    yes: "Yes",
    no: "No",
    close: "Close",
  },
};

// ---------------------------------------------------------------- DOM helper

/**
 * Create an element.  Children that are strings/numbers become TEXT nodes —
 * never HTML.  `null`/`false` children are skipped.
 */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const CATEGORY_ICON = { habits: "🌅", preferences: "💡", heating: "🌡", devices: "⚙", other: "🧠" };
const STATUS_ICON = {
  valid: "✓", confirmed: "✓", inferred: "?", learning: "?", stale: "⏳", drift: "⚠", unreliable: "⚠", invalid: "✕", unknown: "•",
};
const STATUS_TONE = {
  valid: "ok", confirmed: "ok", inferred: "info", learning: "info", stale: "warn", drift: "warn", unreliable: "warn", invalid: "bad", unknown: "info",
};
const CHECK_STATUSES = new Set(["stale", "drift", "unreliable", "invalid"]);

// ---------------------------------------------------------------- panel

class HomeIntentLearningCenter extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._narrow = false;
    this._route = null;
    this._initialized = false;
    this._entries = [];
    this._entryId = null;
    this._summary = null;
    this._models = null;
    this._modelsTotal = 0;
    this._learningEnabled = true;
    this._detail = null;
    this._evidence = null;
    this._permissions = null;
    this._features = null;
    this._mutes = null;
    this._history = null;
    this._historyCursor = null;
    this._traces = null;
    this._bindings = null;
    this._tombstones = null;
    this._error = null;
    this._toast = null;
    this._filters = { category: "all", status: "all", search: "" };
    this._unsub = null;
    this._refreshTimer = null;
    this._busy = false;
    this._dialogReturnFocus = null;
    this._renderStyles();
  }

  // -- HA properties
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
  }
  get hass() { return this._hass; }
  set narrow(value) { this._narrow = Boolean(value); if (this._initialized) this._render(); }
  set route(route) {
    const previous = this._routeKey();
    this._route = route;
    if (this._initialized && previous !== this._routeKey()) this._onRouteChanged();
  }
  set panel(_panel) { /* config holds only the API version */ }

  disconnectedCallback() {
    this._unsubscribe();
    if (this._refreshTimer) clearTimeout(this._refreshTimer);
  }

  connectedCallback() {
    if (this._initialized && !this._unsub) this._subscribe();
  }

  // -- i18n / formatting
  get t() {
    const lang = (this._hass && (this._hass.locale?.language || this._hass.language)) || "de";
    return lang.toLowerCase().startsWith("de") ? STRINGS.de : STRINGS.en;
  }
  get _locale() {
    return (this._hass && (this._hass.locale?.language || this._hass.language)) || "de";
  }
  get _timeZone() {
    const hass = this._hass;
    if (hass?.locale?.time_zone === "local") return undefined;
    return hass?.config?.time_zone || undefined;
  }
  _num(value, digits = 0) {
    return new Intl.NumberFormat(this._locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
  }
  _date(iso, withTime = true) {
    if (!iso) return "–";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "–";
    const options = withTime
      ? { dateStyle: "medium", timeStyle: "short", timeZone: this._timeZone }
      : { dateStyle: "medium", timeZone: this._timeZone };
    try { return new Intl.DateTimeFormat(this._locale, options).format(date); } catch (_err) { return date.toLocaleString(); }
  }
  _relative(iso) {
    if (!iso) return "–";
    const date = new Date(iso);
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const abs = Math.abs(seconds);
    if (abs < 60) return this.t.now;
    const rtf = new Intl.RelativeTimeFormat(this._locale, { numeric: "auto" });
    if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
    if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), "day");
    return this._date(iso, false);
  }
  _seconds(value) {
    if (value >= 120) return `${this._num(value / 60, 1)} min`;
    return `${this._num(value, 1)} s`;
  }
  _fact(fact) {
    const t = this.t;
    switch (fact.unit) {
      case "ratio": return `${this._num(fact.value * 100, 1)} %`;
      case "seconds": return this._seconds(fact.value);
      case "celsius": return `${this._num(fact.value, 1)} °C`;
      case "count": return this._num(fact.value);
      case "datetime": return this._date(fact.value);
      default:
        if (fact.key === "time_band") return t.bandText[fact.value] || fact.value;
        if (fact.key === "suggestion_status") return t.suggestion[fact.value] || fact.value;
        return String(fact.value);
    }
  }
  _factLabel(key) { return this.t.facts[key] || key; }
  _expected(value) {
    const de = this.t === STRINGS.de;
    const map = de
      ? { on: "an", off: "aus", open: "offen", closed: "geschlossen", locked: "verriegelt", unlocked: "entriegelt" }
      : { on: "on", off: "off", open: "open", closed: "closed", locked: "locked", unlocked: "unlocked" };
    return map[value] || String(value).replace(/^([a-z_]+)=/, "$1 = ");
  }
  _actionLabel(key) { return this.t.action[key] || this.t.action.other; }

  // -- routing
  _routeKey() { return this._route ? this._route.path || "" : ""; }
  _view() {
    const path = this._routeKey();
    const parts = path.split("/").filter(Boolean);
    if (parts[0] === "model" && parts[1]) return { tab: "knowledge", ref: decodeURIComponent(parts[1]) };
    if (["knowledge", "autonomy", "activity"].includes(parts[0])) return { tab: parts[0], ref: null };
    return { tab: "overview", ref: null };
  }
  _navigate(path, replace = false) {
    const url = `${PANEL_PATH}${path}`;
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace } }));
    // Keep working even when HA does not feed `route` back (e.g. tests).
    if (!this._route || this._route.path !== path) {
      this._route = { prefix: PANEL_PATH, path };
      this._onRouteChanged();
    }
  }
  _onRouteChanged() {
    const view = this._view();
    this._detail = null;
    this._evidence = null;
    this._render();
    this._loadView(view);
  }

  // -- backend
  async _call(type, payload = {}) {
    const message = { type: `${PREFIX}/${type}`, ...payload };
    if (this._entryId) message.entry_id = this._entryId;
    return this._hass.connection.sendMessagePromise(message);
  }
  _errorText(err) {
    const code = err && (err.code || err.error?.code);
    return this.t.errors[code] || this.t.errors.unknown;
  }
  async _init() {
    this._initialized = true;
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({ type: `${PREFIX}/entries` });
      if (result.api_version !== API_VERSION) {
        this._error = { incompatible: true };
        this._render();
        return;
      }
      this._entries = result.entries || [];
      this._entryId = this._entries.length ? this._entries[0].entry_id : null;
      if (!this._entryId) throw { code: "entry_not_found" };
      this._error = null;
      await this._subscribe();
      await this._loadView(this._view());
      this._hass.connection.addEventListener?.("ready", () => this._onReconnect());
    } catch (err) {
      this._error = { message: this._errorText(err) };
      this._render();
    }
  }
  async _onReconnect() {
    this._unsub = null;
    await this._subscribe();
    this._scheduleRefresh(0);
  }
  async _subscribe() {
    this._unsubscribe();
    if (!this._hass?.connection?.subscribeMessage || !this._entryId) return;
    try {
      const message = { type: `${PREFIX}/subscribe`, entry_id: this._entryId };
      this._unsub = await this._hass.connection.subscribeMessage((event) => {
        if (event && event.entry_id === this._entryId) this._scheduleRefresh(250);
      }, message);
    } catch (_err) {
      this._unsub = null;
    }
  }
  _unsubscribe() {
    if (typeof this._unsub === "function") {
      try { this._unsub(); } catch (_err) { /* connection already closed */ }
    }
    this._unsub = null;
  }
  _scheduleRefresh(delay) {
    if (this._refreshTimer) clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(() => {
      this._refreshTimer = null;
      this._loadView(this._view(), { keepEvidence: true });
    }, delay);
  }
  async _loadView(view, options = {}) {
    if (!this._entryId) return;
    try {
      const summary = await this._call("summary");
      if (summary.api_version !== API_VERSION) { this._error = { incompatible: true }; this._render(); return; }
      this._summary = summary;
      if (view.tab === "knowledge") {
        const list = await this._call("models/list", { limit: 250 });
        this._models = list.models;
        this._modelsTotal = list.total;
        this._learningEnabled = list.learning_enabled;
        if (view.ref) {
          try {
            const detail = await this._call("models/get", { ref: view.ref });
            this._detail = detail.model;
            if (!options.keepEvidence) this._evidence = null;
          } catch (err) {
            this._detail = { missing: true, message: this._errorText(err) };
            this._evidence = null;
          }
        }
      } else if (view.tab === "autonomy") {
        const [permissions, mutes, bindings] = await Promise.all([
          this._call("permissions/list"), this._call("mutes/list"),
          this._call("bindings/list").catch(() => null),
        ]);
        this._permissions = permissions.permissions;
        this._features = permissions.features;
        this._mutes = mutes.mutes;
        this._bindings = bindings;
      } else if (view.tab === "activity") {
        const [history, traces] = await Promise.all([
          this._call("history/list", { limit: 40 }),
          this._call("traces/list", { limit: 40 }).catch(() => ({ traces: [] })),
        ]);
        this._history = history.records;
        this._historyCursor = history.next_cursor;
        this._traces = traces.traces || [];
      }
      this._error = null;
    } catch (err) {
      this._error = { message: this._errorText(err) || this.t.unavailable };
    }
    this._render();
  }
  async _loadMoreHistory() {
    if (this._historyCursor === null || this._historyCursor === undefined) return;
    try {
      const more = await this._call("history/list", { limit: 40, cursor: this._historyCursor });
      this._history = [...(this._history || []), ...more.records];
      this._historyCursor = more.next_cursor;
    } catch (err) {
      this._showToast(this._errorText(err), true);
    }
    this._render();
  }
  async _loadEvidence() {
    if (!this._detail || this._detail.missing) return;
    try {
      const result = await this._call("models/evidence", { ref: this._detail.ref, limit: 10 });
      this._evidence = result.evidence;
    } catch (err) {
      this._evidence = { error: this._errorText(err) };
    }
    this._render();
    this.shadowRoot.querySelector("#evidence")?.focus();
  }
  async _loadTombstones() {
    try {
      const result = await this._call("tombstones/list");
      this._tombstones = result.tombstones;
    } catch (err) {
      this._showToast(this._errorText(err), true);
    }
    this._render();
  }

  /** Mutation flow: confirmation → backend authority → refresh → feedback. */
  async _mutate(type, payload, successText, afterPath) {
    if (this._busy) return;
    this._busy = true;
    try {
      const result = await this._call(type, payload);
      this._showToast(typeof successText === "function" ? successText(result) : successText, false);
      if (afterPath !== undefined) this._navigate(afterPath, true);
      else await this._loadView(this._view());
    } catch (err) {
      this._showToast(this._errorText(err), true);
      this._render();
    } finally {
      this._busy = false;
    }
  }
  _showToast(text, isError) {
    this._toast = { text, isError };
    this._renderToast();
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this._toast = null; this._renderToast(); }, isError ? 7000 : 4000);
  }

  // -- model text
  _modelTitle(model) {
    const t = this.t;
    if (model.kind === "habit") return t.band[model.subject_label] || t.kind.habit;
    if (model.kind === "preference") {
      if (model.redacted) return t.personalPreference;
      return `„${model.subject_label}“${model.area_label ? ` · ${model.area_label}` : ""}`;
    }
    if (model.kind === "reliability" || model.kind === "effect_timing") {
      return model.action_key ? `${model.subject_label} – ${this._actionLabel(model.action_key)}` : model.subject_label;
    }
    return model.subject_label;
  }
  _searchText(model) {
    const t = this.t;
    return [
      this._modelTitle(model), model.subject_label, model.area_label || "", t.kind[model.kind] || model.kind,
      t.categories[model.category] || "", model.owner_label || "",
    ].join(" ").toLocaleLowerCase(this._locale);
  }
  _filtered() {
    const { category, status, search } = this._filters;
    const needle = search.trim().toLocaleLowerCase(this._locale);
    return (this._models || []).filter((model) => {
      if (category !== "all" && model.category !== category) return false;
      if (status === "check" && !CHECK_STATUSES.has(model.status) && !model.subject_missing) return false;
      if (status !== "all" && status !== "check" && model.status !== status) return false;
      if (needle && !this._searchText(model).includes(needle)) return false;
      return true;
    });
  }

  // -- rendering
  _renderStyles() {
    const style = document.createElement("style");
    style.textContent = STYLES;
    this.shadowRoot.appendChild(style);
    this._root = document.createElement("div");
    this._root.className = "root";
    this.shadowRoot.appendChild(this._root);
    this._toastHost = document.createElement("div");
    this._toastHost.setAttribute("aria-live", "polite");
    this._toastHost.className = "toast-host";
    this.shadowRoot.appendChild(this._toastHost);
    this._dialogHost = document.createElement("div");
    this.shadowRoot.appendChild(this._dialogHost);
  }
  _renderToast() {
    this._toastHost.replaceChildren();
    if (this._toast) {
      this._toastHost.appendChild(h("div", { class: `toast ${this._toast.isError ? "error" : ""}`, role: this._toast.isError ? "alert" : "status" }, this._toast.text));
    }
  }
  _render() {
    const t = this.t;
    const view = this._view();
    const scrollY = this._root.querySelector(".content")?.scrollTop || 0;
    const header = h("header", { class: "toolbar" },
      this._narrow ? h("button", { class: "icon-button", "aria-label": t.menu, onclick: () => this._toggleMenu() }, "☰") : null,
      h("div", { class: "heading" }, h("h1", null, "🧠 ", t.title), h("div", { class: "subtitle" }, t.subtitle)),
    );
    const tabs = h("nav", { class: "tabs", role: "tablist", "aria-label": t.title },
      ["overview", "knowledge", "autonomy", "activity"].map((tab) => h("button", {
        role: "tab", id: `tab-${tab}`, class: "tab", "aria-selected": view.tab === tab ? "true" : "false",
        tabindex: view.tab === tab ? "0" : "-1", "aria-controls": "tabpanel",
        onclick: () => this._navigate(tab === "overview" ? "" : `/${tab}`),
        onkeydown: (event) => this._tabKey(event, tab),
      }, t.tabs[tab])),
    );
    const content = h("main", { class: "content", id: "tabpanel", role: "tabpanel", "aria-labelledby": `tab-${view.tab}` });
    if (this._entries.length > 1) content.appendChild(this._entrySelector());
    try {
      if (this._error?.incompatible) content.appendChild(this._message(t.incompatible, true));
      else if (this._error) content.appendChild(this._message(this._error.message || t.unavailable, true, true));
      else if (!this._summary) content.appendChild(h("p", { class: "muted", role: "status" }, t.loading));
      else if (view.tab === "overview") content.appendChild(this._overview());
      else if (view.tab === "knowledge") content.appendChild(view.ref ? this._detailView() : this._knowledge());
      else if (view.tab === "autonomy") content.appendChild(this._autonomy());
      else content.appendChild(this._activity());
    } catch (err) {
      console.error("HomeIntent Learning Center render error", err);
      content.appendChild(this._message(t.errors.unknown, true, true));
    }
    this._root.replaceChildren(header, tabs, content);
    content.scrollTop = scrollY;
  }
  _toggleMenu() {
    this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
  }
  _tabKey(event, tab) {
    const order = ["overview", "knowledge", "autonomy", "activity"];
    let index = order.indexOf(tab);
    if (event.key === "ArrowRight") index = (index + 1) % order.length;
    else if (event.key === "ArrowLeft") index = (index + order.length - 1) % order.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = order.length - 1;
    else return;
    event.preventDefault();
    const next = order[index];
    this._navigate(next === "overview" ? "" : `/${next}`);
    this.shadowRoot.querySelector(`#tab-${next}`)?.focus();
  }
  _entrySelector() {
    const t = this.t;
    const select = h("select", { id: "entry", onchange: (event) => { this._entryId = event.target.value; this._subscribe(); this._loadView(this._view()); } },
      this._entries.map((entry) => h("option", { value: entry.entry_id, selected: entry.entry_id === this._entryId }, entry.title || entry.entry_id)));
    return h("div", { class: "entry-select" }, h("label", { for: "entry" }, t.entry), select);
  }
  _message(text, isError, withRetry) {
    return h("div", { class: `card message ${isError ? "error" : ""}`, role: isError ? "alert" : "status" },
      h("p", null, text),
      withRetry ? h("button", { class: "button", onclick: () => { this._error = null; this._render(); this._init(); } }, this.t.retry) : null);
  }
  _badge(status) {
    const t = this.t;
    return h("span", { class: `badge tone-${STATUS_TONE[status] || "info"}` },
      h("span", { "aria-hidden": "true" }, STATUS_ICON[status] || "•"), " ", t.status[status] || t.status.unknown);
  }
  _row(label, value) {
    return h("div", { class: "row" }, h("span", { class: "row-label" }, label), h("span", { class: "row-value" }, value));
  }
  _link(label, path, count, category) {
    return h("button", { class: "list-link", onclick: () => {
      if (category !== undefined) this._filters = { category, status: "all", search: "" };
      this._navigate(path);
    } },
      h("span", null, label), h("span", { class: "list-link-end" }, count !== undefined ? h("span", { class: "count" }, this._num(count)) : null, h("span", { "aria-hidden": "true" }, "›")));
  }

  _overview() {
    const t = this.t;
    const s = this._summary;
    const wrap = h("div", { class: "grid" });
    wrap.appendChild(h("section", { class: "card hero", "aria-label": t.knowledge },
      h("div", { class: "hero-number" }, t.models(s.total_models)),
      h("ul", { class: "hero-list" },
        h("li", null, h("span", { class: "tone-ok", "aria-hidden": "true" }, "✓ "), t.validCount(s.valid_models)),
        h("li", null, h("span", { class: "tone-info", "aria-hidden": "true" }, "? "), t.learningCount(s.learning_models)),
        h("li", null, h("span", { class: "tone-warn", "aria-hidden": "true" }, "⚠ "), t.checkCount(s.needs_attention))),
      s.total_models === 0 ? h("p", { class: "muted" }, s.features.learning_enabled ? t.emptyKnowledge : t.learningDisabled) : null,
    ));
    const categories = h("section", { class: "card" }, h("h2", null, t.knowledge));
    const order = ["habits", "preferences", "heating", "devices", "other"];
    let any = false;
    for (const category of order) {
      const count = s.category_counts[category];
      if (!count) continue;
      any = true;
      categories.appendChild(this._link(`${CATEGORY_ICON[category]} ${t.categories[category]}`, "/knowledge", count, category));
    }
    if (!any) categories.appendChild(h("p", { class: "muted" }, t.emptyKnowledgeTitle));
    else categories.appendChild(this._link(t.all, "/knowledge", undefined, "all"));
    wrap.appendChild(categories);

    const attention = h("section", { class: "card", "aria-label": t.attention },
      h("h2", null, "⚠ ", t.attention), h("p", { class: "muted" }, s.attention.length ? t.checkCount(s.attention.length) : t.nothingToCheck));
    for (const item of s.attention.slice(0, 8)) {
      attention.appendChild(h("button", {
        class: `attention-item sev-${item.severity}`,
        onclick: () => item.model_ref ? this._navigate(`/model/${encodeURIComponent(item.model_ref)}`) : this._navigate("/autonomy"),
      }, h("span", { class: "attention-kind" }, this._attentionText(item.kind)), h("span", { class: "attention-subject" }, this._attentionSubject(item))));
    }
    wrap.appendChild(attention);
    wrap.appendChild(h("section", { class: "card" },
      h("h2", null, "🤖 ", t.autonomy), this._link(t.permissionsCount(s.standing_permission_count), "/autonomy"),
      h("h2", null, "🔕 ", t.communication), this._link(t.mutesCount(s.mute_count), "/autonomy"),
      h("h2", null, "🕒 ", t.activity), this._link(t.activityCount(s.recent_activity_count), "/activity")));
    return wrap;
  }
  _attentionText(kind) {
    const de = this.t === STRINGS.de;
    const map = de ? {
      preference_pending: "Präferenz wartet auf Bestätigung", habit_pending: "Gewohnheit wartet auf Entscheidung",
      model_drift: "Verhalten hat sich verändert", model_stale: "Modell ist veraltet", model_invalid: "Modell ist ungültig",
      model_unreliable: "Modell ist unzuverlässig", entity_missing: "Entität nicht mehr vorhanden",
      permission_expiring: "Daueranweisung läuft bald ab",
    } : {
      preference_pending: "Preference awaits confirmation", habit_pending: "Habit awaits a decision",
      model_drift: "Behaviour has changed", model_stale: "Model is stale", model_invalid: "Model is invalid",
      model_unreliable: "Model is unreliable", entity_missing: "Entity no longer exists",
      permission_expiring: "Standing permission expires soon",
    };
    return map[kind] || kind;
  }
  _attentionSubject(item) {
    if (item.kind === "habit_pending" || /^(morning|day|evening|night)$/.test(item.subject_label)) return this.t.band[item.subject_label] || item.subject_label;
    return item.subject_label;
  }

  _knowledge() {
    const t = this.t;
    const wrap = h("div", { class: "stack" });
    const models = this._models || [];
    if (!models.length) {
      wrap.appendChild(h("section", { class: "card empty" }, h("h2", null, t.emptyKnowledgeTitle),
        h("p", null, t.emptyKnowledge), this._learningEnabled ? null : h("p", { class: "strong" }, t.learningDisabled)));
      if (this._summary?.is_admin) wrap.appendChild(this._adminSection());
      return wrap;
    }
    const search = h("input", {
      type: "search", id: "search", class: "search", value: this._filters.search, placeholder: t.searchPlaceholder,
      "aria-label": t.search, autocomplete: "off",
      oninput: (event) => { this._filters.search = event.target.value; this._renderList(); },
    });
    const present = new Set(models.map((model) => model.category));
    const categoryChips = ["all", "habits", "preferences", "heating", "devices", "other"].filter((key) => key === "all" || present.has(key));
    const statusChips = ["all", "valid", "learning", "confirmed", "inferred", "stale", "check"];
    wrap.appendChild(h("div", { class: "filters" }, search,
      this._chips(t.filterCategory, categoryChips, "category", (key) => key === "all" ? t.all : t.categories[key]),
      this._chips(t.filterStatus, statusChips, "status", (key) => key === "all" ? t.all : t.statusFilter[key])));
    this._listHost = h("div", { class: "grid cards", "aria-live": "polite" });
    wrap.appendChild(this._listHost);
    this._renderList();
    if (this._summary?.is_admin) wrap.appendChild(this._adminSection());
    return wrap;
  }
  _chips(label, keys, filterKey, text) {
    return h("div", { class: "chips", role: "group", "aria-label": label },
      keys.map((key) => h("button", {
        class: "chip", "aria-pressed": this._filters[filterKey] === key ? "true" : "false",
        onclick: () => { this._filters[filterKey] = key; this._render(); },
      }, text(key))));
  }
  _renderList() {
    if (!this._listHost) return;
    const t = this.t;
    const models = this._filtered();
    const cards = [];
    for (const model of models) {
      try { cards.push(this._card(model)); } catch (err) { console.error("HomeIntent card render error", err); }
    }
    this._listHost.replaceChildren(...(cards.length ? cards : [h("p", { class: "muted" }, t.noMatches)]));
  }
  _card(model) {
    const t = this.t;
    const headline = model.headline && model.headline.key !== "preferred_entity"
      ? h("div", { class: "headline" }, `${this._factLabel(model.headline.key)}: ${this._fact(model.headline)}`)
      : model.headline ? h("div", { class: "headline" }, `${this._factLabel("preferred_entity")}: ${model.headline.value}`) : null;
    return h("button", {
      class: "card model-card", onclick: () => this._navigate(`/model/${encodeURIComponent(model.ref)}`),
      "aria-label": `${this._modelTitle(model)}, ${t.kind[model.kind] || model.kind}, ${t.status[model.status] || ""}`,
    },
      h("div", { class: "card-title" }, h("span", { "aria-hidden": "true" }, CATEGORY_ICON[model.category] || "🧠", " "), h("span", { class: "wrap" }, this._modelTitle(model))),
      h("div", { class: "muted" }, t.kind[model.kind] || model.kind, model.redacted && model.owner_label ? ` · ${t.personalOf(model.owner_label)}` : ""),
      h("div", { class: "card-status" }, this._badge(model.status), h("span", { class: "muted" }, t.observations(model.sample_count))),
      model.subject_missing ? h("div", { class: "tone-warn" }, "⚠ ", t.entityMissing) : null,
      headline,
      h("div", { class: "card-footer" }, h("span", { class: "muted" }, this._relative(model.last_observed)), h("span", { class: "details-link" }, t.details, " ›")));
  }

  _detailView() {
    const t = this.t;
    const wrap = h("div", { class: "stack detail" });
    wrap.appendChild(h("button", { class: "button text back", onclick: () => this._navigate("/knowledge") }, "‹ ", t.back));
    const model = this._detail;
    if (!model) { wrap.appendChild(h("p", { class: "muted", role: "status" }, t.loading)); return wrap; }
    if (model.missing) { wrap.appendChild(this._message(model.message, true)); return wrap; }

    const head = h("section", { class: "card" },
      h("h2", { class: "detail-title" }, h("span", { "aria-hidden": "true" }, CATEGORY_ICON[model.category] || "🧠", " "), h("span", { class: "wrap" }, this._modelTitle(model))),
      h("div", { class: "muted" }, t.kind[model.kind] || model.kind),
      h("div", { class: "card-status" }, this._badge(model.status),
        model.knowledge_state !== "observed" ? h("span", { class: "chip static" }, model.confirmed_by_viewer || model.knowledge_state !== "confirmed" ? t.knowledgeState[model.knowledge_state] : t.confirmedBy(model.confirmed_by_label || "?")) : null),
      model.subject_missing ? h("p", { class: "tone-warn" }, "⚠ ", t.entityMissing) : null,
      this._statusExplanation(model),
      h("p", { class: "muted small" }, this._visibilityText(model)));
    wrap.appendChild(head);

    if (!model.redacted) {
      const facts = h("section", { class: "card" });
      facts.appendChild(this._row(t.qualityLabel, t.quality[model.quality_band] || model.quality_band));
      for (const fact of model.facts) {
        if (fact.key === "preferred_entity") continue;
        facts.appendChild(this._row(this._factLabel(fact.key), this._fact(fact)));
      }
      facts.appendChild(this._row(t.learnedSince, this._date(model.first_observed, false)));
      facts.appendChild(this._row(t.lastEvidence, this._date(model.last_observed)));
      if (model.expires_at) facts.appendChild(this._row(t.expiresAt, this._date(model.expires_at, false)));
      if (model.invalidation_reason) facts.appendChild(this._row(t.invalidation, model.invalidation_reason));
      facts.appendChild(h("p", { class: "muted small" }, t.qualityHint));
      wrap.appendChild(facts);
    }
    if (model.habit_steps.length) {
      const steps = h("ol", { class: "sequence" }, model.habit_steps.map((step) => h("li", null,
        h("span", { class: "wrap" }, step.entity_label), " → ", this._expected(step.expected),
        step.entity_missing ? h("span", { class: "tone-warn" }, ` (${t.entityMissing})`) : null)));
      wrap.appendChild(h("section", { class: "card" }, h("h3", null, t.sequence), steps));
    }
    if (model.preference_choices.length) {
      const total = model.preference_choices.reduce((sum, item) => sum + item.count, 0);
      wrap.appendChild(h("section", { class: "card" }, h("h3", null, t.choices),
        model.preference_choices.map((choice) => this._row(
          `${choice.preferred ? "★ " : ""}${choice.entity_label}${choice.entity_missing ? ` (${t.entityMissing})` : ""}`,
          t.choiceOf(choice.count, total)))));
    }
    wrap.appendChild(h("section", { class: "card" }, h("h3", null, t.purposeTitle), h("p", null, t.purpose[model.kind] || t.purpose.other)));

    const actions = h("div", { class: "actions" });
    if (model.evidence_available) actions.appendChild(h("button", { class: "button", onclick: () => this._loadEvidence() }, t.evidenceButton));
    for (const action of model.actions) {
      if (action === "forget") continue;
      actions.appendChild(h("button", { class: `button ${action.startsWith("confirm") || action.startsWith("accept") ? "primary" : ""}`, onclick: () => this._confirmAction(action, model) }, t.actions[action]));
    }
    if (model.actions.includes("forget")) actions.appendChild(h("button", { class: "button danger", onclick: () => this._confirmAction("forget", model) }, t.actions.forget));
    wrap.appendChild(actions);
    if (this._evidence) wrap.appendChild(this._evidenceView());
    wrap.appendChild(this._technical(model));
    return wrap;
  }
  _statusExplanation(model) {
    const t = this.t;
    const text = { drift: t.driftText, stale: t.staleText, invalid: t.invalidText, unreliable: t.unreliableText, learning: t.learningText }[model.status];
    return text ? h("p", null, text) : null;
  }
  _visibilityText(model) {
    const v = this.t.visibility;
    if (model.visibility === "personal") return model.owned_by_viewer ? v.personal_own : v.personal_admin;
    return v[model.visibility] || "";
  }
  _evidenceView() {
    const t = this.t;
    const ev = this._evidence;
    const section = h("section", { class: "card", id: "evidence", tabindex: "-1", "aria-label": t.evidenceTitle }, h("h3", null, t.evidenceTitle));
    if (ev.error) { section.appendChild(h("p", { class: "tone-bad" }, ev.error)); return section; }
    const facts = {};
    for (const fact of ev.facts) facts[fact.key] = fact.unit === "count" ? this._num(fact.value) : this._fact(fact);
    const template = t.evidence[ev.basis] || t.evidence.samples;
    section.appendChild(h("p", null, template(facts)));
    if (ev.rows.length) {
      section.appendChild(h("h4", null, t.evidenceRows));
      section.appendChild(h("ul", { class: "evidence-list" }, ev.rows.map((row) => h("li", null,
        h("div", null, this._date(row.timestamp)),
        h("div", { class: "muted" }, row.entity_label, " · ", this._actionLabel(row.action_key)),
        h("div", null, t.evidenceState[row.evidence_state] || row.evidence_state,
          row.latency_seconds !== null && row.latency_seconds !== undefined ? ` · ${t.latency} ${this._seconds(row.latency_seconds)}` : "")))));
      if (ev.truncated) section.appendChild(h("p", { class: "muted small" }, t.evidenceTruncated(ev.rows.length, ev.provenance_count)));
    }
    return section;
  }
  _technical(model) {
    const t = this.t;
    const tech = model.technical || {};
    const body = h("div", { class: "technical" });
    for (const key of ["model_id", "model_version", "kind", "health", "knowledge_state", "quality_score", "first_observed", "last_observed", "expires_at", "provenance_count", "invalidation_reason"]) {
      const value = tech[key];
      if (value === null || value === undefined) continue;
      body.appendChild(this._row(t.tech[key] || key, String(value)));
    }
    for (const metric of tech.validation_metrics || []) body.appendChild(this._row(this._factLabel(metric.key), this._fact(metric)));
    return h("details", { class: "card" }, h("summary", null, t.technical), body);
  }

  _adminSection() {
    const t = this.t;
    const section = h("section", { class: "card admin" }, h("h2", null, t.admin));
    section.appendChild(h("h3", null, t.tombstonesTitle));
    section.appendChild(h("p", { class: "muted small" }, t.tombstoneExplain));
    if (this._tombstones === null) {
      section.appendChild(h("button", { class: "button", onclick: () => this._loadTombstones() }, t.tombstonesShow));
    } else if (!this._tombstones.length) {
      section.appendChild(h("p", { class: "muted" }, t.tombstonesEmpty));
    } else {
      section.appendChild(h("ul", { class: "tombstones" }, this._tombstones.map((item) => h("li", null,
        h("div", { class: "wrap mono" }, item.model_id),
        h("div", null, item.durable ? t.tombstoneDurable : t.tombstoneForget),
        h("div", { class: "muted small" }, `${t.tombstoneDeleted} ${this._date(item.deleted_at)} · ${item.reason}`)))));
    }
    section.appendChild(h("button", { class: "button danger", onclick: () => this._confirmReset() }, t.resetButton));
    return section;
  }

  _autonomy() {
    const t = this.t;
    const wrap = h("div", { class: "grid" });
    const permissions = this._permissions;
    const permissionSection = h("section", { class: "card span" }, h("h2", null, "🤖 ", t.permissionsTitle));
    if (permissions === null) permissionSection.appendChild(h("p", { class: "muted" }, t.loading));
    else if (!permissions.length) permissionSection.appendChild(h("p", { class: "muted" }, t.permissionsEmpty));
    else for (const permission of permissions) permissionSection.appendChild(this._permissionCard(permission));
    wrap.appendChild(permissionSection);

    if (this._bindings) wrap.appendChild(this._bindingSection());

    const muteSection = h("section", { class: "card" }, h("h2", null, "🔕 ", t.mutesTitle));
    const mutes = this._mutes || [];
    if (!mutes.length) muteSection.appendChild(h("p", { class: "muted" }, t.mutesEmpty));
    for (const mute of mutes) {
      muteSection.appendChild(h("div", { class: "sub-card" },
        h("div", { class: "strong" }, t.situation[mute.situation_kind] || mute.situation_kind),
        h("div", { class: "muted small" }, `${t.mutedSince} ${this._date(mute.confirmed_at, false)}`),
        mute.can_remove ? h("button", { class: "button", onclick: () => this._mutate("mutes/remove", { situation_kind: mute.situation_kind }, t.success.unmute) }, t.unmute) : null));
    }
    muteSection.appendChild(h("p", { class: "note" }, t.safetyNote));
    wrap.appendChild(muteSection);

    const features = this._features || this._summary.features;
    const onOff = (value) => (value ? t.features.on : t.features.off);
    wrap.appendChild(h("section", { class: "card" },
      h("h2", null, "🌙 ", t.quietTitle),
      h("p", null, features.quiet_hours ? `${features.quiet_hours}${features.quiet_hours_personal ? ` (${t.quietPersonal})` : ""}` : t.quietNone),
      h("h2", null, "⚙ ", t.features.title),
      this._row(t.features.learning, onOff(features.learning_enabled)),
      this._row(t.features.predictive, onOff(features.predictive_models_enabled)),
      this._row(t.features.habits, onOff(features.habit_discovery_enabled)),
      this._row(t.features.suggestions, onOff(features.suggestions_enabled)),
      this._row(t.features.proactive, onOff(features.proactive_enabled)),
      this._row(t.features.permissions, onOff(features.standing_permissions_enabled)),
      h("p", { class: "muted small" }, t.settingsHint),
      h("p", { class: "strong" }, t.settingsPath),
      this._summary?.is_admin ? h("a", { class: "button", href: "/config/integrations/integration/homeintent" }, t.settingsOpen) : null));
    return wrap;
  }
  _permissionCard(permission) {
    const t = this.t;
    const p = t.permission;
    const conditions = permission.conditions.map((key) => p.condition[key] || key).join(", ");
    return h("div", { class: `sub-card ${permission.revoked || permission.expired ? "inactive" : ""}` },
      h("div", { class: "strong wrap" }, permission.description || p.situation[permission.situation_kind] || permission.situation_kind),
      h("div", { class: "muted" }, p.situation[permission.situation_kind] || permission.situation_kind, conditions ? ` (${conditions})` : ""),
      h("div", null, "→ ", p.operator[permission.operator] || permission.operator),
      this._row(p.entities, permission.entities.map((item) => item.missing ? `${item.label} (${t.entityMissing})` : item.label).join(", ")),
      permission.area_label ? this._row(p.area, permission.area_label) : null,
      this._row(p.createdBy, permission.owned_by_viewer ? p.you : permission.owner_label),
      this._row(p.validUntil, this._date(permission.expires_at, false)),
      this._row(p.usageToday, `${this._num(permission.attempts_today)} / ${this._num(permission.max_per_day)}`),
      permission.revoked ? h("div", { class: "badge tone-bad" }, p.revoked) : permission.expired ? h("div", { class: "badge tone-warn" }, p.expired) : null,
      permission.can_revoke ? h("button", { class: "button danger", onclick: () => this._openDialog({
        title: p.revokeTitle, body: p.revokeBody, confirm: p.revoke, destructive: true,
        onConfirm: () => this._mutate("permissions/revoke", { permission_id: permission.permission_id }, t.success.revoke),
      }) }, p.revoke) : null);
  }

  _traceSection() {
    const t = this.t;
    const traces = this._traces || [];
    const section = h("section", { class: "card" }, h("h2", null, t.tracesTitle));
    if (!traces.length) { section.appendChild(h("p", { class: "muted" }, t.tracesEmpty)); return section; }
    const list = h("ol", { class: "timeline" });
    for (const trace of traces) {
      const effects = (trace.effects || []).slice(0, 8).map((effect) => h("li", { class: "small wrap" },
        effect.label, effect.via ? ` (${t.tracesVia(effect.via, effect.step)})` : ""));
      list.appendChild(h("li", { class: "timeline-item" },
        h("div", { class: "muted small" }, this._date(trace.timestamp)),
        h("div", { class: "strong wrap" }, trace.utterance ? `„${trace.utterance}“` : (trace.targets || []).join(", ")),
        trace.user_present ? null : h("div", { class: "small muted" }, t.tracesUnattended),
        h("div", { class: "wrap" }, (trace.targets || []).join(", ")),
        effects.length ? h("ul", { class: "sequence" }, effects) : null,
        (trace.possible_followups || []).length
          ? h("div", { class: "small muted wrap" }, `${t.tracesFollowups} ${trace.possible_followups.join(", ")}`) : null));
    }
    section.appendChild(list);
    return section;
  }

  _bindingSection() {
    const t = this.t;
    const data = this._bindings;
    const section = h("section", { class: "card span" }, h("h2", null, "🔗 ", t.bindingsTitle));
    const level = data.implicit_action_level;
    section.appendChild(h("h3", null, t.implicitTitle));
    if (data.can_change_level) {
      const select = h("select", { class: "select", "aria-label": t.implicitTitle,
        onchange: (event) => this._mutate("settings/implicit_action_level", { level: event.target.value }, t.implicitLevels[event.target.value]) },
        (data.implicit_action_levels || []).map((value) => h("option", value === level ? { value, selected: "" } : { value }, t.implicitLevels[value] || value)));
      section.appendChild(h("div", { class: "entry-select" }, select));
    } else {
      section.appendChild(h("p", null, t.implicitLevels[level] || level));
    }
    const bindings = data.bindings || [];
    if (!bindings.length) section.appendChild(h("p", { class: "muted" }, t.bindingsEmpty));
    for (const binding of bindings) {
      section.appendChild(h("div", { class: "sub-card" },
        h("div", { class: "strong wrap" }, `${binding.kind_label}: „${binding.key_label}“ → ${binding.target_label}`),
        h("div", { class: "muted small" }, `${t.bindingScope[binding.scope] || binding.scope} · ${t.bindingUses(binding.uses)} · ${t.bindingState[binding.state] || binding.state}`),
        binding.can_remove ? h("button", { class: "button danger", onclick: () => this._mutate("bindings/remove", { binding_id: binding.binding_id }, t.bindingRemove) }, t.bindingRemove) : null));
    }
    return section;
  }

  _activity() {
    const t = this.t;
    const wrap = h("div", { class: "stack" });
    if (this._traces !== null) wrap.appendChild(this._traceSection());
    const records = this._history;
    if (records === null) { wrap.appendChild(h("p", { class: "muted" }, t.loading)); return wrap; }
    if (!records.length) { wrap.appendChild(h("section", { class: "card" }, h("p", { class: "muted" }, t.activityEmpty))); return wrap; }
    const list = h("ol", { class: "timeline" });
    for (const record of records) {
      const reasons = record.reasons.map((code) => t.reasons[code]).filter(Boolean);
      list.appendChild(h("li", { class: "card timeline-item" },
        h("div", { class: "muted small" }, this._date(record.timestamp)),
        h("div", { class: "strong wrap" }, record.subject_label || t.situation[record.situation_kind] || record.situation_kind),
        h("div", { class: "muted" }, t.situation[record.situation_kind] || record.situation_kind),
        h("div", null, t.decision[record.decision] || record.decision, " ", t.channel[record.channel] || "",
          record.recipient_is_viewer ? ` ${t.toYou}` : record.recipient_label ? ` ${t.to(record.recipient_label)}` : ""),
        record.result ? h("div", { class: "small" }, t.result[record.result] || record.result) : null,
        reasons.length ? h("div", { class: "small muted" }, `${t.reason}: ${reasons.join(", ")}`) : null,
        record.acknowledgement ? h("div", { class: "small" }, `${t.ack}: ${record.acknowledgement}`) : null));
    }
    wrap.appendChild(list);
    if (this._historyCursor !== null && this._historyCursor !== undefined) {
      wrap.appendChild(h("button", { class: "button", onclick: () => this._loadMoreHistory() }, t.loadMore));
    }
    return wrap;
  }

  // -- confirmation dialogs
  _confirmAction(action, model) {
    const t = this.t;
    const ref = model.ref;
    if (action === "forget") {
      this._openDialog({ title: t.forgetTitle, body: t.forgetBody, confirm: t.forgetConfirm, destructive: true,
        onConfirm: () => this._mutate("models/forget", { ref }, t.success.forget, "/knowledge") });
    } else if (action === "reject_habit") {
      this._openDialog({ title: t.rejectHabitTitle, body: t.rejectHabitBody, confirm: t.rejectHabitConfirm, destructive: true,
        onConfirm: () => this._mutate("habits/reject", { ref }, t.success.reject_habit, "/knowledge") });
    } else if (action === "accept_habit") {
      this._call("habits/preview", { ref }).then((preview) => {
        const steps = h("ol", { class: "sequence" }, preview.steps.map((step) => h("li", null, step.entity_label, " → ", this._expected(step.expected))));
        this._openDialog({ title: t.acceptHabitTitle, body: t.acceptHabitBody, extra: steps, confirm: t.acceptHabitConfirm,
          onConfirm: () => this._mutate("habits/accept", { ref }, t.success.accept_habit) });
      }).catch((err) => this._showToast(this._errorText(err), true));
    } else if (action === "confirm_preference") {
      const preferred = model.preference_choices.find((choice) => choice.preferred);
      this._openDialog({ title: t.confirmPrefTitle, body: t.confirmPrefBody(preferred ? preferred.entity_label : "?", model.subject_label),
        confirm: t.confirmPrefConfirm, onConfirm: () => this._mutate("preferences/confirm", { ref }, t.success.confirm_preference) });
    } else if (action === "reject_preference") {
      this._openDialog({ title: t.rejectPrefTitle, body: t.rejectPrefBody, confirm: t.rejectPrefConfirm, destructive: true,
        onConfirm: () => this._mutate("preferences/reject", { ref }, t.success.reject_preference) });
    }
  }
  _confirmReset() {
    const t = this.t;
    this._openDialog({ title: t.resetTitle, body: t.resetBody, confirm: t.resetConfirm, destructive: true, typeWord: t.resetWord,
      onConfirm: () => this._mutate("models/reset", { confirm: true }, (result) => t.success.reset(result.deleted)) });
  }
  _openDialog({ title, body, confirm, destructive, onConfirm, extra, typeWord }) {
    const t = this.t;
    this._dialogReturnFocus = this.shadowRoot.activeElement;
    const close = () => {
      this._dialogHost.replaceChildren();
      this._dialogReturnFocus?.focus?.();
    };
    const confirmButton = h("button", { class: `button ${destructive ? "danger" : "primary"}`, disabled: Boolean(typeWord),
      onclick: () => { close(); onConfirm(); } }, confirm);
    const input = typeWord ? h("input", { type: "text", class: "search", autocomplete: "off", "aria-label": t.resetTypeHint(typeWord),
      placeholder: typeWord, oninput: (event) => { confirmButton.disabled = event.target.value.trim() !== typeWord; } }) : null;
    const cancelButton = h("button", { class: "button", onclick: close }, t.cancel);
    const dialog = h("div", { class: "dialog", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "dialog-title", "aria-describedby": "dialog-body" },
      h("h2", { id: "dialog-title" }, title),
      h("p", { id: "dialog-body" }, body),
      extra || null,
      typeWord ? h("label", { class: "small" }, t.resetTypeHint(typeWord), input) : null,
      h("div", { class: "dialog-actions" }, cancelButton, confirmButton));
    const backdrop = h("div", { class: "backdrop", onclick: (event) => { if (event.target === backdrop) close(); } }, dialog);
    backdrop.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key === "Tab") {
        const focusable = [...dialog.querySelectorAll("button, input")].filter((el) => !el.disabled);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = this.shadowRoot.activeElement;
        if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
      }
    });
    this._dialogHost.replaceChildren(backdrop);
    (input || cancelButton).focus();
  }
}

const STYLES = `
:host {
  display: block;
  height: 100%;
  background: var(--primary-background-color, #fafafa);
  color: var(--primary-text-color, #212121);
  font-family: var(--paper-font-body1_-_font-family, var(--ha-font-family-body, Roboto, system-ui, sans-serif));
  -webkit-text-size-adjust: 100%;
}
* { box-sizing: border-box; }
.root { display: flex; flex-direction: column; height: 100%; min-width: 0; }
.toolbar {
  display: flex; align-items: center; gap: 8px;
  min-height: 56px; padding: 4px 16px; padding-top: max(4px, env(safe-area-inset-top));
  padding-left: max(16px, env(safe-area-inset-left)); padding-right: max(16px, env(safe-area-inset-right));
  background: var(--app-header-background-color, var(--primary-color, #03a9f4));
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
}
.heading h1 { font-size: 20px; font-weight: 400; margin: 0; line-height: 1.2; }
.subtitle { font-size: 13px; opacity: 0.85; }
.icon-button {
  min-width: 44px; min-height: 44px; border: none; background: transparent; color: inherit;
  font-size: 22px; border-radius: 50%; cursor: pointer;
}
.tabs {
  display: flex; overflow-x: auto; gap: 4px; padding: 0 8px; scrollbar-width: none;
  background: var(--app-header-background-color, var(--primary-color, #03a9f4));
  position: sticky; top: 0; z-index: 2;
}
.tabs::-webkit-scrollbar { display: none; }
.tab {
  flex: 0 0 auto; min-height: 44px; padding: 0 14px; border: none; background: transparent; cursor: pointer;
  color: var(--app-header-text-color, var(--text-primary-color, #fff)); opacity: 0.8; font-size: 15px;
  border-bottom: 3px solid transparent;
}
.tab[aria-selected="true"] { opacity: 1; border-bottom-color: var(--app-header-text-color, var(--text-primary-color, #fff)); font-weight: 500; }
.content {
  flex: 1; overflow-y: auto; overflow-x: hidden; padding: 16px;
  padding-left: max(16px, env(safe-area-inset-left)); padding-right: max(16px, env(safe-area-inset-right));
  padding-bottom: max(24px, env(safe-area-inset-bottom));
}
.grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
.stack { display: flex; flex-direction: column; gap: 12px; max-width: 900px; margin: 0 auto; min-width: 0; }
@media (min-width: 720px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .span { grid-column: 1 / -1; } }
@media (min-width: 1180px) { .grid.cards { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.card {
  background: var(--ha-card-background, var(--card-background-color, #fff));
  color: var(--primary-text-color, #212121);
  border-radius: var(--ha-card-border-radius, 12px);
  border: 1px solid var(--divider-color, rgba(0,0,0,0.12));
  padding: 16px; min-width: 0; overflow-wrap: anywhere;
}
h2 { font-size: 18px; margin: 4px 0 8px; font-weight: 500; }
h3 { font-size: 16px; margin: 0 0 8px; font-weight: 500; }
h4 { font-size: 14px; margin: 12px 0 4px; font-weight: 500; }
p { margin: 6px 0; line-height: 1.45; }
.muted { color: var(--secondary-text-color, #727272); }
.small { font-size: 13px; }
.strong { font-weight: 500; }
.wrap { overflow-wrap: anywhere; word-break: break-word; }
.mono { font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12px; }
.hero-number { font-size: 22px; font-weight: 500; }
.hero-list { list-style: none; padding: 0; margin: 8px 0 0; line-height: 1.8; }
.tone-ok { color: var(--success-color, #2e7d32); }
.tone-info { color: var(--info-color, var(--primary-color, #0288d1)); }
.tone-warn { color: var(--warning-color, #b26a00); }
.tone-bad { color: var(--error-color, #c62828); }
.badge {
  display: inline-flex; align-items: center; gap: 2px; padding: 2px 10px; border-radius: 999px; font-size: 13px;
  border: 1px solid currentColor; font-weight: 500;
}
.button, .list-link, .attention-item, .chip, .model-card { font: inherit; }
.button {
  display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 0 16px;
  border-radius: 22px; border: 1px solid var(--divider-color, rgba(0,0,0,0.2)); cursor: pointer; text-decoration: none;
  background: transparent; color: var(--primary-color, #03a9f4); font-weight: 500; max-width: 100%;
}
.button.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: transparent; }
.button.danger { color: var(--error-color, #c62828); border-color: var(--error-color, #c62828); }
.dialog .button.danger { background: var(--error-color, #c62828); color: var(--text-primary-color, #fff); }
.button:disabled { opacity: 0.45; cursor: not-allowed; }
.button.text { border: none; padding: 0 4px; align-self: flex-start; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible, [tabindex]:focus-visible {
  outline: 3px solid var(--primary-color, #03a9f4); outline-offset: 2px;
}
.list-link {
  display: flex; justify-content: space-between; align-items: center; width: 100%; min-height: 48px; padding: 0 4px;
  border: none; border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.08)); background: transparent;
  color: inherit; cursor: pointer; text-align: start; gap: 8px;
}
.list-link-end { display: inline-flex; gap: 12px; align-items: center; color: var(--secondary-text-color, #727272); }
.count { font-weight: 500; color: var(--primary-text-color, #212121); }
.attention-item {
  display: flex; flex-direction: column; align-items: flex-start; width: 100%; min-height: 48px; padding: 8px 10px;
  margin: 6px 0; border-radius: 8px; border: 1px solid var(--divider-color, rgba(0,0,0,0.12));
  background: transparent; color: inherit; cursor: pointer; text-align: start;
}
.attention-item.sev-warning { border-left: 4px solid var(--warning-color, #b26a00); }
.attention-item.sev-action { border-left: 4px solid var(--primary-color, #03a9f4); }
.attention-kind { font-weight: 500; }
.attention-subject { color: var(--secondary-text-color, #727272); overflow-wrap: anywhere; }
.filters { display: flex; flex-direction: column; gap: 8px; }
.search {
  width: 100%; min-height: 44px; padding: 0 12px; font: inherit; font-size: 16px; border-radius: 8px;
  border: 1px solid var(--divider-color, rgba(0,0,0,0.2));
  background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color, #212121);
}
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  min-height: 36px; padding: 4px 12px; border-radius: 18px; cursor: pointer;
  border: 1px solid var(--divider-color, rgba(0,0,0,0.2)); background: transparent; color: inherit;
}
.chip[aria-pressed="true"] { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: transparent; }
.chip.static { cursor: default; font-size: 13px; }
.model-card { display: flex; flex-direction: column; gap: 6px; text-align: start; cursor: pointer; width: 100%; }
.card-title { font-size: 16px; font-weight: 500; display: flex; min-width: 0; }
.card-status { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 4px 0; }
.headline { font-weight: 500; }
.card-footer { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }
.details-link { color: var(--primary-color, #03a9f4); }
.detail-title { display: flex; font-size: 20px; }
.row { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.06)); }
.row-label { color: var(--secondary-text-color, #727272); }
.row-value { text-align: end; font-weight: 500; overflow-wrap: anywhere; min-width: 0; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
.sequence { margin: 0; padding-inline-start: 22px; line-height: 1.7; }
.evidence-list, .tombstones { list-style: none; padding: 0; margin: 0; }
.evidence-list li, .tombstones li { padding: 8px 0; border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.08)); }
details summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-weight: 500; }
.sub-card { padding: 12px 0; border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.08)); display: flex; flex-direction: column; gap: 4px; }
.sub-card .button { align-self: flex-start; margin-top: 6px; }
.sub-card.inactive { opacity: 0.6; }
.note { background: var(--secondary-background-color, rgba(0,0,0,0.04)); padding: 8px 10px; border-radius: 8px; }
.timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.message.error { border-color: var(--error-color, #c62828); }
.entry-select { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.entry-select select { min-height: 44px; font: inherit; border-radius: 8px; padding: 0 8px; }
.admin { border-style: dashed; }
.admin .button { margin-top: 8px; }
.backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: flex-end; justify-content: center;
  z-index: 10; padding: 0;
}
.dialog {
  width: 100%; max-width: 520px; max-height: 90vh; overflow-y: auto;
  background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color, #212121);
  border-radius: 16px 16px 0 0; padding: 20px 16px; padding-bottom: max(20px, env(safe-area-inset-bottom));
}
@media (min-width: 600px) { .backdrop { align-items: center; padding: 16px; } .dialog { border-radius: 16px; } }
.dialog label { display: flex; flex-direction: column; gap: 6px; margin: 12px 0; }
.dialog-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.toast-host { position: fixed; left: 0; right: 0; bottom: max(16px, env(safe-area-inset-bottom)); display: flex; justify-content: center; pointer-events: none; z-index: 20; padding: 0 16px; }
.toast {
  pointer-events: auto; max-width: 520px; padding: 12px 16px; border-radius: 8px;
  background: var(--primary-text-color, #323232); color: var(--primary-background-color, #fff);
}
.toast.error { background: var(--error-color, #c62828); color: #fff; }
`;

if (!customElements.get("homeintent-learning-center")) {
  customElements.define("homeintent-learning-center", HomeIntentLearningCenter);
}
