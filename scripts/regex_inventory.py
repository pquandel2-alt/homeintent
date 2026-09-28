#!/usr/bin/env python3
"""Inventory and classification of every regular expression (7.5.x).

Every literal pattern passed to ``re.compile``/``re.search``/… in the
integration is found via the AST and classified once:

* ``LEXICAL`` – word lists and alternations (surface forms of a lexeme);
* ``MORPHOLOGICAL`` – stems with inflection or compound endings;
* ``STRUCTURAL`` – tokenisation, punctuation, numbers, whitespace, symbols;
* ``SEMANTIC_SENTENCE_PATTERN`` – a German sentence frame (several words in
  sequence with a slot, anchor or wildcard): these encode meaning as
  sentences and are reduced release by release.

The classification lives in ``docs/regex-klassifikation.json``; a test keeps
it in sync with the code and fails when the number of
``SEMANTIC_SENTENCE_PATTERN`` entries grows beyond the recorded maximum.
Entries marked ``"quelle": "manuell"`` keep their class on regeneration.

    python scripts/regex_inventory.py            # report
    python scripts/regex_inventory.py --write    # regenerate the file
"""

from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "homeintent"
DATA = ROOT / "docs" / "regex-klassifikation.json"
CLASSES = ("LEXICAL", "MORPHOLOGICAL", "STRUCTURAL", "SEMANTIC_SENTENCE_PATTERN")
_FUNCTIONS = {"compile", "search", "match", "fullmatch", "sub", "subn", "findall", "finditer", "split"}


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None and right is not None:
            return left + right
        if left is not None or right is not None:
            return (left or "{}") + (right or "{}")
    return None


class _Collector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.found: list[tuple[str, str, int]] = []

    def _scoped(self, node, name):
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):  # noqa: N802
        self._scoped(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_ClassDef(self, node):  # noqa: N802
        self._scoped(node, node.name)

    def visit_Assign(self, node):  # noqa: N802
        target = node.targets[0]
        name = target.id if isinstance(target, ast.Name) else None
        if name and not self.scope:
            self.scope.append(name)
            self.generic_visit(node)
            self.scope.pop()
        else:
            self.generic_visit(node)

    def visit_Call(self, node):  # noqa: N802
        func = node.func
        if (
            isinstance(func, ast.Attribute) and func.attr in _FUNCTIONS
            and isinstance(func.value, ast.Name) and func.value.id == "re" and node.args
        ):
            pattern = _literal(node.args[0])
            if pattern is not None:
                self.found.append((".".join(self.scope) or "<module>", pattern, node.lineno))
        self.generic_visit(node)


def inventory() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        collector = _Collector(rel)
        collector.visit(ast.parse(path.read_text(encoding="utf-8")))
        seen: collections.Counter = collections.Counter()
        for scope, pattern, line in collector.found:
            digest = hashlib.sha1(pattern.encode()).hexdigest()[:10]
            key = f"{rel}::{scope}::{digest}"
            seen[key] += 1
            if seen[key] > 1:
                key += f"#{seen[key]}"
            entries.append({"id": key, "datei": rel, "bereich": scope, "zeile": line, "muster": pattern})
    return entries


_ESCAPES = re.compile(r"\\[a-zA-Z]")
_WORD = re.compile(r"[a-zäöüß]{3,}", re.I)
_SEQUENCE = re.compile(
    r"[a-zäöüß]{2,}\)?[?*]?(?:\\s[+*?]|\\s\{\d+(?:,\d*)?\}| )\(?(?:\?:)?[a-zäöüß(]", re.I
)
_SLOT = re.compile(r"\(\?P<\w+>\.|\.\+|\.\*|\\w\+|\\S\+")
_SUFFIX = re.compile(r"[a-zäöüß]\(\?:(?:[a-zäöüß]{1,3}\|)+[a-zäöüß]{0,3}\)\??|[a-zäöüß]\\w\*|[a-zäöüß]\[a-z", re.I)


def classify(pattern: str) -> str:
    words = _WORD.findall(_ESCAPES.sub(" ", pattern))
    if not words:
        return "STRUCTURAL"
    sequences = len(_SEQUENCE.findall(pattern))
    anchored = pattern.lstrip("(?i)").startswith("^") or pattern.rstrip().endswith("$")
    if sequences >= 2 or (sequences >= 1 and (_SLOT.search(pattern) or anchored)):
        return "SEMANTIC_SENTENCE_PATTERN"
    if _SUFFIX.search(pattern) and len(set(word.casefold() for word in words)) <= 4:
        return "MORPHOLOGICAL"
    return "LEXICAL"


def build(previous: dict[str, dict[str, object]] | None = None) -> dict[str, object]:
    previous = previous or {}
    entries = []
    for item in inventory():
        old = previous.get(str(item["id"]))
        manual = old is not None and old.get("quelle") == "manuell"
        entries.append({
            "id": item["id"],
            "datei": item["datei"],
            "bereich": item["bereich"],
            "klasse": old["klasse"] if manual else classify(str(item["muster"])),
            "quelle": "manuell" if manual else "heuristik",
        })
    counts = collections.Counter(str(entry["klasse"]) for entry in entries)
    return {"eintraege": entries, "anzahl": {name: counts.get(name, 0) for name in CLASSES}}


def load() -> dict[str, object]:
    return json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--release")
    args = parser.parse_args()
    current = load()
    previous = {str(entry["id"]): entry for entry in current.get("eintraege", [])}
    built = build(previous)
    counts = built["anzahl"]
    print(json.dumps(counts, ensure_ascii=False))
    if args.write:
        history = dict(current.get("verlauf", {}))
        if args.release:
            history[args.release] = counts["SEMANTIC_SENTENCE_PATTERN"]  # type: ignore[index]
        maximum = min(
            [int(value) for value in history.values()] + [int(counts["SEMANTIC_SENTENCE_PATTERN"])]  # type: ignore[index]
        ) if history else counts["SEMANTIC_SENTENCE_PATTERN"]  # type: ignore[index]
        payload = {
            "beschreibung": "Klassifikation aller Regex-Muster (scripts/regex_inventory.py)",
            "maximum_semantic_sentence_pattern": maximum,
            "verlauf": history,
            **built,
        }
        DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
