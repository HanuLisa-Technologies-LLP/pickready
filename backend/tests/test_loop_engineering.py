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


def _competency(category, index):
    return {
        "category": category,
        "name": f"{category}-{index}",
        "description": "what it measures",
        "required_level": "Matching",
    }


def _matrix():
    return [
        _competency(category, index)
        for category in ppi.CATEGORIES
        for index in range(3)
    ]


class _StubSession:
    """Answers both shapes `generate_framework` uses: `.all()` for the existing
    matrix and `.first()` for the SWOT intake."""

    def __init__(self) -> None:
        self.added: list = []

    async def execute(self, *a, **k):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None)
        )

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_an_empty_aspect_is_re_asked_before_the_fallback_fills_it(
    monkeypatch,
) -> None:
    """Draft v4 removed the per-aspect MINIMUM, so a short generation is no
    longer a defect: three items may be the right answer for the job, and the
    old floor of five is what produced mechanically derived names like
    "Kafka (supporting)" on the one screen a human is required to review.

    What remains checkable is COVERAGE. An aspect that came back empty is still
    a defect, because every aspect is graded, remarked and charted on each
    report, and the loop asks for it again before the deterministic fallback
    fills the hole with a placeholder.
    """
    calls: list[list[dict]] = []
    missing_nice_to_have = [
        _competency(ppi.CATEGORY_MUST_HAVE, index) for index in range(3)
    ] + [_competency(ppi.CATEGORY_BEHAVIOURAL, index) for index in range(3)]

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        payload = missing_nice_to_have if len(calls) == 1 else _matrix()
        return json.dumps({"competencies": payload})

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    rows = await ppi.generate_framework(_StubSession(), _job())

    assert len(calls) == 2
    # The correction names the aspect that came back empty.
    correction = calls[1][-1]["content"]
    assert "Nice-to-have" in correction
    # And no placeholder survived into the saved matrix.
    assert not any("Placeholder" in (row.description or "") for row in rows)


@pytest.mark.asyncio
async def test_a_short_matrix_is_accepted_rather_than_padded(monkeypatch) -> None:
    """The direct statement of what Draft v4 changed.

    Three items, one per aspect, is a complete matrix. Under the old rule this
    generation would have been rejected and then padded to fifteen.
    """
    calls: list[list[dict]] = []
    minimal = [_competency(category, 0) for category in ppi.CATEGORIES]

    async def _chat(task_type, messages, **k):
        calls.append(messages)
        return json.dumps({"competencies": minimal})

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _chat)
    rows = await ppi.generate_framework(_StubSession(), _job())

    assert len(calls) == 1
    assert len(rows) == 3
    assert not any("(" in row.name for row in rows)


@pytest.mark.asyncio
async def test_the_deterministic_floor_still_holds_under_a_total_outage(
    monkeypatch,
) -> None:
    """The loop sits ON TOP of the JD-derived fallback, it does not replace it.
    This path is what repaired 19 stranded live jobs with every provider down."""
    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(ppi.llm_router, "chat_completion", _boom)
    rows = await ppi.generate_framework(_StubSession(), _job())

    assert {row.category for row in rows} == set(ppi.CATEGORIES)


@pytest.mark.asyncio
async def test_culture_is_still_dropped_and_never_re_asked_for(monkeypatch) -> None:
    """Dropping beats rejecting: refusing a whole generation because one entry
    was disallowed sends the recruiter back to an empty screen for a problem the
    product can fix itself. The loop must not change that."""
    payload = _matrix() + [
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
