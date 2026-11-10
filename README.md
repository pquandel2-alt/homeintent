# HomeIntent

**Lokale, deterministische Sprachsteuerung für Home Assistant Assist – ohne LLM oder ML-Modell zur Laufzeit.**

- Aktuelle Version: **7.14.0**
- Sprache: Deutsch
- Mindestversion: Home Assistant **2026.4.0**
- Installation: HACS
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

1. In **HACS → Integrationen** nach HomeIntent suchen.
2. HomeIntent installieren und Home Assistant neu starten.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen**
   HomeIntent hinzufügen.
4. In der gewünschten Assist-Pipeline HomeIntent als Conversation Agent wählen.

HomeIntent gehört seit dem 4. November 2026 zur HACS-Standardliste. Nur bei
einem noch nicht aktualisierten HACS-Katalog ist vorübergehend das manuelle
Repository `https://github.com/pquandel2-alt/homeintent` nötig.

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
- Antwortdetails (`response_detail_level`): `concise` blendet bei reinen
  Erfolgen redundante Geräteanzahlen aus, `detailed` nennt sie weiterhin;
  Teilfehler, Rückfragen, Statusauskünfte und Sicherheitsangaben bleiben in
  beiden Modi vollständig. Neue Installationen starten mit `concise`,
  bestehende Installationen ohne gespeicherten Wert bleiben `detailed`,
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

Die optionale [Music-Assistant-Unterstützung](docs/music-assistant.md) nutzt
ausschließlich registrierte Home-Assistant-Aktionen. Direkte Medien-URLs
öffnet HomeIntent nicht.

## Bekannte Grenzen

- Die produktive NLU ist deutsch und kein universeller Freitext-Chatbot.
- Nicht belegte Raumbeziehungen, Gerätefähigkeiten oder Entity-Zuordnungen
  werden nicht erfunden.
- Kalender- und Automationssprache deckt dokumentierte Strukturen ab, nicht
  jede denkbare natürliche Formulierung.
- Der neue Slotdialog deckt unvollständige zeitliche, solare und die
  dokumentierten Ereignisformen ab. Weitere wiederkehrende und komplex
  verschachtelte Fragmente laufen noch über die bestehenden
  Compatibility-Dialoge oder werden sicher abgelehnt.
- Offene Slotentwürfe sind absichtlich flüchtig und werden nach Neustart nicht
  wieder aufgenommen.
- Music Assistant ist automatisiert gegen echten Home-Assistant-Komponentencode
  und mit Service-Fakes geprüft; ein echter MA-Server samt Audiowiedergabe ist
  nur durch die dokumentierte manuelle Matrix abgedeckt.
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

## Version 7.14.0 – Zustandsgebundenes Undo und sichere Medienfortsetzungen

Geprüfter Release-Stand von Version 7.14.0 ist ausschließlich der im
[GitHub-Release v7.14.0](https://github.com/pquandel2-alt/homeintent/releases/tag/v7.14.0)
ausgewiesene Commit mit seinen erfolgreichen CI- und Nightly-Läufen. Die
Korpusbaseline
[`corpus-signatures-7.14.0.json`](docs/perf/corpus-signatures-7.14.0.json)
schützt die deterministischen Sprachsignaturen dieses Stands.

Geräte-Undo verlangt jetzt für jedes Ziel einen unveränderten beobachteten
Nachzustand. Medienfortsetzungen führen Player, Quelle, Medientyp und
Warteschlangenmodus typisiert zusammen. Mit `response_detail_level` lassen
sich redundante Geräteanzahlen bei reinen Erfolgen ausblenden, ohne
Teilresultate, Fehler, Rückfragen oder Sicherheitsangaben zu verkürzen.
Details und Prüfgrenzen stehen in den
[Release Notes](docs/release-notes-7.14.0.md) und im
[Umsetzungsbericht](docs/umsetzung-next-reliability.md).

Geprüfter Release-Stand von Version 7.13.3 ist der commitfest geprüfte Stand
`0fb881c9e00959bedeb97ce0e3733887b3b8235e`. CI und Nightly waren für exakt
diesen Commit vollständig grün. Die zugehörige versionierte Korpusbaseline ist
[`corpus-signatures-7.13.3.json`](docs/perf/corpus-signatures-7.13.3.json).
Tag und [GitHub-Release v7.13.3](https://github.com/pquandel2-alt/homeintent/releases/tag/v7.13.3)
zeigen auf diesen Commit.

## Version 7.13.4 – Zuverlässigkeitskorrekturen

Geprüfter Release-Stand von Version 7.13.4 ist ausschließlich der im
[GitHub-Release v7.13.4](https://github.com/pquandel2-alt/homeintent/releases/tag/v7.13.4)
ausgewiesene Commit mit seinen erfolgreichen CI- und Nightly-Läufen. Bis das
Release-Gate diese Nachweise bestätigt, ist der Stand ein Release-Kandidat.
Die Korpusbaseline [corpus-signatures-7.13.4.json](docs/perf/corpus-signatures-7.13.4.json)
übernimmt die unveränderten Vergleichssignaturen von 7.13.3; Qualitätsgrenzen
wurden nicht gelockert. Änderungen und Grenzen stehen in den
[Release Notes](docs/release-notes-7.13.4.md).

Die unabhängige Nachprüfung hat Lücken in Medienfortsetzungen, gemeinsamer
Vorprüfung, Batch-Teilergebnissen und Dialoggedächtnis nachgewiesen. Der aktuelle
Arbeitsstand korrigiert diese Pfade; Details und Prüfgrenzen stehen im
[Nachprüfungsbericht](docs/reliability-7133-review.md). Mehrbefehle prüfen alle
Klauseln vorab und stoppen nach einem Ausführungsfehler weitere Klauseln.
Bereits angenommene Geräteaktionen können dabei nicht atomar zurückgenommen
werden. Angenommene Medienaufrufe ohne Wirkungsevidenz werden nicht als
bestätigte Wiedergabe ausgegeben. Die automatisierten Serviceprüfungen belegen
keine reale Audiowiedergabe oder Hardwarewirkung.

## Version 7.13.3

7.13.3 schließt die in der unabhängigen Nachprüfung von 7.13.2 verbliebenen
Lücken:

- **Verbindliche Klauselabdeckung:** `und`, zeitliche Fortsetzungen,
  `sowie`/`außerdem`, Komma, Semikolon und Satzgrenzen verwenden dasselbe
  Strukturmodell. Kein nachgelagerter Router darf einen abgelehnten Rest als
  globale Ersatzaktion neu auslegen.
- **Exakte Medien-Mehrbefehle:** Eine bekannte `source_list`-Quelle wird mit
  Operation, Player und Raum klausellokal materialisiert. Erst nach der
  semantischen Bindung und Policy-Prüfung laufen die Service Calls in
  gesprochener Reihenfolge. Die darüber hinaus notwendige gemeinsame
  Verfügbarkeits-/Executor-Vorprüfung und ehrliche Teilfehlerauswertung fehlten
  in 7.13.3 noch; siehe die nachträgliche Korrektur oben. Unbekannte oder mehrdeutige Quellen verwerfen den
  gesamten Auftrag.
- **Cover-Pflichtbestätigung:** Die Pflicht für nicht unabhängige bewegliche
  Covers ist unabhängig von `confirmation_level`. Sie gilt auch hinter
  Gruppen, Szenen und Skripten; nach „Ja“ werden effektive Dienste, Ziele und
  Parameter erneut gebunden.
- **Anwesenheit ist kein Haus-Scope:** „Ich/Wir bin/sind zu Hause“ bleibt eine
  Anwesenheitsaussage. Echte Hausquantoren erweitern nur innerhalb des
  gesprochenen Gerätetyps; Fenster, Garagentore und Rollläden bleiben getrennt.
- **Nightly-Sicherheitslauf:** Die neuen Clause-, Cover-, Orts- und
  Mediendialogregressionen laufen mit größerem Profil und einem protokollierten,
  reproduzierbaren Seed.

Details, Root Causes, Testergebnisse und eine manuelle Testmatrix für echte
Hardware stehen in den [Release Notes](docs/release-notes-7.13.3.md) und im
[Umsetzungsbericht](docs/umsetzung-7.13.3.md).

Für Version 7.13.3 sind die verpflichtenden GitHub-Prüfungen (`ci.yml` und
`nightly-live.yml`) für den exakten Release-Commit erfolgreich abgeschlossen;
die geprüften Lauf-Links stehen im GitHub-Release.

Die vollständigen Prüfergebnisse und historischen Details stehen ausschließlich
in den [Release Notes](docs/release-notes-7.13.3.md), im
[Versionsarchiv](docs/releases/README.md) und bei
[GitHub Releases](https://github.com/pquandel2-alt/homeintent/releases).

## Weiterführende Dokumentation

- [Ausführliches Benutzer- und Entwicklerhandbuch](docs/handbuch.md)
- [Agent-Konfiguration](docs/agent-configuration.md)
- [Music Assistant und gespeicherte Radiosender](docs/music-assistant.md)
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
