"""The final ranking: the pre-assessment order is not the final order.

Workflow sections 43 and 44. Until an assessment exists, a candidate is ranked
on their resume, which is a document they wrote about themselves. Once it
exists, they are ranked on it -- which is the only evidence in the product that
was actually observed. The property the workflow document asks for in as many
words is that "a candidate who ranked highly before assessment can move down",
and a ranking whose top key is a resume score cannot do that at all.

These are tests of the ORDER BY, not of a query plan: the clause is built by a
pure function precisely so the ordering rule can be asserted directly rather
than inferred from a page of results.
"""
from __future__ import annotations

import pytest

from app.services import job_candidates as jc


def _positions(clause: str, *fragments: str) -> list[int]:
    found = []
    for fragment in fragments:
        assert fragment in clause, f"{fragment!r} is not in the ORDER BY"
        found.append(clause.index(fragment))
    return found


@pytest.mark.parametrize(
    "grade", ["non_managerial", "managerial", "leadership", "cxo", None]
)
def test_the_assessment_outranks_the_resume_for_every_grade(grade) -> None:
    """Stage first, then the assessment, then the resume keys.

    The grade decides the order of the RESUME keys and nothing above them: a
    CXO and a graduate are both ranked on their assessment once they have one.
    """
    clause = jc.order_by_clause(grade)
    assessed, score, skills = _positions(
        clause,
        "(rep.synthesized_at IS NOT NULL)",
        "rep.overall_score",
        "skills_match",
    )
    assert assessed < score < skills


def test_an_assessed_candidate_ranks_above_an_unassessed_one() -> None:
    """The first key is presence of a delivered report, descending.

    This is what makes the assessed shortlist the top of the table. A recruiter
    working down the list is working down the candidates who have evidence,
    which is the list they are actually deciding from.
    """
    clause = jc.order_by_clause("non_managerial")
    assert clause.startswith("(rep.synthesized_at IS NOT NULL) DESC,")


def test_the_assessment_stage_is_read_from_the_report_not_the_status() -> None:
    """`rep.synthesized_at`, never `l.status`.

    `assessment_completed` is a denormalised mirror written by the transition
    service; the REPORT is the artifact the ranking is about. Ordering on the
    status would put a candidate whose assessment finished but whose synthesis
    failed above candidates who have a real report, which is the exact
    "a timestamp is not evidence that work happened" failure this codebase has
    already paid for once.
    """
    clause = jc.order_by_clause("cxo")
    assert "rep.synthesized_at" in clause
    assert "l.status" not in clause


def test_the_resume_keys_survive_underneath() -> None:
    """They are not dropped once a report exists, and both reasons matter.

    They are the WHOLE order for the unassessed pool, which is most of the
    table for most of a posting's life. And among the assessed they break a tie
    between two identical assessment scores -- a real and common case, because
    the delivered grade has four bands.
    """
    clause = jc.order_by_clause("non_managerial")
    for fragment in ("skills_match", "experience_relevance", "pfi.pfi_score"):
        assert fragment in clause


def test_the_grade_still_decides_the_resume_key_order() -> None:
    """The 2026-07-27 rule is unchanged below the new keys."""
    non_mgr = jc.order_by_clause("non_managerial")
    cxo = jc.order_by_clause("cxo")
    assert non_mgr.index("experience_relevance") < non_mgr.index("pfi.pfi_score")
    assert cxo.index("pfi.pfi_score") < cxo.index("experience_relevance")


def test_every_score_key_sinks_nulls() -> None:
    """An unassessed or unscored candidate sinks; it is never dropped.

    Retrieval and scoring state must never decide who is VISIBLE. Every linked
    candidate is scored and every linked candidate is listed, so a NULL has to
    sort last rather than filter the row out.
    """
    clause = jc.order_by_clause("managerial")
    assert clause.count("DESC NULLS LAST") == 4
    # The stage key is a boolean and cannot be NULL, so it takes a plain DESC.
    assert "(rep.synthesized_at IS NOT NULL) DESC," in clause


def test_the_order_is_still_total() -> None:
    """Adding keys must not cost the id tiebreak.

    Without it two equally-ranked candidates can swap places between page 1 and
    page 2, and one of them vanishes from the paginated result entirely.
    """
    for grade in ("non_managerial", "managerial", "leadership", "cxo", None):
        assert jc.order_by_clause(grade).endswith("l.created_at ASC, l.id ASC")


def test_no_caller_input_reaches_the_order_by() -> None:
    """An unknown grade falls back; it is never interpolated.

    The clause is composed from module constants keyed by the job's own grade,
    which is what makes it safe to build with an f-string at the call site.
    """
    injected = jc.order_by_clause("'; DROP TABLE jobs; --")
    assert injected == jc.order_by_clause("managerial")
    assert "DROP" not in injected
