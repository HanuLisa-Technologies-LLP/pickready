# ReadyPick

Multi-tenant recruitment and hiring-intelligence platform for Hanulisa
Technologies LLP. Candidates apply, are ranked against the role, sit one
adaptive AI assessment, and come out the other side as a **PRISM Report** a
recruiter can act on, graded in words and never in numbers.

Next.js 16 frontend, FastAPI backend, Firebase authentication, PostgreSQL with
pgvector, Celery for every slow path, fully containerised, deployed to AWS ECS
Fargate.

| | |
|---|---|
| **All documentation** | [docs/README.md](docs/README.md), the index |
| What the product does | [docs/product/PRD.md](docs/product/PRD.md) |
| How it is built | [docs/architecture/ESD.md](docs/architecture/ESD.md) |
| Build conventions (read before changing code) | [claude.md](claude.md) |
| Run it locally | [docs/operations/SETUP.md](docs/operations/SETUP.md) |
| Deploy it | [docs/operations/DEPLOY_AWS.md](docs/operations/DEPLOY_AWS.md) |
| Design system | [DESIGN.md](DESIGN.md), [PRODUCT.md](PRODUCT.md) |

---

## What it does

**Four portals, one platform.** The *Provider Portal* (`/admin`) is the
ReadyPick owner console. The *Customer Portal* (`/org`) is a client company
workspace. The *Candidate Portal* (`/portal`) is where applicants live. The
*Business Development Portal* (`/bd`) is where the ReadyPick sales team works
leads.

**The hiring flow.** A customer creates a job as one markdown JD; the platform
derives a per-job **Tatva Assessment** matrix (Must-have, Nice-to-have,
Behavioural) from that job's own description, informed by a SWOT intake and the
client's compiled Company DNA. A human reviews and freezes the matrix, and that
freeze is the only comparability guarantee the product has. Candidates apply,
every applicant is ranked, and the recruiter selects who is assessed.

**The assessment.** One adaptive conversation per candidate, with questions
written fresh from the JD, the frozen matrix, that candidate's resume and their
project evidence. The coverage plan is deterministic, so two candidates are
probed on the same criteria in the same order; only the wording varies. Five
isolated dimension evaluators score it, and a model-free aggregator turns those
bands into a grade.

**The output.** A PRISM Report (*Predictive Role Intelligence & Suitability
Mapping*) with a fixed section order, three number-free radar charts, and a
citation chokepoint that refuses an uncited statement. Client-visible grades are
four words only: Highly Matching, Matching, Moderately Matching, Not Matching.

**Project Evidence Intelligence.** Candidates may optionally submit projects,
either files or a public repository. The platform parses them deterministically,
derives structured evidence, and **deletes the originals**; only the derived
intelligence is retained.

---

## Architecture

| Layer | Technology |
|---|---|
| Frontend | Next.js 16.2, React 18.3, TypeScript, Tailwind, shadcn/ui |
| Backend | FastAPI, Python 3.12, Pydantic v2, async SQLAlchemy |
| Database | PostgreSQL 16 + pgvector, row-level security per tenant |
| Cache / queue | Redis 7, the Celery broker and result backend |
| AI | One vendor, three endpoints: `gpt-5.6-terra` (judge and write), `gpt-5.6-luna` (extract and classify), `voyage-4` (embeddings). Routed through `services/llm_router` with per-task timeouts, budgets and a circuit breaker |
| Auth | Firebase Authentication (Google, email/password, phone) plus app-issued portal JWTs |
| Payments | Razorpay Subscriptions and credit-pack Orders |
| Email / SMS | Gmail SMTP; MSG91 for SMS |
| Object storage | Private S3 bucket, content-addressed, served through authenticated routes |
| Deployment | AWS ECS Fargate, RDS PostgreSQL, ElastiCache Redis, S3, ECR |

One backend image runs four roles chosen at container start: `api`, `worker`,
`beat`, `migrate`. The frontend proxies `/api/*` to the backend through a
same-origin route handler, which keeps auth cookies `SameSite=Strict` and takes
CORS out of the trust boundary.

Two rules shape most of the code. **Every tenant-scoped query goes through the
RLS-aware session**, because the Postgres policy is the boundary and the
application's WHERE clause is only defence in depth. **Permissions are data,
never a role branch**: `require_capability(...)` resolves per request from a
user overlay over tenant rows over a global template.

---

## Local development

```bash
cp .env.example .env
```

```bash
docker compose -f infra/docker-compose.yml up --build
```

Frontend on http://localhost:3000, API docs on http://localhost:8000/docs.

```bash
docker compose -f infra/docker-compose.yml exec backend alembic upgrade head
```

Full detail, including the Firebase web config and the seeded accounts, is in
[docs/operations/SETUP.md](docs/operations/SETUP.md).

---

## Tests

```bash
./scripts/test.sh
```

The script brings up `docker-compose.test.yml` (Postgres, Redis and MinIO on
non-default ports), recreates the database, flushes the cache, migrates, and
runs pytest. It is the implementation; the `make test*` targets are wrappers.
Pass `integration` for only the tests that touch real infrastructure, or `all`
for the backend suite plus the agent evals and the frontend suite.

The suite runs with **no model credentials set**, deliberately: every generative
path has a deterministic fallback, and a vendor outage must not fail the build.
Current numbers are in
[docs/operations/TEST_BASELINE.md](docs/operations/TEST_BASELINE.md); every skip
is declared in [docs/operations/SKIPS.md](docs/operations/SKIPS.md) and enforced
by a test.

---

## Deployment

Five workloads from two images, all on AWS ECS Fargate:

| Workload | Type | Image | Purpose |
|---|---|---|---|
| `readypick-<env>-api` | ECS service | backend | Public API, Razorpay webhooks |
| `readypick-<env>-frontend` | ECS service | frontend | Public web app |
| `readypick-<env>-worker` | ECS service | backend | Celery task processing |
| `readypick-<env>-beat` | ECS service, exactly 1 task | backend | Scheduled dispatch |
| `readypick-<env>-migrate` | ECS one-shot task | backend | Alembic migrations |

Managed infrastructure: **RDS PostgreSQL 16** with pgvector, **ElastiCache
Redis** set to `noeviction` because it is a broker and not a cache, **AWS
Secrets Manager** for every credential, **ECR** with immutable tags, and a
private **S3** bucket. IAM is scoped per service and enumerated, never by
prefix, and the task role is separate from the execution role. Infrastructure
is Terraform in [infra/](infra/).

> **No live AWS deployment has been executed, and that is deliberate.** An
> offline `terraform plan` succeeds for both environments, which proves the
> configuration is internally consistent and that the graph resolves. It proves
> nothing about a real account: not creatability, not quotas, not IAM
> behaviour. Every deploy job additionally sits behind an unset repository
> variable and a required-reviewer environment.
> [docs/operations/DEPLOY_AWS.md](docs/operations/DEPLOY_AWS.md) states exactly
> what is and is not verified.

---

## Security

- **Secrets** live in AWS Secrets Manager and are injected at runtime, never
  baked into an image and never committed.
- **Tenant isolation** is a Postgres row-level-security policy on every
  tenant-scoped table.
- **Credentials** are Firebase's. The application database stores no password
  and there is no custom recovery flow.
- **Candidate uploads are untrusted input.** Resumes and project submissions are
  validated and never executed, archives are inspected before extraction, and
  every ceiling is configuration rather than a literal.
- **No number reaches a client.** Scores exist internally for ranking and become
  words server-side, at the serializer.
