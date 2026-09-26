"""The policy engine: may THIS agent, acting for THIS tenant, in THIS workflow
stage, call THIS tool on THIS object.

WHAT CHANGED, AND WHY THE OLD ANSWER WAS TOO SMALL
---------------------------------------------------
`permissions.AGENT_TOOLS` answers one question: may this agent call this tool.
That is a real boundary and it stays exactly as it was. It is not the whole
question, because a tool grant says nothing about WHOSE data the call is about.
An agent decides at runtime which tool to reach for, partly from text a
candidate wrote, and the tool name it chooses is only half of what it names.
The other half is the object.

So the decision sequence is:

    LLM proposes a tool call
      -> agent capability   (permissions.AGENT_TOOLS, unchanged)
      -> tenant scope       (the acting tenant)
      -> object scope       (every object the call names)
      -> workflow stage     (TOOL_STAGES)
      -> risk class         (RiskClass, on the ToolSpec)
      -> ALLOW | DENY | REQUIRE_APPROVAL
      -> handler

The LLM appears in the first line and nowhere else. Every step after it is
deterministic code reading declared data, and `executor.execute` runs the whole
sequence BEFORE it parses the payload, let alone invokes the handler. A refusal
that ran the handler first has already read the row it was refusing to show.

WHY THE ORDER IS WHAT IT IS
---------------------------
Capability is first because it consults no object at all, so it cannot leak the
existence of one: an agent that does not hold `extract_resume` is refused
identically whether the profile named exists, belongs to somebody else, or was
invented by an injected instruction. Tenant and object scope come immediately
after, before the stage and risk rules, because those two refuse for reasons
that are properties of the TOOL rather than of the object, and running them
first would let a caller tell "this agent cannot do that here" apart from "that
object is not yours". `rbac.decide` orders its own chain on exactly this
argument.

A CROSS-TENANT REFUSAL IS A 404 AND CARRIES NOTHING
----------------------------------------------------
RBAC 4 forbids a user of one client from accessing, INFERRING, modifying or
retrieving another client's resources, and inference is why the answer is 404
rather than 403. The agent surface answers the same way the HTTP surface does,
because an agent that could distinguish "forbidden" from "absent" is a
cross-tenant existence oracle with a prompt in front of it.

WHAT THIS LAYER IS, AND WHAT IT IS NOT
---------------------------------------
It is the DECLARED-OBJECT gate. An object enters the decision only because the
caller named it in a `ToolContext`, which is the same shape `rbac.Resource`
uses: the facts a decision needs, already loaded, so the rules stay pure and
testable without a database. A caller that declares no objects is not evading
the check, it is making a call the check has nothing to say about, and for
those the RLS-aware session remains the real boundary (claude.md rule 1: app
level filtering is defence in depth, never a substitute). Deriving the object's
tenant here instead would mean reading the row, which is precisely the read the
refusal exists to prevent.

There is deliberately no per-tool declaration of which object KINDS it accepts.
A tool's input model already names that (`JobRef`, `ProfileRef`, `LinkRef`), and
a second declaration of the same fact is a second thing to keep in step.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.services.tools import permissions


class RiskClass(str, Enum):
    """What a tool can do to the world, and therefore how much human it needs.

    A PYTHON CONSTANT declared on the `ToolSpec`, never a database row. The
    argument is the Layer 1 argument `hiring/department_models.py` already
    makes: a table has an UPDATE, an UPDATE eventually gets an admin screen,
    and an admin screen makes tool permissions client editable, at which point
    the policy is decorative. The one thing that IS data is a tenant asking for
    MORE approval than the baseline (`services/tools/approvals.py`), which can
    only ever narrow.
    """

    #: A bounded read. Runs automatically.
    READ = "read"
    #: Changes a row inside the platform. Runs automatically and is ledgered,
    #: because a write nobody can reconstruct afterwards is a write nobody can
    #: audit.
    WRITE_INTERNAL = "write_internal"
    #: Irreversible and visible outside the platform: an email, an SMS,
    #: anything a candidate sees. Never issued speculatively -- the executor
    #: refuses to invoke the handler without a human approval in hand, which is
    #: the adapter gate. Durable execution does not make an outbound email
    #: safe; replaying a checkpoint sends it twice.
    WRITE_EXTERNAL = "write_external"
    #: Changes what the platform is allowed to do. Mandatory human, always.
    POLICY_CHANGE = "policy_change"


#: Classes a human must approve before the handler runs. The baseline, which a
#: tenant may extend and may never shrink.
REQUIRES_HUMAN_APPROVAL: frozenset[RiskClass] = frozenset(
    {RiskClass.WRITE_EXTERNAL, RiskClass.POLICY_CHANGE}
)

#: Classes whose every call is recorded as an audited event rather than only
#: counted. A read is counted (`telemetry`); anything that changes something is
#: named, with its tenant, its objects and its approver.
LEDGERED: frozenset[RiskClass] = frozenset(
    {RiskClass.WRITE_INTERNAL, RiskClass.WRITE_EXTERNAL, RiskClass.POLICY_CHANGE}
)

#: How long a human approval stays usable. An approval is permission for THIS
#: call, and one held open across a session would let a queued or replayed
#: invocation reuse a click the person made about something else. Two minutes
#: is longer than any tool's own deadline and shorter than a recruiter's
#: attention on one screen.
APPROVAL_MAX_AGE_SECONDS = 120


# -- Workflow stages ---------------------------------------------------------
#
# The stages of `docs/spec/HIRING_WORKFLOW.md` at the altitude a tool call
# happens in. Deliberately coarser than `hiring_pipeline`: a pipeline stage is a
# property of one candidate's application, and a tool call is made while the
# product is doing one KIND of work, which is what decides whether reaching for
# a transcript makes any sense.

STAGE_JOB_SETUP = "job_setup"
STAGE_SOURCING = "sourcing"
STAGE_ASSESSMENT = "assessment"
STAGE_REPORTING = "reporting"
STAGE_CORRESPONDENCE = "correspondence"

STAGES: tuple[str, ...] = (
    STAGE_JOB_SETUP,
    STAGE_SOURCING,
    STAGE_ASSESSMENT,
    STAGE_REPORTING,
    STAGE_CORRESPONDENCE,
)

_ALL_STAGES = frozenset(STAGES)

#: Which stages each tool is available in. DENY BY DEFAULT: a tool missing from
#: this table is refused in every declared stage, and
#: `tests/test_tool_firewall.py` fails the build when a registered tool has no
#: entry. The alternative default, "unlisted means everywhere", is how a table
#: like this stops meaning anything.
#:
#: Read the narrow rows. `extract_assessment` is absent from job setup and
#: sourcing because there is no transcript yet, so a call for one in those
#: stages is either a wiring defect or an injected instruction, and both want to
#: be loud. `extract_jd` is everywhere because every stage is about a job.
TOOL_STAGES: dict[str, frozenset[str]] = {
    "extract_jd": _ALL_STAGES,
    "extract_resume": frozenset({STAGE_SOURCING, STAGE_ASSESSMENT, STAGE_REPORTING}),
    "extract_assessment": frozenset({STAGE_ASSESSMENT, STAGE_REPORTING}),
    "extract_framework": frozenset(
        {STAGE_JOB_SETUP, STAGE_ASSESSMENT, STAGE_REPORTING}
    ),
    "retrieve_context": frozenset(
        {STAGE_JOB_SETUP, STAGE_SOURCING, STAGE_ASSESSMENT, STAGE_REPORTING}
    ),
    # Self-validation is part of every generative task, in every stage.
    "validate_output": _ALL_STAGES,
    # Question writing only. Project evidence informs what Vaada ASKS; it is
    # never read while grading or reporting, because a scorer that could read
    # it would grade the candidate's projects rather than their answers.
    "extract_project_evidence": frozenset({STAGE_ASSESSMENT}),
}


class PolicyDecision(str, Enum):
    """The three answers, and nothing in between."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ToolObject:
    """One thing a tool call is about, with the tenant that owns it.

    `tenant_id` is None for a row that is genuinely not tenant-owned. A
    candidate is the case that matters: a candidate spans tenants by design and
    carries a NULL tenant when they arrived through the databank, so treating
    NULL as a mismatch would refuse an agent access to the very person it was
    invoked for. Same rule `candidate_updates`' RLS policy states in SQL.
    """

    kind: str
    object_id: uuid.UUID | str
    tenant_id: uuid.UUID | str | None = None


@dataclass(frozen=True)
class ToolApproval:
    """A human said yes to this specific call.

    Scoped to the tool by name and stamped with when it was given, so it cannot
    be reused for a different tool or replayed later. `approved_by` is a HUMAN
    user id: an agent holds no authority of its own (RBAC 34) and cannot
    approve its own irreversible act, which is why there is no constructor here
    that takes an agent name.
    """

    approved_by: uuid.UUID | str
    tool: str
    granted_at: datetime

    def is_current(self, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        granted = self.granted_at
        if granted.tzinfo is None:
            granted = granted.replace(tzinfo=timezone.utc)
        age = (moment - granted).total_seconds()
        return 0 <= age <= APPROVAL_MAX_AGE_SECONDS


@dataclass(frozen=True)
class ToolContext:
    """On whose behalf, about what, and while doing which kind of work.

    Every field is optional in the type and none of them is optional in
    meaning. `UNSCOPED` below is the value a caller gets when it names nothing,
    and what that buys is nothing: it declares no objects, so it can reach none
    through this layer, and the handler still reads through the RLS-aware
    session that is the actual boundary.
    """

    #: The tenant the agent is acting for. None only for platform-level work
    #: that belongs to no customer.
    tenant_id: uuid.UUID | str | None = None
    #: The human whose authority the agent borrows (RBAC 34). Recorded on every
    #: ledgered call.
    principal_user_id: uuid.UUID | str | None = None
    #: One of `STAGES`. None means the caller declared no stage, and the stage
    #: rule then has nothing to compare against.
    stage: str | None = None
    #: Every object this call is about.
    objects: tuple[ToolObject, ...] = ()
    #: A human's approval, for a call whose risk class requires one.
    approval: ToolApproval | None = None
    #: Risk classes this tenant requires approval for beyond the baseline.
    #: Filled in by the executor from `approvals.required_risk_classes`, which
    #: reads rows the tenant wrote; it is a field on the context rather than an
    #: argument to `evaluate` so the whole decision has one input object. It can
    #: only ever ADD to `REQUIRES_HUMAN_APPROVAL`; there is no representation
    #: for removing one, and that absence is the enforcement.
    tenant_approval_required: frozenset[str] = field(default_factory=frozenset)


#: What a caller that names nothing gets. A module singleton rather than a None
#: default with a branch behind it, because two code paths through one decision
#: is how the second one stops being checked.
UNSCOPED = ToolContext()


@dataclass(frozen=True)
class PolicyVerdict:
    """The decision, plus what an audit row needs about how it was reached.

    `reason` names the rule rather than describing it, so a log line and a test
    assertion quote the same token. Same shape as `rbac.Authorization`, and for
    the same reason: a refusal nobody can attribute to a rule is a refusal
    nobody can argue with.
    """

    decision: PolicyDecision
    reason: str
    risk: RiskClass
    #: 404 for a cross-tenant refusal, 403 for every other denial, 200 when
    #: allowed. Carried here so the shape of the refusal is decided by the rule
    #: that made it rather than by whichever caller renders it.
    http_status: int = 200
    #: True when this call must be recorded as an audited event, not merely
    #: counted.
    ledgered: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.ALLOW


def _same_tenant(left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


def evaluate(
    *,
    tool: str,
    agent: str,
    risk: RiskClass,
    context: ToolContext = UNSCOPED,
    now: datetime | None = None,
) -> PolicyVerdict:
    """Decide one proposed tool call. Pure: no I/O, no clock beyond `now`.

    Pure for the reason `rbac.decide` is pure: the caller does the one database
    read (the tenant's approval rules) and hands the answer in, so the rules
    themselves can be exercised exhaustively without a database and cannot
    behave differently in a test than in production.
    """
    ledgered = risk in LEDGERED

    def refuse(reason: str, status: int) -> PolicyVerdict:
        return PolicyVerdict(
            PolicyDecision.DENY, reason, risk, http_status=status, ledgered=ledgered
        )

    # 1. Agent capability. Consults no object, so it can leak none.
    if not permissions.is_granted(agent, tool):
        return refuse("agent_does_not_hold_tool", 403)

    # 2 and 3. Tenant and object scope. One loop, because an object's tenant is
    #    the only thing that makes it another tenant's object.
    for obj in context.objects:
        if obj.tenant_id is None:
            # Not tenant-owned. See ToolObject.
            continue
        if not _same_tenant(context.tenant_id, obj.tenant_id):
            # 404, and the reason carries the object KIND and nothing else. An
            # id in this string would confirm the id exists.
            return refuse(f"cross_tenant_object:{obj.kind}", 404)

    # 4. Workflow stage. Only when the caller declared one; a tool absent from
    #    the table is refused in every stage.
    if context.stage is not None:
        if context.stage not in _ALL_STAGES:
            return refuse("unknown_workflow_stage", 403)
        if context.stage not in TOOL_STAGES.get(tool, frozenset()):
            return refuse("tool_not_available_in_stage", 403)

    # 5. Risk class.
    needs_approval = risk in REQUIRES_HUMAN_APPROVAL or (
        risk.value in context.tenant_approval_required
    )
    if needs_approval:
        approval = context.approval
        if approval is None:
            return PolicyVerdict(
                PolicyDecision.REQUIRE_APPROVAL,
                "human_approval_required",
                risk,
                http_status=403,
                ledgered=ledgered,
            )
        if approval.tool != tool:
            return refuse("approval_is_for_another_tool", 403)
        if not approval.is_current(now=now):
            return refuse("approval_expired", 403)

    return PolicyVerdict(
        PolicyDecision.ALLOW, "allowed", risk, http_status=200, ledgered=ledgered
    )
