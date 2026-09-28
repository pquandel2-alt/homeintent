"""Phase 6 (7.4.1): learning the household's language from the dialog."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.bindings import BindingKind, BindingScope  # noqa: E402
from homeintent.dialog_learning import (  # noqa: E402
    alias_rejection,
    is_about_me_question,
    macro_invoked,
    parse_activity_announcement,
    parse_forget,
    parse_macro_definition,
    parse_preference_question,
    parse_preference_statement,
    unknown_device_noun,
)
from homeintent.conversation_learning import is_known_device_word  # noqa: E402
from homeintent.nlu.target_resolution import resolve_phrase  # noqa: E402
from homeintent.nlu.device_ontology import lookup_genus_word  # noqa: E402

HOUSE = house_entities()
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _bind(house: HouseConversation, kind, key, target, *, scope=BindingScope.HOUSEHOLD, user=None, data=None):
    return asyncio.run(house.entity._runtime_data.bindings.async_bind(
        kind, key, target, confirmed=True, scope=scope, user_id=user, created_by=user or "admin",
        now=NOW, data=data or {},
    ))


# ------------------------------------------------------------ lexicon, not sentences
FORMS = [
    ("Mach die Kuschelecke an.", {"light.stehlampe"}),
    ("Schalte die Kuschelecke aus.", {"light.stehlampe"}),
    ("Kuschelecke an.", {"light.stehlampe"}),
    ("Dimme die Kuschelecke auf 30 Prozent.", {"light.stehlampe"}),
    ("Kannst du bitte die Kuschelecke einschalten?", {"light.stehlampe"}),
    ("Mach die Kuschelecke und das Küchenlicht an.", {"light.stehlampe", "light.kuechenlicht"}),
    ("Mach die Kuschelecke heller.", {"light.stehlampe"}),
    ("Stell die Kuschelecke auf 50 Prozent.", {"light.stehlampe"}),
    ("Schalt mal die Kuschelecke ein.", {"light.stehlampe"}),
    ("Die Kuschelecke bitte ausschalten.", {"light.stehlampe"}),
]


@pytest.mark.parametrize("sentence,targets", FORMS)
def test_a_learned_word_works_in_every_sentence_form(monkeypatch, sentence, targets):
    house = HouseConversation(monkeypatch)
    _bind(house, BindingKind.ALIAS, "Kuschelecke", "light.stehlampe", data={"spoken": "Kuschelecke"})
    assert house.say(sentence).targets == targets


@pytest.mark.parametrize("sentence,expected", [
    ("Ist die Kuschelecke an?", "Stehlampe"),
    ("Schalte in 10 Minuten die Kuschelecke aus.", "Stehlampe ausschalten"),
    ("Lass die Kuschelecke an.", "Ich ändere nichts"),
])
def test_a_learned_word_in_questions_time_commands_and_keep_as_is(monkeypatch, sentence, expected):
    house = HouseConversation(monkeypatch)
    _bind(house, BindingKind.ALIAS, "Kuschelecke", "light.stehlampe", data={"spoken": "Kuschelecke"})
    turn = house.say(sentence)
    assert turn.calls == [] and expected in turn.speech, turn.speech


def test_negation_with_a_learned_word_never_writes(monkeypatch):
    house = HouseConversation(monkeypatch)
    _bind(house, BindingKind.ALIAS, "Kuschelecke", "light.stehlampe", data={"spoken": "Kuschelecke"})
    assert house.say("Mach die Kuschelecke nicht an.").calls == []


def test_teaching_dialog_stores_a_household_alias(monkeypatch):
    house = HouseConversation(monkeypatch)
    ask = house.say("Mit Kuschelecke meine ich die Stehlampe.")
    assert "Soll ich" in ask.speech and ask.calls == []
    assert house.entity._runtime_data.bindings.all() == ()
    assert "Gespeichert" in house.say("Ja.").speech
    (alias,) = house.entity._runtime_data.bindings.all(BindingKind.ALIAS)
    assert alias.scope is BindingScope.HOUSEHOLD
    other = HouseConversation(monkeypatch, user="anna")
    other.entity._runtime_data.bindings = house.entity._runtime_data.bindings
    assert other.say("Mach die Kuschelecke an.").targets == {"light.stehlampe"}


# ------------------------------------------------------------ unknown words
def test_unknown_device_noun_is_asked_about_then_executed_and_offered(monkeypatch):
    house = HouseConversation(monkeypatch, area="wohnzimmer")
    ask = house.say("Schalte den Zauberkasten aus.")
    assert ask.calls == [] and ask.speech.startswith("Was meinst du mit „Zauberkasten“?")
    assert "Stehlampe" in ask.speech
    done = house.say("Die Stehlampe.")
    assert done.targets == {"light.stehlampe"}
    assert "Soll ich mir „Zauberkasten“ als Namen für Stehlampe merken?" in done.speech
    assert house.entity._runtime_data.bindings.all() == ()
    house.say("Ja.")
    assert house.say("Mach den Zauberkasten an.").targets == {"light.stehlampe"}


def test_unknown_word_no_stores_nothing(monkeypatch):
    house = HouseConversation(monkeypatch, area="wohnzimmer")
    house.say("Schalte den Zauberkasten aus.")
    house.say("Die Stehlampe.")
    assert "nichts" in house.say("Nein.").speech
    assert house.entity._runtime_data.bindings.all() == ()


def test_unknown_word_is_never_completed_from_the_previous_device(monkeypatch):
    house = HouseConversation(monkeypatch, area="wohnzimmer")
    house.say("Ist die Stehlampe an?")
    turn = house.say("Mach den Zauberkasten an.")
    assert turn.calls == [] and "Zauberkasten" in turn.speech


def test_multi_command_with_unknown_part_is_not_turned_into_a_word_question(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Mach das Bürolicht an und flausche den Teppich.")
    assert turn.calls == [] and "Was meinst du mit" not in turn.speech


@pytest.mark.parametrize("text,word", [
    ("Schalte den Zauberkasten aus.", "Zauberkasten"),
    ("Mach die Kuschelecke an.", "Kuschelecke"),
    ("Mach das Küchenlicht an.", None),
    ("Schalte die Lampe ein.", None),
    ("Mach den Zauberkasten und das Licht an.", None),
])
def test_unknown_device_noun_detection(text, word):
    assert unknown_device_noun(text, HOUSE, is_known_device_word) == word


# ------------------------------------------------------------ safety of learning
def test_alias_never_overwrites_device_room_or_genus_names():
    stehlampe = next(entity for entity in HOUSE if entity.entity_id == "light.stehlampe")
    def genus(word):
        return bool(lookup_genus_word(word))
    assert "Gerätenamen" in alias_rejection("Küchenlicht", stehlampe, HOUSE, is_admin=True, genus_word=genus)
    assert "Raumnamen" in alias_rejection("Büro", stehlampe, HOUSE, is_admin=True, genus_word=genus)
    assert "Gattung" in alias_rejection("Lampe", stehlampe, HOUSE, is_admin=True, genus_word=genus)
    assert alias_rejection("Kuschelecke", stehlampe, HOUSE, is_admin=False, genus_word=genus) is None


def test_aliases_for_critical_targets_only_by_admins(monkeypatch):
    lock = next(entity for entity in HOUSE if entity.domain == "lock")
    assert "Administratoren" in alias_rejection("Riegelchen", lock, HOUSE, is_admin=False, genus_word=is_known_device_word)
    turn = HouseConversation(monkeypatch, user="anna").say(f"Mit Riegelchen meine ich {lock.friendly_name}.")
    assert "Administratoren" in turn.speech and "nichts gespeichert" in turn.speech


def test_binding_to_a_no_longer_exposed_target_is_inert_and_marked(monkeypatch):
    house = HouseConversation(monkeypatch, entities=[e for e in HOUSE if e.entity_id != "light.stehlampe"])
    _bind(house, BindingKind.ALIAS, "Kuschelecke", "light.stehlampe", data={"spoken": "Kuschelecke"})
    assert house.say("Mach die Kuschelecke an.").calls == []
    assert "wirkungslos" in house.say("Was weißt du über mich?").speech


def test_learning_is_only_stored_after_yes(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Wenn ich lese, möchte ich die Stehlampe auf 60 Prozent.")
    house.say("Mach das Küchenlicht an.")  # a new command lets the offer lapse
    assert house.entity._runtime_data.bindings.all() == ()


# ------------------------------------------------------------ default choices
def test_same_clarification_twice_offers_a_personal_default_choice(monkeypatch):
    house = HouseConversation(monkeypatch)
    for _ in range(2):
        house.say("Schalte die Nachttischlampe ein.")
        last = house.say("Die linke.")
    assert "Soll ich das künftig" in last.speech
    house.say("Ja.")
    (choice,) = house.entity._runtime_data.bindings.all(BindingKind.DEFAULT_CHOICE)
    assert choice.scope is BindingScope.USER and choice.user_id == "admin"
    assert house.say("Schalte die Nachttischlampe ein.").targets == {"light.nachttischlampe_links"}
    # Naming the other one explicitly always wins.
    assert house.say("Schalte die Nachttischlampe rechts aus.").targets == {"light.nachttischlampe_rechts"}
    # Another person is still asked.
    anna = HouseConversation(monkeypatch, user="anna")
    anna.entity._runtime_data.bindings = house.entity._runtime_data.bindings
    assert anna.say("Schalte die Nachttischlampe ein.").calls == []


def test_different_answers_do_not_offer_a_default(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Schalte die Nachttischlampe ein.")
    house.say("Die linke.")
    house.say("Schalte die Nachttischlampe ein.")
    assert "künftig" not in house.say("Die rechte.").speech


# ------------------------------------------------------------ preferences
def test_preference_is_stored_answered_previewed_and_then_used(monkeypatch):
    house = HouseConversation(monkeypatch)
    offer = house.say("Wenn ich lese, möchte ich die Stehlampe auf 60 Prozent.")
    assert offer.calls == [] and "merken" in offer.speech
    house.say("Ja.")
    assert "60 Prozent" in house.say("Wie hell möchte ich lesen?").speech
    preview = house.say("Ich lese jetzt.")
    assert preview.calls == [] and preview.speech.endswith("Soll ich das machen?")
    assert house.say("Ja.").targets == {"light.stehlampe"}
    assert house.say("Ich will lesen.").targets == {"light.stehlampe"}
    assert "vergessen" in house.say("Vergiss, wie hell ich lesen möchte.").speech
    assert house.say("Ich will lesen.").calls == []


def test_preferences_are_personal_by_default(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Wenn ich lese, möchte ich die Stehlampe auf 60 Prozent.")
    house.say("Ja.")
    (preference,) = house.entity._runtime_data.bindings.all(BindingKind.PREFERENCE)
    assert preference.scope is BindingScope.USER and preference.user_id == "admin"


# ------------------------------------------------------------ macros
def test_macro_is_a_confirmed_sentence_run_through_the_pipeline(monkeypatch):
    house = HouseConversation(monkeypatch)
    offer = house.say(
        "Wenn ich Kinoabend sage, dann mach das Wohnzimmer Deckenlicht aus und fahre die Wohnzimmer Rollläden runter."
    )
    assert offer.calls == [] and "keine Automation" in offer.speech
    house.say("Ja.")
    turn = house.say("Kinoabend.")
    assert turn.targets == {
        "light.wohnzimmer_deckenlicht", "cover.wohnzimmer_rollladen_links", "cover.wohnzimmer_rollladen_rechts",
    }
    house.say("Vergiss Kinoabend.")
    assert house.say("Kinoabend.").calls == []


def test_macro_with_a_critical_step_still_asks_on_every_use(monkeypatch):
    lock = next(entity for entity in HOUSE if entity.domain == "lock")
    house = HouseConversation(monkeypatch)
    house.say(f"Wenn ich Feierabend sage, dann schließe {lock.friendly_name} auf.")
    house.say("Ja.")
    turn = house.say("Feierabend.")
    assert turn.calls == [], turn.speech


def test_macro_names_never_shadow_devices_rooms_or_genera(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Wenn ich Küche sage, dann mach das Küchenlicht an.")
    assert "anderes Wort" in turn.speech


# ------------------------------------------------------------ about me, German
def test_about_me_lists_in_german(monkeypatch):
    house = HouseConversation(monkeypatch)
    _bind(house, BindingKind.ALIAS, "Kuschelecke", "light.stehlampe", data={"spoken": "Kuschelecke"})
    speech = house.say("Was weißt du über mich?").speech
    assert "„Kuschelecke“ heißt Stehlampe" in speech
    for english in ("preference", "alias", "default_choice", "macro", "household"):
        assert english not in speech


def test_memory_disabled_explains_where_to_switch_it_on():
    from homeintent.dialog_learning import MEMORY_DISABLED_TEXT

    assert "Konfigurieren" in MEMORY_DISABLED_TEXT and "Gedächtnis" in MEMORY_DISABLED_TEXT


# ------------------------------------------------------------ parsers
@pytest.mark.parametrize("text,key", [
    ("Ich lese jetzt.", "lesen"), ("Ich will lesen.", "lesen"), ("Ich schaue jetzt fern.", "fernsehen"),
    ("Ich lese gern Bücher.", None), ("Liest du?", None),
])
def test_activity_announcements(text, key):
    assert parse_activity_announcement(text) == key


def test_preference_statement_forms():
    for text in (
        "Wenn ich lese, möchte ich die Stehlampe auf 60 Prozent.",
        "Merk dir, zum Lesen möchte ich die Stehlampe auf 60 Prozent.",
    ):
        draft = parse_preference_statement(text, HOUSE, resolve_phrase)
        assert draft is not None and draft.entity.entity_id == "light.stehlampe" and draft.brightness == 60
    assert parse_preference_question("Wie hell mag ich es beim Lesen?") == "lesen"


def test_macro_and_forget_parsers():
    draft = parse_macro_definition("Wenn ich „Guten Morgen“ sage, mach das Küchenlicht an.")
    assert draft is not None and draft.name == "Guten Morgen" and draft.body == "Mach das Küchenlicht an."
    assert parse_forget("Vergiss den Namen Kuschelecke.").key == "kuschelecke"
    assert parse_forget("Vergiss alles.") is None
    assert is_about_me_question("Was hast du dir über mich gemerkt?")


def test_macro_invocation_matching():
    from homeintent.bindings import Binding

    macro = Binding("m", BindingKind.MACRO, "kinoabend", "macro")
    assert macro_invoked("Kinoabend bitte!", [macro]) is macro
    assert macro_invoked("Starte Kinoabend.", [macro]) is macro
    assert macro_invoked("Wie war der Kinoabend gestern?", [macro]) is None
