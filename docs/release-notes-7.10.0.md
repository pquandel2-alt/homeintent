# HomeIntent 7.10.0

7.10.0 ist das getrennte Minor-Release für kompositionelles deutsches
Sprachverständnis, sicheren Dialogkontext und optionale Music-Assistant-
Unterstützung. Die Sicherheits- und Datenintegritätskorrekturen wurden bereits
separat als 7.9.11 veröffentlicht und werden hier nicht aufgeweicht.

## Dialog und Sprachverständnis

- Kontextschlüssel kombinieren Conversation-ID mit dem authentifizierten
  Benutzer. Ohne Benutzerbindung wird ein bekannter Sprachsatellit verwendet;
  anonymer Kontext wird nicht zwischen Satelliten geteilt.
- Rückbezüge auf den vorherigen Heizungswert verwenden nur den unmittelbar
  beobachteten, typisierten Stellwert desselben Dialogkontexts. Fehlt dieser
  Nachweis oder ist das Ziel nicht mehr eindeutig, wird nichts ausgeführt.
- Relative Rollladenangaben wie „zehn Prozent weiter hoch“ werden aus Richtung,
  Grad und aktueller Position zusammengesetzt und auf 0 bis 100 begrenzt.
  Ohne aktuelle Position endet die Anfrage geschlossen.
- Kausale Fragen erklären anhand lokaler Ausführungsevidenz, was verstanden
  beziehungsweise warum nichts ausgeführt wurde. HomeIntent erfindet keine
  frühere Geräteaktion.
- Gruppen- und Ortsrückbezüge bleiben typisiert. Eine Korrektur oder ein
  Pronomen übernimmt keinen Kontext eines anderen Benutzers.

Die Umsetzung erweitert Lexeme, Bedeutungsmerkmale, typisierte Slots und
Diskursauflösung. Neue vollständige Beispielsätze sind Regressionstests, nicht
die primäre Produktivlogik. Das Regex-Inventar weist weiterhin 171 semantische
Satzmuster aus; deren Zahl wächst in diesem Release nicht.

## Optionale Music-Assistant-Unterstützung

- HomeIntent erkennt die tatsächlich registrierten Dienste
  `music_assistant.search` und `music_assistant.play_media`. Ohne diese
  Fähigkeiten bleibt die bestehende allgemeine `media_player`-Steuerung aktiv.
- Künstler, Alben, Playlists, Titel und gespeicherte Radiosender werden über
  die Music-Assistant-Bibliothek gesucht. Nur exakt normalisierte, eindeutige
  Namen werden in einen typisierten `MusicAssistantPlan` übernommen.
- Der Plan enthält Medientyp, Suchname, geprüfte Media-ID, Zielplayer,
  Queue-Modus, Auflösungsquelle und Eindeutigkeitsstatus. Erst die bestehende
  Policy- und Executor-Schicht darf daraus einen Schreibaufruf machen.
- Unterstützte Queue-Modi sind Ersetzen, Anhängen und „als Nächstes“.
  Pause, Fortsetzen, Stop, Next, Previous, Lautstärke und Mute verwenden die
  vorhandenen sicheren `media_player`-Pläne.
- Player müssen verfügbar, Music Assistant eindeutig zugeordnet und dem Raum
  sicher auflösbar sein. Mehrere Player oder gleichnamige Medientypen führen zu
  einer Rückfrage, niemals zur Auswahl des ersten Treffers.
- Direkte HTTP-, Datei-, FTP- und Daten-URLs werden verworfen. Bibliotheks-IDs
  müssen einem begrenzten URI-Format entsprechen. Suchfehler liefern eine
  neutrale Meldung ohne Providerdetails und ohne Wiedergabeplan.

Bibliotheksdaten werden absichtlich nicht dauerhaft gecacht. Jede benannte
Wiedergabe fragt den aktuellen Music-Assistant-Bestand ab; entfernte Sender
werden dadurch nach Neustart oder Bibliotheksänderung nicht aus altem Zustand
gestartet. Einrichtung und Grenzen beschreibt
[`docs/music-assistant.md`](music-assistant.md).

## Sicherheit und Kompatibilität

- Die in 7.9.11 eingeführten Herkunfts-, Pause-/Resume-, Rollback-, Besitzer-
  und Event-Loop-Schutzmechanismen bleiben unverändert aktiv.
- NLU und Bibliothekssuche führen keine Geräteaktion direkt aus.
- Mehrdeutige oder unsichere Auflösung endet ohne Schreibplan.
- Der Music-Assistant-Adapter führt keine eigene Netzwerkverbindung ein und
  besitzt keine harte Python-Abhängigkeit auf Music Assistant.
- Es gibt keine automatische unscharfe Radiosenderwahl und keine implizite
  Playergruppenbildung.

## Korpus und Veröffentlichung

Die Baseline umfasst 4.153 Signaturen. Gegenüber 7.9.11 sind 46 Test- und
Dokumentationssätze neu, keiner entfällt. Die zwei bewusst veränderten
Verhaltenssignaturen betreffen ausschließlich die zuvor fälschlich absolut
behandelten relativen Rollladenangaben; Details stehen in der
[Korpusbegründung](perf/corpus-signatures-7.10.0-begruendung.md).

Tag und GitHub-Release dürfen erst nach erfolgreichen Pflichtjobs für den
exakten `main`-Commit erstellt werden. Diese Datei behauptet daher keine noch
nicht durchgeführte Veröffentlichung.
