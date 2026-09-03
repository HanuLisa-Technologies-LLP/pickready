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
back cycle in `ppi._compose_formats`, which is the thing that actually decides
what a candidate is served.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_loop, llm_router, ppi
from app.services.assessment_formats import composition, generation
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types

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


def _competency(category: str = ppi.CATEGORY_MUST_HAVE, ordinal: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), category=category, name=f"Item {ordinal}",
        description=f"What item {ordinal} measures.", ordinal=ordinal,
    )


def _slots(count: int = 2) -> list[composition.Slot]:
    conf = format_config.get_config()
    return [
        composition.Slot(
            index=index,
            competency_id=uuid.uuid4(),
            category=ppi.CATEGORY_MUST_HAVE,
            question_type=types.EVIDENCE_BASED,
            weight=conf.weight_by_type[types.EVIDENCE_BASED],
            time_allocation_seconds=conf.time_seconds_by_type[types.EVIDENCE_BASED],
        )
        for index in range(count)
    ]


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
    competencies = {slot.competency_id: _competency(ordinal=slot.index + 1) for slot in slots}
    for slot, competency in zip(slots, competencies.values()):
        competencies[slot.competency_id] = competency
    return await generation.anchor_evidence(
        None,
        job=job or _job(),
        slots=slots,
        competencies=competencies,
        resume_text=resume,
        resume_excerpt=resume[:200],
        project_evidence="",
        hiring_context="{}",
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
async def test_the_resume_and_the_hiring_context_reach_the_writer(monkeypatch) -> None:
    """Spec 2.1 lists all of them as generation inputs, "not a subset"."""
    sent = _responder(monkeypatch, [_anchor_response(_good_anchor_items())])
    await _anchor(_slots())
    assert ANCHOR_ONE in sent[0]
    assert "Own the payment services." in sent[0]
    assert "Backend Engineer" in sent[0]


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
    conf = format_config.get_config()
    return composition.Slot(
        index=0,
        competency_id=uuid.uuid4(),
        category=ppi.CATEGORY_MUST_HAVE,
        question_type=question_type,
        weight=conf.weight_by_type[question_type],
        time_allocation_seconds=conf.time_seconds_by_type[question_type],
    )


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
        None, job=_job(), competency=_competency(), slot=slot, resume_excerpt=RESUME
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
async def test_a_coding_question_keeps_its_expected_approach_off_the_prompt(monkeypatch) -> None:
    response = json.dumps({
        "prompt": "Remove duplicates from a list, preserving first-seen order.",
        "payload": {
            "language": "python",
            "starter_code": "def solve(items):",
            "constraints": "No external libraries.",
            "expected_approach": "Track seen elements in a set and scan once.",
            "language_options": ["python", "javascript"],
        },
    })
    slot = _structured_slot(types.CODING)
    result, _sent = await _write(slot, monkeypatch, [response])
    assert not result.degraded
    assert result.value.payload["expected_approach"].startswith("Track seen")
    assert "expected_approach" not in result.value.prompt
    # The reader's criteria travel with the question, so the evaluator reads
    # the same rubric the question was written against.
    assert set(result.value.rubric["criteria"]) == {
        "correctness_of_approach", "code_quality", "edge_case_handling",
        "efficiency_awareness", "idiomatic_use",
    }


@pytest.mark.asyncio
async def test_an_unavailable_provider_leaves_the_slot_unfilled(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(llm_router, "invoke_llm", _boom)
    slot = _structured_slot(types.MCQ_SINGLE)
    result = await generation.write_structured(
        None, job=_job(), competency=_competency(), slot=slot, resume_excerpt=RESUME
    )
    assert result.degraded
    assert result.value is None


# ── Regenerate, then fall back (spec 3.2) ────────────────────────────────────


class _FakeResult:
    def scalars(self):
        return self

    def first(self):
        return None


class _FakeSession:
    """Enough session for `_hiring_context`, which is the only query the
    composer makes."""

    async def execute(self, *args, **kwargs):
        return _FakeResult()


def _allocation(size: int = 15) -> list[SimpleNamespace]:
    matrix = [
        _competency(category, index + 1)
        for category in ppi.CATEGORIES
        for index in range(5)
    ]
    return ppi._allocate(matrix, size, "non_managerial")


async def _compose(monkeypatch, *, anchor_batches, structured_ok=True, size=15):
    """Drive `ppi._compose_formats` with scripted generation results."""
    allocation = _allocation(size)
    calls = {"anchor": 0, "structured": 0}
    batches = list(anchor_batches)

    async def _anchor_evidence(session, *, slots, **kwargs):
        calls["anchor"] += 1
        wanted = batches[min(calls["anchor"] - 1, len(batches) - 1)]
        anchored = {}
        if wanted:
            for position, slot in enumerate(
                [slot for slot in slots if slot.question_type == types.EVIDENCE_BASED]
            ):
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
        return agent_loop.LoopResult(
            value=generation.StructuredQuestion(
                prompt=f"A structured question for slot {slot.index}.",
                payload=json.loads(_mcq_response())["payload"],
                rubric={"misconceptions": {}},
            ),
            degraded=False,
        )

    monkeypatch.setattr(generation, "anchor_evidence", _anchor_evidence)
    monkeypatch.setattr(generation, "write_structured", _write_structured)
    slots = await ppi._compose_formats(
        _FakeSession(),
        _job(),
        allocation,
        grade="non_managerial",
        base_prompts=[f"Stored question {index}." for index in range(len(allocation))],
        profile=None,
        project_evidence_block="",
    )
    return slots, calls


@pytest.mark.asyncio
async def test_a_composition_that_validates_is_served_after_one_pass(monkeypatch) -> None:
    slots, calls = await _compose(monkeypatch, anchor_batches=[True])
    assert calls["anchor"] == 1
    assert composition.validate(slots, "non_managerial", "STEM") == []
    assert any(slot.question_type == types.EVIDENCE_BASED for slot in slots)
    assert any(slot.question_type in types.SUPPORTING_TYPES for slot in slots)


@pytest.mark.asyncio
async def test_a_failed_validation_regenerates_rather_than_being_served(monkeypatch) -> None:
    """"If validation fails, regenerate." The first pass anchors nothing, so
    every evidence row breaks rule 2; the second pass fixes it."""
    slots, calls = await _compose(monkeypatch, anchor_batches=[False, True])
    assert calls["anchor"] == 2, "the invalid composition was served without a retry"
    assert composition.validate(slots, "non_managerial", "STEM") == []
    assert all(
        slot.resume_anchor
        for slot in slots
        if slot.question_type == types.EVIDENCE_BASED
    )


@pytest.mark.asyncio
async def test_a_structured_slot_is_written_once_across_every_attempt(monkeypatch) -> None:
    """A regeneration re-anchors; it does not pay for the payloads again."""
    _slots_out, calls = await _compose(monkeypatch, anchor_batches=[False, True])
    supporting = [
        slot
        for slot in composition.compose(
            _allocation(15), grade="non_managerial", role_classification="STEM"
        )
        if slot.question_type in types.SUPPORTING_TYPES
    ]
    assert calls["structured"] == len(supporting)


@pytest.mark.asyncio
async def test_after_every_attempt_fails_the_fallback_is_what_is_served(monkeypatch) -> None:
    """THE PROPERTY THAT PROTECTS THE CANDIDATE. Whatever the model does, what
    is served validates, and it is the text question already written for each
    item rather than invented content."""
    conf = format_config.get_config()
    slots, calls = await _compose(monkeypatch, anchor_batches=[False], structured_ok=False)
    assert calls["anchor"] == conf.composition_attempts
    assert {slot.question_type for slot in slots} == {types.SHORT_ANSWER}
    assert all(slot.resume_anchor is None for slot in slots)
    assert [slot.prompt for slot in slots] == [
        f"Stored question {index}." for index in range(len(slots))
    ]
    assert composition.validate(slots, "non_managerial", "STEM") == []


@pytest.mark.asyncio
async def test_a_structured_slot_the_model_could_not_fill_asks_its_text_question(
    monkeypatch,
) -> None:
    """A structured row with no payload would be a question with no answer
    key, so the slot reverts rather than being served empty."""
    slots, _calls = await _compose(monkeypatch, anchor_batches=[True], structured_ok=False)
    assert all(
        slot.payload or slot.question_type in types.TEXT_TYPES for slot in slots
    )
    assert composition.validate(slots, "non_managerial", "STEM") == []
