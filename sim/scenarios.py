"""Scenario catalogue for the live HomeIntent test bed.

The expectations describe what a German household would reasonably expect,
based on the behaviour documented in README.md. A failing expectation is a
finding to be triaged, not automatically a HomeIntent bug.
"""

from __future__ import annotations

from typing import Any

SCENARIOS: list[dict[str, Any]] = []


def S(sid: str, category: str, title: str, *steps: dict[str, Any], **extra: Any) -> None:
    SCENARIOS.append({"id": sid, "category": category, "title": title, "steps": list(steps), **extra})


def say(text: str, user: str = "admin", device: str | None = None, settle: float = 1.0, conv: str | None = None,
        satellite: bool = False, household: bool = False, **expect: Any) -> dict[str, Any]:
    step: dict[str, Any] = {"say": text, "user": user, "settle": settle}
    if household:
        step["household"] = True
    if device:
        step["device"] = device
    if satellite:
        step["satellite"] = True
    if conv:
        step["conv"] = conv
    if expect:
        step["expect"] = expect
    return step


def pipeline(text: str, settle: float = 1.5, conv: str | None = None, **expect: Any) -> dict[str, Any]:
    step: dict[str, Any] = {"pipeline": text, "settle": settle}
    if conv:
        step["conv"] = conv
    if expect:
        step["expect"] = expect
    return step


def wait(seconds: float) -> dict[str, Any]:
    return {"wait": seconds}


def set_(entity_id: str, value: Any, settle: float = 0.5) -> dict[str, Any]:
    return {"set": entity_id, "value": value, "settle": settle}


def check(**expect: Any) -> dict[str, Any]:
    return {"check": True, "expect": expect}


def options(**changes: Any) -> dict[str, Any]:
    return {"options": changes}


def service(name: str, data: dict | None = None, response: bool = False) -> dict[str, Any]:
    return {"service": name, "data": data or {}, "response": response}


YES = "Ja."

# ========================================================= 1. Geräte
S("dev-light-onoff", "Geräte", "Licht ein/aus/umschalten",
  say("Schalte das Küchenlicht ein.", type="action_done", calls=["light.kuechenlicht:turn_on"], only_calls=True, state={"light.kuechenlicht": "on"}),
  say("Mach das Küchenlicht aus.", calls=["light.kuechenlicht:turn_off"], state={"light.kuechenlicht": "off"}),
  say("Schalte die Stehlampe um.", calls=["light.stehlampe:turn_on"], state={"light.stehlampe": "on"}))
S("dev-light-brightness", "Geräte", "Helligkeit absolut/relativ",
  say("Stelle die Stehlampe auf 30 Prozent.", state={"light.stehlampe": {"state": "on", "brightness": 77}}),
  say("Mach die Stehlampe etwas heller.", calls=["light.stehlampe:turn_on"]),
  check(state={"light.stehlampe": {"state": "on"}}),
  say("Dimme die Stehlampe auf zehn Prozent.", state={"light.stehlampe": {"brightness": 26}}))
S("dev-light-color", "Geräte", "Farbe und Farbtemperatur",
  say("Mach den LED-Streifen im Wohnzimmer blau.", calls=["light.wohnzimmer_led_streifen:turn_on"], state={"light.wohnzimmer_led_streifen": "on"}),
  say("Stelle das Wohnzimmer Deckenlicht auf warmweiß.", calls=["light.wohnzimmer_deckenlicht:turn_on"]),
  say("Mach die Schreibtischlampe rot.", type="error", no_calls=True))
S("dev-switch", "Geräte", "Schalter",
  say("Schalte die Kaffeemaschine ein.", calls=["switch.kaffeemaschine:turn_on"], state={"switch.kaffeemaschine": "on"}),
  say("Ist die Kaffeemaschine an?", type="query_answer", any=["ja", "eingeschaltet", "an"]))
S("dev-cover-position", "Geräte", "Rollläden öffnen/schließen/Position",
  say("Fahre den Küchenrollladen runter.", settle=4, calls=["cover.kuechenrollladen:close_cover"], state={"cover.kuechenrollladen": "closed"}),
  say("Fahre den Küchenrollladen auf 40 Prozent.", settle=4, state={"cover.kuechenrollladen": {"current_position": 40}}),
  say("Fahre den Küchenrollladen komplett hoch.", settle=4, state={"cover.kuechenrollladen": {"current_position": 100}}))
S("dev-cover-fractions", "Geräte", "Bruchteile und Zahlwörter",
  say("Fahre den Schlafzimmer Rollladen zur Hälfte.", settle=4, state={"cover.schlafzimmer_rollladen": {"current_position": 50}}),
  say("Fahre den Schlafzimmer Rollladen auf drei Viertel.", settle=4, state={"cover.schlafzimmer_rollladen": {"current_position": 75}}))
S("dev-cover-tilt", "Geräte", "Lamellen Raffstore",
  say("Stelle die Lamellen vom Büro Raffstore auf 20 Prozent.", calls=["cover.buero_raffstore:set_cover_tilt_position"], state={"cover.buero_raffstore": {"current_tilt_position": 20}}))
S("dev-garage-confirm", "Geräte", "Garagentor nur nach Bestätigung",
  say("Öffne das Garagentor.", no_calls=True, any=["soll", "sicher", "bestätig"], continue_=None),
  say(YES, settle=8, calls=["cover.garagentor:open_cover"], state={"cover.garagentor": "open"}),
  say("Schließ das Garagentor.", settle=8))
S("dev-climate", "Geräte", "Heizung Solltemperatur/Preset/Modus",
  say("Stelle die Heizung im Wohnzimmer auf 22 Grad.", calls=["climate.heizung_wohnzimmer:set_temperature"], state={"climate.heizung_wohnzimmer": {"temperature": 22}}),
  say("Stelle die Heizung im Büro auf zweiundzwanzig Grad.", state={"climate.heizung_buero": {"temperature": 22}}),
  say("Mach die Heizung im Schlafzimmer stark wärmer.", calls=["climate.heizung_schlafzimmer:set_temperature"]),
  say("Stelle die Heizung in der Küche auf Eco.", calls=["climate.heizung_kueche:set_preset_mode"], state={"climate.heizung_kueche": {"preset_mode": "eco"}}),
  say("Schalte die Heizung im Badezimmer aus.", state={"climate.heizung_badezimmer": "off"}))
S("dev-climate-missing-value", "Geräte", "Fehlende Temperatur wird erfragt",
  say("Stelle die Heizung im Büro ein.", no_calls=True, any=["welche temperatur", "auf welche"]),
  say("Auf 21 Grad.", state={"climate.heizung_buero": {"temperature": 21}}))
S("dev-fan", "Geräte", "Ventilator Stufe/Preset/Oszillation",
  # fan.turn_on with percentage is Home Assistant's documented equivalent of
  # fan.set_percentage; the observable result (60 %) is what is asserted.
  say("Stelle den Deckenventilator auf Stufe 3.", calls=["fan.deckenventilator:turn_on"], state={"fan.deckenventilator": {"percentage": 60}}),
  say("Stelle den Deckenventilator auf Nacht.", state={"fan.deckenventilator": {"preset_mode": "Nacht"}}),
  say("Schalte beim Deckenventilator die Oszillation ein.", state={"fan.deckenventilator": {"oscillating": True}}),
  say("Schalte den Badlüfter ein.", state={"fan.badluefter": "on"}))
S("dev-media", "Geräte", "Media Player",
  say("Pausiere das Küchenradio.", calls=["media_player.kuechenradio:media_pause"], state={"media_player.kuechenradio": "paused"}),
  say("Bitte weiterspielen.", calls=["media_player.kuechenradio:media_play"]),
  say("Stelle die Lautstärke vom Küchenradio auf 20 Prozent.", state={"media_player.kuechenradio": {"volume_level": 0.2}}),
  say("Schalte den Wohnzimmer TV auf Netflix.", state={"media_player.wohnzimmer_tv": {"source": "Netflix"}}))
S("dev-vacuum", "Geräte", "Saugroboter",
  say("Starte den Saugroboter.", calls=["vacuum.saugroboter:start"], state={"vacuum.saugroboter": "cleaning"}),
  say("Läuft der Saugroboter?", type="query_answer", any=["ja", "saugt", "reinig"]),
  say("Stelle beim Saugroboter die Saugstufe auf stark.", state={"vacuum.saugroboter": {"fan_speed": "stark"}}),
  say("Schicke den Saugroboter zur Ladestation.", calls=["vacuum.saugroboter:return_to_base"]))
S("dev-lawnmower", "Geräte", "Mähroboter",
  say("Starte den Mähroboter.", calls=["lawn_mower.maehroboter:start_mowing"]),
  say("Was macht der Mähroboter?", type="query_answer", any=["mäht", "aktiv"]),
  say("Schick den Mähroboter zurück zur Ladestation.", calls=["lawn_mower.maehroboter:dock"]))
# Locks are HIGH risk in HomeIntent's ExecutionPolicy (risk.py): locking asks
# once as well, unlocking is CRITICAL. The scenario keeps that boundary.
S("dev-lock", "Sicherheit", "Schloss: verriegeln und entriegeln nur mit Bestätigung",
  say("Entriegle das Haustürschloss.", no_calls=True, any=["soll", "sicher", "bestätig", "wirklich"]),
  say(YES, calls=["lock.haustuerschloss:unlock"], state={"lock.haustuerschloss": "unlocked"}),
  say("Verriegle die Haustür.", no_calls=True, any=["haustürschloss"]),
  say(YES, calls=["lock.haustuerschloss:lock"], state={"lock.haustuerschloss": "locked"}))
S("dev-lock-deny", "Sicherheit", "Entriegeln abgelehnt mit Nein",
  say("Schließ das Gartentor auf.", no_calls=True),
  say("Nein.", no_calls=True, state={"lock.gartentor_schloss": "locked"}))
S("dev-valve", "Geräte", "Ventile nach Bestätigung",
  say("Öffne die Bewässerung im Garten.", no_calls=True),
  say(YES, state={"valve.bewaesserung": "open"}))
S("dev-humidifier", "Geräte", "Luftbefeuchter",
  say("Stelle den Luftbefeuchter auf 50 Prozent.", state={"humidifier.luftbefeuchter": {"humidity": 50}}),
  say("Stelle den Luftbefeuchter auf den Modus Schlaf.", state={"humidifier.luftbefeuchter": {"mode": "Schlaf"}}))
S("dev-waterheater", "Geräte", "Warmwasserspeicher",
  say("Stelle den Warmwasserspeicher auf 55 Grad.", state={"water_heater.warmwasserspeicher": {"temperature": 55}}),
  say("Stelle den Warmwasserspeicher auf performance.", state={"water_heater.warmwasserspeicher": {"operation_mode": "performance"}}))
S("dev-select-number", "Geräte", "Select / Number / Input-Helfer",
  say("Wähle beim Heizprogramm Eco.", state={"select.heizprogramm": "Eco"}),
  say("Stelle die Poolpumpe Drehzahl auf 1800.", state={"number.poolpumpe_drehzahl": "1800.0"}),
  say("Stelle die Poolheizung Solltemperatur auf 28 Grad.", state={"input_number.poolheizung_soll": "28.0"}),
  say("Schalte den Gästemodus ein.", state={"input_boolean.gaestemodus": "on"}))
S("dev-button", "Geräte", "Taste nach Bestätigung",
  say("Drücke Kaffeemaschine entkalken.", no_calls=True),
  say(YES, calls=["button.kaffeemaschine_entkalken:press"]))
S("dev-scene-script", "Geräte", "Szene und Skript",
  say("Aktiviere die Szene Filmabend.", settle=2, state={"light.wohnzimmer_led_streifen": "on", "light.stehlampe": "on"}),
  say("Starte das Skript Kaffee kochen.", settle=2, state={"switch.kaffeemaschine": "on"}))
S("dev-alarm", "Sicherheit", "Alarmanlage scharf schalten (kritisch)",
  say("Schalte die Alarmanlage scharf.", no_calls=True),
  say(YES, calls=["alarm_control_panel.alarmanlage:arm_away"]))
S("dev-notify-entity", "Geräte", "Nachricht an benanntes Notify-Ziel",
  say("Schicke an Handy Anna die Nachricht Abendessen ist fertig.", notify="Abendessen ist fertig"))

# ================================================ 2. Natürliche Sprache
for i, text in enumerate([
    "Kannst du das Küchenlicht anmachen?",
    "Wäre es möglich, das Küchenlicht einzuschalten?",
    "Ich hätte gerne das Küchenlicht an.",
    "Sorge bitte dafür, dass das Küchenlicht an ist.",
    "Das Küchenlicht soll an sein.",
    "Küchenlicht an.",
    "Licht in der Küche an bitte.",
    "Mach mal bitte in der Küche das Licht an.",
]):
    S(f"nl-paraphrase-{i}", "Sprache", f"Paraphrase: {text}", say(text, calls=["light.kuechenlicht:turn_on"], state={"light.kuechenlicht": "on"}))

S("nl-word-order", "Sprache", "Freie Wortstellung",
  say("Im Erdgeschoss bitte alle Rollläden hochfahren.", settle=4, no_calls=False),
  say("Nach oben fahren sollen im Obergeschoss alle Rollläden.", settle=4))
S("nl-negation", "Sprache", "Negation führt nichts aus",
  say("Mach das Küchenlicht nicht an.", no_calls=True, state={"light.kuechenlicht": "off"}))
S("nl-hypothetical", "Sprache", "Hypothetisch führt nichts aus",
  say("Wenn ich wollte, könnte ich das Küchenlicht einschalten.", no_calls=True),
  say("Ich überlege, ob ich das Wohnzimmer Deckenlicht anmache.", no_calls=True))
S("nl-whatif", "Sprache", "Was-passiert-wenn-Frage bleibt lesend",
  say("Was passiert, wenn die Bewegung im Flur erkannt wird?", no_calls=True))
S("nl-typo", "Sprache", "Tipp-/ASR-Fehler",
  say("Schalte das Küchenlihct ein.", any=["küchenlicht"]),
  say("Schalte das Kuechenlicht ein.", calls=["light.kuechenlicht:turn_on"]))
S("nl-alias", "Sprache", "Entity-Alias (Leselampe) und Bereichsalias (Bad, Stube)",
  say("Mach die Leselampe an.", calls=["light.stehlampe:turn_on"]),
  say("Mach das Licht im Bad an.", calls=["light.badezimmerlicht:turn_on", "light.spiegelschrank:turn_on"]),
  say("Wie warm ist es in der Stube?", type="query_answer", any=["grad"]))
S("nl-offtopic", "Sprache", "Themenfremd / Unsinn / Englisch",
  say("Wer hat die Fußball-WM 2014 gewonnen?", no_calls=True),
  say("Blubb flurp zack.", no_calls=True),
  say("Turn on the kitchen light.", no_calls=False))
S("nl-explain", "Sprache", "Was hast du verstanden?",
  say("Schalte in Küche und Flur alle Lichter aus, außer dem Flurlicht."),
  say("Was hast du verstanden?", no_calls=True, any=["küche", "flur"]))

# ================================================ 3. Mengen, Orte, Ausschlüsse
S("set-floor-all", "Mengen/Orte", "Alle Rollläden im Erdgeschoss auf 50 %",
  say("Fahre alle Rollläden im Erdgeschoss auf 50 Prozent.", settle=4,
      calls=["cover.wohnzimmer_rollladen_links:set_cover_position", "cover.wohnzimmer_rollladen_rechts:set_cover_position", "cover.kuechenrollladen:set_cover_position", "cover.esszimmer_rollladen:set_cover_position"],
      not_calls=["cover.schlafzimmer_rollladen:set_cover_position", "cover.garagentor:open_cover"]))
S("set-both", "Mengen/Orte", "Beide Rollläden im Wohnzimmer",
  say("Fahre beide Rollläden im Wohnzimmer runter.", settle=4, calls=["cover.wohnzimmer_rollladen_links:close_cover", "cover.wohnzimmer_rollladen_rechts:close_cover"], only_calls=True))
S("set-two-areas", "Mengen/Orte", "Lichter in Küche und Flur",
  service("light.turn_on", {"entity_id": ["light.kuechenlicht", "light.kuecheninsel", "light.flurlicht", "light.buerolicht"]}),
  say("Schalte alle Lichter in Küche und Flur aus.", calls=["light.kuechenlicht:turn_off", "light.kuecheninsel:turn_off", "light.flurlicht:turn_off"], only_calls=True, state={"light.buerolicht": "on"}))
S("set-except", "Mengen/Orte", "Alle Lichter aus außer …",
  service("light.turn_on", {"entity_id": ["light.stehlampe", "light.wohnzimmer_deckenlicht", "light.nachtlicht", "light.kuechenlicht"]}),
  say("Mach alle Lichter aus außer der Stehlampe und dem Nachtlicht.", state={"light.stehlampe": "on", "light.nachtlicht": "on", "light.wohnzimmer_deckenlicht": "off", "light.kuechenlicht": "off"}, not_calls=["light.stehlampe:turn_off", "light.nachtlicht:turn_off"]))
S("set-few", "Mengen/Orte", "„Ein paar Lichter“ wird nicht geraten",
  say("Mach ein paar Lichter an.", no_calls=True, any=["welche"]))
S("set-number", "Mengen/Orte", "Die drei Lampen im Schlafzimmer",
  say("Mach die drei Lampen im Schlafzimmer an.", calls=["light.schlafzimmerlicht:turn_on", "light.nachttischlampe_links:turn_on", "light.nachttischlampe_rechts:turn_on"]))
S("set-upstairs", "Mengen/Orte", "Etagen-Alias „oben“",
  say("Schalte oben alle Lichter an.", calls=["light.schlafzimmerlicht:turn_on", "light.kinderzimmerlicht:turn_on", "light.badezimmerlicht:turn_on", "light.flurlicht_oben:turn_on"], not_calls=["light.kuechenlicht:turn_on"]))
S("set-max-targets", "Sicherheit", "Ganzes Haus: Grenze gleichzeitiger Ziele",
  say("Schalte im ganzen Haus alle Lichter ein."))

# ================================================ 4. Mehrdeutigkeit / Kontext
S("amb-nightstand", "Dialog", "Nachttischlampe links/rechts → Rückfrage → Auswahl",
  say("Mach die Nachttischlampe an.", no_calls=True, any=["welche", "meinst du"], continue_=None),
  say("Die linke.", calls=["light.nachttischlampe_links:turn_on"], only_calls=True))
S("amb-ordinal", "Dialog", "Auswahl per Ordinalzahl",
  say("Mach die Nachttischlampe an.", no_calls=True),
  say("Die zweite.", calls=["light.nachttischlampe_rechts:turn_on"], only_calls=True))
S("amb-invalid-choice", "Dialog", "Ungültige Auswahl verwirft Rückfrage nicht",
  say("Mach die Nachttischlampe an.", no_calls=True),
  say("Die fünfte.", no_calls=True),
  say("Die rechte.", calls=["light.nachttischlampe_rechts:turn_on"]))
S("amb-generic-light", "Dialog", "„Mach das Licht an“ ohne Raum",
  say("Mach das Licht an.", no_calls=True))
S("ctx-here", "Dialog", "„hier“ über Satellitenbereich (Büro)",
  say("Schalte das Licht hier ein.", device="Büro", calls=["light.buerolicht:turn_on"], not_calls=["light.kuechenlicht:turn_on"]))
S("ctx-roomless-cover", "Dialog", "Raumloser Rollladenbefehl im Satellitenraum",
  say("Fahr den Rollladen auf 30 Prozent.", device="Schlafzimmer", settle=4, calls=["cover.schlafzimmer_rollladen:set_cover_position"], only_calls=True))
S("ctx-followup-area", "Dialog", "Folgefrage „Und in der Küche?“",
  say("Wie warm ist es im Wohnzimmer?", type="query_answer", any=["grad"]),
  say("Und im Büro?", type="query_answer", any=["grad"]))
S("ctx-query-then-command", "Dialog", "Temperaturfrage → Erhöhung",
  say("Wie warm ist es im Büro?", type="query_answer"),
  say("Kannst du die Temperatur auf 23 Grad erhöhen?", state={"climate.heizung_buero": {"temperature": 23}}))
S("ctx-undo", "Dialog", "Rückgängig machen",
  say("Fahre den Esszimmer Rollladen auf 40 Prozent.", settle=4),
  say("Mach das rückgängig.", settle=4, state={"cover.esszimmer_rollladen": {"current_position": 100}}))
S("ctx-pronoun", "Dialog", "Pronomen „es“ auf zuletzt genanntes Gerät",
  say("Starte das Küchenradio."),
  say("Kannst du es pausieren?", calls=["media_player.kuechenradio:media_pause"]))
S("ctx-correction", "Dialog", "Korrektur „nein, im Esszimmer“",
  say("Schalte das Licht im Büro an.", calls=["light.buerolicht:turn_on"]),
  say("Nein, ich meinte im Esszimmer.", calls=["light.esszimmer_pendelleuchte:turn_on"], state={"light.esszimmer_pendelleuchte": "on"}))


# ================================================ 5. Abfragen
S("q-state", "Abfragen", "Zustandsfragen",
  say("Ist das Badezimmerfenster geschlossen?", type="query_answer", any=["nein", "geöffnet", "offen"]),
  say("Welchen Zustand hat das Badezimmerfenster?", type="query_answer", any=["geöffnet", "offen"]),
  say("Ist irgendein Fenster offen?", type="query_answer", any=["ja"]),
  say("Ist kein Fenster offen?", type="query_answer", any=["nein", "doch", "küchenfenster", "badezimmerfenster"]),
  say("Wie viele Fenster sind im Erdgeschoss geöffnet?", type="query_answer", any=["1", "ein", "eins"]),
  say("Sind im Erdgeschoss offene Fenster?", type="query_answer", any=["ja", "küchenfenster"]),
  say("Wo sind Fenster offen?", type="query_answer", all=["küche", "bad"]),
  say("Ist die Haustür zu?", type="query_answer", any=["ja", "geschlossen"]))
S("q-covers", "Abfragen", "Rollladen-Abfragen",
  say("Sind alle Rollläden hochgefahren?", type="query_answer", any=["ja", "geöffnet", "offen"]),
  say("Welche Rollläden gibt es im Erdgeschoss?", type="query_answer", all=["küchenrollladen", "esszimmer"], none=["ja, es gibt"]),
  say("Ist das Garagentor offen?", type="query_answer", any=["nein", "geschlossen"]))
S("q-area-on", "Abfragen", "Was ist im Raum eingeschaltet?",
  service("light.turn_on", {"entity_id": ["light.badezimmerlicht"]}),
  service("switch.turn_on", {"entity_id": ["switch.heizluefter_bad"]}),
  say("Was ist im Badezimmer eingeschaltet?", type="query_answer", all=["badezimmerlicht", "heizlüfter"]),
  say("Ist das Radio in der Küche an?", type="query_answer", any=["ja", "spielt", "wiedergabe", "läuft"]))
S("q-measure", "Abfragen", "Messwerte",
  say("Wie warm ist es im Wohnzimmer?", type="query_answer", any=["grad"]),
  say("Wie hoch ist die Luftfeuchtigkeit im Badezimmer?", type="query_answer", all=["68"]),
  say("Wie warm ist es draußen?", type="query_answer", any=["12"]),
  say("Wie hoch ist die Temperatur im Obergeschoss?", type="query_answer", any=["grad"]),
  say("Wie hoch ist der CO2-Wert im Wohnzimmer?", type="query_answer", any=["812"]),
  say("Wie viel Strom verbraucht das Haus gerade?", type="query_answer", any=["432"]))
S("q-battery", "Abfragen", "Batterien unter Schwelle",
  say("Welche Batterien sind unter 20 Prozent?", type="query_answer", all=["bad", "rauchmelder"], none=["küche"]),
  say("Welche Batterien oben sind unter 20 Prozent?", type="query_answer", any=["rauchmelder", "bad"]))
S("q-compare", "Abfragen", "Vergleiche und Superlative",
  service("light.turn_on", {"entity_id": "light.stehlampe", "brightness_pct": 80}),
  service("light.turn_on", {"entity_id": "light.wohnzimmer_deckenlicht", "brightness_pct": 20}),
  say("Welche Lichter im Wohnzimmer sind heller als 50 Prozent?", type="query_answer", all=["stehlampe"], none=["deckenlicht"]),
  say("Welches Gerät verbraucht gerade am meisten Strom?", type="query_answer", any=["waschmaschine"]),
  say("Welcher Raum ist am wärmsten?", type="query_answer", any=["bad"]),
  say("Wie hoch ist die durchschnittliche Temperatur im Haus?", type="query_answer", any=["grad"]))
S("q-appliance", "Abfragen", "Fertigstellungszeit Waschmaschine",
  say("Wann ist die Waschmaschine fertig?", type="query_answer", any=["uhr", "minuten"]))
S("q-readiness", "Abfragen", "Bereitschaftscheck",
  say("Ist HomeIntent bereit?", type="query_answer"))
S("q-count-on", "Abfragen", "Wie viele Lichter sind an?",
  service("light.turn_on", {"entity_id": ["light.kuechenlicht", "light.flurlicht", "light.buerolicht"]}),
  say("Wie viele Lichter sind an?", type="query_answer", any=["3", "drei"]),
  say("Welche davon sind im Erdgeschoss?", type="query_answer", all=["küchenlicht", "flurlicht", "bürolicht"]),
  say("Schalte die aus.", calls=["light.kuechenlicht:turn_off", "light.flurlicht:turn_off", "light.buerolicht:turn_off"]))
S("q-persons", "Abfragen", "Wer ist zu Hause?",
  say("Wer ist zu Hause?", type="query_answer", all=["philipp", "anna"]),
  set_("device_tracker.handy_anna", "not_home"),
  say("Ist Anna zu Hause?", type="query_answer", any=["nein", "nicht"]))
S("q-climate-state", "Abfragen", "Heizungsabfragen",
  say("Auf wie viel Grad ist die Heizung im Wohnzimmer gestellt?", type="query_answer", any=["21"]),
  say("Heizt die Heizung im Büro gerade?", type="query_answer", any=["ja", "heizt"]))

# ================================================ 6. Recorder-Statistik
S("stat-mean", "Statistik", "Recorder: Mittelwert/Min/Max/Veränderung",
  say("Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?", type="query_answer", any=["grad"], none=["keine"]),
  say("Was war gestern der höchste Wert vom Stromverbrauch Haus?", type="query_answer", any=["watt", " w"], none=["keine"]),
  say("Wie hat sich der Energiezähler diese Woche verändert?", type="query_answer", any=["kwh", "kilowattstunden"]),
  say("War die Temperatur im Wohnzimmer gestern niedriger als vorgestern?", type="query_answer"),
  say("Wie kalt war es gestern draußen minimal?", type="query_answer", any=["grad"]))
S("stat-history", "Statistik", "Recorder: Zustandsdauer/Wechsel heute",
  set_("binary_sensor.wohnzimmerfenster", "on"), wait(2), set_("binary_sensor.wohnzimmerfenster", "off"), wait(1),
  set_("binary_sensor.wohnzimmerfenster", "on"), wait(2), set_("binary_sensor.wohnzimmerfenster", "off"), wait(2),
  say("Wie oft war das Wohnzimmerfenster heute offen?", type="query_answer", any=["2", "zwei", "zweimal"]),
  say("Wie lange war das Wohnzimmerfenster heute geöffnet?", type="query_answer", any=["sekunde", "minute"]))

# ================================================ 7. Kalender
S("cal-oneshot", "Kalender", "Termin in einem Satz + Bestätigung",
  say("Trag nächsten Dienstag um 10 Uhr für eine Stunde Zahnarzt in den Kalender Familie ein.", no_calls=True, any=["zahnarzt"]),
  say(YES, any=["eingetragen", "erstellt", "angelegt"]),
  say("Wann ist mein Zahnarzttermin?", any=["10:00", "10 uhr"]))
S("cal-dialog", "Kalender", "Termin im Dialog ergänzen",
  say("Trag einen Termin ein.", any=["wie soll", "heißen", "welche"]),
  say("Elternabend."),
  say("Übermorgen."),
  say("Um 19 Uhr."),
  say("Zwei Stunden."),
  say("Im Kalender Familie."),
  say(YES, any=["eingetragen", "erstellt", "angelegt"]),
  say("Was steht übermorgen in meinem Kalender?", any=["elternabend"]))
S("cal-manage", "Kalender", "Verschieben, umbenennen, freie Zeit, löschen",
  say("Erstelle morgen von 14 bis 15 Uhr den Termin Planung im Kalender Familie."), say(YES),
  say("Habe ich morgen zwischen 14 und 16 Uhr Zeit?", any=["nein", "planung", "belegt"]),
  say("Verschiebe den Termin Planung auf 16 Uhr."), say(YES),
  say("Benenne den Termin Planung in Projektplanung um."), say(YES),
  say("Was steht morgen in meinem Kalender?", all=["projektplanung", "16"]),
  say("Lösche den Termin Projektplanung."), say(YES, any=["gelöscht", "entfernt"]),
  say("Welche Termine habe ich morgen?", none=["projektplanung"]))
S("cal-ambiguous-calendar", "Kalender", "Mehrere Kalender → Rückfrage",
  say("Trag morgen um 8 Uhr Joggen ein."), say("Eine Stunde.", any=["welche", "kalender", "familie", "müllabfuhr"]))

# ================================================ 8. Listen
S("todo-multi", "Listen", "Mehrere Einträge, abhaken, entfernen, verschieben",
  say("Füge Milch, Brot, Butter und Äpfel zur Einkaufsliste hinzu.", any=["milch"]),
  say("Was steht auf meiner Einkaufsliste?", type="query_answer", all=["milch", "brot", "butter", "äpfel"]),
  say("Markiere Milch und Brot als erledigt."),
  say("Lösche alle erledigten Einträge."), say(YES),
  say("Was steht auf der Einkaufsliste?", type="query_answer", all=["butter"], none=["milch"]),
  say("Verschiebe Butter von der Einkaufsliste auf die Arbeitsliste."),
  say("Was steht auf der Arbeitsliste?", type="query_answer", any=["butter"]),
  say("Entferne Äpfel von der Einkaufsliste."),
  say("Was steht auf der Einkaufsliste?", type="query_answer", none=["äpfel"]))
S("todo-ambiguous", "Listen", "Ohne Listenname bei zwei Listen",
  say("Setze Druckerpapier auf die Liste.", any=["welche"]),
  say("Arbeitsliste."),
  say("Was steht auf der Arbeitsliste?", type="query_answer", any=["druckerpapier"]))

# ================================================ 9. Timer
S("timer-no-output", "Timer", "Timer ohne Satellit/TTS-Ziel wird abgelehnt (dokumentiert)",
  say("Stelle einen Timer für 5 Minuten mit dem Namen Nudeln.", any=["nicht", "kein"]))

# ================================================ 10. Push / Erinnerungen
S("push-unconfigured", "Push", "Testbenachrichtigung ohne Push-Ziel → Konfigurationshinweis",
  options(agent_notify_targets=[]),
  say("Schick mir eine Testbenachrichtigung.", no_calls=True, notify=False, any=["konfigur", "kein", "ziel"]))
S("push-two-targets", "Push", "Zwei Push-Ziele, keine Zuordnung → Rückfrage statt Broadcast",
  options(agent_notify_targets=["notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"], agent_delivery_channels=["push"]),
  say("Schick mir eine Testbenachrichtigung.", notify=False, any=["zuordn", "welche", "mehrere"]))
S("push-bound", "Push", "Gebundener Benutzer bekommt Testnachricht",
  service("homeintent.bind_user_context", {"user_id": "$admin_user_id", "person_entity_id": "person.philipp", "notification_targets": ["notify.handy_philipp_nachricht"], "preferred_notification_target": "notify.handy_philipp_nachricht", "confirmed": True}),
  say("Schick mir eine Testbenachrichtigung.", any=["gesendet"], notify="test"),
  check(notify="test"))
S("push-delayed", "Push", "In 10 Sekunden Testbenachrichtigung (persistente Einmal-Automation)",
  say("Kannst du mir in 10 Sekunden eine Test Benachrichtigung schicken?"),
  say(YES, settle=1),
  wait(16),
  check(notify="test"))
S("push-window-automation", "Push", "Benachrichtige mich sobald ein Fenster geöffnet wird",
  say("Benachrichtige mich sobald im Wohnzimmer ein Fenster geöffnet wird.", no_calls=True),
  say(YES, settle=3),
  set_("binary_sensor.wohnzimmerfenster", "on", settle=3),
  check(notify="fenster"))
# 7.8.3: Überwachungsaufträge - geprüft wird die Wirkung (Anzahl, Empfänger
# und Text der Push-Nachrichten), nicht nur die Vorschau.
PUSH_BOTH = options(
    agent_notify_targets=["notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"],
    agent_delivery_channels=["push"], allow_non_admin_automations=True,
)
BIND_PHONES = [
    service("homeintent.bind_user_context", {"user_id": "$admin_user_id", "person_entity_id": "person.philipp", "notification_targets": ["notify.handy_philipp_nachricht"], "preferred_notification_target": "notify.handy_philipp_nachricht", "confirmed": True}),
    service("homeintent.bind_user_context", {"user_id": "$anna_user_id", "person_entity_id": "person.anna", "notification_targets": ["notify.handy_anna_nachricht"], "preferred_notification_target": "notify.handy_anna_nachricht", "confirmed": True}),
]
AWAY_MESSAGE = "niemand ist zuhause"


def window_while_away(user: str, phone: str) -> list[dict[str, Any]]:
    return [
        # Frühere Testautomationen (auch aus Wiederholungsläufen) würden
        # dieselbe Nachricht ein zweites Mal senden: Wirkung zählt genau.
        service("haus_sim.reset", {"full": True}),
        say("Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.",
            user=user, no_calls=True, all=["egal was davon zuletzt eintritt", "anna", "philipp"]),
        say(YES, user=user, settle=3, any=["erstellt"]),
        # Ausgangslage: Küchen- und Badfenster sind im Testhaus offen.
        set_("binary_sensor.kuechenfenster", "off"), set_("binary_sensor.badezimmerfenster", "off"),
        # 1. Küchenfenster auf, danach gehen alle: genau eine Nachricht.
        service("haus_sim.clear_log"),
        set_("binary_sensor.kuechenfenster", "on", settle=2),
        check(notify_count=0, notify_to=phone, notify_match=AWAY_MESSAGE),
        set_("device_tracker.handy_philipp", "not_home", settle=2),
        check(notify_count=0, notify_to=phone, notify_match=AWAY_MESSAGE),
        set_("device_tracker.handy_anna", "not_home", settle=3),
        check(notify_count=1, notify_to=phone, notify_match=AWAY_MESSAGE),
        # 2. Alle Fenster zu, alle gehen: keine Nachricht.
        set_("binary_sensor.kuechenfenster", "off"),
        set_("device_tracker.handy_philipp", "home"), set_("device_tracker.handy_anna", "home", settle=2),
        service("haus_sim.clear_log"),
        set_("device_tracker.handy_philipp", "not_home"), set_("device_tracker.handy_anna", "not_home", settle=3),
        check(notify_count=0, notify_to=phone, notify_match=AWAY_MESSAGE),
        # 3. Haus leer, dann Fenster auf: eine Nachricht.
        service("haus_sim.clear_log"),
        set_("binary_sensor.kuechenfenster", "on", settle=3),
        check(notify_count=1, notify_to=phone, notify_match=AWAY_MESSAGE),
        set_("binary_sensor.kuechenfenster", "off"),
        set_("device_tracker.handy_philipp", "home"), set_("device_tracker.handy_anna", "home", settle=2),
    ]


S("mon-window-away", "Push", "7.8.3: Fenster offen und niemand zuhause, beide Reihenfolgen",
  PUSH_BOTH, *BIND_PHONES,
  *window_while_away("admin", "notify.handy_philipp_nachricht"))
S("mon-window-away-anna", "Push", "7.8.3: derselbe Auftrag von Anna geht an Annas Handy",
  PUSH_BOTH, *BIND_PHONES,
  *window_while_away("anna", "notify.handy_anna_nachricht"))
S("mon-routing-niemand", "Push", "7.8.3: „niemand“ und „keiner“ ergeben dieselbe Automation",
  PUSH_BOTH, *BIND_PHONES,
  say("Sag mir Bescheid, wenn das Garagentor offen ist und niemand zuhause ist.",
      no_calls=True, all=["egal was davon zuletzt eintritt"], none=["haushalt"]),
  say(YES, settle=2, any=["erstellt"]))
# 7.9: Überwachungsaufträge vollständig - je Welle die echte Push-Nachricht.
S("m79-w1-all-windows", "Überwachung", "W1: Nachricht erst, wenn alle Fenster zu sind",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Sag mir Bescheid, wenn alle Fenster zu sind.", no_calls=True, all=["alle 6 fenster geschlossen"]),
  say(YES, settle=3, any=["erstellt"]),
  service("haus_sim.clear_log"),
  set_("binary_sensor.badezimmerfenster", "off", settle=2),
  check(notify_count=0, notify_match="alle 6 fenster"),
  set_("binary_sensor.kuechenfenster", "off", settle=3),
  check(notify_count=1, notify_to="notify.handy_philipp_nachricht", notify_match="alle 6 fenster sind geschlossen"))
S("m79-w2-no-motion", "Überwachung", "W2: 30 Sekunden keine Bewegung im Flur",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn sich im Flur 30 Sekunden nichts bewegt.", no_calls=True, all=["startet home assistant neu"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  set_("binary_sensor.bewegung_flur", "on", settle=2),
  set_("binary_sensor.bewegung_flur", "off", settle=1),
  wait(15),
  check(notify_count=0, notify_match="keine bewegung"),
  wait(25),
  check(notify_count=1, notify_to="notify.handy_philipp_nachricht", notify_match="keine bewegung"))
S("m79-w3-rate", "Überwachung", "W3: Temperatur fällt um 3 Grad (läuft in HomeIntent)",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn die Temperatur im Keller innerhalb von 10 Minuten um 3 Grad fällt.",
      no_calls=True, all=["das überwache ich selbst", "solange homeintent läuft"]),
  say(YES, settle=2, any=["eingerichtet"]),
  set_("sensor.temperatur_keller", 18.5, settle=4),
  service("haus_sim.clear_log"),
  set_("sensor.temperatur_keller", 17.0, settle=4),
  check(notify_count=0, notify_match="temperatur keller"),
  set_("sensor.temperatur_keller", 15.0, settle=4),
  check(notify_count=1, notify_to="notify.handy_philipp_nachricht", notify_match="um 3,5 °c gefallen"),
  set_("sensor.temperatur_keller", 14.0, settle=4),
  check(notify_count=1, notify_match="temperatur keller"))
S("m79-w6-two-turns", "Überwachung", "W6: Bezug über zwei Sätze",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Überwache das Garagentor.", no_calls=True, any=["wann soll ich mich"]),
  say("Wenn es offen ist.", no_calls=True, any=["soll ich das so einrichten"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  service("cover.open_cover", {"entity_id": "cover.garagentor"}), wait(10),
  check(notify_count=1, notify_to="notify.handy_philipp_nachricht", notify_match="garagentor"),
  service("cover.close_cover", {"entity_id": "cover.garagentor"}), wait(8))
S("m79-w8-list-stop", "Überwachung", "W8: Überwachungen auflisten und stoppen",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Überwache die Haustür und melde dich, wenn sie geöffnet wird.", no_calls=True),
  say(YES, settle=2, any=["erstellt"]),
  say("Welche Überwachungen laufen?", type="query_answer", all=["haustür"], none=["binary_sensor"]),
  say("Stopp die Haustür-Überwachung.", any=["ausgeschaltet"]),
  service("haus_sim.clear_log"),
  set_("binary_sensor.haustuer", "on", settle=3),
  check(notify_count=0, notify_match="haustür"),
  set_("binary_sensor.haustuer", "off"),
  say("Welche Überwachungen laufen?", type="query_answer", all=["ausgeschaltet"]))
S("reminder-relative", "Push", "Erinnere mich in 20 Sekunden an die Waschmaschine",
  say("Erinnere mich in 20 Sekunden an die Waschmaschine."),
  say(YES), wait(26),
  check(notify="waschmaschine"))
S("reminder-person", "Push", "Sag Anna morgen um 8 Uhr Bescheid (Person ohne Bindung)",
  say("Sag Anna morgen um 8 Uhr Bescheid, dass der Müll raus muss."))

# ================================================ 11. Timer mit TTS
S("timer-full", "Timer", "Benannte Timer mit TTS-Ausgabe",
  options(agent_tts_entity="tts.sprachausgabe", agent_media_players=["media_player.kuechenradio"]),
  say("Stelle einen Timer für 10 Sekunden.", any=["wie soll", "heißen", "name"]),
  say("Pizza.", any=["pizza"]),
  say("Stelle einen Nudeltimer für 5 Minuten.", any=["nudel"]),
  say("Welche Timer laufen?", all=["pizza", "nudel"]),
  wait(12),
  check(played="Timer+Pizza+ist+abgelaufen"),
  say("Wie lange läuft der Timer Nudeln noch?", any=["minute"]),
  say("Pausiere den Timer Nudeln.", any=["pausiert", "angehalten"]),
  say("Setze den Nudeltimer fort.", any=["fortgesetzt", "läuft"]),
  say("Verlängere den Timer Nudeln um fünf Minuten.", any=["verlängert", "10", "zehn"]),
  say("Stelle einen Timer für 3 Minuten mit dem Namen Tee."),
  say("Lösche den Timer.", any=["welchen"]),
  say("Tee.", any=["tee"]),
  say("Lösche alle Timer.", any=["sicher", "soll", "alle"]),
  say(YES),
  say("Welche Timer laufen?", any=["keine", "kein"]))
S("timer-helper", "Timer", "timer.*-Helfer (Waschgang)",
  say("Starte den Timer Waschgang.", any=["waschgang"]),
  check(state={"timer.waschgang": "active"}),
  say("Wie lange läuft der Waschgang noch?", any=["stunde", "minute"]))

# ================================================ 12. Automationen
S("auto-delayed", "Automationen", "In 15 Sekunden Küchenlicht einschalten",
  say("Schalte in 15 Sekunden das Küchenlicht ein.", no_calls=True, any=["soll", "automation", "15"]),
  say(YES, no_calls=True),
  say("Welche einmaligen Aufträge sind noch geplant?", any=["küchenlicht"]),
  wait(20),
  check(state={"light.kuechenlicht": "on"}))
S("auto-state-trigger", "Automationen", "Wenn Küchenfenster geöffnet wird, Küchenlicht an",
  set_("binary_sensor.kuechenfenster", "off"),
  say("Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.", no_calls=True),
  say(YES, settle=3),
  set_("binary_sensor.kuechenfenster", "on", settle=2),
  check(state={"light.kuechenlicht": "on"}))
S("auto-numeric", "Automationen", "Temperatur unter 18 Grad → Heizung Büro 21 Grad",
  say("Wenn die Temperatur im Keller unter 12 Grad fällt, schalte das Kellerlicht ein.", no_calls=True),
  say(YES, settle=3),
  set_("sensor.temperatur_keller", 11.2, settle=2),
  check(state={"light.kellerlicht": "on"}))
S("auto-sun", "Automationen", "Bei Sonnenuntergang Außenbeleuchtung ein",
  # 7.3.3: ohne Wiederholungsmarker fragt HomeIntent „nur heute oder jeden Tag?“
  say("Bei Sonnenuntergang schalte die Außenbeleuchtung ein.", no_calls=True, any=["jeden tag"]),
  say("Jeden Tag.", no_calls=True, any=["erstellt werden"]),
  say(YES, settle=3, any=["erstellt"]))
S("auto-recurring", "Automationen", "Jeden Werktag um 7 Uhr Küchenlicht ein",
  say("Jeden Werktag um 7 Uhr schalte das Küchenlicht ein.", no_calls=True),
  say(YES, settle=3))
S("auto-for", "Automationen", "Fenster zehn Minuten offen → Heizung aus",
  say("Wenn das Schlafzimmerfenster zehn Minuten offen bleibt, schalte die Heizung im Schlafzimmer aus.", no_calls=True),
  say(YES, settle=3))
S("auto-colortemp", "Automationen", "Automation mit Farbtemperatur (kelvin-Feld)",
  say("Jeden Tag um 21 Uhr stelle das Bürolicht auf warmweiß.", no_calls=True),
  say(YES, settle=3, none=["fehler"]))
S("auto-manage", "Automationen", "Automationen verwalten",
  say("Zeige nur HomeIntent-Automationen.", any=["automation"]),
  say("Wie viele HomeIntent-Automationen sind aktiv?", any=["aktiv"]),
  say("Welche Automation steuert die Außenbeleuchtung?", any=["sonnenuntergang", "außenbeleuchtung"]),
  say("Was passiert, wenn die Bewegung im Flur erkannt wird?", any=["flurlicht"]),
  say("Deaktiviere die Automation für Flurlicht bei Bewegung.", any=["soll", "deaktiv"]),
  check(state={"automation.flurlicht_bei_bewegung": "off"}),
  say("Aktiviere die Automation für Flurlicht bei Bewegung.", any=["aktiviert"]),
  check(state={"automation.flurlicht_bei_bewegung": "on"}),
  say("Füge der Automation für Außenbeleuchtung die Bedingung hinzu, dass jemand zuhause ist."), say(YES),
  say("Dupliziere die Automation für Außenbeleuchtung."), say(YES),
  say("Lösche die Automation für Außenbeleuchtung.", any=["welche", "mehrere", "soll"]),
  say("Mache die letzte HomeIntent-Automationsänderung rückgängig."), say(YES))
S("auto-guided", "Automationen", "Geführter Automationsdialog",
  say("Erstelle eine Automation.", any=["auslöser", "wann"]),
  say("Wenn die Haustür geöffnet wird."),
  say("Nein."),
  say("Schalte das Flurlicht ein."),
  say("Immer."),
  say(YES, settle=3),
  set_("binary_sensor.haustuer", "on", settle=2),
  check(state={"light.flurlicht": "on"}))

# ================================================ 13. Mehrbenutzer / Richtlinie
S("mu-confirm-binding", "Sicherheit", "Fremder Benutzer kann offene Bestätigung nicht abschließen",
  say("Entriegle das Haustürschloss.", no_calls=True, conv="shared"),
  say(YES, user="anna", conv="shared", no_calls=True, state={"lock.haustuerschloss": "locked"}))
S("mu-nonadmin-critical", "Sicherheit", "Nicht-Admin: Alarmanlage deaktivieren (kritisch)",
  service("alarm_control_panel.alarm_arm_away", {"entity_id": "alarm_control_panel.alarmanlage"}),
  say("Deaktiviere die Alarmanlage.", user="anna", any=["administrator"]),
  say(YES, user="anna", no_calls=True, state={"alarm_control_panel.alarmanlage": "armed_away"}))
S("mu-nonadmin-automation", "Sicherheit", "Nicht-Admin-Automationen abgeschaltet",
  options(allow_non_admin_automations=False),
  say("Jeden Werktag um 6 Uhr schalte das Kinderzimmerlicht ein.", user="lena"),
  say(YES, user="lena", none=["wurde erstellt"]),
  options(allow_non_admin_automations=True))
S("mu-readonly", "Sicherheit", "Nur-Lesen-Entität",
  options(read_only_entities=["switch.gartenpumpe"]),
  say("Schalte die Gartenpumpe ein.", no_calls=True),
  say("Ist die Gartenpumpe an?", type="query_answer"),
  options(read_only_entities=[]))
S("mu-control-users", "Sicherheit", "Nur bestimmte Benutzer dürfen steuern",
  options(control_user_ids=["$admin_user_id", "$anna_user_id"]),
  say("Mach das Kinderzimmerlicht an.", user="lena", no_calls=True),
  say("Ist das Kinderzimmerlicht an?", user="lena", type="query_answer"),
  say("Mach das Kinderzimmerlicht an.", user="anna", calls=["light.kinderzimmerlicht:turn_on"]),
  options(control_user_ids=[]))
S("mu-admin-only", "Sicherheit", "Nur-Admin-Entität",
  options(admin_only_entities=["lock.haustuerschloss"]),
  say("Verriegle das Haustürschloss.", user="anna", no_calls=True),
  options(admin_only_entities=[]))
S("mu-max-targets", "Sicherheit", "Max. gleichzeitige Ziele = 5",
  options(max_action_targets=5),
  say("Schalte im ganzen Haus alle Lichter ein.", no_calls=True, any=["zu viele", "maximal", "ziele", "höchstens"]),
  options(max_action_targets=20))

# ================================================ 14. Proaktiv (V12)
PROACTIVE = options(
    proactive_context_enabled=True, push_proactive_enabled=True, voice_proactive_enabled=False,
    proactive_entry_open_minutes=1, quiet_hours_enabled=False, attention_budget_enabled=True,
    agent_enabled=True, agent_delivery_channels=["push"],
    agent_notify_targets=["notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"],
    proactive_appliance_entities=["sensor.leistung_waschmaschine", "sensor.waschmaschine_status"],
)
BIND = [
    service("homeintent.bind_user_context", {"user_id": "$admin_user_id", "person_entity_id": "person.philipp", "notification_targets": ["notify.handy_philipp_nachricht"], "preferred_notification_target": "notify.handy_philipp_nachricht", "confirmed": True}),
    service("homeintent.bind_user_context", {"user_id": "$anna_user_id", "person_entity_id": "person.anna", "notification_targets": ["notify.handy_anna_nachricht"], "preferred_notification_target": "notify.handy_anna_nachricht", "confirmed": True}),
    service("homeintent.set_household", {"person_entity_ids": ["person.philipp", "person.anna", "person.lena"], "confirmed": True}),
]
S("pro-garage", "Proaktiv", "Garage bleibt offen → Push-Vorschlag → Ja schließt",
  PROACTIVE, *BIND,
  service("cover.open_cover", {"entity_id": "cover.garagentor"}),
  wait(80),
  check(notify="garage"),
  say("Ja.", settle=8),
  check(state={"cover.garagentor": "closed"}),
  say("Warum hast du mich wegen der Garage angesprochen?", any=["garage", "offen"]),
  say("Welche Hinweise gab es heute?", any=["garage"]))
S("pro-garage-closed-meanwhile", "Proaktiv", "Garage vor Antwort geschlossen → Ja ist wirkungslos",
  service("cover.open_cover", {"entity_id": "cover.garagentor"}),
  wait(80),
  service("cover.close_cover", {"entity_id": "cover.garagentor"}), wait(8),
  say("Ja.", no_calls=True))
S("pro-smoke", "Proaktiv", "Rauchmelder → sofortige kritische Meldung",
  set_("binary_sensor.rauchmelder_flur", "on", settle=4),
  check(notify="rauch"))
S("pro-washer", "Proaktiv", "Waschmaschine fertig",
  set_("sensor.leistung_waschmaschine", 1850, settle=2),
  wait(5),
  set_("sensor.leistung_waschmaschine", 2, settle=5),
  wait(60),
  check(notify="waschmaschine"))
S("pro-washer-status", "Proaktiv", "Waschmaschine fertig (Text-Statussensor)",
  PROACTIVE,
  set_("sensor.waschmaschine_status", "running", settle=2),
  set_("sensor.waschmaschine_status", "finished", settle=5),
  # Runs ~10 s after pro-washer's notice: V12 groups INFO notices within the
  # 2-minute window by design and delivers them as one digest afterwards.
  wait(125),
  check(notify="waschmaschine"))
S("pro-standing", "Proaktiv", "Daueranweisung: niemand zuhause → Licht aus",
  options(standing_permissions_enabled=True),
  service("homeintent.set_household", {"person_entity_ids": ["person.philipp", "person.anna"], "confirmed": True}),
  say("Wenn niemand zuhause ist und im Wohnzimmer noch Licht an ist, darfst du es automatisch ausschalten.", no_calls=True),
  say(YES),
  service("light.turn_on", {"entity_id": "light.wohnzimmer_deckenlicht"}),
  set_("device_tracker.handy_philipp", "not_home"), set_("device_tracker.handy_anna", "not_home", settle=10),
  wait(330),
  check(state={"light.wohnzimmer_deckenlicht": "off"}),
  say("Was darfst du ohne Rückfrage?", any=["wohnzimmer", "licht"]),
  say("Welche Daueranweisungen gibt es?", any=["wohnzimmer", "licht"]))
S("pro-standing-never-auto", "Proaktiv", "Daueranweisung für Garage wird abgelehnt (NEVER_AUTO)",
  say("Wenn niemand zuhause ist und das Garagentor offen ist, darfst du es automatisch schließen.", any=["nichts gespeichert", "nie automatisch"]))
S("m79-w5-repeat", "Proaktiv", "W5: Wiederholung jede Minute, bis das Tor zu ist",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Wenn das Garagentor offen ist, erinnere mich jede Minute, bis es zu ist.", no_calls=True,
      all=["höchstens 12-mal"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  service("cover.open_cover", {"entity_id": "cover.garagentor"}),
  wait(75),
  # Exakt die Erinnerung: V12 meldet das offene Tor (Proaktiv) mit eigener Frage.
  check(notify_count=2, notify_to="notify.handy_philipp_nachricht", notify_exact="Das Garagentor ist noch offen."),
  service("cover.close_cover", {"entity_id": "cover.garagentor"}), wait(10),
  service("haus_sim.clear_log"),
  wait(60),
  check(notify_count=0, notify_exact="Das Garagentor ist noch offen."))
S("m79-w5-escalate", "Proaktiv", "W5: Eskalation an Anna nach 1 Minute",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn die Haustür offen ist, und wenn sie nach 1 Minute immer noch offen ist, sag Anna Bescheid.",
      no_calls=True, all=["handy anna"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  set_("binary_sensor.haustuer", "on", settle=3),
  check(notify_count=1, notify_to="notify.handy_philipp_nachricht"),
  check(notify_count=0, notify_to="notify.handy_anna_nachricht"),
  wait(65),
  check(notify_count=1, notify_to="notify.handy_anna_nachricht", notify_match="seit 1 minute offen"),
  set_("binary_sensor.haustuer", "off"),
  service("haus_sim.clear_log"),
  set_("binary_sensor.haustuer", "on", settle=3),
  set_("binary_sensor.haustuer", "off", settle=1),
  wait(65),
  check(notify_count=0, notify_to="notify.handy_anna_nachricht"))
S("mon-garage-duration", "Proaktiv", "7.8.3: Garagentor länger als 1 Minute offen → genau eine Nachricht",
  PUSH_BOTH, *BIND_PHONES,
  say("Überwache das Garagentor und melde dich, wenn es länger als 1 Minute offen ist.",
      no_calls=True, all=["länger als 1 minute"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  service("cover.open_cover", {"entity_id": "cover.garagentor"}),
  wait(30),
  check(notify_count=0, notify_to="notify.handy_philipp_nachricht", notify_match="seit 1 minute"),
  wait(50),  # 6 s Fahrzeit + 60 s "for" + Reserve
  check(notify_count=1, notify_to="notify.handy_philipp_nachricht", notify_match="seit 1 minute"),
  service("cover.close_cover", {"entity_id": "cover.garagentor"}), wait(8))

# ================================================ 15. Agent: Gedächtnis, Ziele, Routinen
S("agent-smalltalk", "Agent", "Uhrzeit, Datum, Fähigkeiten",
  say("Wie spät ist es?", type="query_answer", any=["uhr"]),
  say("Welches Datum ist heute?", type="query_answer", any=["2026"]),
  say("Was kannst du?", type="query_answer", any=["geräte"]))
S("agent-memory", "Agent", "Präferenz merken, abfragen, vergessen",
  options(memory_enabled=True),
  say("Merk dir, dass ich beim Lesen die Stehlampe auf 40 Prozent möchte.", no_calls=True),
  say(YES),
  say("Was hast du dir über mich gemerkt?", any=["stehlampe", "40"]),
  say("Vergiss meine Vorliebe für die Stehlampe."), say(YES),
  say("Was hast du dir über mich gemerkt?", none=["40 prozent"]))
S("agent-goal-secure", "Agent", "Ziel: Sichere das Haus",
  service("lock.unlock", {"entity_id": "lock.haustuerschloss"}),
  say("Sichere das Haus.", any=["fenster", "schloss", "haustür", "soll"]),
  say(YES, settle=3))
S("agent-goal-media", "Agent", "Ziel: Pausiere alle Medien",
  service("media_player.media_play", {"entity_id": "media_player.lautsprecher_schlafzimmer"}),
  say("Pausiere alle Medien.", any=["plan"]), say(YES, calls=["media_player.kuechenradio:media_pause", "media_player.lautsprecher_schlafzimmer:media_pause"]))
S("agent-routine-unknown", "Agent", "Unbekannte Routine → Rückfrage statt Annahme",
  say("Bereite den Filmabend vor.", no_calls=True))
S("agent-routine-saved", "Agent", "Gespeicherte Routine ausführen",
  service("homeintent.save_routine", {"routine_id": "lesezeit", "name": "Lesezeit", "owner_user_id": "$admin_user_id", "steps": [{"entity_id": "light.stehlampe", "property": "brightness", "value": 40, "unit": "%"}, {"entity_id": "light.wohnzimmer_deckenlicht", "property": "state", "value": "off"}], "confirmed": True}),
  say("Bereite die Lesezeit vor.", any=["stehlampe", "plan"]),
  say(YES, calls=["light.stehlampe:turn_on"]))
S("agent-prediction", "Agent", "Vorhersage ohne Modell → ehrliche Antwort",
  say("Wann ist das Büro warm?", no_calls=True),
  say("Wie lange braucht das Wohnzimmer bis 23 Grad?", no_calls=True))
S("agent-why-failed", "Agent", "Warum hat das nicht funktioniert?",
  say("Schalte das Bürolicht ein."),
  say("Warum hat das nicht funktioniert?", no_calls=True))


# ================================================ 16. Gezielte Regressionen (hassil 3.12 / Befunde)
S("reg-leading-in", "Regression", "Vorangestellte Zeit: „In 5 Minuten schalte …“",
  say("In 5 Minuten schalte das Küchenlicht ein.", any=["soll diese automation"], none=["n schalte"]),
  say("Nein."))
S("reg-reminder-abs", "Regression", "Erinnerung mit absoluter Zeit",
  say("Erinnere mich morgen um 18 Uhr an den Elternabend.", any=["elternabend"]),
  say("Nein."))
S("reg-comfort-trap", "Regression", "Komfort-Rückfrage lässt sich verlassen",
  options(memory_enabled=True),
  say("Mach es hier gemütlicher.", device="Wohnzimmer"),
  say("Schalte das Küchenlicht ein.", calls=["light.kuechenlicht:turn_on"]))
S("reg-guided-nobedingung", "Regression", "Geführte Automation: „Keine Bedingung“ als Nein",
  say("Erstelle eine Automation."),
  say("Wenn die Haustür geöffnet wird."),
  say("Keine Bedingung.", none=["ja oder nein"]))
S("reg-automation-byname", "Regression", "Automation per Namen ohne „für“",
  say("Deaktiviere die Automation Flurlicht bei Bewegung.", any=["deaktiv", "soll"]))
S("reg-kelvin", "Regression", "Farbtemperatur warmweiß",
  say("Stelle das Bürolicht auf warmweiß.", calls=["light.buerolicht:turn_on"], none=["fehler"]))
S("reg-notify-entity-unknown", "Regression", "Nachricht an Notify-Entity im Zustand unknown",
  say("Schicke an Handy Anna die Nachricht Abendessen ist fertig.", notify="Abendessen"))


# ================================================ 17. Sprache 7.3 (eigene Sätze je Fähigkeit)
L73 = "Sprache 7.3"
# Sicherheitsfehler S1-S7
S("s73-s1-maintain", L73, "S1: „lass … an“ ist Beibehaltung, keine Aktion",
  say("Lass die Kücheninsel an.", no_calls=True),
  say("Lass das Licht in der Küche bitte an.", no_calls=True))
S("s73-s2-everything-room", L73, "S2: „alles“ im Raum = schaltbare Alltagsgeräte mit Vorschau",
  say("Mach im Schlafzimmer alles aus.", no_calls=True, any=["soll"], none=["heizung schlafzimmer aus"]),
  say(YES, calls=["light.schlafzimmerlicht:turn_off", "fan.deckenventilator:turn_off"],
      not_calls=["climate.heizung_schlafzimmer:set_hvac_mode", "climate.heizung_schlafzimmer:turn_off"]))
S("s73-s3-three-clauses", L73, "S3: drei Befehle in einem Satz, keiner fällt weg",
  say("Schalte das Küchenlicht ein, mach die Stehlampe an und fahre den Esszimmer Rollladen runter.", settle=4,
      calls=["light.kuechenlicht:turn_on", "light.stehlampe:turn_on", "cover.esszimmer_rollladen:close_cover"]))
S("s73-s4-quieter-not-pause", L73, "S4: leiser heißt Lautstärke senken, nicht pausieren",
  say("Mach das Radio in der Küche leiser.", not_calls=["media_player.kuechenradio:media_pause"], none=["paused"]))
S("s73-s5-indoor-superlative", L73, "S5: Superlativ ohne Außenbezug vergleicht nur Innenräume",
  say("Welcher Raum ist am wärmsten?", no_calls=True, none=["garten", "außen"]))
S("s73-s6-too-bright", L73, "S6: „zu hell“ wirkt statt Werte vorzulesen",
  service("light.turn_on", {"entity_id": "light.stehlampe", "brightness_pct": 80}),
  # 7.3.3: Bedürfnisse werden im Standard „propose“ vorgeschlagen, „Ja“ führt aus.
  say("Im Wohnzimmer ist es mir zu hell.", none=["lux"], no_calls=True, any=["soll ich"]),
  say(YES, any=["gedimmt", "dunkler", "heruntergefahren", "geschlossen", "gestellt", "gesenkt"]))
S("s73-s7-detector-kind", L73, "S7: Meldergattung bleibt erhalten (Rauch ≠ Bewegung)",
  say("Benachrichtige mich, wenn der Rauchmelder auslöst.", any=["rauchmelder"], none=["bewegungsmelder"]),
  say("Nein."))
# Restlücken R1-R9
S("s73-r1-cooler", L73, "R1: „ein wenig kühler“ senkt den Sollwert",
  say("Mach es im Büro ein wenig kühler.", calls=["climate.heizung_buero:set_temperature"]))
S("s73-r2-cooler-no-device", L73, "R2: Ort + Komparativ ohne Gerät",
  say("Im Schlafzimmer bitte etwas kühler.", calls=["climate.heizung_schlafzimmer:set_temperature"]))
S("s73-r3-vacuum-option", L73, "R3: Saugstufe ohne das Wort „Saugstufe“",
  say("Stell den Saugroboter auf maximal.", calls=["vacuum.saugroboter:set_fan_speed"]))
S("s73-r4-tv-genus", L73, "R4: Fernseher/TV/Glotze als Gattung",
  service("media_player.turn_on", {"entity_id": "media_player.wohnzimmer_tv"}),
  say("Schalte die Glotze aus.", calls=["media_player.wohnzimmer_tv:turn_off"]))
S("s73-r5-white-tone", L73, "R5: kaltweiß/neutralweiß",
  say("Mach das Deckenlicht im Wohnzimmer kaltweiß.", calls=["light.wohnzimmer_deckenlicht:turn_on"]),
  say("Stell die Pendelleuchte im Esszimmer auf neutral weiß.", calls=["light.esszimmer_pendelleuchte:turn_on"]))
S("s73-r6-timer-count", L73, "R6: Anzahl laufender Timer",
  say("Wie viele Timer sind gerade aktiv?", no_calls=True, none=["nicht verstanden"]))
S("s73-r7-unknown-exclusion", L73, "R7: unbekannter Name in der Ausnahme wird korrekt genannt",
  say("Schalte im Büro alle Lampen aus, bis auf die Zimmerpalme.", no_calls=True, any=["zimmerpalme"]))
S("s73-r8-threshold-query", L73, "R8: Vergleich mit Schwelle",
  service("light.turn_on", {"entity_id": "light.stehlampe", "brightness_pct": 60}),
  say("Welche Lampen sind heller als 30 Prozent?", type="query_answer", no_calls=True, any=["stehlampe"]))
S("s73-r9-calendar-trigger", L73, "R9: Kalendertermin als Auslöser",
  say("Wenn der Termin Zahnarzt anfängt, schalte das Flurlicht ein.", any=["zahnarzt"], no_calls=True),
  say("Nein."))
# §1 Kompositionelle Zielauflösung
S("s73-1-singular-asks", L73, "§1: Einzahl bei mehreren Geräten der Gattung → Rückfrage",
  say("Mach die Leuchte im Kinderzimmer an.", no_calls=True, any=["welche", "meinst"]))
S("s73-1-plural-floor", L73, "§1: Plural + Etage = alle der Gattung dort",
  say("Fahre alle Jalousien im Obergeschoss hoch.", settle=4,
      calls=["cover.schlafzimmer_rollladen:open_cover", "cover.kinderzimmer_rollladen:open_cover",
             "cover.badezimmer_rollladen:open_cover"], not_calls=["cover.garagentor:open_cover"]))
S("s73-1-missing-genus", L73, "§1: fehlende Gattung am Ort wird ehrlich benannt",
  say("Mach im Büro den Ventilator an.", no_calls=True, all=["büro", "ventilator"]))
S("s73-1-three-lamps", L73, "§1: Zahl + Gattung + Raum",
  say("Schalte die drei Lampen im Büro ein.",
      calls=["light.buerolicht:turn_on", "light.schreibtischlampe:turn_on", "light.deckenfluter_buero:turn_on"]))
# §2 Bedürfnisse
S("s73-2-freezing", L73, "§2: „Ich friere“ am Satelliten",
  say("Ich friere.", device="Kinderzimmer", no_calls=True, any=["soll ich"]),
  say(YES, device="Kinderzimmer", calls=["climate.heizung_kinderzimmer:set_temperature"]))
S("s73-2-stale-air", L73, "§2: muffige Luft → Lüfter des Raums",
  say("Hier ist es muffig.", device="Badezimmer", no_calls=True, any=["soll ich"]),
  say(YES, device="Badezimmer", calls=["fan.badluefter:turn_on"]))
# §3 Situationssichten
S("s73-3-still-on", L73, "§3: noch an je Etage",
  say("Ist im Erdgeschoss noch etwas an?", type="query_answer", no_calls=True, any=["küchenradio"]))
S("s73-3-ventilate", L73, "§3: Lüften nach dokumentierten Schwellen",
  say("Sollte ich lüften?", no_calls=True, any=["badezimmer"]))
S("s73-3-rooms-floor", L73, "§3: Räume je Etage",
  say("Welche Räume gibt es im Keller?", no_calls=True, all=["hauswirtschaftsraum", "kellerraum"]))
S("s73-3-secure", L73, "§3: abgeschlossen/zu nennt offene Fenster",
  say("Ist alles abgeschlossen?", no_calls=True, any=["küchenfenster"]))
S("s73-3-script", L73, "§3: was macht ein Skript (aus der HA-Konfiguration)",
  say("Was macht das Skript Kaffee kochen?", no_calls=True, any=["kaffeemaschine"]))
S("s73-3-presence", L73, "§3: Präsenz je Raum",
  say("Ist jemand im Büro?", no_calls=True, any=["nein", "niemand"]))
# §4 Diskurs
S("s73-4-other-one", L73, "§4: „die andere“ = Partnergerät",
  say("Schalte die Nachttischlampe rechts ein.", calls=["light.nachttischlampe_rechts:turn_on"]),
  say("Die andere bitte auch.", calls=["light.nachttischlampe_links:turn_on"], only_calls=True))
S("s73-4-deictic-place", L73, "§4: „dort“ = letzter Ort",
  say("Wie warm ist es im Kinderzimmer?", no_calls=True),
  say("Dort bitte wärmer.", calls=["climate.heizung_kinderzimmer:set_temperature"]))
# §5 Modalität
S("s73-5-release", L73, "§5: „kann jetzt aus“ = ausschalten",
  service("switch.turn_on", {"entity_id": "switch.kaffeemaschine"}),
  say("Die Kaffeemaschine kann jetzt aus.", calls=["switch.kaffeemaschine:turn_off"], only_calls=True))
S("s73-5-embedded-question", L73, "§5: eingebettete Frage bleibt Frage",
  say("Ich wüsste gern, ob die Haustür zu ist.", no_calls=True, any=["haustür"]))
# §6 Benachrichtigungen
S("s73-6-tell-anna", L73, "§6: „Schreib Anna, dass …“ geht an Annas Handy",
  say("Schreib Anna, dass das Essen fertig ist.", notify="Essen"))
# §7 Zeitsprache
S("s73-7-quarter-to", L73, "§7: „viertel vor neun“ wird eine Zeitautomation, nie sofort",
  say("Schalte um viertel vor neun die Esszimmer Pendelleuchte aus.", no_calls=True, any=["08:45"]),
  say("Nein."))
S("s73-7-delay-plural", L73, "§7: Verzögerung in beliebiger Wortstellung, Plural mit Vorschau",
  say("Schließe in einer halben Stunde die Rollläden im Erdgeschoss.", no_calls=True, any=["30 minuten"], none=["garagentor", "markise"]),
  say("Nein."))
S("s73-7-camera", L73, "Einfahrtkamera auf dem Fernseher (README-Gegenstück)",
  service("media_player.turn_on", {"entity_id": "media_player.wohnzimmer_tv"}),
  say("Zeig die Einfahrtkamera auf dem Fernseher im Wohnzimmer.", calls=["media_player.wohnzimmer_tv:play_media"]))

# ========================================================= 7.7.1 Sicherheit (unabhängiger Test 7.7)
L771 = "Sicherheit 7.7.1"
S("s771-a1-correction", L771, "A1: Selbstkorrektur ohne Negationswort – nur der Ersatz",
  say("Schalte das Küchenradio aus, ich meine den Fernseher im Wohnzimmer.",
      not_calls=["media_player.kuechenradio:turn_off"]),
  say("Licht im Kinderzimmer an, halt, im Schlafzimmer.",
      not_calls=["light.kinderzimmerlicht:turn_on", "light.nachtlicht:turn_on"]),
  say("Mach die Stehlampe an, nein, doch nicht.", no_calls=True))
S("s771-a2-irrealis", L771, "A2: Irrealis, Abwägung, Beibehaltung schreiben nie",
  say("Hätte ich doch die Heizung im Büro ausgeschaltet.", no_calls=True),
  say("Ich überlege, ob ich den Mähroboter starten soll.", no_calls=True),
  say("Den Fernseher lass bitte aus.", no_calls=True, any=["lasse"]))
S("s771-a3-ellipsis", L771, "A3: Ellipse mit neuem Objekt, Seite, Zeit, Ort",
  say("Fahr den linken Rollladen im Wohnzimmer hoch.", settle=4, calls=["cover.wohnzimmer_rollladen_links:open_cover"]),
  say("Den rechten runter.", settle=4, calls=["cover.wohnzimmer_rollladen_rechts:close_cover"], only_calls=True),
  say("Mach das Licht im Flur aus.", calls=["light.flurlicht:turn_off"]),
  say("Morgen früh wieder an.", no_calls=True),
  say("Nein."),
  say("Mach das Licht im Flur an.", calls=["light.flurlicht:turn_on"]),
  say("Oben auch.", calls=["light.flurlicht_oben:turn_on"], only_calls=True))
S("s771-a4-coordination", L771, "A4: Ergänzungsstrich, gemeinsamer Kopf, Ort für alle Teile",
  say("Schalte Garten- und Terrassenlicht ein.", calls=["light.terrassenlicht:turn_on"]),
  say("Fahr Küche und Esszimmer Rollladen runter.", settle=4,
      calls=["cover.kuechenrollladen:close_cover", "cover.esszimmer_rollladen:close_cover"]),
  say("Mach im Wohnzimmer das Licht aus und fahr die Rollläden runter.", settle=4,
      not_calls=["cover.schlafzimmer_rollladen:close_cover", "cover.kinderzimmer_rollladen:close_cover"]))
S("s771-a5-satellite", L771, "A5: Satellitenraum ohne passendes Gerät – nur Angebot",
  say("Schalte den Fernseher ein.", device="Badezimmer", no_calls=True, any=["badezimmer"]))
S("s771-a6-informed", L771, "A6: Rückfrage nennt die kritische Wirkung des Skripts",
  say("Starte das Skript Gute Nacht.", no_calls=True, any=["haustürschloss"]),
  say("Nein.", no_calls=True))

# ========================================================= 7.8 Sprachverständnis
L78 = "Sprache 7.8"
S("s78-b2-frames", L78, "B2: Höflichkeit, Dank und Begründung sind Rahmen",
  say("Sei so lieb und schalte das Bürolicht ein.", calls=["light.buerolicht:turn_on"], only_calls=True),
  say("Mach das Kellerlicht an, danke.", calls=["light.kellerlicht:turn_on"], only_calls=True),
  say("Mach das Kellerlicht aus, wir essen gleich.", calls=["light.kellerlicht:turn_off"], only_calls=True))
S("s78-b3-short", L78, "B3: Kurzbefehle und Werte ohne Einheit",
  say("Markise raus.", settle=4, calls=["cover.markise:open_cover"], only_calls=True),
  say("Heizung Schlafzimmer 18 Grad.", calls=["climate.heizung_schlafzimmer:set_temperature"], only_calls=True),
  say("Stell die Heizung im Bad auf 23.", calls=["climate.heizung_badezimmer:set_temperature"], only_calls=True))
S("s78-b4-ellipsis", L78, "B4: Ellipse übernimmt die Operation",
  say("Stell die Heizung im Büro auf 20 Grad.", calls=["climate.heizung_buero:set_temperature"]),
  say("Und in der Küche auf 18.", calls=["climate.heizung_kueche:set_temperature"], only_calls=True))
S("s78-b5-presence", L78, "B5: Präsenz als Auslöser, verblose Aktion",
  say("Wenn im Wohnzimmer jemand ist, mach die Stehlampe an.", no_calls=True, any=["automation"]),
  say("Nein."),
  say("Jeden Morgen um sieben die Kaffeemaschine an.", no_calls=True, any=["07:00"]),
  say("Nein."))
S("s78-b6-garage", L78, "B6: „Garage“ meint das Garagentor, mit Bestätigung",
  say("Öffne die Garage.", no_calls=True, any=["garagentor"]),
  say("Nein.", no_calls=True))
S("s78-b7-dialog", L78, "B7: neue Frage beendet die offene Bestätigung ausdrücklich",
  say("Öffne das Garagentor.", no_calls=True),
  say("Wie warm ist es im Büro?", no_calls=True, any=["verworfen"]),
  say("Ja.", no_calls=True))

L781 = "Sicherheit 7.8.1"
S("s781-ellipsis-unknown", L781, "Ellipse mit unbekanntem Objekt schaltet nie das vorherige Gerät",
  say("Mach das Flurlicht an.", calls=["light.flurlicht:turn_on"]),
  say("Und Blumenkohl aus.", no_calls=True, any=["finde ich nicht"]),
  say("Mach das Flurlicht an.", calls=["light.flurlicht:turn_on"]),
  say("Oben auch.", calls=["light.flurlicht_oben:turn_on"], only_calls=True))

# ========================================================= 7.9.1 Nachtest-Befunde und Bestätigungston
L791 = "Nachtest 7.9.1"
S("n791-a1-access", L791, "A1: Garagentor öffnet sich nie automatisch, nur Benachrichtigung",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Wenn alle weg sind, öffne das Garagentor.", no_calls=True,
      all=["öffne ich nicht automatisch", "stattdessen"], none=["rollladen"]),
  say(YES, settle=2, any=["erstellt"]),
  say("Wenn Philipp das Haus verlässt, schließe das Garagentor.", no_calls=True, all=["garagentor"], none=["rollladen"]),
  say("Nein."))
S("n791-a2-owner", L791, "A2: Anna darf Philipps Überwachung nicht pausieren oder löschen",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  options(allow_non_admin_automations=False),
  say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.", no_calls=True),
  say(YES, settle=2, any=["erstellt"]),
  say("Welche Überwachungen laufen?", user="anna", any=["keine überwachung"]),
  say("Pausiere die Garagen-Meldung bis morgen um 7 Uhr.", user="anna", all=["philipp", "administrator"]),
  say("Lösche die Automation für das Garagentor.", user="anna", all=["philipp"]),
  say("Welche Überwachungen laufen?", type="query_answer", all=["garagentor"], none=["ausgeschaltet"]),
  options(allow_non_admin_automations=True))
S("n791-a3-geht", L791, "A3: „über 24 Grad geht“ ist ein Grenzwert",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Ping mich an, wenn die Temperatur im Schlafzimmer über 24 Grad geht.", no_calls=True,
      all=["über 24 grad"], none=["verlässt"]),
  say("Nein."))
S("n791-a4-floor", L791, "A4: „im Keller“ überwacht nie den Flur-Melder",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn sich im Keller fünf Stunden nichts bewegt.", no_calls=True,
      all=["im keller gibt es keinen"], none=["flur"]),
  say("im Flur", no_calls=True, all=["flur", "soll ich das so einrichten"]),
  say("Nein."))
S("n791-a5-delete", L791, "A5: Überwachung löschen geht nie an den Kalender",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.", no_calls=True),
  say(YES, settle=2, any=["erstellt"]),
  say("Lösch die Überwachung vom Garagentor.", none=["termin", "kalender"], any=["soll ich die überwachung"]),
  say(YES, any=["gelöscht"]),
  # Andere Szenarien hinterlassen HomeIntent-Überwachungen (Goal-Store): nur
  # die gelöschte darf fehlen.
  say("Welche Überwachungen laufen?", none=["garagentor"]))
S("n791-a6-period", L791, "A6: Antwort auf „In welchem Zeitraum?“",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn die Temperatur im Büro um 2 Grad fällt.", no_calls=True, any=["in welchem zeitraum"]),
  say("Innerhalb von 10 Minuten.", no_calls=True, all=["das überwache ich selbst"]),
  say(YES, settle=2, any=["eingerichtet"]))

TONE = options(response_style="tone")
SPOKEN = options(response_style="spoken")
S("n791-b-success", L791, "B: Erfolg am Satelliten – genau ein Ton, keine Sprache",
  TONE,
  say("Schalte das Flurlicht ein.", satellite=True, settle=3, calls=["light.flurlicht:turn_on"],
      speech_empty=True, announce_count=1, announce_match="confirm.mp3"),
  say("Aktiviere die Szene Filmabend.", satellite=True, settle=3, speech_empty=True, announce_count=1),
  SPOKEN)
S("n791-b-question", L791, "B: Rückfrage und Sicherheitsfrage werden gesprochen, kein Ton",
  TONE,
  say("Schalte das Licht ein.", satellite=True, settle=2, speech_empty=False, announce_count=0, any=["welches"]),
  say("Abbrechen.", satellite=True),
  say("Öffne das Garagentor.", satellite=True, settle=2, speech_empty=False, announce_count=0, no_calls=True),
  say("Nein.", satellite=True, settle=2, speech_empty=False, announce_count=0),
  SPOKEN)
S("n791-b-error", L791, "B: Fehler und Abfrage werden gesprochen",
  TONE,
  say("Schalte den Fernseher im Keller ein.", satellite=True, settle=2, speech_empty=False, announce_count=0),
  say("Wie warm ist es im Büro?", satellite=True, settle=2, type="query_answer", speech_empty=False, announce_count=0),
  SPOKEN)
# 7.9.2 A1 (Eigentümerentscheidung): Ein Rollladen, der in die verlangte
# Richtung fährt, ist ein Erfolg mit Ton (siehe n792-a1-cover-moving). Der
# Teilerfolg bleibt hier ein echter: eine Lampe ist nicht erreichbar.
S("n791-b-partial", L791, "B: Teilerfolg (ein Gerät nicht erreichbar) wird gesprochen",
  TONE,
  service("haus_sim.configure", {"target": "light.stehlampe", "unavailable": True}),
  say("Schalte die Stehlampe und das Flurlicht ein.", satellite=True, settle=3,
      calls=["light.flurlicht:turn_on"], speech_empty=False, announce_count=0, any=["stehlampe"]),
  SPOKEN)
S("n791-b-text", L791, "B: Text-Chat ohne Gerät bekommt „Erledigt.“",
  TONE,
  say("Schalte das Flurlicht aus.", settle=2, calls=["light.flurlicht:turn_off"], all=["erledigt"], announce_count=0),
  SPOKEN)
S("n791-b-pipeline", L791, "B: echte Assist-Pipeline – bei Erfolg kein TTS, ein Ton; bei Frage TTS",
  TONE,
  pipeline("Schalte das Flurlicht ein.", settle=3, tts=False, announce_count=1, speech_empty=True),
  pipeline("Schalte das Licht ein.", settle=2, tts=True, announce_count=0, speech_empty=False),
  SPOKEN,
  pipeline("Schalte das Flurlicht aus.", settle=2, tts=True, announce_count=0))

# ========================================================= 7.9.2 Nachtest-Befunde und neue Fähigkeiten
L792 = "Nachtest 7.9.2"
S("n792-a1-delay-short", L792, "A1: Gerät meldet nach 0,5 s – Ton, keine Sprache",
  TONE,
  service("haus_sim.configure", {"target": "light.flurlicht", "report_delay": 0.5}),
  say("Schalte das Flurlicht ein.", satellite=True, settle=3, calls=["light.flurlicht:turn_on"],
      speech_empty=True, announce_count=1, state={"light.flurlicht": "on"}),
  SPOKEN)
S("n792-a1-delay-long", L792, "A1: Gerät meldet erst nach 3 s – ehrliche Sprache, kein Ton",
  TONE,
  service("haus_sim.configure", {"target": "light.flurlicht", "report_delay": 3}),
  say("Schalte das Flurlicht ein.", satellite=True, settle=4, calls=["light.flurlicht:turn_on"],
      speech_empty=False, announce_count=0, all=["noch nicht zurückgemeldet"]),
  SPOKEN)
S("n792-a1-cover-moving", L792, "A1: Rollladen fährt in die verlangte Richtung – Ton",
  TONE,
  say("Fahre den Küchenrollladen runter.", satellite=True, settle=2, calls=["cover.kuechenrollladen:close_cover"],
      speech_empty=True, announce_count=1),
  wait(4), check(state={"cover.kuechenrollladen": "closed"}),
  SPOKEN)
S("n792-a1-cover-reverse", L792, "A1: Rollladen fährt in die Gegenrichtung – Sprache",
  TONE,
  say("Fahre den Küchenrollladen auf 50 Prozent.", settle=3),
  service("haus_sim.configure", {"target": "cover.kuechenrollladen", "reverse": True}),
  say("Fahre den Küchenrollladen runter.", satellite=True, settle=2,
      speech_empty=False, announce_count=0, all=["gegenrichtung"]),
  SPOKEN)
S("n792-a2-irrigation", L792, "A2: Bewässerung öffnet automatisch und schließt nach 20 Minuten",
  service("haus_sim.reset", {"full": True}),
  say("Jeden Morgen um 6 Uhr bewässere den Garten 20 Minuten.", no_calls=True,
      all=["bewässerung garten öffnen und nach 20 minuten wieder schließen", "öffnet sich dabei automatisch"]),
  say(YES, settle=2, any=["erstellt"]),
  say("Jeden Abend um 20 Uhr öffne die Bewässerung.", no_calls=True, all=["wie lange"]),
  say("15 Minuten", no_calls=True, all=["wieder schließen"]),
  say("Nein."))
S("n792-a2-main-valve", L792, "A2: Hauptwasserventil öffnet sich nie automatisch",
  service("haus_sim.reset", {"full": True}),
  say("Jeden Morgen um 6 Uhr öffne das Hauptwasserventil.", no_calls=True,
      all=["öffne ich nicht automatisch", "hauptventile"], none=["erstellt"]),
  say("Nein."),
  say("Wenn alle weg sind, öffne das Hauptwasserventil.", no_calls=True, all=["öffne ich nicht automatisch"]),
  say("Nein."))
S("n792-a3-household", L792, "A3: Haushalts-Satellit verwaltet nur gemeinsame Überwachungen",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  options(household_voice_devices=True),
  service("homeintent.set_household", {"person_entity_ids": ["person.philipp", "person.anna"], "confirmed": True}),
  say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.", no_calls=True),
  say(YES, settle=2, any=["erstellt"]),
  say("Stopp die Garagen-Meldung.", household=True, no_calls=True, any=["gemeinsam", "administrator"]),
  say("Mach die Garagen-Meldung für alle.", all=["gilt jetzt für den ganzen haushalt"]),
  say("Welche Überwachungen laufen?", household=True, all=["garagentor", "(gemeinsam)"]),
  say("Pausiere die Garagen-Meldung bis morgen um 7 Uhr.", household=True, none=["administrator"]),
  options(household_voice_devices=False))
S("n792-b3-battery-push", L792, "B3: Batterie-Meldung nennt das Gerät",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn eine Batterie unter 20 Prozent fällt.", no_calls=True, all=["batterien"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  set_("sensor.batterie_fenster_kueche", 15, settle=3),
  check(notify_count=1, notify_match="Batterie Fenstersensor Küche"),
  say("Welche Batterien sind schwach?", all=["rauchmelder oben", "fenstersensor küche"]))
S("n792-b3-unreachable", L792, "B3: „nicht erreichbar“ erst nach der Mindestdauer",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Sag mir Bescheid, wenn der Bewegungsmelder im Flur länger als 1 Minute nicht erreichbar ist.",
      no_calls=True, all=["bewegungsmelder flur", "nicht erreichbar"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  service("haus_sim.configure", {"target": "binary_sensor.bewegung_flur", "unavailable": True}),
  wait(30), check(notify_count=0),
  wait(40), check(notify_count=1, notify_match="nicht erreichbar"),
  say("Welche Geräte sind nicht erreichbar?", all=["bewegungsmelder flur"]))
S("n792-b4-vacation", L792, "B4: Urlaubsmodus an und vollständig zurückgenommen",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  service("input_boolean.turn_off", {"entity_id": "input_boolean.urlaubsmodus"}),
  say("Ich bin bis Sonntag weg.", no_calls=True, all=["urlaubsmodus", "unberührt"]),
  say(YES, settle=3, state={"input_boolean.urlaubsmodus": "on"}),
  say("Was macht der Urlaubsmodus gerade?", none=["ist aus"]),
  say("Urlaub vorbei.", all=["soll ich?"]),
  say(YES, settle=3, state={"input_boolean.urlaubsmodus": "off"}),
  say("Was macht der Urlaubsmodus gerade?", all=["der urlaubsmodus ist aus"]))
S("n792-b1-summary", L792, "B1: Zusammenfassung nach simulierter Abwesenheit",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  set_("device_tracker.handy_philipp", "not_home", settle=3),
  set_("binary_sensor.haustuer", "on", settle=2), set_("binary_sensor.haustuer", "off", settle=2),
  set_("binary_sensor.bewegung_flur", "on", settle=2), set_("binary_sensor.bewegung_flur", "off", settle=2),
  set_("device_tracker.handy_philipp", "home", settle=4),
  say("Was war los, während ich weg war?", all=["haustür wurde geöffnet", "bewegungsmelder flur"],
      none=["es läuft"]))
S("n792-b5-energy", L792, "B5: Tagesverbrauch aus dem simulierten Zähler",
  service("haus_sim.reset", {"full": True}),
  set_("sensor.energiezaehler", 18234.7, settle=2),
  set_("sensor.energiezaehler", 18236.2, settle=4),
  say("Wie viel Energie haben wir heute verbraucht?", all=["kwh verbraucht (energiezähler)"]),
  say("Wie viel verbraucht die Waschmaschine gerade?", all=["1840"]))
S("n792-a4-floor-note", L792, "A4: Etage mit nur einem Melder wird ehrlich benannt",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn sich im Obergeschoss zwei Stunden nichts bewegt.", no_calls=True,
      all=["im obergeschoss gibt es nur im schlafzimmer einen melder", "kann ich nicht beobachten"]),
  say("Nein."))
S("n792-a5-exact-name", L792, "A5: „Stromverbrauch“ wählt den einzigen passenden Registry-Namen",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Wenn der Stromverbrauch über 3000 Watt geht, warn mich.", no_calls=True,
      all=["stromverbrauch haus über 3000 w"], none=["welchen stromverbrauch meinst du"]),
  say(YES, settle=2, any=["eingerichtet", "erstellt"]),
  service("haus_sim.clear_log"),
  set_("sensor.stromverbrauch_haus", 3500, settle=3),
  check(notify_count=1, notify_match="Stromverbrauch Haus"))
S("n792-a6-awning", L792, "A6: Markise – Sonne fragt nach dem Schwellwert, Lux wird verstanden",
  service("haus_sim.reset", {"full": True}),
  say("Wenn die Sonne scheint, fahre die Markise aus.", no_calls=True, all=["ab welcher helligkeit", "helligkeit außen"]),
  say("ab 30000 Lux", no_calls=True, all=["über 30000 lx", "markise"]),
  say(YES, settle=2, any=["erstellt"]),
  set_("sensor.helligkeit_aussen", 42000, settle=4),
  check(calls=["cover.markise:open_cover"]),
  say("Wenn es draußen heller als 30000 Lux ist, öffne die Markise.", no_calls=True, all=["über 30000 lx"]),
  say("Nein."))
S("n792-a6-gaps", L792, "A6: Wiederholung, schneller Abfall und Licht beim Gehen",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Erinnere mich jede Minute, bis die Markise eingefahren ist.", no_calls=True, all=["nur jetzt oder jedes mal"]),
  say("Abbrechen."),
  say("Sag mir Bescheid, wenn die Außentemperatur schnell fällt.", no_calls=True, all=["um wie viel", "zeitraum"]),
  say("Abbrechen."),
  say("Wenn ich gehe und noch Licht an ist, sag mir Bescheid.", no_calls=True, all=["noch licht an"]),
  say(YES, settle=2, any=["erstellt", "eingerichtet"]),
  service("light.turn_on", {"entity_id": "light.stehlampe"}),
  service("haus_sim.clear_log"),
  set_("device_tracker.handy_philipp", "not_home", settle=3),
  check(notify_count=1, notify_match="wohnzimmer"))
S("n792-b2-habits", L792, "B2: Gewohnheiten – ehrliche Antwort ohne Verlauf, Abschalten",
  service("haus_sim.reset", {"full": True}),
  say("Welche Gewohnheiten hast du erkannt?", none=["nicht gefunden"]),
  say("Hast du Vorschläge für Automationen?", all=["keinen neuen vorschlag"]),
  say("Schlag mir nichts mehr vor.", all=["keine automationen mehr vor"]))


# ========================================================= Nachtest 7.9.3
L793 = "Nachtest 7.9.3"


def trigger(alias_part: str, settle: float = 3.0) -> dict[str, Any]:
    """Eine von HomeIntent angelegte Automation jetzt auslösen (echte Bedingungen)."""
    return {"trigger": alias_part, "settle": settle}


WEATHER_RESET = set_("weather.zuhause", {"reset_forecast": True, "condition": "partlycloudy"}, settle=1)

S("n793-a1-battery-already-low", L793, "A1: Batterie schon darunter – nach „Ja“ sofort eine Push-Nachricht",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn eine Batterie unter 20 Prozent fällt.", no_calls=True,
      all=["rauchmelder oben", "schon darunter"]),
  service("haus_sim.clear_log"),
  say(YES, settle=3, any=["erstellt"], notify_count=1, notify_match="Schon beim Einrichten der Überwachung erfüllt"),
  say("Welche Automationen hast du angelegt?", any=["batterie"]))
S("n793-a1-only-create", L793, "A1: „Nur einrichten“ – keine Sofortmeldung",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn eine Batterie unter 20 Prozent fällt.", no_calls=True, all=["schon darunter"]),
  service("haus_sim.clear_log"),
  say("Nur einrichten.", settle=3, any=["erstellt"], notify_count=0))
S("n793-a2-heating-vacation", L793, "A2: „Heizprogramm auf Urlaub“ stellt den Select um",
  service("haus_sim.reset", {"full": True}),
  service("input_boolean.turn_off", {"entity_id": "input_boolean.urlaubsmodus"}),
  say("Stell das Heizprogramm auf Urlaub.", settle=2, calls=["select.heizprogramm:select_option"],
      state={"select.heizprogramm": "Urlaub", "input_boolean.urlaubsmodus": "off"}, none=["urlaubsmodus"]),
  say("Stell das Heizprogramm auf Komfort.", settle=2, state={"select.heizprogramm": "Komfort"}))
S("n793-a3-back-home", L793, "A3: „Wir sind wieder da“ beendet den Urlaub, danach keine Urlaubsmeldung",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  service("input_boolean.turn_off", {"entity_id": "input_boolean.urlaubsmodus"}),
  say("Wir sind wieder da.", no_calls=True, all=["urlaubsmodus ist nicht aktiv"], none=["soll ich"]),
  say("Ich bin bis Sonntag weg.", no_calls=True, all=["urlaubsmodus"]),
  say(YES, settle=3, state={"input_boolean.urlaubsmodus": "on"}),
  say("Wir sind wieder da.", no_calls=True, all=["willkommen zurück", "soll ich"]),
  service("haus_sim.clear_log"),
  say(YES, settle=3, state={"input_boolean.urlaubsmodus": "off"}),
  wait(3), check(notify_count=0, notify_match="urlaub"),
  say("Was macht der Urlaubsmodus gerade?", all=["der urlaubsmodus ist aus"]))
S("n793-a4-summary-date", L793, "A4: Zusammenfassung nennt den Tag und keine eigenen Wege doppelt",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  set_("device_tracker.handy_philipp", "not_home", settle=3),
  set_("binary_sensor.haustuer", "on", settle=2), set_("binary_sensor.haustuer", "off", settle=2),
  set_("device_tracker.handy_philipp", "home", settle=4),
  say("Was habe ich verpasst?", all=["heute", "haustür"], none=["es läuft"]))
S("n793-a5-real-example", L793, "A5: Vorschau nennt ein echtes Beispiel statt Platzhalter",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Sag mir Bescheid, wenn die Temperatur im Wohnzimmer unter 18 Grad fällt.", no_calls=True,
      all=["zum beispiel"], none=["{", "}", "<", "xx"]),
  say("Nein."))
S("n793-b1-weather", L793, "B1: Wetterfragen aus der weather-Entität",
  service("haus_sim.reset", {"full": True}), WEATHER_RESET,
  say("Wie ist das Wetter gerade?", no_calls=True, all=["wettervorhersage"]),
  say("Wie wird das Wetter morgen?", no_calls=True, all=["morgen", "regen"]),
  set_("weather.zuhause", {"rain_from_hour": 2}, settle=1),
  say("Regnet es heute noch?", no_calls=True, all=["ja"]),
  WEATHER_RESET,
  say("Brauche ich heute einen Schirm?", no_calls=True, all=["nein"]))
S("n793-b1-awning-rain", L793, "B1: Markise bei Regenvorhersage einfahren – wirkt nur bei Regen",
  service("haus_sim.reset", {"full": True}), WEATHER_RESET,
  service("cover.open_cover", {"entity_id": "cover.markise"}), wait(8),
  say("Wenn Regen angesagt ist, fahr die Markise ein.", no_calls=True,
      all=["laut wettervorhersage", "markise", "alle 30 minuten"]),
  say(YES, settle=2, any=["erstellt"]),
  service("haus_sim.clear_log"),
  trigger("Wenn Regen angesagt ist"),
  check(not_calls=["cover.markise:close_cover"], state={"cover.markise": "open"}),
  set_("weather.zuhause", {"rain_from_hour": 1}, settle=1),
  trigger("Wenn Regen angesagt ist", settle=8),
  check(calls=["cover.markise:close_cover"], state={"cover.markise": "closed"}),
  WEATHER_RESET)
S("n793-b2-where", L793, "B2: „Wo ist Anna?“ – Zone nur mit Recht oder Haushaltsfreigabe",
  service("haus_sim.reset", {"full": True}),
  options(share_household_location=False),
  set_("device_tracker.handy_anna", "not_home", settle=3),
  say("Wo ist Anna?", no_calls=True, all=["anna ist unterwegs"], none=["latitude", "48,"]),
  say("Wo ist Anna?", user="anna", no_calls=True, any=["anna ist unterwegs", "anna ist in der zone"]),
  say("Ist jemand zuhause?", no_calls=True, all=["philipp"]),
  options(share_household_location=True),
  say("Wo ist Anna?", no_calls=True, any=["anna ist unterwegs", "anna ist in der zone"]),
  options(share_household_location=False),
  set_("device_tracker.handy_anna", "home", settle=3))
S("n793-b3-music", L793, "B3: Musik starten, pausieren, lauter, „Was läuft?“",
  service("haus_sim.reset", {"full": True}),
  say("Spiel Bayern 3 in der Küche.", settle=3, calls=["media_player.kuechenradio:select_source"]),
  check(state={"media_player.kuechenradio": {"state": "playing", "source": "Bayern 3"}}),
  say("Was läuft gerade?", no_calls=True, all=["küchenradio"]),
  say("Lauter.", settle=2, calls=["media_player.kuechenradio:volume_up"]),
  say("Pause.", settle=2, calls=["media_player.kuechenradio:media_pause"],
      state={"media_player.kuechenradio": "paused"}),
  say("Weiter.", settle=2, calls=["media_player.kuechenradio:media_play"],
      state={"media_player.kuechenradio": "playing"}),
  say("Spiel Antenne 1 in der Küche.", no_calls=True, all=["finde ich bei", "verfügbar"]))
S("n793-b4-irrigation", L793, "B4: Bewässerung fällt nach Regen aus, läuft bei Trockenheit",
  service("haus_sim.reset", {"full": True}), WEATHER_RESET,
  set_("weather.zuhause", {"daily": {"0": {"condition": "sunny", "precipitation_probability": 5}}}, settle=1),
  say("Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es nicht geregnet hat bzw. nicht regnen soll.",
      no_calls=True, all=["bewässerung garten öffnen", "regensensor", "heute kein regen angesagt"]),
  say(YES, settle=2, any=["erstellt"]),
  set_("binary_sensor.regensensor", True, settle=2), set_("binary_sensor.regensensor", False, settle=2),
  service("haus_sim.clear_log"),
  trigger("Bewässere jeden Morgen"),
  check(not_calls=["valve.bewaesserung:open_valve"]),
  WEATHER_RESET)
S("n793-b5-notice-tone", L793, "B5: Ton ist Standard; fehlende Rückmeldung gibt den zweiten Ton",
  service("haus_sim.reset", {"full": True}), TONE,
  service("haus_sim.configure", {"target": "light.flurlicht", "report_delay": 0.5}),
  say("Schalte das Flurlicht ein.", satellite=True, settle=3, calls=["light.flurlicht:turn_on"],
      speech_empty=True, announce_count=1),
  service("haus_sim.configure", {"target": "light.flurlicht", "report_delay": 0}),
  service("haus_sim.configure", {"target": "light.stehlampe", "unavailable": False, "report_delay": 30}),
  say("Schalte die Stehlampe ein.", satellite=True, settle=12, calls=["light.stehlampe:turn_on"],
      announce_count=1, announce_match="notice"),
  service("haus_sim.configure", {"target": "light.stehlampe", "report_delay": 0}),
  SPOKEN)
S("n793-b6-house-report", L793, "B6: Haus-Bericht – Vorschau, Senden, Inhalt",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.", no_calls=True,
      all=["jeden sonntag um 18:00 uhr", "haus-bericht", "zum beispiel"]),
  say(YES, settle=2, any=["erstellt", "eingerichtet"]),
  service("haus_sim.clear_log"),
  trigger("Haus-Bericht", settle=4),
  check(notify_count=1, notify_match="Haus-Bericht"),
  say("Was stand im Haus-Bericht?", no_calls=True, any=["batterie", "nicht erreichbar", "kwh"]))
S("n793-b7-monitor-edit", L793, "B7: Überwachung ändern – neue Wirkung nach „Ja“",
  service("haus_sim.reset", {"full": True}), PUSH_BOTH, *BIND_PHONES,
  say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.", no_calls=True),
  say(YES, settle=2, any=["erstellt"]),
  say("Ändere die Garagen-Meldung auf 1 Minute.", no_calls=True, all=["vorher: nach 10 minuten", "nachher"]),
  say(YES, settle=2, all=["geändert"]),
  service("haus_sim.clear_log"),
  service("cover.open_cover", {"entity_id": "cover.garagentor"}),
  wait(75), check(notify_count=1, notify_match="garagentor"),
  service("cover.close_cover", {"entity_id": "cover.garagentor"}))
