"""7.13.1: the release gate requires CI *and* the nightly safety run per SHA.

7.13.0 was published from a commit whose normal CI was green while the
nightly safety run of exactly that commit had failed: the gate only read
``ci.yml``.  The gate now validates every workflow in ``REQUIREMENTS`` for
the exact SHA, picks the newest run of each, and never lets a later run hide
a failed safety property job of the same SHA.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.release_gate import (
    CI_WORKFLOW,
    NIGHTLY_WORKFLOW,
    REQUIRED_CI_JOBS,
    REQUIRED_NIGHTLY_JOBS,
    SAFETY_PROPERTY_JOB,
    GateError,
    validate_required_runs,
)

SHA = "a" * 40
OTHER = "b" * 40


def _run(workflow: str, run_id: int, *, sha: str = SHA, status: str = "completed",
         conclusion: str | None = "success", created: str = "2026-10-27T10:00:00Z") -> dict[str, Any]:
    return {
        "id": run_id,
        "run_number": run_id,
        "run_attempt": 1,
        "head_sha": sha,
        "path": f".github/workflows/{workflow}",
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
    }


def _jobs(names, *, sha: str = SHA, overrides: dict[str, dict[str, Any]] | None = None):
    jobs = []
    for name in names:
        job = {"name": name, "head_sha": sha, "status": "completed", "conclusion": "success"}
        job.update((overrides or {}).get(name, {}))
        jobs.append(job)
    return jobs


def _jobs_by_run(mapping: dict[int, list[dict[str, Any]]]):
    def jobs_for_run(run_id: int):
        return mapping[run_id]

    return jobs_for_run


CI_GREEN = _run(CI_WORKFLOW, 100)
CI_JOBS = {100: _jobs(REQUIRED_CI_JOBS)}


def _validate(nightly_runs, nightly_jobs, *, ci_runs=(CI_GREEN,)):
    return validate_required_runs(
        SHA,
        {CI_WORKFLOW: list(ci_runs), NIGHTLY_WORKFLOW: list(nightly_runs)},
        _jobs_by_run({**CI_JOBS, **nightly_jobs}),
    )


def test_ci_green_nightly_missing_blocks():
    with pytest.raises(GateError, match=f"no {NIGHTLY_WORKFLOW} run found for exact SHA"):
        _validate([], {})


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting"])
def test_ci_green_nightly_running_blocks(status):
    with pytest.raises(GateError, match="latest nightly-live.yml run for exact SHA is not successful"):
        _validate([_run(NIGHTLY_WORKFLOW, 200, status=status, conclusion=None)], {})


def test_ci_green_nightly_failed_blocks():
    with pytest.raises(GateError, match="not successful.*conclusion='failure'"):
        _validate([_run(NIGHTLY_WORKFLOW, 200, conclusion="failure")], {})


def test_ci_green_nightly_cancelled_blocks():
    with pytest.raises(GateError, match="conclusion='cancelled'"):
        _validate([_run(NIGHTLY_WORKFLOW, 200, conclusion="cancelled")], {})


def test_ci_green_nightly_green_for_another_sha_blocks():
    with pytest.raises(GateError, match=f"no {NIGHTLY_WORKFLOW} run found for exact SHA"):
        _validate([_run(NIGHTLY_WORKFLOW, 200, sha=OTHER)], {200: _jobs(REQUIRED_NIGHTLY_JOBS, sha=OTHER)})


def test_nightly_job_for_another_sha_blocks():
    with pytest.raises(GateError, match="wrong-sha"):
        _validate([_run(NIGHTLY_WORKFLOW, 200)], {200: _jobs(REQUIRED_NIGHTLY_JOBS, sha=OTHER)})


def test_both_workflows_green_for_exact_sha_pass_and_report_run_ids():
    checked = _validate([_run(NIGHTLY_WORKFLOW, 200)], {200: _jobs(REQUIRED_NIGHTLY_JOBS)})
    assert [(run.workflow, run.run_id, run.head_sha) for run in checked] == [
        (CI_WORKFLOW, 100, SHA), (NIGHTLY_WORKFLOW, 200, SHA),
    ]
    assert "run 200" in checked[1].describe() and SHA in checked[1].describe()


def test_old_green_and_new_failed_nightly_blocks():
    older = _run(NIGHTLY_WORKFLOW, 200, created="2026-10-27T08:00:00Z")
    newer = _run(NIGHTLY_WORKFLOW, 201, conclusion="failure", created="2026-10-27T09:00:00Z")
    with pytest.raises(GateError, match="run=201"):
        _validate([older, newer], {200: _jobs(REQUIRED_NIGHTLY_JOBS)})


def test_old_failed_safety_job_is_not_hidden_by_a_newer_green_nightly():
    older = _run(NIGHTLY_WORKFLOW, 200, conclusion="failure", created="2026-10-27T08:00:00Z")
    newer = _run(NIGHTLY_WORKFLOW, 201, created="2026-10-27T09:00:00Z")
    jobs = {
        200: _jobs(REQUIRED_NIGHTLY_JOBS, overrides={SAFETY_PROPERTY_JOB: {"conclusion": "failure"}}),
        201: _jobs(REQUIRED_NIGHTLY_JOBS),
    }
    with pytest.raises(GateError, match="counterexample is not disproved"):
        _validate([older, newer], jobs)


def test_old_failed_live_testbed_alone_does_not_block_a_newer_green_nightly():
    older = _run(NIGHTLY_WORKFLOW, 200, conclusion="failure", created="2026-10-27T08:00:00Z")
    newer = _run(NIGHTLY_WORKFLOW, 201, created="2026-10-27T09:00:00Z")
    live = next(name for name in REQUIRED_NIGHTLY_JOBS if name != SAFETY_PROPERTY_JOB)
    jobs = {
        200: _jobs(REQUIRED_NIGHTLY_JOBS, overrides={live: {"conclusion": "failure"}}),
        201: _jobs(REQUIRED_NIGHTLY_JOBS),
    }
    assert _validate([older, newer], jobs)[1].run_id == 201


def test_missing_required_nightly_job_blocks():
    jobs = {200: _jobs(REQUIRED_NIGHTLY_JOBS - {SAFETY_PROPERTY_JOB})}
    with pytest.raises(GateError, match="required nightly-live.yml jobs missing"):
        _validate([_run(NIGHTLY_WORKFLOW, 200)], jobs)


def test_failed_ci_still_blocks_with_green_nightly():
    with pytest.raises(GateError, match="latest CI run"):
        _validate(
            [_run(NIGHTLY_WORKFLOW, 200)], {200: _jobs(REQUIRED_NIGHTLY_JOBS)},
            ci_runs=[_run(CI_WORKFLOW, 100, conclusion="failure")],
        )


def test_the_7130_situation_is_blocked():
    """CI green, nightly of the same SHA failed in the safety job."""
    nightly = _run(NIGHTLY_WORKFLOW, 40470216752, conclusion="failure")
    jobs = {
        40470216752: _jobs(
            REQUIRED_NIGHTLY_JOBS, overrides={SAFETY_PROPERTY_JOB: {"conclusion": "failure"}}
        )
    }
    with pytest.raises(GateError):
        _validate([nightly], jobs)


def test_workflows_wire_the_gate_and_the_nightly_dispatch():
    root = Path(__file__).parent.parent
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    nightly = (root / ".github/workflows/nightly-live.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in nightly
    assert "tests/test_safety_properties.py" in nightly
    # One release at a time; the tag is pinned to the checked SHA before the
    # release is created, and verified afterwards.
    assert "group: release\n" in release
    assert 'sha="$RELEASE_SHA"' in release and "--verify-tag" in release
    assert '[ "$tagged" != "$RELEASE_SHA" ]' in release
    for name in REQUIRED_NIGHTLY_JOBS:
        assert f"name: {name}" in nightly


def test_evidence_names_the_commit_and_every_checked_run():
    from scripts.release_gate import render_evidence

    checked = _validate([_run(NIGHTLY_WORKFLOW, 200)], {200: _jobs(REQUIRED_NIGHTLY_JOBS)})
    text = render_evidence("o/r", SHA, checked)
    assert f"https://github.com/o/r/commit/{SHA}" in text
    assert "[Lauf 100](https://github.com/o/r/actions/runs/100)" in text
    assert "[Lauf 200](https://github.com/o/r/actions/runs/200)" in text
