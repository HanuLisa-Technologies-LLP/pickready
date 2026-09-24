## Current hard rules, the harness, the scripts and the docs on the Skills step (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 1, work package E
(harness, scripts, docs). No migration. The services and routes it drives are
work packages B and C (`claude-p1-b.md`, `claude-p1-c.md`); the flow itself is
written down once, in `docs/spec/JOB_SETUP_FLOW.md`.

### THE HARNESS DRIVES THE SKILLS STEP, AND ITS WORLDS ARE THE STATES THE PRODUCT WRITES

**SUPERSEDES** the 2026-09-21 and 2026-09-20 scenarios' matrix wording: the
four matrix scenarios now drive `/api/v2/assessments/jobs/{id}/skills`, and
`job_with_generated_matrix` is gone.

- **Three worlds, each a state the product reaches**: `job_with_drafted_skills`
  (Sutra drafted from a SAVED SWOT, nothing saved), `job_with_team_written_skills`
  (the team typed skills before any draft, so the draft state is
  `not_started` while rows exist: the exact state audit number 8 lives in) and
  `job_with_saved_skills` (published, saved, ready; the applicant, invitation,
  assessment and ranked-pool worlds stand on it). **No world seeds the
  seven-stage columns** (`dimension`, `weight`, `threshold_json`, ...):
  nothing on the Skills path writes them, and a world standing on a state no
  job created today can reach measures the seed, not the product.
- **A world with skills always has a saved SWOT.** The sweep skips an unsaved
  SWOT before it reads a row, so a sweep scenario without one would pass for
  the wrong reason.
- **The sweep runs for real, scoped by RLS, not by a copied query.**
  `run_the_setup_sweep` runs `pickready.reconcile_job_setup`'s body with the
  worker session swapped for the world's `tenant_scope` session. The query is
  unchanged; RLS limits what it can see. A platform-wide sweep over a SHARED
  database would mark every other suite's jobs `drafting`, which is the damage
  `world.teardown` refuses to do by never truncating.
- **A model that must ANSWER is served at the vendor seam, never patched.**
  `faults.model_answers(answer_for)` serves the authored success envelope with
  only `choices[0].message.content` replaced (a third derivation beside
  `malformed` and `partial`), so the real router, the real contract check and
  Sutra's real validator run. It is deliberately NOT in `faults._REGISTRY`: a
  success is not a degradation, and a scenario declaring it under `faults:`
  would be judged by `degradation_honesty`. It lives in `faults.py` because
  that module owns the one shared patch of `httpx.AsyncClient`; a second owner
  would capture this one's replacement as "the prior state". The harness
  credential exists only inside the block, so `a_model_credential_was_configured`
  still reads the real, empty setting.
- **New scenario, `regression.a_writer_outage_saves_nothing_and_names_no_skill`**:
  a 503 fault from the authored fixture; Save Skills answers 503, the sentence
  names no skill, nothing is written, and the step records the degradation
  with the server's own sentence.
- **Mutation-checked, each seen to fail and restored**: the sweep asking for
  ACTIVE rows only (the emptied-set scenario fails on the state, the dispatch
  count and the fact); an `UPDATE audit_log` after the save's INSERT (the
  audit-row scenario fails: `permission denied for table audit_log`); an
  outage sentence that blames the first skill (the outage scenario fails).

### A SCRIPT THAT MARKS SKILLS SAVED GOES THROUGH `skills.save`, OR WRITES THE HONEST EMPTY CONTEXT

Three scripts stamped "saved" beside the product. Each was wrong in a way the
product would never be:

- **`seed_demo_applications` stamped `framework_approved_at` and an empty
  context with no model call and no audit row.** It now presses Save Skills
  through `skills.save`, demo tenants only, as the tenant's active Super Admin
  (`ck_audit_log_agent_has_principal` needs a person behind Sutra's row), ONE
  TRANSACTION PER JOB, re-entering `superadmin_scope` each time (it is
  transaction-local, the 2026-09-16 probing mistake). A job with no skills, an
  incomplete set, a lock or a writer outage is reported and left pending with
  nothing written.
- **`backfill_functional_reports` stamped every job BEFORE looking for skills**,
  so a demo job with none read as ready for candidates. It now stamps only
  after `skills.validate_for_save` passes, never on a locked job, and derives
  the status through `skills.refresh_setup_status`.
- **`seed_mock_data`'s final UPDATE made every job ready, with or without
  skills.** It now saves only jobs with active skills, seeds each bucket to
  `skills.MAX_PER_BUCKET` and never past it, writes `authored_by='human'` (no
  model drafted these) and a per-bucket `force_rank`, and its publish stamp
  moves `lifecycle_state` to PUBLISHED with `ratified_at`.
- The two shortcut scripts write **the honest empty context migration 0118
  wrote**, `{"role_summary": "", "generated_by": "<the script>"}`: no model
  ran, and a summary claiming one had is template output presented as
  generation (rule 6).

**`backfill_assessment_context.py` (new) is how that empty context gets
filled**: DRY RUN by default; `--apply --operator-email` re-saves each saved,
unlocked, unarchived job whose context Sutra did not write, through
`skills.save`, one transaction per job. **The audit rows name the platform
operator who ran it**, never the job's original saver or the tenant's Super
Admin, who did not make that save. A locked job is never offered (its
contract is the snapshot). Exit 1 when any job was refused, so a partial run
never reads as clean; exit 2 when the operator is not an active platform
super admin.

**A rollback expires every ORM instance.** Both new transaction-per-job
scripts carry ids, never rows, across transactions; the first draft of the
backfill carried the operator `User` row and raised `DetachedInstanceError`
on the first save, which its own test caught.

### `legacy_reset` KNOWS ABOUT THE SKILLS STEP AND THE SNAPSHOT

- The jobs reset also clears `assessment_context_json` and returns the draft
  state to `not_started`, so a purged job reads unsaved and
  `reconcile_job_setup` drafts it again from its saved SWOT.
  `matching_categories_finalized_at` stays listed, as history.
- **`job_skill_snapshots` is classified PRESERVE.** It is immutable by trigger
  and it is the only record of what started candidates were assessed against.
  The consequence is deliberate and counted by the survey
  (`locked_contracts`): a locked job's skill rows are purged, its contract
  stays locked, and new criteria mean a new job.
- Both recorded schema snapshots (`tests/fixtures/legacy_reset`,
  `tests/fixtures/reembed`) were amended BY HAND for 0118, the 0088
  precedent, because a classified table the snapshot does not list reads as a
  database behind the migrations. They remain stale by design otherwise.

### `level` HAS NO SCRIPT READER LEFT

`backfill_catalog_jds` shades a posting's seniority from the EXPERIENCE BAND
(Junior when the band tops out at two years, Senior when it starts at six);
`backfill_job_descriptions` stops passing `level` into the JD brief. Both left
`test_job_level_removed`'s pending list. **`app/scripts/reembed.py`'s
`_JD_TEXT_SQL` still has `CASE WHEN j.level ...`**: it mirrors
`matching._jd_text` and must move WITH it (Phase 2), or the stored vector and
the ranker's text disagree. The AST sweep cannot see SQL text.

### DOCS

`docs/spec/JOB_SETUP_FLOW.md` is normative for job setup: the flow, each
step's route, capability, writes and refusals, the tasks and the sweep rule,
where every rule is pinned (pytest and harness), the operator scripts, and the
open owner questions. `HIRING_WORKFLOW.md` Gates 3 and 4 are marked
SUPERSEDED in place, pointing at it.
