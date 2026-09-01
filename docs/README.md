# ReadyPick documentation

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
| Know who may do what | [spec/RBAC_SPECIFICATION.md](spec/RBAC_SPECIFICATION.md) |
| Know how candidates are evaluated | [product/Readypick Hiring Philosophy.md](product/Readypick%20Hiring%20Philosophy.md) |
| Change code without breaking a rule | [../claude.md](../claude.md) |

## Precedence, when two documents disagree

Settled 2026-08-29 and unchanged. Higher wins:

1. [spec/RBAC_SPECIFICATION.md](spec/RBAC_SPECIFICATION.md) — authorization,
   tenant isolation, role ownership, job lifecycle, audit.
2. [product/Readypick Hiring Philosophy.md](product/Readypick%20Hiring%20Philosophy.md)
   — the Runbook (RPN-PHIL-001 v1.1). Authoritative for evaluation mechanics.
3. The phase specification in force (spec-doc6, recorded in [../claude.md](../claude.md)).
4. [spec/CANDIDATE_DASHBOARD_SPECIFICATION.md](spec/CANDIDATE_DASHBOARD_SPECIFICATION.md)
   — the candidate list surface only.
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
| [adr/](architecture/adr/) | Architecture decision records |

### `spec/` — normative specifications
| File | What it holds |
|---|---|
| [RBAC_SPECIFICATION.md](spec/RBAC_SPECIFICATION.md) | Precedence rank 1. Roles, capabilities, isolation, lifecycle |
| [CANDIDATE_DASHBOARD_SPECIFICATION.md](spec/CANDIDATE_DASHBOARD_SPECIFICATION.md) | The candidate list surface |
| [PROJECT_EVIDENCE_INTELLIGENCE.md](spec/PROJECT_EVIDENCE_INTELLIGENCE.md) | Project evidence: pipeline, security, retention |
| [ARCHITECTURE_DIRECTION_2026-08-28.md](spec/ARCHITECTURE_DIRECTION_2026-08-28.md) | Advisory direction, not a requirement |

### `operations/` — running it
| File | What it holds |
|---|---|
| [SETUP.md](operations/SETUP.md) | Local development from a clean clone |
| [DEPLOY_AWS.md](operations/DEPLOY_AWS.md) | AWS deployment runbook |
| [DATABASE_CREDENTIAL_MIGRATION.md](operations/DATABASE_CREDENTIAL_MIGRATION.md) | Rotating database credentials |
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
| `docs/product/Readypick Hiring Philosophy.md` | `services/hiring/dna_compilation.py`, `tests/test_runbook_parity.py`, `tests/test_runbook_reconciliation.py` |
| `docs/operations/SKIPS.md` | `tests/test_skip_inventory.py` |
| `docs/verification/VERIFICATION_*.md` | `tests/test_no_live_vendor_claims.py` |
| `docs/history/LEGACY_RESET_SURVEY.md` | written by `app/scripts/legacy_reset.py --survey` |
| `.impeccable-exceptions.md` (root) | `frontend/scripts/impeccable-gate.mjs` |

The Runbook's filename uses SPACES and every document writes it with
underscores. That is a known wart, kept deliberately: renaming it would touch
103 citations and buy nothing.
