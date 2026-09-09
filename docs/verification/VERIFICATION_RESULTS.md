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
