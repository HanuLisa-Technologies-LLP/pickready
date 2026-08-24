"""Siddhi's gate runs on the report, before the report is written.

Three properties, each protecting a different failure.

RUNS AT ALL. A gate nobody invokes reads in review exactly like an enforced
property and is worth less than no gate, because it stops anyone looking again.

RUNS BEFORE PERSISTENCE. A gate that runs afterwards has already let the report
reach the candidate table, and a recruiter who opens it in the next thirty
seconds sees an unmarked document.

CANNOT FAIL THE RUN. The report still ships when the gate rejects it, marked.
Refusing to write one would take the product's whole output away over what may
be a single ungrounded phrase, leaving the recruiter with nothing instead of
something imperfect they can judge.
"""
import inspect

from app.services import functional_assessment as fa
from app.services import verification


def test_the_gate_runs_before_the_report_row_is_touched():
    """Ordering asserted on the source, because the failure is an ordering one
    and a passing end state cannot distinguish the two orders."""
    source = inspect.getsource(fa.synthesis_node)
    gate_at = source.index("_gate_report(")
    select_at = source.index("select(FunctionalSkillsReport)")
    assert gate_at < select_at, "the gate must run before the report is loaded or written"


def test_the_verdict_is_written_onto_the_row():
    """A log line is invisible to the one person who acts on the document."""
    source = inspect.getsource(fa.synthesis_node)
    assert '"needs_human_review": not gate_verdict.passed' in source
    assert '"review_findings_json"' in source


def test_the_stored_findings_carry_no_report_prose():
    """A finding's `detail` can quote the report, and this column is read from
    far more places than the report itself."""
    source = inspect.getsource(fa.synthesis_node)
    stored = source[source.index('"review_findings_json"') : source.index('"review_findings_json"') + 600]
    assert "finding.severity" in stored
    assert "finding.issue" in stored
    assert "finding.detail" not in stored, "a finding's detail can quote the report"


def test_a_clean_report_is_not_flagged():
    verdict = fa._gate_report(
        {"link": _Link(), "validation": {"notice_period": "30 days"}},
        [
            {
                "name": "Python",
                "category": "must_have",
                "grade": "Matching",
                "remark": "x",
                "evidence_refs": ["assessment_messages:1"],
            }
        ],
        "overall remark",
        {"groups": []},
        {"notice_period": "30 days"},
    )
    assert verdict.passed, [f.as_dict() for f in verdict.findings]


def test_a_reworded_validation_field_is_caught():
    """THE case the gate exists for. Nothing scores Validation, so a report that
    reworded a notice period has fabricated a fact in a document a client makes
    a decision from."""
    verdict = fa._gate_report(
        {"link": _Link(), "validation": {"notice_period": "90 days"}},
        [
            {
                "name": "Python",
                "category": "must_have",
                "grade": "Matching",
                "remark": "x",
                "evidence_refs": ["assessment_messages:1"],
            }
        ],
        "overall remark",
        {"groups": []},
        {"notice_period": "about three months"},
    )
    assert not verdict.passed
    assert any("validation" in f.location for f in verdict.findings), [
        f.as_dict() for f in verdict.findings
    ]


def _ungrounded(name: str) -> dict:
    return {
        "name": name,
        "category": "behavioural",
        "grade": "Highly Matching",
        "remark": "Led the team decisively.",
        "evidence_refs": [],
    }


def test_one_ungrounded_claim_is_recorded_without_failing_the_report():
    """The severity policy in `verification/base` is deliberate and this pins
    the quiet half of it: one medium finding is worth telling the next attempt
    about and is not worth discarding an otherwise sound report over."""
    verdict = fa._gate_report(
        {"link": _Link(), "validation": {}},
        [_ungrounded("Leadership")],
        "overall remark",
        {"groups": []},
        {},
    )
    assert verdict.passed
    assert any(f.issue == "claim_not_grounded" for f in verdict.findings)


def test_two_ungrounded_claims_do_fail_the_report():
    """One thing wrong is a slip; two independent things wrong is a pattern."""
    verdict = fa._gate_report(
        {"link": _Link(), "validation": {}},
        [_ungrounded("Leadership"), _ungrounded("Ownership")],
        "overall remark",
        {"groups": []},
        {},
    )
    assert not verdict.passed


def test_an_evidence_locator_is_not_mistaken_for_a_number_a_client_reads():
    """Found by the gate itself while this adapter was being written. The first
    version passed raw dimension rows and was rejected with
    `number_reaches_client` pointing at `assessment_messages:1` -- an internal
    audit handle that exists so a grade can be traced and is never rendered.
    The sections now carry client-visible fields only."""
    verdict = fa._gate_report(
        {"link": _Link(), "validation": {}},
        [
            {
                "name": "Python",
                "category": "must_have",
                "grade": "Matching",
                "remark": "x",
                "evidence_refs": ["assessment_messages:1", "profiles:42#line7"],
            }
        ],
        "overall remark",
        {"groups": []},
        {},
    )
    assert not any(f.issue == "number_reaches_client" for f in verdict.findings), [
        f.as_dict() for f in verdict.findings
    ]


def test_a_broken_gate_never_costs_the_report():
    """A guard that can fail the run it guards turns a cosmetic defect into a
    lost report."""
    verdict = fa._gate_report(_Exploding(), [], "", {}, {})
    assert verdict.passed
    assert any(f.issue == "gate_unavailable" for f in verdict.findings)


class _Link:
    id = "00000000-0000-0000-0000-000000000001"


class _Exploding(dict):
    def __getitem__(self, key):
        if key == "link":
            return _Link()
        raise RuntimeError("state is unusable")

    def get(self, key, default=None):
        raise RuntimeError("state is unusable")
