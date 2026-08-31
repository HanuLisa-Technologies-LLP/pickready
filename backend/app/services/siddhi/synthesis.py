"""Siddhi's report generator: the PRISM Report, assembled statement by statement.

    spec-doc6 §4.5: "Citation enforcement is architectural. The generator
    physically cannot emit a statement without a citation to an evidence node.
    The existing test that tries to emit an uncited statement and confirms it is
    blocked must now run against the live generation path."

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
------------------------------------------------------
It is the piece that turns a completed evaluation into the statements a client
reads, and it is the only piece that may do so: `citations.Section.render` is
the one path to delivered text and it raises on anything uncited, so a generator
that wanted to publish an unsupported claim would have to be a second generator.
There is not one.

It is NOT the scorer. Every grade it states was decided upstream, and it never
recomputes one: a report that could disagree with the evaluation it describes
would make a rubric problem indistinguishable from a rendering problem. It is
also NOT the renderer. Section order, the header and the three-chart rule live
where they always have, in `report_pdf` and in the frontend component, because a
report is immutable and those rules have to hold for a report written a year ago
as well as one written today.

TATVA ASSESSMENT IS THE PROCESS. THE PRISM REPORT IS THE DOCUMENT
-------------------------------------------------------------------
Never used for each other, here or anywhere. The client stated the distinction
twice, which is what a name people will otherwise collapse into one looks like.

WHY THE COMPOSITION IS DETERMINISTIC AND CALLS NO MODEL
---------------------------------------------------------
The prose inside a statement is generated upstream, inside `agent_loop`, with
deterministic success criteria. The ASSEMBLY -- which statement exists, what
kind it is, and which evidence node it cites -- is arithmetic over the rated
rows and the exchange record. That split is what makes the citation guarantee
mean anything: if a model chose the citations, the guarantee would be that a
model claimed a citation, which is the thing §57.6 says an instruction cannot be
trusted to deliver.

THE GAP STATEMENT IS THE ENTRY WORTH DEFENDING
------------------------------------------------
"There is no evidence of X" feels uncitable. It is not: the citation is the
evidence that was SEARCHED (`evidence.KIND_SEARCHED`). Without it, a gap in the
assessment is reported as a gap in the candidate. See `siddhi/evidence.py`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services.siddhi import citations, numbers
from app.services.siddhi.evidence import KIND_SEARCHED, EvidenceIndex, EvidenceNode

logger = logging.getLogger(__name__)

__all__ = [
    "ScorecardUnavailable",
    "GENERIC_ADVICE_PHRASES",
    "SECTION_TITLES",
    "ReadyPickNote",
    "ComposedReport",
    "compose",
    "evidence_refs_for",
    "ready_pick_note",
    "READY_PICK_NOTE_KEY",
    "require_frozen_matrix",
]


class ScorecardUnavailable(RuntimeError):
    """The frozen matrix a report must be written against cannot be reached.

    Raised, never worked around. A PRISM Report states grades against the
    criteria a Hiring Manager finalised; written without them it would state
    grades against criteria nobody approved, which is the failure gate G1 exists
    to prevent and is not something a fallback can make safe.
    """


#: The report's section keys and the heading each one prints. Restated here
#: rather than imported from `ppi`: `ppi` is not a leaf, this package sits on the
#: cycle with it, and reading a `ppi` constant at import time is the pattern
#: `gap_analysis` already had to remove. The ORDER these are rendered in is not
#: here and must not be: it lives once per renderer, in `report_pdf.SECTION_ORDER`
#: and in `components/functional-skills-report.tsx`, and a test reads both out of
#: source and compares them.
SECTION_TITLES: dict[str, str] = {
    "ai_score": "AI Score",
    "overall": "Overall Assessment",
    "must_have": "Must-have",
    "nice_to_have": "Nice-to-have",
    "behavioural": "Behavioural Competencies",
    "gap_analysis": "Gap Analysis & Action Plan",
    "validation": "Validation",
    #: LEGACY. A report written against the standalone technical bank that no
    #: longer exists still carries these rows, and a report is immutable.
    "technical": "Technical",
}

#: A rated row's stored CATEGORY, mapped to the report SECTION it belongs to.
#: They are not the same vocabulary and never were: `matching` is the stored key
#: for the four AI Score parameters, and reusing one word for both would make
#: the trail read as though a section had been renamed.
AI_SCORE_CATEGORY = "matching"
CATEGORY_SECTIONS: dict[str, str] = {
    AI_SCORE_CATEGORY: "ai_score",
    "must_have": "must_have",
    "nice_to_have": "nice_to_have",
    "behavioural": "behavioural",
    "technical": "technical",
}

#: GENERIC ADVICE, WHICH IS WHAT THE GAP SECTION EXISTS TO STOP PRODUCING.
#:
#: spec-doc6 §4.5 asks for a banned-phrase corpus and a test that these never
#: appear in output. The corpus is not a style guide: every phrase here is one
#: that could be written without having read a single word the candidate said,
#: which makes it indistinguishable from advice generated for nobody. Runbook
#: §43.2 states the same rule from the other end ("never use unfalsifiable
#: praise... every positive statement must attach to evidence"); a probe that
#: could have been written before the interview is the negative form of it.
#:
#: Enforced through `agent_loop.banned_phrase_gate`, which is the product's one
#: implementation of this check, so a close variant ("improve their
#: communication skills") is caught without a second matcher to keep in step.
GENERIC_ADVICE_PHRASES: tuple[str, ...] = (
    "improve your communication skills",
    "improve their communication skills",
    "work on your communication",
    "consider taking a course",
    "consider taking a training course",
    "take an online course",
    "work on your confidence",
    "be more confident",
    "needs to be more proactive",
    "should be more proactive",
    "brush up on the fundamentals",
    "read more about the subject",
    "gain more experience",
    "get more experience in this area",
    # Three words minimum, every entry. `agent_loop.banned_phrase_gate` allows a
    # window one word narrower than the phrase, with a three-word floor, so a
    # two-word entry contributes nothing the exact match does not already catch
    # and a one-word entry would reject almost every real probe.
    "should practice more",
    "work on soft skills",
    "improve time management",
    "develop leadership skills",
    "become a better team player",
    "think outside the box",
    "show more initiative",
    "ask more questions",
    "seek a mentor",
    "attend a workshop",
    "study the documentation",
)


@dataclass(frozen=True)
class ReadyPickNote:
    """The dashboard's one-line note, and the evidence it rests on.

    THE DASHBOARD RENDERS `sentence` AND NOTHING ELSE. The refs travel with it
    anyway, and that is the point: the note is a claim about a candidate shown
    in a list, so it is subject to the same rule as a claim in the report, and
    the only way that rule can be checked later is if the provenance came along.
    A note whose citations were dropped at the border between the report and the
    list would be a sentence nobody could trace, sitting on the one surface a
    recruiter triages from.

    Derived from the "why this candidate" material (Runbook §43.1 section 2) and
    computed deterministically, because a dashboard cell that read differently
    on each refresh would be worse than no cell.
    """

    sentence: str
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"sentence": self.sentence, "evidence_refs": list(self.evidence_refs)}


@dataclass
class ComposedReport:
    """What the generator produced, once it survived the chokepoint."""

    #: `citations.Report.render()` output. Every statement in it is cited.
    sections: list[dict[str, Any]] = field(default_factory=list)
    note: ReadyPickNote = ReadyPickNote("")
    index: EvidenceIndex = field(default_factory=EvidenceIndex)

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self.index.refs))

    def trail(self) -> dict[str, Any]:
        """The audit shape persisted alongside the immutable report.

        Statement text is included and evidence EXCERPTS are not. The trail
        exists so a reader can ask "what did this sentence rest on"; answering
        that needs the sentence and the locator, and never the transcript the
        locator points at.
        """
        return {
            "evidence_nodes": [node.as_dict() for node in self.index.nodes],
            "statements": [
                {
                    "section": section["key"],
                    "kind": statement["kind"],
                    "text": statement["text"],
                    "evidence_refs": statement["evidence_refs"],
                }
                for section in self.sections
                for statement in section["statements"]
                if statement["kind"] in citations.REQUIRES_CITATION
            ],
        }


def _grade_of(row: Mapping[str, Any]) -> str:
    """The row's grade WORD, taken from the row and never recomputed here.

    A report states the evaluation's grade. Recomputing it in the generator
    would create a second arithmetic that has to agree with the first, and the
    day they disagreed there would be no way to tell which one the client saw.
    """
    grade = row.get("grade")
    if grade:
        return str(grade)
    from app.services.rating import grade_for_percent

    return str(grade_for_percent(row.get("score")) or "")


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def compose(
    *,
    dimensions: Sequence[Mapping[str, Any]],
    evidence_by_item: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    gap_groups: Sequence[Mapping[str, Any]] = (),
    focus_summary: str = "",
    overall_summary: str | None = None,
    overall_grade: str | None = None,
    validation: Mapping[str, Any] | None = None,
) -> ComposedReport:
    """Assemble the report and RENDER IT. Raises on anything uncited.

    This is the chokepoint on the live path. It is called from
    `gap_analysis.build_gap_analysis`, which is called from the assessment
    graph's synthesis node, so a statement that cannot cite an evidence node
    stops the report from being written rather than being logged and shipped.

    It does not catch `UncitedStatement` or `UnknownEvidence`. There is no
    degraded report to fall back to: a PRISM Report whose claims are not traced
    is not a worse report, it is a different product, and spec-doc6 §4.1 is
    explicit that a stage may not silently degrade. The caller audits and
    surfaces; nothing here writes a generic paragraph.
    """
    rated = [row for row in dimensions if row.get("name")]
    index = EvidenceIndex.build(
        items=[str(row["name"]) for row in rated],
        exchanges={
            str(key): list(value or [])
            for key, value in (evidence_by_item or {}).items()
        },
    )
    # The aspect nodes join the SAME index rather than being added to the
    # accepted set beside it. A ref that is citable but absent from the index is
    # a ref the persisted trail cannot explain, and a provenance record that
    # lists fewer nodes than the statements cite is worse than none: it reads as
    # complete.
    aspect_nodes = _aspect_nodes(gap_groups)
    index = EvidenceIndex(
        nodes=index.nodes
        + tuple(
            EvidenceNode(ref=ref, kind=KIND_SEARCHED, item=f"aspect:{category}")
            for category, ref in aspect_nodes.items()
        )
    )
    report = citations.Report(known_refs=index.refs)

    _compose_rated_sections(report, rated, index)
    _compose_overall(report, rated, index, overall_summary, overall_grade)
    _compose_gap_section(report, index, aspect_nodes, gap_groups, focus_summary)
    _compose_validation(report, validation)

    composed = ComposedReport(
        sections=report.render(),
        note=ready_pick_note(rated, index),
        index=index,
    )
    return composed


def evidence_refs_for(
    dimensions: Sequence[Mapping[str, Any]],
    evidence_by_item: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """{item name: the refs a claim about it may cite}, without composing.

    Exposed for the caller that has to state a claim OUTSIDE a section render:
    Siddhi's quality gate reads `claims` as records carrying `evidence_refs`,
    and a dimension row that reaches it with an empty list is reported as a
    claim citing no evidence. The refs exist; they are computed here, from the
    same index the generator uses, so the gate and the report cannot disagree
    about what a given item's evidence is.
    """
    rated = [row for row in dimensions if row.get("name")]
    index = EvidenceIndex.build(
        items=[str(row["name"]) for row in rated],
        exchanges={
            str(key): list(value or [])
            for key, value in (evidence_by_item or {}).items()
        },
    )
    return {str(row["name"]): index.grounding(str(row["name"])) for row in rated}


def _aspect_nodes(gap_groups: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """One `searched` node per aspect, beside the per-item ones.

    An aspect's "No Must-have gaps identified." is a claim about the candidate
    and needs a citation like any other. Its citation cannot be an item's, since
    the statement is about the aspect as a whole and an aspect can legitimately
    hold no rated items at all. The node's meaning is exact: this aspect's gap
    list was computed over whatever was rated in it, and this is what came back.
    """
    return {
        str(group["category"]): f"{KIND_SEARCHED}:aspect:{group['category']}"
        for group in gap_groups
        if group.get("category")
    }


def _compose_rated_sections(
    report: citations.Report,
    rated: Sequence[Mapping[str, Any]],
    index: EvidenceIndex,
) -> None:
    """Every rated line: its grade, and the remark that explains it.

    Two statements per row rather than one, because they are different kinds of
    assertion and the citation rule treats them the same only by coincidence. A
    grade is a verdict; a remark is a finding. Merging them would make a report
    where a grade could be traced only through the prose beside it.
    """
    by_category: dict[str, list[Mapping[str, Any]]] = {}
    for row in rated:
        by_category.setdefault(str(row.get("category") or "unspecified"), []).append(row)

    for category, rows in by_category.items():
        key = CATEGORY_SECTIONS.get(category, category)
        section = report.section(key, SECTION_TITLES.get(key, key))
        for row in rows:
            name = str(row["name"])
            refs = index.grounding(name)
            grade = _grade_of(row)
            if grade:
                section.add(
                    citations.Statement(
                        citations.KIND_GRADE, f"{name}: {grade}", refs
                    )
                )
            remark = _clean(row.get("remark"))
            if remark:
                section.add(
                    citations.Statement(citations.KIND_FINDING, remark, refs)
                )


def _compose_overall(
    report: citations.Report,
    rated: Sequence[Mapping[str, Any]],
    index: EvidenceIndex,
    overall_summary: str | None,
    overall_grade: str | None,
) -> None:
    """The Overall Assessment, when the caller supplied it.

    Optional because the assessment graph computes it beside the gap section
    rather than inside it. When it is supplied it is composed here and is
    subject to the same rule as everything else; when it is not, nothing is
    invented to stand in for it.
    """
    if not (overall_summary or overall_grade):
        return
    assessed = [
        str(row["name"])
        for row in rated
        if row.get("category") != AI_SCORE_CATEGORY
    ]
    refs = tuple(
        dict.fromkeys(ref for name in assessed for ref in index.grounding(name))
    )
    if not refs:
        refs = tuple(
            dict.fromkeys(ref for name in assessed for ref in index.searched(name))
        )
    section = report.section("overall", SECTION_TITLES["overall"])
    if overall_grade:
        section.add(
            citations.Statement(
                citations.KIND_GRADE, f"Overall: {overall_grade}", refs
            )
        )
    summary = _clean(overall_summary)
    if summary:
        section.add(citations.Statement(citations.KIND_FINDING, summary, refs))


def _compose_gap_section(
    report: citations.Report,
    index: EvidenceIndex,
    aspect_nodes: Mapping[str, str],
    gap_groups: Sequence[Mapping[str, Any]],
    focus_summary: str,
) -> None:
    """Gap Analysis & Action Plan: gaps and probes, every one of them cited."""
    section = report.section("gap_analysis", SECTION_TITLES["gap_analysis"])
    section.add(
        citations.Statement(citations.KIND_HEADING, SECTION_TITLES["gap_analysis"])
    )

    summary = _clean(focus_summary)
    if summary:
        # A CLAIM, not connective prose. "Focus the interview on X" asserts that
        # X is where this candidate is weakest, and "no gaps were identified"
        # asserts the opposite about all of them. Both are findings about a
        # person and both are cited to what was actually searched.
        section.add(
            citations.Statement(
                citations.KIND_GAP, summary, tuple(sorted(set(aspect_nodes.values())))
            )
        )

    for group in gap_groups:
        category = str(group.get("category") or "")
        aspect_ref = aspect_nodes.get(category)
        aspect_refs = (aspect_ref,) if aspect_ref else ()
        label = _clean(group.get("label"))
        if label:
            section.add(citations.Statement(citations.KIND_HEADING, label))

        cap = _clean(group.get("cap_statement"))
        if cap:
            capped = [
                str(item.get("name"))
                for item in group.get("items") or []
                if item.get("name")
            ]
            section.add(
                citations.Statement(
                    citations.KIND_GAP,
                    cap,
                    tuple(
                        dict.fromkeys(
                            ref for name in capped for ref in index.grounding(name)
                        )
                    )
                    or aspect_refs,
                )
            )

        no_gaps = _clean(group.get("no_gaps_statement"))
        if no_gaps:
            section.add(citations.Statement(citations.KIND_GAP, no_gaps, aspect_refs))

        for item in group.get("items") or []:
            name = str(item.get("name") or "")
            if not name:
                continue
            item_refs = index.grounding(name) or aspect_refs
            grade = _clean(item.get("grade"))
            if grade:
                section.add(
                    citations.Statement(
                        citations.KIND_GRADE, f"{name}: {grade}", item_refs
                    )
                )
            remark = _clean(item.get("remark"))
            if remark:
                # The item's own remark, REUSED. It is stated once in the
                # report and quoted here, so the gap section can never carry a
                # second, differently worded assessment of the same item.
                section.add(
                    citations.Statement(citations.KIND_GAP, remark, item_refs)
                )
            for probe in item.get("probes") or []:
                text = _clean(probe)
                if text:
                    section.add(
                        citations.Statement(citations.KIND_PROBE, text, item_refs)
                    )


def _compose_validation(
    report: citations.Report, validation: Mapping[str, Any] | None
) -> None:
    """The candidate's own unrated submission, verbatim.

    KIND_VERBATIM, and therefore exempt from the citation requirement, for the
    same reason it is exempt from the number ban: it is not a claim about the
    candidate derived from evidence, it is the candidate's own words carried
    across untouched. Requiring a citation would produce a fake one.
    """
    if not validation:
        return
    section = report.section("validation", SECTION_TITLES["validation"])
    for row in validation.get("fields") or []:
        label = _clean(row.get("label") if isinstance(row, Mapping) else "")
        value = _clean(row.get("value") if isinstance(row, Mapping) else "")
        if label:
            section.add(
                citations.Statement(citations.KIND_VERBATIM, f"{label}: {value}")
            )


# ── The dashboard's Ready Pick Note ──────────────────────────────────────────

#: THE KEY THE DASHBOARD READS THE NOTE FROM.
#:
#: `services/dashboard.READY_PICK_NOTE_KEY` states the other half of this
#: contract: column 5 reads `evaluations.aggregate_json ->> this key`, and never
#: `functional_skills_reports`, because sourcing a dashboard cell from the
#: delivered document would make the row's pending state a statement about the
#: report rather than about the profile.
#:
#: Restated here rather than imported, because `services/dashboard` is not a
#: leaf and Siddhi must stay importable from the report side. The two names are
#: pinned to each other by a test that reads both, which is the same technique
#: `report_pdf.SECTION_ORDER` and the frontend's `REPORT_SECTION_ORDER` use.
READY_PICK_NOTE_KEY = "why_this_candidate"

_SENTENCE = re.compile(r"[^.!?]+[.!?]")


def ready_pick_note(
    dimensions: Sequence[Mapping[str, Any]],
    index: EvidenceIndex,
    *,
    priority: Mapping[str, float] | None = None,
) -> ReadyPickNote:
    """One plain-language line for the candidate list, with its citations.

    Derived from the "why this candidate" material rather than written afresh:
    Runbook §43.1 asks the fit rationale to name the top competency and the
    evidence that satisfies it, and the dashboard cell is that rationale reduced
    to the one sentence a list can hold. Deriving it means the note and the
    report cannot say different things about the same candidate.

    `priority` IS {item name: weight}, AND ITS ABSENCE IS STATED RATHER THAN
    PAPERED OVER. §43.1 asks for the top-WEIGHTED competency. A delivered report
    row carries no weight: `report_dimensions` holds a name, a grade, a remark
    and the copied requirement, and the Tatva weights never cross an API
    boundary. So a caller that holds the frozen matrix passes them and gets the
    Runbook's ordering; a caller that does not gets the best-evidenced item,
    which is a different and weaker claim about the same candidate and is the
    honest one to make from what the report actually holds. Nothing is invented
    to stand in for a weight.

    Deterministic, and it calls no model. A triage line that changed wording
    between two loads of the same list would make a recruiter distrust the list.
    No number reaches it, and it carries no em dash.
    """
    assessed = [
        row
        for row in dimensions
        if row.get("name") and row.get("category") != AI_SCORE_CATEGORY
    ]
    if not assessed:
        return ReadyPickNote(
            "This candidate has no assessed criteria on record yet, so there is "
            "nothing to summarise until the assessment completes."
        )

    weights = priority or {}

    def _rank(row: Mapping[str, Any]) -> tuple[float, float, int]:
        name = str(row.get("name") or "")
        return (
            -float(weights.get(name, 0.0)),
            -float(row.get("score") or 0),
            int(row.get("ordinal") or 0),
        )

    strongest = sorted(assessed, key=_rank)[0]
    name = str(strongest["name"])
    grade = _grade_of(strongest)
    refs = index.grounding(name)
    remark = _clean(strongest.get("remark"))
    # Searched ONCE. Calling `search` twice reads as a null check and is not
    # one: the second call is a second search, and mypy is right that the
    # first result is what has to be narrowed.
    match = _SENTENCE.search(remark)
    first = match.group(0).strip() if match else remark
    lead = first[:160].rstrip(" ,.;:") if first else ""

    if lead:
        sentence = f"Strongest on {name}, graded {grade}: {lead}."
    else:
        sentence = f"Strongest on {name}, graded {grade}, on the evidence recorded in the assessment."
    return ReadyPickNote(sentence=_no_dash(sentence), evidence_refs=refs)


def _no_dash(value: str) -> str:
    """No em dash in any string the product writes, including this one."""
    dash = chr(8212)
    return value.replace(f" {dash} ", ", ").replace(dash, ", ")


# ── The frozen matrix a report is written against ────────────────────────────


async def require_frozen_matrix(session: Any, job_id: Any) -> Any:
    """The job's approved, frozen scorecard, or a refusal naming what is absent.

    LAZY IMPORT, DELIBERATELY. `app.services.hiring.scorecard` is being built by
    the job-setup phase and may not exist in a given checkout; importing it at
    module scope would make Siddhi unimportable rather than make the dependency
    visible. There is no fallback: a report written without the frozen matrix
    would state grades against criteria nobody finalised, which is exactly what
    gate G1 exists to refuse, and "restrict more when unsure" points one way
    here.
    """
    try:
        from app.services.hiring import scorecard
    except ImportError as exc:
        raise ScorecardUnavailable(
            "app.services.hiring.scorecard is not present in this build, so the "
            "frozen matrix a PRISM Report must be written against cannot be "
            "reached. A report is not generated without it."
        ) from exc
    require = getattr(scorecard, "require_frozen_matrix", None)
    if require is None:
        raise ScorecardUnavailable(
            "app.services.hiring.scorecard exists but exposes no "
            "require_frozen_matrix(session, job_id); the frozen matrix cannot "
            "be reached and no report is generated without it."
        )
    return await require(session, job_id)


def assert_deliverable(payload: Any, *, where: str) -> None:
    """The number ban, re-exported at the generator's own boundary.

    A convenience with a purpose: a caller reaching for the generator should not
    have to know which module the ban lives in, and one import site is one fewer
    place a future export format can forget.
    """
    numbers.assert_clean(payload, where=where)
