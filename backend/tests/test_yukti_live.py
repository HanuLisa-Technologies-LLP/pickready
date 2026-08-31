"""Yukti's pre-screen grade, on the live path (spec-doc6 §4.4).

WHAT THIS FILE IS DEFENDING
-----------------------------
Four things, and only the first is about accuracy:

  1. A resume-stage grade exists, it reaches the row a recruiter reads, and it
     is produced by reading EVIDENCE rather than by measuring how alike two
     documents are.
  2. Nothing about a candidate's identity can move it. Same document, different
     name: same grade, same number, every time.
  3. Missing evidence costs CONFIDENCE and never SCORE. This is the one the
     Runbook itself calls a fairness failure when it is got wrong (§6.6), and it
     is the difference between a career changer receiving a low-confidence
     report a person reads and a confidently poor grade nobody looks at.
  4. Nothing here can reject anybody. `Hold` means a person should look, and
     the vocabulary has no fifth value.

WHY SO MUCH OF IT IS STRUCTURAL RATHER THAN BEHAVIOURAL
---------------------------------------------------------
Several assertions below walk a dataclass's field list, or a module's AST, or
the set of module attributes, rather than calling a function and checking what
comes back. That is deliberate and it is the same argument Miti's evaluator
isolation test makes: a test that asserts a name is absent passes happily the
day somebody adds a field called `notes`, and a test that asserts a behaviour
passes happily the day a second code path appears beside the one it exercises.
The properties here are properties of the SHAPE, so they are asserted against
the shape.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import re
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import matching, resume_parsing
from app.services.hiring import prescreen, runbook_data


# ── Fixtures shared across the file ─────────────────────────────────────────

#: The job's requirement set. Five items, none of them exotic, so the coverage
#: arithmetic below has room to move without any single item dominating.
REQUIREMENTS = (
    "data pipeline",
    "relational database",
    "stakeholder management",
    "incident management",
    "python",
)

#: One claim, at each of the three tiers a resume can reach (RPN-PHIL-001 §6.1).
ASSERTED = "Responsible for the data pipeline area and related duties for the group."
CHECKABLE = (
    "I owned the data pipeline rebuild, migrating 40 source systems and cutting "
    "the nightly run from 9 hours to 40 minutes."
)
ARTEFACT = (
    "I owned the data pipeline rebuild, migrating 40 source systems and cutting "
    "the nightly run from 9 hours to 40 minutes, written up at "
    "https://github.com/example/pipeline."
)


def screen(
    resume: str,
    *,
    requirements: tuple[str, ...] = REQUIREMENTS,
    skills: tuple[str, ...] = (),
    identities: tuple[str, ...] = (),
    states: frozenset[str] | None = None,
    primary: str = prescreen.STATE_STANDARD,
    gaps: tuple[int, ...] = (),
) -> prescreen.PreScreenResult:
    return prescreen.grade(
        prescreen.PreScreenInput(
            requirements=requirements,
            requirement_source=prescreen.REQUIREMENTS_FROM_JD,
            claims=prescreen.claims_from_resume(
                resume, skills=list(skills), identities=identities
            ),
            states=states or frozenset({primary}),
            primary_state=primary,
            gap_months=gaps,
        )
    )


# ── 1. THE EVIDENCE MODEL: a resume line is a claim, not a fact ─────────────

def test_the_three_resume_stage_tiers_are_read_off_the_fabrication_cost_ladder():
    """§6.2: strength is how expensive the evidence is to FAKE, not how
    impressive it looks. The three sentences below say the same thing about the
    same work and differ only in what could be checked."""
    assert screen(ASSERTED).internal.best_tier == prescreen.TIER_ASSERTED
    assert screen(CHECKABLE).internal.best_tier == prescreen.TIER_SPECIFIC
    assert screen(ARTEFACT).internal.best_tier == prescreen.TIER_ARTEFACT

    scores = [
        screen(ASSERTED).internal.value,
        screen(CHECKABLE).internal.value,
        screen(ARTEFACT).internal.value,
    ]
    assert scores == sorted(scores), scores


def test_no_resume_evidence_can_reach_a_tier_above_e2():
    """§6.1's E3, E4 and E5 all require something a document cannot contain: a
    controlled response, an observation, or a third party. A grader that could
    reach them from a resume would be asserting the thing it has no evidence
    for, which is the entire failure mode the evidence model exists to close."""
    assert prescreen.RESUME_STAGE_TIERS == ("E0", "E1", "E2")
    florid = (
        "I single-handedly architected, owned and delivered the entire data "
        "pipeline platform, see https://github.com/example/x, migrating 400 "
        "systems, cutting 9000 minutes to 4 minutes, for a team of 300."
    )
    assert screen(florid).internal.best_tier == prescreen.TIER_ARTEFACT


def test_prose_quality_alone_moves_nothing():
    """The AI-written-resume case. Two sentences, both fluent, one carrying a
    mechanism and a checkable number and one carrying neither."""
    beautiful = (
        "A visionary and results-oriented leader with a proven track record of "
        "driving transformational outcomes across the data pipeline landscape."
    )
    plain = "Fixed the data pipeline by cutting its nightly run from 9 hours to 40 minutes."
    assert screen(beautiful).internal.best_tier == prescreen.TIER_ASSERTED
    assert screen(plain).internal.best_tier == prescreen.TIER_SPECIFIC
    assert screen(plain).internal.value > screen(beautiful).internal.value


def test_attribution_is_read_and_it_is_read_in_both_directions():
    """§6.4 calls the participated-in versus owned distinction one of the
    highest-value discriminations in resume evaluation, and one that
    similarity-based systems almost never make."""
    owned = "I owned the data pipeline rebuild, migrating 40 systems in 9 months."
    collective = (
        "We built the data pipeline rebuild as a team, migrating 40 systems in 9 months."
    )
    assert screen(owned).internal.value > screen(collective).internal.value


def test_every_threshold_this_module_uses_comes_from_the_runbook_data():
    """The anti-magic-number rule, checked against the file rather than against
    a constant in this test. The A and B cut points ARE §6.1's E2 and E1 default
    strengths, so moving a tier strength in the Runbook moves the grade
    boundary, and neither can be edited alone."""
    tiers = runbook_data.evidence_tiers()["tiers"]
    assert prescreen.tier_strength("E2") == tiers["E2"]["default_strength"]
    assert prescreen.tier_strength("E1") == tiers["E1"]["default_strength"]

    # Straddle each boundary and confirm the letter turns over exactly there.
    a_line = tiers["E2"]["default_strength"] * 100
    b_line = tiers["E1"]["default_strength"] * 100
    assert prescreen._grade_for(a_line) == prescreen.GRADE_A
    assert prescreen._grade_for(a_line - 0.1) == prescreen.GRADE_B
    assert prescreen._grade_for(b_line) == prescreen.GRADE_B
    assert prescreen._grade_for(b_line - 0.1) == prescreen.GRADE_C


def test_the_section_6_1_clamp_holds_at_both_ends():
    """Added to the Runbook in v1.2. The floor is the half that matters here: a
    strength of zero deletes a claim from the ledger by arithmetic rather than
    recording it as weak, and a weak claim that stays visible can be probed."""
    assert prescreen.clamp_tier_strength(1.05) == 1.00
    assert prescreen.clamp_tier_strength(0.0) == 0.05
    assert prescreen.clamp_tier_strength(-3.0) == 0.05


def test_repeating_one_claim_never_manufactures_corroboration():
    """§5.4: a resume and a cover letter are one authorship in one preparation
    session, so everything on a resume is a single independence group. Saying
    the same thing three times is one person saying one thing three times, which
    is exactly what an AI-written resume is good at producing."""
    once = screen(CHECKABLE)
    thrice = screen(" ".join([CHECKABLE, CHECKABLE.replace("40", "40"), CHECKABLE]))
    assert thrice.internal.value == once.internal.value


# ── 2. INSUFFICIENT EVIDENCE REDUCES CONFIDENCE, NEVER SCORE ────────────────

def test_a_requirement_the_resume_is_silent_about_is_excluded_not_zeroed():
    """§6.6, and the sentence the Runbook writes in its own voice: "A missing
    signal gets scored as zero, which is mathematically identical to negative
    evidence, which is wrong and unfair.\""""
    narrow = screen(CHECKABLE, requirements=REQUIREMENTS)
    only_one = screen(CHECKABLE, requirements=("data pipeline",))

    # One requirement covered out of five, versus one out of one. Same claim,
    # same evidence, therefore the same SCORE.
    assert narrow.internal.assessed_requirements == 1
    assert narrow.internal.value == only_one.internal.value

    # And the difference lands entirely on confidence.
    assert narrow.internal.confidence < only_one.internal.confidence
    statuses = {entry["status"] for entry in narrow.ledger}
    assert statuses == {"ASSESSED", "UNKNOWN"}


def test_negative_evidence_is_scored_and_missing_evidence_is_not():
    """The distinction spec-doc6 §4.4 asks to be tested explicitly, because
    conflating the two is the fairness failure the Runbook names.

    A requirement the resume ADDRESSES WEAKLY is assessed and scored low, which
    is right: the candidate spoke to it and what they said was thin. A
    requirement the resume does not address at all is excluded, which is also
    right: they were never asked.
    """
    weak = screen(ASSERTED, requirements=("data pipeline",))
    silent = screen(
        "I owned the incident management rota for 4 severity one outages.",
        requirements=("data pipeline",),
    )

    assert weak.internal.assessed_requirements == 1
    assert weak.named.grade == prescreen.GRADE_C
    assert weak.internal.value > 0

    assert silent.internal.assessed_requirements == 0
    assert silent.named.grade == prescreen.GRADE_HOLD
    assert silent.ledger[0]["status"] == "UNKNOWN"
    # And the two are told apart in words, not only in a field.
    assert "not read as evidence against" in silent.ledger[0]["note"]


def test_broader_coverage_raises_confidence_at_identical_evidence_strength():
    """The other half of §6.6: coverage is a confidence term (§10.7) and never a
    score term. Two resumes making the SAME claim, one about a single
    requirement and one about all five."""
    one = screen(CHECKABLE)
    everything = screen(
        " ".join(
            f"I owned the {req} rebuild, migrating 40 source systems and cutting "
            f"the nightly run from 9 hours to 40 minutes."
            for req in REQUIREMENTS
        )
    )
    assert everything.internal.coverage == 1.0
    assert everything.internal.confidence > one.internal.confidence


def test_confidence_uses_the_runbook_coefficients_and_labels():
    conf = runbook_data.bands()["confidence"]
    coefficients = [
        conf["terms"][term]["coefficient"]
        for term in ("evidence_coverage", "evidence_depth", "independence", "consistency")
    ]
    assert sum(coefficients) == conf["coefficient_sum"]
    assert prescreen.confidence_label(conf["high_threshold"]) == "High"
    assert prescreen.confidence_label(conf["moderate_threshold"]) == "Moderate"
    assert prescreen.confidence_label(conf["low_threshold"]) == "Low"
    assert prescreen.confidence_label(conf["low_threshold"] - 0.01) == "Insufficient"


# ── 3. NAME-BLINDNESS (§52.2) ───────────────────────────────────────────────

NAMES = (
    "Priya Raghunathan",
    "John Smith",
    "Mohammed Ansari",
    "Lakshmi Devi",
    "Wei Chen",
    "Aarti Kumari Yadav",
)


def test_the_pre_screen_grade_is_invariant_to_the_candidate_name():
    """§52.2's anonymised first pass, asserted as the regression test spec-doc6
    §11.1 requires. One document, six names, and the number must not move by a
    hundredth."""
    results = []
    for name in NAMES:
        resume = f"{name}\n{name} is a data engineer.\n{CHECKABLE}"
        results.append(screen(resume, identities=(name, *name.split())))

    grades = {r.named.grade for r in results}
    scores = {r.internal.value for r in results}
    confidences = {r.internal.confidence for r in results}
    assert len(grades) == 1, [r.named.grade for r in results]
    assert len(scores) == 1, [r.internal.value for r in results]
    assert len(confidences) == 1, [r.internal.confidence for r in results]


def test_contact_details_are_stripped_whatever_the_name_is():
    """Identity is not only a name. An email address, a phone number and a
    LinkedIn URL carry it too, and they are removed by shape rather than by
    being listed, so a candidate whose name the caller failed to pass in is
    still anonymised to that extent."""
    dirty = (
        "priya.raghunathan@example.com +91 98765 43210 "
        "linkedin.com/in/priyaraghunathan " + CHECKABLE
    )
    cleaned = prescreen.anonymise(dirty)
    assert "@" not in cleaned
    assert "linkedin" not in cleaned.lower()
    assert not re.search(r"\d{5}", cleaned)
    # And the evidence survives the scrub.
    assert "data pipeline" in cleaned


def test_the_input_type_has_nowhere_to_put_an_identity():
    """THE FIELD SET IS THE ISOLATION.

    Asserted as a whole rather than by naming forbidden fields, because a
    narrower test passes the day somebody adds `notes` or `context` and the hole
    reopens. Same argument, and the same test shape, as Miti's `EvaluatorInput`.
    """
    assert prescreen.field_names(prescreen.PreScreenInput) == (
        "requirements",
        "requirement_source",
        "claims",
        "clock",
        "states",
        "primary_state",
        "gap_months",
        "resume_parsed",
    )


def test_an_employment_span_carries_dates_and_a_title_and_no_employer():
    """§8.9's pedigree cap and §52.2's anonymised pass at once, enforced by the
    absence of the field rather than by a rule about not reading it."""
    assert prescreen.field_names(prescreen.EmploymentSpan) == ("title", "start", "end")


def test_a_brand_name_employer_or_institution_moves_the_grade_by_nothing():
    """§8.9: institutional pedigree may contribute at most 5% of the score, and
    a client may lower that ceiling to zero freely. This module sets it to zero,
    so the measured contribution is not merely under the ceiling, it is nil."""
    plain = screen(CHECKABLE)
    decorated = screen(
        CHECKABLE
        + " Indian Institute of Technology Bombay. Google. McKinsey and Company. "
        "Goldman Sachs. Ex-Infosys."
    )
    delta = abs(decorated.internal.value - plain.internal.value)
    assert delta == 0.0
    assert decorated.named.grade == plain.named.grade
    # Stated against the Runbook's own ceiling as well as against zero, so the
    # rule this satisfies is visible in the assertion.
    ceiling = prescreen.PEDIGREE_CONTRIBUTION_CEILING * max(plain.internal.value, 1.0)
    assert delta <= ceiling


# ── 4. CANDIDATE STATES (§39, §40) ──────────────────────────────────────────

FRESHER_RESUME = (
    "Final year project: I built a data pipeline in Python for 40 sensor "
    "streams, cutting processing from 9 hours to 40 minutes. Internship at a "
    "logistics firm where I owned the relational database migration."
)

EXPERIENCED_RESUME = (
    "I built a data pipeline in Python for 40 sensor streams, cutting "
    "processing from 9 hours to 40 minutes. I owned the relational database "
    "migration."
)


def test_a_fresher_is_not_charged_for_a_track_record_that_cannot_exist():
    """§8.5 and §40.1. The absence of employment history is not negative
    evidence, and the way that is guaranteed is that an unaddressed requirement
    is excluded rather than zeroed, so there is nothing for the absence to
    subtract from."""
    states, primary = prescreen.candidate_states(
        total_experience_years=0.0,
        spans=(),
        job_terms=frozenset({"data", "engineer"}),
        has_academic_claims=True,
    )
    assert primary == prescreen.STATE_FRESHER
    assert prescreen.STATE_FRESHER in states

    fresher = screen(FRESHER_RESUME, primary=prescreen.STATE_FRESHER)
    experienced = screen(EXPERIENCED_RESUME)
    # The academic and internship framing costs nothing: the same claims, at the
    # same tier, score the same whoever made them.
    assert fresher.internal.value >= experienced.internal.value
    assert fresher.named.grade == experienced.named.grade
    assert fresher.named.grade != prescreen.GRADE_HOLD


def test_a_career_break_is_recorded_and_never_scored():
    """§8.6 and §40.2: "Record the break; do not score it." A gap of 26 months
    sits between two roles, and the grade does not know."""
    spans = (
        prescreen.EmploymentSpan("Data Engineer", date(2016, 1, 1), date(2019, 6, 1)),
        prescreen.EmploymentSpan("Data Engineer", date(2021, 8, 1), date(2026, 1, 1)),
    )
    states, primary = prescreen.candidate_states(
        total_experience_years=8.0,
        spans=spans,
        job_terms=frozenset({"data", "engineer"}),
        has_academic_claims=False,
        today=date(2026, 2, 1),
    )
    assert prescreen.STATE_RETURNER in states
    assert primary == prescreen.STATE_RETURNER
    # Recorded, in months, so a later stage can ask one neutral question.
    assert prescreen.employment_gaps(spans) == (26,)

    continuous = screen(CHECKABLE)
    returner = screen(CHECKABLE, primary=prescreen.STATE_RETURNER, gaps=(26,))
    assert returner.internal.value == continuous.internal.value
    assert returner.named.grade == continuous.named.grade
    assert returner.internal.confidence == continuous.internal.confidence


def test_the_score_is_structurally_incapable_of_reading_an_employment_gap():
    """The stronger form of the assertion above.

    Two inputs differing ONLY in `gap_months`, across every plausible gap, must
    produce byte-identical output. A behavioural test on one fixture could be
    satisfied by a penalty that happens not to bind there; this one cannot.
    """
    base = prescreen.PreScreenInput(
        requirements=REQUIREMENTS,
        requirement_source=prescreen.REQUIREMENTS_FROM_JD,
        claims=prescreen.claims_from_resume(CHECKABLE),
    )
    reference = prescreen.grade(base)
    for months in (0, 6, 12, 36, 120):
        varied = dataclasses.replace(base, gap_months=(months,))
        assert prescreen.grade(varied).internal == reference.internal, months


def test_a_career_changer_is_scored_on_transferable_competence_at_full_weight():
    """§40.3: transferable competencies are scored at FULL weight, and the
    domain gap is a stated gap rather than a deficiency. The gap is exactly what
    the UNKNOWN handling already produces."""
    spans = (
        prescreen.EmploymentSpan("Secondary School Teacher", date(2016, 1, 1), date(2024, 1, 1)),
    )
    states, primary = prescreen.candidate_states(
        total_experience_years=8.0,
        spans=spans,
        job_terms=frozenset({"data", "engineer"}),
        has_academic_claims=False,
        today=date(2024, 3, 1),
    )
    assert prescreen.STATE_CAREER_CHANGER in states
    assert primary == prescreen.STATE_CAREER_CHANGER

    changer = screen(CHECKABLE, primary=prescreen.STATE_CAREER_CHANGER)
    incumbent = screen(CHECKABLE)
    assert changer.internal.value == incumbent.internal.value
    assert changer.named.grade == incumbent.named.grade
    # The domain they have not worked in is an UNKNOWN, which reads as a gap in
    # the assessment and not as a gap in the candidate.
    unknown = [e for e in changer.ledger if e["status"] == "UNKNOWN"]
    assert unknown and all(e["strength"] is None for e in unknown)


def test_a_career_changer_state_is_read_through_the_ontology_not_off_the_words():
    """The state itself has to be vocabulary-fair, or a "Deputy Manager,
    Business Finance" applying to an "FP&A Lead" role would be handled as a
    career changer for having used the Indian title for the same job."""
    spans = (
        prescreen.EmploymentSpan("Deputy Manager Business Finance", date(2016, 1, 1), date(2026, 1, 1)),
    )
    states, primary = prescreen.candidate_states(
        total_experience_years=10.0,
        spans=spans,
        job_terms=frozenset({"fp&a", "associate", "manager"}),
        has_academic_claims=False,
        today=date(2026, 2, 1),
    )
    assert prescreen.STATE_CAREER_CHANGER not in states
    assert primary == prescreen.STATE_STANDARD


def test_an_undocumented_work_history_stays_gradeable():
    """§40.5, which the Runbook flags as common and important in India and
    across emerging markets: "Never treat documentation absence as a negative.\""""
    states, primary = prescreen.candidate_states(
        total_experience_years=9.0,
        spans=(),
        job_terms=frozenset({"data", "engineer"}),
        has_academic_claims=False,
    )
    assert primary == prescreen.STATE_INFORMAL_SECTOR

    informal = screen(
        "Ran the data pipeline work for a family logistics business for 9 years. "
        "I owned the move off spreadsheets, cutting the daily close from 9 hours "
        "to 40 minutes across 40 routes.",
        primary=prescreen.STATE_INFORMAL_SECTOR,
    )
    assert informal.named.grade != prescreen.GRADE_HOLD
    assert informal.internal.assessed_requirements >= 1


def test_the_state_set_is_exactly_the_five_this_stage_can_observe():
    """A closed vocabulary, so a state invented at runtime cannot reach a
    dashboard cell that has no rendering for it."""
    observed = {
        prescreen.STATE_FRESHER,
        prescreen.STATE_INFORMAL_SECTOR,
        prescreen.STATE_CAREER_CHANGER,
        prescreen.STATE_RETURNER,
        prescreen.STATE_STANDARD,
    }
    assert set(prescreen._STATE_PRECEDENCE) == observed


# ── 5. THE NUMBER AND THE NAMED GRADE ARE DIFFERENT TYPES (D8) ──────────────

def test_the_named_grade_type_has_no_numeric_field_at_all():
    """D8's enforcement, in the type system rather than in a serialiser rule.

    The delivered PRISM Report is handed a `PreScreenGrade`. There is no numeric
    field on it, so there is nothing for a report to leak: the rule stops being
    something every call site has to remember.
    """
    for field in dataclasses.fields(prescreen.PreScreenGrade):
        assert field.type in ("str", str), (field.name, field.type)


def test_the_report_payload_contains_no_digit_anywhere():
    result = screen(CHECKABLE)
    payload = result.report_payload()
    assert payload["grade"] in prescreen.GRADES
    for key, value in payload.items():
        assert isinstance(value, str), key
    # `evidence_note` counts requirements, which is a count of criteria and not
    # a score, so the ban is on SCORES rather than on characters. What must
    # never appear is the internal value itself.
    blob = " ".join(payload.values())
    assert str(result.internal.value) not in blob
    assert str(result.internal.confidence) not in blob


def test_the_dashboard_cell_carries_the_number_and_names_it_separately():
    """D8 the other way round: the Ready Pick Score renders on the dashboard.
    Two keys, so a consumer reaching for one cannot receive the other."""
    cell = screen(CHECKABLE).dashboard_cell()
    assert cell["prescreen_grade"] in prescreen.GRADES
    assert isinstance(cell["prescreen_score"], float)
    assert cell["prescreen_grade"] != cell["prescreen_score"]


def test_the_grade_and_the_score_are_stored_in_separate_columns():
    """The same split, in the schema. A serialiser selecting `prescreen_grade`
    receives a word; a single JSON blob carrying both would put the number one
    `.get` away from every consumer in the product."""
    migration = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0065_prescreen_grade.py"
    ).read_text(encoding="utf-8")
    assert '"prescreen_grade"' in migration
    assert '"prescreen_score"' in migration
    # And the closed vocabulary is a database constraint, not a convention.
    for value in prescreen.GRADES:
        assert f"'{value}'" in migration


# ── 6. NOTHING HERE REJECTS ANYBODY ─────────────────────────────────────────

def test_the_grade_vocabulary_has_no_rejecting_value():
    """"No flag ever auto-rejects, and the enforcement is the absence of the
    capability." Four values, and `Hold` is the Runbook's own HOLD (§10.8): not
    ranked pending human disposition."""
    assert prescreen.GRADES == ("A", "B", "C", "Hold")
    lowered = {g.lower() for g in prescreen.GRADES}
    assert not lowered & {"reject", "rejected", "fail", "failed", "excluded", "no"}


def test_no_result_type_carries_a_decision_a_disposition_or_a_status():
    """Same enforcement-by-absence as `TriangulationResult`. There is no field a
    future caller could read as an instruction to end a candidacy."""
    for cls in (
        prescreen.PreScreenGrade,
        prescreen.PreScreenScore,
        prescreen.PreScreenResult,
    ):
        names = set(prescreen.field_names(cls))
        assert not names & {
            "reject",
            "rejected",
            "decision",
            "disposition",
            "status",
            "auto_cleared",
            "shortlist",
        }, (cls.__name__, names)


def test_a_resume_that_cannot_be_read_goes_to_a_person_and_not_to_a_verdict():
    """A scanned image, an empty file, or a job with nothing stated yet. `Hold`
    is a graded outcome meaning somebody should look, and it is deliberately the
    only value that means anything other than a reading of the evidence."""
    unreadable = prescreen.grade(
        prescreen.PreScreenInput(
            requirements=REQUIREMENTS,
            requirement_source=prescreen.REQUIREMENTS_FROM_JD,
            claims=(),
            resume_parsed=False,
        )
    )
    assert unreadable.named.grade == prescreen.GRADE_HOLD
    assert "waiting on a person" in unreadable.named.evidence_note

    no_requirements = prescreen.grade(
        prescreen.PreScreenInput(
            requirements=(),
            requirement_source=prescreen.REQUIREMENTS_FROM_JD,
            claims=prescreen.claims_from_resume(CHECKABLE),
        )
    )
    assert no_requirements.named.grade == prescreen.GRADE_HOLD


# ── 7. NO MODEL, AND THE SAME ANSWER EVERY TIME ─────────────────────────────

def test_the_grader_imports_no_router_and_calls_no_model():
    """An AST walk over the source, not a docstring claim.

    A pre-screen that called a provider would make a candidate's triage position
    depend on when they applied, and would fail exactly when the provider is
    already failing, which is when the dashboard matters most.
    """
    source = pathlib.Path(inspect.getfile(prescreen)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)
    assert not {n for n in imported if "llm" in n or "agent_loop" in n}, imported
    for banned in ("invoke_llm", "chat_completion", "llm_router", "embed("):
        assert banned not in source, banned


def test_the_same_resume_grades_identically_across_a_hundred_runs():
    """Determinism is what makes a disagreement about a grade a disagreement
    about a rule. Two runs producing different grades would make a rubric
    problem indistinguishable from noise."""
    first = screen(CHECKABLE)
    for _ in range(100):
        again = screen(CHECKABLE)
        assert again.internal == first.internal
        assert again.named == first.named
        assert again.ledger == first.ledger


def test_gate_g1_is_not_consulted_and_the_requirement_source_is_recorded_instead():
    """The contract with the scorecard: G1 asks whether a candidate may be
    EVALUATED against a frozen matrix. A pre-screen is a reading of a document
    against whatever the job has said about itself so far, and a job that has
    not been through Sutra still receives applicants who still need triaging.
    What is owed is not a refusal but an honest record of which was used."""
    source = pathlib.Path(inspect.getfile(prescreen)).read_text(encoding="utf-8")
    assert "require_frozen_matrix" not in source
    assert "FrozenMatrix" not in source

    from_jd = prescreen.requirement_terms(jd_skills=["python"], job_title="Data Engineer")
    assert from_jd[1] == prescreen.REQUIREMENTS_FROM_JD
    from_matrix = prescreen.requirement_terms(
        competencies=["Data pipeline design"], jd_skills=["python"]
    )
    assert from_matrix[1] == prescreen.REQUIREMENTS_FROM_COMPETENCIES
    assert from_matrix[0] == ("Data pipeline design",)


# ── 8. THE LIVE PATH ────────────────────────────────────────────────────────

class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _LiveSession:
    """The smallest session that can carry a real `parse_resume` call.

    Deliberately not a mock with recorded call assertions: what is being proven
    is that a grade REACHES THE ROW, so the fake records the SQL the pipeline
    actually issued and the test reads the values out of it.
    """

    def __init__(self, profile: Any, job: Any, links: list[Any], candidate: Any) -> None:
        self.profile = profile
        self.job = job
        self.links = links
        self.candidate = candidate
        self.prescreen_writes: dict[str, dict[str, Any]] = {}
        self.commits = 0

    async def get(self, model: Any, _ident: Any = None) -> Any:
        name = getattr(model, "__name__", "")
        return {
            "Profile": self.profile,
            "Job": self.job,
            "Candidate": self.candidate,
        }.get(name)

    async def execute(self, query: Any, params: dict | None = None) -> _Rows:
        sql = str(query)
        if "UPDATE job_candidate_links" in sql and params:
            self.prescreen_writes[params["link_id"]] = params
            return _Rows([])
        if "job_candidate_links" in sql.lower() or "JobCandidateLink" in sql:
            return _Rows(self.links)
        return _Rows(self.links)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


def _live_fixture(resume: str) -> tuple[_LiveSession, Any]:
    candidate_id = uuid.uuid4()
    profile = SimpleNamespace(
        id=uuid.uuid4(),
        candidate_id=candidate_id,
        resume_text=resume,
        parsed_fields_json={"skills": ["Python"], "employment_history": []},
        aspects_json={},
        embedding=None,
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Data Engineer",
        department="Data",
        jd_json={"skills": list(REQUIREMENTS)},
        framework_approved_at=None,
    )
    link = SimpleNamespace(
        id=uuid.uuid4(),
        job_id=job.id,
        candidate_id=candidate_id,
        profile_id=profile.id,
    )
    candidate = SimpleNamespace(id=candidate_id, full_name="Priya Raghunathan")
    return _LiveSession(profile, job, [link], candidate), profile


@pytest.mark.asyncio
async def test_parsing_a_resume_writes_a_grade_onto_every_application(monkeypatch):
    """THE LIVE ENTRY POINT.

    `resume_parsing.parse_resume` is what every upload route in the product
    enqueues, so hanging the grade off the parse is what makes it impossible for
    a route to accept a resume and forget to grade it. The assertion is that the
    row a recruiter reads carries an A / B / C / Hold, not that a function was
    called.
    """
    session, profile = _live_fixture(f"Priya Raghunathan\n{CHECKABLE}")

    async def _extract(*_args, **_kwargs):
        return {"skills": ["Python"], "total_experience_years": 6, "education": [], "employment_history": []}

    async def _embed(_texts):
        return [[0.1] * 1024]

    monkeypatch.setattr(resume_parsing, "extract_structured_fields", _extract)
    monkeypatch.setattr(resume_parsing, "embed", _embed)

    await resume_parsing.parse_resume(session, profile.id)

    assert session.commits == 1
    assert len(session.prescreen_writes) == 1
    written = next(iter(session.prescreen_writes.values()))
    assert written["grade"] in prescreen.GRADES
    assert isinstance(written["score"], float)
    assert '"requirement_source": "job_description"' in written["payload"]


@pytest.mark.asyncio
async def test_the_live_grade_is_the_same_under_a_different_candidate_name(monkeypatch):
    """Name-blindness through the real path, not only through the pure
    function. The name reaches `parse_resume` on the candidate row, which is the
    one place it could have leaked back in."""
    async def _extract(*_args, **_kwargs):
        return {"skills": ["Python"], "total_experience_years": 6, "education": [], "employment_history": []}

    async def _embed(_texts):
        return [[0.1] * 1024]

    monkeypatch.setattr(resume_parsing, "extract_structured_fields", _extract)
    monkeypatch.setattr(resume_parsing, "embed", _embed)

    seen = []
    for name in NAMES:
        session, profile = _live_fixture(f"{name}\n{name} is a data engineer.\n{CHECKABLE}")
        session.candidate.full_name = name
        await resume_parsing.parse_resume(session, profile.id)
        written = next(iter(session.prescreen_writes.values()))
        seen.append((written["grade"], written["score"]))
    assert len(set(seen)) == 1, seen


@pytest.mark.asyncio
async def test_a_grading_failure_never_costs_the_candidate_their_parse(monkeypatch):
    """Parsing is what makes a candidate searchable, matchable and assessable.
    A grading failure costs one dashboard cell; taking the parse down with it
    would cost the candidate their whole presence in the product."""
    session, profile = _live_fixture(CHECKABLE)

    async def _extract(*_args, **_kwargs):
        return {"skills": [], "total_experience_years": None, "education": [], "employment_history": []}

    async def _embed(_texts):
        return [[0.1] * 1024]

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("the grader fell over")

    monkeypatch.setattr(resume_parsing, "extract_structured_fields", _extract)
    monkeypatch.setattr(resume_parsing, "embed", _embed)
    monkeypatch.setattr(prescreen, "grade_profile", _explode)

    await resume_parsing.parse_resume(session, profile.id)

    assert session.commits == 1
    assert profile.embedding == [0.1] * 1024
    assert session.prescreen_writes == {}


@pytest.mark.asyncio
async def test_an_application_submitted_with_a_different_resume_is_not_regraded():
    """An application is an immutable snapshot of the document it was actually
    sent with. Re-grading it against a resume the candidate uploaded later would
    rewrite the record of what a recruiter saw."""
    session, profile = _live_fixture(CHECKABLE)
    session.links[0].profile_id = uuid.uuid4()  # a different, older resume
    graded = await prescreen.grade_profile(session, profile)
    assert graded == 0
    assert session.prescreen_writes == {}


# ── 9. ONE IMPLEMENTATION, SHARED (spec-doc6 §4.6, §10.1 rule 12) ───────────

def test_matching_asks_this_module_for_its_deterministic_breakdown():
    """The deletion half of spec-doc6 §4.1. There is one resume-stage grader,
    and `matching` calls it rather than carrying a second reading of its own."""
    assert matching.prescreen is prescreen
    assert not hasattr(matching, "_fallback_breakdown")
    source = pathlib.Path(inspect.getfile(matching)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "prescreen_breakdown" in functions
    assert not {f for f in functions if "fallback" in f}, functions


def test_the_deterministic_breakdown_tracks_the_evidence_and_stops_below_the_top():
    weak = matching.prescreen_breakdown(screen(ASSERTED))
    strong = matching.prescreen_breakdown(screen(ARTEFACT))
    assert strong["overall"]["score"] > weak["overall"]["score"]
    assert strong["overall"]["score"] <= 8
    assert strong["scoring_mode"] == matching.SCORING_MODE_PRESCREEN
