# SECURITY_REPORT.md

**Scope.** Frontend, backend, database, infrastructure and CI of the ReadyPick
repository. **Date.** 2026-09-17.
**Method.** Read of every router under `backend/app/api/`, the Terraform tree,
the CI workflow and the frontend, plus `npm audit`, targeted test runs, a
production `next build`, and read-only AWS describe calls against the live
account.

**No penetration test was performed.** Nothing below was proven by exploiting a
running system. Findings are from source, and each one records what was actually
run to verify the fix.

---

## Severity summary

| Severity | Found | Fixed and verified | Fixed, activation pending | Documented, not changed |
|---|---|---|---|---|
| CRITICAL | 2 | 2 | 0 | 0 |
| HIGH | 5 | 4 | 1 | 0 |
| MEDIUM | 9 | 8 | 0 | 1 |
| LOW | 3 | 0 | 0 | 3 |

---

## CRITICAL

### SEC-1. Next.js was in an unauthenticated-RCE range

**Component.** `frontend/package.json`, `next@16.2.12`, and `sharp` transitively.

**Root cause.** The dependency was never bumped past the vulnerable range.
`npm audit --omit=dev` reported `next` CRITICAL (GHSA-p293-qw3h-jr36,
"Unauthenticated Remote Code Execution on windows-hosted servers", and
GHSA-2xp9-vwfh-vxw4, RCE in the Image Optimization API for AVIF) affecting
`>=16.0.0 <16.3.3`, plus `sharp <0.35.4` HIGH (GHSA-rgj7-g3m4-5g8c, libheif
memory corruption).

**Fix.** Upgraded to `next@16.3.5` and `sharp@0.35.4`, then `npm audit fix` for
the remaining development-only advisories.

**Verification.** VERIFIED. `npm audit --omit=dev` and `npm audit` both report
**0 vulnerabilities**. The framework upgrade was then regression tested:
`tsc --noEmit` clean, `eslint` clean, **261/261 vitest tests pass**, and
`next build` succeeds.

### SEC-2. An untimed Redis client sat on the RBAC hot path

**Component.** `backend/app/services/tenant_cache.py`, reached from
`services/rbac._permission_rows`, which `require_capability` calls on
essentially every authorized route.

**Root cause.** It built `aioredis.from_url(...)` with no
`socket_connect_timeout` and no `socket_timeout`, and opened and closed a fresh
client on every call, while `app/core/cache.py` next to it did all of that
correctly. Its `except Exception` guards catch a RAISED error; they cannot catch
a HANG. A Redis network black hole (a security-group change, an overloaded
instance, a routing blip, not a crash) would therefore have stalled **every
RBAC-gated request in the product** rather than failing over to Postgres in
milliseconds. This is the failure class the repository already documents at
length for boto3 and for the message broker.

**Fix.** `tenant_cache` is now a thin facade over `app/core/cache.py`, which has
the timeouts, a lazily-cached client and an unavailability latch. Same module
name, same key format, same public API, so no caller changed. An
`invalidate_pattern` was added to the surviving implementation so
`delete_pattern` survived the consolidation, and a per-event-loop guard was added
to the shared client, because a process-wide singleton holding asyncio state is a
bug class this repository has already been bitten by.

**Verification.** VERIFIED by a new regression guard,
`backend/tests/test_redis_client_timeouts.py`: an AST sweep over all of `app/`
asserting every Redis construction sets both socket keywords, with a named ledger
for the two operator-only CLI scripts and a staleness test in both directions.
That is the test that would have caught the original bug.

---

## HIGH

### SEC-3. The stack shipped exactly one security header

**Component.** `frontend/next.config.js`; `backend/app/main.py` registers only
CORS, GZip and a timing middleware.

**Root cause.** Only HSTS was ever added. No CSP, no `X-Frame-Options` or
`frame-ancestors`, no `X-Content-Type-Options`, no `Referrer-Policy`, no
`Permissions-Policy`. The `Referrer-Policy` gap is the sharpest one in this
product specifically: it serves tokenised URLs (`/verify-employment/<token>`,
`/portal/outreach/<token>`, assessment invites), and with no policy the full URL
including the token was sent in the `Referer` header to every third party a page
linked out to.

**Fix.** A full header set in `next.config.js`, applied in every environment
(HSTS still production only, because it is a browser-persistent commitment). The
CSP allowlists the origins this app genuinely contacts, derived by inventory
rather than guessed: only `checkout.razorpay.com` is an external script, the
proctoring models and workers are same-origin, and the fonts are now self-hosted
so `font-src 'self'` is complete.

**A stated weakness, not an oversight.** The policy keeps `'unsafe-inline'` and
`'unsafe-eval'` for scripts. `unsafe-eval` is non-negotiable: the proctoring
workers run TensorFlow.js, which compiles kernels through `new Function`. The
`unsafe-inline` is a deliberate trade recorded in the file: a nonce CSP in Next
must be minted per request in middleware, which forces every route to dynamic
rendering and gives up static optimisation on the public pages, for a benefit
this app largely does not collect since there is no `dangerouslySetInnerHTML`
anywhere in the tree. What the policy does buy is what was missing entirely:
scripts cannot load from an arbitrary origin, the page cannot be framed,
`base-uri` cannot be hijacked, and `form-action` cannot be pointed at a
collector.

**Verification.** PARTIAL. The config was proven to generate the header set, and
the app was loaded in a browser with the headers active: no CSP violation
appeared and every chunk loaded. **The Razorpay checkout flow and the Google
sign-in popup were NOT exercised**, because both need a live payment and a live
Firebase session. Their origins are allowlisted from the vendors' documented
endpoints, but that is reasoning, not a test. Exercise both immediately after
deploy.

### SEC-4. Nothing prevented indexing of the portals or the tokenised links

Covered in full in `SEO_REPORT.md` (S1). Recorded here because it is a
confidentiality finding as much as an SEO one: the three tokenised routes are
reachable with **no authentication at all** and inherited a fully indexable
title. FIXED and VERIFIED against built HTML.

### SEC-5. The inbound-email webhook was unauthenticated

**Component.** `backend/app/api/verification.py`,
`POST /verification/inbound-email`; `lambda/inbound_email/handler.py`.

**Root cause.** The route is mounted with `get_public_db` and no signature, no
shared secret and no header check, and it writes into `verification_requests`,
BGV threads and conversation records. Its only protection was that a caller had
to know a per-thread token, and those travel by email, so they exist in every
mailbox that ever received or forwarded one of these threads. Nothing stopped a
POST straight at the API, bypassing SES, its DKIM and SPF checks, and the relay
Lambda entirely. Both sibling webhooks do this properly: the SES event webhook
verifies an SNS RSA signature and the Razorpay webhook verifies an HMAC.

**Fix.** A shared relay secret. The Lambda sends `X-ReadyPick-Webhook-Secret`;
the API compares it with `hmac.compare_digest` and answers 403 on a mismatch.

**Empty is a real state**, the same shape `inbound_email_domain` already uses: an
environment that has not been given the value keeps accepting genuine replies and
logs `verification.inbound_unauthenticated` on every call, rather than refusing
every employer reply the moment the code ships.

**Verification.** The CODE is VERIFIED:
`backend/tests/test_inbound_relay_secret.py` asserts refusal with no header, with
a wrong secret and with a prefix of the secret; asserts our own relay is ADMITTED
(the direction that matters most, since a gate that refused the Lambda would
silently drop every employer reply); asserts the unconfigured state stays open;
asserts the comparison is constant time; and asserts the dependency is actually
wired to the route.

**ACTIVATION STATUS** is recorded in `PRODUCTION_READINESS.md`. Until the secret
is provisioned to both the API and the Lambda, the route remains open and says so
in the log.

### SEC-6. The interactive API documentation was served on the live site

**Component.** `backend/app/main.py`.

**Root cause.** `docs_url="/docs"` with no environment guard, and `redoc_url` and
`openapi_url` left at their defaults, so the complete API surface of a
multi-tenant hiring platform, with a point-and-click client attached, was
published to anyone who typed the URL.

**Fix, and the part worth reading.** The docs are now gated on
`settings.serves_over_https`, **not** on `is_production`. `is_production` is
`environment == "production"`, and the deployment actually serving readypick.ai
sets `ENVIRONMENT=pilot`. Gating on `is_production` would have left the docs wide
open on the live site while reading, in the diff, as though it had closed them.
This repository has already been bitten by exactly that: `serves_over_https`
exists because the auth cookie's `Secure` flag was tied to `is_production`, so
pilot and staging issued cookies without it over genuine HTTPS origins.

**`/openapi.json` deliberately stays open.** `scripts/smoke-test.sh` probes it
unauthenticated after every deploy and fails the deploy if it is not 200, because
the registered route list is how this project verifies that the image it shipped
serves the contract it claims. Closing it would break deployment verification
silently. The schema names routes and every one of them still authorizes; `/docs`
additionally hands over a client for them. If the owner wants the schema closed,
`smoke-test.sh` must change in the same commit.

**Verification.** VERIFIED empirically. With `ENVIRONMENT=pilot` and
`FRONTEND_URL=https://readypick.ai` the app reports `is_production: False`,
`serves_over_https: True`, `docs_url: None`, `redoc_url: None`,
`openapi_url: /openapi.json`. Locally, with an http origin, the docs remain on.

### SEC-7. The staging and production deploy lanes were latently destructive

**Component.** `.github/workflows/deploy.yml`, `infra/environments/*`.

**Root cause, two independent faults.** (a) `staging/` and `production/` had no
`backend.tf`, so `terraform init` would have run against empty LOCAL state on an
ephemeral runner: Terraform would try to create everything from scratch, collide
on globally unique S3 and ECR names, and discard the state at job end,
unrecoverable without manual import. (b) `apply-staging`, `plan-production` and
`apply-production` interpolated `needs.build-and-push.outputs.image_tag` without
declaring `build-and-push` in their `needs`. GitHub's `needs` context exposes
DIRECT dependencies only, so all of them resolved to the empty string and the
apply would have run `-var="image_tag="`.

**Fix.** Remote state added to both environments with distinct keys, plus a test
asserting every environment directory with a `main.tf` has a `backend.tf` with a
unique key. The missing `needs` edges were added after a full cross-check of
every `needs.<job>.*` reference against that job's declared dependencies.

**Verification.** VERIFIED. `terraform fmt -check -recursive` clean,
`check-no-wildcard-iam.py` clean, and `plan-offline.sh` succeeds for all three
environments. A latent flake in the new test was then found and fixed: it
discovered environments with `pathlib.glob("*/main.tf")`, which matches
dot-prefixed directories, so it failed for anyone who had run `plan-offline.sh`
and left its `.{env}-offline-plan` scratch copies behind. Proven by recreating a
scratch directory and re-running: 53 passed with it present.

---

## MEDIUM, all fixed unless noted

| # | Finding | Fix | Verification |
|---|---|---|---|
| SEC-8 | Rate limiting was IP-keyed only; the per-user branch read `request.state.rate_limit_subject`, which **nothing ever set**, so it was dead code and one account could escape any limit by changing networks. | The subject is now resolved in `client_identifier` from the token and **verified** with the signing key. It cannot be set from the auth dependency because `rate_limit` is a route-level dependency and FastAPI solves those before the signature's, so it would arrive one dependency too late, silently. | VERIFIED. The old test set the attribute by hand, so it passed while the feature was dead. Rewritten to build real signed tokens: 17 tests now, including that a TAMPERED token falls back to the address bucket, which is what stops an attacker naming a stranger's bucket. |
| SEC-9 | The assessment conversation endpoints invoked a model per turn with **no rate limit**, the most expensive unthrottled surface in the product. A stolen candidate session could drive unbounded model spend and exhaust the shared per-credential provider limit, starving other tenants. | `assessment_start` 10/min, `assessment_turn` 40/min, both far above a real assessment. | Wired and imported; covered by the shared `rate_limit` tests. Not load tested. |
| SEC-10 | `POST /auth/refresh` was the one auth route the earlier sweep missed. | `auth_refresh` 30/min, deliberately generous so multiple tabs waking together do not sign a real person out. | Syntax and wiring verified. |
| SEC-11 | The agent tool-result cache was **not tenant scoped** and is read before the RLS session, so tenant B could be served tenant A's result. | Tenant in the digest and in the key; a missing tenant raises, an unscoped call is not cached at all. | VERIFIED; the repo's own `KNOWN_TENANT_GAPS` ledger is now empty and the AST sweep enforces it. See `PERFORMANCE_REPORT.md` P1. |
| SEC-12 | Firebase token verification had **no timeout**; both its network calls defaulted to 120 seconds. | `initialize_app(..., options={"httpTimeout": 10})`, verified against installed firebase-admin 7.4.0. | Verified against the library's own config keys. |
| SEC-13 | A second untimed Redis client in the OTP limiter, whose "fail open to memory" design a hang defeats. | Explicit 1s socket timeouts. | Covered by the SEC-2 sweep test. |
| SEC-14 | Two log lines passed a storage-signing exception OBJECT, whose string can carry the bucket, object key and credential query fragments. | Log the exception class name, matching every neighbouring line. | Changed; control flow untouched. |
| SEC-15 | Pilot RDS had **no deletion protection and no final snapshot** (both were keyed on `environment == "production"`), and pilot is the only environment ever applied. Confirmed live: `DeletionProtection: False`. | Both are now independent variables defaulting to the safe value. | Confirmed in the offline plan. |
| SEC-16 | `secrets/api-keys.txt` holds live-looking Groq, OpenRouter, Gemini, Cloudinary, Tavily, Resend and MSG91 keys. **NOT a repository leak**: the directory is correctly gitignored and nothing is tracked. | **OPERATIONAL, owner action.** Four of those vendors were removed from the architecture in 2026-08-28 and their keys are still live. | Confirmed gitignored via `git status --ignored`. Rotation is the owner's to do. |

---

## LOW, documented and not changed

- **SEC-17.** `backend/app/api/reports.py` branches on `user.role` against a
  per-report role set after passing a real capability check. The role list is
  central data rather than a literal in the route, so this is mild, but it is a
  second authorization mechanism outside `rbac.has_capability`.
- **SEC-18.** The `detail=str(exc)` pattern appears in many routers. Every site
  sampled raises a domain exception with a deliberately client-safe message, and
  no raw driver or ORM exception was found reaching a client. It is fragile
  rather than broken: one future edit letting a lower-level exception into one of
  those blocks would echo it verbatim with no other change needed.
- **SEC-19.** `app/(public)/site-header.tsx` publishes a personal Gmail address
  as the company contact on the public marketing site. Pre-existing, not a
  regression, and not changed because a contact address is the owner's decision.

---

## Verified as sound, and deliberately left alone

- **Authorization chokepoints.** Every internal, org, super-admin and candidate
  path funnels through `get_current_user` / `get_current_candidate` /
  `get_superadmin_db` / `require_capability`, re-resolved per request against
  `role_permissions` plus the per-user overlay, with no caching from login. No
  business-data mutation was found without an auth dependency.
- **Tenant isolation.** Spot checks across `conversations.py` (including the
  WebSocket), `portal.py`, `bgv.py`, `videos.py` and `candidates.py` all
  re-authorize the resource against the caller on every entry point, and
  cross-tenant reads return 404 rather than 403.
- **SQL injection.** No string-interpolated SQL takes caller input. The three
  f-string `text()` sites interpolate regex-validated identifiers or server-side
  enum values only.
- **XSS.** Zero `dangerouslySetInnerHTML` in the frontend before this pass. The
  two JSON-LD blocks added in this pass are the only uses, built from server data
  with `JSON.stringify` and with `<` escaped.
- **File upload.** Resume upload enforces an extension allowlist, a MIME
  allowlist, a size cap and magic-byte validation. Project archives are inspected
  for traversal, symlinks and compression ratio before extraction.
- **Secrets in CI and Terraform.** OIDC only, no static AWS keys; secrets are
  Secrets Manager injections on the execution role; no secret value is literal in
  Terraform. `git ls-files` shows only `.env.example` files, both with empty
  placeholders.
- **`NEXT_PUBLIC_` variables.** None carries a server-side secret. The Firebase
  web config is publishable by design and the Razorpay Key Secret is server-side
  only, with the public Key ID fetched at runtime.

---

## What could NOT be verified

1. **No penetration test, and no exploit was attempted** against any running
   system.
2. **The CSP was not exercised against the Razorpay checkout or the Google
   sign-in popup.** Both origins are allowlisted from vendor documentation. This
   is the highest-risk unverified item in this report, because a wrong directive
   there breaks payment or sign-in in production rather than failing a test.
3. **Live AWS state was only read, never changed** during the audit. Confirmed by
   describe calls: the only cluster is `readypick-pilot` in ap-south-2, the only
   RDS instance is `readypick-pilot` with `DeletionProtection: False` and 7-day
   backups, and both `readypick.ai` and `www` alias the pilot ALB.
   **`infra/environments/production` has never been applied.**
4. **Whether any secret still holds `PLACEHOLDER_NOT_CONFIGURED`** was not
   checked. This repository has been burned by exactly that once already, with
   `FIREBASE_SERVICE_ACCOUNT_JSON` on 2026-09-06.
5. **Rate limits were not load tested.** The numbers are reasoned from a real
   assessment's shape, not measured against traffic.
