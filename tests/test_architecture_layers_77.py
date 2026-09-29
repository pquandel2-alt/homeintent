"""7.7 B4: dependency direction as an architecture rule (static, AST).

    HA adapter/conversation → controllers → meaning/domain → grounding
      → plans → policy/executor

Forbidden, so the layers cannot grow back together:
- language understanding (``nlu/``) importing the conversation or a controller,
- policy/executor importing a parser, the engine or ``nlu/``,
- target resolution importing a controller, the conversation or the engine,
- learning calling the executor or writing services itself,
- a controller importing the conversation,
- the arbiter importing policy or executor (it decides meaning only),
- the engine importing policy, executor, dialog management or a controller.
Only the listed modules may call ``hass.services.async_call``; device writes
only through ``service_executor``.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent / "custom_components" / "homeintent"


def _module(path: Path) -> str:
    return "/".join(path.relative_to(ROOT).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    package = path.relative_to(ROOT).parent.parts
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
        if node.module:
            found.add("/".join([*base, *node.module.split(".")]))
        else:
            found.update("/".join([*base, alias.name]) for alias in node.names)
    return found


MODULES = {
    _module(path): _imports(path)
    for path in ROOT.rglob("*.py") if "__pycache__" not in path.parts
}
POLICY = {"execution_policy", "service_executor", "effect_graph", "risk", "plan_origin"}
PARSERS = {
    "parsers", "engine", "automation_language", "notification_language",
}
GROUNDING = {"nlu/target_resolution", "entity_scope", "entities", "nlu/place_model", "nlu/device_ontology"}
LEARNING = {
    "learning_manager", "learning_policy", "learning_control", "learning_intent", "learning_dialog",
    "dialog_learning", "alias_learning", "bindings", "habit_discovery", "model_registry",
    "experience", "experience_store",
}
SERVICE_CALLERS = {
    "service_executor",  # the one device write path
    "automation_executor",  # HomeIntent's own automations (system context)
    "agent_delivery", "proactive_runtime", "agent_runtime",  # notifications / announcements
    "native_timer",  # timer signal tone
    "history_query",  # recorder reads
    "controllers/productivity",  # todo, timer, calendar entities
    "controllers/goals",  # failure notification of a background plan
}


def _violations(source, target) -> list[str]:
    return sorted(
        f"{name} -> {imported}"
        for name, imported_set in MODULES.items() if source(name)
        for imported in imported_set if target(imported)
    )


def test_language_understanding_never_imports_conversation_or_controllers():
    assert _violations(
        lambda name: name.startswith("nlu/"),
        lambda target: target == "conversation" or target.startswith("controllers"),
    ) == []


def test_policy_and_executor_never_import_parsers_or_language():
    assert _violations(
        lambda name: name in POLICY,
        lambda target: target in PARSERS or target.startswith("nlu/")
        or target == "conversation" or target.startswith("controllers"),
    ) == []


def test_target_resolution_never_imports_controllers():
    assert _violations(
        lambda name: name in GROUNDING,
        lambda target: target in {"conversation", "engine"} or target.startswith("controllers"),
    ) == []


def test_learning_never_calls_the_executor():
    assert _violations(lambda name: name in LEARNING, lambda target: target == "service_executor") == []


def test_controllers_never_import_the_conversation():
    assert _violations(
        lambda name: name.startswith("controllers/"), lambda target: target == "conversation",
    ) == []


def test_arbiter_never_imports_policy_or_executor():
    assert _violations(
        lambda name: name in {"arbitration", "arbitration_candidates"},
        lambda target: target in {"execution_policy", "service_executor"},
    ) == []


def test_engine_has_no_policy_executor_or_dialog_management():
    assert _violations(
        lambda name: name == "engine",
        lambda target: target in {"execution_policy", "service_executor", "dialog_manager", "conversation"}
        or target.startswith("controllers"),
    ) == []


def test_only_listed_modules_call_home_assistant_services():
    callers = set()
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "async_call"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "services"
            ):
                callers.add(_module(path))
    assert callers <= SERVICE_CALLERS, sorted(callers - SERVICE_CALLERS)


def test_conversation_is_orchestration_sized():
    lines = (ROOT / "conversation.py").read_text(encoding="utf-8").count("\n")
    assert lines <= 2500
    oversized = {
        _module(path): count
        for path in (ROOT / "controllers").glob("*.py")
        if (count := path.read_text(encoding="utf-8").count("\n")) > 1500
    }
    assert oversized == {}
