# The AI runtime, as built

**Status:** current. **Reviewed:** 2026-10-14. A description of what exists, not a plan. Where something is not built or is only decided, it says so in the same section. Rules that must not break: [../engineering/ai-runtime.md](../engineering/ai-runtime.md). The retrieval specification: [../spec/RETRIEVAL.md](../spec/RETRIEVAL.md). History of the upgrade programme that produced it: [../history/SUPERSEDED.md](../history/SUPERSEDED.md) section B.

Read it beside:

- [../verification/AI_UPGRADE_BASELINE.md](../verification/AI_UPGRADE_BASELINE.md): the W0 measurement, taken 2026-09-09 and never edited to match new behaviour.
- [../verification/VERIFICATION_RESULTS.md](../verification/VERIFICATION_RESULTS.md): the log of live vendor runs that succeeded, with dates. `backend/tests/test_no_live_vendor_claims.py` reads it, so a claim about a live call that is not evidenced there fails the suite.

## The shape

```
request / worker
      |
      v
services/evidence_retrieval ..... how Vaada, Miti and Siddhi read retrieved evidence
      |                          (each call is a tools.execute call)
      v
services/tools/execute .......... the only path an agent reaches data through:
      |                          resolve, permit, validate, cache, attempt, validate, count.
      |                          RAISES on final failure; every tool is a bounded READ.
      v
services/rag/retrieval .......... hybrid semantic + lexical, RRF fusion, hnsw.iterative_scan=strict_order
      +--> rag/reranker ......... Voyage rerank, or the lexical pass; the outcome RECORDS which ran
      +--> rag/context .......... whole chunks dropped, never truncated
      v
services/agent_loop ............. plan, execute, evaluate, reflect, verify. NEVER raises; degraded=True
      v
services/miti ................... item grading plus five isolated evaluators
      v
the aggregator .................. deterministic arithmetic, imports no router
      v
services/siddhi ................. PRISM composition, citation chokepoint, withholds uncited statements
```

Miti and Siddhi import neither `evidence_retrieval` nor `services.rag`; the orchestrator injects the readers. Nothing in that column may reach `app/evaluation/`, and nothing in `app/evaluation/` may be reached from a route or a worker; `tests/test_judge_isolation.py` asserts both directions by AST. `tests/test_ai_reachability.py` keeps the claim "this package is live" honest in both directions.

## Retrieval

`context_chunks` holds many small pieces per document with their own vectors, distinct from the profile and job vectors that rank candidates. `pickready.index_document` (Route.LAMBDA) is dispatched from a parsed resume, a published JD and a completed assessment; `pickready.reconcile_context_index` sweeps hourly and asks the table with a `NOT EXISTS`, never a timestamp. Every chunk records its embedding model, contract version and time in the same statement as the vector (NULL for a development-fallback vector); `pickready.repair_semantic_index` re-embeds NULL, retired or wrong-width vectors hourly. Defect classes this index taught (a column name written from inference; two definitions of "has text" that disagreed; a dangling tenant reference acting as a poison pill; a Lambda that could not invoke a Lambda) are in [../engineering/backend.md](../engineering/backend.md) and [../engineering/database.md](../engineering/database.md).

### The reranker

`services/rag/reranker.py`. One backend per deployment, selected by `RETRIEVAL_RERANKER` and validated against a closed set by `configured_backend()`, which raises rather than defaulting. Pilot runs `voyage` (set 2026-09-10); other environments keep the `lexical` default. **A degradation is recorded, never silent:** when a `voyage` deployment cannot reach the cross-encoder, the lexical pass runs and the outcome carries `reranker="lexical", degraded=true` with a reason. A deployment that chose `lexical` is not degraded.

### What retrieval must never do

Retrieval is a ranking prior. A candidate linked to a job is always scored, and the sufficiency signal may never lower a score, move a band or reach the aggregator (`tests/test_retrieval_scoring_isolation.py`). Scope is never broadened on acquisition retry: tenant, source type, source ids and version pin stay exactly as asked.

**Not wired (decided mandatory, register A-20):** the retrieval-time prompt-injection screen (`services/safety/content.screen_chunks`) exists and no live path calls it.

## Embeddings

`voyage-4`, 1024 dimensions. Pseudo-random vectors are refused in production: with no key the module once returned deterministic random unit vectors of the right width with no error, which hid a non-existent model id for a whole phase. Off production the dev fallback survives and warns on every call. (`Settings.is_production` is false on pilot, which runs `ENVIRONMENT=pilot`. The owner ruled that production-grade controls apply whenever real users are served (A-22); this is a pending change, REGISTER Pending change 7.)

## Concurrency

`services/locks.py`. `run_functional_assessment` takes `pg_try_advisory_xact_lock` before the credit check and the model chain; a second run returns. `uq_functional_report_link` stops two reports existing and nothing else: it fires at commit, after both runs paid for the whole chain. The key is BLAKE2b, never `hash()`; a subprocess test with a different `PYTHONHASHSEED` pins it. `services/coalescing` is in-process and cannot see across `Route.ECS` containers.

## Evaluation

`app/evaluation/`, structurally outside the product's closed model mapping.

- **`golden.py` and `datasets/`.** The retrieval set is 60 hand-authored cases against a floor of 300 (version 2026.Q3.2; Q3.1 is frozen on disk). The reasoning and decision sets are empty and stay empty: they must be human labelled, because ground truth produced by the model class being evaluated measures agreement with that model.
- **`judges/`.** Groq is the judge vendor. `qwen/qwen3.8-27b` is the only fully independent leg; the two `gpt-oss` models share a publisher with the product's models, a real weakness of the panel, recorded rather than glossed. A transport failure abstains, widening the interval instead of entering a kappa.
- **`release_gate.py`.** `NOISE_BAND` is derived from the W7.2 measurement (0.03 x 3). **Unavailable is not a pass:** with the human-labelled sets empty the gate returns `releasable=False`. Raw agreement is never gated on.

A judge result reports MCC, Cohen's kappa, the confusion matrix and the protocol, or it is not reported.

## Egress and security

- `services/egress.py`: a host allowlist, no redirect outside it, refusal by resolved address (DNS rebinding cannot walk past a hostname check).
- Support is native (`services/support`): no candidate identifier, score, grade or evaluation detail may reach `support_messages` (`tests/test_support_candidate_boundary.py`).
- A hidden-text hit in a resume is provenance, never a rejection. Embeddings are personal data at rest; `pickready.cascade_erasure` reaches vectors and caches.

## What is not built

- **Retrieval-time injection screen on every path** (A-20): decided, pending.
- **Coding answers by static checker and AI review** (S-20): decided, pending ([../spec/CODING_ASSESSMENT.md](../spec/CODING_ASSESSMENT.md)).
- **Durable execution for long workflows** (brief item W5.2): not built; no ruling (Q-31).
- **Shadow evaluation, tenant canary and drift detection** (W10), designed, not built (Q-32). AI evaluation explanations are recruiter-facing only; a candidate receives application status (A-21, decided; legal scope Q-30).
- **EEO reporting and impact ratios: refused** (owner, 2026-09-10): no jurisdiction was named, customers are Indian entities, and impact ratios would require the protected-attribute data the layer invariants refuse to infer. A published position, not a gap.
- **Retrieval quality is unmeasured.** The harness self check gates; quality does not (60 of 300 cases, none human verified, the only run a reference fixture).
- **`agent_actions` and `agent_execution_traces` have no live writer**, and every registered tool is a bounded read, so the first tool with a side effect must bring its own idempotency key and read-back resolution.
- **The only environment's data volume is not documented here.** The 2026-09-09 statement that the pilot held no candidates was a dated measurement; check the database before assuming either way, and keep seeded worked examples visible as such.
