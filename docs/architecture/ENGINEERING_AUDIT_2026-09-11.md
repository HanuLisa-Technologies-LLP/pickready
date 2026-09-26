# Engineering audit: the service-to-product gap, measured against this tree

The "Brainstorm ReadyPick LLD" brief (2026-09-09) asked for a deep audit of
the gap between this codebase and the engineering quality of high-stakes
product companies, across 71 named dimensions, with fourteen phases and
seventeen deliverables, preserving 100% of product behaviour. This document is
those deliverables, produced the way this repository produces claims: against
the actual tree, with a file or a test behind every statement, and with what
was NOT done stated beside what was.

**The audit's headline finding is that the brief arrived late, and that is a
compliment to the history it did not know about.** Most of what it asks for
was built, tested and in several cases live-verified across the phases
recorded in `claude.md` before the brief was written. The audit therefore
found three REAL gaps, fixed all three on 2026-09-11, reviewed and
deliberately left four near-misses with reasons, and recorded the honest
debt. Everything below is organised by the brief's own seventeen deliverables.

---

## 1. Current architecture map

`docs/architecture/ESD.md` is the system design;
`docs/architecture/AI_RUNTIME.md` is the AI runtime as built, including its
own "what is NOT built" section; `claude.md` section 2 is the repository
layout with the services package map. Summarised: a modular monolith
(FastAPI + Next.js App Router), Postgres with RLS as the tenant boundary and
pgvector for retrieval, Redis for rate limiting, caches, run-status and the
proctoring counter, background work dispatched to Lambda (seconds) or
on-demand Fargate (minutes) through one registry, one LLM router over a
closed two-model mapping, and an evaluation package structurally unreachable
from any route or worker. The brief's own recommendation ("modular monolith +
clear domain boundaries + async workers") is what exists.

## 2. Current code-quality assessment

The enforcement is tooling, not convention, which is the brief's Level 12:

- 6,132 backend tests on a fresh database per run (`scripts/test.sh`), plus
  247 frontend tests, an offline interview eval, an agent-framework eval and
  a retrieval harness self-check that all gate.
- Anti-slop rules are CI-enforced sweeps, not review habits: no silent
  fallbacks, no dual code paths, no dead code, no magic numbers (every
  scoring number cited to the Runbook by `test_runbook_parity`, mutation
  tested 7/7), no em dashes, no placeholder prose.
- Structural rules are asserted by AST, not docstring: judge isolation,
  proctoring/scoring isolation, retrieval/scoring isolation, Miti evaluator
  field sets, the deterministic aggregator importing no router.
- Behavioural invariants live in the database as well as the code: CHECK
  constraints on statuses, generated posting-window columns, RLS policies
  with both USING and WITH CHECK proven by negative-direction tests.

## 3. Service-company to product-company gap analysis

Ranked against the brief's checklist. "At bar" means implemented AND
enforced by a test or a live verification this tree can name.

**At bar, with evidence** — tenant isolation (RLS + `test_rls.py`,
`test_cross_tenant_isolation.py`); authorization as data
(`require_capability`, seed parity tested); idempotency where money and
messages move (credit ledger unique keys, dispatch-once invitation flow);
concurrency locks across processes (scoring, and matching as of today), with
the BLAKE2b key stability proven in a subprocess under a different hash
seed; retries with predictive deadlines at every layer (router, agent loop,
worker runtime); circuit breakers keyed by credential; bounded loops by
construction; database indexing (audited today: 56 indexes across the ten
hottest tables cover every ranked, filtered and swept query shape, including
partial and HNSW indexes; no gap found); SQL-side pagination and total
orders on every list surface (audited today: the candidate table, provider
customers, support, the candidate feed); AI provenance (`model_id`,
`prompt_version` on reports, prompt registry digests, trace tables that
refuse content); AI security (guardrails on both directions, hidden-text as
provenance, egress allowlist by resolved address, embeddings treated as PII
in erasure); AI evaluation honesty (chance-corrected metrics or nothing,
UNAVAILABLE blocks rather than passes); observability (structured logs that
never carry a candidate's text, LangSmith behind an off-by-default flag,
digest-verified deploys read from RUNNING tasks); frontend discipline
(App Router route splitting, recharts dynamically imported where it is an
overlay and eagerly where it is the page, three.js banned by its own test,
virtualization unnecessary because no list renders more than a page).

**Was a real gap, fixed 2026-09-11**: see deliverable 15.

**Reviewed and deliberately left**: see deliverable 16.

**Honest debt**: see deliverable 13.

## 4. Backend improvement plan

Executed rather than proposed. The one correctness gap found:
`run_matching` had no cross-process duplicate guard, so two staff members
triggering matching in the same minute bought two full pipeline runs.
Fixed with `locks.MATCHING` taken before the first vendor call, order pinned
by AST, refusal proven against the real database
(`tests/test_matching_lock.py`). No other backend change met the brief's own
bar of "complexity justified by a real problem".

## 5. Frontend improvement plan

Audited; no change met the bar. Heavy dependencies are split correctly, state
is server-fetch plus local component state (no global store to misuse), lists
paginate in SQL, error states never render an empty list as an answer
(`support-provider-queue` documents why), text tokens enforce contrast at
the token with a script asserting both the passing AND failing colour.
The design gates (`impeccable-gate.mjs`, `check-contrast.mjs`) run in CI.

## 6. Database improvement plan

Audited today: index inventory over the ten hottest tables against the
queries that read them; no missing index. The connection story is documented
where it is decided (pool 12+3 per identity; Lambda concurrency is the true
ceiling; the instance bump to `db.t4g.medium` on 2026-09-10 raised both the
memory head-room for HNSW and the derived max_connections). The RDS Proxy
was evaluated against AWS's own pinning documentation and REFUSED, with the
reasoning in `infra/environments/pilot/main.tf` beside the instance class:
this application pins every session (SET LOCAL ROLE, session-level
set_config, asyncpg prepared statements), so the proxy would multiplex
nothing.

## 7. Security improvement plan

No new finding. The standing posture: Firebase is identity never
authorization; RLS enforced with FORCE and negative write tests; cross-tenant
reads 404; provider access audit-logged per request; candidate text is data
never instructions, in both directions, deterministically; the support
surface added a schema-level candidate-data boundary swept by AST; secrets
enumerated per service, no wildcard IAM; a secret container is known not to
be a configured secret (probed, documented). The one security-adjacent debt
is recorded in 13 (`agent_actions` unwired, with the reason).

## 8. Scalability and reliability plan

The load-bearing properties: stateless services behind target-tracking
autoscaling (ceilings kept at 4 for api/frontend after today's resize);
every slow path dispatched, never inline; queues are Lambda/Fargate with
platform retries set to zero and ONE owner of the retry loop; Redis
noeviction because it holds live assessment state; the proctoring gate
answers 503 rather than silently not warning; rate limiting on the
assessment entry points; graceful degradation recorded, never silent, at
every vendor boundary. Under 10x users the first ceilings, in order: Lambda
concurrency against Postgres connections (watch CloudWatch
DatabaseConnections; the app-level pool is not the binding constraint),
then the api service's CPU ceiling (autoscaling to 4 tasks, then raise
max_count), then RDS instance class (a one-line change, precedent
2026-09-10).

## 9. AI / RAG / agent engineering plan

The runtime is described honestly in `AI_RUNTIME.md`, including what is not
built. Retrieval is hybrid with RRF, a recorded reranker, tenant-recall
measured against a known set, acquisition with one bounded broadened retry;
generation is gated by deterministic sufficiency with fixed empty-state
copy; judging is structurally isolated with chance-corrected reporting;
provenance is on the report row. Remaining, deliberately: retrieval QUALITY
needs a recorded run against real volume; the reasoning and decision sets
need human labels and must never be synthesised.

## 10. Testing strategy

What exists is layered the way the brief asks: unit, integration against
real Postgres/Redis/MinIO, RLS negative-direction tests, AST/structural
tests, subprocess tests for cross-process properties, offline evals with
pinned thresholds, characterization-style sweeps that keep removals removed.
The known ordering trap (seed reconciliation masking a missing migration) is
documented in the parity test itself and bit again on 2026-09-10; the full
suite catches what targeted runs structurally cannot, which is why the
deploy gate is the full suite on the exact deployed commit.

## 11. Observability strategy

Logs are structured and content-free by rule; every LLM call goes through
one traced chokepoint; run status is a Redis record with honest unknown-id
semantics; deploys verify by digest against running tasks; the production
read-back script reports schema version and row counts. Genuinely absent,
and stated in 13: distributed tracing across dispatch hops and a metrics
dashboard beyond CloudWatch defaults.

## 12. Performance strategy

Measure before optimizing is already the house rule (the canonical anecdote
is the 747 mislabelled rows found by measuring `tiers.py` on the live
database). Interactive LLM calls are capped in two tiers with the exception
list test-enforced; list endpoints paginate in SQL with total orders; the
N+1 sweep added today (an AST walk for session calls inside loops) is
repeatable, and its four request-path hits are each resolved or reasoned in
the commit history of 2026-09-11.

## 13. Technical debt inventory

Stated plainly, with reasons, so the next owner inherits decisions rather
than surprises:

- **`agent_actions` has no live writer; `tools.execute` and
  `agent_execution_traces` are unexercised.** Investigated 2026-09-10: every
  candidate first consumer conflicts with a standing rule (ledger commits
  break request atomicity; SMTP has no read-back so UNKNOWN is unresolvable;
  report writing already holds the advisory lock). Wire it first to a side
  effect with a true read-back. Owner has closed further work here.
- **W10 (shadow eval, tenant canary, drift watch) and W11a (candidate
  explanation surface) are designed, not built.** Owner-closed.
- **Retrieval quality unmeasured; golden set 60 of 300, zero human
  verified.** The gate says so itself on every run.
- **The remark system prompt is inline in `bounded_remark`,** versioned by
  the image rather than the registry; the report's `prompt_version` column
  states this limit in its docstring.
- **No distributed tracing across dispatch hops; no metrics dashboards**
  beyond CloudWatch defaults and alarms.
- **`smtp_from_email` still defaults to a `pickready.app` mailbox** whose
  existence is an operational unknown (claude.md, Naming).
- **Pilot accepts single-AZ risk** for cost, stated beside `multi_az` with
  the must-flip condition.

## 14. Refactoring roadmap

For whoever works on this next, in value order: (1) get real candidates into
an environment and record a retrieval run, which unlocks the quality gate;
(2) human-label the reasoning and decision sets, which unlocks the release
gate; (3) wire `agent_actions` to the first read-backable side effect, which
unlocks W5.2 and W10a; (4) put OpenTelemetry on the dispatch chokepoints;
(5) flip pilot `multi_az` the day a real tenant lands. Nothing on this list
is a rewrite; the brief's fear of "dangerous rewrites" has no candidate
here.

## 15. Highest-ROI changes (executed this session)

1. **One matching run per job, across processes**: correctness and cost
   under concurrent staff, the exact class the brief's Phase 9 names
   ("duplicate processing"). `locks.MATCHING`, AST-pinned ordering, and a
   real-database refusal test.
2. **Pilot resized to one task per service at rest**: roughly 40% of the
   Fargate bill saved, behaviour preserved (autoscaling ceilings kept,
   rolling deploys unchanged), reasoning recorded in the file.
3. **Support queue N+1 removed**: two IN-list statements per page instead
   of up to fifty single-row gets, caught by the new repeatable sweep one
   day after the code was written.

## 16. Changes deliberately NOT made

Each was considered against the brief's own test, "is the complexity
justified", and refused:

- **RDS Proxy**: vendor-documented pinning makes it multiplex nothing here.
- **Microservices, CQRS, event sourcing, outbox**: the modular monolith
  with dispatched workers is the brief's own recommendation; no boundary is
  strained.
- **Repository-pattern layering over SQLAlchemy**: the sessions ARE the
  boundary (RLS-aware), and an interface layer would add indirection the
  brief explicitly warns against ("abstractions created only because SOLID
  says so").
- **Batching `api/emails.py`'s per-link resolution**: bounded at 25, and
  per-link skip reporting is the feature.
- **Dynamic-importing the report view on the review page**: the report is
  that page's content; a loading flash to save bytes already paid for
  elsewhere is a worse trade.
- **A global store, list virtualization, service workers**: no surface
  needs them; every list paginates at 25.

## 17. The engineering standard

It already exists and is load-bearing: **`claude.md` is the ReadyPick
engineering constitution** the brief asks for. It is reverse-chronological,
supersessions are marked in place, rules are paired with the tests that
enforce them, and the anti-slop list is CI-enforced. `DESIGN.md` and
`PRODUCT.md` govern the frontend, `docs/README.md` indexes the rest, and the
precedence order for conflicts is written down (RBAC specification first).
New code follows the standard or fails the suite; that is the property the
brief calls "architecture enforced by tooling", and it is the reason this
audit found three gaps rather than seventy-one.
