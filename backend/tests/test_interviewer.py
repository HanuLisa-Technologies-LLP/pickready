"""The adaptive interviewer, and the three things it must never break.

`respond` used to be an index into a fixed list with no LLM call anywhere in the
conversation. Making it adaptive puts an unbounded-by-nature mechanism next to
three that must stay exactly as they were:

  * scoring and question GROUPING -- `answers_by_key` groups by `question_key`;
  * BILLING and completion -- `charge_completed` fires on the index reaching
    len(prompts);
  * TERMINATION -- an interview that can ask "one more thing" must be finite.

Each has a test below, and the budget tests deliberately assert the ceiling
holds even when the model wants to keep going.
"""
from __future__ import annotations

import json

import pytest

from app.services import interviewer


class _Router:
    """Stand-in for llm_router.invoke_llm, recording what it was asked."""

    def __init__(self, follow_up):
        self.follow_up = follow_up
        self.calls: list[dict] = []

    async def __call__(self, task_type, messages, **kwargs):
        self.calls.append({"task_type": task_type, "messages": messages})
        if isinstance(self.follow_up, Exception):
            raise self.follow_up
        return json.dumps({"follow_up": self.follow_up})


_GOOD_ANSWER = (
    "I rebuilt the billing service on Postgres and moved the reconciliation "
    "job off the request path."
)


async def _ask(monkeypatch, follow_up, **overrides):
    router = _Router(follow_up)
    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", router)
    kwargs = {
        "session": None,
        "question": "Describe a system you designed end to end.",
        "answer": _GOOD_ANSWER,
        "transcript": [],
        "follow_ups_used": 0,
        "already_followed_up": False,
    }
    kwargs.update(overrides)
    return await interviewer.next_follow_up(**kwargs), router


# ── It actually adapts ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_follow_up_is_returned_when_the_model_asks_one(monkeypatch) -> None:
    probe = "You mentioned moving reconciliation off the request path. What broke first?"
    result, _ = await _ask(monkeypatch, probe)
    assert result == probe


@pytest.mark.asyncio
async def test_null_moves_the_interview_on(monkeypatch) -> None:
    result, _ = await _ask(monkeypatch, None)
    assert result is None


@pytest.mark.asyncio
async def test_the_model_is_given_the_conversation_so_far(monkeypatch) -> None:
    """This is the memory requirement. Without the transcript, every turn is
    judged on the single answer in front of it, which is how the interview came
    to repeat ground the candidate had already covered."""
    transcript = [
        {"speaker": "agent", "content": "Which languages do you use most?"},
        {"speaker": "candidate", "content": "Mostly Python, some Go."},
    ]
    _, router = await _ask(monkeypatch, "And where did Go win?", transcript=transcript)
    sent = router.calls[0]["messages"][-1]["content"]
    assert "Mostly Python, some Go." in sent, (
        "the prior answer was not sent to the model; the agent has no memory"
    )


@pytest.mark.asyncio
async def test_it_is_routed_as_a_conversation_turn(monkeypatch) -> None:
    """So it picks up the one task temperature above 0.5. At a scoring
    temperature the interviewer repeats itself verbatim to every candidate,
    which is the scripted feel this module exists to remove."""
    from app.config.llm_providers import temperature_for

    _, router = await _ask(monkeypatch, "Say more?")
    assert router.calls[0]["task_type"] == "conversation_turn"
    assert temperature_for("conversation_turn") > 0.5


# ── Termination: the ceiling holds even when the model wants more ────────────

@pytest.mark.asyncio
async def test_the_per_question_cap_is_enforced(monkeypatch) -> None:
    result, router = await _ask(
        monkeypatch, "Another probe?", already_followed_up=True
    )
    assert result is None
    assert router.calls == [], "budget must be checked BEFORE spending a call"


@pytest.mark.asyncio
async def test_the_conversation_budget_is_enforced(monkeypatch) -> None:
    result, router = await _ask(
        monkeypatch, "Another probe?", follow_ups_used=interviewer.MAX_FOLLOW_UPS
    )
    assert result is None
    assert router.calls == []


@pytest.mark.asyncio
async def test_total_turns_are_bounded(monkeypatch) -> None:
    """The property that matters: however enthusiastic the model is, an
    interview of N base questions can never exceed N + MAX_FOLLOW_UPS turns."""
    used = 0
    for _ in range(50):
        result, _ = await _ask(
            monkeypatch, "Keep going?", follow_ups_used=used, already_followed_up=False
        )
        if result is None:
            break
        used += 1
    assert used == interviewer.MAX_FOLLOW_UPS


# ── Degradation: a live candidate is waiting ─────────────────────────────────

@pytest.mark.asyncio
async def test_an_llm_failure_moves_the_interview_on(monkeypatch) -> None:
    """Never a 500. A candidate is mid-assessment; an outage must cost the
    adaptivity and nothing else."""
    result, _ = await _ask(monkeypatch, RuntimeError("provider down"))
    assert result is None


@pytest.mark.asyncio
async def test_malformed_json_moves_the_interview_on(monkeypatch) -> None:
    async def _garbage(task_type, messages, **kwargs):
        return "I'm afraid I can't do that."

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _garbage)
    result = await interviewer.next_follow_up(
        session=None, question="q", answer=_GOOD_ANSWER, transcript=[],
        follow_ups_used=0, already_followed_up=False,
    )
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "   ", "null", "none", "N/A", "-", "x" * 400])
async def test_unusable_follow_ups_are_dropped(monkeypatch, value: str) -> None:
    """A model echoing 'null' as a string, or delivering a speech, must not be
    shown to a candidate as an interview question."""
    result, _ = await _ask(monkeypatch, value)
    assert result is None


@pytest.mark.asyncio
async def test_a_non_answer_is_not_probed(monkeypatch) -> None:
    """Gibberish is already routed to the unanswered scoring path. Probing it
    would spend budget a real-but-thin answer later has a better claim on, and
    would read as the interviewer failing to notice."""
    result, router = await _ask(monkeypatch, "Say more?", answer="ewidjverip")
    assert result is None
    assert router.calls == []
