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
    """A log line is invisible to the one person who acts on the document.

    The assertion is on the FLAG BEING SET FROM THE VERDICT, not on the exact
    expression. The expression legitimately grows: a failing gate is one reason
    to flag a report and the evidence ledger's own uncertainty is another, and
    the day a third is added this test must not fail for having memorised the
    line. What it must still catch is the verdict being dropped out of it,
    which is the change that would leave a rejected report reading as clean.
    """
    source = inspect.getsource(fa.synthesis_node)
    flag = source[source.index('"needs_human_review"') :][:400]
    assert "gate_verdict.passed" in flag
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


def _gate_claims(*names: str) -> "verification.Verdict":
    """Run the gate on ungrounded claims DIRECTLY, not through the adapter.

    The severity policy is the gate's, and since Siddhi went live the adapter
    can no longer produce its input: `synthesis.evidence_refs_for` mints a
    `searched` record for every rated item, so a claim about an item on the
    report always carries at least that. The policy still has to hold for a
    caller that hands the gate a claim with nothing behind it, and this is where
    it is now exercised.
    """
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
    """The severity policy in `verification/base` is deliberate and this pins
    the quiet half of it: one medium finding is worth telling the next attempt
    about and is not worth discarding an otherwise sound report over."""
    verdict = _gate_claims("Leadership")
    assert verdict.passed
    assert any(f.issue == "claim_not_grounded" for f in verdict.findings)


def test_two_ungrounded_claims_do_fail_the_report():
    """One thing wrong is a slip; two independent things wrong is a pattern."""
    assert not _gate_claims("Leadership", "Ownership").passed


class _Competency:
    def __init__(self, name: str) -> None:
        self.id = f"competency-{name}"
        self.name = name


class _Question:
    def __init__(self, competency: _Competency) -> None:
        self.id = f"question-{competency.name}"
        self.competency_id = competency.id
        self.prompt = f"Walk me through your work on {competency.name}."


def _answered_state(name: str) -> dict:
    """A state where exactly one competency actually has an answer behind it."""
    competency = _Competency(name)
    question = _Question(competency)
    return {
        "link": _Link(),
        "validation": {},
        "competencies": [competency],
        "candidate_questions": [question],
        "answers": {question.id: ["I rebuilt the ingest path myself over two sprints."]},
    }


def test_the_adapter_hands_the_gate_the_refs_siddhi_actually_minted(monkeypatch):
    """THE REGRESSION THIS PART OF THE FILE EXISTS TO PIN.

    A dimension row carries no `evidence_refs` key and never has, so the
    adapter's `row.get("evidence_refs") or []` gave the gate an empty list for
    every claim. Two rated items produced two `claim_not_grounded` findings, the
    gate failed, and EVERY report with more than one dimension was written
    `needs_human_review=True`. A check wired to something it cannot read does
    not report a wiring error; it reports a blanket verdict, which is
    indistinguishable from the product working and flagging everybody.

    The refs now come from Siddhi's own index, and they are the ANSWER refs
    rather than everything the index holds. That distinction is deliberate and
    is the reason this test asserts BOTH directions below: a claim resting only
    on the record that a criterion was searched IS a weaker claim, so
    `claim_not_grounded` has to stay able to fire, or the fix would swing the
    defect the other way into a check that reads as enforced and never is.
    """
    from app.services.agents import gates
    from app.services.siddhi import evidence as siddhi_evidence

    seen: dict[str, object] = {}
    real = gates.run_gate

    def _capture(name, payload):
        seen["payload"] = payload
        return real(name, payload)

    monkeypatch.setattr(gates, "run_gate", _capture)
    verdict = fa._gate_report(
        _answered_state("Leadership"),
        [_ungrounded("Leadership")],
        "overall remark",
        {"groups": []},
        {},
    )
    assert not any(f.issue == "claim_not_grounded" for f in verdict.findings), [
        f.as_dict() for f in verdict.findings
    ]

    # Asserted against the payload the gate actually received, and compared to
    # Siddhi's own index rather than to a literal, so this keeps holding
    # whichever node kinds the adapter chooses to expose. What it cannot survive
    # is a revert to `row.get("evidence_refs")`: that key does not exist on a
    # dimension row, so the claim would arrive with nothing while the index
    # plainly holds an answer node for the same item.
    index = siddhi_evidence.EvidenceIndex.build(
        items=["Leadership"],
        exchanges={
            "Leadership": [
                {
                    "question": "Walk me through your work on Leadership.",
                    "answer": "I rebuilt the ingest path myself over two sprints.",
                }
            ]
        },
    )
    claim = seen["payload"]["claims"][0]  # type: ignore[index]
    assert claim["id"] == "Leadership"
    assert claim["evidence_refs"]
    assert set(claim["evidence_refs"]) <= set(index.refs_for("Leadership"))


def test_the_adapter_hands_the_gate_a_list_of_probes_it_can_iterate():
    """The same defect class in the opposite direction. The whole `gaps` DICT
    was passed where the gate iterates a LIST, so `grounded_in_answer` never ran
    on a single probe and the section reported a clean pass rather than an
    error."""
    verdict = fa._gate_report(
        {"link": _Link(), "validation": {}},
        [_ungrounded("Leadership")],
        "overall remark",
        {
            "groups": [
                {
                    "category": "behavioural",
                    "items": [
                        {"name": "Leadership", "probes": ["Walk me through the call."]}
                    ],
                }
            ]
        },
        {},
    )
    # No exchange was recorded for Leadership, so the probe cannot be grounded
    # in an answer and the gate says so. The finding is the proof the check ran.
    assert any(f.issue == "generic_gap_probe" for f in verdict.findings), [
        f.as_dict() for f in verdict.findings
    ]


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
