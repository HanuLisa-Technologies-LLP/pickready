# AGENTS.md

Instructions for Codex and other agents. `CLAUDE.md` carries the same essentials for Claude Code; if they ever differ, `CLAUDE.md` and [docs/decisions/REGISTER.md](docs/decisions/REGISTER.md) win. Keep this file short; detail lives in `docs/`.

Vivekium (formerly ReadyPick) is a multi-tenant recruitment platform: Next.js (`frontend/`), FastAPI (`backend/`), PostgreSQL with pgvector under row-level security, AWS Lambda and Fargate for background work (no Celery). Index of all documentation: [docs/README.md](docs/README.md).

During and after every task, synchronously update all affected authoritative documentation and context records alongside the implementation, keeping them accurate, minimal, consistent, and free of obsolete information.

## Working

- Start narrow: read the file or document the task names; search with targeted `grep` before opening; do not scan the repository; bound command output (`head`, `wc -l`, `git diff --stat`).
- Use Graphify only for unfamiliar architecture, cross-module dependencies, and complex code relationships when its graph is available and current. Use direct search for simple tasks. Never invoke Graphify unnecessarily.
- Run the targeted test while developing and the relevant suite once before finishing. Commands: `./scripts/test.sh`, `./scripts/harness.sh run --tier smoke`. Never run two suites on one database.
- Commit or push only when asked. Conventional Commits. A push to `main` starts the deploy pipeline.
- Notes from earlier sessions are hints; verify against the working tree.

## Rules a change must not break

1. No number reaches a client, no exception; words are made server-side.
2. Permissions are data: `require_capability(...)`, never a role branch; a new capability needs its seeding migration.
3. Every tenant-scoped query uses the RLS-aware session; cross-tenant reads are 404.
4. Slow work is dispatched after the commit (`dispatch_after_commit`); no Celery; every `@task` declares `rls=`.
5. One implementation per concept; no dual code paths.
6. No silent fallbacks; no bare `except`; record every degradation.
7. No em dash in any string.
8. A timestamp is not evidence that work happened; check the table.
9. Candidates are assessed against the locked contract snapshot, never live skill rows.
10. Half a change is not a change: register new prompt builders in `test_ctc_never_in_prompt.py`, document new settings in `.env.example`, ship a sweep test with every removal.

## Documentation and decisions

- Precedence when sources disagree: the latest dated owner statement in [docs/decisions/REGISTER.md](docs/decisions/REGISTER.md); then the RBAC spec as amended; the Runbook; `docs/spec/`; implementation documents. Code shows what exists, not what was intended.
- Unanswered questions ([docs/decisions/OPEN.md](docs/decisions/OPEN.md)) are never approved. A decided but unbuilt feature is "decided, pending"; never describe it as working.
- Obsolete material goes to `docs/history/` as a brief record; no dead text elsewhere. Policy: [docs/engineering/documentation-policy.md](docs/engineering/documentation-policy.md).
- Do not touch: applied migrations, `infra/` applies, production secrets, `.claude/settings.json` hooks. Never write a credential into a file.

## Domain rules (open the one you need)

[backend](docs/engineering/backend.md), [database](docs/engineering/database.md), [security](docs/engineering/security.md), [AI runtime](docs/engineering/ai-runtime.md), [frontend](docs/engineering/frontend.md), [testing](docs/engineering/testing.md), [infrastructure](docs/engineering/infrastructure.md), [where to change what](docs/engineering/where-to-change.md), [agent workflow and handoff](docs/engineering/agent-workflow.md). Nested `AGENTS.md` files in `backend/` and `frontend/` point to the matching rules.
