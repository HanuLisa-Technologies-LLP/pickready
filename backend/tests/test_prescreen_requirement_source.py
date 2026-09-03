"""Where a pre-screen requirement came from, and the state it graded against.

TWO GRADES ON ONE DASHBOARD MUST BE COMPARABLE, OR THE COLUMN IS WORSE THAN
EMPTY. Pre-screen grading runs at resume upload, which is before any scorecard
can be frozen, so the requirements come from an approved competency list when
one exists and from the job description otherwise. Both are legitimate; what is
not legitimate is degrading between them silently, because a recruiter reading
two rows would have no way to know that one was graded against what a human
agreed the job needs and the other against whatever the JD happened to list.
`requirement_source` is that record, and it travels with the grade.

THE STATE MODEL IS A SET, NOT A LABEL. A returner can also be a career changer
and section 39's table does not say which wins, so all of it travels and the
PRIMARY is fixed by a declared precedence -- deterministic, because a dashboard
cell that varied between two reads of one row is a cell nobody can act on.

The fresher rule is the one worth reading twice: no documented employment and
no stated total, with an academic record, is a FRESHER rather than an
undocumented work history. Reading it the other way would treat the candidate
with the least to hide as the one with the most.

Pure functions over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.hiring import prescreen


TODAY = date(2026, 9, 1)


def _span(start: str, end: str | None, title: str = "Engineer") -> prescreen.EmploymentSpan:
    return prescreen.EmploymentSpan(
        title=title,
        start=prescreen._as_date(start),
        end=None if end is None else prescreen._as_date(end),
    )


# ── Where the requirements came from ─────────────────────────────────────────


def test_an_approved_competency_list_is_used_and_said_so() -> None:
    """It is what a human actually agreed the job needs."""
    terms, source = prescreen.requirement_terms(
        competencies=["Stream processing", "Ownership"],
        jd_skills=["Kafka"],
        job_title="Staff Engineer",
    )
    assert terms == ("Stream processing", "Ownership")
    assert source == prescreen.REQUIREMENTS_FROM_COMPETENCIES


def test_the_job_description_is_used_when_no_competencies_exist() -> None:
    terms, source = prescreen.requirement_terms(
        jd_skills=["Kafka", "Airflow"], job_title="Staff Engineer"
    )
    assert terms == ("Kafka", "Airflow", "Staff Engineer")
    assert source == prescreen.REQUIREMENTS_FROM_JD


def test_the_title_is_only_added_to_the_job_description_route() -> None:
    """A competency list is a complete statement of what is being graded; adding
    the title to it would introduce a requirement nobody approved."""
    terms, _source = prescreen.requirement_terms(
        competencies=["Stream processing"], job_title="Staff Engineer"
    )
    assert "Staff Engineer" not in terms


def test_a_blank_title_adds_nothing() -> None:
    terms, _source = prescreen.requirement_terms(jd_skills=["Kafka"], job_title="   ")
    assert terms == ("Kafka",)
    assert prescreen.requirement_terms(jd_skills=["Kafka"], job_title=None)[0] == (
        "Kafka",
    )


def test_blank_entries_are_dropped_from_either_route() -> None:
    """An empty requirement grades every candidate as failing to meet something
    nobody asked for."""
    assert prescreen.requirement_terms(competencies=["", "   ", "Kafka"])[0] == (
        "Kafka",
    )
    assert prescreen.requirement_terms(jd_skills=["", "  "])[0] == ()


def test_a_repeated_requirement_is_stated_once() -> None:
    """Counted twice it would be weighted twice, which is repetition being read
    as importance."""
    terms, _source = prescreen.requirement_terms(
        jd_skills=["Kafka", "Kafka"], job_title="Kafka"
    )
    assert terms == ("Kafka",)


def test_nothing_at_all_still_reports_which_route_it_took() -> None:
    """An empty requirement set is a real state -- a JD with no skills listed --
    and the source still has to be recorded, or the row is unreadable."""
    terms, source = prescreen.requirement_terms()
    assert terms == ()
    assert source == prescreen.REQUIREMENTS_FROM_JD


# ── The tokens a requirement is compared on ──────────────────────────────────


def test_nothing_yields_no_terms_rather_than_a_term_that_is_blank() -> None:
    assert prescreen._terms(None) == set()
    assert prescreen._terms("") == set()


def test_punctuation_around_a_term_is_stripped() -> None:
    assert "kafka" in prescreen._terms("Kafka. Airflow/Spark -")


# ── Section 39's state model ─────────────────────────────────────────────────


def test_a_short_career_is_a_fresher() -> None:
    states, primary = prescreen.candidate_states(
        total_experience_years=0.5,
        spans=(),
        job_terms=frozenset(),
        has_academic_claims=False,
        today=TODAY,
    )
    assert prescreen.STATE_FRESHER in states
    assert primary in states


def test_an_academic_record_with_no_employment_and_no_total_is_a_fresher() -> None:
    """The rule worth reading twice. Reading it the other way would treat the
    candidate with the least to hide as the one with the most."""
    states, _primary = prescreen.candidate_states(
        total_experience_years=None,
        spans=(),
        job_terms=frozenset(),
        has_academic_claims=True,
        today=TODAY,
    )
    assert prescreen.STATE_FRESHER in states


def test_no_employment_no_total_and_no_academic_claims_is_not_a_fresher() -> None:
    """A resume that says nothing at all is unreadable, not junior. Calling it
    fresher would put a number on an absence."""
    states, _primary = prescreen.candidate_states(
        total_experience_years=None,
        spans=(),
        job_terms=frozenset(),
        has_academic_claims=False,
        today=TODAY,
    )
    assert prescreen.STATE_FRESHER not in states


def test_a_long_career_is_never_a_fresher() -> None:
    states, _primary = prescreen.candidate_states(
        total_experience_years=12.0,
        spans=(_span("2014-01", "2026-01"),),
        job_terms=frozenset(),
        has_academic_claims=False,
        today=TODAY,
    )
    assert prescreen.STATE_FRESHER not in states


def test_the_primary_state_is_always_one_of_the_states_found() -> None:
    """A dashboard cell showing a state the set does not contain would be
    unexplainable from the row it sits on."""
    for years, spans, academic in (
        (0.5, (), False),
        (None, (), True),
        (12.0, (_span("2014-01", "2026-01"),), False),
        (6.0, (_span("2018-01", "2021-01"), _span("2024-06", None)), False),
    ):
        states, primary = prescreen.candidate_states(
            total_experience_years=years,
            spans=spans,
            job_terms=frozenset({"kafka"}),
            has_academic_claims=academic,
            today=TODAY,
        )
        assert primary in states or not states, (years, primary, states)


def test_the_same_inputs_always_produce_the_same_primary() -> None:
    kwargs = dict(
        total_experience_years=6.0,
        spans=(_span("2018-01", "2021-01"), _span("2024-06", None)),
        job_terms=frozenset({"kafka"}),
        has_academic_claims=False,
        today=TODAY,
    )
    first = prescreen.candidate_states(**kwargs)
    second = prescreen.candidate_states(**kwargs)
    assert first == second


# ── Strength over no claims at all ───────────────────────────────────────────


def test_no_claims_have_no_strength_rather_than_an_error() -> None:
    """An unreadable resume reaches this function with nothing in it. Zero is
    the honest reading and it routes to Hold, not to a low grade."""
    assert prescreen.effective_strength((), prescreen.CLOCK_FAST) == 0.0
