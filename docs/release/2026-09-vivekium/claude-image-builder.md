# CLAUDE.md section draft: the native arm64 image builder (2026-09-25)

## Current hard rules, the native arm64 image builder (2026-09-25)

No migration. One Terraform module, one script, one test module. A deploy
spent about two hours building arm64 images under QEMU on an x86 laptop;
`infra/modules/image_builder` moves the build to a CodeBuild project on a
NATIVE arm64 host, and `scripts/build-images-remote.sh` is the only intended
way to start it. `docs/operations/DEPLOY_AWS.md` section 5 makes it the default
path and keeps the local QEMU build as the fallback.

### THE BUILD IS OF A COMMIT, NEVER OF A TREE

- The script refuses a dirty working tree (tracked or untracked) and a commit
  not on `origin/main` unless `--allow-non-main`, because main is the only
  deployable branch (owner ruling, 2026-09-25). It `git archive`s the three
  build contexts at that commit; the build never sees a `.git`, a working
  tree or an uncommitted edit.
- **The tag is DERIVED, never chosen**: `sha-` plus the first twelve characters
  of the commit, the same as CI's `sha-${GITHUB_SHA::12}`. The buildspec
  re-derives it from `SOURCE_COMMIT` and refuses a mismatch, so a tag naming
  one commit cannot be pushed over another commit's bytes.
- **Immutable tags make a rebuild impossible, so an existing tag is REUSED and
  said to be.** A rerun after a partial failure builds only what is missing.

### THE LAMBDA SIBLING IS THE SAME BYTES NOW, NOT A SECOND BUILD

`backend:<tag>` and `backend:<tag>-fn` are ONE buildx invocation pushed under
two names, as a single Docker v2 manifest with `--provenance=false
--sbom=false` and `oci-mediatypes=false`. Before, the operator built them
separately and the "same bytes" claim was a claim about layers, not digests.
The script reads both back from ECR and refuses a digest mismatch or any
media type but `application/vnd.docker.distribution.manifest.v2+json`, which
is the manifest Lambda accepts. Consequence worth knowing: builder images
carry NO provenance attestation (the default docker driver cannot write one,
and a docker-container driver would pull BuildKit from Docker Hub on every
build); provenance is the commit, the CodeBuild build id and the log group.

### THE ROLE IS THE BOUNDARY, AND THE TEST PINS IT BY THE EXACT ACTION LIST

ECR push and pull on the backend, frontend and analysis repositories by ARN
(a validation refuses a fourth key), `s3:GetObject` under `builds/` in its
own bucket, its own log group, the Hugging Face token and nothing else; both
KMS grants are conditioned on `kms:ViaService`, the token's also on its
`SecretARN`. No deploy permission of any kind: deploying stays the operator's
act, verified by digest. `privileged_mode = true` exists ONLY in this module
(the Docker daemon needs it), and `test_image_builder_infra.py` fails if it
appears anywhere else in `infra/`.

### THE BUILDSPEC THAT RUNS IS THE APPLIED ONE, SO THE SCRIPT COMPARES IT

The buildspec is embedded in the project at apply time (`file()`), so a commit
cannot change how it is built. The script compares the applied buildspec with
the commit's and refuses a difference: an unapplied edit must not read as
having been built. A commit that predates the builder is refused by name and
pointed at the QEMU fallback.

### PUBLIC CONFIG IS STILL KEPT OUT OF STATE

The six `NEXT_PUBLIC_FIREBASE_*` values are READ (never sourced) from
`frontend/.env.local` at build start, validated against the Firebase value
alphabet, and passed as build environment overrides; the buildspec passes them
as `--build-arg NAME` with no value, so they are on no command line and in no
log. No Firebase name appears in any `.tf` or `.tfvars`, asserted.

### WHAT IS NOT PROVEN

The builder has never run in the account. The offline plan shows eleven
additive resources and zero changes or destroys; it cannot show that ARM
LARGE is offered in ap-south-2, that `amazonlinux2-aarch64-standard:3.0`
ships `docker buildx`, or that anonymous Docker Hub pulls are not throttled
from CodeBuild's shared addresses. The buildspec refuses a non-aarch64 host
and a missing buildx by name, so each fails the first build loudly.

### Supersessions

- DEPLOYMENT_LOG.md section 5 ("Build and push" on the laptop) is the FALLBACK
  now, not the procedure. Not edited: it is dated provenance.
- The 2026-08-28 spec-doc5 PART D rule "NO LIVE AWS DEPLOYMENT HAS BEEN
  EXECUTED" is unchanged by this package; nothing here was applied.
