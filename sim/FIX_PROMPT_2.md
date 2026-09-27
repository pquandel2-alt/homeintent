# Auftrag: Restlücken aus dem Nachtest 7.2.1 und sicherheitsrelevante Fehlgriffe beheben (Release 7.2.2)

Repository `pquandel2-alt/homeintent`, Basis: aktueller `main` (7.2.1). Lege einen neuen
Branch von `main` an. Lies zuerst diese Dateien vom Branch
`claude/sleepy-meitner-xd7oux` (`git fetch origin claude/sleepy-meitner-xd7oux`) und
übernimm die markierten ins Arbeitsverzeichnis:

- `docs/nachtest-live-simulation-7.2.1.md` – Nachtest mit den Restlücken R1–R6 (lesen)
- `docs/sprachverstaendnis-analyse-7.2.1.md` – Abschnitt „Sicherheitsrelevante Fehlgriffe“ S1–S6 (lesen, übernehmen)
- `sim/nlu_probe.py` und `sim/results/nlu_probe_7.2.1.json` – Sprachkorpus und Ausgangsmessung (übernehmen)

Das Live-Testbett `sim/` liegt bereits auf `main` (Anleitung `sim/README.md`, frischer
Start mit `sim/fresh_ha.sh`, Szenarien mit `sim/runner.py`). Home Assistant 2026.9.2 läuft
unter Python ≥ 3.14.2 (`uv python install 3.14`, dann `requirements-ha-test.txt`).

## Zu beheben

### Sicherheitsrelevant (zuerst)

- **S1** „Lass das Licht in der Küche an.“ (Küchenlicht ist an) schaltet zusätzlich die
  Kücheninsel **ein**. „lass X an/zu/offen“ ist eine Beibehaltung ohne Aktion und darf nie
  etwas schalten.
- **S2** „Mach im Wohnzimmer alles aus.“ schaltet nur die Heizung aus. „alles“ in einem
  Raum = alle schaltbaren, freigegebenen, nicht sicherheitskritischen Geräte des Raums
  (Licht, Schalter, Medien, Ventilator …). Ab der Zielgrenze bzw. bei gemischten
  Domänen gibt es eine Vorschau mit Bestätigung. Heizung und Schlösser nur, wenn
  ausdrücklich genannt oder in der Vorschau aufgeführt.
- **S3** Bei drei Befehlen in einem Satz („Mach das Bürolicht an, stell die Heizung im Büro
  auf 21 Grad und fahr den Raffstore hoch.“) fällt der erste Teil stillschweigend weg.
  Nie Klauseln verwerfen: alle ausführen oder ausdrücklich sagen, welcher Teil nicht
  verstanden wurde, und diesen Teil nicht raten.
- **S4** „Mach die Musik in der Küche leiser.“ → Planvorschau „Küchenradio paused“. „leiser/lauter“
  ist eine Lautstärkeänderung, keine Pause. Außerdem steht dort Englisch („paused“) –
  F16 ist im Planvorschau-Pfad unvollständig. Alle Planvorschau-/Goal-Texte auf rohe
  Zustände prüfen.
- **S5** „Wo ist es gerade am kältesten?“ → „Garten.“ Ohne ausdrücklichen Außenbezug
  vergleicht die Frage Innenräume; Außenwerte nur, wenn nach draußen gefragt wird, oder
  ausdrücklich als „draußen“ gekennzeichnet.
- **S6** „Hier ist es zu hell.“ (Satellit im Wohnzimmer) liest Lichtwerte vor. Eine
  Beschwerde über den Zustand ist keine Statusfrage: dimmen bzw. ausschalten oder
  nachfragen (siehe auch das NLU-Folgeprojekt).

### Restlücken aus dem Nachtest

- **R1** „Stell die Heizung im Kinderzimmer etwas kühler.“ fragt nach der Temperatur;
  „etwas wärmer“ funktioniert. Relative Schritte symmetrisch für kühler/kälter/niedriger/runter.
- **R2** „Mach es im Kinderzimmer etwas kühler/wärmer.“ ohne Gerätenamen → die Heizung(en)
  des Raums (bei genau einer eindeutig, sonst Rückfrage).
- **R3** „Saugroboter bitte auf leise.“ ohne das Wort „Saugstufe“ → angebotene
  `fan_speed_list`-Option auswählen, wenn der Wert eindeutig eine Option ist.
- **R4** „Schalte beim Fernseher auf YouTube um.“ → „Fernseher/TV/Glotze“ als Gattung eines
  Media Players mit Gerätetyp TV im Bereich bzw. im Haus (bei genau einem eindeutig).
- **R5** „Stelle die Schreibtischlampe auf neutralweiß.“ → neutralweiß (≈ 4000 K)
  unterstützen, ebenso „tageslichtweiß“ (≈ 5500–6500 K).
- **R6** „Wie viele Timer laufen?“ → Anzahl nennen, wie „Welche Timer laufen?“.
- **R7** Ausschlussliste mit unbekanntem Namen („… außer dem Nachtlicht und der
  Blumenlampe“): Das Ablehnen ist richtig, aber die Meldung nennt das falsche Gerät
  („Nachtlicht gefunden, Funktion nicht unterstützt“). Richtig ist: „Ich finde kein
  Gerät „Blumenlampe“. Ich habe nichts ausgeführt.“

## Regeln

- Das Sicherheitsmodell bleibt unverändert: Parser führen nichts aus; Validator,
  ExecutionPolicy, Bestätigung und Executor bleiben autoritativ. Keine Tests löschen,
  überspringen oder abschwächen. Bestehende Szenarioerwartungen nicht aufweichen.
- Jede Korrektur ist allgemein, nicht auf den Testsatz zugeschnitten: Für jeden Punkt
  mindestens drei eigene Paraphrasen als Tests, die nicht im Korpus stehen.
- `sim/nlu_probe.py` ist ein Holdout: keine Lexikoneinträge aus seinen Sätzen ableiten.
- Keine Modellbezeichnungen in Commits, Code oder PR-Text.

## Abnahme

1. `python -m pytest -q` grün mit hassil 3.11.0 und 3.12.0; `python -m pytest -q tests_ha` grün;
   alle CI-Prüfungen (Pyright-Scopes, Pyflakes, Sprach-Gate, Benchmarks) grün.
2. `sim/fresh_ha.sh` und `python sim/runner.py`: 126/126 und neue Szenarien für S1–S6 und
   R1–R7 in `sim/scenarios.py` (Kategorie „Regression 7.2.2“) grün; HA-Log ohne
   HomeIntent-Fehler (`python sim/check_log.py`).
3. `python sim/nlu_probe.py --out sim/results/nlu_probe_7.2.2.json`: keiner der Fälle
   S1–S6 mehr als Fehlgriff (keine falschen Gerätaufrufe), und kein zuvor als „ok“
   bewerteter Fall verschlechtert.
4. Version 7.2.2, README „Was ist neu“, Shadow-Baseline falls nötig, PR gegen `main`
   mit einer Zeile pro Punkt (Ursache, Fix, Test).
