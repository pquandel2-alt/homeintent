from homeintent.nlu.language_frontend import analyse_language
from homeintent.nlu.semantic_utterance import Polarity, SpeechAct
from homeintent.entities import EntitySnapshot


def test_frontend_preserves_original_and_source_spans():
    text = "Ähm, könntest du das Küchenlicht anmachen?"
    document = analyse_language(text)

    assert document.source_text == text
    assert document.variants[0].text == text
    assert document.variants[0].source == "original"
    assert "kannst du" in document.normalized_text.casefold()
    kitchen = next(token for token in document.tokens if token.text == "Küchenlicht")
    assert text[kitchen.start:kitchen.end] == "Küchenlicht"
    assert document.utterance.speech_act is SpeechAct.COMMAND


def test_frontend_never_loses_negation_when_creating_variants():
    document = analyse_language("Mach das Küchenlicht nicht an")

    assert document.utterance.polarity is Polarity.NEGATIVE
    assert all("nicht" in variant.text.casefold() for variant in document.variants)


def test_frontend_records_orthographic_variant_without_overwriting_source():
    document = analyse_language("Öffne die Tür")

    assert document.source_text == "Öffne die Tür"
    assert any(variant.source == "orthographic" for variant in document.variants)
    assert next(
        variant.text for variant in document.variants
        if variant.source == "orthographic"
    ) == "oeffne die tuer"


def test_frontend_proposes_registry_compound_variant_without_changing_source():
    entities = [
        EntitySnapshot(
            "cover.pole_raum", "Rollladen Pole Raum", "cover", "closed",
            area_id="pole_raum", area_name="Pole Raum",
        )
    ]
    document = analyse_language("Fahre den Rollladen im Poleraum hoch", entities)

    assert document.source_text == "Fahre den Rollladen im Poleraum hoch"
    assert any(
        variant.source == "registry_compound" and "pole raum" in variant.text
        for variant in document.variants
    )


def test_frontend_records_bounded_phonetic_candidate_but_does_not_apply_it():
    entities = [
        EntitySnapshot("light.kueche", "Küchenlicht", "light", "off")
    ]
    document = analyse_language(
        "Mach das Küchenlict an", entities, include_phonetic=True
    )

    assert document.source_text.endswith("Küchenlict an")
    assert any(variant.source.startswith("phonetic:") for variant in document.variants)


def test_frontend_proposes_unique_semantic_spelling_correction():
    entities = [EntitySnapshot("light.kueche", "Küchenlicht", "light", "off")]

    document = analyse_language("Bitte Küchenlicht einschallten", entities)

    assert document.source_text.endswith("einschallten")
    assert any(
        variant.source == "lexical_spelling" and "einschalten" in variant.text
        for variant in document.variants
    )


def test_spelling_variant_never_promotes_descriptive_copula_to_command():
    entities = [
        EntitySnapshot(
            "cover.wohnzimmer",
            "Wohnzimmer Rollladen",
            "cover",
            "open",
            area_id="wohnzimmer",
            area_name="Wohnzimmer",
        )
    ]

    document = analyse_language(
        "Die Rolllade im Wohnzimmer ist halb offen.", entities
    )

    assert document.utterance.speech_act is SpeechAct.STATEMENT
    assert not document.utterance.safe_to_execute_directly


def test_copular_question_with_action_homonym_remains_a_query():
    document = analyse_language("Sind die Lichter nicht an?", [])

    assert document.utterance.speech_act is SpeechAct.QUERY


def test_imperative_is_not_downgraded_by_copula_inside_message_payload():
    entities = [EntitySnapshot("notify.handys", "Familienhandys", "notify", "unknown")]

    document = analyse_language(
        "Sende über Familienhandys die Nachricht Essen ist fertig", entities
    )

    assert document.utterance.speech_act is SpeechAct.COMMAND


def test_registry_name_near_negation_is_not_treated_as_negated_command():
    entities = [EntitySnapshot("script.gute_nacht", "Gute Nacht", "script", "off")]

    document = analyse_language("Starte Gute Nacht", entities)

    assert document.utterance.speech_act is SpeechAct.COMMAND
    assert document.utterance.polarity is Polarity.POSITIVE
    assert document.utterance.safe_to_execute_directly


def test_real_words_near_nicht_do_not_trigger_the_safety_gate():
    for sentence in (
        "Mach das Licht an",
        "Mach das Fenster dicht",
        "Starte das Licht heute Nacht",
    ):
        document = analyse_language(sentence)
        assert document.utterance.polarity is Polarity.POSITIVE, sentence
