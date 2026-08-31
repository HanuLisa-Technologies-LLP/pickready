"""The Report Library catalogue, as data (Master Directive Part 4 section 2).

Every report the directive's Part 4 tables define is a row here: id, name,
category, what it shows, data sources, typed parameters, output formats,
schedule options and role access. The API serves this table filtered to the
caller's role; `implemented` is what lets the UI grey out a report whose
builder has not shipped yet instead of pretending it does not exist.

A COUNTING NOTE, RECORDED SO NOBODY "FIXES" IT THE WRONG WAY. Part 4's
headline says "31 reports" throughout, but its own category tables enumerate
37 distinct report ids: A-01..A-07 (7), B-01..B-05 (5), C-01..C-04 (4),
D-01..D-04 (4), E-01..E-05 (5), F-01..F-05 (5), G-01..G-07 (7). This module
follows the TABLES, which are the operative specification ("Build every
report exactly as specified"), so the catalogue carries all 37 plus the
DEI report below. tests/test_report_library.py pins the per-category counts
so a drift in either direction is a failure, not a shrug.

THE DEI REPORT (Part 4 section 4). Any DEI pipeline analysis must not process
demographic data until the client consent framework exists, and the directive
says to mark it 'Coming Soon' in the Report Library until then. It is
catalogued with `coming_soon=True` and no builder; the engine refuses it with
a message that names the consent requirement.

ROLE MAPPING (Part 4 section 1.2 onto this codebase's Role enum). The
directive's personas do not exist 1:1 here, so they are mapped pragmatically:

  HR Recruiter          -> Role.recruiter
  Hiring Manager        -> Role.hiring_manager (job-scoped by RBAC elsewhere)
  HR Head / CHRO        -> Role.client, Role.hr_manager, Role.recruitment_manager
  Leadership / BU Head,
  Management / CXO,
  CFO / Board           -> Role.client (the customer's Company Admin is the
                           only leadership-tier seat this codebase has; the
                           org-wide HR roles are included because section 1.2
                           grants "HR Head / CHRO: all reports for their
                           organisation", which subsumes every row)
  Provider Portal       -> the owner portal (super_admin audience), which does
                           not reach this org-scoped router at all; cross-
                           client reports (Part 4 section 5) are out of scope
                           for this catalogue.

Because "HR Head / CHRO" holds ALL reports for the organisation, every entry
grants the three org-wide roles; recruiter and hiring_manager appear only
where their column in the Part 4 table names them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import Role

# ── Typed parameter vocabulary ───────────────────────────────────────────────
# The types the generate endpoint knows how to parse. Anything the directive
# names that has no structured meaning yet (e.g. "sourcing channel") is typed
# as `text` so the catalogue can still declare it without the API inventing
# semantics for it.
PARAM_TYPES: tuple[str, ...] = (
    "date_range",     # date_from + date_to
    "job_id",
    "department",
    "candidate_id",
    "grade",          # one of the four word grades (services/rating)
    "stage",          # a hiring_pipeline stage
    "hm_user_id",
    "month",
    "quarter",
    "year",
    "forecast_window",  # 30 / 60 / 90 days
    "min_score",
    "text",
)

FORMAT_PDF = "pdf"
FORMAT_EXCEL = "excel"
FORMAT_CSV = "csv"


@dataclass(frozen=True)
class ReportParameter:
    name: str
    type: str
    required: bool = False

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"unknown parameter type {self.type!r} on {self.name!r}")


@dataclass(frozen=True)
class ReportDefinition:
    id: str
    name: str
    category: str                       # "A".."G", or "DEI"
    description: str                    # the table's "What It Shows"
    data_sources: tuple[str, ...]
    parameters: tuple[ReportParameter, ...]
    formats: tuple[str, ...]            # subset of pdf/excel/csv
    schedules: tuple[str, ...]          # e.g. ("on_demand", "weekly")
    access: frozenset[Role]
    implemented: bool = False
    coming_soon: bool = False
    notes: tuple[str, ...] = field(default=())


CATEGORIES: dict[str, str] = {
    "A": "Candidate Assessment Reports",
    "B": "Hiring Process & Pipeline Reports",
    "C": "Credit & Financial Reports",
    "D": "HM Accountability & Performance Reports",
    "E": "Quality & Strategic Reports",
    "F": "Leadership & Board Reports",
    "G": "Creative, Innovative & Business-Useful Reports",
    "DEI": "DEI Reports (conditional activation, Part 4 section 4)",
}

# Access shorthands (see the role-mapping note in the module docstring).
_ORG_WIDE = frozenset({Role.client, Role.hr_manager, Role.recruitment_manager})
_PLUS_RECRUITER = _ORG_WIDE | {Role.recruiter}
_PLUS_HM = _ORG_WIDE | {Role.hiring_manager}
_PLUS_BOTH = _ORG_WIDE | {Role.recruiter, Role.hiring_manager}

# Parameter shorthands.
_P_RANGE = ReportParameter("date_range", "date_range")
_P_JOB = ReportParameter("job_id", "job_id")
_P_JOB_REQ = ReportParameter("job_id", "job_id", required=True)
_P_DEPT = ReportParameter("department", "department")
_P_CAND = ReportParameter("candidate_id", "candidate_id", required=True)
_P_GRADE = ReportParameter("grade", "grade")


def _r(**kwargs) -> ReportDefinition:
    return ReportDefinition(**kwargs)


CATALOGUE: tuple[ReportDefinition, ...] = (
    # ── Category A: Candidate Assessment Reports ─────────────────────────────
    _r(
        id="A-01", name="Individual PRISM Report", category="A",
        description=(
            "Full Predictive Role Intelligence & Suitability Mapping for one "
            "candidate: Overall Grade, Must-have, Nice-to-have, Behavioural "
            "dimensions, Gap Analysis, interview probes, Validation section."
        ),
        data_sources=("Siddhi PRISM Report", "Miti Scoring Data", "Vaada Conversation Analysis"),
        parameters=(_P_JOB_REQ, _P_CAND),
        formats=(FORMAT_PDF,),
        schedules=("on_demand",),
        access=_PLUS_BOTH,
        # The PRISM PDF already ships from services/report_pdf via its own
        # route; this catalogue entry points the library UI at that document
        # rather than double-rendering it here.
        notes=("Served today by the PRISM report route (services/report_pdf).",),
    ),
    _r(
        id="A-02", name="Batch Assessment Summary", category="A",
        description=(
            "All candidates assessed for one job, ranked by Overall Grade "
            "with Yukti AI Score, grade per dimension, and hard-cap flags."
        ),
        data_sources=("Siddhi PRISM Report", "Yukti AI Score"),
        parameters=(_P_JOB_REQ, _P_RANGE, _P_GRADE),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("on_demand", "weekly"),
        access=_PLUS_BOTH,
        implemented=True,
    ),
    _r(
        id="A-03", name="AI Score vs Full Assessment Report", category="A",
        description=(
            "Compares the Yukti AI Score (resume-based) with the Siddhi full "
            "PRISM grade, identifying where the AI Score over- or "
            "under-predicted quality."
        ),
        data_sources=("Yukti AI Score", "Siddhi PRISM Report"),
        parameters=(_P_JOB, _P_RANGE),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly",),
        access=_PLUS_RECRUITER,
    ),
    _r(
        id="A-04", name="Gap Analysis Aggregate", category="A",
        description=(
            "All gaps identified by Siddhi across all candidates for a job, "
            "ranked by severity and frequency; shows which competencies are "
            "most commonly missing."
        ),
        data_sources=("Siddhi PRISM Report (Gap Analysis section)",),
        parameters=(_P_JOB_REQ, ReportParameter("dimension", "text")),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("on_demand", "quarterly"),
        access=_PLUS_BOTH,
    ),
    _r(
        id="A-05", name="Tatva Assessment Matrix Document", category="A",
        description=(
            "The Sutra-generated evaluation matrix for a specific job: all "
            "Must-have, Nice-to-have and Behavioural items. Reference for "
            "panel interviewers."
        ),
        data_sources=("Sutra Tatva Matrix", "Bodha SWOT Intake"),
        parameters=(_P_JOB_REQ,),
        formats=(FORMAT_PDF,),
        schedules=("on_demand",),
        access=_PLUS_HM,
    ),
    _r(
        id="A-06", name="Scoring Consistency Report", category="A",
        description=(
            "Miti scoring distribution across a job: grade distribution, "
            "hard-cap applications, scoring patterns across the candidate pool."
        ),
        data_sources=("Miti Scoring Data",),
        parameters=(_P_JOB, _P_RANGE),
        formats=(FORMAT_EXCEL, FORMAT_PDF, FORMAT_CSV),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="A-07", name="Hidden Talent Report", category="A",
        description=(
            "Candidates graded Moderately Matching overall (due to a hard-cap "
            "on one Must-have item) but who scored Strong or Outstanding on "
            "multiple other dimensions."
        ),
        data_sources=("Siddhi PRISM Report", "Miti Scoring Data"),
        parameters=(_P_JOB, _P_RANGE, ReportParameter("min_dimension_score", "min_score")),
        formats=(FORMAT_PDF,),
        schedules=("on_demand", "quarterly"),
        access=_PLUS_HM,
    ),
    # ── Category B: Hiring Process & Pipeline Reports ────────────────────────
    _r(
        id="B-01", name="Candidate Pipeline Status", category="B",
        description=(
            "All candidates across open jobs: current stage, days in stage, "
            "assessment status, next action required."
        ),
        data_sources=("job_candidate_links", "pipeline_status", "assessment_conversations"),
        parameters=(_P_RANGE, _P_JOB, _P_DEPT, ReportParameter("stage", "stage")),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("daily", "on_demand"),
        access=_PLUS_RECRUITER,
        implemented=True,
    ),
    _r(
        id="B-02", name="Time-to-Fill Deconstruction", category="B",
        description=(
            "Breaks hiring duration into JD Setup, Assessment, HM Review, "
            "Offer Stage and Notice Period, with benchmarks."
        ),
        data_sources=("jobs", "pipeline_status"),
        parameters=(_P_JOB, _P_DEPT, _P_RANGE),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="B-03", name="HM Profile Review SLA Report", category="B",
        description=(
            "Hiring Manager review latency: average hours to Accept/Reject, "
            "SLA compliance percent, candidates waiting beyond threshold."
        ),
        data_sources=("pipeline_status",),
        parameters=(ReportParameter("hm_user_id", "hm_user_id"), _P_DEPT, _P_RANGE),
        formats=(FORMAT_PDF,),
        schedules=("weekly",),
        access=_PLUS_RECRUITER,
        implemented=True,
    ),
    _r(
        id="B-04", name="Offer & Join Realization Report", category="B",
        description=(
            "Offer acceptance rate, join realization rate, notice period "
            "analysis, renege risk flags."
        ),
        data_sources=("pipeline_status", "job_candidate_links"),
        parameters=(_P_RANGE, _P_DEPT, _P_JOB),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="B-05", name="Candidate Experience Report", category="B",
        description=(
            "Candidate-reported feedback score (c-NPS): promoters, passives, "
            "detractors by communication quality and process clarity."
        ),
        data_sources=("candidate post-assessment survey (not collected yet)",),
        parameters=(_P_RANGE, _P_JOB, ReportParameter("outcome", "text")),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
        # Not DEI-dependent (Part 4 section 4 says so explicitly), but the
        # platform stores no candidate survey responses yet.
    ),
    # ── Category C: Credit & Financial Reports ───────────────────────────────
    _r(
        id="C-01", name="Credit Consumption Report", category="C",
        description=(
            "Credits purchased, consumed and remaining, broken down by month "
            "and event type, with the full/partial/unfilled completion split "
            "and the STEM vs Non-STEM rate split."
        ),
        data_sources=("credit_ledger", "jobs"),
        parameters=(_P_RANGE, _P_DEPT),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly", "on_demand"),
        access=_PLUS_RECRUITER,
        implemented=True,
    ),
    _r(
        id="C-02", name="Credit Forecast Report", category="C",
        description=(
            "Projected credits remaining based on the current burn rate and "
            "active open jobs, with a 30/60/90 day runway."
        ),
        data_sources=("credit_ledger", "jobs"),
        parameters=(ReportParameter("forecast_window", "forecast_window"),),
        formats=(FORMAT_PDF,),
        schedules=("weekly",),
        access=_PLUS_RECRUITER,
        implemented=True,
    ),
    _r(
        id="C-03", name="Cost-per-Hire Report", category="C",
        description=(
            "Total ReadyPick assessment cost per hired candidate: credits "
            "consumed x Rs 600, benchmarked against industry cost-per-hire "
            "norms."
        ),
        data_sources=("credit_ledger", "pipeline_status (joined stage)"),
        parameters=(_P_RANGE, _P_DEPT, _P_JOB),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="C-04", name="Assessment ROI Report", category="C",
        description=(
            "Business case: estimated cost of bad hires avoided, time saved "
            "vs manual screening, revenue-at-risk mitigation using QoH and "
            "join realization data."
        ),
        data_sources=("credit_ledger", "QoH data (not stored yet)", "CoV data (not stored yet)"),
        parameters=(_P_RANGE, ReportParameter("quarter", "quarter")),
        formats=(FORMAT_PDF,),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    # ── Category D: HM Accountability & Performance Reports ──────────────────
    _r(
        id="D-01", name="HM Effectiveness Scorecard", category="D",
        description=(
            "Per-HM monthly performance: profile review speed, SLA "
            "compliance, scorecard submission latency, interview-to-offer "
            "ratio, join realization rate."
        ),
        data_sources=("pipeline_status", "users"),
        parameters=(ReportParameter("month", "month"), ReportParameter("hm_user_id", "hm_user_id"), _P_DEPT),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="D-02", name="JD Realism Analysis Report", category="D",
        description=(
            "Post-hire analysis of how realistic the original JD was: "
            "compares Tatva Matrix Must-have items against actual candidate "
            "pool scores and flags unicorn criteria."
        ),
        data_sources=("Sutra Tatva Matrix", "Miti Scoring Data"),
        parameters=(_P_JOB_REQ, _P_RANGE),
        formats=(FORMAT_PDF,),
        schedules=("on_demand", "post_hire"),
        access=_PLUS_HM,
    ),
    _r(
        id="D-03", name="Candidate Aging & Bottleneck Report", category="D",
        description=(
            "Candidates stagnant beyond threshold in each stage, showing the "
            "exact department and job where talent is sitting idle."
        ),
        data_sources=("job_candidate_links", "pipeline_status"),
        parameters=(_P_DEPT,),
        formats=(FORMAT_PDF,),
        schedules=("weekly", "daily"),
        access=_PLUS_RECRUITER,
        implemented=True,
    ),
    _r(
        id="D-04", name="Interview-to-Offer Selectivity", category="D",
        description=(
            "Ratio of candidates interviewed to offers extended per HM, "
            "identifying indecision, over-interviewing and premature offers."
        ),
        data_sources=("pipeline_status",),
        parameters=(ReportParameter("hm_user_id", "hm_user_id"), _P_RANGE, _P_JOB),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    # ── Category E: Quality & Strategic Reports ──────────────────────────────
    _r(
        id="E-01", name="Quality of Hire Correlation Report", category="E",
        description=(
            "Correlates PRISM Report grades with 90-day performance "
            "outcomes, probation clearance and HM satisfaction."
        ),
        data_sources=("Siddhi PRISM Report", "post-hire performance records (not stored yet)"),
        parameters=(_P_RANGE, _P_DEPT, _P_GRADE),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="E-02", name="Cost of Vacancy Report", category="E",
        description=(
            "Daily revenue and productivity exposure from unfilled "
            "positions, with Rs benchmarks: Green below Rs 8L, Amber "
            "Rs 8-20L, Red above Rs 20L per role per quarter."
        ),
        data_sources=("jobs", "client revenue/headcount inputs (not stored yet)"),
        parameters=(_P_DEPT,),
        formats=(FORMAT_PDF,),
        schedules=("weekly", "on_demand"),
        access=_ORG_WIDE,
    ),
    _r(
        id="E-03", name="Sourcing Channel Performance Report", category="E",
        description=(
            "Yield and quality per sourcing channel: assessment completion "
            "rate, PRISM grade distribution, join realization."
        ),
        data_sources=("job_candidate_links (source_type)", "Siddhi PRISM Report"),
        parameters=(_P_RANGE, ReportParameter("channel", "text")),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="E-04", name="Tatva Assessment Benchmark Report", category="E",
        description=(
            "How candidates' PRISM grades compare to aggregate benchmarks "
            "across similar roles: anonymised cross-client data."
        ),
        data_sources=("Siddhi aggregate data (anonymised)",),
        parameters=(ReportParameter("industry", "text"), ReportParameter("role_type", "text"), _P_GRADE),
        formats=(FORMAT_PDF,),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="E-05", name="Predictive Hiring Risk Report", category="E",
        description=(
            "Candidates in notice period with risk signals: missed "
            "check-ins, slow HM engagement, joining date alerts."
        ),
        data_sources=("pipeline_status", "post-offer engagement logs (not stored yet)"),
        parameters=(ReportParameter("notice_period_days", "min_score"),),
        formats=(FORMAT_PDF,),
        schedules=("weekly", "daily"),
        access=_PLUS_RECRUITER,
    ),
    # ── Category F: Leadership & Board Reports ───────────────────────────────
    _r(
        id="F-01", name="Monthly HR Operations Briefing", category="F",
        description=(
            "One-page executive summary: total hires, assessments, credits "
            "consumed, average PRISM grade, TTF, CoV total, top 3 bottlenecks."
        ),
        data_sources=("all telemetry", "credit_ledger"),
        parameters=(ReportParameter("month", "month"),),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="F-02", name="Leadership Talent Intelligence Brief", category="F",
        description=(
            "Concise report for BU Heads: open positions by revenue impact, "
            "CoV exposure, PRISM grade distribution, projected hiring "
            "completion dates."
        ),
        data_sources=("CoV data (not stored yet)", "pipeline_status", "Siddhi PRISM Report"),
        parameters=(ReportParameter("month", "month"), _P_DEPT),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="F-03", name="Board Quarterly Talent Report", category="F",
        description=(
            "Four-page board-level report: headcount realization, quality of "
            "hire trend, cost-of-vacancy quarterly total, forward hiring plan."
        ),
        data_sources=("all strategic metrics combined",),
        parameters=(ReportParameter("quarter", "quarter"),),
        formats=(FORMAT_PDF,),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="F-04", name="Department Hiring Health Report", category="F",
        description=(
            "Per-department: open roles, TTF, assessment quality, HM SLA, "
            "join realization, comparable across departments."
        ),
        data_sources=("jobs", "pipeline_status", "credit_ledger"),
        parameters=(ReportParameter("month", "month"), ReportParameter("quarter", "quarter"), _P_DEPT),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="F-05", name="Annual Talent Acquisition Review", category="F",
        description=(
            "Full-year review: total assessments, credit spend, quality of "
            "hire trend, CoV impact, sourcing ROI, forward strategy."
        ),
        data_sources=("all annual telemetry and financial data",),
        parameters=(ReportParameter("year", "year"),),
        formats=(FORMAT_PDF,),
        schedules=("annual",),
        access=_ORG_WIDE,
    ),
    # ── Category G: Creative, Innovative & Business-Useful Reports ───────────
    _r(
        id="G-01", name="Interviewer Evaluation Bias Report", category="G",
        description=(
            "Identifies interviewers consistently scoring too harshly or too "
            "leniently vs peer benchmarks, surfacing bias patterns before "
            "they affect decisions."
        ),
        data_sources=("Miti Scoring Data", "candidate_team_reviews"),
        parameters=(_P_DEPT, _P_RANGE),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
        notes=("Minimum 10 evaluations per interviewer before a row renders.",),
    ),
    _r(
        id="G-02", name="HM Calibration Quality Report", category="G",
        description=(
            "How well the HM's Bodha SWOT inputs aligned with what actually "
            "made candidates succeed; retroactive analysis after 90-day "
            "performance data arrives."
        ),
        data_sources=("Bodha SWOT Intake", "QoH post-hire scores (not stored yet)"),
        parameters=(_P_JOB, _P_RANGE),
        formats=(FORMAT_PDF,),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="G-03", name="Candidate Conversation Intelligence", category="G",
        description=(
            "Aggregate analysis of Vaada conversation patterns: which "
            "question types generated the richest answers, which probes "
            "produced the most differentiating responses."
        ),
        data_sources=("Vaada Conversation Analysis", "Miti Scoring Data"),
        parameters=(_P_JOB, ReportParameter("role_type", "text"), _P_RANGE),
        formats=(FORMAT_PDF,),
        schedules=("quarterly",),
        access=_ORG_WIDE,
        notes=("Provider-only in full; a summary edition goes to the client.",),
    ),
    _r(
        id="G-04", name="JD Quality vs Hire Correlation", category="G",
        description=(
            "Did well-calibrated JDs (low scope drift) produce better hires? "
            "Correlates Sutra matrix quality with the 90-day QoH score."
        ),
        data_sources=("Sutra Tatva Matrix", "QoH data (not stored yet)"),
        parameters=(_P_RANGE, _P_DEPT),
        formats=(FORMAT_PDF,),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="G-05", name="Talent Acquisition Efficiency Index", category="G",
        description=(
            "A single 0-100 composite score for the TA function combining "
            "TTF, QoH, credit efficiency, HM SLA and join realization, "
            "tracked month over month."
        ),
        data_sources=("all strategic metrics weighted",),
        parameters=(ReportParameter("month", "month"), ReportParameter("quarter", "quarter")),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
    ),
    _r(
        id="G-06", name="Notice Period Engagement Heatmap", category="G",
        description=(
            "Which candidates have low interaction during notice period, "
            "which HMs are not engaging post-offer, which joining dates are "
            "at risk."
        ),
        data_sources=("pipeline_status", "post-offer engagement log (not stored yet)"),
        parameters=(_P_DEPT,),
        formats=(FORMAT_PDF,),
        schedules=("weekly",),
        access=_PLUS_RECRUITER,
    ),
    _r(
        id="G-07", name="Assessment Investment Return", category="G",
        description=(
            "Which departments get the most value: ratio of credits spent to "
            "successful hires, average PRISM grade improvement, CoV savings "
            "from faster TTF."
        ),
        data_sources=("credit_ledger", "pipeline_status", "CoV data (not stored yet)"),
        parameters=(ReportParameter("quarter", "quarter"), _P_DEPT),
        formats=(FORMAT_PDF, FORMAT_EXCEL, FORMAT_CSV),
        schedules=("quarterly",),
        access=_ORG_WIDE,
    ),
    # ── DEI: conditional activation (Part 4 section 4) ───────────────────────
    _r(
        id="DEI-01", name="DEI Pipeline Analyzer", category="DEI",
        description=(
            "Stage-wise demographic pass-through rates across the hiring "
            "funnel. Activates ONLY when the client has a documented DEI "
            "data collection policy and has explicitly consented to "
            "demographic tracking in the ReadyPick service agreement."
        ),
        data_sources=("demographic funnel data (must not be processed without consent)",),
        parameters=(_P_RANGE, _P_DEPT),
        formats=(FORMAT_PDF,),
        schedules=("monthly",),
        access=_ORG_WIDE,
        coming_soon=True,
        notes=(
            "Regulatory and ethical requirement under Indian law: no "
            "demographic data is collected or processed until the client "
            "consent framework is built. Marked Coming Soon per Part 4 "
            "section 4.",
        ),
    ),
)

_BY_ID: dict[str, ReportDefinition] = {r.id: r for r in CATALOGUE}


def definition_for(report_id: str) -> ReportDefinition | None:
    return _BY_ID.get(report_id)


def visible_to(role: Role) -> tuple[ReportDefinition, ...]:
    """The catalogue filtered to one caller's role (Part 4 section 1.2)."""
    return tuple(r for r in CATALOGUE if role in r.access)
