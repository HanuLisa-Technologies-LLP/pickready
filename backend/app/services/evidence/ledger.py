"""The claim-level evidence ledger: what is asserted, and what stands behind it.

WHY A LEDGER AND NOT A FIELD ON THE REPORT
-------------------------------------------
A report states a grade. It does not state which sentence of which document the
grade rests on, and once the report is written nothing can reconstruct that.
The question a recruiter actually asks when they disagree with a grade is "what
did you read", and until now the only honest answer was "the whole resume and
the whole transcript". The ledger is that answer made addressable: one row per
claim, and every piece of evidence for and against it named individually.

A REFERENCE, NEVER A COPY OF THE TEXT
--------------------------------------
`text_ref` is a locator. It is never the sentence. The reason is the same one
that made `agent_execution_traces` drop a defect's `detail`: an excerpt from a
resume or an answer quotes a real candidate, and this table is far more widely
readable than the transcript it points at. Anyone with database access can read
a ledger row while reading the transcript needs `view_review_screen`, so a
ledger that stored excerpts would be a quiet route around that capability.
`resolve_text` exists for the one caller that legitimately needs the sentence,
and it goes back to the source table under the same tenant scope.

`relevance` AND ANY CONFIDENCE ARE INTERNAL ENGINEERING METADATA
-----------------------------------------------------------------
The specification says so explicitly, and the product's standing rule says it
louder: no number reaches a client. `relevance` orders a list of evidence for a
prompt and for an operator; it is not a score, it is not a grade, and it must
never appear in a response schema. `client_projection` is the only projection
anything client-facing may render, and it drops every number by construction
rather than by the caller remembering to.

TRUST IS A LATTICE, NOT A NUMBER
---------------------------------
authoritative > validated > observed > inferred. It is ordered rather than
added because two weak pieces of evidence are not one strong one: a claim
inferred twice from the same resume phrasing is still inferred. The rule that
matters is that a claim standing only on `inferred` evidence does NOT read as
supported, because "the resume mentions Kafka in a skills list" is the exact
shape of evidence that looks like corroboration and carries none.

SUPERSEDED EVIDENCE IS NEVER DELETED
-------------------------------------
A candidate uploads a new resume and the old resume's evidence becomes
`superseded`, not gone. A report already written is a permanent record of what
it was written from, and deleting the row it pointed at turns "what was this
grade based on?" into an unanswerable question -- the same failure the
`technical_questions` table was deliberately kept unread to avoid.

NOTHING HERE CALLS A MODEL
---------------------------
The ledger records what other components found. A model deciding whether
evidence supports a claim would make the one artifact meant to explain a grade
depend on the same provider whose outage is when explanations matter most.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Source types (spec 47) ───────────────────────────────────────────────────
# Deliberately closed. An unrecognised source type is a programming error and is
# refused at write, because a source nothing can resolve is evidence nobody can
# check, which is worse than no evidence at all.
SOURCE_RESUME = "resume"
SOURCE_ANSWER = "answer"
SOURCE_JD = "jd"
SOURCE_SWOT = "swot"
SOURCE_VALIDATION = "validation"
SOURCE_MEMORY = "memory"

SOURCE_TYPES: frozenset[str] = frozenset(
    {
        SOURCE_RESUME,
        SOURCE_ANSWER,
        SOURCE_JD,
        SOURCE_SWOT,
        SOURCE_VALIDATION,
        SOURCE_MEMORY,
    }
)

# ── The trust lattice ────────────────────────────────────────────────────────
TRUST_AUTHORITATIVE = "authoritative"  # a document the employer or a system issued
TRUST_VALIDATED = "validated"          # the candidate confirmed it when asked
TRUST_OBSERVED = "observed"            # they said it, unprompted, in their own words
TRUST_INFERRED = "inferred"            # the product concluded it from something else

#: Ordinals for COMPARISON only. They are never summed and never averaged: two
#: `inferred` items are not one `observed` item, and any arithmetic over these
#: would quietly assert that they are.
_TRUST_RANK: dict[str, int] = {
    TRUST_INFERRED: 0,
    TRUST_OBSERVED: 1,
    TRUST_VALIDATED: 2,
    TRUST_AUTHORITATIVE: 3,
}

TRUST_LEVELS: frozenset[str] = frozenset(_TRUST_RANK)

#: The floor at which evidence may make a claim read as supported. Set above
#: `inferred` on purpose: an inference is the product agreeing with itself.
MIN_SUPPORTING_TRUST = TRUST_OBSERVED

# ── Item lifecycle ───────────────────────────────────────────────────────────
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REVOKED = "revoked"

ITEM_STATUSES: frozenset[str] = frozenset(
    {STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_REVOKED}
)

# ── Which side of a claim a piece of evidence sits on ────────────────────────
STANCE_SUPPORTS = "supports"
STANCE_CONTRADICTS = "contradicts"
STANCES: frozenset[str] = frozenset({STANCE_SUPPORTS, STANCE_CONTRADICTS})

# ── How a claim reads once its evidence is counted ───────────────────────────
CLAIM_SUPPORTED = "supported"
#: Active evidence exists on both sides. Reported as its own state and NEVER
#: resolved here: which side is right is a question for the recruiter, exactly
#: as `verification.contradiction` has always had it.
CLAIM_CONTRADICTED = "contradicted"
#: Everything standing behind it is `inferred`. Distinct from `unsupported`
#: because the difference is real and actionable: there is something to ask
#: about, and nobody has asked yet.
CLAIM_INFERRED_ONLY = "inferred_only"
CLAIM_UNSUPPORTED = "unsupported"

#: Freshness bands, as WORDS. A ledger row can be read into an operator view and
#: from there into a screen, and "eleven months old" is a number about a
#: candidate. The day count stays in the `freshness` payload for engineering.
FRESHNESS_CURRENT = "current"
FRESHNESS_RECENT = "recent"
FRESHNESS_DATED = "dated"

#: Day boundaries for the bands above, inclusive upward like every other
#: boundary in this product (claude.md rule 8).
_FRESHNESS_CURRENT_DAYS = 90
_FRESHNESS_RECENT_DAYS = 365


class LedgerError(ValueError):
    """A write the ledger refuses.

    Raised rather than swallowed. A ledger that accepted an unknown trust level
    and stored it would hand every later reader a value their comparison does
    not recognise, and the claim would silently stop reading as supported.
    """


def _require(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise LedgerError(f"{label} must be one of {sorted(allowed)}, got {value!r}")
    return value


def trust_rank(trust: str) -> int:
    """Position in the lattice. Unknown trust ranks below everything.

    Deny by default, matching `permissions.granted_tools`: a value nobody
    recognises must not be able to promote a claim.
    """
    return _TRUST_RANK.get(trust, -1)


def freshness(as_of: datetime | None, *, now: datetime | None = None) -> dict[str, Any]:
    """The freshness payload for one evidence item.

    `age_days` is internal engineering metadata and carries the same warning as
    `relevance`. `band` is the word anything human-readable should use.
    """
    if as_of is None:
        return {"as_of": None, "age_days": None, "band": FRESHNESS_DATED}
    reference = now or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    age_days = max(0, int((reference - as_of).total_seconds() // 86400))
    if age_days <= _FRESHNESS_CURRENT_DAYS:
        band = FRESHNESS_CURRENT
    elif age_days <= _FRESHNESS_RECENT_DAYS:
        band = FRESHNESS_RECENT
    else:
        band = FRESHNESS_DATED
    return {"as_of": as_of.isoformat(), "age_days": age_days, "band": band}


@dataclass(frozen=True)
class EvidenceItem:
    """One addressable piece of evidence (spec 47).

    Note what is absent: there is no `text` field and no free-form escape hatch
    that one could be smuggled into. The shape is the enforcement, the same way
    `JobFacts` carries no compensation field -- a rule that travels with the
    model cannot be forgotten by the next call site.
    """

    evidence_id: uuid.UUID
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    link_id: uuid.UUID | None
    source_type: str
    source_id: uuid.UUID
    #: WHERE the evidence lives, never WHAT it says. Written by `text_ref`.
    text_ref: str
    provenance: Mapping[str, Any] = field(default_factory=dict)
    freshness: Mapping[str, Any] = field(default_factory=dict)
    trust: str = TRUST_INFERRED
    #: INTERNAL ENGINEERING METADATA (spec 47). Orders evidence inside a prompt
    #: and inside an operator view. Never a schema field a client reads.
    relevance: float = 0.0
    status: str = STATUS_ACTIVE
    superseded_by: uuid.UUID | None = None

    @property
    def is_live(self) -> bool:
        """Whether this item still counts toward a claim.

        Superseded and revoked items are retained and do not count. Retaining
        them is what keeps an already-written report explicable; counting them
        would let a resume the candidate replaced keep supporting a grade.
        """
        return self.status == STATUS_ACTIVE

    def client_projection(self) -> dict[str, Any]:
        """What may cross a client boundary.

        No relevance, no age in days, no confidence, no identifiers of internal
        rows. A word for how fresh it is and a word for how far it is trusted.
        """
        return {
            "source_type": self.source_type,
            "trust": self.trust,
            "freshness": self.freshness.get("band", FRESHNESS_DATED),
        }


@dataclass(frozen=True)
class Claim:
    """One assertion the product is making, and everything it rests on (spec 13).

    `claim` is the ledger's OWN normalised statement of what is being asserted
    ("has production experience with Kafka"). It is written by the product, not
    lifted from the candidate, for the same reason `text_ref` is not the text.
    """

    claim_id: uuid.UUID
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    link_id: uuid.UUID | None
    subject: str
    dimension: str
    claim: str
    supporting_evidence: tuple[EvidenceItem, ...] = ()
    contradicting_evidence: tuple[EvidenceItem, ...] = ()

    @property
    def live_support(self) -> tuple[EvidenceItem, ...]:
        return tuple(item for item in self.supporting_evidence if item.is_live)

    @property
    def live_contradiction(self) -> tuple[EvidenceItem, ...]:
        return tuple(item for item in self.contradicting_evidence if item.is_live)

    @property
    def status(self) -> str:
        """How the claim reads, derived from live evidence every time it is asked.

        Never stored and never cached. A stored support state is a mirror that
        goes stale the moment somebody revokes an item by another path, and the
        stale value would be the one a report is written from.
        """
        return support_state(self.live_support, self.live_contradiction)

    @property
    def freshness(self) -> str:
        """The freshest band among live supporting evidence, as a word."""
        order = (FRESHNESS_CURRENT, FRESHNESS_RECENT, FRESHNESS_DATED)
        bands = {
            item.freshness.get("band", FRESHNESS_DATED) for item in self.live_support
        }
        for band in order:
            if band in bands:
                return band
        return FRESHNESS_DATED

    @property
    def provenance(self) -> tuple[str, ...]:
        """The distinct source types standing behind it, in a stable order.

        Source TYPES rather than source ids: this is the shape a reader uses to
        judge whether a claim rests on one document read four ways.
        """
        return tuple(sorted({item.source_type for item in self.live_support}))

    def client_projection(self) -> dict[str, Any]:
        """The claim as a client may see it: words, and nothing countable."""
        return {
            "subject": self.subject,
            "dimension": self.dimension,
            "claim": self.claim,
            "status": self.status,
            "freshness": self.freshness,
            "provenance": list(self.provenance),
            "supporting_evidence": [
                item.client_projection() for item in self.live_support
            ],
            "contradicting_evidence": [
                item.client_projection() for item in self.live_contradiction
            ],
        }


def support_state(
    supporting: Sequence[EvidenceItem], contradicting: Sequence[EvidenceItem]
) -> str:
    """How a claim reads, from the evidence currently standing behind it.

    Contradiction is checked FIRST and is not cancelled by support. A claim with
    strong evidence on both sides is the most interesting row in the ledger and
    the easiest one to lose: any rule that let support outweigh contradiction
    would be the silent averaging spec 14 exists to forbid.
    """
    if any(item.is_live for item in contradicting):
        return CLAIM_CONTRADICTED
    live = [item for item in supporting if item.is_live]
    if not live:
        return CLAIM_UNSUPPORTED
    if all(trust_rank(item.trust) < trust_rank(MIN_SUPPORTING_TRUST) for item in live):
        return CLAIM_INFERRED_ONLY
    return CLAIM_SUPPORTED


def text_ref(*, table: str, row_id: uuid.UUID | str, fragment: str | None = None) -> str:
    """Build a locator.

    A structured string rather than three columns because it is only ever
    dereferenced whole, and because a caller that assembles it by hand from a
    format string is a caller that eventually pastes the sentence in.
    """
    ref = f"{table}:{row_id}"
    return f"{ref}#{fragment}" if fragment else ref


def parse_text_ref(ref: str) -> tuple[str, str, str | None]:
    table, _, rest = str(ref or "").partition(":")
    row_id, _, fragment = rest.partition("#")
    return table, row_id, fragment or None


# ── Persistence ──────────────────────────────────────────────────────────────
# Every statement names the tenant in its WHERE clause. That filter is defence
# in depth and NOT the boundary: the boundary is the RLS policy on the three
# tables, reached through `core.db.tenant_scope` (claude.md rule 1). Both, for
# the reason the rule gives -- an app filter is one forgotten clause away from
# gone, and a policy alone cannot express "this job, this candidate".


async def record_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID | None,
    source_type: str,
    source_id: uuid.UUID,
    ref: str,
    trust: str,
    relevance: float = 0.0,
    provenance: Mapping[str, Any] | None = None,
    freshness_payload: Mapping[str, Any] | None = None,
) -> uuid.UUID:
    """Write one evidence item and return its id.

    `ref` is a locator built by `text_ref`. The parameter is deliberately not
    called `text`: the name is the last place a rule like this can be stated
    before somebody passes the wrong thing.
    """
    _require(source_type, SOURCE_TYPES, "source_type")
    _require(trust, TRUST_LEVELS, "trust")
    evidence_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO evidence_items (
                id, tenant_id, job_id, link_id, source_type, source_id,
                text_ref, provenance, freshness, trust, relevance, status
            ) VALUES (
                :id, :tenant_id, :job_id, :link_id, :source_type, :source_id,
                :text_ref, CAST(:provenance AS jsonb), CAST(:freshness AS jsonb),
                :trust, :relevance, :status
            )
            """
        ),
        {
            "id": str(evidence_id),
            "tenant_id": str(tenant_id),
            "job_id": str(job_id),
            "link_id": str(link_id) if link_id else None,
            "source_type": source_type,
            "source_id": str(source_id),
            "text_ref": ref,
            "provenance": _json(provenance or {}),
            "freshness": _json(freshness_payload or {}),
            "trust": trust,
            "relevance": float(relevance),
            "status": STATUS_ACTIVE,
        },
    )
    return evidence_id


async def supersede_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
    replaced_by: uuid.UUID,
) -> None:
    """Retire an item in favour of a newer version of the same source.

    An UPDATE of the status, never a DELETE. The old row keeps pointing at the
    document version a report was written from; `superseded_by` is what lets a
    reader walk forward to the current one.
    """
    await session.execute(
        text(
            """
            UPDATE evidence_items
               SET status = :status, superseded_by = :replaced_by, updated_at = now()
             WHERE id = :id AND tenant_id = :tenant_id AND status = :active
            """
        ),
        {
            "status": STATUS_SUPERSEDED,
            "replaced_by": str(replaced_by),
            "id": str(evidence_id),
            "tenant_id": str(tenant_id),
            "active": STATUS_ACTIVE,
        },
    )


async def revoke_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
    reason: str,
) -> None:
    """Withdraw an item entirely: a mis-parse, or a chunk the guardrails flagged.

    Also an UPDATE. The reason lands in `provenance` rather than in a separate
    column so that a reader asking "why is this not counted" finds the answer in
    the row itself instead of in a log that has since rotated.
    """
    await session.execute(
        text(
            """
            UPDATE evidence_items
               SET status = :status,
                   provenance = provenance || CAST(:reason AS jsonb),
                   updated_at = now()
             WHERE id = :id AND tenant_id = :tenant_id
            """
        ),
        {
            "status": STATUS_REVOKED,
            "reason": _json({"revoked_reason": reason}),
            "id": str(evidence_id),
            "tenant_id": str(tenant_id),
        },
    )


async def record_claim(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID | None,
    subject: str,
    dimension: str,
    claim: str,
) -> uuid.UUID:
    """Create or reuse the claim row for one (subject, dimension, claim).

    Idempotent on that triple, because the alternative is two rows asserting the
    same thing with half the evidence each, which reads as two independent
    findings and is one.
    """
    claim_id = uuid.uuid4()
    row = (
        await session.execute(
            text(
                """
                INSERT INTO evidence_claims (
                    id, tenant_id, job_id, link_id, subject, dimension, claim
                ) VALUES (
                    :id, :tenant_id, :job_id, :link_id, :subject, :dimension, :claim
                )
                ON CONFLICT (tenant_id, job_id, link_id, subject, dimension, claim)
                DO UPDATE SET updated_at = now()
                RETURNING id
                """
            ),
            {
                "id": str(claim_id),
                "tenant_id": str(tenant_id),
                "job_id": str(job_id),
                "link_id": str(link_id) if link_id else None,
                "subject": subject,
                "dimension": dimension,
                "claim": claim,
            },
        )
    ).scalar_one()
    return uuid.UUID(str(row))


async def attach_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    evidence_id: uuid.UUID,
    stance: str,
) -> None:
    """Put one evidence item on one side of one claim.

    The stance is a column on the link rather than two id arrays on the claim.
    Two arrays make "the same item in both" representable, and there is nothing
    a reader could do with that state except guess.
    """
    _require(stance, STANCES, "stance")
    await session.execute(
        text(
            """
            INSERT INTO evidence_claim_links (
                id, tenant_id, claim_id, evidence_id, stance
            ) VALUES (:id, :tenant_id, :claim_id, :evidence_id, :stance)
            ON CONFLICT (claim_id, evidence_id) DO UPDATE SET stance = EXCLUDED.stance
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "claim_id": str(claim_id),
            "evidence_id": str(evidence_id),
            "stance": stance,
        },
    )


_CLAIM_SELECT = """
    SELECT c.id            AS claim_id,
           c.tenant_id     AS claim_tenant,
           c.job_id        AS claim_job,
           c.link_id       AS claim_link,
           c.subject, c.dimension, c.claim,
           l.stance,
           e.id            AS evidence_id,
           e.tenant_id     AS evidence_tenant,
           e.job_id        AS evidence_job,
           e.link_id       AS evidence_link,
           e.source_type, e.source_id, e.text_ref,
           e.provenance, e.freshness, e.trust, e.relevance, e.status,
           e.superseded_by
      FROM evidence_claims c
      LEFT JOIN evidence_claim_links l
             ON l.claim_id = c.id AND l.tenant_id = c.tenant_id
      LEFT JOIN evidence_items e
             ON e.id = l.evidence_id AND e.tenant_id = c.tenant_id
     WHERE c.tenant_id = :tenant_id
"""


def _item_from_row(row: Any) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=row.evidence_id,
        tenant_id=row.evidence_tenant,
        job_id=row.evidence_job,
        link_id=row.evidence_link,
        source_type=row.source_type,
        source_id=row.source_id,
        text_ref=row.text_ref,
        provenance=dict(row.provenance or {}),
        freshness=dict(row.freshness or {}),
        trust=row.trust,
        relevance=float(row.relevance or 0.0),
        status=row.status,
        superseded_by=row.superseded_by,
    )


def _claims_from_rows(rows: Sequence[Any]) -> list[Claim]:
    by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        entry = by_id.setdefault(
            row.claim_id,
            {
                "claim": Claim(
                    claim_id=row.claim_id,
                    tenant_id=row.claim_tenant,
                    job_id=row.claim_job,
                    link_id=row.claim_link,
                    subject=row.subject,
                    dimension=row.dimension,
                    claim=row.claim,
                ),
                "supports": [],
                "contradicts": [],
            },
        )
        if row.evidence_id is None:
            continue
        entry[
            "supports" if row.stance == STANCE_SUPPORTS else "contradicts"
        ].append(_item_from_row(row))

    built: list[Claim] = []
    for entry in by_id.values():
        base_claim: Claim = entry["claim"]
        # Ordered by trust and then by relevance, both descending. This is a
        # presentation order for an operator and for a prompt, and it is the one
        # place `relevance` is legitimately read.
        key = lambda item: (-trust_rank(item.trust), -item.relevance)  # noqa: E731
        built.append(
            Claim(
                claim_id=base_claim.claim_id,
                tenant_id=base_claim.tenant_id,
                job_id=base_claim.job_id,
                link_id=base_claim.link_id,
                subject=base_claim.subject,
                dimension=base_claim.dimension,
                claim=base_claim.claim,
                supporting_evidence=tuple(sorted(entry["supports"], key=key)),
                contradicting_evidence=tuple(sorted(entry["contradicts"], key=key)),
            )
        )
    return built


async def load_claim(
    session: AsyncSession, *, tenant_id: uuid.UUID, claim_id: uuid.UUID
) -> Claim | None:
    rows = list(
        await session.execute(
            text(_CLAIM_SELECT + " AND c.id = :claim_id"),
            {"tenant_id": str(tenant_id), "claim_id": str(claim_id)},
        )
    )
    claims = _claims_from_rows(rows)
    return claims[0] if claims else None


async def load_claims(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID | None = None,
) -> list[Claim]:
    """Every claim on one job, optionally narrowed to one application.

    The tenant and the job are both required arguments rather than optional
    filters. An unscoped "all claims" read has no legitimate caller, and making
    it impossible to express is cheaper than reviewing every caller that could.
    """
    sql = _CLAIM_SELECT + " AND c.job_id = :job_id"
    params: dict[str, Any] = {"tenant_id": str(tenant_id), "job_id": str(job_id)}
    if link_id is not None:
        sql += " AND c.link_id = :link_id"
        params["link_id"] = str(link_id)
    rows = list(await session.execute(text(sql), params))
    return sorted(
        _claims_from_rows(rows), key=lambda claim: (claim.dimension, claim.claim)
    )


#: Tables a `text_ref` may point at, mapped to the column holding the text. An
#: allowlist for the same reason `_SAFE_STAGE_KEYS` is one: the next person
#: adding a source should have to add a line here, rather than discovering that
#: the resolver happily dereferences anything with an id column.
_RESOLVABLE: dict[str, tuple[str, str]] = {
    "context_chunks": ("context_chunks", "content"),
    "assessment_messages": ("assessment_messages", "content"),
}


async def resolve_text(
    session: AsyncSession, *, tenant_id: uuid.UUID, item: EvidenceItem
) -> str | None:
    """Fetch the sentence an item points at.

    The single legitimate reason the text is not in the ledger: a recruiter with
    `view_review_screen` open on one claim, asking what it rests on. It goes
    back to the source table under the caller's own tenant scope, so the
    capability check that guards the transcript still guards this, and an
    unresolvable reference returns None rather than guessing.
    """
    table, row_id, _ = parse_text_ref(item.text_ref)
    target = _RESOLVABLE.get(table)
    if target is None or not row_id:
        return None
    table_name, column = target
    try:
        uuid.UUID(row_id)
    except ValueError:
        return None
    row = (
        await session.execute(
            text(
                f"SELECT {column} AS value FROM {table_name} "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": row_id, "tenant_id": str(tenant_id)},
        )
    ).first()
    return None if row is None else str(row.value)


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload))
