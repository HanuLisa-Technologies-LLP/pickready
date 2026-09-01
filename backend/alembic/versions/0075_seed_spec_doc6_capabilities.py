"""Seed the spec-doc6 / RBAC section 24 capabilities into the global template.

Revision ID: 0075_seed_spec_doc6_capabilities
Revises: 0074_candidate_projects

THE DEFECT THIS REPAIRS, AND HOW IT STAYED INVISIBLE
----------------------------------------------------
The 2026-08-29 RBAC phase added fifteen capabilities to
`services/capabilities.py` (view_company_jobs through integrity_disposition)
and the whole `interview_manager` role, and updated DEFAULT_PERMISSION_MATRIX
in code -- but never wrote the seeding migration. The grant engine reads ROWS,
not the code matrix, and denies any (role, capability) without a row, so on a
migrations-only database every dashboard control answered 403 for every role
and the Interview Manager role had no grants at all.

Nobody saw it because `seed_dev_data._seed_permission_template` reconciles the
full code matrix into the global template, and `tests/test_seed.py` runs that
seed against the live test database -- AFTER the dashboard test files in
alphabetical order. So the first suite run on a fresh database failed 40
dashboard tests, every later run passed, and the failure read as flakiness.
Measured 2026-09-01 on a fresh `alembic upgrade head` database: zero global
rows existed for any of the fifteen capabilities, and zero rows for
interview_manager.

This is the standing rule this codebase already wrote down for itself: a new
capability constant is only half a change; every added capability needs a
seeding migration too, or it silently resolves to False for everyone.

WHAT IS SEEDED
--------------
The literal (role, capability, allowed) table below, which restates the
current DEFAULT_PERMISSION_MATRIX for exactly these capabilities plus the
Interview Manager's base grant set. Hardcoded as string literals rather than
imported, so this historical migration's effect can never shift if the code
constants are later renamed (same convention as 0017/0027/0031).
`tests/test_capability_seed_parity.py` asserts the migrated database and the
code matrix agree, so the NEXT half-done capability batch fails loudly on a
fresh database instead of intermittently.

Explicit allowed=false rows are seeded too, not only grants: a false row and
an absent row resolve identically today, but the explicit row makes the
template state observable and matches what `seed_dev_data` reconciles to, so
dev and migrated databases converge on identical rows.

Idempotent WITHOUT relying on ON CONFLICT, for the reason 0031 documents: the
unique constraint is (tenant_id, role, capability) with NULLS DISTINCT, so
global rows never collide and ON CONFLICT never fires for them. Upgrade
(1) reconciles the `allowed` flag on existing global rows to this table,
(2) collapses any duplicate global rows in scope, and (3) inserts only the
pairs still absent.

Downgrade is a no-op, as for every earlier seed migration: this migration
cannot tell a row it created from one seed_dev_data created, and deleting
either breaks a database that legitimately depends on it.
"""
from alembic import op

revision = "0075_seed_spec_doc6_capabilities"
down_revision = "0074_candidate_projects"
branch_labels = None
depends_on = None

# The section-24 capability batch, per role, restating
# services/capabilities.DEFAULT_PERMISSION_MATRIX as of this revision.
_ORG_WIDE = {
    "view_company_jobs": True,
    "send_jd_to_hiring_manager": True,
    "edit_must_have_skills": True,
    "edit_nice_to_have_skills": True,
    "edit_behavioural_competencies": True,
    "edit_job_philosophy": True,
    "edit_swot": True,
    "edit_evaluation_rubrics": True,
    "finalize_role_definition": True,
    "reject_jd": True,
    "add_team_review_remark": True,
    "view_candidate_reports": True,
    "view_candidate_ratings": True,
    "assign_roles": True,
    "integrity_disposition": True,
}

_RECRUITER = {
    "view_company_jobs": True,
    "send_jd_to_hiring_manager": True,
    "edit_must_have_skills": False,
    "edit_nice_to_have_skills": False,
    "edit_behavioural_competencies": False,
    "edit_job_philosophy": False,
    "edit_swot": False,
    "edit_evaluation_rubrics": False,
    "finalize_role_definition": False,
    "reject_jd": False,
    "add_team_review_remark": True,
    "view_candidate_reports": True,
    "view_candidate_ratings": True,
    "assign_roles": False,
    "integrity_disposition": False,
}

_HIRING_MANAGER = {
    "view_company_jobs": True,
    "send_jd_to_hiring_manager": False,
    "edit_must_have_skills": True,
    "edit_nice_to_have_skills": True,
    "edit_behavioural_competencies": True,
    "edit_job_philosophy": True,
    "edit_swot": True,
    "edit_evaluation_rubrics": True,
    "finalize_role_definition": True,
    "reject_jd": False,
    "add_team_review_remark": True,
    "view_candidate_reports": True,
    "view_candidate_ratings": True,
    "assign_roles": False,
    "integrity_disposition": False,
}

# The Interview Manager's WHOLE grant set: the role predates no migration at
# all, so its base access rows (RBAC sections 13.3 and 13.4) ride with the
# section-24 batch.
_INTERVIEW_MANAGER = {
    "view_review_screen": True,
    "view_dashboard": True,
    "view_company_jobs": True,
    "send_jd_to_hiring_manager": False,
    "edit_must_have_skills": False,
    "edit_nice_to_have_skills": False,
    "edit_behavioural_competencies": False,
    "edit_job_philosophy": False,
    "edit_swot": False,
    "edit_evaluation_rubrics": False,
    "finalize_role_definition": False,
    "reject_jd": False,
    "add_team_review_remark": True,
    "view_candidate_reports": True,
    "view_candidate_ratings": True,
    "assign_roles": False,
    "integrity_disposition": False,
}

SEED_ROWS: list[tuple[str, str, bool]] = [
    (role, capability, allowed)
    for role, grants in (
        ("client", _ORG_WIDE),
        ("hr_manager", _ORG_WIDE),
        ("recruitment_manager", _ORG_WIDE),
        ("recruiter", _RECRUITER),
        ("hiring_manager", _HIRING_MANAGER),
        ("interview_manager", _INTERVIEW_MANAGER),
    )
    for capability, allowed in grants.items()
]


def upgrade() -> None:
    for role, capability, allowed in SEED_ROWS:
        allowed_sql = "true" if allowed else "false"
        # 1. Reconcile any existing global row to this table's value.
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
    # Intentional no-op; see the module docstring.
    pass
