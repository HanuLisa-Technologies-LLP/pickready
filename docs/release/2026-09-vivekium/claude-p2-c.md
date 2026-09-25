# CLAUDE.md section draft, Phase 2 WP-C: the ranked table (2026-09-25)

Package `p2-c`. No migration (WP-B owns `phase2_yukti`; this package codes
against the PLAN-p2 column names). New modules: `app/services/yukti/ranking.py`,
`app/services/yukti/projection.py`, `app/schemas/ranking.py`. Rewritten:
`app/services/job_candidates.py`, `app/api/matching.py`,
`app/schemas/matching.py`, the ranked route in `app/api/jobs.py`, the strengths
hunks in `app/api/emails.py` and `app/api/outreach.py`, and the harness's
no-numbers pieces.

## Supersessions (mark these in place in CLAUDE.md)

- **Rule 1's ONE AMENDMENT (2026-09-18, `match_percent`) is SUPERSEDED.** No
  number reaches a client, with no exception (D3). The top-of-file rule, the
  2026-09-18 section's first bullet and `recruiter_columns`' docstring all said
  otherwise; `test_platform_audit.test_no_number_reaches_a_client_with_no_exception`
  replaces `test_match_percent_is_the_one_sanctioned_number`, and
  `harness/probes.SANCTIONED_NUMERIC_FIELDS` is EMPTY.
- **"The final ranking" (2026-09-04), "STAGE FIRST, never one blended
  number", is SUPERSEDED** by owner decision D2: ONE blended key. An assessed
  candidate whose assessment went badly can now sit below a strong resume.
- **"The candidate table is sorted in SQL" (2026-07-27, the grade-driven
  skills / experience / behavioural keys) is SUPERSEDED.** Still sorted in
  SQL, still a total order, but on the Yukti key, not on
  `match_breakdown_json` (which a renamed category turned into NULL for every
  row) and not by grade. `sort_keys_for_grade`, `order_by_clause` and the
  behavioural CTE are deleted, and so is the "Level" column: a grade belongs to
  the job. `grade_label` survives because four prompt builders read it.

## Current hard rules, the ranked table (2026-09-25)

### THE SORT KEY IS DERIVED IN SQL AT READ TIME, AND NEVER STORED

`yukti.ranking.rank_score_sql()` is the whole ordering rule:

- assessed (the report carries an overall): `w x overall + (100 - w) x pre`,
  `w` = `tenants.yukti_assessment_weight_pct` (default 70), or the overall
  alone when Yukti has not scored them;
- otherwise the Yukti pre score, for `scored` and `legacy` rows only;
- otherwise NULL, which sorts LAST (pending and not-assessed rows are LISTED,
  never hidden);
- then `created_at`, then `id`: a TOTAL order, so a page boundary is stable.

**Why derived:** the key depends on three facts written at different times by
different phases (the Yukti reading, Miti's report, the tenant ratio). A stored
blend goes stale on any of them and needs a "re-blend" nobody remembers.
`ranking.rank_score` is the pure twin, and `test_yukti_rank_expression.py`
evaluates the REAL SQL over a grid of every input and compares every cell.
Change one without the other and that test fails.

- **THE MUST-HAVE CAP IS APPLIED AFTER THE BLEND, OUTERMOST, AS A `min`.**
  Applied to the overall alone, a strong resume could carry a failed Must-have
  back over the ceiling (overall 74, pre 100: 79.7, Matching). The ceiling is
  read through ONE function, `ranking.must_have_ceiling()`, which reads the
  Runbook band Phase 5's `miti.caps.must_have_ceiling()` reads (71, Moderately
  Matching); replace the body with that call when Phase 5 lands.
- **POSTGRES' `LEAST` IGNORES A NULL.** `LEAST(71, NULL)` is 71, so a naive
  cap hands the ceiling, as a score, to a candidate nobody scored. The cap term
  is NULL unless a Must-have failed and is only ever applied to a non-NULL
  value; `test_the_cap_never_gives_a_key_to_a_candidate_who_has_none` pins it.
- **Every term is cast to double precision.** A REAL times a SMALLINT is REAL
  in Postgres, and a single-precision key disagrees with its twin in the last
  places, which is enough to swap two rows.

### THE ROW IS WORDS, AND EVERY FIELD IS DECLARED

`schemas/ranking.RankedCandidateOut` and `RankedCandidatesOut` carry
`extra="forbid"`. **The seven recruiter columns of 2026-09-18 never reached a
browser**: the old schema did not declare them, pydantic dropped them, and the
serializer's own test stayed green while every CTC, notice, education and BGV
cell read its empty word in production (PLAN-p2 NF-1). An undeclared key now
FAILS the request. `test_ranked_candidates_api.py` asserts the RESPONSE JSON,
because a serializer test is exactly what could not see this.

- The AI Match block: `ai_match_status`, `ai_match_label` (one of the four
  words, blended once assessed), `ai_match_status_word` ("Not checked yet" /
  "Not assessed") when there is no grade, `evidence_tags` (`text`, `polarity`,
  `shown_in_row`), `provenance` (sentences), `ai_match_stale`.
- **A tag's text is the skill's CURRENT name**, resolved per page from
  `assessment_contract.load_contract` (read ONCE per page, never per row). A
  rename moves the label and never the order. A tag whose skill left the job
  is dropped, not shown under a name nobody can find. A tag never carries its
  quote or its strength to the client.
- **Staleness is DERIVED**: the stored contract digest differs from the
  current one, or the link now points at a different resume than the one
  read. A `legacy` row always asks for a refresh.
- **Provenance names no part, no weight, no score.** "Resume check: skills,
  experience and the role, read from the resume." The ratio reaches a
  recruiter only as a word (`ranking.weight_word`: entirely, mostly, about
  half, partly, not at all).
- **The header is the server's sentence**: "Resume check only. Real skills
  are tested in the assessment." until anybody in the table has an assessment
  overall, then "Assessed candidates are ranked mostly on their Tatva
  Assessment. Everyone else is a resume check only."
- **`applicant_label` follows the pipeline STATUS**: "Databank, not an
  applicant" / "Sourced, not an applicant" while `status = sourced`, and
  nothing once they apply.

### AI MATCHING'S ROUTES ARE JOB-SCOPED AND TENANT-PROVEN

- **`GET /matching/tasks/{task_id}` is DELETED: it had no tenant check.** The
  run-status record is keyed by the task id alone, so any tenant could read any
  run by its id. `GET /matching/jobs/{job_id}/tasks/{task_id}` resolves the job
  through RLS AND requires the `matching_triggered` audit row the run route
  wrote for that job, in that tenant, with that `task_id` in its metadata.
  404 otherwise, never 403.
- **`POST /matching/jobs/{job_id}/run` refuses** an unpublished, archived or
  closed job, and a job whose Skills step is not saved ("Save the skills on
  this job before running AI Matching."), with the server's sentence. It
  dispatches AFTER COMMIT (`dispatch_after_commit`), and the audit row carries
  the handle's id.
- **`GET /matching/jobs/{job_id}/results` is DELETED** (no caller, ordered by
  the retired `match_score`), with `MatchResultOut` and `MatchResultsOut`.
- The status payload carries `degraded` and `degraded_reasons` (server
  sentences) so a degraded run is never shown as a full one.

### EMAILS SAY WHICH SKILLS WERE EVIDENCED, AND NOTHING ELSE

`api/emails._strengths_prose` and the outreach send read
`yukti.projection.strengths_for_prompt`: positive SKILL tags, by current name.
Never a grade word, a model-written tag, a quote or a number, so a candidate
cannot reconstruct an internal rating from the wording they receive.

## Open, and said out loud

- `tests/phase2_pending_schema.py` and its `conftest.py` hook are TEMPORARY:
  they add the PLAN-p2 columns to the test database and map them onto the ORM
  until WP-B's migration and models land. Both halves are idempotent and inert
  afterwards; delete them at integration.
- The Candidate Dashboard (`/org/candidates`) still serves the numeric
  Vivekium Score until WP-E lands; the no-numbers harness scenario gains its
  `read_candidate_dashboard` step then, not before.
