"""The routes nothing called are gone. This keeps them gone.

Vivekium release, PLAN-p7 WP-B6. Deleted, all of it:

* the report library (`/reports/catalog`, `/reports/generate`,
  `services/reports/`): no screen, it overlapped the intelligence dashboards,
  and it declared schedules it never persisted;
* the Owner console's tenant list and edit, cross-tenant staff list,
  Owner-side staff invite and permission-template editor: no screen,
  duplicated by `/provider/*`, and three of them were Provider WRITES over a
  customer's own data;
* the dashboard's raw D1-D5 calibration view, which returned numbers to a
  client, its divergence queue, which had no screen (divergences are still
  RECORDED, as `calibration_records` audit data), and the controls flag that
  offered the view;
* `GET /dashboard/metrics/overview`, which had no screen and duplicated the
  intelligence dashboards (one implementation per concept); the metric
  functions it bundled stay, read by the intelligence dashboards;
* the per-link rating-comment view counter; the duplicate `/outreach/send-email`
  decorator; the BD portal's manual web-search breaker reset;
* the company approval-levels route with the multi-level approval planner and
  the two capabilities only it read, and the company email-template editor
  with the one capability only it read (migration `0129_route_scrap` deletes
  the three capabilities' `role_permissions` rows);
* `POST /candidates/links/{id}/grant-access`, which lost its only screen;
* `GET /billing/config`, which had no caller while comments across the
  repository said the browser read the Razorpay key from it.

A deleted feature is deleted everywhere, so this is the route table, the
capability constants, the migrated database, and a whitespace-normalised sweep
of the live tree (`tests/removal_sweep`).
"""
from __future__ import annotations

import importlib.util
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import capabilities as caps
from tests.removal_sweep import BACKEND, REPO, sweep

MIGRATION = BACKEND / "alembic" / "versions" / "0129_route_scrap.py"

#: (method, path suffix). Matched against every mount, v1 and v2 alike.
DELETED_ROUTES: tuple[tuple[str, str], ...] = (
    ("get", "/reports/catalog"),
    ("post", "/reports/generate"),
    ("get", "/admin/tenants"),
    ("get", "/admin/tenants/{tenant_id}"),
    ("put", "/admin/tenants/{tenant_id}"),
    ("patch", "/admin/tenants/{tenant_id}"),
    ("get", "/admin/staff"),
    ("post", "/admin/staff-invites"),
    ("get", "/admin/permissions"),
    ("put", "/admin/permissions"),
    ("get", "/dashboard/jobs/{job_id}/candidates/{link_id}/calibration"),
    ("get", "/dashboard/calibration/divergences"),
    ("get", "/dashboard/metrics/overview"),
    ("post", "/telemetry/rating-comments-view/{link_id}"),
    ("post", "/outreach/send-email"),
    ("post", "/bd/ai-reach/web-search/reset"),
    ("put", "/companies/me/approval-levels"),
    ("get", "/companies/me/email-templates"),
    ("post", "/companies/me/email-templates"),
    ("put", "/companies/me/email-templates"),
    ("post", "/candidates/links/{link_id}/grant-access"),
    ("get", "/billing/config"),
)

#: What must survive beside them: the three declared operator routes and the
#: one Owner onboarding write. Asserted so an over-eager deletion fails too.
SURVIVORS: tuple[tuple[str, str], ...] = (
    ("post", "/admin/tenants"),
    ("delete", "/admin/tenants/{tenant_id}"),
    ("post", "/admin/tenants/{tenant_id}/super-admin"),
    ("get", "/admin/audit-log"),
    ("get", "/billing/ledger"),
    ("post", "/billing/cancel"),
    ("post", "/outreach/send"),
    ("post", "/telemetry/landing-view"),
)

PATTERN = re.compile(
    "|".join(
        (
            # The report library.
            r"reports/(?:catalog|generate)",
            r"services[./]reports\b",
            r"\breport_engine\b",
            # The calibration read surfaces.
            r"\bCalibrationInternalsOut\b",
            r"\bCalibrationDimensionOut\b",
            r"\bDivergenceListOut\b",
            r"\bOverrideRateOut\b",
            r"\bcalibration_view\b",
            r"\blog_calibration_view\b",
            r"\boverride_rate\b",
            r"\bCALIBRATION_INTERNALS_VIEWED\b",
            r"calibration/divergences",
            r"\bcan_view_calibration\b",
            r"metrics/overview",
            r"\bmetrics_overview\b",
            # Telemetry, outreach alias, BD reset.
            r"rating[-_]comments[-_]view",
            r"outreach/send-email",
            r"web-search/reset",
            r"\breset_breaker\b",
            r"ai_reach_web_search_breaker_reset",
            # The Owner console routes and their schemas.
            r"\bOwnerStaffOut\b",
            r"\bStaffInviteIn\b",
            r"\bStaffInviteOut\b",
            r"\bTenantUpdateIn\b",
            r"\bPermissionsUpdateIn\b",
            r"\blist_all_staff\b",
            r"admin/staff-invites",
            r"admin/permissions",
            # The approval chain's company half and its capabilities.
            r"approval-levels",
            r"\bApprovalLevels(?:In|Out)\b",
            r"\bApprovalLevelEntry\b",
            r"\bCONFIGURE_APPROVAL_LEVELS\b",
            r"\bconfigure_approval_levels\b",
            r"\bAPPROVE_JOB\b",
            r"\bapprove_job\b",
            r"fsm\.(?:apply_submit|apply_transition|plan_submit|validate_transition)\b",
            # The email-template editor and its capability.
            r"email-templates",
            r"\bEmailTemplate(?:In|Out)\b",
            r"\bMANAGE_EMAIL_TEMPLATES\b",
            r"\bmanage_email_templates\b",
            # Grant access.
            r"grant-access",
            r"\bGrantAccessOut\b",
            # The public billing config.
            r"billing/config",
            r"\bBillingConfigOut\b",
            r"\bbilling_config\b",
            r"\bTTL_PRICING_PLANS\b",
        )
    )
)

#: Every exemption, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_dead_routes_removed.py",
    # Both assert an ABSENCE by name (the calibration schemas are gone from
    # the schema package; the divergence queue answers 404). A test of an
    # absence has to spell the absence.
    BACKEND / "tests" / "test_dashboard_numbers.py",
    BACKEND / "tests" / "test_dashboard_workflows.py",
    # Asserts the controls payload no longer offers the calibration view.
    BACKEND / "tests" / "test_dashboard_rbac_matrix.py",
)

#: Pending hand-offs: files OWNED BY ANOTHER PACKAGE running in parallel that
#: still name something deleted here. Each entry fails as stale the moment
#: its hit is gone, and must then be deleted.
PENDING_HAND_OFFS: tuple[()] = ()


def _mounted() -> set[tuple[str, str]]:
    from app.main import app

    return {
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def _registered(method: str, suffix: str, mounted: set[tuple[str, str]]) -> list[str]:
    return sorted(
        path
        for verb, path in mounted
        if verb == method
        and path.endswith(suffix)
        and path[: -len(suffix)] in {"/api/v1", "/api/v2"}
    )


def test_no_deleted_route_is_registered() -> None:
    mounted = _mounted()
    offending = [
        (method.upper(), path)
        for method, suffix in DELETED_ROUTES
        for path in _registered(method, suffix, mounted)
    ]
    assert not offending, offending


def test_the_routes_that_stay_are_still_there() -> None:
    mounted = _mounted()
    missing = [
        (method.upper(), suffix)
        for method, suffix in SURVIVORS
        if not _registered(method, suffix, mounted)
    ]
    assert not missing, missing


def test_no_live_source_names_a_deleted_route() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT + PENDING_HAND_OFFS)
    assert not hits, hits


def test_every_pending_hand_off_still_has_something_to_hand_off() -> None:
    stale = [
        str(path.relative_to(REPO))
        for path in PENDING_HAND_OFFS
        if not path.exists() or not sweep(PATTERN, roots=(path,))
    ]
    assert not stale, f"these hand-offs have landed; delete their entries: {stale}"


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the live tree."""
    hits = sweep(re.compile(r"raise_divergence"), roots=(BACKEND / "app",))
    assert any("calibration.py" in hit for hit in hits), hits


def test_the_capability_constants_are_gone() -> None:
    module = _migration()
    for name in ("APPROVE_JOB", "CONFIGURE_APPROVAL_LEVELS", "MANAGE_EMAIL_TEMPLATES"):
        assert not hasattr(caps, name), name
    for capability in module.RETIRED_CAPABILITIES:
        assert capability not in caps.ALL_CAPABILITIES
        for grants in caps.DEFAULT_PERMISSION_MATRIX.values():
            assert capability not in grants, capability
        assert capability not in caps.RBAC_INVARIANTS


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0129", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_migration_names_exactly_the_three() -> None:
    module = _migration()
    assert module.revision == "0129_route_scrap"
    assert set(module.RETIRED_CAPABILITIES) == {
        "approve_job",
        "configure_approval_levels",
        "manage_email_templates",
    }


async def test_the_database_holds_no_grant_for_a_retired_capability() -> None:
    """Asked of the MIGRATED database, not of the code: a capability whose
    rows outlive its constant is a grant nobody can see."""
    retired = list(_migration().RETIRED_CAPABILITIES)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    count = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM role_permissions "
                                "WHERE capability = ANY(:retired)"
                            ),
                            {"retired": retired},
                        )
                    ).scalar_one()
    finally:
        await engine.dispose()
    assert count == 0
