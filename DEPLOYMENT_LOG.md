# Deployment log

**Account** `016617990245` · **Region** `ap-south-2` (Hyderabad) ·
**Repository** `HanuLisa-Technologies-LLP/pickready` · **Environment** `pilot`

This is the catch-up artifact. It records what changed in the codebase, what was
created in AWS, every decision made under ambiguity, and what is still waiting
on a human. Read section 6 first if you only read one: it is the list of things
that need you.

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
