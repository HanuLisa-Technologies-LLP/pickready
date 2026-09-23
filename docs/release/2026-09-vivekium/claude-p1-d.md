## Current hard rules, the job setup screens (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 1, work package D
(frontend). No migration. Built against PLAN-p1 sections 3.11 and 3.12; the
backend it calls (WP-A to WP-C) lands in parallel.

### CREATE JOB SAVES A DRAFT, AND PUBLISHING IS ITS OWN GATED STEP

**SUPERSEDES the 2026-07-28 "the sequence is draft, then edit, then publish"
as it was built**, where the Create Job screen called `POST /jobs` with
`publish: true` and the server published inside the create call under
`create_job` alone. The job went live with no SWOT, no skills, a DRAFT
lifecycle state and no JD index.

- **`buildJobCreatePayload(form)` has no publish argument.** The screen saves
  a draft and opens `/org/jobs/{id}`; the old published-link popup is gone.
- **`components/job-publish-card.tsx` is the one place a job goes live.** It
  reads its checklist (JD, SWOT saved, skills saved) from
  `GET /api/v2/assessments/jobs/{id}/setup`, renders the server's
  `publish_blocked_reason` verbatim, offers Publish only to a holder of
  `publish_job`, and calls `POST /jobs/{id}/publish`. The public application
  link and its copy control moved here from the create popup.
- **The client derives no JD sections.** `sectionsFromMarkdown` is deleted:
  the server is the one parser of the markdown document. What still travels
  under `jd` on create is only what is NOT in the document, `reporting_to`
  and the recruiter's seed `skills`.

### A TEMPLATE IS NOT AN AI DRAFT, AND THE SCREEN SAYS WHICH IT IS

`POST /jobs/generate-jd` returns `generated_by_ai: false` when the model was
unavailable and the server fell back to its template. The create page used to
toast "Draft ready" either way. It now shows a persistent notice, "This is a
template, not an AI draft. The AI writer was unavailable. Edit it before
saving.", until the recruiter saves their own edit, and toasts "Template draft
ready". Rule 6 applies to the SCREEN as much as to the service: template
output presented as generation is a lie about how the text was produced,
whichever layer tells it.

### THE JOB PAGE'S JD TAB IS THE SETUP, IN THE ORDER THE WORK HAPPENS

JD document, job details, SWOT, Skills, Assessment monitoring, Publish.

- **ONE JD edit path: the markdown document through `PATCH /jobs/{id}/jd`.**
  The per-section editor (`Draft`, `draftFromJob`, `saveJd`) is deleted. It
  PATCHed `/jobs/{id}` with a `jd` sections object, which re-rendered the
  document from sections and threw away the recruiter's own formatting.
- **The job's details are a separate, smaller form through `PATCH
  /jobs/{id}`** (title, department, grade, experience band, requirement
  period, narrative overrides). **The grade is sent only when it changed**, so
  a job whose grade is locked can still have its title fixed.
- **The grade field locks from the SAME server answer the publish checklist
  shows** (`grade_locked` on `/setup`, reported upward by the Publish card's
  `onSetup`), with the server's sentence as the reason. A second client-side
  notion of "locked" would be a second author for the rule.
- **The badge reads "Grade", never "Level".** `level` is gone from the `Job`
  type, the jobs list (a Grade column now), the public employer page (band
  only) and the job page header. `app/apply/[job_uuid]/page.tsx` and the
  candidate portal still read their own local types' `level`; Phase 6 owns
  those two files.
- **`JobSetupReview`, `MatchingCategoriesCard` and `JobsList` are DELETED**,
  with the setup review's test. `MonitoringPolicyCard` survived the deletion:
  it moved to the JD tab, because choosing what happens at the third warning
  is part of setting the job up.

### THE SKILLS PANEL: WHAT IT SHOWS, WHAT IT REFUSES TO SHOW

`components/job-skills.tsx` replaces the Tatva matrix editor and the Matching
Categories editor (owner ruling D1).

- **Three buckets, at most five each, as chips.** Add one line, paste a list
  (one bulk request, all or nothing on the server), rename, move, remove.
  **Behavioural is a valid move destination now**: Miti grades every skill
  from answers, so the 2026-09-21 "the server refuses a move into Behavioural"
  reason is gone. SUPERSEDES the 2026-09-21 chip editor's `MOVE_TARGETS`.
- **No priority, no evidence line, no weight and no grade word per skill.**
  Sutra's per-bucket priority and its "what good evidence looks like" line are
  hidden context the assessment reads. **Counts are spelled out** ("Three of
  five"), the proctoring report's precedent, so no digit sits on the panel;
  the test asserts the panel's text contains no digit.
- **Every refusal is shown in the server's words**: the limit, a name clash on
  add, rename or move (including a hidden soft-deleted occupant), the lock,
  a save validation naming every problem, and the outage on Save, which names
  no skill. `serverSentences` reads a string, a list, or `{message, problems |
  errors | refused}` out of FastAPI's `detail` and never paraphrases.
- **LOCKED IS A STATE, NOT A PERMISSION.** Once a candidate starts the
  assessment (D5) the panel says "Locked: a candidate has started the
  assessment. The skills and the grade can no longer change." and offers no
  control. `<ReadOnlyNotice>` is reserved for a person who lacks a bucket's
  capability on a job that is still editable, per bucket when only some are
  theirs. The lock beats a stale `can_edit`.
- **A RE-DRAFT NEVER HAPPENS WITHOUT A CLICK THAT NAMED THE TEAM'S SKILLS.**
  Every re-draft, including "Draft again" after a failed draft and the SWOT
  panel's call to action, opens one confirmation that lists
  `human_authored_names`; `confirm_overwrite` is sent true exactly when that
  list was shown non-empty.
- **A draft in progress disables editing and is polled** every three seconds.
  The server serves a stale draft as failed, so the poll ends on its own; the
  client limit is a backstop that says so in words rather than spinning.

### SWOT GENERATION IS POLLED, AND THE SKILLS FOLLOW IT ONLY ON A CLICK

- **Generate answers with a `generating` document and the panel re-reads it**
  until `generated` or `failed`, including a draft another tab started.
  `SwotAnalysisStatus` gains `generating`.
- **A 409 on Generate is the human-edit confirmation ONLY when the document
  carries human edits and the overwrite was not yet confirmed.** Any other 409
  (a JD too thin to draft from) is a refusal, shown in the server's words.
  Treating every 409 as the confirmation would ask "replace what your team
  wrote?" about a SWOT nobody wrote.
- **A save that makes a skills re-draft available offers "Re-draft skills from
  the updated SWOT"** (`skills_redraft_available` on the response). The click
  opens the Skills panel's confirmation through the page; the SWOT panel never
  calls a skills route. The call to action is shown only to a holder of all
  three bucket capabilities.

### CLOSING A JOB SAYS ACCESS STOPS

The Close Job toast said "Your candidate pipeline is unchanged", false since
the 2026-09-22 soft deletion withheld the assessment records at the instant of
closure. It now reads, in Phase 6's words: "New applications have stopped.
This job's assessment records are now withheld from the hiring team and are
deleted when the retention window ends." A marked comment under the posting
banner is the mount point for Phase 6's `AssessmentRetentionPanel`, rendered
only for a closed job; Phase 1 does not import it.

### THE MOUNT SWEEP GREW A THIRD SHAPE

`lib/api-mount-parity.test.ts` now also sweeps every template literal that
puts `/skills` or `/setup` straight after an interpolated job id: those are
assessments-router paths composed OUTSIDE the `api*()` call, which neither
earlier sweep can see. It asserts it found at least two, so it cannot pass
while checking nothing, and it deliberately does not match the v1 jobs
router's own `/jobs/setup/status-hygiene`.
