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


# ═══════════════════════════════════════════════════════════════════════════
# TWO "STAGE" CONCEPTS, TWO TYPES (spec-doc6 C11 / 8.2, RBAC 17)
# ═══════════════════════════════════════════════════════════════════════════
#
# A JOB moves through a lifecycle. A CANDIDATE'S APPLICATION moves through a
# pipeline. Both were called "stage" in conversation and neither had a type,
# and the failure that invites is not subtle: an application status written
# into a job column, or a job state offered in a candidate's "Move to"
# dropdown. RBAC 17 defines the first, the Dashboard Specification defines the
# second, and spec-doc6 C11 rules that they are different enums on different
# entities that never share a table.
#
# THE RECONCILIATION IS THREE-WAY, NOT TWO
# ----------------------------------------
# This module already had a validated ten-value pipeline, and it is the one
# production rows sit in. So there are three vocabularies:
#
#   1. RBAC 17's JOB lifecycle          8 states, on `jobs`
#   2. The Dashboard's CANDIDATE stages 6 coarse stages, presentation only
#   3. This module's PIPELINE_ORDER    10 stages + `offered`, on
#                                       `job_candidate_links.status` and
#                                       `pipeline_status`
#
# (2) and (3) describe the SAME entity at two resolutions, so (2) becomes a
# derived VIEW of (3) rather than a replacement: `DASHBOARD_STAGE` maps each
# stored status onto the coarse stage a dashboard column renders. Nothing is
# deleted. `shortlisted` in particular stays exactly where it is -- historic
# applications sit in it and it is still the only route into
# `interview_scheduled` (see MANUAL_TRANSITION_EXCLUDED above).
#
# (1) is a different entity entirely and gets its own type below.

from enum import Enum


class JobLifecycleState(str, Enum):
    """RBAC 17. The state of a JOB, on `jobs.lifecycle_state`.

    17 permits different internal names as long as the semantic states are
    preserved; the specification's own names are used verbatim, because a
    reader holding the specification should be able to grep for them.

    CLOSED_ARCHIVED collapses 17's "CLOSED / ARCHIVED", which the document
    draws as one terminal box. This product already archives a job with
    `jobs.archived_at` and closes one by the end of its 30-day posting window,
    so the two are recorded there and this is the single terminal state.
    """

    DRAFT = "DRAFT"
    SENT_TO_HIRING_MANAGER = "SENT_TO_HIRING_MANAGER"
    IN_REVIEW = "IN_REVIEW"
    FINALIZED = "FINALIZED"
    PUBLISHED = "PUBLISHED"
    CANDIDATE_APPLICATIONS = "CANDIDATE_APPLICATIONS"
    HIRING_PROCESS = "HIRING_PROCESS"
    CLOSED_ARCHIVED = "CLOSED_ARCHIVED"


#: 17's order, which is also the legal forward path.
JOB_LIFECYCLE_ORDER: tuple[JobLifecycleState, ...] = (
    JobLifecycleState.DRAFT,
    JobLifecycleState.SENT_TO_HIRING_MANAGER,
    JobLifecycleState.IN_REVIEW,
    JobLifecycleState.FINALIZED,
    JobLifecycleState.PUBLISHED,
    JobLifecycleState.CANDIDATE_APPLICATIONS,
    JobLifecycleState.HIRING_PROCESS,
    JobLifecycleState.CLOSED_ARCHIVED,
)

#: States in which the role definition is still being drafted. RBAC 24***
#: limits the Recruiter's JD editing to exactly these, and 19 describes the
#: Hiring Manager working inside them.
DRAFTING_STATES: frozenset[str] = frozenset(
    {
        JobLifecycleState.DRAFT.value,
        JobLifecycleState.SENT_TO_HIRING_MANAGER.value,
        JobLifecycleState.IN_REVIEW.value,
    }
)

#: FINALIZED and everything after it. 21 makes this the precondition for
#: publication and 22 makes it the point after which criteria stop being
#: silently mutable.
FINALIZED_OR_LATER: frozenset[str] = frozenset(
    {
        JobLifecycleState.FINALIZED.value,
        JobLifecycleState.PUBLISHED.value,
        JobLifecycleState.CANDIDATE_APPLICATIONS.value,
        JobLifecycleState.HIRING_PROCESS.value,
        JobLifecycleState.CLOSED_ARCHIVED.value,
    }
)

#: Forward edges. A lifecycle has no "always available" escape the way the
#: candidate pipeline does: a job is archived, which is CLOSED_ARCHIVED, and
#: that is reachable from anywhere.
_LIFECYCLE_FORWARD: dict[JobLifecycleState, frozenset[JobLifecycleState]] = {
    JobLifecycleState.DRAFT: frozenset({JobLifecycleState.SENT_TO_HIRING_MANAGER}),
    JobLifecycleState.SENT_TO_HIRING_MANAGER: frozenset({JobLifecycleState.IN_REVIEW}),
    JobLifecycleState.IN_REVIEW: frozenset({JobLifecycleState.FINALIZED}),
    JobLifecycleState.FINALIZED: frozenset({JobLifecycleState.PUBLISHED}),
    JobLifecycleState.PUBLISHED: frozenset({JobLifecycleState.CANDIDATE_APPLICATIONS}),
    JobLifecycleState.CANDIDATE_APPLICATIONS: frozenset(
        {JobLifecycleState.HIRING_PROCESS}
    ),
    JobLifecycleState.HIRING_PROCESS: frozenset({JobLifecycleState.CLOSED_ARCHIVED}),
    JobLifecycleState.CLOSED_ARCHIVED: frozenset(),
}


def lifecycle_allowed_transitions(
    current: JobLifecycleState | str | None,
) -> frozenset[JobLifecycleState]:
    """Every lifecycle state reachable from `current`.

    A job can be archived from any live state, which is how a client stops a
    requisition that is no longer needed. Nothing leaves CLOSED_ARCHIVED: 22
    requires a controlled revision mechanism rather than a reopen.
    """
    if current is None:
        state = JobLifecycleState.DRAFT
    else:
        state = (
            current
            if isinstance(current, JobLifecycleState)
            else JobLifecycleState(str(current))
        )
    if state is JobLifecycleState.CLOSED_ARCHIVED:
        return frozenset()
    return frozenset(
        _LIFECYCLE_FORWARD[state] | {JobLifecycleState.CLOSED_ARCHIVED}
    ) - {state}


class CandidatePipelineStage(str, Enum):
    """The Dashboard Specification's coarse candidate stage, column 8.

    A PRESENTATION type. The stored, authoritative value is still one of
    `PIPELINE_ORDER` on `job_candidate_links.status`, and this enum is
    derived from it by `dashboard_stage` below. Making it the stored value
    would throw away the distinction between "invited" and "in progress",
    which is the difference between chasing a candidate and waiting for one.
    """

    APPLIED = "Applied"
    SCREENING = "Screening"
    SHORTLISTED = "Shortlisted"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    CLOSED = "Closed"


#: Stored pipeline status -> the dashboard stage that renders it. Every value
#: in ALL_STATUSES appears exactly once, asserted by
#: `tests/test_stage_enum_separation.py`, so a stage added to the FSM without
#: a dashboard home fails a test rather than rendering as blank.
DASHBOARD_STAGE: dict[str, CandidatePipelineStage] = {
    APPLIED: CandidatePipelineStage.APPLIED,
    ASSESSMENT_INVITED: CandidatePipelineStage.SCREENING,
    ASSESSMENT_IN_PROGRESS: CandidatePipelineStage.SCREENING,
    ASSESSMENT_COMPLETED: CandidatePipelineStage.SCREENING,
    SHORTLISTED: CandidatePipelineStage.SHORTLISTED,
    INTERVIEW_SCHEDULED: CandidatePipelineStage.INTERVIEW,
    INTERVIEW_COMPLETED: CandidatePipelineStage.INTERVIEW,
    OFFER_EXTENDED: CandidatePipelineStage.OFFER,
    OFFERED: CandidatePipelineStage.OFFER,
    JOINED: CandidatePipelineStage.CLOSED,
    REJECTED: CandidatePipelineStage.CLOSED,
    # `hold` is deliberately ABSENT, and forcing it into the enum was the
    # first attempt. It has no home: the Dashboard Specification treats hold
    # as an ACTION taken on a candidate rather than as a stage they occupy,
    # and the stored FSM agrees, because `hold` returns to whatever stage it
    # paused rather than carrying outward edges of its own (see _FORWARD).
    # Mapping it to Screening would claim the candidate had moved backwards;
    # mapping it to Closed would say the process had ended. `dashboard_stage`
    # returns None for it and the caller renders the pause as a modifier.
}

#: Statuses that are a MODIFIER on a stage rather than a stage. One entry,
#: named rather than left implicit, so the absence above reads as a decision.
NO_DASHBOARD_STAGE: frozenset[str] = frozenset({HOLD})


def dashboard_stage(status: str | None) -> CandidatePipelineStage | None:
    """Coarse stage for a stored pipeline status.

    None for `hold`, which is a pause on a stage rather than a stage. Unknown
    reads as Applied: a dashboard that raised on one unexpected row is worse
    than one that shows that row at the start of the funnel.
    """
    normalised = normalize(status)
    if normalised in NO_DASHBOARD_STAGE:
        return None
    return DASHBOARD_STAGE.get(normalised, CandidatePipelineStage.APPLIED)


#: The two vocabularies, for the separation test to compare. Kept as data so
#: the test asserts a property rather than restating a list somebody has to
#: keep in step by hand.
JOB_LIFECYCLE_VALUES: frozenset[str] = frozenset(
    state.value for state in JobLifecycleState
)
CANDIDATE_PIPELINE_VALUES: frozenset[str] = frozenset(ALL_STATUSES)
