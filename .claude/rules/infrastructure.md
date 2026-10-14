---
paths:
  - "infra/**"
  - ".github/**"
  - "scripts/**"
  - "**/Dockerfile*"
  - "docker-compose*.yml"
---
# Infrastructure (digest; full rules in docs/engineering/infrastructure.md)

- Pilot is the live site; staging and production are never applied. Do not run `terraform apply` or deploy unless the owner says so.
- `main` is the only deployable branch; a push to it starts the pipeline. Verify a deploy by image digest, not exit code.
- IAM is per service and enumerated; no wildcards. Never delete state, tfvars, secrets, backend config or the lock file.
- A new scheduled sweep needs the Python schedule entry AND the Terraform rule in every environment.
- Never write credentials into a file. One Compose project; clean up temporary worktrees and containers (`docs/development-storage-policy.md`).
- Email target is SES only (register I-20); not built yet.
