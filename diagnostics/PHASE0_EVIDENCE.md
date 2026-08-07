# PHASE 0 — BLOCKING DIAGNOSTIC

**Run date:** 2026-08-07
**Operator:** Claude Code session, against live `pick-ready-503913` / `asia-south1`
**Local HEAD:** `41e46e70be8a9132f5190170c30a7bfa30e04d5b`
**Raw SQL/RLS output:** [`phase0_raw_sql_rls_audit.txt`](phase0_raw_sql_rls_audit.txt)

> **Read the headline first.** Three of the four P0 changes in the brief are
> premised on symptoms this diagnostic could not reproduce against production,
> and two of them were already fixed by commits that are live right now. The
> evidence is below. Nothing here is a summary of a test run — every claim is a
> live HTTP response, a live SQL result, or a `gcloud` read against the
> deployed revision.

---

## 0.0 A schema correction that invalidates the brief's diagnostic SQL

Every query in the brief's Phase 0.2/0.3 references a schema this repo does not
have. This is not pedantry — running them produces seven `relation does not
exist` errors and zero evidence.

| Brief assumes | Actual | Source |
|---|---|---|
| `company_id` on every tenant-scoped table | **`tenant_id`**. There is no `company_id` column anywhere in the schema | 54 `tenant_id` declarations in `backend/app/models/*.py`; 0 `company_id` |
| `companies` is the tenant table | **`tenants`** is the customer. `companies` is the client-authored candidate-facing page, one row per tenant, itself tenant-scoped | `app/models/tenant.py:13-19`, `app/models/company.py:21` |
| `companies.slug`, `companies.is_active` | Neither column exists | grep across models + all 41 migrations |
| `feature_flags` table | **Does not exist.** There is no feature-flag mechanism in this product | zero hits repo-wide |
| GUC `app.current_company_id` | **`app.tenant_id`** (plus `app.bypass_rls`) | `app/core/db.py:90-102` |
| `applications` | `job_candidate_links` | `app/models/candidate.py:125` |
| `assessments` / `assessment_responses` | `assessment_conversations` / `assessment_messages` | `app/models/assessment.py:167,243` |
| `ppi_reports` | `functional_skills_reports` + `report_dimensions` | `app/models/assessment.py:256,298` |
| `ppi_frameworks` | `job_competencies` | `app/models/assessment.py:66` |
| `resumes` | columns on `profiles` (`resume_url`, `resume_public_id`, …) | `app/models/candidate.py:65-73` |
| `invitations` | `staff_invites` | `app/models/invite.py:86` |
| `job_skills`, `assessment_evidence` | Do not exist | — |

Consequence for the plan: **Change 5's premise cannot be diagnosed as written.**
"Which tenants are missing `feature_flags` rows" has no answer, because the table
has never existed. The real cause of the 500s is in §0.4 and is unrelated.

---

## 0.1 Deployment truth

```
$ gcloud run services list --project pick-ready-503913 --region asia-south1
NAME: pickready-backend    LATEST_READY_REVISION: pickready-backend-00102-lin
URL: https://pickready-backend-fcunsks2nq-el.a.run.app
NAME: pickready-frontend   LATEST_READY_REVISION: pickready-frontend-00097-mix
URL: https://pickready-frontend-fcunsks2nq-el.a.run.app

$ gcloud run worker-pools list --project pick-ready-503913 --region asia-south1
WORKER_POOL: pickready-beat    LAST DEPLOYED AT: 2026-08-07T05:40:00Z  BY: github-deployer@…
WORKER_POOL: pickready-worker  LAST DEPLOYED AT: 2026-08-07T05:39:44Z  BY: github-deployer@…

$ gcloud run jobs list --project pick-ready-503913 --region asia-south1
JOB: pickready-migrate     LAST RUN AT: 2026-08-07 05:37:53 UTC
JOB: pickready-grant-role  LAST RUN AT: 2026-07-31 11:47:21 UTC
```

Deployed images:

```
backend :  …/pickready/backend:41e46e70be8a9132f5190170c30a7bfa30e04d5b
frontend:  …/pickready/frontend:41e46e70be8a9132f5190170c30a7bfa30e04d5b
traffic  :  100% -> pickready-backend-00102-lin / pickready-frontend-00097-mix
```

**All five workloads exist and are current.** The image tag on both services is
byte-identical to local `HEAD`. `git status` is clean and
`git log origin/main..HEAD` is empty.

> **HEAD and production do NOT diverge.** The brief anticipated that divergence
> as the explanation for "fixed but not working". It is not the explanation here.

Two stale tagged revisions still hold traffic tags with 0%
(`pickready-backend-00025-fed` / `-00026-pog`, tags `staged-fd712f26beed`,
`staged-c3ff2f8bb349`). Harmless, but they keep old images pinned and
undeletable. Same on the frontend. Worth a cleanup, not a P0.

Deployed schema version, read from the production database:

```
alembic_version = 0041_carry_in_flight   (= repo head migration)
```

---

## 0.2 Tenant reality check

**Method note that matters.** My first pass reported "0 jobs, 0 company profiles,
production is empty". That was **wrong, and it was wrong in an instructive way**:
`psql` connects as `pickready`, which owns the tables, and every tenant-scoped
table carries `FORCE ROW LEVEL SECURITY`. With no `app.tenant_id` GUC set, the
policy correctly denied every row. The empty result was RLS working, not missing
data. Corrected figures below were taken with `set_config('app.bypass_rls','on')`.

Control, proving the point:

```
===== RLS-protected read WITHOUT bypass (owner role, no GUC) =====
 jobs_visible_without_guc
--------------------------
                        0
```

### Tenants (there are four, not three)

```
              id                  |     name      | status | is_demo |  industry  |     created_at
----------------------------------+---------------+--------+---------+------------+-------------------
10000000-…-000000000003           | Specter & Co. | active | t       | Other      | 2026-07-31 10:35
10000000-…-000000000002           | ACRM Corp     | active | t       | Other      | 2026-07-31 10:35
10000000-…-000000000001           | Sarkar Corp   | active | t       | Technology | 2026-07-31 10:35
bee37680-cf16-4dd7-b3eb-3ee453b86a57 | Workify Corp | active | f      | Technology | 2026-07-31 17:12
```

### Per-tenant data volume (RLS bypassed)

```
     name      | is_demo | users | jobs | links | company_page | reports | tenant_caps
---------------+---------+-------+------+-------+--------------+---------+-------------
 ACRM Corp     | t       |     3 |   10 |    33 |            1 |       0 |           0
 Sarkar Corp   | t       |     4 |   13 |    38 |            1 |       2 |           0
 Specter & Co. | t       |     3 |   10 |    32 |            1 |       0 |           0
 Workify Corp  | f       |     2 |    3 |     3 |            0 |       0 |          91
```

Readings:

- **Workify Corp has no `companies` row.** Its Company Profile page therefore
  renders its name and industry with three empty sections. This matters for §0.3.
- **Workify has 91 per-tenant `role_permissions` rows; the three demo tenants
  have 0** and fall through to the global template (which is complete — 22
  capabilities each for `client`/`hr_manager`/`recruiter`/`hiring_manager`). Both
  configurations resolve correctly; there is no permission gap.
- Job setup health is clean everywhere — **0 jobs stamped-but-empty**, across all
  four tenants. The `framework_generated_at`-without-competency-rows defect
  recorded in `claude.md` is not present in current production data.

```
     name      | jobs | stamped | stamped_but_EMPTY | approved | ready
---------------+------+---------+-------------------+----------+-------
 ACRM Corp     |   10 |      10 |                 0 |       10 |    10
 Sarkar Corp   |   13 |      13 |                 0 |       13 |    13
 Specter & Co. |   10 |      10 |                 0 |       10 |    10
 Workify Corp  |    3 |       3 |                 0 |        1 |     1
```

Workify's two unready jobs (`Probe Backend Engineer`,
`Verification Data Engineer`) sit at `questions_pending_review` — the PPI
framework approval gate, **working as designed**. That is a plausible source of
"the portal does not work for my company": nobody has approved those frameworks,
so those two jobs cannot invite candidates. It is a UX/onboarding problem, not a
fault.

---

## 0.3 RLS audit — and the Change 8 verdict

### Database layer: strong

- **28 tables have RLS ENABLED *and* FORCED.** Full list in the raw output.
- Policy shape is uniform and carries the `nullif` guard from migration 0034:
  ```sql
  (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::uuid)
  OR (current_setting('app.bypass_rls', true) = 'on')
  ```
- **The app role is not privileged:**
  ```
    rolname    | rolsuper | rolbypassrls | rolcanlogin
  -------------+----------+--------------+-------------
   pickready_app |    f    |      f       |      f
   pickready     |    f    |      f       |      t
   postgres      |    f    |      f       |      t
  ```
  `pickready` owns every table but is **not** a superuser and does **not** hold
  `BYPASSRLS`, so `FORCE ROW LEVEL SECURITY` genuinely binds it. Demonstrated by
  the control query above returning 0 rows.

**Eight tables have no RLS at all:** `tenants`, `users`, `audit_log`,
`otp_challenges`, `llm_provider_keys`, `pricing_plans`, `webhook_events`,
`alembic_version`. `tenants` and `users` both carry `tenant_id` and are
documented as intentionally global. This is the one genuine gap: `company_name`
and `industry` on the Company Profile response come from `tenants` and are
protected by application filtering alone. It is defensible but it inverts
`claude.md` rule 1, and no test covers it.

### Live cross-tenant probe against production

Tokens minted for four **real production accounts** using the same mechanism CI
already uses (`scripts/mint-smoke-token.py`), then `curl` against the live
backend. This is the endpoint named in the bug report.

```
PROBE 1 — GET /api/v1/companies/me/profile, one call per tenant

[Workify]  HTTP 200  {"tenant_id":"bee37680-…","company_name":"Workify Corp",
                      "industry":"Technology","about_company":null,"work_life":null,…}
[ACRM]     HTTP 200  {"tenant_id":"10000000-…-002","company_name":"ACRM Corp",
                      "about_company":"ACRM Corp builds customer relationship software…"}
[Sarkar]   HTTP 200  {"company_name":"Sarkar Corp","about_company":"We build IT solutions",…}
[Specter]  HTTP 200  {"company_name":"Specter & Co.","about_company":"Specter and Co. is…"}
```

```
PROBE 2 — INTERLEAVED x10 on a warm connection pool
(a stale GUC on a pooled connection would surface here)

  sent=ACRM     received=ACRM Corp        OK
  sent=Workify  received=Workify Corp     OK
  sent=Sarkar   received=Sarkar Corp      OK
  sent=Workify  received=Workify Corp     OK
  sent=Specter  received=Specter & Co.    OK
  sent=Workify  received=Workify Corp     OK
  sent=ACRM     received=ACRM Corp        OK
  sent=Specter  received=Specter & Co.    OK
  sent=Workify  received=Workify Corp     OK
  sent=Sarkar   received=Sarkar Corp      OK

  0 mismatches / 10
```

Data-contamination check — is one tenant's prose sitting in another's row?

```
===== about_company values shared by more than one tenant =====
                  about_prefix                  | tenants_sharing |     which
------------------------------------------------+-----------------+---------------
 ACRM Corp builds customer relationship softwa… |               1 | ACRM Corp
 Specter and Co. is a corporate advisory firm … |               1 | Specter & Co.
 We build IT solutions                          |               1 | Sarkar Corp
(work_life: same result — every value unique to one tenant)
```

### Verdict on Change 8

**I could not reproduce a cross-tenant leak, and I looked for it four ways:**
the read path (correctly filtered on `user.tenant_id` from the JWT, twice —
`app/api/companies.py:126-130` and `:166-180`), the database boundary (RLS
enabled, forced, and demonstrably denying the owner role), a warm-pool
interleaved live probe (10/10 correct), and the data itself (no duplicated
profile text).

**What does explain the report.** The account named in the bug report has **two
user rows in production**:

```
                  id                  |        email         |   role    | status  |    tenant
--------------------------------------+----------------------+-----------+---------+--------------
 20000000-0000-4000-8000-000000000006 | kvsr101112@gmail.com | recruiter | active  | ACRM Corp
 d581262c-197d-4591-b0fa-7c527b9d6e06 | kvsr101112@gmail.com | client    | invited | Workify Corp
```

`otp.eligible_login_users` (`app/services/otp.py:233-251`) treats **invited users
as eligible** — that is how an invite is redeemed. So this identity resolves to
*two* eligible workspaces, login returns a chooser rather than a session
(`app/api/auth.py:268-280`), and the two options are labelled by tenant name.
If the ACRM context is chosen — or a prior ACRM session cookie is still live —
the user sees ACRM's real profile while believing they are "logged in as
Workify". And because **Workify has no `companies` row**, its own profile page is
blank, which makes the ACRM content look like it has replaced theirs.

Two more emails have the same dual-account shape: `saravankumarmk@gmail.com`
(ACRM + Specter) and `126004238@sastra.ac.in` (Sarkar + Specter + platform BD).

This is a real and worth-fixing problem — a user cannot reliably tell which
workspace they are in — but it is an **identity and session-clarity defect, not a
tenant isolation failure**. The remediation in the brief (rebuild RLS, re-key
every cache, audit RSC payloads) would not have changed this behaviour.

**I am not closing Change 8 on this.** See "What would change my mind" below.

---

## 0.4 Live endpoint probe — and the real cause of the 500s

**Current state: every tenant returns 200 on every page the brief lists as broken.**

```
PROBE 3 — live, right now
  Workify  /api/v1/dashboard/summary   HTTP 200   {"jobs":[{"title":"Verification Platform Engineer"…
  Workify  /api/v1/jobs                HTTP 200   [{"title":"Verification Platform Engineer"…
  ACRM     /api/v1/dashboard/summary   HTTP 200   {"jobs":[{"title":"Java Backend Developer"…
  ACRM     /api/v1/jobs                HTTP 200   [{"title":"MERN Stack Developer"…
  Sarkar   /api/v1/dashboard/summary   HTTP 200   {"jobs":[{"title":"Prompt Engineer"…
  Sarkar   /api/v1/jobs                HTTP 200   [{"title":"Prompt Engineer"…
  Specter  /api/v1/dashboard/summary   HTTP 200   {"jobs":[{"title":"DevOps / Cloud Engineer"…
  Specter  /api/v1/jobs                HTTP 200   [{"title":"MERN Stack Developer"…
```

`GET /api/v1/companies/me/profile` also returns 200 for all four (§0.3).

### What the 500s actually were

Cloud Run error logs (14-day window) show the failure, dated **2026-08-01**:

```
LookupError: 'assessment_invited' is not among the defined enum values.
             Enum name: pipelinestatus.
             Possible values: rejected, shortlisted, hold, ..., joined
KeyError: 'assessment_invited'
```

crashing three handlers, in SQLAlchemy's row processor:

```
  File "/app/app/api/dashboard.py",  line 63, in dashboard_summary
  File "/app/app/api/candidates.py", line 816, in list_job_links -> line 88, in _latest_status
  File "/app/app/api/portal.py",     line 971, in my_applications
```

Migration 0018 widened `PIPELINE_STATUSES` from five values to ten; the Python
`PipelineStatus` enum kept the original five. The **first time a recruiter
invited anyone to an assessment**, that tenant's dashboard, candidate list and
candidate portal 500'd permanently. Tenants that had never sent an invitation
kept working — which is precisely the reported "only 3 companies work" shape, and
it is a data-dependent bug, not a provisioning gap.

**It is already fixed.** Commit `23767d7` *"fix: dashboard 500, lost invitations,
and per-tenant permissions below baseline"* added the missing values
(`app/models/enums.py:86`) and documented the failure in the docstring at
`app/models/enums.py:70-88`. That commit is in the deployed image.

```
$ gcloud logging read 'severity>=ERROR' --freshness=2d   # all services
(no rows)
```

**Zero production errors in the last 48 hours.**

### Change 7 — AI Reach, real diagnosis

The "Similar to our customers" segment searches PickReady's own tenants' jobs.
That corpus is 36 rows, and the demo tenants share one catalogue of 10 titles:

```
 ACRM / Sarkar / Specter each carry:
   AI / Generative AI Engineer, Data Analyst, Data Engineer,
   DevOps / Cloud Engineer, Full Stack Developer (.NET),
   Java Backend Developer, Machine Learning Engineer,
   MERN Stack Developer, Python Backend Developer, React Frontend Developer
```

**"Java Backend Developer" exists in all three tenants.** A search for
`Java Developer` that returns Machine Learning Engineer, Full Stack (.NET),
React Frontend, Data Analyst, MERN and Python Backend — while omitting the one
exact-stack match — is a genuine ranking defect, now reproducible against a known
corpus. The six titles the report lists as junk are simply the rest of that
catalogue, i.e. the result set is unranked or unthresholded.

For 7B, the reported string is `UNAVAILABLE_MESSAGE`
(`app/services/web_research.py:103`), **not** `UNCONFIGURED_MESSAGE` (`:95`).
That distinguishes a tripped circuit breaker (`_unavailable_until`,
`_consecutive_failures`, `:117-140`) from a missing `TAVILY_API_KEY`. The
breaker state is a module-level global, so it is per-instance and invisible
across Cloud Run replicas.

### Change 1 — storage

```
  provider   | n  | missing_secure_ref
-------------+----+--------------------
 cloudinary  | 36 |                  0
```

36 resumes, all Cloudinary, **none missing `resume_public_id`**. The reported
"This resume is missing its secure profile reference" therefore does not come
from a null reference in production data; it needs to be reproduced against a
specific resume before the migration is designed around it.

---

## 0.5 Frontend bundle check

The brief asks whether the technical question-bank finalization UI is still in
the deployed bundle. Searching the frontend source for `finali*`:

```
components/job-setup-review.tsx:574:  () => apiPost(`${BASE}/${jobId}/framework/finalize`)
lib/types.ts:356:   * question-bank UI, generation and finalization are fully automatic.
lib/firebase-session.ts:15,39   (unrelated — session finalization)
```

The only surviving finalize control is **`framework/finalize`**, which is the PPI
framework approval gate. Per `claude.md` that gate is *deliberately retained* —
only the technical question-bank half was removed on 2026-08-04. The deployed
backend agrees: `scripts/smoke-test.sh` asserts against `/openapi.json` that
`/assessments/jobs/{job_id}/questions` and `.../finalize` are **absent**, and that
check passes on every deploy including today's.

**Assessment: the control the reporter is seeing is the framework approval step,
which is supposed to be there.** If the intent was to remove that too, that is a
product decision reversing the 2026-07-30 rule, not a regression.

---

## 0.6 Baseline latency

Live production, 12 samples per endpoint, authenticated as Sarkar Corp, warm
instances, measured from this machine in India via `curl`:

| Endpoint | n | p50 | p95 | max | codes |
|---|---|---|---|---|---|
| `/health` | 12 | 390 ms | 817 ms | 817 ms | 200 |
| `/api/v1/jobs` | 12 | 434 ms | 499 ms | 499 ms | 200 |
| `/api/v1/dashboard/summary` | 12 | 417 ms | 537 ms | 537 ms | 200 |
| `/api/v1/auth/me` | 12 | 415 ms | 440 ms | 440 ms | 200 |
| `/api/v1/billing/config` | 12 | 378 ms | 579 ms | 579 ms | 200 |

**Honest limits of this baseline.** n=12 from a single client cannot produce a
trustworthy p99, and it includes ~350 ms of India→`asia-south1` round trip and
TLS that no server-side change will remove. Server time is the small remainder.
A defensible before/after for Change 10 needs Cloud Monitoring
`request_latencies` percentiles over a 24-hour window plus a cold-start count —
**that has not been captured yet, and Change 10 should not start until it is.**

Warm-path latency is already 400–500 ms end-to-end, so the largest available win
is cold starts (`min-instances`), which this sample deliberately does not measure.

---

## Bottom line for the execution order

| Brief item | Status after Phase 0 |
|---|---|
| **Change 8** — cross-tenant leak | **Not reproducible.** RLS enabled+forced on 28 tables, app role unprivileged, 10/10 interleaved live probes correct, no duplicated data. Reported symptom is explained by one email holding accounts in two tenants. Recommend re-scoping to identity/session clarity + the `tenants`/`users` RLS gap. |
| **Change 5** — tenant init failure | **Already fixed** by `23767d7` (`PipelineStatus` enum missing 5 of 10 values). All 4 tenants 200 on every listed page; 0 errors in 48 h. Real residue: Workify's 2 jobs await framework approval. |
| **Change 7** — AI Reach | **Confirmed real.** Corpus identified; `Java Backend Developer` exists and is being missed. 7B is a tripped breaker, not a missing key. |
| **Change 9 / 6 / 3 / 1 / 2 / 10 / 4** | Not yet investigated beyond the data above. Change 1's premise (missing secure refs) is not visible in production data. |

**`FAILURES.md` records what I could not verify.**
