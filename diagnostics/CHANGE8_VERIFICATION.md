# Change 8 verification — identity/session clarity and the remaining RLS gap

Date: 2026-08-07 (Asia/Calcutta)
Release commit: `22fa85e23cc72e3dd5a03d49b58714adbdf74483`
GitHub Actions run: https://github.com/HanuLisa-Technologies-LLP/pickready/actions/runs/31178887248

## Result

Change 8 is deployed to production. The active workspace is now persistent in
the application shell, workspace changes require explicit confirmation, a
sensitive-page warning is shown for billing/profile/company routes, and all
workspace-owned React state is remounted under a user/tenant/role key after a
switch. Tenant fetches use `cache: "no-store"`.

The actual production database is at Alembic revision
`0043_tenant_user_rls`. Both identity tables that were the confirmed defence-in-
depth gap (`tenants` and `users`) have enabled and forced RLS policies.

The user explicitly waived further live-browser checks on 2026-08-07. The proof
below therefore uses production SQL, authenticated production HTTP, Cloud Run
revision state, the gated deployment smoke tests, and automated DOM/state tests.
`change8-acrm-workspace.png`, captured before that waiver, is retained only as
supplementary evidence and is not relied upon for completion.

## What shipped

- `backend/alembic/versions/0043_tenant_user_rls.py`
  - enables and forces RLS on `tenants` and `users`;
  - adds tenant policies using the transaction-scoped `app.tenant_id` GUC;
  - preserves the already-explicit `app.bypass_rls` path for structural
    pre-tenant identity lookup.
- `backend/app/core/db.py`
  - documents and constrains the identity-session bypass to authentication,
    refresh, chooser, and `/auth/me` resolution;
  - business endpoints continue to use tenant-scoped sessions.
- `backend/app/api/auth.py`
  - returns the active `workspace_name`;
  - exposes the authenticated user's available workspaces;
  - signs a single-use context token;
  - records the source user, selected tenant, selected workspace, role, and
    database timestamp in the append-only audit log.
- `frontend/components/app-shell.tsx`
  - shows the active workspace in desktop and mobile headers.
- `frontend/components/workspace-switcher.tsx`
  - displays only alternative memberships;
  - requires confirmation for every switch;
  - adds stronger copy on sensitive routes.
- `frontend/components/workspace-boundary.tsx`
  - remounts portal content under the selected user/tenant/role identity so
    tenant A component state cannot survive a tenant B switch.
- `frontend/lib/api.ts`
  - disables browser/Next fetch reuse for authenticated API data.
- `.github/workflows/deploy.yml`
  - adds blocking frontend component tests, lint, and production build alongside
    the real-Postgres backend test job.

## Production RLS proof

Connected through Cloud SQL Auth Proxy 2.22.0 on local port 5433 and queried the
production database directly:

```text
alembic_head
----------------------
0043_tenant_user_rls

table_name | rls_enabled | rls_forced | policyname
-----------+-------------+------------+--------------------------
tenants    | t           | t          | tenants_tenant_isolation
users      | t           | t          | users_tenant_isolation

tenants qual / with_check:
id = nullif(current_setting('app.tenant_id', true), '')::uuid
OR current_setting('app.bypass_rls', true) = 'on'

users qual / with_check:
tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
OR current_setting('app.bypass_rls', true) = 'on'

rolname       | rolbypassrls
--------------+-------------
pickready     | f
pickready_app | f
```

This is live policy output, not migration-source inspection.

## Production workspace-selection audit proof

The production `audit_log` contains the real ACRM-to-Specter selection exercised
after this release:

```text
at:                 2026-08-07 12:57:20.225962+00
actor_user_id:      20000000-0000-4000-8000-000000000010
tenant_id:          10000000-0000-4000-8000-000000000003
action:             context_selected
metadata_json:
  role:               hiring_manager
  source_user_id:     20000000-0000-4000-8000-000000000007
  workspace_name:     Specter & Co.
  selected_tenant_id: 10000000-0000-4000-8000-000000000003
```

This makes future workspace-confusion reports traceable to the selected
membership and time instead of being indistinguishable from an isolation leak.

## Authenticated production HTTP proof

Fresh 10-minute bearer tokens were minted locally from Secret Manager for the
two seeded memberships of the same email identity. Tokens and the signing secret
were kept in process variables and cleared after the probes.

```text
backend_url: https://pickready-backend-fcunsks2nq-el.a.run.app

/api/v1/auth/me — membership A
workspace_name: ACRM Corp
tenant_id:      10000000-0000-4000-8000-000000000002
role:           hiring_manager

/api/v1/auth/me — membership B
workspace_name: Specter & Co.
tenant_id:      10000000-0000-4000-8000-000000000003
role:           hiring_manager

/api/v1/jobs — membership A
rows:           10
first JD tenant: ACRM Corp
IDs include:    105763df-4b8e-5152-baed-81fa18af6ab3

/api/v1/jobs — membership B
rows:           10
first JD tenant: Specter & Co.
IDs include:    268bef41-0f84-56bd-850d-04376ba5aa4f
```

The two responses contained disjoint tenant-specific job IDs and their JD
content named the selected workspace. No ACRM JD was returned under the Specter
token.

## Automated isolation and state-reset tests

Fresh focused runs after production promotion:

```text
$ docker compose -f infra/docker-compose.yml exec -T backend \
    pytest tests/test_cross_tenant_isolation.py -q
..                                                                       [100%]
2 passed in 4.73s

$ npm test
Test Files  4 passed (4)
Tests       13 passed (13)
```

The new real-Postgres suite:

- seeds complete graphs for two tenants;
- alternates tenant A/B six times through one physical connection pool;
- checks tenants, users, jobs, candidates, job-candidate links, functional
  reports, report dimensions, job competencies, staff invites, companies, and
  resumes through profiles;
- asserts RLS is both enabled and forced and that the policies exist.

The frontend DOM test switches context without a full-page reload and asserts
that tenant A state is unmounted before tenant B content renders.

The complete pre-deploy local suite also passed:

```text
backend:  1274 passed (real PostgreSQL and Redis)
frontend: 13 passed; ESLint passed; Next.js production build passed
agent evaluation: all checks passed; lowest score 1.00
npm audit --omit=dev: 0 vulnerabilities
```

## Gated deployment and production state

Run `31178887248` completed successfully for the exact release SHA:

```text
Frontend tests, lint and build       success
Backend tests and agent evaluation  success
Build, migrate and stage            success
  Deploy (staged, no traffic)       success
  Smoke test staged revision        success
Production environment approval     approved
Promote to production               success
  Shift 100% traffic                success
  Smoke test production             success
```

Production revisions:

```text
backend:  pickready-backend-00109-qav   100% traffic
frontend: pickready-frontend-00104-cug  100% traffic
image tag for both:
22fa85e23cc72e3dd5a03d49b58714adbdf74483
```

The staged and production smoke jobs both passed `/health`, dashboard, jobs,
`/auth/me`, the API contract probe, and frontend availability before/after
traffic promotion.

## Root-cause disposition

Phase 0 disproved a systemic failure of the existing tenant-scoped business
tables and identified the observed company-profile report as ambiguous workspace
context among legitimate duplicate-email memberships. Change 8 closes both
confirmed residual risks:

1. workspace context and switching are now explicit, audited, and state-clearing;
2. the `tenants` and `users` identity tables now have the same database-enforced
   isolation posture as the business resources.

No mock, stub, TODO, hardcoded secret, or end-user numeric assessment score was
introduced.
