# HomeIntent 7.1 – Learning Center & Knowledge Control

Das Learning Center ist eine **Transparenz- und Kontrollschicht** über den
bestehenden V11/V12-Autoritäten. Es ist **kein** zweites Lernsystem: Es
speichert kein Wissen, lernt nichts, berechnet keine neuen Konfidenzen und
führt keine Geräteaktionen aus.

Es beantwortet fünf Fragen – ohne interne JSON-Dateien oder Modell-IDs:

1. Was hat HomeIntent gelernt?
2. Warum hat HomeIntent das gelernt?
3. Wie verlässlich bzw. bestätigt ist dieses Wissen?
4. Wie kann ich es korrigieren oder entfernen?
5. Was darf HomeIntent ohne Rückfrage tun?

## Zugang

- **Seitenleiste → 🧠 HomeIntent** (URL-Pfad `/homeintent`).
- Keine YAML-Konfiguration, kein `panel_custom:`, keine Lovelace-Ressource und
  nichts unter `www/`: Die Integration registriert Panel und Datei selbst.
- Bei mehreren HomeIntent-Einträgen gibt es genau **einen** Seitenleisten-
  Eintrag; oben im Panel erscheint dann eine Auswahl der Instanz.
- Die Konfiguration bleibt in **Einstellungen → Geräte & Dienste → HomeIntent
  → Konfigurieren**. Home Assistant bietet für benutzerdefinierte Integrationen
  keinen unterstützten Weg, auf der Integrationsseite einen eigenen Knopf zu
  einem Panel anzuzeigen, ohne den normalen Optionsdialog zu ersetzen
  (`config_panel_domain` würde „Konfigurieren“ auf das Panel umleiten).
  HomeIntent patcht das Frontend deshalb nicht; die Optionsbeschreibung nennt
  das Learning Center und seinen Pfad.

Assist kann die Oberfläche nicht navigieren. Auf „Was hast du gelernt?“
antwortet HomeIntent wie bisher und verweist zusätzlich auf das Learning
Center.

## Reiter

| Reiter | Inhalt |
|---|---|
| **Übersicht** | Anzahl gelernter Modelle, gültig / lernt noch / zu prüfen, Kategorien, Aufmerksamkeitspunkte, Autonomie, Kommunikation, Aktivität der letzten 24 h |
| **Wissen** | Alle für dich sichtbaren Modelle, Suche (Name, Gerät, Raum, Art), Filter nach Art und Status, Detailansicht, „Woher weißt du das?“, Aktionen; für Administratoren „Verwaltung“ |
| **Autonomie** | Daueranweisungen (anzeigen, erklären, widerrufen), stummgeschaltete Hinweise, Sicherheitshinweis, Ruhezeit, Lernfunktionen und wo sie eingestellt werden |
| **Aktivität** | V12-Proaktivverlauf, neueste zuerst, 40 Einträge, „Weitere laden“ |

## Modellarten

Angezeigt werden nur tatsächlich gespeicherte Modelle. `DURATION`, `ENERGY`,
`BATTERY_TREND` und `FACT` existieren nur als Schema-Plätze; sie erzeugen keine
leeren Kategorien.

| Art | Kategorie | Wofür HomeIntent es tatsächlich verwendet |
|---|---|---|
| `RELIABILITY` | Geräte | Beurteilung, wie zuverlässig beobachtete Gerätewirkungen waren. Keine Ausführungserlaubnis. |
| `EFFECT_TIMING` | Geräte | Anpassung der Wartezeit auf eine Wirkung (`EffectMonitor`) und Erkennung ungewöhnlich langer Reaktionen. Beobachtete Werte, keine Garantie. |
| `THERMAL_MODEL` | Heizung | Abschätzung, wann ein Temperaturziel starten muss und ob es verspätet ist. |
| `PREFERENCE` | Präferenzen | Nur **bestätigt** und nur für den Eigentümer im gespeicherten Kontext: Auflösung mehrdeutiger Namen. |
| `HABIT` | Gewohnheiten | Kann als Routine vorgeschlagen werden. Wird **nie** automatisch ausgeführt. |

### Gesundheit (`ModelHealth`) und Wissensstand (`KnowledgeState`)

Beides sind verschiedene Konzepte und werden getrennt angezeigt. Der
Hauptstatus einer Karte wird deterministisch abgeleitet:

| Zustand | Anzeige |
|---|---|
| `INVALID` oder Invalidierungsgrund | Ungültig |
| `DRIFT_DETECTED` | Verhalten geändert |
| `STALE` oder `expires_at` überschritten | Veraltet |
| `UNRELIABLE` | Unzuverlässig |
| `CONFIRMED` | Bestätigt („Von dir bestätigt“) |
| `LOW_CONFIDENCE` | Lernt noch (kein Fehler, keine Warnung) |
| `VALID` + `INFERRED` | Vermutet |
| `VALID` + `OBSERVED` | Gültig |
| unbekannter künftiger Wert | „Unbekannter Zustand“ (kein Absturz) |

### Qualitätswert

Der V11-Konfidenzwert ist ein **Qualitätsmaß der Datenlage, keine
kalibrierte Wahrscheinlichkeit**. Das Panel zeigt ihn als Stufe (sehr niedrig
… hoch) und den Zahlenwert nur unter „Technische Details“. Warnungen entstehen
nie allein aus dem Qualitätswert, sondern nur aus explizitem Modellzustand.

### Zuverlässigkeit – Zählweise

Es zählen ausschließlich von Home Assistant **angenommene** Aktionen mit
**verifizierter** Wirkung (V11-Semantik). Beispiel: 8 bestätigte Wirkungen,
1 ausgebliebene, 10 bereits erfüllte Zustände (No-ops) und 3 unverifizierte
Läufe ergeben **9** beobachtete Aktionen und **88,9 %** – nicht 22.

## „Woher weißt du das?“

Die Belegansicht wird nur auf ausdrückliche Anforderung geladen.

- Zuverlässigkeit/Reaktionszeit: Die Provenienz-IDs des Modells werden
  ausschließlich über den bestehenden `ExperienceStore` aufgelöst
  (`async_get_many`, Lesen und Filtern im Worker-Thread). Standard 10, maximal
  50 Einträge; gezeigt werden Zeitpunkt, Gerät, Aktion, Wirkungsstatus und
  Reaktionszeit.
- Heizmodell: Anzahl abgeschlossener, nicht kontaminierter Heizzyklen und die
  vorhandenen Validierungsmetriken.
- Gewohnheit: Belege und vergleichbare Gelegenheiten.
- Präferenz: Anzahl der Auswahlen bzw. ausdrückliche Beibringung.

Nie angezeigt werden Äußerungen, Audio, Transkripte oder
Home-Assistant-Zustandsabbilder. Einzelne Erfahrungen können in 7.1 weder
bearbeitet noch gelöscht werden; vergessen wird immer das resultierende
Modell.

## Wissen kontrollieren

Alle Änderungen laufen über geschlossene, typisierte WebSocket-Befehle zu
denselben Funktionen, die auch Assist nutzt (`learning_control.py`). Keine
dieser Aktionen erzeugt einen Geräte-Serviceaufruf.

| Aktion | Autorität | Semantik |
|---|---|---|
| Modell vergessen | `ModelRegistry.async_delete(…, suppress=True)` + `PredictiveHouseModel.forget` | Normales FORGET: Modell entfernt, Tombstone schützt vor Wiederherstellung aus **alten** Belegen; spätere neue Erfahrungen können nach V11-Regeln neues Wissen bilden. Rohdaten werden nicht umgeschrieben. Eine globale bestätigte Präferenz entfernt auch ihre Alias-Regel. |
| Alle Lernmodelle zurücksetzen (nur Admin) | `ModelRegistry.async_reset()` + `PredictiveHouseModel.clear()` | Nur V11-Modelle. Daueranweisungen, Stummschaltungen, Ruhezeiten, Automationen, Benutzerbindungen, Raumsensoren und V12-Verlauf bleiben unverändert. Erfordert Eingabe von „ZURÜCKSETZEN“ und `confirm: true`. |
| Präferenz bestätigen (nur Eigentümer) | `async_confirm_preference` | `INFERRED → CONFIRMED`, gleicher Gültigkeitsbereich (Benutzer, Bereich, Entität); identisch zur Sprachbestätigung. |
| Präferenz verwerfen (nur Eigentümer) | `async_reject_preference` | bleibt unverbindlich, wird nicht mehr vorgeschlagen. |
| Als Routine übernehmen (nur Eigentümer) | `routine_from_habit_model` + `ProfileStore.async_save_routine(confirmed=True)` | Das Panel zeigt die typisierte Vorschau; nach Bestätigung wird die Routine gespeichert, **nicht** ausgeführt. |
| Nicht mehr vorschlagen (nur Eigentümer) | `ModelRegistry.async_delete_with_reason(…, REJECTED_HABIT)` | Dauerhafte Unterdrückung genau dieser Gewohnheit; Tombstone-GC entfernt sie nie. |
| Daueranweisung widerrufen | `StandingPermissionStore.revoke` + `async_persist` | Eigentümer oder Administrator. Danach `permission_revoked` in der `AutoExecutionPolicy`. |
| Stummschaltung aufheben | `AttentionStateStore.unmute` + `async_persist` | Nur eigene Stummschaltungen. |

Ein Wiederherstellen von Tombstones gibt es in 7.1 nicht. Neue
Daueranweisungen werden weiterhin nur im Gespräch erstellt und bestätigt.
Kritische Sicherheitswarnungen können nicht stummgeschaltet werden.

## Privatsphäre und Berechtigungen

Die Sichtbarkeit wird **serverseitig** aus dem gespeicherten Gültigkeitsbereich
bestimmt (nie aus Namen, nie aus Browserangaben). Der Browser sendet keine
Benutzer-ID; maßgeblich ist ausschließlich `connection.user`.

| Klasse | Beispiele | Sehen | Bestätigen/Übernehmen | Vergessen |
|---|---|---|---|---|
| PERSONAL | Präferenz (`context.user_id`), Gewohnheit (`subject`) | Eigentümer; Admin nur als Verwaltungseintrag **ohne Inhalt** (Name des Eigentümers, Status) | nur Eigentümer | Eigentümer, Admin |
| HOUSEHOLD | Heizmodell, Reaktionszeit, Zuverlässigkeit | alle angemeldeten Benutzer | – | Admin |
| SYSTEM | sonstige Arten | Admin | – | Admin |

- Fremde persönliche Modelle werden nicht „ausgeblendet“, sondern vom Backend
  gar nicht erst geliefert; direkte Befehle mit fremden Referenzen enden mit
  `not_found`/`not_authorized` und werden (inhaltsfrei, Akteur gehasht)
  protokolliert.
- Technische Modell-IDs, Tombstones und Unterdrückungsarten sieht nur ein
  Administrator. Karten verwenden eine undurchsichtige Referenz.
- Daueranweisungen: Eigentümer und Administratoren.
- Stummschaltungen: nur der jeweilige Benutzer.
- Aktivität: identisch zur V12-Regel – persönliche (und sensible) Einträge
  nur für den Empfänger, ohne Admin-Ausnahme.

## Live-Aktualisierung

Das Panel abonniert `homeintent/learning_center/subscribe`. Das Ereignis
enthält nur `{entry_id, revision}`. Die Revision ist ein reiner
Laufzeitzähler; sie steigt bei jedem Schreibvorgang der `ModelRegistry`
(Lernen, Sprache, Panel) und bei jedem Persistieren des V12-Zustands
(Daueranweisungen, Stummschaltungen, Verlauf). Das Panel lädt danach seine
eigene, autorisierte Ansicht neu. Kein Polling, kein Bus-Event, keine
Recorder-Einträge.

## WebSocket-API (Version 1)

Alle Befehle: `homeintent/learning_center/<name>`, optional `entry_id`
(Pflicht bei mehreren Einträgen). Jede Antwort enthält `api_version: 1`
(außer Mutationen/Abonnement). Fehlercodes sind stabil:
`not_found`, `not_authorized`, `wrong_owner`, `invalid_state`,
`admin_required`, `unsupported_operation`, `entry_not_found`,
`entry_required`, `unavailable` (plus HA-eigenes `invalid_format`).

| Befehl | Parameter | Antwort |
|---|---|---|
| `entries` | – | `{entries: [{entry_id, title}]}` |
| `summary` | – | `LearningCenterSummary` (Zähler, `features`, `attention[]`, `non_mutable_situations`) |
| `models/list` | `offset` (≥0), `limit` (1–250) | `{total, next_offset, learning_enabled, revision, models: LearningModelListItem[]}` |
| `models/get` | `ref` | `{model: LearningModelDetail}` |
| `models/evidence` | `ref`, `limit` (1–50, Standard 10) | `{evidence: LearningEvidenceSummary}` |
| `models/forget` | `ref` | `{forgotten, ref}` |
| `models/reset` | `confirm: true` | `{deleted}` (nur Admin) |
| `preferences/confirm` / `preferences/reject` | `ref` | `{confirmed}` / `{rejected}` |
| `habits/preview` | `ref` | `{name, steps: [{entity_label, property, expected}]}` |
| `habits/accept` / `habits/reject` | `ref` | `{accepted, routine_name, step_count}` / `{rejected}` |
| `permissions/list` | – | `{permissions: StandingPermissionView[], features}` |
| `permissions/revoke` | `permission_id` | `{revoked}` |
| `mutes/list` | – | `{mutes: MuteView[], non_mutable_situations}` |
| `mutes/remove` | `situation_kind` | `{removed}` |
| `history/list` | `cursor` (≥0), `limit` (1–100, Standard 40) | `{records: HistoryView[], next_cursor}` |
| `tombstones/list` | – | `{tombstones: TombstoneView[]}` (nur Admin) |
| `traces/list` | `limit` (1–200, Standard 40) | `{traces: TraceView[]}` – „Was hat HomeIntent ausgelöst?“ (Admins: Haushalt, sonst nur eigene Ausführungen; seit 7.3.2) |
| `subscribe` | – | Ergebnis `{entry_id, revision}`, danach Ereignisse `{entry_id, revision}` |

Die Felder aller Ansichtsmodelle sind in `learning_center.py` als
`to_dict()` festgelegt und im Panel als JSDoc-Typen dokumentiert. Werte sind
sprachneutral (Schlüssel, Zahlen, aufgelöste Anzeigenamen); das Panel
lokalisiert sie. Es gibt keinen generischen Aktionsbefehl und keine
Möglichkeit, einen Home-Assistant-Dienst anzugeben.

## Technik

- `learning_center.py` – lesende Präsentation, Sichtbarkeit, Attention-Regeln
  (strict Pyright).
- `learning_control.py` – gemeinsame Mutationen für Sprache und Panel
  (strict Pyright).
- `learning_center_ws.py` – Home-Assistant-Anbindung: WebSocket-Befehle,
  `hass.http.async_register_static_paths`, `panel_custom.async_register_panel`,
  Abmeldung des Panels erst nach dem letzten Eintrag.
- `frontend/homeintent-learning-center.js` – ein einzelnes ES-Modul ohne
  Framework, ohne externe Abhängigkeiten, ohne Netzwerkzugriffe außer der
  bestehenden Home-Assistant-WebSocket-Verbindung. Texte werden immer als
  Textknoten eingefügt (kein HTML aus Daten), kein `eval`, kein Tracking.
  Deutsch und Englisch, Zahlen/Daten über `Intl` in Sprache und Zeitzone von
  Home Assistant.
- Cache-Busting: Die Modul-URL enthält den SHA-256-Präfix des Dateiinhalts
  (`…/homeintent-learning-center.js?v=<hash>`), berechnet einmal im Executor.
  Nach einem HACS-Update lädt der Browser die neue Datei ohne manuelles
  Leeren des Caches.
- Keine neue persistente Speicherung. Das Upgrade von 7.0.1 migriert keine
  Daten: Modelle, Tombstones, Daueranweisungen und V12-Verlauf bleiben
  unverändert.
- Kein zusätzlicher Sensor mit Modelldaten, keine Diagnose-Ausgabe
  persönlicher Modelle.

## Mobil und Barrierefreiheit

- Für ~375–430 px entworfen: einspaltige Karten, Touch-Ziele ≥ 44 px
  (Filter-Chips ≥ 36 px), kein horizontales Scrollen der Seite, lange IDs
  brechen um, Safe-Area-Abstände, Dialoge als Bottom-Sheet.
- Tabs mit `role="tablist"`, Pfeil-/Pos1-/Ende-Tasten, sichtbarer Fokus,
  Dialoge mit Fokusfalle und Escape, Status immer als Text plus Symbol.
- Hell/Dunkel über Home-Assistant-Themenvariablen, keine feste Hintergrundfarbe.

Automatische Prüfung (auch in CI, Job „Learning Center mobile UI“):

```bash
python scripts/learning_center_fixtures.py /tmp/lc.json
NODE_PATH="$(npm root -g)" node scripts/check_learning_center_mobile.cjs /tmp/lc.json /tmp/shots
```

Das Skript rendert das echte Panel in Chromium bei 390 × 844 px in Hell und
Dunkel mit echten Backend-Antworten und prüft Überlauf, Touch-Ziele,
erreichbare Löschknöpfe, Dialog, Escape und dass der Browser keine
Benutzer-ID sendet.

## Leistung

`scripts/benchmark_learning_center.py` füllt eine echte `ModelRegistry` bis
zum Modelllimit (1000) und den `ExperienceStore` (5000) und misst
(Referenzlauf 7.1.0):

| Pfad | p95 | Budget |
|---|---|---|
| Übersicht | ~4 ms | 100 ms |
| Liste | ~4 ms | 150 ms |
| Detail | < 1 ms | 150 ms |
| Belege (auf Anforderung, 50 Zeilen) | ~120 ms | 400 ms |

Übersicht, Liste und Detail lesen den `ExperienceStore` nachweislich nie.
