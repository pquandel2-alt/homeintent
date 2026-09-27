"""Release 7.3.0 safety regressions S1-S7 against the stub test house.

Each test drives the real conversation entity (``tests/_testhaus.py``) with
sentences of its own; the live acceptance corpora in ``sim/`` are holdouts
and are deliberately not reused here.
"""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation, house_entities, with_states


# --- S1: "lass X an/zu/offen" keeps a state and never operates -------------

@pytest.mark.parametrize("text", [
    "Lass das Licht in der Küche an.",
    "Lass die Stehlampe bitte an.",
    "Lasst das Bürolicht an.",
    "Kannst du die Schreibtischlampe anlassen?",
    "Lass das Küchenfenster offen.",
    "Lass den Rollladen im Schlafzimmer oben.",
    "Lass die Markise zu.",
    "Lass das Radio in der Küche laufen.",
])
def test_s1_maintain_never_calls_a_service(monkeypatch, text):
    house = HouseConversation(monkeypatch, with_states(
        house_entities(), light__kuechenlicht="on", light__stehlampe="on",
    ))
    turn = house.say(text)
    assert turn.calls == []
    assert "ändere nichts" in turn.speech


def test_s1_herunterlassen_remains_an_operation(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Lass den Rollladen im Schlafzimmer runter.")
    assert turn.targets == {"cover.schlafzimmer_rollladen"}
