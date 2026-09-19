# PERFORMANCE_REPORT.md

**Scope.** Database access patterns, caching, API shape, and frontend bundle
cost. **Date.** 2026-09-17.
**Method.** Static read of query call sites against the indexes actually
declared in `backend/app/models/` and `backend/alembic/versions/`, plus a
production `next build`.

**Read the last section first.** No profiling was done against a live
environment, no `EXPLAIN ANALYZE` was run against pilot, and no bundle-size
delta was measured. Everything below is reasoned from code and verified by
tests, which is a weaker claim than a measurement and is labelled as such.

---

## Severity summary

| Severity | Count | Fixed | Documented, not changed |
|---|---|---|---|
| HIGH | 2 | 1 | 1 |
| MEDIUM | 5 | 3 | 2 |
| LOW | 3 | 0 | 3 |

---

## HIGH

### P1. The agent tool-result cache was not tenant scoped. FIXED

**File.** `backend/app/services/tools/executor.py`, `_cache_key`.

**Root cause.** The key hashed the tool name plus the validated input payload and
nothing else. The cache is read BEFORE the handler runs, therefore before the RLS
session that would otherwise refuse the row, so a tool call scoped to tenant B
could be served tenant A's cached result whenever two tenants shared an input
shape. This is a performance feature with a correctness hole, which is why it is
first. The repo had already named it in a `KNOWN_TENANT_GAPS` ledger in
`backend/tests/test_cache_tenant_keying.py` rather than hidden it.

**Fix.** The tenant id is now part of the digest and a visible key segment
(`pickready:vN:tool:<name>:<tenant>:<digest>`). It was already available at the
read point as `context.tenant_id`, so no public signature changed. Two deliberate
refusals rather than defaults: a missing tenant RAISES
`coalescing.TenantScopeMissing`, and an explicitly UNSCOPED call is **not cached
at all** rather than sharing a "nobody said" bucket.

**Verification.** VERIFIED. The `KNOWN_TENANT_GAPS` entry was deleted, so the
existing AST sweep now enforces it; new tests assert on key VALUES (two tenants,
one input shape, two keys) and two end-to-end firewall tests assert tenant B is
never served tenant A's entry and that its call does reach the handler. The test
fixture replaces the cache because the real one no-ops without Redis, which would
have made the assertion unfailable.

### P2. `matching._semantic_stage` defeats the HNSW index. NOT CHANGED, deliberately

**File.** `backend/app/services/matching.py`, roughly lines 397 to 419.

**Root cause.** The vector distance is computed inside
`SELECT DISTINCT ON (p.candidate_id) ... ORDER BY p.candidate_id, dist`, and the
outer query then re-sorts and applies `LIMIT`. Postgres's HNSW shortcut only
applies when the index's own `ORDER BY ... LIMIT` is the TOP-LEVEL operation, so
this materialises and per-candidate-sorts the entire filtered set before the
limit can help. As the opted-in databank grows this becomes a full distance
computation over every consenting profile, once per matching run.

**Why it was left alone.** The fix is structural, not an index: it means picking
the best profile per candidate before the ANN collapse. That changes the SQL
deciding WHICH profile represents a candidate, and this product's rule is that a
candidate linked to a job is always scored and retrieval is a ranking prior only.
A rewrite that subtly changed the candidate set would be a hiring behaviour
change wearing a performance fix's clothes, and it cannot be proven equivalent
without a dataset that does not exist in any environment. Recorded here as the
top remaining performance item, with a correctness precondition attached.

---

## MEDIUM

### P3. N+1 in the email recipient loader. FIXED

**File.** `backend/app/api/emails.py`, `_load_targets`.

**Root cause.** Three `session.get()` round trips per recipient inside a loop over
`link_ids`: fifty recipients was 150 round trips. The sibling bulk path in
`backend/app/api/outreach.py` had already been fixed for exactly this and carries
a comment saying so, so this was also a one-implementation-per-concept violation.

**Fix.** Three batched `.in_(...)` reads plus dict lookups, matching the
`outreach.py` shape. Return type, ordering, duplicate handling, rejection set and
rejection wording all unchanged.

**Verification.** VERIFIED, and the test was proven to discriminate: with the fix
stashed the new query-count test failed `assert 10 == 3`, and passed once
restored. `_load_targets` had zero test coverage before; it now has six tests.

### P4. `jobs.embedding` had no ANN index. FIXED

**File.** new migration `backend/alembic/versions/0098_jobs_embedding_hnsw_index.py`.

**Root cause.** The column was added in `0001_initial` and never indexed, unlike
`profiles.embedding`, `jobs.reach_embedding` and `context_chunks.embedding`.

**Honest scoping.** This is currently a NON-ISSUE in production: the only caller,
`services/job_relevance.py`, prefilters with `j.id = ANY(:job_ids)`, a primary
key lookup. The index is defensive, so that the first caller who writes
`ORDER BY j.embedding <=> :vec LIMIT n` does not sequential-scan every job in
every tenant. HNSW build cost is trivial next to debugging that later.

**Verification.** PARTIAL. The chain resolves to a single head and the migration
applies cleanly to a fresh test database via `scripts/test.sh`. It has **not**
been applied to pilot.

### P5. `recharts` loaded into the Review Screen's initial bundle. FIXED

**File.** `frontend/components/profile-review.tsx`.

**Root cause.** It statically imported `functional-skills-report`, which imports
`recharts` at module scope. The Review Screen is opened by every recruiter, so
recharts loaded even when no report was viewed. The sibling
`components/ppi-report-modal.tsx` already solved this with `next/dynamic`: one
component, two call sites, only one fixed.

**Verification.** PARTIAL. `tsc`, `eslint` and a successful `next build` of
`/org/review` pass, and the change is byte-for-byte the pattern already shipping
in the sibling. **The bundle-size delta was not measured**: this build uses
Turbopack and emits no `app-build-manifest.json` to attribute chunks from. No
vitest test renders `ProfileReview`, so there is no behavioural test either.

### P6. `audit_log` has no index serving the cross-tenant ordered scan. NOT CHANGED

`backend/app/models/tenant.py` declares only
`Index("ix_audit_log_tenant_at", "tenant_id", "at")`. The Provider's
`GET /admin/audit-log` allows `tenant_id=None` and then orders by `at desc, id`
with no leading-column filter, which that composite index cannot serve. Left
alone because an index should follow a demonstrated access pattern, and it is
unknown whether the cross-tenant view is used at all in practice. Add
`Index("ix_audit_log_at", "at", "id")` if it is.

### P7. Three RLS policies use a correlated EXISTS. NOT CHANGED, correct as-is

`candidate_projects`, `candidate_updates` and `bgv_inquiries` each check
`EXISTS (SELECT 1 FROM candidates c WHERE c.id = <table>.candidate_id AND ...)`.
Every other RLS policy in this schema is plain `tenant_id` equality. This is a
deliberate, documented trade: candidates are shareable across tenants via the
databank, so tenant membership cannot be a column check. The join key is the
primary key of `candidates`, so each check is an index lookup rather than a scan.
Recorded because these three are structurally the most expensive RLS surfaces in
the schema, and any future high-volume write path against them should be measured
with `EXPLAIN ANALYZE` before linear scaling is assumed.

---

## LOW, all documented and not changed

- **P8. OFFSET pagination on `audit_log` and `candidate_updates`.** The repo's own
  rule prefers keyset pagination on large tables, and `api/conversations.py`
  already does it correctly with a comment citing the reason. Cosmetic today;
  both tables are small.
- **P9. Bounded N+1 in `matching.py` around line 1219.** `session.get(Profile, ...)`
  per row, but bounded by `:limit` over a small calibration sample used for
  narrative text, not for scoring.
- **P10. Six large `"use client"` pages** (1063, 1058, 836, 808, 739, 712 lines).
  Architecturally consistent: every portal uses cookie-session plus client
  `apiGet`, so converting one without moving session handling to the server would
  relocate the problem rather than fix it.

---

## Verified as already correct, and deliberately not "fixed"

- **Connection pooling is appropriately sized.** `pool_size=12`,
  `max_overflow=3`, `pool_pre_ping`, `pool_recycle=1800`, one uvicorn process per
  Fargate task. At the configured `max_count=4` that is 60 connections against a
  `db.t4g.medium`, which is comfortable. If `max_count` is ever raised
  materially, the connection maths must be re-derived rather than assumed.
- **The heavy ML dependencies are already correctly isolated.**
  `@tensorflow/tfjs`, `@tensorflow-models/coco-ssd`, `face-api.js` and
  `@mediapipe/tasks-vision` are imported ONLY inside the two proctoring web
  workers and loaded via `new Worker(new URL(...))`, so they never enter the
  main-thread chunk of any page that is not proctoring. This is correct; do not
  "optimise" it.
- **RDS Proxy remains refused**, on the vendor's own documented behaviour: this
  application issues `SET LOCAL ROLE` per tenant transaction, a session-level
  `set_config` on worker connections, and asyncpg caches prepared statements, so
  effectively every session pins and multiplexing cannot occur.
- **Every cache key now contains the tenant id**, enforced by an AST sweep with an
  empty exemption ledger (see P1).

---

## Frontend, incidental to the security work

- **Next.js 16.2.12 to 16.3.5 and sharp to 0.35.4.** Driven by CVEs, not by
  performance, but it carries whatever the minor brings.
- **Three typefaces are now self-hosted through `next/font`** rather than the
  previous single Google-hosted Inter. This removes a render-blocking third-party
  request and lets `font-src 'self'` be complete in the CSP. Plausibly helps LCP;
  **not measured**.

---

## What could NOT be verified

1. **Nothing was profiled against a live environment.** No `EXPLAIN ANALYZE`
   against pilot, no slow-query log, no APM trace. Every claim about a query's
   cost is reasoned from the SQL and the declared indexes.
2. **No bundle-size delta was measured** (P5), for the Turbopack reason above.
3. **No Lighthouse or Core Web Vitals run** was taken, before or after.
4. **Migration 0098 has not been applied to pilot**, only to a fresh test
   database.
5. **Pilot holds three demo tenants, thirty jobs and zero candidates.** Every
   performance claim that depends on volume is therefore untested at volume, in
   any environment. That is the single biggest gap in this report.
