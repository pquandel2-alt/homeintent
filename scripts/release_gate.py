#!/usr/bin/env python3
"""Block a release unless one exact main commit has a fully green CI run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = "ci.yml"
REQUIRED_CI_JOBS = frozenset(
    {
        "Tests (Python 3.12)",
        "Tests (Python 3.13)",
        "Tests mit hassil aus Home Assistant",
        "Live-Testbett (echtes Home Assistant)",
        "Home Assistant 2026.9 Options Flow",
        "Learning Center mobile UI",
        "Hassfest",
        "Home Assistant Stable Smoke-Test",
        "HACS",
    }
)
_SHA = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class GateError(RuntimeError):
    """A release invariant is not satisfied."""


def _gh_json(repository: str, endpoint: str) -> Any:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/{endpoint}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise GateError(result.stderr.strip() or f"GitHub API failed: {endpoint}")
    return json.loads(result.stdout)


def _local_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _latest_ci_run(runs: Iterable[dict[str, Any]], sha: str) -> dict[str, Any]:
    matching = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and str(run.get("path", "")).endswith(f"/{CI_WORKFLOW}")
    ]
    if not matching:
        raise GateError(f"no CI run found for exact SHA {sha}")
    return max(
        matching,
        key=lambda run: (
            str(run.get("run_started_at") or run.get("created_at") or ""),
            int(run.get("run_number") or 0),
            int(run.get("id") or 0),
        ),
    )


def validate_ci_run(
    sha: str,
    runs: Iterable[dict[str, Any]],
    jobs: Iterable[dict[str, Any]],
) -> int:
    """Validate and return the selected CI run id."""
    run = _latest_ci_run(runs, sha)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise GateError(
            "latest CI run for exact SHA is not successful: "
            f"status={run.get('status')!r}, conclusion={run.get('conclusion')!r}"
        )
    run_id = int(run["id"])
    by_name = {str(job.get("name")): job for job in jobs}
    missing = sorted(REQUIRED_CI_JOBS - by_name.keys())
    if missing:
        raise GateError("required CI jobs missing: " + ", ".join(missing))
    invalid = []
    for name in sorted(REQUIRED_CI_JOBS):
        job = by_name[name]
        if job.get("head_sha") != sha:
            invalid.append(f"{name}=wrong-sha")
        elif job.get("status") != "completed":
            invalid.append(f"{name}={job.get('status')}")
        elif job.get("conclusion") != "success":
            invalid.append(f"{name}={job.get('conclusion')}")
    if invalid:
        raise GateError("required CI jobs are not successful: " + ", ".join(invalid))
    return run_id


def _assert_tag_absent(repository: str, tag: str) -> None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/git/ref/tags/{tag}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        raise GateError(f"tag already exists: {tag}")
    # GitHub returns 404 for an absent ref. Authentication and server errors
    # must not be mistaken for permission to publish.
    if "404" not in result.stderr and "Not Found" not in result.stderr:
        raise GateError(result.stderr.strip() or f"could not verify tag absence: {tag}")

    release = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if release.returncode == 0:
        raise GateError(f"release already exists: {tag}")
    if "release not found" not in release.stderr.casefold():
        raise GateError(
            release.stderr.strip() or f"could not verify release absence: {tag}"
        )


def validate_release(
    *,
    repository: str,
    sha: str,
    version: str,
    title: str,
    notes_path: Path,
) -> int:
    """Validate local metadata and remote CI state for one release."""
    if not _SHA.fullmatch(sha):
        raise GateError("sha must be a full lowercase 40-character commit id")
    if not _VERSION.fullmatch(version):
        raise GateError("version must be a three-part semantic version without 'v'")
    tag = f"v{version}"
    expected_title = f"HomeIntent v{version}"
    if title != expected_title:
        raise GateError(f"release title must be {expected_title!r}")
    manifest = json.loads(
        (ROOT / "custom_components/homeintent/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if manifest.get("version") != version:
        raise GateError(
            f"manifest version {manifest.get('version')!r} does not match {version!r}"
        )
    resolved_notes = notes_path if notes_path.is_absolute() else ROOT / notes_path
    if not resolved_notes.is_file() or not resolved_notes.read_text(
        encoding="utf-8"
    ).strip():
        raise GateError(f"release notes are missing or empty: {notes_path}")
    if _local_head() != sha:
        raise GateError("checked-out commit does not equal the requested release SHA")
    commit = _gh_json(repository, f"commits/{sha}")
    if commit.get("sha") != sha:
        raise GateError("GitHub did not resolve the requested exact SHA")
    main = _gh_json(repository, "commits/main")
    if main.get("sha") != sha:
        raise GateError("requested release SHA is not the current main commit")
    _assert_tag_absent(repository, tag)
    runs_payload = _gh_json(
        repository,
        f"actions/workflows/{CI_WORKFLOW}/runs?head_sha={sha}&per_page=100",
    )
    run = _latest_ci_run(runs_payload.get("workflow_runs", []), sha)
    jobs_payload = _gh_json(
        repository,
        f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
    )
    return validate_ci_run(sha, runs_payload.get("workflow_runs", []), jobs_payload.get("jobs", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_id = validate_release(
            repository=args.repository,
            sha=args.sha,
            version=args.version,
            title=args.title,
            notes_path=args.notes,
        )
    except (GateError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as err:
        print(f"RELEASE BLOCKED: {err}")
        return 1
    print(f"RELEASE GATE PASSED: SHA {args.sha}, CI run {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
