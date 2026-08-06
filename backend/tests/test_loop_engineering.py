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

from app.services import functional_assessment as fa
from app.services import interviewer, ppi


# ── bounded_remark: the string a client actually reads ───────────────────────


@pytest.mark.asyncio
async def test_a_remark_outside_the_word_contract_is_regenerated(monkeypatch) -> None:
    calls: list[list[dict]] = []

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        if len(calls) == 1:
            return "Too short."
        return " ".join(["word"] * 27)

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
        return " ".join(["word"] * 27)

    monkeypatch.setattr(fa.llm_router, "chat_completion", _chat)
    out = await fa.bounded_remark(None, "PostgreSQL", "evidence", 25, 30)

    assert calls["n"] == 2
    assert out == " ".join(["word"] * 27)
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


def _competency(category, index):
    return {
        "category": category,
        "name": f"{category}-{index}",
        "description": "what it measures",
        "required_level": "Matching",
    }


def _full_framework():
    return [
        _competency(category, index)
        for category in ppi.CATEGORIES
        for index in range(ppi.MINIMUM_PER_CATEGORY)
    ]


class _StubSession:
    def __init__(self) -> None:
        self.added: list = []

    async def execute(self, *a, **k):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_short_framework_is_re_asked_before_it_is_padded(monkeypatch) -> None:
    """`_top_up` always guaranteed the minimum, so a short generation never
    FAILED -- it was padded with mechanically derived names like
    "Kafka (supporting)". That is a correct floor and a poor framework, and it
    lands on the one screen a human is required to review.
    """
    calls: list[list[dict]] = []
    short = [_competency(ppi.CATEGORY_PRIMARY, i) for i in range(5)]
    short += [_competency(ppi.CATEGORY_SECONDARY, i) for i in range(2)]
    short += [_competency(ppi.CATEGORY_BEHAVIOURAL, i) for i in range(5)]

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        payload = short if len(calls) == 1 else _full_framework()
        return json.dumps({"competencies": payload})

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    rows = await ppi.generate_framework(_StubSession(), _job())

    assert len(calls) == 2
    # The correction names the category and the count it actually returned.
    correction = calls[1][-1]["content"]
    assert "Secondary Skills" in correction and "you returned 2" in correction
    # And no mechanical filler survived into the saved framework.
    assert not any("(supporting)" in row.name for row in rows)


@pytest.mark.asyncio
async def test_the_deterministic_floor_still_holds_under_a_total_outage(monkeypatch) -> None:
    """The loop sits ON TOP of the JD-derived fallback, it does not replace it.
    This path is what repaired 19 stranded live jobs with every provider down."""
    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _boom)
    rows = await ppi.generate_framework(_StubSession(), _job())

    counts = {category: 0 for category in ppi.CATEGORIES}
    for row in rows:
        counts[row.category] += 1
    assert all(count >= ppi.MINIMUM_PER_CATEGORY for count in counts.values())


@pytest.mark.asyncio
async def test_culture_is_still_dropped_and_never_re_asked_for(monkeypatch) -> None:
    """Dropping beats rejecting: refusing a whole generation because one of
    eighteen entries was disallowed sends the recruiter back to an empty screen
    for a problem the product can fix itself. The loop must not change that."""
    payload = _full_framework() + [
        {
            "category": ppi.CATEGORY_BEHAVIOURAL,
            "name": "Culture fit",
            "description": "no",
            "required_level": "Matching",
        }
    ]

    async def _chat(*a, **k):
        return json.dumps({"competencies": payload})

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    rows = await ppi.generate_framework(_StubSession(), _job())
    assert not any(ppi.is_forbidden_competency(row.name) for row in rows)


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
