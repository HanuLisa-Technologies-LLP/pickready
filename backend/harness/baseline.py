"""Promoting a reference run, and diffing against it without inventing a verdict.

WHAT A BASELINE IS FOR
------------------------
HARNESS.md section 8: a promoted run is the reference for its TIER, and
`compare` fails on a regression that crosses an explicit threshold. Per tier
rather than per scenario, because a tier is what CI runs and what a budget is
set against; a per scenario baseline would make "the regression tier got slower"
a question nobody could ask.

NO BASELINE IS `unavailable`, NOT `pass`
------------------------------------------
The single most important decision in this file, and it is borrowed rather than
invented: `app/evaluation/release_gate.py` already states it. "A metric that
could not be computed must BLOCK, or the first thing a broken evaluation
harness does is wave every release through while showing green." A comparison
against a baseline that does not exist has computed nothing, so it reports
`unavailable`, and the CLI exits non-zero on it.

THRESHOLDS ARE DATA AND EVERY ONE CARRIES ITS REASON
------------------------------------------------------
`thresholds.yaml`, loaded here, refused here when a reason is missing. A guard
threshold moves only with its reason written beside it, which is the rule
CLAUDE.md states about `GATED_PROMPTS` and which this file makes mechanical: a
reasonless entry does not load, so it cannot be added quietly during a red
build.

EVERY METRIC COMPARED HERE RISES WHEN THINGS GET WORSE
--------------------------------------------------------
Deliberate uniformity. A table mixing directions is one where a sign error reads
as a passing gate, and a sign error in a gate is invisible until the day it
matters.

Provenance: docs/spec/HARNESS.md section 8.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml

from app.evaluation.release_gate import FAIL, PASS, UNAVAILABLE

from harness.artifacts import ArtifactStore
from harness.report import summary
from harness.run import RunRecord
from harness.scenario import Tier

THRESHOLDS_PATH = pathlib.Path(__file__).resolve().parent / "thresholds.yaml"


class BaselineError(RuntimeError):
    """A baseline cannot be promoted or compared, with the reason."""


class ThresholdError(ValueError):
    """A threshold entry is unusable, naming the entry and what is wrong."""


@dataclass(frozen=True)
class Threshold:
    """One metric's tolerance, and the sentence justifying it.

    `reason` is not documentation, it is a field, and it is required. A
    threshold whose justification lives in a commit message is a threshold the
    next reader has to go archaeology for, and the reader in question is
    normally somebody deciding at speed whether to relax it.
    """

    metric: str
    max_increase: float | None
    max_increase_ratio: float | None
    reason: str
    #: Below this baseline value a RATIO bound is not applied, because a
    #: proportional rise off a tiny number is noise wearing a regression's
    #: clothes. Optional, and meaningless beside `max_increase`: an absolute
    #: bound already means the same thing at every scale.
    #:
    #: Measured while verifying this harness. `safety.the_release_gate_...`
    #: runs in about a second, so a 0.5 ratio put its ceiling at 1.5 seconds,
    #: and the same scenario replayed on a machine carrying five other agents
    #: took 21. The gate fired, correctly by its own arithmetic and wrongly
    #: about the code, which is precisely how a gate becomes one somebody
    #: disables. `NOISE_BAND` in `release_gate.py` exists for the same reason
    #: one layer in: a bound has to be wider than the measurement's own noise.
    min_baseline: float | None = None

    def __post_init__(self) -> None:
        declared = [
            value
            for value in (self.max_increase, self.max_increase_ratio)
            if value is not None
        ]
        if len(declared) != 1:
            raise ThresholdError(
                f"threshold {self.metric!r} must carry exactly one bound, got "
                f"{len(declared)}"
            )
        if not self.reason.strip():
            raise ThresholdError(f"threshold {self.metric!r} carries no reason")
        if self.min_baseline is not None and self.max_increase_ratio is None:
            raise ThresholdError(
                f"threshold {self.metric!r} declares 'min_baseline' beside an "
                "absolute bound. A floor only means anything under a ratio: an "
                "absolute bound already says the same thing at every scale, so "
                "the pair reads as a second bound nobody applies."
            )

    def exceeded_by(self, baseline: float, observed: float) -> bool:
        """True when the rise from `baseline` to `observed` is a regression."""
        if self.max_increase is not None:
            return observed - baseline > self.max_increase
        ratio = self.max_increase_ratio
        if ratio is None:
            raise ThresholdError(f"threshold {self.metric!r} carries no bound")
        if baseline <= 0.0:
            # A ratio against zero has no meaning. Any rise off a zero baseline
            # is reported, because the alternative is a metric that can never
            # regress once it has touched zero, which is the state a healthy
            # count metric sits in permanently.
            return observed > 0.0
        if self.min_baseline is not None and baseline < self.min_baseline:
            # Too small to say anything about. Reported as no regression rather
            # than as unavailable: the metric WAS compared and the comparison
            # was inconclusive, which is a different statement from "nothing
            # could be measured", and the count metrics beside it in the same
            # comparison are still doing their job.
            return False
        return (observed - baseline) / baseline > ratio

    def describe_bound(self, baseline: float) -> str:
        if self.max_increase is not None:
            return f"at most {baseline + self.max_increase:g}"
        ratio = self.max_increase_ratio
        if ratio is None:
            raise ThresholdError(f"threshold {self.metric!r} carries no bound")
        if baseline <= 0.0:
            return "no rise off a zero baseline"
        if self.min_baseline is not None and baseline < self.min_baseline:
            return (
                f"not applied: a baseline of {baseline:g} is below the "
                f"{self.min_baseline:g} floor this ratio needs to mean anything"
            )
        return f"at most {baseline * (1.0 + ratio):g}"


def load_thresholds(path: pathlib.Path = THRESHOLDS_PATH) -> dict[str, Threshold]:
    """Parse `thresholds.yaml`, refusing any entry that cannot justify itself."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ThresholdError(f"{path}: cannot be read: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ThresholdError(f"{path}: is not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ThresholdError(f"{path}: is not a mapping")
    body = document.get("thresholds")
    if not isinstance(body, Mapping) or not body:
        raise ThresholdError(
            f"{path}: has no 'thresholds' mapping. An empty threshold table is a "
            "comparison that can never fail."
        )

    thresholds: dict[str, Threshold] = {}
    for metric, entry in body.items():
        if not isinstance(entry, Mapping):
            raise ThresholdError(f"{path}: threshold {metric!r} is not a mapping")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ThresholdError(
                f"{path}: threshold {metric!r} carries no reason. A guard "
                "threshold moves only with its reason written beside it, so one "
                "without a reason is refused rather than defaulted."
            )
        absolute = entry.get("max_increase")
        ratio = entry.get("max_increase_ratio")
        declared = [value for value in (absolute, ratio) if value is not None]
        if len(declared) != 1:
            raise ThresholdError(
                f"{path}: threshold {metric!r} must declare exactly one of "
                "'max_increase' or 'max_increase_ratio', got "
                f"{len(declared)}. Two bounds on one metric means the looser one "
                "is the real rule and nobody can tell which it is."
            )
        for name, value in (("max_increase", absolute), ("max_increase_ratio", ratio)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ThresholdError(
                    f"{path}: threshold {metric!r} field {name!r} must be a "
                    f"number, got {value!r}"
                )
        floor = entry.get("min_baseline")
        if floor is not None and (
            isinstance(floor, bool) or not isinstance(floor, (int, float))
        ):
            raise ThresholdError(
                f"{path}: threshold {metric!r} field 'min_baseline' must be a "
                f"number, got {floor!r}"
            )
        thresholds[str(metric)] = Threshold(
            metric=str(metric),
            max_increase=None if absolute is None else float(absolute),
            max_increase_ratio=None if ratio is None else float(ratio),
            reason=" ".join(reason.split()),
            min_baseline=None if floor is None else float(floor),
        )
    return thresholds


def run_metrics(run: RunRecord) -> dict[str, float]:
    """The comparable numbers a run yields, all oriented so that rising is worse.

    `duration_seconds` is omitted while a run has not finished rather than
    reported as zero. Zero would enter a comparison as the fastest run ever
    recorded and would then become a baseline nothing could ever match.
    """
    counts = summary(run)
    metrics: dict[str, float] = {
        "checks_failed": float(counts["checks_failed"]),
        "evaluators_failed": float(counts["evaluators_failed"]),
        "evaluators_unavailable": float(counts["evaluators_unavailable"]),
    }
    duration = counts["duration_seconds"]
    if duration is not None:
        metrics["duration_seconds"] = float(duration)
    return metrics


@dataclass(frozen=True)
class Regression:
    """One metric that moved the wrong way by more than its threshold allows."""

    metric: str
    baseline: float
    observed: float
    bound: str
    reason: str

    def described(self) -> str:
        return (
            f"{self.metric}: baseline {self.baseline:g}, now {self.observed:g}, "
            f"{self.bound}. {self.reason}"
        )


@dataclass(frozen=True)
class Comparison:
    """The diff against a tier's promoted baseline, and its verdict.

    `outcome` is one of `release_gate`'s three, and the third is load bearing:
    a comparison with no baseline to compare against reports `unavailable` and
    blocks. A comparison that reported `pass` in that state would go green on
    every brand new tier, which is precisely the tier nobody has checked.
    """

    run_id: str
    tier: Tier
    baseline_run_id: str | None
    outcome: str
    reason: str
    regressions: tuple[Regression, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    baseline_metrics: Mapping[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tier": self.tier.value,
            "baseline_run_id": self.baseline_run_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "regressions": [
                {
                    "metric": item.metric,
                    "baseline": item.baseline,
                    "observed": item.observed,
                    "bound": item.bound,
                    "reason": item.reason,
                }
                for item in self.regressions
            ],
            "metrics": dict(self.metrics),
            "baseline_metrics": dict(self.baseline_metrics),
        }


def _read_registry(store: ArtifactStore) -> dict[str, Any]:
    path = store.baselines_path
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"{path}: cannot be read: {exc}") from exc
    if not isinstance(body, dict):
        raise BaselineError(f"{path}: is not an object")
    return body


def baselines(store: ArtifactStore) -> dict[str, dict[str, Any]]:
    """Every promoted baseline, keyed by tier, as the registry holds them.

    Public because the CI gate has to know WHICH SCENARIO a tier's baseline was
    taken from before it can compare like with like. The registry holds one
    entry per tier, so comparing an arbitrary run of that tier against it would
    diff `duration_seconds` between two different scenarios, and the resulting
    "regression" would be a statement about which scenario ran rather than
    about the code.
    """
    return {
        str(tier): dict(entry)
        for tier, entry in _read_registry(store).items()
        if isinstance(entry, dict)
    }


def promote(run_id: str, store: ArtifactStore) -> dict[str, Any]:
    """Record a run as the reference for its tier.

    The metrics are SNAPSHOTTED into the registry rather than read back from the
    run's manifest at comparison time. The manifest is the authority on what the
    run did, and it is also prunable: a baseline that pointed at a deleted
    directory would answer "no baseline" and the gate would go quiet. Copying
    the numbers means a promoted reference survives its own artifacts, and
    `prune` protects the directory anyway so both halves hold.
    """
    run = store.read_run(run_id)
    if run.finished_at is None:
        raise BaselineError(
            f"run {run_id} never finished, so it has no outcome to be a "
            "reference for. Promoting it would set a baseline from a run that "
            "was killed."
        )
    registry = _read_registry(store)
    entry = {
        "run_id": run.run_id,
        "tier": run.tier.value,
        "scenario_id": run.scenario_id,
        "scenario_version": run.scenario_version,
        "status": run.status,
        "git_sha": run.git_sha,
        "promoted_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "metrics": run_metrics(run),
    }
    registry[run.tier.value] = entry
    store.root.mkdir(parents=True, exist_ok=True)
    store.baselines_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return entry


def compare(
    run_id: str,
    store: ArtifactStore,
    *,
    thresholds: Mapping[str, Threshold] | None = None,
) -> Comparison:
    """Diff a run against the promoted baseline for its tier."""
    resolved = dict(thresholds) if thresholds is not None else load_thresholds()
    run = store.read_run(run_id)
    observed = run_metrics(run)
    registry = _read_registry(store)
    entry = registry.get(run.tier.value)

    if entry is None:
        return Comparison(
            run_id=run.run_id,
            tier=run.tier,
            baseline_run_id=None,
            outcome=UNAVAILABLE,
            reason=(
                f"no promoted baseline for tier {run.tier.value!r}. A comparison "
                "with nothing to compare against has computed nothing, and an "
                "uncomputed gate blocks rather than passes. Promote one with "
                "`python -m harness baseline promote <run_id>`."
            ),
            metrics=observed,
            baseline_metrics={},
        )

    baseline_metrics = {
        str(key): float(value) for key, value in (entry.get("metrics") or {}).items()
    }
    regressions: list[Regression] = []
    missing: list[str] = []
    for metric, value in sorted(observed.items()):
        threshold = resolved.get(metric)
        if threshold is None:
            missing.append(metric)
            continue
        if metric not in baseline_metrics:
            # A metric the baseline never carried cannot be diffed. Reported as
            # missing rather than compared against an assumed zero, because an
            # assumed zero makes every first appearance look like a regression.
            missing.append(metric)
            continue
        if threshold.exceeded_by(baseline_metrics[metric], value):
            regressions.append(
                Regression(
                    metric=metric,
                    baseline=baseline_metrics[metric],
                    observed=value,
                    bound=threshold.describe_bound(baseline_metrics[metric]),
                    reason=threshold.reason,
                )
            )

    if regressions:
        return Comparison(
            run_id=run.run_id,
            tier=run.tier,
            baseline_run_id=str(entry.get("run_id") or ""),
            outcome=FAIL,
            reason=f"{len(regressions)} metric(s) regressed against the baseline",
            regressions=tuple(regressions),
            metrics=observed,
            baseline_metrics=baseline_metrics,
        )
    if missing:
        return Comparison(
            run_id=run.run_id,
            tier=run.tier,
            baseline_run_id=str(entry.get("run_id") or ""),
            outcome=UNAVAILABLE,
            reason=(
                "no threshold or no baseline value for: "
                + ", ".join(sorted(missing))
                + ". A metric nobody set a bound for cannot regress, which reads "
                "as a pass and is not one."
            ),
            metrics=observed,
            baseline_metrics=baseline_metrics,
        )
    return Comparison(
        run_id=run.run_id,
        tier=run.tier,
        baseline_run_id=str(entry.get("run_id") or ""),
        outcome=PASS,
        reason=f"{len(observed)} metric(s) within threshold",
        metrics=observed,
        baseline_metrics=baseline_metrics,
    )
