"""7.9.10: fail-closed release workflow and exact-SHA CI gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_gate import GateError, REQUIRED_CI_JOBS, validate_ci_run


SHA = "a" * 40


def _run(
    *,
    run_id: int = 2,
    status: str = "completed",
    conclusion: str | None = "success",
    started: str = "2026-10-16T12:00:00Z",
) -> dict[str, object]:
    return {
        "id": run_id,
        "run_number": run_id,
        "head_sha": SHA,
        "path": ".github/workflows/ci.yml",
        "status": status,
        "conclusion": conclusion,
        "run_started_at": started,
    }


def _jobs() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
        }
        for name in REQUIRED_CI_JOBS
    ]


def test_exact_sha_with_every_required_job_passes():
    assert validate_ci_run(SHA, [_run()], _jobs()) == 2


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("queued", None),
        ("in_progress", None),
        ("completed", "failure"),
        ("completed", "cancelled"),
    ],
)
def test_pending_failed_or_cancelled_latest_run_blocks(status, conclusion):
    older_success = _run(run_id=1, started="2026-10-16T11:00:00Z")
    latest = _run(status=status, conclusion=conclusion)
    with pytest.raises(GateError, match="latest CI run.*not successful"):
        validate_ci_run(SHA, [older_success, latest], _jobs())


def test_missing_required_job_blocks_release():
    jobs = _jobs()
    jobs.pop()
    with pytest.raises(GateError, match="required CI jobs missing"):
        validate_ci_run(SHA, [_run()], jobs)


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "in_progress"), ("conclusion", "failure"), ("conclusion", "cancelled")],
)
def test_non_green_required_job_blocks_release(field, value):
    jobs = _jobs()
    jobs[0][field] = value
    with pytest.raises(GateError, match="required CI jobs are not successful"):
        validate_ci_run(SHA, [_run()], jobs)


def test_job_for_another_sha_blocks_release():
    jobs = _jobs()
    jobs[0]["head_sha"] = "b" * 40
    with pytest.raises(GateError, match="wrong-sha"):
        validate_ci_run(SHA, [_run()], jobs)


def test_release_workflow_is_manual_rechecks_and_targets_exact_sha():
    root = Path(__file__).parent.parent
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in release
    assert "workflow_run:" not in release
    assert "schedule:" not in release
    assert release.count("python3 scripts/release_gate.py") == 2
    assert '--target "$RELEASE_SHA"' in release
    assert '--notes-file "$RELEASE_NOTES"' in release
    assert "tags-ignore:" in ci and '- "**"' in ci
