"""The eval metrics, against fixtures computed by hand in the docstrings.

WHY HAND-COMPUTED AND NOT PROPERTY-BASED
------------------------------------------
A property test ("nDCG never exceeds one", "recall rises with k") passes for a
whole family of wrong implementations, including the one that returns a
constant. These metrics decide release gates, so the assertions below state the
ARITHMETIC, written out independently of the implementation, and a reader can
check each one on paper. A wrong confidence interval is a wrong release
decision, and the only defence against one is a number somebody verified.

Every expected value is written twice: once as the formula, spelled out from
its published definition, and once as the decimal that formula produces. The
formula catches an implementation that computes something else; the decimal
catches a formula that was transcribed wrong in both places.

Provenance: RPN-AI-UP-001 W7.3, W7.5.
"""
from __future__ import annotations

import math
import pathlib

import pytest

from app.evaluation import golden, metrics
from app.evaluation.metrics import ConfusionMatrix, InsufficientData


# ── recall@k ─────────────────────────────────────────────────────────────────


def test_recall_at_k_counts_relevant_items_found_not_results_returned() -> None:
    """retrieved = [a, b, c, d, e], relevant = {a, c, x}.

    The denominator is the RELEVANT set, three items, never the window:

        recall@1 = |{a}       intersect {a,c,x}| / 3 = 1/3
        recall@3 = |{a,b,c}   intersect {a,c,x}| / 3 = 2/3
        recall@5 = |{a..e}    intersect {a,c,x}| / 3 = 2/3

    x is not in the corpus's ranking at all, so recall cannot reach one. That
    is the property that makes recall the right ceiling metric: it counts what
    retrieval FAILED to surface, and no reranker can recover it.
    """
    retrieved = ["a", "b", "c", "d", "e"]
    relevant = {"a", "c", "x"}
    assert metrics.recall_at_k(retrieved, relevant, 1) == 1 / 3
    assert metrics.recall_at_k(retrieved, relevant, 3) == 2 / 3
    assert metrics.recall_at_k(retrieved, relevant, 5) == 2 / 3
    assert metrics.recall_at_k(retrieved, relevant, 50) == 2 / 3


def test_recall_refuses_an_empty_relevant_set_rather_than_dividing_by_zero() -> None:
    with pytest.raises(InsufficientData):
        metrics.recall_at_k(["a"], [], 5)
    with pytest.raises(InsufficientData):
        metrics.recall_at_k(["a"], ["a"], 0)


# ── nDCG@k ───────────────────────────────────────────────────────────────────


def test_ndcg_at_k_against_the_definition_written_out() -> None:
    """gains = {A: 3, B: 2, C: 1}, retrieved = [B, X, A, C].

    DCG uses the standard log2(rank + 1) discount, so rank 1 divides by
    log2(2) = 1, rank 2 by log2(3), rank 3 by log2(4) = 2, rank 4 by log2(5).

        DCG@3  = 2/1 + 0/log2(3) + 3/2                = 3.5
        IDCG@3 = 3/1 + 2/log2(3) + 1/2                = 4.7618595071429155
        nDCG@3 = 3.5 / 4.7618595071429155             = 0.7350069851388743

    X carries no gain, so it contributes nothing while still consuming rank 2.
    That is the whole reason nDCG beats precision here: a wrong item in second
    place is paid for by everything behind it moving down a discount step.
    """
    gains = {"A": 3.0, "B": 2.0, "C": 1.0}
    retrieved = ["B", "X", "A", "C"]

    expected_dcg = 2 / 1 + 0 / math.log2(3) + 3 / 2
    expected_idcg = 3 / 1 + 2 / math.log2(3) + 1 / 2
    assert expected_dcg == pytest.approx(3.5)
    assert expected_idcg == pytest.approx(4.7618595071429155)

    assert metrics.ndcg_at_k(retrieved, gains, 3) == pytest.approx(
        expected_dcg / expected_idcg
    )
    assert metrics.ndcg_at_k(retrieved, gains, 3) == pytest.approx(0.7350069851, abs=1e-9)


def test_ndcg_is_one_for_the_ideal_ranking_and_falls_when_order_worsens() -> None:
    gains = {"A": 3.0, "B": 2.0, "C": 1.0}
    assert metrics.ndcg_at_k(["A", "B", "C"], gains, 3) == pytest.approx(1.0)
    assert metrics.ndcg_at_k(["C", "B", "A"], gains, 3) < metrics.ndcg_at_k(
        ["A", "B", "C"], gains, 3
    )


def test_ndcg_at_a_deeper_cutoff_is_at_least_the_shallower_one_here() -> None:
    """The stability argument W7.3 makes, shown rather than asserted in prose.

    gains = {A: 3, D: 3}, retrieved = [A, X, X, X, X, X, X, X, X, X, D].

        nDCG@10  puts A at rank 1 and never reaches D:
                 DCG = 3, IDCG = 3 + 3/log2(3), so 3/4.8927892... = 0.61314...
        nDCG@20  reaches D at rank 11:
                 DCG = 3 + 3/log2(12) = 3.8371445...
                 IDCG unchanged, so 0.78424...

    One item moving across the rank-10 boundary swings the shallow figure by
    seventeen points and would swing the deep one far less on a real ranking.
    """
    gains = {"A": 3.0, "D": 3.0}
    retrieved = ["A"] + [f"x{i}" for i in range(9)] + ["D"]
    idcg = 3 / 1 + 3 / math.log2(3)
    assert metrics.ndcg_at_k(retrieved, gains, 10) == pytest.approx(3 / idcg)
    assert metrics.ndcg_at_k(retrieved, gains, 20) == pytest.approx(
        (3 + 3 / math.log2(12)) / idcg
    )
    assert metrics.ndcg_at_k(retrieved, gains, 20) > metrics.ndcg_at_k(
        retrieved, gains, 10
    )


def test_ndcg_refuses_a_gain_set_with_nothing_positive_in_it() -> None:
    with pytest.raises(InsufficientData):
        metrics.ndcg_at_k(["a"], {"a": 0.0}, 5)
    with pytest.raises(InsufficientData):
        metrics.ndcg_at_k(["a"], {"a": 1.0}, 0)


# ── MRR ──────────────────────────────────────────────────────────────────────


def test_reciprocal_rank_and_mrr() -> None:
    """Three queries: first relevant at rank 2, at rank 1, and never.

        RR = 1/2, 1/1, 0
        MRR = (0.5 + 1.0 + 0.0) / 3 = 0.5

    THE ZERO IS MEASURED, not unavailable. "We ranked the whole corpus and none
    of it was relevant" is an observation with a value; it is a different thing
    from "we could not measure this", and the third query is here to pin that
    distinction rather than to pad the fixture.
    """
    assert metrics.reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert metrics.reciprocal_rank(["a", "x"], {"a"}) == 1.0
    assert metrics.reciprocal_rank(["x", "y"], {"a"}) == 0.0
    assert metrics.mrr([0.5, 1.0, 0.0]) == pytest.approx(0.5)


def test_mrr_refuses_an_empty_set_of_queries() -> None:
    with pytest.raises(InsufficientData):
        metrics.mrr([])


# ── Wilson score interval ────────────────────────────────────────────────────


def test_wilson_interval_against_the_published_formula() -> None:
    """8 successes in 10 trials at 95%, z = 1.9599639845400545.

        p           = 0.8
        z^2         = 3.8414588206941245
        denominator = 1 + z^2/10                     = 1.3841458820694124
        centre      = (0.8 + z^2/20) / denominator   = 0.7167401600...
        margin      = z*sqrt(0.8*0.2/10 + z^2/400) / denominator
                    = 1.9599639845400545 * 0.1600113967... / 1.3841458820694124
                    = 0.2265776885...
        interval    = [0.4901624715, 0.9433178485]

    The published Wilson interval for 8 of 10 at 95% is [0.4902, 0.9433].
    """
    z = 1.9599639845400545
    n, s = 10.0, 8.0
    p = s / n
    denominator = 1 + (z * z) / n
    centre = (p + (z * z) / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n)) / denominator

    interval = metrics.wilson_interval(8, 10)
    assert interval.low == pytest.approx(centre - margin)
    assert interval.high == pytest.approx(centre + margin)
    assert interval.low == pytest.approx(0.4901624715, abs=1e-9)
    assert interval.high == pytest.approx(0.9433178485, abs=1e-9)


def test_wilson_keeps_width_at_the_boundary_where_the_normal_approximation_dies() -> None:
    """10 of 10 successes. The normal approximation gives p +/- 0, a zero-width
    interval claiming certainty from ten observations. Wilson does not.

        centre = (1 + z^2/20) / (1 + z^2/10) = 0.8617...
        the interval's upper end is exactly 1.0 and its lower end is 0.7224672001
    """
    interval = metrics.wilson_interval(10, 10)
    assert interval.high == pytest.approx(1.0)
    assert interval.low == pytest.approx(0.7224672001, abs=1e-9)
    assert interval.width > 0.25


def test_wilson_stays_inside_zero_and_one_at_both_extremes() -> None:
    for successes in (0, 10):
        interval = metrics.wilson_interval(successes, 10)
        assert 0.0 <= interval.low <= interval.high <= 1.0


def test_wilson_refuses_a_zero_trial_count_and_an_untabulated_confidence() -> None:
    with pytest.raises(InsufficientData):
        metrics.wilson_interval(0, 0)
    with pytest.raises(InsufficientData):
        metrics.wilson_interval(1, 2, confidence=0.975)
    with pytest.raises(ValueError):
        metrics.wilson_interval(11, 10)


# ── Mean interval ────────────────────────────────────────────────────────────


def test_mean_interval_against_the_definition_written_out() -> None:
    """values = [0.2, 0.4, 0.6, 0.8].

        mean     = 0.5
        variance = (0.09 + 0.01 + 0.01 + 0.09) / 3 = 0.0666666...   (n-1)
        sd       = 0.2581988897471611
        margin   = 1.9599639845400545 * 0.2581988897471611 / 2 = 0.2530302624
        interval = [0.2469697376, 0.7530302624]
    """
    interval = metrics.mean_interval([0.2, 0.4, 0.6, 0.8])
    variance = (0.09 + 0.01 + 0.01 + 0.09) / 3
    margin = 1.9599639845400545 * math.sqrt(variance / 4)
    assert interval.low == pytest.approx(0.5 - margin)
    assert interval.high == pytest.approx(0.5 + margin)
    assert interval.low == pytest.approx(0.2469697376, abs=1e-9)
    assert interval.high == pytest.approx(0.7530302624, abs=1e-9)


def test_mean_interval_refuses_a_single_observation() -> None:
    """A spread from one value is zero, and a zero-width interval beside a
    single measurement is the most confident-looking wrong answer available."""
    with pytest.raises(InsufficientData):
        metrics.mean_interval([0.5])


# ── Confusion matrix, kappa, MCC ─────────────────────────────────────────────


def _balanced_matrix() -> ConfusionMatrix:
    """Truth on rows, prediction on columns, labels (met, not_met):

              pred met   pred not_met
        met       20            5
        not_met   10           15
    """
    return ConfusionMatrix(("met", "not_met"), ((20, 5), (10, 15)))


def test_raw_agreement_and_kappa_disagree_by_thirty_points_on_one_matrix() -> None:
    """The 38.6-point overstatement W7.5 cites, in a single worked case.

        total = 50, correct = 20 + 15 = 35
        po = 35/50                                             = 0.70
        truth totals = (25, 25); predicted totals = (30, 20)
        pe = (25/50)(30/50) + (25/50)(20/50) = 0.30 + 0.20      = 0.50
        kappa = (0.70 - 0.50) / (1 - 0.50)                      = 0.40

    Reporting 70% agreement without the 0.40 beside it is not a rounding error,
    it is a different claim.
    """
    matrix = _balanced_matrix()
    assert matrix.total == 50
    assert matrix.correct == 35
    assert matrix.truth_totals() == (25, 25)
    assert matrix.predicted_totals() == (30, 20)
    assert metrics.raw_agreement(matrix) == pytest.approx(0.70)
    assert metrics.cohens_kappa(matrix) == pytest.approx(0.40)


def test_mcc_against_gorodkins_generalisation_written_out() -> None:
    """Same matrix. s = 50, c = 35, t = (25, 25), p = (30, 20).

        covariance       = 35*50 - (30*25 + 20*25) = 1750 - 1250 = 500
        predicted spread = 50^2 - (30^2 + 20^2)    = 2500 - 1300 = 1200
        truth spread     = 50^2 - (25^2 + 25^2)    = 2500 - 1250 = 1250
        MCC = 500 / sqrt(1200 * 1250) = 500 / 1224.7448713915891
            = 0.4082482904638631
    """
    matrix = _balanced_matrix()
    expected = (35 * 50 - (30 * 25 + 20 * 25)) / math.sqrt(
        (50**2 - (30**2 + 20**2)) * (50**2 - (25**2 + 25**2))
    )
    assert expected == pytest.approx(0.4082482904638631)
    assert metrics.matthews_corrcoef(matrix) == pytest.approx(expected)


def test_kappa_and_mcc_refuse_a_single_label_matrix_rather_than_returning_zero() -> None:
    """The kappa paradox arriving in person.

    Every truth and every verdict is "met": chance agreement is exactly 1, so
    kappa is 0/0 and MCC's denominator is zero. Both are UNDEFINED, and the
    honest answer is to rebalance the set, not to print a zero that reads as a
    judge scoring badly.
    """
    degenerate = ConfusionMatrix(("met", "not_met"), ((40, 0), (0, 0)))
    with pytest.raises(InsufficientData):
        metrics.cohens_kappa(degenerate)
    with pytest.raises(InsufficientData):
        metrics.matthews_corrcoef(degenerate)


def test_confusion_matrix_from_pairs_keeps_a_label_nobody_exercised() -> None:
    """A declared label with no case must still appear as an empty row, or the
    matrix changes shape between runs and kappa is computed over a different
    scale each time."""
    matrix = ConfusionMatrix.from_pairs(
        [("met", "met"), ("met", "not_met")], ["met", "not_met", "unclear"]
    )
    assert matrix.labels == ("met", "not_met", "unclear")
    assert matrix.counts == ((1, 1, 0), (0, 0, 0), (0, 0, 0))
    with pytest.raises(ValueError):
        ConfusionMatrix.from_pairs([("met", "wat")], ["met", "not_met"])


def test_reweighting_to_production_prevalence_is_the_kappa_paradox_made_visible() -> None:
    """The same judge, measured on a 50/50 set and reported at 90/10.

    Rows are rescaled to the target prevalence, preserving each row's error
    profile:

        met     row total 25, scale = (0.9 * 50) / 25 = 1.8 -> (36, 9)
        not_met row total 25, scale = (0.1 * 50) / 25 = 0.2 -> (2, 3)

        total = 50, correct = 39, po = 0.78
        truth = (45, 5), predicted = (38, 12)
        pe = (45/50)(38/50) + (5/50)(12/50) = 0.684 + 0.024 = 0.708
        kappa = (0.78 - 0.708) / (1 - 0.708) = 0.072 / 0.292 = 0.2465753...

    Kappa falls from 0.400 to 0.247 with no change to the judge whatsoever.
    That is why W7.1 says to rebalance internally and project back, and why a
    dashboard that reported only the balanced-set kappa would be flattering.
    """
    projected = metrics.reweight_to_prevalence(
        _balanced_matrix(), {"met": 0.9, "not_met": 0.1}
    )
    assert projected.counts == ((36, 9), (2, 3))
    assert metrics.cohens_kappa(projected) == pytest.approx(0.072 / 0.292)
    assert metrics.cohens_kappa(projected) == pytest.approx(0.2465753, abs=1e-6)
    assert metrics.cohens_kappa(projected) < metrics.cohens_kappa(_balanced_matrix())


def test_reweighting_refuses_a_prevalence_that_names_an_unobserved_label() -> None:
    matrix = ConfusionMatrix(("met", "not_met"), ((20, 5), (0, 0)))
    with pytest.raises(InsufficientData):
        metrics.reweight_to_prevalence(matrix, {"met": 0.9, "not_met": 0.1})
    with pytest.raises(ValueError):
        metrics.reweight_to_prevalence(_balanced_matrix(), {"met": 0.9, "not_met": 0.5})


# ── The golden set on disk ───────────────────────────────────────────────────


def test_the_retrieval_golden_set_loads_and_cross_validates() -> None:
    """Every graded chunk id exists in the corpus. This is the one authoring
    error a retrieval ground truth can carry that nothing downstream detects:
    recall over an unreachable id sits permanently below one and reads as a
    retrieval failure for as long as nobody opens the file."""
    dataset = golden.load_retrieval_set()
    assert dataset.version == golden.GOLDEN_VERSION
    # 2026.Q3.2: Q3.1's 60 chunks and 24 queries verbatim, plus 90 chunks and
    # 36 queries hand-authored on 2026-09-10 across six new role domains.
    # Still below the 300-case floor, and the availability report says so.
    assert len(dataset.corpus) == 150
    assert len(dataset.cases) == 60
    corpus_ids = dataset.corpus_ids
    for case in dataset.cases:
        assert set(case.qrels) <= corpus_ids, case.query_id


def test_the_three_axes_are_stratified_and_the_gaps_are_named() -> None:
    """Coverage is REPORTED, never assumed. Sixty cases cannot fill seventy
    two cells either, and a per-stratum number read without its cell count is
    read as more general than its inputs."""
    dataset = golden.load_retrieval_set()
    report = golden.stratification(dataset.cases)
    assert report["cells"]["total"] == 72
    # Sixty cases over seventy-two cells: fuller than Q3.1's twenty-four, and
    # the unpopulated cells are still NAMED rather than papered over.
    assert report["cells"]["populated"] == 30
    # Every job grade and every dimension category is exercised.
    assert all(count > 0 for count in report["job_grade"].values())
    assert all(count > 0 for count in report["dimension_category"].values())
    # E4 is not, and that is a real gap rather than an oversight: nothing in
    # this fixture corpus is evidence produced under observation.
    assert report["evidence_tier"]["E4"] == 0
    assert any(cell.endswith("/E4") for cell in report["cells"]["unpopulated"])


def test_the_evidence_tier_axis_carries_the_runbook_141_case() -> None:
    """A Must-have query whose only support sits at E0.

    This is the case `claude.md` records as passing today: a fabricated
    Must-have resting on one weakest-tier resume bullet scores high, grades
    Matching, and trips no score-based cap. It is invisible on the grade and
    category axes and shows up only in a breakdown by tier.
    """
    dataset = golden.load_retrieval_set()
    tier_zero_must_haves = [
        case
        for case in dataset.cases
        if case.strata.dimension_category == "must_have"
        and case.strata.evidence_tier == "E0"
    ]
    assert tier_zero_must_haves
    assert all(case.provenance == "adversarial" for case in tier_zero_must_haves)


def test_a_query_grading_an_unknown_chunk_id_is_refused_at_load(tmp_path) -> None:
    """The negative direction. Without this, the cross-validation above could
    be checking nothing and would look identical."""
    import json
    import shutil

    source = golden.set_path(golden.SET_RETRIEVAL)
    target = tmp_path / golden.SET_RETRIEVAL / golden.GOLDEN_VERSION
    shutil.copytree(source, target)
    queries_path = target / "queries.json"
    body = json.loads(queries_path.read_text(encoding="utf-8"))
    body["queries"][0]["qrels"] = {"c999": 3}
    queries_path.write_text(json.dumps(body), encoding="utf-8")

    original_root = golden.DATASETS_ROOT
    golden.DATASETS_ROOT = tmp_path
    try:
        with pytest.raises(golden.GoldenSetError, match="absent from the corpus"):
            golden.load_retrieval_set()
    finally:
        golden.DATASETS_ROOT = original_root


def test_a_version_mismatch_is_refused_so_a_result_cannot_be_misattributed() -> None:
    with pytest.raises(golden.GoldenSetError):
        golden.load_retrieval_set(version="1999.Q1.1")


def test_the_golden_set_files_reach_the_image() -> None:
    """The failure mode `claude.md` records for `seed_resume_corpus`: a fixture
    that lived outside the Docker build context never reached the image, the
    caller logged that it found nothing and exited zero, and production ran on
    two candidates against thirty while every deploy was green.

    `backend/` is the build context and the datasets sit under
    `backend/app/evaluation/datasets/`, so they are inside it. What could still
    remove them is a `.dockerignore` rule, and that is what this checks.
    """
    backend = pathlib.Path(__file__).resolve().parents[1]
    assert golden.DATASETS_ROOT.is_relative_to(backend)
    ignore = backend / ".dockerignore"
    assert ignore.exists()
    patterns = [
        line.strip()
        for line in ignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for pattern in patterns:
        assert "evaluation" not in pattern, pattern
        assert pattern not in ("*.json", "app/", "app"), pattern


def test_the_shipped_run_is_not_gate_eligible_and_says_which_provenance_would_be() -> None:
    run = golden.load_run("reference-fixture")
    assert run.provenance == "reference_fixture"
    assert not run.gate_eligible
    assert "recorded" in golden.RUN_PROVENANCE_VALUES
