"""Anchoring an evidence question, writing a structured one, and regenerating.

    "Every evidence question must anchor to a specific, quotable item from the
     candidate's resume. Generic questions ('tell me about a challenge') are a
     failure of this format and must not be generated." (spec 2.1)

    "Distractors must be plausible -- the generation prompt must require that
     wrong options represent real misconceptions, not filler" (spec 2.2)

    "If validation fails, regenerate." (spec 3.2)

A prompt cannot enforce any of those, so each is a deterministic criterion
inside the loop, and each is tested by handing the loop an output that breaks
exactly one of them. The last section drives the whole regenerate-then-fall-
back cycle in `question_generation._fill_and_validate`, which is the thing
that actually decides what a candidate is served.

THE WRITERS ARE GIVEN THE CONTRACT, NEVER THE JOB DESCRIPTION (2026-09-25).
The skill, its evidence line and Sutra's role summary describe the role; the
job description is recruiter-edited text that can carry compensation.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_loop, llm_router, ppi
from app.services.assessment_contract import AssessmentContract, ContractSkill
from app.services.assessment_formats import composition, generation
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types
from app.services.assessment_questions import budget
from app.services.assessment_questions import generate as question_generation

ROLE_SUMMARY = "Runs the payment services behind checkout and settlement."

RESUME = (
    "Senior Engineer, Northwind Payments (2022 to 2026). Led the checkout "
    "migration from the session table to edge-verified tokens, cutting read "
    "amplification.\nBuilt the reconciliation service that settles card "
    "captures nightly.\nMentored two engineers through their first on-call."
)

ANCHOR_ONE = "Led the checkout migration from the session table to edge-verified tokens"
ANCHOR_TWO = "Built the reconciliation service that settles card captures nightly"


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Backend Engineer",
        assessment_grade="non_managerial",
        role_classification="STEM",
        jd_markdown="Own the payment services.",
        jd_json={"skills": ["Python", "PostgreSQL"]},
        question_target=None,
    )


def _skill(bucket: str = ppi.CATEGORY_MUST_HAVE, priority: int = 1) -> ContractSkill:
    return ContractSkill(
        id=uuid.uuid4(), name=f"Item {priority}", bucket=bucket, priority=priority,
        evidence_line=f"Has shown item {priority} in production work.",
    )


def _slot(index: int, skill: ContractSkill, question_type: str) -> composition.Slot:
    conf = format_config.get_config()
    return composition.Slot(
        index=index,
        competency_id=skill.id,
        category=skill.bucket,
        skill_name=skill.name,
        question_type=question_type,
        planned_family=composition.family_of(question_type),
        weight=conf.weight_by_type[question_type],
        time_allocation_seconds=conf.time_seconds_by_type[question_type],
    )


def _slots(count: int = 2) -> list[composition.Slot]:
    return [_slot(index, _skill(priority=index + 1), types.EVIDENCE_BASED) for index in range(count)]


def _anchor_response(items: list[dict]) -> str:
    return json.dumps({"questions": items})


def _good_anchor_items() -> list[dict]:
    return [
        {
            "index": 0,
            "prompt": "You led the checkout migration to edge-verified tokens. What did you own, and what was hardest?",
            "resume_anchor": ANCHOR_ONE,
            "sub_type": "project_deep_dive",
            "anchor_source": "employment_history[0]",
        },
        {
            "index": 1,
            "prompt": "You built the reconciliation service that settles card captures. How did you know it was right?",
            "resume_anchor": ANCHOR_TWO,
            "sub_type": "claim_substantiation",
            "anchor_source": "employment_history[0]",
        },
    ]


def _responder(monkeypatch, responses: list[str]):
    sent: list[str] = []
    queue = list(responses)

    async def _invoke(task_type, messages, **kwargs):
        sent.append(" ".join(message["content"] for message in messages))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    return sent


async def _anchor(slots, *, job=None, resume=RESUME):
    skills = {
        slot.competency_id: ContractSkill(
            id=slot.competency_id, name=slot.skill_name, bucket=slot.category,
            priority=slot.index + 1, evidence_line=f"Evidence for {slot.skill_name}.",
        )
        for slot in slots
    }
    return await generation.anchor_evidence(
        None,
        job=job or _job(),
        slots=slots,
        skills=skills,
        role_summary=ROLE_SUMMARY,
        resume_text=resume,
        resume_excerpt=resume[:200],
        project_evidence="",
    )


# ── Is this actually a quote from this resume? ───────────────────────────────


def test_a_verbatim_resume_item_is_quotable() -> None:
    assert generation.quotable(ANCHOR_ONE, RESUME)
    assert generation.quotable(ANCHOR_TWO, RESUME)


def test_a_quote_across_a_line_break_is_still_a_quote() -> None:
    """A resume extracted from a PDF carries line breaks the model cannot see,
    so whitespace is collapsed before the comparison."""
    across = "captures nightly. Mentored two engineers"
    assert generation.quotable(across, RESUME)


def test_a_paraphrase_is_not_an_anchor() -> None:
    """The recruiter's view shows the anchor as WHAT WAS PROBED. A paraphrase
    would put words in the candidate's resume that are not in it."""
    assert not generation.quotable("Ran a big migration of the checkout system", RESUME)
    assert not generation.quotable("Led the billing migration", RESUME)
    assert not generation.quotable("", RESUME)


# ── Anchoring a batch ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_anchored_batch_is_accepted_with_its_sub_type_and_locator(monkeypatch) -> None:
    _responder(monkeypatch, [_anchor_response(_good_anchor_items())])
    anchored, result = await _anchor(_slots())
    assert not result.degraded
    assert set(anchored) == {0, 1}
    assert anchored[0].resume_anchor == ANCHOR_ONE
    assert anchored[0].payload["sub_type"] == "project_deep_dive"
    assert anchored[0].payload["anchor_source"] == "employment_history[0]"
    assert anchored[1].payload["sub_type"] == "claim_substantiation"


@pytest.mark.asyncio
async def test_the_resume_and_the_contract_reach_the_writer_and_the_jd_does_not(monkeypatch) -> None:
    """The resume is what an anchor is quoted from; the skill, its evidence
    line and the role summary are what the question probes. The job
    description never reaches the prompt: it is recruiter-edited text that
    can carry compensation."""
    sent = _responder(monkeypatch, [_anchor_response(_good_anchor_items())])
    await _anchor(_slots())
    assert ANCHOR_ONE in sent[0]
    assert ROLE_SUMMARY in sent[0]
    assert "Evidence for Item 1." in sent[0]
    assert "Backend Engineer" in sent[0]
    assert "Own the payment services." not in sent[0]


@pytest.mark.asyncio
async def test_an_invented_anchor_is_rejected_and_the_model_is_told(monkeypatch) -> None:
    """THE FAILURE THIS GATE EXISTS FOR. An anchor the resume does not contain
    is a claim the product would then show a recruiter as the candidate's own
    words."""
    invented = _good_anchor_items()
    invented[0]["resume_anchor"] = "Rewrote the fraud engine end to end"
    sent = _responder(monkeypatch, [_anchor_response(invented), _anchor_response(_good_anchor_items())])
    anchored, result = await _anchor(_slots())
    assert not result.degraded
    assert len(sent) == 2
    assert "copied word for word from the resume" in sent[1]
    assert anchored[0].resume_anchor == ANCHOR_ONE


@pytest.mark.asyncio
async def test_a_generic_question_with_no_anchor_is_rejected(monkeypatch) -> None:
    """"Generic questions ('tell me about a challenge') are a failure of this
    format and must not be generated"."""
    generic = _good_anchor_items()
    generic[0]["resume_anchor"] = ""
    generic[0]["prompt"] = "Tell me about a challenge you faced."
    sent = _responder(monkeypatch, [_anchor_response(generic), _anchor_response(_good_anchor_items())])
    _anchored, result = await _anchor(_slots())
    assert not result.degraded
    assert "quotable item" in sent[1]


@pytest.mark.asyncio
async def test_two_slots_anchored_to_one_resume_item_are_rejected(monkeypatch) -> None:
    """Duplicate prevention at the point of generation, so the composition
    validator is not the first thing to notice."""
    duplicated = _good_anchor_items()
    duplicated[1]["resume_anchor"] = ANCHOR_ONE
    sent = _responder(monkeypatch, [_anchor_response(duplicated), _anchor_response(_good_anchor_items())])
    anchored, result = await _anchor(_slots())
    assert not result.degraded
    assert "duplicates the anchor" in sent[1]
    assert anchored[0].resume_anchor != anchored[1].resume_anchor


@pytest.mark.asyncio
async def test_an_unknown_sub_type_is_rejected(monkeypatch) -> None:
    wrong = _good_anchor_items()
    wrong[0]["sub_type"] = "vibes_check"
    sent = _responder(monkeypatch, [_anchor_response(wrong), _anchor_response(_good_anchor_items())])
    _anchored, result = await _anchor(_slots())
    assert not result.degraded
    assert "sub_type must be one of" in sent[1]


@pytest.mark.asyncio
async def test_the_slots_that_were_anchored_survive_a_degraded_batch(monkeypatch) -> None:
    """Each surviving item passed the same checks individually, so keeping
    them is declining to discard work that was done, not substituting for work
    that was not. The result still reports degraded."""
    partial = [_good_anchor_items()[0], {**_good_anchor_items()[1], "resume_anchor": "not in the resume"}]
    _responder(monkeypatch, [_anchor_response(partial)])
    anchored, result = await _anchor(_slots())
    assert result.degraded
    assert set(anchored) == {0}
    assert anchored[0].resume_anchor == ANCHOR_ONE


@pytest.mark.asyncio
async def test_a_candidate_with_no_resume_text_is_never_asked_for_an_anchor(monkeypatch) -> None:
    """There is nothing to quote, so there is no call to make. The slots fall
    back to the questions already written for their items."""
    called: list[str] = []

    async def _invoke(task_type, messages, **kwargs):
        called.append(task_type)
        return _anchor_response(_good_anchor_items())

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    anchored, result = await _anchor(_slots(), resume="   ")
    assert anchored == {}
    assert called == []
    assert result.degraded


@pytest.mark.asyncio
async def test_two_candidates_are_anchored_to_their_own_resumes(monkeypatch) -> None:
    """Per-candidate uniqueness (spec 3.3): nothing is cached and no template
    is shared, so each call carries that candidate's own resume."""
    sent = _responder(monkeypatch, [_anchor_response(_good_anchor_items())])
    await _anchor(_slots())
    other = "Data Analyst, Contoso. Rebuilt the weekly revenue model in SQL."
    with pytest.raises(Exception):
        # The second candidate's anchors are not in their resume, so the same
        # response is refused rather than reused.
        anchored, result = await _anchor(_slots(), resume=other)
        assert result.degraded and not anchored
        raise AssertionError("reached only when the batch was wrongly accepted")
    assert other in sent[-1]
    assert ANCHOR_ONE not in sent[-1]


# ── Writing a structured payload ─────────────────────────────────────────────


def _structured_slot(question_type: str) -> composition.Slot:
    return _slot(0, _skill(), question_type)


def _mcq_response(**overrides) -> str:
    payload = {
        "prompt": "Which index would speed up this lookup the most?",
        "payload": {
            "options": [
                {"id": "a", "text": "A covering index on the order id"},
                {"id": "b", "text": "A hash index on the created timestamp"},
                {"id": "c", "text": "A partial index on refunded rows"},
                {"id": "d", "text": "A unique index on the customer name"},
            ],
            "correct_option_id": "a",
        },
        "misconceptions": {
            "b": "Assumes a hash index helps a range scan, which it does not.",
            "c": "Assumes the filtered subset is the one being queried here.",
            "d": "Assumes uniqueness implies lookup speed on another column.",
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


async def _write(slot, monkeypatch, responses):
    sent = _responder(monkeypatch, responses)
    result = await generation.write_structured(
        None, job=_job(), skill=_skill(), role_summary=ROLE_SUMMARY, slot=slot,
        resume_excerpt=RESUME,
    )
    return result, sent


@pytest.mark.asyncio
async def test_a_well_formed_mcq_is_accepted_with_its_misconceptions(monkeypatch) -> None:
    slot = _structured_slot(types.MCQ_SINGLE)
    result, _sent = await _write(slot, monkeypatch, [_mcq_response()])
    assert not result.degraded
    assert result.value.payload["correct_option_id"] == "a"
    assert set(result.value.rubric["misconceptions"]) == {"b", "c", "d"}


@pytest.mark.asyncio
async def test_a_filler_option_is_rejected(monkeypatch) -> None:
    """"Distractors must be plausible ... not filler"."""
    filler = json.loads(_mcq_response())
    filler["payload"]["options"][3] = {"id": "d", "text": "None of the above"}
    slot = _structured_slot(types.MCQ_SINGLE)
    result, sent = await _write(slot, monkeypatch, [json.dumps(filler), _mcq_response()])
    assert not result.degraded
    assert "no filler options" in sent[1]


@pytest.mark.asyncio
async def test_a_distractor_with_no_stated_misconception_is_rejected(monkeypatch) -> None:
    """The rationale is what makes "a real misconception" checkable rather
    than merely asked for."""
    thin = json.loads(_mcq_response())
    thin["misconceptions"]["b"] = "wrong"
    slot = _structured_slot(types.MCQ_SINGLE)
    result, sent = await _write(slot, monkeypatch, [json.dumps(thin), _mcq_response()])
    assert not result.degraded
    assert "misconception rationale" in sent[1]


@pytest.mark.asyncio
async def test_two_distractors_standing_for_one_misconception_are_rejected(monkeypatch) -> None:
    repeated = json.loads(_mcq_response())
    repeated["misconceptions"]["c"] = repeated["misconceptions"]["b"]
    slot = _structured_slot(types.MCQ_SINGLE)
    result, sent = await _write(slot, monkeypatch, [json.dumps(repeated), _mcq_response()])
    assert not result.degraded
    assert "DIFFERENT misconception" in sent[1]


@pytest.mark.asyncio
async def test_a_payload_that_is_not_valid_for_its_type_is_rejected(monkeypatch) -> None:
    broken = json.loads(_mcq_response())
    broken["payload"]["correct_option_id"] = "z"
    slot = _structured_slot(types.MCQ_SINGLE)
    result, sent = await _write(slot, monkeypatch, [json.dumps(broken), _mcq_response()])
    assert not result.degraded
    assert "not a valid mcq_single payload" in sent[1]


@pytest.mark.asyncio
async def test_the_objective_writer_refuses_a_coding_slot() -> None:
    """A coding question is EXECUTED: `coding_generation` writes it and proves
    its tests in the sandbox. The read-the-code writer this module used to
    carry is deleted, and asking for one is a caller defect, not a degradation."""
    with pytest.raises(ValueError):
        await generation.write_structured(
            None, job=_job(), skill=_skill(), role_summary=ROLE_SUMMARY,
            slot=_structured_slot(types.CODING), resume_excerpt=RESUME,
        )


@pytest.mark.asyncio
async def test_an_unavailable_provider_leaves_the_slot_unfilled(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(llm_router, "invoke_llm", _boom)
    slot = _structured_slot(types.MCQ_SINGLE)
    result = await generation.write_structured(
        None, job=_job(), skill=_skill(), role_summary=ROLE_SUMMARY, slot=slot,
        resume_excerpt=RESUME,
    )
    assert result.degraded
    assert result.value is None


# ── Regenerate, then fall back (spec 3.2) ────────────────────────────────────


def _contract() -> AssessmentContract:
    skills = tuple(
        ContractSkill(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{bucket}-{n}"), name=f"{bucket} {n}",
            bucket=bucket, priority=n, evidence_line=f"Evidence for {bucket} {n}.",
        )
        for bucket in ppi.CATEGORIES
        for n in range(1, 6)
    )
    return AssessmentContract(
        job_id=uuid.uuid4(), version=0, locked=False, skills=skills,
        role_summary=ROLE_SUMMARY, digest="d" * 64, grade="non_managerial", locked_at=None,
    )


async def _compose(monkeypatch, *, anchor_batches, structured_ok=True):
    """Drive `question_generation._fill_and_validate` with scripted writers,
    over a job with no coding (so every structured slot is objective)."""
    contract = _contract()
    skills = {skill.id: skill for skill in contract.skills}
    total = budget.question_budget(contract.grade, len(contract.skills))
    mix = budget.mix(total, coding=False)
    slots = composition.compose(
        composition.allocate(contract.skills, total, stem=True), mix=mix, grade=contract.grade
    )
    prose = [f"Stored question {index}." for index in range(len(slots))]
    for slot in slots:
        slot.prompt = prose[slot.index]
        slot.generated = True
    calls = {"anchor": 0, "structured": 0}
    batches = list(anchor_batches)

    async def _anchor_evidence(session, *, slots, **kwargs):
        calls["anchor"] += 1
        wanted = batches[min(calls["anchor"] - 1, len(batches) - 1)]
        anchored = {}
        if wanted:
            for slot in slots:
                if slot.question_type != types.EVIDENCE_BASED:
                    continue
                anchored[slot.index] = generation.AnchoredQuestion(
                    index=slot.index,
                    prompt=f"You did the thing at position {slot.index}. What did you own?",
                    resume_anchor=f"Led the work stream numbered {slot.index} at Northwind",
                    payload={"sub_type": "project_deep_dive", "anchor_source": "employment_history[0]", "follow_up_permitted": True},
                )
        return anchored, agent_loop.LoopResult(value=anchored, degraded=not wanted)

    async def _write_structured(session, *, slot, **kwargs):
        calls["structured"] += 1
        if not structured_ok:
            return agent_loop.LoopResult(value=None, degraded=True)
        options = json.loads(_mcq_response())["payload"]["options"]
        payload = {
            types.MCQ_SINGLE: {"options": options, "correct_option_id": "a"},
            types.MCQ_MULTI: {"options": options, "correct_option_ids": ["a", "b"], "scoring": "partial"},
            types.FILL_BLANK: {"template": "The ___ pattern.",
                               "blanks": [{"index": 0, "accepted": ["observer"], "case_sensitive": False}]},
        }[slot.question_type]
        return agent_loop.LoopResult(
            value=generation.StructuredQuestion(
                prompt=f"A structured question for slot {slot.index}.",
                payload=payload,
                rubric={"misconceptions": {}},
            ),
            degraded=False,
        )

    monkeypatch.setattr(generation, "anchor_evidence", _anchor_evidence)
    monkeypatch.setattr(generation, "write_structured", _write_structured)
    await question_generation._fill_and_validate(
        None,
        job=_job(),
        contract=contract,
        slots=slots,
        skills=skills,
        mix=mix,
        prose=prose,
        generated=[True] * len(slots),
        resume_text=RESUME,
        resume_excerpt=RESUME[:200],
        project_evidence="",
    )
    return slots, calls, mix, contract


@pytest.mark.asyncio
async def test_a_composition_that_validates_is_served_after_one_pass(monkeypatch) -> None:
    slots, calls, mix, contract = await _compose(monkeypatch, anchor_batches=[True])
    assert calls["anchor"] == 1
    assert composition.validate(slots, mix=mix, skills=contract.skills) == []
    assert any(slot.question_type == types.EVIDENCE_BASED for slot in slots)
    assert any(slot.planned_family == composition.FAMILY_OBJECTIVE for slot in slots)


@pytest.mark.asyncio
async def test_a_failed_validation_regenerates_rather_than_being_served(monkeypatch) -> None:
    """"If validation fails, regenerate." The first pass anchors nothing, so
    every evidence row is unanchored; the second pass fixes it."""
    slots, calls, mix, contract = await _compose(monkeypatch, anchor_batches=[False, True])
    assert calls["anchor"] == 2, "the invalid composition was served without a retry"
    assert composition.validate(slots, mix=mix, skills=contract.skills) == []
    assert all(slot.resume_anchor for slot in slots if slot.question_type == types.EVIDENCE_BASED)


@pytest.mark.asyncio
async def test_an_objective_slot_is_written_once_across_every_attempt(monkeypatch) -> None:
    """A regeneration re-anchors; it does not pay for the payloads again."""
    slots, calls, mix, _contract_ = await _compose(monkeypatch, anchor_batches=[False, True])
    assert calls["structured"] == mix.objective


@pytest.mark.asyncio
async def test_after_every_attempt_fails_the_fallback_is_what_is_served(monkeypatch) -> None:
    """THE PROPERTY THAT PROTECTS THE CANDIDATE. Whatever the model does, what
    is served validates: every slot asks the prose question already written for
    its skill, and every objective slot that could not be written says why."""
    conf = format_config.get_config()
    slots, calls, mix, contract = await _compose(monkeypatch, anchor_batches=[False], structured_ok=False)
    assert calls["anchor"] == conf.composition_attempts
    assert {slot.question_type for slot in slots} == {types.SHORT_ANSWER}
    assert all(slot.resume_anchor is None for slot in slots)
    assert [slot.prompt for slot in slots] == [f"Stored question {index}." for index in range(len(slots))]
    assert [slot.degradation for slot in slots if slot.degradation] == [
        "objective_generation_failed"
    ] * mix.objective
    assert composition.validate(slots, mix=mix, skills=contract.skills) == []
