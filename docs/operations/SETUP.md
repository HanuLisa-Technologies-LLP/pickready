# CI/CD setup: the short version

**Status:** current. **Reviewed:** 2026-10-14. The full runbook is [DEPLOY_AWS.md](DEPLOY_AWS.md); this page is the file map, the one-time setup and the three things that are not obvious. The workflow itself is `.github/workflows/deploy.yml`; read its header before changing the pipeline. A push to `main` starts it; a pull request into `main` runs the checks and deploys nothing ([../engineering/infrastructure.md](../engineering/infrastructure.md)).

## Files

| Path | What it is |
|---|---|
| `infra/modules/` | Independently plannable Terraform modules, one directory each |
| `infra/environments/pilot` | The live composition. `staging` and `production` are defined and never applied |
| `infra/environments/derive-production.py` | Derives the production root from staging so the two cannot drift in shape, only in the values that should differ. Every edit must match exactly once; review `git diff infra/environments/production` |
| `infra/validate.sh`, `infra/plan-offline.sh` | Offline validation and an offline plan (proves internal consistency only) |
| `.github/workflows/deploy.yml` | The whole pipeline |
| `scripts/build-images-remote.sh` | Native arm64 image build from a commit on `main` |
| `scripts/run-migration.sh` | One-shot ECS task; waits for the exit code |
| `scripts/smoke-test.sh` | Does it answer HTTP |
| `scripts/verify-deployment.sh` | Are the running bytes the tested bytes (reads `EXPECTED_BACKEND_DIGEST`; refuses rather than skips when absent) |
| `scripts/verify-approval-gate.sh` | Does the production approval gate actually exist |

## One-time setup

Four steps, none of which can be Terraform. Commands are in [DEPLOY_AWS.md](DEPLOY_AWS.md).

1. **The state bucket**, versioned. Terraform cannot create the bucket that holds its own state.
2. **The GitHub OIDC provider and the roles.** No AWS access key is stored in this repository.
3. **The `production` environment's required reviewer.**
4. **Secret values**, by hand. Terraform creates the containers and never writes a value.

## Things that are not obvious

- **The migration script waits, and that is the whole script.** `aws ecs run-task` returns when the task is accepted, not when it finishes. `run-migration.sh` polls for `STOPPED`, reads the container's exit code, and fails on anything but zero.
- **Verify by digest, not by exit code.** A green apply proves Terraform finished. `verify-deployment.sh` reads the running tasks' image digests and compares them to what was built, because a circuit-breaker rollback looks like success from the service definition.
- **The approval gate is checked, not assumed.** A job declaring `environment: production` against an environment with no required reviewer runs without a gate, silently. `verify-approval-gate.sh` fails the run when the reviewer is missing.
- **`DATABASE_URL` and `REDIS_URL` are secret mounts**, never plain environment variables; `tests/test_deploy_secret_hygiene.py` asserts no credential appears in `common_environment`. `REDIS_URL` carries an AUTH token on ElastiCache with transit encryption.
- **The frontend reaches the backend through a same-origin proxy** (`frontend/app/api/[...path]/route.ts`), which is why `NEXT_PUBLIC_API_URL` stays unset and the auth cookie stays same-site (`COOKIE_SAMESITE=strict`).
- **Fargate does not scale to zero.** There is a floor cost, stated in [DEPLOY_AWS.md](DEPLOY_AWS.md) section 8.

## Rollback

```bash
terraform apply -var="image_tag=sha-<previous commit>"
```

ECR tags are immutable, so a SHA tag names specific bytes; images are retained by count, never by age. A schema migration does not roll back with the image: migrations are additive under a rolling deploy (`0058` is the documented exception). Restoring data is [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md).
