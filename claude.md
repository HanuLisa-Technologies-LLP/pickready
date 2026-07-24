# claude.md — PickReady Build Conventions

This file is the standing context for any Claude Code session working on this repo. Read `PRD.md` for functional requirements and `ESD.md` for the architecture — this file is *how* to build it, not *what* to build.

---

## 1. Project One-Liner

PickReady is a multi-tenant recruitment/ATS platform for Hanulisa Technologies LLP. Next.js + FastAPI, OTP-only auth for every role, Postgres+pgvector for data and matching, Celery for all async work, fully Dockerized.

---

## 2. Repository Layout

```
/frontend                Next.js 14 (App Router), TypeScript, shadcn/ui
  /app                   routes, grouped by role: (super-admin) (client) (hr) (recruiter) (hiring-manager) (candidate)
  /components            shared UI, shadcn primitives in /components/ui
  /lib                    api client, auth helpers, theme provider
/backend
  /app
    /api                 FastAPI routers, one module per PRD section (auth, jobs, candidates, matching, verification, dashboard, admin)
    /models              SQLAlchemy models, mirroring ESD §4 tables
    /schemas             Pydantic request/response models
    /services            business logic — approval FSM, RBAC engine, LLM router, matching pipeline
    /workers             Celery tasks (send_email, send_sms, run_matching, poll_verification, refresh_dashboard_views)
    /core                config, security (OTP hashing, JWT), db session with RLS tenant-var setter
  /alembic                migrations
  Dockerfile
/infra
  docker-compose.yml      local dev: postgres+pgvector, redis, backend, worker, beat, frontend
  railway.json / render.yaml   production service definitions
/docs
  PRD.md
  ESD.md
  claude.md
```

---

## 3. Non-Negotiable Rules

These are architectural decisions already made in ESD.md — do not silently deviate from them or re-litigate them in code review:

1. **Every tenant-scoped query goes through the RLS-aware session.** Never hand-write a `WHERE tenant_id = ...` filter as the *only* protection — the Postgres RLS policy is the real boundary; app-level filtering is defense in depth, not a substitute.
2. **Authentication is Firebase (as of 2026-07-24).** All roles sign in via Firebase Auth — Google, email/password, and phone. The backend verifies the Firebase ID token (`services/firebase_auth.py`) and issues the app's own portal-scoped JWT cookies; database roles/permissions remain authoritative (Firebase is identity only, never authorization). **Exception to the original "no passwords" rule:** candidate email/password is explicitly allowed (user decision, 2026-07-24). Do NOT build a custom password store or "forgot password" flow — Firebase owns credentials and recovery. The legacy MSG91 OTP send-path is retained as a working SMS feature but is no longer the login mechanism.
3. **Permissions are data, not code.** Never write `if role == "recruiter":` in business logic. Use the `require_capability("...")` FastAPI dependency backed by `role_permissions`.
4. **All async/slow work is a Celery task**, never inline in a request handler: matching/re-ranking, email/SMS sending, resume parsing, verification-reply parsing, dashboard aggregation.
5. **All outbound email goes through Mailtrap (as of 2026-07-24, replaces Resend).** One provider for OTP, outreach, verification, and interview invites. Never wire up Gmail/Outlook OAuth for outbound mail — this was explicitly ruled out by the client.
6. **Candidate resumes are not persisted on the Candidate Portal between applications.** Each application flow requires a fresh upload; don't "helpfully" cache the last resume.
7. **Databank candidates never re-enter the verification/40-aspect flow** — their existing Profile is reused as-is. Only freshly sourced candidates go through Section 5's data-collection + verification steps.
8. **Tier boundaries are inclusive upward**: a score of exactly 90 is Highly Matching, not Moderately Matching. Implement tier assignment top-down (check ≥90 first).
9. **LLM keys are routed with fallback, never hardcoded to a single provider.** Use the `llm_provider_keys` table and the router service (ESD §8.4); mark a key unhealthy on repeated failure rather than crashing the calling task.
10. **The theme toggle lives only in Settings/Profile** — never in the main navbar or a persistent floating control.

---

## 4. Coding Conventions

- **Backend**: Python 3.12, FastAPI, async everywhere (`async def` route handlers, `asyncpg`/`SQLAlchemy` async engine). Pydantic v2 for all request/response schemas — no bare dicts crossing the API boundary.
- **Frontend**: TypeScript strict mode on. Server Components by default; `"use client"` only where interactivity requires it. shadcn/ui components live under `/components/ui` and are not hand-edited beyond the CLI-generated output — wrap/compose instead of modifying generated files.
- **Styling**: Tailwind, monochrome palette (CSS variables for the black/white theme pair so the toggle is a variable swap, not a component-level branch).
- **Migrations**: every schema change is an Alembic migration, checked in — no manual production schema edits.
- **Tests**: Pytest for backend (unit tests on the approval FSM, RBAC engine, tier-boundary logic are mandatory given how much of the product depends on getting these exactly right); Playwright or React Testing Library for frontend critical flows (OTP login, job approval chain, HR review screen).
- **Commits**: Conventional Commits style (`feat:`, `fix:`, `chore:`, `refactor:`) to keep the history usable for a changelog later.

---

## 5. Environment Variables (`.env.example` contents)

```
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/pickready
POSTGRES_RLS_APP_ROLE=pickready_app

# Redis / Celery
REDIS_URL=redis://host:6379/0

# Auth
JWT_SECRET=
JWT_ACCESS_TTL_MINUTES=15
JWT_REFRESH_TTL_DAYS=7

# OTP
OTP_TTL_MINUTES=5
OTP_MAX_ATTEMPTS=5
OTP_COOLDOWN_MINUTES=15

# LLM providers (3 each, router picks/falls back per ESD §8.4)
GROQ_API_KEY_1=
GROQ_API_KEY_2=
GROQ_API_KEY_3=
GEMINI_API_KEY_1=
GEMINI_API_KEY_2=
GEMINI_API_KEY_3=
OPENROUTER_API_KEY_1=
OPENROUTER_API_KEY_2=
OPENROUTER_API_KEY_3=
LLM_KEY_ENCRYPTION_SECRET=

# Embeddings
BGE_M3_ENDPOINT=

# Email
RESEND_API_KEY=

# SMS
MSG91_API_KEY=
MSG91_SENDER_ID=

# File storage
CLOUDINARY_URL=

# App
ENVIRONMENT=development
FRONTEND_URL=http://localhost:3000
```

---

## 6. Local Dev Quick Start

```bash
git clone <repo>
cd pickready
cp .env.example .env          # fill in real keys before first run
docker compose -f infra/docker-compose.yml up --build
# frontend: http://localhost:3000
# backend:  http://localhost:8000/docs (FastAPI auto-docs)
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.scripts.seed_dev_data
```

---

## 7. Build Order (matches PRD §9 phasing — build and ship in this order, don't jump ahead)

1. Tenant model + RLS policies + Super Admin console + RBAC engine
2. OTP auth for every role + Candidate Portal auth scope
3. Company onboarding + Hiring Manager account creation (max-5 enforced)
4. Job creation + configurable approval FSM
5. Resume upload + BGE-M3 embeddings + pgvector + Databank
6. Hybrid ranking pipeline (semantic → keyword → LLM re-rank → tiers)
7. Candidate outreach + 40-aspect flow + employer verification (form + fallback parsing)
8. HR Review Screen + Hiring Manager shortlist actions
9. Interview scheduling (client-domain email, .ics) + mandatory status tracking
10. HR/Recruiter dashboard (materialized views)
11. Observability, audit log UI, load/security hardening

---

## 8. When Unsure

If a requirement in PRD.md is ambiguous and the ESD doesn't resolve it, don't guess silently — implement the most defensible interpretation, leave a clear `# ASSUMPTION:` comment at the point of implementation, and surface it back to the user rather than letting it drift into an undocumented behavior.
