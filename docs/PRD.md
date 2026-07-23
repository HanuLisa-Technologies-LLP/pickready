# PickReady — Product Requirements Document (PRD)

| | |
|---|---|
| **Product** | PickReady |
| **Owner** | Hanulisa Technologies LLP |
| **Document status** | Draft v1.0 |
| **Date** | July 2026 |
| **Classification** | Internal / Confidential |

---

## 1. Executive Summary

PickReady is a multi-tenant, enterprise-grade Applicant Tracking & Recruitment Operations platform. Hanulisa Technologies LLP runs recruitment engagements on behalf of client companies: Hanulisa's own Recruiters and HR Managers source and vet candidates, while each client's Hiring Managers review and decide. The platform's differentiator is a structured, auditable pipeline — OTP-only authentication for every actor, a configurable multi-level job approval chain, a reusable candidate "Databank" with AI-driven contextual matching, and mandatory employer-verification before any candidate reaches a hiring manager.

This is a production system, not a prototype: every workflow in this document must be built with proper validation, audit trails, error handling, and role-based access control from day one.

---

## 2. Problem Statement

Recruitment agencies and in-house TA teams typically lose signal at three points: (1) sourcing repeats work already done for a past requisition, (2) candidate claims (employment history, compensation) go unverified until very late — or never, and (3) approval chains for opening a role are informal, slow, and unauditable. PickReady fixes all three by making the Databank, the verification step, and the approval chain first-class, mandatory parts of the product rather than optional add-ons.

---

## 3. Goals & Success Metrics

| Goal | Metric |
|---|---|
| Reduce duplicate sourcing effort | % of shortlisted candidates sourced from Databank vs. fresh, per job |
| Guarantee verified candidate data reaches Hiring Managers | 100% of freshly sourced candidates have completed the 40-aspect + employer-verification flow before Hiring Manager access is granted |
| Speed up job approval | Median time from "Requested" to "Ratified" |
| Improve match quality | % of Highly Matching (≥90%) profiles that reach "Shortlisted" or better |
| Platform reliability | 99.5% uptime on core workflows (job creation, outreach, review) |

---

## 4. Actors & Role Model

The source concept note used "Admin", "Client", "Recruiter", "HR Manager", and "Hiring Manager" somewhat interchangeably. Based on the brainstorm, the following role model is **assumed and locked in** — flag anything below that doesn't match your intent before build starts, since it drives the entire permission and data model:

| Role | Belongs to | Cardinality | Purpose |
|---|---|---|---|
| **Super Admin** | Hanulisa (platform owner) | Few, platform-wide | Onboards new Client tenants; defines the base permission template for Recruiter / HR Manager / Hiring Manager roles; can adjust any tenant's role permissions dynamically; platform-wide visibility for support and operations |
| **Client (Company)** | Client company | 1 per tenant | The account created in the Section 1/2 OTP onboarding flow. Owns the company page, defines the approval hierarchy (Section 3), and creates/manages up to 5 Hiring Manager accounts |
| **HR Manager** | Hanulisa staff, assigned per tenant | Multiple, per tenant | Adds compensation to ratified jobs, resolves JD ambiguity, manages candidate outreach and the 40-aspect + verification flow, operates the HR Review Screen |
| **Recruiter** | Hanulisa staff, assigned per tenant | Multiple, per tenant | Sources fresh candidates, uploads resumes, schedules interviews, updates candidate pipeline status |
| **Hiring Manager** | Client company | **Max 5 accounts per tenant** | Reviews profiles surfaced by HR, acts as one of up to 4 approval levels (Requested/Recommended/Approved/Ratified), and post-approval reviews candidate profiles with Rejected / Shortlisted / Hold |
| **Candidate** | External | Unbounded | Applies via outreach link, fills the 40-aspect questionnaire, tracks own application status |

**Dynamic permissions**: Super Admin can toggle, per tenant, exactly which actions each of Recruiter / HR Manager / Hiring Manager can perform (see §6 Permission Matrix for the default template). This is a real RBAC system, not a hardcoded role check — permissions are data, not code.

---

## 5. Non-Goals / Explicit Assumptions

- No Gmail/Outlook OAuth integration anywhere (explicit client requirement) — all client-domain email goes through PickReady's own transactional layer with the client's domain as From/Reply-To.
- No password-based login anywhere in the system — OTP only, for every actor including Candidates.
- Verifying a candidate's **current** employer is explicitly out of scope for the automated flow (Section 5g) — it requires a separate, consent-based, Recruiter-led initiative and is not part of this build's automated pipeline.
- No email templates are provided by PickReady; each employer/tenant supplies its own (Section 8) — the platform provides a template *editor*, not fixed copy.
- Candidate resumes are never stored on the Candidate Portal between applications — a fresh resume is required per job application (Section 9).

---

## 6. Default Permission Matrix (Super Admin–editable template)

| Capability | Recruiter | HR Manager | Hiring Manager | Client (Company) |
|---|---|---|---|---|
| Create/edit company page | – | – | – | ✅ |
| Create Hiring Manager accounts | – | – | – | ✅ |
| Configure approval levels | – | – | – | ✅ |
| Create/edit Job Description | – | ✅ (post-ratification) | – | – |
| Approve job at assigned level | – | – | ✅ (if assigned a level) | ✅ (if assigned a level) |
| Add compensation to job | – | ✅ | – | – |
| View Databank matches | ✅ | ✅ | – | – |
| Upload freshly sourced resumes | ✅ | – | – | – |
| Trigger AI contextual rating | ✅ | ✅ | – | – |
| Send candidate outreach (40-aspect + verification) | – | ✅ | – | – |
| View HR Review Screen (full 40 aspects) | – | ✅ | ✅ (read-only, granted profiles only) | – |
| Shortlist/Reject/Hold a profile | – | – | ✅ | – |
| Schedule interviews | ✅ | – | – | – |
| Update candidate pipeline status | ✅ | ✅ | – | – |
| View HR/Recruiter dashboard | ✅ (own jobs) | ✅ (own jobs) | – | – |
| Edit any tenant's role permissions | – | – | – | – (Super Admin only) |

This table is the seed data for the permissions engine (see ESD §6), not a hardcoded ACL — Super Admin can vary this per tenant.

---

## 7. Functional Requirements

### 7.1 Authentication — OTP Everywhere
- **FR-1.1**: Every actor (Client, Super Admin, HR Manager, Recruiter, Hiring Manager, Candidate) authenticates via OTP — no passwords.
- **FR-1.2**: Client's *first* login validates both email and mobile via a dual OTP send; subsequent logins accept either channel.
- **FR-1.3**: Changing a registered email or mobile number re-triggers the full dual-OTP validation.
- **FR-1.4**: OTPs expire after a configurable window (default 5 minutes), allow a configurable max retry count (default 5) before lockout, and are rate-limited per identifier to prevent abuse.
- **FR-1.5**: Internal staff (HR Manager, Recruiter, Hiring Manager, Super Admin) use email-OTP; SMS-OTP (via MSG91) is available wherever a mobile number is on file.

### 7.2 Client & Company Onboarding
- **FR-2.1**: After first login, Client creates a company page (brief, culture, policies, benefits).
- **FR-2.2**: Client creates up to 5 Hiring Manager accounts from the company page, each via the OTP-based invite flow.
- **FR-2.3**: Client configures which of the 4 approval levels (Requested/Recommended/Approved/Ratified) are mandatory, and assigns a specific person (internal Hiring Manager or an external approver invited for this purpose) to each active level.

### 7.3 Job Creation & Multi-Level Approval Workflow
- **FR-3.1**: Hiring Manager creates a JD with: Job Title, Department, Level, Reporting To, Reportees, Role, Responsibilities, Accountabilities, Education, Skills, Experience (years), Requirement period.
- **FR-3.2**: The job moves through the Client-configured subset of Requested → Recommended → Approved → Ratified. Each transition is logged with actor, timestamp, and optional remarks.
- **FR-3.3**: A job cannot skip a mandatory level; skipped/inactive levels are bypassed automatically.
- **FR-3.4**: Only once "Ratified" (or the last mandatory level) does the job become visible to HR.

### 7.4 HR Sourcing, Databank Matching & AI Contextual Rating
- **FR-4.1**: On reaching HR, the job becomes editable by HR Manager for compensation and JD-ambiguity fixes.
- **FR-4.2**: The moment a job reaches HR, PickReady auto-surfaces Databank candidates who previously consented (40-aspect Section 5f — Aspect 40) to be matched against future roles, ranked using the hybrid matching pipeline (ESD §8).
- **FR-4.3**: Recruiter reviews the Databank shortlist, sources additional candidates through their own channels, and uploads freshly sourced resumes, linked to the job.
- **FR-4.4**: Databank matches require no re-upload — their existing Profile is reused as-is and is linked automatically.
- **FR-4.5**: Every linked profile (Databank and fresh) receives an AI contextual rating against the JD, bucketed into: Highly Matching (≥90%), Moderately Matching (≥70%), Matching (≥50%), Not Matching (<50%). Tiers are evaluated top-down; a boundary score (e.g., exactly 90%) falls into the **higher** tier.

### 7.5 Candidate Outreach — Data & Employer-Verification
*Applies to freshly sourced candidates only — Databank matches skip this section entirely, reusing their existing Profile.*
- **FR-5.1**: HR emails selected candidates requesting: Full Name (as per PF records/Class X memorandum), Residing City, Age, Gender, updated resume, and the 40-aspect questionnaire (skipping any aspect already covered by a–d).
- **FR-5.2**: HR additionally requests official HR email IDs of up to 3 previous employers.
- **FR-5.3**: For each previous employer supplied, PickReady sends an automated verification request (Designation, DOJ, DOE, Last Drawn CTC, Last Drawn Gross, NOC status, exit-formalities completion, BGV status, and details on educational/address/ID proof and prior experience/compensation) via a secure tokenized web-form link, with LLM-based parsing of a direct email reply as a fallback if the employer doesn't use the link.
- **FR-5.4**: Verifying the candidate's **current** employer is explicitly excluded from this automated flow (see §5 Non-Goals).
- **FR-5.5**: All of FR-5.1–5.3 must be complete before the Recruiter can move the candidate forward.

### 7.6 Candidate Response, Application & Data Parsing
- **FR-6.1**: Candidate receives an outreach link and completes all requested items on their own Candidate Page.
- **FR-6.2**: Candidate-submitted resumes are parsed via LLM extraction (LangChain) into structured fields (skills, experience, education, employment history) and attached to the candidate's record.

### 7.7 HR Review Screen
- **FR-7.1**: Home screen shows candidate names on the left; selecting one shows all 40 aspects and their responses on the right.
- **FR-7.2**: The combined resume + 40-aspect + employer-verification data set is defined platform-wide as the candidate's **Profile**.

### 7.8 Hiring Manager Shortlisting, Interview Scheduling & Status Tracking
- **FR-8.1**: HR grants Hiring Manager(s) access to reviewed profiles.
- **FR-8.2**: Hiring Manager acts on each profile via three buttons: Rejected, Shortlisted, Hold (Hold requires a mandatory remarks field).
- **FR-8.3**: Based on Hiring Manager actions, Recruiter schedules interviews through the platform, sent only from the client's own official email domain (no Gmail/Outlook).
- **FR-8.4**: Recruiter/HR must keep every profile's status current: Rejected, Shortlisted, Offered, Joined — this update is mandatory, not optional.
- **FR-8.5**: No fixed email templates are shipped; each tenant maintains its own editable templates.

### 7.9 Candidate Portal
- **FR-9.1**: Candidates log in separately (OTP) to see: New Jobs (only after their first outreach email from a given employer), Apply for Jobs, and Application Stage Status.
- **FR-9.2**: No resume is persisted on the portal between applications — each application requires a fresh upload.

### 7.10 HR / Recruiter Dashboard
- **FR-10.1**: Per-job metrics: Databank matches surfaced, candidates sourced (fresh), candidates shortlisted, candidates offered, candidates joined.
- **FR-10.2**: Aggregate metric: total jobs worked, scoped to the logged-in HR/Recruiter's assignments.

### 7.11 Platform Administration (Super Admin Console) — *new, required for the confirmed role model*
- **FR-11.1**: Super Admin onboards new Client tenants and assigns Hanulisa Recruiter/HR Manager staff to them.
- **FR-11.2**: Super Admin edits the default permission template (§6) globally or per tenant.
- **FR-11.3**: Super Admin has read visibility across tenants for support/ops purposes, gated by its own audit-logged access.

---

## 8. Non-Functional Requirements

- **Multi-tenancy isolation**: every tenant-scoped table carries `tenant_id`; enforced at the database layer (Postgres Row-Level Security), not only in application code.
- **Auditability**: every approval transition, permission change, profile status change, and Super Admin cross-tenant access is written to an immutable audit log.
- **Security**: OTPs are never logged or stored in plaintext (hashed + short TTL); LLM API keys and any third-party secrets are encrypted at rest; PII (candidate age, gender, compensation) is access-controlled per the permission matrix.
- **Compliance**: candidate consent (Aspect 40) governs Databank re-use; data retention and right-to-erasure for candidate data should follow India's DPDP Act, 2023.
- **Performance**: AI contextual rating for a batch of profiles against a job should complete asynchronously and notify HR/Recruiter on completion — this is never a blocking, synchronous UI wait.
- **Availability**: core workflows (auth, job workflow, outreach, review) target 99.5% uptime.
- **Theming**: UI ships in a monochrome black-and-white theme with a light/dark toggle, switchable only from Settings/Profile — never a persistent header toggle.
- **Containerization**: the entire application (frontend, backend, workers, scheduler) must be Docker-containerized, runnable identically in local dev and production.

---

## 9. Release Plan (Phased, not a staged "MVP-then-features" cut)

| Phase | Scope |
|---|---|
| **Phase 1 — Foundation** | OTP auth for all roles, tenant + RBAC model, Super Admin console, company onboarding, job creation + approval workflow |
| **Phase 2 — Sourcing & Matching** | Databank, resume upload, BGE-M3 embeddings + pgvector, hybrid ranking pipeline, tier assignment |
| **Phase 3 — Outreach & Verification** | Candidate outreach emails, 40-aspect questionnaire, employer-verification web-form + fallback parsing, Candidate Portal |
| **Phase 4 — Review & Pipeline** | HR Review Screen, Hiring Manager shortlisting, interview scheduling under client domain, mandatory status tracking |
| **Phase 5 — Dashboards & Hardening** | HR/Recruiter dashboard, audit log UI, load/security hardening, observability |

---

## 10. Glossary

- **Profile**: the combined resume + 40-aspect responses + employer-verification data for one candidate.
- **Databank**: the pool of previously processed candidates who consented (Aspect 40) to be matched against future jobs.
- **Tier**: one of Highly Matching / Moderately Matching / Matching / Not Matching, assigned by the AI contextual rating pipeline.
- **Ratified**: the final, terminal state of the job-approval workflow, after which HR gains access to the job.
