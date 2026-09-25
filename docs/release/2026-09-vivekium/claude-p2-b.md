# CLAUDE.md section draft, Phase 2 WP-B: the run, persistence, parse, sourcing (2026-09-25)

Package `p2-b`, on top of WP-A (Yukti core). Migration `0122_yukti`
(`down_revision = "0121_candidate_comms"`; the orchestrator re-chains
0122..0126 at integration). New task `pickready.yukti_score_profile`
(Route.LAMBDA, 2 attempts, no schedule, no Terraform: the same Lambda
self-invocation `parse_resume -> index_document` already exercises).

## Current hard rules, AI Matching runs through Yukti (2026-09-25)

### ONE READING PER PERSON, OF THE RESUME THEIR APPLICATION CARRIES

`matching.run_matching` is the RUN and nothing that judges. It gathers the
pool and hands `(link, the profile the link was made with)` pairs to
`yukti.scoring.score_links`, which writes the link's Yukti columns.

- **Retrieval returns CANDIDATES, never profiles.** The pool used to be keyed
  by profile id, so a person with two resumes could be scored twice and the
  "best" retrieval profile was scored against an application made with a
  different one (audit Part 1 #12). A linked candidate is ALWAYS read from
  `link.profile_id`; `yukti.inputs.candidate_input` raises on anything else.
- **Retrieval never decides who is read.** Every non-archived link joins the
  pool whatever retrieval found (the 2026-07-26 rule, unchanged), and an
  ARCHIVED link is never put back by retrieval finding the same person again.
- **The run is gated**: a published, not closed, not archived job whose Skills
  step was saved (`assessment_contract.skills_saved`). Each refusal is a
  server sentence on the progress payload (`matching.SKILLS_NOT_SAVED`,
  `JOB_NOT_OPEN`, `ALREADY_RUNNING`). The old fallback onto four generic
  categories ranked people on criteria nobody chose (audit #13) and is gone.
- **One commit, at the end.** The per-link `YUKTI_LINK` locks are
  transaction scoped, so they are held exactly until it.

### A DATABANK FIND IS SOURCED, NEVER APPLIED, AND IT HAS A HISTORY ROW

`hiring_pipeline.start_sourced(session, link, actor_user_id, remarks)` is the
ONE way a created link enters at `sourced`: the same two writes
`apply_transition` makes (mirror, then the append-only `pipeline_status`
row), for the one stage that is entered rather than reached. CREATION ONLY:
it refuses a link that already exists. No Updates feed row (`sourced` is not
an event the candidate caused). Its three callers: the matching run's
databank discovery (platform-wide over CONSENTING candidates, owner ruling;
consent re-checked from the candidate row; the MAIN resume is read when it has
text), the databank bulk upload and the single recruiter upload. The last two
used to write `applied` or skip the history row (audit #5).

Migration 0122 CORRECTED the existing rows, conservatively: `applied`, never
invited, no application answers, and a recruiter's or the matcher's
provenance (`source = 'databank'`, or `source_type = 'sourced'` from the
`direct` door). One `pipeline_status` row each, no actor. A real applicant
from before 2026-07-30 (NULL `validation_json` because the fields did not
exist) is untouched, pinned by `tests/test_yukti_migration.py`. Not reversed
by the downgrade.

### A FRESH RESUME IS READ WITHOUT ANYBODY RE-RUNNING AI MATCHING

`pickready.parse_resume` dispatches `pickready.yukti_score_profile` AFTER its
commit (beside `index_document`). `matching.score_profile` reads the profile
against every link that carries it on a published, open job with saved
skills, per job, and commits once. This REPLACES the pre-screen grade the
parse used to write. The grace-period resume replacement and the databank
upload dispatch only the parse; the job-wide `run_matching` they used to
dispatch would re-read every other candidate to rank one.

### THE PARSE SURVIVES AN EMBEDDING OUTAGE, AND KEEPS PAY OUT OF EXTRACTION

`resume_parsing.parse_resume` commits the text and the parsed fields with
`embedding = NULL` when `embed` raises, logged with its traceback, instead of
failing the task and paying for the extraction again (audit #14). The run's
`_backfill_missing_embeddings` embeds any linked profile with text and no
vector (catching `EmbeddingError` only; a dispatch failure raises). The
extraction prompt receives `compensation_guard.redact_text(resume)`, so the
CTC canary in `test_ctc_never_in_prompt.py` is a live CANARY now, not a
pending xfail.

### A DEGRADED RUN SAYS SO, IN THE SERVER'S WORDS

`matching_progress.Progress.degrade(reason)` records why a run is not a full
one; the payload carries `degraded` and `degraded_reasons`. Three sources:
the JD embedding was unavailable (keyword-only retrieval), candidates whose
reading failed transiently (`Not assessed`, retried next run), and candidates
whose earlier result was KEPT through a transient failure. New stages
`validation_fit` and `grounding` with activity events `VALIDATION_CHECKED` and
`EVIDENCE_GROUNDED`; `prescreen`/`remarks` and `EVIDENCE_READ` are gone.

### THE JOB EMBEDDING IS YUKTI'S `jd_text`, AND ITS SOURCE LIST MOVED WITH IT

`models/job._EMBEDDING_SOURCE_FIELDS` is title, department,
`assessment_grade`, the experience band and `jd_json`, because that is what
`yukti.inputs.jd_text` reads. `level` is no longer read (a pre-2026-07-28
field no form collects), so editing it no longer invalidates the vector, and
editing the grade or the band now does.

## Supersessions (mark in place)

- 2026-07-26 "Scoring reads the candidate's actual answers ... A deterministic
  hash is permitted ONLY as a flagged LLM-outage fallback": for the resume
  stage there is NO fallback reading at all now. A model failure is
  `Not assessed`.
- spec-doc6 (2026-08-29) "THE DETERMINISTIC PATH IS NOW EVIDENCE" and the
  pre-screen grade written by `resume_parsing`: SUPERSEDED. The parse no
  longer grades and matching no longer calls `prescreen`. `hiring/prescreen`
  keeps its name-blind helpers until Phase 2 WP-F deletes the grading half.
- 2026-09-06 "The longevity signal is an internal ordering prior": the run no
  longer applies it (it moved `match_score`, which nothing orders on now).
- The `pickready.run_matching` task no longer re-dispatches report synthesis
  for completed conversations with no report (PLAN-p2 NF-5, a side effect of
  ranking added after the 2026-08-16 incident). `pickready.
  release_held_assessments`, scheduled since 2026-09-09, is the one owner of
  that repair.
- 2026-07-28 "databank upload ... one `run_matching` is enqueued for the job
  at the end": SUPERSEDED, the parse's `yukti_score_profile` ranks each new
  link.

## History columns (S4)

`job_candidate_links.match_score`, `match_rationale`, `match_breakdown_json`
and `tier` are written by nothing since 0122 and are kept as readable
history; 0122 carried every scored row across as a `legacy` Yukti reading.
The legacy projections at the bottom of `services/matching.py`
(`ranking_payload`, `client_breakdown`, `matching_label`, the word helpers)
survive only for readers other work packages own and are deleted by WP-F.
