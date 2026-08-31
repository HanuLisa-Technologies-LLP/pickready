"""Per-report data builders (Master Directive Part 4 sections 2 and 3).

Each builder compiles ONE report's content from the stored tables and returns
a format-neutral `ReportContent`; the engine renders that as PDF or CSV. The
builders read the same models the product writes (no shadow tables, no
telemetry side-store): Part 4 says data is "pulled from ReadyPick telemetry
store at generation time", and in this codebase the operational tables ARE
that store.

Every query filters by tenant_id explicitly even though the request session
is already RLS-scoped: belt and braces, matching the rest of the services.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import (
    AssessmentConversation,
    FunctionalSkillsReport,
    ReportDimension,
)
from app.models.billing import (
    EVENT_COMPLETED,
    EVENT_GRANT,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    CreditLedgerEntry,
)
from app.models.candidate import Candidate, JobCandidateLink, PipelineStatusEntry
from app.models.enums import PipelineStatus
from app.models.job import Job
from app.services import hiring_pipeline as hp
from app.services import rating
from app.services.credits import credits_from_subunits


# ── Format-neutral content model ─────────────────────────────────────────────

@dataclass
class ReportTable:
    title: str | None
    columns: list[str]
    rows: list[list[str]]


@dataclass
class ReportContent:
    #: Key-value summary lines rendered above the tables.
    summary: list[tuple[str, str]] = field(default_factory=list)
    tables: list[ReportTable] = field(default_factory=list)
    #: Methodology / caveat lines rendered at the foot of the report.
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReportParams:
    """The parsed, typed generate-request parameters (Part 4 section 1.1)."""
    date_from: datetime | None = None
    date_to: datetime | None = None
    job_id: uuid.UUID | None = None
    department: str | None = None

    def range_label(self) -> str:
        if not self.date_from and not self.date_to:
            return "All time"
        start = self.date_from.date().isoformat() if self.date_from else "beginning"
        end = self.date_to.date().isoformat() if self.date_to else "today"
        return f"{start} to {end}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """Coerce a naive timestamp to UTC so subtraction never raises."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _days_since(value: datetime | None) -> int | None:
    value = _aware(value)
    if value is None:
        return None
    return max(0, (_now() - value).days)


def _fmt_credits(subunits: int) -> str:
    return str(credits_from_subunits(subunits))


# ── B-01 Candidate Pipeline Status ───────────────────────────────────────────

async def build_pipeline_status(
    session: AsyncSession, tenant_id: uuid.UUID, params: ReportParams
) -> ReportContent:
    """All candidates across open (non-archived) jobs: current stage, days in
    stage, assessment status, next action (Part 4 table B-01)."""
    stmt = (
        select(
            Job.title,
            Job.department,
            Candidate.full_name,
            Candidate.email,
            JobCandidateLink.status,
            JobCandidateLink.status_updated_at,
            JobCandidateLink.created_at,
            AssessmentConversation.status.label("assessment_status"),
            AssessmentConversation.completed_at,
        )
        .join(Job, Job.id == JobCandidateLink.job_id)
        .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
        .outerjoin(
            AssessmentConversation,
            AssessmentConversation.job_candidate_link_id == JobCandidateLink.id,
        )
        .where(
            JobCandidateLink.tenant_id == tenant_id,
            JobCandidateLink.archived_at.is_(None),
            Job.archived_at.is_(None),
        )
        .order_by(Job.title, JobCandidateLink.status, JobCandidateLink.created_at)
    )
    if params.job_id:
        stmt = stmt.where(JobCandidateLink.job_id == params.job_id)
    if params.department:
        stmt = stmt.where(Job.department == params.department)
    if params.date_from:
        stmt = stmt.where(JobCandidateLink.created_at >= params.date_from)
    if params.date_to:
        stmt = stmt.where(JobCandidateLink.created_at <= params.date_to)

    rows = (await session.execute(stmt)).all()

    # "Next action required": the step a recruiter would take next, phrased
    # from the pipeline's forward path (services/hiring_pipeline).
    _NEXT_ACTION: dict[str, str] = {
        hp.APPLIED: "Invite to assessment",
        hp.ASSESSMENT_INVITED: "Awaiting candidate start",
        hp.ASSESSMENT_IN_PROGRESS: "Awaiting assessment completion",
        hp.ASSESSMENT_COMPLETED: "HM review: shortlist or reject",
        hp.SHORTLISTED: "Schedule interview",
        hp.INTERVIEW_SCHEDULED: "Hold interview",
        hp.INTERVIEW_COMPLETED: "Extend offer or next round",
        hp.OFFER_EXTENDED: "Track joining",
        hp.OFFERED: "Track joining",
        hp.HOLD: "Resume from hold",
    }

    def next_action(status: str, assessment_status: str | None) -> str:
        if status in hp.TERMINAL:
            return "None (closed)"
        return _NEXT_ACTION.get(status, "Review")

    table_rows: list[list[str]] = []
    stage_counts: dict[str, int] = {}
    for r in rows:
        stage = hp.STAGE_LABELS.get(r.status, r.status)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        days = _days_since(r.status_updated_at or r.created_at)
        table_rows.append([
            r.full_name or r.email or "(unnamed)",
            r.title,
            r.department or "-",
            stage,
            "-" if days is None else str(days),
            (r.assessment_status or "not started").replace("_", " "),
            next_action(r.status, r.assessment_status),
        ])

    content = ReportContent()
    content.summary.append(("Candidates in pipeline", str(len(table_rows))))
    for stage, count in sorted(stage_counts.items(), key=lambda kv: -kv[1]):
        content.summary.append((f"  {stage}", str(count)))
    content.tables.append(ReportTable(
        title="Candidate Pipeline",
        columns=["Candidate", "Job", "Department", "Current Stage",
                 "Days in Stage", "Assessment", "Next Action"],
        rows=table_rows,
    ))
    return content


# ── B-03 HM Profile Review SLA ───────────────────────────────────────────────

#: Hours within which an HM decision is on-SLA. Part 2 section 3.2 CSR_aging
#: sets the HM Review stagnation threshold at 2 days; the SLA line uses the
#: same figure so the two reports cannot disagree about what "late" means.
HM_REVIEW_SLA_HOURS = 48

#: The stages that mean "a profile is sitting with the Hiring Manager".
#: PROXY, DOCUMENTED: this schema has no explicit HM-decision timestamp
#: (EV_HM_DECISION in the directive's telemetry vocabulary). What it does have
#: is the append-only pipeline_status history (FR-8.4), where a completed
#: assessment enters `assessment_completed` and the decision is the NEXT
#: transition out of it (shortlisted / rejected / hold). Review latency is
#: therefore measured as the gap between those two history rows. When a true
#: HM-decision event lands in telemetry this builder should switch to it.
_REVIEW_ENTRY = PipelineStatus.assessment_completed
_DECISION_STATUSES = {
    PipelineStatus.shortlisted,
    PipelineStatus.rejected,
    PipelineStatus.hold,
}


async def build_hm_review_sla(
    session: AsyncSession, tenant_id: uuid.UUID, params: ReportParams
) -> ReportContent:
    stmt = (
        select(
            PipelineStatusEntry.job_candidate_link_id,
            PipelineStatusEntry.status,
            PipelineStatusEntry.at,
            Job.title,
            Job.department,
        )
        .join(
            JobCandidateLink,
            JobCandidateLink.id == PipelineStatusEntry.job_candidate_link_id,
        )
        .join(Job, Job.id == JobCandidateLink.job_id)
        .where(PipelineStatusEntry.tenant_id == tenant_id)
        .order_by(PipelineStatusEntry.job_candidate_link_id, PipelineStatusEntry.at)
    )
    if params.job_id:
        stmt = stmt.where(JobCandidateLink.job_id == params.job_id)
    if params.department:
        stmt = stmt.where(Job.department == params.department)
    if params.date_from:
        stmt = stmt.where(PipelineStatusEntry.at >= params.date_from)
    if params.date_to:
        stmt = stmt.where(PipelineStatusEntry.at <= params.date_to)

    rows = (await session.execute(stmt)).all()

    # Walk each link's history in order: review latency is entry -> decision.
    decided: list[tuple[str, str, float]] = []      # (job, dept, hours)
    waiting: list[tuple[str, str, float]] = []      # still undecided
    entry_at: dict[uuid.UUID, tuple[datetime, str, str]] = {}
    for r in rows:
        at = _aware(r.at)
        if r.status == _REVIEW_ENTRY:
            entry_at[r.job_candidate_link_id] = (at, r.title, r.department or "-")
        elif r.status in _DECISION_STATUSES and r.job_candidate_link_id in entry_at:
            started, title, dept = entry_at.pop(r.job_candidate_link_id)
            decided.append((title, dept, (at - started).total_seconds() / 3600.0))
    for started, title, dept in entry_at.values():
        waiting.append((title, dept, (_now() - started).total_seconds() / 3600.0))

    within = sum(1 for _, _, h in decided if h <= HM_REVIEW_SLA_HOURS)
    avg_hours = sum(h for _, _, h in decided) / len(decided) if decided else 0.0
    compliance = (100.0 * within / len(decided)) if decided else 100.0
    overdue = [w for w in waiting if w[2] > HM_REVIEW_SLA_HOURS]

    content = ReportContent()
    content.summary.extend([
        ("Profiles decided in period", str(len(decided))),
        ("Average hours to decision", f"{avg_hours:.1f}"),
        (f"SLA compliance ({HM_REVIEW_SLA_HOURS}h)", f"{compliance:.0f}%"),
        ("Profiles awaiting decision", str(len(waiting))),
        ("Waiting beyond SLA", str(len(overdue))),
    ])
    if overdue:
        content.tables.append(ReportTable(
            title="Candidates Waiting Beyond SLA",
            columns=["Job", "Department", "Hours Waiting"],
            rows=[[t, d, f"{h:.0f}"] for t, d, h in
                  sorted(overdue, key=lambda w: -w[2])],
        ))
    content.notes.append(
        "Review latency is measured from the assessment_completed pipeline "
        "transition to the next decision transition (shortlist / reject / "
        "hold); the platform stores no separate HM-decision timestamp yet."
    )
    return content


# ── C-01 Credit Consumption ──────────────────────────────────────────────────

#: Ledger event -> the directive's completion split (Part 4 table C-01).
_COMPLETION_SPLIT = {
    EVENT_COMPLETED: "Full",
    EVENT_INCOMPLETE: "Partial",
    EVENT_NO_SHOW: "Unfilled",
    EVENT_OLD_PROFILE_REVIEW: "Old profile review",
}


async def build_credit_consumption(
    session: AsyncSession, tenant_id: uuid.UUID, params: ReportParams
) -> ReportContent:
    ranged = [CreditLedgerEntry.tenant_id == tenant_id]
    if params.date_from:
        ranged.append(CreditLedgerEntry.created_at >= params.date_from)
    if params.date_to:
        ranged.append(CreditLedgerEntry.created_at <= params.date_to)

    month = func.to_char(CreditLedgerEntry.created_at, "YYYY-MM")
    per_month = (
        await session.execute(
            select(
                month.label("month"),
                CreditLedgerEntry.event_type,
                func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0),
                func.count(),
            )
            .where(*ranged)
            .group_by(month, CreditLedgerEntry.event_type)
            .order_by(month)
        )
    ).all()

    # STEM vs Non-STEM split (Part 3 section 5.2: every deduction row carries
    # the classification it was billed at in metadata_json).
    classification = func.coalesce(
        CreditLedgerEntry.metadata_json["role_classification"].astext,
        "NON_STEM",
    ).label("classification")
    per_class = (
        await session.execute(
            select(
                classification,
                func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0),
                func.count(),
            )
            .where(*ranged, CreditLedgerEntry.subunits_delta < 0)
            .group_by(classification)
        )
    ).all()

    # Balance is all-time (SUM over the whole ledger), never range-clipped: a
    # remaining figure that ignored last month's grant would be a lie.
    balance = (
        await session.execute(
            select(func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0))
            .where(CreditLedgerEntry.tenant_id == tenant_id)
        )
    ).scalar_one()

    purchased = sum(s for _, ev, s, _ in per_month if s > 0)
    consumed = -sum(s for _, ev, s, _ in per_month if s < 0)
    split_counts: dict[str, tuple[int, int]] = {}
    for _, ev, s, n in per_month:
        if s < 0 and ev in _COMPLETION_SPLIT:
            label = _COMPLETION_SPLIT[ev]
            subs, cnt = split_counts.get(label, (0, 0))
            split_counts[label] = (subs - s, cnt + n)

    content = ReportContent()
    content.summary.extend([
        ("Credits purchased (period)", _fmt_credits(purchased)),
        ("Credits consumed (period)", _fmt_credits(consumed)),
        ("Credits remaining (all time)", _fmt_credits(balance)),
    ])
    content.tables.append(ReportTable(
        title="Consumption by Month and Event Type",
        columns=["Month", "Event", "Entries", "Credits"],
        rows=[
            [m, ("Grant" if ev == EVENT_GRANT else _COMPLETION_SPLIT.get(ev, ev)),
             str(n), _fmt_credits(abs(s))]
            for m, ev, s, n in per_month
        ],
    ))
    content.tables.append(ReportTable(
        title="Completion Split (Full / Partial / Unfilled)",
        columns=["Outcome", "Assessments", "Credits"],
        rows=[[label, str(cnt), _fmt_credits(subs)]
              for label, (subs, cnt) in sorted(split_counts.items())],
    ))
    content.tables.append(ReportTable(
        title="STEM vs Non-STEM Consumption",
        columns=["Classification", "Deductions", "Credits"],
        rows=[[cls, str(n), _fmt_credits(-s)] for cls, s, n in per_class],
    ))
    return content


# ── C-02 Credit Forecast ─────────────────────────────────────────────────────

async def build_credit_forecast(
    session: AsyncSession, tenant_id: uuid.UUID, params: ReportParams
) -> ReportContent:
    balance = (
        await session.execute(
            select(func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0))
            .where(CreditLedgerEntry.tenant_id == tenant_id)
        )
    ).scalar_one()
    burn_window_start = _now() - timedelta(days=30)
    burned = (
        await session.execute(
            select(func.coalesce(func.sum(-CreditLedgerEntry.subunits_delta), 0))
            .where(
                CreditLedgerEntry.tenant_id == tenant_id,
                CreditLedgerEntry.subunits_delta < 0,
                CreditLedgerEntry.created_at >= burn_window_start,
            )
        )
    ).scalar_one()
    open_jobs = (
        await session.execute(
            select(func.count()).select_from(Job).where(
                Job.tenant_id == tenant_id, Job.archived_at.is_(None)
            )
        )
    ).scalar_one()

    daily_burn = burned / 30.0  # sub-units per day over the trailing window
    runway_days = (balance / daily_burn) if daily_burn > 0 else None

    content = ReportContent()
    content.summary.extend([
        ("Current balance", _fmt_credits(balance) + " credits"),
        ("Consumed, last 30 days", _fmt_credits(burned) + " credits"),
        ("Average daily burn", str(credits_from_subunits(int(round(daily_burn)))) + " credits/day"),
        ("Active open jobs", str(open_jobs)),
        ("Projected runway",
         "no consumption in the last 30 days" if runway_days is None
         else f"{runway_days:.0f} days at the current burn rate"),
    ])
    rows = []
    for horizon in (30, 60, 90):
        projected = balance - int(round(daily_burn * horizon))
        rows.append([
            f"{horizon} days",
            _fmt_credits(max(projected, 0)) if projected >= 0 else "0.00",
            "OK" if projected > 0 else "Depleted before this point",
        ])
    content.tables.append(ReportTable(
        title="Projected Credits Remaining",
        columns=["Horizon", "Projected Balance", "Status"],
        rows=rows,
    ))
    content.notes.append(
        "Burn rate is the trailing 30-day consumption divided by 30. A "
        "projection is arithmetic, not a commitment: a hiring surge or a "
        "paused job moves it immediately."
    )
    return content


# ── A-02 Batch Assessment Summary ────────────────────────────────────────────

async def build_batch_assessment_summary(
    session: AsyncSession, tenant_id: uuid.UUID, params: ReportParams
) -> ReportContent:
    if params.job_id is None:
        raise ValueError("A-02 requires a job_id parameter")

    job = await session.get(Job, params.job_id)
    stmt = (
        select(
            FunctionalSkillsReport.id,
            FunctionalSkillsReport.overall_score,
            FunctionalSkillsReport.synthesized_at,
            Candidate.full_name,
            Candidate.email,
            JobCandidateLink.match_score,
        )
        .join(
            JobCandidateLink,
            JobCandidateLink.id == FunctionalSkillsReport.job_candidate_link_id,
        )
        .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
        .where(
            FunctionalSkillsReport.tenant_id == tenant_id,
            FunctionalSkillsReport.job_id == params.job_id,
        )
    )
    if params.date_from:
        stmt = stmt.where(FunctionalSkillsReport.synthesized_at >= params.date_from)
    if params.date_to:
        stmt = stmt.where(FunctionalSkillsReport.synthesized_at <= params.date_to)
    reports = (await session.execute(stmt)).all()

    report_ids = [r.id for r in reports]
    dims_by_report: dict[uuid.UUID, list] = {}
    if report_ids:
        dims = (
            await session.execute(
                select(ReportDimension)
                .where(ReportDimension.report_id.in_(report_ids))
                .order_by(ReportDimension.category, ReportDimension.ordinal)
            )
        ).scalars().all()
        for d in dims:
            dims_by_report.setdefault(d.report_id, []).append(d)

    def overall_for(r) -> tuple[str, float]:
        """(word grade, sort score). NO NUMBER leaves this module: the internal
        0-100 score is projected through services/rating, same as the API."""
        score = r.overall_score
        if score is None:
            dim_scores = [d.score for d in dims_by_report.get(r.id, [])]
            score = sum(dim_scores) / len(dim_scores) if dim_scores else 0
        return rating.grade_for_percent(score) or rating.GRADE_NOT, float(score)

    ranked = sorted(reports, key=lambda r: -overall_for(r)[1])
    rows = []
    for rank, r in enumerate(ranked, start=1):
        grade, _ = overall_for(r)
        dim_cells = "; ".join(
            f"{d.name}: {rating.grade_for_percent(d.score)}"
            for d in dims_by_report.get(r.id, [])
        )
        rows.append([
            str(rank),
            r.full_name or r.email or "(unnamed)",
            grade,
            (rating.grade_for_percent(r.match_score) or "-")
            if r.match_score is not None else "-",
            dim_cells or "-",
        ])

    grade_dist: dict[str, int] = {}
    for r in ranked:
        g, _ = overall_for(r)
        grade_dist[g] = grade_dist.get(g, 0) + 1

    content = ReportContent()
    content.summary.append(("Job", job.title if job else str(params.job_id)))
    content.summary.append(("Candidates assessed", str(len(ranked))))
    for g in rating.GRADES:
        if g in grade_dist:
            content.summary.append((f"  {g}", str(grade_dist[g])))
    content.tables.append(ReportTable(
        title="Candidates Ranked by Overall Grade",
        columns=["Rank", "Candidate", "Overall Grade (Siddhi PRISM Report)",
                 "Yukti AI Score", "Grades per Dimension"],
        rows=rows,
    ))
    content.notes.append(
        "Grades are the four-word client scale (services/rating); internal "
        "numeric scores never appear in this document."
    )
    return content


# ── D-03 Candidate Aging & Bottleneck ────────────────────────────────────────

#: Days a candidate may sit in a stage before counting as stagnant. Part 2
#: section 3.2 (CSR_aging) names four thresholds against its own coarse stage
#: vocabulary; this maps them onto the 10-stage pipeline pragmatically:
#:   Sourcing Screen 3d      -> applied and the assessment stages (the stretch
#:                              where the recruiter is screening / waiting)
#:   HM Review 2d            -> assessment_completed (profile with the HM)
#:   Interview Scheduling 4d -> shortlisted, interview_scheduled,
#:                              interview_completed
#:   Offer Decision 3d       -> offer_extended, hold
#: Terminal stages (joined / rejected) never age.
AGING_THRESHOLD_DAYS: dict[str, int] = {
    hp.APPLIED: 3,
    hp.ASSESSMENT_INVITED: 3,
    hp.ASSESSMENT_IN_PROGRESS: 3,
    hp.ASSESSMENT_COMPLETED: 2,
    hp.SHORTLISTED: 4,
    hp.INTERVIEW_SCHEDULED: 4,
    hp.INTERVIEW_COMPLETED: 4,
    hp.OFFER_EXTENDED: 3,
    hp.OFFERED: 3,
    hp.HOLD: 3,
}

#: CSR_aging health benchmarks (Part 2 section 3.2): Green < 10%, Amber
#: 10-20%, Red > 20% of the active pipeline stagnant.
def _csr_health(pct: float) -> str:
    if pct < 10.0:
        return "Green"
    if pct <= 20.0:
        return "Amber"
    return "Red"


async def build_candidate_aging(
    session: AsyncSession, tenant_id: uuid.UUID, params: ReportParams
) -> ReportContent:
    stmt = (
        select(
            Candidate.full_name,
            Candidate.email,
            Job.title,
            Job.department,
            JobCandidateLink.status,
            JobCandidateLink.status_updated_at,
            JobCandidateLink.created_at,
        )
        .join(Job, Job.id == JobCandidateLink.job_id)
        .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
        .where(
            JobCandidateLink.tenant_id == tenant_id,
            JobCandidateLink.archived_at.is_(None),
            Job.archived_at.is_(None),
            JobCandidateLink.status.notin_(list(hp.TERMINAL)),
        )
    )
    if params.department:
        stmt = stmt.where(Job.department == params.department)
    rows = (await session.execute(stmt)).all()

    active = len(rows)
    stagnant: list[list[str]] = []
    per_stage: dict[str, int] = {}
    for r in rows:
        threshold = AGING_THRESHOLD_DAYS.get(r.status)
        if threshold is None:
            continue
        days = _days_since(r.status_updated_at or r.created_at) or 0
        if days > threshold:
            label = hp.STAGE_LABELS.get(r.status, r.status)
            per_stage[label] = per_stage.get(label, 0) + 1
            stagnant.append([
                r.full_name or r.email or "(unnamed)",
                r.title,
                r.department or "-",
                label,
                str(days),
                str(threshold),
            ])
    stagnant.sort(key=lambda row: -int(row[4]))
    csr = (100.0 * len(stagnant) / active) if active else 0.0

    content = ReportContent()
    content.summary.extend([
        ("Active candidates in pipeline", str(active)),
        ("Stagnant beyond threshold", str(len(stagnant))),
        ("Candidate Stagnation Rate (CSR)", f"{csr:.1f}%"),
        ("Health", _csr_health(csr)),
    ])
    if per_stage:
        content.tables.append(ReportTable(
            title="Bottlenecks by Stage",
            columns=["Stage", "Stagnant Candidates"],
            rows=[[s, str(n)] for s, n in sorted(per_stage.items(), key=lambda kv: -kv[1])],
        ))
    content.tables.append(ReportTable(
        title="Stagnant Candidates",
        columns=["Candidate", "Job", "Department", "Stage", "Days in Stage",
                 "Threshold (days)"],
        rows=stagnant,
    ))
    content.notes.append(
        "Thresholds per Part 2 section 3.2 (CSR_aging): sourcing screen 3 "
        "days, HM review 2 days, interview scheduling 4 days, offer decision "
        "3 days, mapped onto the 10-stage pipeline."
    )
    return content


#: report id -> builder. The engine dispatches through this and the catalogue's
#: `implemented` flags must agree with it (pinned by tests).
BUILDERS = {
    "A-02": build_batch_assessment_summary,
    "B-01": build_pipeline_status,
    "B-03": build_hm_review_sla,
    "C-01": build_credit_consumption,
    "C-02": build_credit_forecast,
    "D-03": build_candidate_aging,
}
