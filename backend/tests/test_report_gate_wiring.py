"""Siddhi's gate runs on the report, before the report is written.

Three properties, each protecting a different failure.

RUNS AT ALL. A gate nobody invokes reads in review exactly like an enforced
property and is worth less than no gate, because it stops anyone looking again.

RUNS BEFORE PERSISTENCE. A gate that runs afterwards has already let the report
reach the candidate table, and a recruiter who opens it in the next thirty
seconds sees an unmarked document.

CANNOT FAIL THE RUN, AND CANNOT PASS UNCHECKED. The report still ships when the
gate rejects it, marked. A gate that CRASHES returns a failing verdict
(`gate_unavailable`), never the passing one the old adapter returned.

The gate itself is `siddhi.quality_gate.evaluate`, which replaced the
orchestrator's `_gate_report` adapter in the Vivekium release: that adapter
passed one dict as both `grades` and `miti_grades`, so the grade comparison
could never fire (`tests/test_siddhi_grade_check_has_teeth.py`).
"""
import asyncio
import inspect

from app.services import functional_assessment as fa
from app.services.assessment_pipeline import composition, persistence
from app.services import verification
from app.services.siddhi import quality_gate
from app.services.siddhi import report as siddhi_report


def test_the_gate_runs_before_the_report_row_is_touched():
    """Ordering asserted on the source, because the failure is an ordering one
    and a passing end state cannot distinguish the two orders. Since WP5-D the
    gate is inside the composition stage and the only write is stage 4's
    insert, which the orchestrator calls after composition returns."""
    source = inspect.getsource(fa.run_assessment)
    assert source.index("composition.compose(") < source.index("persistence.write_report(")
    compose = inspect.getsource(composition.compose)
    assert "quality_gate.evaluate(" in compose
    assert "insert(" not in compose and "session.add" not in compose


def test_the_gate_reads_two_sources_not_one_dict_twice():
    """The grades the document states come from the stored trail inside the
    gate; Miti's grades come from Miti's skill grades. The call must not hand
    the gate the same mapping twice."""
    source = inspect.getsource(composition.compose)
    call = source[source.index("quality_gate.evaluate(") :][:900]
    assert "gap_analysis_json=gap_analysis_json" in call
    assert "miti_grades=quality_gate.miti_grades_from(miti.skills)" in call
    assert '"grades": graded' not in source


def test_the_verdict_and_siddhis_review_are_written_onto_the_row():
    """A log line is invisible to the one person who acts on the document.

    The assertion is on the FLAG BEING SET FROM BOTH VERDICTS, not on the exact
    expression, which legitimately grows.
    """
    source = inspect.getsource(composition.compose)
    flag = source[source.index("needs_review = (") :][:700]
    assert "not gate.passed" in flag
    assert "composed.needs_human_review" in flag
    assert '"review_findings_json"' in source


def test_the_stored_findings_carry_no_report_prose():
    """A finding's `detail` can quote the report, and this column is read from
    far more places than the report itself."""
    source = inspect.getsource(composition.compose)
    stored = source[source.index("findings = (") :][:700]
    assert "finding.severity" in stored
    assert "finding.issue" in stored
    assert "finding.detail" not in stored, "a finding's detail can quote the report"


def test_provenance_comes_from_the_recorder_not_the_scoring_mode():
    """`model_id` used to be stamped whenever the scoring mode was the model
    one, including on a run whose every remark fell back to a template."""
    source = inspect.getsource(persistence.write_report)
    assert '"model_id": provenance.model_id()' in source
    assert '"prompt_version": provenance.prompt_version()' in source
    assert "model_for(" not in source


def _composed(rows, exchanges, groups=()):
    return asyncio.run(
        siddhi_report.compose_prism(
            dimensions=rows,
            evidence_by_item=exchanges,
            gap_groups=list(groups),
            embed=None,
        )
    )


def _evaluate(rows, exchanges, *, groups=(), validation=None, source=None, miti=None):
    composed = _composed(rows, exchanges, groups)
    return quality_gate.evaluate(
        gap_analysis_json={"groups": list(groups), "siddhi": composed.siddhi_namespace()},
        dimensions=rows,
        overall_summary="overall remark",
        validation=validation or {},
        validation_source=source or {},
        evidence_by_item=exchanges,
        miti_grades=miti if miti is not None else {row["name"]: row["grade"] for row in rows},
        miti_overall_grade=None,
    )


PYTHON = {"name": "Python", "category": "must_have", "grade": "Matching", "remark": "Wrote the ingest path."}
PYTHON_EXCHANGE = {"Python": [{"question": "The ingest path?", "answer": "I wrote the ingest path."}]}


def test_a_clean_report_is_not_flagged():
    verdict = _evaluate(
        [PYTHON],
        PYTHON_EXCHANGE,
        validation={"notice_period": "30 days"},
        source={"notice_period": "30 days"},
    )
    assert verdict.passed, [f.as_dict() for f in verdict.findings]


def test_a_reworded_validation_field_is_caught():
    """THE case the gate exists for. Nothing scores Validation, so a report that
    reworded a notice period has fabricated a fact in a document a client makes
    a decision from."""
    verdict = _evaluate(
        [PYTHON],
        PYTHON_EXCHANGE,
        validation={"notice_period": "about three months"},
        source={"notice_period": "90 days"},
    )
    assert not verdict.passed
    assert any("validation" in f.location for f in verdict.findings)


def _gate_claims(*names: str) -> "verification.Verdict":
    """Run the gate on ungrounded claims DIRECTLY. The severity policy is the
    gate's, and it has to hold for a caller that hands the gate a claim with
    nothing behind it."""
    from app.services.agents import gates

    return gates.run_gate(
        "siddhi",
        {
            "ai_score": [],
            "ppi_assessment": [],
            "validation": {},
            "validation_source": {},
            "gap_analysis": [],
            "overall_summary": "overall remark",
            "grades": {},
            "miti_grades": {},
            "claims": [
                {"id": name, "text": "Led the team decisively.", "evidence_refs": []}
                for name in names
            ],
        },
    )


def test_one_ungrounded_claim_is_recorded_without_failing_the_report():
    verdict = _gate_claims("Leadership")
    assert verdict.passed
    assert any(f.issue == "claim_not_grounded" for f in verdict.findings)


def test_two_ungrounded_claims_do_fail_the_report():
    """One thing wrong is a slip; two independent things wrong is a pattern."""
    assert not _gate_claims("Leadership", "Ownership").passed


def test_the_gate_reads_the_answer_refs_from_the_stored_trail(monkeypatch):
    """A claim about an answered item arrives with that item's ANSWER refs, read
    from the trail the report stores; a claim resting only on the record that
    it was searched arrives with none, so `claim_not_grounded` can still fire."""
    from app.services.agents import gates

    seen: dict[str, object] = {}
    real = gates.run_gate

    def _capture(name, payload):
        seen["payload"] = payload
        return real(name, payload)

    monkeypatch.setattr(gates, "run_gate", _capture)
    unanswered = {"name": "Leadership", "category": "behavioural", "grade": "Matching", "remark": "x"}
    _evaluate([PYTHON, unanswered], PYTHON_EXCHANGE)
    claims = {claim["id"]: claim for claim in seen["payload"]["claims"]}  # type: ignore[index]
    assert claims["Python"]["evidence_refs"] == ["answer:python:0"]
    assert claims["Leadership"]["evidence_refs"] == []


def test_the_gate_is_handed_a_list_of_probes_it_can_iterate():
    """The whole `gaps` DICT was once passed where the gate iterates a LIST, so
    `grounded_in_answer` never ran on a single probe."""
    groups = [
        {
            "category": "behavioural",
            "items": [{"name": "Leadership", "probes": ["Walk me through the call."]}],
        }
    ]
    leadership = {"name": "Leadership", "category": "behavioural", "grade": "Matching", "remark": "x"}
    verdict = _evaluate([leadership], {}, groups=groups)
    assert any(f.issue == "generic_gap_probe" for f in verdict.findings)


def test_an_evidence_locator_is_not_mistaken_for_a_number_a_client_reads():
    """The sections carry client-visible fields only; a locator is an audit
    handle that exists so a grade can be traced and is never rendered."""
    row = dict(PYTHON, evidence_refs=["assessment_messages:1", "profiles:42#line7"])
    verdict = _evaluate([row], PYTHON_EXCHANGE)
    assert not any(f.issue == "number_reaches_client" for f in verdict.findings)


def test_a_broken_gate_flags_the_report_instead_of_passing_it(monkeypatch):
    """The old adapter returned a PASSING verdict on its own error. A gate that
    cannot run has checked nothing."""
    from app.services.agents import gates

    def _broken(name, payload):
        raise TypeError("state is unusable")

    monkeypatch.setattr(gates, "run_gate", _broken)
    verdict = _evaluate([PYTHON], PYTHON_EXCHANGE)
    assert not verdict.passed
    assert [f.issue for f in verdict.findings] == ["gate_unavailable"]
