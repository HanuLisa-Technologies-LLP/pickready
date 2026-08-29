"""RBAC 30, 31 and 34, plus spec-doc6 4.4's no-auto-rejection invariant.

WHAT THIS FILE IS FOR
---------------------
RBAC 31 ends with a sentence that is the whole reason this file exists:

    "The audit trail MUST NOT depend exclusively on dashboard rendering."

So every assertion here is against the WRITER and the stored row, never
against a rendered response. `test_the_trail_exists_with_nothing_rendered`
runs the entire scripted scenario with no view, no route and no serialiser in
the process at all.

The scripted scenario is spec-doc6 9.3's: create job -> send to hiring manager
-> finalise -> publish -> apply -> shortlist -> flag -> dispose. It asserts
every expected row exists with the correct previous and new state.

The recording session is a capture, not a mock. `_RecordingSession` stores the
real `AuditLog` objects the real `audit.record_action` builds, so what is
asserted is the row the database would receive, field by field. No assertion
in this file is "a function was called".
"""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import Role
from app.models.tenant import AuditLog
from app.services import audit as audit_service
from app.services import capabilities as caps
from app.services import rbac
from app.services.hiring_pipeline import JobLifecycleState
from app.services.tools import permissions

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000c1")
RECRUITER = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
HIRING_MANAGER = uuid.UUID("00000000-0000-4000-8000-0000000000c2")
HR_MANAGER = uuid.UUID("00000000-0000-4000-8000-0000000000c3")
JOB = uuid.UUID("11111111-0000-4000-8000-0000000000c1")
CANDIDATE = uuid.UUID("22222222-0000-4000-8000-0000000000c1")
APPLICATION = uuid.UUID("33333333-0000-4000-8000-0000000000c1")
CORRELATION = "corr-0000-0001"


class _RecordingSession:
    """Captures the real ORM rows the writer builds.

    Deliberately not a mock. `record_action` constructs an `AuditLog` and sets
    twelve attributes on it; what matters is the object's field values, which
    is exactly what a database would store. Asserting on those is asserting on
    the row.
    """

    def __init__(self) -> None:
        self.rows: list[AuditLog] = []

    def add(self, row: AuditLog) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        return None

    def by_action(self, action: str) -> list[AuditLog]:
        return [row for row in self.rows if row.action == action]


@pytest.fixture
def session() -> _RecordingSession:
    return _RecordingSession()


# ── RBAC 30: every field it names is a column, and the writer fills it ───────

#: The nine bullets of RBAC 30, mapped onto columns. `SHOULD record` in the
#: document; treated as MUST here, because spec-doc6 9.3 restates the same
#: list as an unconditional requirement.
SECTION_30_FIELDS: tuple[tuple[str, str], ...] = (
    ("actor", "actor_user_id"),
    ("actor role at time of action", "actor_role"),
    ("tenant/client", "tenant_id"),
    ("action", "action"),
    ("resource type", "target_type"),
    ("resource ID", "target_id"),
    ("previous value/state", "previous_state"),
    ("new value/state", "new_state"),
    ("timestamp", "at"),
    ("job context", "job_id"),
    ("application context", "application_id"),
    ("candidate context", "candidate_id"),
    ("source/request metadata", "request_path"),
)

#: RBAC 34's dual attribution. Two columns, because one cannot hold both.
SECTION_34_FIELDS: tuple[str, ...] = ("actor_user_id", "agent_name")


@pytest.mark.parametrize(
    "label,column", SECTION_30_FIELDS, ids=[label for label, _ in SECTION_30_FIELDS]
)
def test_section_30_field_is_a_column(label: str, column: str) -> None:
    assert column in AuditLog.__table__.columns, f"RBAC 30 requires {label}"


@pytest.mark.parametrize("column", SECTION_34_FIELDS)
def test_section_34_dual_attribution_columns_exist(column: str) -> None:
    assert column in AuditLog.__table__.columns


async def test_the_writer_fills_every_section_30_field(session) -> None:
    row = await audit_service.record_action(
        session,
        action=audit_service.JOB_PUBLISHED,
        actor_user_id=RECRUITER,
        actor_role=Role.recruiter.value,
        tenant_id=TENANT,
        resource_type="job",
        resource_id=JOB,
        previous_state={"lifecycle_state": JobLifecycleState.FINALIZED.value},
        new_state={"lifecycle_state": JobLifecycleState.PUBLISHED.value},
        job_id=JOB,
        request_method="POST",
        request_path=f"/api/v1/jobs/{JOB}/publish",
        request_ip="203.0.113.7",
        correlation_id=CORRELATION,
    )
    assert row.actor_user_id == RECRUITER
    assert row.actor_role == Role.recruiter.value
    assert row.tenant_id == TENANT
    assert row.previous_state == {"lifecycle_state": "FINALIZED"}
    assert row.new_state == {"lifecycle_state": "PUBLISHED"}
    assert row.job_id == JOB
    assert row.request_method == "POST"
    assert row.request_ip == "203.0.113.7"
    assert row.correlation_id == CORRELATION
    assert row.agent_name is None


async def test_the_actor_role_is_the_role_at_the_time_not_a_join(session) -> None:
    """RBAC 30 asks for the role AT THE TIME OF THE ACTION.

    Copied onto the row rather than joined to `users` later: a person's role
    changes, and what authority a past action was taken under does not. The
    test is that the stored value is the one passed, even when it differs from
    anything the user might hold now.
    """
    row = await audit_service.record_action(
        session,
        action=audit_service.JOB_FINALIZED,
        actor_user_id=HIRING_MANAGER,
        actor_role=Role.hiring_manager.value,
        tenant_id=TENANT,
        resource_id=JOB,
        job_id=JOB,
    )
    assert row.actor_role == "hiring_manager"


async def test_a_missing_actor_role_is_recorded_as_unknown_not_as_null(session) -> None:
    """A null role is indistinguishable from a column nobody populates. An
    explicit "unknown" says the writer looked and could not tell."""
    row = await audit_service.record_action(
        session,
        action=audit_service.JOB_CREATED,
        actor_user_id=RECRUITER,
        actor_role=None,
        tenant_id=TENANT,
    )
    assert row.actor_role == audit_service.AUDIT_ACTOR_ROLE_UNKNOWN


# ── RBAC 34: an agent row without its human is refused, not defaulted ────────

async def test_an_agent_action_records_both_principals(session) -> None:
    row = await audit_service.record_agent_action(
        session,
        action=audit_service.JOB_CRITERIA_EDITED,
        agent_name=permissions.AGENT_SUTRA,
        principal_user_id=HIRING_MANAGER,
        principal_role=Role.hiring_manager.value,
        tenant_id=TENANT,
        resource_type="job",
        resource_id=JOB,
        job_id=JOB,
        new_state={"must_have": ["Kafka"]},
    )
    assert row.agent_name == permissions.AGENT_SUTRA
    assert row.actor_user_id == HIRING_MANAGER
    assert row.actor_role == "hiring_manager"


async def test_an_agent_row_with_no_human_principal_raises(session) -> None:
    """The one shape RBAC 34 forbids, refused rather than written and
    explained later. A row that lost half its attribution looks exactly like a
    human action, which is the reading that must never be possible."""
    with pytest.raises(audit_service.AgentPrincipalError):
        await audit_service.record_action(
            session,
            action=audit_service.JOB_CRITERIA_EDITED,
            actor_user_id=None,
            actor_role=None,
            tenant_id=TENANT,
            agent_name=permissions.AGENT_SUTRA,
        )
    assert session.rows == [], "nothing may be written on the refusing path"


def test_the_database_refuses_the_same_shape() -> None:
    """The service is not the only writer a database ever gets.

    Migration 0061 carries a CHECK, so a backfill script or a psql session
    cannot produce the row either.
    """
    source = _migration_source()
    assert "ck_audit_log_agent_has_principal" in source
    assert "agent_name IS NULL OR actor_user_id IS NOT NULL" in source


def _migration_source() -> str:
    import pathlib

    return (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0061_rbac_cardinality_and_audit.py"
    ).read_text(encoding="utf-8")


# ── spec-doc6 9.3: the scripted end-to-end scenario ─────────────────────────

async def _run_the_scenario(session) -> None:
    """create job -> send -> finalise -> publish -> apply -> shortlist ->
    flag -> dispose, as audit rows.

    Written as the writer calls a real handler would make, so the previous and
    new states are the ones the lifecycle actually moves through rather than
    values invented for the assertion.
    """
    lifecycle = [
        (
            audit_service.JOB_CREATED,
            RECRUITER,
            Role.recruiter,
            None,
            JobLifecycleState.DRAFT.value,
        ),
        (
            audit_service.JOB_SENT_TO_HIRING_MANAGER,
            RECRUITER,
            Role.recruiter,
            JobLifecycleState.DRAFT.value,
            JobLifecycleState.SENT_TO_HIRING_MANAGER.value,
        ),
        (
            audit_service.JOB_CRITERIA_EDITED,
            HIRING_MANAGER,
            Role.hiring_manager,
            JobLifecycleState.SENT_TO_HIRING_MANAGER.value,
            JobLifecycleState.IN_REVIEW.value,
        ),
        (
            audit_service.JOB_FINALIZED,
            HIRING_MANAGER,
            Role.hiring_manager,
            JobLifecycleState.IN_REVIEW.value,
            JobLifecycleState.FINALIZED.value,
        ),
        (
            audit_service.JOB_PUBLISHED,
            RECRUITER,
            Role.recruiter,
            JobLifecycleState.FINALIZED.value,
            JobLifecycleState.PUBLISHED.value,
        ),
    ]
    for action, actor, role, before, after in lifecycle:
        await audit_service.record_action(
            session,
            action=action,
            actor_user_id=actor,
            actor_role=role.value,
            tenant_id=TENANT,
            resource_type="job",
            resource_id=JOB,
            job_id=JOB,
            previous_state=None if before is None else {"lifecycle_state": before},
            new_state={"lifecycle_state": after},
            correlation_id=CORRELATION,
        )

    await audit_service.record_action(
        session,
        action=audit_service.CANDIDATE_APPLIED,
        actor_user_id=CANDIDATE,
        actor_role=Role.candidate.value,
        tenant_id=TENANT,
        resource_type="application",
        resource_id=APPLICATION,
        job_id=JOB,
        application_id=APPLICATION,
        candidate_id=CANDIDATE,
        new_state={"status": "applied"},
        correlation_id=CORRELATION,
    )
    await audit_service.record_action(
        session,
        action=audit_service.CANDIDATE_SHORTLISTED,
        actor_user_id=RECRUITER,
        actor_role=Role.recruiter.value,
        tenant_id=TENANT,
        resource_type="application",
        resource_id=APPLICATION,
        job_id=JOB,
        application_id=APPLICATION,
        candidate_id=CANDIDATE,
        previous_state={"status": "applied"},
        new_state={"status": "shortlisted"},
        correlation_id=CORRELATION,
    )
    # The integrity flag. Raised by the pipeline, blocking nothing.
    await audit_service.record_agent_action(
        session,
        action=audit_service.INTEGRITY_FLAG_RAISED,
        agent_name=permissions.AGENT_MITI,
        principal_user_id=RECRUITER,
        principal_role=Role.recruiter.value,
        tenant_id=TENANT,
        resource_type="application",
        resource_id=APPLICATION,
        job_id=JOB,
        application_id=APPLICATION,
        candidate_id=CANDIDATE,
        new_state={"severity": "minor", "axis": "timeline"},
        correlation_id=CORRELATION,
    )
    # The human disposition. This is the row that makes the flag actionable.
    await audit_service.record_action(
        session,
        action=audit_service.INTEGRITY_DISPOSITION_RECORDED,
        actor_user_id=HR_MANAGER,
        actor_role=Role.hr_manager.value,
        tenant_id=TENANT,
        resource_type="application",
        resource_id=APPLICATION,
        job_id=JOB,
        application_id=APPLICATION,
        candidate_id=CANDIDATE,
        previous_state={"disposition": None},
        new_state={"disposition": "cleared", "decided_by": str(HR_MANAGER)},
        correlation_id=CORRELATION,
    )


#: Every row spec-doc6 9.3's scenario must produce.
EXPECTED_SCENARIO_ROWS: tuple[tuple[str, str | None, str], ...] = (
    (audit_service.JOB_CREATED, None, "DRAFT"),
    (audit_service.JOB_SENT_TO_HIRING_MANAGER, "DRAFT", "SENT_TO_HIRING_MANAGER"),
    (audit_service.JOB_CRITERIA_EDITED, "SENT_TO_HIRING_MANAGER", "IN_REVIEW"),
    (audit_service.JOB_FINALIZED, "IN_REVIEW", "FINALIZED"),
    (audit_service.JOB_PUBLISHED, "FINALIZED", "PUBLISHED"),
)


async def test_the_scripted_scenario_writes_every_expected_row(session) -> None:
    await _run_the_scenario(session)
    actions = [row.action for row in session.rows]
    for action in (
        audit_service.JOB_CREATED,
        audit_service.JOB_SENT_TO_HIRING_MANAGER,
        audit_service.JOB_CRITERIA_EDITED,
        audit_service.JOB_FINALIZED,
        audit_service.JOB_PUBLISHED,
        audit_service.CANDIDATE_APPLIED,
        audit_service.CANDIDATE_SHORTLISTED,
        audit_service.INTEGRITY_FLAG_RAISED,
        audit_service.INTEGRITY_DISPOSITION_RECORDED,
    ):
        assert action in actions, f"the scenario produced no {action} row"


@pytest.mark.parametrize(
    "action,before,after",
    EXPECTED_SCENARIO_ROWS,
    ids=[action for action, _, _ in EXPECTED_SCENARIO_ROWS],
)
async def test_each_lifecycle_row_carries_the_right_states(
    session, action: str, before: str | None, after: str
) -> None:
    await _run_the_scenario(session)
    rows = session.by_action(action)
    assert len(rows) == 1, action
    row = rows[0]
    expected_before = None if before is None else {"lifecycle_state": before}
    assert row.previous_state == expected_before, action
    assert row.new_state == {"lifecycle_state": after}, action


async def test_the_lifecycle_chain_is_continuous(session) -> None:
    """Each row's previous state is the one the row before it wrote.

    A gap here means a transition happened that nothing recorded, which is
    exactly the case an audit trail exists to make impossible.
    """
    await _run_the_scenario(session)
    previous_new: str | None = None
    for action, _before, after in EXPECTED_SCENARIO_ROWS:
        row = session.by_action(action)[0]
        current_before = (row.previous_state or {}).get("lifecycle_state")
        assert current_before == previous_new, f"{action} does not follow its predecessor"
        previous_new = after


async def test_one_correlation_id_runs_through_the_whole_flow(session) -> None:
    """spec-doc6 4.1: a correlation id issued at job creation must appear in
    every audit row for that flow, including the agent's."""
    await _run_the_scenario(session)
    assert {row.correlation_id for row in session.rows} == {CORRELATION}


async def test_the_agent_row_in_the_scenario_names_both_principals(session) -> None:
    await _run_the_scenario(session)
    flag = session.by_action(audit_service.INTEGRITY_FLAG_RAISED)[0]
    assert flag.agent_name == permissions.AGENT_MITI
    assert flag.actor_user_id == RECRUITER


async def test_the_trail_exists_with_nothing_rendered(session) -> None:
    """RBAC 31's closing sentence, as a test.

    The whole scenario runs with no route, no response model and no
    serialiser involved. If the trail depended on the dashboard, this would
    produce nothing.
    """
    await _run_the_scenario(session)
    assert len(session.rows) == 9
    assert all(isinstance(row, AuditLog) for row in session.rows)


# ── spec-doc6 4.4: no rejection without a recorded human disposition ─────────

async def test_no_rejection_exists_without_a_human_disposition(session) -> None:
    """The invariant, stated as a property over the trail.

    A rejection row must be traceable to a human decision. In the scenario
    above the integrity flag is followed by a disposition and no rejection
    happens at all, which is the point: the flag did not reject anybody.
    """
    await _run_the_scenario(session)
    rejections = [
        row for row in session.rows if row.action in audit_service.REJECTION_ACTIONS
    ]
    assert rejections == [], "the scripted flow must not produce a rejection"

    # And when a rejection IS recorded, it is by a human, never by an agent.
    await audit_service.record_action(
        session,
        action=audit_service.CANDIDATE_REJECTED,
        actor_user_id=RECRUITER,
        actor_role=Role.recruiter.value,
        tenant_id=TENANT,
        resource_type="application",
        resource_id=APPLICATION,
        application_id=APPLICATION,
        candidate_id=CANDIDATE,
        previous_state={"status": "shortlisted"},
        new_state={"status": "rejected"},
    )
    for row in session.by_action(audit_service.CANDIDATE_REJECTED):
        assert row.agent_name is None, "an agent may never author a rejection"
        assert row.actor_user_id is not None


@pytest.mark.parametrize("agent", sorted(rbac.AGENT_CAPABILITIES))
def test_no_agent_can_reach_a_capability_that_rejects(agent: str) -> None:
    """The structural half of the same rule. `DECIDE_PROFILE` is this
    codebase's shortlist/reject/hold capability and no agent holds it, so a
    rejection cannot be agent-authored even in principle."""
    assert caps.DECIDE_PROFILE not in rbac.agent_capabilities(agent)
    assert caps.DECIDE_PROFILE in rbac.AGENT_FORBIDDEN_CAPABILITIES


def test_the_triangulation_result_still_has_no_reject_field() -> None:
    """The standing rule, restated where an audit reader would look: G3 fails
    loudly and blocks nothing, and the enforcement is the absence of the
    capability rather than a handler that declines to use it."""
    from app.services.miti.triangulation import TriangulationResult

    fields = set(TriangulationResult.__dataclass_fields__)
    assert not fields & {"reject", "rejected", "status", "decision"}, fields


def test_review_dispositions_decided_by_is_still_restrict() -> None:
    """A disposition whose person was erased asserts that a human decided
    while being unable to say who, which is indistinguishable from the
    pipeline having written it."""
    import pathlib
    import re

    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    sources = [
        path.read_text(encoding="utf-8")
        for path in versions.glob("*.py")
        if "review_disposition" in path.read_text(encoding="utf-8")
    ]
    assert sources, "no migration defines review_dispositions"
    assert any(
        re.search(r"decided_by.*?RESTRICT", source, re.S) for source in sources
    ), "review_dispositions.decided_by must stay ON DELETE RESTRICT"


# ── The activity view is a reader over the same rows ─────────────────────────

def test_the_activity_view_answers_all_seven_questions_of_section_31() -> None:
    """31 lists seven questions. Each maps onto a field the reader returns."""
    for field in (
        "actor_user_id",   # who changed this
        "actor_role",      # ...and with what authority
        "action",          # what did they change
        "at",              # when
        "job_id",          # which job
        "candidate_id",    # which candidate
        "previous_state",  # what was the previous state
        "new_state",       # what is the current state
    ):
        assert field in audit_service.ACTIVITY_FIELDS


def test_the_activity_actions_are_named_constants_not_literals() -> None:
    """The writer and the view must name the same strings. Two copies of
    "job_published" drift the first time one of them is corrected."""
    assert audit_service.JOB_PUBLISHED in audit_service.ACTIVITY_ACTIONS
    assert audit_service.CANDIDATE_REJECTED in audit_service.ACTIVITY_ACTIONS
    assert len(set(audit_service.ACTIVITY_ACTIONS)) == len(audit_service.ACTIVITY_ACTIONS)


# ── An exceptional decision is recorded as one (RBAC 7.5, spec-doc6 C13) ─────

async def test_an_audited_exception_is_flagged_on_the_row(session) -> None:
    """7.5 grants the Super Admin override authority and then requires the
    override to be recorded. A row that looked like every other row would
    satisfy the grant and not the requirement.

    The Super Admin, not the HR Manager: 9.6 names exactly one administrative
    exception to "Recruiter publishes the job. Period.", and 24's HR Manager
    YES* is withheld pending a product decision (see
    `test_only_the_recruiter_and_the_super_admin_may_publish`).
    """
    from app.models.enums import Role as R

    decision = rbac.decide(
        rbac.Principal(user_id=HR_MANAGER, tenant_id=TENANT, role=R.client),
        caps.PUBLISH_JOB,
        rbac.Resource(
            kind="job",
            tenant_id=TENANT,
            job_id=JOB,
            lifecycle_state=JobLifecycleState.FINALIZED.value,
        ),
        granted=True,
    )
    assert decision.allowed
    assert decision.exceptional, "a Super Admin publish is 7.5's recorded override"

    row = await audit_service.record_action(
        session,
        action=audit_service.JOB_PUBLISHED,
        actor_user_id=HR_MANAGER,
        actor_role=Role.client.value,
        tenant_id=TENANT,
        resource_id=JOB,
        job_id=JOB,
        exceptional=decision.exceptional,
    )
    assert row.exceptional is True


async def test_a_recruiter_publish_is_not_flagged_as_exceptional(session) -> None:
    """9.6: the Recruiter is the operational publisher. Flagging the normal
    case would make the flag meaningless."""
    decision = rbac.decide(
        rbac.Principal(user_id=RECRUITER, tenant_id=TENANT, role=Role.recruiter),
        caps.PUBLISH_JOB,
        rbac.Resource(
            kind="job",
            tenant_id=TENANT,
            job_id=JOB,
            lifecycle_state=JobLifecycleState.FINALIZED.value,
            assignments=frozenset({(rbac.ASSIGNMENT_RECRUITER, str(RECRUITER))}),
        ),
        granted=True,
    )
    assert decision.allowed
    assert decision.exceptional is False
