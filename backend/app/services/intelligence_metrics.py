"""Talent Intelligence metric registry (2026-09-05 spec, section 3).

The metric dictionary as DATA: every metric the specification defines is a
`MetricDefinition` row carrying its title, the specification's formula, its
unit, its Green/Amber/Red thresholds, and either an async compute function or
an honest statement of why it cannot be computed from this product's data
today. The 18 dashboards (services/intelligence_dashboards.py) are widget
lists over these ids.

WHAT COMPUTES AND WHAT DOES NOT
-------------------------------
Computable today, from telemetry-fed pipeline history and existing tables:
PRL, SLA_PR, SR_INT, CSR_AGING, JRR, AISP, TTF. Everything else returns
status "no_data" with a plain-language reason, because an unmeasurable figure
reported as 0.0 is a number that means nothing and looks like something
(claude.md, the eval_agents rule). No metric here is ever fabricated to fill
a widget.

ONE IMPLEMENTATION PER CONCEPT
------------------------------
PRL, SLA_PR, CSR_AGING and AISP delegate to services/metrics.py, the engine
foundation the telemetry phase built; this module adds the registry, the
health-word contract and the metrics that became computable when the
offer/joining pipeline milestones were wired (EV_OFFER_EXTENDED and
EV_ONBOARD_JOIN via hiring_pipeline.apply_transition). TTF here is the
specification's quantity (requisition opened to joining) and is NOT the same
quantity as services/metrics.time_to_fill_segments, which is the older
truncated reading (requisition opened to first completed assessment) kept for
the /dashboard/metrics contract; the two measure different interval
endpoints and both say so.

WHY PIPELINE HISTORY RATHER THAN telemetry_events ROWS
------------------------------------------------------
The milestone events and the pipeline_status history are written at the same
moments, but telemetry rows only exist from the day each emission was wired,
while pipeline_status covers every application the tenant ever had. Computing
from the history keeps a tenant's metrics truthful over their whole record
instead of resetting to empty at each wiring date. The telemetry store remains
the append-only event log the spec asks for; the formulas read the same facts
from the table that has all of them.

THE HEALTH CONTRACT
-------------------
Health is computed SERVER-SIDE and returned as a WORD: green, amber, red, or
no_data. The client renders the word; it never re-derives a band. Candidate
quality never appears here as a number: these are operational metrics
(latencies, ratios, counts), which the no-numbers rule does not cover; any
metric that would need a per-candidate score is no_data with the reason
stating exactly that (see EBDI).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import metrics as foundation

# ── Thresholds, verbatim from spec section 3 ────────────────────────────────
# Foundation already carries PRL 24/48, SLA 90/75, CSR 10/20, AISP 80/65 and
# the TTF 35/50 day bands; the pairs below are the ones it did not have.

#: Section 3.1, Scorecard Submission Latency: Green < 12.0h, Amber 12.0-24.0h,
#: Red > 24.0h.
SSL_GREEN_HOURS = 12.0
SSL_AMBER_HOURS = 24.0

#: Section 3.1, JD Calibration Velocity: Green < 24.0h, Amber 24.0-48.0h,
#: Red > 48.0h (halts the sourcing workflow).
TCAL_GREEN_HOURS = 24.0
TCAL_AMBER_HOURS = 48.0

#: Section 3.1, Requirement Realism Index: Green >= 0.75, Amber 0.50-0.74,
#: Red < 0.50.
RRI_GREEN = 0.75
RRI_AMBER = 0.50

#: Section 3.1, Selectivity Ratio: Green 2.5:1 to 4.0:1, Amber 4.1:1 to
#: 6.0:1, Red > 6.0:1 (indecision) or < 1.5:1 (premature hiring). The two
#: gaps the specification leaves unassigned (1.5 to 2.5 and 4.0 to 4.1) read
#: as amber: between the healthy band and a red boundary is a warning, not a
#: verdict.
SRINT_GREEN_LOW = 2.5
SRINT_GREEN_HIGH = 4.0
SRINT_RED_LOW = 1.5
SRINT_RED_HIGH = 6.0

#: Section 3.1, Mid-Search Scope Drift Rate: Green 0.0%, Amber 1.0-15.0%,
#: Red > 15.0%. Anything above zero and at or below 15 reads amber.
SDR_AMBER_PCT = 15.0

#: Section 3.2, Cost of Vacancy: Green < 25,000, Amber 25,000-60,000,
#: Red > 60,000 per open requisition (the specification states dollars).
COV_GREEN = 25000.0
COV_AMBER = 60000.0

#: Section 3.2, Quality of Hire: Green >= 85.0, Amber 70.0-84.9, Red < 70.0.
QOH_GREEN = 85.0
QOH_AMBER = 70.0

#: Section 3.2, Join Realization Rate: Green >= 90.0%, Amber 75.0-89.9%,
#: Red < 75.0%.
JRR_GREEN_PCT = 90.0
JRR_AMBER_PCT = 75.0

#: Section 3.3, Candidate NPS: Green >= +60.0, Amber +30.0 to +59.9,
#: Red < +30.0.
CNPS_GREEN = 60.0
CNPS_AMBER = 30.0

#: Section 3.3, Evaluation Bias and Dispersion Index: Green 0.85-1.15
#: (calibrated), Amber 0.70-0.84 or 1.16-1.30, Red < 0.70 (harsh) or > 1.30
#: (lax).
EBDI_GREEN_LOW = 0.85
EBDI_GREEN_HIGH = 1.15
EBDI_RED_LOW = 0.70
EBDI_RED_HIGH = 1.30


def _sr_int_health(value: float) -> str:
    """The selectivity ratio's band function: healthy is a RANGE, not a
    direction, so the shared two-threshold helper cannot express it."""
    if SRINT_GREEN_LOW <= value <= SRINT_GREEN_HIGH:
        return "green"
    if value < SRINT_RED_LOW or value > SRINT_RED_HIGH:
        return "red"
    return "amber"


def _sdr_health(value: float) -> str:
    """Scope drift: only a perfectly stable JD is green."""
    if value <= 0.0:
        return "green"
    if value <= SDR_AMBER_PCT:
        return "amber"
    return "red"


def _ebdi_health(value: float) -> str:
    """Dispersion: calibrated is a band around 1.0, red is either extreme."""
    if EBDI_GREEN_LOW <= value <= EBDI_GREEN_HIGH:
        return "green"
    if value < EBDI_RED_LOW or value > EBDI_RED_HIGH:
        return "red"
    return "amber"


# Simple two-threshold metrics reuse foundation.health, whose ordering
# convention carries the direction (green < amber means lower is better).
def _pair_health(green: float, amber: float) -> Callable[[float], str]:
    def _health(value: float) -> str:
        return foundation.health(value, green, amber)

    return _health


ComputeFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class MetricDefinition:
    """One row of the specification's metric dictionary, as data."""

    metric_id: str
    title: str
    #: The specification's formula, stated as the math it gives.
    formula: str
    unit: str
    #: The Green/Amber/Red bands in plain words, exactly as section 3 states.
    thresholds: str
    #: value -> "green" | "amber" | "red". None for a metric with no compute
    #: path, whose bands are recorded for the day its inputs exist.
    health: Callable[[float], str] | None = None
    #: async (session, tenant_id, since, until) -> foundation-shaped dict.
    #: None when the product has no data surface for this metric yet.
    compute: ComputeFn | None = None
    #: Why the metric cannot be computed today (compute is None), or what an
    #: empty result means (compute present, no rows). Always plain language;
    #: never rendered as a zero.
    no_data_reason: str = "No data has been recorded for this metric yet."
    #: Stated deviations from the specification's exact inputs, so a proxy is
    #: never mistaken for the full quantity.
    proxy_note: str | None = None


# ── Computations this module adds (offer/join milestones now exist) ─────────


async def _milestone_counts(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    since: datetime | None,
    until: datetime | None,
) -> dict[str, int]:
    """Distinct applications that reached each late-funnel stage, from the
    append-only pipeline history (`offered` is the legacy synonym of
    `offer_extended` and is counted with it)."""
    params: dict[str, Any] = {"tid": str(tenant_id)}
    window = ""
    if since is not None:
        window += " AND at >= :since"
        params["since"] = since
    if until is not None:
        window += " AND at < :until"
        params["until"] = until
    row = (
        await session.execute(
            sql_text(
                "SELECT "
                "COUNT(DISTINCT CASE WHEN status = 'interview_completed' "
                "  THEN job_candidate_link_id END) AS interviewed, "
                "COUNT(DISTINCT CASE WHEN status IN ('offer_extended', 'offered') "
                "  THEN job_candidate_link_id END) AS offers, "
                "COUNT(DISTINCT CASE WHEN status = 'joined' "
                "  THEN job_candidate_link_id END) AS joined "
                f"FROM pipeline_status WHERE tenant_id = :tid{window}"
            ),
            params,
        )
    ).mappings().one()
    return {
        "interviewed": int(row["interviewed"] or 0),
        "offers": int(row["offers"] or 0),
        "joined": int(row["joined"] or 0),
    }


async def selectivity_ratio(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """SR_int (section 3.1): unique candidates interviewed over offers
    extended. Proxy: an application that reached `interview_completed` is an
    interviewed candidate; the product does not distinguish a final round from
    an earlier one, so every completed interview counts."""
    counts = await _milestone_counts(session, tenant_id, since, until)
    value = (
        None if counts["offers"] == 0 else counts["interviewed"] / counts["offers"]
    )
    return {
        "value": None if value is None else round(value, 2),
        "unit": "ratio",
        "status": None if value is None else _sr_int_health(value),
        "inputs": {
            "candidates_interviewed": counts["interviewed"],
            "offers_extended": counts["offers"],
        },
    }


async def join_realization_rate(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """JRR (section 3.2): candidates joined over offers, times 100. Proxy:
    the denominator is offers EXTENDED, not offers accepted, because the
    product has no accept/decline object (EV_OFFER_DECISION is unwired for the
    same reason); and `joined` carries no scheduled-vs-actual date, so
    on-schedule joining cannot be distinguished from late joining."""
    counts = await _milestone_counts(session, tenant_id, since, until)
    value = (
        None
        if counts["offers"] == 0
        else (counts["joined"] / counts["offers"]) * 100.0
    )
    return {
        "value": None if value is None else round(value, 2),
        "unit": "%",
        "status": (
            None
            if value is None
            else foundation.health(value, JRR_GREEN_PCT, JRR_AMBER_PCT)
        ),
        "inputs": {
            "candidates_joined": counts["joined"],
            "offers_extended": counts["offers"],
        },
    }


async def time_to_fill(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """TTF (section 3.3): t_offer_accepted - t_req_opened, in days.

    Proxy: the closing timestamp is the first `joined` transition on the job
    (acceptance has no object of its own), so this measures requisition opened
    to first joining and only jobs with a joiner contribute. The health band
    is the specification's tech band (35/50 days); the product does not yet
    classify requisitions tech vs non-tech.
    """
    params: dict[str, Any] = {"tid": str(tenant_id)}
    window = ""
    if since is not None:
        window += " AND j.created_at >= :since"
        params["since"] = since
    if until is not None:
        window += " AND j.created_at < :until"
        params["until"] = until
    rows = (
        await session.execute(
            sql_text(
                "SELECT j.created_at AS opened_at, "
                "  (SELECT MIN(p.at) FROM pipeline_status p "
                "   JOIN job_candidate_links l ON l.id = p.job_candidate_link_id "
                "   WHERE l.job_id = j.id AND p.status = 'joined') AS joined_at "
                f"FROM jobs j WHERE j.tenant_id = :tid{window}"
            ),
            params,
        )
    ).mappings().all()
    durations = [
        (row["joined_at"] - row["opened_at"]).total_seconds() / 86400.0
        for row in rows
        if row["joined_at"] is not None and row["opened_at"] is not None
    ]
    value = None if not durations else sum(durations) / len(durations)
    return {
        "value": None if value is None else round(value, 2),
        "unit": "days",
        "status": (
            None
            if value is None
            else foundation.health(
                value, foundation.TTF_GREEN_DAYS, foundation.TTF_AMBER_DAYS
            )
        ),
        "inputs": {"jobs": len(rows), "jobs_filled": len(durations)},
    }


async def candidate_stagnation(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """CSR_aging, delegating to the foundation engine. Stagnation is a NOW
    question by definition, so the window parameters exist only for signature
    uniformity across the registry and are deliberately unused."""
    return await foundation.candidate_stagnation_rate(session, tenant_id)


# ── The registry ────────────────────────────────────────────────────────────

_NO_NUMERIC_INTERVIEW_SCORES = (
    "Interviewer evaluations in this product are verdict words, not numeric "
    "scores (the no-numbers rule), so a score dispersion cannot exist to "
    "measure. A rubric-anchored numeric scorecard would have to be introduced "
    "first."
)

METRICS: dict[str, MetricDefinition] = {
    definition.metric_id: definition
    for definition in (
        MetricDefinition(
            metric_id="PRL",
            title="Hiring Manager Profile Review Latency",
            formula=(
                "PRL = (1 / N) * SUM(t_hm_decision - t_profile_presented) "
                "for i = 1 to N"
            ),
            unit="hours",
            thresholds=(
                "Green under 24.0 hours, Amber 24.0 to 48.0 hours, Red over "
                "48.0 hours"
            ),
            health=_pair_health(foundation.PRL_GREEN_HOURS, foundation.PRL_AMBER_HOURS),
            compute=foundation.profile_review_latency,
            no_data_reason="No submitted profile has received a decision yet.",
            proxy_note=(
                "Measured from application creation to the first pipeline "
                "decision; there is no separate present-to-HM step."
            ),
        ),
        MetricDefinition(
            metric_id="SLA_PR",
            title="Profile Review SLA Compliance Rate",
            formula=(
                "SLA_PR = (Count of Profiles Reviewed within SLA Target / "
                "Total Profiles Submitted to HM) * 100"
            ),
            unit="%",
            thresholds=(
                "Green at or above 90.0 percent, Amber 75.0 to 89.9 percent, "
                "Red under 75.0 percent; the SLA window is 24 hours"
            ),
            health=_pair_health(foundation.SLA_GREEN_PCT, foundation.SLA_AMBER_PCT),
            compute=foundation.profile_review_sla,
            no_data_reason="No profiles have been submitted for review yet.",
            proxy_note=(
                "Measured from application creation to the first pipeline "
                "decision; undecided profiles count against compliance."
            ),
        ),
        MetricDefinition(
            metric_id="SSL",
            title="Scorecard Submission Latency",
            formula=(
                "SSL = (1 / M) * SUM(t_scorecard_submitted - t_interview_ended) "
                "for j = 1 to M"
            ),
            unit="hours",
            thresholds=(
                "Green under 12.0 hours, Amber 12.0 to 24.0 hours, Red over "
                "24.0 hours"
            ),
            health=_pair_health(SSL_GREEN_HOURS, SSL_AMBER_HOURS),
            no_data_reason=(
                "There is no interviewer scorecard object in the product yet; "
                "the nearest surface, the hiring team review, is a verdict on "
                "a candidate rather than a per-interview scorecard."
            ),
        ),
        MetricDefinition(
            metric_id="SR_INT",
            title="Interview-to-Offer Selectivity Ratio",
            formula=(
                "SR_int = Total Unique Candidates Interviewed in Final Round / "
                "Total Offers Extended"
            ),
            unit="ratio",
            thresholds=(
                "Green 2.5 to 4.0 to one, Amber 4.1 to 6.0 to one, Red over "
                "6.0 to one (indecision) or under 1.5 to one (premature hiring)"
            ),
            health=_sr_int_health,
            compute=selectivity_ratio,
            no_data_reason="No offers have been extended yet.",
            proxy_note=(
                "Every application reaching the completed-interview stage "
                "counts as interviewed; final rounds are not distinguished."
            ),
        ),
        MetricDefinition(
            metric_id="T_CAL",
            title="JD Calibration Velocity",
            formula="T_cal = t_calibration_approved - t_calibration_batch_sent",
            unit="hours",
            thresholds=(
                "Green under 24.0 hours, Amber 24.0 to 48.0 hours, Red over "
                "48.0 hours (halts the sourcing workflow)"
            ),
            health=_pair_health(TCAL_GREEN_HOURS, TCAL_AMBER_HOURS),
            no_data_reason=(
                "The calibration batch workflow (a sample of three to five "
                "profiles sent for baseline approval) does not exist in the "
                "product yet."
            ),
        ),
        MetricDefinition(
            metric_id="RRI",
            title="Requirement Realism Index",
            formula=(
                "RRI = (Total Qualified Profiles in Live Market Density / "
                "Target Candidate Pool Size) * (1 - Mandatory Skill Penalty "
                "Factor)"
            ),
            unit="index",
            thresholds=(
                "Green at or above 0.75, Amber 0.50 to 0.74, Red under 0.50 "
                "(unrealistic JD)"
            ),
            health=_pair_health(RRI_GREEN, RRI_AMBER),
            no_data_reason=(
                "No live market talent density source is connected, so JD "
                "feasibility against the market cannot be scored."
            ),
        ),
        MetricDefinition(
            metric_id="SDR",
            title="Mid-Search Scope Drift Rate",
            formula=(
                "SDR = (Count of Mandatory Skill Modifications Post-Approval / "
                "Total Initial Requirements) * 100"
            ),
            unit="%",
            thresholds=(
                "Green 0.0 percent (stable JD), Amber up to 15.0 percent, Red "
                "over 15.0 percent (scope instability)"
            ),
            health=_sdr_health,
            no_data_reason=(
                "Requirement changes after approval are not versioned per "
                "mandatory skill yet, so drift cannot be counted."
            ),
        ),
        MetricDefinition(
            metric_id="COV",
            title="Cost of Vacancy and Revenue Exposure",
            formula=(
                "CoV = Days_Vacant * ((Annual_Revenue_Per_Employee / 260) * "
                "Revenue_Impact_Multiplier + Daily_Overtime_Contractor_Expense)"
            ),
            unit="currency",
            thresholds=(
                "Green under 25,000 per role, Amber 25,000 to 60,000, Red "
                "over 60,000 per open requisition"
            ),
            health=_pair_health(COV_GREEN, COV_AMBER),
            no_data_reason=(
                "Annual revenue per employee, revenue impact multipliers and "
                "contractor expense are not captured, so vacancy cost cannot "
                "be computed."
            ),
        ),
        MetricDefinition(
            metric_id="QOH",
            title="Quality of Hire Composite Index",
            formula=(
                "QoH = (w1 * P_90) + (w2 * C_ramp) + (w3 * R_probation) + "
                "(w4 * S_manager), with w1=0.35, w2=0.25, w3=0.20, w4=0.20"
            ),
            unit="index",
            thresholds=(
                "Green at or above 85.0 of 100, Amber 70.0 to 84.9, Red under "
                "70.0"
            ),
            health=_pair_health(QOH_GREEN, QOH_AMBER),
            no_data_reason=(
                "Ninety-day performance reviews, ramp time and probation "
                "outcomes are not captured after a candidate joins."
            ),
        ),
        MetricDefinition(
            metric_id="CSR_AGING",
            title="Candidate Stagnation Rate",
            formula=(
                "CSR_aging = (Count of Active Candidates Inactive in Current "
                "Stage > Thresh_Days / Total Active Pipeline) * 100"
            ),
            unit="%",
            thresholds=(
                "Green under 10.0 percent, Amber 10.0 to 20.0 percent, Red "
                "over 20.0 percent pipeline stagnation"
            ),
            health=_pair_health(foundation.CSR_GREEN_PCT, foundation.CSR_AMBER_PCT),
            compute=candidate_stagnation,
            no_data_reason="There are no active candidates in the pipeline.",
        ),
        MetricDefinition(
            metric_id="JRR",
            title="Join Realization Rate",
            formula=(
                "JRR = (Total Candidates Joining on Scheduled Date / Total "
                "Candidate Offers Accepted) * 100"
            ),
            unit="%",
            thresholds=(
                "Green at or above 90.0 percent, Amber 75.0 to 89.9 percent, "
                "Red under 75.0 percent (severe renege risk)"
            ),
            health=_pair_health(JRR_GREEN_PCT, JRR_AMBER_PCT),
            compute=join_realization_rate,
            no_data_reason="No offers have been extended yet.",
            proxy_note=(
                "The denominator is offers extended (there is no offer "
                "accept/decline object) and joining is counted without an "
                "on-schedule qualifier."
            ),
        ),
        MetricDefinition(
            metric_id="AISP",
            title="Agentic AI Sourcing Precision",
            formula=(
                "AISP = (Count of AI-Matched Profiles Accepted by HM / Total "
                "AI-Matched Profiles Presented) * 100"
            ),
            unit="%",
            thresholds=(
                "Green at or above 80.0 percent, Amber 65.0 to 79.9 percent, "
                "Red under 65.0 percent (recalibrate agent prompts)"
            ),
            health=_pair_health(foundation.AISP_GREEN_PCT, foundation.AISP_AMBER_PCT),
            compute=foundation.sourcing_precision,
            no_data_reason=(
                "No databank-matched profile has received a decision yet."
            ),
            proxy_note=(
                "AI-matched means databank-sourced applications; accepted "
                "means moved forward past review, rejected means the rejected "
                "status."
            ),
        ),
        MetricDefinition(
            metric_id="SCEI",
            title="Sourcing Channel Efficiency Index",
            formula=(
                "SCEI = (Total Hires from Channel / Total Sourcing Cost of "
                "Channel) * Average_Quality_of_Hire_Score"
            ),
            unit="index",
            thresholds=(
                "Green top quartile ROI, Amber median ROI, Red bottom "
                "quartile (high cost, low quality)"
            ),
            no_data_reason=(
                "Per-channel sourcing cost is not captured, so channel "
                "efficiency cannot be ranked into quartiles."
            ),
        ),
        MetricDefinition(
            metric_id="TTF",
            title="Time-to-Fill",
            formula=(
                "TTF = t_offer_accepted - t_req_opened, deconstructed into "
                "sourcing, evaluation, and decision and offer latency"
            ),
            unit="days",
            thresholds=(
                "Green under 35 days, Amber 35 to 50 days, Red over 50 days"
            ),
            health=_pair_health(foundation.TTF_GREEN_DAYS, foundation.TTF_AMBER_DAYS),
            compute=time_to_fill,
            no_data_reason="No requisition has been filled by a joiner yet.",
            proxy_note=(
                "Closed at the first joining rather than at offer acceptance, "
                "which has no object of its own; the band is the "
                "specification's tech band, requisitions are not yet "
                "classified tech vs non-tech."
            ),
        ),
        MetricDefinition(
            metric_id="CNPS",
            title="Candidate Net Promoter Score",
            formula="c-NPS = % Promoters (9 to 10) - % Detractors (0 to 6)",
            unit="points",
            thresholds=(
                "Green at or above plus 60.0, Amber plus 30.0 to plus 59.9, "
                "Red under plus 30.0"
            ),
            health=_pair_health(CNPS_GREEN, CNPS_AMBER),
            no_data_reason=(
                "No candidate experience survey is collected, so sentiment "
                "cannot be measured."
            ),
        ),
        MetricDefinition(
            metric_id="EBDI",
            title="Interviewer Evaluation Bias and Dispersion Index",
            formula=(
                "EBDI = Standard Deviation of Interview Scores per Reviewer / "
                "Mean Score of Peer Reviewers for Same Job Family"
            ),
            unit="index",
            thresholds=(
                "Green 0.85 to 1.15 (calibrated), Amber 0.70 to 0.84 or 1.16 "
                "to 1.30, Red under 0.70 (harsh) or over 1.30 (lax)"
            ),
            health=_ebdi_health,
            no_data_reason=_NO_NUMERIC_INTERVIEW_SCORES,
        ),
        # ── Supplementary ids: dashboards the section 2 taxonomy demands
        #    whose metric the section 3 dictionary never defines. Each is an
        #    honest no_data placeholder so its dashboard renders a reason,
        #    never an invented figure. ─────────────────────────────────────
        MetricDefinition(
            metric_id="TALENT_FLOW",
            title="Talent Migration Flow",
            formula=(
                "Inbound and outbound talent movement by source and "
                "destination company (no formula is defined by the metric "
                "dictionary)"
            ),
            unit="companies",
            thresholds="No health bands are defined for this metric",
            no_data_reason=(
                "No talent migration data source is connected; competitor "
                "flow needs an external market intelligence feed."
            ),
        ),
        MetricDefinition(
            metric_id="RECRUITER_LOAD",
            title="Active Requisition Load per Recruiter",
            formula=(
                "Open requisitions assigned per recruiter (no formula is "
                "defined by the metric dictionary; the nudge threshold is 18 "
                "requisitions per recruiter)"
            ),
            unit="requisitions",
            thresholds=(
                "Rebalancing is suggested when active requisitions exceed 18 "
                "per recruiter"
            ),
            no_data_reason=(
                "Jobs are not assigned to individual recruiters; there is no "
                "assignment table, so per-recruiter load cannot be measured."
            ),
        ),
        MetricDefinition(
            metric_id="DEI_PARITY",
            title="Funnel Pass-Through Parity",
            formula=(
                "Stage-wise pass-through rate per demographic cohort, with a "
                "disparity alert when the delta between cohorts exceeds 15 "
                "percent"
            ),
            unit="%",
            thresholds=(
                "A disparity alert fires when the pass-through delta between "
                "cohorts exceeds 15 percent"
            ),
            no_data_reason=(
                "The product deliberately collects no demographic attributes "
                "and inferring protected attributes is prohibited; a DEI "
                "funnel needs an explicit, consented demographic intake that "
                "does not exist yet."
            ),
        ),
        MetricDefinition(
            metric_id="CPH",
            title="Cost per Hire",
            formula=(
                "CPH = (Internal Hiring Expenses + External Hiring Expenses) "
                "/ Total Hires (no health bands are defined by the metric "
                "dictionary)"
            ),
            unit="currency",
            thresholds="No health bands are defined for this metric",
            no_data_reason=(
                "Internal and external hiring expenses, agency spend and job "
                "board allocations are not captured."
            ),
        ),
    )
}


def health_for(metric_id: str, value: float) -> str:
    """The health WORD for one metric at one value. Raises KeyError for an
    unknown metric and ValueError for a metric with no bands, because a
    caller asking for a band that does not exist has a bug, not a grey area."""
    definition = METRICS[metric_id]
    if definition.health is None:
        raise ValueError(f"metric {metric_id} has no health bands defined")
    return definition.health(value)


async def compute_metric(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    metric_id: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """One metric, fully resolved for one tenant.

    Always returns the same shape: the definition's copy plus `value`,
    `status` (green / amber / red / no_data) and `status_reason` (set exactly
    when status is no_data). A metric with no compute path and a computable
    metric with no rows both come back as no_data with their reason; neither
    is ever a fabricated zero.
    """
    definition = METRICS[metric_id]
    payload: dict[str, Any] = {
        "metric_id": definition.metric_id,
        "title": definition.title,
        "formula": definition.formula,
        "unit": definition.unit,
        "thresholds": definition.thresholds,
        "proxy_note": definition.proxy_note,
        "value": None,
        "status": "no_data",
        "status_reason": definition.no_data_reason,
        "inputs": {},
    }
    if definition.compute is None:
        return payload

    computed = await definition.compute(session, tenant_id, since=since, until=until)
    payload["inputs"] = computed.get("inputs", {})
    if "segments" in computed:
        payload["segments"] = computed["segments"]
    if computed.get("value") is None:
        return payload
    payload["value"] = computed["value"]
    payload["status"] = computed["status"]
    payload["status_reason"] = None
    return payload
