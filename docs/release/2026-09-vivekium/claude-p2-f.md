# CLAUDE.md section draft, Phase 2 WP-F: the retired matcher is deleted

Vivekium release, Phase 2, work package F (deletions, scripts, sweeps,
reachability). Branch `wip/p2-f`, built on `wip/p2-int` (WP-A to WP-E merged).
No migration.

## What changed

### THE RETIRED MATCHER IS DELETED, NOT LEFT UNREACHABLE

Once WP-C and WP-E moved the ranked table and the Candidate Dashboard onto the
Yukti columns, the old resume-stage machinery had no live caller. It is
deleted, with the tests that only defended it:

- `services/hiring/prescreen.py` (the A/B/C/Hold grade). Its name-blind
  helpers already live in `yukti/anonymise.py`, so there is one anonymiser.
- `services/longevity.py` (a nudge on `match_score`, which nothing orders on).
- `services/tiers.py` (the third copy of the rating scale). `Tier` stays in
  `models/enums.py` because `job_candidate_links.tier` is readable history.
- `services/verification/ranking.py` (the critic of the four parameters'
  25-30 word comments Yukti never writes).
- The legacy read side of `services/matching.py`: `ranking_payload`,
  `client_breakdown`, `matching_label`, `enforce_word_range`,
  `enforce_breakdown_comments`, `word_count`, `compute_overall_score`,
  `PARAMETERS`, `RANKING_*` and `_PAD_CLAUSES`.
- `prompts/matching_scoring_system.txt`.
- `agents/gates.yukti_gate` and `MIN_MATCHING_CATEGORIES`. Yukti is declared
  in `gates.UNGATED` with its reason: its evidence is grounded
  deterministically in `yukti/grounding.py` before a row is written, and it
  publishes no artifact for a gate to read. `run_gate("yukti")` still raises
  `NoGate`; being declared ungated is not a pass.
- Six task types: `rerank`, `competency_transformation`,
  `situation_classification`, `claim_extraction`, `evidence_tiering` and
  `technical_questions`, from every `llm_providers` table.
  `test_llm_task_routing.DELETED_TASK_TYPES` keeps them out of every table,
  and the router tests route through `extraction` and `email_composition`.

`agents/identity` names Yukti as `matching`, `yukti` and `hiring.ontology`,
and the activation stage `prescreen` resolves to `yukti.scoring.score_links`.
The stage KEY stays `prescreen`, because stored traces carry it (the `dna`
correlation-kind precedent).

### ONE HAND-OFF SURVIVES, AND IT IS PINNED TO ITS OWNER

`services/matching_categories.py` keeps only `resolved_categories`, for one
reader: `functional_assessment._matching_dimensions`, the PRISM AI Score
section, which Phase 5 replaces with `yukti.projection.ai_score_summary`.
`test_yukti_legacy_removed.PENDING_CATEGORY_READERS` names that file and its
owner, and `test_the_pending_hand_off_is_still_real` fails the day the reader
goes, so the module and its exemption are deleted together.

### THE SCRIPTS READ YUKTI

- `eval_report` measures the Yukti blend and the Must-have cap through
  `yukti.ranking.rank_score`, and that the report's grade and the AI Match word
  are one scale. The four-parameter measurements are gone.
- `seed_mock_data` seeds an AI Match reading through `yukti.scoring.
  apply_outcome`, the ONE writer of the Yukti columns. Its remark word ranges
  come from a seed-only `_seed_words`: padding AUTHORED MOCK text is what a
  seeder is for, padding a MODEL's output was the defect.
- `validate_stack` counts scored Yukti readings instead of breakdown keys.
- `legacy_reset` returns the Yukti columns to `pending` beside the history
  columns it already NULLs.
- `reembed`'s JD SQL mirrors `yukti.inputs.jd_text` field for field (grade,
  experience band, the `_JD_JSON_KEYS`), reading the builder's own constants.
  It never reads `jobs.level` or `reportees`.

### P2-INTERNAL HUNKS APPLIED AT INTEGRATION

- `yukti_matching_system` joins `generation_sufficiency.GATED_PROMPTS`; the
  meta-commentary floor moves to 22 with the reason beside it.
- `pickready.run_matching` returns its final stage payload and the task-status
  route reads it on SUCCESS as well as PROGRESS, so a finished run keeps its
  stages and its degraded flag instead of redrawing as an all-pending plan.
- The Candidate Dashboard's status words and sentences are read from
  `yukti.projection` and `yukti.ranking`, not retyped.
- The no-score safety scenario (version 3) also reads `/dashboard/candidates`.
- WP-E's scaffold commit (a stand-in migration and ranking module) was
  dropped in the merge; WP-C's temporary pending-schema fixture was deleted.

## New hard rules

- **A removed feature's module is deleted, and a sweep keeps it gone.**
  `tests/test_yukti_legacy_removed.py`: the deleted modules cannot be imported
  and are not on disk, the prompts are gone, live code (backend `app/`,
  frontend `app/`, `components/`, `lib/`, infra, scripts, workflows) names none
  of the retired matcher's surface, only the named Phase 5 reader touches the
  categories module, and no call site passes a deleted task type (by AST, so a
  dict key or an enum member spelled the same is not mistaken for a call).
  Tests and the harness are NOT swept: their guards name these symbols to
  forbid them. The sweep carries its own non-vacuity check.
- **`app.services.yukti` is LIVE in `test_ai_reachability`**, and
  `yukti.scoring.score_links`, `yukti.ranking.order_by_sql` and
  `rank_score_sql` are REQUIRED_CALLERS. A reading nobody calls leaves every
  row "Not checked yet" while every test passes.
- **The vocabulary fairness corpus now runs against Yukti's grounding.** The
  non-standard word must be found to evidence a skill exactly when the job
  description's own word is, and must never produce a "Not evidenced" tag.

## Supersessions (mark in place)

- 2026-08-29 "One scale, and it had silently become three": `tiers.assign_tier`
  and `matching.matching_label` are DELETED. The publishers of the four words
  are `functional_assessment.rating_label` and `yukti.ranking.grade_word`, and
  `test_grade_scale_consistency` sweeps both over the full range.
- 2026-07-30 and spec-doc5 "`tests/test_scoring.py` asserts its absence"
  (`matching.WEIGHTS`): `test_scoring.py` is deleted with the four-parameter
  mean; the absence is pinned in `test_matching.test_the_legacy_read_side_is_gone`
  and in `eval_report`'s `no_weightage_table`.
- 2026-08-18 "The ranking weight-sum check ... became a ranked-list DIVERSITY
  check": that critic (`verification/ranking`) is DELETED with the comments it
  judged.
- 2026-09-06 "The longevity signal is an internal ordering prior": DELETED.
- 2026-07-27 "Task types are `jd_generation | technical_questions | ...` plus
  the legacy `rerank | extraction` hints": `technical_questions` and `rerank`
  are deleted; `extraction` remains.
- spec-doc5 PART B "`claim_extraction` is Luna and MUST NOT EVALUATE": the task
  type is deleted (no caller); the rule is stated in `llm_providers` for every
  extraction task.
- CLAUDE.md "Where to make a change" and the package map: `services/hiring`
  no longer holds `prescreen`.

## Open items handed on

- Phase 5: replace `_matching_dimensions` with `yukti.projection.
  ai_score_summary`, then delete `services/matching_categories.py`, its entry
  in `PENDING_CATEGORY_READERS`, and `JobMatchingCategory`'s last read.
- Phase 1 (orchestrator hunks from WP-A): `swot_analysis.build_context` and
  `sutra._payload` redact the JD and profile text; then flip their two strict
  xfails in `test_ctc_never_in_prompt` to CANARY.
- Phase 5: `app/scripts/backfill_functional_reports.py` still exists here and
  Phase 5 deletes it; it reads no deleted symbol.
- Phase 7: `LLMRoleHint.rerank` (`models/enums.py`) and the `ai_score`
  artifact kind.
