# Branch protection on `main`

`main` is the only deployable branch (owner ruling, CONTRACT v7, 2026-09-25;
audit Part 2 P0). Nothing in the repository enforces that today:
`GET repos/HanuLisa-Technologies-LLP/pickready/branches/main/protection`
answers 404, so a direct push, a force push or a branch deletion is one
command away and would reach the pilot deploy lane unreviewed.

**This has NOT been applied.** The account the release tooling runs as
(`udarshmarthala`) holds READ on the repository, and branch protection needs
ADMIN. It is also a repository security setting, which only the owner applies.
The settings below are exact; the owner (or any repository admin) runs the
command once.

## The settings, and why each one is what it is

| Setting | Value | Why |
|---|---|---|
| Require a pull request before merging | on | Every change to the deploy branch is a reviewable diff with CI on it. |
| Required approving reviews | **0** | There is one maintainer. A required approval from somebody else would block every merge; the PR and its checks are the gate. Raise it the day a second reviewer exists. |
| Dismiss stale reviews | **off** | Single maintainer: nothing to dismiss. |
| Require status checks to pass | on, strict (branch up to date) | The checks below ran against the merge result, not an older base. |
| Allow force pushes | **off** | A force push rewrites the history a deployed digest was built from. |
| Allow deletions | **off** | Deleting `main` deletes the deploy branch. |
| Enforce for administrators | **off** | So the owner can still recover (revert a bad merge, repair a broken workflow file) without first dismantling the protection. |
| Require linear history, signed commits, conversation resolution | off | Not asked for; the release merges with merge commits. |

## The required checks

Read from the last run on `main` (`CI and AWS deploy`, run 36327537114, head
`5f104c0`, every one `success`). Each is a job in
`.github/workflows/deploy.yml` with no `if:` that excludes `pull_request`, so
each one reports on a pull request into `main`; a required check that never
runs on a pull request would block every merge for ever.

| Check name (exactly as GitHub reports it) | Source app | Job |
|---|---|---|
| `Backend tests and agent evaluation` | github-actions (15368) | `backend-tests` |
| `Harness (smoke, regression, integration, adversarial, safety)` | github-actions (15368) | `harness` |
| `Golden end-to-end journey` | github-actions (15368) | `golden-journey` |
| `Frontend tests, design gate and build` | github-actions (15368) | `frontend-checks` |
| `Typing, structure and vendor honesty` | github-actions (15368) | `code-quality` |
| `Security scan` | github-actions (15368) | `security-scan` |
| `Terraform gates` | github-actions (15368) | `terraform` |
| `Analysis service tests` | github-actions (15368) | `analysis-service-tests` |
| `Trivy` | github-advanced-security (57789) | the SARIF upload inside `security-scan` |

Deliberately NOT required: `The production approval gate exists`
(`verify-approval-gate`, `if: github.event_name != 'pull_request'`, so it
never reports on a pull request), and every build, deploy and
`terraform plan/apply` job (they are skipped unless the deploy switches are on,
and they run after the merge, not before it).

A check name is part of this contract: renaming a job in `deploy.yml` makes
the old required name wait for ever. Rename the job and the required check in
the same change.

## The command

Run from a checkout, as a repository admin (bash):

```bash
gh api --method PUT \
  -H "Accept: application/vnd.github+json" \
  repos/HanuLisa-Technologies-LLP/pickready/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "Backend tests and agent evaluation", "app_id": 15368},
      {"context": "Harness (smoke, regression, integration, adversarial, safety)", "app_id": 15368},
      {"context": "Golden end-to-end journey", "app_id": 15368},
      {"context": "Frontend tests, design gate and build", "app_id": 15368},
      {"context": "Typing, structure and vendor honesty", "app_id": 15368},
      {"context": "Security scan", "app_id": 15368},
      {"context": "Terraform gates", "app_id": 15368},
      {"context": "Analysis service tests", "app_id": 15368},
      {"context": "Trivy", "app_id": 57789}
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false,
  "required_conversation_resolution": false
}
JSON
```

## Verifying it

```bash
gh api repos/HanuLisa-Technologies-LLP/pickready/branches/main/protection \
  --jq '{checks: [.required_status_checks.checks[].context],
         strict: .required_status_checks.strict,
         reviews: .required_pull_request_reviews.required_approving_review_count,
         stale: .required_pull_request_reviews.dismiss_stale_reviews,
         admins: .enforce_admins.enabled,
         force: .allow_force_pushes.enabled,
         delete: .allow_deletions.enabled}'
```

Expected: the nine names above, `strict: true`, `reviews: 0`, `stale: false`,
`admins: false`, `force: false`, `delete: false`. Then open a throwaway pull
request into `main` and confirm the merge button waits on the nine checks.

## Interaction with the approval gate check

`verify-approval-gate` reads the repository's environments through
`APPROVAL_GATE_TOKEN` (CLAUDE.md, 2026-09-23: `administration` is not a
GitHub Actions permission). Branch protection does not change that job and
does not need that token.
