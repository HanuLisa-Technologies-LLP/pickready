"""The job approval chain is gone. This keeps it gone.

Vivekium release (PLAN-p1 3.9). A job goes live through ONE route,
`POST /jobs/{id}/publish`, behind the JD, the saved SWOT and the saved skills.
Deleted, all of it:

* the routes: the hand-off to the Hiring Manager, `submit`, `approve`, the
  approvals list, and `PUT /jobs/{id}/jd` (the second JD writer, which left
  the canonical document stale);
* their schemas (`ApproveIn`, `ApprovalOut`, `JDUpdateIn`);
* the capability `send_jd_to_hiring_manager`, from the constants, the matrix,
  the spec grant tables and RBAC_INVARIANTS, and its `role_permissions` rows
  (migration 0119_job_approval_chain_removed), because a capability and its
  seeding are one change and so is its removal;
* the two lifecycle states only the chain could write, `SENT_TO_HIRING_MANAGER`
  and `IN_REVIEW`, from `JobLifecycleState` AND from `ck_jobs_lifecycle_state`,
  which now admits exactly the six states the enum declares.

What SURVIVES, deliberately, and is not this file's business: `APPROVE_JOB`,
`CONFIGURE_APPROVAL_LEVELS`, the multi-level `approval_fsm` functions and the
company approval-levels route are one unit handed to Phase 7 (their route file
is Phase 7's); `approval_fsm.apply_direct_publish` and `job_approvals` stay
because publish writes them.

A deleted feature is deleted everywhere, so this is a whitespace-normalised
sweep of the live tree (`tests/removal_sweep`), plus the route table, the
enum, and the database itself.
"""
from __future__ import annotations

import importlib.util
import re

from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import capabilities as caps
from app.services.hiring_pipeline import JobLifecycleState
from tests import skills_fixtures as fx
from tests.removal_sweep import BACKEND, REPO, sweep

MIGRATION = BACKEND / "alembic" / "versions" / "0119_job_approval_chain_removed.py"

PATTERN = re.compile(
    "|".join(
        (
            r"send[-_]to[-_]hiring[-_]manager",
            r"send_jd_to_hiring_manager",
            r"SEND_JD_TO_HIRING_MANAGER",
            r"SENT_TO_HIRING_MANAGER",
            r"sent_to_hiring_manager",
            r"sendToHiringManager",
            # As a lifecycle state. The word pair "in review" in prose is
            # not this; the upper-case identifier is.
            r"\bIN_REVIEW\b",
            r"\bApproveIn\b",
            r"\bApprovalOut\b",
            r"\bJDUpdateIn\b",
        )
    )
)

#: Every exemption, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_job_approval_chain_removed.py",
    # Asserts, by name, that the capability is ABSENT from the Recruiter's and
    # the HR Manager's grants. A test of an absence has to spell the absence.
    BACKEND / "tests" / "test_rbac.py",
    # Seeds rows in the two retired states on purpose: 0118's remap (and
    # 0119's) exists to move exactly those rows, and a migration test that
    # cannot name its input tests nothing.
    BACKEND / "tests" / "test_skills_contract_migration.py",
    # Maps a stored `audit_log.action` string to words on the activity feed.
    # An audit row is history and is never rewritten, so an action written
    # under that name must still read as words rather than as an identifier.
    # The same reason the `dna` correlation kind survives.
    REPO / "frontend" / "components" / "customer-activity-section.tsx",
)

#: Pending hand-offs, each owned by a package that deletes or rewrites it.
#: EMPTY: the last one, the harness audit-row scenario, was rewritten onto
#: Save Skills (PLAN-p1 WP-E). Kept as a slice so a future hand-off is one
#: entry appended above rather than a new mechanism.
PENDING_HAND_OFFS = EXEMPT[4:]


def test_no_live_source_names_the_approval_chain() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT)
    assert not hits, hits


def test_every_pending_hand_off_still_has_something_to_hand_off() -> None:
    stale = [
        str(path.relative_to(REPO))
        for path in PENDING_HAND_OFFS
        if not path.exists() or not sweep(PATTERN, roots=(path,))
    ]
    assert not stale, f"these hand-offs have landed; delete their EXEMPT entries: {stale}"


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the live tree."""
    hits = sweep(re.compile(r"PUBLISH_STEP_SKILLS"), roots=(BACKEND / "app",))
    assert any("jobs.py" in hit for hit in hits), hits


def test_no_registered_route_is_part_of_the_chain() -> None:
    from app.main import app

    offending = sorted(
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if "/jobs/" in path
        for method in operations
        if path.endswith(("/submit", "/approve", "/approvals", "/send-to-hiring-manager"))
        or (path.endswith("/{job_id}/jd") and method == "put")
    )
    assert not offending, offending


def test_the_capability_constant_is_gone() -> None:
    assert not hasattr(caps, "SEND_JD_TO_HIRING_MANAGER")
    assert "send_jd_to_hiring_manager" not in caps.ALL_CAPABILITIES
    assert "send_jd_to_hiring_manager" not in caps.RBAC_INVARIANTS


def test_the_lifecycle_is_the_six_states_the_migration_admits() -> None:
    spec = importlib.util.spec_from_file_location("migration_0119", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert tuple(state.value for state in JobLifecycleState) == module.LIFECYCLE_STATES
    assert module.revision == "0119_job_approval_chain_removed"
    assert module.down_revision == "0118_skills_contract"


async def test_the_database_holds_no_grant_and_admits_no_retired_state() -> None:
    """Asked of the MIGRATED database, not of the code: a capability whose
    rows outlive its constant is a grant nobody can see, and a CHECK wider
    than the enum admits a state no reader understands."""
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            grants = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM role_permissions "
                        "WHERE capability = 'send_jd_to_hiring_manager'"
                    )
                )
            ).scalar_one()
            check = (
                await session.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'ck_jobs_lifecycle_state'"
                    )
                )
            ).scalar_one()
    assert grants == 0
    for state in JobLifecycleState:
        assert f"'{state.value}'" in check, state
    for retired in ("SENT_TO_HIRING_MANAGER", "IN_REVIEW"):
        assert retired not in check, retired
