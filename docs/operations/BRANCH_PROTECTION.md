# Branch protection on `main`
**Status:** current, **applied**. **Measured:** 2026-10-14 by `gh api repos/HanuLisa-Technologies-LLP/pickready/branches/main/protection` (read-only).

`main` is the only deployable branch (owner ruling, CONTRACT v7, 2026-09-25). A pull request into `main` runs the checks below and deploys nothing; a push to `main` starts the deploy pipeline.

## Live settings

| Setting | Value | Why |
|---|---|---|
| Pull request required | on | Every change to the deploy branch is a reviewable diff with CI on it. |
| Required approving reviews | 0 | One maintainer; the PR and its checks are the gate. Raise it when a second reviewer exists. |
| Required checks | the ten below, strict (branch up to date) | Checks ran against the merge result. |
| Conversation resolution | required | Open review threads block the merge. |
| Force pushes, deletions | off | A force push rewrites the history a deployed digest was built from. |
| Enforce for administrators | off | The owner can still revert a bad merge or repair a broken workflow file. |
| Linear history, signed commits | off | Not asked for; merges use merge commits. |

## Required checks

Each is a job in `.github/workflows/deploy.yml`. Renaming a job makes the old required name wait for ever: rename the job and the required check in the same change.

| Check name (exactly as GitHub reports it) | Source app |
|---|---|
| `Backend tests and agent evaluation` | github-actions (15368) |
| `Harness (smoke, regression, integration, adversarial, safety)` | github-actions (15368) |
| `Golden end-to-end journey` | github-actions (15368) |
| `Frontend tests, design gate and build` | github-actions (15368) |
| `Analysis service tests` | github-actions (15368) |
| `Typing, structure and vendor honesty` | github-actions (15368) |
| `Security scan` | github-actions (15368) |
| `Terraform gates` | github-actions (15368) |
| `The production approval gate exists` | github-actions (15368); skipped on a pull request, which satisfies it |
| `Trivy` | github-advanced-security (57789); the SARIF upload inside the security scan |

`Security scan` fails on any fixable CRITICAL or HIGH finding, so a newly published advisory against an existing dependency blocks every pull request until it is fixed. It is not skipped to unblock a merge.

## Reading it back

```bash
gh api repos/HanuLisa-Technologies-LLP/pickready/branches/main/protection \
  --jq '{checks: [.required_status_checks.checks[].context],
         strict: .required_status_checks.strict,
         reviews: .required_pull_request_reviews.required_approving_review_count,
         admins: .enforce_admins.enabled,
         force: .allow_force_pushes.enabled,
         delete: .allow_deletions.enabled,
         threads: .required_conversation_resolution.enabled}'
```

`verify-approval-gate` reads the repository's environments through `APPROVAL_GATE_TOKEN` (`administration` is not a GitHub Actions permission); branch protection does not change that job.
