"""Technical questions, written per candidate at the moment they are asked.

WHAT THIS REPLACED (2026-08-06, client decision)
------------------------------------------------
A per-JOB preset bank. `technical_questions` held 20/17/15/12 stored strings
that a company authored and edited through the Company Portal, and every
applicant to the job read exactly those strings whatever their resume said. The
CRUD screens, the routes behind them, and the generator that filled the bank are
all gone.

Questions are now written one at a time, during the conversation, from the job
description, THIS candidate's resume and everything said so far.

THE TWO THINGS THAT DID NOT MOVE, AND WHY
-----------------------------------------
1. **The coverage plan stays deterministic.** `skill_plan` is a pure function of
   the job's JD: the same ordered list of skills, of the same length, for every
   candidate on that job. Two candidates are therefore probed on the same skills
   in the same order, which is what keeps their reports comparable and keeps a
   run reproducible. What varies per candidate is how each skill is approached,
   never which skills there are. This is the same rule the PPI framework
   follows, applied to the technical half.

2. **An answer is still scored against ITS OWN rubric.** This was the whole
   reason the old code forbade generating a technical question mid-conversation
   (`interviewer.MODE_REWORD`): a fresh question graded against a stored rubric
   would be graded against a rubric written for a question nobody was asked. The
   objection is answered by generating the RUBRIC WITH THE QUESTION, in one
   model call, and persisting both before the candidate reads either
   (`CandidateTechnicalQuestion`). The rubric now always belongs to the question
   that was actually asked -- a stronger guarantee than the preset bank gave,
   where a recruiter could edit a stored prompt in the UI and leave its rubric
   untouched.

THE LOOP
--------
Generation runs inside `agent_loop.run_loop`, not as a one-shot call. The
criteria are deterministic and are the interesting part:

  * the question names the skill it is supposed to probe, so a model that
    wandered onto a different topic is rejected rather than silently producing
    an answer the report files under the wrong heading;
  * it is ONE question, not a stacked list;
  * it carries all five rubric bands with real text, because a partial rubric
    scores every answer at whatever band happens to be present;
  * it is not a repeat of something already asked;
  * it is not an essay.

A rejection is fed back verbatim as an instruction and one more attempt is made.
That second attempt is the entire reason the loop exists: "you returned three of
the five rubric bands" is a defect a model fixes immediately when told, and the
previous one-shot code threw the whole response away and shipped a canned
question instead.

DEGRADATION IS THE PRODUCT'S PREVIOUS BEHAVIOUR
-----------------------------------------------
Every failure path lands on `fallback_question`, a deterministic probe built
from the skill and the same default rubric the old bank used on an outage. A
candidate is mid-assessment on a live request; a provider problem costs the
specificity of the question and nothing else. `generated_at` stays NULL, which
is the honest record that it happened and what telemetry counts.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from itertools import cycle
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import CandidateTechnicalQuestion
from app.models.job import Job
from app.prompts import fragments, registry
from app.services import agent_loop, llm_router

logger = logging.getLogger(__name__)

__all__ = [
    "TECHNICAL_QUESTION_COUNTS",
    "DEFAULT_RUBRIC",
    "RUBRIC_BANDS",
    "technical_question_count",
    "skill_plan",
    "fallback_question",
    "ensure_slots",
    "write_question",
]

#: Technical question count per grade (spec §5). Unchanged by this release: the
#: questions are written differently, not fewer of them.
TECHNICAL_QUESTION_COUNTS: dict[str, int] = {
    "non_managerial": 20,
    "managerial": 17,
    "leadership": 15,
    "cxo": 12,
}

#: The five bands every rubric must carry. A rubric missing a band cannot
#: express the grade that band stands for, so `_llm_score` would quietly compress
#: the scale -- which looks like a harsh or generous marker rather than a
#: malformed rubric.
RUBRIC_BANDS: tuple[str, ...] = ("0_39", "40_59", "60_74", "75_89", "90_100")

DEFAULT_RUBRIC: dict[str, str] = {
    "0_39": "No relevant example or materially incorrect approach.",
    "40_59": "Partial knowledge with limited practical evidence.",
    "60_74": "Sound practical approach with a credible example.",
    "75_89": "Strong depth, trade-offs, and measurable outcomes.",
    "90_100": "Exceptional depth, judgement, outcomes, and transferable insight.",
}

#: Deterministic probe shapes, cycled so a job whose JD declares one skill still
#: yields distinguishable questions rather than the same sentence N times.
FALLBACK_ANGLES: tuple[str, ...] = (
    "Describe a demanding situation where you applied {skill}. What did you decide, implement, measure, and learn?",
    "Walk me through how you would diagnose a problem involving {skill}. What would you check first, and why?",
    "What trade-offs have you had to make when working with {skill}? Give a concrete example and its outcome.",
    "How do you verify that your work involving {skill} is correct and holds up in production or under review?",
    "Describe the most complex piece of work you have delivered using {skill}. What made it hard?",
)

#: A technical dimension's name is shown to the client verbatim, so it must read
#: as a SKILL ("PostgreSQL", "Incident response"), never as a whole JD sentence.
MAX_SKILL_LABEL = 60

#: A question longer than this is a reading comprehension test, not an interview
#: question. Bounded here rather than truncated, for the standing reason: a
#: truncated question loses its question mark.
MAX_QUESTION_CHARS = 420

#: A rubric band longer than this is an essay the scorer has to read on every
#: single answer.
MAX_RUBRIC_BAND_CHARS = 400


def technical_question_count(grade: str | None) -> int:
    return TECHNICAL_QUESTION_COUNTS.get(
        grade or "", TECHNICAL_QUESTION_COUNTS["non_managerial"]
    )


# ── The deterministic coverage plan ──────────────────────────────────────────


def _topic_label(sentence: str) -> str:
    """Condense a JD responsibility line into a short topic label.

    Takes the leading clause, drops a leading verb like "Build"/"Design" so the
    label reads as a subject rather than an instruction, and truncates on a word
    boundary. "Design MongoDB schemas and indexes that support the product's
    access patterns" -> "MongoDB schemas and indexes".
    """
    clause = re.split(r"[,;:.]| that | which | so that ", sentence.strip(), maxsplit=1)[0]
    words = clause.split()
    if words and words[0].lower() in {
        "build", "design", "implement", "maintain", "write", "own", "run",
        "define", "deliver", "monitor", "manage", "support", "create",
        "develop", "integrate", "profile", "diagnose", "model", "take",
    }:
        words = words[1:]
    if words and words[0].lower() in {"and", "the", "a", "an"}:
        words = words[1:]
    label = " ".join(words).strip(" -,")
    while len(label) > MAX_SKILL_LABEL and " " in label:
        label = label.rsplit(" ", 1)[0]
    label = label.strip(" -,")
    return (label[:1].upper() + label[1:]) if label else ""


def jd_skills(job: Job) -> list[str]:
    """The JD's declared skills -- the canonical technical dimensions.

    Only `jd.skills` is treated as a skill. Responsibilities and accountabilities
    are prose, and using them verbatim put sentences like "Generative features
    degrade gracefully rather than failing outright" into the report's Technical
    section as if they were a skill.
    """
    jd = job.jd_json or {}
    skills: list[str] = []
    seen: set[str] = set()
    for item in jd.get("skills") or []:
        value = str(item).strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            skills.append(value[:MAX_SKILL_LABEL])
    if not skills:
        skills = [(job.title or "this role")[:MAX_SKILL_LABEL]]
    return skills


def jd_topics(job: Job) -> list[str]:
    """Condensed topic labels mined from JD prose, for plans that need more
    variety than the declared skills alone can provide."""
    jd = job.jd_json or {}
    topics: list[str] = []
    seen = {skill.casefold() for skill in jd_skills(job)}
    for field in ("responsibilities", "accountabilities"):
        value = jd.get(field)
        items = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
        for item in items:
            label = _topic_label(str(item))
            if label and label.casefold() not in seen:
                seen.add(label.casefold())
                topics.append(label)
    return topics


def skill_plan(job: Job, count: int | None = None) -> list[str]:
    """The ordered skills this job's technical half probes, one per slot.

    PURE, and that is the point. This is the product's comparability guarantee
    for the technical half: it depends only on the job's JD and its grade, so
    every candidate for a job is probed on the same skills in the same order,
    and a re-run produces the same plan. The QUESTION asked about each skill is
    written per candidate; WHICH skills are covered is not up for negotiation.

    Declared skills come first and in JD order, then mined topics, then the
    declared skills cycle again -- so a JD with four skills and twenty slots
    covers each of them five times rather than asking about the first one twenty
    times.
    """
    target = count if count is not None else technical_question_count(job.assessment_grade)
    if target <= 0:
        return []
    ordered = jd_skills(job) + jd_topics(job)
    # `jd_skills` never returns empty (it falls back to the job title), so the
    # cycle below always has something to yield and cannot spin forever.
    source = cycle(ordered)
    return [next(source)[:MAX_SKILL_LABEL] for _ in range(target)]


def fallback_question(skill: str, ordinal: int) -> str:
    """The deterministic probe used whenever generation is unavailable.

    Indexed by ordinal rather than chosen at random so a retried turn shows the
    candidate the same question it showed them the first time.
    """
    angle = FALLBACK_ANGLES[(max(1, ordinal) - 1) % len(FALLBACK_ANGLES)]
    return angle.format(skill=skill)


# ── Slot creation ────────────────────────────────────────────────────────────


async def ensure_slots(
    session: AsyncSession, job: Job, link: Any
) -> list[CandidateTechnicalQuestion]:
    """This candidate's technical slots, created once and then reused.

    Rows exist BEFORE their questions do, carrying the deterministic fallback
    and the default rubric. Three things fall out of that, all of them wanted:
    the conversation always has something askable even with every provider down;
    `question_key` is a stable row id from the first turn, so a scorer never sees
    a key appear mid-conversation; and a candidate who is already mid-assessment
    when a redelivery arrives keeps exactly the slots they started with.

    Idempotent. A link that already has slots keeps them untouched -- including
    the ones whose questions have already been written and answered.
    """
    existing = (
        await session.execute(
            select(CandidateTechnicalQuestion)
            .where(CandidateTechnicalQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateTechnicalQuestion.ordinal)
        )
    ).scalars().all()
    if existing:
        return list(existing)

    plan = skill_plan(job)
    rows = [
        CandidateTechnicalQuestion(
            tenant_id=job.tenant_id,
            job_id=job.id,
            job_candidate_link_id=link.id,
            ordinal=ordinal,
            skill=skill[:255],
            prompt=fallback_question(skill, ordinal),
            rubric_json=dict(DEFAULT_RUBRIC),
        )
        for ordinal, skill in enumerate(plan, 1)
    ]
    session.add_all(rows)
    await session.flush()
    logger.info(
        "technical_interview.slots_created link_id=%s count=%d grade=%s",
        link.id, len(rows), job.assessment_grade,
    )
    return rows


# ── The generation loop ──────────────────────────────────────────────────────


#: Text in `app/prompts/technical_write_question.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_SYSTEM = registry.render(
    "technical_write_question",
    one_question=fragments.ONE_QUESTION,
    no_evaluation=fragments.NO_EVALUATION,
    candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
)


def _normalise(payload: Any) -> dict[str, Any] | None:
    """Parsed model output as {question, rubric}, or None if it is not that."""
    if not isinstance(payload, dict):
        return None
    question = " ".join(str(payload.get("question") or "").split())
    rubric_raw = payload.get("rubric")
    if not question or not isinstance(rubric_raw, dict):
        return None
    rubric = {
        band: " ".join(str(rubric_raw.get(band) or "").split())
        for band in RUBRIC_BANDS
    }
    return {"question": question, "rubric": rubric}


#: An acronym is UPPER CASE, not merely capitalised, and that distinction is
#: the whole rule. A first pass tested "short and starts with a capital", which
#: swept in "Kafka" -- a proper noun every good question spells out, and exactly
#: the case the mention check exists to police. A single trailing lowercase "s"
#: is allowed so "LLMs" and "APIs" read as the plurals they are.
_ACRONYM = re.compile(r"^[A-Z0-9][A-Z0-9+#./-]{0,7}s?$")


def _looks_like_acronym(skill: str) -> bool:
    """Whether `skill` is a short initialism a good question may expand.

    THE DEFECT THIS EXISTS FOR, FOUND BY RUNNING A REAL INTERVIEW
    ------------------------------------------------------------
    An end-to-end run against a live "AI / Generative AI Engineer" job produced
    a skill plan starting `['LLMs', 'RAG', 'LangGraph', ...]`. The model wrote a
    perfectly good opening question -- "can you describe a specific situation
    where you applied Large Language Models..." -- and `_mentions` rejected it,
    twice, because the string "llms" does not appear in it. Every candidate for
    that job therefore read the deterministic fallback probe as question one.

    That is precisely the failure this repo has a standing rule about: with a
    guard on generated text, the hard part is the distinction and not the
    detection, and a guard that rejects a real question fails INVISIBLY -- the
    logs record a rejection, the product degrades quietly, and it looks like a
    provider outage.

    Detecting an arbitrary expansion cheaply is not possible, so the tolerant
    direction is the correct one: for an acronym the criterion is skipped and
    the prompt instruction carries it alone. The miss it allows is a rare
    off-topic question on a short-named skill; the alternative it avoids is
    every question on that skill being canned.
    """
    return bool(_ACRONYM.match(skill.strip()))


def _mentions(text: str, skill: str) -> bool:
    """Whether `text` plausibly refers to `skill`.

    Compared on WORDS rather than as a substring, because a skill label is
    routinely a phrase ("MongoDB schemas and indexes") that a well-written
    question quite properly says in a different order. Requiring every
    significant word would reject good questions; requiring at least one
    significant word catches the failure this criterion exists for, which is a
    model that ignored the skill entirely and asked about something else.
    """
    skill = skill.strip()
    # A whole-label acronym ("LLMs", "RAG") is expanded by good questions. See
    # `_looks_like_acronym`: this is deliberately the tolerant direction.
    if _looks_like_acronym(skill):
        return True
    significant = [
        word for word in re.findall(r"[A-Za-z0-9+#./-]{3,}", skill.casefold())
        if word not in {"and", "the", "for", "with", "using"}
    ]
    if not significant:
        return True  # nothing specific to check for; do not reject on it
    haystack = text.casefold()
    return any(word in haystack for word in significant)


#: A question that stacks several asks. Counting question marks alone is wrong:
#: "How did you handle the outage? Walk me through it." is one question said in
#: two sentences, and rejecting it would reject good interviewer speech. What
#: actually signals a stacked ask is an enumerated or conjoined SECOND demand.
_STACKED = re.compile(
    r"(?:\balso[, ]|\bsecond(?:ly)?\b|\bfinally\b|\bin addition\b|"
    r"\bpart\s*(?:two|2|b)\b|\(\s*[2b]\s*\)|\b2[.)]\s)",
    re.IGNORECASE,
)


def _evaluate(skill: str, asked_before: list[str]) -> Any:
    """Build the deterministic criteria for one slot.

    Returned as a closure so the criteria carry the slot's own skill and the
    questions already asked, and so they stay pure functions of their input --
    which is what makes them testable without a model or a database.
    """

    def evaluate(candidate: dict[str, Any]) -> agent_loop.Critique:
        question = candidate["question"]
        rubric = candidate["rubric"]
        reasons: list[str] = []

        if len(question) > MAX_QUESTION_CHARS:
            reasons.append(
                f"keep the question under {MAX_QUESTION_CHARS} characters; the "
                f"previous attempt was {len(question)}"
            )
        if not _mentions(question, skill):
            reasons.append(
                f"the question must actually probe {skill!r}; the previous "
                "attempt did not refer to it at all"
            )
        if _STACKED.search(question):
            reasons.append(
                "ask exactly one thing; the previous attempt stacked a second "
                "question or sub-part onto the first"
            )
        empty_bands = [band for band in RUBRIC_BANDS if not rubric.get(band)]
        if empty_bands:
            reasons.append(
                "the rubric must carry all five bands with real text; these "
                "were missing or empty: " + ", ".join(empty_bands)
            )
        long_bands = [
            band for band in RUBRIC_BANDS
            if len(rubric.get(band) or "") > MAX_RUBRIC_BAND_CHARS
        ]
        if long_bands:
            reasons.append(
                f"keep every rubric band under {MAX_RUBRIC_BAND_CHARS} "
                "characters; these were longer: " + ", ".join(long_bands)
            )
        # A rubric whose bands are identical strings cannot separate a strong
        # answer from a weak one, and it is a real failure mode: a model under
        # instruction pressure will pad the shape it was asked for.
        filled = [rubric[band] for band in RUBRIC_BANDS if rubric.get(band)]
        if len(filled) > 1 and len(set(filled)) < len(filled):
            reasons.append(
                "each rubric band must describe a DIFFERENT standard; the "
                "previous attempt repeated the same text across bands"
            )
        if _is_repeat(question, asked_before):
            reasons.append(
                "this is a question the candidate has already been asked; ask "
                "about a different aspect of the skill"
            )

        return agent_loop.reject(*reasons) if reasons else agent_loop.ok()

    return evaluate


def _terms(text: str) -> set[str]:
    """Content words, for the repeat check. Deliberately crude."""
    return {
        word
        for word in re.findall(r"[a-z0-9+#./-]{4,}", text.casefold())
        if word not in {
            "have", "with", "that", "this", "your", "when", "what", "which",
            "would", "there", "their", "about", "been", "were", "from", "into",
            "describe", "explain", "walk", "through", "give", "tell",
        }
    }


def _is_repeat(text: str, asked_before: list[str] | None) -> bool:
    """Whether this question covers ground already covered.

    Compared on content words rather than exact strings, because a model told
    not to repeat itself will happily reword the same question. The threshold is
    high (0.8) on purpose: several questions about one skill SHOULD overlap
    heavily in vocabulary, and a low threshold would reject the legitimate
    second and third probes of a skill the plan deliberately covers more than
    once.
    """
    terms = _terms(text)
    if not terms:
        return False
    for previous in asked_before or []:
        earlier = _terms(previous)
        if not earlier:
            continue
        if len(terms & earlier) / max(len(terms | earlier), 1) > 0.8:
            return True
    return False


async def write_question(
    *,
    session: AsyncSession | None,
    job: Job,
    row: CandidateTechnicalQuestion,
    resume_excerpt: str = "",
    transcript: list[dict[str, Any]] | None = None,
    asked_before: list[str] | None = None,
) -> agent_loop.LoopResult[dict[str, Any]]:
    """Write this slot's question and rubric, and persist both onto `row`.

    The pair is written together, by one call, and stored before the candidate
    reads either -- which is what lets a generated technical question be scored
    against a rubric that genuinely belongs to it.

    Persists only on success. A degraded result leaves the row exactly as
    `ensure_slots` created it: the deterministic probe and the default rubric,
    with `generated_at` still NULL. That NULL is the record that this candidate
    read a fallback, and it is what makes a silent degradation countable.

    Returns the `LoopResult` rather than a bare string so the caller can log the
    degradation and stamp telemetry. Never raises.
    """
    recent = _recent_turns(transcript)

    async def execute(reflection: str) -> dict[str, Any]:
        payload = {
            "skill_to_probe": row.skill,
            "job_description": (job.jd_markdown or "")[:2500],
            "candidate_resume": (resume_excerpt or "")[:2500],
            "conversation_so_far": recent,
            "already_asked": list(asked_before or [])[-20:],
        }
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            # The reflection is an ASSISTANT-directed correction, appended as a
            # further user turn so it is the last thing the model reads.
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "conversation_turn",
            messages,
            response_format_json=True,
            session=session,
        )
        parsed = _normalise(json.loads(raw))
        if parsed is None:
            raise ValueError("response was not {question, rubric}")
        return parsed

    result = await agent_loop.run_loop(
        name="technical_question",
        execute=execute,
        evaluate=_evaluate(row.skill, list(asked_before or [])),
        fallback={"question": row.prompt, "rubric": dict(row.rubric_json or DEFAULT_RUBRIC)},
        max_attempts=agent_loop.INTERACTIVE_ATTEMPTS,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )

    if not result.degraded:
        try:
            row.prompt = result.value["question"]
            row.rubric_json = result.value["rubric"]
            row.generated_at = datetime.now(timezone.utc)
            if session is not None:
                await session.flush()
        except Exception as exc:  # noqa: BLE001
            # PERSISTENCE FAILING MUST NOT COST THE CANDIDATE THEIR TURN.
            #
            # The loop already survives every provider failure; this guard
            # covers the session itself being unusable -- which is a real state,
            # not a hypothetical one. The router loads provider keys through the
            # SAME session as its caller and marks failing keys unhealthy, so a
            # bad enough provider outage can leave the transaction rolled back
            # underneath us. Without this, the one moment every provider is down
            # is the moment `respond` raises a 500 instead of degrading.
            #
            # Reported as degraded because that is what it is: the candidate
            # will read the deterministic probe, and `generated_at` must stay
            # NULL so the transcript does not claim a question was written.
            logger.warning(
                "technical_interview.persist_failed link_id=%s ordinal=%d error=%s",
                row.job_candidate_link_id, row.ordinal, type(exc).__name__,
            )
            return agent_loop.LoopResult(
                value={"question": fallback_question(row.skill, row.ordinal),
                       "rubric": dict(DEFAULT_RUBRIC)},
                degraded=True,
                attempts=result.attempts,
                reasons=("the generated question could not be persisted",),
                elapsed_ms=result.elapsed_ms,
                error=type(exc).__name__,
            )
    else:
        logger.info(
            "technical_interview.degraded link_id=%s ordinal=%d attempts=%d "
            "reasons=%s",
            row.job_candidate_link_id, row.ordinal, result.attempts,
            list(result.reasons),
        )
    return result


def _recent_turns(transcript: list[dict[str, Any]] | None, turns: int = 6) -> list[dict[str, str]]:
    """The last few turns as plain speaker/text pairs.

    Same shape and same bound as `interviewer._recent`: enough to refer back
    without resending a 45-question interview on every turn, which would blow
    the token ceiling on the later questions of a long assessment.
    """
    rows: list[dict[str, str]] = []
    for message in (transcript or [])[-turns * 2:]:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        speaker = "interviewer" if message.get("speaker") == "agent" else "candidate"
        rows.append({"speaker": speaker, "text": content[:600]})
    return rows


async def load_for_link(
    session: AsyncSession, link_id: uuid.UUID
) -> list[CandidateTechnicalQuestion]:
    """This candidate's technical rows in ask order. The scorer's input."""
    rows = (
        await session.execute(
            select(CandidateTechnicalQuestion)
            .where(CandidateTechnicalQuestion.job_candidate_link_id == link_id)
            .order_by(CandidateTechnicalQuestion.ordinal)
        )
    ).scalars().all()
    return list(rows)
