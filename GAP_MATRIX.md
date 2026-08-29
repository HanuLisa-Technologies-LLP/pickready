# SPEC-DOC5 GAP MATRIX

Produced before any implementation, per spec-doc5 §0 ("First action, no
exceptions"). Every row was verified against the repository, not against either
document's claim about what exists. Baseline test run at the time of writing:
**1755 passed, 10 failed, 51 skipped** — all ten failures are local-environment
gaps (`google-cloud-storage` absent from the venv, the fastembed model not
downloaded, and two enum-parity checks that need a live Postgres), not defects.

---

## 0. BLOCKING INPUT GAP — read this first

**`Readypick_Hiring_Philosophy.md` (RPN-PHIL-001) does not exist.** spec-doc5 §0
names it as the *authoritative* source for anything it specifies more precisely
than specdoc4, and then references it 30+ times by section number: Parts 0–XIV,
§3.5 (precedence conflict resolution), §15 (Layer 2 compilation rule), §16 (the
12-section Company DNA instrument with its literal accepted/rejected examples),
§18.3 (seven high-value probes), §18.4 (six situation types), §18.5 (SWOT
rejection rules), §19 (seven-stage transformation), Part II (evidence model),
Part III (five dimensions), Part VI (department evidence graphs), Part VII
(authenticity doctrine), Part VIII (candidate-state logic), §56 (pipeline +
gates), §57.1–57.6, §58 (retrieval/ontology), §59 (data schema), §60.

Searched: the repository, `docs/`, `~/Downloads/readypick/`, and the whole user
home. Not present in any form. The three `.docx` files that *are* present were
extracted and searched — `specdoc4.docx` and
`Readypick_Agent_Framework_Reference.docx` are near-identical copies of the
specdoc4 baseline (2159 / 2155 lines) and neither contains the Runbook.

**How this was handled, rather than used as a reason to stop:** spec-doc5 itself
restates the Runbook's mechanically load-bearing content inline — the five
dimension names, the six situation types, the seven pipeline stages, the four
gates, the five core objects, the isolation rule, the two-benign-explanations
rule, the insufficient-vs-negative-evidence distinction, and the no-auto-reject
constraint. Everything in Part A below is built from those inline statements.
Where the Runbook would have supplied a detail spec-doc5 does not state (exact
rubric anchor wording, the literal §16 accepted/rejected example sentences, the
precise §3.5 conflict-resolution table, the department evidence graphs'
contents), the code carries an explicit `ASSUMPTION (RUNBOOK-GAP)` comment at
the point of implementation naming the Runbook section it stands in for, so the
real document can be diffed against it later rather than silently diverging.
Every such site is listed in the final report.

---

## PART A — THREE-LAYER HIRING INTELLIGENCE

| Capability | Exists today | Wired live? | Tested? | Gap to close |
|---|---|---|---|---|
| Layer 1 — department competency models, evidence-tier rules | **No.** `ppi.py` generates competencies per job from the JD with no department baseline. | n/a | n/a | Department model registry (baseline competencies, weights, rubric anchors by seniority) as data. |
| Layer 2 — Company DNA | **No.** No client-scoped, versioned hiring-philosophy artifact anywhere. `tenants` carries name/industry/profile only. | n/a | n/a | `CompanyDNA` table + 12-section intake + compilation to weight modifiers / thresholds / disqualifiers. |
| Layer 3 — Role SWOT | **Yes.** `services/swot_intake.py` (689 lines), 4 areas, bounded, publishes an A2A artifact via `publish_swot_evidence`. | Yes — `api/jobs`, `JobSwotIntake` model, migration 0049. | Yes — `test_a2a_artifacts.py`, `test_agent_gates.py`. | Add situation-type classification + confirmation, the 7 high-value probes, the 5 §18.5 rejection rules. |
| Bodha dual mandate | **Half.** SWOT session only. No Company DNA session. | Partial | Partial | Second session mode on the same agent, own artifact type, own gate. |
| Sutra 7-stage transformation | **No.** `ppi.generate_framework` emits competency + level + rationale in one LLM pass; no observable-evidence stage, no evidence-source stage, no traceable weight derivation. | Yes (the 1-pass version) | Yes (`test_ppi.py`) | The 7 stages with per-stage provenance; weight = department baseline x Layer-2 modifier x Layer-3 situation modifier, each term recorded. |
| Weight traceability (acceptance: a Layer-2/3 change moves a weight) | **No.** No weights at all — `matching.WEIGHTS` was deliberately deleted 2026-07-30 and `test_scoring.py` asserts its absence. | n/a | n/a | Introduce weights **only inside the Tatva matrix derivation**, never in matching, so the deleted-`WEIGHTS` rule stays intact. |
| Yukti evidence-strength AI Score + skills ontology | **Partial.** `matching.py` (1817 lines) does hybrid retrieval + rating; no claim/evidence distinction at resume stage, no ontology. | Yes | Yes | Ontology expansion + resume-stage evidence strength. |
| Vaada department evidence graph | **Partial.** `interviewer.py` generates per-competency questions; no department graph, no explicit evidence-source routing. | Yes | Yes | Department evidence graph as a retrieval source; route questions to Sutra's required evidence sources. |
| Miti — 5 internal dimensions | **No.** `functional_assessment.py` scores per competency directly. | n/a | n/a | Five isolated rubric-anchored evaluators. |
| Miti — claim extraction / evidence tiering | **Partial.** `evidence/ledger.py` (730 lines) has claims, evidence items, 4 trust levels, freshness, support state. Migration 0056 exists. | Yes (`test_miti_evidence_wiring.py`) | Yes | Materiality, independence groups, specificity/attribution/scale/decay modifiers, tier→dimension mapping. |
| Miti — triangulation + benign explanations | **Partial.** `evidence/contradictions.py` (605 lines) has type/severity. | Yes | Yes (`test_contradiction_severity.py`) | Enforce >=2 benign explanations before any severity above Minor. |
| Miti — deterministic aggregator, zero model calls | **Partial.** `rating.py` + `cap_to_moderately` are deterministic; no composite / authenticity-multiplier / confidence stage. | Partial | Yes (hard-cap pinned) | Full deterministic aggregation + a test asserting zero model calls. |
| Evaluator isolation (no name, no other dimensions, no composite) | **No.** | n/a | n/a | Structural: the evaluator input type physically cannot carry them. |
| Siddhi citation enforcement *in code* | **Partial.** `report_evidence.py` + `test_remark_grounding.py` check grounding; nothing structurally blocks an uncited statement. | Partial | Partial | A structural emitter that cannot produce a statement without an evidence node ref, plus a test that tries and is blocked. |
| Gates G1–G4 | **Partial.** `agents/gates.py` has six *per-agent* gates (a different axis). `report_gate_wiring`, `needs_human_review` (0057) exist. | Yes | Yes | The four *pipeline* gates as named, tested checks. |
| No-auto-reject + human disposition record | **Partial.** `needs_human_review` flag exists; no disposition record. | Partial | Partial | Disposition table + a test that no flag path auto-rejects. |
| Data model (`Role`/`Scorecard`/`Candidate`/`Evaluation`/`CalibrationRecord`/`CompanyDNA`/`Claim`/`EvidenceNode`) | **Partial.** `Job`≈Role, `JobCompetency`≈Scorecard rows, `Candidate` ✓, `EvidenceClaim`/`EvidenceItemRow` ✓. Missing: `CompanyDNA`, `Evaluation` with `evidence_refs[]`, `CalibrationRecord`. | Partial | Partial | Additive migration; map onto existing tables rather than duplicating them. |

## PART B — LLM & EMBEDDING CONSOLIDATION

| Item | Exists today | Gap |
|---|---|---|
| Groq / Gemini / OpenRouter clients | `llm_router.py` 1534 lines, three HTTP paths, 21-key roster | Remove all three; single Anthropic path |
| Capacity registry + `route_score` + quota-domain discovery | `llm_capacity.py` 1371 lines, `ROUTE_SCORE_WEIGHTS`, GREEN/YELLOW/RED `TASK_WORKLOAD` | Delete outright |
| Provider policy data | `config/llm_providers.py` 693 lines | Rewrite: model tier per task, two models only |
| Model probe script | `scripts/probe_llm_models.py` 436 lines | Delete |
| Env keys | 21 `*_API_KEY_*` slots in `core/config.py` | Replace with `ANTHROPIC_API_KEY` + `VOYAGE_API_KEY` |
| Embeddings | `embeddings.py` — BGE-M3, 1024-dim, dev fallback | Voyage-context-4, pinned to 1024 dims so no pgvector migration |
| Reliability | retries / backoff / breaker in `llm_router` | Keep; simplify failure classification to 401/403/429/5xx/timeout |
| `llm_provider_keys` table | Model + migration + admin health UI | Keep the table (historical rows), stop reading it for routing |
| Tests naming providers | `test_llm_router`, `test_route_distribution`, `test_llm_key_telemetry`, `test_llm_task_routing`, `test_tracing`, `test_build_2026_07_27` | Rewrite against the single-vendor layer |

## PART C — UI

| Item | Exists today | Gap |
|---|---|---|
| Brand palette | **Indigo-violet** `#5028E0` in `globals.css` | Must become navy `#012654` / teal `#00888A` — and the current brand is precisely the purple Impeccable's detectors ban |
| `DESIGN.md` / `PRODUCT.md` | Absent | Author both; DESIGN.md in the 9-section format |
| Logomark | `components/brand/logo.tsx`, flat SVG | Add a Three.js R+P scene, landing/login only |
| Dark mode | Present (`globals.css` token swap) | Re-derive for navy/teal; verify contrast |
| `impeccable detect` in CI | No CI at all beyond `deploy.yml` | Add |
| Brand source images | `logo300.jpeg` / `banner300.jpeg` in `~/Downloads/readypick`, **not** in the repo | Sample precisely, vendor into `frontend/public/brand/` |

## PART D — AWS MIGRATION

| Item | Exists today | Gap |
|---|---|---|
| GCP references | 37 files: `infra/gcp/deploy.sh`, 5 `scripts/*.sh`, `.github/workflows/deploy.yml` (418 lines), `resume_storage.py`, `document_storage.py`, `docs/DEPLOY_GCP*.md`, `ESD.md`, `CLAUDE.md`, `README.md`, `frontend/lib/api.ts`, tests | Remove / replace all |
| Object storage | `google-cloud-storage` (GCS signed URLs) | S3 + presigned URLs, same interface |
| Terraform | **None** | 7 modules + 2 environments |
| CI/CD | `deploy.yml` — Cloud Run, no required reviewer (the finding this spec cites) | Rebuild for ECR/ECS with an explicit `production` environment reviewer gate |
| Digest verification | `scripts/promote.sh`, `smoke-test.sh` (Cloud Run revision model) | AWS-equivalent; discipline preserved |
| Live deploy | n/a | **Must NOT run.** `terraform apply` against production is a scope failure |
