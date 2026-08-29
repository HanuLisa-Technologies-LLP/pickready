# Verification pending

Every claim in this repository that **cannot be proven in this phase**, and the
exact command that would settle each one.

spec-doc6 decision D6: there is no Anthropic key and no Voyage key in this
phase. The standing rule that "a provider is not proven to work without a
realistic request succeeding against it" therefore cannot be satisfied, and
nothing in this repository may imply that it has been. The honest framing for
everything listed below is: **built and tested against recorded fixtures and a
stub provider; not executed against a live provider.**

This file is append-only in practice. Add a row when you write code whose
correctness depends on something you could not observe, and delete a row only
when the command in it has actually been run and its result recorded in
`VERIFICATION_RESULTS.md`.

## How to use this file

1. Obtain `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` and export them.
2. Run the rows in the order the **Order** column gives. The order is not
   cosmetic: re-embedding changes what retrieval returns, and the pre-screen
   regrade reads retrieval, so a regrade run before the re-embed grades every
   candidate against an index that is about to change underneath it.
3. Record the outcome of each row, pass or fail, with the figures it printed.
4. A row whose command fails stays in this file. A row is removed only by a run
   that succeeded.

## Outstanding claims

| Order | Claim that is unproven | Owner | Command that settles it | Expected evidence |
|---:|---|---|---|---|
| 1 | `voyage-context-4` accepts the request shape `services/embeddings.embed` sends: `input_type`, an explicit `output_dimension` of 1024, and a `data` array carrying an `index` per row. Derived from published API documentation, never observed. | `app/services/embeddings.py` | `python -m app.scripts.reembed --evaluate` | A JSON block with `pairs`, `recall_at_k` and `mean_reciprocal_rank`, rather than an `EmbeddingError`. |
| 2 | Re-embedding actually improves retrieval. The current index mixes BGE-M3 and Voyage vectors in the same `vector(1024)` columns, and "search quality will be mixed until they are redone" is a reasoned expectation, not a measurement. | `app/scripts/reembed.py` | `python -m app.scripts.reembed --evaluate > before.json`, then row 3, then `python -m app.scripts.reembed --evaluate > after.json` | Two `recall_at_k` and `mean_reciprocal_rank` figures over the same fixed 61-pair evaluation set, before and after. |
| 3 | The re-embedding run completes: 73 vectors across two tables, shadow-written, dimension-verified and swapped, with the provenance stamped. Exercised end to end against a hashing test double on a scratch clone; never against Voyage. | `app/scripts/reembed.py` | `python -m app.scripts.reembed --confirm` | `swapped 73 vectors`, then `python -m app.scripts.reembed --status` reporting `pending 0` for every target and `stale 0`. |
| 4 | Gate G1 blocks evaluation without an approved scorecard **on a live path**. It does not today: `scorecard_gate` is called only from `app/services/miti/pipeline.py:290`, which nothing under `app/api/` or `app/workers/` imports. The legacy reset's archive-and-mark step depends on this and refuses until it is true. | spec-doc6 phases 3 to 5 | `python -c "from app.scripts.legacy_reset import inspect_gate_wiring as g; print(g())"` | `enforced: True`, with at least one `app.api.*` or `app.workers.*` module in `reachable_from`. |
| 5 | The pre-screen regrade produces a grade for every candidate with a resume under the new framework. Built, unit-tested, and dry-run against real data; **no candidate has been regraded.** | `app/scripts/legacy_reset.py` | `python -m app.scripts.legacy_reset --regrade --confirm` | Per-job `scored` and `remaining` lines, then `SELECT count(*) FROM job_candidate_links WHERE match_score IS NULL AND profile_id IN (SELECT id FROM profiles WHERE resume_url IS NOT NULL)` returning 0. |
| 6 | `claude-haiku-4-5-20251001` returns the JSON object shape `matching._parse_scoring_response` expects when the router seeds the assistant turn with `{`. The prefill behaviour is derived from the Messages API documentation. | `app/services/llm_router.py` | `python scripts/verify_live.py` (owned by the vendor-verification phase) | A pass row for the Haiku extraction path. |
| 7 | `claude-sonnet-5` returns the reasoning shape the dimension evaluators expect. Same basis, same gap. | `app/services/llm_router.py` | `python scripts/verify_live.py` | A pass row for the Sonnet reasoning path. |
| 8 | The failure classification is right about the vendors: 401 and 403 are terminal, 429 carries `retry-after`, 5xx is transient. Asserted against synthetic `httpx` errors in `tests/test_reembed.py` and `tests/test_llm_router.py`; the vendors' actual responses have not been seen. | `app/config/llm_providers.py` | `python scripts/verify_live.py` (its 401 branch uses a deliberately invalid key, and its 429 branch a deliberately tiny timeout) | A pass row per failure branch. |
| 9 | The object store reconciliation finds no orphan in either direction. **Not performed:** `S3_BUCKET` is empty in every environment reachable from here, so the bucket half of the check has never run. All 44 profile rows that carry a resume point at a legacy Cloudinary URL rather than an `s3://` URI. | `app/scripts/legacy_reset.py` | `S3_BUCKET=<bucket> python -m app.scripts.legacy_reset --survey` | A survey whose object store section reads `Bucket <name>, N objects listed` rather than `NOT PERFORMED`. |

## Work plans, measured against real data

These are what rows 3 and 5 will actually do. Both were produced by a dry run
against a clone of the development database migrated to
`0062_embedding_provenance`. Costs are **list-price estimates and never a
quotation**: prompt caching, batch discounts and the vendor's own rounding all
move the invoice, and the figure here can see none of them.

### Re-embedding (`python -m app.scripts.reembed --dry-run`)

```
model                     : voyage-context-4
contract version          : v1-1024-doc
rows to re-embed          : 73
batch size                : 128
provider round trips      : 8

per target:
  profiles.embedding      : 43
  jobs.embedding          : 30
  jobs.reach_embedding    : 0     (migration 0058 nulled every one of them)
  context_chunks.embedding: 0     (the table is empty)

per tenant:
  Acme Corp     : 35
  Specter & Co. : 15
  Sarkar Corp   : 13
  ACRM Corp     : 10
  TechStart Inc.:  0
```

Eight provider round trips. The cost is negligible at this volume and is not
the reason to plan it; the reason is that until it runs, `profiles.embedding`
and `jobs.embedding` hold vectors from two different models in one column and
every cosine distance computed between them is a number with no meaning.

### Pre-screen regrade (`python -m app.scripts.legacy_reset --regrade`)

Measured **after** the purge, which is when it runs. Before the purge only two
applications lack a grade; after it, every one does.

```
jobs to re-score          : 34
candidates in scope       : 1077
batch size                : 10
model                     : claude-haiku-4-5-20251001
scoring calls             : 133
embedding calls           : 68
estimated cost            : USD 2.46
estimated wall clock      : 33.2 minutes at the per-call timeout

per tenant:
  Sarkar Corp   : 12 jobs, 392 candidates
  Specter & Co. : 10 jobs, 325 candidates
  ACRM Corp     : 10 jobs, 321 candidates
  Acme Corp     :  2 jobs,  39 candidates
```

The wall clock is the worst case, not the expected one: it assumes every call
takes the full `rerank` timeout of 15 seconds. The run is resumable and
idempotent, so an interruption costs the batch in flight and nothing else.

## Standing language rule

Nowhere in this repository, in a commit message, in `CLAUDE.md` or in a report,
may any wording imply a live vendor call has succeeded. In particular the
phrases **"verified against the API"**, **"confirmed working"** and **"tested
live"** are forbidden for this phase's work. The correct phrasing is "built and
tested against recorded fixtures and a stub provider; not executed against a
live provider".
