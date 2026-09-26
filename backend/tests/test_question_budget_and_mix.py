"""The question budget, the 70/20/10 mix and the deterministic composer.

Pure: no database, no model, no clock. Everything here is a function of the
contract's skills, the grade, the STEM verdict, the title and the settings, so
two candidates on one job must get the same answer every time, and the tests
say so directly.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.assessment_contract import ContractSkill
from app.services.assessment_formats import composition, types
from app.services.assessment_questions import budget
from app.services.stem_classification import is_computing_occupation


def _skills(must: int, nice: int, behavioural: int) -> list[ContractSkill]:
    skills: list[ContractSkill] = []
    for bucket, count in (("must_have", must), ("nice_to_have", nice), ("behavioural", behavioural)):
        for priority in range(1, count + 1):
            skills.append(
                ContractSkill(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"{bucket}-{priority}"),
                    name=f"{bucket} {priority}",
                    bucket=bucket,
                    priority=priority,
                    evidence_line=f"Evidence for {bucket} {priority}.",
                )
            )
    return skills


# ── How many ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "grade,skills,expected",
    [
        ("non_managerial", 3, 10),
        ("managerial", 3, 10),
        ("leadership", 12, 12),
        ("cxo", 3, 8),
        ("cxo", 15, 15),
        ("non_managerial", 15, 15),
        (None, 4, 10),
        ("not-a-grade", 4, 10),
    ],
)
def test_the_budget_is_one_question_per_skill_above_the_floor(grade, skills, expected) -> None:
    assert budget.question_budget(grade, skills) == expected


def test_with_at_most_five_skills_a_bucket_the_budget_is_eight_to_fifteen() -> None:
    budgets = {
        budget.question_budget(grade, must + nice + behavioural)
        for grade in budget.GRADES
        for must in range(1, 6)
        for nice in range(0, 6)
        for behavioural in range(1, 6)
    }
    assert min(budgets) == 8 and max(budgets) == 15


# ── Which kind ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "total,coding,expected",
    [
        (10, True, (7, 2, 1)),
        (8, True, (6, 1, 1)),
        (15, True, (11, 3, 1)),
        (9, True, (6, 2, 1)),
        (12, True, (9, 2, 1)),
        (10, False, (9, 0, 1)),
        (8, False, (7, 0, 1)),
        (15, False, (14, 0, 1)),
        # 4.5 prose and 0.5 objective: the tie goes to prose.
        (5, False, (5, 0, 0)),
    ],
)
def test_the_mix_is_apportioned_by_largest_remainder(total, coding, expected) -> None:
    result = budget.mix(total, coding=coding)
    assert (result.prose, result.coding, result.objective) == expected


def test_a_tie_goes_to_prose_then_coding_then_objective() -> None:
    """N=8: exact shares 5.6 / 1.6 / 0.8. After the floors (5/1/0) two slots
    remain; objective's 0.8 takes one and the 0.6 tie between prose and coding
    goes to prose. Swapping the tie order would give 5/2/1."""
    assert budget.mix(8, coding=True).as_dict() == {"prose": 6, "coding": 1, "objective": 1}


@pytest.mark.parametrize("total", range(5, 26))
def test_every_budget_is_spent_exactly_and_never_on_coding_without_a_coding_role(total) -> None:
    with_coding = budget.mix(total, coding=True)
    without = budget.mix(total, coding=False)
    assert with_coding.total == total and without.total == total
    assert without.coding == 0
    assert without.objective == with_coding.objective
    assert without.prose == with_coding.prose + with_coding.coding


def test_the_shares_must_sum_to_one_or_the_settings_refuse_to_load() -> None:
    with pytest.raises(ValidationError):
        Settings(assessment_share_prose=0.7, assessment_share_coding=0.2, assessment_share_objective=0.2)
    with pytest.raises(ValidationError):
        Settings(assessment_question_floor_cxo=0)


# ── Who is a coding role ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,computing",
    [
        ("Senior Software Engineer", True),
        ("Python Developer", True),
        ("Data Scientist", True),
        ("Machine Learning Engineer", True),
        ("Full Stack Developer", True),
        ("Embedded Software Engineer", True),
        # STEM, and not asked to program.
        ("Civil Engineer", False),
        ("Mechanical Design Engineer", False),
        ("Electronics Engineer", False),
        ("Network Engineer", False),
        # A manager in a computing field is hired to manage.
        ("Software Development Manager", False),
        ("Engineering Manager", False),
        # Not STEM at all.
        ("HR Manager", False),
        ("Sales Engineer", False),
        # Unrecognised: no verdict, no coding.
        ("Data Analyst", False),
    ],
)
def test_a_computing_occupation_is_read_from_the_title(title, computing) -> None:
    assert is_computing_occupation(title) is computing


def test_coding_eligibility_names_the_first_condition_that_failed() -> None:
    assert budget.coding_eligibility(
        role_classification="NON_STEM", job_title="Software Engineer", execution_enabled=True
    ) == budget.CodingEligibility(False, budget.CODING_EXCLUDED_NOT_STEM)
    assert budget.coding_eligibility(
        role_classification="STEM", job_title="Civil Engineer", execution_enabled=True
    ) == budget.CodingEligibility(False, budget.CODING_EXCLUDED_NOT_COMPUTING)
    assert budget.coding_eligibility(
        role_classification="STEM", job_title="Software Engineer", execution_enabled=False
    ) == budget.CodingEligibility(False, budget.CODING_EXCLUDED_DISABLED)
    assert budget.coding_eligibility(
        role_classification="STEM", job_title="Software Engineer", execution_enabled=True
    ) == budget.CodingEligibility(True, None)


# ── Which skill, and which format ────────────────────────────────────────────


def test_every_skill_is_asked_once_in_bucket_and_priority_order() -> None:
    skills = _skills(3, 2, 2)
    plan = composition.allocate(list(reversed(skills)), 7, stem=True)
    assert [skill.name for skill in plan] == [skill.name for skill in skills]


def test_a_stem_role_spends_its_extra_slots_on_must_and_nice_only() -> None:
    skills = _skills(2, 1, 3)
    plan = composition.allocate(skills, 10, stem=True)
    extras = plan[len(skills):]
    assert [skill.bucket for skill in extras] == ["must_have", "must_have", "nice_to_have", "must_have"]
    assert {skill.id for skill in plan} == {skill.id for skill in skills}


def test_a_non_stem_role_spreads_its_extra_slots_over_every_bucket() -> None:
    skills = _skills(1, 0, 2)
    plan = composition.allocate(skills, 8, stem=False)
    extras = plan[len(skills):]
    assert [skill.bucket for skill in extras] == [
        "must_have", "behavioural", "behavioural", "must_have", "behavioural",
    ]


def test_a_budget_below_the_skill_count_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ValueError):
        composition.allocate(_skills(3, 3, 3), 8, stem=True)


def _compose(must: int, nice: int, behavioural: int, *, grade: str = "non_managerial",
             coding: bool = True, stem: bool = True) -> tuple[list[composition.Slot], budget.Mix]:
    skills = _skills(must, nice, behavioural)
    total = budget.question_budget(grade, len(skills))
    mix = budget.mix(total, coding=coding)
    slots = composition.compose(composition.allocate(skills, total, stem=stem), mix=mix, grade=grade)
    return slots, mix


def test_the_composer_plans_exactly_the_mix() -> None:
    slots, mix = _compose(5, 5, 5)
    assert composition.served_mix(slots) == mix.as_dict() == {"prose": 11, "coding": 3, "objective": 1}
    assert {slot.planned_family for slot in slots if slot.question_type == types.CODING} == {"coding"}


def test_structured_questions_land_on_the_lowest_priority_nice_to_have_first() -> None:
    """Fifteen skills, fifteen slots, no repeats: coding goes to the three
    lowest-priority Nice-to-have skills, then the objective question to the
    next, so every Must-have keeps its open-ended question."""
    slots, _ = _compose(5, 5, 5)
    structured = [slot for slot in slots if slot.planned_family != "prose"]
    assert [slot.skill_name for slot in structured if slot.question_type == types.CODING] == [
        "nice_to_have 3", "nice_to_have 4", "nice_to_have 5",
    ]
    assert [slot.skill_name for slot in structured if slot.planned_family == "objective"] == [
        "nice_to_have 2",
    ]
    assert all(slot.category != "behavioural" for slot in structured)


def test_a_skills_repeat_slots_turn_structured_before_any_single_slot_skill() -> None:
    slots, _ = _compose(2, 1, 3)  # 6 skills, budget 10, four repeats on Must/Nice
    structured = [slot for slot in slots if slot.planned_family != "prose"]
    first_seen: set[uuid.UUID] = set()
    repeats: list[int] = []
    for slot in slots:
        if slot.competency_id in first_seen:
            repeats.append(slot.index)
        first_seen.add(slot.competency_id)
    assert {slot.index for slot in structured} <= set(repeats)
    # And every skill still has a prose question.
    prose_skills = {slot.competency_id for slot in slots if slot.planned_family == "prose"}
    assert prose_skills == {slot.competency_id for slot in slots}


def test_behavioural_skills_are_always_prose_and_never_evidence_anchored() -> None:
    for coding in (True, False):
        slots, _ = _compose(1, 0, 5, coding=coding)
        for slot in slots:
            if slot.category == "behavioural":
                assert slot.question_type == types.SHORT_ANSWER
            elif slot.planned_family == "prose":
                assert slot.question_type == types.EVIDENCE_BASED


def test_a_non_coding_role_is_never_planned_a_coding_question() -> None:
    slots, mix = _compose(5, 5, 5, coding=False, stem=False)
    assert mix.coding == 0
    assert not any(slot.question_type == types.CODING for slot in slots)


def test_a_senior_grade_never_uses_the_single_answer_mcq() -> None:
    slots, _ = _compose(5, 5, 5, grade="cxo", coding=False)
    assert not any(slot.question_type == types.MCQ_SINGLE for slot in slots)


def test_the_composition_is_the_same_for_every_candidate_on_a_job() -> None:
    first, _ = _compose(3, 2, 4)
    second, _ = _compose(3, 2, 4)
    assert [(s.competency_id, s.question_type) for s in first] == [
        (s.competency_id, s.question_type) for s in second
    ]


def test_allocations_follow_the_family_clock() -> None:
    slots, _ = _compose(5, 5, 5)
    by_family = {slot.planned_family: slot.time_allocation_seconds for slot in slots}
    assert by_family == {"prose": 180, "coding": 1200, "objective": 60}


# ── Validation: a shortfall needs a reason ───────────────────────────────────


def _filled(slots: list[composition.Slot]) -> None:
    """Make every slot servable the way the writers would."""
    for slot in slots:
        if slot.question_type == types.EVIDENCE_BASED:
            slot.resume_anchor = f"Built the platform for {slot.skill_name} at Kestrel"
            slot.resume_anchor += f" ({slot.index})"
        elif slot.question_type == types.CODING:
            slot.coding_draft = object()
        elif composition.family_of(slot.question_type) == "objective":
            slot.payload = {
                "mcq_single": {"options": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"},
                                           {"id": "c", "text": "z"}], "correct_option_id": "a"},
                "mcq_multi": {"options": [{"id": k, "text": k * 2} for k in "abcd"],
                              "correct_option_ids": ["a", "b"], "scoring": "partial"},
                "fill_blank": {"template": "The ___ pattern.",
                               "blanks": [{"index": 0, "accepted": ["observer"], "case_sensitive": False}]},
            }[slot.question_type]


def test_a_filled_composition_validates() -> None:
    slots, mix = _compose(5, 5, 5)
    _filled(slots)
    assert composition.validate(slots, mix=mix, skills=_skills(5, 5, 5)) == []


def test_serving_prose_for_a_planned_coding_question_needs_a_recorded_reason() -> None:
    slots, mix = _compose(5, 5, 5)
    _filled(slots)
    coding = next(slot for slot in slots if slot.question_type == types.CODING)
    # A silent swap is refused ...
    coding.question_type = types.SHORT_ANSWER
    coding.coding_draft = None
    assert any("no recorded reason" in failure for failure in
               composition.validate(slots, mix=mix, skills=_skills(5, 5, 5)))
    # ... and the recorded degradation is accepted.
    composition.degrade_to_prose(coding, "code_execution_unavailable", "Tell me about it.", generated=False)
    assert composition.validate(slots, mix=mix, skills=_skills(5, 5, 5)) == []
    assert coding.degradation == "code_execution_unavailable"


def test_a_coding_slot_without_its_proven_draft_is_refused() -> None:
    slots, mix = _compose(5, 5, 5)
    _filled(slots)
    next(slot for slot in slots if slot.question_type == types.CODING).coding_draft = None
    assert any("no validated draft" in failure for failure in
               composition.validate(slots, mix=mix, skills=_skills(5, 5, 5)))


def test_a_degradation_must_name_its_reason() -> None:
    slots, _ = _compose(5, 5, 5)
    with pytest.raises(ValueError):
        composition.degrade_to_prose(slots[-1], "", "Tell me.", generated=False)


def test_a_skill_nobody_is_asked_about_is_refused() -> None:
    slots, mix = _compose(2, 1, 2)
    _filled(slots)
    extra = ContractSkill(id=uuid.uuid4(), name="Never asked", bucket="must_have", priority=9, evidence_line="x")
    failures = composition.validate(slots, mix=mix, skills=_skills(2, 1, 2) + [extra])
    assert any("Never asked" in failure for failure in failures)


def test_the_fallback_serves_every_unfilled_slot_and_records_it() -> None:
    slots, mix = _compose(5, 5, 5)
    for slot in slots:
        if slot.question_type == types.CODING:
            composition.degrade_to_prose(slot, "generation_failed", "Tell me.", generated=False)
    prose = [f"Prose question {slot.index}" for slot in slots]
    composition.fall_back(slots, prose, generated=[False] * len(slots))
    assert composition.validate(slots, mix=mix, skills=_skills(5, 5, 5)) == []
    assert composition.served_mix(slots) == {"prose": 15, "coding": 0, "objective": 0}
    reasons = sorted({slot.degradation for slot in slots if slot.degradation})
    assert reasons == ["generation_failed", "objective_generation_failed"]
