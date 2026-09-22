# Deployment log

**Account** `016617990245` · **Region** `ap-south-2` (Hyderabad) ·
**Repository** `HanuLisa-Technologies-LLP/pickready` · **Environment** `pilot`

This is the catch-up artifact. It records what changed in the codebase, what was
created in AWS, every decision made under ambiguity, and what is still waiting
on a human. Read section 6 first if you only read one: it is the list of things
that need you.

---

## 2026-09-23, change requests 07 to 28D and the harness, pilot (ap-south-2)

Deployed as `sha-d63f88937e05` from commit `d63f889`. Twenty two change
requests plus `backend/harness/`, migrations 0106 to 0117, 243 files.

**Verified before the build**: 7124 backend tests passed, 1 documented skip,
2 xfailed; 329 frontend tests passed; agent evals passed; mypy clean over 52
files; no import cycles; no dead code.

**Rollback point**: api `:46`, frontend `:33`, analysis `:14`, all on
`sha-ce438cf`. Note that a code rollback does NOT roll the schema back, and it
does not need to: every migration here is additive or widening except 0106's
rewrite of `medium` to `moderate`, a value the old CHECK never admitted, so no
pre-existing row carried it.

### What ran

1. `terraform apply` for shape: 9 added, 3 changed, 4 destroyed. Five new
   EventBridge rules (`expire-credit-lots`, `purge-closed-job-assessments`,
   `sweep-subscription-usage-alerts`, `purge-assessment-media`,
   `reconcile-candidate-erasures`), all confirmed present afterwards.
2. `backend` and `frontend` built for linux/arm64 and pushed. `analysis` was
   NOT rebuilt: it is unchanged in this commit and `analysis_image_tag` stays
   pinned at `93ebfcb`, which is what that variable exists for.
3. Migration on the new image, before anything serving traffic moved.
4. Services rolled: api `:46` to `:48`, frontend `:33` to `:35`.
5. The three image-backed Lambdas pointed at the same image.
6. Verified by DIGEST of the running tasks, then smoke tested live.

### THE MIGRATION RAN ON THE OLD IMAGE AND REPORTED SUCCESS

The first `run-migration.sh` exited 0 having applied NOTHING. The plan had been
generated before `terraform.tfvars` carried the new tag, so the `migrate` task
definition still pointed at `sha-a03e978`, whose head is 0105. `alembic upgrade
head` on that image had nothing to do and said so with exit 0.

This is the standing rule with a fresh instance: a timestamp, or an exit code,
is not evidence that work happened. What caught it was reading which IMAGE the
stopped task had actually run, not the script's result. The fix was to apply
terraform with the corrected tfvars so the task definition carried
`sha-d63f88937e05`, then re-run and read the log: all twelve revisions, 0106
through 0117, named in order.

`terraform.tfvars` also carried `sha-a03e978` and `sha-7466081` while the
services were really running `sha-ce438cf`, because release 8 was deployed with
CLI `-var` overrides. The file's own comment says it must carry the tag the
environment actually runs, so it now does.

### `scripts/smoke-test.sh` HAD NEVER BEEN RUN AGAINST A LIVE SITE

It failed the deploy of a healthy build, twelve times, on
`/api/v1/health/live`, a route that has never existed.

Every path in that script is written absolute from the origin
(`/api/v1/jobs`, `/openapi.json`, `/health/live`), and it joined them onto
`$TARGET`, which the terraform output ends with `/api/v1`. So liveness asked
for `/api/v1/health/live`, the authenticated probes asked for
`/api/v1/api/v1/jobs`, and the route contract asked for
`/api/v1/openapi.json`. Five failures, none of them the product.

It survived because the CI jobs that invoke it are gated behind
`vars.PILOT_DEPLOY_ENABLED`, which has never been set. The base is the ORIGIN
now, and the reasoning is written into the script beside it. This is the same
finding `verify-deployment.sh` already states in its own words: a check nobody
has run is not a check.

`verify-deployment.sh` itself behaved exactly as designed, refusing to pass
with `analysis: SKIPPED, no expected digest supplied`. Supplying it made all
three services verify.

### Live confirmation

- `api` 2 tasks on `sha256:2bea4819...`, `frontend` 1 task on
  `sha256:6d9744dc...`, `analysis` 2 tasks on `sha256:e0c6d488...`.
- Smoke: liveness, three authenticated endpoints, the capabilities array, four
  route-contract assertions and the frontend root, all passing.
- New surfaces answered: `/drishti/sections`, `/drishti/functions`, and the
  Starter Pack present as `starter_pack_75`, 40 credits purchased at the fixed
  rate plus 35 bonus, Rs 24,000 subtotal, three month validity.

### Still open

- `AWS_DEPLOY_ENABLED` and `PILOT_DEPLOY_ENABLED` remain unset, so CI still
  does not deploy and the post-deploy scripts still only run by hand.
- The analysis service was not rebuilt, so nothing in this release exercised
  its image path.

---


## 1. What this was

Two halves of one change, on 2026-09-05.

**Celery was removed from the product.** Background work is now dispatched to
Lambda for short tasks and to one on-demand Fargate task per dispatch for long
ones. The specification is `docs/spec/BACKGROUND_WORK.md`; the standing rules
are in the new top section of `claude.md`.

**The pilot environment was built and applied in `ap-south-2`.** It is the first
environment this repository has ever actually applied. Staging and production
next door had never been run, and they were migrated to the same shape in the
same change because they still declared `celery -A app.workers.celery_app` as a
container command, which the image can no longer run.

---

## 2. Decisions made under ambiguity

Each of these was a judgment call the brief left open or got wrong about this
codebase. They are recorded here rather than only in a comment, because the
brief asked for exactly that.

### 2.1 Terraform lives in `infra/`, not a new `terraform/` tree

The brief specifies `terraform/` at the repository root. This repository already
had a complete Terraform tree under `infra/`: eleven modules, an offline
planning profile, a wildcard-IAM checker, a `validate.sh`, and CI wired to all
of it. The pilot composition reuses every one of those modules.

A second tree would be two answers to "where is the infrastructure", and CI
reads the older one. `infra/environments/pilot/` is the new composition and the
one to copy.

### 2.2 There is no `readypick-resume-jd-match`

The brief names three short request/response agents. Two of them map onto real
inline work in this product and were moved: `readypick-jd-gen`
(`services/jd_generation.generate_jd_document`) and
`readypick-company-profile` (`services/company_research.research_company`).
Both were awaited in a request handler, so moving them off the API task is a
genuine gain and the UX is unchanged.

The third does not map onto anything. This product's resume-to-JD matching is
`pickready.run_matching`: a batch over every candidate linked to a job, with
model calls per batch and a stage-by-stage progress display a recruiter watches.
The brief sizes that function at 256MB and 300 seconds, which could not finish
it, and there is no single-candidate caller in the product to give one instead.

Building it would have meant inventing a caller for it. `run_matching` runs as an
on-demand Fargate task alongside the assessment agent, which is the same
pay-only-while-running model the brief is buying.

### 2.3 Three functions are container images, one is a zip

The brief suggests zip packaging "unless a dependency forces otherwise". A
dependency forces otherwise for three of the four.

`readypick-task-worker`, `readypick-jd-gen` and `readypick-company-profile` all
import the application: the model router, the prompt registry, a database
session, `pypdf`, the OpenAI client. They run the **backend image** with a
different handler, passed as the container command through the Lambda runtime
interface client. Building a separate artifact carrying the same code would let
an agent and the API disagree about what a prompt says or what a grade means.

`readypick-assessment-trigger` is a zip of one file that imports boto3 and
nothing else, and it must stay that way. It is the only thing in this account
holding `iam:PassRole`, which is a privilege-escalation primitive: anything that
can pass a role can run code as it. The mitigation is that its whole source fits
on a screen.

### 2.4 Retries live in the task runtime; the platform retries zero times

`aws_lambda_function_event_invoke_config` sets `maximum_retry_attempts = 0`
on every function. The retry loop is inside `app/workers/runtime.run_task`.

Two mechanisms stacked would multiply: three in-process attempts under two
platform attempts is nine sends of one email, and a duplicate invitation is
worse than a failure somebody can see. What replaces the platform retry is the
**on-failure destination**: a permanently failed invocation publishes, once, to
the alarm topic. The brief's SQS DLQ was not used; the alarm topic already
exists and this needs no queue semantics.

### 2.5 The pilot auto-applies on `main`, with a declared environment

Per the brief: the pilot is not production, so `pilot-deploy` applies without a
required reviewer. The GitHub `environment: pilot` is declared anyway, so adding
a reviewer later is one repository setting and no workflow change. Production's
apply remains behind a required-reviewer environment and
`vars.AWS_DEPLOY_ENABLED`, which is unset.

### 2.6 One S3 bucket with prefixes, not four buckets

The brief lists `readypick-docs-*`, `readypick-reports-*`,
`readypick-model-cache-*` and `readypick-artifacts-*`. This product's
`services/object_storage` writes into one bucket under prefixes, and the
existing `modules/s3` grants each prefix explicitly so a grant on the whole
bucket cannot silently cover whatever the next feature puts there. That is a
stronger arrangement than four buckets and it is what the code already expects.

`readypick-model-cache-*` was **dropped**, which the brief explicitly left to
judgment. The analysis service bakes its Hugging Face weights into the image at
build time and runs with `HF_HUB_OFFLINE=1`; nothing pulls from S3, so the
bucket would have been created empty and stayed empty.

### 2.7 Staging and production were migrated too, not left behind

Both still declared a Celery `worker` and a Celery `beat` service. Those two
would have started, failed to exec `celery`, and restarted for ever. Neither
environment has ever been applied, so this is a correction rather than a
migration of live infrastructure. They keep every sizing and toggle decision
they already made.

### 2.8 The certificate is a self-signed stopgap

**Decided with the owner on 2026-09-05: there is no domain yet.**

There is deliberately no plaintext mode in the ALB module. The application sets
`Secure` cookies and uvicorn runs with `--proxy-headers`, so over plain http
every auth cookie is refused: an http-only environment is not a smaller product,
it is a product nobody can sign in to.

So a self-signed certificate was generated and **imported** into ACM, with
`subjectAltName = *.ap-south-2.elb.amazonaws.com` so the hostname matches the
load balancer's own name and the only warning a visitor sees is about the
issuer. It is wired through `var.fallback_certificate_arn` and it is named like
what it is. **Setting `domain_name` retires it** and switches the listener to an
ACM-issued certificate with DNS validation and a Route 53 alias record, all of
which is already written and gated on that one variable.

### 2.9 Region is a variable with no default, and one literal is unavoidable

`ap-south-2` is a locked decision, and a locked decision is still a decision: it
is passed in `terraform.tfvars` rather than defaulted, matching staging and
production and the repository's own rule that no region literal appears in
executable Terraform.

The single exception is `infra/environments/pilot/backend.tf`, because Terraform
does not evaluate variables inside a backend block at all.
`test_no_account_id_region_or_domain_is_hardcoded` now carries a narrow
exemption for that one construct, and asserts the file contains nothing but a
backend block so the exemption cannot be used to smuggle a literal into a
resource.

---

## 3. Defects found and fixed on the way

These were not part of the brief. All three were latent because no environment
had ever been applied.

### 3.1 The migration task family did not exist

`scripts/run-migration.sh` runs `${cluster}-migrate` as a one-shot ECS task and
reads the network from Terraform outputs. **No environment declared a `migrate`
service, and no environment emitted `private_subnet_ids` or
`ecs_security_group_id`.** The first real deploy of any environment would have
failed at the migration step.

Fixed as an `on_demand` task definition in all three environments, plus the
three outputs. The migrate role holds one secret, the DSN, which the secrets
module's map already said and nothing consumed.

### 3.2 `project-intake/` was never granted in S3

`services/projects/intake.INTAKE_PREFIX` is `project-intake`, and
`modules/s3`'s `application_prefixes` granted only `resumes` and `compliance`.
Every candidate project upload would have been refused with AccessDenied, in a
feature that ships.

Fixed, and a lifecycle rule was added that **expires** anything left under that
prefix after seven days. It is a backstop for the case where the verified
deletion failed and the hourly reconciler never ran; it deletes rather than
archives, so it does not reintroduce the original-project store the Project
Evidence brief refuses.

### 3.3 The GitHub OIDC provider and CI role did not exist

The brief lists them under "already exists, created manually via CLI, import
rather than recreate". `aws iam get-role --role-name readypick-github-actions`
returned NoSuchEntity and `list-open-id-connect-providers` was empty. The state
bucket and the DynamoDB lock table **did** exist and were used as-is.

Both were created (section 4.1). `PowerUserAccess` alone would not have worked:
it excludes IAM, and this Terraform creates roughly thirty roles and policies.
A second, scoped policy grants role and policy management under the
`readypick-*` prefix, `iam:PassRole` to exactly the four services that run this
product, and access to the state backend. Not `IAMFullAccess`.

---

## 4. What is in AWS now

### 4.1 Created by hand, outside Terraform

| Resource | Identifier | Why not Terraform |
|---|---|---|
| State bucket | `readypick-tfstate-rp-manju-0904` | Terraform cannot create the bucket that holds its own state. Pre-existing. |
| Lock table | `readypick-tfstate-lock` | Same. Pre-existing. |
| OIDC provider | `token.actions.githubusercontent.com` | Account-level identity plumbing, shared by every environment. |
| CI role | `readypick-github-actions` | Trust is scoped to `repo:HanuLisa-Technologies-LLP/pickready:*`. |
| CI policy | `readypick-terraform-iam` | What PowerUserAccess does not grant, scoped to the `readypick-*` prefix. |
| Certificate | `arn:aws:acm:ap-south-2:016617990245:certificate/a36828f3-e71d-409c-8372-44d94743d954` | An imported self-signed stopgap. See 2.8. |

A Terraform run that could destroy its own state store is one bad plan away from
an unrecoverable environment, which is why the first two are not managed here
and must not be adopted.

### 4.2 Applied by Terraform

`infra/environments/pilot`, state at `s3://readypick-tfstate-rp-manju-0904`
under `pilot/terraform.tfstate`.

| Layer | What | Notes |
|---|---|---|
| Network | VPC `10.0.0.0/16`, 2 public + 2 private + 2 data subnets, one NAT | The **data subnets have no route to the internet in either direction**, not even outbound through NAT. An attacker does not need to reach the database from the internet; they need the database's host to reach them. |
| | VPC endpoints | S3 (gateway), ECR API, ECR DKR, Secrets Manager, CloudWatch Logs (interface) |
| | Security groups | `alb`, `ecs`, `rds`, `redis`. The analysis service has no ingress from anywhere but the ECS group and no egress to the internet. |
| Data | RDS PostgreSQL 16, `db.t4g.micro`, single-AZ, 50GB gp3 to 100GB | Encrypted, 7-day backups, AWS-managed master password |
| | ElastiCache Redis 7, `cache.t4g.micro`, one node | TLS in transit, AUTH token, `noeviction` |
| Storage | `readypick-pilot-storage-016617990245` | Versioned, SSE, public access blocked. Prefixes `resumes/`, `compliance/`, `project-intake/` |
| | `readypick-pilot-alb-logs-016617990245` | The load balancer's access logs |
| Registry | `readypick-pilot/{backend,frontend,analysis}` | **Immutable tags**, retained by count (20), never by age: an age rule deletes the image a long-running service needs to restart. |
| Secrets | 14 containers, one scoped IAM policy per consumer | See section 6 for which still need a value |
| Compute | ECS cluster `readypick-pilot` | |
| | 3 services: `api`, `frontend`, `analysis` | Rolling deploys with `deployment_circuit_breaker { rollback = true }` |
| | 2 on-demand task definitions: `agent`, `migrate` | **No service.** Started by RunTask; they run once and exit. |
| Functions | `readypick-task-worker` (image, 1024MB/600s, concurrency 20) | Every short task |
| | `readypick-jd-gen` (image, 512MB/600s) | Synchronous |
| | `readypick-company-profile` (image, 512MB/300s) | Synchronous |
| | `readypick-assessment-trigger` (zip, 128MB/30s) | Holds `iam:PassRole`; reads no secret |
| Schedules | 7 EventBridge Scheduler rules | Mirroring `backend/app/workers/schedule.py` |
| Traffic | ALB, HTTPS listener, HTTP→HTTPS redirect, two target groups | WAF module built and **disabled**: `enabled = false` creates nothing, rather than a permissive web ACL that costs money and proves nothing |
| Observability | SNS topic, 8 alarms, one dashboard | One error-rate alarm per function, never one aggregate |

### Where it answers

| | |
|---|---|
| Frontend | `https://readypick-pilot-893797846.ap-south-2.elb.amazonaws.com` |
| API | `.../api/v1` |
| API documentation | `.../docs` |

Every visitor gets one certificate warning, about the issuer, until a domain
exists (2.8). The hostname itself matches, because the imported certificate's
subject alternative name is `*.ap-south-2.elb.amazonaws.com`.

### Verified, not assumed

| Check | Result |
|---|---|
| `GET /` and `/login` | 200, serving the real landing page |
| `GET /docs`, `/openapi.json` | 200 |
| ALB target health | api 2/2 healthy, frontend 2/2 healthy |
| `alembic upgrade head` | one-shot ECS task, exit 0 |
| `readypick-task-worker` | invoked with `pickready.refresh_dashboard_views`: `taskrun.succeeded ... elapsed=0.2s` |
| `readypick-task-worker` on a sweep payload | invoked exactly as EventBridge sends it; returned the reconciler's real result object |
| `readypick-assessment-trigger` | invoked; returned 202 and a task ARN |
| the on-demand agent | that task ran `pickready.reconcile_job_setup`, logged `taskrun.succeeded`, and **exited 0** |
| 7 schedules | ENABLED |
| 8 alarms | OK |
| Backend suite | 5324 passed, 1 skipped, 0 failed |
| `infra/plan-offline.sh` | all three environments plan |
| `infra/check-no-wildcard-iam.py` | no wildcard IAM grant |

### 4.3 What the first real apply found

Everything in this section was invisible until something ran. The offline plan
is explicit that it proves internal consistency and nothing about an account,
and this is what that sentence means in practice.

**AWS and Terraform constraints, now enforced at plan time rather than
discovered:**

1. **`stopTimeout` cannot exceed 120 seconds on Fargate.** The brief asked for
   3600. It is a SIGTERM-to-SIGKILL grace period, **not a runtime ceiling** --
   an on-demand task runs until its process exits and ECS imposes no limit on
   that. The comments that called it a ceiling were corrected with the number,
   and `modules/ecs` now validates the range.
2. **`AWS_REGION` is a reserved Lambda environment key.** The runtime injects
   it; `CreateFunction` answers 400 for any request that also supplies it.
   Removed, and nothing is lost: `Settings.aws_region` reads that same variable.
3. **Lambda defaults to x86_64 and the ECS module defaults to ARM64.** The same
   backend image serves both, so the mismatch would have produced functions
   created without complaint that fail at cold start with an exec format error.
   `modules/lambda` now states `architectures` explicitly.
4. **This account's Lambda concurrency limit is 10**, the new-account default
   rather than the usual 1000, and AWS refuses any reservation that would leave
   fewer than 10 unreserved. The per-function ceilings are written down and
   switched off behind `var.reserve_lambda_concurrency`, with the quota-increase
   command in its description. A request for 1000 has been submitted and is
   pending. Nothing is unprotected meanwhile: an account cap of 10 is a harder
   ceiling than the 20 that was being asked for.
5. **Lambda will not accept an OCI image INDEX**, only a single-platform
   manifest. `docker buildx` produces an index whenever provenance or SBOM
   attestations are on, and `docker buildx imagetools create` always does. The
   error names the media type and not the cause. The backend image is pushed
   with `--provenance=false --sbom=false`.
6. **HCL evaluates both sides of `||`**, so `x == null || x >= 2` still compares
   a null and fails the whole plan. Validations use `coalesce` instead.
7. **S3 lifecycle filters have no negation** -- `Prefix`, `Tag` and `And`, no
   `Not` -- so the transition rule enumerates the prefixes it applies to.
8. **CloudWatch dashboard metrics are arrays of arrays, and `flatten` is
   recursive**, so it collapsed each metric into loose strings and PutDashboard
   answered with eighty validation errors. `concat`, not `flatten`.

**Defects in this codebase, all of them latent because nothing had ever been
deployed:**

9. **The image's entry point read the Lambda handler as a role name.**
   `docker-entrypoint.sh` takes its first argument as `api|agent|lambda|migrate`,
   and Lambda sets only the container COMMAND. The handler landed in the role
   position and the container died with `exec: ...lambda_handler: not found`,
   exit 127, before the runtime interface client started. The function's
   `image_config` now sets `entry_point` as well.
10. **Nothing fetched the Lambdas' secrets.** ECS injects them; **Lambda has no
    equivalent**, and its own environment variables are the only mechanism it
    offers, which is where a credential must never go. The functions held the
    right IAM policy and used it for nothing, so `Settings` fell back to its
    defaults and the first invocation failed against `127.0.0.1:5432` inside a
    VPC. `app/workers/secrets_bootstrap.py` fetches them at cold start from a
    `{ENV_NAME: ARN}` map Terraform builds from the same list the ECS services
    use.
11. **A percent-encoded database password crashed Alembic before any migration
    ran.** `env.py` hands the DSN to a ConfigParser, whose interpolation reads
    `%` as the start of a reference, and RDS generates passwords from a
    character set that percent-encodes. This is the normal case, not an unlucky
    one. Fixed by doubling the `%`, and pinned by
    `tests/test_alembic_dsn_escaping.py`, which demonstrates the crash rather
    than describing it.
12. **The frontend listened on 8080 while everything else said 3000.** Its
    Dockerfile defaults `PORT=8080`, a Cloud Run convention; nothing on ECS
    injects PORT. The task ran, the log said `Ready`, and the load balancer
    reported "Health checks failed" with no error anywhere, because nothing was
    listening where anybody looked. `modules/ecs` now derives `PORT` from the
    declared container port, so the two cannot disagree.
13. **Nothing ever rolled the ECS services.** `terraform apply` registers a new
    task definition and correctly does not touch the service, which has
    `ignore_changes = [task_definition]`. No pipeline step pointed the service
    at the new revision, so a service would have run its first revision for
    ever while every deploy reported success. `scripts/deploy-services.sh` is
    the missing step.
14. **A secret with no version stops the whole task**, not one feature. ECS
    fetches every secret in a task definition before the container starts, so
    one unpopulated credential took the API down with a message about
    `AWSCURRENT`. Every secret is now created with an explicit
    `PLACEHOLDER_NOT_CONFIGURED` version that `app.core.config` maps back to ""
    before anything reads it, so an absent credential takes the documented
    degraded path instead. See `tests/test_placeholder_secret.py`.
15. **`ignore_changes` does not protect the FIRST create.** The placeholder
    versions overwrote four secrets that had already been populated by hand.
    The ordering is now in the runbook: apply first, populate after.
16. **The deployed services believed they were in `development`.** Every
    environment set `APP_ENV`, a Cloud Run leftover; `Settings.environment`
    reads `ENVIRONMENT`. The pilot's own first agent log said `env=development`.
17. **The auth cookie's `Secure` flag was tied to `is_production`**, so a cookie
    issued by the pilot or by staging would have gone out without it over a real
    HTTPS origin. `Secure` is a property of the ORIGIN, not of an environment
    name, so it is now derived from `frontend_url`.
18. **The frontend image build had no Firebase web config in CI.** Those values
    are inlined into the browser bundle by the compiler, so the build FAILS at
    prerender with `auth/invalid-api-key`, which reads like a bad credential and
    is an absent one. Added to both the pilot and staging build steps.
19. **`project-intake/` was never granted in S3** and **no environment declared
    a `migrate` task family**, both covered in section 3.

---

## 5. How to deploy from here

```bash
# 1. Plan and apply the SHAPE. Terraform ignores the running image on both ECS
#    and Lambda, so this does not deploy code.
cd infra/environments/pilot
terraform init -input=false
terraform plan -input=false -out=tfplan
terraform apply tfplan

# 2. Build and push. A SHA tag, never `latest`: ECR tags are immutable in this
#    account, which is what makes a tag a permanent name for specific bytes.
TAG="sha-$(git rev-parse --short=12 HEAD)"
REG="016617990245.dkr.ecr.ap-south-2.amazonaws.com"
aws ecr get-login-password --region ap-south-2 | docker login --username AWS --password-stdin "$REG"
docker buildx build --platform linux/arm64 --push -t "$REG/readypick-pilot/backend:$TAG"  backend
docker buildx build --platform linux/arm64 --push -t "$REG/readypick-pilot/frontend:$TAG" frontend
docker buildx build --platform linux/arm64 --push -t "$REG/readypick-pilot/analysis:$TAG" analysis-service

# 3. MIGRATE, on the new image, BEFORE anything serving traffic is updated. The
#    script polls for STOPPED and reads the container exit code: `run-task`
#    returning is not the migration finishing.
AWS_REGION=ap-south-2 ./scripts/run-migration.sh pilot

# 4. Roll the services, then point the functions at the same image and read back
#    what they are running.
terraform apply -var="image_tag=$TAG"
AWS_REGION=ap-south-2 ./scripts/update-lambda-code.sh pilot "$REG/readypick-pilot/backend:$TAG"

# 5. Verify by DIGEST, not by an exit code.
AWS_REGION=ap-south-2 ./scripts/verify-deployment.sh pilot
```

CI does the same thing in `pilot-build-and-push` and `pilot-deploy`. It is gated
on `vars.PILOT_DEPLOY_ENABLED == 'true'` and needs
`secrets.AWS_PILOT_DEPLOY_ROLE_ARN` and `vars.AWS_REGION`; none of the three is
set yet, so pushing to `main` runs the test and gate jobs and deploys nothing.

---

## 6. What needs a human

### 6.1 Secrets that need a real value

Four were populated by the deployment and need nothing:

| Secret | How it was set |
|---|---|
| `readypick-pilot/DATABASE_URL` | Composed from the RDS endpoint and the AWS-managed master password |
| `readypick-pilot/REDIS_URL` | Composed from the primary endpoint and the generated AUTH token. `rediss://`, not `redis://`: transit encryption is on |
| `readypick-pilot/JWT_SECRET` | 64 hex characters from the OS CSPRNG |
| `readypick-pilot/LLM_KEY_ENCRYPTION_SECRET` | The same |

**Ten need you.** Nothing was fabricated for any of them: each is an external
credential this deployment has no way to obtain, and each secret exists as an
empty container so the plan is complete and the failure is a clear one.

```bash
S() { aws secretsmanager put-secret-value --region ap-south-2 \
        --secret-id "readypick-pilot/$1" --secret-string "$2" >/dev/null && echo "$1 set"; }

# THE THREE THAT BLOCK EVERY AI PATH. Without these the product runs and every
# generative feature falls back to its deterministic path: a template JD, an
# unscored assessment, keyword-only retrieval.
S OPENAI_GPT_TERRA   '<judge/write tier key>'
S OPENAI_GPT_LUNA    '<extract/classify tier key>'
S VOYAGE_CONTEXT_4   '<Voyage key, for voyage-4 embeddings>'

# SIGN-IN. Without this nobody can authenticate at all: Firebase is identity for
# every role. The whole service-account JSON, on one line.
S FIREBASE_SERVICE_ACCOUNT_JSON '<the service account JSON>'

# OUTBOUND. Gmail SMTP is the only mail path; MSG91 is the retained SMS feature.
S SMTP_PASSWORD      '<Google App Password for the sending mailbox>'
S MSG91_API_KEY      '<MSG91 key>'

# BILLING. Razorpay Subscriptions, not Orders.
S RAZORPAY_KEY_SECRET     '<key secret>'
S RAZORPAY_WEBHOOK_SECRET '<webhook secret>'

# RESEARCH. Without it AI Reach's internet segment reports `unconfigured` with a
# plain message and the page still works, which is by design.
S TAVILY_API_KEY     '<Tavily key>'

# PROCTORING. See 6.2: this one also gates an image build.
S HUGGINGFACE_TOKEN  '<HF token with the two pyannote licences accepted>'
```

After setting them, restart the services so the new values are injected. ECS
fetches secrets at task start, so a running task keeps the old value:

```bash
for s in api frontend analysis; do
  aws ecs update-service --region ap-south-2 --cluster readypick-pilot \
    --service "readypick-pilot-$s" --force-new-deployment >/dev/null
done
```

### 6.2 The analysis service has no diarization models

The two pyannote repositories are gated on Hugging Face: their weights need an
accepted licence and a token, and this repository contains neither. The image
was built with `SKIP_GATED_MODEL_DOWNLOAD=true`, so the image starts, serves,
and reports diarization **unavailable** on `/health`.

It is not currently running at all, for a separate reason: see 6.3. Both have
the same visible consequence, and the product's behaviour is the same in either
case.

That is the product's documented degraded state, not a broken one: the
proctoring report says audio analysis was unavailable rather than reporting that
no second voice was heard. To fix it: accept the two licences on Hugging Face,
then rebuild with the token as a BuildKit secret.

```bash
HUGGINGFACE_TOKEN=<token> docker buildx build --platform linux/arm64 \
  --secret id=huggingface_token,env=HUGGINGFACE_TOKEN --push \
  -t 016617990245.dkr.ecr.ap-south-2.amazonaws.com/readypick-pilot/analysis:<tag> \
  analysis-service
```

### 6.3 Two account quotas, both at their new-account defaults

Neither is a configuration problem and neither can be fixed from this
repository. An offline plan explicitly cannot prove that quotas suffice, and
this is what that sentence meant here.

| Quota | Current | Requested | Status |
|---|---|---|---|
| Lambda concurrent executions | **10** | 1000 | `CASE_OPENED` |
| Fargate On-Demand vCPU | **4** | 64 | `PENDING` |

**Lambda at 10.** AWS refuses any reservation that leaves fewer than 10
unreserved, so the per-function ceilings are impossible and are switched off
behind `var.reserve_lambda_concurrency`. Nothing is unprotected: an account cap
of 10 is a harder ceiling than the 20 the task worker was asking for. Turn the
variable on once the increase lands.

**Fargate at 4 vCPU, and this one has a visible consequence.** The API and the
frontend use 2.0 between them, which leaves 2.0 for everything else:

```
api        2 tasks x 0.5 vCPU = 1.0
frontend   2 tasks x 0.5 vCPU = 1.0
                       in use = 2.0  of 4.0
analysis   2 tasks x 2.0 vCPU = 4.0  <- does not fit, and one task would
                                        leave nothing for an on-demand run
agent      1 task  x 1.0 vCPU        <- needed whenever an assessment runs
migrate    1 task  x 0.5 vCPU        <- needed on every deploy
```

**So the analysis service is not running.** Its events say
`You've reached the limit on the number of vCPUs you can run concurrently`.
It was NOT resized to fit: 2 vCPU and 8 GB is what the diarization pipeline
needs with three models resident, and sizing it for a quota rather than for its
work would be a number to unpick later.

What that costs, exactly: proctoring's audio analysis reports **unavailable**,
which is the product's documented degraded state and not a silent one. The
report says audio analysis was unavailable rather than reporting that no second
voice was heard. It is also already unavailable for the separate reason in 6.2,
so nothing is lost twice.

When the increase lands, the service starts on its own; it is already declared
with `desired_count = 2` and ECS keeps retrying placement. Check with:

```bash
aws service-quotas get-service-quota --region ap-south-2 \
  --service-code fargate --quota-code L-3032A538 --query 'Quota.Value'
aws ecs describe-services --region ap-south-2 --cluster readypick-pilot \
  --services readypick-pilot-analysis --query 'services[0].runningCount'
```

### 6.4 Confirm the alarm email

`manjuchro@gmail.com` is subscribed to
`arn:aws:sns:ap-south-2:016617990245:readypick-pilot-alarms`. **AWS has sent a
confirmation link and nothing is delivered until it is clicked.** Terraform
reports the subscription as created either way, which is exactly the kind of
"green means nothing" this repository has been bitten by. Check it:

```bash
aws sns list-subscriptions-by-topic --region ap-south-2 \
  --topic-arn arn:aws:sns:ap-south-2:016617990245:readypick-pilot-alarms \
  --query 'Subscriptions[].{Endpoint:Endpoint,Arn:SubscriptionArn}'
```

A `SubscriptionArn` of `PendingConfirmation` means nobody is being notified.

### 6.5 A domain, which retires the certificate warning

The listener currently uses an **imported self-signed certificate** whose
subject alternative name is `*.ap-south-2.elb.amazonaws.com`, so the hostname
matches and the only warning a visitor sees is about the issuer. Every visitor
sees it, once per browser.

When a domain exists, set two variables in
`infra/environments/pilot/terraform.tfvars` and apply:

```hcl
domain_name    = "app.example.com"
hosted_zone_id = "Z0123456789ABCDEFGHIJ"
```

That requests an ACM certificate with DNS validation, writes the Route 53 alias
record, switches the listener, and moves `FRONTEND_URL` to the real origin. It
is already written and gated on those two variables; nothing else changes.

Then delete the stopgap:

```bash
aws acm delete-certificate --region ap-south-2 \
  --certificate-arn arn:aws:acm:ap-south-2:016617990245:certificate/a36828f3-e71d-409c-8372-44d94743d954
```

### 6.6 Turn CI's deploy lane on

The workflow's pilot jobs are gated and every gate is currently closed, so a
push to `main` runs the tests and the Terraform checks and deploys nothing. To
enable it, set in the repository:

| | Name | Value |
|---|---|---|
| Variable | `PILOT_DEPLOY_ENABLED` | `true` |
| Variable | `AWS_REGION` | `ap-south-2` |
| Secret | `AWS_PILOT_DEPLOY_ROLE_ARN` | `arn:aws:iam::016617990245:role/readypick-github-actions` |
| Secret | `HUGGINGFACE_TOKEN` | needed by the analysis image build (6.2) |

The role's trust is scoped to `repo:HanuLisa-Technologies-LLP/pickready:*` and
no static AWS key exists in the repository.

### 6.7 Decisions worth revisiting, not defects

- **Auto-apply on `main`** for the pilot (2.5). One repository setting adds a
  required reviewer if you want one.
- **One NAT gateway**, so one AZ failure costs every task its egress and
  therefore the model provider. A locked decision for the pilot.
- **RDS single-AZ.** A failure is a restore, not a failover.
- **No Fargate Spot.** Deferred until the agent is stable.
- **`proctoring_event_retention_days` is 0**, which means no time-based purge:
  candidate data leaves with the tenant cascade. That is an owner decision the
  product deliberately does not make for you.

---

## 7. Architecture, in one page

```
                    a recruiter's browser
                             │  https (self-signed, see 2.8)
                    ┌────────▼─────────┐
                    │       ALB        │  /api/* /docs → api
                    └───┬──────────┬───┘  everything else → frontend
                        │          │
              ┌─────────▼──┐  ┌────▼─────┐
              │  ECS api   │  │ frontend │   Fargate services, private subnets
              └──┬───┬─────┘  └──────────┘
                 │   │
      short work │   │ long work            ┌──────────────────┐
                 │   └──────────────────────► assessment-      │
                 │                          │ trigger (zip)    │
        ┌────────▼─────────┐                └─────────┬────────┘
        │  task-worker     │                          │ ecs:RunTask
        │  (backend image) │                ┌─────────▼────────┐
        └────────┬─────────┘                │  agent task      │  one per
                 │                          │  runs once,      │  dispatch,
   EventBridge   │                          │  then exits      │  no service
   Scheduler ────┘                          └─────────┬────────┘
   7 sweeps                                           │
                                                      │
        ┌──────────────┐  ┌──────────────┐            │
        │  jd-gen      │  │ company-     │            │
        │  (sync)      │  │ profile      │            │
        └──────┬───────┘  └──────┬───────┘            │
               │                 │                    │
         ┌─────▼─────────────────▼────────────────────▼─────┐
         │      RDS PostgreSQL 16 · ElastiCache Redis 7      │
         │      data subnets, no route to the internet       │
         └───────────────────────────────────────────────────┘
```

The analysis service sits beside the API on Cloud Map at
`analysis.readypick.local:8100`, with no load balancer and no internet egress.

---

## 8. The add-features release (2026-09-06)

Deployed to the pilot the same way section 1's deployment was made: images
built locally, terraform applied from `infra/environments/pilot`, the
migration task run and read for its exit code, services rolled and verified BY
DIGEST. Commit `93ebfcb` is what serves; `1a05128` (a verification-script fix)
followed with no runtime change.

What shipped: migrations 0080 to 0085 (corporate email senders, dual-mode
assessment + video pipeline, intelligence dashboards capability, retention
consents, employer pages, BGV), the matching frontend surfaces, and the
production Google sign-in fix. `claude.md`'s 2026-09-06 section carries the
standing rules.

### Deployment lessons this run added

- **The account's Fargate On-Demand vCPU quota is 4**, which is why the
  analysis service had never run (its one task wants 2 vCPU) and why rolling
  api and frontend together trips the ceiling. A quota case for 64 vCPU is
  OPEN with AWS support (created 2026-09-05, still CASE_OPENED). Until it is
  granted: roll ONE service at a time, and park analysis at desired 0 while
  the frontend rolls. One analysis task is now running (first time ever);
  the second stays unplaceable at this quota.
- **Two image defects hid behind the previously deployed frontend image**,
  which had been built from an uncommitted variant: the committed Dockerfile
  had no curl (the ECS health check's probe) and relied on `ENV
  HOSTNAME=0.0.0.0`, which the ECS runtime overrides with the task hostname,
  so Next's standalone server bound the ENI address and refused localhost.
  Both are fixed IN the Dockerfile (`1de867b`, `93ebfcb`); tasks that log
  "Ready" and then die unhealthy are this signature.
- **Lambda refuses buildx's default OCI+provenance manifests.** The
  image-backed functions run the same bytes under the `<tag>-fn` sibling tag,
  pushed with `--provenance=false --sbom=false --output
  type=image,oci-mediatypes=false,push=true`.
- **Sign-in works now.** The apex `readypick.ai` was missing from Firebase's
  authorized domains (only www was listed) and
  `readypick-pilot/FIREBASE_SERVICE_ACCOUNT_JSON` still held the placeholder;
  both fixed live on 2026-09-06. The probe that tells the two failure modes
  apart: POST a bogus token at `/api/v1/auth/firebase/session`; 503 means
  unconfigured, 401 means verification ran.
- `scripts/smoke-test.sh` body-content checks read an empty temp file under
  Git Bash on Windows (curl.exe and MSYS disagree about `/tmp`); run it from
  Linux/CI, or verify the openapi and `/auth/me` shapes directly as this
  release's verification did.

---

## 2026-09-09 — Company DNA removed, pilot (ap-south-2)

Commit `9dd2d95` (the images) on `feat/ses-sns-delivery-tracking`. Every commit
after it in this release changes only tests, fixtures and one frontend comment;
`git diff --stat 9dd2d95 HEAD -- backend/app backend/alembic frontend/app
frontend/components frontend/lib` is two lines of comment, so the running bytes
are the tested bytes.

### What was deployed, and how it was proven

| | |
|---|---|
| Backend image | `backend:sha-9dd2d95958d2` @ `sha256:297e34c9c3ea3881b26d79539e9fcac7093d6613f7d6cb02551936d80f593b1b` |
| Lambda sibling | `backend:sha-9dd2d95958d2-fn` @ `sha256:d4f171300831cebfc27003642c2b124ae749d3355bd053e9fc765f302681d20f` |
| Frontend image | `frontend:sha-9dd2d95958d2` @ `sha256:386739abe4cf785a0ac7146b405f1219186dfc0265f412ca8833410c22e33946` |
| Task definitions | api:21, frontend:13, migrate:18, analysis re-registered on its pinned `93ebfcb` |
| Migration | 0088_remove_company_dna, one-shot task `683a5df5909c4bfc91ac41b44d460050`, exit 0 |

`verify-deployment.sh` per-service lines: api 4 containers all on the backend
digest, frontend 2 on the frontend digest, analysis NO RUNNING TASKS. Its exit
code is still FAILED and still for the standing reason: the account's Fargate
vCPU quota is 4 and analysis wants 2 vCPU per task. Read the lines, not the
exit code. Both ALB target groups: all targets healthy.

### The check that actually proves the release

The OpenAPI document, before and after, against the live site:

    before: 284 paths, 6 of them /api/v1/clients/{client_id}/company-dna*
    after:  278 paths, zero

and `GET /api/v1/clients/<uuid>/company-dna/status`, a live route an hour
earlier, now answers 404. `POST /jobs` and `GET /companies/me/profile` both
answer 401 unauthenticated, which is reachable-and-gated rather than gone.

### THE REGION DEFAULT COST A STEP, AND IT IS FIXED

`run-migration.sh` defaulted to `AWS_REGION=ap-south-1` while the pilot lives
in ap-south-2, so the migration answered **"TaskDefinition not found"** -- which
reads as a broken deploy rather than as a lookup in an empty region.
`verify-deployment.sh` carried the same default; `deploy-services.sh` and
`update-lambda-code.sh` carried the opposite one. All four now read
`AWS_REGION`, then `AWS_DEFAULT_REGION`, then the CLI's own configured region,
and REFUSE by name when none of them says. spec-doc6 D5 removes the assumption
by name; a default here was that assumption, hardcoded in four places, in two
directions.

### THE FRONTEND IMAGE NEEDS BUILD ARGS THAT THIS FILE DID NOT RECORD

`frontend/Dockerfile` refuses without `NEXT_PUBLIC_FIREBASE_API_KEY` and
`NEXT_PUBLIC_FIREBASE_PROJECT_ID`, and the values live in
`frontend/.env.local`. A build without them fails inside `npm run build`
rather than at the guard, because buildx serves the guard's RUN layer from
cache. The working command:

    set -a; . frontend/.env.local; set +a
    docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push \
      --build-arg NEXT_PUBLIC_FIREBASE_API_KEY="$NEXT_PUBLIC_FIREBASE_API_KEY" \
      --build-arg NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN="$NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN" \
      --build-arg NEXT_PUBLIC_FIREBASE_PROJECT_ID="$NEXT_PUBLIC_FIREBASE_PROJECT_ID" \
      --build-arg NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET="$NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET" \
      --build-arg NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID="$NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID" \
      --build-arg NEXT_PUBLIC_FIREBASE_APP_ID="$NEXT_PUBLIC_FIREBASE_APP_ID" \
      -t "<registry>/readypick-pilot/frontend:$TAG" frontend

A stale `frontend/.next-dev/` also fails the build's type check against routes
that no longer exist. `.dockerignore` excludes it from the image, but it fails
a LOCAL `npm run build`, which is where the deleted page surfaced first.

### Things this release did NOT do

- No `terraform apply` outside the two `-target`ed resources.
- `analysis_image_tag` stayed pinned at `93ebfcb`; nothing rebuilt it.
- The vCPU quota case is still open; analysis still has no running task.

## 2026-09-10 — Native support, RDS bump, runtime completions, pilot (ap-south-2)

Commit `3b27abb` on `feat/ai-upgrade-rpn-ai-up-001`. One tag for backend and
frontend (`sha-3b27abb`), plain manifests, one digest per image for both
runtimes. Suite on the deployed commit: 6132 passed, 1 skipped, 0 failed.

- Migrations 0093 (support_threads + support_messages + RLS + capability
  seeds) and 0094 (report `model_id`/`prompt_version`) applied; schema read
  back `0094_report_provenance`.
- `terraform apply -var image_tag=sha-3b27abb -var frontend_image_tag=sha-3b27abb`:
  the tfvars pin `bootstrap`, and applying WITHOUT the overrides registers
  task-definition revisions pointing at the bootstrap image, which the next
  deploy-services.sh would then faithfully roll out. Worth restating every
  release until a wrapper owns it.
- RDS: `db.t4g.micro -> db.t4g.medium` in place, storage ceiling 100 -> 200,
  floor kept at 50, `multi_az` still false with the must-flip note now in the
  file. Terraform waited out the modify; `describe-db-instances` read back
  medium/available/no-pending before the migration ran. NO RDS PROXY, owner
  decision: the vendor's own pinning documentation says PostgreSQL sessions
  pin on SET, set_config() and named prepared statements, and this
  application does all three on effectively every session, so the proxy
  would multiplex nothing. Reasoning beside the instance_class line.
- The vendor-sync EventBridge rule (`readypick-sync-intercom-companies`) was
  destroyed with the integration; the scheduler listing no longer names it.
- Services api 26->27, frontend 14->15; analysis untouched on 12; all three
  verified by digest against RUNNING tasks. Lambdas all on `sha-3b27abb`.
- Support routes answer 401 unauthenticated at the apex: mounted and gated.
  Zero API errors in the ten minutes after rollout.

## 2026-09-11 — The engineering-audit close-out, pilot (ap-south-2)

Commit `bf74fc1` on `feat/ai-upgrade-rpn-ai-up-001`. Backend `sha-bf74fc1`
(single plain manifest, one digest for ECS and Lambda); frontend deliberately
kept on yesterday's `sha-3b27abb` because no frontend file changed. Suite on
the deployed commit: 6135 passed, 1 skipped, 0 failed.

- The LLD audit's three fixes ship: the per-job matching lock, the support
  queue N+1 removal, and the pilot resize to one task per service at rest
  with autoscaling ceilings kept.
- The resize was PROVEN, not assumed: after the apply, target tracking
  scaled api, frontend and analysis in, and `describe-services` read back
  desired 1 / running 1 for all three. Roughly 40% of steady-state Fargate
  spend, gone, with rolling deploys and load behaviour unchanged.
- One operational lesson worth keeping: `MSYS_NO_PATHCONV=1` (required for
  AWS CLI ARNs on Git Bash) BREAKS docker compose's path conversion, so a
  shell that exports it for a build cannot also launch `scripts/test.sh`.
  The suite "failed" instantly with a mangled compose path; the fix is a
  clean environment per concern, and the gate caught it because a suite
  that did not run reports nothing that looks like a pass.
- Audit deliverables: `docs/architecture/ENGINEERING_AUDIT_2026-09-11.md`.

## 2026-09-12 — BGV and conversations go live, pilot (ap-south-2)

Commit `ce139c3` on `feat/ai-upgrade-rpn-ai-up-001`. Images `sha-1af3d00` for
backend and frontend; the three commits after the build touched only Terraform,
so no application byte changed between the tag and HEAD. Clean full suite on the
deployed code: **6245 passed, 1 failed, 1 skipped**, and the one failure is the
hardcoded-region sweep that `ce139c3` answers.

- **No migration.** 0095 went out with the previous release, so the schema was
  already in place and the tables were empty: conversations 0, employments 0,
  verifications 0, read back from the database rather than assumed.
- Task definitions api 29 to 30, frontend 16 to 17, analysis 12 to 13, migrate
  and agent to 27. All three image-backed Lambdas moved to the same backend
  image by `update-lambda-code.sh`, which is how they move: the module sets
  `ignore_changes = [image_uri, ...]` so Terraform owns the shape and a script
  owns the code.
- **Verified by digest against RUNNING tasks**, not against the service
  definition: api 2 tasks and frontend 1 task on the digests this build
  produced. `verify-deployment.sh` REFUSED the first attempt because no expected
  digests were supplied, which is the script working: a skipped check is not a
  passed check.
- Site 200, login 200, api target group healthy, every new route answering 401
  unauthenticated and an unknown path answering 404. **Zero tracebacks, zero
  exceptions and zero 5xx** in the 35 minutes around the rollout.
- The three new capabilities are seeded, 5 rows each, one per customer role.
  Read from `role_permissions`, because a capability constant is half a change.

### THE APPLY WAS TARGETED, AND WHAT WAS EXCLUDED IS THE INTERESTING PART

The plan also wanted to change `module.network.aws_security_group.{rds,redis}`:
adding an egress rule with an EMPTY destination set, which in AWS replaces the
default allow-all egress on the DATABASE's security group. That is pre-existing
drift between the code and the account, it has nothing to do with this feature,
and this product had a full outage from a database-connectivity change the day
before. Folding it into a feature release is how an outage gets attributed to
the wrong change. It is still drift and it still wants applying, deliberately,
on its own.

### SES INBOUND IS BUILT AND NOT APPLIED, BECAUSE THE REGION CANNOT RECEIVE

`ap-south-2` SENDS perfectly well and cannot receive:
`aws ses describe-active-receipt-rule-set` answers `InvalidAction` there because
the API is absent from the region, and `inbound-smtp.ap-south-2.amazonaws.com`
does not resolve at all. The module as first wired would have published an MX
record pointing at a hostname with no address. Every employer's reply would have
bounced at their own mail server and nothing in this account would have logged
it, which is the exact failure the reply-address design exists to prevent,
arriving through the infrastructure instead of through the parser.

So `INBOUND_EMAIL_DOMAIN` is empty on every container, the product sets no
Reply-To, and `conversations.reply_address` records that once rather than
producing a thread that can never receive anything. An employer's reply arrives
in the sending mailbox instead of the thread. Everything else in BGV and
conversations works.

**What remains**: move receiving to `ap-south-1`, which is verified to resolve
and holds no active rule set, through a provider alias and a second `lambda`
instance in that region. The module refuses a non-receiving region by
validation and pilot's `has_inbound` requires one, so this cannot be turned on
by editing a boolean.

### Two repairs this deployment needed before it could be trusted

- **Pilot could not be planned offline AT ALL**, and had never been. Three
  `data "aws_caller_identity"` lookups and two missing `offline-plan.tfvars`
  entries stopped the plan before it reached anything. The data source calls
  STS; the planning profile runs against account 000000000000 in a region that
  does not exist. The account id was already a required variable everywhere, so
  this reads the same fact from the input rather than the network. Staging and
  production were failing on the same lookup inside the `lambda` module.
- **The impeccable gate was failing on generated output** it cannot fix: the
  graphify knowledge-graph viewer, HTML nobody wrote. Now asked of
  `git check-ignore` rather than a hardcoded list.

### The Sarkar Corp accounts, read from the table

Four users on the tenant. The client Super Admin is `active` and Firebase-bound.
Two recruiters and one hiring manager are present; their `firebase_uid` is NULL
and binds on first proven sign-in, which is the designed flow rather than a gap.

---

## 2026-09-12 — The application's own database credential, pilot (ap-south-2)

Commit `a36cd00` on `feat/ai-upgrade-rpn-ai-up-001`. Backend and frontend both
`sha-a36cd00`. Suite on the deployed commit: 6203 passed, 1 skipped, 0 failed.

**The product no longer holds the RDS master credential.** `DATABASE_URL` now
carries `pickready_app` with a password ReadyPick owns, object ownership sits on
a dedicated `readypick_owner` role, and the master is unused. AWS rotates that
master again on 2026-09-19; on 2026-09-11 that rotation took the whole site
down, and it now cannot.

- `rotate-app-db-credential.sh` ran BEFORE the migration, which is the required
  order: `POSTGRES_MIGRATION_ROLE` is set on the migrate container and the role
  it names did not exist yet.
- The migration then ran AS `pickready_app`, escalating with `SET ROLE`, and
  applied 0095. That is the escalation design proven in production rather than
  in a rehearsal.
- Services api 28 -> 29, frontend 15 -> 16; analysis untouched on 12. All three
  verified by digest against RUNNING tasks. All 3 image-backed Lambdas recycled
  onto the same backend image, which is what makes them pick up the new DSN
  (`secrets_bootstrap` fetches once per execution environment).
- Site 200, login 200, API 200, `/health` 200 with real SQL, and ZERO 500s in
  the fifteen minutes around the rollout.

### One real defect this deployment found, before it did any damage

The first rotation attempt FAILED with `AccessDeniedException` on
`PutSecretValue`. The write grant had been added to the per-service secrets
policy, which is attached to the EXECUTION role; the script writes the secret
with the application's own boto3 client, which runs as the TASK role. A grant on
the execution role is a grant the code can never use.

It failed SAFELY, which was the design: the script proves the new credential
before writing the secret, so the existing DSN stayed in place and the site
never noticed. The fix splits the write into its own policy attached to the task
role, the same split `task_s3` already makes and for the same stated reason.

The ownership DDL had already committed at that point, and the site stayed up
throughout: the master inherits `readypick_owner`, so moving ownership was
transparent to the running API.

### What this deployment carries besides the credential fix

Migration 0095: `candidate_employments`, `bgv_verifications`, `conversations`,
`conversation_participants`, `conversation_messages`, `conversation_attachments`,
RLS on all five tenant tables, the employment-history immutability trigger, and
three seeded capabilities. The BGV offer gate is live at `apply_transition` and
is INERT today by construction: it blocks only an explicit `experienced`
declaration, and no candidate has one yet.

There is no user-facing BGV or conversation surface in this build. The APIs,
the realtime layer, the SES bridge and the frontend are not written yet.

---

## 2026-09-16 — RBAC UX, occupational STEM, AI Job SWOT, pilot (ap-south-2)

Commit `1beebe6` on `integrate/pr6-rbac-stem-swot`. One tag for backend and
frontend (`sha-1beebe6`). The RBAC / permission-aware UX / occupational STEM /
Job SWOT specification had been BUILT (PR #6, merged to `origin/main`) and was
never deployed: the running API was `sha-1af3d00`, and `swot-analysis` did not
appear in the live `openapi.json`. That is what this release closes.

### What it took to get a suite that could be believed

The backend suite did not fail. It STOPPED, at roughly a quarter, with no
failure and no output, and the last thing printed was a passing dot. A suite
that hangs is worse than one that fails: there is nothing to read.

`py-spy dump --pid` on the live process named it in one line. The main thread
was in `TestClient.__exit__`, joining the anyio portal thread; the portal thread
was inside `ProactorEventLoop.close()`, which on Windows waits on outstanding
overlapped I/O. The realtime hub's reader task was still awaiting a redis socket
on that loop, so the loop could not close and the join never returned.

Three defects in one module-level singleton, all fixed in `1beebe6`/`29978fd`:

- **Nothing stopped the reader on shutdown.** `leave()` stops it when the LAST
  socket goes, which is the quiet case, not the real one: a deploy stops a task
  with sockets still open. `lifespan` now calls `hub.shutdown()` before
  disposing the engine.
- **A hub that outlived its loop went quiet for good.** `_ensure_reader` guarded
  with "is there a task, and is it not done", and a task on a CLOSED loop never
  reports done -- so it decided it was still subscribed and stopped delivering
  to every socket on the instance, with nothing logged because nothing failed.
  This one is platform-independent and would have bitten production on any loop
  restart.
- **`publish_after_commit` could fail the send it was announcing.** Its
  `after_commit` handler runs INSIDE `session.commit()`, so a RuntimeError from
  `loop.create_task` came out of the commit and 500'd the request AFTER the
  message was durably stored: the sender told it failed while everyone else can
  already read it.

Both shutdown awaits are now bounded, because a shutdown path that can hang
moves the bug rather than fixing it.

### The suite, measured twice

- Host, Python 3.14: **6384 passed, 1 skipped, 0 failed** (18m04s).
- Linux, Python 3.12 (the image's interpreter, in the backend image against an
  isolated database): **6373 passed, 11 skipped, 0 failed**. The 11 skips are a
  harness artifact: those tests read a hardcoded `localhost:55432`, which is not
  reachable from inside a container.
- Frontend: 261 vitest tests, `tsc --noEmit` clean, 19/19 contrast assertions,
  impeccable gate clean.

**One intermediate run was invalidated and is recorded rather than dropped.** A
rerun reported 17 failures with `UndefinedTableError` and "connection was closed
in the middle of operation". That was self-inflicted: `scripts/test.sh` DROPs and
recreates `readypick_test`, and targeted runs were started beside a full one.
Never run two `test.sh` invocations against the same database.

### What was applied

`terraform apply -var image_tag=sha-1beebe6 -var frontend_image_tag=sha-1beebe6`.
Plan read 4 add / 5 change / 4 destroy, and every line was checked before
applying, because "4 to destroy" on a live environment is not something to skim:

- The 4 add/destroy pairs are ECS task-definition REVISIONS (agent, api,
  frontend, migrate). Services keep running the old revision until rolled.
- The RDS and Redis security-group changes read as "remove all egress" and are
  a state reconciliation only: both already had `IpPermissionsEgress: []` live.
- The SES IAM change NARROWS `Resource: "*"` to
  `arn:aws:ses:ap-south-2:...:identity/*`, splitting `ses:ListIdentities` into
  its own statement because that call cannot be scoped. It is the wildcard-IAM
  fix from `f1116d6`.

**Services were rolled ONE AT A TIME, by hand, rather than with
`deploy-services.sh`.** That script rolls every service it finds at once. api
and frontend are 0.5 vCPU each and analysis is 2.0, so with all three up the
account's 4-vCPU Fargate quota leaves room for exactly one extra task: rolling
api and frontend together lands on 4.0 exactly, and a third rollover fails
placement. Rolled api, waited `services-stable`, then frontend.

`analysis` was NOT rebuilt or rolled: nothing under `analysis-service/` changed
since the deployed commit, and `analysis_image_tag` stays pinned at `93ebfcb`.

### Verified, by digest and by table

- `verify-deployment.sh` with all THREE expected digests: "Every running task is
  the image this build produced." Supplying only two made it report
  `analysis: SKIPPED` and exit 1, which is the script behaving correctly -- a
  skipped check is not a passed check.
- Schema read back from the pilot database itself: `0097_job_swot_analysis
  (head)`. The migration script had reported "Migration did not finish within
  900s ... NOT treating this as success"; the task's own CloudWatch log showed
  0095 -> 0096 -> 0097 applied, and `alembic current` confirmed head. The
  script was right to refuse; the timeout was slow task teardown.
- `job_swot_analyses` exists with all 17 columns, 0 rows (nobody has generated
  one yet, which is the correct `not_generated` state).
- **31 historical jobs carry a re-derived classification: 28 STEM, 3 Non-STEM.**
  The STEM side is Machine Learning Engineer, Data Engineer, DevOps / Cloud
  Engineer, Java / Python Backend Developer, React Frontend Developer, MERN
  Stack Developer, Full Stack Developer (.NET), AI / Generative AI Engineer and
  AI architect. All three Non-STEM rows are **Data Analyst**, which is section
  19's exact nuance: an `analyst` is not STEM on the title alone.
- The four SWOT routes are in the live `openapi.json` and all four answer 401
  unauthenticated.

**A probe that reported an EMPTY database was wrong, and the reason is worth
keeping.** `SELECT set_config('app.bypass_rls','on',true)` sets the value
TRANSACTION-locally, so it was discarded with the implicit transaction around
that one statement and every later query ran under RLS, returning zero rows.
Zero rows and "no permission to see any rows" look identical. Pass `false` for
a session-level setting when probing.

### Not done, deliberately

- No SWOT has been generated against a live model. The route is reachable and
  authorized; the first real generation is a human action in the product.
- No authenticated browser walkthrough. Sign-in is Firebase OAuth and cannot be
  completed headless, so the permission-aware UX is proven by its unit tests,
  the source sweep in `lib/read-only-messaging.test.ts` and the API's own
  401/403 answers, and NOT by a visual pass. Saying so plainly rather than
  implying otherwise.

---

## The production-hardening release, 2026-09-18

Backend `sha-a74dfa9`, frontend `sha-3a60eba`, analysis unchanged at the
`93ebfcb` pin. Verified by digest, by smoke test, and by probing the two holes
this release exists to close.

| Artifact | Tag | Digest |
|---|---|---|
| api | `sha-a74dfa9` | `sha256:4641f285f661a440e046e497589b8293652dcf8139678b6b33c18fb436bd9d05` |
| lambdas | `sha-a74dfa9-fn` | `sha256:6360a587dc08848f443a81c42d2d54b802718be514f74da3051eebfab673ed86` |
| frontend | `sha-3a60eba` | `sha256:4269b9bfd85229f1a95aaa0911af37ca39d52ffa2faebb7e24e0e0bebcaaa272` |
| analysis | unchanged | `sha256:e0c6d4880b94fe3531a904042ff932ab42e3caeb19b88c03dd9b58c0fc037ec1` |

`terraform apply` registered `readypick-pilot-api:34` and
`readypick-pilot-frontend:23`; `deploy-services.sh pilot` rolled both and
waited for stable; `update-lambda-code.sh` moved all three image-backed
functions. `verify-deployment.sh pilot` with all THREE expected digests: "Every
running task is the image this build produced."

### The two remote holes, probed on the live site AFTER the roll

Both were reachable by an anonymous caller before this release.

- **`POST /api/v1/billing/webhook/razorpay` now answers 503** to an unsigned
  POST and to one carrying a bogus `X-Razorpay-Signature`, with the body
  `{"detail":"Webhook verification is not configured"}`. It previously treated
  an absent webhook secret as "development" and PROCESSED the unsigned event,
  so an anonymous POST could grant subscription credits on the live site.
- **`POST /api/v1/verification/inbound-email` now answers 403** with no secret
  header and with a wrong one. It previously logged
  `verification.inbound_unauthenticated` and admitted anybody who knew a thread
  token, and thread tokens travel by email.

### Two probe mistakes worth keeping, because both read as findings

Neither was a defect in the product; both would have gone into a report as one.

- **`update-lambda-code.sh` reported "Source image does not exist" for all
  three functions**, which reads exactly like a missed push. The image was
  there. The repository is `readypick-pilot/backend`, not `readypick-backend`,
  and an ECR path that does not exist and an image that does not exist produce
  the same sentence. `describe-repositories` settled it in one call.
- **The webhook answered 404 to the first probe.** The route is
  `/api/v1/billing/webhook/razorpay`, not `/api/v1/billing/webhook`. A 404 from
  a wrong URL and a 404 from a deleted route are indistinguishable from
  outside, and the live `openapi.json` is what told them apart. Read the route
  table before concluding anything from a 404.

`verify-deployment.sh` also refused a first run that supplied
`EXPECTED_API_DIGEST`: the variable is `EXPECTED_BACKEND_DIGEST`, and rather
than silently checking two of three it reported `api: SKIPPED` and exited 1.
That is the script working: a skipped check is not a passed check.

### Verified on the live site

- `scripts/smoke-test.sh https://readypick.ai`: every check PASS, including
  `/health/live 200`, the three authenticated endpoints, the capabilities array
  and all four route-contract assertions.
- **Twelve public routes, all 200**: `/`, `/about`, `/insights`, `/privacy`,
  `/terms`, `/employers`, `/docs`, `/login`, `/robots.txt`, `/sitemap.xml`,
  `/llms.txt`, `/opengraph-image`. Every one of the three defects the previous
  release found by probing production is closed, and the sweep was done in ONE
  pass rather than one route per deploy cycle.
- The full security header set is present on a live response: CSP with
  `frame-ancestors 'none'` and `object-src 'none'`, HSTS at two years with
  `includeSubDomains`, `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy` and a Permissions-Policy that grants camera, microphone and
  display-capture to self (proctoring needs all three) and denies the rest.
- Backend suite before the build: **6552 passed, 1 skipped, 2 xfailed, zero
  failures**, on a fresh database. The four failures the previous run carried
  were all mine and all fixed; one xfail became a pass when
  `"disregard the rubric"` was correctly classified as an injection.

### Still owed, and the first one is new

1. **`RAZORPAY_WEBHOOK_SECRET` is still `PLACEHOLDER_NOT_CONFIGURED`**, so the
   503 above applies to REAL webhooks too and a genuine subscription payment
   will not credit the customer. This is the deliberate safe direction of the
   fix, not a regression, and it is the highest-priority owner action.
   Enumerating all 16 secrets found THREE placeholders, not the two a
   previously paginated listing reported: `SMTP_PASSWORD` and `MSG91_API_KEY`
   are both genuinely harmless, and this one is not.
2. The `alarm_emails` SNS subscription is still `PendingConfirmation`, so all
   16 alarms notify nobody.
3. One real Google sign-in and one real Razorpay checkout against the new CSP.

**`/health` answers 307 on the live site and that is by design.** The ALB api
route patterns are `/api/*`, `/openapi.json` and `/health/live`, so the
readiness probe (which checks the database and Redis) is not routed to the API
and the request reaches the frontend, where `proxy.ts` is deny-by-default.
The target group health check uses `/health/live` deliberately: a readiness
check that fails on a Redis blip would kill tasks that are serving traffic
perfectly well. RDS and ElastiCache are watched by their own CloudWatch alarms
rather than through this path. Recorded so the 307 is not read as a fault.

---

## Release 2, the candidate's right to erasure, 2026-09-18

Backend and frontend both `sha-b0ee2c3`, analysis unchanged. The DEPLOYED CODE
is `b0ee2c3`; later commits on this branch are documentation only.

| Artifact | Tag | Digest |
|---|---|---|
| api | `sha-b0ee2c3` | `sha256:e039f1f0ccd795f81334c00180e7b8f9e67aca942ee91a11db471f88cc90b350` |
| lambdas | `sha-b0ee2c3-fn` | `sha256:57c84ef20d1a5a7d5b1eb8bec70126075de00ac621aa2e3e86b84cb9553f54eb` |
| frontend | `sha-b0ee2c3` | `sha256:6fc25a80e653aeba4793ce313ffe0463a7997fc59dfed5ab42edf46fac44a81f` |
| analysis | unchanged | `sha256:e0c6d4880b94fe3531a904042ff932ab42e3caeb19b88c03dd9b58c0fc037ec1` |

No migration: this release adds no alembic revision.

### What shipped

- **Delete My Profile.** `services/erasure` had been complete since the AI
  runtime upgrade and reachable by nobody: no route on any portal could ask for
  an erasure. `DELETE /portal/me` with a server-checked typed phrase, and
  `GET /portal/me/deletion-notice` serving the warning verbatim so a screen
  cannot author its own copy about an irreversible rule. The erasure also takes
  the sign-in `users` row, without which the person keeps a working identity
  against a profile that no longer exists.
- **The employer verification link expires**, at three days, derived from the
  send date. It was single-use and had NO expiry, so one nobody ever used
  stayed valid for ever in a third party's mailbox. SEC-20.
- **`linkedin.com` excluded at the search provider.** SEC-21. Found by sweeping
  the tree for `if False` after a subagent left one earlier in this work.

Every one of the three is mutation-checked: disabling the fix fails its test.

### Verified, in one pass

- Backend suite before the build: **6561 passed, 1 skipped, 2 xfailed, zero
  failures**, on a fresh database. Exactly +9 over the previous run, matching
  the nine tests added.
- `verify-deployment.sh` with all THREE expected digests: "Every running task is
  the image this build produced."
- `smoke-test.sh https://readypick.ai`: every check PASS.
- **Both new routes live and refusing an anonymous caller**: 401 on
  `GET /portal/me/deletion-notice` and on `DELETE /portal/me`, and both present
  in the live `openapi.json`.
- **Twelve public routes, all 200.**
- **The two CRITICAL holes are still closed after the roll**: the Razorpay
  webhook answers 503 to an unsigned POST and to a bogus signature, and the
  inbound relay answers 403 with no secret header. Re-probed rather than
  assumed, because a deploy is exactly when a fix silently reverts.
- `/` still serves `Site Under Construction`, as the owner requires.

### Two process notes, both of which cost time

**The authoritative suite ran twice because the first run was killed on a wrong
diagnosis.** `py-spy` correctly named `test_import_graph` blocked in
`subprocess.run`, and the conclusion drawn from it was wrong twice over: first
that CPU contention from two concurrent emulated ARM64 builds caused it, then
that flat parent CPU meant "frozen". A parent blocked in `communicate` while
its child works burns no CPU, so flat CPU is that test's NORMAL state. The run
was slow, not hung. `claude.md` now carries the diagnostic that actually
separates the two: ask whether a CHILD process exists and is `active`.

**The machine is clock-throttled to 1200MHz**, which is why the second run took
1660s against an 853s baseline, and why per-module fresh-interpreter imports
cost about twelve seconds each. Worth checking `MaxClockSpeed` before
concluding anything is wrong with a slow run.

---

## Release 3, the week-long outage, 2026-09-18

Backend `sha-da4b5f1`, frontend unchanged at `sha-b0ee2c3`, analysis unchanged.
**The first deploy in this sequence that carried a migration.**

| Artifact | Tag | Digest |
|---|---|---|
| api | `sha-da4b5f1` | `sha256:10bdaed5f0089a842c12176b2c184c189ac218607af735ec755772e6baf0d6cc` |
| lambdas | `sha-da4b5f1-fn` | `sha256:4dd1e589b5d052802688ce811c5c1ced3fcbe60f2f46db2483c2c46ff24b2692` |
| frontend | unchanged | `sha256:6fc25a80e653aeba4793ce313ffe0463a7997fc59dfed5ab42edf46fac44a81f` |
| analysis | unchanged | `sha256:e0c6d4880b94fe3531a904042ff932ab42e3caeb19b88c03dd9b58c0fc037ec1` |

Order: `terraform apply`, then **`run-migration.sh` BEFORE the service roll**,
then the roll, then the Lambdas. That order is required rather than tidy: the
new code calls a function migration 0099 creates, so the function has to exist
first. The old code never calls it, which is what makes migrating first safe.

### THE OUTAGE IS OVER, AND THE PROOF IS A TIMESTAMP

`pickready.refresh_dashboard_views` is scheduled every five minutes and had
failed on every run since the 2026-09-11 credential split, with
"must be owner of materialized view dashboard_job_metrics". Read from the
task's own log:

```
19:35:10 failed        20:10:10 failed
19:40:14 failed        20:15:10 failed
19:45:10 failed        20:20:10 failed
19:50:10 failed        20:25:10 failed
19:55:10 failed
20:00:10 failed        20:30:08 SUCCEEDED
20:05:10 failed
```

Eleven consecutive failures at five-minute intervals, then the first scheduled
run after the Lambda was updated succeeded, in 0.3 seconds.
`readypick-pilot-task-worker-error-rate` has gone from ALARM to **OK**.

### Verified, in one pass

- Backend suite before the build: **6566 passed, 1 skipped, 2 xfailed, zero
  failures**. Exactly +5 over release 2, matching the five tests added.
- Migration: run as a one-shot ECS task, polled to STOPPED, `exit=0`, and the
  task's own log read back to confirm
  `Running upgrade 0098_jobs_embedding_hnsw_index -> 0099_dashboard_refresh_grant`.
  Exit zero alone would not have proven which revision ran.
- `verify-deployment.sh` with all THREE digests: "Every running task is the
  image this build produced."
- `smoke-test.sh`: every check PASS.
- **SEC-23 confirmed closed on the live site**: `Server-Timing` and
  `X-Query-Count` are GONE from API responses, and `X-Debug-SQL: 1` is no
  longer honoured.
- **No regression in the earlier fixes**, re-probed rather than assumed: the
  Razorpay webhook still 503s, the inbound relay still 403s, and
  `GET /portal/me/deletion-notice` still 401s for an anonymous caller.

### What this release says about the alarm nobody receives

The defect it fixes ran for a week in production, was reported correctly by the
code (it retried three times and re-raised), was measured correctly by
CloudWatch, and fired an alarm that reached nobody because the SNS subscription
is still `PendingConfirmation`. It was found by listing alarms by hand.

**Confirming that subscription is worth more than any single fix in these three
releases**, because it is the difference between the next one being found in
minutes and being found in a week.

---

## Release 4, consent lifecycle, 2026-09-18

Backend `sha-3789d79`, frontend unchanged at `sha-b0ee2c3`, analysis unchanged.
Migration 0100 applied BEFORE the service roll, confirmed from the migrate
task's own log (`Running upgrade 0099 -> 0100`), exit 0.

| Artifact | Tag | Digest |
|---|---|---|
| api | `sha-3789d79` | `sha256:953dbf432cee40b19295e788944d55da8125dc4cc49f90f57594a55893e94d42` |
| lambdas | `sha-3789d79-fn` | `sha256:3face500f72d8b8a45737f35f583bf15bdf74e1f66ea58e6690ed09ffcc5e60a` |
| frontend | unchanged | `sha256:6fc25a80e653aeba4793ce313ffe0463a7997fc59dfed5ab42edf46fac44a81f` |
| analysis | unchanged | `sha256:e0c6d4880b94fe3531a904042ff932ab42e3caeb19b88c03dd9b58c0fc037ec1` |

### What shipped

- **Feature 8**: consent renewal, the final warning and the 24-month
  inactivity rule. Four derived-stage stamps (0100), the daily
  `pickready.sweep_consent_lifecycle`, its EventBridge rule in all three
  environments, two letters, and engagement recording at the candidate
  chokepoint. **The erasure half is GATED OFF** by
  `consent_auto_deletion_enabled` and arming it is an owner decision.
- The StaleDataError fix: the engagement stamp had broken Delete My Profile
  (dirty ORM object flushed after a raw-SQL delete), caught by the full suite
  and fixed with `session.expunge` before the cascade.

### Verified, in one pass

- Suite: **6577 passed, 1 skipped, 2 xfailed, zero failures** (a first attempt
  reported 478 skips and was discarded: Docker Desktop had restarted and taken
  the test database with it; the run proved the environment, not the code).
- All three digests confirmed against the RUNNING tasks.
- Smoke test: every check PASS.
- Regressions re-probed: webhook 503, relay 403, deletion notice 401,
  `Server-Timing` still absent, landing page still Under Construction.
- `readypick-sweep-consent-lifecycle` is ENABLED at `rate(1440 minutes)`, and
  the sweep was INVOKED ONCE against production rather than trusted to exist:
  its own log line reads
  `consent.sweep reminded=0 warned=0 erased=0 would_erase=0 deletion_armed=False`,
  which is exactly right for a databank holding zero candidates with the
  deletion gate shut.

---

## Release 5, the vivekium build, 2026-09-19

Backend `sha-0587071`, frontend `sha-2a0d422`, analysis unchanged. Migrations
0101, 0102, 0103 applied BEFORE the roll, confirmed from the migrate task's
own log. Suite: 6632 effective green (one failure was the security sweep
demanding documentation for the new public form routes, which it got).

| Artifact | Tag | Digest |
|---|---|---|
| api | `sha-0587071` | `sha256:6dc5ba19b5029fca8922135501c54b0397253d637f49643701322008e7ad63c8` |
| lambdas | `sha-0587071-fn` | (verified by function ImageUri) |
| frontend | `sha-2a0d422` | `sha256:9ba1b0ac65f30bdd6bdeaf2f521e19e78d2e8649e47249f853dbf17e826582a4` |
| analysis | unchanged | `sha256:e0c6d4880b94fe3531a904042ff932ab42e3caeb19b88c03dd9b58c0fc037ec1` |

### What shipped

The owner ruled the vivekium brief final, and this release lands everything
buildable under that ruling: SEC-24 (ENVIRONMENT=production, five guards
live), the seven-column recruiter table with match_percent as rule 1's one
sanctioned number, the two-stage per-item consent framework (0101), C5 job
closure erasing its assessment data in the close transaction, the employer
checkbox form with 3-day single-use links (0102), the day-3 chase sweep and
SES bounce alerts with masked HR addresses, and the last-two-employers
auto-maintenance under the narrowed finality trigger (0103).

### THE THREE DEPLOYMENT DEFECTS THIS RELEASE SURFACED, in the order the
### pipeline refused

1. **A bare `terraform apply` shipped the bootstrap image.** tfvars said
   `image_tag = "bootstrap"` while every release passed the real tag on the
   CLI; the SEC-24 apply re-registered the API task def onto bootstrap and
   the next roll served it for roughly two hours. Caught by describing the
   RUNNING task's image, which is the rule. tfvars now carry the release tag.
2. **The JWT boot guard refused the migrate one-shot**, which holds no
   signing key by design. First patched as a migrate opt-out, which the next
   defect proved was the wrong shape.
3. **Every Lambda died at import under production**, twice: logging built a
   full validated Settings before the secret bootstrap ran, and then the
   guard itself assumed every container holds every secret, which is false
   under per-service enumeration. The guard is now DECLARED by the signers
   (REQUIRE_JWT_SECRET on the api service and the task worker), the worker
   gains the key it turned out to genuinely need for the invite links it
   signs, and a declared signer without its key still refuses.

### Verified

All three digests against RUNNING tasks; startup log reads
"environment": "production" in the production JSON format; smoke ten for
ten; webhook still 503, deletion notice 401, unknown form token 404; both
sweeps INVOKED live rather than trusted:
`bgv.reminder_sweep chased=0` and
`consent.sweep reminded=0 warned=0 erased=0 would_erase=0
deletion_armed=False`, each correct for a databank with zero candidates.
`readypick-sweep-bgv-reminders` ENABLED daily in the scheduler.

---

## Release 6, the vivekium brief complete, 2026-09-19

Backend and frontend both `sha-fe791e8`, analysis unchanged. Migrations 0104
and 0105 applied BEFORE the roll, confirmed from the migrate task's own log.

| Artifact | Digest |
|---|---|
| api | `sha256:79334cd2911c3f189e76cc8ceaf8e73a5b4a9d5ec4232b3f211d96277cae03a7` |
| frontend | `sha256:1151a247db7377507783a07037da50a5fd0732175b47fd7180a8427a12e459b6` |
| analysis | `sha256:e0c6d4880b94fe3531a904042ff932ab42e3caeb19b88c03dd9b58c0fc037ec1` |

### What shipped, closing the brief

- **Feature 1, Drishti** (C3): one strategic profile per function per
  functional head, five sections behind the observable-evidence critique
  loop, compiled deterministically, feeding weights ONLY through
  layers.resolve under the restored company term. Enhancement layer:
  absent, every matrix freezes byte-identically, pinned.
- **Feature 2** (C2): resume-aware pre-fill with the 40-question ceiling.
  Both signals required, exercises never pre-filled, the transcript labels
  every pre-filled exchange, billing and completion unchanged chokepoints.
- **C8 retirement**: the tenant-owned verification system is gone from live
  source with its sweep test; the table stays as history; the checkbox form
  serves at the retired system's own URL path.

### Verified

Suite green after the gate's five findings were fixed (the removal sweeps
catching their own history being quoted, an os shadow in the logging fix,
and two pins learning the restored company_layer2 term). All three digests
against RUNNING tasks. Smoke ten for ten. Live probes: Drishti 401
unauthenticated, the new form serving, the RETIRED form 404 in production,
webhook still 503 pending its secret, and the worker invoked live on the
new image.

## Release 7, the Vivekium rebrand and five fixes, 2026-09-20

`a03e978`, pilot (ap-south-2). Backend `sha-a03e978`
(`sha256:bf3ae8db...0773fd14`), frontend `sha-a03e978`
(`sha256:08a46ad4...29cd6b56`), analysis unchanged at `93ebfcb`. Lambdas on
`sha-a03e978-fn`. No migration: `run-migration.sh` exited 0 with nothing to
apply, so a rollback needs no data restore.

**Rollback point: the tag `pre-vivekium-20260920`**, which carries all three
image digests in its message. It has to, and this is the part worth copying:
production was running a backend from `fe791e8` and a frontend from `fe1be7a`,
two different commits, so "check out the tag and redeploy" would have been a
wrong instruction. The digest triple is the rollback; the tag is a pointer to
it.

### What shipped

The SWOT false 409, session hardening, the Role Intake removal, the Vivekium
rename and a typography pass. The standing rules are in `claude.md` under
"the Vivekium release (2026-09-20)". The one finding worth repeating here is
that the 409 was not a locking bug at all: three routes mutated an audit row
after `audit()` had flushed it, and on the generate route the resulting
`UPDATE audit_log` failed at COMMIT, after the 200 had been sent, silently
rolling the write back.

### The gate found two regressions that targeted runs could not

The full suite came back `5 failed, 6661 passed` AFTER every targeted run in
the release was green. Three failures were one cause (Bodha's identity map
still naming the deleted `swot_intake`), two were the new
`/auth/password-changed` route being undeclared in both authorization sweeps.
Neither belongs to the task that introduced the file it failed in, and no
per-task test selection would have collected them. **Run the whole suite
before a deploy, and read the count**: after the dead-service deletion it went
6666 to 6653, and the 13 are exactly the parametrisations over two deleted
prompts and one deleted task type. A drop nobody can account for is a
deletion nobody noticed.

### Live verification, and what it cost

Every running task was confirmed by image digest. `verify-deployment.sh`
refused the first attempt because the variable is `EXPECTED_BACKEND_DIGEST`
and not `EXPECTED_API_DIGEST`, which is the script behaving exactly as its
header promises.

The SWOT fix was then proven against the reported job on production: a
first-time save returned 200 and the version really moved (it had been
answering 409), a second save with the refreshed token returned 200, and a
deliberately stale token still returned 409 with the server's own sentence.

**That verification wrote to a real record, and the residue is permanent.**
The job's four quadrants were blanked afterwards, so the panel reads "Nothing
captured yet" as it did before, but `human_edited` latches by design and the
version is now 3 rather than 0. No SWOT content was fabricated to run the
test: text beside a hiring decision that nobody wrote is worse than a blank,
so the markers said what they were. A live write test on a customer record
needs the owner's consent before, not an apology after.

### Open

A real designed logomark. The placeholder below is geometry, not identity.

## Release 7a, the mark stopped spelling the old name, 2026-09-20

`7466081`, frontend only, `sha-7466081`
(`sha256:6ad8304f...705d8605`). Backend and analysis unmoved, so the rollback
pair for this one is that digest against `sha-a03e978`
(`sha256:08a46ad4...29cd6b56`).

Release 7 shipped the rename with the PREVIOUS LOGO still in place.
`brand-mark-2026.png` was the old artwork cropped, so an R+P monogram sat
beside the word Vivekium in every header, and the three icon rasters were the
same image, which put the old initials in the browser tab, the bookmark bar
and the iOS home screen. Rename 01 covers all screens and glyphs that spell
ReadyPick are an instance of the old name, so this is the narrow correction
rather than a new identity: a two-stroke V, navy then teal, drawn inline and
generated for the icons through next/og. Four rasters deleted, no binary
added. A real designed mark is still the owner's to commission.

**Two failures this would have shipped, both caught before the build.**
`/icon` and `/apple-icon` are generated routes with no file extension, so
`proxy.ts`'s matcher would have answered a browser's first favicon request
with a 307 to /login, which is exactly how `robots.txt`, `sitemap.xml` and
`opengraph-image` each shipped broken while every local test passed. And the
JSON-LD Organization logo named `public/icon.png`, which this change deletes;
its own comment says a logo pointing at a 404 is worse than no logo, so it
moved with the file rather than after it.

Verified on production: both icon routes answer 200 image/png with
`num_redirects=0`, the old raster paths 404, the link tags and structured
data carry the new urls, and the header mark is an inline SVG with no raster
left in the document.

## Release 8, the soft delete and the hard constraint, 2026-09-21

`ce438cf`, backend and frontend both rebuilt, both `sha-ce438cf`
(backend `sha256:6193da68...5162c84`, frontend `sha256:fb856e28...fb350b68`,
Lambda sibling `sha-ce438cf-fn`). Analysis unmoved at `93ebfcb`
(`sha256:e0c6d488...fc037ec1`), and that digest is supplied to
`verify-deployment.sh` rather than omitted, because a skipped check is not a
passed check. The rollback pair is `sha-a03e978` for the backend and
`sha-7466081` for the frontend.

**Found by a customer, not by the suite.** A hiring manager cleared the six
generated Must-have items on the AI architect job, pasted their own five, and
got "API error 500" twice, forty-five seconds apart. CloudWatch named it in
one line: `UniqueViolationError` on `uq_job_competency_name`. Deletion here is
SOFT (`is_active = False`, because a generated candidate question may
reference the row) and the constraint has no predicate, so a removed name
still held its slot invisibly and adding it back was an INSERT onto a live
key. THE BULK ROUTE HAD NO TEST OF ANY KIND, which is how it shipped.

The row is revived instead, a name already present is returned untouched
rather than discarding the rest of a paste, and a rename onto an occupied name
is a 409 naming it. Second defect, the one that made the screen misleading
rather than merely broken: `_framework_repair_pending` asked only the ACTIVE
rows, so a matrix a human had emptied was indistinguishable from one the
generator never wrote. It said "We are still preparing the evaluation criteria
for this role" and re-enqueued Sutra, which would have restored the items the
reviewer had just deleted.

The matrix editor is chips now (owner ruling): one line to add, "Paste a list"
for many, and the "What this measures" box is gone from both the add and the
edit control. The column and the generated text survive, because a field
disappearing from a form must not be a field being erased from the record.

**Verified on production against a seeded job, not against the source tree.**
Add, delete, re-add the same name: 201, and the revived row came back under
its ORIGINAL id, which is what proves revival rather than re-insertion. A
paste carrying one already-present name added the rest and left the existing
entry's required level alone. No duplicates. Emptying the matrix then reports
"Must-have has no items", never "still preparing". Every running task matches
the digest this build produced, both ALB target groups are healthy, and the
API log carries no 5xx and no exception since the rollout.

**One operational note.** The first apply died with its shell and left a
DynamoDB state lock behind. Nothing had been written: all four task
definitions were still at their old revisions with the old image tags, which
is what made `force-unlock` safe rather than a guess. Check the task
definitions before breaking a lock; the lock tells you who, not how far.
