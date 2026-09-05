"""The execution envelope every agent run carries (spec 5.1).

WHY AN ENVELOPE AND NOT A HANDFUL OF KEYWORD ARGUMENTS
------------------------------------------------------
A finalised PRISM report is a permanent record that states grades against
criteria, and spec 30 asks a harder question of it than "is it correct": it asks
that it be RECONSTRUCTIBLE. Six months later somebody disputes a grade, and the
only defensible answer is to rebuild the run from the immutable versions it was
written from -- that matrix version, that prompt version, that policy version,
that plan. None of that survives if the versions live as loose arguments passed
down four call frames, because the first caller that forgets one produces a run
nobody can reproduce and nothing notices, exactly the way a `framework_generated_at`
stamp with no rows behind it went unnoticed for weeks.

So the versions travel as ONE value, they are set once at the top of a run, and
the envelope is frozen. A stage that wants a different context version has to
make a new envelope, which is a visible act.

WHAT IS GENERATED AND WHAT IS NEVER INVENTED
---------------------------------------------
`execution_id` is minted per run (uuid4) and `workflow_id` is minted once per
pipeline. `tenant_id`, `job_id`, `candidate_id` and `assessment_id` come from the
CALLER and are never defaulted or invented: a scope this module guessed at is a
cross-tenant read waiting to happen, and spec 24.4 is explicit that tenant
isolation is never delegated to a model or to a convenience default.

SERIALISING IT DROPS NOTHING, BECAUSE THERE IS NOTHING TO DROP
---------------------------------------------------------------
`as_dict` is safe to hand to `observability.trace` in full. Every field here is
an identifier, a version string or an integer ceiling. No JD text, no answer, no
remark, no prompt. That is a property of the SHAPE rather than of the caller's
discipline, which is the same reason `_SAFE_STAGE_KEYS` is an allowlist: the
next person adding "the prompt we sent" for debugging has to add a field to a
frozen dataclass whose docstring says it carries no content.

THE BUDGET FIELDS ARE DERIVED, NOT RESTATED
--------------------------------------------
`reliability.budget` and `agent_loop` already own the ceilings. Retyping 10 and 3
here would create a second set of limits that silently stops agreeing with the
first, and the disagreement would surface as a task that overruns a ceiling
somebody believed they had lowered. `RunBudget.for_task` reads them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from app.services import agent_loop
from app.services.agents import provenance
from app.services.reliability import budget as budgeting

__all__ = [
    "RunBudget",
    "Envelope",
    "MissingCorrelationId",
    "new_workflow_id",
    "Principal",
]


class MissingCorrelationId(ValueError):
    """A stage that cannot be joined to the flow it belongs to.

    A `ValueError` and not a `PermissionError`, unlike `MissingPrincipal`: this
    is a traceability defect rather than an authorization one, and the two get
    fixed by different people.
    """

#: Re-exported so a stage builds its envelope and its principal from one import.
#: An alias rather than a second class: two `Principal` types would eventually
#: disagree about whether a blank user id is allowed, and one of them would be
#: the one the audit writer trusts.
Principal = provenance.Principal


def _uuid_hex() -> str:
    return uuid.uuid4().hex


def new_workflow_id() -> str:
    """One id for a whole pipeline run, minted by whoever starts it.

    Bodha through Siddhi is one workflow even though it is six agents and
    several background tasks. Without a shared id, "what happened to this
    candidate's assessment" is six unrelated trace queries.
    """
    return _uuid_hex()


@dataclass(frozen=True)
class RunBudget:
    """The four ceilings spec 5.1 puts on one agent execution.

    Every value is DERIVED from the modules that already own it. See the module
    docstring: a second copy of a limit is a limit that eventually disagrees.
    """

    max_steps: int
    max_tool_calls: int
    max_retries: int
    max_latency_ms: int

    @classmethod
    def for_task(cls, task_type: str, *, interactive: bool = True) -> RunBudget:
        """Ceilings for one execution of `task_type`.

        `interactive` is the same split `agent_loop` draws and for the same
        reason: a candidate is watching a text box, so the wall clock that
        matters is the one a request handler is blocked for. A background report
        may take four minutes; the conversation turn that keeps someone waiting
        may not.
        """
        attempts = (
            agent_loop.INTERACTIVE_ATTEMPTS if interactive else agent_loop.BACKGROUND_ATTEMPTS
        )
        deadline = (
            agent_loop.INTERACTIVE_DEADLINE if interactive else agent_loop.BACKGROUND_DEADLINE
        )
        steps = budgeting.MAX_ITERATIONS
        return cls(
            max_steps=steps,
            # A tool call per step per attempt, which is the honest worst case
            # rather than a round number: a step that retries re-reaches for its
            # data, and a ceiling that ignored the retry would be reached by a
            # healthy run on its second attempt.
            max_tool_calls=steps * attempts,
            max_retries=budgeting.MAX_REPLANS,
            max_latency_ms=int(deadline * 1000),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_retries": self.max_retries,
            "max_latency_ms": self.max_latency_ms,
        }


#: What a version field reads when the caller pinned nothing. A literal rather
#: than None so a persisted envelope always answers "which version" with a
#: string somebody can search for, instead of a null that reads as "we did not
#: record it" and "there was no version" at the same time.
UNVERSIONED = "unpinned"


@dataclass(frozen=True)
class Envelope:
    """Everything one agent execution needs to be identified and reproduced.

    Frozen. A stage that mutated `prompt_version` halfway through a run would
    produce a trace claiming a version the first half was not written against,
    and the reconstruction in spec 30 would rebuild the wrong thing.
    """

    # ── scope: supplied by the caller, never invented ────────────────────────
    tenant_id: str
    agent_id: str
    job_id: str | None = None
    candidate_id: str | None = None
    assessment_id: str | None = None

    # ── who authorised this run (RBAC 34) ────────────────────────────────────
    #: The HUMAN the agent is acting for. Optional on the dataclass and NOT
    #: optional on the live Part A path: `require_principal` raises, and
    #: `orchestration.enforcement.run_stage` calls it before any handler runs.
    #:
    #: It is optional here only because the legacy publishers in `ppi`,
    #: `matching` and `swot_intake` build envelopes today and are being deleted
    #: rather than migrated. `tests/test_no_silent_degradation.py` ratchets that
    #: set so it can shrink and cannot grow.
    principal: provenance.Principal | None = None

    # ── run identity ─────────────────────────────────────────────────────────
    #: One id for the WHOLE flow, issued at job creation (spec-doc6 4.1). It is
    #: distinct from `workflow_id`, which identifies one pipeline execution: a
    #: rescore is a second workflow inside the same flow, and collapsing the two
    #: would make "everything that happened to this candidate" unanswerable the
    #: first time anything is re-run.
    #:
    #: NEVER DEFAULTED, for the same reason `tenant_id` is not. A minted-on-
    #: demand correlation id would give every stage a flow of its own, and six
    #: stages with six flows is precisely the state this field exists to end --
    #: while looking, in every log line, exactly like a flow that was traced.
    correlation_id: str | None = None
    workflow_id: str = field(default_factory=new_workflow_id)
    task_id: str = field(default_factory=_uuid_hex)
    #: Set only on a sub-task. A root task has none, and that absence is how a
    #: trace reader finds the root without a separate flag that can disagree.
    parent_task_id: str | None = None
    execution_id: str = field(default_factory=_uuid_hex)

    # ── the immutable versions the run is reproducible against (spec 30) ─────
    context_version: str = UNVERSIONED
    agent_version: str = UNVERSIONED
    prompt_version: str = UNVERSIONED
    policy_version: str = UNVERSIONED
    plan_version: str = UNVERSIONED
    #: Which snapshot of experience memory was in scope. Recorded because a
    #: learning is a HINT prepended to a prompt, so two runs with identical
    #: prompt versions and different memory snapshots are not the same run.
    memory_snapshot_id: str | None = None

    # ── bounds ───────────────────────────────────────────────────────────────
    deadline: datetime | None = None
    budget: RunBudget = field(default_factory=lambda: RunBudget.for_task("interviewer"))

    @classmethod
    def for_run(
        cls,
        *,
        tenant_id: str,
        agent_id: str,
        task_type: str,
        interactive: bool = True,
        job_id: str | None = None,
        candidate_id: str | None = None,
        assessment_id: str | None = None,
        principal: provenance.Principal | None = None,
        correlation_id: str | None = None,
        workflow_id: str | None = None,
        parent_task_id: str | None = None,
        context_version: str = UNVERSIONED,
        agent_version: str = UNVERSIONED,
        prompt_version: str = UNVERSIONED,
        policy_version: str = UNVERSIONED,
        plan_version: str = UNVERSIONED,
        memory_snapshot_id: str | None = None,
        now: datetime | None = None,
    ) -> Envelope:
        """Build an envelope with the deadline and budget derived from the task.

        `tenant_id` and `agent_id` are keyword-only and have no default, which
        is the point: a signature that let them be omitted would let a caller
        produce a scopeless envelope, and a scopeless envelope is one that
        passes every scope check in `artifacts.verify_for_consumer` by having
        nothing to compare.
        """
        budget = RunBudget.for_task(task_type, interactive=interactive)
        started = now or datetime.now(timezone.utc)
        return cls(
            tenant_id=str(tenant_id),
            agent_id=agent_id,
            job_id=str(job_id) if job_id else None,
            candidate_id=str(candidate_id) if candidate_id else None,
            assessment_id=str(assessment_id) if assessment_id else None,
            principal=principal,
            correlation_id=correlation_id,
            workflow_id=workflow_id or new_workflow_id(),
            parent_task_id=parent_task_id,
            context_version=context_version,
            agent_version=agent_version,
            prompt_version=prompt_version,
            policy_version=policy_version,
            plan_version=plan_version,
            memory_snapshot_id=memory_snapshot_id,
            deadline=started + timedelta(milliseconds=budget.max_latency_ms),
            budget=budget,
        )

    def child(self, agent_id: str, task_id: str | None = None) -> Envelope:
        """A sub-task's envelope: same workflow, same scope, new task id.

        `parent_task_id` is THIS task, so a trace can rebuild the tree. The
        scope and the versions are copied rather than re-supplied, because a
        sub-task that could be handed a different tenant is the cross-tenant bug
        this envelope exists to make impossible to write by accident.

        A fresh `execution_id` is minted: the sub-task is a distinct execution
        even though it belongs to the same workflow, and reusing the parent's id
        would collapse two rows in the trace table into one.

        `correlation_id` and `principal` are COPIED for the same reason the
        scope is. Re-minting the correlation id would give a sub-task a flow of
        its own, and the flow is the thing spec-doc6 4.1 asks to be traceable
        end to end; dropping the principal would produce a sub-task that acted
        on nobody's authority while its parent was properly attributed, which is
        the shape of an unauthorised write that passes review.
        """
        return replace(
            self,
            agent_id=agent_id,
            task_id=task_id or _uuid_hex(),
            parent_task_id=self.task_id,
            execution_id=_uuid_hex(),
        )

    def require_correlation_id(self) -> str:
        """The flow this run belongs to, or a refusal.

        Rejects a malformed value as hard as a missing one. The failure that
        catches is a caller threading a raw job id or a workflow id into the
        slot: it is hex, it reads correctly in a log line, and it joins the
        stage to no audit row at all -- which is worse than an empty column,
        because an empty column is visibly empty.
        """
        if not provenance.is_correlation_id(self.correlation_id):
            raise MissingCorrelationId(
                f"agent {self.agent_id!r} has no usable correlation id "
                f"({self.correlation_id!r}). One id is issued at job creation "
                "and carried through Bodha, Sutra, Yukti, Vaada, Miti and "
                "Siddhi; a stage that mints its own opens a second flow."
            )
        return self.correlation_id  # type: ignore[return-value]

    def require_principal(self) -> provenance.Principal:
        """The human this run acts for, or a refusal.

        RBAC 34 is not satisfiable after the fact: an audit row written without
        the human principal cannot be repaired later, because by then nobody
        knows who it was. So this raises BEFORE the stage handler runs, which is
        the same ordering the tool layer uses -- a refusal that ran the handler
        first has already read the row it was refusing to show.

        The tenant is checked against the envelope's own scope as well. An
        envelope whose principal belongs to a different tenant is not a
        bookkeeping mismatch, it is a cross-tenant action with a plausible
        audit row attached.
        """
        if self.principal is None:
            raise provenance.MissingPrincipal(
                f"agent {self.agent_id!r} has no human principal; RBAC 34 "
                "requires every AI-initiated action to be attributable to both "
                "the human who authorised it and the agent that executed it"
            )
        if self.principal.tenant_id != self.tenant_id:
            raise provenance.MissingPrincipal(
                f"agent {self.agent_id!r} runs in tenant {self.tenant_id!r} on "
                f"behalf of a principal in tenant {self.principal.tenant_id!r}"
            )
        return self.principal

    def expired(self, now: datetime | None = None) -> bool:
        """Whether the deadline has passed. No deadline means never expired."""
        if self.deadline is None:
            return False
        return (now or datetime.now(timezone.utc)) >= self.deadline

    def as_dict(self) -> dict[str, object]:
        """The whole envelope, safe to persist in a trace as it stands.

        Nothing is filtered on the way out, and nothing needs to be: see the
        module docstring. If this ever needs an allowlist, the field that made
        it necessary is the one that should not have been added.
        """
        return {
            "tenant_id": self.tenant_id,
            "job_id": self.job_id,
            "candidate_id": self.candidate_id,
            "assessment_id": self.assessment_id,
            "agent_id": self.agent_id,
            "principal_user_id": self.principal.user_id if self.principal else None,
            "principal_role": self.principal.role if self.principal else None,
            "correlation_id": self.correlation_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "execution_id": self.execution_id,
            "context_version": self.context_version,
            "agent_version": self.agent_version,
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
            "plan_version": self.plan_version,
            "memory_snapshot_id": self.memory_snapshot_id,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "budget": self.budget.as_dict(),
        }
