"""Background verification via departmental HR email (add-features spec,
2026-09-05, Candidate Verification section).

`bgv_inquiries` is CANDIDATE-owned data: the candidate names their previous
two employers' departmental mailboxes, ReadyPick dispatches one inquiry each,
and the parsed reply lands here, on the candidate's profile side. RLS mirrors
`candidate_projects` (0074) and `candidate_updates` (0079) for the same
reason: a candidate spans tenants by design and carries `tenant_id NULL` when
they came in through the databank, so a tenant-equality policy would hide
their own data from them. The candidate portal runs on `get_candidate_db` and
filters by `candidate_id`, which is the real boundary; this policy is defence
in depth. Writes come only from the candidate portal and workers, both of
which run with bypass, so the WITH CHECK admits nothing else.

`bgv_share_consents` is the visibility boundary the spec locks: an employer
(tenant) sees an inquiry's result ONLY where the candidate has written a
consent row for that specific tenant, never directly. A tenant session may
READ its own consent rows (that is how the recruiter endpoint filters);
writes are bypass-only, because only the candidate grants or revokes.

CHAIN NOTE: the 2026-09-05 plan assigns 0080-0084 to five parallel agents;
this migration is written against the coordinator's instruction to chain onto
0084. If 0084's final revision id differs, reconcile this `down_revision`
string in the integration pass (0085+ is reserved for exactly that).

Revision ID: 0085_bgv_inquiries
Revises: 0084
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0085_bgv_inquiries"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bgv_inquiries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("employer_name", sa.String(200), nullable=False),
        sa.Column("departmental_email", sa.String(320), nullable=False),
        sa.Column("domain_match_result", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="collected"
        ),
        sa.Column("reply_token", sa.String(64), nullable=False, unique=True),
        sa.Column("inquiry_sent_at", sa.DateTime(timezone=True)),
        sa.Column("response_received_at", sa.DateTime(timezone=True)),
        sa.Column("response_raw", sa.Text()),
        sa.Column("parsed_fields_json", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        # Words only, held to the vocabulary at the database so a drifted
        # writer fails loudly (the same discipline the evidence-strength and
        # G4-disposition CHECKs use).
        sa.CheckConstraint(
            "domain_match_result IN ('matched', 'mismatched', 'indeterminate')",
            name="ck_bgv_inquiries_domain_match_vocabulary",
        ),
        sa.CheckConstraint(
            "status IN ('collected', 'dispatched', 'dispatch_failed', "
            "'response_received', 'parsed', 'parse_failed')",
            name="ck_bgv_inquiries_status_vocabulary",
        ),
    )
    op.create_index("ix_bgv_inquiries_candidate", "bgv_inquiries", ["candidate_id"])

    op.execute("ALTER TABLE bgv_inquiries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bgv_inquiries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY bgv_inquiries_owner_visibility ON bgv_inquiries
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR EXISTS (
                SELECT 1 FROM candidates c
                WHERE c.id = bgv_inquiries.candidate_id
                  AND (
                    c.tenant_id IS NULL
                    OR c.tenant_id = current_setting('app.tenant_id', true)::uuid
                  )
            )
        )
        WITH CHECK (current_setting('app.bypass_rls', true) = 'on')
        """
    )

    op.create_table(
        "bgv_share_consents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bgv_inquiry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("bgv_inquiries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "bgv_inquiry_id", "tenant_id", name="uq_bgv_share_consent"
        ),
    )
    op.create_index(
        "ix_bgv_share_consents_tenant", "bgv_share_consents", ["tenant_id"]
    )

    op.execute("ALTER TABLE bgv_share_consents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bgv_share_consents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY bgv_share_consents_visibility ON bgv_share_consents
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR tenant_id = current_setting('app.tenant_id', true)::uuid
        )
        WITH CHECK (current_setting('app.bypass_rls', true) = 'on')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_bgv_share_consents_tenant", table_name="bgv_share_consents")
    op.drop_table("bgv_share_consents")
    op.drop_index("ix_bgv_inquiries_candidate", table_name="bgv_inquiries")
    op.drop_table("bgv_inquiries")
