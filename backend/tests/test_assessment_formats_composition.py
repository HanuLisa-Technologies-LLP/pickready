"""Composition: the budgeted mix is served exactly, and a shortfall is a record.

SUPERSEDES the 2026-09-02 composition (evidence majority by weight and time,
supporting share by grade, a per-grade duration the allocations were scaled
to fit). Appendix B of the Vivekium release fixes the mix by COUNT instead:
70/20/10 prose, coding and objective for a coding role, prose and objective
otherwise, one question per skill above the grade's floor
(`assessment_questions.budget`). The budget and the placement are pinned in
`tests/test_question_budget_and_mix.py`; this module pins each rule the
validator enforces, one at a time, because a validator is only worth having
if every rule fails on its own, and the payload shapes every format stores.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.assessment_contract import ContractSkill
from app.services.assessment_formats import composition
from app.services.assessment_formats import types
from app.services.assessment_questions import budget


def _skills(must: int = 2, nice: int = 1, behavioural: int = 2) -> list[ContractSkill]:
    return [
        ContractSkill(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{bucket}-{priority}"),
            name=f"{bucket} {priority}",
            bucket=bucket,
            priority=priority,
            evidence_line=f"Evidence for {bucket} {priority}.",
        )
        for bucket, count in (("must_have", must), ("nice_to_have", nice), ("behavioural", behavioural))
        for priority in range(1, count + 1)
    ]


def _served(*, coding: bool = True, grade: str = "non_managerial") -> tuple[list[composition.Slot], budget.Mix, list[ContractSkill]]:
    """A composition as the writers would leave it: every slot filled."""
    skills = _skills()
    total = budget.question_budget(grade, len(skills))
    mix = budget.mix(total, coding=coding)
    slots = composition.compose(composition.allocate(skills, total, stem=True), mix=mix, grade=grade)
    for slot in slots:
        if slot.question_type == types.EVIDENCE_BASED:
            slot.resume_anchor = f"Led the {slot.index} migration at a previous employer"
        elif slot.question_type == types.CODING:
            slot.coding_draft = object()
        elif composition.family_of(slot.question_type) == composition.FAMILY_OBJECTIVE:
            slot.payload = dict(_PAYLOADS[slot.question_type])
    return slots, mix, skills


def test_a_served_composition_validates() -> None:
    slots, mix, skills = _served()
    assert composition.validate(slots, mix=mix, skills=skills) == []


def test_an_empty_assessment_is_rejected() -> None:
    assert composition.validate([], mix=budget.Mix(0, 0, 0), skills=[]) == [
        "the assessment has no questions"
    ]


def test_an_unanchored_evidence_question_is_rejected() -> None:
    slots, mix, skills = _served()
    next(slot for slot in slots if slot.question_type == types.EVIDENCE_BASED).resume_anchor = "led"
    assert any("not anchored" in failure for failure in composition.validate(slots, mix=mix, skills=skills))


def test_two_questions_probing_the_same_resume_item_are_rejected() -> None:
    slots, mix, skills = _served()
    evidence = [slot for slot in slots if slot.question_type == types.EVIDENCE_BASED]
    evidence[1].resume_anchor = evidence[0].resume_anchor
    assert "two questions probe the same resume item" in composition.validate(slots, mix=mix, skills=skills)


def test_a_structured_behavioural_question_is_rejected() -> None:
    slots, mix, skills = _served()
    behavioural = next(slot for slot in slots if slot.category == "behavioural")
    behavioural.question_type = types.MCQ_SINGLE
    behavioural.planned_family = composition.FAMILY_OBJECTIVE
    assert "a behavioural skill is answered in prose only" in composition.validate(
        slots, mix=mix, skills=skills
    )


def test_a_coding_question_on_a_role_planned_without_coding_is_rejected() -> None:
    slots, mix, skills = _served(coding=False)
    target = next(slot for slot in slots if slot.question_type == types.EVIDENCE_BASED)
    target.question_type = types.CODING
    target.coding_draft = object()
    failures = composition.validate(slots, mix=mix, skills=skills)
    assert "this role is not given coding questions" in failures


def test_an_objective_question_with_no_valid_payload_is_rejected() -> None:
    slots, mix, skills = _served()
    next(slot for slot in slots if slot.planned_family == composition.FAMILY_OBJECTIVE).payload = {}
    assert any("no valid payload" in failure for failure in composition.validate(slots, mix=mix, skills=skills))


def test_a_plan_that_is_not_the_budgeted_mix_is_rejected() -> None:
    slots, _, skills = _served()
    wrong = budget.Mix(prose=len(slots), coding=0, objective=0)
    assert any("is not the budgeted mix" in failure for failure in composition.validate(slots, mix=wrong, skills=skills))


def test_the_fallback_keeps_an_anchored_evidence_question() -> None:
    slots, mix, skills = _served()
    anchored = next(slot for slot in slots if slot.question_type == types.EVIDENCE_BASED)
    composition.fall_back(slots, [f"prose {slot.index}" for slot in slots], generated=[True] * len(slots))
    assert anchored.question_type == types.EVIDENCE_BASED
    assert anchored.resume_anchor
    assert composition.validate(slots, mix=mix, skills=skills) == []


def test_degrading_a_slot_drops_its_payload_anchor_and_draft() -> None:
    slots, _, _ = _served()
    slot = next(slot for slot in slots if slot.question_type == types.CODING)
    slot.rubric = {"criteria": []}
    composition.degrade_to_prose(slot, "generation_failed", "Tell me.", generated=False)
    assert slot.question_type == types.SHORT_ANSWER
    assert slot.payload == {} and slot.rubric is None and slot.resume_anchor is None
    assert slot.coding_draft is None and slot.generated is False


# ── Payload serialisation, every type ────────────────────────────────────────

_PAYLOADS: dict[str, dict] = {
    types.EVIDENCE_BASED: {
        "sub_type": "project_deep_dive",
        "anchor_source": "employment_history[0]",
        "follow_up_permitted": True,
    },
    types.MCQ_SINGLE: {
        "options": [
            {"id": "a", "text": "One"},
            {"id": "b", "text": "Two"},
            {"id": "c", "text": "Three"},
            {"id": "d", "text": "Four"},
        ],
        "correct_option_id": "b",
    },
    types.MCQ_MULTI: {
        "options": [
            {"id": "a", "text": "One"},
            {"id": "b", "text": "Two"},
            {"id": "c", "text": "Three"},
            {"id": "d", "text": "Four"},
        ],
        "correct_option_ids": ["a", "c"],
        "scoring": "partial",
        "select_count": 2,
    },
    types.FILL_BLANK: {
        "template": "The ___ pattern is used for ___.",
        "blanks": [
            {"index": 0, "accepted": ["observer"], "case_sensitive": False},
            {"index": 1, "accepted": ["events"], "case_sensitive": False},
        ],
    },
    types.CODING: {
        "language": "python",
        "starter_code": "def solve(items):",
        "constraints": "No external libraries.",
        "expected_approach": "Sort, then scan once.",
        "language_options": ["python", "javascript"],
    },
    types.SHORT_ANSWER: {},
}


@pytest.mark.parametrize("question_type", types.QUESTION_TYPES)
def test_every_payload_round_trips_through_its_model(question_type) -> None:
    """Stored as JSONB and read back on every turn, so a payload that does not
    survive the round trip is a question that changes shape between the write
    and the read."""
    payload = _PAYLOADS[question_type]
    once = types.parse_payload(question_type, payload).model_dump()
    twice = types.parse_payload(question_type, once).model_dump()
    assert once == twice


@pytest.mark.parametrize("question_type", types.QUESTION_TYPES)
def test_the_answer_key_never_reaches_the_candidate_view(question_type) -> None:
    view = types.candidate_view(uuid.uuid4(), question_type, _PAYLOADS[question_type])
    blob = repr(view)
    for secret in ("correct_option_id", "correct_option_ids", "accepted", "expected_approach", "sub_type"):
        assert secret not in blob, (question_type, secret)
    # An MCQ's options are still there, in this candidate's own order.
    if question_type in (types.MCQ_SINGLE, types.MCQ_MULTI):
        assert {option["id"] for option in view["options"]} == {
            option["id"] for option in _PAYLOADS[question_type]["options"]
        }


def test_one_candidates_option_order_is_stable_and_two_candidates_differ() -> None:
    """Randomised per candidate (spec 2.2), derived from the question id so the
    order the candidate saw is the order the recruiter's view reconstructs
    without a stored permutation."""
    payload = _PAYLOADS[types.MCQ_SINGLE]
    first = uuid.uuid4()
    once = types.candidate_view(first, types.MCQ_SINGLE, payload)
    again = types.candidate_view(first, types.MCQ_SINGLE, payload)
    assert [option["id"] for option in once["options"]] == [
        option["id"] for option in again["options"]
    ]
    orders = {
        tuple(
            option["id"]
            for option in types.candidate_view(uuid.uuid4(), types.MCQ_SINGLE, payload)["options"]
        )
        for _ in range(40)
    }
    assert len(orders) > 1, "every candidate saw the same option order"


def test_a_fill_blank_view_sizes_the_input_without_revealing_the_answer() -> None:
    view = types.candidate_view(uuid.uuid4(), types.FILL_BLANK, _PAYLOADS[types.FILL_BLANK])
    assert view["template"] == "The ___ pattern is used for ___."
    assert [blank["expected_length"] for blank in view["blanks"]] == [len("observer"), len("events")]
    assert "observer" not in repr(view)


@pytest.mark.parametrize(
    "question_type,answer",
    [
        (types.MCQ_SINGLE, {"selected_option_id": "b"}),
        (types.MCQ_MULTI, {"selected_option_ids": ["a", "c"]}),
        (types.FILL_BLANK, {"values": ["observer", "events"]}),
        (types.CODING, {"language": "python", "code": "def solve(items): return items"}),
        (types.EVIDENCE_BASED, {"text": "I led the migration."}),
        (types.SHORT_ANSWER, {"text": "I led the migration."}),
    ],
)
def test_a_well_formed_answer_parses(question_type, answer) -> None:
    parsed = types.parse_answer(question_type, _PAYLOADS[question_type], answer)
    assert parsed.model_dump()


@pytest.mark.parametrize(
    "question_type,answer",
    [
        # An option id the question never offered is a defect in the client,
        # not a wrong answer.
        (types.MCQ_SINGLE, {"selected_option_id": "z"}),
        (types.MCQ_MULTI, {"selected_option_ids": ["a", "z"]}),
        # One value per blank, or the values do not line up with the blanks.
        (types.FILL_BLANK, {"values": ["observer"]}),
        # A language the question did not permit.
        (types.CODING, {"language": "go", "code": "package main"}),
        # The shape of a different format entirely.
        (types.MCQ_SINGLE, {"text": "b"}),
    ],
)
def test_an_answer_that_does_not_fit_its_question_is_refused(question_type, answer) -> None:
    with pytest.raises(ValueError):
        types.parse_answer(question_type, _PAYLOADS[question_type], answer)


def test_a_payload_that_answers_itself_is_refused() -> None:
    """An MCQ where every option is correct, and a key that is not an option:
    both are questions that cannot be scored, and both are refused at parse
    rather than stored."""
    with pytest.raises(ValueError):
        types.parse_payload(types.MCQ_SINGLE, {**_PAYLOADS[types.MCQ_SINGLE], "correct_option_id": "z"})
    with pytest.raises(ValueError):
        types.parse_payload(
            types.MCQ_MULTI,
            {**_PAYLOADS[types.MCQ_MULTI], "correct_option_ids": ["a", "b", "c", "d"], "select_count": None},
        )
    with pytest.raises(ValueError):
        types.parse_payload(
            types.FILL_BLANK,
            {"template": "no marker here", "blanks": [{"index": 0, "accepted": ["x"]}]},
        )
