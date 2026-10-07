# Development storage and workspace hygiene policy

Permanent policy, adopted 2026-10-07. `claude.md` carries the short form that
every session reads; this file holds the reasons and the commands.

## Why this exists

Before the 2026-10-07 cleanup the machine held:

| What | Size |
|---|---|
| `C:\pickready` | 75.6 GB |
| `.claude\worktrees` (61 stale agent worktrees) | 54.9 GB |
| `.terraform/providers`, duplicated across worktrees and modules | about 16 GB |
| Docker images | 63 GB |
| Docker build cache | 66 GB |

Old worktrees still pointed at `C:\dev\pickready` after the repository moved to
`C:\pickready`, so Git could not check them. Parallel Compose projects
(`pickready`, `readypick-test`, verify and rehearsal containers) outlived the
work that made them. None of it was data. All of it was reproducible.

## Rules

### Git and Claude worktrees

- `C:\pickready` is the canonical repository. Work in the current checkout.
- Create a worktree only when parallel isolated work genuinely needs one. Never
  trade tens of GB of storage for agent parallelism without the user's approval.
- Every worktree has an owner and a purpose and is temporary.
- Before deleting one: check uncommitted files and unique commits
  (`git -C <wt> status --porcelain`, `git log <base>..<branch>`). If the
  worktree's metadata is broken, compare its files against the object database
  instead of assuming it is clean.
- Once its work is committed and merged (or preserved on a branch), remove it
  with `git worktree remove <path>`, then `git worktree prune`. Never `rm -rf`
  a registered worktree first.
- Never leave a finished agent worktree behind.
- Never keep copies of `node_modules`, `.next`, caches, build output or
  Terraform providers inside a worktree.

### Terraform

- Never delete `*.tfstate`, `terraform.tfvars`, secrets, deliberately retained
  plans, backend configuration or `.terraform.lock.hcl`.
- `.terraform/providers`, downloaded modules and similar artifacts are
  reproducible (`terraform init`). Clean them when the work is finished.
- One shared plugin cache, so providers are downloaded once and not once per
  module or worktree. It is configured on this machine at user level
  (`TF_PLUGIN_CACHE_DIR=C:\Users\Saravan Kumar\.terraform.d\plugin-cache`, stored in
  `HKCU\Environment`, so it survives new sessions and reboots). A shell or app
  started before it was set will not see it; open a new one. `terraform init`
  prints "from the shared cache directory" when it works. Without symlink rights
  Windows Terraform copies the provider into each `.terraform`, so the cache saves
  downloads but not disk there; clean `.terraform/providers` when finished. To set
  it up on another machine:

  ```powershell
  New-Item -ItemType Directory -Force "$env:USERPROFILE\.terraform.d\plugin-cache"
  [Environment]::SetEnvironmentVariable("TF_PLUGIN_CACHE_DIR", "$env:USERPROFILE\.terraform.d\plugin-cache", "User")
  ```

### Docker

- There is ONE Compose project, `pickready`, defined by `infra/docker-compose.yml`.
  Many services inside it is correct.
- Do not create persistent parallel projects (`pickready-test`, `readypick-test`,
  verify, rehearse, temporary copies). Reuse the canonical stack where safe. For
  a throwaway, use explicitly temporary resources named `pickready-tmp-<task>`
  and labelled `readypick.temporary=true`, and remove them before the task ends
  (`scripts/hygiene-check.sh --clean-temp` removes exactly the labelled ones).
  An unlabelled temporary resource is a policy violation: it cannot be cleaned
  safely.
- `docker-compose.test.yml` is the one sanctioned second file (`scripts/test.sh`
  drives it). Bring it down with `down -v` after a run.
- Every canonical resource comes from `C:\pickready`. Check with
  `docker ps -a --format '{{.Names}} {{.Label "com.docker.compose.project.working_dir"}}'`.
  Anything labelled with another path (for example `C:\dev\pickready`) is stale.
- Create no new persistent volume unless the architecture needs one.
- Never delete a PostgreSQL, MinIO or user-data volume without verifying and
  backing it up first (tar it from a throwaway container).
- Remove superseded ReadyPick images once their replacement exists. Do not build
  uniquely tagged images that accumulate; reuse a tag or remove the old one.
- Clear safe build cache periodically: `docker builder prune -a -f`.

### Storage hygiene

`node_modules`, `.next`, coverage output, Python, ruff, pytest and mypy caches,
temporary build directories, Terraform downloads and Docker build cache are
reproducible. Keep what local development needs and no more. If temporary
workspace or build usage nears 10 GB beyond normal project dependencies,
investigate before creating another copy.

## End-of-task checklist

After a task is integrated into the canonical codebase:

1. Confirm commits and uncommitted work are preserved.
2. Remove finished temporary worktrees; `git worktree prune`.
3. Remove safe generated, build and cache artifacts.
4. Remove temporary Docker containers, networks and images made for the task.
5. Keep important volumes and data.
6. Confirm the canonical stack still works
   (`docker compose -f infra/docker-compose.yml config -q`, then bring up
   `postgres` and `redis` and check they are healthy).
7. Report in two lines what was cleaned and the approximate space reclaimed.

## Quick audit

```bash
scripts/hygiene-check.sh               # report only
scripts/hygiene-check.sh --clean-temp  # also remove readypick.temporary=true resources
```

It lists worktrees (and prunable ones), Docker disk use, Compose projects,
ReadyPick containers started from another path, and labelled temporary
resources. It never touches unlabelled volumes, worktrees, state or secrets.

Correctness and preservation come before reclaiming space. Never clean blindly.
