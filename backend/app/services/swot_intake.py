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

Its output is Bodha's `swot_evidence` artifact, which is the Layer 3 input to
Sutra's seven-stage transformation (`hiring/scorecard.compile_matrix`). It
informs ALL THREE aspects of the matrix, not the behavioural one alone: Runbook
§18.1 makes the weaknesses quadrant "the gap competencies, the highest-weighted
items on the scorecard", which is this product's Must-have.

The four quadrants are only the first four blocks of §18.2's session, and the
rest of it now runs too: §18.3's seven high-value probes, §18.2's force-ranking
and disqualifier confirmation, §18.5's best-performer test, and §18.4's
situation classification read back for explicit confirmation. §18.5's six
rejection rules are the SINGLE exit -- an intake that trips one is handed back
to the hiring manager and the session does not close.

WHY IT IS A CONVERSATION AND NOT A FORM
---------------------------------------
The value of the intake is entirely in whether the answers are concrete. A form
collects "strong communicator" and stops; a conversation can ask what the person
would be seen doing. The prompt pulls for observable job performance and the
capture step DROPS proxy language rather than rewriting it, because rewriting
"wasn't sharp enough" into something acceptable would launder a bias into the
criteria every candidate on the job is graded against. Dropping it means the
authority is asked again for something real.

BOUNDED BY CONSTRUCTION, EXCEPT WHERE §18.5 SAYS OTHERWISE
----------------------------------------------------------
Every instrument is asked at most once, recorded in a PERSISTED column
(`probes_asked`) so the ceiling survives a retry or a write that fails, and
`MAX_FOLLOW_UPS` bounds the adaptive follow-ups across the whole session. The
generated part of the session is therefore bounded: four quadrant questions, six
§18.3 probes inside them, the force-ranking probe, the disqualifier
confirmation, the best-performer test, the classification read-back and at most
one re-ask of it. §18.2 budgets sixty to ninety minutes for exactly this.

REWORK IS DELIBERATELY UNBOUNDED. §18.5 says an intake "is rejected back to the
hiring manager" if any of six things is true, and a rework counter that
eventually gave up would mean the rule holds until somebody is persistent, which
is the same as not holding at all. A session with an outstanding refusal stays
open, carrying the sentence that says what is wanted.

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

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Sequence

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
from app.services.hiring import pipeline_halt, situations, swot_quality

logger = logging.getLogger(__name__)

__all__ = [
    "AREA_FALLBACK_QUESTIONS",
    "AREA_LABELS",
    "BEST_PERFORMER_QUESTION",
    "DISQUALIFIER_QUESTION",
    "DISQUALIFIER_INSTRUMENT",
    "MAX_FOLLOW_UPS",
    "MAX_POINTS_PER_AREA",
    "PHASES",
    "PHASE_AREAS",
    "PHASE_BEST_PERFORMER",
    "PHASE_COMPLETE",
    "PHASE_FORCE_RANKING",
    "PHASE_REWORK",
    "PHASE_SITUATION",
    "SWOT_ARTIFACT_VERSION",
    "capture_answer",
    "compose_question",
    "context_covered",
    "current_area",
    "get_or_create",
    "is_complete",
    "jd_version",
    "load",
    "publish_swot_evidence",
    "published_evidence",
    "quality_review",
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


# ── §18.2's session protocol, as phases ──────────────────────────────────────
#
# Runbook §18.2 lays the session out as a timeline with six blocks, and the last
# one is not one of the four quadrants:
#
#     0-10   Context
#     10-25  Strengths
#     25-45  Weaknesses          the core of the session
#     45-60  Opportunities
#     60-75  Threats
#     75-90  Force-ranking and disqualifier confirmation
#
# The implementation before this phase stopped at the four quadrants. Everything
# §18.2 puts in the last block, everything §18.3 asks, §18.5's best-performer
# test and §18.4's classification were all absent, so the session collected four
# lists and closed -- which is precisely the "SWOT that stays a form" §18's title
# warns against.
#
# The phases below are that timeline. `PHASE_REWORK` is not a block of §18.2: it
# is §18.5's own consequence, the intake being handed back, and it has its own
# phase because a session in rework is not a session in progress and a screen
# that showed them the same would let a rejected intake look finished.

PHASE_AREAS = "areas"
PHASE_FORCE_RANKING = "force_ranking"
PHASE_BEST_PERFORMER = "best_performer"
PHASE_SITUATION = "situation"
PHASE_REWORK = "rework"
PHASE_COMPLETE = "complete"

PHASES: tuple[str, ...] = (
    PHASE_AREAS,
    PHASE_FORCE_RANKING,
    PHASE_BEST_PERFORMER,
    PHASE_SITUATION,
    PHASE_REWORK,
    PHASE_COMPLETE,
)

#: The §18.2 block a phase belongs to, for the progress indicator. Named rather
#: than derived from the index so a phase inserted later cannot silently shift
#: everything after it.
PHASE_LABELS: dict[str, str] = {
    PHASE_AREAS: "The four quadrants",
    PHASE_FORCE_RANKING: "Force-ranking and disqualifiers",
    PHASE_BEST_PERFORMER: "The best-performer test",
    PHASE_SITUATION: "Confirming the hiring situation",
    PHASE_REWORK: "One thing to revisit",
    PHASE_COMPLETE: "Finished",
}

#: §18.5's sixth trigger, asked rather than inferred. The Runbook calls it "a
#: devastating and highly effective test -- run it", and there is no way to
#: compute it: the platform does not have the hiring manager's current team, and
#: a model asked to guess would be inventing a person to fail a test.
BEST_PERFORMER_QUESTION = (
    "One last check, and it is the most useful question in this session. "
    "Think of the strongest person you have doing work like this today. If "
    "they applied for this role as a stranger, and were held to everything you "
    "have just described, would they get through? Yes or no is enough, and no "
    "is a perfectly normal answer."
)

#: §18.2's 75-90 block, second half: "disqualifier confirmation".
DISQUALIFIER_QUESTION = (
    "Is there anything that would rule someone out completely, whatever else "
    "they brought? A licence, a certification, a legal requirement to work "
    "here. If there is nothing, say so and we are done."
)

#: Recorded in `probes_asked` alongside the §18.3 probe keys, so "which
#: instruments has this manager already been put through" is one list rather
#: than a list plus two booleans somewhere else.
DISQUALIFIER_INSTRUMENT = "disqualifier_confirmation"

#: The trade-off probe is §18.3's fifth and §18.2 puts the force-ranking it
#: performs in the 75-90 block, after every quadrant is in. Asking it during the
#: Strengths block, which is where `swot_quality` files it by area, would force-
#: rank two competencies against a session that had not yet heard the weaknesses
#: -- and the weaknesses are where the highest-weighted items come from.
_TRADE_OFF = "trade_off"

#: Said when the manager's answer to the situation read-back is neither a
#: confirmation nor a recognisable alternative. Asked once; a second unusable
#: answer leaves the classification unset, and `swot_quality.review` then
#: refuses the intake under `situation_undeterminable` rather than guessing.
_SITUATION_REASK = "situation_reask"


async def load(session: AsyncSession, job_id: Any) -> JobSwotIntake | None:
    """This job's intake row, or None. The one reader of the table.

    Moved here from `ppi.load_swot` when Sutra stopped owning it. The SWOT is
    Bodha's artefact, and a second module that could read the row directly is a
    second module that could start reading it INSTEAD of the artifact, which is
    the boundary spec-doc6 §5 draws ("artifacts, never transcripts").
    """
    return (
        await session.execute(
            select(JobSwotIntake).where(JobSwotIntake.job_id == job_id)
        )
    ).scalars().first()


def _area_for(intake: JobSwotIntake) -> str | None:
    """The quadrant currently being asked about, or None outside that phase."""
    if intake.phase != PHASE_AREAS:
        return None
    if intake.area_index >= len(SWOT_AREAS):
        return None
    return SWOT_AREAS[intake.area_index]


def current_area(intake: JobSwotIntake) -> str | None:
    """Public spelling of `_area_for`, for the API projection."""
    return _area_for(intake)


def _asked(intake: JobSwotIntake) -> set[str]:
    return {str(key) for key in (intake.probes_asked or [])}


def _mark_asked(intake: JobSwotIntake, key: str) -> None:
    """Reassign rather than append: the column is JSONB and SQLAlchemy does not
    track in-place mutation of a plain list, so `.append()` alone would leave it
    unchanged in the database -- silently, and only in production."""
    intake.probes_asked = list(intake.probes_asked or []) + [key]


def _quality(intake: JobSwotIntake) -> dict[str, Any]:
    return dict(intake.quality_json or {})


def _set_quality(intake: JobSwotIntake, value: dict[str, Any]) -> None:
    intake.quality_json = value


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


def _merge_points(intake: JobSwotIntake, area: str, points: Sequence[str]) -> None:
    existing = list(getattr(intake, area) or [])
    seen = {str(point).casefold() for point in existing}
    merged = existing + [
        point for point in points if str(point).casefold() not in seen
    ]
    setattr(intake, area, merged[:MAX_POINTS_PER_AREA])


def _proposed_categories(intake: JobSwotIntake) -> list[str]:
    """One category per captured point, per §18.1's quadrant mapping.

    This is what §18.5's everything-is-must-have rule counts. It is derived
    rather than asked for, because the hiring manager never marks an item
    "must-have" in this product: the quadrant they put it in decides, and a rule
    that counted a field nobody fills in would never fire.
    """
    captured = intake.captured()
    return [
        swot_quality.QUADRANT_CATEGORY[area]
        for area in SWOT_AREAS
        for _point in captured.get(area) or []
    ]


def quality_review(intake: JobSwotIntake) -> swot_quality.QualityReport:
    """§18.5's six rules, run against this intake's current state.

    Called at the end of the session and again after every rework turn. It
    reads only persisted state, so the verdict a screen shows and the verdict
    that decides whether the session may close are the same verdict.
    """
    quality = _quality(intake)
    return swot_quality.review(
        intake.captured(),
        categories=_proposed_categories(intake),
        disqualifiers=[str(entry) for entry in (quality.get("disqualifiers") or [])],
        situation_key=intake.situation_key,
        best_performer_excluded=intake.best_performer_excluded,
    )


def _competency_pair(job: Job, intake: JobSwotIntake) -> tuple[str, str] | None:
    """Two named competencies to force-rank against each other, or None.

    §18.3's trade-off probe is "If you could only have deep X or deep Y, which?"
    and `trade_off_question` refuses a blank side, because a probe with one side
    missing is a leading question rather than a force-ranking. The two sides are
    resolved through the SAME `match_competency` call Sutra's stage 1 makes, so
    the manager is asked to choose between two things that will actually appear
    on their scorecard rather than between two sentences they happened to say.
    """
    from app.services.hiring.department_models import (  # noqa: PLC0415
        department_for,
        match_competency,
    )

    model = department_for(job.department, job.title)
    captured = intake.captured()
    named: list[str] = []
    for area in ("weaknesses", "strengths"):
        for point in captured.get(area) or []:
            anchor = match_competency(str(point), model, job.assessment_grade)
            label = anchor.name if anchor else str(point).split(",")[0].strip()[:60]
            if label and label.casefold() not in {n.casefold() for n in named}:
                named.append(label)
            if len(named) >= 2:
                return named[0], named[1]
    return None


def _situation_prompt(intake: JobSwotIntake) -> str:
    """§18.4's read-back, or the open question when nothing points anywhere.

    "The recruiter states the classification back to the hiring manager for
    explicit confirmation before the session closes." A proposal is never
    written to `situation_key`: only a confirmation is, because misclassifying
    the situation is what §18.4 calls the most expensive error available at
    intake and a proposal stored in the confirmation's column is a proposal that
    will eventually be read as one.
    """
    signals = situations.classify_signals(
        [str(point) for points in intake.captured().values() for point in points or []]
    )
    if signals:
        key, _hits, matched = signals[0]
        return situations.confirmation_prompt(key, evidence=matched)
    return (
        "Before we finish, I need to know the shape of this hire, because it "
        "changes how the whole assessment is weighted. Which of these is it: "
        + ", ".join(
            situations.SITUATIONS[key].label for key in situations.SITUATION_TYPES
        )
        + "?"
    )


def _read_situation(intake: JobSwotIntake, answer: str) -> str | None:
    """The situation the manager just confirmed or named, or None.

    Two ways to say yes and one way to say something else. An answer that
    NAMES an alternative is taken as naming it, even when it also begins with
    "no", because "no, it is closer to a turnaround" is the answer the read-back
    is designed to elicit and reading only its first word would throw away the
    correction it exists to collect.
    """
    text = " ".join(str(answer or "").split()).lower()
    if not text:
        return None
    for key, situation in situations.SITUATIONS.items():
        label = situation.label.lower()
        if label in text or key.replace("_", " ") in text or key in text:
            return key
    affirmative = (
        "yes", "correct", "that is right", "that's right", "right", "confirmed",
        "agreed", "exactly", "spot on",
    )
    if any(word in text for word in affirmative):
        signals = situations.classify_signals(
            [
                str(point)
                for points in intake.captured().values()
                for point in points or []
            ]
        )
        return signals[0][0] if signals else None
    return None


async def submit_answer(
    session: AsyncSession,
    job: Job,
    intake: JobSwotIntake,
    answer: str,
) -> str | None:
    """Record one answer and return the next question, or None when finished.

    THE WHOLE OF §18.2's TIMELINE IS HERE, phase by phase. Each phase either
    asks its next question or hands over to the next phase, and only
    `_close_or_rework` can end the session -- so §18.5's rejection rules are the
    single exit, rather than a check somebody remembers to call.
    """
    await pipeline_halt.enforce(
        pipeline_halt.STAGE_BODHA_SWOT,
        tenant_id=job.tenant_id,
        actor_user_id=intake.conducted_by,
        job_id=job.id,
        correlation_id=intake.correlation_id or job.correlation_id,
        agent="bodha",
    )
    guard = conversation_guardrails.inspect_answer(answer)
    if not guard.allowed:
        # Refused input is not recorded and does not advance the intake. The
        # question stands and the caller surfaces the refusal.
        logger.info(
            "swot_intake.answer_refused job_id=%s violation=%s", job.id, guard.violation
        )
        return intake.pending_prompt

    # The SANITIZED form is what gets stored and what reaches the capture
    # prompt. `inspect_answer` neutralises secrets and injection shapes in
    # place, so this is the authority's answer with only the dangerous parts
    # marked, not a summary of it.
    text = guard.sanitized

    if intake.phase == PHASE_AREAS:
        return await _answer_area(session, job, intake, text)
    if intake.phase == PHASE_FORCE_RANKING:
        return await _answer_force_ranking(session, job, intake, text)
    if intake.phase == PHASE_BEST_PERFORMER:
        return await _answer_best_performer(session, job, intake, text)
    if intake.phase == PHASE_SITUATION:
        return await _answer_situation(session, job, intake, text)
    if intake.phase == PHASE_REWORK:
        return await _answer_rework(session, job, intake, text)
    return None


async def _answer_area(
    session: AsyncSession, job: Job, intake: JobSwotIntake, text: str
) -> str | None:
    """One quadrant turn: capture, then follow up, probe, or move on.

    The ORDER is §18.2's and §18.3's: the quadrant's own question, then a
    follow-up if the answer was not concrete enough, then that quadrant's
    high-value probes in the Runbook's printed order. The probes build on each
    other, so a session that asked "what would make this harder" before "who is
    this replacing" would get a worse answer to both.
    """
    area = _area_for(intake)
    if area is None:  # pragma: no cover - the phase guarantees an area
        return None
    _append(intake, "authority", text, area)
    points, sufficient = await capture_answer(session, area, text)
    _merge_points(intake, area, points)

    if not sufficient and intake.follow_ups_used < MAX_FOLLOW_UPS:
        intake.follow_ups_used += 1
        return await _ask(
            session,
            job,
            intake,
            await compose_question(
                session, job, intake, area=area, is_follow_up=True
            ),
            area,
        )

    # §18.3's probes for this quadrant, one per turn, in the Runbook's order.
    # The trade-off probe is deliberately withheld: §18.2 performs the
    # force-ranking in the 75-90 block, after every quadrant is in.
    probe = swot_quality.probe_for(area, asked=_asked(intake) | {_TRADE_OFF})
    if probe is not None:
        _mark_asked(intake, probe.key)
        return await _ask(session, job, intake, probe.question, area)

    intake.area_index += 1
    if _area_for(intake) is not None:
        next_area = SWOT_AREAS[intake.area_index]
        return await _ask(
            session,
            job,
            intake,
            await compose_question(session, job, intake, area=next_area),
            next_area,
        )
    return await _enter_force_ranking(session, job, intake)


async def _enter_force_ranking(
    session: AsyncSession, job: Job, intake: JobSwotIntake
) -> str | None:
    """§18.2's 75-90 block: force-ranking, then disqualifier confirmation."""
    intake.phase = PHASE_FORCE_RANKING
    asked = _asked(intake)
    if _TRADE_OFF not in asked:
        pair = _competency_pair(job, intake)
        if pair is not None:
            _mark_asked(intake, _TRADE_OFF)
            return await _ask(
                session, job, intake, swot_quality.trade_off_question(*pair), "strengths"
            )
        # NOT skipped silently. The probe needs two named competencies to
        # compare and this session produced fewer, so it is recorded as an
        # instrument that could not be run rather than one that passed.
        _mark_asked(intake, _TRADE_OFF)
        quality = _quality(intake)
        quality["instruments_not_run"] = sorted(
            set(quality.get("instruments_not_run") or []) | {_TRADE_OFF}
        )
        _set_quality(intake, quality)
    if DISQUALIFIER_INSTRUMENT not in _asked(intake):
        _mark_asked(intake, DISQUALIFIER_INSTRUMENT)
        return await _ask(session, job, intake, DISQUALIFIER_QUESTION, None)
    return await _enter_best_performer(session, job, intake)


async def _answer_force_ranking(
    session: AsyncSession, job: Job, intake: JobSwotIntake, text: str
) -> str | None:
    _append(intake, "authority", text, None)
    last = _last_instrument(intake)
    if last == _TRADE_OFF:
        # §18.3: "This is the force-ranking, extracted conversationally." The
        # answer names what the manager would keep, which is a statement about
        # the role's strengths, so it is captured there.
        points, _sufficient = await capture_answer(session, "strengths", text)
        _merge_points(intake, "strengths", points)
        return await _enter_force_ranking(session, job, intake)
    quality = _quality(intake)
    quality["disqualifiers"] = _disqualifier_lines(text)
    _set_quality(intake, quality)
    return await _enter_best_performer(session, job, intake)


def _last_instrument(intake: JobSwotIntake) -> str | None:
    asked = list(intake.probes_asked or [])
    return str(asked[-1]) if asked else None


#: A disqualifier answer that means "none". Matched as whole answers rather than
#: as substrings, because "no formal qualification is needed" contains "no" and
#: is a real answer to a different question.
_NO_DISQUALIFIER = frozenset(
    {"no", "none", "nothing", "no.", "none.", "nothing.", "n/a", "na", "nope"}
)


def _disqualifier_lines(text: str) -> list[str]:
    """The manager's stated hard exclusions, one per line, or an empty list.

    Kept VERBATIM rather than normalised, because the next thing that happens to
    them is `company_dna.prohibited_in`, and rewriting a manager's phrasing
    before checking it for an unlawful filter would be laundering exactly the
    thing the check exists to catch.
    """
    stripped = " ".join(str(text or "").split())
    if not stripped or stripped.strip().casefold() in _NO_DISQUALIFIER:
        return []
    return [
        " ".join(line.split())[:MAX_POINT_CHARS]
        for line in re.split(r"[\n;]|(?<=[.!?])\s+", stripped)
        if line.strip()
    ][:MAX_POINTS_PER_AREA]


async def _enter_best_performer(
    session: AsyncSession, job: Job, intake: JobSwotIntake
) -> str | None:
    intake.phase = PHASE_BEST_PERFORMER
    return await _ask(session, job, intake, BEST_PERFORMER_QUESTION, None)


async def _answer_best_performer(
    session: AsyncSession, job: Job, intake: JobSwotIntake, text: str
) -> str | None:
    _append(intake, "authority", text, None)
    verdict = swot_quality.excludes_best_performer(text)
    intake.best_performer_excluded = verdict
    if verdict is None:
        quality = _quality(intake)
        # Asked and not answered is not the same as unasked, and neither is a
        # pass. Recorded so the artifact carries it and a reviewer can see the
        # test was run.
        quality["best_performer_answer_unreadable"] = True
        _set_quality(intake, quality)
    return await _enter_situation(session, job, intake)


async def _enter_situation(
    session: AsyncSession, job: Job, intake: JobSwotIntake
) -> str | None:
    intake.phase = PHASE_SITUATION
    return await _ask(session, job, intake, _situation_prompt(intake), None)


async def _answer_situation(
    session: AsyncSession, job: Job, intake: JobSwotIntake, text: str
) -> str | None:
    _append(intake, "authority", text, None)
    key = _read_situation(intake, text)
    if key is not None:
        intake.situation_key = key
        intake.situation_confirmed_at = datetime.now(timezone.utc)
        return await _close_or_rework(session, job, intake)
    if _SITUATION_REASK not in _asked(intake):
        _mark_asked(intake, _SITUATION_REASK)
        return await _ask(
            session,
            job,
            intake,
            "I did not catch which of these it is, and I would rather ask than "
            "guess: "
            + ", ".join(
                situations.SITUATIONS[k].label for k in situations.SITUATION_TYPES
            )
            + "?",
            None,
        )
    # Asked twice and still unresolved. Left UNSET, which `swot_quality.review`
    # turns into `situation_undeterminable` and hands the session back -- rather
    # than taking the strongest signal, which would let a coin flip re-weight
    # the whole matrix.
    return await _close_or_rework(session, job, intake)


async def _answer_rework(
    session: AsyncSession, job: Job, intake: JobSwotIntake, text: str
) -> str | None:
    """One turn of §18.5's hand-back, captured into whatever it reopened."""
    quality = _quality(intake)
    area = quality.get("rework_area")
    rule = quality.get("rework_rule")
    _append(intake, "authority", text, area)
    if rule == "prohibited_disqualifier":
        quality["disqualifiers"] = _disqualifier_lines(text)
        _set_quality(intake, quality)
    elif rule == "situation_undeterminable":
        key = _read_situation(intake, text)
        if key is not None:
            intake.situation_key = key
            intake.situation_confirmed_at = datetime.now(timezone.utc)
    elif rule == "excludes_best_performer":
        # The manager has just been asked what they would drop or add. Their
        # answer is a statement about what the role actually needs, so it is
        # captured; the refusal itself is cleared because the requirement set
        # they were refused on no longer stands.
        points, _sufficient = await capture_answer(session, "weaknesses", text)
        _merge_points(intake, "weaknesses", points)
        intake.best_performer_excluded = False
    elif area in SWOT_AREAS:
        points, _sufficient = await capture_answer(session, area, text)
        _merge_points(intake, area, points)
    else:
        points, _sufficient = await capture_answer(session, "weaknesses", text)
        _merge_points(intake, "weaknesses", points)
    return await _close_or_rework(session, job, intake)


async def _close_or_rework(
    session: AsyncSession, job: Job, intake: JobSwotIntake
) -> str | None:
    """§18.5. Either the intake closes, or it goes back to the hiring manager.

    THERE IS NO BUDGET AND NO AUTO-ACCEPT. §18.5 says an intake "is rejected
    back to the hiring manager" if any of six things is true, and a rework
    counter that eventually gave up would mean the rule holds until somebody is
    persistent, which is the same as not holding. A session with an outstanding
    refusal stays open, with the sentence that says what is wanted.

    Note what is NOT a rejection: an outstanding check. §18.5's best-performer
    test that was asked and answered unreadably is recorded as outstanding and
    carried on the artifact. A test nobody could read is not a test somebody
    failed.
    """
    report = quality_review(intake)
    quality = _quality(intake)
    quality.update(report.as_dict())
    if report.rejections:
        first = report.rejections[0]
        quality["rework_rule"] = first.rule
        quality["rework_area"] = first.area
        _set_quality(intake, quality)
        intake.phase = PHASE_REWORK
        logger.info(
            "swot_intake.handed_back job_id=%s rule=%s area=%s",
            job.id,
            first.rule,
            first.area,
        )
        return await _ask(session, job, intake, first.say, first.area)

    quality.pop("rework_rule", None)
    quality.pop("rework_area", None)
    _set_quality(intake, quality)
    intake.phase = PHASE_COMPLETE
    intake.status = SWOT_STATUS_COMPLETE
    intake.completed_at = datetime.now(timezone.utc)
    intake.pending_prompt = None
    intake.correlation_id = intake.correlation_id or job.correlation_id
    job.swot_completed_at = intake.completed_at
    await session.flush()
    logger.info(
        "swot_intake.completed job_id=%s captured=%s situation=%s probes=%d "
        "best_performer_excluded=%s",
        job.id,
        {area: len(values) for area, values in intake.captured().items()},
        intake.situation_key,
        len(intake.probes_asked or []),
        intake.best_performer_excluded,
    )
    # Bodha's hand-off to Sutra (spec §4). Published AFTER the flush, so a
    # publish that goes wrong cannot cost the authority the answers they
    # have already given -- the completion is durable before this runs, and
    # `publish_swot_evidence` swallows its own failures on top of that.
    publish_swot_evidence(job, intake, correlation_id=intake.correlation_id)
    return None


async def _ask(
    session: AsyncSession,
    job: Job,
    intake: JobSwotIntake,
    question: str,
    area: str | None,
) -> str:
    """Put one question, record it, and make it the pending prompt.

    Every question the manager reads goes through here, so the transcript
    records what was actually on screen rather than what was next in a list --
    the same `pending_prompt` mechanism the candidate conversation uses.
    """
    text = conversation_guardrails.inspect_agent_output(question) or question
    intake.pending_prompt = text
    _append(intake, "agent", text, area)
    await session.flush()
    return text


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
    if intake.pending_prompt:
        return intake.pending_prompt
    area = _area_for(intake)
    if area is not None:
        return await _ask(
            session,
            job,
            intake,
            await compose_question(session, job, intake, area=area),
            area,
        )
    # A session resumed in a later phase with no pending prompt: re-put the
    # question that phase owes. Re-entering the phase rather than inventing a
    # prompt keeps one path to every question in the session.
    if intake.phase == PHASE_FORCE_RANKING:
        return await _enter_force_ranking(session, job, intake)
    if intake.phase == PHASE_BEST_PERFORMER:
        return await _enter_best_performer(session, job, intake)
    if intake.phase == PHASE_SITUATION:
        return await _enter_situation(session, job, intake)
    if intake.phase == PHASE_REWORK:
        return await _close_or_rework(session, job, intake)
    return None


# ── Bodha publishes, Sutra consumes (spec §4) ────────────────────────────────
#
# WHY THE ARTIFACT IS BUILT ON DEMAND RATHER THAN STORED
# ------------------------------------------------------
# There is no artifact table, and adding one would not buy the property this
# hand-off needs. The intake rows ARE the durable record; an artifact row beside
# them would be a second copy of the same four arrays that can disagree with the
# first, and the disagreement would be invisible -- exactly the shape of a
# `framework_generated_at` stamp with no competency rows behind it. So the
# producer builds the typed envelope from its own rows every time it is asked,
# which means an artifact can never be stale with respect to the data it
# describes. What crosses to Sutra is still a verified, typed, scoped artifact
# and never the ORM rows.

#: One intake per job, enforced by `uq_job_swot_intake_job`, and it completes
#: once. There is no reopen path, so there is no second version to number: a
#: counter here would be a field that is always 1 and reads as though it could
#: be something else.
SWOT_ARTIFACT_VERSION = 1

#: Which part of the role context each SWOT area is actual evidence for. A
#: mapping rather than a blanket "all covered" because `gates.bodha_gate` reads
#: this to decide whether the intake is fit to build a matrix from, and an
#: intake that claimed coverage it did not produce would pass the gate while
#: leaving Sutra to invent the missing half. `team_context` is deliberately
#: absent: nothing in the four areas asks who the person works with, so claiming
#: it would be the same lie in the other direction. The gate answers that with
#: one medium finding, which is not disqualifying, so a genuinely complete
#: intake still publishes as validated.
_AREA_CONTEXT: dict[str, tuple[str, ...]] = {
    "strengths": ("role_objectives", "success_criteria"),
    "weaknesses": ("known_challenges",),
    "opportunities": ("role_objectives",),
    "threats": ("known_challenges",),
}


def context_covered(captured: dict[str, list]) -> list[str]:
    """The role context this intake actually produced evidence for."""
    covered: list[str] = []
    for area, aspects in _AREA_CONTEXT.items():
        if not captured.get(area):
            continue
        for aspect in aspects:
            if aspect not in covered:
                covered.append(aspect)
    return covered


def jd_version(job: Job) -> str:
    """A fingerprint of the job description this intake was captured against.

    Derived rather than read from a column because there is no `jd_version`
    column and inventing one would need a migration nothing else wants yet. A
    content hash answers the question a consumer is actually asking -- "is this
    the JD I am building a matrix from, or an older one" -- and it answers it
    correctly for every job already in the database, which a new column could
    only do going forward.
    """
    material = json.dumps(
        {"markdown": job.jd_markdown or "", "sections": job.jd_json or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _swot_payload(job: Job, intake: JobSwotIntake) -> dict[str, Any]:
    """The typed hand-off, built from validated fields only.

    Nothing here is prose the intake agent wrote about its own reasoning: the
    four arrays are the authority's captured points, and everything else is an
    identifier or a timestamp. That is the guarantee `artifacts.publish` also
    checks for, and the reason it can be checked at all is that this function
    never reaches for the transcript.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    captured = intake.captured()
    completed_at = intake.completed_at or datetime.now(timezone.utc)
    return {
        **captured,
        # Provenance. Who said it and when, as identifiers only -- a consumer
        # showing where a criterion came from must not need the answer text.
        "sources": [
            {
                "kind": "swot_intake",
                "intake_id": str(intake.id),
                "conducted_by": str(intake.conducted_by) if intake.conducted_by else None,
                "captured_at": completed_at.isoformat(),
            }
        ],
        "provenance": {
            "producer": identity.BODHA,
            "captured_points": {area: len(points) for area, points in captured.items()},
            "follow_ups_used": intake.follow_ups_used,
        },
        "job": {
            "job_id": str(job.id),
            "title": job.title,
            "department": job.department,
            "grade": job.assessment_grade,
            "experience_min_years": job.experience_min_years,
            "experience_max_years": job.experience_max_years,
        },
        "jd_version": jd_version(job),
        # What the role is FOR and what doing it well looks like. Named
        # separately from the quadrants because Sutra's matrix needs both, and a
        # consumer that had to re-derive them from the quadrant names would be
        # re-implementing this mapping in a second place.
        "role_expectations": list(captured.get("opportunities") or []),
        "success_characteristics": list(captured.get("strengths") or []),
        "context_covered": context_covered(captured),
        # No contradiction detection is implemented, and an empty list says so
        # honestly. A missing key would read to the gate as "none found", which
        # is a claim this intake is not in a position to make.
        "contradictions": [],
        "captured_at": completed_at.isoformat(),
        "artifact_version": SWOT_ARTIFACT_VERSION,
    }


def publish_swot_evidence(
    job: Job, intake: JobSwotIntake, *, correlation_id: str | None = None
) -> artifacts.Artifact | None:
    """Run Bodha's gate, then publish the `swot_evidence` artifact.

    Returns None rather than raising on any failure, and that direction is the
    whole point. This runs on the request that finishes a hiring manager's SWOT
    intake -- a live path that worked before artifacts existed. A publish that
    could raise would turn a contract bug in a new layer into an intake the
    authority cannot complete, and the intake is the one thing here that cannot
    be re-run cheaply: the manager has already answered the questions.

    The gate's verdict travels as `validated`. It is NOT a publish veto: a
    consumer is entitled to see that the producer's own gate did not pass and
    decide for itself, and refusing to publish would leave Sutra unable to tell
    a failed gate from an intake that never happened.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    if not is_complete(intake):
        return None
    try:
        payload = _swot_payload(job, intake)
        verdict = gates.run_gate(identity.BODHA, payload)
        envelope = run_envelope.Envelope.for_run(
            tenant_id=str(job.tenant_id),
            agent_id=identity.BODHA,
            task_type="extraction",
            interactive=False,
            job_id=str(job.id),
            workflow_id=correlation_id,
            context_version=payload["jd_version"],
        )
        payload["correlation_id"] = envelope.workflow_id
        artifact = artifacts.publish(
            producer=identity.BODHA,
            artifact_type="swot_evidence",
            payload=payload,
            tenant_id=str(job.tenant_id),
            job_id=str(job.id),
            version=SWOT_ARTIFACT_VERSION,
            source_refs=(f"job_swot_intakes:{intake.id}", f"jobs:{job.id}"),
            validated=verdict.passed,
        )
        # Identifiers, counts and a boolean. No captured point, no answer text:
        # this line is read from far more places than the intake screen is.
        logger.info(
            "swot_intake.artifact_published job_id=%s artifact_id=%s validated=%s "
            "confidence=%s",
            job.id,
            artifact.artifact_id,
            verdict.passed,
            verdict.confidence,
        )
        return artifact
    except Exception:
        logger.warning(
            "swot_intake.artifact_publish_failed job_id=%s", job.id, exc_info=True
        )
        return None


async def published_evidence(
    session: AsyncSession, job: Job, *, correlation_id: str | None = None
) -> artifacts.Artifact | None:
    """Bodha's published SWOT evidence for this job, or None if there is none.

    The entry point Sutra uses. It loads the intake through the same RLS-aware
    session every other tenant-scoped read goes through, and returns nothing at
    all for an intake that is absent or unfinished -- an incomplete intake is
    not a thin artifact, it is no artifact, and the two must not read the same
    to a consumer.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    intake = (
        await session.execute(
            select(JobSwotIntake).where(JobSwotIntake.job_id == job.id)
        )
    ).scalars().first()
    if intake is None:
        return None
    return publish_swot_evidence(job, intake, correlation_id=correlation_id)


assert set(AREA_LABELS) == set(SWOT_AREAS)
assert set(AREA_MEANINGS) == set(SWOT_AREAS)
assert set(AREA_FALLBACK_QUESTIONS) == set(SWOT_AREAS)
