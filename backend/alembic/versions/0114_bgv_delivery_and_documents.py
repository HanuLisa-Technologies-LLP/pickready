"""BGV delivery state, the outbound binding, contact corrections, fresher documents.

Revision ID: 0114_bgv_delivery_and_documents
Revises: 0115_drishti_functional_head

Four schema facts the BGV automation was missing, and each one closes a hole
that was invisible from inside the application.

1. A VERIFICATION HAD NO DELIVERY STATE. `status` answers "what did the
   employer say", which is a different question from "did the message reach
   them at all". A bounced request sat at `pending` looking exactly like one
   an HR team had simply not opened yet, and the day-3 chase then told the
   candidate their employer had not responded to a letter nobody ever
   received. `delivery_status` is that second question, and `delivered_at`
   is the instant the 3-day window is allowed to start from.

2. THE OUTBOUND MESSAGE WAS NOT BOUND TO ITS VERIFICATION. The bounce
   handler correlated an `email_log` row back to a verification by the HR
   ADDRESS, so two open verifications sharing one HR mailbox (the same
   person at one company confirming two candidates, which is the normal
   case at a large employer) resolved to whichever was sent last, silently
   and with no way to notice. `email_log.bgv_verification_id` is the
   binding; an address is a property of a recipient and never an identity
   of a message.

3. A BOUNCED ADDRESS COULD NOT BE CORRECTED. `candidate_employments` is
   immutable by trigger and that is deliberate, so the correction is NOT
   stored there: `bgv_contact_corrections` is an append-only,
   candidate-owned record of a new HR address for one employment row. The
   employment FACTS (employer, title, dates) stay exactly as immutable as
   they were, the trigger is untouched, and the correction travels with the
   candidate to every tenant the way the employment row itself does.

4. A FRESHER HAD NOWHERE TO PUT ANYTHING. The brief gives a fresher
   academic certificates and address proof in place of employer BGV, and
   the product had a radio button that stored no file.
   `candidate_bgv_documents` is the store, CANDIDATE-owned and tenant-free
   for the reason `candidate_employments` is: a person's certificate is a
   fact about the person, and it must reach candidate erasure through the
   candidate, not through whichever customer happened to look at it.

TENANT-FREE MEANS NO RLS POLICY, NOT A FORGOTTEN ONE. Both new tables hang
off `candidates` with no `tenant_id`, exactly like `candidate_employments`
and `bgv_inquiries`. A tenant-equality policy on a row with no tenant hides
it from everybody, so these are reached through the candidate's own session
or through the audited bypass scope, and every candidate handler filters by
the candidate id resolved from the session rather than from the request.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0114_bgv_delivery_and_documents"
down_revision = "0113_portable_evidence"
branch_labels = None
depends_on = None


#: Mirrors `models.email_log.LOGGED_EMAIL_TYPES`. The thirteen lifecycle
#: types every earlier migration carried, plus the BGV verification request,
#: which is LOGGED and never AI-drafted: it has no entry in
#: `EMAIL_TYPE_PROMPTS` because the recruiter writes it (or the deterministic
#: template does) and no prompt is involved.
_EMAIL_TYPES_AFTER: tuple[str, ...] = (
    "application_confirmation",
    "assessment_reminder",
    "shortlist",
    "rejected",
    "hold",
    "question_bank_reminder",
    "assessment_invitation",
    "assessment_complete",
    "interview_scheduled",
    "interview_completed",
    "offer_extended",
    "joined",
    "databank_invitation",
    "bgv_verification",
)

_EMAIL_TYPES_BEFORE: tuple[str, ...] = tuple(
    value for value in _EMAIL_TYPES_AFTER if value != "bgv_verification"
)

#: The four delivery facts, and each is a different thing a person would do
#: next. `not_sent` and `sent` are separated for the reason `not_started` and
#: `pending` are separated on `status`.
_DELIVERY_STATES: tuple[str, ...] = ("not_sent", "sent", "delivered", "bounced")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    # ── 1. Delivery state on the verification ────────────────────────────────
    op.add_column(
        "bgv_verifications",
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_sent",
        ),
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("bounced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("delivery_detail", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_bgv_verification_delivery_status",
        "bgv_verifications",
        _in_list("delivery_status", _DELIVERY_STATES),
    )
    # A row that has already been sent is `sent`, not `not_sent`. Backfilled
    # from `first_sent_at` because that stamp IS the evidence a request left
    # the building; nothing else about history is invented here, and in
    # particular no row is backfilled to `delivered`, because no delivery
    # event was ever recorded against one.
    op.execute(
        "UPDATE bgv_verifications SET delivery_status = 'sent' "
        "WHERE first_sent_at IS NOT NULL"
    )
    op.create_index(
        "ix_bgv_verifications_delivery",
        "bgv_verifications",
        ["delivery_status", "delivered_at"],
    )

    # ── 2. The outbound binding ──────────────────────────────────────────────
    op.add_column(
        "email_log",
        sa.Column(
            "bgv_verification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bgv_verifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_email_log_bgv_verification", "email_log", ["bgv_verification_id"]
    )
    op.drop_constraint("ck_email_log_type", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_type", "email_log", _in_list("email_type", _EMAIL_TYPES_AFTER)
    )

    # ── 3. Contact corrections, append-only ──────────────────────────────────
    op.create_table(
        "bgv_contact_corrections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_employment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_employments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_hr_email", sa.String(length=320), nullable=False),
        sa.Column("hr_email", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The CURRENT address is the newest row for an employment, so every read
    # is "order by created_at desc limit 1" and this index is what makes that
    # a single lookup rather than a scan of a candidate's whole correction
    # history.
    op.create_index(
        "ix_bgv_contact_corrections_employment",
        "bgv_contact_corrections",
        ["candidate_employment_id", "created_at"],
    )

    # ── 4. Fresher documents ─────────────────────────────────────────────────
    op.create_table(
        "candidate_bgv_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        # The object key, which NEVER crosses an API boundary. It is here so
        # candidate erasure can enumerate the bytes; every response names the
        # row id and the original filename and nothing else.
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            _in_list("document_type", ("academic_certificate", "address_proof")),
            name="ck_candidate_bgv_document_type",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_candidate_bgv_document_size"),
        # CONTENT-ADDRESSED, so re-uploading the identical scan resolves to the
        # row already stored instead of filing the same certificate twice. The
        # key is `bgv-documents/<sha256>` and is shared across candidates who
        # happen to upload byte-identical files, which is why the uniqueness is
        # per candidate and per type rather than on the key alone.
        sa.UniqueConstraint(
            "candidate_id",
            "document_type",
            "sha256",
            name="uq_candidate_bgv_document",
        ),
    )
    op.create_index(
        "ix_candidate_bgv_documents_candidate",
        "candidate_bgv_documents",
        ["candidate_id", "document_type"],
    )

    # ── Grants ───────────────────────────────────────────────────────────────
    #
    # No RLS on either table, for the reason stated in the module docstring:
    # both are tenant-free and a tenant-equality policy would hide every row
    # from every session.
    for table in ("bgv_contact_corrections", "candidate_bgv_documents"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")


def downgrade() -> None:
    op.drop_table("candidate_bgv_documents")
    op.drop_table("bgv_contact_corrections")
    op.drop_constraint("ck_email_log_type", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_type", "email_log", _in_list("email_type", _EMAIL_TYPES_BEFORE)
    )
    op.drop_index("ix_email_log_bgv_verification", table_name="email_log")
    op.drop_column("email_log", "bgv_verification_id")
    op.drop_index("ix_bgv_verifications_delivery", table_name="bgv_verifications")
    op.drop_constraint(
        "ck_bgv_verification_delivery_status", "bgv_verifications", type_="check"
    )
    op.drop_column("bgv_verifications", "delivery_detail")
    op.drop_column("bgv_verifications", "bounced_at")
    op.drop_column("bgv_verifications", "delivered_at")
    op.drop_column("bgv_verifications", "delivery_status")
