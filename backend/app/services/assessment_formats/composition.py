"""Which skill each question probes, in which format, and the check before serving.

DETERMINISTIC PER JOB. `allocate` and `compose` read the contract's skills,
the budget and the mix (`assessment_questions.budget`) and nothing else, so two
candidates on one job get the same skill and the same format in the same
position. What varies per candidate is the CONTENT the writers put into each
slot. That is what keeps two reports on one job comparable.

WHICH SKILL, AND HOW OFTEN (`allocate`)
---------------------------------------
Every skill is asked at least once, in bucket order (Must-have, Nice-to-have,
Behavioural) and by the hidden priority inside each bucket. A budget above the
skill count spends its extra slots round-robin over the Must-have then the
Nice-to-have skills, by priority. A behavioural skill joins that rotation only
for a non-STEM role, where the behavioural half is most of what the role is;
on a STEM role the extra slots go to the skills a coding or objective question
can probe.

WHICH FORMAT (`compose`)
------------------------
Every slot starts as prose: `evidence_based` for a Must-have or Nice-to-have
skill (anchored to the candidate's resume), `short_answer` for a behavioural
one, which is judged and cannot be anchored. Then exactly `mix.coding` slots
become coding and `mix.objective` slots become objective (multiple choice or
fill-in-the-blank), chosen from the Must-have and Nice-to-have slots in this
order: a skill's SECOND and later slots first, so every skill keeps its prose
probe; then single-slot skills from the lowest priority up, Nice-to-have
before Must-have, so the criteria the role cannot be done without keep their
open-ended question longest. A (skill, format) pair repeats only when there
are fewer Must-have and Nice-to-have slots than structured questions. A
behavioural skill is NEVER structured: a checkbox cannot establish a
behaviour.

A SLOT THAT CANNOT BE FILLED BECOMES PROSE, AND SAYS SO
-------------------------------------------------------
`degrade_to_prose` turns a coding or objective slot the writers could not fill
soundly into the prose question already written for its skill and records the
reason on the slot (`degradation`). The generator copies every degradation
into the conversation's composition record, so "fewer coding questions than
the mix" is always an entry with a reason and never a silent shortfall.
`validate` counts each slot under the family it was PLANNED as, which is what
makes a degradation the only way a served count may differ from the plan.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from itertools import cycle
from typing import Any, Iterable, Sequence

from app.services.assessment_contract import (
    BUCKET_BEHAVIOURAL,
    BUCKET_MUST_HAVE,
    BUCKET_NICE_TO_HAVE,
    BUCKETS,
    ContractSkill,
)
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types
from app.services.assessment_questions.budget import Mix

__all__ = [
    "FAMILY_CODING",
    "FAMILY_OBJECTIVE",
    "FAMILY_PROSE",
    "FAMILIES",
    "Slot",
    "allocate",
    "compose",
    "degrade_to_prose",
    "family_of",
    "fall_back",
    "objective_types_for",
    "served_mix",
    "validate",
]

FAMILY_PROSE = "prose"
FAMILY_CODING = "coding"
FAMILY_OBJECTIVE = "objective"
FAMILIES: tuple[str, ...] = (FAMILY_PROSE, FAMILY_CODING, FAMILY_OBJECTIVE)

_FAMILY_OF_TYPE: dict[str, str] = {
    types.EVIDENCE_BASED: FAMILY_PROSE,
    types.SHORT_ANSWER: FAMILY_PROSE,
    types.CODING: FAMILY_CODING,
    types.MCQ_SINGLE: FAMILY_OBJECTIVE,
    types.MCQ_MULTI: FAMILY_OBJECTIVE,
    types.FILL_BLANK: FAMILY_OBJECTIVE,
}

def family_of(question_type: str) -> str:
    return _FAMILY_OF_TYPE[question_type]


@dataclass
class Slot:
    """One question's plan, before and after the writers fill it.

    Mutable on purpose: `compose` decides the format, the writers fill the
    prompt, payload, rubric and anchor, and `degrade_to_prose` may change the
    format again. The row written to `candidate_questions` is built from the
    final state, so a persisted row never carries an intermediate one.

    `planned_family` never changes after `compose`: it is what `validate`
    counts, so the served mix can only differ from the plan through a recorded
    `degradation`.
    """

    index: int
    competency_id: uuid.UUID
    category: str
    skill_name: str
    question_type: str
    planned_family: str
    weight: float
    time_allocation_seconds: int
    prompt: str = ""
    #: True once a model wrote `prompt` (and, for a structured slot, its
    #: payload). A deterministic fallback prompt leaves it False, and the row
    #: then carries no `generated_at`: template text is never recorded as
    #: generation.
    generated: bool = False
    resume_anchor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    rubric: dict[str, Any] | None = None
    #: Why a planned coding or objective slot is served as prose. None while
    #: the slot is what was planned.
    degradation: str | None = None
    #: The validated coding draft this slot will persist, when it is coding.
    coding_draft: Any = None


def _bucket_order(skill: ContractSkill) -> tuple[int, int, str]:
    return (BUCKETS.index(skill.bucket), skill.priority, skill.name)


def allocate(
    skills: Sequence[ContractSkill], total: int, *, stem: bool
) -> list[ContractSkill]:
    """One skill per question, `total` of them, every skill at least once.

    Raises when the budget is below the skill count: `question_budget` never
    produces that, and truncating would grade a skill nobody was asked about.
    """
    ordered = sorted(skills, key=_bucket_order)
    if total < len(ordered):
        raise ValueError(
            f"a budget of {total} cannot ask each of {len(ordered)} skills once"
        )
    plan = list(ordered)
    extra_buckets = (
        (BUCKET_MUST_HAVE, BUCKET_NICE_TO_HAVE)
        if stem
        else (BUCKET_MUST_HAVE, BUCKET_NICE_TO_HAVE, BUCKET_BEHAVIOURAL)
    )
    rotation = [skill for skill in ordered if skill.bucket in extra_buckets]
    if not rotation:
        rotation = ordered
    extras = cycle(rotation)
    while len(plan) < total:
        plan.append(next(extras))
    return plan


def objective_types_for(grade: str) -> tuple[str, ...]:
    """The objective formats a grade may use, in the order slots take them.

    A senior grade drops the single-answer MCQ, the most recall-shaped format
    (composition rule 4 of the question format specification).
    """
    if grade in format_config.SENIOR_GRADES:
        return (types.MCQ_MULTI, types.FILL_BLANK)
    return (types.MCQ_SINGLE, types.FILL_BLANK, types.MCQ_MULTI)


def _prose_type_for(category: str) -> str:
    return types.SHORT_ANSWER if category == BUCKET_BEHAVIOURAL else types.EVIDENCE_BASED


def _apply_type(slot: Slot, question_type: str) -> None:
    conf = format_config.get_config()
    slot.question_type = question_type
    slot.weight = conf.weight_by_type[question_type]
    slot.time_allocation_seconds = conf.time_seconds_by_type[question_type]


def _structured_candidates(slots: Sequence[Slot]) -> list[Slot]:
    """The Must-have and Nice-to-have slots, in the order they turn structured."""
    seen: set[uuid.UUID] = set()
    repeats: list[Slot] = []
    singles: list[Slot] = []
    for slot in slots:
        if slot.category == BUCKET_BEHAVIOURAL:
            continue
        if slot.competency_id in seen:
            repeats.append(slot)
        else:
            seen.add(slot.competency_id)
            singles.append(slot)
    # Lowest priority first means the END of each bucket's run backwards,
    # because `allocate` put the singles in priority order.
    nice = [slot for slot in reversed(singles) if slot.category == BUCKET_NICE_TO_HAVE]
    must = [slot for slot in reversed(singles) if slot.category == BUCKET_MUST_HAVE]
    return repeats + nice + must


def _place(
    candidates: list[Slot],
    taken: set[tuple[uuid.UUID, str]],
    type_cycle: Sequence[str],
    start: int,
) -> Slot | None:
    """Place one structured question: the first free candidate whose skill has
    not yet been asked in some format of the cycle, trying the cycle from
    `start`; failing that, the first free candidate at all (a forced repeat)."""
    free = [slot for slot in candidates if slot.planned_family == FAMILY_PROSE]
    for slot in free:
        for offset in range(len(type_cycle)):
            question_type = type_cycle[(start + offset) % len(type_cycle)]
            if (slot.competency_id, question_type) not in taken:
                taken.add((slot.competency_id, question_type))
                _apply_type(slot, question_type)
                return slot
    if free:
        slot = free[0]
        question_type = type_cycle[start % len(type_cycle)]
        taken.add((slot.competency_id, question_type))
        _apply_type(slot, question_type)
        return slot
    return None


def compose(
    allocation: Sequence[ContractSkill], *, mix: Mix, grade: str
) -> list[Slot]:
    """Decide every slot's format for this job, deterministically.

    `allocation` is `allocate`'s output and `mix` is `budget.mix`'s, so
    `len(allocation) == mix.total`. Raises when there are too few Must-have and
    Nice-to-have slots to carry the structured questions: the budget and the
    skills rules make that unreachable (one Must-have at least, every extra
    slot on a STEM role going to Must-have or Nice-to-have), and serving fewer
    than the plan without a recorded reason is the one thing the plan forbids.
    """
    if len(allocation) != mix.total:
        raise ValueError(
            f"an allocation of {len(allocation)} does not match a mix of {mix.total}"
        )
    conf = format_config.get_config()
    slots: list[Slot] = []
    for index, skill in enumerate(allocation):
        prose = _prose_type_for(skill.bucket)
        slots.append(
            Slot(
                index=index,
                competency_id=skill.id,
                category=skill.bucket,
                skill_name=skill.name,
                question_type=prose,
                planned_family=FAMILY_PROSE,
                weight=conf.weight_by_type[prose],
                time_allocation_seconds=conf.time_seconds_by_type[prose],
            )
        )

    candidates = _structured_candidates(slots)
    taken: set[tuple[uuid.UUID, str]] = set()
    for _ in range(mix.coding):
        placed = _place(candidates, taken, (types.CODING,), 0)
        if placed is None:
            raise ValueError("there are too few Must-have and Nice-to-have slots for the coding questions")
        placed.planned_family = FAMILY_CODING
    objective_cycle = objective_types_for(grade)
    for position in range(mix.objective):
        placed = _place(candidates, taken, objective_cycle, position)
        if placed is None:
            raise ValueError("there are too few Must-have and Nice-to-have slots for the objective questions")
        placed.planned_family = FAMILY_OBJECTIVE
    return slots


def degrade_to_prose(slot: Slot, reason: str, fallback_prompt: str, *, generated: bool) -> None:
    """Serve a planned coding or objective slot as the prose question already
    written for its skill, and record why.

    Always SHORT_ANSWER, never EVIDENCE_BASED: an evidence question needs an
    anchor this slot was never given, and an evidence row without one is the
    row `validate` refuses.
    """
    if not reason:
        raise ValueError("a degradation must name its reason")
    _apply_type(slot, types.SHORT_ANSWER)
    slot.payload = {}
    slot.rubric = None
    slot.resume_anchor = None
    slot.coding_draft = None
    slot.prompt = fallback_prompt
    slot.generated = generated
    slot.degradation = reason


def _anchor_key(anchor: str | None) -> str:
    return " ".join(str(anchor or "").split()).casefold()


def fall_back(slots: Sequence[Slot], fallback_prompts: Sequence[str], *, generated: Sequence[bool]) -> None:
    """Make every slot servable, deterministically, with no model call.

    An evidence slot with no usable anchor (none, too short, or a duplicate of
    an earlier slot's) becomes a short answer asking the prose question already
    written for its skill: the family is unchanged, only the anchor claim goes.
    An objective slot with no payload is degraded to prose with a recorded
    reason. A coding slot is never left without a draft by this point (the
    generator degrades it the moment the writer refuses), and one that is
    raises rather than being served as a coding question with no answer key.
    """
    conf = format_config.get_config()
    seen: set[str] = set()
    for slot in slots:
        if slot.question_type == types.EVIDENCE_BASED:
            key = _anchor_key(slot.resume_anchor)
            if len(key) < conf.anchor_min_chars or key in seen:
                _apply_type(slot, types.SHORT_ANSWER)
                slot.resume_anchor = None
                slot.payload = {}
                slot.prompt = fallback_prompts[slot.index]
                slot.generated = generated[slot.index]
            else:
                seen.add(key)
        elif family_of(slot.question_type) == FAMILY_OBJECTIVE and not slot.payload:
            degrade_to_prose(
                slot,
                "objective_generation_failed",
                fallback_prompts[slot.index],
                generated=generated[slot.index],
            )
        elif slot.question_type == types.CODING and slot.coding_draft is None:
            raise RuntimeError(
                f"coding slot {slot.index} reached the fallback without a draft or a degradation"
            )


def served_mix(slots: Iterable[Slot]) -> dict[str, int]:
    """How many of each family is actually served."""
    counts = {family: 0 for family in FAMILIES}
    for slot in slots:
        counts[family_of(slot.question_type)] += 1
    return counts


def validate(slots: Sequence[Slot], *, mix: Mix, skills: Sequence[ContractSkill]) -> list[str]:
    """The composition rules, as failures in words. Empty means servable.

    1. The PLANNED families match the mix exactly, and a slot is served in a
       different family only with a recorded degradation (so the served mix
       is the plan minus named reasons, never less without one).
    2. Every contract skill is asked at least once.
    3. A behavioural skill is asked in prose only.
    4. Coding appears only where the plan had coding, and every coding slot
       carries its validated draft.
    5. Every objective slot carries a payload its own model accepts.
    6. Every evidence slot is anchored to a distinct quotable resume item.
    """
    conf = format_config.get_config()
    failures: list[str] = []
    if not slots:
        return ["the assessment has no questions"]

    planned = {family: 0 for family in FAMILIES}
    for slot in slots:
        planned[slot.planned_family] += 1
    if planned != mix.as_dict():
        failures.append(f"the planned mix {planned} is not the budgeted mix {mix.as_dict()}")
    for slot in slots:
        served = family_of(slot.question_type)
        if served != slot.planned_family and not slot.degradation:
            failures.append(
                f"question {slot.index + 1} was planned as {slot.planned_family} and is "
                f"served as {served} with no recorded reason"
            )
        if slot.degradation and served != FAMILY_PROSE:
            failures.append(f"question {slot.index + 1} records a degradation but is not prose")

    asked = {slot.competency_id for slot in slots}
    missing = [skill.name for skill in skills if skill.id not in asked]
    if missing:
        failures.append("these skills are never asked: " + ", ".join(sorted(missing)))

    for slot in slots:
        if slot.category == BUCKET_BEHAVIOURAL and slot.question_type != types.SHORT_ANSWER:
            failures.append("a behavioural skill is answered in prose only")
            break

    if mix.coding == 0 and any(slot.question_type == types.CODING for slot in slots):
        failures.append("this role is not given coding questions")
    for slot in slots:
        if slot.question_type == types.CODING and slot.coding_draft is None:
            failures.append(f"coding question {slot.index + 1} has no validated draft")

    for slot in slots:
        if family_of(slot.question_type) != FAMILY_OBJECTIVE:
            continue
        try:
            types.parse_payload(slot.question_type, slot.payload)
        except ValueError:
            failures.append(f"objective question {slot.index + 1} carries no valid payload")

    anchors: set[str] = set()
    unanchored = 0
    for slot in slots:
        if slot.question_type != types.EVIDENCE_BASED:
            continue
        key = _anchor_key(slot.resume_anchor)
        if len(key) < conf.anchor_min_chars:
            unanchored += 1
        elif key in anchors:
            failures.append("two questions probe the same resume item")
        else:
            anchors.add(key)
    if unanchored:
        failures.append(f"{unanchored} evidence question(s) are not anchored to a resume item")
    return failures
