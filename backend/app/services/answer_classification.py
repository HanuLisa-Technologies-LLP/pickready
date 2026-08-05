"""What KIND of non-answer is this? The layer above `answer_quality`.

THE DEFECT THIS FIXES, OBSERVED LIVE 2026-08-05
-----------------------------------------------
A candidate typed `fsjdemd`, then `xdshfjg,uyytrs`, then `dwrhejyrkhfbgertyfg`,
then `cvdgrertykfmhgnfrshfmgc`. The agent asked the next scripted question every
time and reached "Question 8 of 45" without ever remarking that nothing had been
answered.

`answer_quality.is_substantive` already catches that shape of input -- keyboard
mash, a single token, an empty gesture. What nothing in the product caught is the
answer that is well-formed English prose and still does not answer the question
that was asked: a candidate who describes their team's process when asked what
THEY decided, or who talks around a specific tool for a paragraph without ever
saying whether they have used it. Those read as answers to every check the
product had, because structurally they are.

WHY THIS IS A SEPARATE MODULE AND NOT MORE HEURISTICS
-----------------------------------------------------
Relevance is not a property of the text on its own. The identical paragraph is a
perfect answer to one question and off topic against another, so no in-process
function over the answer alone can decide it -- the question has to be in scope,
which means a model. `answer_quality` deliberately stays model-free (see its
docstring: the bug it guards is TRIGGERED by the model being down), so it cannot
absorb this, and this module must not duplicate it. It calls it.

THE ORDER IS LOAD-BEARING
-------------------------
The deterministic pass runs FIRST and, when it fires, returns without touching
the network. Empty and gibberish are exactly the cases whose existing guard must
keep working during an outage, and they are also the cases where a model call
would be spend for a verdict already known with certainty.

DEGRADATION IS TOWARDS "substantive", ALWAYS
--------------------------------------------
On ANY failure of the model half -- outage, timeout, malformed JSON, a label the
model invented -- this returns label="substantive", confidence="low",
needs_rechallenge=False, scorable=True.

The asymmetry is the whole design. A candidate is mid-assessment on a live
request. A false "evasive" or "off_topic" makes the interviewer push back on a
genuine answer and reads to that candidate as an agent that cannot understand
them, on the strength of a provider hiccup. Falling back to "substantive" costs
the re-challenge and nothing else: the answer still goes to the rubric, which
grades it on its merits and grades it low where it deserves that. This mirrors
`services/interviewer`, where every failure path is the product's previous
behaviour, and is the opposite of `_llm_score`'s old fallback, which invented a
grade.

A NEGATIVE ANSWER IS A REAL ANSWER
----------------------------------
"I have not used Kafka" answers the question asked. It is substantive, it is
neither evasive nor off topic, and the rubric scores it low on its merits. This
is a standing rule in CLAUDE.md and it is the most costly false positive
available here, so it is stated in the prompt AND pinned in the tests.

`confidence` IS A WORD
----------------------
High, medium, low. A float would be a number about a candidate, and no number
reaches a client. It is also honest about what this is: the model's own hedge,
not a calibrated probability.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services import answer_quality, llm_router

logger = logging.getLogger(__name__)

__all__ = ["LABELS", "Classification", "classify"]

#: Ordered worst-to-best is NOT implied; these are kinds, not grades. An
#: off-topic answer is not "better" than gibberish, it is a different thing that
#: calls for a different response from the interviewer.
LABELS: tuple[str, ...] = (
    "substantive",
    "empty",
    "gibberish",
    "off_topic",
    "evasive",
)

#: The two the model is allowed to add on top of "substantive". Kept separate
#: from LABELS because a model returning "empty" or "gibberish" would be
#: contradicting the deterministic pass that already cleared this text, and the
#: deterministic pass is the one that gets to be right about those.
_MODEL_LABELS: frozenset[str] = frozenset({"substantive", "off_topic", "evasive"})

_CONFIDENCE_WORDS: frozenset[str] = frozenset({"high", "medium", "low"})

#: How much prior conversation the classifier sees. An answer can only be judged
#: off topic against what was actually asked, and a candidate legitimately says
#: "as I mentioned above" -- without the preceding turns that reads as a dodge.
#: Bounded so a late question in a 45-question interview does not resend the
#: whole transcript on a request the candidate is waiting on.
TRANSCRIPT_TURNS = 6

#: Long enough for a full answer, short enough that a pasted essay cannot blow
#: the prompt budget. The classifier judges relevance, which is decided in the
#: opening sentences far more often than at the end.
MAX_ANSWER_CHARS = 4000

_SYSTEM = (
    "You are auditing one turn of a job interview. The candidate has replied to "
    "a question. Decide whether the reply actually ANSWERS the question that "
    "was asked.\n"
    "\n"
    "Choose exactly one label:\n"
    "- substantive: the reply engages with the question that was asked. It may "
    "be short, weak, unimpressive, or wrong. That is not your concern; a human "
    "will grade its quality separately.\n"
    "- off_topic: coherent prose that answers a DIFFERENT question than the one "
    "asked.\n"
    "- evasive: talks around the question, stays entirely in generalities, "
    "dodges the specific thing asked, or answers a softer version of the "
    "question.\n"
    "\n"
    "RULES THAT OVERRIDE EVERYTHING ABOVE:\n"
    "- A NEGATIVE ANSWER IS A REAL ANSWER. 'I have not used Kafka', 'I have "
    "never led a team', 'I do not know' are all substantive. They answer the "
    "question directly and honestly. NEVER label them evasive or off_topic.\n"
    "- Brevity is not evasion. A complete short answer is a complete answer.\n"
    "- Poor grammar, a second language, or clumsy phrasing is not evasion.\n"
    "- Admitting limited experience and then describing something adjacent is "
    "substantive, not off_topic: the candidate answered and then gave you the "
    "nearest evidence they have.\n"
    "- When you are genuinely unsure, choose substantive. Wrongly accusing a "
    "real answer of being a dodge is the worst outcome available to you.\n"
    "\n"
    "confidence is one of the WORDS high, medium, low. Never a number.\n"
    "reason is one short internal sentence for an engineer's log. It is never "
    "shown to the candidate, so write what you observed, not a message to "
    "them.\n"
    "\n"
    'Return JSON: {"label": <string>, "confidence": <string>, '
    '"reason": <string>}.'
)


@dataclass(frozen=True)
class Classification:
    """One verdict about one answer.

    `reason` names what fired -- a heuristic on the deterministic path, the
    model's own sentence on the other. It is internal only: it is written for a
    log line, and showing a candidate "answers a softer version of the question"
    would be the product accusing them in words no human reviewed.
    """

    label: str
    confidence: str
    reason: str
    needs_rechallenge: bool
    scorable: bool


def _degraded(reason: str) -> Classification:
    """The one verdict every failure resolves to.

    Factored out so the several `except` paths cannot drift apart from one
    another: they are all the same decision, and only `reason` distinguishes
    them in the log.

    scorable=True because the text IS real text and the rubric can grade it.
    needs_rechallenge=False because pushing back on a possibly-genuine answer,
    on the strength of a failed provider call, is the harm this exists to avoid.
    """
    return Classification(
        label="substantive",
        confidence="low",
        reason=reason,
        needs_rechallenge=False,
        scorable=True,
    )


def _deterministic(answer: str | None) -> Classification | None:
    """The model-free half. Returns None when there is real text to judge.

    `answer_quality.assess` owns these verdicts and is not reimplemented here;
    its reason codes are only MAPPED onto this module's label vocabulary.
    Punctuation-only input ("...", "???") maps to "empty" rather than
    "gibberish": nothing was typed that was meant as language, which is the
    empty gesture, not a keyboard mash.
    """
    verdict = answer_quality.assess(answer)
    if verdict.substantive:
        return None
    label = "empty" if verdict.reason in {"empty", "no_tokens"} else "gibberish"
    return Classification(
        label=label,
        confidence="high",
        reason=verdict.reason,
        needs_rechallenge=True,
        # Routes to the product's existing UNANSWERED_SCORE path, which grades
        # Not Matching and says plainly that no usable evidence was produced.
        scorable=False,
    )


def _recent(transcript: list[dict] | None) -> list[dict[str, str]]:
    """The last few turns, oldest first, as plain speaker/text pairs.

    Same shape and same source as `services/interviewer._recent`: the caller
    reads `assessment_messages`, because each turn is one stateless HTTP request
    and nothing holds the conversation between them.
    """
    rows: list[dict[str, str]] = []
    for message in (transcript or [])[-TRANSCRIPT_TURNS * 2 :]:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        speaker = "interviewer" if message.get("speaker") == "agent" else "candidate"
        rows.append({"speaker": speaker, "text": content[:600]})
    return rows


def _interpret(raw: str | None) -> Classification:
    """Turn the model's JSON into a Classification, or degrade.

    Every rejection here is a degradation rather than a raise, for the reason in
    the module docstring: a malformed response is a provider problem and a
    candidate must not pay for it with a false accusation.
    """
    try:
        payload = json.loads(raw or "")
        label = str(payload.get("label") or "").strip().lower()
        confidence = str(payload.get("confidence") or "").strip().lower()
        reason = " ".join(str(payload.get("reason") or "").split())[:300]
    except Exception:  # noqa: BLE001
        return _degraded("malformed_json")

    if label not in _MODEL_LABELS:
        # An invented label ("uncertain", "partial", "3") is not a verdict this
        # product knows how to act on, and guessing which one it meant would be
        # inventing a judgement about a candidate.
        logger.info("answer_classification.unknown_label label=%r", label[:40])
        return _degraded("unknown_label")

    if confidence not in _CONFIDENCE_WORDS:
        # A model that returned 0.82 here has still usefully told us the label.
        # Downgrading the hedge is honest and costs nothing; discarding the whole
        # verdict over the hedge would not be.
        confidence = "low"

    return Classification(
        label=label,
        confidence=confidence,
        reason=reason or label,
        # off_topic and evasive are real text and stay scorable: the rubric
        # grades them on their merits and grades them low where deserved.
        # Marking them unscorable would reach a low grade by a dishonest route
        # and would discard a candidate's actual words.
        needs_rechallenge=label != "substantive",
        scorable=True,
    )


async def classify(
    *,
    session: Any,
    question: str,
    answer: str,
    transcript: list[dict] | None,
) -> Classification:
    """Is this reply an answer to THAT question, and if not, what kind of not?

    Deterministic first and model second, never the other way round: empty and
    gibberish are decided in-process so they keep being decided when the
    provider is down, which is precisely when they matter most.
    """
    deterministic = _deterministic(answer)
    if deterministic is not None:
        return deterministic

    payload = {
        "question_asked": question,
        "candidate_reply": (answer or "")[:MAX_ANSWER_CHARS],
        "conversation_so_far": _recent(transcript),
    }
    try:
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format_json=True,
            session=session,
        )
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad, and logged at info rather than error: an outage, a
        # timeout and a non-JSON body all have the same correct handling, and
        # none of them is something an operator must act on mid-assessment.
        logger.info(
            "answer_classification.unavailable error=%s", type(exc).__name__
        )
        return _degraded("llm_unavailable")

    return _interpret(raw)
