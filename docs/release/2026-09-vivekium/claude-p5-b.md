## Current hard rules, Miti the sole grading authority (2026-09-24, WP5-B)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 5, work package B.
No migration. Owner rulings applied: O5-1 (an unanswered Must-have is Not
Matching and counts as failed), CONTRACT v2 P5 (item scoring on the existing
`answer_evaluation` task type; a model failure is "Not assessed", never a
synthetic score).

### ONE JUDGE PER SKILL, AND IT IS MITI

`services/miti/items.py` is the ONLY code that grades a skill. It runs after
G1 and before the five evaluators, and `aggregation.aggregate(results, *,
skill_grades, ...)` scores each bucket from those grades alone: the
priority-weighted mean (weight `1 / priority`, Sutra's hidden per-bucket rank
from the locked contract) of the ASSESSED skills in the bucket.

- **SUPERSEDES the 2026-08-28 "A product CATEGORY comes from the item" rule's
  mechanism.** The category scores used to come from the evaluators'
  `per_competency` bands with `DIMENSION_TO_CATEGORY` as a fallback, while the
  report's per-skill words came from a rubric scorer in `functional_assessment`.
  Two judges, nothing making them agree. The table and that path are DELETED.
  The evaluators still decide authenticity, the dimension floors, the hold,
  confidence and a DIVERGENCE review reason (an evaluator reading two or more
  grades from Miti's word routes the report to a person); they grade nothing.
- **Skill to dimension routing is Miti-owned data**
  (`dimensions.BUCKET_DIMENSION`): Must-have and Nice-to-have to Verified
  Competence, Behavioural to Role and Context Fit, and Track Record,
  Trajectory and Authenticity (`CROSS_CUTTING`) read every skill. The contract
  carries no dimension, and `job_competencies.dimension` is no longer read.
- **The DIM constants, the Runbook D1..D5 map and the section 9.x rubric
  anchors live in `miti/dimensions.py`.** `hiring/department_models.py` and
  `hiring/situations.py` import them from there, so there is still one
  definition of each and Miti no longer imports the matrix-building side.

### THE FORMAT DECIDES THE SOURCE, AND THE STORED RUBRIC IS THE RUBRIC

- mcq and fill-in-the-blank read `assessment_answers.auto_score`; no row is
  unanswered. Coding and evidence-based answers read the evaluation with
  reasoning (`assessment_formats.evaluation`); a degraded evaluation is not
  assessed and NO second scorer stands in for it.
- Other prose on a Must-have or Nice-to-have is judged on `answer_evaluation`
  (Terra, temperature 0) against `candidate_questions.rubric_json`, the rubric
  written WITH the question the candidate was shown, inside `agent_loop` with a
  deterministic critic (a JSON object, a score 0..100). A row with no rubric is
  judged against `ppi_interview.DEFAULT_RUBRIC` and says so
  (`method=general_standard`). `tests/test_miti_items.py` reads the exact
  prompt and asserts every band of the stored rubric is in it.
- Behavioural is ONE judgement across every substantive answer about the skill
  against `items.BEHAVIOURAL_STANDARD`.
- `behavioral_assessment` lost its grading caller; it keeps question writing.
- The prompt is `assessment_answer_scoring` version 2: it now carries
  `fragments.CANDIDATE_TEXT_IS_DATA`.

### THREE STATUSES, AND THE LAST TWO ARE NEVER CONFLATED

`miti/grades.py`: `graded`, `unanswered`, `not_assessed`.

- **`unanswered` is a fact about the candidate.** Asked and gave nothing
  gradeable (empty, gibberish, no submission): `UNANSWERED_SCORE` (25), Not
  Matching, no model call. An unanswered Must-have is FAILED and caps the
  overall at `caps.must_have_ceiling()` (O5-1).
- **`not_assessed` is a fact about the platform.** The evaluation could not
  run: NO score and NO grade, the failure's CLASS NAME recorded, logged at
  WARNING as `miti.item_not_assessed`. A skill with some answers evaluated is
  graded on those and flagged `partially_assessed` (always reviewed). A skill
  with no question issued is not assessed, never failed: never asked is not
  "asked and did not answer".
- **The hash fallback is DELETED** (`_stable_score`, 45..94, measured 70% of
  hashed inputs grading Moderately Matching or better), with
  `app/scripts/backfill_functional_reports.py` (its only other user) and
  `tests/test_fallback_scoring_is_reviewed.py` (which pinned its distribution).
  `tests/test_hash_fallback_removed.py` sweeps by AST so the prose recording
  the deletion survives while any executable reference fails.
- **Interim until WP5-D lands**: when any skill is not assessed,
  `evaluate_application` stops after the item stage (the evaluators would be
  paid for and discarded) and `functional_assessment.ppi_scoring_node` RAISES
  `SkillsNotAssessed` before a single remark is written. No report exists, the
  scoring task fails, and the hourly `release_held_assessments` sweep
  re-dispatches it. WP5-D replaces this with the bounded attempts counter and
  the final "Not assessed" report (`allow_incomplete=True` is already there
  for it).
- **An unreadable input RAISES.** The locator and `assessment_answers` reads
  used to be absorbed into "everything unanswered", which is a synthetic Not
  Matching written from an outage.

### G1 ASKS THE LOCKED CONTRACT, AND LOGS THE DIGEST

**SUPERSEDES the 2026-08-29 "G1 is `require_frozen_matrix`" wiring for
scoring.** `miti.live.load_contract` calls
`assessment_contract.load_contract_for_conversation`, logs
`assessment_contract.log_digest("miti", ...)` (the same line the conversation
logs as `stage=vaada`), and runs `pipeline.contract_gate`: locked with a
`locked_at`, non-empty, at least one Must-have and one Behavioural skill. A
digest mismatch (`ContractIntegrityError`) or a started conversation with no
binding (`ContractNotBound`) surfaces as `ScorecardUnavailable` BEFORE the item
stage, so no model call and no ledger row. `hiring.scorecard` is not imported
by Miti, and scoring no longer generates questions on demand: a conversation
with no issued questions is a refusal.

### THE ALWAYS-EMPTY INPUTS ARE DELETED, NOT WIRED

- `caps.competency_threshold_caps(*, grades)`: the numeric `scores` and
  `thresholds` maps are gone. `miti/live.py` passed an empty map
  unconditionally, so the second source for section 12.1's minimum was a
  parameter waiting for a number nobody approved. `caps.must_have_ceiling()`
  is THE one cap number (71, grades Moderately Matching); the ranking blend
  imports it rather than holding a copy.
- `triangulate(report, *, sources)`: `generated` (model benign explanations)
  is gone. Nothing ever filled `EvaluationInputs.benign_explanations`; the
  deterministic stock list is what holds the two-explanation floor through an
  outage.
- `EvaluationInputs` lost `matrix`, `competency_dimensions`,
  `competency_weights`, `matrix_items`, `scorecard_approved_at`,
  `must_have_grades/_scores/_thresholds` and `benign_explanations`; it gained
  `contract`, `skill_buckets` and `skill_grades`.

### G4 TAKES THE HUMAN DISPOSITION

`miti.live.latest_disposition` reads the newest `review_dispositions` row for
the LINK and passes it to G4. Before this the disposition was a state key
nobody set, so a rescore after a person had looked still failed G4.

### A CHECK CONSTRAINT THE WRITER NEVER MATCHED

`evaluations.scoring_mode` is CHECKed to `full | degraded | stub` (0059) and
`_write_evaluation` wrote the REPORT's mode (`llm_rubric`), so every evaluation
write would have failed after paying for the whole run. Pilot held no
evaluation rows, so it never ran. A Miti run that reaches the write graded
every skill and writes `full`. The report's own mode is now `miti`
(`functional_assessment.MODE_MITI`), and any other mode forces review.

### THE MODEL IS INJECTED, ONE DOOR

`items.evaluate_skills` REQUIRES `invoke`; `miti/live.py` is still the only
module in the package that names the router (`_item_invoke` for the item
stage, `_invoke` for the evaluators), inside functions, which
`test_miti_pipeline.test_no_miti_module_reaches_a_model_except_through_the_injected_invoke`
keeps true.
