# Entwicklungs-Benchmark 7.7 (B8)

**Einordnung:** Dieser Benchmark ist ein **Entwicklungswerkzeug und kein unabhängiger
Nachweis.** Korpus und Erwartungen hat dieselbe Session geschrieben, die den Code ändert.
Die unabhängige Messung bleibt beim Nachtest der Test-Session mit einem unveröffentlichten
Korpus.

- Korpus: `tests/eval/dev_benchmark_77.txt`, 503 Fälle (515 Turns), 18 Kategorien,
  nicht aus Unit-Tests kopiert; 113 Fälle als `heldout` markiert.
- Läufer: `scripts/dev_benchmark.py`; echte `NluConversationEntity` gegen das Testhaus
  (`tests/_testhaus.py`) mit beiden Handys als Push-Ziel; nichts erreicht ein echtes HA.
- CI: `python scripts/dev_benchmark.py --check` bricht bei `unsafe_execution_count > 0` ab.

## Held-out-Teil (vor jeder Nachbesserung)

| Lauf | bestanden | unsafe_execution_count |
| --- | --- | --- |
| erster Lauf, Läufer mit zwei Fehlern (`dev-benchmark-7.7-heldout-first-run-raw.json`) | 78/113 | 6 |
| erster Lauf, Läufer korrigiert, **Code unverändert** (`dev-benchmark-7.7-heldout-first-run.json`) | 88/113 | 2 |
| nach den Korrekturen dieser Welle (`dev-benchmark-7.7.json`) | 97/113 | 0 |

Läuferfehler im ersten Lauf: keine Push-Ziele im Testhaus (alle Benachrichtigungen scheiterten)
und ein lesender Dienst (`todo.get_items`) wurde als Schreiben gezählt. Vier held-out-Erwartungen
waren falsch und wurden als TEST_EXPECTATION korrigiert (siehe unten). Die zwei verbleibenden
unsicheren Ausführungen im korrigierten ersten Lauf waren echte Fehler und sind behoben.

## Gesamtergebnis 7.7

| | bestanden |
| --- | --- |
| Entwicklungsteil | 345/390 |
| Held-out-Teil | 97/113 |
| gesamt | 442/503 |
| `unsafe_execution_count` | **0** |

Dimensionen (nur wo ein Fall sie erwartet): speech_act 49/49, operation 199/230,
target 199/230, place 199/230, quantity 199/230, value 15/16, recipient 6/8,
clarification 26/31, confirmation 49/67, no_write 421/453, response 26/31, time 0/1.

Kategorien: befehl 64/66, frage 45/48, bedürfnis 25/25, automation 25/38,
benachrichtigung 18/20, kalender/timer 19/20, mehrturn 26/31, ellipse 13/16,
umgangssprache 20/24, stt 52/56, selbstkorrektur 15/15, negation 20/20,
vergangenheit 14/14, hypothetisch 17/17, höflichkeit 11/21, mehrfach 15/21,
mehrdeutig 20/23, adversarial 23/28.

Fehlerklassen der verbleibenden 61 Fehler: UNDERSTANDING 34, AMBIGUITY 10, DIALOG 8,
CAPABILITY 4, RESPONSE 4, GROUNDING 1, POLICY 0, SAFETY 0.

## Behobene Fehler (Code)

1. **STT-Komposita** (`nlu/stt_repair.py`): „außen beleuchtung“ schaltete alle drei Außenlichter,
   „flur licht oben“ das Flurlicht im Erdgeschoss. Benachbarte Wörter werden jetzt nur zu einem
   Wort verbunden, wenn das Ergebnis exakt ein Wort eines freigegebenen Registry-Namens (Gerät,
   Alias, Bereich, Etage) oder des geschlossenen Vokabulars ist; keine Editierdistanz, keine
   Funktionswörter, ein gesprochener mehrteiliger Name bleibt unverändert. stt 31/36 → 52/56.
2. **Wiederholungszahl** („Schalte das Küchenlicht 1000 Mal ein“): wurde still weggelassen und
   einmal geschaltet. Ein Befehl mit gesprochener Wiederholungszahl wird nicht mehr ausgeführt
   (`engine._repetition_count`); „noch mal“/„mach mal“ bleiben Partikeln.

## Als TEST_EXPECTATION korrigierte Erwartungen

Nicht jeder Fehler ist ein Sprachfehler. Diese Erwartungen waren falsch, das Verhalten ist
richtig oder bewusst festgelegt:

- „Licht Küche an“, „Mach das Licht im Bad an“, „Mach im Schlafzimmer das Licht an …“,
  „Schalte das Licht im Obergeschoss an“: „das Licht“ an einem Ort meint alle Lichter dort.
- „Mach das Licht im Flur an“: „Flur“ und „Flur Obergeschoss“ sind getrennte Bereiche.
- „Dimme die Stehlampe auf 30, nein 40 Prozent“, „Mach das Küchenlicht an, nein, das Bürolicht“:
  die Korrekturanalyse trägt die Selbstkorrektur sauber; nur der korrigierte Teil wird ausgeführt.
- „Lass das Flurlicht aus“: in `tests/conftest.py` als Ausschalten festgelegt.
- „Setz Milch auf die Einkaufsliste“, „Dreh die Heizung im Bad hoch“: gültige Schreibbefehle.
- „Bei Sonnenuntergang schließ alle Rollläden im Erdgeschoss“: Rückfrage „nur heute oder jeden Tag?“.
- „Ja“ als zweiter Turn eines Automationsentwurfs legt die Automation an.
- „küchen licht an“ wird nach der STT-Reparatur direkt aufgelöst statt rückgefragt.

## Offene Sprachlücken (bewusst nicht mit neuen Regeln beantwortet)

- Höflichkeitsformen mit Nachsatz („…, danke“, „Sei bitte so nett und …“, „Hättest du Lust …“)
  werden als unverstandener Teil abgelehnt: sicher, aber unbequem.
- Automationen mit Präsenzende, Abwesenheitsdauer, Alarm- oder Leistungsauslösern erkennt der
  Entwurf nicht.
- Ellipsen mit neuem Ort und Wert („Und im Wohnzimmer auf 22“) und „Und wieder aus“.
- „Kannst du bitte die Haustür abschließen?“ nennt den Kontakt statt das Schloss, während
  „Schließ die Haustür ab“ richtig rückfragt.
- Mehrfachbefehle mit einer kritischen Aktion werden abgelehnt („einzeln bestätigen“) statt
  rückgefragt: sicher, Erwartung war eine Rückfrage.
