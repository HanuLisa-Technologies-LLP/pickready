# Job setup: JD, Skills, the Final Job Posting, publish, and the freeze

**Status:** normative for the Vivekium release (PLAN-p1, owner decisions D1 and
D5, AS AMENDED by CONTRACT v10, the owner ruling of 2026-09-28). It replaces
the Tatva matrix editor, the Matching Categories editor and the job approval
chain, and it supersedes Gates 3 and 4 of
[HIRING_WORKFLOW.md](HIRING_WORKFLOW.md) (marked in place there). Where this
page and a CLAUDE.md section disagree, the newer CLAUDE.md section wins; where
this page and the code disagree, the code is the fact and this page is the bug.
The API shapes the frontend builds against are in
`docs/release/2026-09-vivekium/s4-api-shapes.md`.

## 1. The flow (CONTRACT v10)

```
JD ──→ Skills ──→ Final Job Posting ──→ Publish
         └── structured authority for matching + assessment
SWOT ──→ separate internal hiring intelligence, editable independently
First genuine application ──→ FREEZE JD + Skills
```

```
Create Job (a DRAFT, always, with its JD)
  -> Sutra's skills draft is dispatched AFTER the commit (from the JD)
  -> JD document: PATCH /jobs/{id}/jd                    one edit path
       a save while the job has NO skill row of any kind asks for a draft
  -> Skills step: add / paste / rename / move / remove   per-bucket capability
       "Draft skills" asks again (a redraft over the team's own skills
       needs their confirmation); at most five per bucket; any edit
       un-saves the set
  -> Save Skills: ONE Sutra context call BEFORE any write, then one
       transaction: evidence lines, priorities, role summary, saved stamp,
       lifecycle DRAFT -> FINALIZED, two audit rows
  -> Final Job Posting: GET .../posting-preview (JD + skill NAMES by bucket)
  -> Publish: JD + saved skills, named in one sentence;
       lifecycle PUBLISHED; matching and the JD index dispatched AFTER commit
  -> first GENUINE application: freeze_at_application writes the immutable
       snapshot IN the application's transaction; the JD, title, band,
       grade and skills are read-only from here
  -> first candidate start: lock_contract binds the conversation to that
       snapshot (the idempotent backstop)

SWOT (any time, including after the freeze): generate / edit / save.
       It is optional context for Sutra and for Yukti's company-need fit,
       never a publication step, never shown to a candidate, and its save
       drafts nothing (it may only OFFER a redraft while unfrozen).
```

~~Until 2026-09-28 the order was JD, then the SWOT, then Skills: the first
human SWOT save dispatched the draft, publication needed a saved SWOT, and the
skills and grade locked at the first candidate START (D5).~~ **SUPERSEDED
2026-09-28 by CONTRACT v10**, kept struck for the history it records.

Every "is it done" question is DERIVED from the tables, never from a stamp
(rule 8):

| Question | Asked of | Code |
|---|---|---|
| Is the SWOT saved? | the SWOT row: content, `human_edited`, status `edited` | `swot_analysis.is_saved` (context only; never a gate) |
| Are the skills saved? | `jobs.framework_approved_at` AND `jobs.assessment_context_json` | `assessment_contract.skills_saved` |
| Is the job frozen? | a `job_skill_snapshots` row exists for the job | `assessment_contract.is_locked`, `frozen_since` |
| Ready for candidates? | skills saved | `skills.refresh_setup_status` writes `assessment_status` |
| May it publish? | the JD and saved skills, every missing one named | `api/jobs._publication_blocked` |
| May a draft be asked for? | the title and a JD body of at least `SKILLS_DRAFT_MIN_JD_WORDS` | `generation_sufficiency.skills_draft_input_state` |

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
The ONE dispatch is Sutra's skills draft from the JD, after the commit
(`skills.after_jd_saved`); a JD too thin to draft from is recorded as the
draft's `failed` state with the fixed sentence instead (the create itself
succeeded, and a recorded state stops the sweep selecting the job).

`POST /api/v1/jobs/generate-jd` runs the credit gate and the Company Profile
gate BEFORE the writer is called, with the create route's own sentences.

### 2.2 The SWOT (Bodha): separate internal hiring intelligence

`/api/v2/assessments/jobs/{id}/swot-analysis`: read, generate, save, restore.
Since CONTRACT v10 the SWOT is OPTIONAL context for Sutra's draft and Save
Skills, and for Yukti's company-need fit when saved. It is never a publication
step, never shown to a candidate, and editable at any time, including after
the freeze (a SWOT edit changes nothing frozen).

- **Generate answers 202** and dispatches `pickready.generate_job_swot` after
  the commit. The deterministic sufficiency gate (`swot_input_state`, a JD body
  long enough to analyse) refuses BEFORE anything is dispatched. A generation
  that never reports back READS as failed after
  `SWOT_GENERATION_STALE_MINUTES`; nothing writes that state.
- **A human save wins a race with a generation.** The model runs without the
  row lock, and a save that landed meanwhile is kept.
- ~~**The first human save or restore, on a job with no skill row of any
  kind, asks Sutra for a draft** (`skills.after_swot_saved`).~~ **SUPERSEDED
  2026-09-28 (CONTRACT v10): a SWOT save drafts NOTHING.** While the job is
  not frozen a save only OFFERS a redraft (`skills_redraft_available`, true
  when the saved SWOT is newer than the one the draft read, and a draft that
  read no SWOT records none); a SWOT edit never rewrites the skills.
- **A SWOT edit is not refused by the lock.** The contract a candidate is
  assessed against is the snapshot, whatever happens to the document it was
  drafted from.

### 2.3 The Skills step (Sutra drafts, the team decides)

`/api/v2/assessments/jobs/{id}/skills`: read, add, paste (`/bulk`), edit
(`PATCH /{skill_id}`: rename, move, or both), remove, draft, save.

- **Sutra's draft** (`pickready.draft_job_skills`, task type `skills_drafting`,
  prompt version 2) writes at most five skills per bucket, at least one
  Must-have and one Behavioural, from the JD and its required skills, with
  the SAVED SWOT as optional extra context (with no SWOT the `swot` key is
  ABSENT from the payload, the Weaknesses rule does not apply, and a `swot`
  source is reflected on and refused). It is asked for after a create, after
  a JD save while the job has no skill row of any kind, by "Draft skills", and
  by the repair sweep; its gate is the JD
  (`generation_sufficiency.skills_draft_input_state`, the fixed sentence
  `EMPTY_STATE_COPY["skills.jd_too_thin"]`). A SWOT quote is kept only when it
  is a verbatim substring of the saved SWOT. A failed draft is the `failed`
  state with ZERO rows: there is no template draft.
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

### 2.5 The Final Job Posting

`GET /api/v2/assessments/jobs/{id}/posting-preview` (`view_company_jobs`,
reads only) is what the candidate-facing posting will say: the title, the
grade as a word, the experience band in words, the JD document, the company
narrative (the job's override, else the Company Profile) and the skills by
NAME under "Must-have skills", "Nice-to-have skills" and "Behavioural
competencies", alphabetical inside each bucket so the hidden priority is not
revealed. Never an evidence line, a priority or the role summary. The same
builder (`assessment_contract.posting_skills`) feeds the PUBLIC posting: the
apply page (`GET /jobs/public/{id}`), the portal job payloads and the employer
page carry `skill_buckets`, the SAVED skills or the frozen snapshot's names,
and an EMPTY list when no skills are saved.

### 2.6 Publish

`POST /api/v1/jobs/{id}/publish` is the only way a job goes live. It names
every missing step in one sentence ("Before this job can be published, write
and save the job description and save the skills."; the SWOT step is DELETED,
CONTRACT v10), writes PUBLISHED, and dispatches `pickready.run_matching` and
`pickready.index_document` with `dispatch_after_commit`, so a publish that
rolls back starts nothing.

### 2.7 The freeze and the snapshot (CONTRACT v10)

~~The first candidate start calls `assessment_contract.lock_contract`, which
writes an immutable `job_skill_snapshots` row ... every skills write answers
409 `SKILLS_LOCKED_DETAIL`.~~ **SUPERSEDED 2026-09-28 (CONTRACT v10, D5's lock
point moved).**

The FIRST GENUINE APPLICATION freezes the job: a candidate's own application
through the one apply path (`POST /portal/jobs/{id}/apply`, the portal and the
public `/apply/{job}` page), including a sourced link the candidate converts
by applying. In that application's transaction
`assessment_contract.freeze_at_application` writes an immutable
`job_skill_snapshots` row (`source='application'`, `locked_by_link_id`) with
the skills, the hidden context and the grade, under the SKILLS advisory lock,
so the application and the snapshot commit together and a rolled-back
application leaves none. NOT a freeze: a sourced upload, a databank link, the
matching run's links, an invitation. A legacy published job with no saved
skills takes the application and freezes nothing; the next genuine
application after the skills are saved freezes them.

Frozen means: the JD document, the title, the experience band, the grade and
the skills are read-only, each refused with 409 and ONE dated server sentence
(`assessment_contract.FROZEN_DETAIL`: "The job description and skills are
frozen because a candidate has applied. Frozen since 28 Sep 2026."). A freeze
is a STATE, never "Not permitted". The company narrative, the proctoring
policy, the department, the requirement period and the SWOT stay editable.
`/setup` reports `frozen`, `frozen_at` and `frozen_reason`.

The first candidate START calls `assessment_contract.lock_contract`, the
idempotent backstop: it binds the conversation to the existing snapshot, and
writes one (`source='lock'`) only for a job whose first application predated
its saved skills.

The snapshot is immutable twice: UPDATE is revoked from the application role,
and the trigger refuses every UPDATE and every DELETE not driven by a tenant or
job cascade. Migration 0131 admits exactly ONE update: the ON DELETE SET NULL
of `locked_by_link_id` when the application is erased, nested and changing no
other column, so a candidate's erasure keeps the snapshot every other candidate
is assessed against.

## 3. What runs in the background

| Task | Route | Trigger | What it may never do |
|---|---|---|---|
| `pickready.generate_job_swot` | Lambda | SWOT generate, after commit | overwrite a human save made while it ran |
| `pickready.draft_job_skills` | Lambda | create with a JD, a JD save with no skill rows, "Draft skills" or a confirmed redraft, the sweep (never a SWOT save) | write a template, or replace the team's skills without confirmation |
| `pickready.reconcile_job_setup` | Lambda, every 15 min | schedule | select a job whose rows the team emptied |
| `pickready.remind_unsaved_skills` | Lambda, every 60 min | schedule | mail anybody who cannot save (recipients by capability) |

**The sweep selects a job only when it HAS A JD and is not frozen, and either
no draft was ever asked for AND it has ZERO rows of ANY kind, or a draft was
asked for and never reported back** (`skills.DRAFT_STALE_AFTER`). It asked for
a saved SWOT until CONTRACT v10. A set a person emptied is a decision, not a
missing draft (audit number 8). A JD too thin to draft from is recorded as the
failed state with the fixed sentence. Newest first, BATCH at a time: every job
examined leaves the selectable set. On the first run after the v10 deploy it
drafts skills for every existing job with a JD and no skill row.

## 4. Where each rule is pinned

| Rule | Pytest | Harness scenario |
|---|---|---|
| An emptied set is never redrafted, by the read or the sweep | `test_reconcile_job_setup.py` | `regression.an_emptied_skill_set_is_not_redrafted` |
| A removed skill is revived, not re-inserted | `test_job_skills_service.py`, `test_job_skills_api.py` | `regression.a_removed_skill_can_be_added_back` |
| A rename onto a hidden occupant is a 409 naming it | `test_job_skills_service.py` | `regression.a_rename_onto_a_hidden_occupant_is_a_409` |
| Save's audit rows survive their commit | `test_job_skills_save.py` | `regression.an_audit_row_survives_its_own_commit` |
| A writer outage names no skill and saves nothing | `test_job_skills_save.py` | `regression.a_writer_outage_saves_nothing_and_names_no_skill` |
| The draft is dispatched after commit from the JD, the SWOT optional, verbatim quotes only | `test_job_skills_draft.py` | |
| The publish gate names every step (JD and skills; the SWOT is none); dispatch after commit | `test_job_publish_gate.py` | |
| The first genuine application freezes, in its own transaction; nothing else does | `test_freeze_at_application.py` | `regression.the_first_application_freezes_the_job` |
| A frozen job refuses JD, title, band, grade and skills edits with one dated sentence | `test_job_skills_api.py`, `test_grade_lock.py` | `regression.the_first_application_freezes_the_job` |
| The preview and the public posting carry skill NAMES only | `test_posting_preview.py`, `test_employer_pages.py` | |
| The pre-v10 order stays gone | `test_setup_order_v10_removed.py` | |
| The whole order, end to end | `test_golden_journey.py` | `integration.the_golden_journey` |
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
  the hidden context and the draft state (so the sweep can draft again from
  the JD); `job_skill_snapshots` is PRESERVED, so a frozen job stays frozen
  and its survey counts them.

## 6. Open owner questions

- **HR Manager publish.** RBAC data is unchanged, and the HR Manager used to
  publish only through Create Job's flag, which is gone, so an HR Manager can
  no longer publish.
- **Assigning a Hiring Manager.** There is no assignment screen, so only a
  Hiring Manager who created a job holds skill-edit scope on it.
- **Scoring reads the contract.** Until Phases 3 and 5 move the scoring path
  onto `assessment_contract`, a job saved through the Skills step is not
  scoreable; Phases 1, 3 and 5 ship in one deploy.
