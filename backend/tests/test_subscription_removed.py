"""The monthly subscriptions are gone. This keeps them gone.

WHAT WAS REMOVED (owner spec 2026-09-29, sections 2.1, 23 and 34.7)
--------------------------------------------------------------------
The product shipped two commercial models side by side: monthly Razorpay
Subscriptions over a plan table, and one-time credit purchases over Razorpay
Orders. The owner ruled the product sells credits only. Deleted, all of it:

* the four subscription routes (subscribe, the subscription checkout verify,
  change plan, cancel) and their schemas;
* the webhook's subscription events and its subscription tenant resolver;
* the Razorpay Subscriptions and Plans client calls and the subscription
  checkout signature;
* the month 10 and 11 usage-summary sweep, its service, its schedule entry in
  Python and in every environment's Terraform, and the failed-charge email,
  with both email templates;
* the plan table, the tenant subscription columns and the plan foreign keys
  (migration `0132_subscription_retirement`, behind guards that refuse while
  any of it holds customer history);
* the billing page's plan card, status, renewal date, Subscribe, Change plan
  and the cancel dialog, the frontend subscription types and the Checkout
  helper that opened a subscription, and the Provider Portal's plan and
  status columns.

What SURVIVES and is the product: credit packs, the ledger, lots and expiry,
GST invoices, the webhook's Orders events and its dedupe, and one public price
list (`GET /billing/public/credit-packs`) that the `/pricing` page renders.

A deleted feature is deleted everywhere, so this is the route table, the
webhook, the models, the migrated database, the migration's own guards run for
real, and a whitespace-normalised sweep of the live tree
(`tests/removal_sweep`).
"""
from __future__ import annotations

import importlib.util
import re
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope
from tests.removal_sweep import BACKEND, REPO, sweep

MIGRATION = BACKEND / "alembic" / "versions" / "0132_subscription_retirement.py"

#: (method, path suffix) under /api/v1 and /api/v2 alike.
DELETED_ROUTES: tuple[tuple[str, str], ...] = (
    ("post", "/billing/subscribe"),
    ("post", "/billing/checkout/verify"),
    ("post", "/billing/change-plan"),
    ("post", "/billing/cancel"),
)

#: The per-credit routes, asserted so an over-eager deletion fails too.
SURVIVORS: tuple[tuple[str, str], ...] = (
    ("get", "/billing/public/credit-packs"),
    ("get", "/billing/overview"),
    ("get", "/billing/credit-packs"),
    ("post", "/billing/purchase"),
    ("post", "/billing/purchase/verify"),
    ("get", "/billing/purchases"),
    ("post", "/billing/webhook/razorpay"),
)

#: Symbols and strings only the retired model ever had.
PATTERN = re.compile(
    "|".join(
        (
            # The routes.
            r"/billing/(?:subscribe|change-plan|cancel|checkout/verify)\b",
            # The plan table and its model.
            r"\bPricingPlan\b",
            r"\bpricing_plans\b",
            r"\bmonthly_subunits\b",
            # The tenant and transaction columns.
            r"\brazorpay_subscription_id\b",
            r"\brazorpay_customer_id\b",
            r"\bcurrent_plan_id\b",
            r"\bsubscription_status\b",
            r"\bsubscription_current_end\b",
            r"\bsubscription_started_at\b",
            r"\busage_alert_last_month\b",
            r"\bSUBSCRIPTION_(?:ACTIVE|PAST_DUE|CANCELLED|HALTED|STATUSES)\b",
            # The schemas.
            r"\b(?:SubscribeIn|SubscribeOut|SubscriptionOut|PlanOut|CheckoutVerifyIn)\b",
            # The Razorpay client calls and the subscription signature.
            r"\b(?:create_subscription|update_subscription|cancel_subscription)\b",
            r"\bverify_checkout_signature\b",
            r"\b_ensure_razorpay_plan\b",
            r"\b_grant_for_payment\b",
            r"\b_tenant_for_subscription\b",
            # The webhook's subscription events.
            r"\bsubscription\.(?:charged|cancelled|halted|completed|pending|activated)\b",
            # The sweep, its service, its schedule rule and the two emails.
            r"\bsubscription_usage\b",
            r"sweep[-_]subscription[-_]usage[-_]alerts",
            r"\bsend_payment_failed_email\b",
            r"\bpayment_failed_email\b",
            # The frontend.
            r"\bCancelSubscriptionDialog\b",
            r"cancel-subscription-dialog",
            r"\b(?:SubscribeResponse|SubscriptionSummary|SubscriptionStatus|BillingConfig)\b",
            r"\bopenCheckout\b",
            r"\bCheckoutHandlerPayload\b",
            r"\bOpenCheckoutOptions\b",
            # The screen copy of the retired controls.
            r"Cancel subscription",
            r"Next billing date",
            r"at your current plan rate",
        )
    )
)

#: Every exemption, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_subscription_removed.py",
    # Lists `/billing/cancel` among the deleted routes it asserts are gone.
    BACKEND / "tests" / "test_dead_routes_removed.py",
)


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


# ── Routes ──────────────────────────────────────────────────────────────────


def test_no_subscription_route_is_registered() -> None:
    mounted = _mounted()
    offending = [
        (method.upper(), path)
        for method, suffix in DELETED_ROUTES
        for path in _registered(method, suffix, mounted)
    ]
    assert not offending, offending


def test_the_credit_routes_are_still_there() -> None:
    mounted = _mounted()
    missing = [
        (method.upper(), suffix)
        for method, suffix in SURVIVORS
        if not _registered(method, suffix, mounted)
    ]
    assert not missing, missing


@pytest.mark.parametrize(("method", "suffix"), DELETED_ROUTES)
def test_a_subscription_route_answers_404(method: str, suffix: str) -> None:
    """Section 34.7 in its own words: the routes RETURN 404. Not 401 (the
    route would exist behind a session) and not 405 (a sibling method would
    exist on the path)."""
    from app.main import app

    client = TestClient(app)
    response = client.request(method.upper(), f"/api/v1{suffix}", json={})
    assert response.status_code == 404, (suffix, response.status_code, response.text)


# ── The webhook ─────────────────────────────────────────────────────────────


def test_the_webhook_handles_no_subscription_event() -> None:
    from app.api import billing

    handled = billing._HANDLED_EVENTS
    assert handled == {"payment.captured", "order.paid", "payment.failed"}
    assert not [event for event in handled if event.startswith("subscription.")]


# ── The code ────────────────────────────────────────────────────────────────


def test_the_retired_modules_and_symbols_are_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("app.services.subscription_usage")
    assert not (BACKEND / "app" / "services" / "subscription_usage.py").exists()
    assert not (REPO / "frontend" / "components" / "billing" / "cancel-subscription-dialog.tsx").exists()

    from app import models
    from app.models import billing as billing_models
    from app.models.tenant import Tenant
    from app.schemas import billing as billing_schemas
    from app.services import email_render, razorpay
    import app.workers.tasks  # noqa: F401 -- the import IS the registration
    from app.workers import registry, schedule

    assert not hasattr(billing_models, "PricingPlan")
    assert not hasattr(models, "PricingPlan")
    for column in (
        "razorpay_customer_id",
        "razorpay_subscription_id",
        "current_plan_id",
        "subscription_status",
        "subscription_current_end",
        "subscription_started_at",
        "usage_alert_last_month",
    ):
        assert column not in Tenant.__table__.columns, column
    assert "plan_id" not in billing_models.BillingTransaction.__table__.columns
    assert "razorpay_subscription_id" not in (
        billing_models.BillingTransaction.__table__.columns
    )
    assert "plan_id" not in billing_models.CreditLedgerEntry.__table__.columns
    for name in ("create_plan", "create_subscription", "cancel_subscription"):
        assert not hasattr(razorpay, name), name
    for name in ("SubscriptionOut", "PlanOut", "SubscribeIn"):
        assert not hasattr(billing_schemas, name), name
    assert "subscription" not in billing_schemas.BillingOverviewOut.model_fields
    assert "plans" not in billing_schemas.BillingOverviewOut.model_fields
    for retired in ("payment_failed", "subscription_usage_summary"):
        assert retired not in email_render.DEFAULT_TEMPLATES, retired
    task_names = {spec.name for spec in registry.all_specs()}
    assert "pickready.sweep_subscription_usage_alerts" not in task_names
    assert "pickready.send_payment_failed_email" not in task_names
    assert all(
        entry.task != "pickready.sweep_subscription_usage_alerts"
        for entry in schedule.SCHEDULE
    )


def test_no_live_source_names_the_subscription_model() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT)
    assert not hits, hits


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the live tree:
    the surviving Orders path names its own signature helper in both halves."""
    hits = sweep(re.compile(r"\bverify_order_signature\b"), roots=(BACKEND / "app",))
    assert any("razorpay.py" in hit for hit in hits), hits
    hits = sweep(re.compile(r"\bopenOrderCheckout\b"), roots=(REPO / "frontend" / "lib",))
    assert hits, hits


# ── The migration ───────────────────────────────────────────────────────────


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0132", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_migration_guards_every_column_it_drops() -> None:
    module = _migration()
    assert module.revision == "0132_subscription_retirement"
    assert module.down_revision == "0131_freeze_at_application"
    assert set(module.TENANT_COLUMNS) == {
        "razorpay_customer_id",
        "razorpay_subscription_id",
        "current_plan_id",
        "subscription_status",
        "subscription_current_end",
        "subscription_started_at",
        "usage_alert_last_month",
    }
    assert set(module.SUBSCRIPTION_TRANSACTION_TYPES) == {
        "subscription_charge",
        "plan_change",
    }
    assert set(module.SURVIVING_TRANSACTION_TYPES) == {"credit_pack", "refund"}


async def _information_schema() -> dict[str, set[str]]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tables = set(
                        (
                            await session.execute(
                                text(
                                    "SELECT table_name FROM information_schema.tables "
                                    "WHERE table_schema = 'public'"
                                )
                            )
                        ).scalars()
                    )
                    columns = {
                        f"{table}.{column}"
                        for table, column in (
                            await session.execute(
                                text(
                                    "SELECT table_name, column_name "
                                    "FROM information_schema.columns "
                                    "WHERE table_schema = 'public' AND table_name "
                                    "IN ('tenants', 'billing_transactions', "
                                    "'credit_ledger')"
                                )
                            )
                        ).all()
                    }
    finally:
        await engine.dispose()
    return {"tables": tables, "columns": columns}


async def test_the_migrated_database_holds_none_of_it() -> None:
    """Asked of the MIGRATED database, not of the models: a column the ORM
    forgot is still a column somebody's report can read."""
    found = await _information_schema()
    assert "pricing_plans" not in found["tables"]
    assert "credit_purchases" in found["tables"]  # the sweep saw the schema
    leftovers = {
        column
        for column in found["columns"]
        if column.split(".", 1)[1]
        in {
            "razorpay_customer_id",
            "razorpay_subscription_id",
            "current_plan_id",
            "subscription_status",
            "subscription_current_end",
            "subscription_started_at",
            "usage_alert_last_month",
            "plan_id",
        }
    }
    assert leftovers == set(), leftovers
    assert "tenants.trial_used" in found["columns"]


def _run_migration(sync_conn, steps: list[str]) -> None:
    """Run the migration's own functions on this connection, for real.

    The operations are alembic's, bound to the caller's connection, and the
    caller's transaction is rolled back afterwards, so the migrated test
    database is left exactly as it was.
    """
    module = _migration()
    sync_conn.exec_driver_sql("SELECT set_config('app.bypass_rls', 'on', true)")
    module.op = Operations(MigrationContext.configure(sync_conn))
    for step in steps:
        getattr(module, step)()


async def _in_rolled_back_transaction(fn):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                return await conn.run_sync(fn)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_the_downgrade_restores_the_structure_and_the_upgrade_removes_it() -> None:
    """The round trip, executed: a downgrade nobody ran is a downgrade that
    does not work."""

    def _round_trip(sync_conn) -> tuple[int, int]:
        _run_migration(sync_conn, ["downgrade"])
        plans = sync_conn.exec_driver_sql("SELECT count(*) FROM pricing_plans").scalar_one()
        _run_migration(sync_conn, ["upgrade"])
        remaining = sync_conn.exec_driver_sql(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'pricing_plans'"
        ).scalar_one()
        return int(plans), int(remaining)

    plans, remaining = await _in_rolled_back_transaction(_round_trip)
    assert plans == 4
    assert remaining == 0


@pytest.mark.parametrize(
    ("label", "history_sql"),
    [
        (
            "a tenant with a subscription id",
            "INSERT INTO tenants (id, name, domain, spf_dkim_status, "
            " razorpay_subscription_id) VALUES "
            "('{tenant}', 'Sub {short}', '{short}.sub.test', 'pending', 'sub_live')",
        ),
        (
            "a tenant on a plan",
            "INSERT INTO tenants (id, name, domain, spf_dkim_status, current_plan_id) "
            "SELECT '{tenant}', 'Plan {short}', '{short}.plan.test', 'pending', id "
            "FROM pricing_plans WHERE slug = 'starter'",
        ),
        (
            "a subscription charge on record",
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) VALUES "
            "('{tenant}', 'Charge {short}', '{short}.charge.test', 'pending'); "
            "INSERT INTO billing_transactions (id, tenant_id, amount_inr, status, "
            " transaction_type) VALUES (gen_random_uuid(), '{tenant}', 10000, "
            " 'success', 'subscription_charge')",
        ),
        (
            "a ledger grant tied to a plan",
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) VALUES "
            "('{tenant}', 'Grant {short}', '{short}.grant.test', 'pending'); "
            "INSERT INTO credit_ledger (id, tenant_id, event_type, subunits_delta, "
            " idempotency_key, plan_id) SELECT gen_random_uuid(), '{tenant}', "
            " 'grant', 3000, 'plan-grant-{short}', id FROM pricing_plans "
            " WHERE slug = 'starter'",
        ),
    ],
)
async def test_the_upgrade_refuses_while_subscription_history_exists(
    label: str, history_sql: str
) -> None:
    """S4, executed: every guard RAISES over real rows and drops nothing.

    Downgraded first (inside the rolled-back transaction) so the retired
    columns exist to hold the history, then the upgrade is asked to run over
    it and must refuse, naming what it found.
    """
    tenant = uuid.uuid4()

    def _attempt(sync_conn) -> str:
        _run_migration(sync_conn, ["downgrade"])
        for statement in history_sql.format(
            tenant=tenant, short=tenant.hex[:8]
        ).split("; "):
            sync_conn.exec_driver_sql(statement)
        with pytest.raises(DBAPIError) as refused:
            _run_migration(sync_conn, ["upgrade"])
        return str(refused.value)

    refusal = await _in_rolled_back_transaction(_attempt)
    assert "owner decision" in refusal, (label, refusal)
