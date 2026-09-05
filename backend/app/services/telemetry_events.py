"""Lifecycle telemetry emission (Master Directive Part 2 section 5.1).

Every candidate-job lifecycle milestone emits one `telemetry_events` row via
`emit`. The rows feed the metric engines in services/metrics.py and, later,
the dashboard tiers and nudge agents Part 2 sections 2 and 5.2 describe.

WHAT IS WIRED TODAY, AND WHAT IS NOT
------------------------------------
Part 2 section 5.1 names ten event codes. Only the milestones that exist as
product surfaces in this codebase emit today:

* EV_REQ_CREATED   -- POST /jobs (api/jobs.create_job), after the job flush.
* EV_PROFILE_SUBMIT -- an application/link coming into existence: the portal
  apply path (api/portal.apply_to_job) and the databank bulk upload
  (api/jobs._store_one_databank_resume). Note the mapping: section 5.1 words
  this event as "profile presented to HM"; this platform has no separate
  present-to-HM step, so link creation is the closest existing milestone and
  is what the PRL/SLA proxies in services/metrics.py measure from.
* EV_HM_DECISION   -- a pipeline stage decision (api/pipeline.change_status
  and the dashboard's stage move, api/dashboard.move_stage).
* EV_INT_COMPLETED -- assessment_conversations.completed_at being stamped
  (api/assessments, the conversation-completion write), AND the pipeline's
  `interview_completed` stage (via STAGE_MILESTONE_EVENTS below). The two
  are distinguishable by payload: the assessment path carries
  `conversation_id`, the pipeline path carries `{"stage": ...}`.
* EV_OFFER_EXTENDED -- the pipeline transition into `offer_extended`,
  emitted at the `apply_transition` chokepoint so none of its callers can
  forget (2026-09-05 spec section 5.1, wired via STAGE_MILESTONE_EVENTS).
* EV_ONBOARD_JOIN -- the pipeline transition into `joined`, same chokepoint.
  There is no scheduled-vs-actual join date object, so the payload carries
  the stage only; JRR reads this as "joined" without the on-schedule
  qualifier and says so in its formula note.

The remaining four codes are DEFINED (so the vocabulary is stable and the
store is ready) but NOT EMITTED, because the product surface that would
trigger them does not exist yet. This is a documented gap, not an oversight;
inventing a fake emission would poison every metric reading the code:

* EV_CALIB_SENT / EV_CALIB_APPROVED -- section 5.1's calibration batch flow
  (3-5 profiles sent to the HM for baseline approval) has no object here;
  the existing calibration_records table tracks reviewer/AI divergence,
  which is a different concept.
* EV_SCORECARD_SUB -- there is no interviewer scorecard object; the nearest
  surface (candidate_team_reviews) is a hiring-team verdict, not a
  per-interview rubric scorecard.
* EV_OFFER_DECISION -- there is no candidate accept/decline object. The
  `joined` stage implies acceptance and EV_ONBOARD_JOIN records it; a
  `rejected` transition out of `offer_extended` is ambiguous (candidate
  decline vs employer withdrawal), so emitting a decision there would
  fabricate a datum the product never captured.

When those surfaces land, wire their write paths through `emit` and move the
code from PENDING_EVENT_CODES to WIRED_EVENT_CODES.

THE STAGE-TO-EVENT MAPPING (2026-09-05 spec, Agent F)
-----------------------------------------------------
`STAGE_MILESTONE_EVENTS` maps a pipeline stage the product actually has onto
the section 5.1 milestone it IS. It is consulted by
`hiring_pipeline.apply_transition` -- the chokepoint all six transition
callers share -- so a milestone can never be missed by one caller.
EV_HM_DECISION is deliberately NOT in this mapping: a decision event carries
its ACTOR and is emitted at the API surfaces where the actor is known
(pipeline.change_status, dashboard.move_stage, candidates.decide_profile),
while a milestone event records that the candidate's lifecycle reached a
point, whoever moved it.
"""
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry import TelemetryEvent

logger = logging.getLogger("pickready.telemetry_events")

# ── Part 2 section 5.1 event codes ──────────────────────────────────────────
EV_REQ_CREATED = "EV_REQ_CREATED"
EV_CALIB_SENT = "EV_CALIB_SENT"
EV_CALIB_APPROVED = "EV_CALIB_APPROVED"
EV_PROFILE_SUBMIT = "EV_PROFILE_SUBMIT"
EV_HM_DECISION = "EV_HM_DECISION"
EV_INT_COMPLETED = "EV_INT_COMPLETED"
EV_SCORECARD_SUB = "EV_SCORECARD_SUB"
EV_OFFER_EXTENDED = "EV_OFFER_EXTENDED"
EV_OFFER_DECISION = "EV_OFFER_DECISION"
EV_ONBOARD_JOIN = "EV_ONBOARD_JOIN"

#: Codes with a live emission point in this codebase (see module docstring).
WIRED_EVENT_CODES: frozenset[str] = frozenset(
    {
        EV_REQ_CREATED,
        EV_PROFILE_SUBMIT,
        EV_HM_DECISION,
        EV_INT_COMPLETED,
        EV_OFFER_EXTENDED,
        EV_ONBOARD_JOIN,
    }
)

#: Codes awaiting their product surface (see module docstring for which).
PENDING_EVENT_CODES: frozenset[str] = frozenset(
    {
        EV_CALIB_SENT,
        EV_CALIB_APPROVED,
        EV_SCORECARD_SUB,
        EV_OFFER_DECISION,
    }
)

#: Pipeline stage -> the section 5.1 milestone that stage IS. Consulted by
#: `hiring_pipeline.apply_transition`; see the module docstring for why
#: EV_HM_DECISION is not here. Keys are `hiring_pipeline` stage strings,
#: written literally rather than imported to keep this module import-light
#: (hiring_pipeline imports THIS module).
STAGE_MILESTONE_EVENTS: dict[str, str] = {
    "interview_completed": EV_INT_COMPLETED,
    "offer_extended": EV_OFFER_EXTENDED,
    "joined": EV_ONBOARD_JOIN,
}

#: The full section 5.1 vocabulary. Anything else is refused by `emit` (logged
#: and swallowed, per the never-raise contract) so typos cannot mint a
#: parallel event stream the metric engines never read.
EVENT_CODES: frozenset[str] = WIRED_EVENT_CODES | PENDING_EVENT_CODES


async def emit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    event_code: str,
    job_id: uuid.UUID | str | None = None,
    candidate_id: uuid.UUID | str | None = None,
    job_candidate_link_id: uuid.UUID | str | None = None,
    actor_user_id: uuid.UUID | str | None = None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    """Insert one telemetry row. NEVER raises into the caller.

    Telemetry must never break a business write: the insert runs inside its
    own SAVEPOINT (`session.begin_nested`), so a failure rolls back only the
    telemetry row and leaves the caller's transaction intact and usable.
    Returns True if the row was written, False if the failure was logged and
    swallowed. Same hardening contract as audit.record_auth_event, and for
    the same reason.

    `occurred_at=None` lets the database stamp now(), which is right for the
    live emission points; a backfill or replay passes the historical time.
    """
    if event_code not in EVENT_CODES:
        logger.error(
            "telemetry emit refused: unknown event_code=%r tenant_id=%s",
            event_code,
            tenant_id,
        )
        return False
    try:
        async with session.begin_nested():
            row = TelemetryEvent(
                tenant_id=_coerce_uuid(tenant_id),
                event_code=event_code,
                job_id=_coerce_uuid(job_id),
                candidate_id=_coerce_uuid(candidate_id),
                job_candidate_link_id=_coerce_uuid(job_candidate_link_id),
                actor_user_id=_coerce_uuid(actor_user_id),
                payload=payload or {},
                correlation_id=correlation_id,
            )
            if occurred_at is not None:
                row.occurred_at = occurred_at
            session.add(row)
            await session.flush()
        return True
    except Exception:  # noqa: BLE001 -- telemetry must never break the caller
        logger.exception(
            "telemetry emit failed (swallowed) event_code=%s tenant_id=%s job_id=%s",
            event_code,
            tenant_id,
            job_id,
        )
        return False


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    """None / empty -> None; str/UUID -> UUID. Mirrors audit._coerce_uuid."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    text = str(value).strip()
    if not text:
        return None
    return uuid.UUID(text)
