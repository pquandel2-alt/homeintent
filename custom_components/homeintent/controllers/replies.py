"""Spoken replies shared by the controllers (7.7, B4).

Confirmation questions, the effect summary after a script or scene, and the
wording of refusals and ambiguous readings. Text only - nothing here
decides or executes.
"""

from __future__ import annotations

from typing import Any

from ..effect_graph import summarize_effects
from ..execution_policy import PolicyOutcome
from ..service_executor import CHANGED_SINCE_CONFIRMATION

# Past-tense or passive endings of service_call.py's response texts and the
# infinitive a confirmation question needs ("Burgtor wird geöffnet" ->
# "Soll ich wirklich Burgtor öffnen?"). Longer endings come first.
_CONFIRMATION_INFINITIVES: tuple[tuple[str, str], ...] = (
    ("unscharf geschaltet", "unscharf schalten"),
    ("scharf geschaltet", "scharf schalten"),
    ("wird geöffnet", "öffnen"),
    ("wird geschlossen", "schließen"),
    ("aufgeschlossen", "aufschließen"),
    ("abgeschlossen", "abschließen"),
    ("eingeschaltet", "einschalten"),
    ("ausgeschaltet", "ausschalten"),
    ("geschlossen", "schließen"),
    ("ausgeführt", "ausführen"),
    ("geöffnet", "öffnen"),
    ("gedrückt", "drücken"),
    ("aktiviert", "aktivieren"),
    ("gestartet", "starten"),
    ("gestoppt", "stoppen"),
)


def with_effect_summary(text: str, execution: Any) -> str:
    """„Gute Nacht ausgeführt: 9 Rollläden.“ after a script/scene/group."""
    effects = getattr(getattr(execution, "decision", None), "effects", None)
    summary = summarize_effects(effects) if effects is not None else None
    if not summary or not text:
        return text
    return f"{text.rstrip().rstrip('.')}: {summary}."


def confirmation_question(response_text: str, note: str | None = None) -> str:
    """Turn a device response text into a grammatical safety question.

    ``note`` is the policy's hint (an unverifiable script step, possible
    follow-up automations); it is said before the question.
    """
    text = response_text.rstrip(".")
    for ending, infinitive in _CONFIRMATION_INFINITIVES:
        if text.endswith(f" {ending}"):
            text = f"{text[: -len(ending)]}{infinitive}"
            break
    question = f"Soll ich wirklich {text}?"
    return f"{note} {question}" if note else question


def ambiguous_reading_text(decision: Any) -> str:
    """Two readings with different effects and no evidence: ask, run nothing."""
    names = sorted({
        entity_id for candidate in decision.chosen for entity_id in candidate.targets
    })
    listed = ", ".join(names[:5])
    return (
        f"Das kann ich unterschiedlich verstehen ({listed}). "
        "Bitte sag genauer, was ich tun soll. Ich habe nichts ausgeführt."
    )


def execution_failure_text(execution: Any) -> str:
    """A refusal of the policy is said as it is; a technical error gets a prefix."""
    error = str(execution.error or "")
    decision = getattr(execution, "decision", None)
    if error == CHANGED_SINCE_CONFIRMATION or (
        decision is not None and decision.outcome is PolicyOutcome.DENY
    ):
        return error
    return f"Fehler beim Ausführen: {error}"
