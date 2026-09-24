"""Siddhi's quality gate: the composed PRISM Report against the grades Miti decided.

WHAT WAS WRONG, AND WHY IT READ AS WORKING (audit P2-4, PLAN-p5 row `_gate_report`)
-----------------------------------------------------------------------------------
The gate adapter in `functional_assessment._gate_report` handed the Siddhi gate

    "grades": graded, "miti_grades": graded

the SAME dict twice, under two names. The comparison "the report states the
grade scoring recorded" therefore compared a value with itself, and could not
fire for any report ever written. A comment beside it said so ("this check only
has teeth once the two stages are genuinely separate"), which is a defect
described in prose and left in place.

WHAT THE GATE READS NOW
-------------------------
Two independent sources, which is the only way a comparison can have teeth:

  * `grades`: the grade WORDS parsed out of the statements Siddhi actually
    RENDERED (`KIND_GRADE`, "Name: Grade"), from the rated sections. What the
    document says, read from the document.
  * `miti_grades`: the grades Miti decided, passed in by the caller from Miti's
    own result (`miti_grades_from(skills)` reads any sequence of skill grades
    carrying `name` and `grade`), never from the rows Siddhi rendered from.

And three checks the old shape could not express: a Miti grade the document
never states (`grade_missing_from_report`), a stated grade Miti never decided
(`grade_without_scoring`), and the Overall grade. The document restating one
skill's grade differently in two sections is caught here too
(`grade_restated_differently`), because each restatement is compared.

A GATE CRASH FLAGS REVIEW, IT NEVER PASSES
--------------------------------------------
The old adapter returned a PASSING verdict on its own error, with a low
finding, so a gate that crashed on every report was indistinguishable from a
gate that approved every report. A gate that cannot run has not checked
anything, so its verdict FAILS with a high `gate_unavailable` finding and the
report goes to a person. It still never blocks the report being written: the
gate marks, it does not withhold.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence

from app.services import verification as verification_base
from app.services.rating import GRADES
from app.services.siddhi import citations, synthesis
from app.services.siddhi.evidence import KIND_ANSWER, EvidenceIndex
from app.services.siddhi.report import ComposedPrism

logger = logging.getLogger(__name__)

__all__ = [
    "STATED_GRADE_WORDS",
    "stated_grades",
    "miti_grades_from",
    "evaluate",
]

#: Every word a grade statement may state: the four grades and Not assessed.
STATED_GRADE_WORDS: frozenset[str] = frozenset(GRADES) | {synthesis.NOT_ASSESSED_WORD}


def stated_grades(
    composed: ComposedPrism, *, sections: Iterable[str] = synthesis.RATED_SECTIONS
) -> list[tuple[str, str, str]]:
    """(section, name, grade word) for every grade the document states.

    Read from the RENDERED statements. A grade statement is "Name: Grade"; a
    line whose tail is not a grade word ("Name: evidence confidence High") is
    a different statement and is skipped. A name may itself contain ": ", so
    the split is on the LAST one.
    """
    wanted = set(sections)
    found: list[tuple[str, str, str]] = []
    for key, statement in composed.statements():
        if key not in wanted or statement["kind"] != citations.KIND_GRADE:
            continue
        name, sep, grade = str(statement["text"]).rpartition(": ")
        if sep and grade in STATED_GRADE_WORDS:
            found.append((key, name, grade))
    return found


def miti_grades_from(skills: Iterable[Any]) -> dict[str, str]:
    """{name: grade word} from Miti's skill grades.

    A skill Miti could not assess carries no grade and is stated as Not
    assessed, so it maps to that word: the document must say exactly that.
    """
    grades: dict[str, str] = {}
    for skill in skills:
        name = str(getattr(skill, "name", "") or "")
        if not name:
            continue
        grade = getattr(skill, "grade", None)
        grades[name] = str(grade) if grade else synthesis.NOT_ASSESSED_WORD
    return grades


def _rendered(row: Mapping[str, Any]) -> dict[str, Any]:
    """Client-visible fields only. A locator is an audit handle, not copy."""
    return {
        "name": row.get("name"),
        "description": row.get("description"),
        "grade": row.get("grade"),
        "remark": row.get("remark"),
    }


def _consistency_findings(
    stated: Sequence[tuple[str, str, str]],
    overall_stated: Sequence[tuple[str, str, str]],
) -> list[verification_base.Finding]:
    """The document may not state one skill's grade two different ways."""
    findings: list[verification_base.Finding] = []
    seen: dict[str, str] = {}
    for section, name, grade in [*stated, *overall_stated]:
        first = seen.setdefault(name, grade)
        if first != grade:
            findings.append(
                verification_base.high(
                    "grade_restated_differently",
                    f"report.{section}.{name}",
                    "the report states this grade differently in two places",
                    "State the one grade scoring recorded, everywhere it appears.",
                )
            )
    return findings


def evaluate(
    *,
    composed: ComposedPrism,
    dimensions: Sequence[Mapping[str, Any]],
    gap_groups: Sequence[Mapping[str, Any]],
    overall_summary: str | None,
    validation: Mapping[str, Any],
    validation_source: Mapping[str, Any],
    evidence_by_item: Mapping[str, Sequence[Any]],
    miti_grades: Mapping[str, str],
    miti_overall_grade: str | None,
) -> verification_base.Verdict:
    """The Siddhi gate's verdict on this report. Never raises.

    A crash inside it returns a FAILING verdict (`gate_unavailable`, high), so
    the report is written flagged for review rather than passed unchecked.
    """
    from app.services.agents import gates

    try:
        stated = stated_grades(composed)
        overall_stated = [
            entry
            for entry in stated_grades(composed, sections=("overall",))
            if entry[1] == synthesis.OVERALL_LABEL
        ]
        gap_stated = stated_grades(composed, sections=("gap_analysis",))
        index: EvidenceIndex = composed.index
        payload = {
            "ai_score": [
                _rendered(row)
                for row in dimensions
                if row.get("category") == synthesis.AI_SCORE_CATEGORY
            ],
            "ppi_assessment": [
                _rendered(row)
                for row in dimensions
                if row.get("category") != synthesis.AI_SCORE_CATEGORY
            ],
            "validation": dict(validation),
            "validation_source": dict(validation_source),
            "gap_analysis": [
                {
                    "id": f"{group.get('category')}.{entry.get('name')}.{position}",
                    "text": probe,
                    "grounded_in_answer": bool(
                        evidence_by_item.get(str(entry.get("name")))
                    ),
                }
                for group in gap_groups
                for entry in (group.get("items") or [])
                for position, probe in enumerate(entry.get("probes") or [])
            ],
            "overall_summary": overall_summary or "",
            # WHAT THE DOCUMENT SAYS, parsed from the rendered statements.
            "grades": {name: grade for _, name, grade in stated},
            # WHAT MITI DECIDED, from the caller's copy of Miti's result.
            "miti_grades": dict(miti_grades),
            "overall_grade": overall_stated[0][2] if overall_stated else None,
            "miti_overall_grade": miti_overall_grade,
            "claims": [
                {
                    "id": row.get("name"),
                    "text": row.get("remark") or "",
                    "evidence_refs": list(
                        index.refs_for(str(row.get("name")), kind=KIND_ANSWER)
                    ),
                }
                for row in dimensions
                if row.get("name")
            ],
        }
        verdict = gates.run_gate("siddhi", payload)
        extra = _consistency_findings(stated + gap_stated, overall_stated)
        if not extra:
            return verdict
        return verification_base.verdict(
            verdict.verifier, [*verdict.findings, *extra]
        )
    # NAMED CLASSES, NOT `Exception`, and a programming error among them on
    # purpose. This is the one place a malformed payload must not cost a
    # candidate their report (the report is still written), and it is not
    # absorbed quietly either: the verdict FAILS, the report is flagged, and
    # the traceback is logged at ERROR. That is the opposite of the silent
    # `return False` the 2026-09-22 cost-record rule forbids.
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.error(
            "siddhi.quality_gate_unavailable error=%s", type(exc).__name__,
            exc_info=True,
        )
        return verification_base.verdict(
            "gate:siddhi",
            [
                verification_base.high(
                    "gate_unavailable",
                    "report",
                    "the report quality gate could not run",
                    "A person should read this report before it is relied on; "
                    "the quality gate did not check it.",
                )
            ],
        )
