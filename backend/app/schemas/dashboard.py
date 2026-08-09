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


class GradeCountOut(BaseModel):
    """How many assessed candidates landed on one grade."""

    grade: str
    candidates: int


class AssessmentFunnelOut(BaseModel):
    """Where this customer's invited candidates are.

    `invited` counts invitations, which is the `assessment_conversations` row
    itself: that row IS the invitation, so an invited candidate cannot be
    counted before one exists.
    """

    invited: int
    started: int
    completed: int
    reports_ready: int


class FrameworkHealthOut(BaseModel):
    """Whether each job can actually assess anyone.

    `pending_generation` is the state a stamp alone would hide: a job whose
    framework was stamped as generated but has NO competency rows is stuck at
    `questions_pending_review` forever and nobody on it can be assessed. It is
    measured against the TABLE, never against `framework_generated_at`, which
    is the whole lesson of 2026-08-06.
    """

    ready_for_candidates: int
    awaiting_approval: int
    pending_generation: int


class AIDashboardOut(BaseModel):
    """`GET /dashboard/ai-insights`, scoped to the caller's tenant by RLS."""

    jobs_with_ai_framework: int
    framework: FrameworkHealthOut
    assessments: AssessmentFunnelOut
    #: In `services.rating.GRADES` order, best first, every grade present even
    #: at zero. A breakdown that silently omits empty grades reads as though
    #: nobody landed there rather than as though nobody has been assessed.
    grades: list[GradeCountOut]
    #: Reports scored on the deterministic fallback because every LLM provider
    #: was unavailable. Surfaced rather than hidden: a customer reading a batch
    #: of reports is entitled to know which of them the model did not write.
    reports_on_fallback: int
    total_reports: int
