"""Job setup: the setup checklist, the Job SWOT Analysis and the Skills step.

Mounted under `/api/v2/assessments`, the prefix these URLs always had, so the
SWOT routes moved here from `api/assessments.py` with every URL unchanged.
The Tatva matrix editor (`/framework` and its six siblings) and the Matching
category editor are DELETED (Vivekium release, owner decision D1); the Skills
step replaces both, over `services/skills`, and what a candidate is assessed
against is `services/assessment_contract`.

AUTHORIZATION, ROUTE BY ROUTE, AND WHY IT IS NOT ONE DEPENDENCY
---------------------------------------------------------------
Every bucket of a job's skills is written under its OWN capability
(`capabilities.SKILL_BUCKET_CAPABILITY`), and which bucket a write touches is
in the BODY (an add) or on the ROW (a rename, a move, a remove). A route
dependency cannot see either, so the skills writes carry a flat gate as their
dependency (`require_capability(VIEW_COMPANY_JOBS)`: may this person work on
jobs at all) and then run RBAC 3's WHOLE chain in the handler through
`_require`, the SAME `rbac.authorize` call every other job route uses: tenant,
the 24 ceiling (the Recruiter's NEVER cells), the grant, assignment scope, and
the lifecycle rules, including `skills_locked`.

* add, paste: the target bucket's capability.
* rename, remove: the capability of the bucket the skill is in.
* move: the source AND the target bucket's capability.
* draft: all three bucket capabilities, because a draft writes all three.
* save: `finalize_role_definition` AND all three bucket capabilities.

The read routes use `require_capability(VIEW_COMPANY_JOBS)`, the reasoning the
SWOT read has always carried (below), and resolve `can_edit` / `can_save` for
THIS person on THIS job with the same `_decide` calls the writes enforce with,
so the screen and the API cannot disagree.

A FROZEN JOB ANSWERS 409 WITH THE FROZEN SENTENCE, NOT 403
----------------------------------------------------------
Once a candidate has genuinely applied, the JD and the skills are the
contract (CONTRACT v10; the first START until 2026-09-28). `rbac` refuses the
skill capabilities with reason `skills_locked`, and `_require` turns exactly
that reason into 409 with the frozen sentence
(`assessment_contract.FROZEN_DETAIL`, dated): a freeze is a STATE of the job,
not a permission the person lacks, and "Not permitted" would send them to ask
an administrator for a grant nobody can give. The services re-check inside
the skills advisory lock, because an application can land between the
decision and the write.

THE FINAL JOB POSTING
---------------------
`GET /jobs/{id}/posting-preview` is what the candidate-facing posting says,
for the recruiter before publishing: the JD, the skills by NAME in three
buckets, the company narrative. `assessment_contract.posting_skills` is the
one builder, shared with the public apply page, the portal and the employer
page. Never an evidence line, a priority or the role summary.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.assessment import JobCompetency
from app.models.job import Job
from app.models.job_setup import SWOT_ANALYSIS_SECTIONS, JobSwotAnalysis
from app.models.user import User
from app.schemas.assessments import (
    JobSetupOut,
    PostingPreviewOut,
    SkillAddIn,
    SkillBucketPermissionsOut,
    SkillBucketsOut,
    SkillBulkAddIn,
    SkillOut,
    SkillPatchIn,
    SkillsDraftIn,
    SkillsOut,
    SwotAnalysisGenerateIn,
    SwotAnalysisOut,
    SwotAnalysisSectionsIn,
)
from app.services import assessment_contract, rbac, skills, swot_analysis
from app.services import capabilities as caps
from app.services.audit import record_action
from app.services.hiring import pipeline_halt

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Shared helpers ───────────────────────────────────────────────────────────


async def _staff_job(session: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _principal(user: CurrentUser) -> rbac.Principal:
    return rbac.Principal(user_id=user.user_id, tenant_id=user.tenant_id, role=user.role)


async def _resource(session: AsyncSession, job: Job) -> rbac.Resource:
    resource = await rbac.load_job_resource(session, job.id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return resource


async def _decide(
    session: AsyncSession, user: CurrentUser, capability: str, resource: rbac.Resource
) -> rbac.Authorization:
    return await rbac.authorize(session, _principal(user), capability, resource)


async def _require(
    session: AsyncSession, user: CurrentUser, capability: str, resource: rbac.Resource
) -> None:
    """RBAC 3's whole chain for one capability, with the lock answered as 409."""
    decision = await _decide(session, user, capability, resource)
    if decision.reason == "skills_locked":
        detail = await assessment_contract.frozen_reason(
            session, uuid.UUID(str(resource.job_id))
        )
        if detail is None:
            # `rbac` read a snapshot that this read cannot see: the two answers
            # come from one table in one transaction, so this is a defect.
            raise RuntimeError(f"job {resource.job_id} is locked with no readable snapshot")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    rbac.raise_for(decision, capability)


def _bucket_capability(bucket: str) -> str:
    capability = caps.SKILL_BUCKET_CAPABILITY.get(bucket)
    if capability is None:
        raise HTTPException(status_code=422, detail=f"{bucket!r} is not a skills bucket.")
    return capability


async def _skill_row(session: AsyncSession, job: Job, skill_id: uuid.UUID) -> JobCompetency:
    """The active skill on THIS job, or the same 404 the service would give."""
    row = await session.get(JobCompetency, skill_id)
    if row is None or row.job_id != job.id or not row.is_active:
        raise HTTPException(status_code=404, detail="That skill is not on this job.")
    return row


def _skills_error(exc: Exception) -> HTTPException:
    """One mapping for every refusal the skills service raises.

    Each `SkillsError` carries its own status and a sentence the reviewer
    reads verbatim. A frozen job is 409 with the dated frozen sentence. A halted pipeline stage
    is 503 naming the stage (`pipeline_halt.http_detail`), because an operator
    kill switch is not the reviewer's doing and not something they can fix by
    editing.
    """
    if isinstance(exc, skills.SkillsError):
        return HTTPException(status_code=exc.http_status, detail=exc.detail)
    if isinstance(exc, assessment_contract.SkillsLocked):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail)
    if isinstance(exc, pipeline_halt.PipelineHalted):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=pipeline_halt.http_detail(exc),
        )
    raise TypeError(f"not a skills refusal: {type(exc).__name__}")


_SKILLS_REFUSALS = (
    skills.SkillsError,
    assessment_contract.SkillsLocked,
    pipeline_halt.PipelineHalted,
)


async def _skills_out(session: AsyncSession, user: CurrentUser, job: Job) -> SkillsOut:
    """The Skills step for THIS person on THIS job. Names and states only."""
    view = await skills.view(session, job)
    resource = await _resource(session, job)
    can_edit = {
        bucket: (await _decide(session, user, capability, resource)).allowed
        for bucket, capability in caps.SKILL_BUCKET_CAPABILITY.items()
    }
    can_finalize = (
        await _decide(session, user, caps.FINALIZE_ROLE_DEFINITION, resource)
    ).allowed
    return SkillsOut(
        job_id=job.id,
        draft_status=view.draft_status,
        draft_error=view.draft_error,
        saved=view.saved,
        locked=view.locked,
        max_per_bucket=view.max_per_bucket,
        redraft_available=view.redraft_available,
        human_authored_names=list(view.human_authored_names),
        blocking_reason=view.blocking_reason,
        buckets=SkillBucketsOut(
            **{
                bucket: [
                    SkillOut(
                        id=entry.id,
                        name=entry.name,
                        source=entry.source,
                        from_swot=entry.from_swot,
                    )
                    for entry in view.buckets.get(bucket, ())
                ]
                for bucket in skills.BUCKETS
            }
        ),
        can_edit=SkillBucketPermissionsOut(**can_edit),
        can_save=can_finalize and all(can_edit.values()),
        frozen_reason=view.frozen_reason,
        draft_blocked_reason=view.draft_blocked_reason,
    )


async def _after_skills_write(job: Job) -> None:
    """The public posting shows the SAVED skills, so any write that can change
    them (or un-save them) drops the cached public page. Everyone but the
    author is bounded by the page's TTL anyway; this is so the author sees
    their own change at once, the rule every other posting edit follows."""
    from app.api.jobs import invalidate_public_job  # noqa: PLC0415

    await invalidate_public_job(job.id)


# ── The setup checklist ──────────────────────────────────────────────────────


@router.get("/jobs/{job_id}/setup", response_model=JobSetupOut)
async def get_job_setup(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobSetupOut:
    """Where a job stands on its way to publication and to candidates.

    READS ONLY. The retired setup GET dispatched a matrix compile from a read,
    before the commit; nothing here writes or dispatches. A lost SWOT
    generation or skills draft READS as failed (derived, never written), and
    `pickready.reconcile_job_setup` is what repairs a lost draft.
    """
    # Imported here: `api.jobs` owns the publish gate and imports nothing from
    # this module, and the one sentence the checklist and the 409 both show
    # must come from the one function that decides it.
    from app.api.jobs import _publication_blocked, has_publishable_jd  # noqa: PLC0415

    job = await _staff_job(session, user, job_id)
    swot = await swot_analysis.get(session, job)
    swot_status = (
        swot_analysis.effective_status(swot)[0] if swot is not None else "not_generated"
    )
    draft_status, _draft_error = skills.effective_draft_state(job)
    frozen_at = await assessment_contract.frozen_since(session, job.id)
    frozen = frozen_at is not None
    published = job.ratified_at is not None
    return JobSetupOut(
        job_id=job.id,
        jd_ready=has_publishable_jd(job),
        swot_status=swot_status,
        swot_saved=swot_analysis.is_saved(swot),
        skills_draft_status=draft_status,
        skills_saved=await assessment_contract.skills_saved(session, job.id),
        skills_locked=frozen,
        grade_locked=frozen,
        published=published,
        ready_for_candidates=job.assessment_status == skills.READY_FOR_CANDIDATES,
        publish_blocked_reason=(
            None if published else await _publication_blocked(session, job)
        ),
        frozen=frozen,
        frozen_at=frozen_at,
        frozen_reason=(
            assessment_contract.frozen_detail(frozen_at) if frozen_at is not None else None
        ),
    )


def experience_band(low: int | None, high: int | None) -> str | None:
    """The experience band in the words the posting prints, or None.

    Years of experience are a requirement of the role, not an assessment
    signal, so the number is printed the way the JD itself prints it.
    """
    if low is not None and high is not None:
        return f"{low} to {high} years"
    if low is not None:
        return f"at least {low} years"
    if high is not None:
        return f"up to {high} years"
    return None


@router.get("/jobs/{job_id}/posting-preview", response_model=PostingPreviewOut)
async def get_posting_preview(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> PostingPreviewOut:
    """The Final Job Posting as the candidate will read it (CONTRACT v10).

    READS ONLY. Before the freeze the skills are the job's CURRENT active
    ones (`skills_saved` says whether they are the saved set, which is what
    the public posting will show); once frozen they are the snapshot's. Skill
    NAMES only, in alphabetical order inside each bucket.
    """
    # Imported here for the reason `get_job_setup` gives.
    from app.api.jobs import (  # noqa: PLC0415
        _company_sections,
        _publication_blocked,
        jd_markdown_for,
        posting_skill_buckets,
        public_job_url,
        resolve_jd_sections,
    )
    from app.models.tenant import Tenant  # noqa: PLC0415
    from app.services.job_candidates import grade_label  # noqa: PLC0415

    job = await _staff_job(session, user, job_id)
    postings = await assessment_contract.posting_skills(session, [job], include_unsaved=True)
    resolved, _overridden = resolve_jd_sections(
        job, await _company_sections(session, job.tenant_id)
    )
    tenant = await session.get(Tenant, job.tenant_id)
    frozen_at = await assessment_contract.frozen_since(session, job.id)
    published = job.ratified_at is not None
    return PostingPreviewOut(
        job_id=job.id,
        title=job.title,
        department=job.department,
        grade=job.assessment_grade or "non_managerial",
        grade_label=grade_label(job.assessment_grade),
        experience_band=experience_band(job.experience_min_years, job.experience_max_years),
        jd_markdown=jd_markdown_for(job) or None,
        company_name=tenant.name if tenant is not None else None,
        about_company=resolved["about_company"],
        work_life=resolved["work_life"],
        benefits=resolved["benefits"],
        skill_buckets=posting_skill_buckets(postings[job.id]),
        skills_saved=await assessment_contract.skills_saved(session, job.id),
        published=published,
        public_application_url=(
            public_job_url(job.id) if published and job.archived_at is None else None
        ),
        publish_blocked_reason=(
            None if published else await _publication_blocked(session, job)
        ),
        frozen=frozen_at is not None,
        frozen_at=frozen_at,
        frozen_reason=(
            assessment_contract.frozen_detail(frozen_at) if frozen_at is not None else None
        ),
    )



# ── The Skills step ──────────────────────────────────────────────────────────


@router.get("/jobs/{job_id}/skills", response_model=SkillsOut)
async def get_skills(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    job = await _staff_job(session, user, job_id)
    return await _skills_out(session, user, job)


@router.post("/jobs/{job_id}/skills", response_model=SkillsOut)
async def add_skill(
    job_id: uuid.UUID,
    body: SkillAddIn,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    """Add one skill to a bucket. Re-adding a removed name revives its row;
    adding a name that is already there changes nothing."""
    job = await _staff_job(session, user, job_id)
    await _require(session, user, _bucket_capability(body.bucket), await _resource(session, job))
    try:
        await skills.add(session, job, body.bucket, body.name, actor_user_id=user.user_id)
    except _SKILLS_REFUSALS as exc:
        raise _skills_error(exc) from exc
    await _after_skills_write(job)
    return await _skills_out(session, user, job)


@router.post("/jobs/{job_id}/skills/bulk", response_model=SkillsOut)
async def add_skills_bulk(
    job_id: uuid.UUID,
    body: SkillBulkAddIn,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    """"Paste a list". All or nothing against the limit: a paste that would
    take the bucket past its limit is refused whole, naming how many fit."""
    job = await _staff_job(session, user, job_id)
    await _require(session, user, _bucket_capability(body.bucket), await _resource(session, job))
    try:
        await skills.add_many(
            session, job, body.bucket, body.names, actor_user_id=user.user_id
        )
    except _SKILLS_REFUSALS as exc:
        raise _skills_error(exc) from exc
    await _after_skills_write(job)
    return await _skills_out(session, user, job)


@router.patch("/jobs/{job_id}/skills/{skill_id}", response_model=SkillsOut)
async def edit_skill(
    job_id: uuid.UUID,
    skill_id: uuid.UUID,
    body: SkillPatchIn,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    """Rename a skill, move it to another bucket, or both (rename first).

    A move needs the capability of BOTH buckets: taking a skill out of
    Must-have is as much an edit of Must-have as adding one to it.
    """
    if body.name is None and body.bucket is None:
        raise HTTPException(status_code=422, detail="Send a new name, a new bucket, or both.")
    job = await _staff_job(session, user, job_id)
    row = await _skill_row(session, job, skill_id)
    resource = await _resource(session, job)
    await _require(session, user, _bucket_capability(row.category), resource)
    if body.bucket is not None and body.bucket != row.category:
        await _require(session, user, _bucket_capability(body.bucket), resource)
    try:
        if body.name is not None:
            row = await skills.rename(
                session, job, skill_id, body.name, actor_user_id=user.user_id
            )
        if body.bucket is not None:
            await skills.move(session, job, row.id, body.bucket, actor_user_id=user.user_id)
    except _SKILLS_REFUSALS as exc:
        raise _skills_error(exc) from exc
    await _after_skills_write(job)
    return await _skills_out(session, user, job)


@router.delete("/jobs/{job_id}/skills/{skill_id}", response_model=SkillsOut)
async def remove_skill(
    job_id: uuid.UUID,
    skill_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    """Soft delete: an issued candidate question may reference the row."""
    job = await _staff_job(session, user, job_id)
    row = await _skill_row(session, job, skill_id)
    await _require(session, user, _bucket_capability(row.category), await _resource(session, job))
    try:
        await skills.remove(session, job, skill_id, actor_user_id=user.user_id)
    except _SKILLS_REFUSALS as exc:
        raise _skills_error(exc) from exc
    await _after_skills_write(job)
    return await _skills_out(session, user, job)


@router.post(
    "/jobs/{job_id}/skills/draft",
    response_model=SkillsOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def draft_skills(
    job_id: uuid.UUID,
    body: SkillsDraftIn,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    """The "Draft skills" action: ASK Sutra for a draft (or a redraft). The
    model runs in `pickready.draft_job_skills`, dispatched after the commit;
    the answer is 202 with the skills in the `drafting` state.

    Drafted from the JD, with the saved SWOT as optional context (CONTRACT
    v10); a JD too thin to draft from is a 409 with the fixed sentence. A
    redraft over skills the team wrote is a 409 naming them unless
    `confirm_overwrite` is sent: the UI asks, and nothing is ever re-drafted
    silently. A frozen job is a 409 with the frozen sentence.
    """
    job = await _staff_job(session, user, job_id)
    resource = await _resource(session, job)
    for capability in caps.SKILL_BUCKET_CAPABILITY.values():
        await _require(session, user, capability, resource)
    try:
        await skills.request_draft(
            session, job, requested_by=user.user_id, confirm_overwrite=body.confirm_overwrite
        )
    except _SKILLS_REFUSALS as exc:
        raise _skills_error(exc) from exc
    await _after_skills_write(job)
    return await _skills_out(session, user, job)


@router.post("/jobs/{job_id}/skills/save", response_model=SkillsOut)
async def save_skills(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.FINALIZE_ROLE_DEFINITION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SkillsOut:
    """Save the skills: ONE Sutra call writes the hidden context, then one
    transaction writes it all (`skills.save`).

    Refusals, each a sentence rendered verbatim: 503 when the writer is
    unavailable (it names NO skill, because an outage is not a badly written
    skill, and nothing changed), 422 naming EVERY problem or every skill the
    writer could not describe, 409 when the skills changed during the save or
    are locked. The one recorded exception to rule 4: the context must land in
    the same transaction as the human's save, bounded by the interactive tier.
    """
    job = await _staff_job(session, user, job_id)
    resource = await _resource(session, job)
    await _require(session, user, caps.FINALIZE_ROLE_DEFINITION, resource)
    for capability in caps.SKILL_BUCKET_CAPABILITY.values():
        await _require(session, user, capability, resource)
    try:
        await skills.save(
            session, job, actor_user_id=user.user_id, actor_role=user.role.value
        )
    except _SKILLS_REFUSALS as exc:
        raise _skills_error(exc) from exc
    await _after_skills_write(job)
    return await _skills_out(session, user, job)


# ── The AI-assisted Job SWOT Analysis (2026-09-13 spec, sections 23 to 33) ──
#
# Moved here from `api/assessments.py` with every URL unchanged.
#
# FOUR ROUTES, ONE AUTHORIZATION MODEL, NO NEW PERMISSION SYSTEM (section 27)
# ---------------------------------------------------------------------------
# Reading takes `view_company_jobs`, which is what every other read of a job
# already takes. Writing takes `edit_swot`, through `rbac.require_authorized`,
# so the whole RBAC 3 chain runs on every write: tenant, then the 24 ceiling,
# then the grant, then assignment scope, then lifecycle state. There is
# deliberately no SWOT-only authorization anywhere in this block.
#
# A SWOT EDIT IS NOT REFUSED BY THE FREEZE. The SWOT is internal hiring
# intelligence (CONTRACT v10): editable at any time, never a publication step,
# never shown to a candidate. The contract a candidate is assessed against is
# the immutable snapshot, so editing the document changes nothing frozen. A
# SWOT save NEVER drafts skills: while the job is not frozen the response only
# OFFERS a re-draft (`skills_redraft_available`), which a person asks for.
#
# WHY THE READ USES `require_capability` AND THE WRITES USE `require_authorized`
# ------------------------------------------------------------------------------
# `require_authorized` adds resource SCOPE to the capability question, and for
# three of the five client roles RBAC 24 marks `view_company_jobs` SCOPED. A
# job's creator is now assigned to it (`rbac.assign_creator`), but a Recruiter
# reading a job somebody else created would still be refused the SWOT of a job
# whose JD is on the same page, which is a stricter answer than the job itself
# gives. So the read asks the flat question every sibling read asks, with the
# tenant boundary enforced by RLS and by `_staff_job`.


async def _swot_analysis_out(
    session: AsyncSession,
    user: CurrentUser,
    job: Job,
    row: JobSwotAnalysis,
) -> SwotAnalysisOut:
    """Serialize one analysis, resolving `can_edit` on this job for this user.

    The answer is computed with the SAME call the write routes enforce with
    (`rbac.authorize` over `edit_swot` and this job's resource facts), which is
    what stops the interface and the API disagreeing.
    """
    editor_name = None
    if row.last_modified_by is not None:
        editor = await session.get(User, row.last_modified_by)
        editor_name = editor.full_name if editor is not None else None

    decision = await _decide(session, user, caps.EDIT_SWOT, await _resource(session, job))

    previous = dict(row.previous_json or {})
    # A generation that outlived its window READS as failed, derived and never
    # written, so the tab can never spin for ever on a lost dispatch.
    swot_status, generation_error = swot_analysis.effective_status(row)
    return SwotAnalysisOut(
        job_id=job.id,
        status=swot_status,
        strengths=row.strengths,
        weaknesses=row.weaknesses,
        opportunities=row.opportunities,
        threats=row.threats,
        generated_by=row.generated_by,
        last_generated_at=row.last_generated_at,
        generation_error=generation_error,
        human_edited=row.human_edited,
        last_modified_at=row.last_modified_at,
        last_modified_by_name=editor_name,
        version=row.version,
        can_restore_previous=any(
            str(previous.get(name) or "").strip()
            for name in SWOT_ANALYSIS_SECTIONS
        ),
        can_edit=decision.allowed,
        skills_redraft_available=skills.redraft_available(
            job,
            row,
            locked=await assessment_contract.is_locked(session, job.id),
            any_row=await skills.has_any_row(session, job.id),
        ),
    )


@router.get("/jobs/{job_id}/swot-analysis", response_model=SwotAnalysisOut)
async def get_swot_analysis(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """The SWOT document, in whatever state it is in.

    An empty document is the `not_generated` STATE and not a 404: the tab
    exists for every job, and a reader who may see the job must see the empty
    state rather than an error that reads as "this job is broken".
    """
    job = await _staff_job(session, user, job_id)
    row = await swot_analysis.get_or_create(session, job)
    return await _swot_analysis_out(session, user, job, row)


@router.post(
    "/jobs/{job_id}/swot-analysis/generate",
    response_model=SwotAnalysisOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_swot_analysis(
    job_id: uuid.UUID,
    body: SwotAnalysisGenerateIn,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """ASK for a SWOT draft. The model runs in `pickready.generate_job_swot`.

    Section 28: generation is the same authority as editing, because both
    decide what the document says. Answers 202 with the document in the
    `generating` state; the client re-reads it until the worker has written
    `generated` or `failed`. Refusals happen BEFORE anything is written: 409
    over the team's edits without confirmation (the client resolves it by
    confirming), and 409 with fixed copy when the JD is too thin to draft from.
    The audit row is written by the worker in one insert, naming Bodha and the
    person who asked (RBAC 34).
    """
    job = await _staff_job(session, user, job_id)
    try:
        row, _handle = await swot_analysis.request_generation(
            session,
            job,
            confirm_overwrite=body.confirm_overwrite,
            requested_by=user.user_id,
        )
    except swot_analysis.HumanEditsWouldBeLost as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except swot_analysis.SwotInputInsufficient as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _swot_analysis_out(session, user, job, row)


@router.put("/jobs/{job_id}/swot-analysis", response_model=SwotAnalysisOut)
async def save_swot_analysis(
    job_id: uuid.UUID,
    body: SwotAnalysisSectionsIn,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """Persist the team's edits. This is the write the whole feature exists for."""
    job = await _staff_job(session, user, job_id)
    try:
        row = await swot_analysis.save(
            session,
            job,
            {
                "strengths": body.strengths,
                "weaknesses": body.weaknesses,
                "opportunities": body.opportunities,
                "threats": body.threats,
            },
            editor_id=user.user_id,
            expected_version=body.expected_version,
        )
    except swot_analysis.VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # One INSERT carrying every audit column: a post-flush mutation of the
    # audit row is an UPDATE the application role cannot run (2026-09-20).
    await record_action(
        session,
        action="job_swot_analysis_edited",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        metadata={"version": row.version},
    )
    # CONTRACT v10: a SWOT save never drafts skills. The response OFFERS a
    # re-draft (`skills_redraft_available`); the skills never change silently.
    return await _swot_analysis_out(session, user, job, row)


@router.post("/jobs/{job_id}/swot-analysis/restore", response_model=SwotAnalysisOut)
async def restore_swot_analysis(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """Undo the one destructive action in this feature (section 32)."""
    job = await _staff_job(session, user, job_id)
    try:
        row = await swot_analysis.restore_previous(session, job)
    except swot_analysis.SwotAnalysisError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_action(
        session,
        action="job_swot_analysis_restored",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        metadata={"version": row.version},
    )
    return await _swot_analysis_out(session, user, job, row)
