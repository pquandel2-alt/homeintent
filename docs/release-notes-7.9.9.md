# HomeIntent 7.9.9

7.9.9 stabilisiert die Ereignisverarbeitung. Der Funktions- und
Sicherheitsvertrag bleibt unverändert: HomeIntent arbeitet weiterhin ohne LLM
oder ML-Modell zur Laufzeit; Grounding, Berechtigungen, Bestätigungen,
`NEVER_AUTO`, `ExecutionPolicy` und `ServiceExecutor` bleiben verbindlich.

## EventRuntime

- Ein expliziter Queue-/Lease-/Finalize-Lebenszyklus hält entnommene Events
  bis zur Bestätigung als Live-Arbeit. Scheitert der HouseView-Aufbau oder ein
  anderer Batchschritt, werden nur die noch nicht bestätigten Einträge in
  FIFO-Reihenfolge zurückgestellt.
- Pro Generation existiert höchstens ein Supervisor-Task. Wiederholte Fehler
  verwenden begrenzten exponentiellen Backoff und einen Circuit-Zustand;
  neue Events oder der Retry-Timer können die Verarbeitung wieder wecken.
- Queue-, Lease-, Retention-, Retry-, Circuit- und Recovery-Zustände besitzen
  getrennte Diagnosezähler. Unload und Generationenwechsel verwerfen alte
  geleaste Arbeit, statt sie in die nächste Generation zu übertragen.

## Lang laufende Zustände

- Eine Schwelle von `0` deaktiviert `long_running_state` vollständig, sofern
  kein anderer Consumer das Ereignis benötigt.
- Ein gemeinsamer, auf 4096 Intervalle begrenzter Scheduler löst die Prüfung
  auch ohne ein weiteres `state_changed` aus. Es gibt keinen Task pro Entity.
- Die Aktivitätsregeln sind domainspezifisch und konservativ. Climate nutzt
  `hvac_action`; Vacuum erkennt `cleaning` und `returning`. Die vollständige
  Tabelle und die bewusst nicht unterstützte Water-Heater-Semantik stehen in
  der [Agenten-Konfiguration](agent-configuration.md#langläufer-signale).

## Zeitzonen und Fehlertexte

- Der HA-Teststub besitzt eine explizite Standardzeitzone. UTC,
  `Europe/Berlin`, Sommer-/Winterzeit und beide DST-Übergänge werden geprüft;
  das CI-Gate läuft zusätzlich unter zwei Host-`TZ`-Werten.
- Unerwartete Provider- und Serviceausnahmen bleiben mit Traceback im Log.
  Assist und persistente benutzerseitige Fehler erhalten eine stabile,
  verständliche Meldung ohne Pfade, Tokens oder ungefilterte Ausnahmetexte.

## Freigabestatus

Diese Notiz beschreibt den Draft-Stand. Der Branch wird nicht gemergt,
getaggt oder veröffentlicht, bevor alle verpflichtenden lokalen und
Home-Assistant-CI-Gates tatsächlich grün sind.
