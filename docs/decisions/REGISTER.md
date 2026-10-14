# Decision register

**Status:** current. **Reviewed:** 2026-10-14 (documentation audit; entries were read against code names, tests and specs, not re-run).
**What this is:** every owner and architecture decision that is in force today, one row each. A decision that was reversed or replaced is not here; it is in [../history/RULINGS.md](../history/RULINGS.md).
**What this is not:** a place for detail. The "Where" column names the document or test that holds the detail.

## How to read it

| Status | Meaning |
|---|---|
| **Implemented** | Decided and built. Evidence is the named test or spec. Rows carried over from earlier records were not re-traced on 2026-10-14. |
| **Decided, pending** | The owner has ruled; the code, configuration or deployment does not match yet. The row is also listed in [Pending changes](#pending-changes). Never describe these as working. |
| **Proposed** | A design the owner wants validated. Not approved for adoption. |

Precedence when two entries disagree: the later dated entry wins; an owner statement beats an inferred rule ([../engineering/documentation-policy.md](../engineering/documentation-policy.md)). Unanswered questions are in [OPEN.md](OPEN.md); an unanswered question is never treated as approved.

---

## Owner decisions of 2026-10-14

These replace earlier rulings where noted. All are **Decided, pending** unless marked.

| ID | Decision | Replaces | Where |
|---|---|---|---|
| J-10 | **Job approval flow.** Recruiter creates the job, prepares the JD and assigns a Hiring Manager. Hiring Manager reviews the JD and prepares Skills and the SWOT, then submits. HR or Recruitment Manager approves, or returns it. Recruiter publishes only an approved job. "Skills Matrix" in the owner wording means the Skills step (no matrix, no weights). | CONTRACT v10 publish rule (JD + saved skills only); the 2026-09-25 deletion of the approval chain; RBAC spec 9.6 and 17 (no HR approval step) | [../spec/JOB_SETUP_FLOW.md](../spec/JOB_SETUP_FLOW.md) |
| J-11 | **Who may do what.** Super Admin: full authority, overrides audited, but follows the same steps (cannot skip approval). HR or Recruitment Manager: create jobs, edit JD, Skills and SWOT, assign Hiring Managers, approve; **cannot publish**. Recruiter: create jobs, edit assigned draft JDs, publish assigned approved jobs; **cannot edit Skills or SWOT, cannot approve**. Hiring Manager: edit assigned JD, Skills, SWOT; cannot publish or approve. Interview Manager: no job preparation or publishing. | RBAC spec matrix cell "Publish job: HR Manager YES*" | [../spec/RBAC_SPECIFICATION.md](../spec/RBAC_SPECIFICATION.md), [../reference/RBAC.md](../reference/RBAC.md) |
| J-12 | **Publish needs** a completed JD, saved Skills and HR approval of the exact revision being published. The SWOT is not a publication blocker. | v10: publish needs JD + skills only | [../spec/JOB_SETUP_FLOW.md](../spec/JOB_SETUP_FLOW.md) |
| J-13 | **Approval validity.** A material change to the JD, job details, Skills or hiring-relevant SWOT cancels the approval and needs fresh HR review before publishing. Cosmetic formatting and internal notes do not. Approval history is kept, every change is recorded, and publication uses the exact approved revision. | none (new) | same |
| J-14 | **Independent approval.** Anyone who materially prepared the JD, Skills, SWOT or requirements cannot ordinarily approve that revision; another HR or Recruitment Manager must. The Super Admin may approve when no independent approver exists, including an audited self-approval exception for small companies. Record approver, justification, revision and audit history. Cosmetic edits do not count as preparation. No unauthorised role may ever approve. | none (new) | same |
| J-15 | **Return for corrections.** HR chooses the recipient: back to the Recruiter (JD problems) or to the Hiring Manager (Skills problems), with a written reason. | none (new) | same |
| J-16 | **SWOT timing.** The SWOT may be written and edited at any time before or after publishing and is never shown to candidates. A saved SWOT is mandatory before assessment; it freezes when the first assessment starts. | v10: "SWOT editable independently, also after the freeze" | [../spec/JOB_SETUP_FLOW.md](../spec/JOB_SETUP_FLOW.md) |
| S-20 | **Coding questions without a sandbox.** Candidate code is not executed and no sandbox (Judge0) is required. The candidate writes code; a **static checker tool** analyses it without running it (type and syntax errors) and AI reviews and evaluates it. Coding questions go only to coding (computing) roles. Per-language checker choice and the scoring split are unresolved ([OPEN.md](OPEN.md)). | The 2026-09-25 sandbox design (hidden tests, Run button, 70/30 score, answer-key table) and the 2026-09-28 "on hold" ruling | [../spec/CODING_ASSESSMENT.md](../spec/CODING_ASSESSMENT.md) |
| S-21 | **Yukti weights are provisional.** The current six-part weights (Must-have 40, Nice-to-have 15, experience 15, role fit 15, company need 5, application answers 10) stay as provisional defaults, not permanently approved. The target is a five-part suitability model with provisional weights Must-have 45, Nice-to-have 15, role fit 20, experience 15, company need 5. Compensation, notice period and document readiness are shown separately, outside the score. Keep evidence grounding, missing-data handling, policy versioning and assessment blending. Validate before production adoption. **No code change until the owner approves the design.** Status: **Proposed**. | none (D2 six-part structure stays until then) | [../spec/RETRIEVAL.md](../spec/RETRIEVAL.md), `backend/app/services/yukti/config.py` |
| A-20 | **Retrieval-time prompt-injection screen is mandatory.** Retrieved resume and document text is screened before a model sees it. The screen exists in code (`services/safety/content.py`) but nothing calls it. | The earlier "not in force, owner to decide" note | [../engineering/security.md](../engineering/security.md) |
| I-20 | **Email.** Primary mailbox `manjunath@readypick.ai`. Alias and official public contact `contact@readypick.ai`. Platform transactional email is sent through **Amazon SES only**, from `contact@readypick.ai` (subject to verified sender setup). No SMTP fallback. Emails sent as a company's own approved sender (candidate lifecycle mail) stay as they are, also through SES. No other mailbox or alias may be invented. Every `@pickready.app` address is obsolete and needs replacing. | The 2026-09-06 "email transport is deployment data, smtp or ses" rule and the Gmail SMTP rule | [../spec/CANDIDATE_COMMUNICATIONS.md](../spec/CANDIDATE_COMMUNICATIONS.md) |
| W-20 | **Graphify is optional.** Use it only for unfamiliar architecture, cross-module dependencies and complex relationships, when its graph exists and is current. No mandatory queries, no blocking hooks, no repeated context injection. The `PreToolUse` hooks in `.claude/settings.json` were removed on 2026-10-14 (owner approved); the untracked local `.codex/hooks.json` is pending. Status: **Implemented** for Claude Code; Codex hook file **pending**. | The "MANDATORY graphify query" instruction | [../engineering/agent-workflow.md](../engineering/agent-workflow.md) |
| W-21 | **Latest owner statement wins.** A newer owner instruction overrides older ones without re-asking. Questions to the owner explain current behaviour, the problem, a real example, each option's consequences and a recommendation. | none | [../engineering/documentation-policy.md](../engineering/documentation-policy.md) |
| W-22 | **Documentation-only tasks** change documents and agent instruction files only. Decisions are recorded, never implemented, and appear under Pending changes. | none | [../engineering/documentation-policy.md](../engineering/documentation-policy.md) |

---

## Product and naming

| ID | Decision | Status | Where |
|---|---|---|---|
| P-01 | The product is **Vivekium** (Varpitech LLP). The domain `readypick.ai` stays. Deliberate `pickready` and `readypick` identifiers (environment variable names, `pickready.*` task names, infrastructure names, database roles, migrations) are not renamed. | Implemented | [../../CLAUDE.md](../../CLAUDE.md) |
| P-02 | **No number ever reaches a client, with no exception.** Scores are internal; words are made server-side. `match_percent` is deleted and `harness/probes.SANCTIONED_NUMERIC_FIELDS` is empty. Operational counts and a countdown clock are not assessment signals. | Implemented | `test_platform_audit.py` |
| P-03 | **One rating scale, four grades**: Highly Matching, Matching, Moderately Matching, Not Matching. Cut points 90, 75, 60, inclusive upward. `services/rating.py` is the only definition. | Implemented | `test_grade_scale_consistency.py` |
| P-04 | **Tatva Assessment** is the process; **PRISM Report** is the document (subtitle "Evidence-Based Role Intelligence & Suitability Mapping"). The two names are never used for each other. | Implemented | `report_pdf.py` |
| P-05 | Recruiter-visible vocabulary is JD, SWOT, Skills, AI Match, Tatva Assessment, PRISM Report, Proctoring Report. Not PPI, matrix, framework, matching categories, AI Score or retake. Code identifiers keep their names. | Implemented | `test_user_facing_copy_names.py` |
| P-06 | PRISM section order is fixed: AI Match, Overall Assessment, Must-have, Nice-to-have, Behavioural, Gap Analysis and Action Plan, Validation, Proctoring. Three radar charts (Overall, Must-have, Nice-to-have), no number on any axis. Screen and PDF share one serializer. | Implemented | [../spec/ASSESSMENT_FLOW.md](../spec/ASSESSMENT_FLOW.md) |
| P-07 | A PRISM Report is **immutable**: insert-only at the database (trigger and revoked UPDATE). There is no retake. | Implemented | migration 0130 |
| P-08 | **Proctoring is mandatory**, no flag. A lost camera or microphone pauses the assessment (Path P) within set limits; the server decides every threshold. Session media is stored compressed in S3 and purged 90 days after the session or at the job-closure purge, whichever is first. | Implemented | [../spec/PROCTORING.md](../spec/PROCTORING.md) |
| P-09 | Candidate navigation is New Jobs, Applied Jobs, Updates, Messages, My Profile. | Implemented | `frontend/` |
| P-10 | Text is never grey; navy is structure, teal is evidence; the brand teal fails AA as body text on white, so teal text on white is `teal-700`. No gradient between two hues. | Implemented | [../../DESIGN.md](../../DESIGN.md) |
| P-11 | Customer role chain is Super Admin, Recruitment Manager, Recruiter, Hiring Manager. CEO, MD and Functional Head are leaf roles under the Super Admin (CEO and MD read everything and write only their own leadership input; a Functional Head is bound to one department). Permissions are data (`role_permissions` plus a per-user overlay), never a role branch. | Implemented | [../reference/RBAC.md](../reference/RBAC.md) |
| P-12 | **Leadership Intelligence** replaces Drishti. Versions are insert-only; the compiler is deterministic; an AI draft is never saved; Save Skills freezes the version the contract carries. | Implemented | `services/leadership/` |
| P-13 | The Company Profile is current company context; a job snapshots its narrative at creation. Gate 1: no Company Profile `about_company`, no job. | Implemented | [../spec/HIRING_WORKFLOW.md](../spec/HIRING_WORKFLOW.md) |
| P-14 | Reporting, EEO and impact-ratio surfaces are refused: no jurisdiction was named and the customers are Indian entities. Protected-attribute inference is refused outright. | Implemented | [../architecture/AI_RUNTIME.md](../architecture/AI_RUNTIME.md) |

## Commercial

| ID | Decision | Status | Where |
|---|---|---|---|
| C-01 | **Four monthly plans**, all features included, differing only by completed-assessment allowance: Starter 75 for INR 24,000; Growth 200 for INR 55,000; Scale 500 for INR 1,20,000; Pro 1,200 for INR 2,40,000. Prices exclude GST. Renews monthly; no annual commitment or lock-in; cancel anytime, effective at the end of the paid cycle. No setup fee. | Implemented | `services/monthly_plans.py`, migration 0136 |
| C-02 | The 30-day pilot starts on Starter and is paid at signup. | Implemented | same |
| C-03 | One completed assessment spends one credit, including STEM. Incomplete assessments, no-shows and old-profile reviews spend none. Credits pool across all open jobs. Unused monthly credits roll over three months from grant, then expire. | Implemented | `services/credits.py` |
| C-04 | Only a **Starter pack** (75 assessments, INR 24,000 plus GST) can be bought as a top-up. | Implemented | `services/credit_packs.py` |
| C-05 | Public pricing sits in Start beside "Open a role"; the three-month expiry sits next to the prices. Derived per-assessment prices are never shown. Historical purchases and invoices stay readable at the terms they were sold on. | Implemented | `frontend/` |
| C-06 | The credit ledger is the only balance: `SUM(subunits_delta)`, 60 sub-units per credit, integer only, every write carries a unique idempotency key. Gates ask `services/entitlements.py`; zero credits never block login or reads. A completed assessment is charged even into the negative; the next invitation batch is what is blocked. | Implemented | [../engineering/backend.md](../engineering/backend.md) |
| C-07 | Demonstration tenants are exempt from billing refusals, never from billing records (`tenants.is_demo`). | Implemented | `services/entitlements.py` |

## Auth, security and privacy

| ID | Decision | Status | Where |
|---|---|---|---|
| A-01 | Identity is Firebase (Google, or email and password). Phone sign-in and every login OTP are removed. Database roles stay authoritative; Firebase is identity only. | Implemented | [../engineering/security.md](../engineering/security.md) |
| A-02 | A CAPTCHA, server-drawn and server-checked, guards every auth surface and fails closed. No setting disables it. | Implemented | `services/captcha.py` |
| A-03 | A security code (not a login OTP) serves company registration and password reset or change only, from the platform sender. The server resets and changes passwords and revokes every session. | Implemented | `services/security_codes.py` |
| A-04 | Google never authenticates a company role; candidates keep Google. Team members are invite-only and the server creates the password sign-in for exactly the invited email. | Implemented | `firebase_auth.assert_provider_allowed` |
| A-05 | Self-registration: register, security code, onboarding tenant, first plan payment, password, active. Payment is derived from billing rows, never stored; a browser callback never activates. | Implemented | `services/company_onboarding.py` |
| A-06 | A session dies with the browser; the idle deadline (30 minutes) moves only on real user activity (`X-User-Activity`); Redis unavailable is 503, never fail-open. | Implemented | `services/auth_sessions.py` |
| A-07 | One person, one candidate record, resolved by `candidates.user_id`; email matching happens only at sign-in on a Firebase-verified address. | Implemented | `services/candidate_identity.py` |
| A-08 | Every tenant-scoped query goes through the RLS-aware session. A cross-tenant read answers 404. | Implemented | [../engineering/database.md](../engineering/database.md) |
| A-09 | Compensation never reaches a model. One module (`services/compensation_guard.py`) strips or redacts it; every prompt builder is registered in `test_ctc_never_in_prompt.py`. | Implemented | same test |
| A-10 | Candidate text and uploaded files are data, never instructions. Candidate projects are never executed. A hidden-text hit is provenance, not a rejection. | Implemented | [../engineering/security.md](../engineering/security.md) |
| A-11 | Embeddings are personal data at rest; erasure reaches vectors and caches. Delete My Profile deletes the Firebase identity in the same transaction. | Implemented | `pickready.cascade_erasure` |
| A-12 | Consent is per item with the verbatim sentence, version and digest, in an append-only table of its own. | Implemented | `consent_catalog.py` |
| A-13 | The application's database credential is its own (`pickready_app`), minted and rotated inside the platform; the DSN carries `ssl=require`. | Implemented | `scripts/rotate-app-db-credential.sh` |

## Job setup, contract and hiring

| ID | Decision | Status | Where |
|---|---|---|---|
| J-01 | Skills are drafted by Sutra from the JD (and the saved SWOT and Company Profile when they exist). The team edits and saves them: at most five per bucket (Must-have, Nice-to-have, Behavioural). No matrix, weight, priority or grade per skill is ever shown. | Implemented | [../spec/JOB_SETUP_FLOW.md](../spec/JOB_SETUP_FLOW.md) |
| J-02 | The **Final Job Posting** (skill names only, alphabetical, never evidence lines or priorities) is previewed before publish and is what candidates see. | Implemented | `assessment_contract.posting_skills` |
| J-03 | The first genuine application **freezes** the JD, title, experience band, grade and Skills, in the same transaction as the application. Refusals are one dated sentence (409). A sourced upload, databank link or invitation never freezes. | Implemented | `test_freeze_at_application.py` |
| J-04 | Candidates are assessed and graded against the locked contract snapshot (`assessment_contract`), never live skill rows. A snapshot is insert-only. | Implemented | `services/assessment_contract.py` |
| J-05 | Save Skills makes exactly one Sutra call (the recorded exception to "no slow work in a request"), before any write, and never holds the lock across it. | Implemented | `services/skills.py` |
| J-06 | A job lifecycle has six states: DRAFT, FINALIZED, PUBLISHED, CANDIDATE_APPLICATIONS, HIRING_PROCESS, CLOSED_ARCHIVED. The approval states of J-10 will extend this; the names are unresolved ([OPEN.md](OPEN.md)). | Implemented | `ck_jobs_lifecycle_state` |
| J-07 | A job is live 30 days plus 5 days grace; the dates are generated columns. Closing a job withholds its assessment data and purges it after 30 days; there is no reopen. | Implemented | [../spec/HIRING_WORKFLOW.md](../spec/HIRING_WORKFLOW.md) |
| J-08 | Sourced is not applied: a sourced candidate cannot be invited because the pipeline edge does not exist. The one invitation writer is `assessment_invitations.invite_batch`. | Implemented | [../spec/HIRING_WORKFLOW.md](../spec/HIRING_WORKFLOW.md) |
| J-09 | The BGV offer gate sits in `apply_transition`: a candidate with declared employers cannot be moved to offer until each is marked verified by a person. Rejection is never blocked. Nothing infers a verdict from a reply. | Implemented | `hiring_pipeline.py` |

## Assessment and grading

| ID | Decision | Status | Where |
|---|---|---|---|
| S-01 | **Miti is the sole grading authority.** Per skill: graded, unanswered (25, an unanswered Must-have fails) or not assessed (no score). No hash fallback. A model failure is "Not assessed", bounded to three attempts. | Implemented | [../spec/ASSESSMENT_FLOW.md](../spec/ASSESSMENT_FLOW.md) |
| S-02 | Questions: one per skill, never below the grade floor (10, 10, 10, 8), so 8 to 15; mix by largest remainder, prose 0.7, coding 0.2, objective 0.1. Coding only for a computing occupation (S-20). Every item is asked; there is no pre-fill, early close or extension. | Implemented | `services/assessment_questions/budget.py` |
| S-03 | The **server keeps the time** (turn stamps, allocation snapshots, pause rows); the client reports no durations. Every answer names `turn_seq`; a stale one is 409. | Implemented | `services/assessment_conversation/timers.py` |
| S-04 | Yukti reads resumes against the saved Skills on fixed parts and blends into one rank key derived in SQL, with the Must-have cap outermost. A model failure is "not assessed", never a substitute score. | Implemented | `services/yukti/` |
| S-05 | Siddhi withholds any statement without a supporting citation and routes the report to a person. There is no bypass flag. | Implemented | `services/siddhi/` |
| S-06 | Evidence RAG is reached only through the tool layer; every registered tool is a bounded read; Miti and Siddhi import neither retrieval nor the index. | Implemented | `test_evidence_rag_wiring.py` |
| S-07 | The PRISM PDF leaves only through gate G4 and the candidate's retention consent. | Implemented | `siddhi/delivery.py` |

## Data, retention and legacy

| ID | Decision | Status | Where |
|---|---|---|---|
| R-01 | Closing a job is a 30-day soft deletion; objects are deleted before rows; a purge that cannot confirm every object gone deletes nothing and retries. | Implemented | `services/job_assessment_retention.py` |
| R-02 | No irreversible drop of a table that may hold customer rows; a table proven empty may be dropped by a migration that counts first and raises. | Implemented | [../operations/LEGACY_TABLES.md](../operations/LEGACY_TABLES.md) |
| R-03 | A project upload is staged temporarily and deleted after derived evidence is persisted; the product stores intelligence, not the project. | Implemented | [../spec/PROJECT_EVIDENCE_INTELLIGENCE.md](../spec/PROJECT_EVIDENCE_INTELLIGENCE.md) |

## Platform and infrastructure

| ID | Decision | Status | Where |
|---|---|---|---|
| I-01 | Hosting is AWS (ECS Fargate, Lambda, RDS PostgreSQL with pgvector, ElastiCache Redis, S3). Pilot (`readypick-pilot`, `ap-south-2`) is the live site; staging and production are defined, not applied. | Implemented | [../operations/INFRA_TOPOLOGY.md](../operations/INFRA_TOPOLOGY.md) |
| I-02 | **No Celery.** Slow work is dispatched (`dispatch`, `dispatch_after_commit`) to Lambda or on-demand Fargate; every `@task` declares `rls`. | Implemented | [../spec/BACKGROUND_WORK.md](../spec/BACKGROUND_WORK.md) |
| I-03 | One model vendor, closed mapping: `gpt-5.6-terra` (judge and write), `gpt-5.6-luna` (extract and classify), `voyage-4` (embeddings), plus the rerank model. No fallback chain. | Implemented | `config/llm_providers.py` |
| I-04 | `main` is the only deployable branch; pilot is built and deployed from a commit on `main`, verified by image digest. | Implemented | [../operations/BRANCH_PROTECTION.md](../operations/BRANCH_PROTECTION.md) |
| I-05 | Development storage hygiene: one Compose project, temporary worktrees, reproducible artifacts are not data. | Implemented | [../development-storage-policy.md](../development-storage-policy.md) |
| I-06 | OpenTelemetry is the only tracer. | Implemented | `services/observability/otel.py` |

---

## Pending changes

Code, configuration or deployment work the owner has decided and nobody has done. A documentation-only task never does these. Delete a row only when the change has shipped and its owning document is updated in the same change.

| # | Change | Driven by | Touches |
|---|---|---|---|
| 1 | Add the approval states, HR approval, return loop, independence rule, revision pinning and the Recruiter-only publish path; remove HR Manager publish; add the SWOT-before-assessment check and SWOT freeze at first start | J-10 to J-16 | `services/rbac`, `services/capabilities.py` (+ seeding migration), `api/job_setup.py`, `api/jobs.py`, lifecycle CHECK, frontend job page, tests |
| 2 | Remove the sandbox: `services/code_execution/`, `coding_assessment` run and key modules, `coding_question_keys`, Judge0 Terraform and settings; add the static checker and AI review path | S-20 | backend, `infra/modules/code_sandbox`, `docs` |
| 3 | Switch email to SES only: remove the SMTP path and `email_transport`; set `platform_from_email` to `contact@readypick.ai` (pilot currently `noreply@readypick.ai`); replace `noreply@pickready.app` (config default and test fixture) and `hello@pickready.app` (two billing-page links) | I-20 | `core/config.py`, `services/email*`, `infra/`, `.env.example`, `frontend/app/(org)/org/billing/page.tsx` |
| 4 | Call the retrieval-time injection screen on every retrieval path | A-20 | `services/rag/`, `services/evidence_retrieval.py`, tests |
| 5 | Remove the Graphify `PreToolUse` hooks from the untracked local `.codex/hooks.json` (`.claude/settings.json` is done) | W-20 | `.codex/hooks.json` |
| 6 | Yukti five-part model, only after the owner approves the validated design | S-21 | `services/yukti/config.py`, prompt, projection, tests |
