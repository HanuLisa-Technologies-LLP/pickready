"""Background verification: the candidate's declaration, and the recruiter's diligence.

TWO AUDIENCES, ONE ROUTER, AND THE SESSION DEPENDENCY IS THE BOUNDARY
-----------------------------------------------------------------------
`/bgv/me` is the CANDIDATE's own employment history and runs on
`get_candidate_db`. Everything else is the recruiter's and runs on
`get_tenant_db` behind `require_capability`. A candidate can never reach a
verification record and a recruiter can never rewrite an employment claim,
because neither route exists for them.

WHAT IS DELIBERATELY ABSENT
-----------------------------
There is no route that sets a verification status from email text, and no
parameter anywhere that infers one. `POST /verifications/{id}/decision` takes a
BOOLEAN A PERSON SET. The brief is explicit that HR responses are reviewed
manually, and the deeper reason is `hiring/layers.INVARIANTS`: an automated
decision here would be the product deciding a hiring outcome.

There is also no DELETE for an employment row and no PUT after finalisation.
The immutability is enforced three times over, on purpose: the database refuses
the write, the service refuses it with a sentence the candidate can act on, and
the route never offers it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_user,
    get_public_db,
    get_tenant_db,
    require_capability,
)
from app.core.config import get_settings
from app.models.bgv_verification import (
    VERIFICATION_NOT_STARTED,
    VERIFICATION_NOT_VERIFIED,
    VERIFICATION_PENDING,
    VERIFICATION_VERIFIED,
)
from app.models.conversation import CHANNEL_EMAIL, DELIVERY_SENT, PARTY_RECRUITER
from app.models.employment import BACKGROUND_EXPERIENCED, EMPLOYMENT_BACKGROUNDS
from app.schemas.bgv_workflow import (
    CandidateBGVOut,
    DecisionIn,
    DraftOut,
    EmploymentHistoryIn,
    EmploymentHistoryOut,
    EmploymentOut,
    SendIn,
    VerificationOut,
)
from app.services import bgv_agent, bgv_form, bgv_workflow, conversations
from app.services import capabilities as caps
from app.services.audit import audit
from app.workers.dispatch import dispatch

router = APIRouter()

#: Shown to the candidate BEFORE they submit, served from the server so the
#: warning and the rule cannot drift apart. A one-way door that is not
#: announced is a trap rather than a guarantee.
SUBMISSION_WARNING = (
    "These details are used to contact each previous employer and verify your "
    "employment. Once you submit, they are FINAL and cannot be edited or "
    "removed, including by our support team. Please check every company name, "
    "job title, date and HR email address carefully before you submit."
)


# ── The candidate's own history ──────────────────────────────────────────────


async def _candidate_id_for(session: AsyncSession, user: CurrentUser) -> uuid.UUID:
    """Resolve the signed-in candidate. Never trusts an id from the client."""
    row = (
        await session.execute(
            text(
                "SELECT c.id FROM candidates c "
                "LEFT JOIN users u ON u.id = :uid "
                "WHERE c.user_id = :uid OR (u.email IS NOT NULL AND c.email = u.email) "
                "ORDER BY c.created_at LIMIT 1"
            ),
            {"uid": str(user.user_id)},
        )
    ).scalar()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No candidate record yet, you appear after an employer's first "
                "outreach"
            ),
        )
    return uuid.UUID(str(row))


async def _history_out(
    session: AsyncSession, candidate_id: uuid.UUID
) -> EmploymentHistoryOut:
    row = (
        (
            await session.execute(
                text(
                    "SELECT employment_background, employment_history_finalized_at "
                    "FROM candidates WHERE id = :cid"
                ),
                {"cid": str(candidate_id)},
            )
        )
        .mappings()
        .first()
    )
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, employer_name, designation, started_on, ended_on, "
                    " hr_name, hr_email FROM candidate_employments "
                    "WHERE candidate_id = :cid ORDER BY started_on DESC, id"
                ),
                {"cid": str(candidate_id)},
            )
        )
        .mappings()
        .all()
    )
    return EmploymentHistoryOut(
        background=row["employment_background"] if row else None,
        finalized=bool(row and row["employment_history_finalized_at"]),
        finalized_at=row["employment_history_finalized_at"] if row else None,
        employments=[EmploymentOut(**dict(item)) for item in rows],
        submission_warning=SUBMISSION_WARNING,
    )


@router.get("/me", response_model=EmploymentHistoryOut)
async def my_employment_history(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_candidate_db),
) -> EmploymentHistoryOut:
    """The candidate's own declaration and its employers."""
    return await _history_out(session, await _candidate_id_for(session, user))


@router.put("/me", response_model=EmploymentHistoryOut)
async def save_employment_history(
    body: EmploymentHistoryIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_candidate_db),
) -> EmploymentHistoryOut:
    """Save the declaration, and optionally close it forever.

    REPLACE, not merge. The candidate edits a list on one screen and submits
    the list they see; a merge would leave a row they had deleted from the form
    still in the database and still about to be emailed.

    The replace is only legal BEFORE finalisation. After it, this refuses with
    409 and the database refuses independently, so the guarantee does not rest
    on this function being the only writer.
    """
    candidate_id = await _candidate_id_for(session, user)
    finalized_at = (
        await session.execute(
            text(
                "SELECT employment_history_finalized_at FROM candidates WHERE id = :cid"
            ),
            {"cid": str(candidate_id)},
        )
    ).scalar()
    if finalized_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Your employment details were submitted on "
                f"{finalized_at:%d %B %Y} and are final. They cannot be edited."
            ),
        )

    if body.background not in EMPLOYMENT_BACKGROUNDS:  # pragma: no cover - schema guards
        raise HTTPException(status_code=422, detail="Choose fresher or experienced.")

    await session.execute(
        text("DELETE FROM candidate_employments WHERE candidate_id = :cid"),
        {"cid": str(candidate_id)},
    )
    for item in body.employments:
        await session.execute(
            text(
                "INSERT INTO candidate_employments (id, candidate_id, employer_name, "
                " designation, started_on, ended_on, hr_name, hr_email, created_at) "
                "VALUES (gen_random_uuid(), :cid, :name, :role, :start, :end, "
                " :hr, :email, now())"
            ),
            {
                "cid": str(candidate_id),
                "name": item.employer_name.strip(),
                "role": item.designation.strip(),
                "start": item.started_on,
                "end": item.ended_on,
                "hr": item.hr_name.strip(),
                "email": str(item.hr_email).strip(),
            },
        )

    now = datetime.now(timezone.utc)
    await session.execute(
        text(
            "UPDATE candidates SET employment_background = :bg"
            + (", employment_history_finalized_at = :at" if body.finalize else "")
            + " WHERE id = :cid"
        ),
        (
            {"bg": body.background, "cid": str(candidate_id), "at": now}
            if body.finalize
            else {"bg": body.background, "cid": str(candidate_id)}
        ),
    )
    if body.finalize:
        await audit(
            session,
            tenant_id=None,
            actor_user_id=user.user_id,
            action=bgv_workflow.AUDIT_HISTORY_SUBMITTED,
            target_type="candidate",
            target_id=candidate_id,
            metadata={
                "background": body.background,
                "employer_count": len(body.employments),
            },
        )
    await session.flush()
    return await _history_out(session, candidate_id)


# ── The recruiter's view ─────────────────────────────────────────────────────


async def _assert_candidate_in_tenant(
    session: AsyncSession, candidate_id: uuid.UUID, tenant_id: uuid.UUID
) -> str | None:
    """A candidate this tenant has never linked to a job is NOT FOUND.

    404 rather than 403, the rule every cross-tenant read in this product
    follows: distinguishing them confirms that somebody else's candidate exists.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.full_name FROM candidates c "
                    "WHERE c.id = :cid AND EXISTS ("
                    "  SELECT 1 FROM job_candidate_links l "
                    "   WHERE l.candidate_id = c.id AND l.tenant_id = :tid)"
                ),
                {"cid": str(candidate_id), "tid": str(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return row["full_name"]


async def _candidate_bgv(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    tenant_id: uuid.UUID,
    name: str | None,
) -> CandidateBGVOut:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT e.id AS employment_id, e.employer_name, e.designation, "
                    " e.started_on, e.ended_on, e.hr_name, e.hr_email, "
                    " v.id AS verification_id, v.status, v.conversation_id, "
                    " v.first_sent_at, v.responded_at, v.decided_at, "
                    " v.decision_note, u.full_name AS decided_by_name "
                    "FROM candidate_employments e "
                    "LEFT JOIN bgv_verifications v "
                    "  ON v.candidate_employment_id = e.id AND v.tenant_id = :tid "
                    "LEFT JOIN users u ON u.id = v.decided_by "
                    "WHERE e.candidate_id = :cid "
                    "ORDER BY e.started_on DESC, e.id"
                ),
                {"cid": str(candidate_id), "tid": str(tenant_id)},
            )
        )
        .mappings()
        .all()
    )

    background = (
        await session.execute(
            text("SELECT employment_background FROM candidates WHERE id = :cid"),
            {"cid": str(candidate_id)},
        )
    ).scalar()

    verifications = [
        VerificationOut(
            id=row["verification_id"] or row["employment_id"],
            employment=EmploymentOut(
                id=row["employment_id"],
                employer_name=row["employer_name"],
                designation=row["designation"],
                started_on=row["started_on"],
                ended_on=row["ended_on"],
                hr_name=row["hr_name"],
                hr_email=row["hr_email"],
            ),
            status=row["status"] or VERIFICATION_NOT_STARTED,
            conversation_id=row["conversation_id"],
            first_sent_at=row["first_sent_at"],
            responded_at=row["responded_at"],
            decided_at=row["decided_at"],
            decided_by_name=row["decided_by_name"],
            decision_note=row["decision_note"],
        )
        for row in rows
    ]
    statuses = [row["status"] for row in rows if row["status"]]
    derived = bgv_workflow.derive_status(
        background=background, employer_count=len(rows), statuses=statuses
    )
    return CandidateBGVOut(
        candidate_id=candidate_id,
        candidate_name=name,
        background=background,
        required=background == BACKGROUND_EXPERIENCED,
        status=derived,
        employer_count=len(rows),
        verified_count=sum(1 for value in statuses if value == VERIFICATION_VERIFIED),
        verifications=verifications,
        offer_blocked_reason=bgv_workflow.blocking_message(derived),
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=CandidateBGVOut,
    dependencies=[Depends(require_capability(caps.VIEW_BGV))],
)
async def candidate_bgv(
    candidate_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> CandidateBGVOut:
    name = await _assert_candidate_in_tenant(session, candidate_id, user.tenant_id)
    return await _candidate_bgv(
        session, candidate_id=candidate_id, tenant_id=user.tenant_id, name=name
    )


@router.post(
    "/candidates/{candidate_id}/initiate",
    response_model=CandidateBGVOut,
    dependencies=[Depends(require_capability(caps.MANAGE_BGV))],
)
async def initiate(
    candidate_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> CandidateBGVOut:
    """Open one verification and one conversation per submitted employer.

    IDEMPOTENT. Two recruiters opening the same candidate on the same afternoon
    must not produce two records for one employer; the UNIQUE constraint on
    (tenant, employment) is what enforces that, and this skips what exists.

    Refused before the candidate has SUBMITTED: verifying a draft would email
    an employer about details the candidate is still editing.
    """
    name = await _assert_candidate_in_tenant(session, candidate_id, user.tenant_id)
    row = (
        (
            await session.execute(
                text(
                    "SELECT employment_background, employment_history_finalized_at "
                    "FROM candidates WHERE id = :cid"
                ),
                {"cid": str(candidate_id)},
            )
        )
        .mappings()
        .first()
    )
    if row["employment_background"] != BACKGROUND_EXPERIENCED:
        raise HTTPException(
            status_code=409,
            detail=(
                "This candidate has not declared previous employment, so there "
                "is nothing to verify."
            ),
        )
    if row["employment_history_finalized_at"] is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This candidate has not submitted their employment details yet. "
                "Verification opens once they do."
            ),
        )

    employments = (
        (
            await session.execute(
                text(
                    "SELECT e.id, e.employer_name, e.hr_name, e.hr_email "
                    "FROM candidate_employments e "
                    "WHERE e.candidate_id = :cid AND NOT EXISTS ("
                    "  SELECT 1 FROM bgv_verifications v "
                    "   WHERE v.candidate_employment_id = e.id AND v.tenant_id = :tid)"
                ),
                {"cid": str(candidate_id), "tid": str(user.tenant_id)},
            )
        )
        .mappings()
        .all()
    )

    link_id = (
        await session.execute(
            text(
                "SELECT id FROM job_candidate_links WHERE candidate_id = :cid "
                "AND tenant_id = :tid ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": str(candidate_id), "tid": str(user.tenant_id)},
        )
    ).scalar()

    for employment in employments:
        verification_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO bgv_verifications (id, tenant_id, candidate_id, "
                " candidate_employment_id, initiated_from_link_id, status, created_at) "
                "VALUES (:id, :tid, :cid, :eid, :lid, :st, now())"
            ),
            {
                "id": str(verification_id),
                "tid": str(user.tenant_id),
                "cid": str(candidate_id),
                "eid": str(employment["id"]),
                "lid": str(link_id) if link_id else None,
                "st": VERIFICATION_NOT_STARTED,
            },
        )
        conversation = await conversations.create_bgv_conversation(
            session,
            tenant_id=user.tenant_id,
            verification_id=verification_id,
            candidate_id=candidate_id,
            candidate_name=name or "Candidate",
            employer_name=employment["employer_name"],
            hr_name=employment["hr_name"],
            hr_email=employment["hr_email"],
            actor_user_id=user.user_id,
        )
        await session.execute(
            text("UPDATE bgv_verifications SET conversation_id = :conv WHERE id = :id"),
            {"conv": str(conversation["id"]), "id": str(verification_id)},
        )
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action=bgv_workflow.AUDIT_VERIFICATION_INITIATED,
            target_type="bgv_verification",
            target_id=verification_id,
            metadata={"employer": employment["employer_name"]},
        )
    await session.flush()
    return await _candidate_bgv(
        session, candidate_id=candidate_id, tenant_id=user.tenant_id, name=name
    )


async def _verification_or_404(
    session: AsyncSession, verification_id: uuid.UUID, tenant_id: uuid.UUID
) -> dict:
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.tenant_id, v.status, v.conversation_id, "
                    " v.candidate_id, v.first_sent_at, v.form_submitted_at, "
                    " e.employer_name, "
                    " e.designation, e.started_on, e.ended_on, e.hr_name, "
                    " e.hr_email, c.full_name "
                    "FROM bgv_verifications v "
                    "JOIN candidate_employments e ON e.id = v.candidate_employment_id "
                    "JOIN candidates c ON c.id = v.candidate_id "
                    "WHERE v.id = :vid"
                ),
                {"vid": str(verification_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None or str(row["tenant_id"]) != str(tenant_id):
        raise HTTPException(status_code=404, detail="Verification not found")
    return dict(row)


def _facts_for(row: dict, team: str) -> bgv_agent.FactBlock:
    return bgv_agent.FactBlock(
        candidate_name=row["full_name"] or "The candidate",
        employer_name=row["employer_name"],
        designation=row["designation"],
        started_on=row["started_on"],
        ended_on=row["ended_on"],
        hr_name=row["hr_name"],
        recruiter_team=team,
    )


async def _tenant_name(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    name = (
        await session.execute(
            text("SELECT name FROM tenants WHERE id = :tid"), {"tid": str(tenant_id)}
        )
    ).scalar()
    return f"Recruitment team, {name}" if name else "Recruitment team"


@router.post(
    "/verifications/{verification_id}/draft",
    response_model=DraftOut,
    dependencies=[Depends(require_capability(caps.MANAGE_BGV))],
)
async def draft(
    verification_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> DraftOut:
    """Ask the BGV Agent for a verification email. Sends nothing.

    Regenerating is allowed and cheap: the recruiter owns the final text, and a
    draft they discarded costs one model call.
    """
    row = await _verification_or_404(session, verification_id, user.tenant_id)
    facts = _facts_for(row, await _tenant_name(session, user.tenant_id))
    subject, body, generated_by_ai = await bgv_agent.draft_verification_email(facts)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=bgv_workflow.AUDIT_EMAIL_GENERATED,
        target_type="bgv_verification",
        target_id=verification_id,
        metadata={"generated_by_ai": generated_by_ai},
    )
    return DraftOut(
        verification_id=verification_id,
        subject=subject,
        body=body,
        generated_by_ai=generated_by_ai,
    )


async def _one_verification(
    session: AsyncSession, verification_id: uuid.UUID, tenant_id: uuid.UUID
) -> VerificationOut:
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.status, v.conversation_id, v.first_sent_at, "
                    " v.responded_at, v.decided_at, v.decision_note, "
                    " e.id AS employment_id, e.employer_name, e.designation, "
                    " e.started_on, e.ended_on, e.hr_name, e.hr_email, "
                    " u.full_name AS decided_by_name "
                    "FROM bgv_verifications v "
                    "JOIN candidate_employments e ON e.id = v.candidate_employment_id "
                    "LEFT JOIN users u ON u.id = v.decided_by "
                    "WHERE v.id = :vid AND v.tenant_id = :tid"
                ),
                {"vid": str(verification_id), "tid": str(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:  # pragma: no cover - caller has already checked
        raise HTTPException(status_code=404, detail="Verification not found")
    return VerificationOut(
        id=row["id"],
        employment=EmploymentOut(
            id=row["employment_id"],
            employer_name=row["employer_name"],
            designation=row["designation"],
            started_on=row["started_on"],
            ended_on=row["ended_on"],
            hr_name=row["hr_name"],
            hr_email=row["hr_email"],
        ),
        status=row["status"],
        conversation_id=row["conversation_id"],
        first_sent_at=row["first_sent_at"],
        responded_at=row["responded_at"],
        decided_at=row["decided_at"],
        decided_by_name=row["decided_by_name"],
        decision_note=row["decision_note"],
    )


@router.post(
    "/verifications/{verification_id}/send",
    response_model=VerificationOut,
    dependencies=[Depends(require_capability(caps.MANAGE_BGV))],
)
async def send(
    verification_id: uuid.UUID,
    body: SendIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> VerificationOut:
    """Send the recruiter's edited text to the employer, and record it once.

    The message is written to the conversation BEFORE the dispatch, and the
    dispatch RAISES on failure, so a send that never left the building cannot
    read as one that did. The conversation row and the `email_log` row are the
    same event seen from two sides.
    """
    row = await _verification_or_404(session, verification_id, user.tenant_id)
    if row["conversation_id"] is None:  # pragma: no cover - initiate always sets it
        raise HTTPException(status_code=409, detail="This verification has no thread")

    now = datetime.now(timezone.utc)
    # The employer checkbox form link (vivekium feature 4). A FRESH token on
    # every send while unanswered: a re-send after the 3-day expiry has to
    # carry a working link, and replacing the token retires the one already
    # sitting in the mailbox rather than leaving two live credentials. Once
    # the form is submitted the token is never reissued.
    form_url = None
    if row.get("form_submitted_at") is None:
        token = bgv_form.mint_token()
        await session.execute(
            text(
                "UPDATE bgv_verifications SET form_token = :tok, "
                " form_token_issued_at = :at WHERE id = :vid"
            ),
            {"tok": token, "at": now, "vid": str(verification_id)},
        )
        frontend = get_settings().frontend_url.rstrip("/")
        form_url = f"{frontend}/verify-employment/{token}"
    message = await conversations.post_message(
        session,
        conversation_id=uuid.UUID(str(row["conversation_id"])),
        tenant_id=user.tenant_id,
        author_party=PARTY_RECRUITER,
        author_user_id=user.user_id,
        body=body.body,
        channel=CHANNEL_EMAIL,
        delivery_status=DELIVERY_SENT,
        client_token=f"bgv-send-{verification_id}-{int(now.timestamp())}",
        now=now,
    )
    # THE REPLY ADDRESS IS WHAT MAKES THE THREAD RECEIVE ANYTHING. It carries
    # the conversation's own token, so the employer's answer routes back to
    # THIS employer's thread whatever they do to the subject line and however
    # little of the original their mail client quotes. None when the deployment
    # has no inbound domain, which `conversations.reply_address` records rather
    # than hides: the request still goes out and the reply arrives in the
    # sending mailbox instead.
    conversation = await conversations.authorize_participant(
        session,
        conversation_id=uuid.UUID(str(row["conversation_id"])),
        tenant_id=user.tenant_id,
    )
    # The form link travels UNDER the recruiter's edited text, added
    # server-side so no draft can drop it and no prompt can rewrite it. The
    # sentence states the brief's contract: three days, single use.
    outbound_body = body.body
    if form_url:
        outbound_body = (
            f"{body.body}\n\n"
            "To complete this verification in under two minutes, use the "
            f"secure form below. The link is unique to this request, works "
            f"once, and expires in "
            f"{get_settings().verification_link_ttl_days} days:\n{form_url}"
        )
    dispatch(
        "pickready.send_email",
        args=[
            str(user.tenant_id),
            row["hr_email"],
            "bgv_verification",
            {"subject": body.subject, "body": outbound_body},
            None,
            conversations.reply_address(conversation["thread_token"]),
        ],
    )
    await session.execute(
        text(
            "UPDATE bgv_verifications SET status = :st, "
            " first_sent_at = COALESCE(first_sent_at, :at), updated_at = :at "
            "WHERE id = :vid"
        ),
        {"st": VERIFICATION_PENDING, "at": now, "vid": str(verification_id)},
    )
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=bgv_workflow.AUDIT_EMAIL_SENT,
        target_type="bgv_verification",
        target_id=verification_id,
        metadata={"message_id": str(message["id"]), "employer": row["employer_name"]},
    )
    await session.flush()
    return await _one_verification(session, verification_id, user.tenant_id)


@router.post(
    "/verifications/{verification_id}/decision",
    response_model=CandidateBGVOut,
    dependencies=[Depends(require_capability(caps.MANAGE_BGV))],
)
async def decide(
    verification_id: uuid.UUID,
    body: DecisionIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> CandidateBGVOut:
    """A PERSON marks this employer verified, or not. Nothing else may.

    Reversible on purpose: an employer who replies late, or a discrepancy that
    turns out to be a payroll-system artefact, has to be recordable. What is
    NOT reversible is the fact that somebody decided, and when, and who: every
    decision is audited, and `decided_by` is ON DELETE RESTRICT.
    """
    row = await _verification_or_404(session, verification_id, user.tenant_id)
    now = datetime.now(timezone.utc)
    new_status = VERIFICATION_VERIFIED if body.verified else VERIFICATION_NOT_VERIFIED
    await session.execute(
        text(
            "UPDATE bgv_verifications SET status = :st, decided_by = :uid, "
            " decided_at = :at, decision_note = :note, updated_at = :at "
            "WHERE id = :vid"
        ),
        {
            "st": new_status,
            "uid": str(user.user_id),
            "at": now,
            "note": (body.note or "").strip() or None,
            "vid": str(verification_id),
        },
    )
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=(
            bgv_workflow.AUDIT_MARKED_VERIFIED
            if body.verified
            else bgv_workflow.AUDIT_MARKED_NOT_VERIFIED
        ),
        target_type="bgv_verification",
        target_id=verification_id,
        metadata={"employer": row["employer_name"]},
    )
    await session.flush()

    candidate_id = uuid.UUID(str(row["candidate_id"]))
    result = await _candidate_bgv(
        session,
        candidate_id=candidate_id,
        tenant_id=user.tenant_id,
        name=row["full_name"],
    )
    # The moment the LAST employer lands, the offer gate opens. Recorded as its
    # own event, because "why can I suddenly extend an offer" is a question the
    # audit log should be able to answer without arithmetic.
    if result.offer_blocked_reason is None and result.required:
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action=bgv_workflow.AUDIT_SELECTION_UNLOCKED,
            target_type="candidate",
            target_id=candidate_id,
            metadata={"employer_count": result.employer_count},
        )
    return result


# ── The employer checkbox form, public by token (vivekium feature 4) ─────────
#
# NO AUTHENTICATION: the token IS the credential, unguessable, unique to one
# employer-candidate verification, single-use and 3-day expired. This is the
# same trust model as the apply page, reached through `get_public_db`. The
# form serves and accepts ONLY the seven fixed items; nothing free-text
# crosses this boundary in either direction.


async def _verification_by_form_token(session: AsyncSession, token: str) -> dict:
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.tenant_id, v.candidate_id, v.status, "
                    " v.form_token_issued_at, v.form_submitted_at, "
                    " e.employer_name, e.hr_email, c.full_name, c.email "
                    "FROM bgv_verifications v "
                    "JOIN candidate_employments e "
                    "  ON e.id = v.candidate_employment_id "
                    "JOIN candidates c ON c.id = v.candidate_id "
                    "WHERE v.form_token = :tok"
                ),
                {"tok": token},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if row["form_submitted_at"] is not None:
        # A DIFFERENT answer from expiry, deliberately: the person clicking a
        # used link most likely already answered, and telling them the link
        # "expired" would send them chasing a new one for work that is done.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This verification has already been completed. Thank you; "
                "nothing further is needed."
            ),
        )
    issued = row["form_token_issued_at"]
    if issued is None or bgv_form.is_expired(issued):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "This verification link has expired. Links work for "
                f"{get_settings().verification_link_ttl_days} days from "
                "sending. The recruitment team can send a fresh one."
            ),
        )
    return dict(row)


@router.get("/form/{token}")
async def get_employer_checkbox_form(
    token: str, session: AsyncSession = Depends(get_public_db)
) -> dict:
    """What the employer's HR sees: who this is about, and the seven items."""
    row = await _verification_by_form_token(session, token)
    return {
        "candidate_name": row["full_name"],
        "employer_name": row["employer_name"],
        "items": bgv_form.items_payload(),
        "expires_at": bgv_form.expires_at(row["form_token_issued_at"]),
    }


@router.post("/form/{token}")
async def submit_employer_checkbox_form(
    token: str,
    body: dict,
    session: AsyncSession = Depends(get_public_db),
) -> dict:
    """The employer's submission: ticks in, status out, once.

    C4's boundary, held exactly: an HR person ticking the boxes IS the human
    decision, so THIS route may set the status. The model-parsed free-text
    reply path still never does, and `decided_by` stays untouched here
    because it names a platform user and the form's provenance is its own
    stored answers.
    """
    row = await _verification_by_form_token(session, token)
    answers = bgv_form.clean_answers(body.get("answers"))
    missing = [key for key in bgv_form.ITEM_KEYS if key not in answers]
    if missing:
        # Every item must be ANSWERED (true or false), or an unticked box is
        # indistinguishable from an unseen one.
        raise HTTPException(
            status_code=422,
            detail="Every item must be answered before submitting.",
        )
    now = datetime.now(timezone.utc)
    new_status = bgv_form.status_from_answers(answers)
    await session.execute(
        text(
            "UPDATE bgv_verifications SET status = :st, "
            " form_submitted_at = :at, form_answers_json = CAST(:ans AS jsonb), "
            " responded_at = COALESCE(responded_at, :at), updated_at = :at "
            "WHERE id = :vid"
        ),
        {
            "st": new_status,
            "at": now,
            "ans": json.dumps(answers),
            "vid": str(row["id"]),
        },
    )
    await audit(
        session,
        tenant_id=uuid.UUID(str(row["tenant_id"])),
        actor_user_id=None,
        action=bgv_workflow.AUDIT_FORM_SUBMITTED,
        target_type="bgv_verification",
        target_id=uuid.UUID(str(row["id"])),
        metadata={"status": new_status, "employer": row["employer_name"]},
    )
    # Email 2: the candidate is told it is DONE, with no verification detail
    # shared, per the brief. Sent only on a fully confirmed submission; a
    # partial confirmation goes to the recruiter's queue, not the candidate.
    if new_status == VERIFICATION_VERIFIED and row["email"]:
        dispatch(
            "pickready.send_email",
            args=[
                str(row["tenant_id"]),
                row["email"],
                "bgv_completed",
                {"candidate_name": row["full_name"] or "there"},
            ],
        )
    return {"status": "recorded"}
