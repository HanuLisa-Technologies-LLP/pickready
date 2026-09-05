"""Corporate email senders + email delivery tracking + the two capabilities.

Corporate Email System specification (2026-09-05). Three things, one revision,
because they are one feature and a database holding half of it would 403 or
500 its way through the other half:

1. `client_email_senders` -- one row per corporate mailbox a client registers
   as an authorized From identity. The spec's `client_id` is `tenant_id` here:
   a customer IS a `tenants` row in this schema (spec section 9, same
   substitution the billing work made). Status is the spec's lifecycle with
   AUTHORIZED collapsed into ACTIVE: the Super Admin's authorize action IS
   activation, recorded by `authorized_by`/`authorized_at`, and the service
   FSM (`services/email_senders/lifecycle.py`) refuses illegal moves.

2. `email_log` gains delivery tracking (spec section 8): the provider message
   id the SES event webhook matches on, delivered/bounced/complained
   timestamps, an optional `sender_id`, and a widened status CHECK
   (queued, processing, sent, delivered, failed, bounced, complaint).

3. The two capabilities, WITH their seeding rows, because a capability
   constant is only half a change (the 0075 lesson): `manage_email_senders`
   (add / verify / list) for the client Super Admin and the Recruitment
   Manager; `authorize_email_senders` (authorize / disable / enable / revoke)
   for the client Super Admin ONLY -- spec section 10 is explicit that
   unauthorized client users must not be able to activate senders.

Revision ID: 0080_client_email_senders
Revises: 0079_candidate_updates
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0080_client_email_senders"
down_revision = "0079_candidate_updates"
branch_labels = None
depends_on = None

#: Mirrors models/email_sender.SENDER_STATUSES.
_SENDER_STATUSES = (
    "pending_verification",
    "email_verified",
    "active",
    "verification_expired",
    "disabled",
    "revoked",
)

#: Mirrors models/email_log.EMAIL_STATUSES. The BEFORE tuple restates
#: migration 0016's original CHECK for the downgrade.
_EMAIL_STATUSES = (
    "queued", "processing", "sent", "delivered", "failed", "bounced", "complaint",
)
_EMAIL_STATUSES_BEFORE = ("queued", "sent", "failed")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


# ── Capability seeding (the 0075 pattern, restated literally) ────────────────
#
# Hardcoded string literals rather than imports, so this historical
# migration's effect can never shift if the code constants are renamed.
# Explicit allowed=false rows are seeded too, matching what seed_dev_data
# reconciles to, so dev and migrated databases converge on identical rows.
# `tests/test_capability_seed_parity.py` compares the migrated rows against
# `services/capabilities.DEFAULT_PERMISSION_MATRIX`.
SEED_ROWS: list[tuple[str, str, bool]] = [
    # The client Super Admin holds both halves.
    ("client", "manage_email_senders", True),
    ("client", "authorize_email_senders", True),
    # Recruitment Managers can register and verify a mailbox but not activate
    # it (spec section 3: authorization is the Super Admin's move).
    ("recruitment_manager", "manage_email_senders", True),
    ("recruitment_manager", "authorize_email_senders", False),
    # hr_manager mirrors recruitment_manager: the legacy role ranks beside it
    # until accounts are migrated deliberately (claude.md, spec v4), and
    # tests/test_rbac.py pins the two organisation-wide roles as identical.
    ("hr_manager", "manage_email_senders", True),
    ("hr_manager", "authorize_email_senders", False),
    ("recruiter", "manage_email_senders", False),
    ("recruiter", "authorize_email_senders", False),
    ("hiring_manager", "manage_email_senders", False),
    ("hiring_manager", "authorize_email_senders", False),
    ("interview_manager", "manage_email_senders", False),
    ("interview_manager", "authorize_email_senders", False),
]


def upgrade() -> None:
    # 1. The sender registry.
    op.create_table(
        "client_email_senders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="pending_verification",
        ),
        sa.Column(
            "email_verified", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "authorized_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("authorized_at", sa.DateTime(timezone=True)),
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
            _in_list("status", _SENDER_STATUSES),
            name="ck_client_email_senders_status",
        ),
        # The write path lowercases before storing; the CHECK makes a mixed-case
        # row unstorable so the unique index below cannot be dodged by casing.
        sa.CheckConstraint(
            "email = lower(email)", name="ck_client_email_senders_email_lower"
        ),
        sa.UniqueConstraint(
            "tenant_id", "email", name="uq_client_email_senders_tenant_email"
        ),
    )
    op.create_index(
        "ix_client_email_senders_tenant",
        "client_email_senders",
        ["tenant_id", "created_at"],
    )

    # RLS, the standard tenant-isolation shape with the 0034 guarded cast.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON client_email_senders TO pickready_app"
    )
    op.execute("ALTER TABLE client_email_senders ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE client_email_senders FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY client_email_senders_tenant_isolation ON client_email_senders
        USING (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )

    # 2. email_log delivery tracking.
    op.add_column("email_log", sa.Column("provider_message_id", sa.String(300)))
    op.add_column(
        "email_log", sa.Column("delivered_at", sa.DateTime(timezone=True))
    )
    op.add_column("email_log", sa.Column("bounced_at", sa.DateTime(timezone=True)))
    op.add_column(
        "email_log", sa.Column("complained_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "email_log",
        sa.Column(
            "sender_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "client_email_senders.id",
                ondelete="SET NULL",
                name="fk_email_log_sender",
            ),
        ),
    )
    # The SES event webhook looks rows up by provider message id.
    op.create_index(
        "ix_email_log_provider_message_id", "email_log", ["provider_message_id"]
    )
    op.drop_constraint("ck_email_log_status", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_status", "email_log", _in_list("status", _EMAIL_STATUSES)
    )

    # 3. Capability seeding, idempotent without ON CONFLICT (the 0075 pattern:
    # the unique constraint is NULLS DISTINCT, so global rows never collide).
    for role, capability, allowed in SEED_ROWS:
        allowed_sql = "true" if allowed else "false"
        op.execute(
            f"""
            UPDATE role_permissions SET allowed = {allowed_sql}
            WHERE tenant_id IS NULL
              AND role = '{role}' AND capability = '{capability}'
              AND allowed IS DISTINCT FROM {allowed_sql}
            """
        )
        op.execute(
            f"""
            DELETE FROM role_permissions a
            USING role_permissions b
            WHERE a.tenant_id IS NULL AND b.tenant_id IS NULL
              AND a.role = b.role AND a.capability = b.capability
              AND a.role = '{role}' AND a.capability = '{capability}'
              AND a.ctid < b.ctid
            """
        )
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            SELECT gen_random_uuid(), NULL, '{role}', '{capability}', {allowed_sql}
            WHERE NOT EXISTS (
                SELECT 1 FROM role_permissions
                WHERE tenant_id IS NULL
                  AND role = '{role}' AND capability = '{capability}'
            )
            """
        )


def downgrade() -> None:
    # A row carrying a new status would fail the narrower constraint, so fold
    # the tracking states back to the nearest 0016 value first.
    op.execute(
        "UPDATE email_log SET status = 'sent' "
        "WHERE status IN ('processing', 'delivered')"
    )
    op.execute(
        "UPDATE email_log SET status = 'failed' "
        "WHERE status IN ('bounced', 'complaint')"
    )
    op.drop_constraint("ck_email_log_status", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_status",
        "email_log",
        _in_list("status", _EMAIL_STATUSES_BEFORE),
    )
    op.drop_index("ix_email_log_provider_message_id", table_name="email_log")
    op.drop_column("email_log", "sender_id")
    op.drop_column("email_log", "complained_at")
    op.drop_column("email_log", "bounced_at")
    op.drop_column("email_log", "delivered_at")
    op.drop_column("email_log", "provider_message_id")

    op.execute(
        "DROP POLICY IF EXISTS client_email_senders_tenant_isolation "
        "ON client_email_senders"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON client_email_senders "
        "FROM pickready_app"
    )
    op.drop_index(
        "ix_client_email_senders_tenant", table_name="client_email_senders"
    )
    op.drop_table("client_email_senders")
    # Capability rows are deliberately left in place, as every earlier seed
    # migration's downgrade does: this migration cannot tell a row it created
    # from one seed_dev_data created.
