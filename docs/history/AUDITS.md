# Audits, reconciliations and point-in-time reports

**Status:** history record. Not an instruction. Each entry says what was examined, the headline result, what became of it, and where the original is. Retrieve any original with `git show 73103c3:<path>` (see [SUPERSEDED.md](SUPERSEDED.md) for the note on that commit).
Findings that are still open were copied into [../decisions/OPEN.md](../decisions/OPEN.md); nothing below needs to be read to work on the product.

## Spec-doc5 and spec-doc6 phases (August 2026)

| Record | Date | Headline | Fate |
|---|---|---|---|
| `docs/history/GAP_MATRIX.md` (RPN spec-doc5 gap matrix) | 2026-08-28 | Found that the Runbook (RPN-PHIL-001) the spec cited as authoritative was not in the repository; Part A was built from restated mechanics with marked assumptions | The Runbook arrived 2026-08-29 (now v1.4); every marker reconciled |
| `docs/history/GAP_MATRIX_V2.md` (RPN-GAP-002) | 2026-08-29 | Part A was built and tested but reachable from no live path; method and the command that settles it | Part A went live; the reachability test replaced the grep ([../engineering/testing.md](../engineering/testing.md)) |
| `docs/history/CONTRADICTIONS.md` (RPN-CONTRA-001) | 2026-08-29 | 47 contradictions between documents and the repository; 41 resolved by the precedence table; six defaulted for owner review (C19, C32, C34, C35, C37, C46); seven were defects in spec-doc6's own citations of the RBAC spec | Precedence policy kept in [../engineering/documentation-policy.md](../engineering/documentation-policy.md); the six were resolved in later releases |
| `docs/history/PHASE0_FINDINGS.md` (RPN-P0F-001) | 2026-08-29 | The decisions a human had to make; the 39 versus 40 aspect question answered against the repository; the owner supplied the RBAC and Dashboard specs mid-session | All acted on |
| `docs/history/RUNBOOK_RECONCILIATION.md`, `RUNBOOK_EDITS.md`, `RUNBOOK_OPEN_QUESTIONS.md` | 2026-08-29 | Nine assumptions about the Runbook were all wrong (0 confirmed, 8 corrected, 1 corrected in part); the Runbook went v1.0 to v1.1, then v1.2 and v1.3 on the owner's instruction when every open question (22 raised) was closed; v1.4 followed | Closed. The data lives in `backend/app/services/hiring/runbook_data/` with a parity test |
| `docs/history/LEGACY_RESET_SURVEY.md` | 2026-08-28 | A read-only survey of 51 tables for the legacy reset, written against the premise that gate G1 was enforced (it was not) | Path kept as a brief record because `app/scripts/legacy_reset.py --survey` writes there |
| `docs/operations/TEST_BASELINE.md` | 2026-08-29 | Suite baseline: 1755 passed, 10 failed (environmental), 51 skipped; the Windows `postgresql-x64-13` service on port 5432 made 71 integration tests skip while the suite stayed green | Led to the test stack on ports 55432, 6381, 9101 ([../../CONTRIBUTING.md](../../CONTRIBUTING.md)) |

## The 2026-08-07 execution session (diagnostics)

`docs/history/diagnostics/` held per-change verification notes for ten production changes (private regional asset storage, bulk skill editing, assessment relevance, an optional proctoring scaffold, residual pipeline cleanup, report authenticity and PDF export, AI Reach semantic matching, identity and session clarity with the remaining RLS gap, agent loop architecture, latency), a Phase 0 blocking diagnostic (three of four P0 premises could not be reproduced against production), a cost report for the Google Cloud platform (2026-08-24, since decommissioned), a failures list and a session summary. All described a platform that has since moved to AWS. The notes were removed; the evidence artifacts remain beside this file: `diagnostics/phase0_raw_sql_rls_audit.txt`, `diagnostics/change6-production-report.pdf`, six screenshots, and `baseline/product-docs.docx` and `baseline/tech-docs.docx` (the original specdoc4 baselines).

## Combined audit of 2026-09-23

`docs/history/COMBINED_AUDIT_2026-09-23.md` held three audits (a local working-folder audit and two repository architecture audits). Headline: the product was substantially built, and what went wrong was where pieces met: creating a job published it; applying created an assessment session silently; candidates could not use the BGV screens (staff-only routes); the retrieval index was write-only; large parts of the agent framework were never run; a delivered report could be rewritten in place. The 2026-09-28 close-out checked every item of Part 1 sections 3 to 5 and the master prompt's checklist against `origin/main`: each is closed (a named test pins it), resolved by deletion (a removal sweep keeps it gone), or listed as open. The open ones are in [../decisions/OPEN.md](../decisions/OPEN.md) (Q-22, Q-24, Q-25, Q-26 and A-20); see [RELEASES.md](RELEASES.md).

## Engineering audit, 2026-09-11

`docs/architecture/ENGINEERING_AUDIT_2026-09-11.md` measured the repository against a 71-dimension "service company to product company" brief with seventeen deliverables. Three real gaps were found and fixed. Refused on purpose: RDS Proxy (pinning), microservices and CQRS, a repository layer over SQLAlchemy, virtualisation and a global store. Debt it named is in [../decisions/OPEN.md](../decisions/OPEN.md) Q-32.

## Production hardening, 2026-09-17

| Record | Result |
|---|---|
| `docs/verification/SECURITY_REPORT.md` | 19 findings. Critical: Next.js was in an unauthenticated-RCE range; an untimed Redis client sat on the RBAC hot path. High: one security header only; portals and token links were indexable; the inbound-email webhook was unauthenticated; interactive API docs were served on the live site; staging and production deploy lanes were latently destructive; `is_production` is false on pilot and five guards rest on it (SEC-24, still open: Q-28). All fixed unless noted. Not verifiable then: CSP against Razorpay and Google sign-in, placeholder secrets, rate limits under load (Q-29). |
| `docs/verification/PERFORMANCE_REPORT.md` | Seven findings. Fixed: agent tool cache not tenant-scoped; N+1 in the email recipient loader; missing ANN index on `jobs.embedding`; `recharts` in an initial bundle. Left: a semantic stage defeating the HNSW index (legacy matching since deleted); no `audit_log` index for the cross-tenant scan (Q-32). Static read only, no profiling. |
| `docs/verification/SEO_REPORT.md` | Public surface, `robots`, sitemap and the indexability of everything that must stay out of the index. Limitation: JSON-LD is client-rendered (Q-32). Lighthouse not run. |
| `docs/verification/PRODUCTION_READINESS.md` | "Production" is the pilot environment; the go/no-go gates passed and pilot was deployed on 2026-09-17 and verified by digest (api 31 to 32, frontend 19 to 20, analysis unchanged). |
| `docs/verification/PROCTORING_AND_FORMATS_VERIFICATION.md` | What was executed for the 2026-09-02 proctoring and question-format work, and what was not (live camera and microphone behaviour in real browsers, the analysis service models). |

Two documents in `docs/verification/` stay current because a test reads them: `VERIFICATION_RESULTS.md` and `VERIFICATION_PENDING.md`. `AI_UPGRADE_BASELINE.md` stays as a dated measurement that later work is compared against.
