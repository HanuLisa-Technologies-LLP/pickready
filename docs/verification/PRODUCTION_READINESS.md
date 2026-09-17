# PRODUCTION_READINESS.md

**Date.** 2026-09-17. **Subject.** The hardening and interface pass of this date.
**Read the two tables first.** Everything after them is evidence.

---

## The single most important fact about this deployment

**"Production" is the pilot environment.** Verified against AWS, read only:

| Question | Answer |
|---|---|
| Where does `readypick.ai` point? | `readypick-pilot-893797846.ap-south-2.elb.amazonaws.com`. So does `www`. |
| What ECS clusters exist in the account? | Exactly one, `readypick-pilot`, in **ap-south-2**. |
| What RDS instances exist? | Exactly one, `readypick-pilot`. |
| Has `infra/environments/production` ever been applied? | **No.** |

Two consequences that must not be lost:

1. **Deploying "to production" means deploying to pilot.** Standing the
   `production` environment up would be a first-ever apply of an entire stack and
   would also move the domain. That is a separate decision, not a deploy.
2. **`ENVIRONMENT=pilot`, so `Settings.is_production` is FALSE on the live
   site.** Any hardening gated on `is_production` is therefore OFF in production.
   This already bit this repository once (the auth cookie's `Secure` flag), and
   it is why the API documentation fix in this pass is gated on
   `serves_over_https` instead. **Audit every remaining `is_production` branch
   against this fact.**

A second trap, recorded because it wasted time: an `aws` call without
`--region ap-south-2` returns empty and reads as "nothing is deployed".

---

## Go / no-go

| Gate | Status | Evidence |
|---|---|---|
| Frontend typecheck | **PASS** | `tsc --noEmit`, clean |
| Frontend lint | **PASS** | `eslint .`, no findings |
| Frontend tests | **PASS** | 261/261, 36 files |
| Frontend production build | **PASS** | `next build` succeeds; `/robots.txt` and `/sitemap.xml` prerender |
| Contrast gate | **PASS** | 19/19 assertions |
| Design gate | **PASS** | impeccable: 3 findings, 3 documented exceptions, 0 to answer for |
| Dependency audit | **PASS** | `npm audit` and `npm audit --omit=dev`: **0 vulnerabilities** |
| Backend suite | **PASS** | 6437 passed, 1 skipped. The single failure was a Terraform sweep invalidated by the relay-secret split; the test was rewritten to assert the real invariant and 76 now pass. |
| Terraform format | **PASS** | `fmt -check -recursive` clean |
| IAM wildcard check | **PASS** | `check-no-wildcard-iam.py` clean |
| Offline plan | **PASS** | pilot, staging and production all plan |
| Backend ARM64 image | **PASS** | builds clean for `linux/arm64` |

### DEPLOYED, and verified by digest

The blocker below was cleared (the test now asserts the invariant across both
version resources: 76 passed) and pilot was deployed on 2026-09-17.

| Step | Result |
|---|---|
| Images | `backend:sha-3143b43` `sha256:48108cba...`, `frontend:sha-4fc346c` `sha256:a1847b55...`, plus the `-fn` Lambda sibling `sha256:42a08c7e...` |
| `terraform apply` | 18 added, 15 changed in place, 4 task-definition revisions replaced. **RDS updated IN PLACE, never replaced.** |
| Migration | `alembic upgrade head` as an ECS task, polled to STOPPED, `exit=0` |
| Service rollout | api 31 to 32, frontend 18 to 19 to 20, analysis untouched at 13 |
| Lambda code | all 3 image-backed functions on `sha-3143b43-fn` |
| Digest verification | **"Every running task is the image this build produced."** |
| Smoke test | **All passed**, including authenticated endpoints, the capabilities array and the route contract |

Live checks against `https://readypick.ai` after deploy:

| Check | Result |
|---|---|
| Security headers | CSP, `X-Content-Type-Options`, `X-Frame-Options` present on the response |
| `/docs` | **404.** The interactive API docs are closed on the live site. |
| `/openapi.json` | 200, serving the real schema, as designed for the smoke test |
| `/api/v1/auth/me` unauthenticated | 401, correct |
| `robots.txt`, `sitemap.xml`, `opengraph-image` | 200 after the fix below |
| `/` | still `Site Under Construction`, as the owner requires |
| `/about` | no robots meta, indexable |

### THREE defects found ONLY by probing production

None of these could have been caught locally. In all three the build output was
correct, every unit test passed, and each file was correct in isolation: the
defect existed in the RELATIONSHIP between a file and something else, and only a
request to the deployed site showed it. This is the strongest argument in this
whole document for the project's standing rule that a green pipeline proves the
tooling finished and nothing else.

**1. Five public pages, including PRIVACY and TERMS, redirected to sign-in.**
`proxy.ts` is a deny-by-default allowlist and `PUBLIC_PREFIXES` was missing
`/about`, `/insights`, `/privacy`, `/terms` and `/employers`. The site footer
links to `/about` and `/insights` from every public page, so the marketing site
dead-ended at a login form, and a privacy policy that requires an account to read
is not a published privacy policy. It was about to get worse: the new
`robots.ts` invites crawlers to all five and `sitemap.ts` lists them, so a
crawler following the sitemap would have been handed a redirect to a login form
for every URL it had just been told to index. Fixed in `sha-9e7fdc3`, with
`lib/public-routes.test.ts` asserting the sitemap and the allowlist agree in both
directions, verified to fail with the prefixes removed.

**2. `/docs` was routed to the API, shadowing the public documentation page.**
An ALB listener rule sent `/docs` to the API target group for FastAPI's Swagger
UI. The frontend has a public docs page that the header and footer link to from
every public page, and it had never been reachable. Closing the interactive docs
then turned that linked nav item into a 404. `/openapi.json` stays on the rule,
because the smoke test probes it.

**3. robots.txt, the sitemap and the OG image redirected to sign-in.**

`robots.txt`, `sitemap.xml` and `opengraph-image` returned **307 to the sign-in
page**. They are GENERATED routes rather than files under `public/`, so they fell
inside `proxy.ts`'s matcher, and that middleware is deny-by-default. Every
crawler got a redirect instead of the file, which defeated the entire indexing
pass: the disallow rules protecting `/org`, `/portal`, `/admin` and the tokenised
links were never delivered to anybody.

They are GENERATED routes rather than files under `public/`, so they fell inside
the same matcher. Fixed in `sha-4fc346c`, redeployed, re-verified at 200.

**Final state, all verified live:** thirteen public routes answer 200 (`/`,
`/about`, `/insights`, `/docs`, `/privacy`, `/terms`, `/employers`,
`/robots.txt`, `/sitemap.xml`, `/opengraph-image`, `/login`, `/register`,
`/join`); `/org`, `/admin`, `/bd` and `/portal` answer 307; `/docs` serves the
product page rather than Swagger; and the full smoke test passes.

### The backend failure that was cleared before deploying

`tests/test_placeholder_secret.py::test_the_terraform_seeds_a_version_and_never_overwrites_it`.

It asserts that `aws_secretsmanager_secret_version.placeholder` carries
`for_each = aws_secretsmanager_secret.this`, so that **every** secret gets an
initial version. The reason the invariant exists is in the test's own message: a
secret with no version cannot be injected into a task, so the ones it misses
"stop their whole task from starting".

The relay-secret work in this pass splits that resource, because a
Terraform-GENERATED secret must receive its real value rather than a placeholder.
That is a correct design change and it invalidates the assertion as written.
Deploying was held until the test was rewritten to assert both halves, because
the property it protects, that no secret is left version-less, is exactly what a
bad split would break. It now asserts the invariant across both version
resources and passes.

**Everything else in the suite passed.** The single skip is the documented
legitimate one (`VOYAGE_CONTEXT_4` unset).

### A trap worth recording

The suite was run as `pytest ... | tee ... | tail`, and the harness reported
**exit code 0** while pytest had reported a failure. The exit code belonged to
`tail`. This is the same lesson this repository already learned about green
pipelines: read the result, never the pipeline.

---

## What changed in this pass

**Security.** 19 findings, 2 CRITICAL. See `SECURITY_REPORT.md`. The headline
items: a Next.js unauthenticated-RCE CVE (fixed, audit now clean); an untimed
Redis client on the RBAC hot path that would have hung every authorized request
during a network partition; a stack that shipped exactly one security header;
four authenticated portals and three unauthenticated tokenised routes with
nothing preventing indexing; an unauthenticated webhook that writes into BGV
records; and the interactive API docs served on the live site.

**Deployment safety.** `staging` and `production` had **no Terraform remote
state**, so an apply would have run against empty local state, tried to create
everything, collided on globally unique names and discarded the state. Three jobs
also interpolated an `image_tag` from a job they did not declare in `needs`,
which resolves to the empty string, so the apply would have run
`-var="image_tag="`. Both fixed, with a test for the first.

**Observability.** Alarms existed for five conditions; API 5xx and RDS
connections were dashboard widgets only, and ElastiCache had no alarm at all
despite `noeviction` turning memory exhaustion into write failures on the live
proctoring counter. Alarms added, plus per-environment AWS Budgets, plus the
CloudWatch and Budgets grants on the SNS topic's KMS key, without which every
alarm would have fired and no notification would have arrived. Structured JSON
logging and a request correlation id now exist, and the workers emit the same
format as the API rather than plain text.

**Interface.** The load-bearing items: the product was shipping default Inter,
which its own `DESIGN.md` names as the tell to avoid; there were **zero** route
error boundaries in the entire app, so any render throw blanked the page; every
loading spinner froze under `prefers-reduced-motion`, making a pending request
look hung; and the dashboard was six equal-weight metric tiles restating the
table beneath them.

---

## What is owed before this is genuinely finished

| # | Item | Why it matters |
|---|---|---|
| 1 | ~~Fix `test_placeholder_secret.py`.~~ **DONE** before deploy. | Was the deploy blocker. |
| 2 | **Exercise Google sign-in and Razorpay checkout immediately after deploy.** | The CSP is new. Both flows need a live session or a live payment and could not be tested. A wrong directive breaks sign-in or payment in production rather than failing a test. This is the highest-risk unverified item in the whole pass. |
| 3 | **Confirm the relay secret reached BOTH consumers.** | Until the API and the inbound Lambda hold the same value, `POST /verification/inbound-email` stays open and logs `verification.inbound_unauthenticated` on every call. |
| 4 | **Check no secret still holds `PLACEHOLDER_NOT_CONFIGURED`.** | A secret container is not a configured secret. This repository has been burned by exactly that (`FIREBASE_SERVICE_ACCOUNT_JSON`, 2026-09-06). |
| 5 | **Confirm the `alarm_emails` SNS subscription.** | Terraform reports a pending subscription as created. Unconfirmed means nobody is notified, which makes every alarm added in this pass decorative. |
| 6 | **Set a real `monthly_budget_usd`.** | It defaults to a conservative placeholder with a description saying the owner must set it. |
| 7 | **Rotate the keys in `secrets/api-keys.txt`.** | Live third-party keys for four vendors the architecture removed in 2026-08-28. Correctly gitignored and never committed; this is local-machine hygiene, not a leak. |
| 8 | **`frontend/lib/api.ts` has no client-side fetch timeout.** Deliberately NOT changed: a blanket `AbortController` would also cut a large resume or project upload, and the backend's own interactive task timeouts (15 to 50 seconds) should resolve first. It is a missing backstop rather than a defect, and it needs a per-call budget rather than one global number. | A hung backend connection would hang the browser's fetch with no independent client-side ceiling. |
| 9 | **Decide on the public contact address.** | The public site publishes a personal Gmail as the company contact. Pre-existing, and an owner decision rather than a bug. |

---

## Two behaviours that are deliberate, and will look like bugs

1. **The landing page is not live, on purpose.** `/` serves the "Site Under
   Construction" page and will continue to, because `NEXT_PUBLIC_LANDING_LIVE`
   defaults to off and is deliberately **not** a Docker build argument. The
   optimised landing page is complete, composed and verified behind that flag.
   `NEXT_PUBLIC_*` variables are baked at BUILD time, so going live means adding
   the build argument and rebuilding the frontend image, not flipping a runtime
   setting.
2. **An unknown URL redirects to `/login`, not to the 404 page.** `proxy.ts` is
   deny-by-default: anything outside its public prefixes requires a session. So
   the new `not-found.tsx` is reached by signed-in users and on public paths, and
   an anonymous visitor mistyping a URL lands on sign-in. That is the existing
   security posture and was not weakened to make the 404 prettier.

---

## Disaster recovery

`docs/operations/DISASTER_RECOVERY.md` is new and is the first written restore
procedure this project has had. **It has never been rehearsed**, says so in its
first paragraph, and its RTO is a target rather than a measurement. It also
records two gaps that were found and not closed: there is **no cross-region
backup copy in any environment**, and a restored `pickready_app` password can
silently disagree with Secrets Manager if a rotation happened after the restore
point.

Pilot RDS previously had **no deletion protection and no final snapshot**,
because both were keyed on `environment == "production"` and pilot is the only
environment that exists. Confirmed live before the fix: `DeletionProtection:
False`, 7-day backups.

---

## What could NOT be verified

1. **No penetration test.** Nothing was proven by exploiting a running system.
2. **Nothing was profiled at volume**, because no environment has volume: pilot
   holds three demo tenants, thirty jobs and **zero candidates**. Every claim in
   `PERFORMANCE_REPORT.md` that depends on scale is untested at scale, anywhere.
3. **No Lighthouse or Core Web Vitals measurement** was taken, before or after.
4. **Migration 0098 has been applied only to a fresh test database**, never to
   pilot.
5. **The GitHub `production` environment's required reviewers were not read.**
   The verifier now has the permission it needs and a truthful error message, but
   the claim is unproven in both directions.
6. **`ses_inbound` is not exercised by the offline plan**, because the offline
   region is not an SES receiving region. Its module validates standalone; the
   bucket-versioning change is unproven by plan.
