"""Compositional, read-only explanations for omitted device actions."""

from __future__ import annotations

from _testhaus import HouseConversation


def test_why_did_you_not_act_asks_for_an_ambiguous_device(monkeypatch):
    turn = HouseConversation(monkeypatch).say(
        "Warum hast du das Licht nicht eingeschaltet?"
    )

    assert turn.calls == []
    assert "Welches Licht meinst du" in turn.speech


def test_why_did_you_not_act_never_invents_a_reason_without_evidence(monkeypatch):
    turn = HouseConversation(monkeypatch).say(
        "Warum hast du die Stehlampe nicht eingeschaltet?"
    )

    assert turn.calls == []
    assert "keinen lokalen Nachweis" in turn.speech
    assert "keinen Grund behaupten" in turn.speech
