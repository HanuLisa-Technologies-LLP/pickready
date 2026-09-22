"""Shortlisting opens background verification, and never holds anything up.

THE EVENT MAP SAYS TWO TRIGGERS, IN PARALLEL
----------------------------------------------
`docs/spec/VIVEKIUM_SPRINT_FEATURES.md`, the eight wirings, number 2:
"Shortlisting fires two parallel triggers: assessment start AND the BGV email.
Neither waits for the other." Until now nothing connected them. A recruiter had
to remember `POST /bgv/candidates/{id}/initiate` and then a send per employer,
and a verification nobody remembered to start is the state `apply_transition`
then refuses an offer over, three weeks later, with the candidate waiting.

WHY IT HANGS OFF `apply_transition`
-------------------------------------
Same argument the offer gate and the Updates feed row already make: six
callers reach that function, and a rule wired into one route is a rule the
other five do not have. A candidate can be shortlisted from the pipeline
board, the dashboard, the job page and the portal, and every one of those
paths arrives at the same line.

THREE PROPERTIES THIS MUST HAVE, AND HOW EACH IS OBTAINED
------------------------------------------------------------
* **It never blocks or delays the assessment.** Everything here is SQL and a
  dispatch. The email itself is `pickready.send_email`, dispatched, never sent
  inline (rule 4), so the slow part happens in a Lambda while the recruiter's
  request has already returned.
* **A BGV failure never fails the transition.** The whole body runs inside a
  SAVEPOINT. A failed statement inside a transaction poisons everything after
  it, so catching an exception without a savepoint would not save the
  transition at all -- the shortlist write would have already been doomed. The
  savepoint is what makes the catch mean something, and the failure is logged
  with its traceback rather than swallowed: this returns a count nobody is
  required to read, and the log line is the record that it did not run.
* **It is idempotent.** A re-shortlist, a retried dispatch and a redelivered
  task all arrive as separate calls. The `NOT EXISTS` guard skips employers
  this tenant already has a verification for, and
  `uq_bgv_verification_employer` refuses the row underneath it if two calls
  race, which rolls back the savepoint and leaves the first one's work intact.

A FRESHER IS UNTOUCHED, and the check is the same one the offer gate uses: an
explicit `experienced` declaration is what makes verification required, so a
fresher and a candidate who never answered both fall straight out of the first
branch and nothing is sent about either of them.

THE TEMPLATE, NOT THE AGENT. There is no recruiter here to review a draft, so
the deterministic body goes out and `generated_by_ai` is recorded False. The
same choice `bgv_maintenance.auto_open_verifications` makes, for the same
reason: template output presented as generation is a lie about how the text
was produced.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bgv_verification import VERIFICATION_PENDING
from app.models.conversation import CHANNEL_EMAIL, DELIVERY_SENT, PARTY_RECRUITER
from app.models.employment import BACKGROUND_EXPERIENCED

logger = logging.getLogger(__name__)


async def _employers_awaiting_verification(
    session: AsyncSession, *, tenant_id: uuid.UUID, candidate_id: uuid.UUID
) -> list[dict]:
    """Every submitted employer this tenant has not opened a verification for.

    The NOT EXISTS is the idempotency, and it is the same guard
    `POST /bgv/candidates/{id}/initiate` uses: two recruiters, a retry and a
    second shortlist all have to produce one verification per employer.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT e.id, e.employer_name, e.designation, e.started_on, "
                    " e.ended_on, e.hr_name, e.hr_email "
                    "FROM candidate_employments e "
                    "WHERE e.candidate_id = :cid AND NOT EXISTS ("
                    "  SELECT 1 FROM bgv_verifications v "
                    "   WHERE v.candidate_employment_id = e.id "
                    "     AND v.tenant_id = :tid) "
                    "ORDER BY e.started_on DESC, e.id"
                ),
                {"cid": str(candidate_id), "tid": str(tenant_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def _open_and_send(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
    candidate_name: str,
    tenant_name: str,
    link_id: uuid.UUID,
    job_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    employment: dict,
    now: datetime,
) -> None:
    """One employer: verification row, thread, delivery record, dispatch."""
    from app.services import bgv_agent, bgv_delivery, conversations
    from app.workers.dispatch import dispatch

    verification_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO bgv_verifications (id, tenant_id, candidate_id, "
            " candidate_employment_id, initiated_from_link_id, status, "
            " created_at) "
            "VALUES (:id, :tid, :cid, :eid, :lid, :st, now())"
        ),
        {
            "id": str(verification_id),
            "tid": str(tenant_id),
            "cid": str(candidate_id),
            "eid": str(employment["id"]),
            "lid": str(link_id),
            "st": VERIFICATION_PENDING,
        },
    )
    conversation = await conversations.create_bgv_conversation(
        session,
        tenant_id=tenant_id,
        verification_id=verification_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        employer_name=employment["employer_name"],
        hr_name=employment["hr_name"],
        hr_email=str(employment["hr_email"]),
        actor_user_id=actor_user_id,
        job_id=job_id,
    )
    await session.execute(
        text("UPDATE bgv_verifications SET conversation_id = :conv WHERE id = :id"),
        {"conv": str(conversation["id"]), "id": str(verification_id)},
    )

    facts = bgv_agent.FactBlock(
        candidate_name=candidate_name,
        employer_name=employment["employer_name"],
        designation=employment["designation"],
        started_on=employment["started_on"],
        ended_on=employment["ended_on"],
        hr_name=employment["hr_name"],
        recruiter_team=f"Recruitment team, {tenant_name}",
    )
    subject = bgv_agent.subject_for(facts)
    body = bgv_agent.deterministic_body(facts)
    token = await bgv_delivery.issue_form_token(
        session, verification_id=verification_id, at=now
    )
    if token is not None:
        body = f"{body}\n\n{bgv_delivery.form_link_paragraph(token)}"

    # The thread carries what was sent, so a recruiter opening the employer's
    # reply can read the question it answers. Written BEFORE the dispatch, the
    # rule the recruiter's own send follows: a message announced but never
    # stored is one the reply has nothing to attach to.
    await conversations.post_message(
        session,
        conversation_id=uuid.UUID(str(conversation["id"])),
        tenant_id=tenant_id,
        author_party=PARTY_RECRUITER,
        author_user_id=actor_user_id,
        body=body,
        channel=CHANNEL_EMAIL,
        delivery_status=DELIVERY_SENT,
        client_token=f"bgv-autostart-{verification_id}",
        now=now,
    )
    # The address the request actually goes to, which is the candidate's
    # correction when they have made one. Resolved here rather than read off
    # the employment row, so a shortlist that happens AFTER a bounce and a
    # correction does not send to the address that already failed.
    recipient = await bgv_delivery.effective_hr_email(
        session, uuid.UUID(str(employment["id"]))
    )
    if not recipient:  # pragma: no cover - hr_email is NOT NULL on the table
        raise RuntimeError(
            f"employment {employment['id']} has no HR address to verify against"
        )
    email_log_id = await bgv_delivery.record_outbound(
        session,
        tenant_id=tenant_id,
        verification_id=verification_id,
        candidate_id=candidate_id,
        recipient=recipient,
        subject=subject,
        body=body,
        generated_by_ai=False,
        edited_by_human=False,
    )
    await bgv_delivery.mark_sent(session, verification_id=verification_id, at=now)
    dispatch(
        "pickready.send_email",
        args=[
            str(tenant_id),
            recipient,
            "bgv_verification",
            {"subject": subject, "body": body},
            None,
            conversations.reply_address(conversation["thread_token"]),
            str(email_log_id),
        ],
    )


async def on_shortlist(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
    link_id: uuid.UUID,
    job_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Open and send every outstanding verification for a shortlisted candidate.

    Returns how many employers were asked. Zero is the ordinary answer for a
    fresher, for a candidate whose history is still a draft, and for a
    re-shortlist where every employer already has a verification.

    NEVER RAISES. The caller is `hiring_pipeline.apply_transition`, and a
    verification that could not be opened must not undo a stage change a
    recruiter made: the failure is logged with its traceback and the
    verification stays at `not_started`, which is exactly the state the
    recruiter's manual Initiate control exists to clear.
    """
    now = now or datetime.now(timezone.utc)
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.full_name, c.employment_background, "
                    " c.employment_history_finalized_at, t.name AS tenant_name "
                    "FROM candidates c "
                    "LEFT JOIN tenants t ON t.id = :tid "
                    "WHERE c.id = :cid"
                ),
                {"cid": str(candidate_id), "tid": str(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return 0
    # A FRESHER IS NEVER TOUCHED, and neither is somebody who has not answered
    # the question: `derive_status` reads both as `not_required` and the offer
    # gate never fires for either, so mailing anybody about them would be the
    # product acting on a claim nobody made.
    if row["employment_background"] != BACKGROUND_EXPERIENCED:
        return 0
    # A DRAFT IS NOT A DECLARATION. Verifying one would ask an employer to
    # confirm details the candidate is still editing, and the employment rows
    # are only immutable once the history is final.
    if row["employment_history_finalized_at"] is None:
        return 0

    employments = await _employers_awaiting_verification(
        session, tenant_id=tenant_id, candidate_id=candidate_id
    )
    if not employments:
        return 0

    opened = 0
    for employment in employments:
        # ONE SAVEPOINT PER EMPLOYER, not one for the batch. A candidate with
        # two employers where the second collides with a concurrent Initiate
        # must still have the first one asked; rolling the batch back would
        # make one recruiter's click silently undo another's.
        try:
            async with session.begin_nested():
                await _open_and_send(
                    session,
                    tenant_id=tenant_id,
                    candidate_id=candidate_id,
                    candidate_name=row["full_name"] or "The candidate",
                    tenant_name=row["tenant_name"] or "our team",
                    link_id=link_id,
                    job_id=job_id,
                    actor_user_id=actor_user_id,
                    employment=employment,
                    now=now,
                )
        except Exception:  # noqa: BLE001 -- see the docstring: never fail the move
            logger.exception(
                "bgv.autostart_failed tenant=%s candidate=%s employment=%s",
                tenant_id,
                candidate_id,
                employment["id"],
            )
            continue
        opened += 1
    if opened:
        logger.info(
            "bgv.autostart tenant=%s candidate=%s opened=%d",
            tenant_id,
            candidate_id,
            opened,
        )
    return opened
