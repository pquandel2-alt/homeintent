# Nachtest: HomeIntent 7.3.0 (Sprachverständnis ohne Sprachmodell)

Stand: 27. September 2026 · getestet: Branch `claude/sprachverstaendnis-prompt-dkzpr9`,
Commit `df3ae85` (noch nicht in `main`) · frisches, echtes Home Assistant 2026.9.2 ·
Testhaus mit der ursprünglichen Automations-Grundkonfiguration (siehe unten)

## Ergebnis

| Messung | 7.2.1 | 7.3.0 |
| --- | --- | --- |
| Unit-Tests (hassil 3.11 und 3.12) | 4588 grün | 6034 grün |
| Funktionsszenarien (`sim/runner.py`) | 126 / 126 | 162 / 162 |
| README-Beispiele (`sim/readme_check.py`) | 23 echte Fehler | 0 echte Fehler (3 verbleibende sind Konfigurationszeilen bzw. ein fehlender Listeneintrag) |
| Push-Matrix mit echter Auslösung (`sim/push_check.py`) | 23 / 35 | **35 / 35** |
| Bekannter Alltagskorpus (`sim/nlu_probe.py`, 66 Sätze) | 11 / 66 (17 %) | 56 / 66 (85 %) |
| **Unveröffentlichter Korpus (45 nie gezeigte Sätze)** | 12 / 45 (27 %) | **17 / 45 (38 %)** |
| Falsch geschaltete Geräte (alle Korpora) | 3 | **0** |
| HomeIntent-Befunde im HA-Log | 0 | 0 |

Die Korrekturen wirken und sind sicher: keine einzige falsche Geräteaktion, alle
Sicherheitsfehler S1–S7 behoben, Push vollständig. Auf **nie gesehenen** Sätzen fällt der
Zuwachs aber deutlich kleiner aus (+5 statt +45 Sätze). Das Sprachverständnis
generalisiert also nur begrenzt über die Sätze und Fälle hinaus, die in den
veröffentlichten Korpora und im Auftrag standen.

Typische ungesehene Sätze, die weiterhin scheitern (Auswahl, Bedeutung umschrieben):
kurze Umgangs-Befehle mit anderen Verben („… anwerfen“, „… los“, „… zwei Grad rauf“),
Beschwerden über Geräte („das Radio nervt“), bildhafte Zustandsbeschreibungen (Sonne,
beschlagener Spiegel, Dunkelheit), Ellipsen wie „und die linke auch“ und „doch nicht“,
Varianten bekannter Fragen („steht irgendwo ein Fenster offen?“ statt „sind alle Fenster
zu?“), Ereignis-Push mit freier Wortstellung ohne „benachrichtige/sag Bescheid“.

Weitere Beobachtungen:
- „Dann dreh da die Heizung hoch.“ nach einer Temperaturfrage antwortet „Heizung
  Kinderzimmer unterstützen diese Aktion nicht“ – falsche Begründung, die Heizung kann das.
- „Könntest du vielleicht irgendwann mal die Markise einfahren?“ wird als Zustandsfrage
  beantwortet statt ausgeführt.

## Architektur (Abgleich mit dem Architektur-Briefing)

- **Zwei Geräteauflösungen:** Neu ist `nlu/target_resolution.py` (Gattung × Ort × Menge),
  daneben bleibt `nlu/entity_resolution.py` bestehen (24 Nutzer gegenüber 8). Der neue
  Resolver greift nicht auf den alten zurück; ein Shadow-Vergleich fehlt.
- **Sprachinseln:** Push wurde auf die gemeinsame Bedeutung umgestellt (24 → 1 Regex).
  Verlauf, Listen, Kalender und Automationsverwaltung arbeiten weiter mit eigenen Mustern
  (Regex gesamt 824 → 770).
- **Ein großes Release:** 98 Dateien, rund 17 000 Zeilen.
- **Testbett:** Die Session hat 16 Test-Automationen in `sim/config/automations.yaml`
  eingecheckt (18 statt 2). Sie verändern das Testhaus und wurden für diesen Nachtest
  durch die ursprüngliche Datei ersetzt.

## Einordnung

Freigabe aus Sicherheitssicht unbedenklich (0 Fehlschaltungen, alle Tests grün). Die
Messwerte im README 7.3.0 beziehen sich aber auf die veröffentlichten Korpora; für ein
ehrliches Bild sollte der Wert auf ungesehenen Sätzen (38 %) mit genannt werden. Der
nächste Schritt sollte nach dem Architektur-Briefing erfolgen: eine Geräteauflösung,
Bereich für Bereich mit Shadow-Vergleich, Messung an ungesehenen Sätzen und an der
kanonischen Bedeutung.
