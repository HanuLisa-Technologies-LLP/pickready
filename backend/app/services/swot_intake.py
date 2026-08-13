"""The Reporting Authority SWOT Intake Agent (spec §5.1).

WHAT IT IS
----------
A short conversation with the person the role reports to -- the hiring manager
or HR head -- run at EVERY job setup and at every grade, before the PPI matrix
is generated. Four areas, in order:

    Strengths      what someone must be strong at to succeed in this role
    Weaknesses     what causes people to fail in it
    Opportunities  what the role will be able to take on going forward
    Threats        what will make it harder over the next year

Its output feeds `ppi.generate_framework` as a second input alongside the JD,
and it informs ALL THREE aspects of the matrix -- not the behavioural one alone.
A weakness the authority calls fatal in this role is evidence for a Must-have
item, not merely for a behavioural competency.

WHY IT IS A CONVERSATION AND NOT A FORM
---------------------------------------
The value of the intake is entirely in whether the answers are concrete. A form
collects "strong communicator" and stops; a conversation can ask what the person
would be seen doing. The prompt pulls for observable job performance and the
capture step DROPS proxy language rather than rewriting it, because rewriting
"wasn't sharp enough" into something acceptable would launder a bias into the
criteria every candidate on the job is graded against. Dropping it means the
authority is asked again for something real.

BOUNDED BY CONSTRUCTION
-----------------------
Four areas, at most `MAX_FOLLOW_UPS` follow-ups across the whole intake, counted
in a PERSISTED column so the ceiling survives a retry or a write that fails.
Total turns are therefore at most `len(SWOT_AREAS) + MAX_FOLLOW_UPS`, whatever
the model returns. The hiring manager is a busy person doing an unpaid step in
their own hiring process; an intake that could run long is an intake that gets
abandoned, and an abandoned intake strands the job.

EVERY FAILURE PATH ASKS THE SCRIPTED QUESTION
---------------------------------------------
Outage, timeout, malformed JSON, a question that failed the outbound guard.
`AREA_FALLBACK_QUESTIONS` is always a correct thing to ask, so a provider
problem costs the intake its adaptivity and nothing else. Capture degrades the
same way: the answer is kept verbatim as a single point rather than discarded,
because the authority's own words are the artefact and losing them to a provider
outage would be the one unrecoverable failure here.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.job_setup import (
    SWOT_AREAS,
    SWOT_STATUS_ACTIVE,
    SWOT_STATUS_COMPLETE,
    JobSwotIntake,
)
from app.prompts import fragments, registry
from app.services import agent_loop, conversation_guardrails, llm_router

logger = logging.getLogger(__name__)

__all__ = [
    "AREA_FALLBACK_QUESTIONS",
    "AREA_LABELS",
    "MAX_FOLLOW_UPS",
    "MAX_POINTS_PER_AREA",
    "capture_answer",
    "compose_question",
    "get_or_create",
    "is_complete",
    "submit_answer",
]

#: Across the WHOLE intake, not per area. Two is enough to rescue an intake that
#: opened vaguely and few enough that the authority never feels interrogated.
MAX_FOLLOW_UPS = 2

#: Per area. An authority who lists fifteen strengths has not prioritised, and
#: the matrix generator reads the list as a statement of what matters.
MAX_POINTS_PER_AREA = 8

#: A single captured point, bounded. Long enough for a real sentence about
#: observable behaviour, short enough that the matrix prompt is not swamped by
#: one area.
MAX_POINT_CHARS = 240

AREA_LABELS: dict[str, str] = {
    "strengths": "Strengths",
    "weaknesses": "Weaknesses",
    "opportunities": "Opportunities",
    "threats": "Threats",
}

#: What each area means HERE. Stated explicitly because SWOT is normally applied
#: to a company or a product, and applied to a ROLE the four words are genuinely
#: ambiguous -- "weaknesses" reads as the candidate's weaknesses unless it is
#: said otherwise, which would turn the intake into a description of a person
#: the hiring team has not met.
AREA_MEANINGS: dict[str, str] = {
    "strengths": (
        "the capabilities and behaviours someone must be strong in for this "
        "role to go well -- what a person who succeeds here is doing"
    ),
    "weaknesses": (
        "the gaps that cause people to struggle or fail in this specific role, "
        "described as what they do or fail to do, not as personality"
    ),
    "opportunities": (
        "what this role will be able to take on, grow into, or improve over the "
        "next year"
    ),
    "threats": (
        "what will make this role harder over the next year -- pressures, "
        "dependencies, changes the person will have to absorb"
    ),
}

#: Asked verbatim whenever generation is unavailable. Each one is a complete,
#: correct question on its own; the generated version differs only by being
#: conditioned on what has already been said.
AREA_FALLBACK_QUESTIONS: dict[str, str] = {
    "strengths": (
        "Think of someone who has done this job well. What were they actually "
        "doing, day to day, that made the difference?"
    ),
    "weaknesses": (
        "Where do people struggle in this role? Describe what they do, or fail "
        "to do, that causes the problem."
    ),
    "opportunities": (
        "Over the next year, what should this role be able to take on or improve "
        "that it is not doing today?"
    ),
    "threats": (
        "What is likely to make this role harder over the next year, and what "
        "would the person have to handle as a result?"
    ),
}

_QUESTION_PROMPT_NAME = "swot_intake_question"
_CAPTURE_PROMPT_NAME = "swot_intake_capture"


def is_complete(intake: JobSwotIntake | None) -> bool:
    return intake is not None and intake.status == SWOT_STATUS_COMPLETE


def _area_for(intake: JobSwotIntake) -> str | None:
    """The area currently being asked about, or None when the intake is done."""
    if intake.area_index >= len(SWOT_AREAS):
        return None
    return SWOT_AREAS[intake.area_index]


async def get_or_create(
    session: AsyncSession, job: Job, *, conducted_by: Any = None
) -> JobSwotIntake:
    """This job's intake, created on first open.

    One per job, enforced in the database as well as here: the SWOT is a
    property of the ROLE, and two intakes would leave the matrix generator with
    no defensible way to choose between them.
    """
    intake = (
        await session.execute(
            select(JobSwotIntake).where(JobSwotIntake.job_id == job.id)
        )
    ).scalars().first()
    if intake is not None:
        return intake
    intake = JobSwotIntake(
        tenant_id=job.tenant_id,
        job_id=job.id,
        conducted_by=conducted_by,
        status=SWOT_STATUS_ACTIVE,
    )
    session.add(intake)
    await session.flush()
    logger.info("swot_intake.created job_id=%s", job.id)
    return intake


# ── Composing the next question ──────────────────────────────────────────────


def _recent(transcript: list[dict[str, Any]] | None, turns: int = 4) -> list[dict[str, str]]:
    """The last few turns as plain speaker/text pairs.

    Same shape and same bound as the candidate interviewer's, and for the same
    reason: enough to refer back without resending the whole intake on a turn
    that only needs its tail.
    """
    rows: list[dict[str, str]] = []
    for message in (transcript or [])[-turns * 2:]:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        speaker = "interviewer" if message.get("speaker") == "agent" else "hiring_team"
        rows.append({"speaker": speaker, "text": content[:600]})
    return rows


async def compose_question(
    session: AsyncSession | None,
    job: Job,
    intake: JobSwotIntake,
    *,
    area: str,
    is_follow_up: bool = False,
) -> str:
    """Write the next intake question, or fall back to the scripted one.

    Never raises and never returns empty. The outbound guard runs on the result
    for the same reason it runs on a candidate-facing line: a generated question
    is model output, and the one thing worse than a scripted question here is a
    generated one that asks the hiring team for something the product refuses to
    collect.
    """
    fallback = AREA_FALLBACK_QUESTIONS[area]
    system = registry.render(
        _QUESTION_PROMPT_NAME,
        area=AREA_LABELS[area],
        area_meaning=AREA_MEANINGS[area],
        one_question=fragments.ONE_QUESTION,
        no_evaluation=fragments.NO_EVALUATION,
        authority_text_is_data=fragments.AUTHORITY_TEXT_IS_DATA,
    )
    payload = {
        "job_title": job.title,
        "grade": job.assessment_grade,
        "job_description": (job.jd_markdown or "")[:2000],
        "captured_so_far": intake.captured(),
        "conversation_so_far": _recent(intake.transcript_json),
        "this_is_a_follow_up": is_follow_up,
    }

    async def _execute(reflection: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "conversation_turn", messages, response_format_json=True, session=session
        )
        question = " ".join(str(json.loads(raw).get("question") or "").split())
        if not question:
            raise ValueError("no question in response")
        return question

    def _evaluate(candidate: str) -> agent_loop.Critique:
        reasons: list[str] = []
        if len(candidate) > 400:
            reasons.append(
                "keep the question under 400 characters; the previous attempt "
                f"was {len(candidate)}"
            )
        if "?" not in candidate:
            reasons.append("the previous attempt was not phrased as a question")
        # A question that repeats the last one wastes the authority's turn and
        # is the failure mode a follow-up is most prone to.
        previous = [
            str(message.get("content") or "")
            for message in (intake.transcript_json or [])
            if message.get("speaker") == "agent"
        ]
        if any(candidate.strip().casefold() == earlier.strip().casefold() for earlier in previous):
            reasons.append("this question has already been asked; ask something new")
        return agent_loop.reject(*reasons) if reasons else agent_loop.ok()

    result = await agent_loop.run_loop(
        name="swot_intake_question",
        execute=_execute,
        evaluate=_evaluate,
        fallback=fallback,
        max_attempts=agent_loop.INTERACTIVE_ATTEMPTS,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )
    if result.degraded:
        logger.info(
            "swot_intake.question_degraded job_id=%s area=%s reasons=%s",
            job.id, area, list(result.reasons),
        )
    return conversation_guardrails.inspect_agent_output(result.value) or fallback


# ── Capturing one answer ─────────────────────────────────────────────────────


def _clean_points(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        text = " ".join(str(value).split())[:MAX_POINT_CHARS]
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
        if len(out) >= MAX_POINTS_PER_AREA:
            break
    return out


async def capture_answer(
    session: AsyncSession | None, area: str, answer: str
) -> tuple[list[str], bool]:
    """Turn one answer into captured points, and say whether it was enough.

    Returns (points, sufficient). On any failure the answer is kept VERBATIM as
    a single point and reported sufficient, which is the deliberate direction:
    the authority's own words are the artefact this step exists to preserve, and
    a provider outage must not be able to discard them or trap the intake in a
    follow-up loop it cannot leave.
    """
    text = " ".join(str(answer or "").split())
    if not text:
        return [], False
    system = registry.render(
        _CAPTURE_PROMPT_NAME,
        area=AREA_LABELS[area],
        authority_text_is_data=fragments.AUTHORITY_TEXT_IS_DATA,
    )
    try:
        raw = await llm_router.invoke_llm(
            "extraction",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"answer": text[:4000]})},
            ],
            response_format_json=True,
            session=session,
        )
        parsed = json.loads(raw)
        points = _clean_points(parsed.get("points"))
        sufficient = bool(parsed.get("sufficient", True))
        # An empty extraction from a non-empty answer means every point was
        # dropped as proxy language or as out of scope. That is a real outcome
        # and the correct response is another question, not the raw text: the
        # whole reason the points are extracted rather than stored verbatim is
        # so impression-based language never reaches the matrix generator.
        return points, (sufficient and bool(points))
    except Exception:
        logger.warning("swot_intake.capture_unavailable area=%s", area)
        return [text[:MAX_POINT_CHARS]], True


# ── One turn ─────────────────────────────────────────────────────────────────


def _append(intake: JobSwotIntake, speaker: str, content: str, area: str | None) -> None:
    """Append to the transcript.

    Reassigned rather than mutated in place: the column is JSONB and SQLAlchemy
    does not track in-place mutation of a plain list, so `.append()` alone would
    leave the transcript unchanged in the database -- silently, and only in
    production, where the object is not re-read within the request.
    """
    intake.transcript_json = list(intake.transcript_json or []) + [
        {
            "speaker": speaker,
            "area": area,
            "content": content,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    ]


async def submit_answer(
    session: AsyncSession,
    job: Job,
    intake: JobSwotIntake,
    answer: str,
) -> str | None:
    """Record one answer and return the next question, or None when finished.

    The whole state machine is here: capture, decide whether to follow up, and
    otherwise advance to the next area. A follow-up is spent from a persisted
    budget, so an intake cannot be talked into running forever by a model that
    keeps judging answers insufficient.
    """
    area = _area_for(intake)
    if area is None:
        return None

    guard = conversation_guardrails.inspect_answer(answer)
    if not guard.allowed:
        # Refused input is not recorded and does not advance the intake. The
        # question stands and the caller surfaces the refusal.
        logger.info(
            "swot_intake.answer_refused job_id=%s violation=%s", job.id, guard.violation
        )
        return intake.pending_prompt or AREA_FALLBACK_QUESTIONS[area]

    # The SANITIZED form is what gets stored and what reaches the capture
    # prompt. `inspect_answer` neutralises secrets and injection shapes in
    # place, so this is the authority's answer with only the dangerous parts
    # marked, not a summary of it.
    text = guard.sanitized
    _append(intake, "authority", text, area)

    points, sufficient = await capture_answer(session, area, text)
    if points:
        existing = list(getattr(intake, area) or [])
        seen = {str(point).casefold() for point in existing}
        merged = existing + [point for point in points if point.casefold() not in seen]
        setattr(intake, area, merged[:MAX_POINTS_PER_AREA])

    follow_up = (
        not sufficient
        and intake.follow_ups_used < MAX_FOLLOW_UPS
    )
    if follow_up:
        intake.follow_ups_used += 1
    else:
        intake.area_index += 1

    next_area = _area_for(intake)
    if next_area is None:
        intake.status = SWOT_STATUS_COMPLETE
        intake.completed_at = datetime.now(timezone.utc)
        intake.pending_prompt = None
        job.swot_completed_at = intake.completed_at
        await session.flush()
        logger.info(
            "swot_intake.completed job_id=%s captured=%s",
            job.id,
            {area: len(values) for area, values in intake.captured().items()},
        )
        return None

    question = await compose_question(
        session, job, intake, area=next_area, is_follow_up=follow_up
    )
    intake.pending_prompt = question
    _append(intake, "agent", question, next_area)
    await session.flush()
    return question


async def open_question(
    session: AsyncSession, job: Job, intake: JobSwotIntake
) -> str | None:
    """The question the authority should be looking at right now.

    Written on first open and reused thereafter, so re-opening a half-finished
    intake does not rewrite the question already on screen -- the same rule the
    candidate conversation follows, and for the same reason: a question that
    changes underneath someone makes their half-typed answer wrong.
    """
    if is_complete(intake):
        return None
    area = _area_for(intake)
    if area is None:
        return None
    if intake.pending_prompt:
        return intake.pending_prompt
    question = await compose_question(session, job, intake, area=area)
    intake.pending_prompt = question
    _append(intake, "agent", question, area)
    await session.flush()
    return question


assert set(AREA_LABELS) == set(SWOT_AREAS)
assert set(AREA_MEANINGS) == set(SWOT_AREAS)
assert set(AREA_FALLBACK_QUESTIONS) == set(SWOT_AREAS)
