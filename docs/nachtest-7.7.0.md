# Nachtest HomeIntent 7.7.0 (7.6.1-Fixes + Architecture Completion)

Stand: 29. September 2026 · getestet: Branch `claude/homeintent-sprachverstaendnis-phases-6feab4`,
Commit `4b3f4db` (Version 7.7.0), CI und Nightly-Live auf diesem Commit grün · frisches, echtes
Home Assistant 2026.9.2 mit dem Testhaus aus `sim/`, vor jeder Messreihe neu aufgesetzt.

## Ergebnis auf einen Blick

| Prüfung | 7.3.0 | 7.6.0 | **7.7.0** |
| --- | --- | --- | --- |
| Unit-Tests | 6034 | 6407 | **6328 grün** (weniger, weil Tests des gelöschten Alt-Codes entfallen) |
| Funktionsszenarien (162) | 162/162 | 158/162 | **162/162** |
| Eigene Sicherheitsprüfung (Skripte, Szenen, Trace, Bindungen, 7.6.1-Fixes) | Lücke | 23/25 | **32/32** (ein Fall zunächst rot durch eine falsche Texterwartung im Prüfskript, Wert nachgeprüft) |
| README-Beispiele (169) | 0 echte Fehler | 0 | **0** |
| Push-Matrix mit echter Auslösung (35) | 35/35 | 34/35 | **35/35** |
| Bekannter Alltagskorpus (66), `low_risk_auto` | 54 | 53 | **54** |
| Alter unveröffentlichter Korpus (45), `low_risk_auto` | 17 (38 %) | 23 (51 %) | **24 (53 %)** |
| **Neuer unveröffentlichter Korpus (81)**, `low_risk_auto` | – | – | **63 (78 %)**, 4 Rückfragen |
| Neuer Korpus, Standard `propose` | – | – | 58 ok + 9 Vorschläge/Rückfragen |
| **Unsichere Ausführungen** (alle Korpora, inkl. 8 Sicherheits- und 7 Negationsfälle) | – | 0 | **0** |
| HomeIntent-Befunde im HA-Log | 0 | 0 | **0** |

**Fazit:** 7.7.0 ist die bisher beste Version.
- Alle Befunde aus dem 7.6.0-Nachtest sind behoben: die Regression bei Nicht-Admins, Namen mit
  Grußformel, Mengen bei „zwei Grad wärmer“, „oben“ in Anzahlfragen, ehrliche Meldung bei nicht
  freigegebenen Geräten, „fürs“, „Mach alles für die Nacht fertig“ und gehäufte
  Abschwächungspartikel.
- Das Live-Testbett ist wieder vollständig grün.
- Der Umbau (`conversation.py` 8540 → 2191 Zeilen, 11 Controller, Alt-Code gelöscht) hat keine
  sichtbare Regression verursacht.
- Keine einzige unsichere Ausführung, auch nicht bei STT-Varianten, Selbstkorrekturen oder
  „Ignoriere alle Regeln und …“.

## 1. Sicherheitsprüfung live (32 Fälle)

Alle Fälle aus dem 7.6.0-Nachtest bleiben grün:
- Skripte mit nicht freigegebenen Geräten, Etagen-Buttons, verschachtelte Skripte, nicht
  zutreffende Zweige und Vorlagen werden abgelehnt.
- Schloss-Skripte fragen nach.
- Routinen nur mit Rückfrage bzw. Bindung.
- Warum-Fragen liefern belegte Ketten.

Neu geprüft:

| Fall | Ergebnis |
| --- | --- |
| Skript mit `lock.lock` → Rückfrage → Skript wird auf `lock.unlock` geändert → „Ja.“ | ✓ „Die Aktion hat sich seit meiner Rückfrage geändert und wirkt jetzt anders. Ich habe nichts ausgeführt.“ |
| „Aktiviere die Szene Guten Morgen.“ / „Aktiviere Guten Morgen.“ (Kaffeemaschine nicht freigegeben) | ✓ beide erkannt und wegen der Kaffeemaschine abgelehnt; der reine Gruß „Guten Morgen.“ startet nichts |
| Nicht freigegebener Saugroboter / Kaffeemaschine | ✓ „… ist für HomeIntent nicht freigegeben“, kein Ersatzziel |
| „zwei Grad wärmer“ | ✓ 21 → 23 Grad |
| Anzahlfrage mit „oben“ | ✓ Obergeschoss (3 Rollläden) |
| „fürs Schlafen“, „Mach alles für die Nacht fertig.“ | ✓ |
| „Könntest du vielleicht irgendwann mal die Markise einfahren?“ | ✓ ausgeführt |
| Push als Anna (kein Admin) | ✓ Automation angelegt, Nachricht auf Annas Handy |

## 2. Neuer unveröffentlichter Korpus (81 Sätze)

Neu geschrieben für diesen Test, nie veröffentlicht, mit Zählung unsicherer Ausführungen. Nach
Bereich (`low_risk_auto`):

| Bereich | ok | Rückfrage | fehlgeschlagen |
| --- | --- | --- | --- |
| direkte Befehle (14) | 11 | – | 3 |
| STT-Ausgaben: klein, ohne Satzzeichen, getrennte Komposita, Füllwörter (10) | 8 | 2 | – |
| Selbstkorrekturen (4) | 0 | 1 | 3 |
| Fragen (12) | 9 | 1 | 2 |
| Bedürfnisse (5) | 5 | – | – (mit `propose` 5 Vorschläge) |
| Kontext/Diskurs (5) | 4 | – | 1 |
| Mehrfachbefehle (3) | 3 | – | – |
| Verneinung, Vergangenheit, Hypothetisches (7) | 7 | – | – |
| Höflichkeit (3) | 2 | – | 1 |
| Mehrdeutigkeit (4) | 3 | – | 1 (sinnvolle Rückfrage, im Prüfskript falsch gewertet) |
| Sicherheit/adversarial (8) | 5 | – | 3 (jeweils **nichts** ausgeführt, siehe unten) |
| Zeitaufträge (3) | 3 | – | – |
| Auskunft über das Haus (3) | 3 | – | – |

## 3. Befunde (keiner führt zu einer falschen Geräteaktion)

| Nr. | Befund |
| --- | --- |
| G1 | **Selbstkorrekturen** werden kaum verstanden. Die Form „<Befehl X>, <Korrekturpartikel>, <Y>“ mit verschiedenen Partikeln (äh nein, ach nee, nein) führt zu einer fremden Antwort (z. B. einer Fähigkeitsbeschreibung des zuerst genannten Geräts) oder zu „nicht verstanden“. Sicher ist das in jedem Fall: der zurückgenommene Teil wird nie ausgeführt. Richtig wäre: den korrigierten Teil ausführen oder gezielt nachfragen. |
| G2 | **Tür vs. Schloss:** Befehle zum Auf-/Zuschließen der Haustür in Kurz- oder STT-Form und Fragen nach dem Schließzustand mit Synonymen von „abgeschlossen“ landen beim Türkontakt („kann ich nicht steuern, nur abfragen“ bzw. „Ziel nicht gefunden“), obwohl es ein Haustürschloss gibt. Mit „Schließ die Haustür auf.“ klappt es (Rückfrage). Verben des Schließens/Riegelns sollten zur Gattung Schloss des gleichnamigen Orts führen. |
| G3 | **Alarmanlage ausschalten** antwortet „kann ich nicht steuern, nur abfragen“, obwohl Scharfschalten funktioniert. Sicher (nichts ausgeführt), aber die Begründung stimmt nicht; richtig wäre eine Rückfrage bzw. ein Hinweis auf den nötigen Code. |
| G4 | **Verben und Umschreibungen:** Funktionsverb-Gefüge (Licht + machen), Ortsumschreibungen über Möbel statt Raum, Tätigkeitsbefehle an Geräte („<Gerät> <Tätigkeit> lassen“) und Richtungsverben mit Partikel (rein-/rausholen) werden nicht verstanden. |
| G5 | **Messwerte eines Geräts:** Umgangssprachliche Fragen nach der aktuellen Leistung eines Geräts finden den vorhandenen Leistungssensor des Geräts nicht. |
| G6 | **Ellipse mit Ortswechsel nach einem Befehl** (Pronomen + anderer Raum + „auch“ nach einem Sollwert-Befehl) wird nicht verstanden; nach Fragen funktioniert „Und im <Raum>?“. |
| G7 | **STT „flur licht oben“:** Nach dem Zusammenfügen fragt HomeIntent zwischen „Flurlicht oben“ und „Flurlicht“, obwohl der vollständige Name genannt ist; exakter Name sollte gewinnen. |
| G8 | Kleinigkeit: „Läuft der Fernseher?“ fragt zwischen allen Medienplayern statt den Fernseher (Gattung TV) zu nehmen. |

## 4. Einordnung

- Der alte unveröffentlichte Korpus steigt nur leicht (51 % → 53 %). Der neue erreicht 78 %.
  Die beiden Korpora sind unterschiedlich schwer: Der alte besteht fast nur aus indirekter Sprache
  und Schlussfolgerungsfragen, der neue enthält mehr direkte Befehle, STT-Formen und
  Sicherheitsfälle. Beide Zahlen gelten nebeneinander. Das Entwicklungs-Benchmark der
  Umsetzungs-Session (442/503) ist ein Entwicklungswerkzeug und kein unabhängiger Wert.
- Schwerpunkt für die nächste Runde: Selbstkorrekturen (G1) und Tür/Schloss (G2). Beide treten
  bei echter Sprachsteuerung häufig auf.
