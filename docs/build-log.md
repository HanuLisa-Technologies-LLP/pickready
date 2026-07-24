# PickReady Build Log

## Technical Build Log

### Foundation

- SQLAlchemy models, tenant-scoped RLS sessions, OTP hashing, portal-scoped JWTs, encrypted provider keys, and a data-driven permission matrix.

### API Layer

- 40 contract routes across authentication, jobs, candidates, matching, dashboard, administration, approvals, verification, and outreach.
- RBAC capability enforcement, approval FSM audit history, dual-channel OTP limits, and append-only audit logging.

### AI Pipelines

- LLM provider fallback and circuit breaking, hybrid candidate matching, four-parameter weighted scoring, resume/verification parsing, and seven Celery tasks.

### Frontend

- Role-aware Admin, client-org, and Candidate portals; OTP flow; HR Review Screen; monochrome settings theme; and production Next.js build.

### Infrastructure & Integration

- Docker Compose services, deployment configurations, Alembic RLS/index migration, owner invariant protection, JWT refresh rotation, and auth auditing.

### Current Sprint — Landing Page

- `feat: add public PickReady landing page` — public hero, feature overview, candidate sign-up/login routes, safe portal redirect hints, and non-blocking landing-view audit telemetry.
- `refactor: show AI match comments without numerical ratings` — comments-only matching and HR review UI, with authenticated rating-comment view auditing.

---

## Current Status (As of 2026-07-24)

**Live & Tested**: Authentication, OTP email/SMS, RBAC, matching, Celery tasks, and backend contract tests.

**Known Blocker**: Resend requires a verified domain before sending to non-owner recipients.

**Current Sprint**: Landing and comments-only AI review display shipped; production verification remains.

## Product Status & Feature Summary

### What Works Now

#### All Users

- ✅ OTP login by email and SMS
- ✅ Unified authentication routes users to the correct portal
- ✅ Settings, profile details, and theme toggle
- ✅ Append-only audit trail for key protected actions
- ✅ Public landing page with candidate sign-up and login routes
- ✅ AI match explanations display comments only; numerical ratings remain audit data

#### Super Admin

- ✅ Tenant onboarding, permission templates, and cross-tenant support visibility

#### Client Companies

- ✅ Company information, staff management, approval hierarchy, and job workflow

#### HR Managers

- ✅ Candidate outreach, Databank matching, AI reasoning, and full profile review

#### Recruiters

- ✅ Resume upload/parsing, candidate sourcing, and interview scheduling

#### Hiring Managers

- ✅ Matched-profile review, shortlist/reject/hold actions, and pipeline visibility

#### Candidates

- ✅ Self-registration, OTP login, outreach response, job applications, and status tracking

### Coming Soon

- [ ] Domain verification onboarding guide for Resend SPF/DKIM
- [ ] Tenant-editable email templates and delivery webhook monitoring
- [ ] Audit-log UI, advanced filters, and bulk operations

### Deployment Readiness

**Local Dev** ✅ — Docker Compose and production frontend build available.

**Staging** ⏳ — Needs managed-service deployment and domain verification.

**Production** ⏳ — Needs secrets rotation, monitoring, and backup policy.
