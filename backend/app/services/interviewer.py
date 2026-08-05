"""The conversational half of the unified candidate assessment, as a graph.

WHAT WAS THERE BEFORE, AND WHY IT KEPT BEING WRONG
--------------------------------------------------
Twice now this module has been reported "done" and been visibly not done.

Round one: `api/assessments.respond` was an index into a pre-generated list. No
LLM call happened during the conversation at all, so "the agent has no memory"
was not a prompt-quality problem, there was no agent in the loop to give memory
to.

Round two (2026-08-04): one LLM call was added AFTER each answer to decide
whether to ask a follow-up. That is a real per-turn model call with the real
transcript, and it fixed the memory complaint. But the base questions were
still a static list walked by an index, nothing here imported LangGraph, and
the text the candidate READ for a base question was the stored string, byte for
byte, identical for every candidate. Calling that an adaptive conversational
agent was a stretch, and it was measured as such: `grep -rn "from langgraph"`
returned three files and this was not one of them.

WHAT THIS IS NOW
----------------
Two compiled LangGraph state machines, and every line the candidate reads comes
out of one of them:

    _DECIDE_GRAPH   after an answer: is there something specific worth pressing
                    on, and if so what would a competent interviewer say?
                    budget -> substance -> assess (LLM) -> validate

    _DELIVER_GRAPH  before a base question: say it the way an interviewer would
                    say it here, having heard everything said so far.
                    plan -> compose (LLM) -> validate

Both run against the running transcript, so both can refer back to what the
candidate actually said. Nodes are explicit and each one can refuse: a graph
whose every hop is a plain function is the point, because the failure paths are
the part of this module that has to be right.

THE FOUR THINGS IT MUST NOT BREAK
---------------------------------
Pinned by `tests/test_conversation_flow.py`, and all four survive because the
graphs change WORDING and never BOOKKEEPING:

1. **Scoring and grouping.** A follow-up is answered under the SAME
   `question_key` as the question that produced it, so `answers_by_key` files it
   with that question's other answers. No new key is ever invented.
2. **Billing and completion.** `charge_completed` fires when
   `next_question_index >= len(prompts)`. Neither graph extends the prompt list
   or advances the index.
3. **Termination.** At most ONE follow-up per base question and MAX_FOLLOW_UPS
   per conversation, counted in a PERSISTED column. Total turns are
   `len(prompts) + MAX_FOLLOW_UPS` whatever any model returns.
4. **What is ASKED is fixed; only how it is SAID varies.** `_DELIVER_GRAPH` may
   rephrase and may add a bridging clause. It may not change the substance,
   drop a named technology, or ask a second thing. Every base question is
   scored against its own stored rubric, so a delivery that quietly changed the
   question would be graded against a rubric for a question nobody was asked.
   `_substance_preserved` enforces this and falls back to the stored text.

EVERY FAILURE PATH IS THE PRODUCT'S PREVIOUS BEHAVIOUR
------------------------------------------------------
`next_follow_up` returns None ("ask the next scripted question") and
`compose_next_question` returns the stored text verbatim. Outage, timeout,
malformed JSON, a model echoing "null", a response long enough to be a speech.
A candidate is mid-assessment on a live request, so a provider problem costs the
adaptivity and nothing else. Unlike `_llm_score`'s old fallback, which invented
a grade, this one is honest: it is exactly what the product did yesterday.

TEMPERATURE
-----------
Routed as `conversation_turn`, 0.7, the only task in the product above 0.5.
Phrasing SHOULD vary between candidates; at 0.0 the interviewer repeats itself
verbatim to everyone, which is the scripted feel this module exists to remove.
Scoring stays deterministic (config/llm_providers.TASK_TEMPERATURE).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.services import answer_quality, llm_router

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FOLLOW_UPS",
    "MAX_FOLLOW_UPS_PER_QUESTION",
    "follow_up_budget",
    "next_follow_up",
    "compose_next_question",
    "challenge_non_answer",
]

#: Floor for a short interview, and the value this was FIXED at until
#: 2026-08-05. Five probes across a non-managerial assessment's 45 questions
#: means at most 11% of the interview could react to anything the candidate
#: said; the other 89% was a script whatever they typed. That is the structural
#: reason it read as a form with a chat skin, and no amount of prompt tuning
#: reaches it. `follow_up_budget` now scales the ceiling with the interview's
#: actual length and this is only its lower bound.
MAX_FOLLOW_UPS = 5

#: Hard upper bound, whatever the arithmetic says. Bounds the candidate's time
#: and the token spend per assessment, and keeps an interview that can ask "one
#: more thing" provably finite.
MAX_FOLLOW_UPS_CEILING = 15

#: One probe per this many base questions. At 45 questions that is 15, the
#: ceiling; at 22 (CXO) it is 7. Roughly "the interviewer pressed on a third of
#: what I said", which is what an attentive human interview feels like.
QUESTIONS_PER_FOLLOW_UP = 3


def follow_up_budget(question_count: int) -> int:
    """How many probes this conversation may spend, scaled to its length.

    A fixed ceiling is wrong in both directions: five is nearly nothing across a
    45-question non-managerial interview, and would be an interrogation across a
    10-question CXO one. Clamped at both ends so the result is always between
    MAX_FOLLOW_UPS and MAX_FOLLOW_UPS_CEILING.
    """
    scaled = int(question_count) // QUESTIONS_PER_FOLLOW_UP
    return max(MAX_FOLLOW_UPS, min(MAX_FOLLOW_UPS_CEILING, scaled))

#: One per base question. Two consecutive probes on the same point is where an
#: interview starts to feel like cross-examination, and it is also what would
#: let a single evasive candidate consume the whole conversation budget.
MAX_FOLLOW_UPS_PER_QUESTION = 1

#: How much transcript either graph sees. Enough to refer back without resending
#: an entire 45-question interview on every turn, which would blow the token
#: ceiling on the later questions of a long assessment.
TRANSCRIPT_TURNS = 6

#: A follow-up longer than this is not a question, it is a speech.
MAX_FOLLOW_UP_CHARS = 320

#: A delivered base question may gain a bridging clause, not a paragraph. Scaled
#: off the stored text rather than fixed, because a two-line scenario question
#: and a one-line factual one have very different honest ceilings.
DELIVERY_GROWTH_FACTOR = 2.0
DELIVERY_MIN_CEILING = 400

_DECIDE_SYSTEM = (
    "You are conducting a job interview. You have just received an answer to "
    "one question and must decide whether to press on it before moving to the "
    "next topic.\n"
    "\n"
    "Ask a follow-up ONLY when the answer leaves something specific and "
    "material unsaid: no concrete example, a claim with no outcome, a decision "
    "with no reasoning, or an answer that talks around the question. Do NOT "
    "follow up merely because an answer is short. A complete short answer is a "
    "complete answer, and a negative answer such as 'I have not used that' is "
    "complete and should be accepted without pressing.\n"
    "\n"
    "When you do ask, write ONE question a competent human interviewer would "
    "say out loud. Refer naturally to what the candidate actually said, using "
    "their own words where it helps. Never repeat a question already asked, "
    "never ask several things at once, and never evaluate, praise, score or "
    "reassure the candidate.\n"
    "\n"
    'Return JSON: {"follow_up": <string or null>}. Use null to move on.'
)

_DELIVER_SYSTEM = (
    "You are conducting a job interview. You are about to ask the next "
    "question on your list, and your job is to SAY it the way an interviewer "
    "would say it at this point in this conversation.\n"
    "\n"
    "HARD RULES. The question you ask must be the SAME question:\n"
    "- Keep every specific term, technology, tool, metric and constraint that "
    "appears in the original. Do not generalise 'Kafka' to 'a message queue'.\n"
    "- Do not add a second question, and do not add an example answer.\n"
    "- Do not make it easier, harder, or narrower.\n"
    "\n"
    "WHAT YOU MAY DO. Speak it naturally, and where the conversation genuinely "
    "warrants it, open with a SHORT clause connecting it to something the "
    "candidate already said. Only do that when the connection is real.\n"
    "\n"
    "Never evaluate, praise, score, thank or reassure the candidate. Do not "
    "say 'great', 'perfect', 'understood' or 'let us proceed'. Do not number "
    "the question or mention how many are left.\n"
    "\n"
    'Return JSON: {"question": <string>}.'
)

#: Words that turn an interviewer into a cheerleader. The brief called these out
#: by name, and they are checked rather than merely forbidden in the prompt: a
#: prompt instruction is a request, not a guarantee (the same reasoning that
#: puts a Postgres CHECK behind the "Culture" ban).
_PRAISE = re.compile(
    r"^\s*(great|perfect|excellent|awesome|nice|good|well done|thanks|thank you"
    r"|understood|got it|makes sense|interesting|impressive)\b[\s,.!:-]*",
    re.IGNORECASE,
)


# ── Shared helpers ───────────────────────────────────────────────────────────


def _recent(transcript: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """The last few turns, oldest first, as plain speaker/text pairs.

    This is the agent's MEMORY. Read from `assessment_messages` by the caller
    rather than accumulated in a process, because `respond` is one stateless
    HTTP request per turn and nothing holds the conversation between them.
    """
    rows = []
    for message in (transcript or [])[-TRANSCRIPT_TURNS * 2 :]:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        speaker = "interviewer" if message.get("speaker") == "agent" else "candidate"
        rows.append({"speaker": speaker, "text": content[:600]})
    return rows


def _strip_praise(text: str) -> str:
    """Remove a leading acknowledgment, however the model phrased it.

    The brief forbids templated acknowledgments outright. There are none in the
    product's own strings, but a model at temperature 0.7 will happily open with
    "Great, and..." unprompted, which reads exactly like the hardcoded filler it
    was told not to write.
    """
    previous = None
    while previous != text:
        previous = text
        text = _PRAISE.sub("", text, count=1)
    return text.strip()


def _tokens(text: str) -> set[str]:
    """Specific terms worth protecting: anything containing a digit or internal
    punctuation, plus anything capitalised MID-sentence.

    Deliberately crude -- it only has to catch 'Kafka', 'p99' and 'CI/CD'.

    The mid-sentence qualifier is load-bearing rather than fussy. Counting
    sentence-initial capitals would make "Describe how you tuned Kafka..."
    protect the word "describe", so the perfectly good rewrite "You mentioned
    throughput earlier, so: how did you tune Kafka consumer lag?" would be
    rejected for dropping a verb. Every legitimate rewrite reopens the sentence,
    so that rule would have refused essentially all of them and silently reduced
    this graph back to the stored text it replaced.
    """
    found: set[str] = set()
    # A new sentence starts the string or follows . ? ! : or a newline.
    for sentence in re.split(r"(?<=[.?!:])\s+|\n+", text):
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9./+#_-]*", sentence)
        for position, raw_word in enumerate(words):
            # TRAILING punctuation is the sentence's, not the term's. The
            # character class above has to allow '.' and '-' so that "3.5",
            # "CI/CD" and "end-to-end" survive as single tokens, which means it
            # also swallows the full stop on "load." -- and that made an
            # ordinary word look like a specific term, so "load." was demanded
            # of a rewrite that had written "load?".
            word = raw_word.rstrip(".,;:?!()-")
            if not word:
                continue
            specific = (
                any(ch.isdigit() for ch in word)
                or any(ch in "./+#_-" for ch in word)
                # Capitalised, not an all-caps shout, and not the word that
                # merely happens to open the sentence.
                or (
                    position > 0
                    and word[:1].isupper()
                    and not word.isupper()
                    and len(word) > 2
                )
            )
            if specific:
                found.add(word.lower())
    return found


def _substance_preserved(original: str, delivered: str) -> bool:
    """Whether a delivered question still asks the ORIGINAL question.

    The specific terms are the load-bearing part. A rewrite that dropped
    'Kafka' would be scored against a rubric for a Kafka question the candidate
    was never asked, which is the one failure mode that would reach a client as
    an unexplainable grade.
    """
    if not delivered:
        return False
    ceiling = max(DELIVERY_MIN_CEILING, int(len(original) * DELIVERY_GROWTH_FACTOR))
    if len(delivered) > ceiling:
        return False
    missing = _tokens(original) - _tokens(delivered)
    return not missing


# ── Graph 1: should we press on that answer? ─────────────────────────────────


class _DecideState(TypedDict, total=False):
    session: Any
    question: str
    answer: str
    transcript: list[dict[str, Any]]
    follow_ups_used: int
    already_followed_up: bool
    budget: int
    # working
    stop: bool
    raw: str | None
    follow_up: str | None


_CHALLENGE_SYSTEM = (
    "You are conducting a job interview. The candidate's last reply was not an "
    "answer: it was keyboard mash, a single word, or an empty gesture.\n"
    "\n"
    "Say what a competent interviewer would say. Note plainly that the reply "
    "did not come through as an answer, and ask them for the question again in "
    "one sentence. Be matter of fact and not unkind: assume a slip or a "
    "misunderstanding, never accuse them of anything.\n"
    "\n"
    "Do NOT quote their non-answer back at them, do NOT praise, evaluate or "
    "score, and do NOT move on to another topic.\n"
    "\n"
    'Return JSON: {"challenge": <string>}.'
)

#: Used when the model is unavailable. This is NOT the templated filler the
#: brief forbids: filler asserts a reaction that did not happen ("Appreciate the
#: detail." to gibberish), whereas this is a true statement about what actually
#: arrived, and the alternative is the silence that made the agent look like it
#: was not reading at all. Deterministic on purpose -- an outage is exactly when
#: a candidate is most likely to be typing into a void.
_CHALLENGE_FALLBACK = (
    "That did not come through as an answer. Could you take another go at it?"
)


async def _decide_budget(state: _DecideState) -> _DecideState:
    """Budget first, before anything can spend.

    Checked without touching the model, so an exhausted budget costs nothing and
    the ceiling cannot be argued with by a provider response.
    """
    used = int(state.get("follow_ups_used") or 0)
    # The caller passes a length-scaled budget; MAX_FOLLOW_UPS is the floor used
    # when it did not, so an older caller keeps the previous ceiling rather than
    # accidentally getting an unbounded one.
    budget = int(state.get("budget") or MAX_FOLLOW_UPS)
    if state.get("already_followed_up") or used >= budget:
        return {"stop": True}
    return {"stop": False}


async def _decide_substance(state: _DecideState) -> _DecideState:
    """A non-answer is not worth a follow-up.

    Gibberish is already routed to the unanswered scoring path by
    `services/answer_quality`. Probing keyboard mash would spend budget a
    real-but-thin answer later in the interview has a much better claim on, and
    it would read as the interviewer failing to notice.
    """
    if not answer_quality.is_substantive(state.get("answer") or ""):
        return {"stop": True}
    return {"stop": False}


async def _decide_assess(state: _DecideState) -> _DecideState:
    """The model call. Every exception becomes stop=True, never a raised error."""
    payload = {
        "current_question": state.get("question"),
        "candidate_answer": state.get("answer"),
        "conversation_so_far": _recent(state.get("transcript")),
    }
    try:
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            [
                {"role": "system", "content": _DECIDE_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format_json=True,
            session=state.get("session"),
        )
        return {"raw": raw, "stop": False}
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. A candidate is waiting on this request, and every
        # failure here -- outage, timeout, malformed JSON, a response that is
        # not JSON at all -- has the same correct answer: ask the next scripted
        # question. Logged at info: this is a degraded path, not an error an
        # operator must act on.
        logger.info("interviewer.follow_up_unavailable error=%s", type(exc).__name__)
        return {"stop": True}


async def _decide_validate(state: _DecideState) -> _DecideState:
    """Reject anything that is not a single usable question."""
    if state.get("stop"):
        return {"follow_up": None}
    try:
        value = json.loads(state.get("raw") or "").get("follow_up")
    except Exception:  # noqa: BLE001
        return {"follow_up": None}
    if value is None:
        return {"follow_up": None}
    text = _strip_praise(" ".join(str(value).split()))
    if not text:
        return {"follow_up": None}
    if len(text) > MAX_FOLLOW_UP_CHARS:
        # Truncating would produce a question with no question mark and,
        # potentially, half a sentence. Dropping it just moves the interview on.
        return {"follow_up": None}
    # A model answering with its own instructions, or with an empty gesture,
    # must not be shown to a candidate as an interview question.
    if text.lower() in {"null", "none", "n/a", "no", "-"}:
        return {"follow_up": None}
    return {"follow_up": text}


def _decide_route(state: _DecideState) -> str:
    return "validate" if state.get("stop") else "continue"


def _build_decide_graph():
    graph = StateGraph(_DecideState)
    graph.add_node("budget", _decide_budget)
    graph.add_node("substance", _decide_substance)
    graph.add_node("assess", _decide_assess)
    graph.add_node("validate", _decide_validate)
    graph.add_edge(START, "budget")
    # Each gate short-circuits straight to validate, which turns a stop into the
    # None the caller reads as "ask the next scripted question".
    graph.add_conditional_edges(
        "budget", _decide_route, {"continue": "substance", "validate": "validate"}
    )
    graph.add_conditional_edges(
        "substance", _decide_route, {"continue": "assess", "validate": "validate"}
    )
    graph.add_edge("assess", "validate")
    graph.add_edge("validate", END)
    return graph.compile()


# ── Graph 2: how do we say the next question? ────────────────────────────────


class _DeliverState(TypedDict, total=False):
    session: Any
    question: str
    transcript: list[dict[str, Any]]
    # working
    stop: bool
    raw: str | None
    delivered: str


async def _deliver_plan(state: _DeliverState) -> _DeliverState:
    """The opening question has nothing to connect to.

    With an empty transcript there is no prior answer to condition on, so a
    model call could only produce a paraphrase for its own sake, against the
    one question where the stored wording was written to stand alone.
    """
    question = (state.get("question") or "").strip()
    if not question:
        return {"stop": True}
    if not _recent(state.get("transcript")):
        return {"stop": True}
    return {"stop": False}


async def _deliver_compose(state: _DeliverState) -> _DeliverState:
    payload = {
        "question_to_ask": state.get("question"),
        "conversation_so_far": _recent(state.get("transcript")),
    }
    try:
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            [
                {"role": "system", "content": _DELIVER_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format_json=True,
            session=state.get("session"),
        )
        return {"raw": raw, "stop": False}
    except Exception as exc:  # noqa: BLE001
        logger.info("interviewer.delivery_unavailable error=%s", type(exc).__name__)
        return {"stop": True}


async def _deliver_validate(state: _DeliverState) -> _DeliverState:
    """Fall back to the STORED text on any doubt.

    The stored question is always a correct thing to ask. A rewrite is only ever
    an improvement in tone, so there is never a reason to accept a doubtful one.
    """
    original = state.get("question") or ""
    if state.get("stop"):
        return {"delivered": original}
    try:
        value = json.loads(state.get("raw") or "").get("question")
    except Exception:  # noqa: BLE001
        return {"delivered": original}
    text = _strip_praise(" ".join(str(value or "").split()))
    if not _substance_preserved(original, text):
        logger.info("interviewer.delivery_rejected reason=substance")
        return {"delivered": original}
    return {"delivered": text}


def _deliver_route(state: _DeliverState) -> str:
    return "validate" if state.get("stop") else "continue"


def _build_deliver_graph():
    graph = StateGraph(_DeliverState)
    graph.add_node("plan", _deliver_plan)
    graph.add_node("compose", _deliver_compose)
    graph.add_node("validate", _deliver_validate)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", _deliver_route, {"continue": "compose", "validate": "validate"}
    )
    graph.add_edge("compose", "validate")
    graph.add_edge("validate", END)
    return graph.compile()


# Compiled once at import. Building a StateGraph per turn would add latency to a
# request a candidate is waiting on, for no behavioural difference.
_DECIDE_GRAPH = _build_decide_graph()
_DELIVER_GRAPH = _build_deliver_graph()


# ── Public entry points ──────────────────────────────────────────────────────


async def next_follow_up(
    *,
    session: Any,
    question: str,
    answer: str,
    transcript: list[dict[str, Any]] | None,
    follow_ups_used: int,
    already_followed_up: bool,
    budget: int | None = None,
) -> str | None:
    """One adaptive follow-up, or None to ask the next scripted question.

    `budget` is this conversation's length-scaled ceiling
    (`follow_up_budget(len(prompts))`); it defaults to MAX_FOLLOW_UPS so a
    caller that does not pass one keeps the previous behaviour rather than
    losing its ceiling.

    Every other part of the signature is unchanged, deliberately: it is the seam
    the conversation-flow tests patch to pin the four invariants, and those
    tests are the only thing standing between an edit here and a billing or
    scoring defect that would not show up until a report was wrong.
    """
    try:
        result = await _DECIDE_GRAPH.ainvoke(
            {
                "session": session,
                "question": question,
                "answer": answer,
                "transcript": transcript or [],
                "follow_ups_used": follow_ups_used,
                "already_followed_up": already_followed_up,
                "budget": budget if budget is not None else MAX_FOLLOW_UPS,
            }
        )
    except Exception as exc:  # noqa: BLE001
        # The graph itself failing (not a node inside it) still must not cost a
        # candidate their assessment.
        logger.info("interviewer.decide_graph_failed error=%s", type(exc).__name__)
        return None
    return result.get("follow_up")


async def challenge_non_answer(
    *,
    session: Any,
    question: str,
    answer: str,
    transcript: list[dict[str, Any]] | None,
) -> str | None:
    """Push back on a non-answer instead of silently asking the next question.

    THE DEFECT THIS FIXES, OBSERVED LIVE 2026-08-05
    -----------------------------------------------
    A candidate typed `fsjdemd`, then `xdshfjg,uyytrs`, then
    `dwrhejyrkhfbgertyfg`, then `cvdgrertykfmhgnfrshfmgc`. The agent asked the
    next scripted question each time and reached "Question 8 of 45" without ever
    remarking that nothing had been answered.

    That was a direct consequence of the follow-up path's own budget guard:
    `_decide_substance` sees a non-answer and returns "no probe" to avoid
    spending a scarce follow-up on keyboard mash. The reasoning was sound for
    PROBING and produced the worst possible behaviour overall, because the one
    case where any human interviewer certainly reacts became the one case the
    agent was guaranteed to be silent on.

    So a non-answer now gets its own response, and it is deliberately NOT a
    follow-up:

      * It does not consume the follow-up budget. Probing a thin-but-real answer
        later in the interview is worth more, and asking someone to actually
        answer is not a probe.
      * It is bounded by construction. The re-ask is delivered through the same
        `pending_prompt` mechanism, and a pending prompt suppresses any further
        reaction on the turn that answers it, so there is at most ONE per base
        question and total turns stay bounded by
        2 * len(prompts) + follow_up_budget. No new column, and still provably
        finite.
      * It changes NO scoring. The non-answer is already recorded and already
        routes to UNANSWERED_SCORE via `services/answer_quality`. This is the
        conversation reacting, not the scorer being overridden. A candidate who
        mashes the keyboard twice is still graded Not Matching on that question.

    Returns None when the answer is substantive, meaning "nothing to push back
    on"; the caller then runs the normal follow-up decision.
    """
    if answer_quality.is_substantive(answer):
        return None

    payload = {
        "question_asked": question,
        "conversation_so_far": _recent(transcript),
    }
    try:
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            [
                {"role": "system", "content": _CHALLENGE_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format_json=True,
            session=session,
        )
        text = _strip_praise(" ".join(str(json.loads(raw).get("challenge") or "").split()))
    except Exception as exc:  # noqa: BLE001
        logger.info("interviewer.challenge_unavailable error=%s", type(exc).__name__)
        return _CHALLENGE_FALLBACK
    if not text or len(text) > MAX_FOLLOW_UP_CHARS:
        return _CHALLENGE_FALLBACK
    return text


async def compose_next_question(
    *,
    session: Any,
    question: str,
    transcript: list[dict[str, Any]] | None,
) -> str:
    """The next base question, said the way an interviewer would say it here.

    Returns the STORED text unchanged whenever the rewrite is unavailable or
    doubtful, so the worst case is exactly the product's previous behaviour.
    What is asked never changes; only how it is said.
    """
    try:
        result = await _DELIVER_GRAPH.ainvoke(
            {
                "session": session,
                "question": question,
                "transcript": transcript or [],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("interviewer.deliver_graph_failed error=%s", type(exc).__name__)
        return question
    return result.get("delivered") or question
