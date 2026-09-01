# FAILURES — what could not be done, verified, or reproduced

Maintained per the brief's instruction that this file must exist and must not be
empty. Updated 2026-08-07, after Phase 0.

---

## 1. Could not reproduce the Change 8 cross-tenant leak

**Tried:** live probe of `GET /api/v1/companies/me/profile` as four real
production tenants; a 10-request interleaved run on a warm connection pool; a
SQL sweep for duplicated `about_company` / `work_life` across tenants; a read of
the handler, the auth dependency chain and every cache in the repo; `pg_policies`
and `pg_roles` audit.

**Result:** every probe returned the correct tenant's data. RLS is enabled and
FORCED on 28 tables, the app role holds neither `SUPERUSER` nor `BYPASSRLS`, and
an owner-role read with no GUC returned 0 rows.

**Why this is still open, not closed.** A negative result on today's data is not
proof the reporter was wrong. Specifically unverified:

- I probed **one** endpoint family. Jobs, candidates, resumes, assessments and
  reports were **not** probed cross-tenant. The brief says to assume systemic
  until proven otherwise, and I have not proven otherwise for those.
- I never reproduced the reporter's exact session. I minted tokens; I did not
  log in through Firebase, get a chooser, pick Workify, and screenshot the
  result. **That is the single test that would settle it** and it needs the
  account holder or their credentials.
- The report mentions seeing ACRM's *About company* and *Work life* text.
  Workify has no `companies` row at all, so today it can only render nulls. If
  the reporter genuinely saw ACRM's prose under a Workify session, something
  happened that current data cannot explain and my conclusion is wrong.

**What would change my mind:** a screenshot with the browser URL and the
workspace indicator visible, or the approximate timestamp so I can pull the
matching Cloud Run request log.

---

## 2. Change 10 baseline is not adequate for a before/after claim

Captured 12 `curl` samples per endpoint from a single machine in India. That is
enough to show warm-path latency is 400–500 ms end-to-end; it is **not** enough
for p95/p99, and it cannot separate network round-trip from server time.

**Not captured:** Cloud Monitoring `request_latencies` percentiles over 24 h,
cold-start counts, Cloud SQL slow-query log, per-endpoint request volume.

**Consequence:** any latency work started now would be unfalsifiable, which is
exactly what the brief forbids. Change 10 should not begin until this exists.

---

## 3. Cloud SQL diagnostics required RLS bypass, and my first pass was wrong

My first production data audit reported "0 jobs, 0 company profiles, production is
empty". That was an artefact of my own method: `psql` connects as the table
owner, `FORCE ROW LEVEL SECURITY` applies to the owner, and with no
`app.tenant_id` set every policy denied every row.

Corrected with `set_config('app.bypass_rls','on',false)`. All figures in
`PHASE0_EVIDENCE.md` are post-correction. Recording it because anyone repeating
this audit will hit the same trap, and because an empty result from this database
is ambiguous by design.

---

## 4. Secret Manager `DATABASE_URL` is stale and misleading

`DATABASE_URL` (version 2, 2026-07-31T08:55) carries a 32-character password that
**does not authenticate**. The working credential is `POSTGRES_PASSWORD`
version 2 (17 chars), which matches the deployed revision's env var exactly.

Not currently harmful — `scripts/deploy.sh:141-149` composes `DATABASE_URL` from
`POSTGRES_PASSWORD` and never reads the `DATABASE_URL` secret. But it is a live
trap for the next operator who reaches for the obvious secret. Recommend
disabling that secret version or deleting the secret.

---

## 5. Not investigated at all in Phase 0

Stated plainly so it is not mistaken for coverage:

- **Change 9** (refinement loop) — not started. Note the repo already has
  `services/agent_loop.run_loop` (plan → execute → evaluate → reflect → verify,
  deterministic gates, `degraded` flag). Change 9 substantially overlaps it and
  should be scoped as *extend*, not *build new*.
- **Change 6** (PPI authenticity, PDF) — not started. Production holds only
  **2 reports**, both Sarkar Corp. The brief's 5-candidates-same-job comparison
  cannot be run on production data as it stands; it needs generated fixtures.
- **Change 3** (assessment relevance + counter) — not started.
- **Change 1** (Cloudinary → GCS) — only the data shape checked: 36 resumes, all
  Cloudinary, **0 missing `resume_public_id`**. The reported "missing its secure
  profile reference" error was **not reproduced** and its trigger is unknown.
- **Change 2** (PPI UI/UX) — not started.
- **Change 4** (proctoring) — not started, flag-off scaffolding only when it is.

---

## 6. Environment hazards that will bite anyone repeating this

- `gcloud` takes **2–4 minutes per invocation** on this machine. A ten-command
  sweep is a 30-minute job. Wrap in bounded background jobs; do not read a slow
  call as a hang.
- PowerShell `Invoke-WebRequest` **times out at 60 s** against the Cloud Run URL
  while `curl` returns the same URL in 0.5 s. Python `urllib` appears to share
  the failure. **Use `curl` for all HTTP evidence** or you will record false
  negatives.
- Host Python is **3.14.3**; the image and CI pin **3.12**. Host test runs are
  not representative.
- `backend/tests` has **no shared DB fixture** — each DB-backed file calls
  `_factory_or_skip()` and **skips silently** when no database is reachable. A
  green `pytest tests -q` on the host means the RLS and isolation tests did not
  run. Run inside `pickready-backend-1`, or report the skip count.
- `psql` client is **v13** against a **v16** server: fine for `SELECT`,
  `pg_dump` and some `\d` meta-commands will refuse.
- `/health` returns a hardcoded `{"status":"ok"}` and **touches no database**
  (`app/main.py:147-149`). It is not a liveness signal for anything but the
  process.

---

## 7. Change 9 LangSmith trace links cannot be retrieved

Change 9's loop-level LangSmith instrumentation is deployed and the production
runtime receives `LANGSMITH_API_KEY` from Secret Manager. A synthetic,
non-customer production loop was executed as Cloud Run Job execution
`pickready-migrate-cbht9`; the job completed successfully.

The current stored key returns HTTP 403 from the LangSmith sessions/runs API in
both the US and EU API regions. Consequently the live run cannot be read back
and an honest dashboard trace link cannot be attached. The SDK integration is
deliberately failure-tolerant, so this did not break the report loop.

Resolving this requires a valid key with read access to the owning LangSmith
workspace. No secret was written to the repository, and no customer data was
used in the verification execution.

---

## 8. Execution-session closeout (2026-08-07)

Several earlier entries above describe Phase 0 state and are retained for
auditability, but are now resolved:

- The systemic cross-tenant audit was completed under Change 8 and is a
  blocking real-Postgres CI suite.
- Changes 1, 2, 3, 5, 6, 7, 8 and 9 were implemented, staged, smoke-tested,
  promoted, and production-verified. Their individual evidence files contain
  the exact revisions and run ids.
- Change 10 now has a real 24-hour Cloud Monitoring baseline, cold-start count,
  per-route log percentiles, resource percentiles, and Cloud SQL review.
- `/health` now checks the database and the staged/production pipeline relies
  on it.
- The stale `DATABASE_URL` version 2 is disabled and deployments explicitly
  compose the runtime URL from `POSTGRES_PASSWORD`.

Items still honestly open:

1. The Change 10 after-window is approximately five minutes with two complete
   Monitoring minute points, not 24 hours. It is labeled as such in
   `LATENCY_BEFORE_AFTER.md`; a scheduled 24-hour follow-up is still needed for
   a controlled long-window comparison.
2. Cloud SQL had no slow-query logging or Query Insights during the baseline,
   so “zero entries” was not treated as proof of no slow queries. Query Insights
   is now enabled for future evidence.
3. Change 4 remains flag-off. Retention period, operational data-request
   process, and full legal review are deliberately unresolved.
4. Change 9’s LangSmith read-access issue in section 7 remains unresolved.
5. The user explicitly waived live-browser checks for this execution. No
   missing browser screenshot is represented as completed; DOM tests, direct
   production HTTP, SQL/object checks, deployed-bundle checks, and CI smoke
   evidence were used instead.
6. Host-only full pytest runs report two failures because the local Postgres
   credential is stale. The same enum/database tests pass in the deployment CI
   job against its real PostgreSQL service with migrations and RLS enabled.

The first Change 4 run (`31206381523`) was blocked before staging by the banned
em-dash source audit. The single punctuation offender in the new consent copy
was removed and the pipeline was rerun; no failed revision reached traffic.
