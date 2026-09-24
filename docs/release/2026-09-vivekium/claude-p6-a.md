# CLAUDE.md section draft: Phase 6 WP6-A (candidate identity and sessions)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
Migration `0120_candidate_identity` (the orchestrator re-chains it).

## ONE PERSON, ONE CANDIDATE RECORD, RESOLVED ONE WAY

`services/candidate_identity` is the only answer to "which candidate record is
this signed-in person". Four resolvers answered it before and disagreed: the
portal matched `user_id = uid OR email = users.email` with no ORDER BY and
lazily linked whatever row came back; the BGV and conversation routes ran the
same OR in raw SQL and never linked; the assessment and proctoring routes read
`user_id` alone. So one person could be two candidates depending on the screen,
and the email half matched an address nobody had verified: a password sign-up
carrying somebody else's address resolved to THEIR recruiter-sourced record.

- **A request resolves by `candidates.user_id` and nothing else**
  (`resolve_candidate_id`, `require_candidate`). There is no request-time email
  fallback anywhere. `require_candidate` is the chokepoint and stamps
  engagement (feature 8), the job `portal._record_engagement` used to do.
- **Email matching happens at SIGN-IN, and only on an address Firebase says is
  verified** (`link_on_sign_in`, called for EVERY candidate sign-in from
  `_finalize_single`). It links the OLDEST unlinked record with that address,
  case-insensitively, and audits `candidate_linked`. An unverified identity
  gets a record of its own and links nothing, deliberately: it proved nothing
  about the address. The call is idempotent and also repairs an account whose
  record an administrator erased.
- **One user owns at most one record, by the database.** Migration 0120 makes
  `candidates.user_id` UNIQUE where set, behind a guard that RAISES naming the
  count if any user already owns two (merging is an owner decision; pilot had
  one candidate and no duplicates on 2026-09-24).
- **Conversion re-points ONE link; it never merges records.**
  `rehome_sourced_link` moves a `sourced` link on the job from an UNLINKED
  record with the same address onto the verified applicant, audited as
  `candidate_link_rehomed`, and the caller then runs `sourced -> applied`.
  Twenty-odd tables reference `candidates.id`; a bulk merge on live data is
  erasure-grade risk for a problem one UPDATE solves.
- **`find_canonical_by_email` is the one upload lookup**: the signed-in record
  first, then the oldest, case-insensitive through `ix_candidates_email_lower`.
- `tests/test_candidate_identity.py` sweeps `app/api` and `app/services` by AST
  for `Candidate.user_id ==`, `Candidate.email ==` and the raw SQL resolvers,
  with a `LEGACY_RESOLVERS` ratchet naming each remaining file and the work
  package that converts it. The list only shrinks: a converted file left in it
  fails as stale.

## PHONE SIGN-IN IS REMOVED

Owner ruling. `_phone_aliases`, the phone match in `firebase_session`, the
phone-only candidate creation, both `provider == "phone"` branches and the
phone-reuse 409 are deleted; `FirebaseIdentity` has no `phone` field; the
workspace chooser (`login_context.find_users`, `/auth/workspaces`) matches
email only, case-insensitively, because a phone match there offered one person
another's workspace whenever imported data shared a number. The provider stays
REFUSED (`firebase_auth.ALLOWED_PROVIDERS`). `users.phone` and
`phone_verified_at` survive as CONTACT data (S4). A sign-in with no email is
422 "An email address is required to create a candidate profile".
`tests/test_phone_login_removed.py` sweeps the tree.

**SUPERSEDES general rule 2's "Google, email/password, and phone"** (section 3
of this file): sign-in is Google or email/password, for every role.

## THE IDLE DEADLINE MOVES FOR A PERSON, NEVER FOR A TIMER

The 2026-09-20 section says the thirty minutes are "touched only by REAL USER
ACTIVITY, never by a polling tab". **That sentence was prose only**:
`auth_sessions.validate` ran `EXPIRE` on every authenticated request and
`rotate` did the same on every refresh, so a polling tab stayed signed in for
ever, and a poll's 401 after the fifteen-minute access expiry was repaired by a
refresh that renewed it again. It is true now:

- **`validate(..., touch=)` and `rotate(..., touch=)` take a REQUIRED flag**,
  no default, so a new caller decides rather than inherits. `touch=False` is a
  single HGET; `_ROTATE` renews only when ARGV[8] is `'1'`.
- **The request says whether it is activity**: `X-User-Activity: 1`
  (`deps.ACTIVITY_HEADER`, `deps.is_user_activity`), read by every
  authenticated dependency and by `/auth/refresh`. `create` always sets the
  deadline: signing in is activity.
- **The browser sends the header only within five seconds of a real pointer,
  key or touch event** (`frontend/lib/user-activity.ts`, capture phase,
  installed once by `auth-context`). `lib/api.ts` attaches it on the JSON path,
  the raw fetch path, the upload path and the refresh. The auth context's
  interaction revalidation (`/auth/me`, five-minute throttle) is what keeps a
  person who is reading or typing, and so sending nothing else, signed in.
- **A socket checks the session and never renews it.**
  `deps.authenticate_socket_token` applies the REST rule (a `sid` is required,
  the store is asked, a store outage refuses) with `touch=False`. The
  conversation socket used to decode the JWT and never ask, so a revoked
  session kept streaming.
- The header is not a credential: forging it keeps only the forger's own
  session alive, which a real click also does.
- `tests/test_idle_timeout_activity.py` proves all of it over REAL Redis and
  the REAL HTTP stack with no dependency overrides, reading `TTL` off the key.
  Mutation-checked in both scripts.

The stale "the access cookie has a 15-minute Max-Age" comments in
`lib/api.ts` and `lib/authenticated-fetch.test.ts` now say the JWT expires;
every auth cookie is a browser-session cookie.

## `firebase_auth` CHANGES

- `delete_identity(uid)`: synchronous (call through `run_in_threadpool`),
  idempotent (`UserNotFoundError` is True), anything else raises
  `IdentityDeletionFailed`. For WP6-B's Delete My Profile.
- The pre-6.2 `TypeError` fallback in `verify_id_token` is DELETED:
  `requirements.txt` pins `firebase-admin>=6.5`, so the branch could only ever
  run on a silently downgraded image, and would then have hidden that.

## A REAL CANDIDATE SESSION FOR TESTS

`tests/candidate_session.py` mints a candidate the production way: rows through
`link_on_sign_in`, the session record and cookies through `auth._issue_session`.
Use it with NO dependency overrides. The BGV candidate routes were unreachable
for every real candidate for weeks while every test passed, because every test
overrode both the staff principal and the candidate session; an override is a
claim about who is calling, and this replaces the claim with the thing. It does
not skip: a helper that quietly skipped would turn every test built on it back
into the untested claim.

## HARNESS

`harness/doubles/redis.py` emulates the new `_ROTATE` (the ARGV[8] flag) and
serves the untouched check through `hget`; its exact-text recognition of the
product's scripts is unchanged, so the next edit to either script still makes
the double refuse rather than emulate yesterday's semantics.
