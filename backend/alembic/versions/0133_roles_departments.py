"""Roles and departments: CEO, MD, Functional Head, and the department boundary.

Revision ID: 0133_roles_departments
Revises: 0132_subscription_retirement

The leadership release (2026-09-29), spec 2.4, 2.11, 2.12, 13, 15 and 30
("Migration B"). Four halves of one change, and they ride together because
each is useless without the others: a Functional Head role with no
department is a person who can see nothing, a department with no role is a
boundary nobody is inside, and both without their grants are a role nobody
can use.

1. `company_departments`, a tenant's departments as ROWS (see
   `models/department.py` for why the free-text string was not a security
   boundary). Tenant-equality RLS, FORCE, and the grant.

2. `jobs.department_id` and `users.department_id`, each a SAME-TENANT
   composite foreign key onto company_departments (id, tenant_id), so a job
   or a person can only ever name a department of their own tenant. And
   `ck_users_department_iff_functional_head`: a Functional Head has exactly
   one department and nobody else has one (spec 13.2). The role column is a
   plain varchar, so the new role VALUES need no enum change.

3. The BACKFILL (spec 13.1): every distinct non-blank `jobs.department`
   string per tenant becomes one department row, matched on the normalised
   name (`lower`, whitespace collapsed and trimmed, the SQL twin of
   `services/departments.normalize`), named by the spelling on the OLDEST
   job that used it, and every such job is pointed at it. The legacy string
   is left exactly as it was. Runs under `app.bypass_rls`, which
   `alembic/env.py` sets: FORCE RLS binds the owner, and a backfill that
   could not see the rows would create nothing and report nothing.

4. THE GRANTS, as global template rows (the engine reads rows, a capability
   constant is only half a change):
   * the three new roles, every capability as of this revision, the
     ungranted ones as explicit allowed=false rows;
   * the five new capabilities for every existing customer role;
   * PER TENANT, the MANAGE -> VIEW mirror: every existing tenant row of
     `manage_staff`, `manage_compliance_documents` or `manage_email_senders`
     gains its VIEW twin with the SAME value, and every per-user overlay
     (`users.permissions_json`) that pins a MANAGE capability gains the VIEW
     pin with the same value. Without it a tenant that had revoked a MANAGE
     capability from a role would find the role reading that surface again
     through the new VIEW default, and a person pinned to MANAGE would be
     able to write a list they could no longer read;
   * PER TENANT, the new rows for every tenant that already carries tenant
     rows (the console-created ones, `api/admin._seed_permissions`), so an
     existing customer's template looks like a new customer's.

Seed rows are LITERALS rather than imported constants, the convention every
seed migration follows (0017, 0031, 0075, 0080, 0115): a historical
migration's effect must not shift if a code constant is later renamed.
Idempotent without ON CONFLICT for the reason 0031 records (the unique
constraint is NULLS DISTINCT, so global rows never collide).

Downgrade drops the constraint, the columns and the table. The permission
rows are left, as in every earlier seed migration: this cannot tell a row it
created from one `seed_dev_data` created. A downgrade with a Functional Head
present would leave a role no code knows; it is refused rather than letting
that happen.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0133_roles_departments"
down_revision = "0132_subscription_retirement"
branch_labels = None
depends_on = None

BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Every capability as of this revision, in `ALL_CAPABILITIES` order.
ALL_CAPABILITIES_AT_0133: tuple[str, ...] = (
    "manage_staff", "edit_job_description", "create_job", "add_compensation",
    "view_databank", "upload_resumes", "trigger_matching", "send_outreach",
    "view_review_screen", "decide_profile", "schedule_interviews",
    "update_pipeline_status", "view_dashboard", "edit_role_permissions",
    "edit_company_profile", "publish_job", "manage_compliance_documents",
    "manage_bd_leads", "view_bd_customers", "use_ai_reach", "view_bgv",
    "manage_bgv", "use_conversations", "manage_billing", "view_billing",
    "view_company_jobs", "edit_must_have_skills", "edit_nice_to_have_skills",
    "edit_behavioural_competencies", "edit_job_philosophy", "edit_swot",
    "edit_evaluation_rubrics", "finalize_role_definition", "reject_jd",
    "add_team_review_remark", "view_candidate_reports",
    "view_candidate_ratings", "assign_roles", "integrity_disposition",
    "view_intelligence_dashboards", "open_support_threads",
    "manage_email_senders", "authorize_email_senders",
    "author_drishti_profile", "retrieve_disputed_assessment", "view_staff",
    "view_compliance_documents", "view_email_senders",
    "author_leadership_intelligence", "view_leadership_intelligence",
)

_LEADERSHIP_READS = (
    "view_dashboard", "view_company_jobs", "view_review_screen",
    "view_candidate_reports", "view_candidate_ratings",
)

#: What each new role is granted; everything else is an explicit false row.
LEADER_GRANTS: dict[str, frozenset[str]] = {
    "ceo": frozenset(
        _LEADERSHIP_READS
        + (
            "view_intelligence_dashboards", "view_billing",
            "view_compliance_documents", "view_staff", "view_email_senders",
            "view_bgv", "author_leadership_intelligence",
            "view_leadership_intelligence",
        )
    ),
    "md": frozenset(
        _LEADERSHIP_READS
        + (
            "view_intelligence_dashboards", "view_billing",
            "view_compliance_documents", "view_staff", "view_email_senders",
            "view_bgv", "author_leadership_intelligence",
            "view_leadership_intelligence",
        )
    ),
    "functional_head": frozenset(
        _LEADERSHIP_READS + ("author_leadership_intelligence",)
    ),
}

#: The five new capabilities for the existing customer roles, restating
#: DEFAULT_PERMISSION_MATRIX as of this revision: each VIEW equals that
#: role's MANAGE grant, only the Super Admin views Leadership Intelligence,
#: and nobody existing authors it.
EXISTING_ROLE_ROWS: list[tuple[str, str, bool]] = [
    ("client", "view_staff", True),
    ("client", "view_compliance_documents", True),
    ("client", "view_email_senders", True),
    ("client", "author_leadership_intelligence", False),
    ("client", "view_leadership_intelligence", True),
    ("recruitment_manager", "view_staff", True),
    ("recruitment_manager", "view_compliance_documents", True),
    ("recruitment_manager", "view_email_senders", True),
    ("recruitment_manager", "author_leadership_intelligence", False),
    ("recruitment_manager", "view_leadership_intelligence", False),
    ("hr_manager", "view_staff", True),
    ("hr_manager", "view_compliance_documents", True),
    ("hr_manager", "view_email_senders", True),
    ("hr_manager", "author_leadership_intelligence", False),
    ("hr_manager", "view_leadership_intelligence", False),
    ("recruiter", "view_staff", True),
    ("recruiter", "view_compliance_documents", True),
    ("recruiter", "view_email_senders", False),
    ("recruiter", "author_leadership_intelligence", False),
    ("recruiter", "view_leadership_intelligence", False),
    ("hiring_manager", "view_staff", False),
    ("hiring_manager", "view_compliance_documents", True),
    ("hiring_manager", "view_email_senders", False),
    ("hiring_manager", "author_leadership_intelligence", False),
    ("hiring_manager", "view_leadership_intelligence", False),
    ("interview_manager", "view_staff", False),
    ("interview_manager", "view_compliance_documents", False),
    ("interview_manager", "view_email_senders", False),
    ("interview_manager", "author_leadership_intelligence", False),
    ("interview_manager", "view_leadership_intelligence", False),
]

#: Listed for no customer role, true or false: the Owner's matrix edit and
#: the platform `bd` role's three grants.
NEVER_IN_A_CUSTOMER_TEMPLATE = frozenset(
    {"edit_role_permissions", "manage_bd_leads", "view_bd_customers", "use_ai_reach"}
)

SEED_ROWS: list[tuple[str, str, bool]] = EXISTING_ROLE_ROWS + [
    (role, capability, capability in granted)
    for role, granted in LEADER_GRANTS.items()
    for capability in ALL_CAPABILITIES_AT_0133
    if capability not in NEVER_IN_A_CUSTOMER_TEMPLATE
]

#: MANAGE capability -> its VIEW twin (the per-tenant and per-user mirror).
VIEW_FOR_MANAGE: dict[str, str] = {
    "manage_staff": "view_staff",
    "manage_compliance_documents": "view_compliance_documents",
    "manage_email_senders": "view_email_senders",
}

#: The SQL twin of `services/departments.normalize`.
_NORMALIZED = "lower(btrim(regexp_replace({col}, '\\s+', ' ', 'g')))"


def _sql_bool(value: bool) -> str:
    return "true" if value else "false"


def _seed_global(role: str, capability: str, allowed: bool) -> None:
    allowed_sql = _sql_bool(allowed)
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


def upgrade() -> None:
    # ── 1. The table ─────────────────────────────────────────────────────────
    op.create_table(
        "company_departments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.UniqueConstraint(
            "tenant_id", "normalized_name", name="uq_company_departments_tenant_name"
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_company_departments_id_tenant"),
        sa.CheckConstraint(
            "btrim(normalized_name) <> ''", name="ck_company_departments_name_present"
        ),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON company_departments TO pickready_app"
    )
    op.execute("ALTER TABLE company_departments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE company_departments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY company_departments_tenant_isolation ON company_departments
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid OR {BYPASS}
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)::uuid OR {BYPASS}
        )
        """
    )

    # ── 2. The references and the constraint ─────────────────────────────────
    op.add_column("jobs", sa.Column("department_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_jobs_department_same_tenant",
        "jobs",
        "company_departments",
        ["department_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_index("ix_jobs_tenant_department", "jobs", ["tenant_id", "department_id"])

    op.add_column("users", sa.Column("department_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_users_department_same_tenant",
        "users",
        "company_departments",
        ["department_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_check_constraint(
        "ck_users_department_iff_functional_head",
        "users",
        "(role = 'functional_head') = (department_id IS NOT NULL)",
    )

    # ── 3. The backfill ──────────────────────────────────────────────────────
    normalized_job = _NORMALIZED.format(col="j.department")
    op.execute(
        f"""
        INSERT INTO company_departments (id, tenant_id, name, normalized_name)
        SELECT gen_random_uuid(), picked.tenant_id, picked.name, picked.normalized
        FROM (
            SELECT DISTINCT ON (j.tenant_id, {normalized_job})
                   j.tenant_id,
                   btrim(regexp_replace(j.department, '\\s+', ' ', 'g')) AS name,
                   {normalized_job} AS normalized
            FROM jobs j
            WHERE j.department IS NOT NULL AND {normalized_job} <> ''
            ORDER BY j.tenant_id, {normalized_job}, j.created_at, j.id
        ) AS picked
        WHERE NOT EXISTS (
            SELECT 1 FROM company_departments d
            WHERE d.tenant_id = picked.tenant_id
              AND d.normalized_name = picked.normalized
        )
        """
    )
    op.execute(
        f"""
        UPDATE jobs j SET department_id = d.id
        FROM company_departments d
        WHERE d.tenant_id = j.tenant_id
          AND j.department IS NOT NULL
          AND d.normalized_name = {normalized_job}
          AND j.department_id IS NULL
        """
    )

    # ── 4. The grants ────────────────────────────────────────────────────────
    for role, capability, allowed in SEED_ROWS:
        _seed_global(role, capability, allowed)

    # Per tenant, the MANAGE -> VIEW mirror, carrying the tenant's own value.
    for manage, view in VIEW_FOR_MANAGE.items():
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            SELECT gen_random_uuid(), m.tenant_id, m.role, '{view}', m.allowed
            FROM role_permissions m
            WHERE m.tenant_id IS NOT NULL AND m.capability = '{manage}'
              AND NOT EXISTS (
                  SELECT 1 FROM role_permissions v
                  WHERE v.tenant_id = m.tenant_id AND v.role = m.role
                    AND v.capability = '{view}'
              )
            """
        )
        # Per user, the same mirror over the sparse overlay.
        op.execute(
            f"""
            UPDATE users
            SET permissions_json = permissions_json
                || jsonb_build_object('{view}', permissions_json -> '{manage}')
            WHERE permissions_json IS NOT NULL
              AND jsonb_typeof(permissions_json) = 'object'
              AND permissions_json ? '{manage}'
              AND NOT permissions_json ? '{view}'
            """
        )

    # Per tenant, the new rows for every tenant that already carries tenant
    # rows (a console-created customer), after the mirror so a tenant's own
    # MANAGE value wins over the template's VIEW default.
    for role, capability, allowed in SEED_ROWS:
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            SELECT gen_random_uuid(), t.tenant_id, '{role}', '{capability}',
                   {_sql_bool(allowed)}
            FROM (
                SELECT DISTINCT tenant_id FROM role_permissions
                WHERE tenant_id IS NOT NULL
            ) AS t
            WHERE NOT EXISTS (
                SELECT 1 FROM role_permissions r
                WHERE r.tenant_id = t.tenant_id AND r.role = '{role}'
                  AND r.capability = '{capability}'
            )
            """
        )


def downgrade() -> None:
    held = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM users WHERE role = 'functional_head'"))
        .scalar_one()
    )
    if held:
        raise RuntimeError(
            f"{held} Functional Head account(s) exist; move them to another role "
            "before downgrading past 0133_roles_departments, or their role would "
            "outlive the department boundary that confines it."
        )
    op.drop_constraint("ck_users_department_iff_functional_head", "users", type_="check")
    op.drop_constraint("fk_users_department_same_tenant", "users", type_="foreignkey")
    op.drop_column("users", "department_id")
    op.drop_index("ix_jobs_tenant_department", table_name="jobs")
    op.drop_constraint("fk_jobs_department_same_tenant", "jobs", type_="foreignkey")
    op.drop_column("jobs", "department_id")
    op.execute("DROP POLICY IF EXISTS company_departments_tenant_isolation ON company_departments")
    op.drop_table("company_departments")
