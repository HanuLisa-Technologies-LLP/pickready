"""One quality gate per named agent (spec 31), and every one is arithmetic.

YUKTI HAS NO GATE HERE, AND THAT IS DECLARED RATHER THAN IMPLIED
----------------------------------------------------------------
The Yukti gate judged the retired AI Score payload: at least five matching
categories, each citing a resume line. Its only caller was the unread
`ai_score` artifact hand-off, and both are DELETED (Vivekium release, Phase 2
WP-F). Yukti's reading is now checked where it is produced, deterministically:
`yukti/grounding.py` drops any quote the resume does not contain and any tag
naming a protected trait BEFORE a row is written, which is the boundary this
module's own argument asks for. `UNGATED` names the agent and the reason, so
the gate table still accounts for every agent.

WHY A GATE PER AGENT RATHER THAN ONE AT THE END
------------------------------------------------
Because a defect caught at the end of the pipeline is a defect that has already
been built on. A matrix missing a critical JD requirement does not fail at
Sutra; it fails at Siddhi, months later, as "the report never mentions Kubernetes"
-- by which point a candidate has been interviewed against criteria that omitted
the thing the job is for, and the conversation cannot be re-run. Each gate
therefore refuses at the boundary where the artifact is published, which is also
the only place the information needed to judge it still exists.

NOTHING HERE CALLS A MODEL, AND THAT IS THE ENTIRE POINT
----------------------------------------------------------
Same argument `verification/base.py` makes: the moment a guard matters most is
the moment the provider is down. A gate that needs a provider fails open exactly
when it is needed, and an LLM judging its own pipeline's output makes the
criteria unfalsifiable as well as adding a second flaky dependency. Every check
below is a set comparison, a count or a string comparison, so it is testable
offline and a reviewer can reconstruct any verdict by hand.

CONFIDENCE IS NEVER ASKED FOR
-----------------------------
Each gate returns a `verification.base.Verdict`, whose confidence is severity
arithmetic. One high finding is disqualifying; two mediums are; one is not.
Those thresholds live in `base.py` and are deliberately not restated here.

THE HARD CAP IS CHECKED, NOT REQUESTED
---------------------------------------
Any Must-have graded Not Matching caps Overall at Moderately Matching, with no
override. `rating.cap_to_moderately` is the arithmetic half; `miti_gate` is the
half that catches a scoring state where it was not applied. Asking a model to
confirm its own cap was applied is asking the thing that got it wrong.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.services import conversation_guardrails, rating
from app.services.verification import base as verification

__all__ = [
    "bodha_gate",
    "sutra_gate",
    "vaada_gate",
    "miti_gate",
    "siddhi_gate",
    "GATES",
    "UNGATED",
    "run_gate",
]

# ── Bodha ────────────────────────────────────────────────────────────────────
#: The role context a SWOT intake has to have covered before a matrix can be
#: built from it. A quadrant full of adjectives about the team tells Sutra
#: nothing about what the person will be measured on.
REQUIRED_ROLE_CONTEXT: tuple[str, ...] = (
    "role_objectives",
    "success_criteria",
    "team_context",
    "known_challenges",
)
_SWOT_QUADRANTS: tuple[str, ...] = ("strengths", "weaknesses", "opportunities", "threats")

# ── Sutra ────────────────────────────────────────────────────────────────────
#: At least five per category, per the standing PPI rule. Fewer is not a thin
#: matrix, it is one that cannot distinguish two candidates.
MIN_MATRIX_ITEMS = 5
MATRIX_CATEGORIES: tuple[str, ...] = ("must_have", "nice_to_have", "behavioural")

# ── Miti ─────────────────────────────────────────────────────────────────────
#: Answers behind a grade before it counts as evidenced. Two, because one answer
#: is an anecdote and the grade is stated as a finding about a person.
MIN_EVIDENCE_ANSWERS = 2

# ── Siddhi ───────────────────────────────────────────────────────────────────
#: Report order is fixed and every section is required, including an empty one:
#: a Gap Analysis that is absent and a Gap Analysis that is empty read the same
#: to a client and mean opposite things.
REQUIRED_REPORT_SECTIONS: tuple[str, ...] = (
    "ai_score",
    "ppi_assessment",
    "validation",
    "gap_analysis",
)


def _items(source: Mapping[str, Any], key: str) -> list[Any]:
    value = source.get(key)
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _names(items: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            out.append(str(item.get("name", "")).strip())
        else:
            out.append(str(item).strip())
    return out


def bodha_gate(swot: Mapping[str, Any]) -> verification.Verdict:
    """Bodha, the SWOT intake.

    Expects: the four quadrants as lists, `context_covered`, `sources`, and
    `contradictions` as records carrying `critical` and `resolved`.

    A missing source is HIGH rather than MEDIUM because a SWOT with no
    provenance is indistinguishable from one a model invented, and the matrix
    built from it inherits the invention silently.
    """
    findings: list[verification.Finding] = []

    for quadrant in _SWOT_QUADRANTS:
        if quadrant not in swot:
            findings.append(
                verification.high(
                    "missing_swot_quadrant",
                    f"swot.{quadrant}",
                    "the SWOT schema requires all four quadrants",
                    f"Return a {quadrant} list, empty only if the manager stated none.",
                )
            )
        elif not isinstance(swot.get(quadrant), (list, tuple)):
            findings.append(
                verification.high(
                    "invalid_swot_schema",
                    f"swot.{quadrant}",
                    "the quadrant is not a list",
                    f"Return {quadrant} as a list of separate points.",
                )
            )

    covered = {str(item).strip() for item in _items(swot, "context_covered")}
    for required in REQUIRED_ROLE_CONTEXT:
        if required not in covered:
            findings.append(
                verification.medium(
                    "role_context_not_covered",
                    f"swot.context_covered.{required}",
                    "the intake did not cover this part of the role context",
                    f"Ask the Reporting Authority about {required.replace('_', ' ')}.",
                )
            )

    if not _items(swot, "sources"):
        findings.append(
            verification.high(
                "no_sources_recorded",
                "swot.sources",
                "the SWOT records no source for anything it states",
                "Record who said each point and when it was captured.",
            )
        )

    for contradiction in _items(swot, "contradictions"):
        if not isinstance(contradiction, Mapping):
            continue
        if contradiction.get("critical") and not contradiction.get("resolved"):
            findings.append(
                verification.high(
                    "unresolved_critical_contradiction",
                    f"swot.contradictions.{contradiction.get('id', 'unknown')}",
                    "a critical contradiction in the role context is still open",
                    "Put the conflicting statements to the Reporting Authority and "
                    "record which one holds.",
                )
            )

    return verification.verdict("gate:bodha", findings)


def sutra_gate(matrix: Mapping[str, Any]) -> verification.Verdict:
    """Sutra, the Tatva Matrix.

    Expects: `must_have`, `nice_to_have`, `behavioural` as lists of records with
    `name` and `rubric`, plus `critical_requirements` and `covered_requirements`
    naming what the JD demanded and what the matrix represents.

    An uncovered critical requirement is HIGH because the matrix is frozen once
    anyone is assessed against it, so the omission is permanent for that job.
    """
    findings: list[verification.Finding] = []
    seen: dict[str, str] = {}

    for category in MATRIX_CATEGORIES:
        items = _items(matrix, category)
        if len(items) < MIN_MATRIX_ITEMS:
            findings.append(
                verification.high(
                    "insufficient_coverage",
                    f"matrix.{category}",
                    f"{len(items)} items, below the required minimum",
                    f"Return at least {MIN_MATRIX_ITEMS} distinct {category} criteria "
                    "drawn from the job description.",
                )
            )
        for name in _names(items):
            if not name:
                findings.append(
                    verification.high(
                        "unnamed_criterion",
                        f"matrix.{category}",
                        "a criterion has no name",
                        "Name every criterion; an unnamed one cannot be scored or reported.",
                    )
                )
                continue
            key = name.casefold()
            # "Culture" is refused at three layers already (prompt, save,
            # database CHECK). This is the fourth and cheapest: a Hiring
            # Manager's Edit control can type anything, and cultural fit cannot
            # be assessed from a single conversation.
            if category == "behavioural" and key == "culture":
                findings.append(
                    verification.high(
                        "culture_as_competency",
                        "matrix.behavioural",
                        "culture is not assessable from one conversation",
                        "Replace it with a named observable behaviour.",
                    )
                )
            if key in seen:
                findings.append(
                    verification.high(
                        "duplicate_criterion",
                        f"matrix.{category}.{name}",
                        f"already present in {seen[key]}",
                        "Remove the duplicate; one criterion belongs to exactly one category.",
                    )
                )
            else:
                seen[key] = category

        for item in items:
            if isinstance(item, Mapping) and not str(item.get("rubric", "")).strip():
                findings.append(
                    verification.medium(
                        "unusable_rubric",
                        f"matrix.{category}.{item.get('name', 'unnamed')}",
                        "the criterion carries no rubric, so no answer can be graded against it",
                        "Write the rubric bands for this criterion alongside it.",
                    )
                )

    critical = {str(x).strip().casefold() for x in _items(matrix, "critical_requirements")}
    covered = {str(x).strip().casefold() for x in _items(matrix, "covered_requirements")}
    for requirement in sorted(critical - covered):
        findings.append(
            verification.high(
                "critical_requirement_unrepresented",
                "matrix.covered_requirements",
                f"the job description demands {requirement!r} and no criterion covers it",
                f"Add a criterion covering {requirement}.",
            )
        )

    return verification.verdict("gate:sutra", findings)


def vaada_gate(conversation: Mapping[str, Any]) -> verification.Verdict:
    """Vaada, the candidate conversation.

    Expects: `required_competencies`, `covered_competencies`, `completed`,
    `follow_ups_used`, `follow_up_budget`, `redundant_questions`.

    The stopping rules are not restated here. `interviewer.follow_up_budget`
    owns them and this gate reads the budget it was given, because a second copy
    of the ceiling would let a change to one silently disagree with the other.
    """
    findings: list[verification.Finding] = []

    required = {str(x).strip() for x in _items(conversation, "required_competencies")}
    covered = {str(x).strip() for x in _items(conversation, "covered_competencies")}
    for missing in sorted(required - covered):
        findings.append(
            verification.high(
                "evidence_coverage_insufficient",
                f"conversation.covered_competencies.{missing}",
                "the conversation gathered no evidence for a required competency",
                f"Ask at least one question that probes {missing} before closing.",
            )
        )

    if not conversation.get("completed"):
        findings.append(
            verification.high(
                "conversation_not_complete",
                "conversation.completed",
                "the conversation did not reach its stopping condition",
                "Hold the assessment open until the scripted questions and any "
                "outstanding follow-up are answered.",
            )
        )

    used = int(conversation.get("follow_ups_used", 0) or 0)
    budget = int(conversation.get("follow_up_budget", 0) or 0)
    if budget and used > budget:
        findings.append(
            verification.high(
                "excessive_questioning",
                "conversation.follow_ups_used",
                "more follow-ups were asked than the conversation's budget allows",
                "Stop at the follow-up budget; an over-long interview is an "
                "abandoned one.",
            )
        )

    redundant = int(conversation.get("redundant_questions", 0) or 0)
    if redundant:
        findings.append(
            verification.medium(
                "redundant_questioning",
                "conversation.redundant_questions",
                "the same ground was covered more than once",
                "Read the transcript before writing the next question and probe "
                "something not yet answered.",
            )
        )

    return verification.verdict("gate:vaada", findings)


def _grade_rank(grade: str) -> int:
    """Position in `rating.GRADES`, best first. Unknown sorts worst.

    INTERNAL ordering only. It never leaves the server and is never rendered.
    """
    try:
        return rating.GRADES.index(str(grade))
    except ValueError:
        return len(rating.GRADES)


def miti_gate(scoring: Mapping[str, Any]) -> verification.Verdict:
    """Miti, the scoring state.

    Expects: `required_events` and `scored_events` as question keys,
    `item_grades` as records with `name`, `category`, `grade` and
    `evidence_count`, `overall_grade`, and `contradictions` with `resolved`.

    THE HARD CAP IS THE REASON THIS GATE EXISTS. It is checked arithmetically:
    if any Must-have grades Not Matching, Overall may not rank above Moderately
    Matching, and there is no override. Asking a model whether it applied its
    own cap is asking the component that got it wrong.
    """
    findings: list[verification.Finding] = []

    required = {str(x) for x in _items(scoring, "required_events")}
    scored = {str(x) for x in _items(scoring, "scored_events")}
    for missing in sorted(required - scored):
        findings.append(
            verification.high(
                "scoring_event_missing",
                f"scoring.scored_events.{missing}",
                "an answer that must be scored was never scored",
                f"Score the answer filed under {missing} before completing the assessment.",
            )
        )

    must_have_failed: list[str] = []
    for item in _items(scoring, "item_grades"):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "unnamed"))
        category = str(item.get("category", "")).strip().casefold()
        grade = str(item.get("grade", "")).strip()
        evidence = int(item.get("evidence_count", 0) or 0)

        if category == "must_have" and grade == rating.GRADE_NOT:
            must_have_failed.append(name)

        important = category == "must_have" or grade in (
            rating.GRADE_NOT,
            rating.GRADE_HIGHLY,
        )
        if important and evidence < MIN_EVIDENCE_ANSWERS:
            findings.append(
                verification.medium(
                    "insufficient_evidence_for_grade",
                    f"scoring.item_grades.{name}",
                    "a consequential grade rests on fewer answers than the minimum",
                    f"Gather at least {MIN_EVIDENCE_ANSWERS} answers for {name} or "
                    "record the grade as unevidenced.",
                )
            )

    overall = str(scoring.get("overall_grade", "")).strip()
    if must_have_failed and overall:
        if _grade_rank(overall) < _grade_rank(rating.GRADE_MODERATELY):
            findings.append(
                verification.high(
                    "hard_cap_not_applied",
                    "scoring.overall_grade",
                    f"{must_have_failed} graded {rating.GRADE_NOT} while Overall reads "
                    f"{overall}",
                    "Cap the Overall grade at Moderately Matching; a failed Must-have "
                    "admits no override.",
                )
            )

    for contradiction in _items(scoring, "contradictions"):
        if isinstance(contradiction, Mapping) and not contradiction.get("resolved"):
            findings.append(
                verification.high(
                    "contradiction_unhandled",
                    f"scoring.contradictions.{contradiction.get('id', 'unknown')}",
                    "two answers conflict and the scoring state records no resolution",
                    "Probe the conflict or record which answer the grade rests on.",
                )
            )

    return verification.verdict("gate:miti", findings)


def _client_visible_strings(report: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every string a client would read, with where it came from."""
    out: list[tuple[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            out.append((path, value))
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    for section in REQUIRED_REPORT_SECTIONS:
        if section in report:
            walk(report[section], f"report.{section}")
    return out


def siddhi_gate(report: Mapping[str, Any]) -> verification.Verdict:
    """Siddhi, the PRISM report.

    Expects: the four sections, `claims` as records with `text` and
    `evidence_refs`, `grades` and `miti_grades` as name -> grade mappings,
    `gap_analysis` probes carrying `grounded_in_answer`, and
    `validation_source` beside the rendered `validation`.

    Validation is compared field by field rather than trusted. It is factual
    application data the candidate supplied and nothing scores it, so a report
    that reworded a notice period has fabricated a fact in a document a client
    makes a decision from.
    """
    findings: list[verification.Finding] = []

    for section in REQUIRED_REPORT_SECTIONS:
        if section not in report:
            findings.append(
                verification.high(
                    "missing_report_section",
                    f"report.{section}",
                    "a required section is absent",
                    f"Render the {section.replace('_', ' ')} section, stating explicitly "
                    "when it is empty.",
                )
            )

    for claim in _items(report, "claims"):
        if not isinstance(claim, Mapping):
            continue
        if not _items(claim, "evidence_refs"):
            findings.append(
                verification.medium(
                    "claim_not_grounded",
                    f"report.claims.{claim.get('id', 'unnamed')}",
                    "a stated claim cites no evidence",
                    "Ground the claim in a specific answer or resume line, or remove it.",
                )
            )

    # THE TWO MAPPINGS MUST COME FROM TWO PLACES. `grades` is what the
    # document states, parsed from its rendered statements; `miti_grades` is
    # what the grading authority decided. Until the Vivekium release the only
    # caller passed one dict under both names, so this comparison could never
    # fire (`siddhi.quality_gate` is the caller now). Three shapes of
    # disagreement, each a separate finding: a different grade, a grade Miti
    # decided that the document never states, and a stated grade Miti never
    # decided.
    grades = report.get("grades")
    miti_grades = report.get("miti_grades")
    if isinstance(grades, Mapping) and isinstance(miti_grades, Mapping):
        for name, grade in grades.items():
            if name not in miti_grades:
                findings.append(
                    verification.high(
                        "grade_without_scoring",
                        f"report.grades.{name}",
                        "the report states a grade the scoring agent never recorded",
                        "State only the grades the scoring agent recorded.",
                    )
                )
                continue
            expected = miti_grades.get(name)
            if str(expected) != str(grade):
                findings.append(
                    verification.high(
                        "grade_disagrees_with_scoring",
                        f"report.grades.{name}",
                        f"the report states {grade!r} where scoring recorded {expected!r}",
                        "State the grade the scoring agent recorded; the report does "
                        "not regrade.",
                    )
                )
        for name in miti_grades:
            if name not in grades:
                findings.append(
                    verification.high(
                        "grade_missing_from_report",
                        f"report.grades.{name}",
                        "a grade the scoring agent recorded is absent from the report",
                        "State every graded skill; a missing grade reads as a skill "
                        "that was never assessed.",
                    )
                )
    if "miti_overall_grade" in report:
        stated_overall = report.get("overall_grade")
        expected_overall = report.get("miti_overall_grade")
        if str(stated_overall) != str(expected_overall):
            findings.append(
                verification.high(
                    "overall_grade_disagrees_with_scoring",
                    "report.overall_grade",
                    f"the report states {stated_overall!r} as the overall grade "
                    f"where scoring recorded {expected_overall!r}",
                    "State the overall grade the scoring agent recorded.",
                )
            )

    for probe in _items(report, "gap_analysis"):
        if isinstance(probe, Mapping) and not probe.get("grounded_in_answer"):
            findings.append(
                verification.medium(
                    "generic_gap_probe",
                    f"report.gap_analysis.{probe.get('id', 'unnamed')}",
                    "a gap probe is not anchored in anything this candidate said",
                    "Rewrite the probe around a sentence the candidate actually wrote.",
                )
            )

    source = report.get("validation_source")
    rendered = report.get("validation")
    if isinstance(source, Mapping) and isinstance(rendered, Mapping):
        for key, value in source.items():
            if key not in rendered:
                findings.append(
                    verification.high(
                        "validation_field_dropped",
                        f"report.validation.{key}",
                        "a validation field the candidate answered is missing",
                        f"Reproduce {key} exactly as the candidate submitted it.",
                    )
                )
            elif str(rendered.get(key)) != str(value):
                findings.append(
                    verification.high(
                        "validation_altered",
                        f"report.validation.{key}",
                        "a validation answer was reworded or recomputed",
                        f"Reproduce {key} verbatim; validation is never scored or edited.",
                    )
                )

    for path, text in _client_visible_strings(report):
        if conversation_guardrails.contains_forbidden_number(text):
            findings.append(
                verification.high(
                    "number_reaches_client",
                    path,
                    "the section states a number bound to the assessment",
                    "State the grade as a word; no score, percentage or rank reaches "
                    "a client.",
                )
            )

    return verification.verdict("gate:siddhi", findings)


#: agent id -> its gate. A table rather than a chain of `if`s for the same
#: reason the tool grants are: an agent added without a gate should be visibly
#: absent from a mapping, not silently unchecked.
GATES = {
    "bodha": bodha_gate,
    "sutra": sutra_gate,
    "vaada": vaada_gate,
    "miti": miti_gate,
    "siddhi": siddhi_gate,
}


#: Agents with no gate in this table, each with the reason. `GATES` and this
#: mapping together must name every agent in `identity.AGENTS`, so an agent
#: added without a gate is still visibly absent rather than silently unchecked.
UNGATED: dict[str, str] = {
    "yukti": (
        "Yukti's evidence is grounded deterministically in yukti/grounding.py "
        "before any row is written; it publishes no artifact for a gate to read."
    ),
}


class NoGate(KeyError):
    """No gate for this agent. Never defaulted to a permissive pass."""


def run_gate(agent_id: str, payload: Mapping[str, Any]) -> verification.Verdict:
    """Run the gate for `agent_id`.

    Raises rather than returning a passing verdict for an unknown agent. A
    default pass is how an ungated agent ships looking gated.
    """
    try:
        gate = GATES[agent_id]
    except KeyError as exc:
        raise NoGate(agent_id) from exc
    return gate(payload)
