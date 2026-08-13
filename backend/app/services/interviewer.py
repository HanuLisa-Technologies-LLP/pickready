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

from app.prompts import fragments, registry
from app.services import agent_loop, answer_quality, llm_router

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FOLLOW_UPS",
    "MAX_FOLLOW_UPS_PER_QUESTION",
    "follow_up_budget",
    "next_follow_up",
    "compose_next_question",
    "challenge_non_answer",
]

#: Floor for a short interview.
#:
#: LOWERED FROM 5 (Draft v4), and it had to be. The floor was set when the
#: shortest interview in the product was a CXO's 22 questions; the grade ranges
#: now bottom out at 7, and five probes across seven questions is the
#: interrogation the scaling was introduced to avoid. Two keeps a short
#: interview able to press on something without the pressing becoming the
#: interview.
MAX_FOLLOW_UPS = 2

#: Hard upper bound, whatever the arithmetic says. Bounds the candidate's time
#: and the token spend per assessment, and keeps an interview that can ask "one
#: more thing" provably finite. Above what the ratio can currently produce
#: (28 // 3 = 9), and deliberately kept there: it is a backstop against a future
#: count change, not a number the arithmetic is tuned to reach.
MAX_FOLLOW_UPS_CEILING = 15

#: One probe per this many base questions. At 28 that is 9; at 7 (CXO) the floor
#: takes over at 2. Roughly "the interviewer pressed on a third of what I said",
#: which is what an attentive human interview feels like.
QUESTIONS_PER_FOLLOW_UP = 3


def follow_up_budget(question_count: int) -> int:
    """How many probes this conversation may spend, scaled to its length.

    A fixed ceiling is wrong in both directions: it is nearly nothing across a
    long non-managerial interview and an interrogation across a short CXO one.
    Clamped at both ends so the result is always between MAX_FOLLOW_UPS and
    MAX_FOLLOW_UPS_CEILING.
    """
    scaled = int(question_count) // QUESTIONS_PER_FOLLOW_UP
    return max(MAX_FOLLOW_UPS, min(MAX_FOLLOW_UPS_CEILING, scaled))

#: One per base question. Two consecutive probes on the same point is where an
#: interview starts to feel like cross-examination, and it is also what would
#: let a single evasive candidate consume the whole conversation budget.
MAX_FOLLOW_UPS_PER_QUESTION = 1

#: How much transcript either graph sees. Enough to refer back without resending
#: an entire interview on every turn, which would blow the token ceiling on the
#: later questions of a long assessment.
TRANSCRIPT_TURNS = 6

#: A follow-up longer than this is not a question, it is a speech.
MAX_FOLLOW_UP_CHARS = 320

#: A delivered base question may gain a bridging clause, not a paragraph. Scaled
#: off the stored text rather than fixed, because a two-line scenario question
#: and a one-line factual one have very different honest ceilings.
DELIVERY_GROWTH_FACTOR = 2.0
DELIVERY_MIN_CEILING = 400

#: Text in `app/prompts/interview_follow_up_decision.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_DECIDE_SYSTEM = registry.render("interview_follow_up_decision")

#: Text in `app/prompts/interview_write_question.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_GENERATE_SYSTEM = registry.render(
    "interview_write_question",
    one_question=fragments.ONE_QUESTION,
    no_evaluation=fragments.NO_EVALUATION,
    candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
)

#: Text in `app/prompts/interview_deliver_question.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_DELIVER_SYSTEM = registry.render(
    "interview_deliver_question", no_evaluation=fragments.NO_EVALUATION
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


#: What the interviewer is reacting to, in its own words. The wording has to
#: differ by KIND: telling someone who wrote three coherent paragraphs that
#: their reply "did not come through" is obviously wrong and reads as a bot
#: that cannot tell prose from keyboard mash.
_CHALLENGE_BY_LABEL: dict[str, str] = {
    "gibberish": (
        "The candidate's last reply was keyboard mash or a single stray token. "
        "Note plainly that it did not come through as an answer and ask them "
        "to have another go. Assume a slip, never accuse them of anything."
    ),
    "empty": (
        "The candidate submitted nothing, or only punctuation. Note that "
        "nothing came through and ask them to answer the question."
    ),
    "off_topic": (
        "The candidate wrote a real answer, but to a different question than "
        "the one asked. Acknowledge briefly what they did address, then steer "
        "them back to what you actually asked and ask it again clearly."
    ),
    "shallow": (
        "The candidate addressed the right topic but did not provide the "
        "specific example, action, reasoning, measurement, or outcome the "
        "question requested. Name the missing kind of evidence and ask for it "
        "directly."
    ),
    "evasive": (
        "The candidate talked around the question: generalities, no specifics, "
        "or a softer version of what was asked. Name the specific thing you "
        "still need -- an example, a number they owned, a decision they made "
        "-- and ask for that directly."
    ),
}

def challenge_prompt(situation: str) -> str:
    """The challenge system prompt, with the situation clause already in it.

    A FUNCTION rather than a template plus a `.replace()` at the call site,
    because the call site is what got this wrong before. `.format()` was used
    here and raised KeyError on the literal JSON braces at the end of the
    prompt (`{"challenge": ...}`); the broad except below turned that into the
    deterministic fallback, so every challenge a candidate ever saw was the
    canned sentence and never a composed one. Functional, and unable to refer
    to anything the candidate had said, which is most of the point.

    It was caught by reading a live transcript, not by a test: the fallback is
    a legitimate output, so nothing failed.

    Two things stop it recurring. The prompt now lives in
    `app/prompts/interview_challenge.txt` and is rendered by the registry,
    which uses `string.Template` precisely because these prompts are full of
    JSON braces. And there is no longer a raw template to substitute into by
    hand: a caller can only ask for the finished prompt.
    """
    return registry.render("interview_challenge", situation=situation)

#: Used when the model is unavailable. These are NOT the templated filler the
#: brief forbids: filler asserts a reaction that did not happen ("Appreciate the
#: detail." to gibberish), whereas each of these is a true statement about what
#: actually arrived, and the alternative is the silence that made the agent look
#: like it was not reading at all. Deterministic on purpose -- an outage is
#: exactly when a candidate is most likely to be typing into a void.
#:
#: Keyed by label for the same reason the prompts are: telling someone who wrote
#: real prose that nothing "came through" is a worse failure than saying nothing.
_CHALLENGE_FALLBACK: dict[str, str] = {
    "gibberish": "That did not come through as an answer. Could you take another go at it?",
    "empty": "Nothing came through there. Could you answer the question?",
    "off_topic": (
        "That reads as an answer to something else. Could you come back to what "
        "I asked?"
    ),
    "shallow": (
        "You are on the right topic, but I still need a concrete example and "
        "what you personally did. Could you add those details?"
    ),
    "evasive": (
        "Could you be more specific? A concrete example of your own would help "
        "more than the general picture."
    ),
}
_CHALLENGE_FALLBACK_DEFAULT = _CHALLENGE_FALLBACK["gibberish"]


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


#: How freely the next question may be written, and it is decided by how the
#: answer will be SCORED. This is the load-bearing distinction in this module.
#:
#:   GENERATE  A PPI answer is scored against its COMPETENCY, across all the
#:             answers filed under it (functional_assessment, the competency
#:             scorer). No per-question rubric exists, so the question may be
#:             written fresh from the JD, the resume and the transcript. This is
#:             where a conversation can actually be adaptive.
#:
#:   REWORD    A technical answer is scored against THAT QUESTION'S OWN stored
#:             prompt and rubric_json (functional_assessment, `_llm_score`).
#:             Generating a fresh technical question would grade the answer
#:             against a rubric written for a question nobody was asked, and
#:             the candidate would receive an unexplainable mark. So the
#:             substance is pinned and only the phrasing may move.
MODE_GENERATE = "generate"
MODE_REWORD = "reword"


class _DeliverState(TypedDict, total=False):
    session: Any
    question: str            # the stored question, and the fallback
    mode: str
    competency: str          # what this turn must probe (generate mode)
    competency_hint: str
    jd_excerpt: str
    resume_excerpt: str
    asked_before: list[str]
    transcript: list[dict[str, Any]]
    # working
    stop: bool
    raw: str | None
    delivered: str


async def _deliver_plan(state: _DeliverState) -> _DeliverState:
    """Decide whether this turn is worth a model call at all."""
    question = (state.get("question") or "").strip()
    mode = state.get("mode") or MODE_REWORD
    if not question and mode == MODE_REWORD:
        # Nothing to reword and nothing to fall back to.
        return {"stop": True}
    if mode == MODE_REWORD and not _recent(state.get("transcript")):
        # The opening question has nothing to connect to. With an empty
        # transcript a reword could only paraphrase for its own sake, against
        # the one question whose stored wording was written to stand alone.
        return {"stop": True}
    if mode == MODE_GENERATE and not (state.get("competency") or "").strip():
        # Generating without a named criterion would produce an answer that no
        # scorer has anywhere to file.
        return {"stop": True}
    return {"stop": False}


async def _deliver_compose(state: _DeliverState) -> _DeliverState:
    """The model call, inside the bounded loop.

    THIS NODE USED TO BE ONE SHOT, AND THAT COST A MEASURABLE AMOUNT OF THE
    ADAPTIVITY THE WHOLE MODULE EXISTS FOR
    ----------------------------------------------------------------------
    `_deliver_validate` below rejects a delivery for four specific, testable
    reasons: it dropped a named technology, it grew into an essay, it repeated
    ground already covered, or it is not a question at all. Each rejection fell
    straight through to the STORED text -- so the failure mode of "the model
    said 'a message queue' instead of 'Kafka'" was identical to the failure mode
    of "every provider is down", and the candidate read a scripted line either
    way while the logs recorded a rejection nobody could act on.

    Every one of those is a defect a model fixes when told. The loop tells it,
    once, and only then falls back. The criteria are unchanged and still live in
    `_deliver_validate`, which is also what keeps the LangGraph node's contract
    intact: it still returns `{"raw": ...}` or `{"stop": True}`, so the graph
    around it is untouched.
    """
    generate = (state.get("mode") or MODE_REWORD) == MODE_GENERATE
    if generate:
        system = _GENERATE_SYSTEM
        payload = {
            "competency_to_probe": state.get("competency"),
            "what_it_means": state.get("competency_hint") or "",
            "job_description": (state.get("jd_excerpt") or "")[:2500],
            "candidate_resume": (state.get("resume_excerpt") or "")[:2500],
            "conversation_so_far": _recent(state.get("transcript")),
            # Named explicitly so the model can avoid repeating ground. Asking
            # the same thing twice is the single most obvious tell that an
            # interviewer is not listening.
            "already_asked": list(state.get("asked_before") or [])[-20:],
        }
    else:
        system = _DELIVER_SYSTEM
        payload = {
            "question_to_ask": state.get("question"),
            "conversation_so_far": _recent(state.get("transcript")),
        }

    original = state.get("question") or ""
    asked_before = list(state.get("asked_before") or [])

    async def execute(reflection: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            messages,
            response_format_json=True,
            session=state.get("session"),
        )
        # Parsed here so a malformed body is a FAILED ATTEMPT the loop can
        # retry, rather than something `_deliver_validate` silently converts
        # into the stored text with no second chance.
        text = _strip_praise(" ".join(str(json.loads(raw).get("question") or "").split()))
        if not text:
            raise ValueError("no question in response")
        return text

    def evaluate(text: str) -> agent_loop.Critique:
        # Deliberately the SAME rules `_deliver_validate` applies, phrased as
        # instructions. Duplicating the checks would let the two drift and the
        # loop would then "fix" something the validator still rejects.
        if generate:
            if len(text) > max(DELIVERY_MIN_CEILING, len(original) * 3):
                return agent_loop.reject(
                    "ask one short question; the previous attempt was too long"
                )
            if _is_repeat(text, asked_before):
                return agent_loop.reject(
                    "the candidate has already been asked this; ask about a "
                    "different aspect of the competency"
                )
            return agent_loop.ok()
        if not _substance_preserved(original, text):
            missing = sorted(_tokens(original) - _tokens(text))
            if missing:
                return agent_loop.reject(
                    "keep every specific term from the original question; the "
                    "previous attempt dropped: " + ", ".join(missing)
                )
            return agent_loop.reject(
                "say the same question more briefly; the previous attempt grew "
                "well beyond the original"
            )
        return agent_loop.ok()

    result = await agent_loop.run_loop(
        name=f"interviewer_deliver_{state.get('mode') or MODE_REWORD}",
        execute=execute,
        evaluate=evaluate,
        # Empty means "no usable delivery", which `_deliver_validate` reads as
        # the stored question -- exactly the product's previous behaviour.
        fallback="",
        max_attempts=agent_loop.INTERACTIVE_ATTEMPTS,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )
    if result.degraded:
        logger.info(
            "interviewer.delivery_unavailable mode=%s attempts=%d error=%s reasons=%s",
            state.get("mode"), result.attempts, result.error, list(result.reasons),
        )
        return {"stop": True}
    # Re-wrapped into the shape `_deliver_validate` already parses, so that node
    # stays the single place the acceptance rules are enforced.
    return {"raw": json.dumps({"question": result.value}), "stop": False}


def _is_repeat(text: str, asked_before: list[str] | None) -> bool:
    """Whether this is a question the candidate has already been asked.

    Compared on the specific terms rather than the exact string, because a model
    asked not to repeat itself will happily reword the same question.
    """
    tokens = _tokens(text)
    if not tokens:
        return False
    for previous in asked_before or []:
        earlier = _tokens(previous)
        if not earlier:
            continue
        overlap = len(tokens & earlier) / max(len(tokens | earlier), 1)
        if overlap > 0.8:
            return True
    return False


async def _deliver_validate(state: _DeliverState) -> _DeliverState:
    """Fall back to the STORED question on any doubt.

    The stored question is always a correct thing to ask: under REWORD it is the
    rubric's own question, and under GENERATE it is the question that was
    pre-generated for this competency from this candidate's resume. So a
    doubtful result is never worth accepting.
    """
    original = state.get("question") or ""
    if state.get("stop"):
        return {"delivered": original}
    try:
        value = json.loads(state.get("raw") or "").get("question")
    except Exception:  # noqa: BLE001
        return {"delivered": original}
    text = _strip_praise(" ".join(str(value or "").split()))
    if not text:
        return {"delivered": original}

    if (state.get("mode") or MODE_REWORD) == MODE_REWORD:
        if not _substance_preserved(original, text):
            logger.info("interviewer.delivery_rejected reason=substance")
            return {"delivered": original}
        return {"delivered": text}

    # GENERATE mode. There is no original to compare against, so the checks are
    # that it is a question, that it is not an essay, and that it is not ground
    # already covered.
    if len(text) > max(DELIVERY_MIN_CEILING, len(original) * 3):
        logger.info("interviewer.delivery_rejected reason=length")
        return {"delivered": original}
    if _is_repeat(text, state.get("asked_before")):
        logger.info("interviewer.delivery_rejected reason=repeat")
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
    label: str = "gibberish",
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

    `label` comes from `services/answer_classification` and decides BOTH the
    prompt and the outage fallback. It is not cosmetic: telling a candidate who
    wrote three coherent paragraphs that their reply "did not come through" is a
    worse failure than saying nothing, because it proves the agent cannot tell
    prose from keyboard mash.

    Returns None when there is nothing to push back on; the caller then runs the
    normal follow-up decision.
    """
    situation = _CHALLENGE_BY_LABEL.get(label)
    if situation is None:
        # An unknown label means the classifier degraded or changed under us.
        # Saying nothing is the safe direction: a false challenge accuses a real
        # candidate of not answering.
        return None

    fallback = _CHALLENGE_FALLBACK.get(label, _CHALLENGE_FALLBACK_DEFAULT)
    payload = {
        "question_asked": question,
        "conversation_so_far": _recent(transcript),
    }
    try:
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            [
                {
                    "role": "system",
                    "content": challenge_prompt(situation),
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format_json=True,
            session=session,
        )
        text = _strip_praise(" ".join(str(json.loads(raw).get("challenge") or "").split()))
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "interviewer.challenge_unavailable label=%s error=%s",
            label, type(exc).__name__,
        )
        return fallback
    if not text or len(text) > MAX_FOLLOW_UP_CHARS:
        return fallback
    return text


async def compose_next_question(
    *,
    session: Any,
    question: str,
    transcript: list[dict[str, Any]] | None,
    mode: str = MODE_REWORD,
    competency: str = "",
    competency_hint: str = "",
    jd_excerpt: str = "",
    resume_excerpt: str = "",
    asked_before: list[str] | None = None,
) -> str:
    """The next base question, written for THIS candidate at THIS point.

    Two modes, and which one applies is decided by how the answer will be
    SCORED, not by preference. See MODE_GENERATE / MODE_REWORD above: a PPI
    answer is graded against its competency so the question may be written
    fresh; a technical answer is graded against its own stored rubric so only
    the phrasing may move.

    Returns the STORED text whenever the result is unavailable or doubtful, so
    the worst case is exactly the product's previous behaviour.
    """
    try:
        result = await _DELIVER_GRAPH.ainvoke(
            {
                "session": session,
                "question": question,
                "transcript": transcript or [],
                "mode": mode,
                "competency": competency,
                "competency_hint": competency_hint,
                "jd_excerpt": jd_excerpt,
                "resume_excerpt": resume_excerpt,
                "asked_before": asked_before or [],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("interviewer.deliver_graph_failed error=%s", type(exc).__name__)
        return question
    return result.get("delivered") or question
