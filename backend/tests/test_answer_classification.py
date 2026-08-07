"""The "Question 8 of 45 and nothing was answered" defect, pinned.

Observed live 2026-08-05: a candidate typed `fsjdemd`, then `xdshfjg,uyytrs`,
then `dwrhejyrkhfbgertyfg`, then `cvdgrertykfmhgnfrshfmgc`, and the agent asked
the next scripted question every time. Those four exact strings are parametrised
below and are the reason this module exists.

Three properties are asserted here, and each one is a defect if it moves:

1. The deterministic pre-pass NEVER reaches the model. Enforced with a spy that
   raises if called, because the guard on empty and gibberish has to keep
   working during exactly the outage that would take the model away.
2. A real answer is never accused. "I have not used Kafka" is the standing
   CLAUDE.md rule and the most costly false positive available here.
3. Every degradation path lands on substantive / low / no re-challenge. A false
   "evasive" pushes back on a genuine answer on the strength of a provider
   hiccup; falling back to substantive costs the re-challenge and nothing else.

No test here touches the network: `llm_router.invoke_llm` is monkeypatched in
every case that gets far enough to call it.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services import answer_classification
from app.services.answer_classification import LABELS, Classification, classify


# ── Helpers ─────────────────────────────────────────────────────────────────


def _spy_that_must_not_be_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if the model is reached at all.

    An assertion on a returned value would not catch this: the deterministic
    verdict would be correct either way, and the module would simply have become
    dependent on a provider for a decision it can make in-process.
    """

    async def _boom(*args: Any, **kwargs: Any) -> str:
        raise AssertionError(
            "the deterministic pre-pass reached the model; empty and gibberish "
            "must be decided in-process so they survive an outage"
        )

    monkeypatch.setattr(answer_classification.llm_router, "invoke_llm", _boom)


def _returns(payload: Any) -> Any:
    """A stand-in invoke_llm returning `payload` as the raw response body."""
    body = payload if isinstance(payload, str) else json.dumps(payload)

    async def _fake(*args: Any, **kwargs: Any) -> str:
        return body

    return _fake


def _raises(exc: BaseException) -> Any:
    async def _fake(*args: Any, **kwargs: Any) -> str:
        raise exc

    return _fake


QUESTION = "Tell me about a time you tuned Kafka consumer lag in production."


# ── The public shape other code imports ─────────────────────────────────────


def test_labels_include_shallow_as_a_distinct_relevance_failure() -> None:
    assert LABELS == (
        "substantive",
        "empty",
        "gibberish",
        "off_topic",
        "shallow",
        "evasive",
    )


# ── 1. The deterministic pre-pass, and that it never calls a model ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "fsjdemd",                    # the four exact production inputs,
        "xdshfjg,uyytrs",             # 2026-08-05, in the order they were typed
        "dwrhejyrkhfbgertyfg",
        "cvdgrertykfmhgnfrshfmgc",
        "ewidjverip",                 # the original report, for continuity
        "asdfghjkl",
        "ok",                         # an acknowledgement, not an answer
    ],
)
async def test_gibberish_is_caught_without_a_model_call(
    answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spy_that_must_not_be_called(monkeypatch)
    result = await classify(
        session=None, question=QUESTION, answer=answer, transcript=None
    )
    assert result.label == "gibberish", result
    assert result.scorable is False, "must route to the existing UNANSWERED path"
    assert result.needs_rechallenge is True
    assert result.confidence == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["", "   \n\t ", "...", "???", None])
async def test_empty_is_caught_without_a_model_call(
    answer: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spy_that_must_not_be_called(monkeypatch)
    result = await classify(
        session=None, question=QUESTION, answer=answer, transcript=None
    )
    assert result.label == "empty", result
    assert result.scorable is False
    assert result.needs_rechallenge is True


# ── 2. The false positive that matters most ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "I have not used Kafka in any production system so far.",
        "I have never led a team of that size, so I cannot speak to it.",
        "No, I do not know that tool. I have only used RabbitMQ.",
    ],
)
async def test_a_negative_answer_is_substantive(
    answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLAUDE.md: a negative answer is a real answer, scored low on its merits.

    Two halves are asserted at once. The deterministic pass must let it through
    (it is prose, not mash), and the classifier must accept the model's
    "substantive" without the module second-guessing it into a dodge.
    """
    monkeypatch.setattr(
        answer_classification.llm_router,
        "invoke_llm",
        _returns(
            {
                "label": "substantive",
                "confidence": "high",
                "reason": "answers the question directly in the negative",
            }
        ),
    )
    result = await classify(
        session=None, question=QUESTION, answer=answer, transcript=None
    )
    assert result.label == "substantive", result
    assert result.scorable is True
    assert result.needs_rechallenge is False


# ── 3. The two kinds only a model can see ──────────────────────────────────


@pytest.mark.asyncio
async def test_off_topic_is_flagged_but_still_scorable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        answer_classification.llm_router,
        "invoke_llm",
        _returns(
            {
                "label": "off_topic",
                "confidence": "high",
                "reason": "describes a Postgres migration, not Kafka lag",
            }
        ),
    )
    result = await classify(
        session=None,
        question=QUESTION,
        answer=(
            "We migrated our primary Postgres cluster to a new region over a "
            "weekend, and I wrote the cutover runbook for the whole team."
        ),
        transcript=None,
    )
    assert result.label == "off_topic"
    assert result.needs_rechallenge is True
    # Real text stays scorable: marking it unanswered would reach a low grade by
    # a dishonest route and would discard what the candidate actually wrote.
    assert result.scorable is True


@pytest.mark.asyncio
async def test_evasive_is_flagged_but_still_scorable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        answer_classification.llm_router,
        "invoke_llm",
        _returns(
            {
                "label": "evasive",
                "confidence": "medium",
                "reason": "generalities about teamwork, no specific instance",
            }
        ),
    )
    result = await classify(
        session=None,
        question=QUESTION,
        answer=(
            "I always believe performance work should be data driven and that "
            "the team should own its own reliability outcomes collectively."
        ),
        transcript=None,
    )
    assert result.label == "evasive"
    assert result.needs_rechallenge is True
    assert result.scorable is True


@pytest.mark.asyncio
async def test_the_transcript_is_passed_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate saying "as I mentioned above" is only judgeable in context.

    Without the preceding turns that reads as a dodge, so the transcript
    reaching the prompt is a correctness property, not a nicety.
    """
    seen: dict[str, Any] = {}

    async def _capture(task_type: str, messages: list[dict], **kwargs: Any) -> str:
        seen["task_type"] = task_type
        seen["user"] = messages[-1]["content"]
        return json.dumps(
            {"label": "substantive", "confidence": "high", "reason": "in context"}
        )

    monkeypatch.setattr(answer_classification.llm_router, "invoke_llm", _capture)
    await classify(
        session=None,
        question=QUESTION,
        answer="As I mentioned above, we cut lag by resharding the topic.",
        transcript=[
            {"speaker": "agent", "content": "What was your throughput ceiling?"},
            {"speaker": "candidate", "content": "About forty thousand a second."},
        ],
    )
    # Routing policy (temperature 0.7, provider order, timeouts) is data in
    # config/llm_providers; this module must only name the task type.
    assert seen["task_type"] == "conversation_turn"
    assert "forty thousand" in seen["user"]


# ── 4. Every degradation path ──────────────────────────────────────────────


def _assert_degraded(result: Classification) -> None:
    assert result.label == "substantive", result
    assert result.confidence == "low"
    assert result.needs_rechallenge is False, (
        "a provider failure must never make the interviewer push back on what "
        "may well be a genuine answer"
    )
    assert result.scorable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("provider outage"),
        asyncio.TimeoutError(),
        ValueError("no healthy key"),
    ],
    ids=["outage", "timeout", "no_key"],
)
async def test_llm_failure_degrades_to_substantive(
    exc: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        answer_classification.llm_router, "invoke_llm", _raises(exc)
    )
    _assert_degraded(
        await classify(
            session=None,
            question=QUESTION,
            answer="We resharded the topic and lag dropped within the hour.",
            transcript=None,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    ["", "not json at all", "{", "[]", "null", '{"label": null}'],
)
async def test_malformed_response_degrades_to_substantive(
    body: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        answer_classification.llm_router, "invoke_llm", _returns(body)
    )
    _assert_degraded(
        await classify(
            session=None,
            question=QUESTION,
            answer="We resharded the topic and lag dropped within the hour.",
            transcript=None,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label",
    [
        "uncertain",       # a label the model made up
        "partial",
        "3",               # a grade, which this module does not produce
        "empty",           # contradicts the deterministic pass, which wins
        "gibberish",
    ],
)
async def test_an_invented_label_degrades_to_substantive(
    label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guessing which real label an invented one meant would be inventing a
    judgement about a candidate out of a malformed response."""
    monkeypatch.setattr(
        answer_classification.llm_router,
        "invoke_llm",
        _returns({"label": label, "confidence": "high", "reason": "whatever"}),
    )
    _assert_degraded(
        await classify(
            session=None,
            question=QUESTION,
            answer="We resharded the topic and lag dropped within the hour.",
            transcript=None,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [0.82, "0.82", "very high", "", None])
async def test_a_non_word_confidence_is_downgraded_not_discarded(
    confidence: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No number reaches a client, so a numeric confidence is refused as a WORD.

    The label still stands: the model usefully told us what it saw, and throwing
    the whole verdict away over its hedge would lose real information.
    """
    monkeypatch.setattr(
        answer_classification.llm_router,
        "invoke_llm",
        _returns(
            {"label": "evasive", "confidence": confidence, "reason": "generalities"}
        ),
    )
    result = await classify(
        session=None,
        question=QUESTION,
        answer="Performance work should always be driven by good data.",
        transcript=None,
    )
    assert result.label == "evasive"
    assert result.confidence == "low"
    assert isinstance(result.confidence, str)


@pytest.mark.asyncio
async def test_confidence_is_never_a_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Across every path in the module, including the deterministic one."""
    monkeypatch.setattr(
        answer_classification.llm_router,
        "invoke_llm",
        _returns({"label": "off_topic", "confidence": "medium", "reason": "x"}),
    )
    results = [
        await classify(session=None, question=QUESTION, answer="", transcript=None),
        await classify(
            session=None, question=QUESTION, answer="fsjdemd", transcript=None
        ),
        await classify(
            session=None,
            question=QUESTION,
            answer="We tuned the consumer group and lag fell away.",
            transcript=None,
        ),
    ]
    for result in results:
        assert result.confidence in {"high", "medium", "low"}, result


@pytest.mark.asyncio
async def test_classification_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A verdict is a record of a decision, not a mutable working value."""
    _spy_that_must_not_be_called(monkeypatch)
    result = await classify(
        session=None, question=QUESTION, answer="", transcript=None
    )
    with pytest.raises(Exception):
        result.label = "substantive"  # type: ignore[misc]
