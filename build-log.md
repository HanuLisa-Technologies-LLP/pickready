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

## Consolidation (main agent)

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
