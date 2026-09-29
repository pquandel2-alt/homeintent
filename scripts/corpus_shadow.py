#!/usr/bin/env python3
"""Engine and meaning-IR signatures of every corpus sentence (7.7).

Compares an old state (a git worktree, ``--root``) with the working tree
without keeping legacy code in the tree: both dumps are computed over the
*same* sentence set (the corpus of ``scripts/shadow_compare.py`` plus every
sentence-like literal of the test suite), each from its own source tree.

    git worktree add /tmp/old <commit>
    python scripts/corpus_shadow.py --root /tmp/old --dump old.json
    python scripts/corpus_shadow.py --dump new.json
    python scripts/corpus_shadow.py --compare old.json new.json

Per sentence it records the behaviour signature of ``NluEngine.understand``
(writes, targets, domains, risk, confirmation, response) and the grounded
meaning IR (operation, targets, value, time, residue per clause). Nothing
is executed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent


def _sentences() -> list[str]:
    sys.path.insert(0, str(SCRIPT_ROOT / "scripts"))
    from shadow_compare import corpus  # noqa: E402 - current tree's corpus

    texts = [text for _source, text in corpus()]
    seen = set(texts)
    for path in sorted((SCRIPT_ROOT / "tests").glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                words = text.split()
                if (
                    3 <= len(words) <= 25 and text[:1].isupper() and text[-1:] in ".?!"
                    and "\n" not in text and text not in seen
                ):
                    seen.add(text)
                    texts.append(text)
    return texts


def _dump(root: Path, sentences: list[str]) -> dict[str, object]:
    sys.path.insert(0, str(root / "custom_components"))
    sys.path.insert(0, str(SCRIPT_ROOT / "tests"))
    import _ha_stub

    _ha_stub.install()
    from _testhaus import house_entities
    from homeintent.engine import NluEngine
    from homeintent.nlu.language_frontend import analyse_language
    from homeintent.nlu.meaning_ir import ground_meaning
    from homeintent.shadow_runtime import signature_for

    engine = NluEngine()
    entities = house_entities()
    result: dict[str, object] = {}
    for text in sentences:
        try:
            document = analyse_language(text, entities)
            payload = engine.understand(text, entities, document=document).payload
            signature = signature_for(payload, entities)
            engine_repr = repr(signature)
        except Exception as err:  # noqa: BLE001 - recorded, compared like a result
            engine_repr = f"error:{type(err).__name__}"
        try:
            meaning = ground_meaning(analyse_language(text, entities), entities)
            ir_repr = repr([
                (sorted(clause.operation), clause.targets, sorted(clause.value.items(), key=str),
                 clause.time, clause.residue, clause.conditions, clause.exceptions, clause.origin)
                for clause in meaning.clauses
            ])
        except Exception as err:  # noqa: BLE001
            ir_repr = f"error:{type(err).__name__}"
        result[text] = {"engine": engine_repr, "ir": ir_repr}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=SCRIPT_ROOT)
    parser.add_argument("--dump", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path)
    parser.add_argument("--sentences", type=Path, help="internal: sentence list file")
    args = parser.parse_args()
    if args.compare:
        old = json.loads(args.compare[0].read_text(encoding="utf-8"))
        new = json.loads(args.compare[1].read_text(encoding="utf-8"))
        common = sorted(set(old) & set(new))
        diffs = {key: [text for text in common if old[text][key] != new[text][key]] for key in ("engine", "ir")}
        print(f"{len(common)} Sätze; Engine-Abweichungen {len(diffs['engine'])}, IR-Abweichungen {len(diffs['ir'])}")
        for key, texts in diffs.items():
            for text in texts[:15]:
                print(f"  [{key}] {text}\n    alt: {old[text][key][:300]}\n    neu: {new[text][key][:300]}")
        return 1 if any(diffs.values()) else 0
    if args.sentences is None:
        # The sentence set always comes from the current tree; the dump
        # runs in a fresh process with the requested root.
        listing = args.dump.with_suffix(".sentences.json")
        listing.write_text(json.dumps(_sentences(), ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        return subprocess.call(
            [sys.executable, __file__, "--root", str(args.root), "--dump", str(args.dump),
             "--sentences", str(listing)],
            env=env,
        )
    sentences = json.loads(args.sentences.read_text(encoding="utf-8"))
    data = _dump(args.root.resolve(), sentences)
    args.dump.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(data)} Sätze -> {args.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
