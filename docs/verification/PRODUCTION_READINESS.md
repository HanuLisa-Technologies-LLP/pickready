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
   `serves_over_https` instead. ~~**Audit every remaining `is_production` branch
   against this fact.**~~ **DONE, 2026-09-18, and it found three more.** All
   five consumers are enumerated in **SEC-24** of `SECURITY_REPORT.md`. One was
   live and is now fixed (the request-diagnostics middleware, SEC-23); two are
   load-bearing guards that are inert on the live site and masked ONLY by
   correct configuration rather than by the guard, so the product is one
   environment-variable edit away from serving retrieval over pseudo-random
   vectors or accepting every background task and running none. Settling those
   two is an owner decision, because the obvious move, setting
   `ENVIRONMENT=production`, flips all five at once and two of them change
   behaviour on the next request rather than at the next mistake.

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

### Post-deploy verification against the live account

Run after the final rollout. Everything here is measured, not reasoned.

| Check | Result |
|---|---|
| `POST /verification/inbound-email` with NO relay header | **403** |
| the same with a WRONG header | **403** |
| `INBOUND_WEBHOOK_SECRET` on the `api` container | mounted (revision 32) |
| Secrets still on `PLACEHOLDER_NOT_CONFIGURED` | **THREE**, not the two this row first claimed: `SMTP_PASSWORD`, `MSG91_API_KEY` and `RAZORPAY_WEBHOOK_SECRET`. Only the first two are benign, see below |
| CloudWatch alarms on the pilot | **16**, up from 5 |
| `alarm_emails` SNS subscription | **PendingConfirmation** |
| CSP against the live sign-in page | no violation; the Firebase popup to `pick-ready.firebaseapp.com` was attempted |

**SEC-5 is now closed, not merely shipped.** The webhook refuses an
unauthenticated caller in production. That is safe because no inbound-mail
Lambda exists in this region (`has_inbound` is false for ap-south-2), confirmed
by listing the account's functions: `jd-gen`, `assessment-trigger`,
`company-profile`, `task-worker`, and nothing else. So the route had no
legitimate caller to break.

**There are THREE placeholder secrets, not two, and the third one disables a
control.** This paragraph read "the two placeholder secrets are both harmless"
and was wrong, for a reason worth keeping: the scan behind it enumerated only 9
of the 16 secrets. `aws secretsmanager list-secrets` PAGINATES, and a truncated
listing is indistinguishable from a complete one because both are a list of
secrets with no error on it. Re-run over all 16, reading each value and
comparing it to the sentinel:

| Secret | State | Consequence |
|---|---|---|
| `SMTP_PASSWORD` | placeholder | Harmless. `EMAIL_TRANSPORT` is `ses` here, so it is mounted on no task or function |
| `MSG91_API_KEY` | placeholder | Harmless. Mounted only on `task-worker`; `app.core.config` maps the sentinel back to `""`, so the retained SMS feature is simply off |
| `RAZORPAY_WEBHOOK_SECRET` | placeholder | **NOT harmless. Every Razorpay webhook is refused with 503 until a real value is set** |

The third one is the deliberate consequence of the fix in this release and not a
regression. The handler used to treat an absent secret as "development" and
PROCESS the unsigned event, so an anonymous POST could grant credits on the live
site; it now refuses outright. **Refusing every webhook is the correct failure
direction and it is still a failure**: a real subscription payment will not
credit the customer's account until the owner sets the value. It is listed in
the owner actions below.

None of the three is the `FIREBASE_SERVICE_ACCOUNT_JSON` class of problem: that
one IS configured.

**On the CSP and Google sign-in, be exact about what was proven.** The live login
page raises no CSP violation, and clicking the button made the SDK construct and
attempt its popup to `pick-ready.firebaseapp.com`, which matches the
`https://*.firebaseapp.com` entry in `connect-src` and `frame-src` exactly. Two
caveats. `signInWithPopup` uses `window.open`, which CSP does not govern at all,
so the popup opening proves nothing about the policy by itself. And the leg that
the policy DOES govern, the token exchange after the popup returns, needs a real
Google account and was not exercised. The risk is narrowed, not eliminated.

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

## A LIVE DEFECT, found on 2026-09-18 by following an alarm nobody receives

**`pickready.refresh_dashboard_views` has failed on every run since the
database credential was split on 2026-09-11.** The dashboard's materialised
view has not refreshed once in that week, so every surface reading it has been
serving a snapshot frozen at the moment of the rotation.

```
asyncpg.exceptions.InsufficientPrivilegeError:
    must be owner of materialized view dashboard_job_metrics
```

**How it was found is the point.** `readypick-pilot-task-worker-error-rate` is
in ALARM and has been, and the SNS subscription for every alarm in this account
is still `PendingConfirmation`, so the alarm fired into nothing. Nothing else
surfaced it: the task is scheduled every five minutes, it retries three times,
it re-raises correctly, and all of that lands in CloudWatch where nobody was
looking. It was found by listing the alarms, not by any part of the product
reporting a problem.

**Root cause, and it is a correct change interacting badly with another correct
change.** The 2026-09-11 work moved the application off the rotating RDS master
onto `pickready_app`, a least-privileged NOINHERIT role, and that fix was right
and stands. `REFRESH MATERIALIZED VIEW` requires OWNERSHIP, which
`pickready_app` deliberately does not have, and `tasks.py` issues it directly.
Nothing in the credential work was wrong; this one statement needed a companion
change nobody looked for, because the failure it produces is a background task
in a log rather than a request anybody makes.

**Two fixes, and the easy one is the wrong one.**

- `SET ROLE readypick_owner` before the refresh WOULD work with no migration:
  `provision_app_db_role` makes `pickready_app` a member of the owner
  precisely so `alembic/env.py` can escalate for DDL, and NOINHERIT only means
  it must ask. It is rejected anyway, because it hands a scheduled background
  task the FULL rights of the object owner for that transaction to buy one
  statement, and because `POSTGRES_MIGRATION_ROLE` is deliberately set on the
  migrate container and on nothing that serves a request, with
  `test_app_db_credential.py` sweeping every environment's Terraform to keep it
  that way.
- **The right fix is a `SECURITY DEFINER` function owned by
  `readypick_owner`** that performs the `REFRESH MATERIALIZED VIEW
  CONCURRENTLY`, with `EXECUTE` granted to `pickready_app` and nothing else.
  That is the enumerated minimal grant this repository already argues for
  everywhere it talks about IAM: one capability, named, rather than a role
  change that carries everything. It needs a migration, and it needs its
  `search_path` pinned in the function definition, because a `SECURITY
  DEFINER` function with a mutable `search_path` is a privilege-escalation
  primitive rather than a fix.

**FIXED, in migration 0099**, after the design was proven on a scratch
database rather than argued. `refresh_dashboard_job_metrics()` is SECURITY
DEFINER, owned by the object owner, `search_path` pinned in the definition,
EXECUTE revoked from PUBLIC and granted to `pickready_app` alone. The two
`set_config` calls that bypass RLS live INSIDE the function, so a caller cannot
omit them and silently rebuild the view empty.

Verified against the real schema and the real role: `SET ROLE pickready_app`
can CALL it and is still refused the raw statement with "must be owner", and
the refresh took the view from 30 stale rows to 32, matching the base table, so
the bypass works and it did not rebuild empty. `tests/test_dashboard_refresh_privilege.py`
pins all of it and is mutation-checked in two directions: making the function
SECURITY INVOKER fails two of its tests, and granting EXECUTE back to PUBLIC
fails the third.

**LIVE AND VERIFIED, 2026-09-18.** Migration 0099 was applied to pilot as a
one-shot ECS task (exit 0, and the task's own log read back to confirm which
revision ran, because an exit code does not say that). The proof the outage is
over is a timestamp rather than an argument: eleven consecutive failures at
five-minute intervals from 19:35:10 to 20:25:10, then the first scheduled run
after the Lambda was updated, **20:30:08, succeeded in 0.3 seconds**.
`readypick-pilot-task-worker-error-rate` has gone from ALARM to OK.

**Evidence.** Alarm `readypick-pilot-task-worker-error-rate`, state ALARM,
"Threshold Crossed: 2 datapoints [100.0, 50.0] were greater than the threshold
(1.0)". The traceback above read from `/aws/lambda/readypick-task-worker`.
`schedule.py` sets `interval_minutes=5`. One materialised view exists in the
schema, `dashboard_job_metrics`, created in 0001 and redefined in 0018.

**The fix was PROVEN on a scratch database before being written down**, because
a `SECURITY DEFINER` function carries two questions that are not worth
guessing: whether `REFRESH MATERIALIZED VIEW CONCURRENTLY` is permitted inside
a function body (which runs in a transaction), and whether the grant really is
as narrow as the argument for it claims. A throwaway database with the same
shape as production, an owner role owning the view and a NOINHERIT member app
role, answered all of it and was then removed:

| Probe | Result |
|---|---|
| App role refreshes DIRECTLY | `ERROR: must be owner of materialized view mv`, **the exact production error, reproduced** |
| App role calls the `SECURITY DEFINER` function | **Succeeds** |
| The same inside an explicit `BEGIN ... COMMIT` | **Succeeds**, so the transaction a session already holds is not an obstacle |
| App role tries to remove the materialised view | refused, must be owner |
| App role tries `ALTER TABLE ... ADD COLUMN` | refused, must be owner |
| App role tries to remove the source table | refused, must be owner |
| App role tries `ALTER ... OWNER TO` itself | refused, must be owner |
| App role runs `SET ROLE <owner>` | **Succeeds** |

The last two rows together are the whole argument for choosing the harder fix.
`SET ROLE` works, so the one-line version is genuinely available; and it would
hand a scheduled background task every one of the four operations the rows
above it refuse. The function grants the refresh and nothing else.

`REFRESH ... CONCURRENTLY` also needs a unique index, and
`ux_dashboard_job_metrics_job` already exists (0001, recreated in 0018), so
ownership is the only blocker.

---

## What is owed before this is genuinely finished

| # | Item | Why it matters |
|---|---|---|
| 1 | ~~Fix `test_placeholder_secret.py`.~~ **DONE** before deploy. | Was the deploy blocker. |
| 2 | **Complete one real Google sign-in and one Razorpay checkout.** PARTIALLY verified: no CSP violation on the live login page and the auth domain matches the allowlist, but the post-popup token exchange and the payment flow need a real account and a real payment. | Still the highest-risk unverified item: a wrong directive breaks sign-in or payment in production rather than failing a test. |
| 3 | ~~Confirm the relay secret reached both consumers.~~ **DONE for the API**, verified by a live 403. The relay half activates automatically whenever an inbound-mail Lambda is created; none exists in ap-south-2 today. | The route is closed rather than open, which is the safe direction given it has no legitimate caller. |
| 4 | ~~Check no secret still holds `PLACEHOLDER_NOT_CONFIGURED`.~~ **DONE, and the first answer was wrong.** THREE do, not two: the listing behind the first pass was paginated at 9 of 16. See the corrected table above. | A secret container is not a configured secret, and a truncated listing looks exactly like a complete one. |
| 4b | **Set a real `RAZORPAY_WEBHOOK_SECRET`** (`readypick-pilot/RAZORPAY_WEBHOOK_SECRET`, still the sentinel, created 2026-09-05 and never given a value). Copy it from the Razorpay dashboard's webhook configuration, then roll the `api` service so the new version is mounted. | **Every webhook is refused with 503 until this is set**, so a real subscription payment will not credit the customer. This is the deliberate safe direction of the fix that closed the unsigned-webhook hole, not a regression, and it is the highest-priority owner action in this table. |
| 5 | **Confirm the `alarm_emails` SNS subscription.** Measured: it is `PendingConfirmation`. Click the link AWS emailed to manjuchro@gmail.com. | Until then all 16 alarms are decorative: they will fire and notify nobody. |
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
