"""The adaptive half of the unified candidate conversation.

WHAT WAS THERE BEFORE
---------------------
`api/assessments.respond` was an index into a pre-generated list. It appended
the fixed prompt, stored the answer, incremented `next_question_index` and
returned the next prompt. No LLM call happened during the conversation at all,
so "the agent has no memory" was not a prompt-quality problem: there was no
agent in the loop to give memory to. A one-word answer to "describe a system you
designed" was followed by the next scripted question as though it had been a
full account.

WHAT THIS ADDS
--------------
After each answer, one decision: is there anything worth pressing on, and if so
what would a competent interviewer actually say next? The follow-up is written
against the transcript so far, so it can refer to what the candidate said
earlier and does not repeat ground already covered.

THE THREE THINGS IT MUST NOT BREAK
----------------------------------
1. **Scoring and question grouping.** `functional_assessment.answers_by_key`
   groups candidate answers by `question_key`. A follow-up is answered under the
   SAME key as the question that produced it, so its answer joins that group and
   every rubric, scorer and report row keeps working untouched. No new key is
   ever invented.
2. **Billing and completion.** `charge_completed` fires when
   `next_question_index >= len(prompts)`. A follow-up does not extend the prompt
   list and does not advance the index, so the customer is charged after exactly
   the same set of base questions as before.
3. **Termination.** An interview that can ask "one more thing" has to be
   provably finite. At most ONE follow-up per base question, and at most
   MAX_FOLLOW_UPS per conversation, counted in a persisted column. Total turns
   are bounded by len(prompts) + MAX_FOLLOW_UPS, whatever the model returns.

DEGRADES TO THE OLD BEHAVIOUR, DELIBERATELY
-------------------------------------------
Every failure path returns None, which means "ask the next scripted question".
The conversation is a live request with a candidate waiting, so an LLM outage,
a timeout, a malformed response or a refusal must cost the adaptivity and
nothing else. It must never cost the candidate their assessment. This is the
same reasoning as `_llm_score` returning None, with one important difference:
there, the fallback silently invented a grade. Here the fallback is simply the
product's previous behaviour, which is honest.

TEMPERATURE
-----------
Routed as `conversation_turn`, the only task in the product above 0.5. Phrasing
SHOULD vary between candidates -- at 0.0 the interviewer repeats itself
verbatim to everyone, which is the scripted feel this module exists to remove.
What is ASKED stays fixed by the framework; only the wording varies. Scoring
tasks remain deterministic (config/llm_providers.TASK_TEMPERATURE).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services import answer_quality, llm_router

logger = logging.getLogger(__name__)

__all__ = ["MAX_FOLLOW_UPS", "MAX_FOLLOW_UPS_PER_QUESTION", "next_follow_up"]

#: Ceiling per conversation. Five is roughly one probe per five base questions
#: on a non-managerial assessment (45 questions), which reads as an attentive
#: interviewer rather than an interrogation, and it bounds both the candidate's
#: time and the token spend per assessment.
MAX_FOLLOW_UPS = 5

#: One per base question. Two consecutive probes on the same point is where an
#: interview starts to feel like cross-examination, and it is also what would
#: let a single evasive candidate consume the whole conversation budget.
MAX_FOLLOW_UPS_PER_QUESTION = 1

#: How much transcript the model sees. Enough to refer back to earlier answers
#: without resending an entire 45-question interview on every turn, which would
#: blow the token ceiling on the later questions of a long assessment.
TRANSCRIPT_TURNS = 6

#: A follow-up longer than this is not a question, it is a speech.
MAX_FOLLOW_UP_CHARS = 320

_SYSTEM = (
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


def _recent(transcript: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """The last few turns, oldest first, as plain speaker/text pairs."""
    rows = []
    for message in (transcript or [])[-TRANSCRIPT_TURNS * 2 :]:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        speaker = "interviewer" if message.get("speaker") == "agent" else "candidate"
        rows.append({"speaker": speaker, "text": content[:600]})
    return rows


def _clean(raw: str | None) -> str | None:
    """Reject anything that is not a single usable question."""
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return None
    if len(text) > MAX_FOLLOW_UP_CHARS:
        # Truncating would produce a question with no question mark and,
        # potentially, half a sentence. Dropping it just moves the interview on.
        return None
    # A model that answers with its own instructions, or with an empty gesture,
    # must not be shown to a candidate as an interview question.
    if text.lower() in {"null", "none", "n/a", "no", "-"}:
        return None
    return text


async def next_follow_up(
    *,
    session: Any,
    question: str,
    answer: str,
    transcript: list[dict[str, Any]] | None,
    follow_ups_used: int,
    already_followed_up: bool,
) -> str | None:
    """One adaptive follow-up, or None to ask the next scripted question.

    Every branch that returns None is a decision to fall back to the product's
    previous behaviour, which is always a safe outcome for a live conversation.
    """
    # ── Budget, checked before anything can spend ────────────────────────────
    # First, and without touching the model, so an exhausted budget costs
    # nothing and the ceiling cannot be argued with by a provider response.
    if already_followed_up or follow_ups_used >= MAX_FOLLOW_UPS:
        return None

    # A non-answer is not worth a follow-up. It is already routed to the
    # unanswered scoring path by services/answer_quality, and probing keyboard
    # mash would spend budget that a real-but-thin answer later in the interview
    # has a much better claim on. It would also read as the interviewer failing
    # to notice.
    if not answer_quality.is_substantive(answer):
        return None

    payload = {
        "current_question": question,
        "candidate_answer": answer,
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
        return _clean(json.loads(raw).get("follow_up"))
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. A candidate is waiting on this request, and every
        # possible failure here -- provider outage, timeout, malformed JSON, a
        # response that is not JSON at all -- has the same correct answer: ask
        # the next scripted question. Logged at info because this is a degraded
        # path, not an error the operator must act on.
        logger.info(
            "interviewer.follow_up_unavailable error=%s", type(exc).__name__
        )
        return None
