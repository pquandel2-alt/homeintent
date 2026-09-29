from homeintent.entities import (
    EntityMatchSource,
    EntitySnapshot,
    ResolutionStatus,
)
from homeintent.nlu.target_resolution import resolve_phrase
from homeintent.nlu.entity_clarification import (
    CandidateReplyKind,
    candidate_labels,
    render_candidate_question,
    resolve_candidate_reply,
)


CANDIDATES = (
    EntitySnapshot(
        "light.a", "Bürolicht", "light", "off",
        area_id="buero_1", area_name="Büro 1", aliases=("Lampe links",),
    ),
    EntitySnapshot(
        "light.b", "Bürolicht", "light", "off",
        area_id="buero_2", area_name="Büro 2", aliases=("Lampe rechts",),
    ),
)


def test_labels_and_question_name_distinguishing_areas():
    assert candidate_labels(CANDIDATES) == (
        "Bürolicht im Bereich Büro 1",
        "Bürolicht im Bereich Büro 2",
    )
    question = render_candidate_question(CANDIDATES)
    assert "1. Bürolicht im Bereich Büro 1" in question
    assert "2. Bürolicht im Bereich Büro 2" in question


def test_reply_can_select_by_ordinal_area_alias_and_descriptor():
    cases = (
        ("das zweite", "light.b"),
        ("Nummer 1", "light.a"),
        ("Büro 2", "light.b"),
        ("Lampe links", "light.a"),
        ("die rechte", "light.b"),
    )
    for reply, entity_id in cases:
        resolved = resolve_candidate_reply(reply, CANDIDATES, list(CANDIDATES))
        assert resolved.kind is CandidateReplyKind.SELECTED, reply
        assert resolved.entity is not None
        assert resolved.entity.entity_id == entity_id


def test_unknown_reply_retries_and_cancel_is_explicit():
    assert resolve_candidate_reply(
        "irgendwas anderes", CANDIDATES, list(CANDIDATES)
    ).kind is CandidateReplyKind.RETRY
    assert resolve_candidate_reply(
        "keins davon", CANDIDATES, list(CANDIDATES)
    ).kind is CandidateReplyKind.CANCELLED


def test_single_candidate_supports_explicit_confirmation_and_rejection():
    single = CANDIDATES[:1]

    assert render_candidate_question(single) == "Meintest du Bürolicht?"
    selected = resolve_candidate_reply("ja", single, list(single))
    rejected = resolve_candidate_reply("nein", single, list(single))

    assert selected.kind is CandidateReplyKind.SELECTED
    assert selected.entity == single[0]
    assert rejected.kind is CandidateReplyKind.CANCELLED


def test_multiple_fuzzy_names_are_ambiguous_but_single_fuzzy_needs_confirmation():
    similar = [
        EntitySnapshot("light.a", "Küchenlicht", "light", "off"),
        EntitySnapshot("light.b", "Kuchenlicht", "light", "off"),
    ]
    ambiguous = resolve_phrase("Kuechenlict", similar)
    single = resolve_phrase("Kuechenlict", similar[:1])

    assert ambiguous.status is ResolutionStatus.AMBIGUOUS
    assert len(ambiguous.candidates) == 2
    assert ambiguous.candidate_set is not None
    assert all(
        item.source is EntityMatchSource.FUZZY
        for item in ambiguous.candidate_set.ranked
    )
    assert single.status is ResolutionStatus.CONFIRMATION_REQUIRED


def test_more_than_five_candidates_are_bounded_in_speech():
    candidates = tuple(
        EntitySnapshot(f"light.{index}", f"Licht {index}", "light", "off")
        for index in range(7)
    )
    question = render_candidate_question(candidates)

    assert "5. Licht 4" in question
    assert "6. Licht 5" not in question
    assert "2 weitere" in question
