# Verification pending

Every claim in this repository that **cannot be proven in this phase**, and the
exact command that would settle each one.

spec-doc6 decision D6: there is no model-vendor key and no Voyage key in this
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

1. Obtain `OPENAI_GPT_TERRA`, `OPENAI_GPT_LUNA` and `VOYAGE_CONTEXT_4`
   and export them.
2. Run the rows in the order the **Order** column gives. The order is not
   cosmetic: re-embedding changes what retrieval returns, and the pre-screen
   regrade reads retrieval, so a regrade run before the re-embed grades every
   candidate against an index that is about to change underneath it.
3. Record the outcome of each row, pass or fail, with the figures it printed.
4. A row whose command fails stays in this file. A row is removed only by a run
   that succeeded.

## Outstanding claims

**Rows 6, 7, 10, 11, 12, 13 and the second half of row 18 describe the Anthropic
integration and are SUPERSEDED by the vendor change of 2026-08-31.** They are
left in place rather than deleted because this file's rule is that a row is
removed by a run that succeeded, and none of them were; what retires them is
that the code they describe no longer exists. Read them as the record of what
was unproven about the previous integration, and read the "OpenAI vendor
change" section at the end of this file for what is unproven about the current
one. Rows 1 to 5, 8, 9, 14 to 17 and 19 are unaffected.

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
| 10 | **The Sonnet 5 JSON-mode path cannot succeed as written, if the published schema is right.** `llm_router.build_payload` appends a last-assistant-turn prefill seeded with `{` whenever `json_mode` is set, and Anthropic's published documentation states that a last-assistant-turn prefill returns 400 `invalid_request_error` on `claude-sonnet-5`. Derived from documentation; never observed. Every JSON-mode Sonnet task is affected: `report_synthesis`, `dimension_evaluation`, `triangulation`, `competency_transformation`, `swot_intake`, `company_dna_intake`, `behavioral_assessment`. A 400 is classified as our bug and is correctly not retried, so the symptom would be every caller degrading to its deterministic fallback with nothing naming the cause. `llm_router` now appends a hazard sentence to the error on any 400, and `tests/test_vendor_contracts.py` pins it. **Needs an owner decision, not more searching:** the documented replacement is a structured output format or a system-prompt instruction, and either changes what every Sonnet call sends. | `app/services/llm_router.py`, `app/services/reliability/vendor_contract.py` | `python scripts/verify_live.py --only extraction --only reasoning` | A PASS row for `reasoning`, and either a PASS for a JSON-mode Sonnet call or a FAIL naming `anthropic/error_400_prefill_rejected.json`. |
| 11 | **Every Sonnet 5 call sends `temperature`, and the published schema says a non-default value returns 400.** `build_payload` always sets `temperature` from `config.llm_providers.temperature_for(task_type)`; every value that function returns is 0.0 or 0.7, and the documented default is 1.0. Derived from documentation; never observed. This affects the plain-text Sonnet paths as well as the JSON-mode ones, so it is a superset of row 10. The determinism argument for `temperature=0.0` on a judging task is unaffected by the remedy: the documentation states the parameter never guaranteed identical outputs. | `app/config/llm_providers.py`, `app/services/llm_router.py` | `python scripts/verify_live.py --only reasoning` | A PASS row, or a FAIL naming `anthropic/error_400_sampling_rejected.json`. |
| 12 | `claude-sonnet-5` returns the reasoning shape Siddhi's report synthesis and Miti's five dimension evaluators read: a `content` list carrying at least one `text` block, and a `usage` object with `input_tokens`. Asserted against `tests/fixtures/vendor/anthropic/messages_response_sonnet_reasoning.json`, hand-authored from the published schema. `llm_router` checks the first live response per model and raises `VendorContractViolation` naming that fixture if it differs. | `app/services/reliability/vendor_contract.py` | `python scripts/verify_live.py --only reasoning` | A PASS row for the Sonnet reasoning path. |
| 13 | `claude-haiku-4-5-20251001` returns the extraction shape Yukti's AI Score (`rerank`) and Miti's `claim_extraction` and `evidence_tiering` read, AND the dated snapshot id resolves at all. The published model table lists the alias `claude-haiku-4-5` alongside this dated id; the id in this codebase has never been sent. A wrong id returns 404 `not_found_error`, which classifies as a client error and is not retried. | `app/config/llm_providers.py` | `python scripts/verify_live.py --only extraction` | A PASS row for the Haiku extraction path, whose detail line names the top-level keys returned. |
| 14 | `voyage-context-4` accepts the request shape on the Yukti pre-screen path and echoes back its own model id. The `model` field is checked without being read anywhere else, because a response produced by a different model is a vector-space corruption that leaves every column the right width and every cosine distance computable. Derived from the published schema; never observed. | `app/services/embeddings.py`, `app/services/reliability/vendor_contract.py` | `python scripts/verify_live.py --only embedding` | A PASS row reporting the vector count and width. |
| 15 | Voyage returns one `data` row per input, each carrying an explicit `index`, and the set of indices is exactly 0..n-1. `_embed_batch` SORTS on `index`; two rows sharing one index sort perfectly well and silently attach one candidate's vector to two rows. The completeness check is new and has only ever run against fixtures. | `app/services/embeddings.py` | `python scripts/verify_live.py --only embedding` | A PASS row rather than a `VendorContractViolation` naming `voyage/embeddings_response_document.json`. |
| 16 | A real 429 from either vendor carries a `retry-after` header in seconds. The router prefers it over its own backoff curve and bounds it by the remaining budget. Asserted against `tests/fixtures/vendor/anthropic/error_429_rate_limit.json`; a rate limit cannot be provoked on demand without abusing the vendor, so `verify_live.py` reports this branch as NOT PROVOKED unless one arrives by chance. | `app/services/llm_router.py` | `python scripts/verify_live.py --only rate_limit`, run while genuinely rate limited | A PASS row rather than NOT PROVOKED. |
| 17 | An invalid credential returns 401 or 403 rather than some other status. The whole first-occurrence breaker rule rests on it: a revoked key returning, say, 400 would be classified as our bug, would not trip the breaker, and would be retried on every subsequent call for as long as the key stayed revoked. | `app/services/llm_router.py` | `python scripts/verify_live.py --only credential_failure` | A PASS row naming the status and its classification. |
| 18 | The interactive caps are achievable in practice. 15s per attempt and 30s total for `rerank`; 25s and 50s for `jd_generation`, widened because a multi-thousand-token JD cannot finish in 15 seconds on Sonnet. Both numbers are reasoned rather than measured, and Sonnet 5 runs adaptive thinking by default, which the caps were not sized against. | `app/config/llm_providers.py` | `python scripts/verify_live.py` and read the `ms` column | Observed latencies for a realistic prompt on each path, against the caps. |
| 19 | The per-million token prices in `TOKEN_PRICES_USD_PER_MILLION` are current, and the cost estimates that rest on them (including the USD 2.46 regrade figure in this file) are the right order of magnitude. List price is public and changes; nothing here has been reconciled against an invoice. | `app/config/llm_providers.py` | Compare `estimate_cost_usd` against the `usage` figures a real run reports, then against the invoice | A cost line whose modelled and billed figures agree. |

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

## OpenAI vendor change, 2026-08-31

The model vendor moved from Anthropic to OpenAI by owner decision. The embedding
vendor did not move; its credential was renamed to `VOYAGE_CONTEXT_4` so that
every credential is named after the model it unlocks. Nothing below has been
executed against a live provider, and there is no OpenAI key in this phase.

The honest framing is unchanged: **built and tested against recorded fixtures
and a stub provider; not executed against a live provider.**

| Order | Claim that is unproven | Owner | Command that settles it | Expected evidence |
|---:|---|---|---|---|
| 20 | **`gpt-5.6-terra` and `gpt-5.6-luna` exist and this account may call them.** These are the product owner's strings, used verbatim, and neither has been resolved against a models endpoint. This is a stronger gap than a shape assumption: a wrong id returns 404, which classifies as a client error, is correctly not retried, and reaches every caller as its deterministic fallback with nothing naming the cause. They are named constants (`MODEL_TERRA`, `MODEL_LUNA`) so a correction is a one-line edit. | `app/config/llm_providers.py` | `python scripts/verify_live.py --only reasoning --only extraction` | A PASS row per path, or a FAIL whose detail column carries the status. |
| 21 | `gpt-5.6-terra` returns the reasoning shape Siddhi's report synthesis and Miti's five dimension evaluators read: a `choices` list whose first element carries a `message` with string `content`, and a `usage` object with `prompt_tokens`. Asserted against `tests/fixtures/vendor/openai/chat_completion_terra_reasoning.json`, hand-authored from the published schema. `llm_router` checks the first live response per model and raises `VendorContractViolation` naming that fixture if it differs. | `app/services/reliability/vendor_contract.py` | `python scripts/verify_live.py --only reasoning` | A PASS row for the reasoning path. |
| 22 | `gpt-5.6-luna` accepts `response_format: {"type": "json_object"}` and returns one whole top-level object, so that `matching._parse_scoring_response` and every other JSON-mode caller can `json.loads` it and subscript the result. The native format REPLACED an assistant-turn prefill; the guarantee is stronger on paper and has never been observed. A response that is valid JSON but a top-level array would parse and then fail on the first subscript, which is why `check_openai_response` refuses one. | `app/services/llm_router.py`, `app/services/reliability/vendor_contract.py` | `python scripts/verify_live.py --only extraction` | A PASS row whose detail line names the top-level keys returned. |
| 23 | The published requirement that the token `json` appear in the messages before `json_object` is accepted is real, and `llm_router._JSON_SYSTEM_SUFFIX` satisfies it. `describe_request_hazards` names the constraint on any 400 so the failure is a sentence rather than a silent degradation, and `test_vendor_contracts.py` exercises both directions. Derived from documentation; never observed. | `app/services/reliability/vendor_contract.py` | `python scripts/verify_live.py --only extraction` | A PASS row rather than a 400 whose detail carries the hazard sentence. |
| 24 | Chat Completions accepts `max_tokens` on these two models. `build_payload` sends it, from `TASK_MAX_TOKENS`. The parameter is long-standing on this endpoint and newer OpenAI model families are documented to require `max_completion_tokens` instead; which of the two applies to `gpt-5.6-*` cannot be established without a call. A rejection arrives as a 400, is not retried, and reaches callers as their deterministic fallback. | `app/services/llm_router.py`, `app/config/llm_providers.py` | `python scripts/verify_live.py --only reasoning` | A PASS row, or a FAIL whose detail names the rejected parameter. |
| 25 | Both credentials are separately valid: `OPENAI_GPT_TERRA` may call `gpt-5.6-terra` and `OPENAI_GPT_LUNA` may call `gpt-5.6-luna`. Two keys for one vendor is an unusual arrangement and each is used on exactly one path, so a key entitled to only one of the two models would leave that tier working and the other returning 403 -- which trips its own breaker on the first occurrence and degrades only half the product, with the healthy half looking entirely normal. | `app/services/llm_router.py` | `python scripts/verify_live.py --only reasoning --only extraction` | A PASS row on both paths, from two different credentials. |
| 26 | The per-million token prices for `gpt-5.6-terra` and `gpt-5.6-luna` in `TOKEN_PRICES_USD_PER_MILLION` bear any relation to what these two models actually cost. **No published price sheet has been read for either id**; the rows carry forward the previous roster's reasoning-tier and extraction-tier figures, so what they encode is the RATIO between the tiers rather than the cost of either. Every surface labels the output `estimated_cost_usd`, and the operator question the table answers (which task_type is spending the budget) is stable under a uniform error. The regrade cost figure in this file rests on the old rates and should be recomputed. | `app/config/llm_providers.py` | Compare `estimate_cost_usd` against the `usage` figures a real run reports, then against the invoice | A cost line whose modelled and billed figures agree. |
| 27 | The interactive caps are achievable on these two models. 15s per attempt and 30s total for `rerank`; 25s and 50s for `jd_generation`. Both numbers were reasoned against a differently shaped model and were NOT re-derived for the vendor change, deliberately: changing a latency contract on an unmeasured assumption about an unresolved model id would replace one guess with another. | `app/config/llm_providers.py` | `python scripts/verify_live.py` and read the `ms` column | Observed latencies for a realistic prompt on each path, against the caps. |
| 28 | **The Terraform secret names still say `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`.** `infra/` was out of scope for the vendor change and was not touched, so a deployed task would be injected with the old variable names and would find neither model credential nor the embedding credential. Every path would degrade to its deterministic fallback, and the embedding path would do so SILENTLY, returning pseudo-random unit vectors. This is not a verification gap, it is a known unfinished edit, and it must be closed before any deploy. | `infra/modules/secrets/variables.tf`, `infra/environments/*/main.tf` | `grep -rn "ANTHROPIC_API_KEY\|VOYAGE_API_KEY" infra/` | No results, and `OPENAI_GPT_TERRA`, `OPENAI_GPT_LUNA` and `VOYAGE_CONTEXT_4` in their place. |
| 29 | Two stale docstrings survive in modules that were explicitly out of scope for the vendor change. `app/services/matching.py` names `VOYAGE_API_KEY` as the variable that switches embeddings off the dev fallback, and `app/api/admin.py`'s `/llm/stats` docstring still describes the two model tiers as "Sonnet 5 and Haiku 4.5". Neither affects behaviour; both now describe things that do not exist, which is exactly the drift the vendor change set out to avoid everywhere it was in scope. | `app/services/matching.py`, `app/api/admin.py` | `grep -rn "VOYAGE_API_KEY\|Sonnet\|Haiku" app/services/matching.py app/api/admin.py` | No results. |

## Standing language rule

Nowhere in this repository, in a commit message, in `CLAUDE.md` or in a report,
may any wording imply a live vendor call has succeeded. In particular the
phrases **"verified against the API"**, **"confirmed working"** and **"tested
live"** are forbidden for this phase's work. The correct phrasing is "built and
tested against recorded fixtures and a stub provider; not executed against a
live provider".
