# PickReady CI/CD, setup instructions

A staged rollout on Cloud Run: every push to `main` builds, migrates and
creates revisions that serve **nobody**, smoke tests those revisions on their
private tag URL, and stops. Live traffic moves only after a human approves the
`production` environment in GitHub.

```
push to main
   |
   +-- deploy-staged ------------------------------------------------+
   |     build backend + frontend images, tagged with the git SHA    |
   |     push to Artifact Registry                                   |
   |     run pickready-migrate to completion (blocking)              |
   |     deploy backend + frontend with --no-traffic --tag=staged-*  |
   |     deploy worker + beat (no traffic concept, see caveat below) |
   |     smoke test https://staged-<sha>---<service>.run.app         |
   +-----------------------------------------------------------------+
   |
   |   [ MANUAL APPROVAL, GitHub Environment "production" ]
   |
   +-- promote-to-prod ----------------------------------------------+
         shift 100% traffic to the exact revisions that were tested  |
         remove the staged tag                                       |
         smoke test the live URLs                                    |
   +-----------------------------------------------------------------+
```

---

## Files

| File | Run where | Purpose |
|---|---|---|
| `scripts/setup-wif-once.sh` | Local, once, as a project owner | Creates `github-deployer`, the WIF pool and provider |
| `scripts/deploy.sh` | CI (and locally) | Build, push, migrate, deploy every workload |
| `scripts/smoke-test.sh` | CI (and locally) | Probe a revision; non-200 fails the build |
| `scripts/promote.sh` | CI (and locally) | Shift traffic; also does rollback |
| `.github/workflows/deploy.yml` | GitHub Actions | Wires the three together with the approval gate |

`infra/gcp/deploy.sh` is unchanged and still owns **provisioning** (creating
Cloud SQL, Memorystore, the Artifact Registry repository, the runtime service
account, and the initial Secret Manager entries). `scripts/deploy.sh` owns
**deployment** and assumes that provisioning already ran.

---

## Step 1, provision the infrastructure (once)

Skip this if the project is already live, which it is for
`pick-ready-503913`.

```bash
./infra/gcp/deploy.sh infra
```

This creates `pickready-runtime@pick-ready-503913.iam.gserviceaccount.com`,
which `scripts/setup-wif-once.sh` requires and refuses to work without.

---

## Step 2, one-time Workload Identity Federation setup

Run locally, authenticated as a project owner. There is no key file anywhere in
this flow: a downloaded JSON key never expires, never rotates, and is readable
by any workflow that can read repository secrets. A WIF token lives for minutes
and is bound to one repository.

```bash
gcloud auth login
GITHUB_REPO=your-org/pickready ./scripts/setup-wif-once.sh
```

It prints the two values you need in step 3. It is idempotent, so rerun it
freely.

What it grants `github-deployer@pick-ready-503913.iam.gserviceaccount.com`:

| Role | Scope | Why |
|---|---|---|
| `roles/run.admin` | project | Deploy services, worker pools and jobs; shift traffic |
| `roles/artifactregistry.writer` | project | Push images |
| `roles/cloudsql.client` | project | Attach the Cloud SQL instance to a revision |
| `roles/secretmanager.secretAccessor` | project | List secret names, read `POSTGRES_PASSWORD` |
| `roles/iam.serviceAccountUser` | **the runtime SA only** | Say `--service-account=pickready-runtime` at deploy time |

`serviceAccountUser` is bound on the runtime service account rather than on the
project on purpose. At project scope it would let the deployer impersonate
every service account in the project, including any future one with broader
rights.

---

## Step 3, repository secrets

GitHub, **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GCP_WIF_PROVIDER` | The provider path printed by the setup script, `projects/<number>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_DEPLOY_SA` | `github-deployer@pick-ready-503913.iam.gserviceaccount.com` |
| `TEST_BEARER_TOKEN` | A valid PickReady access JWT for a test hiring-manager account (see step 4) |

Optional, only if you would rather keep the Firebase web config in GitHub than
in Secret Manager. `deploy.sh` looks in the process environment first, then
`frontend/.env.local`, then Secret Manager, so setting them in **either** place
works:

`NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`,
`NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET`,
`NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID`, `NEXT_PUBLIC_FIREBASE_APP_ID`.

To keep them in Secret Manager instead (recommended, one source of truth):

```bash
while IFS='=' read -r k v; do
  case "$k" in NEXT_PUBLIC_FIREBASE_*)
    printf '%s' "$v" | gcloud secrets create "$k" --data-file=- --replication-policy=automatic 2>/dev/null \
      || printf '%s' "$v" | gcloud secrets versions add "$k" --data-file=- ;;
  esac
done < frontend/.env.local
```

These are **not** secrets in the security sense: Firebase web config is
delivered to every browser that loads the app by design. They live in Secret
Manager for distribution, not for confidentiality.

---

## Step 4, mint the `TEST_BEARER_TOKEN`

The smoke test needs a real access JWT so it can prove that authentication,
tenancy and permission resolution all still work on the new revision, not just
that the process started.

Access tokens live 15 minutes by default (`JWT_ACCESS_TTL_MINUTES`), so a raw
sign-in token is useless as a repository secret. Mint a long-lived one for a
dedicated test hiring-manager account instead:

```bash
gcloud run jobs execute pickready-migrate --region asia-south1 --wait \
  --args=python --args=-c --args='
import asyncio
from app.core.security import create_access_token
from app.core.db import get_sessionmaker
from sqlalchemy import text

async def main():
    sm = get_sessionmaker()
    async with sm() as s:
        row = (await s.execute(text(
            "SELECT id, tenant_id, role FROM users WHERE email = :e"
        ), {"e": "smoke-test@yourcompany.com"})).first()
        print(create_access_token(str(row[0]), str(row[1]), row[2], ttl_minutes=60*24*90))

asyncio.run(main())
'
```

Confirm the argument names against `backend/app/core/security.py` before
running this: the helper's signature is the contract, and it has changed once
already in this codebase.

Then:

1. Create a real hiring-manager user for `smoke-test@yourcompany.com` in a
   dedicated test tenant. Do not point the smoke test at a live customer's
   tenant: `/api/v1/dashboard/summary` and `/api/v1/jobs` read that tenant's
   data on every deploy.
2. Store the printed token as `TEST_BEARER_TOKEN`.
3. Put a calendar reminder to rotate it before it expires. A 401 in the smoke
   test halts every deploy, and the script says so explicitly when it sees one.

---

## Step 5, the approval gate

**This is the step that is easy to skip and silently defeats the whole
design.** The `promote-to-prod` job declares `environment: production`, but an
environment with no required reviewer approves itself instantly.

GitHub, **Settings → Environments → New environment**:

1. Name it exactly `production`.
2. Tick **Required reviewers** and add at least one person.
3. Optionally restrict deployment branches to `main`.

Verify by pushing a trivial commit: the run should reach `promote-to-prod` and
sit in **Waiting** until someone clicks Review deployments.

---

## Step 6, first run

```bash
git push origin main
```

Watch the Actions tab. On success the job summary carries both staged URLs; you
can open the staged frontend in a browser and click through it before
approving, because it is a real running revision that simply has no traffic
pointed at it.

To run the same thing locally:

```bash
IMAGE_TAG=$(git rev-parse HEAD) TRAFFIC_MODE=no-traffic ./scripts/deploy.sh
TEST_BEARER_TOKEN=eyJ... ./scripts/smoke-test.sh
./scripts/promote.sh
```

`deploy.sh` writes `.deploy-state.env`; `smoke-test.sh` and `promote.sh` read
it, so the three chain with no arguments. Add `.deploy-state.env` to
`.gitignore` — it carries service URLs and revision names, not secrets, but it
is build output.

---

## Rollback

```bash
gcloud run revisions list --service=pickready-backend --region=asia-south1
./scripts/promote.sh rollback pickready-backend-00041-abc pickready-backend
./scripts/promote.sh rollback pickready-frontend-00018-xyz pickready-frontend
```

Traffic shifts in seconds. **Migrations do not roll back**, which is why the
project's standing rule is that schema changes are additive (extend tables and
routes; do not replace established contracts). A rollback is safe exactly to
the extent that rule was followed in the commit being rolled back.

---

## Things that are not obvious

### `DATABASE_URL` and `REDIS_URL` are plain env vars, never `--set-secrets`

Cloud Run refuses a deploy where one name appears in both `--set-env-vars` and
`--set-secrets`, and the error names a "type conflict" rather than the
variable, which is expensive to read. `deploy.sh` builds the `--set-secrets`
flag by **listing** Secret Manager and filtering:

```
SECRET_EXCLUDE_RE='^(DATABASE_URL|REDIS_URL|POSTGRES_PASSWORD|NEXT_PUBLIC_.*)$'
```

`POSTGRES_PASSWORD` is excluded because it is consumed by the deploy script to
compose `DATABASE_URL` and the application never reads it directly.
`NEXT_PUBLIC_*` are frontend build arguments, not backend runtime config.

Everything else in Secret Manager is mounted automatically, so adding a new
secret reaches the next deploy with no code change.

### The database URL uses a UNIX socket, not host:port

`--add-cloudsql-instances` mounts a socket at
`/cloudsql/<CONNECTION_NAME>`; a `host:port` DSN cannot reach it. The driver is
named explicitly because the app's engine is asyncpg:

```
postgresql+asyncpg://pickready:PASSWORD@/pickready?host=/cloudsql/pick-ready-503913:asia-south1:pickready-postgres
```

`deploy.sh` resolves the connection name and the password at run time rather
than hardcoding either, so a recreated instance or a rotated password does not
silently deploy a revision pointed at an address that no longer works.

### Migrations block the deploy

`gcloud run jobs execute --wait` exits non-zero on failure, and the script runs
under `set -euo pipefail`, so a failed migration aborts before a single service
revision is created. Migrations never run on API startup: several instances
boot at once during a rollout, Alembic takes a lock, and the losers crash-loop.

### The first deploy of a service cannot be staged

Cloud Run rejects `--no-traffic` when a service is being created, because there
is no prior revision to keep serving. `deploy.sh` detects this, warns, and
deploys with traffic. Every subsequent deploy stages normally. For
`pick-ready-503913` both services already exist, so this never fires.

### Worker and beat are not staged

Neither serves HTTP, so neither has a traffic split. Whatever `deploy.sh`
deploys starts consuming the Celery queue immediately, before the approval
gate. This is a real limitation, and it is why additive migrations matter: a
worker on the new image must be able to process a task enqueued by the old one.

The workload **kind** is detected, not assumed. `infra/gcp/deploy.sh`
provisions `pickready-worker` and `pickready-beat` as Cloud Run **worker
pools**; an environment that provisioned them as Cloud Run **jobs** is equally
valid. `deploy.sh` checks for a worker pool first and falls back to a job, so
it updates whatever is actually there instead of creating a duplicate beside
it.

### The frontend reaches the backend through a same-origin proxy

`NEXT_PUBLIC_API_URL` is deliberately **not** set on the frontend service.
`frontend/lib/api.ts` defaults `API_BASE` to the relative `/api/v1`, and
`next.config.js` rewrites that to `BACKEND_INTERNAL_URL` server-side. Keeping
API calls same-origin is what keeps the `SameSite=Strict` auth cookies attached
— an absolute cross-origin `NEXT_PUBLIC_API_URL` would drop them and break
every signed-in request.

`deploy.sh` therefore sets `BACKEND_INTERNAL_URL` to the backend service URL.
`SET_PUBLIC_API_URL=true` is an escape hatch for a deliberate split-origin
deployment; it also needs a **rebuild**, because `NEXT_PUBLIC_*` values are
inlined into the bundle by the compiler and cannot be injected at deploy time.

Firebase web config has the same build-time constraint, which is why those six
values are `--build-arg`s and not env vars.

### `FRONTEND_URL` on the backend

The backend needs the public frontend origin for links in outbound email and
for the CORS allowlist. A Cloud Run service URL is fixed for the life of the
service, so `deploy.sh` resolves it **before** deploying the backend and each
workload needs exactly one revision. The post-frontend reconciliation pass only
fires when the URL actually changed (in practice, the very first deploy), and
when it does it stages with `--no-traffic --tag` exactly like the first pass,
so it cannot leak traffic to an unproven revision.

### Smoke test endpoint paths

Two of the paths in the original brief do not exist in this codebase, and the
script uses the real ones:

| Asked for | Actual | Why |
|---|---|---|
| `/api/v1/health` | `/health` | The health route is mounted on the app root in `backend/app/main.py`, outside the `/api/v1` prefix. `/api/v1/health` is a 404 and would fail every deploy. |
| `/api/v1/me/capabilities` | `/api/v1/auth/me` | No such route exists. `GET /api/v1/auth/me` returns `{user, capabilities[]}`, which is the same information. |

`/api/v1/dashboard/summary` and `/api/v1/jobs` are correct as given. Override
the list without editing the script:

```bash
SMOKE_AUTHED_PATHS="/api/v1/jobs /api/v1/auth/me" ./scripts/smoke-test.sh
```

The script also asserts that `/api/v1/auth/me` actually carries a
`capabilities` array. A 200 with an empty body means the token authenticated
but permission resolution returned nothing, which renders an empty portal
rather than an error page and would otherwise promote cleanly.

Health is retried (12 attempts, 5 seconds apart) because a scale-to-zero
revision's cold start routinely exceeds a default curl budget and a
first-request timeout is not a broken build. The authenticated probes are not
retried; by then the instance is warm.

### Promotion names revisions, not "latest"

`--to-latest` resolves at execution time. If a second deploy lands between the
smoke test and the approval, `--to-latest` would put an unproven revision live
under a green check mark. `promote.sh` promotes by revision name, passed
through as a job output, so the approval means exactly what the reviewer read.
It falls back to `--to-latest` only when no revision name was supplied.

The `concurrency` group in the workflow makes that race unlikely; naming the
revision makes it impossible.

### Staged tags are removed after promotion

Tagged revisions are pinned and accumulate against the per-service revision
limit, and a stale `staged-<sha>` URL left reachable is an unauthenticated door
into an old build. `promote.sh` removes the tag once its revision is live.

---

## Post-deploy manual steps

Both are one-time and neither is automatable from CI:

1. Add the frontend hostname to **Firebase Console → Authentication → Settings
   → Authorised domains**, or Google sign-in fails on the deployed origin.
2. Point the Razorpay webhook at
   `https://<backend-url>/api/v1/billing/webhook`.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `unable to get ACTIONS_ID_TOKEN_REQUEST_URL` | The job is missing `permissions: id-token: write` |
| `Permission denied on secret` | `github-deployer` is missing `roles/secretmanager.secretAccessor`; rerun `setup-wif-once.sh` |
| `iam.serviceaccounts.actAs` denied | The `actAs` binding on `pickready-runtime` has not propagated; wait a minute and rerun |
| Deploy fails with a **type conflict** on an env var | A name is in both `--set-env-vars` and `--set-secrets`; add it to `SECRET_EXCLUDE_RE` in `deploy.sh` |
| Smoke test 401 on every authenticated path | `TEST_BEARER_TOKEN` expired; mint a new one (step 4) |
| Smoke test never gets a healthy `/health` | The revision is crash-looping. `gcloud run revisions logs read <revision> --region=asia-south1` |
| Container exits immediately with `/bin/sh\r: not found` | CRLF line endings reached the image. `.gitattributes` pins LF; the backend Dockerfile also strips them defensively |
| `--no-traffic is not supported` | The service does not exist yet. Expected on a first deploy; the script warns and continues |
| Migration job times out | Its `--task-timeout` is 900s. A longer migration needs that raised in `run_migrations` |
