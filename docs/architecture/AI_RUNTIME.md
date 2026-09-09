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

- **`golden.py` and `datasets/`.** The retrieval set is 60 hand-authored cases
  against a floor of 300 (version 2026.Q3.2, grown from 24 on 2026-09-10;
  Q3.1 is frozen on disk because a version-stamped ground truth must not
  mutate). The reasoning and decision sets are EMPTY and stay empty: they must
  be human labelled, because ground truth produced by the same class of model
  being evaluated measures agreement with that model.
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

Stated here so nobody has to infer it from silence. Four of the 2026-09-09
entries were BUILT on 2026-09-10 and moved out of this section: W6.3 was found
already wired and proven live (the same sentence embeds at cosine 0.841 to
itself across the two input types), W6.5 is `rag/acquisition.py` (one bounded
broadened retry, structurally two attempts, the EMPTY_STATE_COPY contract
untouched), W6.6 is `evidence/negative.py` (the ledger's `contradicts` stance
finally has a writer; a disclaimer routes a report to a person and can move no
grade), and reports now carry `model_id` and `prompt_version` (0094, NULL for
historical and fallback rows, never backfilled).

- **W5.2 durable execution.** Not built, and the investigation says why: a
  resume path composes with `agent_actions`, and see that entry below for
  what blocks its first consumer.
- **W10**: shadow evaluation, tenant canary, drift detection. Sequential, not
  parallel: drift detection is a scheduled window over `release_gate.evaluate`
  and is small, but it needs a rolling window of judge traffic that only
  shadow evaluation can produce, and shadow evaluation is its own migration,
  task and evaluation-side comparison. The canary needs `MODEL_FOR_TASK` to
  grow a per-tenant resolution layer without breaking the comparability rule
  (two candidates on one job, one model version).
- **W11a, the GDPR Article 15 explanation surface**: not built; the right
  foundation is Siddhi's citation chokepoint and the no-numbers rule applies
  in full. **W11b EEO reporting and W11c impact ratios: REFUSED, owner
  decision 2026-09-10.** EEO because no jurisdiction was ever named and
  ReadyPick's customers are Indian entities, so a US-schema surface would be
  wrong work; impact ratios because they require collecting the exact
  protected-attribute data `hiring/layers.INVARIANTS` refuses to infer, and
  the invariant stands. These are published positions, not gaps.
- **Retrieval QUALITY is unmeasured.** The harness self check gates; quality
  does not. The golden set is 60 cases against the 300 floor, 0 human
  verified, and the only run is a reference fixture.
- **`agent_actions` still has no live writer, and the 2026-09-10
  investigation found a real reason at every candidate site.** Wiring the
  gate inside a request handler breaks endpoint atomicity, because
  `ledger.reserve` COMMITS so intent survives the process, and committing
  mid-transaction changes what a failed batch rolls back. Wiring it around
  the SMTP send does not fit either: SMTP has NO read-back, so an UNKNOWN
  (timeout mid-DATA) is unresolvable and the gate would freeze a delivery the
  current retry policy deliberately risks duplicating. Wiring it around
  report writing would be a second concurrency mechanism beside the advisory
  lock, the exact `tiers.py` violation. The right first consumer is a future
  side effect with a true read-back (a vendor API with a GET). `tools.execute`
  and `agent_execution_traces` stay unexercised for the related reason: no
  production caller makes agent tool calls yet, so `retrieve_context` (which
  now performs W6.5 acquisition) is reachable and waiting.
- **The only deployed environment holds no candidate data**: three demo
  tenants, thirty jobs, zero candidates, profiles, applications, reports or
  evaluations. Every acceptance criterion phrased against production volume
  needs a seeded worked example rather than traffic, and that substitution
  must stay visible.
