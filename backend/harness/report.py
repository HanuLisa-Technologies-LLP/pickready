"""Two reports per run: one a machine reads, one a person reads at 02:00.

WHY BOTH, AND WHY THEY ARE BUILT FROM ONE SOURCE
--------------------------------------------------
`report.json` is what `compare`, CI and a dashboard read. `report.md` is what
somebody opens when a build goes red. They are rendered from the same
`RunRecord` in the same function, because the failure that matters here is the
two disagreeing: a recruiter approving a report on screen and mailing a PDF that
reads differently is the exact shape this repository already pins in
`test_the_screen_and_the_pdf_agree_on_the_section_order`.

WHAT THE MARKDOWN HAS TO ANSWER
---------------------------------
Five questions, in this order, because that is the order somebody debugging asks
them: what was tested, what passed, what failed, what was expected against what
actually happened, and where the artifacts are. Everything else is below the
fold.

An unavailable evaluator gets a LINE, never a silent omission.
`reporting.render_lines` already makes that argument ("a dropped row is worse
than a stated absence: the reader assumes the metric was fine and the printer
was terse") and the evaluator table is rendered through it rather than
reimplemented for that reason.

THE DATASET STAMP IS MANDATORY HERE TOO
-----------------------------------------
`EvalReport` refuses construction without a dataset version, because a score
that drops has three candidate explanations and only the stamp separates the
third from the other two. This module does not work around that refusal: a run
minted without dataset versions cannot be reported on, and the message says so.

Provenance: docs/spec/HARNESS.md sections 5 and 7.
"""
from __future__ import annotations

import pathlib
from typing import Any

from app.evaluation.metrics import (
    InsufficientData,
    Interval,
    Measurement,
    wilson_interval,
)
from app.evaluation.reporting import EvalReport, render_lines

from harness.artifacts import ArtifactStore
from harness.run import FAIL, PASS, UNAVAILABLE, RunRecord

REPORT_JSON = "report.json"
REPORT_MD = "report.md"

#: What produced the numbers, stamped on every `EvalReport` this module builds.
#: The harness is not a generic eval script and a report that did not say so
#: could be mistaken for one of `app/scripts/eval_*`'s.
PRODUCED_BY = "harness"


class ReportError(RuntimeError):
    """A report cannot be built from this run, with the reason."""


def dataset_stamp(run: RunRecord) -> str:
    """The versions this run was measured against, as one readable string.

    Several versions rather than one, because a harness run stands on more than
    a golden set: the scenario has its own version and the world builder may
    pin a fixture. Flattening them into one stamp keeps `EvalReport`'s contract
    (one string, always present) while losing nothing, since the parts are also
    carried verbatim in the manifest.
    """
    if not run.dataset_versions:
        raise ReportError(
            f"run {run.run_id} carries no dataset versions. A result that "
            "cannot name what it was measured against cannot distinguish a "
            "regression from a set change, which is why `EvalReport` refuses "
            "to be built without one."
        )
    return ", ".join(
        f"{key}={value}" for key, value in sorted(run.dataset_versions.items())
    )


def _check_pass_rate(run: RunRecord) -> Measurement:
    """The proportion of ground-truth assertions that held, with its interval.

    A proportion over a finite sample, so it gets a Wilson interval from
    `metrics.py` rather than a bare number: a run of four checks all passing is
    not evidence of a 100% pass rate, and an interval is what says so. With no
    checks at all the metric is UNAVAILABLE, never 1.0, because a scenario that
    asserted nothing did not pass everything.
    """
    total = len(run.checks)
    passed = sum(1 for check in run.checks if check.passed)
    try:
        interval = wilson_interval(passed, total)
    except InsufficientData as exc:
        return Measurement.unavailable(
            f"no ground-truth assertion was evaluated ({exc})"
        )
    return Measurement.measured(passed / total, interval, sample_size=total)


def build_eval_report(run: RunRecord) -> EvalReport:
    """The run's measurements, in the envelope that refuses a bare float.

    Every evaluator becomes one measurement. An evaluator that reported a score
    with an interval becomes a measured one; everything else, including a plain
    pass or fail verdict, becomes `unavailable` carrying its reason. That is not
    a downgrade of a passing verdict: a verdict is not a measurement, and
    rendering "pass" as 1.0 would put a yes/no answer into a numeric diff where
    a threshold could be set against it.
    """
    measurements: dict[str, Measurement] = {"check_pass_rate": _check_pass_rate(run)}
    for outcome in run.evaluations:
        if outcome.score is not None and outcome.interval is not None:
            low, high = outcome.interval
            measurements[outcome.evaluator] = Measurement.measured(
                outcome.score,
                Interval(low, high),
                sample_size=outcome.sample_size,
                note=outcome.reason,
            )
        else:
            measurements[outcome.evaluator] = Measurement.unavailable(
                f"{outcome.outcome}: {outcome.reason}",
                sample_size=outcome.sample_size,
            )
    return EvalReport(
        surface=f"{run.scenario_id}@v{run.scenario_version} ({run.tier.value})",
        dataset_version=dataset_stamp(run),
        produced_by=PRODUCED_BY,
        measurements=measurements,
        context={
            "run_id": run.run_id,
            "git": run.git.described,
            "dispatch_backend": run.dispatch_backend,
            "seed": run.seed,
            "status": run.status,
        },
        notes=tuple(
            f"fault injected: {fault.name}"
            + (f" {dict(fault.params)}" if fault.params else "")
            for fault in run.faults
        ),
    )


def summary(run: RunRecord) -> dict[str, Any]:
    """The counts `compare` reads, computed once so the two reports cannot differ."""
    return {
        "status": run.status,
        "checks_total": len(run.checks),
        "checks_failed": len(run.failed_checks),
        "evaluators_total": len(run.evaluations),
        "evaluators_failed": len(run.failed_evaluations),
        "evaluators_unavailable": len(run.unavailable_evaluations),
        "duration_seconds": run.duration_seconds,
    }


def _fence(value: Any) -> str:
    """Render one side of an expected/actual pair without swallowing its shape.

    `None` renders as the words "not observed" rather than as an empty cell,
    because an empty cell in a failure report reads as "nothing was wrong here".
    """
    if value is None:
        return "`not observed`"
    text = str(value)
    if len(text) > 300:
        text = text[:297] + "..."
    return "`" + text.replace("`", "'") + "`"


def render_markdown(
    run: RunRecord, *, artifact_dir: pathlib.Path, description: str = ""
) -> str:
    """The human report. Ordered by the questions a reader actually asks.

    `description` is the scenario's own prose. It is a parameter rather than a
    field on `RunRecord` because the manifest is deliberately the minimum set of
    facts a replay needs, and a paragraph of prose reproduces nothing.
    """
    report = build_eval_report(run)
    counts = summary(run)
    status_word = {PASS: "PASSED", FAIL: "FAILED", UNAVAILABLE: "UNAVAILABLE"}.get(
        run.status, run.status
    )

    lines: list[str] = [
        f"# {run.scenario_id} v{run.scenario_version}: {status_word}",
        "",
        f"Tier `{run.tier.value}`, run `{run.run_id}`.",
        "",
        "| | |",
        "|---|---|",
        f"| Commit | {run.git.described} |",
        f"| Started | {run.started_at} |",
        f"| Finished | {run.finished_at or 'did not finish'} |",
        "| Duration | "
        + (
            "unknown"
            if counts["duration_seconds"] is None
            else f"{counts['duration_seconds']:.1f}s"
        )
        + " |",
        f"| Dispatch backend | `{run.dispatch_backend}` |",
        f"| Seed | `{run.seed}` |",
        f"| Datasets | {report.dataset_version} |",
        "",
        "## What was tested",
        "",
    ]
    if description.strip():
        lines.extend([" ".join(description.split()), ""])
    lines.extend(
        [
            f"World `{run.world}`.",
            "",
            "Workload: "
            + (", ".join(f"`{step}`" for step in run.workload) or "none declared")
            + ".",
            "",
            "Faults injected: "
            + (
                ", ".join(
                    f"`{fault.name}`"
                    + (f" {dict(fault.params)}" if fault.params else "")
                    for fault in run.faults
                )
                or "none"
            )
            + ".",
            "",
            "## Ground truth",
            "",
        ]
    )

    if not run.checks:
        lines.extend(
            [
                "No assertions were evaluated. This is not a pass: a run that "
                "asserted nothing cannot distinguish a working system from a "
                "silent one.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"{counts['checks_total'] - counts['checks_failed']} of "
                f"{counts['checks_total']} assertions held.",
                "",
                "| | Kind | Assertion | Expected | Actual |",
                "|---|---|---|---|---|",
            ]
        )
        # Failures first. A reader scanning a red report should not have to
        # page past twenty passing rows to reach the one that broke.
        ordered = sorted(run.checks, key=lambda check: check.passed)
        for check in ordered:
            mark = "ok" if check.passed else "**FAIL**"
            lines.append(
                f"| {mark} | {check.kind} | {check.expression} | "
                f"{_fence(check.expected)} | {_fence(check.actual)} |"
            )
        lines.append("")

    lines.extend(["## Evaluators", ""])
    if not run.evaluations:
        lines.extend(["No evaluator reported on this run.", ""])
    lines.extend(["```", *render_lines(report), "```", ""])

    failing = run.failed_checks
    failing_evaluators = run.failed_evaluations
    if failing or failing_evaluators:
        lines.extend([f"## Why {run.scenario_id} failed", ""])
        for check in failing:
            lines.append(
                f"- `{check.kind}` assertion `{check.expression}` expected "
                f"{_fence(check.expected)} and saw {_fence(check.actual)}."
                + (f" {check.note}" if check.note else "")
            )
        for outcome in failing_evaluators:
            lines.append(f"- evaluator `{outcome.evaluator}`: {outcome.reason}")
        lines.append("")

    unavailable = run.unavailable_evaluations
    if unavailable:
        lines.extend(
            [
                "## Could not be evaluated",
                "",
                "An evaluator that cannot compute reports `unavailable`, which "
                "blocks rather than passes.",
                "",
            ]
        )
        for outcome in unavailable:
            lines.append(f"- `{outcome.evaluator}`: {outcome.reason}")
        lines.append("")

    lines.extend(
        [
            "## Artifacts",
            "",
            f"`{artifact_dir}`",
            "",
            f"Replay this run with `python -m harness replay {run.run_id}`.",
            "",
        ]
    )
    return "\n".join(lines)


def write(
    run: RunRecord, store: ArtifactStore, *, description: str = ""
) -> tuple[pathlib.Path, pathlib.Path]:
    """Emit both reports into the run's artifact directory.

    `description` is the scenario's own prose, which the manifest does not carry
    (it is not needed to reproduce anything) but the human report badly wants.
    Passed in rather than stored on the run so that the manifest stays the
    minimum set of facts a replay needs.
    """
    body = {
        "contract": run.contract,
        "run": run.to_manifest(),
        "summary": summary(run),
        "evaluation": build_eval_report(run).as_dict(),
    }
    json_path = store.write_json(run.run_id, REPORT_JSON, body)
    markdown = render_markdown(
        run, artifact_dir=store.run_dir(run.run_id), description=description
    )
    md_path = store.write_text(run.run_id, REPORT_MD, markdown)
    return json_path, md_path
