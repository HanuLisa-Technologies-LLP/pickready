"""Job creation + multi-level approval FSM endpoints (FR-3.x, FR-4.1)."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, UploadFile, status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_public_db,
    get_tenant_db,
    require_capability,
)
from app.core import cache
from app.core.config import get_settings
from app.models.candidate import (
    SOURCE_TYPE_DATABANK,
    Candidate,
    JobCandidateLink,
    Profile,
)
from app.models.billing import (
    CONSUMPTION_SUBUNITS,
    EVENT_OLD_PROFILE_REVIEW,
    OldProfileReview,
)
from app.models.company import Company
from app.models.enums import ApprovalDecision, JobStatus, LinkSource
from app.models.job import REPORTING_TO_OPTIONS, Job, JobApproval
from app.models.proctoring import DEFAULT_WARNING_POLICY
from app.models.tenant import Tenant
from app.schemas.jobs import (
    ApprovalOut,
    ApproveIn,
    CompensationIn,
    DatabankUploadOut,
    DatabankUploadResultOut,
    JDGenerateIn,
    JDGenerateOut,
    JDMarkdownIn,
    JDUpdateIn,
    JobCreateIn,
    JobDetailOut,
    JobOut,
    JobPatchIn,
    PublicJobOut,
    PublishJobOut,
    RankedCandidateOut,
    RankedCandidatesOut,
    ReportingToOptionsOut,
    ReviewProfileOut,
)
from app.schemas.matching import RunMatchingOut
from uuid import uuid4 as _uuid4

from app.services import approval_fsm as fsm
from app.services import capabilities as caps
from app.services import credits
from app.services import job_candidates
from app.services import job_posting
from app.services import hiring_pipeline
from app.services import rbac
from app.services import telemetry_events
from app.services.audit import audit
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _invalidate_public_job(job_id: uuid.UUID) -> None:
    """Drop the cached public application page for one job.

    Called from every path that changes what an applicant would read: the JD
    edits, publish, archive/restore and renew. The author must see their own
    change immediately; everyone else is bounded by the 10-minute TTL.

    Editing the COMPANY profile also changes what a job with NULL sections
    renders (they read through to the live profile). That is deliberately left
    to the TTL rather than fanned out over every job in the tenant: a company
    profile edit is rare, and invalidating hundreds of keys inside a request
    would trade a slow read for a slow write.
    """
    await cache.invalidate(cache.key("public-job", job_id))


router = APIRouter()

#: Hard ceiling on one Upload Candidate Data Bank request (client spec:
#: "At once 25 resumes/Candidates can be uploaded"). A 26th file is refused
#: outright rather than silently truncated, because a recruiter who drags 40
#: files and gets a cheerful 200 will never notice the 15 that vanished.
MAX_DATABANK_FILES = 25


def public_job_url(job_id: uuid.UUID) -> str:
    """The public application link (FR-3.4): readypick.ai/apply/{job_uuid}.

    The base comes from settings.frontend_url (env-driven — set it to
    https://readypick.ai in production; it defaults to http://localhost:3000 in
    dev). The frontend serves the public application page at /apply/{job_uuid}
    (a bare root catch-all was deliberately avoided), so the link points there."""
    base = get_settings().frontend_url.rstrip("/")
    return f"{base}/apply/{job_id}"


def _apply_posting_window(out: JobOut, job: Job) -> JobOut:
    """Attach the read-time posting lifecycle (spec §2.1).

    `posting_status` is computed on every read rather than stored, because it
    depends on `now()` — a cached value is wrong the moment the window rolls
    over. See services/job_posting for the rule and migration 0018 for why it
    cannot be a generated column.
    """
    window = job_posting.describe(job)
    out.posting_start_date = window.posting_start_date
    out.posting_end_date = window.posting_end_date
    out.grace_period_end_date = window.grace_period_end_date
    out.posting_status = window.posting_status
    out.days_until_posting_ends = window.days_until_posting_ends
    out.days_until_grace_ends = window.days_until_grace_ends
    out.posting_summary = window.summary()
    return out


def _attach_public_url(out: JobOut, job: Job) -> JobOut:
    """Set BOTH public-link fields from the one builder.

    `public_url` is the established name; `public_application_url` is the name
    the 2026-07-28 spec asks for and the one the copy-link popup reads. They
    are always the same string, computed once, so they cannot drift.
    """
    if job.ratified_at is not None and job.archived_at is None:
        link = public_job_url(job.id)
        out.public_url = link
        out.public_application_url = link
    return out


def jd_markdown_for(job: Job) -> str:
    """The canonical JD document for one job, never empty for a real JD.

    `jd_markdown` is authoritative. A job created before migration 0022 has
    none, so the document is RENDERED from its per-section `jd_json` on read
    rather than showing the candidate a blank page. The rendering is not
    written back: the recruiter's first explicit save is what makes it real.
    """
    from app.services import jd_generation

    stored = (job.jd_markdown or "").strip()
    if stored:
        return stored
    return jd_generation.render_jd_markdown(
        job.jd_json or {},
        min_years=job.experience_min_years,
        max_years=job.experience_max_years,
    ).strip()


def _has_publishable_jd(job: Job) -> bool:
    """Is there any real job description to publish?

    ASSUMPTION (2026-07-28, claude.md §8): the client's rule is "no publishing
    an empty JD". Read literally as "jd_markdown must be non-empty" it would
    also refuse every job created through the still-supported per-section
    contract, which writes `jd_json` and no document. So the gate is on the
    RESOLVED document: an explicit `jd_markdown`, or one renderable from
    `jd_json`. A job with neither has nothing to show a candidate and is
    refused, which is the case the client actually cares about.
    """
    stored = (job.jd_markdown or "").strip()
    if stored:
        # Headings alone are not a job description. Strip them and see whether
        # anything was actually said underneath.
        body = "\n".join(
            line for line in stored.splitlines() if not line.lstrip().startswith("#")
        )
        return bool(body.strip())

    # No document yet: fall back to the per-section contract. Checked SECTION BY
    # SECTION rather than by rendering, because the rendering supplies its own
    # boilerplate (an "experience is set by the hiring team" line for a job with
    # no band) and an emptiness test must not be satisfied by filler this code
    # wrote itself.
    sections = job.jd_json or {}
    return any(
        str(sections.get(key) or "").strip() if not isinstance(sections.get(key), list)
        else any(str(item).strip() for item in sections.get(key) or [])
        for key in (
            "description", "role", "responsibilities", "accountabilities",
            "education", "skills",
        )
    )


def _with_public_url(job: Job) -> JobOut:
    """JobOut carrying the public link when (and only when) the job is
    published (ratified_at set), plus its posting window."""
    out = JobOut.model_validate(job)
    out.jd_markdown = jd_markdown_for(job) or None
    _attach_public_url(out, job)
    return _apply_posting_window(out, job)


async def _approval_config(session: AsyncSession, tenant_id: uuid.UUID) -> dict | None:
    company = (
        await session.execute(select(Company).where(Company.tenant_id == tenant_id))
    ).scalars().first()
    return company.approval_levels_config if company else None


# ── Company-narrative JD sections (spec §3.1/§3.2) ───────────────────────────
# About Company / Work Life / Benefits live in two layers:
#   * the COMPANY PROFILE, edited once on Company Portal -> Profile, and
#   * a PER-JOB override, edited in place on the job page.
# A job snapshots the company values at creation. Editing them on the job sets
# the override and never writes back to the company; editing the company later
# reaches future jobs only. A job created before migration 0016 has all three
# NULL and therefore reads through to the live company profile — that read-
# through is what stops the upgrade blanking existing JDs.

_JD_SECTIONS: tuple[str, ...] = ("about_company", "work_life", "benefits")


async def _company_sections(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, str | None]:
    """The tenant's company-wide section defaults."""
    company = (
        await session.execute(select(Company).where(Company.tenant_id == tenant_id))
    ).scalars().first()
    if company is None:
        return {key: None for key in _JD_SECTIONS}
    return {
        "about_company": company.about_company,
        "work_life": company.work_life,
        # `benefits_text` is the Profile field; the legacy `benefits` column on
        # companies belongs to the Company Profile record.
        "benefits": company.benefits_text,
    }


def resolve_jd_sections(
    job: Job, company_sections: dict[str, str | None]
) -> tuple[dict[str, str | None], list[str]]:
    """Resolve the three sections for one job.

    Returns (resolved values, names the JOB overrides). A section is treated as
    overridden only when the job holds a non-empty value — a job storing "" is
    an override to *nothing*, which is not the same as never having set one,
    but rendering an empty heading helps nobody, so it reads through too.

    Pure and side-effect free; unit-tested in tests/test_jobs.py.
    """
    resolved: dict[str, str | None] = {}
    overridden: list[str] = []
    for key in _JD_SECTIONS:
        own = getattr(job, key, None)
        if own is not None and str(own).strip():
            resolved[key] = own
            overridden.append(key)
        else:
            resolved[key] = company_sections.get(key)
    return resolved, overridden


async def _job_detail_out(session: AsyncSession, job: Job) -> JobDetailOut:
    """JobDetailOut with the public URL and the resolved narrative sections."""
    out = JobDetailOut.model_validate(job)
    out.jd_markdown = jd_markdown_for(job) or None
    _attach_public_url(out, job)
    _apply_posting_window(out, job)
    resolved, overridden = resolve_jd_sections(
        job, await _company_sections(session, job.tenant_id)
    )
    out.about_company = resolved["about_company"]
    out.work_life = resolved["work_life"]
    out.benefits = resolved["benefits"]
    out.overridden_sections = overridden
    return out


async def _can_see_pre_ratified(session: AsyncSession, user: CurrentUser) -> bool:
    """# ASSUMPTION: visibility of pre-ratified jobs is capability-derived,
    not role-derived (claude.md rule 3): actors who create jobs or sit in the
    approval chain see the whole lifecycle; everyone else (HR/Recruiter) sees
    a job only once ratified (FR-3.4)."""
    return (
        await rbac.has_capability(session, user.tenant_id, user.role, caps.CREATE_JOB)
        or await rbac.has_capability(session, user.tenant_id, user.role, caps.APPROVE_JOB)
        or await rbac.has_capability(session, user.tenant_id, user.role, caps.CONFIGURE_APPROVAL_LEVELS)
    )


async def _get_visible_job(
    session: AsyncSession, user: CurrentUser, job_id: uuid.UUID
) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:  # defense in depth; RLS is the boundary
        raise HTTPException(status_code=404, detail="Job not found")
    # Terminal/HR-visibility marker is ratified_at, NOT the status value: a job
    # pending at an active "ratified" level also carries status ratified.
    if job.ratified_at is None and not await _can_see_pre_ratified(session, user):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Create a job.

    Flat staff model (PRD v1.0 §4): any of the 3 staff roles (or the Company
    Admin) holding CREATE_JOB may create one, and the approval chain is
    bypassed entirely.

    TWO FLOWS, ONE HANDLER (2026-07-28). `publish` defaults to True, which is
    the established behaviour: the job goes live immediately and the public
    application link comes back with it. The new Create Job screen sends
    `publish: false` instead, because the client's flow is AI draft, then
    recruiter edit, THEN publish. That draft is finished with
    PATCH /jobs/{id}/jd and made live with POST /jobs/{id}/publish.
    """
    # ── The credit gate on job creation (spec §11) ──────────────────────────
    # Checked at the MOMENT of creation and nowhere else. A job created while
    # the pool had credit stays created if the pool later empties; what is
    # blocked is starting something new with nothing to pay for it.
    #
    # Loud and immediate, with the way out named in the message. Spec §11 is
    # explicit that there is no silent failure and no degraded mode here: a
    # recruiter who cannot create a job must be told why and what to do, not
    # handed a draft that quietly never becomes anything.
    if not await credits.has_positive_balance(session, user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "Your credit pool is exhausted, so new jobs cannot be created. "
                "Purchase a credit bundle to continue creating jobs and "
                "assessing candidates."
            ),
        )

    # Snapshot the company's narrative sections onto the job (spec §3.2). An
    # explicit value in the create body wins; otherwise the company profile
    # seeds it. Snapshotting — rather than always reading through — is what
    # lets a later company-profile edit leave published JDs alone.
    company_sections = await _company_sections(session, user.tenant_id)
    seeded = {
        key: (
            getattr(body, key)
            if getattr(body, key, None) is not None
            else company_sections.get(key)
        )
        for key in _JD_SECTIONS
    }
    from app.services import jd_generation

    jd_sections = body.jd.model_dump(mode="json")
    document = (body.jd_markdown or "").strip()
    if document:
        # The document is canonical: re-derive the sections from it so
        # `jd_json.skills` can never contradict what the candidate reads.
        document = jd_generation.strip_em_dashes(document)
        jd_sections = jd_generation.parse_jd_markdown(document)
    else:
        # Per-section create (the pre-2026-07-28 contract, still supported):
        # render the document from the sections so the job still has one.
        document = jd_generation.render_jd_markdown(
            jd_sections,
            min_years=body.experience_min_years,
            max_years=body.experience_max_years,
        )

    # ── STEM / Non-STEM classification (Master Directive Part 3) ────────────
    # The AI flow passes `jd_draft_id`, and the job inherits the result that
    # was locked to the RAW AI-generated JD at generation time — the recruiter
    # edits between generate and create change nothing (Rule 3). The manual
    # path (a typed JD, no draft) is classified here, once, on the document as
    # first submitted; later PATCH edits never re-trigger it.
    from app.models.job import JDDraft
    from app.services import stem_classification

    draft = None
    if body.jd_draft_id is not None:
        draft = await session.get(JDDraft, body.jd_draft_id)
        if draft is not None and draft.tenant_id != user.tenant_id:
            draft = None  # RLS already blocks this read; keep the guard anyway
    if draft is not None:
        role_classification = draft.role_classification
        classification_confidence = draft.classification_confidence
        classification_signals = list(draft.classification_signals or [])
        classification_tentative = bool(draft.tentative or draft.engine_error)
        raw_jd_text = draft.raw_jd_text
    else:
        classified = stem_classification.classify_safe(document, body.title)
        role_classification = classified.classification
        classification_confidence = classified.confidence
        classification_signals = classified.signals
        classification_tentative = bool(classified.tentative or classified.engine_error)
        raw_jd_text = document or None

    job = Job(
        tenant_id=user.tenant_id,
        title=body.title,
        department=body.department,
        level=body.level,
        requirement_period=body.requirement_period,
        jd_json=jd_sections,
        jd_markdown=document,
        role_classification=role_classification,
        classification_confidence=classification_confidence,
        classification_signals=classification_signals,
        classification_tentative=classification_tentative,
        credit_cost_per_report=stem_classification.credit_cost(role_classification),
        raw_jd_text=raw_jd_text,
        experience_min_years=body.experience_min_years,
        experience_max_years=body.experience_max_years,
        status=JobStatus.draft,
        created_by=user.user_id,
        about_company=seeded["about_company"],
        work_life=seeded["work_life"],
        benefits=seeded["benefits"],
        # `grade` is a required Create Job field; assessment_grade is its
        # canonical store. Questions generate + finalize asynchronously — there
        # is no manual approval gate (user decision, 2026-07-25).
        assessment_grade=body.grade,
        assessment_status="questions_pending_review",
        # The recruiter's third-warning choice (proctoring spec 6). Omitted
        # means continue-and-note: never terminate by default.
        proctoring_warning_policy=body.proctoring_warning_policy or DEFAULT_WARNING_POLICY,
        # RBAC 17's first state, written rather than left to the column default.
        # A row whose lifecycle_state is NULL is refused every state-gated
        # capability by `rbac._state_rules`, which is the safe direction and
        # also an unhelpful one for a job that was just created correctly.
        lifecycle_state=hiring_pipeline.JobLifecycleState.DRAFT.value,
        # spec-doc6 4.1: ONE correlation id, issued at job creation, traceable
        # through Bodha, Sutra, Yukti, Vaada, Miti and Siddhi, and present on
        # every audit row and log line for that flow. Issued here because this
        # is where the flow starts; nothing rewrites it afterwards.
        correlation_id=f"job-{_uuid4().hex}",
    )
    session.add(job)
    await session.flush()

    # Master Directive Part 2 section 5.1: EV_REQ_CREATED, emitted the moment
    # the requisition exists. Never allowed to fail the create (emit swallows).
    await telemetry_events.emit(
        session,
        tenant_id=user.tenant_id,
        event_code=telemetry_events.EV_REQ_CREATED,
        job_id=job.id,
        actor_user_id=user.user_id,
        correlation_id=job.correlation_id,
        payload={
            "title": body.title,
            "department": body.department,
            "grade": body.grade,
            "role_classification": role_classification,
        },
    )

    if body.publish:
        # Direct publish: draft → ratified in one step (no submit/approve chain).
        await fsm.apply_direct_publish(session, job)
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_created", target_type="job", target_id=job.id,
                metadata={"title": body.title, "published": body.publish,
                          "grade": body.grade,
                          "public_url": public_job_url(job.id) if body.publish else None})
    if body.publish:
        # Databank matching runs the moment a job is published (FR-4.2), async.
        celery_app.send_task("pickready.run_matching", args=[str(job.id)])
    # The PPI framework is generated from the JD as soon as the JD exists; it
    # does not wait for publish, so an unpublished draft is never the thing
    # holding up the assessment.
    #
    # This enqueue is now BACKED UP by `pickready.reconcile_job_setup` on the
    # beat schedule. It used to be the only attempt the job ever got, and when
    # it failed -- a broker hiccup, an exhausted retry budget, an exception in
    # the technical-bank half that used to share this task -- the job was
    # silently unusable forever. Nineteen live jobs were in exactly that state.
    # NOT ENQUEUED HERE ANY MORE (2026-08-29). Sutra compiles the Tatva matrix
    # from Bodha's completed SWOT session and the client's compiled Company DNA;
    # at job creation neither exists, so a task fired here would refuse on every
    # job the moment it ran. The compile is enqueued by the SWOT session's own
    # completion (`api/assessments.respond_swot_intake`), which is the event
    # that actually produces its input, and `pickready.reconcile_job_setup`
    # sweeps for a job whose session finished and whose matrix never landed.
    # The two halves of job setup are generated IN PARALLEL (spec §10): the PPI
    # matrix from the JD and the SWOT intake, the Matching category list from
    # the JD. Two tasks rather than one, and that split is not stylistic. A
    # single task that generated both would take the gating half down with any
    # failure in the other, which is the exact coupling that left nineteen live
    # jobs with a stamped timestamp and no framework.
    celery_app.send_task("pickready.generate_matching_categories", args=[str(job.id)])
    # Load the GENERATED posting-window columns before serialising. The mapper
    # asks for them via RETURNING (models/job.eager_defaults), but a direct
    # publish re-stamps `posting_start_date` after the INSERT, which expires
    # both derived columns again; reading them from the serialiser would then
    # trigger a lazy load in the wrong greenlet and 500 the whole request.
    await session.refresh(job)
    return _with_public_url(job)


# ── Create Job form data + the unified JD document (2026-07-28) ──────────────

@router.get("/reporting-to-options", response_model=ReportingToOptionsOut)
async def reporting_to_options(
    _user: CurrentUser = Depends(get_current_user),
) -> ReportingToOptionsOut:
    """The "Reporting to" dropdown.

    A stable ordered list plus the "Others" escape hatch, served from the API
    so the UI does not carry a second copy that drifts. The STORED value stays
    a free string: whatever the recruiter picked, or whatever they typed under
    Others. A company with an unusual reporting title is never forced into
    somebody else's taxonomy.

    Declared BEFORE `/{job_id}` so the literal path is matched first; FastAPI
    resolves routes in declaration order and `reporting-to-options` would
    otherwise be parsed as a job UUID and 422.
    """
    return ReportingToOptionsOut(options=list(REPORTING_TO_OPTIONS))


@router.patch("/{job_id}/jd", response_model=JobDetailOut)
async def save_jd_markdown(
    job_id: uuid.UUID,
    body: JDMarkdownIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_JOB_DESCRIPTION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """Save an edit of the unified JD document.

    Available at ANY time, before or after publish. The client asked for an
    explicit, always-visible Edit button: an AI draft has to be editable
    before it goes live, and a live posting with a typo should be fixable
    without unpublishing the role. The edit is audited every time, so the
    document's history is recoverable even though only the latest text is
    stored.

    The document is canonical, so `jd_json` is re-derived from it here. That is
    what keeps the matching pipeline, the technical question generator and the
    public apply page reading the same words the candidate reads.
    """
    from app.services import jd_generation

    job = await _get_visible_job(session, user, job_id)
    document = jd_generation.strip_em_dashes(body.jd_markdown.strip())
    previous_length = len((job.jd_markdown or "").strip())

    job.jd_markdown = document
    job.jd_json = jd_generation.parse_jd_markdown(document)
    await session.flush()
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="job_jd_document_edited",
        target_type="job",
        target_id=job.id,
        metadata={
            "published": job.ratified_at is not None,
            "previous_length": previous_length,
            "new_length": len(document),
        },
    )
    await _invalidate_public_job(job.id)
    return await _job_detail_out(session, job)


@router.post("/{job_id}/publish", response_model=PublishJobOut)
async def publish_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(rbac.require_authorized(caps.PUBLISH_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> PublishJobOut:
    """Publish a drafted job and hand back its public application link.

    This is the separate, explicit step the client asked for: draft, edit, then
    publish. Two things it refuses:

      * an EMPTY job description. Publishing a role with nothing to read wastes
        every candidate who clicks the link, so it is a 409 naming the fix.
      * publishing twice. Already-live is a 409 rather than a silent re-stamp,
        because re-stamping `posting_start_date` would quietly restart the
        fixed 30-day window and extend a posting nobody agreed to extend.

    The response carries `public_application_url`, the absolute link the copy
    popup shows for pasting into LinkedIn, Naukri or an email.
    """
    job = await _get_visible_job(session, user, job_id)
    if job.archived_at is not None:
        raise HTTPException(
            status_code=409, detail="Restore this job before publishing it."
        )
    if job.ratified_at is not None:
        raise HTTPException(status_code=409, detail="This job is already published.")
    if not _has_publishable_jd(job):
        raise HTTPException(
            status_code=409,
            detail=(
                "Write the job description before publishing. "
                "Generate a draft or type one, save it, then publish."
            ),
        )
    # ── RBAC 21: publication is blocked while any Hiring-Manager-controlled
    #    component is incomplete ───────────────────────────────────────────
    #
    # TWO CHECKS, AND BOTH ARE LOAD-BEARING. `rbac.require_authorized` above has
    # already refused any state earlier than FINALIZED, which is the STRUCTURAL
    # half: FINALIZED is reachable only through the Hiring Manager's explicit
    # finalisation, so the state is the record that the components are complete.
    #
    # This second check asks the TABLE. The lesson is on record and was
    # expensive: 19 live jobs carried `framework_generated_at` and had zero
    # competency rows, and every health check in the product asked the stamp
    # rather than the table. A lifecycle_state is a stamp like any other.
    _blocked = await _publication_blocked(session, job)
    if _blocked:
        raise HTTPException(status_code=409, detail=_blocked)

    await fsm.apply_direct_publish(session, job)
    # RBAC 17: FINALIZED -> PUBLISHED. Written here rather than inferred from
    # `ratified_at`, because 21 requires publication to RECORD the publishing
    # user, the timestamp, the published version and the job identifier, and a
    # derived state records none of them.
    job.lifecycle_state = hiring_pipeline.JobLifecycleState.PUBLISHED.value
    await session.flush()
    # Publishing moves `posting_start_date`, which regenerates the two derived
    # window columns in the database. Same refresh as `renew_job` below.
    await session.refresh(job)
    await _invalidate_public_job(job.id)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="job_published",
        target_type="job",
        target_id=job.id,
        metadata={
            "title": job.title,
            "public_url": public_job_url(job.id),
            # RBAC 21's four required facts. The publishing user and the
            # timestamp are the audit row's own columns.
            "published_version": job.criteria_version,
            "job_identifier": str(job.id),
        },
    )
    celery_app.send_task("pickready.run_matching", args=[str(job.id)])

    out = PublishJobOut.model_validate(job)
    out.jd_markdown = jd_markdown_for(job) or None
    _attach_public_url(out, job)
    _apply_posting_window(out, job)
    # `_attach_public_url` only fills the link for a live, unarchived job, which
    # is exactly what we just made this one, so it is never blank here.
    out.public_application_url = out.public_application_url or public_job_url(job.id)
    return out


async def _publication_blocked(session: AsyncSession, job: Job) -> str | None:
    """RBAC 21's precondition, asked of the TABLE. None when nothing blocks.

    21 lists what must be finalised before a job may be published: the final JD,
    Must-Have skills, Nice-to-Have skills, behavioural competencies, the job-role
    philosophy, the SWOT analysis and the evaluation rubrics. In this product
    those are three things a reader can check: the SWOT session closed, the
    Tatva matrix exists with all three aspects, and the Hiring Manager froze it.

    The message NAMES the outstanding step, because "publication blocked" sends
    a recruiter to ask someone else what is wrong.
    """
    from app.services.hiring import scorecard  # noqa: PLC0415

    if job.swot_completed_at is None:
        return (
            "The Hiring Manager has not finished the SWOT session for this "
            "role, so the evaluation criteria do not exist yet. A published job "
            "would take applications nobody could assess."
        )
    matrix = await scorecard.load_frozen_matrix(session, job.id)
    if matrix is None:
        return (
            "The evaluation criteria for this role have not been finalised by "
            "the Hiring Manager. They are what every candidate on this job is "
            "graded against, so publication waits for them."
        )
    return None


@router.post("/{job_id}/send-to-hiring-manager", response_model=JobOut)
async def send_jd_to_hiring_manager(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.SEND_JD_TO_HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """RBAC 9.3: the Recruiter hands the draft JD to the assigned Hiring Manager.

    DRAFT -> SENT_TO_HIRING_MANAGER, which is the second state of 17's lifecycle
    and the one that was previously unreachable: migration 0061 added the column
    and backfilled it, and nothing in the product could advance it, so every job
    sat in DRAFT forever and 21's publish precondition could never be satisfied
    honestly.

    A JD with nothing in it is refused. Sending an empty draft to a Hiring
    Manager wastes the one review the workflow has, and 18 says in as many words
    that "the job MUST NOT be published as an unfinished draft".
    """
    job = await _get_visible_job(session, user, job_id)
    if not _has_publishable_jd(job):
        raise HTTPException(
            status_code=409,
            detail=(
                "Write the job description before sending it to the Hiring "
                "Manager. Generate a draft or type one, save it, then send."
            ),
        )
    current = job.lifecycle_state or hiring_pipeline.JobLifecycleState.DRAFT.value
    target = hiring_pipeline.JobLifecycleState.SENT_TO_HIRING_MANAGER
    if target not in hiring_pipeline.lifecycle_allowed_transitions(current):
        raise HTTPException(
            status_code=409,
            detail=(
                f"This job is already past the draft stage ({current}), so it "
                f"cannot be sent to the Hiring Manager again. A change after "
                f"finalisation goes through the revision workflow."
            ),
        )
    previous = job.lifecycle_state
    job.lifecycle_state = target.value
    await session.flush()
    row = await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="jd_sent_to_hiring_manager",
        target_type="job",
        target_id=job.id,
        metadata={"title": job.title},
    )
    row.job_id = job.id
    row.actor_role = user.role.value
    row.correlation_id = job.correlation_id
    row.previous_state = {"lifecycle_state": previous}
    row.new_state = {"lifecycle_state": job.lifecycle_state}
    logger.info(
        "jobs.sent_to_hiring_manager job_id=%s by=%s correlation_id=%s",
        job.id, user.user_id, job.correlation_id,
    )
    return _with_public_url(job)


@router.post("/{job_id}/renew", response_model=PublishJobOut)
async def renew_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.PUBLISH_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> PublishJobOut:
    """Re-open an expired job for another fixed 30-day window.

    This is the ONLY sanctioned way `posting_start_date` moves after publish,
    and it is deliberately not available while the job is still live: publish
    refuses a second stamp precisely so nobody silently extends a running
    posting, and renewal must not become a back door to the same thing.

    Everyone who applied to the previous run keeps their application and stays
    fully visible — they simply become Old Profiles (services/job_candidates),
    which is provenance and billing, never a loss of access.
    """
    job = await _get_visible_job(session, user, job_id)
    if job.archived_at is not None:
        raise HTTPException(status_code=409, detail="Restore this job before renewing it.")
    if job.ratified_at is None:
        raise HTTPException(
            status_code=409, detail="Publish this job before renewing it."
        )
    window = job_posting.describe(job)
    if window.posting_status not in (job_posting.STATUS_GRACE, job_posting.STATUS_EXPIRED):
        raise HTTPException(
            status_code=409,
            detail=(
                "This job is still live. You can renew it once its 30-day "
                "posting window has closed."
            ),
        )
    now = datetime.now(timezone.utc)
    previous_start = job.posting_start_date
    job.posting_start_date = now
    await session.flush()
    await session.refresh(job)  # pick up the regenerated end/grace columns
    await _invalidate_public_job(job.id)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="job_renewed",
        target_type="job",
        target_id=job.id,
        metadata={
            "previous_posting_start": previous_start.isoformat() if previous_start else None,
            "new_posting_start": now.isoformat(),
        },
    )
    out = PublishJobOut.model_validate(job)
    out.jd_markdown = jd_markdown_for(job) or None
    _attach_public_url(out, job)
    _apply_posting_window(out, job)
    out.public_application_url = out.public_application_url or public_job_url(job.id)
    return out


@router.post("/{job_id}/archive", response_model=JobOut)
async def archive_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Safely close a job without deleting applications or audit history."""
    job = await _get_visible_job(session, user, job_id)
    if job.archived_at is None:
        job.archived_at = datetime.now(timezone.utc)
        await session.flush()
        await _invalidate_public_job(job.id)
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="job_archived",
            target_type="job",
            target_id=job.id,
        )
    return _with_public_url(job)


@router.post("/{job_id}/restore", response_model=JobOut)
async def restore_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    job = await _get_visible_job(session, user, job_id)
    if job.archived_at is not None:
        job.archived_at = None
        await session.flush()
        await _invalidate_public_job(job.id)
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="job_restored",
            target_type="job",
            target_id=job.id,
        )
    return _with_public_url(job)


@router.get("", response_model=list[JobOut])
async def list_jobs(
    include_archived: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[JobOut]:
    """One page of the tenant's jobs, newest first.

    Bounded rather than 25-by-default: this list is what the Jobs page renders
    in full and what the job pickers elsewhere in the portal read, so a hard 25
    would silently hide jobs from a dropdown. 100 keeps the response small while
    staying above any realistic single-screen need, and `skip` is there for the
    customers who outgrow it.
    """
    stmt = (
        select(Job)
        .where(Job.tenant_id == user.tenant_id)
        # `id` makes the order total so a page boundary cannot drop a job that
        # shares a created_at with its neighbour.
        .order_by(Job.created_at.desc(), Job.id)
        .offset(skip)
        .limit(limit)
    )
    if not include_archived:
        stmt = stmt.where(Job.archived_at.is_(None))
    if not await _can_see_pre_ratified(session, user):
        stmt = stmt.where(Job.ratified_at.isnot(None))  # FR-3.4 (terminal marker)
    rows = (await session.execute(stmt)).scalars().all()
    return [_with_public_url(j) for j in rows]


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """Full JD for the job detail page, including the three narrative sections
    resolved through the per-job override -> company profile chain (spec §3.1)."""
    job = await _get_visible_job(session, user, job_id)
    return await _job_detail_out(session, job)


@router.patch("/{job_id}", response_model=JobDetailOut)
async def patch_job(
    job_id: uuid.UUID,
    body: JobPatchIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_JOB_DESCRIPTION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """In-place partial edit of the JD from the job page (spec §3.1).

    True PATCH semantics: a field the caller did not send is untouched. For the
    three narrative sections that distinction carries meaning — sending
    `about_company: null` CLEARS the per-job override so the job falls back to
    the company profile, which is different from not mentioning the field.
    `model_fields_set` is what separates them.

    Unlike the older PUT /jobs/{id}/jd, this does NOT require the job to be
    ratified: on the flat staff model a job is published the moment it is
    created, so a pre-ratification gate would only ever reject edits to jobs
    that no longer exist in that state.
    """
    job = await _get_visible_job(session, user, job_id)
    sent = body.model_fields_set

    if "title" in sent and body.title is not None:
        job.title = body.title
    if "department" in sent:
        job.department = body.department
    if "level" in sent:
        job.level = body.level
    if "requirement_period" in sent:
        job.requirement_period = body.requirement_period
    for field in ("experience_min_years", "experience_max_years"):
        if field in sent:
            setattr(job, field, getattr(body, field))
    if "jd_markdown" in sent and body.jd_markdown is not None:
        # The document wins over any `jd` sent alongside it: it is canonical,
        # and re-deriving keeps the two from contradicting each other.
        from app.services import jd_generation

        job.jd_markdown = jd_generation.strip_em_dashes(body.jd_markdown.strip())
        job.jd_json = jd_generation.parse_jd_markdown(job.jd_markdown)
    elif "jd" in sent and body.jd is not None:
        # Re-render the document from the edited sections. Writing only
        # `jd_json` left `jd_markdown` stale, and `jd_markdown_for` prefers the
        # STORED document over a re-render — so an edit made on the job detail
        # page (which sends `jd`, not `jd_markdown`) updated the recruiter's
        # view and never reached the candidate-facing JD at /apply/{job_id}.
        # The document is canonical; it must move whenever the sections do.
        from app.services import jd_generation

        job.jd_json = body.jd.model_dump(mode="json")
        job.jd_markdown = jd_generation.strip_em_dashes(
            jd_generation.render_jd_markdown(
                job.jd_json,
                min_years=job.experience_min_years,
                max_years=job.experience_max_years,
            )
        )
    if "grade" in sent and body.grade is not None:
        # ASSUMPTION (mirrors PUT /jd): changing the grade changes how many
        # questions a FUTURE candidate is asked, but does NOT regenerate an
        # already-built technical bank — every candidate on a job must answer
        # the identical technical set (spec §5), and regenerating mid-flight
        # would break that comparison. It does not touch the PPI framework
        # either: the framework is derived from the JD, not from the grade.
        job.assessment_grade = body.grade
    for key in _JD_SECTIONS:
        if key in sent:
            setattr(job, key, getattr(body, key))
    if "proctoring_warning_policy" in sent and body.proctoring_warning_policy is not None:
        # Applies to every candidate assessed on this job from now on. It
        # moves no score and no grade, so it needs no freeze: a policy is
        # about what happens DURING a session, not about how it is graded.
        job.proctoring_warning_policy = body.proctoring_warning_policy

    await session.flush()
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="job_patched",
        target_type="job",
        target_id=job.id,
        metadata={"fields": sorted(sent), "grade": job.assessment_grade},
    )
    await _invalidate_public_job(job.id)
    return await _job_detail_out(session, job)


@router.get("/{job_id}/candidates", response_model=RankedCandidatesOut)
async def list_job_candidates(
    job_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=job_candidates.PAGE_SIZE, ge=1, le=job_candidates.MAX_PAGE_SIZE),
    include_archived: bool = Query(default=False),
    profile_age: str | None = Query(
        default=None,
        pattern="^(old|new)$",
        description="Narrow to Old Profiles (applied before the current posting window) or New Profiles.",
    ),
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> RankedCandidatesOut:
    """The job page's inline candidate table — ranked, paginated, word-labelled.

    Ordering is decided in SQL from the job's grade (services/job_candidates)
    and is a TOTAL order, so page boundaries stay stable across requests. No
    numeric score appears in the response: the five comments each carry a word
    label instead (spec §2.2 / claude.md).

    Every row carries `profile_age`. After a renewal, applicants from the
    previous window read as Old Profiles — still listed, still ranked, still
    openable; the distinction is provenance and billing, never access.
    """
    job = await _get_visible_job(session, user, job_id)
    grade = job.assessment_grade or "non_managerial"
    result = await job_candidates.ranked_candidates(
        session,
        job.id,
        grade,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
        profile_age_filter=profile_age,
    )
    return RankedCandidatesOut(
        job_id=job.id,
        grade=grade,
        level=job_candidates.grade_label(grade),
        results=[RankedCandidateOut.model_validate(row) for row in result.rows],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
        has_next=result.has_next,
        has_previous=result.has_previous,
        range_start=result.range_start,
        range_end=result.range_end,
    )


@router.post("/{job_id}/candidates/{link_id}/review", response_model=ReviewProfileOut)
async def review_profile(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ReviewProfileOut:
    """Record that a recruiter opened a candidate's detail view.

    This is the precise trigger the credit spec asks for (§3.2): the FIRST time
    a recruiter opens an OLD Profile — one carried over from a previous posting
    window — costs a bulk-rate 3 sub-units (20 reviews per credit). Opening a
    New Profile costs nothing; opening the same Old Profile again costs nothing,
    because `old_profile_reviews` is UNIQUE on (link, reviewer) and 20-per-credit
    only means anything if a re-read is free.

    Moving an Old Profile into a live assessment is NOT this event — that goes
    through the invitation route and is charged at the standard rate, exactly
    like a fresh candidate.
    """
    job = await _get_visible_job(session, user, job_id)
    row = (
        await session.execute(
            select(JobCandidateLink).where(
                JobCandidateLink.id == link_id,
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.tenant_id == user.tenant_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")

    age = job_candidates.profile_age(row.created_at, job.posting_start_date)
    if age != job_candidates.PROFILE_AGE_OLD:
        return ReviewProfileOut(profile_age=age, charged=False, subunits_charged=0)

    marker = OldProfileReview(
        tenant_id=user.tenant_id, job_candidate_link_id=row.id, reviewer_user_id=user.user_id
    )
    try:
        async with session.begin_nested():
            session.add(marker)
            await session.flush()
    except IntegrityError:
        # This reviewer has opened this profile before. Already paid for.
        return ReviewProfileOut(profile_age=age, charged=False, subunits_charged=0)

    # The old-profile rate is FLAT across STEM and non-STEM (Part 5 §2.1 only
    # differentiates full and partial reports); the classification is passed
    # for the ledger's audit trail, not the price.
    charged = await credits.consume(
        session,
        tenant_id=user.tenant_id,
        event_type=EVENT_OLD_PROFILE_REVIEW,
        idempotency_key=f"old-profile-review:{row.id}:{user.user_id}",
        job_candidate_link_id=row.id,
        metadata={"job_id": str(job.id)},
        role_classification=job.role_classification,
    )
    return ReviewProfileOut(
        profile_age=age,
        charged=charged,
        subunits_charged=CONSUMPTION_SUBUNITS[EVENT_OLD_PROFILE_REVIEW] if charged else 0,
    )


@router.post(
    "/{job_id}/run-matching",
    response_model=RunMatchingOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_job_matching(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.TRIGGER_MATCHING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> RunMatchingOut:
    """"RUN AI MATCHING" on the job page (spec §7).

    Job-scoped alias for POST /matching/jobs/{job_id}/run, so the job page does
    not have to reach into a second router for its own primary action. Both
    paths share one implementation — there is no second copy of the eligibility
    rules to drift.
    """
    from app.api.matching import run_matching as _run_matching

    return await _run_matching(job_id=job_id, user=user, session=session)


@router.post("/{job_id}/submit", response_model=JobOut)
async def submit_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """DEPRECATED (PRD v1.0 §4): the approval chain is bypassed — jobs publish
    directly on create (see create_job). This endpoint is retained for the
    dormant multi-level FSM and returns gracefully; on the normal flat path a
    job is already `ratified`, so a submit attempt returns 409 (never 500).

    draft -> first active approval level; leading inactive levels are logged as
    explicitly skipped."""
    job = await _get_visible_job(session, user, job_id)
    if job.status != JobStatus.draft:
        raise HTTPException(status_code=409, detail="Job has already been submitted")

    config = await _approval_config(session, user.tenant_id)
    try:
        result = await fsm.apply_submit(session, job, config)
    except fsm.ApprovalConfigError as exc:
        raise HTTPException(
            status_code=409,
            detail="Approval levels are not configured for this company (FR-2.3)",
        ) from exc

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_submitted", target_type="job", target_id=job.id,
                metadata={"new_status": result.new_status.value})
    if result.ratified:
        celery_app.send_task("pickready.run_matching", args=[str(job.id)])  # FR-4.2
    return JobOut.model_validate(job)


@router.post("/{job_id}/approve", response_model=JobOut)
async def approve_job(
    job_id: uuid.UUID,
    body: ApproveIn,
    user: CurrentUser = Depends(require_capability(caps.APPROVE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Approve/reject at the job's current level. The FSM re-validates that
    the actor is the assigned approver of exactly this level."""
    job = await _get_visible_job(session, user, job_id)
    config = await _approval_config(session, user.tenant_id)
    try:
        result = await fsm.apply_transition(
            session, job, config,
            acting_user_id=user.user_id,
            decision=ApprovalDecision(body.decision),
            remarks=body.remarks,
        )
    except fsm.NotAssignedApprover as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except fsm.PriorLevelPending as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (fsm.AlreadyTerminal, fsm.NotSubmitted) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except fsm.ApprovalConfigError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_approval_decision", target_type="job", target_id=job.id,
                metadata={"decision": body.decision, "new_status": result.new_status.value,
                          "remarks": body.remarks})
    if result.ratified:
        # The moment a job reaches HR, Databank matching runs (FR-4.2) —
        # asynchronously, never inline.
        celery_app.send_task("pickready.run_matching", args=[str(job.id)])
    return JobOut.model_validate(job)


@router.get("/{job_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[ApprovalOut]:
    job = await _get_visible_job(session, user, job_id)
    rows = (
        await session.execute(
            select(JobApproval).where(JobApproval.job_id == job.id)
            .order_by(JobApproval.decided_at, JobApproval.id)
        )
    ).scalars().all()
    return [ApprovalOut.model_validate(r) for r in rows]


@router.put("/{job_id}/compensation", response_model=JobDetailOut)
async def set_compensation(
    job_id: uuid.UUID,
    body: CompensationIn,
    user: CurrentUser = Depends(require_capability(caps.ADD_COMPENSATION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """HR adds compensation post-ratification (FR-4.1)."""
    job = await _get_visible_job(session, user, job_id)
    if job.ratified_at is None:
        raise HTTPException(status_code=409, detail="Compensation is added after ratification (FR-4.1)")
    job.compensation_json = body.compensation
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_compensation_set", target_type="job", target_id=job.id)
    return await _job_detail_out(session, job)


@router.put("/{job_id}/jd", response_model=JobDetailOut)
async def edit_jd(
    job_id: uuid.UUID,
    body: JDUpdateIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_JOB_DESCRIPTION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """HR JD-ambiguity fixes, post-ratification only (FR-4.1)."""
    job = await _get_visible_job(session, user, job_id)
    if job.ratified_at is None:
        raise HTTPException(status_code=409, detail="HR edits the JD only after ratification (FR-4.1)")
    job.jd_json = body.jd.model_dump(mode="json")
    if body.title is not None:
        job.title = body.title
    if body.department is not None:
        job.department = body.department
    if body.level is not None:
        job.level = body.level
    if body.grade is not None:
        # ASSUMPTION: changing the grade changes how many questions a FUTURE
        # candidate is asked, but does NOT regenerate an already-generated
        # technical bank — every candidate on a job must answer the identical
        # technical set (spec §5), and regenerating mid-flight would break that
        # comparison. The PPI framework is derived from the JD, not the grade,
        # so it is unaffected.
        job.assessment_grade = body.grade
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_jd_edited", target_type="job", target_id=job.id,
                metadata={"grade": job.assessment_grade})
    await _invalidate_public_job(job.id)
    return await _job_detail_out(session, job)


# ── AI JD generation (FR-3.3 Path A) ─────────────────────────────────────────

@router.post("/generate-jd", response_model=JDGenerateOut)
async def generate_jd(
    body: JDGenerateIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JDGenerateOut:
    """Draft the unified JD document from the recruiter's brief (FR-3.3).

    Returns ONE Markdown document, `jd_markdown`, with seven fixed sections,
    plus `jd`, the per-section projection parsed straight back out of it. The
    recruiter edits the document and only then publishes.

    The pre-2026-07-28 top-level section keys are still emitted (see
    JDGenerateOut), so a client that has not been rebuilt keeps working.

    Coded defensively: if the jd_generation service is not wired up at runtime,
    this returns 503 rather than 500.
    """
    try:
        from app.services import jd_generation
    except ImportError as exc:  # module not present yet at runtime
        raise HTTPException(status_code=503, detail="JD generation unavailable") from exc
    if not hasattr(jd_generation, "generate_jd_document"):
        raise HTTPException(status_code=503, detail="JD generation unavailable")

    brief = {
        "title": body.title,
        # The renamed "Skills" box, with the deprecated key_requirements folded
        # in so a half-deployed client is never rejected.
        "skills": body.merged_skills(),
        "experience_min_years": body.experience_min_years,
        "experience_max_years": body.experience_max_years,
        "grade": body.grade,
        "reporting_to": body.reporting_to,
        "department": body.department,
    }
    generated = await jd_generation.generate_jd_document(brief)

    # ── STEM classification, at THIS moment (Master Directive Part 3 §3) ────
    # Runs on the raw AI draft before the recruiter sees it, is persisted
    # server-side, and is what the created job inherits. The recruiter's later
    # edits never re-trigger it (Rule 3), and nothing the client sends can
    # change it (Rule 2) — job creation references the row by id only.
    from app.models.job import JDDraft
    from app.services import stem_classification

    result = stem_classification.classify_safe(generated["jd_markdown"], body.title)
    draft = JDDraft(
        tenant_id=user.tenant_id,
        created_by=user.user_id,
        title=body.title,
        raw_jd_text=generated["jd_markdown"],
        role_classification=result.classification,
        classification_confidence=result.confidence,
        stem_score=result.stem_score,
        classification_signals=result.signals,
        tentative=result.tentative,
        engine_error=result.engine_error,
    )
    session.add(draft)
    await session.flush()

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_jd_generated", target_type="job", target_id=None,
                metadata={
                    "title": body.title,
                    "generated_by_ai": generated.get("generated_by_ai", True),
                    "experience_min_years": body.experience_min_years,
                    "experience_max_years": body.experience_max_years,
                    "jd_draft_id": str(draft.id),
                    "role_classification": result.classification,
                    "classification_confidence": result.confidence,
                    "classification_tentative": result.tentative,
                    "classification_engine_error": result.engine_error,
                })
    return JDGenerateOut(
        jd_markdown=generated["jd_markdown"],
        jd=generated["jd"],
        generated_by_ai=bool(generated.get("generated_by_ai", True)),
        jd_draft_id=draft.id,
        role_classification=result.classification,
        credit_cost_per_report=float(result.credit_cost_per_report),
    )


# ── Upload Candidate Data Bank (2026-07-28) ──────────────────────────────────

def _placeholder_email(job_id: uuid.UUID, sha_or_name: str) -> str:
    """A stable, obviously-fake address for a resume that carries no email.

    Rejecting the file would be worse: the resume is still a real candidate the
    recruiter wants ranked, and an unreachable address is a data-quality
    problem a human can fix later. The row is flagged `identified: false` in
    the response so it is visible rather than silent. The local part is derived
    from the file's content hash, so re-uploading the same file finds the same
    candidate instead of creating a duplicate every time.
    """
    token = "".join(ch for ch in sha_or_name.lower() if ch.isalnum())[:24] or "unknown"
    return f"databank+{job_id.hex[:8]}.{token}@placeholder.invalid"


async def _store_one_databank_resume(
    session: AsyncSession,
    user: CurrentUser,
    job: Job,
    upload: UploadFile,
) -> DatabankUploadResultOut:
    """Store one resume and link it to the job. Never raises."""
    import hashlib

    from app.services import resume_parsing, resume_storage

    filename = upload.filename or "resume"
    try:
        data, safe_name, _mime = await resume_storage.read_validated_resume(upload)
    except HTTPException as exc:
        return DatabankUploadResultOut(
            filename=filename, ok=False, error=_detail_message(exc)
        )

    # Cheap, local identity read so a candidate row can exist NOW. The real
    # parse (LLM extraction + embedding) is still a Celery task and still runs
    # afterwards; nothing slow happens in this request (claude.md rule 4).
    identity = await run_in_threadpool(
        resume_parsing.extract_contact_identity, data, safe_name
    )
    sha256 = hashlib.sha256(data).hexdigest()
    email = identity.get("email")
    identified = bool(email)
    if not identified:
        email = _placeholder_email(job.id, sha256)

    # Rewind so resume_storage re-reads the same bytes it just validated.
    await upload.seek(0)
    try:
        asset = await resume_storage.store_resume(upload)
    except HTTPException as exc:
        return DatabankUploadResultOut(
            filename=filename, ok=False, email=email, identified=identified,
            error=_detail_message(exc),
        )

    candidate = (
        await session.execute(select(Candidate).where(Candidate.email == email))
    ).scalars().first()
    if candidate is None:
        candidate = Candidate(
            tenant_id=user.tenant_id,
            email=email,
            full_name=identity.get("full_name"),
            phone=identity.get("phone"),
        )
        session.add(candidate)
        await session.flush()
    elif not candidate.full_name and identity.get("full_name"):
        candidate.full_name = identity["full_name"]

    existing = (
        await session.execute(
            select(JobCandidateLink).where(
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.candidate_id == candidate.id,
            )
        )
    ).scalars().first()
    if existing is not None:
        return DatabankUploadResultOut(
            filename=filename, ok=False, email=email, identified=identified,
            candidate_id=candidate.id,
            error="This candidate is already linked to this job.",
        )

    profile = Profile(candidate_id=candidate.id, source_tenant_id=user.tenant_id)
    resume_storage.apply_resume_asset(profile, asset)
    session.add(profile)
    await session.flush()
    if candidate.main_profile_id is None:
        candidate.main_profile_id = profile.id

    link = JobCandidateLink(
        tenant_id=user.tenant_id,
        job_id=job.id,
        candidate_id=candidate.id,
        profile_id=profile.id,
        # `source` is the older retrieval marker; `source_type` is the
        # procurement tag the job page renders. Both say databank here.
        source=LinkSource.databank,
        source_type=SOURCE_TYPE_DATABANK,
    )
    session.add(link)
    await session.flush()
    # Master Directive Part 2 section 5.1: EV_PROFILE_SUBMIT for a profile
    # entering the pipeline via the databank upload path.
    await telemetry_events.emit(
        session,
        tenant_id=user.tenant_id,
        event_code=telemetry_events.EV_PROFILE_SUBMIT,
        job_id=job.id,
        candidate_id=candidate.id,
        job_candidate_link_id=link.id,
        actor_user_id=user.user_id,
        correlation_id=job.correlation_id,
        payload={"source": SOURCE_TYPE_DATABANK},
    )
    return DatabankUploadResultOut(
        filename=filename, ok=True, email=email, identified=identified,
        candidate_id=candidate.id, profile_id=profile.id, link_id=link.id,
    )


def _detail_message(exc: HTTPException) -> str:
    """Flatten resume_storage's {message, retryable} detail into one string."""
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or "This file could not be stored.")
    return str(detail)


@router.post(
    "/{job_id}/candidates/databank",
    response_model=DatabankUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_databank_candidates(
    job_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    user: CurrentUser = Depends(require_capability(caps.UPLOAD_RESUMES)),
    session: AsyncSession = Depends(get_tenant_db),
) -> DatabankUploadOut:
    """Upload Candidate Data Bank: up to 25 resumes in one request.

    PARTIAL SUCCESS IS THE CONTRACT. Each file is handled on its own and every
    file gets a result row, so one corrupt PDF costs the recruiter that one
    file and not the other 24. Nothing here parses a resume inline: each
    accepted file enqueues the existing `parse_resume` task, and one
    `run_matching` is enqueued for the job at the end rather than once per file
    (claude.md rule 4).

    Databank candidates go through IDENTICAL AI parsing, embedding, matching
    and assessment to applied and sourced candidates. `source_type` is a tag
    for display and filtering; no behaviour anywhere branches on it.

    (claude.md rule 7 still holds and is untouched: it exempts databank
    profiles from the EMPLOYER VERIFICATION flow, which keys off `source`, and
    the 40 aspects are a profile form now rather than an outreach step.)

    Gated on the existing UPLOAD_RESUMES capability, which all three flat staff
    roles already hold. No new capability is invented for this.
    """
    job = await _get_visible_job(session, user, job_id)
    if job.archived_at is not None:
        raise HTTPException(
            status_code=409, detail="Restore this job before uploading candidates."
        )

    if not files:
        raise HTTPException(status_code=400, detail="Select at least one resume file.")
    if len(files) > MAX_DATABANK_FILES:
        # Refused whole, before a single byte is stored: a truncated batch that
        # reports success is worse than an honest rejection.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Upload up to {MAX_DATABANK_FILES} resumes at a time. "
                f"You selected {len(files)}."
            ),
        )

    results: list[DatabankUploadResultOut] = []
    for upload in files:
        try:
            results.append(await _store_one_databank_resume(session, user, job, upload))
        except Exception as exc:  # noqa: BLE001 — one bad file, not a failed batch
            logger.warning(
                "databank.file_failed job_id=%s file=%s error=%s",
                job_id, upload.filename, type(exc).__name__,
            )
            results.append(
                DatabankUploadResultOut(
                    filename=upload.filename or "resume",
                    ok=False,
                    error="This file could not be processed. Please retry it.",
                )
            )

    created = [r for r in results if r.ok]
    for result in created:
        celery_app.send_task("pickready.parse_resume", args=[str(result.profile_id)])
    if created:
        # One matching run for the batch. Twenty-five is the same job scored
        # twenty-five times otherwise, and matching already scores every
        # non-archived link on the job.
        celery_app.send_task("pickready.run_matching", args=[str(job.id)])

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="databank_candidates_uploaded",
        target_type="job",
        target_id=job.id,
        metadata={
            "received": len(files),
            "created": len(created),
            "failed": len(results) - len(created),
        },
    )
    return DatabankUploadOut(
        job_id=job.id,
        received=len(files),
        created=len(created),
        failed=len(results) - len(created),
        results=results,
    )


# ── Public (unauthenticated) published-job read (FR-3.4) ─────────────────────

@router.get("/public/{job_id}", response_model=PublicJobOut)
async def get_public_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_public_db),
) -> PublicJobOut:
    """Canonical PUBLIC read of a published job — powers the open application
    page reached via readypick.ai/apply/{job_uuid}. Unauthenticated, but ONLY returns
    PUBLISHED jobs (`ratified_at` set) and ONLY public fields (title, JD,
    company name) — no internal ATS data leaks. 404 for any unpublished or
    unknown id (never reveal existence).

    Cached for 10 minutes. This is the highest-volume read in the product — a
    job link pasted into LinkedIn is fetched by everyone who scrolls past it —
    and every hit otherwise costs a job read, a tenant read, a company-profile
    read and a markdown render. The write paths (JD edit, publish, archive)
    invalidate it, so the author sees their own change at once.

    Only the SUCCESSFUL payload is cached. A 404 is never stored: the two ways
    to get one are "not published yet" and "window just closed", and both flip
    on a schedule that a stale negative entry would fight with.
    """
    cache_key = cache.key("public-job", job_id)
    cached = await cache.get(cache_key)
    if cached is not None:
        return PublicJobOut.model_validate(cached)

    job = await session.get(Job, job_id)
    if job is None or job.ratified_at is None or job.archived_at is not None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Spec Rule 2: an externally shared link (Naukri, LinkedIn, a forwarded
    # email) works for the 30-day active window and 404s afterwards — including
    # throughout the grace period, which is scoped to people who already
    # applied and grants nothing to an anonymous visitor.
    if not job_posting.public_link_active(
        job.posting_start_date, job.posting_end_date
    ):
        raise HTTPException(
            status_code=404, detail="This job posting has expired"
        )
    company_name = (
        await session.execute(select(Tenant.name).where(Tenant.id == job.tenant_id))
    ).scalar_one_or_none()
    out = PublicJobOut.model_validate(job)
    out.company_name = company_name
    # The unified document is what the candidate reads. A job written before
    # 2026-07-28 has none stored, so one is rendered from its sections rather
    # than showing an applicant a blank description.
    out.jd_markdown = jd_markdown_for(job) or None
    resolved, _ = resolve_jd_sections(
        job, await _company_sections(session, job.tenant_id)
    )
    out.about_company = resolved["about_company"]
    out.work_life = resolved["work_life"]
    out.benefits = resolved["benefits"]
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=cache.TTL_JOB_DESCRIPTION)
    return out
