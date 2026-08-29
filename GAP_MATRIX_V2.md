# GAP MATRIX V2

**Status:** PHASE 0 deliverable, per spec-doc6 §0.3.
**Document ID:** RPN-GAP-002
**Date:** 29 August 2026
**Branch:** `feat/specdoc6-activation`
**Supersedes:** `GAP_MATRIX.md` (spec-doc5 phase), which remains for its §0 record.

**Revision 2, 29 August 2026.** Three things changed after revision 1 was posted: the RBAC
and Dashboard specifications were supplied by the product owner and filed under
`docs/spec/`; the Runbook reached v1.1 with all nine assumption sites reconciled; and
`runbook_data/` plus its parity test were built. Phase 0's gate is now met. The live-path
findings in §0, §1 and §2 are unchanged and were re-verified.

Every row was verified against the repository, not against any document's claim about the
repository (spec-doc6 §0.1 item 6, §20). Every command is named and reproducible.

---

## 0. THE ONE COLUMN THAT MATTERS

spec-doc6 §0.1 quotes the previous phase's own honest finding:

> **Part A is built and tested but not wired into a single live path.** Nothing a real user touches runs the three-layer framework.

**That finding is confirmed, and it is more complete than the sentence suggests.** This
matrix's **Live path?** column means: reachable from a real HTTP route a user can hit, or
from a registered Celery task, **not** merely importable.

### The method, and the command that settles it

```bash
cd /c/dev/pickready/backend
grep -rn "services\.hiring\|services\.miti\|services\.siddhi\|services\.agents\|services\.evidence" \
     app/ --include="*.py" | grep -v __pycache__
```

Every importer in `app/` was enumerated and then followed. Two independent checks were run
on top:

```bash
# 1. Do api/ or workers/ mention these packages at all, in any import form?
grep -rn "hiring\.\|miti\.\|siddhi\." app/api app/workers --include="*.py"     # no output

# 2. Is the one script that does import them reachable from api/ or workers/?
grep -rn "from app.scripts\|import app.scripts" app/api app/workers            # one hit, C27, unrelated
```

### Verdict per package

| Package | Lines | Non-test importers | On a live path? |
|---|---|---|---|
| `app/services/hiring/` (10 modules) | 4,646 | `app/scripts/worked_example.py:32-34` and `app/services/miti/*` | **NO** |
| `app/services/miti/` (7 modules) | 2,048 | `app/scripts/worked_example.py:34-35` | **NO** |
| `app/services/siddhi/` (2 modules) | 301 | `app/scripts/worked_example.py:36` | **NO** |
| `app/services/agents/` (6 modules) | 1,972 | `ppi.py` ×5, `swot_intake.py` ×3, `matching.py` ×2, `functional_assessment.py` ×1 | **YES** |
| `app/services/evidence/` (3 modules) | 1,479 | `app/api/assessments.py:1416`, `functional_assessment.py:529,629`, `miti/*` | **YES** |

`app/scripts/worked_example.py` is a standalone `python -m` script (`:9`). It is not
imported by anything (`grep -rn "worked_example" app/ | grep import` returns only
`department_models.py`'s unrelated `"worked_example"` string constant, an evidence-source
name). It is not a live path.

### What that means in one sentence

**Three of the five Part A packages, 6,995 lines and 173 passing tests, are reachable only
from a script.** The two that are live (`agents/`, `evidence/`) are live because they were
bolted onto the **old** pipeline: `agents/identity.py` is an identity table pointing at
`services/ppi`, `services/swot_intake`, `services/matching`, `services/interviewer`,
`services/functional_assessment` and `services/gap_analysis`
(`app/services/agents/identity.py:104,124,138,152,176`), not at `hiring/`, `miti/` or
`siddhi/`.

So the six agents **do** run today. They run the pre-Part-A implementations under Part A
names.

---

## 1. INVENTORY

### 1.1 Routers registered in `main.py`

**24 `include_router` calls, 17 distinct routers.** Seven are mounted twice, once under the
legacy prefix and once under `/api/v2`.

```
auth admin companies jobs candidates matching verification outreach dashboard
portal telemetry emails pipeline provider bd billing assessments
```

`assessments` is mounted **only** under `/api/v2/assessments`
(`app/main.py:128`). Command: `grep -c "include_router" app/main.py`;
`grep -oE "include_router\(([a-z_]+)\." app/main.py | sort -u`.

### 1.2 Part A modules with zero non-test importers

Modules whose only importers are other modules in the same unreachable package, plus
`worked_example.py`:

| Module | Lines | Reached from |
|---|---|---|
| `app/services/hiring/gates.py` | 329 | `miti/pipeline.py:60` only |
| `app/services/hiring/company_dna.py` | 959 | `transformation.py:61`, `swot_quality.py:55` |
| `app/services/hiring/transformation.py` | 624 | `worked_example.py:32` only |
| `app/services/hiring/layers.py` | 817 | `company_dna.py:63`, `transformation.py:60` |
| `app/services/hiring/situations.py` | 502 | `transformation.py:60`, `swot_quality.py:55` |
| `app/services/hiring/department_models.py` | 853 | 5 siblings, `miti/aggregation.py:53`, `miti/dimensions.py:51` |
| `app/services/hiring/evidence_graph.py` | 537 | **nothing at all** |
| `app/services/hiring/ontology.py` | 228 | **nothing at all** |
| `app/services/hiring/swot_quality.py` | 398 | **nothing at all** |
| `app/services/miti/pipeline.py` | 387 | `worked_example.py:34` only |
| `app/services/miti/aggregation.py` | 460 | `pipeline.py:61`, `worked_example.py:34` |
| `app/services/miti/dimensions.py` | 404 | `pipeline.py:62`, `aggregation.py:60`, `worked_example.py:35` |
| `app/services/miti/tiering.py` | 374 | **nothing at all** |
| `app/services/miti/triangulation.py` | 404 | `pipeline.py:61` |
| `app/services/miti/claims.py` | 303 | `tiering.py:51` |
| `app/services/siddhi/citations.py` | 291 | `worked_example.py:36` only |

**Three modules have no importer anywhere in `app/`, including within their own package:**
`hiring/evidence_graph.py`, `hiring/ontology.py`, `hiring/swot_quality.py`, and
`miti/tiering.py` is reached only by `claims.py`, which nothing else reaches. These are the
Department Evidence Graphs (spec-doc6 §4.4 Vaada), the skills ontology (§4.4 Yukti, §4.6
matching, called a **fairness requirement**), and the §18.5 SWOT rejection rules (§4.3).
All three are named acceptance criteria and none is connected to anything.

### 1.3 Part A ORM models with zero readers and zero writers

Migration `0059_hiring_intelligence.py` and `0056_evidence_ledger.py` create the tables.
`app/models/__init__.py:92-95,47` exports them, which is why Alembic sees them. Nothing
else touches them.

```bash
for m in CompanyDNA Evaluation ReviewDisposition CalibrationRecord EvidenceItemRow EvidenceClaim; do
  grep -rn "\b$m\b" app/ --include="*.py" | grep -v __pycache__ | grep -v "app/models/"
done
```

| Table | Model | Readers | Writers |
|---|---|---|---|
| `company_dna` | `app/models/hiring.py:88` | 0 | 0 |
| `evaluations` | `app/models/hiring.py:156` | 0 | 0 |
| `review_dispositions` | `app/models/hiring.py:247` | 0 | 0 |
| `calibration_records` | `app/models/hiring.py:293` | 0 | 0 |
| `evidence_items` | `app/models/evidence.py:26` | 0 | 0 |
| `evidence_claims` | `app/models/evidence.py:65` | 0 | 0 |
| `evidence_claim_links` | `app/models/evidence.py:90` | 0 | 0 |

**Consequence for spec-doc6 §6 (legacy reset).** D2 lists *"`Evaluation` rows and all
dimension/competency scores"* among the purge targets. The `evaluations` table has never
been written to and is empty by construction. What actually needs purging lives in the old
tables (`functional_skills_reports`, `report_dimensions`, `job_candidate_links.match_score`,
`.match_breakdown_json`, `.tier`). The survey (`--survey` mode) must classify the **old**
tables, not the new empty ones.

The one exception: `app/services/hiring/company_dna.py:725` defines a **second class also
called `CompanyDNA`**, an in-memory compiled artifact. It is unrelated to the ORM row.
See `CONTRADICTIONS.md` C10.

### 1.4 Does `app/services/siddhi/` have a report generator?

**No.** The package is two files:

```
app/services/siddhi/__init__.py     10 lines
app/services/siddhi/citations.py   291 lines
```

`citations.py` contains `UncitedStatement` (`:72`), `UnknownEvidence` (`:80`),
`Statement` (`:132`), `Section` (`:168`), `Report` (`:209`) and `check` (`:283`). These are
the **citation enforcement primitives**: `Section.render` is the only path to text and it
raises on an uncited statement (CLAUDE.md). There is no prompt, no model call, no PRISM
assembly, no export.

Report generation still lives entirely in the old modules:
`app/services/functional_assessment.py` and `app/services/gap_analysis.py`, which is
exactly what `app/services/agents/identity.py:176` records as Siddhi's `implemented_by`.

**spec-doc6 §4.5 requires** *"The existing test that tries to emit an uncited statement and
confirms it is blocked must now run against the live generation path."* The live generation
path does not import `siddhi` at all, so this is not a wiring task; it is a rewrite of
report synthesis on top of `citations.Section`.

### 1.5 Test inventory

108 test files, 1,834 test functions
(`ls backend/tests/test_*.py | wc -l`;
`grep -rh "^def test_\|^async def test_" backend/tests/test_*.py | wc -l`).
19 `pytest.skip(` call sites, predominantly `"no database reachable"`, which is what
spec-doc6 §3.3's 71 runtime skips resolve to.

The previous phase's published baseline (`GAP_MATRIX.md:5-8`) was
**1755 passed, 10 failed, 51 skipped**. **No new baseline is published here**: Phase 1 owns
that, and running the suite now would measure a working tree that seven agents are
concurrently editing (116 changed paths at the time of writing).

---

## 2. THE SIX AGENTS

Registry: `app/services/agents/identity.py:93-181`. Activation order
(`:187-192`): `(Bodha) → (Sutra, Yukti) → (Vaada, Miti) → (Siddhi)`.

**Read the "Runs what?" column carefully.** Every one of the six is live. None of them runs
Part A.

### 2.1 Bodha, Hiring Manager SWOT Intake Agent

| | |
|---|---|
| **Registry** | `identity.py:94-106`. `runtime_id = AGENT_JOB_SETUP`, portal `customer`, produces `swot_evidence` |
| **What exists** | `app/services/swot_intake.py` (four SWOT areas, bounded, A2A publish at `:559,621,676`). Part A side: `app/services/hiring/swot_quality.py` (398 lines, §18.3 probes and §18.5 rejection rules), `app/services/hiring/situations.py` (502 lines, six situation types), `app/services/hiring/company_dna.py` (959 lines, the 12-section instrument) |
| **Live path?** | **PARTIAL.** `swot_intake.py` is live via `api/jobs` and the `JobSwotIntake` model (migration 0049). Its gate is live: `swot_intake.py:626` calls `agents/gates.py:128 bodha_gate`. **`hiring/swot_quality.py` has zero importers anywhere.** **`hiring/situations.py` is imported only by `transformation.py` and `swot_quality.py`, neither live.** **`hiring/company_dna.py` is imported only by `transformation.py` and `swot_quality.py`, neither live.** |
| **Tested?** | Yes, in isolation. `tests/test_bodha_intake.py` (588 lines, 50 tests), `tests/test_hiring_layers.py` (447 lines, 35 tests), `tests/test_a2a_artifacts.py`, `tests/test_agent_gates.py` |
| **Missing** | The **second mandate entirely**: no Company DNA session, no route, no screen, no persistence (`company_dna` table has zero writers, §1.3). The §18.5 rejection rules never fire on a real save. Situation-type classification never runs, so nothing is ever read back to a Hiring Manager for confirmation (spec-doc6 §4.3, and CLAUDE.md calls misclassification *"the most expensive error at intake"*). No forced-scale API-layer rejection. No versioning |

### 2.2 Sutra, Tatva Matrix Agent

| | |
|---|---|
| **Registry** | `identity.py:107-120`. `implemented_by = ("services/ppi",)`, produces `tatva_matrix` |
| **What exists** | `app/services/ppi.py` generates competencies in **one LLM pass**. Part A side: `app/services/hiring/transformation.py` (624 lines, the seven stages), `app/services/hiring/layers.py` (817 lines, `BOUNDS`/`INVARIANTS`), `app/services/hiring/department_models.py` (853 lines, Layer 1) |
| **Live path?** | **NO for Part A.** `ppi.py` is live (Celery `pickready.generate_ppi_framework`, `app/workers/tasks.py:672`; routes in `api/jobs`, `api/assessments`) and its gate is live (`ppi.py:1025` calls `sutra_gate`). **`hiring/transformation.py`'s only importer in `app/` is `app/scripts/worked_example.py:32`.** Seven-stage transformation runs nowhere a user can reach |
| **Tested?** | Old path: `tests/test_ppi.py`. Part A: `tests/test_hiring_layers.py` (35 tests) covers `layers.py`, `transformation.py`, `department_models.py`, `situations.py` |
| **Missing** | The live seven-stage pipeline. Weight traceability through the API (spec-doc6 §17: *"a frozen matrix whose every weight traces to a named Layer 1/2/3 source"*). The Hiring Manager review screen showing provenance in plain language (spec-doc6 §4.3 attributes this to RBAC §10.3; **§10.3 does not contain it**, see C38, so cite spec-doc6 and RBAC §12 instead). The `FINALIZED` state and its audit row (RBAC §20) do not exist, see `CONTRADICTIONS.md` C11. **G1 does not block anything**, see C20 |

### 2.3 Yukti, Matching Agent

| | |
|---|---|
| **Registry** | `identity.py:119-133`. `implemented_by = ("services/matching", "services/matching_categories")`, produces `ai_score` |
| **What exists** | `app/services/matching.py` (~1,800 lines): hybrid retrieval, LLM re-rank, four-parameter breakdown, tier assignment. Part A side: `app/services/hiring/ontology.py` (228 lines) |
| **Live path?** | **YES for the old implementation.** Celery `pickready.run_matching` (`app/workers/tasks.py:587`), routes in `api/matching`, `api/jobs`, `api/candidates`. Gate live at `matching.py:1511`. **NO for Part A: `hiring/ontology.py` has zero importers anywhere in `app/`** |
| **Tested?** | Old path: `tests/test_matching.py`, `tests/test_scoring.py`, `tests/test_tiers.py`, `tests/test_yukti_publishing.py`. Ontology: `tests/test_hiring_retrieval.py` (249 lines, 25 tests) |
| **Missing** | The Pre-Screen Grade (A/B/C/Hold) does not exist, see `CONTRADICTIONS.md` C9. The ontology is not wired, so the 40-pair vocabulary-mismatch regression corpus spec-doc6 §4.4 calls **a fairness requirement** measures nothing on the live path. Resume-stage evidence strength (*"a resume line is a claim, not a fact"*) is not implemented: `matching.py` scores textual and semantic similarity. **And a live defect:** `matching.py:1775` writes `link.tier` from a **second, contradicting grade scale**, see `CONTRADICTIONS.md` C19 |

### 2.4 Vaada, Candidate Conversational Agent

| | |
|---|---|
| **Registry** | `identity.py:134-146`. `implemented_by = ("services/interviewer", "services/ppi_interview")`, portal `candidate` |
| **What exists** | `app/services/interviewer.py` (adaptive follow-ups, bounded), `app/services/technical_interview.py` (question plus rubric in one call), `app/services/answer_classification.py`, `app/services/conversation_guardrails.py`. Part A side: `app/services/hiring/evidence_graph.py` (537 lines, Department Evidence Graphs) |
| **Live path?** | **YES for the old implementation**, and it is the most thoroughly live agent in the product: `POST /api/v2/assessments/.../respond` and the whole conversation surface in `api/assessments.py`. **NO for Part A: `hiring/evidence_graph.py` has zero importers anywhere** |
| **Tested?** | Extensively: `tests/test_conversation_flow.py`, `tests/test_vaada_miti_loop.py`, plus `app/scripts/eval_interview.py` gating CI. Evidence graphs: `tests/test_hiring_retrieval.py` |
| **Missing** | Question generation still draws on the generic per-competency path, not the Department Evidence Graph for the role's department (spec-doc6 §4.4 requires deleting the generic bank **in the same commit**). No triangulation posture routing questions toward the evidence sources Sutra's matrix flagged, because there is no Sutra matrix on the live path. Termination is by question count only; evidence sufficiency is not consulted |

### 2.5 Miti, Tatva Scoring Agent

| | |
|---|---|
| **Registry** | `identity.py:145-166`. `implemented_by = ("services/functional_assessment", "services/evidence")`, portal `internal`. The registry docstring at `:35-41` notes Miti *"is not merely a rename"*: it had no separate identity before |
| **What exists** | `app/services/functional_assessment.py` scores per competency directly. Part A side: the whole `app/services/miti/` package (2,048 lines): `pipeline.py` (stages 2 to 6), five isolated evaluators in `dimensions.py`, `tiering.py`, `triangulation.py`, deterministic `aggregation.py`, `claims.py` |
| **Live path?** | **NO for Part A, and this is the sharpest gap in the product.** `functional_assessment.py` is live via Celery `pickready.run_functional_assessment` (`app/workers/tasks.py:895`). It reaches `app/services/evidence/ledger.py:529` and `contradictions.py:629`, and `api/assessments.py:1416` imports the ledger directly, so **the evidence ledger is live**. **`app/services/miti/pipeline.py` has one importer in all of `app/`: `app/scripts/worked_example.py:34`.** The five isolated evaluators, the tiering, the triangulation and the deterministic aggregator never run for a real candidate |
| **Tested?** | Yes, heavily, in isolation: `tests/test_miti_pipeline.py` (965 lines, **69 tests**), `tests/test_miti_evidence_wiring.py`, `tests/test_evidence_ledger.py` (521 lines, 22 tests), `tests/test_contradiction_severity.py` |
| **Missing** | Everything downstream of the module boundary. Specifically: **gates G1 to G4 block nothing** (`hiring/gates.py:121,170,215,273` are called only from `miti/pipeline.py:290,334,350,381`, see `CONTRADICTIONS.md` C20). The Must-have hard cap, the authenticity multiplier, insufficient-versus-negative evidence, candidate-state logic and the no-auto-reject guarantee are all real, all tested, and all unreachable. `review_dispositions` has zero writers, so spec-doc6 §4.4's invariant (*"no rejection exists without a recorded human disposition"*) has no data to assert over |

### 2.6 Siddhi, PRISM Report Synthesis Agent

| | |
|---|---|
| **Registry** | `identity.py:167-181`. `implemented_by = ("services/functional_assessment", "services/gap_analysis")`, portal `customer` |
| **What exists** | Report synthesis in `functional_assessment.py` and `gap_analysis.py`; rendering at `api/assessments.py:753`; PDF at `report_pdf.py`; frontend `components/functional-skills-report.tsx`. Part A side: `app/services/siddhi/citations.py` (291 lines) **and nothing else**, see §1.4 |
| **Live path?** | **YES for the old implementation** (`GET /api/v2/assessments/.../report`, Celery synthesis, PDF export). Gate live at `functional_assessment.py:1590`. **NO for Part A: `siddhi/citations.py`'s only importer is `app/scripts/worked_example.py:36`** |
| **Tested?** | Old path: `tests/test_prism_report.py` (header and section order pinned), `tests/test_report_authenticity.py`, `tests/test_report_gate_wiring.py`, `tests/test_report_eval.py`. Citations: `tests/test_siddhi_citations.py` (265 lines, 19 tests) |
| **Missing** | **Structural citation enforcement is not on the live path at all.** The live generator emits prose without passing through `Section.render`, so the guarantee CLAUDE.md describes (*"`Section.render` is the only path to text and it raises on an uncited statement"*) holds for a module nobody calls. There is no evidence-node model behind a live report. The GAP-statement-needs-a-citation rule, which CLAUDE.md calls *"the entry worth defending"*, is unenforced in production. No serialiser-level number ban (spec-doc6 §4.5, D8). No banned-generic-advice corpus. No Ready Pick Note |

### 2.7 Summary

| Agent | Live? | Runs what? | Part A module live? | Part A tests |
|---|---|---|---|---|
| Bodha | Yes | `swot_intake.py` (SWOT only) | **No** | 85 |
| Sutra | Yes | `ppi.py` (one LLM pass) | **No** | 35 (shared) |
| Yukti | Yes | `matching.py` (similarity) | **No** | 25 (shared) |
| Vaada | Yes | `interviewer.py` (generic) | **No** | 25 (shared) |
| Miti | Yes | `functional_assessment.py` + live evidence ledger | **No** | 91 |
| Siddhi | Yes | `functional_assessment.py` + `gap_analysis.py` | **No** | 19 |

**Zero of six run Part A. Six of six run under Part A names.** That naming is not
dishonest, `identity.py`'s docstring explains it deliberately (*"the runtime id stays what
it was"*), but it does mean an observer reading logs, traces or the A2A artifact stream sees
"Bodha", "Sutra", "Miti" and "Siddhi" executing successfully today while none of the
three-layer framework runs.

---

## 3. PHASES 0 TO 11

### Phase 0, Runbook editing, extraction, parity test, reconciliation, contradictions, gap matrix

**GATE MET.**

| | |
|---|---|
| **Exists** | **The Runbook is v1.1**, `Readypick Hiring Philosophy.md` at the repository root. 21 editorial edits applied: front matter, a 100-entry table of contents, §16's twelve subsections numbered §16.1 to §16.12, Appendices D and E renumbered so they stop colliding with the dimension names D1 to D5 and the tier names E0 to E5, naming normalised, and a canonical-spellings glossary. **Anything citing "Section N" under §16 now cites §16.N.** <br><br>**Both specifications are now filed**, unedited and with provenance headers: `docs/spec/RBAC_SPECIFICATION.md` (1,741 lines, all 40 sections, **§24's permission matrix complete at `:1003-1046`, 24 capability rows by 5 role columns**), `docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md` (447 lines, all 8 columns with states, styling, palette, typography, spacing, a11y and all three workflows), and `docs/spec/ARCHITECTURE_DIRECTION_2026-08-28.md` (228 lines, **advisory, ranked below everything in spec-doc6 §0.2**). <br><br>`app/hiring/runbook_data/` holds **nine YAML files: 2,453 values under 103 citations naming 119 Runbook sections**, plus a typed loader. `CONTRADICTIONS.md`, `GAP_MATRIX_V2.md`, `PHASE0_FINDINGS.md`, `RUNBOOK_RECONCILIATION.md`, `RUNBOOK_EDITS.md` and `RUNBOOK_OPEN_QUESTIONS.md` all exist. |
| **Live path?** | n/a (documentation and data phase) |
| **Tested?** | **Yes.** `tests/test_runbook_parity.py`: **29 tests checking 300 numbers and 1,042 verbatim strings**, mutation-tested in both directions (edit a value in the data, edit a value in the Runbook), **7 of 7 mutations caught**. `PyYAML`, `types-PyYAML` and `mypy` pinned in `requirements.txt`. |
| **Missing** | Nothing blocking. Five substantive Runbook ambiguities remain open and are `RESOLVED-BY-DEFAULT`, listed in `RUNBOOK_OPEN_QUESTIONS.md` and registered as `CONTRADICTIONS.md` C32, C34, C35, C37 (plus C19, which is a repository defect rather than a Runbook one). Two further Runbook items are resolved but need the owner told: C31 (a misdirected citation that read as authorisation to filter on age, caste, gender and employment gaps) and C36 (three Layer 1 baselines outside the clamp §11.4 calls absolute). |

#### The nine assumption sites: 0 CONFIRMED, 8 CORRECTED, 1 CORRECTED-in-part

**Every single guess was wrong.** Zero `ASSUMPTION (RUNBOOK-GAP` markers remain. Full table
in `RUNBOOK_RECONCILIATION.md`. The four worth recording here as defects found, because each
was silently affecting output:

| Site | What was assumed | What the Runbook says | Blast radius |
|---|---|---|---|
| `app/services/hiring/department_models.py` | 5 departments | **15** (Part VI, §21 to §35) | Civil engineers, designers, architects, HR and skilled trades were all scored against a **generic** model. Two thirds of the department coverage was missing |
| `app/services/miti/triangulation.py` | benign explanations generated ad hoc | **§13.2 names seven** | The module had **none of the seven**. CLAUDE.md's "two benign explanations before any escalation above Minor" was running on invented explanations |
| `app/services/hiring/situations.py` | six situation types with inferred multipliers | §18.4's six types with arrows only | **4 of 6 rows wrong, two by inversion.** An inverted situation weighting re-weights the whole matrix coherently and invisibly, which CLAUDE.md calls "the most expensive error available at intake" |
| `app/services/hiring/company_dna.py` | 12 sections | §16's twelve, now §16.1 to §16.12 | **5 of 12 missing and 4 invented.** The Layer 2 instrument that constrains every job a client will ever post |

Note the relationship to §0's live-path finding: **none of these four defects ever reached a
user**, because none of these modules is on a live path (§2). That is the only reason the
department-model gap did not mis-score a real civil engineer. It is not a mitigation to rely
on twice.

### Phase 1, Docker environment, baseline, close failing tests, categorise skips

| | |
|---|---|
| **Exists** | **`docker-compose.test.yml` already exists at the repository root** with `pgvector/pgvector:pg16` (`:42`), `redis:7.2-alpine` (`:75`) and MinIO (`:99`, plus an `mc` init container at `:129`). CI is one workflow, `.github/workflows/deploy.yml`, with jobs `backend-tests:68`, `frontend-checks:149`, `security-scan:194`, `terraform:226`, `verify-approval-gate:255`, `build-and-push:278`, `plan-staging:339`, `apply-staging:387`, `plan-production:441`, `apply-production:489` |
| **Live path?** | n/a |
| **Tested?** | 108 test files, 1,834 test functions, 19 `pytest.skip(` sites |
| **Missing** | `make test` / `make test-integration` / `make test-all`. `docs/SKIPS.md` with the four categories. The CI skip-count assertion. Version alignment between the compose Postgres (pg16) and `infra/modules/rds`, which spec-doc6 §3.2 says is a **defect** if they differ: verify. A published baseline. Previous baseline for reference: **1755 passed, 10 failed, 51 skipped** (`GAP_MATRIX.md:5-8`) |

### Phase 2, Company DNA intake end to end

| | |
|---|---|
| **Exists** | `company_dna` table (`app/models/hiring.py:88-108`, migration 0059). The instrument logic (`app/services/hiring/company_dna.py`, 959 lines) with `is_observable`, `compile_artifact` at `:817`, and the `CompanyDNA` compiled dataclass at `:725` |
| **Live path?** | **NO. Zero readers, zero writers on the table (§1.3). Zero routes. Zero UI** (`grep -rln "company.dna\|companyDna" frontend/app frontend/components` returns nothing) |
| **Tested?** | Yes in isolation: `tests/test_bodha_intake.py` (50 tests) |
| **Missing** | All seven routes from spec-doc6 §4.2. All four screens. Versioning and immutability. Forced-scale rejection **at the API layer**. The Runbook §16 accepted/rejected example table as a test (spec-doc6: *"This test is the specification"*). RBAC per D3 across six principals. Cross-tenant 404. The structural guarantee that Sutra cannot reach the raw session. **Blocked on:** `Role` mapping, `CONTRADICTIONS.md` C22 and C23; the duplicate `CompanyDNA` class name, C10 |

### Phase 3, Job setup live, Bodha SWOT to Sutra seven stages, G1

| | |
|---|---|
| **Exists** | `swot_intake.py` live; `ppi.py` live; `hiring/transformation.py` (seven stages) and `hiring/swot_quality.py` (§18.5 rules) built and unreachable |
| **Live path?** | **PARTIAL.** SWOT intake yes; seven-stage transformation no; §18.5 rejection no; situation classification no; G1 no |
| **Tested?** | `tests/test_ppi.py`, `tests/test_assessment_setup_gate.py`, `tests/test_hiring_layers.py` |
| **Missing** | The RBAC §17 lifecycle does not exist: three unrelated vocabularies do (`JobStatus` at `app/models/enums.py:36-43`, `assessment_status` as a bare `String(40)` at `app/models/job.py:99`, `posting_status` computed at `app/services/job_posting.py:74`). No `FINALIZED`, no `SENT_TO_HIRING_MANAGER`, see C11. **G1 blocks nothing**, C20. No per-item Layer 1/2/3 provenance surfaced through the API. No HM review screen. The old one-pass matrix code must be deleted in the same commit (D1) |

### Phase 4, Scoring live, Yukti pre-screen, Vaada evidence graphs, Miti five dimensions, G2/G3

| | |
|---|---|
| **Exists** | Old scoring live end to end. Part A: `miti/` (2,048 lines), `hiring/evidence_graph.py`, `hiring/ontology.py`. Evidence ledger **is** live |
| **Live path?** | **NO for everything Part A**, see §2.3 to §2.5. Two of the three supporting modules (`evidence_graph.py`, `ontology.py`) have **zero importers anywhere** |
| **Tested?** | 69 tests in `test_miti_pipeline.py`, 25 in `test_hiring_retrieval.py`, 22 in `test_evidence_ledger.py` |
| **Missing** | Pre-Screen Grade (C9). The 40-pair fairness corpus. Property-based hard-cap tests. The 100-run and cross-process determinism test. The four candidate-state fixtures. G2/G3 on the live path. The AST isolation test *"extended to cover the live wiring, not just the module"* (spec-doc6 §4.4), which cannot be extended to a wiring that does not exist. **And a live defect to fix first:** the second grade scale at `app/services/tiers.py`, C19 |

### Phase 5, Reports live, Siddhi citation enforcement, G4, number ban

| | |
|---|---|
| **Exists** | PRISM live (`api/assessments.py:753`), section order pinned in both renderers, PDF export. `siddhi/citations.py` built and unreachable |
| **Live path?** | **NO for citation enforcement.** §1.4: there is no report generator in `siddhi/` at all |
| **Tested?** | `tests/test_prism_report.py`, `test_report_authenticity.py`, `test_report_gate_wiring.py`, `test_siddhi_citations.py` (19 tests) |
| **Missing** | An evidence-node model behind live reports. `Section.render` as the only text path in production. The serialiser-level number ban across **every export format** (D8). The banned-generic-advice corpus. The Ready Pick Note with internal citations. G4 on the live path. Note the existing no-numbers tests (`tests/test_platform_audit.py:295,305`) assert **labels**, not payload traversal |

### Phase 6, Legacy reset

| | |
|---|---|
| **Exists** | Nothing. `app/scripts/legacy_reset.py` does not exist |
| **Live path?** | n/a |
| **Tested?** | No |
| **Missing** | All four modes. **Two corrections to the spec's premise:** (a) the `evaluations` table D2 names as a purge target has **never been written to** (§1.3); the real purge targets are `functional_skills_reports`, `report_dimensions`, and `job_candidate_links.{match_score, match_breakdown_json, tier}`. (b) D2's *"gate G1 already blocks evaluation … Do not build a second enforcement path"* is **false today**, C20: G1 must be wired in Phase 3 first. Team Review preservation is feasible: `CandidateTeamReview` exists with routes at `api/candidates.py:572-580`. `--regrade` cannot execute (D6) |

### Phase 7, Re-embedding

| | |
|---|---|
| **Exists** | Nothing. `app/scripts/reembed.py` does not exist. Migration 0058 widened `jobs.reach_embedding` to 1024 and NULLed it |
| **Live path?** | n/a |
| **Tested?** | No |
| **Missing** | The whole script. **The per-row embedding model/version column, which spec-doc6 §7 identifies as the root cause of the current ambiguity, does not exist**: `profiles.embedding` and `jobs.embedding` are `vector(1024)` holding a mix of BGE-M3 and Voyage vectors with nothing recording which. CLAUDE.md states the consequence plainly: *"They are not COMPARABLE … retrieval mixes two spaces until a re-embed runs."* Shadow column, 50-pair quality set. Cannot execute (D6) |

### Phase 8, Candidate dashboard, Company DNA screens, calibration view, UI polish

| | |
|---|---|
| **Exists** | `frontend/components/candidate-ranking-table.tsx` (602 lines). Its own header comment at `:7-8` gives the current columns: **Name, Type of Procurement, Status, Resume, AI Rating & Report, PRISM Report, Decision**. `candidate-team-review-modal.tsx` (277 lines). Design system from the previous phase (navy/teal, `DESIGN.md`, `frontend/scripts/impeccable-gate.mjs`, `check-contrast.mjs`) |
| **Live path?** | Yes for what exists |
| **Tested?** | Partially. `logomark-placement.test.ts`, contrast script |
| **Missing** | Columns 3, 4, 5 and 6 entirely (Pre-Screen Grade, Ready Pick Score, Ready Pick Note, Ready Pick Profile): `grep -rln "Pre-Screen\|Ready Pick Score\|preScreen" frontend` returns **nothing**. Column 8 is an 11-value enum, not 6 (C11). The muted Pre-Screen Grade component test. RBAC-driven controls, **blocked on the missing per-job assignment table**, C5. The three named workflows. Company DNA screens. The calibration view. The override-rate measurement. **No longer blocked on the specification:** `docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md` supplies content, states, exact pixel sizes, palette, typography, spacing, tab order, screen-reader behaviour, mobile behaviour, the five Ready Pick Score bands with numeric ranges, and all three workflows step by step (`:320-345`). **New blockers found in it:** its palette contradicts the navy/teal system and the never-grey token (C30); its Source column has two values where the repo has three (C40); its Team Review panel offers Pass/Hold/Reject to a role RBAC §13.5 forbids from rejecting (C6); and its band vocabulary is a third grade scale with a fourth set of cut-points (C9) |

### Phase 9, RBAC conformance, tenant isolation, agent authorization, audit

| | |
|---|---|
| **Exists** | A capability model that is **data, not code**, which is the right foundation: 25 capabilities (`app/services/capabilities.py:15-62`), resolution user overlay → tenant row → global template → deny (`app/services/rbac.py:73` `resolve_permission`, `:131` `has_capability`), enforced by `require_capability` (`app/api/deps.py:342`), re-resolved on **every** request rather than cached from login (`:346-348`). `AuditLog` (`app/models/tenant.py:111-128`), append-only. Tests: `tests/test_rbac.py`, `tests/test_authorization_surface.py` |
| **Live path?** | Yes |
| **Tested?** | Partially. No role × capability × relationship matrix |
| **Missing** | **The §24 matrix is available and complete** (`docs/spec/RBAC_SPECIFICATION.md:1003-1046`): 24 capability rows by 5 role columns, **120 cells**, with three defined footnote markers. spec-doc6 §17's *"Every cell of the §24 matrix is an executable test"* is now **meetable**, and the suite can be generated from the table. The repo has 25 capabilities (`backend/app/services/capabilities.py:15-62`) against §24's 24 rows, so the mapping is close but not one to one and must be written down cell by cell in `docs/RBAC.md`. `Role.interview_manager` does not exist (C16). Per-job assignment does not exist, so "scoped versus unscoped" has no axis (C5). Cross-tenant 404-not-403 is applied at exactly two sites (`api/jobs.py:1362`, `api/portal.py:321`), not cross-cuttingly. **None** of the four cardinality invariants has a database constraint. **The audit schema cannot record 7 of the 13 required fields, including both agent-attribution columns RBAC §34 calls non-negotiable** (C26). No Super Admin activity view |

### Phase 10, AWS readiness

| | |
|---|---|
| **Exists** | **Seven modules** confirmed present: `ls infra/modules` returns `ecr ecs elasticache network rds s3 secrets`. Two environments: `infra/environments/{staging,production}` plus `derive-production.py`. `infra/validate.sh`. Deploy workflow with `terraform:226`, `verify-approval-gate:255`, `plan-staging:339`, `apply-staging:387`, `plan-production:441`, `apply-production:489` |
| **Live path?** | **Not deployed, and that is the requirement** (D5, spec-doc5 §D.1). Two independent stops per CLAUDE.md: an unset `vars.AWS_DEPLOY_ENABLED` and a required-reviewer environment |
| **Tested?** | `tests/test_deploy_secret_hygiene.py`, `tests/test_platform_audit.py`; `terraform validate` offline |
| **Missing** | `alb/`, `acm/`, `dns/`, `waf/` (C12). **The ALB listener rule must be derived from the same constant as `public_job_url`**: RBAC §15 says `/jobs/{...}`, the app serves `/apply/{...}` (a deliberate, recorded divergence, C45), and a listener rule written for the wrong one 404s the public link in production and nowhere else. The offline planning profile (§13.3) and the plan artifact. `docs/DEPLOY_AWS.md` as an ordered runbook. `ap-south-1` demoted from assumption to required variable. Re-verification of zero GCP references: `docs/DEPLOY_GCP.md` and `docs/DEPLOY_GCP_RUNBOOK.md` are staged as deleted in the working tree, so this is in progress |

### Phase 11, Slop sweep, naming, typing, docs, commits, final report

| | |
|---|---|
| **Exists** | `CLAUDE.md`, `GAP_MATRIX.md`, `docs/ESD.md`, `docs/PRD.md`, `README.md`, `SETUP_INSTRUCTIONS.md`, `docs/adr/0001-*.md`, a `diagnostics/` set |
| **Live path?** | n/a |
| **Tested?** | Partially. Model-string grep (`tests/test_llm_task_routing.py`), em-dash and platform sweeps (`tests/test_platform_audit.py`) |
| **Missing** | Nine of the ten §10.1 CI checks. Naming: **`picready.com` is FIXED** (2026-08-29; one historical comment remains at `backend/tests/test_staff.py:266`), and RBAC §15 settles `readypick.ai` as canonical. **Two `pickready.app` instances remain open** and need the owner rather than a sweep, because whether the mailboxes exist is an operational fact: `backend/app/core/config.py:93` (a **runtime default** for `smtp_from_email`) and `frontend/app/(org)/org/billing/page.tsx:442` (a live `mailto:`). See C3 and C43. **Vendored design skills are handled** (`.gitignore:52-67`, `tools/design-tools.manifest.json`, `tools/install-design-tools.sh`); the residual gap is that `impeccable` is unpinned, installed twice, and gates CI (C46). `mypy --strict`. The pre-existing frontend typecheck failure. **`CLAUDE.md`'s §0 says the Runbook does not exist, which is now stale** (C24). **Part A is entirely untracked in git** (C28). A registered Celery task imports a deleted module (C27) |

---

## 4. THE SHORTEST PATH THROUGH

Derived from the dependencies above, not from spec-doc6's §16 ordering, which is otherwise
sound.

1. **Commit Part A** (C28). D1's three revertable commits need a base.
2. **Fix the two live defects found here**, since both are cheap and both corrupt data
   already: the second grade scale (C19, C29) and the deleted-module import (C27).
3. **Build the per-job assignment table.** Phase 8's dashboard, Phase 9's scoped access, and
   two of four cardinality invariants all block on it (C5). Nothing else unblocks this much.
4. **Extend `AuditLog`** with the five missing columns (C26). Every later phase writes audit
   rows; adding columns after the writes exist means backfilling.
5. **Apply the `Role` mapping** before any RBAC cell is implemented. C22 is now **settled**
   by RBAC §5 ("Client Super Admin") and §7.1 ("Each client **organization** MUST have
   exactly one active Super Admin"): it is `Role.client`, not `Role.super_admin`. C23 (HR
   Manager onto both `recruitment_manager` and `hr_manager`) still needs the owner. Wrong
   here is a privilege escalation, not a rename.
6. **Generate the RBAC conformance suite from `docs/spec/RBAC_SPECIFICATION.md:1003-1046`**,
   not from spec-doc6's summary of it. Seven of spec-doc6's RBAC citations misstate their
   source (C38), and one of the seven would have granted a real capability on a false
   provenance.
7. **Then** Phase 2 to 5 in the stated order, with G1 wired in Phase 3 before Phase 6 relies
   on it (C20).

---

## 5. REPRODUCTION

```bash
cd /c/dev/pickready/backend

# §0  the live-path verdict
grep -rn "services\.hiring\|services\.miti\|services\.siddhi\|services\.agents\|services\.evidence" \
     app/ --include="*.py" | grep -v __pycache__ | sort
grep -rn "hiring\.\|miti\.\|siddhi\." app/api app/workers --include="*.py"     # no output

# §1.1 routers
grep -c "include_router" app/main.py
grep -oE "include_router\(([a-z_]+)\." app/main.py | sed 's/include_router(//;s/\.//' | sort -u

# §1.2 module sizes
wc -l app/services/hiring/*.py app/services/miti/*.py app/services/siddhi/*.py

# §1.3 tables with no readers
for m in CompanyDNA Evaluation ReviewDisposition CalibrationRecord EvidenceItemRow; do
  echo "-- $m"; grep -rn "\b$m\b" app/ --include="*.py" | grep -v __pycache__ | grep -v "app/models/"
done

# §1.4 siddhi contents
wc -l app/services/siddhi/*.py; grep -n "^def \|^class " app/services/siddhi/citations.py

# §2   agent registry
sed -n '93,192p' app/services/agents/identity.py

# §3   phase evidence
cd /c/dev/pickready
ls -la docs/spec/                                        # both specifications, filed
sed -n '1003,1046p' docs/spec/RBAC_SPECIFICATION.md      # the complete 24 matrix
sed -n '207,230p'   docs/spec/RBAC_SPECIFICATION.md      # 5: "four" then five listed
sed -n '258,266p'   docs/spec/RBAC_SPECIFICATION.md      # 7.1: per client organization
sed -n '839,874p'   docs/spec/RBAC_SPECIFICATION.md      # 17: eight lifecycle states
grep -ci "interview manager" docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md   # 0
grep -n "^## \|^# " "Readypick Hiring Philosophy.md" | head -80
ls infra/modules infra/environments
grep -n "^  [a-z-]*:" .github/workflows/deploy.yml
sed -n '1,10p' frontend/components/candidate-ranking-table.tsx
```
