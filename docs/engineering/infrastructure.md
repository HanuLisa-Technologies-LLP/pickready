# Infrastructure and deployment rules

**Status:** current. **Reviewed:** 2026-10-14. Loaded on demand by `.claude/rules/infrastructure.md`.
What runs where: [../operations/INFRA_TOPOLOGY.md](../operations/INFRA_TOPOLOGY.md). Runbooks: [../operations/DEPLOY_AWS.md](../operations/DEPLOY_AWS.md), [../operations/DISASTER_RECOVERY.md](../operations/DISASTER_RECOVERY.md). Workspace hygiene: [../development-storage-policy.md](../development-storage-policy.md).

## Environments and branches

- `readypick-pilot` (`ap-south-2`, Hyderabad) is the only environment ever applied and it is the live site, so there is nowhere to rehearse. `infra/environments/staging` and `production` are defined, never applied; `production` is derived from `staging` by `infra/environments/derive-production.py` (every edit must match exactly once; review `git diff infra/environments/production`).
- `main` is the only deployable branch. Pilot is built and deployed from a commit on `main`, verified by image digest. Pushing to `main` starts `.github/workflows/deploy.yml`; a pull request into `main` runs checks and deploys nothing. Branch protection is written, not applied (OPEN Q-22).
- The deploy workflow's ref restriction is pinned (`tests/test_deploy_workflow_guard.py`). An environment with no required reviewer runs silently; `verify-approval-gate.sh` fails without one. `administration` is not a valid GitHub Actions permission.

## Terraform

- Never apply from an agent task without explicit instruction. An offline plan (`infra/plan-offline.sh`) proves the configuration is internally consistent; it proves nothing about a real account, quotas or IAM.
- IAM is per service and enumerated, never a prefix or wildcard. Task role and execution role are separate. `iam:PassRole` is held only by the thirty-line trigger function. A `for_each` or `count` keyed on an ARN cannot be planned; key by a literal name.
- Secret containers are not configured secrets. A copy of a credential something else rotates is an outage with a date on it; the application's database credential is its own.
- Data subnets have no route to the internet in either direction. ECR tags are immutable and retained by count. Redis is `noeviction` (it holds a live proctoring warning counter and run status).
- Never delete `*.tfstate`, `terraform.tfvars`, secrets, retained plans, backend config or the lock file. `.terraform/providers` and downloaded modules are reproducible; the shared `TF_PLUGIN_CACHE_DIR` stays.

## Deploying and verifying

- Build natively for arm64 on the builder (`scripts/build-images-remote.sh`; it builds a commit on `main`, derives the tag `sha-<12>`, and refuses a dirty tree or a digest mismatch between `backend:<tag>` and `<tag>-fn`). The laptop QEMU build is the fallback.
- Verify by digest, not by exit code: `scripts/verify-deployment.sh` reads `EXPECTED_BACKEND_DIGEST` and refuses rather than skips when it is absent. Read the running tasks, not the service definition. `aws ecs run-task` returning is not the migration finishing: `scripts/run-migration.sh` polls for STOPPED and reads the exit code.
- Rollback points are tags carrying all image digests, because backend and frontend can be built from different commits.
- `scripts/mint-smoke-token.py` plus `JWT_SECRET` from the environment's secret gives a live bearer token without a password.
- Pilot ships with `transcribe_enabled` and inbound email off until the deploy stage turns them on. Describe neither as working until observed.

## Workspace hygiene (permanent)

C:\pickready is canonical. Worktrees are temporary and need an owner and purpose; never trade tens of GB for agent parallelism without approval. One Compose project, `pickready`; throwaways are named `pickready-tmp-<task>`, labelled `readypick.temporary=true` and removed before the task ends (`scripts/hygiene-check.sh --clean-temp`). Reproducible artifacts (`node_modules`, `.next`, caches, build output, provider downloads, Docker build cache) are not data. Never delete a PostgreSQL, MinIO or user-data volume without verifying and backing it up. End of every substantial task: confirm work is preserved, remove finished worktrees, clear safe artifacts, remove temporary containers, confirm the canonical stack config, report in two lines what was cleaned.
