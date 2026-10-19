# HomeIntent

**Lokale, deterministische Sprachsteuerung für Home Assistant Assist – ohne LLM oder ML-Modell zur Laufzeit.**

- Aktuelle Version: **7.9.11**
- Sprache: Deutsch
- Mindestversion: Home Assistant **2026.4.0**
- Installation: HACS Custom Repository
- Verarbeitung: lokal in Home Assistant
- Lizenz: [MIT](LICENSE)

[![HomeIntent in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pquandel2-alt&repository=homeintent&category=integration)
[![GitHub Release](https://img.shields.io/github/v/release/pquandel2-alt/homeintent)](https://github.com/pquandel2-alt/homeintent/releases/latest)
[![Website](https://img.shields.io/badge/Website-Live--Demo-5eead4?logo=github&logoColor=white)](https://pquandel2-alt.github.io/homeintent/)

## Was ist HomeIntent?

HomeIntent ist ein deutschsprachiger Conversation Agent für Home Assistant
Assist. Er analysiert Befehle, Fragen und Automationswünsche lokal, löst Ziele
gegen die echte Home-Assistant-Konfiguration auf und führt Änderungen erst
nach Capability-, Berechtigungs-, Risiko- und Bestätigungsprüfung aus.

HomeIntent ist bewusst kein allgemeiner Chatbot. Gleicher Hauszustand und
gleicher Dialogkontext führen zum gleichen Ergebnis. Bei Mehrdeutigkeit oder
unvollständiger Bedeutung fragt HomeIntent nach oder führt nichts aus.

## Voraussetzungen

- Home Assistant 2026.4.0 oder neuer
- HACS für die empfohlene Installation
- für Spracheingabe eine eingerichtete Assist-Pipeline
- sinnvoll benannte und für Assist freigegebene Entitäten

GPU, Cloud-API, Modell-Download und externer HomeIntent-Server sind nicht
erforderlich.

## Installation und Update

1. In **HACS → Integrationen → Benutzerdefinierte Repositories** öffnen.
2. `https://github.com/pquandel2-alt/homeintent` als Typ **Integration** hinzufügen.
3. HomeIntent installieren und Home Assistant neu starten.
4. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen**
   HomeIntent hinzufügen.
5. In der gewünschten Assist-Pipeline HomeIntent als Conversation Agent wählen.

Bei Updates über HACS anschließend Home Assistant neu starten. Hinweise für
alte Installationen mit der Domain `ha_nlu` stehen in der
[Migrationsanleitung](docs/homeintent-rename-migration.md).

## Schnellstart

1. Benötigte Geräte, Sensoren, Kalender, Listen und Timer für Assist freigeben.
2. Bereiche, Etagen, Geräteklassen und Friendly Names in Home Assistant prüfen.
3. Unter **HomeIntent → Konfigurieren** Auswahl und Sicherheitsoptionen setzen.
4. Mit einer lesenden Frage beginnen, danach einen eindeutigen Befehl testen.

Beispiele:

```text
Wie warm ist es im Wohnzimmer?
Schalte die Stehlampe ein.
Mach alle Lichter im Erdgeschoss aus, außer die Küchenlampe.
Erinnere mich in 20 Minuten an den Backofen.
Wenn das Garagentor länger als eine Minute offen ist, melde dich.
```

Schreibende Automationen und risikoreiche Änderungen werden vor der Ausführung
als Vorschau bestätigt. Eine Rückfrage ist ein Sicherheitsmerkmal, kein Fehler.

## Zentrale Konfiguration

Die Optionen befinden sich unter **Einstellungen → Geräte & Dienste →
HomeIntent → Konfigurieren**. Dort werden insbesondere festgelegt:

- ausgewählte beziehungsweise von Assist freigegebene Entitäten,
- Benutzer-, Haushalts- und Administratorgrenzen,
- Bestätigungs- und Ausführungsrichtlinien,
- proaktive Kategorien, Kanäle und Aufmerksamkeitsbudgets,
- Langläufer-Schwelle (`0` deaktiviert die Kategorie vollständig),
- Benachrichtigungsziele sowie optionale lokale Adapter.

Die vollständige Optionsreferenz steht in der
[Agent-Konfiguration](docs/agent-configuration.md). Das Learning Center in der
Seitenleiste zeigt Wissen, Modelle, Berechtigungen und Autonomie transparent an.

## Sicherheitsmodell

- Entity Resolution und Grounding erfolgen ausschließlich gegen reale,
  freigegebene Home-Assistant-Entitäten.
- Mehrdeutige oder unvollständige Bedeutungen werden nicht geraten.
- `ExecutionPolicy`, Benutzerrechte, Bestätigungen und `NEVER_AUTO` gelten für
  jeden Schreibpfad.
- Nur `ServiceExecutor` und `AutomationExecutor` bilden Schreibgrenzen.
- Proaktive Vorschläge vergeben keine Rechte und umgehen keine Bestätigung.
- Technische Provider-, Pfad- und Geheimnisdetails werden nicht in
  Assist-Antworten oder dauerhafte Fehlerzustände übernommen; sekundäre
  Fehlerlogs werden nach Möglichkeit ebenfalls redigiert und rate-limitiert.

Details: [Sicherheit und Datenschutz](docs/security-privacy.md).

## Datenschutz

Sprachverständnis und Entscheidungslogik laufen lokal. HomeIntent benötigt
keine HomeIntent-Cloud und speichert keine Prompts bei einem Modellanbieter,
weil zur Laufzeit kein LLM/ML-Modell beteiligt ist. Externe Wirkung entsteht
nur durch vom Benutzer konfigurierte Home-Assistant-Dienste, etwa Kalender-
oder Push-Integrationen; deren eigene Datenschutzregeln gelten weiterhin.

## Bekannte Grenzen

- Die produktive NLU ist deutsch und kein universeller Freitext-Chatbot.
- Nicht belegte Raumbeziehungen, Gerätefähigkeiten oder Entity-Zuordnungen
  werden nicht erfunden.
- Kalender- und Automationssprache deckt dokumentierte Strukturen ab, nicht
  jede denkbare natürliche Formulierung.
- Proaktive Raumansprache benötigt aktuelle Präsenzdaten und eine eindeutige
  Satellitenzuordnung; andernfalls wird konservativ ausgewichen oder nichts
  ausgeführt.
- Unter extremer Event-Überlast bleibt die Queue begrenzt. Drops und
  Degradationszustände werden gemessen; Sicherheitsregeln werden nicht gelockert.

Weitere Grenzen und ausführliche Funktionsbeispiele stehen im
[Handbuch](docs/handbuch.md).

## Fehlerdiagnose

1. Home-Assistant-Protokoll nach `homeintent` filtern.
2. Prüfen, ob die betroffene Entity für Assist freigegeben und eindeutig benannt ist.
3. Bereiche, Geräteklassen, Benutzerbindung und HomeIntent-Optionen kontrollieren.
4. Nach einem Update Home Assistant neu starten und die Integration bei Bedarf
   einmal neu laden.
5. Bei einem Fehler Version, Eingabe, erwartetes Ergebnis und relevante
   anonymisierte Logzeilen angeben; Tokens und private Pfade nicht veröffentlichen.

Ausführlich: [Troubleshooting](docs/troubleshooting.md).

## Version 7.9.11

7.9.11 ist ein Sicherheits- und Datenintegritäts-Patch. Automationen dürfen
nur noch mit eindeutigem HomeIntent-Herkunftsnachweis gelöscht werden;
automatisches Fortsetzen benötigt einen offenen, exakt passenden Pausenauftrag.
Rückgängig prüft Besitzer oder Administrator sowie exakte YAML- und
Metadaten-Digests, bevor es schreibt. Thermal-Persistenz blockiert den
Home-Assistant-Event-Loop nicht mehr. Details und Migrationsgrenzen stehen in
den [Release Notes](docs/release-notes-7.9.11.md).

Geprüfter Release-Stand von Version 7.9.11: Die verpflichtenden GitHub-Prüfungen
müssen für den exakten Release-Commit erfolgreich abgeschlossen sein, bevor
das Tag und der GitHub-Release erstellt werden.

Die versionierte Korpusbaseline ist
[`corpus-signatures-7.9.11.json`](docs/perf/corpus-signatures-7.9.11.json).
Die vollständigen Prüfergebnisse und historischen Details stehen ausschließlich
in den [Release Notes](docs/release-notes-7.9.11.md), im
[Versionsarchiv](docs/releases/README.md) und bei
[GitHub Releases](https://github.com/pquandel2-alt/homeintent/releases).

## Weiterführende Dokumentation

- [Ausführliches Benutzer- und Entwicklerhandbuch](docs/handbuch.md)
- [Agent-Konfiguration](docs/agent-configuration.md)
- [Sicherheits- und Datenschutzmodell](docs/security-privacy.md)
- [Proaktiver Agent](docs/proactive-agent.md)
- [Architektur V13](docs/architecture-v13.md) und [Jarvis Core](docs/jarvis-core.md)
- [Learning Center](docs/learning-center.md)
- [Qualitäts- und Release-Gates](docs/quality-checklist.md)
- [Historischer Versionsverlauf](docs/releases/README.md)
- [GitHub Releases](https://github.com/pquandel2-alt/homeintent/releases)

## Entwicklung

```bash
python -m pip install --requirement requirements-dev.txt
python -m pytest -q -W error::RuntimeWarning \\
  -W error::pytest.PytestUnraisableExceptionWarning
python -m pyflakes custom_components tests tests_ha scripts
python -m pyright
```

Echte-HA-Tests verwenden die getrennten Abhängigkeiten aus
`requirements-ha-test.txt`. CI, Benchmarks, Live-Testbett und Nightly-Gates
sind in [der Qualitätscheckliste](docs/quality-checklist.md) beschrieben.

Beiträge sollen deterministisch bleiben, Mehrdeutigkeit nie erraten, Bedeutung
und Ausführung trennen und jedes neue Verhalten durch positive, negative und
metamorphische Tests absichern.
