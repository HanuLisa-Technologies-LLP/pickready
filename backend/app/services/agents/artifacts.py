"""Typed, versioned artifacts: the only thing that crosses between two agents.

WHY AN ARTIFACT AND NOT A PROMPT STRING
----------------------------------------
The cheap way to pass Sutra's matrix to Vaada is to paste it into Vaada's
prompt. It works, and it quietly gives away three properties the product cannot
afford to lose.

  * It makes the boundary UNTYPED. A consumer handed prose cannot tell a matrix
    that was generated from one that degraded to a stub, so it renders both.
    This is the same failure shape as a tool that swallowed its error and handed
    its caller an empty list: the caller had no way to know, so it rendered it.
  * It makes the boundary UNVERSIONED. A report states grades against criteria,
    and spec 30 requires that it be reconstructible against the exact version of
    those criteria. A string carries no version.
  * It invites an agent to reach into another agent's INTERNALS. Once the
    contract is "text", the tempting next step is passing the transcript, then
    the memory handle, then the tool result -- and reach a future prompt does
    not have is reach a future prompt cannot start using (spec 16.4).

So an agent publishes a typed artifact, a consumer verifies it before reading
it, and neither one touches the other's state, memory or tools.

HIDDEN REASONING MUST NEVER CROSS THE BOUNDARY
------------------------------------------------
Spec 16.4 forbids passing an agent's internal deliberation to another agent. The
REAL guarantee is that nothing puts it there: every producer in this product
builds a typed payload from validated fields. `_forbidden_reasoning_keys` is
belt and braces on top of that, and it exists because the failure it prevents is
silent -- a scratchpad quoted into a downstream prompt becomes text a grade is
written from, and it reads exactly like evidence.

TENANT SCOPE IS CARRIED AND CHECKED, ARITHMETICALLY
-----------------------------------------------------
Spec 24.4: tenant isolation is never delegated to a model. An artifact carries
its tenant, `verify_for_consumer` compares it to the scope the consumer is
running in, and a mismatch is disqualifying. A prompt instruction saying "only
use data from this tenant" is a request; a string comparison is a boundary.

WHY THIS RETURNS A VERDICT RATHER THAN A BOOL
-----------------------------------------------
Because `verification.base.Verdict` is what `agent_loop` already consumes, and a
second accept/reject shape would mean the downstream check is the one verifier
whose rejection cannot be fed back to a retry.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.services.agents import identity, provenance
from app.services.verification import base as verification

__all__ = [
    "Artifact",
    "ArtifactContractError",
    "IncompleteContract",
    "publish",
    "require_contract_complete",
    "verify_for_consumer",
    "REQUIRED_PAYLOAD_FIELDS",
]

# ── Lifecycle ────────────────────────────────────────────────────────────────
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
#: Frozen. A locked matrix is what makes two reports on one job comparable, so
#: modifying one is not an edit, it is an escalation (see `escalation.py`).
STATUS_LOCKED = "locked"
STATUS_SUPERSEDED = "superseded"

READABLE_STATUSES: frozenset[str] = frozenset({STATUS_PUBLISHED, STATUS_LOCKED})

# ── Classification ───────────────────────────────────────────────────────────
CLASSIFICATION_INTERNAL = "internal"
CLASSIFICATION_CUSTOMER = "customer_visible"
CLASSIFICATION_CANDIDATE = "candidate_visible"

#: The fields a consumer is entitled to find. Checked at publish AND at consume:
#: at publish so a malformed artifact never lands, and at consume because an
#: artifact may have been written by an older build whose contract was thinner.
#: A consumer that only checked at publish would be trusting the version of the
#: code that wrote the row, which is precisely the version it cannot see.
REQUIRED_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "swot_evidence": ("strengths", "weaknesses", "opportunities", "threats", "sources"),
    "tatva_matrix": ("must_have", "nice_to_have", "behavioural"),
    "ai_score": ("categories",),
    "answer_event": ("question_key", "answer"),
    "scoring_state": ("item_grades",),
    "evidence_gap": ("competency", "missing_evidence"),
    "prism_report": ("ai_score", "ppi_assessment", "validation", "gap_analysis"),
}

#: Payload keys that would carry an agent's own deliberation across the
#: boundary. Matched on the key NAME rather than on the value, because the value
#: is prose and any content test over prose is a guess.
_REASONING_KEYS: frozenset[str] = frozenset(
    {
        "reasoning",
        "chain_of_thought",
        "chain_of_thoughts",
        "cot",
        "scratchpad",
        "scratch_pad",
        "internal_monologue",
        "deliberation",
        "thoughts",
        "thinking",
        "rationale_trace",
    }
)


class ArtifactContractError(ValueError):
    """A publish that violates the contract. REFUSED, never warned.

    A warning here would produce an artifact that exists, is malformed, and has
    a consumer already reading it -- which is strictly worse than not having it,
    because the consumer renders what it is handed.
    """


def _forbidden_reasoning_keys(payload: Any, path: str = "payload") -> list[str]:
    """Every place a deliberation-shaped key appears, at any depth."""
    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            here = f"{path}.{key}"
            if str(key).strip().casefold() in _REASONING_KEYS:
                found.append(here)
            found.extend(_forbidden_reasoning_keys(value, here))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(_forbidden_reasoning_keys(value, f"{path}[{index}]"))
    return found


def _declared_producer(artifact_type: str) -> str | None:
    """The one agent that declares it produces this type.

    One, because `identity.validate_identities` refuses two producers for one
    type: a consumer holding an artifact whose producer is ambiguous cannot know
    whose version of the contract it is reading.
    """
    for agent_id, agent in identity.AGENTS.items():
        if artifact_type in agent.produces:
            return agent_id
    return None


def _declared_consumers(artifact_type: str) -> tuple[str, ...]:
    return tuple(
        agent_id
        for agent_id, agent in identity.AGENTS.items()
        if artifact_type in agent.consumes
    )


@dataclass(frozen=True)
class Artifact:
    """One typed hand-off between two agents (spec 16.5 envelope).

    Frozen, and `immutable` is a field on top of that. The dataclass being
    frozen stops this process from editing it; `immutable` is the statement a
    PERSISTED artifact carries, so a reader loading a row from the database
    knows a new version is the only legal way to change it.
    """

    artifact_type: str
    version: int
    status: str
    producer: str
    consumers: tuple[str, ...]
    tenant_id: str
    payload: Mapping[str, Any]
    artifact_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    job_id: str | None = None
    candidate_id: str | None = None
    # ── the rest of specdoc4 15's contract fields ────────────────────────────
    #: The flow this artifact belongs to, issued once at job creation. Optional
    #: on the dataclass and mandatory on the live Part A path, which is what
    #: `require_contract_complete` enforces: a legacy publisher that predates
    #: the flow id must keep working while it is being deleted, and a new stage
    #: must not be able to publish without one.
    correlation_id: str | None = None
    #: The task and message this artifact answers. `task_id` joins it to the
    #: envelope's execution; `message_id` is the hand-off itself, so two
    #: republications of the same task are distinguishable.
    task_id: str | None = None
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    #: The HUMAN who authorised the run that produced this (RBAC 34). Carried on
    #: the artifact and not only in the audit row, because a consumer verifying
    #: an artifact should be able to answer "on whose authority" without a
    #: database round trip it may not be in a position to make.
    principal_user_id: str | None = None
    principal_role: str | None = None
    immutable: bool = True
    #: Where the content came from: chunk ids, a resume id, a JD version. Ids
    #: only, so provenance can be shown without re-disclosing the source text.
    source_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Whether the producer's own gate passed before publishing. INTERNAL
    #: engineering state; no number or flag here reaches a client.
    validated: bool = False
    provenance_complete: bool = False
    classification: str = CLASSIFICATION_INTERNAL
    #: Who may read it. Data, exactly like `AGENT_TOOLS`: never a role branch
    #: inside a consumer deciding for itself whether it is allowed.
    allowed_agents: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The envelope WITHOUT the payload.

        Deliberately payload-free so it is safe beside a trace. The payload is
        the content; everything here is an identifier, a count or a flag.
        """
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "version": self.version,
            "status": self.status,
            "producer": self.producer,
            "consumers": list(self.consumers),
            "immutable": self.immutable,
            "source_refs": list(self.source_refs),
            "created_at": self.created_at.isoformat(),
            "tenant_id": self.tenant_id,
            "job_id": self.job_id,
            "candidate_id": self.candidate_id,
            "correlation_id": self.correlation_id,
            "task_id": self.task_id,
            "message_id": self.message_id,
            "principal_user_id": self.principal_user_id,
            "principal_role": self.principal_role,
            "quality": {
                "validated": self.validated,
                "provenance_complete": self.provenance_complete,
            },
            "policy": {
                "classification": self.classification,
                "allowed_agents": list(self.allowed_agents),
            },
        }


def publish(
    *,
    producer: str,
    artifact_type: str,
    payload: Mapping[str, Any],
    tenant_id: str,
    version: int = 1,
    job_id: str | None = None,
    candidate_id: str | None = None,
    status: str = STATUS_PUBLISHED,
    source_refs: Iterable[str] = (),
    validated: bool = False,
    classification: str = CLASSIFICATION_INTERNAL,
    allowed_agents: Iterable[str] | None = None,
    correlation_id: str | None = None,
    task_id: str | None = None,
    principal: provenance.Principal | None = None,
) -> Artifact:
    """Build an artifact, refusing anything the identity table does not permit.

    Four refusals, and each one is a bug that would otherwise surface far away
    from its cause:

      * an unknown producer, because it would hold no declared contract at all;
      * a type the producer does not declare it produces, because a consumer
        verifying provenance would then reject an artifact that is genuine, or
        worse accept one that is not;
      * a missing required field, because the consumer discovers it mid-render;
      * a deliberation-shaped payload key, per the module docstring.

    `allowed_agents` defaults to the agents that DECLARE they consume this type.
    Defaulting to "everyone" would make the policy field decorative, and the
    declared consumer list is the narrowest set that still works -- the same
    posture as the tool grants.
    """
    agent = identity.get(producer)  # raises UnknownAgent, which is correct
    if artifact_type not in agent.produces:
        raise ArtifactContractError(
            f"{producer} does not declare it produces {artifact_type!r}; "
            f"it produces {list(agent.produces)}"
        )
    if int(version) < 1:
        raise ArtifactContractError(
            f"artifact version must be a positive integer, got {version!r}"
        )

    missing = [
        field_name
        for field_name in REQUIRED_PAYLOAD_FIELDS.get(artifact_type, ())
        if field_name not in payload
    ]
    if missing:
        raise ArtifactContractError(
            f"{artifact_type} is missing required payload fields: {missing}"
        )

    leaked = _forbidden_reasoning_keys(payload)
    if leaked:
        raise ArtifactContractError(
            "an artifact payload may not carry an agent's internal deliberation: "
            f"{leaked}"
        )

    if correlation_id is not None and not provenance.is_correlation_id(correlation_id):
        raise ArtifactContractError(
            f"{correlation_id!r} is not a correlation id issued by "
            "provenance.new_correlation_id. A job id or a workflow id in this "
            "slot reads correctly in a log line and joins the artifact to no flow."
        )
    if principal is not None and principal.tenant_id != str(tenant_id):
        raise ArtifactContractError(
            f"{producer} published into tenant {tenant_id!r} on behalf of a "
            f"principal in tenant {principal.tenant_id!r}"
        )

    consumers = _declared_consumers(artifact_type)
    permitted = tuple(allowed_agents) if allowed_agents is not None else consumers
    return Artifact(
        artifact_type=artifact_type,
        version=int(version),
        status=status,
        producer=producer,
        consumers=consumers,
        tenant_id=str(tenant_id),
        payload=dict(payload),
        job_id=str(job_id) if job_id else None,
        candidate_id=str(candidate_id) if candidate_id else None,
        source_refs=tuple(str(ref) for ref in source_refs),
        validated=bool(validated),
        # Provenance is complete when the artifact can say where it came from.
        # Derived rather than asserted by the caller: a producer marking its own
        # provenance complete while recording no refs is the exact shape of a
        # timestamp claiming work that did not happen.
        provenance_complete=bool(tuple(source_refs)),
        classification=classification,
        allowed_agents=permitted,
        correlation_id=correlation_id,
        task_id=task_id,
        principal_user_id=principal.user_id if principal else None,
        principal_role=principal.role if principal else None,
    )


def contract_fields(artifact: Artifact) -> dict[str, Any]:
    """The eight specdoc4 15 contract fields, read off one artifact.

    A view rather than eight more columns: `message`, `task` and `artifact` are
    the three identifiers the hand-off already carries under their own names,
    and duplicating them under the spec's names would create two fields that
    must agree. `context` and `provenance` are composites, so they are built
    here rather than stored flattened.
    """
    return {
        "message": artifact.message_id,
        "task": artifact.task_id,
        "artifact": artifact.artifact_id,
        "status": artifact.status,
        "context": {
            "tenant_id": artifact.tenant_id,
            "job_id": artifact.job_id,
            "candidate_id": artifact.candidate_id,
        },
        "provenance": {
            "producer": artifact.producer,
            "source_refs": list(artifact.source_refs),
            "principal_user_id": artifact.principal_user_id,
            "principal_role": artifact.principal_role,
        },
        "version": artifact.version,
        "correlation_id": artifact.correlation_id,
    }


class IncompleteContract(ArtifactContractError):
    """An artifact published on the live Part A path without its full contract.

    A DIFFERENT error class from a malformed payload, and deliberately so. A
    missing payload field means the artifact cannot be read; a missing
    correlation id or principal means it can be read perfectly well and cannot
    be TRACED or ATTRIBUTED, which is a governance failure rather than a data
    one. Two error classes because the two get fixed by different people.
    """


def require_contract_complete(artifact: Artifact) -> Artifact:
    """Refuse an artifact that does not carry all eight A2A contract fields.

    THIS IS THE RAISING CHECK, and `verify_for_consumer` is not.

    The split is not squeamishness. `verify_for_consumer` answers one question,
    "may this consumer read this artifact", and the honest answer to that
    question is not changed by an absent correlation id: the content is in
    scope, from the declared producer, and safe to read. Recording the gap as a
    low finding there and refusing here puts each check where its failure is
    actionable -- the consumer cannot fix a producer's missing provenance, and
    the producer can.

    Called by `orchestration.enforcement.run_stage` for every Part A stage, so
    a new stage physically cannot publish an untraceable artifact, while the
    legacy publishers being deleted keep working until they are gone.
    """
    gaps = provenance.contract_gaps(contract_fields(artifact))
    # `context` and `provenance` are composites and are never empty dicts, so a
    # gap in either is really a gap in one of their parts. Named precisely,
    # because "context is missing" sends a reader to the wrong place.
    if artifact.principal_user_id is None:
        gaps.append("provenance.principal_user_id")
    if not artifact.source_refs:
        gaps.append("provenance.source_refs")
    if gaps:
        raise IncompleteContract(
            f"{artifact.producer} published {artifact.artifact_type!r} without "
            f"the A2A contract fields {sorted(set(gaps))}. Every stage writes "
            "provenance (spec-doc6 4.1); an artifact nobody can trace to a flow "
            "or attribute to a human is not publishable."
        )
    return artifact


def verify_for_consumer(
    artifact: Artifact,
    consumer_id: str,
    *,
    tenant_id: str | None = None,
    job_id: str | None = None,
) -> verification.Verdict:
    """Spec 37: what a consumer checks BEFORE it reads an artifact.

    Ordering matters and is deliberate. Scope and permission are evaluated from
    the ENVELOPE, and this function never touches `artifact.payload` -- the same
    rule the tool layer follows, where a refusal that ran the handler first has
    already read the row it was refusing to show.

    Returned as a `Verdict` so a failure feeds `agent_loop` verbatim like every
    other verifier. Confidence is base.py's severity arithmetic; nothing here
    asks a model what it thinks of itself.
    """
    findings: list[verification.Finding] = []

    if not artifact.version or int(artifact.version) < 1:
        findings.append(
            verification.high(
                "missing_artifact_version",
                f"{artifact.artifact_type}.version",
                "the artifact carries no positive version",
                "Republish the artifact with an explicit version before consuming it.",
            )
        )

    expected_producer = _declared_producer(artifact.artifact_type)
    if expected_producer is None:
        findings.append(
            verification.high(
                "unknown_artifact_type",
                artifact.artifact_type,
                "no agent declares it produces this artifact type",
                "Reject the artifact and route the task to a declared producer.",
            )
        )
    elif artifact.producer != expected_producer:
        findings.append(
            verification.high(
                "producer_identity_mismatch",
                f"{artifact.artifact_type}.producer",
                f"produced by {artifact.producer!r}, declared producer is {expected_producer!r}",
                "Reject the artifact; consume only the declared producer's output.",
            )
        )

    for field_name in REQUIRED_PAYLOAD_FIELDS.get(artifact.artifact_type, ()):
        if field_name not in artifact.payload:
            findings.append(
                verification.high(
                    "missing_required_field",
                    f"{artifact.artifact_type}.{field_name}",
                    "the contract requires this field and the artifact omits it",
                    f"Regenerate the artifact with {field_name} populated.",
                )
            )

    # Scope. Never inferred, never asked of a model (spec 24.4): two strings.
    if tenant_id is not None and str(tenant_id) != artifact.tenant_id:
        findings.append(
            verification.high(
                "tenant_scope_mismatch",
                f"{artifact.artifact_type}.tenant_id",
                "the artifact belongs to a different tenant than the consuming run",
                "Discard the artifact; load one produced inside this tenant.",
            )
        )
    if job_id is not None and artifact.job_id is not None and str(job_id) != artifact.job_id:
        findings.append(
            verification.high(
                "job_scope_mismatch",
                f"{artifact.artifact_type}.job_id",
                "the artifact belongs to a different job than the consuming run",
                "Discard the artifact; load the one produced for this job.",
            )
        )

    # Permission. A consumer outside `allowed_agents` is HIGH, not a warning:
    # the reason artifacts exist is that an agent cannot reach into another
    # agent's state, and a permission check that only logs is not a boundary.
    if consumer_id not in artifact.allowed_agents:
        findings.append(
            verification.high(
                "consumer_not_permitted",
                f"{artifact.artifact_type}.allowed_agents",
                f"{consumer_id!r} is not in the artifact's allowed consumers",
                "Refuse the read and escalate; do not widen the allowed set at runtime.",
            )
        )
    elif artifact.artifact_type not in identity.get(consumer_id).consumes:
        findings.append(
            verification.medium(
                "undeclared_consumption",
                f"{consumer_id}.consumes",
                "the consumer is permitted but does not declare it consumes this type",
                "Declare the artifact type in the agent identity table or stop reading it.",
            )
        )

    if artifact.status not in READABLE_STATUSES:
        findings.append(
            verification.high(
                "artifact_not_published",
                f"{artifact.artifact_type}.status",
                f"status is {artifact.status!r}, which is not readable downstream",
                "Wait for the producer to publish, or consume the previous version.",
            )
        )
    if not artifact.validated:
        findings.append(
            verification.medium(
                "artifact_not_validated",
                f"{artifact.artifact_type}.quality.validated",
                "the producer's own quality gate did not pass before publishing",
                "Run the producer's gate and republish before this artifact is consumed.",
            )
        )
    if not artifact.provenance_complete:
        findings.append(
            verification.low(
                "provenance_incomplete",
                f"{artifact.artifact_type}.source_refs",
                "the artifact records no source references",
                "Record the source ids the artifact was built from.",
            )
        )
    # LOW, and `require_contract_complete` raises on the same two conditions.
    # See that function's docstring: a consumer cannot repair a producer's
    # missing provenance, and refusing the read would punish the wrong side of
    # the boundary for a defect that does not make the content unsafe.
    if not artifact.correlation_id:
        findings.append(
            verification.low(
                "correlation_id_absent",
                f"{artifact.artifact_type}.correlation_id",
                "the artifact belongs to no traceable flow",
                "Publish it with the correlation id issued at job creation.",
            )
        )
    if not artifact.principal_user_id:
        findings.append(
            verification.low(
                "principal_not_attributed",
                f"{artifact.artifact_type}.principal_user_id",
                "the artifact records no human principal for the run that made it",
                "Publish it with the principal whose authority the agent acted under.",
            )
        )

    return verification.verdict(f"a2a:{artifact.artifact_type}", findings)
