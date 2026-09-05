"""The Talent Intelligence dashboards API (2026-09-05 spec, sections 2-5).

Three reads, all behind `view_intelligence_dashboards` (seeded by migration
0082) and all tenant-scoped through the RLS-aware session:

* GET /intelligence/dashboards        the 18-dashboard registry, by tier;
* GET /intelligence/dashboards/{key}  one dashboard's widgets, computed;
* GET /intelligence/alerts            current threshold breaches.

Health is a WORD decided server-side (green / amber / red / no_data); a
metric with nothing to measure states its reason rather than a zero. Every
figure is operational (latency, ratio, compliance); no per-candidate score
travels here.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.schemas.intelligence import (
    AlertsOut,
    DashboardDetailOut,
    DashboardIndexOut,
    DashboardSummaryOut,
    DashboardTierOut,
    MetricOut,
)
from app.services import capabilities as caps
from app.services import intelligence_dashboards as dashboards
from app.services import intelligence_metrics

router = APIRouter()


def _summary(dashboard: dashboards.Dashboard) -> DashboardSummaryOut:
    measurable, total = dashboards.dashboard_availability(dashboard)
    return DashboardSummaryOut(
        key=dashboard.key,
        title=dashboard.title,
        tier=dashboard.tier,
        tier_title=dashboards.TIER_TITLES[dashboard.tier],
        audience=dashboard.audience,
        description=dashboard.description,
        metric_ids=list(dashboard.widgets),
        metrics_measurable=measurable,
        metrics_total=total,
    )


@router.get("/dashboards", response_model=DashboardIndexOut)
async def list_dashboards(
    user: CurrentUser = Depends(
        require_capability(caps.VIEW_INTELLIGENCE_DASHBOARDS)
    ),
) -> DashboardIndexOut:
    """The registry, grouped under its four tiers in taxonomy order.

    Registry-only, no database work: availability here says what the PRODUCT
    can measure, and the per-tenant answer lives on the detail read.
    """
    return DashboardIndexOut(
        tiers=[
            DashboardTierOut(
                tier=tier,
                title=dashboards.TIER_TITLES[tier],
                dashboards=[
                    _summary(dashboard)
                    for dashboard in dashboards.DASHBOARDS
                    if dashboard.tier == tier
                ],
            )
            for tier in ("A", "B", "C", "D")
        ]
    )


def _window(
    since: dt.date | None, until: dt.date | None
) -> tuple[dt.datetime | None, dt.datetime | None]:
    since_at = (
        None
        if since is None
        else dt.datetime.combine(since, dt.time.min, tzinfo=dt.timezone.utc)
    )
    until_at = (
        None
        if until is None
        else dt.datetime.combine(
            until + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc
        )
    )
    if since_at is not None and until_at is not None and until_at <= since_at:
        raise HTTPException(status_code=422, detail="until precedes since")
    return since_at, until_at


@router.get("/dashboards/{key}", response_model=DashboardDetailOut)
async def dashboard_detail(
    key: str,
    since: dt.date | None = Query(default=None),
    until: dt.date | None = Query(default=None),
    user: CurrentUser = Depends(
        require_capability(caps.VIEW_INTELLIGENCE_DASHBOARDS)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> DashboardDetailOut:
    """One dashboard, widgets computed for the caller's tenant.

    The tenant comes from the session, never from the request. The optional
    date range bounds the windowed metrics; stagnation is always a "now"
    reading. `until` is inclusive of its whole day.
    """
    dashboard = dashboards.DASHBOARDS_BY_KEY.get(key)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Unknown dashboard")
    since_at, until_at = _window(since, until)
    widgets = [
        MetricOut(
            **await intelligence_metrics.compute_metric(
                session, user.tenant_id, metric_id, since=since_at, until=until_at
            )
        )
        for metric_id in dashboard.widgets
    ]
    return DashboardDetailOut(
        key=dashboard.key,
        title=dashboard.title,
        tier=dashboard.tier,
        tier_title=dashboards.TIER_TITLES[dashboard.tier],
        audience=dashboard.audience,
        description=dashboard.description,
        widgets=widgets,
    )


@router.get("/alerts", response_model=AlertsOut)
async def alerts(
    user: CurrentUser = Depends(
        require_capability(caps.VIEW_INTELLIGENCE_DASHBOARDS)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> AlertsOut:
    """Current threshold breaches, most severe first; empty means healthy."""
    computed_at = dt.datetime.now(dt.timezone.utc)
    rows = await dashboards.compute_alerts(session, user.tenant_id, now=computed_at)
    return AlertsOut(alerts=rows, computed_at=computed_at)
