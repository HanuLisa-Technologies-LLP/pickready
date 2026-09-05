"""The correlation id, the human principal, and the A2A contract fields.

WHAT THIS MODULE EXISTS TO PREVENT
-----------------------------------
Two failures that this codebase has already paid for once each, and one the
specification names before it has happened.

  * A stage that ran and left nothing behind. `framework_generated_at` was set
    on 19 of 35 live jobs with zero competency rows underneath, and every health
    check asked the stamp. A `StageRecord` therefore carries the ARTIFACT the
    stage produced, not the moment it finished: a record with no artifact id is
    a record of nothing, and `Ledger.problems` says so.
  * A trace that looks complete because each agent recorded itself under its own
    id. Bodha through Siddhi is one flow across six agents and several background
    tasks; without one id shared by all of them, "what happened to this
    candidate" is six unrelated queries whose answers cannot be joined.
  * An AI-initiated mutation attributable to nobody. RBAC 34 requires BOTH the
    human on whose behalf an agent acted and the agent that executed. A
    `Principal` with a blank user id is refused at construction rather than
    written and explained later, because a row that lost the human reads exactly
    like a human action, which is the one reading that must never be possible.

THE CORRELATION ID IS ISSUED ONCE, AT JOB CREATION, AND IS NEVER INVENTED
---------------------------------------------------------------------------
spec-doc6 4.1. Not per agent, not per task, not per dispatch: one id for
the whole flow. `Envelope.child` copies it rather than re-minting, which is what
makes a sub-task joinable to its parent's flow instead of merely adjacent to it.

It is DERIVED from the row the flow belongs to -- `job-<job id hex>` for a
hiring flow, `dna-<artifact id hex>` for a Company DNA intake, which begins
before any job exists. Derivation rather than a fresh uuid buys two things. The
id is stable across a re-run, so a rescore joins to the same flow instead of
opening a second one that looks like a different candidate. And it is
RECONSTRUCTIBLE: given a job row, an operator can compute the id and query the
audit trail, rather than needing the id to have been recorded somewhere first.
`0064_sutra_seven_stage_provenance` backfills `jobs.correlation_id` with exactly
this shape, and this module is where that shape is defined rather than repeated.

There is deliberately no `new_correlation_id()`. A minted id would let any stage
open a flow of its own, and six stages each with their own flow is the state
this field exists to end. A stage that has no correlation id is refused by
`Envelope.require_correlation_id`, exactly as one with no principal is.

WHY THE LEDGER IS AN OBJECT AND NOT A MODULE-LEVEL DICT
--------------------------------------------------------
A process-global accumulator keyed by correlation id would be shared across
tenants, would grow without bound in a long-lived worker, and would make two
concurrent flows in one process observable to each other. The ledger is created
by whoever issues the correlation id and travels with the flow, which is the
same discipline the envelope follows and for the same reason.

NOTHING HERE CARRIES CONTENT
-----------------------------
Every field on `StageRecord` is an identifier, a type name, a version integer or
a status word. No JD text, no answer, no remark, no prompt. That is a property
of the SHAPE, so a future field carrying content has to be added to a frozen
dataclass whose docstring says it carries none.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

__all__ = [
    "A2A_CONTRACT_FIELDS",
    "CORRELATION_KINDS",
    "Ledger",
    "MissingPrincipal",
    "Principal",
    "STAGES",
    "ARTIFACT_BEARING_STAGES",
    "STAGE_APPLICATION",
    "STAGE_CONVERSATION",
    "STAGE_DELIVERY",
    "STAGE_DISPOSITION",
    "STAGE_JOB_CREATED",
    "STAGE_MATRIX",
    "STAGE_PRESCREEN",
    "STAGE_REPORT",
    "STAGE_SCORING",
    "STAGE_SWOT",
    "StageRecord",
    "correlation_for_dna",
    "correlation_for_job",
    "is_correlation_id",
    "log_fields",
]

#: The eight fields specdoc4 15 requires on every hand-off. Named here as DATA
#: so `artifacts.contract_gaps` and the tests read the same list rather than
#: each restating it, which is how two lists that must agree stop agreeing.
A2A_CONTRACT_FIELDS: tuple[str, ...] = (
    "message",
    "task",
    "artifact",
    "status",
    "context",
    "provenance",
    "version",
    "correlation_id",
)

#: What a flow can be anchored to. A hiring flow is anchored to its job; a
#: Company DNA intake begins before any job exists and is anchored to the
#: artifact instead. Two kinds and no more: a third would mean a flow whose
#: anchor a reader has to guess at.
CORRELATION_KINDS: tuple[str, ...] = ("job", "dna")

_HEX = frozenset("0123456789abcdef")


def _correlation(kind: str, identifier: Any) -> str:
    if kind not in CORRELATION_KINDS:
        raise ValueError(
            f"{kind!r} is not a correlation kind; expected one of {CORRELATION_KINDS}"
        )
    # Through `uuid.UUID` rather than string surgery, so a malformed identifier
    # raises here instead of producing a well-shaped id that joins to nothing.
    return f"{kind}-{uuid.UUID(str(identifier)).hex}"


def correlation_for_job(job_id: Any) -> str:
    """The correlation id for everything that happens to one job.

    Issued at job creation and never rewritten. `0064` backfills existing rows
    with exactly this value, so an id computed here matches the one already in
    `jobs.correlation_id` for every job that predates the flow.
    """
    return _correlation("job", job_id)


def correlation_for_dna(company_dna_id: Any) -> str:
    """The correlation id for a Company DNA intake session.

    A separate anchor because Layer 2 intake happens before any job exists, and
    an intake correlated to a job would have to pick one arbitrarily out of
    every job that client will ever post.
    """
    return _correlation("dna", company_dna_id)


def is_correlation_id(value: Any) -> bool:
    """Whether `value` is a well-formed correlation id.

    Checked rather than assumed, because the failure it catches is a caller
    passing a raw job id or a workflow id into the correlation slot: both are
    hex, both look right in a log line, and neither joins the flow to the audit
    rows written under the real id.
    """
    if not isinstance(value, str) or "-" not in value:
        return False
    kind, _, suffix = value.partition("-")
    return (
        kind in CORRELATION_KINDS
        and len(suffix) == 32
        and all(c in _HEX for c in suffix)
    )


class MissingPrincipal(PermissionError):
    """An agent action with no human behind it, or no tenant to act inside.

    A `PermissionError` rather than a `ValueError` because that is what it is:
    RBAC 34 makes the human principal part of the authorization, not part of the
    bookkeeping. Raised at construction so the refusal happens before the
    handler runs, which is the same ordering the tool layer uses -- a refusal
    that ran the handler first has already read the row it was refusing to show.
    """


@dataclass(frozen=True)
class Principal:
    """The human an agent is acting for, and the tenant they are acting inside.

    `user_id` is the HUMAN, always. It is never an agent name and never a
    service account: the question this field answers is "who authorised this",
    and an agent cannot authorise itself.
    """

    user_id: str
    role: str
    tenant_id: str

    def __post_init__(self) -> None:
        for name in ("user_id", "role", "tenant_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise MissingPrincipal(
                    f"an agent action requires a {name}; RBAC 34 attributes every "
                    "AI-initiated mutation to both the human principal and the "
                    "executing agent, and a blank one attributes it to neither"
                )

    def as_dict(self) -> dict[str, str]:
        return {
            "principal_user_id": self.user_id,
            "principal_role": self.role,
            "tenant_id": self.tenant_id,
        }


# ── The stages one flow passes through ───────────────────────────────────────
#
# Named after what HAPPENED, not after the agent that did it. Two agents run in
# parallel at matrix time and one agent (Bodha) has two mandates, so a stage
# list keyed on agent names could not express either. The journey test walks
# this tuple.

STAGE_JOB_CREATED = "job_created"
STAGE_SWOT = "swot_intake"
STAGE_MATRIX = "tatva_matrix"
STAGE_PRESCREEN = "prescreen"
STAGE_APPLICATION = "application"
STAGE_CONVERSATION = "conversation"
STAGE_SCORING = "scoring"
STAGE_DISPOSITION = "human_disposition"
STAGE_REPORT = "report"
STAGE_DELIVERY = "delivery"

STAGES: tuple[str, ...] = (
    STAGE_JOB_CREATED,
    STAGE_SWOT,
    STAGE_MATRIX,
    STAGE_PRESCREEN,
    STAGE_APPLICATION,
    STAGE_CONVERSATION,
    STAGE_SCORING,
    STAGE_DISPOSITION,
    STAGE_REPORT,
    STAGE_DELIVERY,
)


@dataclass(frozen=True)
class StageRecord:
    """One stage of one flow, recorded in identifiers and nothing else.

    `artifact_id` is what makes this a record of WORK rather than a record of
    TIME. A stage that produced no artifact and claims to have run is the exact
    shape of the `framework_generated_at` defect, and `Ledger.problems` reports
    it as one.
    """

    correlation_id: str
    stage: str
    agent_id: str | None
    tenant_id: str
    principal_user_id: str
    principal_role: str
    job_id: str | None = None
    candidate_id: str | None = None
    artifact_id: str | None = None
    artifact_type: str | None = None
    artifact_version: int | None = None
    #: The gate that guarded this stage and whether it passed, when one did.
    gate: str | None = None
    gate_passed: bool | None = None
    status: str = "ok"
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "stage": self.stage,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "principal_user_id": self.principal_user_id,
            "principal_role": self.principal_role,
            "job_id": self.job_id,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "artifact_version": self.artifact_version,
            "gate": self.gate,
            "gate_passed": self.gate_passed,
            "status": self.status,
            "at": self.at.isoformat(),
        }


class Ledger:
    """Every stage one flow has completed, in order, under one correlation id.

    Append-only within a run. `record` refuses a stage whose correlation id is
    not this flow's, which is the check that catches the copy-paste bug where a
    second flow's id is threaded into the first flow's stage: without it the two
    flows merge into one plausible-looking trace and neither is recoverable.
    """

    def __init__(self, correlation_id: str) -> None:
        if not is_correlation_id(correlation_id):
            raise ValueError(
                f"{correlation_id!r} is not a correlation id issued by "
                "provenance.new_correlation_id; a job id or a workflow id in "
                "this slot joins the flow to nothing"
            )
        self.correlation_id = correlation_id
        self._records: list[StageRecord] = []

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[StageRecord, ...]:
        return tuple(self._records)

    def record(self, entry: StageRecord) -> StageRecord:
        if entry.correlation_id != self.correlation_id:
            raise ValueError(
                f"stage {entry.stage!r} carries correlation id "
                f"{entry.correlation_id!r}, but this ledger is "
                f"{self.correlation_id!r}"
            )
        self._records.append(entry)
        return entry

    def stages(self) -> tuple[str, ...]:
        return tuple(entry.stage for entry in self._records)

    def for_stage(self, stage: str) -> tuple[StageRecord, ...]:
        return tuple(entry for entry in self._records if entry.stage == stage)

    def missing(self, expected: Iterable[str] = STAGES) -> tuple[str, ...]:
        seen = set(self.stages())
        return tuple(stage for stage in expected if stage not in seen)

    def problems(self, expected: Iterable[str] = STAGES) -> list[str]:
        """Everything wrong with this flow's provenance. Empty is healthy.

        A list rather than an exception because a reader wants all of it at
        once, the same posture `orchestration_checks.structural_invariants`
        takes.
        """
        out: list[str] = []
        for stage in self.missing(expected):
            out.append(f"stage {stage!r} never ran under {self.correlation_id}")

        # A stage legitimately writes MORE THAN ONE record: the work, and each
        # gate that fired during it. G2 and G3 both fire at aggregation and
        # neither produces an artifact of its own, so the rule is per STAGE and
        # not per record: an artifact-bearing stage must have left at least one
        # artifact behind somewhere among its records.
        for stage in expected:
            entries = self.for_stage(stage)
            if not entries or stage not in ARTIFACT_BEARING_STAGES:
                continue
            if not any(entry.artifact_id for entry in entries):
                out.append(
                    f"stage {stage!r} recorded no artifact. A stage that left "
                    "nothing behind is a timestamp, and a timestamp is not "
                    "evidence that work happened."
                )

        for entry in self._records:
            # A record that is neither work nor a gate verdict is a record of
            # nothing at all, which is the shape the artifact rule above would
            # otherwise let through one stage at a time.
            if not entry.artifact_id and not entry.gate:
                out.append(
                    f"stage {entry.stage!r} recorded neither an artifact nor a "
                    "gate verdict, so nothing about it can be checked."
                )
            if not entry.principal_user_id:
                out.append(
                    f"stage {entry.stage!r} ran with no human principal (RBAC 34)"
                )
        return out


#: Stages that must leave an artifact behind. `job_created`, `application` and
#: `human_disposition` are database facts rather than agent hand-offs, so they
#: are legitimately artifact-free; every stage an AGENT performs is not.
ARTIFACT_BEARING_STAGES: frozenset[str] = frozenset(
    {
        STAGE_SWOT,
        STAGE_MATRIX,
        STAGE_PRESCREEN,
        STAGE_CONVERSATION,
        STAGE_SCORING,
        STAGE_REPORT,
    }
)


def log_fields(
    *,
    correlation_id: str,
    stage: str | None = None,
    agent_id: str | None = None,
    **extra: Any,
) -> str:
    """One `key=value` fragment to append to a log line for this flow.

    Key=value rather than JSON for the reason `observability.trace.log` already
    gives: it stays greppable in a terminal and every backend this product uses
    parses it. Only identifiers are accepted -- a caller passing an answer would
    be passing it as `extra`, and `_SAFE_LOG_KEYS` drops it.
    """
    parts = [f"correlation_id={correlation_id}"]
    if stage:
        parts.append(f"stage={stage}")
    if agent_id:
        parts.append(f"agent={agent_id}")
    for key, value in extra.items():
        if key in _SAFE_LOG_KEYS and value is not None:
            parts.append(f"{key}={value}")
    return " ".join(parts)


#: The allowlist for `log_fields`, same discipline as `_SAFE_STAGE_KEYS` in the
#: trace: the next person adding "the prompt we sent" for debugging finds it
#: dropped rather than finding it in a log aggregator a month later.
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "job_id",
        "candidate_id",
        "artifact_id",
        "artifact_type",
        "artifact_version",
        "principal_user_id",
        "principal_role",
        "gate",
        "gate_passed",
        "status",
        "count",
        "duration_ms",
    }
)


def contract_gaps(fields: Mapping[str, Any]) -> list[str]:
    """Which of the eight A2A contract fields are absent or empty.

    Takes a mapping rather than an object so it can be run against a persisted
    row as easily as against a live artifact -- an artifact written by an older
    build is exactly the case a consumer cannot verify by trusting the code that
    wrote it.
    """
    gaps: list[str] = []
    for name in A2A_CONTRACT_FIELDS:
        value = fields.get(name)
        if value is None or (isinstance(value, (str, dict, list, tuple)) and not value):
            gaps.append(name)
    return gaps
