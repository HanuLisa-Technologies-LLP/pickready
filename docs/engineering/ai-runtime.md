# AI and hiring-intelligence rules

**Status:** current. **Reviewed:** 2026-10-14. Loaded on demand by `.claude/rules/ai.md`.
How the AI runtime is built: [../architecture/AI_RUNTIME.md](../architecture/AI_RUNTIME.md). Flows: [../spec/JOB_SETUP_FLOW.md](../spec/JOB_SETUP_FLOW.md), [../spec/ASSESSMENT_FLOW.md](../spec/ASSESSMENT_FLOW.md), [../spec/RETRIEVAL.md](../spec/RETRIEVAL.md). Model calls and prompts: [backend.md](backend.md).

## The agents and who owns what

| Agent | Job | Hard limit |
|---|---|---|
| Bodha | Drafts the Job SWOT (dispatched; human edits win) | Never overwrites human-edited content unconfirmed; a failure is a state |
| Sutra | Drafts Skills from the JD; writes the hidden context at Save Skills | One call at save; an outage names no skill; never renames a skill |
| Yukti | Reads resumes against the saved Skills; one rank key | Model returns words and quotes; code decides worth; failure is "not assessed" |
| Vaada | Writes each candidate's questions and rubric together | Reads the bound snapshot; persists only what is shown |
| Miti | **Sole grading authority** | Five evaluators judge authenticity, floors, hold, confidence; they grade nothing |
| Siddhi | Composes the PRISM Report | Withholds any statement without a supporting citation; no bypass flag |

## Rules that must not break

- **Assess against the snapshot.** A started conversation reads its bound `job_skill_snapshots` row, never live skill rows or "the job's latest". A digest mismatch raises `ContractIntegrityError`; a started conversation with no binding raises `ContractNotBound`. Vaada and Miti each log one digest line.
- **The model returns words; code decides what they are worth.** Grades are converted server-side; no number reaches a client.
- **Three statuses, never conflated.** `graded`; `unanswered` (a fact about the candidate: 25, Not Matching, and an unanswered Must-have fails); `not_assessed` (a fact about the platform: no score, no grade, failure class recorded). A skill with no question issued is not assessed, never failed. The hash fallback is deleted.
- **Pipeline direction.** `functional_assessment.run_assessment` orchestrates `evidence.load_inputs`, `grading.grade`, `composition.compose`, `persistence`. A stage imports the stages before it, never after (`test_assessment_pipeline_direction.py`).
- **The Must-have cap is outermost and a `min`**, from one number (`miti/caps.must_have_ceiling()`). Postgres `LEAST` ignores NULL, so the cap term is NULL unless a Must-have failed.
- **Evidence RAG enters through the tool layer only** (`services/evidence_retrieval`). Grant, stage and tenant are checked before a row is read. An execution failure degrades; a policy refusal raises. Miti and Siddhi import neither `evidence_retrieval` nor `services.rag`. Project evidence stays out of the index. Nothing numeric crosses the boundary.
- **Every registered tool is a bounded READ.** The first tool with a side effect must bring its own idempotency key and read-back resolution (`import_graph.tool_layer_problems`).
- **A degraded retrieval is recorded** (`reranker: "lexical", degraded: true`), never shown as a cross-encoder result. The sufficiency signal is a ranking and acquisition prior only; it never lowers a score or reaches the aggregator.
- **A citation must support its statement** (`siddhi.support`). "We could not check" is `weak`, never `supported`. A gate that cannot run fails its verdict.
- **Insufficient evidence is not negative evidence.** An excluded part is recorded in words and the rest renormalise. Behavioural skills are never judged from a resume.
- **Candidate Retrieval (Yukti) and Evidence RAG share primitives, not code paths.** Retrieval never decides who is scored: every non-archived link joins the pool.
- **Reproducibility is repeats with dispersion, never a seed alone.** `temperature` 0 is refused by the vendor; the aggregator is deterministic arithmetic with no model call.
- **The judge is outside the closed model mapping**, in `app/evaluation/` only. A judge result reports MCC, Cohen's kappa, the confusion matrix and the protocol, or it is not reported. An abstaining judge yields an interval or `unavailable`, never 0.0. `unavailable` is never a pass.
- **A sensitive action requires a human at any confidence.** Enforcement is the absence of a write tool; no flag auto-rejects.
- **Embedding index provenance.** Every chunk records its model, contract version and time in the statement that writes the vector. `pickready.repair_semantic_index` re-embeds NULL, retired or wrong-width vectors.
- **Evaluation honesty.** Human-labelled sets are never synthesised; quality metrics are `unavailable` until they exist. Never imply a live vendor call succeeded unless `docs/verification/VERIFICATION_RESULTS.md` records it (`test_no_live_vendor_claims.py`).

## Pending or proposed (do not describe as built)

- Retrieval-time injection screen wired on every path (A-20).
- Coding answers: static checker plus AI review, no sandbox (S-20).
- Yukti five-part model 45/15/20/15/5 (S-21): approved, decided and not built.
