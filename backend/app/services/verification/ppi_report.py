"""Critic for the PPI assessment: the graded items and the Overall remark.

THE SPEC'S FIVE LABELS ARE NOT THIS PRODUCT'S SCALE
---------------------------------------------------
The specification asks this critic to assert "word labels only: Very High,
High, Medium, Low, Developing". Those five were the assessment half of two
parallel scales the product used to run, and they were collapsed into the four
grades of `services.rating` on 2026-07-30 precisely because a reader had no way
to know that a "High" and a "Matching" meant the same thing. A verifier
enforcing the retired labels would reject every report written since. It checks
`rating.GRADES`, and it checks them by importing them.

WHAT IT ACTUALLY GUARDS
-----------------------
Three properties, and each has cost the product something before:

  No number reaches a client. The rule predates every other rule in this file.
  Grades are words, and a remark that names a score is a leak whether or not
  anybody meant it as one.

  The Must-have cap held. Any Must-have graded Not Matching caps Overall at
  Moderately Matching. That single sentence is what a client relies on when
  they read "Matching" and conclude the mandatory criteria were met, so a
  report where the arithmetic and the cap disagree is worse than no report.

  Remarks are 45-50 words AND specific. The word range is a spec constant
  imported from `functional_assessment`; the specificity is why the generic
  language finding is HIGH severity here and medium elsewhere. A 48-word remark
  built from filler satisfies every mechanical check and tells a hiring manager
  nothing at all, which is the exact failure this whole package exists for.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services import conversation_guardrails, functional_assessment, ppi, rating
from app.services.verification import base, generic_language

_REMARK_MIN, _REMARK_MAX = functional_assessment.PPI_REMARK_WORDS


def verify_report(
    dimensions: Sequence[dict[str, Any]],
    *,
    overall_remark: str | None = None,
    overall_grade: str | None = None,
) -> base.Verdict:
    """Verify the PPI half of one report.

    `dimensions` is the internal row shape synthesis produces -- category, name,
    score, remark, ordinal -- not the client-facing payload. Verifying the
    internal shape is deliberate: the score is still present there, which is the
    only place a cap violation or a leaked number can actually be detected.
    """
    findings: list[base.Finding] = []
    ppi_rows = [
        row for row in dimensions if row.get("category") in ppi.CATEGORIES
    ]

    findings.extend(_coverage_findings(ppi_rows))
    for index, row in enumerate(ppi_rows):
        findings.extend(_row_findings(row, f"dimensions[{index}]"))
    findings.extend(_cap_findings(ppi_rows, overall_grade))

    if overall_remark is not None:
        findings.extend(
            _remark_findings(overall_remark, "overall_remark", name="the Overall remark")
        )

    return base.verdict("ppi_report", findings)


def _coverage_findings(rows: Sequence[dict[str, Any]]) -> list[base.Finding]:
    """Every aspect the framework defines must appear in the report.

    A missing category is not a shorter report, it is a report that silently
    did not assess something the job was graded against -- the same class of
    defect as a framework stamped as generated with no rows behind it.
    """
    present = {row.get("category") for row in rows}
    return [
        base.high(
            "missing_category",
            f"dimensions[{category}]",
            f"no {ppi.CATEGORY_LABELS.get(category, category)} item is present",
            f"assess and report every {ppi.CATEGORY_LABELS.get(category, category)} "
            "criterion in the saved framework",
        )
        for category in ppi.CATEGORIES
        if category not in present
    ]


def _row_findings(row: dict[str, Any], location: str) -> list[base.Finding]:
    findings: list[base.Finding] = []
    name = str(row.get("name") or "").strip()

    if not name:
        findings.append(
            base.high(
                "missing_name",
                location,
                "the item has no criterion name",
                "name the criterion this item assesses",
            )
        )
    elif ppi.is_forbidden_competency(name):
        # Refused at three layers already -- the generator prompt, the save
        # gate, and a Postgres CHECK. This is the fourth, and it exists because
        # a report can also be written against a framework row that predates
        # the constraint.
        findings.append(
            base.high(
                "forbidden_competency",
                location,
                f"{name!r} is not an assessable competency",
                ppi.FORBIDDEN_COMPETENCY_DETAIL,
            )
        )

    grade = rating.grade_for_percent(row.get("score"))
    if grade is None:
        findings.append(
            base.high(
                "ungraded_item",
                location,
                "the item carries no usable score, so it has no grade",
                "score every item so it can be graded",
            )
        )

    findings.extend(
        _remark_findings(row.get("remark"), f"{location}.remark", name=name or "this item")
    )
    return findings


def _remark_findings(
    remark: Any, location: str, *, name: str
) -> list[base.Finding]:
    text = str(remark or "").strip()
    if not text:
        return [
            base.high(
                "missing_remark",
                location,
                f"{name} has no remark",
                f"write a {_REMARK_MIN}-{_REMARK_MAX} word remark for {name}",
            )
        ]

    findings: list[base.Finding] = []
    count = base.words_in(text)
    if not _REMARK_MIN <= count <= _REMARK_MAX:
        findings.append(
            base.medium(
                "remark_word_count",
                location,
                f"the remark is {count} words",
                (
                    f"rewrite it as complete sentences of {_REMARK_MIN}-"
                    f"{_REMARK_MAX} words; never truncate a sentence to fit"
                ),
            )
        )

    if conversation_guardrails.contains_forbidden_number(text):
        findings.append(
            base.high(
                "number_leaked",
                location,
                "the remark states a score, percentage, rank or band",
                "state the grade in words only; remove every number that "
                "describes the assessment itself",
            )
        )

    # High here, medium elsewhere. A PPI remark exists to say something true
    # about one person against one criterion; filler in it is not a blemish on
    # an otherwise useful sentence, it is the sentence failing its only job.
    findings.extend(
        generic_language.findings(
            text, location=location, severity=base.SEVERITY_HIGH
        )
    )
    return findings


def _cap_findings(
    rows: Sequence[dict[str, Any]], overall_grade: str | None
) -> list[base.Finding]:
    """The Must-have cap, checked against the grade the report actually states."""
    if overall_grade is None:
        return []

    capped = any(
        row.get("category") == ppi.CATEGORY_MUST_HAVE
        and rating.grade_for_percent(row.get("score")) == rating.GRADE_NOT
        for row in rows
    )
    if not capped:
        return []
    if overall_grade in rating.MODERATE_OR_BELOW:
        return []
    return [
        base.high(
            "must_have_cap_violated",
            "overall_grade",
            (
                f"a Must-have criterion graded {rating.GRADE_NOT} while Overall "
                f"states {overall_grade}"
            ),
            (
                f"cap Overall at {rating.GRADE_MODERATELY} whenever any "
                f"Must-have criterion grades {rating.GRADE_NOT}"
            ),
        )
    ]
