# History

**Status:** index of provenance. **Nothing under this folder is an instruction or a description of how the product works today.** Do not open a file here unless a current document cites it or you are investigating why something is the way it is.

Every file is a short record: what it was, when, why it is obsolete, what replaced it, and how to retrieve the original. Full originals live in Git; the last commit before the 2026-10-14 restructure is `73103c3`:

```bash
git show 73103c3:docs/spec/CODE_EXECUTION.md      # any removed file, by its old path
git log --oneline -- docs/spec/CODE_EXECUTION.md    # when it changed
```

## Records

| File | Covers |
|---|---|
| [RULINGS.md](RULINGS.md) | The old 367 KB `CLAUDE.md` as a dated ruling log, the reversals and their reasons, and a crosswalk from each old section to its current home |
| [SUPERSEDED.md](SUPERSEDED.md) | Designs and specifications replaced by later decisions: the sandbox coding design, the AI upgrade brief, the sprint brief, the architecture direction, the Tatva matrix, retired pricing, old operational notes, removed PRD and ESD parts |
| [AUDITS.md](AUDITS.md) | Audits, reconciliations, security, performance and readiness reports, with what became of each finding |
| [RELEASES.md](RELEASES.md) | The deployment log, the Vivekium release working notes and the 2026-08 build log |
| [LEGACY_RESET_SURVEY.md](LEGACY_RESET_SURVEY.md) | Brief record kept at the path the reset script writes to |
| `diagnostics/`, `baseline/` | Binary and raw evidence only: screenshots, one PDF, a raw SQL audit, the two original specdoc4 `.docx` baselines |

## Moved or removed paths

Code comments and older documents cite these paths. Use this table to find where the content went.

| Old path | Now |
|---|---|
| `CLAUDE.md` sections by date, `claude.md` | [RULINGS.md](RULINGS.md) crosswalk; current rules in [../../CLAUDE.md](../../CLAUDE.md) and [../engineering/](../engineering/) |
| `DEPLOYMENT_LOG.md` (root) | [RELEASES.md](RELEASES.md) |
| `docs/spec/CODE_EXECUTION.md`, `docs/operations/JUDGE0_RUNBOOK.md` | [SUPERSEDED.md](SUPERSEDED.md) section A; [../spec/CODING_ASSESSMENT.md](../spec/CODING_ASSESSMENT.md) |
| `docs/spec/AI_RUNTIME_UPGRADE.md` (`ai-upgrade-spec-doc.md`) | [SUPERSEDED.md](SUPERSEDED.md) section B |
| `docs/spec/VIVEKIUM_SPRINT_FEATURES.md` | [SUPERSEDED.md](SUPERSEDED.md) section C |
| `docs/spec/ARCHITECTURE_DIRECTION_2026-08-28.md` | [SUPERSEDED.md](SUPERSEDED.md) section D |
| `docs/operations/DATABASE_CREDENTIAL_MIGRATION.md`, `PICKREADY_APP_ADDRESSES.md`, `TEST_BASELINE.md` | [SUPERSEDED.md](SUPERSEDED.md) section G |
| `docs/architecture/ENGINEERING_AUDIT_2026-09-11.md` | [AUDITS.md](AUDITS.md) |
| `docs/verification/SECURITY_REPORT.md`, `PERFORMANCE_REPORT.md`, `SEO_REPORT.md`, `PRODUCTION_READINESS.md`, `PROCTORING_AND_FORMATS_VERIFICATION.md` | [AUDITS.md](AUDITS.md) |
| `docs/release/2026-09-vivekium/s4-api-shapes.md` | [../reference/JOB_SETUP_API.md](../reference/JOB_SETUP_API.md) |
| `docs/release/2026-09-vivekium/*` (everything else) | [RELEASES.md](RELEASES.md) |
| `docs/history/CONTRADICTIONS.md`, `GAP_MATRIX*.md`, `PHASE0_FINDINGS.md`, `RUNBOOK_*.md`, `COMBINED_AUDIT_2026-09-23.md`, `diagnostics/*.md` | [AUDITS.md](AUDITS.md) |
| `docs/history/build-log.md` | [RELEASES.md](RELEASES.md) |
| `.claude/CLAUDE.md` | Folded into the root [../../CLAUDE.md](../../CLAUDE.md) |

## Writing a record

Follow [../engineering/documentation-policy.md](../engineering/documentation-policy.md): a few lines per item, no instructions, no copied bodies, always the retrieval command. A superseded design that still explains something visible in code gets a record; a statement that was simply wrong or is fully captured by Git history does not.
