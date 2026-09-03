"""Composition: the ratio is enforced in code, and an invalid mix is never served.

    "The AI's assessment-composition logic must enforce this ratio. If a
     generated assessment is mostly MCQs, that is a bug, not a configuration
     choice." (spec section 1)

    "Implement composition validation. After the AI generates an assessment,
     validate it against these rules before serving it to the candidate. If
     validation fails, regenerate. A generated assessment that is 70% MCQ must
     be rejected by the system, not served." (spec section 3.2)

So the assertions here are of three kinds, and the third is the one that
actually protects the candidate:

  1. `compose` produces a valid mix for every role and grade the product has.
  2. `validate` REJECTS each of the six rules being broken, one at a time. A
     validator is only worth having if each rule fails on its own; one that
     passed everything would satisfy point 1 forever.
  3. Whatever the model does or fails to do, what is SERVED is valid:
     regeneration is attempted, and the deterministic fallback that follows it
     is asserted to satisfy the validator by construction rather than by luck.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import ppi
from app.services.assessment_formats import composition
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types

GRADES = ("non_managerial", "managerial", "leadership", "cxo")
CLASSIFICATIONS = ("STEM", "NON_STEM", None)


def _competency(category: str, ordinal: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        category=category,
        name=f"{category}-{ordinal}",
        description=f"What {category}-{ordinal} measures.",
        ordinal=ordinal,
    )


def _matrix(per_aspect: int = 5) -> list[SimpleNamespace]:
    return [
        _competency(category, index + 1)
        for category in ppi.CATEGORIES
        for index in range(per_aspect)
    ]


def _allocation(size: int, per_aspect: int = 5) -> list[SimpleNamespace]:
    """What `ppi._allocate` hands the composer: one row per question, every
    item probed at least once and the remainder repeating items."""
    return ppi._allocate(_matrix(per_aspect), size, "non_managerial")


def _anchor(slot: composition.Slot) -> None:
    slot.resume_anchor = f"led the {slot.index} migration at a previous employer"


def _anchor_all(slots: list[composition.Slot]) -> list[composition.Slot]:
    for slot in slots:
        if slot.question_type == types.EVIDENCE_BASED:
            _anchor(slot)
    return slots


# ── What `compose` produces ──────────────────────────────────────────────────


@pytest.mark.parametrize("grade", GRADES)
@pytest.mark.parametrize("classification", CLASSIFICATIONS)
@pytest.mark.parametrize("size", (12, 18, 25))
def test_every_composed_assessment_passes_its_own_validator(grade, classification, size) -> None:
    """The property that matters: for every role this product can post, the
    mix the composer decides is one the validator accepts."""
    slots = composition.compose(_allocation(size), grade=grade, role_classification=classification)
    assert len(slots) == size
    _anchor_all(slots)
    assert composition.validate(slots, grade, classification) == []


@pytest.mark.parametrize("grade", GRADES)
def test_evidence_carries_the_majority_of_weight_and_time(grade) -> None:
    conf = format_config.get_config()
    slots = composition.compose(_allocation(18), grade=grade, role_classification="STEM")
    text = [slot for slot in slots if slot.question_type in types.TEXT_TYPES]
    weight = sum(slot.weight for slot in text) / sum(slot.weight for slot in slots)
    time = sum(slot.time_allocation_seconds for slot in text) / sum(
        slot.time_allocation_seconds for slot in slots
    )
    assert weight >= conf.evidence_min_share
    assert time >= conf.evidence_min_share


@pytest.mark.parametrize("grade", GRADES)
@pytest.mark.parametrize("classification", CLASSIFICATIONS)
def test_evidence_dominates_the_part_that_can_be_anchored(grade, classification) -> None:
    """Rule 1b, and the reason it exists beside rule 1.

    Rule 1 counts SHORT_ANSWER on the evidence side, which is argued at the
    point it is implemented. The consequence is that a large behavioural
    dimension can leave EVIDENCE_BASED a minority of the whole assessment
    while rule 1 passes: measured 2026-09-03, a managerial STEM role is 35.9%
    evidence by weight.

    On the Must-have and Nice-to-have slots there is no such argument. Every
    one of them can be anchored to a resume claim, so the specification's
    sentence has one reading, and this is where an assessment quietly filling
    up with MCQs would show. Measured on the same run, the composer delivers
    73.7% to 90.9% here, so the floor has real headroom.
    """
    conf = format_config.get_config()
    slots = composition.compose(
        _allocation(18), grade=grade, role_classification=classification
    )
    rubric_scored = [slot for slot in slots if slot.category != "behavioural"]
    assert rubric_scored, "the matrix must have something anchorable in it"
    evidence = [
        slot for slot in rubric_scored if slot.question_type == types.EVIDENCE_BASED
    ]
    share = sum(slot.weight for slot in evidence) / sum(
        slot.weight for slot in rubric_scored
    )
    assert share >= conf.evidence_min_share, (
        f"{grade}/{classification}: evidence carries {share:.1%} of the "
        "anchorable weight"
    )


def test_an_anchorable_half_filled_with_mcqs_is_rejected() -> None:
    """The failure rule 1b catches and rule 1 does not.

    Behavioural short answers carry the whole assessment past rule 1 while
    every Must-have and Nice-to-have slot has become a supporting format. That
    is section 1's "mostly MCQs is a bug, not a configuration choice", hidden
    behind a dimension that is prose by product decision.
    """
    slots = composition.compose(_allocation(18), grade="managerial", role_classification="STEM")
    for slot in slots:
        if slot.category != "behavioural":
            composition._apply_type(slot, types.MCQ_SINGLE)
            slot.resume_anchor = None
    failures = composition.validate(slots, grade="managerial", role_classification="STEM")
    assert any("can be anchored" in failure for failure in failures), failures


@pytest.mark.parametrize("grade", GRADES)
def test_the_supporting_formats_are_a_bounded_minority_of_the_count(grade) -> None:
    conf = format_config.get_config()
    slots = composition.compose(_allocation(20), grade=grade, role_classification="STEM")
    supporting = [slot for slot in slots if slot.question_type in types.SUPPORTING_TYPES]
    assert len(supporting) <= 20 * conf.supporting_share_for(grade)
    assert len(supporting) * 2 < len(slots)


def test_a_senior_role_skews_further_toward_evidence() -> None:
    """Rule 4: "Senior roles skew further toward evidence and away from
    recall-style MCQs"."""
    junior = composition.compose(_allocation(20), grade="non_managerial", role_classification="STEM")
    senior = composition.compose(_allocation(20), grade="leadership", role_classification="STEM")
    junior_supporting = [s for s in junior if s.question_type in types.SUPPORTING_TYPES]
    senior_supporting = [s for s in senior if s.question_type in types.SUPPORTING_TYPES]
    assert len(senior_supporting) < len(junior_supporting)
    # And the most recall-shaped format is not offered at all at a senior grade.
    assert types.MCQ_SINGLE not in {slot.question_type for slot in senior_supporting}


def test_only_a_stem_role_is_asked_to_write_code() -> None:
    """Rule 4: "Engineering roles may include coding; non-technical roles must
    not"."""
    for grade in GRADES:
        assert types.CODING in composition.supporting_types_for(grade, "STEM")
        for classification in ("NON_STEM", None, "unknown"):
            assert types.CODING not in composition.supporting_types_for(grade, classification)
            slots = composition.compose(
                _allocation(20), grade=grade, role_classification=classification
            )
            assert types.CODING not in {slot.question_type for slot in slots}


def test_a_behavioural_competency_is_always_answered_in_prose() -> None:
    """A checkbox cannot establish a behaviour, and a behavioural item is
    graded by judgement across everything said about it."""
    for grade in GRADES:
        slots = composition.compose(_allocation(25), grade=grade, role_classification="STEM")
        behavioural = [slot for slot in slots if slot.category == ppi.CATEGORY_BEHAVIOURAL]
        assert behavioural
        assert {slot.question_type for slot in behavioural} == {types.SHORT_ANSWER}


def test_no_item_is_asked_the_same_structured_format_twice() -> None:
    slots = composition.compose(_allocation(25), grade="non_managerial", role_classification="STEM")
    pairs = [
        (slot.competency_id, slot.question_type)
        for slot in slots
        if slot.question_type in types.SUPPORTING_TYPES
    ]
    assert len(pairs) == len(set(pairs))


def test_every_item_keeps_at_least_one_open_ended_probe() -> None:
    """An item probed once must not have its only question turned into a
    checkbox: the report grades that item, and an MCQ cannot establish it."""
    allocation = _allocation(18)
    slots = composition.compose(allocation, grade="non_managerial", role_classification="STEM")
    by_item: dict[uuid.UUID, list[str]] = {}
    for slot in slots:
        by_item.setdefault(slot.competency_id, []).append(slot.question_type)
    for item_id, formats in by_item.items():
        if len(formats) == 1 and formats[0] in types.SUPPORTING_TYPES:
            # Permitted only where the budget forced it, and never for the
            # aspect the role cannot be performed without.
            category = next(slot.category for slot in slots if slot.competency_id == item_id)
            assert category == ppi.CATEGORY_NICE_TO_HAVE, formats


def test_the_assessment_fits_the_role_duration() -> None:
    conf = format_config.get_config()
    for grade in GRADES:
        slots = composition.compose(_allocation(25), grade=grade, role_classification="STEM")
        assert sum(slot.time_allocation_seconds for slot in slots) <= conf.duration_for(grade)


def test_the_format_mix_is_the_same_for_two_candidates_on_one_job() -> None:
    """The comparability guarantee. What varies per candidate is the CONTENT
    the model writes into each slot, never which formats there are."""
    allocation = _allocation(18)
    first = composition.compose(allocation, grade="managerial", role_classification="STEM")
    second = composition.compose(allocation, grade="managerial", role_classification="STEM")
    assert [slot.question_type for slot in first] == [slot.question_type for slot in second]
    assert [slot.time_allocation_seconds for slot in first] == [
        slot.time_allocation_seconds for slot in second
    ]


# ── What `validate` refuses, one rule at a time ──────────────────────────────


def _valid_slots(size: int = 18) -> list[composition.Slot]:
    return _anchor_all(
        composition.compose(_allocation(size), grade="non_managerial", role_classification="STEM")
    )


def _hand_built(counts: dict[str, int]) -> list[composition.Slot]:
    """An assessment with exactly this format mix, weighted and timed from
    the config. Built by hand rather than by mutating a composed one, so the
    arithmetic each rule reads is visible in the test."""
    conf = format_config.get_config()
    slots: list[composition.Slot] = []
    for question_type, count in counts.items():
        for _ in range(count):
            index = len(slots)
            slot = composition.Slot(
                index=index,
                competency_id=uuid.uuid4(),
                category=(
                    ppi.CATEGORY_MUST_HAVE
                    if question_type != types.SHORT_ANSWER
                    else ppi.CATEGORY_BEHAVIOURAL
                ),
                question_type=question_type,
                weight=conf.weight_by_type[question_type],
                time_allocation_seconds=conf.time_seconds_by_type[question_type],
            )
            if question_type == types.EVIDENCE_BASED:
                _anchor(slot)
            slots.append(slot)
    return slots


def test_an_assessment_the_supporting_formats_dominate_is_rejected() -> None:
    """THE HEADLINE RULE, in the form that breaks every measure of it at once:
    five coding questions against four evidence questions carries most of the
    weight AND most of the time on the supporting side."""
    slots = _hand_built({types.EVIDENCE_BASED: 4, types.CODING: 5})
    failures = composition.validate(slots, "non_managerial", "STEM")
    assert any("majority of the assessment's weight" in reason for reason in failures)
    assert any("majority of the assessment's time" in reason for reason in failures)
    assert any("share of the question count" in reason for reason in failures)


def test_a_mostly_mcq_assessment_is_rejected_even_though_it_is_quick() -> None:
    """WHY THE COUNT RULE EXISTS BESIDE THE WEIGHT AND TIME RULES.

    Eleven MCQs against four evidence questions is the "70% MCQ" assessment
    the specification says must be rejected -- and because an MCQ is quick,
    the four evidence questions still carry most of the assessment's MINUTES.
    A validator that only measured time and weight would serve it. The count
    rule is what catches it, and this test is the reason that rule is not
    redundant.
    """
    slots = _hand_built({types.EVIDENCE_BASED: 4, types.MCQ_SINGLE: 11})
    text_time = sum(
        slot.time_allocation_seconds
        for slot in slots
        if slot.question_type in types.TEXT_TYPES
    )
    total_time = sum(slot.time_allocation_seconds for slot in slots)
    assert text_time / total_time >= format_config.get_config().evidence_min_share, (
        "this fixture is meant to pass the time rule and fail the count rule"
    )
    failures = composition.validate(slots, "non_managerial", "STEM")
    assert any("share of the question count" in reason for reason in failures)
    assert any("minority of the assessment" in reason for reason in failures)


def test_an_unanchored_evidence_question_is_rejected() -> None:
    """Rule 2. An evidence question with no anchor is the generic "tell me
    about a challenge" the format exists to forbid."""
    slots = _valid_slots()
    evidence = next(slot for slot in slots if slot.question_type == types.EVIDENCE_BASED)
    evidence.resume_anchor = None
    assert any("not anchored" in reason for reason in composition.validate(slots, "non_managerial", "STEM"))
    # And a one-word "anchor" is not an anchor.
    evidence.resume_anchor = "Kafka"
    assert any("not anchored" in reason for reason in composition.validate(slots, "non_managerial", "STEM"))


def test_two_questions_probing_the_same_resume_item_are_rejected() -> None:
    """Rule 5. The candidate would visibly be asked about one project twice."""
    slots = _valid_slots()
    evidence = [slot for slot in slots if slot.question_type == types.EVIDENCE_BASED]
    evidence[1].resume_anchor = evidence[0].resume_anchor
    assert any(
        "same resume item" in reason for reason in composition.validate(slots, "non_managerial", "STEM")
    )
    # Whitespace and case are not a way around it.
    evidence[1].resume_anchor = f"  {evidence[0].resume_anchor.upper()}  "
    assert any(
        "same resume item" in reason for reason in composition.validate(slots, "non_managerial", "STEM")
    )


def test_two_structured_questions_of_one_format_on_one_item_are_rejected() -> None:
    conf = format_config.get_config()
    slots = _valid_slots(25)
    supporting = [slot for slot in slots if slot.question_type in types.SUPPORTING_TYPES]
    assert len(supporting) >= 2
    supporting[1].competency_id = supporting[0].competency_id
    supporting[1].question_type = supporting[0].question_type
    supporting[1].weight = conf.weight_by_type[supporting[0].question_type]
    assert any(
        "same format probe the same competency" in reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
    )


def test_a_coding_question_on_a_non_technical_role_is_rejected() -> None:
    conf = format_config.get_config()
    slots = _valid_slots()
    slots[0].question_type = types.CODING
    slots[0].weight = conf.weight_by_type[types.CODING]
    slots[0].time_allocation_seconds = conf.time_seconds_by_type[types.CODING]
    slots[0].resume_anchor = None
    assert any(
        "not permitted for this role" in reason
        for reason in composition.validate(slots, "non_managerial", "NON_STEM")
    )


def test_a_structured_behavioural_question_is_rejected() -> None:
    slots = _valid_slots()
    behavioural = next(slot for slot in slots if slot.category == ppi.CATEGORY_BEHAVIOURAL)
    behavioural.question_type = types.MCQ_SINGLE
    behavioural.resume_anchor = None
    assert any(
        "answered in prose only" in reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
    )


def test_an_assessment_longer_than_the_role_duration_is_rejected() -> None:
    conf = format_config.get_config()
    slots = _valid_slots()
    slots[0].time_allocation_seconds = conf.duration_for("non_managerial") + 1
    assert any(
        "does not fit the role's duration" in reason
        for reason in composition.validate(slots, "non_managerial", "STEM")
    )


def test_an_empty_assessment_is_rejected() -> None:
    assert composition.validate([], "non_managerial", "STEM") == ["the assessment has no questions"]


# ── The deterministic fallback ───────────────────────────────────────────────


@pytest.mark.parametrize("grade", GRADES)
@pytest.mark.parametrize("classification", ("STEM", "NON_STEM"))
@pytest.mark.parametrize("size", (12, 20, 25))
def test_the_fallback_is_always_valid(grade, classification, size) -> None:
    """WHAT IS SERVED IS ALWAYS VALID. The fallback is the last thing between
    a failed generation and a candidate, so its validity is asserted rather
    than argued: no supporting rows, no unanchored evidence rows, nothing for
    a rule to catch.
    """
    slots = composition.compose(_allocation(size), grade=grade, role_classification=classification)
    # Nothing was anchored and nothing was filled: the worst case, a model
    # that produced nothing usable at all.
    composition.fall_back(slots, grade)
    assert {slot.question_type for slot in slots} == {types.SHORT_ANSWER}
    assert all(slot.resume_anchor is None for slot in slots)
    assert all(slot.payload == {} for slot in slots)
    assert composition.validate(slots, grade, classification) == []


def test_the_fallback_keeps_an_anchored_evidence_question() -> None:
    """It reverts what the model could not do, not what it did. An anchored
    evidence question is the format the whole assessment is built around."""
    slots = composition.compose(_allocation(18), grade="non_managerial", role_classification="STEM")
    _anchor_all(slots)
    composition.fall_back(slots, "non_managerial")
    kept = [slot for slot in slots if slot.question_type == types.EVIDENCE_BASED]
    assert kept, "an anchored evidence question was thrown away"
    assert all(slot.resume_anchor for slot in kept)
    assert composition.validate(slots, "non_managerial", "STEM") == []


def test_reverting_a_slot_drops_its_payload_and_its_anchor() -> None:
    """A short-answer row carrying an anchor would claim a provenance it does
    not have, and one carrying a payload would be a question with an answer
    key nobody reads."""
    slots = composition.compose(_allocation(18), grade="non_managerial", role_classification="STEM")
    slot = next(s for s in slots if s.question_type in types.SUPPORTING_TYPES)
    slot.payload = {"options": []}
    slot.rubric = {"misconceptions": {}}
    slot.resume_anchor = "something"
    composition.revert_to_text(slot)
    assert slot.question_type == types.SHORT_ANSWER
    assert slot.payload == {} and slot.rubric is None and slot.resume_anchor is None


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
