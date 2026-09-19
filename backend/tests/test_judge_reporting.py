"""A judge result missing MCC, kappa, the confusion matrix or the protocol is
rejected AT CONSTRUCTION.

WHY AT CONSTRUCTION AND NOT AT THE RENDER SITE (RPN-AI-UP-001 W7.5)
--------------------------------------------------------------------
A rule enforced at one render site is a rule the next render site breaks. There
must be no way to build a partial judge result and then decide, later and
somewhere else, whether to print the parts that make it interpretable. So the
type refuses.

The claim being defended is specific and measured: across 21 models and roughly
541,000 judgments, raw exact-match agreement overstates chance-corrected
agreement by a mean of 38.6 percentage points. A judge advertising 85% agreement
is at roughly 48% chance corrected. Publishing agreement alone is not a rounding
error, it is a different claim, and the type is what stops it being made by
accident.

The same study found test-retest consistency above 0.95 coexisting with position
bias above 0.10, so REPRODUCIBILITY IS NOT VALIDITY, which is why the protocol
has to state how position was handled. And judge rankings do not transfer across
benchmarks -- more than half of the 21 models shifted at least four rank
positions between two -- which is why the protocol has to name its population.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.evaluation.judges import ABSTAIN, INVALID, JudgeProtocol, JudgeResult, build_result
from app.evaluation.judges import jury
from app.evaluation.judges.protocol import MIN_USABLE_SHARE, JudgeReportingError
from app.evaluation.metrics import ConfusionMatrix, InsufficientData, Interval, Measurement


def _protocol(**overrides: object) -> JudgeProtocol:
    base: dict[str, object] = {
        "scale": ("met", "not_met"),
        "population": "ReadyPick reasoning golden set 2026.Q3.1, Must-have items",
        "abstention_handling": "interval",
        "aggregation": "majority",
        "position_handling": "not_applicable",
        "repeats": 5,
        "judge_ids": ("juror-a", "juror-b", "juror-c"),
    }
    base.update(overrides)
    return JudgeProtocol(**base)  # type: ignore[arg-type]


# ── The protocol: four things a judge number means nothing without ───────────


def test_a_protocol_needs_a_scale_of_at_least_two_labels() -> None:
    with pytest.raises(JudgeReportingError):
        _protocol(scale=("met",))
    with pytest.raises(JudgeReportingError):
        _protocol(scale=("met", "met"))


def test_a_protocol_needs_a_population_because_a_judge_does_not_transfer() -> None:
    """More than half of 21 models shifted at least four rank positions between
    two benchmarks, one by fifteen. A kappa with no population beside it invites
    exactly the generalisation the evidence denies."""
    with pytest.raises(JudgeReportingError, match="population"):
        _protocol(population="   ")


def test_a_protocol_states_abstention_handling_from_a_closed_vocabulary() -> None:
    with pytest.raises(JudgeReportingError):
        _protocol(abstention_handling="ignore_them")


def test_a_protocol_states_aggregation_and_position_handling() -> None:
    with pytest.raises(JudgeReportingError):
        _protocol(aggregation="whatever_the_first_judge_said")
    with pytest.raises(JudgeReportingError):
        _protocol(position_handling="we_did_not_think_about_it")


def test_a_protocol_names_its_judges_and_a_single_judge_names_exactly_one() -> None:
    with pytest.raises(JudgeReportingError):
        _protocol(judge_ids=())
    with pytest.raises(JudgeReportingError):
        _protocol(aggregation="single_judge", judge_ids=("a", "b"))
    with pytest.raises(JudgeReportingError):
        _protocol(repeats=0)


# ── The result: refuses to exist without the four mandatory pieces ───────────


def _result() -> JudgeResult:
    pairs = [("met", "met")] * 20 + [("met", "not_met")] * 5
    pairs += [("not_met", "met")] * 10 + [("not_met", "not_met")] * 15
    return build_result(
        judge_label="jury",
        dataset_version="2026.Q3.1",
        protocol=_protocol(),
        pairs=pairs,
        abstentions=0,
        invalid=0,
    )


def test_a_built_result_carries_mcc_kappa_the_matrix_and_the_protocol() -> None:
    """The 20/5/10/15 matrix from `test_eval_metrics`: agreement 0.70, kappa
    0.40, MCC 0.4082. The thirty-point gap is visible in one object."""
    result = _result()
    assert result.raw_agreement == pytest.approx(0.70)
    assert result.kappa == pytest.approx(0.40)
    assert result.mcc == pytest.approx(0.4082482904638631)
    assert result.confusion.counts == ((20, 5), (10, 15))

    body = result.as_dict()
    for required in ("mcc", "cohens_kappa", "confusion_matrix", "protocol"):
        assert required in body, required
    for required in ("scale", "population", "abstention_handling", "aggregation"):
        assert required in body["protocol"], required
    # Raw agreement is present and it never travels alone.
    assert body["raw_agreement"] == pytest.approx(0.70)
    assert "38.6" in body["raw_agreement_caveat"]


@pytest.mark.parametrize(
    "field",
    ["protocol", "confusion", "mcc", "kappa"],
)
def test_the_four_mandatory_pieces_have_no_default_to_omit_them(field: str) -> None:
    """None of the four is optional, so there is no constructor call that
    accidentally leaves one out. Checked against the dataclass rather than by
    trying every wrong call: a default added later would silently reopen this."""
    fields = {f.name: f for f in dataclasses.fields(JudgeResult)}
    assert field in fields
    assert fields[field].default is dataclasses.MISSING
    assert fields[field].default_factory is dataclasses.MISSING  # type: ignore[misc]


def test_a_result_without_a_dataset_version_is_rejected() -> None:
    """Without the stamp, a score drop cannot be attributed to a model
    regression, a rubric change or a set change."""
    result = _result()
    with pytest.raises(JudgeReportingError, match="dataset version"):
        dataclasses.replace(result, dataset_version="  ")


def test_a_matrix_whose_labels_are_not_the_protocol_scale_is_rejected() -> None:
    """Otherwise kappa is computed over one scale and reported under another."""
    result = _result()
    with pytest.raises(JudgeReportingError, match="scale"):
        dataclasses.replace(
            result, confusion=ConfusionMatrix(("yes", "no"), ((1, 0), (0, 1)))
        )


def test_a_degenerate_matrix_refuses_to_produce_a_result_at_all() -> None:
    """Every case labelled and judged "met". Kappa is 0/0 and MCC's denominator
    is zero. `build_result` raises rather than emitting zeroes that would read
    as a judge performing badly on a set that cannot measure it."""
    with pytest.raises(InsufficientData):
        build_result(
            judge_label="jury",
            dataset_version="2026.Q3.1",
            protocol=_protocol(),
            pairs=[("met", "met")] * 40,
            abstentions=0,
            invalid=0,
        )


def test_no_case_at_all_refuses_rather_than_reporting_zero() -> None:
    with pytest.raises(InsufficientData):
        build_result(
            judge_label="jury",
            dataset_version="2026.Q3.1",
            protocol=_protocol(),
            pairs=[],
            abstentions=0,
            invalid=0,
        )


# ── Abstention is an interval, never a failure ───────────────────────────────


def test_an_abstention_widens_the_interval_and_is_never_scored_as_wrong() -> None:
    """40 presented: 35 judged (28 correct), 5 abstained.

        low  = 28 / 40                     = 0.700   every abstention wrong
        high = (28 + 5) / 40               = 0.825   every abstention right
        point estimate = 28 / 35           = 0.800   over what was judged

    Scoring the abstentions 0.0 would report 0.700 as the answer and reward a
    judge that guesses over one that declines. The width IS the cost of the
    abstentions, and it is visible.
    """
    pairs = [("met", "met")] * 18 + [("met", "not_met")] * 4
    pairs += [("not_met", "not_met")] * 10 + [("not_met", "met")] * 3
    result = build_result(
        judge_label="jury",
        dataset_version="2026.Q3.1",
        protocol=_protocol(),
        pairs=pairs,
        abstentions=5,
        invalid=0,
    )
    assert result.presented == 40
    assert result.judged == 35
    assert result.confusion.correct == 28
    accuracy = result.accuracy
    assert accuracy.available
    assert accuracy.interval is not None
    assert accuracy.value == pytest.approx(28 / 35)
    assert accuracy.interval.low == pytest.approx(28 / 40)
    assert accuracy.interval.high == pytest.approx(33 / 40)
    assert accuracy.note is not None and "never counted as failures" in accuracy.note


def test_a_judge_that_mostly_abstains_reports_unavailable_rather_than_a_range() -> None:
    """Below half usable, the interval is wider than the quantity it estimates,
    which is a shape that looks like a measurement and is not one."""
    pairs = [("met", "met")] * 6 + [("not_met", "not_met")] * 4
    result = build_result(
        judge_label="jury",
        dataset_version="2026.Q3.1",
        protocol=_protocol(),
        pairs=pairs,
        abstentions=30,
        invalid=5,
    )
    assert result.judged / result.presented < MIN_USABLE_SHARE
    assert not result.accuracy.available
    assert "floor" in (result.accuracy.unavailable_reason or "")
    body = result.as_dict()
    assert body["accuracy"]["status"] == "unavailable"
    assert "value" not in body["accuracy"]
    # The abstentions are still COUNTED and reported. An unavailable accuracy
    # with no counts beside it cannot be acted on.
    assert body["counts"] == {
        "presented": 45,
        "judged": 10,
        "abstentions": 30,
        "invalid": 5,
    }


def test_an_invalid_verdict_is_distinguished_from_an_abstention() -> None:
    """They need different fixes: an abstention is the judge declining, an
    invalid verdict is the judge or the parsing being broken. One count would
    send somebody to recalibrate a rubric when the parser is at fault."""
    pairs = [("met", "met")] * 10 + [("not_met", "not_met")] * 10
    result = build_result(
        judge_label="jury",
        dataset_version="2026.Q3.1",
        protocol=_protocol(),
        pairs=pairs,
        abstentions=3,
        invalid=2,
    )
    assert result.abstentions == 3
    assert result.invalid == 2
    assert result.as_dict()["counts"]["presented"] == 25


def test_negative_counts_are_refused() -> None:
    with pytest.raises(JudgeReportingError):
        build_result(
            judge_label="jury",
            dataset_version="2026.Q3.1",
            protocol=_protocol(),
            pairs=[("met", "met")],
            abstentions=-1,
            invalid=0,
        )


# ── Pooling ──────────────────────────────────────────────────────────────────


def test_the_panel_pools_by_majority_and_abstains_on_a_tie() -> None:
    """A TIE IS NOT A VERDICT. Breaking it by juror order would make the pooled
    label depend on the order the panel was declared in, which is a number
    nobody could justify and everybody would eventually tune by reordering."""
    scale = ("met", "not_met")
    assert jury.pool("c1", ["met", "met", "not_met"], scale).label == "met"
    assert jury.pool("c1", ["met", "not_met"], scale).label == ABSTAIN
    assert jury.pool("c1", [ABSTAIN, ABSTAIN, ABSTAIN], scale).label == ABSTAIN
    # An abstaining juror does not veto a majority among those who committed.
    assert jury.pool("c1", ["met", "met", ABSTAIN], scale).label == "met"
    assert jury.pool("c1", ["met", INVALID, ABSTAIN], scale).label == "met"


def test_the_individual_verdicts_survive_pooling() -> None:
    """A pooled label alone cannot say whether the panel was unanimous or split
    two to one, and the disagreement rate is the input to W8's gate threshold.
    So the members are kept rather than discarded."""
    scale = ("met", "not_met")
    unanimous = jury.pool("c1", ["met", "met", "met"], scale)
    assert unanimous.member_verdicts == ("met", "met", "met")
    assert unanimous.unanimous is True
    split = jury.pool("c2", ["met", "met", "not_met"], scale)
    assert split.label == "met"
    assert split.unanimous is False


def test_a_verdict_outside_the_declared_scale_is_refused() -> None:
    with pytest.raises(ValueError):
        jury.pool("c1", ["maybe"], ("met", "not_met"))
    with pytest.raises(jury.JuryUnavailable):
        jury.pool("c1", [], ("met", "not_met"))


def test_a_panel_of_one_judge_repeated_is_refused() -> None:
    """The panel result reports 7 to 8 times lower cost, higher agreement with
    humans and the lowest variance of any configuration tested, and the
    mechanism is HETEROGENEITY, not count. Two jurors with the same id are one
    judge counted twice, and the variance claim does not survive it."""

    class _Juror:
        def __init__(self, judge_id: str) -> None:
            self.judge_id = judge_id

        def verdict(self, case: object) -> str:
            return "met"

    with pytest.raises(ValueError, match="heterogeneity"):
        jury.judge_set([], (_Juror("a"), _Juror("a")), _protocol())


def test_a_measurement_that_is_genuinely_zero_is_still_a_measurement() -> None:
    """The boundary the whole unavailable rule rests on. A judge that got
    nothing right scores 0.0 and that is a real number; only an UNMEASURED
    quantity is unavailable."""
    zero = Measurement.measured(0.0, Interval(0.0, 0.1))
    assert zero.available
    assert zero.as_dict()["value"] == 0.0
