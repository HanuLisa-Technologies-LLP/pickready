"""AI evaluation with reasoning, and where each format's score comes from.

    "Evaluation output must include reasoning, not just a score. The
     recruiter's Q&A view needs to show why an answer was judged as it was. A
     bare number is not defensible in a hiring context." (spec 6.2)

    "because code is not executed, evaluation reflects whether the code
     appears correct, not whether it runs" (spec 2.5)

The deterministic gate inside the loop is what makes those two sentences hold
rather than being asked for, so each of its criteria is tested by giving it an
output that breaks exactly one of them:

  * reasoning shorter than the floor
  * no citation at all
  * a CITATION THAT IS NOT IN THE ANSWER, which is the worst of them: a
    fabricated quote reads as provenance
  * a coding verdict with no hedging, which claims a run that never happened
  * a number in the reasoning, which is prose a recruiter reads

And the second half of the file pins where each format's score comes from
(spec 6), including the weighted mean that makes a supporting question carry
less of a matrix item than an evidence question does.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_loop, llm_router
from app.services.assessment_contract import ContractSkill
from app.services.assessment_pipeline import evidence as answer_evidence
from app.services.miti import grades as miti_grades
from app.services.miti import items as miti_items
from app.services import ppi
from app.services.assessment_formats import evaluation, types

ANSWER = (
    "I owned the checkout migration end to end. We moved from the monolith's "
    "session table to a token the edge could verify, because the read amplification "
    "was the thing actually hurting us. The hardest part was the dual-write window, "
    "and I got that wrong the first time: I cut over reads before the backfill had "
    "caught up and had to roll back within the hour."
)

CODE = "def solve(items):\n    seen = set()\n    return [x for x in items if not (x in seen or seen.add(x))]"

REASONING = (
    "The answer names a specific system and a specific decision, saying the move went "
    "from the session table to a token the edge could verify, and it gives the reason "
    "in terms of read amplification rather than in general terms. Ownership reads as "
    "personal throughout. The account also volunteers a failure, which suggests candour "
    "worth confirming in interview."
)

CODING_REASONING = (
    "The code was read and not executed, so this describes how it appears rather than "
    "how it runs. The comprehension over items with a seen set appears to preserve "
    "first-seen order while removing repeats, which seems to match the stated problem. "
    "It relies on the side effect of adding inside the condition, which is compact but "
    "harder to read. Behaviour on unhashable elements cannot be confirmed without "
    "running it."
)


def _valid_evidence_output() -> dict:
    return {
        "score": 78,
        "rubric_scores": {name: 0.8 for name in evaluation.EVIDENCE_CRITERIA},
        "reasoning": REASONING,
        "citations": ["the dual-write window", "read amplification"],
    }


def _valid_coding_output() -> dict:
    return {
        "score": 71,
        "rubric_scores": {name: 0.7 for name in evaluation.CODING_CRITERIA},
        "reasoning": CODING_REASONING,
        "citations": ["seen = set()"],
    }


def _responder(monkeypatch, payloads):
    """Answer each attempt with the next payload; record the prompts sent."""
    sent: list[str] = []
    queue = list(payloads)

    async def _invoke(task_type, messages, **kwargs):
        sent.append(" ".join(message["content"] for message in messages))
        return json.dumps(queue.pop(0) if len(queue) > 1 else queue[0])

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    return sent


# ── The evaluation itself ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_evidence_evaluation_carries_reasoning_citations_and_a_rubric(monkeypatch) -> None:
    sent = _responder(monkeypatch, [_valid_evidence_output()])
    result = await evaluation.evaluate(
        None,
        question_type=types.EVIDENCE_BASED,
        prompt="You list the checkout migration. What did you own?",
        answer_text=ANSWER,
        item_name="Migration ownership",
        resume_anchor="Led the checkout migration",
        question_rubric={"90_100": "Exceptional depth and owned outcomes."},
    )
    assert not result.degraded
    record = result.value
    assert record["score"] == 78
    assert set(record["rubric_scores"]) == set(evaluation.EVIDENCE_CRITERIA)
    assert record["reasoning"] == REASONING
    assert record["citations"] == ["the dual-write window", "read amplification"]
    assert record["rubric"] == evaluation.EVIDENCE_CRITERIA
    # The anchor and the question's own rubric reach the model: an evidence
    # answer is judged against the resume claim it was written from.
    assert "Led the checkout migration" in sent[0]
    assert "Exceptional depth and owned outcomes." in sent[0]
    # No not-executed note on an evidence answer; there is no code.
    assert "not_executed_note" not in record


@pytest.mark.asyncio
async def test_a_coding_evaluation_always_states_that_the_code_was_not_run(monkeypatch) -> None:
    sent = _responder(monkeypatch, [_valid_coding_output()])
    result = await evaluation.evaluate(
        None,
        question_type=types.CODING,
        prompt="Remove duplicates, preserving order.",
        answer_text=CODE,
        item_name="Python",
        payload={"language": "python", "constraints": "No imports.", "expected_approach": "Set plus scan."},
        language="python",
    )
    assert not result.degraded
    record = result.value
    assert record["not_executed_note"] == evaluation.NOT_EXECUTED_NOTE
    assert "not executed" in record["not_executed_note"].casefold()
    # The prompt says it too, so the model does not have to be corrected into it.
    assert "NOT BEEN EXECUTED" in sent[0]
    # And the expected approach is for the reader, never for the candidate.
    assert "Set plus scan." in sent[0]


@pytest.mark.asyncio
async def test_a_short_reasoning_is_rejected_and_the_model_is_told(monkeypatch) -> None:
    """"A bare number is not defensible in a hiring context"."""
    thin = {**_valid_evidence_output(), "reasoning": "Good answer with detail."}
    sent = _responder(monkeypatch, [thin, _valid_evidence_output()])
    result = await evaluation.evaluate(
        None,
        question_type=types.EVIDENCE_BASED,
        prompt="q",
        answer_text=ANSWER,
        item_name="Migration ownership",
    )
    assert not result.degraded, "the second attempt should have been accepted"
    assert len(sent) == 2
    assert "at least" in sent[1] and "words of reasoning" in sent[1]


@pytest.mark.asyncio
async def test_an_evaluation_with_no_citation_is_rejected(monkeypatch) -> None:
    uncited = {**_valid_evidence_output(), "citations": []}
    sent = _responder(monkeypatch, [uncited, _valid_evidence_output()])
    result = await evaluation.evaluate(
        None, question_type=types.EVIDENCE_BASED, prompt="q", answer_text=ANSWER, item_name="x"
    )
    assert not result.degraded
    assert "cite at least one phrase" in sent[1]


@pytest.mark.asyncio
async def test_a_fabricated_citation_is_rejected_and_named(monkeypatch) -> None:
    """THE WORST FAILURE THIS GATE CATCHES. A quote that is not in the answer
    reads as provenance, which is worse than no provenance at all."""
    invented = {**_valid_evidence_output(), "citations": ["I rewrote the billing engine"]}
    sent = _responder(monkeypatch, [invented, _valid_evidence_output()])
    result = await evaluation.evaluate(
        None, question_type=types.EVIDENCE_BASED, prompt="q", answer_text=ANSWER, item_name="x"
    )
    assert not result.degraded
    assert "copied word for word" in sent[1]
    assert "I rewrote the billing engine" in sent[1]


@pytest.mark.asyncio
async def test_an_unhedged_coding_verdict_is_rejected(monkeypatch) -> None:
    """The code was not executed, so a verdict that claims it works is a claim
    the product cannot make."""
    overclaimed = {
        **_valid_coding_output(),
        "reasoning": (
            "The function removes duplicates correctly and returns the list in the "
            "original order. The set membership check is efficient and the code is "
            "clean, readable and handles the required cases without any problem at "
            "all in the general case as written by the candidate here."
        ),
    }
    sent = _responder(monkeypatch, [overclaimed, _valid_coding_output()])
    result = await evaluation.evaluate(
        None, question_type=types.CODING, prompt="q", answer_text=CODE, item_name="Python"
    )
    assert not result.degraded
    assert "hedged language" in sent[1]


@pytest.mark.asyncio
async def test_a_number_in_the_reasoning_is_rejected(monkeypatch) -> None:
    """The reasoning is prose a recruiter reads, and the no-numbers rule covers
    it exactly as it covers a report remark."""
    numeric = {
        **_valid_evidence_output(),
        "reasoning": REASONING + " On the ownership rubric this answer scores 8/10.",
    }
    sent = _responder(monkeypatch, [numeric, _valid_evidence_output()])
    result = await evaluation.evaluate(
        None, question_type=types.EVIDENCE_BASED, prompt="q", answer_text=ANSWER, item_name="x"
    )
    assert not result.degraded
    assert "carries no score" in sent[1]


@pytest.mark.asyncio
async def test_a_rubric_score_outside_its_range_is_rejected(monkeypatch) -> None:
    out_of_range = {
        **_valid_evidence_output(),
        "rubric_scores": {**{name: 0.8 for name in evaluation.EVIDENCE_CRITERIA}, "specificity": 4.0},
    }
    sent = _responder(monkeypatch, [out_of_range, _valid_evidence_output()])
    result = await evaluation.evaluate(
        None, question_type=types.EVIDENCE_BASED, prompt="q", answer_text=ANSWER, item_name="x"
    )
    assert not result.degraded
    assert "rubric_scores.specificity" in sent[1]


@pytest.mark.asyncio
async def test_an_unavailable_provider_degrades_and_stores_nothing(monkeypatch) -> None:
    """No invented reasoning. The caller scores the answer the way every
    rubric-scored answer was scored before this format existed."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("no providers")

    monkeypatch.setattr(llm_router, "invoke_llm", _boom)
    result = await evaluation.evaluate(
        None, question_type=types.EVIDENCE_BASED, prompt="q", answer_text=ANSWER, item_name="x"
    )
    assert result.degraded
    assert result.value is None


def test_the_two_rubrics_are_the_ones_the_specification_lists() -> None:
    assert list(evaluation.EVIDENCE_CRITERIA) == [
        "specificity",
        "ownership_clarity",
        "technical_depth",
        "coherence_with_resume",
        "honesty_markers",
    ]
    assert list(evaluation.CODING_CRITERIA) == [
        "correctness_of_approach",
        "code_quality",
        "edge_case_handling",
        "efficiency_awareness",
        "idiomatic_use",
    ]
    assert evaluation.rubric_for(types.EVIDENCE_BASED) == evaluation.EVIDENCE_CRITERIA
    with pytest.raises(ValueError):
        evaluation.rubric_for(types.MCQ_SINGLE)


# ── Where each format's score comes from (spec 6.3) ─────────────────────────
#
# Graded by Miti's item stage since WP5-B (`miti/items.py`), the only code that
# grades a skill. `_score` below returns (score, used answers, not assessed),
# the same three facts the retired `functional_assessment._score_item` did.


def _competency(category: str = ppi.CATEGORY_MUST_HAVE) -> ContractSkill:
    return ContractSkill(
        id=uuid.uuid4(), name="Migration ownership", bucket=category,
        priority=1, evidence_line="",
    )


def _question(question_type: str, weight: float, payload: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        competency_id=None,
        prompt="Tell me about the migration.",
        rubric_json={"0_39": "none", "90_100": "deep"},
        ordinal=1,
        question_type=question_type,
        payload_json=payload or {},
        resume_anchor="Led the checkout migration",
        weight=weight,
    )


def _record(**fields) -> SimpleNamespace:
    base = dict(
        answer_json={}, auto_score=None, ai_evaluation_json=None,
        time_spent_seconds=None, revision_count=0,
    )
    base.update(fields)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_ledger(monkeypatch):
    """The ledger writer has its own suite; scoring here files nothing."""
    async def _record_nothing(session, **kwargs):
        return None

    monkeypatch.setattr(answer_evidence, "backfill_answer_evidence", _record_nothing)


async def _no_model(task, messages, **kwargs):
    raise AssertionError("this format is not judged by the rubric model")


async def _score(skill, questions, answers, records, *, invoke=_no_model):
    grade = await miti_items.evaluate_skill(
        None,
        context=miti_items.ItemContext(
            tenant_id=uuid.uuid4(), job_id=uuid.uuid4(), link_id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
        ),
        skill=skill,
        questions=questions,
        answers=answers,
        locators={},
        structured=records,
        invoke=invoke,
    )
    return grade.score, list(grade.used_answers), grade.status == miti_grades.ANSWER_NOT_ASSESSED


@pytest.mark.asyncio
async def test_an_objective_answer_is_scored_from_its_stored_auto_score() -> None:
    """Never re-derived, and never asked of a model: the score was computed
    deterministically on submission (spec 6.1)."""
    competency = _competency()
    question = _question(types.MCQ_SINGLE, weight=0.4)
    question.competency_id = competency.id
    record = _record(answer_json={"selected_option_id": "a"}, auto_score=1.0)
    score, used, degraded = await _score(
        competency, [question], {str(question.id): ["Selected: A write-ahead log"]},
        {str(question.id): record},
    )
    assert score == 100
    assert not degraded and used


@pytest.mark.asyncio
async def test_a_partially_correct_objective_answer_scores_in_between() -> None:
    competency = _competency()
    question = _question(types.MCQ_MULTI, weight=0.5)
    question.competency_id = competency.id
    record = _record(answer_json={"selected_option_ids": ["a"]}, auto_score=0.5)
    score, _used, _degraded = await _score(
        competency, [question], {str(question.id): ["Selected: Partition the topic"]},
        {str(question.id): record},
    )
    assert score == 50


@pytest.mark.asyncio
async def test_an_objective_question_with_no_submission_is_unanswered() -> None:
    competency = _competency()
    question = _question(types.MCQ_SINGLE, weight=0.4)
    question.competency_id = competency.id
    score, used, _degraded = await _score(competency, [question], {}, {})
    assert score == miti_items.UNANSWERED_SCORE
    assert used == []


@pytest.mark.asyncio
async def test_an_evidence_answer_is_scored_by_the_evaluation_and_stores_it(monkeypatch) -> None:
    competency = _competency()
    question = _question(types.EVIDENCE_BASED, weight=1.0)
    question.competency_id = competency.id
    record = _record(answer_json={"text": ANSWER})

    async def _evaluate(session, **kwargs):
        assert kwargs["question_type"] == types.EVIDENCE_BASED
        assert kwargs["resume_anchor"] == "Led the checkout migration"
        return agent_loop.LoopResult(value={**_valid_evidence_output(), "rubric": {}}, degraded=False)

    monkeypatch.setattr(evaluation, "evaluate", _evaluate)
    score, used, degraded = await _score(
        competency, [question], {str(question.id): [ANSWER]}, {str(question.id): record},
    )
    assert score == 78
    assert not degraded and used == [ANSWER]
    # The reasoning is persisted where the recruiter's view reads it.
    assert record.ai_evaluation_json["reasoning"] == REASONING


@pytest.mark.asyncio
async def test_a_coding_answer_is_evaluated_from_the_code_it_submitted(monkeypatch) -> None:
    competency = _competency()
    question = _question(types.CODING, weight=0.8, payload={"language": "python"})
    question.competency_id = competency.id
    record = _record(answer_json={"language": "python", "code": CODE})
    seen: dict = {}

    async def _evaluate(session, **kwargs):
        seen.update(kwargs)
        return agent_loop.LoopResult(value={**_valid_coding_output(), "rubric": {}}, degraded=False)

    monkeypatch.setattr(evaluation, "evaluate", _evaluate)
    score, used, _degraded = await _score(competency, [question], {}, {str(question.id): record})
    assert score == 71
    assert seen["language"] == "python"
    assert CODE in seen["answer_text"]
    assert used


@pytest.mark.asyncio
async def test_an_empty_coding_submission_is_unanswered() -> None:
    competency = _competency()
    question = _question(types.CODING, weight=0.8)
    question.competency_id = competency.id
    record = _record(answer_json={"language": "python", "code": "   "})
    score, used, _degraded = await _score(competency, [question], {}, {str(question.id): record})
    assert score == miti_items.UNANSWERED_SCORE and used == []


@pytest.mark.asyncio
async def test_a_degraded_evaluation_is_not_assessed_and_no_second_scorer_stands_in(
    monkeypatch,
) -> None:
    """REVERSED IN WP5-B. A degraded evaluation used to fall back to the rubric
    scorer, a second method silently standing in for the first. Now the answer
    is not assessed, nothing is stored, and no score is invented."""
    competency = _competency()
    question = _question(types.EVIDENCE_BASED, weight=1.0)
    question.competency_id = competency.id
    record = _record(answer_json={"text": ANSWER})

    async def _degraded(session, **kwargs):
        return agent_loop.LoopResult(value=None, degraded=True)

    monkeypatch.setattr(evaluation, "evaluate", _degraded)
    score, _used, not_assessed = await _score(
        competency, [question], {str(question.id): [ANSWER]}, {str(question.id): record},
    )
    assert score is None
    assert not_assessed
    assert record.ai_evaluation_json is None, "a degraded evaluation must store nothing"


@pytest.mark.asyncio
async def test_the_item_score_is_the_weighted_mean_of_its_questions(monkeypatch) -> None:
    """WHAT MAKES EVIDENCE DOMINANCE STRUCTURAL INSIDE AN ITEM. An MCQ carries
    less of the item than the evidence question beside it, by the weight the
    composer stored on the row."""
    competency = _competency()
    evidence = _question(types.EVIDENCE_BASED, weight=1.0)
    mcq = _question(types.MCQ_SINGLE, weight=0.4)
    evidence.competency_id = mcq.competency_id = competency.id

    async def _evaluate(session, **kwargs):
        return agent_loop.LoopResult(
            value={**_valid_evidence_output(), "score": 80, "rubric": {}}, degraded=False
        )

    monkeypatch.setattr(evaluation, "evaluate", _evaluate)
    records = {
        str(evidence.id): _record(answer_json={"text": ANSWER}),
        str(mcq.id): _record(answer_json={"selected_option_id": "a"}, auto_score=1.0),
    }
    score, _used, _degraded = await _score(
        competency, [evidence, mcq],
        {str(evidence.id): [ANSWER], str(mcq.id): ["Selected: A write-ahead log"]},
        records,
    )
    # (80 * 1.0 + 100 * 0.4) / 1.4
    assert score == 86
    # An unweighted mean would have been 90: the MCQ would have pulled the
    # item up as hard as the evidence question did.
    assert score != 90


def test_a_row_written_before_the_formats_reads_as_a_text_question() -> None:
    """Rows written before migration 0076 have no format column, and
    relabelling them as evidence-based would claim a provenance they lack."""
    legacy = SimpleNamespace(id=uuid.uuid4(), prompt="q", rubric_json=None)
    assert miti_items._question_type(legacy) == types.SHORT_ANSWER
    assert miti_items._question_weight(legacy) == 1.0
