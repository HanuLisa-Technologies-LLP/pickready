# PickReady — Engineering Design Document (ESD)

> **CORRECTION (Rev 2, 2026-07-23)**: the role model that placed HR Managers and
> Recruiters as Hanulisa staff "assigned per tenant" is WRONG. Per
> Pickready.docx (source of truth) and owner direction: the entire staff
> hierarchy (Client → HR Manager → Recruiter → Hiring Manager) belongs to the
> client organization; Hanulisa (the sole platform **Owner**,
> manjuchro@gmail.com) only onboards tenants and tunes permission templates.
> The LLM re-rank stage in §8.2 step 3 is superseded by the 4-parameter
> weighted scoring defined in docs/API_CONTRACT.md Rev 2. Do not build to the
> superseded sections.

| | |
|---|---|
| **Product** | PickReady |
| **Owner** | Hanulisa Technologies LLP |
| **Document status** | Draft v1.0 |
| **Companion doc** | PRD.md (functional requirements), claude.md (build conventions) |

---

## 1. Architecture Overview

```
                         ┌─────────────────────────┐
                         │   Next.js 14 (TS) SPA   │
                         │   shadcn/ui, App Router │
                         │   hosted on Vercel      │
                         └────────────┬────────────┘
                                      │ HTTPS / REST (JWT-in-cookie, refreshed via OTP session)
                                      ▼
                         ┌─────────────────────────┐
                         │   FastAPI (Python 3.12) │
                         │   Dockerized, on        │
                         │   Railway/Render        │
                         └───┬─────────┬───────────┘
                             │         │
                 ┌───────────┘         └───────────┐
                 ▼                                 ▼
      ┌────────────────────┐              ┌──────────────────────┐
      │  PostgreSQL 16      │              │   Redis 7             │
      │  + pgvector ext.    │              │  (Celery broker/       │
      │  Row-Level Security │              │   result backend,      │
      │  per tenant_id       │              │   rate-limit counters) │
      └────────────────────┘              └──────────┬────────────┘
                                                       │
                                            ┌──────────▼────────────┐
                                            │  Celery workers        │
                                            │  + Celery beat         │
                                            │  (Dockerized, same     │
                                            │   Railway/Render project)│
                                            └──────────┬────────────┘
                                                       │
                       ┌───────────────────────────────┼────────────────────────────┐
                       ▼                               ▼                            ▼
             ┌──────────────────┐          ┌───────────────────────┐     ┌────────────────────┐
             │ LLM Router        │          │  Resend (transactional │     │  MSG91 (SMS OTP)    │
             │ (Groq / Gemini /  │          │  email, client-domain  │     │                     │
             │ OpenRouter,       │          │  From/Reply-To)        │     │                     │
             │ fallback chain)   │          └───────────────────────┘     └────────────────────┘
             └──────────────────┘
                       │
                       ▼
             ┌──────────────────┐
             │  Cloudinary        │
             │  (resume/PDF store)│
             └──────────────────┘
```

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js 14 (App Router), TypeScript, shadcn/ui, Tailwind | Monochrome theme system, toggle in Settings only |
| Backend API | FastAPI (Python 3.12), Pydantic v2 | Async throughout |
| LLM orchestration | LangChain | Chains for extraction, re-rank, verification-reply parsing |
| LLM providers | Groq, Google Gemini, OpenRouter (free tiers) | Routed with fallback — see §8.4 |
| Embeddings | BAAI/BGE-M3 (self-hosted or via a free inference endpoint) | 1024-dim dense vectors |
| Database | PostgreSQL 16 + pgvector | Row-Level Security for tenant isolation |
| Cache/Broker | Redis 7 | Celery broker + result backend, OTP rate-limit counters |
| Background jobs | Celery + Celery beat | Retries, scheduling, monitoring via Flower |
| File storage | Cloudinary | Resumes, uploaded PDFs |
| Transactional email | Resend | Per-client domain From/Reply-To, SPF/DKIM per tenant |
| SMS OTP | MSG91 | India-optimized deliverability |
| Containerization | Docker + Docker Compose | Identical local/prod images |
| Frontend hosting | Vercel | |
| Backend hosting | Railway or Render (Docker deploy) | API + workers + beat + Postgres + Redis as services |

---

## 3. Multi-Tenancy Design

- Single shared Postgres database. Every tenant-scoped table carries a `tenant_id UUID NOT NULL` column referencing `tenants(id)`.
- **Postgres Row-Level Security (RLS)** policies enforce `tenant_id = current_setting('app.tenant_id')::uuid` on every tenant-scoped table — the application sets this session variable per request from the authenticated user's tenant claim. This means a bug in application-layer filtering cannot leak cross-tenant data; the database itself refuses it.
- `Super Admin` requests run under a separate Postgres role with a bypass policy (`BYPASSRLS`-gated, only reachable through a dedicated, audit-logged code path), never the default connection pool used for tenant requests.
- Global (non-tenant) tables: `tenants`, `permission_templates`, `llm_provider_keys`, `audit_log` (tenant_id nullable for platform-level events).

---

## 4. Data Model (Core Tables)

```
tenants               (id, name, domain, spf_dkim_status, created_at)
users                 (id, tenant_id NULL for super_admin, role, email, phone, status, created_at)
role_permissions      (id, tenant_id, role, capability, allowed BOOLEAN)   -- the RBAC engine's data
otp_challenges        (id, user_id, channel, code_hash, expires_at, attempts)
companies             (id, tenant_id, brief, culture, policies, benefits)
hiring_managers       (id, tenant_id, user_id, approval_level)             -- max 5 enforced at insert
jobs                  (id, tenant_id, title, department, level, jd_json, status, requirement_period)
job_approvals         (id, job_id, level, approver_user_id, decision, remarks, decided_at)
candidates            (id, tenant_id NULL, full_name, city, age, gender, consent_databank BOOLEAN)
                         -- tenant_id NULL: a candidate profile can be shared across tenants via Databank
profiles              (id, candidate_id, resume_url, aspects_json, parsed_fields_json, source_tenant_id)
job_candidate_links   (id, job_id, candidate_id, profile_id, source ENUM[databank,fresh], match_score, tier)
verification_requests (id, profile_id, employer_seq, token, status, submitted_via ENUM[form,email_reply])
interviews            (id, job_candidate_link_id, scheduled_at, sent_from_email, ics_uid)
pipeline_status       (id, job_candidate_link_id, status ENUM[rejected,shortlisted,hold,offered,joined], remarks)
audit_log             (id, tenant_id NULL, actor_user_id, action, target_type, target_id, metadata_json, at)
```

Indexes of note: `pgvector` HNSW index on `profiles.embedding vector(1024)`; a Postgres full-text `tsvector` column on parsed resume text for BM25-style keyword scoring (via `ts_rank` / `pg_trgm`, no extra search engine needed at this scale).

---

## 5. Auth & OTP Design

1. Client requests OTP → backend generates a 6-digit code, stores **only its hash** in `otp_challenges` with a 5-minute TTL, sends via Resend (email) and/or MSG91 (SMS).
2. Verification checks hash match, TTL, and attempt count (max 5, then a 15-minute cool-off) — all counters live in Redis for atomic increment under concurrent attempts.
3. On success, backend issues a short-lived JWT (15 min access + rotating refresh token in an httpOnly cookie), embedding `user_id`, `tenant_id`, `role`.
4. Every request sets the Postgres session variable for RLS from the JWT's `tenant_id` claim before running any query.
5. Changing registered email/phone re-triggers the full dual-OTP flow per FR-1.3, invalidating the old identifier only after both new channels verify.

---

## 6. Dynamic Permission (RBAC) Engine

- `role_permissions` is genuinely data: `(tenant_id, role, capability, allowed)`. The default template from PRD §6 seeds every new tenant.
- Super Admin's console (FR-11.2) is a CRUD UI over this table, scoped per tenant or applied as a new global default.
- Every protected FastAPI endpoint depends on a single `require_capability("capability_name")` dependency that checks the caller's `(tenant_id, role)` row — no scattered `if role == "recruiter"` checks anywhere in business logic.

---

## 7. Job Approval Workflow Engine

- Modeled as an explicit finite state machine, not free-text status: `Requested → Recommended → Approved → Ratified`, with each tenant choosing which states are active (`companies.approval_levels_config` maps level → assigned user + active/inactive).
- Transition attempts validate: (a) the acting user is the assigned approver for that level, (b) all prior *active* levels are already passed. Inactive levels are skipped automatically, never silently "auto-approved" in the audit trail — the log explicitly records "level skipped (inactive)".
- Every transition writes a `job_approvals` row; the job's current `status` is a materialized view of the latest transition, not hand-maintained.

---

## 8. AI Matching Pipeline

### 8.1 Ingestion
On resume upload (fresh) or Databank surfacing, the resume text (extracted via the docx/pdf pipeline) is chunked and embedded with BGE-M3, stored in `profiles.embedding vector(1024)`.

### 8.2 Hybrid Ranking (per confirmed decision)
For a given job JD:
1. **Semantic stage** — pgvector cosine similarity (`<=>` operator, HNSW index) between the JD embedding and each candidate profile embedding → top-N candidates (N configurable, default 50).
2. **Keyword stage** — Postgres full-text `ts_rank` / trigram score on required skills/keywords extracted from the JD, computed over the same top-N to catch exact-match terms embeddings can miss (tool names, certifications).
3. **LLM re-rank stage** — the top-N (semantic ⊕ keyword, deduplicated) are passed to an LLM chain (LangChain) that scores each profile against the full JD context, returning a 0–100 contextual score with a short rationale (stored for HR visibility, not shown to the candidate).
4. **Tier assignment** — thresholds evaluated top-down: ≥90 → Highly Matching, ≥70 → Moderately Matching, ≥50 → Matching, else Not Matching. A score landing exactly on a boundary takes the **higher** tier (FR-4.5).

### 8.3 Why hybrid, not pure-embedding or pure-LLM
Pure embedding similarity is fast but misses hard keyword requirements (a specific certification, a tool name); pure LLM-as-judge over every candidate is accurate but too slow/costly to run on a full Databank for every job. The hybrid pipeline uses cheap stages to narrow the field, then spends LLM budget only on the shortlist that matters.

### 8.4 LLM Provider Routing & Fallback
Nine keys available (3× Groq, 3× Gemini, 3× OpenRouter). Routing policy:
- **Re-rank scoring** (latency-sensitive, run per-job over N candidates): Groq first (fastest), falling back through the other two Groq keys, then Gemini, then OpenRouter, on rate-limit/5xx.
- **Resume/verification-reply extraction** (longer context, less latency-sensitive): Gemini first (long-context strength), falling back to OpenRouter, then Groq.
- Each of the 9 keys is a row in `llm_provider_keys` (provider, key_encrypted, role_hint, healthy BOOLEAN, last_error_at); a lightweight circuit breaker marks a key unhealthy for a cool-off period after repeated failures and skips it in the fallback chain automatically.
- All keys are encrypted at rest (Fernet/AES via an app-level secret, itself sourced from the hosting platform's secret manager — never committed to the repo).

---

## 9. Resume Parsing Pipeline

- Raw PDF → text extraction (via a PDF text-extraction step in the ingestion Celery task) → LangChain extraction chain against a fixed structured schema (skills, years of experience, education, employment history with dates) → stored in `profiles.parsed_fields_json`.
- Chosen over a dedicated NLP parser (pyresparser/spaCy) because resume formats vary widely and the team is already standardized on LangChain for the rest of the AI pipeline — one extraction paradigm, one set of provider keys, one failure-handling pattern.

---

## 10. Employer Verification Pipeline

1. HR triggers verification for up to 3 previous employers (FR-5.2) → Celery task creates a `verification_requests` row per employer with a signed, single-use token, and sends the request via Resend (client-domain From/Reply-To).
2. Employer either (a) opens the tokenized web-form link and submits structured fields directly, or (b) replies to the email — an inbound-email webhook (Resend inbound parsing) triggers an LLM-based extraction chain against the same structured schema as a fallback.
3. Either path writes into the same `verification_requests.response_json`; the Recruiter cannot move a fresh candidate forward until all requested employer responses (or an explicit HR override with a logged reason) are present.

---

## 11. Notification System

- All outbound email (candidate outreach, employer verification, interview invites) flows through a single Celery-backed `send_email` task, parameterized by tenant so the correct client-domain From/Reply-To and SPF/DKIM-verified sending domain is used (Resend's domain verification, one per tenant).
- SMS (OTP only) flows through a `send_sms` Celery task via MSG91.
- Both tasks retry with exponential backoff and log delivery status to `audit_log`.

---

## 12. Interview Scheduling

- Recruiter schedules through the platform UI; backend generates an `.ics` calendar attachment and sends via the tenant's verified sending domain (Resend) — no Google/Outlook Calendar API integration, per explicit requirement.
- No email templates are shipped; each tenant maintains editable templates (stored per tenant, versioned) used to render the interview-invite body around the `.ics` attachment.

---

## 13. Candidate Portal

- Separate OTP-based session scope from internal/client users (distinct JWT audience claim).
- "New Jobs" visibility is derived from whether the candidate has at least one prior `verification_requests`/outreach record with that tenant — not a general public job board.
- Resume upload is required fresh per application; no resume is retained in candidate-portal storage between applications (Cloudinary object is deleted or made unreachable post-processing, per FR-9.2).

---

## 14. Dashboard & Analytics

- Per-job metrics (PRD §7.10) are computed via materialized views refreshed on a Celery-beat schedule (e.g., every 5 minutes) rather than live aggregation on every dashboard load, to keep the dashboard fast as data grows.

---

## 15. Containerization & Deployment

- **Images**: `frontend` (Next.js standalone build), `backend` (FastAPI + Uvicorn/Gunicorn), `worker` (Celery worker, same codebase, different entrypoint), `beat` (Celery beat scheduler) — all built from a shared base image to keep dependency drift impossible.
- **Local dev**: a single `docker-compose.yml` brings up Postgres+pgvector, Redis, backend, worker, beat, and the frontend dev server, with hot-reload volumes mounted.
- **Production**: `frontend` deploys to Vercel directly from the repo (not the Docker image — Vercel builds Next.js natively); `backend`, `worker`, `beat` deploy as Docker services on Railway/Render, pointed at managed Postgres (with pgvector enabled) and managed Redis.
- **CI**: on every merge to `main`, build and push versioned images, run migrations against a staging database, run the test suite, then deploy.
- **Secrets**: all API keys, DB credentials, and signing secrets live in the hosting platform's secret manager, injected as environment variables — never baked into images.

---

## 16. Security Considerations

- OTPs: hashed at rest, short TTL, rate-limited, never logged.
- PII (age, gender, compensation, verification responses) gated by the RBAC engine, not just UI hiding.
- Postgres RLS as the hard tenant boundary, independent of application code correctness.
- All LLM provider keys encrypted at rest; requests to LLM providers strip any data beyond what the specific chain needs (e.g., re-rank chain never receives raw compensation figures).
- Audit log is append-only (no UPDATE/DELETE grants for the application role) for every approval, permission change, and cross-tenant Super Admin access.
- Candidate data handling aligned to India's DPDP Act, 2023 — consent-gated Databank reuse (Aspect 40), and a defined data-retention/erasure path.

---

## 17. Scalability & Performance

- Databank matching and LLM re-ranking are always asynchronous (Celery), never inline with an HTTP request — the UI polls or receives a websocket/SSE completion event.
- pgvector HNSW index keeps semantic search sub-100ms even as the Databank grows into the tens of thousands of profiles.
- Horizontal scaling: stateless FastAPI + worker containers scale independently on Railway/Render; Redis and Postgres scale vertically first, with read replicas considered post-launch if dashboard read load grows.

---

## 18. Observability

- Structured JSON logging throughout (request ID, tenant ID, user ID on every log line).
- Celery task monitoring via Flower (internal-only, not public-facing).
- Application metrics (request latency, LLM fallback rate, OTP failure rate, Celery queue depth) exported to a metrics backend (e.g., Grafana Cloud free tier) for alerting.

---

## 19. Open Items / Assumptions Carried Forward

- Exact OTP expiry/retry limits above are sensible defaults — confirm before launch.
- PDF/resume retention policy (how long fresh, non-Databank resumes persist) needs a business decision.
- Rate limits on bulk candidate outreach (to avoid Resend/MSG91 throttling on large batches) will be tuned once real batch sizes are known.
