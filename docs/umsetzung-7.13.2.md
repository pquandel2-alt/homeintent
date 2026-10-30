# Umsetzung HomeIntent 7.13.2

Stand: 30. Oktober 2026. Dieser Bericht wurde mit der unveränderten
Code-Baseline angelegt, bevor die Korrekturen für 7.13.2 begonnen wurden.

## 1. Baseline vor der Änderung

- aktueller `main`: `0f97bbfd33ec914d5fe21073adde9a4baec666e6`
- veröffentlichtes Tag `v7.13.1`:
  `0f1e63d02fb97e23d626e924c62e9b3491147650`
- Manifest-Version: `7.13.1`
- Worktree: sauber
- Diff `v7.13.1..main`: ausschließlich der Dokumentationscommit
  `docs: record HomeIntent 7.13.1 release evidence` in
  `docs/quality-checklist.md`
- Release-CI für `0f1e63d`: erfolgreich
  ([Lauf 40605416517](https://github.com/pquandel2-alt/homeintent/actions/runs/40605416517))
- Release-Nightly für denselben SHA: erfolgreich
  ([Lauf 40605390788](https://github.com/pquandel2-alt/homeintent/actions/runs/40605390788))
- Release-Gate für denselben SHA: erfolgreich
  ([Lauf 40610331432](https://github.com/pquandel2-alt/homeintent/actions/runs/40610331432))
- CI des nachfolgenden Dokumentationscommits `0f97bbf`: erfolgreich
  ([Lauf 40610441892](https://github.com/pquandel2-alt/homeintent/actions/runs/40610441892))
- HACS-PR
  [hacs/default#10191](https://github.com/hacs/default/pull/10191): offen,
  `CHANGES_REQUESTED`; die automatischen HACS-Prüfungen sind grün. Es wurde
  kein weiterer Kommentar hinterlassen.

Die vorhandenen Medien-, Cover-, Batch- und Release-Gate-Regressionstests
waren auf dieser Baseline grün: 124 bestanden. Sie deckten die folgenden
Gegenbeispiele damit nicht wirksam ab.

## 2. Reproduktionen auf `0f97bbf`

### A und B: Quellenklausel wird verworfen, Pause global verteilt

Die vollständige Conversation-Pipeline erzeugte für alle folgenden Eingaben
dieselbe falsche Vorschau:

```text
Spiel Quirlomat in der Küche und pausiere die Musik im Schlafzimmer.
Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.
Spiel Bayern 3 in der Küche und stoppe die Musik im Schlafzimmer.
Spiel Bayern 3 in der Küche und dann pausiere die Musik im Schlafzimmer.
Spiel One in der Küche und mach die Musik im Schlafzimmer aus.

Planvorschau: Küchenradio pausieren; Lautsprecher Schlafzimmer pausieren;
Wohnzimmer TV pausieren. 3 geprüfte Aktion(en), Gesamtrisiko niedrig.
Soll ich den gesamten Plan ausführen?
```

Nach `Ja` wurde in jedem Lauf mindestens
`media_player.media_pause(entity_id=media_player.kuechenradio)` aufgerufen.
Die endgültig tatsächlich aufgerufene Medienzielmenge enthielt damit das
Küchenradio, obwohl die erste Klausel dort Wiedergabe verlangte. Quelle,
Operation und Ort waren nicht klauselspezifisch gebunden; Küche,
Schlafzimmer und Wohnzimmer wurden in der Vorschau global vermischt.

Die isolierte Engine lehnte dieselben Sätze bereits korrekt ab. Ihre
strukturelle Analyse enthielt zwei Klauseln und meldete für die erste Klausel
bei `Quirlomat` beziehungsweise `WDR 2` Residuum. Der nachgelagerte
Goal-Interpreter ignorierte diesen Ablehnungsnachweis jedoch, interpretierte
die globale Pause-Bedeutung erneut und materialisierte den falschen Plan.

### C: `door`-Cover ohne Bestätigung

Für zwei freigegebene Cover mit `device_class=door` und den Plan
`cover.open_cover([cover.front, cover.back])` ergab die zentrale
Ausführungsgrenze bei `confirmed=False`:

```text
risk=MEDIUM
outcome=ALLOW
executed=True
service call=cover.open_cover(entity_id=[cover.front, cover.back])
```

Der Batch-Selektor hielt die Türen zwar vom partiellen Standard-Cover-Pfad
fern, die getrennte Risikoklassifizierung kannte aber nur `garage`,
`garage_door` und `gate` als bestätigungspflichtige bewegliche Zugänge.

### D: NLU-Performance

Offizielle, auf vergleichbarer Hardware veröffentlichte p95-Werte:

| Fall | 7.13.0 | 7.13.1 |
| --- | ---: | ---: |
| `light_on` | 1,56 ms | 3,09 ms |
| `multi_target` | 4,51 ms | 46,61 ms |
| `multi_clause_command` | 7,33 ms | 49,19 ms |
| `v9_relational_command` | 15,46 ms | 26,97 ms |

Lokale Wiederholung unter identischen Bedingungen (Python 3.12, 5.000
Entitäten, 10 Warmups, 50 Iterationen, `understand`; der geteilte Runner war
insgesamt deutlich langsamer als die Release-Hardware):

| Fall | lokales 7.13.0 p95 | lokales 7.13.1 p95 | lokales 7.13.2-RC p95 |
| --- | ---: | ---: | ---: |
| `light_on` | 44,21 ms | 41,19 ms | 38,29 ms |
| `light_off` | 35,44 ms | 49,74 ms | 32,39 ms |
| `multi_target` | 57,56 ms | 255,77 ms | 117,67 ms |
| `multi_clause_command` | 186,81 ms | 301,91 ms | 171,97 ms |
| `cover_percentage` | 48,60 ms | 44,16 ms | 65,89 ms |
| `v9_relational_command` | 242,27 ms | 270,85 ms | 445,36 ms |

Die absoluten lokalen Werte sind wegen der Runner-Auslastung nicht als
Release-Budget geeignet; das zeigt insbesondere die starke Streuung des von
der Änderung unabhängigen relationalen Pfads. Der identisch konfigurierte
Nachlauf (10 Warmups, 50 Iterationen) reduziert `multi_target` gegenüber
7.13.1 um 54 Prozent und `multi_clause_command` um 43 Prozent. Bezogen auf
den langsameren Licht-Kontrollpfad betragen die neuen Faktoren 3,07 und 4,49.
Das verschärfte relative Gate besteht damit lokal; die absoluten Werte werden
auf der identischen GitHub-CI-Hardware des Release-SHA entschieden.

## 3. Vorläufige Root Causes

1. Der Medien-Fastpath gibt koordinierte Sätze korrekt an die gemeinsame
   Pipeline ab; die Engine erkennt dort das Residuum der Quellenklausel.
2. Der Goal-Router führt danach eine zweite, globale Interpretation aus und
   verwendet weder die Engine-Ablehnung noch klauselspezifische Bindungen.
3. Die Vollständigkeitsprüfung validiert globale Orts- und Zielmengen und
   baut dafür bei koordinierten Sätzen erneut Registry-Namen und das
   Place-Lexikon auf.
4. Risikoklasse und Batch-Eignung sind getrennt, aber die Risikoklasse der
   beweglichen Cover ist unvollständig.

Die folgenden Abschnitte werden mit Implementierung, vollständiger
Cover-Matrix, Profilen, Tests und Release-Nachweisen fortgeschrieben.

## 4. Implementierte klauselspezifische Bindung

`nlu/clause_binding.py` verwendet das bereits aufgebaute `LanguageDocument`,
dessen Strukturrelationen und `read_clauses`; es ist keine zweite Medien-NLU.
Für jedes echte koordinierte Schreibprädikat entsteht ein unveränderliches
`ClauseBinding` mit:

- Klausel-ID und Zeichen-Span,
- Operation,
- Zielausdruck und exakt aufgelösten Entity-IDs,
- Ortsausdruck und exakt aufgelösten Area-/Floor-IDs,
- Quelle oder Wert,
- verbrauchten Token-Spans und Rest,
- den exakt zuordenbaren `ServiceCallPlan`-Objekten,
- Ablehnungsgrund und stabiler semantischer Signatur.

Die Aktivierung ist strukturell begrenzt: zwei vollständige Prädikate, ein
gemeinsames Prädikat über mehrere Ziele oder eine Play-/Quellenklausel mit
weiterer Klausel. Nominale Koordinationen („zwei Stunden und 30 Minuten“) und
Automationsmetasprache bleiben in ihren vorhandenen Modellen. Bei
Prädikatellipse darf nur das bereits belegte Ziel/der Ort weitergetragen
werden, niemals die Operation.

Vor dem ersten Write müssen alle Bindungen vollständig sein. Die Vereinigungs-
menge der lokal zugeordneten Pläne muss exakt der lokalen Zielmenge entsprechen;
ein still fallengelassenes Garagentor oder ein Player außerhalb des
Klauselorts blockiert daher den ganzen Turn. Die Goal-Vorstufe darf eine
abgelehnte Quellenbindung nicht erneut als globale Pause-/Goal-Bedeutung
interpretieren.

### Medienquellen: sichere Variante B

Die Quellenauflösung unterscheidet `exact`, `ambiguous` und `unknown` gegen
die Player im Ort derselben Klausel. Da der gemeinsame Mehrbefehls-Compiler
noch keinen sicheren Quellenoperator materialisiert, führen derzeit alle drei
Zustände im zusammengesetzten Satz zur vollständigen Ablehnung:

| Quellenart | Bindung | Ergebnis im Mehrbefehl |
| --- | --- | --- |
| exakte `source_list`-Quelle, z. B. WDR 2/Bayern 3/One | `exact` | `compound_source_not_supported`, kein Plan/Write |
| Music-Assistant-Favorit | quellensemantisch erkannt, gemeinsamer Compiler ohne sicheren Operator | kein Plan/Write |
| unbekannter Name | `unknown` | `unknown_source`, kein Plan/Write |
| mehrere exakte Playertreffer | `ambiguous` | `ambiguous_source`, kein Plan/Write |

Einzelne Quellenbefehle bleiben im bestehenden exakten Medien-/MA-Pfad.

## 5. Cover-Risikomodell

Home Assistant 2026.9.2 definiert zentral `awning`, `blind`, `curtain`,
`damper`, `door`, `garage`, `gate`, `shade`, `shutter`, `window`. Ein realer
HA-Komponententest gleicht diese Enum mit HomeIntent ab; `garage_door` wird nur
als historischer Registry-Wert akzeptiert.

| Gruppe | Klassen | Bewegung | Risiko | Batch |
| --- | --- | --- | --- | --- |
| unabhängige Beschattung | `shutter`, `blind`, `shade`, `curtain` | öffnen, schließen, Position | grundsätzlich `MEDIUM`; Breitenregel unverändert | pro Ziel, vollständige Vorprüfung |
| physischer Zugang, kritisch | `garage`, `garage_door`, `gate` | alle drei Dienste | `CRITICAL`, immer Bestätigung | atomar |
| physischer Zugang | `door`, `window` | alle drei Dienste | mindestens `HIGH`, immer Bestätigung | atomar |
| sonstig/unklar | `awning`, `damper`, andere, fehlend/unbekannt | alle drei Dienste | mindestens `HIGH`, immer Bestätigung | atomar |
| gemischt | Standard plus eine nicht unabhängige Klasse | alle drei Dienste | höchstes Einzel-/Breitenrisiko | atomar |

`ConfirmedScope` bindet neben Risiko und effektiven Zielen nun die vollständige
Plansignatur (Domain, Service, sortierte Ziele, rekursiv eingefrorene Daten).
Eine geänderte Quelle, Position, Aktion oder Zielmenge wird nach „Ja“ ebenso
abgelehnt wie ein nicht mehr freigegebenes oder nicht verfügbares Ziel.

Konkrete Nachreproduktion für `door`, `window` und fehlende Klasse:

```text
risk=HIGH, policy=CONFIRM, confirmed=False, executed=False, calls=0
confirmed=True, unveränderter Plan: exakt ein atomarer Call für front+back
confirmed=True, geänderte Zielmenge: executed=False, calls=0
```

## 6. Performancekorrektur

Der Turn baut das `LanguageDocument` einmal und reicht es an die Bindungsgrenze
weiter. `analyse_clause_bindings` prüft einfache Sätze über die vorhandenen
Strukturrelationen und kehrt ohne Registry-Scan zurück. Die alte globale
Vollständigkeitsprüfung bleibt nur als Kompatibilitätsfallback.

Der Registry-Namensindex ist auf acht Strukturstände begrenzt. Sein Schlüssel
enthält ausschließlich Entity-ID, Friendly Name und Aliase; sein Wert nur
normalisierte Wortfolgen und IDs. Weder Entity-Snapshots noch Turn-, Benutzer-
oder Conversation-Objekte werden gehalten. Eine Namens-/Aliasänderung erzeugt
sofort einen neuen Index. Zielauflösung bindet diese ID-Struktur bei jedem
Aufruf an die aktuellen Snapshots; identische Namen können deshalb keinen
alten Zustand, Ort oder `device_class` aus einem früheren Turn zurückgeben.
Das Ortslexikon verwendet weiterhin seinen begrenzten, strukturellen Cache.

Profil vor der Korrektur (`multi_target`, 20 instrumentierte Aufrufe):

| Funktion | kumuliert |
| --- | ---: |
| `analyse_write_completeness` | 2,470 s |
| `_Names.__init__` | 2,227 s |
| `utterance_fields` | 1,892 s |

Nachprofil des gesamten 5.000-Entitäten-Satzes (313 `understand`-Aufrufe):

| Funktion | Aufrufe | kumuliert |
| --- | ---: | ---: |
| `analyse_clause_bindings` | 313 | 0,586 s |
| `analyse_write_completeness` | 96 | 0,054 s |
| Registry-Namensaufbau/`utterance_fields` | – | nicht mehr unter den messbaren Hotspots |

Der isolierte lokale Nachlauf mit 5 Warmups/30 Iterationen auf dem belasteten
Host ergab p95 27,17 ms (`media_coverage`), 25,47 ms
(`write_completeness`) und 58,44 ms (`clause_binding`). Die Bindungsmessung
verwendet dasselbe bereits
vorhandene `LanguageDocument` wie der Produktionspfad; Dokumentaufbau bleibt im
End-to-End-Benchmark enthalten. Absolute lokale Zahlen sind wegen starker
Hostlast nicht mit GitHub-CI gleichzusetzen.

Zusätzlich zum absoluten p95-Budget prüft die CI nun relativ auf demselben
Runner: `multi_target` und `multi_clause_command` dürfen höchstens Faktor 6,5
über dem langsameren `light_on`/`light_off`-Kontrollpfad liegen. Ein stabiler
Unit-Test belegt, dass die veröffentlichten 7.13.1-Werte dieses Gate reißen.
Ein zusätzlicher Grenztest belegt, dass bereits Faktor 7 nicht mehr grün ist.

## 7. Neue Regressionen

- vollständige Conversation-Pipeline für unbekannte, exakte und mehrdeutige
  Quellen, Ziffernquellen, MA-Favorit, umgekehrte Reihenfolge und zweite
  Operationen Pause/Stop/Aus/Lauter/Leiser/Stumm/Nächster Titel,
- unbekannte Quellen vor Licht-, Cover- und Medienbefehlen sowie bekannte
  Quelle vor unbekannter Folgeklausel,
- harte Ortsgrenzen, mehrere Player im Raum, Satellitenbereich plus expliziter
  Raum und unveränderlicher Confirmation Scope,
- Hypothesis-/Metamorphietests für unbekannte Quellen, Höflichkeit und
  Joiner-Varianten,
- Matrix aller HA-Cover-Klassen plus Legacy/fehlend/unbekannt über
  `open_cover`, `close_cover`, `set_cover_position`, bestätigt/unbestätigt,
  Zieländerung und Unverfügbarkeit,
- positive partielle Standard-Cover- und gemischte atomare Cover-Fälle,
- strukturelles Cache-Lebenszyklus- und relatives Performance-Gate.

Die vollständigen bestehenden Automationskorpora wurden nach der neuen
Grenzziehung erneut geprüft; Automations-, zusammengesetzte Zeit- und
Planänderungsdialoge werden nicht als unmittelbare Schreibkoordination
fehlklassifiziert.

## 8. End-to-End-Nachreproduktion

Für die fünf Sätze aus Fehler A/B erkennt die Pipeline jeweils zwei Klauseln.
Beispiel:

```text
c0: operation=play, source=WDR 2, resolution=exact,
    targets=(media_player.kuechenradio), places=(area:kuche),
    reject=compound_source_not_supported
c1: operation=pause, source=None,
    targets=(media_player.lautsprecher_schlafzimmer),
    places=(area:schlafzimmer), reject=None
Gesamt: kein Plan; keine Vorschau; keine Bestätigung; calls=[]; Ziele=[]
```

Bei `Quirlomat` lautet die Quellenauflösung `unknown` und der Ablehnungsgrund
`unknown_source`; zweite Klausel, Calls und Zielmenge bleiben ebenfalls leer.
Kein Fall nimmt das Wohnzimmer-TV hinzu.

## 9. Lokale Abschlussnachweise und noch offene Release-Gates

- Python 3.12: 10.418 bestanden, 12 übersprungen in 56:25;
- Python 3.13: 10.418 bestanden, 12 übersprungen in 48:28;
- RuntimeWarning und `PytestUnraisableExceptionWarning` waren in beiden
  Läufen Fehler;
- Safety-/Medien-/Klausel-Properties im CI-Profil: 126 bestanden;
- dasselbe Nightly-Profil mit Seeds 7132, 424242 und 99991: jeweils 126
  bestanden, kein Gegenbeispiel;
- Entwicklungsbenchmark 7.7: 478/516 Fälle, 573 Turns, alle Safety-Zähler 0;
- Entwicklungsbenchmark 7.8: 103/107 Fälle, 126 Turns, alle Safety-Zähler 0;
- versionierte 7.13.2-Korpusbaseline: 4.295 Satzsignaturen, unabhängiger
  Nachlauf ohne Signaturdrift;
- echte HA-2026.9.2-Komponententests: 23 bestanden;
- Music-Assistant-Komponentenvertrag gegen HA 2026.4.0 und 2026.9.2 jeweils
  mit dem von HA deklarierten Client und realem Komponentenschema bestanden;
  ein echter Music-Assistant-Server beziehungsweise Audiowiedergabe wurde
  dabei weiterhin nicht behauptet.

Ein lokaler 5.000-Entitäten-Nachlauf am 30. Oktober war wegen einer
Systemlast von 54 auf vier CPUs nicht als absolutes Latenzgate verwertbar;
auch unveränderte relationale Altpfade überschritten dort 100 ms. Die
relativen Verbesserungen gegenüber 7.13.1 und das strukturelle Gate bleiben
belegt. Das absolute p95-Gate wird deshalb unverändert auf dem isolierten
GitHub-Runner für den finalen SHA entschieden, nicht durch gelockerte Budgets.

Finaler Commit, CI, Nightly, Release-Gate und Tag werden erst nach der
vollständig grünen SHA-genauen Prüfung eingetragen. Reale Sonos-,
Music-Assistant-, Licht- und Rollladenhardware bleibt ausdrücklich offen. Der
HACS-PR bleibt ohne neue Review-Entscheidung `CHANGES_REQUESTED`.
