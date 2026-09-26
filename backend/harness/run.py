"""Run identity and provenance: what a run was, on what code, against what data.

WHY A MANIFEST AND NOT A LOG LINE
-----------------------------------
HARNESS.md section 5 asks for a record that makes a failure reproducible and
section 6 asks for replay from that record. Both fall apart the moment a single
field is reconstructed by hand, so everything replay needs is written down at
the time the run starts, when it is a fact, rather than inferred afterwards,
when it is a guess: the commit, whether the tree was dirty, the scenario
version, the world, the faults, the seed, and which dispatch backend was in
force.

The dirty flag earns its place. A run against an edited working tree is not a
run against its commit, and a bug report quoting only a SHA sends the next
reader to code that never produced the failure.

GIT MAY BE ABSENT, AND THAT IS RECORDED RATHER THAN RAISED
------------------------------------------------------------
The harness runs inside containers that carry the source without the history.
Refusing to run there would be the harness deciding it knows better than its
operator; pretending to know the SHA would be worse. `None` is the honest third
answer, it serialises as `null`, and a report renders it as "unknown" so a
reader is told rather than shown a blank.

OUTCOME VOCABULARY IS BORROWED, NOT RETYPED
---------------------------------------------
`pass`, `fail` and `unavailable` come from `app/evaluation/release_gate.py`,
which already owns them and already states the rule that matters: UNAVAILABLE
is a distinct outcome and never a synonym for either of the others. Retyping
the three strings here is how `services/tiers.py` ended up disagreeing with
`services/rating.py` for a year, so they are imported.

Provenance: docs/spec/HARNESS.md sections 5, 6 and 7.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.evaluation.release_gate import FAIL, PASS, UNAVAILABLE

from harness import CONTRACT
from harness.scenario import FaultRequest, Scenario, Tier

#: Re-exported so a caller writing an evaluator or a check does not have to
#: know that the vocabulary came from the release gate, while there remains
#: exactly one definition of each string in the tree.
__all__ = [
    "PASS",
    "FAIL",
    "UNAVAILABLE",
    "Check",
    "EvaluationOutcome",
    "GitState",
    "RunRecord",
    "git_state",
    "new_run",
]

_OUTCOMES = frozenset({PASS, FAIL, UNAVAILABLE})

#: The assertion kinds of HARNESS.md section 3. A check names its kind so a
#: report can group by it: "three state assertions failed" and "three
#: prohibited outcomes occurred" are very different mornings.
CHECK_KINDS = ("state", "dispatched", "output", "trajectory", "prohibited")


def _utc_now() -> str:
    """UTC, to the second, as an ISO 8601 string with an explicit offset.

    Stored as text rather than as an epoch number because a manifest is read by
    people at least as often as by code, and a naive local timestamp in an
    artifact that travels between a Windows workstation and a Linux container
    is a fact nobody can compare.
    """
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class GitState:
    """The commit a run stood on, or a stated absence of one."""

    sha: str | None
    branch: str | None
    dirty: bool | None

    @property
    def described(self) -> str:
        if self.sha is None:
            return "unknown (git unavailable)"
        suffix = ""
        if self.dirty:
            suffix = " (working tree dirty)"
        elif self.dirty is None:
            suffix = " (dirty state unknown)"
        return f"{self.sha} on {self.branch or 'detached'}{suffix}"


def _git(args: Sequence[str], *, cwd: pathlib.Path) -> str | None:
    """One git question, answered or explicitly unanswered.

    `OSError` covers the case this is written for: git absent from the image,
    which raises `FileNotFoundError`. It is caught by name rather than by a bare
    `except`, and a non-zero exit is treated the same way for the same reason:
    a repository git cannot read is not a repository whose SHA we may invent.
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_state(repo_root: pathlib.Path) -> GitState:
    """Read the commit, the branch and the dirty flag, or record that we could not."""
    sha = _git(["rev-parse", "HEAD"], cwd=repo_root)
    if sha is None:
        return GitState(None, None, None)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
    status = _git(["status", "--porcelain"], cwd=repo_root)
    return GitState(sha, branch, None if status is None else bool(status))


@dataclass(frozen=True)
class EvaluationOutcome:
    """One evaluator's answer, in the shape a manifest can carry losslessly.

    WHY THE SCORE AND ITS INTERVAL ARE PLAIN NUMBERS HERE
    A `metrics.Measurement` is the right type for a number that knows whether it
    is a number, and `report.py` builds one from every outcome. It is not the
    right type for a field that round trips through JSON, because reconstructing
    one from a dict is a second parser for an invariant `Measurement` already
    owns. So the invariant is restated once, at this boundary: a score without
    an interval is REFUSED, exactly as `Measurement` refuses it, and the report
    layer converts rather than re-validates.

    A bare verdict with no score is the normal case. Most of section 7's
    evaluators answer a yes or no question ("did a number reach a client") where
    a numeric score would be an invention.
    """

    evaluator: str
    outcome: str
    reason: str
    score: float | None = None
    interval: tuple[float, float] | None = None
    sample_size: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evaluator.strip():
            raise ValueError("an evaluation outcome must name its evaluator")
        if self.outcome not in _OUTCOMES:
            raise ValueError(
                f"unknown outcome {self.outcome!r}; permitted: "
                f"{', '.join(sorted(_OUTCOMES))}"
            )
        if not self.reason.strip():
            raise ValueError(
                f"evaluator {self.evaluator!r} gave no reason. An outcome a "
                "reader cannot act on is a number with no explanation, which "
                "is what 'unavailable, never 0.0' exists to prevent."
            )
        if self.outcome == UNAVAILABLE and self.score is not None:
            raise ValueError(
                f"evaluator {self.evaluator!r} reported unavailable and a score "
                f"of {self.score}. An unavailable measurement has no value."
            )
        if self.score is not None and self.interval is None:
            raise ValueError(
                f"evaluator {self.evaluator!r} gave a score with no interval. A "
                "point estimate with no dispersion beside it is what turns "
                "eval noise into a release decision."
            )
        if self.interval is not None:
            low, high = self.interval
            if low > high:
                raise ValueError(
                    f"evaluator {self.evaluator!r} interval low {low} exceeds "
                    f"high {high}"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluator": self.evaluator,
            "outcome": self.outcome,
            "reason": self.reason,
            "score": self.score,
            "interval": None if self.interval is None else list(self.interval),
            "sample_size": self.sample_size,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> "EvaluationOutcome":
        interval = body.get("interval")
        return cls(
            evaluator=str(body["evaluator"]),
            outcome=str(body["outcome"]),
            reason=str(body["reason"]),
            score=None if body.get("score") is None else float(body["score"]),
            interval=None if interval is None else (float(interval[0]), float(interval[1])),
            sample_size=(
                None if body.get("sample_size") is None else int(body["sample_size"])
            ),
            details=dict(body.get("details") or {}),
        )


@dataclass(frozen=True)
class Check:
    """One ground-truth assertion, with what was expected and what was seen.

    Both sides are carried because "assertion failed" is the least useful
    sentence a harness can print. The report renders them side by side, which
    is the difference between a run somebody debugs and a run somebody re-runs.

    `actual` is `None` only when the check never ran, which happens when an
    earlier fault aborted the workload. That is a distinct state from a check
    that ran and saw nothing, and a report that collapsed the two would send a
    reader looking for a bug in the wrong half of the system.
    """

    kind: str
    expression: str
    expected: Any
    actual: Any
    passed: bool
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in CHECK_KINDS:
            raise ValueError(
                f"unknown check kind {self.kind!r}; permitted: "
                f"{', '.join(CHECK_KINDS)}"
            )
        if not self.expression.strip():
            raise ValueError("a check must carry the expression it evaluated")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "expression": self.expression,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> "Check":
        return cls(
            kind=str(body["kind"]),
            expression=str(body["expression"]),
            expected=body.get("expected"),
            actual=body.get("actual"),
            passed=bool(body["passed"]),
            note=str(body.get("note") or ""),
        )


@dataclass
class RunRecord:
    """One execution, from the facts known before it to the verdicts after it.

    MUTABLE, DELIBERATELY, UNLIKE EVERY OTHER TYPE IN THIS PACKAGE. A run is the
    one thing here that genuinely has a before and an after: the manifest is
    written at `begin` so that a crashed run still leaves evidence, and the
    outcome is filled in later. A frozen record would force the crash case to
    leave nothing behind, which is the case the artifact store exists for.
    """

    run_id: str
    started_at: str
    scenario_id: str
    scenario_version: int
    tier: Tier
    world: str
    workload: tuple[str, ...]
    faults: tuple[FaultRequest, ...]
    seed: int
    dispatch_backend: str
    git_sha: str | None
    git_branch: str | None
    git_dirty: bool | None
    config: dict[str, Any] = field(default_factory=dict)
    dataset_versions: dict[str, str] = field(default_factory=dict)
    finished_at: str | None = None
    status: str = UNAVAILABLE
    evaluations: list[EvaluationOutcome] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    contract: str = CONTRACT

    @property
    def git(self) -> GitState:
        return GitState(self.git_sha, self.git_branch, self.git_dirty)

    @property
    def failed_checks(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    @property
    def failed_evaluations(self) -> list[EvaluationOutcome]:
        return [item for item in self.evaluations if item.outcome == FAIL]

    @property
    def unavailable_evaluations(self) -> list[EvaluationOutcome]:
        return [item for item in self.evaluations if item.outcome == UNAVAILABLE]

    @property
    def duration_seconds(self) -> float | None:
        """Wall clock, or None while the run is still open.

        None rather than zero, for the reason the whole package keeps repeating:
        a zero duration on an unfinished run would enter a baseline comparison
        as the fastest run ever recorded.
        """
        if self.finished_at is None:
            return None
        start = dt.datetime.fromisoformat(self.started_at)
        end = dt.datetime.fromisoformat(self.finished_at)
        return (end - start).total_seconds()

    def finish(self) -> None:
        """Stamp the end and DERIVE the status from what was actually recorded.

        Derived rather than assigned by the caller, because a status somebody
        sets by hand is a status that can disagree with the checks beside it,
        and the disagreement always resolves in the optimistic direction. The
        precedence is fail, then unavailable, then pass: a single failing check
        or evaluator sinks the run, and an evaluator that could not compute
        BLOCKS rather than passes, which is `release_gate`'s existing and
        correct behaviour.
        """
        self.finished_at = _utc_now()
        if self.failed_checks or self.failed_evaluations:
            self.status = FAIL
        elif self.unavailable_evaluations or not (self.checks or self.evaluations):
            self.status = UNAVAILABLE
        else:
            self.status = PASS

    def to_manifest(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git_sha": self.git_sha,
            "git_branch": self.git_branch,
            "git_dirty": self.git_dirty,
            "scenario_id": self.scenario_id,
            "scenario_version": self.scenario_version,
            "tier": self.tier.value,
            "world": self.world,
            "workload": list(self.workload),
            "faults": [
                {"name": fault.name, "params": dict(fault.params)} for fault in self.faults
            ],
            "seed": self.seed,
            "dispatch_backend": self.dispatch_backend,
            "config": dict(self.config),
            "dataset_versions": dict(self.dataset_versions),
            "status": self.status,
            "evaluations": [item.as_dict() for item in self.evaluations],
            "checks": [item.as_dict() for item in self.checks],
        }

    @classmethod
    def from_manifest(cls, body: Mapping[str, Any]) -> "RunRecord":
        return cls(
            run_id=str(body["run_id"]),
            started_at=str(body["started_at"]),
            scenario_id=str(body["scenario_id"]),
            scenario_version=int(body["scenario_version"]),
            tier=Tier(str(body["tier"])),
            world=str(body["world"]),
            workload=tuple(str(item) for item in body.get("workload") or ()),
            faults=tuple(
                FaultRequest(str(item["name"]), dict(item.get("params") or {}))
                for item in body.get("faults") or ()
            ),
            seed=int(body["seed"]),
            dispatch_backend=str(body["dispatch_backend"]),
            git_sha=None if body.get("git_sha") is None else str(body["git_sha"]),
            git_branch=None if body.get("git_branch") is None else str(body["git_branch"]),
            git_dirty=None if body.get("git_dirty") is None else bool(body["git_dirty"]),
            config=dict(body.get("config") or {}),
            dataset_versions={
                str(key): str(value)
                for key, value in (body.get("dataset_versions") or {}).items()
            },
            finished_at=(
                None if body.get("finished_at") is None else str(body["finished_at"])
            ),
            status=str(body.get("status") or UNAVAILABLE),
            evaluations=[
                EvaluationOutcome.from_dict(item) for item in body.get("evaluations") or ()
            ],
            checks=[Check.from_dict(item) for item in body.get("checks") or ()],
            contract=str(body.get("contract") or CONTRACT),
        )


def new_run(
    scenario: Scenario,
    *,
    repo_root: pathlib.Path,
    dispatch_backend: str,
    seed: int,
    config: Mapping[str, Any] | None = None,
    dataset_versions: Mapping[str, str] | None = None,
) -> RunRecord:
    """Mint a run for a scenario, capturing provenance before anything executes.

    The id is a uuid4 hex rather than a counter or a timestamp. A counter needs
    shared state the harness does not have, and a timestamp collides the moment
    two tiers run in parallel, which is exactly what CI does.
    """
    state = git_state(repo_root)
    return RunRecord(
        run_id=uuid.uuid4().hex,
        started_at=_utc_now(),
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        tier=scenario.tier,
        world=scenario.given.world,
        workload=scenario.workload,
        faults=scenario.faults,
        seed=seed,
        dispatch_backend=dispatch_backend,
        git_sha=state.sha,
        git_branch=state.branch,
        git_dirty=state.dirty,
        config=dict(config or {}),
        dataset_versions=dict(dataset_versions or {}),
    )
