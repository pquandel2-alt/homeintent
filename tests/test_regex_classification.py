"""Phase 8 (7.5.x): every regex is classified; sentence patterns only shrink."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import regex_inventory  # noqa: E402


def test_classification_file_matches_the_code():
    stored = regex_inventory.load()
    stored_ids = {entry["id"] for entry in stored["eintraege"]}
    current_ids = {entry["id"] for entry in regex_inventory.inventory()}
    missing = sorted(current_ids - stored_ids)
    stale = sorted(stored_ids - current_ids)
    assert not missing and not stale, (
        "Regex-Klassifikation veraltet - python scripts/regex_inventory.py --write\n"
        f"neu: {missing[:10]}\nentfernt: {stale[:10]}"
    )


def test_every_entry_has_a_known_class():
    for entry in regex_inventory.load()["eintraege"]:
        assert entry["klasse"] in regex_inventory.CLASSES, entry


def test_semantic_sentence_patterns_never_grow():
    stored = regex_inventory.load()
    count = sum(1 for entry in stored["eintraege"] if entry["klasse"] == "SEMANTIC_SENTENCE_PATTERN")
    assert count <= stored["maximum_semantic_sentence_pattern"], (
        f"{count} SEMANTIC_SENTENCE_PATTERN > Maximum {stored['maximum_semantic_sentence_pattern']}"
    )


def test_classifier_examples():
    classify = regex_inventory.classify
    assert classify(r"\s+") == "STRUCTURAL"
    assert classify(r"\b(?:schalte|mach|fahre)\b") == "LEXICAL"
    assert classify(r"\bregl(?:e|en|st|t)?\b") == "MORPHOLOGICAL"
    assert classify(r"^\s*mit\s+(?P<alias>.+?)\s+meine\s+ich\s+(?P<target>.+?)$") == "SEMANTIC_SENTENCE_PATTERN"
