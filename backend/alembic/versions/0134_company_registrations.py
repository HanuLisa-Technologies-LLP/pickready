"""Company self-registration: `company_registrations` and the `onboarding` tenant.

Revision ID: 0134_company_registrations
Revises: 0133_roles_departments

Owner spec 2026-09-29, sections 2.2, 5, 24 and 30 (Migration A): the first
Company Super Admin registers the company publicly, proves the mailbox with a
security code, buys the first credit pack and then sets a password. The steps
before the last run with no org session, and the first of them with no tenant.

WHAT IT ADDS
------------
1. `company_registrations`, the durable record of a registration in progress
   (`models/company_registration`). One OPEN registration per address, by a
   partial UNIQUE index over the two open states. Payment state is NOT a
   column: it is derived from `credit_purchases` (claude.md rule 8).
   RLS is enabled and FORCED with a BYPASS-ONLY policy: the row exists before
   its tenant does, so no tenant session may ever read it, and the onboarding
   routes reach it through the public session's explicit bypass.
2. `tenants.status` gains `onboarding`, beside `prospect` (0023): a company
   that verified its email and has not activated. Not a live customer, so the
   Provider Portal never lists it, and its `client` user cannot sign in.

DOWNGRADE
---------
Refuses while any tenant is `onboarding` or any registration row exists, by a
count that RAISES: a registration is somebody's paid or half-paid signup, and
this downgrade will not relabel a company or delete the record of one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0134_company_registrations"
down_revision = "0133_roles_departments"
branch_labels = None
depends_on = None

_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: 0023's set, then `onboarding`.
_TENANT_STATUSES_BEFORE = ("active", "archived", "prospect")
_TENANT_STATUSES_AFTER = _TENANT_STATUSES_BEFORE + ("onboarding",)

#: Mirrors `models.company_registration.REGISTRATION_STATUSES`, written as a
#: literal because a migration applies to the schema as it stood.
_REGISTRATION_STATUSES = (
    "email_verification_pending",
    "email_verified",
    "activated",
    "expired",
    "cancelled",
    "locked",
)
_OPEN_STATUSES = ("email_verification_pending", "email_verified")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _refuse_if_any(sql: str, message: str) -> None:
    """RAISE with `message` (which takes the count as `%`) when `sql` counts
    anything. Runs under the bypass flag so FORCE RLS cannot read a full table
    as empty."""
    op.execute(
        f"""
        DO $$
        DECLARE n bigint;
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            {sql.replace("SELECT count(*)", "SELECT count(*) INTO n", 1)};
            IF n > 0 THEN
                RAISE EXCEPTION '{message}', n;
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    # ── 1. The registration record ──────────────────────────────────────────
    op.create_table(
        "company_registrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(100), nullable=False),
        sa.Column("industry_other", sa.String(100), nullable=True),
        sa.Column(
            "status",
            sa.String(40),
            nullable=False,
            server_default="email_verification_pending",
        ),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("status", _REGISTRATION_STATUSES),
            name="ck_company_registrations_status",
        ),
        # Stored lowercased; the CHECK makes a second spelling impossible
        # rather than merely unlikely.
        sa.CheckConstraint(
            "email = lower(btrim(email))", name="ck_company_registrations_email_lower"
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_company_registrations_open_email "
        "ON company_registrations (email) "
        f"WHERE {_in_list('status', _OPEN_STATUSES)}"
    )
    op.create_index(
        "ix_company_registrations_tenant", "company_registrations", ["tenant_id"]
    )
    op.execute("ALTER TABLE company_registrations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE company_registrations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY company_registrations_bypass_only ON company_registrations "
        f"USING ({_BYPASS}) WITH CHECK ({_BYPASS})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON company_registrations TO pickready_app"
    )

    # ── 2. The onboarding tenant status ─────────────────────────────────────
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.create_check_constraint(
        "ck_tenants_status", "tenants", _in_list("status", _TENANT_STATUSES_AFTER)
    )


def downgrade() -> None:
    _refuse_if_any(
        "SELECT count(*) FROM tenants WHERE status = 'onboarding'",
        "tenants: % company registration(s) are part way through onboarding. "
        "The narrower CHECK would reject them, and this downgrade will not "
        "relabel a company.",
    )
    _refuse_if_any(
        "SELECT count(*) FROM company_registrations",
        "company_registrations: % registration(s) exist, and this downgrade "
        "will not delete the record of a signup.",
    )
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.create_check_constraint(
        "ck_tenants_status", "tenants", _in_list("status", _TENANT_STATUSES_BEFORE)
    )
    op.execute(
        "DROP POLICY IF EXISTS company_registrations_bypass_only ON company_registrations"
    )
    op.drop_index("ix_company_registrations_tenant", table_name="company_registrations")
    op.execute("DROP INDEX IF EXISTS uq_company_registrations_open_email")
    op.drop_table("company_registrations")
