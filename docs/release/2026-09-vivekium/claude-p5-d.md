# CLAUDE.md section draft: Phase 5 WP5-D, the one-way grading pipeline

Package p5-d (stage 3). Migration `0130_report_immutability`. Merges
`wip/p5-b` (Miti the sole grading authority) and `wip/p5-c` (Siddhi) onto the
stage 3 base and does the integration they both deferred.

## Current hard rules, the grading pipeline (2026-09-26)

### THE PIPELINE RUNS ONE WAY, AND AN IMPORT TEST SAYS SO

`functional_assessment` was a 2,700 line LangGraph whose nodes scored, wrote
prose, ran the gate and UPDATEd the delivered report in place. It is now ONLY
the orchestrator (`run_assessment`, a persisted name with `locks.SCORING`)
over four stage modules in `services/assessment_pipeline`:

    1 evidence.load_inputs     transcript, locators, structured and coding evidence, validation
    2 grading.grade            MITI: every per-skill grade and the overall
    3 composition.compose      SIDDHI: remarks, citations, gaps, AI Score snapshot, the gate
    4 persistence              insert-only report + rows, evaluation upsert, assessment_completed

- **A stage imports the stages before it and never a later one; none imports
  the orchestrator.** `tests/test_assessment_pipeline_direction.py` walks
  module-level AND function-level imports (this repository breaks cycles by
  moving an import into a function, which is exactly how a back edge hides).
  Miti imports no Siddhi and no later stage; Siddhi imports no persistence;
  the orchestrator makes no model call and no write of its own.
- **The LangGraph, `synthesis_node`, `ppi_scoring_node`, `_gate_report`, the
  remark writer, `_matching_dimensions` and `services/report_evidence` are
  DELETED.** The remark writer lives in `siddhi.remarks`, the gate in
  `siddhi.quality_gate`, the ledger verdict in `grading.evidence_uncertainty`.

### THE REPORT IS INSERT-ONLY, IN THREE PLACES

SUPERSEDES "Reports are immutable" (2026-07-27) as a SERVICE rule: it is now a
database rule, because a rule enforced in a service is a rule the next writer
does not know about (the BGV employment trigger's argument).

- `persistence.write_report` is `INSERT ... ON CONFLICT DO NOTHING` on
  `uq_functional_report_link`; no row back RAISES `ReportAlreadyWritten`
  (a defect signal: the task checked under the lock).
- Migration 0130's BEFORE UPDATE trigger `prism_report_is_immutable` refuses
  any UPDATE on `functional_skills_reports` and `report_dimensions`, and UPDATE
  is REVOKED from `pickready_app` on both. DELETE stays (job-closure purge,
  candidate erasure).
- **The scoring task returns on an existing report under the lock**, before
  the coding hold, the credit check or any model call. A second run is a no-op
  (CONTRACT v1 "Grading").

### A MODEL FAILURE IS "NOT ASSESSED", BOUNDED BY ATTEMPTS

- Before the final attempt a run with a skill not assessed writes NO report:
  the live evaluation row records `status='not_assessed'` and `attempts`, the
  task returns, and the hourly `release_held_assessments` sweep re-dispatches
  it (it selects completed conversations with no report). The evaluators are
  not paid for (`allow_incomplete=False`).
- On attempt `MITI_NOT_ASSESSED_ATTEMPTS` (default 3) the report IS written:
  those skills carry `score NULL`, `assessment_status='not_assessed'` and the
  catalogue sentence, the overall is withheld (`overall_status='not_assessed'`,
  `overall_score NULL`, a catalogue Overall remark) when a Must-have is among
  them, `scoring_mode='miti_partial'`, the report is routed to a person, and
  `miti.not_assessed_final_report` is logged at ERROR. A CloudWatch metric
  filter and alarm (`infra/modules/observability`, `miti_not_assessed_final`)
  count the token; `tests/test_miti_not_assessed.py` pins the two together.
- **Why bounded, why not first time:** a report is permanent, so a two-minute
  outage must not become a permanent "Not assessed"; and a permanent state
  (a skill with no question issued) must not re-pay the other skills'
  evaluations every hour for ever, which is what p5-b's interim raise did.
- A radar chart draws NO axis for a skill with no score: plotting it at the
  bottom band would state Not Matching. The report route states such a
  skill, and a withheld overall, as "Not assessed" (a minimal hunk in
  `api/assessments.py` ahead of WP5-F's read model, because the old
  projection turned a missing score into Not Matching).

### THE EVALUATION IS ONE LIVE ROW

`evaluations` gains `superseded_at`, `status`, `attempts`, `contract_digest`.
0130 stamps every older duplicate superseded (NONE deleted: review
dispositions keep their `evaluation_id`) and adds the partial unique index
`uq_evaluations_live_link (link_id) WHERE superseded_at IS NULL`; the writer
upserts onto it. Its downgrade REFUSES while a report states a skill with no
score.

### WHAT THE REPORT ROW NOW CARRIES (0130)

`must_have_failed` (THE predicate, `miti.grades.must_have_failed`, written on
every insert; 0122 added and backfilled the column but the model never
declared it), `overall_status`, `ai_score_json` (Yukti's pre-assessment
snapshot, frozen: `yukti.projection.pre_assessment_snapshot` through
`siddhi.ai_score`), `generation_provenance_json` (INTERNAL: models, prompts,
templates), `category_grades_json` (Miti's bucket words, so a reader draws the
Overall chart from the grading authority instead of an unweighted mean),
`contract_version`, `contract_digest`. `report_dimensions` gains
`assessment_status` (CHECK-tied to a NULL score) and `remark_provenance`.

- **No AI Score rows are written any more.** The four matching-parameter rows
  (25 to 30 word remarks, a score read from `match_breakdown_json`) are gone;
  `services/matching_categories` went with its last reader (the p2-f hunk).
  `CATEGORY_MATCHING` is read so a historic report renders.
- `model_id` / `prompt_version` come from the run's `ProvenanceRecorder`, read
  at insert time, so a report no model wrote carries NULL for both.

### CODING IS GRADED FROM WHAT THE SANDBOX RAN

- Miti's item stage reads Phase 4's `coding_assessment.evidence` (hidden tests
  70, the code-quality review 30, CONTRACT v2 P4) through
  `evidence.load_inputs`; it calls no model for a coding item. Pending or
  unavailable is not assessed (`coding_pending`, `coding_unavailable`), code
  the sandbox never saw is not assessed (`coding_not_executed`), an empty
  submission is unanswered.
- **The read-only coding evaluator is DELETED** (the p4-4f hunk):
  `assessment_formats.evaluation.evaluate(CODING)` RAISES, the not-executed
  note, criteria, hedge rule and prompt are gone. The API still serves a
  stored `not_executed_note` for a legacy row.
- **Scoring waits for an owed coding answer.** The task calls
  `coding_assessment.submissions.scoring_hold` after the lock and the exists
  check; a held run returns, and the submission's own completion dispatches
  scoring again (v8).

### THE EVIDENCE RAG REACHES ALL THREE AGENTS THROUGH ONE ENTRY POINT

`evidence_retrieval` (the typed tool layer) is LIVE: question writing reads
`resume_passages_for_skill` per skill and `project_evidence_for_candidate`
(replacing the direct `projects.context` read); `grading` injects
`transcript_passages_for_skill` into Miti; `composition` binds
`support_passages_for_statement` into Siddhi's support check. Miti and Siddhi
import neither `evidence_retrieval` nor `services.rag`
(`tests/test_evidence_rag_wiring.py`, which also pins the four call sites the
reachability test cannot see because they live under `app/services`).

### THE CONTRACT DIGEST IS LOGGED TWICE AND MUST MATCH

`tests/test_start_locks_contract.py::test_vaada_and_miti_log_the_same_digest_for_one_conversation`
drives the REAL start route (Vaada's line) and Miti's own G1 read (Miti's line)
for one conversation and requires the same conversation id and digest.

### `assessment_completed` IS SYSTEM-ONLY

The report's writer moves an application from `assessment_in_progress` to
`assessment_completed` in the insert's transaction, through `apply_transition`.
`hiring_pipeline.MANUAL_TRANSITION_EXCLUDED` and `SYSTEM_ONLY_TARGETS` now
carry it (the p3-w1 deferred half): a hand move claimed a report that did not
exist. An application a person already moved on keeps their stage.

### SMALLER RULES

- `yukti.ranking.must_have_ceiling()` delegates to
  `miti.caps.must_have_ceiling()`: one number for one rule (CONTRACT v2 P2).
  The blend reads `functional_skills_reports.overall_score` (Miti's
  `stated_score`, NULL when withheld) and `must_have_failed`, verified.
- The ledger reads on the report path RAISE (`composition._ledger_claims` was
  a logged "no claims" on any exception). The interviewer's follow-up parser
  catches only `json.JSONDecodeError` and logs it; the silent-handler
  inventory lost `functional_assessment`, `interviewer` and
  `video/processing`.
- `siddhi.evidence.portable_node` / `KIND_PORTABLE` are deleted (they lost
  their only caller with the pre-fill); the prefill ledger is empty.
