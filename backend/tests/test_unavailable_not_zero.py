"""No eval surface may emit 0.0 for a quantity it did not measure.

THE RULE AND WHY IT IS MECHANICAL (RPN-AI-UP-001 W7.5)
-------------------------------------------------------
Scoring an unmeasured quantity 0.0 counts an abstention as a failure. It reads
as "the judge got everything wrong" when the truth is "nobody asked the judge
anything", and the two are indistinguishable once the number is in a dashboard.
The same error one layer up would report a quality metric of 0.0 against no
ground truth, which is why `eval_agents.py` already reports UNAVAILABLE there
and why `claude.md` records that it must keep doing so.

The enforcement is a TYPE, not a convention. `Measurement.unavailable(reason)`
serialises with no numeric key at all, so there is nothing for a dashboard, a
threshold or a diff to read a zero out of; and `EvalReport` refuses a bare float
in its measurements, so a caller cannot route around the type by passing one.

THE OTHER HALF OF THE RULE IS AS IMPORTANT
--------------------------------------------
A MEASURED zero is a real number and must survive. "The retriever ranked the
whole corpus and none of it was relevant" is an observation with a value. A
system that reported every zero as unavailable would be as dishonest as one that
reported every unavailable as zero, and the tests below pin both directions.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.evaluation import golden, metrics, retrieval_eval
from app.evaluation.metrics import InsufficientData, Interval, Measurement
from app.evaluation.reporting import EvalReport, render_lines


# ── The type ─────────────────────────────────────────────────────────────────


def test_an_unavailable_measurement_serialises_with_no_number_in_it() -> None:
    body = Measurement.unavailable("no expert labels exist").as_dict()
    assert body == {"status": "unavailable", "reason": "no expert labels exist"}
    assert "value" not in body
    assert "interval" not in body
    assert not any(isinstance(value, (int, float)) for value in body.values())


def test_an_unavailable_measurement_must_say_why() -> None:
    with pytest.raises(ValueError):
        Measurement.unavailable("   ")


def test_a_measurement_is_never_both_and_never_neither() -> None:
    with pytest.raises(ValueError):
        Measurement(0.5, Interval(0.0, 1.0), "and also unavailable")
    with pytest.raises(ValueError):
        Measurement(None, None, None)
    with pytest.raises(ValueError):
        Measurement(0.5, None, None)


def test_a_measured_zero_survives_as_a_number() -> None:
    """The other half of the rule. A retriever that surfaced nothing relevant
    scored zero, and turning that into `unavailable` would hide a real
    regression behind the word reserved for missing inputs."""
    body = Measurement.measured(0.0, Interval(0.0, 0.15)).as_dict()
    assert body["status"] == "measured"
    assert body["value"] == 0.0
    assert metrics.reciprocal_rank(["x", "y"], {"a"}) == 0.0


# ── Every metric refuses rather than defaulting ──────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: metrics.recall_at_k(["a"], [], 5), id="recall_no_truth"),
        pytest.param(lambda: metrics.ndcg_at_k(["a"], {}, 5), id="ndcg_no_gains"),
        pytest.param(lambda: metrics.ndcg(["a"], {}, 5), id="ndcg_legacy_no_gains"),
        pytest.param(lambda: metrics.mrr([]), id="mrr_no_queries"),
        pytest.param(lambda: metrics.mean_interval([0.5]), id="mean_one_observation"),
        pytest.param(lambda: metrics.wilson_interval(0, 0), id="wilson_no_trials"),
        pytest.param(lambda: metrics.spearman([1.0], [1.0]), id="spearman_one_pair"),
        pytest.param(lambda: metrics.diversity([["a"]]), id="diversity_one_profile"),
        pytest.param(lambda: metrics.behavioural_rate([]), id="behavioural_no_probes"),
        pytest.param(
            lambda: metrics.reciprocal_rank(["a"], []), id="reciprocal_no_truth"
        ),
    ],
)
def test_an_insufficient_input_raises_with_a_reason_instead_of_returning_zero(
    call: Any,
) -> None:
    """Every one of these returned 0.0 or a fabricated 1.0 before W7.5.

    `ndcg` with an empty relevance dict returned 0.0, which is a perfect score
    report for a ranking nobody could evaluate; `diversity` with one profile
    returned 1.0, claiming maximal diversity from a single item. Both look like
    results. Raising is what forces the caller to render the reason.
    """
    with pytest.raises(InsufficientData) as caught:
        call()
    assert str(caught.value).strip()


def test_the_raised_reason_is_what_the_report_renders() -> None:
    """Metrics raise, reports degrade, and the reason survives the crossing.

    `retrieval_eval._macro` is the one place that crossing happens, and it does
    not restate the threshold: the rule about how few observations can support
    an interval lives in `mean_interval`, and the reason a reader sees is the
    one that function raised.
    """
    result = retrieval_eval._macro([0.5])
    assert not result.available
    assert "at least two observations" in (result.unavailable_reason or "")
    assert result.sample_size == 1
    assert "value" not in result.as_dict()
    # And the same path yields a real measurement the moment it can.
    measured = retrieval_eval._macro([0.4, 0.6])
    assert measured.available and measured.value == pytest.approx(0.5)


def test_a_zero_width_interval_is_labelled_as_an_artefact_rather_than_certainty() -> None:
    """Four queries that all scored 1.0 give a sample variance of zero, so the
    normal approximation reports [1.0000, 1.0000]. That is arithmetically
    correct and the most confident-looking thing this report can print. Four
    observations do not license certainty, so the number carries the reason its
    interval has no width.
    """
    measurement = retrieval_eval._macro([1.0, 1.0, 1.0, 1.0])
    assert measurement.available
    assert measurement.interval is not None
    assert measurement.interval.width == 0.0
    assert "not certainty" in (measurement.note or "")


def test_an_interval_that_ran_outside_the_bound_says_it_was_clamped() -> None:
    """Recall is bounded and the normal approximation is not. At three
    observations it will print an upper bound of 1.32, which is not a recall.
    Clamping silently would hide the signal that the sample is too small, so
    the raw bound travels in the note.
    """
    measurement = retrieval_eval._macro([0.0, 1.0, 1.0])
    assert measurement.interval is not None
    assert measurement.interval.high == 1.0
    assert "was clamped" in (measurement.note or "")
    assert "1.3200" in (measurement.note or "")


# ── The report envelope ──────────────────────────────────────────────────────


def test_a_report_refuses_a_bare_float_because_a_float_cannot_abstain() -> None:
    with pytest.raises(TypeError, match="Measurement"):
        EvalReport(
            surface="retrieval",
            dataset_version="2026.Q3.1",
            produced_by="test",
            measurements={"recall@20": 0.0},  # type: ignore[dict-item]
        )


def test_a_report_refuses_to_exist_without_its_dataset_version_stamp() -> None:
    """A score that drops has three candidate causes: a model regression, a
    rubric change, or a set change. Only the stamp separates the third."""
    with pytest.raises(ValueError, match="dataset version"):
        EvalReport(surface="retrieval", dataset_version="", produced_by="test")


def test_an_unavailable_row_is_printed_rather_than_dropped() -> None:
    """A dropped row reads as "the metric was fine and the printer was terse"."""
    report = EvalReport(
        surface="retrieval",
        dataset_version="2026.Q3.1",
        produced_by="test",
        measurements={"ndcg@100": Measurement.unavailable("the run has depth 20")},
    )
    rendered = "\n".join(render_lines(report))
    assert "ndcg@100" in rendered
    assert "unavailable" in rendered
    assert "the run has depth 20" in rendered
    assert "0.0000" not in rendered


# ── The three golden sets, as they actually stand today ──────────────────────


def _numeric_leaves(node: Any, path: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_numeric_leaves(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_numeric_leaves(value, f"{path}[{index}]"))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        found.append((path, node))
    return found


@pytest.mark.parametrize("name", [golden.SET_REASONING, golden.SET_DECISION])
def test_the_judged_sets_report_unavailable_with_a_reason_and_no_number(
    name: str,
) -> None:
    """Populated by nobody, and honest about both halves of why: there is no
    production sample to draw from, and the labels must be human anyway."""
    report = retrieval_eval.judged_set_report(name)
    assert report.unavailable == ("accuracy", "cohens_kappa", "mcc")
    body = report.as_dict()
    for key, measurement in body["measurements"].items():
        assert measurement["status"] == "unavailable", key
        assert measurement["reason"].strip(), key
        assert not _numeric_leaves(measurement), key
    assert body["context"]["cases"] == 0
    assert body["context"]["set_availability"]["gate_eligible"] is False


def test_the_retrieval_set_is_populated_and_the_other_two_are_not() -> None:
    """The plain statement of where this deployment stands, executed.

    Retrieval is judge free and objectively checkable, so it could be built.
    Reasoning and decision need human labels AND a production sample, and the
    only deployed environment holds zero candidates, profiles, applications and
    reports.
    """
    dataset = golden.load_retrieval_set()
    assert golden.availability(golden.SET_RETRIEVAL, dataset.cases).status == "available"
    for name in (golden.SET_REASONING, golden.SET_DECISION):
        judged = golden.load_judged_set(name)
        assert judged.cases == ()
        availability = golden.availability(name, judged.cases, judged.why_empty)
        assert availability.status == golden.SetAvailability.UNAVAILABLE
        assert "human labelled" in availability.reason.casefold()


def test_a_cutoff_deeper_than_the_run_is_unavailable_rather_than_silently_shallower() -> None:
    """nDCG@100 over a depth-20 run is nDCG@20 wearing a different name, and
    recall@200 over it is recall@20. Both are arithmetically defined and both
    would be read as what their names say. This is the W7.3 point made
    mechanical: recall at the candidate generation k of 100 to 200 cannot be
    measured without a run that deep, whatever arithmetic you are willing to do.
    """
    evaluation = retrieval_eval.evaluate_default()
    for key in ("ndcg@100", "recall@100", "recall@200"):
        measurement = evaluation.report.measurements[key]
        assert not measurement.available, key
        assert "depth" in (measurement.unavailable_reason or ""), key
    # And the cutoffs the run CAN support are measured, so the rule above is a
    # depth rule rather than a blanket refusal.
    for key in ("recall@20", "ndcg@10", "ndcg@20", "mrr"):
        assert evaluation.report.measurements[key].available, key


def test_no_unavailable_row_anywhere_in_the_full_report_carries_a_number() -> None:
    """The sweep. A rule enforced at one call site is a rule the next call site
    breaks, so this walks the WHOLE serialised report, headline measurements and
    per-stratum breakdowns alike, and fails on any unavailable row holding a
    numeric leaf."""
    body = json.loads(json.dumps(retrieval_eval.evaluate_default().as_dict()))
    checked = 0
    stack: list[Any] = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("status") == "unavailable":
                checked += 1
                assert node.get("reason", "").strip()
                numeric = [
                    (path, value)
                    for path, value in _numeric_leaves(node)
                    if not path.endswith(".sample_size")
                ]
                assert not numeric, numeric
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    # The sweep has to have found something, or it passes vacuously.
    assert checked >= 5, checked


def test_the_quality_question_is_unavailable_and_names_every_block() -> None:
    """It is never answered with a zero and never answered by the fixture. The
    blocks are listed separately because they are lifted by different work:
    human verification is cheap reading, a production sample needs a deployed
    environment with candidates in it, and a threshold needs a first baseline.
    """
    dataset = golden.load_retrieval_set()
    run = golden.load_run("reference-fixture")
    availability = golden.availability(golden.SET_RETRIEVAL, dataset.cases)
    status, reason = retrieval_eval.quality_verdict(run, availability)
    assert status == "unavailable"
    assert "recorded run" in reason
    assert "human verified" in reason
    assert "production sample" in reason
    assert "threshold" in reason
    assert retrieval_eval.QUALITY_THRESHOLDS == {}


def test_the_harness_self_check_is_a_real_gate_and_can_fail() -> None:
    """The negative direction. A self check that could not fail is a green dot,
    and the whole reason the fixture stamps expectations is so a change to the
    metric arithmetic goes red instead of moving every reported number quietly.
    """
    evaluation = retrieval_eval.evaluate_default()
    assert evaluation.harness.status == retrieval_eval.HarnessCheck.PASSED

    dataset = golden.load_retrieval_set()
    run = golden.load_run("reference-fixture")
    import dataclasses

    tampered = dataclasses.replace(
        run, expected_metrics={**run.expected_metrics, "ndcg_at_10": 0.5}
    )
    availability = golden.availability(golden.SET_RETRIEVAL, dataset.cases)
    failed = retrieval_eval.evaluate(dataset, tampered, availability)
    assert failed.harness.status == retrieval_eval.HarnessCheck.FAILED
    assert any("ndcg_at_10" in difference for difference in failed.harness.differences)


def test_an_empty_run_reports_unavailable_across_the_board_rather_than_zeroes() -> None:
    """The degenerate case a real recorded run could genuinely be in: a
    retriever that returned nothing. Every metric is unmeasurable, and every one
    of them says so."""
    import dataclasses

    dataset = golden.load_retrieval_set()
    run = golden.load_run("reference-fixture")
    empty = dataclasses.replace(run, results={"nothing-matching": ("c001",)})
    availability = golden.availability(golden.SET_RETRIEVAL, dataset.cases)
    evaluation = retrieval_eval.evaluate(dataset, empty, availability)
    for key, measurement in evaluation.report.measurements.items():
        assert not measurement.available, key
        assert (measurement.unavailable_reason or "").strip(), key
    assert evaluation.harness.status == retrieval_eval.HarnessCheck.UNAVAILABLE


def test_the_script_exits_zero_on_unavailable_and_non_zero_only_on_a_real_failure() -> None:
    """An unmeasured quantity is not a failing one, which is the same rule that
    keeps it from being reported as 0.0. The gate goes red for a broken harness,
    never for an honest absence."""
    from app.scripts import eval_retrieval as script

    assert script.main(["--gate", "--json"]) == 0
    assert script.main([]) == 0
