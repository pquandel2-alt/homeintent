"""Count regular-expression call sites in the HomeIntent integration.

Usage: python scripts/count_regex_usage.py [--by-file] [--max N]

A "use" is one syntactic call of ``re.compile``/``re.search``/``re.match``/
``re.fullmatch``/``re.sub``/``re.subn``/``re.findall``/``re.finditer``/
``re.split`` in a Python source file of ``custom_components/homeintent``.
The number is tracked across releases (7.2.1: 854) because every raw-text
regex outside the shared language model is a place where lexicon and
ontology knowledge cannot arrive.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "homeintent"
_FUNCTIONS = frozenset({
    "compile", "search", "match", "fullmatch", "sub", "subn",
    "findall", "finditer", "split",
})


def count_file(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _FUNCTIONS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--by-file", action="store_true")
    parser.add_argument("--max", type=int, default=None, help="fail above this total")
    args = parser.parse_args()
    counts = {
        path.relative_to(ROOT).as_posix(): count_file(path)
        for path in sorted(ROOT.rglob("*.py"))
    }
    total = sum(counts.values())
    if args.by_file:
        for name, value in sorted(counts.items(), key=lambda item: -item[1]):
            if value:
                print(f"{value:5d}  {name}")
    print(f"total {total}")
    if args.max is not None and total > args.max:
        print(f"regex budget exceeded: {total} > {args.max}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
