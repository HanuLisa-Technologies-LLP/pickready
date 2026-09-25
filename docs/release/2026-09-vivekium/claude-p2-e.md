# CLAUDE.md section draft, Phase 2 WP-E: the Candidate Dashboard has no number (2026-09-25)

Package `p2-e`. No migration of its own. Files: `app/services/dashboard.py`,
`app/api/dashboard.py`, `app/schemas/dashboard.py`, `tests/dashboard_world.py`,
`tests/test_dashboard_{columns,numbers,workflows}.py`,
`frontend/components/candidate-dashboard/*` (`band.ts` became `grade.ts`,
`pre-screen-grade.test.tsx` became `ai-match-cell.test.tsx`).

## Supersessions to mark in place

- **Rule 1's ONE AMENDMENT (2026-09-18, `match_percent`) and spec-doc6 D8's
  numeric Vivekium Score are both SUPERSEDED by D3 (CONTRACT C8).** No number
  reaches a client on the Candidate Dashboard, with no exception. Mark the
  2026-08-29 spec-doc6 lines "Note the Dashboard adds a FIFTH vocabulary
  (85/72/60 five-band)" and every reference to "the dashboard's one number" as
  superseded: the five-band vocabulary, its cut-points, `band_for_score`,
  `BAND_*`, `score_range*` and `vocabularies_are_disjoint` are deleted, not
  deprecated.
- **2026-09-04 "Pre-Assessment Report IS the Pre-Screen Grade / AI Score"**:
  the A / B / C / Hold letter is gone from the dashboard (spec v4 forbade
  letter grades). Column 3 is AI Match, Yukti's resume-only reading.
- **spec-doc6 "Pre-Screen Grade renders muted / outline only"** survives
  unchanged and now binds column 3, AI Match.

## Current hard rules, the Candidate Dashboard reads the one rank (2026-09-25)

### TWO WORD COLUMNS, ONE SCALE, AND THE BROWSER NEVER SEES THE NUMBERS UNDER THEM

- **Column 3, AI Match**, is `job_candidate_links.yukti_status` /
  `yukti_pre_score` as one of the four `services/rating` words, or a status
  word: "Not checked yet" (pending) or "Not assessed" with a sentence per
  `yukti.config.FAILURE_REASONS`. A `legacy` reading is a word with a note
  saying it predates the evidence tags. It renders MUTED, as the early signal.
- **Column 4, Vivekium Grade**, is the word for `yukti.ranking.rank_score_sql`
  read in the same SQL statement. It is the rank the recruiter's ranked table
  sorts by, so the two surfaces can never order one job's candidates two ways.
  Its note says what decided it: "Resume check only", "Tatva Assessment and
  resume check", "Tatva Assessment only", plus the Must-have cap sentence when
  a failed Must-have capped it.
- **Every word is chosen server-side from a number that never leaves
  `services/dashboard.assemble_row`.** `DashboardRow` has no numeric
  assessment field. `test_dashboard_numbers.py` walks every schema (the
  calibration view's two schemas are the only licensed exception, and their
  fate is Phase 7's); `test_dashboard_workflows.py::test_the_page_json_carries_no_number`
  walks the RESPONSE over rows with real scores behind them, because a schema
  that declares a word can still be handed a stringified number.
- **The browser styles by a STATE the server sends** (`highly_matching`,
  `matching`, `moderately_matching`, `not_matching`, `not_checked`,
  `not_assessed`, `under_review`). It never derives a state from a word or a
  word from a number.
- **An unknown Yukti status, failure reason or confidence word RAISES.** The
  CHECK on `yukti_status`, Yukti's own writer and migration 0106's CHECK on
  `evaluations.confidence` refuse each of them, so reaching the renderer with
  one is a diverged vocabulary, and a dashboard that quietly renders it is how
  that goes unnoticed. `test_dashboard_columns` pins the reason sentences to
  exactly `FAILURE_REASONS`.

### THE CONFIDENCE DOT READ A WORD THE COLUMN NO LONGER HOLDS

`confidence_indicator` filled the dot for `high` and `medium`. Migration 0106
rewrote `medium` to `moderate` because the aggregator writes `moderate`, so
EVERY moderate assessment rendered a grayed dot labelled "Insufficient
confidence" beside a grade a recruiter could act on. The dot now reads
`miti.aggregation`'s own four constants and raises on anything else. A row
with no evaluation says "No assessment yet" instead of "Insufficient
confidence": a resume check is not an assessment.

### UNDER REVIEW STILL WITHHOLDS COLUMN 4, AND SORTS WITH THE GRADELESS ROWS

An open G3 finding (failed, no disposition) shows "Under Review" and no grade,
and its sort key is NULL so it sits with the rows that show no grade at the end
of both directions. Column 3 is NOT withheld: it is the resume's reading, and
the lock exists to stop anybody acting on the assessment.

### THE AI MATCH FILTER TAKES WORDS AND SENDS BOUNDS

`?ai_match=<grade word>` is validated against `rating.GRADES` (422 otherwise,
including the retired letters). The ranges are READ OFF
`rating.grade_for_percent` at every whole percent, never retyped, and only the
bounds travel as bound parameters. A fine-grid sweep proves the ranges and the
scale agree everywhere, which also catches a cut-point moved off a whole
number.

### SORT KEYS

`grade` (default, descending) is the rank; `ai_match` is the resume-only score
under the word; `name`, `added`, `source`, `stage` unchanged. `score` and
`pre_screen` are gone and answer 422.
