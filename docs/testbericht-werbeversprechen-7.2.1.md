# Testbericht: Hält HomeIntent 7.2.1, was es verspricht?

Stand: 27. September 2026 · getestet: `v7.2.1` (Commit `0080230`) · echtes Home Assistant
2026.9.2 (Python 3.14, hassil 3.12) mit dem simulierten Einfamilienhaus aus [`sim/`](../sim/README.md)

Ziel dieses Laufs: **jede beworbene Funktion** ausdrücklich prüfen, mit besonderem
Augenmerk auf Push-Benachrichtigungen („Benachrichtige mich, wenn Fenster X aufgeht“) und
darauf, wie nah HomeIntent an das Sprachverständnis eines LLM herankommt – **ohne**
selbst ein LLM zu verwenden.

## Ergebnis auf einen Blick

| Prüfung | Ergebnis |
| --- | --- |
| Funktionsszenarien (Geräte, Abfragen, Kalender, Listen, Timer, Automationen, Push, Sicherheit, V12, Agent) | **126 / 126** (6 Szenarien waren zunächst rot, weil Timer, Listeneinträge, Push-Bindungen und Fensterverlauf aus den vorherigen Prüfungen im selben HA übrig waren; auf frischem HA alle grün) |
| Alle Beispielsätze aus den README-Codeblöcken, live | 101 ok · 17 Rückfragen · **23 fehlgeschlagen** (von 141) |
| Push-Matrix: Satz → „Ja“ → Ereignis wirklich auslösen → Nachricht auf dem richtigen Handy | **23 / 35** |
| Alltagssprache (66 Sätze, wie Menschen reden) | **11 / 66** (HA-Standard-Agent: 5 / 66) |

**Fazit:** Die beworbenen Funktionen sind technisch vorhanden und in den dokumentierten
Satzformen verlässlich. Sobald man aber so spricht, wie man im Alltag spricht – mit
Gerätearten statt exakten Namen, mit Etagen, mit Umgangssprache, mit Wünschen statt
Befehlen –, bricht das Verständnis weg. Beim Push betrifft das gerade die häufigsten
Wünsche (Garagentor, Wassermelder, „im Keller“, „im Obergeschoss“, Nachricht mit Text an
eine Person).

## 1. README-Beispielsätze

Skript `sim/readme_check.py`: 141 Sätze aus allen README-Codeblöcken (Dialogblöcke als
zusammenhängendes Gespräch, sonst einzeln auf zurückgesetztem Haus;
Bestätigungsfragen werden mit „Nein“ beantwortet). Rohdaten:
`sim/results/readme_check_7.2.1.json`.

Nach Abzug von Sätzen, deren Gerät das Testhaus nicht hat (Einfahrtkamera, zweites
Bürolicht), bleiben diese echten Fehler:

| README-Satz | Antwort | Ursache |
| --- | --- | --- |
| „Stelle die Heizung auf zweiundzwanzig Grad.“ | „kein eindeutig passendes … Gerät“ | ohne Raum müsste gefragt werden, welche Heizung |
| „Fahre den Rollladen auf drei Viertel.“ / „… zur Hälfte.“ | dito | „Fahre die Rolllade halb runter“ fragt korrekt nach – „den Rollladen“ nicht |
| „Mach das Wohnzimmerlicht an.“ / „Schalte das Wohnzimmerlicht ein.“ | dito | Kompositum Raum + Gattung wird nicht aufgelöst |
| „Stelle den Ventilator auf Stufe 3.“ | dito | zwei Ventilatoren → Rückfrage erwartet |
| „Fahre die Büro-Rolllade auf 40 Prozent.“, „Fahre in 30 Sekunden die Rolllade im Büro …“ | dito | im Büro gibt es genau einen Raffstore (Rollladen-Gattung) |
| „Fahre in zwei Stunden und 30 Minuten die Rollläden herunter.“ | dito | Plural = alle, mit Vorschau |
| „Fahre morgen um 8 Uhr die Rolllade hoch.“ | dito | Rückfrage erwartet |
| „Mach die dimmbare Lampe etwas heller.“ | dito | Merkmal „dimmbar“ als Filter → Rückfrage |
| „Mach die drei Lampen im Büro an.“ (zwei vorhanden) | dito | ehrliche Antwort „Im Büro gibt es nur zwei Lampen“ |
| „Schalte in Küche und Flur alle Lichter aus, außer dem Nachtlicht.“ | „Nachtlicht gefunden, aber Funktion nicht unterstützt“ | Ausnahme liegt außerhalb der Menge → ignorieren oder erklären |
| „Welche Lichter sind mindestens 50 Prozent hell?“ | „Ziel nicht gefunden“ | Schwellenvergleich ohne Raum |
| „Wenn der Termin Urlaub beginnt, aktiviere die Szene Abwesend.“ | „Trigger und Aktion nicht eindeutig“ | Kalenderauslöser |
| „Wann wird die Rolllade gefahren?“, „Welche Automation steuert die Büro Rolllade?“, „Warum wurde die Automation für Büro Rollladen nicht ausgelöst?“ | „Gerät nicht gefunden“ | Gattung statt Name |

Rückfragen, die in diesem Haus berechtigt sind (zwei Kalender, zwei Listen, mehrere
Heizungen im Erdgeschoss, Timer-Name), zählen nicht als Fehler.

## 2. Push-Benachrichtigungen mit echter Auslösung

Skript `sim/push_check.py`: Satz sprechen (als Philipp oder Anna), Vorschau mit „Ja“
bestätigen, dann das Ereignis im Haus wirklich auslösen und prüfen, dass **genau das
richtige Handy** eine sinnvolle deutsche Nachricht erhält; danach die Automation
entfernen. Rohdaten: `sim/results/push_check_7.2.1.json`.

**Funktioniert (23):** Küchen-, Bad-, Schlafzimmer-, Büro-, Kinderzimmer- und
Wohnzimmerfenster in vielen Formulierungen (auch „Benachrichtige mich wenn Fenster Küche
auf geht“, „…wenn das Badfenster auf geht“, „Gib mir Bescheid wenn im Schlafzimmer das
Fenster aufgemacht wird“), „ein Fenster“, „wieder zu“, Haustür, Bewegung im Flur,
Luftfeuchte über 75 %, Anna kommt nach Hause, Nachrichten an Anna, Anna spricht selbst
(→ Annas Handy), Testbenachrichtigung, verzögerte Nachricht und Erinnerung (15 s),
„Benachrichtige mich nicht, …“ (keine Automation), „Sag mir, ob das Küchenfenster offen
ist“ (Frage). Die Nachrichtentexte sind sauber formuliert („Das Küchenfenster wurde
geöffnet.“).

**Scheitert (12):**

| Satz | Antwort |
| --- | --- |
| „Informier mich, falls jemand die Terrassentür öffnet.“ | „Ich finde in der Terrasse kein passendes Gerät für ‚Terrassentür‘.“ – Wortteil als Raum gelesen, die Tür liegt im Wohnzimmer |
| „Benachrichtige mich, wenn im Obergeschoss irgendein Fenster geöffnet wird.“ | „Einen Bereich ‚Obergeschoss‘ kenne ich nicht.“ |
| „Benachrichtige mich, wenn der Wassermelder im Keller auslöst.“ | „Einen Bereich ‚Keller‘ kenne ich nicht.“ – ohne „im Keller“: „kein passendes Gerät für **‚Bewegungsmelder‘**“ (falsche Korrektur!) |
| „Benachrichtige mich, wenn die Temperatur im Keller unter 12 Grad fällt.“ | „Einen Bereich ‚Keller‘ kenne ich nicht.“ |
| „Sag mir Bescheid, wenn das Garagentor aufgeht.“ | „Ich finde kein passendes Gerät für ‚Garagentor‘.“ |
| „Benachrichtige mich, wenn das Küchenfenster geöffnet wird, dass ich lüften soll.“ | „Ich finde in der Küche kein passendes Gerät für ‚Küchenfenster‘.“ – diktierter Text stört |
| „Push an mich, wenn die Haustür offen ist.“ | „Trigger und Aktion nicht eindeutig“ |
| „Sag mir Bescheid, wenn die Waschmaschine fertig ist.“ | „… konnte das Ereignis keinem Gerät zuordnen“ (Statussensor vorhanden) |
| „Schicke mir eine Benachrichtigung wenn im Büro der Raffstore 50% erreicht hat“ | dito (mit „Rolllade“ statt „Raffstore“ funktioniert es) |
| „Schick mir aufs Handy: Essen ist fertig.“ | „nicht verstanden“ („Schick mir eine Nachricht: …“ geht) |
| „Schreib Anna, dass das Essen fertig ist.“ | „nicht verstanden“ |
| „Sag Anna Bescheid, dass ich gleich komme.“ | „nicht verstanden“ |

Sicherheitsrelevant ist nichts davon – es wurde nie an ein falsches Handy gesendet und
nie beim Anlegen schon eine Nachricht verschickt. Aber genau die Alltagsfälle Garage,
Wassermelder, Keller und „schreib X, dass …“ fehlen.

## 3. Sprachverständnis wie ein LLM?

Details: [`sprachverstaendnis-analyse-7.2.1.md`](sprachverstaendnis-analyse-7.2.1.md).
11 / 66 Alltagssätze werden so verstanden, wie ein Mensch es erwartet; drei davon lösten
falsche Geräteaktionen aus („Lass das Licht in der Küche an“ schaltet eine weitere Lampe
ein, „Mach im Wohnzimmer alles aus“ schaltet nur die Heizung aus, bei drei Befehlen fällt
einer weg).

**Entscheidung des Projektinhabers:** HomeIntent erhält **kein LLM und kein ML-Modell** –
auch nicht optional. Die in der Analyse skizzierten Stufen 2 (Embedding) und 3
(LLM-Brücke) entfallen. LLM-ähnliches Verständnis muss aus der eigenen,
deterministischen Sprachverarbeitung kommen. Der Weg dorthin ist im Auftrag
[`sim/PROMPT_SPRACHVERSTAENDNIS.md`](../sim/PROMPT_SPRACHVERSTAENDNIS.md) beschrieben:

1. kompositionelle Zielauflösung (Gattung × Ort × Merkmal × Menge) statt Namenssuche,
2. Etagen und Komposita überall, Korrektur nie über Klassengrenzen,
3. Bedürfnis-Semantik („mir ist kalt“ → wärmer),
4. Situationssichten („muss ich lüften?“, „ist alles zu?“),
5. Diskurs und Modalität,
6. eine vollständige Benachrichtigungsbedeutung für jede Entität, jeden Empfänger und
   jede Wortstellung.

## Kleinere Befunde

- Tippfehler in der Antwort: „2 **Eintrage** wurden als erledigt markiert.“ (Umlaut fehlt).
- Testbett: `haus_sim.reset` setzt nur die Geräte zurück, nicht HomeIntent-Zustand
  (laufende Timer, Listen, Bindungen, Verlauf). Läufe nacheinander im selben HA
  beeinflussen sich; `fresh_ha.sh` vor jeder Messreihe verwenden oder einen Reset für
  diese Zustände ergänzen.

## Rohdaten und Skripte

`sim/readme_check.py`, `sim/push_check.py`, `sim/nlu_probe.py` (neu, wiederholbar) ·
`sim/results/readme_check_7.2.1.json`, `push_check_7.2.1.json`,
`nlu_probe_7.2.1.json`, `nlu_probe_ha_default.json`, `funktionstest_7.2.1_erneut.json`.
