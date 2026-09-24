## Current hard rules, the Skills step, simplified Sutra and the dispatched SWOT (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 1, work package B
(services, prompts, tasks). No migration: 0118 already carries every column this
package writes. The routes over these services are the next package's.

### THE RECRUITER NEVER SEES A MATRIX. THE SKILLS STEP IS ONE SERVICE

Owner decision D1. `services/skills.py` is the ONE implementation of add, paste,
rename, move, remove, draft and save; `services/hiring/sutra.py` is the model
half. A skill is a `job_competencies` row: a bucket (Must-have, Nice-to-have,
Behavioural), at most five per bucket, and at least one Must-have and one
Behavioural to save. The team sees NAMES; the priority (`force_rank`, per
bucket), the evidence line (`observable_evidence`, mirrored into
`description`), the SWOT quotation and `authored_by` are internal.

- **Every write takes `locks.advisory_xact_lock(SKILLS, job)` and then
  `assessment_contract.require_unlocked`**, the same lock a candidate's start
  takes, so an edit and a start never interleave. After the first start every
  write is refused with `SKILLS_LOCKED_DETAIL` and nothing changes (D5).
- **Every edit makes the skills UNSAVED again** (`framework_approved_at`
  cleared, `assessment_status` back to pending). A published job keeps taking
  applications and cannot invite until the skills are saved again.
- **Carried over unchanged**: revive, never re-insert (2026-09-21); a rename
  clears `swot_origin` and everything derived and makes the row the team's, a
  revive keeps its quotation (2026-09-23); a rename onto an occupied name,
  visible or soft-deleted, is a 409 naming it; adding a name already there is
  idempotent.
- **A MOVE CHECKS THE TARGET BUCKET FIRST**, which fixes the reorder 500
  (audit #17): an active occupant is a 409; a removed occupant is REVIVED in
  the target and the moved row retired. Behavioural is a valid destination now,
  because every skill is graded from the answers.
- **Names match case-insensitively after collapsing whitespace, and a name
  active in another bucket is refused**: one skill is graded once.
- **A limit breach is refused WHOLE.** A paste that would take a bucket past
  five writes nothing and says how many fit, in words; the reviewer chooses
  what to keep rather than the server dropping the tail.
- **Every refusal is a `SkillsError` carrying `http_status` and a sentence the
  route renders verbatim.** `SkillsLocked` stays the contract's own type (409).

### SAVE SKILLS: ONE SUTRA CALL, THEN ONE TRANSACTION. THE RECORDED EXCEPTION TO RULE 4

`skills.save` makes exactly one model call (`assessment_context`, Terra, the
generative-interactive tier at 25s / 50s) BEFORE any write, then takes the
skills lock, re-reads the rows and writes the evidence lines, the per-bucket
priorities, `jobs.assessment_context_json`
(`{role_summary, generated_by, model_id, prompt_version, generated_at,
skills_digest}`), the saved stamp, lifecycle DRAFT to FINALIZED (never
backwards) and two audit rows, each in ONE insert.

- **Synchronous in the request, and that is the one exception to rule 4 this
  phase makes.** The context must land in the same transaction as the human's
  save, the call is bounded by the interactive tier, and every refusal precedes
  every write. Same shape as the matrix freeze it replaces.
- **The lock is NEVER held across the model call.** An edit or a start can
  land while the model thinks; the save compares the rows it sent with the rows
  it would write and answers 409 `SKILLS_CHANGED_DURING_SAVE` rather than write
  a context describing skills nobody saved.
- **An outage names no skill** (503 `SKILLS_CONTEXT_UNAVAILABLE`, nothing
  changed); **a refusal names EVERY refused skill** (422); invalid skills are
  refused listing every problem before any call. The 2026-09-23 rule, kept.
- **The model never renames a skill.** A returned name that differs from the
  one sent is rejected and reflected on; the team's names are the contract.
- **Only a SWOT the team SAVED is context** (`swot_analysis.is_saved`). A model
  draft nobody read must not shape what candidates are assessed against.

### THE SKILLS DRAFT IS DISPATCHED, AND IT NEVER OVERWRITES THE TEAM SILENTLY

- **The first human SWOT save on a job with no skill row of ANY kind** asks
  for a draft (`skills.after_swot_saved`), handed off after the commit
  (`pickready.draft_job_skills`, Route.LAMBDA). Every later save only OFFERS a
  re-draft (`skills_redraft_available` on the SWOT response); nothing is
  re-drafted without the team asking, and a re-draft over the team's own
  skills (`authored_by = 'human'`) is refused until they confirm.
- **The draft is ONE model call** (`skills_drafting`, Terra, background) with a
  deterministic evaluator: at most five per bucket, one Must-have and one
  Behavioural, short capability names, no culture term, and **the JD's
  required skills and the SWOT Weaknesses must both reach Must-have** (a prompt
  sentence is a request; the evaluator is the enforcement).
- **A SWOT quotation is stored only if it is VERBATIM in the saved SWOT**;
  otherwise NULL. A fabricated citation reads as provenance.
- **A failure is the `failed` state with ZERO rows. There is no template
  draft** (rule 6); the team drafts again or adds their own.
- **The model is called with NO lock held**, then the writes re-check the lock,
  the draft state and the team's own skills under the skills lock. A FIRST
  draft on a job that already has rows is a no-op, which makes a redelivered
  message harmless. A `drafting` state older than fifteen minutes READS as
  failed.

### THE JOB SWOT IS DISPATCHED WORK NOW

SUPERSEDES the 2026-09-13 rule that generation ran in the request, and
`swot_analysis` is no longer a member of the generative-interactive tier:
`assessment_context` took its place, and the tier is still capped at two.

- **`swot_analysis.request_generation` refuses first, writes second, dispatches
  last**: over the team's edits without confirmation (409), over a JD too thin
  to analyse (409 with the fixed `EMPTY_STATE_COPY["swot.jd_too_thin"]`,
  decided by `generation_sufficiency.swot_input_state` before any model is
  involved), then `generating` and `pickready.generate_job_swot` after the
  commit. The route answers 202.
- **The worker calls the model WITHOUT the row lock and writes only if the row
  is still the one asked about** (still `generating`, same version). A human
  save in the meantime wins and the generation stands down.
- **A generation that never reports back READS as failed** after
  `SWOT_GENERATION_STALE_MINUTES` (default 5), derived and never written. No
  sweep: the team presses Generate again.
- **`is_saved` is the one definition of a SAVED SWOT**: the content is the
  team's. The Skills draft and the publish gate both ask it.
- **The prompt reads the GRADE, never `jobs.level`.**

### THE SWEEP NEVER REFILLS WHAT A PERSON EMPTIED

`pickready.reconcile_job_setup` keeps its name, schedule and Terraform, and its
body is new. It selects a job only when its SWOT is SAVED and either no draft
was ever asked for and it has ZERO rows of ANY kind, or a draft was asked for
and never reported back. **A job whose rows are all soft-deleted is never
selected**: the old sweep asked for ACTIVE rows and put back the skills a hiring
manager had removed (audit #8). A lost re-draft it may not repeat on the team's
behalf is finished as `failed`, once. It dispatches through
`skills.request_draft`, the same door a human save takes.

### THE REMINDER GOES BY CAPABILITY, TO A PAGE THAT EXISTS

`pickready.remind_unsaved_skills` (rule `readypick-remind-unsaved-skills`,
hourly, in `schedule.py` and all three environments) replaces the
technical-questions reminder, which mailed by ROLE NAME and linked to a setup
page that does not exist. Recipients are whoever
`rbac.authorize(FINALIZE_ROLE_DEFINITION)` allows ON THAT JOB (the client super
admin, and a Hiring Manager only when assigned); one reminder per job; link
`/org/jobs/{id}`. Setting `skills_setup_reminder_hours` (48) replaces
`technical_review_reminder_hours`.

### DRISHTI IS OPTIONAL CONTEXT TEXT AND NOTHING ELSE

Owner ruling. Its weighting is deleted, and so is the raw non-negotiables text
it was stored for: client free text nothing reads is client free text nobody
needs to hold. `drishti.prompt_context` is the only thing that reaches a prompt
(Sutra's two calls), and with no profile the key is ABSENT from the payload and
the rule absent from the instruction: the same bytes as before Drishti existed.

### WHAT IS DELETED, ALL OF IT

The scorecard's WRITE half (the compiler, the reviewed-row enrichment, the
freeze, the naming step and its prompt), `hiring/transformation.py`,
`hiring/swot_quality.py`, the `ppi` matrix save check and the A2A matrix
artifact, the matching-category generator and its prompt, the tasks
`compile_tatva_matrix` and `generate_matching_categories` and both alias names
that forwarded to the compiler, the technical-questions reminder, the
`/framework` routes, the job-setup route that repaired a matrix by dispatching
from a GET, the categories routes, and the two retired demo scripts (and the CI
step that ran one). `tests/test_tatva_matrix_editor_removed.py` sweeps the live
tree and the route table; its frontend and harness exemptions are PENDING
hand-offs with a staleness check, so none outlives its reason.

**What survives, deliberately**: the scorecard's READ half (`MatrixItem`,
`FrozenMatrix`, `load_frozen_matrix`, `require_frozen_matrix` as G1,
`plain_provenance`) because `functional_assessment`, `evidence_graph`, `miti`,
`siddhi` and `api/jobs` still read it until the grading phase moves them onto
the contract. **A row written by the skills flow is NOT a matrix item** (no
dimension, no weight), so G1 refuses a new-skills job until then, which is why
the skills, assessment and grading phases ship in one deploy.

### SUPERSESSIONS TO MARK IN PLACE

- 2026-09-23 "Save Matrix enriches, then freezes": superseded by Save Skills
  above; the ordering rule (model work before writes, one transaction) is kept.
- 2026-09-23 "An outage is not a badly written criterion": kept, now in
  `sutra.SutraUnavailable` / `SutraRefused`.
- 2026-09-23 "A rebuild may not discard a human decision": the signal is
  `authored_by = 'human'` now, not an absent `swot_origin`; the rule is kept in
  `skills.request_draft` and re-checked in `skills.draft`.
- 2026-09-23 "Reopen is bounded by the issued contract": superseded; there is
  no reopen, and the lock at the first start replaces it.
- 2026-09-21 "A matrix a human emptied is not a matrix that was never written":
  kept, now in the sweep and in `after_swot_saved`.
- 2026-09-21 "The matrix editor is a list of skills": superseded by the Skills
  step (D1); chips survive in the frontend package.
- 2026-09-13 "The Job SWOT Analysis is AI-drafted and human-owned": generation
  is dispatched now; "swot_analysis is the second and last member of the
  generative interactive tier" is superseded by `assessment_context`.
- 2026-09-19 Drishti as a weight layer: superseded; context text only.
- 2026-08-06 "Job setup generates ONE thing" and the reconcile paragraph:
  superseded; the aliases are deleted, the sweep is rewritten.
- 2026-07-30 manual review gate: the gate is Save Skills now;
  `matching_categories_finalized_at` is no longer read.
- Draft v4 / spec v4 "two job-setup outputs": the Matching category list is
  gone (D2).
