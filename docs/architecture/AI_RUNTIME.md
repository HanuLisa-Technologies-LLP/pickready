# The AI runtime, as built

RPN-AI-UP-001, as it actually exists in the tree on 2026-09-09. This is a
DESCRIPTION, not a plan: everything below is implemented, and where something is
not, this document says so in the same section rather than in a later one.

Read it beside two others, and note what each is for:

- [`../verification/AI_UPGRADE_BASELINE.md`](../verification/AI_UPGRADE_BASELINE.md)
  is the W0 measurement. It records what was true on the day it was taken and is
  never edited to match new behaviour.
- [`../verification/VERIFICATION_RESULTS.md`](../verification/VERIFICATION_RESULTS.md)
  is the log of live vendor runs that actually succeeded, with dates.
  `backend/tests/test_no_live_vendor_claims.py` reads it, so a claim about a
  live call that is not evidenced there fails the suite.

## The shape

```
request / worker
      |
      v
services/tools/execute ......... the only path an agent reaches data through:
      |                          resolve, permit, validate, cache, attempt,
      |                          validate, count. RAISES on final failure.
      v
services/rag/retrieval ......... hybrid semantic + lexical, RRF fusion,
      |                          hnsw.iterative_scan=strict_order
      +--> rag/reranker ........ voyage rerank-2.5, or the lexical pass.
      |                          The outcome RECORDS which one ran.
      +--> rag/context ......... whole chunks dropped, never truncated
      v
services/agent_loop ............ plan, execute, evaluate, reflect, verify.
      |                          NEVER raises; returns degraded=True.
      v
services/miti .................. five isolated dimension evaluators
      v
the aggregator ................. deterministic arithmetic, imports no router
      v
services/siddhi ................ PRISM composition, citation chokepoint
```

Nothing in that column may reach `app/evaluation/`, and nothing in
`app/evaluation/` may be reached from a route or a worker.
`tests/test_judge_isolation.py` asserts both directions by AST.

## Retrieval

`context_chunks` had never held a row in any environment before this work.
`pickready.index_document` (Route.LAMBDA) is dispatched from a parsed resume, a
published JD and a completed assessment; `pickready.reconcile_context_index`
sweeps hourly and asks the TABLE with a `NOT EXISTS`, never a timestamp.

Four defects made it real, and each is worth remembering as a CLASS rather than
an instance: a column name written from inference; two definitions of "has text"
that disagreed, because SQL `btrim` strips spaces only while Python `.strip()`
strips all whitespace; a dangling tenant reference acting as a poison pill; and
a Lambda that could not invoke a Lambda. `dispatch` RAISING rather than
degrading is the only reason any of them was visible.

### The reranker

`services/rag/reranker.py`. One backend per deployment, selected by
`RETRIEVAL_RERANKER` and validated against a closed set by
`configured_backend()`, which RAISES rather than defaulting. Pilot runs
`voyage`; the other environments keep the `lexical` default, because retrieval
quality is still unmeasured and a ranking change should be made where it can be
watched.

**A degradation is RECORDED, never silent.** When a `voyage` deployment cannot
reach the cross-encoder, the lexical pass runs and the outcome carries
`reranker="lexical", degraded=true` with a reason. A deployment that CHOSE
`lexical` is not degraded: reporting a chosen configuration as a degradation
would make the signal that flags a real outage fire constantly and stop being
read.

Proven live, then proven again inside the cluster under the API task role:
`reranker="voyage", degraded=false`, with the cross-encoder putting both
relevant chunks above one that fusion had ranked first.

### What retrieval must never do

Retrieval is a RANKING PRIOR. A candidate linked to a job is always scored, and
the sufficiency signal may never lower a score, move a band, or reach the
aggregator. `tests/test_retrieval_scoring_isolation.py` asserts the import graph
the way the proctoring isolation test does.

## Embeddings

`voyage-4`, 1024 dimensions, which is what the schema already expected.

**Pseudo-random vectors are refused in production.** With no `VOYAGE_CONTEXT_4`
the module used to return deterministic pseudo-random unit vectors of the right
width, with no exception and no log line. That is the mechanism that hid
`voyage-context-4` for a whole phase: a model id enshrined as a hard rule, cited
in nine modules, pinned by tests, and non-existent. Retrieval over those vectors
is not degraded retrieval; distances come back, an ordering exists, every count
is populated, and the rows are unrelated to the query. The dev fallback survives
off production and warns on every call rather than once.

## Concurrency

`services/locks.py`. A tree-wide search for `pg_advisory_lock`, `FOR UPDATE` and
`with_for_update` returned nothing before this work.

`run_functional_assessment` takes `pg_try_advisory_xact_lock` BEFORE the credit
check and the model chain; a second run returns, because the first is doing
exactly what it came to do. Five call sites dispatch that task and `Route.ECS`
gives each its own container, so `services/coalescing` cannot see across the
boundary by its own stated design.

`uq_functional_report_link` stops two REPORTS existing and stops nothing else.
It fires at COMMIT, after both runs have paid for the whole chain, and the
loser's retry then finds the row EXISTS and rewrites a report that may already
have been delivered.

The key is BLAKE2b and never `hash()`, which Python salts per process: two
containers would compute different keys, each take a lock nobody held, and both
score. A subprocess test with a different `PYTHONHASHSEED` is what pins it,
because no in-process assertion can see that failure at all.

## Evaluation

`app/evaluation/`, structurally outside the product's closed model mapping.

- **`golden.py` and `datasets/`.** The retrieval set is 24 hand-authored cases
  against a floor of 300. The reasoning and decision sets are EMPTY and stay
  empty: they must be human labelled, because ground truth produced by the same
  class of model being evaluated measures agreement with that model.
- **`judges/`.** Groq is the judge vendor. `qwen/qwen3.8-27b` is the only fully
  independent leg; the two `gpt-oss` models share a publisher with the product's
  models, which is a real weakness of the panel and is recorded rather than
  glossed. A transport failure ABSTAINS rather than guessing, so a vendor outage
  widens the accuracy interval instead of entering a kappa.
- **`release_gate.py`.** `NOISE_BAND` is DERIVED from the W7.2 measurement
  (0.03 x 3) rather than typed. **UNAVAILABLE IS NOT A PASS**: with the
  human-labelled sets empty the gate returns `releasable=False`, because a
  metric that could not be computed must block, or the first thing a broken
  harness does is wave every release through while showing green. Raw agreement
  is never gated on; it overstates chance-corrected agreement by a mean of 38.6
  points.

A judge result reports MCC, Cohen's kappa, the confusion matrix and the
protocol, or it is not reported.

## Egress and security

- `services/egress.py`: a host allowlist, no redirect outside it, and refusal by
  RESOLVED address, so DNS rebinding cannot walk past a hostname check.
- **The Intercom integration was DELETED on 2026-09-10** by owner decision, and
  the rule it carried moved rather than lapsed. Support is now native
  (`services/support`), so there is no outbound customer projection at all: a
  candidate identifier, score, grade or evaluation detail may never reach
  `support_messages`, and `tests/test_support_candidate_boundary.py` sweeps for
  it. A schema boundary is stronger than a payload allowlist because there is
  nothing left to widen.
- A hidden-text hit in a resume is PROVENANCE, never a rejection.
- Embeddings are PII at rest, so `pickready.cascade_erasure` reaches vectors and
  caches. An erasure that deletes rows and leaves vectors leaves the resume
  recoverable.

## What is NOT built

Stated here so nobody has to infer it from silence.

- **W5.2 durable execution**, **W6.3 asymmetric embeddings**, **W6.5 the
  sufficiency loop**, **W6.6 negative evidence**.
- **W10**: shadow evaluation, tenant canary, drift detection.
- **W11**: the GDPR Article 15 explanation surface, EEO reporting, impact
  ratios.
- **Retrieval QUALITY is unmeasured.** The harness self check gates; quality
  does not, and the golden set is far below its own stated floor.
- **`agent_actions` has no live writer**, `tools.execute` is importable and
  unexercised, and `agent_execution_traces` has no live writer.
- **No report carries a `model_id` or `prompt_version` column**, so a delivered
  report cannot be replayed against the exact model and prompt that produced it.
- **The only deployed environment holds no candidate data**: three demo tenants,
  thirty jobs, zero candidates, profiles, applications, reports or evaluations.
  Every acceptance criterion phrased against production volume needs a seeded
  worked example rather than traffic, and that substitution must stay visible.
