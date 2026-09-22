"""Credit lots with FIFO draw-down and three-month expiry, plus the
subscription-month usage alert (change requests 25, 26 and 27).

Revision ID: 0111_credit_lots
Revises: 0110_assessment_media_storage

THE ONE THING THIS MIGRATION MUST NOT DO
------------------------------------------
Expire a credit somebody has already paid for.

Three-month expiry is an owner ruling for NEW GRANTS ONLY. Every credit granted
before this ships was sold under a different promise, and that promise is not
soft marketing copy: `credit_packs.render_invoice_pdf` prints "Credits never
expire." on GST tax invoices that have already been issued to paying customers,
and the billing page and the PRD say the same. A migration that retroactively
expired one of those is a commercial and legal problem rather than a bug, and
it is the kind of problem nobody notices until a customer's balance drops.

So the backfill below writes every pre-existing grant as a lot with
`expires_at IS NULL`, which the whole system reads as "never expires":
`credit_lots.expire_due` filters on `expires_at IS NOT NULL`, and
`draw_fifo`'s validity test is `expires_at IS NULL OR expires_at > now()`. The
ruling is therefore enforced by the ROW rather than by a cut-off date compared
in code, which is one refactor away from being wrong in the expensive
direction.

HOW THE BACKFILL RECONSTRUCTS EACH LOT'S REMAINDER
----------------------------------------------------
The old ledger records no linkage between a debit and the grant it drew from,
so the per-lot remainder cannot be read out of history; it has to be derived.
The derivation is the standard FIFO identity and it is exact:

    remaining(lot) = clamp(cumulative_grants_through(lot) - total_consumed, 0, size)

where the cumulative sum runs over the tenant's grants oldest first. Every
sub-unit consumed is charged against the oldest grants, which is precisely what
FIFO means, so the reconstructed remainders sum to the tenant's balance
whenever that balance is non-negative. A tenant already in deficit ends with
every lot at zero and a negative ledger balance, which is the correct
representation of an overdraft: there is nothing left to allocate.

WHAT THE EXPIRY DOES TO THE LEDGER
------------------------------------
`expiry` joins `ck_credit_ledger_event_type`. An expiring lot writes a negative
ledger row for whatever was on it, in the same transaction that zeroes the lot,
so `balance = SUM(subunits_delta)` stays the single definition of the balance
and a customer reading a statement can see why it fell.

SUBSCRIPTION MONTHS
-------------------
`tenants.subscription_started_at` and `tenants.usage_alert_last_month`. The
product knew when the CURRENT billing period ends and had no idea when the
subscription began, so "which month of their subscription is this customer in"
was unanswerable. Both columns are nullable and default to nothing: a tenant
who has never been charged has no subscription month, and inventing one from
`created_at` would send a month-10 summary to an account that has never paid.

No data is destroyed by the downgrade beyond the lots themselves, and the
`expiry` ledger rows are deliberately NOT deleted by it: they record money that
actually moved, and a downgrade that erased them would make a customer's
statement stop adding up.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0111_credit_lots"
down_revision = "0110_assessment_media_storage"
branch_labels = None
depends_on = None

LOTS = "credit_lots"
DRAWS = "credit_lot_draws"

#: Restated as literals rather than imported, the convention every earlier
#: migration follows: a historical migration's effect must not shift when a
#: code constant is renamed.
_LEDGER_EVENT_TYPES = (
    "grant",
    "completed_assessment",
    "incomplete_assessment",
    "no_show",
    "old_profile_review",
    "adjustment",
    "expiry",
)

_TENANT_RLS = (
    "tenant_id = current_setting('app.tenant_id', true)::uuid "
    "OR current_setting('app.bypass_rls', true) = 'on'"
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # ── 1. The ledger learns the word "expiry" ──────────────────────────────
    op.drop_constraint(
        "ck_credit_ledger_event_type", "credit_ledger", type_="check"
    )
    op.create_check_constraint(
        "ck_credit_ledger_event_type",
        "credit_ledger",
        _in_list("event_type", _LEDGER_EVENT_TYPES),
    )

    # ── 2. Lots and draws ───────────────────────────────────────────────────
    op.create_table(
        LOTS,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ledger_entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("credit_ledger.id", ondelete="CASCADE"),
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        # NULL means never expires. See the module docstring: this is the whole
        # enforcement of the owner's "new grants only" ruling.
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("original_subunits", sa.Integer(), nullable=False),
        sa.Column("remaining_subunits", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("ledger_entry_id", name="uq_credit_lots_ledger_entry"),
        sa.CheckConstraint(
            "original_subunits > 0", name="ck_credit_lots_original_positive"
        ),
        sa.CheckConstraint(
            "remaining_subunits >= 0 AND remaining_subunits <= original_subunits",
            name="ck_credit_lots_remaining_in_range",
        ),
        sa.CheckConstraint(
            "expired_at IS NULL OR remaining_subunits = 0",
            name="ck_credit_lots_expired_is_empty",
        ),
    )
    # The FIFO order as an index, and `id` makes it TOTAL: two lots issued in
    # one transaction share an `issued_at`, and an unstable draw order would
    # let two runs over identical data disagree about which batch was spent.
    op.create_index(
        "ix_credit_lots_tenant_fifo", LOTS, ["tenant_id", "issued_at", "id"]
    )
    # The sweep's whole query. PARTIAL, so it holds only the handful of rows
    # that are actually due rather than every lot the platform has ever issued.
    op.execute(
        f"CREATE INDEX ix_credit_lots_due ON {LOTS} (expires_at) "
        "WHERE expires_at IS NOT NULL AND remaining_subunits > 0"
    )

    op.create_table(
        DRAWS,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Its own tenant_id rather than a join through the lot, for the reason
        # `support_messages` records: an RLS policy that resolves the boundary
        # through a JOIN evaluates against rows the session cannot see, so the
        # INSERT is admitted and only a later read refuses it.
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("credit_lots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ledger_entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("credit_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subunits", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("subunits > 0", name="ck_credit_lot_draws_positive"),
    )
    op.create_index("ix_credit_lot_draws_tenant_at", DRAWS, ["tenant_id", "created_at"])
    op.create_index("ix_credit_lot_draws_lot", DRAWS, ["lot_id"])
    op.create_index("ix_credit_lot_draws_entry", DRAWS, ["ledger_entry_id"])

    # ── 3. Backfill: every pre-existing grant becomes a NEVER-EXPIRING lot ──
    # Runs BEFORE RLS is enabled below. FORCE ROW LEVEL SECURITY applies to the
    # table owner too, and `app.tenant_id` is not set during a migration, so an
    # insert after the policies existed would be refused for every row.
    op.execute(
        """
        WITH grant_rows AS (
            SELECT
                id,
                tenant_id,
                subunits_delta,
                created_at,
                SUM(subunits_delta) OVER (
                    PARTITION BY tenant_id
                    ORDER BY created_at, id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative
            FROM credit_ledger
            WHERE event_type = 'grant'
        ), spent AS (
            SELECT tenant_id, -COALESCE(SUM(subunits_delta), 0) AS used
            FROM credit_ledger
            WHERE event_type <> 'grant'
            GROUP BY tenant_id
        )
        INSERT INTO credit_lots (
            id, tenant_id, ledger_entry_id, issued_at, expires_at,
            original_subunits, remaining_subunits, source, created_at
        )
        SELECT
            gen_random_uuid(),
            g.tenant_id,
            g.id,
            g.created_at,
            NULL,
            g.subunits_delta,
            GREATEST(
                0,
                LEAST(
                    g.subunits_delta,
                    g.cumulative - COALESCE(s.used, 0)
                )
            ),
            'backfill_never_expires',
            g.created_at
        FROM grant_rows g
        LEFT JOIN spent s ON s.tenant_id = g.tenant_id
        """
    )

    # ── 4. RLS, matching `credit_ledger` exactly ────────────────────────────
    for table in (LOTS, DRAWS):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_TENANT_RLS}) WITH CHECK ({_TENANT_RLS})"
        )

    # ── 5. The validity a purchase was SOLD under, stored on the purchase ───
    # `render_invoice_pdf`'s contract is that everything it prints comes from
    # the stored row, so the PDF a customer downloads in two years matches the
    # money that actually moved whatever the constants say by then. The footer
    # prints the validity, so the validity has to be on the row: without this
    # column, re-downloading an invoice issued before today would print a
    # three-month statement over credits that were sold as never expiring.
    # NULL means exactly what it means on a lot: never expires. Every existing
    # purchase keeps it.
    op.add_column(
        "credit_purchases",
        sa.Column("credit_validity_months", sa.Integer()),
    )

    # ── 6. Subscription months (change request 27) ──────────────────────────
    op.add_column(
        "tenants",
        sa.Column("subscription_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "tenants", sa.Column("usage_alert_last_month", sa.Integer())
    )
    # Deliberately NOT backfilled from `created_at`. A tenant row exists from
    # onboarding, months before any card is charged, so seeding the start date
    # from it would put existing customers straight past month 10 and post a
    # subscription summary to accounts that have never paid for one.


def downgrade() -> None:
    op.drop_column("credit_purchases", "credit_validity_months")
    op.drop_column("tenants", "usage_alert_last_month")
    op.drop_column("tenants", "subscription_started_at")

    for table in (DRAWS, LOTS):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_credit_lot_draws_entry", table_name=DRAWS)
    op.drop_index("ix_credit_lot_draws_lot", table_name=DRAWS)
    op.drop_index("ix_credit_lot_draws_tenant_at", table_name=DRAWS)
    op.drop_table(DRAWS)
    op.execute("DROP INDEX IF EXISTS ix_credit_lots_due")
    op.drop_index("ix_credit_lots_tenant_fifo", table_name=LOTS)
    op.drop_table(LOTS)

    # The `expiry` rows STAY. They record credits that really did lapse, and a
    # statement that stopped adding up would be a worse outcome than a check
    # constraint that permits a value nothing writes any more. The constraint
    # therefore keeps the value too; dropping it would make those rows
    # unwritable-but-present, which is the state that breaks a restore.
