# PickReady — Build Log

Running log of substantive build decisions and work. Newest entries at top.

---

## 2026-07-24 — Production sprint: Firebase auth, Mailtrap email, resume seeding

**Directive:** Firebase auth for ALL roles (Google / email+password / phone),
Mailtrap replaces Resend for all email, MSG91 SMS must remain working (checked
manually), only rating *comments* visible in UI (not numbers), seed 33 sample
resumes to Cloudinary + candidate self-upload, everything production-ready.

**User decisions (asked before proceeding):**
- Auth model: **Firebase for everyone.** Firebase is the primary login for all
  roles. Firebase phone auth uses Google's SMS, not MSG91.
- Email: **Mailtrap replaces Resend everywhere.**
- Passwords: **allowed** for candidates via Firebase — this overrides
  claude.md rule 2 ("no passwords, ever"). Recorded as an exception in claude.md.

**Foundation (main agent, done first):**
- Migration hygiene: `0002_firebase_identity.py` was internally revision `0003`
  (misleading filename) — renamed to `0003_firebase_identity.py`. Chain is
  `0001 → 0002_role_model_and_breakdown → 0003_firebase_identity`; DB already at
  head `0003`. `users.firebase_uid` (unique) and `users.auth_providers` (jsonb)
  exist and are mapped on the User model.
- Firebase already provisioned: real creds in `frontend/.env.local`
  (`NEXT_PUBLIC_FIREBASE_*`) and backend (`FIREBASE_SERVICE_ACCOUNT_JSON`).
  Stubs present: `frontend/lib/firebase.ts`, `frontend/lib/firebase-session.ts`,
  `backend/app/services/firebase_auth.py` (token verify + provider gate).
- Numerical ratings already hidden (commit `1dbbc2b`, comments-only) — verifying,
  not redoing.

**Open blocker:** Mailtrap API key + snippet not yet provided — email integration
is being coded against Mailtrap's Sending API reading the token from env; delivery
can't be tested until the key lands in `.env`.

**MSG91 note:** With Firebase-for-everyone, MSG91 is no longer the login path. The
existing `/auth/otp/*` MSG91 SMS send-path is being kept alive and testable so the
SMS feature can be verified manually.

### Progress

- **[Agent 2 — Mailtrap email] done.** Swapped Resend → Mailtrap Sending API for
  all outbound email. New `services/mailtrap_service.py` (httpx, no SDK) reuses the
  shared permanent-vs-transient delivery taxonomy; `workers/tasks.py` email path,
  `email_render.text_to_html`, and config email settings updated; preflight now
  checks `MAILTRAP_API_TOKEN`. Request: `POST {host}/api/send` (sandbox host + inbox
  path when `MAILTRAP_INBOX_ID` set), Bearer token. 218 tests pass. **Live delivery
  pending `MAILTRAP_API_TOKEN` in `.env`** (verified sender required). Env to set:
  `MAILTRAP_API_TOKEN`, optional `MAILTRAP_SENDER_EMAIL`/`_NAME`/`MAILTRAP_INBOX_ID`.
- **[Agent 4 — Frontend Firebase UI] done.** Rewrote `login-flow.tsx` +
  `register-flow.tsx` for Firebase: Continue-with-Google (`signInWithPopup`),
  email/password, and phone (`signInWithPhoneNumber` + invisible reCAPTCHA, 6-box
  code via existing `OtpInput`). `exchangeFirebaseSession` now returns
  `AuthSession | AuthContextsResponse`; multi-workspace identities render the
  "Choose your workspace" chooser → `/auth/select-context`. `friendlyAuthError`
  maps every Firebase/backend error to clean copy (403 staff-Google →
  "not available for your account"); no stack traces. Logout also
  `firebaseAuth.signOut()`. Routes/middleware unchanged. `next build` clean, 0
  type errors. Live Google/SMS + the backend single-vs-multi branch need real
  sign-in to confirm (reserved for the user).
- **[Agent 6 — Integration/validation] done.** `.env.example` +
  `frontend/.env.local.example` made authoritative for the Firebase+Mailtrap
  stack (Mailtrap vars added, Firebase/Cloudinary documented, Resend deprecated).
  New `validate_stack.py` readiness harness (DB/migrations/Redis/Celery/env/seed/
  matching, PASS/FAIL/WARN, non-zero exit). `validate_auth.py` extended with
  Firebase checks (`/auth/firebase/session` exists + rejects bogus token 401),
  checks labeled `[firebase]`/`[legacy-otp]`. New `docs/DEPLOYMENT.md`. Compose:
  confirmed backend+worker+beat get all secrets via `env_file`. Final:
  validate_auth 13/13, validate_stack 10/11 + 1 Mailtrap-token warn.
- **[Agent 1 — Firebase backend hardening] done.** Hardened
  `/auth/firebase/session`: owner email → seeded `super_admin` (any provider incl.
  Google, never fabricated, 403 if owner row missing); staff pre-seed linked by
  email (no duplicate); multi-workspace identity → `{contexts, context_token}` +
  `/auth/select-context` (was a 409); phone-only candidates supported. New
  migration `0004_nullable_email_for_phone_signup` (users.email + candidates.email
  nullable, applied). MSG91 OTP endpoints untouched. `UserOut.email` now optional.
  276 tests pass.
- **CONSOLIDATION TODOs (main agent):**
  1. Sync ORM to migration 0004: `User.email` and `Candidate.email` →
     `Mapped[str | None]`, `nullable=True` (DB already nullable; avoids autogenerate
     drift).
  2. `seed_resumes.py:176` `scalar_one_or_none()` → `MultipleResultsFound` on
     duplicate candidate emails; breaks seed idempotency. Verify the resume-seed
     agent fixed it, else fix (`.first()`/limit or unique emails).
  3. Confirm `test_firebase.py::test_phone_only_candidate_signup_allowed` passes in
     the final merged state (agent 5 saw it fail mid-flight; agent 1 finished at
     276 green — likely a snapshot-timing artifact).
- **[Agent 5 — AI pipeline] done.** `resume_parsing.py`: docx (python-docx) + pdf
  (pypdf) extraction with magic-byte sniffing for extension-less Cloudinary raw
  URLs; never raises on bad content (empty → NULL embedding, excluded from
  semantic pool). `matching.py` graceful degradation: embeddings down →
  keyword-only; LLM chain down → deterministic retrieval-rank breakdown (capped at
  Moderately, comments flagged "AI scoring unavailable"); one bad candidate never
  aborts the batch. Weights 35/30/20/15, Python-side overall, 1-decimal rounding,
  top-down inclusive-upward tiers — locked by tests (exactly-90 → Highly).
  `verification_parsing.py` degrades to null schema on junk replies. Numeric
  breakdown still fully stored server-side (UI hiding is presentational only). Live
  run confirmed correct tiers. Own suites green.
- **[Agent 3 — Resume→Cloudinary + upload] done.** New `seed_resumes.py`:
  30 `.docx` → 30 Cloudinary raw assets (`pickready/resumes`, deterministic
  public_id, `overwrite=False`) + 30 shared-databank Candidate + Profile rows
  (`@candidates.pickready.test`, `consent_databank=True`), each enqueuing
  `parse_resume`. Idempotent (runs 2/3 = 0 new). `store_resume` hardened: accept
  pdf/doc/docx, 422 wrong-type/empty, 413 >10MB, threadpool, fail-soft.
  `apply_to_job` mints a fresh Profile per application (rule 6). 276 tests pass.
  **Repro note:** resumes aren't in the backend image — before seeding in-container
  run `docker compose -f infra/docker-compose.yml cp ./resumes backend:/resumes`.

## 2026-07-24 — PRD v1.0 alignment sprint (simplification)

**Directive:** align to PRD v1.0. The doc contradicted itself in 4 places; user
settled each:
- **Email → SMTP from the FastAPI backend** (provider-agnostic, env-driven
  `SMTP_*`; replaces the Mailtrap HTTP API). Interpretation of "FastAPI in-built
  SMTP" = a generic SMTP sender (aiosmtplib) usable with Mailtrap SMTP / Gmail
  SMTP / any provider.
- **Auth**: keep Google + email/password + phone (all built, unchanged).
- **Resume**: **reuse across applications** (store on profile) — reverses the old
  fresh-upload-only rule (claude.md rule 6 updated).
- **Roles**: **simplify per §4 (FINAL)** — HR Manager / Recruiter / Hiring Manager
  are equal, all create jobs, shared candidate pool, **direct job publish** (no
  multi-level approval). Engineering call: flatten the permission matrix + bypass
  the approval FSM rather than deleting the engine (lower risk, reversible,
  identical UX). claude.md rule 3 updated.
- **New**: public job link `picready.com/{job_uuid}` + open candidate application
  (register → 40-question questionnaire → resume upload/reuse → apply), not
  outreach-gated. AI JD generation (FR-3.3 Path A) + AI-personalized outreach
  emails (FR-5.3).

Contradictory PRD lines treated as stale boilerplate (superseded by the answers):
§5 "OTP only / no passwords / no Gmail / resume never stored", §6 full permission
matrix, §8–9 OTP. Recorded here so the reversal is auditable.

### Progress (PRD v1.0 sprint)

- **[Agent 2 — Open application + resume reuse] done.** Dropped the
  outreach/contacted-tenant gate: `GET /portal/jobs` lists all published
  (ratified) jobs across tenants; new `GET /portal/jobs/{job_id}` serves the
  public `picready.com/{uuid}` target; `POST /portal/jobs/{job_id}/apply` takes
  `aspects` (40-question JSON) + `resume` file OR `reuse_previous=true` (carries
  the newest Profile's resume_url onto a new Profile; each application still gets
  its own Profile + aspects). Consent + aspects_completed_at captured, parse task
  enqueued, 413/422 validation intact. 14 tests green.
  - **CONSOLIDATION TODOs:** (a) optional cleaner reuse — add
    `Candidate.last_resume_url` column + migration (currently reuses newest
    Profile); (b) `candidates.update_pipeline_status` forward-gate requires
    VerificationRequests, which open applicants don't have (employer verification
    is a §5 non-goal now) — relax the gate so open applicants aren't blocked.
- **[Agent 1 — Email → SMTP] done.** All outbound email migrated from the Mailtrap
  HTTP API to provider-agnostic backend SMTP: new `services/smtp_service.py`
  (aiosmtplib, MIME multipart/alternative + base64 attachments, STARTTLS 587 /
  implicit SSL 465), env-driven `SMTP_*`, preflight checks SMTP_HOST/USER/PASSWORD
  (+MSG91). Celery retry/backoff/audit + permanent-vs-transient taxonomy preserved;
  `send_email` signature unchanged. `mailtrap_service.py` retained but unused.
  `aiosmtplib>=3.0` added. 30 email tests pass. **Env to set:** `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` (Gmail: 587 +
  STARTTLS + app password; or Mailtrap SMTP creds).
- **[Agent 4 — AI content services] done.** New `services/jd_generation.py`
  (`generate_job_description(brief) -> JDIn-shaped dict`, FR-3.3 Path A) and
  `services/outreach_content.py` (`generate_outreach_email(candidate, job, company,
  kind) -> {subject, html, text}`, FR-5.3). Both route via `llm_router` with a
  corrective retry then deterministic template fallback (never raise on
  LLM-unavailable); outreach HTML-escapes all interpolation. 15 tests, no network.
  Note: JD schema is `JDIn` in schemas/jobs.py (not `JobJD`) — agent 3's call site
  reads it directly.
- **[Agent 6 — Integration/validation/docs] done.** `.env.example` +
  `docker-compose.yml` moved Mailtrap HTTP → `SMTP_*` (Mailtrap-SMTP or Gmail
  app-password); `MAILTRAP_*`/`RESEND_*` deprecated. `validate_stack.py` extended
  for PRD v1.0: SMTP warn (Firebase/Cloudinary still hard) + published-job
  public-link, flat 3-staff-role matrix equality, open-application checks
  (discovery-based). `API_CONTRACT.md` + `DEPLOYMENT.md` → rev 3. **Final backend:
  pytest 319 passed 0 failed, validate_stack 13/14 (1 SMTP warn), validate_auth
  13/13, migrations head 0006.** Confirms agent 3's flatten/direct-publish/public-
  link/generate-jd landed (all 3 staff roles = identical 13-cap set).
- **[Agent 5 — Frontend PRD v1.0] done.** New public `/apply/[job_uuid]` page:
  inline Firebase candidate auth (Google/email-pw/phone, no forced redirect),
  40-aspect questionnaire, resume upload-or-reuse, confirmation. Staff job-create
  form gained "Generate with AI" (`POST /jobs/generate-jd`, editable, manual
  fallback) + copyable public link on publish. Flat nav: removed Approval-Levels +
  Approvals pages/nav, ungated Jobs/Review/Dashboard for all staff (only
  client-admin items stay gated). Resume-reuse added to candidate portal apply.
  Ranking UI confirmed comments-only. `next build` clean, 0 type/lint errors.
  - **CONSOLIDATION TODOs:** (c) frontend job fetch tries `/jobs/{id}` then
    `/portal/jobs/{id}`; canonical public read is `/jobs/public/{id}` — verify the
    fallback resolves (agent 2's `/portal/jobs/{id}` should catch it) or point it
    at the public endpoint. (d) `docs/PRD.md` still documents the old approval-FSM
    model — update to PRD v1.0 flat/direct-publish.
- **[Agent 3 — Jobs/roles simplification] done.** Flattened
  DEFAULT_PERMISSION_MATRIX — HR Manager/Recruiter/Hiring Manager identical with
  the full 13-cap operational set; Company Admin keeps company/staff mgmt +
  create_job; EDIT_ROLE_PERMISSIONS stays Owner-only. Jobs publish directly via new
  `approval_fsm.plan_direct_publish`/`apply_direct_publish` (stamps
  ratified/ratified_at, logs 4 levels skipped; FSM dormant not deleted);
  submit/approve return 409 gracefully. Added `public_url` (`frontend_url/{uuid}`)
  on published JobOut, `GET /jobs/public/{id}` (unauthenticated, published-only),
  `POST /jobs/generate-jd` (503-defensive). Seed permission template reconciles
  `allowed` (idempotent). 319 tests pass.
  - **CONSOLIDATION TODO (e):** backend `public_url` = `frontend_url/{uuid}` but the
    actual page is `/apply/{uuid}` (agent 5) — MISMATCH; fix the backend link to
    `/apply/{uuid}`.

### Consolidation (main agent) — PRD v1.0 sprint

All 6 agents merged. Main-agent fixes applied:
1. **Public link path mismatch fixed** — backend `public_job_url` was
   `frontend_url/{uuid}` but the page lives at `/apply/{uuid}`; corrected to
   `frontend_url/apply/{uuid}` (+ updated 3 tests).
2. **Pipeline forward-gate relaxed** — `update_pipeline_status` no longer requires
   VerificationRequests (employer verification is a §5 non-goal); open applicants
   move forward once the 40-question application is complete. Dropped the now-unused
   `VerificationStatus` import.
3. **Public apply page fetch order** — frontend now tries `/jobs/public/{id}` FIRST
   (unauthenticated) so a visitor sees the job before signing in (FR-3.5), then
   auth-scoped fallbacks.
4. **docs/PRD.md** — added a SUPERSEDED banner pointing to PRD v1.0 / API_CONTRACT
   rev 3 (flat roles, direct publish, public link, open application, SMTP, Firebase).

Left as-is (works, low value/high risk to change now): `Candidate.last_resume_url`
column — reuse via newest-Profile scan is tested and functional; a migration is a
future polish, not needed.

**Final verification (all green):**
- Backend suite: **319 passed, 0 failed.**
- `validate_stack`: **13/14 (1 SMTP-creds warn), 0 fail.**
- `validate_auth`: **13/13.**
- Frontend `next build`: clean, 22 routes incl. `/apply/[job_uuid]`.
- Live smoke: `GET /jobs/public/{id}` 200 unauthenticated (title+company), bogus → 404.
- **Pending external:** set `SMTP_*` creds in `.env` for live email; set
  `FRONTEND_URL=https://picready.com` in prod for correct public links.

## Consolidation (main agent) — Firebase/Mailtrap sprint

- All 6 agents merged. Applied ORM `User.email`/`Candidate.email` → nullable
  (sync with migration 0004).
- **Final consolidated verification (all green):**
  - Backend suite: **276 passed, 0 failed** (the disputed phone-only + seed
    idempotency tests both pass in the merged state — mid-flight artifacts).
  - Seed idempotent: two full runs → 0 new, no crash. DB: 1 super_admin (Owner),
    30 databank candidates with resume_url, alembic head 0004.
  - `validate_auth`: **13/13** (11 legacy-OTP + 2 Firebase).
  - `validate_stack`: **10/11 pass, 1 warn (Mailtrap token), 0 fail.**
  - Frontend `next build`: clean, 24 routes.
- **Remaining external actions (not code):** (1) add `MAILTRAP_API_TOKEN` (+ a
  verified sender) to `.env` for live email; (2) in the Firebase console enable
  Google/Email-Password/Phone providers + add authorized domains; (3) MSG91 live
  mode for real SMS. See docs/DEPLOYMENT.md.
