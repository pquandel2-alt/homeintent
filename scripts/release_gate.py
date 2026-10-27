#!/usr/bin/env python3
"""Block a release unless one exact main commit passed every required workflow.

Required for exactly the release SHA (7.13.1):

* the newest run of ``ci.yml`` is completed and successful, with every job in
  ``REQUIRED_CI_JOBS`` successful;
* the newest run of ``nightly-live.yml`` is completed and successful, with
  every job in ``REQUIRED_NIGHTLY_JOBS`` successful;
* no run of ``nightly-live.yml`` for that SHA ever failed the safety
  property job: a counterexample found with one seed is never disproved by a
  later run with luckier seeds (7.13.0 was released although the nightly
  safety run of the same SHA had failed - the gate did not read it).

A missing, queued, running, cancelled or failed run blocks.  The gate prints
every run id and SHA it checked.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = "ci.yml"
NIGHTLY_WORKFLOW = "nightly-live.yml"
REQUIRED_CI_JOBS = frozenset(
    {
        "Tests (Python 3.12)",
        "Tests (Python 3.13)",
        "Tests mit hassil aus Home Assistant",
        "Live-Testbett (echtes Home Assistant)",
        "Home Assistant 2026.9 Options Flow",
        "Learning Center mobile UI",
        "MA-Vertrag (HA 2026.4.0)",
        "MA-Vertrag (HA 2026.9.2)",
        "Hassfest",
        "Home Assistant Stable Smoke-Test",
        "HACS",
    }
)
SAFETY_PROPERTY_JOB = "Sicherheitsinvarianten mit wechselnden Seeds"
REQUIRED_NIGHTLY_JOBS = frozenset(
    {
        "Live-Testbett vollständig (inkl. Proaktiv)",
        SAFETY_PROPERTY_JOB,
    }
)
# Jobs whose failure in *any* run of the exact SHA blocks the release.
STICKY_NIGHTLY_JOBS = frozenset({SAFETY_PROPERTY_JOB})
_SHA = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class GateError(RuntimeError):
    """A release invariant is not satisfied."""


@dataclass(frozen=True)
class WorkflowRequirement:
    workflow: str
    required_jobs: frozenset[str]
    sticky_jobs: frozenset[str] = frozenset()


CI_REQUIREMENT = WorkflowRequirement(CI_WORKFLOW, REQUIRED_CI_JOBS)
NIGHTLY_REQUIREMENT = WorkflowRequirement(
    NIGHTLY_WORKFLOW, REQUIRED_NIGHTLY_JOBS, STICKY_NIGHTLY_JOBS
)
REQUIREMENTS = (CI_REQUIREMENT, NIGHTLY_REQUIREMENT)


@dataclass(frozen=True)
class CheckedRun:
    """One workflow run the gate accepted, for the release evidence."""

    workflow: str
    run_id: int
    run_number: int
    run_attempt: int
    head_sha: str
    url: str

    def describe(self) -> str:
        return (
            f"{self.workflow}: run {self.run_id} (#{self.run_number}, attempt "
            f"{self.run_attempt}) on SHA {self.head_sha} - {self.url}"
        )


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


def _run_key(run: Mapping[str, Any]) -> tuple[str, int, int]:
    return (
        str(run.get("created_at") or run.get("run_started_at") or ""),
        int(run.get("run_number") or 0),
        int(run.get("id") or 0),
    )


def matching_runs(
    runs: Iterable[Mapping[str, Any]], sha: str, workflow: str
) -> list[Mapping[str, Any]]:
    """Every run of ``workflow`` for exactly ``sha``, newest first."""
    found = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and str(run.get("path", "")).split("@", 1)[0].endswith(f"/{workflow}")
    ]
    return sorted(found, key=_run_key, reverse=True)


def _latest_run(
    runs: Iterable[Mapping[str, Any]], sha: str, workflow: str = CI_WORKFLOW
) -> Mapping[str, Any]:
    matching = matching_runs(runs, sha, workflow)
    if not matching:
        raise GateError(f"no {workflow} run found for exact SHA {sha}")
    return matching[0]


def _check_jobs(
    sha: str,
    workflow: str,
    jobs: Iterable[Mapping[str, Any]],
    required: frozenset[str],
) -> None:
    by_name = {str(job.get("name")): job for job in jobs}
    label = "CI" if workflow == CI_WORKFLOW else workflow
    missing = sorted(required - by_name.keys())
    if missing:
        raise GateError(f"required {label} jobs missing: " + ", ".join(missing))
    invalid = []
    for name in sorted(required):
        job = by_name[name]
        if job.get("head_sha") != sha:
            invalid.append(f"{name}=wrong-sha")
        elif job.get("status") != "completed":
            invalid.append(f"{name}={job.get('status')}")
        elif job.get("conclusion") != "success":
            invalid.append(f"{name}={job.get('conclusion')}")
    if invalid:
        raise GateError(
            f"required {label} jobs are not successful: " + ", ".join(invalid)
        )


def validate_workflow(
    sha: str,
    requirement: WorkflowRequirement,
    runs: Iterable[Mapping[str, Any]],
    jobs_for_run: Callable[[int], Sequence[Mapping[str, Any]]],
) -> CheckedRun:
    """Validate the newest run of one workflow (and its sticky history)."""
    workflow = requirement.workflow
    label = "CI" if workflow == CI_WORKFLOW else workflow
    matching = matching_runs(runs, sha, workflow)
    if not matching:
        raise GateError(f"no {workflow} run found for exact SHA {sha}")
    latest = matching[0]
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise GateError(
            f"latest {label} run for exact SHA is not successful: "
            f"run={latest.get('id')}, status={latest.get('status')!r}, "
            f"conclusion={latest.get('conclusion')!r}"
        )
    run_id = int(latest["id"])
    _check_jobs(sha, workflow, jobs_for_run(run_id), requirement.required_jobs)
    if requirement.sticky_jobs:
        for older in matching[1:]:
            failed = [
                str(job.get("name"))
                for job in jobs_for_run(int(older["id"]))
                if str(job.get("name")) in requirement.sticky_jobs
                and job.get("status") == "completed"
                and job.get("conclusion") not in {"success", "skipped", "cancelled"}
            ]
            if failed:
                raise GateError(
                    f"{workflow} run {older.get('id')} for the same SHA failed "
                    f"{', '.join(sorted(failed))}; a found counterexample is not "
                    "disproved by a later run"
                )
    return CheckedRun(
        workflow,
        run_id,
        int(latest.get("run_number") or 0),
        int(latest.get("run_attempt") or 1),
        str(latest.get("head_sha")),
        str(latest.get("html_url") or ""),
    )


def validate_ci_run(
    sha: str,
    runs: Iterable[Mapping[str, Any]],
    jobs: Iterable[Mapping[str, Any]],
) -> int:
    """Validate and return the selected CI run id (compatibility API)."""
    job_list = list(jobs)
    return validate_workflow(sha, CI_REQUIREMENT, runs, lambda _run_id: job_list).run_id


def validate_required_runs(
    sha: str,
    runs_by_workflow: Mapping[str, Iterable[Mapping[str, Any]]],
    jobs_for_run: Callable[[int], Sequence[Mapping[str, Any]]],
) -> tuple[CheckedRun, ...]:
    """Every required workflow must be green for exactly ``sha``."""
    return tuple(
        validate_workflow(
            sha,
            requirement,
            runs_by_workflow.get(requirement.workflow, ()),
            jobs_for_run,
        )
        for requirement in REQUIREMENTS
    )


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


def _all_runs(repository: str, workflow: str, sha: str) -> list[Mapping[str, Any]]:
    runs: list[Mapping[str, Any]] = []
    page = 1
    while True:
        payload = _gh_json(
            repository,
            f"actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100&page={page}",
        )
        batch = payload.get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def validate_release(
    *,
    repository: str,
    sha: str,
    version: str,
    title: str,
    notes_path: Path,
) -> tuple[CheckedRun, ...]:
    """Validate local metadata and remote workflow state for one release."""
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
    runs_by_workflow = {
        requirement.workflow: _all_runs(repository, requirement.workflow, sha)
        for requirement in REQUIREMENTS
    }
    cache: dict[int, Sequence[Mapping[str, Any]]] = {}

    def jobs_for_run(run_id: int) -> Sequence[Mapping[str, Any]]:
        if run_id not in cache:
            cache[run_id] = _gh_json(
                repository, f"actions/runs/{run_id}/jobs?filter=latest&per_page=100"
            ).get("jobs", [])
        return cache[run_id]

    return validate_required_runs(sha, runs_by_workflow, jobs_for_run)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args()
    print(f"Release gate: repository {args.repository}, requested SHA {args.sha}")
    try:
        checked = validate_release(
            repository=args.repository,
            sha=args.sha,
            version=args.version,
            title=args.title,
            notes_path=args.notes,
        )
    except (GateError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as err:
        print(f"RELEASE BLOCKED: {err}")
        return 1
    for run in checked:
        print(f"  checked {run.describe()}")
    print(
        f"RELEASE GATE PASSED: SHA {args.sha}, "
        + ", ".join(f"{run.workflow} run {run.run_id}" for run in checked)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
