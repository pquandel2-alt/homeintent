#!/usr/bin/env python3
"""Whole-conversation shadow: every multi-turn dialog, old tree vs new (7.7).

Runs the real ``NluConversationEntity`` on the stub test house through
every say-sequence of the live test bed (``sim/scenarios.py``; option
steps are applied, HA-side steps such as ``service``/``set``/``wait`` are
skipped), every dialog case (``tests/eval/dialog_cases.json``) and the
collision corpus, and records per turn the spoken answer and the service
calls. The old state runs from a git worktree (``--root``), so refactoring
the conversation (B3/B4) is measured without keeping legacy code:

    git worktree add /tmp/old <commit>
    python scripts/dialog_shadow.py --root /tmp/old --dump old.json
    python scripts/dialog_shadow.py --dump new.json
    python scripts/dialog_shadow.py --compare old.json new.json

Time is frozen and random ids are neutralised, so identical behaviour gives
identical dumps. Nothing is executed for real.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
FIXED_NOW = datetime(2026, 9, 29, 14, 30, tzinfo=timezone.utc)
_UUID = re.compile(r"\b[0-9a-f]{32}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _scenario_sequences() -> list[tuple[str, list[dict[str, object]]]]:
    """say/options steps of every scenario, read without importing sim."""
    tree = ast.parse((SCRIPT_ROOT / "sim" / "scenarios.py").read_text(encoding="utf-8"))
    constants = {
        target.id: node.value.value
        for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets if isinstance(target, ast.Name)
    }
    sequences: list[tuple[str, list[dict[str, object]]]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "S"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        steps: list[dict[str, object]] = []
        for step in node.args[3:]:
            if not (isinstance(step, ast.Call) and isinstance(step.func, ast.Name)):
                continue
            if step.func.id == "say" and step.args:
                first = step.args[0]
                text = first.value if isinstance(first, ast.Constant) else constants.get(getattr(first, "id", ""))
                if not isinstance(text, str):
                    continue
                keywords = {kw.arg: _literal(kw.value) for kw in step.keywords if kw.arg in {"user", "conv"}}
                steps.append({"say": text, "user": keywords.get("user") or "admin", "conv": keywords.get("conv")})
            elif step.func.id == "options":
                changes = {kw.arg: _literal(kw.value) for kw in step.keywords}
                steps.append({"options": changes})
        if any("say" in step for step in steps):
            sequences.append((f"scenario:{node.args[0].value}", steps))
    return sequences


def _corpus() -> list[tuple[str, list[dict[str, object]]]]:
    items = _scenario_sequences()
    for case in json.loads((SCRIPT_ROOT / "tests/eval/dialog_cases.json").read_text(encoding="utf-8")):
        turns = [turn for turn in case.get("turns", []) if isinstance(turn, str)]
        if turns:
            items.append((f"dialog:{case['id']}", [{"say": turn, "user": "admin"} for turn in turns]))
    for index, item in enumerate(json.loads((SCRIPT_ROOT / "tests/eval/collisions_v75.json").read_text(encoding="utf-8"))):
        items.append((f"collision:{index}", [{"say": item["text"], "user": "admin"}]))
    return items


def _neutral(value: object) -> object:
    if isinstance(value, str):
        return _UUID.sub("<id>", value)
    if isinstance(value, dict):
        return {str(_neutral(key)): _neutral(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_neutral(item) for item in value]
    return repr(value) if not isinstance(value, (int, float, bool, type(None))) else value


def _dump(root: Path, corpus: list[tuple[str, list[dict[str, object]]]]) -> dict[str, object]:
    sys.path.insert(0, str(root / "custom_components"))
    sys.path.insert(0, str(SCRIPT_ROOT / "tests"))
    import _ha_stub

    _ha_stub.install()
    logging.disable(logging.CRITICAL)
    from homeassistant.util import dt as dt_util

    dt_util.now = lambda *args, **kwargs: FIXED_NOW.astimezone()
    dt_util.utcnow = lambda: FIXED_NOW
    from _testhaus import PUSH_OPTIONS, HouseConversation

    class _MonkeyPatch:
        def setattr(self, target: object, name: str, value: object) -> None:
            setattr(target, name, value)

    result: dict[str, object] = {}
    for key, steps in corpus:
        with tempfile.TemporaryDirectory() as tmp:
            house = HouseConversation(_MonkeyPatch(), tmp_path=Path(tmp), options=dict(PUSH_OPTIONS))
            turns: list[object] = []
            for step in steps:
                if "options" in step:
                    house.entity.entry.options = {**house.entity.entry.options, **step["options"]}  # type: ignore[dict-item]
                    continue
                house.user = str(step.get("user") or "admin")
                house.conversation_id = str(step.get("conv") or "shadow")
                try:
                    turn = house.say(str(step["say"]))
                    turns.append([_neutral(turn.speech), _neutral(sorted(map(repr, turn.calls)))])
                except Exception as err:  # noqa: BLE001 - recorded like an answer
                    turns.append([f"error:{type(err).__name__}", []])
            result[key] = turns
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=SCRIPT_ROOT)
    parser.add_argument("--dump", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path)
    parser.add_argument("--corpus", type=Path, help="internal: corpus file")
    args = parser.parse_args()
    if args.compare:
        old = json.loads(args.compare[0].read_text(encoding="utf-8"))
        new = json.loads(args.compare[1].read_text(encoding="utf-8"))
        common = sorted(set(old) & set(new))
        changed = [key for key in common if old[key] != new[key]]
        turns = sum(len(old[key]) for key in common)
        print(f"{len(common)} Dialoge, {turns} Turns; abweichend: {len(changed)}")
        for key in changed[:25]:
            for index, (before, after) in enumerate(zip(old[key], new[key])):
                if before != after:
                    print(f"  {key} Turn {index + 1}\n    alt: {str(before)[:300]}\n    neu: {str(after)[:300]}")
                    break
        return 1 if changed else 0
    if args.corpus is None:
        listing = args.dump.with_suffix(".corpus.json")
        listing.write_text(json.dumps(_corpus(), ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "PYTHONHASHSEED": "0", "TZ": "Europe/Berlin"}
        return subprocess.call(
            [sys.executable, __file__, "--root", str(args.root), "--dump", str(args.dump),
             "--corpus", str(listing)],
            env=env,
        )
    corpus = [(key, steps) for key, steps in json.loads(args.corpus.read_text(encoding="utf-8"))]
    data = _dump(args.root.resolve(), corpus)
    args.dump.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(data)} Dialoge -> {args.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
