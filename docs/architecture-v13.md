# HomeIntent 7.3 architecture — understanding language without a language model

Release 7.3.0 extends the existing deterministic NLU pipeline so that
HomeIntent understands everyday German beyond exact device names and fixed
sentence shapes. There is no LLM, no ML model, no embedding and no external
service. Every new capability is knowledge stored as data (ontology,
catalogues) plus general rules over meaning units (word meaning, inflection,
sentence structure, roles, world model). There are no sentence templates, no
regular expressions over complete phrasings and no "sentence → action"
lookup tables.

7.3 is additive: it adds no second executor, validator, policy or
confirmation path. Every new meaning ends in the same `ServiceCallPlan`,
`CommandPlan`, `AutomationModel` or read-only answer as before and passes
the unchanged validator, `ExecutionPolicy`, user binding, `NEVER_AUTO` and
confirmations.

```text
Eingabe
  → clock_language        gesprochene Uhrzeiten → H:MM Uhr, Weckwunsch
  → language_frontend     Tokens, Sprechakt, Modalität (inkl. MAINTAIN), Negation
  → situation_views       lesende Sichten (noch an, alles zu, lüften, …)
  → need_semantics        Bedürfnis → Wirkung (wärmer, heller, lüften, Routine)
  → discourse_compiler    Ellipse/Pronomen/„die andere“/„dort“ → Kontextziel
  → release frame         „X kann aus“ → ausschalten
  → established compilers (hassil, semantic compiler, registered operations)
  → ontology_compiler     Gattung × Ort × Merkmal × Menge (Vorrang/Fallback)
  → validator · ExecutionPolicy · Bestätigung · Ausführung
```

## 1. Ontology (`nlu/device_ontology.py`)

A `Genus` is data: lemmas (first = display form), plural, grammatical
gender, Home Assistant domains, optional `device_class`es, irregular forms,
a `parent` (Raffstore → Rollladen), and flags:

* `critical` — never part of an unspecific "alles" (locks, alarm, garage door);
* `in_everything` — switchable everyday devices that "alles" reaches;
* `sensor` — read-only kinds (Melder, Temperatur, Feuchte, CO2, …);
* `mass_lemmas` — mass nouns: "das Licht im Büro" means every light there;
* `forbidden_actions` — "Musik" cannot be switched on as a device.

`analyse_word()` maps any surface word to genera: inflection, plural,
diminutive, colloquial words ("Glotze"), compounds with the head on the
right ("Wohnzimmer|licht", "Büro-|Rolllade", "Kinderzimmer|jalousie") and
detector word formation ("Rauch|melder", "Wasser|sensor"). A compound head
must be at least four letters; a modifier that is a place becomes the place,
any other modifier becomes a name filter.

`entity_genera()` decides membership from domain, device class and — only
where a class alone is not enough — name evidence (a `media_player` named
"Wohnzimmer TV" is a Fernseher).

## 2. Places (`nlu/place_model.py`)

`build_place_lexicon()` derives places from the registry: areas and area
aliases, floors and floor aliases ("oben", "unten", "EG"), the house, indoor,
outdoor ("draußen"), and "hier" (the speaking satellite). Generic level
words apply only when the house did not name them, and "oben"/"unten"/
"Keller" pick exactly one floor (the unique extreme level). The same lexicon
is used by commands, views, needs, discourse, automations and
notifications — there is one notion of "where".

## 3. Target resolution (`nlu/target_resolution.py`)

A target is a `TargetDescription` = genus × place × features × quantity, or
an explicit registry name. Precedence:

1. exact registry name or alias (longest first);
2. name + place;
3. genus + place (+ side/size filters: "links", "große");
4. genus alone.

A kind word that is also someone's name yields to a quantity ("beide
Rollladen") and to a spoken place where the named device is not.
Quantities: singular → exactly one, otherwise a numbered question; plural,
"alle", "die X", numbers → every match (larger groups get a preview);
"irgendein" → *any* in triggers. Words the description cannot explain are
**residue**; a clause with residue is never executed ("Den Teil … habe ich
nicht verstanden").

`resolve_description()` produces honest outcomes that name kind and place
("Im Büro gibt es keinen Ventilator.", "Im Büro gibt es nur zwei Lichter.").
Fuzzy/phonetic correction stays inside one genus: a Wassermelder can never
become a Bewegungsmelder.

## 4. Compilers

* **Ontology compiler** (`nlu/ontology_compiler.py`) compiles coordinated
  clauses into one `CommandPlan`. It answers when the established compilers
  found nothing and takes precedence when their result is provably wrong:
  an unspecific "alles", a legacy set that is broader than the spoken genus
  ("Rollos" → garage door), readings that are disjoint ("die Lichter unten"),
  a singular kind word with several members (ask instead of picking one),
  or dropped clauses (never execute a subset silently). Time-bound
  sentences are left to scheduling. White tones reach every capable light.
* **Need compiler** (`nlu/need_semantics.py`, `nlu/need_compiler.py`): a
  typed table sensation → effect → capability. Place comes from the
  sentence, else the satellite, else the context. Questions, negation,
  past and counterfactual statements never operate. Low-risk, unique
  effects run with a short reason; routines are proposed.
* **Discourse compiler** (`nlu/discourse_compiler.py`) binds ellipses and
  references to the typed conversation context: last targets, last result
  set, last place, last operation, the named partner for "die andere". An
  explicit new place always outranks the remembered referent.
* **Situation views** (`nlu/situation_views.py`): still on, secure, ventilate
  (documented thresholds 60 % humidity / 1000 ppm CO2), why cold/warm (only
  measured facts), overview, presence per room, controllable devices, rooms
  per floor, count per genus, script/scene contents, device purpose.
* **Time language** (`nlu/clock_language.py`): spoken clock times after a
  time preposition, delays in any word order, wake requests. Scheduled and
  delayed actions reuse the same genus reading and are lifted into exact
  entity lists, never widened to a whole domain.
* **Notifications** share one meaning for immediate, delayed, reminder and
  event push: recipient from binding or name, dictated text separated before
  target resolution, valency frames instead of phrase patterns.

## 5. Safety argument

1. **Parsers never execute.** Every compiler returns a plan; execution stays
   in the existing conversation/executor path with validator, policy, user
   binding and confirmation.
2. **No new broadening.** The genus reading can only narrow or replace a
   legacy reading with one grounded in the registry; lifted automation
   targets are exact entity lists; "alles" excludes critical genera and
   previews mixed or large groups.
3. **Ambiguity asks.** Singular with several members, several capable
   lights for a singular, unknown names in exclusions, missing rooms for a
   wake request — all produce a question or an honest refusal naming kind
   and place.
4. **Residue blocks.** Unexplained words in a clause stop that clause, and a
   partially understood multi-clause command names the part it did not
   understand instead of executing the rest.
5. **Non-assertive language never operates:** questions, embedded
   questions, negation, uncertainty without a request shell, maintenance
   ("lass … an"), past and counterfactual need statements.
6. **Time-bound language never runs immediately.** Clock times, delays and
   wake requests become previewed automations.

Every capability has a generated productivity test (`tests/test_sprache73_*`)
that combines its building blocks freely against the test house; the live
test bed has a scenario category „Sprache 7.3“ with its own sentences.
