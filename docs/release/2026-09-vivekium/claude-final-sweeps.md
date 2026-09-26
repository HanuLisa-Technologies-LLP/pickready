# CLAUDE.md section draft: the final sweeps (stage 3, package final-sweeps)

Draft for the orchestrator to fold into CLAUDE.md. No migration. Items from
CONTRACT v4 item 6, v6, v8 (the sweeps' share), PLAN-p7 WP-B8 and the Judge0
runbook's stage B.

## Current hard rules, the final sweeps (2026-09-26)

### THE COMPATIBILITY CUTOFF: AN ALIAS SURVIVES ONLY FOR A STORED ROW

A redirect, alias or deprecated projection is deleted unless an old STORED row
needs it to render. Pilot held no application links when the cutoff was taken
(CONTRACT v3), so no stored Updates entry or email names a bare path.

- **Deleted:** `/portal/settings` (redirect to My Profile, the 2026-07-27
  rename), `/bd/social` (redirect to BD Reach, the 2026-08-09 merge), the bare
  `/portal/assessments` index redirect (the per-application
  `/portal/assessments/[link_id]` page is NOT an alias and stays: every
  invitation and Updates entry links to it), and `ApplicationOut.stage`, the
  old five-value pipeline enum no client read (`status`/`stage_label` cover
  all ten stages).
- **Kept, with the reason:** `/org` (the org home, a real server redirect, not
  a compatibility alias); `JDGenerateIn.key_requirements` (its docstring calls
  it a deprecated alias, but the live Create Job form still sends the AI brief
  box through it, so it is an input with a writer, not an alias);
  `TenantCreateIn.domain`/`client_phone` (optional inputs, not projections);
  `LinkSource`/`resume_storage_provider` CHECK values (stored data).
- `tests/test_compatibility_cutoff.py` pins every deletion.

### THE DASHBOARD'S STAGE CONTROL IS NOT A SECOND INVITATION DOOR

`POST /dashboard/jobs/{id}/candidates/{link}/stage` called `apply_transition`
for any FSM-legal target, so "Assessment invitation sent" was written with no
`assessment_conversations` row (which IS the invitation), no credit question
and no email, and "Assessment in progress" could be set by hand. That is the
defect Phase 3 WP1 closed in `api/pipeline.change_status`, reachable again
from a second surface (CONTRACT v8).

- **Both stage controls call ONE implementation,
  `assessment_invitations.invite_by_stage_move`**: SEND_OUTREACH on top of the
  route's own capability, `invite_batch` for the one link, and every refusal an
  `InvitationRefused` raised before the first write.
- **`hiring_pipeline.SYSTEM_ONLY_TARGETS` is refused at both doors** with the
  same `SYSTEM_ONLY_REFUSAL` sentence.
- `test_dashboard_workflows.test_the_stage_control_is_not_a_second_invitation_door`
  reads the stage and the invitation count back from a SECOND connection;
  mutation-checked in both halves.

### EVERY BUCKET WRITE NAMES THIS ENVIRONMENT'S KMS KEY

`infra/modules/s3` denies a PutObject whose encryption is not `aws:kms` under
the environment's own key, and `object_storage.put_if_absent` still sent
`AES256`, so every resume, compliance, project and attachment upload on pilot
was refused. `object_storage.sse_arguments` is now the ONE place the header is
built; `video/storage._sse` reads it rather than keeping its own copy. An
empty `S3_KMS_KEY_ID` refuses before any request (`ObjectStorageNotConfigured`),
never a write under the AWS-managed key. The Transcribe working bucket keeps
`AES256` on its cross-region copy: that bucket is its own, SSE-S3 by
configuration, and outside the platform key's region.

### THE CANDIDATE TABLE HAS NO ASSESSMENT MODE COLUMN

One assessment mode is left, so the mode word told a recruiter nothing.
`assessment_mode`, `assessment_mode_label` and `video_status` left
`RankedCandidateOut`, `ranked_candidates` dropped its video lateral join, and
the table lost the column (13 to 12). The recording is reached from the
report's video section (`api/videos`), and the dashboard's own row schema
still carries both words; `video_access.mode_label` and `video_status_word`
keep those callers.

### A PERMISSION FLAG NOTHING CAN SET IS A RULE IN PROSE ONLY

`job_candidate_links.hm_access_granted` lost its only writer with the route
scrap, so every reader could only answer "not granted". The readers are
deleted: full profile access is SEND_OUTREACH through ONE helper,
`api/candidates._require_full_profile_access`. The COLUMN stays (server
default false; dropping it is a migration of its own) and is no longer a model
attribute, so nothing can read it by accident. **Supersedes** the 2026-07-27
"HR grants Hiring Manager access per profile (FR-8.1)" behaviour, which had
stopped being reachable when its route went.

### "CREDITS NEVER EXPIRE" IS TRUE OF SOME CREDITS, AND THE COPY SAYS WHICH

Change request 25 gave new grants an expiry; credits granted before it keep
the promise their GST invoices printed. The billing page states the server's
term (`credit_validity_months`) and the qualifier; the public pricing page
states both. `frontend/lib/credit-expiry-copy.test.ts` refuses an unqualified
"Credits never expire" anywhere in client source. The invoice PDF already
chose its sentence per purchase and is unchanged.

### A SWEEP WITH A BACKSPACE IN IT IS A SWEEP THAT NEVER RAN

`test_miti_live.test_an_employment_gap_reaches_no_control` carried literal
U+0008 characters where `\b` belonged, so the protected-attribute sweep over
Miti's caps and aggregator matched nothing and passed on any source. With real
word boundaries it bit on a COMMENT recording the citation history ("filtering
on age"), so it now reads identifiers and non-docstring string literals only.
Mutation-checked. The general lesson is the 2026-09-23 one again: a sweep with
a blind spot is worse than no sweep, because the green result is what stops
anybody looking.

### EVERY REQUEST-PATH DISPATCH RUNS AFTER THE COMMIT

`LEGACY_CALL_SITES` in `tests/test_dispatch_after_commit_sweep.py` is EMPTY:
the four BGV sends, the interview invitation, the staff and client
invitations, outreach and the candidate's BGV inquiry now use
`dispatch_after_commit`. Two consequences worth knowing:

- **The BGV inquiry route used to catch an enqueue failure, write
  `dispatch_failed`, and then RAISE**, and the raise rolled back the very write
  it promised the candidate. A lost invoke is now logged at ERROR by the commit
  hook and leaves the inquiry `collected`, which is dispatchable, so Send still
  works; a delivery failure inside the task lands in `dispatch_failed` there.
- **Outreach's per-recipient "could not be queued" and its all-failed 503 are
  gone**: the invoke is no longer attempted inside the request. A programming
  error (unknown task, non-JSON argument) still raises in the request.
- The sweep's vacuity guard now counts `dispatch_after_commit` calls, because
  with the list empty there is no bare call left for it to find.
- A test that inspects a dispatch inside a transaction it rolls back reads
  `after_commit.pending_labels(session)`, not `dispatch.recorded()`.

### PROJECT EVIDENCE REACHES QUESTION WRITING THROUGH THE TOOL LAYER

`assessment_questions/generate.py` reads project evidence through
`evidence_retrieval.project_evidence_for_candidate` (the
`extract_project_evidence` tool, Vaada only, assessment stage), not
`projects.context` directly. `PENDING_PROJECT_CONTEXT_READERS` is empty. A
degraded read is logged by `evidence_retrieval` and the questions are written
without the block, because projects are optional context and never a reason to
refuse an assessment.

### THE PROCTORING STATE IS A `LoopBoundRedis`, AND IT FAILS CLOSED

`proctoring/state` built its own per-loop client by hand; it now shares
`core/redis_loop.LoopBoundRedis` with the cache, the run-status record and the
web-search breaker. Unlike the cache, a client that cannot be built is
`StateUnavailable` (503), never a None read as a miss: a proctoring decision
that could not be made must not read as one that was made.
**Supersedes** the 2026-09-22 harness note that `proctoring/state` caches its
own client.

### THE PRE-AWS OBJECT STORE HAS NO READER

Pilot holds no `gs://` row (CONTRACT v3). `object_storage.is_legacy_uri`,
`LEGACY_GCS_SCHEME`, `resume_storage.LEGACY_STORAGE_PROVIDER`, the named read
error (which told operators to run a script that no longer exists) and
`legacy_reset.OBJECT_SCHEMES` are deleted. **Erasure keeps its refusal**
(`LegacyObjectNotDeletable`), now keyed on "not the current provider" rather
than on the old name, because the database CHECK still admits `gcs` until a
migration narrows it, and a deletion reported for bytes in another store would
be a false confirmation.

### THE PII MASKER WENT; THE CONTENT SCREEN DID NOT, AND THAT IS OPEN

`services/safety/pii.py` had no product caller (only an eval regression case
and two tests; re-checked against the unmerged Phase 5 branches) and is
deleted. `services/safety/content.py` is ALSO eval-only, and it is **kept**:
it is the only implementation of the retrieval-time quarantine the 2026-08-18
section states as a rule ("Retrieved chunks pass
`conversation_guardrails.inspect_answer` too"). **That rule is NOT in force on
the live path**: nothing in `evidence_retrieval` or `rag/retrieval` calls
`screen_chunks`, and only `rag/contextual` inspects a whole document before
its prefix is written. Wiring it (in `evidence_retrieval`, after the tool
returns passages) or deleting it together with the rule is an owner/Phase 5
decision; deleting it here would have deleted the rule's only implementation
while the rule still reads as live.

### JUDGE0 STAGE B IS ITS OWN SWITCH

`judge0_clients_enabled` (pilot, default false, a check block requires
`judge0_enabled`) wires the code sandbox's callers: the client security group
on the API service, the task worker Lambda and (through the trigger's
`ECS_SECURITY_GROUP_IDS`) the on-demand agent; `JUDGE0_AUTH_TOKEN` mounted
from `module.code_sandbox` with its read policy on the role that reads it (the
EXECUTION role for ECS, which injects; the function role for the Lambda, which
fetches at cold start); and `JUDGE0_URL`. NOT the frontend, the analysis
service, the migration job or the two drafting Lambdas.

- **Why its own switch, not `judge0_enabled`:** the runbook requires stages A1
  and A2 to be plans that touch no running service. Riding on `judge0_enabled`
  would put a rolling API deployment into the A1 plan.
- The `ecs` module gained per-service `extra_security_group_ids` and
  `extra_execution_policy_arns`; the `lambda` module per-function
  `extra_security_group_ids` and `extra_policy_arns`. All default empty and
  are keyed by POSITION in their `for_each`, never by ARN.
- The default plan is byte-identical to the base (compared). The offline plan
  switches stage B on too, so CI plans every attachment.
- `JUDGE0_AUTH_TOKEN` joined `test_deploy_secret_hygiene.CREDENTIAL_NAMES`.

### THE `pickready.app` MAILBOXES STAY, AND THEY ARE LISTED

`docs/operations/PICKREADY_APP_ADDRESSES.md` names where `noreply@` and
`hello@pickready.app` are still written and the owner question they wait on.
It supersedes nothing: the 2026-08-29 "Naming" paragraph stated the same open
question.

### WHAT THE HAND-OFF SWEEP LEFT, AND WHOSE IT IS

Every removal-sweep PENDING list outside Phase 5 is empty, and so is the
dispatch-before-commit allowlist. What the PLAN-p7 section 7 hand-off list
still finds in live source, named so it is not mistaken for done:

- **Phase 5 (p5-d and its successors):** `rating_label`,
  `suggested_interview_questions` (kept for old-report readability, which the
  hand-off allows), `_stable_score` and `_llm_score` mentions, the
  `test_prefill_removed`, `test_yukti_legacy_removed`, `test_job_level_removed`
  and `test_coding_read_only_evaluation_removed` PENDING entries.
- **Phase 1, not taken by any package:** the older per-key JD path,
  `jd_generation.generate_job_description` with `jd_generation_system.txt`,
  `generation_sufficiency.jd_json_states` and
  `scripts/backfill_job_descriptions.py` (its only caller), and the
  `JobMatchingCategory` model over its kept table. Deleting the JD path moves
  `test_no_meta_commentary`'s gated-prompt floor, which must be done with the
  deletion named beside it.
