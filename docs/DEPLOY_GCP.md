# Deploying ReadyPick to Google Cloud Run

Target project: `pick-ready-503913` (project number `1034326377358`), region
`asia-south1` by default.

Four workloads, two images:

| Workload | Cloud Run type | Image | Args |
|---|---|---|---|
| `pickready-backend` | service (public) | backend | `api` |
| `pickready-frontend` | service (public) | frontend | (default) |
| `pickready-worker` | **worker pool** | backend | `worker` |
| `pickready-beat` | **worker pool** | backend | `beat` |
| `pickready-migrate` | job | backend | `migrate` |

---

## 1. Why the shapes are what they are

Four decisions here are not stylistic. Changing them breaks the deployment.

**Celery runs in worker pools, not services.** A Cloud Run *service* must listen
on `$PORT` and pass a startup probe. A Celery worker serves no HTTP at all, so
the revision never goes ready and rolls back. Cloud Run *worker pools* exist for
exactly this. Beat is pinned to `--instances=1`: two schedulers means every
scheduled task fires twice.

**The browser never talks to the backend directly.** It calls `/api/*` on the
frontend, and `frontend/app/api/[...path]/route.ts` forwards to
`BACKEND_INTERNAL_URL`. That is what lets `COOKIE_SAMESITE` stay `strict`:
`*.a.run.app` is on the Public Suffix List, so two Cloud Run services are
**cross-site**, and a split origin means the browser silently drops every auth
cookie. Nobody could stay signed in. It also removes CORS from the picture.

This is a route handler and not a `next.config.js` rewrite because rewrites are
resolved during `next build` and frozen into `routes-manifest.json`. An
environment-derived destination would be captured at build time, and an unset
variable would emit no rewrite at all, so API calls would fall through to a 404
page.

**Cloud SQL is reached over a unix socket.** `--add-cloudsql-instances` mounts a
socket at `/cloudsql/<CONNECTION_NAME>`; there is no host and port to connect to.
The app uses asyncpg, so the DSN must name the driver:

```
postgresql+asyncpg://USER:PASS@/pickready?host=/cloudsql/PROJECT:REGION:INSTANCE
```

A `postgresql://user:pass@PROJECT:REGION:INSTANCE/db` DSN fails 100% of the time.

**Memorystore needs VPC egress.** It only has a private VPC address. The deploy
uses Direct VPC egress (`--network` / `--subnet`), which replaces the older
Serverless VPC Access connector and costs nothing extra.

---

## 2. One-time manual prerequisites

Only these cannot be scripted.

**a. Service account.** IAM & Admin > Service Accounts > Create. Roles:

```
roles/run.admin                    roles/secretmanager.admin
roles/artifactregistry.admin       roles/iam.serviceAccountUser
roles/cloudsql.admin               roles/compute.networkAdmin
roles/redis.admin                  roles/serviceusage.serviceUsageAdmin
```

Download the JSON key to the repo root as `service-account-key.json`. It is
gitignored. **Never commit it, never paste it into chat** — it is effectively
project takeover.

**b. `.env`** at the repo root, with one addition beyond local dev:

```
POSTGRES_PASSWORD=<a strong password for the Cloud SQL user>
```

Everything else the deploy needs (JWT, SMTP, Razorpay, Firebase admin JSON,
Cloudinary, the 21 LLM slots) is already there and is pushed to Secret Manager
by name, never printed.

**c. `frontend/.env.local`** with the six `NEXT_PUBLIC_FIREBASE_*` values. These
are inlined into the JS bundle at **build** time, so they must exist before the
image is built. They are public by design (every browser receives them); access
is controlled by Firebase Auth rules and the authorised-domains list.

---

## 3. Deploy

No local `gcloud` install is needed — the script falls back to Google's official
CLI container automatically.

```bash
./infra/gcp/deploy.sh preflight
```

Then, first time:

```bash
./infra/gcp/deploy.sh all
```

Or step by step, which is what you want when something fails:

```bash
./infra/gcp/deploy.sh infra      # APIs, registry, Cloud SQL, Redis, secrets
./infra/gcp/deploy.sh images     # build + push both images
./infra/gcp/deploy.sh services   # migrate, then deploy all four workloads
```

Every step is idempotent and nothing deletes. Cloud SQL and Memorystore each
take 5 to 10 minutes to create on the first run.

---

## 4. Two manual steps after the first deploy

The script prints both; neither can be done through `gcloud`.

1. **Firebase authorised domains.** Console > Authentication > Settings >
   Authorised domains, add the frontend hostname. Until this is done, Google
   sign-in fails with `auth/unauthorized-domain`.
2. **Razorpay webhook.** Point it at
   `https://<backend-url>/api/v1/billing/webhook` with `RAZORPAY_WEBHOOK_SECRET`.
   This is why the backend is public: Razorpay posts to it directly.

---

## 5. Cost

Defaults are the small tier, roughly **$80 to $110/month**:

| Resource | Default | Approx |
|---|---|---|
| Cloud SQL `db-custom-1-3840` | 1 vCPU, 3.75 GB | ~$50 |
| Memorystore Basic 1 GB | | ~$25 |
| Worker + beat pools | 1 instance each, always on | ~$25 |
| Cloud Run services | scale to zero | usage |

Override with `SQL_TIER`, `REDIS_SIZE`. The worker pools are the one thing that
cannot scale to zero: Celery has to be there when a task is queued.

---

## 6. Updating

```bash
./infra/gcp/deploy.sh images
./infra/gcp/deploy.sh services
```

Images are tagged with the short git SHA, so a rollback is a redeploy of an
earlier tag.

Rotate a secret without a rebuild:

```bash
printf '%s' "$NEW" | gcloud secrets versions add JWT_SECRET --data-file=-
gcloud run services update pickready-backend --region=asia-south1
```

---

## 7. Troubleshooting

| Symptom | Cause |
|---|---|
| Revision fails startup probe | Container not listening on `$PORT` at `0.0.0.0`. Next needs `HOSTNAME=0.0.0.0`; both Dockerfiles set it. |
| Login succeeds then instantly logs out | Auth cookies being dropped. Something is bypassing the same-origin proxy — check `NEXT_PUBLIC_API_URL` is **unset** on the frontend service. |
| API calls return HTML | `BACKEND_INTERNAL_URL` unset, so the proxy 502s or the path 404s to a page. |
| `auth/invalid-api-key` at build | Firebase build args missing. The Dockerfile fails fast on this. |
| Worker deploys but processes nothing | Deployed as a service instead of a worker pool, or no VPC egress so Memorystore is unreachable. |
| `CREATE EXTENSION vector` fails | pgvector not available on that Cloud SQL version. Use Postgres 15+. |

Logs:

```bash
gcloud run services logs read pickready-backend --region=asia-south1 --limit=100
```

Note it is `gcloud run services logs read`, not `gcloud run logs read`.
