"""The 18 Talent Intelligence dashboards and the on-read alerts panel
(2026-09-05 spec, sections 2, 4 and 5.2).

THE REGISTRY IS DATA
--------------------
Each dashboard is a key, a title, a tier, an audience, a description and an
ordered widget list of metric ids from services/intelligence_metrics.METRICS.
The taxonomy is section 2's, verbatim in count and grouping: five dashboards
in Tier A, five in B, five in C, three in D. A dashboard whose metric has no
data surface today still renders, honestly: its widgets come back as no_data
with the reason, never as an invented figure.

ALERTS ARE COMPUTED ON READ, NOT DELIVERED
------------------------------------------
Section 5.2 describes Slack, Teams, email and SMS nudge agents.

ASSUMPTION (2026-09-05 spec section 5.2): Slack/Teams/SMS delivery is an
external integration awaiting credentials and an owner decision on channels,
and is deliberately not wired here; `compute_alerts` is the reusable core
(the threshold evaluation), rendered as the Alerts panel on the dashboards
index. When a delivery channel lands, it calls this same function; a second
threshold evaluation inside a notifier would drift from this one. No
scheduled sweep is added either: the alert set is derived state, recomputed
on every read, so there is nothing for a sweep to persist.

The alert thresholds come from section 4's nudge column (18h nudge, 24h
alert, 48h escalation on HM review latency) and from the product's own
standing credit rules (services/credits.BalanceSummary.warning_level, the
Master Directive Part 5 section 4 absolute tiers), which govern where the
spec's "below 10 percent" wording predates the product's settled absolute
thresholds.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import credits
from app.services import intelligence_metrics
from app.services import metrics as foundation

# ── Section 4's HM review latency ladder, in hours ──────────────────────────
HM_NUDGE_HOURS = 18.0
HM_ALERT_HOURS = 24.0
HM_ESCALATION_HOURS = 48.0


@dataclass(frozen=True)
class Dashboard:
    key: str
    title: str
    tier: str  # "A" | "B" | "C" | "D"
    audience: str
    description: str
    #: Ordered metric ids; every one must exist in intelligence_metrics.METRICS
    #: (asserted by tests/test_intelligence_dashboards.py).
    widgets: tuple[str, ...]


TIER_TITLES: dict[str, str] = {
    "A": "Hiring Manager Accountability and Behavioral",
    "B": "Strategic Leadership and Business Impact",
    "C": "Talent Acquisition Operational and Behavioral",
    "D": "Standard and Foundational ATS",
}

DASHBOARDS: tuple[Dashboard, ...] = (
    # ── Tier A ──────────────────────────────────────────────────────────────
    Dashboard(
        key="hm-sla-command-center",
        title="HM Responsiveness and SLA Command Center",
        tier="A",
        audience="Hiring Managers, BU Heads, TA Ops",
        description=(
            "Tracks hiring manager profile review latency, scorecard "
            "submission speed and SLA compliance against organizational "
            "thresholds."
        ),
        widgets=("PRL", "SLA_PR", "SSL"),
    ),
    Dashboard(
        key="jd-realism-scope-drift",
        title="JD Realism, Calibration and Scope Drift Monitor",
        tier="A",
        audience="Hiring Managers, TA Leads",
        description=(
            "Measures calibration velocity, mandatory versus nice-to-have "
            "skill density, and flags mid-search requirement changes."
        ),
        widgets=("T_CAL", "RRI", "SDR"),
    ),
    Dashboard(
        key="decision-precision",
        title="Interview Decision Precision and Selectivity Ratio",
        tier="A",
        audience="BU Heads, TA Directors",
        description=(
            "Tracks the ratio of interviews conducted to offers extended, "
            "exposing interviewer indecision, vague rubrics or "
            "over-interviewing."
        ),
        widgets=("SR_INT", "SLA_PR"),
    ),
    Dashboard(
        key="stagnation-tracker",
        title="Candidate Stagnation and Aging Bottleneck Tracker",
        tier="A",
        audience="TA Ops, Hiring Managers",
        description=(
            "Monitors candidate aging across pipeline stages, pinpointing "
            "exactly where talent sits stagnant."
        ),
        widgets=("CSR_AGING", "PRL"),
    ),
    Dashboard(
        key="post-offer-realization",
        title="Post-Offer Engagement and Join Realization Heatmap",
        tier="A",
        audience="TA Specialists, Hiring Managers, CHRO",
        description=(
            "Evaluates post-offer engagement, notice-period risk and joining "
            "realization."
        ),
        widgets=("JRR",),
    ),
    # ── Tier B ──────────────────────────────────────────────────────────────
    Dashboard(
        key="cost-of-vacancy",
        title="Cost of Vacancy and Commercial Exposure Tracker",
        tier="B",
        audience="CXO, CFO, Head of TA",
        description=(
            "Quantifies daily revenue loss, billing lag and project risk "
            "caused by unfilled strategic seats."
        ),
        widgets=("COV", "TTF"),
    ),
    Dashboard(
        key="market-supply-feasibility",
        title="Talent Market Supply vs Hiring Feasibility Index",
        tier="B",
        audience="TA Leadership, BU Heads",
        description=(
            "Assesses live talent pool density against JD criteria before "
            "requisition release, predicting time-to-fill feasibility."
        ),
        widgets=("RRI", "T_CAL"),
    ),
    Dashboard(
        key="quality-of-hire",
        title="Quality of Hire and Retention Correlation Matrix",
        tier="B",
        audience="CXO, CHRO, TA Leadership",
        description=(
            "Correlates evaluation outcomes with early performance, "
            "probation clearance and early attrition."
        ),
        widgets=("QOH", "JRR"),
    ),
    Dashboard(
        key="competitor-talent-flow",
        title="Competitor Talent Flow and Migration Intelligence",
        tier="B",
        audience="CXO, TA Leadership",
        description=(
            "Tracks talent migration trends, source companies and "
            "destination competitors for outbound and inbound talent."
        ),
        widgets=("TALENT_FLOW",),
    ),
    Dashboard(
        key="ai-recruiter-performance",
        title="Agentic AI Recruiter Performance and Credit Utilization",
        tier="B",
        audience="TA Specialists, TA Leadership",
        description=(
            "Monitors autonomous sourcing yield, profile match precision and "
            "credit burn efficiency."
        ),
        widgets=("AISP",),
    ),
    # ── Tier C ──────────────────────────────────────────────────────────────
    Dashboard(
        key="recruiter-capacity",
        title="Recruiter Capacity, Throughput and Load Balancer",
        tier="C",
        audience="TA Operations Manager",
        description=(
            "Monitors active requisition load per recruiter, throughput "
            "velocity and burnout risk indicators."
        ),
        widgets=("RECRUITER_LOAD", "CSR_AGING"),
    ),
    Dashboard(
        key="sourcing-channel-roi",
        title="Sourcing Channel ROI and Candidate Quality Index",
        tier="C",
        audience="HR / TA Operations",
        description=(
            "Evaluates yield per channel against downstream joining outcomes "
            "and cost."
        ),
        widgets=("SCEI", "AISP"),
    ),
    Dashboard(
        key="dei-funnel",
        title="Diversity, Equity and Inclusion Funnel Analyzer",
        tier="C",
        audience="CHRO, Head of Diversity",
        description=(
            "Analyzes pass-through rates at every funnel stage to identify "
            "institutional bottlenecks and ensure equitable hiring."
        ),
        widgets=("DEI_PARITY",),
    ),
    Dashboard(
        key="candidate-nps",
        title="Candidate Experience and Net Promoter Score",
        tier="C",
        audience="HR / TA Operations, Employer Brand",
        description=(
            "Captures candidate sentiment, communication timeliness and "
            "brand Net Promoter Score."
        ),
        widgets=("CNPS",),
    ),
    Dashboard(
        key="interviewer-calibration",
        title="Interviewer Calibration and Evaluation Bias Detector",
        tier="C",
        audience="TA Leadership, CHRO",
        description=(
            "Detects variance in interviewer evaluation distributions, "
            "outlier pass and fail rates, and rubric drift."
        ),
        widgets=("EBDI", "SSL"),
    ),
    # ── Tier D ──────────────────────────────────────────────────────────────
    Dashboard(
        key="funnel-pipeline",
        title="End-to-End Funnel and Requisition Pipeline Tracker",
        tier="D",
        audience="All hiring stakeholders",
        description=(
            "Real-time visibility into open requisitions, stage-by-stage "
            "candidate volumes and pipeline health."
        ),
        widgets=("CSR_AGING", "SR_INT", "JRR"),
    ),
    Dashboard(
        key="ttf-waterfall",
        title="Time-to-Fill and Time-to-Hire Waterfall",
        tier="D",
        audience="TA Leadership, BU Heads",
        description=(
            "Deconstructs total hiring duration across sourcing, evaluation "
            "and offer stages."
        ),
        widgets=("TTF",),
    ),
    Dashboard(
        key="cost-per-hire",
        title="Cost-per-Hire and Budget Variance Dashboard",
        tier="D",
        audience="CFO, Head of TA",
        description=(
            "Calculates internal and external hiring expenses, agency spend "
            "variance and cost per hire by level."
        ),
        widgets=("CPH", "COV"),
    ),
)

DASHBOARDS_BY_KEY: dict[str, Dashboard] = {d.key: d for d in DASHBOARDS}


def dashboard_availability(dashboard: Dashboard) -> tuple[int, int]:
    """(widgets with a compute path, total widgets). Registry-level, no
    database: it says whether the PRODUCT can measure the widget, not whether
    this tenant has rows yet."""
    computable = sum(
        1
        for metric_id in dashboard.widgets
        if intelligence_metrics.METRICS[metric_id].compute is not None
    )
    return computable, len(dashboard.widgets)


# ── The alerts panel (section 5.2, computed on read) ────────────────────────


async def compute_alerts(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Current threshold breaches for one tenant, most severe first.

    Each entry: {id, severity ("red" | "amber"), title, detail, link_path}.
    `link_path` is relative, same rule as the candidate feed. An empty list
    means nothing is breaching, which the panel renders as exactly that.
    """
    now = now or datetime.now(timezone.utc)
    alerts: list[dict[str, Any]] = []

    # 1. HM review latency ladder (section 4: nudge at 18h, alert at 24h,
    #    escalation at 48h). A profile awaiting its first decision is a link
    #    still sitting at `applied`.
    row = (
        await session.execute(
            sql_text(
                "SELECT "
                "COUNT(*) FILTER (WHERE created_at <= :t18) AS over_nudge, "
                "COUNT(*) FILTER (WHERE created_at <= :t24) AS over_alert, "
                "COUNT(*) FILTER (WHERE created_at <= :t48) AS over_escalation "
                "FROM job_candidate_links "
                "WHERE tenant_id = :tid AND archived_at IS NULL "
                "AND status = 'applied'"
            ),
            {
                "tid": str(tenant_id),
                "t18": now - timedelta(hours=HM_NUDGE_HOURS),
                "t24": now - timedelta(hours=HM_ALERT_HOURS),
                "t48": now - timedelta(hours=HM_ESCALATION_HOURS),
            },
        )
    ).mappings().one()
    over_escalation = int(row["over_escalation"] or 0)
    over_alert = int(row["over_alert"] or 0) - over_escalation
    over_nudge = int(row["over_nudge"] or 0) - over_alert - over_escalation
    if over_escalation:
        alerts.append(
            {
                "id": "hm-review-escalation",
                "severity": "red",
                "title": "Profile reviews past 48 hours",
                "detail": (
                    f"{over_escalation} submitted "
                    f"{'profile has' if over_escalation == 1 else 'profiles have'} "
                    "waited over 48 hours for a first decision. The "
                    "specification escalates these to the BU head."
                ),
                "link_path": "/org/candidates",
            }
        )
    if over_alert:
        alerts.append(
            {
                "id": "hm-review-alert",
                "severity": "red",
                "title": "Profile reviews past 24 hours",
                "detail": (
                    f"{over_alert} submitted "
                    f"{'profile has' if over_alert == 1 else 'profiles have'} "
                    "waited between 24 and 48 hours for a first decision, "
                    "outside the 24 hour SLA window."
                ),
                "link_path": "/org/candidates",
            }
        )
    if over_nudge:
        alerts.append(
            {
                "id": "hm-review-nudge",
                "severity": "amber",
                "title": "Profile reviews approaching the SLA",
                "detail": (
                    f"{over_nudge} submitted "
                    f"{'profile has' if over_nudge == 1 else 'profiles have'} "
                    "waited between 18 and 24 hours for a first decision."
                ),
                "link_path": "/org/candidates",
            }
        )

    # 2. Credit protection (section 5.2 agent 5), on the product's standing
    #    absolute thresholds rather than the spec's percentage wording; the
    #    invitation gate itself already blocks at zero, this panel says so
    #    where the operational metrics live.
    summary = await credits.summarize(session, uuid.UUID(str(tenant_id)))
    if not summary.unlimited:
        if summary.exhausted and summary.granted_subunits > 0:
            alerts.append(
                {
                    "id": "credits-exhausted",
                    "severity": "red",
                    "title": "Credit pool exhausted",
                    "detail": (
                        "The credit balance is at or below zero. New "
                        "assessment invitations are paused until a top up; "
                        "interviews, reviews and offers already in motion "
                        "continue."
                    ),
                    "link_path": "/org/billing",
                }
            )
        elif summary.warning_level >= 2:
            alerts.append(
                {
                    "id": "credits-critical",
                    "severity": "red",
                    "title": "Credit balance critical",
                    "detail": (
                        "The credit balance is at or below 10 credits. "
                        "Automated sourcing work will pause when it reaches "
                        "zero."
                    ),
                    "link_path": "/org/billing",
                }
            )
        elif summary.warning_level == 1:
            alerts.append(
                {
                    "id": "credits-low",
                    "severity": "amber",
                    "title": "Credit balance running low",
                    "detail": "The credit balance is at or below 20 credits.",
                    "link_path": "/org/billing",
                }
            )

    # 3. Pipeline stagnation, from the same engine the dashboards read.
    stagnation = await foundation.candidate_stagnation_rate(session, tenant_id, now=now)
    if stagnation["status"] in ("amber", "red"):
        stale = stagnation["inputs"]["stale_candidates"]
        alerts.append(
            {
                "id": "pipeline-stagnation",
                "severity": stagnation["status"],
                "title": "Candidates stagnating in the pipeline",
                "detail": (
                    f"{stale} active "
                    f"{'candidate has' if stale == 1 else 'candidates have'} "
                    "been inactive in their current stage beyond its aging "
                    "threshold."
                ),
                "link_path": "/org/candidates",
            }
        )

    severity_rank = {"red": 0, "amber": 1}
    alerts.sort(key=lambda alert: severity_rank.get(alert["severity"], 2))
    return alerts
