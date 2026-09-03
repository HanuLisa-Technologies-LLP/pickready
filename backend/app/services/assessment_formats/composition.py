"""The format mix per role, its validation, and the deterministic fallback.

THE RATIO IS ENFORCED HERE, IN CODE, NOT SUGGESTED IN A PROMPT (spec section
3.2). A model is never asked "how many MCQs should this assessment have"; the
mix is decided by `compose` from the job's grade and role classification, the
result is checked by `validate` against the six rules the specification
states, and an assessment that fails them is never served. "A generated
assessment that is 70% MCQ must be rejected by the system, not served."

WHAT "EVIDENCE" MEANS IN THE MAJORITY RULE
------------------------------------------
Section 1's dichotomy is open-ended questions about the candidate's own work
against the supporting formats (MCQ, fill-in-the-blank, coding), which "cannot
close evidence gaps". A behavioural short-answer question is an open-ended
account of something the candidate did, scored by judgement, and is on the
evidence side of that line; it is the recall-style formats the rule bounds.
So the majority is measured over `types.TEXT_TYPES` and the minority over
`types.SUPPORTING_TYPES`, and the two sets partition the six formats. Reading
"evidence" as `evidence_based` alone would forbid every supporting question on
any job whose behavioural rows are close to half the matrix, which is most of
them under the typical splits, and the feature would be inert by arithmetic.

WHICH SLOTS BECOME SUPPORTING, AND WHY THAT ORDER
-------------------------------------------------
Every matrix item is probed at least once (`ppi._allocate`), and an item that
is probed more than once has spare capacity: its second and later slots become
supporting questions first, so every item keeps one open-ended probe. Only
when the supporting budget is not yet spent do single-slot items give one up,
Nice-to-have before Must-have and from the end of the sequence backwards, so
the criteria the role cannot be performed without keep their evidence
questions longest. Behavioural rows are never supporting: a checkbox cannot
establish a behaviour.

THE FALLBACK IS DETERMINISTIC AND ALWAYS VALID
----------------------------------------------
`fall_back` turns every supporting slot, and every evidence slot the model
could not anchor, back into the text question `ppi.generate_candidate_questions`
already wrote for that item from this candidate's resume. That is the
product's previous behaviour, not invented content, and `validate` accepts the
result by construction: no supporting rows means no supporting-share, mix or
duplicate-type rule can fire, an unanchored row is no longer an evidence row,
and `fit_duration` bounds the total. `tests/test_assessment_formats_composition.py`
asserts that property rather than assuming it.

TIME ALLOCATIONS ARE SCALED TO THE ROLE'S DURATION
--------------------------------------------------
The per-format allocations are suggestions shown to the candidate as guidance,
and a STEM managerial assessment may legitimately hold more questions than the
role's duration divides into at the default allocations. `fit_duration` scales
every allocation by the same factor so the sum fits, rather than dropping
questions (which would grade an item nobody was asked about) or refusing the
assessment (which would strand the candidate). The shares between formats are
preserved exactly, which is what the majority rule reads.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types

__all__ = [
    "Slot",
    "compose",
    "fall_back",
    "fit_duration",
    "revert_to_text",
    "supporting_types_for",
    "validate",
]


@dataclass
class Slot:
    """One question's format decision, before and after the model fills it.

    Mutable on purpose: `compose` decides the format, `generation` fills the
    prompt, the payload and the anchor, and `fall_back` may change the format
    again. The row written to `candidate_questions` is built from the final
    state, so the persisted row never carries an intermediate one.
    """

    index: int
    competency_id: uuid.UUID
    category: str
    question_type: str
    weight: float
    time_allocation_seconds: int
    prompt: str = ""
    resume_anchor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    rubric: dict[str, Any] | None = None


def _config() -> format_config.FormatConfig:
    return format_config.get_config()


def supporting_types_for(grade: str, role_classification: str | None) -> tuple[str, ...]:
    """The supporting formats this role may use, in the order slots cycle
    through them.

    Coding only for a STEM role (rule 4: "non-technical roles must not"). A
    senior grade drops the single-correct MCQ, the most recall-shaped format,
    which is the direction rule 4 states: "Senior roles skew further toward
    evidence and away from recall-style MCQs". The senior share bound applies
    on top of that.
    """
    stem = role_classification == "STEM"
    if grade in format_config.SENIOR_GRADES:
        return (
            (types.CODING, types.MCQ_MULTI, types.FILL_BLANK)
            if stem
            else (types.MCQ_MULTI, types.FILL_BLANK)
        )
    return (
        (types.MCQ_SINGLE, types.CODING, types.FILL_BLANK, types.MCQ_MULTI)
        if stem
        else (types.MCQ_SINGLE, types.FILL_BLANK, types.MCQ_MULTI)
    )


def _text_type_for(category: str) -> str:
    """The open-ended format a row of this category falls back to."""
    return types.SHORT_ANSWER if category == "behavioural" else types.EVIDENCE_BASED


def _apply_type(slot: Slot, question_type: str) -> None:
    conf = _config()
    slot.question_type = question_type
    slot.weight = conf.weight_by_type[question_type]
    slot.time_allocation_seconds = conf.time_seconds_by_type[question_type]


def _supporting_candidates(slots: Sequence[Slot]) -> list[Slot]:
    """The rubric-scored slots that may become supporting, in priority order."""
    seen: set[uuid.UUID] = set()
    repeats: list[Slot] = []
    singles: list[Slot] = []
    for slot in slots:
        if slot.category == "behavioural":
            continue
        if slot.competency_id in seen:
            repeats.append(slot)
        else:
            seen.add(slot.competency_id)
            singles.append(slot)
    # Repeats in sequence order, then single-slot items from the end
    # backwards, Nice-to-have before Must-have.
    nice = [slot for slot in reversed(singles) if slot.category == "nice_to_have"]
    must = [slot for slot in reversed(singles) if slot.category != "nice_to_have"]
    return repeats + nice + must


def _shares(slots: Sequence[Slot]) -> tuple[float, float]:
    """(share of weight, share of time) held by the open-ended formats."""
    total_weight = sum(slot.weight for slot in slots)
    total_time = sum(slot.time_allocation_seconds for slot in slots)
    if not total_weight or not total_time:
        return 0.0, 0.0
    text = [slot for slot in slots if slot.question_type in types.TEXT_TYPES]
    return (
        sum(slot.weight for slot in text) / total_weight,
        sum(slot.time_allocation_seconds for slot in text) / total_time,
    )


def compose(
    allocation: Sequence[Any],
    *,
    grade: str,
    role_classification: str | None,
) -> list[Slot]:
    """Decide every row's format for this job, deterministically.

    `allocation` is `ppi._allocate`'s output: one competency row per question,
    in ask order. Two candidates on one job get the same format in the same
    position, which is what keeps their reports comparable; what varies per
    candidate is the content the model writes into each slot.
    """
    conf = _config()
    slots: list[Slot] = []
    for index, competency in enumerate(allocation):
        slot = Slot(
            index=index,
            competency_id=competency.id,
            category=str(competency.category),
            question_type=types.SHORT_ANSWER,
            weight=conf.weight_by_type[types.SHORT_ANSWER],
            time_allocation_seconds=conf.time_seconds_by_type[types.SHORT_ANSWER],
        )
        _apply_type(slot, _text_type_for(slot.category))
        slots.append(slot)

    budget = math.floor(len(slots) * conf.supporting_share_for(grade))
    cycle = supporting_types_for(grade, role_classification)
    chosen: list[Slot] = []
    taken: set[tuple[uuid.UUID, str]] = set()
    for slot in _supporting_candidates(slots):
        if len(chosen) >= budget:
            break
        # Duplicate prevention (rule 5): never two structured questions of the
        # same type on one item. Try the cycle from this slot's position and
        # leave the slot open-ended if every type is already taken.
        start = len(chosen) % len(cycle)
        for offset in range(len(cycle)):
            question_type = cycle[(start + offset) % len(cycle)]
            key = (slot.competency_id, question_type)
            if key in taken:
                continue
            taken.add(key)
            _apply_type(slot, question_type)
            chosen.append(slot)
            break

    # The majority rule, re-checked on the actual weights and times rather
    # than assumed from the count bound, because the coding allocation is
    # large enough that one of them can move the time share on a short
    # assessment. Revert the most recently chosen slot until it holds.
    while chosen:
        weight_share, time_share = _shares(slots)
        if weight_share >= conf.evidence_min_share and time_share >= conf.evidence_min_share:
            break
        reverted = chosen.pop()
        _apply_type(reverted, _text_type_for(reverted.category))

    fit_duration(slots, grade)
    return slots


def fit_duration(slots: Sequence[Slot], grade: str) -> None:
    """Scale the suggested times so their sum fits the role's duration."""
    duration = _config().duration_for(grade)
    total = sum(slot.time_allocation_seconds for slot in slots)
    if total <= duration or total <= 0:
        return
    factor = duration / total
    for slot in slots:
        slot.time_allocation_seconds = math.floor(slot.time_allocation_seconds * factor)


def _anchor_key(anchor: str | None) -> str:
    return " ".join(str(anchor or "").split()).casefold()


def revert_to_text(slot: Slot) -> None:
    """Turn one slot back into the plain text question already written for
    its item: the product's previous behaviour for every row.

    Always SHORT_ANSWER, never EVIDENCE_BASED. A reverted slot has no anchor
    (the anchor is what the model could not supply, or what a supporting slot
    never had), and an evidence row without an anchor is exactly the row rule
    2 forbids. Its payload and rubric go with the format they belonged to.
    """
    _apply_type(slot, types.SHORT_ANSWER)
    slot.payload = {}
    slot.rubric = None
    slot.resume_anchor = None


def validate(
    rows: Iterable[Any],
    grade: str,
    role_classification: str | None,
) -> list[str]:
    """The six composition rules (spec section 3.2), as failures in words.

    Duck-typed over anything carrying `category`, `question_type`,
    `resume_anchor`, `weight`, `time_allocation_seconds` and `competency_id`,
    so it reads a `Slot` before persistence and a `CandidateQuestion` row after
    it. An empty list means the assessment may be served.
    """
    conf = _config()
    items = list(rows)
    failures: list[str] = []
    if not items:
        return ["the assessment has no questions"]

    total = len(items)
    supporting = [item for item in items if item.question_type in types.SUPPORTING_TYPES]
    text_weight = sum(float(item.weight) for item in items if item.question_type in types.TEXT_TYPES)
    text_time = sum(
        int(item.time_allocation_seconds) for item in items if item.question_type in types.TEXT_TYPES
    )
    total_weight = sum(float(item.weight) for item in items)
    total_time = sum(int(item.time_allocation_seconds) for item in items)

    # Rule 1: evidence questions dominate, in weight AND in time.
    #
    # ASSUMPTION (assessment-spec-doc section 1 and rule 1). The specification
    # splits the assessment two ways: "Evidence-based questions" carry the
    # majority of time and weight, and "Supporting formats (MCQ, fill-blank,
    # coding)" are the minority. It never says which side SHORT_ANSWER sits on.
    # It is read here as an open-ended probe on the evidence side, because that
    # is the format the product already asked every behavioural competency in
    # before this change (section 2.6 says short answer is retained as-is), and
    # because the contrast section 1 actually draws is open-ended probing
    # against recall-style formats.
    #
    # THE CONSEQUENCE, MEASURED 2026-09-03 across all four grades on both role
    # classifications: EVIDENCE_BASED alone is 35.9% to 63.7% of weight, so on
    # a managerial STEM role it is a minority of the whole assessment while
    # this rule passes. That is not supporting formats taking over: it is the
    # behavioural third of the Tatva matrix, which is judgement-scored prose by
    # product decision and cannot be anchored to a resume claim at all
    # (rule 2 requires an anchor, and there is nothing on a resume to quote for
    # "judgement under pressure"). Requiring EVIDENCE_BASED to exceed half of
    # everything would therefore be a requirement on the behavioural
    # dimension's size, not on the format mix.
    #
    # What IS unambiguous is enforced immediately below, and it is the rule
    # that actually protects the principle.
    if total_weight <= 0 or text_weight / total_weight < conf.evidence_min_share:
        failures.append(
            "evidence questions must carry the majority of the assessment's weight"
        )
    if total_time <= 0 or text_time / total_time < conf.evidence_min_share:
        failures.append(
            "evidence questions must carry the majority of the assessment's time"
        )

    # Rule 1b: evidence questions dominate the part that CAN be anchored.
    #
    # Every Must-have and Nice-to-have slot is resume-anchorable, so on that
    # part the specification's sentence has exactly one reading and no
    # behavioural dimension to argue about. Without this, rule 1 above could be
    # satisfied entirely by behavioural short answers while the anchorable half
    # filled up with MCQs, which is precisely the failure section 1 calls a bug
    # rather than a configuration choice. Measured on the same run, the
    # composer delivers 73.7% to 90.9% here, so this is a floor with real
    # headroom rather than a restatement of what the composer happens to do.
    #
    # The same `evidence_min_share` bound, applied where it is unambiguous: a
    # second constant would be a second definition of "majority".
    #
    # SCOPED TO AN ASSESSMENT THAT ACTUALLY USES A SUPPORTING FORMAT, because
    # the failure being prevented is the anchorable half filling up with them.
    # `fall_back` reverts every supporting slot to the plain text question
    # already written for its item, which is the product's behaviour from
    # before formats existed and is the correct thing to serve when generation
    # was unavailable. Firing on that would refuse the degradation path and
    # leave a candidate with no assessment at all, which is a worse outcome
    # than the one this rule exists to prevent.
    rubric_scored = [item for item in items if item.category != "behavioural"]
    if rubric_scored and any(
        item.question_type in types.SUPPORTING_TYPES for item in rubric_scored
    ):
        anchorable_weight = sum(float(item.weight) for item in rubric_scored)
        evidence_weight = sum(
            float(item.weight)
            for item in rubric_scored
            if item.question_type == types.EVIDENCE_BASED
        )
        if (
            anchorable_weight <= 0
            or evidence_weight / anchorable_weight < conf.evidence_min_share
        ):
            failures.append(
                "evidence questions must carry the majority of the Must-have and "
                "Nice-to-have weight, which is the part of the assessment that "
                "can be anchored to the candidate's own resume"
            )

    # Rule 3: the supporting formats are a bounded minority of the count.
    share = conf.supporting_share_for(grade)
    if len(supporting) > math.floor(total * share):
        failures.append(
            "supporting formats exceed their share of the question count for this grade"
        )
    if supporting and len(supporting) * 2 >= total:
        failures.append("supporting formats must be the minority of the assessment")

    # Rule 4: role-appropriate mix.
    permitted = set(supporting_types_for(grade, role_classification))
    for item in supporting:
        if item.question_type not in permitted:
            failures.append(
                f"format {item.question_type} is not permitted for this role and grade"
            )
            break
    if any(
        item.category == "behavioural" and item.question_type != types.SHORT_ANSWER
        for item in items
    ):
        failures.append("a behavioural competency is answered in prose only")

    # Rule 2: every evidence question is anchored to a quotable resume item.
    unanchored = [
        item
        for item in items
        if item.question_type == types.EVIDENCE_BASED
        and len(_anchor_key(item.resume_anchor)) < conf.anchor_min_chars
    ]
    if unanchored:
        failures.append(
            f"{len(unanchored)} evidence question(s) are not anchored to a resume item"
        )

    # Rule 5: duplicate prevention, on the resume item and on the (item,
    # structured type) pair.
    anchors: set[str] = set()
    for item in items:
        key = _anchor_key(item.resume_anchor)
        if not key:
            continue
        if key in anchors:
            failures.append("two questions probe the same resume item")
            break
        anchors.add(key)
    pairs: set[tuple[Any, str]] = set()
    for item in supporting:
        pair = (item.competency_id, item.question_type)
        if pair in pairs:
            failures.append("two questions of the same format probe the same competency")
            break
        pairs.add(pair)

    # Rule 6: total length bounded.
    if total_time > conf.duration_for(grade):
        failures.append("the assessment does not fit the role's duration")

    return failures


def fall_back(slots: Sequence[Slot], grade: str) -> list[Slot]:
    """Turn every slot the model could not fill soundly back into the text
    question already written for its item. Deterministic; calls no model.

    Returns the same slot objects, mutated, so the caller's prompts written
    by `ppi.generate_candidate_questions` before composition are what a
    reverted slot asks. A reverted evidence slot loses its anchor: a stored
    anchor on a short-answer row would claim a provenance the row does not
    have.
    """
    conf = _config()
    seen_anchors: set[str] = set()
    for slot in slots:
        if slot.question_type in types.SUPPORTING_TYPES:
            revert_to_text(slot)
        if slot.category == "behavioural" and slot.question_type != types.SHORT_ANSWER:
            revert_to_text(slot)
        if slot.question_type == types.EVIDENCE_BASED:
            key = _anchor_key(slot.resume_anchor)
            if len(key) < conf.anchor_min_chars or key in seen_anchors:
                revert_to_text(slot)
            else:
                seen_anchors.add(key)
    fit_duration(slots, grade)
    return list(slots)
