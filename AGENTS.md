# AGENTS.md, ReadyPick Build Conventions

Standing context for any session working on this repository.
[docs/README.md](docs/README.md) is the documentation index; **read `PRD.md`
for what to build and `ESD.md` for the architecture. This file is HOW to
build** -- the rules a change must not break.

## How to read this file

Sections are **reverse-chronological**, newest first, and that ordering is
load-bearing: a later section supersedes an earlier one wherever they touch the
same thing, and supersessions are marked in place rather than by deleting the
history that explains them. When two sections disagree, the higher one wins.

The general conventions (repository layout, coding standards, environment,
local dev) live in the numbered sections at the BOTTOM. They change rarely. The
phase sections above them are where the sharp edges are.

### Jump to a phase

| Section | What it governs |
|---|---|
| Tatva human authority (2026-09-23) | Sutra proposes; the Hiring Manager owns criteria; Save Matrix freezes the approved version; Company Profile is current company context |
| The vivekium ruling (2026-09-18) | The brief is final; the match_percent exception to rule 1, the derived seven-column words, the C2/C8 supersessions |
| The singleton that outlived its loop (2026-09-16) | Hub shutdown, per-loop binding, a suite that hangs instead of failing |
| Permission-aware UX + occupational STEM + Job SWOT (2026-09-13) | The one read-only sentence, capability-first UI, occupational classification, the AI-drafted Job SWOT |
| BGV and conversations (2026-09-12) | The employment declaration, the offer gate, native chat, the reply address, SES inbound |
| The rotated credential (2026-09-11) | The database credential split, TLS on the DSN, the primary-contact carve-out, the AI Reach contact harvest |
| Native support + runtime completions (2026-09-10) | The Support surface, the vendor sync removal, the RDS proxy refusal, W6.5, W6.6, report provenance, the golden set at 60 |
| AI runtime upgrade (2026-09-09) | The retrieval index, the tool firewall, the action ledger, the eval OS, the sufficiency gate, AI activity |
| Company DNA removed (2026-09-09) | Gate 1 on the Company Profile, the two-layer framework, the surviving detector |
| The add-features release (2026-09-06) | Corporate senders + OTP, dual-mode assessment, video access, retention consents, BGV, employer pages, intelligence dashboards |
| Background work without Celery (2026-09-05) | Dispatch, the four functions, the on-demand agent, the schedule |
| End-to-end hiring workflow (2026-09-04) | The eight gates, the sourced stage, the final ranking, the Updates feed, job closure |
| Proctoring + question formats (2026-09-02) | Mandatory monitoring, the shared warning counter, the six question formats, evidence dominance |
| Project Evidence Intelligence (2026-09-01) | Candidate projects, derived evidence, temporary originals |
| spec-doc6 (2026-08-29) | Runbook reconciliation, Part A activation, RBAC, dashboard, AWS close-out |
| spec-doc5 (2026-08-28) | The three-layer hiring framework, single-vendor models, navy/teal UI, AWS-ready |
| Tatva + PRISM (2026-08-23) | The naming split, report section order, three radar charts |
| Ten-system agent framework (2026-08-18) | Tools, agent loop, retrieval, traces, budgets |
| Product spec v4 (2026-08-14) | Role hierarchy, two job-setup outputs, validation, credit gates |
| Per-candidate questions (2026-08-06) | No preset bank, rubric-with-question, loop engineering |
| Conversational agent (2026-08-05) | Adaptive interview, non-answers, guardrails, telemetry |
| Adaptive interview + demo (2026-08-05) | Follow-up bounds, temperature policy, demo tenants |
| PPI + four-grade scale (2026-07-30) | One rating scale, per-job framework, frozen matrix |
| Subscriptions + credit ledger (2026-07-28) | Sub-units, idempotency, Razorpay |
| BD Portal + unified JD (2026-07-28) | Fourth portal, one markdown JD, procurement types |
| Provider Portal (2026-07-27) | Read-only-by-absence, compliance slots, archive |
| Job posting lifecycle (2026-07-27) | 30+5 day window, 10-stage pipeline |
| Job detail + router (2026-07-27) | No numbers to a client, inline candidates, immutable reports |
| Unified candidate profile (2026-07-27) | Main resume, profile form |
| Grade-driven assessment (2026-07-26) | Grade drives counts, scoring reads real answers |

### The rules that break the most builds

1. **No number ever reaches a client.** Scores are internal; conversion to one
   of four words happens server-side at the serializer. **ONE AMENDMENT,
   owner-ruled 2026-09-18 (vivekium brief is final): the Executive Profile
   Match Score, `match_percent` on the recruiter candidate table, is the one
   sanctioned number.** It is `job_candidate_links.match_score` rounded to an
   integer at the serializer and it is the WHOLE exception: grades everywhere
   else stay words, the per-parameter scores stay internal, and
   `test_platform_audit.py` pins the exception at exactly one field.
2. **Permissions are data, never a role branch.** `require_capability(...)`,
   and a new capability constant is only HALF a change -- the seeding migration
   is the other half.
3. **Every tenant-scoped query goes through the RLS-aware session.**
4. **All slow work is DISPATCHED**, never inline in a request handler.
   `dispatch("pickready.x", args=[...])`, never Celery: it was removed on
   2026-09-05 and the dependency is gone from `requirements.txt`.
5. **One implementation per concept.** No dual code paths for one behaviour.
6. **No silent fallbacks.** No bare `except`, no default substituted for a
   failed retrieval, no template output presented as generation.
7. **No em dash anywhere**, including in seeded and generated content.
8. **A timestamp is not evidence that work happened.** Check the table.


## Current hard rules, Tatva human authority (2026-09-23)

**Sutra proposes; the authorized Hiring Manager decides.** Sutra compiles an
initial Tatva draft and the technical metadata needed to assess it. The Hiring
Manager owns the product-facing criteria: existence, name, category, order and
the exposed importance controls. Enrichment after a human edit preserves those
decisions. Save Matrix validates and freezes exactly the reviewed matrix as a
version; assessments and reports use that version, and a later revision cannot
rewrite a candidate's earlier assessment contract.

The Company Profile is the current company-level context. Jobs snapshot its
narrative sections at creation. Drishti is an optional functional strategic
profile and is distinct from the retired Company DNA instrument. Company DNA
has no live API, UI, gate, table, or assessment dependency. Older sections and
migrations that name it are historical records, not implementation directions.
Inspect the checked-out code before applying older design descriptions.

### SAVE MATRIX ENRICHES, THEN FREEZES, AND THE REFUSAL IT REPLACED WAS THE BUG

`scorecard.freeze` calls `_enrich_reviewed_rows` before it writes a binding.
What it replaced was a validation that read the rows and refused: **"These
criteria carry no derivation and cannot be frozen"**. That sentence was the
defect, not the diagnosis. Adding a criterion is the product's NORMAL way into
this form, and adding one produced a state the freeze then called invalid, so
the supported workflow terminated in an instruction to rebuild the matrix the
reviewer had just finished reviewing.

- **All model work and all validation finish BEFORE the first row is
  changed.** A refusal therefore cannot leave a half enriched matrix, and the
  whole thing shares the request transaction, so a raise anywhere rolls back
  the enrichment, the stamps and the binding together.
- **`transformation.build_item(preserve_name=True)` on the reviewed path.**
  Draft compilation may take the department model's canonical name; a reviewed
  criterion may not. The anchor supplies technical evidence and never a
  replacement label.
- **`load_frozen_matrix` refuses a PARTIAL matrix exactly as hard as an absent
  one**, which is what makes enrichment mandatory at freeze rather than best
  effort: one un-enriched row makes the job un-scoreable rather than half
  scoreable.

### AN OUTAGE IS NOT A BADLY WRITTEN CRITERION

`_name_unanchored` falls back to an empty naming result, so a degraded run
refuses EVERY pending phrase with the same stock reason. The freeze path read
the FIRST refusal and told the reviewer to edit that one criterion. It blamed
an arbitrary criterion for a provider outage, and the reviewer could not fix it
by editing, so they would edit until the provider came back.

- **`degraded` is the only thing that separates the two cases**, and it was
  being discarded into an underscore. It now raises its own message, naming no
  criterion at all.
- **A genuine refusal names EVERY criterion it refused**, not the first.
  Telling somebody about the first of four means four round trips, each
  reporting the matrix as broken.

### A RENAME CLEARS `swot_origin`. A REVIVE DOES NOT. THAT ASYMMETRY IS THE RULE

`swot_origin` carries the reporting authority's own SWOT sentence and
`plain_provenance` renders it to the reviewer verbatim as `You said: "..."`.

- **`_invalidate_derived_criterion` clears it**, because a rename is a
  different criterion wearing the row's identity. Carrying the sentence across
  attributes a quotation about "Kubernetes operations" to a criterion now
  called "Incident command", which is a FABRICATED CITATION on the one screen
  this contract exists to make trustworthy. A fabricated citation is worse
  than a missing one: it reads as provenance.
- **`_revive` deliberately keeps it**, unchanged from 2026-09-21. A revived
  row is the SAME name coming back, so the sentence still refers to it.

### A REBUILD MAY NOT DISCARD A HUMAN DECISION

The default compile path is idempotent: a redelivered message finds active
rows and returns them. `replace=True` skips that guard, and the deprecated
`pickready.generate_ppi_framework` alias FORWARDS it, so a message sitting on
the broker under the old name is a live route to rewriting every name,
category, requirement level and ordinal on the draft.

- **The refusal is in `compile_matrix`, before Layer 3 and before the naming
  call**, the same ordering the Job SWOT regeneration rule already follows: a
  refusal after the model call has spent the budget it was meant to protect.
- **The signal is `swot_origin IS NULL` on an active row, and it is READ
  rather than stored.** Every row the compiler writes carries the sentence it
  came from. A criterion the hiring manager ADDED never had one, and a RENAME
  clears it. So the absence means exactly "this entry is the human's, not this
  compiler's", which is the question being asked, with no new column.

### REOPEN IS BOUNDED BY THE ISSUED CONTRACT, AND BOTH EARLIER BOUNDARIES WERE WRONG

- **It asked for a `functional_skills_reports` row** until 2026-09-22. A report
  exists only at the END of an assessment, so between invitation and synthesis
  the matrix was reopenable underneath somebody already answering questions
  derived from it. Their questions came from one version and their grade would
  have been written against another, with nothing recording it.
- **It then asked for any `job_candidate_links` row**, which refuses to reopen
  the moment the first CV lands on a job nobody has been invited to. There is
  no reopen after that, so a typo caught on the day the posting went live
  became permanent. Applying is not being assessed: a link is written by an
  application, a sourced upload and a databank import, none of which reads a
  competency.
- **It now asks whether an assessment contract has been ISSUED**: an
  `assessment_conversations` row, which IS the invitation, or a
  `candidate_questions` row. Somebody who applies before a revision and is
  invited after it is assessed against the revision, which is the currently
  approved contract and the only one ever used on them.

**`orchestration/versioning.resolve_for_application` HAS NO PRODUCTION
CALLER.** It is implemented and tested and nothing on the live scoring path
invokes it, so "a candidate is assessed against the version in force when they
applied" is NOT in force. The reopen guard is a blanket prohibition standing in
for it. Anybody who believes the resolver is load bearing will relax that guard
and silently move a candidate's contract, which is why it is said here and in
`models/job_scorecard_binding.py` rather than left to be discovered.

### THE REMOVAL SWEEP HAD A BLIND SPOT, AND IT WAS EXACTLY ONE LINE WIDE

`test_company_dna_removed.py` swept `app/`, `tests/` and `scripts/` one LINE at
a time, so a mention wrapped across a newline never matched a pattern
containing a space. One had been sitting in `workers/tasks.py` since the
removal, describing a compiled company instrument as a live precondition of
Sutra, and it passed every run for two weeks. The sweep normalises whitespace
now and maps offsets back so a hit still names a line. **A sweep with a blind
spot is worse than no sweep, because the green result is what stops anybody
looking.**

Live runtime carries no reference of any kind. What survives, deliberately:
the `dna` correlation kind with no issuer (stored traces carry `dna-` ids and
dropping the kind would make `is_correlation_id` call valid history
malformed), the `company_layer2` provenance KEY (Layer 2 is Drishti since
2026-09-19 and renaming the key would make stored provenance unreadable), and
`job_scorecard_bindings`, renamed with its rows intact by migration 0088.

### `administration` IS NOT A GITHUB ACTIONS PERMISSION

`verify-approval-gate` declared `permissions: administration: read` on
2026-09-17, on the advice of its own 403 message. `permissions:` accepts a
closed set of scopes and `administration` is a fine-grained PAT permission that
is not among them. **An unknown key there fails the workflow file at
VALIDATION, before any job is created**: every run since recorded 0 seconds, no
jobs and no logs, and the `pull_request` trigger stopped firing entirely, so
the integration PR reported "no checks" while thirty commits landed on it.

The scope is unobtainable from `GITHUB_TOKEN` under any configuration, so the
job takes `APPROVAL_GATE_TOKEN` when configured and FAILS naming that when it
is not. The check exists because a gate nobody can run is a gate nobody has;
repairing it with a key that stops the file parsing did that to every other
gate in the pipeline.

## Current hard rules, the vivekium ruling (2026-09-18)

The owner ruled, verbatim: "whatever is given in vivekium is ultimate final
source of truth." `docs/spec/VIVEKIUM_SPRINT_FEATURES.md` section 3 carries
each conflict's resolution in place. The ones that amend standing rules:

- **Rule 1 is amended, narrowly.** `match_percent` (the Executive Profile
  Match Score on the recruiter candidate table) is the ONE number that
  reaches a client. It is `match_score` rounded at the serializer in
  `job_candidates._row_payload`, and nothing else moved: parameter scores,
  grades and every other surface stay words. Pinned at exactly one field by
  `test_platform_audit.py`.
- **The seven-column words are DERIVED, never stored**
  (`services/recruiter_columns.py`): CTC Match against
  `compensation_json.ctc_min/ctc_max` (inclusive boundaries, rule 8), the
  notice-period bucket keyed by the form's own options, Education Match as a
  five-rung ladder where Partial is exactly one rung short, and BGV Status as
  a re-wording of `bgv_workflow.derive_status`. **None means "Not stated"**:
  an absent or unparseable input renders no comparison, because a fabricated
  word beside a hiring decision is worse than an honest blank. Nothing here
  scores, ranks, or gates anything.
- **The question ceiling (C2) will make the coverage plan resume-dependent**
  when feature 2 lands, superseding "the coverage plan stays deterministic"
  for COUNT while keeping it for criteria ORDER; a criterion the resume
  already evidences is pre-filled, not silently dropped.
- **`verification_requests` is the retiring BGV system (C8)**;
  `bgv_inquiries`/`bgv_verifications`/`candidate_employments` is the one the
  brief describes and the one that survives.

## Current hard rules, the singleton that outlived its loop (2026-09-16)

No migration. One module, `services/realtime`, and a class of bug this
repository had not written down: a process-wide singleton holding asyncio state.

### A SUITE THAT HANGS IS WORSE THAN ONE THAT FAILS, AND `py-spy` IS THE ANSWER

The backend suite stopped at roughly a quarter with no failure and no output,
and the last thing printed was a passing dot. Every instinct here is wrong:
the percentage is unreliable (pytest block-buffers to a file, and a hard kill
on Windows does NOT flush, so the log under-reports), the DB showed nothing
blocked, and the file blamed by the arithmetic passed in four seconds alone.

**`py-spy dump --pid <pid>` named it in one line**, from outside the process,
with no rerun and no instrumentation. Reach for it FIRST when a run is quiet
rather than red. `pytest --timeout=N --timeout-method=thread` (pytest-timeout)
turns the hang into a failure with a stack; pytest 9 removed
`--faulthandler-timeout`, so do not reach for that.

**A SECOND CAUSE, 2026-09-18, and it is not a deadlock in the product at all.**
The suite went quiet at 36% and `py-spy` again named it in one line:
`test_import_graph.py` blocked in `subprocess.run`. That test spawns a FRESH
interpreter per module on purpose, 27 of them, and several import pandas, which
costs about twelve seconds each on a cold interpreter on this machine. It has a
`timeout=120` and passes in 342 seconds when run alone.

**What broke it was running the suite CONCURRENTLY WITH TWO EMULATED ARM64
DOCKER BUILDS.** Under that load a pandas import crossed 120 seconds, and once
`subprocess.run` raises `TimeoutExpired` it calls `kill()` and then
`communicate()` AGAIN WITH NO TIMEOUT, which on Windows can block for ever. So
a slow import became a permanent hang, and the last thing printed was a passing
dot.

This is the same lesson as "never run two `scripts/test.sh` invocations against
one database", with CPU as the contended resource instead of Postgres. **Do not
run the authoritative suite beside a container build**, or beside the graphify
rebuild the commit hook launches.

**AND THE OBVIOUS DIAGNOSTIC IS A TRAP, WHICH COST A SECOND WRONG CONCLUSION
HERE.** Sampling the PARENT's CPU twice and seeing it flat reads as "frozen",
and that reading is wrong: a parent blocked in `communicate` while its child
works legitimately burns no CPU, so flat CPU is the NORMAL state of this test
rather than evidence of anything. `py-spy dump` on the parent is equally
uninformative for the same reason, because it always shows `join
(threading.py)` under `_communicate`.

**ASK ABOUT THE CHILD.** `Get-CimInstance Win32_Process -Filter
"ParentProcessId=<pid>"` answers it in one call, and the answer separates the
two states cleanly:

- **A live child, `active` in `get_data`**, is an import grinding through disk.
  The run is SLOW and will finish. Sample the child's command line a few times
  and watch the module name advance.
- **No child at all, parent still in `join`**, is the real hang: the timeout
  fired, `subprocess.run` called `kill()` and then `communicate()` AGAIN with
  no timeout, and on Windows that second call can block for ever.

Only the second is worth killing, and one command tells them apart.

### THE THREE RULES THE HUB NOW FOLLOWS

- **A LONG-LIVED TASK IS STOPPED BY THE LIFESPAN, NEVER ONLY BY ITS LAST
  USER.** `leave()` stopping the reader when the last socket goes covers the
  quiet case and not the real one: a deploy stops a task with sockets still
  open. `lifespan` calls `hub.shutdown()` BEFORE `get_engine().dispose()`, so
  the loop is torn down with nothing still awaiting on it. On Linux, skipping
  this is untidy; on Windows it HANGS, because `ProactorEventLoop.close()`
  waits on outstanding overlapped I/O.
- **A TASK ON A CLOSED LOOP IS NEVER `done()`, SO NEVER GUARD ON `done()`.**
  That guard made the hub believe it was still subscribed and stop delivering
  to every socket on the instance, permanently, with nothing logged because
  nothing failed. The hub records the loop it is bound to and starts clean on a
  new one; the lock is per-loop for the same reason, since reusing the one a
  dead loop was waiting on is how a singleton becomes a deadlock.
- **A SHUTDOWN PATH THAT CAN HANG MOVES THE BUG RATHER THAN FIXING IT.** Both
  awaits in `_stop_reader` are bounded and a timeout is logged. Giving up is
  safe HERE and nowhere else: the process is leaving, so an abandoned socket is
  reaped by the kernel.

### AN `after_commit` HANDLER RUNS INSIDE `commit()`, SO IT RAISES AT THE CALLER

`hub.publish` already refused to fail a send it could not announce. The two
lines that SCHEDULED it undid that: a RuntimeError from `loop.create_task` came
out of `session.commit()` and 500'd the request AFTER the message was durably
stored, so the sender was told their message failed while everyone else could
already read it. Guard anything you schedule from a SQLAlchemy event the way
you guard the work itself, and `close()` the coroutine you did not schedule --
an un-awaited coroutine warns from whatever unrelated code the collector
happens to be running, which is harder to trace than the failure behind it.

### TWO PROBING MISTAKES THAT BOTH READ AS FACTS

- **`set_config('app.bypass_rls','on',true)` IS TRANSACTION-LOCAL.** Passed
  `true` outside an explicit transaction it is discarded immediately, every
  later query runs under RLS, and the answer is zero rows -- which is
  indistinguishable from an empty database. It reported "no jobs in pilot"; the
  truth was 31. Pass `false` for a session-level setting when probing.
- **NEVER RUN TWO `scripts/test.sh` INVOCATIONS AGAINST ONE DATABASE.** It DROPs
  and recreates `readypick_test`, so a concurrent run reports
  `UndefinedTableError` and "connection was closed in the middle of operation"
  and looks exactly like 17 real failures.

## Current hard rules, permission-aware UX + occupational STEM + Job SWOT (2026-09-13)

Migrations 0096 and 0097. Three pieces of one specification: make the
authorization model reach the whole interface, classify the OCCUPATION rather
than the keywords, and give a job an AI-drafted SWOT the recruitment team owns.

### The read-only sentence has exactly one author

A restriction message may appear when a user can view a resource and genuinely
cannot edit it, and at no other time. It is rendered by
`components/permission-notice.tsx` and by nothing else, because that component
returns null when `canEdit` is true and therefore cannot contradict the server.

The bug this replaces was not a permission bug. The company profile page
computed `canEdit` correctly and then wrote

```tsx
{canEdit && editing ? <Save/> : <p>You have read-only access...</p>}
```

so a user who HELD `edit_company_profile` and had simply not clicked Edit was
told they did not hold it. `lib/read-only-messaging.test.ts` walks the source
tree and fails on any screen that writes restriction copy of its own.

- **Ask `can(...)`, never the role.** `lib/permissions.ts` (pure: the capability
  constants, the wording, `resolvePermission`) and `lib/use-permissions.ts`
  (the hook) are the interface's whole permission vocabulary. Import `CAP.x`
  rather than a string literal.
- **A resource-scoped answer from the server beats the capability list.** A
  capability can say "may edit SWOTs"; it cannot say "on THIS job", because
  assignment scope and lifecycle state belong to the job. Endpoints that know
  return it on the payload (`can_edit`), resolved by the SAME `rbac.authorize`
  call the write route enforces with, and the component combines the two
  through `resolvePermission`. Neither is a security boundary; both routes
  re-authorize.
- **A 403 refreshes the capability snapshot.** `api.onForbidden` tells the auth
  context that its copy is stale, and a navigation revalidates it at most once
  a minute. The server was never stale; the client was.

### STEM classification reads the occupation, not the keywords

`services/stem_classification` now has two layers. The BODY pass is unchanged.
The OCCUPATIONAL layer parses the job title into a head noun and its domain
qualifiers and applies the verdict as a floor (`TITLE_STEM_FLOOR`) or a ceiling
(`TITLE_NON_STEM_CEILING`) on the body score.

Before it, every one of these was stored and billed as Non-STEM whenever the
job description was thin: Software Engineer, Software Developer, Data
Scientist, Electronics Engineer, Research Scientist, Cloud Engineer.

- **A recognised Non-STEM domain decides the title outright**, before the head
  noun is read. An HR Manager, a Finance Manager and a Sales Engineer are
  Non-STEM whatever technology sits around them.
- **`analyst` is never decided by the title alone** unless its qualifier names
  a mathematical practice. A Data Analyst writing marketing reports is
  Non-STEM, and the body pass is what says so.
- **It is three vocabularies, not a list of job titles.** A title nobody has
  seen resolves from the head noun and the domain that compose it. Do not
  "fix" a misclassification by adding the title.
- **Migration 0096 re-ran the engine over historical jobs**, skipping any job
  that is `classification_locked` (a report has been billed against its rate)
  or `classification_overridden` (a Provider admin already ruled on it).

### The Job SWOT Analysis is AI-drafted and human-owned

`job_swot_analyses` (migration 0097) is a DOCUMENT. `job_swot_intakes`
(migration 0049) is the reporting authority's TRANSCRIPT and feeds Sutra. They
are different tables on purpose: editing the document is the feature, and
rewriting the transcript would launder the evidence the Tatva matrix is
derived from.

- **No new capability.** Writing takes `edit_swot`, which RBAC 24 already
  governs; reading takes `view_company_jobs`. The three WRITES go through
  `rbac.require_authorized`, so tenant, ceiling, grant, assignment scope and
  lifecycle state all run. The READ uses `require_capability`, because
  `view_company_jobs` is SCOPED for three roles and nothing in this product
  writes `job_assignments` yet: a scope check there would refuse a Recruiter
  the SWOT of a job whose JD is on the same page. Tighten it the day
  assignments are written.
- **A regeneration over human-edited content is refused** unless the caller
  confirms it, the refusal happens BEFORE the model call, and a confirmed
  replacement snapshots what it replaced into `previous_json` so it can be put
  back. `human_edited` latches and is never cleared.
- **A failed generation is a STATE**, not a template. It writes
  `status=failed` with the reason and leaves existing content alone. No
  deterministic SWOT is ever presented as generated output (rule 6).
- **`swot_analysis` is the second and last member of the generative
  interactive LLM tier.** `tests/test_platform_audit.py` caps the list at two.

## Current hard rules, BGV and conversations (2026-09-12)

Two features, built together because one is the other's first real user: the
BGV Agent drafts an email to a previous employer, and the employer's reply has
to land somewhere. Migration 0095.

### THE OFFER GATE IS IN `apply_transition`, AND THAT IS THE WHOLE ENFORCEMENT

`hiring_pipeline.apply_transition` is the single chokepoint all six pipeline
callers reach, and the gate sits there, after `assert_transition` and BEFORE the
first write. A candidate who declared previous employment cannot be moved to
`offer_extended` or `offered` until every employer they submitted is marked
verified by a person.

- **REJECTION IS NEVER BLOCKED, and a fresher is never blocked.** A gate that
  could stop a rejection would trap somebody in a pipeline over paperwork, and a
  fresher has no previous employer to verify: `derive_status` answers
  `not_required` and the gate never fires.
- **THE STATUS IS DERIVED, NEVER STORED**, like `profile_age` and
  `posting_status`. `bgv_workflow.derive_status` is a pure function of the
  declared background, the employer count and the per-employer statuses, so a
  stored value can never disagree with the rows it was computed from.
- **ONE `not_verified` DOMINATES.** Checked before anything else, including
  before "no verifications exist yet": an employer the team has actively refused
  is a stronger fact than an incomplete process.
- **The screen renders the SERVER's refusal sentence verbatim**
  (`offer_blocked_reason`). It is the exact string `apply_transition` would
  refuse with, so the UI can never promise something the pipeline then refuses,
  which is the specific way a gate becomes infuriating.

### THE EMPLOYMENT HISTORY IS THE CANDIDATE'S, ONCE, AND IT IS FINAL

Owner decision: one history, per-tenant verification. The candidate submits
employers ONCE; each hiring tenant runs its own `bgv_verifications` records
against that one list. `candidate_employments` is deliberately TENANT-FREE.

- **Immutability is a TRIGGER, not a code path.**
  `candidate_employment_is_final` raises on any INSERT, UPDATE or DELETE once
  `candidates.employment_history_finalized_at` is set. A rule enforced in a
  service is a rule the next writer of that table does not know about.
- **The warning the candidate reads is SERVED BY THE SERVER**
  (`SUBMISSION_WARNING`), so the sentence describing the rule and the rule
  itself cannot drift.
- **The HR contact's address reaches the recruiter running the verification and
  nobody else.** It is on no list endpoint and no cross-tenant response: it is a
  third party's personal contact detail a candidate handed over for one purpose.

### THE BGV AGENT WRITES FROM A FACT BLOCK, AND GROUNDING IS CHECKED

`bgv_agent.FactBlock` is a frozen dataclass with seven fields and no free-form
escape hatch, so a prompt cannot be handed anything the candidate did not
submit. `verify_grounding` runs inside the existing `agent_loop`, and a failure
returns the DETERMINISTIC template with `generated_by_ai=False`, which the
recruiter is told, because template output presented as generation is a lie
about how the text was produced.

**Nothing infers a verdict from the reply.** A person reads the employer's
answer and presses Verified or Not verified.
`tests/test_inbound_conversation_reply.py` asserts that an arriving reply stamps
`responded_at` and changes no status, no `decided_by` and no `decided_at`,
because an inferred "verified" would be an automated hiring decision wearing a
convenience feature's clothes.

### CONVERSATIONS: THE SOCKET IS A NOTIFICATION, THE DATABASE IS THE RECORD

A message is written to Postgres FIRST and announced SECOND. A dropped frame, a
sleeping background tab, a network change and a deploy that moves a connection
to another API task are all the same event, and none of them can lose a message
because the message was never only in flight. Every (re)connect refetches.

- **`BackgroundTasks` DOES NOT GIVE YOU "AFTER THE COMMIT", AND BELIEVING IT
  DOES IS THE TRAP.** FastAPI sends the response, and therefore runs background
  tasks, INSIDE the dependency exit stack, so a background publish fires before
  `get_tenant_db` commits. `realtime.publish_after_commit` hangs off
  SQLAlchemy's `after_commit` instead, which is the one event that means what it
  says. `tests/test_conversations_api.py` asserts the ordering from a SECOND
  connection and is what caught it. A rolled-back request now publishes nothing
  at all, which is the half that matters: a notification for a message that was
  never stored would have every listening tab render one that does not exist.
- **The room is keyed by TENANT and conversation.** Keyed on the conversation
  alone it would be a cross-tenant broadcast waiting for an id collision, and
  nothing would report it. The same rule every cache key already follows.
- **The queue is BOUNDED and overflow is dropped**, which is safe ONLY because
  the message is already in Postgres. A hub that awaited `queue.put` would let
  one asleep background tab stall the fan-out for every other browser on the
  instance.
- **THE SOCKET ACCEPTS NO FRAME THAT WRITES.** It authenticates from the same
  cookie, resolves `USE_CONVERSATIONS` live rather than trusting the token, and
  authorises the same row. Sending is a POST, which is where idempotency, the
  audit trail and the email bridge live; a socket that could write would be a
  second send path with none of them.
- **A BGV thread REFUSES a chat send**, 409 with the reason. An employer message
  is a verification act with its own capability and its own status transition,
  so chat must not become an unaudited way to contact a former employer.
- **The candidate's side is a SECOND ROUTER on the candidate audience.** A
  candidate has no tenant, so RLS by tenant cannot apply and `get_candidate_db`
  runs in the bypass scope: every candidate handler filters by the candidate id
  resolved from their own session. The recruiter's handlers are NOT reused with
  a different dependency, because that would be one function with two security
  models and the weaker one would be invisible in the code.
- **A candidate cannot reach the BGV thread about themselves.** It carries their
  `candidate_id`, so the lookup checks the KIND as well as the owner. Pinned by
  a test, because "is this yours" is the obvious simplification and it would
  hand the candidate their former employer's words about them.
- **Idempotency is a client token the CLIENT mints.** The server cannot derive
  it: a double click, a retry after a lost response and a reconnect that replays
  the send all arrive as distinct requests with identical content, and a content
  hash would refuse a candidate who legitimately wrote "yes" twice.

### THE REPLY ADDRESS IS THE ROUTING, AND A SUBJECT LINE IS NOT

Every verification request carries
`Reply-To: conversations+<thread token>@<inbound domain>`. The inbound webhook
reads the token from the ADDRESS, which is the one part of a message every mail
system on the path reproduces verbatim. Matching on a subject loses to
`Re: Fwd: Re:`, to a translated prefix, to a client that rewrites the subject,
and to a recruiter forwarding the thread, and every one of those failures is
silent.

- **`INBOUND_EMAIL_DOMAIN` EMPTY IS A REAL STATE.** The deployment still sends;
  the reply arrives in the sending mailbox rather than in the thread, and
  `conversations.reply_address` logs that once rather than producing a thread
  that can never receive anything.
- **Idempotent on the sender's Message-ID.** SNS delivers AT LEAST ONCE, so a
  redelivery is the default behaviour unless something prevents it.
- **The inbound parser is a SECOND ZIP LAMBDA**, standard library and boto3
  only, for the reason the ECS trigger gives: anything that can send mail to the
  reply domain reaches it. It holds no database credential and no model key. S3
  plus SNS rather than a direct Lambda action, because that path caps a message
  at 256KB and a reply with a scanned letter attached is routinely larger.
- **A SUBDOMAIN, never the apex.** Receiving mail means owning the MX record,
  and the apex's MX belongs to whatever mailbox the company actually reads.
  `reply_domain` validates that it carries at least three labels.
- **ONE ACTIVE SES RECEIPT RULE SET PER REGION PER ACCOUNT.**
  `activate_rule_set` defaults to false so an environment has to say it is the
  one receiving, and a second environment claiming it is then a merge conflict
  rather than an outage nobody can see.

### PILOT CAN BE PLANNED OFFLINE NOW, AND COULD NOT BEFORE

Three `data "aws_caller_identity"` lookups and two missing
`offline-plan.tfvars` entries meant `infra/plan-offline.sh` stopped before it
reached anything in that environment. The data source calls STS, and the
planning profile runs against account 000000000000 in a region that does not
exist. **The account id was already a required variable in every environment**,
so this is the same fact read from the input rather than from the network, and
`scheduler` already took it that way. Staging and production were failing on the
same lookup inside the `lambda` module and now plan clean.

**A pre-apply check that cannot run is a check nobody reads**, which is the same
argument the impeccable gate makes about a detector that only prints warnings.
That gate also stopped judging gitignored build output: it was failing on the
graphify knowledge-graph viewer, generated HTML nobody wrote and nobody can fix
without changing a third-party renderer. Asked of `git check-ignore` rather than
hardcoded, so it cannot drift, and it excludes nothing that ships, because an
ignored file is by definition one nobody reviews in a diff.

### The candidate nav is FIVE entries now

Messages joins New Jobs, Applied Jobs, Updates and My Profile, which AMENDS the
2026-07-27 rule that the nav is exactly three. That rule was written when every
word from a company arrived by email. A candidate can now be WRITTEN TO inside
the product, and a reply box they cannot find is an outbox rather than a
conversation.

## Current hard rules, the rotated credential (2026-09-11)

The product was DOWN for part of this day and nothing had been deployed. It is
the most useful outage this project has had, because the cause was a sentence
in this repository that had described the right design since the day it was
written, and had never been true.

### THE APPLICATION'S DATABASE CREDENTIAL IS ITS OWN, AND NOTHING ELSE ROTATES IT

`infra/modules/rds`'s header has always read "THE APPLICATION DOES NOT USE THE
MASTER CREDENTIAL. `DATABASE_URL` is a separate secret holding a
least-privileged application role; the master exists to create that role and to
run migrations." **That role had never been created.** `DATABASE_URL` was
hand-composed with the RDS master username and a COPY of its password, and
`manage_master_user_password = true` hands that password to Secrets Manager to
rotate on a schedule. Seven days after the pilot instance was created, AWS
rotated it. Every connection in the product failed at once: the API, `/health`,
and therefore sign-in.

- **A copy of a credential something else rotates is an outage with a date on
  it.** Not a risk, a schedule. The only durable fix is to stop holding the
  copy, and `pickready_app` already existed for exactly this: migration 0001
  created it and every migration since has maintained its grants. It was
  NOLOGIN, which is the one thing missing.
  `app.scripts.provision_app_db_role` gives it LOGIN and a password ReadyPick
  owns, and `scripts/rotate-app-db-credential.sh` runs it again to rotate. The
  password is minted INSIDE the task and written straight to Secrets Manager,
  so it is never an argument, never in a RunTask call, never in CloudTrail and
  never in a shell history.
- **It PROVES the new credential before it writes the secret.** Opening a second
  connection with it and reading through the policies, then writing. The other
  order would end this outage by causing it.
- **NOINHERIT, and the migration job is the only thing that escalates.** The
  login role is a member of the object owner, so `alembic/env.py` can
  `SET ROLE` for DDL; NOINHERIT means an ordinary session holds none of the
  owner's privileges until it asks. `POSTGRES_MIGRATION_ROLE` is set on the
  `migrate` container and on nothing that serves a request, and
  `test_app_db_credential.py` sweeps every environment's Terraform to keep it
  that way. Two consequences fall out and both are improvements: the app can no
  longer run DDL at all, and `REVOKE UPDATE, DELETE ON audit_log` finally binds,
  because it never bound while the app connected as the owner.
- **`PutSecretValue` is a grant, and it is one service over one secret.**
  `service_secret_writers` in `infra/modules/secrets`, enumerated like the read
  map, with a precondition that refuses a writer naming a service that has no
  read entry -- the policies are built with `for_each = var.service_secrets`, so
  that entry would otherwise be dropped in silence and the rotation would fail
  after it had already changed the password.

### THE DSN CARRIES `ssl=require`, AND THE REASON IS THE ERROR MESSAGE

asyncpg's default `prefer` mode retries a refused connection WITHOUT TLS. So the
traceback said

    no pg_hba.conf entry for host "10.0.11.22", user "readypick_admin",
    database "readypick", no encryption

which reads as a network or TLS fault and sent the first hour of the
investigation in the wrong direction. The database's own log had the answer one
line earlier: `password authentication failed`, on a connection that had matched
the `hostssl` rule perfectly. **A fallback that changes the error message is
worse than no fallback**, and this one also meant the product was willing to
carry a tenant's data across the VPC in the clear. `?ssl=require` removes both.
`_compose_dsn` carries the query string across verbatim so a rotation cannot
drop it, and a test asserts that.

- **READ THE DATABASE'S LOG, NOT ONLY THE APPLICATION'S.** `log_connections = 1`
  is on in the pilot parameter group and it is what settled this in one minute
  after an hour of reading tracebacks.

### The Provider may set a customer's primary contact. One route, and it is named

Read-only-by-absence stands. The carve-out is `PUT
/provider/customers/{id}/primary-contact` and its justification is that the
primary contact is not the customer's own data in the sense the rule protects:
it is the DOOR into the tenant. Onboarding collected the address once, so a typo,
an expired invitation, or a tenant seeded without one left a customer
permanently unreachable and no route anywhere could repair it.

- **`test_provider_portal.PROVIDER_WRITES` now pins the EXACT set of writes.**
  An inequality only forbids the shapes somebody thought of; the exact set
  makes a second carve-out a test change with a justification attached.
- **Changing a bound account's email is a REBIND, never a field write.** Auth
  resolves an identity by `firebase_uid` OR email, so an account that kept its
  uid while its email moved would leave the ORIGINAL person signed in under the
  NEW address. The uid is cleared, the row returns to `invited`, the prior
  pending invite is revoked, a fresh one is sent, and `rebound` is both audited
  and serialized so the screen can say what happened before it happens. The
  same applies to a BD account: `PATCH /admin/bd-users/{id}` now accepts an
  email and rebinds identically.

### AI Reach: a published mailbox is READ, never inferred

Every card reported no contact, for every company. Two defects wore one symptom,
which is why "it cannot find a single usual company site" was both true and not
about finding sites.

- **The evaluator is forbidden from inferring a contact, and a search snippet
  almost never prints one.** So the model correctly reported nothing, forever.
  `web_research.harvest_contacts` re-reads pages ALREADY FETCHED and can only
  find what a page actually published, which meets the never-infer bar by
  construction instead of by instruction. Three guards: the address's
  registrable domain must be the company's own (a job board's mailbox is never
  attributed to the employer it lists), the local part must be on a role-mailbox
  allowlist (a named person's address on a page is not a hiring contact, and
  publishing it would be scraping), and `contact_source_url` is always the page
  it appeared on. An evaluator-verified contact is never overwritten.
- **The official-site link existed the whole time, in `text-brand-700` on a
  dark surface.** Dark navy on dark navy. A link nobody can see and a link that
  does not exist are the same bug report, and no amount of work on the retrieval
  half would have fixed it.

## Current hard rules, native support and the runtime completions (2026-09-10)

Owner decisions this day: the Intercom integration is DELETED and replaced by
an in-product Support surface; EEO reporting and impact ratios are REFUSED
rather than deferred; the pilot RDS proxy is NOT built, with the vendor's
pinning documentation as the reason. Migrations 0093 and 0094.

- **Support is native, and the candidate boundary moved from a payload
  allowlist to a schema.** `support_threads` and `support_messages` (0093),
  `services/support` owns the FSM, `api/support.py` carries both routers.
  The thread status is DERIVED from who wrote (`status_after_message` takes
  the author's side and nothing else), so "waiting on ReadyPick" can never
  mean "threads somebody remembered to mark". `open | awaiting_customer |
  resolved`, named for who owes the next move; `resolved` reopens on a
  customer reply. `author_side` is denormalised at write time so a later role
  change cannot rewrite who said what. RLS is plain tenant equality in BOTH
  directions, and the message row carries its own tenant_id because a policy
  that joins to the parent evaluates against rows the session cannot see.
  No candidate identifier, score, grade or evaluation detail may reach a
  support message; enforced structurally (the write path imports no candidate
  model, neither table has a candidate-shaped column, the notification email
  carries no message body), swept by `test_support_candidate_boundary.py`.
  `tests/test_intercom_removed.py` keeps the vendor gone, absolutely: live
  source does not name it, per the c718694 precedent.
- **`handle_support_threads` is a PLATFORM capability outside
  `DEFAULT_PERMISSION_MATRIX`,** seeded as a global row by 0093, because the
  matrix is copied into per-tenant rows for every new customer and the role
  holding it has no tenant. It is the notification ROUTING list, not a route
  gate; the Provider routes stay behind `get_superadmin_db`.
  `open_support_threads` sits in the matrix AND in the interview_manager
  entry, and the second half is load-bearing: `seed_dev_data` RECONCILES
  global rows to the matrix, so a grant living only in a migration is flipped
  to False the first time the dev seed runs. The full suite caught exactly
  that; a targeted run structurally could not have.
- **The pilot RDS proxy was evaluated against the vendor's documentation and
  refused.** RDS Proxy for PostgreSQL pins a session on any SET command, on
  `set_config()`, and on named prepared statements; only transaction-level
  advisory locks are exempt. This application issues `SET LOCAL ROLE` in
  every tenant transaction, a session-level `set_config` on every worker
  connection, and asyncpg caches prepared statements, so effectively every
  session pins: no multiplexing, and the "instance connections stay flat"
  acceptance test is unpassable by documented behaviour. The instance went
  `db.t4g.micro` to `db.t4g.medium` instead (pgvector HNSW working memory,
  and the max_connections ceiling scales with instance memory), the storage
  CEILING to 200GB with the floor kept at 50, and `multi_az = false` now
  says in place that it must flip before any real tenant's data arrives.
- **W6.5 lives at ACQUISITION, not in the sufficiency gates.**
  `rag/acquisition.acquire`: one broadened retry (section filter dropped,
  pool doubled and capped), bounded by STRUCTURE (two attempts exist in
  straight-line code, no loop) and by the predictive deadline rule. Scope is
  never broadened: tenant, source type, source ids and version pin stay
  exactly as asked, because a widened scope is an isolation bug wearing a
  recall improvement's clothes. The gates' EMPTY_STATE_COPY contract is
  untouched and the module imports neither the gates nor anything that
  scores.
- **W6.6: the ledger's `contradicts` stance finally has a writer.** The read
  side existed end to end (CLAIM_CONTRADICTED grades MATERIAL and routes to
  `needs_human_review`); nothing had ever written the stance, so "I have not
  used Kafka" was filed as SUPPORT for the Kafka claim.
  `evidence/negative.py` detects first-person disclaimers deterministically,
  a denial's reach ends at the first clause boundary, and the stance is
  decided per answer inside the one existing recording loop. Negative
  evidence is NOT absent evidence: non-answers never reach the loop and keep
  costing confidence, not score. No flag auto-rejects, by import graph.
- **Reports carry `model_id` and `prompt_version` (0094).** Written only for
  a model-backed run, resolved at write time; a deterministic-fallback report
  carries NULL for both because naming a model would claim work that never
  happened, and old rows are never backfilled for the same reason.
  `prompt_version` states its own limit: the remark system prompt is inline
  in `bounded_remark` and versioned by the image, not by the registry labels
  the column carries.
- **EEO reporting and impact ratios are REFUSED, not deferred** (owner,
  2026-09-10). No jurisdiction was ever named and the customers are Indian
  entities, so a US-schema EEO surface is wrong work; impact ratios require
  collecting exactly the protected-attribute data `hiring/layers.INVARIANTS`
  refuses, and the invariant stands. Recorded in AI_RUNTIME.md as published
  positions.

## Current hard rules, the AI runtime upgrade (2026-09-09)

`ai-upgrade-spec-doc.md` (RPN-AI-UP-001) is the brief, at precedence rank 3a.
[docs/verification/AI_UPGRADE_BASELINE.md](docs/verification/AI_UPGRADE_BASELINE.md)
is the measurement everything in it is scored against, and it is a
MEASUREMENT: it records what was true on the day it was taken and is never
edited to match new behaviour.

### The brief's own audit was wrong, and that is the most useful thing in it

Section 1.1 concluded from a grep that 19,000 lines of Part A were unreachable,
and scoped a wiring workstream on it. **They were already wired.** That grep
cannot match an import statement at all and never looked past one hop. Building
W1 as written would have produced a second scoring path.

**So `pytest tests/test_ai_reachability.py` is the check, not a grep.** It walks
the import graph transitively from `app/api` and `app/workers`, follows
function-level imports, and fails in BOTH directions. It separates IMPORTABLE
from EXERCISED, and `REQUIRED_CALLERS` asserts that a function which must have
a caller still has one -- the assertion that would have caught `index_document`
sitting uncalled for its entire existence.

### Retrieval is real now, and four defects found it

`context_chunks` had never held a row in any environment.
`pickready.index_document` (Route.LAMBDA) is dispatched from a parsed resume, a
published JD and a completed assessment; `pickready.reconcile_context_index`
sweeps hourly and asks the TABLE with a NOT EXISTS, never a timestamp. Verified
in pilot: the sweep queued 1, six chunks were written and embedded, the next
sweep queued 0.

All four were invisible to a local test, and each is worth remembering as a
CLASS rather than as an instance:

- **A column name written from inference.** `assessment_conversations.link_id`
  does not exist; it is `job_candidate_link_id`.
- **Two definitions of "has text" that disagreed.** The sweep asked SQL
  `btrim(col)`, the loader asked Python `.strip()`, and `btrim` with no second
  argument strips SPACES ONLY. A JD holding two newlines was queued forever and
  no-opped forever, with a bill as the only symptom. The test now lives in SQL
  once and the loaders ASK for it.
- **A dangling tenant reference is a poison pill.** `profiles.source_tenant_id`
  is a plain nullable UUID with NO foreign key, because a profile is shared
  across tenants via the databank; `context_chunks.tenant_id` HAS one. Checking
  only for NULL meant that one document burned three attempts every hour,
  forever. Both `pending` and `load` now require the tenant to EXIST.
- **A Lambda could not invoke a Lambda.** `reconcile_context_index` is the
  first task in the product that dispatches Route.LAMBDA work from Route.LAMBDA
  work, and one function serves every short task, so it is the worker invoking
  ITSELF. Every earlier sweep dispatched to Route.ECS, a different grant. Not a
  regression: a capability the architecture had never exercised.
  `invokable_function_keys` names functions by KEY, never by ARN, because a
  `for_each` keyed on an ARN cannot be planned.

**`dispatch` RAISING rather than swallowing is the only reason any of it was
visible.** A dispatcher that degraded would have reported a queue it never
wrote to, above an index that stayed empty.

### The new hard rules, one line each

- **A degradation is RECORDED, never silent.** When the cross-encoder is
  unavailable, retrieval falls back to the deterministic lexical pass and the
  run records `reranker: "lexical", degraded: true`. Pretending a cross-encoder
  ran is the same failure as presenting template output as generation.
- **The sufficiency signal is a ranking and acquisition prior ONLY.** It may
  never lower a score, move a band, or reach the aggregator.
  `tests/test_retrieval_scoring_isolation.py` asserts the import graph the way
  the proctoring isolation test does.
- **EVERY cache key contains the tenant id.** This is the most common place
  tenant isolation silently disappears, and it disappears when somebody adds a
  cache later for a performance fix. `tests/test_cache_tenant_keying.py` greps
  every key builder and fails on one that lacks it.
- **The judge is OUTSIDE the closed model mapping, structurally.** The jury
  lives in `app/evaluation/judges/`, never `app/services/`;
  `tests/test_judge_isolation.py` asserts by AST that nothing under
  `app/services/` imports `app/evaluation/`, and that no route or worker can
  reach the judges. `MODEL_FOR_TASK` stays a closed mapping onto two ids, and
  the grep exemption for `app/evaluation/` carries its reason inside the test.
- **A judge result reports MCC, Cohen's kappa, the confusion matrix and the
  protocol, or it is not reported.** Raw agreement overstates chance-corrected
  agreement by a mean of 38.6 points. An abstaining judge produces an INTERVAL
  or `unavailable`, never 0.0.
- **UNKNOWN is a third outcome, not a rounding of failure.** A timeout on a
  side-effecting call means the request MAY have succeeded. Retrying a FAILED
  action is correct; retrying an UNKNOWN one is a duplicate side effect, and it
  is resolved by READING BACK. `agent_actions` has no UNKNOWN to RUNNING edge,
  and that absence is the enforcement.
- **An idempotency key comes from stable logical inputs**, never a timestamp
  and never a per-attempt UUID, the same shape the Razorpay path already uses.
- **An `agent_learnings` row is scoped to ONE tenant.** A learning derived from
  candidate-authored text in tenant A must not influence grading in tenant B,
  and per-tenant scoping is enforceable structurally where an approval step is
  a process somebody performs under deadline.
- **A hidden-text hit is PROVENANCE, never a rejection.** A resume carrying
  invisible instructions is signal: it is recorded as a limitation, the model
  sees the normalised text, and a human decides. Roughly 1% of real resumes
  carry an injection attempt, so this is not hypothetical.
- **Embeddings are PII at rest.** Published inversion work recovers 50 to 70%
  of input words, so an erasure that deletes rows and leaves vectors leaves the
  resume recoverable. `pickready.cascade_erasure` reaches vectors and caches.
- **No generation prompt may let the model describe its own confidence,
  sourcing or sufficiency in output text.** That is decided deterministically
  BEFORE the prompt runs (`services/generation_sufficiency`), per field, and an
  insufficient verdict SKIPS generation and returns a fixed key from
  `EMPTY_STATE_COPY` -- never freeform text. Every generation prompt carries a
  good, a fenced bad and an edge-case example. An empty state states a fact
  about the RECORD; meta-commentary states the MODEL's uncertainty.
- **AI activity status is derived from events the workflow actually reached**,
  rendered from a fixed catalogue in `services/activity`, never from a timer
  and never from an extra model call. No chain of thought, no invented count,
  and a failure terminates the line rather than leaving it spinning.
- **ONE SCORING RUN PER APPLICATION, AND THE LOCK IS ACROSS PROCESSES.**
  `pg_advisory_lock`, `FOR UPDATE` and `with_for_update` appeared NOWHERE in
  this tree before 2026-09-09: every concurrency guarantee rested on a UNIQUE
  constraint refusing the second write, or on nothing. `services/locks` is the
  one implementation. `run_functional_assessment` takes
  `pg_try_advisory_xact_lock` BEFORE the credit check and the model chain, and
  a second run RETURNS rather than waiting -- the first run is doing exactly
  what the second came to do. `uq_functional_report_link` is not a substitute:
  it fires at COMMIT, after both runs have paid for Miti's five evaluators and
  Siddhi's synthesis, and the loser's retry then finds the row EXISTS and
  rewrites a report that may already have been delivered, which is the
  immutability rule failing without a sound. `services/coalescing` is not a
  substitute either and says so itself: it is in-process, and `Route.ECS`
  gives every dispatch its own container.
- **The lock key comes from BLAKE2b, never `hash()`.** Python salts `hash()`
  per process, so two Fargate containers would compute different keys, each
  would take a lock nobody held, and both would score.
  `test_the_key_is_stable_across_processes` runs a SUBPROCESS with a different
  `PYTHONHASHSEED`, because an in-process assertion cannot see this at all.
- **`release_held_assessments` is SCHEDULED now, and scheduling it was unsafe
  until the lock existed.** It was registered and dispatched only from the two
  credit-grant call sites, so a report lost to a dispatch that never arrived or
  a container killed mid-scoring stayed lost, for a candidate who had done the
  work and a customer who had been charged. With no tenant argument the sweep
  also matches conversations that finished seconds ago and are being scored
  right now: without the lock it would have MANUFACTURED the duplicate it
  exists to repair.
- ~~**Intercom holds CUSTOMER data and never candidate data, by
  construction.**~~ **SUPERSEDED 2026-09-10, owner decision: THE INTERCOM
  INTEGRATION IS DELETED**, not finished and not disabled.
  `services/intercom.py`, its test, the `pickready.sync_intercom_companies`
  task, its schedule entry, the EventBridge rule in all three environments and
  `INTERCOM_ACCESS_TOKEN` are gone; `tests/test_intercom_removed.py` sweeps the
  tree and fails on a hit. **THE RULE IT CARRIED SURVIVES INTACT AND NOW BINDS
  `services/support`**, the native in-product replacement: a support thread is
  about the CUSTOMER, and no candidate identifier, score, grade or evaluation
  detail may ever reach `support_messages`. What changed is that the boundary
  is now a SCHEMA boundary rather than a vendor payload allowlist, which is
  strictly stronger: there is no outbound projection left to widen. The reason
  the allowlist existed is the reason the sweep in
  `tests/test_support_candidate_boundary.py` exists, and it is the same reason:
  the failure mode of every support integration ever written is that somebody
  helpfully pastes the record they were looking at into the ticket. See the
  2026-09-10 section for what replaced it.
- **A URL is an address, not a sentence about a candidate.**
  `contains_forbidden_number` masks URL-like tokens before its patterns run.
  Before that fix it read `.../assessments/d7be...` as an assessment word beside
  a number, so `lifecycle_email` rejected EVERY AI-drafted invitation and
  reminder, and both went out from the deterministic template with
  `generated_by_ai=False`, silently, on every send.

### What is NOT proven, and must not be described as if it were

- ~~**No live rerank call has ever been made.**~~ **SUPERSEDED 2026-09-09.**
  `rerank-2.5` IS real, resolved against the endpoint and then exercised
  through the shipped module, so the SDK's real response shape is what
  `_voyage_order` reads. Over four chunks whose FUSED order put an irrelevant
  retail chunk first at 0.9, the cross-encoder returned both Kafka chunks
  (0.5 and 0.4) above it, and the run recorded `reranker="voyage",
  degraded=False`; blanking the credential recorded `reranker="lexical",
  degraded=True, reason="credential_not_configured"`. Evidence in
  `VERIFICATION_RESULTS.md`. **`VOYAGE_RERANK_2_5` holds the same Voyage
  ACCOUNT key as `VOYAGE_CONTEXT_4`** -- one account serves both endpoints --
  and the two names stay separate because a credential is named after the
  model it unlocks, so an absent key names the missing capability rather than
  a vendor. **The context prefix is still unproven**: that prompt has never
  been sent to Luna.
- ~~**No Gemini credential exists**~~ ~~**W7.2 is still not measured**~~
  **SUPERSEDED 2026-09-09. W7.2 IS MEASURED, ON GROQ.** Gemini's free tier
  exhausted its daily allowance at a third of 600 calls; the Groq keys already
  in `.env` completed the run. Six arms, five cases, twenty calls each,
  `usable_share` 1.00 throughout. Worst pooled self-disagreement 0.0300
  (`gpt-oss-120b` unseeded), worst single case 0.150.
  **A SEED IS NOT DETERMINISM**: it took `gpt-oss-120b` from 0.0300 to 0.0000
  and left `gpt-oss-20b` at 0.0100, so reproducibility rests on REPEATS WITH
  REPORTED DISPERSION, never a seed alone. **All dispersion sat at band
  boundaries**; every obvious case was 20 for 20 on every model, so a probe of
  easy cases would have reported 0.0000 and calibrated the gate on the wrong
  distribution.
  This unblocked W8: `app/evaluation/release_gate.py` derives
  `NOISE_BAND = 0.03 x 3 = 0.09` from the measurement rather than guessing it,
  and **UNAVAILABLE IS NOT A PASS** -- with the human-labelled sets empty the
  gate returns `releasable=False`, because a metric that could not be computed
  must block or a broken harness releases everything while showing green.
  The jury itself is wired and proven end to end (MCC 0.627, kappa 0.556) over
  SYNTHETIC cases that live in the probe script and never in `datasets/`.
  Evidence in `VERIFICATION_RESULTS.md`.
- **The judge vendor is Groq, and that does not reopen the product mapping.**
  spec-doc5 deleted Groq as a PRODUCT vendor and that stands: `MODEL_FOR_TASK`
  is still closed onto `gpt-5.6-terra` and `gpt-5.6-luna` with no fallback
  chain. A JUDGE has the opposite requirement, because a model scoring its own
  family's output is worth roughly +10% to +25% in win rate. Two of the three
  jurors share a publisher with the product's models and that is a real
  weakness of the panel, recorded rather than glossed: `qwen/qwen3.8-27b` is
  the only fully independent leg.
- **Retrieval QUALITY is unmeasured.** ~~The golden retrieval set is 24
  hand-authored cases against a floor of 300~~ **AMENDED 2026-09-10: 60 cases
  now, version 2026.Q3.2, Q3.1 frozen.** Still 0% production sample, 0 of 60
  human verified, and the shipped run is a `reference_fixture` rather than a
  `recorded` one, so it is explicitly not gate-eligible for quality. What DOES
  gate is the harness self check.
- **The only deployed environment holds no candidate data.** Three demo
  tenants, thirty jobs, and zero candidates, profiles, applications, reports,
  evaluations and matrices. Every acceptance criterion phrased against
  production volume needs a seeded worked example rather than traffic, and that
  substitution must stay visible rather than implied.


## Historical ruling, Company DNA removed (2026-09-09)

This section records the 2026-09-09 state. Its statement that the company
weight layer had no live supplier was superseded by optional Drishti on
2026-09-19. The 2026-09-23 Tatva authority rule above governs current work.

Owner decision. The Company DNA questionnaire is GONE and the **Company
Profile** replaces it. Five modules, four frontend files, six test modules, one
YAML data file and Runbook Part IV plus Appendix A were deleted; migration 0088
drops the tables. **A client must not be able to tell it ever existed.**
`tests/test_company_dna_removed.py` sweeps `app/`, `tests/` and `scripts/` for
the name and fails on a hit, so this is enforced over the tree rather than at a
call site.

### Gate 1 now asks the Company Profile, and it is the same shape

`hiring/company_requirements.creation_blocked` still runs at the top of
`POST /jobs` and still returns a MESSAGE or None, so no caller can invent its
own wording. What changed is the table: it reads `companies.about_company`,
stripped.

- **`about_company` only.** Work Life and Benefits are fields a company may
  legitimately leave empty; refusing job creation over a section whose absence
  costs nothing downstream is a gate nobody could defend.
- **Whitespace is not content.** A profile holding three spaces would seed a
  job's About section with three spaces.
- **Still the TABLE, never a stamp**, and still at CREATE and not at publish. A
  job created before the client wrote their profile stays created.

### The three-layer framework is now TWO layers, and nothing pretends otherwise

Layer 2 was the compiled Company DNA artifact. It is gone, so a weight is
`baseline (L1) x situation (L3) x role (L3)` and `Weight` no longer carries a
`company` term. `transformation.derive_threshold(category)` takes the category
alone. `scorecard._layer2` and `_candidates_from_layer2` are DELETED, which
removes a refusal as well as a term: a tenant with no artifact could not freeze
a matrix at all, and now can.

- **`layers.py` is UNCHANGED and `LAYER_COMPANY` stays.** It is the Runbook's
  own bounds and precedence engine (3.5, 11.2, 11.4), its rows are DATA pinned
  by `test_runbook_parity`, and 11.2's bounds table is cited by
  `dimensions.yaml`. Gutting the middle layer would be a hiring-mechanic change
  this removal does not authorise. What it has today is no live supplier, and
  `test_hiring_layers` asserts the `evidence_threshold` bound stays ASYMMETRIC
  for exactly that reason: an asymmetry with no caller is the one most likely
  to be "simplified" by somebody who cannot see what it was protecting.

### Two things survived deliberately, and both would have been easy to lose

- **`services/hiring/observable.py`.** `is_observable`, `rejection_message` and
  `prohibited_in` were defined inside the instrument and are not Company DNA
  concepts: one is Runbook 18.5 rule 3's bar for a SWOT requirement, the other
  is 12.3. `swot_quality` holds a hiring manager to them and `scorecard` holds
  the MODEL that names a competency to the same bar, so the two cannot come
  apart. Moved byte-for-byte; `tests/test_observable_detector.py` is now the
  one place the behaviour is pinned, in both directions -- "Must hold a valid
  CA licence" is not a protected-attribute disqualifier.
- **`job_company_dna_bindings` became `job_scorecard_bindings`, rows intact.**
  It was never only about Company DNA: it is the append-only record of WHEN a
  job's scorecard was frozen and at what version, and
  `orchestration/versioning.resolve_for_application` reads it to answer "what
  was this job built on when I applied" for every candidate already assessed.
  Dropping it would delete that answer. Renamed with `ALTER TABLE ... RENAME`
  rather than left carrying a dead feature's name, and its two `company_dna_*`
  columns dropped BEFORE the table they referenced.

### The Runbook is v1.4, and section numbers were NOT renumbered

Part IV (15, 16, 17) and Appendix A are removed in full; 61's SOP-01 is
rewritten around the Company Profile. **15 to 17 are absent rather than
reused**, because `runbook_data/` carries 103 citations by section number and
renumbering would repoint every one of them silently. The YAML mirror lost
`company_dna_instrument.yaml` and one duplicate rule in `disqualifiers.yaml`
(18.5 already carried it, cited correctly), and every meta moved to 1.4 in the
same change -- `test_runbook_parity` compares the two directions and would have
failed either half alone.

### Two names that look removable and are not

- **`dna` stays in `provenance.CORRELATION_KINDS`** with no issuer. Traces and
  audit rows written before today carry `dna-<hex>` ids, and dropping the kind
  would make `is_correlation_id` answer False for a stored value that is
  perfectly well formed -- a reader silently deciding history is corrupt.
- **`docs/history/` and `docs/operations/TEST_BASELINE.md` still name it.**
  Both are dated provenance. They record what was true when they were written
  and are not updated to match current behaviour.


## Current hard rules, the add-features release (2026-09-06)

The 2026-09-05 add-features specification is an OWNER document and three of its
requirements deliberately reverse standing rules. Each reversal was made WITH
its pinning test in the same change, never around it. Migrations 0080 to 0085.

### Three owner reversals, and their exact width

- **The no-OTP rule is NARROWED, not gone.** Login OTP stays banned everywhere.
  A 6-digit OTP exists for exactly one purpose: proving ownership of a
  corporate SENDER mailbox (`services/email_senders/verification.py`). Hash
  only in Redis, 3 attempts, 45-second resend cooldown, TTL from settings.
  `test_platform_audit.py` exempts `email-senders-card.tsx` by name and
  nothing else.
- **"Gmail SMTP only" became transport-as-deployment-data.** `email_transport`
  selects `smtp` (default, unchanged behaviour) or `ses`. ONE transport per
  deployment, never a fallback chain, same shape as `TASK_DISPATCH_BACKEND`.
  Under smtp the authenticated mailbox stays From and the tenant sender is
  Reply-To; under ses the sender is From. The spec's SQS stage maps onto the
  existing dispatch system: one implementation per concept.
- **An assessment video is a CONSENTED artifact, and proctoring still stores
  no media.** The two must never blur: everything under `services/video/` is
  imported by no scorer (pinned beside the proctoring isolation tests), and
  nothing under `services/proctoring/` gained a media write path. A
  conversational session has NO video; the dashboard says "No video recorded"
  rather than pretending.

### Corporate senders

Only an ACTIVE sender sends, and the check runs AT SEND TIME in the delivery
task, which is what makes revoking a sender apply to emails already queued.
The lifecycle FSM lives in `services/email_senders/lifecycle.py`; authorize is
the client super admin's act and stamps who and when. The free-provider
blocklist is the `sender_domain_blocklist` setting (subdomain-aware), shared
by BGV's departmental-email validation rather than copied.

### Dual-mode assessment

`assessment_conversations.mode` defaults `conversational`, so every legacy row
keeps its truthful mode. The mode is chosen BEFORE consent and FROZEN once the
session starts. Consent is per session, versioned (consent, privacy policy,
terms), configurable wording from settings, and gates BOTH modes at the same
chokepoint as the proctoring gate. The video pipeline (Route.ECS) is
extract -> transcribe -> structure -> score -> compress -> verify -> delete
raw, each failure landing in its NAMED state and re-raised; transcription
disabled reports `transcription_failed` with an honest message, never a fake
transcript. The raw object is deleted only after the compressed object is
HEAD-verified, the project-intake pattern. Video answers land in the SAME
rows the conversational scorers read, so scoring and the PRISM report are
mode-blind; `services/assessment_canonical.py` is the one adapter.

### Video access, retention consents

Preview is a short-lived presigned URL on the compressed mp4 (S3 range serves
streaming; nothing transcodes on read). Download additionally requires the
candidate's OWN retention consent: `retention_consent.video_download_allowed`,
where NULL means never-asked and refuses, the safe direction. The same shape
gates the PRISM PDF via `assessment_download_allowed`, enforced at the route,
with `report_download_allowed` serialized so the UI hides the control instead
of rendering a dead button. Every preview and download writes an audit row.
No bucket name and no object key crosses an API boundary.

### BGV, employer pages, dashboards, signals

- BGV rows are CANDIDATE-owned. An employer tenant reads a result only through
  a `bgv_share_consents` row for that tenant; no consent reads as "Not shared
  by the candidate", never a 404 of the page. Domain match is provenance,
  never a block. A parse failure is `parse_failed`, honestly.
- The public employer page hangs off `tenants.public_slug` (a customer IS a
  tenant), reads through the same no-auth pattern as `/apply/{id}`, lists only
  in-window published jobs, and serializes a closed allowlist: no contacts, no
  billing, no applicant counts.
- The 18 intelligence dashboards read `telemetry_events` (which ALREADY
  existed, migration 0073 -- check before building on a "new" table) and the
  tenant's own history. Metric formulas and G/A/R thresholds are DATA in
  `services/intelligence_metrics.py`; an unmeasurable metric answers
  `no_data` with a reason, never 0.0. Operational numbers are allowed here;
  a candidate score never is.
- The longevity signal is an internal ordering prior: grade-preserving by
  construction (it cannot move a four-word tier) and insufficient history
  contributes exactly nothing. The status-hygiene pre-check is ADVISORY by
  locked decision; nothing may wire it into POST /jobs as a gate.

### What production sign-in taught (2026-09-06)

Google sign-in was dead on the live site for two stacked reasons, and both are
worth generalising. `www.readypick.ai` was authorized in Firebase while the
site serves at the APEX, so every popup failed `auth/unauthorized-domain`; a
"domain was added" claim needs the EXACT origin checked, and
`friendlyAuthError` now names the failing origin in the console instead of
telling the user to re-check their password. And
`readypick-pilot/FIREBASE_SERVICE_ACCOUNT_JSON` still held
`PLACEHOLDER_NOT_CONFIGURED`: a secret CONTAINER is not a configured secret,
and the 503-vs-401 answer of `/auth/firebase/session` on a bogus token is the
one-line probe that tells them apart.


## Current hard rules, background work without Celery (2026-09-05)

Celery is GONE. `app/workers/celery_app.py` is deleted, `celery` and `flower`
are out of `requirements.txt`, and the `worker`, `mail-worker` and `beat`
container roles are out of `docker-entrypoint.sh`. Redis stays, and it is no
longer a broker. **This supersedes general rule 4** and the parts of the
2026-07-28 and 2026-08-06 sections that describe queues, pools and beat.

`docs/spec/BACKGROUND_WORK.md` is the specification. `DEPLOYMENT_LOG.md` at the
repository root is the record of the AWS deployment that went with it.

### The one way to start background work

`dispatch("pickready.<task>", args=[...])`, from `app/workers/dispatch.py`. It
returns a `TaskHandle` whose `id` is generated client-side, so a request handler
has the polling id before the invoke completes -- the same contract Celery's
client-side task ids gave, so the frontend's `task_id` and `task_ids` fields are
unchanged.

- **Three backends, three real deployments, never a fallback chain.** `aws`
  invokes Lambda; `local` runs the task in a daemon thread, which is what the
  compose stack does because there is no Lambda on a laptop; `record` accepts
  the dispatch, runs nothing and remembers it, which is what the test suite does
  because executing a task would make every route test depend on a model
  provider. `record` is REFUSED IN PRODUCTION by the dispatcher itself.
- **A dispatch failure RAISES.** Several call sites already caught the enqueue
  failure and degraded visibly (a skipped outreach recipient says it could not
  be queued); a dispatcher that swallowed the error would turn those into
  silence.
- **Every boto3 client is built with explicit connect and read timeouts and a
  bounded retry count.** This is the same lesson the broker's publish timeout
  taught: an unreachable endpoint that HANGS defeats every `try/except` around
  the call, because nothing is ever raised for the handler to catch. botocore's
  default connect timeout is 60 seconds with retries stacked on top.

### The route is the cost decision, and it is DATA

`@task(name=..., route=...)` in `app/workers/registry.py` records the name, the
implementation, the destination and the retry policy in ONE declaration. Celery
kept the name on a decorator and the queue in `task_routes` on the app object,
and a task added without a routing entry fell through to the default queue.

- **`Route.LAMBDA`** is work measured in seconds: delivery, resume parsing, the
  reconciliation sweeps. It runs in `readypick-task-worker`, billed per
  invocation, with no standing capacity for a slow task to occupy.
- **`Route.ECS`** is work measured in minutes: scoring a whole candidate pool,
  compiling a Tatva matrix, writing a PRISM report. One on-demand Fargate task
  per dispatch, started by `readypick-assessment-trigger`, which stops when the
  process exits. Lambda's fifteen-minute ceiling is the hard reason for the
  split.
- **What the mail-queue split guaranteed now holds structurally.** A staff
  invitation can never wait behind an LLM chain, because the two kinds of work
  do not share a pool: neither of them has one.

### Retries live in ONE place, and the platform's own retry is set to zero

`runtime.run_task` owns the loop, and `aws_lambda_function_event_invoke_config`
sets `maximum_retry_attempts = 0`. Two mechanisms stacked would MULTIPLY: three
in-process attempts under two platform attempts is nine sends of one email.

- **The loop PREDICTS.** `delay + longest_attempt_so_far >= remaining budget`
  refuses an attempt that could not finish, using
  `context.get_remaining_time_in_millis()` as the budget. The same rule
  `agent_loop` and `llm_router` already state, applied to the outer loop.
- **There is no soft time limit any more, and that is a simplification.** The
  ceiling is the Lambda's timeout or the Fargate task's `stopTimeout`. The retry
  loop lives inside the invocation that gets killed, so a task that outran its
  budget is not retried, structurally -- which is what
  `dont_autoretry_for=(SoftTimeLimitExceeded,)` was buying.
- **A final failure is RE-RAISED**, so the function's error metric moves, the
  alarm fires and the on-failure destination publishes. Swallowing it would
  leave the platform believing every invocation succeeded.

### The schedule is data in two places, and a test compares them

`app/workers/schedule.py` is the source of truth; each environment mirrors it as
EventBridge Scheduler rules; `tests/test_schedule_parity.py` reads both and
fails on drift. The failure this prevents has happened here: a beat entry fired
`pickready.probe_llm_models` every hour for a whole release after the module it
imported was deleted. **An entry in Python with no rule in Terraform is the
silent half** -- a sweep does nothing when there is nothing to repair, so "not
running" and "nothing to do" produce the same empty log.

### Two agents are invoked SYNCHRONOUSLY, and there is no third

`readypick-jd-gen` and `readypick-company-profile` produce a draft the recruiter
is waiting for, so the route blocks exactly as long as it did; what changed is
which process spends the time. `agent_client` calls the service function
directly under `local` and `record`, so there is ONE implementation of each
agent and it lives in `app/services`.

**There is no `readypick-resume-jd-match`**, and that is deliberate. This
product's resume-to-JD matching is `pickready.run_matching`: a batch over every
candidate linked to a job, with model calls per batch and a stage-by-stage
progress display. A 256MB, 300-second function could not finish it, and there is
no single-candidate caller to give one instead, so building it would mean
inventing a caller for it.

### Redis is not a broker, which made the health probe MORE necessary

What it still carries is the rate limiter (fails open by design), the caches,
the background run-status record every polling screen reads, and the proctoring
warning counter. The counter is the one that decides it: `proctoring/gate`
answers 503 rather than silently not warning, so a task that has lost Redis
refuses every assessment turn while looking healthy from outside. `/health`
probes it, and `maxmemory-policy` stays `noeviction` for a NEW reason -- LRU
would evict a live assessment's warning counter and silently reset a
candidate's warnings to zero.

### The run-status record replaced Celery's result backend

`app/workers/status.py`, Redis, six-hour TTL. It answers the two live questions
the product actually asks: the matching run's stage list on the job page, and
the per-recipient delivery state in the outreach modal.

- **An unknown id reads as PENDING**, exactly as `AsyncResult` behaved. The
  alternative is worse: reporting "unknown" would make the job page stop polling
  a run that is about to start. The consequence is documented rather than
  hidden.
- **A FAILURE records the exception CLASS NAME and an empty payload.** A message
  can quote a row, and this payload is read by a recruiter's browser. The
  Celery endpoint read `result.info`, which is the stage payload only while the
  task is running and is the exception on failure.
- **Reading the outreach state needs BOTH halves.** `send_email` returns
  `{"status": "failed"}` for a permanent failure it deliberately did not retry,
  which is a run that succeeded and an email that did not arrive.

### The infrastructure, and the one thing that holds iam:PassRole

`infra/environments/pilot/` is the canonical composition; staging and production
were migrated to the same shape in the same change, because they still declared
`celery -A app.workers.celery_app` as a container command and the image can no
longer run it. The brief asked for a `terraform/` tree at the repository root
and this reuses `infra/` instead, because a second tree is two answers to "where
is the infrastructure" and CI reads the older one.

- **`readypick-assessment-trigger` is the only zip, and it must stay thirty
  lines and boto3.** Passing a role is a privilege-escalation primitive:
  anything that can pass a role can run code as it. The API service is reachable
  from the internet and has no business holding it; a function whose whole
  source fits on a screen can. Its grant names all three of the task
  definitions (with a revision wildcard, so a deploy does not break it), the
  cluster, and the two roles.
- **The other three functions run the BACKEND IMAGE with a different handler**,
  because they import the model router, the prompt registry and a database
  session. A second artifact carrying the same code would let an agent and the
  API disagree about what a prompt says or what a grade means.
- **The migration job is an on-demand task definition and nothing had ever
  declared one.** `scripts/run-migration.sh` runs `${cluster}-migrate` and reads
  the network from Terraform outputs; neither existed in any environment, and it
  had never been found because no environment had ever been applied.
- **A `for_each` or `count` keyed on an ARN cannot be planned.** A function
  names its secret policy by KEY, a literal, and the module looks the ARN up in
  a map whose keys are equally static. The same trade the `ecs` module already
  makes.


## Current hard rules, the end-to-end hiring workflow (2026-09-04)

`docs/spec/HIRING_WORKFLOW.md` is the specification. It is precedence rank 4,
beside the Dashboard Specification, and the two do not overlap: one governs a
table, the other governs a sequence.

### Three names that already existed, and were NOT duplicated

The workflow document names three things this product had already built. All
three kept their implementation and their name in code, because one
implementation per concept:

- **"Company Hiring Requirements" IS the Company Profile.** SUPERSEDED IN
  PART 2026-09-09: this named Company DNA until that instrument was removed,
  and the substitution it argues for is unchanged. A second free-text box
  beside the profile would immediately disagree with it about which one Sutra
  reads. What changed is WHAT Sutra reads: the Company Profile narrative as
  snapshotted onto the job, and it reaches the naming prompt as DATA under an
  explicit "not instructions" rule, because an unbounded client-authored
  string in a prompt that decides what every candidate is graded on is an
  injection surface.
- **"Executive Profile" IS the PRISM Report.** Already immutable, already with
  a fixed section order. A second consolidated document would force a choice
  about which one a recruiter is reading.
- **"Pre-Assessment Report" IS the Pre-Screen Grade / AI Score.** spec-doc6 C9
  had already settled that those two names are one artifact.

### The gates that were missing, and what enforces them now

- **Gate 1: no Company Profile, no job.** SUPERSEDED 2026-09-09, and read the
  replacement rather than the original: this said "no Company DNA, no job" and
  described a `status = complete` column on a table migration 0088 has
  DROPPED, so anybody following it goes looking for a table that is gone.
  `hiring/company_requirements.creation_blocked` still runs at the top of
  `POST /jobs` and still returns a MESSAGE or None, so no caller can invent
  its own wording. It now reads `companies.about_company`, stripped, because
  whitespace is not content: a profile holding three spaces would seed a job's
  About section with three spaces. At CREATE and not at publish, because the
  profile is what the JD generator, the SWOT session and the scorecard
  derivation all read. NOT retroactive: a job created before the client wrote
  their profile stays created.
- **Gate 2: the experience band spans at most `MAX_EXPERIENCE_SPAN_YEARS` (5).**
  In `ExperienceBandMixin`, so create, patch and JD generation all inherit it and
  a recruiter cannot create a legal band then widen it with a PATCH. **The
  ceiling is on the SPAN, never on the values** -- 15-to-20 is as valid as
  0-to-5, or the rule quietly becomes "no senior roles". Refused rather than
  clamped: clamping picks an end of the recruiter's band to discard and
  publishes a range nobody agreed to. An inverted pair is reported as inverted,
  not as a span of minus seven years.
- **Gate 5: SOURCED IS NOT APPLIED, and the enforcement is a MISSING EDGE.**
  `hiring_pipeline.SOURCED` is the first stage and its only forward edge is
  `applied`. A sourced candidate cannot be invited to an assessment or
  shortlisted because the transition does not exist -- not a flag a future
  caller has to remember. **What was wrong:** the databank upload wrote every
  resume as `applied`, which claims a person read the job, wanted it and
  submitted their notice period. Every count and funnel inherited the claim.
  The invitation (`POST /jobs/{id}/candidates/databank/invite`) moves nobody:
  being emailed is not applying. The candidate converts their own row by
  applying, and the apply path finds the sourced link rather than refusing it
  as a duplicate -- telling somebody acting on our own invitation that they
  have already applied is false and a dead end.
- **Gate 8: `POST /jobs/{id}/close`, and `closed` is a fifth posting state that
  DOMINATES the four date-derived ones.** Checked first in `posting_status`;
  checked after the window, a job closed on day 18 reads as active for twelve
  more days. A COLUMN, never a back-dated `posting_start_date` -- moving the
  start rewrites which applications were in-window and which profiles are Old,
  and both are read as history. Terminal for candidates, invisible to the team:
  `can_edit_application` deliberately takes no `closed_at`, so somebody already
  invited can finish work the client has already been charged for. **No
  reopen** (RBAC 22 asks for a controlled revision mechanism).

### The final ranking, which the product did not have

**The pre-assessment order was the permanent order.** The top sort key was a
resume-derived skills score, so a candidate who ranked first on their resume
ranked first for ever and the assessment moved nobody. `order_by_clause` is now
two stages: assessed candidates by their assessment, then everyone else by
their resume.

- **STAGE FIRST, never one blended number.** An assessment score and a resume
  similarity score are not on the same scale, so any fixed weighting is a number
  nobody can justify -- the same argument `services/rag/fusion` makes for reading
  ORDER rather than mixing a cosine distance with a `ts_rank`.
- **The stage is read from the REPORT (`rep.synthesized_at`), never from
  `l.status`.** A status is a denormalised mirror; the report is the artifact.
  Ordering on the status would put a candidate whose synthesis failed above
  candidates who have a real report.
- **The resume keys stay underneath.** They are the whole order for the
  unassessed pool and they break a tie between two identical assessment scores,
  which is common on a four-band scale.

### The Updates feed, and why it is not `email_log`

`candidate_updates` (migration 0079) is the candidate's in-portal record of
everything that has happened to them. It exists because email was the only
channel and email fails silently: a spam filter, a full inbox, a typo in an
address a recruiter uploaded, and somebody misses an assessment invitation with
neither side finding out.

- **The copy is a FIXED CATALOGUE in `services/candidate_updates.py`, not a
  prompt.** No model is called, and `test_candidate_updates.py` asserts that
  over the source. This is the surface that exists BECAUSE the email might not
  arrive, and the email already depends on a provider; a feed that failed the
  same way at the same time would protect nobody.
- **No score, no grade, no number, no em dash**, swept over the whole catalogue
  rather than checked at a call site -- a rule enforced at one call site is a
  rule the next entry breaks.
- **`email_log` is an outbound DELIVERY record, including for internal
  recipients.** Not every update has an email and not every email has an update.
  One table would force every future event to invent an email it does not send.
- **The write is at the chokepoint.** `apply_transition` writes the feed row
  beside its two status writes, so none of its six callers can forget. It still
  does NOT send the email, and that is why a third write is acceptable: an email
  needs drafting, a provider, review and a Celery enqueue; a feed row needs none
  of them.
- **`sourced` produces no update.** A resume landing in a databank is not an
  event the candidate caused.
- **`link_path` must be RELATIVE, by database CHECK.** The feed renders it as an
  href, so an absolute URL would turn the candidate's own page into somebody
  else's redirector.
- **The RLS WITH CHECK admits a tenant-scoped write**, unlike
  `candidate_projects`. A feed row is written by a RECRUITER's session on every
  stage change, so bypass-only would 403 every transition in the product.

### New Candidates

- **New means: arrived after the most recent assessment round on this job.**
  Derived, never stored, exactly like `profile_age`.
- **Before the first round, NOBODY is new.** `created_at > (MAX over empty set)`
  is NULL, which is falsy -- the behaviour falls out of the comparison rather
  than out of a special case. The complementary filter must be
  `NOT COALESCE(..., FALSE)`: `NOT NULL` is NULL and would empty the whole table
  on every job that has never invited anybody.
- **The count is over the WHOLE job, never narrowed by the page filters.** It
  answers "who is waiting outside the list you are looking at".

### Two inferences that were correct and silently became wrong

Both were fine while `hold` was the only status outside the six coarse
dashboard stages, and both broke the moment `sourced` joined it:

- **`dashboard_stage(...) is None` no longer means "on hold".** Use
  `hiring_pipeline.is_on_hold`. Reading the absence of a stage as a pause labels
  a resume nobody has contacted as an application somebody deliberately paused.
- **`stage.value` on a None stage is an AttributeError, not a wrong label.** Both
  dashboard call sites now read `hiring_pipeline.dashboard_stage_label`.

`PipelineStatus` in `models/enums.py` gained `sourced` for the reason its own
docstring already records: an enum missing a value the column accepts 500s
every read of every row that carries it, permanently, for that whole tenant.

## Current hard rules, proctoring and question formats (2026-09-02)

Two specifications, `docs/spec/PROCTORING.md` and
`docs/spec/ASSESSMENT_QUESTION_FORMATS.md`, built together because the
behavioural capture of one attaches to the answer fields of the other.

- **Proctoring is MANDATORY and there is no flag.** `services/proctoring/gate.require_active`
  runs FIRST in `start_conversation` and `respond`. A candidate who declines
  the consent screen does not take the assessment. The old optional
  screen-capture consent component is deleted, not flagged off: it captured
  the screen and was optional, which contradicts both P1 and P4.
- ~~**No frame, image, snapshot or audio buffer is ever stored, anywhere.**~~
  **SUPERSEDED 2026-09-22 by owner ruling: assessment media IS stored,
  compressed, in S3, linked to the assessment. `services/proctoring/` still
  persists none; media lives in `services/video/` on `video_recordings` alone,
  and `tests/test_proctoring_no_media.py` was rewritten to guard that boundary
  rather than deleted. See claude.md, the 2026-09-22 section.** The original
  rule, kept because it explains the shape that survived:
  Inference happens in a Web Worker in the candidate's browser, which posts
  detections only. The one medium that leaves the browser is a 15-second audio
  chunk, read into memory, handed to the analysis service, and deleted.
  `tests/test_proctoring_no_media.py` fails the build on a write path. A face
  descriptor is a 128-float vector, not an image, and the database CHECK
  refuses any other width.
- **Proctoring touches NO score, grade, ranking or matrix.** Nothing under
  `services/proctoring/` is imported by a scorer, by Miti, by Siddhi, by the
  dashboard or by ranking, and `tests/test_proctoring_scoring_isolation.py`
  asserts the import graph. The evidence tier of the assessment conversation
  stays E3: monitoring an assessment must not silently promote its evidence,
  because that would let proctoring move a grade.
- **The server decides.** The warning counter is one shared Redis counter per
  session, across EVERY Path B event type, mirrored to the row. The client
  requests; the server debounces, applies cooldowns, counts and terminates.
  Redis being down answers 503 rather than silently not warning.
- **Every threshold is a `proctoring_*` setting**, read once by
  `services/proctoring/config.py` and served to the browser on the session
  response, so the client and the server never disagree about a number. No
  literal in the pipeline.
- **The report is WORDS ONLY** so it travels inside the delivered PRISM payload
  under the number ban: counts spelled out, durations approximate, no
  internal identifier, no forbidden word (strike, tier, violation, flag,
  anomaly, signal, confidence, threshold, severity). Ordering carries the
  weight; there are no icons and no colour codes. It is the EIGHTH and last
  PRISM section, `proctoring`, written once per renderer as before.
- **The third warning consults `jobs.proctoring_warning_policy`** and the
  default is `continue_and_note`. The product never terminates by default.
- **Retention follows the platform's cascade policy.** There is no time-based
  purge in the product and `proctoring_event_retention_days` defaults to 0,
  which means exactly that; a positive value enables the hourly purge. The
  number is an owner decision, not something this code invents.
- **Be honest about the limits, in the code.** Browser-level blocking stops an
  ordinary candidate, not a determined one; a monitoring gap is reported as a
  gap; unconfigured audio analysis is reported as unavailable; the AI-text
  detector ships DISABLED and informational because it is unreliable.
- **Evidence-based questions are the majority of weight AND time, in code.**
  `services/assessment_formats/composition.py` validates every generated
  assessment against six rules and regenerates, then falls back
  deterministically to evidence questions, so what is served is always valid.
  A 70% MCQ assessment cannot be served. `CandidateQuestion.weight` makes
  the dominance structural inside the item score.
- **`candidate_questions` IS the specification's `assessment_questions`.**
  Migration 0076 added the format columns to the existing per-candidate row
  rather than a second table; rows from before read as `short_answer`,
  because relabelling them evidence-based would claim an anchor they do not
  have. `assessment_answers` is the structured answer record; the transcript
  is unchanged and still what every scorer reads.
- **The answer key never crosses the candidate boundary unprojected.**
  `assessment_formats.types.candidate_view` is an allowlist per type.
- **Structured formats are delivered verbatim and scored deterministically;
  text formats keep the whole conversational machinery.** Multi-answer MCQ
  partial credit floors at zero so "select everything" scores zero;
  fill-in-the-blank escalates an exact-match miss to an AI equivalence check
  before marking it wrong; coding is judged by READING and every evaluation
  and every recruiter view says the code was not executed.
- **Per-question time is measured by the server** from
  `assessment_conversations.prompt_shown_at`, less the bounded time a
  blocking warning held the screen. A client-reported duration is a number
  the client chose.
- **The analysis service is a separate ECS service** with its own image, its
  own task role and exactly one secret, `HUGGINGFACE_TOKEN`. The pyannote
  models are gated on Hugging Face: their download needs an accepted licence
  and the token, which this repository never contains.

## Current hard rules, documentation layout (2026-09-01)

- **ALL documentation lives under `docs/`, indexed by
  [docs/README.md](docs/README.md).** Five markdown files stay at the
  repository root and only these five: `README.md`, `AGENTS.md`,
  `CONTRIBUTING.md`, and `PRODUCT.md` + `DESIGN.md` (the Impeccable tooling
  resolves those two at the project root, so moving them breaks the design
  agents). `.impeccable-exceptions.md` also stays, because
  `frontend/scripts/impeccable-gate.mjs` reads it there.
- **`docs/history/` is provenance, not truth.** Phase logs, gap matrices,
  contradiction surveys and diagnostics live there. Do not read one as a
  description of how the product works today, and do not update one to match
  current behaviour: they record what was true when they were written.
- **Five documents are resolved on disk by code**, so their paths are part of
  the contract: the Runbook (`docs/product/`), `docs/operations/SKIPS.md`,
  both `docs/verification/VERIFICATION_*.md`, and
  `docs/history/LEGACY_RESET_SURVEY.md`. `docs/README.md` carries the table of
  which module reads which.
- **The public site is user-visible copy and follows the naming rule**: Tatva
  Assessment is the process, PRISM Report is the document, and neither is
  called PPI. The `ppi` identifiers in CODE stay exactly as they are.

## Current hard rules, Project Evidence Intelligence (2026-09-01)

- **The product stores intelligence derived from projects, never the projects
  themselves.** Candidate project uploads are staged TEMPORARILY under the
  `project-intake/` object prefix and deleted, with a HEAD check confirming
  each deletion, only AFTER the derived evidence is validated and persisted on
  `candidate_projects` (migration 0074). There is no original-project archive,
  no download route for an original, and no fallback archive when deletion
  fails; a failed deletion is counted on the row and retried hourly by
  `pickready.reconcile_project_intake`. Do not add any of those. Optional
  original retention is a documented FUTURE capability only
  (`docs/spec/PROJECT_EVIDENCE_INTELLIGENCE.md`).
- **Candidate project submissions are hostile input and are never executed.**
  No installs, no builds, no shells, ever, in any parser. Archives are
  inspected against their declared directory BEFORE extraction
  (`services/projects/archive_safety.py`): a traversal entry, symlink, or
  implausible compression ratio poisons the whole archive as
  `failed_security`. Every ceiling is a `project_*` setting, never a literal
  in the pipeline.
- **Projects are OPTIONAL and absence is never penalised.** No fixed
  "no project = minus N" rule anywhere; a candidate with no projects is a
  normal state everywhere it renders. Presence is not quality either: the
  reasoning prompt and `ai_reasoning.validate_interpretation` both enforce
  that strength reflects evidence quality, not file count.
- **Four layers never blur: candidate claims (verbatim), deterministic
  extraction, derived evidence, AI interpretation.** The interpretation lives
  in its own column (`ai_interpretation_json`) and is never merged into
  `evidence_json`; a model inference must never read as extracted fact. Claim
  assessments come from a fixed careful-language vocabulary and
  `evidence_strength` is a WORD (Strong / Moderate / Limited / Insufficient),
  pinned by the prompt, a deterministic validator, and a database CHECK. The
  no-numbers rule applies in full.
- **Deterministic extraction first, ONE reasoning call second.** The parser
  router (`services/projects/parsers.py`) is total: corrupt or unsupported
  files become recorded limitations, never exceptions and never hallucinated
  contents. The model receives only the reduced pack (capped by
  `project_max_ai_context_chars`); raw files never reach a prompt. The task
  type is `project_evidence`, Terra, temperature 0.0. An interpretation
  failure is `partially_processed`, a real partial success: deterministic
  evidence persists, the AI-only completion path reruns later WITHOUT the
  originals (which are correctly gone by then).
- **Public repositories only.** No private-repo OAuth, no token intake from
  candidates, credentials embedded in a URL are refused at validation.
  `GITHUB_API_TOKEN` exists solely for rate-limit headroom. Providers are a
  host-keyed registry in `services/projects/repository.py`; the tree is
  classified before any content is fetched and generated/dependency paths
  never spend the fetch budget.
- **"Versioned evidence" means DECOMPOSED dimensions, not V1/V2 history.** Do
  not build a project-history versioning system against this feature.
- **Consumption points**: candidate Projects card on My Profile
  (`/portal/me/projects`), recruiter view behind `view_review_screen` with a
  link-in-tenant 404 gate (`GET /candidates/{id}/project-evidence`), and the
  AI context block joined into per-candidate PPI question generation
  (`services/projects/context.py`). It moves no weight, no grade and no PRISM
  section; the report's fixed section order is untouched, deliberately.

## Current hard rules, spec-doc6 (2026-08-29)

Runbook reconciliation, Part A activation, legacy reset, dashboard and RBAC,
codebase hardening, AWS close-out. This section supersedes spec-doc5's open
items 9 to 20 and its seven-module Terraform list.

### THE THREE MISSING DOCUMENTS ALL EXIST NOW. Read them, do not re-derive them.

- **`docs/product/Readypick Hiring Philosophy.md`** (RPN-PHIL-001, now **v1.4**
  -- this line read v1.1 and was stale for two releases).
  It sat at the repository root until the 2026-09-01 documentation
  consolidation. Note the filename uses SPACES; every document writes it with
  underscores. TWO call sites resolve this path on disk (the third,
  `dna_compilation.RUNBOOK_MARKDOWN`, went with Company DNA on 2026-09-09), so
  moving it again means changing them: `tests/test_runbook_parity.RUNBOOK_GLOB`
  and `tests/test_runbook_reconciliation.RUNBOOK_PATH`. It was absent for the whole
  of spec-doc5, which is why nine sites carried guesses. It is authoritative
  for evaluation mechanics.
- **`docs/spec/RBAC_SPECIFICATION.md`** is **precedence rank 1**, above the
  Runbook and above spec-doc6 itself, for authorization, tenant isolation, role
  ownership, job lifecycle and audit.
- **`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md`** is rank 4 and governs the
  candidate list surface only.
- `docs/spec/ARCHITECTURE_DIRECTION_2026-08-28.md` is ADVISORY, below everything
  in the precedence table. Useful for intent, never a requirement.

### The precedence order, used to resolve every conflict

1. RBAC Specification. 2. The Runbook. 3. spec-doc6. 4. Dashboard
Specification. 5. specdoc4, then spec-doc5.

**"Restrict more when unsure" applies ONLY where the higher authority is
SILENT.** It never licenses overriding an affirmative grant in a higher-ranked
document. This was got wrong once already: "the Hiring Manager cannot reject a
JD" (RBAC §11, correct) was over-generalised into deleting the Reject JD
capability entirely, which RBAC §24 affirmatively grants to Super Admin and HR
Manager.

**Do not trust a document's claim about another document.** Seven of
twenty-five spec-doc6 citations do not say what spec-doc6 says they say. The
worst grants HR Manager publish authority that RBAC §9.6 gives only to Super
Admin, resting on a §24 footnote that disclaims itself.

### EVERY ONE OF THE NINE RUNBOOK ASSUMPTIONS WAS WRONG

0 confirmed, 8 corrected, 1 corrected-in-part. This is the single most useful
fact about the previous phase's output. `department_models.py` modelled **5
departments where the Runbook has 15**, so civil engineers, designers,
architects, HR and skilled trades were all graded against a generic model.
`triangulation.py` had **none** of §13.2's seven named benign explanations while
AGENTS.md called two-before-escalation a hard rule. `situations.py` had 4 of 6
rows wrong, two inverted. Nothing reached a user only because none of those
modules is reachable, which is not a mitigation to rely on twice.

Zero `ASSUMPTION (RUNBOOK-GAP` markers remain. Twelve `RUNBOOK-AMBIGUITY (§N)`
markers replace them, each with an entry in `RUNBOOK_OPEN_QUESTIONS.md`.

### The Runbook's mechanical content is DATA, and a parity test keeps it honest

`backend/app/services/hiring/runbook_data/` holds nine YAML files carrying
**2,453 values under 103 citations naming 119 Runbook sections**, plus a typed
loader (`runbook_data.load(name)` and nine named accessors). spec-doc6 §2.2
writes the path as `app/hiring/runbook_data/`; this repo's layout puts it under
`app/services/`.

**Every weight, threshold, cap and boundary comes from there with a source
citation, never from a literal in a module.** `backend/tests/test_runbook_parity.py`
parses the Runbook itself and checks 300 numbers and 1,042 verbatim strings
against the exact section each cites. It is mutation-tested in both directions,
7 of 7 caught: it fails if someone edits a weight in code without editing the
Runbook, and fails if someone edits the Runbook without updating the data.
`PyYAML` is pinned EXPLICITLY in `requirements.txt` rather than relied on
transitively through langchain-core, because the hiring layer fails at import
without it.

### Four things spec-doc6 assumes the Runbook says, which it does not

- **"The Must-have hard cap" is not a phrase or a rule in the Runbook.** Three
  separate band-capping mechanisms are (§12.1 competency threshold, §12.2
  dimension floors, §14.1 unassessed Must-have). The product implements ONE.
  Its behaviour is correct and only the name and citation are invented, but
  **§14.1 catches a case a score-based cap structurally cannot**: §10.2's
  competency score puts evidence strength in both numerator and denominator, so
  for a single-claim competency the terms cancel and the score equals the rubric
  level exactly, at every evidence tier from E0 to E5. A fabricated Must-have
  resting on one weakest-tier resume bullet scores high, grades Matching, and
  never trips a score-based cap. That is the AI-generated-resume case the
  product exists to defeat, and it passes today. The Runbook's ceiling is also
  71 against the product's 74.
- **Per-seniority rubric anchors exist for one department of fifteen** (§21.11).
  Anchors are universal, stated once per dimension in §9.1 to §9.5.
- **Scale-up and Succession have no numeric weight consequence anywhere.**
  §18.4 gives arrows with no magnitude; §11.3 bounds four of six types.
  `situations.py` raises `RunbookDataUnavailable` naming the missing key rather
  than inventing a multiplier. **This blocks Part A scoring and needs an owner
  decision, not more searching.**
- **"Weakly" is a third value inside an integer independence count**, with no
  defined arithmetic, and it is the most common evidence pair in the product.

### A repaired Runbook defect worth remembering

Decision Contract C5 cited **§12.4, the PROHIBITED disqualifier list, where it
meant §12.3, the legitimate one**. Read literally it authorised automatic
filtering on age, caste, gender and employment gaps. One character of citation,
that consequence. Now pinned by a test asserting C5 cites §12.3 and not §12.4.
The general lesson: extend parity checking from VALUE parity to CITATION-TARGET
parity, because a citation annotating a permitted list must fail loudly if it
resolves to a section headed "prohibited".

### Part A IS on the live path now (2026-08-29). It was not, and that is the history

`grep -rn "hiring\.\|miti\.\|siddhi\." backend/app/api backend/app/workers`
returned **nothing** for the whole of spec-doc5. The only non-test importer of
the entire Part A stack was `app/scripts/worked_example.py`. G1 to G4 were real
checks guarding nothing: G1's only caller was `miti/pipeline.py:290`, which no
route or worker imported. **spec-doc6 D2's "gate G1 already blocks
evaluation... Use it" was therefore false**, and anything written against it was
relying on nothing.

Part A IS live now. Job setup runs Bodha's SWOT and Sutra's seven stages and
freezes a matrix behind G1; Yukti grades a resume on the evidence model and the
ontology; Miti's five isolated evaluators score live with a model-free
aggregator; Siddhi composes the PRISM report through a citation chokepoint with
no bypass parameter. The old single-pass generators are DELETED, not flagged
off.

~~**Keep that grep as the check.**~~ **SUPERSEDED 2026-09-09, and the
supersession is the interesting part.** This paragraph used to read "that grep
now returns hits in `api/assessments.py`, `api/jobs.py`, `api/dashboard.py`,
`api/company_dna.py` and `workers/tasks.py`". **Run it today and it returns two
hits, both comments in `workers/tasks.py`** -- and Part A is live anyway. The
sentence was wrong about the METHOD while being right about the SUBSTANCE, and
both halves of that matter:

- **The grep never could have worked.** `hiring\.` does not match `from
  app.services.hiring import scorecard`, because there is no dot after the
  package name. It matches attribute access like `hiring.scorecard.freeze`,
  which is what a COMMENT tends to contain and what an import statement does
  not. The check this file called "the cheapest honest answer" was measuring
  prose.
- **And it only ever looked one hop deep.** Miti and Siddhi are reached at
  depth two, through `services/functional_assessment`, and `from
  app.services.miti import live` is written INSIDE `synthesis_node` on purpose,
  to break a real import cycle. Nothing that reads module-level imports in two
  directories can see either fact.

RPN-AI-UP-001 section 1.1 ran exactly this grep, got the two comment hits, and
concluded that 19,000 lines of Part A were unreachable and needed wiring. They
were already wired. **A bad detector does not fail safe: it manufactured a
phantom workstream, and the next thing built on top of it would have been a
second scoring path.**

**The check is now `backend/tests/test_ai_reachability.py`**, which walks the
real import graph transitively from every module under `app/api` and
`app/workers`, follows function-level imports, and fails in BOTH directions --
when a package that is supposed to be live loses its last route, and when a
package recorded as dead quietly acquires one. It also separates *importable*
from *exercised*, because `services/rag` is importable from a route and has
never run: `index_document` has no caller, and `context_chunks` held zero rows
in pilot on 2026-09-09. The evidence is in
[docs/verification/AI_UPGRADE_BASELINE.md](docs/verification/AI_UPGRADE_BASELINE.md).

### A test-isolation trap that hid nineteen failures

The Miti live harness installed a fake scorecard with
`monkeypatch.setitem(sys.modules, "app.services.hiring.scorecard", fake)`. That
reads correctly and is wrong: **`from package import submodule` resolves the
PACKAGE ATTRIBUTE once anything has imported the real module**, and after that
the `sys.modules` entry is never consulted again. So the fake worked when the
file ran alone and silently stopped working the moment any earlier test touched
the real module. Nineteen tests passed in isolation and failed in the suite.

Set BOTH bindings when faking a submodule, and when testing the missing-module
case remove both, because a genuinely absent module has neither.

### Normalisation makes a stored weight scale-invariant, and a test must know that

`scorecard._rank_and_normalise` divides the scored items by their total so the
matrix sums to 1.0, which is what Runbook §20.1's own scorecard table does. The
consequence: when a situation type or a company philosophy lifts every scored
item alike, because they sit on dimensions it treats alike, **normalisation
divides the lift straight back out and the stored shares match to fifteen
decimal places.** Nothing is broken; the quantity cannot show it.

The effect lands in the four-term product `baseline x company x situation x
role`, kept in `provenance["raw_value"]` precisely so it stays observable.
spec-doc6 §4.3's acceptance evidence is about THAT number. A test asserting a
layer moved a weight must read `raw_value`, not the stored share.

### One scale, and it had silently become three

`services/tiers.py` never got converted in the 2026-07-30 consolidation. It kept
90/70/50 with **`matching` and `moderately_matching` SWAPPED** relative to
`rating.py`'s 90/75/60, and it is live at `matching.py` and serialised to
clients. Measured: **747 of 1075 scored rows, 69.5%, across 34 jobs carried a
label the correct scale disagrees with**, 275 of them two bands out. A weaker
candidate read as better than a stronger one.

`tiers.assign_tier` is now a thin alias over `rating.grade_for_percent` with no
arithmetic left in the module, pinned by a full-range sweep and by a test
asserting a better score never earns a worse grade. **`tier` must stay NULLed in
the same legacy-reset rule as `match_score`**; splitting that pairing makes the
misclassification permanent and unrecoverable.

Note the Dashboard adds a FIFTH vocabulary (85/72/60 five-band). Three grade
vocabularies and four cut-point sets now exist across the documents.

### RBAC facts that are easy to get catastrophically wrong

- **RBAC "Super Admin" is `Role.client`, tenant-scoped** (§5 "Client Super
  Admin", §7.1 "per client organization"), NEVER the platform `Role.super_admin`
  whose `tenant_id` is NULL. Mapping it wrong is a privilege escalation that
  looks correct in a diff.
- **Uniqueness is PER-TENANT.** A global "one active Super Admin" constraint
  passes every single-tenant test and then rejects the second customer ever
  onboarded.
- **§7.1 requires a Super Admin transfer mechanism and nothing implements one.**
  A hard uniqueness constraint without it locks a client out of their own tenant
  permanently.
- **There is no job assignment table.** `jobs` has one user reference,
  `created_by`, nullable and `ON DELETE SET NULL`. "Own assigned jobs" scoping
  (§9.2, §23) and two of the four §39 cardinality invariants are not expressible
  without one. `created_by` is not a substitute: it records who created the row,
  not who is assigned, and it evaporates on user deletion.
- **RBAC §5 says "four internal role categories" and lists five.** Five is
  correct, confirmed three independent ways.
- **The §24 capabilities shipped in code with NO seeding migration, for a
  whole phase (repaired 2026-09-01, migration 0075).** The grant engine reads
  ROWS, and a migrations-only database had zero global rows for all fifteen
  §24 capabilities and zero rows for the entire interview_manager role, so
  every dashboard control answered 403 for every role. It stayed invisible
  because `tests/test_seed.py` runs `seed_dev_data` (which reconciles the
  full code matrix) against the shared test database AFTER the dashboard
  files, so the first run on a fresh database failed 40 tests and every rerun
  passed, which read as flakiness. The rule stands: a capability constant is
  HALF a change; the seeding migration is the other half, and
  `tests/test_capability_seed_parity.py` now fails a fresh database that is
  missing one.
- **Cross-tenant reads return 404, never 403.** The rule is right; its
  provenance is not §33, which never mentions a status code.
- **§17's job lifecycle has EIGHT states**, not the six spec-doc6's ellipsis
  shows. `JobLifecycleState` and `CandidatePipelineStage` are different enums on
  different entities and are never interchanged. `hold` is an action, not a
  stage.

### Anti-slop rules, CI-enforced

No silent fallbacks (no `except Exception: pass`, no bare `except`, no default
substituted for a failed retrieval, no template output when generation fails).
No dual code paths for one product behaviour. No placeholder prose (`TODO`,
`FIXME`, `XXX`, "in a real implementation", "for now", "this is a simplified",
"stub" outside test doubles). No dead code, delete rather than deprecate. No
magic numbers, every one comes from `runbook_data/` with a citation. No new
model strings beyond `gpt-5.6-terra`, `gpt-5.6-luna` and
`voyage-4`. No wildcard IAM.
No em dashes in generated product copy. Docstrings state provenance. **A test
whose only assertion is that a mock was called is not a test.** No commented-out
code. **One implementation per concept**, which is the rule `tiers.py` broke.

### Verification honesty

**Keys arrived on 2026-08-31 and every vendor path is now PROVEN by a real
request.** `bash -c 'set -a; . .env; set +a; python scripts/verify_live.py'`
returns reasoning PASS, extraction PASS, embedding PASS, credential-failure PASS
and timeout PASS. `VERIFICATION_RESULTS.md` carries the run. This supersedes the
rule that stood for the whole of spec-doc6, which was that no wording anywhere
may imply a live call had succeeded.

**What the run found is the reason the rule existed.** Three things had been
carried as settled for an entire phase and all three were wrong:

- **`voyage-context-4` is not a real model.** Voyage returns 400 naming the
  supported list, which does not contain it. It had been enshrined in this file
  as a hard rule, cited in nine modules and pinned by tests. It never failed
  because `embeddings.embed` returns pseudo-random unit vectors of the right
  width when the key is absent, with no exception and no log line, and there was
  never a key. **The model is now `voyage-4`**, verified at 1024 dimensions,
  which is what the schema already expects, so no migration was needed.
- **`max_tokens` is refused outright** by both models: 400 `unsupported_parameter`,
  use `max_completion_tokens`.
- **`temperature` 0.0 is refused outright.** Only the default of 1 is accepted.

**That last one cost this product a stated guarantee, and it is not recoverable.**
The standing rule was that every task which JUDGES samples at 0.0, because a
scoring call above zero makes a candidate's grade depend on WHEN they were
scored. These models cannot do it. `seed` is sent instead and measured
byte-identical over three runs, but the vendor documents it as BEST EFFORT and
`system_fingerprint` came back null, so a backend change underneath is not
observable. What still holds is the part that matters most: the AGGREGATOR makes
zero model calls and is deterministic arithmetic, so the step that turns five
bands into a delivered grade cannot vary. What can vary is the band one
evaluator returns for identical evidence.

**Re-run `verify_live.py` after any change to the transport, the model ids or
the credentials.** A passing result is a statement about the code that produced
it and nothing more. `VERIFICATION_PENDING.md` still lists what remains unproven:
the 429 path has never been provoked, so the rate-limit classifier and the
retry-after reader are still only proven against recorded fixtures.

Likewise: no `terraform apply`. Running it against a real account this phase is
a failure of scope, not an accomplishment. An offline `terraform plan` proves
the configuration is internally consistent and that the graph resolves. It does
NOT prove the account can create the resources, that quotas suffice, or that IAM
will behave.

### Vendored design tools are gitignored and pinned, not committed

302 third-party skill files were untracked AND unignored, so a single
`git add -A` would have committed all of them. They are now ignored;
`tools/design-tools.manifest.json` and `tools/install-design-tools.sh` reproduce
the environment. **`impeccable` is 296 of those files, is unpinned by SHA or
hash, and `frontend/scripts/impeccable-gate.mjs` gates CI on it.** Its installed
tree declares v4.1.1 while npm publishes 3.6.0 under that name. Open for the
owner.

### Naming

`picready.com`, missing the `k`, was the documented domain in five code sites
and asserted by eight test lines. RBAC §15 settles it: `readypick.ai`.
**`pickready` spelled correctly is DELIBERATE** in Celery task names, cache key
prefixes and GCP/JWT identifiers; do not "fix" those. Still open, because
whether the mailboxes exist is an operational fact: `config.py:93` defaults
`smtp_from_email` to `noreply@pickready.app` (a runtime default, the higher
risk) and a live `mailto:hello@pickready.app` sits in the billing page.

`RBAC §15`'s `public_job_id` **does not exist in this codebase**; the public URL
is `/apply/{jobs.id}`, the raw internal primary key. Adding the identifier is a
column, a backfill and a lookup path, not a rename.

---

## Current hard rules, spec-doc5 (2026-08-28)

Four parts: the three-layer hiring intelligence framework, single-vendor model
consolidation, the navy/teal UI, and an AWS-ready codebase. Sequenced B, A, C, D
because every agent needs a stable model layer under it before its internals are
worth deepening.

### The Runbook this was written against does not exist

- **`Readypick_Hiring_Philosophy.md` (RPN-PHIL-001) IS NOT IN THIS REPOSITORY
  OR ANYWHERE ON THE MACHINE.** spec-doc5 §0 names it as authoritative for
  anything it specifies more precisely than specdoc4 and then cites it thirty
  times by section number. It was searched for exhaustively; the two `.docx`
  files that exist are copies of the specdoc4 baseline. Part A was therefore
  built from spec-doc5's own inline restatements, which are mechanically
  complete for the five dimensions, six situation types, seven pipeline stages,
  four gates and five core objects.
  Every place the Runbook would have supplied a detail spec-doc5 does not state
  carries an `ASSUMPTION (RUNBOOK-GAP, §N)` comment naming the section it stands
  in for. **Grep for that string before treating any of it as settled**, and
  diff the real document against it rather than assuming agreement. `GAP_MATRIX.md`
  §0 records the search.

### PART B, one vendor, three endpoints

**THE MODEL VENDOR CHANGED ON 2026-08-31, FROM ANTHROPIC TO OPENAI.** Owner
decision, and a deliberate reversal of the rule this section used to state. The
three places that forbade it -- this section, `test_llm_task_routing.py`'s
closure test, and `.env.example` -- were all changed with the code rather than
left contradicting it. **What was reversed is the VENDOR, not the discipline.**
A prior phase deleted Groq, Gemini, OpenRouter and a 1371-line capacity registry
to reach one vendor; none of that comes back, there is no fallback chain, and
`Codex-*` is now forbidden in executable source where it previously had two
exemptions. Anthropic is REMOVED, not kept as a fallback, and
`ANTHROPIC_API_KEY` is deleted rather than deprecated.

- **Every model call resolves to `gpt-5.6-terra` or `gpt-5.6-luna`, and every
  embedding to `voyage-4`.** `MODEL_FOR_TASK` in
  `config/llm_providers.py` is a closed mapping onto exactly two ids and
  `tests/test_llm_task_routing.py` greps the executable source for any other
  model string. No third model on implementation judgment.
- **ALL THREE IDS ARE VERIFIED LIVE (2026-08-31).** `gpt-5.6-terra` and
  `gpt-5.6-luna` both answered; the embedding id did NOT, and that is the
  finding: `voyage-context-4` does not exist and never did. It is `voyage-4`
  now, at the 1024 dimensions the schema already expects. Re-run
  `scripts/verify_live.py` after any change to the transport, the ids or the
  credentials.
- **The split is JUDGE-or-WRITE versus EXTRACT-or-CLASSIFY, and NO TASK MOVED
  TIER in the vendor change.** Every task on Sonnet went to Terra and every task
  on Haiku went to Luna, one for one. `claim_extraction` is Luna and MUST NOT
  EVALUATE: an opinion formed there enters the pipeline before the dimension
  evaluators, without a rubric, without their isolation and without a citation,
  and downstream it is indistinguishable from a finding. Putting Terra on it
  would be a boundary violation, not an upgrade. A vendor swap is exactly the
  change during which a task quietly moves a tier because both ids were being
  retyped anyway, so `SPEC_B3_ASSIGNMENT` in `test_llm_task_routing.py` states
  the assignment a second time, independently.
- **TWO CREDENTIALS FOR ONE VENDOR, one per model.** `OPENAI_GPT_TERRA` and
  `OPENAI_GPT_LUNA`; the embedding key is `VOYAGE_CONTEXT_4`, renamed from
  `VOYAGE_API_KEY` so every credential is named after the model it unlocks.
  Which model uses which is DATA in `SETTINGS_ATTR_FOR_MODEL`, never a branch.
  `llm_router.key_for_model` RAISES when the key for the called model is absent
  and never falls back to the other one: that would run a judging call on the
  extraction credential and leave nothing in the record saying so. The breaker
  is keyed by credential fingerprint, so the two trip independently.
- **Groq, Gemini and OpenRouter are GONE, not disabled.** `llm_capacity.py`
  (1371 lines: the capacity registry, `route_score`, quota-domain discovery) and
  `scripts/probe_llm_models.py` are deleted. So is the 21-key roster. What that
  machinery existed for is worth remembering rather than mourning: it routed
  around three FREE tiers' failure modes -- a retired model id that took a tier
  dark twice, an exhausted prepaid balance, an 8000-token-per-minute
  organisation pool that 413'd every realistic extraction, a model withdrawn
  from the free tier outright. One paid vendor removes the class of problem.
- **The reliability discipline survives and the vendor quirks do not.** Retries,
  exponential backoff, per-attempt timeout, total wall-clock budget, circuit
  breaker with half-open recovery: all kept. Failure classification is now
  401/403 credential, 429 rate limit, 5xx provider, timeout. Every branch that
  existed for one vendor's quirk is gone.
- **A credential failure trips the breaker on the FIRST occurrence**, unlike a
  429 or a 5xx. No amount of waiting fixes a revoked key, and the caller's
  deterministic fallback should start one attempt sooner rather than three.
- **The router deadline PREDICTS.** `elapsed + longest_attempt_so_far >=
  deadline`, so an attempt that cannot finish inside the budget is never
  started. Same rule `agent_loop` already follows, and for the same reason.
- **THE INTERACTIVE CAP IS NOW TWO TIERS, and this amends the flat 15s rule.**
  Short-output interactive tasks keep 15s/30s. `jd_generation` is 25s/50s,
  because a multi-thousand-token JD cannot finish in 15 seconds on a
  reasoning-tier model and holding the cap would not make the button faster --
  it would make every generation time out and fall back to the template,
  permanently. That is the argument the brief already accepts for
  `report_synthesis`, one tier down.
  `test_platform_audit.py` encodes both tiers and asserts the exception list
  stays short, so it is a reviewed rule rather than a drifted number.
- **JSON MODE IS NATIVE `response_format`, AND THE PREFILL IS DELETED.** This
  supersedes the rule that read "JSON mode is a PREFILL, not an instruction".
  The Messages API had no `response_format`, so `llm_router` seeded the
  assistant turn with `{` and prepended it back; Chat Completions has
  `response_format: {"type": "json_object"}`, which is a STRONGER guarantee of
  the same property, so the prefill branch, the re-prepend and the constant
  carrying the brace are gone rather than kept beside it. Two mechanisms for one
  behaviour is the rule `tiers.py` broke.
  **The invariant the prefill protected is unchanged and is pinned in three
  places**: every JSON-mode caller in this codebase parses a top-level OBJECT.
  `test_llm_router.py` still greps the services for a caller scanning for a
  leading `[`; `build_payload` is asserted to send the format; and
  `vendor_contract.check_openai_response` REFUSES a JSON-mode response whose
  text does not open with `{`, because `response_format` permits any JSON value
  and an array would `json.loads` perfectly and then fail on the first subscript.
  **The system instruction survives and is now load bearing for a second
  reason**: the published API rejects the format with a 400 unless the token
  "json" appears in the messages, so `_JSON_SYSTEM_SUFFIX` is what makes the
  request acceptable at all. `describe_request_hazards` names that constraint on
  any 400, because a 400 is classified as our bug, is correctly not retried, and
  would otherwise look exactly like a permanent outage with nothing explaining
  it.
- **ONE EMBEDDING MODEL, INCLUDING AI REACH.** `reach_embeddings` was a second
  stack -- `BAAI/bge-small-en-v1.5` on CPU at 384 dims -- and it now delegates
  to the shared client. Migration 0058 widens `jobs.reach_embedding` to 1024 and
  NULLs every vector, which is not data loss: a bge-small vector and a Voyage
  vector share a column name and nothing else, and `bd_leads` re-embeds a NULL
  on the next search. The COLUMN stays separate from `jobs.embedding`; only the
  model is shared.
- **A same-width swap is not a same-space swap.** `profiles.embedding` and
  `jobs.embedding` are `vector(1024)` and Voyage is pinned to 1024, so nothing
  needed migrating to remain STORABLE. They are not COMPARABLE with the BGE-M3
  vectors already in them, and retrieval mixes two spaces until a re-embed runs.

### PART A, the three-layer framework

- **Layer 1 is a Python constant, and that is the whole reason it holds.**
  `hiring/department_models.py`. A table has an UPDATE, an UPDATE eventually
  gets an admin screen, and an admin screen makes Layer 1 client-editable -- at
  which point the layering is decorative. Same argument `candidate_profile_form`
  already makes.
- **A lower layer may TUNE a higher layer within declared bounds and may never
  SUSPEND one.** `hiring/layers.py`. `BOUNDS` is a table of multipliers around
  1.0; `INVARIANTS` is the list that carries no bound at all and is refused
  outright -- the Must-have cap, auto-rejection, authenticity, evidence
  sufficiency, protected-attribute inference, exposing a number. **Every refusal
  and every clamp is RECORDED**: a clamp that left no trace is indistinguishable
  from an input that was already in range.
- **The composed product is clamped too, not only each term.** Two layers each
  applying the maximum must not compound past what one was allowed to ask for,
  or "within declared bounds" is a claim about the steps and not the result.
- **A weight is `baseline x company x situation x role`, and all four terms are
  stored.** `hiring/transformation.py`. That is the acceptance criterion: a
  Layer 2 or Layer 3 change must demonstrably MOVE a weight, not merely appear
  in a summary. Verified: a Turnaround raises a Track Record competency from
  1.10 to 1.4850 and a Greenfield lowers it to 0.9900.
- **These weights exist ONLY inside the Tatva matrix derivation.**
  `matching.WEIGHTS` stays deleted and `test_scoring.py` still asserts its
  absence. The two faults of the old table are both absent here: it was a fixed
  0.35/0.30/0.20/0.15 applied to every role in the product, and it was SHOWN to
  the client as "35% role-fit weighting". These are per-job, derived from three
  declared layers, and never cross an API boundary.
- **Nothing enters the matrix without completing all seven stages.** Competency,
  observable evidence, evidence sources, assessment method, weight, threshold,
  and disqualifier if applicable. `Item.is_complete` refuses at `build`, not
  later -- a partially-transformed item is one whose grade rests on a stage
  nobody ran.
- **`match_competency` returns None rather than a best guess.** Forcing a
  role-specific phrase onto the nearest baseline would relabel it as something
  the department model already knew about, which looks like traceability and is
  not. A None anchor is an honest provenance.
- **Situation misclassification is the most expensive error at intake**, because
  it re-weights the WHOLE matrix coherently and invisibly -- nothing downstream
  can detect it, since there is nothing inconsistent to detect. So Bodha reads
  the classification back with its consequence and its most-confused-with
  alternative, and a human confirms it before the session closes.
- **Bodha has TWO mandates on one agent.** The per-job SWOT session, and the
  one-time-per-client Company DNA intake: twelve sections, forced trade-off
  scales in section 2 (a free-text "what do you value" is always "excellence and
  integrity" and modifies no weight), and observable-evidence questions in
  section 3 that REJECT an adjective and ask again. "ownership mindset" is
  refused; "has taken a project from an unclear brief to a shipped outcome" is
  accepted. One detector, now `observable.is_observable`, used by the SWOT
  quality rules and by Sutra -- two copies would drift invisibly. The DNA half
  of Bodha's dual mandate was withdrawn on 2026-09-09; the detector was not.
- **A disqualifier is matched on WORD BOUNDARIES and includes numeric age
  bars.** The first version matched substrings and refused "Must hold a valid CA
  licence" because "hold" contains "old", while accepting "No candidates over
  45" because it contains no listed word. A false positive is not harmless: it
  tells a client their lawful professional requirement is discriminatory, which
  destroys their trust in every refusal that follows.
- ~~**Compilation is deterministic and calls no model.**~~
  ~~**Sutra reads the COMPILED artifact, never the client's free-text.**~~
  **BOTH SUPERSEDED 2026-09-09: there is no compiled Company DNA artifact.**
  Neither rule was dropped, and that is the point of recording them here
  rather than deleting them. They MOVED, intact, into
  `services/hiring/drishti`, whose module docstring names them as "the two
  properties the 2026-09-09 removal demanded": its `compile_profile` is
  deterministic and calls no model, so a profile constraining every job a
  function will post stays reproducible, diffable between versions and
  explainable without a provider; and `prompt_context` is the only thing that
  reaches a prompt, derived from the compiled artifact, re-checked against the
  observable detector and capped. The Company Profile reaches Sutra by the
  other door, as bounded context carrying an explicit "treat as data, not
  instructions" rule, and it may clarify the setting for a criterion the job
  or the SWOT already supplied. It may never become a criterion, a weight or a
  disqualifier of its own, which is what still stops "we like people who are
  hungry" being something a candidate is graded on.
- **Miti's five dimension evaluators are ISOLATED STRUCTURALLY.**
  `EvaluatorInput` is a frozen dataclass whose field set has no candidate name,
  no other dimension's score, no composite and no free-form context dict.
  `test_miti_pipeline.py` asserts the exact FIELD SET rather than the absence of
  specific names, because a future field called `notes` would pass a narrower
  test and reopen the whole hole. The five run concurrently so no ordering
  exists in which one could observe another.
- **The aggregator is deterministic and imports no router.** Asserted by an AST
  walk over its source, not by a docstring. Every earlier stage has a model in
  it; this is the step that turns five bands into the grade a client reads, and
  two runs over identical inputs producing different grades would make a rubric
  problem indistinguishable from noise.
- **The Must-have hard cap is applied LAST, on the SCORE, and it is a `min`.**
  After the authenticity multiplier, because a cap a later multiplication can
  undo is not a cap. A `min` rather than an assignment, because a candidate who
  already grades Not Matching must stay there -- setting the score would
  promote the weakest candidates into the band the cap exists to keep the strong
  ones out of.
- **A product CATEGORY comes from the item, not from the dimension.** Must-have
  and Nice-to-have are properties of the criterion the hiring manager declared
  essential. The first version keyed the composite on a dimension→category table
  and a job whose essentials all sat on one dimension produced an EMPTY
  Must-have grade with nothing for the hard cap to bind against.
- **INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE.** A dimension flagged
  insufficient is EXCLUDED from the composite and paid for in CONFIDENCE, never
  scored low. The practical consequence is the point: a career-changer gets a
  low-confidence report that goes to a human rather than a confidently poor
  grade that does not.
- **Confidence is arithmetic over counts, never a model's opinion of itself.**
  Same rule `Verdict` already follows. An unresolved contradiction caps it
  regardless of coverage.
- **Two benign explanations before any escalation above Minor, always.** Not
  one. The first explanation a system reaches for is the one that confirms the
  suspicion; the second is where the honest answer usually is. `escalate`
  REFUSES to raise severity without them -- it does not warn, the escalation
  simply does not happen. Deterministic stock explanations exist per axis so the
  rule holds during a provider outage, because an outage that silently disabled
  integrity escalation would look like a clean run.
- **Independence is counted by ORIGINATOR, never by document.** A resume line
  and the candidate restating it in the interview could not have disagreed:
  that is one person saying one thing twice. Platform memory is never
  independent -- it is derived from things already counted. An unknown source
  type is assumed DEPENDENT, because assuming independence manufactures
  corroboration.
- **NO FLAG EVER AUTO-REJECTS, and the enforcement is the absence of the
  capability.** `TriangulationResult` has no reject field, no status and no
  decision. G3 fails LOUDLY and blocks NOTHING, because a blocking integrity
  gate would end a candidacy without a person ever seeing the finding.
- **G2 is non-blocking for a fairness reason.** A blocking sufficiency gate
  would refuse a report to exactly the candidates who most need a person to
  look, which is a silent rejection with better manners.
- **G4 asks whether a human DECIDED, not whether they approved.** All four
  dispositions pass, including `rejected`. A gate requiring approval could be
  satisfied by nagging; a gate requiring a recorded decision is satisfied only by
  someone having looked. There is no `auto_cleared`, and a Postgres CHECK
  refuses one.
- **`review_dispositions.decided_by` is ON DELETE RESTRICT**, alone among user
  references in this schema. A disposition whose person was erased asserts that
  a human decided while being unable to say who, which is indistinguishable from
  the pipeline having written it.
- **Siddhi's citation enforcement is STRUCTURAL.** `Section.render` is the only
  path to text and it raises on an uncited statement. There is no `force`, no
  `strict=False`, no `allow_uncited` -- a bypass parameter is a bypass that will
  be used, in a hotfix, at the end of a release. A FABRICATED citation raises a
  different error class than a missing one, because it is worse: it reads as
  provenance.
- **A GAP statement needs a citation, and this is the entry worth defending.**
  "There is no evidence of X" feels uncitable; the citation is the evidence that
  was SEARCHED. Without it, a gap in the assessment is reported as a gap in the
  candidate.
- **`Evaluation` is the WORKING; `functional_skills_reports` is the DELIVERED
  artifact.** One is internal and replaceable by a rescore, the other is
  immutable and client-facing. One table would force a choice between making the
  working immutable and making the report mutable.
- **Three of Runbook §59's five objects already existed** as `jobs`,
  `job_competencies` and `candidates`/`profiles`/`job_candidate_links`, and were
  NOT duplicated. Same substitution the billing work made when its spec wrote
  `companies` and this schema meant `tenants`.

### PART C, navy and teal

- **The brand is navy `#012654` and teal `#00888A`, SAMPLED not chosen.**
  Weighted centroids over 102,974 and 48,891 pixels of `logo300.jpeg`, both
  inside spec-doc5 §C.1's stated ±2. This replaced an indigo-violet ramp
  (`#5028E0`) which is precisely the palette Impeccable's `ai-color-palette`
  detector flags -- and it flagged four call sites here before the change.
- **NAVY IS STRUCTURE, TEAL IS EVIDENCE.** Navy carries primary actions,
  navigation and the frame; teal carries what is corroborated and what is cited.
  Teal is the one colour in the system with a meaning, and spending it on a
  button would waste it on the element that needs none.
- **THE BRAND TEAL FAILS AA FOR BODY TEXT ON WHITE.** 4.30:1, below 4.5. That is
  a measured property of the colour the client chose. teal-600 is a FILL, a RULE
  and an ICON colour; **teal TEXT on white is `teal-700`** (5.99:1).
  `scripts/check-contrast.mjs` asserts both, and also asserts the NEGATIVE case
  -- that teal-600 is still below the bar -- because if that stopped being true
  the DESIGN.md rule sending everybody to teal-700 would have become a lie.
- **`brand-*` is an alias onto `navy-*`.** 193 call sites say `bg-brand-600`, and
  rewriting them in the same change that recolours the palette would be one diff
  doing two jobs with indistinguishable regressions. New work uses `navy-*` and
  `teal-*`.
- **No gradient between two hues.** A single-hue tint is fine. Navy-to-teal
  would be the same tell in the brand's own colours.
- **`DESIGN.md` and `PRODUCT.md` are the design authority**, in the
  awesome-design-md nine-section format. `frontend/scripts/impeccable-gate.mjs`
  gates CI: it exits non-zero on any finding not listed in
  `.impeccable-exceptions.md` WITH A REASON, because a detector that only prints
  warnings is one everybody scrolls past. Two exceptions today, both semantic
  left rules.
- **The Three.js R+P logomark is landing and login ONLY**, and
  `logomark-placement.test.ts` counts the call sites. The failure mode is not
  deliberate misuse; it is that a component gets reused, which is what
  components are for. The mark is PROCEDURAL rather than traced so the shared
  stroke is its own addressable mesh -- a traced outline is one blob of geometry
  and the brand's one distinctive idea could not be animated.
- **Text is never grey**, enforced at the token. Unchanged.

### PART D, AWS-ready and NOT DEPLOYED

- **NO LIVE AWS DEPLOYMENT HAS BEEN EXECUTED, and that is a requirement.**
  spec-doc5 §D.1 and its acceptance list make running `terraform apply` against
  production in this phase a FAILURE OF SCOPE. Two independent stops: every
  deploy job is behind `vars.AWS_DEPLOY_ENABLED`, which is unset, and the
  production apply additionally sits behind a required-reviewer environment.
- ~~**`terraform validate` is verified; `terraform plan` is NOT and cannot be.**~~
  SUPERSEDED 2026-08-29 by spec-doc6 §13.3, which asked for the planning profile
  this paragraph said was impossible. `bash infra/plan-offline.sh --artifact`
  runs `plan` for staging and production with `skip_credentials_validation`,
  `skip_requesting_account_id`, `skip_region_validation` and
  `skip_metadata_api_check`, a local backend and dummy static credentials, and
  it succeeds: 137 resources to add for staging, 135 for production, uploaded by
  CI as an artifact a human can read.
  **Be exact about what that proves.** The configuration is internally
  consistent, the graph resolves, every module reference exists and every
  argument type-checks against the provider schema. It proves NOTHING about a
  real account: not creatability, not quotas, not IAM behaviour, not that the
  chosen instance types exist in the chosen region. It runs against account
  `000000000000`, region `xx-plan-1` and an RFC 2606 `.invalid` domain, and has
  never contacted AWS. Do not let "plan succeeds" read as "ready to run".
  **The gap over `validate` is not theoretical**: the first offline run failed on
  `var.secret_policy_arns["frontend"]`, an apply-time error that eleven modules
  of `terraform validate` had reported clean for the whole previous phase.
- **IAM is scoped PER SERVICE, enumerated, never a prefix.** `service_secrets`
  maps a service to the exact secrets it may read: beat gets the broker and
  nothing else, the worker gets no Firebase key, migrate gets one secret. The
  GCP-phase finding was one runtime identity holding all of them -- nothing was
  misconfigured, the grant was simply wider than the need, and a wildcard looks
  identical whether it is over-broad or exactly right.
- **Task role and execution role are SEPARATE.** The execution role pulls the
  image and fetches secrets to inject, before the container starts; the task
  role is what the application's own SDK calls use. One role means the
  application can read every secret the platform injects.
- **ECR tags are IMMUTABLE**, which is what makes a SHA tag a permanent name for
  specific bytes and makes digest verification mean anything. Images are
  retained by COUNT, never by age: an age rule deletes the image a long-running
  service needs to restart from.
- **Verify by DIGEST, not by exit code**, and read the RUNNING TASKS rather than
  the service definition. The gap between them is a circuit-breaker rollback,
  which is exactly the case the service definition reports as success.
- **`aws ecs run-task` returning is not the migration finishing.**
  `run-migration.sh` polls for STOPPED and reads the exit code. A job that was
  accepted and then died is what a pipeline reports as success -- this platform
  has had that exact failure.
- **The approval gate is CHECKED, not assumed.** An environment with no required
  reviewer runs the job silently while the workflow file still reads as gated.
  `verify-approval-gate.sh` fails the run when it is missing.
- **The data subnets have NO route to the internet in either direction.** Not
  even outbound through NAT. An attacker does not need to reach the database from
  the internet; they need the database's host to reach them.
- **Redis is `noeviction`, not `allkeys-lru`.** It is the Celery broker, not a
  cache. The LRU default would silently evict queued TASKS under memory
  pressure, and the symptom is work that was accepted and never happened with
  nothing recording the drop.
- **Fargate does not scale to zero.** The one place it is not equivalent to
  Cloud Run, and there is a floor cost the previous platform did not have.
- **When the GCP deploy script was deleted, six secret-hygiene assertions began
  reporting SKIPPED** -- one word from PASSED in a summary line -- and nothing
  was enforcing secret hygiene any more. They were ported to read the Terraform
  and the workflow, and are now stronger: not "the script does not print the
  DSN" but "the worker's IAM policy does not contain the Firebase key".


## Current release authority, Tatva Assessment and the PRISM Report (2026-08-23)

- **Tatva Assessment is the PROCESS; the PRISM Report is the DOCUMENT it
  produces.** The Tatva Assessment is the evaluation framework, previously
  called the PPI framework or PPI matrix, and its three dimensions are
  Must-have, Nice-to-have and Behavioural. Completing one produces a PRISM
  Report. The two names are never used for each other and never used as
  synonyms in copy, in a heading, in an email or in a comment. The client
  stated the distinction twice, which is what a name people will otherwise
  collapse into one looks like.
- **The report header is exactly, and only:** `PRISM Report` over
  `Predictive Role Intelligence & Suitability Mapping`. The abbreviation alone
  does not tell a reader that they are holding the document rather than the
  framework, so the expansion travels with it everywhere the header is drawn,
  on screen and in the PDF. Pinned in `tests/test_prism_report.py`.
- **The section order is fixed:** AI Score, Overall Assessment, Must-have,
  Nice-to-have, Behavioural, Gap Analysis & Action Plan, Validation. **Gap
  Analysis now PRECEDES Validation**, reversing the earlier order. Validation
  is the candidate's own unrated submission, so the action plan belongs beside
  the grades it was drawn from rather than after a block of uninterpreted form
  answers. The order is written down once per renderer,
  `REPORT_SECTION_ORDER` in `components/functional-skills-report.tsx` and
  `report_pdf.SECTION_ORDER`, and BOTH renderers walk their own constant.
  `test_the_screen_and_the_pdf_agree_on_the_section_order` reads both out of
  source and compares them, because the failure being prevented is a recruiter
  approving a report on screen and mailing a PDF that reads differently.
- **THREE radar charts, not four. This SUPERSEDES the 2026-07-30 rule "FOUR
  radar charts, each plotting TWO shapes" and the spec-v4 line "Exactly four
  number-free radar charts are shown".** The charts are Overall, Must-have and
  Nice-to-have. The Behavioural dimension carries a grade and a 45-50 word
  remark and NO chart, because spec doc 4 lists a chart under each of the other
  three sections and lists only a grade and a remark under Behavioural. Do not
  re-add the fourth: it was removed by the client, not lost.
  The filter is at the RENDERER (`RENDERED_CHART_KEYS`, in both files), never
  at `functional_assessment.build_radar_charts`. A report is immutable, so
  every report written before today still carries a behavioural chart in its
  stored payload; filtering at the generator would leave an old report showing
  four charts and a new one three, which is the drift the fixed chart set
  exists to prevent. Everything else about the charts is unchanged: two shapes
  on shared axes, and NO number on an axis tick, a data label, a tooltip or a
  legend.
- **The report carries its reference code, and it is a label.** The
  COMPANY-JOB-CANDIDATE code is rendered under the header, monospace and
  select-all on screen, so a printed report and a row in the candidate table
  can be matched by eye and quoted without transcription errors. It identifies
  a row and authorises nothing; nothing may ever read it back as permission.
- **The code still says PPI, deliberately, and must not be "fixed".** The
  `ppi` module, `job_competencies`, `ppi-report-modal.tsx`, `report_pdf.py`,
  the `/framework` routes and the persisted trace fields keep their names. A
  route is quoted in report links already in people's inboxes and in traces a
  rolling deploy is still writing, and every report written before today was
  filed under those names, so a symbol rename would cost a reader access to an
  existing report and buy nothing anybody sees. The rename is USER-VISIBLE COPY
  ONLY.

## Current hard rules, the ten-system agent framework (2026-08-18)

- **Tools RAISE, loops DEGRADE, and that split is why both stay simple.**
  `services/tools.execute` is the only path an agent reaches data through:
  resolve, permit, validate input, cache, bounded attempt, validate output,
  count. It raises on final failure. `agent_loop.run_loop` still never raises,
  and it is still where a user-visible degradation is decided. A tool that
  swallowed its failure would hand its caller an empty shape indistinguishable
  from a legitimately empty result, and the caller would render it.
- **An agent's reach is `tools/permissions.AGENT_TOOLS`, checked BEFORE the
  handler runs.** Data, never a role branch inside a handler, exactly like
  `require_capability`. The email agent holds no resume and no transcript tool:
  an email states a decision that was already made, and reach it does not have
  is reach a future prompt cannot start using. Enforcement is ordering, not
  politeness -- a refusal that ran the handler first has already read the row it
  was refusing to show.
- **Compensation stripping and the four-grade scale are properties of the tool
  SHAPE.** `JobFacts` has no compensation field and no free-form escape hatch;
  `Competency.required_level` is a WORD converted from the stored integer. ESD
  16 and the no-numbers rule were previously enforced at one call site each.
  Every agent prompt is now downstream of these models, so both travel with the
  layer instead of with somebody's memory.
- **`extract_assessment` is NEVER cached and never idempotent.** A live
  conversation grows between two reads by design. An agent scoring a transcript
  two answers stale is scoring the wrong assessment.
- **A verifier returns a `Verdict` that converts to `agent_loop.Critique`.**
  There is no second retry framework: the loop's `reflect -> improve` step IS
  the auto-regeneration, already bounded twice over. `Verdict.confidence` is
  ARITHMETIC over severity counts, never a model's opinion of itself -- an LLM
  judge makes the criteria unfalsifiable and fails exactly when the provider is
  already failing. One high finding is disqualifying; two mediums are; one is
  not.
- **The specification this framework implements was written against an older
  product, and two of its checks are deliberately absent.** The ranking
  weight-sum check (there are no weights; `tests/test_scoring.py` asserts the
  symbol's absence) became a ranked-list DIVERSITY check, which is what the
  weighting was standing in for. The five-label scale became `services.rating`'s
  four grades. Both absences are documented where the check would have gone, so
  the next reader finds out why rather than re-adding it.
- **Retrieval is CHUNK-level and is a different question from ranking.**
  `context_chunks` (0054) holds many small pieces per document with their own
  vectors; `profiles.embedding` and `jobs.embedding` are unchanged and still
  rank candidates. Retrieval must never decide who gets scored -- a candidate
  linked to a job is always scored, and retrieval is a ranking prior only.
- **The lexical retriever ORs its terms, and this was found on the live index
  rather than in a test.** `plainto_tsquery` ANDs every term, so the query
  "kafka partition rebalance migration" matched NOTHING in a resume containing
  Kafka, partition and migration. The failure was silent: fusion still returned
  the semantic hits, so retrieval looked like it worked. Precision is fusion's
  job, not the lexical retriever's.
- **Fusion is Reciprocal Rank Fusion, never a weighted sum.** A cosine distance
  and a `ts_rank` are not on the same scale as each other or across two
  queries, so any fixed weighting is a number nobody can justify and everybody
  eventually tunes by feel. RRF reads ORDER only.
- **There is no cross-encoder reranker deployed, and the code says so.**
  `retrieval.rerank` takes its scorer as a parameter and defaults to a
  deterministic lexical-affinity pass with a section prior. Pretending a
  `bge-reranker` service exists behind an interface that silently returns the
  input order would be worse than not having one.
- **Context is assembled by dropping WHOLE chunks and recording the drop.**
  Cutting the assembled string hands a model half a sentence, and a model handed
  half a sentence completes it from its own priors -- into text a grade is
  written from. Compression is EXTRACTIVE and calls no model: an LLM
  summarisation inside retrieval spends the interactive budget before generation
  starts, and an outage in the summariser becomes an outage in the feature.
- **The planner calls no model and is pure arithmetic.** Same inputs, same
  plan, every time -- otherwise a latency regression cannot be told apart from a
  provider sampling differently, and a provider outage costs you the ability to
  plan around a provider outage. Its one real decision is fast path versus deep
  path, and the threshold is deliberately low: a fast path on a task that needed
  reflection produces a worse report permanently, while a deep path on a simple
  task costs a second.
- **Reflection is still mechanical.** `reasoning.runner` has a reflect stage and
  it calls `agent_loop.reflection_text`, unchanged. The reflection is real; it
  is not generative, for the reason above.
- **A trace carries identifiers, counts and timings, and NEVER content.**
  `agent_execution_traces` (0055) stores a defect's type and location and drops
  its `detail`, because a detail can quote the output. `_SAFE_STAGE_KEYS` is an
  allowlist, so the next person adding "the prompt we sent" for debugging finds
  it dropped rather than finding it in the database a month later. Persisting a
  trace never fails the run it describes.
- **Experience memory is a HINT and never a gate.** `agent_learnings` rows are
  prepended to a prompt as guidance and cannot relax a word range, skip a
  verifier or lower a threshold. A mechanism that could would let one unlucky
  run permanently lower the bar, and the code doing it would be a table row
  rather than a reviewed line. Nothing is applied below `MIN_OBSERVATIONS`.
- **Budgets refuse BEFORE the work.** Checking afterwards means the overspend
  already happened and the ceiling is a report. Cost, iterations and replans are
  separate ceilings because a loop can spin without spending. Every refusal is
  recorded: a budget that stopped something silently is indistinguishable from a
  task that simply finished.
- **A stub is always flagged for human review.** Three levels -- full, degraded,
  stub -- and the stub exists so a provider outage returns the product's
  previous behaviour rather than a 500. What makes that honest rather than
  misleading is `needs_human_review`, never a stub that reads like a result.
- **A sensitive action requires a human at ANY confidence.** Reject, revoke an
  offer, override a ranking. Low confidence only WIDENS the review set. Building
  it the other way round means the agent's own opinion of itself authorises an
  irreversible act, and a confidently wrong agent is the one that should be
  stopped. Enforcement remains the absence of a write tool.
- **Retrieved chunks pass `conversation_guardrails.inspect_answer` too.** A
  resume is a file a candidate uploaded and a JD is text a client typed; an
  injection in a PDF reaches the model by exactly the path an injection in a
  chat message does. A flagged chunk is QUARANTINED, not fatal -- failing the
  retrieval would let one poisoned paragraph disable assessment for that
  candidate.
- **`app/scripts/eval_agents.py` gates CI as the third eval.** It measures the
  framework rather than what an agent says: routing against permissions, tool
  reachability, deadline feasibility, and ten specific past defects. It reports
  quality metrics as UNAVAILABLE while no expert-labelled dataset exists, and it
  must keep doing so -- an unmeasurable quality figure reported as 0.0 is a
  number that means nothing and looks like something. The 50-100 stratified
  expert-rated cases are HUMAN work and must never be synthesised: ground truth
  produced by the same class of model being evaluated measures agreement with
  that model, not quality.

## Current release authority — Product Development Specification v4 (2026-08-14)

- **ReadyPick is a standalone AI-native product.** Product and marketing copy
  uses only ReadyPick branding. Do not reuse another product's name, logo,
  collateral, positioning language, client identity, or go-to-market story.
- **Customer roles are hierarchical, not flat.** The chain is Super Admin
  (`client`) -> Recruitment Manager -> Recruiter -> Hiring Manager. A person
  may manage only roles strictly beneath their own and may grant only a
  capability they hold. `users.permissions_json` remains the sparse per-user
  overlay, and every operational endpoint in jobs, pipeline and candidates must
  continue to enforce it through `require_capability(...)`; never add role-name
  branches to business routers. Legacy `hr_manager` ranks beside Recruitment
  Manager until existing accounts are migrated deliberately.
- **Job setup has two fixed, job-specific outputs.** The Reporting Authority
  SWOT intake informs a PPI matrix of Must-have, Nice-to-have and Behavioural
  criteria; the Matching Agent separately proposes at least five coarse,
  resume-only matching categories. Both are human-reviewed and finalized once
  per job. The PPI matrix supports drag/drop between Must-have and Nice-to-have.
- **One candidate conversation, one scoring agent.** Questions are generated
  per candidate from the JD, saved SWOT-informed matrix and resume, while the
  matrix stays identical for everyone on that job. Must-have and Nice-to-have
  use question rubrics; Behavioural uses judgement-based scoring. There is no
  standalone technical agent or split behavioural bot.
- **Validation is factual application data.** Current CTC, expected CTC,
  notice period, joining date, document readiness and the exact answer to
  "Why does this role interest you?" are captured before assessment, never
  scored, and shown as an explicit recruiter Q&A view. CTC is annual INR and
  the UI gives `4,00,000` as the worked example.
- **Client-visible grades are words only:** Highly Matching, Matching,
  Moderately Matching, Not Matching. Never expose scores, percentages or
  letter grades. Any Not Matching Must-have caps Overall at Moderately
  Matching. Rated PPI and Overall remarks are 45-50 words; AI Score category
  remarks and gap probes are 25-30 words.
- **Reports contain AI Score, then PPI Assessment, then Validation, then Gap
  Analysis & Action Plan.** Suggested interview questions are removed. Gap
  groups reuse item remarks, order Not Matching before Moderately Matching,
  state empty groups, and ground every probe in the candidate's actual answer.
  Exactly four number-free radar charts are shown: Overall, Must-have,
  Nice-to-have and Behavioural.
- **Credit gates are immediate.** Warn at or below 30%; at zero block job
  creation and new assessment starts. An active conversation may finish, but
  report finalization waits for top-up. Never fail silently or degrade access.
- **Company profile edits begin with professional web research.** Prefer the
  official site, LinkedIn, Glassdoor and AmbitionBox; reject Facebook, X,
  Reddit and Instagram. Show sources and require an explicit Edit action before
  a person can change or save the generated draft.
- **The customer AI Dashboard is deleted.** Do not restore its route, component
  or navigation entry. Items explicitly deferred by spec v4 (LinkedIn sourcing,
  go-to-market execution, sourcing-seat/ToS choices and Resume Alignment Agent)
  remain unimplemented until decided.

## Current hard rules, per-candidate technical questions + loop engineering (2026-08-06)

- **There is no preset technical question bank, and a company can never author
  one again.** `technical_questions` was a per-JOB list of stored strings a
  company created, edited and finalised through the Company Portal, and every
  applicant read the same strings whatever their resume said. The five routes
  (`GET/POST/PUT/DELETE /jobs/{id}/questions`, `POST /jobs/{id}/finalize`), the
  screens behind them, the generator and its schemas are DELETED, not
  deprecated. Pinned by `test_the_preset_technical_bank_generator_is_gone` and
  `test_the_preset_bank_routes_are_gone`. The TABLE survives unread: reports
  written before today were scored against those rows, and dropping it turns
  "what was this candidate actually asked?" into an unanswerable question.
- **A generated question is only sound if its RUBRIC was generated WITH it.**
  This is what unlocked the change. The old rule forbade generating a technical
  question mid-conversation (`interviewer.MODE_REWORD`) because the answer was
  scored against a preset question's stored rubric, so a fresh question would be
  graded against a rubric for a question nobody was asked.
  `technical_interview.write_question` writes both in ONE call and persists both
  before the candidate reads either. That is a STRONGER guarantee than the bank
  gave, where a recruiter could edit a stored prompt in the UI and leave its
  rubric behind.
- **The coverage plan stays deterministic; only the questions vary.**
  `technical_interview.skill_plan` is a PURE function of the JD and the grade,
  so every candidate for a job is probed on the same skills in the same order.
  That is what keeps two reports comparable now that no two candidates are asked
  the same words. Counts are unchanged: 20/17/15/12. Same rule the PPI framework
  follows, applied to the technical half.
- **Every generative task runs inside `services/agent_loop.run_loop`:
  plan -> execute -> evaluate -> (reflect -> improve)* -> verify.** Success
  criteria are DETERMINISTIC code, never an LLM judge -- the moment the guard
  matters most is the moment the provider is down, and a judge makes the
  criteria unfalsifiable as well as adding a second flaky dependency. A
  rejection is fed back VERBATIM as an instruction, which is the whole point: "you
  returned three of the five rubric bands" is a defect a model fixes when told,
  and the one-shot code it replaced threw the response away and shipped a canned
  string. Bounded TWICE -- `max_attempts` AND `deadline_seconds`, checked BEFORE
  each attempt, because N attempts at the per-task timeout is a multiple of what
  the user experiences. `run_loop` NEVER raises; it returns `fallback` with
  `degraded=True`, and `LoopResult.degraded` is the honest record that gets
  counted. Interactive loops get 2 attempts / 26s; background ones 3 / 240s.
- **A loop deadline must PREDICT the next attempt, not merely observe the
  elapsed time.** `elapsed >= deadline` sounds right and is not: one
  `conversation_turn` call is bounded by the router at 24s and the interactive
  deadline is 26s, so after a slow first attempt `24 >= 26` is False, attempt
  two starts, and the real worst case is 48 seconds with a candidate watching a
  text box. The check is `elapsed + longest_attempt_so_far >= deadline`, so an
  attempt that cannot FINISH inside the budget is never started, and a failed
  attempt's duration counts -- a timeout is the slowest and most informative
  thing that can happen.
- **A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED.** Measured on the live
  database 2026-08-06: 19 of 35 jobs, across three entire tenants, carried
  `framework_generated_at` and had ZERO competency rows. Every one of those jobs
  was permanently stuck at `questions_pending_review` with an empty framework
  nobody could approve, so no candidate on any of them could ever be assessed --
  and that IS what "the portal does not work for other companies" was. It stayed
  invisible because every health check asked the stamp rather than the table,
  including `remind_unapproved_technical_questions`, which filters on
  `framework_generated_at IS NOT NULL` and therefore specifically EXCLUDED the
  jobs whose generation had failed. Three changes, and all three are load-bearing:
  `ppi.generate_framework` now stamps ONLY when rows exist; the setup and
  framework GETs repair on read and report `framework_pending`; and
  `pickready.reconcile_job_setup` sweeps every tenant every 15 minutes asking
  the TABLE. Verified by repairing all 19 live jobs with every LLM provider
  down, on the deterministic fallback.
- **Job setup generates ONE thing, and that is why it could be renamed.**
  `pickready.generate_technical_questions` ran the bank generator FIRST and the
  framework generator second in one session, so any failure in the first half
  took the gating half with it. The task is now
  `pickready.generate_ppi_framework`; the old name stays registered as a
  delegating alias, because a beat entry, a queued message and a worker
  registration cannot be changed atomically during a rolling deploy.
- **A recruiter can read what a candidate was actually asked and answered**
  (`GET /assessments/transcripts/links/{link_id}`, `view_review_screen`). Keyed
  on the LINK, not the report: the transcript exists from the first answer and
  the report does not exist until the assessment finishes, so hanging it off the
  report would make the stalled-assessment case -- the one a recruiter most
  wants -- unreachable. Exchanges are paired SERVER-SIDE, because the follow-up
  rule (a probe reuses its parent's `question_key`, which is exactly how the
  scorers file it) would otherwise be reimplemented per client and drift.
  Paginated from day one; a non-managerial interview is up to 120 messages. No
  score, no rubric, no required level and no number crosses this boundary, and
  an answer is never re-worded or summarised -- a summary of an answer is not
  evidence of what someone said.

## Current hard rules, the conversational agent (2026-08-05)

- **"The pipeline passed" is not evidence that anything works.** On 2026-08-04
  every deploy was green, every revision was promoted, and production was
  serving the newest commit -- while three reported features did not work. The
  mechanisms were a change that shipped half of itself, a seed script judged by
  its exit code rather than by the rows it wrote, and smoke tests that only ever
  asserted status codes. A green run means the service answers HTTP. Verify a
  claim against the thing a user touches: a row count from the live database, a
  grep of the DEPLOYED image (`docker run --rm --entrypoint sh <digest> -c
  'grep -rl ... /app'`), or an actual API response. Never against the source
  tree, and never against a `--no-traffic` staged revision.
- **How freely a question may be generated is decided by HOW ITS ANSWER IS
  SCORED, never by preference.** A PPI answer is scored against its COMPETENCY
  across every answer filed under it, so the question is written fresh each turn
  from the JD, the resume, the competency and the transcript
  (`interviewer.MODE_GENERATE`). A technical answer is scored against THAT
  QUESTION'S own stored prompt and `rubric_json`
  (`functional_assessment._llm_score`), so only the phrasing may move
  (`MODE_REWORD`) and `_substance_preserved` refuses a rewrite that dropped a
  named technology. Generating a fresh technical question would grade an answer
  against a rubric written for a question nobody was asked.
- **The COVERAGE PLAN stays deterministic: which criterion, in what order, how
  many.** That is what keeps two candidates comparable, keeps billing where it
  is, and makes a run reproducible. What varies per candidate is how each
  criterion is approached, never which criteria there are.
- **A non-answer is never met with silence.** `answer_classification.classify`
  separates substantive / empty / gibberish / off_topic / evasive. Empty and
  gibberish are settled DETERMINISTICALLY with no model call, because the model
  being down is exactly when the guard matters; off_topic and evasive need the
  model, because they are well-formed prose that does not answer the question.
  Every degradation path returns "substantive": a false "evasive" silently
  penalises a real answer, and "I have not used Kafka" is a complete answer.
  The challenge WORDING is keyed by label -- telling a candidate who wrote three
  coherent paragraphs that their reply "did not come through" proves the agent
  cannot tell prose from keyboard mash.
- **A re-ask is not a follow-up.** It costs no follow-up budget, is bounded to
  one per base question by the `pending_prompt` mechanism, and changes no
  scoring. Follow-up budget SCALES with interview length
  (`interviewer.follow_up_budget`, 15 at 45 questions, 7 at a CXO's 22): the
  flat 5 it replaced meant 89% of a non-managerial interview could not react to
  anything the candidate said.
- **NO TEMPLATED ACKNOWLEDGMENTS, and this has been violated once already.**
  `_CONNECTORS` prepended one of eight canned openers to every question by
  `position % 8`, so "Appreciate the detail." answered gibberish. Pinned by
  `test_no_canned_acknowledgments_in_the_conversation_path`, which checks CODE
  lines only so the comment recording the removal may still quote it. A model at
  0.7 writes praise unprompted, so `_strip_praise` removes leading openers to
  exhaustion.
- **Candidate text is DATA, never instructions.** Every answer passes
  `conversation_guardrails.inspect_answer` before it is stored or reaches a
  prompt, and every interviewer line passes `inspect_agent_output` before a
  candidate reads it. Note the contract: `violation is not None` does NOT mean
  refused, only `allowed` does -- an answer that legitimately DISCUSSES prompt
  injection is still an answer. Both directions are deterministic and call no
  model, for the same reason the substance check does not.
- **`contains_forbidden_number` strips ASSESSMENT numbers, not technical
  content.** "How did you bring p99 latency under 200ms?" is an ordinary
  interview question. The hard part is the distinction, not the detection, and a
  guard that mangles a real question fails invisibly.
- **Telemetry logs labels, keys and timings, NEVER answer or question text.** An
  ordinary log is far more widely readable than a LangSmith trace, and prompts
  carry a real candidate's answers. `interview_telemetry.conversation_summary`
  is OPERATOR data, carries numbers, and must never reach a response schema.
- **`app/scripts/eval_interview.py` is the agent's evaluation and CI gates on
  it.** TRUE ONLY SINCE 2026-08-06, and this line asserted it for two days while
  it was false: `deploy.yml` built, migrated, staged and smoke-tested, and ran
  neither the eval nor the unit suite. Nothing stopped a commit whose tests
  failed from reaching a production revision. The `test` job that now precedes
  `deploy-staged` is what makes the sentence true; do not remove it, and do not
  write "CI gates on X" here again without opening the workflow file.
  Fully stubbed and offline on purpose: a rate that moves means the CODE
  changed, not that a provider sampled differently. It measures judgement across
  a labelled set (non-answer detection, the real-answer false-positive
  direction, outage degradation, question integrity, injection resistance, the
  no-numbers rule in BOTH directions, budget determinism). It deliberately does
  NOT judge whether a real model writes a GOOD question; that needs a live model
  and a human. Thresholds are where they are today, not aspirationally -- a rate
  allowed to fall silently is a rate nobody is defending.
- **The demo seed creates APPLICATIONS, not just candidates.**
  `seed_demo_candidates` creates candidate rows and uploads resumes and does
  nothing else, by its own docstring. `seed_demo_applications` generates each
  demo job's PPI framework, approves it (scoped to `tenants.is_demo` read from
  the COLUMN, so Workify Corp keeps its manual gate) and then creates the links.
  Production measured 32 candidates against 9 applications while every deploy
  reported success.

## Current hard rules, adaptive interview + demo fixtures (2026-08-05)

- **The assessment conversation is ADAPTIVE, and three things must never move
  with it.** `api/assessments.respond` used to be an index into a pre-generated
  list with no LLM call anywhere in the conversation, so "the agent has no
  memory" was not a prompt problem, there was no agent. `services/interviewer`
  now writes at most ONE follow-up per base question against the transcript so
  far. The invariants it must not break, each pinned by a test in
  `tests/test_conversation_flow.py`: a follow-up is answered under the SAME
  `question_key` (so `answers_by_key` hands the scorer one richer answer, never
  an unknown key that every scorer would silently DROP); it does NOT advance
  `next_question_index` (which is what fires `charge_completed`, so billing is
  unmoved); and a follow-up outstanding on the LAST base question HOLDS
  completion open, or the customer is charged and scoring dispatched while the
  candidate is still typing.
- **The interview is bounded by construction, not by convention.** One follow-up
  per question, `MAX_FOLLOW_UPS` per conversation, counted in a PERSISTED column
  so the ceiling survives a retry or a message that fails to write. Total turns
  are `len(prompts) + MAX_FOLLOW_UPS`, whatever the model returns.
- **Every follow-up failure path returns None, meaning "ask the next scripted
  question".** Outage, timeout, malformed JSON, a model echoing the string
  "null", a follow-up long enough to be a speech. A candidate is mid-assessment
  on a live request, so a provider problem costs the adaptivity and nothing
  else. Unlike `_llm_score`'s fallback, which invented a grade, this one is
  simply the product's previous behaviour.
- **Sampling temperature is DATA in `config/llm_providers.TASK_TEMPERATURE`, and
  the split is judge-versus-write.** Every task that JUDGES is 0.0:
  `behavioral_assessment`, `report_synthesis` (it states the grades a client
  reads, prose or not), `rerank`, `extraction`. A scoring call that samples
  above zero makes a candidate's grade depend on WHEN they were scored, which is
  unfalsifiable -- a disagreeing rescore reads as a broken rubric. Unlisted
  tasks default to 0.0, the safe direction. `conversation_turn` is 0.7 and is
  the only task above 0.5.
- **A non-answer never reaches a scoring prompt.** `services/answer_quality`
  routes gibberish, empty and single-token answers to the SAME unanswered path
  the product already had (`UNANSWERED_SCORE`, which grades Not Matching).
  Gibberish used to reach `_stable_score`, which hashes into 45..94: measured
  over 20,000 seeds, 69.6% graded Moderately Matching or better and 10.1%
  Highly Matching. The defect was never that gibberish could not fail; it is
  that a HASH decided whether it did. The guard is deliberately conservative:
  "I have not used Kafka" is a real answer and is scored low on its merits.
- **Demonstration tenants are exempt from billing REFUSALS, never from billing
  RECORDS.** `tenants.is_demo` is a column, not a UUID list in Python, so the
  exemption is visible in the table and a fourth demo tenant is an UPDATE.
  Sarkar Corp, ACRM Corp, Specter & Co. -- keyed by their seed UUIDs, never by
  name (a fourth tenant, Workify Corp, is REAL and must keep being billed; the
  brief that requested this called the third company "ACME Corp", which does not
  exist). `has_credit_headroom` checks the demo flag BEFORE summing the balance,
  because a demo tenant that has run assessments has a negative ledger like any
  other. Ledger entries are still written: a billing page with no usage on it
  demonstrates nothing. The dangerous direction is a LEAKED exemption, which
  raises nothing and just stops collecting money, so every test has a
  paying-tenant twin.
- **The 30 demo candidates and their resume corpus ship in the image.** The
  corpus lives at `backend/demo_resumes` because `backend` is the Docker build
  context; at `<repo-root>/resumes` it never reached the image, `resumes_dir()`
  returned None on Cloud Run, and the seed logged that it found nothing and
  EXITED 0. Production ran on two candidates against thirty while every deploy
  was green. `seed_resume_corpus` still refuses production by default (that
  guard protects `seed_dev_data`, which seeds an entire dev world); only
  `app.scripts.seed_demo_candidates` opts in with `allow_production=True`.
- **The migrate job has VPC egress, and the broker has publish timeouts.**
  Publishing to Redis has NO timeout by default, so an unreachable broker does
  not fail, it HANGS -- which silently defeats every `try/except` around an
  enqueue, because nothing is ever raised for the handler to catch. Observed as
  a management job that found 30 files then died at the 900s ceiling having
  written nothing, because the first `send_task` never returned.
- **Every LLM call is traced to LangSmith from ONE chokepoint,
  `llm_router.invoke_llm`.** Runs are `llm:<task_type>` and tagged, so the
  dashboard separates the agents with no per-agent wiring. Tracing is OFF
  without `LANGSMITH_API_KEY` (tests and local dev post nothing), a broken SDK
  degrades to an UNTRACED call and never a failed one, and prompt/completion
  TEXT is not sent unless `LANGSMITH_TRACE_CONTENT=true` -- prompts carry a real
  candidate's answers and a real JD, and that is the data owner's call.
- **Sign-in asks for no workspace.** The login page is Continue with Google,
  email, password. The backend routes to the correct portal from the account's
  own record; `?portal=` still deep-links for candidate apply links. The old
  picker was worse than redundant: choosing "Provider owner" never GRANTED
  provider access, so a wrong guess produced a refusal that read like a broken
  account.

## Current hard rules, PPI + the four-grade scale (2026-07-30)

- **There is ONE rating scale, it has FOUR grades, and it lives in
  `services/rating.py`.** Highly Matching, Matching, Moderately Matching, Not
  Matching. It replaced the product's two parallel five-label scales, the
  assessment's *Very High / High / Medium / Low / Developing* and matching's
  *Highly Matching / Matching / Moderate / Low / No Matching*, which had to be
  kept in step by hand in two modules and gave a reader no way to know that a
  "High" and a "Matching" meant the same thing. `matching.matching_label` and
  `functional_assessment.rating_label` are now thin aliases over it and must
  stay that way. The cut-points are unchanged (90 / 75 / 60), so a report
  written before this release regrades identically, with the old Low and
  Developing collapsing into Not Matching. Boundaries stay inclusive upward
  (rule 8).
- **PPI replaced PFI, and the difference is per-job versus per-product.** The
  ReadyPick Functional Index was ONE fixed dimension set per grade, reused
  across every job. ReadyPick Profile Intelligence generates a FRESH framework
  for every job from that job's own JD: at least 5 Primary Skills, 5 Secondary
  Skills and 5 Behavioural Competencies, more when complexity warrants it.
  `services/pfi_bank.py` and `services/validation_bank.py` are DELETED, and
  `tests/test_functional_assessment.py` asserts they cannot be imported. PPI is
  proprietary ReadyPick work and is never associated with DISC, MBTI, Hogan,
  CliftonStrengths or any other licensed instrument.
- **The framework is per JOB, the questions are per CANDIDATE, and confusing
  the two breaks the product's only comparability guarantee.** A saved
  framework is the fixed evaluation criteria for every candidate on that job.
  The questions probing it are generated from the JD, the framework, AND that
  candidate's resume, so two candidates get different questions against
  identical criteria. Counts are fixed by the CANDIDATE's grade, never by the
  job: 25 / 20 / 15 / 10 for non-managerial / managerial / leadership / CXO.
  Note the direction, MORE questions for a junior candidate.
- **"Culture" is refused as a Behavioural Competency, at three layers.** The
  generator prompt forbids it, `ppi.framework_is_complete` rejects it at save,
  and a Postgres CHECK on `job_competencies` refuses the row. A prompt
  instruction is a request, not a guarantee, and the Hiring Manager's Edit
  control can type anything. Cultural fit cannot be assessed accurately from a
  single assessment and PPI does not claim otherwise.
- **The manual review gate covers the FRAMEWORK ONLY** (amended 2026-08-04,
  client decision). `jobs.assessment_status` starts at
  `questions_pending_review` and reaches `ready_for_candidates` when
  `framework_approved_at` is stamped (`api/assessments._refresh_setup_status`).
  Until then the conversation 409s and `select-candidates` 409s, so nobody is
  mailed an assessment they cannot open.
  **The TECHNICAL question bank no longer gates anything**: generated questions
  are usable immediately, the "Finalise questions" control is gone from
  `components/job-setup-review.tsx`, and editing an individual question still
  takes effect at once. This reverses only the technical half of the 2026-07-30
  decision; the framework half stands. The two are not symmetric, and that is
  the whole reason one survived: the framework is the fixed criteria EVERY
  candidate on the job is graded against and is frozen once anyone is assessed,
  so a human confirming it is the product's only comparability guarantee. A
  technical question is scored against its own rubric, so a weak one costs one
  item on one report rather than making two reports incomparable.
  `questions_approved_at` is still stamped by the surviving finalize route and
  is now READ BY NOTHING; it was deliberately not dropped in the same change
  that stopped reading it, so a rollback needs no data restore.
  `pickready.remind_unapproved_technical_questions` keeps its name and its
  hourly schedule but now chases an unapproved FRAMEWORK, measured against
  `framework_generated_at` alone. The both-halves rule had NO test for its
  entire life, which is why `tests/test_assessment_setup_gate.py` now pins the
  rule that replaced it.
- **Publishing and assessment readiness are independent.** A published job
  takes applications and ranks them immediately; it just cannot invite anyone
  yet. Making publish wait on the review would hold the 30-day posting window
  closed over a step that only affects what happens after someone applies.
- **A saved framework is frozen, and reopening is refused once anyone has been
  assessed.** A report is immutable and states a grade against those exact
  criteria; letting the criteria change underneath it would make two reports on
  the same job incomparable, which is the one property the framework exists to
  guarantee.
- **Validation is six MANDATORY FIELDS on the application form, and nothing
  scores it.** Current CTC, expected CTC, notice period, joining date, document
  readiness, and "Why does this role interest you?" in the candidate's own
  words. `services/application_validation.py` is the single source of the field
  list, served to the form so the form and the report's Validation section
  cannot drift. It lands on `job_candidate_links.validation_json`, NOT the
  candidate profile: current CTC and notice period are answered per opportunity
  and change over time. Capturing it before the conversation is what lets a
  recruiter drop a candidate outside the budget before a credit is spent. The
  RECRUITER, not any agent, decides whether stated interest is genuine.
- **There are TWO scoring agents, not three.** Technical (per-question rubric)
  and PPI (against the saved framework), fanning out in parallel and joining at
  synthesis. `validation_capture` is a graph node but NOT a scorer: it copies
  the application's fields into the report shape and touches no model.
- **Report order is fixed: AI Score, then the PPI Assessment.** AI Score (four
  matching parameters, 25-30 word remarks) is the pre-assessment resume
  snapshot; Overall + Primary Skills + Secondary Skills + Behavioural
  Competencies (45-50 word remarks) is the post-conversation assessment. They
  are deliberately NEVER merged: a close match confirms the resume was
  accurate, and a gap is itself signal. Then Validation, then 8-10 suggested
  interview questions anchored on whatever graded Moderately Matching or Not
  Matching. Technical items are scored and anchor those questions but are not a
  rendered section.
- **FOUR radar charts, each plotting TWO shapes.** Overall, Primary, Secondary,
  Behavioural, each overlaying the job's required level and the candidate's
  assessed level on the same axes. Built from the SAME dimension rows the
  sections render, so a chart can never disagree with the text beside it. No
  number anywhere: not an axis tick, not a data label, not a tooltip. The
  legend names the two shapes by word. The Overall chart plots the three PPI
  category aggregates and EXCLUDES technical, which carries no job-requirement
  level and would force the requirement shape to invent a value for that spoke.
- **`report_dimensions.required_level` is COPIED onto the report, never joined
  to the live framework.** A written report is a permanent record of the
  criteria it was written against, and the job's framework may be edited later.
- **The four matching parameters carry NO mathematical weightage.** The
  0.35 / 0.30 / 0.20 / 0.15 table is gone and `services/matching.py` has no
  `WEIGHTS` symbol; `tests/test_scoring.py` asserts its absence. Two things
  were wrong with it: the weights were shown to the client as "35% role-fit
  weighting" beside each remark, which is a number reaching a client, and a
  fixed weighting asserts that skills matter 2.3x more than education for every
  role in the product, an arithmetic the comments do not perform. The internal
  overall is now their plain mean and orders a list; it is never displayed.
- **Report REUSE is retired.** `retake.PORTABLE_CATEGORIES` is an explicit
  EMPTY frozenset and `copy_report` never copies. Under PPI both the framework
  and the technical bank come from each job's own JD, so every section is
  job-scoped and carrying one across would state a grade against criteria the
  candidate was never assessed on, the identical error that always kept the
  matching section from travelling. The six-month classification still runs so
  the candidate is told why they are answering questions again.

## Current hard rules, subscriptions + the credit ledger (2026-07-28, later)

- **A customer's SUBSCRIPTION hangs off `tenants`, not `companies`.** The spec
  writes `ALTER TABLE companies ADD COLUMN razorpay_...`; in this schema a
  customer IS a `tenants` row, and `companies` is the client-authored page that
  does not exist until they first sign in. Billing on `companies` would be
  unreachable for exactly the customer who has just paid on the landing page.
  Same substitution for `credit_ledger.related_application_id`, which maps to
  `job_candidate_links`.
- **One credit is 60 integer SUB-UNITS, and nothing in the money path is a
  float.** Consumption is 1, 1/3, 1/15, 1/20 of a credit; LCM(1,3,15,20) = 60,
  so completed = 60, incomplete = 20, no-show = 4, old-profile review = 3.
  Division happens ONCE, at display, through `Decimal`.
- **The balance is `SUM(subunits_delta)`, never a stored counter.** A customer
  disputing usage gets a statement, not a number. `tenants.credit_deficit` is
  the one derived cache, and it exists only so the invitation gate does not
  re-aggregate the ledger on every send.
- **Every credit write carries a UNIQUE `idempotency_key`.** Razorpay delivers
  webhooks at least once and Celery redelivers tasks, so a double grant is the
  DEFAULT behaviour unless something prevents it. Checkout-verify and the
  webhook derive the SAME key from the payment id, which is why both can run for
  one payment and the customer is granted one month.
- **A completed assessment is charged even into the negative; the NEXT
  invitation is what gets blocked.** The work is already done and cannot be
  undone, so refusing the charge would only lose the revenue.
  `POST /pipeline/jobs/{id}/select-candidates` answers 402 with both ways out
  named.
- **Razorpay Subscriptions, never Orders.** An Order is a one-time charge and
  would silently turn a monthly plan into a single payment. The Checkout
  signature for a subscription is `payment_id|subscription_id`, the REVERSE of
  the Orders flow; getting it backwards fails 100% of real payments.
- **The Key Secret is server-side only and never reaches the frontend.** The
  browser gets the Key ID from `GET /billing/config` at runtime, not from a
  build-time `NEXT_PUBLIC_` variable, so the frontend container never needs the
  `.env` at all. `secrets/api-keys.txt` is gitignored and was never committed.
- **`checkout_ready` is about the SERVER's credentials, not the plan row.**
  Razorpay Plans are minted lazily on first subscribe, so keying it off
  `razorpay_plan_id` disables every Subscribe button on a fresh install and the
  only thing that could populate that column is the button it just disabled.
- **A job renewal restamps `posting_start_date`, and that is the ONLY thing
  that distinguishes an Old Profile.** `profile_age` is DERIVED from
  `link.created_at < job.posting_start_date`, never stored. Renewal is refused
  while a posting is still live, for the same reason publish refuses a second
  stamp. An Old Profile is ranked, listed, opened and assessed identically; the
  label is provenance and billing, never access.
- **Interactive LLM calls are capped at 15s per attempt and 30s in total;
  background ones are not.** The latency brief asks for a flat 10 to 15s cap on
  every call. Applied to `report_synthesis` that does not make the product
  faster, it makes every report fail and then retry. The split is by whether a
  request handler is blocked, and BOTH bounds are needed: four attempts at 15s
  is a 60-second request with a 15-second timeout on it.
- **No em dash in a STRING, in either language.** The 2026-07-28 sweep covered
  `frontend/` and the database; it did not cover backend Python, where 123
  em dashes sat in `detail=` messages, stage labels, profile-form options and
  seeded content. The same sweep covers every file added since. `tests/test_platform_audit.py` now asserts this, along with
  the DISC/MBTI/Hogan sweep, the no-OTP-in-any-portal rule, Gmail-SMTP-only, and
  no-numbers-to-a-client. A character class that MATCHES a dash is data, not
  prose: build it from `chr(8212)` so a repo-wide sweep cannot rewrite the code
  that strips it.

## Current hard rules, BD Portal, unified JD, procurement types (2026-07-28)

- **There are now FOUR portals, and the fourth is the Business Development
  Portal** (`/bd` in the UI and in the API). It is where ReadyPick's own sales
  team works leads and closes customers. The other three are unchanged:
  Provider Portal (`/admin`, `/provider`), Customer Portal (`/org`,
  `/companies`), Candidate Portal (`/portal`). A `bd` user is PLATFORM staff:
  `tenant_id` is NULL, the token carries the OWNER audience, and `bd` must
  never be added to `_ORG_ROLES` (that path demands a tenant they do not have).
- **A signed agreement CREATES a tenant, in a third `prospect` status.**
  `PATCH /bd/leads/{id}/agreement` with `true` mints a `tenants` row and links
  it, because a customer IS a tenant. Setting it back to false or null CLEARS
  the link and ARCHIVES the tenant, never deletes it, and
  `bd_leads.promoted_tenant_id` is permanent so a re-signed lead reuses its
  original company instead of minting a duplicate. The Provider Portal's
  customer list still accepts only `active | archived | all`, so a prospect
  cannot appear there as though it were live.
- **AI Reach returns two segments and the first one never touches the network.**
  `similar_to_customers` is computed from ReadyPick's own tenants and jobs and
  is computed FIRST; `from_internet` is a LangGraph agent over Tavily. With no
  `TAVILY_API_KEY` the internet segment returns `status: "unconfigured"` with a
  plain message and the page still works. Retrieved web content is DATA, never
  instructions, and the evaluate node says so explicitly.
- **`confidence_label` on an AI Reach card is a word, never a number.** High,
  Medium, Low. The no-numbers rule covers discovered jobs exactly as it covers
  candidate ratings.
- **A job description is ONE markdown document.** `jobs.jd_markdown` is
  canonical; the per-section columns are DERIVED from it and kept populated so
  nothing downstream breaks. The seven separate text boxes are gone from the
  Create Job form. The sequence is draft, then edit, then publish: publishing
  with an empty `jd_markdown` is refused.
- **`level` is superseded by an experience band.** `experience_min_years` and
  `experience_max_years`, with a Postgres CHECK that min never exceeds max.
  `level` survives only for jobs created before 2026-07-28 and is not collected
  on the form. `reportees` and the JD generator's `company_context` were
  DROPPED, not deprecated.
- **Every candidate link carries `source_type`: `applied | sourced |
  databank`.** Applied means they came through ReadyPick, sourced means a
  third-party link, databank means the recruitment team bulk-uploaded them.
  This is provenance for DISPLAY and filtering ONLY. Nothing may branch on it:
  all three are parsed, embedded, matched and assessed identically. Bulk upload
  is `POST /jobs/{id}/candidates/databank`, at most 25 files, partial success
  allowed so one unreadable PDF cannot discard the other 24, and parsing is a
  Celery task as always.
- **`shortlisted` stays in the FSM but is no longer OFFERED as a manual move.**
  It is the only route into `interview_scheduled` and `offer_extended`, it is
  written by `api/candidates.decide_profile`, and historic applications sit in
  it, so deleting it would strand them. Only its offer is withdrawn, via
  `hiring_pipeline.MANUAL_TRANSITION_EXCLUDED`. The UI renders
  `allowed_transition_options` from the server and hardcodes no stage list.
- **A BD account is reserved, never credentialed.** `POST /admin/bd-users`
  writes a `users` row with `role = 'bd'`, `tenant_id = NULL`, status
  `invited` and no `firebase_uid`; the first proven Firebase sign-in on that
  email binds the uid and flips it to `active`
  (`api/auth._finalize_single`). ReadyPick stores no password and sends no
  invite token for BD, so a Firebase identity must exist for the address
  before the first login. Disable is the reversible switch and there is no
  delete route: a BD rep owns leads (`bd_leads.owner_user_id`).
- **NO EM DASHES anywhere in the UI, INCLUDING IN DATA.** Not in labels, helper
  text, empty states, toasts, emails, page titles or generated JD text, and not
  in seeded or generated CONTENT either. Sweeping `frontend/` for U+2014 only
  covers what the code writes; `jobs.about_company`, `work_life`, `benefits`
  and `jd_json` render straight onto the public application page and broke the
  rule just as visibly (fixed in `0025_strip_em_dashes`). Check both the source
  tree AND the database.
- **Text is never grey, enforced at the TOKEN.** `globals.css` sets
  `--muted-foreground: var(--ink)` in both themes, so the shadcn primitives'
  built-in `text-muted-foreground` resolves to pure ink. Do not chase call
  sites in `components/ui/**`; fix the token if it ever drifts.
- **The brand is ReadyPick.** The code-native mark and wordmark live in
  `frontend/components/brand/logo.tsx`; product surfaces must not point at
  inherited logo or collateral assets. Design tokens remain in
  `docs/spec/DESIGN_BRIEF.md`.
- **Page metadata must not repeat the site name.** `app/layout.tsx` sets a
  `%s | ReadyPick` template, so a page title is just "Sign in".
- **The frontend dev container does not see file changes over the Windows bind
  mount.** Restart the `frontend` service after editing, or you will verify
  against stale output and believe a change did not work.

## Current hard rules — Provider Portal (2026-07-27)

- **Three portals, three names, never interchanged.** *Provider Portal* is the
  ReadyPick owner's console (`/admin` in the UI, `/provider` in the API).
  *Customer Portal* is a client company's own dashboard (`/org`,
  `/companies`). *Candidate Portal* is `/portal`. A **customer** is one
  onboarded client company.
- **A customer IS a `tenants` row, not a `companies` row.** `tenants` carries
  the customer identity (name, industry, profile) and exists from onboarding;
  `companies` is the client-AUTHORED candidate-facing page and does not exist
  until the client signs in. Compliance documents, the archive lifecycle and
  the Provider-editable metadata therefore hang off `tenants`.
- **The Provider is READ-ONLY over the customer's own data, enforced by
  ABSENCE.** `api/provider.py` exposes no route that writes a contact detail, a
  team member, or a compliance document — not a handler that checks a flag, no
  route at all. The Provider may edit exactly `industry`, `website_domain`,
  `notes` and the archive flag; `CustomerUpdateIn` has no other fields.
- **Archive is a reversible hide; delete is not on this screen.** Archiving
  sets `tenants.status` and stamps `archived_at`, touching no job, application,
  report or user; unarchiving CLEARS `archived_at`. The irreversible
  `DELETE /admin/tenants/{id}` still exists and still requires retyping the
  company name — it is never one click away from Edit.
- **All seven compliance slots are always returned, present or not.** Four tax
  records (GSTIN, PAN, TAN, bank details) then three commercial ones (signed
  agreement, PO, MSME), in that fixed order. An absent document is a slot with
  `document: null` rendering "Not Available Yet" — never a short list a missing
  PAN card can hide in. UNIQUE on (tenant, type): re-uploading REPLACES in
  place, keeping the document id stable.
- **`jobs_closed` and `jobs_ongoing` OVERLAP and are not a partition of
  `jobs_posted`.** Closed is `now > posting_end_date`; ongoing is
  `now <= grace_period_end_date`; a job in its 5-day grace tail is both. They
  are two independent questions — never render them as parts of a whole, and
  never "fix" them to sum. Boundaries stay inclusive at the end of each window
  (rule 8), matching `services/job_posting`.
- **Customer search, the archived filter and pagination run in SQL**
  (`api/provider.list_customers`), before pagination. Filtering a fetched page
  in the browser makes the match count depend on which page was loaded.
- **The Provider Portal nav is Customers + Business Development + Billing +
  Settings, nothing else** (amended twice on 2026-07-28). Team Management,
  Permissions and the Audit Log still have no page. Billing is READ-ONLY like
  every other Provider view of customer data, and read-only by ABSENCE: there
  is no route in `api/billing` that lets the Provider write a subscription, a
  plan or a credit. Business Development is not a fourth
  cross-tenant admin surface: it is the ONLY place a `bd` account can be
  created, because every invite path in the product is tenant-scoped and a BD
  user has no tenant. The audit trail is
  still written for every Provider request (`get_superadmin_db`); it simply has
  no UI. Settings stays because the theme toggle lives there (rule 10).
- **`manage_compliance_documents` remains independently grantable.** A
  GSTIN certificate and a signed agreement are the company's legal instruments,
  not recruitment data. Still a capability, never a role branch; an authorised
  manager can delegate it via `users.permissions_json`.

## Current hard rules — Job posting lifecycle + hiring pipeline (2026-07-27)

- **Every job is live for EXACTLY 30 days, then 5 days of grace.** The window
  is not configurable and a recruiter can never move it: `jobs.posting_end_date`
  and `grace_period_end_date` are Postgres GENERATED columns, so an UPDATE
  against them is rejected by the database itself. `posting_start_date` is
  stamped at publish and is the only writable date.
- **`posting_status` and `is_within_grace_period` are READ-TIME values, never
  stored.** The spec asks for them as generated columns; Postgres refuses,
  because their definitions call `now()` (and one subqueries another table) and
  a generated column must be IMMUTABLE. They live in
  `services/job_posting.py`, mirrored by the `job_posting_state` SQL view. The
  two must agree — change them together.
- **Visibility rules are in `services/job_posting.py` and are pure functions.**
  A wrong boundary there silently grants or removes a person's access, so every
  boundary is asserted directly in `tests/test_job_posting.py`. Boundaries are
  INCLUSIVE at the end of each window: an instant exactly on `posting_end_date`
  is still active, ties go to the candidate (consistent with rule 8).
- **A candidate who registered after `posting_end_date` never sees that job** —
  not in the board, not in search, not by direct URL. The window filter is
  applied BEFORE relevance ranking and before `?search=`, because search
  deliberately bypasses relevance and would otherwise bypass this too.
- **The grace period is for EDITING an existing application, never for creating
  one.** It grants nothing to a non-applicant and nothing to an anonymous
  visitor: the public/external job link 404s the moment the 30 days end.
- **Not every applicant is assessed.** All applicants are ranked on resume +
  profile form, but only candidates a recruiter selects
  (`POST /pipeline/jobs/{id}/select-candidates`) get an assessment — and
  therefore a PFI report. The `assessment_conversations` row IS the invitation;
  `POST /assessments/conversations/links/{id}/start` refuses without one, so an
  uninvited candidate cannot reach the questions by guessing a URL.
- **Application status is a validated 10-stage pipeline**
  (`services/hiring_pipeline.py`). Illegal moves are refused — an application
  cannot jump from `applied` to `offer_extended`, because each stage carries a
  promise (`assessment_completed` means a report exists) and the transition
  emails reference it. `rejected` and `hold` are reachable from any live stage.
  `pipeline_status` stays the append-only history; `job_candidate_links.status`
  is a denormalised mirror, and only `apply_transition` writes either.

## Current hard rules — Job detail page + LangGraph router (2026-07-27)

- **NO NUMBERS REACH A CLIENT. EVER.** Not a score, percentage, rank, band
  index, "7.5/10", or "top 12%" — in the UI, in an API response, or in an
  email. SUPERSEDED IN PART, 2026-07-30: rated output is now the FOUR grades of
  `services/rating.py`, not two parallel five-label scales. The conversion from
  the internal score still happens SERVER-SIDE so a number cannot leak by
  omission. The single, documented exception is the radar chart's band index
  (now 1–4), which is a rendering coordinate — a radar has no geometry without
  a radius — and is never displayed as a number anywhere.
- **Every LLM call routes through `services/llm_router.invoke_llm(task_type,
  …)`.** Task types are `jd_generation | technical_questions |
  behavioral_assessment | report_synthesis | email_composition`, plus the
  legacy `rerank | extraction` hints. Routing policy is DATA in
  `config/llm_providers.py` (provider order, timeout, retry budget per task) —
  never inline in a service. The key roster is 7 slots per provider (21 total),
  every slot optional; the router round-robins within a provider tier and
  walks the tier order on failure. A LangGraph `StateGraph` drives the retry
  loop; the circuit breaker, half-open recovery, and never-log-a-key rules are
  unchanged.
- **Candidates are listed INLINE on the job detail page.** There is no separate
  Review Screen, no Email Templates builder, and no separate JD-edits card.
  Columns are Name | Level | PPI Report | Resume | the rated comments (and
  Decision, when the caller holds `decide_profile`). The job page also carries
  the assessment-setup review (`components/job-setup-review.tsx`), which is the
  one manual step in the pipeline.
- **The candidate table is sorted in SQL, never in JavaScript.** Order is
  grade-driven (`services/job_candidates.order_by_clause`): non-managerial is
  skills → experience → behavioural; managerial and above is skills →
  behavioural → experience. It must stay a TOTAL order (trailing
  `created_at, id`) or paginated rows will duplicate or vanish. 25 per page.
- **About Company / Work Life / Benefits live in two layers.** The company
  profile (Company Portal → Profile) is the default; a job SNAPSHOTS it at
  creation and may override it per job. Editing the company profile reaches
  FUTURE jobs only — never a job candidates are already applying to. A NULL
  section on a job reads through to the live company profile.
- **Reports are immutable.** No edit or delete affordance in the UI, and
  PATCH/PUT/DELETE on the report route return 403 explicitly (a registered
  handler, not an accidental 405). A retake generates a NEW report alongside
  the old one. This is also why a saved PPI framework cannot be reopened once
  anyone has been assessed against it.
- ~~**Six-month retake rule**~~ REUSE RETIRED 2026-07-30 (`services/retake`):
  every application runs its own assessment, because under PPI the framework and
  the technical bank both come from the job's own JD and nothing in a report is
  portable any more. `PORTABLE_CATEGORIES` is an explicit empty frozenset. The
  183-day classification still runs so the candidate is told why they are
  answering questions again.
- **All six lifecycle emails are AI-drafted and editable before sending.**
  Prompts are `.txt` files in `app/prompts/`; every send is recorded in
  `email_log` with the copy actually sent and whether a human edited it.
  Delivery is a Celery task over Gmail SMTP. An email never contains a score.
- **Permissions gain a per-user layer.** Resolution is user overlay → tenant
  row → global template → deny. `users.permissions_json` is a SPARSE
  {capability: bool} object: a capability the HR Head never pinned keeps
  tracking its role default. Still `require_capability(...)`, never a role
  branch.

## Hard rules — Unified candidate profile release (2026-07-27)

- ~~**The 40 validation aspects are a FORM on the candidate profile.**~~
  SUPERSEDED 2026-07-30: validation is six mandatory fields on the APPLICATION
  form (see the top of this file). `candidate_profile_form.py` survives as the
  candidate's own reusable profile, but it is no longer where the report's
  Validation section reads from. Original rule, for context: A candidate's answers are identical for every job, so
  they are collected once under My Profile and snapshotted onto each
  application's `profiles.aspects_json`. `services/candidate_profile_form.py` is
  the single source of truth for that form — a fixed Python constant, never
  LLM-generated and never client-editable, exactly like `pfi_bank.py`. The
  report's Validation section reads the snapshot; `validation_bank.py` survives
  only to keep pre-2026-07-27 transcripts readable.
- **A per-job assessment is technical (by grade) + PPI (by grade), and nothing
  else.** SUPERSEDED 2026-07-30: the behavioural half is PPI and its count now
  varies by grade, so non-managerial is 45 questions rather than 40.
- **The candidate has a MAIN resume** (`candidates.main_profile_id`), managed on
  My Profile and offered on every application beside "upload a new resume".
  Replacing it never rewrites a submitted application — each application remains
  an immutable snapshot of the resume it was actually sent with.
- **The candidate's New Jobs board shows RELEVANT roles only**, ranked by
  `services/job_relevance.py` against their main resume, its parsed skills, and
  their profile form. `?search=` deliberately bypasses relevance entirely. This
  is candidate-side presentation ONLY — it must never decide who gets scored.
- **Text is never grey.** Every text token resolves to pure black in the light
  theme and pure white in the dark theme; grey survives only on borders, input
  outlines and muted backgrounds. The single exception is `::placeholder`, dimmed
  so an empty field cannot be mistaken for a filled one.
- The candidate portal's nav is **New Jobs → Applied Jobs → My Profile**. There
  is no "Settings" page for candidates, and their role is never displayed.

## Hard rules — Grade-driven assessment release (2026-07-26)

- Evolve the system additively. Extend tables and routes; do not replace
  established contracts without a migration and versioned compatibility path.
- The ReadyPick Functional Index is proprietary ReadyPick work derived from
  first-principles job analysis. Never associate its name, prompts, code,
  comments, UI, or documentation with a third-party licensed assessment
  instrument.
- Client-facing rated output uses only these labels: Very High, High, Medium,
  Low, Developing. Stored numeric scores are internal ranking data and must
  never be returned by report APIs or rendered in the client UI.
- Rated remarks are 25–30 words and overall summaries are 45–50 words. AMENDED
  2026-07-30: the 25–30 rule now covers only the AI Score's four matching
  parameters and technical items; every Primary Skill, Secondary Skill,
  Behavioural Competency and the Overall Remark is 45–50 words. Validate
  and regenerate complete prose; never truncate a sentence to hit a limit.
- A candidate experiences one unified conversation. Technical, Behavioral, and
  Validation scoring fan out in parallel; synthesis is an explicit join.
  (SUPERSEDED IN PART, 2026-07-27: validation is no longer *asked* in the
  conversation — the three scorers still fan out in parallel, but
  `validation_capture` now reads the candidate's profile form.)
- Gmail SMTP is the only outbound email path. Authentication is email/password
  or Google OAuth; no OTP UI or copy is permitted.

### Grade drives the assessment (2026-07-26)

- **Every job carries a grade**: `non_managerial | managerial | leadership |
  cxo`. It is a REQUIRED dropdown on the Create Job form, stored in the existing
  `jobs.assessment_grade` column and exposed on every job read as `grade`. It is
  never null — legacy rows read `non_managerial`. Grade is chosen by the
  recruiter, not inferred; LLM inference survives only as a fallback for rows
  created before this release.
- **Question counts are fixed by grade.** Technical: non-managerial 20,
  managerial 17, leadership 15, CXO 12 — unchanged. ~~Behavioural: always 20 (5
  grade-specific PFI dimensions × 4 fixed questions).~~ SUPERSEDED 2026-07-30:
  the behavioural half is now PPI and its count varies by grade — 25 / 20 / 15 /
  10 — so a non-managerial candidate answers 45 questions and a CXO 22.
  ~~Validation: always all 40 aspects.~~ Validation left the conversation on
  2026-07-27 for the profile form, and left the profile form on 2026-07-30 for
  six mandatory fields on the application form.
- ~~**There is no manual question-bank approval step, and no question-bank
  UI.**~~ REVERSED 2026-07-30, client decision. The gate is back and now covers
  the PPI framework as well as the technical bank; see the 2026-07-30 section
  at the top of this file. Recruiters review, edit and finalise both, and no
  candidate can be invited until they have.
- ~~**Behavioural questions and the profile form are fixed Python
  constants**~~ SUPERSEDED 2026-07-30 for the behavioural half:
  `services/pfi_bank.py` and `services/validation_bank.py` are deleted, and the
  behavioural competencies are now part of the per-job PPI framework generated
  from the JD. `services/candidate_profile_form.py` survives unchanged and is
  still a fixed constant, never LLM-generated and never client-editable.
- **Scoring reads the candidate's actual answers.** Each technical answer is
  scored against that question's own rubric; each PFI dimension is scored from
  its four answers. A deterministic hash is permitted ONLY as a flagged
  LLM-outage fallback and must set `scoring_mode`.
- **A technical report dimension is named after a skill, never a JD sentence.**
  `report_dimensions` is UNIQUE on (report_id, category, name), so the report
  carries one entry per distinct skill probed — not one per question.
- **A candidate linked to a job is always scored.** Retrieval (pgvector, ts_rank)
  is a ranking prior only; it must never decide who gets scored. Every
  non-archived link on the job enters the scoring pool.
- ~~**Report section order is fixed**: overall summary → radar chart → Profile
  Matching → Behavioural (PFI) → Technical → Validation → Suggested Interview
  Probes.~~ SUPERSEDED 2026-07-30, see the report order at the top of this
  file: AI Score → Overall → Primary Skills → Secondary Skills → Behavioural
  Competencies → Validation → Suggested interview questions.
- **Never name a storage vendor in user-facing copy.** Candidates are told the
  file limits, not where the bytes land.

This file is the standing context for any Codex session working on this repo. Read `PRD.md` for functional requirements and `ESD.md` for the architecture — this file is *how* to build it, not *what* to build.

---

## 1. Project One-Liner

ReadyPick is a multi-tenant recruitment/ATS platform for Hanulisa Technologies LLP. Next.js + FastAPI, Firebase auth for every role, Postgres+pgvector for data and matching, a grade-driven AI assessment producing the Functional Skills Report, Celery for all async work, fully Dockerized.

---

## 2. Repository Layout

```
/frontend                  Next.js 16 (App Router), TypeScript, shadcn/ui
  /app                     routes grouped by audience:
                             (public)      landing, docs, about, legal
                             (auth)        login, register, join
                             (org)         Customer Portal
                             (candidate)   Candidate Portal
                             (super-admin) Provider Portal
                             (bd)          Business Development Portal
  /components              shared UI; shadcn primitives in /components/ui
  /lib                     api client, auth helpers, types, theme provider
  /scripts                 impeccable-gate.mjs, contrast checks
/analysis-service          the proctoring analysis service (speaker
                           diarization, the flagged AI-text detector): its own
                           image, its own tests, one secret
/backend
  /app
    /api                   FastAPI routers, one module per PRD section
    /config                llm_providers.py, the model policy as DATA
    /models                SQLAlchemy models mirroring ESD section 4
    /schemas               Pydantic request/response models
    /services              domain logic; see the package map below
    /prompts               versioned prompt files + registry.py
    /workers               dispatch, registry, runtime, schedule, tasks,
                           and entrypoints/ (the Lambda and Fargate doors)
    /core                  config, security, db session with the RLS setter
    /scripts               seeds, evals, legacy_reset, verify_live
  /alembic/versions        migrations, 0001 to 0079
  /tests                   151 test modules
/lambda                    the one zip-packaged function: assessment_trigger
/infra                     Terraform modules + docker-compose.yml (local dev)
                           environments/pilot is the canonical composition
/scripts                   test.sh, deploy helpers, smoke tests
/docs                      ALL documentation. Start at docs/README.md
```

The `services/` packages worth knowing before adding one:

```
services/hiring/     Bodha + Sutra: SWOT, Tatva scorecard, Drishti, layers,
                     transformation, gates, prescreen, runbook_data/
services/miti/       the five isolated dimension evaluators + triangulation
services/siddhi/     PRISM composition behind the citation chokepoint
services/projects/   Project Evidence Intelligence, end to end
services/evidence/   the shared evidence ledger, tiers, contradictions
services/rag/        retrieval: chunking, fusion, rerank
services/agents/     tools, permissions, the agent loop
services/proctoring/ the event catalog, the server-side warning machine,
                     behavioural evaluation, the report; imported by no scorer
services/assessment_formats/
                     the six question formats, composition, objective scoring,
                     AI evaluation with reasoning
```

---

## 3. Non-Negotiable Rules

These are architectural decisions already made in ESD.md — do not silently deviate from them or re-litigate them in code review:

1. **Every tenant-scoped query goes through the RLS-aware session.** Never hand-write a `WHERE tenant_id = ...` filter as the *only* protection — the Postgres RLS policy is the real boundary; app-level filtering is defense in depth, not a substitute.
2. **Authentication is Firebase (as of 2026-07-24).** All roles sign in via Firebase Auth — Google, email/password, and phone. The backend verifies the Firebase ID token (`services/firebase_auth.py`) and issues the app's own portal-scoped JWT cookies; database roles/permissions remain authoritative (Firebase is identity only, never authorization). **Exception to the original "no passwords" rule:** candidate email/password is explicitly allowed (user decision, 2026-07-24). Do NOT build a custom password store or "forgot password" flow — Firebase owns credentials and recovery. The legacy MSG91 OTP send-path is retained as a working SMS feature but is no longer the login mechanism.
3. **Permissions are data, not code, and staff are hierarchical (reversed 2026-08-14).** Super Admin -> Recruitment Manager -> Recruiter -> Hiring Manager. Managers control only roles below them and may grant only capabilities they hold. Keep using `require_capability("...")` backed by `role_permissions` and the per-user overlay; never hardcode operational access by role in jobs, pipeline or candidates.
4. **All async/slow work is DISPATCHED**, never inline in a request handler: matching/re-ranking, email/SMS sending, resume parsing, verification-reply parsing, dashboard aggregation. SUPERSEDED IN PART 2026-09-05: the transport is `app/workers/dispatch.dispatch`, not Celery, which is deleted. The rule that slow work never runs in a request handler is unchanged.
5. **All outbound email goes through Gmail SMTP from the backend.** Configure `smtp.gmail.com:587` with STARTTLS, the Gmail address, and a Google App Password via `SMTP_*`. The authenticated Gmail mailbox is always the From address. Sending remains a Celery task with database audit records and permanent-vs-transient failure handling.
6. **Candidate resumes ARE persisted on the candidate profile and reused across applications (as of 2026-07-24, PRD v1.0 FR-6.2).** Store the uploaded resume on the candidate's profile; on a new application, offer to reuse the last resume or upload a fresh one. (This reverses the earlier fresh-upload-only rule.)
7. **Databank candidates never re-enter the verification/40-aspect flow** — their existing Profile is reused as-is. Only freshly sourced candidates go through Section 5's data-collection + verification steps.
8. **Tier boundaries are inclusive upward**: a score of exactly 90 is Highly Matching, not Moderately Matching. Implement tier assignment top-down (check ≥90 first).
9. **LLM keys are routed with fallback, never hardcoded to a single provider.** Use the `llm_provider_keys` table and the router service (ESD §8.4); mark a key unhealthy on repeated failure rather than crashing the calling task.
10. **The theme toggle lives only in Settings/Profile** — never in the main navbar or a persistent floating control.

---

## 4. Coding Conventions

- **Backend**: Python 3.12, FastAPI, async everywhere (`async def` route handlers, `asyncpg`/`SQLAlchemy` async engine). Pydantic v2 for all request/response schemas — no bare dicts crossing the API boundary.
- **Frontend**: TypeScript strict mode on. Server Components by default; `"use client"` only where interactivity requires it. shadcn/ui components live under `/components/ui` and are not hand-edited beyond the CLI-generated output — wrap/compose instead of modifying generated files.
- **Styling**: Tailwind, monochrome palette (CSS variables for the black/white theme pair so the toggle is a variable swap, not a component-level branch).
- **Migrations**: every schema change is an Alembic migration, checked in — no manual production schema edits.
- **Tests**: Pytest for backend (unit tests on the approval FSM, RBAC engine, tier-boundary logic are mandatory given how much of the product depends on getting these exactly right); Playwright or React Testing Library for frontend critical flows (OTP login, job approval chain, HR review screen).
- **Commits**: Conventional Commits style (`feat:`, `fix:`, `chore:`, `refactor:`) to keep the history usable for a changelog later.

---

## 5. Environment Variables

**`/.env.example` is the single source of truth — read it, do not trust a copy.**
A duplicated list here drifts: this section previously still advertised
`RESEND_API_KEY` (email moved to Gmail SMTP) and a 9-key LLM roster (now 21).

Notes that are not obvious from the file itself:

- **LLM keys**: 7 slots per provider (`GROQ_API_KEY_1..7`, `GEMINI_API_KEY_1..7`,
  `OPENROUTER_API_KEY_1..7`). Every slot is OPTIONAL — the router enumerates
  only populated ones, so three keys and twenty-one behave identically. The
  `llm_provider_keys` table takes precedence over env when it has rows.
- **`TASK_DISPATCH_BACKEND`** selects where background work runs: `aws`
  (Lambda and on-demand Fargate), `local` (a thread in this process, for
  compose) or `record` (accept and remember, for the test suite; refused in
  production). See `app/workers/dispatch.py`.
- **Email is Gmail SMTP only** (`SMTP_*`): `smtp.gmail.com:587` with STARTTLS
  and a Google App Password. The authenticated mailbox is always the From
  address. There is no Resend/Mailtrap path.
- **OTP settings remain** for the retained SMS feature; they are no longer the
  login mechanism (Firebase owns authentication).

---

## 6. Local Dev Quick Start

```bash
git clone <repo>
cd pickready
cp .env.example .env          # fill in real keys before first run
docker compose -f infra/docker-compose.yml up --build
# frontend: http://localhost:3000
# backend:  http://localhost:8000/docs (FastAPI auto-docs)
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.scripts.seed_dev_data
```

---

## 7. Where to make a change

The build order that used to sit here described a product that has been built,
and every one of its eleven steps had been superseded (OTP auth became
Firebase, BGE-M3 became `voyage-4`, the 40-aspect flow became the profile form
and then the application's validation fields). Replaced with the lookup a
change actually needs.

| Adding or changing... | Touch | And do not forget |
|---|---|---|
| An API route | `app/api/<section>.py` | `require_capability(...)`, never a role branch |
| A capability | `services/capabilities.py` | **A seeding migration too**, plus `tests/test_capability_seed_parity.py` |
| A table | `app/models/`, `alembic/versions/` | RLS policy + grant; export it from `models/__init__.py` |
| A background task | `workers/tasks.py` | `@task(name=..., route=...)`, and a route is a COST decision: seconds go to `Route.LAMBDA`, minutes to `Route.ECS` |
| A scheduled sweep | `workers/schedule.py` AND every environment's `module "scheduler"` | `tests/test_schedule_parity.py` compares them; one without the other is a sweep that never runs |
| An LLM call | `config/llm_providers.py` first | Task type, timeout, budget, temperature, max tokens, retry budget |
| A prompt | `app/prompts/*.txt` | Bump `# version:`; the registry digests the body |
| A scoring rule | `services/hiring/runbook_data/*.yaml` | Cite the Runbook section; parity test enforces it |
| A client-facing string | The renderer | No number, no em dash, correct Tatva/PRISM naming |
| A candidate-facing upload | The relevant storage service | Validate, never execute, bound every ceiling in config |
| A frontend surface | `frontend/app/(group)/` | `DESIGN.md` tokens; navy is structure, teal is evidence |
| A workflow gate | `docs/spec/HIRING_WORKFLOW.md` first | The gate is a real check with tests, never a paragraph; name where it lives |
| A candidate-facing notification | `services/candidate_updates.py` | A fixed catalogue entry, never a prompt; no number, no grade, relative link only |
| A pipeline stage | `services/hiring_pipeline.py` | The CHECK constraint, `PipelineStatus`, the dashboard mapping, and the frontend union |
| A proctoring threshold | `core/config.py` (`proctoring_*`) | It is served to the browser from `services/proctoring/config.py`; no literal in the pipeline |
| A proctoring event type | `services/proctoring/catalog.py` | Its path (A, B, C), its group, its phrasing in `phrasing.py`; internal identifiers never reach a recruiter |
| A question format | `services/assessment_formats/types.py` | The payload model, the answer model, `candidate_view`, the migration CHECK, the frontend component behind the one dispatcher |
| A retrieval change | `services/rag/` | `tests/test_retrieval_tenant_recall.py` measures RECALL against a known set, never "rows came back"; a degraded reranker is RECORDED |
| A judge or eval change | `app/evaluation/` ONLY | Never `app/services/`; `tests/test_judge_isolation.py` asserts it by AST, and the judge reports MCC, kappa and its protocol or reports nothing |
| A tool capability | `services/tools/permissions.py` + `policy.py` | Risk class stays a Python constant; only tenant approval is data; the refusal runs BEFORE the handler |
| An agent action with a side effect | `services/agent_actions/` | A ledger row BEFORE the call, an idempotency key from stable logical inputs, and UNKNOWN resolved by reading back |
| A generation prompt | `app/prompts/*.txt` + `services/generation_sufficiency.py` | Sufficiency is decided deterministically FIRST; the prompt carries good, bad and edge-case examples; bump `# version:` |
| An AI activity message | `services/activity/phrasing.py` | A fixed catalogue keyed by (task, event), never a timer, never a number the workflow did not compute |

### Before you claim it works

- A green pipeline means the service answers HTTP. Verify against the thing a
  user touches: a row count, an actual API response, a grep of the DEPLOYED
  image. Never against the source tree.
- `pytest tests/test_ai_reachability.py` answers "is the framework actually
  reachable", transitively and in both directions. **It replaces the grep this
  line used to recommend** (`grep -rn "hiring\.\|miti\.\|siddhi\."
  backend/app/api backend/app/workers`), which could not match an import
  statement at all and never looked past one hop. See the 2026-08-29 section
  above: that grep returns two comment hits today, against a framework that is
  live.
- A package being IMPORTABLE is not the same as it being EXERCISED, and the
  test keeps the two apart. `services/rag` is reachable from a route and has
  never run once.
- Run `./scripts/test.sh` (fresh database, flushed cache) rather than pytest
  against a reused one. A suite that only passes on a warm database is telling
  you something.

---

## 8. When Unsure

If a requirement in PRD.md is ambiguous and the ESD doesn't resolve it, don't guess silently — implement the most defensible interpretation, leave a clear `# ASSUMPTION:` comment at the point of implementation, and surface it back to the user rather than letting it drift into an undocumented behavior.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
