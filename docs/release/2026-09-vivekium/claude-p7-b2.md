## Current hard rules, the legacy scrap (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 7 Wave B, WP-B2
(models, migrations, credits). One migration, `0128_legacy_scrap`, written
against `0118_skills_contract` for the orchestrator to re-chain.

### A DROP IS SAFE WHEN IT CAN ONLY EVER DROP NOTHING

**SUPERSEDES IN PART owner ruling S4** ("no irreversible drop of legacy tables
that may hold customer rows"), as CONTRACT v3 already ruled from the pilot
probe. `technical_questions`, `candidate_technical_questions`,
`llm_provider_keys` and `otp_challenges` are DROPPED, and
`jobs.questions_approved_at` with them, each behind a count that RAISES and
aborts the whole upgrade when anything is there. The migration therefore cannot delete a row:
on a database that holds one, it refuses, and deleting that row is an owner
decision rather than a side effect of a deploy.

- **The guard first proves it can SEE the rows.** Two of the tables carry
  FORCE row level security, which binds the table owner too, so a count taken
  by a migration role outside the bypass scope reads ZERO over a full table,
  and zero is exactly the answer that lets the drop proceed.
  `_require_unfiltered_reads` refuses unless the connection is a superuser, a
  BYPASSRLS role, or in `app.bypass_rls = 'on'` (which `alembic/env.py`
  sets). This is the 2026-09-16 probing mistake ("no jobs in pilot", truth
  31) turned into a refusal instead of a lesson.
- **The guards are tested against real SQL, not read.**
  `tests/test_legacy_scrap_removed.py` loads the migration with `op` replaced
  by a recorder and executes the DO blocks it BUILDS on the asyncpg
  connection: a row makes the emptiness guard raise, a stamp makes the
  all-NULL guard raise, and `SET LOCAL ROLE pickready_app` outside the bypass
  scope makes the scope guard raise. Mutation-checked in all three.
- **A test that runs `SET LOCAL` on the DRIVER connection needs the DRIVER's
  transaction.** SQLAlchemy's `begin()` is lazy and never reaches a connection
  used underneath it; outside a transaction `SET LOCAL` and a
  transaction-local `set_config` are discarded at once, so the RLS test first
  ran as the superuser it exists to avoid and passed for the wrong reason.
- **The downgrade recreates the tables exactly**, verified by diffing
  `pg_dump -s` of the four tables at 0118 against a 0128 downgrade.

**KEPT, deliberately:** `job_swot_intakes` (3 pilot rows) and `jobs.level`
(30 pilot rows) as history, and `verification_requests` is Phase 6's drop
(its candidate communications migration, same guard), because one drop per
table is the rule. `agent_learnings` and `agent_actions` keep their tables;
the `AgentAction` mapping is removed together with WP-B5's deletion of the
action ledger that imports it, because removing it first breaks that package.

### A DERIVED CACHE NOTHING READS IS DELETED, NOT MAINTAINED

**SUPERSEDES the 2026-07-28 line "`tenants.credit_deficit` is the one derived
cache, and it exists only so the invitation gate does not re-aggregate the
ledger on every send"** and the 2026-08-05 line "`has_credit_headroom` checks
the demo flag BEFORE summing the balance". The column was written after every
grant and charge and read by nothing; the model comment claiming it drove a
dunning email and a portal banner described neither. `credits._sync_deficit`
and `credits.has_credit_headroom` (the negative-balance gate, no caller left
since Draft v4 moved the start gate to zero) are deleted. The start gates are
`credits.has_positive_balance` and `credits.can_start_assessment`, both demo
exempt first; `BalanceSummary.in_deficit` is computed from the ledger. The
downgrade recomputes the column from `credit_ledger`, so nothing is lost.

### A CAPABILITY'S REMOVAL IS ONE CHANGE WITH ITS SEEDING

`revoke_agent_learnings` (RPN-AI-UP-001 W3.6) guarded the revocation of an
experience memory no route or worker can reach (WP-B5 deletes the package).
The constant, its `Role.client` grant and every `role_permissions` row, global
and per-tenant, go together in 0128, the mirror of "a capability constant is
only half a change". A stale key in `users.permissions_json` needs no rewrite:
`rbac.sanitize_overrides` drops an unknown capability on every read.

### A DEFAULT THAT NAMES A STORE NOBODY USES IS A MISLABEL WAITING FOR A WRITER

`profiles.resume_storage_provider` defaulted to `"gcs"` in Python and
`'cloudinary'` on the SERVER, while every writer stores to S3 and says so. The
plan assumed no server default; the database had one. Both are `"s3"` now
(0128 changes the server default only; no stored label is rewritten, a label
is history), and a test compares the model default to
`resume_storage.STORAGE_PROVIDER`.

### WHAT WENT WITH THE TABLES

The mappings `TechnicalQuestion`, `CandidateTechnicalQuestion`,
`LLMProviderKey`, `OTPChallenge` and `AgentLearning`; the enums `OTPChannel`,
`LLMProvider`, `LLMRoleHint`; `Job.questions_approved_at`,
`Tenant.credit_deficit`. Every reader of a dropped table stopped naming it in
the same change, because a DELETE naming a dropped table fails at runtime:
`erasure.job_closure_erasure` (it deleted `candidate_technical_questions` rows
by raw table name and would have failed every closure purge), the transcript
label resolver, `report_evidence.extract_payloads` /
`persist_skill_evidence` (the `technical_questions` parameter is gone),
`run_functional_assessment`, `seed_mock_data` (a raw UPDATE of the dropped
column), `validate_functional_assessment`, the no-op `_seed_llm_keys`, and the
legacy reset classification with its two schema snapshots.

Swept by `tests/test_legacy_scrap_removed.py` and
`tests/test_multivendor_ai_removed.py`, whitespace-normalised, each with a
two-way pending ratchet naming the files another package still owns.

### Superseded in place (for WP-B9)

- 2026-07-28 subscriptions: the `credit_deficit` derived-cache line.
- 2026-08-05 adaptive interview + demo: "`has_credit_headroom` checks the demo
  flag BEFORE summing the balance" now reads `has_positive_balance`.
- 2026-08-06 per-candidate questions: "The TABLE survives unread" for
  `technical_questions` (dropped by 0128).
- 2026-07-30 PPI: "`questions_approved_at` ... was deliberately not dropped"
  (dropped by 0128).
- 2026-09-09 AI runtime: "An `agent_learnings` row is scoped to ONE tenant"
  stays true of the table; nothing writes it any more.
- General rule 9 ("Use the `llm_provider_keys` table and the router service")
  and section 5's "The `llm_provider_keys` table takes precedence over env":
  the table is gone.
