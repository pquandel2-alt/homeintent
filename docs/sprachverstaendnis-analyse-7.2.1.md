# Sprachverständnis-Analyse: Wie weit ist HomeIntent 7.2.1 von „versteht wie ein LLM“?

Stand: 27. September 2026 · getestet: `v7.2.1` im Live-Testhaus (echtes Home Assistant 2026.9.2)

## 1. Messung

Korpus [`sim/nlu_probe.py`](../sim/nlu_probe.py): 66 Sätze, die ein Mensch oder ein LLM
mühelos versteht und die in keinem bisherigen Szenario vorkommen. Für jeden Satz ist
festgelegt, was ein Mensch erwarten würde (handeln, sinnvoll nachfragen, antworten oder
nichts tun). Jeder Satz läuft gegen ein frisch zurückgesetztes Haus, einige mit
vorbereitendem Kontextsatz.

| Bereich | Beispiel | HomeIntent 7.2.1 | HA-Standard-Agent |
| --- | --- | --- | --- |
| Indirekte Wünsche / Zustandsaussagen | „Mir ist kalt.“ · „Die Sonne blendet im Büro.“ | 1 / 12 | 0 / 12 |
| Fragen mit Schlussfolgerung | „Muss ich lüften?“ · „Ist das Haus abgeschlossen?“ | 1 / 12 | 0 / 12 |
| Umgangssprache, Synonyme, Kurzform | „Mach die Glotze an.“ · „Rollos runter“ · „Staubsauger an.“ | 3 / 14 | 4 / 14 |
| Mehrfachbefehle | „Mach im Wohnzimmer alles aus.“ | 2 / 4 | 0 / 4 |
| Kontext, Pronomen, Ellipsen | „Etwas heller bitte.“ · „Die andere.“ · „Mach's da wärmer.“ | 2 / 7 | 0 / 7 |
| Zeit/Bedingung in Alltagssprache | „Weck mich morgen um 7 mit Licht.“ · „…sobald es dunkel wird“ | 1 / 6 | 0 / 6 |
| Negation, Modalität, Konjunktiv | „Lass das Licht an.“ · „Die Stehlampe muss nicht an sein.“ | 0 / 5 | 1 / 5 |
| Auskunft über das Haus | „Was kann ich im Wohnzimmer steuern?“ | 1 / 6 | 0 / 6 |
| **Gesamt** | | **11 / 66 (17 %)** | **5 / 66 (8 %)** |

Rohdaten: `sim/results/nlu_probe_7.2.1.json`, `sim/results/nlu_probe_ha_default.json`.

HomeIntent versteht damit doppelt so viel wie der HA-Standard-Agent. Es arbeitet aber
weiterhin auf Befehlen mit **Gerätenamen und festen Verben**. Sobald ein Satz ein
**Bedürfnis** („mir ist kalt“), eine **Gerätegattung statt eines Namens** („Glotze“,
„Rollos“, „Radio“), eine **Schlussfolgerung** („muss ich lüften?“) oder **Kontext ohne
Nomen** („etwas heller“, „die andere“) enthält, endet er in „nicht verstanden“.

### Sicherheitsrelevante Fehlgriffe (gehören sofort in einen Fix)

| Satz | Ergebnis | Erwartet |
| --- | --- | --- |
| „Lass das Licht in der Küche an.“ (Küchenlicht an) | schaltet **zusätzlich die Kücheninsel ein** | nichts tun |
| „Mach im Wohnzimmer alles aus.“ | schaltet **nur die Heizung** aus, Lichter/TV bleiben an | alle schaltbaren Geräte aus bzw. Vorschau |
| „Mach das Bürolicht an, stell die Heizung im Büro auf 21 Grad und fahr den Raffstore hoch.“ | Heizung und Raffstore ja, **Bürolicht fehlt ohne Hinweis** | alle drei oder klar sagen, was fehlt |
| „Mach die Musik in der Küche leiser.“ | Planvorschau „Küchenradio **paused**“ (pausieren statt leiser; Englisch trotz F16) | Lautstärke senken |
| „Wo ist es gerade am kältesten?“ | „Garten.“ | Innenräume (bzw. „draußen“ ausdrücklich kennzeichnen) |
| „Hier ist es zu hell.“ | liest Lichtwerte vor | dimmen/ausschalten oder nachfragen |

## 2. Warum der jetzige Ansatz hier an Grenzen stößt

HomeIntent übersetzt Sprache symbolisch: Lexikon → Rollen → `SemanticFrame` →
Entity-Auflösung → Validator → Richtlinie. Das ist deterministisch, schnell (p50 24 ms)
und sicher, aber:

1. **Die Bedeutung hängt an Wörtern, nicht an Zielen.** Es gibt keinen Schritt
   „Bedürfnis → gewünschte Wirkung“ (kalt → wärmer; dunkel → heller; stickig → lüften;
   laut → leiser; blendet → beschatten).
2. **Geräte werden über Namen gefunden, nicht über ihre Gattung.** „Glotze“, „Fernseher“,
   „Rollo“, „Radio“, „Staubsauger“ sind keine Namen, aber eindeutige Klassen
   (`media_player` mit Gerätetyp TV, `cover`, …).
3. **Fragen sind an bekannte Muster gebunden.** Aggregierende Sichten („was ist noch an“,
   „ist alles zu“, „muss ich lüften“, „warum ist es kalt“) fehlen als eigene
   Abfragetypen.
4. **Der Diskurszustand ist schmal.** Ellipsen ohne Nomen („etwas heller“, „die andere“,
   „alle“) werden nicht an das letzte Ergebnis gebunden.
5. **Jede neue Formulierung braucht Handarbeit.** Die Abdeckung wächst linear mit
   Lexikon- und Grammatikpflege; offene Alltagssprache wächst schneller.

Ein rein symbolisches System wird für **das Hausvokabular** sehr weit kommen, aber nie
„wie ein LLM“ auf beliebige Formulierungen generalisieren. Deshalb war ursprünglich ein
Ausbau in drei Stufen vorgesehen (Stufen 2 und 3 inzwischen verworfen, siehe unten). Das Sicherheitsmodell bleibt dabei unverändert: Jede Bedeutung
landet am Ende im selben typisierten Frame und läuft durch Validator, Richtlinie und
Bestätigung.

## 3. Empfohlener Weg

### Stufe 1 – Symbolisch generalisieren (ohne neues Modell, Standard)

- **Bedürfnis-Ontologie:** typisierte Abbildung von Zustandsaussagen auf Wirkungen mit
  Raumbezug aus Satellit/Kontext: `kalt/friere → Temperatur ↑`, `warm/heiß → ↓`,
  `dunkel → Licht an/↑`, `hell/blendet → dimmen/beschatten`, `stickig/feucht → lüften`,
  `laut → Lautstärke ↓`, `leise → ↑`. Eindeutige, risikoarme Wirkung → ausführen;
  sonst Vorschlag mit Rückfrage.
- **Gattungs-Lexikon:** Synonyme für Geräteklassen, aufgelöst über Domäne,
  `device_class`, Fähigkeiten und Bereich statt über Namen (Glotze/Fernseher/TV,
  Rollo/Jalousie/Rollladen, Radio/Musik/Lautsprecher, Staubsauger/Sauger, Lampe/Licht,
  Tür→Schloss oder Tor mit Rückfrage).
- **Situations-Sichten als Abfragetypen:** „noch an“, „alles zu/abgeschlossen“,
  „lüften?“ (Feuchte/CO2-Schwellen), „warum kalt/warm?“ (Sollwert, Heizbetrieb, offenes
  Fenster), „was ist los?“ (Zusammenfassung), „wer/ist jemand im Raum?“ (Präsenz),
  „was kann ich steuern?“, Räume je Etage, Anzahl je Gattung, Skript-/Szenenbeschreibung.
- **Diskurs:** Ellipsen und Pronomen binden an das letzte Ziel bzw. die letzte
  Ergebnismenge („etwas heller“, „die andere“, „alle“, „da“, „dann …“).
- **Modalität:** „lass X an/zu“ = nichts tun; „X muss/braucht nicht an sein“,
  „X kann aus“ = ausschalten; „Nicht X, Y meine ich“ = Korrektur.
- **Mehrfachbefehle:** nie stillschweigend Klauseln verwerfen; alles oder klar sagen,
  welcher Teil nicht verstanden wurde.
- **Zeitsprache:** „halb sieben“, „sobald es dunkel wird“ (Sonne/Helligkeit), „wenn ich
  nach Hause komme“ (Person), „weck mich … mit Licht“.

Erwartung: Das Hausvokabular wird breit abgedeckt, geschätzt 55–70 % des Korpus. Das
Versprechen „ohne LLM zur Laufzeit“ bleibt erhalten.

### Stufen 2 und 3 – verworfen

**Entscheidung des Projektinhabers (27.09.2026): HomeIntent erhält kein LLM und kein
ML-Modell, auch nicht optional.** Die ursprünglich vorgeschlagenen Stufen 2
(Satz-Embedding) und 3 (LLM-Brücke über `ai_task`) entfallen. LLM-ähnliches
Verständnis muss vollständig aus der deterministischen Sprachverarbeitung kommen;
Stufe 1 wird dafür deutlich breiter angelegt (kompositionelle Zielauflösung,
Gattungs-Ontologie, Etagen und Komposita überall, vollständige
Benachrichtigungsbedeutung). Umsetzungsauftrag:
[`sim/PROMPT_SPRACHVERSTAENDNIS.md`](../sim/PROMPT_SPRACHVERSTAENDNIS.md).

## 4. Messbarkeit

- Der Korpus `sim/nlu_probe.py` bleibt **Abnahme-Holdout**: Aus ihm dürfen keine
  Lexikoneinträge abgeleitet werden. Die Entwicklung braucht einen eigenen, größeren
  Entwicklungskorpus.
- Für die Abnahme wird zusätzlich ein **zweiter, unveröffentlichter Korpus** in der
  Test-Session verwendet.
- Zielwerte: ≥ 75 % ok und 0 falsche Geräteaktionen, ohne LLM; die Latenz bleibt im
  heutigen Budget (p95 < 100 ms).
