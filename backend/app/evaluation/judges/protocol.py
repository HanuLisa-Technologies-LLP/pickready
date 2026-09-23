"""What a judge result must carry before anybody is allowed to read it.

THE RULE THIS FILE IS (RPN-AI-UP-001 W7.5)
-------------------------------------------
"Every judge result carries: MCC, Cohen's kappa, the confusion matrix, and the
protocol (scale, population, abstention handling, aggregation). Never raw
agreement alone."

It is enforced AT CONSTRUCTION rather than at a render site, because a rule
enforced at one render site is a rule the next render site breaks. There is no
way to build a `JudgeResult` that is missing any of the four; a caller that
cannot compute kappa or MCC gets an exception naming which one and why, and its
honest move is to report the whole thing `unavailable`.

WHY RAW AGREEMENT IS NOT ENOUGH, IN NUMBERS
--------------------------------------------
The 2026 large-scale study across 21 models and roughly 541,000 judgments found
raw exact-match agreement overstates chance-corrected agreement by a mean of
38.6 percentage points. A judge advertising 85% agreement is at roughly 48%
chance corrected. Publishing the first figure alone is not a rounding error, it
is a different claim.

The same study found test-retest consistency above 0.95 coexisting with position
bias above 0.10, which is why `JudgeProtocol` carries `position_handling` as a
required field: REPRODUCIBILITY IS NOT VALIDITY. A judge that agrees with itself
is not thereby trustworthy, and a protocol that does not say how position was
controlled cannot be read as evidence that it was.

AND WHY A JUDGE VALIDATED HERE IS VALIDATED ONLY HERE
------------------------------------------------------
Judge rankings do not transfer across benchmarks: more than half of the 21
models shifted at least four rank positions between two benchmarks, one by
fifteen. `JudgeProtocol.population` is required for that reason. A kappa with no
population beside it invites the reader to generalise it, and the generalisation
is the thing the evidence specifically denies.

ABSTENTION IS AN INTERVAL, NEVER A FAILURE
-------------------------------------------
An abstaining or invalid judge output is not a wrong answer. Scoring it 0.0
counts a refusal as a mistake and rewards a judge that guesses over one that
declines. `JudgeResult.accuracy` is therefore a range: the low end assumes every
abstention would have been wrong, the high end assumes every one would have been
right, and the width is the honest cost of the abstentions. When abstentions
swamp the sample the whole result is `unavailable`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.evaluation import metrics
from app.evaluation.metrics import ConfusionMatrix, InsufficientData, Interval, Measurement

#: How an abstention may be handled, and the only permitted answers. `interval`
#: is the one this codebase uses; `excluded` is legitimate only when the
#: abstention count is reported beside it, which `JudgeResult` enforces by
#: carrying the count either way.
ABSTENTION_HANDLING: tuple[str, ...] = ("interval", "excluded")

#: How the jury's individual verdicts became one verdict.
AGGREGATION: tuple[str, ...] = ("majority", "unanimous", "single_judge")

#: How position bias was controlled on a pairwise comparison. `not_applicable`
#: is correct for a pointwise rubric grading, which is what this product's
#: reasoning and decision sets are; it is a stated answer rather than a silence.
POSITION_HANDLING: tuple[str, ...] = (
    "randomised",
    "both_orders_inconsistent_discarded",
    "not_applicable",
)

#: Below this share of usable judgments the result is refused outright. Set at
#: one half because an accuracy interval wider than the distance between a good
#: judge and a coin is not a measurement, it is a shape that looks like one.
MIN_USABLE_SHARE = 0.5


class JudgeReportingError(ValueError):
    """A judge result was built without something W7.5 requires."""


@dataclass(frozen=True)
class JudgeProtocol:
    """The four things a judge number means nothing without.

    Every field is required and every field is validated against a closed
    vocabulary or a non-empty string. A protocol with a blank population reads
    as a protocol, which is worse than no protocol at all.
    """

    scale: tuple[str, ...]
    population: str
    abstention_handling: str
    aggregation: str
    position_handling: str
    repeats: int
    judge_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.scale) < 2:
            raise JudgeReportingError(
                "a judge protocol must state a scale of at least two labels"
            )
        if len(set(self.scale)) != len(self.scale):
            raise JudgeReportingError("the scale must not repeat a label")
        if not self.population.strip():
            raise JudgeReportingError(
                "a judge protocol must name its population; a judge validated "
                "on one benchmark is validated only there"
            )
        if self.abstention_handling not in ABSTENTION_HANDLING:
            raise JudgeReportingError(
                f"abstention handling {self.abstention_handling!r} is not one of "
                + ", ".join(ABSTENTION_HANDLING)
            )
        if self.aggregation not in AGGREGATION:
            raise JudgeReportingError(
                f"aggregation {self.aggregation!r} is not one of " + ", ".join(AGGREGATION)
            )
        if self.position_handling not in POSITION_HANDLING:
            raise JudgeReportingError(
                f"position handling {self.position_handling!r} is not one of "
                + ", ".join(POSITION_HANDLING)
            )
        if self.repeats < 1:
            raise JudgeReportingError("a protocol runs at least one repeat")
        if not self.judge_ids:
            raise JudgeReportingError(
                "a protocol must name the judges it ran; an unnamed jury cannot "
                "be recalibrated when one of its members changes"
            )
        if self.aggregation == "single_judge" and len(self.judge_ids) != 1:
            raise JudgeReportingError(
                "single_judge aggregation names exactly one judge"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scale": list(self.scale),
            "population": self.population,
            "abstention_handling": self.abstention_handling,
            "aggregation": self.aggregation,
            "position_handling": self.position_handling,
            "repeats": self.repeats,
            "judge_ids": list(self.judge_ids),
        }


@dataclass(frozen=True)
class JudgeResult:
    """A judge's measured agreement with human ground truth, or nothing.

    Construction computes MCC and kappa from the matrix and RAISES if either is
    undefined, so the four mandatory pieces exist or the object does not. The
    caller catches that and reports `unavailable` with the reason; what it
    cannot do is publish a partial result that reads as complete.
    """

    judge_label: str
    dataset_version: str
    protocol: JudgeProtocol
    confusion: ConfusionMatrix
    abstentions: int
    invalid: int
    mcc: float
    kappa: float
    raw_agreement: float
    accuracy: Measurement

    def __post_init__(self) -> None:
        if not self.judge_label.strip():
            raise JudgeReportingError("a judge result must name the judge")
        if not self.dataset_version.strip():
            raise JudgeReportingError(
                "a judge result must carry its dataset version; without it a "
                "score drop cannot be attributed to a model, a rubric or a set"
            )
        if self.abstentions < 0 or self.invalid < 0:
            raise JudgeReportingError("abstention and invalid counts are counts")
        if tuple(self.confusion.labels) != tuple(self.protocol.scale):
            raise JudgeReportingError(
                "the confusion matrix labels must be the protocol's scale, or "
                "kappa is computed over a different scale than the one reported"
            )

    @property
    def judged(self) -> int:
        return self.confusion.total

    @property
    def presented(self) -> int:
        return self.judged + self.abstentions + self.invalid

    def as_dict(self) -> dict[str, Any]:
        """Every mandatory field, in one shape, with agreement never alone."""
        return {
            "judge": self.judge_label,
            "dataset_version": self.dataset_version,
            "protocol": self.protocol.as_dict(),
            "confusion_matrix": self.confusion.as_dict(),
            "mcc": round(self.mcc, 6),
            "cohens_kappa": round(self.kappa, 6),
            "raw_agreement": round(self.raw_agreement, 6),
            "raw_agreement_caveat": (
                "Raw exact-match agreement overstates chance-corrected "
                "agreement by a mean of 38.6 percentage points across the 2026 "
                "21-model study. Read it only beside the kappa above."
            ),
            "accuracy": self.accuracy.as_dict(),
            "counts": {
                "presented": self.presented,
                "judged": self.judged,
                "abstentions": self.abstentions,
                "invalid": self.invalid,
            },
        }


def build_result(
    *,
    judge_label: str,
    dataset_version: str,
    protocol: JudgeProtocol,
    pairs: list[tuple[str, str]],
    abstentions: int,
    invalid: int,
) -> JudgeResult:
    """Assemble a result from (truth, verdict) pairs, or refuse and say why.

    THE ACCURACY INTERVAL IS THE ABSTENTION RULE MADE ARITHMETIC. Over
    `presented` cases the judge answered `judged` of them and got `correct`
    right. The interval is

        low  = correct / presented                 (every abstention was wrong)
        high = (correct + unusable) / presented    (every abstention was right)

    so a judge that abstains widens its own interval rather than losing points,
    and the width is visible to the reader. When usable judgments fall below
    `MIN_USABLE_SHARE` of what was presented, the accuracy is `unavailable`
    instead: an interval spanning most of the scale is not a measurement.
    """
    if abstentions < 0 or invalid < 0:
        raise JudgeReportingError("abstention and invalid counts are counts")
    matrix = ConfusionMatrix.from_pairs(pairs, protocol.scale)
    unusable = abstentions + invalid
    presented = matrix.total + unusable
    if presented == 0:
        raise InsufficientData("the judge was presented with no cases")

    # Raise rather than substitute. A degenerate matrix is the kappa paradox
    # arriving in person, and the answer is to rebalance the set.
    mcc = metrics.matthews_corrcoef(matrix)
    kappa = metrics.cohens_kappa(matrix)
    agreement = metrics.raw_agreement(matrix)

    if matrix.total / presented < MIN_USABLE_SHARE:
        accuracy = Measurement.unavailable(
            f"only {matrix.total} of {presented} presented cases produced a "
            f"usable verdict, below the {MIN_USABLE_SHARE:.0%} floor; the "
            "accuracy interval would be wider than the quantity it estimates",
            sample_size=presented,
        )
    else:
        low = matrix.correct / presented
        high = (matrix.correct + unusable) / presented
        accuracy = Measurement.measured(
            matrix.correct / matrix.total,
            Interval(low, high),
            sample_size=presented,
            note=(
                f"{unusable} of {presented} cases abstained or returned an "
                "invalid verdict; the interval spans every way they could have "
                "resolved. They are never counted as failures."
            )
            if unusable
            else None,
        )

    return JudgeResult(
        judge_label=judge_label,
        dataset_version=dataset_version,
        protocol=protocol,
        confusion=matrix,
        abstentions=abstentions,
        invalid=invalid,
        mcc=mcc,
        kappa=kappa,
        raw_agreement=agreement,
        accuracy=accuracy,
    )
