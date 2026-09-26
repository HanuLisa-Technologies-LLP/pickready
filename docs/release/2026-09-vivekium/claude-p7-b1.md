# CLAUDE.md draft, PLAN-p7 WP-B1: every task declares how it reaches data

No migration. `app/workers/registry.py`, `app/workers/runtime.py`, the `@task`
decorators in every `app/workers/*.py` module, four task bodies' session
lines, and `tests/test_worker_tenant_session.py`.

## EVERY `@task` DECLARES ITS RLS SCOPE, AND THE DECLARATION IS CHECKED AGAINST THE BODY

Until this release every background task opened `worker_session`, which sets
`app.bypass_rls = 'on'` for the whole run. A task dispatched for ONE customer
could read and write every customer's rows and only its own WHERE clauses
stood between them, so rule 3 ("the Postgres policy is the boundary, the WHERE
clause is defence in depth") had never been true for background work.

- **`rls` is a REQUIRED keyword on `@task`, with no default.** A registration
  that does not say `"tenant"` or `"bypass"` is refused at import. A
  `"bypass"` registration must carry `rls_reason` in words, and a `"tenant"`
  one must not (only the escape hatch needs a justification). An undeclared
  escape hatch is indistinguishable from an accidental one.
- **The declaration is BINDING, not decorative.**
  `tests/test_worker_tenant_session.py` reads every registered body: a
  `"tenant"` task that opens `worker_session`, or a `"bypass"` task that opens
  `tenant_worker_session`, fails. `CONVERTED` pins the tenant set by name, so a
  conversion (or a revert) is a reviewed change to that set.
- **`TaskSpec.rls` defaults to None only so a hand-built spec in a runtime test
  need not invent a scope.** None reads as "undeclared" and the registry sweep
  fails on it; it never reads as bypass.

## `tenant_worker_session(tenant_id)` SCOPES EVERY CONNECTION AT STARTUP, AND THAT IS CORRECT ONLY HERE

- A fresh engine per run (the Lambda-freeze reason `worker_session` gives),
  disposed on exit.
- The scope is asyncpg `server_settings`, a CONNECTION STARTUP PARAMETER:
  `role` = `postgres_rls_app_role` (validated by `core.db._app_role`, the
  API's own rule), `app.tenant_id` = the tenant, `app.bypass_rls` = `off`.
  A startup parameter is the session DEFAULT, so a commit, a rollback in the
  body or even `RESET ALL` returns to it rather than shedding it.
- **NOT statements run once after connecting, and the first draft of this
  package did exactly that.** A task commits several times (claim, send,
  settle), each commit hands the connection back to the engine's pool, and
  `pool_pre_ping` silently REPLACES one that died in between. `SET ROLE` and
  `set_config` issued on the first connection are absent on the replacement,
  so under a login role that owns the tables (dev, the test database) the rest
  of the run saw every tenant. `test_a_replacement_connection_carries_the_same_scope`
  kills the backend between two commits and reads back from the replacement;
  mutation-checked against the statement version, which answers as the
  superuser.
- **Session-scoped rather than `SET LOCAL`** because the engine is private to
  the run (no shared pool can carry the setting to another tenant) and a
  transaction-local tenant would be gone after the first commit. Do NOT copy
  this into anything that shares a pool; `core.db.tenant_scope` stays
  transaction-local for exactly that reason.
- **The test database logs in as a superuser**, so without the `role`
  parameter the policy would not bind at all and every isolation test would
  pass by construction. The mutation check that removes it fails five tests.

## `resolve_tenant_id(kind, id)` READS ONE COLUMN FROM AN ALLOWLISTED TABLE

A task dispatched with an entity id must learn the tenant before it can open
the tenant session, and under RLS it cannot read the row to find out. So
`resolve_tenant_id` is a one-query BYPASS read of `tenant_id` alone, by
primary key, from `TENANT_OWNER_TABLES` (`link`, `job`, `email_log`,
`purchase`, `support_thread`). An allowlist keyed by a Literal, because the
table name is interpolated into SQL. Each table's `tenant_id` is NOT NULL,
asserted against `information_schema`. None means the row does not exist and
the caller answers exactly as its body already answered a missing row.

## WHAT WAS CONVERTED, AND WHY MOST OF THE PLAN'S LIST WAS NOT

The plan's rule governs: convert ONLY when a test runs the body against real
Postgres under the RLS role and it writes the same rows as under bypass, read
back from a second connection. Four tasks pass it and run under the tenant
session: `send_lifecycle_email` (resolved from `email_log`, including its
retries-exhausted settle), `send_payment_failed_email`,
`send_credit_warning_email`, `send_credit_invoice_email` (resolved from
`credit_purchases`). A fifth test points tenant B's session at tenant A's
queued row and gets "log row not found" with nothing sent: the policy doing
the work, not a WHERE clause.

**Four tasks the plan listed stay `bypass`, each for a reason found in the
code, not assumed:**

- `send_application_confirmation`, `send_assessment_reminder`,
  `generate_proctoring_report` read the CANDIDATE row. `candidates_databank`
  shows a tenant only `tenant_id IS NULL` rows and its own, while
  `matching._databank_profiles` links a consenting candidate uploaded by
  ANOTHER tenant. Under a tenant session that candidate is invisible, so the
  confirmation or reminder would skip as "no recipient" and the proctoring
  report would lose the name, silently.
- `notify_support_message`: a customer's message is routed to Vivekium staff,
  whose `users` rows carry no tenant.

Everything else is `bypass` with its reason in the decorator: every sweep
("one run reads every tenant's rows"), every task reading `profiles`,
`candidates` or `candidate_employments`, the sandbox probes, platform
`send_email`, and five one-tenant tasks not yet proven
(`generate_job_swot`, `draft_job_skills`, `execute_coding_submission`,
`transcribe_voice_answer`, `process_assessment_video`), which are the next
conversion candidates and say so.

## Supersessions

- **2026-09-05 "Background work without Celery"**: `worker_session`'s
  ASSUMPTION that "background tasks are trusted backend processes that
  legitimately operate across tenants" is SUPERSEDED. Bypass is now a per-task
  declaration with a written reason, and a one-tenant task that has been
  proven runs under the policy.
- **Section 7 table, "A background task" row**: `@task(name=..., route=...,
  rls=...)`; a new task decides its RLS scope at registration, and a tenant
  task needs a real-Postgres read-back test beside it.
