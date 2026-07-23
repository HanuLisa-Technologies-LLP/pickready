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
