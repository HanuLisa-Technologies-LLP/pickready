## Current hard rules, the skills contract and the one ledger writer (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Foundation stage, part
B. Migration `0118_skills_contract` (revision id to be re-chained at
integration; `down_revision` is `0117_consent_versioning`).

### THE CONTRACT IS ONE READ API, AND LOCKED MEANS A ROW EXISTS

`app/services/assessment_contract.py` is the only way anything reads what a
job's candidates are assessed against: `load_contract(db, job_id)`,
`load_contract_for_conversation(db, conversation_id)`, `lock_contract(db,
job_id, conversation_id)`, `is_locked`, `require_unlocked`, `skills_saved`,
`log_digest`. An `AssessmentContract` carries `job_id, version, locked,
locked_at, grade, skills, role_summary, digest`; a `ContractSkill` carries
`id, name, bucket, priority, evidence_line`.

- **Live until the first START, a snapshot forever after (D5).** Before any
  candidate starts, the contract is the active `job_competencies` rows plus
  `jobs.assessment_context_json` and `jobs.assessment_grade`, reported as
  `version=0, locked=False`. `lock_contract` writes `job_skill_snapshots`
  version N and binds the conversation (`skill_snapshot_id` and
  `contract_digest`). **"Locked" is the EXISTENCE of a snapshot row**, never a
  timestamp (rule 8). The grade is inside the snapshot and the digest, so the
  grade is locked with the skills.
- **A conversation reads ITS snapshot, never "the job's latest".**
  `load_contract_for_conversation` answers from the bound snapshot and RAISES
  when the conversation's stored digest disagrees with it
  (`ContractIntegrityError`) or when a STARTED conversation has no binding
  (`ContractNotBound`). Falling back to the live rows would grade a candidate
  against skills that may have changed since they were asked.
- **The digest covers CONTENT only**: skills, role summary, grade, in a total
  canonical order (bucket, priority, name, id). Never the version or the lock
  state, so the live digest at the instant of the lock equals the snapshot's.
- **`log_digest(stage, conversation_id, contract)` is the one line**:
  `assessment_contract.digest stage=%s conversation_id=%s job_id=%s
  version=%d contract_digest=%s`. Vaada logs `stage=vaada` at the start, Miti
  `stage=miti` at grading, and a test compares them. Identifiers and a hash
  only: never a skill name, an evidence line or the role summary. An unknown
  stage raises.
- **The lock is idempotent and serialised.** `locks.advisory_xact_lock(SKILLS,
  job_id)` is the new BLOCKING sibling of `try_advisory_lock`: two candidates
  starting at once and a skills edit racing a start all wait for each other,
  and `uq_job_skill_snapshots_version` is the second line. Never hold it across
  a model call. An unsaved or emptied contract refuses (`ContractNotReady`),
  and the lock writes ONE `audit_log` row through `record_action`
  (`job_skills_locked`, actor role `candidate`, no user, no agent).

### A SNAPSHOT IS INSERT-ONLY, AND THE DATABASE SAYS SO TWICE

`job_skill_snapshots` has UPDATE revoked from `pickready_app` (0014's default
privileges grant it to every new table, so omitting it from a GRANT omits
nothing) AND a trigger, `job_skill_snapshot_is_immutable`, that refuses every
UPDATE and every DELETE except a cascade from its job or tenant
(`pg_trigger_depth() > 1`), so tenant deletion and job purges keep working.
Tenant RLS is ENABLE plus FORCE with the bypass clause. The test that proves
the refusal is the trigger's disables it inside a rolled-back transaction and
watches the owner's UPDATE and DELETE go through, which is a mutation check
that runs on every pass.

### THE SKILLS STORE IS `job_competencies`, REPURPOSED IN PLACE

- **`force_rank` is the per-bucket PRIORITY now, 1 = highest.** Internal,
  never serialised, never edited by the team. 0118 rewrote it IN PLACE for
  every unlocked job; a locked job's rows are history and were not touched,
  its priorities live in the snapshot.
- **`authored_by` (`sutra` | `human`) states who wrote an entry.** It
  SUPERSEDES, for new code, the 2026-09-23 rule that read "the human's entry"
  off an ABSENT `swot_origin`: a Sutra draft may carry no SWOT quotation (a
  JD-sourced skill) and must still not read as the team's own. The rename and
  revive asymmetry on `swot_origin` itself is unchanged.
- **`framework_approved_at` is REUSED as "skills saved at"**, and
  `assessment_contract.skills_saved` additionally requires
  `assessment_context_json`, so a saved job whose hidden context nobody wrote
  cannot be locked silently.
- `jobs` gains the Sutra draft state (`skills_draft_status` and its four
  companions) and `job_swot_analyses` gains `generating` plus
  `generation_requested_at`, for the dispatched flows the job-setup phase
  builds on them.

### A MIGRATION ON A LIVE DATABASE CONVERTS, IT NEVER REBUILDS

- **UPDATE in place, never delete and re-insert.** `candidate_questions`
  cascades from `job_competencies`, and answers hang off questions, so a
  rebuilt matrix deletes what candidates were asked and what they said.
- **NO PUBLISHED JOB IS UNPUBLISHED.** The lifecycle remap runs PUBLISHED
  FIRST: a live job (`ratified_at` or `status = 'ratified'`) that is not
  archived becomes PUBLISHED from any pre-publication state. Only then do the
  retired approval states and NULL go back to DRAFT, on unpublished rows only.
  Pilot's 31 live jobs have no saved skills; they stay live and cannot invite
  until their skills are saved, which is today's state too.
- **A saved matrix stays invitable**: 0118 stamps
  `{"role_summary": "", "generated_by": "migration"}`, an honest empty
  summary rather than an invented one.
- **Over five in a bucket is REPORTED, never truncated.** Logged per job at
  WARNING and printed on demand by `python -m app.scripts.skills_overflow_report`.
- **The creator is assigned.** SUPERSEDES 0061's refusal to backfill
  `job_assignments` from `created_by`: the skills flow reads the SCOPED cells,
  and with the table empty a creator could not finish their own job. Only the
  creator, only in their own role (recruiter or hiring_manager), never a
  disabled account, never beside an active holder.
- **The data steps take an optional `job_ids` scope** for exactly one caller,
  the migration test, which runs them on a shared database; `upgrade()` passes
  None. The test also round-trips `downgrade()` and `upgrade()` inside a
  rolled-back transaction, because DDL is transactional in Postgres.
- **What 0118 deliberately does NOT do**: tighten `ck_jobs_lifecycle_state` to
  six states, or delete the `send_jd_to_hiring_manager` rows from
  `role_permissions`. Each is the database half of a code change (the enum
  members and approval routes, the capability constant) that lands with the
  routes package; doing it first would let a live route write a value the
  CHECK refuses and would fail `test_capability_seed_parity`. A capability and
  its seeding are one change, and so is its removal.

### ONE LEDGER WRITER, CALLED TWICE, WRITING ONCE

`app/services/assessment_pipeline/evidence.record_answer_evidence` is the ONLY
code that files a candidate's answer in Miti's ledger. The conversation calls
it per answer (wired by the assessment phase), so Vaada's contradiction
probing reads a ledger that exists while the candidate is answering; scoring
calls it again through `backfill_answer_evidence`.
`tests/test_answer_evidence_idempotent.py` sweeps `app/` for any other call to
a ledger write.

- **Idempotent under concurrency, not only in sequence.**
  `ledger.record_evidence` returns the existing LIVE row for the same
  (tenant, application, source type, source id, locator), and 0118's partial
  unique index `ux_evidence_items_live_answer` is the INSERT's conflict
  arbiter, so two writers of one message leave one row and the loser reads the
  winner's id. The migration SUPERSEDED any duplicate live answer rows onto the
  earliest one before building the index; nothing was deleted.
- **A repeat is a clean no-op**, not a unique violation absorbed by the
  savepoint: the tests assert no `evidence_not_recorded` line on a repeat, and
  a mutation that removes the lookup and the arbiter fails them.
- **Only a DATABASE failure is absorbed** (`except SQLAlchemyError`, logged
  with the traceback, inside a SAVEPOINT so the caller's transaction survives).
  A `TypeError` is a bug and propagates (the 2026-09-22 rule). The previous
  writer caught `Exception`.
- Locators, never sentences; substance decided by `answer_quality`, the
  scorer's own classifier; evidence first, then the claim, then the
  attachment. Unchanged rules, one owner.
