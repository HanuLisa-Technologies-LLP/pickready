"""The numbers that say whether an agent got better, and what each one hides.

WHY SEVERAL METRICS PER AGENT
------------------------------
Every single metric here can be gamed by a change that makes the product worse.
Precision@5 rises if the ranker collapses onto one obvious profile shape;
diversity rises if it ranks randomly. Generic-language rate falls to zero if
remarks become terse and uninformative. They are reported together because the
combination is much harder to move in the wrong direction than any one of them.

WHAT IS MEASURABLE WITHOUT A HUMAN, AND WHAT IS NOT
----------------------------------------------------
Structural metrics -- word ranges, evidence sourcing, generic-language rate,
behavioural phrasing, no-numbers compliance -- are computed from the output
alone and need no labels. Quality metrics -- is this the right ranking, is this
report insightful -- need ground truth a recruiting expert produced, and this
module computes them only when that ground truth exists. It never estimates
them, because an estimated quality metric is a number that moves when the
estimator changes and looks exactly like progress.

INSUFFICIENT INPUT RAISES; THE REPORT DEGRADES (RPN-AI-UP-001 W7.5)
--------------------------------------------------------------------
Every function below raises `InsufficientData`, naming the reason, rather than
returning 0.0 for a quantity it could not measure. This is the split this
codebase already lives by -- tools raise, loops degrade -- applied to the eval
layer: `Measurement.unavailable(reason)` is the only way a caller renders an
unmeasured quantity, and it carries no numeric field at all, so a dashboard
cannot accidentally plot it as zero. Scoring an unmeasured thing 0.0 counts an
abstention as a failure, which is precisely the error W7.5 forbids.

NO MODEL CALL, NO NETWORK, NO DATABASE
---------------------------------------
Everything here is arithmetic over inputs the caller supplies. A rate that
moves means the CODE changed, not that a provider sampled differently, which is
the property that makes any of these eligible to be a CI gate (W7.3).

Provenance: RPN-AI-UP-001 W7.3 (retrieval metrics), W7.5 (judge reporting).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


# ── Insufficient input, and how it is reported ───────────────────────────────


class InsufficientData(ValueError):
    """A metric's inputs cannot support it, with the reason a reader needs.

    Raised, never swallowed into a default. The caller that wants a renderable
    answer catches it and builds `Measurement.unavailable(str(exc))`; a caller
    that has no honest answer to give lets it propagate.
    """

    def __init__(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("InsufficientData needs a reason a reader can act on")
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Interval:
    """A closed interval. `low <= high` is checked, because a reversed pair
    renders as a plausible-looking range and is read by nobody as an error."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"interval low {self.low} exceeds high {self.high}")

    def as_dict(self) -> dict[str, float]:
        return {"low": round(self.low, 6), "high": round(self.high, 6)}

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class Measurement:
    """A number that knows whether it is a number.

    THE POINT OF THIS TYPE is `as_dict`: an unavailable measurement serialises
    with NO numeric key. A dashboard, a JSON consumer or a gate cannot read a
    zero out of it, because there is nothing numeric to read. That is the
    mechanical form of "unavailable, never 0.0".

    An interval is mandatory on a measured value. Every quantity this package
    produces is an estimate over a finite sample, and a point estimate with no
    dispersion beside it is what turns eval noise into a release decision.
    """

    value: float | None
    interval: Interval | None
    unavailable_reason: str | None
    sample_size: int | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        measured = self.value is not None
        if measured == (self.unavailable_reason is not None):
            raise ValueError(
                "a Measurement is either measured or unavailable, never both "
                "and never neither"
            )
        if measured and self.interval is None:
            raise ValueError("a measured value must carry its interval")
        if not measured and self.interval is not None:
            raise ValueError("an unavailable measurement has no interval")
        if self.unavailable_reason is not None and not self.unavailable_reason.strip():
            raise ValueError("an unavailable measurement must say why")

    @classmethod
    def measured(
        cls,
        value: float,
        interval: Interval,
        *,
        sample_size: int | None = None,
        note: str | None = None,
    ) -> "Measurement":
        return cls(float(value), interval, None, sample_size, note)

    @classmethod
    def unavailable(
        cls, reason: str, *, sample_size: int | None = None, note: str | None = None
    ) -> "Measurement":
        return cls(None, None, reason, sample_size, note)

    @property
    def available(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict[str, Any]:
        if not self.available:
            body: dict[str, Any] = {
                "status": "unavailable",
                "reason": self.unavailable_reason,
            }
        else:
            if self.interval is None:  # guaranteed by __post_init__
                raise ValueError("a measured value must carry its interval")
            body = {
                "status": "measured",
                "value": round(float(self.value or 0.0), 6),
                "interval": self.interval.as_dict(),
            }
        if self.sample_size is not None:
            body["sample_size"] = self.sample_size
        if self.note:
            body["note"] = self.note
        return body


# ── Confidence intervals ─────────────────────────────────────────────────────

#: Two-sided standard-normal quantiles. A table rather than an inverse-CDF
#: implementation: three levels are the ones anybody asks for, an unlisted level
#: RAISES, and a wrong confidence interval is a wrong release decision, so the
#: arithmetic here has to be checkable by eye against a published table.
_Z: dict[float, float] = {
    0.90: 1.6448536269514722,
    0.95: 1.9599639845400545,
    0.99: 2.5758293035489004,
}

DEFAULT_CONFIDENCE = 0.95


def z_for(confidence: float) -> float:
    """The quantile, or a refusal naming the levels that exist."""
    key = round(float(confidence), 4)
    if key not in _Z:
        raise InsufficientData(
            f"confidence {confidence} is not tabulated; available: "
            + ", ".join(str(level) for level in sorted(_Z))
        )
    return _Z[key]


def wilson_interval(
    successes: int, trials: int, confidence: float = DEFAULT_CONFIDENCE
) -> Interval:
    """Wilson score interval for a binomial proportion.

    WHY WILSON AND NOT THE NORMAL APPROXIMATION. The textbook
    `p +/- z*sqrt(p(1-p)/n)` degenerates exactly where a retrieval eval spends
    its time: at p near 0 or 1 it produces a zero-width interval, and on a set
    of 24 queries where 24 succeeded it would report "recall is 1.000, interval
    [1.000, 1.000]", which claims certainty from twenty-four observations. The
    Wilson interval keeps width at the boundaries and stays inside [0, 1].

    Formula (Wilson 1927), with p = successes/trials:

        denominator = 1 + z^2/n
        centre      = (p + z^2/(2n)) / denominator
        margin      = z * sqrt(p(1-p)/n + z^2/(4n^2)) / denominator

    Provenance: RPN-AI-UP-001 W7.3, "extend it with ... a Wilson score interval".
    """
    if trials <= 0:
        raise InsufficientData("a proportion needs at least one trial")
    if successes < 0 or successes > trials:
        raise ValueError(f"{successes} successes in {trials} trials is not a count")
    z = z_for(confidence)
    n = float(trials)
    p = successes / n
    denominator = 1.0 + (z * z) / n
    centre = (p + (z * z) / (2.0 * n)) / denominator
    margin = (
        z * math.sqrt((p * (1.0 - p)) / n + (z * z) / (4.0 * n * n))
    ) / denominator
    return Interval(max(0.0, centre - margin), min(1.0, centre + margin))


def mean_interval(
    values: Sequence[float], confidence: float = DEFAULT_CONFIDENCE
) -> Interval:
    """Normal-approximation interval for the mean of per-query scores.

    nDCG is continuous, not binomial, so Wilson does not apply to it. This is
    the ordinary `mean +/- z * s / sqrt(n)` with the SAMPLE standard deviation
    (n-1 in the denominator).

    IT IS NAMED FOR WHAT IT IS. It is not a bootstrap and it does not claim
    bootstrap coverage: on the small sets this package ships, the normal
    approximation is the weaker estimator and saying so is cheaper than
    implying a resampling procedure that is not running. A caller that needs
    coverage guarantees at n below thirty should read the width as indicative.

    Fewer than two values RAISES: a "spread" computed from one observation is
    zero, and a zero-width interval printed beside a single measurement is the
    most confident-looking wrong answer this module could produce.
    """
    sample = [float(value) for value in values]
    if len(sample) < 2:
        raise InsufficientData(
            f"a mean interval needs at least two observations, got {len(sample)}"
        )
    n = float(len(sample))
    mean = sum(sample) / n
    variance = sum((value - mean) ** 2 for value in sample) / (n - 1.0)
    margin = z_for(confidence) * math.sqrt(variance / n)
    return Interval(mean - margin, mean + margin)


# ── Retrieval quality (W7.3): judge free, offline, gate eligible ─────────────
#
# Ground truth here is a query paired with the chunk ids that answer it. That
# pair is objectively checkable, which is why W7.1 permits the retrieval set to
# be built without an expert rating every case: a person verifies a chunk id
# cheaply, and nothing about the pair is a matter of opinion.


def _positive_gains(gains: Mapping[Any, float]) -> dict[Any, float]:
    return {key: float(value) for key, value in gains.items() if float(value) > 0.0}


def recall_at_k(retrieved: Sequence[Any], relevant: Iterable[Any], k: int) -> float:
    """Share of the relevant items that appear in the first k results.

    MEASURE THIS AT THE CANDIDATE GENERATION K, not at the final k. A reranker
    can only reorder what retrieval returned, so recall at the generation depth
    (100 to 200 in production) is the ceiling on everything downstream; recall
    at the final k measures the reranker and retrieval together and cannot tell
    you which one regressed. That is the whole disentangling idea of W7.1.
    """
    truth = {item for item in relevant}
    if not truth:
        raise InsufficientData("recall is undefined with no relevant items")
    if k <= 0:
        raise InsufficientData(f"recall@{k} is undefined for a non-positive k")
    window = list(retrieved)[:k]
    return len(truth.intersection(window)) / len(truth)


def dcg(gains: Sequence[float]) -> float:
    """Discounted cumulative gain with the standard log2(rank + 1) discount."""
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def ndcg_at_k(retrieved: Sequence[Any], gains: Mapping[Any, float], k: int) -> float:
    """Normalised DCG over graded relevance, at an explicit cutoff.

    REPORT BOTH A SHALLOW AND A DEEP CUTOFF. nDCG@10 is what a user sees;
    nDCG@100 is the more stable estimator, because a single item moving between
    rank 9 and rank 11 swings the shallow figure and barely moves the deep one.
    A release decision made on the shallow number alone is a decision made on
    the noisier of the two.

    Provenance: RPN-AI-UP-001 W7.3, "Report nDCG@10 and nDCG@100; the deeper
    cutoff is the more stable estimator."
    """
    if k <= 0:
        raise InsufficientData(f"nDCG@{k} is undefined for a non-positive k")
    positive = _positive_gains(gains)
    if not positive:
        raise InsufficientData("nDCG is undefined with no positively graded item")
    actual = dcg([positive.get(item, 0.0) for item in list(retrieved)[:k]])
    ideal = dcg(sorted(positive.values(), reverse=True)[:k])
    if ideal <= 0.0:
        raise InsufficientData("the ideal ranking carries no gain at this cutoff")
    return actual / ideal


def reciprocal_rank(retrieved: Sequence[Any], relevant: Iterable[Any]) -> float:
    """1/rank of the first relevant item, or 0.0 when none was retrieved.

    THE ZERO HERE IS MEASURED, not unavailable. "We searched and found nothing
    relevant in this ranking" is a real observation with a real value; it is a
    different thing from "we could not measure this", and conflating the two is
    the error the rest of this module exists to prevent.
    """
    truth = {item for item in relevant}
    if not truth:
        raise InsufficientData("reciprocal rank is undefined with no relevant items")
    for index, item in enumerate(retrieved, start=1):
        if item in truth:
            return 1.0 / index
    return 0.0


def mrr(per_query: Sequence[float]) -> float:
    """Mean reciprocal rank across queries."""
    if not per_query:
        raise InsufficientData("MRR needs at least one query")
    return sum(float(value) for value in per_query) / len(per_query)


# ── Judge agreement (W7.5): never raw agreement alone ────────────────────────


@dataclass(frozen=True)
class ConfusionMatrix:
    """Truth on the rows, prediction on the columns.

    Carried on every judge result because kappa and MCC are both summaries and
    both hide the same thing: WHICH way the judge is wrong. A judge that calls
    everything "met" and a judge that is randomly wrong can land on the same
    kappa, and only the matrix separates them.
    """

    labels: tuple[str, ...]
    counts: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        if len(self.labels) < 2:
            raise ValueError("a confusion matrix needs at least two labels")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("confusion matrix labels must be distinct")
        if len(self.counts) != len(self.labels):
            raise ValueError("the matrix must be square over its labels")
        for row in self.counts:
            if len(row) != len(self.labels):
                raise ValueError("the matrix must be square over its labels")
            if any(cell < 0 for cell in row):
                raise ValueError("a confusion matrix holds counts, never negatives")

    @classmethod
    def from_pairs(
        cls, pairs: Iterable[tuple[str, str]], labels: Sequence[str]
    ) -> "ConfusionMatrix":
        """Build from (truth, prediction) pairs over a DECLARED label set.

        The labels are declared rather than inferred from the data: a label that
        no case happened to exercise must still appear as an empty row, or the
        matrix silently changes shape between runs and kappa becomes a number
        computed over a different scale each time.
        """
        index = {label: position for position, label in enumerate(labels)}
        grid = [[0] * len(labels) for _ in labels]
        for truth, predicted in pairs:
            if truth not in index:
                raise ValueError(f"truth label {truth!r} is outside the declared scale")
            if predicted not in index:
                raise ValueError(
                    f"predicted label {predicted!r} is outside the declared scale"
                )
            grid[index[truth]][index[predicted]] += 1
        return cls(tuple(labels), tuple(tuple(row) for row in grid))

    @property
    def total(self) -> int:
        return sum(sum(row) for row in self.counts)

    @property
    def correct(self) -> int:
        return sum(self.counts[i][i] for i in range(len(self.labels)))

    def truth_totals(self) -> tuple[int, ...]:
        return tuple(sum(row) for row in self.counts)

    def predicted_totals(self) -> tuple[int, ...]:
        return tuple(
            sum(self.counts[row][column] for row in range(len(self.labels)))
            for column in range(len(self.labels))
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "labels": list(self.labels),
            "counts": [list(row) for row in self.counts],
            "total": self.total,
        }


def raw_agreement(matrix: ConfusionMatrix) -> float:
    """Exact-match agreement. NEVER REPORT THIS ON ITS OWN.

    The 2026 large-scale judge study across 21 models and roughly 541,000
    judgments found raw exact-match agreement overstates chance-corrected
    agreement by a mean of 38.6 percentage points: a judge advertising 85%
    agreement is at roughly 48% chance corrected. It is computed here because
    the confusion matrix carries it anyway and hiding it would invite somebody
    to recompute it worse; `judges.protocol.JudgeResult` is what makes it
    impossible to publish without kappa and MCC beside it.
    """
    if matrix.total == 0:
        raise InsufficientData("agreement needs at least one judgment")
    return matrix.correct / matrix.total


def cohens_kappa(matrix: ConfusionMatrix) -> float:
    """Chance-corrected agreement: (po - pe) / (1 - pe).

    UNDEFINED, NOT ZERO, WHEN pe IS 1. That happens when every judgment and
    every truth carry one single label, which is the degenerate end of exactly
    the prevalence problem W7.1 warns about: on a rubric where 95% of items are
    "met", chance agreement is already near 1 and kappa collapses towards zero
    for a judge that is doing well. That is the kappa paradox, and the response
    is to rebalance the SET (see `golden.BALANCED_POSITIVE_RATE`) and project
    back, never to paper over the undefined case with a zero.
    """
    total = matrix.total
    if total == 0:
        raise InsufficientData("kappa needs at least one judgment")
    observed = matrix.correct / total
    truth = matrix.truth_totals()
    predicted = matrix.predicted_totals()
    expected = sum(
        (truth[i] / total) * (predicted[i] / total) for i in range(len(matrix.labels))
    )
    if math.isclose(expected, 1.0):
        raise InsufficientData(
            "expected agreement is 1.0, so kappa is undefined; the set carries "
            "only one label and must be rebalanced before a judge is scored"
        )
    return (observed - expected) / (1.0 - expected)


def matthews_corrcoef(matrix: ConfusionMatrix) -> float:
    """Multiclass Matthews correlation coefficient, in [-1, 1].

    WHY IT TRAVELS WITH KAPPA. Kappa corrects for chance agreement; MCC is a
    correlation over the whole matrix and is the one that stays honest under
    severe class imbalance, where a majority-class-always judge scores high
    accuracy. Reporting both means a judge has to be genuinely right rather than
    merely lucky about which failure mode the summary statistic forgives.

    Gorodkin's generalisation:

        MCC = (c*s - sum_k p_k t_k)
              / sqrt((s^2 - sum_k p_k^2) * (s^2 - sum_k t_k^2))

    with s the total, c the correct count, t_k the true count of label k and
    p_k the predicted count of label k.
    """
    total = matrix.total
    if total == 0:
        raise InsufficientData("MCC needs at least one judgment")
    truth = matrix.truth_totals()
    predicted = matrix.predicted_totals()
    correct = matrix.correct
    covariance = correct * total - sum(
        predicted[i] * truth[i] for i in range(len(matrix.labels))
    )
    predicted_spread = total * total - sum(value * value for value in predicted)
    truth_spread = total * total - sum(value * value for value in truth)
    if predicted_spread <= 0 or truth_spread <= 0:
        raise InsufficientData(
            "MCC is undefined when every judgment or every truth carries one "
            "label; the set must be rebalanced before a judge is scored"
        )
    return covariance / math.sqrt(float(predicted_spread) * float(truth_spread))


def reweight_to_prevalence(
    matrix: ConfusionMatrix, prevalence: Mapping[str, float]
) -> ConfusionMatrix:
    """Project a matrix measured on a rebalanced set back to production ratios.

    WHY THIS EXISTS (W7.1, the kappa paradox). A judge is calibrated on a set
    deliberately rebalanced towards 40/60, because on the production ratio of a
    Must-have rubric -- roughly 95% "met" -- chance agreement swamps kappa and a
    good judge and a useless judge both look like noise. But the number an
    operator reads has to describe production, not the calibration set. So the
    judge is MEASURED on the balanced set and REPORTED at the production
    prevalence, and this is the projection.

    It rescales each truth ROW to the target prevalence, preserving that row's
    conditional error profile, which is the assumption being made and the one
    that fails if the judge's behaviour itself depends on prevalence. Counts are
    rounded to integers so the result is still a matrix of counts; the rounding
    is why the projected total may differ from the source total by a few.
    """
    if matrix.total == 0:
        raise InsufficientData("a matrix with no judgments cannot be reweighted")
    missing = [label for label in matrix.labels if label not in prevalence]
    if missing:
        raise InsufficientData(
            "target prevalence is missing labels: " + ", ".join(missing)
        )
    weight_total = sum(float(prevalence[label]) for label in matrix.labels)
    if not math.isclose(weight_total, 1.0, abs_tol=1e-6):
        raise ValueError(f"target prevalence must sum to 1.0, got {weight_total}")
    truth = matrix.truth_totals()
    total = matrix.total
    rows: list[tuple[int, ...]] = []
    for index, label in enumerate(matrix.labels):
        row_total = truth[index]
        if row_total == 0:
            if float(prevalence[label]) > 0.0:
                raise InsufficientData(
                    f"label {label!r} carries target prevalence but no observed "
                    "case, so its error profile is unknown"
                )
            rows.append(tuple(0 for _ in matrix.labels))
            continue
        scale = (float(prevalence[label]) * total) / row_total
        rows.append(tuple(int(round(cell * scale)) for cell in matrix.counts[index]))
    return ConfusionMatrix(matrix.labels, tuple(rows))


# ── Ranking quality (needs ground truth) ─────────────────────────────────────


def ndcg(predicted: Sequence[Any], relevance: dict[Any, float], k: int = 5) -> float:
    """Normalised discounted cumulative gain over expert star ratings.

    Rank-position aware, unlike precision: putting the best candidate fifth
    instead of first is a real regression that precision@5 cannot see at all.

    The candidate-ranking entry point. `ndcg_at_k` is the same arithmetic under
    the retrieval vocabulary, and this delegates to it: two implementations of
    one discount would drift, and a ranking regression and a retrieval
    regression would then be measured on subtly different scales.
    """
    return ndcg_at_k(predicted, relevance, k)


def spearman(predicted: Sequence[float], truth: Sequence[float]) -> float:
    """Rank correlation between predicted scores and expert ratings.

    Rank-based rather than Pearson because the two scales are not comparable:
    an internal 0-100 and a 1-5 star rating agree about ORDER or they do not.
    """
    pairs = list(zip(predicted, truth))
    n = len(pairs)
    if n < 2:
        raise InsufficientData(f"rank correlation needs at least two pairs, got {n}")

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        index = 0
        while index < len(order):
            # Average tied ranks, or two identical scores would get an arbitrary
            # order and a spurious correlation.
            stop = index
            while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
                stop += 1
            average = (index + stop) / 2 + 1
            for position in range(index, stop + 1):
                result[order[position]] = average
            index = stop + 1
        return result

    predicted_ranks, truth_ranks = ranks([p for p, _ in pairs]), ranks([t for _, t in pairs])
    differences = sum((a - b) ** 2 for a, b in zip(predicted_ranks, truth_ranks))
    return round(1 - (6 * differences) / (n * (n * n - 1)), 4)


def diversity(profiles: Sequence[Sequence[str]]) -> float:
    """How different the top-k candidates' skill profiles are from each other.

    1.0 means no two share a skill; 0.0 means they are identical. Reported
    alongside precision precisely because it is the metric precision cannot see:
    a ranker that found five copies of one profile scores well on precision and
    has told the recruiter nothing.
    """
    sets = [set(item) for item in profiles if item]
    if len(sets) < 2:
        raise InsufficientData(
            f"diversity needs at least two non-empty profiles, got {len(sets)}"
        )
    overlaps = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            overlaps.append(len(sets[i] & sets[j]) / len(union) if union else 0.0)
    return round(1 - (sum(overlaps) / len(overlaps)), 4)


# ── Output quality (no ground truth needed) ──────────────────────────────────


#: A behavioural question asks what someone DID. A hypothetical asks what they
#: WOULD do, and the answer to a hypothetical is imagination, not evidence.
_HYPOTHETICAL = ("would you", "what would", "if you were", "imagine", "hypothetically")


def behavioural_rate(probes: Sequence[str]) -> float:
    if not probes:
        raise InsufficientData("a behavioural rate needs at least one probe")
    behavioural = sum(
        1
        for probe in probes
        if not any(marker in str(probe).casefold() for marker in _HYPOTHETICAL)
    )
    return round(behavioural / len(probes), 4)
