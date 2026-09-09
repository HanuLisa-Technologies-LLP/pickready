# AI upgrade baseline, RPN-AI-UP-001 W0

**Taken 2026-09-09 against commit `6f696a8ee93ac3ee6ad44380c63bfb0c4e423715`
(`6f696a8`), and against the `readypick-pilot` database in `ap-south-2`.**

This is the file RPN-AI-UP-001 W0 requires before the first line of new code.
Every claim of improvement in that programme is measured against the numbers
below. It is a **measurement, not a description**: it records what was true on
the day it was taken and must not be edited later to match new behaviour.

Reproduce it with:

```bash
./scripts/pilot-baseline.sh pilot
cd backend && python -m pytest tests/test_ai_reachability.py
```

---

## 1. The headline: three findings, and two of them contradict the brief

### Finding 1. Miti and Siddhi are ALREADY on the live path

RPN-AI-UP-001 section 1.1 states that `services/miti` and `services/siddhi` are
imported by no route or worker, calls the whole of Part A "an expensive unit
test", and builds W1 as a wiring workstream on that basis. **That is wrong, and
the error is in the measurement rather than in the tree.**

The audit command it prescribes greps `app/api` and `app/workers` only, so it
sees depth zero. Miti and Siddhi are at depth two:

```
app.api.assessments -> app.services.functional_assessment -> app.services.miti
app.api.assessments -> app.services.report_pdf            -> app.services.siddhi.numbers
```

and on the worker side, `workers/tasks.py:1017` registers
`pickready.run_functional_assessment`, which calls
`functional_assessment.run_assessment`, whose `synthesis_node` calls
`miti.live.evaluate_application` and raises `ScorecardUnavailable` when Miti
produces no aggregate. That module carries a comment dating the wiring:

> `── MITI, STAGES 2 TO 6, ON THE LIVE PATH ──` ... "Until 2026-08-29 the whole
> of that stack was reachable only from `app/scripts/worked_example.py`, so
> gates G1 to G4 were real checks guarding nothing."

A second reason the grep could not see it is deliberate: `from
app.services.miti import live` is written **inside** the function to break a
documented import cycle, so it is invisible to any check that reads only
module-level imports.

**Consequence for the programme.** W1.1 and W1.2 are already done. W0.1's three
candidate answers (reverted / never merged / written ahead of the code) all
miss; the answer is a fourth, *the wiring landed and the audit method could not
see it*. `claude.md`'s spec-doc6 reachability paragraph is therefore correct
about `hiring`, and correct in substance about Miti and Siddhi, and the
statement in RPN-AI-UP-001 that it is false is itself the false statement.

`tests/test_ai_reachability.py` replaces the grep with a transitive walk of the
real import graph, so this class of error cannot recur.

### Finding 2. The RAG index is genuinely dead, and this half of the brief holds

`services/rag/index.index_document` is the only writer of `context_chunks`, and
**nothing calls it**: not a route, not a task, not a schedule entry. There is no
`pickready.index_*` task among the 31 registered. Confirmed at source level and
confirmed against production, where `context_chunks` holds zero rows.

`rag` *is* importable from a route (`app.api.admin -> services.rbac ->
services.tools -> services.tools.implementations -> services.rag.retrieval`),
because `tools.implementations` defines a handler over it. **Importable is not
exercised**, and the distinction is the whole of W2. It is recorded in
`tests/test_ai_reachability.py` as `IMPORTED_BUT_NOT_EXERCISED`, with the sharp
form of the claim in `ENTRY_POINTS_WITHOUT_CALLERS`.

### Finding 3. The pilot database holds no candidate data at all

`readypick-pilot` is the **only** deployed environment in the account: one ECS
cluster, one RDS instance, and no clusters in any other region. It holds 3
tenants and 30 jobs, and **zero candidates, zero profiles, zero applications,
zero reports, zero evaluations, zero evidence rows and zero agent traces.**

This has to be stated plainly because several of the programme's acceptance
criteria are written against production volume and cannot be evaluated as
written:

- W2's "`test_retrieval_tenant_recall` passes at the production tenant count"
  has a production tenant count of **3**.
- W1's "a candidate assessed in the pilot environment produces a PRISM report"
  has **no candidate to assess** until one is seeded.
- W7.1's golden sets specify "60% stratified production sample". **There is no
  production sample.** The stratified 60% cannot be drawn, and synthesising it
  would violate that same section's standing rule that ground truth produced by
  the same class of model being evaluated measures agreement with that model.

None of this makes the programme wrong. It makes the *evidence* for it come
from a deliberately seeded worked example rather than from traffic, and that
substitution has to be visible rather than implied.

---

## 2. Audit output, verbatim

### 2.1 The canonical reachability grep (section 2.1 command 1)

```
$ cd backend && grep -rn "hiring\.\|miti\.\|siddhi\." app/api app/workers
app/workers/tasks.py:750:    `hiring.scorecard.compile_matrix`: Layer 1's department model and Layer 3's
app/workers/tasks.py:762:    finalises it (`hiring.scorecard.freeze`).
```

Two hits, both comments. This is the output RPN-AI-UP-001 reads as
"unreachable", and it is why finding 1 matters: the same tree, walked
transitively, gives the opposite answer.

### 2.2 Direct importers per package (section 2.1 command 2)

Non-test, non-`__pycache__` importers outside the package itself. Route and
worker importers in bold.

| Package | Direct importers outside itself |
|---|---|
| `services/rag` | `evaluation/regression.py`, `services/tools/implementations.py` |
| `services/tools` | `evaluation/regression.py`, `orchestration_checks.py`, `scripts/eval_adversarial.py`, `scripts/eval_trajectory.py`, `services/agents/identity.py`, `services/orchestration/router.py`, `services/rbac.py` |
| `services/memory` | `scripts/eval_adversarial.py`, `services/reasoning/planner.py`, `services/reasoning/runner.py` |
| `services/agents` | `orchestration_checks.py`, `scripts/eval_agents.py`, `scripts/eval_trajectory.py`, `services/functional_assessment.py`, `services/hiring/scorecard.py`, `services/matching.py`, `services/orchestration/{activation,enforcement,versioning}.py`, `services/ppi.py`, `services/swot_intake.py` |
| `services/observability` | `services/reasoning/runner.py` |
| `services/orchestration` | `evaluation/regression.py`, `orchestration_checks.py`, `scripts/eval_agents.py` |
| `services/evidence` | **`api/assessments.py`**, `scripts/eval_adversarial.py`, `services/agents/identity.py`, `services/functional_assessment.py`, `services/miti/*` (7 modules) |
| `services/reasoning` | `orchestration_checks.py`, `scripts/eval_adversarial.py`, `scripts/eval_trajectory.py` |
| `services/miti` | `orchestration_checks.py`, `scripts/legacy_reset.py`, `scripts/worked_example.py`, `services/agents/identity.py`, `services/calibration.py`, `services/dashboard.py`, `services/functional_assessment.py`, `services/orchestration/activation.py` |
| `services/siddhi` | `schemas/reports.py`, `scripts/legacy_reset.py`, `scripts/worked_example.py`, `services/agents/identity.py`, `services/assessment_formats/evaluation.py`, `services/functional_assessment.py`, `services/gap_analysis.py`, `services/orchestration/activation.py`, `services/report_pdf.py` |
| `services/hiring` | **`api/assessments.py`, `api/dashboard.py`, `api/jobs.py`, `workers/tasks.py`** plus 24 service modules |

### 2.3 Transitive reachability from `app/api` and `app/workers`

The corrected measurement. Shortest path, breadth-first, following imports at
any nesting depth.

| Package | Reachable | Hops | Shortest path |
|---|---|---|---|
| `services/hiring` | yes | 1 | `api.assessments -> services.hiring` |
| `services/evidence` | yes | 1 | `api.assessments -> services.evidence` |
| `services/miti` | yes | 2 | `api.assessments -> services.functional_assessment -> services.miti` |
| `services/siddhi` | yes | 2 | `api.assessments -> services.report_pdf -> services.siddhi.numbers` |
| `services/agents` | yes | 2 | `api.assessments -> services.ppi -> services.agents.envelope` |
| `services/tools` | yes, unexercised | 2 | `api.admin -> services.rbac -> services.tools.permissions` |
| `services/rag` | yes, unexercised | 4 | `api.admin -> services.rbac -> services.tools -> services.tools.implementations -> services.rag.retrieval` |
| `services/memory` | **no** | - | - |
| `services/reasoning` | **no** | - | - |
| `services/orchestration` | **no** | - | - |
| `services/observability` | **no** | - | - |
| `app/evaluation` | **no** | - | and W7.4 requires it stays so |

### 2.4 Is the RAG index ever written? (section 2.1 command 3)

```
$ grep -rn --include=*.py "index_document" app | grep -v __pycache__
app/services/rag/index.py:97:async def index_document(
app/services/rag/__init__.py:15:from app.services.rag.index import IndexResult, index_document
app/services/rag/__init__.py:31:    "index_document",
```

A definition and a re-export. **No call site anywhere in the tree.**

### 2.5 Registered tasks vs scheduled rules (section 2.1 command 5)

**31 registered tasks.** `send_email`, `send_lifecycle_email`,
`send_application_confirmation`, `send_assessment_reminder`, `send_sms`,
`run_matching`, `compile_tatva_matrix`, `generate_matching_categories`,
`generate_ppi_framework`, `generate_technical_questions`,
`reconcile_job_setup`, `generate_candidate_questions`,
`run_functional_assessment`, `generate_proctoring_report`,
`reconcile_proctoring_sessions`, `purge_proctoring_events`,
`release_held_assessments`, `remind_unapproved_technical_questions`,
`parse_resume`, `process_candidate_project`, `reconcile_project_intake`,
`send_verification_requests`, `parse_verification_reply`,
`reconcile_assessment_credits`, `send_payment_failed_email`,
`send_credit_warning_email`, `send_credit_invoice_email`,
`refresh_dashboard_views`, `send_bgv_inquiry`, `parse_bgv_reply`,
`process_assessment_video`.

**7 scheduled entries.** `refresh_dashboard_views`,
`remind_unapproved_technical_questions`, `reconcile_job_setup`,
`reconcile_assessment_credits`, `reconcile_project_intake`,
`reconcile_proctoring_sessions`, `purge_proctoring_events`.

**None of the seven touches retrieval, evaluation or AI health**, exactly as
RPN-AI-UP-001 section 2.3 records. No registered task name begins
`pickready.index`.

---

## 3. Production numbers, `readypick-pilot`, ap-south-2

Produced by `./scripts/pilot-baseline.sh pilot`, which runs
`python -m app.scripts.ai_baseline` as a one-shot ECS task on the `migrate`
task definition.

Measured twice. The first run, task `134e067c67964e61aaea7b31dcf5142a`, was an
inline `python -c` against the image then deployed (`sha-9dd2d95958d2`); see
3.1 for what it got wrong. The numbers below are the **second** run, task
`c9a4770204e3454d8ca627ca5b4545ff`, exit 0, executed by the checked-in module
from the deployed image `sha-efa7a5cbdd6d`
(`sha256:fd30c9116c44840e6008bd7a77193ba63d37b9b0d273d936d68b5139eca0d940`),
with every probe returning a value and none reporting an error.

That second run is also W0's production verification: it proves the module is
in the image the API is running, and that the script works end to end against
the real database rather than against a local stack.

| Probe | Value |
|---|---|
| `schema_version` | `0088_remove_company_dna` |
| `postgres_version` | `16.13` |
| `pgvector_version` | **`0.8.1`** |
| `tenants_total` | 3 |
| `tenants_active` | 3 |
| `tenants_demo` | **3** |
| `jobs_total` | 30 |
| `jobs_published` | 30 |
| `jobs_with_embedding` | **0** |
| `candidates_total` | 0 |
| `profiles_total` | **0** |
| `profiles_with_embedding` | **0** |
| `applications_total` | **0** |
| `context_chunks_total` | **0** |
| `context_chunks_sources` | **0** |
| `context_chunks_embedded` | **0** |
| `reports_total` | **0** |
| `evaluations_total` | **0** |
| `evidence_items_total` | **0** |
| `evidence_claims_total` | **0** |
| `agent_traces_total` | **0** |
| `agent_learnings_total` | **0** |
| `job_competencies_total` | **0** |
| `scorecard_bindings_total` | **0** |

Two of those rows are findings in their own right and were not visible in the
first measurement.

**`tenants_demo: 3` of `tenants_total: 3`.** Every tenant in the only deployed
environment is a demonstration tenant. `has_credit_headroom` checks the demo
flag before summing the balance, so no credit gate in this environment has ever
refused anything. There is no paying customer here to regress.

**`job_competencies_total: 0` and `scorecard_bindings_total: 0`.** Not one of
the 30 published jobs has a frozen Tatva matrix. That is decisive for W1's
acceptance criterion, and it is the 2026-08-06 defect's exact shape seen from
the other side: `miti.live.evaluate_application` runs gate G1,
`require_frozen_matrix` refuses a job with no approved frozen matrix, and
Runbook 14.1 states the consequence as "scoring blocked entirely".
`functional_assessment.synthesis_node` deliberately lets that raise rather than
falling back to the job's competency rows, because a fallback would be a second
implementation of the criteria chosen at runtime.

**So an assessment started against any job in pilot today would raise
`ScorecardUnavailable`, correctly, and produce no report.** The Part A stack
being wired is necessary and not sufficient: proving it end to end requires a
job that has been through Bodha's SWOT and Sutra's seven stages first. That is
a prerequisite of W1's demonstration, not a defect in W1.

**pgvector 0.8.1 settles W2.3 by measurement rather than by inference from the
RDS release notes.** `hnsw.iterative_scan` requires 0.8.0 or later, so the
`strict_order` mitigation for filtered-ANN recall collapse is available on this
cluster, and the LIST-partition fallback does not have to be designed first.

The zeros are not a broken probe. The probe reads under the same explicit
`app.bypass_rls='on'` flag `core.db.superadmin_scope` sets, precisely because a
connection with no `app.tenant_id` GUC returns zero from a FORCE ROW LEVEL
SECURITY table without erroring, and that silent zero is indistinguishable from
an empty table. `tenants_total: 3` and `jobs_total: 30` on the same connection
are the control: the bypass works, and the other tables really are empty.

### 3.1 The first measurement was wrong, and how

The first attempt ran every query on one connection inside one implicit
transaction. `evidence_records` does not exist -- the ledger is
`evidence_items` -- that query aborted the transaction, and **every subsequent
probe returned "current transaction is aborted"** rather than its number. The
rewrite in `app/scripts/ai_baseline.py` gives each probe its own connection and
its own error boundary, so one wrong table name costs one row of the report.
Recorded here because it is the same failure shape this programme is about: a
measurement that reports one error uniformly looks exactly like a system that
is uniformly broken.

---

## 4. Defects found and fixed in W0

- **`services/tools/permissions.py` declared `AGENT_BODHA` through
  `NAMED_AGENTS` twice**, at lines 160-179 and 204-223. Confirmed real. The
  duplicate had no runtime symptom -- both copies held the same values and
  Python keeps the last -- but they documented `AGENT_VAADA` differently
  ("evidence graphs" versus "the candidate conversational agent"), so the
  module that enforces agent reach carried two answers to what one of its
  agents is. The first block is deleted, the second kept because it holds the
  RBAC 34 rationale, and
  `tests/test_tool_permissions_single_definition.py` now fails the build on a
  second module-level assignment to any of those names.

- **`tests/test_import_graph.py` is flaky on Windows, and was already flaky
  before this change.** Two to three of its 25 `python -c "import X"`
  subprocesses exit `3221227274` on any given run, and **which modules fail
  changes between runs**: `gap_analysis` / `verification.base` /
  `verification_parsing` on one run, `report_evidence` /
  `verification.contradiction` on the next. Verified pre-existing by stashing
  the `permissions.py` change and re-running. Not caused by this programme;
  recorded so the next reader does not attribute it to one. The canonical
  runner remains `./scripts/test.sh` against a fresh database.

---

## 5. What this baseline commits the programme to

1. **W1 is re-scoped from "wire it" to "verify it, and prove it end to end".**
   The wiring exists. What does not exist is a single row of evidence that it
   has ever run against real data, because there is no real data.
2. **W2 keeps its full scope.** The index is genuinely never written, and the
   production table is genuinely empty.
3. **W2's migration is smaller than the brief expects.** `context_chunks`
   already carries `tenant_id` NOT NULL, RLS enabled and forced with a tenant
   isolation policy, `content_sha256`, `source_version`, an HNSW cosine index
   and a GIN index on the tsvector, all from migration 0054. The next migration
   number is 0089, and it adds only what is genuinely missing.
4. **Every acceptance criterion phrased against production volume is
   unevaluable until pilot carries a worked example.** Seeding one is a
   prerequisite, not a shortcut, and the seeded rows must be identifiable as
   seeded.
