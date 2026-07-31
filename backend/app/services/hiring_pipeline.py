"""The 10-stage hiring pipeline (spec §3.3 / §4).

    applied
      -> assessment_invited      (recruiter selects candidates)
      -> assessment_in_progress  (candidate opens the assessment)
      -> assessment_completed    (candidate finishes; PPI report generated)
      -> shortlisted | rejected  (recruiter reads the report)
      -> interview_scheduled -> interview_completed
      -> offer_extended -> joined

WHY TRANSITIONS ARE VALIDATED
-----------------------------
Each stage carries a promise. `assessment_completed` means a report exists;
`shortlisted` means a person read one. Letting an application jump from
`applied` straight to `offer_extended` would produce a candidate holding an
offer with no assessment behind it, and — because emails fire on transition —
would send them copy that references an assessment they never took. The legal
map below is what stops that.

`rejected` and `hold` are reachable from anywhere before a terminal state: a
recruiter can always stop a process, at any point, for reasons the system does
not model.

TWO WRITES, ONE TRUTH
---------------------
`pipeline_status` is the append-only history and remains authoritative;
`job_candidate_links.status` is a denormalised mirror so the candidate table
can sort and filter without a correlated subquery per row. `apply_transition`
writes both in one transaction — they cannot drift because nothing else writes
either one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

APPLIED = "applied"
ASSESSMENT_INVITED = "assessment_invited"
ASSESSMENT_IN_PROGRESS = "assessment_in_progress"
ASSESSMENT_COMPLETED = "assessment_completed"
SHORTLISTED = "shortlisted"
REJECTED = "rejected"
INTERVIEW_SCHEDULED = "interview_scheduled"
INTERVIEW_COMPLETED = "interview_completed"
OFFER_EXTENDED = "offer_extended"
JOINED = "joined"
HOLD = "hold"

#: Legacy synonym retained so historic rows stay readable. Normalised on read.
OFFERED = "offered"

#: Display order — the pipeline view groups candidates by this sequence.
PIPELINE_ORDER: tuple[str, ...] = (
    APPLIED,
    ASSESSMENT_INVITED,
    ASSESSMENT_IN_PROGRESS,
    ASSESSMENT_COMPLETED,
    SHORTLISTED,
    INTERVIEW_SCHEDULED,
    INTERVIEW_COMPLETED,
    OFFER_EXTENDED,
    JOINED,
    HOLD,
    REJECTED,
)

ALL_STATUSES: frozenset[str] = frozenset(PIPELINE_ORDER) | {OFFERED}

#: Nothing moves out of these.
TERMINAL: frozenset[str] = frozenset({REJECTED, JOINED})

#: Reachable from any non-terminal stage — a recruiter can always stop or
#: pause a process for reasons this model does not capture.
ALWAYS_AVAILABLE: frozenset[str] = frozenset({REJECTED, HOLD})

#: The forward path. `hold` returns to the stage it paused, so it is handled
#: separately rather than given outward edges here.
_FORWARD: dict[str, frozenset[str]] = {
    APPLIED: frozenset({ASSESSMENT_INVITED, SHORTLISTED}),
    ASSESSMENT_INVITED: frozenset({ASSESSMENT_IN_PROGRESS, ASSESSMENT_COMPLETED}),
    ASSESSMENT_IN_PROGRESS: frozenset({ASSESSMENT_COMPLETED}),
    ASSESSMENT_COMPLETED: frozenset({SHORTLISTED}),
    SHORTLISTED: frozenset({INTERVIEW_SCHEDULED, OFFER_EXTENDED}),
    INTERVIEW_SCHEDULED: frozenset({INTERVIEW_COMPLETED}),
    INTERVIEW_COMPLETED: frozenset({OFFER_EXTENDED, INTERVIEW_SCHEDULED}),
    OFFER_EXTENDED: frozenset({JOINED}),
    JOINED: frozenset(),
    REJECTED: frozenset(),
    HOLD: frozenset(),
}
# `applied -> shortlisted` is deliberately legal: the spec's own permission
# matrix keeps `shortlist_reject_hold` available on an application that was
# never assessed, and a recruiter who recognises a candidate from a referral
# should not be forced through an assessment to move them forward.
#
# There is deliberately NO `interview_scheduled -> interview_scheduled` edge.
# Multiple rounds ARE supported, but booking round two does not change the
# status — it is already `interview_scheduled`. Each round creates its own
# `interviews` row, and api/pipeline.schedule_interview guards its transition
# with `can_transition`, so the no-op is skipped rather than raising. Modelling
# it as a self-loop would put a duplicate row in the status history for a stage
# the application never actually re-entered.

#: Human-readable stage, shown to the candidate beside the machine value.
STAGE_LABELS: dict[str, str] = {
    APPLIED: "Application received",
    ASSESSMENT_INVITED: "Assessment invitation sent",
    ASSESSMENT_IN_PROGRESS: "Assessment in progress",
    ASSESSMENT_COMPLETED: "Assessment complete, under review",
    SHORTLISTED: "Shortlisted",
    REJECTED: "Not proceeding",
    INTERVIEW_SCHEDULED: "Interview scheduled",
    INTERVIEW_COMPLETED: "Interview complete",
    OFFER_EXTENDED: "Offer extended",
    OFFERED: "Offer extended",
    JOINED: "Joined",
    HOLD: "On hold",
}

#: Which lifecycle email a transition sends, if any. `assessment_in_progress`
#: is a backend event the candidate caused themselves — mailing them about it
#: would be noise (spec §4.1 marks it "none, backend event only").
TRANSITION_EMAIL: dict[str, str | None] = {
    APPLIED: "application_confirmation",
    ASSESSMENT_INVITED: "assessment_invitation",
    # The candidate caused this one themselves by opening the assessment.
    ASSESSMENT_IN_PROGRESS: None,
    ASSESSMENT_COMPLETED: "assessment_complete",
    SHORTLISTED: "shortlist",
    REJECTED: "rejected",
    HOLD: "hold",
    INTERVIEW_SCHEDULED: "interview_scheduled",
    INTERVIEW_COMPLETED: "interview_completed",
    OFFER_EXTENDED: "offer_extended",
    JOINED: "joined",
}


class InvalidTransition(ValueError):
    """Raised when a status change would break the pipeline's promises."""


def normalize(status: str | None) -> str:
    """Collapse the legacy `offered` synonym; unknown/None reads as applied."""
    if not status:
        return APPLIED
    if status == OFFERED:
        return OFFER_EXTENDED
    return status


def allowed_transitions(current: str | None) -> frozenset[str]:
    """Every status reachable from `current`.

    Pure; unit-tested directly. A terminal status returns an empty set — the
    UI reads this to decide which action buttons to render at all, so a wrong
    answer here shows a button that then 409s.
    """
    now = normalize(current)
    if now in TERMINAL:
        return frozenset()
    forward = _FORWARD.get(now, frozenset())
    return frozenset(forward | ALWAYS_AVAILABLE) - {now}


def can_transition(current: str | None, target: str) -> bool:
    return target in allowed_transitions(current)


# ── Manual transitions: what the "Move to" dropdown may offer ────────────────
#
# ASSUMPTION (2026-07-28, claude.md §8). The client asked for "Shortlisted" to
# be removed from the Decision dropdown on the job detail page. `shortlisted`
# is NOT a cosmetic stage: it is the only route into `interview_scheduled` and
# `offer_extended`, it is written by api/candidates.decide_profile, it has a
# lifecycle email of its own, and historic applications sit in it. Deleting it
# from the FSM would strand every one of those and break the forward path.
#
# So the stage STAYS in the FSM and stays reachable by the system, and only its
# OFFER as a manual target is withdrawn. Scheduling an interview still moves an
# application through it (api/pipeline.schedule_interview guards with
# can_transition), and `assert_transition` still accepts it, so nothing that
# already works stops working. What changes is exactly what the client asked
# for: the recruiter is no longer shown it as a thing to pick.
#
# The API now returns the allowed manual set (GET /pipeline/applications/{id}
# /transitions and every candidate row), so this rule lives in one place and
# the UI never hardcodes a stage list of its own.
MANUAL_TRANSITION_EXCLUDED: frozenset[str] = frozenset({SHORTLISTED})


def manual_transitions(current: str | None) -> frozenset[str]:
    """The subset of `allowed_transitions` a recruiter may choose by hand."""
    return allowed_transitions(current) - MANUAL_TRANSITION_EXCLUDED


def transition_options(current: str | None) -> list[dict[str, str]]:
    """The "Move to" dropdown: allowed manual targets with human labels.

    Ordered by PIPELINE_ORDER so the list reads forward through the funnel and
    does not reshuffle itself between two applications.
    """
    available = manual_transitions(current)
    return [
        {"status": stage, "label": STAGE_LABELS.get(stage, stage)}
        for stage in PIPELINE_ORDER
        if stage in available
    ]


def assert_transition(current: str | None, target: str) -> str:
    """Validate a transition, returning the normalised target."""
    if target not in ALL_STATUSES:
        raise InvalidTransition(
            f"unknown status {target!r}; expected one of {sorted(PIPELINE_ORDER)}"
        )
    target = normalize(target)
    current_normalised = normalize(current)
    if target == current_normalised:
        raise InvalidTransition(f"application is already {target!r}")
    if not can_transition(current, target):
        if current_normalised in TERMINAL:
            raise InvalidTransition(
                f"this application is {current_normalised!r} and cannot be reopened"
            )
        raise InvalidTransition(
            f"cannot move an application from {current_normalised!r} to {target!r}; "
            f"available: {sorted(allowed_transitions(current))}"
        )
    return target


@dataclass
class TransitionResult:
    link_id: uuid.UUID
    previous: str
    status: str
    stage_label: str
    email_type: str | None
    changed_at: datetime


async def apply_transition(
    session: AsyncSession,
    *,
    link_id: uuid.UUID,
    tenant_id: uuid.UUID,
    target: str,
    actor_user_id: uuid.UUID | None = None,
    remarks: str | None = None,
    now: datetime | None = None,
) -> TransitionResult:
    """Move one application to `target`, writing history and mirror together.

    Raises InvalidTransition for an illegal move, and LookupError if the link
    does not belong to this tenant — checked explicitly as defense in depth
    even though RLS is the real boundary (claude.md rule 1).

    This does NOT send the email. It returns the type to send, and the caller
    (which owns drafting, recruiter review and the Celery enqueue) decides —
    keeping this service free of I/O beyond its own two writes.
    """
    now = now or datetime.now(timezone.utc)
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, status FROM job_candidate_links "
                "WHERE id = :lid"
            ),
            {"lid": str(link_id)},
        )
    ).mappings().first()
    if row is None or str(row["tenant_id"]) != str(tenant_id):
        raise LookupError("Application not found")

    previous = normalize(row["status"])
    status = assert_transition(previous, target)
    label = STAGE_LABELS.get(status, status.replace("_", " ").title())

    await session.execute(
        text(
            "UPDATE job_candidate_links "
            "SET status = :status, status_updated_at = :at, current_stage = :label "
            "WHERE id = :lid"
        ),
        {"status": status, "at": now, "label": label, "lid": str(link_id)},
    )
    # The append-only history. Written second so a failure leaves the mirror
    # ahead rather than the history ahead — a missing history row is
    # recoverable from the mirror, the reverse is not.
    await session.execute(
        text(
            "INSERT INTO pipeline_status "
            "(id, tenant_id, job_candidate_link_id, status, remarks, set_by, at) "
            "VALUES (gen_random_uuid(), :tid, :lid, :status, :remarks, :actor, :at)"
        ),
        {
            "tid": str(tenant_id), "lid": str(link_id), "status": status,
            "remarks": remarks, "actor": str(actor_user_id) if actor_user_id else None,
            "at": now,
        },
    )
    return TransitionResult(
        link_id=link_id,
        previous=previous,
        status=status,
        stage_label=label,
        email_type=TRANSITION_EMAIL.get(status),
        changed_at=now,
    )


async def timelines(
    session: AsyncSession, link_ids: list[uuid.UUID]
) -> dict[str, list[dict[str, Any]]]:
    """Timelines for MANY applications in one query, keyed by link id as str.

    The batch counterpart of `timeline` below. The candidate's applications list
    renders a timeline per row, and calling the single-link version in a loop
    made that page cost one query per application on top of everything else it
    already fetched. Same SQL, same shape, one round trip.
    """
    if not link_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT job_candidate_link_id, status, remarks, at "
                "FROM pipeline_status "
                "WHERE job_candidate_link_id = ANY(:lids) "
                "ORDER BY job_candidate_link_id, at, id"
            ),
            {"lids": [str(value) for value in link_ids]},
        )
    ).mappings().all()
    grouped: dict[str, list[dict[str, Any]]] = {str(value): [] for value in link_ids}
    for row in rows:
        status = normalize(row["status"])
        grouped[str(row["job_candidate_link_id"])].append(
            {"status": status, "label": STAGE_LABELS.get(status, status), "at": row["at"]}
        )
    return grouped


async def timeline(session: AsyncSession, link_id: uuid.UUID) -> list[dict[str, Any]]:
    """The candidate-visible status history, oldest first.

    Read from `pipeline_status` rather than the mirror so the candidate sees
    the whole journey, not just where they are now.
    """
    rows = (
        await session.execute(
            text(
                "SELECT status, remarks, at FROM pipeline_status "
                "WHERE job_candidate_link_id = :lid ORDER BY at, id"
            ),
            {"lid": str(link_id)},
        )
    ).mappings().all()
    return [
        {
            "status": normalize(row["status"]),
            "label": STAGE_LABELS.get(
                normalize(row["status"]), normalize(row["status"])
            ),
            # `remarks` can carry an internal note (a hold reason written for
            # colleagues), so it is NOT surfaced to the candidate here.
            "at": row["at"],
        }
        for row in rows
    ]
