# HomeIntent 7.2.0 — Natural Language Automations

## 1. Audit of the production failure (7.1.2, `10d9513`)

Utterance: `Schicke mir eine Benachrichtigung wenn im Büro die Rolllade 50% erreicht hat`

| stage | 7.1.2 behaviour | meaning lost |
|---|---|---|
| automation detection | `_AUTOMATION_TRIGGER_RE` sees `wenn` → `match_automation()` | – |
| structural split | `split_automation_document()` needs a structural IF relation; no comma → no relation; `split_trigger_action()` needs a comma | clause boundary |
| candidate search | `…\s+(wenn\|sobald\|falls)\s+…` marker split: action half parses as notification, trigger half `wenn im Büro die Rolllade 50 Prozent erreicht hat` is offered to `AutomationTriggerParser` | – |
| trigger parser | the equality shape required exactly one final verb (`beträgt\|erreicht\|hat`), so `erreicht hat` failed; the free `{name}` slot would have grounded `im Büro die Rolllade` fuzzily (`resolve_entity_scored("Rolllade im Büro")` returns **`light.buero`**) | predicate, subject |
| trigger model | only entity-state `NUMERIC_STATE` existed; a cover's percentage is the `current_position` *attribute* | property |
| result | no candidate → `understanding_feedback()` → "Ich konnte Trigger und Aktion der Automation nicht eindeutig erkennen." | everything |

Working example `Benachrichtige mich, wenn das Wohnzimmer Fenster geöffnet wird.`
succeeded because the comma produced a structural IF relation and the state
grammar matched the whole trigger.

## 2. Architecture

```
utterance
  → prepare_automation_text()      repairs, STT rejoins, shared normalize() (never inside dictated text)
  → segment_event_automation()     EVENT clause + ACTION clause, both orders, no comma needed
  → read_event_roles()             SUBJECT / LOCATION / COMPARATOR / VALUE / UNIT / STATE / DIRECTION / DURATION / CONDITION
  → ground_event()                 typed: device noun class + area + literal name modifiers
  → TriggerModel                   existing model (+ measurement, direction, presence_event)
  → read_actions()                 notification_language / established action parsers, chunk by chunk
  → AutomationModel                existing validator, preview, generator, writer, confirmation
```

* `automation_language.py` – segmentation and role extraction.
* `automation_grounding.py` – typed grounding, clarification questions.
* `automation_composition.py` – projection, conditions, actions, follow-ups.
* `nlu/automation_lexicon.py` – the one lexical layer (Rolllade/Rollade/Rollladen/Rolladen/Rollo/Jalousie, STT rejoins, repairs).
* `nlu/measurement.py` – the closed attribute mapping and template builders.

Structure is projected into structure; no German sentence is generated for
another parser.  The grammar trigger parser only receives unchanged source
spans, and a device state/number it would pick through its free name slot is
discarded (typed grounding is authoritative for device events).

## 3. Cover position

`TriggerModel(type=NUMERIC_STATE, measurement=COVER_POSITION, comparator=EQUAL, threshold=50)`.
Generation (built only from the mapping, a validated entity id and a number):

```yaml
trigger: template
value_template: "{{ (state_attr('cover.buero_rollladen', 'current_position') is number and state_attr('cover.buero_rollladen', 'current_position') == 50) }}"
```

Inclusive bounds use `>=`/`<=` templates; an explicit direction uses a state
trigger on the attribute plus arrival and travel-sense template conditions.
`scripts/validate_measurement_automation_ha.py` runs these automations in a
real Home Assistant core (20 → 35 → 50: exactly one `notify.send_message`).

## 4. Notification delivery

Event notifications keep the 7.1.2 delivery architecture: the recipient is
resolved by `NotificationTargetResolver` when the preview is created and the
automation calls `notify.send_message` on exactly that target. A rejected
push fails the Home Assistant automation run (visible in its trace); nothing
is reported as delivered by HomeIntent itself.

## 5. Known gaps (not implemented in 7.2.0)

* Recipient binding happens at confirmation, not at run time; after changing
  the phone the automation must be re-created (7.1.2 design, unchanged).
* No HomeIntent activity entry for event-notification delivery (spec §67/§69).
* "Auf welches Gerät soll die Nachricht gehen?" follow-up: several push
  targets still yield the existing binding guidance.
* Relative/rate triggers ("um 2 Grad steigt", "schneller als") are rejected
  with an explanation; aggregate states ("alle Fenster offen") likewise.
* Modal-passive device wishes ("Die Bürolampe soll ausgehen, wenn …"),
  participial conditions ("Bei geöffnetem Bürofenster …"), one-shot
  reminders with fronted dates ("Morgen um acht erinnere mich …") and
  "Es wäre super, wenn du mir Bescheid gibst, sobald …" are held-out misses.
