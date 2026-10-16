# ADR-0001: Optionales lokales Modell nur als nicht vertrauenswürdige Bedeutungsquelle

- Status: **für die Produktlaufzeit von v7.9.9 abgelehnt; spätere Forschung aufgeschoben**
- Datum: 2026-10-16
- Entscheidungsträger: Projektmaintainer vor einer späteren Implementierung

## Kontext

HomeIntent ist bewusst deutschsprachig, lokal, deterministisch und läuft ohne
LLM oder ML-Modell. Das ist eine Produkteigenschaft. Ein optionales lokales
Modell könnte künftig unbekannte Paraphrasen strukturieren, darf aber weder
die Verlässlichkeit von Home Assistant noch die deterministischen
Sicherheitsgrenzen schwächen.

Typische offizielle HA-Hardware ist keine freie Inferenzmaschine:

- Home Assistant Green besitzt einen Rockchip RK3566 mit vier Cortex-A55-
  Kernen bei 1,8 GHz, 4 GB RAM und 32 GB eMMC. Quelle:
  [Home Assistant Green](https://www.home-assistant.io/green/).
- Home Assistant Yellow basiert auf einem CM4 mit vier Cortex-A72-Kernen bei
  1,5 GHz; die offizielle Empfehlung beginnt bei 2 GB RAM, unterstützt werden
  Varianten bis 8 GB. Quelle:
  [Home Assistant Yellow](https://www.home-assistant.io/yellow/).
- Für eine allgemeine HAOS-VM nennt Home Assistant mindestens 2 GB RAM und
  zwei vCPUs. Quelle:
  [Home Assistant auf Linux](https://www.home-assistant.io/installation/linux/).

Aus diesen Daten folgt keine belastbare Modellgröße. Sie zeigen vielmehr,
dass ein Modell auf 2- bis 4-GB-Systemen mit Core, Recorder, Datenbank und
Add-ons konkurriert. Ohne Messungen auf Green und einer 2-GB-CM4-Konfiguration
darf kein Modell als für „typische HA-Hardware“ geeignet bezeichnet werden.

## Entscheidung

v7.9.9 und die hier vorbereiteten Refactorings enthalten **kein Modell, kein
Modell-Framework, keinen Download und keinen Modellpfad**. Der
deterministische Parser bleibt die einzige Produktionsautorität.

Ein späterer Prototyp ist nur zulässig, wenn er ausschließlich einen
untrusted `MeaningCandidate` beziehungsweise eine gleichwertige
`SemanticFrame`-/Meaning-IR-Hypothese erzeugt. Dieses Ergebnis ist Input für
die vorhandene deterministische Pipeline, nicht deren Ersatz.

```text
Benutzertext
  -> begrenzter lokaler Modellprozess
  -> streng validierter, untrusted MeaningCandidate
  -> deterministische Entity-/Area-/Floor-Resolution
  -> Grounding + Capability Validator
  -> Ambiguity/Confidence Gate
  -> ExecutionPolicy + Bestätigung + NEVER_AUTO
  -> ServiceExecutor
```

Das Modell darf insbesondere:

- keine HA-Services oder Tools aufrufen,
- keine Berechtigung, Bestätigung oder Standing Permission vergeben,
- keine Entity-ID als geerdet markieren,
- keine Registry-, History-, Token- oder Secret-Daten erhalten,
- keinen Klartext außerhalb des lokalen, flüchtigen Inferenzprozesses
  speichern oder senden,
- keinen bestehenden `UNSAFE`-, `AMBIGUOUS`- oder `UNSUPPORTED`-Ausgang in
  eine Ausführung hochstufen.

## Opt-in und Betriebsform

Eine spätere Funktion muss separat installiert und ausdrücklich aktiviert
werden. Der Standard bleibt deaktiviert. Aktivierung erfordert:

1. einen eigenen, klar benannten experimentellen Optionsschalter,
2. sichtbare Angabe von Modell, Version, Lizenz, Downloadgröße und lokalem
   Speicherort,
3. eine Hardware-Vorprüfung und einen lokalen Benchmark,
4. ausdrückliche Bestätigung, dass der bisherige Betrieb „ohne LLM zur
   Laufzeit“ für diesen Modus nicht gilt,
5. jederzeitigen Rückfall auf den deterministischen Parser ohne Verlust von
   Konfiguration oder Dialogzustand.

Der Modellprozess läuft isoliert außerhalb des HA-Event-Loops. Laden,
Inference, Validierung und Beenden dürfen keinen Event-Bus-Callback
blockieren. Netzwerkzugriff wird technisch verweigert; offline bedeutet hier
nicht nur „funktioniert ohne Internet“, sondern „kann bei der Inferenz kein
Internet erreichen“.

## Versioniertes Ausgabeschema

Der Prototyp akzeptiert nur ein versioniertes JSON-ähnliches Schema mit
`additionalProperties = false`. Mindestens enthalten sind:

- `schema_version`, `model_id` und `model_version`,
- höchstens vier Kandidaten,
- pro Kandidat Sprechakt, Modalität, Polarität, Prädikate, Argumentrollen,
  Modifier, Negations-/Klausel-Scope und Quellspannen,
- ein begrenzter, kalibrierter Confidence-Wert und explizite fehlende Slots,
- ausschließlich Erwähnungsspannen für Geräte und Orte, niemals eine vom
  Modell behauptete Entity-ID,
- keine Service-, Permission-, Policy-, Bestätigungs- oder Executorfelder.

Grenzen für Stringlänge, Arraygröße, Verschachtelungstiefe, Kandidatenzahl,
Enumwerte und Gesamtbytes werden vor jeder Projektion geprüft. Unbekannte
Felder, ungültiges UTF-8, überlappende oder außerhalb des Quelltexts liegende
Spannen, NaN/Infinity, zu tiefe Strukturen oder eine falsche Schemaversion
verwerfen die gesamte Modellausgabe.

Schema und Modellversion werden gemeinsam versioniert. Eine inkompatible
Version startet nicht still mit einem älteren Validator.

## Zeit-, Speicher- und Lastgrenzen

Vor Auswahl eines Modells müssen Messungen auf Home Assistant Green und auf
einem 2-GB-CM4-System vorliegen. Als Schutzvertrag gelten bereits für einen
Prototyp:

- höchstens eine aktive Inferenz und höchstens ein wartender Turn,
- keine Task-pro-Anfrage- oder unbeschränkte Queue,
- ein harter Wall-Clock-Timeout; Startwert höchstens 2 Sekunden, Zielwert für
  interaktive Turns 500 ms p95,
- ein separat erzwungenes RSS-Limit; erster Green-Versuch höchstens 512 MiB,
  nicht nur eine nachträgliche Messung,
- höchstens 8 KiB normalisierter Eingabetext und 32 KiB Modellausgabe,
- Lazy Load im Hintergrund, bounded Startup und vollständiges Unload,
- Circuit Breaker bei wiederholtem Timeout, Crash, Schemafehler oder
  Speicherüberschreitung,
- kein automatischer Neustartsturm; begrenztes exponentielles Backoff,
- CPU-/RAM-/Latenzbudgets werden gemeinsam mit den vorhandenen
  5.000-Entity-Budgets geprüft.

Ein Modell, das diese Grenzen nicht einhalten kann, ist für diesen
Betriebsmodus ungeeignet. Das Limit wird nicht erhöht, nur damit ein
bestimmtes Modell startet. Größere Hardware kann später ein eigenes,
ausdrücklich ausgewähltes Profil erhalten.

## Fallback und Unsicherheit

Bei deaktiviertem Modell, Cold-Start, Timeout, Prozessabbruch, Circuit
Breaker, ungültigem Schema, zu geringer Confidence oder fehlendem Slot wird
das Ergebnis verworfen. Danach gilt genau einer dieser Wege:

1. Der unveränderte deterministische Parser liefert ein vollständiges,
   eindeutiges Ergebnis und durchläuft alle vorhandenen Gates.
2. HomeIntent fragt typisiert nach.
3. HomeIntent lehnt ehrlich als unbekannt oder unsupported ab.

Es gibt kein „best effort“-Grounding auf ähnlich klingende Entity-IDs und
keine Teil-Ausführung eines unvollständigen Kandidaten. Ein Modellscore allein
macht niemals einen Turn ausführbar.

## Prompt Injection und halluzinierte Entitäten

Der Benutzertext wird als Datenfeld an einen schemafixierten Parserprozess
gegeben. Er kann keine Systemanweisung, Toolbeschreibung, Registryliste oder
Ausführungsfähigkeit überschreiben. Texte wie „ignoriere deine Regeln“,
gefälschte JSON-Fragmente oder behauptete Entity-IDs bleiben gewöhnliche
Quelltoken.

Entity- und Ortsreferenzen werden ausschließlich aus validierten
Quellspannen durch die bestehenden deterministischen Resolver geerdet.
Erfundene Namen haben keinen Registrytreffer; mehrere Treffer erzeugen eine
Rückfrage. Capability-, Berechtigungs-, Bestätigungs- und NEVER_AUTO-Regeln
werden erst nach dieser Auflösung von bestehenden Komponenten geprüft und
sind für das Modell nicht sichtbar oder änderbar.

## Datenschutz und Telemetrie

Inference und Modellartefakte bleiben lokal. Klartextäußerungen,
Friendly Names, Entity-IDs, Orte und Modellausgaben werden weder dauerhaft
telemetriert noch in Crashreports übernommen.

Vollständige Betriebsdiagnose ohne Klartext umfasst:

- lokal gesalzenen, nicht extern korrelierbaren Turn-Hash,
- Schema-, Modell- und Runtimeversion,
- Aktivierungsprofil und Circuit-Breaker-Zustand,
- Dauer, Queuezeit, Timeout-/Crashart und Peak-RSS,
- Kandidatenzahl, Validierungsausgang und Fallbackgrund,
- Ergebnisart vor und nach deterministischem Grounding,
- Zähler für Ambiguität, Ablehnung und Safety-Gate – niemals Serviceparameter
  oder gesprochene Inhalte.

Debug-Klartext ist nur als kurzlebiges, lokales und ausdrücklich aktiviertes
Diagnosefeature zulässig und darf nie Standardtelemetrie sein.

## Erforderliche Belege vor einer späteren Annahme

- isolierter Prototyp ohne Verbindung zum ServiceExecutor,
- Schema-Fuzzing sowie Tests für Übergröße, Tiefe, NaN und fremde Felder,
- adversariale Prompt-Injection- und Entity-Halluzinationssuite,
- Timeout-, OOM-, Crash-, Backoff-, Circuit-Breaker- und Unload-Tests mit
  kontrollierter Clock,
- Messungen auf Green und 2-GB-CM4 unter gleichzeitigem realistischem HA-
  Betrieb, einschließlich Responsivität und Event-Loop-Lag,
- `unsafe_execution_count = 0`, `SAFETY_DRIFT = 0` und unveränderte
  Permission-/Confirmation-/NEVER_AUTO-Property-Suites,
- Datenschutzprüfung, Lizenzprüfung und reproduzierbarer Modellbezug,
- gesonderte Produktdokumentation, die deterministischen Standard und
  experimentellen Modellmodus unmissverständlich trennt.

## Folgen

Positiv: Eine spätere Forschung kann flexible Bedeutungsanalyse untersuchen,
ohne dem Modell Ausführungsautorität zu geben. Offlinebetrieb, lokale Daten
und deterministische Sicherheitsgrenzen bleiben technisch prüfbar.

Negativ: Viele Modellvorschläge werden nach dem Grounding verworfen; auf
kleiner Hardware sind Latenz und Speicher wahrscheinlich der begrenzende
Faktor. Der zusätzliche Modus erhöht Packaging-, Lizenz-, Update- und
Supportaufwand erheblich.

Diese Nachteile sind akzeptabel, weil der ADR keine Implementierung
beschließt. Für v7.9.9 bleibt die Entscheidung eindeutig: kein LLM und kein
ML-Modell in der Produktlaufzeit.

