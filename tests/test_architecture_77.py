"""7.7: architecture rules (static, AST).

- Meaning IR and arbitration read public semantic primitives only; they
  never import a historic compiler's private helper (B2).
- No module in ``nlu/`` imports a private name of another module.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent / "custom_components" / "homeintent"
NLU = ROOT / "nlu"
# Compilers whose results the IR must not reconstruct meaning from.
LEGACY_COMPILERS = frozenset({
    "ontology_compiler", "semantic_compiler", "discourse_compiler", "semantic_interpreter",
    "semantic_projection", "parser", "parsers", "engine", "query_executor",
})


def _imports(path: Path) -> list[tuple[str, tuple[str, ...], int]]:
    found: list[tuple[str, tuple[str, ...], int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").rsplit(".", 1)[-1]
            found.append((module, tuple(alias.name for alias in node.names), node.lineno))
    return found


def test_meaning_ir_uses_public_primitives_only():
    offenders = []
    for name in ("meaning_ir.py", "clause_reading.py"):
        for module, names, line in _imports(NLU / name):
            if module in LEGACY_COMPILERS:
                offenders.append(f"{name}:{line} imports {module}")
            offenders += [
                f"{name}:{line} imports private {module}.{item}"
                for item in names if item.startswith("_") and item != "__future__"
            ]
    assert offenders == []


def test_nlu_modules_import_no_private_names():
    offenders = []
    for path in sorted(NLU.glob("*.py")):
        for module, names, line in _imports(path):
            if module == "__future__":
                continue
            offenders += [
                f"{path.name}:{line} {module}.{item}"
                for item in names if item.startswith("_")
            ]
    assert offenders == []
