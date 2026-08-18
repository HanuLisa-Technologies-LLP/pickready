"""The built-in tools. Every one is a bounded read; none of them call a model.

WHY READ TOOLS FIRST
--------------------
An agent's output is only as specific as what it was handed, and today each
agent assembles its own context by reaching into whichever service it happens
to know about. That is where two of this product's recurring defects come from:
the same fact arrives in two shapes at two call sites, and a guarantee that
lives at one call site (strip compensation, never emit a number) is absent at
the next one. These five tools are the single shape.

NONE OF THEM CALL AN LLM, AND THAT IS THE POINT
-----------------------------------------------
A tool is the deterministic half of an agent. Keeping generation out of the
tool layer is what lets `agent_loop` stay the only place a model call is
retried, bounded and counted -- two retry mechanisms wrapped around one another
is how a 15-second budget becomes a 60-second request.
"""
from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobCandidateLink, Profile
from app.models.assessment import (
    AssessmentConversation,
    AssessmentMessage,
    JobCompetency,
)
from app.services import rating
from app.services.rag import context as rag_context
from app.services.rag import retrieval as rag_retrieval
from app.services.tools import schemas
from app.services.tools.errors import ToolExecutionError
from app.services.tools.registry import ToolSpec, register

# Compensation-shaped keys, dropped from anything a tool emits. Same markers
# `matching` uses; centralised here so the guarantee travels with the layer
# every agent reads through rather than with one call site (ESD 16).
_COMPENSATION_MARKERS = (
    "salary",
    "compensation",
    "ctc",
    "pay",
    "budget",
    "remuneration",
    "package",
)


def _is_compensation_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _COMPENSATION_MARKERS)


def _labels(value: Any, *, limit: int = 40, width: int = 160) -> tuple[str, ...]:
    """Coerce a JD or resume field into short, deduplicated labels.

    JD fields arrive as a list, a string, or a list of dicts depending on which
    generator wrote them, and an agent should not have to branch on that. A
    responsibility line is prose, so its leading clause is taken -- the same
    reduction `ppi._jd_terms` performs, for the same reason: the label wants to
    read as a criterion, not as an instruction.
    """
    if isinstance(value, str):
        items: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, dict):
        items = [v for k, v in value.items() if not _is_compensation_key(k)]
    else:
        items = []

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            item = " ".join(
                str(v) for k, v in item.items() if v and not _is_compensation_key(k)
            )
        text = " ".join(str(item).split())
        if not text:
            continue
        text = text[:width].strip(" -,;:")
        folded = text.casefold()
        if text and folded not in seen:
            seen.add(folded)
            out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


# ── extract_jd ───────────────────────────────────────────────────────────────


async def _extract_jd(
    payload: schemas.JobRef, *, session: AsyncSession | None
) -> schemas.JobFacts:
    assert session is not None  # guaranteed by ToolSpec.needs_session
    job = await session.get(Job, payload.job_id)
    if job is None:
        raise ToolExecutionError("extract_jd", f"job {payload.job_id} not found")

    jd = job.jd_json or {}
    return schemas.JobFacts(
        job_id=job.id,
        title=job.title,
        department=job.department,
        grade=job.assessment_grade,
        experience_min_years=job.experience_min_years,
        experience_max_years=job.experience_max_years,
        skills=_labels(jd.get("skills")),
        responsibilities=_labels(
            jd.get("responsibilities") or jd.get("accountabilities")
        ),
        education=_labels(jd.get("education"), limit=8),
        jd_markdown=job.jd_markdown,
        assessment_status=job.assessment_status,
        framework_approved=job.framework_approved_at is not None,
    )


register(
    ToolSpec(
        name="extract_jd",
        handler=_extract_jd,
        input_model=schemas.JobRef,
        output_model=schemas.JobFacts,
        description="A job's requirements as an agent may see them, without compensation.",
        idempotent=True,
        # A JD changes when a recruiter edits it, which is rare and never
        # mid-assessment. Five minutes is short enough that an edit is visible
        # inside one recruiter's working session and long enough to matter
        # across the fan-out of one ranking run over fifty candidates.
        cache_ttl_seconds=300,
        timeout_seconds=3.0,
    )
)


# ── extract_resume ───────────────────────────────────────────────────────────


async def _extract_resume(
    payload: schemas.ProfileRef, *, session: AsyncSession | None
) -> schemas.ResumeFacts:
    assert session is not None
    profile = await session.get(Profile, payload.profile_id)
    if profile is None:
        raise ToolExecutionError(
            "extract_resume", f"profile {payload.profile_id} not found"
        )

    parsed = profile.parsed_fields_json or {}
    parsed = {k: v for k, v in parsed.items() if not _is_compensation_key(k)}
    text = " ".join((profile.resume_text or "").split())

    experience = parsed.get("total_experience_years")
    try:
        experience_years = float(experience) if experience is not None else None
    except (TypeError, ValueError):
        experience_years = None

    return schemas.ResumeFacts(
        profile_id=profile.id,
        candidate_id=profile.candidate_id,
        skills=_labels(parsed.get("skills")),
        education=_labels(parsed.get("education"), limit=8),
        employment_history=_labels(parsed.get("employment_history"), limit=20),
        total_experience_years=experience_years,
        resume_excerpt=text[: payload.resume_chars] if payload.resume_chars else "",
        # Parsed, not merely uploaded. A resume that failed extraction has text
        # and no fields, and an agent told only "resume present" would build a
        # prompt around a document it cannot actually read.
        resume_parsed=bool(parsed.get("skills") or parsed.get("employment_history")),
    )


register(
    ToolSpec(
        name="extract_resume",
        handler=_extract_resume,
        input_model=schemas.ProfileRef,
        output_model=schemas.ResumeFacts,
        description="A candidate's parsed resume, compensation-stripped and size-bounded.",
        idempotent=True,
        # A profile is rewritten by an async parse that can land at any moment
        # after upload. Short enough that the row an agent reads is the row the
        # parser most recently wrote.
        cache_ttl_seconds=120,
        timeout_seconds=3.0,
    )
)


# ── extract_assessment ───────────────────────────────────────────────────────


def pair_exchanges(
    messages: list[AssessmentMessage],
) -> list[tuple[AssessmentMessage, AssessmentMessage]]:
    """Pair each agent line with the candidate line that answered it.

    Walks ordinals rather than zipping alternate rows: the last question of an
    abandoned assessment has no answer, and zipping would silently pair it with
    somebody else's. This is the same rule `api/assessments.get_transcript`
    applies for the recruiter view; it is written here as a pure function so
    that route can adopt it and the two cannot drift.
    """
    pairs: list[tuple[AssessmentMessage, AssessmentMessage]] = []
    pending: AssessmentMessage | None = None
    for message in messages:
        if message.speaker == "agent":
            pending = message
            continue
        if pending is None:
            continue
        pairs.append((pending, message))
        pending = None
    return pairs


async def _extract_assessment(
    payload: schemas.LinkRef, *, session: AsyncSession | None
) -> schemas.AssessmentFacts:
    assert session is not None
    link = await session.get(JobCandidateLink, payload.link_id)
    if link is None:
        raise ToolExecutionError(
            "extract_assessment", f"application {payload.link_id} not found"
        )

    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()

    if conversation is None:
        # Invited but never opened, or never invited. An empty transcript is the
        # true answer; raising here would make "nothing yet" indistinguishable
        # from a broken lookup.
        return schemas.AssessmentFacts(
            link_id=link.id, status="not_started", grade="", exchanges=()
        )

    messages = list(
        (
            await session.execute(
                select(AssessmentMessage)
                .where(AssessmentMessage.conversation_id == conversation.id)
                .order_by(AssessmentMessage.ordinal)
            )
        ).scalars()
    )

    pairs = pair_exchanges(messages)
    kept = pairs[: payload.max_exchanges]
    return schemas.AssessmentFacts(
        link_id=link.id,
        conversation_id=conversation.id,
        status=conversation.status,
        grade=conversation.grade,
        exchanges=tuple(
            schemas.Exchange(
                question_key=question.question_key,
                domain=question.domain,
                question=question.content,
                answer=answer.content,
                answer_label=answer.answer_label,
            )
            for question, answer in kept
        ),
        truncated=len(pairs) > len(kept),
    )


register(
    ToolSpec(
        name="extract_assessment",
        handler=_extract_assessment,
        input_model=schemas.LinkRef,
        output_model=schemas.AssessmentFacts,
        description="What a candidate was actually asked and actually answered.",
        # A live conversation grows between two reads by design, so this is not
        # idempotent and must never be cached: an agent scoring a transcript
        # that is two answers stale is scoring the wrong assessment.
        idempotent=False,
        timeout_seconds=5.0,
    )
)


# ── extract_framework ────────────────────────────────────────────────────────


async def _extract_framework(
    payload: schemas.JobRef, *, session: AsyncSession | None
) -> schemas.FrameworkFacts:
    assert session is not None
    job = await session.get(Job, payload.job_id)
    if job is None:
        raise ToolExecutionError("extract_framework", f"job {payload.job_id} not found")

    rows = list(
        (
            await session.execute(
                select(JobCompetency)
                .where(
                    JobCompetency.job_id == job.id,
                    JobCompetency.is_active.is_(True),
                )
                .order_by(JobCompetency.category, JobCompetency.ordinal)
            )
        ).scalars()
    )

    return schemas.FrameworkFacts(
        job_id=job.id,
        competencies=tuple(
            schemas.Competency(
                competency_id=row.id,
                category=row.category,
                name=row.name,
                description=row.description,
                # The column is an integer and no number leaves this layer.
                required_level=rating.grade_for_percent(row.required_level)
                or rating.GRADE_MATCHING,
            )
            for row in rows
        ),
        approved=job.framework_approved_at is not None,
        # A stamp with no rows. Measured on the live database once: 19 jobs
        # across three tenants carried a generation timestamp and zero
        # competencies, and every health check that asked the STAMP reported
        # them healthy. This field asks the table.
        framework_pending=job.framework_generated_at is not None and not rows,
    )


register(
    ToolSpec(
        name="extract_framework",
        handler=_extract_framework,
        input_model=schemas.JobRef,
        output_model=schemas.FrameworkFacts,
        description="The job's saved PPI criteria, with required levels as words.",
        idempotent=True,
        # Frozen once anyone has been assessed, and edited only during setup.
        cache_ttl_seconds=300,
        timeout_seconds=3.0,
    )
)


# ── validate_output ──────────────────────────────────────────────────────────

#: Schemas an agent may validate against BY NAME. A caller that could name any
#: importable model could name anything importable, and the point of the tool
#: layer is that an agent's reach is enumerable.
VALIDATABLE: dict[str, type[BaseModel]] = {
    "JobFacts": schemas.JobFacts,
    "ResumeFacts": schemas.ResumeFacts,
    "AssessmentFacts": schemas.AssessmentFacts,
    "FrameworkFacts": schemas.FrameworkFacts,
    "Competency": schemas.Competency,
    "Exchange": schemas.Exchange,
}


async def _validate_output(payload: schemas.ValidationRequest) -> schemas.ValidationVerdict:
    model = VALIDATABLE.get(payload.schema_name)
    if model is None:
        return schemas.ValidationVerdict(
            schema_name=payload.schema_name,
            valid=False,
            errors=(f"unknown schema: {payload.schema_name}",),
        )
    try:
        model.model_validate(payload.payload)
    except ValidationError as exc:
        return schemas.ValidationVerdict(
            schema_name=payload.schema_name,
            valid=False,
            errors=tuple(
                ".".join(str(piece) for piece in error.get("loc", ()) or ("payload",))
                + ": "
                + str(error.get("msg", "invalid"))
                for error in exc.errors()[:8]
            ),
        )
    return schemas.ValidationVerdict(schema_name=payload.schema_name, valid=True)


register(
    ToolSpec(
        name="validate_output",
        handler=_validate_output,
        input_model=schemas.ValidationRequest,
        output_model=schemas.ValidationVerdict,
        description="Check a generated payload against a named schema.",
        needs_session=False,
        # Pure computation. Retrying it would produce the identical verdict.
        max_attempts=1,
        timeout_seconds=2.0,
    )
)


# ── retrieve_context ─────────────────────────────────────────────────────────


async def _retrieve_context(
    payload: schemas.RetrievalRequest, *, session: AsyncSession | None
) -> schemas.RetrievedContext:
    assert session is not None
    chunks = await rag_retrieval.retrieve(
        session,
        payload.query,
        source_type=payload.source_type,
        source_ids=list(payload.source_ids),
        section_types=list(payload.section_types) or None,
        top_k=payload.top_k,
    )
    assembled = rag_context.assemble(
        chunks, query=payload.query, max_tokens=payload.max_tokens
    )
    return schemas.RetrievedContext(
        query=payload.query,
        pieces=tuple(
            schemas.RetrievedPiece(
                chunk_id=chunk.chunk_id,
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                section_type=chunk.section_type,
                content=chunk.content,
                retrievers=chunk.retrievers,
            )
            for chunk in assembled.chunks
        ),
        text=assembled.text,
        tokens=assembled.tokens,
        dropped=assembled.dropped,
        compressed=assembled.compressed,
    )


register(
    ToolSpec(
        name="retrieve_context",
        handler=_retrieve_context,
        input_model=schemas.RetrievalRequest,
        output_model=schemas.RetrievedContext,
        description="The pieces of a scoped document that bear on a query.",
        # Two database round trips plus an embedding call to a GPU service. The
        # ceiling is generous because the alternative to slow retrieval is a
        # prompt built from nothing, and tight because a candidate is waiting.
        timeout_seconds=6.0,
        # NOT cached. The index is rewritten whenever a resume finishes parsing
        # or a JD is edited, and a five-minute-old retrieval against a document
        # that changed is evidence for a claim about text that no longer exists.
        idempotent=False,
    )
)


__all__ = ["VALIDATABLE", "pair_exchanges"]
