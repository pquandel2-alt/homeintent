# Proaktiver HomeIntent-Agent

HomeIntent kann lokale Home-Assistant-Zustände über seine bestehenden,
nativen HA-Automationen beobachten und daraus proaktive Meldungen erzeugen.
Der gesamte Pfad bleibt deterministisch, ohne LLM, Cloud oder zusätzliche
Laufzeitabhängigkeiten.

## Architektur

```text
gesprochene Automationsanweisung
  -> bestehende Automation-AST und Validierung
  -> native HA-Automation in automations.yaml
  -> homeintent.proactive_message
  -> AgentEvent (atomare Sidecar-Persistenz)
  -> Deduplizierung, Cooldown und Policy
  -> Push und/oder TTS
  -> mobile_app_notification_action
  -> frischer Entity-Snapshot + Policy + gemeinsamer ServiceCallPlan-Executor
```

HomeIntent betreibt keine zweite State-/Timer-Engine. State-, Numeric-State-,
Zeit-, Sonnen- und Kalenderauslöser bleiben Aufgaben der Home-Assistant-
Automation Engine. Von HomeIntent erzeugte Regeln werden in
`automations.yaml` gespeichert, der HA-Kategorie **HomeIntent** zugeordnet und
sind deshalb unter **Einstellungen > Automationen & Szenen > Automationen**
sichtbar. Dort können sie aktiviert, deaktiviert, bearbeitet und gelöscht
werden. Die Sprachbefehle für Auflisten, Pausieren, Fortsetzen, Ändern und
Löschen bleiben ebenfalls verfügbar.

## Einrichtung

Unter **Einstellungen > Geräte & Dienste > HomeIntent > Konfigurieren**:

- **Proaktives Agentenverhalten aktivieren** schaltet den kompletten
  Agentenpfad ein oder aus. Bei ausgeschaltetem Schalter werden weder neue
  AgentEvents noch Push-/TTS-Ausgaben oder Notification Actions verarbeitet.
- **Ausgabekanäle** erlaubt Push, TTS oder beide Kanäle.
- **Push-Ziele** wählt eine oder mehrere `notify.*`-Entities. Ohne Auswahl
  fällt Push lokal und ohne Interaktionsbuttons auf eine persistente
  Home-Assistant-Benachrichtigung zurück.
- **TTS-Engine** wählt eine `tts.*`-Entity.
- **TTS-Lautsprecher oder Sprachsatelliten** wählt die Media Player für
  `tts.speak`.
- **Standard-Cooldown** verhindert wiederholte Meldungen derselben
  Regelsituation.
- **Zur Steuerung berechtigte Benutzer** gilt auch für Notification Actions.
  HomeIntent übernimmt dafür die authentifizierte Benutzer-ID aus dem
  Home-Assistant-Eventkontext. Fehlt sie bei aktiver Benutzer-Allowlist, wird
  sichtbar abgelehnt und nichts ausgeführt.
- Kritische Aktionen benötigen immer einen authentifizierten Benutzer. Für
  authentifizierte Nicht-Administratoren sind sie standardmäßig deaktiviert.

Kein Ziel ist im Code fest verdrahtet.

## Regeln per Sprache

Die vorhandene Automationssprache bleibt der Einstieg. Ein verifizierter
Beispielsatz ist:

> Kannst du mich benachrichtigen, wenn die Außentemperatur 28 Grad beträgt?

HomeIntent zeigt wie bisher eine Vorschau und verlangt die Bestätigung zur
Automationserstellung. Nach der Bestätigung wird eine normale HA-Automation
angelegt. Eine zieloffene Benachrichtigungsaktion wird an den Agenten
gebunden und respektiert anschließend die konfigurierten Push-/TTS-Kanäle.
Sprachlich erzeugte, zieloffene Benachrichtigungsregeln verwenden bewusst
`INFORM`. Ein ausführbares `ASK` oder `AUTO` muss als fortgeschrittene Regel
im HA-Action-Editor vollständig und explizit konfiguriert werden.

Benachrichtigungen an ein im Satz eindeutig genanntes `notify.*`-Ziel bleiben
bewusst eine direkte Automation. Sie sind eine explizite Benutzeranweisung
und werden nicht nachträglich auf andere Agentenkanäle umgeroutet.

## AgentEvent-Lifecycle

Konkrete Situationen werden in `homeintent_agent_events.json` atomar gespeichert;
eine vorhandene Legacy-Datei wird beim ersten V10-Start atomar übernommen.
Mögliche Zustände sind:

- `ACTIVE`
- `NOTIFIED`
- `ACKNOWLEDGED`
- `SNOOZED`
- `RESOLVED`
- `EXPIRED`
- `DENIED`
- `FAILED`

Der Deduplizierungsschlüssel kombiniert die Regel-ID mit einem ausdrücklich
gesetzten Suffix, andernfalls mit der beobachteten Entität oder `global`.
Aktive, zugestellte oder snoozende Situationen erzeugen keine zweite Meldung.
Abgeschlossene Situationen beachten anschließend den konfigurierten Cooldown.

## Push-Aktionen und Snooze

**Welche Ziele Buttons tragen können (seit 7.1.2):** Home Assistant adressiert
`notify.*`-*Entities* über `notify.send_message`. Diese Aktion akzeptiert nur
`message` und `title`; jeder weitere Schlüssel (`data` mit Tag oder Buttons)
wird von Home Assistant abgelehnt. HomeIntent sendet an Entities deshalb
ausschließlich Text und Titel. Aktionsbuttons gibt es nur für eine bestätigte
Bindung an einen Legacy-Dienst `notify.mobile_app_<gerät>` (Feld
`notification_services` von `homeintent.bind_user_context`). Ist ein
V12-Vorschlag an eine Entity gebunden, kommt er als normale Push-Nachricht
ohne Buttons und ohne Aktions-Token an und bleibt per Sprache oder im Learning
Center beantwortbar. Das gilt auch für die unter **Push-Ziele** gewählten
Entities der Regel-Benachrichtigungen.

Interaktive Push-Meldungen enthalten abhängig vom bereits bestimmten Modus:

- **Ausführen** bei `ASK` und einer validierten vorgeschlagenen Aktion,
- **Ignorieren**,
- **In 30 Minuten erinnern**.

Action IDs tragen nur eine zufällige AgentEvent-ID. Sie enthalten weder
Entity-ID noch Serviceparameter. Beim Ausführen lädt HomeIntent das Event,
beansprucht es atomar gegen Doppelklicks, baut frische Entity-Snapshots und
prüft Ziel und Execution Policy erneut.
Dabei wird die vom Companion-Event getragene HA-Benutzer-ID ausgewertet. Eine
konfigurierte Benutzer-Allowlist, Admin-only- und Read-only-Ziele sowie die
CRITICAL-Policy gelten deshalb unverändert auch für Push-Taps. Ablehnungen und
Servicefehler werden als eigene Lifecycle-Zustände gespeichert und über die
konfigurierten Kanäle sichtbar zurückgemeldet.
Wenn Home Assistant für das konfigurierte Notify-Ziel und das eingehende
Companion-Event eine Geräte-ID bereitstellt, muss auch diese zum ursprünglich
benachrichtigten Gerät passen. Fehlt die plattformabhängige Geräte-ID, bleibt
die authentifizierte Benutzer- und Policy-Prüfung maßgeblich.

Snooze verwendet eine vorhandene, persistente HomeIntent-One-Shot-Automation.
Nach 30 Minuten wird die Situation erneut geprüft. Ist der beobachtete
Zustand beziehungsweise numerische Grenzwert inzwischen verschwunden, wird
keine alte Nachricht wiederholt. Bei einer von HomeIntent erzeugten Regel
wird außerdem die ursprüngliche HA-Automation mit `skip_condition: false`
erneut angestoßen. Damit werden auch deren aktuellen Presence-, Wetter-,
Zeit- und sonstigen Conditions erneut durch Home Assistant geprüft.

Eine über TTS gestellte `ASK`-Frage kann außerdem mit „Ja“ oder „Nein“ in
Assist beantwortet werden. HomeIntent akzeptiert die Kurzantwort nur, wenn
kein normaler Conversation-Dialog offen ist und genau eine unexpired,
tatsächlich per TTS zugestellte Agentenfrage existiert. Bei mehreren offenen
Fragen wird niemals geraten.

## INFORM, ASK und AUTO

- `INFORM` liefert nur die Meldung aus.
- `ASK` bietet eine Aktion an und führt sie erst nach einer gültigen
  Notification Action oder einer eindeutigen TTS-Bestätigung aus.
- `AUTO` darf nur einen von der normalen Execution Policy erlaubten Plan mit
  niedrigem Risiko ausführen. Mittlere, hohe oder kritische Risiken werden
  automatisch auf `ASK` herabgestuft.

Zusätzlich muss AUTO global aktiviert und jede Ziel-ID ausdrücklich in der
AUTO-Allowlist stehen. Neue Config Entries starten mit deaktiviertem AUTO und
leerer Allowlist. Nur reversible Ein/Aus- beziehungsweise begrenzte
Öffnen/Schließen-Operationen sind Kandidaten. Erinnerung oder statistische
Wiederholung erweitert diese Liste niemals.

Konfigurierte `state_changed`-Kategorien laufen vor der Ausgabe durch die
zentrale Ereignisnormalisierung, Situationsbewertung und die gemeinsame
`ProactiveDecisionEngine`. Diese prüft in fester Reihenfolge Qualität und
Alter, Deduplizierung/Cooldown, Relevanz, Erreichbarkeit/Kanal sowie erst
danach eine optional vorgeschlagene, geschlossene Aktion und die Execution
Policy. Ohne konfigurierte
Kategorie bleibt dieser Listener ausgabeseitig still. Routinebewertung ist
separat opt-in und beginnt erst nach der konfigurierten Mindestzahl von
Beobachtungen; neue Muster werden nur informiert oder angeboten. Explizites
Feedback (`hilfreich`, `unnötig`, `falsch`, `künftig ignorieren`, `später erneut
fragen`) bleibt eine inhaltsarme Entscheidungshistorie. `ignorieren` und
`später` unterdrücken ausschließlich Hinweise und können keine
Aktionsberechtigung freischalten.

Der öffentliche HA-Action-Editor zeigt `homeintent.proactive_message` auch für
fortgeschrittene Regeln an. Öffentliche ASK-/AUTO-Aufrufe benötigen einen
authentifizierten Administrator. Von HomeIntent erzeugte Automationen können
ohne Benutzerkontext auslösen, tragen dafür aber einen zufälligen, im Sidecar
gespeicherten Nachweis, der exakt an Automation, Aktion und Zielmenge gebunden
und beim Löschen widerrufen wird. Der Aufrufer kann dieses Vertrauensmerkmal
nicht frei setzen. Generische Unlock- und Alarm-Disarm-Vorschläge sind
gesperrt. Jede ASK-Ausgabe nennt zusätzlich zum Kontexttext die tatsächliche
Aktion, Ziele und Parameter.

Vorgeschlagene Aktionen werden durch eine geschlossene Service-Allowlist
geprüft; freie `domain.service`-Ausführung ist nicht möglich. Derselbe
Validator läuft beim Erzeugen, beim Neustart und unmittelbar vor der
Ausführung eines persistierten Plans. Target-Felder sind in `action_data`
verboten; Nutzdaten können das geprüfte Ziel daher nicht ersetzen oder
erweitern. Ablauf, Replay, veränderte Nachricht/Aktion/Ziele, unbekannte
Push-Geräte und inzwischen nicht mehr freigegebene Entitäten führen ohne
Serviceaufruf zur Ablehnung. `ha_nlu.proactive_message` verwendet denselben
Handler und ist kein Kompatibilitäts-Bypass.

## Erwartete Wirkung

Der gemeinsame Executor registriert nach bekannten Zustandsdiensten eine
zeitlich begrenzte Erwartung pro stabiler Entity-ID. Ein passendes
`state_changed` erfüllt sie. Bei Ablauf erzeugt die ausdrücklich aktivierte
Kategorie `expected_effect_missing` eine knappe INFORM-Meldung mit erwartetem
und beobachtetem Zustand. Sie enthält keine Aktion und wiederholt den
ursprünglichen Dienst nicht. Ein neuer Dienst für dieselbe Entität ersetzt die
ältere Erwartung; Unload entfernt Listener und Timer.
