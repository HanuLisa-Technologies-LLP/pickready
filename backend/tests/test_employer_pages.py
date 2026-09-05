"""Public employer pages (2026-09-05 add-features spec, "Employer Page &
Content").

What is pinned here, and why each pin exists:

- The slug rule is deterministic and collision-safe, because a slug is a
  published URL and two companies sharing one would serve one company's page
  under the other's name.
- The two public routes take NO authenticated user, structurally, following
  the `GET /jobs/public/{id}` pattern.
- The careers list can never show a job whose public apply link 404s: expired,
  never published, and early-closed jobs are all excluded through the same
  `services/job_posting` predicate the apply page answers with.
- A hidden or non-active company 404s exactly like an unknown slug, so the
  directory cannot be used to probe which companies exist on the platform.
- The response schemas are CLOSED allowlists: no internal notes, contacts,
  billing or subscription state, and no applicant counts.
- Both routes carry the shared rate limiter, because they do real database
  work for an anonymous caller.

Integration tests follow the test_portal.py convention: they run against the
suite's database and skip cleanly when none is reachable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.employer_pages import slugify


# ── The slug rule (pure) ─────────────────────────────────────────────────────

def test_slugify_casefolds_and_collapses_separators() -> None:
    assert slugify("Acme Corp") == "acme-corp"
    assert slugify("  Sarkar & Sons, Pvt. Ltd.  ") == "sarkar-sons-pvt-ltd"
    assert slugify("HanuLisa Technologies LLP") == "hanulisa-technologies-llp"


def test_slugify_is_empty_for_a_wholly_non_alphanumeric_name() -> None:
    """The caller substitutes the id-derived fallback; slugify itself must
    report the honest empty string rather than invent one."""
    assert slugify("!!! ***") == ""
    assert slugify("") == ""


def test_slugify_is_bounded_below_the_column_width() -> None:
    slug = slugify("x" * 500)
    assert len(slug) <= 130  # column is 140; the collision suffix must fit


# ── Structural: no auth, rate limiting, closed schemas ───────────────────────

def test_public_routes_take_no_authenticated_user() -> None:
    """Both handlers depend on get_public_db and nothing user-shaped. An auth
    dependency added later would show up here as a new parameter."""
    import inspect

    from app.api import employer_pages as mod

    for handler in (mod.search_employers, mod.get_employer_page):
        params = inspect.signature(handler).parameters
        assert "user" not in params, "public route must not take a user"
        assert "current_user" not in params


def test_both_routes_are_rate_limited() -> None:
    """The routes do real database work for an anonymous visitor, so each one
    must carry the shared abuse counter from services/rate_limit."""
    from app.api import employer_pages as mod

    routes = [r for r in mod.router.routes if hasattr(r, "dependant")]
    assert len(routes) == 2
    for route in routes:
        limited = any(
            dep.call is not None
            and getattr(dep.call, "__module__", "") == "app.services.rate_limit"
            for dep in route.dependant.dependencies
        )
        assert limited, f"{route.path} is not rate limited"


def _all_route_paths(routes: object, prefix: str = "") -> set[str]:
    """Every served path, DESCENDING into included routers.

    A flat scan of `app.routes` works only on FastAPI versions that flatten
    `include_router`; newer ones keep the included router as one nested
    object with no `.path`, and a flat scan either crashes or sees almost
    nothing (the same trap `test_deploy_secret_hygiene._collect` documents).
    """
    out: set[str] = set()
    for route in routes:  # type: ignore[attr-defined]
        context = getattr(route, "include_context", None)
        if context is not None:
            out |= _all_route_paths(
                context.included_router.routes,
                prefix + (getattr(context, "prefix", "") or ""),
            )
            continue
        path = getattr(route, "path", None)
        if path is not None:
            out.add(prefix + path)
        elif hasattr(route, "routes"):
            out |= _all_route_paths(route.routes, prefix)
    return out


def test_router_is_mounted_publicly_in_main() -> None:
    from app.main import app

    paths = _all_route_paths(app.routes)
    assert "/api/v1/employers" in paths
    assert "/api/v1/employers/{slug}" in paths


FORBIDDEN_FIELD_WORDS = (
    "note", "email", "contact", "phone", "billing", "credit", "subscription",
    "gstin", "applicant", "candidate", "score", "deficit", "razorpay",
)


def test_response_schemas_are_closed_allowlists() -> None:
    """The exact field sets an anonymous visitor can read. Extending one is a
    deliberate act that must also update this pin."""
    from app.schemas.employer_pages import (
        EmployerCardOut,
        EmployerOpenRoleOut,
        EmployerPageOut,
        EmployerSearchOut,
    )

    assert set(EmployerCardOut.model_fields) == {
        "slug", "name", "industry", "website_domain",
    }
    assert set(EmployerSearchOut.model_fields) == {
        "employers", "total", "page", "page_size",
    }
    assert set(EmployerOpenRoleOut.model_fields) == {
        "id", "title", "department", "level",
        "experience_min_years", "experience_max_years",
        "apply_path", "apply_url",
    }
    assert set(EmployerPageOut.model_fields) == {
        "slug", "name", "industry", "website_domain",
        "about_company", "work_life", "benefits", "open_roles",
    }
    for model in (
        EmployerCardOut, EmployerSearchOut, EmployerOpenRoleOut, EmployerPageOut
    ):
        for field in model.model_fields:
            for word in FORBIDDEN_FIELD_WORDS:
                assert word not in field.lower(), (
                    f"{model.__name__}.{field} looks like internal data "
                    f"crossing the public boundary"
                )


# ── Integration (skips cleanly with no database) ─────────────────────────────

async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable, skipping employer pages test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _tenant(name: str, **overrides):
    from app.models import Tenant

    tid = uuid.uuid4()
    fields = dict(
        id=tid,
        name=name,
        domain=f"{tid}.employers.test",
        is_public=True,
    )
    fields.update(overrides)
    return Tenant(**fields)


async def _delete_tenants(factory, tenant_ids) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for tid in tenant_ids:
                    await s.execute(
                        text("DELETE FROM tenants WHERE id = :t"),
                        {"t": str(tid)},
                    )


async def test_assign_slug_uniquifies_a_name_collision_and_is_idempotent() -> None:
    from app.core.db import superadmin_scope
    from app.services import employer_pages

    engine, factory = await _factory_or_skip()
    marker = uuid.uuid4().hex[:8]
    first = _tenant(f"Slug Twin {marker}")
    second = _tenant(f"Slug Twin {marker}")
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(first)
                    s.add(second)
                    await s.flush()
                    slug_a = await employer_pages.assign_slug(s, first)
                    await s.flush()
                    slug_b = await employer_pages.assign_slug(s, second)
                    await s.flush()
                    # Idempotent: a slug is a published URL and must survive
                    # a second call unchanged.
                    assert await employer_pages.assign_slug(s, first) == slug_a

        assert slug_a == f"slug-twin-{marker}"
        assert slug_b.startswith(f"slug-twin-{marker}-")
        assert slug_a != slug_b
    finally:
        await _delete_tenants(factory, [first.id, second.id])
        await engine.dispose()


async def test_search_filters_in_sql_and_hidden_companies_never_list() -> None:
    from app.api import employer_pages as api
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    marker = uuid.uuid4().hex[:8]
    visible = _tenant(f"Visible Foundry {marker}", public_slug=f"visible-{marker}")
    hidden = _tenant(
        f"Hidden Foundry {marker}", public_slug=f"hidden-{marker}", is_public=False
    )
    prospect = _tenant(
        f"Prospect Foundry {marker}",
        public_slug=f"prospect-{marker}",
        status="prospect",
    )
    tenants = [visible, hidden, prospect]
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    for t in tenants:
                        s.add(t)
                    await s.flush()

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await api.search_employers(
                        search=f"Foundry {marker}", page=1, page_size=20,
                        session=s,
                    )
        # The filter ran in SQL over the whole table: of the three matching
        # names, only the active public one is a result and `total` agrees.
        assert out.total == 1
        assert [e.slug for e in out.employers] == [f"visible-{marker}"]
    finally:
        await _delete_tenants(factory, [t.id for t in tenants])
        await engine.dispose()


async def test_hidden_prospect_and_unknown_slugs_all_404_identically() -> None:
    from fastapi import HTTPException

    from app.api import employer_pages as api
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    marker = uuid.uuid4().hex[:8]
    hidden = _tenant(
        f"Curtained {marker}", public_slug=f"curtained-{marker}", is_public=False
    )
    prospect = _tenant(
        f"Unsigned {marker}", public_slug=f"unsigned-{marker}", status="prospect"
    )
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(hidden)
                    s.add(prospect)
                    await s.flush()

        details = set()
        for slug in (f"curtained-{marker}", f"unsigned-{marker}", f"ghost-{marker}"):
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        with pytest.raises(HTTPException) as exc:
                            await api.get_employer_page(slug, session=s)
            assert exc.value.status_code == 404
            details.add(exc.value.detail)
        # Identical detail for hidden, prospect and unknown: whether a company
        # exists on the platform is not probeable through this route.
        assert len(details) == 1
    finally:
        await _delete_tenants(factory, [hidden.id, prospect.id])
        await engine.dispose()


async def test_careers_list_shows_only_currently_live_published_jobs() -> None:
    from app.api import employer_pages as api
    from app.core.db import superadmin_scope
    from app.models import Job, JobStatus

    engine, factory = await _factory_or_skip()
    marker = uuid.uuid4().hex[:8]
    tenant = _tenant(f"Liveboard {marker}", public_slug=f"liveboard-{marker}")
    now = datetime.now(timezone.utc)

    live_id = uuid.uuid4()
    expired_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    closed_id = uuid.uuid4()
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(tenant)
                    await s.flush()
                    s.add(Job(
                        id=live_id, tenant_id=tenant.id, title="Live role",
                        jd_json={}, status=JobStatus.ratified, ratified_at=now,
                        posting_start_date=now,
                    ))
                    # Published 40 days ago: outside the 30-day window, and
                    # the grace period grants nothing to an anonymous visitor.
                    s.add(Job(
                        id=expired_id, tenant_id=tenant.id, title="Expired role",
                        jd_json={}, status=JobStatus.ratified, ratified_at=now,
                        posting_start_date=now - timedelta(days=40),
                    ))
                    # Never published: no ratified_at.
                    s.add(Job(
                        id=draft_id, tenant_id=tenant.id, title="Draft role",
                        jd_json={}, status=JobStatus.draft,
                    ))
                    # Closed early (workflow Gate 8): 404s from that instant.
                    s.add(Job(
                        id=closed_id, tenant_id=tenant.id, title="Closed role",
                        jd_json={}, status=JobStatus.ratified, ratified_at=now,
                        posting_start_date=now, closed_at=now,
                    ))
                    await s.flush()

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    page = await api.get_employer_page(
                        f"liveboard-{marker}", session=s
                    )
        assert [role.id for role in page.open_roles] == [live_id]
        assert page.open_roles[0].apply_path == f"/apply/{live_id}"
        assert page.open_roles[0].apply_url.endswith(f"/apply/{live_id}")
        assert page.name == f"Liveboard {marker}"
    finally:
        await _delete_tenants(factory, [tenant.id])
        await engine.dispose()
