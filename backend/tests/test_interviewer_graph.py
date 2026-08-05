"""The conversational agent is a graph, and its rewrites cannot change a question.

WHY THIS FILE EXISTS
--------------------
The interview was reported rebuilt on LangGraph on 2026-08-04 and was not:
`grep -rn "from langgraph" backend/app/` returned three files and
`services/interviewer` was not among them. Nothing failed, because nothing
asserted it. A claim with no test is how the same defect gets reported fixed
twice.

The second half of this file guards the sharper risk introduced by delivering
questions through a model at temperature 0.7. A base question is scored against
its OWN stored rubric, so a rewrite that quietly dropped "Kafka" would be graded
against a rubric for a Kafka question the candidate was never asked -- and that
reaches a client as a grade nobody can explain.
"""
from __future__ import annotations

import pytest

from app.services import interviewer


# ── It is actually a graph ───────────────────────────────────────────────────


def test_the_interviewer_is_built_on_langgraph() -> None:
    """The claim that kept being made without being true."""
    import inspect

    source = inspect.getsource(interviewer)
    assert "from langgraph.graph import" in source, (
        "services/interviewer does not import LangGraph. This was reported "
        "rebuilt on LangGraph once already while remaining an index into a list."
    )
    # Compiled at import, not per turn: building a StateGraph on a request a
    # candidate is waiting on would add latency for no behavioural difference.
    for graph in (interviewer._DECIDE_GRAPH, interviewer._DELIVER_GRAPH):
        assert hasattr(graph, "ainvoke"), "graph was not compiled"


def test_both_graphs_expose_their_nodes() -> None:
    """Explicit nodes are the point. A single node would be the old function
    wearing a graph's clothes."""
    decide = set(interviewer._DECIDE_GRAPH.get_graph().nodes)
    deliver = set(interviewer._DELIVER_GRAPH.get_graph().nodes)
    assert {"budget", "substance", "assess", "validate"} <= decide
    assert {"plan", "compose", "validate"} <= deliver


def test_no_canned_acknowledgments_in_the_conversation_path() -> None:
    """`_CONNECTORS` prepended one of eight fixed openers to every question by
    POSITION -- "Great.", "Understood.", "Appreciate the detail." -- chosen by
    `position % 8` and therefore blind to what the candidate had just said. An
    answer of "I do not know" was met with "Appreciate the detail."

    It survived every prior pass because nothing asserted its absence, and it
    was the most visible reason the assessment read as a form rather than a
    conversation. The transition is now written per turn against the real
    transcript, or not written at all.
    """
    import inspect

    from app.api import assessments

    # CODE lines only. The comment recording the removal necessarily quotes the
    # strings it removed, and a check that could not tell those apart would
    # forbid explaining the change.
    code = "\n".join(
        line
        for line in inspect.getsource(assessments).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "_CONNECTORS" not in code, (
        "a canned connector table is back in the conversation path"
    )
    for canned in (
        "Thanks for that.",
        "Good, moving on.",
        "Understood.",
        "Appreciate the detail.",
        "Right, next one.",
    ):
        assert canned not in code, f"canned acknowledgment {canned!r} is back"


# ── A rewrite may not change the question ────────────────────────────────────


@pytest.mark.parametrize(
    "original, delivered, ok",
    [
        # Plain rewording, every specific term kept.
        (
            "Describe how you tuned Kafka consumer lag under load.",
            "You mentioned throughput earlier, so: how did you tune Kafka "
            "consumer lag under load?",
            True,
        ),
        # THE failure this check exists for: the technology is gone, so the
        # answer would be scored against a rubric for a question nobody asked.
        (
            "Describe how you tuned Kafka consumer lag under load.",
            "How did you tune the message queue when it got slow?",
            False,
        ),
        # A dropped metric is the same defect in a smaller costume.
        (
            "How did you bring p99 latency under 200ms?",
            "How did you bring latency down?",
            False,
        ),
        # Empty is never a question.
        ("Tell me about CI/CD in your last team.", "", False),
    ],
)
def test_substance_must_survive_delivery(original, delivered, ok) -> None:
    assert interviewer._substance_preserved(original, delivered) is ok


def test_a_delivery_may_not_become_a_speech() -> None:
    """Length is bounded off the original. A model that answered with an essay
    would otherwise bury the question it was asked to deliver."""
    original = "Why did you choose Postgres?"
    assert not interviewer._substance_preserved(original, "Why did you choose Postgres? " + "x" * 500)


# ── No templated acknowledgments, whoever wrote them ─────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Great! What broke first?", "What broke first?"),
        ("Understood, and how did you fix it?", "and how did you fix it?"),
        ("Thanks. Why Postgres?", "Why Postgres?"),
        # Stacked openers are stripped to exhaustion, not once.
        ("Great, thanks. Why Postgres?", "Why Postgres?"),
        # A real question that merely STARTS with one of these words is not an
        # acknowledgment and must survive intact.
        ("Good practice looks like what here?", "practice looks like what here?"),
    ],
)
def test_leading_praise_is_stripped(raw, expected) -> None:
    """The brief forbids templated acknowledgments. There are none in the
    product's own strings, but a model at 0.7 opens with one unprompted, which
    reads exactly like the hardcoded filler it was told not to write."""
    assert interviewer._strip_praise(raw) == expected


# ── Every failure path is the product's previous behaviour ───────────────────


@pytest.mark.asyncio
async def test_delivery_falls_back_to_the_stored_question_on_outage(monkeypatch) -> None:
    """An LLM outage costs the phrasing and nothing else."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _boom)
    stored = "Describe a system you designed end to end."
    out = await interviewer.compose_next_question(
        session=None,
        question=stored,
        transcript=[{"speaker": "candidate", "content": "I led the billing rewrite."}],
    )
    assert out == stored


@pytest.mark.asyncio
async def test_the_first_question_is_never_rewritten(monkeypatch) -> None:
    """With an empty transcript there is nothing to condition on, so a model
    call could only paraphrase for its own sake."""
    called = False

    async def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return '{"question": "something else entirely"}'

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _spy)
    stored = "Tell me about your last role."
    out = await interviewer.compose_next_question(
        session=None, question=stored, transcript=[]
    )
    assert out == stored
    assert not called, "the opening question should not cost a model call"


@pytest.mark.asyncio
async def test_a_follow_up_is_refused_once_the_budget_is_spent(monkeypatch) -> None:
    """The ceiling is checked before anything can spend, so an exhausted budget
    costs nothing and cannot be argued with by a provider response."""
    called = False

    async def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return '{"follow_up": "one more thing?"}'

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _spy)
    out = await interviewer.next_follow_up(
        session=None,
        question="Describe a system you designed.",
        answer="I built the billing pipeline and cut p99 to 180ms.",
        transcript=[],
        follow_ups_used=interviewer.MAX_FOLLOW_UPS,
        already_followed_up=False,
    )
    assert out is None
    assert not called


@pytest.mark.asyncio
async def test_a_non_answer_is_never_probed(monkeypatch) -> None:
    """Gibberish is already routed to the unanswered scoring path. Probing it
    would spend budget a real-but-thin answer later has a better claim on, and
    would read as the interviewer failing to notice."""
    called = False

    async def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return '{"follow_up": "say more?"}'

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _spy)
    out = await interviewer.next_follow_up(
        session=None,
        question="Describe a system you designed.",
        answer="asdkjhasd",
        transcript=[],
        follow_ups_used=0,
        already_followed_up=False,
    )
    assert out is None
    assert not called


# ── A non-answer is never met with silence ───────────────────────────────────


@pytest.mark.parametrize(
    "mash",
    # The four answers actually typed into production on 2026-08-05. Every one
    # of them was met with the next scripted question, and the interview reached
    # "Question 8 of 45" without ever remarking that nothing had been answered.
    ["fsjdemd", "xdshfjg,uyytrs", "dwrhejyrkhfbgertyfg", "cvdgrertykfmhgnfrshfmgc"],
)
@pytest.mark.asyncio
async def test_keyboard_mash_is_challenged_not_ignored(monkeypatch, mash) -> None:
    async def _down(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _down)
    out = await interviewer.challenge_non_answer(
        session=None,
        question="Describe a system you designed end to end.",
        answer=mash,
        transcript=[],
    )
    assert out, (
        f"{mash!r} was met with silence. This is the defect the user reported: "
        "the one case a human interviewer certainly reacts to was the one case "
        "the agent was guaranteed to say nothing about."
    )


@pytest.mark.asyncio
async def test_a_real_answer_is_not_challenged(monkeypatch) -> None:
    """The guard must not nag someone who answered. A negative answer is a real
    answer and is scored low on its merits, never re-asked.

    WHOSE JOB THIS IS, as of the classifier landing: `answer_classification`
    decides, and `challenge_non_answer` trusts the label it is handed. So the
    protection for a real answer is that the classifier returns "substantive"
    and `needs_rechallenge` is False, which is asserted in
    tests/test_answer_classification.py. What is asserted HERE is the other
    half of that contract: a label this module does not recognise must produce
    SILENCE rather than a guessed challenge, because a false challenge accuses
    a real candidate of not answering.
    """
    async def _spy(*args, **kwargs):
        raise AssertionError("should not have reached the model")

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _spy)
    for label in ("substantive", "", "something_new", "SUBSTANTIVE"):
        assert await interviewer.challenge_non_answer(
            session=None,
            question="Tell me about Kafka.",
            answer="I have not used Kafka in production.",
            transcript=[],
            label=label,
        ) is None


@pytest.mark.asyncio
async def test_the_challenge_wording_matches_what_went_wrong(monkeypatch) -> None:
    """Telling a candidate who wrote three coherent paragraphs that their reply
    "did not come through" proves the agent cannot tell prose from keyboard
    mash. Each label gets its own outage fallback for that reason."""
    async def _down(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _down)
    seen = {}
    for label in ("gibberish", "empty", "off_topic", "evasive"):
        seen[label] = await interviewer.challenge_non_answer(
            session=None, question="Tell me about Kafka.", answer="...",
            transcript=[], label=label,
        )
    assert all(seen.values()), "every rechallengeable label needs wording"
    assert len(set(seen.values())) == 4, f"wording must differ by label: {seen}"
    # The prose labels must not claim nothing arrived.
    for label in ("off_topic", "evasive"):
        assert "come through" not in seen[label].lower()


def test_the_follow_up_budget_scales_with_the_interview() -> None:
    """A flat five probes across 45 questions left 89% of the conversation
    unable to react to anything the candidate said."""
    assert interviewer.follow_up_budget(45) == 15   # non-managerial
    assert interviewer.follow_up_budget(22) == 7    # CXO
    # Clamped at both ends, so the ceiling is always provable.
    assert interviewer.follow_up_budget(3) == interviewer.MAX_FOLLOW_UPS
    assert interviewer.follow_up_budget(900) == interviewer.MAX_FOLLOW_UPS_CEILING


@pytest.mark.asyncio
async def test_a_model_echoing_null_is_not_shown_as_a_question(monkeypatch) -> None:
    async def _null(*args, **kwargs):
        return '{"follow_up": "null"}'

    monkeypatch.setattr(interviewer.llm_router, "invoke_llm", _null)
    out = await interviewer.next_follow_up(
        session=None,
        question="Describe a system you designed.",
        answer="I built the billing pipeline and cut p99 to 180ms.",
        transcript=[],
        follow_ups_used=0,
        already_followed_up=False,
    )
    assert out is None
