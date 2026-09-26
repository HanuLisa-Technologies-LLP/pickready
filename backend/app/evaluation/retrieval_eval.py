"""Scoring a retrieval run against the golden set. Offline, judge free, arithmetic.

WHY THIS IS THE BACKBONE (RPN-AI-UP-001 W7.3)
----------------------------------------------
"These are pure arithmetic, they run offline, they never call a model, and they
are eligible to be a CI gate. They are the backbone of the whole eval programme
for exactly that reason." Everything else in W7 needs either a judge or human
labels. This needs neither, so it is the one part that can gate a release today.

WHAT IS SCORED, AND WHAT A RUN IS
----------------------------------
A RUN is a ranked list of chunk ids per query. It is the interface between "a
retrieval system executed" and "we measured it", and keeping it a file is what
makes the measurement offline: nothing here opens a database, embeds a query or
calls a provider.

The only run that ships is `reference-fixture`, hand authored, and it is
`gate_eligible = False`. Its numbers measure this harness and say nothing about
the product retriever, because there is no product retriever to record: the RAG
index has no writer with a caller and `context_chunks` holds zero rows
(`docs/verification/AI_UPGRADE_BASELINE.md` finding 2).

So this module answers TWO questions and never blurs them:

  1. THE HARNESS SELF CHECK. Do the fixture's stamped expectations still hold?
     This is a real regression gate on the metric code -- change the discount,
     the cutoff handling or the gain scale and it goes red -- and it runs today.
  2. THE RETRIEVAL QUALITY QUESTION. Is the retriever good enough to ship? That
     needs a recorded run and reports `unavailable` with the reason until one
     exists. It is never answered with a zero and never answered by the fixture.

A CUTOFF DEEPER THAN THE RUN IS UNAVAILABLE, NOT SMALLER
----------------------------------------------------------
nDCG@100 over a depth-20 run is nDCG@20 wearing a different name, and recall@200
over it is recall@20. Both are arithmetically defined and both would be read as
what their names say. So a cutoff above the run's depth reports `unavailable`
with the depth in the reason, and becomes measured with no code change the day a
deeper run is recorded. THIS IS THE POINT W7.3 IS MAKING: recall must be
measured at the candidate generation k of 100 to 200, and you cannot measure it
there without a run that deep, whatever arithmetic you are willing to perform.

Provenance: RPN-AI-UP-001 W7.3, W7.1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.evaluation import golden, metrics
from app.evaluation.metrics import Interval, Measurement
from app.evaluation.reporting import EvalReport

#: The cutoffs reported, and why each one is here.
#:
#: 20 is the acceptance criterion's recall cutoff. 100 and 200 bracket the
#: production CANDIDATE GENERATION k: a reranker can only reorder what retrieval
#: returned, so recall at the generation depth is the ceiling on everything
#: downstream, and recall at the final k measures retrieval and reranking
#: together while being unable to say which one moved.
RECALL_CUTOFFS: tuple[int, ...] = (20, 100, 200)
CANDIDATE_GENERATION_CUTOFFS: tuple[int, ...] = (100, 200)

#: 10 is what a reader sees; 100 is the more stable estimator, because one item
#: moving between rank 9 and rank 11 swings the shallow figure and barely moves
#: the deep one. 20 sits between them and is reported because it is the deepest
#: cutoff the shipped fixture can actually support.
NDCG_CUTOFFS: tuple[int, ...] = (10, 20, 100)

#: DELIBERATELY EMPTY. A quality threshold is set from a measured baseline, and
#: there is no baseline: no run in this repository was produced by executing
#: retrieval against an index. A threshold invented before the first measurement
#: is a number that passes by construction and gets tuned until it keeps
#: passing. The gate reports the quality question `unavailable` instead, and the
#: first recorded run is what turns thresholds into a decision somebody can make
#: from evidence.
QUALITY_THRESHOLDS: dict[str, float] = {}

#: How closely a recomputed metric must match the fixture's stamped value. Tight
#: because both sides are the same arithmetic on the same inputs in the same
#: process; the only tolerance needed is floating-point summation order.
HARNESS_TOLERANCE = 1e-9


@dataclass(frozen=True)
class HarnessCheck:
    """Did the metric implementation still produce the fixture's stamped values."""

    status: str
    reason: str
    differences: tuple[str, ...] = ()

    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"status": self.status, "reason": self.reason}
        if self.differences:
            body["differences"] = list(self.differences)
        return body


@dataclass(frozen=True)
class RetrievalEvaluation:
    report: EvalReport
    harness: HarnessCheck
    by_stratum: dict[str, dict[str, Measurement]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        body = self.report.as_dict()
        body["harness_self_check"] = self.harness.as_dict()
        body["by_stratum"] = {
            axis: {value: item.as_dict() for value, item in sorted(rows.items())}
            for axis, rows in sorted(self.by_stratum.items())
        }
        return body


def _macro(values: Sequence[float], *, note: str | None = None) -> Measurement:
    """Mean over queries with its interval, or a refusal naming the shortfall.

    THE INTERVAL IS CLAMPED TO [0, 1] AND SAYS SO WHEN IT CLAMPED. Recall, nDCG
    and reciprocal rank are all bounded, and the normal approximation is not: at
    three queries it will happily print an upper bound of 1.32, which is not a
    recall and reads as a bug in the metric rather than as what it is, a signal
    that three observations cannot support an interval. Clamping silently would
    hide that signal, so the clamp is recorded in the note beside the number.

    THIS IS ALSO THE ONE PLACE THE RAISE BECOMES A RENDERED REASON. The rule
    about how few observations can support an interval lives in `mean_interval`
    and is not restated here: a second copy of the threshold would drift from
    the first, and the two would then disagree about the same set.
    """
    try:
        raw = metrics.mean_interval(values)
    except metrics.InsufficientData as exc:
        return Measurement.unavailable(exc.reason, sample_size=len(values))
    mean = sum(values) / len(values)
    clamped = Interval(max(0.0, raw.low), min(1.0, raw.high))
    notes = [note] if note else []
    if clamped.low != raw.low or clamped.high != raw.high:
        notes.append(
            f"the normal approximation ran outside [0, 1] at n={len(values)} "
            f"(raw [{raw.low:.4f}, {raw.high:.4f}]) and was clamped; read the "
            "width as indicative, not as coverage"
        )
    if clamped.width == 0.0:
        # Every query scored identically, so the sample variance is zero and the
        # normal approximation reports a zero-width interval. Arithmetically
        # correct and badly misleading: n identical observations do not license
        # certainty, and an interval of [1.0000, 1.0000] is the single most
        # confident-looking thing this report can print.
        notes.append(
            f"all {len(values)} observations are identical, so the sample "
            "variance is zero and the interval has no width; that is an "
            "artefact of the estimator at this sample size, not certainty"
        )
    return Measurement.measured(
        mean,
        clamped,
        sample_size=len(values),
        note="; ".join(notes) if notes else None,
    )


def _cutoff_unavailable(kind: str, k: int, depth: int) -> Measurement:
    return Measurement.unavailable(
        f"{kind}@{k} needs a run of depth {k}; the run scored here has depth "
        f"{depth}, and computing it anyway would report {kind}@{depth} under "
        f"the name {kind}@{k}"
    )


def evaluate(
    dataset: golden.RetrievalSet,
    run: golden.RetrievalRun,
    availability: golden.SetAvailability,
) -> RetrievalEvaluation:
    """Score one run against the golden set and render the result honestly."""
    covered = [case for case in dataset.cases if case.query_id in run.results]
    uncovered = [case.query_id for case in dataset.cases if case.query_id not in run.results]

    measurements: dict[str, Measurement] = {}
    by_stratum: dict[str, dict[str, Measurement]] = {}

    if not covered:
        reason = (
            f"run {run.run_id} ranks nothing for any of the "
            f"{len(dataset.cases)} golden queries"
        )
        for k in RECALL_CUTOFFS:
            measurements[f"recall@{k}"] = Measurement.unavailable(reason)
        for k in NDCG_CUTOFFS:
            measurements[f"ndcg@{k}"] = Measurement.unavailable(reason)
        measurements["mrr"] = Measurement.unavailable(reason)
        measurements["recall_micro@20"] = Measurement.unavailable(reason)
        return RetrievalEvaluation(
            report=_envelope(dataset, run, availability, measurements, uncovered),
            harness=HarnessCheck(
                HarnessCheck.UNAVAILABLE, reason
            ),
            by_stratum=by_stratum,
        )

    recomputed: dict[str, float] = {}

    # ── Recall, macro and micro ──────────────────────────────────────────────
    #
    # MACRO is the mean of per-query recall: every query counts once, so a query
    # with one relevant chunk weighs the same as one with five. MICRO pools the
    # relevant ITEMS: found over total, which is a genuine binomial proportion
    # and therefore the one the Wilson interval belongs to. They answer
    # different questions and a set with uneven qrel counts separates them, so
    # both are reported rather than one being picked.
    for k in RECALL_CUTOFFS:
        if k > run.depth:
            measurements[f"recall@{k}"] = _cutoff_unavailable("recall", k, run.depth)
            continue
        per_query = [
            metrics.recall_at_k(run.results[case.query_id], case.relevant_ids, k)
            for case in covered
        ]
        note = (
            "the production candidate generation k; a reranker can only "
            "reorder what retrieval returned"
            if k in CANDIDATE_GENERATION_CUTOFFS
            else None
        )
        measurements[f"recall@{k}"] = _macro(per_query, note=note)
        recomputed[f"recall_at_{k}"] = sum(per_query) / len(per_query)

    micro_k = 20
    if micro_k <= run.depth:
        found = 0
        total = 0
        for case in covered:
            window = set(run.results[case.query_id][:micro_k])
            relevant = set(case.relevant_ids)
            found += len(relevant & window)
            total += len(relevant)
        if total:
            measurements[f"recall_micro@{micro_k}"] = Measurement.measured(
                found / total,
                metrics.wilson_interval(found, total),
                sample_size=total,
                note=(
                    "pooled over relevant chunks rather than over queries, so "
                    "the interval is a Wilson score interval on a genuine "
                    "binomial proportion"
                ),
            )
        else:
            measurements[f"recall_micro@{micro_k}"] = Measurement.unavailable(
                "no query in the covered set grades any chunk as relevant"
            )
    else:
        measurements[f"recall_micro@{micro_k}"] = _cutoff_unavailable(
            "recall_micro", micro_k, run.depth
        )

    # ── nDCG ─────────────────────────────────────────────────────────────────
    for k in NDCG_CUTOFFS:
        if k > run.depth:
            measurements[f"ndcg@{k}"] = _cutoff_unavailable("ndcg", k, run.depth)
            continue
        per_query = [
            metrics.ndcg_at_k(run.results[case.query_id], case.qrels, k)
            for case in covered
        ]
        measurements[f"ndcg@{k}"] = _macro(per_query)
        recomputed[f"ndcg_at_{k}"] = sum(per_query) / len(per_query)

    # ── MRR ──────────────────────────────────────────────────────────────────
    reciprocals = [
        metrics.reciprocal_rank(run.results[case.query_id], case.relevant_ids)
        for case in covered
    ]
    measurements["mrr"] = _macro(reciprocals)
    recomputed["mrr"] = metrics.mrr(reciprocals)

    # ── Per stratum, and the evidence tier axis in particular ────────────────
    for axis in golden.AXES:
        rows: dict[str, Measurement] = {}
        for value in golden.AXES[axis]:
            subset = [case for case in covered if getattr(case.strata, axis) == value]
            if not subset:
                rows[value] = Measurement.unavailable(
                    f"no golden query occupies {axis}={value}"
                )
                continue
            if 20 > run.depth:
                rows[value] = _cutoff_unavailable("recall", 20, run.depth)
                continue
            rows[value] = _macro(
                [
                    metrics.recall_at_k(run.results[case.query_id], case.relevant_ids, 20)
                    for case in subset
                ]
            )
        by_stratum[axis] = rows

    harness = _harness_check(run, recomputed)
    return RetrievalEvaluation(
        report=_envelope(dataset, run, availability, measurements, uncovered),
        harness=harness,
        by_stratum=by_stratum,
    )


def _harness_check(run: golden.RetrievalRun, recomputed: dict[str, float]) -> HarnessCheck:
    """Compare the recomputed values against the run's stamped expectations.

    A `recorded` run carries none and this reports `unavailable`: there is
    nothing to pin, because its numbers ARE the measurement. Only the fixture
    stamps expectations, and the fixture exists to make the metric arithmetic
    itself gate-able.
    """
    if not run.expected_metrics:
        return HarnessCheck(
            HarnessCheck.UNAVAILABLE,
            f"run {run.run_id} stamps no expected metrics, so there is nothing "
            "for the harness self check to compare against",
        )
    differences: list[str] = []
    for key, expected in sorted(run.expected_metrics.items()):
        if key not in recomputed:
            differences.append(f"{key}: stamped {expected!r} but not recomputed here")
            continue
        actual = recomputed[key]
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=HARNESS_TOLERANCE):
            differences.append(f"{key}: stamped {expected!r}, recomputed {actual!r}")
    if differences:
        return HarnessCheck(
            HarnessCheck.FAILED,
            "the metric implementation no longer reproduces the fixture's "
            "stamped values; regenerate them deliberately, never to make a red "
            "gate green",
            tuple(differences),
        )
    return HarnessCheck(
        HarnessCheck.PASSED,
        f"{len(run.expected_metrics)} stamped values reproduced within "
        f"{HARNESS_TOLERANCE}",
    )


def quality_verdict(
    run: golden.RetrievalRun, availability: golden.SetAvailability
) -> tuple[str, str]:
    """Whether the retrieval QUALITY question can be answered, and why not.

    Separate from the harness check on purpose. One asks whether the ruler still
    measures the same way; the other asks whether the thing measured is good
    enough to ship, and only the second needs a real retrieval execution behind
    it.
    """
    blocks: list[str] = []
    if not run.gate_eligible:
        blocks.append(
            f"run {run.run_id} has provenance {run.provenance!r}; only a "
            "recorded run, produced by executing retrieval against an index, "
            "can carry a quality claim"
        )
    if not availability.gate_eligible:
        blocks.append(availability.gate_block_reason or "the golden set cannot gate")
    if not QUALITY_THRESHOLDS:
        blocks.append(
            "no quality threshold is set, because none has ever been measured; "
            "a threshold invented before the first baseline passes by "
            "construction"
        )
    if blocks:
        return ("unavailable", "; ".join(blocks))
    return ("available", "")


def _envelope(
    dataset: golden.RetrievalSet,
    run: golden.RetrievalRun,
    availability: golden.SetAvailability,
    measurements: dict[str, Measurement],
    uncovered: list[str],
) -> EvalReport:
    status, reason = quality_verdict(run, availability)
    notes = [
        "The harness self check and the retrieval quality question are "
        "different questions and are reported separately.",
        f"Retrieval quality: {status}. {reason}" if reason else f"Retrieval quality: {status}.",
    ]
    if uncovered:
        notes.append(
            f"{len(uncovered)} golden queries are absent from this run and were "
            "excluded from every figure above: " + ", ".join(sorted(uncovered))
        )
    context: dict[str, Any] = {
        "run_id": run.run_id,
        "run_provenance": run.provenance,
        "run_depth": run.depth,
        "corpus_chunks": len(dataset.corpus),
        "golden_queries": len(dataset.cases),
        "queries_scored": len(dataset.cases) - len(uncovered),
        "set_availability": availability.as_dict(),
        "stratification": golden.stratification(dataset.cases),
        "quality_question": {"status": status, "reason": reason},
    }
    return EvalReport(
        surface="retrieval",
        dataset_version=dataset.version,
        produced_by="app.evaluation.retrieval_eval",
        measurements=measurements,
        context=context,
        notes=tuple(notes),
    )


def evaluate_default(run_id: str = "reference-fixture") -> RetrievalEvaluation:
    """Load the shipped set and one run, and score it. The whole offline path."""
    dataset = golden.load_retrieval_set()
    run = golden.load_run(run_id)
    availability = golden.availability(golden.SET_RETRIEVAL, dataset.cases)
    return evaluate(dataset, run, availability)


def judged_set_report(name: str) -> EvalReport:
    """The reasoning or decision set's answer today: unavailable, with the reason.

    It is a REPORT rather than a skipped section. A surface that prints nothing
    when it has nothing reads as a surface nobody built; one that prints
    "unavailable" and why is the difference between an unfinished eval and an
    honest one.
    """
    judged = golden.load_judged_set(name)
    availability = golden.availability(name, judged.cases, judged.why_empty)
    reason = availability.reason
    measurements = {
        "mcc": Measurement.unavailable(reason),
        "cohens_kappa": Measurement.unavailable(reason),
        "accuracy": Measurement.unavailable(reason),
    }
    return EvalReport(
        surface=f"{name}_set",
        dataset_version=judged.version,
        produced_by="app.evaluation.retrieval_eval",
        measurements=measurements,
        context={
            "cases": len(judged.cases),
            "set_availability": availability.as_dict(),
        },
        notes=(
            "Human labelled ground truth is required and does not exist. "
            "Ground truth produced by the same class of model being evaluated "
            "measures agreement with that model, not quality.",
        ),
    )


__all__ = [
    "CANDIDATE_GENERATION_CUTOFFS",
    "HARNESS_TOLERANCE",
    "HarnessCheck",
    "NDCG_CUTOFFS",
    "QUALITY_THRESHOLDS",
    "RECALL_CUTOFFS",
    "RetrievalEvaluation",
    "evaluate",
    "evaluate_default",
    "judged_set_report",
    "quality_verdict",
]
