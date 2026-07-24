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
