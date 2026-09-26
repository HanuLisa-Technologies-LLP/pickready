# Legacy tables and columns

Owner ruling S4 (Vivekium simplification release, 2026-09-25): nothing that
may hold a customer row is dropped irreversibly. The pilot probe of
2026-09-24 (CONTRACT v3, owner role, RLS bypassed) narrowed it: a table proven
EMPTY may be dropped by a migration that COUNTS first and RAISES, aborting the
whole upgrade, when anything is there. Everything else the release stopped
reading or writing is kept here as history, with the reason.

Row counts are from that probe unless stated. "Nothing writes it" is enforced
by the removal sweeps named in `claude.md`.

## Dropped, each behind an emptiness guard

| Object | Migration | Pilot rows | Guard |
|---|---|---|---|
| `technical_questions` | 0128 | 0 | count, raise |
| `candidate_technical_questions` | 0128 | 0 | count, raise |
| `llm_provider_keys` | 0128 | 0 | count, raise |
| `otp_challenges` | 0128 | 0 | count, raise |
| `jobs.questions_approved_at` | 0128 | all NULL | all-NULL check, raise |
| `tenants.credit_deficit` | 0128 | derived cache | the downgrade recomputes it from `credit_ledger` |
| `verification_requests` | 0121 | 0 | count, raise |

0128's guards first prove they can SEE the rows: two of the tables carry
FORCE row level security, which binds the owner too, so a count outside the
bypass scope reads zero over a full table. `_require_unfiltered_reads`
refuses unless the connection is a superuser, a BYPASSRLS role, or in
`app.bypass_rls = 'on'`. Each downgrade recreates its tables exactly.

## Kept as history: tables

| Table | Pilot rows | Why it stays | Written by |
|---|---|---|---|
| `job_swot_intakes` | 3 | the reporting authority's intake transcripts (Role Intake, deleted 2026-09-20) | nothing |
| `job_matching_categories` | on 2 jobs | the retired matching categories (D2); old reports rendered them | nothing; the `JobMatchingCategory` model still maps it |
| `job_scorecard_bindings` | as found | when a frozen matrix was bound (renamed from the Company DNA bindings by 0088) | nothing; the contract snapshot replaced it |
| `portable_evidence_items` | not counted | the retired portable layer (change request 23) | nothing |
| `agent_learnings` | not counted | the deleted experience memory | nothing |
| `agent_actions` | not counted | the deleted action ledger | nothing |
| `agent_execution_traces` | not counted | agent traces | nothing since the reasoning runner was deleted (open) |

## Kept as history: columns

| Column | Pilot state | Why it stays |
|---|---|---|
| `jobs.level` | 30 rows set | data present; read and written by nothing |
| `jobs.matching_categories_finalized_at` | as found | the retired category review stamp |
| `job_competencies.dimension`, `evidence_sources`, `assessment_method`, `weight`, `threshold_json`, `disqualifier`, `anchor_key` | as found | the seven-stage derivation; old reports and evaluations reference them |
| `job_candidate_links.match_score`, `match_rationale`, `match_breakdown_json`, `tier`, `prescreen_grade` | 0 links on pilot | the retired matcher's reading; 0122 carried scored rows across as `legacy` Yukti readings |
| `job_candidate_links.hm_access_granted` | 0 links on pilot | lost its only writer with the route scrap; no model attribute any more; dropping it is its own migration |
| `candidate_questions.prefilled_answer`, `prefill_source` | 0 questions on pilot | the retired resume pre-fill |
| `companies.approval_levels_config` | as found | data a customer typed for the deleted approval chain |
| `assessment_conversations.mode` | 0 conversations on pilot | old rows keep their truthful mode; new rows are `conversational` |
| `users.phone`, `users.phone_verified_at` | as found | CONTACT data; phone sign-in is removed |
| `profiles.resume_storage_provider` values other than `s3` | 0 `gs://` rows | a label is history; the server default is `s3` since 0128 |

## Held, not dropped

`LLM_KEY_ENCRYPTION_SECRET` stays in the secrets module's `secret_names`,
granted to no service and mounted nowhere, because it was the only key that
opened `llm_provider_keys`, and deleting a secret is irreversible once its
recovery window passes. It leaves with an owner decision.

## To drop one of the kept objects

Write a migration that counts first and raises naming the count, proves it
reads outside RLS, recreates the object exactly on downgrade, and names the
owner decision in its docstring. Never drop in the same change that stops
reading: the rollback of the code must not need a data restore.
