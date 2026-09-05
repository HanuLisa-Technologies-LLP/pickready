"""Response schemas for the Talent Intelligence dashboards (2026-09-05 spec).

Every figure here is an OPERATIONAL metric: a latency, a ratio, a count, a
compliance percentage. No per-candidate assessment score, numeric grade or
match percentage may ever travel through these models; candidate quality
reaches a client only as the four grade words, elsewhere.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

HealthWord = Literal["green", "amber", "red", "no_data"]


class MetricOut(BaseModel):
    """One computed widget: value, health WORD, and the definition's copy."""

    metric_id: str
    title: str
    formula: str
    unit: str
    thresholds: str
    proxy_note: str | None = None
    value: float | None = None
    status: HealthWord
    #: Set exactly when status is no_data: the plain-language reason there is
    #: nothing to show, rendered instead of a fabricated zero.
    status_reason: str | None = None
    #: The formula's own input counts (profiles reviewed, offers extended...).
    inputs: dict[str, Any] = {}
    #: Sub-readings for deconstructed metrics, when present.
    segments: dict[str, float | None] | None = None


class DashboardSummaryOut(BaseModel):
    key: str
    title: str
    tier: Literal["A", "B", "C", "D"]
    tier_title: str
    audience: str
    description: str
    metric_ids: list[str]
    #: How many of this dashboard's widgets the product can measure today
    #: (registry-level; a measurable widget can still be no_data for a tenant
    #: with no rows yet).
    metrics_measurable: int
    metrics_total: int


class DashboardTierOut(BaseModel):
    tier: Literal["A", "B", "C", "D"]
    title: str
    dashboards: list[DashboardSummaryOut]


class DashboardIndexOut(BaseModel):
    tiers: list[DashboardTierOut]


class DashboardDetailOut(BaseModel):
    key: str
    title: str
    tier: Literal["A", "B", "C", "D"]
    tier_title: str
    audience: str
    description: str
    widgets: list[MetricOut]


class AlertOut(BaseModel):
    id: str
    severity: Literal["red", "amber"]
    title: str
    detail: str
    #: Relative path inside the portal, same rule as the candidate feed.
    link_path: str | None = None


class AlertsOut(BaseModel):
    alerts: list[AlertOut]
    computed_at: datetime
