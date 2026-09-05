"""The candidate's in-portal Updates feed (workflow section 14).

Everything the product tells a candidate went out by email and nowhere else.
A spam filter, a full inbox, or a typo in an address a recruiter uploaded, and
the candidate silently misses an assessment invitation or an interview -- and
neither side ever finds out. This table is where the same events are also
recorded, durably, where signing in finds them.

WHY NOT `email_log`
---------------------
`email_log` is an OUTBOUND DELIVERY record: what was sent, to which address,
whether it delivered, including to internal recipients who are not candidates.
This is a per-candidate FEED of things that happened to them. Not every update
has an email and not every email has an update, and one table would force every
future event to invent an email it does not send.

RLS MIRRORS THE PARENT CANDIDATE ROW
--------------------------------------
Exactly as `candidate_projects` (0074) does, and for the same reason: a
candidate spans tenants by design and carries `tenant_id NULL` when they came
in through the databank, so a tenant-equality policy would hide their own feed
from them. The candidate portal runs on `get_candidate_db` and filters by
`candidate_id`, which is the real boundary; this policy is defence in depth.

`tenant_id` on the row is PROVENANCE -- which client's action produced the
update -- and is deliberately not the security boundary for READS.

It IS the boundary for WRITES, and that is the one place this policy differs
from `candidate_projects`. A project row is only ever written by the candidate
portal or a worker, both of which run with bypass. A feed row is written by a
RECRUITER's session too: every stage change on the client portal produces one.
So the WITH CHECK admits a tenant-scoped insert whose row is attributed to that
same tenant, and nothing else -- a client can write into their own candidates'
feeds and cannot write into anybody else's.

Revision ID: 0079_candidate_updates
Revises: 0078_sourced_pipeline_stage
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0079_candidate_updates"
down_revision = "0078_sourced_pipeline_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_updates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE")
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link_path", sa.String(500)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column(
            "emailed", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # A stored path must never become an off-site link. The feed renders it
        # as an href, so a row carrying "https://elsewhere.example" would turn
        # the candidate's own Updates page into somebody else's redirector.
        # Enforced at the database because the writer is code today and will be
        # more than one writer soon.
        sa.CheckConstraint(
            "link_path IS NULL OR link_path LIKE '/%'",
            name="ck_candidate_updates_link_is_relative",
        ),
    )
    op.create_index(
        "ix_candidate_updates_feed",
        "candidate_updates",
        ["candidate_id", "created_at"],
    )

    op.execute("ALTER TABLE candidate_updates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE candidate_updates FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY candidate_updates_owner_visibility ON candidate_updates
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR EXISTS (
                SELECT 1 FROM candidates c
                WHERE c.id = candidate_updates.candidate_id
                  AND (
                    c.tenant_id IS NULL
                    OR c.tenant_id = current_setting('app.tenant_id', true)::uuid
                  )
            )
        )
        WITH CHECK (
            current_setting('app.bypass_rls', true) = 'on'
            OR tenant_id = current_setting('app.tenant_id', true)::uuid
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_updates_feed", table_name="candidate_updates")
    op.drop_table("candidate_updates")
