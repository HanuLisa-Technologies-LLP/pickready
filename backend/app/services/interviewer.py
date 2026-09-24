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
One compiled LangGraph state machine, and the deterministic helpers the live
conversation reads:

    _DECIDE_GRAPH   after an answer: is there something specific worth pressing
                    on, and if so what would a competent interviewer say?
                    budget -> substance -> assess (LLM) -> validate

    challenge_non_answer   the re-ask after a non-answer, worded by label
    is_semantic_repeat     the deterministic repeat detector the question
                           writer's loop refuses a repeat with
    conversation_state     the operator's view of where a conversation stands

THE BASE QUESTION IS WRITTEN ELSEWHERE. `services/ppi_interview.write_question`
writes each base question with its rubric in one call. The second graph that
lived here (the question DELIVERY graph, with its generate and reword modes
and its substance check) had no caller on the live path and was reached only
by the offline eval; it was DELETED on 2026-09-24 with its two prompts
(`tests/test_dead_interviewer_modes_removed.py`). `challenge_prompt` and
`interview_challenge.txt` are LIVE, through `challenge_non_answer`, and stay.

THE FOUR THINGS IT MUST NOT BREAK
---------------------------------
Pinned by `tests/test_conversation_flow.py`:

1. **Scoring and grouping.** A follow-up is answered under the SAME
   `question_key` as the question that produced it, so `answers_by_key` files it
   with that question's other answers. No new key is ever invented.
2. **Billing and completion.** `charge_completed` fires when
   `next_question_index >= len(prompts)`. Nothing here extends the prompt list
   or advances the index.
3. **Termination.** At most ONE follow-up per base question and a
   length-scaled budget per conversation, counted in a PERSISTED column.
4. **Every item is asked.** There is no early close (2026-09-24): a
   conversation ends when its written questions are exhausted, and
   `conversation_state` REPORTS coverage without deciding anything.

EVERY FAILURE PATH IS THE PRODUCT'S PREVIOUS BEHAVIOUR
------------------------------------------------------
`next_follow_up` returns None ("ask the next scripted question") on an outage,
a timeout, malformed JSON, a model echoing "null" or a response long enough to
be a speech. A candidate is mid-assessment on a live request, so a provider
problem costs the adaptivity and nothing else.

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
from dataclasses import dataclass
from typing import Any, Sequence

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.prompts import registry
from app.services import answer_quality, llm_router

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "COVERAGE_CONFLICTING",
    "COVERAGE_COVERED",
    "COVERAGE_PARTIAL",
    "COVERAGE_UNPROBED",
    "COVERAGE_WEAK",
    "MAX_FOLLOW_UPS",
    "MAX_FOLLOW_UPS_PER_QUESTION",
    "STOP_CONDITIONS",
    "STOP_EVERY_DIMENSION_COVERED",
    "STOP_EVIDENCE_SUFFICIENT",
    "STOP_FLOOR_REACHED",
    "STOP_NO_CONFLICT_OUTSTANDING",
    "STOP_NO_PROBE_OUTSTANDING",
    "STOP_PROMPTS_EXHAUSTED",
    "ConversationState",
    "DimensionEvidence",
    "challenge_non_answer",
    "challenge_prompt",
    "conversation_state",
    "follow_up_budget",
    "is_semantic_repeat",
    "next_follow_up",
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

#: Text in `app/prompts/interview_follow_up_decision.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_DECIDE_SYSTEM = registry.render("interview_follow_up_decision")

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


# ── Graph 1: should we press on that answer? ─────────────────────────────────


class _DecideState(TypedDict, total=False):
    session: Any
    question: str
    answer: str
    transcript: list[dict[str, Any]]
    follow_ups_used: int
    already_followed_up: bool
    budget: int
    #: Every interviewer line so far. A probe that re-asks something already
    #: asked is worse than no probe: it spends a scarce budget to demonstrate
    #: that nobody was listening.
    asked_before: list[str]
    #: How far up 38.3's specificity gradient this matrix item has already been
    #: probed. Zero means "not above the base question", which is the ordinary
    #: case and the default.
    specificity_reached: int
    #: What the department evidence graph says this item must establish, and
    #: which corroboration is out of reach. Absent when no Part VI department
    #: covers the role, in which case the probe is written from the answer
    #: alone, exactly as it was before.
    evidence_target: dict[str, Any] | None
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


def _probe_specificity(reached: int) -> dict[str, Any] | None:
    """The rung of 38.3's gradient a probe should aim at, as payload data.

    A FOLLOW-UP IS WHERE THE DISCRIMINATION HAPPENS, so the floor is the first
    DISCRIMINATING rung rather than the next one after whatever the base
    question used. 38.3 is explicit that a generative model produces plausible
    level 1 to 3 content effortlessly and generic level 4 to 5 content, and that
    real experience produces specific level 4 to 5 content. A probe that lands
    below that line spends the conversation's scarcest budget on a rung the
    base question already covered.

    Returns None rather than raising when the Runbook data is unreachable. This
    runs on a live turn with a candidate waiting, and the correct degradation is
    the probe the product asked yesterday, not a failed request.
    """
    from app.services.hiring import evidence_graph  # noqa: PLC0415

    try:
        discriminators = evidence_graph.discriminator_levels()
        floor = (min(discriminators) - 1) if discriminators else 0
        level = evidence_graph.next_specificity_level(max(int(reached), floor))
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.info(
            "interviewer.specificity_unavailable error=%s", type(exc).__name__
        )
        return None
    if level is None:
        return None
    # THE RUNG NUMBER IS NOT SENT. It is engineering metadata and the model has
    # no use for it, but it is a number in a payload one turn away from text a
    # candidate reads, and this product's oldest rule is that no number reaches
    # a client. The two sentences carry the whole rung.
    return {"question": level.question, "answerable_by": level.answerable_by}


async def _decide_assess(state: _DecideState) -> _DecideState:
    """The model call. Every exception becomes stop=True, never a raised error."""
    payload: dict[str, Any] = {
        "current_question": state.get("question"),
        "candidate_answer": state.get("answer"),
        "conversation_so_far": _recent(state.get("transcript")),
    }
    specificity = _probe_specificity(int(state.get("specificity_reached") or 0))
    if specificity is not None:
        payload["probe_at_specificity"] = specificity
    target = state.get("evidence_target")
    if target:
        payload["evidence_target"] = target
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
    # DETERMINISTIC repetition check, applied to the probe as well as to a base
    # question. A probe is where a repeat is most likely and most damaging: the
    # model has just been shown one answer and asked what to press on, and the
    # obvious thing to press on is often what the previous question already
    # covered. Dropping it costs nothing -- the conversation simply moves to the
    # next scripted question, which is this module's standard degradation.
    if is_semantic_repeat(text, state.get("asked_before")):
        logger.info("interviewer.follow_up_rejected reason=repeat")
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


# ── Repetition detection ─────────────────────────────────────────────────────
#
# DETERMINISTIC, AND IT HAS TO BE. Asking the same thing twice is the single
# most obvious tell that an interviewer is not listening, and it is most likely
# to happen exactly when the provider is degraded and the writer is falling back
# to stored text or to a thinner prompt. A model asked "is this a repeat?" is
# absent in precisely that moment, and its "no" would be indistinguishable from
# a real "no". Same reasoning as `answer_classification` settling gibberish
# without a model call.
#
# TWO SIGNALS, both cheap and both explainable:
#
#   * the SPECIFIC terms (`_tokens`): a rewording that keeps 'Kafka', 'p99' and
#     'CI/CD' and changes the verbs is the same question in new clothes;
#   * word SHINGLES: overlapping runs of content words, which catch a repeat
#     that carries no specific term at all ("tell me about a time you handled a
#     disagreement" asked twice with different filler).
#
# Neither alone is enough. Terms miss a purely behavioural question; shingles
# alone would fire on two different questions about the same narrow topic.

#: Length of a shingle, in content words. Three is the smallest run that carries
#: word order; at two, "handled the disagreement" and "the disagreement handled"
#: look alike, and any two questions in the same domain start to collide.
_SHINGLE_SIZE = 3

#: Below this many shingles a comparison is noise -- a six-word question has
#: four of them, and matching two of four is not evidence of anything.
#: Shingles a question must produce before the shingle comparison is trusted.
#:
#: Lowered from 4 on 2026-08-24. A short behavioural question reduces to three
#: or four content words, which yields ONE or TWO shingles, so the floor of four
#: meant the shingle path could never fire for exactly the questions most likely
#: to be reworded. With the openers now stripped, the identical-substance check
#: catches the common case; this floor governs the near-miss, and two shingles
#: of three consecutive content words each is already a strong signal.
_MIN_SHINGLES = 2

#: Share of shingles two questions must have in common. Set high on purpose: a
#: false positive here silently throws away a well-written adaptive question and
#: shows the candidate the stored one instead, which is the exact scripted feel
#: this module exists to remove.
REPEAT_SHINGLE_THRESHOLD = 0.5

#: Share of specific terms two questions must have in common. Unchanged from the
#: value this check has always used.
REPEAT_TERM_THRESHOLD = 0.8

#: Words that carry no topic. Stripped before shingling so that "could you tell
#: me about a time when you" does not make every behavioural question look like
#: every other one.
#:
#: THE OPENERS ARE THE POINT, and leaving one out is how a repeat slips through.
#: "Tell me about a disagreement with a peer" and "Describe a disagreement with
#: a peer" are the same question, and the second is exactly what a model returns
#: when told not to repeat itself. `tell` was stripped and `describe` was not,
#: so the two reduced to different content words and the identical-substance
#: check could not fire. Every verb a question can open with belongs here, or
#: the detector catches only the rewordings nobody would have written anyway.
_FILLER: frozenset[str] = frozenset(
    "a about an and any are as at be been but by can could describe did do does "
    "explain for from give had has have how i in into is it its me more of on "
    "one or our outline please recall share so tell than that the their them "
    "then there these they this to us walk was we were what when where which "
    "who why will with would you your".split()
)


def _content_words(text: str) -> list[str]:
    """Lower-cased words with the filler removed, in order.

    Order is kept because the shingles below depend on it; a bag of words would
    call a question and its inverse the same question.
    """
    kept: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9./+#_-]*", str(text or "").lower()):
        word = raw.strip(".,;:?!()-")
        if word and word not in _FILLER:
            kept.append(word)
    return kept


def _shingles(words: Sequence[str]) -> set[tuple[str, ...]]:
    if len(words) < _SHINGLE_SIZE:
        return set()
    return {
        tuple(words[index : index + _SHINGLE_SIZE])
        for index in range(len(words) - _SHINGLE_SIZE + 1)
    }


def _overlap(left: set[Any], right: set[Any]) -> float:
    """Jaccard. Symmetric, so a longer rewrite of a short question is not
    treated differently from a shorter rewrite of a long one."""
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def is_semantic_repeat(text: str, asked_before: Sequence[str] | None) -> bool:
    """Whether this question has, in substance, already been asked.

    Compared on substance rather than on the exact string, because a model told
    not to repeat itself will cheerfully reword the same question and return it.
    """
    words = _content_words(text)
    if not words:
        return False
    terms = _tokens(text)
    shingles = _shingles(words)

    for previous in asked_before or ():
        earlier_words = _content_words(previous)
        if not earlier_words:
            continue
        if words == earlier_words:
            # Identical once the filler is gone. No threshold can argue with
            # this one and none should have to.
            return True
        if terms:
            earlier_terms = _tokens(previous)
            if earlier_terms and _overlap(terms, earlier_terms) > REPEAT_TERM_THRESHOLD:
                return True
        earlier_shingles = _shingles(earlier_words)
        if (
            len(shingles) >= _MIN_SHINGLES
            and len(earlier_shingles) >= _MIN_SHINGLES
            and _overlap(shingles, earlier_shingles) >= REPEAT_SHINGLE_THRESHOLD
        ):
            return True
    return False


# Compiled once at import. Building a StateGraph per turn would add latency to a
# request a candidate is waiting on, for no behavioural difference.
_DECIDE_GRAPH = _build_decide_graph()


# ── Vaada's explicit conversation state ──────────────────────────────────────
#
# WHAT THIS REPLACED. The conversation's whole idea of itself was one tuple,
# `(covered, total)`, computed for the stopping rule and then thrown away. That
# is enough to answer "may I stop?" and nothing else: an item probed once and
# answered evasively, an item nobody has reached yet, and an item the evidence
# ledger already holds two contradicting readings of all looked identical, and
# all three looked identical to an item that was answered well.
#
# INTERNAL ONLY. Nothing here crosses a client boundary. `confidence` is a WORD
# and the counts below are engineering metadata in the same sense the ledger's
# `relevance` is: they order work and they explain a decision to an operator,
# and the standing no-numbers rule covers them exactly as it covers a score.
#
# THIS IS THE VAADA SIDE OF THE LOOP WITH MITI. `conflicting` and `weak` are not
# computed from the transcript -- they cannot be, because whether two readings
# disagree is a question about the evidence ledger, which is Miti's. The caller
# reads them from the ledger and passes them in, which keeps the direction of
# the dependency honest and keeps this function pure.

#: A dimension with at least one substantive answer and nothing outstanding.
COVERAGE_COVERED = "covered"
#: Answered substantively, but a re-ask on it was exhausted without the evidence
#: the question asked for. There IS something here; it is not yet enough.
COVERAGE_PARTIAL = "partially_covered"
#: Probed, and nothing usable came back. Distinct from `unprobed` for the same
#: reason the ledger separates `inferred_only` from `unsupported`: the
#: difference is real and it changes what to do next.
COVERAGE_WEAK = "weak_evidence"
#: The ledger holds live evidence on both sides. Never resolved here; which side
#: is right is a question for the recruiter (services/evidence/contradictions).
COVERAGE_CONFLICTING = "conflicting_evidence"
#: Nobody has asked yet.
COVERAGE_UNPROBED = "unprobed"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

#: Every written question has been asked. Since 2026-09-24 the floor IS the
#: whole plan (every item is asked), so this and `prompts_exhausted` hold
#: together.
STOP_FLOOR_REACHED = "floor_reached"
STOP_EVERY_DIMENSION_COVERED = "every_dimension_covered"
STOP_NO_PROBE_OUTSTANDING = "no_probe_outstanding"
#: A MATERIAL contradiction obliges `ask_follow_up` while a conversation is
#: still running (services/evidence/contradictions.actions_for). Stopping with
#: one outstanding throws away the only chance to ask.
STOP_NO_CONFLICT_OUTSTANDING = "no_conflict_outstanding"
STOP_PROMPTS_EXHAUSTED = "prompts_exhausted"
#: 6.7's sufficiency floor, as far as a CONVERSATION can satisfy it. Added
#: 2026-08-29 for spec-doc6 4.4: "Ends when Sutra's question-count range AND
#: evidence sufficiency are both satisfied."
STOP_EVIDENCE_SUFFICIENT = "evidence_sufficient"

#: The whole vocabulary, in the order `ConversationState.stop_conditions`
#: reports them. It exists because `assessment_conversations.end_reason` now
#: STORES one of these words, so the set has to be nameable in one place: the
#: column's CHECK constraint (migration 0116) enumerates exactly this tuple,
#: and `tests/test_vaada_end_reason.py` compares the two. Restating six string
#: literals in SQL and hoping they keep step with six in Python is the drift
#: `test_runbook_parity` exists to catch elsewhere.
#:
#: ONE member is written as an end reason since 2026-09-24:
#: `prompts_exhausted`, because every item is asked and the early close that
#: wrote `every_dimension_covered` is DELETED. That word stays in the tuple
#: (and in the CHECK) so a row written while the early close existed still
#: reads; the rest are conditions that HOLD at a stop without being the
#: reason for it.
#: The constraint states the rule that a reason is one of Vaada's named stop
#: conditions, so recording a different one later is a code change rather than
#: a production write that a forgotten migration refuses.
STOP_CONDITIONS: tuple[str, ...] = (
    STOP_FLOOR_REACHED,
    STOP_EVERY_DIMENSION_COVERED,
    STOP_NO_PROBE_OUTSTANDING,
    STOP_NO_CONFLICT_OUTSTANDING,
    STOP_EVIDENCE_SUFFICIENT,
    STOP_PROMPTS_EXHAUSTED,
)


@dataclass(frozen=True)
class DimensionEvidence:
    """What the conversation currently holds on one matrix item."""

    dimension: str
    answers: int = 0
    substantive: int = 0
    gaps: int = 0
    #: From the ledger, never from the transcript.
    conflicting: bool = False
    weak: bool = False
    #: Whether this matrix item is a Must-have. 6.7's sufficiency floor is
    #: stated over MUST-HAVE competencies, and nothing else: "All must-have
    #: competencies at >= 1 independent group beyond self-report". Defaults to
    #: False so a caller that has not marked any keeps the strict reading below
    #: rather than silently getting a weaker one.
    must_have: bool = False
    @property
    def state(self) -> str:
        """One word, resolved in a fixed precedence.

        Conflicting outranks everything, exactly as `ledger.support_state`
        checks contradiction FIRST: a dimension with strong evidence on both
        sides is the most interesting one in the conversation and the easiest to
        lose behind a rule that lets support outweigh contradiction.
        """
        if self.conflicting:
            return COVERAGE_CONFLICTING
        if self.answers <= 0:
            return COVERAGE_UNPROBED
        if self.substantive <= 0:
            return COVERAGE_WEAK
        if self.weak:
            return COVERAGE_WEAK
        if self.gaps > 0:
            return COVERAGE_PARTIAL
        return COVERAGE_COVERED


@dataclass(frozen=True)
class ConversationState:
    """Everything Vaada knows about where this conversation has got to."""

    dimensions: tuple[DimensionEvidence, ...] = ()
    asked: int = 0
    total_written: int = 0
    floor: int = 0
    probe_outstanding: bool = False
    def _named(self, state: str) -> tuple[str, ...]:
        return tuple(
            item.dimension for item in self.dimensions if item.state == state
        )

    @property
    def covered(self) -> tuple[str, ...]:
        return self._named(COVERAGE_COVERED)

    @property
    def partially_covered(self) -> tuple[str, ...]:
        return self._named(COVERAGE_PARTIAL)

    @property
    def unprobed(self) -> tuple[str, ...]:
        return self._named(COVERAGE_UNPROBED)

    @property
    def weak_evidence(self) -> tuple[str, ...]:
        return self._named(COVERAGE_WEAK)

    @property
    def conflicting_evidence(self) -> tuple[str, ...]:
        return self._named(COVERAGE_CONFLICTING)

    @property
    def remaining(self) -> tuple[str, ...]:
        """Every dimension that is not yet covered, in matrix order.

        Deliberately one list rather than four: what the conversation still owes
        is a single question, and a caller that had to union four tuples would
        eventually union three of them.
        """
        return tuple(
            item.dimension
            for item in self.dimensions
            if item.state != COVERAGE_COVERED
        )

    @property
    def confidence(self) -> str:
        """A WORD, and ARITHMETIC over the dimension states.

        Never a model's opinion of its own work, for the reason
        `verification.Verdict.confidence` gives: a self-assessed confidence is
        unfalsifiable and fails exactly when the provider is already failing.
        This one can be recomputed by hand from the counts.
        """
        if not self.dimensions:
            return CONFIDENCE_LOW
        if self.conflicting_evidence or self.weak_evidence:
            return CONFIDENCE_LOW
        if not self.remaining:
            return CONFIDENCE_HIGH
        if not self.unprobed:
            return CONFIDENCE_MEDIUM
        return (
            CONFIDENCE_MEDIUM
            if len(self.covered) * 2 >= len(self.dimensions)
            else CONFIDENCE_LOW
        )

    @property
    def critical(self) -> tuple[DimensionEvidence, ...]:
        """The dimensions 6.7's sufficiency floor is stated over.

        ONE RULE, NOT TWO PATHS. 6.7 states the floor over MUST-HAVE
        competencies. When the caller has marked which items those are, they are
        the critical set; when it has marked none, every dimension is critical.
        The second half is the strict direction and it is deliberate: "restrict
        more when unsure" applies exactly here, because the higher authority is
        silent about a matrix that does not say which of its items are
        essential, and the alternative reading -- no must-haves marked, so the
        floor is vacuously met -- would let an unwired caller declare every
        conversation sufficient and close it at the grade minimum.
        """
        marked = tuple(item for item in self.dimensions if item.must_have)
        return marked or self.dimensions

    @property
    def unevidenced(self) -> tuple[DimensionEvidence, ...]:
        """Critical dimensions the conversation has not established.

        A dimension is established when a SUBSTANTIVE answer stands under it
        with nothing outstanding, which is `COVERAGE_COVERED`. That maps onto
        6.7's Moderate row -- "all must-have competencies at >= 1 independent
        group beyond self-report" -- because the assessment IS an independence
        group beyond self-report: it is source 3 of 38.1's six, and the resume
        the candidate wrote is source 1. An evasive or empty answer is not
        evidence and does not establish anything, which is what stops a
        candidate shortening their own assessment by not answering.
        """
        return tuple(
            item for item in self.critical if item.state != COVERAGE_COVERED
        )

    @property
    def evidence_sufficient(self) -> bool:
        return bool(self.dimensions) and not self.unevidenced

    @property
    def stop_conditions(self) -> tuple[str, ...]:
        """Which stop conditions currently hold, in a stable order.

        REPORTING, NOT DECIDING. Nothing ends an assessment early: it ends when
        its written questions are exhausted (2026-09-24, "every item is
        asked"). A second decider here would be one more than anybody would
        remember to keep in step.
        """
        met: list[str] = []
        if self.asked >= self.floor:
            met.append(STOP_FLOOR_REACHED)
        if self.dimensions and not self.remaining:
            met.append(STOP_EVERY_DIMENSION_COVERED)
        if not self.probe_outstanding:
            met.append(STOP_NO_PROBE_OUTSTANDING)
        if not self.conflicting_evidence:
            met.append(STOP_NO_CONFLICT_OUTSTANDING)
        if self.evidence_sufficient:
            met.append(STOP_EVIDENCE_SUFFICIENT)
        if self.total_written and self.asked >= self.total_written:
            met.append(STOP_PROMPTS_EXHAUSTED)
        return tuple(met)

    def as_log(self) -> dict[str, Any]:
        """COUNTS and WORDS for one operator log line.

        No dimension names and no answer text. A competency name is a label and
        would be safe, but a log line carrying twenty of them is a log line
        nobody reads, and `interview_telemetry` already established that this
        channel is counts and keys only.
        """
        return {
            "covered": len(self.covered),
            "partially_covered": len(self.partially_covered),
            "unprobed": len(self.unprobed),
            "weak_evidence": len(self.weak_evidence),
            "conflicting_evidence": len(self.conflicting_evidence),
            "remaining": len(self.remaining),
            "asked": self.asked,
            "unevidenced": len(self.unevidenced),
            "confidence": self.confidence,
            "stop_conditions": list(self.stop_conditions),
        }


def conversation_state(
    *,
    dimensions: Sequence[DimensionEvidence],
    asked: int,
    total_written: int,
    floor: int,
    probe_outstanding: bool,
) -> ConversationState:
    """Assemble the state. Pure, deterministic, and calls no model.

    A model asked to summarise its own conversation would be absent during an
    outage and confidently wrong during a degraded one. REPORTING ONLY since
    2026-09-24: there is no early close, so nothing reads this to decide
    whether an assessment ends; it is the operator's log line.
    """
    return ConversationState(
        dimensions=tuple(dimensions),
        asked=int(asked),
        total_written=int(total_written),
        floor=int(floor),
        probe_outstanding=bool(probe_outstanding),
    )


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
    asked_before: Sequence[str] | None = None,
    specificity_reached: int = 0,
    evidence_target: dict[str, Any] | None = None,
) -> str | None:
    """One adaptive follow-up, or None to ask the next scripted question.

    `budget` is this conversation's length-scaled ceiling
    (`follow_up_budget(len(prompts))`); it defaults to MAX_FOLLOW_UPS so a
    caller that does not pass one keeps the previous behaviour rather than
    losing its ceiling.

    `asked_before` is every interviewer line so far, and it defaults to none for
    the same reason: an older caller keeps the previous behaviour instead of
    silently losing a guard it never knew about.

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
                "asked_before": list(asked_before or ()),
                "specificity_reached": int(specificity_reached or 0),
                "evidence_target": evidence_target,
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
