"""Portable Intelligence: the candidate-scoped evidence store (change request 23).

Revision ID: 0113_portable_evidence
Revises: 0115_drishti_functional_head

THE OWNER RULING, 2026-09-22. The full Portable plus Job Specific split, which
knowingly reverses the 2026-07-30 retirement of cross-job reuse. The reversal
itself is recorded in place in `services/retake.py`; this migration is the
store it needs, and every decision below is about making the reversal SAFE
rather than about making it possible.

WHY A NEW TABLE AND NOT A NULLABLE `evidence_items.job_id`
------------------------------------------------------------
The change request asked for the choice to be made deliberately, so here it is,
with the three reasons that each stand alone:

1.  `evidence_items.tenant_id` is NOT NULL and its RLS policy is tenant
    equality. Portable evidence follows the CANDIDATE across tenants the way
    `candidate_employments` already does, so a portable row in that table would
    have to carry some tenant's id and would then be invisible to the next
    employer that needs it. Dropping the tenant column instead would rewrite
    the policy every existing reader of that table depends on.

2.  `evidence_items.job_id` is NOT NULL ON DELETE CASCADE, and job closure is
    being given a purge of job-scoped assessment data. Both of those are
    correct for a job-scoped ledger and fatal for a person's standing record.
    A nullable `job_id` would leave the portable rows one mistyped WHERE away
    from a sweep written for the job-scoped ones, and the symptom would be a
    candidate quietly losing evidence they had already given.

3.  The two tables answer different questions. The ledger answers "what did we
    read when we graded this application". This answers "what do we know about
    this person". One table would make every future reader work out which it
    was holding from the nullability of a column.

So `source_job_id` and `source_tenant_id` here are PROVENANCE: nullable, and
ON DELETE SET NULL rather than CASCADE. A closed job or a departed customer
costs a row its origin and never the row. SET NULL rather than a bare UUID with
no foreign key (the `profiles.source_tenant_id` precedent) because SET NULL
keeps referential integrity AND guarantees the row survives, where a bare UUID
keeps the row but leaves a dangling reference behind: the 2026-09-09 retrieval
sweep burned three attempts an hour forever on exactly that.

WHAT THE TABLE DOES NOT HAVE
------------------------------
No score. No grade, band, percent, rating, level, tier, composite or verdict.
That is the enforcement of the constraint this whole feature rests on:
portability is of the EVIDENCE, judged afresh by the new job's matrix, never of
a verdict another matrix reached. A column is the only durable place a verdict
could travel, so there is no column, and `tests/test_portable_evidence.py`
reads `information_schema.columns` against this table and fails if one appears.

No tenant column either, for the reason `candidate_employments` has none: a
person's education did not happen inside a tenant.

THE RLS POLICY IS BYPASS ONLY, AND THAT IS STRICTER THAN THE PRECEDENT
------------------------------------------------------------------------
`bgv_inquiries` admits a tenant session in its USING clause and leans on an
application-level consent check for the real boundary. This table does not.
Its two readers both already run in an audited bypass scope: the candidate's
own portal session (`get_candidate_db`) and the assessment worker. No recruiter
route reads it, because what a recruiter sees is the PRISM report, which is
tenant scoped already.

So the tenant-equality arm would be a door nobody walks through, and a door
nobody walks through is a door nobody notices being used. Asked to choose the
more restrictive option where unsure, this chooses it: a tenant session sees
zero rows, and `tests/test_portable_evidence.py` proves it by selecting under
`tenant_scope` and asserting the count is zero.

THE UNIQUE CONSTRAINT AND THE SOFT DELETE, TOGETHER, ON PURPOSE
-----------------------------------------------------------------
`uq_portable_evidence_subject` has no predicate and `status` is a soft
retirement. That pairing is exactly what turned the matrix editor's paste box
into a 500 in pilot on 2026-09-20, and it is safe here for the one reason it
was not safe there: the only writer is `portable_evidence.record`, which is an
UPSERT that REVIVES on conflict. There is no INSERT path that can land on an
occupied key, and a retired fact coming back is the same fact about the same
person.

Downgrade drops the table. Nothing else in the schema references it, so the
drop is total and a re-upgrade re-harvests from the rows the facts were derived
from, which are all still there.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0113_portable_evidence"
down_revision = "0112_job_closure_soft_deletion"
branch_labels = None
depends_on = None


#: String LITERALS rather than imported constants, the convention every earlier
#: migration follows: a historical migration's effect must not shift when a code
#: constant is later renamed. `tests/test_portable_evidence.py` compares these
#: against `services/portable_evidence` in both directions.
_KINDS = (
    "career_history",
    "education",
    "employment_confirmed",
    "profile_fact",
    "core_skill",
)
_SOURCE_KINDS = ("resume", "validation", "bgv", "profile")
_TRUST = ("authoritative", "validated", "observed", "inferred")
_STATUSES = ("active", "superseded", "revoked")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "portable_evidence_items",
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
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "facts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("trust", sa.String(20), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        # PROVENANCE, never access control, and never a cascade. See the
        # module docstring: a closed job costs the row its origin, never the
        # row.
        sa.Column(
            "source_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # A DATE, because it comes from a document that states a month at best
        # and a timestamptz would invent a time of day and a zone every later
        # comparison then depends on. Nullable, and an absent value is what
        # `_is_fresh` refuses on: a fact with no date cannot stand in for
        # asking.
        sa.Column("observed_on", sa.Date(), nullable=True),
        # An event in OUR system rather than in a document, so an instant.
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(_in_list("kind", _KINDS), name="ck_portable_evidence_kind"),
        sa.CheckConstraint(
            _in_list("source_kind", _SOURCE_KINDS),
            name="ck_portable_evidence_source_kind",
        ),
        sa.CheckConstraint(
            _in_list("trust", _TRUST), name="ck_portable_evidence_trust"
        ),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name="ck_portable_evidence_status"
        ),
        sa.UniqueConstraint(
            "candidate_id", "kind", "subject", name="uq_portable_evidence_subject"
        ),
    )
    op.create_index(
        "ix_portable_evidence_candidate",
        "portable_evidence_items",
        ["candidate_id", "kind"],
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON portable_evidence_items "
        "TO pickready_app"
    )
    op.execute("ALTER TABLE portable_evidence_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portable_evidence_items FORCE ROW LEVEL SECURITY")
    # BYPASS ONLY, in BOTH directions. A tenant session sees nothing and can
    # write nothing. See the module docstring for why that is stricter than
    # `bgv_inquiries` on purpose and why nothing legitimate is refused by it.
    op.execute(
        """
        CREATE POLICY portable_evidence_bypass_only ON portable_evidence_items
        USING (current_setting('app.bypass_rls', true) = 'on')
        WITH CHECK (current_setting('app.bypass_rls', true) = 'on')
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS portable_evidence_bypass_only "
        "ON portable_evidence_items"
    )
    op.drop_index(
        "ix_portable_evidence_candidate", table_name="portable_evidence_items"
    )
    op.drop_table("portable_evidence_items")
