# CLAUDE.md section draft: Phase 7 WP-B3 (settings) and WP-B4 (infra and CI)

Package `p7-b34`, branch `wip/p7-b34`. No migration.

## Current hard rules, one configuration surface and a held key (2026-09-24)

### `.ENV.EXAMPLE` AND `SETTINGS` ARE ONE SURFACE, AND A TEST SAYS SO BOTH WAYS

`.env.example` had drifted from `app/core/config.Settings` in both directions.
Keys the product reads and the pilot Terraform SETS were missing
(`INBOUND_EMAIL_DOMAIN`, `INBOUND_WEBHOOK_SECRET`, `POSTGRES_MIGRATION_ROLE`,
`SES_CONFIGURATION_SET`, `TRANSCRIBE_*`, `REQUIRE_JWT_SECRET`,
`EMBEDDING_DIMENSIONS`), so a laptop could not reproduce the deployed
configuration without reading the Python. Keys nothing read were still there
(`SENDER_OTP_*` two weeks after the mailbox code went, `OTP_*`, the SMS vendor
keys, the Cloud Run block with a project id), each one a setting an operator
could tune expecting an effect.

`tests/test_env_example_parity.py` pins it:

- **Every `Settings` field is documented, or declared in `INTERNAL_ONLY` under
  a written reason.** Listed by NAME, never by prefix: a prefix rule admits the
  next `proctoring_*` field without anybody deciding it.
- **Every documented key has a reader**: a field, or an `EXTERNAL` key with the
  process that reads it (the analysis service, the OTel SDK, the frontend).
- **A field any Terraform root assigns must be DOCUMENTED, never internal.**
  What a deployment actually sets is deployment data by definition.
- **The declarations are a ratchet** in both directions.
- `# KEY=value` means documented and deliberately unset (`OWNER_EMAIL`,
  `SENDER_DOMAIN_BLOCKLIST`): an empty `KEY=` line would override the default
  with an empty string, which for `OWNER_EMAIL` means no owner.

**A new `Settings` field is half a change until it is in `.env.example` or in
`INTERNAL_ONLY` with its reason.** Phases adding fields in parallel will fail
this test at merge by design; the fix is the entry, not a relaxation.

### THE ENCRYPTION KEY IS HELD AND INJECTED INTO NOTHING

`llm_key_encryption_secret` is deleted from `Settings`: nothing decrypted with
it since the multi-vendor router went, and a setting kept "as key material"
reads as live. The `LLM_KEY_ENCRYPTION_SECRET` CONTAINER stays in
`infra/modules/secrets` `secret_names`, granted to no service and mounted in no
root, because it is the only copy of the key that opens `llm_provider_keys`
and destroying a secret is irreversible once the recovery window passes. It
leaves in the same change that drops that table, which is an owner decision.
`test_deploy_secret_hygiene.test_a_held_secret_is_granted_to_no_service`
fails in both directions: removed from `secret_names`, or granted/mounted.

The SMS vendor secret left `secret_names`, the task-worker grant and the pilot
mount; the three login-code settings and the SMS settings left `Settings`;
`missing_delivery_keys` is SMTP only.

### A PARSER THAT READ PAST ITS OWN VARIABLE

`test_deploy_secret_hygiene._service_secrets` read from `variable
"service_secrets"` to the END of the module, so `service_secret_writers`,
declared later with the same `"migrate" = [...]` shape, replaced the read
map's migrate entry. **Every assertion about what the migration job may READ
was asserting what it may WRITE**, and a read grant added to migrate passed.
Found by mutation-checking the new held-secret guard; the parser now stops at
the next `variable`. A guard is not checked until something that should fail
it has been seen to fail it.

### THE PRODUCTION ROOT IS DERIVED AGAIN, AND EVERY EDIT MATCHES EXACTLY ONCE

`infra/environments/derive-production.py` read and wrote
`C:\dev\pickready\...` by absolute path, so from any other checkout it
rewrote a DIFFERENT working copy's production root. It had also drifted: the
staging backend key had moved to `backend.tf`, it rewrote a Celery
`--concurrency` flag that no longer existed, and production had been
hand-edited. The first stale edit aborted the run, so nothing had been derived
for weeks and every change reached production by hand.

- Paths resolve from the script. Run it, then review
  `git diff infra/environments/production`.
- **Every edit must match EXACTLY once.** `replace(old, new, 1)` on a string
  that occurs twice silently chooses which block production gets.
- A run over the staging root reproduces the committed production root byte
  for byte; that is how the EDITS list was rebuilt, and it is the check to
  repeat after changing either.

### THE RETIRED PLATFORMS ARE OUT OF LIVE CONFIG

`tests/test_gcp_and_celery_wording_removed.py` sweeps (the shared
whitespace-normalised `removal_sweep`) for the Cloud Run keys, the deleted GCS
migration script, "Celery broker", "Celery worker", `celery_app` and
`celery -A`. A sentence recording the removal is provenance and is admitted by
an explicit `(file, phrase)` HISTORY list with a reason, never by a heuristic,
and the list only shrinks. `infra/railway.json` and `infra/render.yaml`
(a `celery -A ... worker` and `beat` the image cannot run) are deleted.

### THE DEPLOY WORKFLOW'S REF RESTRICTION IS PINNED

`tests/test_deploy_workflow_guard.py`, per CONTRACT v2 ("main or tags"):
every job that pushes an image refuses a pull request and admits exactly
`refs/heads/main` and `refs/tags/*`; every job that changes an environment
admits `refs/heads/main` only; every job holding `id-token: write` depends
transitively on a restricted build and none overrides skip-on-skipped with
`always()` or `!cancelled()`; no gated checkout names a `ref:`. Conditions are
read from the parsed YAML, so a comment quoting the old condition cannot
satisfy it.

## Supersessions to mark in place

- Section 3 rule 2 ("The legacy MSG91 OTP send-path is retained as a working
  SMS feature") and section 5 ("OTP settings remain for the retained SMS
  feature"): SUPERSEDED 2026-09-24, the SMS settings and secret are gone;
  section 5's `GROQ/GEMINI/OPENROUTER` roster note was already stale.
- Section 3 rule 9 ("Use the `llm_provider_keys` table and the router
  service"): SUPERSEDED, nothing reads that table; its key is held ungranted.
- spec-doc5 PART D "Redis is `noeviction`... It is the Celery broker":
  the reason is the proctoring counter (2026-09-05 section already says so).
