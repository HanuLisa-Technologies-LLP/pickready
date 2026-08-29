> **HISTORICAL RECORD. The platform this describes no longer exists.**
>
> This documents the 2026-08-24 fix for a `DATABASE_URL` that was composed at
> deploy time and passed as a plain Cloud Run environment variable. It is kept
> because the FINDING outlived the platform: spec-doc5 §D.4 names it as "the
> exact class of mistake to design out here from the start rather than harden
> later", and the per-service IAM scoping in `infra/modules/secrets` is the
> answer to it.
>
> Nothing in this file is a current procedure. `gcloud` commands here will not
> run and are not meant to. For how credentials are delivered today, see
> [DEPLOY_AWS.md](DEPLOY_AWS.md) and
> `backend/tests/test_deploy_secret_hygiene.py`, which asserts the guarantee
> this document's fix established.

---

# Moving DATABASE_URL out of the revision environment

Status: **step 1 and 2 done and deployed through the gated pipeline. Step 3, the
credential rotation, is NOT done and needs a human decision.**

## What the finding actually was

A cost audit in `diagnostics/` reported "plaintext database credentials embedded
in production Cloud Run service, worker-pool, migration-job and legacy
diagnostic-job environment definitions". That is directionally right and
overstated, and the difference matters for deciding urgency.

What was true:

* `DATABASE_URL` was a plain environment variable on every revision, so the
  assembled DSN, password included, was readable by anyone holding
  `run.services.get` on the project.

What was already correct, and was missed by the audit:

* the password itself has always lived in Secret Manager as `POSTGRES_PASSWORD`;
* `scripts/deploy.sh` composed the DSN at deploy time and never echoed it, never
  logged it, and never wrote it to a temporary file;
* every other secret in the project, 24 of them, was already a secret mount.

So this was one credential materialised in a place it did not need to be, not a
credential sitting in source control.

## Why it was an env var, and why that was a real constraint

A name cannot be both a secret mount and an environment variable on the same
Cloud Run revision. Cloud Run rejects the whole deploy with a type conflict. The
switch is therefore mutually exclusive, which is why it could not be done by
adding a mount and leaving the env var in place.

## The trap that was waiting

Secret Manager already held a `DATABASE_URL` secret, created 2026-07-31. Its
version 1 was a STALE host-and-credential DSN that does not authenticate against
the current instance, which is exactly why `deploy.sh` carried a comment telling
future readers not to use it.

Binding the mount to that secret without checking would have pointed production
at a DSN that cannot connect. This was verified by hash before anything changed:
the live revision's DSN and secret version 1 did not match.

## What was done

1. **A correct version was added.** Version 3 holds the Cloud SQL socket DSN
   composed the same way `deploy.sh` composes it, and its SHA-256 was compared
   against the DSN the running revision was already using. They matched exactly,
   which makes the switch a behavioural no-op rather than a change of address.
2. **Versions 1 and 2 were disabled**, so nothing can bind to the stale DSN by
   pinning a version.
3. **`deploy.sh` now mounts it as a secret.** `DATABASE_URL` left
   `SECRET_EXCLUDE_RE` and `build_env` no longer emits it, which satisfies the
   mutual exclusivity above. Secrets bind at `:latest`.
4. **The rotation loop was closed.** The block that used to compose the env var
   now recomposes the authoritative DSN from `POSTGRES_PASSWORD`, compares it to
   the latest version, and adds a new version only on drift. This is the part
   that was missing before: version 1 sat stale for a month because nothing read
   it, and a rotated password would have left the mounted DSN just as stale with
   production failing to reach its database as the first symptom.

Neither value is echoed, logged, or written to a temp file at any point. Only
whether they matched is printed.

### The permission that step 4 needs, and why the failure mode is a blocked deploy

`github-deployer@` held `secretmanager.secretAccessor` and `secretmanager.viewer`
only, both read. The drift branch would therefore have hit its `die` on the
first deploy after a password rotation.

That `die` is deliberate and stays. The alternative -- warn and continue -- would
mount a DSN that no longer authenticates, and the first symptom would be
production unable to reach its database. A blocked deploy is loud, safe, and
fixable in a minute; a stale mount is an outage.

`roles/secretmanager.secretVersionAdder` was granted on THIS SECRET ONLY, not at
project level, so the refresh self-heals and the `die` remains the backstop for
anything it cannot handle. The role can add a version and cannot read or destroy
one, which is the narrowest grant that makes the design work.

## What is NOT done

**The database credential has not been rotated.** The password that was exposed
in revision environments is still the live password. Rotating it is a coordinated
operation and needs a human to schedule it:

```
create the new password in Cloud SQL
  -> add it as a new POSTGRES_PASSWORD version
  -> deploy (deploy.sh detects the drift and refreshes DATABASE_URL)
  -> verify the backend, both worker pools and the migrate job connect
  -> confirm the OLD credential is rejected
  -> keep the previous secret version disabled, not destroyed, until the
     rollback window closes
```

Do not run this without a rollback plan. The migrate job and both worker pools
connect with the same credential, so all four workloads must be verified, and a
half-rotated estate is worse than an un-rotated one.

## Verification

* `gcloud run services describe pickready-backend` must show `DATABASE_URL` under
  the secret references and NOT under inline env values.
* `/health` must report `"database":"ok"`.
* `alembic current` on the migrate job must reach head.
