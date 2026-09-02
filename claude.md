# claude.md, ReadyPick Build Conventions

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
   of four words happens server-side at the serializer.
2. **Permissions are data, never a role branch.** `require_capability(...)`,
   and a new capability constant is only HALF a change -- the seeding migration
   is the other half.
3. **Every tenant-scoped query goes through the RLS-aware session.**
4. **All slow work is a Celery task**, never inline in a request handler.
5. **One implementation per concept.** No dual code paths for one behaviour.
6. **No silent fallbacks.** No bare `except`, no default substituted for a
   failed retrieval, no template output presented as generation.
7. **No em dash anywhere**, including in seeded and generated content.
8. **A timestamp is not evidence that work happened.** Check the table.


## Current hard rules, documentation layout (2026-09-01)

- **ALL documentation lives under `docs/`, indexed by
  [docs/README.md](docs/README.md).** Five markdown files stay at the
  repository root and only these five: `README.md`, `claude.md`,
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

- **`docs/product/Readypick Hiring Philosophy.md`** (RPN-PHIL-001, now **v1.1**).
  It sat at the repository root until the 2026-09-01 documentation
  consolidation. Note the filename uses SPACES; every document writes it with
  underscores. Three call sites resolve this path on disk, so moving it again
  means changing them: `services/hiring/dna_compilation.RUNBOOK_MARKDOWN`,
  `tests/test_runbook_parity.RUNBOOK_GLOB` and
  `tests/test_runbook_reconciliation.RUNBOOK_PATH`. It was absent for the whole
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
CLAUDE.md called two-before-escalation a hard rule. `situations.py` had 4 of 6
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

That grep now returns hits in `api/assessments.py`, `api/jobs.py`,
`api/dashboard.py`, `api/company_dna.py` and `workers/tasks.py`. Job setup runs
Bodha's SWOT and Sutra's seven stages and freezes a matrix behind G1; Yukti
grades a resume on the evidence model and the ontology; Miti's five isolated
evaluators score live with a model-free aggregator; Siddhi composes the PRISM
report through a citation chokepoint with no bypass parameter. The old
single-pass generators are DELETED, not flagged off.

**Keep that grep as the check.** It is the cheapest honest answer to "is the
framework actually reachable", and it is the one that was quietly false for a
whole phase while every module was green in isolation.

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
`claude-*` is now forbidden in executable source where it previously had two
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
  accepted. One detector, `company_dna.is_observable`, used by both the DNA
  instrument and the SWOT quality rules -- two copies would drift invisibly.
- **A disqualifier is matched on WORD BOUNDARIES and includes numeric age
  bars.** The first version matched substrings and refused "Must hold a valid CA
  licence" because "hold" contains "old", while accepting "No candidates over
  45" because it contains no listed word. A false positive is not harmless: it
  tells a client their lawful professional requirement is discriminatory, which
  destroys their trust in every refusal that follows.
- **Compilation is deterministic and calls no model.** A Company DNA artifact
  constrains every job that client will ever post, so it must be reproducible,
  diffable between versions, and explainable without a provider.
- **Sutra reads the COMPILED artifact, never the client's free-text.** An
  unbounded client-authored string in a prompt that decides what every candidate
  is graded on is both an injection surface and a way for "we like people who
  are hungry" to become a criterion.
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

This file is the standing context for any Claude Code session working on this repo. Read `PRD.md` for functional requirements and `ESD.md` for the architecture — this file is *how* to build it, not *what* to build.

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
/backend
  /app
    /api                   FastAPI routers, one module per PRD section
    /config                llm_providers.py, the model policy as DATA
    /models                SQLAlchemy models mirroring ESD section 4
    /schemas               Pydantic request/response models
    /services              domain logic; see the package map below
    /prompts               versioned prompt files + registry.py
    /workers               celery_app.py (schedule) and tasks.py
    /core                  config, security, db session with the RLS setter
    /scripts               seeds, evals, legacy_reset, verify_live
  /alembic/versions        migrations, 0001 to 0075
  /tests                   151 test modules
/infra                     Terraform modules + docker-compose.yml (local dev)
/scripts                   test.sh, deploy helpers, smoke tests
/docs                      ALL documentation. Start at docs/README.md
```

The `services/` packages worth knowing before adding one:

```
services/hiring/     Bodha + Sutra: SWOT, Company DNA, scorecard, layers,
                     transformation, gates, prescreen, runbook_data/
services/miti/       the five isolated dimension evaluators + triangulation
services/siddhi/     PRISM composition behind the citation chokepoint
services/projects/   Project Evidence Intelligence, end to end
services/evidence/   the shared evidence ledger, tiers, contradictions
services/rag/        retrieval: chunking, fusion, rerank
services/agents/     tools, permissions, the agent loop
```

---

## 3. Non-Negotiable Rules

These are architectural decisions already made in ESD.md — do not silently deviate from them or re-litigate them in code review:

1. **Every tenant-scoped query goes through the RLS-aware session.** Never hand-write a `WHERE tenant_id = ...` filter as the *only* protection — the Postgres RLS policy is the real boundary; app-level filtering is defense in depth, not a substitute.
2. **Authentication is Firebase (as of 2026-07-24).** All roles sign in via Firebase Auth — Google, email/password, and phone. The backend verifies the Firebase ID token (`services/firebase_auth.py`) and issues the app's own portal-scoped JWT cookies; database roles/permissions remain authoritative (Firebase is identity only, never authorization). **Exception to the original "no passwords" rule:** candidate email/password is explicitly allowed (user decision, 2026-07-24). Do NOT build a custom password store or "forgot password" flow — Firebase owns credentials and recovery. The legacy MSG91 OTP send-path is retained as a working SMS feature but is no longer the login mechanism.
3. **Permissions are data, not code, and staff are hierarchical (reversed 2026-08-14).** Super Admin -> Recruitment Manager -> Recruiter -> Hiring Manager. Managers control only roles below them and may grant only capabilities they hold. Keep using `require_capability("...")` backed by `role_permissions` and the per-user overlay; never hardcode operational access by role in jobs, pipeline or candidates.
4. **All async/slow work is a Celery task**, never inline in a request handler: matching/re-ranking, email/SMS sending, resume parsing, verification-reply parsing, dashboard aggregation.
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
| A Celery task | `workers/tasks.py`, `celery_app.py` | Name contract, idempotency, and the worker does not autoreload |
| An LLM call | `config/llm_providers.py` first | Task type, timeout, budget, temperature, max tokens, retry budget |
| A prompt | `app/prompts/*.txt` | Bump `# version:`; the registry digests the body |
| A scoring rule | `services/hiring/runbook_data/*.yaml` | Cite the Runbook section; parity test enforces it |
| A client-facing string | The renderer | No number, no em dash, correct Tatva/PRISM naming |
| A candidate-facing upload | The relevant storage service | Validate, never execute, bound every ceiling in config |
| A frontend surface | `frontend/app/(group)/` | `DESIGN.md` tokens; navy is structure, teal is evidence |

### Before you claim it works

- A green pipeline means the service answers HTTP. Verify against the thing a
  user touches: a row count, an actual API response, a grep of the DEPLOYED
  image. Never against the source tree.
- `grep -rn "hiring\.\|miti\.\|siddhi\." backend/app/api backend/app/workers`
  is the cheapest honest answer to "is the framework actually reachable". It
  returned nothing for a whole phase while every module was green in isolation.
- Run `./scripts/test.sh` (fresh database, flushed cache) rather than pytest
  against a reused one. A suite that only passes on a warm database is telling
  you something.

---

## 8. When Unsure

If a requirement in PRD.md is ambiguous and the ESD doesn't resolve it, don't guess silently — implement the most defensible interpretation, leave a clear `# ASSUMPTION:` comment at the point of implementation, and surface it back to the user rather than letting it drift into an undocumented behavior.
