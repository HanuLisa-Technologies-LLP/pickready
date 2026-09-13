"""The AI-assisted Job SWOT Analysis (2026-09-13 spec, sections 23 to 33).

    Job created -> JD available -> AI drafts a SWOT -> the recruitment team
    reviews it -> an authorized user edits it -> the final SWOT is theirs.

THE DRAFT IS A DRAFT (section 31)
----------------------------------
The model writes first and is wrong about something roughly every time: it has
the job description and nothing about the market the company is actually
hiring in. That is the intended shape of the feature, not a shortcoming of it.
So every path here is built around the human keeping the last word:

  * `human_edited` latches True the first time a person saves, and nothing
    ever clears it;
  * a regeneration over a human-edited document is REFUSED unless the caller
    says explicitly that it should be replaced (section 32);
  * a confirmed replacement snapshots the four sections first, so the one
    destructive action in the feature can be undone;
  * `version` increments on every write, and a save carrying a stale version
    is refused rather than silently winning a race (section 38's concurrent
    editing).

WHAT THE MODEL IS GIVEN, AND WHAT IT IS NOT
---------------------------------------------
Only what the platform already holds about this job: title, department,
seniority, the JD document, the derived skills and responsibilities, the
experience band, the company's own About / Work Life / Benefits text, and the
reporting authority's SWOT intake points when an intake exists. Section 24 is
explicit that the generator must not invent company-specific facts, so the
prompt is given the material and told to say what the team should establish
where the material is thin.

FAILURE IS A STATE, NOT AN EXCEPTION THAT REACHES THE USER AS A BLANK PAGE
---------------------------------------------------------------------------
claude.md rule 6 forbids a silent fallback, and a template SWOT presented as
generated output would be exactly that. So a failed generation writes
`status=failed` with the reason and LEAVES ANY EXISTING CONTENT ALONE
(section 30: "Preserve previously saved content where applicable"). The UI
renders the reason and the retry; it never renders an invented SWOT.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.job_setup import (
    SWOT_ANALYSIS_EDITED,
    SWOT_ANALYSIS_FAILED,
    SWOT_ANALYSIS_GENERATED,
    SWOT_ANALYSIS_NOT_GENERATED,
    SWOT_ANALYSIS_SECTIONS,
    JobSwotAnalysis,
    JobSwotIntake,
)
from app.prompts import registry
from app.services import llm_router

log = logging.getLogger(__name__)

TASK_TYPE = "swot_analysis"

#: One section may not exceed this. A model that ignores "two to four
#: sentences" produces an essay nobody edits, and a column with no ceiling is
#: a denial-of-service on the editor.
MAX_SECTION_CHARS = 4000


class SwotAnalysisError(RuntimeError):
    """Generation did not produce a usable document. Carries the sentence the
    UI shows, which is why it is one class rather than three."""


class HumanEditsWouldBeLost(RuntimeError):
    """A regeneration was asked for over content a human has edited, without
    the explicit confirmation section 32 requires."""


class VersionConflict(RuntimeError):
    """The document moved under an editor who was holding an older version."""


# ── Reading and creating the row ────────────────────────────────────────────

async def get(session: AsyncSession, job: Job) -> JobSwotAnalysis | None:
    return (
        await session.execute(
            select(JobSwotAnalysis).where(JobSwotAnalysis.job_id == job.id)
        )
    ).scalar_one_or_none()


async def get_or_create(session: AsyncSession, job: Job) -> JobSwotAnalysis:
    """The row for this job, created empty if it does not exist yet.

    An empty row is the `not_generated` state rather than a missing resource:
    the SWOT tab exists for every job, and a reader with view access must see
    the empty state rather than a 404 that reads as "this job is broken".
    """
    existing = await get(session, job)
    if existing is not None:
        return existing
    row = JobSwotAnalysis(
        tenant_id=job.tenant_id,
        job_id=job.id,
        status=SWOT_ANALYSIS_NOT_GENERATED,
    )
    session.add(row)
    await session.flush()
    return row


# ── The generator's inputs ──────────────────────────────────────────────────

def _clean(value: object) -> str:
    return str(value or "").strip()


def _listed(values: object, limit: int = 20) -> str:
    if not isinstance(values, (list, tuple)):
        return ""
    items = [_clean(v) for v in values if _clean(v)]
    return ", ".join(items[:limit])


async def build_context(session: AsyncSession, job: Job) -> dict[str, str]:
    """Everything the platform already knows about this job, as flat text.

    Flat, and assembled here rather than in the prompt, so the one place that
    decides what the model may see is a function a reviewer can read. Section
    37's rule that the client never supplies authorization state has a quieter
    sibling here: the client never supplies GENERATION INPUT either. Every
    value below is read from the row.
    """
    jd = dict(job.jd_json or {})
    experience = ""
    if job.experience_min_years is not None and job.experience_max_years is not None:
        experience = f"{job.experience_min_years} to {job.experience_max_years} years"
    elif jd.get("experience_years") is not None:
        experience = f"{jd.get('experience_years')} years"

    intake = (
        await session.execute(
            select(JobSwotIntake).where(JobSwotIntake.job_id == job.id)
        )
    ).scalar_one_or_none()
    intake_points = ""
    if intake is not None:
        captured = {
            area: _listed(getattr(intake, area, None), limit=8)
            for area in SWOT_ANALYSIS_SECTIONS
        }
        intake_points = "\n".join(
            f"{area.capitalize()} the reporting authority named: {text}"
            for area, text in captured.items()
            if text
        )

    return {
        "title": _clean(job.title),
        "department": _clean(job.department),
        "level": _clean(job.level) or _clean(job.assessment_grade),
        "role_classification": _clean(job.role_classification),
        "experience": experience,
        "role_summary": _clean(jd.get("role")),
        "responsibilities": _listed(jd.get("responsibilities")),
        "skills": _listed(jd.get("skills")),
        "education": _clean(jd.get("education")),
        "jd_document": _clean(job.jd_markdown)[:6000],
        "about_company": _clean(job.about_company),
        "work_life": _clean(job.work_life),
        "benefits": _clean(job.benefits),
        "intake_points": intake_points,
    }


def _user_message(context: dict[str, str]) -> str:
    """The job, as labelled lines. Absent values are OMITTED rather than sent
    as an empty label: a line reading "Skills:" with nothing after it invites
    the model to fill the gap, which is the one thing section 24 forbids."""
    labels = [
        ("Job title", "title"),
        ("Department", "department"),
        ("Seniority", "level"),
        ("Role type", "role_classification"),
        ("Experience required", "experience"),
        ("Role summary", "role_summary"),
        ("Responsibilities", "responsibilities"),
        ("Required skills", "skills"),
        ("Education", "education"),
        ("About the company", "about_company"),
        ("Work life", "work_life"),
        ("Benefits", "benefits"),
        ("Job description", "jd_document"),
        ("From the hiring manager's SWOT intake", "intake_points"),
    ]
    lines = [f"{label}: {context[key]}" for label, key in labels if context.get(key)]
    return "\n".join(lines)


def _parse(raw: str) -> dict[str, str] | None:
    """The four sections, or None if the response was not usable.

    A response missing a section is unusable rather than partially accepted:
    section 30's "Partially generated SWOT" edge case is answered by never
    creating one, so the states stay the four the UI knows how to render.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    sections: dict[str, str] = {}
    for name in SWOT_ANALYSIS_SECTIONS:
        value = _clean(parsed.get(name))
        if not value:
            return None
        sections[name] = value[:MAX_SECTION_CHARS]
    return sections


async def draft(session: AsyncSession, job: Job) -> dict[str, str]:
    """Ask the model for the four sections. Raises `SwotAnalysisError`.

    ONE CORRECTIVE RETRY, THEN A REFUSAL. `jd_generation` answers an
    unparseable response with a deterministic template because a job cannot be
    created without a JD. Nothing here is blocked by the absence of a SWOT, so
    the honest answer is the failed state and the reason: a template SWOT
    would be a silent fallback presented as generation (claude.md rule 6).
    """
    context = await build_context(session, job)
    messages = [
        {"role": "system", "content": registry.render("swot_analysis_system")},
        {"role": "user", "content": _user_message(context)},
    ]

    try:
        raw = await llm_router.chat_completion(
            TASK_TYPE, messages, response_format_json=True, session=session
        )
    except llm_router.LLMUnavailableError as exc:
        log.warning("swot_analysis.llm_unavailable job=%s", job.id)
        raise SwotAnalysisError(
            "The SWOT writer could not be reached. Try again in a moment."
        ) from exc

    sections = _parse(raw)
    if sections is not None:
        return sections

    corrective = (
        "Your previous response was not valid JSON in the required shape. "
        "Re-emit ONLY a JSON object with exactly these four keys: strengths, "
        "weaknesses, opportunities, threats. Each value is prose. No markdown."
    )
    try:
        retry = await llm_router.chat_completion(
            TASK_TYPE,
            messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": corrective},
            ],
            response_format_json=True,
            session=session,
        )
    except llm_router.LLMUnavailableError as exc:
        log.warning("swot_analysis.llm_unavailable_on_retry job=%s", job.id)
        raise SwotAnalysisError(
            "The SWOT writer could not be reached. Try again in a moment."
        ) from exc

    sections = _parse(retry)
    if sections is None:
        log.warning("swot_analysis.unparseable_after_retry job=%s", job.id)
        raise SwotAnalysisError(
            "The SWOT writer returned something unusable. Try again, or write "
            "the analysis yourself."
        )
    return sections


# ── The two writes ──────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def generate(
    session: AsyncSession,
    job: Job,
    *,
    confirm_overwrite: bool = False,
) -> JobSwotAnalysis:
    """Draft the SWOT and store it. Raises before touching a human's work.

    The refusal comes FIRST, before the model is called, for two reasons: a
    regeneration the caller is not allowed to complete should not spend a
    model call, and a confirmation prompt that appears after a thirty-second
    wait is a confirmation nobody reads.
    """
    row = await get_or_create(session, job)
    if row.human_edited and row.has_content and not confirm_overwrite:
        raise HumanEditsWouldBeLost(
            "This SWOT has been edited by your team. Regenerating replaces "
            "what they wrote."
        )

    try:
        sections = await draft(session, job)
    except SwotAnalysisError as exc:
        # The failed state keeps whatever was already there. A recruiter whose
        # regeneration failed still has last week's SWOT.
        row.status = SWOT_ANALYSIS_FAILED
        row.generation_error = str(exc)
        await session.flush()
        raise

    if row.human_edited and row.has_content:
        row.previous_json = {
            **row.sections(),
            "replaced_at": _now().isoformat(),
            "version": row.version,
        }

    for name, value in sections.items():
        setattr(row, name, value)
    row.status = SWOT_ANALYSIS_GENERATED
    row.generated_by = "ai"
    row.last_generated_at = _now()
    row.generation_error = None
    row.version += 1
    # `human_edited` is NOT cleared. It is a fact about the document's history,
    # and a team that edited once will edit again; the UI uses it to keep
    # showing the confirmation on the next regeneration.
    await session.flush()
    return row


async def save(
    session: AsyncSession,
    job: Job,
    sections: dict[str, str],
    *,
    editor_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> JobSwotAnalysis:
    """Persist a human's four sections. Raises `VersionConflict` on a stale save."""
    row = await get_or_create(session, job)
    if expected_version is not None and expected_version != row.version:
        raise VersionConflict(
            "Someone else saved this SWOT while you were editing. Reload to "
            "see their version before saving yours."
        )

    for name in SWOT_ANALYSIS_SECTIONS:
        value = _clean(sections.get(name))[:MAX_SECTION_CHARS]
        setattr(row, name, value or None)

    row.human_edited = True
    row.status = SWOT_ANALYSIS_EDITED
    row.last_modified_by = editor_id
    row.last_modified_at = _now()
    row.generation_error = None
    row.version += 1
    await session.flush()
    return row


async def restore_previous(session: AsyncSession, job: Job) -> JobSwotAnalysis:
    """Put back the human content the last confirmed regeneration replaced."""
    row = await get_or_create(session, job)
    snapshot = dict(row.previous_json or {})
    if not any(_clean(snapshot.get(name)) for name in SWOT_ANALYSIS_SECTIONS):
        raise SwotAnalysisError("There is no earlier version to restore.")
    for name in SWOT_ANALYSIS_SECTIONS:
        setattr(row, name, _clean(snapshot.get(name)) or None)
    row.status = SWOT_ANALYSIS_EDITED
    row.previous_json = {}
    row.version += 1
    row.last_modified_at = _now()
    await session.flush()
    return row
