"""The derived words on the recruiter home page's seven-column table.

Vivekium sprint brief, feature 3 (owner-ruled final on 2026-09-18). Four of
the seven columns are DERIVED comparisons rather than stored values, and they
are derived HERE, deterministically, with no model call:

* CTC Match          - the candidate's expected CTC against the job's range
* Notice Period      - the candidate's stated notice, bucketed
* Education Match    - highest attained level against the JD's requirement
* BGV Status         - `bgv_workflow.derive_status`, re-worded for the column

EVERY FUNCTION RETURNS A WORD OR None, and None means "Not stated": the input
was absent or unparseable, and the honest answer is that no comparison exists.
Nothing here guesses, rounds a guess, or substitutes a default, because each
of these words sits beside a hiring decision.

WHAT THIS MODULE IS NOT. It scores nothing and ranks nothing. A "Below range"
CTC or a "No match" education changes no ordering, no grade and no access;
the recruiter reads the word and decides. That is the same boundary
`validation_answers` already draws around this exact data.

The one NUMBER on the table, the Executive Profile Match Score, does not come
from here: it is `job_candidate_links.match_score` serialized directly, under
the 2026-09-18 amendment to rule 1 recorded in `claude.md`.
"""
from __future__ import annotations

import re
from typing import Any

# ── CTC Match ────────────────────────────────────────────────────────────────

#: The three words the brief specifies, exactly.
CTC_WITHIN = "Within range"
CTC_ABOVE = "Above range"
CTC_BELOW = "Below range"

#: One lakh, the unit "4 LPA" and "12L" are stated in.
_LAKH = 100_000

#: A number with optional Indian digit grouping, optionally followed by a
#: lakh/LPA marker. `4,00,000`, `400000`, `4 LPA`, `4.5lpa`, `12L`, `12 lakhs`.
_CTC_PATTERN = re.compile(
    # The comma-grouped alternative REQUIRES a comma (`+`, not `*`): with `*`
    # it also matched the first three digits of a plain "400000" and read four
    # lakh as four hundred.
    r"(?P<number>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>lpa|lakhs?|lacs?|l\b)?",
    re.IGNORECASE,
)


def parse_ctc_rupees(raw: Any) -> int | None:
    """A candidate-typed CTC as annual rupees, or None.

    The application form deliberately accepts free text (the hint says the
    field is never scored), so "4 LPA", "4,00,000" and "400000" are all real
    answers. The heuristic for a bare number: anything under 1000 is read as
    lakhs, because nobody's annual CTC is 12 rupees and nobody writes
    12,00,000 as "12" meaning rupees.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    match = _CTC_PATTERN.search(text)
    if match is None:
        return None
    try:
        value = float(match.group("number").replace(",", ""))
    except ValueError:  # a comma pattern the regex admitted but float refuses
        return None
    if value <= 0:
        return None
    unit = (match.group("unit") or "").lower()
    if unit or value < 1000:
        value *= _LAKH
    return int(value)


def ctc_match(expected_ctc: Any, compensation: Any) -> str | None:
    """Within / Above / Below the job's declared range, or None.

    Reads the canonical `ctc_min` / `ctc_max` keys the compensation editor
    writes. A job with no range, or a candidate answer that does not parse,
    is None: rendering "Within range" against a range that does not exist
    would be a comparison the data cannot support.

    Boundaries are inclusive (house rule 8): an expectation exactly at
    `ctc_max` is within the range, ties go to the candidate.
    """
    if not isinstance(compensation, dict):
        return None
    lo = compensation.get("ctc_min")
    hi = compensation.get("ctc_max")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None
    if lo <= 0 or hi <= 0 or hi < lo:
        return None
    expected = parse_ctc_rupees(expected_ctc)
    if expected is None:
        return None
    if expected > hi:
        return CTC_ABOVE
    if expected < lo:
        return CTC_BELOW
    return CTC_WITHIN


# ── Notice Period ────────────────────────────────────────────────────────────

#: The brief's buckets, verbatim, keyed by the application form's own options
#: (`application_validation.NOTICE_PERIOD_OPTIONS`). A form option maps to a
#: bucket HERE rather than being re-derived per client, so the form and the
#: column cannot drift. "Serving notice period" states no day count, so it is
#: shown as itself rather than forced into a numeric bucket it never claimed.
_NOTICE_BUCKETS: dict[str, str] = {
    "immediate": "Immediate",
    "15 days": "Within 30 days",
    "30 days": "Within 30 days",
    "45 days": "30 to 60 days",
    "60 days": "30 to 60 days",
    "90 days": "60 to 90 days",
    "serving notice period": "Serving notice",
}


def notice_period_bucket(raw: Any) -> str | None:
    """The brief's bucket for a stated notice period, or None.

    Falls back to parsing a bare day count for answers submitted before the
    select existed (the field was once free text), inclusive upward per house
    rule 8: exactly 30 is "Within 30 days", exactly 60 is "30 to 60 days".
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _NOTICE_BUCKETS:
        return _NOTICE_BUCKETS[text]
    match = re.search(r"(\d+)", text)
    if match is None:
        return None
    days = int(match.group(1))
    if days == 0:
        return "Immediate"
    if days <= 30:
        return "Within 30 days"
    if days <= 60:
        return "30 to 60 days"
    if days <= 90:
        return "60 to 90 days"
    return "Above 90 days"


# ── Education Match ──────────────────────────────────────────────────────────

EDUCATION_MATCH = "Match"
EDUCATION_PARTIAL = "Partial match"
EDUCATION_NO_MATCH = "No match"

#: The attainment ladder. School is the floor; a certification row alone
#: asserts no degree level and is deliberately absent from the ladder.
_LEVEL_SCHOOL = 0
_LEVEL_DIPLOMA = 1
_LEVEL_GRADUATION = 2
_LEVEL_POST_GRADUATION = 3
_LEVEL_DOCTORATE = 4

#: Profile-form education-table row keys (`candidate_profile_form.
#: EDUCATION_ROWS`) to the level a filled row attests. `others` and
#: `certifications` attest nothing about degree level.
_PROFILE_ROW_LEVEL: dict[str, int] = {
    "class_x": _LEVEL_SCHOOL,
    "class_xii": _LEVEL_SCHOOL,
    "diploma": _LEVEL_DIPLOMA,
    "graduation": _LEVEL_GRADUATION,
    "post_graduation": _LEVEL_POST_GRADUATION,
}

#: Keyword families for the JD's free-text education requirement, checked
#: HIGHEST FIRST so "Master's preferred, Bachelor's required" reads as the
#: higher bar it states. Word-boundary matched: the disqualifier detector
#: learned the hard way that "hold" contains "old".
_REQUIREMENT_LEVELS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (_LEVEL_DOCTORATE, ("phd", "ph.d", "doctorate", "doctoral")),
    (
        _LEVEL_POST_GRADUATION,
        (
            "master", "mba", "m.tech", "mtech", "m.e", "msc", "m.sc", "mca",
            "m.com", "postgraduate", "post-graduate", "post graduation",
            "post-graduation", "pgdm",
        ),
    ),
    (
        _LEVEL_GRADUATION,
        (
            "bachelor", "b.tech", "btech", "b.e", "bsc", "b.sc", "bca",
            "b.com", "bba", "graduate", "graduation", "degree", "engineering",
        ),
    ),
    (_LEVEL_DIPLOMA, ("diploma", "vocational", "iti")),
    (_LEVEL_SCHOOL, ("class xii", "12th", "intermediate", "class x", "10th")),
)


def required_education_level(jd_education: Any) -> int | None:
    """The level the JD's education sentence asks for, or None."""
    if jd_education is None:
        return None
    text = str(jd_education).strip().lower()
    if not text:
        return None
    for level, keywords in _REQUIREMENT_LEVELS:
        for keyword in keywords:
            if re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", text):
                return level
    return None


def attained_education_level(profile_form: Any) -> int | None:
    """The highest level the candidate's education table attests, or None."""
    if not isinstance(profile_form, dict):
        return None
    education = profile_form.get("education")
    if not isinstance(education, dict):
        return None
    levels = [
        level
        for row_key, level in _PROFILE_ROW_LEVEL.items()
        if isinstance(education.get(row_key), dict) and education[row_key]
    ]
    return max(levels) if levels else None


def education_match(profile_form: Any, jd_education: Any) -> str | None:
    """Match / Partial match / No match, or None when either side is silent.

    Partial is exactly one rung short: a diploma against a bachelor's
    requirement is a related qualification worth a human look, which is what
    the middle word exists to say. Two or more rungs short is No match.
    Meeting or exceeding the bar is Match, the same inclusive-upward
    direction every tier boundary here already takes.
    """
    required = required_education_level(jd_education)
    attained = attained_education_level(profile_form)
    if required is None or attained is None:
        return None
    if attained >= required:
        return EDUCATION_MATCH
    if attained == required - 1:
        return EDUCATION_PARTIAL
    return EDUCATION_NO_MATCH


# ── BGV Status ───────────────────────────────────────────────────────────────

#: `bgv_workflow` statuses to the column's words. The brief's vocabulary is
#: Done / Pending / Not Started; the two states it does not name are rendered
#: honestly rather than squeezed into it: a fresher's verification is not
#: "Not Started" work somebody owes, and an employer's refusal is a finding,
#: not a pending task.
_BGV_WORDS: dict[str, str] = {
    "not_required": "Not Required",
    "not_started": "Not Started",
    "pending": "Pending",
    "verified": "Done",
    "not_verified": "Not Verified",
}


def bgv_status_word(status: str | None) -> str:
    """The column word for a derived candidate-level BGV status."""
    if status is None:
        return "Not Started"
    return _BGV_WORDS.get(status, "Not Started")
