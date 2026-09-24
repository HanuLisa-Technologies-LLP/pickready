"""The approval chain's database half: six lifecycle states, one capability gone.

Revision ID: 0119_skills_setup
Revises: 0118_skills_contract

The Vivekium release deleted the job approval chain (`send-to-hiring-manager`,
`submit`, `approve`, `approvals`) together with the two lifecycle states only
it could write, `SENT_TO_HIRING_MANAGER` and `IN_REVIEW`, and the capability
only its route read, `send_jd_to_hiring_manager`. 0118 deliberately left the
database half of that to the change that deletes the code, because each is
the second half of one change: tightening the CHECK before the enum lost its
members would let a live route write a value the CHECK refuses, and deleting
the rows before the constant went would fail `test_capability_seed_parity`.

1. LIFECYCLE REMAP, AGAIN, AND WIDER THAN 0118'S. 0118 remapped the retired
   states on NOT-ARCHIVED rows only. An archived job can still carry one, and
   the CHECK below would refuse the whole upgrade over it. So every row still
   in a retired state moves: to PUBLISHED when it was ever published
   (`ratified_at` or `status = 'ratified'`), because NO PUBLISHED JOB IS EVER
   UNPUBLISHED (CONTRACT v3), and to DRAFT otherwise. Idempotent: on a
   database 0118 already remapped this touches only archived stragglers.
2. `ck_jobs_lifecycle_state` is recreated with exactly the six states
   `hiring_pipeline.JobLifecycleState` declares.
3. Every `role_permissions` row for `send_jd_to_hiring_manager`, global and
   per-tenant, is deleted.

DOWNGRADE restores the eight-state CHECK and re-seeds the GLOBAL rows 0075
wrote for the capability. Two things it cannot restore, and says so: rows the
remap moved stay where they were moved (the retired states carried no
information a later state lacks), and per-tenant overrides of the capability
are gone (they overrode a route that no longer exists).
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0119_skills_setup"
down_revision = "0118_skills_contract"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: `hiring_pipeline.JobLifecycleState`, restated as of this revision.
#: `tests/test_job_approval_chain_removed.py` compares the two.
LIFECYCLE_STATES: tuple[str, ...] = (
    "DRAFT",
    "FINALIZED",
    "PUBLISHED",
    "CANDIDATE_APPLICATIONS",
    "HIRING_PROCESS",
    "CLOSED_ARCHIVED",
)
RETIRED_STATES: tuple[str, ...] = ("SENT_TO_HIRING_MANAGER", "IN_REVIEW")
#: 0061's order, for the downgrade.
_PREVIOUS_STATES: tuple[str, ...] = (
    "DRAFT",
    "SENT_TO_HIRING_MANAGER",
    "IN_REVIEW",
    "FINALIZED",
    "PUBLISHED",
    "CANDIDATE_APPLICATIONS",
    "HIRING_PROCESS",
    "CLOSED_ARCHIVED",
)
RETIRED_CAPABILITY = "send_jd_to_hiring_manager"
#: 0075's global grants for the retired capability, for the downgrade only.
_PREVIOUS_GLOBAL_GRANTS: tuple[tuple[str, bool], ...] = (
    ("client", True),
    ("hr_manager", True),
    ("recruitment_manager", True),
    ("recruiter", True),
    ("hiring_manager", False),
    ("interview_manager", False),
)
_IS_PUBLISHED = "(ratified_at IS NOT NULL OR status::text = 'ratified')"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def remap_retired_states(connection: sa.engine.Connection) -> dict[str, int]:
    """Move every row still in a retired approval state. Returns the counts."""
    retired = _quoted(RETIRED_STATES)
    published = connection.execute(
        sa.text(
            f"UPDATE jobs SET lifecycle_state = 'PUBLISHED' "
            f"WHERE lifecycle_state IN ({retired}) AND {_IS_PUBLISHED}"
        )
    ).rowcount
    drafted = connection.execute(
        sa.text(
            f"UPDATE jobs SET lifecycle_state = 'DRAFT' "
            f"WHERE lifecycle_state IN ({retired}) AND NOT {_IS_PUBLISHED}"
        )
    ).rowcount
    return {"published": published, "drafted": drafted}


def delete_retired_capability(connection: sa.engine.Connection) -> int:
    return connection.execute(
        sa.text("DELETE FROM role_permissions WHERE capability = :capability"),
        {"capability": RETIRED_CAPABILITY},
    ).rowcount


def upgrade() -> None:
    connection = op.get_bind()
    moved = remap_retired_states(connection)
    op.drop_constraint("ck_jobs_lifecycle_state", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_lifecycle_state",
        "jobs",
        f"lifecycle_state IS NULL OR lifecycle_state IN ({_quoted(LIFECYCLE_STATES)})",
    )
    deleted = delete_retired_capability(connection)
    logger.info(
        "0119 lifecycle_published=%d lifecycle_drafted=%d "
        "send_jd_role_permissions_deleted=%d",
        moved["published"], moved["drafted"], deleted,
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_lifecycle_state", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_lifecycle_state",
        "jobs",
        f"lifecycle_state IS NULL OR lifecycle_state IN ({_quoted(_PREVIOUS_STATES)})",
    )
    for role, allowed in _PREVIOUS_GLOBAL_GRANTS:
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            SELECT gen_random_uuid(), NULL, '{role}', '{RETIRED_CAPABILITY}',
                   {'true' if allowed else 'false'}
            WHERE NOT EXISTS (
                SELECT 1 FROM role_permissions
                WHERE tenant_id IS NULL
                  AND role = '{role}' AND capability = '{RETIRED_CAPABILITY}'
            )
            """
        )
