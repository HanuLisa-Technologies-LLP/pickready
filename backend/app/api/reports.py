"""Report Library endpoints (Master Directive Part 4).

Two routes and no more, because the library IS two verbs:

  GET  /catalog   - the Part 4 catalogue filtered to the caller's role, with
                    `implemented` / `coming_soon` flags the UI greys out on.
  POST /generate  - compile one report server-side and return the finished
                    file as an attachment (Part 4 section 3.1: the client
                    receives a completed file, never a dashboard feed).

Access is enforced twice, deliberately: `require_capability(VIEW_DASHBOARD)`
gates the surface (every staff role holds it; candidates and cross-portal
tokens do not reach the tenant session at all), and the per-report role sets
from the catalogue gate each report, because Part 4 section 1.2 scopes access
per REPORT, not per endpoint, and the capability engine has no per-report
vocabulary. The role check reads catalogue data, not a role branch in
business logic: the catalogue is the permission table.

Scheduling (daily / weekly / monthly auto-dispatch) is deliberately metadata
only in this pass: the catalogue declares each report's schedule options and
no schedules table exists yet, so nothing here persists a schedule.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.user import User
from app.services import capabilities as caps
from app.services.reports import catalogue
from app.services.reports import engine as report_engine

logger = logging.getLogger(__name__)

router = APIRouter()


class ReportParameterOut(BaseModel):
    name: str
    type: str
    required: bool


class ReportOut(BaseModel):
    id: str
    name: str
    category: str
    category_name: str
    description: str
    data_sources: list[str]
    parameters: list[ReportParameterOut]
    formats: list[str]
    schedules: list[str]
    implemented: bool
    coming_soon: bool
    notes: list[str]


class CatalogOut(BaseModel):
    categories: dict[str, str]
    reports: list[ReportOut]


class GenerateParamsIn(BaseModel):
    """The typed parameter envelope of Part 4 section 1.1."""
    date_from: str | None = None
    date_to: str | None = None
    job_id: str | None = None
    department: str | None = None


class GenerateIn(BaseModel):
    report_id: str
    params: GenerateParamsIn = Field(default_factory=GenerateParamsIn)
    format: str = "pdf"


def _to_out(defn: catalogue.ReportDefinition) -> ReportOut:
    return ReportOut(
        id=defn.id,
        name=defn.name,
        category=defn.category,
        category_name=catalogue.CATEGORIES.get(defn.category, defn.category),
        description=defn.description,
        data_sources=list(defn.data_sources),
        parameters=[
            ReportParameterOut(name=p.name, type=p.type, required=p.required)
            for p in defn.parameters
        ],
        formats=list(defn.formats),
        schedules=list(defn.schedules),
        implemented=defn.implemented,
        coming_soon=defn.coming_soon,
        notes=list(defn.notes),
    )


@router.get("/catalog", response_model=CatalogOut)
async def get_catalog(
    user: CurrentUser = Depends(require_capability(caps.VIEW_DASHBOARD)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CatalogOut:
    """The Report Library, filtered to the caller's role (Part 4 section 1.2).

    Coming Soon and not-yet-implemented reports are INCLUDED, flagged: the
    directive says to show the DEI report greyed as Coming Soon rather than
    hide it, and the same presentation is right for an unshipped builder.
    """
    visible = catalogue.visible_to(user.role)
    return CatalogOut(
        categories=dict(catalogue.CATEGORIES),
        reports=[_to_out(defn) for defn in visible],
    )


@router.post("/generate")
async def generate_report(
    body: GenerateIn,
    user: CurrentUser = Depends(require_capability(caps.VIEW_DASHBOARD)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    defn = catalogue.definition_for(body.report_id)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Unknown report: {body.report_id}")
    if user.role not in defn.access:
        # Per-report RBAC (Part 4 section 1.2), from catalogue data.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role does not have access to report {defn.id}",
        )
    fmt = body.format.lower()
    if fmt not in ("pdf", "excel", "csv"):
        raise HTTPException(status_code=422, detail=f"Unsupported format: {body.format}")
    if fmt not in defn.formats and not (fmt == "csv" and "excel" in defn.formats):
        # CSV is always an acceptable stand-in for a declared excel format
        # (section 3.1: "CSV: raw data exports on request").
        raise HTTPException(
            status_code=422,
            detail=f"Report {defn.id} is not offered as {fmt}; offered: "
                   + ", ".join(defn.formats),
        )

    params: dict[str, Any] = body.params.model_dump()
    # "Generated By" on the section 3.1 header block is a person, not a UUID.
    author = await session.get(User, user.user_id)
    params["generated_by"] = (
        (author.full_name or author.email) if author else None
    ) or str(user.user_id)

    try:
        content, media_type, filename = await report_engine.generate(
            session, user.tenant_id, defn.id, params, fmt
        )
    except report_engine.ReportNotImplemented as exc:
        # Covers ReportComingSoon too (it subclasses). 501: the route exists,
        # the report is catalogued, the capability has not shipped.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info(
        "report generated: %s fmt=%s tenant=%s user=%s bytes=%d",
        defn.id, fmt, user.tenant_id, user.user_id, len(content),
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
