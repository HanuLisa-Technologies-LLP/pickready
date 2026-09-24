# Vivekium documentation

Everything written about this product lives under `docs/`. Five files stay at
the repository root because a tool or a convention resolves them there:
[`README.md`](../README.md) (entry point), [`claude.md`](../claude.md) (build
conventions for AI agents), [`CONTRIBUTING.md`](../CONTRIBUTING.md), and
[`PRODUCT.md`](../PRODUCT.md) + [`DESIGN.md`](../DESIGN.md) (the Impeccable
design tooling reads them from the project root).

## Start here

| I want to… | Read |
|---|---|
| Understand what the product does | [product/PRD.md](product/PRD.md) |
| Understand how it is built | [architecture/ESD.md](architecture/ESD.md) |
| Run it locally | [operations/SETUP.md](operations/SETUP.md) |
| Deploy it | [operations/DEPLOY_AWS.md](operations/DEPLOY_AWS.md) |
| Get the data back | [operations/DISASTER_RECOVERY.md](operations/DISASTER_RECOVERY.md) |
| Know who may do what | [spec/RBAC_SPECIFICATION.md](spec/RBAC_SPECIFICATION.md) |
| Know how candidates are evaluated | [product/Readypick Hiring Philosophy.md](product/Readypick%20Hiring%20Philosophy.md) |
| Follow a candidate or a job end to end | [spec/HIRING_WORKFLOW.md](spec/HIRING_WORKFLOW.md) |
| Set up a job: JD, SWOT, Skills, publish, the lock | [spec/JOB_SETUP_FLOW.md](spec/JOB_SETUP_FLOW.md) |
| Add, route or debug a background task | [spec/BACKGROUND_WORK.md](spec/BACKGROUND_WORK.md) |
| Prove a change works end to end, or reproduce a failure | [spec/HARNESS.md](spec/HARNESS.md) |
| Change code without breaking a rule | [../claude.md](../claude.md) |

Current Tatva authority: Sutra proposes the initial assessment matrix; the
authorized Hiring Manager decides its criteria; Save Matrix enriches and freezes
the reviewed version for downstream assessment. Company Profile supplies relevant
company context. Company DNA is a retired feature mentioned only in historical
records and migration history.

## Precedence, when two documents disagree

Settled 2026-08-29 and unchanged. Higher wins:

1. [spec/RBAC_SPECIFICATION.md](spec/RBAC_SPECIFICATION.md) — authorization,
   tenant isolation, role ownership, job lifecycle, audit.
2. [product/Readypick Hiring Philosophy.md](product/Readypick%20Hiring%20Philosophy.md)
   — the Runbook (RPN-PHIL-001 v1.1). Authoritative for evaluation mechanics.
3. The phase specification in force (spec-doc6, recorded in [../claude.md](../claude.md)).
4. [spec/CANDIDATE_DASHBOARD_SPECIFICATION.md](spec/CANDIDATE_DASHBOARD_SPECIFICATION.md)
   — the candidate list surface only, and
   [spec/HIRING_WORKFLOW.md](spec/HIRING_WORKFLOW.md) — the end-to-end journeys
   and their eight gates. The two do not overlap: one governs a table, the
   other governs a sequence.
5. [product/PRD.md](product/PRD.md), then [architecture/ESD.md](architecture/ESD.md).

[spec/ARCHITECTURE_DIRECTION_2026-08-28.md](spec/ARCHITECTURE_DIRECTION_2026-08-28.md)
is ADVISORY and sits below everything above. Useful for intent, never a
requirement.

**"Restrict more when unsure" applies only where the higher authority is
SILENT.** It never licenses overriding an affirmative grant in a higher-ranked
document.

## The map

### `product/` — what the product is
| File | What it holds |
|---|---|
| [PRD.md](product/PRD.md) | Product requirements: users, journeys, features, rules |
| [Readypick Hiring Philosophy.md](product/Readypick%20Hiring%20Philosophy.md) | The Runbook, RPN-PHIL-001 v1.1. Loaded by code at this path — see below |

### `architecture/` — how it is built
| File | What it holds |
|---|---|
| [ESD.md](architecture/ESD.md) | Engineering and system design, implementation-aligned |
| [AI_RUNTIME.md](architecture/AI_RUNTIME.md) | The AI runtime AS BUILT: retrieval, the reranker, the scoring lock, the eval layer and the release gate. Names what is NOT built, in the same document |
| [ENGINEERING_AUDIT_2026-09-11.md](architecture/ENGINEERING_AUDIT_2026-09-11.md) | The LLD brief's seventeen audit deliverables, measured against this tree: three real gaps found and fixed, the refusals with reasons, and the honest debt inventory |
| [adr/](architecture/adr/) | Architecture decision records |

### `spec/` — normative specifications
| File | What it holds |
|---|---|
| [RBAC_SPECIFICATION.md](spec/RBAC_SPECIFICATION.md) | Precedence rank 1. Roles, capabilities, isolation, lifecycle |
| [HIRING_WORKFLOW.md](spec/HIRING_WORKFLOW.md) | The end-to-end candidate and client journeys, and the eight gates that hold them together |
| [JOB_SETUP_FLOW.md](spec/JOB_SETUP_FLOW.md) | Job setup since the Vivekium release: the draft-only create, the dispatched SWOT, the Skills step and Save Skills, the three-step publish gate, the contract lock, the sweeps and the scenarios that pin each rule. Supersedes HIRING_WORKFLOW's Gates 3 and 4 |
| [AI_RUNTIME_UPGRADE.md](spec/AI_RUNTIME_UPGRADE.md) | RPN-AI-UP-001, precedence rank 3a. The AI runtime, retrieval, evaluation and AI security. **Read it beside [verification/AI_UPGRADE_BASELINE.md](verification/AI_UPGRADE_BASELINE.md)**, which records where its own section 2 audit turned out to be wrong |
| [CANDIDATE_DASHBOARD_SPECIFICATION.md](spec/CANDIDATE_DASHBOARD_SPECIFICATION.md) | The candidate list surface |
| [PROJECT_EVIDENCE_INTELLIGENCE.md](spec/PROJECT_EVIDENCE_INTELLIGENCE.md) | Project evidence: pipeline, security, retention |
| [PROCTORING.md](spec/PROCTORING.md) | Mandatory assessment monitoring: principles, paths, the report, retention |
| [ASSESSMENT_QUESTION_FORMATS.md](spec/ASSESSMENT_QUESTION_FORMATS.md) | The six question formats and the evidence-dominance rule |
| [BACKGROUND_WORK.md](spec/BACKGROUND_WORK.md) | How background work is dispatched, routed, retried and scheduled after Celery |
| [HARNESS.md](spec/HARNESS.md) | RPN-HARNESS-001. The engineering harness: scenarios, fault injection at the real seams, run identity and replay, the layered evaluators, baselines and the tiers CI fails on. It is the contract `backend/harness/` conforms to, and it records the findings the harness itself produced |
| [VIVEKIUM_SPRINT_FEATURES.md](spec/VIVEKIUM_SPRINT_FEATURES.md) | The eight-feature sprint brief, RECONCILED. Its section 3 is the conflict register: seven places where the brief and a standing hard rule cannot both be true. Read that before building anything from it |
| [ARCHITECTURE_DIRECTION_2026-08-28.md](spec/ARCHITECTURE_DIRECTION_2026-08-28.md) | Advisory direction, not a requirement |

### `operations/` — running it
| File | What it holds |
|---|---|
| [SETUP.md](operations/SETUP.md) | Local development from a clean clone |
| [DEPLOY_AWS.md](operations/DEPLOY_AWS.md) | AWS deployment runbook |
| [DISASTER_RECOVERY.md](operations/DISASTER_RECOVERY.md) | Restoring the database, and what Redis and S3 do not restore with it. Written, never rehearsed, and says so |
| [DATABASE_CREDENTIAL_MIGRATION.md](operations/DATABASE_CREDENTIAL_MIGRATION.md) | Rotating database credentials |
| [JUDGE0_RUNBOOK.md](operations/JUDGE0_RUNBOOK.md) | The code sandbox: topology, monthly cost, the staged rollout, and the outage runbook. Not yet provisioned, and says so |
| [TEST_BASELINE.md](operations/TEST_BASELINE.md) | What the suite covers and the current numbers |
| [SKIPS.md](operations/SKIPS.md) | The declared skip inventory, enforced by a test |

### `reference/` — lookup material
| File | What it holds |
|---|---|
| [RBAC.md](reference/RBAC.md) | Implemented capability reference, including what is not yet wired |

### `verification/` — what has actually been proven
| File | What it holds |
|---|---|
| [VERIFICATION_RESULTS.md](verification/VERIFICATION_RESULTS.md) | Live vendor runs that succeeded, with dates |
| [VERIFICATION_PENDING.md](verification/VERIFICATION_PENDING.md) | What remains unproven, stated plainly |
| [PROCTORING_AND_FORMATS_VERIFICATION.md](verification/PROCTORING_AND_FORMATS_VERIFICATION.md) | What was executed for proctoring and question formats, and what was not |
| [AI_UPGRADE_BASELINE.md](verification/AI_UPGRADE_BASELINE.md) | RPN-AI-UP-001 W0. What was reachable, what was exercised, and the live pilot row counts on 2026-09-09. A measurement, not a description: never edit it to match new behaviour |
| [SECURITY_REPORT.md](verification/SECURITY_REPORT.md) | The 2026-09-17 production hardening pass. Nineteen findings by severity, each with its root cause, its fix and what was actually run to verify it, plus a closing list of what could NOT be verified |
| [PRODUCTION_READINESS.md](verification/PRODUCTION_READINESS.md) | Whether this is safe to deploy and what is still owed. Read the go/no-go table first |
| [PERFORMANCE_REPORT.md](verification/PERFORMANCE_REPORT.md) | Query patterns, indexes, caching and bundle cost. Reasoned from code and tests, not profiled: it says so at the top and again at the bottom |
| [SEO_REPORT.md](verification/SEO_REPORT.md) | The public web surface, and the indexability of everything that must stay OUT of the index |

These two are load-bearing: `backend/tests/test_no_live_vendor_claims.py`
reads them, so a claim about a live call must be evidenced in
`VERIFICATION_RESULTS.md` or the suite fails.

### `history/` — point-in-time artifacts, not current truth
Analysis, surveys and phase logs kept for provenance. **Do not read these as a
description of how the product works today**; they record what was true when
they were written. Includes `CONTRADICTIONS.md`, `GAP_MATRIX*.md`,
`build-log.md`, `PHASE0_FINDINGS.md`, the `RUNBOOK_*` reconciliation set,
`LEGACY_RESET_SURVEY.md`, `UNTRACKED_INVENTORY.md`, `diagnostics/` (verification
evidence, screenshots and reports) and `baseline/` (the original specdoc4
`.docx` files).

## Documents that code resolves on disk

Moving any of these breaks a test or a service. The path is part of the
contract:

| Path | Read by |
|---|---|
| `docs/product/Readypick Hiring Philosophy.md` | `services/hiring/runbook_data/`, `tests/test_runbook_parity.py`, `tests/test_runbook_reconciliation.py` |
| `docs/operations/SKIPS.md` | `tests/test_skip_inventory.py` |
| `docs/verification/VERIFICATION_*.md` | `tests/test_no_live_vendor_claims.py` |
| `docs/history/LEGACY_RESET_SURVEY.md` | written by `app/scripts/legacy_reset.py --survey` |
| `.impeccable-exceptions.md` (root) | `frontend/scripts/impeccable-gate.mjs` |

The Runbook's filename uses SPACES and every document writes it with
underscores. That is a known wart, kept deliberately: renaming it would touch
103 citations and buy nothing.
