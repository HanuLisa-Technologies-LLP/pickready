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

### Current Sprint — Firebase Authentication

- `feat: Firebase auth (all roles)` — Firebase verifies identity while PickReady keeps database-backed RBAC, tenant scope, and portal sessions.
- `fix: provision permanent Firebase development accounts` — idempotent Alembic migration provisions Sarkar Corp, ACRM Corp, Specter & Co., and the requested active team roster in PostgreSQL; matching Firebase email/password identities are provisioned without storing passwords in the database.
- Google sign-in is enforced as candidate-only at both the interface and backend verification boundary; Owner and every internal role use Firebase email/password or a uniquely assigned phone identity.
- Shared imported phone numbers are rejected for phone sign-in rather than becoming a cross-person workspace chooser; those accounts use their provisioned email/password credentials until distinct mobile numbers are assigned.

### Current Sprint — MVP Jobs Catalogue

- `feat: import permanent MVP jobs catalogue` — 30 supplied open roles are stored through an idempotent Alembic migration, with 10 ratified and portal-visible jobs each for Sarkar Corp, ACRM Corp, and Specter & Co.
- Source job IDs, descriptions, skills, education, experience, location, remote status, openings, deadline, and INR LPA salary ranges are mapped into existing `jobs`, `jd_json`, and `compensation_json` fields; no new temporary data loader is required.

---

## Current Status (As of 2026-07-24)

**Live & Tested**: Authentication, OTP email/SMS, RBAC, matching, Celery tasks, and backend contract tests.

**Known Blocker**: Resend requires a verified domain before sending to non-owner recipients.

**Current Sprint**: Landing and comments-only AI review display shipped; production verification remains.

**Firebase Auth Verification**: The permanent development roster can sign in through Firebase email/password and exchange into the correct PickReady portal session. Google is limited to candidates.

**MVP Jobs Verification**: 30 imported jobs are ratified and visible to the appropriate tenant portals.

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
