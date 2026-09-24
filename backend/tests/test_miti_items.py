"""Miti's item stage: every per-skill grade in the product (WP5-B).

What is pinned here, one section each:

  1. THE STORED RUBRIC. A Must-have or Nice-to-have prose answer is judged on
     the `answer_evaluation` task against the rubric written WITH the question
     the candidate was shown (`candidate_questions.rubric_json`), and that exact
     text is in the prompt. A row with no rubric uses the general standard and
     the method says so.
  2. BEHAVIOURAL is one judgement across every substantive answer, against the
     shared standard.
  3. UNANSWERED is a fact about the candidate: 25, Not Matching, and no model
     call. An unanswered Must-have is a failed Must-have (O5-1).
  4. A MODEL FAILURE IS NOT ASSESSED: no score, no grade, the failure's class
     name recorded and logged. There is no hash fallback, and no default score.
  5. The format decides the source: objective answers read `auto_score`,
     evidence and coding answers read the evaluation with reasoning.
  6. The scoring node refuses to write a report for an application with a skill
     Miti could not assess, before a single remark is paid for.

No database and no provider: the model is injected, and the ledger writer is
recorded rather than reached.
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.config import llm_providers
from app.services import agent_loop, rating
from app.services import functional_assessment as fa
from app.services.assessment_contract import ContractSkill
from app.services.assessment_formats import evaluation as format_evaluation
from app.services.assessment_formats import types as question_types
from app.services.assessment_pipeline import evidence as answer_evidence
from app.services.miti import grades, items
from app.services.miti import live as miti_live
from app.services.ppi_interview import DEFAULT_RUBRIC
from tests import miti_fixtures as mf

_ANSWER = "I rebuilt the ingest pipeline and cut the nightly batch from hours to minutes."
_RUBRIC = {
    "0_39": "No relevant example.",
    "90_100": "Explains the GIL trade-off and when multiprocessing beats threads.",
}


# ── Harness ──────────────────────────────────────────────────────────────────


class _Judge:
    """The injected model. Records every call; answers from a script."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses) or ['{"score": 82, "band": "75_89"}']
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def __call__(self, task, messages, **kwargs):
        self.calls.append((task, messages))
        response = self.responses[min(len(self.calls), len(self.responses)) - 1]
        if isinstance(response, BaseException):
            raise response
        return response

    def system_prompts(self) -> list[str]:
        return [messages[0]["content"] for _task, messages in self.calls]


@pytest.fixture(autouse=True)
def _ledger(monkeypatch) -> list[dict]:
    """The ledger writer is exercised by its own suite; here it is recorded."""
    written: list[dict] = []

    async def _record(session, **kwargs):
        written.append(kwargs)

    monkeypatch.setattr(answer_evidence, "backfill_answer_evidence", _record)
    return written


def _context() -> items.ItemContext:
    return items.ItemContext(
        tenant_id=uuid.uuid4(), job_id=uuid.uuid4(), link_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
    )


def _skill(name: str = "Python", bucket: str = "must_have") -> ContractSkill:
    return ContractSkill(
        id=mf.skill_id(name), name=name, bucket=bucket, priority=1,
        evidence_line="Explains the trade-off it made.",
    )


def _question(skill: ContractSkill, *, rubric=_RUBRIC, question_type=None, weight=None, payload=None):
    return SimpleNamespace(
        id=uuid.uuid4(), competency_id=skill.id, prompt=f"Tell me about {skill.name}.",
        rubric_json=rubric, question_type=question_type, weight=weight,
        payload_json=payload or {}, resume_anchor=None,
    )


async def _grade(skill, questions, answers, *, judge, structured=None):
    return await items.evaluate_skill(
        None,
        context=_context(),
        skill=skill,
        questions=questions,
        answers=answers,
        locators={},
        structured=structured or {},
        invoke=judge,
    )


# ── 1. the stored rubric ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_must_have_answer_is_judged_against_its_own_questions_stored_rubric() -> None:
    skill = _skill()
    question = _question(skill)
    judge = _Judge('{"score": 82, "band": "75_89"}')

    grade = await _grade(skill, [question], {str(question.id): [_ANSWER]}, judge=judge)

    assert (grade.status, grade.score, grade.grade) == ("graded", 82, rating.GRADE_MATCHING)
    assert grade.items[0].method == grades.METHOD_RUBRIC
    assert [task for task, _ in judge.calls] == [items.EVALUATION_TASK]
    prompt = judge.system_prompts()[0]
    # EVERY band of the stored rubric, verbatim, and none of the general one.
    for text in _RUBRIC.values():
        assert text in prompt
    for text in DEFAULT_RUBRIC.values():
        assert text not in prompt
    # The question the candidate was shown is what the judge is told it was.
    assert question.prompt in judge.calls[0][1][1]["content"]


@pytest.mark.asyncio
async def test_a_row_with_no_rubric_uses_the_general_standard_and_says_so() -> None:
    skill = _skill()
    question = _question(skill, rubric={})
    judge = _Judge('{"score": 70}')

    grade = await _grade(skill, [question], {str(question.id): [_ANSWER]}, judge=judge)

    assert grade.items[0].method == grades.METHOD_GENERAL_STANDARD
    assert all(text in judge.system_prompts()[0] for text in DEFAULT_RUBRIC.values())


def test_the_item_task_is_the_judging_tier_at_temperature_zero() -> None:
    assert items.EVALUATION_TASK == "answer_evaluation"
    assert items.EVALUATION_TASK in llm_providers.MODEL_FOR_TASK
    assert llm_providers.TASK_TEMPERATURE[items.EVALUATION_TASK] == 0.0


@pytest.mark.asyncio
async def test_candidate_text_is_fenced_as_data_in_the_judging_prompt() -> None:
    from app.prompts import fragments

    skill = _skill()
    question = _question(skill)
    judge = _Judge()
    await _grade(skill, [question], {str(question.id): [_ANSWER]}, judge=judge)
    assert fragments.CANDIDATE_TEXT_IS_DATA in judge.system_prompts()[0]


# ── 2. behavioural ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_behavioural_skill_is_one_judgement_against_the_standard() -> None:
    skill = _skill("Ownership", "behavioural")
    first, second = _question(skill, rubric=None), _question(skill, rubric=None)
    judge = _Judge('{"score": 71}')

    grade = await _grade(
        skill,
        [first, second],
        {str(first.id): [_ANSWER], str(second.id): ["I stayed on the migration until the last consumer cut over."]},
        judge=judge,
    )

    assert len(judge.calls) == 1, "one judgement across the skill, not one per answer"
    assert grade.score == 71
    assert grade.items[0].method == grades.METHOD_BEHAVIOURAL_STANDARD
    assert all(text in judge.system_prompts()[0] for text in items.BEHAVIOURAL_STANDARD.values())


# ── 3. unanswered ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("given", [None, "ewidjverip", "   "])
async def test_an_unanswered_skill_is_not_matching_and_calls_no_model(given) -> None:
    skill = _skill()
    question = _question(skill)
    judge = _Judge()
    answers = {} if given is None else {str(question.id): [given]}

    grade = await _grade(skill, [question], answers, judge=judge)

    assert judge.calls == []
    assert grade.status == grades.ANSWER_UNANSWERED
    assert grade.score == items.UNANSWERED_SCORE
    assert grade.grade == rating.GRADE_NOT


@pytest.mark.asyncio
async def test_an_unanswered_must_have_counts_as_failed_and_caps_the_overall() -> None:
    """O5-1: asked and gave nothing is Not Matching, and a Must-have graded Not
    Matching is failed, which caps the overall at `must_have_ceiling()`."""
    from app.services.miti import aggregation, caps

    skill = _skill()
    unanswered = await _grade(skill, [_question(skill)], {}, judge=_Judge())
    out = aggregation.aggregate(
        [],
        skill_grades=(unanswered, mf.skill("Go", "must_have", 99, priority=2),
                      mf.skill("Ownership", "behavioural", 99)),
        must_have_evidence={
            name: aggregation.MustHaveEvidence(tiers=("E3",), independence_groups=2)
            for name in ("Python", "Go")
        },
    )
    assert out.must_have_failed
    assert caps.CONTROL_COMPETENCY_THRESHOLD in {cap.control for cap in out.applied_caps}
    assert out.delivered_score <= caps.must_have_ceiling()


# ── 4. a model failure is not assessed ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_model_outage_never_produces_a_score(caplog) -> None:
    skill = _skill()
    question = _question(skill)
    judge = _Judge(RuntimeError("provider down"))

    with caplog.at_level(logging.WARNING, logger="app.services.miti.items"):
        grade = await _grade(skill, [question], {str(question.id): [_ANSWER]}, judge=judge)

    assert grade.status == grades.ANSWER_NOT_ASSESSED
    assert grade.score is None and grade.grade is None
    assert grade.items[0].failure == "RuntimeError"
    assert len(judge.calls) == agent_loop.BACKGROUND_ATTEMPTS
    assert any("miti.item_not_assessed" in record.getMessage() for record in caplog.records)
    # The class name only: an exception message can quote the answer.
    assert all("provider down" not in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not json", '{"score": 140}', '{"score": "high"}', "[82]"])
async def test_an_unusable_judgement_is_asked_again_then_not_assessed(response) -> None:
    skill = _skill()
    question = _question(skill)
    judge = _Judge(response)

    grade = await _grade(skill, [question], {str(question.id): [_ANSWER]}, judge=judge)

    assert grade.status == grades.ANSWER_NOT_ASSESSED
    assert len(judge.calls) == agent_loop.BACKGROUND_ATTEMPTS
    # The critic's instruction was fed back on the second attempt.
    assert "JSON object" in judge.calls[1][1][-1]["content"]


@pytest.mark.asyncio
async def test_a_skill_with_some_answers_evaluated_is_graded_on_those_and_flagged() -> None:
    skill = _skill()
    first, second = _question(skill), _question(skill)
    judge = _Judge('{"score": 90}', RuntimeError("down"))

    grade = await _grade(
        skill, [first, second],
        {str(first.id): [_ANSWER], str(second.id): [_ANSWER]},
        judge=judge,
    )

    assert grade.status == grades.ANSWER_GRADED
    assert grade.score == 90
    assert grade.partially_assessed is True


@pytest.mark.asyncio
async def test_a_skill_with_no_question_issued_is_not_assessed_rather_than_failed() -> None:
    """Never asked is not "asked and did not answer": grading it Not Matching
    would fail an essential skill over a platform defect."""
    grade = await _grade(_skill(), [], {}, judge=_Judge())
    assert grade.status == grades.ANSWER_NOT_ASSESSED
    assert grade.items[0].failure == items.FAILURE_NO_QUESTION


def test_the_item_stage_has_no_hash_and_no_default_score() -> None:
    import inspect

    source = inspect.getsource(items)
    for banned in ("hashlib", "sha256", "_stable", "random"):
        assert banned not in source, banned


# ── 5. the format decides the source ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_objective_answer_reads_its_auto_score_and_calls_no_model() -> None:
    skill = _skill()
    answered = _question(skill, question_type=question_types.MCQ_SINGLE)
    missing = _question(skill, question_type=question_types.MCQ_SINGLE)
    judge = _Judge()
    structured = {
        str(answered.id): SimpleNamespace(auto_score=0.8, answer_json={"selected": ["b"]}),
    }

    grade = await _grade(
        skill, [answered, missing], {str(answered.id): ["b"]}, judge=judge, structured=structured
    )

    assert judge.calls == []
    assert [(item.status, item.score) for item in grade.items] == [
        (grades.ANSWER_GRADED, 80),
        (grades.ANSWER_UNANSWERED, items.UNANSWERED_SCORE),
    ]


def _loop_result(value, degraded=False):
    return agent_loop.LoopResult(value=value, degraded=degraded, attempts=1)


@pytest.mark.asyncio
async def test_an_evidence_answer_reads_the_evaluation_and_stores_its_reasoning(monkeypatch) -> None:
    async def _evaluate(session, **kwargs):
        assert kwargs["question_rubric"] == _RUBRIC, "the stored rubric reaches the evaluation"
        return _loop_result({"score": 77, "reasoning": "It names the constraint."})

    monkeypatch.setattr(format_evaluation, "evaluate", _evaluate)
    skill = _skill()
    question = _question(skill, question_type=question_types.EVIDENCE_BASED)
    record = SimpleNamespace(auto_score=None, answer_json={}, ai_evaluation_json=None)

    grade = await _grade(
        skill, [question], {str(question.id): [_ANSWER]}, judge=_Judge(),
        structured={str(question.id): record},
    )

    assert grade.score == 77
    assert grade.items[0].method == grades.METHOD_EVIDENCE
    assert record.ai_evaluation_json["reasoning"] == "It names the constraint."


@pytest.mark.asyncio
async def test_a_degraded_evidence_evaluation_is_not_assessed_and_stores_nothing(monkeypatch) -> None:
    async def _evaluate(session, **kwargs):
        return _loop_result(None, degraded=True)

    monkeypatch.setattr(format_evaluation, "evaluate", _evaluate)
    skill = _skill()
    question = _question(skill, question_type=question_types.EVIDENCE_BASED)
    record = SimpleNamespace(auto_score=None, answer_json={}, ai_evaluation_json=None)
    judge = _Judge()

    grade = await _grade(
        skill, [question], {str(question.id): [_ANSWER]}, judge=judge,
        structured={str(question.id): record},
    )

    assert grade.status == grades.ANSWER_NOT_ASSESSED
    assert record.ai_evaluation_json is None
    assert judge.calls == [], "no second scorer stands in for the evaluation"


@pytest.mark.asyncio
async def test_a_coding_question_with_no_code_is_unanswered(monkeypatch) -> None:
    skill = _skill()
    question = _question(skill, question_type=question_types.CODING)
    grade = await _grade(
        skill, [question], {}, judge=_Judge(),
        structured={str(question.id): SimpleNamespace(auto_score=None, answer_json={"code": "  "})},
    )
    assert grade.status == grades.ANSWER_UNANSWERED


# ── The skill set and parsing ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_contract_skill_is_graded_in_contract_order() -> None:
    skills = [_skill("Kafka"), _skill("Go", "nice_to_have"), _skill("Ownership", "behavioural")]
    questions = [_question(skill) for skill in reversed(skills)]
    answers = {str(question.id): [_ANSWER] for question in questions}

    out = await items.evaluate_skills(
        None, context=_context(), skills=skills, questions=questions, answers=answers,
        locators={}, structured={}, invoke=_Judge('{"score": 80}'),
    )

    assert [grade.name for grade in out] == ["Kafka", "Go", "Ownership"]
    assert all(grade.score == 80 for grade in out)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [('{"score": 82}', 82), ('{"score": 81.6}', 82), ('{"score": 0}', 0),
     ('{"score": 101}', None), ('{"score": -1}', None), ('{"score": true}', None),
     ('{"band": "x"}', None), ("", None)],
)
def test_parse_score_accepts_only_a_usable_score(raw, expected) -> None:
    assert items.parse_score(raw) == expected


# ── 6. the scoring node refuses an incomplete run ────────────────────────────


class _Session:
    async def get(self, model, ident):
        return None


def _state(conversation=True):
    job = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Engineer")
    link = SimpleNamespace(id=uuid.uuid4(), candidate_id=uuid.uuid4())
    state = {
        "session": _Session(), "job": job, "link": link, "candidate_questions": [],
        "answers": {}, "answer_refs": {}, "structured_answers": {}, "transcript": [],
    }
    if conversation:
        state["conversation"] = SimpleNamespace(id=uuid.uuid4())
    return state


@pytest.mark.asyncio
async def test_the_scoring_node_writes_no_remark_when_a_skill_was_not_assessed(monkeypatch) -> None:
    remarks: list[str] = []

    async def _remark(*args, **kwargs):
        remarks.append(args[1])
        return "remark"

    async def _evaluate(session, **kwargs):
        return miti_live.MitiResult(
            contract=mf.contract(),
            skills=(mf.skill("Kafka", score=None), mf.skill("Ownership", "behavioural", 80)),
        )

    monkeypatch.setattr(fa, "bounded_remark", _remark)
    monkeypatch.setattr(miti_live, "evaluate_application", _evaluate)

    with pytest.raises(fa.SkillsNotAssessed) as raised:
        await fa.ppi_scoring_node(_state())
    assert remarks == []
    assert "Kafka" not in str(raised.value), "skill names are job content"


@pytest.mark.asyncio
async def test_the_scoring_node_lays_out_miti_grades_in_contract_order(monkeypatch) -> None:
    async def _remark(session, name, evidence, minimum, maximum, *, rating=None):
        return f"remark for {name} at {rating}"

    skills = (
        mf.skill("Kafka", "must_have", 92),
        mf.unanswered("Go", "nice_to_have"),
        mf.skill("Ownership", "behavioural", 70),
    )
    skills = tuple(
        dataclasses.replace(skill, used_answers=() if skill.status == "unanswered" else (_ANSWER,))
        for skill in skills
    )

    async def _evaluate(session, **kwargs):
        return miti_live.MitiResult(contract=mf.contract(), skills=skills)

    async def _no_claims(session, **kwargs):
        return []

    from app.services.evidence import ledger

    monkeypatch.setattr(ledger, "load_claims", _no_claims)
    monkeypatch.setattr(fa, "bounded_remark", _remark)
    monkeypatch.setattr(miti_live, "evaluate_application", _evaluate)

    out = await fa.ppi_scoring_node(_state())

    assert [(row["name"], row["category"], row["score"]) for row in out["ppi"]] == [
        ("Kafka", "must_have", 92), ("Go", "nice_to_have", 25), ("Ownership", "behavioural", 70),
    ]
    assert out["ppi"][0]["remark"] == f"remark for Kafka at {rating.GRADE_HIGHLY}"
    assert "No substantive answer" in out["ppi"][1]["remark"]
    assert all(row["required_level"] is None for row in out["ppi"])
    assert out["ppi_mode"] == fa.MODE_MITI
    assert out["miti"].skills == skills
    # The report-side helpers read the LOCKED contract's skills, never live rows.
    assert [skill.name for skill in out["competencies"]] == ["Kafka", "Ownership"]


@pytest.mark.asyncio
async def test_the_scoring_node_refuses_without_a_conversation() -> None:
    with pytest.raises(miti_live.ScorecardUnavailable):
        await fa.ppi_scoring_node(_state(conversation=False))


def test_the_skill_grade_statuses_tie_a_score_to_an_assessment() -> None:
    with pytest.raises(ValueError):
        grades.SkillGrade(skill_id=uuid.uuid4(), name="x", bucket="must_have", priority=1,
                          status="not_assessed", score=40, grade=rating.GRADE_NOT)
    with pytest.raises(ValueError):
        grades.ItemEvaluation(question_id=None, status="graded", score=None, weight=1.0,
                              method=grades.METHOD_RUBRIC)


def test_json_scoring_prompt_is_versioned() -> None:
    from app.prompts import registry

    assert registry.version(items.SCORING_PROMPT).startswith("2+")
