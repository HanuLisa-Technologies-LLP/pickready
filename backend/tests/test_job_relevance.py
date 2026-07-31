"""Candidate-side job relevance (services/job_relevance.py).

This ranking decides only what the candidate's New Jobs board SHOWS. It must
never become a gate on who gets scored — that remains every non-archived link
on the job (claude.md hard rule) — so these tests pin the two behaviours that
protect a candidate from an empty or misleading board:

  * a candidate with no usable profile signal sees everything;
  * search is an escape hatch that ignores relevance entirely.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import job_relevance as jr


def _job(title: str, skills: list[str] | None = None, **jd) -> SimpleNamespace:
    return SimpleNamespace(
        id=title,
        title=title,
        level=None,
        department=None,
        jd_json={"skills": skills or [], **jd},
    )


def _signal(text: str = "", *, embedding: bool = False) -> jr.CandidateSignal:
    profile = SimpleNamespace(
        id="p1", embedding=[0.0] if embedding else None,
        resume_text=text, parsed_fields_json=None,
    )
    return jr.candidate_signal(profile, None)


def test_a_candidate_with_no_profile_signal_is_never_filtered() -> None:
    """An empty profile must not produce an empty board — relevance would be a
    coin toss, so everything is shown and the candidate can search."""
    signal = jr.candidate_signal(None, None)
    assert signal.is_empty is True

    ranked = [jr.RankedJob(_job("Anything"), 0.0, False) for _ in range(4)]
    assert len(jr.visible(ranked, signal)) == 4


def test_keyword_relevance_prefers_the_candidates_own_skills() -> None:
    signal = _signal("Built Kubernetes and Terraform pipelines on AWS for five years.")
    devops = _job("Platform Engineer", ["Kubernetes", "Terraform", "AWS"])
    payroll = _job("Payroll Specialist", ["Payroll", "Statutory compliance"])

    assert jr.keyword_score(signal, devops) > jr.keyword_score(signal, payroll)


def test_a_thin_board_still_shows_a_minimum_number_of_roles() -> None:
    """Never strand a candidate on an empty page just because nothing cleared
    the relevance floor."""
    signal = _signal("Payroll and statutory compliance for a 400-person firm.")
    jobs = [_job(f"Unrelated {n}", ["Welding"]) for n in range(10)]
    ranked = [jr.RankedJob(job, 0.0, False) for job in jobs]

    shown = jr.visible(ranked, signal)
    assert len(shown) == jr.MIN_RESULTS


def test_search_matches_every_token_across_the_visible_fields() -> None:
    job = _job("Senior Python Backend Developer", ["FastAPI", "PostgreSQL"])

    assert jr.matches_search(job, "python") is True
    assert jr.matches_search(job, "PYTHON backend") is True   # case-insensitive
    assert jr.matches_search(job, "fastapi") is True          # skills are searched
    assert jr.matches_search(job, "eng") is False             # no such substring
    assert jr.matches_search(job, "python payroll") is False  # every token must hit
    assert jr.matches_search(job, "   ") is True              # blank matches all


async def test_ranking_orders_by_score_and_survives_a_missing_embedding() -> None:
    """No embedding on either side must degrade to keyword scoring, not to a
    board of zeros."""
    signal = _signal("Kubernetes Terraform AWS platform reliability")
    jobs = [
        _job("Payroll Specialist", ["Payroll"]),
        _job("Platform Engineer", ["Kubernetes", "Terraform", "AWS"]),
    ]
    ranked = await jr.rank_jobs(None, jobs, signal)

    assert [item.job.title for item in ranked][0] == "Platform Engineer"
    assert ranked[0].score > ranked[-1].score
