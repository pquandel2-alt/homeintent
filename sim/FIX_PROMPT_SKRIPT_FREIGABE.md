# Auftrag: Skripte, Szenen und Gruppen dürfen die Freigabeliste nicht umgehen

Dringender Sicherheitsfix für HomeIntent. Umsetzen auf Basis von **7.3.0**
(Branch `claude/sprachverstaendnis-prompt-dkzpr9`, Commit `df3ae85`) als **7.3.1**.
Nur dieser Fix: keine NLU-Arbeit, keine Refactorings außerhalb des Ausführungspfads.

## 1. Der Fehler

HomeIntent prüft vor dem Start eines Skripts, einer Szene oder einer Gruppe nur, ob
**diese eine Entität** freigegeben ist. Was sie wirklich schaltet, wird nicht geprüft.
Home Assistant führt danach alles aus, was darin steht. Ein einziges freigegebenes
Skript hebelt damit die Freigabeliste, `read_only_entities`, `admin_only_entities`, die
Risikostufen und die Bestätigungspflicht aus.

### Nachweis im Testhaus (7.1.2, 7.2.1 und 7.3.0 identisch)

Freigegeben: nur `script.nachtruhe` und `light.schlafzimmerlicht`. **Nicht** freigegeben:
`vacuum.saugroboter`, `switch.kaffeemaschine`.

```yaml
nachtruhe:
  alias: Nachtruhe
  sequence:
    - action: light.turn_off
      target: {entity_id: light.schlafzimmerlicht}
    - action: vacuum.start
      target: {entity_id: vacuum.saugroboter}
    - action: switch.turn_on
      target: {entity_id: switch.kaffeemaschine}
```

```
> Starte den Saugroboter.        → abgelehnt: kein … für HomeIntent freigegebenes Gerät   (richtig)
> Schalte die Kaffeemaschine ein. → abgelehnt                                               (richtig)
> Aktiviere Nachtruhe.           → „Nachtruhe ausgeführt.“
    call light.schlafzimmerlicht turn_off
    call vacuum.saugroboter start        ← nicht freigegeben
    call switch.kaffeemaschine turn_on   ← nicht freigegeben
```

### Realer Vorfall beim Projektinhaber

Ein Skript „Gute Nacht“ sollte Rollläden schließen. Sein erster Schritt lautet:

```yaml
- action: button.press
  target: {floor_id: erdgeschoss}
  alias: Rolladen Runterfahren
```

Dieser Schritt drückt **jeden Button im Erdgeschoss**. Dadurch startete der Saugroboter,
und die Brandmelder lösten ihren Selbsttest aus. Beide Geräte stehen nicht in der
Freigabeliste. Mit dem Fix darf HomeIntent dieses Skript nicht mehr stillschweigend
starten, sondern muss sagen, welche nicht freigegebenen Geräte es schalten würde.

## 2. Architekturregeln (verbindlich)

- **Kein Parser und kein NLU-Modul** entscheidet darüber. Die Prüfung gehört in den
  Ausführungspfad: Auflösung der wirksamen Ziele → `execution_policy.evaluate_service_plan`
  → `service_executor.async_execute_service_plan`. Diese Stellen bleiben die einzigen
  Autoritäten.
- **Genau eine Funktion** ermittelt die wirksamen Ziele, z. B. ein neues Modul
  `effective_targets.py`. Alle Aufrufer benutzen sie; niemand baut eine zweite
  Expansion.
- **Prüfung unmittelbar vor dem Schreiben.** Die Expansion läuft in
  `async_execute_service_plan` direkt vor `hass.services.async_call`, also auch nach
  einer Bestätigung erneut. Wurde das Skript zwischen Vorschau und „Ja“ geändert, gilt
  der neue Inhalt.
- **Deterministisch, nie raten.** Was sich nicht statisch bestimmen lässt, gilt als
  „unbekannt“ und wird nicht ausgeführt (siehe 3.4).
- `evaluate_service_plan` bleibt eine reine Funktion ohne `hass`. Sie bekommt die
  expandierten Ziele als zusätzlichen Parameter (`effective_target_ids`) übergeben.

## 3. Soll-Verhalten

### 3.1 Wirksame Ziele ermitteln

Für einen Plan auf `script.*`, `scene.*`, `group.*` oder eine Gruppen-Entität (Light-,
Switch-, Cover- und Fan-Gruppen mit Attribut `entity_id`) wird die Menge der
**wirksamen Ziel-Entitäten** bestimmt:

| Quelle | Vorgehen |
| --- | --- |
| Skript | Home-Assistant-Helfer aus `homeassistant.components.script`: `entities_in_script`, `devices_in_script`, `areas_in_script`, `floors_in_script`, `labels_in_script` |
| Szene | `homeassistant.components.homeassistant.scene.entities_in_scene` bzw. das Szenen-Attribut `entity_id` |
| Gruppe | Attribut `entity_id` der Gruppen-Entität, rekursiv |
| Geräte-Aktionen (`type:`/`domain:`/`device_id:`/`entity_id:` als Registry-UUID) | UUID über `entity_registry.async_resolve_entity_id` in eine `entity_id` auflösen |
| `target: device_id` | Entitäten des Geräts **in der Domäne der Aktion** (`button.press` → nur `button.*` des Geräts) |
| `target: area_id` / `floor_id` / `label_id` | alle Entitäten dieses Bereichs, dieser Etage bzw. dieses Labels **in der Domäne der Aktion**, genau wie Home Assistant sie auflöst (Entitätsbereich vor Gerätebereich) |
| Aufrufe anderer Skripte (`script.turn_on`, `action: script.xyz`), Szenen (`scene.turn_on`) und `automation.trigger` | rekursiv expandieren, mit Zyklenschutz und Tiefenbegrenzung (z. B. 8) |

Schritte ohne Geräteziel sind unkritisch und werden ignoriert: `delay`, `wait_*`,
`notify.*` ohne Entität, `persistent_notification.*`, `logbook.log`, Variablen und
Bedingungen. Bedingungen und `choose`-/`if`-Zweige werden **alle** berücksichtigt,
nicht nur der aktuell zutreffende Zweig.

### 3.2 Entscheidung

Auf die wirksamen Ziele gelten **dieselben Regeln wie für direkte Befehle**:

1. **Nicht freigegeben** (nicht in `available_ids`, also nicht in der HomeIntent- bzw.
   Assist-Freigabe) → **DENY**, nichts ausführen. Eine Bestätigung darf das **nicht**
   überstimmen, denn die Freigabe ist die Konfiguration des Nutzers.
2. `read_only_entities` → DENY.
3. `admin_only_entities` und Nicht-Admin → DENY.
4. **Risiko = Maximum über alle wirksamen Ziele.** Ein Skript mit Schloss, Alarmanlage,
   Sirene, Garagentor oder Ventil wird so behandelt wie der direkte Befehl darauf
   (CONFIRM bzw. nur für Admins).
5. `max_action_targets` zählt die wirksamen Ziele.
6. Ist alles erlaubt, verhält sich HomeIntent wie bisher.

### 3.3 Antworttext (Deutsch, konkret)

> Das Skript „Gute Nacht“ schaltet auch Geräte, die für HomeIntent nicht freigegeben
> sind: Saugroboter Reinigung starten, Brandmelder Flur Selbsttest, Brandmelder Küche
> Selbsttest. Ich habe nichts ausgeführt.

- Freundliche Namen verwenden, höchstens 5 nennen, danach „und N weitere“.
- Bei Bereichs- oder Etagenzielen den Hinweis ergänzen, woher die Geräte kommen: „Der
  Schritt ‚Rolladen Runterfahren‘ drückt alle Buttons im Erdgeschoss.“ Dafür den
  `alias` des Schritts verwenden, falls vorhanden.
- Im Audit-Log und in den Diagnosedaten die vollständige Liste speichern.

### 3.4 Nicht bestimmbare Ziele

Schritte mit Templates im Ziel (`entity_id: "{{ … }}"`, `target: "{{ … }}"`),
`event:`-Schritte, `python_script.*`, `shell_command.*`, `rest_command.*` und
Dienstaufrufe fremder Integrationen mit Ziel in `data` lassen sich nicht statisch
prüfen. Standard: **DENY** mit dem Hinweis „Das Skript enthält Schritte, deren Ziel ich
vorher nicht prüfen kann (Schritt ‚…‘).“ Eine Option
`script_unverifiable_steps: deny | confirm` (Standard `deny`) darf das auf
Bestätigung lockern. Den Standard nicht ändern.

### 3.5 Weitere Pfade mit derselben Lücke

Dieselbe Expansion muss überall dort greifen, wo HomeIntent ein Skript, eine Szene oder
eine Gruppe auslöst oder das Auslösen einplant:

- direkte Befehle („Aktiviere …“, „Starte das Skript …“, Szenen, Bedürfnis-Compiler
  `nlu/need_compiler.py`, `situation_views.py`),
- zeitversetzte und geplante Befehle,
- Agent- und Proaktiv-Pfad (`agent_runtime.py`, `proactive_decision.py`,
  `standing_permission.py`); eine Daueranweisung für ein Skript deckt nie mehr ab als
  dessen wirksame Ziele,
- Automationen, die HomeIntent anlegt (`validate_automation_action_targets`): Die
  Aktion „Skript X starten“ wird beim Anlegen mit den wirksamen Zielen von X geprüft.
  Dass X später bearbeitet werden kann, in der README unter „Grenzen“ dokumentieren.

Mit `grep -rn "evaluate_service_plan\|async_execute_service_plan" custom_components/`
sicherstellen, dass kein Aufrufer die Expansion umgeht. `conversation.py` ruft
`evaluate_service_plan` an zwei Stellen direkt auf (Zeilen um 2974 und 4381); auch dort
müssen die wirksamen Ziele übergeben werden.

## 4. Tests

### Unit-Tests (neu, `tests/test_effective_targets.py` und Policy-Tests)

1. Skript mit nicht freigegebener `entity_id` → DENY mit Namen im Text, kein Service-Aufruf.
2. **Genau der reale Fall:** `button.press` mit `floor_id` → Buttons von Saugroboter
   und Brandmelder auf der Etage nicht freigegeben → DENY; wenn alle Buttons der Etage
   freigegeben sind → ALLOW.
3. Geräte-Aktion mit Registry-UUID (`type: turn_on`, `domain: light`) → korrekt
   aufgelöst und geprüft.
4. `target: device_id` expandiert nur Entitäten der Aktionsdomäne.
5. Verschachteltes Skript (A ruft B, B schaltet ein nicht freigegebenes Gerät) → DENY;
   Zyklus A ↔ B → kein Hänger, DENY mit Hinweis.
6. `choose`/`if`: auch der Zweig, der gerade nicht zutrifft, wird geprüft.
7. Szene mit nicht freigegebenem Mitglied → DENY; Lichtgruppe dito.
8. Skript mit Schloss (freigegeben) → CONFIRM; Nicht-Admin und kritisch → DENY.
9. Template-Ziel → DENY; mit Option `confirm` → CONFIRM.
10. Skript wird zwischen Vorschau und „Ja“ geändert → neuer Inhalt wird geprüft.
11. Skript nur mit `notify`, `delay` und freigegebenen Lichtern → ALLOW wie bisher.
    Kein bestehender Test darf rot werden.
12. Agent, Proaktiv-Pfad und Daueranweisung können das DENY nicht umgehen.
13. Eine von HomeIntent angelegte Automation, die ein Skript mit nicht freigegebenem
    Ziel startet, wird beim Anlegen abgelehnt.

### Abnahme im Live-Testhaus

Die Abnahme macht die Test-Session in `sim/`; sie reproduziert dabei den obigen
Nachweis und den Etagen-Button-Fall. Erwartet:
„Aktiviere Nachtruhe.“ → nichts geschaltet, Antwort nennt Saugroboter und
Kaffeemaschine; alle 126 Funktionsszenarien bleiben grün.

## 5. Lieferumfang

- Version **7.3.1** in `manifest.json`, `CHANGELOG`/README-Release-Notes mit einem
  klaren Sicherheitshinweis („Skripte, Szenen und Gruppen konnten nicht freigegebene
  Geräte schalten“).
- README, Abschnitt Sicherheit/Grenzen: Wie die Prüfung funktioniert, was als „nicht
  prüfbar“ gilt und wie man ein Skript freigabefähig macht (konkrete `entity_id`s statt
  `floor_id`/`area_id`, oder die betroffenen Geräte freigeben).
- Alle bestehenden Tests, `ruff` und die hassfest- bzw. HACS-Prüfungen grün.
- Commit und Push auf den eigenen Branch. Keine Änderungen an `sim/`.
