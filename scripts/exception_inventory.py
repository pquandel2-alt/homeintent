#!/usr/bin/env python3
"""Inventory broad ``except Exception``/``BaseException`` handlers.

The checked-in inventory makes every broad boundary explicit.  The check
fails for additions, removals, moved handlers, changed handler bodies,
missing classifications, or a count above the approved ceiling.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "custom_components" / "homeintent"
INVENTORY_PATH = ROOT / "docs" / "except-exception-inventory.json"
HISTORICAL_COMMIT = "eb07317a789e5014cc71cd90d944c21f79d27a03"
HISTORICAL_EXCEPTION_COUNT = 116
HISTORICAL_BASE_EXCEPTION_COUNT = 2
APPROVED_EXCEPTION_MAXIMUM = 119
APPROVED_BASE_EXCEPTION_MAXIMUM = 2
APPROVED_ADDITION_IDS = frozenset(
    {
        (
            "custom_components/homeintent/event_runtime_scheduler.py::"
            "LongRunningScheduler.initialize::1"
        ),
        (
            "custom_components/homeintent/event_runtime_scheduler.py::"
            "LongRunningScheduler._timer_fired::1"
        ),
        (
            "custom_components/homeintent/event_runtime_supervisor.py::"
            "EventWorkerSupervisor.run::1"
        ),
    }
)

CLASSIFICATIONS = frozenset(
    {
        "BOUNDARY_REQUIRED",
        "NARROWABLE",
        "RETHROW",
        "FALLBACK",
        "ROLLBACK",
        "POTENTIALLY_SILENT",
    }
)


@dataclass(frozen=True)
class BroadHandler:
    """One broad handler with a stable scope-relative identity."""

    handler_id: str
    path: str
    line: int
    scope: str
    ordinal: int
    caught: str
    body_digest: str
    source: str
    body: str
    raises: bool


def _broad_exception_kind(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id if node.id in {"Exception", "BaseException"} else None
    if isinstance(node, ast.Tuple):
        kinds = {_broad_exception_kind(item) for item in node.elts}
        kinds.discard(None)
        if "BaseException" in kinds:
            return "BaseException"
        if "Exception" in kinds:
            return "Exception"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self._path = path
        self._source = source
        self._scopes: list[str] = []
        self._ordinals: dict[str, int] = {}
        self.handlers: list[BroadHandler] = []

    def _visit_scope(self, node: ast.AST, name: str) -> None:
        self._scopes.append(name)
        self.generic_visit(node)
        self._scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        caught = _broad_exception_kind(node.type)
        if caught is not None:
            relative = self._path.relative_to(ROOT).as_posix()
            scope = ".".join(self._scopes) or "<module>"
            ordinal = self._ordinals.get(scope, 0) + 1
            self._ordinals[scope] = ordinal
            body = "\n".join(
                ast.get_source_segment(self._source, item) or ast.dump(item)
                for item in node.body
            )
            source = ast.get_source_segment(self._source, node) or "except Exception"
            digest = hashlib.sha256(ast.dump(node).encode()).hexdigest()[:16]
            handler_id = f"{relative}::{scope}::{ordinal}"
            self.handlers.append(
                BroadHandler(
                    handler_id,
                    relative,
                    node.lineno,
                    scope,
                    ordinal,
                    caught,
                    digest,
                    source.splitlines()[0].strip(),
                    body,
                    any(
                        isinstance(item, ast.Raise)
                        for statement in node.body
                        for item in ast.walk(statement)
                    ),
                )
            )
        self.generic_visit(node)


def scan_handlers() -> list[BroadHandler]:
    """Return every broad integration handler in deterministic order."""
    found: list[BroadHandler] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        visitor = _Visitor(path, source)
        visitor.visit(ast.parse(source, filename=str(path)))
        found.extend(visitor.handlers)
    return found


def _contains_raise(handler: BroadHandler) -> bool:
    return handler.raises


def _bootstrap_classification(handler: BroadHandler) -> str:
    """Provide a conservative first classification for human review."""
    body = handler.body.lower()
    source = handler.source.lower()
    scope = handler.scope.lower()
    path = handler.path.lower()
    if body.strip() == "pass":
        return "POTENTIALLY_SILENT"
    if "rollback" in body or "roll back" in body:
        return "ROLLBACK"
    if _contains_raise(handler):
        return "RETHROW"
    if any(
        token in body or token in source
        for token in (
            "best-effort",
            "best effort",
            "optional",
            "unavailable",
            "cosmetic",
            "advisory",
            "return none",
            "return []",
            "return {}",
            "continue",
        )
    ):
        return "FALLBACK"
    if "/controllers/" in path or any(
        token in path
        for token in (
            "event_runtime.py",
            "proactive_runtime.py",
            "agent_delivery.py",
            "service_executor.py",
            "history_query.py",
        )
    ):
        return "BOUNDARY_REQUIRED"
    if "return" in body or "_logger" in body:
        return "FALLBACK"
    if "_write" in scope:
        return "RETHROW"
    return "NARROWABLE"


def _reason(classification: str) -> str:
    return {
        "BOUNDARY_REQUIRED": (
            "Heterogene Home-Assistant-/Providergrenze; Fehler wird lokal "
            "protokolliert, gemessen oder in einen stabilen Vertrag übersetzt."
        ),
        "NARROWABLE": (
            "Keine dauerhaft heterogene Boundary belegt; nach zusätzlicher "
            "Charakterisierung auf konkrete Ausnahmearten verengen."
        ),
        "RETHROW": (
            "Bereinigung oder Diagnose erfolgt vor unverändertem erneutem Werfen."
        ),
        "FALLBACK": (
            "Dokumentierter Best-Effort-/Unavailable-Fallback ohne Schreibfreigabe."
        ),
        "ROLLBACK": (
            "Transaktionaler Fehlerpfad stellt Zustand wieder her; der "
            "ursprüngliche Fehler bleibt maßgeblich."
        ),
        "POTENTIALLY_SILENT": (
            "Breiter Handler ohne ausreichende Instrumentierung; priorisiert härten."
        ),
    }[classification]


def inventory_document(handlers: list[BroadHandler]) -> dict[str, object]:
    """Build a reviewable bootstrap document from the current source."""
    rows = []
    for handler in handlers:
        classification = _bootstrap_classification(handler)
        rows.append(
            {
                "id": handler.handler_id,
                "path": handler.path,
                "line": handler.line,
                "scope": handler.scope,
                "caught": handler.caught,
                "classification": classification,
                "reason": _reason(classification),
                "body_digest": handler.body_digest,
            }
        )
    return {
        "schema_version": 2,
        "historical_baseline": {
            "commit": HISTORICAL_COMMIT,
            "exception_count": HISTORICAL_EXCEPTION_COUNT,
            "base_exception_count": HISTORICAL_BASE_EXCEPTION_COUNT,
        },
        "approved_exception_maximum": APPROVED_EXCEPTION_MAXIMUM,
        "approved_base_exception_maximum": APPROVED_BASE_EXCEPTION_MAXIMUM,
        "approved_additions": [
            {
                "id": (
                    "custom_components/homeintent/event_runtime_scheduler.py::"
                    "LongRunningScheduler.initialize::1"
                ),
                "reason": "Asynchrone Initialisierung über heterogene HA-State-Payloads.",
            },
            {
                "id": (
                    "custom_components/homeintent/event_runtime_scheduler.py::"
                    "LongRunningScheduler._timer_fired::1"
                ),
                "reason": "Synchroner HA-Timer-Callback muss Schedulerfehler isolieren.",
            },
            {
                "id": (
                    "custom_components/homeintent/event_runtime_supervisor.py::"
                    "EventWorkerSupervisor.run::1"
                ),
                "reason": "Worker-Boundary übersetzt normale Laufzeitfehler in den Circuit-Zustand.",
            },
        ],
        "handlers": rows,
    }


def _errors(document: dict[str, object], actual: list[BroadHandler]) -> Iterator[str]:
    historical = document.get("historical_baseline")
    if historical != {
        "commit": HISTORICAL_COMMIT,
        "exception_count": HISTORICAL_EXCEPTION_COUNT,
        "base_exception_count": HISTORICAL_BASE_EXCEPTION_COUNT,
    }:
        yield "historical v7.9.8 baseline metadata changed"
    if document.get("approved_exception_maximum") != APPROVED_EXCEPTION_MAXIMUM:
        yield f"approved_exception_maximum must remain {APPROVED_EXCEPTION_MAXIMUM}"
    if document.get("approved_base_exception_maximum") != APPROVED_BASE_EXCEPTION_MAXIMUM:
        yield (
            "approved_base_exception_maximum must remain "
            f"{APPROVED_BASE_EXCEPTION_MAXIMUM}"
        )
    additions = document.get("approved_additions")
    addition_ids: set[str] = set()
    if isinstance(additions, list):
        for row in additions:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                addition_ids.add(row["id"])
    if addition_ids != APPROVED_ADDITION_IDS:
        yield "approved Phase-1 additions changed"
    if (
        APPROVED_EXCEPTION_MAXIMUM
        != HISTORICAL_EXCEPTION_COUNT + len(APPROVED_ADDITION_IDS)
    ):
        yield "approved maximum no longer matches baseline plus reviewed additions"
    exception_count = sum(handler.caught == "Exception" for handler in actual)
    base_exception_count = sum(handler.caught == "BaseException" for handler in actual)
    if exception_count > APPROVED_EXCEPTION_MAXIMUM:
        yield (
            f"except Exception count {exception_count} exceeds "
            f"{APPROVED_EXCEPTION_MAXIMUM}"
        )
    if base_exception_count > APPROVED_BASE_EXCEPTION_MAXIMUM:
        yield (
            f"except BaseException count {base_exception_count} exceeds "
            f"{APPROVED_BASE_EXCEPTION_MAXIMUM}"
        )
    rows = document.get("handlers")
    if not isinstance(rows, list):
        yield "handlers must be a list"
        return
    expected: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            yield "inventory row must be an object"
            continue
        handler_id = row.get("id")
        if not isinstance(handler_id, str):
            yield "inventory row has no string id"
            continue
        if handler_id in expected:
            yield f"duplicate handler id in inventory: {handler_id}"
        expected[handler_id] = row
    observed = {handler.handler_id: handler for handler in actual}
    for handler_id in sorted(observed.keys() - expected.keys()):
        yield f"uninventoried broad handler: {handler_id}"
    for handler_id in sorted(expected.keys() - observed.keys()):
        yield f"stale inventory row: {handler_id}"
    for handler_id in sorted(expected.keys() & observed.keys()):
        row = expected[handler_id]
        handler = observed[handler_id]
        classification = row.get("classification")
        if classification not in CLASSIFICATIONS:
            yield f"invalid classification for {handler_id}: {classification!r}"
        elif classification == "POTENTIALLY_SILENT":
            yield f"potentially silent broad handler remains: {handler_id}"
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            yield f"missing reason for {handler_id}"
        if row.get("line") != handler.line:
            yield f"line drift for {handler_id}: {row.get('line')} -> {handler.line}"
        if row.get("caught") != handler.caught:
            yield f"caught exception drift for {handler_id}"
        if row.get("body_digest") != handler.body_digest:
            yield f"handler changed without review: {handler_id}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-bootstrap", action="store_true")
    args = parser.parse_args()
    handlers = scan_handlers()
    if args.write_bootstrap:
        INVENTORY_PATH.write_text(
            json.dumps(inventory_document(handlers), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if not INVENTORY_PATH.exists():
        print(f"missing inventory: {INVENTORY_PATH.relative_to(ROOT)}")
        return 1
    document = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    errors = list(_errors(document, handlers))
    counts = {classification: 0 for classification in sorted(CLASSIFICATIONS)}
    for row in document.get("handlers", []):
        if isinstance(row, dict) and row.get("classification") in counts:
            counts[str(row["classification"])] += 1
    exception_count = sum(handler.caught == "Exception" for handler in handlers)
    base_exception_count = sum(handler.caught == "BaseException" for handler in handlers)
    print(
        f"except Exception: {exception_count}/{APPROVED_EXCEPTION_MAXIMUM}; "
        f"except BaseException: {base_exception_count}/{APPROVED_BASE_EXCEPTION_MAXIMUM}; "
        + ", ".join(f"{key}={value}" for key, value in counts.items())
    )
    for error in errors:
        print(f"ERROR: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
