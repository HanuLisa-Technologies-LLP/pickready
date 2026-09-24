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
  `report.WITHHELD_LOG_EVENT`, pinned by a test, and a CloudWatch metric filter
  on the agent log group counts it. Rename one without the other and the alarm
  stops counting in silence.

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
