"""Drishti: the functional-head binding, and the capability that governs it.

Revision ID: 0115_drishti_functional_head
Revises: 0108_candidate_deletion_requests

TWO HALVES OF ONE CHANGE, and they ride together on purpose.

THE BINDING. `drishti_profiles` recorded `updated_by`, which answers "who
last pressed save" and not "whose profile is this". The brief answers a
different question: Drishti is once per function PER FUNCTIONAL HEAD, the
head updates it at any time, and a change of functional head is the CLIENT's
trigger, never auto-detected. Four columns make that binding a fact the
table holds rather than an assumption a handler makes:

* `functional_head_user_id` is the person the profile belongs to. ON DELETE
  SET NULL like every other user reference in this schema except
  `review_dispositions.decided_by`: a profile whose author has left the
  company is still the function's strategic profile, and deleting it would
  silently drop a layer every assessment in the function reads.
* `functional_head_name` and `functional_head_title` are DENORMALISED at
  bind time, which is why the pair survives the SET NULL above. "Authored by
  the CTO" has to stay readable after the user row goes, for the same reason
  `author_side` is denormalised on a support message: who said it must not
  be rewritten by a later change to who they are.
* `functional_head_bound_at` is when the binding was last made. A rebind
  stamps it, so a leadership change is visible in the row and not only in
  the audit trail.

THE CAPABILITY. `author_drishti_profile`, seeded here as global template
rows, because a capability constant is only half a change and the grant
engine reads ROWS. The brief names its audience and excludes one role by
name ("NOT the Hiring Manager"), so the exclusion is seeded as an explicit
allowed=false row rather than as an absent one: both deny, and only the
explicit row makes the refusal observable in the template. See
`services/capabilities.AUTHOR_DRISHTI_PROFILE` for why this is not
`edit_company_profile`.

Seed rows are string LITERALS rather than imported constants, the
convention every earlier seed migration follows (0017, 0027, 0031, 0075,
0080): a historical migration's effect must not shift if a code constant is
later renamed. Idempotent without ON CONFLICT for the reason 0031 records,
that the unique constraint is (tenant_id, role, capability) with NULLS
DISTINCT so global rows never collide.

Downgrade drops the four columns only. The permission rows are left, as in
every earlier seed migration: this cannot tell a row it created from one
`seed_dev_data` created, and deleting either breaks a database that
legitimately depends on it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0115_drishti_functional_head"
down_revision = "0111_credit_lots"
branch_labels = None
depends_on = None


#: (role, capability, allowed), restating DEFAULT_PERMISSION_MATRIX for this
#: one capability as of this revision.
SEED_ROWS: list[tuple[str, str, bool]] = [
    ("client", "author_drishti_profile", True),
    ("hr_manager", "author_drishti_profile", True),
    ("recruitment_manager", "author_drishti_profile", True),
    ("recruiter", "author_drishti_profile", False),
    ("hiring_manager", "author_drishti_profile", False),
    ("interview_manager", "author_drishti_profile", False),
]


def upgrade() -> None:
    op.add_column(
        "drishti_profiles",
        sa.Column(
            "functional_head_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "drishti_profiles",
        sa.Column("functional_head_name", sa.String(200), nullable=True),
    )
    op.add_column(
        "drishti_profiles",
        sa.Column("functional_head_title", sa.String(120), nullable=True),
    )
    op.add_column(
        "drishti_profiles",
        sa.Column(
            "functional_head_bound_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    # Existing rows are bound to whoever last saved them. That is the only
    # honest answer available: before this revision the product had no other
    # record of whose profile it was, and leaving them unbound would hand the
    # next person through the door an unclaimed profile rather than a rebind
    # they have to confirm.
    op.execute(
        "UPDATE drishti_profiles "
        "SET functional_head_user_id = updated_by, "
        "    functional_head_bound_at = updated_at "
        "WHERE updated_by IS NOT NULL"
    )

    for role, capability, allowed in SEED_ROWS:
        allowed_sql = "true" if allowed else "false"
        # 1. Reconcile an existing global row to this table's value.
        op.execute(
            f"""
            UPDATE role_permissions SET allowed = {allowed_sql}
            WHERE tenant_id IS NULL
              AND role = '{role}' AND capability = '{capability}'
              AND allowed IS DISTINCT FROM {allowed_sql}
            """
        )
        # 2. Collapse duplicates the NULLS DISTINCT constraint never blocked.
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
        # 3. Insert the pair if still absent.
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
    op.drop_column("drishti_profiles", "functional_head_bound_at")
    op.drop_column("drishti_profiles", "functional_head_title")
    op.drop_column("drishti_profiles", "functional_head_name")
    op.drop_column("drishti_profiles", "functional_head_user_id")
