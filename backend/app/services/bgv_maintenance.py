"""BGV auto-maintenance: the last-two-employers rule (vivekium feature 5).

WHAT THE RULING CHANGED. The 2026-09-12 owner decision made the employment
history one submission, final for ever. The vivekium brief, ruled final on
2026-09-18, says the candidate's BGV record is PERMANENT AND PORTABLE, holds
"always the last 2 employers only", and that "BGV auto-maintenance must run
automatically when the candidate updates employment history. No manual
trigger. No admin action required." A history that can never gain the
candidate's newest employer cannot hold their LAST two, so finality is
NARROWED, not discarded:

* an existing row's CONTENT is still immutable, database-enforced (UPDATE
  still raises in the 0103 trigger),
* the candidate may APPEND a new employer after finalisation, and only
  append: there is still no edit and no chosen deletion,
* the auto-drop of the oldest row is the ONE sanctioned delete, and the
  trigger admits it only under the `app.bgv_maintenance` GUC this module
  sets transaction-locally, so nothing else in the product can quietly
  delete an employment row.

Stage B consent item 4 is the sentence the candidate agreed to: "updating my
employment history automatically replaces the oldest record."

"OLDEST" IS BY EMPLOYMENT START DATE, tie-broken by row creation: the brief
speaks of employers in career order, and the record being maintained is the
career's most recent two, not the two most recently typed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The brief's cap, verbatim: "Always the last 2 employers only."
MAX_EMPLOYERS = 2


async def append_employer(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    employer_name: str,
    designation: str,
    started_on: Any,
    ended_on: Any,
    hr_name: str,
    hr_email: str,
) -> dict[str, Any]:
    """Add one employer to a FINALISED history and enforce the cap.

    Returns {"added_id", "dropped"}: the new row's id and the ids of every
    row the cap removed (each of which took its verifications with it, by
    CASCADE, which is exactly what the consent item says happens).
    """
    new_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO candidate_employments (id, candidate_id, "
            "employer_name, designation, started_on, ended_on, hr_name, "
            "hr_email, created_at) VALUES (:id, :cid, :name, :role, :start, "
            ":end, :hr, :email, now())"
        ),
        {
            "id": str(new_id),
            "cid": str(candidate_id),
            "name": employer_name.strip(),
            "role": designation.strip(),
            "start": started_on,
            "end": ended_on,
            "hr": hr_name.strip(),
            "email": hr_email.strip(),
        },
    )
    # TRANSACTION-LOCAL (`true` as the third argument INSIDE an explicit
    # transaction; the probing lesson about set_config's scope, applied in
    # the direction that helps): the escape hatch dies with this commit.
    await session.execute(
        text("SELECT set_config('app.bgv_maintenance', 'on', true)")
    )
    dropped = [
        str(row[0])
        for row in (
            await session.execute(
                text(
                    "DELETE FROM candidate_employments WHERE id IN ("
                    "  SELECT id FROM candidate_employments "
                    "  WHERE candidate_id = :cid "
                    "  ORDER BY started_on DESC, created_at DESC, id "
                    "  OFFSET :keep"
                    ") RETURNING id"
                ),
                {"cid": str(candidate_id), "keep": MAX_EMPLOYERS},
            )
        ).all()
    ]
    return {"added_id": str(new_id), "dropped": dropped}


async def auto_open_verifications(
    session: AsyncSession, *, candidate_id: uuid.UUID, employment_id: uuid.UUID
) -> int:
    """Fire the new employer's verification for every tenant already
    verifying this candidate. No manual trigger, no admin action.

    "Already verifying" is the scope on purpose: a tenant that never opened
    BGV for this candidate has not asked, and mailing employers on behalf of
    a tenant that never chose to is the product making a hiring-process
    decision nobody took. For each qualifying tenant the verification row is
    created (idempotent under the UNIQUE constraint's NOT EXISTS guard), the
    form link is minted, and the DETERMINISTIC inquiry goes out with
    generated_by_ai never claimed: automation gets the template, because
    there is no recruiter here to review a generated draft.

    Returns how many tenant verifications were opened and sent.
    """
    from datetime import datetime, timezone

    from app.models.bgv_verification import DELIVERY_SENT, VERIFICATION_PENDING
    from app.services import bgv_agent, bgv_delivery, bgv_form
    from app.workers.dispatch import dispatch

    employment = (
        (
            await session.execute(
                text(
                    "SELECT e.employer_name, e.designation, e.started_on, "
                    " e.ended_on, e.hr_name, e.hr_email, c.full_name "
                    "FROM candidate_employments e "
                    "JOIN candidates c ON c.id = e.candidate_id "
                    "WHERE e.id = :eid AND e.candidate_id = :cid"
                ),
                {"eid": str(employment_id), "cid": str(candidate_id)},
            )
        )
        .mappings()
        .first()
    )
    if employment is None:
        return 0

    tenants = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT v.tenant_id, t.name "
                    "FROM bgv_verifications v "
                    "JOIN tenants t ON t.id = v.tenant_id "
                    "WHERE v.candidate_id = :cid"
                ),
                {"cid": str(candidate_id)},
            )
        )
        .mappings()
        .all()
    )
    opened = 0
    now = datetime.now(timezone.utc)
    for tenant in tenants:
        exists = (
            await session.execute(
                text(
                    "SELECT 1 FROM bgv_verifications WHERE tenant_id = :tid "
                    "AND candidate_employment_id = :eid"
                ),
                {"tid": str(tenant["tenant_id"]), "eid": str(employment_id)},
            )
        ).scalar()
        if exists:
            continue
        verification_id = uuid.uuid4()
        token = bgv_form.mint_token()
        await session.execute(
            text(
                "INSERT INTO bgv_verifications (id, tenant_id, candidate_id, "
                "candidate_employment_id, status, form_token, "
                "form_token_issued_at, first_sent_at, delivery_status, "
                "created_at) "
                "VALUES (:id, :tid, :cid, :eid, :st, :tok, :at, :at, "
                ":delivery, now())"
            ),
            {
                "id": str(verification_id),
                "tid": str(tenant["tenant_id"]),
                "cid": str(candidate_id),
                "eid": str(employment_id),
                "st": VERIFICATION_PENDING,
                "tok": token,
                "at": now,
                "delivery": DELIVERY_SENT,
            },
        )
        facts = bgv_agent.FactBlock(
            candidate_name=employment["full_name"] or "The candidate",
            employer_name=employment["employer_name"],
            designation=employment["designation"],
            started_on=employment["started_on"],
            ended_on=employment["ended_on"],
            hr_name=employment["hr_name"],
            recruiter_team=f"Recruitment team, {tenant['name']}",
        )
        body = (
            f"{bgv_agent.deterministic_body(facts)}\n\n"
            f"{bgv_delivery.form_link_paragraph(token)}"
        )
        subject = bgv_agent.subject_for(facts)
        # The corrected address when the candidate has replaced one, so an
        # employer appended after a bounce is not asked at the mailbox that
        # already refused a message about them.
        recipient = await bgv_delivery.effective_hr_email(session, employment_id)
        if not recipient:  # pragma: no cover - hr_email is NOT NULL
            continue
        # BOUND TO THIS VERIFICATION. Without it a bounce on this message has
        # nothing to resolve through, and the candidate is never told which
        # employer to correct.
        email_log_id = await bgv_delivery.record_outbound(
            session,
            tenant_id=uuid.UUID(str(tenant["tenant_id"])),
            verification_id=verification_id,
            candidate_id=candidate_id,
            recipient=recipient,
            subject=subject,
            body=body,
            generated_by_ai=False,
            edited_by_human=False,
        )
        dispatch(
            "pickready.send_email",
            args=[
                str(tenant["tenant_id"]),
                recipient,
                "bgv_verification",
                {"subject": subject, "body": body},
                str(email_log_id),
            ],
        )
        opened += 1
    return opened
