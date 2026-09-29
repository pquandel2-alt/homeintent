from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent.parent


def test_current_release_version_is_consistent_across_main_release_surfaces():
    manifest = json.loads(
        (ROOT / "custom_components/homeintent/manifest.json").read_text(encoding="utf-8")
    )
    version = manifest["version"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    baseline_name = f"corpus-signatures-{version}.json"
    baseline_path = ROOT / "docs/perf" / baseline_name

    assert f"Aktuelle Version: **{version}**" in readme
    assert f"Geprüfter Release-Stand von Version {version}" in readme
    assert baseline_name in readme
    assert baseline_name in workflow
    assert baseline_path.is_file()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert len(baseline) > 3000  # one digest per corpus sentence
