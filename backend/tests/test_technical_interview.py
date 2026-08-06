"""Per-candidate technical questions: the plan, the criteria, the degradation.

The preset per-job bank was withdrawn on 2026-08-06 and this module replaced it.
Three claims were made when that happened, and each one is only worth the test
that pins it:

  1. **Comparability survives.** The bank guaranteed that two candidates for a
     job answered the same questions. The replacement guarantees they are probed
     on the same SKILLS, in the same order, from a pure function of the JD.
  2. **The rubric always belongs to the question actually asked.** This was the
     stated reason a technical question could not be generated mid-conversation.
     It is answered by writing both together and persisting them together.
  3. **A provider outage costs specificity and nothing else.** The candidate
     always has a question to answer.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_loop
from app.services import technical_interview as ti


def _job(grade: str = "non_managerial", skills=("Python", "PostgreSQL", "Kafka")):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Backend Engineer",
        level=None,
        jd_json={"skills": list(skills)},
        jd_markdown="Build and run the ingestion platform.",
        assessment_grade=grade,
    )


def _row(skill="Kafka", ordinal=1):
    return SimpleNamespace(
        id=uuid.uuid4(),
        job_candidate_link_id=uuid.uuid4(),
        ordinal=ordinal,
        skill=skill,
        prompt=ti.fallback_question(skill, ordinal),
        rubric_json=dict(ti.DEFAULT_RUBRIC),
        generated_at=None,
    )


def _good_payload(skill="Kafka", question=None):
    return {
        "question": question or f"How did you tune {skill} consumer lag on the ingest path?",
        "rubric": {
            "0_39": "No relevant experience with the system named.",
            "40_59": "Names the tool but describes no concrete change.",
            "60_74": "Describes a specific change and why it was made.",
            "75_89": "Adds the measurement that showed it worked.",
            "90_100": "Adds the trade-off accepted and what it cost.",
        },
    }


# ── The deterministic coverage plan ──────────────────────────────────────────


def test_the_plan_is_a_pure_function_of_the_job() -> None:
    """Two candidates, one job, same skills in the same order. This is what
    replaced the preset bank's comparability guarantee, so it is a property of
    the code and not of how carefully a caller uses it."""
    job = _job()
    assert ti.skill_plan(job) == ti.skill_plan(job)


def test_the_plan_covers_every_declared_skill_before_repeating() -> None:
    plan = ti.skill_plan(_job("non_managerial"))
    assert plan[:3] == ["Python", "PostgreSQL", "Kafka"]
    assert len(plan) == 20
    # Twenty slots over three skills: each is covered six or seven times, and
    # none is starved.
    assert all(plan.count(skill) >= 6 for skill in ("Python", "PostgreSQL", "Kafka"))


def test_the_plan_falls_back_to_the_title_when_a_jd_declares_no_skills() -> None:
    job = _job("cxo")
    job.jd_json = {}
    plan = ti.skill_plan(job)
    assert len(plan) == 12
    assert set(plan) == {"Backend Engineer"}


def test_the_plan_mines_jd_prose_when_declared_skills_are_thin() -> None:
    job = _job("cxo", skills=("Excel",))
    job.jd_json["responsibilities"] = [
        "Design MongoDB schemas and indexes that support the access patterns",
        "Own incident response for the payments platform",
    ]
    plan = ti.skill_plan(job)
    assert "MongoDB schemas and indexes" in plan
    # The leading verb is dropped, so a label reads as a subject and not as an
    # instruction -- it becomes a report heading a client reads verbatim.
    assert not any(skill.lower().startswith("design ") for skill in plan)


def test_a_fallback_question_is_stable_for_a_slot() -> None:
    """A retried turn must show the candidate the same question it showed them
    the first time; a randomly chosen angle would not."""
    assert ti.fallback_question("Kafka", 3) == ti.fallback_question("Kafka", 3)
    assert ti.fallback_question("Kafka", 1) != ti.fallback_question("Kafka", 2)


# ── The generation criteria ──────────────────────────────────────────────────
# These are deterministic on purpose: the moment the guard matters most is the
# moment the provider is down, and an LLM judge would make the criteria
# unfalsifiable as well as adding a second flaky dependency.


def _evaluate(payload, skill="Kafka", asked_before=()):
    return ti._evaluate(skill, list(asked_before))(payload)


def test_a_well_formed_question_and_rubric_is_accepted() -> None:
    assert _evaluate(_good_payload()).ok


def test_a_question_that_ignores_its_skill_is_rejected() -> None:
    """The skill is the heading this answer will be reported under. A question
    about something else produces an answer the report cannot file."""
    payload = _good_payload(question="Tell me about a time you missed a deadline.")
    critique = _evaluate(payload, skill="Kafka")
    assert not critique.ok
    assert any("Kafka" in reason for reason in critique.reasons)


def test_a_phrase_skill_matches_on_words_not_as_a_substring() -> None:
    """A skill label is routinely a phrase a good question quite properly says
    in a different order. Requiring the literal phrase would reject the best
    questions and quietly reduce this module to its fallback."""
    payload = _good_payload(
        question="Which access patterns drove the indexes you put on that MongoDB collection?"
    )
    assert _evaluate(payload, skill="MongoDB schemas and indexes").ok


def test_a_partial_rubric_is_rejected() -> None:
    """A rubric missing a band cannot express the grade that band stands for, so
    the scorer silently compresses the scale -- which reads as a harsh marker
    rather than as a malformed rubric."""
    payload = _good_payload()
    payload["rubric"]["90_100"] = ""
    critique = _evaluate(payload)
    assert not critique.ok
    assert any("90_100" in reason for reason in critique.reasons)


def test_a_rubric_that_repeats_itself_is_rejected() -> None:
    """A model under instruction pressure will pad the shape it was asked for.
    Five identical bands satisfy "five bands" and cannot separate a strong
    answer from a weak one."""
    payload = _good_payload()
    for band in ti.RUBRIC_BANDS:
        payload["rubric"][band] = "The answer is good."
    critique = _evaluate(payload)
    assert not critique.ok
    assert any("DIFFERENT" in reason for reason in critique.reasons)


def test_a_stacked_question_is_rejected() -> None:
    payload = _good_payload(
        question="How did you tune Kafka consumer lag? Also, what would you do differently?"
    )
    critique = _evaluate(payload)
    assert not critique.ok
    assert any("one thing" in reason for reason in critique.reasons)


def test_one_question_said_in_two_sentences_is_accepted() -> None:
    """Counting question marks would reject ordinary interviewer speech. What
    signals a stacked ask is a second DEMAND, not a second sentence."""
    payload = _good_payload(
        question="You mentioned the ingest path earlier. How did you tune Kafka consumer lag on it?"
    )
    assert _evaluate(payload).ok


def test_an_essay_is_rejected_rather_than_truncated() -> None:
    """Truncating a question loses its question mark, which reaches a candidate
    looking like a bug rather than like a limit."""
    payload = _good_payload(question="Kafka. " + ("word " * 200))
    critique = _evaluate(payload)
    assert not critique.ok
    assert any("characters" in reason for reason in critique.reasons)


def test_a_repeat_of_an_earlier_question_is_rejected() -> None:
    asked = "How did you tune Kafka consumer lag on the ingest path?"
    payload = _good_payload(question=asked)
    critique = _evaluate(payload, asked_before=[asked])
    assert not critique.ok
    assert any("already been asked" in reason for reason in critique.reasons)


def test_a_second_probe_of_the_same_skill_is_not_treated_as_a_repeat() -> None:
    """The plan deliberately probes each skill several times. A low similarity
    threshold would reject the legitimate second and third questions and starve
    the coverage the plan exists to provide."""
    payload = _good_payload(
        question="When a Kafka broker failed mid-rebalance, what did you change about the consumer group?"
    )
    critique = _evaluate(
        payload,
        asked_before=["How did you tune Kafka consumer lag on the ingest path?"],
    )
    assert critique.ok


# ── Generation end to end ────────────────────────────────────────────────────


class _NullSession:
    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_generated_question_and_its_rubric_are_persisted_together(monkeypatch) -> None:
    """THE invariant. An answer is scored against its own rubric, so a generated
    question is only sound if the rubric was generated with it and stored beside
    it -- otherwise the candidate is graded against a rubric written for a
    question nobody asked."""
    import json

    payload = _good_payload()

    async def _invoke(*a, **k):
        return json.dumps(payload)

    monkeypatch.setattr(ti.llm_router, "invoke_llm", _invoke)
    row = _row()
    result = await ti.write_question(session=_NullSession(), job=_job(), row=row)

    assert result.degraded is False
    assert row.prompt == payload["question"]
    assert row.rubric_json == payload["rubric"]
    # Stamped only when a model actually wrote the pair. This is what makes a
    # silent degradation countable.
    assert row.generated_at is not None


@pytest.mark.asyncio
async def test_an_outage_leaves_the_deterministic_probe_untouched(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("every provider down")

    monkeypatch.setattr(ti.llm_router, "invoke_llm", _boom)
    row = _row()
    before = row.prompt
    result = await ti.write_question(session=_NullSession(), job=_job(), row=row)

    assert result.degraded is True
    assert result.value["question"] == before
    assert row.prompt == before
    assert row.rubric_json == dict(ti.DEFAULT_RUBRIC)
    # NULL is the honest record that this candidate read a fallback.
    assert row.generated_at is None


@pytest.mark.asyncio
async def test_a_rejected_first_attempt_is_corrected_on_the_second(monkeypatch) -> None:
    """The loop's whole value, exercised through the real criteria: a missing
    rubric band is a defect a model fixes when told, and the previous one-shot
    code threw the response away and shipped a canned question."""
    import json

    calls: list[list[dict]] = []
    bad = _good_payload()
    bad["rubric"]["75_89"] = ""

    async def _invoke(task_type, messages, **k):
        calls.append(messages)
        return json.dumps(bad if len(calls) == 1 else _good_payload())

    monkeypatch.setattr(ti.llm_router, "invoke_llm", _invoke)
    row = _row()
    result = await ti.write_question(session=_NullSession(), job=_job(), row=row)

    assert result.degraded is False
    assert result.attempts == 2
    # The second call carried the reflection naming the exact missing band.
    assert "75_89" in calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_a_response_that_is_not_json_degrades_rather_than_raising(monkeypatch) -> None:
    async def _invoke(*a, **k):
        return "I'm sorry, I can't help with that."

    monkeypatch.setattr(ti.llm_router, "invoke_llm", _invoke)
    row = _row()
    result = await ti.write_question(session=_NullSession(), job=_job(), row=row)
    assert result.degraded is True
    assert row.generated_at is None


@pytest.mark.asyncio
async def test_a_broken_session_degrades_instead_of_500ing_the_turn(monkeypatch) -> None:
    """The router loads provider keys through the SAME session as its caller, so
    a bad enough outage can leave the transaction unusable underneath us. The
    one moment every provider is down must not also be the moment `respond`
    raises."""
    import json

    async def _invoke(*a, **k):
        return json.dumps(_good_payload())

    class _BrokenSession:
        async def flush(self):
            raise RuntimeError("Can't operate on closed transaction")

    monkeypatch.setattr(ti.llm_router, "invoke_llm", _invoke)
    row = _row()
    result = await ti.write_question(session=_BrokenSession(), job=_job(), row=row)

    assert result.degraded is True
    assert result.error == "RuntimeError"
    assert isinstance(result, agent_loop.LoopResult)
    # The candidate still has something to answer.
    assert result.value["question"]
    assert result.value["rubric"] == dict(ti.DEFAULT_RUBRIC)
