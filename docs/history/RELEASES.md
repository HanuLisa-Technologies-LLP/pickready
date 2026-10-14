# Releases and deployments, 2026-08 to 2026-09

**Status:** history record. Not an instruction. For how deployment works today read [../operations/DEPLOY_AWS.md](../operations/DEPLOY_AWS.md) and [../engineering/infrastructure.md](../engineering/infrastructure.md). Originals: `git show 73103c3:<path>`.

## Deployment log (`DEPLOYMENT_LOG.md`, repository root, 93 KB)

An append-only catch-up artifact for `readypick-pilot` (account `016617990245`, region `ap-south-2`, repository `HanuLisa-Technologies-LLP/pickready`). Each entry recorded what changed in the code, what was created in AWS, decisions made under ambiguity, and what waited on a human. It is no longer maintained (last entry 2026-09-23). Durable facts it held moved to the register and the operations documents.

| Release (date) | What shipped | Lesson that outlived it |
|---|---|---|
| 2026-09-06 | Add-features release: corporate senders, dual-mode assessment, video access, retention consents, BGV, employer pages, 18 dashboards (migrations 0080 to 0085) | Google sign-in was dead for two stacked reasons: the apex origin was not authorised in Firebase, and a secret container held `PLACEHOLDER_NOT_CONFIGURED` |
| 2026-09-09 | Company DNA removed; AI runtime upgrade | A bad grep invented a 19,000-line workstream |
| 2026-09-10 | Native support, RDS to `db.t4g.medium`, runtime completions | An RDS proxy pins every session here and was refused |
| 2026-09-11 | Engineering-audit close-out | none |
| 2026-09-12 | BGV and conversations live; the application's own database credential | AWS rotated a copied master password seven days after creation and the product went down |
| 2026-09-16 | Permission-aware UX, occupational STEM, AI Job SWOT | none |
| 2026-09-18 | Production hardening, candidate erasure, the repair of "the week-long outage" (the dashboard refresh task failed every five minutes after the 2026-09-11 credential split, because the application role did not own the materialized view), consent lifecycle | A credential change can silently break a scheduled task; the proof of repair was a timestamp of the first successful run |
| 2026-09-19 to 2026-09-21 | The Vivekium build and rebrand, the mark, the soft-delete defect | Generated routes with no extension must be named in `proxy.ts` |
| 2026-09-23 | Change requests 07 to 28D and the harness; migrations 0106 to 0117; deployed as `sha-d63f88937e05` | Rollback point api `:46`, frontend `:33`, analysis `:14` on `sha-ce438cf` |

The 2026-09-25 to 2026-09-30 releases (the Vivekium simplification release, CONTRACT v10, company onboarding and auth hardening, monthly plans; migrations 0118 to 0136) are recorded in [RULINGS.md](RULINGS.md) and the specs, not in a log.

## Vivekium release working notes (`docs/release/2026-09-vivekium/`)

Package notes written by parallel work packages for the orchestrator to integrate: an audit close-out (every audit item closed, resolved by deletion, or open), CLAUDE.md section drafts (S4 audit close, S4 flow frontend half, landing page, typography), a deferred-hunks register with its patches (all landed except two optional items), and the API contract page `s4-api-shapes.md`.

- The close-out's open items are in [../decisions/OPEN.md](../decisions/OPEN.md).
- The API contract page was kept: it is now [../reference/JOB_SETUP_API.md](../reference/JOB_SETUP_API.md) (old path `docs/release/2026-09-vivekium/s4-api-shapes.md`, cited by comments in `frontend/`).
- Patches (`hunks/*.patch`, `p5-c-orchestrator-hunks.patch`) were integration scaffolding; the code they describe is in the tree.
- Gitignored working files `.claude/vivekium-release/` (CONTRACT.md v1 to v10, per-phase plans, handoff notes) are local and not part of the repository.

## Build log (`docs/history/build-log.md`, 2026-08)

A product-and-technical diary, newest first, for the fixes of 2026-08-09 to 2026-08-12 (resume preview redirect, AI Reach and an AI dashboard that was later deleted, an invitation link that forced sign-in, the platform audit scanning zero files locally, rate limits on public endpoints). Superseded by git history and the register.
