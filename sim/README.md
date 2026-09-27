# HomeIntent Live-Testbett (Haus-Simulation)

Echtes Home Assistant (2026.9.2, Python 3.14) mit einem simulierten
Einfamilienhaus, gegen das HomeIntent Ende-zu-Ende über die
Conversation-WebSocket-API getestet wird. Anders als `tests/` (Stub) und
`tests_ha/` (Options Flow) laufen hier echte Serviceaufrufe, echte Registries,
echter Recorder, echte Automationen, Timer, Kalender und Listen.

## Das Haus

`custom_components/haus_sim/house.py` – Familie mit drei Personen
(Philipp = Admin, Anna = Benutzerin, Lena = Kind ohne Tracker), 4 Etagen
(Keller, EG, OG, Außenbereich), 15 Räume, rund 125 Geräte:
23 Lichter (an/aus, dimmbar, Farbtemperatur, RGB), 10 Rollläden/Raffstore/
Garagentor/Markise mit realistischer Fahrzeit, 6 Heizungen mit
Temperaturverlauf, Temperatur-/Feuchte-/CO2-/Leistungs-/Energie-/Batterie-
Sensoren, Fenster-/Tür-/Bewegungs-/Präsenz-/Rauch-/Wassermelder,
Media Player, Ventilatoren, Saug- und Mähroboter, Schlösser, Ventile,
Luftbefeuchter, Warmwasser, Alarmanlage, Select/Number/Buttons,
Notify-Entities (Handys), TTS, Presence-Tracker. Dazu Szenen, Skripte,
zwei „fremde“ Benutzer-Automationen, `timer`-/`input_*`-Helfer,
zwei lokale Kalender, zwei Listen und 10 Tage importierte Recorder-Statistik.

Jedes Gerät protokolliert empfangene Aufrufe (`haus_sim.get_log`);
`haus_sim.set` treibt Sensoren, `haus_sim.reset` stellt den Ausgangszustand her.

## Benutzung

```bash
# einmalig: echtes HA installieren (Python >= 3.14.2)
uv venv --python 3.14 ../havenv && uv pip install --python ../havenv/bin/python -r ../requirements-ha-test.txt

HASS=../havenv/bin/hass ./run_ha.sh        # HA starten/neu starten (Port 8123)
../havenv/bin/python bootstrap.py          # Onboarding, Benutzer, Integrationen, Freigaben, Statistik
../havenv/bin/python runner.py             # alle Szenarien -> results/run.json
../havenv/bin/python runner.py --category Kalender -v
../havenv/bin/python say.py "Schalte das Küchenlicht ein." "Und jetzt wieder aus."
../havenv/bin/python dump.py results/run.json dev-light   # Transkripte
../havenv/bin/python lc_check.py           # Learning-Center-WebSocket-API
```

`config/` enthält nur die versionierte Grundkonfiguration; Laufzeitdaten
(`.storage`, Datenbank, Tokens, Logs) sind per `.gitignore` ausgeschlossen.
Für einen sauberen Neustart `config/.storage`, `config/home-assistant_v2.db*`
löschen und `automations.yaml` aus git wiederherstellen.

`./fresh_ha.sh` erledigt den sauberen Neustart samt `bootstrap.py` in einem
Schritt (für Vergleichsläufe immer frisch starten: Listen, Bindungen und
Verlauf eines früheren Laufs verfälschen sonst einzelne Szenarien).
