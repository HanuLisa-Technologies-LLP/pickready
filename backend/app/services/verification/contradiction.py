"""Cross-source conflicts: the resume, the validation fields, and the transcript.

WHY THIS IS A FINDING AND NEVER A CORRECTION
---------------------------------------------
When a resume claims eight years and the validation form says five, something
is wrong and it is NOT this module's job to decide what. It could be a resume
written optimistically, a form filled in carelessly, a parser that read a date
range wrong, or two different definitions of "experience". Every one of those
is a different conversation for the recruiter to have, and picking one silently
is how an agent turns a discrepancy worth a question into a fact worth acting
on.

So: detected, surfaced, never resolved. The recruiter decides, exactly as they
decide whether stated interest is genuine. That is the same division of labour
the Validation section already runs on -- factual application data is captured
and never scored.

THRESHOLDS ARE WIDE ON PURPOSE
------------------------------
A one-year difference between a resume and a form is normal rounding and firing
on it would bury the eight-year case that matters. Everything here is tuned to
miss the ambiguous cases rather than to catch them, because a contradiction
detector nobody trusts gets switched off.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services.verification import base

#: Years of difference before an experience claim is worth raising. Two, so a
#: resume that rounds up and a form that rounds down never collide.
EXPERIENCE_TOLERANCE_YEARS = 2.0


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _terms(values: Sequence[Any]) -> set[str]:
    return {
        " ".join(str(value).casefold().split())
        for value in values or ()
        if str(value or "").strip()
    }


def verify_consistency(
    *,
    resume_skills: Sequence[str] = (),
    resume_experience_years: Any = None,
    validation_experience_years: Any = None,
    claimed_skills: Sequence[str] = (),
    unanswered_skills: Sequence[str] = (),
) -> base.Verdict:
    """Findings where two sources about one candidate disagree.

    `claimed_skills` are skills the candidate asserted in conversation;
    `unanswered_skills` are criteria they left unanswered or answered as a
    non-answer. A skill claimed on a resume and abandoned when asked about is
    the single most useful contradiction in the set, and it is the one a report
    reading only the resume can never surface.
    """
    findings: list[base.Finding] = []

    resume_years = _number(resume_experience_years)
    stated_years = _number(validation_experience_years)
    if resume_years is not None and stated_years is not None:
        gap = abs(resume_years - stated_years)
        if gap > EXPERIENCE_TOLERANCE_YEARS:
            findings.append(
                base.medium(
                    "experience_conflict",
                    "experience_years",
                    (
                        "the resume and the application form state different "
                        f"lengths of experience, {gap:g} years apart"
                    ),
                    (
                        "raise the difference with the candidate rather than "
                        "choosing one figure; do not state either as fact"
                    ),
                )
            )

    resume = _terms(resume_skills)
    abandoned = _terms(unanswered_skills) & resume
    for skill in sorted(abandoned):
        findings.append(
            base.medium(
                "claimed_but_unevidenced",
                f"skills.{skill}",
                (
                    f"{skill} appears on the resume and the candidate gave no "
                    "usable answer when asked about it"
                ),
                (
                    f"ask about {skill} directly at interview; report it as "
                    "unevidenced rather than as absent"
                ),
            )
        )

    invented = _terms(claimed_skills) - resume
    for skill in sorted(invented):
        findings.append(
            base.low(
                "claimed_beyond_resume",
                f"skills.{skill}",
                (
                    f"the candidate described {skill} in conversation and it "
                    "does not appear on their resume"
                ),
                (
                    f"treat {skill} as new information worth confirming, not as "
                    "a discrepancy against the candidate"
                ),
            )
        )

    return base.verdict("contradiction", findings)
