"""Re-embed every vector onto voyage-context-4, through a shadow column.

    python -m app.scripts.reembed --dry-run          # the work plan
    python -m app.scripts.reembed --status           # what each column holds
    python -m app.scripts.reembed --confirm          # needs VOYAGE_API_KEY
    python -m app.scripts.reembed --evaluate         # retrieval quality

WHAT THIS EXISTS TO FIX
-----------------------
`profiles.embedding`, `jobs.embedding`, `jobs.reach_embedding` and
`context_chunks.embedding` are all `vector(1024)` and hold vectors from two
different models. The platform ran a self-hosted BGE-M3 endpoint, then
consolidated on voyage-context-4, and Voyage's 1024-dimension output meant the
swap needed no migration to remain STORABLE. It does not follow that the
vectors are COMPARABLE: a cosine distance between a BGE-M3 vector and a Voyage
vector is a number with no meaning, and nothing about it looks wrong. Retrieval
has been mixing two spaces ever since, and ranking quality degrades silently
rather than failing.

Migration 0062 added `<column>_model`, `<column>_contract_version` and
`<column>_generated_at` so "which model produced this vector" is answerable by
query. It deliberately did NOT backfill them to Voyage: nobody can tell the two
apart retroactively, so a NULL model on a non-NULL vector is the honest record
of "produced by an unrecorded model", and it is exactly the predicate this
script selects on.

THE SHADOW COLUMN IS THE WHOLE SAFETY ARGUMENT
------------------------------------------------
Writing straight into the live column means a run that fails at 60 percent
leaves an index serving 60 percent Voyage and 40 percent BGE-M3, which is
strictly worse than the state it started in: before the run the mixture was at
least stable, and afterwards it changes under every retry. So every vector is
written into `<column>_shadow` first, the whole set is counted and its
dimensionality checked, and only then does one UPDATE per tenant move the
shadow into the live column and stamp the provenance. A failed run leaves
shadow values and an untouched index, and re-running picks up where it stopped.

THE PROVENANCE IS STAMPED AT THE SWAP, NEVER AT THE WRITE
------------------------------------------------------------
Stamping `embedding_model = 'voyage-context-4'` while the live column still
holds the old vector would make the provenance column lie for the entire
duration of the run, and a crash in the middle would leave it lying
permanently. The stamp and the vector move in the same statement.

RETRY AND BACKOFF ARE THE ROUTER'S, NOT A SECOND COPY
--------------------------------------------------------
spec-doc6 section 10.1 rule 12: one implementation per concept. The exponential
curve, its cap, the 401/403-is-terminal rule, the 429-is-transient rule and the
vendor's own `retry-after` all come from `config.llm_providers` and
`services.llm_router`, which is where this platform's retry discipline already
lives. What is local to this script is only the batching and the resume
predicate, which are properties of walking a table rather than of talking to a
vendor.

IT CANNOT RUN IN THIS PHASE, AND IT SAYS SO
---------------------------------------------
spec-doc6 decision D6: there is no Voyage key. `--confirm` refuses without one
rather than falling through to `embeddings`' deterministic dev fallback, which
would write pseudo-random unit vectors into every profile and stamp them
`voyage-context-4`. That is the single worst outcome available here: retrieval
would return confident nonsense and the provenance column, the one thing built
to make this answerable, would assert that a real model produced it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.llm_providers import (
    BACKOFF_MAX_SECONDS,
    EMBEDDING_MODEL,
    backoff_seconds,
    classify_status,
)
from app.core.config import get_settings
from app.core.db import get_session_factory, superadmin_scope
from app.services import llm_router
from app.services.embeddings import EMBEDDING_DIM, MAX_BATCH, EmbeddingError, embed

logger = logging.getLogger("pickready.reembed")

#: OUR version of how the embedded text is built: the width, the input type and
#: the template. The vendor does not version a model id, so without this a
#: change to WHAT is embedded would be invisible while the model string stayed
#: identical. Bump it whenever a text builder below changes, and every row
#: built by the previous builder becomes stale by query rather than by memory.
CONTRACT_VERSION = "v1-1024-doc"

#: How many rows are read, embedded and shadow-written per round. Bounded by the
#: vendor's own per-request ceiling, which `embeddings.embed` already splits on;
#: matching it here keeps one database round trip per one vendor round trip so a
#: failure costs one batch rather than one page of batches.
BATCH_ROWS = MAX_BATCH

#: A credential failure trips on the FIRST occurrence, unlike a 429 or a 5xx.
#: Same rule the router follows, and for the same reason: no amount of waiting
#: fixes a revoked key, and a re-embed that kept retrying one would spend its
#: whole budget proving it.
CREDENTIAL_ATTEMPTS = 1
#: Transient failures get the router's budget.
TRANSIENT_ATTEMPTS = 4


class ReembedRefused(RuntimeError):
    """The run cannot start, or cannot safely continue."""


# ── Targets ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    """One vector column, and how to rebuild it.

    `text_sql` is a SQL expression producing the text to embed FROM THE ROW,
    rather than a Python builder, for one reason: the text must be reproducible
    from the database alone. A builder in Python means re-embedding depends on
    an application module that may have changed since the vector was written,
    and the contract version could not then be trusted to describe it.
    """

    table: str
    column: str
    tenant_column: str | None
    text_sql: str
    #: Rows with no source text can never be embedded and are reported as such
    #: rather than counted as remaining work forever.
    has_text_sql: str
    description: str

    @property
    def shadow(self) -> str:
        return f"{self.column}_shadow"

    @property
    def model_column(self) -> str:
        return f"{self.column}_model"

    @property
    def contract_column(self) -> str:
        return f"{self.column}_contract_version"

    @property
    def generated_column(self) -> str:
        return f"{self.column}_generated_at"

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


#: The JD text `matching._jd_text` builds, expressed in SQL so it can be rebuilt
#: from the row. Compensation is excluded, and that is not incidental: ESD 16
#: and `matching._strip_compensation` keep salary out of every model prompt, and
#: an embedding is a model prompt.
_JD_TEXT_SQL = """
    concat_ws(E'\\n',
        'Job title: ' || j.title,
        CASE WHEN j.department IS NOT NULL THEN 'Department: ' || j.department END,
        CASE WHEN j.level IS NOT NULL THEN 'Level: ' || j.level END,
        CASE WHEN j.jd_json ? 'role' THEN 'Role: ' || (j.jd_json ->> 'role') END,
        CASE WHEN j.jd_json ? 'responsibilities'
             THEN 'Responsibilities: ' || (j.jd_json ->> 'responsibilities') END,
        CASE WHEN j.jd_json ? 'education'
             THEN 'Education: ' || (j.jd_json ->> 'education') END,
        CASE WHEN j.jd_json ? 'skills' THEN 'Skills: ' || (j.jd_json ->> 'skills') END,
        CASE WHEN j.jd_json ? 'experience_years'
             THEN 'Experience Years: ' || (j.jd_json ->> 'experience_years') END,
        j.jd_markdown
    )
"""

#: `bd_leads.role_embedding_text`, in SQL. Title plus the parsed skill list, and
#: nothing else: AI Reach ranks ROLES against each other, so a full JD would
#: swamp the signal with company-specific prose.
_REACH_TEXT_SQL = """
    concat_ws(E'\\n',
        'Job role: ' || lower(trim(j.title)),
        'Primary skills: ' || COALESCE(
            (SELECT string_agg(DISTINCT lower(trim(s)), ', ' ORDER BY lower(trim(s)))
               FROM jsonb_array_elements_text(
                   CASE WHEN jsonb_typeof(j.jd_json -> 'skills') = 'array'
                        THEN j.jd_json -> 'skills' ELSE '[]'::jsonb END
               ) AS s),
            ''
        )
    )
"""

TARGETS: tuple[Target, ...] = (
    Target(
        table="profiles",
        column="embedding",
        tenant_column="source_tenant_id",
        text_sql="p.resume_text",
        has_text_sql="p.resume_text IS NOT NULL AND length(trim(p.resume_text)) > 0",
        description="Candidate resume vectors. The semantic half of ranking.",
    ),
    Target(
        table="jobs",
        column="embedding",
        tenant_column="tenant_id",
        text_sql=_JD_TEXT_SQL,
        has_text_sql="j.title IS NOT NULL",
        description="JD vectors. The query side of candidate retrieval.",
    ),
    Target(
        table="jobs",
        column="reach_embedding",
        tenant_column="tenant_id",
        text_sql=_REACH_TEXT_SQL,
        has_text_sql="j.title IS NOT NULL",
        description=(
            "AI Reach role vectors. A separate COLUMN in a separate space by "
            "design; only the model is shared."
        ),
    ),
    Target(
        table="context_chunks",
        column="embedding",
        tenant_column="tenant_id",
        text_sql="c.content",
        has_text_sql="c.content IS NOT NULL AND length(trim(c.content)) > 0",
        description="Chunk-level retrieval index for the context engine.",
    ),
)

#: The alias each target's SQL uses, so one query builder serves all four.
_ALIAS = {"profiles": "p", "jobs": "j", "context_chunks": "c"}


def target_for(key: str) -> Target:
    for target in TARGETS:
        if target.key == key:
            return target
    raise ReembedRefused(f"No such target: {key}. Known: {[t.key for t in TARGETS]}")


# ── SQL builders (pure) ──────────────────────────────────────────────────────


def _alias(target: Target) -> str:
    return _ALIAS[target.table]


def stale_predicate(target: Target) -> str:
    """Rows whose live vector was not produced by the current contract.

    A row is stale when it HAS a vector and its provenance is anything other
    than this model at this contract version. A row with no vector at all is
    not this script's problem: the matching pipeline backfills those on demand,
    and claiming them here would make the work plan report a number nobody
    asked for.
    """
    a = _alias(target)
    return (
        f"{a}.{target.column} IS NOT NULL AND ("
        f"{a}.{target.model_column} IS DISTINCT FROM :model OR "
        f"{a}.{target.contract_column} IS DISTINCT FROM :contract)"
    )


def pending_predicate(target: Target) -> str:
    """Stale rows whose shadow has not been written yet. The resume predicate.

    Idempotent and resumable with no state file, because it is a property of
    the ROWS rather than of a progress record somebody has to keep in step. A
    run that died half way leaves the shadow written for what it finished, and
    the next run selects exactly the remainder.
    """
    a = _alias(target)
    return f"({stale_predicate(target)}) AND {a}.{target.shadow} IS NULL"


def _scope(target: Target, alias: str) -> str:
    if not target.tenant_column:
        return "TRUE"
    return f"{alias}.{target.tenant_column} = CAST(:tenant AS uuid)"


def count_sql(target: Target, predicate: str) -> str:
    a = _alias(target)
    return (
        f"SELECT COUNT(*) FROM {target.table} {a} "
        f"WHERE {_scope(target, a)} AND ({predicate})"
    )


def select_batch_sql(target: Target) -> str:
    """One page of work, ordered by primary key so two runs walk the same way."""
    a = _alias(target)
    return (
        f"SELECT {a}.id AS id, {target.text_sql} AS source_text "
        f"FROM {target.table} {a} "
        f"WHERE {_scope(target, a)} AND ({pending_predicate(target)}) "
        f"AND ({target.has_text_sql}) "
        f"ORDER BY {a}.id LIMIT :limit"
    )


def write_shadow_sql(target: Target) -> str:
    return (
        f"UPDATE {target.table} SET {target.shadow} = CAST(CAST(:vector AS text) AS vector({EMBEDDING_DIM})) "
        "WHERE id = CAST(CAST(:id AS text) AS uuid)"
    )


def shadow_ready_sql(target: Target) -> str:
    """How many shadow vectors are present AND the right width.

    Dimensionality is checked rather than assumed. `embeddings.embed` already
    refuses a malformed batch, so this is the second of two independent checks
    on the same property, and it is the one that would catch a shadow written by
    an older version of this script with a different `output_dimension`.
    """
    a = _alias(target)
    return (
        f"SELECT COUNT(*) FROM {target.table} {a} "
        f"WHERE {_scope(target, a)} AND {a}.{target.shadow} IS NOT NULL "
        f"AND vector_dims({a}.{target.shadow}) = {EMBEDDING_DIM}"
    )


def shadow_present_sql(target: Target) -> str:
    a = _alias(target)
    return (
        f"SELECT COUNT(*) FROM {target.table} {a} "
        f"WHERE {_scope(target, a)} AND {a}.{target.shadow} IS NOT NULL"
    )


def swap_sql(target: Target) -> str:
    """Move the shadow into the live column and stamp the provenance, at once.

    One statement, so there is no instant in which the vector is new and the
    provenance still says otherwise, or the reverse. The shadow is cleared in
    the same statement so a second run has nothing left to swap.
    """
    a = _alias(target)
    return (
        f"UPDATE {target.table} {a} SET "
        f"{target.column} = {a}.{target.shadow}, "
        f"{target.model_column} = :model, "
        f"{target.contract_column} = :contract, "
        f"{target.generated_column} = now(), "
        f"{target.shadow} = NULL "
        f"WHERE {_scope(target, a)} AND {a}.{target.shadow} IS NOT NULL"
    )


# ── The provider call, with the router's retry discipline ────────────────────


async def embed_with_backoff(
    texts: Sequence[str],
    *,
    embedder: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> list[list[float]]:
    """One batch, retried on the router's curve and classified by its rules.

    `embedder` exists so the loop can be exercised against a recorded provider
    without a credential. It is never a substitute for a result: `--confirm`
    refuses to start without a key, so a substituted embedder can only ever be
    reached from a test.
    """
    call = embedder or (lambda batch: embed(batch))
    pause = sleep or asyncio.sleep
    attempt = 0
    last: Exception | None = None
    while True:
        attempt += 1
        try:
            return await call(list(texts))
        except Exception as exc:  # noqa: BLE001 -- classified on the next line
            last = exc
            status = llm_router.status_of(exc)
            kind = classify_status(status) if status is not None else "transport"
            budget = (
                CREDENTIAL_ATTEMPTS if kind == "credential" else TRANSIENT_ATTEMPTS
            )
            retryable = llm_router.is_retryable(exc) or isinstance(exc, EmbeddingError)
            logger.warning(
                "reembed.batch_failed kind=%s status=%s attempt=%d of %d",
                kind, status, attempt, budget,
            )
            if kind == "credential" or not retryable or attempt >= budget:
                raise ReembedRefused(
                    f"The embedding provider failed and the attempt budget is "
                    f"spent: {kind}"
                    + (f" (HTTP {status})" if status is not None else "")
                    + f" after {attempt} attempt(s). Nothing was swapped, so the "
                    "live index is exactly as it was; re-run to continue from "
                    "the rows already shadowed."
                ) from last
            wait = llm_router.retry_after_seconds(exc)
            await pause(min(wait, BACKOFF_MAX_SECONDS) if wait else backoff_seconds(attempt + 1))


# ── Status and the work plan ─────────────────────────────────────────────────


@dataclass
class TargetStatus:
    target: Target
    tenant: str
    tenant_name: str
    total: int = 0
    stale: int = 0
    pending: int = 0
    shadowed: int = 0
    without_text: int = 0

    @property
    def ready_to_swap(self) -> bool:
        return self.pending == 0 and self.shadowed > 0


@dataclass
class Plan:
    statuses: list[TargetStatus] = field(default_factory=list)
    keys_present: bool = False

    @property
    def rows(self) -> int:
        return sum(status.pending for status in self.statuses)

    @property
    def batches(self) -> int:
        return sum(-(-status.pending // BATCH_ROWS) for status in self.statuses)

    @property
    def by_target(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status in self.statuses:
            out[status.target.key] = out.get(status.target.key, 0) + status.pending
        return out

    @property
    def by_tenant(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status in self.statuses:
            out[status.tenant_name] = out.get(status.tenant_name, 0) + status.pending
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": EMBEDDING_MODEL,
            "contract_version": CONTRACT_VERSION,
            "rows": self.rows,
            "batches": self.batches,
            "batch_size": BATCH_ROWS,
            "by_target": self.by_target,
            "by_tenant": self.by_tenant,
            "keys_present": self.keys_present,
        }


async def _scalar(session: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    return int((await session.execute(text(sql), params)).scalar() or 0)


async def _tenants(session: AsyncSession) -> list[dict[str, str]]:
    rows = await session.execute(
        text("SELECT id::text AS id, name FROM tenants ORDER BY name")
    )
    return [dict(row) for row in rows.mappings().all()]


async def collect_status(session: AsyncSession) -> Plan:
    plan = Plan(keys_present=bool((get_settings().voyage_api_key or "").strip()))
    tenants = await _tenants(session)
    for target in TARGETS:
        a = _alias(target)
        for tenant in tenants:
            params = {
                "tenant": tenant["id"],
                "model": EMBEDDING_MODEL,
                "contract": CONTRACT_VERSION,
            }
            status = TargetStatus(
                target=target, tenant=tenant["id"], tenant_name=tenant["name"]
            )
            status.total = await _scalar(session, count_sql(target, "TRUE"), params)
            status.stale = await _scalar(
                session, count_sql(target, stale_predicate(target)), params
            )
            status.pending = await _scalar(
                session,
                count_sql(
                    target, f"({pending_predicate(target)}) AND ({target.has_text_sql})"
                ),
                params,
            )
            status.without_text = await _scalar(
                session,
                count_sql(
                    target,
                    f"({pending_predicate(target)}) AND NOT ({target.has_text_sql})",
                ),
                params,
            )
            status.shadowed = await _scalar(
                session,
                f"SELECT COUNT(*) FROM {target.table} {a} "
                f"WHERE {_scope(target, a)} AND {a}.{target.shadow} IS NOT NULL",
                params,
            )
            plan.statuses.append(status)
    return plan


def render_plan(plan: Plan) -> str:
    out = [
        "Re-embedding work plan (estimates, not a quotation)",
        f"  model                     : {EMBEDDING_MODEL}",
        f"  contract version          : {CONTRACT_VERSION}",
        f"  rows to re-embed          : {plan.rows}",
        f"  batch size                : {BATCH_ROWS}",
        f"  provider round trips      : {plan.batches}",
        f"  credentials present       : {'yes' if plan.keys_present else 'no'}",
        "",
        "  per target:",
    ]
    for key, count in sorted(plan.by_target.items()):
        out.append(f"    {key}: {count}")
    out.append("")
    out.append("  per tenant:")
    for name, count in sorted(plan.by_tenant.items()):
        out.append(f"    {name}: {count}")
    stranded = [s for s in plan.statuses if s.without_text]
    if stranded:
        out.append("")
        out.append(
            "  stale rows with no source text, which cannot be re-embedded and "
            "are reported rather than retried forever:"
        )
        for status in stranded:
            out.append(
                f"    {status.target.key} / {status.tenant_name}: {status.without_text}"
            )
    return "\n".join(out)


# ── The run ──────────────────────────────────────────────────────────────────


@dataclass
class TenantResult:
    tenant: str
    tenant_name: str
    target: str
    embedded: int = 0
    swapped: int = 0
    verified_dimension: int = 0
    swapped_at: datetime | None = None


async def reembed_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    target: Target,
    tenant: dict[str, str],
    embedder: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
) -> TenantResult:
    """Shadow every stale row for one tenant, verify, then swap.

    The swap is a separate transaction from the shadow writes on purpose. A
    shadow write that fails leaves partial shadow values and an untouched live
    column, which is a resumable state; folding both into one transaction would
    make an interruption throw away work that was already paid for at the
    vendor.
    """
    params = {
        "tenant": tenant["id"],
        "model": EMBEDDING_MODEL,
        "contract": CONTRACT_VERSION,
    }
    result = TenantResult(
        tenant=tenant["id"], tenant_name=tenant["name"], target=target.key
    )
    while True:
        async with session_factory() as session:
            async with superadmin_scope(session):
                rows = (
                    await session.execute(
                        text(select_batch_sql(target)),
                        {**params, "limit": BATCH_ROWS},
                    )
                ).mappings().all()
        if not rows:
            break
        vectors = await embed_with_backoff(
            [str(row["source_text"]) for row in rows], embedder=embedder
        )
        if len(vectors) != len(rows):
            raise ReembedRefused(
                f"{target.key}: asked for {len(rows)} vectors and received "
                f"{len(vectors)}. Nothing is written, because a batch whose "
                "order cannot be trusted would attach each row's vector to the "
                "next row."
            )
        async with session_factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for row, vector in zip(rows, vectors):
                        if len(vector) != EMBEDDING_DIM:
                            raise ReembedRefused(
                                f"{target.key}: a vector of width {len(vector)} "
                                f"was returned where {EMBEDDING_DIM} is required."
                            )
                        await session.execute(
                            text(write_shadow_sql(target)),
                            {
                                "id": str(row["id"]),
                                "vector": "[" + ",".join(repr(float(v)) for v in vector) + "]",
                            },
                        )
        result.embedded += len(rows)
        logger.info(
            "reembed.batch target=%s tenant=%s embedded=%d",
            target.key, tenant["name"], result.embedded,
        )

    async with session_factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                present = await _scalar(session, shadow_present_sql(target), params)
                correct = await _scalar(session, shadow_ready_sql(target), params)
                if present != correct:
                    raise ReembedRefused(
                        f"{target.key} / {tenant['name']}: {present} shadow "
                        f"vectors are present and only {correct} are "
                        f"{EMBEDDING_DIM}-dimensional. Nothing is swapped, so "
                        "the live column still holds one consistent space."
                    )
                remaining = await _scalar(
                    session,
                    count_sql(
                        target,
                        f"({pending_predicate(target)}) AND ({target.has_text_sql})",
                    ),
                    params,
                )
                if remaining:
                    raise ReembedRefused(
                        f"{target.key} / {tenant['name']}: {remaining} rows are "
                        "still un-shadowed. A partial swap is what the shadow "
                        "column exists to prevent."
                    )
                result.verified_dimension = correct
                swapped = await session.execute(text(swap_sql(target)), params)
                result.swapped = swapped.rowcount or 0
                result.swapped_at = datetime.now(timezone.utc)
    logger.info(
        "reembed.swapped target=%s tenant=%s rows=%d",
        target.key, tenant["name"], result.swapped,
    )
    return result


async def run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    apply: bool,
    only: str | None = None,
    embedder: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
) -> tuple[Plan, list[TenantResult]]:
    async with session_factory() as session:
        async with superadmin_scope(session):
            plan = await collect_status(session)
            tenants = await _tenants(session)
    if not apply:
        return plan, []
    if embedder is None and not plan.keys_present:
        raise ReembedRefused(
            "--confirm needs VOYAGE_API_KEY. Without it `embeddings.embed` "
            "returns deterministic pseudo-random unit vectors, and this script "
            "would write them into every profile and stamp them "
            f"'{EMBEDDING_MODEL}'. Retrieval would then return confident "
            "nonsense and the provenance column, the one thing built to make "
            "this answerable, would assert that a real model produced it."
        )
    targets = [target_for(only)] if only else list(TARGETS)
    results: list[TenantResult] = []
    for target in targets:
        for tenant in tenants:
            results.append(
                await reembed_tenant(
                    session_factory, target=target, tenant=tenant, embedder=embedder
                )
            )
    return plan, results


# ── Retrieval quality on a fixed evaluation set ──────────────────────────────
#
# spec-doc6 section 7: report before and after retrieval quality on at least 50
# query/expected-result pairs, so "search quality will be mixed until they are
# redone" becomes a measured statement rather than a guess.
#
# The set and the scorer live here; the NUMBERS the real model produces do not
# exist yet and are listed in VERIFICATION_PENDING.md. What can be measured
# without a vendor is that the harness computes what it claims to: run it with
# an embedder whose geometry is known and the metrics move the way they must.

EVAL_SET_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "reembed" / "retrieval_eval.json"
)
#: The cut-off recall is reported at. Three, because the product surfaces a
#: short ranked list and a document that arrives eleventh is not found.
RECALL_AT = 3


@dataclass
class RetrievalScore:
    pairs: int
    recall_at_k: float
    mean_reciprocal_rank: float
    k: int = RECALL_AT

    def as_dict(self) -> dict[str, Any]:
        return {
            "pairs": self.pairs,
            "k": self.k,
            "recall_at_k": round(self.recall_at_k, 4),
            "mean_reciprocal_rank": round(self.mean_reciprocal_rank, 4),
        }


def load_eval_set(path: Path | None = None) -> dict[str, Any]:
    source = path or EVAL_SET_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    pairs = payload["pairs"]
    documents = payload["documents"]
    if len(pairs) < 50:
        raise ReembedRefused(
            f"{source} carries {len(pairs)} query/expected pairs and spec-doc6 "
            "section 7 requires at least 50. A smaller set makes a recall "
            "figure that moves on one document look like a quality change."
        )
    known = {doc["id"] for doc in documents}
    missing = sorted(
        {expected for pair in pairs for expected in pair["expected"]} - known
    )
    if missing:
        raise ReembedRefused(
            f"{source}: these pairs expect documents the corpus does not have: "
            + ", ".join(missing)
        )
    return payload


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


async def score_retrieval(
    embedder: Callable[[list[str]], Awaitable[list[list[float]]]],
    *,
    payload: dict[str, Any] | None = None,
    k: int = RECALL_AT,
) -> RetrievalScore:
    """Recall at k and mean reciprocal rank over the fixed evaluation set.

    Both are reported because they answer different questions. Recall says
    whether the right document is on the first screen; MRR says how far up it
    is, and a change that moves every answer from third to first improves MRR
    while leaving recall flat.
    """
    data = payload or load_eval_set()
    documents = data["documents"]
    doc_vectors = await embedder([doc["text"] for doc in documents])
    query_vectors = await embedder([pair["query"] for pair in data["pairs"]])
    hits = 0
    reciprocal = 0.0
    for pair, query_vector in zip(data["pairs"], query_vectors):
        ranked = sorted(
            range(len(documents)),
            key=lambda index: cosine(query_vector, doc_vectors[index]),
            reverse=True,
        )
        expected = set(pair["expected"])
        positions = [
            rank
            for rank, index in enumerate(ranked, start=1)
            if documents[index]["id"] in expected
        ]
        best = min(positions) if positions else 0
        if best and best <= k:
            hits += 1
        if best:
            reciprocal += 1.0 / best
    total = len(data["pairs"])
    return RetrievalScore(
        pairs=total,
        recall_at_k=hits / total,
        mean_reciprocal_rank=reciprocal / total,
        k=k,
    )


# ── Entry point ──────────────────────────────────────────────────────────────


async def _status(_: argparse.Namespace) -> int:
    async with get_session_factory()() as session:
        async with superadmin_scope(session):
            plan = await collect_status(session)
    print(render_plan(plan))
    print()
    print("| target | tenant | rows | stale | pending | shadowed |")
    print("|---|---|---:|---:|---:|---:|")
    for status in plan.statuses:
        print(
            f"| {status.target.key} | {status.tenant_name} | {status.total} "
            f"| {status.stale} | {status.pending} | {status.shadowed} |"
        )
    return 0


async def _dry_run(_: argparse.Namespace) -> int:
    plan, _results = await run(get_session_factory(), apply=False)
    print(render_plan(plan))
    if not plan.keys_present:
        print()
        print(
            "NOT EXECUTED. VOYAGE_API_KEY is absent, so `--confirm` refuses. "
            "This plan is the work that is waiting; see VERIFICATION_PENDING.md."
        )
    return 0


async def _confirm(args: argparse.Namespace) -> int:
    started = time.monotonic()
    plan, results = await run(get_session_factory(), apply=True, only=args.only)
    print(render_plan(plan))
    print()
    for result in results:
        if result.embedded or result.swapped:
            print(
                f"  {result.target} / {result.tenant_name}: embedded "
                f"{result.embedded}, verified {result.verified_dimension}, "
                f"swapped {result.swapped}"
            )
    print(f"swapped {sum(r.swapped for r in results)} vectors in "
          f"{time.monotonic() - started:.1f}s")
    return 0


async def _evaluate(_: argparse.Namespace) -> int:
    payload = load_eval_set()
    if not (get_settings().voyage_api_key or "").strip():
        print(
            f"Evaluation set loaded: {len(payload['pairs'])} query/expected "
            f"pairs over {len(payload['documents'])} documents."
        )
        print(
            "NOT SCORED. Scoring needs VOYAGE_API_KEY. `embeddings.embed` would "
            "otherwise return deterministic pseudo-random vectors, and a recall "
            "figure computed from those measures nothing while looking exactly "
            "like a measurement. See VERIFICATION_PENDING.md."
        )
        return 0
    score = await score_retrieval(lambda batch: embed(batch), payload=payload)
    print(json.dumps(score.as_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reembed",
        description="Re-embed every vector onto voyage-context-4 through a shadow column.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="what each column holds")
    mode.add_argument("--dry-run", action="store_true", help="the work plan")
    mode.add_argument("--confirm", action="store_true", help="run it, needs a key")
    mode.add_argument("--evaluate", action="store_true", help="retrieval quality")
    parser.add_argument("--only", help="one target, as table.column")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.status:
            return asyncio.run(_status(args))
        if args.dry_run:
            return asyncio.run(_dry_run(args))
        if args.confirm:
            return asyncio.run(_confirm(args))
        return asyncio.run(_evaluate(args))
    except ReembedRefused as refusal:
        print(f"REFUSED: {refusal}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
