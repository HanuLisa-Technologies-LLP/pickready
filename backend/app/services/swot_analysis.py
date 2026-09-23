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

GENERATION IS DISPATCHED, AND THE REQUEST ONLY ASKS FOR IT (rule 4)
--------------------------------------------------------------------
`request_generation` runs in the request: it refuses over human edits and over
a JD too thin to analyse (`generation_sufficiency.swot_input_state`), BEFORE
anything is written, then moves the row to `generating` and hands
`pickready.generate_job_swot` off to run after the transaction is durable.
`run_generation` is the worker body: it calls the model WITHOUT holding a lock,
then locks the row and writes only if nobody saved over it meanwhile, so a
human save during a generation always wins. A `generating` row older than
`settings.swot_generation_stale_minutes` READS as failed (`effective_status`),
derived and never written, so a worker that never reported back cannot leave a
spinner on the tab for ever.

A SAVED SWOT IS A HUMAN ACT (`is_saved`)
-----------------------------------------
The Skills draft and the publish gate both ask whether the team has SAVED the
SWOT, and a model draft is not that. `is_saved` is derived from the row: the
content is the team's, either because the last write was theirs (`edited`), or
because a regeneration was requested or failed over content they wrote and
left it in place.

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

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.job import Job
from app.models.job_setup import (
    SWOT_ANALYSIS_EDITED,
    SWOT_ANALYSIS_FAILED,
    SWOT_ANALYSIS_GENERATED,
    SWOT_ANALYSIS_GENERATING,
    SWOT_ANALYSIS_NOT_GENERATED,
    SWOT_ANALYSIS_SECTIONS,
    JobSwotAnalysis,
)
from app.prompts import registry
from app.services import generation_sufficiency, llm_router
from app.workers.dispatch import TaskHandle, dispatch_after_commit

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


class SwotInputInsufficient(RuntimeError):
    """The job carries too little to draft a SWOT from. Carries the fixed
    `EMPTY_STATE_COPY` key and its copy, never model text."""

    def __init__(self, empty_state_key: str) -> None:
        self.empty_state_key = empty_state_key
        super().__init__(generation_sufficiency.empty_state_copy(empty_state_key))


#: The dispatched worker. Registered in `workers/tasks.py`.
GENERATE_TASK = "pickready.generate_job_swot"

#: What a reader is told about a generation that never reported back. Fixed
#: copy, like every other state sentence on this tab.
STALE_GENERATION_ERROR = "The draft did not finish. Generate it again."


# ── Reading and creating the row ────────────────────────────────────────────

async def get(
    session: AsyncSession, job: Job, *, for_update: bool = False
) -> JobSwotAnalysis | None:
    query = select(JobSwotAnalysis).where(JobSwotAnalysis.job_id == job.id)
    if for_update:
        query = query.with_for_update()
    return (
        await session.execute(query)
    ).scalar_one_or_none()


async def get_or_create(
    session: AsyncSession, job: Job, *, for_update: bool = False
) -> JobSwotAnalysis:
    """The row for this job, created empty if it does not exist yet.

    An empty row is the `not_generated` state rather than a missing resource:
    the SWOT tab exists for every job, and a reader with view access must see
    the empty state rather than a 404 that reads as "this job is broken".
    """
    existing = await get(session, job, for_update=for_update)
    if existing is not None:
        return existing
    # A missing document can be opened in two tabs at once. Serialize its
    # creation on the parent job, then check again after the lock is acquired.
    # This also makes the first save see the version that GET actually seeded.
    await session.execute(select(Job.id).where(Job.id == job.id).with_for_update())
    existing = await get(session, job, for_update=for_update)
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

    from app.services.job_candidates import grade_label  # noqa: PLC0415

    return {
        "title": _clean(job.title),
        "department": _clean(job.department),
        # The GRADE, never `jobs.level`: `level` is a pre-2026-07-28 free-text
        # field no form collects any more (Vivekium release, Phase 1). The
        # grade and the experience band below are what the job states.
        "grade": grade_label(job.assessment_grade),
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
    }


def _user_message(context: dict[str, str]) -> str:
    """The job, as labelled lines. Absent values are OMITTED rather than sent
    as an empty label: a line reading "Skills:" with nothing after it invites
    the model to fill the gap, which is the one thing section 24 forbids."""
    labels = [
        ("Job title", "title"),
        ("Department", "department"),
        ("Grade", "grade"),
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


def is_stale(row: JobSwotAnalysis, *, now: datetime | None = None) -> bool:
    """A `generating` row nobody has finished within the stale window."""
    if row.status != SWOT_ANALYSIS_GENERATING:
        return False
    requested = row.generation_requested_at
    if requested is None:
        return True
    window = timedelta(minutes=get_settings().swot_generation_stale_minutes)
    return (now or _now()) - requested > window


def effective_status(
    row: JobSwotAnalysis, *, now: datetime | None = None
) -> tuple[str, str | None]:
    """(status, error) as a reader should see them. DERIVED, never written.

    A `generating` row past the stale window reads as `failed` with fixed copy,
    so a lost dispatch or a worker killed mid-run becomes a retry button rather
    than a spinner that never stops.
    """
    if is_stale(row, now=now):
        return SWOT_ANALYSIS_FAILED, STALE_GENERATION_ERROR
    return row.status, row.generation_error


def is_saved(row: JobSwotAnalysis | None) -> bool:
    """Whether the team has SAVED this SWOT: its content is the team's.

    `edited` is the plain case. A regeneration that was requested, or that
    failed, over the team's saved content leaves that content in place, so it
    is still theirs; `last_modified_at` against `last_generated_at` on the SAME
    row says which of the two kinds of write the content came from. A
    `generated` row holds a model draft nobody has saved, which is exactly what
    the Skills draft and the publish gate must not treat as the team's SWOT.
    """
    if row is None or not row.has_content or not row.human_edited:
        return False
    if row.status == SWOT_ANALYSIS_EDITED:
        return True
    if row.status in (SWOT_ANALYSIS_FAILED, SWOT_ANALYSIS_GENERATING):
        return row.last_modified_at is not None and (
            row.last_generated_at is None
            or row.last_modified_at >= row.last_generated_at
        )
    return False


async def request_generation(
    session: AsyncSession,
    job: Job,
    *,
    confirm_overwrite: bool,
    requested_by: uuid.UUID,
) -> tuple[JobSwotAnalysis, TaskHandle | None]:
    """Ask for a SWOT draft. Refuses first, writes second, dispatches LAST.

    Order is the whole design:

    1. `HumanEditsWouldBeLost` over the team's content without confirmation,
       before anything else (section 32; a confirmation after a wait is one
       nobody reads).
    2. `SwotInputInsufficient` when the JD cannot carry a SWOT, decided in code
       before any model is involved (`generation_sufficiency.swot_input_state`).
    3. An in-flight generation that is not stale is returned as it is, with no
       second dispatch: a double click is one request.
    4. Otherwise the row moves to `generating` and the task is handed off to run
       AFTER the transaction is durable, so a rolled-back request dispatches
       nothing and the worker can never read a row that was not stored.

    A lost invoke is repaired by the read rule, not a sweep: the row reads as
    failed after the stale window and the team presses Generate again.
    """
    row = await get_or_create(session, job, for_update=True)
    if row.human_edited and row.has_content and not confirm_overwrite:
        raise HumanEditsWouldBeLost(
            "This SWOT has been edited by your team. Regenerating replaces "
            "what they wrote."
        )
    verdict = generation_sufficiency.swot_input_state(job.title, job.jd_markdown)
    if not verdict.sufficient:
        log.info(
            "swot_analysis.generation_refused job=%s reason=%s", job.id, verdict.reason
        )
        raise SwotInputInsufficient(str(verdict.empty_state_key))
    if row.status == SWOT_ANALYSIS_GENERATING and not is_stale(row):
        return row, None
    row.status = SWOT_ANALYSIS_GENERATING
    row.generation_requested_at = _now()
    row.generation_error = None
    await session.flush()
    handle = dispatch_after_commit(
        session,
        GENERATE_TASK,
        args=[str(job.id)],
        kwargs={
            "confirm_overwrite": bool(confirm_overwrite),
            "requested_by": str(requested_by),
            "requested_version": row.version,
        },
    )
    return row, handle


async def run_generation(
    session: AsyncSession,
    job: Job,
    *,
    confirm_overwrite: bool,
    requested_version: int,
) -> JobSwotAnalysis | None:
    """The worker body. Returns the written row, or None when it stood down.

    THE MODEL IS CALLED WITHOUT A ROW LOCK, then the row is locked and re-read.
    Holding `FOR UPDATE` across a model call would make a person's Save wait on
    a provider. Instead the write happens only if the row is still the one that
    was asked about: still `generating`, at the requested version. A human save
    in the meantime bumps the version and moves the status to `edited`, and the
    generation stands down without touching their words.

    Raises `SwotAnalysisError` after recording the failed state on the row; the
    caller persists that state and re-raises, so the error metric moves.
    """
    current = await get(session, job)
    if (
        current is None
        or current.status != SWOT_ANALYSIS_GENERATING
        or current.version != requested_version
    ):
        log.info(
            "swot_analysis.generation_superseded job=%s status=%s version=%s requested=%d",
            job.id,
            None if current is None else current.status,
            None if current is None else current.version,
            requested_version,
        )
        return None

    try:
        sections = await draft(session, job)
    except SwotAnalysisError as exc:
        row = await get(session, job, for_update=True)
        if (
            row is not None
            and row.status == SWOT_ANALYSIS_GENERATING
            and row.version == requested_version
        ):
            # The failed state keeps whatever was already there. A recruiter
            # whose regeneration failed still has last week's SWOT.
            row.status = SWOT_ANALYSIS_FAILED
            row.generation_error = str(exc)
            await session.flush()
        raise

    row = await get(session, job, for_update=True)
    if (
        row is None
        or row.status != SWOT_ANALYSIS_GENERATING
        or row.version != requested_version
    ):
        log.info("swot_analysis.generation_lost_race job=%s", job.id)
        return None
    if row.human_edited and row.has_content:
        if not confirm_overwrite:
            # Unreachable through the request, which refuses first. Kept as a
            # second guard because this is the write that would destroy the
            # team's words, and a guard at the request alone is one the next
            # caller of this function does not know about.
            row.status = SWOT_ANALYSIS_FAILED
            row.generation_error = (
                "This SWOT has been edited by your team. Regenerating replaces "
                "what they wrote."
            )
            await session.flush()
            return None
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
    row = await get_or_create(session, job, for_update=True)
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
    row = await get_or_create(session, job, for_update=True)
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
