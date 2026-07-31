"""Provider Portal: customer lifecycle + compliance documents.

The Provider Portal is the PickReady owner's console over its CUSTOMERS. A
"customer" in that spec is one onboarded client company — which in this schema
is the `tenants` row, NOT `companies`.

    ASSUMPTION (spec §5.1 names `companies`): `tenants` already carries the
    customer identity the Provider Portal lists — name, industry, culture,
    details — and is what the Owner console's "Companies" table has always
    read. `companies` is the client-AUTHORED, candidate-facing page, one per
    tenant, created only after the client signs in; a customer that has never
    logged in has no `companies` row at all and would silently vanish from the
    Provider list. Compliance documents therefore hang off `tenants.id`, and
    the archive lifecycle lives on `tenants`.

Three changes:

1. `tenants.status` — active | archived, with `archived_at`. Archiving is a
   SOFT delete and the reversible counterpart to the existing hard
   `DELETE /admin/tenants/{id}`: it hides the customer from the default list
   without touching a single job, application, or report. Plus the two
   Provider-editable metadata fields (`website_domain`, `notes`).

2. `users.landline` — the primary contact's landline + extension. It lives on
   the user rather than being denormalised onto `tenants` (spec §5.1 suggests
   `primary_contact_*` columns) because the HR Head maintains their own contact
   details in the Customer Portal: a copy on `tenants` would go stale the first
   time they edit their profile, and the Provider view is read-only anyway.
   One free-text column, not two, since "+91-22-1234-5678 ext. 101" is a single
   thing a human types and reads.

3. `compliance_documents` — the 7 mandatory records (4 tax + 3 commercial),
   one row per (customer, type). Tenant-scoped with RLS in exactly the shape
   every other tenant table uses (claude.md rule 1), so the customer's own HR
   Head can read and write only their own rows while the Owner reads across
   tenants through the audited bypass scope.

Revision ID: 0020_provider_portal
Revises: 0019_pipeline_email_types
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020_provider_portal"
down_revision = "0019_pipeline_email_types"
branch_labels = None
depends_on = None

#: The 7 compliance document types (spec §3.2). VARCHAR + CHECK rather than a
#: native PG enum, matching `email_log`: adding an eighth type later is an
#: ALTER CONSTRAINT instead of a locking type migration.
DOCUMENT_TYPES = (
    # A. Mandatory Indian compliance & tax documents
    "gstin_certificate",
    "pan_card",
    "tan_number",
    "bank_account_details",
    # B. Vital commercial & legal records
    "signed_agreement",
    "purchase_order",
    "msme_certificate",
)

CUSTOMER_STATUSES = ("active", "archived")


def upgrade() -> None:
    # ── 1. Customer lifecycle + Provider-editable metadata ──────────────────
    op.add_column(
        "tenants",
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
    )
    op.add_column("tenants", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("website_domain", sa.String(length=255), nullable=True))
    op.add_column("tenants", sa.Column("notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_tenants_status",
        "tenants",
        "status IN (" + ", ".join(f"'{value}'" for value in CUSTOMER_STATUSES) + ")",
    )
    # The default list filters on status; every customer is active today, so
    # the index earns its keep only as the archived set grows — but it is free
    # to create now and awkward to add later.
    op.create_index("ix_tenants_status", "tenants", ["status"])

    # ── 2. Primary contact landline ─────────────────────────────────────────
    op.add_column("users", sa.Column("landline", sa.String(length=50), nullable=True))

    # ── 3. Compliance documents ─────────────────────────────────────────────
    op.create_table(
        "compliance_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("file_url", sa.String(length=500), nullable=False),
        # Cloudinary's content-addressed id, so a replace can delete the old
        # asset and a retry cannot orphan one.
        sa.Column("file_public_id", sa.String(length=255), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        # SET NULL, not CASCADE: the document outlives the person who uploaded
        # it. A deactivated HR Head must never take the GSTIN certificate with
        # them — the row stays and the "Uploaded by" line degrades to a date.
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "document_type IN ("
            + ", ".join(f"'{value}'" for value in DOCUMENT_TYPES)
            + ")",
            name="ck_compliance_documents_type",
        ),
        # Exactly one GSTIN certificate per customer: uploading again REPLACES
        # the current one rather than accumulating versions the Provider would
        # have to disambiguate.
        sa.UniqueConstraint(
            "tenant_id", "document_type", name="uq_compliance_documents_type"
        ),
    )
    op.create_index(
        "ix_compliance_documents_tenant", "compliance_documents", ["tenant_id"]
    )

    # RLS, identical in shape to every other tenant table (claude.md rule 1).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON compliance_documents TO pickready_app"
    )
    op.execute("ALTER TABLE compliance_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE compliance_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY compliance_documents_tenant_isolation ON compliance_documents
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS compliance_documents_tenant_isolation "
        "ON compliance_documents"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON compliance_documents "
        "FROM pickready_app"
    )
    op.drop_index("ix_compliance_documents_tenant", table_name="compliance_documents")
    op.drop_table("compliance_documents")

    op.drop_column("users", "landline")

    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.drop_column("tenants", "notes")
    op.drop_column("tenants", "website_domain")
    op.drop_column("tenants", "archived_at")
    op.drop_column("tenants", "status")
