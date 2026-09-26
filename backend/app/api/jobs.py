"""Job endpoints: create a draft, edit its JD, publish, and the posting lifecycle.

THE FLOW (Vivekium release): Create Job saves a DRAFT, always. The JD has one
edit path (`PATCH /jobs/{id}/jd`). The job goes live only through
`POST /jobs/{id}/publish`, which needs a JD, a saved SWOT and saved skills.
The multi-level approval chain (the hand-off to the Hiring Manager, submit,
approve and the approvals list) and the second JD writer (`PUT /jobs/{id}/jd`)
are DELETED: none had a caller, and one left the canonical document stale.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, UploadFile, status,
)
from sqlalchemy import select, text
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
from app.models.enums import JobStatus, LinkSource
from app.models.job import REPORTING_TO_OPTIONS, Job
from app.models.proctoring import DEFAULT_WARNING_POLICY
from app.models.tenant import Tenant
from app.schemas.jobs import (
    AssessmentDisputeIn,
    AssessmentRetentionOut,
    CompensationIn,
    DatabankInviteIn,
    DatabankInviteOut,
    DatabankInviteResultOut,
    DatabankUploadOut,
    DatabankUploadResultOut,
    JDGenerateIn,
    JDGenerateOut,
    JDMarkdownIn,
    JobCloseIn,
    JobCreateIn,
    JobDetailOut,
    JobOut,
    JobPatchIn,
    PublicJobOut,
    PublishJobOut,
    ReportingToOptionsOut,
    ReviewProfileOut,
    jd_body_is_empty,
)
from app.schemas.ranking import RankedCandidateOut, RankedCandidatesOut
from uuid import uuid4 as _uuid4

from app.services import approval_fsm as fsm
from app.services import assessment_contract
from app.services import capabilities as caps
from app.services import credits
from app.services import job_assessment_retention
from app.services import job_candidates
from app.services import job_posting
from app.services import locks
from app.services import candidate_identity
from app.services import candidate_updates
from app.services import hiring_pipeline
from app.services import rbac
from app.services import status_hygiene
from app.services import swot_analysis
from app.services import telemetry_events
from app.services.audit import audit, record_action
from app.workers import agent_client
from app.workers.dispatch import dispatch_after_commit

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
    out.closed_at = window.closed_at
    out.closed_reason = window.closed_reason
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


def has_publishable_jd(job: Job) -> bool:
    """Is there any real job description to publish?

    The RESOLVED document: an explicit `jd_markdown`, or, for a job written
    before the document existed (migration 0022), one renderable from its
    `jd_json` sections. Headings alone are not a job description, by the one
    test create and publish share (`schemas.jobs.jd_body_is_empty`).
    """
    stored = (job.jd_markdown or "").strip()
    if stored:
        return not jd_body_is_empty(stored)

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
    not role-derived (claude.md rule 3). Whoever may create a job or publish a
    draft must be able to read it before it is published; everyone else sees a
    job only once ratified (FR-3.4)."""
    return await rbac.has_capability(
        session, user.tenant_id, user.role, caps.CREATE_JOB
    ) or await rbac.has_capability(session, user.tenant_id, user.role, caps.PUBLISH_JOB)


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


#: The credit gate's refusal (spec §11). Shared by create and JD generation.
CREDITS_EXHAUSTED_DETAIL = (
    "Your credit pool is exhausted, so new jobs cannot be created. "
    "Purchase a credit bundle to continue creating jobs and "
    "assessing candidates."
)


async def _require_create_gates(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """The two gates on starting a new job, in order, before any work.

    The credit gate (spec §11) is checked at the MOMENT of creation and nowhere
    else: a job created while the pool had credit stays created if the pool
    later empties. Loud and immediate, with the way out named: spec §11 is
    explicit that there is no silent failure and no degraded mode here.

    Gate 1 (workflow §18): the Company Profile must say something. It is what
    every job on the tenant is derived from and what this job's narrative
    sections are seeded from. Asked of the TABLE, and only at creation: a job
    created before the client wrote their profile stays created.
    """
    if not await credits.has_positive_balance(session, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=CREDITS_EXHAUSTED_DETAIL,
        )
    from app.services.hiring import company_requirements  # noqa: PLC0415

    blocked = await company_requirements.creation_blocked(session, tenant_id)
    if blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Create a job as a DRAFT. Publishing is `POST /jobs/{id}/publish`.

    ALWAYS A DRAFT (Vivekium release). `publish` used to default to True and
    went live under CREATE_JOB alone, skipping the publish gate, the JD index
    and the lifecycle: every live job on pilot reached PUBLISHED that way with
    no skills anyone had saved. A body still sending `publish: true` is a 422
    naming the new flow (`schemas.jobs.JobCreateIn`), loud during the rolling
    deploy rather than a silently mislabelled draft.

    THE CREATOR IS ASSIGNED. A Recruiter or Hiring Manager who creates a job
    becomes its assigned Recruiter or Hiring Manager (`rbac.assign_creator`),
    because every SCOPED cell of RBAC 24 reads `job_assignments` and nothing
    else writes it: without this, the person who created a job could not
    publish it or edit its skills.

    Nothing is dispatched here. The skills draft starts when the team first
    saves the Job SWOT (`skills.after_swot_saved`), the event that produces
    its input, and matching starts at publication.
    """
    await _require_create_gates(session, user.tenant_id)

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

    # The document is canonical: the sections are derived from it so
    # `jd_json.skills` can never contradict what the candidate reads. The
    # schema has already refused a document that is only headings.
    document = jd_generation.strip_em_dashes(body.jd_markdown.strip())
    jd_sections = jd_generation.parse_jd_markdown(document)
    # The document has no reporting line of its own, so the parse leaves it
    # empty; the recruiter's choice from the Create Job form is stored beside
    # the derived sections rather than dropped.
    jd_sections["reporting_to"] = (
        jd_generation.strip_em_dashes((body.reporting_to or "").strip()) or None
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
        classification_signals = classified.explanation
        classification_tentative = bool(classified.tentative or classified.engine_error)
        raw_jd_text = document or None

    job = Job(
        tenant_id=user.tenant_id,
        title=body.title,
        department=body.department,
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
        # canonical store. It locks with the skills at the first candidate
        # start (D5), see `patch_job`.
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

    await rbac.assign_creator(session, job, user.user_id, user.role)
    await record_action(
        session,
        action="job_created",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        new_state={"lifecycle_state": job.lifecycle_state},
        metadata={"title": body.title, "grade": body.grade},
    )
    # Load the GENERATED posting-window columns before serialising, so the
    # serialiser never triggers a lazy load in the wrong greenlet.
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
    """Save an edit of the unified JD document. The ONE JD edit path.

    `PATCH /jobs/{id}` no longer takes the JD and `PUT /jobs/{id}/jd` is
    deleted (Vivekium release): three writers of one text is the shape rule 5
    forbids, and the PUT left the document stale. Available at ANY time,
    before or after publish. The client asked for an
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
    sections = jd_generation.parse_jd_markdown(document)
    # `reporting_to` is not a section of the document, so re-deriving would
    # erase the value Create Job stored; it is carried across instead.
    sections["reporting_to"] = (job.jd_json or {}).get("reporting_to")
    job.jd_json = sections
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
    user: CurrentUser = Depends(require_capability(caps.PUBLISH_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> PublishJobOut:
    """Publish a drafted job and hand back its public application link.

    The ONLY way a job goes live (Vivekium release). Refused, each with a 409
    naming the fix: an archived job, a job already live (re-stamping
    `posting_start_date` would quietly restart the fixed 30-day window), and a
    job missing any setup step: the JD, the saved SWOT, the saved skills
    (`_publication_blocked` names every missing one, in order).

    Matching and the JD index are dispatched AFTER the commit, so a publish
    that rolls back starts nothing.

    AUTHORIZATION IS RBAC 3's WHOLE CHAIN, run here rather than as a route
    dependency for one reason: the lifecycle refusal. A job whose skills were
    never saved is still DRAFT, and `publish_requires_finalized` answered it
    with a bare "Not permitted", which sends a recruiter to ask somebody else
    what is wrong. Tenant, ceiling, grant and assignment scope still refuse
    first and exactly as before; only a caller who passes all of them and is
    stopped by the STATE is told which setup steps are missing.

    The response carries `public_application_url`, the absolute link the copy
    popup shows for pasting into LinkedIn, Naukri or an email.
    """
    resource = await rbac.load_job_resource(session, job_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Not found")
    decision = await rbac.authorize(
        session,
        rbac.Principal(user_id=user.user_id, tenant_id=user.tenant_id, role=user.role),
        caps.PUBLISH_JOB,
        resource,
    )
    job = None
    if decision.reason == "publish_requires_finalized":
        job = await _get_visible_job(session, user, job_id)
        if job.ratified_at is None and job.archived_at is None:
            blocked = await _publication_blocked(session, job)
            if blocked:
                raise HTTPException(status_code=409, detail=blocked)
    rbac.raise_for(decision, caps.PUBLISH_JOB)
    job = job or await _get_visible_job(session, user, job_id)
    if job.archived_at is not None:
        raise HTTPException(
            status_code=409, detail="Restore this job before publishing it."
        )
    if job.ratified_at is not None:
        raise HTTPException(status_code=409, detail="This job is already published.")
    # ── RBAC 21: publication waits for every setup step, asked of the TABLES.
    #
    # TWO CHECKS, AND BOTH ARE LOAD-BEARING. The authorization above has
    # already refused any state earlier than FINALIZED, which only Save Skills
    # writes. This second check asks the rows themselves, because a lifecycle
    # state is a stamp like any other and a timestamp is not evidence that
    # work happened (rule 8).
    blocked = await _publication_blocked(session, job)
    if blocked:
        raise HTTPException(status_code=409, detail=blocked)

    previous_state = job.lifecycle_state
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
    # Every column in ONE insert (the 2026-09-20 audit_log rule).
    await record_action(
        session,
        action="job_published",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        previous_state={"lifecycle_state": previous_state},
        new_state={"lifecycle_state": job.lifecycle_state},
        metadata={
            "title": job.title,
            "public_url": public_job_url(job.id),
            # RBAC 21's four required facts. The publishing user and the
            # timestamp are the audit row's own columns.
            "published_version": job.criteria_version,
            "job_identifier": str(job.id),
        },
    )
    # Both AFTER the commit: a publish that rolls back must start nothing.
    dispatch_after_commit(session, "pickready.run_matching", args=[str(job.id)])
    # The JD is public and final at this point, so it becomes retrievable
    # (RPN-AI-UP-001 W2.1). At publish rather than at draft save: a draft is
    # edited repeatedly, and indexing every intermediate state would re-embed a
    # document nobody can apply to yet. `index_document` is incremental by
    # content hash, so a later edit re-embeds only the paragraphs that moved.
    dispatch_after_commit(session, "pickready.index_document", args=["jd", str(job.id)])

    out = PublishJobOut.model_validate(job)
    out.jd_markdown = jd_markdown_for(job) or None
    _attach_public_url(out, job)
    _apply_posting_window(out, job)
    # `_attach_public_url` only fills the link for a live, unarchived job, which
    # is exactly what we just made this one, so it is never blank here.
    out.public_application_url = out.public_application_url or public_job_url(job.id)
    return out


#: The setup steps publication waits for, in the order the job page walks
#: them. Each is the sentence the checklist and the 409 both render.
PUBLISH_STEP_JD = "write and save the job description"
PUBLISH_STEP_SWOT = "save the SWOT analysis"
PUBLISH_STEP_SKILLS = "save the skills"


async def publication_missing_steps(session: AsyncSession, job: Job) -> list[str]:
    """Every setup step still missing before `job` may be published, in order.

    Asked of the TABLES, never of the lifecycle: the JD from the document
    itself, the SWOT from its own row (`swot_analysis.is_saved`: a human saved
    or restored it; a model draft nobody read is not the team's analysis), and
    the skills from the saved stamp AND the hidden context
    (`assessment_contract.skills_saved`), so "publishable" and "lockable" can
    never disagree.
    """
    missing: list[str] = []
    if not has_publishable_jd(job):
        missing.append(PUBLISH_STEP_JD)
    if not swot_analysis.is_saved(await swot_analysis.get(session, job)):
        missing.append(PUBLISH_STEP_SWOT)
    if not await assessment_contract.skills_saved(session, job.id):
        missing.append(PUBLISH_STEP_SKILLS)
    return missing


async def _publication_blocked(session: AsyncSession, job: Job) -> str | None:
    """RBAC 21's precondition as ONE sentence naming EVERY missing step.

    None when nothing blocks. Naming all of them rather than the first is the
    2026-09-23 lesson: telling somebody about the first of three means three
    round trips.
    """
    missing = await publication_missing_steps(session, job)
    if not missing:
        return None
    if len(missing) == 1:
        steps = missing[0]
    else:
        steps = ", ".join(missing[:-1]) + " and " + missing[-1]
    return f"Before this job can be published, {steps}."


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


@router.post("/{job_id}/close", response_model=JobOut)
async def close_job(
    job_id: uuid.UUID,
    body: JobCloseIn,
    user: CurrentUser = Depends(require_capability(caps.PUBLISH_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Workflow Gate 8: stop the posting because the requirement was met.

    The 30 days are the LONGEST a posting runs, never the shortest. A client
    who fills the role on day 18 says so here, and from that instant the public
    link 404s, the job leaves every candidate's board and no new application is
    accepted.

    SUPERSEDED IN PART, 2026-09-18 (vivekium C5, owner-ruled final): this
    docstring used to promise "the reports ... continue exactly as before".
    They do not. Closing the job now PERMANENTLY ERASES its assessment data,
    the PRISM reports, the Tatva scores and the transcripts, because Stage B
    consent item 3 told every assessed candidate exactly that would happen.
    `erasure.job_closure_erasure` says precisely what goes and what stays
    (the ranked list, the pipeline history, the billing record and the
    consent records all remain). THERE IS NO REOPEN, so this deletion is as
    final as the closure itself; the confirmation UI must say so.

    SUPERSEDED AGAIN, 2026-09-22 (change request 22, owner ruling), and the
    part that reverses is WHEN. Closure no longer deletes anything. It
    WITHHOLDS: the employer and the recruiter lose access at this instant, the
    data is retained for thirty days, and
    `pickready.purge_closed_job_assessments` deletes it permanently at the end
    of that window. The owner's reason is this docstring's own next paragraph:
    there is no reopen, so an immediate irreversible delete left a misclick, a
    wrong job id and a dispute raised the following week with nothing to
    examine. The candidate's promise is unchanged and still kept, which is why
    the window has a hard end rather than an operator's discretion.
    `services/job_assessment_retention` owns the whole lifecycle, the access
    gate and the dispute path.

    WHY IT IS `publish_job` AND NOT A NEW CAPABILITY. Opening a posting to the
    public and closing it again are the same authority over the same thing, and
    a capability with no seeding migration is half a change (the product has
    shipped that mistake once and every dashboard control answered 403 for a
    whole phase). Whoever may publish this job may close it.

    THERE IS NO REOPEN. RBAC 22 asks for a controlled revision mechanism rather
    than a reopen, and reopening would restart a window candidates have already
    been told is over. A client who needs the role again renews or creates one.
    """
    job = await _get_visible_job(session, user, job_id)
    if job.closed_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This job is already closed. Closing it again would restamp "
                "the moment it stopped taking applications."
            ),
        )
    if job.ratified_at is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This job has never been published, so there is no posting to "
                "close. Archive the draft instead."
            ),
        )
    reason = (body.reason or "").strip() or None
    job.closed_at = datetime.now(timezone.utc)
    job.closed_reason = reason
    # RBAC 17's terminal state. Written here rather than inferred, for the same
    # reason publication writes PUBLISHED: a derived state records no actor.
    job.lifecycle_state = hiring_pipeline.JobLifecycleState.CLOSED_ARCHIVED.value
    # The closure STARTS the clock, in the same transaction, so a close that
    # rolls back schedules nothing and a close that commits can never leave
    # the data reachable. Stamped from `closed_at` rather than from a second
    # `now()`, because two clocks read a microsecond apart would put the
    # promise and the act it was made about on different days at midnight.
    job.assessment_purge_due_at = job_assessment_retention.purge_due_at(
        job.closed_at
    )
    await session.flush()
    await _invalidate_public_job(job.id)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=job_assessment_retention.ACTION_PENDING_DELETION,
        target_type="job",
        target_id=job.id,
        metadata=job_assessment_retention.describe(job).as_json(),
    )
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="job_closed",
        target_type="job",
        target_id=job.id,
        metadata={
            "title": job.title,
            "closed_at": job.closed_at.isoformat(),
            # Recorded because "why did this requisition stop" is the question
            # the audit log exists to answer, and the client typed the answer.
            "reason": reason,
        },
    )
    await _notify_applicants_of_closure(session, job)
    logger.info("jobs.closed job_id=%s by=%s", job.id, user.user_id)
    return _with_public_url(job)


async def _notify_applicants_of_closure(session: AsyncSession, job: Job) -> None:
    """Tell everyone with a live application that the role has closed.

    They applied and are now waiting for an answer that is not coming through
    the usual route. Leaving them to work it out from a link that stopped
    working is the exact silence the Updates feed exists to end.

    THREE EXCLUSIONS, ALL DELIBERATE. Archived links are not live applications.
    Terminal ones (`rejected`, `joined`) have already had their outcome, and a
    closure notice after a rejection reads as a second rejection. `sourced`
    rows never applied, so there is nothing for them to be told the end of --
    and telling somebody a role closed when they did not know it was open is
    only confusing.

    One statement rather than a row-by-row loop: a popular posting has hundreds
    of applicants and this runs inside the request that closed the job.
    """
    from app.services import candidate_updates  # noqa: PLC0415

    template = candidate_updates.TEMPLATES[candidate_updates.JOB_CLOSED]
    tenant_name = (
        await session.execute(select(Tenant.name).where(Tenant.id == job.tenant_id))
    ).scalar_one_or_none()
    body = template.body.format(
        job=job.title or "this role",
        company=tenant_name or "the hiring team",
        link_id="",
        job_id="",
    )
    await session.execute(
        text(
            """
            INSERT INTO candidate_updates
                (id, candidate_id, tenant_id, job_id, job_candidate_link_id,
                 kind, title, body, link_path, emailed)
            SELECT gen_random_uuid(), l.candidate_id, l.tenant_id, l.job_id,
                   l.id, :kind, :title, :body,
                   '/portal/applications?application=' || l.id::text,
                   FALSE
              FROM job_candidate_links l
             WHERE l.job_id = :job_id
               AND l.archived_at IS NULL
               AND l.status NOT IN ('sourced', 'rejected', 'joined')
            """
        ),
        {
            "kind": template.kind,
            "title": template.title,
            "body": body,
            "job_id": str(job.id),
        },
    )


def _retention_out(job: Job) -> AssessmentRetentionOut:
    """One serializer for all three retention routes.

    The refusal sentence travels from `job_assessment_retention` rather than
    being written here, so the screen, the 410 body and the gate can never
    disagree about what a closed job's records are: one author, the rule
    `components/permission-notice.tsx` already follows on the other side.
    """
    state = job_assessment_retention.describe(job)
    return AssessmentRetentionOut(
        state=state.state,
        closed_at=state.closed_at,
        purge_due_at=state.purge_due_at,
        purged_at=state.purged_at,
        dispute_open=state.dispute_open,
        days_remaining=state.days_remaining,
        message=state.message(),
        dispute_reason=(
            job.assessment_dispute_reason if state.dispute_open else None
        ),
    )


@router.get(
    "/{job_id}/assessment-retention", response_model=AssessmentRetentionOut
)
async def assessment_retention(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> AssessmentRetentionOut:
    """Where this job's assessment data is in its thirty day lifecycle.

    Readable by anyone who may open the review screen, DELIBERATELY WIDER than
    the dispute capability that can act on it. A recruiter who clicks into a
    closed job and finds the report gone has to be told what happened and by
    when it can still be recovered; answering that question only to the person
    who can already recover it would leave everybody else with a 410 and no
    explanation, which is the exact silence the Updates feed exists to end.

    It carries no candidate detail and no count of assessed people, only
    dates. See `AssessmentRetentionOut`.
    """
    job = await _get_visible_job(session, user, job_id)
    return _retention_out(job)


@router.post(
    "/{job_id}/assessment-dispute", response_model=AssessmentRetentionOut
)
async def open_assessment_dispute(
    job_id: uuid.UUID,
    body: AssessmentDisputeIn,
    user: CurrentUser = Depends(
        require_capability(caps.RETRIEVE_DISPUTED_ASSESSMENT)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> AssessmentRetentionOut:
    """THE DISPUTE PATH: the one way back into a closed job's assessment data.

    It is an UNLOCK rather than a second reader, and that is rule 5 doing the
    design. A dispute endpoint that returned the report itself would be a
    second PRISM serializer, a second transcript pager and a second set of
    no-numbers decisions, and the day the two disagreed the recruiter and the
    disputing party would be reading different documents about the same
    person. Instead this records the dispute on the job, and the existing
    report, PDF and transcript routes answer again for a caller who holds this
    capability, through the one gate in
    `job_assessment_retention.require_readable`.

    IT DOES NOT EXTEND THE THIRTY DAYS. The window is a promise made to the
    candidate as well as to the employer, and a dispute that could push the
    deletion out would let an unresolved argument keep a person's assessment
    alive indefinitely. A dispute opened on day twenty nine has one day, and
    the response says so in `days_remaining`.

    Refused once the data is gone, with the server's own sentence: offering to
    open a dispute over records that no longer exist would be a control that
    can only ever disappoint.
    """
    job = await _get_visible_job(session, user, job_id)
    state = job_assessment_retention.describe(job)
    if state.state == job_assessment_retention.STATE_LIVE:
        raise HTTPException(
            status_code=409,
            detail=(
                "This job is still open, so its assessment records are "
                "available in the normal way."
            ),
        )
    if not state.retrievable:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=state.message())
    previous = _retention_out(job).model_dump(mode="json")
    now = datetime.now(timezone.utc)
    job.assessment_dispute_opened_at = now
    job.assessment_dispute_opened_by = user.user_id
    job.assessment_dispute_reason = body.reason
    await session.flush()
    await record_action(
        session,
        action=job_assessment_retention.ACTION_DISPUTE_OPENED,
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        previous_state=previous,
        new_state=_retention_out(job).model_dump(mode="json"),
        # The opener's own words, recorded with the act rather than beside it.
        metadata={"reason": body.reason},
    )
    logger.info(
        "jobs.assessment_dispute_opened job_id=%s by=%s", job.id, user.user_id
    )
    return _retention_out(job)


@router.delete(
    "/{job_id}/assessment-dispute", response_model=AssessmentRetentionOut
)
async def close_assessment_dispute(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(
        require_capability(caps.RETRIEVE_DISPUTED_ASSESSMENT)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> AssessmentRetentionOut:
    """Lock the records again once the dispute is settled.

    Exists because the unlock otherwise runs to the end of the window by
    default, and an access grant nobody can hand back is one people stop
    treating as exceptional. Closing a dispute that is not open is a no-op
    rather than a 409: the end state the caller asked for is the end state
    they get, and refusing it would only encourage a retry loop.
    """
    job = await _get_visible_job(session, user, job_id)
    if job.assessment_dispute_opened_at is None:
        return _retention_out(job)
    previous = _retention_out(job).model_dump(mode="json")
    job.assessment_dispute_opened_at = None
    job.assessment_dispute_opened_by = None
    # The reason is KEPT. It is why the records were read, and clearing it
    # would leave the audit trail as the only place a reviewer could find out,
    # which is exactly why the columns exist in the first place.
    await session.flush()
    await record_action(
        session,
        action=job_assessment_retention.ACTION_DISPUTE_CLOSED,
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        previous_state=previous,
        new_state=_retention_out(job).model_dump(mode="json"),
    )
    logger.info(
        "jobs.assessment_dispute_closed job_id=%s by=%s", job.id, user.user_id
    )
    return _retention_out(job)


@router.get(
    "/setup/status-hygiene",
    response_model=status_hygiene.StatusHygieneSummary,
)
async def status_hygiene_precheck(
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> status_hygiene.StatusHygieneSummary:
    """Operational Hygiene pre-check for new Job Setup.

    Provenance: add-features-specdoc (2026-09-05), "Operational Hygiene".
    Lists this tenant's earlier jobs (closed, or past their posting window)
    that still carry applications in a non-terminal pipeline stage, so the
    Create Job screen can remind the team to finish deciding them.

    DELIBERATELY ADVISORY. The locked owner decision is a strong reminder and
    never a hard block: an urgent hire must not stall on unrelated old-job
    cleanup. Nothing here is wired into POST /jobs as a gate, and nothing may
    be -- a non-empty summary changes what the recruiter is TOLD, never what
    they may do. Behind CREATE_JOB because it exists solely to precede that
    action, and inventing a new capability for a read the creator already
    implies would be a seeding migration for nothing.
    """
    return await status_hygiene.unresolved_summary(session, user.tenant_id)


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


#: The refusal a grade change gets once a candidate has started (D5).
GRADE_LOCKED_DETAIL = (
    "The grade is locked because a candidate has started the assessment. It "
    "decides the question budget every candidate on this job receives."
)


@router.patch("/{job_id}", response_model=JobDetailOut)
async def patch_job(
    job_id: uuid.UUID,
    body: JobPatchIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_JOB_DESCRIPTION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """Partial edit of a job's METADATA from the job page (spec §3.1).

    True PATCH semantics: a field the caller did not send is untouched. For the
    three narrative sections that distinction carries meaning: sending
    `about_company: null` CLEARS the per-job override so the job falls back to
    the company profile, which is different from not mentioning the field.
    `model_fields_set` is what separates them.

    The JD itself is NOT edited here: `PATCH /jobs/{id}/jd` is its one path.

    THE GRADE LOCKS WITH THE SKILLS (D5). It decides the question budget
    every candidate on the job receives, so once a candidate has started the
    assessment a change is a 409, asked of the snapshot TABLE. A value equal
    to the current grade is not a change and is not refused.
    """
    job = await _get_visible_job(session, user, job_id)
    sent = body.model_fields_set

    if "grade" in sent and body.grade is not None and body.grade != job.assessment_grade:
        # The skills lock a candidate's start takes too, so a grade change and
        # a first start can never interleave: either the snapshot carries the
        # new grade, or the change is refused.
        await locks.advisory_xact_lock(session, locks.SKILLS, job.id)
        if await assessment_contract.is_locked(session, job.id):
            raise HTTPException(status_code=409, detail=GRADE_LOCKED_DETAIL)

    if "title" in sent and body.title is not None:
        job.title = body.title
    if "department" in sent:
        job.department = body.department
    if "requirement_period" in sent:
        job.requirement_period = body.requirement_period
    for field in ("experience_min_years", "experience_max_years"):
        if field in sent:
            setattr(job, field, getattr(body, field))
    if "grade" in sent and body.grade is not None:
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
    arrival: str | None = Query(
        default=None,
        pattern="^(new|considered)$",
        description=(
            "Narrow to the New Candidates section (people who applied after "
            "the last assessment round on this job) or to everyone else."
        ),
    ),
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> RankedCandidatesOut:
    """The job page's inline candidate table: ranked, paginated, in words.

    The order is ONE key derived in SQL (`services/yukti/ranking`): the Yukti
    resume check, blended with the Tatva Assessment once a report exists,
    capped after the blend when a Must-have failed, then arrival, then id. It
    is a TOTAL order, so page boundaries stay stable across requests. No
    number appears in the response (D3): the AI Match is a grade word, its
    evidence tags are text and its provenance is sentences, and the page's
    `ranking_header` says in words how the order is made.

    Every row carries `profile_age`. After a renewal, applicants from the
    previous window read as Old Profiles: still listed, still ranked, still
    openable; the distinction is provenance and billing, never access.

    Every row also carries `is_new_candidate`, and the page carries
    `new_candidate_count` (workflow section 32), computed over the WHOLE job
    rather than over the rows on this page.
    """
    job = await _get_visible_job(session, user, job_id)
    grade = job.assessment_grade or "non_managerial"
    result = await job_candidates.ranked_candidates(
        session,
        job.id,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
        profile_age_filter=profile_age,
        arrival_filter=arrival,
    )
    return RankedCandidatesOut(
        job_id=job.id,
        grade=grade,
        ranking_header=result.ranking_header,
        results=[RankedCandidateOut.model_validate(row) for row in result.rows],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
        has_next=result.has_next,
        has_previous=result.has_previous,
        range_start=result.range_start,
        range_end=result.range_end,
        new_candidate_count=result.new_candidate_count,
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

    The generator itself degrades rather than failing: an outage produces a
    template document the recruiter can edit. What CANNOT be degraded here is
    not reaching the generator at all, which is answered 503, because an empty
    draft returned for an unreachable agent would present "we could not ask"
    as "this is what it wrote".

    THE CREATE GATES RUN FIRST (Vivekium release). The credit gate and Gate 1
    (the Company Profile) used to run only at `POST /jobs`, AFTER the model had
    already been paid to write a draft the recruiter could then never save.
    Both are asked here BEFORE the writer is reached, with the same sentences.
    """
    await _require_create_gates(session, user.tenant_id)
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
    try:
        generated = await agent_client.generate_jd_document(brief)
    except agent_client.AgentInvokeError as exc:
        raise HTTPException(
            status_code=503,
            detail="The job description writer could not be reached. Try again in a moment.",
        ) from exc

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
        classification_signals=result.explanation,
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
    return f"{_PLACEHOLDER_LOCAL_PREFIX}{job_id.hex[:8]}.{token}{PLACEHOLDER_EMAIL_DOMAIN}"


#: The two halves of a placeholder address, as constants, because the
#: invitation route has to RECOGNISE one. Mailing `@placeholder.invalid` sends
#: nothing, bounces nowhere, and writes an `email_log` row claiming a candidate
#: was contacted -- so the recruiter would be told the invitation went out and
#: would never hear back, with nothing anywhere explaining why.
_PLACEHOLDER_LOCAL_PREFIX = "databank+"
PLACEHOLDER_EMAIL_DOMAIN = "@placeholder.invalid"


def is_placeholder_email(email: str | None) -> bool:
    """True when this address was invented for a resume that carried none."""
    return bool(email) and str(email).endswith(PLACEHOLDER_EMAIL_DOMAIN)


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
    # parse (LLM extraction + embedding) is still a background task and still runs
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

    candidate = await candidate_identity.find_canonical_by_email(session, email)
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
    # Gate 5. NOT `applied`: the recruiter moved a file out of their own
    # databank, and the person has not read this job, wanted it, or answered a
    # single question about their notice period. `sourced` has exactly one
    # forward edge, `applied`, which the candidate takes themselves in the
    # portal, so nothing here can invite them to an assessment or shortlist
    # them by mistake. `start_sourced` also writes the history row this path
    # used to skip (audit Part 1 #5).
    await hiring_pipeline.start_sourced(
        session,
        link,
        actor_user_id=user.user_id,
        remarks="Uploaded to the job from the recruitment team's databank",
    )
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

    PARTIAL SUCCESS IS THE CONTRACT. Each file is handled on its own, inside
    its own savepoint, and every file gets a result row, so one corrupt PDF
    costs the recruiter that one file and not the other 24: a file that fails
    after writing a row rolls back its own rows and leaves the others and the
    request transaction intact. Nothing here parses a resume inline: each
    accepted file enqueues the existing `parse_resume` task AFTER the commit,
    so no parse can start on a row the request then rolls back. The parse
    dispatches `pickready.yukti_score_profile`, which reads that one link
    against the job's saved skills; a job-wide AI Matching run here would
    re-read every other candidate on the job to rank the new ones.

    Databank candidates go through IDENTICAL AI parsing, embedding, matching
    and assessment to applied and sourced candidates. `source_type` is a tag
    for display and filtering; no behaviour anywhere branches on it.

    (claude.md rule 7 still holds and is untouched: it exempts databank
    profiles from the EMPLOYER VERIFICATION flow, which keys off `source`, and
    the questionnaire it once named no longer exists.)

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
            async with session.begin_nested():
                results.append(
                    await _store_one_databank_resume(session, user, job, upload)
                )
        except Exception as exc:  # noqa: BLE001 -- one bad file, not a failed batch
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
        dispatch_after_commit(
            session, "pickready.parse_resume", args=[str(result.profile_id)]
        )

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


@router.post(
    "/{job_id}/candidates/databank/invite",
    response_model=DatabankInviteOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invite_databank_candidates(
    job_id: uuid.UUID,
    body: DatabankInviteIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> DatabankInviteOut:
    """Workflow section 12: ask sourced candidates to sign in and apply.

    WHAT THIS DOES NOT DO, AND THAT IS THE POINT. It does not move anybody into
    the pipeline, create an application, or start an assessment. Gate 5 says a
    databank candidate is not an applicant until they complete the flow
    themselves, so all this does is send an email. The link goes to the public
    application page for this job; the candidate signs in, finishes their
    profile, answers the mandatory fields and applies, exactly like anybody who
    found the role on their own.

    That is also why there is no invitation STATE. The candidate's stage stays
    `sourced` until they apply, and `email_log` already records who was written
    to, when, and with what copy -- a second stage meaning "we emailed them"
    would be a stage nothing can leave except by the same edge, and it would
    let a recruiter mistake having sent an email for having got a reply.

    Every skip is REPORTED. A batch that silently drops the unreachable rows
    tells the recruiter twenty invitations went out when eleven did.
    """
    job = await _get_visible_job(session, user, job_id)
    window = job_posting.describe(job)
    if not window.is_active:
        # The link in the mail would 404. Sending it anyway is worse than
        # refusing: the candidate acts on it, fails, and blames themselves.
        raise HTTPException(
            status_code=409,
            detail=(
                "This job is not open to applications right now, so an "
                "invitation would link to a page the candidate cannot use."
            ),
        )

    rows = (
        await session.execute(
            select(JobCandidateLink, Candidate)
            .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
            .where(
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.id.in_(body.link_ids),
            )
        )
    ).all()
    by_id = {link.id: (link, candidate) for link, candidate in rows}

    tenant = await session.get(Tenant, job.tenant_id)
    company_name = tenant.name if tenant else "our team"
    application_link = public_job_url(job.id)

    results: list[DatabankInviteResultOut] = []
    queued: list[uuid.UUID] = []
    for link_id in body.link_ids:
        found = by_id.get(link_id)
        if found is None:
            results.append(
                DatabankInviteResultOut(
                    link_id=link_id,
                    invited=False,
                    reason="This candidate is not on this job.",
                )
            )
            continue
        link, candidate = found
        if link.status != hiring_pipeline.SOURCED:
            results.append(
                DatabankInviteResultOut(
                    link_id=link_id,
                    invited=False,
                    candidate_name=candidate.full_name,
                    reason=(
                        "This candidate has already applied, so there is "
                        "nothing to invite them to."
                    ),
                )
            )
            continue
        if is_placeholder_email(candidate.email) or not candidate.email:
            results.append(
                DatabankInviteResultOut(
                    link_id=link_id,
                    invited=False,
                    candidate_name=candidate.full_name,
                    reason=(
                        "No email address was found on this resume, so there "
                        "is nowhere to send the invitation. Add one on the "
                        "candidate before inviting them."
                    ),
                )
            )
            continue

        log_id = await _queue_databank_invitation(
            session,
            user,
            job=job,
            link=link,
            candidate=candidate,
            company_name=company_name,
            application_link=application_link,
        )
        queued.append(log_id)
        results.append(
            DatabankInviteResultOut(
                link_id=link_id,
                invited=True,
                candidate_name=candidate.full_name,
            )
        )

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="databank_candidates_invited",
        target_type="job",
        target_id=job.id,
        metadata={
            "requested": len(body.link_ids),
            "invited": len(queued),
            "skipped": len(body.link_ids) - len(queued),
        },
    )
    return DatabankInviteOut(
        job_id=job.id,
        requested=len(body.link_ids),
        invited=len(queued),
        skipped=len(body.link_ids) - len(queued),
        results=results,
    )


async def _queue_databank_invitation(
    session: AsyncSession,
    user: CurrentUser,
    *,
    job: Job,
    link: JobCandidateLink,
    candidate: Candidate,
    company_name: str,
    application_link: str,
) -> uuid.UUID:
    """Draft one invitation and queue it through the one outbox writer.

    `email_outbox.queue_candidate_email` records the `email_log` row, threads
    it into the tenant's conversation with the candidate (a person sent it),
    resolves the tenant's default sender and dispatches the send AFTER the
    commit, so a rolled-back request sends nothing. It is NOT keyed to a
    status change, because no status changes.
    """
    from app.models.email_log import EMAIL_TYPE_DATABANK_INVITATION  # noqa: PLC0415
    from app.services import email_outbox, lifecycle_email  # noqa: PLC0415

    draft = await lifecycle_email.draft(
        EMAIL_TYPE_DATABANK_INVITATION,
        {
            "candidate_name": candidate.full_name or "there",
            "job_title": job.title,
            "company_name": company_name,
            "application_link": application_link,
        },
        session=session,
    )
    log = await email_outbox.queue_candidate_email(
        session,
        tenant_id=job.tenant_id,
        email_type=EMAIL_TYPE_DATABANK_INVITATION,
        recipient_email=candidate.email,
        candidate_id=candidate.id,
        job_id=job.id,
        link_id=link.id,
        subject=draft["subject"],
        body=draft["body"],
        generated_by_ai=draft["generated_by_ai"],
        edited_by_human=False,
        sent_by=user.user_id,
        candidate_name=candidate.full_name,
    )
    if log is None:
        # No dedupe key is passed, so the insert cannot be skipped as a
        # duplicate; None here means the outbox contract changed underneath.
        raise RuntimeError(
            f"the databank invitation for link {link.id} was not queued"
        )
    # The Updates feed. This is the ONE kind that exists for a candidate with
    # no application, which is exactly the person most likely to miss the
    # email: they have never heard of us, so our address has no history in
    # their inbox and every spam filter is against us.
    await candidate_updates.record(
        session,
        kind=candidate_updates.INVITED_TO_APPLY,
        candidate_id=candidate.id,
        tenant_id=job.tenant_id,
        job_id=job.id,
        link_id=link.id,
        job_title=job.title,
        company_name=company_name,
        emailed=True,
    )
    return log.id


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
        job.posting_start_date, job.posting_end_date, closed_at=job.closed_at
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
