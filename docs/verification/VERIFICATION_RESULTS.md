# Verification results

Produced by `backend/scripts/verify_live.py` against the live vendor
endpoints. Everything below is an OBSERVED result; nothing here is inferred
from documentation.

- Run at: 2026-08-31 16:36:50Z
- Commit: `b87a5aa53c587bdd605ce05391593c8414bdb239`
- Reasoning path: `gpt-5.6-terra`
- Extraction path: `gpt-5.6-luna`
- Embedding path: `voyage-4`

Every path exercised returned the shape this codebase was built against.

| Path | Vendor | Model | Result | ms | Detail |
|---|---|---|---:|---:|---|
| `reasoning` | OpenAI | `gpt-5.6-terra` | PASS | 5711 | 1037 characters of text returned |
| `extraction` | OpenAI | `gpt-5.6-luna` | PASS | 4848 | top-level object with keys ['comments', 'education', 'experience', 'role_fit', 'skills'] |
| `embedding` | Voyage | `voyage-4` | PASS | 1219 | 2 vectors, 1024 wide |
| `credential_failure` | OpenAI | `gpt-5.6-luna` | PASS | 973 | 401 classified as credential, breaker trips on first |
| `timeout` | OpenAI | `gpt-5.6-terra` | PASS | 1825 | raised inside the budget: openai exhausted for task_type=report_synthesis: transport (ConnectTimeout); transport (ConnectTimeout); transport (ConnectTimeout) |
| `rate_limit` | OpenAI | `gpt-5.6-luna` | NOT PROVOKED | 5531 | the call succeeded, so no rate limit was reached. The classifier and the retry-after reader remain proven only against recorded fixtures. |

## What a NOT PROVOKED row means

The branch could not be reached without abusing the vendor. It is not a pass
and it is not a failure: it means the behaviour remains proven only against the
hand-authored fixtures in `backend/tests/fixtures/vendor/`.

## Next

Remove the corresponding rows from `VERIFICATION_PENDING.md` **only** for the
paths that show PASS above. A row is removed by a run that succeeded, never by
a run that was attempted.

---

# The reranker, live. RPN-AI-UP-001 W6.1 (2026-09-09)

Until today `claude.md` carried this under "what is NOT proven": *"No live
rerank call has ever been made. `rerank-2.5` has not been resolved against the
endpoint; the module is proven against a fake shaped like the installed SDK."*
It has now been made, twice: once against the raw endpoint to resolve the model
id, and once through the SHIPPED module so the installed SDK's real response
shape is what `_voyage_order` reads.

- Run at: 2026-09-09
- Credential: `VOYAGE_RERANK_2_5`, added to the environment on this date. It
  holds the same Voyage ACCOUNT key as `VOYAGE_CONTEXT_4`, because one Voyage
  account serves both `/v1/embeddings` and `/v1/rerank`. The two names are kept
  separate deliberately, per the convention that a credential is named after the
  model it unlocks, so an absent key names the missing CAPABILITY.

| Path | Vendor | Model | Result | Detail |
|---|---|---|---:|---|
| `rerank_model_id` | Voyage | `rerank-2.5` | PASS | 200, `usage.total_tokens` 28. The vendor's own supported list, returned verbatim in a 400 for a bad id, is `['rerank-lite-1', 'rerank-2-lite', 'rerank-2', 'rerank-3', 'rerank-3-lite', 'rerank-2.5', 'rerank-2.5-lite']` |
| `rerank_sdk_shape` | Voyage | `rerank-2.5` | PASS | through `services/rag/reranker.rerank_chunks`; the run recorded `reranker="voyage", degraded=False` |
| `rerank_moves_the_order` | Voyage | `rerank-2.5` | PASS | over four chunks whose FUSED order put an irrelevant retail chunk first at 0.9, the cross-encoder returned both Kafka chunks (fused 0.5 and 0.4) above it |
| `rerank_degradation_recorded` | Voyage | n/a | PASS | with the credential blanked, the run recorded `reranker="lexical", degraded=True, reason="credential_not_configured"` and returned results rather than raising |

**`rerank-3` remains refused and the reason is unchanged.** It is Preview, and
the module's docstring argues a preview model has no place under a
grade-adjacent pipeline. This run does not disturb that: it confirms
`rerank-2.5` is real, which is the only thing that was in doubt.

---

# The judge determinism probe. RPN-AI-UP-001 W7.2 (2026-09-09), PARTIAL

**This is not a completed measurement and must not be cited as one.** The full
probe is 3 models x 2 arms x 5 cases x 20 repeats = 600 calls, and the Gemini
free tier's daily allowance was exhausted before it finished. Every key answered
429 on every model at the end of the session.

What the run DID establish, and each of these is worth more than the sigma would
have been:

| Finding | Detail |
|---|---|
| `models.list` is not a capability list | The account's model listing advertises `gemini-2.5-pro`, `gemini-2.5-flash` and `gemini-2.5-flash-lite`. All three answer `generateContent` with 404 "no longer available to new users". This is the `voyage-context-4` failure in a new shape and it is why `JUDGE_MODELS` is now resolved by CALL, never by listing. |
| The panel that answers | `gemini-3.5-flash`, `gemini-3.6-flash`, `gemini-3.7-flash` all returned 200. Every `-pro` id answers 429 on these keys, so no pro juror exists to seat. |
| The keys are separate meters | Key 1 answered 200 in the same second keys 2 and 3 answered 429, so the slots are separate projects, and rotation is real throughput rather than theatre. |
| A quota rejection is not dispersion | Recorded because getting it wrong was the probe's first result: it reported `sigma=0.0000, unanimous 5/5` out of five cases of nothing but 404s. Only in-scale answers now count toward the rate, `usable_share` travels beside every sigma, and an arm that measured nothing reports `unavailable`. |
| Where verdicts WERE obtained, they were unanimous | `gemini-3.5-flash` `clear-strong`: 8 usable calls, all `highly_matching`. `gemini-3.7-flash` `clear-strong`: 6 usable, all `highly_matching`; `boundary-thin`: 3 usable, all `moderately_matching`. **Suggestive only.** Eight calls is not twenty, and the cases that survived the quota are the EASY ones, which is exactly the sampling bias the probe's own docstring warns produces a reassuring number. |

**Consequence, stated plainly:** `configured_jurors()` still returns an empty
tuple, and W8's release gate still has no measured threshold to rest on. The
transport (`app/evaluation/judges/gemini.py`) and the probe
(`app/evaluation/judges/determinism.py`) are written, exercised and correct;
what is missing is quota, not code. Completing it needs either a paid Gemini
tier or a rerun after the daily allowance resets:

    python -m app.scripts.probe_judge_determinism --repeats 20 --json

Seating a jury on the partial numbers above would produce a kappa describing
nothing while looking exactly like a measurement, which is the failure the whole
package was built to prevent.

---

# The deployment, and what was verified IN PRODUCTION (2026-09-09)

Commit `f1416e4` on `feat/ai-upgrade-rpn-ai-up-001`. Backend image
`sha-b790534`, index digest
`sha256:a5003fc033059401c9ba4d921c93c1d98c1af6afb390575615eaf456d8dc7b37`,
arm64 confirmed from the manifest rather than assumed.

| Step | Result |
|---|---|
| Backend suite on the deployed commit | 6071 passed, 1 skipped, 0 failed |
| `terraform apply` (pilot) | 10 added, 12 changed, 5 replaced. No standalone destroy: the five are task-definition revisions |
| Migration job | exit 0, polled to STOPPED. `aws ecs run-task` returning is not the migration finishing |
| Services rolled | api 24->25, frontend 13->14, analysis 11->12 |
| Lambdas | 3 image-backed functions updated by DIGEST |
| **Verified by digest** | api, frontend and analysis: EVERY running task is the image this build produced |
| Production read-back (`pilot-baseline.sh`) | `schema_version: 0092_security_provenance`, `context_chunks_total: 6`, `context_chunks_embedded: 6` |
| New schedules | `readypick-release-held-assessments` and `readypick-sync-intercom-companies`, both ENABLED |

## The reranker, exercised IN THE CLUSTER

A one-shot Fargate task on the `api` task definition, so it ran under the api
task role with the real Secrets Manager value, not a local `.env`:

    BACKEND: voyage
    CRED_PRESENT: True
    CRED_IS_PLACEHOLDER: False
    RECORD: {'reranker': 'voyage', 'degraded': False, 'reason': None}
      fused=0.5  Led the Kafka migration and tuned consumer group r
      fused=0.4  Debugged partition assignment stalls in a multi-br
      fused=0.9  Managed a retail store team and handled inventory

`CRED_IS_PLACEHOLDER: False` is checked explicitly and is not a formality. The
secrets module seeds every container with a placeholder VERSION so tasks can
start, and this platform has already shipped a release where
`FIREBASE_SERVICE_ACCOUNT_JSON` held `PLACEHOLDER_NOT_CONFIGURED` in a
perfectly healthy-looking secret: a secret CONTAINER is not a configured
secret.

The ordering is the finding. The cross-encoder put both Kafka chunks above the
retail chunk that FUSION had ranked first at 0.9, so what ran was a real
reranking rather than a pass-through returning the input order.

## Two things this deployment found

**A Lambda will not accept an OCI index carrying an attestation manifest.**
`docker buildx --provenance=true` pushes an index whose children are the image
manifest and a provenance attestation. ECS pulled it without complaint; Lambda
answered `InvalidParameterValueException: The image manifest, config or layer
media type for the source image ... is not supported`. The functions are
pointed at the CHILD arm64 manifest
(`sha256:218a11d72340a77123c95658157fcbc9586d7614be040b211d7279e16b524583`)
rather than at the index. Worth knowing before the next manual deploy: the two
runtimes do not accept the same artifact reference.

**`rds.force_ssl` is a STATIC parameter and the module never said so.**
Terraform defaults `apply_method` to `immediate`, RDS refuses `immediate` for a
static parameter, and `plan` accepts it happily. The failure only appears on
the SECOND apply, once the parameter group already carries `pending-reboot` in
its deployed state, which is the first apply this branch reached. Fixed in
`f1416e4`; the value is unchanged at "1".

## One untidy artifact, recorded rather than hidden

The tag `sha-f1416e4` on the backend repository points at index digest
`sha256:c0754de5...`, NOT at the deployed `sha256:a5003fc0...`. It was created
by re-registering a manifest that had been round-tripped through
`--output text`, which reserialised it into different bytes. Nothing is
deployed from it. It was left in place rather than deleted because its child
manifests are shared with the deployed index. **Deploy from `sha-b790534`.**

---

# W7.2 COMPLETE. The judge determinism measurement (2026-09-09)

Superseding the PARTIAL Gemini entry above. Gemini's free tier exhausted its
daily allowance at roughly a third of 600 calls; the Groq keys already in the
environment completed the run.

    python -m app.scripts.probe_judge_determinism --vendor groq --repeats 20

Six arms, five cases each, twenty calls per case. `usable_share` was 1.00 on
every arm, so every number below rests on twenty real verdicts.

| model | arm | pooled sigma | worst case | unanimous |
|---|---|---:|---:|---|
| `qwen/qwen3.8-27b` | temperature 0 | 0.0000 | 0.000 | 5/5 |
| `qwen/qwen3.8-27b` | temperature 0 + seed | 0.0000 | 0.000 | 5/5 |
| `openai/gpt-oss-120b` | temperature 0 | 0.0300 | 0.150 | 4/5 |
| `openai/gpt-oss-120b` | temperature 0 + seed | 0.0000 | 0.000 | 5/5 |
| `openai/gpt-oss-20b` | temperature 0 | 0.0200 | 0.100 | 4/5 |
| `openai/gpt-oss-20b` | temperature 0 + seed | 0.0100 | 0.100 | 4/5 |

## What it settled

**A SEED IS NOT DETERMINISM.** It helps and it does not guarantee.
`gpt-oss-120b` went 0.0300 to 0.0000 with a seed; `gpt-oss-20b` still disagreed
with itself at 0.0100 WITH one. So W7.2's central question is answered against
the seed: reproducibility rests on REPEATS WITH REPORTED DISPERSION. That
matches what this platform already found for its own models, where
`temperature=0.0` is refused outright and `system_fingerprint` came back null.

**DISPERSION LIVES AT BAND BOUNDARIES.** Every non-zero cell is a boundary case.
`clear-strong`, `clear-absent` and `claim-without-detail` were 20 for 20 on
every model in every arm. A probe made of obvious cases would have reported
0.0000 across the board and calibrated the gate on the wrong distribution.

**THE PANEL IS STEADIER THAN ITS MEMBERS.** The worst single juror moved on 3 of
20 calls for one case. A majority over three needs two to move together before
the pooled label does.

**IT DID NOT SETTLE JUDGE QUALITY.** Self-agreement is not accuracy: a model
answering `matching` every time agrees with itself perfectly. The reasoning and
decision sets stay EMPTY until a human labels them.

## What it unblocked

`app/evaluation/release_gate.py` (W8) now exists with a threshold derived from
the measurement rather than guessed: `NOISE_BAND = 0.03 x 3 = 0.09`. A fall in
agreement inside that band is reported and does not fail, because it is the
judge disagreeing with itself. A fall wider than it fails even when the absolute
value still clears the floor.

**UNAVAILABLE IS NOT A PASS.** With the human-labelled sets empty the gate
returns `unavailable` and `releasable=False`. A metric that could not be
computed must block, or the first thing a broken harness does is wave every
release through while showing green.

## The jury pipeline, proven end to end (W7.4, W7.5)

`python -m app.scripts.probe_judge_jury` against the real panel: MCC 0.627,
Cohen's kappa 0.556, raw agreement 0.667 reported only beside the kappa with its
38.6-point caveat, full confusion matrix, accuracy interval, 6 presented, 6
judged, 0 abstentions.

**THOSE CASES ARE SYNTHETIC AND LIVE IN THE SCRIPT.** They prove the plumbing:
a real panel, real calls, majority pooling with ties abstaining, and
`build_result` producing chance-corrected metrics. They are not evidence about
candidates, and they are deliberately not in `app/evaluation/datasets/`, where
they would be indistinguishable from human labels in six months.

## Two transport findings

**Groq sits behind Cloudflare, which answers 403 code 1010 to urllib's default
User-Agent.** Not a credential failure and not a rate limit. Without the header
every call fails looking like a rejected key.

**A fault is classified by STATUS as well as by message text.** Groq's 429 body
matched no message fragment, read as `unclassified`, and was therefore never
retried, because `unclassified` is not in `RETRYABLE_FAULTS`.

## One juror excluded for a reason worth recording

`qwen/qwen3.6-27b` ANSWERS CORRECTLY and is still not on the panel. It emits a
`<think>` scratchpad on every call, spends its whole token ceiling on it, then
meets the per-minute meter: it made no measurable progress in eleven minutes
while the other models finished cases in seconds. A juror that cannot be
measured inside a probe's budget cannot inform a gate threshold.
`strip_reasoning` is kept anyway, because any model may emit one.

---

# Second deployment, commit 418b1c3 (2026-09-09)

Backend image `sha-418b1c3`, digest
`sha256:fbc7e572be84acea6ec73d7afa1ac1e0d83f8c3f228ba9ece7a550ddcd6ea925`.

| Step | Result |
|---|---|
| Backend suite on the deployed commit | 6093 passed, 1 skipped, 0 failed |
| `terraform apply` | 3 added, 2 changed, 3 replaced. The three replacements are task-definition revisions |
| Migration job | exit 0, polled to STOPPED |
| Services rolled | api, frontend, analysis |
| Lambdas | 3 image-backed functions, updated BY DIGEST |
| **Verified by digest** | every running task is this build |
| Production read-back | `schema_version: 0092_security_provenance` |
| Site | 200 |
| API errors in the ten minutes after rollout | none |

## The Lambda manifest problem is solved, not worked around

The previous deployment could not point a Lambda at the image it had built:
`docker buildx --provenance=true` pushes an OCI INDEX whose children are the
image manifest and a provenance attestation, ECS pulls it happily, and Lambda
answers `InvalidParameterValueException: The image manifest, config or layer
media type for the source image ... is not supported`. That deploy pointed the
functions at the child arm64 manifest instead, which worked and left two
different digests describing one build.

This build uses `--provenance=false --sbom=false`, which pushes a single plain
manifest. ECS and Lambda now accept THE SAME digest, so "verify by digest" means
one number for the whole deployment rather than one per runtime.

---

# W6.3 asymmetric embeddings: already wired, now proven live (2026-09-10)

The workstream brief allowed for the possibility that this was already done,
and it was. `rag/index.py` embeds with the DOCUMENT input type,
`rag/retrieval._semantic` calls `embed_query` and carries a comment saying
exactly why, and `matching.py` is deliberately symmetric because its stored
vectors are compared in both directions (a job ranked for a resume and a
resume ranked for a job read the same columns). No code changed for this
workstream; what was missing was live evidence that the asymmetry is real.

- Run at: 2026-09-10, `VOYAGE_CONTEXT_4`, model `voyage-4`

| Path | Result | Detail |
|---|---|---|
| `embed(text, input_type="document")` vs `embed(text, input_type="query")` | PASS | the SAME sentence produced two different 1024-wide vectors: cosine similarity 0.841414 between them, all 1024 components differing |

A cosine of 0.84 between two embeddings of one identical sentence is the
vendor's query/document asymmetry doing real work. Had the two come back
identical, `embed_query` would have been decoration and the retrieval comment
a false claim.

---

# The retrieval golden set: 60 cases, version 2026.Q3.2 (2026-09-10)

Q3.1's 24 hand-authored queries and 60 chunks are frozen and carried verbatim;
36 new hand-authored queries over 90 new hand-authored chunks join them across
six new role domains. All synthetic by construction, which W7.1 permits for
retrieval because a query-to-chunk pair is objectively checkable.

| Check | Result |
|---|---|
| `python -m app.scripts.eval_retrieval --gate` | exit 0 |
| Harness self check | passed, 4 stamped values reproduced within 1e-09 |
| Case count against the floor | 60 of 300, reported by the gate itself |
| Human verification | 0 of 60; every case carries `human_verified: false` |
| Quality gate eligibility | still refused: the only run is a `reference_fixture` |

**What this did NOT change, stated so nobody infers otherwise:** retrieval
QUALITY remains unmeasured. The fixture run measures the metric harness, not
the retriever; quality becomes measurable only with a `recorded` run against
an index holding real volume, and the deployed environment still holds zero
candidates. The reasoning and decision sets remain EMPTY at version 2026.Q3.2
and must stay empty until a human labels them.

---

# Third deployment, commit 3b27abb (2026-09-10)

Native support replaces the deleted vendor sync; the pilot database grows up a
size; reports gain provenance columns. Backend image `sha-3b27abb`, digest
`sha256:bfa2a8eb48fa793107cd9814e03c08fc7b11520ba2f9f600206a26c5ab7e86dd`;
frontend image `sha-3b27abb`, digest
`sha256:e54c7d134918d7b8150a5991d8f97e99f4dcf546de517b5bc22814fb11ae5f70`.
Single plain manifests (`--provenance=false --sbom=false`), so ECS and Lambda
share one digest per image.

| Step | Result |
|---|---|
| Backend suite on the deployed commit | 6132 passed, 1 skipped, 0 failed |
| `terraform apply` (pilot) | 4 added, 4 changed, 5 destroyed: four task-definition revisions, the RDS in-place modify, and the vendor sync's EventBridge rule destroyed |
| RDS after the apply | `db.t4g.medium`, status available, `PendingModifiedValues` empty, `max_allocated_storage` 200. Read back from `describe-db-instances`, not from the plan |
| RDS Proxy | NOT built, owner decision, after the vendor's pinning documentation was read: for PostgreSQL the proxy pins on SET commands, `set_config()`, and named prepared statements, and this application does all three on effectively every session. The reasoning lives beside the `instance_class` line in `infra/environments/pilot/main.tf` |
| Migration job | exit 0, polled to STOPPED; schema read back as `0094_report_provenance` |
| Services rolled | api 26 to 27, frontend 14 to 15, analysis already on 12 |
| Lambdas | all 3 image-backed functions running `sha-3b27abb` |
| **Verified by digest** | api (4 tasks), frontend (2), analysis (4): every running task is the image this build produced |
| Support routes live | `GET /api/v1/support/threads` and `GET /api/v1/provider/support/threads` both answer 401 unauthenticated: mounted and gated, not 404 |
| Vendor sync rule | `readypick-sync-intercom-companies` absent from the scheduler listing |
| Site | 200 |
| API errors in the ten minutes after rollout | none |

## What this deployment deliberately did not prove

The Support surface is proven live at the ROUTE level (mounted, auth-gated,
zero errors) and end to end in the suite (RLS in both directions, the FSM,
the notification dispatch, the capability gates, against a real database).
No support thread has been opened through the production UI yet, because the
environment's three tenants are demo tenants and opening one is a signed-in
human act. The first real thread is the remaining live exercise, and it is a
two-minute manual step, not an engineering gap.

The RDS bump is proven applied; what it is FOR (HNSW working memory, the
connection ceiling under Lambda concurrency) becomes measurable only when
real load exists. The standing watch item is CloudWatch `DatabaseConnections`
against the new ceiling, and the pinning analysis stands recorded for whoever
next reaches for a proxy.
