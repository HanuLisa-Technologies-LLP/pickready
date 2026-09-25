"""SCAFFOLD FOR WP-E ONLY. DROP THIS FILE AT INTEGRATION; WP-C OWNS IT.

Phase 2 WP-E (the Candidate Dashboard) reads the ONE rank expression that
WP-C (`app/services/yukti/ranking.py`) owns, and WP-C had not landed on WP-E's
base. This file carries exactly the two symbols WP-E calls, written from
PLAN-p2 section 3.7 so the dashboard is built and tested against the
interface the plan fixes:

    rank_score_sql(link="l", report="rep", tenant="t") -> str
    grade_word(score) -> str | None

It is committed on its own, in a commit named for dropping, so the
orchestrator takes WP-C's file and discards this one. Nothing else in WP-E
defines a rank: the dashboard calls these two names and nothing more.
"""
from __future__ import annotations

from app.services import rating

__all__ = ["must_have_cap", "rank_score_sql", "rank_score", "grade_word"]


def must_have_cap() -> int:
    """The Must-have ceiling (CONTRACT P2: Phase 5 owns the number)."""
    return rating.MODERATELY_CEILING


def rank_score_sql(link: str = "l", report: str = "rep", tenant: str = "t") -> str:
    """The derived rank score, PLAN-p2 3.7, verbatim in shape."""
    cap = must_have_cap()
    return (
        "(CASE"
        f" WHEN {report}.id IS NOT NULL AND {report}.overall_score IS NOT NULL THEN"
        f"   LEAST(CASE WHEN {report}.must_have_failed THEN {cap} ELSE 100 END,"
        f"         CASE WHEN {link}.yukti_pre_score IS NULL"
        f"              OR {link}.yukti_status NOT IN ('scored','legacy')"
        f"              THEN {report}.overall_score"
        f"              ELSE ({tenant}.yukti_assessment_weight_pct * {report}.overall_score"
        f"                    + (100 - {tenant}.yukti_assessment_weight_pct)"
        f"                      * {link}.yukti_pre_score) / 100.0 END)"
        f" WHEN {link}.yukti_status IN ('scored','legacy') THEN {link}.yukti_pre_score"
        " ELSE NULL END)"
    )


def rank_score(
    pre: float | None,
    status: str | None,
    overall: float | None,
    must_have_failed: bool,
    weight_pct: int,
) -> float | None:
    """The pure twin of `rank_score_sql`."""
    usable_pre = pre is not None and status in ("scored", "legacy")
    if overall is not None:
        blended = (
            float(overall)
            if not usable_pre
            else (weight_pct * float(overall) + (100 - weight_pct) * float(pre)) / 100.0
        )
        return min(must_have_cap() if must_have_failed else 100, blended)
    if usable_pre:
        return float(pre)
    return None


def grade_word(score: float | int | None) -> str | None:
    return rating.grade_for_percent(score)
