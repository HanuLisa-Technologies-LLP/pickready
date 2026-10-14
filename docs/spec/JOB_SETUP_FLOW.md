# Job setup: JD, Skills, approval, the Final Job Posting, publish, and the freeze

**Status:** current for what is built (sections 1 to 6, the Vivekium release as amended by CONTRACT v10, 2026-09-28). **Section 7 is decided 2026-10-14 and not built** (register J-10 to J-16); where it differs from sections 1 to 6, section 7 is the intent and the register's Pending change 1 is the work owed. **Reviewed:** 2026-10-14.
The API shapes the frontend builds against: [../reference/JOB_SETUP_API.md](../reference/JOB_SETUP_API.md). Authorization is in [../spec/RBAC_SPECIFICATION.md](RBAC_SPECIFICATION.md) and [../reference/RBAC.md](../reference/RBAC.md). The end-to-end journey is [HIRING_WORKFLOW.md](HIRING_WORKFLOW.md).

## 1. The flow today

```
Create Job (a DRAFT, always, with its JD)
  -> Sutra's skills draft is dispatched AFTER the commit (from the JD)
  -> JD document: PATCH /jobs/{id}/jd                    one edit path
  -> Skills step: add / paste / rename / move / remove   per-bucket capability
  -> Save Skills: ONE Sutra context call, then one transaction
  -> Final Job Posting: GET .../posting-preview
  -> Publish: JD + saved skills                          (section 7 adds approval)
  -> first GENUINE application freezes JD + Skills       immutable snapshot
  -> first candidate start binds the conversation to the snapshot
SWOT: separate internal hiring intelligence (section 2.2; timing is section 7)
```

Every "is it done" question is derived from the tables, never from a stamp:

| Question | Asked of | Code |
|---|---|---|
| Is the SWOT saved? | the SWOT row: content, `human_edited`, status `edited` | `swot_analysis.is_saved` |
| Are the skills saved? | `jobs.framework_approved_at` AND `jobs.assessment_context_json` | `assessment_contract.skills_saved` |
| Is the job frozen? | a `job_skill_snapshots` row exists | `assessment_contract.is_locked`, `frozen_since` |
| Ready for candidates? | skills saved | `skills.refresh_setup_status` writes `assessment_status` |
| May it publish? | the JD and saved skills, every missing one named | `api/jobs._publication_blocked` |
| May a draft be asked for? | the title and a JD body of at least `SKILLS_DRAFT_MIN_JD_WORDS` | `generation_sufficiency.skills_draft_input_state` |

`framework_approved_at` means "skills saved at" (a persisted name is not renamed for copy).

## 2. Each step

### 2.1 Create Job

`POST /api/v1/jobs` saves a DRAFT. `jd_markdown` is required and may not be headings only. `publish: true` is a 422 naming the separate publish step. A Recruiter or Hiring Manager who creates a job is assigned to it (`rbac.assign_creator`), because every scoped cell reads `job_assignments`. The one dispatch is Sutra's skills draft from the JD, after the commit (`skills.after_jd_saved`); a JD too thin to draft from is recorded as the draft's `failed` state with a fixed sentence (the create succeeded, and a recorded state stops the sweep re-selecting the job). `POST /api/v1/jobs/generate-jd` runs the credit gate and the Company Profile gate before the writer.

### 2.2 The SWOT (Bodha)

`/api/v2/assessments/jobs/{id}/swot-analysis`: read, generate, save, restore. The SWOT is internal hiring intelligence: optional context for Sutra's draft and Save Skills and for Yukti's company-need fit when saved; never a publication step; never shown to a candidate. A SWOT save drafts nothing; while the job is unfrozen it only offers a redraft (`skills_redraft_available`).

- Generate answers 202 and dispatches `pickready.generate_job_swot` after the commit. The deterministic gate (`swot_input_state`) refuses before dispatch; a generation that never reports back reads as failed after `SWOT_GENERATION_STALE_MINUTES`.
- A human save wins a race with a generation (the model runs without the row lock). A regeneration over human-edited content is refused unless confirmed, and a confirmed replacement snapshots what it replaced.
- **When the SWOT freezes is decided (J-16) and not built:** today a SWOT edit is never refused.

### 2.3 The Skills step

`/api/v2/assessments/jobs/{id}/skills`: read, add, paste (`/bulk`), edit (`PATCH /{skill_id}`), remove, draft, save.

- **Sutra's draft** (`pickready.draft_job_skills`, task type `skills_drafting`, prompt version 2): at most five per bucket, at least one Must-have and one Behavioural, from the JD and its required skills, with the saved SWOT as optional context (with none, the `swot` key is absent). A SWOT quotation is kept only if verbatim in the saved SWOT. A failed draft is the `failed` state with zero rows: there is no template draft. A redraft over the team's own skills needs their confirmation.
- **Authorization is per bucket** (`capabilities.SKILL_BUCKET_CAPABILITY`): the target bucket for an add or paste, the row's bucket for a rename or remove, both for a move, all three for a draft, and `finalize_role_definition` plus all three for a save. `can_edit` per bucket and `can_save` ride the payload from the same `rbac.authorize` calls.
- **Soft delete under a unique key.** Re-adding a removed name revives the row; a rename onto any occupant, visible or not, is a 409 naming it; a move onto a removed row revives it; a rename clears the SWOT sentence and marks the row the team's. A paste past five in a bucket is refused whole.
- Any edit un-saves the set; a published job keeps taking applications and cannot invite until re-saved.

### 2.4 Save Skills

`POST /api/v2/assessments/jobs/{id}/skills/save` (`skills.save`), the recorded exception to "no slow work in a request". (1) Refuse if frozen; refuse with every problem if the set cannot be saved (422). (2) One `sutra.build_context` call for exactly these skills: an outage is a 503 whose sentence names no skill and changes nothing; a skill the writer cannot describe observably is a 422 naming every such skill. (3) Take the skills lock, re-check, compare the rows with what was sent: an edit that landed meanwhile is a 409. (4) Write evidence lines, per-bucket priorities, the hidden context (role summary, model, prompt version, digest), the saved stamp, the lifecycle DRAFT to FINALIZED, and two audit rows, each in one insert.

### 2.5 The Final Job Posting

`GET /api/v2/assessments/jobs/{id}/posting-preview` (reads only): title, grade as a word, experience band in words, the JD, the company narrative (the job's override, else the Company Profile) and skill names under "Must-have skills", "Nice-to-have skills" and "Behavioural competencies", alphabetical so the hidden priority is not revealed. Never an evidence line, a priority or the role summary. One builder (`assessment_contract.posting_skills`) also feeds the public apply page, the portal job payloads and the employer page: the saved skills, the frozen snapshot's, or an empty list.

### 2.6 Publish

`POST /api/v1/jobs/{id}/publish` is the only way a job goes live. Today it names every missing step ("Before this job can be published, write and save the job description and save the skills."), writes PUBLISHED, and dispatches `pickready.run_matching` and `pickready.index_document` after the commit, so a rolled-back publish starts nothing. Authorization runs the RBAC chain in the handler so a caller stopped by the job's state is told the missing steps; a scope refusal is never told them.

### 2.7 The freeze and the snapshot

The first genuine application freezes the job: the candidate's own application through the one apply path (including a sourced link converted by applying). In that application's transaction `assessment_contract.freeze_at_application` writes an immutable `job_skill_snapshots` row (`source='application'`, `locked_by_link_id`) with the skills, the hidden context and the grade, under the SKILLS advisory lock. A rolled-back application leaves none. Not a freeze: a sourced upload, a databank link, the matching run's links, an invitation. A legacy job with no saved skills takes the application and freezes nothing; the next genuine application after the skills are saved does.

Frozen means the JD, title, experience band, grade and skills are read-only; each refusal is 409 with one dated server sentence (`FROZEN_DETAIL`: "The job description and skills are frozen because a candidate has applied. Frozen since 28 Sep 2026."). The company narrative, proctoring policy, department, requirement period and (until J-16 is built) the SWOT stay editable. `/setup` reports `frozen`, `frozen_at` and `frozen_reason`.

The first candidate start calls `lock_contract`, the idempotent backstop: it binds the conversation to the existing snapshot and writes one (`source='lock'`) only for a job whose first application predated its saved skills. The snapshot is immutable twice (UPDATE revoked; a trigger refusing every UPDATE and DELETE not driven by a tenant or job cascade). Migration 0131 admits exactly one update: the nested `ON DELETE SET NULL` of `locked_by_link_id` when the application is erased, changing no other column.

## 3. What runs in the background

| Task | Route | Trigger | What it may never do |
|---|---|---|---|
| `pickready.generate_job_swot` | Lambda | SWOT generate, after commit | overwrite a human save made while it ran |
| `pickready.draft_job_skills` | Lambda | create with a JD, a JD save with no skill rows, "Draft skills" or a confirmed redraft, the sweep | write a template, or replace the team's skills without confirmation |
| `pickready.reconcile_job_setup` | Lambda, every 15 min | schedule | select a job whose rows the team emptied |
| `pickready.remind_unsaved_skills` | Lambda, every 60 min | schedule | mail anybody who cannot save (recipients by capability) |

The sweep selects a job only when it has a JD, is not frozen, and either no draft was ever asked for and it has zero rows of any kind, or a draft was asked for and never reported back. A set a person emptied is a decision, not a missing draft. Newest first, in batches of at most 25 per run.

## 4. Where each rule is pinned

| Rule | Pytest | Harness scenario |
|---|---|---|
| An emptied set is never redrafted | `test_reconcile_job_setup.py` | `regression.an_emptied_skill_set_is_not_redrafted` |
| A removed skill is revived, not re-inserted | `test_job_skills_service.py`, `test_job_skills_api.py` | `regression.a_removed_skill_can_be_added_back` |
| A rename onto a hidden occupant is a 409 naming it | `test_job_skills_service.py` | `regression.a_rename_onto_a_hidden_occupant_is_a_409` |
| Save's audit rows survive their commit | `test_job_skills_save.py` | `regression.an_audit_row_survives_its_own_commit` |
| A writer outage saves nothing and names no skill | `test_job_skills_save.py` | `regression.a_writer_outage_saves_nothing_and_names_no_skill` |
| Draft dispatched after commit from the JD; verbatim quotes only | `test_job_skills_draft.py` | |
| The publish gate names every step; dispatch after commit | `test_job_publish_gate.py` | |
| The first genuine application freezes, in its own transaction; nothing else does | `test_freeze_at_application.py` | `regression.the_first_application_freezes_the_job` |
| A frozen job refuses JD, title, band, grade and skills edits with one dated sentence | `test_job_skills_api.py`, `test_grade_lock.py` | same |
| The preview and the public posting carry skill names only | `test_posting_preview.py`, `test_employer_pages.py` | |
| The contract snapshot is immutable | `test_assessment_contract.py` | |
| The whole order, end to end | `test_golden_journey.py` | `integration.the_golden_journey` |

Harness worlds: `job_with_drafted_skills`, `job_with_team_written_skills`, `job_with_saved_skills` (`backend/harness/world.py`).

## 5. Operator scripts

- `python -m app.scripts.skills_overflow_report`: every bucket over the limit. Read only.
- `python -m app.scripts.backfill_assessment_context`: dry run by default; lists saved, unfrozen jobs whose hidden context Sutra did not write; `--apply --operator-email` re-saves each through `skills.save`, one transaction per job, naming the platform operator on the audit rows.
- `python -m app.scripts.seed_demo_applications`: saves demo jobs' drafted skills through `skills.save` (demo tenants only), then seeds applications.
- `python -m app.scripts.legacy_reset`: clears the saved stamp, hidden context and draft state; `job_skill_snapshots` is preserved.

## 6. Roles today

The RBAC cells read by job preparation: Recruiter creates and edits an assigned draft JD but cannot edit a skill; Hiring Manager edits skills and the SWOT on an assigned job; HR and Recruitment Managers edit everything and finalize; publishing is a scoped capability (`PUBLISH_JOB`) held by the Recruiter in scope and the Super Admin as an audited exception; there is no assignment screen, so only a Hiring Manager who created a job holds skill-edit scope on it. The HR Manager can no longer publish (the flag in Create Job that gave it that right is gone).

## 7. Decided 2026-10-14, not built (J-10 to J-16)

**Status: decided, pending.** The owner replaced the publish rule above with an approval flow. Nothing in this section is implemented; do not describe it as working.

### 7.1 The flow

```
Recruiter      creates the job, prepares the JD, assigns a Hiring Manager
Hiring Manager reviews the JD, prepares Skills and the SWOT, submits
HR / Recruitment Manager   approves, or returns it with a reason
Recruiter      publishes ONLY an approved job
```

Publish requires a completed JD, saved Skills and HR approval of the exact revision published. The SWOT is not a publication blocker. "Skills Matrix" in the owner's wording is the Skills step; there is no matrix.

### 7.2 Who may do what

| Role | May | May not |
|---|---|---|
| Company Super Admin | Everything below; overrides are audited | Skip the steps (approval still required); may approve when no independent approver exists (7.4) |
| HR or Recruitment Manager | Create jobs; edit JD, Skills and SWOT; assign Hiring Managers; approve or return | Publish |
| Recruiter | Create jobs; edit assigned draft JDs; publish assigned approved jobs | Edit Skills or SWOT; approve |
| Hiring Manager | Edit assigned JD, Skills and SWOT; submit | Publish; approve |
| Interview Manager | Nothing in job preparation or publishing | Anything above |

Server-side enforcement includes tenant isolation and job assignments. This amends the RBAC specification's publish cell for the HR Manager ("YES*" becomes no) and adds a step to its section 17 lifecycle; the filed specification text is not edited.

### 7.3 Return and re-approval

HR returns a job to the Recruiter (JD problems) or the Hiring Manager (Skills problems), choosing the recipient and writing a reason. A **material change** to the JD, job details, Skills or hiring-relevant SWOT after approval cancels the approval and needs fresh HR review before publishing; cosmetic formatting and internal notes do not. Approval history is preserved, every change is recorded, and publication uses the exact approved revision. The definition of "material" and "hiring-relevant" is unresolved (Q-03).

### 7.4 Independence

By default an independent HR or Recruitment Manager approves: anyone who materially prepared the JD, Skills, SWOT or requirements of a revision cannot ordinarily approve that revision. If no independent approver exists, the Super Admin may approve, including an audited self-approval exception for small companies. Record approver identity, justification, revision and audit history. Cosmetic edits do not count as preparation. An unauthorised role never approves.

### 7.5 SWOT timing

The SWOT may be written and edited before or after publishing, is never shown to a candidate, must be saved before assessment, and freezes when the first assessment starts. The JD and Skills still freeze at the first genuine application (2.7). Open points: Q-04 to Q-06.

### 7.6 What stays

Existing candidate records, immutable assessment snapshots and the skills-locking rules are preserved. No second workflow is added: the approval states extend the existing lifecycle (names open, Q-01).
