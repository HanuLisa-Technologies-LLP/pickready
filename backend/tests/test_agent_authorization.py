"""RBAC 34: an agent acts under a human, and can never exceed them.

    "A Recruiter-authorized AI agent may assist with JD generation. It MUST
     NOT use that authority to modify Hiring Manager-controlled criteria."
                                          -- RBAC_SPECIFICATION.md 34

That sentence is the acceptance criterion for this file, and
`test_the_specifications_own_worked_example` is it, executed.

WHAT IS AND IS NOT BEING TESTED
-------------------------------
This tests the AUTHORIZATION of an agent action, through the same
`rbac.decide` an HTTP request goes through (spec-doc6 9.2 requires the same
layer, not a parallel one).

It is NOT a test of the Part A pipeline running. It cannot be, and saying so
precisely matters: `services/agents/identity.py` maps all six named agents
onto the OLD runtime surfaces (`AGENT_JOB_SETUP`, `AGENT_RANKING`,
`AGENT_INTERVIEWER`, `AGENT_SCORING`, `AGENT_PPI_REPORT`), and no live path
calls `services/hiring`, `services/miti` or `services/siddhi` yet. So what is
proven here is that the authorization gate refuses an over-reaching agent
action, for every one of the six names, whichever module eventually executes
behind it. Whoever activates Part A inherits the gate; they do not inherit a
claim that Part A ran.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import Role
from app.services import capabilities as caps
from app.services import rbac
from app.services.agents import identity
from app.services.hiring_pipeline import JobLifecycleState
from app.services.tools import permissions

TENANT_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
TENANT_B = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")
RECRUITER = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
HIRING_MANAGER = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
JOB = uuid.UUID("11111111-0000-4000-8000-0000000000a1")
OTHER_JOB = uuid.UUID("11111111-0000-4000-8000-0000000000a2")

ASSIGNMENTS = frozenset(
    {
        (rbac.ASSIGNMENT_RECRUITER, str(RECRUITER)),
        (rbac.ASSIGNMENT_HIRING_MANAGER, str(HIRING_MANAGER)),
    }
)


def _job(
    *,
    tenant: uuid.UUID = TENANT_A,
    job_id: uuid.UUID = JOB,
    state: str = JobLifecycleState.IN_REVIEW.value,
    assignments: frozenset[tuple[str, str]] = ASSIGNMENTS,
) -> rbac.Resource:
    return rbac.Resource(
        kind="job",
        resource_id=job_id,
        tenant_id=tenant,
        job_id=job_id,
        lifecycle_state=state,
        assignments=assignments,
    )


def _principal(role: Role, agent: str, *, user=None, tenant=TENANT_A) -> rbac.Principal:
    return rbac.Principal(
        user_id=user or (RECRUITER if role is Role.recruiter else HIRING_MANAGER),
        tenant_id=tenant,
        role=role,
        agent=agent,
    )


def _granted(role: Role, capability: str) -> bool:
    """What the real permission data would answer for this role.

    Read from `DEFAULT_PERMISSION_MATRIX` rather than hardcoded, so a change
    to the grants moves these cases with it instead of leaving them asserting
    a distribution the product no longer has.
    """
    return bool(caps.DEFAULT_PERMISSION_MATRIX[role].get(capability, False))


# ── The specification's own example ──────────────────────────────────────────

def test_the_specifications_own_worked_example() -> None:
    """RBAC 34, verbatim: a Recruiter-authorized agent must not write the
    Hiring-Manager-controlled criteria."""
    agent_for_recruiter = _principal(Role.recruiter, permissions.AGENT_SUTRA)
    for capability in sorted(caps.HIRING_MANAGER_CONTROLLED):
        result = rbac.authorize_agent_action(
            agent_for_recruiter,
            permissions.AGENT_SUTRA,
            capability,
            _job(),
            granted=True,  # even if the grant layer said yes
        )
        assert result.decision is rbac.Decision.DENY, capability


def test_the_same_agent_under_a_hiring_manager_may_write_them() -> None:
    """The refusal above must come from the PRINCIPAL, not from the agent.

    Without this the test above would pass just as well if agents were
    refused everything, which would prove the gate works by proving the
    feature does not.
    """
    agent_for_hm = _principal(Role.hiring_manager, permissions.AGENT_SUTRA)
    writable = rbac.agent_capabilities(permissions.AGENT_SUTRA)
    assert writable, "Sutra must be able to write something, or the case is vacuous"
    for capability in sorted(writable):
        result = rbac.authorize_agent_action(
            agent_for_hm,
            permissions.AGENT_SUTRA,
            capability,
            _job(),
            granted=_granted(Role.hiring_manager, capability),
        )
        assert result.decision is rbac.Decision.ALLOW, capability


# ── Every one of the six, against an attempt to exceed its principal ─────────

#: Each named agent, the principal it plausibly runs under, and one capability
#: that principal does not hold. RBAC 34 requires the attempt to fail for each.
OVERREACH_CASES: tuple[tuple[str, Role, str], tuple, ...] = (
    # Bodha runs the SWOT session for a Hiring Manager. Under a Recruiter it
    # must not write the SWOT, which is Hiring-Manager-controlled (10.4).
    (permissions.AGENT_BODHA, Role.recruiter, caps.EDIT_SWOT),
    (permissions.AGENT_SUTRA, Role.recruiter, caps.EDIT_MUST_HAVE_SKILLS),
    # Yukti grades resumes. It holds no write capability at all, so every one
    # of these is refused at the declaration step, before the human's
    # permissions are even consulted.
    (permissions.AGENT_YUKTI, Role.client, caps.EDIT_EVALUATION_RUBRICS),
    (permissions.AGENT_VAADA, Role.client, caps.EDIT_MUST_HAVE_SKILLS),
    (permissions.AGENT_MITI, Role.client, caps.EDIT_BEHAVIOURAL_COMPETENCIES),
    (permissions.AGENT_SIDDHI, Role.client, caps.EDIT_JOB_PHILOSOPHY),
)


@pytest.mark.parametrize(
    "agent,role,capability",
    OVERREACH_CASES,
    ids=[f"{agent}|{role.value}|{capability}" for agent, role, capability in OVERREACH_CASES],
)
def test_an_agent_cannot_exceed_its_principal(
    agent: str, role: Role, capability: str
) -> None:
    principal = _principal(role, agent, user=RECRUITER if role is Role.recruiter else HIRING_MANAGER)
    result = rbac.authorize_agent_action(
        principal, agent, capability, _job(), granted=True
    )
    assert result.decision is rbac.Decision.DENY, f"{agent} exceeded {role.value}"


@pytest.mark.parametrize("agent", identity.AGENTS, ids=list(identity.AGENTS))
def test_every_named_agent_has_a_capability_declaration(agent: str) -> None:
    """Deny-by-default is only honest if the table is complete.

    An agent missing from `AGENT_CAPABILITIES` holds nothing, which is the
    safe answer. It is also indistinguishable from an agent somebody forgot to
    register, and the second one is a bug that looks like a policy.
    """
    assert agent in rbac.AGENT_CAPABILITIES


@pytest.mark.parametrize("agent", identity.AGENTS, ids=list(identity.AGENTS))
def test_no_agent_holds_a_capability_forbidden_to_every_agent(agent: str) -> None:
    """RBAC 34's closing list plus 39's last rule: agent execution is not a
    bypass route. Finalization, publication and candidate decisions are human
    acts, whatever the agent's confidence."""
    held = rbac.agent_capabilities(agent)
    assert not (held & rbac.AGENT_FORBIDDEN_CAPABILITIES), agent


@pytest.mark.parametrize("agent", identity.AGENTS, ids=list(identity.AGENTS))
def test_no_agent_may_finalize_publish_or_decide_a_candidate(agent: str) -> None:
    """The same rule as behaviour rather than as set arithmetic. A principal
    who legitimately holds all three still cannot have an agent do them."""
    super_admin = rbac.Principal(
        user_id=HIRING_MANAGER, tenant_id=TENANT_A, role=Role.client, agent=agent
    )
    for capability in (
        caps.FINALIZE_ROLE_DEFINITION,
        caps.PUBLISH_JOB,
        caps.DECIDE_PROFILE,
        caps.UPDATE_PIPELINE_STATUS,
        caps.REJECT_JD,
    ):
        result = rbac.authorize_agent_action(
            super_admin,
            agent,
            capability,
            _job(state=JobLifecycleState.FINALIZED.value),
            granted=True,
        )
        assert result.decision is rbac.Decision.DENY, f"{agent}/{capability}"
        assert result.reason == "capability_forbidden_to_every_agent"


# ── Tenant and job scope carry through to agents unchanged ───────────────────

@pytest.mark.parametrize("agent", identity.AGENTS, ids=list(identity.AGENTS))
def test_an_agent_cannot_cross_a_tenant_boundary(agent: str) -> None:
    """RBAC 34: AI agents MUST NOT bypass tenant isolation. Because the agent
    goes through `decide`, it gets the same 404 a human would, for free."""
    principal = rbac.Principal(
        user_id=HIRING_MANAGER, tenant_id=TENANT_A, role=Role.hiring_manager, agent=agent
    )
    result = rbac.authorize_agent_action(
        principal, agent, caps.EDIT_SWOT, _job(tenant=TENANT_B), granted=True
    )
    assert result.decision is rbac.Decision.NOT_FOUND


def test_an_agent_is_bound_to_its_principals_assigned_job() -> None:
    """RBAC 34: "constrained to the Hiring Manager's tenant AND assigned job
    scope". A Hiring Manager's agent reaches the job they own and no other."""
    principal = _principal(Role.hiring_manager, permissions.AGENT_BODHA)
    mine = rbac.authorize_agent_action(
        principal, permissions.AGENT_BODHA, caps.EDIT_SWOT, _job(), granted=True
    )
    assert mine.decision is rbac.Decision.ALLOW

    somebody_elses = rbac.authorize_agent_action(
        principal,
        permissions.AGENT_BODHA,
        caps.EDIT_SWOT,
        _job(job_id=OTHER_JOB, assignments=frozenset()),
        granted=True,
    )
    assert somebody_elses.decision is rbac.Decision.DENY
    assert somebody_elses.reason == "not_assigned"


def test_an_agent_obeys_the_workflow_state_rules() -> None:
    """RBAC 34: agents must not bypass workflow state. The criteria freeze at
    finalization (22) applies to an agent exactly as to the human."""
    principal = _principal(Role.hiring_manager, permissions.AGENT_SUTRA)
    result = rbac.authorize_agent_action(
        principal,
        permissions.AGENT_SUTRA,
        caps.EDIT_MUST_HAVE_SKILLS,
        _job(state=JobLifecycleState.FINALIZED.value),
        granted=True,
    )
    assert result.decision is rbac.Decision.DENY
    assert result.reason == "criteria_frozen_after_finalization"


# ── An agent has no identity of its own ──────────────────────────────────────

def test_a_principal_cannot_be_built_for_an_agent_with_no_human() -> None:
    """RBAC 34's dual-attribution requirement, enforced in the constructor.

    Making this a ValueError rather than a nullable field is the whole
    mechanism: there is no way to represent an agent acting on nobody's
    authority, so no code path can reach one by forgetting an argument.
    """
    with pytest.raises(ValueError, match="human principal"):
        rbac.Principal(
            user_id=None, tenant_id=TENANT_A, role=Role.recruiter, agent="bodha"
        )


def test_an_agent_action_must_name_the_agent_it_was_authorized_as() -> None:
    """A principal carrying agent X cannot be used to authorize agent Y.

    Otherwise the narrowest declaration in the table would be irrelevant: any
    caller could borrow a permissive agent's principal for a restrictive
    agent's action.
    """
    principal = _principal(Role.hiring_manager, permissions.AGENT_BODHA)
    result = rbac.authorize_agent_action(
        principal, permissions.AGENT_SUTRA, caps.EDIT_MUST_HAVE_SKILLS, _job(), granted=True
    )
    assert result.decision is rbac.Decision.DENY
    assert result.reason == "agent_principal_mismatch"


def test_a_human_request_is_not_run_through_the_agent_gate() -> None:
    """A principal with no agent is a person acting directly, and putting
    their action through this gate would attribute it to a program."""
    human = rbac.Principal(
        user_id=HIRING_MANAGER, tenant_id=TENANT_A, role=Role.hiring_manager
    )
    assert human.is_agent_action is False
    result = rbac.authorize_agent_action(
        human, permissions.AGENT_BODHA, caps.EDIT_SWOT, _job(), granted=True
    )
    assert result.decision is rbac.Decision.DENY
    assert result.reason == "agent_principal_mismatch"


def test_an_unregistered_agent_holds_nothing() -> None:
    principal = rbac.Principal(
        user_id=HIRING_MANAGER,
        tenant_id=TENANT_A,
        role=Role.client,
        agent="something_new",
    )
    result = rbac.authorize_agent_action(
        principal, "something_new", caps.EDIT_SWOT, _job(), granted=True
    )
    assert result.decision is rbac.Decision.DENY
    assert result.reason == "agent_not_declared_for_capability"


# ── The tool grant and the capability declaration are different questions ────

def test_the_tool_grants_are_unchanged_and_still_enforced() -> None:
    """The pre-existing `AGENT_TOOLS` boundary is about what an agent READS.

    Both layers are needed and neither subsumes the other: a tool grant cannot
    express "on whose authority", and a capability declaration cannot express
    "which rows may this agent load". Asserted so a future simplification that
    deletes one has to argue for it.
    """
    assert permissions.is_granted(permissions.AGENT_SCORING, "extract_assessment")
    assert not permissions.is_granted(permissions.AGENT_SCORING, "extract_jd")
    assert not permissions.is_granted(permissions.AGENT_EMAIL, "extract_resume")


def test_the_named_agents_map_onto_runtime_surfaces_that_exist() -> None:
    """The six names are identity metadata over the runtime ids in
    `AGENT_TOOLS`. This asserts the mapping is not dangling, which is what
    would otherwise make the tool boundary silently empty for a named agent.
    """
    for name, agent in identity.AGENTS.items():
        assert agent.runtime_id in permissions.AGENT_TOOLS, name
        assert permissions.granted_tools(agent.runtime_id), name
