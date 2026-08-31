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
import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_loop
from app.services import functional_assessment as fa
from app.services import gap_analysis
from app.services import interviewer, ppi


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


# ── The PPI matrix: filler a human has to fix by hand ────────────────────────
# ── The PPI framework: filler a human has to fix by hand ─────────────────────


def _job():
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Backend Engineer",
        level=None, jd_json={"skills": ["Python", "PostgreSQL", "Kafka"]},
        jd_markdown="Own the ingestion platform.",
        experience_min_years=3, experience_max_years=7,
        assessment_grade="non_managerial", assessment_status="questions_pending_review",
        framework_generated_at=None, framework_approved_at=None,
    )


# ── Sutra's naming loop (stages 1 and 2) ─────────────────────────────────────
#
# These four tests used to exercise `ppi.generate_framework`, which is deleted
# (spec-doc6 D1). The loop it ran inside did not go anywhere: Sutra still asks a
# model for exactly the two stages that need judgment -- naming a competency
# from a hiring manager's prose, and writing the observable-evidence statement
# for it -- inside `agent_loop.run_loop`, with a deterministic evaluator.
#
# What CHANGED is the fallback, and it is the whole point of the rewrite. The
# old loop fell back to a matrix assembled from the JD's own noun phrases, so an
# outage produced criteria that looked reviewed and were not. This one falls
# back to NOTHING, and every phrase it could not name comes back as a recorded
# refusal naming the stage that refused it.


def _pending(*phrases: str):
    from app.services.hiring.scorecard import _Candidate

    return [
        _Candidate(
            phrase=phrase,
            category=ppi.CATEGORY_MUST_HAVE,
            quadrant="weaknesses",
            swot_origin=phrase,
        )
        for phrase in phrases
    ]


def _generic_model():
    from app.services.hiring.department_models import department_for

    return department_for("generic")


@pytest.mark.asyncio
async def test_a_rejected_naming_is_fed_back_verbatim_and_re_asked(monkeypatch) -> None:
    """The reason the loop exists at all.

    "you returned a whole sentence where a competency name goes" is a defect a
    model fixes when it is told, and the one-shot code this replaced threw the
    response away.
    """
    from app.services.hiring import scorecard

    calls: list[list[dict]] = []

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "named": [
                        {
                            "index": 0,
                            "competency": (
                                "the person needs to be able to own production "
                                "incidents from start to finish without help"
                            ),
                            "observable": "Has carried production on-call and led an incident.",
                        }
                    ],
                    "refused": [],
                }
            )
        return json.dumps(
            {
                "named": [
                    {
                        "index": 0,
                        "competency": "Production incident ownership",
                        "observable": "Has carried production on-call and led an incident.",
                    }
                ],
                "refused": [],
            }
        )

    monkeypatch.setattr(scorecard.llm_router, "chat_completion", _chat)
    named, refusals, degraded = await scorecard._name_unanchored(
        None,
        _job(),
        _pending("nobody here has ever been paged for the scheduler"),
        _generic_model(),
        "non_managerial",
    )
    assert len(calls) == 2
    correction = calls[1][-1]["content"]
    assert "competency name" in correction
    assert named[0][0] == "Production incident ownership"
    assert refusals == []
    assert degraded is False


@pytest.mark.asyncio
async def test_an_adjective_observable_is_refused_by_the_same_detector(
    monkeypatch,
) -> None:
    """The model is held to the bar §18.5 rule 4 holds the hiring manager to.

    One detector, `company_dna.is_observable`, used by the DNA instrument, the
    SWOT quality rules and this evaluator. Two copies would drift, and the drift
    would be invisible: one surface accepting what another refuses.
    """
    from app.services.hiring import scorecard

    calls: list[list[dict]] = []

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        return json.dumps(
            {
                "named": [
                    {
                        "index": 0,
                        "competency": "Ownership",
                        "observable": "Has a strong ownership mindset.",
                    }
                ],
                "refused": [],
            }
        )

    monkeypatch.setattr(scorecard.llm_router, "chat_completion", _chat)
    named, refusals, degraded = await scorecard._name_unanchored(
        None, _job(), _pending("people here do not follow through"),
        _generic_model(), "non_managerial",
    )
    # Rejected every attempt, so nothing was named and the phrase is recorded as
    # refused rather than admitted with an adjective standing in for evidence.
    assert named == {}
    assert len(refusals) == 1
    assert refusals[0]["stage"] == "competency"
    assert degraded is True
    assert "watched happen" in calls[-1][-1]["content"]


@pytest.mark.asyncio
async def test_a_total_outage_invents_no_competency(monkeypatch) -> None:
    """THE CHANGE THIS PHASE MADE, stated as a test.

    The old loop's fallback was a matrix built from the JD's own noun phrases,
    which was reviewed, approved and graded against for the life of the job with
    nothing in the output saying it had degraded. There is no fallback now: an
    outage costs the naming, and every phrase comes back refused with the reason
    stated.
    """
    from app.services.hiring import scorecard

    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(scorecard.llm_router, "chat_completion", _boom)
    named, refusals, degraded = await scorecard._name_unanchored(
        None, _job(), _pending("alpha phrase", "bravo phrase"),
        _generic_model(), "non_managerial",
    )
    assert named == {}
    assert [row["phrase"] for row in refusals] == ["alpha phrase", "bravo phrase"]
    assert degraded is True
    assert all(row["reason"] for row in refusals)


@pytest.mark.asyncio
async def test_a_phrase_the_model_refuses_carries_the_models_own_reason(
    monkeypatch,
) -> None:
    """Refusing is a correct answer, and the reason belongs to the reviewer.

    A phrase about the market rather than the role names no capability, and
    inventing one would put a criterion on the scorecard nobody stated. The
    hiring manager is told which of their sentences produced nothing and why.
    """
    from app.services.hiring import scorecard

    async def _chat(task_type, messages, **k):
        return json.dumps(
            {
                "named": [],
                "refused": [
                    {"index": 0, "reason": "This is about the market, not the role."}
                ],
            }
        )

    monkeypatch.setattr(scorecard.llm_router, "chat_completion", _chat)
    named, refusals, degraded = await scorecard._name_unanchored(
        None, _job(), _pending("salaries here are not competitive"),
        _generic_model(), "non_managerial",
    )
    assert named == {}
    assert refusals[0]["reason"] == "This is about the market, not the role."
    assert degraded is False


# ── The interviewer: a rejection that used to be indistinguishable from an outage ──


@pytest.mark.asyncio
async def test_a_reword_that_dropped_a_named_technology_is_re_asked(monkeypatch) -> None:
    """`_substance_preserved` has always rejected this, and the rejection fell
    straight through to the stored text -- so "the model said 'a message queue'
    instead of 'Kafka'" and "every provider is down" had the same outcome, and
    the candidate read a scripted line either way."""
    calls: list[list[dict]] = []
    stored = "Walk me through how you tuned Kafka consumer lag."

    async def _invoke(task_type, messages, **k):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"question": "How did you tune the message queue?"})
        return json.dumps(
            {"question": "You mentioned ingestion earlier, so how did you tune Kafka consumer lag?"}
        )

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _invoke)
    out = await interviewer.compose_next_question(
        session=None,
        question=stored,
        transcript=[{"speaker": "candidate", "content": "I own the ingestion platform."}],
        mode=interviewer.MODE_REWORD,
    )

    assert len(calls) == 2
    assert "kafka" in calls[1][-1]["content"].lower()
    assert "Kafka" in out
    assert out != stored


@pytest.mark.asyncio
async def test_a_reword_that_stays_wrong_still_falls_back_to_the_stored_text(monkeypatch) -> None:
    """The loop adds a second chance, not a lower bar. A question that would be
    graded against a rubric it no longer matches must never be asked."""
    stored = "Walk me through how you tuned Kafka consumer lag."

    async def _invoke(*a, **k):
        return json.dumps({"question": "How did you tune the message queue?"})

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _invoke)
    out = await interviewer.compose_next_question(
        session=None,
        question=stored,
        transcript=[{"speaker": "candidate", "content": "I own the ingestion platform."}],
        mode=interviewer.MODE_REWORD,
    )
    assert out == stored


@pytest.mark.asyncio
async def test_a_generated_repeat_is_re_asked_then_falls_back(monkeypatch) -> None:
    # Deliberately carries specific terms. `_is_repeat` compares on the tokens
    # `interviewer._tokens` protects -- digits, internal punctuation and
    # mid-sentence capitals -- so a question of entirely ordinary words has
    # nothing to compare and is correctly never called a repeat.
    asked = "Tell me about the PagerDuty rotation you ran for the Kafka ingest incident."
    calls = {"n": 0}

    async def _invoke(*a, **k):
        calls["n"] += 1
        return json.dumps({"question": asked})

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _invoke)
    out = await interviewer.compose_next_question(
        session=None,
        question="stored fallback question",
        transcript=[{"speaker": "agent", "content": asked}],
        mode=interviewer.MODE_GENERATE,
        competency="Incident response",
        asked_before=[asked],
    )
    assert calls["n"] == 2
    assert out == "stored fallback question"


@pytest.mark.asyncio
async def test_an_outage_costs_the_delivery_and_nothing_else(monkeypatch) -> None:
    stored = "Walk me through how you tuned Kafka consumer lag."

    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _boom)
    out = await interviewer.compose_next_question(
        session=None,
        question=stored,
        transcript=[{"speaker": "candidate", "content": "I own ingestion."}],
        mode=interviewer.MODE_REWORD,
    )
    assert out == stored
