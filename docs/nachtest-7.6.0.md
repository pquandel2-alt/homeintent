# Nachtest HomeIntent 7.6.0 (Umsetzung von `sim/PROMPT_GESAMT.md`)

Stand: 28. September 2026 · getestet: Branch `claude/homeintent-sprachverstaendnis-phases-6feab4`,
Commit `ce9e9e6` (Version 7.6.0) · frisches, echtes Home Assistant 2026.9.2 mit dem Testhaus
aus `sim/`, vor jeder Messreihe neu aufgesetzt.

## Ergebnis auf einen Blick

| Prüfung | 7.3.0 | 7.6.0 |
| --- | --- | --- |
| Unit-Tests | 6034 grün | **6407 grün**, 12 übersprungen |
| Funktionsszenarien (`runner.py`, 162) | 162/162 | **158/162**; die 4 Abweichungen sind gewollte Verhaltensänderungen (siehe unten) |
| Eigene Sicherheitsprüfung Skripte/Szenen/Trace/Bindungen (25 Fälle) | Lücke | **23/25**, beide Abweichungen ohne Sicherheitsbezug |
| README-Beispiele (169) | 0 echte Fehler | **0 echte Fehler** (unverändert) |
| Push-Matrix mit echter Auslösung (35) | 35/35 | **34/35**: Regression bei Nicht-Admins (F1) |
| Bekannter Alltagskorpus (66), Stufe `low_risk_auto` | 54/66 | **53/66** |
| Bekannter Alltagskorpus, Standardstufe `propose` | – | 47 ok + 11 berechtigte Vorschläge |
| **Unveröffentlichter Korpus (45), `low_risk_auto`** | 17/45 (38 %) | **23/45 (51 %)**; Fehlschläge 22 → 15 |
| Unveröffentlichter Korpus, `propose` | – | 19 ok + 11 Vorschläge/Rückfragen |
| Falsch geschaltete Geräte (alle Korpora) | 0–1 | **0** |
| HomeIntent-Befunde im HA-Log | 0 | **0** |

**Fazit:** Die Sicherheitsziele sind erreicht. Skripte, Szenen und Gruppen schalten keine nicht
freigegebenen Geräte mehr, auch nicht über Etagenziele, verschachtelte Skripte oder nicht
zutreffende Zweige. Nicht prüfbare Schritte werden abgelehnt. „Warum ist X an?“ nennt belegte
Ketten. Routinen werden nie mehr über Wortähnlichkeit ohne Rückfrage gestartet. Das
Sprachverständnis auf **nie gesehenen** Sätzen ist von 38 % auf 51 % gestiegen. Das ist ein echter
Fortschritt, aber deutlich weniger als die 94 %, die die Umsetzungs-Session auf ihren eigenen
Paraphrasen gemessen hat. Eine Regression (F1) sollte vor dem Einsatz in Haushalten mit
Nicht-Admin-Nutzern behoben werden.

## 1. Sicherheit (Phasen 1–3), live

Eigenes Prüfskript (nicht eingecheckt). Es legt Test-Skripte über die HA-Konfigurations-API an,
entzieht Saugroboter, Kaffeemaschine und den Entkalken-Button die Freigabe und prüft jede Antwort
und jeden Geräteaufruf.

| Fall | Ergebnis |
| --- | --- |
| „Aktiviere Nachtruhe.“ (Licht + nicht freigegebener Sauger + Kaffeemaschine), danach „Ja.“ | ✓ abgelehnt mit beiden Namen, kein Aufruf; „Ja“ führt nichts aus |
| „Gute Nacht“ mit `button.press` auf `floor_id: erdgeschoss` (dein Vorfall) | ✓ „… nicht freigegeben: Kaffeemaschine entkalken. Der Schritt ‚Rolladen Runterfahren‘ drückt alle Buttons im Erdgeschoss. Ich habe nichts ausgeführt.“ |
| „Aktiviere Schlafen.“ (nur Licht + Rollladen) | ✓ genau diese zwei Aufrufe, „Schlafen ausgeführt: 1 Rollladen und 1 Licht.“ |
| Template-Ziel | ✓ abgelehnt: „Einen Schritt kann ich nicht prüfen …“ |
| Verschachteltes Skript, das „Nachtruhe“ aufruft | ✓ abgelehnt |
| Nicht zutreffender `choose`-Zweig mit Sauger | ✓ abgelehnt |
| Skript mit Schloss (Admin und Anna) | ✓ Rückfrage; „Nein“ → nichts |
| Neue Szene mit nicht freigegebener Kaffeemaschine | ✓ abgelehnt, auch ohne das Wort „Szene“ |
| Szene Filmabend (alles freigegeben) | ✓ ausgeführt, „Filmabend aktiviert: 3 Lichter.“ |
| „Ich gehe schlafen.“, „Gute Nacht.“, „Starte die Schlafroutine.“ | ✓ kein Aufruf, „Welche Routine meinst du: Gute Nacht, Nachtruhe und Schlafen?“ |
| Wahl „Schlafen.“ → Ausführung + Bindung; danach „Ich gehe schlafen.“ | ✓ „Soll ich das Skript Schlafen starten?“, nichts ohne Ja |
| „Vergiss die Schlafroutine.“ | ✓ |
| „Warum ist der Saugroboter angegangen?“ nach Skript „Saugen“ | ✓ „Du hast um 23:00 „Aktiviere Saugen.“ gesagt. Ich habe das Skript Saugen gestartet. Dessen Schritt ‚Saugen starten‘ hat Saugroboter gestartet.“ |
| „Warum ist das Flurlicht an?“ nach Bewegung | ✓ „Automation „Flurlicht bei Bewegung“, ausgelöst durch eine Zustandsänderung von Bewegungsmelder Flur, …“ |
| „Warum ist die Stehlampe an?“ nach Szene | ✓ Kette über die Szene |
| „Aktiviere die Szene Guten Morgen.“ | ✗ siehe F2 |
| „Welche Routine nutzt du fürs Schlafen?“ | ✗ siehe F6 (mit „für schlafen gehen“ ✓) |

## 2. Funktionsszenarien

158/162. Die vier Abweichungen sind die in der Umsetzung angekündigten, gewollten Änderungen:
- „Ich friere“, „muffige Luft“ und „zu hell“ werden in der neuen Standardstufe `propose`
  vorgeschlagen statt sofort ausgeführt.
- `auto-manage` baut auf einer Sonnenuntergangs-Automation auf, die jetzt erst nach „Nur heute
  oder jeden Tag?“ entsteht.

Diese Szenarien passt die Test-Session an.

## 3. Befunde

| Nr. | Schwere | Befund | Beleg |
| --- | --- | --- | --- |
| **F1** | **hoch (Regression)** | Nicht-Admins können trotz `allow_non_admin_automations` keine Automation mehr anlegen, auch keine Push-Benachrichtigung. Antwort: „Fehler beim Erstellen der Automation: Unauthorized“ (englisch, roh). | Push-Matrix: Anna, „Benachrichtige mich, wenn das Küchenfenster aufgeht.“ → „Ja“. Ursache: `automation_executor.py` ruft `automation.reload` (in HA ein Admin-Dienst) mit `call_context()` auf, also seit 7.3.2 mit Annas Benutzer-ID. |
| **F2** | mittel | Szenen/Skripte, deren Name eine Grußformel ist, sind nicht aktivierbar. „Aktiviere Guten Morgen.“ → „Guten Morgen kann ich nicht steuern, nur abfragen.“; „Aktiviere die Szene Guten Morgen.“ → „Welches **Gerät** meinst du: Abwesend, Filmabend oder Guten Morgen?“ | live |
| **F3** | mittel | Mengen bei relativen Änderungen werden ignoriert: „Mach die Heizung im Bad zwei Grad wärmer.“ → +1 Grad (22 → 23). | live |
| **F4** | mittel (Regression) | „oben“/„unten“ in **Anzahl- und Bestandsfragen** zu Beschattungen wird als Position gelesen, die Etage fällt weg (ganzes Haus gezählt). Die 7.6-Regel „oben am Satzende nach Beschattungsgattung = Position“ ist zu breit; sie darf nur bei Zustandsfragen („Sind … oben/unten?“) greifen. Bei Lichtern stimmt es. | unveröffentlichter Korpus (1 Fall, ok → fail) |
| F5 | niedrig | Nicht freigegebene Geräte werden irreführend beschrieben: „Starte den Saugroboter.“ → „Im Haus gibt es keinen Staubsauger.“; „Schalte die Kaffeemaschine ein.“ → „mehrere passende Geräte (Kaffeemaschine entkalken, Leistung Kaffeemaschine) …“ | live |
| F6 | niedrig | „Welche Routine nutzt du fürs Schlafen?“ nicht verstanden (Verschmelzung „fürs“). | live |
| F7 | niedrig | „Mach alles für die Nacht fertig.“ startet den Dialog zum **Anlegen** einer Routine („Was soll ich bei schlafengehen erledigen?“), statt die vorhandenen Schlafroutinen anzubieten; dazu Grammatik („bei schlafengehen“). | live |
| F8 | niedrig | Höflich-abgeschwächter Befehl mit mehreren Partikeln („Könntest du vielleicht irgendwann mal die Markise einfahren?“) wird weiter als Zustandsfrage beantwortet („Markise ist geöffnet.“). Das war ausdrücklich Teil von Phase 9. | bekannter Korpus |
| F9 | Beobachtung | Generalisierung auf echten ungesehenen Sätzen: 51 %. Weiter offene **Bedeutungsklassen**: bildhafte Bedürfnisse (Sonne, beschlagener Spiegel, Redewendungen für Dunkelheit), verblose Kurzbefehle mit Richtungspartikel und Menge, Bedarfs-/Sorge-/Anwesenheitsfragen je Etage, Push-Wünsche ohne Benachrichtigungsverb („… will ich das wissen“, „Meld dich bei …“), Absichtserklärungen, „Tür zu“ bei einem Schloss. | unveröffentlichter Korpus |
| F10 | Beobachtung | Der Arbiter ist nur für Bedürfnis ↔ Frage umgeschaltet; sonst gilt weiter die first-match-Kaskade (so im Umsetzungsbericht dokumentiert). | Bericht |

Keiner der Befunde führt zu einer falschen Geräteaktion.

## 4. Hinweis zum Ablauf

Mein eigenes Prüfskript hat im ersten Durchgang das Test-Skript „Gute Nacht“ des Testhauses
überschrieben und danach gelöscht. Die Korpus- und README-Läufe wurden deshalb mit
wiederhergestelltem Testhaus vollständig wiederholt; nur die wiederholten Ergebnisse stehen oben.

## Rohdaten

Die Ergebnisse liegen in der Test-Session (Scratchpad). Der unveröffentlichte Korpus wird
bewusst nicht eingecheckt.
