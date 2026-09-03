"""Metric engine foundation (Master Directive Part 2 section 3).

Implements the section 3 formulas whose inputs exist in this codebase today,
each with the directive's exact Green/Amber/Red thresholds. Metrics whose
inputs do not exist yet (offer, onboarding, calibration and scorecard
surfaces; see services/telemetry_events.py for the event-code inventory) are
deliberately absent rather than approximated into meaninglessness.

Every metric returns the same shape:

    {"value": float | None, "unit": str, "status": "green"|"amber"|"red"|None,
     "inputs": {...}}

`value` is None (and `status` is None) when the tenant has no data to compute
from; an empty pipeline is "nothing to report", not a health verdict.

PROXIES, STATED PLAINLY
-----------------------
* PRL / SLA_PR: section 3.1 measures from t_profile_presented (the profile
  reaching the HM) to t_hm_decision. This platform has no separate
  present-to-HM step, so the proxy runs from LINK CREATION (EV_PROFILE_SUBMIT)
  to the FIRST pipeline decision (the first pipeline_status entry that is not
  `applied`).
* TTF deconstruction: section 3.3 runs t_req_opened to t_offer_accepted.
  There is no offer object, so the available segments are
  job.created_at (EV_REQ_CREATED) -> first application -> first completed
  assessment conversation, and the health band is applied to that truncated
  total. It UNDERSTATES true TTF; when offers land, extend it.
* AISP: section 3.3 counts HM accept/reject per AI-matched profile. The
  AI-matched population here is the databank-matched links (`source =
  'databank'`, minted by the matching pipeline); "accepted" is a link moved
  FORWARD past `applied`, "rejected" is the `rejected` status. Links still
  sitting at `applied` or on `hold` carry no decision yet and are excluded
  from the denominator.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Thresholds, verbatim from Part 2 section 3 ──────────────────────────────
# Ordering convention for `health`: (green, amber) with green < amber means
# lower-is-better; green > amber means higher-is-better.

#: Section 3.1, HM Profile Review Latency: Green < 24.0h, Amber 24.0-48.0h,
#: Red > 48.0h.
PRL_GREEN_HOURS = 24.0
PRL_AMBER_HOURS = 48.0

#: Section 3.1, Profile Review SLA Compliance: Green >= 90.0%, Amber
#: 75.0-89.9%, Red < 75.0%. The SLA window itself defaults to the section
#: 3.1 example ("within 24 hours of submission").
SLA_GREEN_PCT = 90.0
SLA_AMBER_PCT = 75.0
SLA_TARGET_HOURS = 24.0

#: Section 3.2, Candidate Stagnation Rate: Green < 10.0%, Amber 10.0-20.0%,
#: Red > 20.0%.
CSR_GREEN_PCT = 10.0
CSR_AMBER_PCT = 20.0

#: Section 3.3, Agentic AI Sourcing Precision: Green >= 80.0%, Amber
#: 65.0-79.9%, Red < 65.0%.
AISP_GREEN_PCT = 80.0
AISP_AMBER_PCT = 65.0

#: Section 3.3, Time-to-Fill: Green < 35 days (the tech band; this platform
#: does not yet distinguish tech/non-tech requisitions), Amber 35-50, Red > 50.
TTF_GREEN_DAYS = 35.0
TTF_AMBER_DAYS = 50.0

#: Section 3.2's per-stage stagnation thresholds, mapped onto this codebase's
#: pipeline vocabulary: "Sourcing Screen = 3 days" covers the pre-decision
#: stages, "HM Review = 2 days" the stages awaiting a hiring decision,
#: "Interview Scheduling = 4 days" the interview stages, "Offer Decision =
#: 3 days" the offer stages. `hold` and anything unmapped fall back to the
#: 3-day sourcing default.
STAGE_STALE_THRESHOLD_DAYS: dict[str, int] = {
    "applied": 3,
    "assessment_invited": 3,
    "assessment_in_progress": 3,
    "assessment_completed": 2,
    "shortlisted": 2,
    "interview_scheduled": 4,
    "interview_completed": 4,
    "offer_extended": 3,
    "offered": 3,
    "hold": 3,
}
DEFAULT_STALE_THRESHOLD_DAYS = 3

#: For AISP: a link at any of these has been moved FORWARD past the initial
#: review, which is the accept signal available today.
_FORWARD_STATUSES = (
    "assessment_invited",
    "assessment_in_progress",
    "assessment_completed",
    "shortlisted",
    "interview_scheduled",
    "interview_completed",
    "offer_extended",
    "offered",
    "joined",
)


def health(value: float, green: float, amber: float) -> str:
    """Apply one Part 2 section 3 threshold pair to a value.

    Direction is carried by the ordering: `green < amber` is a
    lower-is-better metric (latency, stagnation) where the green band is
    `value < green` and amber is inclusive of both boundaries, matching the
    directive's "Amber: 24.0 -- 48.0 hours" wording; `green > amber` is a
    higher-is-better metric (compliance, precision) where green is
    `value >= green` and amber is `value >= amber`.
    """
    if green < amber:
        if value < green:
            return "green"
        if value <= amber:
            return "amber"
        return "red"
    if value >= green:
        return "green"
    if value >= amber:
        return "amber"
    return "red"


def _metric(
    value: float | None, unit: str, green: float, amber: float, inputs: dict[str, Any]
) -> dict[str, Any]:
    return {
        "value": None if value is None else round(value, 2),
        "unit": unit,
        "status": None if value is None else health(value, green, amber),
        "inputs": inputs,
    }


def _window_clause(
    column: str, since: datetime | None, until: datetime | None, params: dict
) -> str:
    clause = ""
    if since is not None:
        clause += f" AND {column} >= :since"
        params["since"] = since
    if until is not None:
        clause += f" AND {column} < :until"
        params["until"] = until
    return clause


async def candidate_stagnation_rate(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """CSR_aging (Part 2 section 3.2).

    Formula: (active candidates inactive in current stage > thresh_days /
    total active pipeline) * 100. Stage entry time is the link's
    `status_updated_at` (the denormalised twin of the pipeline_status
    history), falling back to `created_at` for a link that never moved.
    No date window: stagnation is a NOW question by definition.
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            sql_text(
                "SELECT status, status_updated_at, created_at "
                "FROM job_candidate_links "
                "WHERE tenant_id = :tid AND archived_at IS NULL "
                "AND status NOT IN ('rejected', 'joined')"
            ),
            {"tid": str(tenant_id)},
        )
    ).mappings().all()

    stale = 0
    for row in rows:
        entered = row["status_updated_at"] or row["created_at"]
        threshold = STAGE_STALE_THRESHOLD_DAYS.get(
            row["status"], DEFAULT_STALE_THRESHOLD_DAYS
        )
        if entered is not None and (now - entered) > timedelta(days=threshold):
            stale += 1

    total = len(rows)
    value = None if total == 0 else (stale / total) * 100.0
    return _metric(
        value,
        "%",
        CSR_GREEN_PCT,
        CSR_AMBER_PCT,
        {
            "stale_candidates": stale,
            "active_pipeline": total,
            "stage_thresholds_days": dict(STAGE_STALE_THRESHOLD_DAYS),
        },
    )


async def _review_latencies(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    since: datetime | None,
    until: datetime | None,
) -> list[tuple[datetime, datetime | None]]:
    """(presented_at, first_decision_at) per link in the window.

    The PRL/SLA proxy pair (module docstring): presented = link creation,
    decided = the first pipeline_status entry that is not `applied`.
    """
    params: dict[str, Any] = {"tid": str(tenant_id)}
    window = _window_clause("l.created_at", since, until, params)
    rows = (
        await session.execute(
            sql_text(
                "SELECT l.created_at AS presented_at, MIN(p.at) AS decided_at "
                "FROM job_candidate_links l "
                "LEFT JOIN pipeline_status p "
                "  ON p.job_candidate_link_id = l.id AND p.status <> 'applied' "
                f"WHERE l.tenant_id = :tid{window} "
                "GROUP BY l.id, l.created_at"
            ),
            params,
        )
    ).mappings().all()
    return [(row["presented_at"], row["decided_at"]) for row in rows]


async def profile_review_latency(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """PRL (Part 2 section 3.1): mean decision latency in decimal hours.

    Formula: (1 / N) * SUM(t_hm_decision - t_profile_presented) over the N
    profiles that HAVE a decision; undecided profiles cannot contribute a
    latency (they show up in SLA compliance instead).
    """
    pairs = await _review_latencies(session, tenant_id, since, until)
    decided = [
        (decided_at - presented_at).total_seconds() / 3600.0
        for presented_at, decided_at in pairs
        if decided_at is not None
    ]
    value = None if not decided else sum(decided) / len(decided)
    return _metric(
        value,
        "hours",
        PRL_GREEN_HOURS,
        PRL_AMBER_HOURS,
        {
            "profiles_reviewed": len(decided),
            "profiles_submitted": len(pairs),
            "proxy": "link creation -> first pipeline decision",
        },
    )


async def profile_review_sla(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    sla_hours: float = SLA_TARGET_HOURS,
) -> dict[str, Any]:
    """SLA_PR (Part 2 section 3.1): profiles reviewed within the SLA window
    over TOTAL profiles submitted, * 100. Undecided profiles count against
    compliance, exactly as the formula's denominator says.
    """
    pairs = await _review_latencies(session, tenant_id, since, until)
    within = sum(
        1
        for presented_at, decided_at in pairs
        if decided_at is not None
        and (decided_at - presented_at) <= timedelta(hours=sla_hours)
    )
    total = len(pairs)
    value = None if total == 0 else (within / total) * 100.0
    return _metric(
        value,
        "%",
        SLA_GREEN_PCT,
        SLA_AMBER_PCT,
        {
            "within_sla": within,
            "profiles_submitted": total,
            "target_sla_hours": sla_hours,
        },
    )


async def sourcing_precision(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """AISP (Part 2 section 3.3): AI-matched profiles accepted by the HM over
    AI-matched profiles presented, * 100.

    Mapping (module docstring): AI-matched = `source = 'databank'` links;
    accepted = moved forward past `applied`; rejected = `rejected`. Links
    with no decision yet (`applied`, `hold`) are excluded from the
    denominator because neither verdict exists for them.
    """
    params: dict[str, Any] = {"tid": str(tenant_id)}
    window = _window_clause("created_at", since, until, params)
    rows = (
        await session.execute(
            sql_text(
                "SELECT status, COUNT(*) AS n FROM job_candidate_links "
                f"WHERE tenant_id = :tid AND source = 'databank'{window} "
                "GROUP BY status"
            ),
            params,
        )
    ).mappings().all()
    counts = {row["status"]: row["n"] for row in rows}
    accepted = sum(counts.get(status, 0) for status in _FORWARD_STATUSES)
    rejected = counts.get("rejected", 0)
    decided = accepted + rejected
    presented = sum(counts.values())
    value = None if decided == 0 else (accepted / decided) * 100.0
    return _metric(
        value,
        "%",
        AISP_GREEN_PCT,
        AISP_AMBER_PCT,
        {
            "ai_profiles_presented": presented,
            "accepted": accepted,
            "rejected": rejected,
            "undecided": presented - decided,
            "mapping": "accepted = moved past applied; rejected = rejected status",
        },
    )


async def time_to_fill_segments(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """TTF deconstruction (Part 2 section 3.3), truncated to available data.

    Per job: t_req_opened = job.created_at (the EV_REQ_CREATED moment);
    sourcing latency = first application's link creation - t_req_opened;
    evaluation latency = first completed assessment conversation - first
    application. The health band applies to the truncated total (req opened
    -> first completed assessment), which UNDERSTATES true TTF because the
    offer/joining segments have no product surface yet.
    """
    params: dict[str, Any] = {"tid": str(tenant_id)}
    window = _window_clause("j.created_at", since, until, params)
    rows = (
        await session.execute(
            sql_text(
                "SELECT j.created_at AS opened_at, "
                "  (SELECT MIN(l.created_at) FROM job_candidate_links l "
                "   WHERE l.job_id = j.id) AS first_profile_at, "
                "  (SELECT MIN(ac.completed_at) FROM assessment_conversations ac "
                "   WHERE ac.job_id = j.id AND ac.completed_at IS NOT NULL) "
                "   AS first_completed_at "
                f"FROM jobs j WHERE j.tenant_id = :tid{window}"
            ),
            params,
        )
    ).mappings().all()

    def _days(later: datetime | None, earlier: datetime | None) -> float | None:
        if later is None or earlier is None:
            return None
        return (later - earlier).total_seconds() / 86400.0

    sourcing = [
        d for row in rows if (d := _days(row["first_profile_at"], row["opened_at"])) is not None
    ]
    evaluation = [
        d
        for row in rows
        if (d := _days(row["first_completed_at"], row["first_profile_at"])) is not None
    ]
    totals = [
        d
        for row in rows
        if (d := _days(row["first_completed_at"], row["opened_at"])) is not None
    ]

    def _avg(values: list[float]) -> float | None:
        return None if not values else sum(values) / len(values)

    total = _avg(totals)
    result = _metric(
        total,
        "days",
        TTF_GREEN_DAYS,
        TTF_AMBER_DAYS,
        {
            "jobs": len(rows),
            "jobs_with_completed_assessment": len(totals),
            "proxy": (
                "req opened -> first completed assessment; offer and joining "
                "segments unavailable (no offer/onboarding surface yet)"
            ),
        },
    )
    result["segments"] = {
        "sourcing_days": None if (s := _avg(sourcing)) is None else round(s, 2),
        "evaluation_days": None if (e := _avg(evaluation)) is None else round(e, 2),
    }
    return result


async def overview(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Every computable Part 2 section 3 metric for one tenant, in one call.

    The blocked metrics are named rather than silently absent, so the caller
    can render "awaiting product surface" instead of a hole.
    """
    return {
        "window": {
            "since": None if since is None else since.isoformat(),
            "until": None if until is None else until.isoformat(),
        },
        "metrics": {
            "candidate_stagnation_rate": await candidate_stagnation_rate(
                session, tenant_id
            ),
            "profile_review_latency": await profile_review_latency(
                session, tenant_id, since=since, until=until
            ),
            "profile_review_sla": await profile_review_sla(
                session, tenant_id, since=since, until=until
            ),
            "sourcing_precision": await sourcing_precision(
                session, tenant_id, since=since, until=until
            ),
            "time_to_fill": await time_to_fill_segments(
                session, tenant_id, since=since, until=until
            ),
        },
        # Section 3 metrics whose inputs have no product surface yet; see
        # services/telemetry_events.py for the matching event-code inventory.
        "unavailable": {
            "scorecard_submission_latency": "no interviewer scorecard object",
            "selectivity_ratio": "no offer object",
            "calibration_velocity": "no calibration batch flow",
            "join_realization_rate": "no onboarding object",
            "cost_of_vacancy": "no compensation/revenue inputs",
        },
    }
