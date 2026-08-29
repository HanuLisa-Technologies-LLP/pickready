"""Company DNA intake (Layer 2), end to end. spec-doc6 §4.2, decision D3.

WHO OWNS THIS, AND WHY IT IS IN THE CLIENT PORTAL
---------------------------------------------------
Company DNA is tenant-scoped, client-owned data: RBAC §4 lists "AI-generated
hiring intelligence" among the tenant-isolated resources. It therefore cannot
live in ReadyPick's own Business Development console, which sits outside every
client's tenant boundary.

spec-doc6 D3, derived from that specification, settles the rest:

    HR Manager          creates, edits, completes and versions the artifact
    Super Admin         the same, under RBAC §7.5 override authority, recorded
                        in the audit trail as an override
    Recruiter, Hiring
    Manager             read-only, and ONLY the compiled artifact, never the
                        raw session
    Interview Manager   no access
    Candidate           no access, no visibility, not even existence
    Internal BD staff   completion status and version number only, never
                        content, and never across a boundary they may author in

MAPPED ONTO THIS CODEBASE'S ROLES
-----------------------------------
The RBAC specification's "Client Super Admin" is this product's `client` role
(`role_hierarchy.ROLE_LABELS[Role.client] == "Super Admin"`), not the platform
`super_admin`, which is ReadyPick's own owner console and has no tenant. The
specification's "HR Manager" is `hr_manager` and the `recruitment_manager` that
ranks alongside it. Every one of these is an ORG-audience session, so this whole
router is org-scoped and there is no owner-audience write path anywhere in it.

GATING IS DATA, NEVER A ROLE NAME
-----------------------------------
`require_capability(MANAGE_COMPANY_DNA)` and `require_capability(VIEW_COMPANY_DNA)`,
resolved through the same engine every other portal uses (CLAUDE.md rule 3).
The two grants are seeded as global `role_permissions` rows by migration 0060.
There is no `if role ==` in this file. `_is_override` reads the role, and it
decides what the AUDIT ROW SAYS, never who may act: RBAC §7.5 requires a Super
Admin's use of another role's authority to be recognisable in the trail.

CROSS-TENANT READS ARE 404, NEVER 403
---------------------------------------
RBAC §33: obscurity is not authorization, and a 403 confirms the resource
exists. `_resolve_client` compares the path's client id against the tenant on
the caller's own session and answers 404 on any mismatch, before a row is read.

THE SESSION CALLS NO MODEL
----------------------------
The instrument is a fixed Python constant in `services/hiring/company_dna`, the
same argument Layer 1's department models make: a table has an UPDATE, an
UPDATE eventually gets an admin screen, and a client-editable instrument is a
Layer 2 that can rewrite its own questions. Asking the questions in order,
refusing an answer of the wrong kind and restating the compilation are all
deterministic, so the intake works with every provider down, which matters
because a Company DNA artifact constrains every job this client will ever post.

WHAT BODHA DOES HERE
--------------------
Bodha's second mandate. It administers the instrument, refuses an adjective
where the Runbook asks for observable evidence, refuses free text where §16.2
asks for a forced scale, and STATES ITS COMPILED UNDERSTANDING BACK for explicit
confirmation before the session closes. That confirmation is bound to a
fingerprint of the understanding, so a client cannot confirm one reading and
freeze another.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_tenant_db,
    require_capability,
)
from app.core.db import get_session_factory, superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG, AUDIENCE_OWNER
from app.models.enums import Role
from app.models.hiring import CompanyDNA
from app.models.user import User
from app.schemas.company_dna import (
    STATUS_COMPLETE,
    STATUS_DRAFT,
    STATUS_SUPERSEDED,
    CompanyDNACompiledOut,
    CompanyDNACompleteIn,
    CompanyDNACreateIn,
    CompanyDNAOverviewOut,
    CompanyDNAPermissionsOut,
    CompanyDNASessionOut,
    CompanyDNAStatusOut,
    CompanyDNAVersionDetailOut,
    CompanyDNAVersionListOut,
    CompanyDNAVersionOut,
    EvidenceExampleOut,
    IntakeAnswerIn,
    QuestionOut,
    ScorecardBlockOut,
    SectionOut,
    UnderstandingBlockOut,
)
from app.services import rbac
from app.services.audit import audit
from app.services.hiring import company_dna as instrument
from app.services.hiring import dna_compilation

router = APIRouter()

#: The two capability names this router gates on.
#:
#: Defined here rather than in `services/capabilities` because that module is
#: owned elsewhere in this wave. The RBAC engine reads ROWS, and migration 0060
#: seeds them, so these are already authoritative. What their absence from
#: `ALL_CAPABILITIES` costs is that they do not appear in the login response and
#: cannot be pinned per user, which is why the screens read
#: `CompanyDNAPermissionsOut` from the server instead.
MANAGE_COMPANY_DNA = "manage_company_dna"
VIEW_COMPANY_DNA = "view_company_dna"

#: The agent principal recorded alongside the human one on every mutation
#: (RBAC §34: an AI-initiated mutation is attributable to both).
AGENT_NAME = "bodha"

#: What the screen says when a client has no Layer 2 artifact.
#:
#: A REQUIREMENT, STATED, AND NOT A CLAIM ABOUT WHAT IS CURRENTLY ENFORCED.
#: Sutra cannot compile a scorecard without a Layer 2 artifact, so the
#: requirement is real and the wording is spec-doc6 D3's. The ENFORCEMENT is
#: gate G1 in `services/hiring/gates`, which is reached only from
#: `services/miti/pipeline`, which no API route or worker imports yet: Part A
#: is not on a live path until spec-doc6 phases 3 to 5 wire it. So this string
#: says what a person has to do, and nothing here asserts that an evaluation is
#: prevented today, because it is not.
SCORECARD_BLOCK_MESSAGE = (
    "Company DNA required before this job's scorecard can be locked. "
    "Complete it once, and every job you post is evaluated against it."
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


# ── Tenant boundary ──────────────────────────────────────────────────────────


def _resolve_client(user: CurrentUser, client_id: uuid.UUID) -> uuid.UUID:
    """The tenant this request may act on, or 404.

    404 AND NEVER 403. RBAC §33: a 403 tells the caller the id they guessed is
    real, which is exactly the information the check exists to withhold. The
    tenant comes from the caller's own session and the path parameter is only
    ever compared against it, never trusted (RBAC §4's closing rule).
    """
    if user.tenant_id is None or client_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    return user.tenant_id


def _is_override(role: Role) -> bool:
    """Whether this action is the Super Admin using another role's authority.

    AUDIT ANNOTATION, NOT AUTHORIZATION. Who may act is decided entirely by
    `require_capability` above; this only decides what the trail says, because
    RBAC §7.5 requires an override to be recognisable as one.
    """
    return role == Role.client


async def _record(
    session: AsyncSession,
    user: CurrentUser,
    *,
    action: str,
    target_id: uuid.UUID | None,
    extra: dict[str, Any] | None = None,
) -> None:
    """One audit row, carrying BOTH principals.

    RBAC §34: every AI-initiated mutation is attributable to the human on whose
    behalf it was authorized and to the agent that executed it. Bodha runs
    inside this request, under this person's session and this tenant, so both
    are known and both are written.
    """
    metadata: dict[str, Any] = {
        "actor_role": user.role.value,
        "agent": AGENT_NAME,
        "super_admin_override": _is_override(user.role),
    }
    metadata.update(extra or {})
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="company_dna",
        target_id=target_id,
        metadata=metadata,
    )


# ── Reading rows ─────────────────────────────────────────────────────────────


async def _current_complete(
    session: AsyncSession, tenant_id: uuid.UUID
) -> CompanyDNA | None:
    return (
        await session.execute(
            select(CompanyDNA).where(
                CompanyDNA.tenant_id == tenant_id,
                CompanyDNA.is_current.is_(True),
                CompanyDNA.status == STATUS_COMPLETE,
            )
        )
    ).scalar_one_or_none()


async def _open_draft(session: AsyncSession, tenant_id: uuid.UUID) -> CompanyDNA | None:
    return (
        await session.execute(
            select(CompanyDNA)
            .where(CompanyDNA.tenant_id == tenant_id, CompanyDNA.status == STATUS_DRAFT)
            .order_by(CompanyDNA.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _row_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, dna_id: uuid.UUID
) -> CompanyDNA:
    row = (
        await session.execute(
            select(CompanyDNA).where(
                CompanyDNA.id == dna_id, CompanyDNA.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


async def _author_names(
    session: AsyncSession, user_ids: Sequence[uuid.UUID | None]
) -> dict[uuid.UUID | None, str]:
    """Display names for the people who conducted these sessions.

    Keyed on an OPTIONAL id because `conducted_by` is `ON DELETE SET NULL`: an
    HR manager can leave, and their departure must not take the philosophy
    every job for that client is built on with it. A missing person reads as
    absent rather than as a lookup failure.
    """
    wanted = [uid for uid in user_ids if uid is not None]
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(wanted))
        )
    ).all()
    return {row[0]: (row[1] or row[2] or "A former team member") for row in rows}


# ── Rendering ────────────────────────────────────────────────────────────────


def _question_out(question: instrument.Question) -> QuestionOut:
    is_scale = question.kind == instrument.SCALE_QUESTION
    return QuestionOut(
        key=question.key,
        kind=question.kind,
        prompt=question.prompt,
        help_text=question.help_text,
        required=question.required,
        poles=list(question.poles) if question.poles else None,
        scale_min=instrument.SCALE_MIN if is_scale else None,
        scale_max=instrument.SCALE_MAX if is_scale else None,
        options=list(question.options),
    )


def _sections_out(answers: dict[str, Any]) -> list[SectionOut]:
    """The twelve sections, with the client's progress through each.

    The accepted and rejected pairs come off the SECTION rather than from a
    second read of the Runbook. The instrument carries §16's own examples, so
    the question and the example beside it cannot come from two different
    readings of the same section, and the extracted `runbook_data/` copy is
    checked against both by `tests/test_company_dna_runbook_examples.py`.
    """
    progress = {block.key: block for block in dna_compilation.progress(answers)}
    return [
        SectionOut(
            key=section.key,
            title=section.title,
            intent=section.intent,
            questions=[_question_out(q) for q in section.questions],
            answered=progress[section.key].answered,
            total=progress[section.key].total,
            required_answered=progress[section.key].required_answered,
            required_total=progress[section.key].required_total,
            complete=progress[section.key].complete,
            examples=[
                EvidenceExampleOut(rejected=e.rejected, accepted=e.accepted)
                for e in section.examples
            ],
            min_items=section.min_items,
            max_items=section.max_items,
            item_format=section.item_format,
        )
        for section in instrument.SECTIONS
    ]


def _understanding_out(document: dict[str, Any]) -> list[UnderstandingBlockOut]:
    return [
        UnderstandingBlockOut(key=block["key"], title=block["title"], lines=block["lines"])
        for block in dna_compilation.plain_language(document)
    ]


def _session_out(row: CompanyDNA, *, author: str | None) -> CompanyDNASessionOut:
    answers = dict(row.answers_json or {})
    completeness = instrument.completeness(answers)
    next_question = instrument.next_unanswered(answers)
    ready = bool(completeness["complete"])
    understanding = None
    token = None
    if ready:
        document = dna_compilation.compile_document(answers, dna_version=row.version)
        understanding = _understanding_out(document)
        token = dna_compilation.checksum(document)
    return CompanyDNASessionOut(
        id=row.id,
        version=row.version,
        status=row.status,
        created_at=row.created_at,
        authored_by=author,
        sections=_sections_out(answers),
        answers=answers,
        next_question=_question_out(next_question) if next_question else None,
        pending_prompt=row.pending_prompt,
        answered=int(completeness["answered"]),
        required=int(completeness["required"]),
        ready_to_complete=ready,
        understanding=understanding,
        understanding_token=token,
    )


def _compiled_out(row: CompanyDNA, *, author: str | None) -> CompanyDNACompiledOut:
    return CompanyDNACompiledOut(
        version=row.version,
        status=row.status,
        completed_at=row.completed_at,
        authored_by=author,
        understanding=_understanding_out(dict(row.artifact_json or {})),
    )


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get(
    "/clients/{client_id}/company-dna", response_model=CompanyDNAOverviewOut
)
async def read_company_dna(
    client_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(VIEW_COMPANY_DNA)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyDNAOverviewOut:
    """The compiled artifact, plus the open draft for whoever may author it.

    A Recruiter or Hiring Manager holds `view_company_dna` and not
    `manage_company_dna`, so `session` comes back null for them and the raw
    intake never enters the response body at all. That is checked by reading a
    recruiter's actual response in `tests/test_company_dna_authorization.py`,
    not by trusting this docstring.
    """
    tenant_id = _resolve_client(user, client_id)
    can_author = await rbac.has_capability(
        session, tenant_id, user.role, MANAGE_COMPANY_DNA, user.user_id
    )
    current = await _current_complete(session, tenant_id)
    # The draft is LOADED for everybody and RETURNED only to an author, so
    # `draft_open` is truthful for a Recruiter without the session itself ever
    # entering their response body.
    draft = await _open_draft(session, tenant_id)

    names = await _author_names(
        session,
        [row.conducted_by for row in (current, draft) if row is not None],
    )
    compiled = (
        _compiled_out(current, author=names.get(current.conducted_by))
        if current is not None
        else None
    )
    session_out = (
        _session_out(draft, author=names.get(draft.conducted_by))
        if draft is not None and can_author
        else None
    )
    return CompanyDNAOverviewOut(
        client_id=tenant_id,
        has_artifact=current is not None,
        compiled=compiled,
        draft_open=draft is not None,
        session=session_out,
        permissions=CompanyDNAPermissionsOut(
            can_author=can_author,
            can_view_compiled=True,
            can_view_session=can_author,
        ),
        scorecard=ScorecardBlockOut(
            blocked=current is None,
            message="" if current is not None else SCORECARD_BLOCK_MESSAGE,
        ),
    )


@router.post(
    "/clients/{client_id}/company-dna",
    response_model=CompanyDNASessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_company_dna(
    body: CompanyDNACreateIn,
    client_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(MANAGE_COMPANY_DNA)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyDNASessionOut:
    """Open a new draft version of the intake.

    A NEW ROW EVERY TIME, never an edit of a completed one. That is what makes
    "which criteria was this candidate graded under" answerable for every job
    already run under an earlier version, and it is enforced twice: here, and
    by the BEFORE UPDATE trigger migration 0060 puts on the table.

    One draft at a time. A second open draft would give the client two answers
    to "what am I in the middle of", and the completion path would have to pick
    one.
    """
    tenant_id = _resolve_client(user, client_id)
    existing = await _open_draft(session, tenant_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A Company DNA session is already open. Finish or continue that "
                "one before starting another."
            ),
        )

    highest = (
        await session.execute(
            select(func.max(CompanyDNA.version)).where(CompanyDNA.tenant_id == tenant_id)
        )
    ).scalar()
    seed: dict[str, Any] = {}
    if body.copy_from_version is not None:
        source = (
            await session.execute(
                select(CompanyDNA.answers_json).where(
                    CompanyDNA.tenant_id == tenant_id,
                    CompanyDNA.version == body.copy_from_version,
                )
            )
        ).scalar_one_or_none()
        if source is None:
            raise HTTPException(status_code=404, detail="Not found")
        # A revision edits a handful of answers out of the whole instrument.
        # Seeding from the previous version is what makes that true in practice
        # rather than in principle; the source row is not touched.
        seed = dict(source or {})

    row = CompanyDNA(
        tenant_id=tenant_id,
        version=int(highest or 0) + 1,
        is_current=False,
        status=STATUS_DRAFT,
        conducted_by=user.user_id,
        answers_json=seed,
        artifact_json={},
        transcript_json=[],
    )
    session.add(row)
    await session.flush()
    await _record(
        session,
        user,
        action="company_dna_session_started",
        target_id=row.id,
        extra={"version": row.version, "copied_from": body.copy_from_version},
    )
    names = await _author_names(session, [user.user_id])
    return _session_out(row, author=names.get(user.user_id))


@router.post(
    "/clients/{client_id}/company-dna/{dna_id}/messages",
    response_model=CompanyDNASessionOut,
)
async def answer_company_dna_question(
    body: IntakeAnswerIn,
    client_id: uuid.UUID,
    dna_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(MANAGE_COMPANY_DNA)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyDNASessionOut:
    """One turn of the session: an answer in, the next question out.

    THE TWO REFUSALS ARE HERE, AT THE API, AND NOT IN THE UI. A free-text
    answer to a Section 2 forced scale is a 422 (Runbook §16.2 is explicit that
    those are answered on a scale rather than in prose), and an adjective where
    Section 3 asks for observable evidence is a 422 carrying the sentence Bodha
    says back, with the Runbook's own accepted example in it. A rule the
    rendering control enforces is a rule anybody with a terminal is exempt
    from.

    Save-and-resume falls out of this: every accepted answer is persisted on
    the row, so the session survives a closed tab with no separate draft
    mechanism to go stale.
    """
    tenant_id = _resolve_client(user, client_id)
    row = await _row_or_404(session, tenant_id, dna_id)
    if row.status != STATUS_DRAFT:
        raise HTTPException(
            status_code=409,
            detail=(
                "This version is complete and cannot be changed. Start a new "
                "version to revise it."
            ),
        )

    question = instrument.question(body.question_key)
    if question is None:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        value = dna_compilation.validate_answer(question, body.answer)
    except dna_compilation.AnswerRejected as rejected:
        # 422 with what Bodha would say. The client sees the reason and the
        # rewrite, which is the only version of this refusal that teaches
        # anything.
        raise HTTPException(
            status_code=422,
            detail={"question_key": rejected.question_key, "message": rejected.message},
        ) from rejected

    answers = dict(row.answers_json or {})
    answers[question.key] = value
    row.answers_json = answers

    transcript = list(row.transcript_json or [])
    transcript.append(
        {
            "question_key": question.key,
            "prompt": question.prompt,
            "kind": question.kind,
            "at": datetime.now(timezone.utc).isoformat(),
            "by": str(user.user_id),
        }
    )
    row.transcript_json = transcript
    following = instrument.next_unanswered(answers)
    row.pending_prompt = following.prompt if following else None
    await session.flush()
    names = await _author_names(session, [row.conducted_by])
    return _session_out(row, author=names.get(row.conducted_by))


@router.post(
    "/clients/{client_id}/company-dna/{dna_id}/complete",
    response_model=CompanyDNACompiledOut,
)
async def complete_company_dna(
    body: CompanyDNACompleteIn,
    client_id: uuid.UUID,
    dna_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(MANAGE_COMPANY_DNA)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyDNACompiledOut:
    """Compile, confirm and freeze.

    THE CONFIRMATION IS STRUCTURAL. `understanding_token` is the fingerprint of
    the compiled understanding the client was shown, and completion recompiles
    from the answers as they are NOW and compares. A token that no longer
    matches means an answer changed after the client read the restatement, and
    the request is refused with that as the reason. Bodha therefore cannot
    freeze a version nobody confirmed, and it cannot freeze a different version
    from the one they confirmed.

    The supersede is in the same transaction as the freeze. Two statements with
    a gap between them would leave a moment where a client has no current
    philosophy or two, and `uq_company_dna_one_current` would reject the second
    case outright.
    """
    tenant_id = _resolve_client(user, client_id)
    row = await _row_or_404(session, tenant_id, dna_id)
    if row.status != STATUS_DRAFT:
        raise HTTPException(
            status_code=409, detail="This version has already been completed."
        )

    answers = dict(row.answers_json or {})
    completeness = instrument.completeness(answers)
    if not completeness["complete"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Some questions are still unanswered, so there is nothing "
                    "to compile yet."
                ),
                "missing": completeness["missing"],
            },
        )

    document = dna_compilation.compile_document(answers, dna_version=row.version)
    token = dna_compilation.checksum(document)
    if body.understanding_token != token:
        raise HTTPException(
            status_code=409,
            detail=(
                "An answer changed after you read the summary, so what you "
                "confirmed is no longer what this would compile to. Read the "
                "summary again and confirm it."
            ),
        )

    previous = await _current_complete(session, tenant_id)
    if previous is not None:
        previous.status = STATUS_SUPERSEDED
        previous.is_current = False
        await session.flush()

    row.artifact_json = document
    row.status = STATUS_COMPLETE
    row.is_current = True
    row.completed_at = datetime.now(timezone.utc)
    row.pending_prompt = None
    transcript = list(row.transcript_json or [])
    transcript.append(
        {
            "kind": "confirmation",
            "understanding_token": token,
            "at": datetime.now(timezone.utc).isoformat(),
            "by": str(user.user_id),
        }
    )
    row.transcript_json = transcript
    await session.flush()

    await _record(
        session,
        user,
        action="company_dna_completed",
        target_id=row.id,
        extra={
            "version": row.version,
            "supersedes": previous.version if previous is not None else None,
            "artifact_checksum": token,
        },
    )
    names = await _author_names(session, [row.conducted_by])
    return _compiled_out(row, author=names.get(row.conducted_by))


@router.get(
    "/clients/{client_id}/company-dna/versions",
    response_model=CompanyDNAVersionListOut,
)
async def list_company_dna_versions(
    client_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: CurrentUser = Depends(require_capability(MANAGE_COMPANY_DNA)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyDNAVersionListOut:
    """Every version, newest first, with its author and its timestamps.

    Authorship roles only. A version list is a history of who decided what a
    client's hiring philosophy is, which is exactly the thing D3 keeps out of
    the read-only roles' reach; the compiled artifact they DO get is on
    `GET /clients/{id}/company-dna`.
    """
    tenant_id = _resolve_client(user, client_id)
    total = (
        await session.execute(
            select(func.count())
            .select_from(CompanyDNA)
            .where(CompanyDNA.tenant_id == tenant_id)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(CompanyDNA)
                .where(CompanyDNA.tenant_id == tenant_id)
                .order_by(CompanyDNA.version.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    names = await _author_names(session, [row.conducted_by for row in rows])
    return CompanyDNAVersionListOut(
        items=[_version_out(row, names) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def _version_out(
    row: CompanyDNA, names: dict[uuid.UUID | None, str]
) -> CompanyDNAVersionOut:
    document = dict(row.artifact_json or {})
    return CompanyDNAVersionOut(
        version=row.version,
        status=row.status,
        is_current=row.is_current,
        authored_by=names.get(row.conducted_by),
        created_at=row.created_at,
        completed_at=row.completed_at,
        checksum=dna_compilation.checksum(document) if document else None,
    )


@router.get(
    "/clients/{client_id}/company-dna/versions/{version}",
    response_model=CompanyDNAVersionDetailOut,
)
async def read_company_dna_version(
    request: Request,
    client_id: uuid.UUID,
    version: int,
    configuration: bool = Query(
        False,
        description=(
            "Include the numeric engine configuration. Restricted and audited."
        ),
    ),
    user: CurrentUser = Depends(require_capability(MANAGE_COMPANY_DNA)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyDNAVersionDetailOut:
    """One version, in plain language.

    `?configuration=true` additionally returns the numeric engine
    configuration. spec-doc6 D8 puts raw internals behind an authenticated view
    restricted to the Super Admin and the HR Manager and requires it to be
    logged whenever it is read, so it is opt-in per request, gated on the
    authorship capability, and audited here rather than sampled.

    THE RAW SESSION IS NOT HERE AT ALL. Even the authorship roles read the
    answers through the draft session on `GET /clients/{id}/company-dna`, which
    only ever carries the OPEN draft. A completed version's answers are audit
    material and are not served by any route in this router.
    """
    tenant_id = _resolve_client(user, client_id)
    row = (
        await session.execute(
            select(CompanyDNA).where(
                CompanyDNA.tenant_id == tenant_id, CompanyDNA.version == version
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    names = await _author_names(session, [row.conducted_by])
    document = dict(row.artifact_json or {})
    detail = CompanyDNAVersionDetailOut(
        **_version_out(row, names).model_dump(),
        understanding=_understanding_out(document) if document else [],
    )
    if configuration:
        detail.configuration = document
        await _record(
            session,
            user,
            action="company_dna_configuration_read",
            target_id=row.id,
            extra={"version": row.version, "path": request.url.path},
        )
    return detail


@router.get(
    "/clients/{client_id}/company-dna/status", response_model=CompanyDNAStatusOut
)
async def company_dna_status(
    request: Request,
    client_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
) -> CompanyDNAStatusOut:
    """Completion status and version number. No content, ever.

    TWO KINDS OF CALLER, ONE SHAPE. A client's own staff read it to see whether
    their onboarding step is done; ReadyPick's Business Development staff read
    it to see whether a customer they are onboarding has finished. D3 gives BD
    completion status and the version number and nothing else, so this response
    model has no artifact field, no answers field and no author field to leave
    out.

    The branch below is on the token's AUDIENCE, not on a role name: it decides
    which database scope to open, exactly as `get_tenant_db` and
    `get_superadmin_db` do. Both branches then resolve a capability through the
    same RBAC engine, and neither can reach a route that returns content, which
    is what makes "BD may never read the content" structural rather than
    promised. A BD principal calling any other route in this file is refused by
    `get_tenant_db`, which requires the org audience.
    """
    factory = get_session_factory()
    if user.audience == AUDIENCE_ORG:
        if user.tenant_id is None or client_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="Not found")
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, user.tenant_id):
                    if not await rbac.has_capability(
                        session,
                        user.tenant_id,
                        user.role,
                        VIEW_COMPANY_DNA,
                        user.user_id,
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Missing capability: {VIEW_COMPANY_DNA}",
                        )
                    return await _status_for(session, client_id)

    if user.audience != AUDIENCE_OWNER:
        raise HTTPException(status_code=404, detail="Not found")

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                if not await rbac.has_capability(
                    session, None, user.role, "view_bd_customers", user.user_id
                ):
                    raise HTTPException(
                        status_code=403, detail="Missing capability: view_bd_customers"
                    )
                await audit(
                    session,
                    tenant_id=None,
                    actor_user_id=user.user_id,
                    action="company_dna_status_read",
                    target_type="tenant",
                    target_id=client_id,
                    metadata={
                        "actor_role": user.role.value,
                        "path": request.url.path,
                        "scope": "completion_status_only",
                    },
                )
                return await _status_for(session, client_id)


async def _status_for(
    session: AsyncSession, client_id: uuid.UUID
) -> CompanyDNAStatusOut:
    """Three columns, none of them content."""
    row = (
        await session.execute(
            select(CompanyDNA.version, CompanyDNA.completed_at)
            .where(
                CompanyDNA.tenant_id == client_id,
                CompanyDNA.is_current.is_(True),
                CompanyDNA.status == STATUS_COMPLETE,
            )
            .limit(1)
        )
    ).first()
    draft_open = (
        await session.execute(
            select(func.count())
            .select_from(CompanyDNA)
            .where(
                CompanyDNA.tenant_id == client_id, CompanyDNA.status == STATUS_DRAFT
            )
        )
    ).scalar_one()
    return CompanyDNAStatusOut(
        client_id=client_id,
        status=STATUS_COMPLETE if row is not None else "incomplete",
        version=int(row[0]) if row is not None else None,
        completed_at=row[1] if row is not None else None,
        draft_open=bool(draft_open),
    )
