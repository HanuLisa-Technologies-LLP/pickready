## Current hard rules, job setup routes, the publish gate and the retired approval chain (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 1, work package C
(API, RBAC, schemas). Migration `0119_job_approval_chain_removed`. The
services under these routes are work package B's (`claude-p1-b.md`).

### A JOB GOES LIVE THROUGH ONE ROUTE, BEHIND THREE STEPS ASKED OF THE TABLES

- **`POST /jobs` saves a DRAFT, always.** `publish` used to default to True
  and a job went live under `create_job` alone, skipping the gate, the JD
  index and the lifecycle: every live job on pilot reached PUBLISHED that way
  with no skills anyone had saved. `publish` stays in `JobCreateIn` ONLY to
  refuse `true` with a 422 naming the new flow, loud during a rolling deploy
  instead of a silently mislabelled draft. `jd_markdown` is REQUIRED and may
  not be headings only; the per-section `jd` input and `level` are gone.
  Nothing is dispatched at creation.
- **`POST /jobs/{id}/publish` is the only way live**, and
  `_publication_blocked` names EVERY missing step in one sentence, in order:
  the JD (`has_publishable_jd`), the SWOT SAVED by a human
  (`swot_analysis.is_saved`: a model draft nobody read is not the team's
  analysis), the skills saved (`assessment_contract.skills_saved`, the stamp
  AND the hidden context, so "publishable" and "lockable" cannot disagree).
  Asked of the rows, never of `lifecycle_state`, which is a stamp like any
  other (rule 8).
- **Publish authorization is RBAC 3's whole chain run in the handler**, not a
  route dependency, for one reason: a caller stopped by the job's STATE
  (`publish_requires_finalized`) is told which steps are missing instead of a
  bare "Not permitted". Tenant, ceiling, grant and scope still refuse first
  and exactly as before, and a scope refusal is never told the setup steps.
- **`run_matching` and `index_document` are `dispatch_after_commit`.** A
  publish that rolls back starts nothing; `tests/test_job_publish_gate.py`
  proves it by failing the request after the dispatches were requested.
- **`POST /jobs/generate-jd` runs the credit gate and Gate 1 BEFORE the
  writer**, with the create route's own sentences (`_require_create_gates`).
  They used to run only at create, after the model had been paid for a draft
  the recruiter could never save.
- **The JD has ONE edit path, `PATCH /jobs/{id}/jd`.** `PATCH /jobs/{id}` is
  metadata only with `extra="forbid"`, so an old client sending `jd`,
  `jd_markdown` or `level` is a 422 rather than a field silently ignored, and
  `PUT /jobs/{id}/jd` (which left the document stale) is deleted. Three
  writers of one text is the shape rule 5 forbids.
- **THE GRADE LOCKS WITH THE SKILLS (D5).** It decides every candidate's
  question budget, so once a `job_skill_snapshots` row exists a grade CHANGE
  is a 409 (`GRADE_LOCKED_DETAIL`). The check takes the same SKILLS advisory
  lock a candidate's start takes, so a change and a first start never
  interleave. Resending the current grade is not a change.

### THE CREATOR IS ASSIGNED, AND THE STATEMENT THAT DOES IT HAD NEVER RUN

`rbac.assign_creator` writes one active `job_assignments` row for a Recruiter
or Hiring Manager who creates a job (the Super Admin and HR Manager are never
scoped, so nothing is written for them). Every SCOPED cell of RBAC 24 reads
that table and nothing else wrote it: without the row the Recruiter who
created a job could not publish it and a Hiring Manager could not edit its
skills. Migration 0118 backfilled existing jobs with the same rule.

**Its first version would have 500'd every such create.** Each parameter sat
in both the SELECT list of an `INSERT ... SELECT` and the WHERE, so asyncpg
deduced two types and raised `AmbiguousParameterError`. Every test that
reached it stubbed it. The parameters are CAST now, and the lesson is the old
one: a statement that has only ever met a stub has not been tested.
`tests/test_job_publish_gate.py` drives `POST /jobs` over the real session.

### THE SKILLS STEP'S AUTHORIZATION IS PER BUCKET, AND THE LOCK IS 409, NOT 403

`api/job_setup.py` (mounted under `/api/v2/assessments`, so the SWOT URLs it
absorbed from `api/assessments.py` are unchanged) carries the setup
checklist, the Skills step and the SWOT routes.

- **Each bucket is its own capability** (`capabilities.SKILL_BUCKET_CAPABILITY`).
  Which bucket a write touches is in the BODY or on the ROW, which a route
  dependency cannot see, so the dependency is the flat
  `require_capability(VIEW_COMPANY_JOBS)` and the handler runs
  `rbac.authorize` for: the target bucket (add, paste), the row's bucket
  (rename, remove), BOTH buckets (move), all three (draft), and
  `finalize_role_definition` plus all three (save). The retired matrix routes
  all asked `create_job`, which let a Recruiter (a NEVER cell for every
  criterion) edit Must-haves (audit #16).
- **`can_edit` per bucket and `can_save` are resolved on the payload by the
  same `rbac.authorize` calls the writes enforce with.**
- **A locked job answers 409 with `SKILLS_LOCKED_DETAIL`, not 403.** A lock is
  a STATE of the job, not a grant the person lacks, and "Not permitted" would
  send them to an administrator who cannot help.
- **The reads (`GET /setup`, `GET /skills`, `GET /swot-analysis`) write and
  dispatch nothing.** The retired setup GET dispatched a matrix compile from a
  read, before the commit.

### RBAC: THE SKILLS LOCK REPLACES THE FINALIZATION FREEZE

**SUPERSEDES** the rule `criteria_frozen_after_finalization` (RBAC 22 and 26
as read since 2026-08-29): it refused every Hiring-Manager-controlled
capability from FINALIZED onward, which refused every SWOT edit the moment the
skills were first saved and refused a skills correction on a job nobody had
been assessed on. Now `Resource.skills_locked` is loaded from the TABLE
(`EXISTS job_skill_snapshots`) and only `capabilities.SKILL_CAPABILITIES` (the
three buckets, the rubric capability and `finalize_role_definition`) are
refused with `skills_locked`. `edit_swot` and `edit_job_philosophy` are no
longer lifecycle-gated: the contract is the immutable snapshot, whatever
happens to the documents it was drafted from. A job whose `lifecycle_state`
is NULL is still refused all of them (`lifecycle_state_unknown`).

**Bodha and Sutra run under separate runtime ids** (`AGENT_SWOT`,
`AGENT_SKILLS`). The shared `job_setup` id held the UNION of both agents'
reach, so the SWOT writer held the skills capabilities; least privilege is one
id per agent. Neither holds `retrieve_context`, which the shared grant carried
for a compiler that no longer exists.

### THE APPROVAL CHAIN IS DELETED, CODE AND DATABASE TOGETHER

**SUPERSEDES** RBAC 17's eight-state lifecycle ("§17's job lifecycle has
EIGHT states", 2026-08-29) and RBAC 9.3's hand-off. The routes (hand-off to
the Hiring Manager, `submit`, `approve`, the approvals list), their schemas,
the capability `send_jd_to_hiring_manager` and the states
`SENT_TO_HIRING_MANAGER` and `IN_REVIEW` are gone. The lifecycle is
DRAFT -> FINALIZED (Save Skills) -> PUBLISHED -> CANDIDATE_APPLICATIONS ->
HIRING_PROCESS -> CLOSED_ARCHIVED.

- **Migration 0119 is the second half of the code change, not a separate
  one.** It remaps every straggler in a retired state (including archived
  rows 0118's remap skipped: PUBLISHED when ever published, since no
  published job is ever unpublished, DRAFT otherwise), recreates
  `ck_jobs_lifecycle_state` with exactly the enum's six values, and deletes
  every `role_permissions` row for the capability. Tightening the CHECK before
  the enum lost its members, or deleting the rows before the constant, would
  each break the other half.
- **What survives, deliberately**: `APPROVE_JOB`, `CONFIGURE_APPROVAL_LEVELS`,
  the multi-level `approval_fsm` functions and the company approval-levels
  route, handed to Phase 7 as one unit; `approval_fsm.apply_direct_publish`
  and `job_approvals`, because publish writes them.
- `tests/test_job_approval_chain_removed.py` sweeps the live tree, the route
  table, the enum against the migration's own list, and the MIGRATED database
  (no grant rows, a six-state CHECK).

### `level` IS READ AND WRITTEN BY NOTHING THIS PACKAGE OWNS

**SUPERSEDES** "`level` survives only for jobs created before 2026-07-28"
(2026-07-28). It is gone from every job schema, the employer page, the
relevance ranker (which reads the grade label instead) and the SWOT context.
THE COLUMN STAYS (CONTRACT v3: 30 rows carry a value). Remaining readers are
named pending hand-offs in `tests/test_job_level_removed.py` with their
owners (portal: Phase 6; `matching._jd_text`: Phase 2; `infer_grade`: Phase 7;
two backfill scripts and the frontend: their packages), and each entry fails
as stale the moment its reader goes.

### OPEN

- **HR Manager publish**: RBAC data unchanged (CONTRACT v2), so an HR Manager,
  who used to publish through Create Job's flag, now cannot publish at all.
  Owner question.
- **No Hiring Manager assignment UI**: only a Hiring Manager who creates a job
  holds skill-edit scope on it.
