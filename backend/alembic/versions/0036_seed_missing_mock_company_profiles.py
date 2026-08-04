"""Fill the About Company / Work Life / Benefits gap on the seeded mock tenants.

WHAT WAS WRONG
--------------
Measured against production 2026-08-04 via GET /api/v1/companies/me/profile,
once per mock tenant:

    Sarkar Corp     about_company / work_life / benefits  ALL present
    ACRM Corp       about_company / work_life / benefits  ALL empty
    Specter & Co.   about_company / work_life / benefits  ALL empty

Two of the three demo companies render an empty Company Profile page. It is not
only that page: a job SNAPSHOTS the company profile at creation and a NULL
section on a job reads through to the live company profile, so every job those
two tenants post also shows nothing under those headings on the PUBLIC
application page, where a candidate is deciding whether to apply.

WHERE THE DATA ACTUALLY LIVES
-----------------------------
On `companies`, not on `tenants`, and the Profile page's "benefits" is
`companies.benefits_text` -- NOT the older `companies.benefits`, which belongs
to the legacy company-page form and which 0016 seeded this column FROM. Writing
the wrong one would leave the page exactly as blank as it started while
appearing to have fixed it.

`companies` is the client-AUTHORED page and does not exist until the client
first signs in, so for these two tenants the row may be absent entirely rather
than present-and-blank. Hence an upsert: a plain UPDATE would match nothing and
report success.

SCOPED TO TWO LITERAL UUIDS, AND ONLY WHERE EMPTY
--------------------------------------------------
The ON CONFLICT branch keeps any existing non-blank value and fills only what is
NULL or whitespace. Three consequences, all deliberate:

* A real customer can never be touched, whatever their id, and no future signup
  can inherit demo copy. The dev seed assigns these UUIDs deterministically.
* Sarkar Corp is not listed at all. It already has copy, and overwriting it
  would discard content someone may have edited.
* Re-running is a no-op, because after the first run nothing is blank.

RLS
---
`companies` carries FORCE ROW LEVEL SECURITY, so the migration connection is
subject to the tenant policy even as the table owner. The bypass flag alone is
not enough: `current_setting` is STABLE and the planner constant-folds the
policy's ::uuid cast, so an unset-then-poisoned GUC raises during planning
regardless of the flag. The sentinel is pinned alongside it for the same reason
`core/db.superadmin_scope` and `workers/tasks.refresh_dashboard_views` pin it.

NO EM DASHES IN THIS COPY
-------------------------
It renders straight onto the public application page, and claude.md's sweep
covers seeded CONTENT exactly as it covers code (see 0025_strip_em_dashes).

DOWNGRADE
---------
Clears only the exact text this migration wrote, so an edit made afterwards
survives. The `companies` rows themselves are left in place: deleting a row this
migration may not have created is not a safe inverse.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0036_seed_mock_company_profiles"
down_revision = "0035_unreview_empty_setups"
branch_labels = None
depends_on = None


ACRM = "10000000-0000-4000-8000-000000000002"
SPECTER = "10000000-0000-4000-8000-000000000003"

PROFILES: dict[str, dict[str, str]] = {
    ACRM: {
        "about_company": (
            "ACRM Corp builds customer relationship software for mid-market "
            "lenders across India. Our teams work close to the people who use "
            "what we ship, and a release is judged by whether it made a "
            "collections officer's day shorter, not by how much of it we built."
        ),
        "work_life": (
            "We work in small teams with a clear owner for every piece of work. "
            "Hybrid by default, with two anchor days in the office each week so "
            "that design and review happen face to face. Meetings are kept few "
            "and short, and nobody is expected to answer messages after hours."
        ),
        "benefits_text": (
            "Health cover for you, your partner and your children, and for your "
            "parents if you need it. An annual learning budget you choose how to "
            "spend. Paid parental leave for every parent. Flexible hours around "
            "school runs and commitments outside work."
        ),
    },
    SPECTER: {
        "about_company": (
            "Specter and Co. is a corporate advisory firm working with founders "
            "through fundraising, restructuring and exits. The work is detailed "
            "and the stakes are real, so we hire people who would rather ask an "
            "awkward question early than present a confident answer late."
        ),
        "work_life": (
            "Client work runs in cycles, and we staff for the peaks rather than "
            "asking people to absorb them. Deal weeks are demanding and are "
            "followed by genuine recovery time. Junior colleagues sit with "
            "partners on live matters from their first month."
        ),
        "benefits_text": (
            "Comprehensive health and life cover. Professional membership and "
            "examination fees paid in full. Structured mentoring with a partner "
            "from the first week. Additional leave after a deal closes, taken as "
            "time off rather than carried forward and forgotten."
        ),
    },
}

_UPSERT = sa.text(
    """
    INSERT INTO companies (id, tenant_id, about_company, work_life, benefits_text)
    VALUES (gen_random_uuid(), :tid, :about, :work, :benefits)
    ON CONFLICT (tenant_id) DO UPDATE SET
        about_company = COALESCE(
            NULLIF(btrim(companies.about_company), ''), EXCLUDED.about_company),
        work_life = COALESCE(
            NULLIF(btrim(companies.work_life), ''), EXCLUDED.work_life),
        benefits_text = COALESCE(
            NULLIF(btrim(companies.benefits_text), ''), EXCLUDED.benefits_text)
    """
)


def _allow_cross_tenant(connection) -> None:
    connection.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
    connection.execute(
        sa.text("SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000000'")
    )


def upgrade() -> None:
    connection = op.get_bind()
    _allow_cross_tenant(connection)
    for tenant_id, values in PROFILES.items():
        connection.execute(
            _UPSERT,
            {
                "tid": tenant_id,
                "about": values["about_company"],
                "work": values["work_life"],
                "benefits": values["benefits_text"],
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    _allow_cross_tenant(connection)
    for tenant_id, values in PROFILES.items():
        for column, text in values.items():
            connection.execute(
                sa.text(
                    f"UPDATE companies SET {column} = NULL "
                    f"WHERE tenant_id = :tid AND {column} = :text"
                ),
                {"text": text, "tid": tenant_id},
            )
