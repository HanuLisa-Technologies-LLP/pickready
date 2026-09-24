# Job setup: JD, SWOT, Skills, publish, and the lock

**Status:** normative for the Vivekium release (PLAN-p1, owner decisions D1 and
D5). It replaces the Tatva matrix editor, the Matching Categories editor and
the job approval chain, and it supersedes Gates 3 and 4 of
[HIRING_WORKFLOW.md](HIRING_WORKFLOW.md) (marked in place there). Where this
page and a CLAUDE.md section disagree, the newer CLAUDE.md section wins; where
this page and the code disagree, the code is the fact and this page is the bug.

## 1. The flow

```
Create Job (a DRAFT, always)
  -> JD document: PATCH /jobs/{id}/jd                    one edit path
  -> SWOT: generate (202, dispatched) / edit / SAVE
       first human save with no skill row of any kind:
       Sutra's skills draft is dispatched AFTER the commit
  -> Skills step: add / paste / rename / move / remove   per-bucket capability
       at most five per bucket; any edit un-saves the set
  -> Save Skills: ONE Sutra context call BEFORE any write, then one
       transaction: evidence lines, priorities, role summary, saved stamp,
       lifecycle DRAFT -> FINALIZED, two audit rows
  -> Publish: JD + saved SWOT + saved skills, named in one sentence;
       lifecycle PUBLISHED; matching and the JD index dispatched AFTER commit
  -> first candidate start: lock_contract writes the immutable snapshot;
       skills and grade are read-only from here
```

Every "is it done" question is DERIVED from the tables, never from a stamp
(rule 8):

| Question | Asked of | Code |
|---|---|---|
| Is the SWOT saved? | the SWOT row: content, `human_edited`, status `edited` | `swot_analysis.is_saved` |
| Are the skills saved? | `jobs.framework_approved_at` AND `jobs.assessment_context_json` | `assessment_contract.skills_saved` |
| Are the skills locked? | a `job_skill_snapshots` row exists for the job | `assessment_contract.is_locked` |
| Ready for candidates? | skills saved | `skills.refresh_setup_status` writes `assessment_status` |
| May it publish? | all three steps, every missing one named | `api/jobs._publication_blocked` |

`framework_approved_at` keeps its name and now means "skills saved at", the
same trade the `ppi` identifiers make: a persisted name is not renamed for
copy.

## 2. Each step

### 2.1 Create Job

`POST /api/v1/jobs` saves a DRAFT. `jd_markdown` is required and may not be
headings only; the per-section `jd` and `level` inputs are gone. `publish:
true` is refused with a 422 naming the separate publish step, loud during a
rolling deploy rather than a silently mislabelled draft. A Recruiter or Hiring
Manager who creates a job is assigned to it (`rbac.assign_creator`), because
every SCOPED cell of RBAC 24 reads `job_assignments` and nothing else wrote it.
Nothing is dispatched.

`POST /api/v1/jobs/generate-jd` runs the credit gate and the Company Profile
gate BEFORE the writer is called, with the create route's own sentences.

### 2.2 The SWOT (Bodha)

`/api/v2/assessments/jobs/{id}/swot-analysis`: read, generate, save, restore.

- **Generate answers 202** and dispatches `pickready.generate_job_swot` after
  the commit. The deterministic sufficiency gate (`swot_input_state`, a JD body
  long enough to analyse) refuses BEFORE anything is dispatched. A generation
  that never reports back READS as failed after
  `SWOT_GENERATION_STALE_MINUTES`; nothing writes that state.
- **A human save wins a race with a generation.** The model runs without the
  row lock, and a save that landed meanwhile is kept.
- **The first human save or restore, on a job with no skill row of any kind,
  asks Sutra for a draft** (`skills.after_swot_saved`). Every later save only
  OFFERS a redraft (`skills_redraft_available`); a SWOT edit never rewrites
  the skills.
- **A SWOT edit is not refused by the lock.** The contract a candidate is
  assessed against is the snapshot, whatever happens to the document it was
  drafted from.

### 2.3 The Skills step (Sutra drafts, the team decides)

`/api/v2/assessments/jobs/{id}/skills`: read, add, paste (`/bulk`), edit
(`PATCH /{skill_id}`: rename, move, or both), remove, draft, save.

- **Sutra's draft** (`pickready.draft_job_skills`, task type `skills_drafting`)
  writes at most five skills per bucket, at least one Must-have and one
  Behavioural, from the JD's required skills and the SAVED SWOT. A SWOT quote
  is kept only when it is a verbatim substring of the saved SWOT. A failed
  draft is the `failed` state with ZERO rows: there is no template draft.
- **Authorization is per bucket** (`capabilities.SKILL_BUCKET_CAPABILITY`):
  the target bucket for an add or a paste, the row's bucket for a rename or a
  remove, BOTH buckets for a move, all three for a draft, and
  `finalize_role_definition` plus all three for a save. A Recruiter cannot
  edit a Must-have (RBAC 24's NEVER cell). `can_edit` per bucket and
  `can_save` are served on the payload by the same `rbac.authorize` calls the
  writes enforce with.
- **Soft delete under a hard unique key.** `uq_job_competency_name` has no
  predicate, so a removed name still holds its slot. Re-adding it REVIVES the
  row (keeping the SWOT sentence it came from); a rename onto any occupant,
  visible or not, is a 409 naming the name; a move onto a removed row revives
  that row. A rename CLEARS the SWOT sentence and marks the row the team's,
  because a rename is a different skill wearing the row's identity.
- **Limits are all or nothing.** A paste that would take a bucket past five is
  refused whole, naming how many fit. Nothing is written.
- **Any edit un-saves the set** and moves the job back to not ready; a
  published job keeps taking applications and cannot invite until re-saved.

### 2.4 Save Skills

`POST /api/v2/assessments/jobs/{id}/skills/save` (`skills.save`). The one
recorded exception to rule 4: the context must land in the same transaction as
the human's save, bounded by the interactive tier (`assessment_context`).

1. Refuse if locked; refuse with EVERY problem if the set cannot be saved
   (422).
2. ONE `sutra.build_context` call for exactly these skills. An outage is a 503
   whose sentence names NO skill and changes nothing; a skill the writer could
   not describe observably is a 422 naming EVERY such skill.
3. Take the skills lock, re-check the lock, and compare the rows with what was
   sent: an edit that landed during the call is a 409, never lost silently.
4. Write the evidence lines, per-bucket priorities, the hidden context (role
   summary, model, prompt version, digest), the saved stamp, the lifecycle
   (DRAFT to FINALIZED, never backwards) and two audit rows, EACH IN ONE
   INSERT (the 2026-09-20 rule).

### 2.5 Publish

`POST /api/v1/jobs/{id}/publish` is the only way a job goes live. It names
every missing step in one sentence ("Before this job can be published, ..."),
writes PUBLISHED, and dispatches `pickready.run_matching` and
`pickready.index_document` with `dispatch_after_commit`, so a publish that
rolls back starts nothing.

### 2.6 The lock and the snapshot

The first candidate start calls `assessment_contract.lock_contract`, which
writes an immutable `job_skill_snapshots` row (a trigger refuses UPDATE, and
DELETE unless a tenant or job cascade drives it) and binds the conversation to
it. From then on every skills write answers 409 `SKILLS_LOCKED_DETAIL` (a lock
is a STATE, never "Not permitted"), and a grade change is a 409 too: the grade
decides every candidate's question budget and is part of the snapshot's
digest.

## 3. What runs in the background

| Task | Route | Trigger | What it may never do |
|---|---|---|---|
| `pickready.generate_job_swot` | Lambda | SWOT generate, after commit | overwrite a human save made while it ran |
| `pickready.draft_job_skills` | Lambda | first SWOT save, a confirmed redraft, the sweep | write a template, or replace the team's skills without confirmation |
| `pickready.reconcile_job_setup` | Lambda, every 15 min | schedule | select a job whose rows the team emptied |
| `pickready.remind_unsaved_skills` | Lambda, every 60 min | schedule | mail anybody who cannot save (recipients by capability) |

**The sweep selects a job only when its SWOT is SAVED and either no draft was
ever asked for AND it has ZERO rows of ANY kind, or a draft was asked for and
never reported back** (`skills.DRAFT_STALE_AFTER`). A set a person emptied is
a decision, not a missing draft (audit number 8).

## 4. Where each rule is pinned

| Rule | Pytest | Harness scenario |
|---|---|---|
| An emptied set is never redrafted, by the read or the sweep | `test_reconcile_job_setup.py` | `regression.an_emptied_skill_set_is_not_redrafted` |
| A removed skill is revived, not re-inserted | `test_job_skills_service.py`, `test_job_skills_api.py` | `regression.a_removed_skill_can_be_added_back` |
| A rename onto a hidden occupant is a 409 naming it | `test_job_skills_service.py` | `regression.a_rename_onto_a_hidden_occupant_is_a_409` |
| Save's audit rows survive their commit | `test_job_skills_save.py` | `regression.an_audit_row_survives_its_own_commit` |
| A writer outage names no skill and saves nothing | `test_job_skills_save.py` | `regression.a_writer_outage_saves_nothing_and_names_no_skill` |
| The draft is dispatched after commit, verbatim quotes only | `test_job_skills_draft.py` | |
| The publish gate names every step; dispatch after commit | `test_job_publish_gate.py` | |
| The lock refuses skills writes with 409 | `test_job_skills_api.py`, `test_grade_lock.py` | |
| The contract snapshot is immutable | `test_assessment_contract.py` | |
| The matrix editor and the approval chain stay deleted | `test_tatva_matrix_editor_removed.py`, `test_job_approval_chain_removed.py` | |

The harness worlds that stand for these states are `job_with_drafted_skills`,
`job_with_team_written_skills` and `job_with_saved_skills`
(`backend/harness/world.py`). Save Skills' writer answers in the harness from
the authored success envelope at the vendor seam (`faults.model_answers`),
never from a patched function.

## 5. Operator scripts

- `python -m app.scripts.skills_overflow_report`: every bucket over the limit.
  Read only. Migration 0118 logged these and truncated nothing; an unlocked
  job over the limit is refused at its next save with the count.
- `python -m app.scripts.backfill_assessment_context`: DRY RUN by default.
  Lists saved, unlocked jobs whose hidden context Sutra did not write (0118's
  honest empty context, the seeds'). `--apply --operator-email` re-saves each
  through `skills.save`, one transaction per job, naming the platform operator
  on the audit rows. A locked job is never offered. Exit 1 when any job was
  refused.
- `python -m app.scripts.seed_demo_applications`: saves demo jobs' drafted
  skills through `skills.save` (demo tenants only, as the tenant's Super
  Admin), then seeds applications.
- `python -m app.scripts.legacy_reset`: its jobs reset clears the saved stamp,
  the hidden context and the draft state (so the sweep can draft again from the
  saved SWOT); `job_skill_snapshots` is PRESERVED, so a locked job stays
  locked and its survey counts them.

## 6. Open owner questions

- **HR Manager publish.** RBAC data is unchanged, and the HR Manager used to
  publish only through Create Job's flag, which is gone, so an HR Manager can
  no longer publish.
- **Assigning a Hiring Manager.** There is no assignment screen, so only a
  Hiring Manager who created a job holds skill-edit scope on it.
- **Scoring reads the contract.** Until Phases 3 and 5 move the scoring path
  onto `assessment_contract`, a job saved through the Skills step is not
  scoreable; Phases 1, 3 and 5 ship in one deploy.
