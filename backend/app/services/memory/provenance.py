"""Where a remembered thing came from, and how far it may be trusted.

THE DECISION THIS MODULE EXISTS TO RECORD (RPN-AI-UP-001 W3.5)
---------------------------------------------------------------
A learning derived from candidate authored text in tenant A must not influence
grading in tenant B. Before this, nothing prevented it: `agent_learnings` had
no tenant column at all, and its own docstring argued that a lesson about how
an agent misreads a JD is a property of the product. That argument is true for
the lesson and false for its INPUT. The pattern is extracted from a run whose
prompt carried a real candidate's answers and a real client's job description,
so a hostile paragraph in a resume uploaded to one customer could shape the
guidance prepended to every later run for every other customer. It is the one
place in this architecture where one tenant's untrusted input can reach another
tenant's decision.

The specification offers two acceptable answers and says to pick one and write
it down. THIS PRODUCT SCOPES LEARNINGS PER TENANT.

Why that one and not human approval before promotion to shared:

  * Per-tenant scoping is enforceable STRUCTURALLY. `tenant_id` is NOT NULL, the
    retrieval query filters on it, and an RLS policy on `agent_learnings` means
    a session scoped to tenant B cannot read tenant A's rows even through a
    query somebody wrote wrong. Approval is a PROCESS: it is a checkbox
    somebody ticks, under deadline, on evidence they cannot easily verify, and
    the thing they are approving is a sentence whose provenance is a summary of
    a summary.
  * Approval creates a second class of learning, the shared one, and therefore
    two retrieval paths. One implementation per concept: two paths means the
    second is the one nobody tests.
  * The cost is real and is the smaller cost: a genuine product-wide lesson is
    relearned per tenant rather than once. That buys repetition. The other
    answer buys a cross-tenant influence channel guarded by attention.

WHAT DOES NOT CHANGE, AND MUST NOT
-----------------------------------
A learning is a HINT and never a gate. It is prepended to a prompt as guidance
and cannot relax a word range, skip a verifier or lower a threshold, because
the gates are code and a hint is prompt text. Nothing below `MIN_OBSERVATIONS`
is applied at all. That constraint is stronger than most published memory
poisoning defences and this module weakens no part of it: everything here
NARROWS what may be retrieved.

TRUST LEVEL IS ABOUT THE INPUT, NOT ABOUT THE CONCLUSION
----------------------------------------------------------
The four levels name who authored the text the lesson was extracted from. They
do not rank how good the lesson is, because nothing here can know that. What
they decide is how long it may stand before it has to be re-proven: text this
platform generated deterministically stays true for as long as the code does,
and text a stranger uploaded is the input an attacker controls, so it earns the
shortest life.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

#: Derived from the platform's own deterministic output: a verifier defect, a
#: schema rejection, a word count. Nobody outside ReadyPick chose the text.
TRUST_PLATFORM = "platform"
#: Recorded by a ReadyPick engineer looking at a run.
TRUST_OPERATOR = "operator"
#: Derived from text the client wrote, such as a job description.
TRUST_TENANT_AUTHORED = "tenant_authored"
#: Derived from text a candidate wrote: a resume, a project, an answer. The
#: input an attacker controls, and the default when nobody says otherwise.
TRUST_CANDIDATE_AUTHORED = "candidate_authored"

#: Least trusted last. The order is read by the retrieval query, so a tie
#: between two equally proven learnings goes to the one with the safer input.
TRUST_LEVELS: tuple[str, ...] = (
    TRUST_PLATFORM,
    TRUST_OPERATOR,
    TRUST_TENANT_AUTHORED,
    TRUST_CANDIDATE_AUTHORED,
)

TRUST_RANK: dict[str, int] = {level: rank for rank, level in enumerate(TRUST_LEVELS)}

#: How long a learning may be applied before it has to be re-proven, per trust
#: level. These are the consequence of the trust level: without them the column
#: is a label, and a label that changes nothing is a comment in a table.
#:
#: Platform and operator sit at a release cadence, because what they are about
#: is this codebase and this codebase changes on that scale. Tenant authored is
#: shorter because a client rewrites their own job descriptions. Candidate
#: authored is shortest because it is the only one an outsider chooses the
#: words of, and a poisoned learning that expires in a month is a poisoned
#: learning with a bounded life even if nobody ever notices it.
REVALIDATION_DAYS: dict[str, int] = {
    TRUST_PLATFORM: 180,
    TRUST_OPERATOR: 180,
    TRUST_TENANT_AUTHORED: 90,
    TRUST_CANDIDATE_AUTHORED: 30,
}


class ProvenanceError(ValueError):
    """A remembered thing that cannot say where it came from.

    Raised rather than defaulted. A default tenant would be a cross-tenant
    write with a plausible-looking row behind it, which is precisely the
    failure this whole module exists to make impossible.
    """


def revalidate_after(trust_level: str, *, now: datetime | None = None) -> datetime:
    """When a learning at this trust level stops being applied.

    Raises on an unknown level rather than picking a window. Inventing a
    deadline for a level nobody declared is inventing a trust decision.
    """
    if trust_level not in REVALIDATION_DAYS:
        raise ProvenanceError(
            f"unknown trust level {trust_level!r}; expected one of {TRUST_LEVELS}"
        )
    moment = now or datetime.now(timezone.utc)
    return moment + timedelta(days=REVALIDATION_DAYS[trust_level])


@dataclass(frozen=True)
class Provenance:
    """The seven facts every memory layer records about what it holds.

    Frozen, and validated at construction, so a layer cannot be handed half of
    it. The alternative shape, seven optional keyword arguments on each write
    function, makes "the tenant was not passed" a runtime condition each writer
    has to remember to check, and one of them will not.
    """

    #: The tenant this belongs to. Never None: see the module docstring.
    tenant_id: uuid.UUID | str
    #: What produced it, by name. Stable enough to revoke by:
    #: `revoke_learnings_from_source` matches on this exact string.
    source: str
    #: Which version of that producer. A prompt fingerprint
    #: (`memory.procedural.fingerprint`), a release tag, a schema version. None
    #: when the producer is not versioned.
    source_version: str | None = None
    #: The human whose authority the run acted under, when there was one.
    #: A background sweep has none, and says so rather than borrowing one.
    created_by: uuid.UUID | str | None = None
    #: One of `TRUST_LEVELS`.
    trust_level: str = TRUST_CANDIDATE_AUTHORED
    #: Identifiers of the evidence this was drawn from. Identifiers, never
    #: content: a quoted answer stored here would put a candidate's words in a
    #: table that is read to build a prompt for a different candidate.
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.tenant_id is None:
            raise ProvenanceError("a remembered thing must name its tenant")
        if not str(self.source).strip():
            raise ProvenanceError("a remembered thing must name its source")
        if self.trust_level not in TRUST_RANK:
            raise ProvenanceError(
                f"unknown trust level {self.trust_level!r}; "
                f"expected one of {TRUST_LEVELS}"
            )

    def expires_at(self, *, now: datetime | None = None) -> datetime:
        return revalidate_after(self.trust_level, now=now)

    def as_row(self, *, now: datetime | None = None) -> dict[str, object]:
        """The seven columns, as a query's bind parameters."""
        return {
            "tenant_id": str(self.tenant_id),
            "source": self.source,
            "source_version": self.source_version,
            "created_by": str(self.created_by) if self.created_by else None,
            "trust_level": self.trust_level,
            "evidence_ids": list(self.evidence_ids),
            "revalidate_after": self.expires_at(now=now),
        }
