"""Business Development Portal: leads, the prospect tenant status, capabilities.

The BD Portal is the FOURTH portal (`/bd` in both the UI and the API), where
PickReady's business development team works the leads that become customers.

Four changes:

1. `bd_leads`: one table serving both reach channels, discriminated by
   `channel`. NOT tenant-scoped: a lead has no tenant until it converts, and
   PickReady's own sales pipeline is not any customer's data. It is a global
   table in the same family as `tenants` and `llm_provider_keys`, but it still
   gets an RLS policy: the policy requires `app.bypass_rls = 'on'`, so only the
   audited BD and Owner code paths can reach it and an org or candidate session
   cannot read the pipeline (claude.md rule 1: RLS is the boundary, the
   handler filters are defence in depth).

   Three CHECK constraints. The important one is
   `ck_bd_leads_social_source_matches_channel`: a social lead MUST carry a
   source and a personal lead MUST NOT. Enforcing it in Postgres rather than
   only in pydantic is what stops a seed script, a backfill or a psql session
   from creating a personal lead that claims it came from LinkedIn.

2. `tenants.status` gains `prospect`. When a lead's agreement is set to yes it
   is promoted to a real `tenants` row, because a customer IS a tenant
   (claude.md hard rule) and a second parallel notion of "customer" would have
   to be reconciled later. But that tenant has not been onboarded: nobody has
   been invited, no company page exists. `prospect` keeps it out of the
   Provider Portal's customer list, which defaults to `status=active` and
   accepts only active | archived | all, so a prospect can never be mistaken
   for a live customer there.

3. `bd_leads.tenant_id` / `promoted_tenant_id`: the live link and the
   permanent history. Un-setting agreement clears the link and ARCHIVES the
   tenant; it never deletes one, because by then the customer may already have
   users, jobs and applications. `promoted_tenant_id` is what lets a re-signed
   lead reuse its original company instead of minting a duplicate.

4. Three capability rows. `role_permissions` is the RBAC engine's data and the
   engine denies anything it has no row for, so the router's
   `require_bd_capability(...)` would refuse every BD request without these.
   Granted to `bd` and to `super_admin` (the platform owner runs the BD console
   too). Adding the `bd` value to the Role enum, and the constants to
   services/capabilities.py, are specified in docs/spec/handoff-bd-backend.md;
   the enum is a non-native VARCHAR in this schema, so no type migration is
   needed here.

Revision ID: 0023_business_development_portal
Revises: 0022_jd_document_and_procurement
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0023_business_development_portal"
down_revision = "0022_jd_document_and_procurement"
branch_labels = None
depends_on = None

CHANNELS = ("personal", "social")
SOCIAL_SOURCES = ("linkedin", "google", "facebook", "instagram", "x")
PROGRESS_FLAGS = (
    "interaction_1",
    "interaction_2",
    "interaction_3",
    "meeting_demo_1",
    "meeting_demo_2",
    "meeting_demo_3",
)

#: active | archived existed before; prospect is added here.
CUSTOMER_STATUSES = ("active", "archived", "prospect")

BD_CAPABILITIES = ("manage_bd_leads", "view_bd_customers", "use_ai_reach")
BD_GRANTED_ROLES = ("bd", "super_admin")


def _quoted(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # ── 1. The leads table ──────────────────────────────────────────────────
    columns = [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("contact_name", sa.String(length=255), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
        sa.Column("social_source", sa.String(length=20), nullable=True),
    ]
    # Six checkboxes, each with the timestamp of the FIRST time it was ticked.
    # The stamp survives an untick so a mis-click cannot erase the history of
    # when a company was actually contacted.
    for flag in PROGRESS_FLAGS:
        columns.append(
            sa.Column(
                flag, sa.Boolean(), nullable=False, server_default=sa.text("false")
            )
        )
        columns.append(
            sa.Column(f"{flag}_at", sa.DateTime(timezone=True), nullable=True)
        )
    columns.extend(
        [
            # Three-valued: NULL not decided, true signed, false declined.
            sa.Column("agreement", sa.Boolean(), nullable=True),
            sa.Column("agreement_at", sa.DateTime(timezone=True), nullable=True),
            # The LIVE link to the customer. Cleared when agreement stops being
            # true; SET NULL so deleting a tenant does not delete the lead.
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
            # The PERMANENT record of the tenant this lead created. No foreign
            # key on purpose, the same reasoning as audit_log.tenant_id: the
            # history must survive a tenant deletion.
            sa.Column(
                "promoted_tenant_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            # SET NULL: the pipeline outlives the rep who entered it.
            sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["owner_user_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.CheckConstraint(
                f"channel IN ({_quoted(CHANNELS)})", name="ck_bd_leads_channel"
            ),
            sa.CheckConstraint(
                f"social_source IS NULL OR social_source IN ({_quoted(SOCIAL_SOURCES)})",
                name="ck_bd_leads_social_source_value",
            ),
            # BOTH directions. This is the constraint that makes one table safe
            # for two channels.
            sa.CheckConstraint(
                "(channel = 'social' AND social_source IS NOT NULL) "
                "OR (channel = 'personal' AND social_source IS NULL)",
                name="ck_bd_leads_social_source_matches_channel",
            ),
        ]
    )
    op.create_table("bd_leads", *columns)
    op.create_index("ix_bd_leads_channel", "bd_leads", ["channel"])
    op.create_index("ix_bd_leads_agreement", "bd_leads", ["agreement"])
    op.create_index("ix_bd_leads_archived_at", "bd_leads", ["archived_at"])
    op.create_index("ix_bd_leads_owner", "bd_leads", ["owner_user_id"])

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bd_leads TO pickready_app")
    op.execute("ALTER TABLE bd_leads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bd_leads FORCE ROW LEVEL SECURITY")
    # Reachable ONLY through the bypass scope the BD and Owner consoles open.
    # There is deliberately no tenant_id predicate: a lead belongs to PickReady,
    # not to a customer, so no tenant session should ever see one.
    op.execute(
        """
        CREATE POLICY bd_leads_platform_only ON bd_leads
        USING (current_setting('app.bypass_rls', true) = 'on')
        WITH CHECK (current_setting('app.bypass_rls', true) = 'on')
        """
    )

    # ── 2. The prospect tenant status ───────────────────────────────────────
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.create_check_constraint(
        "ck_tenants_status",
        "tenants",
        f"status IN ({_quoted(CUSTOMER_STATUSES)})",
    )

    # ── 3. Capability grants ────────────────────────────────────────────────
    for role in BD_GRANTED_ROLES:
        for capability in BD_CAPABILITIES:
            op.execute(
                f"""
                INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
                VALUES (gen_random_uuid(), NULL, '{role}', '{capability}', true)
                ON CONFLICT ON CONSTRAINT uq_role_permissions DO NOTHING
                """
            )


def downgrade() -> None:
    for capability in BD_CAPABILITIES:
        op.execute(
            f"DELETE FROM role_permissions "
            f"WHERE tenant_id IS NULL AND capability = '{capability}'"
        )

    # Any tenant still sitting in `prospect` would violate the narrowed CHECK.
    # Archived is the honest landing place: it is the reversible hide, and the
    # row is by definition not a live customer.
    op.execute("UPDATE tenants SET status = 'archived' WHERE status = 'prospect'")
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.create_check_constraint(
        "ck_tenants_status", "tenants", "status IN ('active', 'archived')"
    )

    op.execute("DROP POLICY IF EXISTS bd_leads_platform_only ON bd_leads")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON bd_leads FROM pickready_app")
    op.drop_index("ix_bd_leads_owner", table_name="bd_leads")
    op.drop_index("ix_bd_leads_archived_at", table_name="bd_leads")
    op.drop_index("ix_bd_leads_agreement", table_name="bd_leads")
    op.drop_index("ix_bd_leads_channel", table_name="bd_leads")
    op.drop_table("bd_leads")
