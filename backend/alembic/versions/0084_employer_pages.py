"""Public employer pages: a URL slug and a visibility flag on tenants.

From the 2026-09-05 add-features spec ("Employer Page & Content"): every client
company gets a persistent, publicly visible page at /employers/{slug} carrying
its profile and a careers list of its live jobs.

WHY `tenants` AND NOT `companies`
---------------------------------
A customer IS a `tenants` row (claude.md, Provider Portal section). The name
the slug derives from lives on `tenants`, and the `companies` row -- the
client-authored candidate-facing page -- does not exist until the client first
signs in, so a slug keyed there would leave a freshly onboarded customer with
no page at all. The page's narrative sections still read from `companies` when
a row exists, with the tenant's onboarding prose as the fallback, exactly as
the candidate portal already resolves them.

RLS: no policy change. The public read path runs on `get_public_db` (the
bypass scope the public apply page already uses), and the handlers whitelist
public-safe fields; the tenant-scoped policies on `tenants` are untouched
because these are just two more columns on an existing table.

BACKFILL
--------
Every existing tenant gets a slug derived from its name: casefolded, runs of
non-alphanumerics collapsed to single hyphens, trimmed. A name that slugs to
the empty string gets `employer-` plus eight hex characters of md5(id); a
base that collides with another tenant's gets a six-character md5(id) suffix.
The same rules live in `services/employer_pages.slugify` for rows created
after this migration -- change them together.

CHAIN NOTE: down_revision names "0083" per the 2026-09-05 wave's migration
number assignments (0080-0083 are parallel agents' revisions). If a chain
neighbour registered a longer revision id, the coordinator reconciles the
identifiers in an integration pass.

Revision ID: 0084
Revises: 0083
"""
from alembic import op
import sqlalchemy as sa

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("public_slug", sa.String(140), nullable=True))
    op.add_column(
        "tenants",
        sa.Column(
            "is_public", sa.Boolean(), nullable=False, server_default="true"
        ),
    )

    # Backfill: base slug from the name; md5-of-id suffix on collision; a
    # wholly non-alphanumeric name falls back to an id-derived slug. The
    # window count is over BASE slugs, so every member of a colliding group
    # gets a distinct suffix (deterministic, keyed to the row's own id).
    op.execute(
        """
        WITH slugged AS (
            SELECT id,
                   left(
                       trim(both '-' from
                            regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')),
                       130
                   ) AS base
            FROM tenants
        ),
        resolved AS (
            SELECT id,
                   CASE
                       WHEN base = '' OR base IS NULL
                           THEN 'employer-' || substr(md5(id::text), 1, 8)
                       WHEN count(*) OVER (PARTITION BY base) > 1
                           THEN base || '-' || substr(md5(id::text), 1, 6)
                       ELSE base
                   END AS slug
            FROM slugged
        )
        UPDATE tenants t
        SET public_slug = r.slug
        FROM resolved r
        WHERE t.id = r.id
        """
    )

    # UNIQUE after the backfill so a pre-existing duplicate name would fail
    # HERE, loudly, rather than corrupt the page routing silently.
    op.create_unique_constraint("uq_tenants_public_slug", "tenants", ["public_slug"])


def downgrade() -> None:
    op.drop_constraint("uq_tenants_public_slug", "tenants", type_="unique")
    op.drop_column("tenants", "is_public")
    op.drop_column("tenants", "public_slug")
