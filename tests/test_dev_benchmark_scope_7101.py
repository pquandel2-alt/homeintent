"""7.10.1 regression coverage for the scoped benchmark harness."""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation
from homeassistant.components.conversation import ConversationInput
from homeintent.conversation_scope import (
    conversation_scope_key_for_input,
    reusable_conversation_scope_key,
    scope_conversation_input,
)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dev_benchmark  # noqa: E402


def _input(
    *,
    user: str | None = None,
    satellite: str | None = None,
    device: str | None = None,
    conversation_id: str | None = "shared",
) -> ConversationInput:
    return ConversationInput(
        text="Test",
        conversation_id=conversation_id,
        context=types.SimpleNamespace(user_id=user) if user else None,
        satellite_id=satellite,
        device_id=device,
    )


def test_public_scope_helper_is_the_same_boundary_used_by_production():
    for user_input in (
        _input(user="admin"),
        _input(satellite="satellite.kueche"),
        _input(device="device-tablet"),
        _input(),
    ):
        scoped, public_id = scope_conversation_input(user_input)
        assert public_id == "shared"
        assert scoped.conversation_id == conversation_scope_key_for_input(user_input)
        assert scoped.conversation_id.startswith("homeintent:")
        assert scoped.conversation_id != public_id


def test_principals_with_the_same_public_id_have_distinct_scopes():
    keys = {
        conversation_scope_key_for_input(_input(user="admin")),
        conversation_scope_key_for_input(_input(user="anna")),
        conversation_scope_key_for_input(_input(satellite="satellite.kueche")),
        conversation_scope_key_for_input(_input(satellite="satellite.flur")),
        conversation_scope_key_for_input(_input(device="device-tablet")),
        conversation_scope_key_for_input(_input()),
    }
    assert len(keys) == 6


def test_an_unbound_anonymous_turn_has_no_reusable_scope():
    user_input = _input(conversation_id=None)
    assert reusable_conversation_scope_key(user_input) is None
    first, _ = scope_conversation_input(user_input)
    second, _ = scope_conversation_input(user_input)
    assert first.conversation_id != second.conversation_id


def test_benchmark_observes_confirmation_draft_clarification_and_multiturn():
    runner = dev_benchmark.Runner()
    service = runner.run_case(dev_benchmark.Case(
        1, "test", False, ["Schließ die Haustür ab", "Ja"],
        [dev_benchmark.parse_expectation("confirm"), dev_benchmark.parse_expectation("execute")],
    ))
    automation = runner.run_case(dev_benchmark.Case(
        2, "test", False,
        ["Wenn die Haustür aufgeht, schalte das Flurlicht an", "Ja"],
        [dev_benchmark.parse_expectation("draft"), dev_benchmark.parse_expectation("execute")],
    ))
    clarification = runner.run_case(dev_benchmark.Case(
        3, "test", False, ["Mach das Licht an"],
        [dev_benchmark.parse_expectation("clarify")],
    ))
    assert [turn.outcome for turn in service] == ["confirm", "execute"]
    assert [turn.outcome for turn in automation] == ["draft", "execute"]
    assert [turn.outcome for turn in clarification] == ["clarify"]


def test_user_change_cannot_confirm_another_users_public_session(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.conversation_id = "shared-confirmation"
    assert not house.say("Schließ die Haustür ab").calls
    house.user = "anna"
    assert not house.say("Ja").calls


def test_satellites_with_same_public_id_cannot_share_confirmation(monkeypatch):
    house = HouseConversation(monkeypatch, user=None)
    first = house.conversation_input(
        "Schließ die Haustür ab", satellite_id="satellite.kueche"
    )
    second = house.conversation_input(
        "Ja", satellite_id="satellite.flur"
    )
    assert not house.say_input(first).calls
    assert not house.say_input(second).calls


def test_benchmark_source_never_indexes_context_with_the_public_id():
    source = (ROOT / "scripts/dev_benchmark.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        argument = ast.unparse(node.args[0])
        if argument in {"self.house.conversation_id", "user_input.conversation_id"}:
            forbidden.append(argument)
    assert forbidden == []


def test_quality_gate_rejects_accuracy_and_safety_regressions(tmp_path):
    report = dev_benchmark.run([
        dev_benchmark.Case(
            1, "automation", False,
            ["Wenn die Haustür aufgeht, schalte das Flurlicht an"],
            [dev_benchmark.parse_expectation("draft")],
        )
    ])
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        '{"corpora":{"tests/eval/dev_benchmark_77.txt":{'
        '"exact":{"unsafe_execution_count":1},"minimum":{"passed":2}}}}',
        encoding="utf-8",
    )
    failures = dev_benchmark.check_quality_gates(
        report, ROOT / "tests/eval/dev_benchmark_77.txt", baseline
    )
    assert any("unsafe_execution_count" in failure for failure in failures)
    assert any("passed" in failure for failure in failures)
