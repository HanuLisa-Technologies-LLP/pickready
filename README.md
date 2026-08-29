# ReadyPick
Multi-tenant recruitment and applicant tracking platform built for Hanulisa Technologies LLP. Next.js 14 frontend, FastAPI backend, Firebase Authentication (email/password + Google OAuth), PostgreSQL with pgvector for AI-powered candidate matching, and Celery for all asynchronous work. Fully containerized and deployed on AWS ECS Fargate.

- **Functional requirements**: [docs/PRD.md](docs/PRD.md)
- **Architecture**: [docs/ESD.md](docs/ESD.md)
- **Build conventions**: [claude.md](claude.md)
- **Deployment runbook**: [docs/DEPLOY_AWS.md](docs/DEPLOY_AWS.md)
- **Design system**: [DESIGN.md](DESIGN.md) and [PRODUCT.md](PRODUCT.md)

---

## Architecture Overview

| Layer | Technology |
|---|---|
| Frontend | Next.js 14, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | FastAPI (Python 3.12), Pydantic v2, async SQLAlchemy |
| Database | PostgreSQL 16 + pgvector, row-level security (multi-tenant isolation) |
| Cache / Queue | Redis 7 (Celery broker and result backend) |
| AI | LangChain + LangGraph, multi-provider LLM routing (Groq, Gemini, OpenRouter) |
| Auth | Firebase Authentication (email/password + Google OAuth) |
| Payments | Razorpay Subscriptions API |
| Email | Gmail SMTP |
| Storage | Cloudinary |
| Deployment | AWS ECS Fargate, RDS PostgreSQL, ElastiCache Redis, S3, ECR |

The system runs as one backend image with four roles, selected at container start: `api`, `worker`, `beat`, and `migrate`. The frontend proxies all `/api/*` calls to the backend through a same-origin route handler, keeping authentication cookies same-site in both local and cloud environments.

---

## Local Development

```bash
cp .env.example .env                    # fill in real keys before first run
cp frontend/.env.local.example frontend/.env.local   # Firebase web config

docker compose -f infra/docker-compose.yml up --build
```

- Frontend: http://localhost:3000
- Backend + API docs: http://localhost:8000/docs

Run migrations and seed data:

```bash
docker compose -f infra/docker-compose.yml exec backend alembic upgrade head
docker compose -f infra/docker-compose.yml exec backend python -m app.scripts.seed_dev_data
```

Authentication in development uses the same Firebase project as production. Seeded accounts are provisioned by migration `0005_provision_pickready_team.py` and sign in with Firebase email/password or Google OAuth — there is no OTP flow anywhere in the platform.

---

## Tests

```bash
cd backend && python -m pytest tests -q
```

---

## Deployment

ReadyPick deploys entirely to **AWS ECS Fargate**, with no other hosting provider involved. Five workloads run from two container images:

| Workload | Type | Image | Purpose |
|---|---|---|---|
| `readypick-<env>-api` | ECS service | backend | Public API, serves Razorpay webhooks |
| `readypick-<env>-frontend` | ECS service | frontend | Public web app |
| `readypick-<env>-worker` | ECS service | backend | Celery task processing |
| `readypick-<env>-beat` | ECS service (exactly 1 task) | backend | Scheduled task dispatch |
| `readypick-<env>-migrate` | ECS one-shot task | backend | Alembic schema migrations |

Managed infrastructure:

- **Cloud SQL** — PostgreSQL 16 with pgvector, reached over a Unix socket
- **Memorystore** — Redis 7, reached over Direct VPC egress
- **Secret Manager** — all credentials and API keys; nothing sensitive is ever stored as a plain environment variable or committed to the repository
- **Artifact Registry** — private Docker image storage

Infrastructure is Terraform in [infra/](infra/) -- seven independently-plannable modules and two environment roots -- and the runbook is [docs/DEPLOY_AWS.md](docs/DEPLOY_AWS.md).

> **No live AWS deployment has been executed.** The codebase and pipeline are complete and validated offline (`./infra/validate.sh`); the pipeline stops at a required-reviewer gate before any production apply, and every step past it is additionally disabled behind an unset repository variable. See docs/DEPLOY_AWS.md for exactly what is and is not verified.

---

## Security Notes

- All secrets are stored in Google Secret Manager and mounted at runtime — never baked into images or committed to source control
- Row-level security is enforced at the database level for every tenant-scoped table; the application's Postgres role holds no bypass privileges by default
- Firebase Authentication owns all credential and session handling; the application database never stores a password
- The frontend and backend share one origin from the browser's perspective, via a server-side proxy, which keeps authentication cookies `SameSite=Strict` and removes CORS from the trust boundary entirely
