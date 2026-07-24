# PickReady — Production Readiness & Deployment Guide

Concise operator guide for standing up PickReady (Firebase auth for all roles,
Mailtrap email, MSG91 SMS, Cloudinary storage). Pairs with `claude.md`
(conventions) and `docs/API_CONTRACT.md` (routes). Two validation harnesses are
the go/no-go gate — see [Validation](#7-validation-harnesses).

---

## 1. Architecture recap

| Service   | Image / runtime            | Notes                                             |
|-----------|----------------------------|---------------------------------------------------|
| postgres  | `pgvector/pgvector:pg16`   | data + pgvector matching; RLS enforced            |
| redis     | `redis:7-alpine`           | Celery broker/result + OTP rate-limit counters. Host port **6380** (in-network stays `redis:6379`) |
| backend   | `../backend` (uvicorn)     | FastAPI API on `:8000`                            |
| worker    | `../backend` (celery)      | all async work: email, SMS, matching, parsing, uploads |
| beat      | `../backend` (celery beat) | schedules `refresh_dashboard_views` every 5 min   |
| frontend  | `../frontend` (Next.js 14) | `:3000`; prod deploys to Vercel natively          |

One backend image runs API, worker, and beat (different commands) so
dependencies never drift.

---

## 2. Required environment / secrets

Backend + worker read the **entire** `.env` (compose `env_file: ../.env`). See
`.env.example` for the authoritative template; frontend uses
`frontend/.env.local.example`. Never commit real secrets.

### Backend (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Postgres (asyncpg driver) |
| `REDIS_URL` | yes | Celery broker + OTP counters |
| `JWT_SECRET` | yes (prod) | signs app-session cookies — set a strong random value |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | yes | Firebase Admin service-account key, **minified to one line**; verifies ID tokens |
| `CLOUDINARY_URL` | yes | resume storage/upload |
| `MAILTRAP_API_TOKEN` | yes (email) | Mailtrap Sending API token — **PENDING** (see Known Gaps) |
| `MAILTRAP_SENDER_EMAIL` | yes (email) | verified From address |
| `MAILTRAP_SENDER_NAME` | no | From display name (default `PickReady`) |
| `MAILTRAP_API_HOST` | no | `send.api.mailtrap.io` (real) — leave default in prod |
| `MAILTRAP_INBOX_ID` | no | set to route through a Testing/sandbox inbox in dev |
| `MSG91_API_KEY` / `MSG91_SENDER_ID` | yes (SMS) | MSG91 transactional SMS (manually-tested feature) |
| `GROQ/GEMINI/OPENROUTER_API_KEY_1..3` | yes (matching) | LLM router keys with fallback (ESD §8.4) |
| `LLM_KEY_ENCRYPTION_SECRET` | yes (matching) | encrypts stored provider keys |
| `BGE_M3_ENDPOINT` | yes (matching) | embedding endpoint for semantic stage |
| `ENVIRONMENT` | yes | `development` enables OTP `debug_code`; use `production` live |
| `FRONTEND_URL` | yes | CORS + email link base |

> `RESEND_API_KEY` is **deprecated** — Mailtrap replaced Resend for all outbound
> email (claude.md rule 5). The app no longer reads it.

### Frontend (`.env.local`)

All `NEXT_PUBLIC_FIREBASE_*` values (API key, auth domain, project ID, storage
bucket, messaging sender ID, app ID) from Firebase console → Project settings →
Web app, plus `NEXT_PUBLIC_API_URL`. These are publishable client config, not
secrets, and are baked into the browser bundle at build time.

---

## 3. Firebase console setup (auth for ALL roles)

1. **Authentication → Sign-in method** — enable providers:
   - **Email/Password** (candidates may use passwords — the recorded exception
     to claude.md rule 2).
   - **Google** (restricted to candidates by the backend provider gate).
   - **Phone** (Google SMS, not MSG91).
2. **Authentication → Settings → Authorized domains** — add every domain the
   web app is served from (`localhost` for dev, the Vercel domain, and any
   custom domain). **Phone auth silently fails from an unauthorized domain.**
3. **Project settings → Service accounts → Generate new private key** — download
   the JSON, minify to a single line, set as `FIREBASE_SERVICE_ACCOUNT_JSON`.
4. Confirm the frontend `NEXT_PUBLIC_FIREBASE_*` values match the same project.

Backend contract: `POST /auth/firebase/session` verifies the ID token
(`check_revoked=True`), enforces the provider gate (`password`/`phone`/`google.com`;
Google is candidate-only), then issues the normal scoped app-session cookies.
Database roles/permissions stay authoritative — Firebase only proves identity.

---

## 4. Mailtrap setup

1. Create a **Sending** stream; verify your sending domain (SPF/DKIM) in
   Mailtrap. An unverified `From` bounces/rejects.
2. Set `MAILTRAP_API_TOKEN`, `MAILTRAP_SENDER_EMAIL` (a verified address),
   `MAILTRAP_SENDER_NAME`.
3. Dev/testing: set `MAILTRAP_INBOX_ID` to capture mail in a sandbox inbox
   instead of delivering (host switches to `sandbox.api.mailtrap.io`).
4. Client-domain sending: outbound tenant mail must go through the tenant's
   verified sending domain — never Gmail/Outlook OAuth (claude.md rule 5).

---

## 5. Cloudinary & MSG91

- **Cloudinary**: create a product environment, copy the `CLOUDINARY_URL`
  (`cloudinary://<key>:<secret>@<cloud>`). The worker uploads resumes here; the
  URL is stored on `profiles.resume_url`.
- **MSG91 (SMS)**: retained as a **manually-tested** feature — with Firebase-for-
  everyone it is no longer the login path. For live SMS, register the sender ID
  and a DLT-approved template, then verify via the `pickready.send_sms` task /
  the OTP send-path. Missing keys warn loudly at startup but do not hard-crash.

---

## 6. Migrations & seed

```bash
docker compose -f infra/docker-compose.yml up --build -d
docker compose -f infra/docker-compose.yml exec backend alembic upgrade head
docker compose -f infra/docker-compose.yml exec backend python -m app.scripts.seed_dev_data
```

Migration chain head is `0003_firebase_identity` (adds `users.firebase_uid`
unique + `users.auth_providers`). The seed provisions the Owner
(`manjuchro@gmail.com`, the sole `super_admin`), demo tenants/staff, a
multi-context identifier, and the resume corpus (Cloudinary uploads when
`CLOUDINARY_URL` is set).

---

## 7. Validation harnesses

Run both against the **running** stack; both print a PASS/FAIL summary and exit
non-zero on any hard failure (CI/smoke gate).

```bash
# Auth surface — legacy-OTP flows + Firebase route wiring
docker compose -f infra/docker-compose.yml exec -T backend python -m app.scripts.validate_auth

# Broad readiness — DB/migrations/redis/celery/env/seed/matching
docker compose -f infra/docker-compose.yml exec -T backend python -m app.scripts.validate_stack
```

- `validate_auth` requires `ENVIRONMENT=development` (needs the OTP `debug_code`).
  Firebase checks (`[firebase] …`) assert the `/auth/firebase/session` route
  exists and rejects a bogus token with 401 (a real token can't be minted from a
  script); legacy-OTP checks (`[legacy-otp] …`) still exercise the full OTP login,
  multi-context chooser, lockout, and cross-portal token isolation.
- `validate_stack` checks: DB reachable + migrations at head; Redis; Celery
  worker ping; required env (Firebase JSON valid + Cloudinary present hard;
  Mailtrap token WARN-only); seed sanity (sole super_admin = Owner, ≥25
  candidates with `resume_url`, multi-context identifier resolves to 2+ users);
  and a persisted 4-parameter matching breakdown for ≥1 job.

Backend unit/integration suite: `docker compose -f infra/docker-compose.yml exec -T backend python -m pytest tests -q`.

---

## 8. Known gaps (as of 2026-07-24)

- **Mailtrap token pending.** `MAILTRAP_API_TOKEN` is not yet in `.env`; email
  delivery is disabled until it lands. `validate_stack` reports this as **WARN**
  (not a hard failure) and the startup preflight logs it. Email code reads the
  token from env — no code change needed once the key is added.
- **Firebase phone auth needs authorized domains.** Phone sign-in fails from any
  domain not listed in Firebase → Authentication → Settings → Authorized domains.
  Add all serving domains before enabling phone login in production.
- **Resume seed vs live DB.** The Cloudinary resume corpus is applied by the seed
  script; if `validate_stack` reports fewer than 25 candidates with `resume_url`,
  run `seed_dev_data` (with `CLOUDINARY_URL` set) against that environment.
