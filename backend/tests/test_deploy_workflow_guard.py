"""Nothing deployable is built, and nothing is deployed, from an arbitrary ref.

WHY THIS EXISTS
---------------
The 2026-09-23 audit (Part 2, P0) found production had drifted from `main`: a
`workflow_dispatch` run can target ANY branch, and the build job's only
condition was the deploy flag, so an image built from an unreviewed branch was
a deployable image. Phase 0 restricted the build jobs to `main` or a tag, and
CONTRACT v2 kept exactly that ("Deploy dispatch restriction stays as Phase 0
shipped it (main or tags)"). A restriction that lives only in a YAML `if:` is
one edit away from gone, and the edit that removes it reads like tidying, so
this file pins it.

WHAT IS PINNED, AND WHY EACH HALF
---------------------------------
* Every job that PUSHES AN IMAGE refuses a pull request and admits exactly two
  ref shapes: `refs/heads/main` and `refs/tags/*`. The set is compared exactly,
  so a third branch added "for the release" fails here with its name.
* Every job that CHANGES AN ENVIRONMENT (a deploy or a `terraform apply`)
  admits `refs/heads/main` only. A tag may build an image; only `main` may put
  one in front of users through this workflow.
* Every job that assumes the AWS role (`id-token: write`) is either one of the
  above or depends, transitively, on a job that is, and no job in that chain
  overrides GitHub's default "skip when a dependency was skipped" with
  `always()` or `!cancelled()`. Without that half, a later job could be given
  AWS credentials on a ref the build job refused.
* No checkout in a gated job names a `ref:`. `github.ref` is what the
  conditions test; a checkout of a different ref would build code the
  condition never looked at.
* `push` fires on `main` only.

The conditions are read from the parsed workflow, not grepped from its text,
so a comment quoting the old condition cannot satisfy or break an assertion.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"

#: The refs a job that pushes an image may run on, in the one shape the
#: workflow writes them. CONTRACT v2: "main or tags".
BUILD_REF_CLAUSE = "(github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/'))"
DEPLOY_REF_CLAUSE = "github.ref == 'refs/heads/main'"

#: Jobs that push an image to a registry. Named, because "which job builds" is
#: the question the restriction is about and a heuristic would be the place a
#: new build job slipped through; `test_every_image_push_is_a_named_build_job`
#: keeps the list honest against the steps themselves.
BUILD_JOBS = frozenset({"pilot-build-and-push", "build-and-push"})

#: Jobs that change a running environment.
DEPLOY_JOBS = frozenset({"pilot-deploy", "apply-staging", "plan-production", "apply-production"})

_REF_LITERAL = re.compile(r"github\.ref\s*==\s*'([^']+)'")
_REF_PREFIX = re.compile(r"startsWith\(\s*github\.ref\s*,\s*'([^']+)'\s*\)")
_DOCKER_PUSH = re.compile(r"\bdocker\s+(image\s+)?push\b|\bcrane\s+push\b")


def _workflow() -> dict:
    if not WORKFLOW.exists():
        pytest.fail(f"{WORKFLOW.relative_to(ROOT)} is missing; port these assertions rather than skipping them.")
    # PyYAML reads YAML 1.1, where the bare key `on` is the boolean True.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict[str, dict]:
    return _workflow()["jobs"]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _condition(job: dict) -> str:
    return str(job.get("if") or "")


def _admitted_refs(condition: str) -> set[str]:
    """Every ref literal and ref prefix a condition compares `github.ref` to."""
    return set(_REF_LITERAL.findall(condition)) | {f"{p}*" for p in _REF_PREFIX.findall(condition)}


def _pushes_an_image(step: dict) -> bool:
    """A build action told to push, or a shell step that pushes."""
    if str(step.get("uses", "")).startswith("docker/build-push-action"):
        return bool((step.get("with") or {}).get("push"))
    return bool(_DOCKER_PUSH.search(str(step.get("run") or "")))


def test_the_workflow_triggers_are_the_ones_this_file_reasons_about() -> None:
    triggers = _workflow().get(True) or _workflow().get("on")
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}, triggers
    assert triggers["push"] == {"branches": ["main"]}, (
        "push fires on main only; a pushed branch that builds is the drift this guards"
    )


def test_every_image_push_is_a_named_build_job() -> None:
    """The BUILD_JOBS list is the thing the restriction is asserted over, so it
    must be exactly the set of jobs that push an image."""
    pushing = {
        name
        for name, job in _jobs().items()
        if any(_pushes_an_image(step) for step in job.get("steps", []))
    }
    assert pushing == BUILD_JOBS, f"jobs that push an image: {sorted(pushing)}"


@pytest.mark.parametrize("name", sorted(BUILD_JOBS))
def test_a_build_job_admits_main_or_a_tag_and_nothing_else(name: str) -> None:
    condition = _condition(_jobs()[name])
    assert "github.event_name != 'pull_request'" in condition, (
        f"{name} must refuse a pull request: a PR head is not a reviewed ref"
    )
    assert BUILD_REF_CLAUSE in condition, f"{name}: {condition!r}"
    assert _admitted_refs(condition) == {"refs/heads/main", "refs/tags/*"}, (
        f"{name} admits {sorted(_admitted_refs(condition))}; CONTRACT v2 allows main or a tag only"
    )


@pytest.mark.parametrize("name", sorted(DEPLOY_JOBS))
def test_a_deploy_job_admits_main_only(name: str) -> None:
    condition = _condition(_jobs()[name])
    assert DEPLOY_REF_CLAUSE in condition, f"{name}: {condition!r}"
    assert _admitted_refs(condition) == {"refs/heads/main"}, (
        f"{name} admits {sorted(_admitted_refs(condition))}"
    )


def test_every_job_holding_aws_credentials_is_behind_a_build_job() -> None:
    jobs = _jobs()

    def gated(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in BUILD_JOBS:
            return True
        if name in seen:
            return False
        return any(gated(dep, seen | {name}) for dep in _needs(jobs[name]))

    holders = {
        name
        for name, job in jobs.items()
        if isinstance(job.get("permissions"), dict) and job["permissions"].get("id-token") == "write"
    }
    assert holders, "no job assumes the AWS role; this test would be vacuous"
    ungated = sorted(name for name in holders if not gated(name))
    assert not ungated, f"these jobs can assume the AWS role without a restricted build: {ungated}"

    for name in holders:
        condition = _condition(jobs[name]).replace(" ", "")
        assert "always()" not in condition and "!cancelled()" not in condition, (
            f"{name} overrides skip-on-skipped-dependency, so it would run when the "
            f"restricted build job refused the ref"
        )


def test_no_gated_job_checks_out_a_ref_other_than_the_one_it_was_triggered_on() -> None:
    jobs = _jobs()
    for name in BUILD_JOBS | DEPLOY_JOBS | {"plan-staging"}:
        for step in jobs[name].get("steps", []):
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert "ref" not in (step.get("with") or {}), (
                    f"{name} checks out an explicit ref; the ref conditions test "
                    f"github.ref, so this would build code they never looked at"
                )
