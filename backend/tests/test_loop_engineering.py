"""What the loop actually bought, agent by agent.

Every generative task now runs inside `agent_loop.run_loop`. That is easy to
describe as a refactor and it is not one: each conversion closed a specific
defect that the one-shot code shipped silently. This file pins those, because a
"behaviour-preserving refactor" that quietly preserved the bugs would pass every
other test in the suite.

`tests/test_agent_loop.py` covers the harness itself.
"""
from __future__ import annotations

import json

import pytest

from app.services import agent_loop
from app.services import functional_assessment as fa
from app.services import gap_analysis
from app.services import interviewer


# ── bounded_remark: the string a client actually reads ───────────────────────


@pytest.mark.asyncio
async def test_a_remark_outside_the_word_contract_is_regenerated(monkeypatch) -> None:
    calls: list[list[dict]] = []

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        if len(calls) == 1:
            return "Too short."
        return "query " + " ".join(["word"] * 26)

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    out = await fa.bounded_remark(None, "PostgreSQL", "they tuned the query plan", 25, 30)

    assert 25 <= fa.word_count(out) <= 30
    assert len(calls) == 2
    # The correction names the actual count, which is what makes it actionable.
    assert "25" in calls[1][-1]["content"] and "30" in calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_corrections_do_not_accumulate_into_one_prompt(monkeypatch) -> None:
    """The hand-rolled loop did `prompt += correction`, so a second miss left
    the model reading two contradictory instructions at once."""
    corrections: list[str] = []

    async def _chat(task_type, messages, **k):
        last = messages[-1]["content"]
        if "rejected" in last.lower():
            corrections.append(last)
        return "short"

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    await fa.bounded_remark(None, "PostgreSQL", "evidence", 25, 30)

    # Every correction is a self-contained turn naming exactly one word count.
    for text in corrections:
        assert text.count("the previous attempt was") == 1


@pytest.mark.asyncio
async def test_one_transient_failure_no_longer_abandons_the_remark(monkeypatch) -> None:
    """The old loop did `except: break`, so a single provider blip on attempt
    one shipped the canned fallback for the most client-visible string in the
    product, even though attempt two would have worked."""
    calls = {"n": 0}

    async def _chat(task_type, messages, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient 503")
        return "evidence " + " ".join(["word"] * 26)

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    out = await fa.bounded_remark(None, "PostgreSQL", "evidence", 25, 30)

    assert calls["n"] == 2
    assert out == "evidence " + " ".join(["word"] * 26)
    assert out != fa._fallback_remark_25("PostgreSQL")


@pytest.mark.asyncio
async def test_a_remark_that_states_a_score_is_rejected(monkeypatch) -> None:
    """NOTHING checked this before. The prompt asked for no score or grade, and
    a prompt instruction is a request rather than a guarantee -- which is the
    same reasoning that puts a Postgres CHECK behind the Culture ban.

    A remark is prose written by a model that has just been shown a candidate's
    answers and asked to assess them, which is precisely where "scored 8/10"
    comes from, and it goes straight into a document a client reads.
    """
    attempts: list[str] = []
    clean = " ".join(["evidence"] * 27)

    async def _chat(task_type, messages, **k):
        attempts.append("call")
        if len(attempts) == 1:
            return (
                "The candidate scored 8/10 on this dimension and demonstrated "
                + " ".join(["solid"] * 20)
            )
        return clean

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    out = await fa.bounded_remark(None, "PostgreSQL", "evidence", 25, 30)

    assert len(attempts) == 2
    assert out == clean
    assert "8/10" not in out


@pytest.mark.asyncio
async def test_a_total_outage_still_returns_the_canned_remark(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(fa.llm_router, "chat_completion", _boom)
    out = await fa.bounded_remark(None, "PostgreSQL", "evidence", 45, 50)
    assert 45 <= fa.word_count(out) <= 50
    assert agent_loop.banned_phrase_gate(
        out, fa.REPORT_BANNED_PHRASES
    ).ok


@pytest.mark.asyncio
async def test_report_remark_revises_a_banned_template_phrase(monkeypatch) -> None:
    attempts: list[list[dict]] = []
    rejected = (
        "The conversation produced usable evidence for PostgreSQL and the "
        + " ".join(["candidate"] * 19)
    )
    accepted = "PostgreSQL query planning " + " ".join(["evidence"] * 24)

    async def _chat(task_type, messages, **kwargs):
        attempts.append(messages)
        return rejected if len(attempts) == 1 else accepted

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    out = await fa.bounded_remark(
        None,
        "PostgreSQL",
        "The candidate explained PostgreSQL query planning.",
        25,
        30,
    )

    assert out == accepted
    assert len(attempts) == 2
    assert "banned" in attempts[1][-1]["content"].lower()


@pytest.mark.asyncio
async def test_gap_probes_use_the_loop_and_are_re_asked_when_they_break_a_rule(
    monkeypatch,
) -> None:
    """The probe generator moved to `services/gap_analysis` with Draft v4.

    What did not move is why it is a LOOP: "your probe was 9 words and I need 25
    to 30" is a defect a model fixes when told, and the one-shot code it
    replaced threw the response away and shipped a deterministic probe instead.
    """
    attempts: list[list[dict]] = []
    item = {
        "category": "must_have",
        "name": "Capability A",
        # Moderately Matching: one probe. A Not Matching Must-have earns two,
        # and this test is about the WORD rule, not the count rule.
        "score": 65,
        "ordinal": 1,
        "remark": "Evidence gap for capability A.",
    }
    valid = (
        "You mentioned rebuilding the ingest path yourself, so walk me through "
        "the constraint that forced you to abandon your first design and what "
        "you measured afterwards."
    )

    async def _chat(task_type, messages, **kwargs):
        attempts.append(messages)
        payload = (
            {"probes": ["Too short."]}
            if len(attempts) == 1
            else {"probes": [valid]}
        )
        return json.dumps(payload)

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _chat)
    section = await gap_analysis.build_gap_analysis(
        None,
        [item],
        {
            "Capability A": [
                {
                    "question": "How did you approach the ingest rebuild?",
                    "answer": "I rebuilt the ingest path myself over two sprints.",
                }
            ]
        },
    )

    probes = section["groups"][0]["items"][0]["probes"]
    assert probes == [valid]
    assert len(attempts) == 2
    # The correction names the rule and the count it actually produced.
    correction = attempts[1][-1]["content"]
    assert "25 to 30 words" in correction


@pytest.mark.asyncio
async def test_a_probe_that_repeats_the_original_question_is_re_asked(
    monkeypatch,
) -> None:
    """Spec §9.6: a probe must not repeat the wording of the question the
    candidate was already asked. The interviewer is going somewhere NEW with an
    answer that was already given."""
    attempts: list[list[dict]] = []
    asked = "Walk me through how you tuned Kafka consumer lag in production."
    good = (
        "You mentioned shrinking the consumer group, so tell me what you would "
        "have done instead had the partition count been fixed for you by an "
        "entirely different team."
    )

    async def _chat(task_type, messages, **kwargs):
        attempts.append(messages)
        payload = {"probes": [asked]} if len(attempts) == 1 else {"probes": [good]}
        return json.dumps(payload)

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _chat)
    section = await gap_analysis.build_gap_analysis(
        None,
        [{"category": "must_have", "name": "Kafka", "score": 65, "ordinal": 1,
          "remark": "Some evidence."}],
        {"Kafka": [{"question": asked,
                    "answer": "I shrank the consumer group and repartitioned."}]},
    )
    assert section["groups"][0]["items"][0]["probes"] == [good]
    assert len(attempts) == 2
    assert "already asked" in attempts[1][-1]["content"]


# ── Sutra's loops ────────────────────────────────────────────────────────────
#
# The naming loop that lived here went with the matrix compiler (Vivekium
# release). Sutra's two remaining loops, the skills draft and the hidden
# assessment context, are tested in `test_job_skills_draft.py` and
# `test_job_skills_save.py`, including the two properties these tests pinned:
# a rejection is fed back verbatim and re-asked, and an outage invents nothing.


# ── The question writer: a rejection is a defect the model is told about ─────


def _question(prompt: str = "Describe a system you designed end to end."):
    """One question row, its skill and the Vaada context, with no session."""
    import uuid

    from app.models.assessment import CandidateQuestion, JobCompetency
    from app.models.job import Job
    from app.services import assessment_contract
    from app.services.vaada_context import VaadaContext

    job = Job(id=uuid.uuid4(), title="Senior Backend Engineer", jd_markdown="Kafka ingest.")
    competency = JobCompetency(
        id=uuid.uuid4(), name="Kafka", category="must_have", description="Kafka."
    )
    row = CandidateQuestion(
        id=uuid.uuid4(), job_candidate_link_id=uuid.uuid4(), competency_id=competency.id,
        ordinal=0, prompt=prompt, rubric_json=None, question_type="short_answer",
    )
    skill = assessment_contract.ContractSkill(
        id=competency.id, name="Kafka", bucket="must_have", priority=1,
        evidence_line="Has run Kafka consumers under real load.",
    )
    contract = assessment_contract.AssessmentContract(
        job_id=job.id, version=1, locked=True, skills=(skill,), role_summary="Ingest.",
        digest="0" * 64, grade="non_managerial", locked_at=None,
    )
    return job, row, competency, VaadaContext(
        contract=contract, skill=skill, role_summary=contract.role_summary
    )


_RUBRIC = {
    "0_39": "No partitioning decision.",
    "40_59": "Names partitions only.",
    "60_74": "One real sizing decision.",
    "75_89": "Sized from measured lag.",
    "90_100": "Trades ordering and rebalancing with outcomes.",
}


@pytest.mark.asyncio
async def test_a_repeated_question_is_re_asked_then_nothing_is_persisted(monkeypatch) -> None:
    """A repeat is a criterion of the writer's own loop: the model is told, and
    a result that still repeats is DEGRADED and writes nothing. Before
    2026-09-24 the repeat was persisted with a new rubric and then hidden, so
    the candidate was graded against a question they never read."""
    from app.services import ppi_interview

    asked = "How did you size the Kafka partitions when consumer lag grew?"
    calls: list[list[dict]] = []

    async def _invoke(task_type, messages, **k):
        calls.append(messages)
        return json.dumps({"question": asked, "rubric": _RUBRIC})

    monkeypatch.setattr(ppi_interview.llm_router, "invoke_llm", _invoke)
    job, row, competency, context = _question()
    result = await ppi_interview.write_question(
        session=None, job=job, row=row, competency=competency, context=context,
        asked_before=[asked],
    )
    assert len(calls) == 2, "the repeat was not fed back for a second attempt"
    assert "already been asked" in calls[1][-1]["content"]
    assert result.degraded
    assert row.prompt == "Describe a system you designed end to end."
    assert row.rubric_json is None and row.generated_at is None


@pytest.mark.asyncio
async def test_a_second_attempt_that_fixes_the_repeat_is_persisted_with_its_rubric(
    monkeypatch,
) -> None:
    from app.services import ppi_interview

    asked = "How did you size the Kafka partitions when consumer lag grew?"
    fixed = "What did the Kafka consumer lag look like before you rebalanced?"
    answers = iter([asked, fixed])

    async def _invoke(*a, **k):
        return json.dumps({"question": next(answers), "rubric": _RUBRIC})

    monkeypatch.setattr(ppi_interview.llm_router, "invoke_llm", _invoke)
    job, row, competency, context = _question()
    result = await ppi_interview.write_question(
        session=None, job=job, row=row, competency=competency, context=context,
        asked_before=[asked],
    )
    assert not result.degraded
    assert row.prompt == fixed and row.rubric_json == _RUBRIC
    assert row.generated_at is not None


@pytest.mark.asyncio
async def test_an_outage_costs_the_question_and_nothing_else(monkeypatch) -> None:
    from app.services import ppi_interview

    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(ppi_interview.llm_router, "invoke_llm", _boom)
    job, row, competency, context = _question()
    result = await ppi_interview.write_question(
        session=None, job=job, row=row, competency=competency, context=context,
    )
    assert result.degraded
    assert row.prompt == "Describe a system you designed end to end."
    assert row.generated_at is None
