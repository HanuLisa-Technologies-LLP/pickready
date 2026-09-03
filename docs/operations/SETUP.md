# ReadyPick CI/CD, setup instructions

**The full runbook is [docs/operations/DEPLOY_AWS.md](DEPLOY_AWS.md).** This file is
the short version, plus the things that are not obvious.

> **NO LIVE AWS DEPLOYMENT HAS BEEN EXECUTED**, and in this phase that is a
> requirement rather than an omission. spec-doc5 §D.1 asks for a codebase and
> pipeline that are complete and correct but not run, and its acceptance list is
> explicit: *"running `terraform apply` against production in this phase is a
> failure of scope, not an accomplishment."*
>
> Everything below is written to be followed and has not been followed.

**What this file replaced.** It previously documented a Cloud Run staged
rollout: `infra/gcp/deploy.sh`, Workload Identity Federation, `--set-secrets`
versus `--set-env-vars`, revision promotion and staged tag cleanup. That
infrastructure was removed (spec-doc5 §D.2) and every one of those sections
described something that no longer exists. Rather than leave a document that
reads as current and is not, it was rewritten — and the two pieces of it that
were still load-bearing were carried forward rather than deleted, at the bottom
of this page.

---

## The pipeline

```
PR                              main
 |                               |
 lint / typecheck                same
 backend tests + 3 evals         same
 frontend tests + design gate    same
 security scan (trivy)           same
 terraform validate              same
 |                               |
 terraform plan (staging)        build -> ECR, tagged by commit SHA
 posted to the PR                terraform apply (staging), automatic
                                 migrate (one-shot ECS task, WAITS)
                                 smoke test
                                 verify by DIGEST, not by exit code
                                 |
                                 terraform plan (production), read-only role
                                 |
                                 [HUMAN APPROVAL GATE]
                                 |
                                 terraform apply (production)  <- OUT OF SCOPE
```

Everything from `build -> ECR` down is additionally disabled behind
`vars.AWS_DEPLOY_ENABLED`, which is unset. The steps are written, wired and
reviewable; a person has to set a repository variable before any of them touches
an AWS account.

---

## Files

| Path | What it is |
|---|---|
| `infra/modules/` | Seven independently-plannable Terraform modules |
| `infra/environments/staging`, `production` | The two roots. Two directories rather than one root with a workspace: a `terraform.workspace` conditional means one plan describes two environments, and the blast radius of running the wrong one is production. |
| `infra/environments/derive-production.py` | Derives the production root from staging, so the two cannot drift in **shape** — only in the values that are supposed to differ. |
| `infra/validate.sh` | Offline validation of every module and both roots |
| `.github/workflows/deploy.yml` | The whole pipeline |
| `scripts/run-migration.sh` | One-shot ECS task, and it waits for the exit code |
| `scripts/smoke-test.sh` | Does it answer HTTP |
| `scripts/verify-deployment.sh` | Are the running bytes the tested bytes |
| `scripts/verify-approval-gate.sh` | Does the gate actually exist |

---

## One-time setup

Four steps, none of which can be Terraform. Full commands in
[docs/operations/DEPLOY_AWS.md](DEPLOY_AWS.md).

1. **The state bucket**, versioned. Terraform cannot create the bucket that
   holds its own state.
2. **The GitHub OIDC provider and four roles.** No AWS access key is stored in
   this repository.
3. **The `production` environment's required reviewer.**
4. **Secret values**, by hand. Terraform creates the containers and never
   writes a value.

---

## Things that are not obvious

### A skipped check reads almost like a passing one

When `scripts/deploy.sh` was deleted, six assertions in
`tests/test_deploy_secret_hygiene.py` began reporting `SKIPPED` with "deploy
script is not present in this checkout". In a summary line that is one word
away from `PASSED`, and it meant nothing was enforcing secret hygiene any more.

They were rewritten against the Terraform and the workflow, and the guarantee is
now stronger than it was: the check is no longer *"the deploy script does not
print the DSN"* but *"the worker's IAM policy does not include the Firebase key
at all"* — a property of the infrastructure rather than of one script's care.

### `DATABASE_URL` and `REDIS_URL` are MOUNTS now, and this reverses the old note

The previous version of this file explained at length why they had to be plain
environment variables: Cloud Run rejects a deploy where one name appears in both
`--set-env-vars` and `--set-secrets`, so a name could be one or the other and
not both.

**ECS has no such conflict.** A container definition's `environment` and
`secrets` are separate lists and a name in `secrets` is simply injected. So both
are mounts, and `tests/test_deploy_secret_hygiene.py` asserts that no credential
appears in `common_environment`.

`REDIS_URL` moved from "not a secret" to "a secret" in the same change, and for
a real reason rather than for consistency: on ElastiCache with transit
encryption it carries an AUTH token, where on the old platform it was a host and
a port.

### The migration script waits, and that is the whole script

`aws ecs run-task` returns as soon as the task is **accepted**. Treating that as
success is exactly the failure this project has already had: a management job
that found 30 files, died at a 900-second ceiling having written nothing, and
was reported as a green step. `run-migration.sh` polls for `STOPPED`, reads the
container's exit code, and fails on anything but zero.

### Verify by digest, not by exit code

A green apply proves Terraform finished. `verify-deployment.sh` reads the
**running tasks'** image digests and compares them to what was built — because
the gap between "the service definition points at the new image" and "the tasks
are running it" is exactly what a circuit-breaker rollback looks like from the
outside, and asking the service would report success for it.

### The approval gate is checked, not assumed

A job declaring `environment: production` against an environment with no
required reviewer runs **without a gate**, silently. The workflow file still
reads as gated. `verify-approval-gate.sh` fails the run when the reviewer is
missing, which is the only way "we have an approval gate" is a fact rather than
a belief.

### `beat` is exactly one task, and zero during a deploy

Its service sets `deployment_minimum_healthy_percent = 0`, so the old scheduler
stops before the new one starts. Two schedulers running at once would double
every scheduled task — which here means two reconciliation sweeps and two sets
of reminder emails. The `ecs` module's `services` variable has a validation
block that refuses a beat service with more than one task, so this cannot be
undone by editing a number.

### Fargate does not scale to zero

The one place it is not equivalent to Cloud Run. There is a floor cost the
previous platform did not have (~$150/month staging, ~$700 production). Stated
here rather than discovered on a bill.

### The frontend reaches the backend through a same-origin proxy

Unchanged, and it is why `NEXT_PUBLIC_API_URL` stays unset. Every browser call
goes to `/api/v1/...` on the frontend's own origin and
`frontend/app/api/[...path]/route.ts` forwards it server-side, so the auth
cookie stays same-site and `COOKIE_SAMESITE` can remain `strict`.

---

## Rollback

```bash
terraform apply -var="image_tag=sha-<previous commit>"
```

ECR tags are **immutable**, so a SHA tag is a permanent name for a specific set
of bytes. The lifecycle policy retains tagged images by **count**, never by age:
an age rule deletes the image a long-running service needs to restart from, and
the failure surfaces at 3am on the image that had been working for months.

A schema migration does not roll back with the image. Every migration here is
written to be additive under a rolling deploy; `0058` is the documented
exception and says so in its own docstring.
