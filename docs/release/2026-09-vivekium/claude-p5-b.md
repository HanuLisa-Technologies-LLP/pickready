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
- The prompt is `assessment_answer_scoring` version 3: version 2 added
  `fragments.CANDIDATE_TEXT_IS_DATA`, version 3 tells the judge what the
  related-passages field is (see below).

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
logs as `stage=vaada`), and runs `hiring.gates.contract_gate`: locked with a
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

### G1 IS STATED ONCE, IN `hiring/gates.py`

`contract_gate` lives beside G2 to G4 and `run_gate(G1)` dispatches to it. The
Miti package defines no gate of its own, asserted by AST
(`test_miti_live_contracts.test_g1_for_grading_is_the_gates_modules_contract_gate`).
`scorecard_gate`, the frozen-MATRIX form of G1, is marked RETIRING in the same
module: grading no longer asks it and its one remaining caller is
`hiring.scorecard.require_frozen_matrix`, reached by question generation until
the assessment phase moves that onto the contract. PLAN-p1 section 7 deletes
both together with the scorecard read half.

### ONE PREDICATE FOR "FAILED MUST-HAVE"

`grades.must_have_failed(skill_grades)` is the definition (PLAN-p5 3.2.5):
graded or unanswered AND Not Matching. The aggregate's flag and
`MitiResult.must_have_failed` both call it; the report column
`functional_skills_reports.must_have_failed` (Phase 2's migration) must be
written from it, and the ranking blend reads that column plus
`caps.must_have_ceiling()`, never a second number.

### RELATED PASSAGES REACH THE JUDGE THROUGH AN INJECTED READER, NEVER A SCORE

`items.evaluate_skills(..., passages=)` takes a `PassageReader`, the exact
signature of `evidence_retrieval.transcript_passages_for_skill` (wip/p5-e).
Asked once per Must-have or Behavioural skill (`items.RETRIEVED_BUCKETS`),
lazily, only when there is something substantive to judge, with the skill's
OWN answer message ids excluded, so the judge is never shown the answer it is
grading dressed up as corroboration.

- **The passages ride in their own payload field**
  (`items.RELATED_KEY = "other_answers_bearing_on_this_skill"`), never merged
  into `answer`. `assessment_answer_scoring` is version 3 and tells the judge
  the field is context and must not raise a score by itself.
- **Every skill records `retrieval`**: `used`, `degraded` (with the class
  name) or `not_requested` (no reader supplied, or a Nice-to-have). None is an
  explicit caller choice, never "retrieval found nothing".
- **A degraded read never moves or fails a grade**, pinned by comparing the
  degraded run with a run that had no reader. A WIRING refusal from the reader
  (a tool the agent does not hold) propagates, because a caller that never
  retrieves is a defect.
- `miti/items.py` imports neither `services.rag` nor `evidence_retrieval`
  (AST test), so `test_retrieval_scoring_isolation` holds by construction.
- **NOT WIRED ON THE LIVE PATH YET.** `evidence_retrieval` is not on this
  branch; WP5-D passes it (see the interface below).

### PROVENANCE COMES FROM WHAT RETURNED, AND A WITHHELD OVERALL HAS NO NUMBER

- `MitiResult.model_calls()` returns `(task type, prompt)` for every model
  call that produced a usable value, from `items.MODEL_PROMPT_FOR_METHOD`
  (objective and unanswered items made no call; a failed judgement produced
  nothing) plus the five evaluators when at least one returned a usable band.
  The report's `model_id` / `prompt_version` / generation provenance must be
  built from this, never from a list of prompts a run might have used.
- `Aggregate.stated_score` is `delivered_score`, or None when
  `overall_status == "not_assessed"`. `delivered_score` is still computed and
  recorded (it is the working), but writing it as the overall beside a "Not
  assessed" word would state a judgement nobody made.
- `_divergent_skills` no longer skips a band it cannot read: `parse_result`
  admits only known bands, so an unknown one is a caller defect and raises.

### THE INTERFACE WP5-D (ORCHESTRATION, PERSISTENCE) AND WP5-F (API) CALL

`miti.live.evaluate_application(session, *, job, link, conversation_id,
questions, answers, locators, structured, subject_names=(),
allow_incomplete=False, invoke=None, item_invoke=None, passages=None) ->
MitiResult`

- `job`: needs `.id`, `.tenant_id`, `.title`. `link`: `.id`, `.candidate_id`.
- `questions`: this link's `candidate_questions` rows
  (`ppi_interview.load_for_link`), each carrying `competency_id`, `prompt`,
  `rubric_json`, `question_type`, `weight`, `payload_json`, `resume_anchor`.
- `answers`: `{str(question.id): [answer text, ...]}`; `locators`:
  `assessment_pipeline.evidence.answer_records(session, link.id)`;
  `structured`: `{str(question.id): AssessmentAnswer}`.
- `passages`: pass `evidence_retrieval.transcript_passages_for_skill` as-is.
- RAISES `live.ScorecardUnavailable` when G1 cannot be met (no binding on a
  started conversation, stored digest disagrees with the snapshot, unlocked,
  empty, missing Must-have or Behavioural). Nothing has been called or
  written at that point. Not transient: do not retry it as an outage.
- `allow_incomplete=False` (default): if any skill is `not_assessed` the run
  stops after the item stage, `result.outcome is None`, and no evaluator is
  paid for. `True` runs everything and the aggregate carries
  `overall_status="not_assessed"` when a Must-have is among them. Use True only
  for the FINAL attempt (PLAN-p5 P5-D4, `miti_not_assessed_attempts`).
- It writes, in the caller's transaction: ledger rows through
  `record_answer_evidence` (savepointed, never raising), and
  `assessment_answers.ai_evaluation_json` for evidence and coding answers the
  evaluation graded. The caller commits.

`MitiResult`:

| Member | Meaning |
|---|---|
| `contract`, `contract_version`, `contract_digest` | the locked contract graded against; persist version and digest on the report and the evaluation |
| `skills: tuple[SkillGrade, ...]` | one per contract skill, contract order |
| `complete`, `not_assessed_skills` | no skill `not_assessed`; the names otherwise |
| `must_have_failed` | THE predicate; write it to `functional_skills_reports.must_have_failed` |
| `outcome` / `aggregate` | None when the run stopped after the item stage |
| `outcome.gate_results`, `outcome.deliverable` | G1 to G4; G4 already read the latest `review_dispositions` row for the link |
| `review_reasons` | aggregate reasons plus unreadable-evidence note |
| `model_calls()` | provenance, see above |
| `unresolved_evidence`, `evidence_count`, `competency_sources` | as before |

`SkillGrade` (`miti/grades.py`, frozen): `skill_id`, `name`, `bucket`,
`priority`, `status` in `graded | unanswered | not_assessed`, `score`
(INTERNAL int, None exactly when not assessed), `grade` (word, None exactly
when not assessed), `partially_assessed`, `items` (`ItemEvaluation`:
`question_id`, `status`, `score`, `weight`, `method`, `failure` as a class
name), `used_answers` (in-process only, for the remark writer), `passages`
(`PassageRef`s for Siddhi's `KIND_PASSAGE` nodes; locators via `.locator`),
`retrieval`, `retrieval_reason`. `as_dict()` is the INTERNAL working
projection (scores, passage LOCATORS, no passage text) for
`evaluations.competency_scores`; it is never a client payload.

`Aggregate` fields the writer reads: `category_grades`, `overall_grade` ("" when
withheld), `overall_status` (`graded | not_assessed`), `stated_score` (write
THIS as `overall_score`), `must_have_failed`, `must_have_cap_applied`,
`applied_caps`, `insufficient_skills`, `insufficient_dimensions`,
`confidence`, `needs_human_review`, `review_reasons`, `as_dict()`,
`client_projection()` (words only).

`report_dimensions` rows: one per `SkillGrade`, `score = grade.score` (NULL
when not assessed, which needs PLAN-p5 3.10's migration), `assessment_status =
grade.status`, `required_level` NULL. `evaluations.scoring_mode` is CHECKed to
`full | degraded | stub`: a complete Miti run is `full`; a final-attempt
incomplete run is `degraded`.

### THREE MORE SILENT SUBSTITUTIONS, CLOSED IN THE COMPLETION PASS

- **The contradiction read-back RAISES.** `_uncertainty_from_evidence`
  swallowed ANY exception and answered "no contradiction", a silent PASS on
  the one signal that routes a disagreeing record to a person, and it caught
  a `TypeError` as readily as an outage. Miti reads the same ledger twice in
  the same transaction before it runs, so a failure there is a defect; a
  failed SQL statement has already aborted the transaction the report would
  be written in anyway. `test_miti_evidence_wiring` pins the reversal.
- **An unscored AI Score parameter is OMITTED, never read as five.**
  `_matching_dimensions` defaulted a missing `match_breakdown_json` score to
  5 of 10, a Not Matching row written from nothing. `_matching_score` returns
  None for absent, boolean, non-numeric and non-finite values; the row is
  dropped and `functional_assessment.ai_score_parameter_unscored` is logged.
- **The report writes `stated_score`, and refuses a withheld overall BEFORE
  the AI Score section or any remark is paid for.** `synthesis_node` used
  `delivered_score`, which is the working and exists even when the overall is
  "not assessed". The refusal is `SkillsNotAssessed`, the same exception the
  scoring node raises, so WP5-D replaces both with its final-attempt path.
- `infer_grade` (an LLM grade inference with no caller and a bare
  `except Exception` fallback) is DELETED. `infer_grade_fallback` survives for
  pre-0014 rows only.

`tests/test_miti_report_rows.py` runs `run_assessment` itself on committed
rows and reads back from a second connection: every PPI report row carries
Miti's skill score, the overall is `stated_score`, the evaluation row passes
the `scoring_mode` CHECK with G1 to G4 in order; a judging outage commits no
report, no report dimension and no evaluation.

### STILL OPEN, FOR WP5-D, AND WHY IT MATTERS

- **Do not deploy WP5-B without WP5-D.** Until the attempts counter exists,
  `ppi_scoring_node` raises `SkillsNotAssessed` on every incomplete run and the
  hourly `release_held_assessments` re-dispatches it with no bound. A skill
  with NO question issued (`no_question_issued`) is a permanent state, so that
  application would re-run, and re-pay the item evaluations for its other
  skills, every hour. Pilot holds zero conversations (CONTRACT v3), so nothing
  is exposed today.
- The coding branch still reads the code (`assessment_formats.evaluation`,
  not executed). PLAN-p5's 70 / 30 hidden-tests and quality split needs Phase
  4's `code_execution.evidence_for_answer`, which is not on this base; coding
  ships disabled (CONTRACT v2 P4), so no coding question is served until it is.
- **The Overall radar chart averages report rows UNWEIGHTED**
  (`functional_assessment.build_radar_charts._mean`), while Miti's category
  grade is the `1 / priority` weighted mean. On a bucket whose skills differ
  in priority the chart's band can disagree with the category word beside it.
  WP5-D should persist `aggregate.category_grades` on the report and WP5-F's
  read model should draw the Overall chart from them, never recompute.
