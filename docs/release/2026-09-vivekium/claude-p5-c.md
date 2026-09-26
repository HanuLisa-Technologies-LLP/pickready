## Current hard rules, Siddhi: withheld, supported, gated (2026-09-24)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 5, work package
WP5-C (Siddhi). No migration. One new setting, `siddhi_support_similarity_min`
(0.55, ASSUMPTION accepted as O5-4 in CONTRACT v2).

### AN UNCITED STATEMENT IS WITHHELD AND SENT TO A PERSON; IT NO LONGER FAILS THE TASK

**SUPERSEDES** the spec-doc5 Part A rule "Siddhi's citation enforcement is
STRUCTURAL. `Section.render` is the only path to text and it raises on an
uncited statement", and the gap_analysis docstring that said an uncited
statement "stops the report from being written". The raise was on the live
path, so one uncited statement failed the whole scoring task, the task retried
twice, and a candidate who had finished the assessment got no report while the
only trace was a log line. That is a silent failure with a loud log.

- **`citations.Report.render_collect` is the delivered path.** It renders every
  cited statement and WITHHOLDS every other one: not rendered, absent from the
  trail, returned as a `Withheld` (section, kind, item, problem code). Never the
  prose, anywhere: the finding, the log line and the trail all omit it.
- **The rule the raise protected is unchanged: nothing uncited is ever rendered
  as cited.** `Statement.problem` is the ONE rule; the raising `render` (tests,
  `check`, the worked example) and `render_collect` both read it. There is
  still no `force`, `strict` or `allow_uncited` anywhere.
- **`siddhi.report.compose_prism` is the one composer.** A withheld statement
  produces an `uncited_statement` (or `fabricated_citation`, for a ref outside
  the evidence set) finding, flags the report for human review, and logs
  `prism.statement_withheld_for_review` at ERROR. That token is
  `report.WITHHELD_LOG_EVENT`, and a CloudWatch metric filter on the AGENT log
  group (the scoring task is Route.ECS) counts it, with an alarm at more than
  zero in five minutes: one withheld statement is a prompt regression or a
  wiring defect, and both repeat on every report until fixed.
  `tests/test_prism_withheld_alarm.py` reads the Terraform and the constant
  and compares them. Rename one without the other and the alarm stops
  counting in silence.

### A CITATION MUST SUPPORT THE STATEMENT, NOT MERELY EXIST

`siddhi.support` runs on the prose a model writes FROM evidence (rated
remarks, the Overall remark, gap probes: `report.SUPPORT_CHECKED`, a closed
table). Deterministic first: an invented proper noun is `unsupported` (the same
`remarks.invented_terms` the writer is held to, so writer and checker cannot
disagree), a shared content term outside the skill's own name and the report's
own vocabulary is `supported`. Semantic second, only when the anchor fails:
voyage-4 cosine against `siddhi_support_similarity_min`.

- **"We could not check" is `weak`, never `supported`.** No real embedding
  model (`embeddings.is_semantic()` false, where `embed` returns pseudo-random
  vectors) or an embedding failure records `semantic_check_unavailable`. An
  outage is not evidence against the candidate either, so it is not
  `unsupported`.
- **A statement citing only the SEARCHED record is `weak` by definition.**
- **Grade statements and catalogue sections are NOT support-checked.** A grade
  word is a verdict, not a paraphrase; its check is the quality gate. Catalogue
  text no model wrote would flag every report, and a check that fires on every
  report is a blanket verdict.
- **`unsupported` flags review and carries a words-only marker**
  (`support.SUPPORT_NOTES`); `weak` is recorded and changes nothing else. No
  similarity number is ever stored: the verdict is a level and a reason code.
- **The threshold is a cosine, so a value outside (0, 1] refuses to boot.**
  Above one every paraphrase is `unsupported` and every report goes to review;
  at or below zero every unrelated sentence is `supported`. Both would read as
  the check working, and `55` is what somebody types meaning `0.55`.
- **A third look can only LIFT `unsupported` to `weak`, never to
  `supported`.** A statement its own citation does not support is looked up
  once in the candidate's OTHER answers through
  `evidence_retrieval.support_passages_for_statement` (the typed tool layer,
  `AGENT_PPI_REPORT`, `STAGE_REPORTING`; Siddhi never imports `services.rag`
  or the retrieval entry point: the orchestrator passes it in as the
  `support.PassageSource` seam). A passage that supports it records
  `supported_by_uncited_passage` with the passage's `context_chunks:<id>`
  locator and its own words-only marker, and does NOT route to review: the
  claim is the candidate's, only its citation is wrong. Lookups are
  sequential on the scoring session and capped at `PASSAGE_LOOKUP_LIMIT` per
  report; a degraded or skipped lookup is recorded on the verdict
  (`passage_check`), never read as "looked and found nothing". A policy
  refusal from the tool layer RAISES: it is a wiring defect identical on every
  retry.

### THE GRADE CHECK COMPARES TWO SOURCES NOW, SO IT CAN FIRE

The Siddhi gate was handed `"grades": graded, "miti_grades": graded`, one
dict under two names, so "the report states the grade scoring recorded" could
not fire for any report ever written, with a comment beside it saying so.
`siddhi.quality_gate.evaluate` reads the grades the document RENDERED out of
the stored trail and compares them with Miti's grades passed in from scoring.
The Siddhi gate gained `grade_missing_from_report`, `grade_without_scoring` and
`overall_grade_disagrees_with_scoring`, and the quality gate adds
`grade_restated_differently`. **A gate that cannot run FAILS its verdict**
(`gate_unavailable`, high) instead of the passing verdict the old adapter
returned; the report is still written, flagged.

### A REMARK SAYS HOW IT WAS WRITTEN, AND PROVENANCE NEVER CLAIMS A CALL

The remark writer moved into `siddhi.remarks` and returns
`Remark(text, source)`: `model` (the critic accepted it), `template` (the
fallback: recorded, flagged, marked in words), `catalogue` (a deliberate fixed
sentence: an unanswered skill, or `NOT_ASSESSED_REMARK`). Gap probes carry
`probes_source` (`model | template | empty_state`). A recorder
(`assessment_pipeline.types.ProvenanceRecorder`, structural interface
`siddhi.provenance.ProvenanceSink`) is told about a model call ONLY after the
critic accepted it, so `model_id` and `prompt_version` are `None` for a report
no model wrote. **SUPERSEDES** the 2026-09-10 rule "Reports carry `model_id`
and `prompt_version`, written only for a model-backed run": the condition was
the scoring MODE, so a run whose every remark fell back still named the model.

### LOCATORS ARE DURABLE AND THE TRAIL HAS A READER

Evidence nodes carry `locators` (`assessment_messages:<id>`,
`candidate_questions:<id>`, `context_chunks:<id>`); refs stay positional and
report-local. Trail version 2 adds items, locators and support verdicts;
`siddhi.trail.read_trail` reads both versions and reports `available=False`
for a report with none. `resolve_evidence` turns locators into text at READ
time, scoped to the report's own application (a locator to another
candidate's message resolves to nothing), and `view` is the client shape: no
ref, locator, id or position. Passages a grading judgement read join an item's
grounding as `KIND_PASSAGE`.

### DELIVERY IS G4 THEN THE PDF, AND THE OTHER EXPORTS ARE GONE

`siddhi.delivery` keeps `gate_delivery`, `clearance_or_reason` and
`prism_pdf`; `prism_json`, `prism_email_body`, `prism_attachment` and
`deliver` had no consumer and were deleted. `prism_pdf` takes the
`DeliveryClearance` as its first argument and a clearance can only be minted by
`gate_delivery`, so the renderer is unreachable from the route without G4. The
PDF route answers 409 with `PDF_BLOCKED_REASON` while a flagged report has no
human disposition; the on-screen report is deliberately not gated.

- **A disposition must POSTDATE the report.** Dispositions hang off the
  application, and an evaluation (and a decision on it) can exist before the
  report does; a person who decided on that earlier state has not read this
  report, so `created_at < synthesized_at` does not clear it.
- **G4 is a REQUIRED CALLER, not a hope.** `test_ai_reachability.REQUIRED_CALLERS`
  names `delivery.gate_delivery` and `delivery.prism_pdf` (orchestrator hunk,
  part 2), so a PDF route that goes back to calling the renderer directly
  fails the build instead of shipping every flagged report ungated, which is
  what happened for the whole life of the module before this release.
- **An absent report is refused, never cleared.** `getattr(None,
  "needs_human_review", False)` reads as "nothing to review", so
  `gate_delivery(session, None)` raises instead of minting a clearance for a
  document that does not exist.

### SMALLER RULES

- `synthesis.compose` became `assemble` (no rendering);
  `require_frozen_matrix` and Siddhi's `ScorecardUnavailable` were deleted
  (no live caller; G1 runs against the locked contract in Miti).
- Gap probes are checked with the DELIVERED-document number rule
  (`siddhi.numbers.scan_text`), not the interviewer's: a probe quoting "30%"
  used to pass and then make the PDF refuse after the report was written.
- An exchange's row ids never reach a prompt: the probe payload sends question
  and answer only.
- The word contracts live in `siddhi.remarks` (`SKILL_REMARK_WORDS`,
  `PROBE_REMARK_WORDS`); `verification.ppi_report` reads them there.

### THE SURFACE WP5-D AND WP5-F CALL (exact, so neither re-derives it)

This subsection is the interface record, not a rule; the orchestrator may move
it to `docs/spec/` when assembling CLAUDE.md.

**The dimension row Siddhi reads** (one per contract skill, contract order):
`category` (`must_have | nice_to_have | behavioural`, plus legacy
`matching | technical`), `name`, `grade` (the grade WORD Miti decided, or
absent), `assessment_status` (`not_assessed` renders `Not assessed` and is
neither a gap nor a failed Must-have), `score` (INTERNAL, optional, read only
when `grade` is absent), `remark`, `remark_provenance`
(`model | template | catalogue`), `evidence_confidence`, `ordinal`.
`remark_provenance` is not a column until WP5-D's migration adds it
(`report_dimensions.remark_provenance`, CHECK in those three words); until
then the orchestrator strips it before `ReportDimension(**row)` and the
`template_output` finding carries the fact.

**WP5-D (the grading orchestrator) calls, in this order:**

1. `siddhi.remarks.bounded_remark(session, name, evidence, *, rating, provenance) -> Remark`
   per graded skill; `remarks.unanswered_remark(name)` for an unanswered one;
   `remarks.not_assessed_remark()` for a not assessed one. `Remark.row_fields()`
   is `{"remark", "remark_provenance"}`. The Overall remark is
   `bounded_remark(session, "this candidate's overall suitability", evidence,
   *remarks.OVERALL_REMARK_WORDS, rating=<overall word>, provenance=...)`, and
   its `.source` is passed to the composer as `overall_remark_source`.
2. `siddhi.inputs.evidence_by_item(questions=, competencies=, answers=, answer_records=)`:
   `competencies` needs `.id` and `.name` (`ContractSkill` satisfies it),
   `answer_records` is `assessment_pipeline.evidence.answer_records(...)`.
3. `gap_analysis.build_gap_groups(session, dimensions, evidence_by_item, *, provenance)`
   returns `{"focus_summary", "must_have_cap_applied", "groups"}`; every entry
   carries `probes_source` (`model | template | empty_state`).
4. `siddhi.report.compose_prism(dimensions=, evidence_by_item=, gap_groups=,
   focus_summary=, overall_summary=, overall_grade=, overall_remark_source=,
   validation=, validation_points=, claim_evidence=, extra_nodes=, passages=,
   embed=support.semantic_embedder(), passage_source=...) -> ComposedPrism`.
   `passages` is `{skill name: [{"chunk_id", "content"}]}` from
   `SkillGrade.passages` (a passage without a chunk id RAISES: it could never
   be found again). `passage_source` is
   `support.statement_passage_source(evidence_retrieval.support_passages_for_statement,
   session, tenant_id=<job tenant>, link_id=<link>)`.
   Store `gap_analysis_json = {**groups, "siddhi": composed.siddhi_namespace()}`;
   OR `composed.needs_human_review` into the row and append
   `composed.review_findings()` (issue, location, severity, recommendation; no
   prose) to `review_findings_json`.
5. `siddhi.quality_gate.evaluate(gap_analysis_json=<the dict from 4>,
   dimensions=, overall_summary=, validation=, validation_source=<the
   application's own validation_json>, evidence_by_item=,
   miti_grades=quality_gate.miti_grades_from(miti.skills),
   miti_overall_grade=<Miti's overall word, or None when not assessed>) -> Verdict`.
   `not verdict.passed` flags review; its findings join the row's.
6. `siddhi.ai_score.snapshot_for_report(session, link_id, source=yukti.pre_assessment_snapshot)`
   returns an `AiScoreSnapshot` or None; store `snapshot.as_json()` on
   `ai_score_json`, OR `snapshot.needs_human_review`, append
   `snapshot.review_findings()`.
7. The run's `assessment_pipeline.types.ProvenanceRecorder` (hunk 2 in
   `p5-c-orchestrator-hunks.patch`) is threaded into 1, 3 and Miti;
   `model_id()`, `prompt_version()` and `as_json()` fill `model_id`,
   `prompt_version` and `generation_provenance_json`.

**WP5-F (the API and read model) calls:**

- `siddhi.trail.citation_view(session, report.gap_analysis_json, *, link_id,
  chunk_source_ids=(link_id, link.profile_id))` on the TENANT session, behind
  the transcript capability and `require_readable`. It answers
  `{"trail_available": bool, "statements": [{"section", "item", "kind",
  "text", "support": <words or None>, "evidence": [{"kind", "question",
  "excerpt"}]}]}`: words only, no ref, locator, id or position.
- `siddhi.trail.read_trail(report.gap_analysis_json).remark_support_note(section, item)`
  is `DimensionOut.support_note`; `siddhi.remarks.remark_note(row.remark_provenance)`
  is `DimensionOut.remark_note` (the template marker).
- `siddhi.delivery.clearance_or_reason(session, report)` gives the payload's
  `pdf_blocked_reason` (None when the PDF is available); the PDF route runs
  `gate_delivery(session, report)` then `prism_pdf(clearance, report_out, ...)`
  and answers 409 with `PDF_BLOCKED_REASON` on `DeliveryBlocked`.
- `siddhi.ai_score.read_snapshot(report.ai_score_json)` types the stored AI
  Score section; `None` is a report written without one.
