# Umsetzung HomeIntent 7.13.3

Stand: 7. November 2026. Dieser Bericht trennt reproduzierte Fehler,
Teilmodulbefunde, lokale Verifikation und eine spätere Veröffentlichung.

## 1. Geprüfte Ausgangslage

- Repository: `https://github.com/pquandel2-alt/homeintent`
- `origin/main`, lokaler `main` und Tag `v7.13.2` zeigten beim Start auf
  `7a30353ba98d731eae7feae0159b8d572acdbf83`.
- Es lagen keine neueren Upstream-Commits vor. Der Arbeitsbaum war vor den
  Änderungen sauber.
- Der HACS-PR
  [hacs/default#10191](https://github.com/hacs/default/pull/10191) ist seit
  4. November 2026 genehmigt und gemergt.

## 2. Reproduktionen vor der Änderung

### 2.1 Klauselabdeckung

Alle folgenden Varianten erzeugten auf 7.13.2 eine falsche Pause-Vorschau für
Küche, Schlafzimmer und Wohnzimmer; nach „Ja“ wurde mindestens
`media_player.media_pause` für das Küchenradio aufgerufen:

- „Spiel WDR 2 in der Küche, pausiere die Musik im Schlafzimmer“
- „Spiel Quirlomat in der Küche, pausiere die Musik im Schlafzimmer“
- „Spiel WDR 2 in der Küche sowie pausiere die Musik im Schlafzimmer“
- „Spiel WDR 2 in der Küche. Pausiere die Musik im Schlafzimmer“
- „Spiel WDR 2 in der Küche danach pausiere die Musik im Schlafzimmer“

Ursache: Die Klauselauswahl akzeptierte nur einen Teil der strukturellen
Relationen. Wenn die direkte Interpretation keinen vollständigen Plan lieferte,
durfte der Goal-Router die gesamte Eingabe erneut als globale Medienoperation
deuten. „Kein Ergebnis“ und „zulässige fachliche Übergabe“ waren nicht
unterschieden.

### 2.2 Cover-Pflichtbestätigung

Mit `confirmation_level: critical` und `confirmed=False` waren `door`,
`window`, `awning`, `damper`, fehlende und unbekannte Klassen `HIGH`, die
Policy gab sie aber frei und der Service Call lief. `garage` blieb als
`CRITICAL` bestätigt. Über die Conversation-Pipeline öffnete „Öffne das
Terrassenfenster“ ein `window`-Cover ohne Rückfrage.

Ursache: Risikoklasse und Bestätigungspflicht waren dieselbe Schwellenabfrage.
Eine höhere konfigurierbare Schwelle konnte daher die fachlich zwingende
Bestätigung entfernen.

### 2.3 Anwesenheit und Gerätetyp

Die genannten Anwesenheitssätze wurden durch die vollständige 7.13.2-Pipeline
ohne Write abgelehnt; eine reale Haussteuerung war dort nicht reproduzierbar.
`whole_home_phrase` und das Ortslexikon erkannten „zu Hause“ in „Ich bin zu
Hause …“ und „Wir sind zuhause“ intern dennoch als Haus-Scope.

Direkt ausführbar war dagegen die zu breite quantifizierte Cover-Auflösung:
„Öffne zu Hause alle Rollläden“ beziehungsweise typisierte Varianten konnten
fremde Cover-Klassen wie Garage oder Markise aufnehmen. Ursache war ein
Domainfilter ohne erneute Bindung an den gesprochenen Gerätegenus.

## 3. Gemeinsame strukturelle Änderungen

### 3.1 Eine verbindliche Grenze vor Vorschau und Write

`LanguageDocument` enthält nun zusätzlich die Relation `SEQUENCE` für
semantisch prüfbare Satzzeichen- und Satzgrenzen. Die Clause-Bindung kennt die
Zustände `NOT_APPLICABLE`, `COMPLETE`, `DELEGATED`, `CLARIFICATION` und
`REJECTED`. Jede Bindung trägt Operation, Ziele, Ort, Quelle/Wert, belegte
Spannen, Rest und konkrete Pläne.

`Devices` erzwingt diese Prüfung als gemeinsame letzte Grenze für Einzel- und
Mehrpläne. `Goals` darf einen koordinierten unvollständigen oder abgelehnten
Turn nicht neu interpretieren. Die Medienroute verwendet dasselbe Dokument und
dasselbe Planmodell. Bei gemischten Verbindungen wird nach Quellspanne und
nicht nach der internen Reihenfolge der Relationsdetektoren sortiert.

### 3.2 Orthogonale Cover-Policy

`PolicyDecision.mandatory_confirmation` ist unabhängig von `risk`. Die Policy
wertet direkte und transitive Effekte aus. `ConfirmedScope` bindet zusätzlich
zur äußeren Plansignatur die statisch bestimmten effektiven Dienste, Ziele und
Parameter. Dynamische Parameter einer Cover-Bewegung sind nicht sicher
bestimmbar und werden fail-closed behandelt.

Unabhängige Beschattung bleibt batch-fähig und folgt den normalen Schwellen.
Nicht unabhängige oder gemischte Zielmengen bleiben atomar vorgeprüft. Eine
physische Transaktion oder ein Hardware-Rollback wird nicht behauptet.

### 3.3 Anwesenheit und Quantoren

Persönliche Anwesenheitssubjekte und Kopula werden im gemeinsamen Ortsmodell
als Anwesenheitsstruktur erkannt. Der quantifizierte Zielpfad schneidet die
Domainmenge anschließend mit den durch den gesprochenen Genus belegten
Entitäten. Kontakt- und Sensorentitäten werden dabei nicht zu schaltbaren
Geräten.

### 3.4 Medienquellen und Dialog

Eine exakte Quelle wird als `select_source`- beziehungsweise typisierter
Music-Assistant-Plan in ihrer Klausel materialisiert. Der Mehrplan wird erst
nach vollständiger Vorprüfung aller Klauseln an den bestehenden Device-Handler
übergeben. Die Ausführung bleibt geordnet und fail-fast; schon ausgeführte
Schritte werden nicht als zurückgerollt dargestellt.

`PendingMediaCommand` ergänzt den vorhandenen `DialogManager` und
`ConversationContext`; es wurde kein zweites Dialogsystem angelegt. Quelle,
Operation und Queue-Modus bleiben erhalten, während eine Antwort nur den
fehlenden Player/Ort ergänzt.

## 4. Verhalten der Gegenbeispiele

- Bekannte eindeutige Quelle: exakter Start im genannten Raum, danach nur die
  Aktion der zweiten Klausel in deren Raum; keine fremden Player.
- Unbekannte oder mehrdeutige Quelle: vollständige Ablehnung ohne Vorschau,
  Zielmenge oder Service Call; ein nachfolgendes „Ja“ führt nichts aus.
- Drei oder mehr gültige Klauseln: vollständige Vorprüfung und Ausführung in
  Quellreihenfolge.
- Nicht vollständig verstandene Zusatzklausel: kein Teilauftrag bleibt offen.
- Nicht unabhängiges Cover bei `medium`, `high` oder `critical`: ohne
  Bestätigung kein Write; nach „Ja“ erneute vollständige Prüfung.
- Anwesenheitssatz: kein hausweiter Scope. Echte Hausanweisung: nur Geräte des
  gesprochenen Typs; ohne passenden Typ kein Domain-Fallback.

## 5. Test- und Performance-Nachweise

Auf dem dokumentierten Arbeitsstand bestanden:

- fokussierte Abschlussmatrix aus allen zunächst fehlgeschlagenen Bereichen
  sowie Clause-, Medien-, Cover- und Effektgraphregressionen: 473 Tests in
  242,33 s;
- erweiterter Nightly-Sicherheitsjob mit
  `HOMEINTENT_HYPOTHESIS_PROFILE=nightly` und festem Seed 7133: 506 Tests in
  1.822,57 s, kein Gegenbeispiel;
- Sprachverständnis-Sicherheitsgate: 463 Tests in 207,74 s;
- versionierte Entwicklungsbenchmarks: 478/516 Fälle bei 573 Turns und
  103/107 Fälle bei 126 Turns; jeweils 0 unsichere Ausführungen und
  0 Safety-Verstöße;
- Automationssprachkorpora einschließlich Development und Held-out: alle fünf
  Release-Gates bestanden;
- Arbiter zweimal über 2.161 Sätze: 2.156 `EQUIVALENT`, genau fünf
  dokumentierte `BEHAVIOR_CHANGE`; unter dem Warning-Gate 0 RuntimeWarnings;
- EventRuntime-Reproduktion 7.9.8: 0 RuntimeWarnings, 0 Worker-Restarts,
  0 offene Tasks nach Drain/Unload;
- Pyflakes für Integration, Tests und Skripte: ohne Befund;
- Pyright 1.1.411: 0 Fehler, 0 Warnungen, 0 Informationen.

Der abschließende vollständige lokale pytest-Lauf auf diesem Arbeitsstand
bestand mit 10.555 Tests und 12 übersprungenen Tests in 5.259,39 s
(1:27:39). `RuntimeWarning` und
`PytestUnraisableExceptionWarning` waren dabei als Fehler konfiguriert. Echte
Hardware und das reale Nightly-Live-Testbett sind lokal nicht Teil dieser
Fake-/Komponententestumgebung.

Der commitfeste Alt-/Neu-Korpusvergleich verwendet für beide Codebasen dieselbe
aktuelle Satzmenge. Vor Bereinigung einer gefundenen Reihenfolgeregression
zeigte er zunächst 4.310 Sätze, acht externe Engine-Deltas und 103 reine
interne Meaning-IR-Deltas. Drei der acht Engine-Deltas waren nicht beabsichtigte
Ablehnungen gültiger Drei-Aktions-Sätze und wurden vor Erzeugung der neuen
Baseline behoben. Der finale Vergleich enthält exakt fünf externe Engine-
Abweichungen und 77 interne IR-Abweichungen durch die nun sichtbaren Satz- und
Klauselgrenzen. Die beabsichtigten externen Deltas sind die engeren
Gerätetypmengen für Rollläden, Markisen und Garagentore sowie die vollständige
Ablehnung einer durch „stopp“ widerrufenen Aktion.

Die neue Releasebaseline umfasst 4.310 Sätze. Ein unabhängiger Neucheck fand
4.310/4.310 Baselines und 0 geänderte Signaturen.

Die 7.13.2-Referenzwerte aus der Release-CI waren ungefähr 3,17 ms
(`light_on`), 11,08 ms (`multi_target`) und 17,97 ms
(`multi_clause_command`). Absolute und relative Gates bleiben unverändert.
Der lokale Vorher-/Nachher-Vergleich lief in derselben Arbeitsumgebung, aber
nicht unter isolierter CPU-Last. Vor der Änderung lagen die p95-Werte bei
65,89 ms (`light_on`), 89,97 ms (`multi_target`) und 155,19 ms
(`multi_clause_command`). Nach der Änderung wurden bei 5.000 Entitäten,
20 Iterationen und 3 Warmups 29,48 ms, 84,54 ms und 142,22 ms gemessen;
das relative Koordinationsverhältnis beträgt 4,82 und bleibt unter 6,5.

Die Abschlussmessung lief auf 4 logischen CPUs bei einer Load Average von
54,29 bis 55,59. Deshalb sind diese absoluten Werte kein Ersatz für die
isolierte Release-CI und nicht direkt mit deren Referenzwerten vergleichbar.
Das separate 5.000-Entitäten-Gate ergab bei 30 Iterationen und 5 Warmups:
`media_coverage` p95 23,54 ms, `write_completeness` 38,02 ms und
`clause_binding` 86,21 ms; alle drei blieben unter dem unveränderten
100-ms-Budget. Es wurde kein Latenz- oder Qualitätslimit gelockert.

## 6. Grenzen und Releasezustand

- Kein echter Music-Assistant-Server, Sonos-Gerät oder anderer Audioplayer
  wurde angesteuert. Die Softwarepfade verwenden HA-Komponentenverträge und
  Service-Fakes; reale Wiedergabe bleibt offen.
- Ein Laufzeitfehler nach einem bereits erfolgreichen Schritt kann nicht
  physisch zurückgerollt werden. Folgeschritte werden gestoppt und die Antwort
  muss den Teilzustand benennen.
- Unbekannte, mehrdeutige und nicht vollständig gebundene Quellen bleiben
  sicher abgelehnt; sie werden nicht geraten.
- 7.13.3 ist vorbereitet, aber noch nicht veröffentlicht. Ein finaler Commit,
  Tag oder GitHub-Release wird erst nach grüner CI und grünen Pflicht-Nightly-
  Jobs für exakt diesen Commit eingetragen.
