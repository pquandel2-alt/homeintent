# HomeIntent 7.9.0 – Überwachungsaufträge vollständig

Auftrag: `sim/PROMPT_UEBERWACHUNG_7.9.md` (Zweig `claude/sleepy-meitner-xd7oux`).
Voraussetzung 7.8.3 ist umgesetzt (`docs/umsetzung-7.8.3.md`).

Leitlinie: Für jede Lücke gibt es entweder eine **korrekte** Überwachung oder
eine **ehrliche** Antwort, die sagt, was fehlt und was der Nutzer tun kann.
Nichts wird still verworfen, nichts ist „nur so ähnlich“.

## Wo welche Überwachung läuft

| Art | Laufort | Warum |
|---|---|---|
| Zustände, Dauer, Gruppen, Gesamtzustände (W1), Inaktivität (W2), Leistung/Verbrauch (W4), Wiederholen/Eskalieren (W5), Bezug über zwei Sätze (W6) | **Home-Assistant-Automation** | HA drückt die Bedeutung ohne neue Helfer aus; sie ist in HA sichtbar (Beschreibung = gesprochene Vorschau) und läuft ohne HomeIntent. |
| Änderung um einen Betrag in einem Zeitraum (W3) | **HomeIntent-Monitor-Runtime** (V10 `MonitorGoalStore`, Zustellung über `agent_delivery`) | HA bräuchte dafür neue Helfer (derivative/trend); die legt HomeIntent nicht ungefragt an. Die Vorschau sagt: „Das überwache ich selbst; es läuft, solange HomeIntent läuft.“ |
| „etwas Ungewöhnliches“ (W7) | V12-Situationserkennung (bestehend) | Nur die Situationen, die der Katalog wirklich kennt. |

Die Vorschau sagt ausdrücklich, wie oft gemeldet wird, wie lange und bis zu
welcher Obergrenze wiederholt wird, wer die Nachricht bekommt, wo die
Überwachung läuft und was ein Neustart bewirkt (`for`-Timer, `last_changed`,
laufende Wiederholungen, HomeIntent-Runtime).

## Wellen

| Welle | Status | Kern |
|---|---|---|
| W1 Gesamtzustände | umgesetzt | „alle Fenster zu“, „kein Licht mehr an“ (= alle aus), „das letzte Fenster geht zu“: Auslöser über alle Mitglieder, Bedingung „alle im Zustand“ (HA-State-Condition mit Liste). „niemand zuhause“/„alle weg“ allein = die letzte Person geht. Anzahl in der Vorschau, leere Menge und Mischarten fragen nach, Gesamtzustand mit Dauer oder Wert ehrlich abgelehnt. |
| W2 Inaktivität | umgesetzt | „12 Stunden keine Bewegung“, „zwei Tage nicht geöffnet“: Ruhezustand mit `for`. „bis 10 Uhr keine Bewegung“: Zeit-Auslöser plus typisierte Bedingung `UNCHANGED_TODAY` (Template nur aus Entity-ID und Zustand). Fehlender Melder, fehlende Uhrzeit, nur Leistungssensor: ehrliche Antwort. Neustart-Satz bei jedem Inaktivitäts-Auslöser. |
| W3 Änderungen und Raten | umgesetzt | `rate_monitor.py`, `MonitorGoalRuntime.async_process_value_change`: bei jeder Zustandsänderung Werte des Fensters aus dem Recorder, Vergleich mit Maximum/Minimum, höchstens eine Meldung je Fenster. Ohne Zeitraum Rückfrage „In welchem Zeitraum?“; Grad nur bei Temperatursensoren, Prozent nur bei %-Sensoren. Keine HA-Helfer. |
| W4 Verbrauch, Zähler, Leistung | umgesetzt | Leistung mit Dauer = numeric_state mit `for`; W/kW/Wh/kWh getrennt und umgerechnet; „heute/diese Woche“ nur mit Verbrauchszähler passenden Zyklus (`meter_period`), sonst Antwort mit dem fehlenden Helfer; kein Rechnen aus Gesamtzählern; Menge ≠ Leistung. |
| W5 Wiederholen und Eskalieren | umgesetzt | `ActionType.REPEAT` (Intervall, Fortsetzungsbedingung, Obergrenze 12, nie schneller als 1/min) → HA `repeat: while`; `ActionType.ESCALATE` → `wait_template` mit Timeout, nur ein Timeout meldet der zweiten Person. Ohne Ereignis wird „Nur jetzt oder jedes Mal?“ gefragt. Ein gesprochenes Intervall wird nie verworfen. |
| W6 Bezug über zwei Sätze | umgesetzt | „Überwache das Garagentor.“ → „Wann soll ich mich melden?“ → „Wenn es länger als 10 Minuten offen ist.“ Dialog nur für denselben Nutzer im selben Gespräch, TTL, „Abbrechen“, ein anderer Befehl beendet ihn ohne Nebenwirkung. Pronomen in Geräteaktionen mit derselben Bindung und Genus-Prüfung; Schlösser schaltet nie eine Automation (ehrliche Antwort). |
| W7 „etwas Ungewöhnliches“ | umgesetzt | Katalog der wirklich erkannten Situationen, Zustand der Erkennung, nötige Optionen, Angebot zum Wiedereinschalten stummgeschalteter Hinweise (Ja/Nein). Konkrete Begriffe („wenn Wasser austritt“) gehen den normalen Weg. |
| W8 Überwachungen verwalten | umgesetzt | Auflisten (Automationen + HomeIntent-Überwachungen, Klartext aus der gespeicherten Vorschau), Stoppen (ausschalten), Löschen (mit Bestätigung), Pausieren „bis morgen um 7 Uhr“ mit automatischem Wiedereinschalten; ohne Uhrzeit oder bei mehreren Treffern Rückfrage. |

## Neue Modelltypen und Felder

| Typ / Feld | Bedeutung | Generator / Runtime |
|---|---|---|
| `GroundedEvent.aggregate`, `TriggerTarget.quantifier="all"` | Gesamtzustand (W1) | State-Trigger über alle Mitglieder, State-Condition mit Entitätsliste |
| `TriggerModel.absent_state` | Zustand, der ausblieb (W2) | Ruhezustand mit `for` |
| `ConditionType.UNCHANGED_TODAY` | seit Mitternacht unverändert (W2) | `unchanged_today_template()` (nur Entity-IDs, geschlossenes Zustandsvokabular) |
| `EventRoles.change`, `RelativeChange`, `RateRule`, `GoalTrigger(kind="value_change", …)` | Änderung um Δ im Fenster (W3) | `MonitorGoalRuntime.async_process_value_change`, `history_query.async_get_numeric_samples` |
| `ValueUnit.WATT/KILOWATT/WATT_HOUR/KILOWATT_HOUR` | Leistung und Energie (W4) | numeric_state mit Umrechnung in die Sensor-Einheit |
| `ActionType.REPEAT` (`if_condition`, `then_steps`, `delay_seconds`, `max_repeats`) | Wiederholung (W5) | `repeat: while` + `repeat.index <= N` + `delay`, `mode: restart` |
| `ActionType.ESCALATE` (`wait_condition`, `timeout_seconds`, `then_steps`) | Eskalation (W5) | `wait_template` + `continue_on_timeout` + `if not wait.completed` |
| `AutomationModel.situation_parts`, `.notes`, `.ask_start` | Vorschau-Ehrlichkeit, „nur jetzt oder jedes Mal“ | Vorschau, Rückfrage |
| `OutcomeKind.MONITOR`, `MonitorProposalResult`, `DialogTaskKind.MONITOR_CONFIRMATION` | HomeIntent-Überwachung vor dem „Ja“ | `controllers/monitoring.py` |
| `DialogTaskKind.MONITOR_EVENT`, `UNUSUAL_OPT_IN`, `MONITOR_DELETE` | W6, W7, W8 | `controllers/monitoring.py` |
| `CanonicalEvent.quantifier_all/absent_state/change_delta/change_window_seconds`, `CanonicalEventNotification.repeat_interval_seconds/max_repeats/escalation_seconds/escalation_recipient` | kanonische Bedeutung | `tests/test_monitoring_79_canonical.py` |

## Vorher → nachher (je zwei Beispielsätze pro Welle, Testhaus)

| Satz | vorher (7.8.3) | nachher (7.9.0): Auslöser · Bedingung · Aktion · Laufort |
|---|---|---|
| „Sag mir Bescheid, wenn alle Fenster zu sind.“ | „Alle … beschreibt einen Gesamtzustand … kann ich noch nicht“ | 6 Fenster → off · alle 6 off · Push „Alle 6 Fenster sind geschlossen.“ · HA |
| „Melde dich, sobald kein Licht mehr an ist.“ | „kein passendes Gerät für Licht“ | 24 Lichter → off · alle 24 off · Push · HA |
| „Melde dich, wenn sich im Flur 12 Stunden nichts bewegt.“ | „kein Gerät ‚nichts‘“ | Bewegungsmelder Flur off für 12 h · – · Push, Neustart-Hinweis · HA |
| „Warne mich, wenn Oma bis 10 Uhr keine Bewegung im Flur hatte.“ | nicht zugeordnet | 10:00 · Melder off und seit Mitternacht unverändert · Push, Hinweis „erkenne nur Bewegung, nicht wer“ · HA |
| „Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.“ | „relative Änderung … noch nicht“ | jede Änderung von „Temperatur Keller“ · max(1 h) − aktuell ≥ 3 °C · Push, höchstens 1/h · HomeIntent |
| „Warne mich, wenn die Luftfeuchtigkeit im Bad in 10 Minuten um 20 Prozent steigt.“ | „relative Änderung … noch nicht“ | jede Änderung · aktuell − min(10 min) ≥ 20 % · Push · HomeIntent |
| „Sag mir Bescheid, wenn der Stromverbrauch heute über 10 kWh liegt.“ | „kein passendes Gerät für Stromverbrauch“ | ehrlich: Verbrauchszähler-Helfer mit täglichem Zyklus auf „Energiezähler“ fehlt (mit Zähler: numeric_state > 10) |
| „Melde dich, wenn die Leistung der Waschmaschine länger als 5 Minuten über 3000 Watt liegt.“ | „kein passendes Gerät für Leistung“ | numeric_state > 3000 W für 5 min · – · Push · HA |
| „Erinnere mich alle 10 Minuten, bis das Garagentor zu ist.“ | „nicht verstanden“ | Rückfrage „Nur jetzt oder jedes Mal …?“ → Tor → open · – · repeat (Push, 10 min) solange offen, max. 12 · HA |
| „Melde dich, wenn die Haustür offen ist, und wenn sie nach 15 Minuten immer noch offen ist, sag Anna Bescheid.“ | „kein passendes Gerät für Anna“ | Haustür → on · – · Push an mich; warten bis zu, nach 15 min Push an Anna · HA |
| „Überwache das Garagentor.“ → „Wenn es länger als 10 Minuten offen ist.“ | „nicht verstanden“ | Tor open für 10 min · – · Push · HA |
| „Überwache die Stehlampe und schalte sie aus, wenn sie länger als 2 Stunden an ist.“ | nicht erkannt | Stehlampe on für 2 h · – · Stehlampe aus · HA |
| „Melde dich, wenn etwas Ungewöhnliches passiert.“ | „nicht zugeordnet“ | Katalog der V12-Situationen, Zustand, Optionen / Wiedereinschalten |
| „Sag mir Bescheid, wenn im Haus was Komisches ist.“ | „nicht zugeordnet“ | wie oben |
| „Welche Überwachungen laufen?“ | „Frage erkannt, Ziel nicht gefunden“ | Liste in Klartext (Automationen + HomeIntent) |
| „Pausiere die Garagen-Meldung bis morgen um 7 Uhr.“ | „nicht verstanden“ | ausgeschaltet, dauerhafter Fortsetzungsauftrag 07:00 |

## Geänderte Test-Erwartungen

| Test | Änderung | Begründung |
|---|---|---|
| `tests/eval/automation_v72/dev_more_c.txt:154`, `dev_notification.txt:198`, `dev_more_b.txt:76` | `unsupported` → `auto: … ; if: all state(…)` | W1: „alle Fenster/Rollläden …“ ist jetzt ein korrekter Gesamtzustand statt einer Ablehnung (Kommentar in der Datei). |
| `tests/_automation_eval.py` | Signatur markiert Gesamtbedingungen als `all state(…)` | Damit eine Gesamtbedingung nie mit dem Zustand einer einzelnen Entität verwechselt wird. |
| `tests/test_condition_model.py` | `UNCHANGED_TODAY` in der Typenmenge | neuer typisierter Bedingungstyp (W2), Kommentar im Test |
| `tests/test_action_model.py` | `REPEAT`, `ESCALATE` in der Typenmenge | neue typisierte Aktionen (W5), Kommentar im Test |

## Tests

Je Welle eine Datei `tests/test_monitoring_79_w<N>.py` mit kombinatorischen
Paraphrasen, Negativ- und Mehrdeutigkeitsfällen und – für jede erzeugte
Automation – einer Nachbildung der Home-Assistant-Auswertung
(`tests/_ha_sim.py`: Trigger mit `for`, Listen, `or/and/not`, Zeit, das
Inaktivitäts-Template, `repeat: while`, `wait_template`, `sequence`, auf einer
Zeitachse). Dazu `tests/test_monitoring_79_canonical.py`.

## Gates und Live-Testbett

Siehe README, Abschnitt „Geprüfter Release-Stand von Version 7.9.0“.

Der vollständige Live-Lauf ergab zunächst 185/186: In `m79-w5-repeat` zählte
die Prüfung „noch offen“ auch die V12-Proaktivmeldung „Das Garagentor ist noch
offen. Soll ich es schließen?“ mit (die Automation selbst schickte korrekt zwei
Erinnerungen). Der Runner kennt dafür jetzt `notify_exact`; das Szenario prüft
exakt den Erinnerungstext und besteht in der Wiederholung.

## Bekannte Grenzen

- W3 läuft nur, solange HomeIntent läuft; nach einem Neustart prüft es ab der
  nächsten Änderung (Recorder liefert das Fenster).
- W3-Überwachungen lassen sich ausschalten und löschen, aber nicht zeitlich
  pausieren.
- W2 „bis U nicht“: Ein Neustart von Home Assistant am selben Tag zählt als
  Änderung (die Vorschau sagt es).
- W4 rechnet nie aus Gesamtzählern; ohne Verbrauchszähler-Helfer gibt es nur
  die ehrliche Antwort.
- W5: Wiederholung höchstens 48-mal (Standard 12), nie schneller als einmal
  pro Minute; ein Neustart bricht eine laufende Wiederholung ab.
- W6: Schlösser schaltet keine Automation; die Überwachung meldet stattdessen.
- W7 kennt nur die vier Situationsarten der V12-Erkennung (Gerät fertig nur
  mit konfigurierten Geräten).
- W8 findet Überwachungen über ihre Geräte und ihren Wortlaut; bei mehreren
  Treffern wird gefragt.
