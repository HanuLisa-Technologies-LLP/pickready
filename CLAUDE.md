# CLAUDE.md

Vivekium (formerly ReadyPick; Varpitech LLP) is a multi-tenant recruitment platform: Next.js frontend, FastAPI backend, Firebase identity, PostgreSQL with pgvector under row-level security, work dispatched to AWS Lambda and Fargate (no Celery). A candidate applies, is ranked against saved Skills, sits one proctored, server-timed assessment, and yields an immutable PRISM Report graded in words, never numbers.

This file is short on purpose. It holds only what you cannot infer from the code. Everything else is one link away: [docs/README.md](docs/README.md) is the index. `AGENTS.md` carries the same essentials for Codex.

## Documentation discipline

During and after every task, synchronously update all affected authoritative documentation and context records alongside the implementation, keeping them accurate, minimal, consistent, and free of obsolete information.

- Obsolete material goes to `docs/history/` as a brief record. No dead or deprecated text elsewhere.
- Precedence when sources disagree: the latest dated owner statement in [docs/decisions/REGISTER.md](docs/decisions/REGISTER.md), then the RBAC spec (as amended), the Runbook, `docs/spec/`, implementation documents. Code is evidence of what exists, not of what was intended. Details: [docs/engineering/documentation-policy.md](docs/engineering/documentation-policy.md).
- A question the owner has not answered is never approved: see [docs/decisions/OPEN.md](docs/decisions/OPEN.md). A decided but unbuilt feature is labelled "decided, pending" and is never described as working.
- Ask the owner only when blocked on a decision that is theirs. Explain the current behaviour, the problem, a real example, what each option causes, and your recommendation.

## How to work

- **Start narrow.** Read the file or document the task names. Search with targeted `grep` or Glob/Grep before opening; do not scan the repository. Keep command output bounded (`head`, `tail`, `wc -l`, `--stat`). Do not re-read a file you just edited.
- Use Graphify only for unfamiliar architecture, cross-module dependencies, and complex code relationships when its graph is available and current. Use direct search for simple tasks. Never invoke Graphify unnecessarily.
- Run the targeted test while developing and the relevant suite once before finishing. Finishing means the named checks ran and passed, and anything left undone is said aloud.
- Subagents only for genuinely independent work; never two on overlapping files; never beside a container build or a second test run on the same database.
- Anything a past session wrote (memory, summary, handoff) is a hint; verify against the working tree. Handoff and compaction: [docs/engineering/agent-workflow.md](docs/engineering/agent-workflow.md).
- Commits are Conventional Commits. Commit or push only when asked. `main` is the deploy branch; a push to it starts the pipeline.

## Rules a change must not break

1. **No number reaches a client, no exception.** Scores are internal; words are made server-side.
2. **Permissions are data, never a role branch.** `require_capability(...)`; a capability constant is half a change, the seeding migration is the other half.
3. **Every tenant-scoped query goes through the RLS-aware session.** Cross-tenant reads are 404.
4. **Slow work is dispatched**, never inline in a handler, and after the commit: `dispatch_after_commit`. No Celery. Every `@task` declares `rls=`.
5. **One implementation per concept.** No dual code paths.
6. **No silent fallbacks.** No bare `except`; a degradation is recorded; template output is never presented as generation.
7. **No em dash** in any string, including seeded and generated content.
8. **A timestamp is not evidence that work happened.** Check the table.
9. **Candidates are assessed and graded against the locked contract snapshot**, never live skill rows.
10. **Half a change is not a change.** A new prompt builder is registered in `test_ctc_never_in_prompt.py`; a new setting is in `.env.example`; a capability ships with its seeding; a removal ships with a sweep test.

## Do not touch without the owner

Applied migrations; insert-only tables' triggers; `infra/` applies; production secrets; `.claude/settings.json` hooks (removal is a pending owner-approved change); anything marked "decided, pending" (build it only when asked). Never write a credential into a file. Never claim a live vendor call, deploy or test succeeded without evidence in `docs/verification/`.

## Where to find things

| Need | Read |
|---|---|
| What to change where | [docs/engineering/where-to-change.md](docs/engineering/where-to-change.md) |
| Backend, database, security, AI, frontend, testing, infrastructure rules | [docs/engineering/](docs/engineering/) (path-scoped digests load from `.claude/rules/`) |
| Decisions, owed changes, open questions | [docs/decisions/](docs/decisions/) |
| What the product does | [docs/product/PRD.md](docs/product/PRD.md), [docs/spec/](docs/spec/) |
| How it is built | [docs/architecture/ESD.md](docs/architecture/ESD.md) |
| Run and deploy | [docs/operations/](docs/operations/), [CONTRIBUTING.md](CONTRIBUTING.md) |
| Design system | [DESIGN.md](DESIGN.md), [PRODUCT.md](PRODUCT.md) |
| Why something is the way it is | [docs/history/](docs/history/): provenance, never instructions |

Code comments cite old `CLAUDE.md` sections by date; [docs/history/README.md](docs/history/README.md) maps each to its new home.

## Commands

```bash
./scripts/test.sh                       # canonical backend suite (Docker, fresh DB)
./scripts/test.sh unit --keep -- -k name -x   # one test, keep the stack
./scripts/harness.sh run --tier smoke   # end-to-end harness
docker compose -f infra/docker-compose.yml up --build   # local stack; frontend :3000, backend :8000/docs
docker compose exec backend alembic upgrade head
```

Python runs locally on Windows; tests use Docker for Postgres, Redis and MinIO. Never run two suites on one database.

## Compact instructions

When compacting, keep the task and its acceptance check, files changed and why, exact verification commands, dated owner statements and register IDs, unresolved questions, and the branch and `HEAD` sha. Drop file contents and search output.
