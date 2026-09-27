"""F18: grammar files are loaded at setup (executor), never inside a turn."""

from __future__ import annotations


def test_warm_up_preloads_every_grammar_a_live_turn_can_touch(monkeypatch):
    """F18: no YAML file is opened lazily inside a conversation turn."""
    from homeintent.engine import NluEngine
    from homeintent.entities import EntitySnapshot

    engine = NluEngine()
    engine.warm_up()

    def _fail(*_args, **_kwargs):
        raise AssertionError("grammar loaded during a live turn")

    monkeypatch.setattr("homeintent.engine.Intents.from_files", _fail)
    light = EntitySnapshot("light.x", "Leselampe", "light", "on", capabilities=frozenset({"TURN_ON"}))
    # A percentage command the engine cannot execute reaches the feedback path.
    engine.understand("Stelle die Gartenlaterne auf 30 Prozent", [light])
