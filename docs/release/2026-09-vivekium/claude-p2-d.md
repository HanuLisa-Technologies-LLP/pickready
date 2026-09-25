# CLAUDE.md section draft, Phase 2 WP-D: the ranked table on screen (2026-09-25)

Package `p2-d`. Frontend only, no migration, no backend file. New:
`frontend/components/evidence-tags.tsx`, `frontend/components/ai-match-dialog.tsx`
and their tests. Rewritten hunks: `candidate-ranking-table.tsx`,
`matching-reasoning.tsx`, the matching run in `app/(org)/org/jobs/[id]/page.tsx`,
and `lib/types.ts` (`RankedCandidate`, `RankedCandidatesResponse`,
`MatchingTaskStatus`, the new `EvidenceTag` and `AiMatchStatus`). Deleted:
`components/ai-rating-report-modal.tsx`, `components/tier-badge.tsx`, and the
legacy matcher types in `lib/types.ts` (`Tier`, `MatchingResult`,
`MatchBreakdown`, `MatchComment`, `CandidateSummary`, `MatchingCategoryResult`,
the deprecated `MATCHING_LABELS` / `MatchingLabel` aliases).

## Supersessions (mark these in place in CLAUDE.md)

- **"Candidates are listed INLINE on the job detail page" (2026-07-27): the
  column list "Name | Level | PPI Report | Resume | the rated comments" is
  SUPERSEDED.** The columns are Name | AI Match | CTC Match | Notice Period |
  Education | BGV Status | Type of Procurement | Status | Assessment | Resume |
  PRISM Report | Q&A | Validation | Team review | Decision. No Level, no Match
  percentage, no rated comments, no tier badge.
- **The 2026-09-18 section's `match_percent` column is GONE from the screen**,
  with the API field WP-C removed. Column 2 is AI Match, a word.
- **The "AI Rating & Report" cell and modal (2026-07-28) are DELETED.** Their
  five sections were the retired matcher's weighted parameters.

## Current hard rules, the ranked table on screen (2026-09-25)

### THE SCREEN RENDERS WHAT THE SERVER DECIDED, AND DECIDES NOTHING

- **The sentence above the table is `ranking_header`, verbatim.** The server
  writes it from the same facts the SQL orders by ("Resume check only. Real
  skills are tested in the assessment." until anybody on the job is
  assessed), so the claim and the order cannot drift. The client writes no
  sentence about how the table is ordered.
- **Which tags fit on a row is the SERVER's `shown_in_row`**, not a client
  slice. The row shows those, then "More in Details" when there are others.
  **The hidden count is deliberately not shown**: "+3" beside a grade reads as
  a score. The Details dialog shows every tag, positives first, each polarity
  in the server's order.
- **No client sort, no arithmetic, no number.** `candidate-ranking-table.test.tsx`
  walks the row text nodes (minus the reference code, an identifier label)
  and fails on a digit or a percent sign, and pins the server's row order
  against a fixture in no order a client could compute.
- **A tag is never colour alone.** Positive carries a teal-700 Check, negative
  an ink X, and each has an sr-only prefix ("Evidenced:" / "Not evidenced:").
- **The dialog names none of the resume check's parts (D2).** Grade word,
  tags, the server's provenance sentences verbatim and in order, the reference
  code. `ai-match-dialog.test.tsx` sweeps the six `yukti.config` component
  names in identifier, spaced and hyphenated form.
- **The applicant label sits under the procurement badge** ("Databank, not an
  applicant" / "Sourced, not an applicant"), server-worded, null once the
  candidate applies.

### A DEGRADED RUN IS SHOWN AS DEGRADED, TO THE END

- **The job page runs `POST /matching/jobs/{id}/run` and polls
  `GET /matching/jobs/{id}/tasks/{task_id}`.** The unscoped
  `/matching/tasks/{id}` and the `/jobs/{id}/run-matching` alias are not
  called from anywhere in the frontend.
- **`degraded` and `degraded_reasons` are LATCHED across polls.** The status
  route reads the stage payload only while the run is in PROGRESS; once the
  task finishes, the run-status record holds the task's return value, so the
  final poll answers the empty plan with `degraded: false`. A degradation
  never un-happens within a run, so the page keeps every reason it saw.
- **A finished run's all-pending stage list does not replace the run.** It is
  the server saying it no longer holds a stage record, not a description of
  the run. Before this, every finished run redrew its panel as "not started".
- **The panel heading says "AI matching finished, but not everything was
  checked"** and a `role="note"` lists the server's reasons verbatim; the
  completion message and toast never say "complete" for a degraded run. The
  one client sentence is for a degraded run that gave no reason, and it says
  exactly that rather than nothing.
