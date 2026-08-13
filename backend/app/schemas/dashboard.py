"""Dashboard schemas (API_CONTRACT.md `/dashboard`, FR-10.x)."""
import uuid

from pydantic import BaseModel


class JobMetricsOut(BaseModel):
    job_id: uuid.UUID
    title: str
    databank_matched: int
    fresh_sourced: int
    shortlisted: int
    offered: int
    joined: int


class DashboardSummaryOut(BaseModel):
    jobs: list[JobMetricsOut]
    total_jobs_worked: int


# ── AI Dashboard (2026-08-09) ────────────────────────────────────────────────
# What the AI actually did for this customer. Every figure below is a COUNT OF
# THINGS (jobs, candidates, assessments), which is what the existing dashboard
# already reports and is explicitly outside the no-numbers rule: that rule
# covers a score, percentage, rank or band for an assessment or a match. No
# schema here carries a score, and the grade breakdown is keyed by the four
# WORD labels of services/rating, never by the internal percentage that
# produced them.


# ── The AI Dashboard's response shapes: REMOVED ──────────────────────────────
#
# `GradeCountOut`, `AssessmentFunnelOut`, `FrameworkHealthOut` and
# `AIDashboardOut` lived here and went with the feature (spec 30). Removed
# rather than deprecated: a response model nothing returns is a contract that
# quietly reads as still supported.
