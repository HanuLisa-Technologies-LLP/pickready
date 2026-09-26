# CLAUDE.md section draft: the golden end-to-end journey (CONTRACT v4 item 1)

Package `journey` (stage 3). No migration. Built on WP5-D (the one-way grading
pipeline) and B1 (task RLS scopes).

## Current hard rules, the golden journey (2026-09-26)

### ONE DRIVER, THREE JUDGES

`harness/golden_journey.drive` is the journey, written once. Three things run
it and none of them re-states it:

- `tests/test_golden_journey.py`, over REAL sessions (`auth._issue_session`, the
  production cookie path, no dependency override), judging every gate from a
  SECOND CONNECTION the moment it is reached;
- the harness scenario `integration_golden_journey.yaml` (step
  `drive_the_golden_journey`), over the harness `Application`, judging the end
  state through its probes;
- the CI job `golden-journey` in `.github/workflows/deploy.yml`, which runs both
  and sits in the `needs:` of both image builds.

Two copies of a forty-step journey would disagree within a release, and the
disagreement would surface as one of them passing a path the other no longer
takes.

### WHAT IS DOUBLED, AND ONLY THAT

- **The model, at the router** (`harness/doubles/golden_model.GoldenModel`),
  swapped at `llm_router.invoke_llm` and the two modules that bind the name at
  import. Keyed by the TASK TYPE the product names, never by prompt text alone,
  and every answer is built from the request (the slots, the skill, the anchor,
  the evidence refs), so an answer names what the product actually sent.
  **An unscripted call is RECORDED AND REFUSED** with `LLMUnavailableError`,
  the product's own outage signal, and the journey asserts the list is empty:
  a caller that degraded silently would otherwise read as a pass.
- **The code sandbox, at its provider seam**: `code_execution.override_provider`
  with the product's own `FakeProvider`, scripted by lookup for every program
  the journey runs (the reference and the candidate on every test, each
  starter on every hidden input). It never executes.
- **Speech to text, at `video.transcribe.run_transcription`**, the one
  function that calls Amazon Transcribe.
- Object storage is the harness's in-memory S3: infrastructure the CI job does
  not run, like the database it does.

Everything else is the product: routes, `require_capability`, RLS, the
proctoring gate, dispatch on `record`, the task bodies through
`runtime.run_task`, Miti, Siddhi, Yukti and the proctoring report.

### A DISPATCHED TASK IS RUN BY NAME, NEVER BY DRAINING

`_run_dispatched(name)` runs every recorded, not yet run dispatch of exactly
that name and refuses when there is none. Draining the whole record would run
the mail worker against a real SMTP host and would hide a missing dispatch
behind whatever else was queued. The coding submission runs BEFORE scoring,
because scoring holds while a submission is owed (v8).

### THE QUESTION MIX IS DEPLOYMENT DATA, AND THE JOURNEY SETS IT

At the default 70/20/10 every budget the skills store allows (8 to 15) carries
exactly ONE objective question, so one assessment can never hold both a
multiple choice and a fill-in-the-blank item. The journey sets
`ASSESSMENT_SHARE_PROSE/CODING/OBJECTIVE` to `0.7 / 0.1 / 0.2` for its
duration: a budget of ten is then seven prose, one coding and two objective
(one of each objective format for a non-managerial grade), through the
production `budget.mix` arithmetic, and every format CONTRACT v4 item 1 names
is in one candidate's assessment. The defaults are unchanged.

### WHAT EACH GATE PROVES (all from a second connection)

`job_created`, `jd_saved`, `swot_saved`, `skills_drafted`, `skills_saved`
(every saved skill carries its hidden context), `job_published`, `applied`
(two candidates, and applying created no assessment), `matched` (both scored;
the rival reads better on the resume alone), `invited`, `questions_written`
(every format present, none templated, no degradation recorded),
`proctoring_opened` (a session with a face baseline, consent granted),
`started` (the contract locked, the questions written against it),
`voice_transcribed` (transcript final, audio deleted), `coding_run`,
`completed` (charged once, a voice answer on record), `coding_executed`,
`graded` (one live evaluation, assessed), `reported` (one PRISM Report, the
contract's digest, not withheld, the link moved to `assessment_completed`),
`proctoring_reported`, `reranked` (the assessed candidate now ABOVE the rival,
the rival still `applied`), `profile_read` (grade words only, the proctoring
section identical to the proctoring route's, `model_id` set, no template in the
provenance, the spoken answer in the transcript).

After the run: Vaada and Miti logged the SAME conversation and digest; no
number and no em dash reached ANY response the journey received (the harness
probes' `number_hits` and `em_dash_hits`, now the one implementation both
judges call); the spoken answer was written with `aws:kms` under the bucket's
own key and deleted.

### THE NUMBER SWEEP GAINED TWO NARROW EXEMPTIONS, EACH ON A PATH

The journey is the first scenario to open a proctoring session and to serve a
fill-in-the-blank, and both surfaced numbers that are not assessment signals:

- **The proctoring client thresholds** (`config.object_confidence_threshold`
  and the rest of `proctoring.config.CLIENT_FIELDS`) under the `config` object
  of a `/api/v2/proctoring/` route only. The 2026-09-02 rule requires the
  server to hand the browser these numbers so the two never disagree.
- **A blank's position** (`...blanks[i].index`), which the fill-blank editor
  renders as a word ("Blank one") and cannot render any other way.

Neither widened `SANCTIONED_NUMERIC_FIELDS` or `ORDER_COORDINATE_FIELDS`, which
stay empty: a key called `index` or `...threshold` anywhere else is still
judged.

### `test_end_to_end_journey.py` IS NOT SUPERSEDED

It runs every situation type and asserts the provenance ledger, the A2A
contracts and the gate arithmetic row by row, none of which the HTTP journey
reaches. Its docstring now says which journey owns which half.

## What the journey found and did NOT fix (outside this package)

- **Matching progress is never published while a run is in flight.**
  `runtime.TaskContext.publish` calls `asyncio.run(status.write(...))`, and
  `run_matching`'s progress callback fires from INSIDE the task's own
  `asyncio.run`, so every in-flight publish raises "cannot be called from a
  running event loop", is swallowed at DEBUG, and leaves an un-awaited
  coroutine (the `RuntimeWarning` in every run). The job page's stage list only
  ever shows the terminal payload. Hunk in the package report.
