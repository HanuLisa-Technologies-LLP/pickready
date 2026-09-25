"""The ranked table's ONE sort key, derived in SQL at read time, and its words.

WHY THE KEY IS DERIVED AND NEVER STORED (CONTRACT v2, PLAN-p2 C1)
------------------------------------------------------------------
Where a candidate sits depends on three facts written at different times by
different parts of the product: Yukti's pre-assessment score on the link, the
Tatva Assessment's overall on the report Miti writes, and the tenant's
assessment weight. A stored blended score goes stale the moment any one of them
changes, needs a "re-blend" call somebody must remember after every report and
every ratio change, and races a concurrent Yukti rescore. So nothing is stored:
`rank_score_sql` recomputes the key per request, in ONE expression, and
`rank_score` is its pure Python twin, pinned to it by a parity test over a grid
of every input (`tests/test_yukti_rank_expression.py`), the same precedent as
`job_candidates.profile_age`.

THE EXPRESSION, IN WORDS
------------------------
* An ASSESSED candidate (their report carries an overall) ranks on
  `w x overall + (100 - w) x pre`, with `w` the tenant's
  `yukti_assessment_weight_pct` (default 70). When Yukti has not scored them,
  the overall alone.
* Anybody else ranks on their Yukti pre-assessment score, when Yukti scored
  them (`scored`, or `legacy` for a row carried over from the retired matcher).
* Nobody else has a key at all: NULL, which sorts LAST. A `pending` or
  `not_assessed` candidate is listed, never hidden and never given a number
  nobody computed (every linked candidate is listed; claude.md).
* THE MUST-HAVE CAP IS APPLIED AFTER THE BLEND, OUTERMOST. A report that says a
  Must-have skill was not demonstrated (`must_have_failed`, CONTRACT v2) holds
  the key at or below the one Must-have ceiling, as a `min` and never an
  assignment, so a candidate already below it stays below it. Applied inside
  the blend, a strong resume could carry a failed Must-have back over the
  ceiling, which is the one thing the cap exists to stop.
* `LEAST` in Postgres IGNORES a NULL argument, so `LEAST(71, NULL)` is 71. The
  cap is therefore guarded: a candidate with no key keeps no key, instead of
  being handed the ceiling as a score nobody computed.

The total order is `key DESC NULLS LAST, created_at, id`: one key for assessed
and unassessed candidates alike (the 2026-09-04 "stage first" order is
SUPERSEDED, owner decision D2), and the trailing `id` is what keeps a page
boundary stable when two keys tie.

NUMBERS NEVER LEAVE THE SERVER. The key orders rows and converts to one of the
four grade words (`grade_word`); the ratio reaches a recruiter only as a word
("mostly"); the ceiling never reaches anybody.
"""
from __future__ import annotations

from app.services import rating
from app.services.miti import caps

__all__ = [
    "RANKED_STATUSES",
    "header_sentence",
    "grade_word",
    "must_have_ceiling",
    "order_by_sql",
    "rank_score",
    "rank_score_sql",
    "weight_word",
]

#: The Yukti statuses whose pre score may rank a candidate. Written out rather
#: than imported from `yukti.config` so the SQL literal below and this tuple are
#: visibly the same two words; `tests/test_yukti_rank_expression.py` asserts
#: they equal `config.STATUS_SCORED` and `config.STATUS_LEGACY`.
RANKED_STATUSES: tuple[str, str] = ("scored", "legacy")

#: The recruiter's one line above the table when nobody on it has been assessed.
#: Owner copy, verbatim.
HEADER_RESUME_ONLY = "Resume check only. Real skills are tested in the assessment."


def must_have_ceiling() -> int:
    """The ONE Must-have cap number, read through ONE function (CONTRACT v2).

    It is the top of the Runbook's "Consider with reservations" band
    (section 10.8 via section 12.2), which grades Moderately Matching on the
    product's four-word scale. Phase 5 owns the number and exposes it as
    `miti.caps.must_have_ceiling()`; until that lands this reads the same band
    through the same module, so the value cannot differ between the two.
    """
    return caps.band_ceiling(caps.BAND_CONSIDER_WITH_RESERVATIONS)


def rank_score_sql(link: str = "l", report: str = "rep", tenant: str = "t") -> str:
    """The sort key as a SQL expression over the three aliases.

    The aliases are MODULE-CHOSEN identifiers, never caller input, and the only
    number interpolated is the ceiling, an integer read from the Runbook data.
    Every term is cast to double precision so the SQL and `rank_score` perform
    the same arithmetic (a REAL times a SMALLINT is REAL in Postgres, and a
    single-precision key would disagree with its twin in the last places).
    """
    for alias in (link, report, tenant):
        if not alias.isidentifier():
            raise ValueError(f"{alias!r} is not a SQL alias")
    ceiling = int(must_have_ceiling())
    statuses = ", ".join(f"'{status}'" for status in RANKED_STATUSES)
    pre = f"CAST({link}.yukti_pre_score AS double precision)"
    overall = f"CAST({report}.overall_score AS double precision)"
    weight = f"CAST({tenant}.yukti_assessment_weight_pct AS double precision)"
    has_pre = f"({link}.yukti_status IN ({statuses}) AND {link}.yukti_pre_score IS NOT NULL)"
    # The cap term is NULL unless the report says a Must-have failed, and
    # `LEAST(x, NULL)` is `x`. It is only ever applied to a NON-NULL `x` (both
    # branches below have a value by construction), which is what stops Postgres'
    # NULL-ignoring LEAST from turning "no key" into the ceiling.
    cap = (
        f"CASE WHEN COALESCE({report}.must_have_failed, FALSE)"
        f" THEN CAST({ceiling} AS double precision) END"
    )
    blend = (
        f"(CASE WHEN {has_pre}"
        f" THEN ({weight} * {overall} + (100.0 - {weight}) * {pre}) / 100.0"
        f" ELSE {overall} END)"
    )
    return (
        "(CASE"
        f" WHEN {report}.id IS NOT NULL AND {report}.overall_score IS NOT NULL"
        f" THEN LEAST({blend}, {cap})"
        f" WHEN {has_pre} THEN LEAST({pre}, {cap})"
        " ELSE NULL END)"
    )


def order_by_sql(link: str = "l", report: str = "rep", tenant: str = "t") -> str:
    """The table's TOTAL order: one key, then arrival, then id."""
    return (
        f"{rank_score_sql(link, report, tenant)} DESC NULLS LAST, "
        f"{link}.created_at ASC, {link}.id ASC"
    )


def rank_score(
    pre: float | None,
    status: str | None,
    overall: float | None,
    must_have_failed: bool | None,
    weight_pct: int | float,
) -> float | None:
    """The pure twin of `rank_score_sql`. `overall` is the report's overall, or
    None when there is no report or the report carries no overall."""
    has_pre = status in RANKED_STATUSES and pre is not None
    if overall is not None:
        weight = float(weight_pct)
        if has_pre:
            base: float | None = (
                weight * float(overall) + (100.0 - weight) * float(pre)
            ) / 100.0
        else:
            base = float(overall)
    elif has_pre:
        base = float(pre)
    else:
        base = None
    if base is None:
        return None
    if must_have_failed:
        return min(float(must_have_ceiling()), base)
    return base


def grade_word(score: float | None) -> str | None:
    """One of the four grade words for a key, or None for no key.

    Rounded to six places first: a blend is floating-point arithmetic, and a
    key that is exactly 90 on paper must not read as 89.99999999999999 and
    grade one band down (bands are inclusive upward, rule 8).
    """
    if score is None:
        return None
    return rating.grade_for_percent(round(float(score), 6))


def weight_word(pct: int | float) -> str:
    """How much the assessment decides an assessed candidate's place, in a word.

    100 "entirely", 60 to 99 "mostly", 41 to 59 "about half", 1 to 40
    "partly", 0 "not at all". The ratio is data a recruiter never sees as a
    number (D3), so this is the whole of what they are told about it.
    """
    value = float(pct)
    if value >= 100:
        return "entirely"
    if value >= 60:
        return "mostly"
    if value > 40:
        return "about half"
    if value > 0:
        return "partly"
    return "not at all"


def header_sentence(*, has_assessed: bool, weight_pct: int | float) -> str:
    """The one line the recruiter reads above the ranked table.

    Before anybody on the job has been assessed, the owner's sentence. After,
    a sentence that says how the order is made, in words: the ratio as a word,
    never as a number.
    """
    if not has_assessed:
        return HEADER_RESUME_ONLY
    word = weight_word(weight_pct)
    if word == "not at all":
        return (
            "Assessed candidates are ranked on their resume check, held back "
            "where the assessment found a Must-have skill missing. Everyone "
            "else is a resume check only."
        )
    return (
        f"Assessed candidates are ranked {word} on their Tatva Assessment. "
        "Everyone else is a resume check only."
    )
